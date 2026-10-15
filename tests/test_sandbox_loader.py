"""The workbook-to-sandbox loader, the account-prefix reader and the round trip (Phase 6).

No test here touches the network. A fake QuickBooks answers the token refresh, queries,
creates and deletes in memory. Most tests use a generated 18-month workbook (multi_co),
so they run on a fresh clone; the ABC-specific ones skip when its workbook is missing.
"""

from __future__ import annotations

import copy
import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest
import yaml

from finance_engine.cli import EXIT_DATA, EXIT_OK, main as cli_main
from finance_engine.config import ClientConfig, Settings
from finance_engine.credentials import EnvCredentialStore
from finance_engine.round_trip import round_trip
from finance_engine.sources import quickbooks_api
from finance_engine.sources.quickbooks import read_profit_and_loss
from finance_engine.sources.quickbooks_api import TOKEN_URL, QuickBooksError, pull_snapshot

from tests.helpers import MONTHLY_CONFIG, build_monthly_workbook, write_monthly_config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import load_workbook_to_sandbox as loader  # noqa: E402

MONTHS = [f"{y}-{m:02d}" for y in (2025, 2026) for m in range(1, 13)][:18]
SECRETS = {"QBO_CLIENT_ID": "ABfakeClientId0123456789abcdef",
           "QBO_CLIENT_SECRET": "fakeClientSecret0123456789ABCDEFxyz",
           "QBO_REFRESH_TOKEN": "AB11fakeRefreshToken0123456789abcdefghijklmnopq",
           "QBO_REALM_ID": "9341455123456789", "QBO_ENVIRONMENT": "sandbox"}
ACCESS = "eyJfakeAccessToken.abcdefghijklmnopqrstuvwxyz0123456789"
ROTATED = "AB11fakeRotatedToken9876543210zyxwvutsrqponmlkj"


def creds(**over):
    return EnvCredentialStore(list(quickbooks_api.ENV_NAMES), environ={**SECRETS, **over})


@pytest.fixture
def workspace(tmp_path) -> Path:
    """An 18-month workbook client (multi_co) and its QuickBooks twin (multi_qb)."""
    configs = tmp_path / "config" / "clients"
    write_monthly_config(configs, overrides={"source": {"type": "excel",
                                                        "filename": "multi.xlsx"}})
    source = tmp_path / "data" / "clients" / "multi_co" / "source"
    source.mkdir(parents=True)
    build_monthly_workbook(source / "multi.xlsx", MONTHS)
    twin = yaml.safe_load((ROOT / "config" / "clients" / "qb_sandbox_abc.yaml")
                          .read_text(encoding="utf-8"))
    twin.update(client_id="multi_qb", company_name="Multi Co via QuickBooks",
                opex_categories=copy.deepcopy(MONTHLY_CONFIG["opex_categories"]),
                expected_negative_lines=[])
    (configs / "multi_qb.yaml").write_text(yaml.safe_dump(twin, sort_keys=False),
                                           encoding="utf-8")
    return tmp_path


def plan_for(root: Path) -> loader.Plan:
    return loader.build_plan("multi_co", root)


def save_snapshot(root: Path, report: dict, name: str = "qb_pl_sim.json") -> Path:
    folder = root / "data" / "clients" / "multi_qb" / "source"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


# ---- a fake QuickBooks ------------------------------------------------------------------

#: The 400 QuickBooks sends for a second account of a single-instance detail type.
ONLY_ONE_400 = (b'{"Fault":{"Error":[{"Message":"There can be only one account of this '
                b'detail type","Detail":"There can be only one account of this detail type",'
                b'"code":"6000","element":"AccountSubType"}],"type":"ValidationFault"},'
                b'"time":"2026-10-15T09:12:44.123-07:00"}')

