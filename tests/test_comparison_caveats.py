"""Comparison caveats: a change that rests on a month that does not reconcile.

Built from tiny plain-value workbooks (tests.helpers.build_monthly_workbook) whose totals
are written as numbers, so an edited line leaves its section total disagreeing, exactly as
a careless edit to a real workbook would.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from finance_engine.brief import build_brief
from finance_engine.commentary.deterministic import DeterministicProvider
from finance_engine.commentary.orchestrator import validate
from finance_engine.config import ClientConfig
from finance_engine.guard import NumberGuard
from finance_engine.metrics import build_metrics
from finance_engine.report import build_report
from finance_engine.sources.excel import ExcelSource
from finance_engine.wording import unstated_caveats
from tests.helpers import build_monthly_workbook, write_monthly_config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.report_checks import check_report  # noqa: E402

MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]


@pytest.fixture
def make(tmp_path):
    cfg_path = write_monthly_config(tmp_path / "config" / "clients")
    src = tmp_path / "data" / "clients" / "multi_co" / "source"
    src.mkdir(parents=True)

    def _make(period, overrides=None, months=MONTHS):
        path = build_monthly_workbook(src / "book.xlsx", months, overrides)
        cfg = ClientConfig.load_path(cfg_path)
        return build_metrics(ExcelSource(path).load(cfg), cfg, period, project_root=tmp_path)
    return _make


def _report(doc, commentary=None):
    commentary = DeterministicProvider().generate(doc) if commentary is None else commentary
    guard = NumberGuard(doc).check(commentary).to_dict()
    return build_report(doc, commentary, "deterministic", guard)


class TestEngine:
    def test_a_clean_client_has_no_caveat_key(self, make):
        doc = make("2025-12")
        assert "comparison_caveats" not in doc

    def test_a_blank_prior_month_line(self, make):
        """Rent blank in November: the opex total no longer adds up in November."""
        doc = make("2025-12", {("Rent", "2025-11"): None})
        block = doc["comparison_caveats"]
        assert block["period"] == "2025-12" and block["period_reconciles"] is True
        texts = [i["text"] for i in block["items"]]
        # Newest first: Dec 2025 in column C, Nov 2025 in D; Rent is on row 19.
        assert texts == [
            "opex.total vs 2025-11: prior month failed opex_items_vs_total; blank cell at "
            "D19 (Rent)",
            "operating_income vs 2025-11: prior month failed opex_items_vs_total; blank "
            "cell at D19 (Rent)",
            "operating_margin_pct vs 2025-11: prior month failed opex_items_vs_total; blank "
            "cell at D19 (Rent)",
        ]
        measures = [i["measure"] for i in block["items"]]
        assert measures == ["opex.total", "operating_income", "operating_margin_pct"]
        first = block["items"][0]
        assert first["comparison"] == "mom" and first["against"] == "2025-11"
        assert first["failed_checks"] == ["opex_items_vs_total"]
        assert first["cells"][0]["code"] == "blank_cell"
        assert first["cells"][0]["label"] == "Rent"
        assert "opex.total.mom_abs" in first["metrics"]

    def test_a_non_numeric_prior_month_revenue_line(self, make):
        doc = make("2025-06", {("Sales", "2025-05"): "n/a"})
        measures = {i["measure"] for i in doc["comparison_caveats"]["items"]}
        assert measures == {"revenue.total", "gross_profit", "gross_margin_pct",
                            "operating_income", "operating_margin_pct"}
        cell = doc["comparison_caveats"]["items"][0]["cells"][0]
        assert cell["code"] == "non_numeric_cell" and cell["label"] == "Sales"

    def test_a_failed_check_alone(self, make):
        # Total Income edited by hand in May: revenue items no longer add to it.
        doc = make("2025-06", {("Total Income", "2025-05"): 9999.0})
        items = doc["comparison_caveats"]["items"]
        assert {i["measure"] for i in items} >= {"revenue.total", "gross_profit"}
        assert all(not i["cells"] for i in items)
        assert "revenue_items_vs_total" in items[0]["failed_checks"]

    def test_year_on_year(self, make):
        months = [f"2024-{m:02d}" for m in range(1, 13)] + MONTHS
        doc = make("2025-06", {("Total Income", "2024-06"): 9999.0}, months)
        yoy = [i for i in doc["comparison_caveats"]["items"] if i["comparison"] == "yoy"]
        assert [i["measure"] for i in yoy] == ["revenue.total"]
        assert yoy[0]["against"] == "2024-06"
        assert yoy[0]["metrics"] == ["revenue.total.yoy_abs", "revenue.total.yoy_pct"]

    def test_the_reporting_month_itself(self, make):
        doc = make("2025-06", {("Total Income", "2025-06"): 9999.0})
        block = doc["comparison_caveats"]
        assert block["period_reconciles"] is False
        assert block["period_failed_checks"] == ["revenue_items_vs_total", "gross_profit"]
        assert block["items"] == []

    def test_no_comparison_no_caveat(self, make):
        """The first month has no prior month, so nothing rests on one."""
        doc = make("2025-02", {("Rent", "2025-01"): 0.0, ("Total Income", "2025-01"): 1.0},
                   months=["2025-01", "2025-02"])
        items = (doc.get("comparison_caveats") or {}).get("items") or []
        assert all(i["against"] == "2025-01" for i in items)
        doc = make("2025-01", {("Total Income", "2025-01"): 1.0}, months=MONTHS)
        assert (doc.get("comparison_caveats") or {}).get("items") == []

    def test_other_months_do_not_trigger_a_caveat(self, make):
        doc = make("2025-12", {("Rent", "2025-05"): None})
        assert "comparison_caveats" not in doc


class TestReportAndBrief:
    def test_markers_and_notes_only_with_caveats(self, make):
        clean = _report(make("2025-12"))
        assert "†" not in clean and "Reporting month reconciles" not in clean
        doc = make("2025-12", {("Rent", "2025-11"): None})
        text = _report(doc)
        assert "| Reporting month reconciles | yes | — |" in text
        assert "| Comparison caveats | **3** |" in text
        assert text.count(" †") >= 4
        assert "† Rests on a month that does not reconcile" in text
        # The whole report traces, status row included: with a failure it quotes the
        # stored "1 of 60 checks failed", not a derived "59/60 checks passed".
        assert "1 of 60 checks failed across all periods" in text
        assert "59/60" not in text
        assert NumberGuard(doc).check(text).passed

    def test_the_deterministic_commentary_states_every_caveat(self, make):
        doc = make("2025-12", {("Rent", "2025-11"): None})
        commentary = DeterministicProvider().generate(doc)
        assert "### Comparisons that need care" in commentary
        assert "- Dec 2025 itself reconciles." in commentary
        assert unstated_caveats(commentary, doc) == []
        assert validate(commentary, doc, require_periods=False) == []

    def test_a_clean_clients_commentary_is_unchanged(self, make):
        assert "Comparisons that need care" not in DeterministicProvider().generate(
            make("2025-12"))

    def test_the_brief_puts_caveats_first(self, make):
        doc = make("2025-11", {("Payroll", "2025-10"): "n/a"})
        brief = build_brief(doc)
        assert brief.index("## Comparison caveats") < brief.index("## Status")
        assert "`opex.total` (Total operating expenses) vs 2025-10 (prior month)" in brief
        assert "- Reporting month 2025-11: reconciles." in brief
        assert NumberGuard(doc).check(brief).passed
        assert "## Comparison caveats" not in build_brief(make("2025-12"))


class TestTheCheck:
    def test_flags_a_commentary_that_omits_a_caveat(self, make):
        doc = make("2025-12", {("Rent", "2025-11"): None})
        plain = DeterministicProvider().generate(make("2025-12"))
        missing = unstated_caveats(plain, doc)
        assert [i["measure"] for i in missing] == ["opex.total", "operating_income",
                                                   "operating_margin_pct"]

    def test_the_measure_month_and_caveat_must_share_a_sentence(self, make):
        doc = make("2025-12", {("Rent", "2025-11"): None})
        spread = ("Operating expenses, operating income and operating margin moved "
                  "against 2025-11. Some month somewhere does not reconcile.")
        assert len(unstated_caveats(spread, doc)) == 3
        stated = ("Operating expenses, operating income and operating margin against "
                  "Nov 2025 rest on a prior month that does not reconcile.")
        assert unstated_caveats(stated, doc) == []

    def test_level_2_check(self, make):
        doc = make("2025-12", {("Rent", "2025-11"): None})
        case = {"skill": {}}
        good = {r["id"]: r for r in check_report(_report(doc), doc, case, "deterministic")}
        assert good["comparison_caveats_stated"]["passed"]
        plain = DeterministicProvider().generate(make("2025-12"))
        bad = {r["id"]: r for r in check_report(_report(doc, plain), doc, case,
                                                "deterministic")}
        assert not bad["comparison_caveats_stated"]["passed"]
        assert "opex.total vs 2025-11" in bad["comparison_caveats_stated"]["detail"]
        # The Python note under the tables does not count: only the commentary does.
        assert "† Rests on a month" in _report(doc, plain)

    def test_no_check_without_caveats(self, make):
        doc = make("2025-12")
        ids = {r["id"] for r in check_report(_report(doc), doc, {"skill": {}},
                                             "deterministic")}
        assert "comparison_caveats_stated" not in ids

    def test_provider_validation_sends_the_caveat_back(self, make):
        doc = make("2025-12", {("Rent", "2025-11"): None})
        plain = DeterministicProvider().generate(make("2025-12"))
        problems = validate(plain, doc, require_periods=False)
        assert any("State this comparison caveat" in p and "opex.total vs 2025-11" in p
                   for p in problems)

    def test_a_signed_figure_after_a_direction_word_is_a_violation(self, make):
        doc = make("2025-12")
        text = DeterministicProvider().generate(doc) + \
            "\nGross margin narrowed by -2.6 pts.\n"
        assert any("narrowed by -2.6 pts" in p and "unsigned" in p
                   for p in validate(text, doc))


def test_the_commentary_source_line(make):
    doc = make("2025-12")
    commentary = DeterministicProvider().generate(doc)
    guard = NumberGuard(doc).check(commentary).to_dict()
    assert "commentary provider from metrics.json._" in \
        build_report(doc, commentary, "deterministic", guard)
    from_brief = build_report(doc, commentary, "gemini", guard, commentary_from_brief=True)
    assert ("_Written by the `gemini` commentary provider from the brief (the compact view "
            "of metrics.json); every number was traced to metrics.json._") in from_brief


def test_check_count_quotes_only_stored_numbers():
    from finance_engine.formatting import check_count
    assert check_count({"checks_run": 108, "checks_failed": 0}) == "108/108 passed"
    assert check_count({"checks_run": 55, "checks_failed": 4}) == "4 of 55 failed"
    assert check_count({"checks_run": 60, "checks_failed": 1}, "checks passed",
                       "checks failed") == "1 of 60 checks failed"
