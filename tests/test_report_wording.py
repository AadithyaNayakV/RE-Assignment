"""Wording tests for report.md and the deterministic commentary.

These exist because wrong-but-plausible prose is harder to catch than a wrong number: the
figures were already correct in every case below, while the sentences around them said
something the data did not support. Each test asserts both the new phrasing and the
absence of the old, so it fails if the wording regresses.

The five defects, all live in a previous build:

1. The summary said "4 item(s)" and then listed three.
2. The summary and the TTM-window section claimed *every* trailing-twelve-month figure
   included the items, when all four were operating expenses and revenue was untouched.
3. "Possible one-offs" said none were found while the window table above listed four.
4. The multiple column read "n/a (prior period was zero)" — the right reason for a
   month-over-month base, the wrong one for an average over twelve months.
5. "What moved" quoted "25.0% or $10,000.00" and omitted the $1,000 floor that had in
   fact removed half the list.
"""

from __future__ import annotations

import re

import pytest

from finance_engine import naming
from finance_engine.commentary.deterministic import DeterministicProvider
from finance_engine.formatting import (one_off_multiple_reason, significance_rule)
from finance_engine.guard import check_commentary
from finance_engine.metrics import build_metrics
from finance_engine.report import build_report


@pytest.fixture
def commentary(abc_metrics) -> str:
    return DeterministicProvider().generate(abc_metrics)


@pytest.fixture
def report(abc_metrics, commentary) -> str:
    guard = check_commentary(commentary, abc_metrics)
    return build_report(abc_metrics, commentary, "deterministic", guard.to_dict())


def section(text: str, heading: str) -> str:
    """One section of a markdown document, up to the next heading of any level.

    The heading is matched at the start of a line and at its exact level. A plain
    substring search would find "## Possible one-offs" inside the commentary's
    "### Possible one-offs", since the report embeds the commentary.
    """
    match = re.search(rf"^{re.escape(heading)}\s*$", text, re.M)
    assert match, f"missing heading: {heading}"
    body = text[match.end():]
    return re.split(r"\n#{1,6} ", body)[0]


# ---- 1. every item named -----------------------------------------------

class TestSummaryNamesEveryItem:
    def test_the_count_matches_the_number_of_items_named(self, abc_metrics,
                                                         commentary):
        items = abc_metrics["movements"]["ttm_window_one_offs"]
        assert len(items) == 4, "fixture assumption changed"
        summary = section(commentary, "### Summary")
        assert f"contains {len(items)} item(s)" in summary
        for entry in items:
            assert entry["label"] in summary, f"{entry['label']} not named in summary"

    def test_all_four_abc_items_appear_by_name(self, commentary):
        summary = section(commentary, "### Summary")
        for label in ("Income Tax Benefit", "Stock-Based Compensation Expense",
                      "Bonus", "Events & Conferences"):
            assert label in summary, label

    def test_each_named_item_carries_its_month(self, abc_metrics, commentary):
        summary = section(commentary, "### Summary")
        for entry in abc_metrics["movements"]["ttm_window_one_offs"]:
            assert entry["period"] in summary

    def test_the_truncation_that_caused_the_bug_is_gone(self, abc_metrics, commentary):
        """The old code sliced the list to three while printing the full count."""
        summary = section(commentary, "### Summary")
        named = sum(1 for entry in abc_metrics["movements"]["ttm_window_one_offs"]
                    if entry["label"] in summary)
        assert named == len(abc_metrics["movements"]["ttm_window_one_offs"])
        assert named != 3


# ---- 1 & 2. which figures are affected ---------------------------------