class FakeQBO:
    """In-memory accounts and journal entries; records every request."""

    def __init__(self):
        self.calls: list[tuple[str, str, bytes | None]] = []
        self.accounts: dict[str, dict] = {}
        self.entries: dict[str, dict] = {}
        self.next_id = 100
        #: Detail types this company already has one of and may not have twice. With no
        #: AccountSubType, QuickBooks makes an Equity account OpeningBalanceEquity.
        self.taken = {"OpeningBalanceEquity"}

    def _id(self) -> str:
        self.next_id += 1
        return str(self.next_id)

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, body))
        if method == "POST" and url == TOKEN_URL:
            return 200, json.dumps({"access_token": ACCESS, "refresh_token": ROTATED,
                                    "x_refresh_token_expires_in": 8726400}).encode()
        assert urllib.parse.urlparse(url).hostname == quickbooks_api.SANDBOX_HOST
        parsed = urllib.parse.urlparse(url)
        path, params = parsed.path, dict(urllib.parse.parse_qsl(parsed.query))
        if method == "GET" and path.endswith("/query"):
            sql = params["query"]
            value = sql.split("= '", 1)[1][:-1].replace("\\'", "'")
            if " from Account " in sql:
                found = [a for a in self.accounts.values() if a["Name"] == value]
                return 200, json.dumps({"QueryResponse": {"Account": found}
                                        if found else {}}).encode()
            found = [e for e in self.entries.values() if e["DocNumber"] == value]
            return 200, json.dumps({"QueryResponse": {"JournalEntry": found}
                                    if found else {}}).encode()
        if method == "POST" and path.endswith("/account"):
            data = json.loads(body)
            sub = data.get("AccountSubType") or (
                "OpeningBalanceEquity" if data["AccountType"] == "Equity" else None)
            if sub in self.taken:
                return 400, ONLY_ONE_400
            acc = {"Id": self._id(), "Name": data["Name"],
                   "AccountType": data["AccountType"], "Active": True,
                   **({"AccountSubType": sub} if sub else {})}
            self.accounts[acc["Id"]] = acc
            return 200, json.dumps({"Account": acc}).encode()
        if method == "POST" and path.endswith("/journalentry"):
            data = json.loads(body)
            if params.get("operation") == "delete":
                entry = self.entries.get(data["Id"])
                assert entry and entry["SyncToken"] == data["SyncToken"]
                del self.entries[data["Id"]]
                return 200, json.dumps({"JournalEntry": {"Id": data["Id"],
                                                         "status": "Deleted"}}).encode()
            debit = sum(ln["Amount"] for ln in data["Line"]
                        if ln["JournalEntryLineDetail"]["PostingType"] == "Debit")
            credit = sum(ln["Amount"] for ln in data["Line"]
                         if ln["JournalEntryLineDetail"]["PostingType"] == "Credit")
            assert abs(debit - credit) < 0.001, "unbalanced journal entry"
            entry = {"Id": self._id(), "SyncToken": "0", **data}
            self.entries[entry["Id"]] = entry
            return 200, json.dumps({"JournalEntry": entry}).encode()
        if method == "GET" and "/journalentry/" in path:
            entry = self.entries.get(path.rsplit("/", 1)[1])
            if entry is None:
                return 400, b'{"Fault":{"Error":[{"Message":"Object Not Found"}]}}'
            return 200, json.dumps({"JournalEntry": entry}).encode()
        raise AssertionError(f"unexpected request: {method} {url}")


def fake_writer(tmp_path, fake, **over) -> loader.SandboxWriter:
    return loader.SandboxWriter(creds(**over), quickbooks_api.TokenStore(
        tmp_path / ".secrets" / "t.json"), transport=fake)


# ---- the plan (offline) -----------------------------------------------------------------

