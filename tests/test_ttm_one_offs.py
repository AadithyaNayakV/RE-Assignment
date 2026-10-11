"""Golden tests for one-offs inside the TTM window, and for the movement rules.

The failure these guard against is specific and was real: ABC's June 2026
trailing-twelve-month figures silently included a 1,244,430 income tax benefit and
334,661 of stock-based compensation, both booked in December 2025 and both zero in every
other month. `possible_one_offs` looked only at the reporting month, so the report showed
a TTM operating expense total that no reader could reconcile with the monthly run rate,
with nothing flagged.

Expected values here are read straight from the workbook's own cells, listed in the
docstrings so a reviewer can check them without running anything.
"""

from __future__ import annotations

import pytest

from finance_engine.commentary.deterministic import DeterministicProvider
from finance_engine.config import ClientConfig
from finance_engine.guard import check_commentary
from finance_engine.metrics import build_metrics, crosses_zero, pct_change
from finance_engine.report import build_report
from finance_engine.sources.excel import ExcelSource
from tests.helpers import build_small_workbook, write_small_config

TOLERANCE = 0.01

# Income Statement, Dec 2025 (column I). Every other month in the window is 0.00 for the
# first two; Bonus runs at a few thousand a month outside December.
DEC_2025_INCOME_TAX_BENEFIT = -1244429.84     # row 101
DEC_2025_STOCK_COMPENSATION = 334660.53       # row 99
DEC_2025_BONUS = 80342.53                     # row 33
FEB_2026_EVENTS = 56851.23                    # row 65


class TestWindowOneOffsAreDetected:
    """`movements.ttm_window_one_offs` for ABC, June 2026 (window 2025-07..2026-06)."""

    @pytest.fixture
    def window(self, abc_metrics):
        return abc_metrics["movements"]["ttm_window_one_offs"]

    def test_the_december_items_are_found(self, window):
        found = {(c["label"], c["period"]): c for c in window}
        assert ("Income Tax Benefit", "2025-12") in found
        assert ("Stock-Based Compensation Expense", "2025-12") in found
        assert ("Bonus", "2025-12") in found

    def test_the_large_credit_is_reported_at_full_value(self, window):
        """A credit distorts an annual total exactly as much as a charge."""
        entry = next(c for c in window if c["label"] == "Income Tax Benefit")
        assert entry["period"] == "2025-12"
        assert entry["current"] == pytest.approx(DEC_2025_INCOME_TAX_BENEFIT,
                                                 abs=TOLERANCE)
        assert entry["comparison_average"] == pytest.approx(0.0)
        assert entry["excess_over_average"] == pytest.approx(
            DEC_2025_INCOME_TAX_BENEFIT, abs=TOLERANCE
        )
        # No multiple is reportable against a zero baseline, and none is invented.
        assert entry["multiple_of_average"] is None
        assert entry["multiple_unavailable_reason"] == "zero_base"

    def test_the_stock_compensation_charge_is_reported(self, window):
        entry = next(c for c in window
                     if c["label"] == "Stock-Based Compensation Expense")
        assert entry["current"] == pytest.approx(DEC_2025_STOCK_COMPENSATION,
                                                 abs=TOLERANCE)
        assert entry["comparison_average"] == pytest.approx(0.0)

    def test_bonus_is_found_against_a_non_zero_baseline(self, window):
        """Bonus runs at a few thousand a month, so this one has a real multiple."""
        entry = next(c for c in window if c["label"] == "Bonus")
        assert entry["current"] == pytest.approx(DEC_2025_BONUS, abs=TOLERANCE)
        baseline = entry["comparison_average"]
        assert 0 < baseline < 15000
        assert entry["current"] >= baseline * 3.0
        assert entry["excess_over_average"] == pytest.approx(
            DEC_2025_BONUS - baseline, abs=TOLERANCE
        )
        # A real baseline means a real multiple.
        assert entry["multiple_of_average"] == pytest.approx(
            DEC_2025_BONUS / baseline, rel=1e-9
        )
        assert entry["multiple_unavailable_reason"] is None

    def test_detection_covers_the_whole_window_not_just_the_reporting_month(self,
                                                                           window):
        """February 2026 is mid-window: the old code could not have seen it."""
        entry = next(c for c in window if c["label"] == "Events & Conferences")
        assert entry["period"] == "2026-02"
        assert entry["current"] == pytest.approx(FEB_2026_EVENTS, abs=TOLERANCE)
        periods = {c["period"] for c in window}
        assert periods != {"2026-06"}, "detection never left the reporting month"

    def test_ranked_by_how_much_they_move_the_total(self, window):
        excesses = [abs(c["excess_over_average"]) for c in window]
        assert excesses == sorted(excesses, reverse=True)

    def test_each_entry_is_traceable_and_labelled_possible(self, window):
        for entry in window:
            assert entry["sheet"] == "Income Statement"
            assert isinstance(entry["row"], int)
            assert entry["metric_name"].startswith(("opex.line.", "cogs.line."))
            assert "possible one-off" in entry["assessment"]
            assert "no information about the cause" in entry["assessment"]

    def test_no_cause_is_asserted_anywhere(self, window):
        for entry in window:
            text = entry["assessment"].lower()
            for forbidden in ("because", "due to", "caused by", "driven by"):
                assert forbidden not in text


