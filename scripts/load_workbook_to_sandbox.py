"""Load a client's workbook into the QuickBooks SANDBOX, one journal entry per month.

    python scripts/load_workbook_to_sandbox.py                 # dry run: offline, prints the plan
    python scripts/load_workbook_to_sandbox.py --apply         # writes to the sandbox
    python scripts/load_workbook_to_sandbox.py --undo          # deletes what --apply logged
    python scripts/load_workbook_to_sandbox.py --write-fixture # golden case 20's snapshot

This is the brief's stretch goal: put the ABC workbook into the sandbox so the engine
can read it back through `pull-quickbooks`, and `round-trip` can prove nothing changed.
It is the project's ONLY code that writes to QuickBooks. A person runs it by hand, after
reading the dry run. Claude never writes to QuickBooks: the MCP server's write tools stay
denied in `.claude/settings.json`, and nothing in `finance_engine/` can write.

What it reads: the workbook, through the client's existing config (default `abc_inc`):
the same reader, the same section anchors. Every line item in revenue, cost of goods
sold and operating expenses, for every month. No figure is typed in here. The workbook
is opened read-only and never changed. A blank cell stops the plan: QuickBooks can't
hold "missing", and the round trip would read it back as 0.

What it would create:
* one account per workbook line, named "RE - <line>" (a ":" becomes " -", which
  QuickBooks does not allow in a name). Revenue lines become Income accounts, COGS lines
  Cost of Goods Sold accounts, and operating-expense lines Expense accounts. One more
  account, "RE - Loader Clearing" (Equity, detail type Partner's Equity, or Owner's
  Equity if QuickBooks refuses that), takes the balancing side;
* one JournalEntry per month, DocNumber "RE-YYYY-MM", dated the month's last day. Each
  line posts the workbook amount to its account. A revenue amount is a credit and a cost
  is a debit; a negative amount takes the other side. The clearing account balances the
  entry. It is an equity account, so it never appears in the Profit and Loss.

Safety:
* **Sandbox only.** It reuses the reader's guard (`QuickBooksReader` in
  `finance_engine/sources/quickbooks_api.py`): the host must be
  sandbox-quickbooks.api.intuit.com, and QBO_ENVIRONMENT must be sandbox. Every request
  is checked again before it is sent.
* **Dry run by default, and the dry run is offline.** It reads the workbook and prints the
  plan. It makes no network call and no token refresh. Only `--apply` and `--undo` talk
  to QuickBooks.
* **Safe to re-run.** An account that already exists by name is reused, if its type
  matches; if not, the run stops. A month whose DocNumber already exists is skipped and
  left alone, whoever created it. The loader never edits anything.
* **Undo deletes only what it logged.** Everything `--apply` creates is logged, as it
  happens, in `data/clients/qb_sandbox_abc/load_log.json` (git-ignored). The log holds
  Intuit's entity IDs, never the realm ID or a token. It also holds a SHA-256 of the realm
  ID, so `--undo` refuses to run against a different company. `--undo` asks you to type
  DELETE. It deletes only the journal entries in the log, and only those that still have
  the DocNumber and SyncToken it logged; one changed since is left alone. Accounts are not
  deleted: QuickBooks cannot delete an account, only deactivate it, so they stay.
* **Network errors.** A DNS failure, timeout or dropped connection is retried up to 3
  times, with a short wait between tries. A create that may have reached QuickBooks
  before the connection dropped is looked up by name or DocNumber before it is sent
  again, so a retry never makes a second copy and what was made is still logged. After
  the third failure the run stops with one line, and `--apply` can simply be re-run.
  Each account and journal entry is created whole or not at all; the log is written
  after each one. If even the final look-up fails, an entry made in that last moment is
  skipped by the re-run (its DocNumber exists) but is not in the log: check before --undo.
* **Credentials.** The same QBO_* variables and the same token store
  (`.secrets/quickbooks_reader_token.json`) as `pull-quickbooks`, through the reader's
  refresh logic. Intuit has no read-only scope, so the reader's app can write; this
  script is the only code that does. No token, secret or realm ID is ever printed.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finance_engine.config import ClientConfig, Settings  # noqa: E402
from finance_engine.credentials import EnvCredentialStore  # noqa: E402
from finance_engine.model import Period  # noqa: E402
from finance_engine.sources.quickbooks_api import (DEFAULT_TOKEN_STORE,  # noqa: E402
                                                   ENV_NAMES, SANDBOX_HOST,
                                                   QuickBooksError, QuickBooksReader,
                                                   TokenStore)

PREFIX = "RE - "
CLEARING = f"{PREFIX}Loader Clearing"
ACCOUNT_TYPES = {"revenue": "Income", "cogs": "Cost of Goods Sold", "opex": "Expense"}
CLEARING_TYPE = "Equity"
#: Equity detail types a company may hold many of, in the order tried. Sending none gives
#: OpeningBalanceEquity, which QuickBooks allows only once per company, as it does
#: RetainedEarnings; both are left out on purpose. If QuickBooks still answers "There can
#: be only one account of this detail type", the next one is tried, once, then the run stops.
CLEARING_SUBTYPES = ("PartnersEquity", "OwnersEquity")
ONLY_ONE = re.compile(r"only one account of (this )?detail type", re.IGNORECASE)
TARGET_CLIENT = "qb_sandbox_abc"
FIXTURE_CLIENT = "qb_sandbox_abc_fixture"
FIXTURE_NAME = "qb_pl_roundtrip_fixture.json"
CENT = Decimal("0.01")
MINOR_VERSION = "75"


#: What a dropped network looks like from urllib: DNS failure ("getaddrinfo failed") and
#: refused connections arrive as URLError; timeouts, resets and cut-off reads as these.
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError,
                  http.client.HTTPException)
ATTEMPTS = 3
BACKOFF = (1.0, 2.0)          # seconds before the second and third attempts


class PlanError(Exception):
    """The workbook can't be loaded faithfully. Nothing was sent."""


