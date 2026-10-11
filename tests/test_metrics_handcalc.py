"""Metrics against a three-month company whose answers are computed on paper.

Inputs (from tests/helpers.py), oldest month first:

                 2025-01  2025-02  2025-03
    Widgets          100      200        0
    Services          50        0       25
    Materials         30       40        5
    Rent              10       10       10
    Salary A          20       20       20
    Salary B           5        5        0
    Marketing          0       50      100

Derived by hand:

    Total income     150      200       25
    Total COGS        30       40        5
    Gross profit     120      160       20
    Salaries group    25       25       20
    Total opex        35       85      130      (rent + marketing + salaries group)
    Operating income  85       75     -110
"""

from __future__ import annotations

import pytest

from finance_engine.config import ClientConfig
from finance_engine.metrics import build_metrics
from finance_engine.sources.excel import ExcelSource
from tests.helpers import build_small_workbook, write_small_config


@pytest.fixture
def tiny(tmp_path):
    workbook = build_small_workbook(tmp_path / "tiny.xlsx")
    config = ClientConfig.load_path(write_small_config(tmp_path))
    dataset = ExcelSource(workbook).load(config)
    return dataset, config


def metrics_for(tiny, period: str) -> dict:
    dataset, config = tiny
    doc = build_metrics(dataset, config, period)
    return {m["name"]: m for m in doc["metrics"]}


class TestLevels:
    def test_february_totals(self, tiny):
        m = metrics_for(tiny, "2025-02")
        assert m["revenue.total"]["value"] == 200.0        # 200 + 0
        assert m["cogs.total"]["value"] == 40.0
        assert m["gross_profit"]["value"] == 160.0         # 200 - 40
        assert m["opex.total"]["value"] == 85.0            # 10 + 50 + (20 + 5)
        assert m["operating_income"]["value"] == 75.0      # 160 - 85

    def test_march_totals_including_a_loss(self, tiny):
        m = metrics_for(tiny, "2025-03")
        assert m["revenue.total"]["value"] == 25.0         # 0 + 25
        assert m["gross_profit"]["value"] == 20.0          # 25 - 5
        assert m["opex.total"]["value"] == 130.0           # 10 + 100 + (20 + 0)
        assert m["operating_income"]["value"] == -110.0    # 20 - 130

    def test_subtotal_group_is_counted_once(self, tiny):
        m = metrics_for(tiny, "2025-02")
        # Salaries group = 20 + 5 = 25. Total opex = 85, not 85 + 25.
        assert m["opex.group.salaries"]["value"] == 25.0
        assert m["opex.group.ungrouped"]["value"] == 60.0  # rent 10 + marketing 50
        assert m["opex.total"]["value"] == 85.0

    def test_categories_partition_the_total(self, tiny):
        m = metrics_for(tiny, "2025-02")
        assert m["opex.category.people"]["value"] == 25.0        # salaries A + B
        assert m["opex.category.marketing"]["value"] == 50.0
        assert m["opex.category.occupancy"]["value"] == 10.0
        total = sum(m[f"opex.category.{k}"]["value"]
                    for k in ("people", "marketing", "occupancy"))
        assert total == m["opex.total"]["value"] == 85.0

    def test_line_values(self, tiny):
        m = metrics_for(tiny, "2025-02")
        assert m["revenue.stream.widgets"]["value"] == 200.0
        assert m["revenue.stream.services"]["value"] == 0.0
        assert m["opex.line.salary_b"]["value"] == 5.0


class TestMargins:
    def test_february_gross_margin(self, tiny):
        m = metrics_for(tiny, "2025-02")
        # 160 / 200 * 100 = 80.0
        assert m["gross_margin_pct"]["value"] == pytest.approx(80.0)

    def test_january_gross_margin_and_the_point_change(self, tiny):
        m = metrics_for(tiny, "2025-02")
        # Jan: 120 / 150 * 100 = 80.0. Feb is also 80.0, so the change is 0 points.
        assert m["gross_margin_pct.mom_pts"]["value"] == pytest.approx(0.0)

    def test_march_margin_change_in_points(self, tiny):
        m = metrics_for(tiny, "2025-03")
        # Mar: 20 / 25 * 100 = 80.0, also 80.0. Still zero points.
        assert m["gross_margin_pct"]["value"] == pytest.approx(80.0)
        assert m["gross_margin_pct.mom_pts"]["value"] == pytest.approx(0.0)

    def test_operating_margin_february(self, tiny):
        m = metrics_for(tiny, "2025-02")
        # 75 / 200 * 100 = 37.5
        assert m["operating_margin_pct"]["value"] == pytest.approx(37.5)

    def test_operating_margin_can_be_negative(self, tiny):
        m = metrics_for(tiny, "2025-03")
        # -110 / 25 * 100 = -440.0
        assert m["operating_margin_pct"]["value"] == pytest.approx(-440.0)