class TestTtmMetricsCarryTheWarning:
    def test_the_warning_targets_named_metrics_not_every_ttm_metric(self,
                                                                      abc_metrics):
        """A blanket `*.ttm` tagged revenue.total.ttm, which no opex item can affect."""
        issue = next(i for i in abc_metrics["data_quality_issues"]
                     if i["code"] == "ttm_includes_possible_one_offs")
        assert issue["severity"] == "warning"
        assert issue["affects"] != ["*.ttm"]
        assert "*.ttm" not in issue["affects"]
        assert all(a.endswith(".ttm") for a in issue["affects"]), issue["affects"]
        assert set(issue["affects"]) == {
            "opex.total.ttm",
            "operating_income.ttm",
            "opex.category.finance_tax_non_cash.ttm",
            "opex.category.marketing_advertising.ttm",
            "opex.category.payroll_people_costs.ttm",
            "opex.group.marketing.ttm",
        }
        assert issue["details"]["sections"] == ["opex"]
        assert issue["details"]["window_start"] == "2025-07"
        assert issue["details"]["window_end"] == "2026-06"

    @pytest.mark.parametrize("name", [
        "operating_income.ttm",
        "opex.total.ttm",
        "opex.category.finance_tax_non_cash.ttm",
        "opex.category.payroll_people_costs.ttm",
        "opex.category.marketing_advertising.ttm",
        "opex.group.marketing.ttm",
    ])
    def test_affected_ttm_metrics_carry_it(self, abc_metric_map, name):
        """The totals that actually include the December and February items."""
        metric = abc_metric_map[name]
        assert metric["validation_status"] == "warning"
        assert any("trailing-twelve-month window" in w for w in metric["warnings"]), \
            f"{name} does not carry the window warning"

    @pytest.mark.parametrize("name", [
        "revenue.total.ttm",
        "cogs.total.ttm",
        "gross_profit.ttm",
        "gross_margin_pct.ttm",
        "opex.group.s_and_m_payroll.ttm",
        "opex.category.occupancy_insurance.ttm",
    ])
    def test_unaffected_ttm_metrics_stay_clean(self, abc_metric_map, name):
        """All four items are operating expenses.

        Revenue and COGS totals cannot contain them, and neither can an opex category or
        subtotal group they do not belong to. Tagging those would be a warning on a
        figure it has no bearing on.
        """
        metric = abc_metric_map[name]
        assert not any("trailing-twelve-month window" in w
                       for w in metric["warnings"]), f"{name} wrongly carries it"

    def test_the_wording_names_the_section(self, abc_metric_map):
        """It must not claim "every trailing-twelve-month figure"."""
        warning = next(w for w in abc_metric_map["opex.total.ttm"]["warnings"]
                       if "trailing-twelve-month window" in w)
        assert "operating expense item(s)" in warning
        assert "figures that include these lines" in warning
        assert "Every trailing-twelve-month figure" not in warning

    def test_the_warning_names_the_items(self, abc_metric_map):
        warning = next(w for w in abc_metric_map["opex.total.ttm"]["warnings"]
                       if "trailing-twelve-month window" in w)
        assert "Income Tax Benefit" in warning
        assert "Stock-Based Compensation Expense" in warning
        assert "2025-12" in warning
        assert "not a run rate" in warning

    def test_a_warning_does_not_block_the_value(self, abc_metric_map):
        """These are warnings, not errors: the TTM figure still computes."""
        assert abc_metric_map["opex.total.ttm"]["value"] is not None
        assert abc_metric_map["operating_income.ttm"]["value"] is not None

    def test_monthly_metrics_do_not_carry_it(self, abc_metric_map):
        """`*.ttm` must not leak onto the monthly figures."""
        for name in ("opex.total", "operating_income", "revenue.total",
                     "opex.total.mom_abs", "opex.line.bonus"):
            assert not any("trailing-twelve-month window" in w
                           for w in abc_metric_map[name]["warnings"]), name

    def test_no_warning_when_the_window_is_incomplete(self, abc_dataset, abc_config):
        """Nothing to warn about when there is no TTM figure in the first place."""
        doc = build_metrics(abc_dataset, abc_config, "2025-06")
        assert doc["ttm"]["complete"] is False
        assert doc["movements"]["ttm_window_one_offs"] == []
        assert not any(i["code"] == "ttm_includes_possible_one_offs"
                       for i in doc["data_quality_issues"])

    def test_a_clean_window_raises_no_warning(self, tmp_path):
        """A company with no outliers must not be warned about imaginary ones."""
        workbook = build_small_workbook(tmp_path / "tiny.xlsx")
        config = ClientConfig.load_path(
            write_small_config(tmp_path, {"ttm": {"months": 2}})
        )
        doc = build_metrics(ExcelSource(workbook).load(config), config, "2025-02")
        assert doc["ttm"]["complete"] is True
        assert doc["movements"]["ttm_window_one_offs"] == []


