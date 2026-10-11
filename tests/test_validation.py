"""Validation severity and its consequences.

The distinction being tested: an **error** blocks the metrics it affects and they become
`n/a`; a **warning** lets them compute but travels with them. Unrelated metrics must keep
working in both cases — a problem with the TTM window must not take down the monthly
figures.
"""

from __future__ import annotations

import pytest

from finance_engine.config import ClientConfig
from finance_engine.metrics import build_metrics
from finance_engine.sources.excel import ExcelSource
from finance_engine.validation import ERROR, GLOBAL, WARNING, ValidationReport, validate
from tests.helpers import build_small_workbook, write_small_config


def dataset_for(tmp_path, overrides=None, **kwargs):
    workbook = build_small_workbook(tmp_path / "tiny.xlsx", **kwargs)
    config = ClientConfig.load_path(write_small_config(tmp_path, overrides))
    return ExcelSource(workbook).load(config), config


def codes(report, severity=None):
    return {i.code for i in report.issues
            if severity is None or i.severity == severity}


class TestMatching:
    def test_exact_name(self):
        r = ValidationReport()
        r.error("x", "msg", ("revenue.total",))
        assert r.blocked("revenue.total")
        assert not r.blocked("revenue.total.ttm")

    def test_prefix(self):
        r = ValidationReport()
        r.warn("x", "msg", ("revenue.",))
        assert [i.code for i in r.for_metric("revenue.stream.widgets")] == ["x"]
        assert r.for_metric("opex.total") == []

    def test_suffix(self):
        r = ValidationReport()
        r.error("x", "msg", ("*.ttm",))
        assert r.blocked("opex.category.people.ttm")
        assert r.blocked("revenue.total.ttm")
        # The monthly figure under the same family must be untouched.
        assert not r.blocked("opex.category.people")

    def test_global_affects_everything(self):
        r = ValidationReport()
        r.error("x", "msg", (GLOBAL,))
        assert r.blocked("anything.at.all")
        assert len(r.fatal) == 1

    def test_warnings_do_not_block(self):
        r = ValidationReport()
        r.warn("x", "msg", ("revenue.total",))
        assert not r.blocked("revenue.total")
        assert len(r.for_metric("revenue.total")) == 1


class TestMissingMonths:
    def test_a_calendar_gap_is_warned_about(self, tmp_path):
        # February is dropped entirely, leaving Jan and Mar.
        dataset, config = dataset_for(tmp_path, drop_period_index=1)
        assert dataset.period_keys() == ["2025-01", "2025-03"]
        report = validate(dataset, config, "2025-03")
        assert "missing_months" in codes(report, WARNING)
        issue = next(i for i in report.issues if i.code == "missing_months")
        assert issue.details["missing"] == ["2025-02"]

    def test_a_gap_warns_but_does_not_block_the_monthly_figures(self, tmp_path):
        dataset, config = dataset_for(tmp_path, drop_period_index=1)
        doc = build_metrics(dataset, config, "2025-03")
        m = {x["name"]: x for x in doc["metrics"]}
        # March still computes: 0 + 25 = 25.
        assert m["revenue.total"]["value"] == 25.0
        assert m["revenue.total"]["validation_status"] == "warning"
        assert any("missing" in w.lower() for w in m["revenue.total"]["warnings"])

    def test_month_over_month_across_a_gap_has_no_prior_value(self, tmp_path):
        dataset, config = dataset_for(tmp_path, drop_period_index=1)
        doc = build_metrics(dataset, config, "2025-03")
        m = {x["name"]: x for x in doc["metrics"]}
        # The prior month (2025-02) is absent, so there is nothing to compare against.
        assert m["revenue.total.mom_abs"]["value"] is None
        assert m["revenue.total.mom_pct"]["value"] is None

    def test_contiguous_months_raise_no_gap_warning(self, tmp_path):
        dataset, config = dataset_for(tmp_path)
        assert "missing_months" not in codes(validate(dataset, config, "2025-03"))


class TestTtmCompleteness:
    def test_incomplete_window_is_an_error(self, tmp_path):
        dataset, config = dataset_for(tmp_path)
        report = validate(dataset, config, "2025-03")
        assert "ttm_window_incomplete" in codes(report, ERROR)

    def test_the_error_blocks_only_ttm_metrics(self, tmp_path):
        dataset, config = dataset_for(tmp_path)
        doc = build_metrics(dataset, config, "2025-03")
        m = {x["name"]: x for x in doc["metrics"]}
        assert m["revenue.total.ttm"]["validation_status"] == "blocked"
        # The monthly figures are unaffected.
        assert m["revenue.total"]["value"] == 25.0
        assert m["revenue.total"]["validation_status"] != "blocked"
        assert m["opex.category.people"]["value"] == 20.0

    def test_complete_window_raises_nothing(self, tmp_path):
        dataset, config = dataset_for(tmp_path, {"ttm": {"months": 2}})
        assert "ttm_window_incomplete" not in codes(
            validate(dataset, config, "2025-02")
        )


