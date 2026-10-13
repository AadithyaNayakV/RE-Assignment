"""One real, read-only pull from the QuickBooks sandbox. Run by hand; never run by the tests.

    python scripts/qb_smoke.py                                  # the last three months
    python scripts/qb_smoke.py --start 2025-10 --end 2026-10    # a chosen range

It makes one OAuth token refresh (a POST to Intuit's token endpoint; Intuit rotates the
refresh token, and the new one is saved to the reader's own git-ignored token store) and
one GET of the Profit and Loss report, one column per month. It saves nothing else: no
snapshot is written (use `pull-quickbooks` for that). It prints only: the months returned,
whether QuickBooks reported data, the response's SHA-256, and whether the qb_sandbox
mapping reads it and reconciles with Intuit's own totals. It prints no token, secret,
realm ID or authorization header; an error message is redacted before it is shown.

Needs QBO_CLIENT_ID, QBO_CLIENT_SECRET, QBO_REFRESH_TOKEN and QBO_REALM_ID (the reader's
own Intuit app, not the MCP server's) in the environment or the root .env. Sandbox only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finance_engine.config import ClientConfig  # noqa: E402
from finance_engine.credentials import EnvCredentialStore  # noqa: E402
from finance_engine.model import LayoutError  # noqa: E402
from finance_engine.sources.quickbooks_api import (DEFAULT_TOKEN_STORE,  # noqa: E402
                                                   ENV_NAMES, QuickBooksError,
                                                   QuickBooksReader, TokenStore,
                                                   month_range)


def _last_three_months() -> tuple[str, str]:
    today = dt.date.today().replace(day=1)
    end = today - dt.timedelta(days=1)
    start = (end.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    start = (start - dt.timedelta(days=1)).replace(day=1)
    return f"{start:%Y-%m}", f"{end:%Y-%m}"


def smoke(start: str, end: str, transport=None, out=print, credentials=None,
          token_store=None) -> int:
    from finance_engine.metrics import build_metrics
    from finance_engine.sources.quickbooks import QuickBooksSnapshotSource, read_profit_and_loss

    credentials = credentials or EnvCredentialStore(list(ENV_NAMES), env_file=ROOT / ".env")
    missing = [n for n in ENV_NAMES[:4] if not credentials.present(n)]
    if missing:
        out(f"FAIL  not set: {', '.join(missing)} (names only; see docs/quickbooks_setup.md)")
        return 1
    try:
        first_day, last_day = month_range(start, end)
        reader = QuickBooksReader(credentials,
                                  token_store or TokenStore(ROOT / DEFAULT_TOKEN_STORE),
                                  transport=transport)
        raw = reader.profit_and_loss_raw(first_day, last_day)
    except QuickBooksError as exc:
        out(f"FAIL  {exc}")
        return 1
    report = json.loads(raw.decode("utf-8"))
    options = {o.get("Name"): o.get("Value") for o in report["Header"].get("Option", [])}
    out(f"ok    one token refresh, one GET ({first_day}..{last_day})")
    out(f"      report data: {'none' if options.get('NoReportData') == 'true' else 'yes'}")
    out(f"      sha256: {hashlib.sha256(raw).hexdigest()}")
    config = ClientConfig.load_path(ROOT / "config" / "clients" / "qb_sandbox.yaml")
    try:
        sheet = read_profit_and_loss(report, "smoke", config)
    except LayoutError as exc:
        out(f"      mapping: not readable with qb_sandbox.yaml: {reader._clean(str(exc))}")
        return 1
    keys = [p.key for p in sheet.periods]
    out(f"      months: {keys[0]}..{keys[-1]} ({len(keys)})")
    # The engine reads files, so the check goes through a temporary snapshot file that is
    # deleted afterwards.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "smoke.json"
        path.write_bytes(raw)
        doc = build_metrics(QuickBooksSnapshotSource(path).load(config), config, keys[-1])
    recon = doc["reconciliation"]
    out(f"      reconciliation for {keys[-1]}: "
        f"{recon['checks_run'] - recon['checks_failed']}/{recon['checks_run']} passed")
    return 0 if recon["all_passed"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", help="first month, YYYY-MM (default: three months ago)")
    ap.add_argument("--end", help="last month, YYYY-MM (default: last month)")
    args = ap.parse_args(argv)
    start, end = _last_three_months()
    return smoke(args.start or start, args.end or end)


if __name__ == "__main__":
    raise SystemExit(main())