class TestWindowOneOffsReachTheReader:
    @pytest.fixture
    def rendered(self, abc_metrics):
        text = DeterministicProvider().generate(abc_metrics)
        guard = check_commentary(text, abc_metrics)
        report = build_report(abc_metrics, text, "deterministic", guard.to_dict())
        return text, guard, report

    def test_commentary_says_the_ttm_is_not_a_run_rate(self, rendered):
        text, _, _ = rendered
        assert "not a run rate" in text
        assert "Income Tax Benefit" in text

    def test_commentary_still_passes_the_guard(self, rendered):
        _, guard, _ = rendered
        assert guard.passed, guard.summary()

    def test_the_report_has_its_own_section(self, rendered):
        _, _, report = rendered
        assert "## Items inside the trailing-twelve-month window" in report
        section = report.split("## Items inside the trailing-twelve-month window", 1)[1]
        assert "Income Tax Benefit" in section
        assert "Stock-Based Compensation Expense" in section
        assert "not a run rate" in section

    def test_the_warning_sits_beside_the_ttm_table(self, rendered):
        """A reader must meet the caveat before leaving the TTM numbers."""
        _, _, report = rendered
        ttm_section = report.split("## Trailing twelve months", 1)[1].split("\n## ", 1)[0]
        assert "> **Note.**" in ttm_section
        assert "trailing-twelve-month window" in ttm_section

    def test_no_section_is_emitted_when_there_is_nothing_to_report(self, abc_dataset,
                                                                   abc_config):
        doc = build_metrics(abc_dataset, abc_config, "2025-06")
        report = build_report(doc, DeterministicProvider().generate(doc))
        assert "## Items inside the trailing-twelve-month window" not in report


