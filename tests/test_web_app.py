"""The local web app (Phase 5D, part 2): every route, parity, loopback, no cross-origin
changes, no secret in any response, escaping, and no calculation in the UI.

Skips cleanly when fastapi (an optional dependency) is not installed. No network: the
QuickBooks pull uses the fake Intuit server. Workbooks are generated test books.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from finance_engine.cli import main  # noqa: E402
from finance_engine.service import parity  # noqa: E402
from finance_engine.web.app import UI_DIR, create_app  # noqa: E402
from tests.helpers import build_monthly_workbook, write_monthly_config  # noqa: E402
from tests.test_quickbooks import SECRETS, FakeIntuit, _qb_root  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"
UI = {"X-Finance-UI": "1"}
FAKE_ENV = {"GEMINI_API_KEY": "AIzaSyWEBFAKE-0123456789abcdefghij",
            "ANTHROPIC_API_KEY": "sk-ant-api03-WEBFAKE-0123456789abc"}
HOSTILE_LABEL = "<script>alert(1)</script> Services"
HOSTILE_CELL = "<img src=x onerror=alert(2)>"
MONTHS = [f"2025-{m:02d}" for m in range(1, 13) if m != 4]          # April is a gap


def _client(app, client_host="127.0.0.1") -> TestClient:
    return TestClient(app, base_url=BASE, client=(client_host, 50000))


def _wait(c: TestClient, job: dict, timeout: float = 120) -> dict:
    end = time.monotonic() + timeout
    while job["status"] not in ("succeeded", "failed", "timeout", "error"):
        assert time.monotonic() < end, job
        time.sleep(0.3)
        job = c.get(f"/api/jobs/{job['id']}").json()
    return job


@pytest.fixture
def root(tmp_path, monkeypatch, capsys):
    """multi_co with a hostile label and cell, a gap month, and two files; reports run."""
    for name in ("providers.yaml", "review.yaml"):
        (tmp_path / "config").mkdir(exist_ok=True)
        shutil.copy(ROOT / "config" / name, tmp_path / "config" / name)
    write_monthly_config(tmp_path / "config" / "clients")
    src = tmp_path / "data" / "clients" / "multi_co" / "source"
    src.mkdir(parents=True)
    book = build_monthly_workbook(src / "book.xlsx", MONTHS,
                                  overrides={("Rent", "2025-03"): HOSTILE_CELL})
    from openpyxl import load_workbook
    wb = load_workbook(book)
    wb.active["B8"] = HOSTILE_LABEL                                  # was "Services"
    wb.save(book)
    (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in FAKE_ENV.items()),
                                   encoding="utf-8")
    for k in FAKE_ENV:
        monkeypatch.delenv(k, raising=False)                        # read from .env
    monkeypatch.setenv("FINANCE_OUTPUT_DIR", "output/clients")
    for args in (["--period", "2025-06"], ["--scope", "year", "--year", "2025"],
                 ["--scope", "all"]):
        assert main(["report", "--client", "multi_co", *args, "--provider",
                     "deterministic", "--root", str(tmp_path)]) in (0, 2)
    capsys.readouterr()
    return tmp_path


@pytest.fixture
def c(root):
    return _client(create_app(root))


# ---- parity -----------------------------------------------------------------------------

class TestParity:
    def _app_routes(self, app):
        out = set()
        for r in app.routes:
            if isinstance(r, APIRoute) and r.path not in ("/", "/{rest:path}"):
                out |= {(m, r.path) for m in r.methods if m != "HEAD"}
        return out

    def test_every_route_has_a_cli_equivalent(self, root):
        app_routes = self._app_routes(create_app(root))
        table = {(r["method"], r["path"]) for r in parity.ROUTES}
        assert app_routes - table == set(), "routes without a CLI equivalent"
        assert table - app_routes == set(), "parity rows without a route"

    def test_no_route_runs_paid_evals(self, root):
        for r in create_app(root).routes:
            assert "skill" not in getattr(r, "path", "")


# ---- local only -----------------------------------------------------------------------

class TestLocalOnly:
    @pytest.mark.parametrize("host", ["evil.example", "192.168.1.5:8765", "0.0.0.0",
                                      "127.0.0.1.evil.example"])
    def test_other_host_headers_are_refused(self, c, host):
        r = c.get("/api/clients", headers={"Host": host})
        assert r.status_code == 400 and "127.0.0.1" in r.json()["error"]

    def test_localhost_is_accepted(self, c):
        assert c.get("/api/clients", headers={"Host": "localhost:8765"}).status_code == 200

    def test_a_remote_client_is_refused(self, root):
        r = _client(create_app(root), "10.0.0.7").get("/api/clients")
        assert r.status_code == 403

    def test_serve_binds_loopback_only(self, root):
        from finance_engine.web import app as web
        with pytest.raises(SystemExit):
            web.serve(root, "0.0.0.0", 8765)

    def test_post_needs_the_ui_header(self, c):
        r = c.post("/api/review/mark-reviewed", json={"client": "multi_co", "period": "2025-06",
                                                "by": "A. Writer"})
        assert r.status_code == 403

    def test_cross_origin_post_is_refused(self, c):
        r = c.post("/api/review/mark-reviewed", headers={**UI, "Origin": "http://evil.example"},
                   json={"client": "multi_co", "period": "2025-06", "by": "A. Writer"})
        assert r.status_code == 403

    def test_same_origin_post_is_accepted(self, c):
        r = c.post("/api/review/mark-reviewed", headers={**UI, "Origin": BASE},
                   json={"client": "multi_co", "period": "2025-06", "by": "A. Writer"})
        assert r.status_code == 200

    def test_security_headers(self, c):
        r = c.get("/")
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff"


class TestOnlyUiFilesAreServed:
    @pytest.mark.parametrize("path", [
        "/.env", "/static/../.env", "/static/%2e%2e/.env", "/static/..%2f.env",
        "/static/../../.env", "/config/clients/multi_co.yaml",
        "/static/../config/providers.yaml", "/data/clients/multi_co/source/book.xlsx",
        "/output/clients/multi_co/2025-06/metrics.json", "/.secrets/quickbooks_reader_token.json",
        "/static/../finance_engine/web/app.py", "/docs", "/openapi.json"])
    def test_nothing_outside_ui(self, c, path):
        r = c.get(path)
        assert r.status_code == 404, path
        for value in FAKE_ENV.values():
            assert value not in r.text

    def test_the_ui_files(self, c):
        for path in ("/", "/static/js/app.js", "/static/styles.css",
                     "/static/vendor/chart.umd.min.js", "/static/theme-init.js"):
            assert c.get(path).status_code == 200, path


# ---- every route ----------------------------------------------------------------------------

class TestRoutes:
    def test_reads(self, c):
        q = "client=multi_co&period=2025-06"
        for url in ["/api/clients", "/api/clients/multi_co/files",
                    "/api/clients/multi_co/periods", f"/api/report?{q}", f"/api/brief?{q}",
                    f"/api/export/report.md?{q}", f"/api/export/metrics.json?{q}",
                    "/api/charts?client=multi_co", "/api/charts?client=multi_co&year=2025",
                    f"/api/cloud-notice?{q}&keep_data_local=false", f"/api/review/status?{q}",
                    f"/api/review/log?{q}", "/api/review/queue", "/api/evals",
                    "/api/quickbooks/status", "/api/providers", "/api/jobs",
                    "/api/report?client=multi_co&scope=year&year=2025",
                    "/api/report?client=multi_co&scope=all"]:
            r = c.get(url)
            assert r.status_code == 200, (url, r.text[:300])

    def test_the_report_is_what_the_files_hold(self, root, c):
        r = c.get("/api/report?client=multi_co&period=2025-06").json()
        folder = root / "output" / "clients" / "multi_co" / "2025-06"
        assert r["report_md"] == (folder / "report.md").read_text("utf-8")
        assert r["metrics_doc"] == json.loads((folder / "metrics.json").read_text("utf-8"))
        assert r["commentary_log"] is None
        md = c.get("/api/export/report.md?client=multi_co&period=2025-06")
        assert md.text == (folder / "report.md").read_text("utf-8")
        assert "attachment" in md.headers["content-disposition"]

    def test_engine_errors_come_back_as_plain_messages(self, c):
        r = c.get("/api/report?client=multi_co&period=2025-02")
        assert r.status_code == 400
        assert "Run: python -m finance_engine report --client multi_co --period 2025-02" \
            in r.json()["error"]
        assert c.get("/api/clients/..%5Cx/files").status_code in (400, 404)
        assert c.get("/api/report?client=multi_co&period=2025-06&file=../x.xlsx") \
            .status_code == 400
        assert c.get("/api/clients/nobody/files").status_code == 400

    def test_commentary_log_only_with_the_cloud_footer(self, root, c):
        folder = root / "output" / "clients" / "multi_co" / "2025-06"
        (folder / "commentary_log.json").write_text('{"stale": true}', encoding="utf-8")
        assert c.get("/api/report?client=multi_co&period=2025-06").json()[
            "commentary_log"] is None

    def test_a_report_job_and_one_job_per_client(self, root, c):
        app_jobs = c.app.state.jobs
        slow = app_jobs.start_commands("multi_co", "slow",
                                       [[sys.executable, "-c", "import time; time.sleep(5)"]],
                                       timeout=20)
        r = c.post("/api/jobs/report", headers=UI, json={"client": "multi_co",
                                                         "period": "2025-06"})
        assert r.status_code == 409 and "already running" in r.json()["error"]
        app_jobs.wait(slow.id, 30)
        job = c.post("/api/jobs/report", headers=UI,
                     json={"client": "multi_co", "period": "2025-06",
                           "keep_data_local": True}).json()
        assert "--provider deterministic" in job["commands"][0]
        done = _wait(c, job)
        assert done["status"] == "succeeded" and done["result"]["provider"] == "deterministic"

    def test_the_free_evals_route_runs_only_the_free_commands(self, c, monkeypatch):
        from finance_engine.service import commands
        shown = [s for _, s in commands.free_eval_commands()]
        assert shown == ["python evals/run_engine_evals.py --compare",
                         "python evals/run_skill_evals.py --provider deterministic --compare"]
        monkeypatch.setattr(commands, "free_eval_commands", lambda: [
            ([sys.executable, "-c", "print('engine evals')"], shown[0]),
            ([sys.executable, "-c", "print('deterministic level 2')"], shown[1])])
        job = c.post("/api/jobs/evals", headers=UI, json={}).json()
        assert job["key"] == "evals" and job["commands"] == shown
        done = _wait(c, job)
        assert done["status"] == "succeeded" and "deterministic level 2" in done["log"]

    def test_analyze_months_only_fills_missing_month_files(self, root, c):
        before = (root / "output" / "clients" / "multi_co" / "2025-06" / "metrics.json") \
            .read_bytes()
        job = c.post("/api/jobs/analyze-months", headers=UI,
                     json={"client": "multi_co"}).json()
        assert len(job["commands"]) == len(MONTHS) - 1          # 2025-06 already exists
        assert _wait(c, job)["status"] == "succeeded"
        after = (root / "output" / "clients" / "multi_co" / "2025-06" / "metrics.json")
        assert after.read_bytes() == before                      # never rewritten
        net = next(s for s in c.get("/api/charts?client=multi_co").json()["series"]
                   if s["measure"] == "net_income.reported")
        assert all(p["value"] is not None for p in net["points"] if p["period"] != "2025-04")


# ---- review and onboarding ------------------------------------------------------------------

class TestReviewRoutes:
    W = {"client": "multi_co", "period": "2025-06"}

    def test_the_flow(self, c, root):
        p = lambda action, **kw: c.post(f"/api/review/{action}", headers=UI,  # noqa: E731
                                        json={**self.W, **kw})
        status = lambda: c.get("/api/review/status?client=multi_co&period=2025-06").json()  # noqa: E731
        st = status()                                     # the fixture generated it
        assert st["state"] == "ready_for_review" and st["label"] == "ready for review"
        assert st["log"][0]["name"] == "system" and st["log"][0]["action_label"] == "Submitted"
        assert st["submitted_by_person"] is None and st["writer_follows_comments"] is False
        assert st["require_different_reviewer"] is False    # the repo's config/review.yaml
        queue = c.get("/api/review/queue").json()
        assert queue["count"] == len(queue["ready_for_review"]) == 3     # month, year, all
        for gone in ("submit", "approve"):                # no human submit, no approve
            assert p(gone, by="A. Writer").status_code in (404, 405)
        assert p("send-back", by="B. Partner").status_code == 400  # no comment
        assert p("send-back", by="B. Partner", comment="Rent line?").status_code == 200
        assert status()["state"] == "changes_requested"
        assert p("mark-reviewed", by="B. Partner").status_code == 400
        job = c.post("/api/jobs/report", headers=UI,               # regenerate with it
                     json={**self.W, "keep_data_local": True}).json()
        assert _wait(c, job)["status"] == "succeeded"
        st = status()
        assert st["state"] == "ready_for_review" and st["last_entry"]["automatic"] is True
        assert p("mark-reviewed", by="").status_code == 400         # a name is required
        assert p("mark-reviewed", by="A. Writer").status_code == 200   # system submitted it
        st = status()
        assert st["state"] == "reviewed" and len(st["log"]) == 4
        assert [e["action_label"] for e in st["log"]] == [
            "Submitted", "Sent back", "Submitted", "Marked as reviewed"]
        assert c.get("/api/review/queue").json()["count"] == 2
        v = c.get(f"/api/review/version?client=multi_co&period=2025-06"
                  f"&name={st['versions'][0]}").json()
        assert v["text"].startswith("# Multi Co")
        assert c.get("/api/review/version?client=multi_co&period=2025-06&name=../x.md") \
            .status_code == 400
        notice = c.get("/api/cloud-notice?client=multi_co&period=2025-06"
                       "&keep_data_local=true").json()
        assert notice["leaves_machine"] is False and "Reviewer notes are not sent" in \
            notice["text"]


class TestOnboardingRoutes:
    def _book(self, tmp_path) -> bytes:
        sys.path.insert(0, str(ROOT / "scripts"))
        import make_test_workbooks
        path = tmp_path / "up.xlsx"
        make_test_workbooks.build(str(path), False, "Upload Co (SYNTHETIC)")
        return path.read_bytes()

    def test_upload_validate_approve_report(self, root, c, tmp_path):
        r = c.post("/api/onboarding/draft", headers=UI,
                   data={"client_id": "upload_co", "name": "Upload Co"},
                   files={"file": ("../../evil name.xlsx", self._book(tmp_path))})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["summary"]["checks"] == "65/65 passed"
        assert (root / "data" / "clients" / "upload_co" / "source" / "evil name.xlsx").exists()
        bad = d["yaml"].replace("date_order: newest_first", "date_order: sideways")
        v = c.post("/api/onboarding/upload_co/validate", headers=UI, json={"yaml": bad}).json()
        assert not v["ok"] and v["errors"][0]["line"]
        assert c.post("/api/onboarding/upload_co/approve", headers=UI,
                      json={"by": "A. Reviewer"}).status_code == 400   # unchanged failure
        v = c.post("/api/onboarding/upload_co/validate", headers=UI,
                   json={"yaml": d["yaml"]}).json()
        assert v["ok"]
        assert c.post("/api/onboarding/upload_co/approve", headers=UI,
                      json={"by": ""}).status_code == 400
        rec = c.post("/api/onboarding/upload_co/approve", headers=UI,
                     json={"by": "A. Reviewer"}).json()
        assert rec["approved_by"] == "A. Reviewer"
        job = c.post("/api/jobs/report", headers=UI, json={
            "client": "upload_co", "period": "2026-01", "keep_data_local": True}).json()
        assert _wait(c, job)["status"] == "succeeded"

    @pytest.mark.parametrize("name, body, field", [
        ("notes.txt", b"hello", "client_id"),
        ("book.xlsx", b"not a zip", "client_id"),
    ])
    def test_wrong_files_are_refused(self, c, name, body, field):
        r = c.post("/api/onboarding/draft", headers=UI,
                   data={"client_id": "upload_co", "name": "U"}, files={"file": (name, body)})
        assert r.status_code == 400

    def test_a_bad_client_id_is_refused(self, c, tmp_path):
        r = c.post("/api/onboarding/draft", headers=UI,
                   data={"client_id": "../evil", "name": "U"},
                   files={"file": ("b.xlsx", self._book(tmp_path))})
        assert r.status_code == 400

    def test_an_oversized_upload_is_refused(self, c):
        r = c.post("/api/onboarding/draft", headers=UI,
                   data={"client_id": "upload_co", "name": "U"},
                   files={"file": ("b.xlsx", b"PK" + b"\0" * (26 * 1024 * 1024))})
        assert r.status_code in (400, 413)


# ---- secrets ---------------------------------------------------------------------------

class TestNoSecrets:
    def test_no_response_holds_a_secret(self, tmp_path, monkeypatch, capsys):
        root = _qb_root(tmp_path, monkeypatch)            # fake QBO_* values in the env
        for name in ("providers.yaml", "review.yaml"):
            shutil.copy(ROOT / "config" / name, root / "config" / name)
        (root / ".env").write_text("".join(f"{k}={v}\n" for k, v in FAKE_ENV.items()),
                                   encoding="utf-8")
        c = _client(create_app(root, qb_transport=FakeIntuit()))
        assert c.post("/api/jobs/quickbooks-pull", headers=UI, json={
            "client": "qb_sandbox", "start": "2025-10", "end": "2026-10"}).status_code == 400
        job = c.post("/api/jobs/quickbooks-pull", headers=UI, json={
            "client": "qb_sandbox", "start": "2025-10", "end": "2026-10",
            "confirm": True}).json()
        done = _wait(c, job)
        assert done["status"] == "succeeded", done
        assert any(f["filename"] == done["result"]["filename"]
                   for f in c.get("/api/clients/qb_sandbox/files").json()["files"])
        texts = [json.dumps(done)]
        for url in ["/api/clients", "/api/clients/qb_sandbox/files", "/api/providers",
                    "/api/quickbooks/status", "/api/jobs", "/api/evals",
                    "/api/cloud-notice?keep_data_local=false", "/api/review/queue",
                    "/.env", "/static/../.env"]:
            texts.append(c.get(url).text)
        failing = _client(create_app(root, qb_transport=FakeIntuit(token_status=400,
                                                                   echo=True)))
        bad = _wait(failing, failing.post("/api/jobs/quickbooks-pull", headers=UI, json={
            "client": "qb_sandbox", "start": "2025-10", "end": "2026-10",
            "confirm": True}).json())
        assert bad["status"] == "failed"
        texts.append(json.dumps(bad))
        blob = "\n".join(texts)
        for value in [*SECRETS, *FAKE_ENV.values()]:
            assert value not in blob
        status = c.get("/api/providers").json()
        gem = next(p for p in status["providers"] if p["provider"] == "gemini")
        assert gem["missing_variables"] == []                      # names only, yes/no


# ---- escaping ------------------------------------------------------------------------------

class TestEscaping:
    def test_hostile_workbook_text_comes_back_as_json_text(self, c):
        r = c.get("/api/report?client=multi_co&period=2025-06")
        assert r.headers["content-type"].startswith("application/json")
        assert r.headers["x-content-type-options"] == "nosniff"
        doc = r.json()
        labels = [m.get("label") for m in doc["metrics_doc"]["metrics"]]
        assert any(HOSTILE_LABEL in str(l) for l in labels)
        assert any(w["raw_value"] == HOSTILE_CELL for w in
                   c.get("/api/report?client=multi_co&scope=all").json()["cell_warnings"])

    def test_no_js_parses_html(self):
        banned = re.compile(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|"
                            r"\beval\(|new Function|createContextualFragment|DOMParser")
        for path in _js_files():
            for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
                assert not banned.search(line.split("//")[0]), f"{path.name}:{n}"

    def test_the_renderer_keeps_html_as_text(self, tmp_path):
        node = shutil.which("node")
        if not node:
            pytest.skip("node is not installed")
        md = ("# Report\n\nRevenue was $1,200.00 and " + HOSTILE_LABEL + " moved.  \n"
              "Second line with $9.99.\n\n| a | b |\n| --- | --- |\n| <b>x</b> | 1 |\n\n"
              "- [ ] check " + HOSTILE_CELL + "\n")
        links = [{"line": 3, "start": 12, "end": 21, "text": "$1,200.00", "metric": "m.one"},
                 {"line": 4, "start": 17, "end": 22, "text": "$9.99", "metric": "m.two"},
                 {"line": 4, "start": 0, "end": 6, "text": "WRONG!", "metric": "m.bad"}]
        harness = tmp_path / "harness.mjs"
        harness.write_text(_HARNESS.replace("__MD__", json.dumps(md))
                           .replace("__LINKS__", json.dumps(links))
                           .replace("__MODULE__", (UI_DIR / "static" / "js" / "markdown.js")
                                    .as_uri()), encoding="utf-8")
        out = subprocess.run([node, str(harness)], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        assert out.returncode == 0, out.stderr
        res = json.loads(out.stdout)
        assert res["tags"].count("SCRIPT") == 0 and res["tags"].count("IMG") == 0
        assert res["tags"].count("B") == 0                       # "<b>" stayed text
        assert HOSTILE_LABEL in res["text"] and HOSTILE_CELL in res["text"]
        assert res["links"] == [["$1,200.00", "m.one"], ["$9.99", "m.two"]]


_HARNESS = r"""
class N { constructor() { this.childNodes = []; }
  append(...xs) { for (const x of xs) this.childNodes.push(x instanceof N ? x : new T(String(x))); }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v) { this.childNodes = [new T(String(v))]; }
  replaceChildren() { this.childNodes = []; } }
