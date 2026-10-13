"""Year and whole-file scopes (Phase 5B, tasks 2 and 3).

Hand-worked figures come from `tests.helpers.monthly_values`: for month n (months since
Jan 2025) revenue = 1200 + 10n, COGS = 100, gross profit = 1100 + 10n, opex = 350 and
operating income = 750 + 10n. So for Jan..Jun 2025 (n = 0..5) revenue sums to
6 x 1200 + 10 x 15 = 7,350, and for Jan..Jun 2024 (n = -12..-7) to 7,200 - 570 = 6,630.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_engine.cli import EXIT_DATA, EXIT_OK, EXIT_USAGE, main
from finance_engine.config import ClientConfig, Settings
from finance_engine.guard import NumberGuard
from finance_engine.metrics import build_metrics
from finance_engine.monthly import HEADLINE, MARGINS, month_figures
from finance_engine.sources.excel import ExcelSource
from finance_engine.spans import SpanError, build_all_metrics, build_year_metrics
from tests.helpers import build_monthly_workbook, write_monthly_config

ROOT = Path(__file__).resolve().parents[1]


def months(year: int, first: int = 1, last: int = 12) -> list[str]:
    return [f"{year}-{m:02d}" for m in range(first, last + 1)]


def _m(doc, name):
    return {x["name"]: x for x in doc["metrics"]}[name]


def _codes(doc):
    return [i["code"] for i in doc["data_quality_issues"]]


@pytest.fixture
def make(tmp_path):
    """Build a multi_co workbook with the given months and load it."""
    cfg_path = write_monthly_config(tmp_path / "config" / "clients")
    src = tmp_path / "data" / "clients" / "multi_co" / "source"
    src.mkdir(parents=True, exist_ok=True)

    def _make(month_list, name="book.xlsx", overrides=None):
        path = build_monthly_workbook(src / name, month_list, overrides)
        config = ClientConfig.load_path(cfg_path)
        return ExcelSource(path).load(config), config

    _make.root = tmp_path
    return _make


def _project_client(client_id: str):
    cfg = ClientConfig.load(client_id, Settings.from_env(ROOT))
    path = ROOT / cfg.source_directory / cfg.source_filename
    if not path.exists():
        pytest.skip(f"{path} not present (generate it with scripts/make_test_workbooks.py)")
    return ExcelSource(path).load(cfg), cfg


# ---- the monthly figures are the month report's figures -----------------------------

@pytest.mark.parametrize("client_id", ["test_clean", "test_errors",
                                       "company_i_multiple_revenue_streams",
                                       "company_h_zero_negative_values", "abc_inc"])
def test_month_figures_equal_the_month_report(client_id):
    """A year total is a sum of these, so they must be the month report's values."""
    if client_id.startswith("company"):
        cfg = ClientConfig.load(client_id, Settings.from_env(ROOT))
        ds = ExcelSource(ROOT / cfg.source_directory / cfg.source_filename).load(cfg)
    elif client_id == "abc_inc":
        cfg = ClientConfig.load(client_id, Settings.from_env(ROOT))
        path = ROOT / "data" / "clients" / "abc_inc" / "source" / cfg.source_filename
        if not path.exists():
            pytest.skip("ABC workbook not present")
        ds = ExcelSource(path).load(cfg)
    else:
        ds, cfg = _project_client(client_id)
    for period in ds.period_keys():
        doc = build_metrics(ds, cfg, period)
        month = {x["name"]: x["value"] for x in doc["metrics"]}
        fig = month_figures(ds.income_statement, cfg, period)
        compared = 0
        for name, value in fig.values.items():
            if name in month and month[name] is not None:
                assert value == pytest.approx(month[name], abs=1e-9), (period, name)
                compared += 1
        assert compared >= len(HEADLINE) + len(MARGINS)


# ---- year scope -------------------------------------------------------------------

