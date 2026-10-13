"""Year and whole-file scopes: `--scope year --year YYYY` and `--scope all`.

Both documents follow the month document's conventions exactly — every figure is a
`Metric` with a name, value, unit, method_id, validation_status, source_rows, warnings and
notes — and both are built only from one month's figures at a time (`monthly.py`), summed.

Rules that matter here, on top of the month rules:

* **A partial year is never scaled.** Year to date is the total of the months present,
  labelled as such. Nothing is annualised, extrapolated or averaged up to twelve months.
* **A gap blocks the total.** A month missing *inside* the year's span (not merely a short
  year) blocks every year total and the year-on-year comparison, through the same
  validation-error mechanism that blocks an incomplete trailing-twelve-month window.
* **Quarters need all three months.** An incomplete quarter is n/a, never two-thirds of one.
* **Year on year needs the same months.** Jan-Jun 2026 against Jan-Jun 2025 is compared;
  anything else is n/a with a note. Percentages need a strictly positive base.
* **One file.** A comparison month that exists only in another workbook is n/a.

Metric names (documented in docs/metrics_schema.md):

    year scope   revenue.total, revenue.stream.<slug>, cogs.total, gross_profit,
                 gross_margin_pct, opex.total, opex.category.<slug>, operating_income,
                 operating_margin_pct, net_income.reported      (year totals)
                 <measure>.q1 .. .q4                              (quarters)
                 <measure>.prior_year / .yoy_abs / .yoy_pct / .yoy_pts
                 monthly.<YYYY-MM>.<measure>[.mom_abs|.mom_pct|.mom_pts]
                 extremes.<measure>.highest / .lowest
                 coverage.months_present / .months_missing / .months_in_year
    all scope    monthly.<YYYY-MM>.<measure>[...]  (every month: the series)
                 year.<YYYY>.<measure>             (a total per calendar year)
                 <measure>.start_to_end_abs / _pct / _pts
                 extremes.<measure>.highest / .lowest
                 coverage.months_present / .months_missing / .months_in_year
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from finance_engine import SCHEMA_VERSION
from finance_engine.config import ClientConfig
from finance_engine.metrics import (BLOCKED, COUNT, CURRENCY, METHODS, OK, PERCENT,
                                    PERCENTAGE_POINTS, WARNING, Metric, MetricSet,
                                    pct_change, prior_period, ratio_pct, relative_path)
from finance_engine.model import Dataset, Period
from finance_engine.monthly import (HEADLINE, LABELS, MARGIN_NUMERATOR, MARGINS, NET_INCOME,
                                    month_figures)
from finance_engine.reconciliation import ReconciliationReport, reconcile
from finance_engine.validation import ValidationReport, validate_span

SPAN_METHODS = {
    **METHODS,
    "month_value_v1": "One month's figure, computed exactly as the month report computes "
                      "it: section line items summed, gross profit and operating income "
                      "as differences, margins over a strictly positive revenue.",
    "span_sum_v1": "Sum of the monthly values for every month in the year. Requires every "
                   "month inside the year's span and a value for each; a partial year is "
                   "the total of the months present, never scaled.",
    "quarter_sum_v1": "Sum of a calendar quarter's three monthly values. Requires all three.",
    "prior_year_value_v1": "The same measure summed over the same calendar months one year "
                           "earlier.",
    "yoy_span_abs_v1": "This year's total minus the prior year's total for the same months. "
                       "Only when every one of those months exists in both years.",
    "yoy_span_pct_v1": "(this year - prior year) / prior year x 100 for the same months, "
                       "requiring a strictly positive prior-year total.",
    "extreme_month_v1": "The month with the highest or lowest value among the months that "
                        "have one. Ties go to the earliest month.",
    "count_v1": "A count of months.",
    "start_to_end_abs_v1": "The last month's value minus the first month's value.",
    "start_to_end_pct_v1": "(last - first) / first x 100, requiring a strictly positive "
                           "first-month value.",
}

QUARTERS = {1: (1, 2, 3), 2: (4, 5, 6), 3: (7, 8, 9), 4: (10, 11, 12)}
CHANGE_SUFFIXES = ("mom_abs", "mom_pct")


class SpanError(Exception):
    """The requested year or file has no months to describe. Nothing is written."""


def _key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _shift(period: str, months: int) -> str:
    o = Period.from_key(period).ordinal() + months
    return f"{o // 12:04d}-{o % 12 + 1:02d}"


def _display(period: str) -> str:
    return Period.from_key(period).display


def year_coverage(year: int, months_in_file: list[str]) -> dict:
    """Which months of a calendar year the file holds, and what that allows."""
    present = sorted(p for p in months_in_file if p.startswith(f"{year:04d}-"))
    calendar_months = [_key(year, m) for m in range(1, 13)]
    if not present:
        return {"year": year, "months_present": [], "months_present_count": 0,
                "months_absent": calendar_months, "gaps_inside_span": [], "span": None,
                "status": "absent", "label": "no months in the source",
                "totals_available": False}
    inside = [p for p in calendar_months if present[0] <= p <= present[-1]]
    gaps = [p for p in inside if p not in present]
    n = len(present)
    if n == 12:
        status, label = "complete", "full year, 12 of 12 months"
    elif gaps:
        status, label = "partial", (f"incomplete year, {n} of 12 months, with "
                                    f"{', '.join(gaps)} missing inside it")
    elif present[0].endswith("-01"):
        status, label = "partial", f"year to date, {n} of 12 months"
    else:
        status, label = "partial", (f"partial year, {n} of 12 months "
                                    f"({present[0]} to {present[-1]})")
    return {
        "year": year,
        "months_present": present,
        "months_present_count": n,
        "months_absent": [p for p in calendar_months if p not in present],
        "gaps_inside_span": gaps,
        "span": [present[0], present[-1]],
        "status": status,
        "label": label,
        "totals_available": not gaps,
    }


class SpanBuilder:
    def __init__(self, dataset: Dataset, config: ClientConfig, scope: str,
                 year: int | None = None, project_root: Path | None = None,
                 periods_elsewhere: dict[str, list[str]] | None = None,
                 selection: dict | None = None):
        self.ds, self.cfg, self.scope, self.year = dataset, config, scope, year
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.sheet = dataset.income_statement
        self.keys = self.sheet.period_keys()
        self.available = set(self.keys)
        self.filename = Path(dataset.source_path).name
        if scope == "year":
            self.months = [p for p in self.keys if p.startswith(f"{year:04d}-")]
            if not self.months:
                years = sorted({k[:4] for k in self.keys})
                raise SpanError(
                    f"Year {year} is not in {self.filename}. Years it covers: "
                    f"{', '.join(years) or 'none'}.")
        else:
            self.months = list(self.keys)
            if not self.months:
                raise SpanError(f"{self.filename} holds no months.")
        self.years = sorted({int(p[:4]) for p in self.months})
        self.fig = {p: month_figures(self.sheet, config, p) for p in self.keys}
        self.elsewhere = {p: names for p, names in (periods_elsewhere or {}).items()
                          if p not in self.available}
        self.selection = selection

        self.streams = self._ordered("streams")
        self.categories = self._ordered("categories")
        self.has_net_income = any(NET_INCOME in self.fig[p].values for p in self.keys)

        self.val: ValidationReport = validate_span(dataset, config, self.months,
                                                   self._affects_for)
        full = reconcile(dataset, config)
        wanted = set(self.months)
        self.rec = ReconciliationReport(
            tolerance_abs=full.tolerance_abs,
            results=[r for r in full.results if r.period in wanted])
        self.metrics = MetricSet()
        self.limitations: list[str] = []
        self.extra: dict = {}

    # ---- names -------------------------------------------------------------

    def _ordered(self, attr: str) -> list[str]:
        out: list[str] = []
        for p in self.months:
            for name in getattr(self.fig[p], attr):
                if name not in out:
                    out.append(name)
        return out

    def _prefix(self, year: int) -> str:
        return "" if self.scope == "year" else f"year.{year:04d}."

    def _total_measures(self) -> list[str]:
        """Currency measures with a year total, in statement order."""
        names = list(HEADLINE)
        if self.scope == "year":
            names[1:1] = self.streams                 # streams sit under revenue
            names += self.categories
        if self.has_net_income:
            names.append(NET_INCOME)
        return names

    def _year_names(self, year: int) -> set[str]:
        """Every year-total and year-on-year metric for `year`: what a gap blocks."""
        prefix = self._prefix(year)
        names = {prefix + k for k in self._total_measures()}
        names |= {prefix + k for k in MARGINS}
        if self.scope == "year":
            for k in HEADLINE:
                names |= {f"{k}.prior_year", f"{k}.yoy_abs", f"{k}.yoy_pct"}
            for k in MARGINS:
                names |= {f"{k}.prior_year", f"{k}.yoy_pts"}
        return names

    def _affects_for(self, period: str, item) -> set[str]:
        """The metrics whose value includes `period` (and, given an item, that line)."""
        year = int(period[:4])
        prefix = self._prefix(year)
        quarter = (int(period[5:]) - 1) // 3 + 1
        nxt = _shift(period, 1)
        if item is None:
            measures = set(HEADLINE) | set(MARGINS)
            names = {f"monthly.{period}."}
            names |= {prefix + k for k in self._total_measures()}
        else:
            section = item.section
            measures = {"operating_income", "operating_margin_pct"}
            if section == "revenue":
                measures |= {"revenue.total", "gross_profit", "gross_margin_pct"}
            elif section == "cogs":
                measures |= {"cogs.total", "gross_profit", "gross_margin_pct"}
            elif section == "opex":
                measures |= {"opex.total"}
            names = {f"monthly.{period}.{k}" for k in measures}
            from finance_engine import naming
            if section == "revenue":
                names.add(prefix + naming.revenue_stream(item.label))
            if section == "opex":
                names.add(prefix + naming.opex_category(
                    item.category or self.cfg.fallback_opex_category))
        for k in measures:
            names.add(prefix + k)
            if k in HEADLINE:
                names |= {f"monthly.{period}.{k}.{s}" for s in CHANGE_SUFFIXES}
                names |= {f"monthly.{nxt}.{k}.{s}" for s in CHANGE_SUFFIXES}
                if self.scope == "year":
                    names |= {f"{k}.q{quarter}", f"{k}.prior_year", f"{k}.yoy_abs",
                              f"{k}.yoy_pct", f"extremes.{k}."}
                else:
                    names |= {f"{k}.start_to_end_abs", f"{k}.start_to_end_pct",
                              f"extremes.{k}."}
            else:
                names |= {f"monthly.{period}.{k}.mom_pts", f"monthly.{nxt}.{k}.mom_pts"}
                names |= ({f"{k}.prior_year", f"{k}.yoy_pts"} if self.scope == "year"
                          else {f"{k}.start_to_end_pts"})
        return names

    # ---- emission ------------------------------------------------------------

    def _emit(self, name: str, value: float | None, unit: str, method_id: str,
              source_rows: list[dict] | None = None, reason: str | None = None,
              label: str | None = None, notes: list[str] | None = None,
              period: str | None = None) -> Metric:
        """As `MetricsBuilder._emit`: an error blocks, a warning travels."""
        issues = self.val.for_metric(name)
        blocked = any(i.severity == "error" for i in issues)
        warnings = [i.message for i in issues if i.severity == "warning"]
        if blocked:
            status, value, reason = BLOCKED, None, reason or "blocked_by_validation"
        elif warnings:
            status = WARNING
        else:
            status = OK
        if value is None and reason is None:
            reason = "missing_input"
        return self.metrics.add(Metric(
            name=name, client_id=self.cfg.client_id, period=period or self._doc_period(),
            value=value, unit=unit, method_id=method_id, source_rows=source_rows or [],
            validation_status=status, warnings=warnings,
            unavailable_reason=reason if value is None else None,
            label=label, notes=notes or []))

    def _doc_period(self) -> str:
        return f"{self.year:04d}" if self.scope == "year" else "all"

    def _not_in_file_reason(self, period: str) -> tuple[str, str]:
        names = self.elsewhere.get(period)
        if names:
            return "in_other_file", (f"Not available: prior period {period} is in a "
                                     f"different file ({', '.join(names)}). Only "
                                     f"{self.filename} is read; files are never combined.")
        return "missing_input", f"Not available: {period} is not in the source."

    # ---- the build ---------------------------------------------------------------

    def build(self) -> dict:
        if self.scope == "year":
            cov = year_coverage(self.year, self.keys)
            self.extra["coverage"] = {"scope": "year", **cov}
            self._coverage_metrics(cov["months_present_count"],
                                   len(cov["months_absent"]))
            self._year_totals(self.year)
            self._quarters(self.year)
            self._year_on_year(self.year)
            self._monthly(self.months)
            self._extremes()
        else:
            self._coverage_all()
            self._monthly(self.months)
            years = []
            for year in self.years:
                cov = year_coverage(year, self.keys)
                self._year_totals(year)
                years.append({**cov, "metric_prefix": self._prefix(year)})
            self.extra["years"] = years
            self._start_to_end()
            self._extremes()
            self._series()
        self._seasonality()
        self._limitations()
        return self._document()

    def _coverage_metrics(self, present: int, missing: int) -> None:
        self._emit("coverage.months_present", float(present), COUNT, "count_v1",
                   label="Months present",
                   notes=["Months the source holds for this scope."])
        self._emit("coverage.months_missing", float(missing), COUNT, "count_v1",
                   label="Months missing",
                   notes=["Calendar months with no column in the source for this scope."])
        self._emit("coverage.months_in_year", 12.0, COUNT, "count_v1",
                   label="Months in a calendar year",
                   notes=["The 12 in 'N of 12 months'."])

    def _coverage_all(self) -> None:
        first, last = self.months[0], self.months[-1]
        spanned = Period.from_key(last).ordinal() - Period.from_key(first).ordinal() + 1
        gaps = [p for p in (_shift(first, i) for i in range(spanned))
                if p not in self.available]
        self.extra["coverage"] = {
            "scope": "all", "first": first, "last": last,
            "months_present": list(self.months),
            "months_present_count": len(self.months),
            "calendar_months_spanned": spanned, "gaps": gaps, "contiguous": not gaps,
            "label": (f"{_display(first)} to {_display(last)}, {len(self.months)} months"
                      + (f", {len(gaps)} missing inside" if gaps else "")),
        }
        self._coverage_metrics(len(self.months), len(gaps))
        if gaps:
            self.val.warn(
                "missing_months",
                f"The workbook's month columns are not contiguous. Missing: "
                f"{', '.join(gaps)}. Month-over-month changes into a missing month, and "
                f"the totals of a year with a month missing inside it, are reported as "
                f"unavailable rather than computed across the gap.",
                (), missing=gaps)

    def _year_totals(self, year: int) -> None:
        cov = year_coverage(year, self.keys)
        months, gaps = cov["months_present"], cov["gaps_inside_span"]
        prefix = self._prefix(year)
        if gaps:
            self.val.error(
                "year_months_missing",
                f"{year}: {', '.join(gaps)} {'is' if len(gaps) == 1 else 'are'} missing "
                f"inside the year ({cov['span'][0]}..{cov['span'][1]}). Every {year} total"
                f"{' and the year-on-year comparison' if self.scope == 'year' else ''} is "
                f"blocked rather than summed over the months that remain. Quarters with "
                f"all three months present still compute.",
                tuple(sorted(self._year_names(year))),
                year=year, missing=gaps)
        partial = cov["status"] != "complete"
        basis = (f"Sum of {len(months)} monthly value(s), {months[0]}..{months[-1]}."
                 + (f" {cov['label'].capitalize()}: the months present only, never scaled "
                    f"or annualised." if partial else ""))
        for name in self._total_measures():
            label = LABELS.get(name) or self.fig[months[0]].labels.get(name) or \
                next((self.fig[p].labels[name] for p in self.keys
                      if name in self.fig[p].labels), name)
            if gaps:
                self._emit(prefix + name, None, CURRENCY, "span_sum_v1", [],
                           "missing_month_in_span", label=label,
                           notes=[f"Not available: {', '.join(gaps)} missing inside "
                                  f"{year}."])
                continue
            values = [(p, self.fig[p].values.get(name)) for p in months]
            absent = [p for p, v in values if v is None]
            if absent:
                self._emit(prefix + name, None, CURRENCY, "span_sum_v1", [],
                           "missing_input", label=label,
                           notes=[f"Not available: no value for {', '.join(absent)}. A "
                                  f"total is never computed with a month left out."])
                continue
            # Headline totals trace to the monthly metrics; a stream or category has no
            # monthly metric of its own, so it carries its cells.
            rows = ([] if name in HEADLINE or name == NET_INCOME else
                    [r for p in months for r in self.fig[p].traces.get(name, [])])
            self._emit(prefix + name, sum(v for _, v in values), CURRENCY, "span_sum_v1",
                       rows, label=label,
                       notes=[basis] + ([f"The monthly values are monthly.<YYYY-MM>."
                                         f"{name} for each month listed."]
                                        if name in HEADLINE else []))
        for margin, numerator in MARGIN_NUMERATOR.items():
            value, reason = ratio_pct(self.metrics.value(prefix + numerator),
                                      self.metrics.value(prefix + "revenue.total"))
            self._emit(prefix + margin, value, PERCENT, "margin_pct_v1", [],
                       "missing_month_in_span" if gaps else reason,
                       label=LABELS[margin],
                       notes=[f"{LABELS[numerator]} as a percentage of total revenue, "
                              f"both for the year."])

    def _quarters(self, year: int) -> None:
        quarters = []
        for q, month_numbers in QUARTERS.items():
            months = [_key(year, m) for m in month_numbers]
            missing = [p for p in months if p not in self.available]
            quarters.append({"quarter": f"Q{q}", "months": months,
                             "complete": not missing, "missing": missing})
            for name in HEADLINE:
                label = f"{LABELS[name]}, Q{q}"
                metric = f"{name}.q{q}"
                if missing:
                    self._emit(metric, None, CURRENCY, "quarter_sum_v1", [],
                               "incomplete_quarter", label=label,
                               notes=[f"Q{q} needs {months[0]}..{months[-1]}; "
                                      f"{', '.join(missing)} "
                                      f"{'is' if len(missing) == 1 else 'are'} not in "
                                      f"the source. No partial quarter is presented."])
                    continue
                values = [self.fig[p].values.get(name) for p in months]
                if any(v is None for v in values):
                    self._emit(metric, None, CURRENCY, "quarter_sum_v1", [],
                               "missing_input", label=label,
                               notes=["Not available: a month in the quarter has no value."])
                    continue
                self._emit(metric, sum(values), CURRENCY, "quarter_sum_v1", [],
                           label=label,
                           notes=[f"Sum of monthly.<YYYY-MM>.{name} for "
                                  f"{months[0]}..{months[-1]}."])
        self.extra["quarters"] = quarters

    def _year_on_year(self, year: int) -> None:
        cov = year_coverage(year, self.keys)
        months, gaps = cov["months_present"], cov["gaps_inside_span"]
        prior = [_key(year - 1, int(p[5:])) for p in months]
        absent = [p for p in prior if p not in self.available]
        elsewhere = [p for p in absent if p in self.elsewhere]
        comparable = not gaps and not absent
        if gaps:
            reason = "missing_month_in_span"
            note = (f"Not available: {', '.join(gaps)} missing inside {year}, so the year "
                    f"has no total to compare.")
        elif absent and len(elsewhere) == len(absent):
            names = sorted({n for p in elsewhere for n in self.elsewhere[p]})
            reason = "in_other_file"
            note = (f"Not available: the prior-year months {', '.join(absent)} are in a "
                    f"different file ({', '.join(names)}). Files are never combined.")
        elif absent:
            reason = "not_comparable"
            note = (f"Not available: the prior year does not hold the same months in this "
                    f"file. Missing: {', '.join(absent)}. Year on year is compared only "
                    f"over the same set of months in both years.")
        else:
            reason = None
            note = (f"The same {len(months)} month(s) one year earlier: "
                    f"{prior[0]}..{prior[-1]}.")
        self.extra["yoy"] = {"comparable": comparable, "months": months,
                             "prior_year_months": prior, "missing_in_prior_year": absent,
                             "in_other_file": elsewhere, "note": note}

        prior_totals: dict[str, float | None] = {}
        for name in HEADLINE:
            pv = None
            if comparable:
                values = [self.fig[p].values.get(name) for p in prior]
                pv = None if any(v is None for v in values) else sum(values)
            prior_totals[name] = pv
            cur = self.metrics.value(name)
            label = LABELS[name]
            self._emit(f"{name}.prior_year", pv, CURRENCY, "prior_year_value_v1", [],
                       None if pv is not None else (reason or "missing_input"),
                       label=f"{label}, same months of the prior year", notes=[note])
            change = None if cur is None or pv is None else cur - pv
            self._emit(f"{name}.yoy_abs", change, CURRENCY, "yoy_span_abs_v1", [],
                       None if change is not None else (reason or "missing_input"),
                       label=f"{label}, change on the prior year", notes=[note])
            pct, pct_reason = pct_change(cur, pv)
            self._emit(f"{name}.yoy_pct", pct, PERCENT, "yoy_span_pct_v1", [],
                       (reason or pct_reason) if pct is None else None,
                       label=f"{label}, % change on the prior year", notes=[note])
        for margin, numerator in MARGIN_NUMERATOR.items():
            pv, pv_reason = ratio_pct(prior_totals[numerator], prior_totals["revenue.total"])
            cur = self.metrics.value(margin)
            self._emit(f"{margin}.prior_year", pv, PERCENT, "margin_pct_v1", [],
                       (reason or pv_reason) if pv is None else None,
                       label=f"{LABELS[margin]}, same months of the prior year", notes=[note])
            delta = None if cur is None or pv is None else cur - pv
            self._emit(f"{margin}.yoy_pts", delta, PERCENTAGE_POINTS, "pct_points_delta_v1",
                       [], (reason or "missing_input") if delta is None else None,
                       label=f"{LABELS[margin]}, change on the prior year in points",
                       notes=[note])

    def _monthly(self, months: list[str]) -> None:
        for p in months:
            f = self.fig[p]
            for name in HEADLINE + MARGINS:
                value = f.values.get(name)
                self._emit(f"monthly.{p}.{name}", value,
                           PERCENT if name in MARGINS else CURRENCY, "month_value_v1",
                           f.traces.get(name, []),
                           None if value is not None else f.reasons.get(name),
                           label=f"{LABELS[name]}, {_display(p)}", period=p)
            before = prior_period(p)
            if before in self.available:
                base_fig, base_reason, base_note = self.fig[before], None, None
            else:
                base_fig = None
                base_reason, base_note = self._not_in_file_reason(before)
            for name in HEADLINE:
                cur = f.values.get(name)
                pv = base_fig.values.get(name) if base_fig else None
                change = None if cur is None or pv is None else cur - pv
                notes = [base_note] if base_note else [f"Change from {before} to {p}."]
                self._emit(f"monthly.{p}.{name}.mom_abs", change, CURRENCY, "mom_abs_v1",
                           [], None if change is not None else base_reason,
                           label=f"{LABELS[name]}, change on {before}", notes=notes,
                           period=p)
                pct, pct_reason = pct_change(cur, pv)
                self._emit(f"monthly.{p}.{name}.mom_pct", pct, PERCENT, "mom_pct_v1", [],
                           None if pct is not None else (base_reason or pct_reason),
                           label=f"{LABELS[name]}, % change on {before}", notes=notes,
                           period=p)
            for name in MARGINS:
                cur = f.values.get(name)
                pv = base_fig.values.get(name) if base_fig else None
                delta = None if cur is None or pv is None else cur - pv
                self._emit(f"monthly.{p}.{name}.mom_pts", delta, PERCENTAGE_POINTS,
                           "pct_points_delta_v1", [],
                           None if delta is not None else base_reason,
                           label=f"{LABELS[name]}, change on {before} in points",
                           notes=[base_note] if base_note else [], period=p)

    def _extremes(self) -> None:
        out = {}
        for name in ("revenue.total", "operating_income"):
            have = [(p, self.metrics.value(f"monthly.{p}.{name}")) for p in self.months]
            have = [(p, v) for p, v in have if v is not None]
            entry = {"months_compared": len(have)}
            for which, pick in (("highest", lambda c: (-c[1], c[0])),
                                ("lowest", lambda c: (c[1], c[0]))):
                metric = f"extremes.{name}.{which}"
                if not have:
                    self._emit(metric, None, CURRENCY, "extreme_month_v1", [],
                               "missing_input", label=f"{which.capitalize()} month, "
                                                      f"{LABELS[name].lower()}")
                    entry[which] = None
                    continue
                p, v = sorted(have, key=pick)[0]
                ties = [q for q, w in have if w == v and q != p]
                self._emit(metric, v, CURRENCY, "extreme_month_v1", [],
                           label=f"{which.capitalize()} month for "
                                 f"{LABELS[name].lower()}: {_display(p)}",
                           notes=[f"{p}, out of {len(have)} month(s) with a value."
                                  + (f" Equal in {', '.join(ties)}; the earliest is "
                                     f"shown." if ties else "")],
                           period=p)
                entry[which] = {"period": p, "value": v, "metric": metric, "ties": ties}
            out[name] = entry
        self.extra["extremes"] = out

    def _start_to_end(self) -> None:
        first, last = self.months[0], self.months[-1]
        single = first == last
        note = (f"{last} against {first}, the first and last months in the source."
                if not single else "Not available: the source holds one month.")
        for name in HEADLINE:
            a = self.metrics.value(f"monthly.{first}.{name}")
            b = self.metrics.value(f"monthly.{last}.{name}")
            change = None if single or a is None or b is None else b - a
            self._emit(f"{name}.start_to_end_abs", change, CURRENCY,
                       "start_to_end_abs_v1", [], None if change is not None else
                       "missing_input", label=f"{LABELS[name]}, first to last month",
                       notes=[note])
            pct, reason = (None, "missing_input") if single else pct_change(b, a)
            self._emit(f"{name}.start_to_end_pct", pct, PERCENT, "start_to_end_pct_v1",
                       [], reason if pct is None else None,
                       label=f"{LABELS[name]}, % change first to last month", notes=[note])
        for name in MARGINS:
            a = self.metrics.value(f"monthly.{first}.{name}")
            b = self.metrics.value(f"monthly.{last}.{name}")
            delta = None if single or a is None or b is None else b - a
            self._emit(f"{name}.start_to_end_pts", delta, PERCENTAGE_POINTS,
                       "pct_points_delta_v1", [],
                       None if delta is not None else "missing_input",
                       label=f"{LABELS[name]}, first to last month in points",
                       notes=[note])
        self.extra["start_to_end"] = {"first": first, "last": last}

    def _series(self) -> None:
        """The chart view: one list of points per measure, from the monthly metrics."""
        measures = []
        for name in HEADLINE + MARGINS:
            points = []
            for p in self.months:
                m = self.metrics.get(f"monthly.{p}.{name}")
                point = {"period": p, "value": m.value,
                         "validation_status": m.validation_status}
                if m.value is None:
                    point["unavailable_reason"] = m.unavailable_reason
                points.append(point)
            measures.append({"measure": name, "label": LABELS[name],
                             "unit": PERCENT if name in MARGINS else CURRENCY,
                             "metric_pattern": f"monthly.<YYYY-MM>.{name}",
                             "points": points})
        self.extra["series"] = {"periods": list(self.months), "measures": measures}

    def _seasonality(self) -> None:
        months, needed = len(self.available), 24
        self.seasonality = {
            "assessed": months >= needed, "months_available": months,
            "months_required": needed,
            "reason": "" if months >= needed else (
                f"A seasonality conclusion needs at least {needed} months so that each "
                f"calendar month appears twice. The source covers {months} months, so no "
                f"seasonal pattern is asserted."),
        }

    def _limitations(self) -> None:
        first, last = self.keys[0], self.keys[-1]
        self.limitations.append(
            f"The source covers {first} to {last} ({len(self.keys)} months). Nothing "
            f"outside that range is known to the engine.")
        cov = self.extra.get("coverage") or {}
        if self.scope == "year" and cov.get("status") != "complete":
            self.limitations.append(
                f"{self.year} is {cov.get('label')}. Its totals cover those months only "
                f"and are never scaled, annualised or extrapolated to twelve months.")
        if self.scope == "all":
            partial = [y for y in self.extra.get("years", []) if y["status"] != "complete"]
            if partial:
                self.limitations.append(
                    "Calendar years that are not complete are labelled as such; their "
                    "totals cover the months present only and are never annualised.")
        if self.rec.failures:
            self.limitations.append(
                f"{len(self.rec.failures)} reconciliation check(s) failed in these months; "
                f"figures that depend on the affected totals should not be relied on until "
                f"the discrepancy is explained.")
        if self.elsewhere:
            self.limitations.append(
                f"Only {self.filename} is read. Months held only in other workbooks are "
                f"never combined with it.")
        self.limitations.append(
            "The engine reads the workbook only. It has no access to invoices, contracts, "
            "headcount or any other context, so it can describe what changed but never "
            "why.")
        self.limitations.append(
            "Output is a draft for a human reviewer, not a reviewed financial statement.")

    def _document(self) -> dict:
        if self.scope == "year":
            cov = self.extra["coverage"]
            label = f"{self.year} ({cov['label']})"
        else:
            label = self.extra["coverage"]["label"]
        doc = {
            "schema_version": SCHEMA_VERSION,
            "scope": self.scope,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc)
                                  .replace(microsecond=0).isoformat(),
            "client_id": self.cfg.client_id,
            "company_name": self.cfg.company_name,
            "period": self._doc_period(),
            "period_label": label,
        }
        if self.scope == "year":
            doc["year"] = self.year
        doc.update({
            "currency": self.cfg.currency,
            "currency_symbol": self.cfg.currency_symbol,
            "units_scale": self.cfg.units_scale,
            "source": {
                "config_path": relative_path(self.cfg.path, self.project_root),
                "type": self.ds.source_type,
                "path": relative_path(self.ds.source_path, self.project_root),
                "filename": self.filename,
                "sha256": self.ds.source_sha256,
                "sheets": [self.ds.income_statement.name],
                "sheet_headings": {self.ds.income_statement.name:
                                   self.ds.income_statement.titles},
                "periods_available": self.keys,
                "period_count": len(self.keys),
                **({"selection": self.selection} if self.selection else {}),
            },
            "coverage": self.extra["coverage"],
            "methods": SPAN_METHODS,
            "metrics": self.metrics.to_list(),
        })
        for key in ("quarters", "yoy", "extremes", "years", "start_to_end", "series"):
            if key in self.extra:
                doc[key] = self.extra[key]
        doc.update({
            "seasonality": self.seasonality,
            "reconciliation": self.rec.to_dict(),
            "data_quality_issues": self.val.to_list(),
            "classification_notes": self.cfg.classification_notes,
            "limitations": self.limitations,
        })
        return doc


def build_year_metrics(dataset: Dataset, config: ClientConfig, year: int,
                       project_root: Path | None = None,
                       periods_elsewhere: dict[str, list[str]] | None = None,
                       selection: dict | None = None) -> dict:
    return SpanBuilder(dataset, config, "year", year, project_root, periods_elsewhere,
                       selection).build()


def build_all_metrics(dataset: Dataset, config: ClientConfig,
                      project_root: Path | None = None,
                      periods_elsewhere: dict[str, list[str]] | None = None,
                      selection: dict | None = None) -> dict:
    return SpanBuilder(dataset, config, "all", None, project_root, periods_elsewhere,
                       selection).build()
