"""Arithmetic and formatting primitives, against values worked out by hand.

Each expected number is written as a literal with its arithmetic in a comment, so these
tests disagree with the engine if the engine changes — which is the point.
"""

from __future__ import annotations

import pytest

from finance_engine.formatting import (money, na_with_reason, percent, points,
                                       signed_money, signed_percent)
from finance_engine.metrics import (pct_change, prior_period, ratio_pct, slug,
                                    year_ago)
from finance_engine.model import Period, find_gaps, trailing_window


class TestPeriod:
    def test_key_and_display(self):
        p = Period.from_key("2026-06")
        assert (p.year, p.month) == (2026, 6)
        assert p.key == "2026-06"
        assert p.display == "Jun 2026"

    def test_ordinal_difference_is_months(self):
        # Jan 2025 to Jun 2026 is 17 months apart.
        assert (Period.from_key("2026-06").ordinal()
                - Period.from_key("2025-01").ordinal()) == 17

    @pytest.mark.parametrize("bad", ["2026-13", "2026-00", "26-06", "2026/06", "", "x"])
    def test_rejects_malformed(self, bad):
        with pytest.raises(ValueError):
            Period.from_key(bad)

    def test_prior_period_crosses_the_year(self):
        assert prior_period("2026-01") == "2025-12"
        assert prior_period("2026-06") == "2026-05"

    def test_year_ago(self):
        assert year_ago("2026-06") == "2025-06"
        assert year_ago("2026-01") == "2025-01"


class TestGaps:
    def test_contiguous_has_no_gaps(self):
        periods = [Period.from_key(k) for k in ("2025-01", "2025-02", "2025-03")]
        assert find_gaps(periods) == []

    def test_single_missing_month_is_found(self):
        periods = [Period.from_key(k) for k in ("2025-01", "2025-03")]
        assert find_gaps(periods) == ["2025-02"]

    def test_gap_across_a_year_boundary(self):
        periods = [Period.from_key(k) for k in ("2025-11", "2026-02")]
        assert find_gaps(periods) == ["2025-12", "2026-01"]


class TestTrailingWindow:
    def test_twelve_months_ending_june_2026(self):
        window = trailing_window([], "2026-06", 12)
        assert len(window) == 12
        assert window[0] == "2025-07"      # 11 months before Jun 2026
        assert window[-1] == "2026-06"

    def test_window_is_calendar_not_availability(self):
        # Asking for a window the source cannot cover still returns the calendar months,
        # so the caller can see exactly what is missing.
        window = trailing_window(["2025-01"], "2025-06", 12)
        assert window[0] == "2024-07"
        assert window[-1] == "2025-06"

    def test_single_month_window(self):
        assert trailing_window([], "2026-06", 1) == ["2026-06"]


class TestPctChange:
    def test_plain_increase(self):
        # (200 - 150) / 150 * 100 = 33.333...
        value, reason = pct_change(200.0, 150.0)
        assert reason is None
        assert value == pytest.approx(100.0 / 3.0)

    def test_plain_decrease(self):
        # (150 - 200) / 200 * 100 = -25.0
        assert pct_change(150.0, 200.0) == (-25.0, None)

    def test_fall_to_zero_is_minus_one_hundred(self):
        # (0 - 50) / 50 * 100 = -100.0
        assert pct_change(0.0, 50.0) == (-100.0, None)

    def test_zero_base_is_refused_not_infinite(self):
        value, reason = pct_change(25.0, 0.0)
        assert value is None
        assert reason == "zero_base"

    def test_zero_to_zero_is_also_refused(self):
        assert pct_change(0.0, 0.0) == (None, "zero_base")

    def test_negative_base_is_refused(self):
        # A rise from -100 to 50 computes as -150%, which reads backwards. Refused.
        value, reason = pct_change(50.0, -100.0)
        assert value is None
        assert reason == "negative_base"

    @pytest.mark.parametrize("current,prior", [(None, 10.0), (10.0, None), (None, None)])
    def test_missing_input(self, current, prior):
        assert pct_change(current, prior) == (None, "missing_input")


class TestRatioPct:
    def test_margin(self):
        # 160 / 200 * 100 = 80.0
        assert ratio_pct(160.0, 200.0) == (80.0, None)

    def test_negative_numerator_is_allowed(self):
        # A loss-making margin is meaningful: -110 / 25 * 100 = -440.0
        value, reason = ratio_pct(-110.0, 25.0)
        assert reason is None
        assert value == pytest.approx(-440.0)

    def test_zero_denominator_refused(self):
        assert ratio_pct(10.0, 0.0) == (None, "zero_base")

    def test_negative_denominator_refused(self):
        assert ratio_pct(10.0, -5.0) == (None, "negative_base")


class TestSlug:
    @pytest.mark.parametrize("label,expected", [
        ("Usage Fees", "usage_fees"),
        ("S & M Payroll", "s_m_payroll"),
        ("Marketing/Advertising: Ad Spend", "marketing_advertising_ad_spend"),
        ("Bad Debts", "bad_debts"),
        ("  spaced  out  ", "spaced_out"),
        ("!!!", "unnamed"),
    ])
    def test_slug(self, label, expected):
        assert slug(label) == expected

    def test_distinct_labels_stay_distinct(self):
        assert slug("Marketing") != slug("Marketing/Advertising")


class TestFormatting:
    def test_money_rounds_only_for_display(self):
        # Full precision in, two decimals out. The stored value is untouched.
        assert money(195898.16) == "$195,898.16"
        assert money(58472.439999999995) == "$58,472.44"

    def test_negative_money_keeps_the_sign_outside_the_symbol(self):
        assert money(-330756.08) == "-$330,756.08"

    def test_signed_money_shows_direction(self):
        assert signed_money(17970.67) == "+$17,970.67"
        assert signed_money(-9000.0) == "-$9,000.00"
        assert signed_money(0.0) == "$0.00"

    def test_percent(self):
        assert percent(70.1516134709994) == "70.2%"
        assert percent(100.0) == "100.0%"
        assert percent(0.0) == "0.0%"

    def test_signed_percent_shows_direction(self):
        assert signed_percent(158.6966869217435) == "+158.7%"
        assert signed_percent(-5.9768383116641) == "-6.0%"

    def test_points_are_labelled(self):
        assert points(3.11019931096868) == "+3.1 pts"
        assert points(-0.5) == "-0.5 pts"

    def test_none_is_na_never_zero(self):
        assert money(None) == "n/a"
        assert percent(None) == "n/a"
        assert signed_money(None) == "n/a"
        assert points(None) == "n/a"

    def test_na_carries_its_reason(self):
        assert na_with_reason("zero_base") == "n/a (prior period was zero)"
        assert na_with_reason("incomplete_window") == "n/a (window incomplete)"
        assert na_with_reason(None) == "n/a"

    def test_no_infinity_ever_reaches_display(self):
        # The engine refuses a zero base, so an infinite percentage cannot be produced.
        value, _ = pct_change(10.0, 0.0)
        assert percent(value) == "n/a"
