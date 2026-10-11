"""ABC Inc: the engine's totals against the workbook's own formula totals, every month.

This is the central correctness test for the real data. The engine sums line items itself;
the workbook computed its totals with its own SUM() formulas. If the client config pointed
at the wrong rows, these two would diverge.

The expected values below were read directly out of the workbook's cached formula results,
not produced by the engine.
"""

from __future__ import annotations

import pytest

from finance_engine.metrics import build_metrics, sum_present
from finance_engine.reconciliation import reconcile

ALL_PERIODS = [
    "2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06",
    "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
]

# Read from the workbook's own formula cells (Income Statement rows 14, 24, 103, 105),
# newest column C through oldest column T.
WORKBOOK_TOTAL_INCOME = {
    "2026-06": 195898.16, "2026-05": 208350.96, "2026-04": 204512.41999999998,
    "2026-03": 191468.19, "2026-02": 183160.30, "2026-01": 204153.96000000002,
    "2025-12": 190029.49, "2025-11": 187800.63, "2025-10": 191426.63,
    "2025-09": 181508.47, "2025-08": 181747.20, "2025-07": 183919.09999999998,
    "2025-06": 190555.65, "2025-05": 169403.74, "2025-04": 154136.03,
    "2025-03": 170550.65, "2025-02": 176651.19, "2025-01": 167587.50,
}
WORKBOOK_TOTAL_COGS_JUN_2026 = 58472.439999999995
WORKBOOK_TOTAL_OPEX_JUN_2026 = 468181.79999999993
WORKBOOK_GROSS_PROFIT_JUN_2026 = 137425.72
WORKBOOK_OPERATING_INCOME_JUN_2026 = -330756.07999999996
WORKBOOK_TOTAL_ASSETS_JUN_2026 = 4191851.55
WORKBOOK_TOTAL_LIAB_EQUITY_JUN_2026 = 4191851.549999999

TOLERANCE = 0.01


@pytest.fixture(scope="module")
def abc_reconciliation(abc_dataset, abc_config):
    return reconcile(abc_dataset, abc_config)


class TestReconciliationPasses:
    def test_every_check_passes_for_every_period(self, abc_reconciliation):
        failures = [
            f"{f.key} {f.period}: computed {f.computed} vs reported {f.reported}"
            for f in abc_reconciliation.failures
        ]
        assert failures == []

    def test_the_expected_number_of_checks_ran(self, abc_reconciliation):
        summary = abc_reconciliation.summary()
        # 18 months x (revenue + cogs + opex + gross profit + operating income) = 90,
        # plus 2 subtotal groups x 18 = 36, plus 18 balance-sheet checks = 144.
        assert summary["checks_run"] == 144
        assert summary["checks_failed"] == 0
        assert summary["all_passed"] is True

    def test_each_check_covered_all_eighteen_months(self, abc_reconciliation):
        for entry in abc_reconciliation.summary()["by_check"]:
            expected = 36 if entry["key"] == "opex_group_subtotals" else 18
            assert entry["periods_checked"] == expected, entry["key"]

    def test_differences_are_float_noise_not_real_gaps(self, abc_reconciliation):
        # The largest disagreement anywhere should be far below a cent — it exists only
        # because the workbook's cached SUM() results carry IEEE-754 error.
        worst = max(abs(r.difference) for r in abc_reconciliation.results
                    if r.difference is not None)
        assert worst < 1e-6, f"largest difference {worst}"

    def test_all_seven_configured_checks_actually_ran(self, abc_reconciliation):
        keys = {entry["key"] for entry in abc_reconciliation.summary()["by_check"]}
        assert keys == {
            "revenue_items_vs_total", "cogs_items_vs_total", "opex_items_vs_total",
            "opex_group_subtotals", "gross_profit", "operating_income",
            "balance_sheet_balances",
        }