class TestPlan:
    def test_one_balanced_entry_per_month(self, workspace):
        plan = plan_for(workspace)
        assert [e.doc_number for e in plan.entries] == [f"RE-{m}" for m in MONTHS]
        assert plan.entries[1].txn_date == "2025-02-28"
        for e in plan.entries:
            assert e.total("Debit") == e.total("Credit")
            assert all(ln["amount"] > 0 for ln in e.lines)

    def test_accounts_by_section_and_prefixed(self, workspace):
        plan = plan_for(workspace)
        by = {a.name: a.account_type for a in plan.accounts}
        assert by == {"RE - Sales": "Income", "RE - Services": "Income",
                      "RE - Hosting": "Cost of Goods Sold", "RE - Payroll": "Expense",
                      "RE - Rent": "Expense", "RE - Loader Clearing": "Equity"}

    def test_amounts_come_from_the_workbook(self, workspace):
        jan = plan_for(workspace).entries[0]
        posted = {ln["account"]: (ln["posting"], str(ln["amount"])) for ln in jan.lines}
        assert posted["RE - Sales"] == ("Credit", "1000.00")
        assert posted["RE - Hosting"] == ("Debit", "100.00")
        # Revenue 1,200 less costs 450 leaves 750 for the clearing account to debit.
        assert posted["RE - Loader Clearing"] == ("Debit", "750.00")

    def test_a_negative_amount_takes_the_other_side(self, workspace):
        source = workspace / "data" / "clients" / "multi_co" / "source" / "multi.xlsx"
        build_monthly_workbook(source, MONTHS, {("Rent", "2025-01"): -50.0})
        jan = plan_for(workspace).entries[0]
        assert ("RE - Rent", "Credit") in {(ln["account"], ln["posting"]) for ln in jan.lines}

    def test_a_blank_cell_stops_the_plan(self, workspace):
        source = workspace / "data" / "clients" / "multi_co" / "source" / "multi.xlsx"
        build_monthly_workbook(source, MONTHS, {("Rent", "2025-03"): None})
        with pytest.raises(loader.PlanError, match="blank cell"):
            plan_for(workspace)

    def test_fractions_of_a_cent_stop_the_plan(self, workspace):
        source = workspace / "data" / "clients" / "multi_co" / "source" / "multi.xlsx"
        build_monthly_workbook(source, MONTHS, {("Rent", "2025-03"): 50.004})
        with pytest.raises(loader.PlanError, match="whole number of cents"):
            plan_for(workspace)

    def test_float_noise_is_not_a_fraction_of_a_cent(self):
        assert str(loader.to_cents(204512.41999999998, "x", "p")) == "204512.42"

    def test_a_colon_is_not_allowed_in_a_quickbooks_name(self):
        assert loader.account_name("Marketing/Advertising: Ad Spend") == \
            "RE - Marketing/Advertising - Ad Spend"

    def test_the_workbook_is_not_modified(self, workspace):
        path = workspace / "data" / "clients" / "multi_co" / "source" / "multi.xlsx"
        before = path.read_bytes()
        plan_for(workspace)
        assert path.read_bytes() == before

    def test_the_dry_run_makes_no_network_call(self, monkeypatch, capsys):
        def no_network(*a, **k):
            raise AssertionError("the dry run must not build a writer")
        monkeypatch.setattr(loader, "writer", no_network)
        monkeypatch.setattr(loader, "build_plan", lambda client: _tiny_plan())
        assert loader.main([]) == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out and "RE-2025-01" in out


def _tiny_plan() -> loader.Plan:
    accounts = [loader.Account("RE - Sales", "Income", "revenue", "Sales"),
                loader.Account(loader.CLEARING, "Equity", None, None)]
    entry = loader.Entry("RE-2025-01", "2025-01-31", "2025-01")
    from decimal import Decimal
    entry.lines = [{"account": "RE - Sales", "label": "Sales", "posting": "Credit",
                    "amount": Decimal("10.00")},
                   {"account": loader.CLEARING, "label": "balancing line",
                    "posting": "Debit", "amount": Decimal("10.00")}]
    return loader.Plan("x", "x.xlsx", "0" * 64, accounts, [entry])


# ---- sandbox only, and the writes ---------------------------------------------------------

class TestSandboxOnly:
    @pytest.mark.parametrize("base", ["https://quickbooks.api.intuit.com",
                                      "https://sandbox-quickbooks.api.intuit.com.evil.example"])
    def test_a_non_sandbox_host_is_refused(self, tmp_path, base):
        with pytest.raises(QuickBooksError, match="only the sandbox host"):
            loader.SandboxWriter(creds(), quickbooks_api.TokenStore(tmp_path / "t.json"),
                                 base_url=base, transport=FakeQBO())

    def test_production_is_refused(self, tmp_path):
        with pytest.raises(QuickBooksError, match="sandbox only"):
            fake_writer(tmp_path, FakeQBO(), QBO_ENVIRONMENT="production")

    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
    def test_only_get_and_post(self, tmp_path, method):
        fake = FakeQBO()
        with pytest.raises(QuickBooksError, match="refusing"):
            fake_writer(tmp_path, fake)._call(method, "/account")
        assert fake.calls == []

    def test_the_engine_reader_is_still_read_only(self):
        # The loader's writes live in scripts/, never in finance_engine/.
        source = Path(quickbooks_api.__file__).read_text(encoding="utf-8")
        assert "journalentry" not in source.lower()


