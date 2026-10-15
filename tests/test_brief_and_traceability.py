"""The brief, and the whole report, contain only numbers that trace to metrics.json.

Before Phase 3 the guard only ever ran on the *commentary section*. The eval suite ran it
on the whole report.md and found numbers the report computed or printed itself: prior-
month totals rebuilt as `current - change`, a hard-coded "100.0%", and the guard's own
counts. These tests keep the whole document traceable, for every client and for the
brief the Claude skill reads.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from finance_engine.brief import build_brief
from finance_engine.cli import EXIT_OK, EXIT_USAGE, main
from finance_engine.commentary.deterministic import DeterministicProvider
from finance_engine.config import ClientConfig, Settings
from finance_engine.guard import NumberGuard
from finance_engine.metrics import build_metrics
from finance_engine.report import build_report
from finance_engine.sources.excel import ExcelSource

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# (client, period) pairs that exercise each scenario's interesting month.
SYNTHETIC = [
    ("company_a_normal_growth", "2026-06"),
    ("company_c_expense_spike", "2026-01"),
    ("company_e_seasonal_business", "2026-01"),
    ("company_f_one_time_expense", "2025-11"),
    ("company_g_missing_data", "2026-06"),
    ("company_h_zero_negative_values", "2026-06"),
    ("company_i_multiple_revenue_streams", "2026-06"),
    ("company_j_balance_sheet", "2026-06"),
]


def _doc(client_id: str, period: str) -> dict:
    cfg = ClientConfig.load(client_id, Settings.from_env(PROJECT_ROOT))
    src = PROJECT_ROOT / (cfg.source_directory or f"data/clients/{client_id}/source")
    path = src / (cfg.source_filename or f"{client_id}.xlsx")
    if not path.exists():
        pytest.skip(f"workbook not present: {path}")
    return build_metrics(ExcelSource(path).load(cfg), cfg, period, project_root=PROJECT_ROOT)


@pytest.fixture
def abc_workspace(tmp_path, project_root, abc_config, abc_workbook):
    (tmp_path / "config" / "clients").mkdir(parents=True)
    dest = tmp_path / "data" / "clients" / "abc_inc" / "source"
    dest.mkdir(parents=True)
    shutil.copy(project_root / "config" / "clients" / "abc_inc.yaml",
                tmp_path / "config" / "clients" / "abc_inc.yaml")
    shutil.copy(abc_workbook, dest / abc_config.source_filename)
    return tmp_path


@pytest.fixture(scope="module", params=SYNTHETIC, ids=[c for c, _ in SYNTHETIC])
def synthetic_doc(request) -> dict:
    return _doc(*request.param)


class TestBrief:
    def test_every_number_in_the_brief_is_in_metrics_json(self, synthetic_doc):
        result = NumberGuard(synthetic_doc).check(build_brief(synthetic_doc))
        assert result.numbers_passed, result.summary()

    def test_abc_brief_is_traceable_and_small(self, abc_metrics):
        brief = build_brief(abc_metrics)
        assert NumberGuard(abc_metrics).check(brief).numbers_passed
        # The point of the brief: the skill reads ~11 KB, not ~860 KB of metrics.json.
        assert len(brief) < len(json.dumps(abc_metrics)) / 20

    def test_brief_names_the_metric_behind_each_figure(self, abc_metrics):
        brief = build_brief(abc_metrics)
        for name in ("revenue.total", "gross_margin_pct", "opex.total",
                     "opex.category.payroll_people_costs", "opex.line.events_conferences"):
            assert f"`{name}`" in brief

    def test_brief_shows_the_prior_value_rather_than_leaving_it_to_be_derived(
            self, abc_metrics):
        assert "$208,350.96" in build_brief(abc_metrics)     # revenue.total.prior

    def test_incomplete_ttm_is_marked_and_carries_no_ttm_figure(self):
        doc = _doc("company_g_missing_data", "2026-06")
        brief = build_brief(doc)
        section = brief.split("## Trailing twelve months")[1].split("\n## ")[0]
        assert "NOT AVAILABLE" in section
        assert "$" not in section
        assert "INCOMPLETE" in brief.split("## Status")[1].split("\n## ")[0]

    def test_every_data_quality_code_is_listed(self, synthetic_doc):
        brief = build_brief(synthetic_doc)
        for issue in synthetic_doc["data_quality_issues"]:
            assert f"`{issue['code']}`" in brief

    def test_seasonality_limitation_is_stated(self):
        brief = build_brief(_doc("company_e_seasonal_business", "2026-01"))
        assert "Seasonality: NOT assessed" in brief

    def test_metric_filter(self, abc_metrics):
        out = build_brief(abc_metrics, "opex.line.legal")
        assert "`opex.line.legal_expenses`" in out
        assert "revenue.total" not in out


class TestBriefCommand:
    def test_requires_an_analyze_run_first(self, tmp_path, capsys):
        code = main(["brief", "--client", "abc_inc", "--period", "2026-06",
                     "--root", str(tmp_path)])
        assert code == EXIT_USAGE
        assert "Run 'analyze" in capsys.readouterr().err

    def test_prints_the_brief(self, abc_workspace, capsys):
        assert main(["analyze", "--client", "abc_inc", "--period", "2026-06",
                     "--root", str(abc_workspace)]) == EXIT_OK
        capsys.readouterr()
        assert main(["brief", "--client", "abc_inc", "--period", "2026-06",
                     "--root", str(abc_workspace)]) == EXIT_OK
        assert "# Brief: ABC Inc" in capsys.readouterr().out


class TestWholeReportIsTraceable:
    def test_synthetic_reports(self, synthetic_doc):
        commentary = DeterministicProvider().generate(synthetic_doc)
        guard = NumberGuard(synthetic_doc).check(commentary).to_dict()
        report = build_report(synthetic_doc, commentary, "deterministic", guard)
        result = NumberGuard(synthetic_doc).check(report)
        assert result.passed, result.summary()

    def test_abc_report(self, abc_metrics):
        commentary = DeterministicProvider().generate(abc_metrics)
        guard = NumberGuard(abc_metrics).check(commentary).to_dict()
        report = build_report(abc_metrics, commentary, "deterministic", guard)
        result = NumberGuard(abc_metrics).check(report)
        assert result.passed, result.summary()

    def test_prior_month_totals_come_from_the_engine(self, abc_metric_map):
        # Workbook's own May 2026 Total Income and Total Operating Expenses.
        assert abc_metric_map["revenue.total.prior"]["value"] == pytest.approx(208350.96)
        assert abc_metric_map["opex.total.prior"]["value"] == pytest.approx(478781.03)
        assert abc_metric_map["revenue.total.prior"]["method_id"] == "prior_period_value_v1"

    def test_prior_is_emitted_only_for_the_headline_measures(self, abc_metric_map):
        priors = sorted(n for n in abc_metric_map if n.endswith(".prior"))
        assert priors == ["cogs.total.prior", "gross_profit.prior", "operating_income.prior",
                          "opex.total.prior", "revenue.total.prior"]
