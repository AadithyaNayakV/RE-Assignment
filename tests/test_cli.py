"""CLI behaviour, including the exit codes a pipeline would gate on.

    0  success
    1  usage / config / layout problem — nothing written
    2  completed, but validation or reconciliation failed
    3  the guard rejected the commentary
"""

from __future__ import annotations

import json
import shutil

import pytest

from finance_engine.cli import EXIT_DATA, EXIT_GUARD, EXIT_OK, EXIT_USAGE, main
from tests.helpers import build_small_workbook, write_small_config


@pytest.fixture
def workspace(tmp_path):
    """A self-contained project root with one tiny client."""
    (tmp_path / "config" / "clients").mkdir(parents=True)
    (tmp_path / "data" / "clients" / "tiny_co" / "source").mkdir(parents=True)
    write_small_config(tmp_path / "config" / "clients")
    build_small_workbook(tmp_path / "data" / "clients" / "tiny_co" / "source" / "tiny.xlsx")
    return tmp_path


@pytest.fixture
def abc_workspace(tmp_path, project_root, abc_config, abc_workbook):
    (tmp_path / "config" / "clients").mkdir(parents=True)
    dest = tmp_path / "data" / "clients" / "abc_inc" / "source"
    dest.mkdir(parents=True)
    shutil.copy(project_root / "config" / "clients" / "abc_inc.yaml",
                tmp_path / "config" / "clients" / "abc_inc.yaml")
    shutil.copy(abc_workbook, dest / abc_config.source_filename)
    return tmp_path


class TestAnalyze:
    def test_writes_metrics_json(self, abc_workspace, capsys):
        code = main(["analyze", "--client", "abc_inc", "--period", "2026-06",
                     "--root", str(abc_workspace)])
        assert code == EXIT_OK
        path = (abc_workspace / "output" / "clients" / "abc_inc" / "2026-06"
                / "metrics.json")
        assert path.exists()
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["schema_version"] == 1
        assert doc["client_id"] == "abc_inc"
        assert doc["period"] == "2026-06"
        assert len(doc["metrics"]) > 300
        assert "wrote" in capsys.readouterr().out

    def test_output_is_keyed_by_client_and_period(self, abc_workspace):
        main(["analyze", "--client", "abc_inc", "--period", "2026-06",
              "--root", str(abc_workspace)])
        main(["analyze", "--client", "abc_inc", "--period", "2026-05",
              "--root", str(abc_workspace)])
        base = abc_workspace / "output" / "clients" / "abc_inc"
        assert sorted(p.name for p in base.iterdir()) == ["2026-05", "2026-06"]

    def test_valid_json_is_written_even_when_data_quality_fails(self, workspace):
        # The tiny client has only three months, so the TTM window is incomplete.
        code = main(["analyze", "--client", "tiny_co", "--period", "2025-03",
                     "--root", str(workspace)])
        assert code == EXIT_DATA
        path = (workspace / "output" / "clients" / "tiny_co" / "2025-03"
                / "metrics.json")
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert any(i["code"] == "ttm_window_incomplete"
                   for i in doc["data_quality_issues"])

    def test_source_workbook_is_untouched(self, abc_workspace, abc_config):
        source = (abc_workspace / "data" / "clients" / "abc_inc" / "source"
                  / abc_config.source_filename)
        before = source.read_bytes()
        main(["analyze", "--client", "abc_inc", "--period", "2026-06",
              "--root", str(abc_workspace)])
        assert source.read_bytes() == before


class TestReport:
    def test_writes_both_files(self, abc_workspace, capsys):
        code = main(["report", "--client", "abc_inc", "--period", "2026-06",
                     "--root", str(abc_workspace)])
        assert code == EXIT_OK
        out = abc_workspace / "output" / "clients" / "abc_inc" / "2026-06"
        assert (out / "metrics.json").exists()
        assert (out / "report.md").exists()
        report = (out / "report.md").read_text(encoding="utf-8")
        assert report.startswith("# ABC Inc")
        assert "## Reviewer checklist" in report
        captured = capsys.readouterr().out
        assert "number check:   PASS" in captured

    def test_deterministic_provider_is_used_by_default(self, abc_workspace):
        main(["report", "--client", "abc_inc", "--period", "2026-06",
              "--root", str(abc_workspace)])
        report = (abc_workspace / "output" / "clients" / "abc_inc" / "2026-06"
                  / "report.md").read_text(encoding="utf-8")
        assert "`deterministic` commentary provider" in report

    def test_an_unimplemented_provider_falls_back_with_a_warning(self, abc_workspace,
                                                                 capsys):
        code = main(["report", "--client", "abc_inc", "--period", "2026-06",
                     "--provider", "ollama", "--root", str(abc_workspace)])
        assert code == EXIT_OK
        captured = capsys.readouterr()
        assert "not implemented" in captured.err
        assert "Falling back" in captured.err

    def test_an_unknown_provider_also_falls_back_rather_than_crashing(self,
                                                                     abc_workspace,
                                                                     capsys):
        code = main(["report", "--client", "abc_inc", "--period", "2026-06",
                     "--provider", "gemini", "--root", str(abc_workspace)])
        assert code == EXIT_OK
        assert "Unknown commentary provider" in capsys.readouterr().err

    def test_report_is_reproducible_apart_from_the_timestamp(self, abc_workspace):
        out = abc_workspace / "output" / "clients" / "abc_inc" / "2026-06" / "report.md"
        main(["report", "--client", "abc_inc", "--period", "2026-06",
              "--root", str(abc_workspace)])
        first = out.read_text(encoding="utf-8")
        main(["report", "--client", "abc_inc", "--period", "2026-06",
              "--root", str(abc_workspace)])
        second = out.read_text(encoding="utf-8")

        def strip_time(text):
            return "\n".join(l for l in text.splitlines()
                             if "Generated:" not in l)

        assert strip_time(first) == strip_time(second)