class NetworkError(QuickBooksError):
    """QuickBooks could not be reached after every retry."""

    def __init__(self, rerun: str = "--apply"):
        super().__init__(f"Network error talking to QuickBooks. Nothing is half-written; "
                         f"rerun {rerun} to continue.")


# ---- the plan: workbook -> accounts and journal entries (offline) ----------------------

@dataclass
class Account:
    name: str
    account_type: str
    section: str | None           # revenue | cogs | opex; None for the clearing account
    workbook_label: str | None
    sub_types: tuple[str, ...] = ()   # AccountSubType to try, in order; () sends none


@dataclass
class Entry:
    doc_number: str
    txn_date: str
    period: str
    lines: list[dict] = field(default_factory=list)  # account, posting, amount, label

    def total(self, posting: str) -> Decimal:
        return sum((ln["amount"] for ln in self.lines if ln["posting"] == posting),
                   Decimal("0"))


@dataclass
class Plan:
    source_client: str
    workbook: str
    workbook_sha256: str
    accounts: list[Account]
    entries: list[Entry]


def account_name(label: str) -> str:
    name = PREFIX + " ".join(label.replace(":", " -").replace('"', "'").split())
    if len(name) > 100:
        raise PlanError(f"{label!r} gives an account name over QuickBooks' 100 characters.")
    return name


def to_cents(value: float, label: str, period: str) -> Decimal:
    """The workbook value as cents. A value that isn't a whole number of cents (beyond
    float noise such as 204512.41999999998) stops the plan rather than being rounded."""
    exact = Decimal(repr(value))
    cents = exact.quantize(CENT)
    if abs(exact - cents) > Decimal("0.000001"):
        raise PlanError(f"{label!r} {period} is {value!r}, not a whole number of cents; "
                        f"QuickBooks would round it.")
    return cents


