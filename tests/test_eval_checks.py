"""The eval suite's own checks: each must catch the fault it exists for.

A grader that never fails is worse than none. Every Level 2 check here is shown failing on
a deliberately bad report and passing on the deterministic report for the same case.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from finance_engine.commentary.deterministic import DeterministicProvider
from finance_engine.config import ClientConfig, Settings
from finance_engine.guard import NumberGuard
from finance_engine.metrics import build_metrics
from finance_engine.report import build_report
from finance_engine.sources.excel import ExcelSource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.common import compare_results, load_cases  # noqa: E402
from evals.report_checks import check_report, other_clients  # noqa: E402


def _case(case_id: str) -> dict:
    return next(c for c in load_cases([case_id]))


def _report(case: dict) -> tuple[str, dict]:
    inp = case["input"]
    cfg = ClientConfig.load(inp["client_id"], Settings.from_env(ROOT))
    doc = build_metrics(ExcelSource(ROOT / inp["workbook"]).load(cfg), cfg, inp["period"],
                        project_root=ROOT)
    commentary = DeterministicProvider().generate(doc)
    guard = NumberGuard(doc).check(commentary).to_dict()
    return build_report(doc, commentary, "deterministic", guard), doc


def _failed(text: str, doc: dict, case: dict) -> set[str]:
    results = check_report(text, doc, case, "deterministic",
                           other_clients(ROOT / "config" / "clients",
                                         case["input"]["client_id"]))
    return {r["id"] for r in results if not r["passed"]}


def _insert(report: str, sentence: str) -> str:
    """Put a sentence into the commentary, where a writer's prose would go."""
    import re
    return re.sub(r"(?m)^## Revenue$", lambda _: f"{sentence}\n\n## Revenue", report, count=1)


@pytest.fixture(scope="module")
def spike():
    case = _case("04_expense_spike")
    text, doc = _report(case)
    return case, text, doc


@pytest.fixture(scope="module")
def missing():
    case = _case("06_missing_months")
    text, doc = _report(case)
    return case, text, doc


class TestDeterministicControlPasses:
    def test_spike_case(self, spike):
        case, text, doc = spike
        assert _failed(text, doc, case) == set()

    def test_missing_months_case(self, missing):
        case, text, doc = missing
        assert _failed(text, doc, case) == set()


class TestEachCheckCatchesItsFault:
    def test_invented_number(self, spike):
        case, text, doc = spike
        assert "guard" in _failed(_insert(text, "Legal Expenses were $41,234.56."), doc, case)

    def test_unhedged_cause(self, spike):
        case, text, doc = spike
        failed = _failed(_insert(text, "Legal Expenses rose due to a new lawsuit."),
                         doc, case)
        assert {"causal_language_hedged", "cause_vocabulary_hedged",
                "forbidden:invented_legal_cause"} <= failed

    def test_a_hedged_question_is_allowed(self, spike):
        case, text, doc = spike
        ok = _insert(text, "Is the Legal Expenses rise a new legal matter or a timing "
                           "difference? Possible explanation only; needs confirmation.")
        assert _failed(ok, doc, case) == set()

    def test_wrong_direction(self, spike):
        case, text, doc = spike
        failed = _failed(_insert(text, "Legal Expenses fell sharply."), doc, case)
        assert "forbidden:direction_wrong" in failed

    def test_unhedged_one_off(self, spike):
        case, text, doc = spike
        assert "one_offs_hedged" in _failed(
            _insert(text, "Legal Expenses is a one-off."), doc, case)

    def test_missing_section(self, spike):
        case, text, doc = spike
        cut = text.replace("## Reviewer checklist", "## Sign-off")
        failed = _failed(cut, doc, case)
        assert {"section:reviewer_checklist", "reviewer_checklist"} <= failed

    def test_warnings_buried_at_the_end(self, spike):
        case, text, doc = spike
        code = doc["data_quality_issues"][0]["code"]
        buried = text.replace(code, "a warning") + f"\n\nWarning code: `{code}`\n"
        assert f"issue_shown_early:{code}" in _failed(buried, doc, case)

    def test_other_client_mentioned(self, spike):
        case, text, doc = spike
        assert "client_isolation" in _failed(
            _insert(text, "Compare abc_inc for context."), doc, case)

    def test_credentials(self, spike):
        case, text, doc = spike
        assert "no_credentials" in _failed(
            _insert(text, "refresh_token=AB12cd34EF56gh78IJ90kl"), doc, case)

    def test_missing_required_figure(self, spike):
        case, text, doc = spike
        assert "figure:opex.line.legal_expenses.mom_abs" in _failed(
            text.replace("+$34,000.00", "a large amount"), doc, case)

    def test_seasonality_asserted(self):
        case = _case("08_seasonal_business")
        text, doc = _report(case)
        failed = _failed(_insert(text, "The January fall is seasonal."), doc, case)
        assert "seasonality_not_asserted" in failed
        hedged = _insert(text, "The January fall could be seasonal; that cannot be "
                               "confirmed with 18 months of data.")
        assert _failed(hedged, doc, case) == set()

    def test_ttm_figure_on_an_incomplete_window(self, missing):
        case, text, doc = missing
        revenue = next(m for m in doc["metrics"] if m["name"] == "revenue.total")["value"]
        bad = _insert(text, f"TTM revenue was ${revenue:,.2f}.")
        assert "ttm_incomplete_no_figures" in _failed(bad, doc, case)

    def test_impossible_value(self, spike):
        case, text, doc = spike
        assert "impossible_values" in _failed(
            _insert(text, "Legal Expenses rose by #DIV/0!"), doc, case)


