"""Display formatting. The only place numbers are rounded.

Python owns every character of every figure that reaches a reader. A commentary provider
is handed pre-formatted strings, never raw floats to format itself, so a provider cannot
change a number's precision, scale or sign while "just writing it up".

An unavailable value is always rendered as "n/a" with its reason available alongside —
never as 0, a dash, or an infinite percentage.
"""

from __future__ import annotations

NA = "n/a"

_REASON_TEXT = {
    "zero_base": "prior period was zero",
    "negative_base": "prior period was negative",
    "sign_change": "crossed zero",
    "missing_input": "no comparable value",
    "incomplete_window": "window incomplete",
    "blocked_by_validation": "blocked by a data-quality error",
    "not_available": "not present in the source",
}


def money(value: float | None, symbol: str = "$", decimals: int = 2) -> str:
    if value is None:
        return NA
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):,.{decimals}f}"


def signed_money(value: float | None, symbol: str = "$", decimals: int = 2) -> str:
    """For changes, where the direction is the point."""
    if value is None:
        return NA
    sign = "+" if value > 0 else ("-" if value < 0 else "")
    return f"{sign}{symbol}{abs(value):,.{decimals}f}"


def percent(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return NA
    return f"{value:,.{decimals}f}%"


def signed_percent(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return NA
    sign = "+" if value > 0 else ("-" if value < 0 else "")
    return f"{sign}{abs(value):,.{decimals}f}%"


def points(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return NA
    sign = "+" if value > 0 else ("-" if value < 0 else "")
    return f"{sign}{abs(value):,.{decimals}f} pts"


def reason(code: str | None) -> str:
    if not code:
        return ""
    return _REASON_TEXT.get(code, code.replace("_", " "))


def na_with_reason(code: str | None) -> str:
    r = reason(code)
    return f"{NA} ({r})" if r else NA


class Formatter:
    """Formats values for one client, honouring its currency symbol."""

    def __init__(self, currency_symbol: str = "$", currency: str = "USD"):
        self.symbol = currency_symbol or ""
        self.currency = currency

    def money(self, v: float | None, decimals: int = 2) -> str:
        return money(v, self.symbol, decimals)

    def signed_money(self, v: float | None, decimals: int = 2) -> str:
        return signed_money(v, self.symbol, decimals)

    def percent(self, v: float | None, decimals: int = 1) -> str:
        return percent(v, decimals)

    def signed_percent(self, v: float | None, decimals: int = 1) -> str:
        return signed_percent(v, decimals)

    def points(self, v: float | None, decimals: int = 1) -> str:
        return points(v, decimals)

    def metric(self, doc_metric: dict | None, decimals: int | None = None) -> str:
        """Format a metric dict by its declared unit, falling back to n/a with a reason."""
        if doc_metric is None:
            return NA
        value, unit = doc_metric.get("value"), doc_metric.get("unit")
        if value is None:
            return na_with_reason(doc_metric.get("unavailable_reason"))
        if unit == "percent":
            return self.percent(value, 1 if decimals is None else decimals)
        if unit == "percentage_points":
            return self.points(value, 1 if decimals is None else decimals)
        if unit == "count":
            return f"{value:,.0f}"
        return self.money(value, 2 if decimals is None else decimals)

    def metric_signed(self, doc_metric: dict | None, decimals: int | None = None) -> str:
        if doc_metric is None:
            return NA
        value, unit = doc_metric.get("value"), doc_metric.get("unit")
        if value is None:
            return na_with_reason(doc_metric.get("unavailable_reason"))
        if unit == "percent":
            return self.signed_percent(value, 1 if decimals is None else decimals)
        if unit == "percentage_points":
            return self.points(value, 1 if decimals is None else decimals)
        return self.signed_money(value, 2 if decimals is None else decimals)
