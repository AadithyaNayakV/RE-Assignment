"""Failed reconciliation checks reach the writer, and a report must name their months.

The brief used to print only "56/60 checks passed". A writer following the skill (which
forbids reading the raw metrics.json) could not say which checks failed, and the
test_errors 2025-12 report said exactly that. An earlier report for the same client did
list them, but only because that run read metrics.json directly.

test_errors is rebuilt here in tmp_path from scripts/make_test_workbooks.py, so the test
does not depend on the git-ignored data/clients folder. Its planted faults give four
failed checks: revenue and gross profit in 2025-08, operating expenses in 2025-10 and
2025-11.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from finance_engine.brief import build_brief
from finance_engine.commentary.deterministic import DeterministicProvider
from finance_engine.config import ClientConfig
from finance_engine.guard import NumberGuard
from finance_engine.metrics import build_metrics
from finance_engine.report import build_report
from finance_engine.sources.excel import ExcelSource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.report_checks import check_report  # noqa: E402

EXPECTED_FAILURES = [
    ("revenue_items_vs_total", "2025-08", "$55,000.00", "$60,000.00", "-$5,000.00"),
    ("gross_profit", "2025-08", "$50,300.00", "$45,300.00", "+$5,000.00"),
    ("opex_items_vs_total", "2025-10", "$31,000.00", "$36,000.00", "-$5,000.00"),
    ("opex_items_vs_total", "2025-11", "$33,000.00", "$36,000.00", "-$3,000.00"),
]
CHECK = "reconciliation_failures_named"


@pytest.fixture(scope="module")
def test_errors(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("test_errors")
    spec = importlib.util.spec_from_file_location(
        "make_test_workbooks", ROOT / "scripts" / "make_test_workbooks.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    source = root / "data" / "clients" / "test_errors" / "source"
    source.mkdir(parents=True)
    gen.build(source / "test_with_errors.xlsx", True, "Test Errors Co (SYNTHETIC)")
    (root / "config" / "clients").mkdir(parents=True)
    shutil.copy(ROOT / "config" / "clients" / "test_errors.yaml",
                root / "config" / "clients" / "test_errors.yaml")
    config = ClientConfig.load_path(root / "config" / "clients" / "test_errors.yaml")
    dataset = ExcelSource(source / "test_with_errors.xlsx").load(config)
    return build_metrics(dataset, config, "2025-12", project_root=root)


class TestBrief:
    def test_lists_all_four_failed_checks(self, test_errors):
        brief = build_brief(test_errors)
        section = brief.split("## Reconciliation failures", 1)[1].split("\n## ", 1)[0]
        rows = [line for line in section.splitlines() if line.startswith("| `")]
        assert len(rows) == 4
        for (key, period, computed, reported, difference), row in zip(EXPECTED_FAILURES,
                                                                       rows):
            cells = [c.strip() for c in row.strip("|").split("|")]
            assert cells[0] == f"`{key}`"
            assert cells[2:] == [period, computed, reported, difference]

    def test_every_listed_figure_passes_the_guard(self, test_errors):
        section = build_brief(test_errors).split("## Reconciliation failures", 1)[1]
        section = section.split("\n## ", 1)[0]
        assert NumberGuard(test_errors).check(section).passed

    def test_status_line_uses_stored_counts_and_passes_the_guard(self, test_errors,
                                                                 abc_metrics):
        """"56/60 checks passed" failed the guard: 56 is derived, not stored. The line
        now prints checks_failed and checks_run, which a writer can copy as is."""
        for doc, expected in ((test_errors, "- Reconciliation: FAIL — 4 of 60 checks failed"),
                              (abc_metrics, "- Reconciliation: PASS — 0 of 144 checks failed")):
            line = next(l for l in build_brief(doc).splitlines()
                        if l.startswith("- Reconciliation:"))
            assert line == expected
            assert "passed" not in line
            assert NumberGuard(doc).check(line).passed, line

    def test_no_section_when_everything_reconciles(self, abc_metrics):
        assert "## Reconciliation failures" not in build_brief(abc_metrics)


def _check(text: str, doc: dict) -> dict:
    results = check_report(text, doc, {"input": {"client_id": "test_errors"}},
                           "deterministic", [])
    return next(r for r in results if r["id"] == CHECK)


class TestReportCheck:
    def test_deterministic_report_passes(self, test_errors):
        commentary = DeterministicProvider().generate(test_errors)
        text = build_report(test_errors, commentary, "deterministic")
        assert _check(text, test_errors)["passed"]

    def test_month_names_are_accepted(self, test_errors):
        text = ("Reconciliation FAILED. Revenue and gross profit do not reconcile in "
                "Aug 2025; operating expenses in October 2025 and 2025-11.")
        assert _check(text, test_errors)["passed"]

    def test_a_missing_month_fails(self, test_errors):
        text = ("Reconciliation FAILED. Revenue and gross profit do not reconcile in "
                "Aug 2025; operating expenses in October 2025.")
        result = _check(text, test_errors)
        assert not result["passed"]
        assert "2025-11" in result["detail"]

    def test_the_vague_wording_fails(self, test_errors):
        """What the 2025-12 report said when the brief did not list the failures."""
        text = ("Reconciliation failed: four of 60 checks did not pass. The brief does not "
                "say which checks or which months failed.")
        assert not _check(text, test_errors)["passed"]

    def test_not_applied_when_everything_reconciles(self, abc_metrics):
        results = check_report("Anything.", abc_metrics,
                               {"input": {"client_id": "abc_inc"}}, "deterministic", [])
        assert CHECK not in {r["id"] for r in results}
