"""The eval checks for the year, whole-file and file-selection cases (14-18).

As in `test_eval_checks.py`: each check passes on the deterministic report and fails on a
deliberately bad one. A grader that never fails is worse than none.
"""

from __future__ import annotations

import copy
import functools
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from finance_engine.commentary.deterministic import DeterministicProvider
from finance_engine.config import ClientConfig, Settings
from finance_engine.guard import NumberGuard
from finance_engine.report import build_report
from finance_engine.sources.excel import ExcelSource
from finance_engine.spans import build_all_metrics, build_year_metrics
from tests.helpers import build_monthly_workbook, write_monthly_config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.common import CaseResult, load_cases, output_rel, scope_args  # noqa: E402
from evals.report_checks import check_report, other_clients  # noqa: E402
from evals.run_engine_evals import _check_span, _check_values  # noqa: E402


def _case(case_id):
    case = next(iter(load_cases([case_id])))
    if not (ROOT / case["input"]["workbook"]).exists():
        pytest.skip(f"{case['input']['workbook']} not present")
    return case


def _doc(case):
    """The engine's document for a case, built once per case and copied per use."""
    return copy.deepcopy(_built(case["id"]))


@functools.lru_cache(maxsize=None)
def _built(case_id):
    inp = next(iter(load_cases([case_id])))["input"]
    cfg = ClientConfig.load(inp["client_id"], Settings.from_env(ROOT))
    ds = ExcelSource(ROOT / inp["workbook"]).load(cfg)
    if inp.get("scope") == "year":
        return build_year_metrics(ds, cfg, int(inp["year"]), ROOT)
    return build_all_metrics(ds, cfg, ROOT)


def _report(doc):
    commentary = DeterministicProvider().generate(doc)
    guard = NumberGuard(doc).check(commentary).to_dict()
    return build_report(doc, commentary, "deterministic", guard)


def _failed(text, doc, case):
    others = other_clients(ROOT / "config" / "clients", case["input"]["client_id"])
    return {r["id"] for r in check_report(text, doc, case, "deterministic", others)
            if not r["passed"]}


def _insert(report, sentence):
    return re.sub(r"(?m)^## Coverage$", lambda _: f"{sentence}\n\n## Coverage", report,
                  count=1)


class TestCasesAreRegistered:
    def test_twenty_cases(self):
        assert [c["id"][:2] for c in load_cases()] == [f"{i:02d}" for i in range(1, 21)]

    def test_inputs_map_to_flags_and_paths(self):
        assert scope_args({"period": "2026-06"}) == ["--period", "2026-06"]
        assert scope_args({"scope": "year", "year": 2025}) == ["--scope", "year",
                                                                "--year", "2025"]
        assert scope_args({"scope": "all", "file": "b.xlsx"}) == ["--scope", "all",
                                                                  "--file", "b.xlsx"]
        assert output_rel({"client_id": "c", "period": "2026-06"}).as_posix() == "c/2026-06"
        assert output_rel({"client_id": "c", "scope": "year", "year": 2025}).as_posix() \
            == "c/year-2025"
        assert output_rel({"client_id": "c", "scope": "all", "file": "b.xlsx"}).as_posix() \
            == "c/b/all"


class TestDeterministicControlPasses:
    @pytest.mark.parametrize("case_id", ["14", "15", "16", "17"])
    def test_span_cases(self, case_id):
        case = _case(case_id)
        doc = _doc(case)
        assert _failed(_report(doc), doc, case) == set()

    def test_a_portable_year_report_passes_the_generic_checks(self, tmp_path):
        cfg_path = write_monthly_config(tmp_path / "config" / "clients")
        src = tmp_path / "data" / "clients" / "multi_co" / "source"
        src.mkdir(parents=True)
        months = [f"2025-{m:02d}" for m in range(1, 13)]
        build_monthly_workbook(src / "book.xlsx", months)
        cfg = ClientConfig.load_path(cfg_path)
        ds = ExcelSource(src / "book.xlsx").load(cfg)
        for doc in (build_year_metrics(ds, cfg, 2025, tmp_path),
                    build_all_metrics(ds, cfg, tmp_path)):
            results = check_report(_report(doc), doc, {"skill": {}}, "deterministic", [])
            assert [r for r in results if not r["passed"]] == []


@pytest.fixture(scope="module")
def complete():
    case = _case("14")
    doc = _doc(case)
    return case, _report(doc), doc


