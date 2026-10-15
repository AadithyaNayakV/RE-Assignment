"""The number guard: accepts figures that trace to metrics.json, rejects the rest.

The guard is the control that makes a language-model commentary provider safe to add
later, so these tests also pin down its *limits*. The false-pass rates measured in
`TestMeasuredLimits` are quoted in docs/metrics_schema.md; if they move, the
documentation is wrong and the tests should fail.
"""

from __future__ import annotations

import random

import pytest

from finance_engine.guard import (CURRENCY_BUCKET, PERCENT_BUCKET, STRUCTURAL_BUCKET,
                                  NumberGuard, _NUMBER, _parse_match, check_commentary,
                                  collect_allowed_values)


@pytest.fixture(scope="module")
def guard(abc_metrics) -> NumberGuard:
    return NumberGuard(abc_metrics)


class TestNumberParsing:
    @pytest.mark.parametrize("text,expected", [
        ("195898.16", 195898.16),
        ("$195,898.16", 195898.16),
        ("195,898.16", 195898.16),
        ("-$330,756.08", -330756.08),
        ("(1,234.56)", -1234.56),           # accounting negative
        ("70.2%", 70.2),
        ("+158.7%", 158.7),
        ("$2.3M", 2_300_000.0),
        ("$196k", 196_000.0),
        ("2.3 million", 2_300_000.0),
    ])
    def test_values_are_parsed(self, text, expected):
        match = _NUMBER.search(text)
        value, _, _ = _parse_match(match)
        assert value == pytest.approx(expected)

    def test_brackets_with_an_explicit_plus_stay_positive(self):
        """"(+158.7%)" is a positive figure in brackets, not an accounting negative."""
        value, _, kind = _parse_match(_NUMBER.search("(+158.7%)"))
        assert value == pytest.approx(158.7)
        assert kind == PERCENT_BUCKET

    def test_precision_sets_the_tolerance(self):
        # A number written to one decimal is held to half of that last digit.
        _, tol_one_dp, _ = _parse_match(_NUMBER.search("70.2%"))
        _, tol_two_dp, _ = _parse_match(_NUMBER.search("70.15%"))
        assert tol_one_dp == pytest.approx(0.05)
        assert tol_two_dp < tol_one_dp

    def test_a_magnitude_suffix_widens_the_tolerance(self):
        _, tol, _ = _parse_match(_NUMBER.search("$2.3M"))
        assert tol == pytest.approx(50_000.0)   # half of 0.1M

    @pytest.mark.parametrize("text,kind", [
        ("70.2%", PERCENT_BUCKET),
        ("$195,898.16", CURRENCY_BUCKET),
        ("195,898.16", CURRENCY_BUCKET),        # thousands separator implies money
        ("$2.3M", CURRENCY_BUCKET),
        ("18", "any"),
    ])
    def test_quantity_kind_is_inferred_from_how_it_was_written(self, text, kind):
        _, _, parsed_kind = _parse_match(_NUMBER.search(text))
        assert parsed_kind == kind


class TestAllowedValues:
    def test_buckets_are_populated_from_the_document(self, abc_metrics):
        buckets = collect_allowed_values(abc_metrics)
        assert len(buckets[CURRENCY_BUCKET]) > 100
        assert len(buckets[PERCENT_BUCKET]) > 10
        assert len(buckets[STRUCTURAL_BUCKET]) > 5

    def test_both_signs_are_allowed_for_prose(self, abc_metrics):
        # Commentary writes "fell 6.0%" where the metric is -5.98.
        buckets = collect_allowed_values(abc_metrics)
        assert any(abs(v - 5.9768383116641) < 0.001 for v in buckets[PERCENT_BUCKET])
        assert any(abs(v + 5.9768383116641) < 0.001 for v in buckets[PERCENT_BUCKET])

    def test_an_empty_document_allows_no_financial_values(self):
        """An empty document supports no amount and no percentage.

        The structural bucket is not empty, because "0 metrics" and "0 warnings" are
        true statements about an empty document. What matters is that no currency or
        percentage figure can be sourced from it.
        """
        buckets = collect_allowed_values({})
        assert buckets[CURRENCY_BUCKET] == set()
        assert buckets[PERCENT_BUCKET] == set()
        assert buckets[STRUCTURAL_BUCKET] == {0.0}

    def test_every_number_in_a_text_is_rejected_when_nothing_is_allowed(self):
        result = check_commentary("Revenue was $100.00.", {})
        assert not result.passed
        assert result.checked == 1