def build_plan(source_client: str = "abc_inc", root: Path = ROOT) -> Plan:
    from finance_engine.sources.excel import ExcelSource

    settings = Settings.from_env(root)
    config = ClientConfig.load(source_client, settings)
    if config.source_type != "excel":
        raise PlanError(f"{source_client} is not a workbook client.")
    folder = (root / config.source_directory) if config.source_directory \
        else settings.data_dir / source_client / "source"
    path = folder / (config.source_filename or "")
    if not path.is_file():
        raise PlanError(f"workbook not found: {path}")
    source = ExcelSource(path)
    dataset = source.load(config)
    sheet = dataset.income_statement

    accounts: dict[str, Account] = {}
    items = []
    for item in sheet.line_items:
        if item.role != "item" or item.section not in ACCOUNT_TYPES:
            continue
        name = account_name(item.label)
        if name in accounts:
            raise PlanError(f"two workbook lines give the account name {name!r}.")
        accounts[name] = Account(name, ACCOUNT_TYPES[item.section], item.section, item.label)
        items.append((name, item))
    if not items:
        raise PlanError("no revenue, COGS or operating-expense lines found.")
    blanks = [f"{item.label} {p}" for _, item in items for p in sheet.period_keys()
              if item.value(p) is None]
    if blanks:
        raise PlanError(f"{len(blanks)} blank cell(s), e.g. {', '.join(blanks[:5])}. "
                        f"QuickBooks can't hold a missing value; fix the workbook first.")
    accounts[CLEARING] = Account(CLEARING, CLEARING_TYPE, None, None, CLEARING_SUBTYPES)

    entries = []
    for period in sheet.period_keys():
        p = Period.from_key(period)
        day = calendar.monthrange(p.year, p.month)[1]
        entry = Entry(f"RE-{period}", f"{period}-{day:02d}", period)
        for name, item in items:
            cents = to_cents(item.value(period), item.label, period)
            if cents == 0:
                continue
            normal = "Credit" if item.section == "revenue" else "Debit"
            other = "Debit" if normal == "Credit" else "Credit"
            entry.lines.append({"account": name, "label": item.label,
                                "posting": normal if cents > 0 else other,
                                "amount": abs(cents)})
        gap = entry.total("Debit") - entry.total("Credit")
        if gap:
            entry.lines.append({"account": CLEARING, "label": "balancing line",
                                "posting": "Credit" if gap > 0 else "Debit",
                                "amount": abs(gap)})
        entries.append(entry)
    return Plan(source_client, path.name, source.sha256(), list(accounts.values()), entries)


def plan_lines(plan: Plan) -> list[str]:
    by_type: dict[str, int] = {}
    for a in plan.accounts:
        by_type[a.account_type] = by_type.get(a.account_type, 0) + 1
    out = [f"Workbook: {plan.workbook} (sha256 {plan.workbook_sha256[:16]}...), read "
           f"through config/clients/{plan.source_client}.yaml",
           f"Accounts it would create (or reuse, if one already exists by name): "
           f"{len(plan.accounts)}  "
           + ", ".join(f"{t} {n}" for t, n in by_type.items())]
    for a in plan.accounts:
        out.append(f"  {a.account_type:<19} {a.name}"
                   + (f"  (detail type {', else '.join(a.sub_types)})" if a.sub_types
                      else ""))
    out.append(f"Journal entries it would create (one per month; skipped if the "
               f"DocNumber exists): {len(plan.entries)}")
    out.append(f"  {'DocNumber':<11} {'TxnDate':<10} {'lines':>5} {'debits':>16} "
               f"{'credits':>16}  clearing")
    for e in plan.entries:
        clearing = next((ln for ln in e.lines if ln["account"] == CLEARING), None)
        out.append(f"  {e.doc_number:<11} {e.txn_date:<10} {len(e.lines):>5} "
                   f"{e.total('Debit'):>16,} {e.total('Credit'):>16,}  "
                   + (f"{clearing['posting']} {clearing['amount']:,}" if clearing else "-"))
    return out


