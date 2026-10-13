"""All calculations. The only place numbers are produced.

Every figure that reaches a report or a commentary provider is a `Metric` built here,
carrying its own provenance: which rows it came from, which method computed it, and
whether validation blocked it.

Conventions that matter for financial correctness:

* Full float precision is kept. Rounding happens only in `report.py`.
* A percentage change needs a strictly positive base. A zero base, a missing base, or a
  negative base yields `None` with a stated reason, never infinity and never a figure
  that would read backwards.
* A TTM figure requires every month in the window. A short window is blocked, never
  presented as a twelve-month number.
* Missing is never zero. A metric whose inputs are entirely absent is `None`.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

from finance_engine import SCHEMA_VERSION
from finance_engine import naming
from finance_engine.config import ClientConfig
from finance_engine.model import Dataset, LineItem, Period, Sheet, trailing_window
from finance_engine.reconciliation import ReconciliationReport, reconcile
from finance_engine.validation import ValidationReport, validate

CURRENCY = "currency"
PERCENT = "percent"
PERCENTAGE_POINTS = "percentage_points"
COUNT = "count"

#: Documented in docs/metrics_schema.md. Each value names one calculation.
METHODS = {
    "sum_section_items_v1": "Sum of the section's leaf line items for the period.",
    "sum_group_members_v1": "Sum of a subtotal group's member rows.",
    "sum_category_items_v1": "Sum of the lines assigned to a presentation category.",
    "line_value_v1": "A single line item's value, read directly from the source.",
    "workbook_reported_v1": "A value the workbook derives itself, read from its cached result.",
    "difference_v1": "One computed metric minus another.",
    "margin_pct_v1": "numerator / denominator x 100, requiring a strictly positive denominator.",
    "mom_abs_v1": "Current period minus the immediately preceding period.",
    "prior_period_value_v1": "The same measure for the immediately preceding period, "
                             "as the month-over-month comparison used it.",
    "mom_pct_v1": "(current - prior) / prior x 100, requiring a strictly positive prior.",
    "pct_points_delta_v1": "Current percentage minus prior percentage, in percentage points.",
    "yoy_abs_v1": "Current period minus the same month one year earlier.",
    "yoy_pct_v1": "(current - year-ago) / year-ago x 100, requiring a strictly positive base.",
    "ttm_sum_v1": "Sum across a complete trailing twelve-month window.",
}

OK, WARNING, BLOCKED = "ok", "warning", "blocked"

#: Readable section names, for warning messages that have to say which part of the
#: statement an item came from.
_SECTION_PHRASES = {
    "opex": "operating expense",
    "cogs": "cost of goods sold",
    "revenue": "revenue",
}


@dataclass
class Metric:
    name: str
    client_id: str
    period: str
    value: float | None
    unit: str
    method_id: str
    source_rows: list[dict] = field(default_factory=list)
    validation_status: str = OK
    warnings: list[str] = field(default_factory=list)
    #: Why a value is absent: zero_base, negative_base, missing_input, incomplete_window,
    #: blocked_by_validation, not_available.
    unavailable_reason: str | None = None
    #: The workbook's own figure for the same concept, where one exists. Recorded for
    #: transparency; never used as a calculation input.
    reported_value: float | None = None
    label: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "client_id": self.client_id,
            "period": self.period,
            "value": self.value,
            "unit": self.unit,
            "method_id": self.method_id,
            "validation_status": self.validation_status,
            "source_rows": self.source_rows,
            "warnings": self.warnings,
        }
        if self.label:
            d["label"] = self.label
        if self.unavailable_reason:
            d["unavailable_reason"] = self.unavailable_reason
        if self.reported_value is not None:
            d["reported_value"] = self.reported_value
        if self.notes:
            d["notes"] = self.notes
        return d


class MetricSet:
    """An ordered, name-addressable collection of metrics."""

    def __init__(self) -> None:
        self._items: dict[str, Metric] = {}

    def add(self, metric: Metric) -> Metric:
        self._items[metric.name] = metric
        return metric

    def get(self, name: str) -> Metric | None:
        return self._items.get(name)

    def value(self, name: str) -> float | None:
        m = self._items.get(name)
        return m.value if m else None

    def __iter__(self):
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def names(self) -> list[str]:
        return list(self._items)

    def to_list(self) -> list[dict]:
        return [m.to_dict() for m in self._items.values()]


# ---- arithmetic primitives ---------------------------------------------

def sum_present(items: list[LineItem], period: str) -> tuple[float | None, list[dict], int]:
    """Sum the line items that have a value. Returns (total, traces, missing_count)."""
    total, traces, missing = 0.0, [], 0
    for item in items:
        cell = item.cells.get(period)
        if cell is None or cell.value is None:
            missing += 1
            continue
        total += cell.value
        traces.append(cell.trace())
    return (total if traces else None), traces, missing


def pct_change(current: float | None, prior: float | None) -> tuple[float | None, str | None]:
    """Percentage change, or `None` with a reason.

    Four cases are refused rather than reported as a number:

    * `zero_base` — a base of zero has no defined percentage change.
    * `negative_base` — the figure would read backwards, a worsening looking like a gain.
    * `sign_change` — the line crossed zero. Bad Debts going from +12,935 to -1,806 is a
      swing from a charge to a credit; calling that "-113.96%" invites the reader to treat
      it as a 114% reduction in a cost that still exists. The absolute change and the
      sign change are the real information.
    * `missing_input` — no comparable value.

    A fall to exactly zero stays -100%, which is both well defined and what a reader
    expects.
    """
    if current is None or prior is None:
        return None, "missing_input"
    if prior == 0:
        return None, "zero_base"
    if prior < 0:
        return None, "negative_base"
    if current < 0:
        return None, "sign_change"
    return (current - prior) / prior * 100.0, None


def crosses_zero(current: float | None, prior: float | None) -> bool:
    """True when a line moved from a charge to a credit or back."""
    if current is None or prior is None:
        return False
    return (prior > 0 > current) or (prior < 0 < current)


def ratio_pct(numerator: float | None,
              denominator: float | None) -> tuple[float | None, str | None]:
    """A margin. Requires a strictly positive denominator."""
    if numerator is None or denominator is None:
        return None, "missing_input"
    if denominator == 0:
        return None, "zero_base"
    if denominator < 0:
        return None, "negative_base"
    return numerator / denominator * 100.0, None


def prior_period(period: str) -> str:
    o = Period.from_key(period).ordinal() - 1
    return f"{o // 12:04d}-{o % 12 + 1:02d}"


def year_ago(period: str) -> str:
    p = Period.from_key(period)
    return f"{p.year - 1:04d}-{p.month:02d}"


#: Re-exported from `naming`, which both this module and `validation` import so the
#: metric-name convention has exactly one definition.
slug = naming.slug


def relative_path(path: str | Path, root: Path | None) -> str:
    """A project-relative POSIX path, falling back to the absolute POSIX form.

    The fallback is deliberate rather than silent truncation: a file genuinely outside the
    project should say so, not be reduced to a bare filename that looks local.
    """
    target = Path(path)
    if root is not None:
        try:
            return target.resolve().relative_to(Path(root).resolve()).as_posix()
        except (ValueError, OSError):
            pass
    return target.as_posix()


# ---- the builder --------------------------------------------------------

class MetricsBuilder:
    def __init__(self, dataset: Dataset, config: ClientConfig, period: str,
                 validation: ValidationReport, reconciliation: ReconciliationReport,
                 project_root: Path | None = None,
                 periods_elsewhere: dict[str, list[str]] | None = None,
                 selection: dict | None = None):
        self.ds = dataset
        self.cfg = config
        self.period = period
        self.val = validation
        self.rec = reconciliation
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.sheet = dataset.income_statement
        self.metrics = MetricSet()
        self.limitations: list[str] = []
        self.movements: dict[str, list[dict]] = {}
        self.window_one_offs: list[dict] = []
        self.window_one_off_summary: dict = {}
        self.prior = prior_period(period)
        self.available = set(self.sheet.period_keys())
        self.ttm_window = trailing_window(
            self.sheet.period_keys(), period, config.ttm_months
        )
        self.ttm_complete = all(p in self.available for p in self.ttm_window)
        #: Period -> other workbooks holding it. Only a file-aware run passes this, and it
        #: is only ever used to say *why* a comparison is n/a; no value is read from it.
        self.elsewhere = {p: names for p, names in (periods_elsewhere or {}).items()
                          if p not in self.available}
        self.selection = selection

    def _other_file_note(self, period: str) -> str | None:
        names = self.elsewhere.get(period)
        if not names:
            return None
        return (f"Not available: prior period {period} is in a different file "
                f"({', '.join(names)}). Only {Path(self.ds.source_path).name} is read; "
                f"files are never combined.")

    # -- metric construction helpers --

    def _emit(self, name: str, value: float | None, unit: str, method_id: str,
              source_rows: list[dict] | None = None, reason: str | None = None,
              reported: float | None = None, label: str | None = None,
              notes: list[str] | None = None) -> Metric:
        issues = self.val.for_metric(name)
        blocked = any(i.severity == "error" for i in issues)
        warnings = [i.message for i in issues if i.severity == "warning"]
        if blocked:
            status, value, reason = BLOCKED, None, reason or "blocked_by_validation"
        elif warnings:
            status = WARNING
        else:
            status = OK
        return self.metrics.add(Metric(
            name=name, client_id=self.cfg.client_id, period=self.period,
            value=value, unit=unit, method_id=method_id,
            source_rows=source_rows or [], validation_status=status,
            warnings=warnings,
            unavailable_reason=reason if value is None else None,
            reported_value=reported, label=label, notes=notes or [],
        ))

    def _emit_mom(self, base_name: str, current: float | None,
                  prior_value: float | None, unit: str,
                  traces: list[dict] | None = None, emit_prior: bool = False) -> None:
        """Absolute and percentage month-over-month change for a base metric.

        `emit_prior` also records the prior-period value itself as `<base>.prior`, for
        the headline measures a report shows side by side. Without it a report has to
        rebuild the prior figure as `current - change`, which is arithmetic outside the
        engine and produces a number the guard cannot trace.
        """
        elsewhere = self._other_file_note(self.prior) if prior_value is None else None
        if emit_prior:
            self._emit(f"{base_name}.prior", prior_value, unit, "prior_period_value_v1",
                       None, None if prior_value is not None
                       else ("in_other_file" if elsewhere else "missing_input"),
                       notes=[f"The {self.prior} value every change in {base_name} "
                              f"is measured against."] + ([elsewhere] if elsewhere else []))
        if current is None or prior_value is None:
            abs_change, abs_reason = None, "in_other_file" if elsewhere else "missing_input"
        else:
            abs_change, abs_reason = current - prior_value, None
        self._emit(f"{base_name}.mom_abs", abs_change, unit, "mom_abs_v1",
                   traces, abs_reason,
                   notes=[f"Change from {self.prior} to {self.period}."]
                   + ([elsewhere] if elsewhere else []))
        pct, pct_reason = pct_change(current, prior_value)
        if elsewhere:
            pct_reason = "in_other_file"
        self._emit(f"{base_name}.mom_pct", pct, PERCENT, "mom_pct_v1", traces, pct_reason,
                   notes=[elsewhere or _pct_note(pct_reason, self.prior, prior_value)])

    def _emit_ttm(self, base_name: str, items: list[LineItem], method: str) -> None:
        if not self.ttm_complete:
            self._emit(f"{base_name}.ttm", None, CURRENCY, method, [],
                       "incomplete_window",
                       notes=[f"A complete {self.cfg.ttm_months}-month window ending "
                              f"{self.period} requires {self.ttm_window[0]}.."
                              f"{self.ttm_window[-1]}; the source does not cover it."])
            return
        total, traces = 0.0, []
        for p in self.ttm_window:
            sub, sub_traces, _ = sum_present(items, p)
            if sub is None:
                self._emit(f"{base_name}.ttm", None, CURRENCY, method, [],
                           "missing_input",
                           notes=[f"No values available for {p}."])
                return
            total += sub
            traces.extend(sub_traces)
        self._emit(f"{base_name}.ttm", total, CURRENCY, method, traces,
                   notes=[f"Sum of {self.ttm_window[0]}..{self.ttm_window[-1]} "
                          f"({self.cfg.ttm_months} months)."])

    # -- the build --

    def build(self) -> dict:
        # Must run before any metric is emitted: it raises a validation warning that the
        # TTM metrics then pick up through the normal `affects` mechanism.
        self._detect_window_one_offs()
        self._revenue()
        self._cogs_and_gross_profit()
        self._opex()
        self._operating_income()
        self._balance_sheet()
        self._movements()
        self._seasonality()
        self._limitations()
        return self._document()

    def _revenue(self) -> None:
        items = self.sheet.in_section("revenue")
        total_row = self.sheet.section_total("revenue")
        total, traces, missing = sum_present(items, self.period)
        self._emit("revenue.total", total, CURRENCY, "sum_section_items_v1", traces,
                   "missing_input" if total is None else None,
                   reported=total_row.value(self.period) if total_row else None,
                   label="Total revenue",
                   notes=[f"{len(traces)} revenue line(s) summed"
                          + (f"; {missing} missing" if missing else "") + "."])
        prior_total, _, _ = sum_present(items, self.prior)
        self._emit_mom("revenue.total", total, prior_total, CURRENCY, traces, emit_prior=True)
        self._emit_ttm("revenue.total", items, "ttm_sum_v1")
        self._emit_yoy("revenue.total", items)

        for item in items:
            name = f"revenue.stream.{slug(item.label)}"
            cell = item.cells.get(self.period)
            self._emit(name, item.value(self.period), CURRENCY, "line_value_v1",
                       [cell.trace()] if cell else [],
                       None if item.value(self.period) is not None else "missing_input",
                       label=item.label)
            self._emit_mom(name, item.value(self.period), item.value(self.prior),
                           CURRENCY, [cell.trace()] if cell else [])
            share, share_reason = ratio_pct(item.value(self.period), total)
            self._emit(f"{name}.share_of_revenue_pct", share, PERCENT, "margin_pct_v1",
                       [], share_reason, label=item.label,
                       notes=["Share of total revenue for the month."])

    def _cogs_and_gross_profit(self) -> None:
        items = self.sheet.in_section("cogs")
        total_row = self.sheet.section_total("cogs")
        total, traces, missing = sum_present(items, self.period)
        self._emit("cogs.total", total, CURRENCY, "sum_section_items_v1", traces,
                   "missing_input" if total is None else None,
                   reported=total_row.value(self.period) if total_row else None,
                   label="Total cost of goods sold",
                   notes=[f"{len(traces)} COGS line(s) summed"
                          + (f"; {missing} missing" if missing else "") + "."])
        prior_total, _, _ = sum_present(items, self.prior)
        self._emit_mom("cogs.total", total, prior_total, CURRENCY, traces, emit_prior=True)
        self._emit_ttm("cogs.total", items, "ttm_sum_v1")

        for item in items:
            name = f"cogs.line.{slug(item.label)}"
            cell = item.cells.get(self.period)
            self._emit(name, item.value(self.period), CURRENCY, "line_value_v1",
                       [cell.trace()] if cell else [],
                       None if item.value(self.period) is not None else "missing_input",
                       label=item.label)
            self._emit_mom(name, item.value(self.period), item.value(self.prior),
                           CURRENCY, [cell.trace()] if cell else [])

        rev = self.metrics.value("revenue.total")
        gp = None if rev is None or total is None else rev - total
        gp_row = self.sheet.reported(self.cfg.reported_lines.get("gross_profit", ""))
        self._emit("gross_profit", gp, CURRENCY, "difference_v1", [],
                   "missing_input" if gp is None else None,
                   reported=gp_row.value(self.period) if gp_row else None,
                   label="Gross profit",
                   notes=["Total revenue minus total cost of goods sold."])

        prior_rev, _, _ = sum_present(self.sheet.in_section("revenue"), self.prior)
        prior_gp = (None if prior_rev is None or prior_total is None
                    else prior_rev - prior_total)
        self._emit_mom("gross_profit", gp, prior_gp, CURRENCY, emit_prior=True)

        margin, reason = ratio_pct(gp, rev)
        self._emit("gross_margin_pct", margin, PERCENT, "margin_pct_v1", [], reason,
                   label="Gross margin",
                   notes=["Gross profit as a percentage of total revenue."])
        prior_margin, _ = ratio_pct(prior_gp, prior_rev)
        delta = None if margin is None or prior_margin is None else margin - prior_margin
        self._emit("gross_margin_pct.mom_pts", delta, PERCENTAGE_POINTS,
                   "pct_points_delta_v1", [],
                   "missing_input" if delta is None else None,
                   notes=["Percentage-point change in gross margin versus the prior month. "
                          "Margins are compared in points, not as a percentage of a "
                          "percentage."])

        if self.ttm_complete:
            rev_ttm = self.metrics.value("revenue.total.ttm")
            cogs_ttm = self.metrics.value("cogs.total.ttm")
            gp_ttm = None if rev_ttm is None or cogs_ttm is None else rev_ttm - cogs_ttm
            self._emit("gross_profit.ttm", gp_ttm, CURRENCY, "difference_v1", [],
                       "missing_input" if gp_ttm is None else None,
                       notes=["Trailing-twelve-month revenue minus trailing-twelve-month COGS."])
            ttm_margin, ttm_reason = ratio_pct(gp_ttm, rev_ttm)
            self._emit("gross_margin_pct.ttm", ttm_margin, PERCENT, "margin_pct_v1", [],
                       ttm_reason,
                       notes=["Trailing-twelve-month gross margin."])
        else:
            self._emit("gross_profit.ttm", None, CURRENCY, "difference_v1", [],
                       "incomplete_window")
            self._emit("gross_margin_pct.ttm", None, PERCENT, "margin_pct_v1", [],
                       "incomplete_window")

    def _opex(self) -> None:
        leaves = self.sheet.in_section("opex")
        direct = self.sheet.direct_items("opex")
        group_totals = self.sheet.group_totals("opex")
        total_row = self.sheet.section_total("opex")

        total, traces, missing = sum_present(leaves, self.period)
        self._emit("opex.total", total, CURRENCY, "sum_section_items_v1", traces,
                   "missing_input" if total is None else None,
                   reported=total_row.value(self.period) if total_row else None,
                   label="Total operating expenses",
                   notes=[f"{len(traces)} operating expense line(s) summed"
                          + (f"; {missing} missing" if missing else "")
                          + ". Subtotal-group members are summed once; the workbook's "
                            "own group subtotal rows are not added again."])
        prior_total, _, _ = sum_present(leaves, self.prior)
        self._emit_mom("opex.total", total, prior_total, CURRENCY, traces, emit_prior=True)
        self._emit_ttm("opex.total", leaves, "ttm_sum_v1")

        # By subtotal group, as the workbook presents them.
        for gt in group_totals:
            members = self.sheet.group_members(gt.group_key)
            name = f"opex.group.{gt.group_key}"
            value, g_traces, g_missing = sum_present(members, self.period)
            self._emit(name, value, CURRENCY, "sum_group_members_v1", g_traces,
                       "missing_input" if value is None else None,
                       reported=gt.value(self.period), label=gt.group,
                       notes=[f"{len(g_traces)} member row(s) of the "
                              f"{gt.group!r} subtotal group"
                              + (f"; {g_missing} missing" if g_missing else "") + "."])
            prior_value, _, _ = sum_present(members, self.prior)
            self._emit_mom(name, value, prior_value, CURRENCY, g_traces)
            self._emit_ttm(name, members, "ttm_sum_v1")

        ungrouped_value, u_traces, u_missing = sum_present(direct, self.period)
        self._emit("opex.group.ungrouped", ungrouped_value, CURRENCY,
                   "sum_group_members_v1", u_traces,
                   "missing_input" if ungrouped_value is None else None,
                   label="Not in a subtotal group",
                   notes=[f"{len(u_traces)} operating expense line(s) that sit directly "
                          f"in the section rather than inside a subtotal group"
                          + (f"; {u_missing} missing" if u_missing else "") + "."])
        prior_ungrouped, _, _ = sum_present(direct, self.prior)
        self._emit_mom("opex.group.ungrouped", ungrouped_value, prior_ungrouped,
                       CURRENCY, u_traces)

        # By presentation category. Categories partition every opex leaf, so they sum
        # back to opex.total.
        by_category: dict[str, list[LineItem]] = {}
        for item in leaves:
            by_category.setdefault(item.category or self.cfg.fallback_opex_category,
                                   []).append(item)
        for category, members in sorted(by_category.items()):
            name = f"opex.category.{slug(category)}"
            value, c_traces, c_missing = sum_present(members, self.period)
            self._emit(name, value, CURRENCY, "sum_category_items_v1", c_traces,
                       "missing_input" if value is None else None, label=category,
                       notes=[f"{len(c_traces)} line(s) assigned to {category!r} by "
                              f"config/clients/{self.cfg.client_id}.yaml"
                              + (f"; {c_missing} missing" if c_missing else "") + "."])
            prior_value, _, _ = sum_present(members, self.prior)
            self._emit_mom(name, value, prior_value, CURRENCY, c_traces)
            self._emit_ttm(name, members, "ttm_sum_v1")
            share, share_reason = ratio_pct(value, total)
            self._emit(f"{name}.share_of_opex_pct", share, PERCENT, "margin_pct_v1",
                       [], share_reason, label=category,
                       notes=["Share of total operating expenses for the month."])

        # Individual lines.
        for item in leaves:
            name = f"opex.line.{slug(item.label)}"
            cell = item.cells.get(self.period)
            self._emit(name, item.value(self.period), CURRENCY, "line_value_v1",
                       [cell.trace()] if cell else [],
                       None if item.value(self.period) is not None else "missing_input",
                       label=item.label,
                       notes=[f"Category: {item.category}."
                              + (f" Subtotal group: {item.group}." if item.group else "")])
            self._emit_mom(name, item.value(self.period), item.value(self.prior),
                           CURRENCY, [cell.trace()] if cell else [])

    def _operating_income(self) -> None:
        gp = self.metrics.value("gross_profit")
        opex = self.metrics.value("opex.total")
        oi = None if gp is None or opex is None else gp - opex
        oi_row = self.sheet.reported(self.cfg.reported_lines.get("operating_income", ""))
        ni_row = self.sheet.reported(self.cfg.reported_lines.get("net_income", ""))

        classification = (
            "This workbook presents interest, bad debts and income tax inside Operating "
            "Expenses, so its Operating Income equals its Net Income. The figure is not "
            "comparable to an operating income that excludes those items."
        )
        self._emit("operating_income", oi, CURRENCY, "difference_v1", [],
                   "missing_input" if oi is None else None,
                   reported=oi_row.value(self.period) if oi_row else None,
                   label="Operating income",
                   notes=["Gross profit minus total operating expenses.", classification])

        prior_gp_rev, _, _ = sum_present(self.sheet.in_section("revenue"), self.prior)
        prior_cogs, _, _ = sum_present(self.sheet.in_section("cogs"), self.prior)
        prior_opex, _, _ = sum_present(self.sheet.in_section("opex"), self.prior)
        prior_oi = (None if None in (prior_gp_rev, prior_cogs, prior_opex)
                    else prior_gp_rev - prior_cogs - prior_opex)
        self._emit_mom("operating_income", oi, prior_oi, CURRENCY, emit_prior=True)

        rev = self.metrics.value("revenue.total")
        margin, reason = ratio_pct(oi, rev)
        self._emit("operating_margin_pct", margin, PERCENT, "margin_pct_v1", [], reason,
                   label="Operating margin",
                   notes=["Operating income as a percentage of total revenue.",
                          classification])
        prior_margin, _ = ratio_pct(prior_oi, prior_gp_rev)
        delta = None if margin is None or prior_margin is None else margin - prior_margin
        self._emit("operating_margin_pct.mom_pts", delta, PERCENTAGE_POINTS,
                   "pct_points_delta_v1", [],
                   "missing_input" if delta is None else None,
                   notes=["Percentage-point change in operating margin versus the "
                          "prior month."])

        if self.ttm_complete:
            gp_ttm = self.metrics.value("gross_profit.ttm")
            opex_ttm = self.metrics.value("opex.total.ttm")
            oi_ttm = None if gp_ttm is None or opex_ttm is None else gp_ttm - opex_ttm
            self._emit("operating_income.ttm", oi_ttm, CURRENCY, "difference_v1", [],
                       "missing_input" if oi_ttm is None else None,
                       notes=["Trailing-twelve-month gross profit minus "
                              "trailing-twelve-month operating expenses.", classification])
        else:
            self._emit("operating_income.ttm", None, CURRENCY, "difference_v1", [],
                       "incomplete_window")

        if ni_row is not None:
            cell = ni_row.cells.get(self.period)
            self._emit("net_income.reported", ni_row.value(self.period), CURRENCY,
                       "workbook_reported_v1", [cell.trace()] if cell else [],
                       None if ni_row.value(self.period) is not None else "missing_input",
                       label="Net income (as reported in the workbook)",
                       notes=["Read from the workbook's own Net Income row.",
                              classification])

    def _balance_sheet(self) -> None:
        bs = self.ds.balance_sheet
        spec = self.cfg.balance_sheet
        if bs is None:
            for entry in spec.get("trend_lines") or []:
                self._emit(f"balance_sheet.{entry['key']}", None, CURRENCY,
                           "line_value_v1", [], "not_available",
                           label=entry.get("label"))
            return

        bs_prior = self.prior
        for key, label in (spec.get("reported_totals") or {}).items():
            row = bs.by_label(label)
            name = f"balance_sheet.{key}"
            if row is None:
                self._emit(name, None, CURRENCY, "workbook_reported_v1", [],
                           "not_available", label=label,
                           notes=[f"No row labelled {label!r} on {bs.name}."])
                continue
            cell = row.cells.get(self.period)
            self._emit(name, row.value(self.period), CURRENCY, "workbook_reported_v1",
                       [cell.trace()] if cell else [],
                       None if row.value(self.period) is not None else "missing_input",
                       label=label)

        for entry in spec.get("trend_lines") or []:
            label, key = entry["label"], entry["key"]
            row = bs.by_label(label)
            name = f"balance_sheet.{key}"
            if row is None:
                self._emit(name, None, CURRENCY, "line_value_v1", [], "not_available",
                           label=label,
                           notes=[f"No row labelled {label!r} on {bs.name}; reported as "
                                  f"unavailable rather than zero."])
                continue
            cell = row.cells.get(self.period)
            value = row.value(self.period)
            self._emit(name, value, CURRENCY, "line_value_v1",
                       [cell.trace()] if cell else [],
                       None if value is not None else "missing_input", label=label)
            self._emit_mom(name, value, row.value(bs_prior), CURRENCY,
                           [cell.trace()] if cell else [])

    def _movements(self) -> None:
        """Rank month-over-month movements and flag possible one-offs.

        Everything here is derived from metrics already emitted above; nothing new is
        calculated, so every number in a movement also appears as a metric.
        """
        pct_floor = self.cfg.threshold("significant_mom_pct", 25.0)
        abs_floor = self.cfg.threshold("significant_mom_abs", 10000.0)
        min_abs = self.cfg.threshold("significant_mom_min_abs", 1000.0)
        top_n = int(self.cfg.threshold("top_movements", 5))

        candidates: list[dict] = []
        scopes: list[tuple[str, list[LineItem], str]] = [
            ("revenue_stream", self.sheet.in_section("revenue"), "revenue.stream."),
            ("cogs_line", self.sheet.in_section("cogs"), "cogs.line."),
            ("opex_line", self.sheet.in_section("opex"), "opex.line."),
        ]
        for scope, items, prefix in scopes:
            for item in items:
                current, previous = item.value(self.period), item.value(self.prior)
                if current is None or previous is None:
                    continue
                change = current - previous
                if change == 0:
                    continue
                pct, pct_reason = pct_change(current, previous)
                sign_change = crosses_zero(current, previous)
                candidates.append({
                    "scope": scope,
                    "label": item.label,
                    "category": item.category,
                    "group": item.group,
                    "sheet": item.sheet,
                    "row": item.row,
                    "previous_period": self.prior,
                    "previous": previous,
                    "current": current,
                    "change_abs": change,
                    "change_pct": pct,
                    "change_pct_unavailable_reason": pct_reason,
                    "sign_change": sign_change,
                    "direction": _direction(change, sign_change, previous, current),
                    "metric_name": f"{prefix}{slug(item.label)}",
                })

        # A percentage on a small base is noise: a line moving 400 to 600 is "+50%" and
        # means nothing in a business with six-figure months. So the percentage route now
        # also has to clear an absolute floor. A genuinely large movement still qualifies
        # on its own, and a line crossing zero qualifies once it clears the floor, since
        # no percentage is available for it at all.
        significant = [
            c for c in candidates
            if abs(c["change_abs"]) >= abs_floor
            or (c["change_pct"] is not None
                and abs(c["change_pct"]) >= pct_floor
                and abs(c["change_abs"]) >= min_abs)
            or (c["sign_change"] and abs(c["change_abs"]) >= min_abs)
        ]
        significant.sort(key=lambda c: abs(c["change_abs"]), reverse=True)

        ranked = sorted(candidates, key=lambda c: c["change_abs"], reverse=True)
        self.movements = {
            "thresholds": {
                "significant_mom_pct": pct_floor,
                "significant_mom_abs": abs_floor,
                "significant_mom_min_abs": min_abs,
                "rule": (
                    f"A movement is significant if its absolute change is at least "
                    f"{abs_floor:,.2f}, or its percentage change is at least "
                    f"{pct_floor:,.1f}% AND its absolute change is at least "
                    f"{min_abs:,.2f}, or it crossed zero and its absolute change is at "
                    f"least {min_abs:,.2f}. The absolute floor on the percentage route "
                    f"keeps large percentages on trivial amounts out of the list."
                ),
            },
            "significant": significant,
            "candidates_considered": len(candidates),
            "top_increases": [c for c in ranked if c["change_abs"] > 0][:top_n],
            "top_decreases": [c for c in reversed(ranked) if c["change_abs"] < 0][:top_n],
            "possible_one_offs": self._possible_one_offs(),
            "ttm_window_one_offs": self.window_one_offs,
            "ttm_window_one_offs_summary": self.window_one_off_summary,
        }

    # ---- one-off detection ---------------------------------------------
    #
    # Two scans, one implementation. They differ only in which months they test and what
    # they compare each month against:
    #
    #   possible_one_offs    — the reporting month, against the trailing N months
    #                          before it.
    #   ttm_window_one_offs  — every month in the TTM window, each against the average
    #                          of the *other* months in that window.
    #
    # Everything else is shared: the same thresholds shape, the same magnitude-based
    # test, and the same entry schema. Before this was unified the monthly scan used a
    # signed comparison and so could not see a large credit at all, while the two scans
    # emitted different field names for the same concepts.

    def _scan_one_offs(self, *, test_periods: list[str],
                       baseline_periods: Callable[[str], list[str]],
                       basis: str, multiple: float, min_abs: float,
                       assessment: str) -> list[dict]:
        """Find months that stand far away from their comparison average.

        Magnitudes throughout: a large credit distorts a total exactly as much as a large
        charge, so `abs()` governs both the floor and the multiple. A baseline of zero
        has no multiple to report, and a line that is normally zero and suddenly carries
        a large amount is the clearest one-off there is — so it is flagged with the
        multiple left unavailable rather than skipped for want of a denominator.
        """
        out: list[dict] = []
        for item in self.sheet.in_section("opex") + self.sheet.in_section("cogs"):
            for period in test_periods:
                current = item.value(period)
                if current is None:
                    continue
                compared = [p for p in baseline_periods(period) if p != period]
                history = [item.value(p) for p in compared]
                history = [v for v in history if v is not None]
                if len(history) < 3:
                    continue

                baseline = sum(history) / len(history)
                excess = current - baseline
                if abs(excess) < min_abs:
                    continue

                if baseline == 0:
                    ratio, ratio_reason, stands_out = None, "zero_base", True
                elif baseline < 0:
                    ratio, ratio_reason = None, "negative_base"
                    stands_out = abs(current) >= abs(baseline) * multiple
                else:
                    ratio, ratio_reason = current / baseline, None
                    stands_out = abs(current) >= baseline * multiple
                if not stands_out:
                    continue

                out.append({
                    "label": item.label,
                    "section": item.section,
                    "category": item.category,
                    "group": item.group,
                    "group_key": item.group_key,
                    "sheet": item.sheet,
                    "row": item.row,
                    "period": period,
                    "current": current,
                    "comparison_average": baseline,
                    "comparison_basis": basis,
                    "comparison_months": len(history),
                    "excess_over_average": excess,
                    "multiple_of_average": ratio,
                    "multiple_unavailable_reason": ratio_reason,
                    "metric_name": naming.line_for(item.section, item.label),
                    "assessment": assessment,
                })

        out.sort(key=lambda c: abs(c["excess_over_average"]), reverse=True)
        return out

    def _detect_window_one_offs(self) -> None:
        """Find one-offs anywhere in the TTM window, not just in the reporting month.

        A trailing-twelve-month total silently carries whatever happened eleven months
        ago. ABC's December 2025 holds a 1,244,430 income tax benefit and 334,661 of
        stock-based compensation, each zero in every other month; together they move TTM
        operating expenses by well over a million without appearing anywhere in the June
        figures. A reader comparing TTM against a monthly run rate has to be told.

        Runs before any metric is emitted, so the warning it raises is picked up by the
        TTM metrics through the normal `affects` mechanism.
        """
        self.window_one_offs = []
        self.window_one_off_summary = {}
        if not self.ttm_complete:
            return

        window = self.ttm_window
        self.window_one_offs = self._scan_one_offs(
            test_periods=list(window),
            baseline_periods=lambda _period: list(window),
            basis=f"other months in the window {window[0]}..{window[-1]}",
            multiple=self.cfg.threshold("oneoff_multiple", 3.0),
            min_abs=self.cfg.threshold("oneoff_window_min_abs", 25000.0),
            assessment=("possible one-off inside the trailing-twelve-month window — "
                        "magnitude only; the workbook contains no information about the "
                        "cause"),
        )
        if not self.window_one_offs:
            return

        described = "; ".join(
            f"{c['label']} {c['current']:,.2f} in {c['period']}"
            for c in self.window_one_offs[:6]
        )
        extra = (f" and {len(self.window_one_offs) - 6} other item(s)"
                 if len(self.window_one_offs) > 6 else "")

        # Scoped to the TTM metrics whose totals actually include these lines. All four
        # of ABC's items are operating expenses, so revenue.total.ttm must not be tagged:
        # a warning on a figure it cannot affect is worse than no warning at all.
        affects: set[str] = set()
        sections = sorted({c["section"] for c in self.window_one_offs if c["section"]})
        for section in sections:
            affects |= naming.ttm_metrics_for_section(section)
        for c in self.window_one_offs:
            if c["section"] != "opex":
                continue
            if c["category"]:
                affects.add(f"{naming.opex_category(c['category'])}.ttm")
            if c["group_key"]:
                affects.add(f"{naming.opex_group(c['group_key'])}.ttm")

        section_phrase = " and ".join(_SECTION_PHRASES.get(s, s) for s in sections)
        figures = naming.ttm_figures_phrase(affects)

        # Both renderers read this instead of each working out its own phrasing, so the
        # commentary and the report cannot describe the same finding differently.
        self.window_one_off_summary = {
            "count": len(self.window_one_offs),
            "window_start": window[0],
            "window_end": window[-1],
            "sections": sections,
            "section_phrase": section_phrase,
            "affected_ttm_metrics": sorted(affects),
            "affected_figures_phrase": figures,
        }

        self.val.warn(
            "ttm_includes_possible_one_offs",
            f"The trailing-twelve-month window {window[0]}..{window[-1]} includes "
            f"{len(self.window_one_offs)} {section_phrase} item(s) that stand far above "
            f"the rest of the window and may be one-offs: {described}{extra}. The "
            f"trailing-twelve-month {figures} figures include these lines and are "
            f"therefore not a run rate. The workbook gives no reason for any of them; a "
            f"reviewer must confirm or dismiss each.",
            tuple(sorted(affects)),
            items=[{"label": c["label"], "section": c["section"],
                    "period": c["period"], "value": c["current"],
                    "excess_over_average": c["excess_over_average"],
                    "row": c["row"]} for c in self.window_one_offs],
            sections=sections,
            affected_figures_phrase=figures,
            window_start=window[0],
            window_end=window[-1],
        )

    def _possible_one_offs(self) -> list[dict]:
        """Months in the reporting period that stand far from their trailing average.

        Labelled "possible" and never given a cause. A spike is a pattern in the data,
        not an explanation of it.
        """
        lookback = int(self.cfg.threshold("oneoff_trailing_months", 6))
        trailing = [p for p in trailing_window(self.sheet.period_keys(), self.prior,
                                               lookback)
                    if p in self.available]
        if len(trailing) < 3:
            self.limitations.append(
                f"One-off detection needs at least three of the {lookback} months before "
                f"{self.period}; only {len(trailing)} are available, so no one-off "
                f"assessment was made for {self.period}."
            )
            return []

        return self._scan_one_offs(
            test_periods=[self.period],
            baseline_periods=lambda _period: trailing,
            basis=f"the {len(trailing)} month(s) {trailing[0]}..{trailing[-1]}",
            multiple=self.cfg.threshold("oneoff_multiple", 3.0),
            min_abs=self.cfg.threshold("oneoff_min_abs", 5000.0),
            assessment=("possible one-off — magnitude only; the workbook contains no "
                        "information about the cause"),
        )

    def _seasonality(self) -> None:
        """Only assessed with at least two full years. Otherwise explicitly not assessed."""
        months = len(self.available)
        needed = 24
        self.seasonality = {
            "assessed": False,
            "months_available": months,
            "months_required": needed,
            "reason": (
                f"A seasonality conclusion needs at least {needed} months so that each "
                f"calendar month appears twice. The source covers {months} months, so no "
                f"seasonal pattern is asserted."
            ),
        }
        if months >= needed:
            self.seasonality = {
                "assessed": True,
                "months_available": months,
                "months_required": needed,
                "reason": "",
            }

    def _emit_yoy(self, base_name: str, items: list[LineItem]) -> None:
        ya = year_ago(self.period)
        if ya not in self.available:
            elsewhere = self._other_file_note(ya)
            reason = "in_other_file" if elsewhere else "missing_input"
            self._emit(f"{base_name}.yoy_abs", None, CURRENCY, "yoy_abs_v1", [],
                       reason,
                       notes=[f"{ya} is not in the source, so no year-on-year "
                              f"comparison is available."]
                       + ([elsewhere] if elsewhere else []))
            self._emit(f"{base_name}.yoy_pct", None, PERCENT, "yoy_pct_v1", [],
                       reason, notes=[elsewhere] if elsewhere else None)
            return
        current, traces, _ = sum_present(items, self.period)
        base, _, _ = sum_present(items, ya)
        change = None if current is None or base is None else current - base
        self._emit(f"{base_name}.yoy_abs", change, CURRENCY, "yoy_abs_v1", traces,
                   "missing_input" if change is None else None,
                   notes=[f"Change from {ya} to {self.period}."])
        pct, reason = pct_change(current, base)
        self._emit(f"{base_name}.yoy_pct", pct, PERCENT, "yoy_pct_v1", traces, reason,
                   notes=[_pct_note(reason, ya, base)])

    def _limitations(self) -> None:
        first, last = self.sheet.period_keys()[0], self.sheet.period_keys()[-1]
        self.limitations.insert(0,
            f"The source covers {first} to {last} ({len(self.available)} months). "
            f"Nothing outside that range is known to the engine."
        )
        if not self.ttm_complete:
            self.limitations.append(
                f"No complete {self.cfg.ttm_months}-month window ends at {self.period}, "
                f"so every trailing-twelve-month figure is blocked rather than computed "
                f"over a shorter span."
            )
            elsewhere = sorted(p for p in self.ttm_window if p in self.elsewhere)
            if elsewhere:
                names = sorted({n for p in elsewhere for n in self.elsewhere[p]})
                self.limitations.append(
                    f"{len(elsewhere)} month(s) of that window are in a different file "
                    f"({', '.join(names)}): {', '.join(elsewhere)}. Files are never "
                    f"combined, so they do not complete the window."
                )
        if self.rec.failures:
            self.limitations.append(
                f"{len(self.rec.failures)} reconciliation check(s) failed; metrics that "
                f"depend on the affected totals should not be relied on until the "
                f"discrepancy is explained."
            )
        self.limitations.append(
            "The engine reads the workbook only. It has no access to invoices, contracts, "
            "headcount or any other context, so it can describe what changed but never "
            "why."
        )
        self.limitations.append(
            "Output is a draft for a human reviewer, not a reviewed financial statement."
        )

    def _document(self) -> dict:
        period = Period.from_key(self.period)
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc)
                                  .replace(microsecond=0).isoformat(),
            "client_id": self.cfg.client_id,
            "company_name": self.cfg.company_name,
            "period": self.period,
            "period_label": period.display,
            "prior_period": self.prior,
            "currency": self.cfg.currency,
            "currency_symbol": self.cfg.currency_symbol,
            "units_scale": self.cfg.units_scale,
            "source": {
                # Project-relative with forward slashes. An absolute "C:\\Users\\..."
                # leaks a developer's home directory into a client deliverable and makes
                # two machines' output differ for no reason.
                "config_path": relative_path(self.cfg.path, self.project_root),
                "type": self.ds.source_type,
                "path": relative_path(self.ds.source_path, self.project_root),
                "filename": Path(self.ds.source_path).name,
                "sha256": self.ds.source_sha256,
                "sheets": [s.name for s in
                           filter(None, (self.ds.income_statement, self.ds.balance_sheet))],
                "sheet_headings": {
                    s.name: s.titles for s in
                    filter(None, (self.ds.income_statement, self.ds.balance_sheet))
                },
                "periods_available": self.sheet.period_keys(),
                "period_count": len(self.available),
                **({"selection": self.selection} if self.selection else {}),
            },
            "ttm": {
                "months": self.cfg.ttm_months,
                "window": self.ttm_window,
                "complete": self.ttm_complete,
            },
            "methods": METHODS,
            "metrics": self.metrics.to_list(),
            "movements": self.movements,
            "seasonality": self.seasonality,
            "reconciliation": self.rec.to_dict(),
            "data_quality_issues": self.val.to_list(),
            "classification_notes": self.cfg.classification_notes,
            "limitations": self.limitations,
        }


def _direction(change: float, sign_change: bool, previous: float,
               current: float) -> str:
    """A word for the movement, so a reader is not left to infer it from a signed number.

    "sign change" is called out separately because "a cost fell by more than 100%" is a
    sentence that invites the wrong conclusion.
    """
    if sign_change:
        return ("sign change: charge to credit" if previous > 0 > current
                else "sign change: credit to charge")
    if change > 0:
        return "increase"
    if change < 0:
        return "decrease"
    return "unchanged"


def _pct_note(reason: str | None, base_period: str, base_value: float | None) -> str:
    if reason == "zero_base":
        return (f"Not available: {base_period} was zero, so a percentage change is "
                f"undefined. Shown as n/a rather than as an infinite change.")
    if reason == "negative_base":
        return (f"Not available: {base_period} was negative ({base_value:,.2f}), so a "
                f"percentage change would read backwards. The absolute change is the "
                f"meaningful comparison.")
    if reason == "sign_change":
        return (f"Not available: the line crossed zero, from {base_value:,.2f} in "
                f"{base_period} to a figure of the opposite sign. A percentage would "
                f"misrepresent a swing between a charge and a credit; the absolute "
                f"change and the sign change are the information.")
    if reason == "missing_input":
        return f"Not available: no comparable value for {base_period}."
    return f"Measured against {base_period}."


def build_metrics(dataset: Dataset, config: ClientConfig, period: str,
                  project_root: Path | None = None,
                  periods_elsewhere: dict[str, list[str]] | None = None,
                  selection: dict | None = None) -> dict:
    """Validate, reconcile and calculate. Returns the metrics.json document.

    `project_root` is used only to write source paths relative to it. It defaults to the
    working directory, which is the project root for every supported entry point.

    `periods_elsewhere` and `selection` come from a file-aware run (`--file`, or a
    workbook chosen by period). They only add notes and the `source.selection` block;
    a run without them is unchanged.
    """
    validation = validate(dataset, config, period)
    reconciliation = reconcile(dataset, config)
    return MetricsBuilder(dataset, config, period, validation, reconciliation,
                          project_root, periods_elsewhere, selection).build()