class TestTheTwoOneOffScansAreConsistent:
    """`possible_one_offs` and `ttm_window_one_offs` are one implementation.

    They differ only in which months they test and what each month is compared against.
    Before they were unified, the monthly scan used a *signed* comparison and so could
    not see a large credit at all, and the two emitted different field names for the
    same concepts (`current`/`value`, `trailing_average`/
    `window_average_excluding_period`).
    """

    #: The single schema both scans produce.
    SCHEMA = {
        "label", "section", "category", "group", "group_key", "sheet", "row", "period",
        "current", "comparison_average", "comparison_basis", "comparison_months",
        "excess_over_average", "multiple_of_average", "multiple_unavailable_reason",
        "metric_name", "assessment",
    }

    @pytest.fixture
    def both(self, abc_dataset, abc_config):
        """February 2026 is a month where both scans fire."""
        doc = build_metrics(abc_dataset, abc_config, "2026-02")
        monthly = doc["movements"]["possible_one_offs"]
        window = doc["movements"]["ttm_window_one_offs"]
        assert monthly and window, "need both populated to compare them"
        return monthly, window

    def test_both_emit_exactly_the_same_keys(self, both):
        monthly, window = both
        for entry in monthly + window:
            assert set(entry) == self.SCHEMA, (
                f"{entry['label']}: "
                f"extra={sorted(set(entry) - self.SCHEMA)} "
                f"missing={sorted(self.SCHEMA - set(entry))}"
            )

    def test_the_old_field_names_are_gone(self, both):
        monthly, window = both
        for entry in monthly + window:
            for retired in ("value", "trailing_average",
                            "window_average_excluding_period", "months_compared",
                            "trailing_months_used"):
                assert retired not in entry, f"{entry['label']} still has {retired!r}"

    def test_both_state_what_they_compared_against(self, both):
        monthly, window = both
        for entry in monthly:
            assert entry["comparison_basis"].startswith("the ")
            assert ".." in entry["comparison_basis"]
        for entry in window:
            assert entry["comparison_basis"].startswith("other months in the window")

    def test_both_report_a_multiple_or_say_why_not(self, both):
        monthly, window = both
        for entry in monthly + window:
            ratio = entry["multiple_of_average"]
            reason = entry["multiple_unavailable_reason"]
            assert (ratio is None) == (reason is not None), entry["label"]
            if reason is not None:
                assert reason in ("zero_base", "negative_base")

    def test_both_are_magnitude_based(self, abc_dataset, abc_config):
        """A large credit must be detectable by the monthly scan too.

        December 2025's income tax benefit is -1,244,430 against a zero baseline. The
        old monthly rule tested `current >= min_abs`, so a negative value could never
        qualify however large it was.
        """
        doc = build_metrics(abc_dataset, abc_config, "2025-12")
        labels = {c["label"]: c for c in doc["movements"]["possible_one_offs"]}
        assert "Income Tax Benefit" in labels, sorted(labels)
        entry = labels["Income Tax Benefit"]
        assert entry["current"] < 0
        assert entry["excess_over_average"] < 0

    def test_both_rank_by_absolute_distance_from_the_average(self, both):
        for entries in both:
            excesses = [abs(c["excess_over_average"]) for c in entries]
            assert excesses == sorted(excesses, reverse=True)

    def test_both_label_their_findings_possible(self, both):
        monthly, window = both
        for entry in monthly + window:
            assert "possible one-off" in entry["assessment"]
            assert "no information about the cause" in entry["assessment"]

    def test_the_window_scan_says_it_is_the_window(self, both):
        monthly, window = both
        assert all("trailing-twelve-month window" in c["assessment"] for c in window)
        assert not any("trailing-twelve-month window" in c["assessment"]
                       for c in monthly)

    def test_metric_name_points_at_a_real_metric(self, abc_dataset, abc_config):
        doc = build_metrics(abc_dataset, abc_config, "2026-02")
        names = {x["name"] for x in doc["metrics"]}
        for key in ("possible_one_offs", "ttm_window_one_offs"):
            for entry in doc["movements"][key]:
                assert entry["metric_name"] in names, entry["metric_name"]

    def test_group_key_is_present_for_scoping(self, both):
        """`group_key` is what the TTM warning uses to tag the right group metric."""
        monthly, window = both
        for entry in monthly + window:
            assert "group_key" in entry
            if entry["group"] is None:
                assert entry["group_key"] is None


