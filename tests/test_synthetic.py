"""Engine output against the synthetic workbooks' independently computed expectations.

`scripts/generate_synthetic.py` computes each workbook's expected totals from its own
inputs without importing `finance_engine`. These tests compare the engine against those
files, so a pass means two separate implementations of the same arithmetic agree.

The scenarios also prove the config-driven design: ten companies, three different sheet
layouts, one engine, zero code changes.

**No figure in this file says anything about ABC Inc.** All of it is generated test data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_engine.config import ClientConfig, Settings
from finance_engine.metrics import build_metrics
from finance_engine.reconciliation import reconcile
from finance_engine.sources.excel import ExcelSource

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DIR = PROJECT_ROOT / "data" / "synthetic" / "expected"
TOLERANCE = 0.01

SCENARIOS = [
    "company_a_normal_growth",
    "company_b_revenue_decline",
    "company_c_expense_spike",
    "company_d_margin_decline",
    "company_e_seasonal_business",
    "company_f_one_time_expense",
    "company_g_missing_data",
    "company_h_zero_negative_values",
    "company_i_multiple_revenue_streams",
    "company_j_balance_sheet",
]


def requires_generated(client_id: str) -> dict:
    path = EXPECTED_DIR / f"{client_id}.json"
    if not path.exists():
        pytest.skip(f"{path} missing — run python scripts/generate_synthetic.py")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def loaded():
    """All ten datasets, read once."""
    settings = Settings.from_env(PROJECT_ROOT)
    out = {}
    for client_id in SCENARIOS:
        expected_path = EXPECTED_DIR / f"{client_id}.json"
        if not expected_path.exists():
            continue
        config = ClientConfig.load(client_id, settings)
        workbook = PROJECT_ROOT / config.source_directory / config.source_filename
        dataset = ExcelSource(workbook).load(config)
        out[client_id] = (dataset, config,
                          json.loads(expected_path.read_text(encoding="utf-8")))
    if not out:
        pytest.skip("no synthetic workbooks generated")
    return out


def get(loaded, client_id):
    if client_id not in loaded:
        pytest.skip(f"{client_id} not generated")
    return loaded[client_id]


class TestAllScenariosLoadAndReconcile:
    @pytest.mark.parametrize("client_id", SCENARIOS)
    def test_the_workbook_loads(self, loaded, client_id):
        dataset, _, expected = get(loaded, client_id)
        assert dataset.period_keys() == expected["periods"]
        assert len(dataset.period_keys()) == expected["period_count"]

    @pytest.mark.parametrize("client_id", SCENARIOS)
    def test_reconciliation_passes_for_every_period(self, loaded, client_id):
        dataset, config, _ = get(loaded, client_id)
        report = reconcile(dataset, config)
        failures = [f"{f.key} {f.period}: {f.computed} vs {f.reported}"
                    for f in report.failures]
        assert failures == [], f"{client_id}: {failures[:3]}"

    @pytest.mark.parametrize("client_id", SCENARIOS)
    def test_every_period_matches_the_independent_totals(self, loaded, client_id):
        """The heart of it: engine arithmetic against the generator's arithmetic."""
        dataset, config, expected = get(loaded, client_id)
        sheet = dataset.income_statement
        from finance_engine.metrics import sum_present

        for period, want in expected["per_period"].items():
            revenue, _, _ = sum_present(sheet.in_section("revenue"), period)
            cogs, _, _ = sum_present(sheet.in_section("cogs"), period)
            opex, _, _ = sum_present(sheet.in_section("opex"), period)
            for label, got, target in (
                ("revenue", revenue, want["total_revenue"]),
                ("cogs", cogs, want["total_cogs"]),
                ("opex", opex, want["total_opex"]),
            ):
                if target is None:
                    assert got is None, f"{client_id} {period} {label}: expected none"
                else:
                    assert got == pytest.approx(target, abs=TOLERANCE), \
                        f"{client_id} {period} {label}"

    @pytest.mark.parametrize("client_id", SCENARIOS)
    def test_reporting_period_metrics_match(self, loaded, client_id):
        dataset, config, expected = get(loaded, client_id)
        period = expected["reporting_period"]
        m = {x["name"]: x for x in build_metrics(dataset, config, period)["metrics"]}
        want = expected["per_period"][period]
        assert m["revenue.total"]["value"] == pytest.approx(want["total_revenue"],
                                                            abs=TOLERANCE)
        assert m["cogs.total"]["value"] == pytest.approx(want["total_cogs"],
                                                         abs=TOLERANCE)
        assert m["gross_profit"]["value"] == pytest.approx(want["gross_profit"],
                                                           abs=TOLERANCE)
        assert m["opex.total"]["value"] == pytest.approx(want["total_opex"],
                                                         abs=TOLERANCE)
        assert m["operating_income"]["value"] == pytest.approx(
            want["operating_income"], abs=TOLERANCE
        )

    @pytest.mark.parametrize("client_id", SCENARIOS)
    def test_month_over_month_matches(self, loaded, client_id):
        dataset, config, expected = get(loaded, client_id)
        period = expected["reporting_period"]
        m = {x["name"]: x for x in build_metrics(dataset, config, period)["metrics"]}
        for field, name in (("total_revenue", "revenue.total"),
                            ("total_opex", "opex.total")):
            want = expected["month_over_month"][field]
            got_abs = m[f"{name}.mom_abs"]["value"]
            got_pct = m[f"{name}.mom_pct"]["value"]
            if want["abs"] is None:
                assert got_abs is None
            else:
                assert got_abs == pytest.approx(want["abs"], abs=TOLERANCE)
            if want["pct"] is None:
                assert got_pct is None, f"{client_id} {name}: expected no percentage"
                assert m[f"{name}.mom_pct"]["unavailable_reason"] is not None
            else:
                assert got_pct == pytest.approx(want["pct"], abs=1e-4)

    @pytest.mark.parametrize("client_id", SCENARIOS)
    def test_ttm_completeness_and_totals_match(self, loaded, client_id):
        dataset, config, expected = get(loaded, client_id)
        period = expected["reporting_period"]
        doc = build_metrics(dataset, config, period)
        m = {x["name"]: x for x in doc["metrics"]}
        assert doc["ttm"]["complete"] is expected["ttm"]["complete"]
        assert doc["ttm"]["window"] == expected["ttm"]["window"]
        if not expected["ttm"]["complete"]:
            assert m["revenue.total.ttm"]["value"] is None
            return
        assert m["revenue.total.ttm"]["value"] == pytest.approx(
            expected["ttm"]["totals"]["total_revenue"], abs=TOLERANCE
        )
        assert m["opex.total.ttm"]["value"] == pytest.approx(
            expected["ttm"]["totals"]["total_opex"], abs=TOLERANCE
        )

    @pytest.mark.parametrize("client_id", SCENARIOS)
    def test_group_subtotals_match(self, loaded, client_id):
        dataset, config, expected = get(loaded, client_id)
        period = expected["reporting_period"]
        m = {x["name"]: x for x in build_metrics(dataset, config, period)["metrics"]}
        from finance_engine.metrics import slug
        for group_name, want in expected["per_period"][period]["group_subtotals"].items():
            metric = m.get(f"opex.group.{slug(group_name)}")
            assert metric is not None, f"{client_id}: missing group {group_name}"
            if want is None:
                assert metric["value"] is None
            else:
                assert metric["value"] == pytest.approx(want, abs=TOLERANCE)

    @pytest.mark.parametrize("client_id", SCENARIOS)
    def test_commentary_passes_the_guard(self, loaded, client_id):
        from finance_engine.commentary.deterministic import DeterministicProvider
        from finance_engine.guard import check_commentary
        dataset, config, expected = get(loaded, client_id)
        doc = build_metrics(dataset, config, expected["reporting_period"])
        text = DeterministicProvider().generate(doc)
        result = check_commentary(text, doc)
        assert result.passed, f"{client_id}: {result.summary()}"

    @pytest.mark.parametrize("client_id", SCENARIOS)
    def test_the_workbook_is_labelled_synthetic(self, loaded, client_id):
        """Generated data must be impossible to mistake for a real client."""
        dataset, config, _ = get(loaded, client_id)
        assert "SYNTHETIC" in config.company_name
        assert config.is_synthetic is True
        headings = " ".join(dataset.income_statement.titles)
        assert "SYNTHETIC" in headings