class TestEachCheckCatchesItsFault:

    def test_a_cause_outside_the_questions(self, complete):
        case, text, doc = complete
        failed = _failed(_insert(text, "Revenue rose because of a price increase."), doc,
                         case)
        assert {"causal_words_outside_questions", "forbidden:causal_wording"} <= failed

    def test_an_invented_total(self, complete):
        case, text, doc = complete
        assert "guard" in _failed(_insert(text, "Revenue was $642,001.00."), doc, case)

    def test_a_missing_quarter_section(self, complete):
        case, text, doc = complete
        failed = _failed(text.replace("## Quarters", "## Three-month blocks"), doc, case)
        assert "section:quarters" in failed

    def test_an_invented_year_on_year_claim(self, complete):
        case, text, doc = complete
        failed = _failed(_insert(text, "Revenue rose sharply against 2024."), doc, case)
        assert "forbidden:invented_year_on_year" in failed

    def test_an_annualised_figure(self):
        case = _case("15")
        doc = _doc(case)
        bad = _insert(_report(doc), "On an annualised basis revenue would be $732,000.00.")
        assert {"forbidden:annualised_figure", "guard"} <= _failed(bad, doc, case)

    def test_a_total_stated_over_a_gap(self):
        case = _case("16")
        doc = _doc(case)
        bad = _insert(_report(doc), "Total revenue was $600,000.00 for the year.")
        assert {"forbidden:stated_year_total", "guard"} <= _failed(bad, doc, case)

    def test_the_gap_must_be_named(self):
        case = _case("16")
        doc = _doc(case)
        text = _report(doc).replace("2025-06", "one month")
        assert "flag:gap_named" in _failed(text, doc, case)

    def test_a_seasonal_claim(self):
        case = _case("17")
        doc = _doc(case)
        bad = _insert(_report(doc), "The business shows a seasonal peak each December.")
        assert {"seasonality_not_asserted", "forbidden:seasonal_claim"} <= _failed(bad, doc,
                                                                                    case)


class TestEngineChecksCatchTheirFault:
    def _results(self, case, doc):
        r = CaseResult(case["id"])
        _check_values(r, doc, case["_expected"])
        _check_span(r, doc, case.get("engine") or {}, case["_expected"])
        return {c["id"] for c in r.checks if not c["passed"]}

    def test_the_real_document_passes(self):
        case = _case("16")
        assert self._results(case, json.loads(json.dumps(_doc(case)))) == set()

    def test_a_total_summed_over_the_gap_is_caught(self):
        case = _case("16")
        doc = copy.deepcopy(_doc(case))
        for m in doc["metrics"]:
            if m["name"] == "revenue.total":
                m.update(value=600000.0, validation_status="ok")
                m.pop("unavailable_reason", None)
        failed = self._results(case, doc)
        assert {"n/a:revenue.total", "status:revenue.total"} <= failed

    def test_a_wrong_label_or_extreme_is_caught(self):
        case = _case("15")
        doc = copy.deepcopy(_doc(case))
        doc["coverage"]["label"] = "full year, 12 of 12 months"
        doc["extremes"]["revenue.total"]["highest"]["period"] = "2025-12"
        failed = self._results(case, doc)
        assert {"coverage:label", "field:extremes.revenue.total.highest.period"} <= failed

    def test_an_annualised_metric_is_caught(self):
        case = _case("15")
        doc = copy.deepcopy(_doc(case))
        doc["metrics"].append({"name": "revenue.total.annualised", "value": 732000.0})
        assert "no_metric_matching:annuali" in self._results(case, doc)


class TestFreshClone:
    """data/clients/ is git-ignored. The workbooks cases 14 to 18 read are generated when
    missing, and what is generated must be exactly what the cases recorded."""

    def test_generated_workbooks_match_the_recorded_cells(self, tmp_path):
        sys.path.insert(0, str(ROOT / "scripts"))
        import make_test_workbooks
        from evals.common import workbook_fingerprint

        written = make_test_workbooks.ensure(tmp_path)
        assert len(written) == 4
        for case in load_cases(["14", "15", "16", "17", "18"]):
            exp = case["_expected"]
            inputs = {case["input"]["workbook"]: exp["input_content_sha256"],
                      **(exp.get("other_inputs_content_sha256") or {})}
            for rel, want in inputs.items():
                assert workbook_fingerprint(tmp_path / rel) == want, (case["id"], rel)

    def test_existing_workbooks_are_never_touched(self, tmp_path):
        sys.path.insert(0, str(ROOT / "scripts"))
        import make_test_workbooks

        make_test_workbooks.ensure(tmp_path)
        path = tmp_path / "data" / "clients" / "test_clean" / "source" / "test_clean.xlsx"
        path.write_bytes(b"a file someone already has")
        assert make_test_workbooks.ensure(tmp_path) == []
        assert path.read_bytes() == b"a file someone already has"

    def test_cases_needing_files_outside_git_and_the_generator(self):
        """Only the ABC cases: the supplied workbook is client data and is not in git."""
        sys.path.insert(0, str(ROOT / "scripts"))
        import make_test_workbooks
        generated = {f"data/clients/{c}/source/{f}" for c, f, *_ in
                     make_test_workbooks.WORKBOOKS}
        # Tracked, or not git-ignored (so it goes in with the next commit): case 19's
        # fixture is tests/fixtures/qb_pl_monthly.json.
        tracked = set(subprocess.run(["git", "ls-files", "--cached", "--others",
                                      "--exclude-standard", "data", "tests/fixtures"],
                                     cwd=ROOT, text=True,
                                     capture_output=True).stdout.split("\n"))
        needs = sorted(c["id"] for c in load_cases()
                       if c["input"].get("workbook")
                       and c["input"]["workbook"] not in generated | tracked)
        # Case 20 reads the ABC workbook too (its round-trip snapshot is written from it).
        assert needs == ["01_abc_inc_2026_06", "13_deterministic_fallback",
                         "20_quickbooks_round_trip"]
