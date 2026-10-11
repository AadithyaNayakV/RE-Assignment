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
        return d


class ValidationReport:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def add(self, severity: str, code: str, message: str,
            affects: tuple[str, ...] = (), **details) -> None:
        self.issues.append(
            Issue(severity=severity, code=code, message=message,
                  affects=affects, details=details)
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
