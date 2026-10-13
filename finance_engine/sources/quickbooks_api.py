"""Read-only QuickBooks Online reader for the CLI: sandbox only, GET only.

This is the only module in `finance_engine` that touches the network for QuickBooks, and
it is used only by `pull-quickbooks` (and `report --source quickbooks`). It fetches the
raw Profit and Loss report and nothing else. The engine never analyses a live response:
the pull is saved as a snapshot file, and `sources/quickbooks.py` reads that file like any
other source, so a run is reproducible and its input has a SHA-256.

Safety, each enforced in code and tested:

* **GET only.** Every API request is a GET. The one exception is the OAuth token refresh,
  a POST to Intuit's token endpoint, whitelisted by URL. No function here can create,
  update or delete anything.
* **Sandbox only.** The API host must be `sandbox-quickbooks.api.intuit.com`; anything
  else, production included, is refused before a request is made.
* **Its own token store.** The refresh token Intuit rotates is saved to a git-ignored file
  of this reader's own (default `.secrets/quickbooks_reader_token.json`). It never reads or
  writes the MCP server's `quickbooks-online-mcp-server/.env`: Intuit rotates refresh
  tokens, and two programs sharing one token would invalidate each other.
* **No secret in any message.** Client secret, refresh and access tokens, the realm ID and
  the authorization header are redacted from every exception and log line, including an
  error body that echoes them.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from finance_engine.credentials import CredentialStore, redact

SANDBOX_HOST = "sandbox-quickbooks.api.intuit.com"
SANDBOX_BASE_URL = f"https://{SANDBOX_HOST}"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
DEFAULT_TOKEN_STORE = Path(".secrets") / "quickbooks_reader_token.json"

#: The variable NAMES this reader reads (from the environment or the root .env).
ENV_NAMES = ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REFRESH_TOKEN", "QBO_REALM_ID",
             "QBO_ENVIRONMENT")


class QuickBooksError(Exception):
    """A pull failed. The message is redacted and says what to do."""


class TokenStore:
    """The reader's own refresh token, in a git-ignored JSON file. Never the MCP's."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        token = data.get("refresh_token")
        return str(token) if token else None

    def save(self, refresh_token: str, expires_in: int | None) -> None:
        """Write atomically: a temp file in the same folder, then replace."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "refresh_token": refresh_token,
            "saved_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                              .isoformat(),
            "refresh_token_expires_in_seconds": expires_in,
            "note": "Rotated by Intuit on refresh. Used only by finance_engine's "
                    "pull-quickbooks; never shared with the MCP server.",
        }, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def _urllib_transport(method: str, url: str, headers: dict, body: bytes | None,
                      timeout: float):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class QuickBooksReader:
    """Fetches one Profit and Loss report from the sandbox. Read-only."""

    def __init__(self, credentials: CredentialStore, token_store: TokenStore,
                 base_url: str = SANDBOX_BASE_URL, transport=None, timeout: float = 30.0):
        host = urllib.parse.urlparse(base_url).hostname
        if urllib.parse.urlparse(base_url).scheme != "https" or host != SANDBOX_HOST:
            raise QuickBooksError(
                f"Refusing to read from {host or base_url!r}: only the sandbox host "
                f"{SANDBOX_HOST} is allowed. Production QuickBooks is out of scope.")
        environment = (credentials.get("QBO_ENVIRONMENT") or "sandbox").strip().lower()
        if environment != "sandbox":
            raise QuickBooksError(
                f"QBO_ENVIRONMENT is {environment!r}; this reader works with the sandbox "
                f"only. Production QuickBooks is out of scope.")
        self.credentials = credentials
        self.token_store = token_store
        self.base_url = base_url.rstrip("/")
        self.transport = transport or _urllib_transport
        self.timeout = timeout
        self._access_token: str | None = None
        self._secrets: list[str] = []

    # ---- secrets ------------------------------------------------------------------

    def _clean(self, text) -> str:
        return redact(text, [*self.credentials.loaded_values(), *self._secrets])

    def _need(self, name: str) -> str:
        value = self.credentials.get(name)
        if not value:
            raise QuickBooksError(f"{name} is not set (environment or .env). See "
                                  f"docs/quickbooks_setup.md, 'The direct reader'.")
        return value

    # ---- the one POST: the OAuth token refresh -------------------------------------------

    def _refresh_token(self) -> None:
        """Exchange the refresh token for an access token, and store the rotated one."""
        client_id, secret = self._need("QBO_CLIENT_ID"), self._need("QBO_CLIENT_SECRET")
        stored, seeded = self.token_store.load(), self.credentials.get("QBO_REFRESH_TOKEN")
        candidates = [t for t in dict.fromkeys([stored, seeded]) if t]
        if not candidates:
            raise QuickBooksError("No refresh token: set QBO_REFRESH_TOKEN, from the reader's "
                                  "own Intuit app, in .env.")
        self._secrets += candidates
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        self._secrets.append(basic)
        last = ""
        # The stored (rotated) token first; the .env seed if the store is stale, as after
        # a re-authorisation that put a new token in .env.
        for token in candidates:
            body = urllib.parse.urlencode({"grant_type": "refresh_token",
                                           "refresh_token": token}).encode()
            status, raw = self.transport(
                "POST", TOKEN_URL,
                {"Authorization": f"Basic {basic}", "Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
                body, self.timeout)
            if status == 200:
                data = json.loads(raw.decode("utf-8"))
                self._access_token = data["access_token"]
                self._secrets.append(self._access_token)
                rotated = data.get("refresh_token") or token
                self._secrets.append(rotated)
                self.token_store.save(rotated, data.get("x_refresh_token_expires_in"))
                return
            last = f"HTTP {status}: {self._clean(raw.decode('utf-8', 'replace'))[:200]}"
        raise QuickBooksError(
            f"The token refresh was refused ({last}). The refresh token has expired or "
            f"was revoked: re-authorise the reader's own Intuit app and put the new "
            f"refresh token in QBO_REFRESH_TOKEN (docs/quickbooks_setup.md).")

    # ---- GET only ------------------------------------------------------------------------------

    def _get(self, path: str, params: dict) -> bytes:
        """One GET. Returns the response body exactly as Intuit sent it."""
        if self._access_token is None:
            self._refresh_token()
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        if urllib.parse.urlparse(url).hostname != SANDBOX_HOST:
            raise QuickBooksError("Refusing a request to a host other than the sandbox.")
        status, raw = self.transport(
            "GET", url, {"Authorization": f"Bearer {self._access_token}",
                         "Accept": "application/json"}, None, self.timeout)
        if status != 200:
            raise QuickBooksError(
                f"QuickBooks returned HTTP {status} for {path.replace(self._realm(), '<realm>')}"
                f": {self._clean(raw.decode('utf-8', 'replace'))[:300]}")
        return raw

    def _realm(self) -> str:
        realm = self._need("QBO_REALM_ID")
        if realm not in self._secrets:
            self._secrets.append(realm)
        return realm

    def profit_and_loss_raw(self, start: str, end: str) -> bytes:
        """The raw Profit and Loss response, one column per month, `start`..`end`
        (YYYY-MM-DD), byte for byte: this is what a snapshot stores."""
        try:
            raw = self._get(f"/v3/company/{self._realm()}/reports/ProfitAndLoss",
                            {"start_date": start, "end_date": end,
                             "summarize_column_by": "Month"})
            json.loads(raw.decode("utf-8"))          # it must at least be JSON
            return raw
        except QuickBooksError:
            raise
        except Exception as exc:                  # network, JSON: redacted, never raw
            raise QuickBooksError(f"The pull failed: {type(exc).__name__}: "
                                  f"{self._clean(exc)[:200]}") from None

    def profit_and_loss(self, start: str, end: str) -> dict:
        return json.loads(self.profit_and_loss_raw(start, end).decode("utf-8"))


# ---- the pull: fetch, check, save a snapshot --------------------------------------------

def month_range(start: str, end: str) -> tuple[str, str]:
    """`YYYY-MM`, `YYYY-MM` -> the first day of `start` and the last day of `end`."""
    import calendar
    from finance_engine.model import Period
    first, last = Period.from_key(start), Period.from_key(end)
    if last.ordinal() < first.ordinal():
        raise QuickBooksError(f"--end {end} is before --start {start}.")
    day = calendar.monthrange(last.year, last.month)[1]
    return f"{first.key}-01", f"{last.key}-{day:02d}"


def pull_snapshot(config, root: Path, data_dir: Path, start: str, end: str,
                  transport=None) -> dict:
    """Pull the P&L for whole months `start`..`end` and save it as a new snapshot.

    The response is checked with the snapshot reader before anything is written, so a
    range with no data, a partial month or an unexpected report gives a clear message and
    leaves no file. A snapshot is never overwritten: each pull gets its own name.
    """
    from finance_engine.credentials import EnvCredentialStore
    from finance_engine.model import LayoutError
    from finance_engine.sources.quickbooks import read_profit_and_loss

    if config.source_type != "quickbooks":
        raise QuickBooksError(f"{config.client_id} is not a QuickBooks client: its config "
                              f"says source.type {config.source_type!r}.")
    first_day, last_day = month_range(start, end)
    credentials = EnvCredentialStore(list(ENV_NAMES), env_file=Path(root) / ".env")
    reader = QuickBooksReader(credentials, TokenStore(Path(root) / DEFAULT_TOKEN_STORE),
                              transport=transport)
    raw = reader.profit_and_loss_raw(first_day, last_day)
    report = json.loads(raw.decode("utf-8"))
    try:
        sheet = read_profit_and_loss(report, f"{start}..{end}", config)
    except LayoutError as exc:
        raise QuickBooksError(reader._clean(str(exc))) from None
    folder = Path(data_dir) / config.client_id / "source"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"qb_pl_{start}_{end}_{stamp}.json"
    n = 1
    while path.exists():                              # never overwrite a snapshot
        n += 1
        path = folder / f"qb_pl_{start}_{end}_{stamp}_{n}.json"
    path.write_bytes(raw)
    import hashlib
    keys = [p.key for p in sheet.periods]
    return {"path": path, "filename": path.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "first_period": keys[0], "last_period": keys[-1], "period_count": len(keys)}