class TestApply:
    def test_creates_accounts_and_one_entry_per_month(self, workspace, tmp_path):
        fake, log = FakeQBO(), tmp_path / "log" / "load_log.json"
        counts = loader.apply(plan_for(workspace), fake_writer(tmp_path, fake), log,
                              out=lambda *_: None)
        assert counts == {"accounts_created": 6, "accounts_reused": 0,
                          "entries_created": 18, "entries_skipped": 0}
        assert sorted(e["DocNumber"] for e in fake.entries.values()) == \
            [f"RE-{m}" for m in MONTHS]
        logged = json.loads(log.read_text(encoding="utf-8"))
        assert len(logged["created"]) == 24

    def test_a_rerun_creates_nothing(self, workspace, tmp_path):
        fake, log = FakeQBO(), tmp_path / "load_log.json"
        w = fake_writer(tmp_path, fake)
        loader.apply(plan_for(workspace), w, log, out=lambda *_: None)
        counts = loader.apply(plan_for(workspace), w, log, out=lambda *_: None)
        assert counts == {"accounts_created": 0, "accounts_reused": 6,
                          "entries_created": 0, "entries_skipped": 18}
        assert len(fake.entries) == 18

    def test_an_existing_entry_it_did_not_create_is_left_alone(self, workspace, tmp_path):
        fake = FakeQBO()
        fake.entries["7"] = {"Id": "7", "SyncToken": "3", "DocNumber": "RE-2025-05"}
        loader.apply(plan_for(workspace), fake_writer(tmp_path, fake),
                     tmp_path / "log.json", out=lambda *_: None)
        assert fake.entries["7"] == {"Id": "7", "SyncToken": "3", "DocNumber": "RE-2025-05"}
        assert len(fake.entries) == 18

    def test_an_account_of_another_type_stops_the_run(self, workspace, tmp_path):
        fake = FakeQBO()
        fake.accounts["5"] = {"Id": "5", "Name": "RE - Rent", "AccountType": "Income",
                              "Active": True}
        with pytest.raises(QuickBooksError, match="not 'Expense'"):
            loader.apply(plan_for(workspace), fake_writer(tmp_path, fake),
                         tmp_path / "log.json", out=lambda *_: None)
        assert fake.entries == {}

    def test_no_secret_in_output_or_log(self, workspace, tmp_path):
        fake, log, printed = FakeQBO(), tmp_path / "log.json", []
        loader.apply(plan_for(workspace), fake_writer(tmp_path, fake), log,
                     out=printed.append)
        text = "\n".join(printed) + log.read_text(encoding="utf-8")
        for secret in [*SECRETS.values(), ACCESS, ROTATED]:
            if secret != "sandbox":
                assert secret not in text


def account_posts(fake, name):
    return [json.loads(body) for method, url, body in fake.calls
            if method == "POST" and url.split("?")[0].endswith("/account")
            and json.loads(body)["Name"] == name]