class TestCompleteYear:
    def test_test_clean_2025_cross_check(self):
        """These equal test_clean's existing 2025-12 trailing-twelve-month figures."""
        ds, cfg = _project_client("test_clean")
        doc = build_year_metrics(ds, cfg, 2025)
        expected = {"revenue.total": 642000, "cogs.total": 114600,
                    "gross_profit": 527400, "opex.total": 426000,
                    "operating_income": 101400}
        for name, value in expected.items():
            assert _m(doc, name)["value"] == pytest.approx(value), name
        ttm = {x["name"]: x["value"] for x in build_metrics(ds, cfg, "2025-12")["metrics"]}
        for name in expected:
            assert _m(doc, name)["value"] == pytest.approx(ttm[f"{name}.ttm"])
        assert doc["coverage"]["status"] == "complete"
        assert doc["coverage"]["label"] == "full year, 12 of 12 months"

    def test_totals_streams_categories_quarters_margins(self, make):
        ds, cfg = make(months(2025))
        doc = build_year_metrics(ds, cfg, 2025)
        # n = 0..11: revenue = 12 x 1200 + 10 x 66 = 15,060; sales = 12,000 + 660.
        assert _m(doc, "revenue.total")["value"] == 15060
        assert _m(doc, "revenue.stream.sales")["value"] == 12660
        assert _m(doc, "revenue.stream.services")["value"] == 2400
        assert _m(doc, "cogs.total")["value"] == 1200
        assert _m(doc, "gross_profit")["value"] == 13860
        assert _m(doc, "opex.total")["value"] == 4200
        assert _m(doc, "opex.category.people")["value"] == 3600
        assert _m(doc, "opex.category.occupancy")["value"] == 600
        assert _m(doc, "operating_income")["value"] == 9660
        assert _m(doc, "net_income.reported")["value"] == 9660
        assert _m(doc, "gross_margin_pct")["value"] == pytest.approx(13860 / 15060 * 100)
        assert _m(doc, "operating_margin_pct")["value"] == pytest.approx(9660 / 15060 * 100)
        # Q1 = n 0..2: 3 x 1200 + 10 x 3 = 3,630; Q4 = n 9..11: 3,600 + 300 = 3,900.
        assert _m(doc, "revenue.total.q1")["value"] == 3630
        assert _m(doc, "revenue.total.q4")["value"] == 3900
        for metric in doc["metrics"]:
            assert set(metric) >= {"name", "value", "unit", "method_id",
                                   "validation_status", "source_rows", "warnings"}
            assert metric["method_id"] in doc["methods"], metric["name"]

    def test_monthly_table_and_extremes(self, make):
        ds, cfg = make(months(2024, 12, 12) + months(2025))
        doc = build_year_metrics(ds, cfg, 2025)
        assert _m(doc, "monthly.2025-03.revenue.total")["value"] == 1220
        assert _m(doc, "monthly.2025-03.revenue.total.mom_abs")["value"] == 10
        assert _m(doc, "monthly.2025-03.revenue.total.mom_pct")["value"] == \
            pytest.approx(10 / 1210 * 100)
        # January compares with December of the prior year, which this file holds.
        assert _m(doc, "monthly.2025-01.revenue.total.mom_abs")["value"] == 10
        assert _m(doc, "extremes.revenue.total.highest")["value"] == 1310
        assert doc["extremes"]["revenue.total"]["highest"]["period"] == "2025-12"
        assert doc["extremes"]["revenue.total"]["lowest"]["period"] == "2025-01"
        assert not any(n.startswith("monthly.2024-12") for n in
                       (x["name"] for x in doc["metrics"]))

    def test_ties_go_to_the_earliest_month(self, make):
        ds, cfg = make(months(2025, 1, 3), overrides={("Sales", "2025-03"): 1000.0})
        doc = build_year_metrics(ds, cfg, 2025)
        lowest = doc["extremes"]["revenue.total"]["lowest"]
        assert lowest["period"] == "2025-01" and lowest["ties"] == ["2025-03"]


