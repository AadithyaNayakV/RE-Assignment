"""Reconciliation: our arithmetic against the workbook's own formula totals.

This is the main defence against a misread layout. If the config pointed at the wrong
rows, our independently summed items would stop matching the workbook's cached SUM()
results, and that shows up here as a failure rather than as a plausible-looking report.

Checks run for **every period** in the source, not just the reporting month, so a layout
problem that only affects older columns is still caught.

Tolerance is absolute currency (default 0.01). It exists because the workbook's own cached
results carry IEEE-754 noise — ABC's Total Income for Apr 2026 caches as
204512.41999999998 against an exact item sum of 204512.42. Anything beyond a cent is
treated as a real discrepancy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from finance_engine.config import ClientConfig
from finance_engine.model import Dataset, LineItem, Sheet


@dataclass
class CheckResult:
    key: str
    description: str
    period: str
    computed: float | None
    reported: float | None
    difference: float | None
    passed: bool
    note: str = ""
    evidence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "key": self.key,
            "description": self.description,
            "period": self.period,
            "computed": self.computed,
            "reported": self.reported,
            "difference": self.difference,
            "passed": self.passed,
        }
        if self.note:
            d["note"] = self.note
        if self.evidence:
            d["evidence"] = self.evidence
        return d


@dataclass
class ReconciliationReport:
    tolerance_abs: float
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]

    def by_key(self, key: str) -> list[CheckResult]:
        return [r for r in self.results if r.key == key]

    def failed_keys(self) -> set[str]:
        return {r.key for r in self.failures}

    def summary(self) -> dict:
        keys: dict[str, dict] = {}
        for r in self.results:
            entry = keys.setdefault(
                r.key, {"key": r.key, "description": r.description,
                        "periods_checked": 0, "periods_failed": [],
                        "max_abs_difference": 0.0}
            )
            entry["periods_checked"] += 1
            if not r.passed:
                entry["periods_failed"].append(r.period)
            if r.difference is not None:
                entry["max_abs_difference"] = max(
                    entry["max_abs_difference"], abs(r.difference)
                )
        for entry in keys.values():
            entry["passed"] = not entry["periods_failed"]
        return {
            "tolerance_abs": self.tolerance_abs,
            "all_passed": self.passed,
            "checks_run": len(self.results),
            "checks_failed": len(self.failures),
            "by_check": list(keys.values()),
        }

    def to_dict(self) -> dict:
        d = self.summary()
        d["failures"] = [r.to_dict() for r in self.failures]
        return d


def reconcile(dataset: Dataset, config: ClientConfig) -> ReconciliationReport:
    tol = config.tolerance_abs
    report = ReconciliationReport(tolerance_abs=tol)
    sheet = dataset.income_statement
    descriptions = {
        c["key"]: c.get("description", c["key"])
        for c in (config.raw.get("reconciliation") or {}).get("checks", [])
    }

    def describe(key: str) -> str:
        return descriptions.get(key, key)

    for period in sheet.period_keys():
        _section_check(report, sheet, config, "revenue", "revenue_items_vs_total",
                       describe("revenue_items_vs_total"), period, tol, grouped=False)
        _section_check(report, sheet, config, "cogs", "cogs_items_vs_total",
                       describe("cogs_items_vs_total"), period, tol, grouped=False)
        _section_check(report, sheet, config, "opex", "opex_items_vs_total",
                       describe("opex_items_vs_total"), period, tol, grouped=True)
        _group_checks(report, sheet, config, period, tol,
                      describe("opex_group_subtotals"))
        _gross_profit_check(report, sheet, config, period, tol, describe("gross_profit"))
        _operating_income_check(report, sheet, config, period, tol,
                                describe("operating_income"))

    _balance_sheet_checks(report, dataset, config, tol,
                          describe("balance_sheet_balances"))
    return report


# ---- checks ------------------------------------------------------------

def _sum_present(items: list[LineItem], period: str) -> tuple[float | None, int, int]:
    """Sum the values that exist. Returns (total, present_count, missing_count).

    A section where every member is missing yields `None`, not 0.0 — the difference
    between "adds up to nothing" and "we have nothing" matters.
    """
    total, present, missing = 0.0, 0, 0
    for item in items:
        v = item.value(period)
        if v is None:
            missing += 1
        else:
            total += v
            present += 1
    return (total if present else None), present, missing


def _record(report: ReconciliationReport, key: str, description: str, period: str,
            computed: float | None, reported: float | None, tol: float,
            note: str = "", evidence: list[dict] | None = None) -> None:
    if computed is None or reported is None:
        diff = None
        passed = False
        note = (note + " Cannot compare: "
                f"{'computed' if computed is None else 'reported'} value unavailable.").strip()
    else:
        diff = computed - reported
        passed = abs(diff) <= tol
    report.results.append(
        CheckResult(key=key, description=description, period=period, computed=computed,
                    reported=reported, difference=diff, passed=passed, note=note,
                    evidence=evidence or [])
    )


def _section_check(report, sheet: Sheet, config: ClientConfig, section: str, key: str,
                   description: str, period: str, tol: float, grouped: bool) -> None:
    total_row = sheet.section_total(section)
    if total_row is None:
        return
    if grouped:
        # Mirror the workbook: direct items plus each group's own subtotal, once.
        parts = sheet.direct_items(section) + sheet.group_totals(section)
    else:
        parts = sheet.in_section(section)
    computed, present, missing = _sum_present(parts, period)
    note = f"{present} row(s) summed" + (f", {missing} missing" if missing else "")
    _record(report, key, description, period, computed, total_row.value(period), tol,
            note=note,
            evidence=[{"sheet": total_row.sheet, "row": total_row.row,
                       "label": total_row.label,
                       "formula": total_row.cells.get(period).formula
                       if total_row.cells.get(period) else None}])


def _group_checks(report, sheet: Sheet, config: ClientConfig, period: str, tol: float,
                  description: str) -> None:
    for total_row in sheet.group_totals("opex"):
        members = sheet.group_members(total_row.group_key)
        computed, present, missing = _sum_present(members, period)
        _record(report, "opex_group_subtotals",
                f"{description} — {total_row.group}", period,
                computed, total_row.value(period), tol,
                note=f"{present} member row(s) summed"
                     + (f", {missing} missing" if missing else ""),
                evidence=[{"sheet": total_row.sheet, "row": total_row.row,
                           "label": total_row.label, "group": total_row.group}])


def _gross_profit_check(report, sheet: Sheet, config: ClientConfig, period: str,
                        tol: float, description: str) -> None:
    label = config.reported_lines.get("gross_profit")
    reported_row = sheet.reported(label) if label else None
    rev_total = sheet.section_total("revenue")
    cogs_total = sheet.section_total("cogs")
    if reported_row is None or rev_total is None or cogs_total is None:
        return
    rev, cogs = rev_total.value(period), cogs_total.value(period)
    computed = None if rev is None or cogs is None else rev - cogs
    _record(report, "gross_profit", description, period, computed,
            reported_row.value(period), tol,
            note="Total Income minus Total COGS, both taken from the workbook's own totals",
            evidence=[{"sheet": reported_row.sheet, "row": reported_row.row,
                       "label": reported_row.label}])


def _operating_income_check(report, sheet: Sheet, config: ClientConfig, period: str,
                            tol: float, description: str) -> None:
    label = config.reported_lines.get("operating_income")
    reported_row = sheet.reported(label) if label else None
    gp_label = config.reported_lines.get("gross_profit")
    gp_row = sheet.reported(gp_label) if gp_label else None
    opex_total = sheet.section_total("opex")
    if reported_row is None or gp_row is None or opex_total is None:
        return
    gp, opex = gp_row.value(period), opex_total.value(period)
    computed = None if gp is None or opex is None else gp - opex
    _record(report, "operating_income", description, period, computed,
            reported_row.value(period), tol,
            note="Gross Profit minus Total Operating Expenses",
            evidence=[{"sheet": reported_row.sheet, "row": reported_row.row,
                       "label": reported_row.label}])


def _balance_sheet_checks(report, dataset: Dataset, config: ClientConfig, tol: float,
                          description: str) -> None:
    bs = dataset.balance_sheet
    if bs is None:
        return
    cfg_totals = config.balance_sheet.get("reported_totals") or {}
    assets_label = cfg_totals.get("assets")
    le_label = cfg_totals.get("liabilities_and_equity")
    if not assets_label or not le_label:
        return
    assets_row = bs.by_label(assets_label)
    le_row = bs.by_label(le_label)
    if assets_row is None or le_row is None:
        return
    for period in bs.period_keys():
        _record(report, "balance_sheet_balances", description, period,
                assets_row.value(period), le_row.value(period), tol,
                note="Total Assets against Total Liabilities and Equity",
                evidence=[
                    {"sheet": bs.name, "row": assets_row.row, "label": assets_row.label},
                    {"sheet": bs.name, "row": le_row.row, "label": le_row.label},
                ])
