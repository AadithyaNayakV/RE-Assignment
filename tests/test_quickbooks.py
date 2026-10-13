"""The read-only QuickBooks reader and the snapshot source (Phase 5C, part 2).

No test here touches the network. A fake Intuit server answers the token refresh and the
report GET. `tests/fixtures/qb_pl_monthly.json` is a real sandbox Profit and Loss response
(Oct 2025 - Oct 2026, one column per month), scrubbed: it never held a realm ID or a token.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from finance_engine.cli import EXIT_OK, EXIT_USAGE, main
from finance_engine.config import ClientConfig
from finance_engine.credentials import EnvCredentialStore
from finance_engine.metrics import build_metrics
from finance_engine.model import LayoutError
from finance_engine.sources import quickbooks_api
from finance_engine.sources.quickbooks import QuickBooksSnapshotSource, read_profit_and_loss
from finance_engine.sources.quickbooks_api import (DEFAULT_TOKEN_STORE, SANDBOX_BASE_URL,
                                                   TOKEN_URL, QuickBooksError,
                                                   QuickBooksReader, TokenStore)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "qb_pl_monthly.json"
CONFIG = ROOT / "config" / "clients" / "qb_sandbox.yaml"

CLIENT_ID = "ABfakeClientId0123456789abcdef"
SECRET = "fakeClientSecret0123456789ABCDEFxyz"
REFRESH = "AB11fakeRefreshToken0123456789abcdefghijklmnopq"
ROTATED = "AB11fakeRotatedToken9876543210zyxwvutsrqponmlkj"
ACCESS = "eyJfakeAccessToken.abcdefghijklmnopqrstuvwxyz0123456789"
REALM = "9341455123456789"
SECRETS = [CLIENT_ID, SECRET, REFRESH, ROTATED, ACCESS, REALM]


def creds(**over):
    env = {"QBO_CLIENT_ID": CLIENT_ID, "QBO_CLIENT_SECRET": SECRET,
           "QBO_REFRESH_TOKEN": REFRESH, "QBO_REALM_ID": REALM,
           "QBO_ENVIRONMENT": "sandbox", **over}
    return EnvCredentialStore(list(quickbooks_api.ENV_NAMES), environ=env)


class FakeIntuit:
    """Answers the token refresh and the P&L GET; records every request."""

    def __init__(self, report: bytes | None = None, token_status=200, get_status=200,
                 echo=False, refuse=()):
        self.calls: list[tuple[str, str, dict, bytes | None]] = []
        self.report = report if report is not None else FIXTURE.read_bytes()
        self.token_status, self.get_status, self.echo = token_status, get_status, echo
        self.refuse = set(refuse)

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        if method == "POST" and url == TOKEN_URL:
            sent = dict(p.split("=", 1) for p in body.decode().split("&"))
            if self.token_status != 200 or sent.get("refresh_token") in self.refuse:
                echo = (f'{{"error":"invalid_grant","refresh_token":"{sent.get("refresh_token")}",'
                        f'"auth":"{headers.get("Authorization")}","secret":"{SECRET}"}}')
                return 400, (echo if self.echo else '{"error":"invalid_grant"}').encode()
            return 200, json.dumps({"access_token": ACCESS, "refresh_token": ROTATED,
                                    "expires_in": 3600,
                                    "x_refresh_token_expires_in": 8726400}).encode()
        if method == "GET":
            if self.get_status != 200:
                echo = (f'{{"Fault":"AuthenticationFailed","header":'
                        f'"{headers.get("Authorization")}","realm":"{REALM}","url":"{url}"}}')
                return self.get_status, (echo if self.echo else '{"Fault":"x"}').encode()
            return 200, self.report
        raise AssertionError(f"unexpected request: {method} {url}")


def reader(tmp_path, fake=None, **kw):
    return QuickBooksReader(kw.pop("credentials", creds()),
                            TokenStore(tmp_path / ".secrets" / "t.json"),
                            transport=fake or FakeIntuit(), **kw)


# ---- read-only and sandbox-only ---------------------------------------------------------

class TestReadOnly:
    SOURCE = Path(quickbooks_api.__file__).read_text(encoding="utf-8")

    def test_no_write_capable_function_exists(self):
        tree = ast.parse(self.SOURCE)
        names = {n.name.lower() for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        for verb in ("create", "update", "delete", "post", "put", "patch", "send", "write"):
            assert not any(verb in n for n in names), (verb, names)

    def test_only_the_token_refresh_uses_a_non_get_call(self):
        tree = ast.parse(self.SOURCE)
        found = {}
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef):
                for node in ast.walk(fn):
                    if isinstance(node, ast.Constant) and node.value in (
                            "POST", "PUT", "PATCH", "DELETE"):
                        found.setdefault(node.value, set()).add(fn.name)
        assert found == {"POST": {"_refresh_token"}}
        # And the POST goes only to Intuit's token endpoint.
        assert TOKEN_URL == "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

    def test_every_request_is_a_get_except_the_refresh(self, tmp_path):
        fake = FakeIntuit()
        reader(tmp_path, fake).profit_and_loss("2025-10-01", "2026-10-31")
        methods = [(m, u.split("?")[0]) for m, u, _, _ in fake.calls]
        assert methods[0] == ("POST", TOKEN_URL)
        assert [m for m, _ in methods[1:]] == ["GET"]
        assert methods[1][1].startswith(SANDBOX_BASE_URL + "/v3/company/")
        assert "summarize_column_by=Month" in fake.calls[1][1]

    @pytest.mark.parametrize("base", ["https://quickbooks.api.intuit.com",
                                      "http://sandbox-quickbooks.api.intuit.com",
                                      "https://sandbox-quickbooks.api.intuit.com.evil.example"])
    def test_a_non_sandbox_host_is_refused(self, tmp_path, base):
        fake = FakeIntuit()
        with pytest.raises(QuickBooksError, match="only the sandbox host"):
            reader(tmp_path, fake, base_url=base)
        assert fake.calls == []

    def test_a_production_environment_is_refused(self, tmp_path):
        with pytest.raises(QuickBooksError, match="sandbox only"):
            reader(tmp_path, credentials=creds(QBO_ENVIRONMENT="production"))


# ---- tokens ------------------------------------------------------------------------------

class TestTokens:
    def test_the_rotated_token_is_saved_to_the_readers_own_store(self, tmp_path):
        r = reader(tmp_path)
        r.profit_and_loss("2025-10-01", "2026-10-31")
        saved = json.loads((tmp_path / ".secrets" / "t.json").read_text("utf-8"))
        assert saved["refresh_token"] == ROTATED

    def test_the_stored_token_is_used_first_then_the_env_seed(self, tmp_path):
        store = TokenStore(tmp_path / ".secrets" / "t.json")
        store.save("AB11staleStoredToken000000000000000000000000", None)
        fake = FakeIntuit(refuse={"AB11staleStoredToken000000000000000000000000"})
        QuickBooksReader(creds(), store, transport=fake).profit_and_loss("2025-10-01",
                                                                         "2026-10-31")
        sent = [dict(p.split("=", 1) for p in b.decode().split("&"))["refresh_token"]
                for m, _, _, b in fake.calls if m == "POST"]
        assert sent == ["AB11staleStoredToken000000000000000000000000", REFRESH]
        assert store.load() == ROTATED

    def test_the_default_store_is_git_ignored_and_not_the_mcp_servers(self):
        assert DEFAULT_TOKEN_STORE == Path(".secrets") / "quickbooks_reader_token.json"
        ignored = subprocess.run(["git", "check-ignore", "-q", str(DEFAULT_TOKEN_STORE)],
                                 cwd=ROOT).returncode == 0
        assert ignored
        # The MCP server's folder is named in the docstrings (to say it is never used),
        # but no code string refers to it.
        tree = ast.parse(TestReadOnly.SOURCE)
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)}
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                   and isinstance(n.value, str) and id(n) not in docs]
        assert not any("quickbooks-online-mcp-server" in s or "mcp-server" in s.lower()
                       for s in strings)


# ---- secrets never surface ---------------------------------------------------------------

class TestSecrets:
    def test_a_refused_refresh_that_echoes_secrets(self, tmp_path):
        with pytest.raises(QuickBooksError) as err:
            reader(tmp_path, FakeIntuit(token_status=400, echo=True)).profit_and_loss(
                "2025-10-01", "2026-10-31")
        text = str(err.value)
        assert "token refresh was refused" in text and "re-authorise" in text
        for s in SECRETS:
            assert s not in text

    def test_a_failed_get_that_echoes_the_header_and_realm(self, tmp_path):
        with pytest.raises(QuickBooksError) as err:
            reader(tmp_path, FakeIntuit(get_status=401, echo=True)).profit_and_loss(
                "2025-10-01", "2026-10-31")
        text = str(err.value)
        assert "HTTP 401" in text
        for s in SECRETS:
            assert s not in text

    def test_the_cli_never_prints_or_writes_a_secret(self, tmp_path, monkeypatch, capsys):
        root = _qb_root(tmp_path, monkeypatch)
        fake = FakeIntuit(token_status=400, echo=True)
        monkeypatch.setattr(quickbooks_api, "_urllib_transport", fake)
        code = main(["pull-quickbooks", "--client", "qb_sandbox", "--start", "2025-10",
                     "--end", "2026-10", "--json", "--root", str(root)])
        captured = capsys.readouterr()
        assert code == EXIT_USAGE and json.loads(captured.out)["ok"] is False
        for s in SECRETS:
            assert s not in captured.out and s not in captured.err

        monkeypatch.setattr(quickbooks_api, "_urllib_transport", FakeIntuit())
        assert main(["report", "--client", "qb_sandbox", "--period", "2026-09",
                     "--source", "quickbooks", "--start", "2025-10", "--end", "2026-10",
                     "--provider", "deterministic", "--json", "--root", str(root)]) == EXIT_OK
        captured = capsys.readouterr()
        outputs = list((root / "output").rglob("*.*"))
        snapshot = next((root / "data" / "clients" / "qb_sandbox" / "source").glob("*.json"))
        assert outputs
        for s in SECRETS:
            assert s not in captured.out and s not in captured.err
            assert s not in snapshot.read_text("utf-8")
            for f in outputs:
                assert s not in f.read_text("utf-8"), f


# ---- the pull -------------------------------------------------------------------------------

def _qb_root(tmp_path, monkeypatch):
    (tmp_path / "config" / "clients").mkdir(parents=True)
    (tmp_path / "config" / "clients" / "qb_sandbox.yaml").write_text(
        CONFIG.read_text("utf-8"), encoding="utf-8")
    for name, value in (("QBO_CLIENT_ID", CLIENT_ID), ("QBO_CLIENT_SECRET", SECRET),
                        ("QBO_REFRESH_TOKEN", REFRESH), ("QBO_REALM_ID", REALM),
                        ("QBO_ENVIRONMENT", "sandbox")):
        monkeypatch.setenv(name, value)
    return tmp_path


class TestPull:
    def test_pull_saves_the_raw_response_as_a_new_snapshot(self, tmp_path, monkeypatch,
                                                            capsys):
        root = _qb_root(tmp_path, monkeypatch)
        monkeypatch.setattr(quickbooks_api, "_urllib_transport", FakeIntuit())
        args = ["pull-quickbooks", "--client", "qb_sandbox", "--start", "2025-10",
                "--end", "2026-10", "--json", "--root", str(root)]
        assert main(args) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        path = root / payload["snapshot"]
        assert path.read_bytes() == FIXTURE.read_bytes()          # byte for byte
        assert payload["first_period"] == "2025-10" and payload["last_period"] == "2026-10"
        assert main(args) == EXIT_OK                               # never overwritten
        assert len(list(path.parent.glob("*.json"))) == 2

    def test_the_whole_months_are_requested(self, tmp_path, monkeypatch):
        root = _qb_root(tmp_path, monkeypatch)
        fake = FakeIntuit()
        monkeypatch.setattr(quickbooks_api, "_urllib_transport", fake)
        main(["pull-quickbooks", "--client", "qb_sandbox", "--start", "2026-01",
              "--end", "2026-02", "--root", str(root)])
        url = next(u for m, u, _, _ in fake.calls if m == "GET")
        assert "start_date=2026-01-01" in url and "end_date=2026-02-28" in url

    def test_no_data_in_range_gives_a_clear_message(self, tmp_path, monkeypatch, capsys):
        root = _qb_root(tmp_path, monkeypatch)
        empty = json.loads(FIXTURE.read_text("utf-8"))
        empty["Header"]["Option"] = [{"Name": "NoReportData", "Value": "true"}]
        empty["Rows"] = {}
        monkeypatch.setattr(quickbooks_api, "_urllib_transport",
                            FakeIntuit(report=json.dumps(empty).encode()))
        code = main(["pull-quickbooks", "--client", "qb_sandbox", "--start", "2020-01",
                     "--end", "2020-03", "--root", str(root)])
        err = capsys.readouterr().err
        assert code == EXIT_USAGE
        assert "QuickBooks has no Profit and Loss data between" in err
        assert not (root / "data" / "clients" / "qb_sandbox" / "source").exists() or \
            not list((root / "data" / "clients" / "qb_sandbox" / "source").glob("*.json"))

    @pytest.mark.parametrize("start, end, message", [("2026-02", "2026-01", "before"),
                                                     ("2026-13", "2026-12", "YYYY-MM")])
    def test_bad_ranges(self, tmp_path, monkeypatch, capsys, start, end, message):
        root = _qb_root(tmp_path, monkeypatch)
        fake = FakeIntuit()
        monkeypatch.setattr(quickbooks_api, "_urllib_transport", fake)
        assert main(["pull-quickbooks", "--client", "qb_sandbox", "--start", start,
                     "--end", end, "--root", str(root)]) == EXIT_USAGE
        assert message in capsys.readouterr().err and fake.calls == []

    def test_a_non_quickbooks_client_is_refused_before_any_call(self, tmp_path, monkeypatch,
                                                                capsys):
        fake = FakeIntuit()
        monkeypatch.setattr(quickbooks_api, "_urllib_transport", fake)
        assert main(["pull-quickbooks", "--client", "test_clean", "--start", "2025-01",
                     "--end", "2025-02", "--root", str(ROOT)]) == EXIT_USAGE
        assert "not a QuickBooks client" in capsys.readouterr().err and fake.calls == []


# ---- the snapshot source, on the real fixture ----------------------------------------------

@pytest.fixture(scope="module")
def dataset():
    cfg = ClientConfig.load_path(CONFIG)
    return QuickBooksSnapshotSource(FIXTURE).load(cfg), cfg


class TestSnapshotSource:
    def test_months_and_sections(self, dataset):
        ds, _ = dataset
        assert ds.period_keys()[0] == "2025-10" and ds.period_keys()[-1] == "2026-10"
        assert len(ds.period_keys()) == 13
        sheet = ds.income_statement
        assert sheet.section_total("revenue").label == "Total Income"
        assert sheet.section_total("opex").label == "Total Expenses"
        assert sheet.reported("Net Operating Income") is not None

    def test_parent_accounts_and_ambiguous_names(self, dataset):
        ds, _ = dataset
        labels = {i.label: i for i in ds.income_statement.line_items}
        own = labels["Landscaping Services (own amount)"]
        assert own.role == "item" and own.section == "revenue"
        assert own.value("2026-09") == 642.50
        assert labels["Cost of Goods Sold (account)"].section == "cogs"
        assert labels["Total Landscaping Services"].role == "subtotal"
        assert labels["Job Expenses: Job Materials: Plants and Soil"].section == "opex"
        assert labels["Landscaping Services: Job Materials: Plants and Soil"].section == \
            "revenue"

    def test_other_income_and_expenses_stay_outside(self, dataset):
        ds, _ = dataset
        misc = ds.income_statement.by_label("Miscellaneous")
        assert misc.section is None
        assert all(i.label != "Miscellaneous" for s in ("revenue", "cogs", "opex")
                   for i in ds.income_statement.in_section(s))

    def test_everything_reconciles_with_intuits_totals(self, dataset):
        ds, cfg = dataset
        doc = build_metrics(ds, cfg, "2026-09")
        m = {x["name"]: x["value"] for x in doc["metrics"]}
        assert doc["reconciliation"]["all_passed"] and doc["reconciliation"]["checks_run"] == 65
        raw = json.loads(FIXTURE.read_text("utf-8"))
        sep = 12                                          # Oct 2025 is column 1
        summary = {r["Summary"]["ColData"][0]["value"]: float(r["Summary"]["ColData"][sep]["value"])
                   for r in raw["Rows"]["Row"] if r.get("Summary")}
        assert m["revenue.total"] == pytest.approx(summary["Total Income"])
        assert m["opex.total"] == pytest.approx(summary["Total Expenses"])
        assert m["operating_income"] == pytest.approx(summary["Net Operating Income"])
        assert m["net_income.reported"] == pytest.approx(summary["Net Income"])
        assert any("Other Expenses" in n and "not folded" in n
                   for n in doc["classification_notes"])

    def test_a_blank_cell_is_quickbooks_zero(self, dataset):
        ds, _ = dataset
        assert ds.income_statement.by_label("Design income").value("2025-10") == 0.0

    @pytest.mark.parametrize("change, message", [
        (lambda r: r["Header"].update(ReportName="BalanceSheet"), "ProfitAndLoss"),
        (lambda r: r["Header"].update(SummarizeColumnsBy="Total"), "one column per month"),
        (lambda r: r["Header"].update(Currency="EUR"), "is in EUR"),
        (lambda r: r["Columns"]["Column"][1]["MetaData"][0].update(Value="2025-10-15"),
         "not a whole calendar month"),
    ])
    def test_unexpected_reports_are_refused(self, change, message):
        raw = json.loads(FIXTURE.read_text("utf-8"))
        change(raw)
        with pytest.raises(LayoutError, match=message):
            read_profit_and_loss(raw, "x.json", ClientConfig.load_path(CONFIG))

    def test_the_fixture_holds_no_credentials(self):
        text = FIXTURE.read_text("utf-8").lower()
        for word in ("realm", "token", "authorization", "bearer", "secret"):
            assert word not in text


# ---- golden case 19 -------------------------------------------------------------------------

class TestGoldenCase:
    def test_the_fixture_client_maps_exactly_like_qb_sandbox(self):
        import yaml
        live = yaml.safe_load(CONFIG.read_text("utf-8"))
        fixture = yaml.safe_load((ROOT / "config" / "clients" / "qb_sandbox_fixture.yaml")
                                 .read_text("utf-8"))
        assert fixture["source"] == {"type": "quickbooks", "directory": "tests/fixtures",
                                     "filename": "qb_pl_monthly.json"}
        for d in (live, fixture):
            for key in ("client_id", "company_name", "source"):
                d.pop(key)
        assert fixture == live

    def test_the_expected_values_come_from_plain_json(self):
        import sys
        sys.path.insert(0, str(ROOT / "evals" / "tools"))
        try:
            import build_expected
        finally:
            sys.path.pop(0)
        body = build_expected.quickbooks_expected(
            {"input": {"workbook": "tests/fixtures/qb_pl_monthly.json",
                       "period": "2026-09"}})
        got = {v["metric"]: v["expected"] for v in body["values"]}
        # Intuit's own figures for Sep 2026, as printed in the report's Summary rows.
        assert got["revenue.total"] == pytest.approx(6641.38)
        assert got["opex.total"] == pytest.approx(2755.10)
        assert got["operating_income"] == pytest.approx(3481.28)
        assert got["net_income.reported"] == pytest.approx(1481.28)
        assert body["ttm"]["complete"] is True


# ---- the smoke script, offline -------------------------------------------------------------

class TestSmokeScript:
    def _smoke(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import qb_smoke
        finally:
            sys.path.pop(0)
        return qb_smoke

    def test_prints_months_and_reconciliation_and_no_secret(self, tmp_path):
        lines: list[str] = []
        fake = FakeIntuit()
        code = self._smoke().smoke("2025-10", "2026-10", transport=fake, out=lines.append,
                                   credentials=creds(),
                                   token_store=TokenStore(tmp_path / "t.json"))
        text = "\n".join(lines)
        assert code == 0
        assert "months: 2025-10..2026-10 (13)" in text
        assert "reconciliation for 2026-10: " in text
        assert [m for m, *_ in fake.calls] == ["POST", "GET"]
        for s in SECRETS:
            assert s not in text

    def test_a_refused_token_is_reported_redacted(self, tmp_path):
        lines: list[str] = []
        code = self._smoke().smoke("2025-10", "2026-10",
                                   transport=FakeIntuit(token_status=400, echo=True),
                                   out=lines.append, credentials=creds(),
                                   token_store=TokenStore(tmp_path / "t.json"))
        assert code == 1 and lines[0].startswith("FAIL")
        for s in SECRETS:
            assert s not in "\n".join(lines)
