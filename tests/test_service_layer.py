"""The service layer (Phase 5D, part 1): the same values as the files, safe uploads,
background jobs, terminal parity, and no secret in any result.

No network: QuickBooks uses the fake Intuit server from test_quickbooks.py. No model is
called. Workbooks are generated test books.
"""

from __future__ import annotations

import io
import json
import re
import shlex
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from finance_engine.brief import build_brief
from finance_engine.cli import EXIT_OK, EXIT_USAGE, build_parser, main
from finance_engine.formatting import check_count
from finance_engine.onboarding import OnboardingError
from finance_engine.service import commands, core, parity, uploads
from finance_engine.service.commands import CommandError
from finance_engine.service.jobs import (FAILED, SUCCEEDED, TIMEOUT, JobConflict,
                                         JobManager, MAX_LOG_LINES)
from finance_engine.service.uploads import UploadError
from tests.helpers import build_monthly_workbook, write_monthly_config
from tests.test_quickbooks import FIXTURE, SECRETS, FakeIntuit, _qb_root

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
TS = [re.compile(r'"generated_at_utc": "[^"]*"'), re.compile(r"\*\*Generated:\*\* [^\n]*")]


def _masked(path: Path) -> str:
    text = path.read_text("utf-8")
    for pattern in TS:
        text = pattern.sub("<TS>", text)
    return text


@pytest.fixture
def root(tmp_path):
    """multi_co with two workbooks (first and second half of 2025), never merged."""
    write_monthly_config(tmp_path / "config" / "clients")
    src = tmp_path / "data" / "clients" / "multi_co" / "source"
    src.mkdir(parents=True)
    build_monthly_workbook(src / "h1.xlsx", [f"2025-{m:02d}" for m in range(1, 7)])
    build_monthly_workbook(src / "h2.xlsx", [f"2025-{m:02d}" for m in range(6, 13)])
    return tmp_path


def _xlsx_bytes(tmp_path) -> bytes:
    path = tmp_path / "made.xlsx"
    build_monthly_workbook(path, ["2025-01", "2025-02", "2025-03"])
    return path.read_bytes()


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


# ---- the same values as the files ------------------------------------------------------