class TestPartialYear:
    def test_year_to_date_is_labelled_and_never_annualised(self, make):
        ds, cfg = make(months(2025, 1, 6))
        doc = build_year_metrics(ds, cfg, 2025)
        cov = doc["coverage"]
        assert cov["status"] == "partial"
        assert cov["label"] == "year to date, 6 of 12 months"
        assert _m(doc, "revenue.total")["value"] == 7350               # the six months only
        assert not any("annual" in x["name"] for x in doc["metrics"])
        assert "never scaled" in _m(doc, "revenue.total")["notes"][0]
        assert any("never scaled, annualised" in l for l in doc["limitations"])
        assert _m(doc, "revenue.total.q2")["value"] == 3720            # n 3..5
        assert _m(doc, "revenue.total.q3")["unavailable_reason"] == "incomplete_quarter"

    def test_a_year_starting_mid_year_is_a_partial_year(self, make):
        ds, cfg = make(months(2025, 4, 12))
        cov = build_year_metrics(ds, cfg, 2025)["coverage"]
        assert cov["label"] == "partial year, 9 of 12 months (2025-04 to 2025-12)"

    def test_test_clean_2026(self):
        ds, cfg = _project_client("test_clean")
        doc = build_year_metrics(ds, cfg, 2026)
        assert doc["coverage"]["label"] == "year to date, 1 of 12 months"
        assert _m(doc, "revenue.total")["value"] == 61000
        assert _m(doc, "revenue.total")["value"] == \
            _m(doc, "monthly.2026-01.revenue.total")["value"]


class TestGap:
    def test_a_month_missing_inside_the_year_blocks_the_totals(self, make):
        ds, cfg = make([p for p in months(2025) if p != "2025-06"])
        doc = build_year_metrics(ds, cfg, 2025)
        error = next(i for i in doc["data_quality_issues"]
                     if i["code"] == "year_months_missing")
        assert error["severity"] == "error" and error["details"]["missing"] == ["2025-06"]
        for name in ("revenue.total", "revenue.stream.sales", "opex.category.people",
                     "gross_margin_pct", "operating_income", "revenue.total.yoy_abs"):
            metric = _m(doc, name)
            assert metric["value"] is None and metric["validation_status"] == "blocked"
        assert _m(doc, "revenue.total")["unavailable_reason"] == "missing_month_in_span"
        assert _m(doc, "revenue.total.q1")["value"] == 3630            # still complete
        assert _m(doc, "revenue.total.q2")["unavailable_reason"] == "incomplete_quarter"
        assert _m(doc, "monthly.2025-07.revenue.total")["value"] == 1260
        assert _m(doc, "monthly.2025-07.revenue.total.mom_abs")["value"] is None

    def test_test_errors_2025(self):
        ds, cfg = _project_client("test_errors")
        doc = build_year_metrics(ds, cfg, 2025)
        assert doc["coverage"]["gaps_inside_span"] == ["2025-06"]
        assert _m(doc, "revenue.total")["value"] is None
        assert "year_months_missing" in _codes(doc)
        # Cell-level issues inside 2025 are listed; Jan 2026's negative Bad Debts is not.
        assert {"non_numeric_cell", "blank_cell"} <= set(_codes(doc))
        assert "negative_line_item" not in _codes(doc)
        assert all(f["period"].startswith("2025-") for f in doc["reconciliation"]["failures"])
        assert doc["reconciliation"]["checks_failed"] == 4

    def test_test_errors_2026_has_only_its_own_issues(self):
        ds, cfg = _project_client("test_errors")
        doc = build_year_metrics(ds, cfg, 2026)
        assert "negative_line_item" in _codes(doc)
        assert "non_numeric_cell" not in _codes(doc)
        assert doc["reconciliation"]["checks_failed"] == 0


class TestYearOnYear:
    def test_same_months_in_both_years(self, make):
        ds, cfg = make(months(2024) + months(2025, 1, 6))
        doc = build_year_metrics(ds, cfg, 2025)
        assert doc["yoy"]["comparable"] is True
        assert doc["yoy"]["prior_year_months"] == months(2024, 1, 6)
        assert _m(doc, "revenue.total.prior_year")["value"] == 6630
        assert _m(doc, "revenue.total.yoy_abs")["value"] == 720
        assert _m(doc, "revenue.total.yoy_pct")["value"] == pytest.approx(720 / 6630 * 100)
        assert _m(doc, "opex.total.yoy_abs")["value"] == 0
        assert _m(doc, "gross_margin_pct.yoy_pts")["value"] is not None

    def test_different_months_are_not_compared(self, make):
        ds, cfg = make(months(2024, 3, 12) + months(2025, 1, 6))
        doc = build_year_metrics(ds, cfg, 2025)
        assert doc["yoy"]["comparable"] is False
        assert doc["yoy"]["missing_in_prior_year"] == ["2024-01", "2024-02"]
        for name in ("revenue.total.yoy_abs", "revenue.total.yoy_pct",
                     "revenue.total.prior_year", "gross_margin_pct.yoy_pts"):
            assert _m(doc, name)["value"] is None
            assert _m(doc, name)["unavailable_reason"] == "not_comparable"

    def test_a_negative_prior_year_has_no_percentage(self, make):
        # Payroll of 10,000 in each 2024 month makes 2024 operating income negative.
        overrides = {("Payroll", p): 10000.0 for p in months(2024, 1, 3)}
        ds, cfg = make(months(2024, 1, 3) + months(2025, 1, 3), overrides=overrides)
        doc = build_year_metrics(ds, cfg, 2025)
        assert _m(doc, "operating_income.prior_year")["value"] < 0
        assert _m(doc, "operating_income.yoy_abs")["value"] is not None
        assert _m(doc, "operating_income.yoy_pct")["unavailable_reason"] == "negative_base"


