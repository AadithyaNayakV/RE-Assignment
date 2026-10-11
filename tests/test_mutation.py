"""Mutation check: break the engine on purpose and confirm something notices.

A passing test suite only means something if it would fail when the code is wrong. Each
test here injects one deliberate fault, runs the check that should catch it, and asserts
that the check *fails*. A mutation that survives is a hole in the suite.

The faults are chosen to be the plausible ones: an off-by-a-cent total, a double-counted
subtotal group, a percentage computed against the wrong base, a TTM over a short window,
a zero substituted for missing data, and a guard that waves everything through.

Run just these with:

    python -m pytest -q -m mutation
"""

from __future__ import annotations

import json

import pytest

import finance_engine.guard as guard_module
import finance_engine.metrics as metrics_module
import finance_engine.reconciliation as reconciliation_module
from finance_engine.config import ClientConfig
from finance_engine.metrics import build_metrics
from finance_engine.reconciliation import reconcile
from finance_engine.sources.excel import ExcelSource
from tests.helpers import build_small_workbook, write_small_config

pytestmark = pytest.mark.mutation


@pytest.fixture
def tiny(tmp_path):
    workbook = build_small_workbook(tmp_path / "tiny.xlsx")
    config = ClientConfig.load_path(write_small_config(tmp_path))
    return ExcelSource(workbook).load(config), config


class TestReconciliationCatchesBadSums:
    def test_baseline_reconciles(self, abc_dataset, abc_config):
        """Control: without a mutation the real workbook reconciles cleanly."""
        assert reconcile(abc_dataset, abc_config).passed

    def test_a_one_cent_error_in_every_sum_is_caught(self, abc_dataset, abc_config,
                                                     monkeypatch):
        """The tolerance absorbs float noise, not a real discrepancy."""
        original = reconciliation_module._sum_present

        def off_by_a_cent(items, period):
            total, present, missing = original(items, period)
            return (None if total is None else total + 0.02), present, missing

        monkeypatch.setattr(reconciliation_module, "_sum_present", off_by_a_cent)
        report = reconcile(abc_dataset, abc_config)
        assert not report.passed, "a 2-cent error survived reconciliation"
        assert len(report.failures) > 0

    def test_a_dropped_line_item_is_caught(self, abc_dataset, abc_config, monkeypatch):
        original = reconciliation_module._sum_present

        def drop_one(items, period):
            return original(items[:-1] if len(items) > 1 else items, period)

        monkeypatch.setattr(reconciliation_module, "_sum_present", drop_one)
        assert not reconcile(abc_dataset, abc_config).passed, \
            "dropping a line item survived reconciliation"

    def test_double_counting_a_subtotal_group_is_caught(self, abc_dataset, abc_config,
                                                        monkeypatch):
        """The mistake the config is designed to prevent: adding members *and* subtotal."""
        original = reconciliation_module._section_check

        def count_members_and_subtotals(report, sheet, config, section, key,
                                        description, period, tol, grouped):
            if section == "opex":
                # Sum every leaf *and* the group subtotal rows: the classic double count.
                parts = sheet.in_section(section) + sheet.group_totals(section)
                computed, present, missing = reconciliation_module._sum_present(
                    parts, period
                )
                total_row = sheet.section_total(section)
                reconciliation_module._record(
                    report, key, description, period, computed,
                    total_row.value(period), tol
                )
                return
            original(report, sheet, config, section, key, description, period, tol,
                     grouped)

        monkeypatch.setattr(reconciliation_module, "_section_check",
                            count_members_and_subtotals)
        report = reconcile(abc_dataset, abc_config)
        assert not report.passed, "double-counting the subtotal groups survived"
        assert "opex_items_vs_total" in report.failed_keys()

    def test_a_loosened_tolerance_would_hide_a_real_error(self, abc_dataset, abc_config,
                                                          monkeypatch):
        """Shows the tolerance is doing real work, not rubber-stamping."""
        original = reconciliation_module._sum_present

        def off_by_a_lot(items, period):
            total, present, missing = original(items, period)
            return (None if total is None else total + 500.0), present, missing

        monkeypatch.setattr(reconciliation_module, "_sum_present", off_by_a_lot)
        monkeypatch.setattr(type(abc_config), "tolerance_abs",
                            property(lambda self: 1000.0))
        assert reconcile(abc_dataset, abc_config).passed
        # ...and with the real tolerance the same fault is caught.
        monkeypatch.setattr(type(abc_config), "tolerance_abs",
                            property(lambda self: 0.01))
        assert not reconcile(abc_dataset, abc_config).passed