class TestSameValuesAsTheFiles:
    @pytest.mark.parametrize("scope, where", [
        ("month", dict(period="2025-06", file="h2.xlsx")),
        ("year", dict(year=2025, file="h1.xlsx")),
        ("all", dict(file="h2.xlsx")),
    ])
    def test_get_report(self, root, capsys, scope, where):
        argv = {"month": ["--period", "2025-06"], "year": ["--scope", "year", "--year",
                                                           "2025"],
                "all": ["--scope", "all"]}[scope]
        assert main(["report", "--client", "multi_co", *argv, "--file", where["file"],
                     "--provider", "deterministic", "--root", str(root)]) in (0, 2)
        capsys.readouterr()
        got = core.get_report(root, "multi_co", scope, **where)
        folder = root / got["folder"]
        doc = json.loads((folder / "metrics.json").read_text("utf-8"))
        report_md = (folder / "report.md").read_text("utf-8")
        assert folder.parent.name == Path(where["file"]).stem
        assert got["metrics_doc"] == doc
        assert got["report_md"] == report_md
        assert got["brief"] == build_brief(doc)
        assert got["period_label"] == doc["period_label"]
        assert got["badges"]["source_sha256"] == doc["source"]["sha256"]
        recon = got["badges"]["reconciliation"]
        assert recon["checks_run"] == doc["reconciliation"]["checks_run"]
        assert recon["label"] == check_count(doc["reconciliation"])
        nc = got["badges"]["number_check"]
        assert f"| Commentary number check | {nc['result']} | {nc['detail']} |" in report_md
        assert got["badges"]["provider"]["provider"] == "deterministic"
        assert got["badges"]["provider"]["data_left_machine"] is False
        assert got["badges"]["review"] == "draft"

    def test_show_report_json_is_the_service_result(self, root, capsys):
        main(["report", "--client", "multi_co", "--period", "2025-06", "--file", "h2.xlsx",
              "--provider", "deterministic", "--root", str(root)])
        capsys.readouterr()
        assert main(["show-report", "--client", "multi_co", "--period", "2025-06",
                     "--file", "h2.xlsx", "--json", "--root", str(root)]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        direct = core.get_report(root, "multi_co", "month", "2025-06", file="h2.xlsx")
        assert {k: payload[k] for k in direct} == direct

    def test_a_missing_report_names_the_command(self, root):
        with pytest.raises(core.ServiceError, match="python -m finance_engine report "
                                                    "--client multi_co --period 2025-03"):
            core.get_report(root, "multi_co", "month", "2025-03")

    def test_cell_warnings_come_from_the_issues(self):
        doc = {"data_quality_issues": [{"code": "non_numeric_cell", "severity": "warning",
                                        "message": "m", "affects": ["x"],
                                        "cells": [{"sheet": "IS", "cell": "F20",
                                                   "label": "Marketing",
                                                   "period": "2025-10",
                                                   "raw_value": "n/a"}]},
                                       {"code": "no_balance_sheet", "severity": "warning",
                                        "message": "m"}]}
        assert core.cell_warnings(doc) == [{
            "code": "non_numeric_cell", "severity": "warning", "sheet": "IS",
            "cell": "F20", "label": "Marketing", "period": "2025-10", "raw_value": "n/a",
            "message": "m", "affects": ["x"]}]

    def test_files_and_periods_match_list_periods(self, root, capsys):
        for file in ("h1.xlsx", "h2.xlsx"):
            assert main(["list-periods", "--client", "multi_co", "--file", file, "--json",
                         "--root", str(root)]) == EXIT_OK
            cli = json.loads(capsys.readouterr().out)
            svc = core.list_periods(root, "multi_co", file)
            assert svc["periods"] == cli["periods"] and svc["file"] == cli["selected"]
            listed = {f["filename"]: f for f in core.list_files(root, "multi_co")["files"]}
            for f in cli["files"]:
                assert {k: listed[f["filename"]][k] for k in f if k != "selected"} == \
                    {k: v for k, v in f.items() if k != "selected"}
        with pytest.raises(core.ServiceError, match="(?i)choose one with --file"):
            core.list_periods(root, "multi_co")             # two files, no default

    def test_list_clients_shows_live_configs_and_drafts(self, root, tmp_path):
        clients = core.list_clients(root)
        assert [c["client_id"] for c in clients["clients"]] == ["multi_co"]
        assert clients["drafts"] == []


# ---- the job runs the same command the terminal does ----------------------------------

class TestReportJobs:
    def _jobs(self, root, out):
        import os
        env = dict(os.environ, FINANCE_OUTPUT_DIR=str(out), LLM_PROVIDER="deterministic")
        return JobManager(root, env=env)

    @pytest.mark.parametrize("scope, where, cli_args", [
        ("month", dict(period="2025-06", file="h2.xlsx"),
         ["--period", "2025-06", "--file", "h2.xlsx"]),
        ("year", dict(year=2025, file="h1.xlsx"),
         ["--scope", "year", "--year", "2025", "--file", "h1.xlsx"]),
    ])
    def test_a_report_job_writes_what_the_cli_writes(self, root, tmp_path, monkeypatch,
                                                     capsys, scope, where, cli_args):
        jobs = self._jobs(root, tmp_path / "out_job")
        job = core.start_report(jobs, root, "multi_co", scope, **where,
                                keep_data_local=True)
        jobs.wait(job.id, 120)
        assert job.status == SUCCEEDED, job.to_dict()
        assert job.result["provider"] == "deterministic"
        assert job.display[0].startswith("python -m finance_engine report --client "
                                         "multi_co")
        assert "--provider deterministic" in job.display[0]
        monkeypatch.setenv("FINANCE_OUTPUT_DIR", str(tmp_path / "out_cli"))
        assert main(["report", "--client", "multi_co", *cli_args, "--provider",
                     "deterministic", "--root", str(root)]) in (0, 2)
        capsys.readouterr()
        a = sorted(p.relative_to(tmp_path / "out_job")
                   for p in (tmp_path / "out_job").rglob("*.*"))
        b = sorted(p.relative_to(tmp_path / "out_cli")
                   for p in (tmp_path / "out_cli").rglob("*.*"))
        assert a == b and a
        for rel in a:
            assert _masked(tmp_path / "out_job" / rel) == _masked(tmp_path / "out_cli" / rel)

    def test_keep_data_local_is_exactly_provider_deterministic(self):
        argv, shown = commands.report_command("multi_co", "all", keep_data_local=True)
        assert "--provider=deterministic" in argv and "--provider deterministic" in shown
        argv, shown = commands.report_command("multi_co", "all", keep_data_local=False)
        assert "--provider=auto" in argv

    def test_cloud_notice_says_only_the_brief_leaves(self, root):
        assert core.cloud_notice(root, True)["leaves_machine"] is False
        notice = core.cloud_notice(ROOT, False)
        assert notice["provider_flag"] == "--provider auto"
        assert "never leave this machine" in notice["text"]


# ---- jobs ---------------------------------------------------------------------------------

def _py(code: str) -> list[str]:
    return [PY, "-c", code]


class TestJobs:
    def test_success_parses_the_json_result(self, tmp_path):
        jobs = JobManager(tmp_path)
        job = jobs.start_commands("c1", "t", [_py("import json; print('note'); "
                                                  "print(json.dumps({'ok': True}))")])
        jobs.wait(job.id, 60)
        assert job.status == SUCCEEDED and job.result == {"ok": True}
        assert "note" in job.to_dict()["log"]

    @pytest.mark.parametrize("code, status", [(0, SUCCEEDED), (2, SUCCEEDED),
                                              (1, FAILED), (3, FAILED)])
    def test_exit_codes(self, tmp_path, code, status):
        jobs = JobManager(tmp_path)
        job = jobs.start_commands("c1", "t", [_py(f"raise SystemExit({code})")])
        jobs.wait(job.id, 60)
        assert job.status == status and job.exit_code == code

    def test_one_running_job_per_client(self, tmp_path):
        jobs = JobManager(tmp_path)
        first = jobs.start_commands("c1", "t", [_py("import time; time.sleep(30)")],
                                    timeout=5)
        with pytest.raises(JobConflict, match="already running"):
            jobs.start_commands("c1", "t", [_py("pass")])
        other = jobs.start_commands("c2", "t", [_py("pass")])
        jobs.wait(other.id, 60)
        assert other.status == SUCCEEDED
        jobs.wait(first.id, 60)
        again = jobs.start_commands("c1", "t", [_py("pass")])   # free once finished
        jobs.wait(again.id, 60)
        assert again.status == SUCCEEDED

    def test_timeout_stops_the_command(self, tmp_path):
        jobs = JobManager(tmp_path)
        marker = tmp_path / "late.txt"
        started = time.monotonic()
        job = jobs.start_commands("c1", "t", [_py(
            f"import time; time.sleep(8); open({str(marker)!r}, 'w').write('x')")],
            timeout=1)
        jobs.wait(job.id, 60)
        assert job.status == TIMEOUT and time.monotonic() - started < 8
        time.sleep(8.5)
        assert not marker.exists()                         # the process was killed

    def test_the_log_is_capped(self, tmp_path):
        jobs = JobManager(tmp_path)
        job = jobs.start_commands("c1", "t", [_py(
            "for i in range(1000): print('line', i, 'x' * 3000)")])
        jobs.wait(job.id, 60)
        d = job.to_dict()
        assert len(d["log"]) == MAX_LOG_LINES and d["log_dropped_lines"] > 500
        assert all(len(line) < 1100 for line in d["log"])

    def test_secrets_are_redacted_from_logs_and_results(self, tmp_path):
        secret = "AIzaSyFAKEFAKE0123456789abcdefghijk"
        jobs = JobManager(tmp_path, redact=lambda s: s.replace(secret, "[REDACTED]"))
        job = jobs.start_commands("c1", "t", [_py(
            f"import json, sys; print('key', {secret!r}, file=sys.stderr); "
            f"print(json.dumps({{'echo': {secret!r}}}))")])
        jobs.wait(job.id, 60)
        assert secret not in json.dumps(job.to_dict())

    def test_callable_jobs(self, tmp_path):
        jobs = JobManager(tmp_path)
        ok = jobs.start_callable("c1", "t", lambda log: (log("hi"), {"n": 1})[1], ["x"])
        jobs.wait(ok.id, 10)
        assert ok.status == SUCCEEDED and ok.result == {"n": 1}
        bad = jobs.start_callable("c2", "t", lambda log: 1 / 0, ["x"])
        jobs.wait(bad.id, 10)
        assert bad.status == FAILED
        slow = jobs.start_callable("c3", "t", lambda log: time.sleep(5), ["x"], timeout=0.5)
        jobs.wait(slow.id, 10)
        assert slow.status == TIMEOUT


# ---- uploads ----------------------------------------------------------------------------

class TestUploads:
    @pytest.mark.parametrize("name, safe", [
        ("book.xlsx", "book.xlsx"),
        ("../../evil.xlsx", "evil.xlsx"),
        ("..\\..\\windows\\evil.xlsm", "evil.xlsm"),
        ("C:evil.xlsx", "evil.xlsx"),
        (".hidden.xlsx", "hidden.xlsx"),
        ("a<script>b.xlsx", "a_script_b.xlsx"),
        ("ABC Inc - FY 2026.xlsx", "ABC Inc - FY 2026.xlsx"),
    ])
    def test_names_are_reduced_to_a_safe_base_name(self, name, safe):
        assert uploads.sanitise_filename(name) == safe

    @pytest.mark.parametrize("name", ["", "..", "book.xls", "book.csv", "book.xlsx.exe",
                                      "book", ".xlsx", "/", "<>.xlsx"])
    def test_wrong_names_are_refused(self, name):
        with pytest.raises(UploadError):
            uploads.sanitise_filename(name)

    def test_a_workbook_is_saved_only_in_the_clients_source_folder(self, tmp_path):
        data = _xlsx_bytes(tmp_path)
        path = uploads.save_upload(tmp_path / "data", "acme_ltd", "../../x.xlsx", data)
        assert path == (tmp_path / "data" / "acme_ltd" / "source" / "x.xlsx").resolve()
        assert path.read_bytes() == data
        assert uploads.save_upload(tmp_path / "data", "acme_ltd", "x.xlsx", data) == path

    @pytest.mark.parametrize("client", ["../acme", "ACME", "a", "acme/ltd", "x" * 41])
    def test_a_bad_client_id_is_refused(self, tmp_path, client):
        with pytest.raises(OnboardingError):
            uploads.save_upload(tmp_path / "data", client, "x.xlsx", _xlsx_bytes(tmp_path))
        assert not (tmp_path / "data").exists()

    def test_wrong_content_is_refused(self, tmp_path):
        cases = {
            "not a zip": b"<script>alert(1)</script>",
            "a zip without a workbook": _zip({"hello.txt": b"hi"}),
            "empty": b"",
        }
        for label, data in cases.items():
            with pytest.raises(UploadError):
                uploads.save_upload(tmp_path / "data", "acme_ltd", "x.xlsx", data)
        assert not (tmp_path / "data").exists()

    def test_an_oversized_file_is_refused(self, tmp_path):
        data = b"PK" + b"\0" * (uploads.MAX_UPLOAD_BYTES - 1)
        with pytest.raises(UploadError, match="25 MB"):
            uploads.save_upload(tmp_path / "data", "acme_ltd", "x.xlsx", data)

    def test_a_zip_bomb_is_refused(self, tmp_path):
        bomb = _zip({"[Content_Types].xml": b"x", "xl/workbook.xml": b"x",
                     "xl/worksheets/sheet1.xml": b"\0" * (8 * 1024 * 1024)})
        assert len(bomb) < 100_000
        with pytest.raises(UploadError, match="zip bomb"):
            uploads.save_upload(tmp_path / "data", "acme_ltd", "x.xlsx", bomb)

    def test_the_uncompressed_total_is_capped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(uploads, "MAX_UNCOMPRESSED_BYTES", 1000)
        with pytest.raises(UploadError, match="expands to"):
            uploads.save_upload(tmp_path / "data", "acme_ltd", "x.xlsx",
                                _xlsx_bytes(tmp_path))

    def test_an_existing_different_file_is_never_overwritten(self, tmp_path):
        data = _xlsx_bytes(tmp_path)
        path = uploads.save_upload(tmp_path / "data", "acme_ltd", "x.xlsx", data)
        other = _zip({"[Content_Types].xml": b"y", "xl/workbook.xml": b"y"})
        with pytest.raises(UploadError, match="never overwritten"):
            uploads.save_upload(tmp_path / "data", "acme_ltd", "x.xlsx", other)
        assert path.read_bytes() == data

    def test_an_upload_then_draft(self, tmp_path):
        sys.path.insert(0, str(ROOT / "scripts"))
        import make_test_workbooks
        made = tmp_path / "made.xlsx"
        make_test_workbooks.build(str(made), False, "Upload Co (SYNTHETIC)")
        root = tmp_path / "root"
        (root / "config" / "clients").mkdir(parents=True)
        saved = uploads.save_upload(root / "data" / "clients", "upload_co",
                                    "C:\\Users\\me\\book.xlsx", made.read_bytes())
        summary = core.draft_client(root, saved, "upload_co", "Upload Co")
        assert summary["status"] == "valid" and summary["checks"] == "65/65 passed"
        assert summary["workbook"] == "data/clients/upload_co/source/book.xlsx"
        drafts = core.list_clients(root)["drafts"]
        assert [d["client_id"] for d in drafts] == ["upload_co"]
        assert core.draft_config_text(root, "upload_co")["yaml"].startswith("# DRAFT")


# ---- commands, parity, loopback, secrets --------------------------------------------------

class TestCommandValues:
    @pytest.mark.parametrize("name", ["../x.xlsx", "-x.xlsx", "x.txt", "a/b.xlsx",
                                      "--root=x.xlsx", ".x.xlsx"])
    def test_file_names(self, name):
        with pytest.raises(CommandError):
            commands.check_file_name(name)

    @pytest.mark.parametrize("args", [("month", "2025-13", None), ("month", "June", None),
                                      ("year", None, "20x5"), ("week", None, None)])
    def test_scope_values(self, args):
        with pytest.raises(CommandError):
            commands.scope_pairs(*args)

    def test_values_cannot_become_options(self):
        argv, _ = commands.report_command("multi_co", "month", "2025-06",
                                          file="a b.xlsx")
        assert "--file=a b.xlsx" in argv and "--period=2025-06" in argv


class TestParity:
    def _parse(self, cli: str):
        parser = build_parser()
        filled = cli.format(**parity.SAMPLES)
        for part in filled.split("&&"):
            words = shlex.split(part)
            if words[:3] == ["python", "-m", "finance_engine"]:
                parser.parse_args(words[3:])          # SystemExit if it does not parse
            else:
                assert words[0] == "python" and (ROOT / words[1]).exists(), part

    @pytest.mark.parametrize("route", parity.ROUTES, ids=lambda r: f"{r['method']} {r['path']}")
    def test_every_route_has_a_cli_command_that_parses(self, route):
        assert route["cli"] and route["kind"] in ("read", "action", "support")
        self._parse(route["cli"])

    def test_routes_are_unique(self):
        keys = [(r["method"], r["path"]) for r in parity.ROUTES]
        assert len(keys) == len(set(keys))

    def test_no_route_spends_money(self):
        for r in parity.ROUTES:
            if "run_skill_evals.py" in r["cli"]:
                assert "--provider deterministic" in r["cli"]
            assert "provider_smoke" not in r["cli"] and "qb_smoke" not in r["cli"]
            assert "claude -p" not in r["cli"]
        assert all("--provider deterministic" in c
                   for c in core.list_evals(ROOT)["free_commands"] if "skill" in c)

    def test_every_new_command_is_reachable_from_a_route(self):
        reachable = {shlex.split(r["cli"].format(**parity.SAMPLES))[3]
                     for r in parity.ROUTES if r["cli"].startswith("python -m")}
        for command in ("list-clients", "list-files", "list-periods", "show-report",
                        "report", "analyze", "init-client", "validate-config",
                        "approve-config", "review", "list-evals", "provider-status",
                        "quickbooks-status", "pull-quickbooks", "brief"):
            assert command in reachable, command


class TestLoopbackOnly:
    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::", "example.com", ""])
    def test_serve_refuses_any_other_host(self, host, capsys):
        assert main(["serve", "--host", host]) == EXIT_USAGE
        assert "127.0.0.1 only" in capsys.readouterr().err

    def test_serve_without_the_web_packages_says_how_to_install(self, monkeypatch,
                                                                capsys):
        monkeypatch.setitem(sys.modules, "finance_engine.web", None)
        assert main(["serve"]) == EXIT_USAGE
        assert "requirements-optional.txt" in capsys.readouterr().err

    def test_the_engine_imports_without_the_web_packages(self):
        code = ("import sys, finance_engine.cli, finance_engine.service.core, "
                "finance_engine.service.jobs, finance_engine.service.uploads, "
                "finance_engine.service.parity, finance_engine.review, "
                "finance_engine.onboarding; "
                "bad = [m for m in ('fastapi', 'uvicorn', 'multipart', 'starlette') "
                "if m in sys.modules]; print(bad); raise SystemExit(1 if bad else 0)")
        assert subprocess.run([PY, "-c", code], cwd=ROOT).returncode == 0


class TestNoSecrets:
    FAKE = {"GEMINI_API_KEY": "AIzaSyFAKE-gemini-0123456789abcdef",
            "ANTHROPIC_API_KEY": "sk-ant-api03-FAKE-0123456789abcdef"}

    def _root(self, tmp_path, monkeypatch):
        root = _qb_root(tmp_path, monkeypatch)              # seeds fake QBO_* values
        (root / "config" / "providers.yaml").write_text(
            (ROOT / "config" / "providers.yaml").read_text("utf-8"), encoding="utf-8")
        for name, value in self.FAKE.items():
            monkeypatch.setenv(name, value)
        return root

    def _values(self):
        return [*SECRETS, *self.FAKE.values()]

    def test_status_results_hold_names_never_values(self, tmp_path, monkeypatch, capsys):
        root = self._root(tmp_path, monkeypatch)
        results = [core.provider_status(root), core.quickbooks_status(root),
                   core.cloud_notice(root, False), core.list_clients(root)]
        for command in ("provider-status", "quickbooks-status", "list-clients"):
            assert main([command, "--json", "--root", str(root)]) == EXIT_OK
        captured = capsys.readouterr()
        text = json.dumps(results) + captured.out + captured.err
        for value in self._values():
            assert value not in text
        qb = core.quickbooks_status(root)
        assert qb["configured"] and qb["missing_variables"] == []
        gem = next(p for p in core.provider_status(root)["providers"]
                   if p["provider"] == "gemini")
        assert gem["missing_variables"] == []

    def test_missing_variables_are_listed_by_name(self, tmp_path, monkeypatch):
        root = self._root(tmp_path, monkeypatch)
        for name in ("QBO_CLIENT_SECRET", "QBO_REFRESH_TOKEN", "GEMINI_API_KEY"):
            monkeypatch.delenv(name)
        qb = core.quickbooks_status(root)
        assert not qb["configured"]
        assert qb["missing_variables"] == ["QBO_CLIENT_SECRET", "QBO_REFRESH_TOKEN"]
        gem = next(p for p in core.provider_status(root)["providers"]
                   if p["provider"] == "gemini")
        assert gem["missing_variables"] == ["GEMINI_API_KEY"] and not gem["configured"]

    def test_the_redactor_knows_every_env_value(self, tmp_path, monkeypatch):
        from tests.test_quickbooks import CLIENT_ID, REALM, REFRESH, SECRET
        root = self._root(tmp_path, monkeypatch)
        clean = core.make_redactor(root)
        env_values = [CLIENT_ID, SECRET, REFRESH, REALM, *self.FAKE.values()]
        text = " ".join(env_values) + " data/clients/qb_sandbox/source"
        for value in env_values:
            assert value not in clean(text)
        assert "qb_sandbox" in clean(text)            # QBO_ENVIRONMENT is not a secret

    def test_quickbooks_pull_job_against_the_fake_server(self, tmp_path, monkeypatch):
        root = self._root(tmp_path, monkeypatch)
        jobs = JobManager(root, redact=core.make_redactor(root))
        job = core.start_quickbooks_pull(jobs, root, "qb_sandbox", "2025-10", "2026-10",
                                         transport=FakeIntuit())
        jobs.wait(job.id, 30)
        assert job.status == SUCCEEDED, job.to_dict()
        snapshot = root / job.result["snapshot"]
        assert snapshot.read_bytes() == FIXTURE.read_bytes()
        assert job.display == ["python -m finance_engine pull-quickbooks --client "
                               "qb_sandbox --start 2025-10 --end 2026-10 --json"]
        assert "qb_pl_2025-10_2026-10" in json.dumps(core.list_files(root, "qb_sandbox"))
        failing = core.start_quickbooks_pull(jobs, root, "qb_sandbox", "2025-10",
                                             "2026-10",
                                             transport=FakeIntuit(token_status=400,
                                                                  echo=True))
        jobs.wait(failing.id, 30)
        assert failing.status == FAILED
        text = json.dumps([job.to_dict(), failing.to_dict()])
        for value in self._values():
            assert value not in text

    def test_a_non_quickbooks_client_cannot_pull(self, root):
        with pytest.raises(core.ServiceError, match="not a QuickBooks client"):
            core.start_quickbooks_pull(JobManager(root), root, "multi_co", "2025-01",
                                       "2025-02")


class TestEvalsListing:
    def test_lists_every_golden_case(self):
        data = core.list_evals(ROOT)
        ids = [c["id"] for c in data["cases"]]
        assert len(ids) == 19 and ids[0].startswith("01_") and ids[-1].startswith("19_")
        assert data["paid_command"].startswith("python evals/run_skill_evals.py")
        assert "run it yourself" in data["paid_note"]