# ---- whole file -------------------------------------------------------------------

class TestWholeFile:
    def test_series_years_and_start_to_end(self, make):
        ds, cfg = make(months(2025) + months(2026, 1, 1))
        doc = build_all_metrics(ds, cfg)
        cov = doc["coverage"]
        assert (cov["first"], cov["last"], cov["months_present_count"]) == \
            ("2025-01", "2026-01", 13)
        assert cov["gaps"] == []
        series = {s["measure"]: s for s in doc["series"]["measures"]}
        assert set(series) == set(HEADLINE) | set(MARGINS)
        revenue = series["revenue.total"]["points"]
        assert [p["period"] for p in revenue] == doc["series"]["periods"]
        assert revenue[0] == {"period": "2025-01", "value": 1200.0,
                              "validation_status": "ok"}
        for point in revenue:
            assert point["value"] == _m(doc, f"monthly.{point['period']}.revenue.total")["value"]
        assert _m(doc, "year.2025.revenue.total")["value"] == 15060
        assert _m(doc, "year.2026.revenue.total")["value"] == 1320
        years = {y["year"]: y for y in doc["years"]}
        assert years[2026]["label"] == "year to date, 1 of 12 months"
        assert _m(doc, "revenue.total.start_to_end_abs")["value"] == 120
        assert _m(doc, "revenue.total.start_to_end_pct")["value"] == pytest.approx(10.0)
        assert doc["seasonality"]["assessed"] is False

    def test_year_totals_match_the_year_scope(self, make):
        ds, cfg = make(months(2025) + months(2026, 1, 4))
        whole = build_all_metrics(ds, cfg)
        for year in (2025, 2026):
            single = build_year_metrics(ds, cfg, year)
            for name in HEADLINE + MARGINS:
                assert _m(whole, f"year.{year}.{name}")["value"] == \
                    pytest.approx(_m(single, name)["value"])

    def test_gaps_and_a_blocked_year(self, make):
        ds, cfg = make([p for p in months(2025) if p != "2025-06"] + months(2026, 1, 2))
        doc = build_all_metrics(ds, cfg)
        assert doc["coverage"]["gaps"] == ["2025-06"]
        assert "missing_months" in _codes(doc)
        assert _m(doc, "year.2025.revenue.total")["validation_status"] == "blocked"
        assert _m(doc, "year.2026.revenue.total")["value"] == 2650        # n 12, 13
        assert _m(doc, "revenue.total.start_to_end_abs")["value"] == 130

    def test_seasonality_needs_24_months(self, make):
        ds, cfg = make(months(2024) + months(2025))
        assert build_all_metrics(ds, cfg)["seasonality"]["assessed"] is True

    def test_reconciliation_covers_every_month(self, make):
        ds, cfg = make(months(2025, 1, 5))
        doc = build_all_metrics(ds, cfg)
        month_doc = build_metrics(ds, cfg, "2025-05")
        assert doc["reconciliation"]["checks_run"] == month_doc["reconciliation"]["checks_run"]

    def test_a_zero_first_month_has_no_percentage(self, make):
        overrides = {("Sales", "2025-01"): 0.0, ("Services", "2025-01"): 0.0}
        ds, cfg = make(months(2025, 1, 3), overrides=overrides)
        doc = build_all_metrics(ds, cfg)
        assert _m(doc, "revenue.total.start_to_end_abs")["value"] == 1220
        assert _m(doc, "revenue.total.start_to_end_pct")["unavailable_reason"] == "zero_base"