class TestAcceptsSupportedFigures:
    def test_exact_metric_values_pass(self, guard):
        text = ("Total revenue was $195,898.16 and gross profit $137,425.72, "
                "with operating income of -$330,756.08.")
        result = guard.check(text)
        assert result.passed, result.summary()
        assert result.checked == 3

    def test_rounded_percentages_pass(self, guard):
        # Stored as 70.1516...; written to one decimal.
        result = guard.check("Gross margin was 70.2%.")
        assert result.passed, result.summary()

    def test_magnitude_shorthand_passes(self, guard):
        # TTM revenue is 2,303,975.51.
        assert guard.check("Trailing revenue is about $2.3M.").passed

    def test_source_row_values_pass(self, guard):
        # Usage Fees for Jun 2026, a traced source row rather than a headline metric.
        assert guard.check("Usage Fees contributed $166,781.50.").passed

    def test_structural_numbers_pass(self, guard):
        assert guard.check("The source covers 18 months across 144 checks.").passed

    def test_row_and_date_references_are_not_treated_as_claims(self, guard):
        text = ("See row 15 and rows 29-46 for detail; the period is 2026-06, "
                "covering Jun 2026 and 30 Jun 2026.")
        result = guard.check(text)
        assert result.passed, result.summary()

    def test_the_deterministic_provider_passes_its_own_output(self, abc_metrics):
        from finance_engine.commentary.deterministic import DeterministicProvider
        text = DeterministicProvider().generate(abc_metrics)
        result = check_commentary(text, abc_metrics)
        assert result.passed, result.summary()
        assert result.checked > 20, "the commentary should contain real figures"


class TestRejectsInventedFigures:
    def test_an_invented_currency_amount_is_rejected(self, guard):
        result = guard.check("Marketing spend was $88,500.00 last month.")
        assert not result.passed
        assert len(result.unsupported) == 1
        assert result.unsupported[0].value == pytest.approx(88500.0)

    def test_an_invented_large_amount_is_rejected(self, guard):
        result = guard.check("The pipeline is worth $1,250,000.")
        assert not result.passed

    def test_a_plausible_but_wrong_total_is_rejected(self, guard):
        # One digit different from the real $195,898.16.
        result = guard.check("Total revenue was $195,998.16.")
        assert not result.passed

    def test_the_rejection_names_the_closest_supported_value(self, guard):
        result = guard.check("Marketing spend was $88,500.00.")
        finding = result.unsupported[0]
        assert finding.closest_allowed is not None
        assert finding.closest_difference > finding.tolerance
        assert "88,500.00" in result.summary()
        assert "closest supported value" in result.summary()

    def test_a_mix_reports_only_the_unsupported_ones(self, guard):
        result = guard.check("Revenue was $195,898.16 but forecast is $450,000.00.")
        assert not result.passed
        assert result.checked == 2
        assert len(result.supported) == 1
        assert len(result.unsupported) == 1

    def test_a_currency_figure_cannot_be_rescued_by_a_percentage(self, guard):
        # 70.15 exists as a percentage; "$70.15" must not borrow it.
        result = guard.check("The fee was $70.15.")
        assert not result.passed