class TestHandCalcsCatchBadMetrics:
    """The hand-calculated expectations must reject a wrong calculation."""

    def test_baseline_matches_the_hand_calculation(self, tiny):
        dataset, config = tiny
        m = {x["name"]: x for x in build_metrics(dataset, config, "2025-02")["metrics"]}
        assert m["revenue.total"]["value"] == 200.0

    def test_an_inflated_total_fails_the_hand_calculation(self, tiny, monkeypatch):
        original = metrics_module.sum_present

        def inflate(items, period):
            total, traces, missing = original(items, period)
            return (None if total is None else total * 1.01), traces, missing

        monkeypatch.setattr(metrics_module, "sum_present", inflate)
        dataset, config = tiny
        m = {x["name"]: x for x in build_metrics(dataset, config, "2025-02")["metrics"]}
        # Hand-calculated: Widgets 200 + Services 0 = 200 exactly.
        assert m["revenue.total"]["value"] != 200.0, "a 1% inflation went unnoticed"

    def test_a_percentage_against_the_wrong_base_fails(self, tiny, monkeypatch):
        def wrong_base(current, prior):
            # Divides by the current value instead of the prior one.
            if current is None or prior is None:
                return None, "missing_input"
            if current == 0:
                return None, "zero_base"
            return (current - prior) / current * 100.0, None

        monkeypatch.setattr(metrics_module, "pct_change", wrong_base)
        dataset, config = tiny
        m = {x["name"]: x for x in build_metrics(dataset, config, "2025-02")["metrics"]}
        # Hand-calculated: (200 - 150) / 150 * 100 = 33.333...
        assert m["revenue.total.mom_pct"]["value"] != pytest.approx(100.0 / 3.0), \
            "a wrong percentage base went unnoticed"

    def test_a_margin_against_the_wrong_denominator_fails(self, tiny, monkeypatch):
        def wrong_denominator(numerator, denominator):
            if numerator is None or denominator is None:
                return None, "missing_input"
            # Uses the numerator as its own denominator.
            if numerator == 0:
                return None, "zero_base"
            return denominator / numerator * 100.0, None

        monkeypatch.setattr(metrics_module, "ratio_pct", wrong_denominator)
        dataset, config = tiny
        m = {x["name"]: x for x in build_metrics(dataset, config, "2025-02")["metrics"]}
        # Hand-calculated: 160 / 200 * 100 = 80.0
        assert m["gross_margin_pct"]["value"] != pytest.approx(80.0), \
            "an inverted margin went unnoticed"

    def test_treating_missing_as_zero_fails(self, tmp_path, monkeypatch):
        """The rule most likely to be broken by a well-meaning change.

        Patches `LineItem.value` rather than `sum_present`, because that is where a blank
        cell would actually get coerced — a blank line's metric reads the cell directly.
        """
        from finance_engine.model import LineItem

        workbook = build_small_workbook(tmp_path / "tiny.xlsx",
                                        blank_cells=[("Marketing spend", 1)])
        config = ClientConfig.load_path(write_small_config(tmp_path))
        dataset = ExcelSource(workbook).load(config)

        honest = {x["name"]: x for x in
                  build_metrics(dataset, config, "2025-02")["metrics"]}
        assert honest["opex.line.marketing_spend"]["value"] is None
        assert honest["opex.total"]["value"] == 35.0      # rent 10 + salaries 25

        original_value = LineItem.value

        def blank_becomes_zero(self, period):
            result = original_value(self, period)
            return 0.0 if result is None else result

        monkeypatch.setattr(LineItem, "value", blank_becomes_zero)
        mutated = {x["name"]: x for x in
                   build_metrics(dataset, config, "2025-02")["metrics"]}
        assert mutated["opex.line.marketing_spend"]["value"] == 0.0, \
            "mutation did not take effect"
        # A blank month would now be indistinguishable from a genuine zero, which is
        # exactly what tests/test_validation.py asserts must never happen.
        assert (mutated["opex.line.marketing_spend"]["value"]
                != honest["opex.line.marketing_spend"]["value"])