# ---- reports and the CLI -------------------------------------------------------------

class TestCli:
    def test_year_paths_and_json(self, make, capsys):
        make(months(2025, 1, 6))
        root = str(make.root)
        code = main(["report", "--client", "multi_co", "--scope", "year", "--year", "2025",
                     "--provider", "deterministic", "--json", "--root", root])
        payload = json.loads(capsys.readouterr().out)
        assert code in (EXIT_OK, EXIT_DATA)
        out = make.root / "output" / "clients" / "multi_co" / "year-2025"
        assert (out / "metrics.json").exists() and (out / "report.md").exists()
        assert payload["scope"] == "year" and payload["number_check"]["passed"] is True
        report = (out / "report.md").read_text(encoding="utf-8")
        assert "year to date, 6 of 12 months" in report
        assert NumberGuard(json.loads((out / "metrics.json").read_text("utf-8"))) \
            .check(report).passed

    def test_all_scope_with_file_keys_the_path(self, make, capsys):
        make(months(2025, 1, 3), name="early.xlsx")
        make(months(2025, 4, 6), name="late.xlsx")
        root = str(make.root)
        assert main(["analyze", "--client", "multi_co", "--scope", "all",
                     "--file", "late.xlsx", "--root", root]) == EXIT_OK
        path = make.root / "output" / "clients" / "multi_co" / "late" / "all" / "metrics.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["source"]["filename"] == "late.xlsx"
        assert doc["coverage"]["first"] == "2025-04"
        # The whole file is one request; two files both match it, so it must be named.
        assert main(["analyze", "--client", "multi_co", "--scope", "all",
                     "--root", root]) == EXIT_USAGE
        assert "never merged" in capsys.readouterr().err

    def test_year_on_year_month_in_another_file(self, make, capsys):
        make(months(2024, 1, 6), name="y2024.xlsx")
        make(months(2025, 1, 6), name="y2025.xlsx")
        main(["analyze", "--client", "multi_co", "--scope", "year", "--year", "2025",
              "--root", str(make.root)])
        assert "source: y2025.xlsx" in capsys.readouterr().err
        doc = json.loads((make.root / "output" / "clients" / "multi_co" / "year-2025"
                          / "metrics.json").read_text(encoding="utf-8"))
        assert _m(doc, "revenue.total.yoy_abs")["unavailable_reason"] == "in_other_file"
        assert "y2024.xlsx" in doc["yoy"]["note"]
        assert _m(doc, "monthly.2025-01.revenue.total.mom_abs")["unavailable_reason"] == \
            "missing_input"

    @pytest.mark.parametrize("argv, message", [
        (["--scope", "year"], "needs --year"),
        (["--scope", "year", "--year", "2025", "--period", "2025-01"], "--period applies"),
        (["--scope", "all", "--year", "2025"], "--year applies"),
        (["--period", "2025-01", "--year", "2025"], "--year applies"),
        (["--scope", "year", "--year", "2030"], "Year 2030 is not in"),
    ])
    def test_usage_errors(self, make, capsys, argv, message):
        make(months(2025, 1, 3))
        code = main(["analyze", "--client", "multi_co", *argv, "--root", str(make.root)])
        assert code == EXIT_USAGE
        err = capsys.readouterr().err
        assert message in err and "Traceback" not in err

    def test_brief_and_check_commentary_read_the_year_document(self, make, tmp_path, capsys):
        make(months(2025))
        root = ["--root", str(make.root)]
        main(["analyze", "--client", "multi_co", "--scope", "year", "--year", "2025", *root])
        capsys.readouterr()
        assert main(["brief", "--client", "multi_co", "--scope", "year", "--year", "2025",
                     *root]) == EXIT_OK
        assert "$15,060.00" in capsys.readouterr().out
        note = tmp_path / "note.md"
        note.write_text("Revenue for 2025 was $15,060.00.", encoding="utf-8")
        assert main(["check-commentary", "--client", "multi_co", "--scope", "year",
                     "--year", "2025", "--file", str(note), *root]) == EXIT_OK

    def test_span_error_is_raised_for_an_empty_year(self, make):
        ds, cfg = make(months(2025, 1, 3))
        with pytest.raises(SpanError):
            build_year_metrics(ds, cfg, 2024)
