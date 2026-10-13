"""One month's figures, computed the way the month report computes them.

The year and whole-file scopes are built from these. Every value uses the primitives
`MetricsBuilder` uses (`sum_present`, `ratio_pct`) over the same structural sections, so a
month's revenue inside a year report *is* the month report's `revenue.total` for that
month. `tests/test_spans.py` checks the two agree for every month of several workbooks.

Nothing here is new arithmetic: section totals are sums of the section's line items, gross
profit and operating income are differences, margins need a strictly positive revenue,
and a missing input stays missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from finance_engine import naming
from finance_engine.config import ClientConfig
from finance_engine.metrics import ratio_pct, sum_present
from finance_engine.model import Sheet

#: The five headline measures, in statement order.
HEADLINE = ("revenue.total", "cogs.total", "gross_profit", "opex.total", "operating_income")
#: Margins, each over total revenue.
MARGINS = ("gross_margin_pct", "operating_margin_pct")
MARGIN_NUMERATOR = {"gross_margin_pct": "gross_profit",
                    "operating_margin_pct": "operating_income"}
NET_INCOME = naming.NET_INCOME_REPORTED

LABELS = {
    "revenue.total": "Total revenue",
    "cogs.total": "Cost of goods sold",
    "gross_profit": "Gross profit",
    "opex.total": "Total operating expenses",
    "operating_income": "Operating income",
    "gross_margin_pct": "Gross margin",
    "operating_margin_pct": "Operating margin",
    NET_INCOME: "Net income (as reported)",
}


@dataclass
class MonthFigures:
    period: str
    values: dict[str, float | None] = field(default_factory=dict)
    #: Cell traces behind each value, in metrics.json `source_rows` form.
    traces: dict[str, list[dict]] = field(default_factory=dict)
    #: Why a value is None, where the reason is more specific than a missing input.
    reasons: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    streams: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)


def month_figures(sheet: Sheet, config: ClientConfig, period: str) -> MonthFigures:
    f = MonthFigures(period)

    revenue_items = sheet.in_section("revenue")
    revenue, rev_traces, _ = sum_present(revenue_items, period)
    f.values["revenue.total"], f.traces["revenue.total"] = revenue, rev_traces
    for item in revenue_items:
        name = naming.revenue_stream(item.label)
        cell = item.cells.get(period)
        f.values[name] = item.value(period)
        f.traces[name] = [cell.trace()] if cell else []
        f.labels[name] = item.label
        if name not in f.streams:
            f.streams.append(name)

    cogs, cogs_traces, _ = sum_present(sheet.in_section("cogs"), period)
    f.values["cogs.total"], f.traces["cogs.total"] = cogs, cogs_traces
    gross = None if revenue is None or cogs is None else revenue - cogs
    f.values["gross_profit"] = gross

    leaves = sheet.in_section("opex")
    opex, opex_traces, _ = sum_present(leaves, period)
    f.values["opex.total"], f.traces["opex.total"] = opex, opex_traces
    by_category: dict[str, list] = {}
    for item in leaves:
        by_category.setdefault(item.category or config.fallback_opex_category,
                               []).append(item)
    for category, members in sorted(by_category.items()):
        name = naming.opex_category(category)
        value, traces, _ = sum_present(members, period)
        f.values[name], f.traces[name], f.labels[name] = value, traces, category
        f.categories.append(name)

    operating = None if gross is None or opex is None else gross - opex
    f.values["operating_income"] = operating

    for margin, numerator in MARGIN_NUMERATOR.items():
        value, reason = ratio_pct(f.values[numerator], revenue)
        f.values[margin] = value
        if reason:
            f.reasons[margin] = reason

    ni_row = sheet.reported(config.reported_lines.get("net_income", ""))
    if ni_row is not None:
        cell = ni_row.cells.get(period)
        f.values[NET_INCOME] = ni_row.value(period)
        f.traces[NET_INCOME] = [cell.trace()] if cell else []
    return f