class TestClearingDetailType:
    """QuickBooks allows one OpeningBalanceEquity account per company, and that is what an
    Equity account becomes when no AccountSubType is sent."""

    def test_the_clearing_account_is_sent_with_a_reusable_detail_type(self, workspace,
                                                                       tmp_path):
        fake, log = FakeQBO(), tmp_path / "log.json"
        loader.apply(plan_for(workspace), fake_writer(tmp_path, fake), log,
                     out=lambda *_: None)
        assert [b.get("AccountSubType") for b in account_posts(fake, loader.CLEARING)]             == ["PartnersEquity"]
        assert "AccountSubType" not in account_posts(fake, "RE - Sales")[0]
        logged = [c for c in json.loads(log.read_text(encoding="utf-8"))["created"]
                  if c.get("Name") == loader.CLEARING]
        assert logged[0]["AccountSubType"] == "PartnersEquity"

    def test_never_a_single_instance_type(self):
        assert not {"OpeningBalanceEquity", "RetainedEarnings"} &             set(loader.CLEARING_SUBTYPES)

    def test_refused_once_it_tries_the_next_type(self, workspace, tmp_path):
        fake, printed = FakeQBO(), []
        fake.taken.add("PartnersEquity")
        counts = loader.apply(plan_for(workspace), fake_writer(tmp_path, fake),
                              tmp_path / "log.json", out=printed.append)
        assert [b["AccountSubType"] for b in account_posts(fake, loader.CLEARING)] ==             ["PartnersEquity", "OwnersEquity"]
        made = [a for a in fake.accounts.values() if a["Name"] == loader.CLEARING]
        assert made[0]["AccountSubType"] == "OwnersEquity"
        assert counts["entries_created"] == 18
        assert any("only one Equity account of detail type PartnersEquity" in line
                   for line in printed)

    def test_refused_twice_it_stops_with_a_clear_message(self, workspace, tmp_path):
        fake = FakeQBO()
        fake.taken |= {"PartnersEquity", "OwnersEquity"}
        with pytest.raises(QuickBooksError, match="every detail type tried "
                           r"\(PartnersEquity, OwnersEquity\).*rerun --apply"):
            loader.apply(plan_for(workspace), fake_writer(tmp_path, fake),
                         tmp_path / "log.json", out=lambda *_: None)
        assert len(account_posts(fake, loader.CLEARING)) == 2      # no third try
        assert len(fake.accounts) == 5 and fake.entries == {}

    def test_the_rerun_reuses_the_accounts_already_made(self, workspace, tmp_path):
        # The failure in the sandbox: every other account made, then the clearing refused.
        fake, log = FakeQBO(), tmp_path / "log.json"
        fake.taken |= {"PartnersEquity", "OwnersEquity"}
        w = fake_writer(tmp_path, fake)
        with pytest.raises(QuickBooksError):
            loader.apply(plan_for(workspace), w, log, out=lambda *_: None)
        before = copy.deepcopy(fake.accounts)
        fake.taken = {"OpeningBalanceEquity"}           # the fixed loader, same company
        fake.calls.clear()
        counts = loader.apply(plan_for(workspace), w, log, out=lambda *_: None)
        assert counts == {"accounts_created": 1, "accounts_reused": 5,
                          "entries_created": 18, "entries_skipped": 0}
        assert {k: fake.accounts[k] for k in before} == before   # untouched
        posted = {json.loads(b)["Name"] for m, u, b in fake.calls
                  if m == "POST" and u.split("?")[0].endswith("/account")}
        assert posted == {loader.CLEARING}
        logged = [c["Name"] for c in json.loads(log.read_text(encoding="utf-8"))["created"]
                  if c["kind"] == "Account"]
        assert len(logged) == 6 and len(set(logged)) == 6        # no duplicates

    def test_another_400_is_not_retried(self, workspace, tmp_path):
        class Duplicate(FakeQBO):
            def __call__(self, method, url, headers, body, timeout):
                if method == "POST" and url.split("?")[0].endswith("/account"):
                    self.calls.append((method, url, body))
                    return 400, b'{"Fault":{"Error":[{"Message":"Duplicate Name Exists Error"}]}}'
                return super().__call__(method, url, headers, body, timeout)
        fake = Duplicate()
        with pytest.raises(QuickBooksError, match="Duplicate Name"):
            loader.apply(plan_for(workspace), fake_writer(tmp_path, fake),
                         tmp_path / "log.json", out=lambda *_: None)
        assert len([c for c in fake.calls if c[0] == "POST" and "/account" in c[1]]) == 1


class FlakyQBO(FakeQBO):
    """A FakeQBO whose network fails as told. `fail(n, when)`: the next n requests for
    which `when(method, url)` holds raise, like a DNS failure; `after=True` makes the
    request reach QuickBooks first and lose only the reply."""

    def __init__(self):
        super().__init__()
        self.plan: list[tuple] = []

    def fail(self, n, when=lambda m, u: True, error=None, after=False):
        self.plan.append([n, when, error or urllib.error.URLError(
            OSError(11001, "getaddrinfo failed")), after])

    def __call__(self, method, url, headers, body, timeout):
        for rule in self.plan:
            if rule[0] and rule[1](method, url):
                rule[0] -= 1
                if rule[3]:
                    super().__call__(method, url, headers, body, timeout)
                raise rule[2]
        return super().__call__(method, url, headers, body, timeout)


def is_create(kind):
    return lambda m, u: m == "POST" and u.split("?")[0].endswith("/" + kind)


def flaky_writer(tmp_path, fake, slept):
    return loader.SandboxWriter(creds(), quickbooks_api.TokenStore(
        tmp_path / ".secrets" / "t.json"), transport=fake, sleep=slept.append)