class TestCheckCommentary:
    def _prepare(self, abc_workspace):
        main(["analyze", "--client", "abc_inc", "--period", "2026-06",
              "--root", str(abc_workspace)])

    def test_accepts_supported_numbers(self, abc_workspace, tmp_path, capsys):
        self._prepare(abc_workspace)
        note = tmp_path / "note.md"
        note.write_text("Revenue was $195,898.16 in Jun 2026.", encoding="utf-8")
        code = main(["check-commentary", "--client", "abc_inc", "--period", "2026-06",
                     "--file", str(note), "--root", str(abc_workspace)])
        assert code == EXIT_OK
        assert "PASS" in capsys.readouterr().out

    def test_rejects_invented_numbers(self, abc_workspace, tmp_path, capsys):
        self._prepare(abc_workspace)
        note = tmp_path / "note.md"
        note.write_text("Revenue was $195,898.16 but marketing was $88,500.00.",
                        encoding="utf-8")
        code = main(["check-commentary", "--client", "abc_inc", "--period", "2026-06",
                     "--file", str(note), "--root", str(abc_workspace)])
        assert code == EXIT_GUARD
        assert "REJECTED" in capsys.readouterr().out

    def test_json_output_is_machine_readable(self, abc_workspace, tmp_path, capsys):
        self._prepare(abc_workspace)
        capsys.readouterr()            # discard the analyze step's own output
        note = tmp_path / "note.md"
        note.write_text("Revenue was $1.00.", encoding="utf-8")
        main(["check-commentary", "--client", "abc_inc", "--period", "2026-06",
              "--file", str(note), "--json", "--root", str(abc_workspace)])
        payload = json.loads(capsys.readouterr().out)
        assert payload["passed"] is False
        assert payload["numbers_unsupported"] == 1

    def test_missing_metrics_file_is_a_usage_error(self, abc_workspace, tmp_path):
        note = tmp_path / "note.md"
        note.write_text("Anything.", encoding="utf-8")
        code = main(["check-commentary", "--client", "abc_inc", "--period", "2026-06",
                     "--file", str(note), "--root", str(abc_workspace)])
        assert code == EXIT_USAGE

    def test_missing_commentary_file_is_a_usage_error(self, abc_workspace):
        self._prepare(abc_workspace)
        code = main(["check-commentary", "--client", "abc_inc", "--period", "2026-06",
                     "--file", "nope.md", "--root", str(abc_workspace)])
        assert code == EXIT_USAGE


class TestUsageErrors:
    def test_unknown_client(self, abc_workspace, capsys):
        code = main(["analyze", "--client", "nobody", "--period", "2026-06",
                     "--root", str(abc_workspace)])
        assert code == EXIT_USAGE
        assert "No config for client" in capsys.readouterr().err

    @pytest.mark.parametrize("period", ["2026-13", "June", "2026/06", "26-06"])
    def test_malformed_period(self, abc_workspace, period):
        with pytest.raises(ValueError):
            main(["analyze", "--client", "abc_inc", "--period", period,
                  "--root", str(abc_workspace)])

    def test_a_period_outside_the_source_is_reported(self, abc_workspace, capsys):
        code = main(["analyze", "--client", "abc_inc", "--period", "2030-01",
                     "--root", str(abc_workspace)])
        assert code == EXIT_DATA
        assert "period_not_in_source" in capsys.readouterr().err

    def test_missing_workbook_is_reported(self, tmp_path, project_root, capsys):
        (tmp_path / "config" / "clients").mkdir(parents=True)
        shutil.copy(project_root / "config" / "clients" / "abc_inc.yaml",
                    tmp_path / "config" / "clients" / "abc_inc.yaml")
        code = main(["analyze", "--client", "abc_inc", "--period", "2026-06",
                     "--root", str(tmp_path)])
        assert code == EXIT_USAGE
        assert "not found" in capsys.readouterr().err

    def test_no_subcommand_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code != 0


class TestListPeriods:
    def test_lists_months_and_ttm_availability(self, abc_workspace, capsys):
        code = main(["list-periods", "--client", "abc_inc",
                     "--root", str(abc_workspace)])
        assert code == EXIT_OK
        out = capsys.readouterr().out
        assert "18 month(s)" in out
        assert out.count("ttm complete") == 7      # 2025-12 through 2026-06
        assert "INCOMPLETE" in out