class TestAffectedFiguresAreNamed:
    def test_the_phrase_is_derived_from_the_affected_metrics(self, abc_metrics):
        summary = abc_metrics["movements"]["ttm_window_one_offs_summary"]
        assert summary["affected_figures_phrase"] == (
            "operating expense and operating income"
        )
        assert summary["sections"] == ["opex"]

    def test_the_summary_names_the_affected_figures(self, commentary):
        summary = section(commentary, "### Summary")
        assert ("trailing-twelve-month operating expense and operating income figures"
                in summary)

    def test_the_summary_says_the_others_are_unaffected(self, commentary):
        summary = section(commentary, "### Summary")
        assert "other trailing-twelve-month figures are unaffected" in summary

    def test_the_summary_no_longer_overclaims(self, commentary):
        summary = section(commentary, "### Summary")
        for old in ("included in every trailing-twelve-month total",
                    "every trailing-twelve-month total",
                    "every trailing-twelve-month figure"):
            assert old not in summary, f"old overclaim present: {old!r}"

    def test_the_window_section_names_the_affected_figures(self, report):
        body = section(report, "## Items inside the trailing-twelve-month window")
        assert "operating expense and operating income" in body
        assert "not a run rate" in body
        assert "other trailing-twelve-month figures are unaffected" in body

    def test_the_window_section_no_longer_overclaims(self, report):
        body = section(report, "## Items inside the trailing-twelve-month window")
        for old in ("so every trailing-twelve-month figure above includes them",
                    "every trailing-twelve-month figure"):
            assert old not in body, f"old overclaim present: {old!r}"

    def test_the_validation_warning_also_names_them(self, abc_metrics):
        issue = next(i for i in abc_metrics["data_quality_issues"]
                     if i["code"] == "ttm_includes_possible_one_offs")
        assert "operating expense and operating income figures" in issue["message"]
        assert "Every trailing-twelve-month figure" not in issue["message"]
        assert issue["details"]["affected_figures_phrase"] == (
            "operating expense and operating income"
        )

    @pytest.mark.parametrize("names,expected", [
        ({"opex.total.ttm", "operating_income.ttm"},
         "operating expense and operating income"),
        ({"cogs.total.ttm", "gross_profit.ttm", "operating_income.ttm"},
         "cost of goods sold, gross profit and operating income"),
        ({"revenue.total.ttm"}, "revenue"),
        (set(), "trailing-twelve-month"),
    ])
    def test_phrase_builder(self, names, expected):
        assert naming.ttm_figures_phrase(names) == expected

    def test_phrase_ignores_category_and_group_metrics(self):
        """Listing every tagged category would bury the point."""
        phrase = naming.ttm_figures_phrase({
            "opex.total.ttm", "operating_income.ttm",
            "opex.category.payroll_people_costs.ttm", "opex.group.marketing.ttm",
        })
        assert phrase == "operating expense and operating income"
        assert "payroll" not in phrase

    def test_phrase_reads_in_statement_order(self):
        phrase = naming.ttm_figures_phrase({
            "operating_income.ttm", "revenue.total.ttm", "opex.total.ttm",
        })
        assert phrase == "revenue, operating expense and operating income"


# ---- 3. one-off sections must not contradict the window table ----------

class TestOneOffSectionsAreConsistent:
    def test_the_commentary_points_at_the_window_items(self, abc_metrics, commentary):
        body = section(commentary, "### Possible one-offs")
        items = abc_metrics["movements"]["ttm_window_one_offs"]
        assert f"contain {len(items)} such item(s)" in body
        for entry in items:
            assert entry["label"] in body, entry["label"]

    def test_the_commentary_no_longer_flatly_says_none(self, commentary):
        body = section(commentary, "### Possible one-offs")
        assert "No expense line stood far enough above" not in body
        # It must scope the negative to this month, not to the data as a whole.
        assert "Jun 2026 itself" in body

    def test_the_commentary_explains_where_they_do_apply(self, commentary):
        body = section(commentary, "### Possible one-offs")
        assert "operating expense and operating income" in body
        assert "trailing-twelve-month window table" in body

    def test_the_report_cross_references_the_table(self, abc_metrics, report):
        body = section(report, "## Possible one-offs")
        items = abc_metrics["movements"]["ttm_window_one_offs"]
        assert f"contains {len(items)}" in body
        assert "Items inside the trailing-twelve-month window" in body
        assert "None of them falls in Jun 2026" in body

    def test_the_report_no_longer_flatly_says_none(self, report):
        body = section(report, "## Possible one-offs")
        assert "No expense line stood far enough above its own trailing average" not in body
        assert "This is not the same as saying there are none" in body

    def test_a_clean_company_still_says_none_without_a_dangling_reference(self,
                                                                         tmp_path):
        """No window items means no cross-reference to a table that is not there."""
        from finance_engine.config import ClientConfig
        from finance_engine.sources.excel import ExcelSource
        from tests.helpers import build_small_workbook, write_small_config

        workbook = build_small_workbook(tmp_path / "tiny.xlsx")
        config = ClientConfig.load_path(
            write_small_config(tmp_path, {"ttm": {"months": 2}})
        )
        doc = build_metrics(ExcelSource(workbook).load(config), config, "2025-02")
        assert doc["movements"]["ttm_window_one_offs"] == []
        text = DeterministicProvider().generate(doc)
        rendered = build_report(doc, text)
        assert "trailing-twelve-month window table" not in text
        assert "Items inside the trailing-twelve-month window" not in rendered
        assert "## Possible one-offs" in rendered