class TestAgainstWorkbookLiterals:
    @pytest.mark.parametrize("period", ALL_PERIODS)
    def test_summed_revenue_matches_the_workbook_total_income(self, abc_dataset, period):
        """All 18 months, against values transcribed from the workbook's own cells."""
        items = abc_dataset.income_statement.in_section("revenue")
        computed, _, _ = sum_present(items, period)
        assert computed == pytest.approx(WORKBOOK_TOTAL_INCOME[period], abs=TOLERANCE)

    @pytest.mark.parametrize("period", ALL_PERIODS)
    def test_the_engine_reads_the_same_total_the_workbook_cached(self, abc_dataset,
                                                                 period):
        total_row = abc_dataset.income_statement.section_total("revenue")
        assert total_row.value(period) == pytest.approx(
            WORKBOOK_TOTAL_INCOME[period], abs=1e-9
        )

    def test_cogs_june_2026(self, abc_dataset):
        items = abc_dataset.income_statement.in_section("cogs")
        computed, _, _ = sum_present(items, "2026-06")
        assert computed == pytest.approx(WORKBOOK_TOTAL_COGS_JUN_2026, abs=TOLERANCE)

    def test_opex_june_2026_direct_plus_group_subtotals(self, abc_dataset):
        """Mirrors the workbook formula =(SUM(C29:C46,C72:C102) + (C59 + C71))."""
        sheet = abc_dataset.income_statement
        direct, _, _ = sum_present(sheet.direct_items("opex"), "2026-06")
        groups, _, _ = sum_present(sheet.group_totals("opex"), "2026-06")
        assert direct + groups == pytest.approx(WORKBOOK_TOTAL_OPEX_JUN_2026,
                                                abs=TOLERANCE)

    def test_opex_june_2026_all_leaves_give_the_same_answer(self, abc_dataset):
        """Summing every leaf once must equal direct + subtotals. No double counting."""
        sheet = abc_dataset.income_statement
        leaves, _, _ = sum_present(sheet.in_section("opex"), "2026-06")
        assert leaves == pytest.approx(WORKBOOK_TOTAL_OPEX_JUN_2026, abs=TOLERANCE)

    def test_group_subtotals_june_2026(self, abc_dataset):
        sheet = abc_dataset.income_statement
        s_and_m, _, _ = sum_present(sheet.group_members("s_and_m_payroll"), "2026-06")
        marketing, _, _ = sum_present(sheet.group_members("marketing"), "2026-06")
        # Workbook rows 59 and 71.
        assert s_and_m == pytest.approx(126575.18999999999, abs=TOLERANCE)
        assert marketing == pytest.approx(49392.69999999999, abs=TOLERANCE)

    def test_gross_profit_june_2026(self, abc_metric_map):
        assert abc_metric_map["gross_profit"]["value"] == pytest.approx(
            WORKBOOK_GROSS_PROFIT_JUN_2026, abs=TOLERANCE
        )

    def test_operating_income_june_2026(self, abc_metric_map):
        assert abc_metric_map["operating_income"]["value"] == pytest.approx(
            WORKBOOK_OPERATING_INCOME_JUN_2026, abs=TOLERANCE
        )

    def test_balance_sheet_balances_june_2026(self, abc_metric_map):
        assets = abc_metric_map["balance_sheet.assets"]["value"]
        liab_eq = abc_metric_map["balance_sheet.liabilities_and_equity"]["value"]
        assert assets == pytest.approx(WORKBOOK_TOTAL_ASSETS_JUN_2026, abs=TOLERANCE)
        assert liab_eq == pytest.approx(WORKBOOK_TOTAL_LIAB_EQUITY_JUN_2026,
                                        abs=TOLERANCE)
        assert assets == pytest.approx(liab_eq, abs=TOLERANCE)


class TestClassificationNote:
    def test_operating_income_equals_net_income_in_this_workbook(self, abc_metric_map):
        """The documented consequence of interest and tax sitting inside opex."""
        operating = abc_metric_map["operating_income"]["value"]
        net = abc_metric_map["net_income.reported"]["value"]
        assert operating == pytest.approx(net, abs=TOLERANCE)

    def test_the_note_is_reported_not_silently_corrected(self, abc_metrics):
        notes = " ".join(abc_metrics["classification_notes"]).lower()
        assert "operating income equals its net income" in notes
        assert "interest" in notes

    def test_the_classification_items_are_still_inside_opex(self, abc_dataset):
        labels = {i.label for i in abc_dataset.income_statement.in_section("opex")}
        for label in ("Interest Income", "Interest Expense", "Bad Debts",
                      "Income Tax Benefit"):
            assert label in labels, f"{label} was reclassified out of opex"


class TestTtmAcrossPeriods:
    @pytest.mark.parametrize("period,complete", [
        ("2026-06", True), ("2026-01", True), ("2025-12", True),
        ("2025-11", False), ("2025-06", False), ("2025-01", False),
    ])
    def test_only_december_2025_onward_has_a_complete_window(self, abc_dataset,
                                                             abc_config, period,
                                                             complete):
        doc = build_metrics(abc_dataset, abc_config, period)
        assert doc["ttm"]["complete"] is complete

    def test_ttm_revenue_equals_the_sum_of_its_twelve_months(self, abc_dataset,
                                                             abc_config):
        doc = build_metrics(abc_dataset, abc_config, "2026-06")
        m = {x["name"]: x for x in doc["metrics"]}
        window = doc["ttm"]["window"]
        # Summed independently here from the workbook's own monthly totals.
        expected = sum(WORKBOOK_TOTAL_INCOME[p] for p in window)
        assert m["revenue.total.ttm"]["value"] == pytest.approx(expected, abs=TOLERANCE)