class TestNetworkErrors:
    def test_a_short_outage_is_retried(self, workspace, tmp_path):
        fake, slept = FlakyQBO(), []
        fake.fail(2, lambda m, u: m == "GET", error=TimeoutError("timed out"))
        counts = loader.apply(plan_for(workspace), flaky_writer(tmp_path, fake, slept),
                              tmp_path / "log.json", out=lambda *_: None)
        assert counts["accounts_created"] == 6 and counts["entries_created"] == 18
        assert slept == [1.0, 2.0]

    def test_the_token_refresh_is_retried(self, workspace, tmp_path):
        fake, slept = FlakyQBO(), []
        fake.fail(1, lambda m, u: u == TOKEN_URL, error=ConnectionResetError())
        loader.apply(plan_for(workspace), flaky_writer(tmp_path, fake, slept),
                     tmp_path / "log.json", out=lambda *_: None)
        assert len(fake.entries) == 18 and slept == [1.0]

    def test_a_lost_reply_does_not_create_twice(self, workspace, tmp_path):
        fake, slept, log = FlakyQBO(), [], tmp_path / "log.json"
        fake.fail(1, is_create("account"), error=ConnectionResetError(), after=True)
        fake.fail(1, is_create("journalentry"), error=TimeoutError(), after=True)
        loader.apply(plan_for(workspace), flaky_writer(tmp_path, fake, slept), log,
                     out=lambda *_: None)
        assert len(fake.accounts) == 6 and len(fake.entries) == 18
        created = json.loads(log.read_text(encoding="utf-8"))["created"]
        assert len(created) == 24 == len({c["Id"] for c in created})

    def test_a_dns_failure_stops_with_one_line_and_the_log_is_complete(
            self, workspace, tmp_path):
        fake, slept, log = FlakyQBO(), [], tmp_path / "log.json"
        fake.accounts["5"] = {"Id": "5", "Name": "RE - Rent", "AccountType": "Expense",
                              "Active": True}                  # existed before this run
        made = []
        real = fake._id
        fake._id = lambda: made.append(real()) or made[-1]
        plan = plan_for(workspace)
        # As in the real run: DNS goes after some accounts are made, and stays gone.
        state = {"creates": 0}

        def down(m, u):
            if is_create("account")(m, u):
                state["creates"] += 1
            return state["creates"] > 3
        fake.fail(10 ** 6, down)
        with pytest.raises(loader.NetworkError) as exc:
            loader.apply(plan, flaky_writer(tmp_path, fake, slept), log,
                         out=lambda *_: None)
        assert str(exc.value) == ("Network error talking to QuickBooks. Nothing is "
                                  "half-written; rerun --apply to continue.")
        logged = json.loads(log.read_text(encoding="utf-8"))["created"]
        assert len(made) == 3 and fake.entries == {}
        assert sorted(c["Id"] for c in logged) == sorted(made)          # all, and only
        assert "5" not in {c["Id"] for c in logged}

        # Back online: a re-run finishes, and --undo then removes every entry.
        fake.plan.clear()
        loader.apply(plan, flaky_writer(tmp_path, fake, slept), log, out=lambda *_: None)
        assert len(fake.entries) == 18
        loader.undo(flaky_writer(tmp_path, fake, slept), log, confirm=lambda _: "DELETE",
                    out=lambda *_: None)
        assert fake.entries == {}
        assert "5" not in {c["Id"] for c in
                           json.loads(log.read_text(encoding="utf-8"))["created"]}

    def test_the_command_prints_one_line_not_a_traceback(self, workspace, tmp_path,
                                                         monkeypatch, capsys):
        fake, plan = FlakyQBO(), plan_for(workspace)
        fake.fail(10 ** 6)
        monkeypatch.setattr(loader, "build_plan", lambda *_: plan)
        monkeypatch.setattr(loader, "writer", lambda: flaky_writer(tmp_path, fake, []))
        monkeypatch.setattr(loader, "log_path", lambda: tmp_path / "log.json")
        assert loader.main(["--apply"]) == 1
        err = capsys.readouterr().err
        assert err.strip() == ("Network error talking to QuickBooks. Nothing is "
                               "half-written; rerun --apply to continue.")
        assert "Traceback" not in err