# ---- 4. the multiple column's reason -----------------------------------

class TestMultipleColumnReason:
    def test_a_zero_average_says_so(self, report):
        body = section(report, "## Items inside the trailing-twelve-month window")
        assert "n/a (average was zero)" in body

    def test_the_month_over_month_wording_is_not_reused(self, report):
        """"prior period was zero" is right for a MoM base, wrong for an average."""
        body = section(report, "## Items inside the trailing-twelve-month window")
        assert "prior period was zero" not in body
        assert "prior period was negative" not in body

    def test_a_real_multiple_is_still_shown(self, report):
        body = section(report, "## Items inside the trailing-twelve-month window")
        # Bonus has a non-zero comparison average, so it gets a real multiple.
        assert re.search(r"\|\s*\d+\.\d+x\s*\|", body), body

    @pytest.mark.parametrize("code,expected", [
        ("zero_base", "average was zero"),
        ("negative_base", "average was negative"),
        (None, ""),
    ])
    def test_reason_text(self, code, expected):
        assert one_off_multiple_reason(code) == expected

    def test_the_monthly_table_uses_the_same_wording(self, abc_dataset, abc_config):
        """company_f's settlement path, but on ABC: a month with a zero-average one-off."""
        doc = build_metrics(abc_dataset, abc_config, "2025-12")
        entries = doc["movements"]["possible_one_offs"]
        zero_base = [c for c in entries
                     if c["multiple_unavailable_reason"] == "zero_base"]
        assert zero_base, "expected a zero-average one-off in 2025-12"
        rendered = build_report(doc, DeterministicProvider().generate(doc))
        body = section(rendered, "## Possible one-offs")
        assert "n/a (average was zero)" in body
        assert "prior period was zero" not in body


# ---- 5. the full significance rule -------------------------------------

class TestSignificanceRuleIsStated:
    def test_the_absolute_floor_is_quoted(self, commentary, report):
        for text in (commentary, report):
            assert "$1,000.00" in text, "the floor that halved the list is not stated"

    def test_all_three_routes_are_described(self, report):
        body = section(report, "## Significant movements")
        assert "an absolute change of at least $10,000.00" in body
        assert "at least 25.0% that is also at least $1,000.00" in body
        assert "crossing zero by at least $1,000.00" in body

    def test_the_commentary_states_the_same_rule(self, commentary):
        body = section(commentary, "### What moved")
        assert "an absolute change of at least $10,000.00" in body
        assert "at least 25.0% that is also at least $1,000.00" in body

    def test_the_old_two_part_wording_is_gone(self, commentary, report):
        for text in (commentary, report):
            assert "moved by at least 25.0% or $10,000.00" not in text
            assert "cleared the significance thresholds" not in text

    def test_the_filtering_is_quantified(self, abc_metrics, commentary, report):
        considered = abc_metrics["movements"]["candidates_considered"]
        kept = len(abc_metrics["movements"]["significant"])
        assert f"out of {considered} that changed at all" in commentary
        assert f"{kept} of {considered} lines that changed at all" in report

    def test_rule_builder_includes_every_clause(self):
        rule = significance_rule({
            "significant_mom_pct": 25.0,
            "significant_mom_abs": 10000.0,
            "significant_mom_min_abs": 1000.0,
        })
        assert "at least $10,000.00" in rule
        assert "at least 25.0%" in rule
        assert "at least $1,000.00" in rule
        assert rule.count("or") >= 2

    def test_rule_builder_copes_without_a_floor(self):
        rule = significance_rule({
            "significant_mom_pct": 25.0, "significant_mom_abs": 10000.0,
        })
        assert "at least $10,000.00" in rule
        assert "at least 25.0%" in rule


