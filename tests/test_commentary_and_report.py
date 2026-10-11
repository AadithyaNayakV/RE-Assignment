"""Commentary and report behaviour.

The properties that matter are structural, not stylistic: the provider must not calculate,
unavailable figures must read as "n/a" with a reason rather than 0 or infinity, no section
may be padded with an unsupported metric, and the reviewer checklist must always be there.
"""

from __future__ import annotations

import re

import pytest

from finance_engine.commentary.base import (CommentaryProvider, ProviderUnavailable,
                                            get_provider)
from finance_engine.commentary.deterministic import DeterministicProvider
from finance_engine.config import ClientConfig
from finance_engine.guard import check_commentary
from finance_engine.metrics import build_metrics
from finance_engine.report import build_report
from finance_engine.sources.excel import ExcelSource
from tests.helpers import build_small_workbook, write_small_config

ALL_ABC_PERIODS = [
    "2025-01", "2025-06", "2025-11", "2025-12", "2026-01", "2026-03", "2026-06",
]

#: A Python value that leaked into prose, as opposed to the English words "infinite" or
#: "none of these". Matches only when the token stands alone.
VALUE_TOKEN = re.compile(
    r"(?<![\w.])(?:[-+]?inf(?:inity)?|nan|None|null|NaN)(?![\w.])"
)


@pytest.fixture(scope="module")
def abc_commentary(abc_metrics) -> str:
    return DeterministicProvider().generate(abc_metrics)


@pytest.fixture(scope="module")
def abc_report(abc_metrics, abc_commentary) -> str:
    guard = check_commentary(abc_commentary, abc_metrics)
    return build_report(abc_metrics, abc_commentary, "deterministic", guard.to_dict())


class TestProviderResolution:
    def test_default_is_deterministic(self):
        provider = get_provider("deterministic")
        assert provider.provider_id == "deterministic"
        assert provider.always_available is True

    @pytest.mark.parametrize("name", ["", "none", "template", None])
    def test_blank_names_fall_back_to_deterministic(self, name):
        assert get_provider(name).provider_id == "deterministic"

    @pytest.mark.parametrize("name", ["ollama", "claude_api", "claude"])
    def test_documented_but_unimplemented_providers_fail_loudly(self, name):
        with pytest.raises(ProviderUnavailable, match="not implemented"):
            get_provider(name)

    def test_an_unknown_provider_does_not_silently_fall_back(self):
        with pytest.raises(ProviderUnavailable, match="Unknown"):
            get_provider("gemini")

    def test_the_interface_requires_generate(self):
        assert hasattr(CommentaryProvider, "generate")
        with pytest.raises(TypeError):
            CommentaryProvider()


class TestDeterministicCommentary:
    def test_it_passes_the_guard_for_every_tested_period(self, abc_dataset, abc_config):
        """The provider must never produce a figure it cannot source."""
        for period in ALL_ABC_PERIODS:
            doc = build_metrics(abc_dataset, abc_config, period)
            text = DeterministicProvider().generate(doc)
            result = check_commentary(text, doc)
            assert result.passed, f"{period}: {result.summary()}"

    def test_it_separates_confirmed_facts_from_possibilities(self, abc_commentary):
        assert "confirmed figures from the workbook" in abc_commentary
        lowered = abc_commentary.lower()
        assert "possible one-off" in lowered

    def test_it_never_states_a_cause(self, abc_commentary):
        lowered = abc_commentary.lower()
        assert "records no reason" in lowered
        # Causal connectives would mean the provider had explained something.
        for forbidden in (" because of ", " due to ", " caused by ", " driven by "):
            assert forbidden not in lowered, f"commentary asserted a cause: {forbidden!r}"

    def test_it_says_it_cannot_explain_why(self, abc_commentary):
        assert "cannot state why" in abc_commentary

    def test_it_carries_the_classification_note(self, abc_commentary):
        assert "Classification note" in abc_commentary
        assert "Net Income" in abc_commentary

    def test_it_reports_reconciliation_status(self, abc_commentary):
        assert "144 reconciliation checks passed" in abc_commentary

    def test_it_declines_to_assert_seasonality(self, abc_commentary):
        assert "no seasonal pattern is asserted" in abc_commentary

    def test_it_explains_a_missing_percentage_instead_of_printing_zero(self,
                                                                      abc_commentary):
        assert "zero or negative" in abc_commentary

    def test_an_incomplete_ttm_is_stated_not_substituted(self, abc_dataset, abc_config):
        doc = build_metrics(abc_dataset, abc_config, "2025-06")
        text = DeterministicProvider().generate(doc)
        assert "not available" in text.lower()
        assert "No shorter window is presented" in text

    def test_no_infinity_or_nan_ever_appears(self, abc_dataset, abc_config):
        """A figure must never render as inf/nan/None.

        The words "infinite" and "none of these" are legitimate prose, so only a
        standalone value token counts as a failure.
        """
        for period in ALL_ABC_PERIODS:
            doc = build_metrics(abc_dataset, abc_config, period)
            text = DeterministicProvider().generate(doc)
            hits = [m.group(0) for m in VALUE_TOKEN.finditer(text)]
            assert hits == [], f"{period} rendered a value as {hits}"

    def test_it_works_on_a_three_month_company(self, tmp_path):
        workbook = build_small_workbook(tmp_path / "tiny.xlsx")
        config = ClientConfig.load_path(write_small_config(tmp_path))
        doc = build_metrics(ExcelSource(workbook).load(config), config, "2025-03")
        text = DeterministicProvider().generate(doc)
        assert check_commentary(text, doc).passed, "tiny company commentary failed guard"
        assert "Trailing-twelve-month figures are not available" in text


