"""Choosing one workbook when a client's folder holds several (Phase 5B, task 1).

Two files share one layout. Exactly one is read per run, it is named, outputs are kept
apart, and no comparison ever reaches into the other file. A client whose config names its
file, or whose folder holds one workbook, behaves exactly as before.

Expected figures come from `tests.helpers.monthly_values`, written out by hand there:
revenue = 1200 + 10n, where n is months since Jan 2025.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from finance_engine.cli import EXIT_DATA, EXIT_OK, EXIT_USAGE, main
from tests.helpers import (build_monthly_workbook, build_small_workbook,
                           write_monthly_config, write_small_config)

FIRST = [f"2025-{m:02d}" for m in range(1, 7)]           # Jan..Jun 2025
SECOND = [f"2025-{m:02d}" for m in range(6, 13)]         # Jun..Dec 2025 (Jun in both)


@pytest.fixture
def two_files(tmp_path):
    """multi_co: no source.filename, two workbooks overlapping in June 2025."""
    write_monthly_config(tmp_path / "config" / "clients")
    src = tmp_path / "data" / "clients" / "multi_co" / "source"
    src.mkdir(parents=True)
    build_monthly_workbook(src / "first.xlsx", FIRST)
    build_monthly_workbook(src / "second.xlsx", SECOND)
    return tmp_path


def _run(root, *args):
    return main([*args, "--root", str(root)])


def _doc(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _m(doc, name):
    return next(x for x in doc["metrics"] if x["name"] == name)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestAutomaticChoice:
    def test_period_only_in_the_first_file(self, two_files, capsys):
        code = _run(two_files, "analyze", "--client", "multi_co", "--period", "2025-03")
        assert code == EXIT_DATA                     # 3 months: the TTM window is short
        assert "source: first.xlsx" in capsys.readouterr().err
        # Without --file the output path is unchanged: no file stem in it.
        doc = _doc(two_files / "output" / "clients" / "multi_co" / "2025-03"
                   / "metrics.json")
        assert doc["source"]["filename"] == "first.xlsx"
        assert doc["source"]["sha256"] == _sha(
            two_files / "data" / "clients" / "multi_co" / "source" / "first.xlsx")
        assert doc["source"]["selection"]["mode"] == "auto"
        assert _m(doc, "revenue.total")["value"] == 1220.0          # 1200 + 10 x 2

    def test_period_only_in_the_second_file(self, two_files, capsys):
        _run(two_files, "analyze", "--client", "multi_co", "--period", "2025-09")
        assert "source: second.xlsx" in capsys.readouterr().err
        doc = _doc(two_files / "output" / "clients" / "multi_co" / "2025-09"
                   / "metrics.json")
        assert doc["source"]["filename"] == "second.xlsx"
        assert _m(doc, "revenue.total")["value"] == 1280.0          # 1200 + 10 x 8
        assert _m(doc, "revenue.total.mom_abs")["value"] == 10.0

    def test_period_in_both_files_stops(self, two_files, capsys):
        code = _run(two_files, "analyze", "--client", "multi_co", "--period", "2025-06")
        err = capsys.readouterr().err
        assert code == EXIT_USAGE
        assert "first.xlsx" in err and "second.xlsx" in err and "--file" in err
        assert not (two_files / "output").exists()

    def test_no_file_holds_the_period(self, two_files, capsys):
        code = _run(two_files, "analyze", "--client", "multi_co", "--period", "2030-01")
        err = capsys.readouterr().err
        assert code == EXIT_USAGE
        assert "No workbook" in err
        assert "first.xlsx (2025-01..2025-06" in err
        assert "second.xlsx (2025-06..2025-12" in err
        assert not (two_files / "output").exists()

    def test_failure_with_json_is_machine_readable(self, two_files, capsys):
        code = _run(two_files, "analyze", "--client", "multi_co", "--period", "2025-06",
                    "--json")
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_USAGE
        assert payload["ok"] is False and payload["exit_code"] == EXIT_USAGE
        assert "second.xlsx" in payload["error"]


class TestExplicitFile:
    def test_file_forces_one_workbook_and_keys_the_output(self, two_files, capsys):
        code = _run(two_files, "analyze", "--client", "multi_co", "--period", "2025-06",
                    "--file", "second.xlsx", "--json")
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_DATA
        path = (two_files / "output" / "clients" / "multi_co" / "second" / "2025-06"
                / "metrics.json")
        assert path.exists()
        assert payload["outputs"]["metrics"].endswith("multi_co/second/2025-06/metrics.json")
        assert payload["source"]["filename"] == "second.xlsx"
        doc = _doc(path)
        assert doc["source"]["selection"]["mode"] == "explicit"
        assert doc["source"]["sha256"] == payload["source"]["sha256"]

    def test_a_comparison_month_in_the_other_file_is_na(self, two_files, capsys):
        """June is in both files; May only in the first. Reading the second, the
        month-over-month comparison is n/a and says where May is. It is not read."""
        _run(two_files, "report", "--client", "multi_co", "--period", "2025-06",
             "--file", "second.xlsx")
        out = two_files / "output" / "clients" / "multi_co" / "second" / "2025-06"
        doc = _doc(out / "metrics.json")
        for name in ("revenue.total.mom_abs", "revenue.total.mom_pct",
                     "revenue.total.prior", "opex.total.mom_abs"):
            metric = _m(doc, name)
            assert metric["value"] is None
            assert metric["unavailable_reason"] == "in_other_file"
            assert any("prior period 2025-05 is in a different file (first.xlsx)" in n
                       for n in metric["notes"]), metric["notes"]
        assert _m(doc, "revenue.total")["value"] == 1250.0           # 1200 + 10 x 5
        report = (out / "report.md").read_text(encoding="utf-8")
        assert "n/a (prior period is in a different file)" in report
        assert "| Commentary number check | PASS |" in report

    def test_brief_and_check_commentary_read_the_file_keyed_output(self, two_files,
                                                                    tmp_path, capsys):
        _run(two_files, "analyze", "--client", "multi_co", "--period", "2025-09",
             "--file", "second.xlsx")
        capsys.readouterr()
        assert _run(two_files, "brief", "--client", "multi_co", "--period", "2025-09",
                    "--file", "second.xlsx") == EXIT_OK
        assert "$1,280.00" in capsys.readouterr().out
        note = tmp_path / "note.md"
        note.write_text("Revenue was $1,280.00 in Sep 2025.", encoding="utf-8")
        assert _run(two_files, "check-commentary", "--client", "multi_co", "--period",
                    "2025-09", "--workbook", "second.xlsx", "--file", str(note)) == EXIT_OK
        # Without --workbook it looks at the unkeyed path, which this run never wrote.
        assert _run(two_files, "check-commentary", "--client", "multi_co", "--period",
                    "2025-09", "--file", str(note)) == EXIT_USAGE

    def test_a_path_is_refused(self, two_files, capsys):
        code = _run(two_files, "analyze", "--client", "multi_co", "--period", "2025-03",
                    "--file", "../other/first.xlsx")
        assert code == EXIT_USAGE
        assert "not a path" in capsys.readouterr().err

    def test_an_unknown_file_lists_the_workbooks(self, two_files, capsys):
        code = _run(two_files, "analyze", "--client", "multi_co", "--period", "2025-03",
                    "--file", "third.xlsx")
        err = capsys.readouterr().err
        assert code == EXIT_USAGE
        assert "first.xlsx, second.xlsx" in err


class TestListPeriods:
    def test_lists_each_file_with_its_first_and_last_period(self, two_files, capsys):
        assert _run(two_files, "list-periods", "--client", "multi_co") == EXIT_OK
        out = capsys.readouterr().out
        assert "2 workbooks" in out
        assert "first.xlsx  2025-01..2025-06, 6 month(s)" in out
        assert "second.xlsx  2025-06..2025-12, 7 month(s)" in out

    def test_json(self, two_files, capsys):
        _run(two_files, "list-periods", "--client", "multi_co", "--json")
        payload = json.loads(capsys.readouterr().out)
        files = {f["filename"]: f for f in payload["files"]}
        assert files["first.xlsx"]["first_period"] == "2025-01"
        assert files["second.xlsx"]["last_period"] == "2025-12"
        assert payload["selected"] is None

    def test_with_file_lists_that_files_months(self, two_files, capsys):
        _run(two_files, "list-periods", "--client", "multi_co", "--file", "first.xlsx")
        out = capsys.readouterr().out
        assert "6 month(s)" in out and "  2025-06  ttm" in out and "(selected)" in out


class TestExistingClientsUnchanged:
    """No --file: a config that names its file reads it, writes where it always did, and
    adds nothing to metrics.json, even with a second workbook beside it."""

    @pytest.fixture
    def tiny(self, tmp_path):
        write_small_config(tmp_path / "config" / "clients")
        src = tmp_path / "data" / "clients" / "tiny_co" / "source"
        src.mkdir(parents=True)
        build_small_workbook(src / "tiny.xlsx")
        return tmp_path

    def test_single_file_client(self, tiny, capsys):
        assert _run(tiny, "analyze", "--client", "tiny_co", "--period", "2025-03") \
            == EXIT_DATA
        captured = capsys.readouterr()
        assert "source:" not in captured.err
        doc = _doc(tiny / "output" / "clients" / "tiny_co" / "2025-03" / "metrics.json")
        assert "selection" not in doc["source"]
        assert _m(doc, "revenue.total.mom_abs").get("unavailable_reason") is None
        _run(tiny, "list-periods", "--client", "tiny_co")
        assert "workbooks in" not in capsys.readouterr().out

    def test_configured_file_wins_over_a_sibling(self, tiny, capsys):
        src = tiny / "data" / "clients" / "tiny_co" / "source"
        build_small_workbook(src / "another.xlsx", drop_period_index=0)
        assert _run(tiny, "analyze", "--client", "tiny_co", "--period", "2025-01") \
            == EXIT_DATA
        doc = _doc(tiny / "output" / "clients" / "tiny_co" / "2025-01" / "metrics.json")
        assert doc["source"]["filename"] == "tiny.xlsx"
        assert "selection" not in doc["source"]
        assert _m(doc, "revenue.total.mom_abs")["unavailable_reason"] == "missing_input"
        # list-periods keeps its old lines and adds the folder listing after them.
        capsys.readouterr()
        _run(tiny, "list-periods", "--client", "tiny_co")
        out = capsys.readouterr().out
        assert out.splitlines()[0].endswith("3 month(s)")
        assert "tiny.xlsx" in out and "(read by default)" in out

    def test_hint_when_the_configured_file_lacks_the_month(self, two_files, capsys):
        write_monthly_config(two_files / "config" / "clients",
                             overrides={"source": {"filename": "first.xlsx"}})
        code = _run(two_files, "analyze", "--client", "multi_co", "--period", "2025-09")
        assert code == EXIT_DATA                   # unchanged: period_not_in_source
        err = capsys.readouterr().err
        assert "period_not_in_source" in err
        assert "hint: 2025-09 is in other workbook(s)" in err and "second.xlsx" in err
        doc = _doc(two_files / "output" / "clients" / "multi_co" / "2025-09"
                   / "metrics.json")
        assert doc["source"]["filename"] == "first.xlsx"