class TestMonthOverMonth:
    def test_revenue_increase(self, tiny):
        m = metrics_for(tiny, "2025-02")
        assert m["revenue.total.mom_abs"]["value"] == 50.0            # 200 - 150
        # 50 / 150 * 100 = 33.333...
        assert m["revenue.total.mom_pct"]["value"] == pytest.approx(100.0 / 3.0)

    def test_revenue_collapse(self, tiny):
        m = metrics_for(tiny, "2025-03")
        assert m["revenue.total.mom_abs"]["value"] == -175.0          # 25 - 200
        # -175 / 200 * 100 = -87.5
        assert m["revenue.total.mom_pct"]["value"] == pytest.approx(-87.5)

    def test_opex_increase(self, tiny):
        m = metrics_for(tiny, "2025-02")
        assert m["opex.total.mom_abs"]["value"] == 50.0               # 85 - 35
        # 50 / 35 * 100 = 142.857...
        assert m["opex.total.mom_pct"]["value"] == pytest.approx(5000.0 / 35.0)

    def test_fall_to_zero_is_minus_one_hundred_percent(self, tiny):
        m = metrics_for(tiny, "2025-02")
        # Services: 50 -> 0
        assert m["revenue.stream.services.mom_abs"]["value"] == -50.0
        assert m["revenue.stream.services.mom_pct"]["value"] == pytest.approx(-100.0)

    def test_rise_from_zero_has_no_percentage(self, tiny):
        m = metrics_for(tiny, "2025-03")
        # Services: 0 -> 25. The absolute change is reported; the percentage is not.
        assert m["revenue.stream.services.mom_abs"]["value"] == 25.0
        assert m["revenue.stream.services.mom_pct"]["value"] is None
        assert m["revenue.stream.services.mom_pct"]["unavailable_reason"] == "zero_base"

    def test_unchanged_line_reports_zero_not_none(self, tiny):
        m = metrics_for(tiny, "2025-02")
        # Rent is 10 in both months: a real zero change, not missing data.
        assert m["opex.line.rent.mom_abs"]["value"] == 0.0
        assert m["opex.line.rent.mom_pct"]["value"] == pytest.approx(0.0)


class TestTtm:
    def test_twelve_month_window_is_blocked_with_only_three_months(self, tiny):
        m = metrics_for(tiny, "2025-03")
        ttm = m["revenue.total.ttm"]
        assert ttm["value"] is None
        assert ttm["validation_status"] == "blocked"
        assert ttm["unavailable_reason"] == "incomplete_window"

    def test_the_document_states_the_window_it_needed(self, tiny):
        dataset, config = tiny
        doc = build_metrics(dataset, config, "2025-03")
        assert doc["ttm"]["complete"] is False
        assert doc["ttm"]["window"][0] == "2024-04"     # 11 months before Mar 2025
        assert doc["ttm"]["window"][-1] == "2025-03"
        assert len(doc["ttm"]["window"]) == 12

    def test_a_short_window_is_never_substituted(self, tiny):
        m = metrics_for(tiny, "2025-03")
        # The three months available sum to 375, but that must not appear as a TTM.
        for name in ("revenue.total.ttm", "cogs.total.ttm", "opex.total.ttm",
                     "gross_profit.ttm", "operating_income.ttm"):
            assert m[name]["value"] is None, f"{name} invented a short-window total"

    def test_ttm_is_computed_when_the_window_is_complete(self, tmp_path):
        # A one-month "TTM" is complete by definition, which exercises the summing path.
        workbook = build_small_workbook(tmp_path / "tiny.xlsx")
        config = ClientConfig.load_path(
            write_small_config(tmp_path, {"ttm": {"months": 2}})
        )
        dataset = ExcelSource(workbook).load(config)
        doc = build_metrics(dataset, config, "2025-02")
        m = {x["name"]: x for x in doc["metrics"]}
        assert doc["ttm"]["complete"] is True
        assert m["revenue.total.ttm"]["value"] == 350.0        # Jan 150 + Feb 200
        assert m["opex.total.ttm"]["value"] == 120.0           # Jan 35 + Feb 85


class TestProvenance:
    def test_every_metric_names_its_method(self, tiny):
        dataset, config = tiny
        doc = build_metrics(dataset, config, "2025-02")
        for metric in doc["metrics"]:
            assert metric["method_id"] in doc["methods"], metric["name"]

    def test_totals_carry_their_source_rows(self, tiny):
        m = metrics_for(tiny, "2025-02")
        rows = m["revenue.total"]["source_rows"]
        assert len(rows) == 2
        assert {r["label"] for r in rows} == {"Widgets", "Services"}
        assert all(r["period"] == "2025-02" for r in rows)
        assert {r["original_value"] for r in rows} == {200.0, 0.0}

    def test_workbook_reported_value_is_recorded_but_not_used(self, tiny):
        m = metrics_for(tiny, "2025-02")
        # Our figure is summed from items; the workbook's own total is kept alongside.
        assert m["revenue.total"]["value"] == 200.0
        assert m["revenue.total"]["reported_value"] == 200.0
        assert m["revenue.total"]["method_id"] == "sum_section_items_v1"

    def test_every_metric_is_keyed_by_client_and_period(self, tiny):
        dataset, config = tiny
        doc = build_metrics(dataset, config, "2025-02")
        assert all(x["client_id"] == "tiny_co" for x in doc["metrics"])
        assert all(x["period"] == "2025-02" for x in doc["metrics"])


class TestLayoutOrientation:
    def test_newest_first_gives_identical_results(self, tmp_path):
        """Reversing the column order must not change a single figure."""
        oldest = build_small_workbook(tmp_path / "oldest.xlsx", newest_first=False)
        newest = build_small_workbook(tmp_path / "newest.xlsx", newest_first=True)
        cfg_old = ClientConfig.load_path(
            write_small_config(tmp_path / "a", client_id="tiny_old")
        )
        cfg_new = ClientConfig.load_path(
            write_small_config(tmp_path / "b", client_id="tiny_new",
                               overrides={"sheets": {"income_statement":
                                                     {"date_order": "newest_first"}}})
        )
        doc_old = build_metrics(ExcelSource(oldest).load(cfg_old), cfg_old, "2025-02")
        doc_new = build_metrics(ExcelSource(newest).load(cfg_new), cfg_new, "2025-02")
        old = {x["name"]: x["value"] for x in doc_old["metrics"]}
        new = {x["name"]: x["value"] for x in doc_new["metrics"]}
        assert old == new