class TestUndo:
    def _applied(self, workspace, tmp_path):
        fake, log = FakeQBO(), tmp_path / "log.json"
        w = fake_writer(tmp_path, fake)
        loader.apply(plan_for(workspace), w, log, out=lambda *_: None)
        return fake, w, log

    def test_asks_first(self, workspace, tmp_path):
        fake, w, log = self._applied(workspace, tmp_path)
        counts = loader.undo(w, log, confirm=lambda _: "yes", out=lambda *_: None)
        assert counts["cancelled"] and len(fake.entries) == 18

    def test_deletes_only_what_it_logged(self, workspace, tmp_path):
        fake, w, log = self._applied(workspace, tmp_path)
        fake.entries["999"] = {"Id": "999", "SyncToken": "0", "DocNumber": "RE-2027-01"}
        counts = loader.undo(w, log, confirm=lambda _: "DELETE", out=lambda *_: None)
        assert counts == {"deleted": 18, "left_alone": 0, "already_gone": 0}
        assert list(fake.entries) == ["999"]
        assert len(fake.accounts) == 6              # accounts are kept

    def test_an_entry_changed_since_is_left_alone(self, workspace, tmp_path):
        fake, w, log = self._applied(workspace, tmp_path)
        changed = next(e for e in fake.entries.values() if e["DocNumber"] == "RE-2025-03")
        changed["SyncToken"] = "1"
        counts = loader.undo(w, log, confirm=lambda _: "DELETE", out=lambda *_: None)
        assert counts["left_alone"] == 1 and list(fake.entries) == [changed["Id"]]

    def test_another_company_is_refused(self, workspace, tmp_path):
        fake, _, log = self._applied(workspace, tmp_path)
        other = fake_writer(tmp_path, fake, QBO_REALM_ID="1111222233334444")
        with pytest.raises(QuickBooksError, match="different sandbox company"):
            loader.undo(other, log, confirm=lambda _: "DELETE", out=lambda *_: None)
        assert len(fake.entries) == 18


# ---- reading it back: account prefix, the 18-month pull, the round trip --------------------

class TestAccountPrefix:
    def test_other_accounts_and_their_totals_are_left_out(self, workspace):
        report = loader.simulated_report(plan_for(workspace))
        config = ClientConfig.load("multi_qb", Settings.from_env(workspace))
        warnings: list[dict] = []
        sheet = read_profit_and_loss(report, "sim", config, warnings)
        labels = [i.label for i in sheet.line_items]
        assert "Sample services" not in labels
        assert not any("Job" in label for label in labels)
        assert "RE - Sales" in labels
        by = {i.label: i for i in sheet.line_items}
        for total in ("Total Income", "Total Expenses", "Gross Profit",
                      "Net Operating Income", "Net Income"):
            assert by[total].omitted_by_source and by[total].cells == {}, total
        # No sample account in COGS, so Intuit's COGS total is still used.
        assert not by["Total Cost of Goods Sold"].omitted_by_source
        assert [w["code"] for w in warnings] == ["quickbooks_accounts_left_out"]

    def test_without_the_setting_nothing_is_left_out(self, workspace):
        report = loader.simulated_report(plan_for(workspace))
        raw = yaml.safe_load((workspace / "config" / "clients" / "multi_qb.yaml")
                             .read_text(encoding="utf-8"))
        del raw["source"]["account_prefix"]
        config = ClientConfig(raw, Path("multi_qb.yaml"))
        sheet = read_profit_and_loss(report, "sim", config, [])
        assert "Sample services" in [i.label for i in sheet.line_items]


