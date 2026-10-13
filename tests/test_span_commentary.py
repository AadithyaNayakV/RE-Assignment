"""Commentary, guard and brief for the year and whole-file scopes (Phase 5B, task 4).

The deterministic provider writes the year and whole-file commentary with the month
rules: every number traces to metrics.json, direction words match the sign of the change
they describe, and nothing states a cause.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from finance_engine.brief import build_brief
from finance_engine.cli import EXIT_GUARD, EXIT_OK, main
from finance_engine.commentary.deterministic import DeterministicProvider
from finance_engine.config import ClientConfig
from finance_engine.guard import NumberGuard
from finance_engine.report import build_report
from finance_engine.sources.excel import ExcelSource
from finance_engine.spans import build_all_metrics, build_year_metrics
from tests.helpers import build_monthly_workbook, write_monthly_config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.report_checks import CAUSAL_WORDS, _labels, _without, units  # noqa: E402


def months(year, first=1, last=12):
    return [f"{year}-{m:02d}" for m in range(first, last + 1)]


@pytest.fixture
def load(tmp_path):
    cfg_path = write_monthly_config(tmp_path / "config" / "clients")
    src = tmp_path / "data" / "clients" / "multi_co" / "source"
    src.mkdir(parents=True)

    def _load(month_list, overrides=None):
        path = build_monthly_workbook(src / "book.xlsx", month_list, overrides)
        cfg = ClientConfig.load_path(cfg_path)
        return ExcelSource(path).load(cfg), cfg

    _load.root = tmp_path
    return _load


def _full_report(doc):
    commentary = DeterministicProvider().generate(doc)
    guard = NumberGuard(doc).check(commentary)
    return commentary, guard, build_report(doc, commentary, "deterministic", guard.to_dict())


SCENARIOS = {
    "complete year": (months(2025), "year", 2025, None),
    "year to date with a prior year": (months(2024) + months(2025, 1, 6), "year", 2025, None),
    "gap inside the year": ([p for p in months(2025) if p != "2025-06"], "year", 2025, None),
    "falling year": (months(2024, 1, 6) + months(2025, 1, 6), "year", 2025,
                     {("Sales", p): 500.0 for p in months(2025, 1, 6)}),
    "whole file": (months(2025) + months(2026, 1, 3), "all", None, None),
    "whole file with a gap": ([p for p in months(2025) if p != "2025-03"], "all", None, None),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_commentary_and_report_pass_the_guard_and_the_wording_rule(load, name):
    month_list, scope, year, overrides = SCENARIOS[name]
    ds, cfg = load(month_list, overrides)
    # project_root keeps the source path project-relative, as every CLI run does; an
    # absolute tmp path ("pytest-105") would otherwise put stray digits in the report.
    doc = (build_year_metrics(ds, cfg, year, load.root) if scope == "year"
           else build_all_metrics(ds, cfg, load.root))
    commentary, guard, report = _full_report(doc)
    assert guard.passed, guard.summary()
    whole = NumberGuard(doc).check(report)
    assert whole.passed, whole.summary()
    labels = _labels(doc)
    causal = [u for u in units(report) if CAUSAL_WORDS.search(_without(u, labels))]
    assert not causal, causal
    assert "draft for a human reviewer" in report
    assert "## Reviewer checklist" in report


def test_a_gap_year_states_no_total_and_names_the_month(load):
    ds, cfg = load([p for p in months(2025) if p != "2025-06"])
    commentary = DeterministicProvider().generate(build_year_metrics(ds, cfg, 2025))
    assert "2025-06 is missing inside the year" in commentary
    assert "total revenue was" not in commentary


def test_year_to_date_says_it_is_not_annualised(load):
    ds, cfg = load(months(2025, 1, 6))
    commentary = DeterministicProvider().generate(build_year_metrics(ds, cfg, 2025))
    assert "year to date, 6 of 12 months" in commentary
    assert "not scaled, annualised or extrapolated" in commentary


def test_direction_words_follow_the_sign(load):
    ds, cfg = load(months(2024, 1, 6) + months(2025, 1, 6),
                   {("Sales", p): 500.0 for p in months(2025, 1, 6)})
    doc = build_year_metrics(ds, cfg, 2025)
    commentary = DeterministicProvider().generate(doc)
    change = next(x for x in doc["metrics"] if x["name"] == "revenue.total.yoy_abs")["value"]
    assert change < 0 and "total revenue fell" in commentary


class TestGuardOnSpanDocuments:
    @pytest.fixture
    def year_doc(self, load):
        ds, cfg = load(months(2024, 1, 6) + months(2025, 1, 6))
        return build_year_metrics(ds, cfg, 2025)        # revenue +720.00 on the prior year

    def test_a_wrong_direction_on_a_year_on_year_change_is_rejected(self, year_doc):
        guard = NumberGuard(year_doc)
        assert guard.check("Revenue rose $720.00 on the same months a year earlier.").passed
        result = guard.check("Revenue fell $720.00 on the same months a year earlier.")
        assert not result.passed and result.direction_errors

    def test_a_wrong_direction_on_a_start_to_end_change_is_rejected(self, load):
        ds, cfg = load(months(2025, 1, 4))
        doc = build_all_metrics(ds, cfg)                # revenue 1,200 -> 1,230: +30.00
        guard = NumberGuard(doc)
        assert guard.check("From first to last month revenue rose $30.00 (+2.5%).").passed
        assert not guard.check("From first to last month revenue fell $30.00.").passed

    def test_quarter_labels_are_not_figures(self, year_doc):
        result = NumberGuard(year_doc).check("Q1 and Q2 revenue were $3,630.00 and $3,720.00.")
        assert result.passed and result.checked == 2

    def test_an_invented_year_total_is_rejected(self, year_doc):
        assert not NumberGuard(year_doc).check("Revenue for 2025 was $7,351.00.").passed

    def test_a_value_on_a_rounding_half_step_passes(self):
        """0.75 written to one decimal is "0.8". It used to fail by 4e-17."""
        doc = {"metrics": [{"name": "x.mom_pct", "value": 0.75, "unit": "percent"}]}
        assert NumberGuard(doc).check("It moved +0.8%.").passed
        assert not NumberGuard(doc).check("It moved +0.9%.").passed


class TestBrief:
    def test_year_brief(self, load):
        ds, cfg = load(months(2024) + months(2025, 1, 6))
        doc = build_year_metrics(ds, cfg, 2025, load.root)
        brief = build_brief(doc)
        assert brief.startswith("# Brief: Multi Co — 2025 (year to date, 6 of 12 months)")
        for heading in ("## Status", "## Year totals", "## Quarters",
                        "## Months (`monthly.<YYYY-MM>.<measure>`)",
                        "## Highest and lowest months"):
            assert heading in brief
        assert "`revenue.total` | $7,350.00 | $6,630.00 | +$720.00" in brief
        assert NumberGuard(doc).check(brief).passed

    def test_all_brief(self, load):
        ds, cfg = load(months(2025) + months(2026, 1, 2))
        doc = build_all_metrics(ds, cfg, load.root)
        brief = build_brief(doc)
        assert "## Calendar years" in brief and "`year.2026.`" in brief
        assert "year to date, 2 of 12 months" in brief
        assert NumberGuard(doc).check(brief).passed

    def test_metric_filter_still_works(self, load):
        ds, cfg = load(months(2025))
        brief = build_brief(build_year_metrics(ds, cfg, 2025), "revenue.total.q")
        assert "`revenue.total.q1`" in brief and "`revenue.total.q4`" in brief


class TestCli:
    def test_check_commentary_on_the_whole_file(self, load, tmp_path, capsys):
        load(months(2025, 1, 4))
        root = ["--root", str(load.root)]
        main(["analyze", "--client", "multi_co", "--scope", "all", *root])
        capsys.readouterr()
        good, bad = tmp_path / "good.md", tmp_path / "bad.md"
        good.write_text("Revenue rose $30.00 from the first month to the last.", "utf-8")
        bad.write_text("Revenue fell $30.00 from the first month to the last.", "utf-8")
        assert main(["check-commentary", "--client", "multi_co", "--scope", "all",
                     "--file", str(good), "--json", *root]) == EXIT_OK
        assert json.loads(capsys.readouterr().out)["passed"] is True
        assert main(["check-commentary", "--client", "multi_co", "--scope", "all",
                     "--file", str(bad), *root]) == EXIT_GUARD

    def test_brief_json(self, load, capsys):
        load(months(2025))
        root = ["--root", str(load.root)]
        main(["analyze", "--client", "multi_co", "--scope", "year", "--year", "2025", *root])
        capsys.readouterr()
        assert main(["brief", "--client", "multi_co", "--scope", "year", "--year", "2025",
                     "--json", *root]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True and payload["scope"] == "year"
        assert payload["brief"].startswith("# Brief: Multi Co — 2025 (full year")