# ---- the sandbox writer (only --apply and --undo) --------------------------------------

class SandboxWriter(QuickBooksReader):
    """The reader's guard, token store and redaction, plus the few writes this needs."""

    def __init__(self, *args, sleep=time.sleep, **kwargs):
        super().__init__(*args, **kwargs)
        self.sleep = sleep
        self.rerun = "--apply"
        self._send = self.transport
        self.transport = self._retrying          # the token refresh goes through this

    def _retrying(self, *request):
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return self._send(*request)
            except NETWORK_ERRORS:
                if attempt == ATTEMPTS:
                    raise NetworkError(self.rerun) from None
                self.sleep(BACKOFF[attempt - 1])

    def _call(self, method: str, path: str, params: dict | None = None,
              body: dict | None = None, recover=None) -> dict:
        """One request. A GET is retried on a network error. A POST is retried only with
        `recover`: a look-up that returns the response if the failed attempt got through
        after all (the reply was lost, not the request), so nothing is created twice."""
        if method not in ("GET", "POST"):
            raise QuickBooksError(f"refusing HTTP {method}.")
        if self._access_token is None:
            self._refresh_token()
        query = {"minorversion": MINOR_VERSION, **(params or {})}
        url = f"{self.base_url}/v3/company/{self._realm()}{path}?" \
              f"{urllib.parse.urlencode(query)}"
        if urllib.parse.urlparse(url).hostname != SANDBOX_HOST:
            raise QuickBooksError("Refusing a request to a host other than the sandbox.")
        headers = {"Authorization": f"Bearer {self._access_token}",
                   "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        status = raw = None
        for attempt in range(1, ATTEMPTS + 1):
            try:
                status, raw = self._send(method, url, headers, data, self.timeout)
                break
            except NETWORK_ERRORS:
                if method == "POST" and recover is not None:
                    try:
                        found = recover()
                    except NetworkError:
                        found = None
                    if found is not None:
                        return found
                elif method == "POST":
                    raise NetworkError(self.rerun) from None
                if attempt == ATTEMPTS:
                    raise NetworkError(self.rerun) from None
                self.sleep(BACKOFF[attempt - 1])
        if status != 200:
            text = self._clean(raw.decode("utf-8", "replace"))
            exc = QuickBooksError(f"QuickBooks returned HTTP {status} for {method} {path}: "
                                  f"{text[:300]}")
            exc.status, exc.body = status, text
            raise exc
        return json.loads(raw.decode("utf-8"))

    def query(self, sql: str) -> dict:
        return self._call("GET", "/query", {"query": sql}).get("QueryResponse") or {}

    def accounts_named(self, name: str) -> list[dict]:
        quoted = name.replace("\\", "\\\\").replace("'", "\\'")
        return self.query(f"select * from Account where Name = '{quoted}'").get("Account") \
            or []

    def entries_numbered(self, doc_number: str) -> list[dict]:
        return self.query(f"select * from JournalEntry where DocNumber = "
                          f"'{doc_number}'").get("JournalEntry") or []

    def new_account(self, account: Account, out=print) -> dict:
        def recover():
            found = self.accounts_named(account.name)
            return {"Account": found[0]} if found else None
        body = {"Name": account.name, "AccountType": account.account_type}
        if not account.sub_types:
            return self._call("POST", "/account", body=body, recover=recover)["Account"]
        tried = []
        for sub in account.sub_types[:2]:         # the first, then the next one, once
            try:
                return self._call("POST", "/account", body={**body, "AccountSubType": sub},
                                  recover=recover)["Account"]
            except QuickBooksError as exc:
                if getattr(exc, "status", None) != 400 or                         not ONLY_ONE.search(getattr(exc, "body", "")):
                    raise
                tried.append(sub)
                out(f"  QuickBooks allows only one {account.account_type} account of detail "
                    f"type {sub} and has one already")
        raise QuickBooksError(
            f"QuickBooks refused {account.name!r} with every detail type tried "
            f"({', '.join(tried)}): \"There can be only one account of this detail type\". "
            f"Nothing else was changed, and the accounts made so far stay; a rerun reuses "
            f"them by name. To finish, add {account.name!r} in the sandbox by hand as an "
            f"{account.account_type} account with another detail type, then rerun --apply.")

    def new_entry(self, entry: Entry, account_ids: dict[str, str], note: str) -> dict:
        lines = [{"Description": ln["label"], "Amount": float(ln["amount"]),
                  "DetailType": "JournalEntryLineDetail",
                  "JournalEntryLineDetail": {"PostingType": ln["posting"],
                                             "AccountRef": {"value":
                                                            account_ids[ln["account"]]}}}
                 for ln in entry.lines]
        def recover():
            found = self.entries_numbered(entry.doc_number)
            return {"JournalEntry": found[0]} if found else None
        return self._call("POST", "/journalentry", body={
            "DocNumber": entry.doc_number, "TxnDate": entry.txn_date,
            "PrivateNote": note, "Line": lines}, recover=recover)["JournalEntry"]

    def read_entry(self, entry_id: str) -> dict | None:
        try:
            return self._call("GET", f"/journalentry/{entry_id}")["JournalEntry"]
        except QuickBooksError as exc:
            if "HTTP 400" in str(exc) or "HTTP 404" in str(exc):
                return None
            raise

    def remove_entry(self, entry_id: str, sync_token: str) -> None:
        self._call("POST", "/journalentry", {"operation": "delete"},
                   body={"Id": entry_id, "SyncToken": sync_token},
                   recover=lambda: {} if self.read_entry(entry_id) is None else None)


def writer(root: Path = ROOT, transport=None, credentials=None) -> SandboxWriter:
    credentials = credentials or EnvCredentialStore(list(ENV_NAMES), env_file=root / ".env")
    return SandboxWriter(credentials, TokenStore(root / DEFAULT_TOKEN_STORE),
                         transport=transport)


# ---- the log ---------------------------------------------------------------------------

def log_path(root: Path = ROOT) -> Path:
    return Settings.from_env(root).data_dir / TARGET_CLIENT / "load_log.json"


def _realm_hash(w: SandboxWriter) -> str:
    return hashlib.sha256(w._realm().encode()).hexdigest()


def read_log(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"note": "Created by scripts/load_workbook_to_sandbox.py. --undo deletes only "
                    "the journal entries listed here. No token or realm ID is stored.",
            "realm_sha256": None, "created": []}


def write_log(path: Path, log: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(log, indent=2), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ---- apply and undo --------------------------------------------------------------------

def apply(plan: Plan, w: SandboxWriter, path: Path, out=print) -> dict:
    log = read_log(path)
    realm = _realm_hash(w)
    if log["realm_sha256"] not in (None, realm):
        raise QuickBooksError(f"{path} was written for a different sandbox company; "
                              f"refusing to mix two companies in one log.")
    log["realm_sha256"] = realm
    counts = {"accounts_created": 0, "accounts_reused": 0, "entries_created": 0,
              "entries_skipped": 0}

    ids: dict[str, str] = {}
    for account in plan.accounts:
        found = w.accounts_named(account.name)
        if len(found) > 1:
            raise QuickBooksError(f"{len(found)} accounts are named {account.name!r}; "
                                  f"refusing to choose.")
        if found:
            existing = found[0]
            if existing.get("AccountType") != account.account_type:
                raise QuickBooksError(f"{account.name!r} exists as "
                                      f"{existing.get('AccountType')!r}, not "
                                      f"{account.account_type!r}. Nothing was changed.")
            if existing.get("Active") is False:
                raise QuickBooksError(f"{account.name!r} exists but is inactive. Make it "
                                      f"active in QuickBooks, or rename it, then re-run.")
            ids[account.name] = existing["Id"]
            counts["accounts_reused"] += 1
            continue
        made = w.new_account(account, out)
        ids[account.name] = made["Id"]
        log["created"].append({"kind": "Account", "Id": made["Id"], "Name": account.name,
                               "AccountType": account.account_type,
                               **({"AccountSubType": made.get("AccountSubType")}
                                  if account.sub_types else {}),
                               "created_at": _now()})
        write_log(path, log)
        counts["accounts_created"] += 1
        out(f"  created account {account.name}")

    note = (f"Loaded by scripts/load_workbook_to_sandbox.py from {plan.workbook} "
            f"(sha256 {plan.workbook_sha256[:16]}...). Test data in the sandbox.")
    for entry in plan.entries:
        existing = w.entries_numbered(entry.doc_number)
        if existing:
            counts["entries_skipped"] += 1
            out(f"  {entry.doc_number} exists (Id {existing[0].get('Id')}): skipped, "
                f"left alone")
            continue
        made = w.new_entry(entry, ids, note)
        log["created"].append({"kind": "JournalEntry", "Id": made["Id"],
                               "SyncToken": made.get("SyncToken"),
                               "DocNumber": entry.doc_number, "TxnDate": entry.txn_date,
                               "created_at": _now()})
        write_log(path, log)
        counts["entries_created"] += 1
        out(f"  created {entry.doc_number} ({len(entry.lines)} lines)")
    return counts


def undo(w: SandboxWriter, path: Path, confirm=input, out=print) -> dict:
    log = read_log(path)
    entries = [c for c in log["created"]
               if c["kind"] == "JournalEntry" and not c.get("deleted_at")]
    if not entries:
        out(f"Nothing to undo: {path} lists no journal entries.")
        return {"deleted": 0, "left_alone": 0, "already_gone": 0}
    if log.get("realm_sha256") != _realm_hash(w):
        raise QuickBooksError(f"{path} was written for a different sandbox company. "
                              f"Nothing was deleted.")
    out(f"--undo would delete {len(entries)} journal entr"
        f"{'y' if len(entries) == 1 else 'ies'} from the sandbox: "
        f"{entries[0]['DocNumber']} .. {entries[-1]['DocNumber']}. Accounts are kept.")
    if confirm("Type DELETE to continue: ").strip() != "DELETE":
        out("Not confirmed. Nothing was deleted.")
        return {"deleted": 0, "left_alone": 0, "already_gone": 0, "cancelled": True}
    counts = {"deleted": 0, "left_alone": 0, "already_gone": 0}
    for logged in entries:
        current = w.read_entry(logged["Id"])
        if current is None:
            logged["deleted_at"] = _now()
            counts["already_gone"] += 1
            out(f"  {logged['DocNumber']}: already gone")
        elif (current.get("DocNumber") != logged["DocNumber"]
              or current.get("SyncToken") != logged["SyncToken"]):
            counts["left_alone"] += 1
            out(f"  {logged['DocNumber']}: changed since the loader created it; left alone")
            continue
        else:
            w.remove_entry(logged["Id"], current["SyncToken"])
            logged["deleted_at"] = _now()
            counts["deleted"] += 1
            out(f"  {logged['DocNumber']}: deleted")
        write_log(path, log)
    made = [c["Name"] for c in log["created"] if c["kind"] == "Account"]
    if made:
        out(f"  {len(made)} account(s) the loader created are kept: QuickBooks can't delete "
            f"an account. Make them inactive in QuickBooks if you want them hidden.")
    return counts


# ---- golden case 20: an offline snapshot in Intuit's shape ------------------------------

def simulated_report(plan: Plan, pulled_at: str = "2026-10-14T12:00:00-07:00") -> dict:
    """The Profit and Loss QuickBooks would return after `--apply`, built offline.

    Intuit's shape (one column per month plus Total; blank for no activity), the loaded
    "RE - " accounts, and two accounts that are not the client's: one income account and
    one nested expense group, as the sandbox's sample company has. Intuit's totals include
    those, as they would, so the reader must leave them out. Totals are added in exact
    cents. This stands in for a real pull until one exists (`--write-fixture --from`).
    """
    months = [e.period for e in plan.entries]
    titles = [Period.from_key(p).display for p in months]
    amounts: dict[str, dict[str, Decimal]] = {a.name: {} for a in plan.accounts}
    for e in plan.entries:
        for ln in e.lines:
            if ln["account"] == CLEARING:
                continue
            section = next(a.section for a in plan.accounts if a.name == ln["account"])
            normal = "Credit" if section == "revenue" else "Debit"
            sign = 1 if ln["posting"] == normal else -1
            amounts[ln["account"]][e.period] = sign * ln["amount"]
    first, last = months[0], months[-1]
    sample = {
        "revenue": {"Sample services": {first: Decimal("1250.00"), last: Decimal("980.50")}},
        "opex": {"Job Expenses": {"Job Materials": {first: Decimal("310.25")},
                                  "Equipment Rental": {last: Decimal("125.00")}}},
    }

    def money(v: Decimal | None) -> dict:
        return {"value": "" if v is None else f"{v:.2f}"}

    def row(label: str, values: dict[str, Decimal], blank_zero: bool = True) -> list:
        cells = [{"value": label}]
        for p in months:
            v = values.get(p)
            cells.append(money(None if (v is None or (blank_zero and v == 0)) else v))
        cells.append(money(sum(values.values(), Decimal("0"))))
        return cells

    def add(a: dict, b: dict, sign: int = 1) -> dict:
        return {p: a.get(p, Decimal("0")) + sign * b.get(p, Decimal("0")) for p in months}

    def section(group: str, header: str, total_label: str, key: str) -> tuple[dict, dict]:
        rows, total = [], {}
        for a in plan.accounts:
            if a.section == key:
                rows.append({"ColData": row(a.name, amounts[a.name]), "type": "Data"})
                total = add(total, amounts[a.name])
        for name, vals in sample.get(key, {}).items():
            if all(isinstance(v, dict) for v in vals.values()):
                inner, sub = [], {}
                for leaf, leaf_vals in vals.items():
                    inner.append({"ColData": row(leaf, leaf_vals), "type": "Data"})
                    sub = add(sub, leaf_vals)
                rows.append({"Header": {"ColData": [{"value": name}] +
                                       [{"value": ""}] * (len(months) + 1)},
                             "Rows": {"Row": inner},
                             "Summary": {"ColData": row(f"Total {name}", sub, False)},
                             "type": "Section"})
                total = add(total, sub)
            else:
                rows.append({"ColData": row(name, vals), "type": "Data"})
                total = add(total, vals)
        return ({"Header": {"ColData": [{"value": header}] +
                            [{"value": ""}] * (len(months) + 1)},
                 "Rows": {"Row": rows},
                 "Summary": {"ColData": row(total_label, total, False)},
                 "type": "Section", "group": group}, total)

    def summary(group: str, label: str, values: dict) -> dict:
        return {"Summary": {"ColData": row(label, values, False)}, "type": "Section",
                "group": group}

    inc, inc_t = section("Income", "Income", "Total Income", "revenue")
    cogs, cogs_t = section("COGS", "Cost of Goods Sold", "Total Cost of Goods Sold", "cogs")
    exp, exp_t = section("Expenses", "Expenses", "Total Expenses", "opex")
    gp = add(inc_t, cogs_t, -1)
    noi = add(gp, exp_t, -1)
    columns = [{"ColTitle": "", "ColType": "Account",
                "MetaData": [{"Name": "ColKey", "Value": "account"}]}]
    for p, title in zip(months, titles):
        per = Period.from_key(p)
        day = calendar.monthrange(per.year, per.month)[1]
        columns.append({"ColTitle": title, "ColType": "Money", "MetaData": [
            {"Name": "StartDate", "Value": f"{p}-01"},
            {"Name": "EndDate", "Value": f"{p}-{day:02d}"},
            {"Name": "ColKey", "Value": title}]})
    columns.append({"ColTitle": "Total", "ColType": "Money",
                    "MetaData": [{"Name": "ColKey", "Value": "total"}]})
    last_p = Period.from_key(last)
    return {
        "Header": {"Time": pulled_at, "ReportName": "ProfitAndLoss",
                   "ReportBasis": "Accrual", "StartPeriod": f"{first}-01",
                   "EndPeriod": f"{last}-"
                                f"{calendar.monthrange(last_p.year, last_p.month)[1]:02d}",
                   "SummarizeColumnsBy": "Month", "Currency": "USD",
                   "Option": [{"Name": "AccountingStandard", "Value": "GAAP"},
                              {"Name": "NoReportData", "Value": "false"}]},
        "Columns": {"Column": columns},
        "Rows": {"Row": [inc, cogs, summary("GrossProfit", "Gross Profit", gp), exp,
                         summary("NetOperatingIncome", "Net Operating Income", noi),
                         summary("NetIncome", "Net Income", noi)]},
    }


def fixture_path(root: Path = ROOT) -> Path:
    return Settings.from_env(root).data_dir / FIXTURE_CLIENT / "source" / FIXTURE_NAME


def write_fixture(root: Path = ROOT, from_snapshot: Path | None = None,
                  overwrite: bool = False) -> Path:
    """Golden case 20's snapshot, in a git-ignored folder: it holds client figures.

    Without `from_snapshot`, the offline simulation of what `--apply` then
    `pull-quickbooks` would give. With it, a copy of a real pull, after checking that
    none of the QBO_* values (realm ID, tokens, client ID or secret) appears in it.
    """
    path = fixture_path(root)
    if path.exists() and not overwrite:
        return path
    if from_snapshot is not None:
        raw = Path(from_snapshot).read_bytes()
        store = EnvCredentialStore(list(ENV_NAMES), env_file=root / ".env")
        values = [v for n in ENV_NAMES if n != "QBO_ENVIRONMENT"
                  for v in [store.get(n)] if v and len(v) >= 4]
        values += [(TokenStore(root / DEFAULT_TOKEN_STORE).load() or "")]
        if any(v and v.encode() in raw for v in values):
            raise PlanError(f"{from_snapshot} contains a credential or the realm ID; "
                            f"not copied.")
        json.loads(raw.decode("utf-8"))
    else:
        raw = (json.dumps(simulated_report(build_plan(root=root)), indent=1) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


# ---- command line ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="create the accounts and journal entries in the sandbox")
    mode.add_argument("--undo", action="store_true",
                      help="delete the journal entries listed in the load log (asks first)")
    mode.add_argument("--write-fixture", action="store_true",
                      help="write golden case 20's snapshot (offline unless --from)")
    ap.add_argument("--from", dest="from_snapshot", type=Path, default=None,
                    help="with --write-fixture: copy this real pulled snapshot instead")
    ap.add_argument("--client", default="abc_inc", help="workbook client (default abc_inc)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    try:
        if args.write_fixture:
            path = write_fixture(from_snapshot=args.from_snapshot, overwrite=True)
            print(f"wrote {path}")
            return 0
        if args.undo:
            w = writer()
            w.rerun = "--undo"
            counts = undo(w, log_path())
            print(f"undo: {counts}")
            return 0
        plan = build_plan(args.client)
        for line in plan_lines(plan):
            print(line)
        if not args.apply:
            print("\nDRY RUN: nothing was sent, and no network call was made. "
                  "Re-run with --apply to write to the sandbox.")
            return 0
        print(f"\nApplying to the sandbox ({SANDBOX_HOST}) ...")
        counts = apply(plan, writer(), log_path())
        print(f"done: {counts}. Log: {log_path()}")
        print(f"next: python -m finance_engine pull-quickbooks --client {TARGET_CLIENT} "
              f"--start {plan.entries[0].period} --end {plan.entries[-1].period}")
        return 0
    except NetworkError as exc:
        print(exc, file=sys.stderr)
        return 1
    except (PlanError, QuickBooksError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