class TestBlankVersusZero:
    def test_a_blank_month_on_a_populated_row_is_warned_about(self, tmp_path):
        # Marketing has values in Jan and Mar but is genuinely blank in Feb.
        dataset, config = dataset_for(tmp_path,
                                      blank_cells=[("Marketing spend", 1)])
        report = validate(dataset, config, "2025-02")
        assert "blank_not_zero" in codes(report, WARNING)

    def test_a_blank_is_not_summed_as_zero(self, tmp_path):
        dataset, config = dataset_for(tmp_path,
                                      blank_cells=[("Marketing spend", 1)])
        doc = build_metrics(dataset, config, "2025-02")
        m = {x["name"]: x for x in doc["metrics"]}
        # Feb opex without marketing: rent 10 + salaries 25 = 35, and the engine says so
        # while recording that one row was missing.
        assert m["opex.total"]["value"] == 35.0
        assert m["opex.line.marketing_spend"]["value"] is None
        assert m["opex.line.marketing_spend"]["unavailable_reason"] == "missing_input"

    def test_a_genuine_zero_is_not_warned_about(self, tmp_path):
        dataset, config = dataset_for(tmp_path)
        report = validate(dataset, config, "2025-02")
        # Services is 0 in February, which is data, not absence.
        blanks = [i for i in report.issues if i.code == "blank_not_zero"]
        assert blanks == []


class TestRequestedPeriod:
    def test_a_period_outside_the_source_is_fatal(self, tmp_path):
        dataset, config = dataset_for(tmp_path)
        report = validate(dataset, config, "2030-01")
        assert "period_not_in_source" in codes(report, ERROR)
        assert len(report.fatal) >= 1

    def test_a_fatal_error_blocks_every_metric(self, tmp_path):
        dataset, config = dataset_for(tmp_path)
        doc = build_metrics(dataset, config, "2030-01")
        assert all(x["value"] is None for x in doc["metrics"])
        assert all(x["validation_status"] == "blocked" for x in doc["metrics"])