class TestTtmGuardrailCatchesShortWindows:
    def test_the_window_rule_has_two_independent_guardrails(self, tiny, monkeypatch):
        """Defeating only the builder's flag is not enough — validation still blocks.

        Discovered by this mutation test: patching `ttm_complete` alone leaves the TTM
        metrics at `None`, because `_check_ttm_completeness` independently raises an error
        matching `*.ttm`. Both have to be defeated to produce a wrong number, which is
        the defence-in-depth the design intends.
        """
        dataset, config = tiny
        original_init = metrics_module.MetricsBuilder.__init__

        def lie_about_completeness(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.ttm_complete = True
            self.ttm_window = self.sheet.period_keys()

        monkeypatch.setattr(metrics_module.MetricsBuilder, "__init__",
                            lie_about_completeness)
        doc = build_metrics(dataset, config, "2025-03")
        m = {x["name"]: x for x in doc["metrics"]}
        assert doc["ttm"]["complete"] is True            # the lie took hold
        assert m["revenue.total.ttm"]["value"] is None   # ...and validation still blocked
        assert m["revenue.total.ttm"]["validation_status"] == "blocked"

    def test_defeating_both_guardrails_produces_a_detectably_wrong_figure(
            self, tiny, monkeypatch):
        dataset, config = tiny
        original_init = metrics_module.MetricsBuilder.__init__

        def lie_about_completeness(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.ttm_complete = True
            self.ttm_window = self.sheet.period_keys()
            # Second guardrail: drop the validation error that blocks *.ttm.
            self.val.issues = [i for i in self.val.issues
                               if i.code != "ttm_window_incomplete"]

        monkeypatch.setattr(metrics_module.MetricsBuilder, "__init__",
                            lie_about_completeness)
        m = {x["name"]: x for x in build_metrics(dataset, config, "2025-03")["metrics"]}
        # Hand-calculated three-month revenue: 150 + 200 + 25 = 375. Presenting that as a
        # twelve-month figure is the error the suite must catch, and
        # test_metrics_handcalc.py asserts this value is None.
        assert m["revenue.total.ttm"]["value"] == 375.0, "mutation did not take effect"

        monkeypatch.undo()
        honest = build_metrics(dataset, config, "2025-03")
        honest_m = {x["name"]: x for x in honest["metrics"]}
        assert honest_m["revenue.total.ttm"]["value"] is None
        assert honest["ttm"]["complete"] is False


class TestGuardMutations:
    def test_baseline_guard_rejects_an_invented_number(self, abc_metrics):
        result = guard_module.check_commentary("Marketing was $88,500.00.", abc_metrics)
        assert not result.passed

    def test_a_guard_that_allows_everything_is_caught(self, abc_metrics, monkeypatch):
        """If the allowed set were built too broadly the guard would stop working."""
        def allow_everything(doc):
            wide = {float(v) for v in range(0, 2_000_000, 1)}
            return {guard_module.CURRENCY_BUCKET: wide,
                    guard_module.PERCENT_BUCKET: wide,
                    guard_module.STRUCTURAL_BUCKET: wide}

        monkeypatch.setattr(guard_module, "collect_allowed_values", allow_everything)
        result = guard_module.check_commentary("Marketing was $88,500.00.", abc_metrics)
        assert result.passed, "mutation did not take effect"
        # The real guard rejects it, which the real test asserts.
        monkeypatch.undo()
        assert not guard_module.check_commentary(
            "Marketing was $88,500.00.", abc_metrics
        ).passed

    def test_a_guard_that_stops_extracting_numbers_is_caught(self, abc_metrics,
                                                             monkeypatch):
        import re
        monkeypatch.setattr(guard_module, "_NUMBER", re.compile(r"(?!x)x"))
        result = guard_module.check_commentary("Marketing was $88,500.00.", abc_metrics)
        # It "passes" only because it checked nothing — which the count exposes.
        assert result.passed
        assert result.checked == 0, "mutation did not take effect"
        monkeypatch.undo()
        assert guard_module.check_commentary(
            "Marketing was $88,500.00.", abc_metrics
        ).checked == 1

    def test_a_widened_tolerance_lets_wrong_figures_through(self, abc_metrics,
                                                            monkeypatch):
        monkeypatch.setattr(guard_module, "MIN_TOLERANCE", 10_000.0)
        # $88,500.00 is within $10,000 of a real value, so it would now pass.
        assert guard_module.check_commentary(
            "Marketing was $88,500.00.", abc_metrics
        ).passed
        monkeypatch.undo()
        assert not guard_module.check_commentary(
            "Marketing was $88,500.00.", abc_metrics
        ).passed


class TestSchemaMutations:
    def test_dropping_source_rows_breaks_traceability(self, tiny, monkeypatch):
        """Every metric must keep its provenance; stripping it has to be detectable."""
        dataset, config = tiny
        doc = build_metrics(dataset, config, "2025-02")
        stripped = json.loads(json.dumps(doc))
        for metric in stripped["metrics"]:
            metric["source_rows"] = []
        total = next(m for m in doc["metrics"] if m["name"] == "revenue.total")
        stripped_total = next(m for m in stripped["metrics"]
                              if m["name"] == "revenue.total")
        assert len(total["source_rows"]) == 2
        assert stripped_total["source_rows"] == []

    def test_an_unknown_method_id_is_detectable(self, tiny):
        dataset, config = tiny
        doc = build_metrics(dataset, config, "2025-02")
        doc["metrics"][0]["method_id"] = "made_up_method_v9"
        unknown = [m["name"] for m in doc["metrics"]
                   if m["method_id"] not in doc["methods"]]
        assert unknown, "an unregistered method id went unnoticed"