class TestPullAndRoundTrip:
    def test_pull_quickbooks_reads_all_18_months(self, workspace, tmp_path):
        body = json.dumps(loader.simulated_report(plan_for(workspace))).encode()

        def transport(method, url, headers, data, timeout):
            if method == "POST":
                return 200, json.dumps({"access_token": ACCESS,
                                        "refresh_token": ROTATED}).encode()
            assert "start_date=2025-01-01" in url and "end_date=2026-06-30" in url
            return 200, body

        (workspace / ".env").write_text(
            "\n".join(f"{k}={v}" for k, v in SECRETS.items()), encoding="utf-8")
        config = ClientConfig.load("multi_qb", Settings.from_env(workspace))
        result = pull_snapshot(config, workspace, workspace / "data" / "clients",
                               "2025-01", "2026-06", transport=transport)
        assert (result["first_period"], result["last_period"],
                result["period_count"]) == ("2025-01", "2026-06", 18)

    def test_the_round_trip_matches_every_month(self, workspace):
        save_snapshot(workspace, loader.simulated_report(plan_for(workspace)))
        result = round_trip("multi_co", "multi_qb", workspace)
        assert result["ok"], result["mismatches"][:3]
        assert [r["period"] for r in result["rows"]] == MONTHS
        assert all(r["mismatched"] == 0 for r in result["rows"])
        assert sum(r["ttm_compared"] for r in result["rows"]) > 0

    def test_one_changed_cent_is_caught(self, workspace):
        report = loader.simulated_report(plan_for(workspace))
        income = report["Rows"]["Row"][0]["Rows"]["Row"]
        sales = next(r for r in income if r["ColData"][0]["value"] == "RE - Sales")
        sales["ColData"][3]["value"] = "1020.01"             # Mar 2025: 1,020.00 in the book
        save_snapshot(workspace, report)
        result = round_trip("multi_co", "multi_qb", workspace)
        assert not result["ok"]
        bad = {(m["period"], m["metric"]) for m in result["mismatches"]}
        assert ("2025-03", "revenue.total") in bad

    def test_sample_accounts_would_be_caught(self, workspace):
        cfg = workspace / "config" / "clients" / "multi_qb.yaml"
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        del raw["source"]["account_prefix"]
        cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        save_snapshot(workspace, loader.simulated_report(plan_for(workspace)))
        result = round_trip("multi_co", "multi_qb", workspace)
        assert ("2025-01", "revenue.total") in {(m["period"], m["metric"])
                                               for m in result["mismatches"]}

    def test_a_missing_month_fails(self, workspace):
        source = workspace / "data" / "clients" / "multi_co" / "source" / "multi.xlsx"
        save_snapshot(workspace, loader.simulated_report(plan_for(workspace)))
        build_monthly_workbook(source, MONTHS + ["2026-07"])
        result = round_trip("multi_co", "multi_qb", workspace)
        assert not result["ok"] and result["months_missing_from_snapshot"] == ["2026-07"]

    def test_the_cli_command(self, workspace, capsys):
        save_snapshot(workspace, loader.simulated_report(plan_for(workspace)))
        code = cli_main(["round-trip", "--excel-client", "multi_co", "--qb-client",
                         "multi_qb", "--root", str(workspace)])
        out = capsys.readouterr().out
        assert code == EXIT_OK
        assert "RESULT: all 18 months match" in out
        code = cli_main(["round-trip", "--excel-client", "multi_co", "--qb-client",
                         "multi_qb", "--root", str(workspace), "--json"])
        doc = json.loads(capsys.readouterr().out)
        assert code == EXIT_OK and doc["ok"] and doc["command"] == "round-trip"
        assert EXIT_DATA != EXIT_OK


# ---- the real configs ---------------------------------------------------------------------

def _yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / "clients" / f"{name}.yaml")
                          .read_text(encoding="utf-8"))


class TestConfigs:
    def test_categories_are_abc_inc_s(self):
        abc = _yaml("abc_inc")
        for name in ("qb_sandbox_abc", "qb_sandbox_abc_fixture"):
            cfg = _yaml(name)
            assert cfg["opex_categories"] == abc["opex_categories"]
            assert cfg["fallback_opex_category"] == abc["fallback_opex_category"]
            assert cfg["thresholds"] == abc["thresholds"]

    def test_the_fixture_differs_only_in_name_and_file(self):
        live, fixture = _yaml("qb_sandbox_abc"), _yaml("qb_sandbox_abc_fixture")
        for cfg in (live, fixture):
            for key in ("client_id", "company_name"):
                cfg.pop(key)
        assert fixture["source"].pop("filename") == loader.FIXTURE_NAME
        assert live == fixture
        assert live["source"]["account_prefix"] == loader.PREFIX


class TestAbcWorkbook:
    def test_abc_plan(self, abc_workbook):
        plan = loader.build_plan("abc_inc", ROOT)
        assert len(plan.entries) == 18
        assert all(e.total("Debit") == e.total("Credit") for e in plan.entries)
        assert all(a.name.startswith(loader.PREFIX) and ":" not in a.name
                   for a in plan.accounts)

    def test_every_abc_line_keeps_its_category(self, abc_workbook, abc_config):
        twin = ClientConfig.load("qb_sandbox_abc", Settings.from_env(ROOT))
        for account in loader.build_plan("abc_inc", ROOT).accounts:
            if account.section == "opex":
                assert twin.categorise_opex(account.name) == \
                    abc_config.categorise_opex(account.workbook_label), account.name