class TestSignConventions:
    def test_negative_expense_lines_are_flagged_not_reclassified(self, abc_dataset,
                                                                 abc_config):
        report = validate(abc_dataset, abc_config, "2026-06")
        assert "negative_opex_line" in codes(report, WARNING)
        issue = next(i for i in report.issues if i.code == "negative_opex_line")
        labels = {r["label"] for r in issue.details["rows"]}
        # Bad Debts is -1,806.24 and Interest Income is -1,329.64 in Jun 2026. Both stay
        # inside operating expenses with their sign.
        assert "Bad Debts" in labels
        assert "Interest Income" in labels

    def test_the_warning_is_scoped_to_the_section_it_came_from(self, abc_dataset,
                                                               abc_config):
        """A negative operating expense says nothing about cost of goods sold.

        Bad Debts and Interest Income are opex lines. Attaching their warning to every
        `cogs.*` metric would put a misleading note on figures it has no bearing on.
        """
        report = validate(abc_dataset, abc_config, "2026-06")
        issue = next(i for i in report.issues if i.code == "negative_opex_line")

        # Named metrics, not a prefix. "opex." would tag all ~270 opex metrics, putting
        # a note about Bad Debts onto opex.line.travel.
        assert "opex." not in issue.affects
        assert all("." in a for a in issue.affects)

        def tagged(name):
            return any(i.code == "negative_opex_line"
                       for i in report.for_metric(name))

        # The lines themselves, their category, their group, and the section total.
        for affected in ("opex.line.bad_debts", "opex.line.bad_debts.mom_abs",
                         "opex.line.interest_income",
                         "opex.category.finance_tax_non_cash",
                         "opex.category.finance_tax_non_cash.ttm",
                         "opex.group.ungrouped",
                         "opex.total", "opex.total.mom_abs", "opex.total.ttm"):
            assert tagged(affected), f"{affected} should carry the warning"

        # Sibling lines, other categories and groups, and other sections must be clean.
        for untouched in ("opex.line.travel", "opex.line.rent",
                          "opex.category.payroll_people_costs",
                          "opex.group.s_and_m_payroll",
                          "cogs.total", "cogs.line.hosting", "revenue.total"):
            assert not tagged(untouched), f"{untouched} should not carry the warning"

    def test_the_warning_reaches_a_bounded_number_of_metrics(self, abc_dataset,
                                                             abc_config):
        """Guards the regression directly: it used to reach all 270 opex metrics."""
        from finance_engine.metrics import build_metrics
        doc = build_metrics(abc_dataset, abc_config, "2026-06")
        issue = next(i for i in doc["data_quality_issues"]
                     if i["code"] == "negative_opex_line")
        carrying = [x["name"] for x in doc["metrics"]
                    if issue["message"] in x["warnings"]]
        opex_metrics = [x for x in doc["metrics"] if x["name"].startswith("opex.")]
        assert len(opex_metrics) > 200          # the pool it could have hit
        assert len(carrying) == 18, sorted(carrying)
        assert "opex.line.travel" not in carrying

    def test_abc_raises_no_negative_cogs_warning(self, abc_dataset, abc_config):
        # No COGS line is negative in Jun 2026, so the cogs variant must not appear.
        report = validate(abc_dataset, abc_config, "2026-06")
        assert "negative_cogs_line" not in codes(report)

    def test_a_negative_cogs_line_warns_separately(self, tmp_path):
        """Both variants exist independently and carry their own scope."""
        from tests.helpers import SMALL_MATERIALS
        negative_materials = list(SMALL_MATERIALS)
        negative_materials[1] = -40.0
        workbook = build_small_workbook(tmp_path / "tiny.xlsx")
        config = ClientConfig.load_path(write_small_config(tmp_path))
        dataset = ExcelSource(workbook).load(config)
        # Flip the COGS line negative on the loaded dataset rather than regenerating.
        dataset.income_statement.by_label("Materials").cells["2025-02"].value = -40.0
        report = validate(dataset, config, "2025-02")
        assert "negative_cogs_line" in codes(report, WARNING)
        issue = next(i for i in report.issues if i.code == "negative_cogs_line")
        assert "cogs." not in issue.affects
        assert set(issue.affects) == {
            "cogs.line.materials", "cogs.line.materials.mom_abs",
            "cogs.line.materials.mom_pct",
            "cogs.total", "cogs.total.mom_abs", "cogs.total.mom_pct", "cogs.total.ttm",
        }
        assert [i.code for i in report.for_metric("opex.total")
                if i.code == "negative_cogs_line"] == []

    def test_a_negative_expense_keeps_its_sign_in_the_metrics(self, abc_metric_map):
        assert abc_metric_map["opex.line.bad_debts"]["value"] == pytest.approx(-1806.24)


class TestBalanceSheetChecks:
    def test_absent_balance_sheet_is_warned_about(self, tmp_path):
        dataset, config = dataset_for(tmp_path)
        assert dataset.balance_sheet is None
        report = validate(dataset, config, "2025-02")
        assert "no_balance_sheet" in codes(report, WARNING)

    def test_balance_sheet_metrics_report_unavailable_not_zero(self, tmp_path):
        dataset, config = dataset_for(tmp_path)
        doc = build_metrics(dataset, config, "2025-02")
        m = {x["name"]: x for x in doc["metrics"]}
        assert "balance_sheet.cash" not in m or m["balance_sheet.cash"]["value"] is None

    def test_abc_has_a_balance_sheet_and_raises_no_mismatch(self, abc_dataset,
                                                            abc_config):
        report = validate(abc_dataset, abc_config, "2026-06")
        assert "no_balance_sheet" not in codes(report)
        assert "statement_period_mismatch" not in codes(report)


class TestAbcValidationState:
    def test_the_reporting_month_has_no_errors(self, abc_metrics):
        errors = [i for i in abc_metrics["data_quality_issues"]
                  if i["severity"] == "error"]
        assert errors == []

    def test_no_metric_is_blocked_for_june_2026(self, abc_metrics):
        blocked = [m["name"] for m in abc_metrics["metrics"]
                   if m["validation_status"] == "blocked"]
        assert blocked == []

    def test_an_early_month_blocks_ttm_only(self, abc_dataset, abc_config):
        doc = build_metrics(abc_dataset, abc_config, "2025-06")
        blocked = {m["name"] for m in doc["metrics"]
                   if m["validation_status"] == "blocked"}
        assert blocked, "an incomplete TTM window should block something"
        assert all(name.endswith(".ttm") for name in blocked), sorted(blocked)[:5]

    def test_december_2025_is_the_earliest_complete_ttm(self, abc_dataset, abc_config):
        # The source starts at 2025-01, so a 12-month window first closes at 2025-12.
        assert build_metrics(abc_dataset, abc_config, "2025-12")["ttm"]["complete"]
        assert not build_metrics(abc_dataset, abc_config, "2025-11")["ttm"]["complete"]