class TestNaming:
    def test_metrics_including_an_opex_line_are_bounded(self):
        from finance_engine import naming
        names = naming.metrics_including_opex_line(
            "Bad Debts", "Finance, Tax & Non-Cash", None
        )
        assert "opex.line.bad_debts" in names
        assert "opex.total" in names
        assert "opex.total.ttm" in names
        assert "opex.category.finance_tax_non_cash" in names
        assert "opex.group.ungrouped" in names
        # A sibling line is not included.
        assert "opex.line.travel" not in names
        # `ungrouped` has no TTM metric, so none is claimed.
        assert "opex.group.ungrouped.ttm" not in names

    def test_a_grouped_line_tags_its_group_including_ttm(self):
        from finance_engine import naming
        names = naming.metrics_including_opex_line(
            "Events & Conferences", "Marketing & Advertising", "marketing"
        )
        assert "opex.group.marketing" in names
        assert "opex.group.marketing.ttm" in names
        assert "opex.group.ungrouped" not in names

    def test_ttm_metrics_by_section(self):
        from finance_engine import naming
        assert naming.ttm_metrics_for_section("opex") == {
            "opex.total.ttm", "operating_income.ttm"
        }
        assert "revenue.total.ttm" not in naming.ttm_metrics_for_section("opex")
        assert naming.ttm_metrics_for_section("cogs") == {
            "cogs.total.ttm", "gross_profit.ttm", "operating_income.ttm"
        }

    def test_slug_matches_the_metrics_module(self):
        from finance_engine import metrics, naming
        assert metrics.slug is naming.slug
        assert naming.slug("S & M Payroll") == "s_m_payroll"


class TestSignificanceRequiresBothBars:
    """A large percentage on a trivial amount is noise, not a movement."""

    def test_the_min_absolute_floor_is_applied(self, abc_metrics):
        min_abs = abc_metrics["movements"]["thresholds"]["significant_mom_min_abs"]
        assert min_abs == 1000.0
        for c in abc_metrics["movements"]["significant"]:
            abs_floor = abc_metrics["movements"]["thresholds"]["significant_mom_abs"]
            assert (abs(c["change_abs"]) >= abs_floor
                    or abs(c["change_abs"]) >= min_abs), c["label"]

    def test_the_rule_is_stated_in_the_document(self, abc_metrics):
        rule = abc_metrics["movements"]["thresholds"]["rule"]
        assert "AND" in rule
        assert "25.0%" in rule

    def test_the_list_is_shorter_than_the_pool_it_came_from(self, abc_metrics):
        movements = abc_metrics["movements"]
        assert movements["candidates_considered"] > len(movements["significant"])

    def test_a_big_percentage_on_a_small_amount_is_excluded(self, abc_dataset,
                                                            abc_config):
        """Raise the floor and the small movements must drop out, large ones stay."""
        import copy
        raw = copy.deepcopy(abc_config.raw)
        raw["thresholds"]["significant_mom_min_abs"] = 5000.0
        strict = ClientConfig(raw, abc_config.path)
        doc = build_metrics(abc_dataset, strict, "2026-06")
        for c in doc["movements"]["significant"]:
            assert (abs(c["change_abs"]) >= 10000.0
                    or abs(c["change_abs"]) >= 5000.0), c["label"]
        # Events & Conferences moved +17,970.67 and must survive any sane floor.
        assert "Events & Conferences" in {c["label"]
                                          for c in doc["movements"]["significant"]}

    def test_a_percentage_alone_no_longer_qualifies(self, abc_dataset, abc_config):
        """With the floor above every change, only the absolute bar can qualify a line."""
        import copy
        raw = copy.deepcopy(abc_config.raw)
        raw["thresholds"]["significant_mom_min_abs"] = 1_000_000.0
        strict = ClientConfig(raw, abc_config.path)
        doc = build_metrics(abc_dataset, strict, "2026-06")
        for c in doc["movements"]["significant"]:
            assert abs(c["change_abs"]) >= 10000.0, c["label"]


