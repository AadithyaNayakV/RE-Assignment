"""Domain types shared by every stage of the pipeline.

Values are kept at full float precision here. Rounding happens only at display time
in report.py, never in the model or in metrics.py.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from typing import Iterable


class LayoutError(Exception):
    """A sheet could not be safely interpreted against the client config.

    Raised instead of guessing. The CLI turns this into a reported failure.
    """


@dataclass(frozen=True, order=True)
class Period:
    """A calendar month. `key` ("2026-06") is the canonical identifier everywhere."""

    year: int
    month: int
    label: str = field(compare=False, default="")

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def month_name(self) -> str:
        return calendar.month_abbr[self.month]

    @property
    def display(self) -> str:
        return f"{self.month_name} {self.year}"

    def ordinal(self) -> int:
        """Months since year 0 — lets us detect gaps without date arithmetic."""
        return self.year * 12 + (self.month - 1)

    @classmethod
    def from_key(cls, key: str) -> "Period":
        m = re.fullmatch(r"(\d{4})-(\d{2})", key.strip())
        if not m:
            raise ValueError(f"Period must look like YYYY-MM, got {key!r}")
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"Month out of range in {key!r}")
        return cls(year=year, month=month, label=f"{calendar.month_abbr[month]} {year}")


@dataclass
class Cell:
    """One value, with everything needed to trace it back to the workbook."""

    sheet: str
    row: int
    column: str
    label: str
    period: str          # Period.key
    value: float | None  # None means blank/unreadable — never coerced to 0.0
    is_formula: bool = False
    formula: str | None = None
    unreadable_formula: bool = False

    def trace(self) -> dict:
        d = {
            "sheet": self.sheet,
            "row": self.row,
            "column": self.column,
            "label": self.label,
            "period": self.period,
            "original_value": self.value,
        }
        if self.is_formula:
            d["formula"] = self.formula
        return d


@dataclass
class LineItem:
    """A labelled row of the statement across all periods."""

    sheet: str
    row: int
    label: str
    section: str | None = None          # revenue | cogs | opex | None
    group: str | None = None            # subtotal group name, if a member of one
    group_key: str | None = None
    category: str | None = None         # presentation category (opex only)
    role: str = "item"                  # item | section_total | group_total | reported | header
    cells: dict[str, Cell] = field(default_factory=dict)

    def value(self, period: str) -> float | None:
        cell = self.cells.get(period)
        return cell.value if cell else None

    def trace(self, period: str) -> dict | None:
        cell = self.cells.get(period)
        return cell.trace() if cell else None

    def present_periods(self) -> list[str]:
        return [p for p, c in self.cells.items() if c.value is not None]


@dataclass
class Sheet:
    """One parsed worksheet."""

    name: str
    periods: list[Period]                                  # oldest first, always
    line_items: list[LineItem] = field(default_factory=list)
    #: Free text found above the header row (statement name, company, "as at" line).
    titles: list[str] = field(default_factory=list)

    def by_label(self, label: str) -> LineItem | None:
        target = _norm(label)
        for item in self.line_items:
            if _norm(item.label) == target:
                return item
        return None

    def all_by_label(self, label: str) -> list[LineItem]:
        target = _norm(label)
        return [i for i in self.line_items if _norm(i.label) == target]

    def in_section(self, section: str) -> list[LineItem]:
        """Every leaf line in the section, including subtotal-group members.

        These sum to the section total exactly once, because a group's members replace
        its subtotal rather than adding to it.
        """
        return [i for i in self.line_items if i.section == section and i.role == "item"]

    def direct_items(self, section: str) -> list[LineItem]:
        """Leaf lines in the section that are *not* inside a subtotal group.

        Pair with `group_totals` to reproduce the workbook's own section total.
        """
        return [
            i for i in self.line_items
            if i.section == section and i.role == "item" and i.group_key is None
        ]

    def group_members(self, group_key: str) -> list[LineItem]:
        return [
            i for i in self.line_items
            if i.group_key == group_key and i.role == "item"
        ]

    def group_totals(self, section: str) -> list[LineItem]:
        return [
            i for i in self.line_items
            if i.section == section and i.role == "group_total"
        ]

    def section_total(self, section: str) -> LineItem | None:
        for i in self.line_items:
            if i.section == section and i.role == "section_total":
                return i
        return None

    def reported(self, label: str) -> LineItem | None:
        target = _norm(label)
        for i in self.line_items:
            if i.role == "reported" and _norm(i.label) == target:
                return i
        return None

    def period_keys(self) -> list[str]:
        return [p.key for p in self.periods]


@dataclass
class Dataset:
    """Everything read from one client's source, before any calculation."""

    client_id: str
    company_name: str
    currency: str
    source_type: str
    source_path: str
    source_sha256: str
    income_statement: Sheet
    balance_sheet: Sheet | None = None

    def period_keys(self) -> list[str]:
        return self.income_statement.period_keys()

    def periods(self) -> list[Period]:
        return self.income_statement.periods


def _norm(text: str | None) -> str:
    """Compare labels forgivingly: case, whitespace and dash variants only.

    Deliberately does not strip punctuation — "Marketing" and "Marketing/Advertising"
    must stay distinct, since one is a subtotal group header and the other a line item.
    """
    if text is None:
        return ""
    s = str(text).replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip().casefold()


def find_gaps(periods: Iterable[Period]) -> list[str]:
    """Months missing from an otherwise contiguous run. Empty if contiguous."""
    ordered = sorted(periods)
    missing: list[str] = []
    for prev, nxt in zip(ordered, ordered[1:]):
        step = nxt.ordinal() - prev.ordinal()
        for k in range(1, step):
            o = prev.ordinal() + k
            missing.append(f"{o // 12:04d}-{o % 12 + 1:02d}")
    return missing


def trailing_window(all_periods: list[str], end: str, months: int) -> list[str]:
    """The `months` consecutive period keys ending at `end`, regardless of availability.

    Returns the calendar window, not the available one, so callers can tell a complete
    TTM from an incomplete one by comparing against what the dataset actually holds.
    """
    end_period = Period.from_key(end)
    want = []
    for back in range(months - 1, -1, -1):
        o = end_period.ordinal() - back
        want.append(f"{o // 12:04d}-{o % 12 + 1:02d}")
    return want