class TestProviderAgnostic:
    def test_the_guard_does_not_inspect_who_wrote_the_text(self, abc_metrics):
        """Identical text must get an identical verdict regardless of origin."""
        text = "Revenue was $195,898.16 and the forecast is $999,999.00."
        first = check_commentary(text, abc_metrics)
        second = NumberGuard(abc_metrics).check(text)
        assert first.passed == second.passed is False
        assert len(first.unsupported) == len(second.unsupported) == 1

    def test_result_serialises_for_the_report(self, guard):
        payload = guard.check("Revenue was $195,898.16.").to_dict()
        assert payload["passed"] is True
        assert payload["numbers_checked"] == 1
        assert "notes" in payload and payload["notes"]

    def test_limits_are_stated_in_the_result(self, guard):
        notes = " ".join(guard.check("Revenue was $195,898.16.").notes).lower()
        assert "sign" in notes          # magnitudes compared, not direction
        assert "right sentence" in notes


class TestMeasuredLimits:
    """Quantified false-pass rates. These are documented, not hidden.

    The guard is strong on precisely written currency and weak on loosely rounded
    figures, because hundreds of legitimate percentages densely cover 0-100.
    """

    def test_exact_currency_is_effectively_airtight(self, guard):
        rng = random.Random(7)
        trials = 2000
        passes = sum(
            1 for _ in range(trials)
            if guard.check(f"${rng.randrange(0, 50_000_000) / 100:,.2f}").passed
        )
        rate = passes / trials
        assert rate < 0.01, f"false-pass rate for exact currency was {rate:.3f}"

    def test_one_decimal_percentages_are_weakly_constrained(self, guard):
        rng = random.Random(7)
        trials = 2000
        passes = sum(
            1 for _ in range(trials)
            if guard.check(f"{rng.randrange(0, 1001) / 10:.1f}%").passed
        )
        rate = passes / trials
        # Documented as roughly 15%. Asserting a band, so a big change is noticed.
        assert 0.05 < rate < 0.30, f"false-pass rate for 1dp percentages was {rate:.3f}"

    def test_a_number_cannot_pass_outside_every_bucket(self, guard):
        # Far from any real figure in the workbook.
        assert not guard.check("$77,777,777.77").passed
        assert not guard.check("$0.07").passed


class TestSpansFoundByTheEvalSuite:
    """Gaps the Phase 3 evals exposed by running the guard over a whole report.md."""

    def test_iso_timestamp_is_not_a_figure(self, abc_metrics):
        result = NumberGuard(abc_metrics).check("**Generated:** 2026-10-11T10:48:47+00:00")
        assert result.checked == 0, result.summary()

    def test_sha256_label_and_digest_are_not_figures(self, abc_metrics):
        text = ("- **SHA-256:** `75da8d679a89551f9e46b3563b7cfc724d9aaeabe0444b0214d455a0b3"
                "35a0ea` and a digest ending in digits `a1b2c3d4e5f60718293a5847036`")
        result = NumberGuard(abc_metrics).check(text)
        assert result.checked == 0, result.summary()

    def test_the_guards_own_counts_are_not_figures(self, abc_metrics):
        text = ("| Commentary number check | PASS | 30/30 numbers traced to metrics.json |\n"
                "_All 30 numbers in this section were checked._\n"
                "PASS — 12/12 number(s) traced to metrics.json.")
        result = NumberGuard(abc_metrics).check(text)
        assert result.checked == 0, result.summary()

    def test_magnitudes_are_compared_in_both_directions(self, abc_metrics):
        # Documented behaviour: the guard does not judge signs. A negative written
        # figure for a positive metric used to be rejected, while the reverse passed.
        assert NumberGuard(abc_metrics).check("Revenue was -$195,898.16.").passed
        assert NumberGuard(abc_metrics).check("Revenue was $195,898.16.").passed

    def test_a_wrong_figure_is_still_rejected_whatever_its_sign(self, abc_metrics):
        for text in ("Revenue was $195,998.16.", "Revenue was -$195,998.16."):
            assert not NumberGuard(abc_metrics).check(text).passed