class TestCasesAreWellFormed:
    def test_thirteen_cases_each_with_criteria(self):
        cases = load_cases()
        assert len(cases) == 13
        for case in cases:
            assert case["pass_criteria"] and case["why"], case["id"]
            assert case.get("engine") and case.get("skill"), case["id"]

    def test_expected_values_were_built_without_the_engine(self):
        for case in load_cases():
            exp = case["_expected"]
            if case["id"].startswith("12_"):
                assert exp is None
                continue
            if exp is None:            # ABC workbook absent on this machine
                continue
            assert exp["independent_of_engine"] is True
            assert exp["generated_by"] == "evals/tools/build_expected.py"

    def test_the_builder_does_not_import_the_engine(self):
        import ast
        tree = ast.parse((ROOT / "evals" / "tools" / "build_expected.py")
                         .read_text(encoding="utf-8"))
        modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                   for a in n.names}
        modules |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not any(str(m).startswith("finance_engine") for m in modules), modules

    def test_forbidden_claim_patterns_compile(self):
        import re
        for case in load_cases():
            for item in (case["skill"].get("forbidden_claims") or []) + \
                        (case["skill"].get("required_flags") or []):
                re.compile(item["pattern"])
                if item.get("unless"):
                    re.compile(item["unless"])


class TestBaselineCompare:
    BASE = {"git_commit": "aaa", "date_utc": "d", "cases": {
        "c1": {"status": "pass", "checks": [{"id": "x", "passed": True, "detail": ""}],
               "values": {"revenue.total": 100.0}}}}

    def test_no_change(self):
        cmp = compare_results(self.BASE, self.BASE)
        assert not cmp["regressed"] and not cmp["changed_values"]

    def test_new_failure_and_changed_value(self):
        now = json.loads(json.dumps(self.BASE))
        now["cases"]["c1"]["status"] = "fail"
        now["cases"]["c1"]["checks"][0]["passed"] = False
        now["cases"]["c1"]["values"]["revenue.total"] = 101.0
        cmp = compare_results(now, self.BASE)
        assert cmp["regressed"]
        assert cmp["newly_failing"] == ["c1"]
        assert cmp["changed_values"][0]["metric"] == "revenue.total"


class TestFixesFoundOnTheFirstClaudeRun:
    """Checker faults from CHANGELOG #9 and #10. Each still catches the real fault."""

    def test_a_hedge_on_the_previous_wrapped_line_counts(self, spike):
        case, text, doc = spike
        wrapped = _insert(text, "- Seasonality: not assessed. No movement in this report is\n"
                                "  described as seasonal.")
        assert "seasonality_not_asserted" not in _failed(wrapped, doc, case)

    def test_an_unhedged_claim_across_wrapped_lines_is_still_caught(self, spike):
        case, text, doc = spike
        wrapped = _insert(text, "- Legal Expenses rose sharply this month, driven by\n"
                                "  the new office lease.")
        assert "causal_language_hedged" in _failed(wrapped, doc, case)

    def test_a_conditional_one_off_is_hedged(self, spike):
        case, text, doc = spike
        ok = _insert(text, "If it is a one-time recovery, the cost base fell less "
                           "than the total suggests.")
        assert "one_offs_hedged" not in _failed(ok, doc, case)

    def test_powershell_commands_are_counted(self):
        from evals.run_skill_evals import _bash_commands
        info = {"tool_uses": [
            {"name": "PowerShell", "input": {"command": "python -m finance_engine analyze"}},
            {"name": "Bash", "input": {"command": "python -m finance_engine brief"}},
            {"name": "Read", "input": {"file_path": "x"}}]}
        assert _bash_commands(info) == ["python -m finance_engine analyze",
                                        "python -m finance_engine brief"]

    def test_stating_the_ttm_rule_is_not_breaking_it(self, missing):
        """CHANGELOG #13: case 06's first Claude run was failed for quoting the rule."""
        case, text, doc = missing
        ok = _insert(text, "No TTM, annualised or run-rate figure is given.")
        assert "forbidden:eleven_month_ttm" not in _failed(ok, doc, case)
        bad = _insert(text, "The annualised figure is shown for reference.")
        assert "forbidden:eleven_month_ttm" in _failed(bad, doc, case)
