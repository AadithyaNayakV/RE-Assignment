"""Metric naming, in one place.

Validation needs to name the exact metrics an issue affects, and `metrics.py` needs to
emit those same names. Keeping the convention in a module both can import stops the two
drifting apart — and avoids a circular import, since `metrics` already imports
`validation`.

A warning is only useful if it lands on the figures it actually concerns. "Bad Debts is
negative" is worth knowing on `opex.total` and on the Bad Debts line; attached to
`opex.line.travel` it is noise that trains a reader to ignore warnings.
"""

from __future__ import annotations

import re


def slug(text: str) -> str:
    """Lowercase, non-alphanumerics collapsed to underscores.

    `Marketing/Advertising: Ad Spend` -> `marketing_advertising_ad_spend`
    """
    s = re.sub(r"[^a-z0-9]+", "_", str(text).casefold()).strip("_")
    return s or "unnamed"


# ---- metric names ------------------------------------------------------

REVENUE_TOTAL = "revenue.total"
COGS_TOTAL = "cogs.total"
OPEX_TOTAL = "opex.total"
GROSS_PROFIT = "gross_profit"
OPERATING_INCOME = "operating_income"
NET_INCOME_REPORTED = "net_income.reported"

#: The group key used for opex lines that sit directly in the section.
UNGROUPED = "ungrouped"


def revenue_stream(label: str) -> str:
    return f"revenue.stream.{slug(label)}"


def cogs_line(label: str) -> str:
    return f"cogs.line.{slug(label)}"


def opex_line(label: str) -> str:
    return f"opex.line.{slug(label)}"


def opex_category(name: str) -> str:
    return f"opex.category.{slug(name)}"


def opex_group(key: str) -> str:
    return f"opex.group.{key}"


def section_total(section: str) -> str:
    return {"revenue": REVENUE_TOTAL, "cogs": COGS_TOTAL, "opex": OPEX_TOTAL}[section]


def line_for(section: str, label: str) -> str:
    return {"revenue": revenue_stream, "cogs": cogs_line, "opex": opex_line}[section](label)


# ---- derived metric families -------------------------------------------

def variants(base: str, *, mom: bool = True, ttm: bool = False,
             extra: tuple[str, ...] = ()) -> set[str]:
    """`base` plus the derived metrics actually emitted for it.

    Only pass `ttm=True` where a TTM metric is really emitted. Naming one that does not
    exist is harmless — it simply never matches — but it makes the intent unclear.
    """
    names = {base}
    if mom:
        names |= {f"{base}.mom_abs", f"{base}.mom_pct"}
    if ttm:
        names.add(f"{base}.ttm")
    names |= {f"{base}.{suffix}" for suffix in extra}
    return names


def metrics_including_opex_line(label: str, category: str | None,
                               group_key: str | None) -> set[str]:
    """Every metric whose value includes one particular operating expense line.

    That is: the line itself, the category and subtotal group it belongs to, and the
    section total. Deliberately *not* every `opex.*` metric — a sibling line is
    unaffected by this one.
    """
    names = variants(opex_line(label))                       # no TTM for a single line
    names |= variants(OPEX_TOTAL, ttm=True)
    if category:
        names |= variants(opex_category(category), ttm=True,
                          extra=("share_of_opex_pct",))
    # `ungrouped` carries no TTM metric; real subtotal groups do.
    key = group_key or UNGROUPED
    names |= variants(opex_group(key), ttm=group_key is not None)
    return names


def metrics_including_cogs_line(label: str) -> set[str]:
    """Every metric whose value includes one particular COGS line."""
    return variants(cogs_line(label)) | variants(COGS_TOTAL, ttm=True)


def ttm_metrics_for_section(section: str) -> set[str]:
    """The TTM metrics whose totals include a given section.

    Used to scope a warning about items inside the TTM window. An operating-expense
    one-off has no bearing on `revenue.total.ttm`, so that metric must not be tagged.
    """
    if section == "opex":
        return {f"{OPEX_TOTAL}.ttm", f"{OPERATING_INCOME}.ttm"}
    if section == "cogs":
        return {f"{COGS_TOTAL}.ttm", f"{GROSS_PROFIT}.ttm", f"{OPERATING_INCOME}.ttm"}
    if section == "revenue":
        return {f"{REVENUE_TOTAL}.ttm", f"{GROSS_PROFIT}.ttm",
                f"{OPERATING_INCOME}.ttm"}
    return set()