class TestDirectionWords:
    """A direction word next to a change figure must agree with the change's sign.

    Revenue fell -12,452.80 in Jun 2026; gross margin rose +3.1 pts; operating income
    rose +8,343.52 (the operating loss narrowed).
    """

    @pytest.mark.parametrize("text", [
        "Revenue fell $12,452.80 (-6.0%).",          # the house style: unsigned amount
        "Revenue was down $12,452.80.",
        "Revenue decreased by $12,452.80.",
        "Gross margin rose 3.1 pts.",
        "The operating loss narrowed by $8,343.52.",  # loss narrowing = income up
        "Operating income rose $8,343.52.",
        "Revenue fell to $195,898.16.",               # a level, not a change: unchecked
    ])
    def test_right_direction_passes(self, abc_metrics, text):
        result = NumberGuard(abc_metrics).check(text)
        assert result.passed, result.summary()

    @pytest.mark.parametrize("text", [
        "Revenue rose $12,452.80.",
        "Revenue increased by $12,452.80.",
        "Gross margin fell 3.1 pts.",
        "Gross margin narrowed 3.1 pts.",
        "The operating loss widened by $8,343.52.",
        "Operating income was down $8,343.52.",
    ])
    def test_wrong_direction_is_rejected(self, abc_metrics, text):
        result = NumberGuard(abc_metrics).check(text)
        assert not result.passed
        assert result.direction_errors and not result.unsupported
        assert "contradict" in result.summary()

    def test_a_parenthetical_inherits_the_direction(self, abc_metrics):
        result = NumberGuard(abc_metrics).check("Revenue fell $12,452.80 (+6.0%).")
        assert not result.passed
        assert "written sign '+'" in result.direction_errors[0]["problem"]

    def test_the_unsigned_amount_is_accepted(self, abc_metrics):
        result = NumberGuard(abc_metrics).check("Usage Fees fell $11,142.74 (-6.3%).")
        # Only the amount gets a verdict: 6.3% matches Usage Fees (-6.3%) *and*
        # Software, Hosting & AI (+6.3%), so the percentage is ambiguous and unchecked.
        assert result.passed and result.directions_checked == 1

    def test_cli_rejects_a_wrong_direction(self, tmp_path, abc_metrics, capsys):
        import json
        from finance_engine.cli import EXIT_GUARD, main
        out = tmp_path / "output" / "clients" / "abc_inc" / "2026-06"
        out.mkdir(parents=True)
        (out / "metrics.json").write_text(json.dumps(abc_metrics), encoding="utf-8")
        note = tmp_path / "note.md"
        note.write_text("Revenue rose $12,452.80.", encoding="utf-8")
        code = main(["check-commentary", "--client", "abc_inc", "--period", "2026-06",
                     "--file", str(note), "--root", str(tmp_path)])
        assert code == EXIT_GUARD
        assert "direction word" in capsys.readouterr().out