class TestProviderDoesNotCalculate:
    def test_the_largest_stream_share_comes_from_a_metric(self, abc_metric_map):
        """The share quoted in commentary must exist as its own metric."""
        metric = abc_metric_map["revenue.stream.usage_fees.share_of_revenue_pct"]
        assert metric["value"] is not None
        assert metric["method_id"] == "margin_pct_v1"

    def test_removing_a_metric_removes_the_sentence_rather_than_faking_it(self,
                                                                         abc_metrics):
        """If a figure is absent the provider must omit it, not compute a replacement."""
        import copy
        doc = copy.deepcopy(abc_metrics)
        for metric in doc["metrics"]:
            if metric["name"].endswith(".share_of_revenue_pct"):
                metric["value"] = None
                metric["unavailable_reason"] = "missing_input"
        text = DeterministicProvider().generate(doc)
        assert "of the month's revenue" not in text
        assert check_commentary(text, doc).passed


class TestReport:
    EXPECTED_SECTIONS = [
        "Status at a glance", "Source", "Commentary", "Revenue",
        "Margins and profitability", "Operating expenses", "Month over month",
        "Trailing twelve months", "Significant movements", "Possible one-offs",
        "Balance sheet", "Reconciliation", "Data quality", "Limitations",
        "Reviewer checklist",
    ]

    def test_every_required_section_is_present(self, abc_report):
        for section in self.EXPECTED_SECTIONS:
            assert f"## {section}" in abc_report, f"missing section: {section}"

    def test_it_declares_itself_a_draft(self, abc_report):
        assert "draft for a human reviewer" in abc_report

    def test_the_reviewer_checklist_has_actionable_items(self, abc_report):
        checklist = abc_report.split("## Reviewer checklist", 1)[1]
        assert checklist.count("- [ ]") >= 5

    def test_provenance_is_recorded(self, abc_report):
        assert "SHA-256" in abc_report
        assert "never modified" in abc_report

    def test_reconciliation_results_are_shown(self, abc_report):
        assert "144" in abc_report
        assert "Tolerance 0.01" in abc_report or "tolerance 0.01" in abc_report

    def test_warnings_appear_near_the_top(self, abc_report):
        status_index = abc_report.index("## Status at a glance")
        quality_index = abc_report.index("## Data quality")
        assert status_index < quality_index
        assert "Data-quality warnings" in abc_report[:quality_index]

    def test_the_guard_verdict_is_surfaced(self, abc_report):
        assert "Commentary number check" in abc_report
        assert "numbers traced to metrics.json" in abc_report

    def test_no_section_is_padded_when_data_is_missing(self, tmp_path):
        """A three-month company has no balance sheet and no TTM. Both must say so."""
        workbook = build_small_workbook(tmp_path / "tiny.xlsx")
        config = ClientConfig.load_path(write_small_config(tmp_path))
        doc = build_metrics(ExcelSource(workbook).load(config), config, "2025-03")
        report = build_report(doc, DeterministicProvider().generate(doc))
        assert "## Balance sheet" in report
        assert "No supporting data" in report
        assert "**Not available.**" in report

    def test_unavailable_values_render_as_na_with_a_reason(self, tmp_path):
        workbook = build_small_workbook(tmp_path / "tiny.xlsx")
        config = ClientConfig.load_path(write_small_config(tmp_path))
        doc = build_metrics(ExcelSource(workbook).load(config), config, "2025-03")
        report = build_report(doc, "")
        assert "n/a" in report
        # "infinite" appears in the report's own explanation of why a percentage is
        # withheld, so only a standalone value token is forbidden.
        assert [m.group(0) for m in VALUE_TOKEN.finditer(report)] == []

    def test_a_failed_guard_is_announced_in_the_report(self, abc_metrics):
        bad = "Revenue was $999,999.99 last month."
        guard = check_commentary(bad, abc_metrics)
        report = build_report(abc_metrics, bad, "deterministic", guard.to_dict())
        assert "Number check rejected this commentary" in report
        assert "REJECTED" in report

    def test_report_is_markdown_and_non_trivial(self, abc_report):
        assert abc_report.startswith("# ABC Inc")
        assert abc_report.endswith("\n")
        assert len(abc_report.splitlines()) > 100

    def test_tables_are_closed(self, abc_report):
        """Every table row has a consistent cell count within its block."""
        block: list[str] = []
        for line in abc_report.splitlines() + [""]:
            if line.startswith("|"):
                block.append(line)
                continue
            if len(block) >= 2:
                widths = {row.count("|") for row in block}
                assert len(widths) == 1, f"ragged table: {block[0]}"
            block = []
