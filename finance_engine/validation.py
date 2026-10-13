"""Data-quality validation.

Two severities, with different consequences:

* **error** — the affected metric is blocked. It appears in `metrics.json` with
  `validation_status: "blocked"` and a null value, never an estimate. Unrelated metrics
  still compute.
* **warning** — the metric still computes, but the warning travels with it all the way
  into the report.

Validation never repairs data. Missing is never turned into zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from finance_engine import naming
from finance_engine.config import ClientConfig
from finance_engine.model import Dataset, Period, Sheet, find_gaps, trailing_window

ERROR = "error"
WARNING = "warning"

#: `affects` sentinel for a fault that invalidates every metric in the run.
GLOBAL = "*"

_SCALE_WORDS = re.compile(r"\b(in\s+)?(thousand|thousands|million|millions|lakh|crore)\b", re.I)

#: Readable names for the structural section keys, used in warning messages.
_SECTION_NAMES = {
    "revenue": "revenue",
    "cogs": "cost of goods sold",
    "opex": "operating expense",
}


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    #: Metric names (or `name.` prefixes) this issue applies to. Empty = dataset-wide.
    affects: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)
    #: Cell-level checks only: every cell the issue is about, as
    #: {sheet, cell, label, period, raw_value}. Omitted from the dict when empty.
    cells: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.affects:
            d["affects"] = list(self.affects)
        if self.details:
            d["details"] = self.details
        if self.cells:
            d["cells"] = self.cells
        return d


class ValidationReport:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def add(self, severity: str, code: str, message: str,
            affects: tuple[str, ...] = (), cells: list[dict] | None = None,
            **details) -> None:
        self.issues.append(
            Issue(severity=severity, code=code, message=message,
                  affects=affects, details=details, cells=list(cells or []))
        )

    def error(self, code: str, message: str, affects: tuple[str, ...] = (), **d) -> None:
        self.add(ERROR, code, message, affects, **d)

    def warn(self, code: str, message: str, affects: tuple[str, ...] = (), **d) -> None:
        self.add(WARNING, code, message, affects, **d)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    def for_metric(self, name: str) -> list[Issue]:
        """Issues that apply to `name`.

        A pattern matches in one of four ways:

        * `GLOBAL` ("*") — every metric, for a fault that invalidates the whole run.
        * `"revenue.total"` — that exact metric.
        * `"revenue."` — every metric under that prefix.
        * `"*.ttm"` — every metric with that suffix. Needed because the metrics affected
          by an incomplete window are spread across dynamically named families
          (`opex.group.<key>.ttm`, `opex.category.<slug>.ttm`) while their base metrics
          must stay unaffected.
        """
        out = []
        for issue in self.issues:
            for pattern in issue.affects:
                if (
                    pattern == GLOBAL
                    or name == pattern
                    or (pattern.startswith("*") and name.endswith(pattern[1:]))
                    or (pattern.endswith(".") and name.startswith(pattern))
                ):
                    out.append(issue)
                    break
        return out

    @property
    def fatal(self) -> list[Issue]:
        """Errors that invalidate the entire run, not just one metric."""
        return [i for i in self.errors if GLOBAL in i.affects]

    def blocked(self, name: str) -> bool:
        return any(i.severity == ERROR for i in self.for_metric(name))

    def to_list(self) -> list[dict]:
        return [i.to_dict() for i in self.issues]


def validate(dataset: Dataset, config: ClientConfig, period: str) -> ValidationReport:
    """Run every check. `period` is the reporting month being asked for."""
    report = ValidationReport()
    sheet = dataset.income_statement

    _check_requested_period(report, dataset, period)
    _check_period_continuity(report, sheet, period)
    _check_ttm_completeness(report, sheet, config, period)
    _check_unreadable_formulas(report, sheet, period)
    _check_blank_vs_zero(report, sheet, period)
    _check_duplicate_labels(report, sheet)
    _check_sign_conventions(report, sheet, period, config.expected_negative_lines)
    _check_fallback_categories(report, sheet, config)
    _check_units(report, dataset, config)
    _check_balance_sheet(report, dataset, period)
    _check_cells(report, sheet, config, period)
    return report


# ---- individual checks -------------------------------------------------

def _check_requested_period(report, dataset: Dataset, period: str) -> None:
    available = dataset.period_keys()
    if period not in available:
        report.error(
            "period_not_in_source",
            f"Period {period} is not in the workbook. Available: "
            f"{available[0]}..{available[-1]} ({len(available)} months).",
            (GLOBAL,),
            requested=period,
            available_first=available[0],
            available_last=available[-1],
        )


def _check_period_continuity(report, sheet: Sheet, period: str) -> None:
    gaps = find_gaps(sheet.periods)
    if gaps:
        report.warn(
            "missing_months",
            f"The workbook's month columns are not contiguous. Missing: "
            f"{', '.join(gaps)}. Month-over-month and TTM figures that span a gap are "
            f"reported as unavailable rather than computed across it.",
            ("revenue.", "cogs.", "opex.", "gross_profit.", "operating_income.", "movement."),
            missing=gaps,
        )


def _check_ttm_completeness(report, sheet: Sheet, config: ClientConfig, period: str) -> None:
    if period not in sheet.period_keys():
        return
    months = config.ttm_months
    window = trailing_window(sheet.period_keys(), period, months)
    have = set(sheet.period_keys())
    absent = [p for p in window if p not in have]
    if absent:
        earliest = sheet.period_keys()[0]
        report.error(
            "ttm_window_incomplete",
            f"A complete {months}-month window ending {period} needs "
            f"{window[0]}..{window[-1]}, but the workbook starts at {earliest}. "
            f"Missing {len(absent)} month(s): {', '.join(absent)}. "
            f"TTM metrics are blocked rather than computed over a short window.",
            # Suffix match: every TTM metric, whatever family it belongs to, and nothing
            # else. A prefix like "opex.group." would wrongly block the monthly figures.
            ("*.ttm",),
            window_start=window[0],
            window_end=window[-1],
            missing=absent,
        )


def _check_unreadable_formulas(report, sheet: Sheet, period: str) -> None:
    bad = [
        (i.label, i.row, i.cells[period].formula)
        for i in sheet.line_items
        if period in i.cells and i.cells[period].unreadable_formula
    ]
    if bad:
        report.error(
            "formula_value_unreadable",
            f"{len(bad)} formula cell(s) for {period} have no readable cached value, so "
            f"their result cannot be used: "
            f"{'; '.join(f'{lbl} (row {row})' for lbl, row, _ in bad[:5])}"
            f"{' ...' if len(bad) > 5 else ''}. Open and re-save the workbook in Excel "
            f"to refresh cached values.",
            (GLOBAL,),
            rows=[{"label": lbl, "row": row, "formula": f} for lbl, row, f in bad],
        )


def _check_blank_vs_zero(report, sheet: Sheet, period: str) -> None:
    """A row with numbers in other months but blank in this one is suspicious.

    Blank is not zero: it may be an un-posted month. Reported, never filled in.
    """
    blanks: list[tuple[str, int, str | None]] = []
    for item in sheet.line_items:
        if item.role not in ("item", "section_total", "group_total", "reported"):
            continue
        if not item.present_periods():
            continue
        cell = item.cells.get(period)
        if cell is None or cell.value is None:
            blanks.append((item.label, item.row, item.section))
    if not blanks:
        return

    # Scoped to the sections that actually contain a blank, so a gap in operating
    # expenses does not put a warning on the revenue metrics.
    affected = tuple(sorted({f"{s}." for _, _, s in blanks if s}))
    report.warn(
        "blank_not_zero",
        f"{len(blanks)} row(s) carry values in other months but are blank for "
        f"{period}: {'; '.join(f'{l} (row {r})' for l, r, _ in blanks[:5])}"
        f"{' ...' if len(blanks) > 5 else ''}. Blank is treated as missing, not zero.",
        affected or (GLOBAL,),
        rows=[{"label": l, "row": r, "section": s} for l, r, s in blanks],
    )


def _check_duplicate_labels(report, sheet: Sheet) -> None:
    seen: dict[str, list[int]] = {}
    for item in sheet.line_items:
        if item.role != "item" or not item.label:
            continue
        seen.setdefault(item.label.casefold(), []).append(item.row)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    if dupes:
        report.warn(
            "duplicate_line_labels",
            f"{len(dupes)} line label(s) appear on more than one row: "
            f"{'; '.join(f'{k!r} rows {v}' for k, v in list(dupes.items())[:5])}. "
            f"Each row is kept separate; per-line figures are reported by row.",
            ("opex.line.", "revenue.stream."),
            labels={k: v for k, v in dupes.items()},
        )


def _check_sign_conventions(report, sheet: Sheet, period: str,
                            expected_negative: set[str] | None = None) -> None:
    """Warn about negative lines, except those the client config expects to be negative.

    The message never says what a negative line *is*. "Typically a recovery or reversal"
    was wrong for Interest Income and an Income Tax Benefit, which are negative by
    nature, and it is a guess for every other line too.
    """
    expected_negative = expected_negative or set()

    def unexpected(item) -> bool:
        return ((item.value(period) or 0) < 0
                and str(item.label).casefold() not in expected_negative)

    neg_revenue = [
        (i.label, i.row, i.value(period))
        for i in sheet.in_section("revenue")
        if unexpected(i)
    ]
    if neg_revenue:
        report.warn(
            "negative_revenue_line",
            f"{len(neg_revenue)} revenue line(s) are negative in {period}: "
            f"{'; '.join(f'{l} ({v:,.2f})' for l, _, v in neg_revenue)}. "
            f"Kept with their sign and never reclassified as an expense. Each may be a "
            f"refund, a credit or a reversal; that needs confirmation.",
            ("revenue.",),
            rows=[{"label": l, "row": r, "value": v} for l, r, v in neg_revenue],
        )
    # Scoped to the metrics that actually include the offending lines: the lines
    # themselves, their category and subtotal group, and the section total. A prefix like
    # "opex." would tag all ~270 opex metrics, putting a note about Bad Debts onto
    # opex.line.travel — which trains a reader to ignore warnings.
    for section in ("opex", "cogs"):
        negatives = [i for i in sheet.in_section(section) if unexpected(i)]
        if not negatives:
            continue

        affects: set[str] = set()
        for item in negatives:
            if section == "opex":
                affects |= naming.metrics_including_opex_line(
                    item.label, item.category, item.group_key
                )
            else:
                affects |= naming.metrics_including_cogs_line(item.label)

        described = "; ".join(
            f"{i.label} ({i.value(period):,.2f})" for i in negatives
        )
        report.warn(
            f"negative_{section}_line",
            f"{len(negatives)} {_SECTION_NAMES[section]} line(s) are negative in "
            f"{period}: {described}. Kept with their sign and never reclassified as "
            f"income. Each may be a recovery, a reversal or a misposting; that needs "
            f"confirmation. Lines the client config expects to be negative are not "
            f"listed. This warning is attached only to the figures that include these "
            f"lines.",
            tuple(sorted(affects)),
            rows=[{"label": i.label, "row": i.row, "value": i.value(period),
                   "category": i.category, "group": i.group} for i in negatives],
        )


def _check_fallback_categories(report, sheet: Sheet, config: ClientConfig) -> None:
    fallback = config.fallback_opex_category
    unmatched = [
        (i.label, i.row) for i in sheet.in_section("opex") if i.category == fallback
    ]
    if unmatched:
        report.warn(
            "opex_category_unmatched",
            f"{len(unmatched)} operating expense line(s) matched no category rule and "
            f"are grouped under {fallback!r}: "
            f"{'; '.join(f'{l} (row {r})' for l, r in unmatched[:8])}"
            f"{' ...' if len(unmatched) > 8 else ''}. Add a rule in "
            f"config/clients/{config.client_id}.yaml to classify them.",
            ("opex.category.",),
            rows=[{"label": l, "row": r} for l, r in unmatched],
        )


def _check_units(report, dataset: Dataset, config: ClientConfig) -> None:
    """Look for a stated scale ("in thousands") that would contradict the config."""
    hits = []
    for sheet in filter(None, (dataset.income_statement, dataset.balance_sheet)):
        for title in sheet.titles:
            if _SCALE_WORDS.search(title):
                hits.append(f"{sheet.name} heading: {title!r}")
        for item in sheet.line_items:
            if item.label and _SCALE_WORDS.search(item.label):
                hits.append(f"{sheet.name} row {item.row}: {item.label!r}")
    if hits and config.units_scale == 1:
        report.warn(
            "possible_unit_scaling",
            f"The workbook mentions a unit scale but the config declares "
            f"units_scale=1 (plain {config.currency}): {'; '.join(hits)}. "
            f"Confirm the scale before relying on absolute figures.",
            (GLOBAL,),
            mentions=hits,
        )


def _check_balance_sheet(report, dataset: Dataset, period: str) -> None:
    bs = dataset.balance_sheet
    if bs is None:
        report.warn(
            "no_balance_sheet",
            "No balance sheet was found in the source, so balance-sheet trends and the "
            "Assets = Liabilities + Equity check are unavailable.",
            ("balance_sheet.",),
        )
        return
    is_keys, bs_keys = set(dataset.period_keys()), set(bs.period_keys())
    if is_keys != bs_keys:
        only_is, only_bs = sorted(is_keys - bs_keys), sorted(bs_keys - is_keys)
        report.warn(
            "statement_period_mismatch",
            f"The income statement and balance sheet cover different months. "
            f"Income statement only: {only_is or 'none'}; balance sheet only: "
            f"{only_bs or 'none'}.",
            ("balance_sheet.",),
            income_statement_only=only_is,
            balance_sheet_only=only_bs,
        )
    if period not in bs_keys:
        report.error(
            "balance_sheet_period_missing",
            f"The balance sheet has no column for {period}, so balance-sheet metrics "
            f"for that month are blocked.",
            ("balance_sheet.",),
            requested=period,
        )


# ---- cell-level checks ---------------------------------------------------
#
# A text cell or an internal blank used to surface only indirectly, as a reconciliation
# failure on the total. These name the cell. They are warnings only: no value changes and
# nothing is blocked. Each is switchable per client (`data_quality.cell_checks`).
#
# Scope: leaf line items inside the revenue, COGS and operating-expense sections, in the
# months this report reads (the trailing window ending at the reporting month, plus the
# year-ago month). A problem in a month the report never uses is not raised here.

_CELL_SECTIONS = ("revenue", "cogs", "opex")
_MAX_LISTED = 5


def _check_cells(report, sheet: Sheet, config: ClientConfig, period: str) -> None:
    enabled = config.cell_checks
    if not any(enabled.values()) or period not in sheet.period_keys():
        return
    _run_cell_checks(report, sheet, config, _CellContext(sheet, config, period))


def _run_cell_checks(report, sheet: Sheet, config: ClientConfig, ctx) -> None:
    """The three cell checks over `ctx.periods`, naming `ctx.affects(...)` metrics."""
    enabled = config.cell_checks
    items = [i for s in _CELL_SECTIONS for i in sheet.in_section(s)]

    if enabled["non_numeric_cell"]:
        # Text the reader could parse ("1,234", "(500)", "$75") is a number, and text the
        # engine reads as missing is flagged. There is no separate accounting-zero rule:
        # a typed "-" is read as missing, so it is flagged too.
        found = [(i, p, i.cells[p].unreadable_text) for i in items for p in ctx.periods
                 if p in i.cells and i.cells[p].unreadable_text is not None]
        if found:
            report.warn(
                "non_numeric_cell",
                f"{len(found)} line-item cell(s) hold text that is not a number: "
                f"{ctx.listing(found, show_raw=True)}. Each is treated as missing, not "
                f"zero, so totals that include it will not reconcile until the cell "
                f"holds a number.",
                ctx.affects(found),
                cells=ctx.cell_dicts(found),
            )

    if enabled["blank_cell"]:
        keys = sheet.period_keys()
        found = []
        for item in items:
            for idx in range(1, len(keys) - 1):
                p = keys[idx]
                if p not in ctx.periods or not _is_blank(item.cells.get(p)):
                    continue
                if (item.value(keys[idx - 1]) is not None
                        and item.value(keys[idx + 1]) is not None):
                    found.append((item, p, None))
        if found:
            report.warn(
                "blank_cell",
                f"{len(found)} line-item cell(s) are blank between two months that have "
                f"values: {ctx.listing(found)}. Each is treated as missing, not zero. It "
                f"may be an un-posted month or a deleted entry; that needs confirmation.",
                ctx.affects(found),
                cells=ctx.cell_dicts(found),
            )

    if enabled["negative_line_item"]:
        expected = config.expected_negative_lines
        found = [(i, p, i.value(p)) for i in items for p in ctx.periods
                 if (i.value(p) or 0) < 0 and i.label.casefold() not in expected]
        if found:
            report.warn(
                "negative_line_item",
                f"{len(found)} revenue, cost of goods sold or operating-expense cell(s) "
                f"are negative: {ctx.listing(found, show_raw=True)}. Each may be a "
                f"legitimate credit, refund or reversal, or a misposting; that needs "
                f"confirmation. Values are kept with their sign. Lines the client config "
                f"expects to be negative are not listed.",
                ctx.affects(found),
                cells=ctx.cell_dicts(found),
            )


def _is_blank(cell) -> bool:
    """Truly empty. Text and unreadable formulas have their own checks."""
    if cell is None:
        return True
    return (cell.value is None and cell.unreadable_text is None
            and not cell.unreadable_formula)


class _CellContext:
    """Which months the report reads, and which metrics a cell in each one feeds."""

    def __init__(self, sheet: Sheet, config: ClientConfig, period: str) -> None:
        self.sheet = sheet
        self.period = period
        self.prior = _shift(period, -1)
        self.year_ago = _shift(period, -12)
        self.window = set(trailing_window(sheet.period_keys(), period, config.ttm_months))
        self.periods = [p for p in sheet.period_keys()
                        if p in self.window or p == self.year_ago]

    def address(self, item, p: str) -> str:
        column = self.sheet.period_columns.get(p) or (
            item.cells[p].column if p in item.cells else "?")
        return f"{column}{item.row}"

    def listing(self, found, show_raw: bool = False) -> str:
        parts = []
        for item, p, raw in found[:_MAX_LISTED]:
            shown = ""
            if show_raw and isinstance(raw, str):
                shown = f" = {raw!r}"
            elif show_raw and raw is not None:
                shown = f" = {raw:,.2f}"
            parts.append(f"{self.sheet.name}!{self.address(item, p)} "
                         f"({item.label}, {p}){shown}")
        more = len(found) - _MAX_LISTED
        return "; ".join(parts) + (f"; and {more} more" if more > 0 else "")

    def cell_dicts(self, found) -> list[dict]:
        return [{"sheet": self.sheet.name, "cell": self.address(item, p),
                 "label": item.label, "period": p, "raw_value": raw}
                for item, p, raw in found]

    def affects(self, found) -> tuple[str, ...]:
        names: set[str] = set()
        for item, p, _ in found:
            names |= self._dependent(item, p)
        return tuple(sorted(names))

    def _dependent(self, item, p: str) -> set[str]:
        """The metrics in this report whose value includes this cell.

        A cell in the reporting month feeds the line, its section total, category and
        group, and the profit lines below them; a cell in the prior month feeds their
        `.prior` and month-over-month figures; any cell in the window feeds the TTM
        figures; the year-ago month feeds year-on-year. Names that the engine does not
        emit for a given base (a single line has no TTM) simply never match.
        """
        section = item.section
        bases = {naming.line_for(section, item.label), naming.section_total(section),
                 naming.OPERATING_INCOME}
        if section in ("revenue", "cogs"):
            bases.add(naming.GROSS_PROFIT)
        if section == "opex":
            if item.category:
                bases.add(naming.opex_category(item.category))
            bases.add(naming.opex_group(item.group_key or naming.UNGROUPED))

        names: set[str] = set()
        suffixes: list[str] = []
        if p == self.period:
            names |= bases
            suffixes += ["mom_abs", "mom_pct"]
        if p == self.prior:
            suffixes += ["prior", "mom_abs", "mom_pct"]
        if p in self.window:
            suffixes.append("ttm")
        if p == self.year_ago:
            suffixes += ["yoy_abs", "yoy_pct"]
        names |= {f"{b}.{s}" for b in bases for s in suffixes}
        return names


def _shift(period: str, months: int) -> str:
    o = Period.from_key(period).ordinal() + months
    return f"{o // 12:04d}-{o % 12 + 1:02d}"


# ---- span validation (year and whole-file scopes) -------------------------
#
# The month checks above look at one reporting month and its window. A year or a whole
# file is many months, so the same checks run month by month here and each issue names the
# months it concerns. The rules do not change: errors block the metrics they affect,
# warnings travel with them, nothing is repaired, and blank is never zero.
#
# `affects_for(period, item)` is supplied by the span builder, which alone knows the names
# it will emit: every metric whose value includes that month (and, given a line item, that
# line). It keeps a problem in March off the figures for April.

def validate_span(dataset: Dataset, config: ClientConfig, months: list[str],
                  affects_for) -> ValidationReport:
    report = ValidationReport()
    sheet = dataset.income_statement
    months = [p for p in sheet.period_keys() if p in set(months)]

    _span_unreadable_formulas(report, sheet, months, affects_for)
    _span_blank_vs_zero(report, sheet, months, affects_for)
    _check_duplicate_labels(report, sheet)
    _span_sign_conventions(report, sheet, months, config.expected_negative_lines,
                           affects_for)
    _check_fallback_categories(report, sheet, config)
    _check_units(report, dataset, config)
    if any(config.cell_checks.values()) and months:
        _run_cell_checks(report, sheet, config,
                         _SpanCellContext(sheet, config, months, affects_for))
    return report


def _span_affects(found, affects_for) -> tuple[str, ...]:
    names: set[str] = set()
    for item, period in found:
        names |= set(affects_for(period, item))
    return tuple(sorted(names))


def _span_unreadable_formulas(report, sheet: Sheet, months, affects_for) -> None:
    for period in months:
        bad = [i for i in sheet.line_items
               if period in i.cells and i.cells[period].unreadable_formula]
        if not bad:
            continue
        report.error(
            "formula_value_unreadable",
            f"{len(bad)} formula cell(s) for {period} have no readable cached value, so "
            f"their result cannot be used: "
            f"{'; '.join(f'{i.label} (row {i.row})' for i in bad[:5])}"
            f"{' ...' if len(bad) > 5 else ''}. Every figure that includes {period} is "
            f"blocked. Open and re-save the workbook in Excel to refresh cached values.",
            tuple(sorted(set(affects_for(period, None)))),
            period=period,
            rows=[{"label": i.label, "row": i.row, "formula": i.cells[period].formula}
                  for i in bad],
        )


def _span_blank_vs_zero(report, sheet: Sheet, months, affects_for) -> None:
    found = []
    for item in sheet.line_items:
        if item.role not in ("item", "section_total", "group_total", "reported"):
            continue
        if not item.present_periods():
            continue
        for period in months:
            cell = item.cells.get(period)
            if cell is None or cell.value is None:
                found.append((item, period))
    if not found:
        return
    listed = "; ".join(f"{i.label} (row {i.row}, {p})" for i, p in found[:5])
    report.warn(
        "blank_not_zero",
        f"{len(found)} row-month(s) are blank while the row carries values in other "
        f"months: {listed}{' ...' if len(found) > 5 else ''}. Blank is treated as "
        f"missing, not zero.",
        _span_affects(found, affects_for),
        rows=[{"label": i.label, "row": i.row, "section": i.section, "period": p}
              for i, p in found],
    )


def _span_sign_conventions(report, sheet: Sheet, months, expected_negative,
                           affects_for) -> None:
    for section in ("revenue", "opex", "cogs"):
        found = [(i, p) for i in sheet.in_section(section) for p in months
                 if (i.value(p) or 0) < 0
                 and str(i.label).casefold() not in expected_negative]
        if not found:
            continue
        described = "; ".join(f"{i.label} {p} ({i.value(p):,.2f})" for i, p in found[:6])
        more = f"; and {len(found) - 6} more" if len(found) > 6 else ""
        kind = _SECTION_NAMES[section]
        report.warn(
            f"negative_{section}_line",
            f"{len(found)} {kind} line value(s) are negative in the months covered: "
            f"{described}{more}. Kept with their sign and never reclassified. Each may be "
            f"a refund, a recovery, a reversal or a misposting; that needs confirmation. "
            f"Lines the client config expects to be negative are not listed.",
            _span_affects(found, affects_for),
            rows=[{"label": i.label, "row": i.row, "period": p, "value": i.value(p),
                   "category": i.category} for i, p in found],
        )


class _SpanCellContext(_CellContext):
    """The cell checks over every month of a span, naming the span's metrics."""

    def __init__(self, sheet: Sheet, config: ClientConfig, months: list[str],
                 affects_for) -> None:
        self.sheet = sheet
        self.periods = list(months)
        self._affects_for = affects_for

    def _dependent(self, item, p: str) -> set[str]:
        return set(self._affects_for(p, item))