class TestRoundingHalfSteps:
    """A figure on an exact half-step may be rounded either way; nothing wider passes.

    `|0.8 - 0.75| <= 0.05` is False in floating point (0.05000000000000004), so a
    correctly rounded "0.8%" used to be rejected. The rule now: the written figure must be
    within the precision tolerance, or be one of the two neighbouring roundings of the
    stored value's exact binary value, computed in exact decimal arithmetic.
    """

    @staticmethod
    def _doc(value, name="x.level_pct", unit="percent"):
        return {"metrics": [{"name": name, "value": value, "unit": unit}]}

    @pytest.mark.parametrize("stored, written", [
        (0.75, "0.7%"), (0.75, "0.8%"), (12.75, "12.7%"), (12.75, "12.8%"),
        (1.125, "1.12%"), (1.125, "1.13%"),
    ])
    def test_a_half_step_accepts_both_neighbouring_roundings(self, stored, written):
        assert NumberGuard(self._doc(stored)).check(f"It was {written}.").passed

    @pytest.mark.parametrize("stored, written", [
        (0.75, "0.6%"), (0.75, "0.9%"), (12.75, "12.6%"), (12.75, "12.9%"),
        (12.75, "14%"), (1.125, "1.11%"), (1.125, "1.14%"),
    ])
    def test_a_half_step_accepts_nothing_wider(self, stored, written):
        """At two or more decimals the existing one-cent floor (MIN_TOLERANCE, 0.011) is
        still the bar, unchanged: 0.74% against 0.75 passed before and still does."""
        assert not NumberGuard(self._doc(stored)).check(f"It was {written}.").passed

    @pytest.mark.parametrize("stored, ok, rejected", [
        (0.7500000001, "0.8%", "0.7%"),
        (0.7499999999, "0.7%", "0.8%"),
    ])
    def test_just_off_a_half_step_only_the_nearest_rounding_passes(self, stored, ok,
                                                                  rejected):
        """A blanket float slack (1e-9) would have let the second rounding through."""
        guard = NumberGuard(self._doc(stored))
        assert guard.check(f"It was {ok}.").passed
        assert not guard.check(f"It was {rejected}.").passed

    def test_the_stored_binary_value_decides(self):
        """0.15 is stored as 0.1499999999999999944...: only "0.1" is its rounding."""
        guard = NumberGuard(self._doc(0.15))
        assert f"{0.15:.1f}" == "0.1"
        assert guard.check("It was 0.1%.").passed
        assert not guard.check("It was 0.2%.").passed

    def test_the_formatters_own_rounding_always_passes(self):
        from finance_engine.formatting import percent
        for k in range(0, 2001):
            stored = k / 40                                # 0.025 steps: many half-steps
            text = f"It was {percent(stored)}."
            assert NumberGuard(self._doc(stored)).check(text).passed, (stored, text)

    def test_the_direction_check_uses_the_same_rule(self):
        guard = NumberGuard(self._doc(-0.75, name="x.mom_pct"))
        result = guard.check("It fell 0.8%.")
        assert result.passed and result.directions_checked == 1
        wrong = guard.check("It rose 0.8%.")
        assert not wrong.passed and wrong.direction_errors
        off = NumberGuard(self._doc(-0.7500000001, name="x.mom_pct")).check("It fell 0.7%.")
        assert not off.passed and off.unsupported


class TestCausalWording:
    """check-commentary rejects causal wording outside "Questions for the reviewer",
    with the word list the eval `causal_words_outside_questions` uses
    (`finance_engine.wording.CAUSAL_WORDS`). A skill run passed the guard with this exact
    sentence and failed the eval (evals/runs/skill_2026-10-15T06-13-34Z, case 01)."""

    SENTENCE = ("- **The operating loss narrowed by $8,343.52 to -$330,756.08, yet operating "
                "margin fell 6.1 pts to -168.8%.** Both are true because revenue fell "
                "proportionally faster than the loss narrowed.")

    def test_the_slipped_sentence_is_rejected(self, guard):
        result = guard.check(self.SENTENCE)
        assert not result.unsupported and not result.direction_errors   # numbers are fine
        assert not result.passed
        assert result.causal_wording and "because" in result.causal_wording[0]
        assert "causal wording" in result.summary()
        assert result.to_dict()["causal_wording"] == result.causal_wording

    def test_the_same_sentence_without_a_cause_passes(self, guard):
        fixed = ("- **The operating loss narrowed by $8,343.52 to -$330,756.08, while "
                 "operating margin fell 6.1 pts to -168.8%.**")
        assert guard.check(fixed).passed

    def test_a_cause_as_a_reviewer_question_is_allowed(self, guard):
        text = ("## Questions for the reviewer\n\n"
                "- Did margin fall because of pricing? Possible; needs confirmation.\n")
        assert guard.check(text).passed

    def test_guard_and_eval_share_one_list(self):
        import finance_engine.guard as guard_module
        from finance_engine import wording
        from evals import report_checks
        assert guard_module.causal_units is wording.causal_units
        assert report_checks.CAUSAL_WORDS is wording.CAUSAL_WORDS