class TestSignChange:
    def test_pct_change_refuses_a_crossing(self):
        # Bad Debts: +12,935.29 -> -1,806.24
        value, reason = pct_change(-1806.24, 12935.29)
        assert value is None
        assert reason == "sign_change"

    def test_a_fall_to_exactly_zero_is_still_minus_one_hundred(self):
        assert pct_change(0.0, 50.0) == (-100.0, None)

    def test_crosses_zero_detects_both_directions(self):
        assert crosses_zero(-1.0, 1.0) is True
        assert crosses_zero(1.0, -1.0) is True
        assert crosses_zero(1.0, 2.0) is False
        assert crosses_zero(0.0, 1.0) is False
        assert crosses_zero(None, 1.0) is False

    def test_bad_debts_metric_reports_a_sign_change(self, abc_metric_map):
        metric = abc_metric_map["opex.line.bad_debts.mom_pct"]
        assert metric["value"] is None
        assert metric["unavailable_reason"] == "sign_change"
        assert "crossed zero" in metric["notes"][0]

    def test_the_absolute_change_is_still_reported(self, abc_metric_map):
        """Withholding the percentage must not withhold the real information."""
        metric = abc_metric_map["opex.line.bad_debts.mom_abs"]
        assert metric["value"] == pytest.approx(-14741.53, abs=TOLERANCE)

    def test_the_movement_is_marked_rather_than_given_a_percentage(self, abc_metrics):
        entry = next(c for c in abc_metrics["movements"]["significant"]
                     if c["label"] == "Bad Debts")
        assert entry["sign_change"] is True
        assert entry["change_pct"] is None
        assert entry["change_pct_unavailable_reason"] == "sign_change"
        assert entry["direction"] == "sign change: charge to credit"
        # The misleading figure must be gone.
        assert entry["change_pct"] != pytest.approx(-113.96, abs=0.01)

    def test_an_ordinary_movement_keeps_its_percentage_and_direction(self, abc_metrics):
        entry = next(c for c in abc_metrics["movements"]["significant"]
                     if c["label"] == "Events & Conferences")
        assert entry["sign_change"] is False
        assert entry["change_pct"] == pytest.approx(158.6966869, abs=1e-4)
        assert entry["direction"] == "increase"

    def test_the_reader_is_told_it_was_a_crossing(self, abc_metrics):
        text = DeterministicProvider().generate(abc_metrics)
        guard = check_commentary(text, abc_metrics)
        report = build_report(abc_metrics, text, "deterministic", guard.to_dict())
        assert "sign change" in text
        assert "crossed zero" in report
        assert "not a percentage reduction" in report
        assert guard.passed, guard.summary()

    def test_no_misleading_percentage_survives_into_the_report(self, abc_metrics):
        text = DeterministicProvider().generate(abc_metrics)
        report = build_report(abc_metrics, text, "deterministic")
        assert "-113.96%" not in report
        assert "-114.0%" not in report


class TestRelativeSourcePaths:
    def test_source_paths_are_project_relative(self, abc_metrics):
        source = abc_metrics["source"]
        assert source["path"] == (
            "data/clients/abc_inc/source/ABC Inc - Financial Statements.xlsx"
        )
        assert source["config_path"] == "config/clients/abc_inc.yaml"

    def test_no_absolute_windows_path_leaks(self, abc_metrics):
        for key in ("path", "config_path", "filename"):
            value = abc_metrics["source"][key]
            assert not value.startswith("C:"), value
            assert "\\" not in value, value
            assert "Users" not in value, value

    def test_the_filename_is_still_available_on_its_own(self, abc_metrics):
        assert abc_metrics["source"]["filename"] == (
            "ABC Inc - Financial Statements.xlsx"
        )

    def test_the_checksum_still_identifies_the_file(self, abc_metrics):
        assert len(abc_metrics["source"]["sha256"]) == 64

    def test_the_report_shows_the_relative_path(self, abc_metrics):
        report = build_report(abc_metrics, "")
        assert "data/clients/abc_inc/source/" in report
        assert "C:\\Users" not in report

    def test_a_file_outside_the_project_keeps_a_full_path(self, tmp_path):
        """Silently shortening an outside path to a bare filename would look local."""
        from finance_engine.metrics import relative_path
        outside = tmp_path / "elsewhere" / "book.xlsx"
        result = relative_path(outside, tmp_path / "project")
        assert result.endswith("elsewhere/book.xlsx")
        assert "/" in result

    def test_paths_use_forward_slashes_on_every_platform(self, tmp_path):
        from finance_engine.metrics import relative_path
        target = tmp_path / "a" / "b" / "c.xlsx"
        assert relative_path(target, tmp_path) == "a/b/c.xlsx"