# ---- the whole thing still holds together ------------------------------

class TestStillConsistentOverall:
    def test_the_commentary_passes_the_guard(self, abc_metrics, commentary):
        result = check_commentary(commentary, abc_metrics)
        assert result.passed, result.summary()

    def test_no_section_contradicts_another_on_the_one_off_count(self, abc_metrics,
                                                                 report):
        count = len(abc_metrics["movements"]["ttm_window_one_offs"])
        window_body = section(report, "## Items inside the trailing-twelve-month window")
        oneoff_body = section(report, "## Possible one-offs")
        assert f"These {count} item(s)" in window_body
        assert f"contains {count}" in oneoff_body

    def test_every_tested_period_renders_without_contradiction(self, abc_dataset,
                                                               abc_config):
        """Guards the no-items and the items-present branches across real periods."""
        for period in ("2025-06", "2025-12", "2026-02", "2026-06"):
            doc = build_metrics(abc_dataset, abc_config, period)
            text = DeterministicProvider().generate(doc)
            guard = check_commentary(text, doc)
            assert guard.passed, f"{period}: {guard.summary()}"
            rendered = build_report(doc, text, "deterministic", guard.to_dict())
            window_items = doc["movements"]["ttm_window_one_offs"]
            if window_items:
                assert "Items inside the trailing-twelve-month window" in rendered
                for entry in window_items:
                    assert entry["label"] in rendered, f"{period}: {entry['label']}"
            else:
                assert "## Items inside the trailing-twelve-month window" not in rendered


# ---- n/a reason stated once ---------------------------------------------

class TestUnavailableChangeStatedOnce:
    """A missing change and its missing % used to print the reason twice:
    "Year on year: n/a (no comparable value) (n/a (no comparable value))"."""

    @staticmethod
    def _without(doc: dict, *names: str) -> dict:
        import copy
        doc = copy.deepcopy(doc)
        for metric in doc["metrics"]:
            if metric["name"] in names:
                metric["value"] = None
                metric["unavailable_reason"] = "missing_input"
        return doc

    def test_change_pair_formats(self):
        from finance_engine.formatting import Formatter
        fmt = Formatter()
        missing = {"value": None, "unit": "currency", "unavailable_reason": "missing_input"}
        missing_pct = {"value": None, "unit": "percent", "unavailable_reason": "missing_input"}
        assert fmt.change_pair(missing, missing_pct) == "n/a (no comparable value)"
        assert fmt.change_pair(None, None) == "n/a"
        both = fmt.change_pair({"value": 5.0, "unit": "currency"},
                               {"value": 2.0, "unit": "percent"})
        assert both == "+$5.00 (+2.0%)"
        zero_base = {"value": None, "unit": "percent", "unavailable_reason": "zero_base"}
        assert (fmt.change_pair({"value": 5.0, "unit": "currency"}, zero_base, "{abs}, {pct}")
                == "+$5.00, n/a (prior period was zero)")

    def test_year_on_year_line(self, abc_metrics):
        doc = self._without(abc_metrics, "revenue.total.yoy_abs", "revenue.total.yoy_pct")
        text = build_report(doc)
        assert "- Year on year: n/a (no comparable value)\n" in text
        assert "(n/a (" not in text

    def test_summary_mom_sentence(self, abc_metrics):
        doc = self._without(abc_metrics, "revenue.total.mom_abs", "revenue.total.mom_pct")
        text = DeterministicProvider().generate(doc)
        assert "(n/a (no comparable value) versus" in text
        assert "n/a (no comparable value), n/a (no comparable value)" not in text

    def test_available_values_unchanged(self, report):
        assert re.search(r"- Year on year: [+-]\$[\d,]+\.\d\d \([+-][\d.]+%\)", report)