class TestLayoutVariation:
    """Different sheet layouts, handled by config alone."""

    def test_oldest_first_layouts_are_read_correctly(self, loaded):
        for client_id in ("company_f_one_time_expense", "company_g_missing_data"):
            dataset, config, expected = get(loaded, client_id)
            assert config.sheet_spec("income_statement")["date_order"] == "oldest_first"
            # Normalised to oldest-first regardless of how the file was written.
            assert dataset.period_keys() == sorted(dataset.period_keys())
            assert dataset.period_keys() == expected["periods"]

    def test_a_shifted_layout_is_read_correctly(self, loaded):
        dataset, config, expected = get(loaded, "company_h_zero_negative_values")
        spec = config.sheet_spec("income_statement")
        assert spec["name"] == "Profit and Loss"
        assert spec["header_row"] == 7
        assert spec["label_columns"] == ["C", "D"]
        assert spec["first_data_column"] == "F"
        assert spec["date_formats"] == ["%b-%y"]
        assert dataset.period_keys() == expected["periods"]

    def test_three_distinct_layouts_are_covered(self, loaded):
        shapes = set()
        for client_id in SCENARIOS:
            if client_id not in loaded:
                continue
            _, config, _ = get(loaded, client_id)
            spec = config.sheet_spec("income_statement")
            shapes.add((spec["header_row"], spec["first_data_column"],
                        spec["date_order"], tuple(spec["label_columns"])))
        assert len(shapes) >= 3, f"only {len(shapes)} layout shape(s) exercised"