class T extends N { constructor(t) { super(); this.data = t; } get textContent() { return this.data; } }
class E extends N { constructor(tag) { super(); this.tagName = tag.toUpperCase(); this.attrs = {}; this.className = ""; }
  setAttribute(k, v) { this.attrs[k] = String(v); } addEventListener() {}
  set innerHTML(v) { throw new Error("innerHTML used"); } }
globalThis.Node = N;
globalThis.document = { createElement: (t) => new E(t), createTextNode: (t) => new T(t) };
const { renderMarkdown } = await import("__MODULE__");
const root = renderMarkdown(__MD__, __LINKS__, () => {});
const tags = []; const links = [];
(function walk(n) { if (n instanceof E) { tags.push(n.tagName);
  if (n.tagName === "BUTTON") links.push([n.textContent, n.attrs.title.replace("Source cells: ", "")]); }
  n.childNodes.forEach(walk); })(root);
console.log(JSON.stringify({ tags, links, text: root.textContent }));
"""


# ---- charts: read, gaps, markers ----------------------------------------------------------

class TestCharts:
    def test_gaps_failures_and_values_come_from_the_files(self, root, c):
        data = c.get("/api/charts?client=multi_co").json()
        doc = json.loads((root / "output" / "clients" / "multi_co" / "all" / "metrics.json")
                         .read_text("utf-8"))
        assert "2025-04" in data["periods"] and data["gaps"] == ["2025-04"]
        rev = next(s for s in data["series"] if s["measure"] == "revenue.total")
        gap = next(p for p in rev["points"] if p["period"] == "2025-04")
        assert gap["value"] is None and gap["status"].startswith("gap")
        src = next(m for m in doc["series"]["measures"] if m["measure"] == "revenue.total")
        assert [p["value"] for p in rev["points"] if p["period"] != "2025-04"] == \
            [p["value"] for p in src["points"]]                  # copied, never changed
        failed = {f["period"] for f in doc["reconciliation"]["failures"]}
        assert failed and set(data["reconciliation_failed"]) == failed
        assert all(p["reconciliation_failed"] == (p["period"] in failed) for p in rev["points"])
        net = next(s for s in data["series"] if s["measure"] == "net_income.reported")
        june = next(p for p in net["points"] if p["period"] == "2025-06")
        assert june["status"] == "ok" and june["display"].startswith("$")
        assert "not analysed" in next(p for p in net["points"]
                                      if p["period"] == "2025-05")["status"]

    def test_a_year_is_labelled_and_never_annualised(self, root, c):
        data = c.get("/api/charts?client=multi_co&year=2025").json()
        assert data["partial_year"] and data["year_status"] == "partial"
        assert data["label"].startswith("2025: ") and data["never_annualised"]

    def test_a_month_report_from_another_source_is_not_used(self, root, c):
        month = root / "output" / "clients" / "multi_co" / "2025-06" / "metrics.json"
        doc = json.loads(month.read_text("utf-8"))
        doc["source"]["sha256"] = "0" * 64
        month.write_text(json.dumps(doc), encoding="utf-8")
        net = next(s for s in c.get("/api/charts?client=multi_co").json()["series"]
                   if s["measure"] == "net_income.reported")
        june = next(p for p in net["points"] if p["period"] == "2025-06")
        assert june["value"] is None and "different source" in june["status"]


# ---- the UI does no calculation (best effort) ------------------------------------------------

def _js_files():
    return [p for p in (UI_DIR / "static").rglob("*.js") if "vendor" not in p.parts]


METRIC_FIELDS = r"(?:value|computed|reported|difference|original_value|reported_value|raw_value)"


class TestNoCalculationInTheUi:
    """Best effort: a static scan, not a proof. It catches the usual ways arithmetic on a
    figure gets written; reviewers still read the diff."""

    PATTERNS = [
        (re.compile(r"\.reduce\("), "reduce("),
        (re.compile(r"Math\.(round|floor|ceil|trunc)\("), "Math rounding"),
        (re.compile(r"\*\s*100\b"), "* 100"),
        (re.compile(r"/\s*100\b"), "/ 100"),
        (re.compile(r"\.toFixed\(|\.toPrecision\("), "toFixed / toPrecision"),
        (re.compile(r"parseFloat\("), "parseFloat("),
        (re.compile(rf"\.{METRIC_FIELDS}\b\s*[-+*/%]"), "arithmetic after a metric field"),
        (re.compile(rf"[-+*/%]\s*[\w.\]\)]*\.{METRIC_FIELDS}\b"),
         "arithmetic before a metric field"),
        (re.compile(r"Math\.(max|min|abs)\("), "Math max/min/abs"),
    ]

    def test_scan(self):
        found = []
        for path in _js_files():
            for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
                code = line.split("//")[0]
                for pattern, what in self.PATTERNS:
                    if pattern.search(code):
                        found.append(f"{path.name}:{n}: {what}: {line.strip()}")
        assert not found, "\n".join(found)

    def test_the_scan_catches_what_it_should(self):
        bad = ["const t = rows.reduce((a, r) => a + r.value, 0);", "x = p.value * 100;",
               "Math.round(m.value)", "share = m.value / total.value", "v.toFixed(1)",
               "d = f.computed - f.reported"]
        for line in bad:
            assert any(p.search(line) for p, _ in self.PATTERNS), line


# ---- vendored Chart.js, no CDN -------------------------------------------------------------

class TestVendor:
    def test_chartjs_hash_version_and_licence(self):
        vendor = UI_DIR / "static" / "vendor"
        text = (vendor / "VERSION.md").read_text("utf-8")
        digest = hashlib.sha256((vendor / "chart.umd.min.js").read_bytes()).hexdigest()
        assert digest in text and "4.5.1" in text
        assert (vendor / "chart.umd.min.js").read_text("utf-8").startswith(
            "/*!\n * Chart.js v4.5.1")
        assert "MIT License" in (vendor / "CHARTJS_LICENSE.md").read_text("utf-8")

    def test_no_cdn(self):
        for path in [UI_DIR / "index.html", *(UI_DIR / "static").rglob("*.css"),
                     *_js_files()]:
            assert not re.search(r"https?://(?!127\.0\.0\.1)", path.read_text("utf-8")), path