class TestScenarioBehaviour:
    """Each workbook exists to exercise one behaviour. These assert it actually does."""

    def test_growth_scenario_shows_rising_revenue(self, loaded):
        dataset, config, expected = get(loaded, "company_a_normal_growth")
        per = expected["per_period"]
        keys = expected["periods"]
        assert per[keys[-1]]["total_revenue"] > per[keys[0]]["total_revenue"]
        m = {x["name"]: x for x in
             build_metrics(dataset, config, keys[-1])["metrics"]}
        assert m["revenue.total.mom_abs"]["value"] > 0

    def test_decline_scenario_shows_falling_revenue(self, loaded):
        dataset, config, expected = get(loaded, "company_b_revenue_decline")
        keys = expected["periods"]
        m = {x["name"]: x for x in
             build_metrics(dataset, config, keys[-1])["metrics"]}
        assert m["revenue.total.mom_abs"]["value"] < 0
        assert m["revenue.total.mom_pct"]["value"] < 0

    def test_expense_spike_is_flagged_as_a_significant_movement(self, loaded):
        dataset, config, expected = get(loaded, "company_c_expense_spike")
        # The jump happens at index 12, which is 2026-01.
        doc = build_metrics(dataset, config, "2026-01")
        labels = {c["label"] for c in doc["movements"]["significant"]}
        assert "Legal Expenses" in labels

    def test_margin_decline_is_visible_in_the_margin_metric(self, loaded):
        dataset, config, expected = get(loaded, "company_d_margin_decline")
        keys = expected["periods"]
        first = build_metrics(dataset, config, keys[1])
        last = build_metrics(dataset, config, keys[-1])
        first_margin = {x["name"]: x for x in first["metrics"]}["gross_margin_pct"]
        last_margin = {x["name"]: x for x in last["metrics"]}["gross_margin_pct"]
        assert last_margin["value"] < first_margin["value"]

    def test_seasonal_scenario_peaks_in_the_expected_months(self, loaded):
        _, _, expected = get(loaded, "company_e_seasonal_business")
        per = expected["per_period"]
        december = per["2025-12"]["total_revenue"]
        september = per["2025-09"]["total_revenue"]
        assert december > september * 1.5

    def test_seasonality_is_still_not_asserted_without_two_full_years(self, loaded):
        dataset, config, expected = get(loaded, "company_e_seasonal_business")
        doc = build_metrics(dataset, config, expected["reporting_period"])
        # The data is seasonal by construction, but 18 months is not enough to claim it.
        assert doc["seasonality"]["assessed"] is False
        assert doc["seasonality"]["months_available"] == 18

    def test_one_time_expense_is_flagged_as_a_possible_one_off(self, loaded):
        dataset, config, expected = get(loaded, "company_f_one_time_expense")
        # The settlement lands at index 10, which is 2025-11.
        doc = build_metrics(dataset, config, "2025-11")
        labels = {c["label"] for c in doc["movements"]["possible_one_offs"]}
        assert "Legal Settlement" in labels
        entry = next(c for c in doc["movements"]["possible_one_offs"]
                     if c["label"] == "Legal Settlement")
        assert "possible one-off" in entry["assessment"]
        assert "no information about the cause" in entry["assessment"]

    def test_a_one_off_on_a_normally_zero_line_reports_no_multiple(self, loaded):
        """The clearest one-off has no multiple to report, and must not claim one.

        Legal Settlement is zero in every other month, so its trailing average is zero.
        An earlier version skipped such lines entirely for want of a positive
        denominator, which missed the most obvious case.
        """
        from finance_engine.commentary.deterministic import DeterministicProvider
        from finance_engine.guard import check_commentary
        from finance_engine.report import build_report

        dataset, config, _ = get(loaded, "company_f_one_time_expense")
        doc = build_metrics(dataset, config, "2025-11")
        entry = next(c for c in doc["movements"]["possible_one_offs"]
                     if c["label"] == "Legal Settlement")
        assert entry["comparison_average"] == 0.0
        assert entry["multiple_of_average"] is None
        assert entry["multiple_unavailable_reason"] == "zero_base"
        assert entry["excess_over_average"] == pytest.approx(285000.0)

        # Both renderers must cope with the absent multiple rather than crash.
        text = DeterministicProvider().generate(doc)
        assert "no comparable spend in the trailing window" in text
        guard = check_commentary(text, doc)
        assert guard.passed, guard.summary()
        report = build_report(doc, text, "deterministic", guard.to_dict())
        assert "Legal Settlement" in report
        assert "n/a" in report.split("## Possible one-offs", 1)[1][:800]

    def test_missing_data_scenario_reports_the_gaps(self, loaded):
        dataset, config, expected = get(loaded, "company_g_missing_data")
        assert "2025-04" not in dataset.period_keys()
        assert "2025-09" not in dataset.period_keys()
        doc = build_metrics(dataset, config, expected["reporting_period"])
        codes = {i["code"] for i in doc["data_quality_issues"]}
        assert "missing_months" in codes
        issue = next(i for i in doc["data_quality_issues"]
                     if i["code"] == "missing_months")
        assert set(issue["details"]["missing"]) == {"2025-04", "2025-09"}

    def test_missing_data_blocks_ttm_rather_than_shortening_it(self, loaded):
        dataset, config, expected = get(loaded, "company_g_missing_data")
        doc = build_metrics(dataset, config, expected["reporting_period"])
        m = {x["name"]: x for x in doc["metrics"]}
        assert doc["ttm"]["complete"] is False
        assert m["revenue.total.ttm"]["value"] is None
        assert m["revenue.total.ttm"]["unavailable_reason"] == "incomplete_window"

    def test_a_blank_cell_is_not_read_as_zero(self, loaded):
        dataset, config, expected = get(loaded, "company_g_missing_data")
        services = dataset.income_statement.by_label("Services Revenue")
        blanks = [p for p in dataset.period_keys() if services.value(p) is None]
        assert len(blanks) == 1, "the generated blank cell was not preserved"

    def test_zero_base_percentages_are_withheld(self, loaded):
        dataset, config, expected = get(loaded, "company_h_zero_negative_values")
        # New Product Revenue is zero for the first nine months, then positive.
        doc = build_metrics(dataset, config, "2025-10")
        m = {x["name"]: x for x in doc["metrics"]}
        pct = m["revenue.stream.new_product_revenue.mom_pct"]
        assert pct["value"] is None
        assert pct["unavailable_reason"] == "zero_base"
        assert m["revenue.stream.new_product_revenue.mom_abs"]["value"] > 0

    def test_negative_lines_keep_their_sign(self, loaded):
        dataset, config, expected = get(loaded, "company_h_zero_negative_values")
        doc = build_metrics(dataset, config, expected["reporting_period"])
        m = {x["name"]: x for x in doc["metrics"]}
        assert m["opex.line.interest_income"]["value"] == pytest.approx(-650.0)
        codes = {i["code"] for i in doc["data_quality_issues"]}
        assert "negative_opex_line" in codes
        assert "negative_revenue_line" in codes
        # Each warning is scoped to its own section, so a negative opex line does not
        # put a note on the revenue or COGS metrics.
        opex_warning = next(i for i in doc["data_quality_issues"]
                            if i["code"] == "negative_opex_line")
        # Named metrics, not a blanket "opex." prefix.
        assert "opex." not in opex_warning["affects"]
        assert "opex.total" in opex_warning["affects"]
        assert "opex.line.interest_income" in opex_warning["affects"]
        assert m["cogs.total"]["warnings"] == []

    def test_negative_base_percentages_are_withheld(self, loaded):
        dataset, config, expected = get(loaded, "company_h_zero_negative_values")
        # Bad Debts is -1,500 in Feb 2025, so the March comparison has a negative base.
        doc = build_metrics(dataset, config, "2025-03")
        m = {x["name"]: x for x in doc["metrics"]}
        pct = m["opex.line.bad_debts.mom_pct"]
        assert pct["value"] is None
        assert pct["unavailable_reason"] == "negative_base"

    def test_many_streams_all_appear(self, loaded):
        dataset, config, expected = get(loaded, "company_i_multiple_revenue_streams")
        doc = build_metrics(dataset, config, expected["reporting_period"])
        streams = [x for x in doc["metrics"]
                   if x["name"].startswith("revenue.stream.")
                   and x["name"].count(".") == 2]
        assert len(streams) == 6

    def test_two_subtotal_groups_are_both_handled(self, loaded):
        dataset, config, expected = get(loaded, "company_i_multiple_revenue_streams")
        doc = build_metrics(dataset, config, expected["reporting_period"])
        m = {x["name"]: x for x in doc["metrics"]}
        assert m["opex.group.s_m_payroll"]["value"] is not None
        assert m["opex.group.marketing"]["value"] is not None
        # The section total must still equal direct items plus both subtotals.
        report = reconcile(dataset, config)
        assert "opex_items_vs_total" not in report.failed_keys()

    def test_balance_sheet_scenario_balances(self, loaded):
        dataset, config, expected = get(loaded, "company_j_balance_sheet")
        assert dataset.balance_sheet is not None
        doc = build_metrics(dataset, config, expected["reporting_period"])
        m = {x["name"]: x for x in doc["metrics"]}
        assets = m["balance_sheet.assets"]["value"]
        liab_eq = m["balance_sheet.liabilities_and_equity"]["value"]
        assert assets == pytest.approx(liab_eq, abs=TOLERANCE)
        report = reconcile(dataset, config)
        assert "balance_sheet_balances" not in report.failed_keys()

    def test_balance_sheet_trend_lines_are_picked_up(self, loaded):
        dataset, config, expected = get(loaded, "company_j_balance_sheet")
        doc = build_metrics(dataset, config, expected["reporting_period"])
        m = {x["name"]: x for x in doc["metrics"]}
        assert m["balance_sheet.deferred_revenue"]["value"] is not None
        assert m["balance_sheet.accounts_receivable"]["value"] is not None
        assert m["balance_sheet.deferred_revenue.mom_abs"]["value"] is not None


class TestIsolation:
    def test_synthetic_configs_are_marked_and_separate_from_abc(self, loaded):
        for client_id in SCENARIOS:
            if client_id not in loaded:
                continue
            _, config, _ = get(loaded, client_id)
            assert config.client_id != "abc_inc"
            assert config.is_synthetic

    def test_abc_is_not_marked_synthetic(self, abc_config):
        assert abc_config.is_synthetic is False
        assert "SYNTHETIC" not in abc_config.company_name

    def test_output_is_written_per_client(self, loaded, tmp_path):
        """Client separation: one directory per client_id, never shared."""
        settings = Settings.from_env(tmp_path)
        paths = set()
        for client_id in list(loaded)[:3]:
            _, config, expected = loaded[client_id]
            paths.add(settings.output_dir / config.client_id
                      / expected["reporting_period"])
        assert len(paths) == 3
