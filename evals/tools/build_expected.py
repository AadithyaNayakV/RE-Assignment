"""Writes evals/cases/<case>/expected.json from sources independent of finance_engine.

    python evals/tools/build_expected.py            # all cases
    python evals/tools/build_expected.py 04      # id prefix, like the runners

Where the expected numbers come from:

* **Synthetic cases**: the generator's own inputs (`scripts/generate_synthetic.py`,
  `scenarios()` and `compute_expected()`), which never import finance_engine. Derived
  values (margins, % changes, shares, TTM sums) are worked out here by hand-written
  arithmetic, following the definitions in docs/metrics_schema.md, not the engine code.
* **ABC Inc**: the workbook's own cached formula results (Total Income, Total COGS,
  Gross Profit, Total Operating Expenses, Net Income, subtotal rows, balance-sheet
  totals), plus single line cells read as spot checks. Rows are found by their label,
  columns by their date heading, with openpyxl alone.

The script refuses to finish if finance_engine was imported, so the independence is
checked, not just claimed. Re-run it only when a case's *input* changes. Its output is
committed and reviewed like code: a changed expected.json is a claim that needs evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import openpyxl
import yaml

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "evals" / "cases"
sys.path.insert(0, str(ROOT / "scripts"))

import generate_synthetic as gen  # noqa: E402  (imports xlsx_builder only)

sys.path.insert(0, str(ROOT))
from evals.common import ensure_generated_workbooks, workbook_fingerprint  # noqa: E402

CURRENCY_TOL = 0.01      # one cent: the engine's reconciliation tolerance
PERCENT_TOL = 0.0005     # display is 1 dp; this is far tighter than that


# ---- small helpers ------------------------------------------------------

def slug(text: str) -> str:
    """The metric naming convention in docs/metrics_schema.md, written out here."""
    return re.sub(r"[^a-z0-9]+", "_", str(text).casefold()).strip("_") or "unnamed"


def prior_of(period: str) -> str:
    y, m = int(period[:4]), int(period[5:])
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def year_before(period: str) -> str:
    return f"{int(period[:4]) - 1:04d}-{period[5:]}"


def window_of(period: str, months: int = 12) -> list[str]:
    out, key = [], period
    for _ in range(months):
        out.append(key)
        key = prior_of(key)
    return list(reversed(out))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Expect:
    """Collects expected values and expected n/a results, each with its derivation."""

    def __init__(self):
        self.values: list[dict] = []
        self.unavailable: list[dict] = []

    def value(self, metric: str, expected: float | None, derivation: str,
              unit: str = "currency") -> None:
        if expected is None:
            return
        tol = CURRENCY_TOL if unit == "currency" else PERCENT_TOL
        self.values.append({"metric": metric, "expected": expected, "tolerance": tol,
                            "unit": unit, "derivation": derivation})

    def na(self, metric: str, reason: str, derivation: str) -> None:
        self.unavailable.append({"metric": metric, "reason": reason,
                                 "derivation": derivation})

    def change(self, base: str, current: float | None, previous: float | None,
               what: str, against: str) -> None:
        """`<base>.mom_abs` and `<base>.mom_pct` per the schema's rules."""
        if current is None or previous is None:
            return
        self.value(f"{base}.mom_abs", round(current - previous, 6),
                   f"{what}: {current} - {previous} ({against})")
        self.pct(f"{base}.mom_pct", current, previous, what, against)

    def pct(self, metric: str, current: float, previous: float, what: str,
            against: str) -> None:
        if previous == 0:
            self.na(metric, "zero_base", f"{what}: {against} was exactly 0")
        elif previous < 0:
            self.na(metric, "negative_base", f"{what}: {against} was {previous}")
        elif current < 0:
            self.na(metric, "sign_change", f"{what}: {previous} -> {current} crosses zero")
        else:
            self.value(metric, (current - previous) / previous * 100.0,
                       f"{what}: ({current} - {previous}) / {previous} x 100", "percent")


# ---- synthetic cases ----------------------------------------------------

def synthetic_expected(client_id: str, period: str) -> dict:
    spec = next(s for s in gen.scenarios() if s["client_id"] == client_id)
    exp = gen.compute_expected(spec)
    pp = exp["per_period"]
    keys = spec["periods"]
    cur, prev_key = pp[period], prior_of(period)
    prev = pp.get(prev_key)
    e = Expect()
    src = f"generator inputs for {client_id}, {period}"

    rev, cogs, opex = cur["total_revenue"], cur["total_cogs"], cur["total_opex"]
    gp, oi = cur["gross_profit"], cur["operating_income"]
    e.value("revenue.total", rev, f"sum of revenue inputs ({src})")
    e.value("cogs.total", cogs, f"sum of COGS inputs ({src})")
    e.value("gross_profit", gp, "revenue - COGS")
    e.value("opex.total", opex, f"sum of every opex input, groups counted once ({src})")
    e.value("operating_income", oi, "gross profit - opex")
    if rev and rev > 0:
        e.value("gross_margin_pct", gp / rev * 100, f"{gp} / {rev} x 100", "percent")
        e.value("operating_margin_pct", oi / rev * 100, f"{oi} / {rev} x 100", "percent")

    if prev is not None:
        for base, field in (("revenue.total", "total_revenue"),
                            ("cogs.total", "total_cogs"),
                            ("gross_profit", "gross_profit"),
                            ("opex.total", "total_opex"),
                            ("operating_income", "operating_income")):
            e.change(base, cur[field], prev[field], base, prev_key)
        if rev and prev["total_revenue"]:
            gm_now = gp / rev * 100
            gm_prev = prev["gross_profit"] / prev["total_revenue"] * 100
            e.value("gross_margin_pct.mom_pts", gm_now - gm_prev,
                    f"gross margin {gm_now:.6f} - {gm_prev:.6f} points", "percent")
    else:
        e.na("revenue.total.mom_abs", "missing_input", f"{prev_key} is not in the source")

    yb = year_before(period)
    if yb in pp:
        e.value("revenue.total.yoy_abs", round(rev - pp[yb]["total_revenue"], 6),
                f"{rev} - {pp[yb]['total_revenue']} ({yb})")
        e.pct("revenue.total.yoy_pct", rev, pp[yb]["total_revenue"], "revenue YoY", yb)

    for name, value in cur["revenue_by_stream"].items():
        base = f"revenue.stream.{slug(name)}"
        e.value(base, value, f"input '{name}' {period}")
        if value is not None and rev and rev > 0:
            e.value(f"{base}.share_of_revenue_pct", value / rev * 100,
                    f"{value} / {rev} x 100", "percent")
        if prev is not None:
            e.change(base, value, prev["revenue_by_stream"].get(name), name, prev_key)

    for name, value in cur["opex_by_line"].items():
        base = f"opex.line.{slug(name)}"
        e.value(base, value, f"input '{name}' {period}")
        if prev is not None:
            e.change(base, value, prev["opex_by_line"].get(name), name, prev_key)

    window = window_of(period)
    complete = all(k in keys for k in window)
    ttm_names = ("revenue.total.ttm", "cogs.total.ttm", "gross_profit.ttm",
                 "gross_margin_pct.ttm", "opex.total.ttm", "operating_income.ttm")
    if complete:
        def s(field):
            return sum(pp[k][field] for k in window)
        r, c, o = s("total_revenue"), s("total_cogs"), s("total_opex")
        span = f"sum over {window[0]}..{window[-1]}"
        e.value("revenue.total.ttm", r, f"revenue {span}")
        e.value("cogs.total.ttm", c, f"COGS {span}")
        e.value("opex.total.ttm", o, f"opex {span}")
        e.value("gross_profit.ttm", r - c, "TTM revenue - TTM COGS")
        e.value("operating_income.ttm", r - c - o, "TTM gross profit - TTM opex")
        e.value("gross_margin_pct.ttm", (r - c) / r * 100,
                "TTM gross profit / TTM revenue x 100", "percent")
    else:
        missing = [k for k in window if k not in keys]
        for name in ttm_names:
            e.na(name, "incomplete_window",
                 f"window {window[0]}..{window[-1]} lacks {', '.join(missing)}")

    cells = cell_warnings_expected(spec, period)

    if spec["balance_sheet"]:
        bs, i = spec["balance_sheet"], keys.index(period)
        a = sum(v[i] for lines in bs["assets"].values() for v in lines.values())
        li = sum(v[i] for v in bs["liabilities"].values())
        cash = sum(v[i] for v in bs["assets"]["Cash"].values())
        e.value("balance_sheet.assets", a, "sum of every asset input")
        e.value("balance_sheet.liabilities", li, "sum of every liability input")
        e.value("balance_sheet.equity", a - li, "assets - liabilities (the generator's plug)")
        e.value("balance_sheet.liabilities_and_equity", a, "liabilities + equity = assets")
        e.value("balance_sheet.cash", cash, "Checking Account + Savings Account")

    out = {
        "source": ("scripts/generate_synthetic.py scenarios() inputs and "
                   "compute_expected(); derived values computed in "
                   "evals/tools/build_expected.py"),
        "ttm": {"complete": complete, "window": window},
        "values": e.values,
        "unavailable": e.unavailable,
    }
    # Written only when a cell warning is expected, so a case with none keeps the
    # expected.json it already had.
    if any(cells["expected"].values()):
        out["cell_warnings"] = cells
    return out


# ---- cell-level warnings ---------------------------------------------------

#: The documented defaults (docs/metrics_schema.md, "Cell-level checks"), written out
#: here rather than imported, like every other rule in this file.
CELL_CHECK_DEFAULTS = {"non_numeric_cell": True, "blank_cell": True,
                       "negative_line_item": False}


def _enabled_cell_checks(client_id: str) -> dict[str, bool]:
    raw = yaml.safe_load((ROOT / "config" / "clients" / f"{client_id}.yaml")
                         .read_text(encoding="utf-8"))
    setting = (raw.get("data_quality") or {}).get("cell_checks")
    if setting is None:
        return dict(CELL_CHECK_DEFAULTS)
    if isinstance(setting, dict):
        return {k: bool(setting.get(k, d)) for k, d in CELL_CHECK_DEFAULTS.items()}
    return dict.fromkeys(CELL_CHECK_DEFAULTS, bool(setting))


def cell_warnings_expected(spec: dict, period: str) -> dict:
    """Which line-item cells the cell-level checks should name, from generator inputs.

    Rules, per docs/metrics_schema.md: leaf lines in revenue, COGS and opex; months in
    the trailing window ending at `period` plus the year-ago month; text that is not a
    number -> non_numeric_cell; empty with values in the neighbouring *columns* (the
    months present in the workbook) -> blank_cell; negative and not listed in
    expected_negative_lines -> negative_line_item. Addresses are read from the generated
    workbook with openpyxl: the row by its label, the column by its month heading.
    """
    enabled = _enabled_cell_checks(spec["client_id"])
    keys = spec["periods"]
    scope = [k for k in keys if k in window_of(period) or k == year_before(period)]
    raw_cfg = yaml.safe_load((ROOT / "config" / "clients" / f"{spec['client_id']}.yaml")
                             .read_text(encoding="utf-8"))
    expected_negative = {str(x).casefold()
                         for x in (raw_cfg.get("expected_negative_lines") or [])}

    lines = {**spec["revenue"], **spec["cogs"], **spec["opex_direct"]}
    for group in spec["groups"]:
        lines.update(group["items"])

    grid: list = []          # read on first use: only a case with a hit needs addresses

    def entry(label: str, key: str, raw) -> dict:
        if not grid:
            wb = openpyxl.load_workbook(
                ROOT / "data" / "synthetic" / f"{spec['client_id']}.xlsx",
                data_only=True, read_only=True)
            label_cols = openpyxl.utils.column_index_from_string(spec["item_col"])
            grid.extend(_grid(wb[spec["sheet_name"]], label_cols)[:2])
        labels, cols = grid
        address = f"{openpyxl.utils.get_column_letter(cols[key] + 1)}{labels[label] + 1}"
        return {"sheet": spec["sheet_name"], "cell": address, "label": label,
                "period": key, "raw_value": raw}

    found: dict[str, list[dict]] = {code: [] for code, on in enabled.items() if on}
    for label, values in lines.items():
        for i, key in enumerate(keys):
            if key not in scope:
                continue
            v = values[i]
            if "non_numeric_cell" in found and isinstance(v, str):
                found["non_numeric_cell"].append(entry(label, key, v))
            if ("blank_cell" in found and v is None and 0 < i < len(keys) - 1
                    and values[i - 1] is not None and values[i + 1] is not None):
                found["blank_cell"].append(entry(label, key, None))
            if ("negative_line_item" in found and isinstance(v, (int, float)) and v < 0
                    and label.casefold() not in expected_negative):
                found["negative_line_item"].append(entry(label, key, v))
    return {
        "derivation": ("generator inputs (None / text / negative values), the client "
                       "config's data_quality.cell_checks over the documented defaults, "
                       "and cell addresses read from the workbook with openpyxl"),
        "enabled": enabled,
        "expected": found,
    }


# ---- ABC Inc ------------------------------------------------------------

def _grid(ws, label_cols: int) -> tuple[dict[str, int], dict[str, int], dict]:
    """label -> row, 'Mon YYYY' -> column, and the cell values, from cached results."""
    rows = list(ws.iter_rows(values_only=True))
    header_idx = next(i for i, r in enumerate(rows)
                      if any(isinstance(v, str) and re.fullmatch(
                          r"(\d{1,2} )?[A-Z][a-z]{2} \d{4}", v.strip()) for v in r))
    months = {}
    for c, v in enumerate(rows[header_idx]):
        if isinstance(v, str):
            m = re.fullmatch(r"(?:\d{1,2} )?([A-Z][a-z]{2}) (\d{4})", v.strip())
            if m:
                months[_key(m.group(1), m.group(2))] = c
    labels = {}
    for r, row in enumerate(rows):
        for v in row[:label_cols]:
            if isinstance(v, str) and v.strip() and v.strip() not in labels:
                labels[v.strip()] = r
    return labels, months, rows


def _key(mon: str, year: str) -> str:
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
             "Nov", "Dec"]
    return f"{year}-{names.index(mon) + 1:02d}"


def abc_expected(case: dict, subset: bool) -> dict:
    path = ROOT / case["input"]["workbook"]
    period = case["input"]["period"]
    prev_key = prior_of(period)
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    is_labels, is_cols, is_rows = _grid(wb["Income Statement"], 2)
    bs_labels, bs_cols, bs_rows = _grid(wb["Balance Sheet"], 3)

    def cell(label: str, month: str, sheet: str = "IS") -> float:
        labels, cols, rows = ((is_labels, is_cols, is_rows) if sheet == "IS"
                              else (bs_labels, bs_cols, bs_rows))
        value = rows[labels[label]][cols[month]]
        return float(value or 0.0) if value is not None else None

    def where(label: str, month: str, sheet: str = "IS") -> str:
        labels, cols = (is_labels, is_cols) if sheet == "IS" else (bs_labels, bs_cols)
        name = "Income Statement" if sheet == "IS" else "Balance Sheet"
        return (f"{name}!{openpyxl.utils.get_column_letter(cols[month] + 1)}"
                f"{labels[label] + 1} '{label}' {month}")

    e = Expect()
    ti, ti_p = cell("Total Income", period), cell("Total Income", prev_key)
    gp, gp_p = cell("Gross Profit", period), cell("Gross Profit", prev_key)
    opex = cell("Total Operating Expenses", period)
    ni = cell("Net Income", period)
    e.value("revenue.total", ti, f"cached formula {where('Total Income', period)}")
    e.value("gross_margin_pct", gp / ti * 100,
            f"{where('Gross Profit', period)} / Total Income x 100 = {gp} / {ti}",
            "percent")
    e.value("opex.total", opex, f"cached formula {where('Total Operating Expenses', period)}")
    e.value("operating_income", gp - opex,
            f"Gross Profit - Total Operating Expenses = {gp} - {opex}; the workbook's "
            f"Net Income row reads {ni}")
    if not subset:
        cogs = cell("Total Cost of Goods Sold", period)
        e.value("cogs.total", cogs,
                f"cached formula {where('Total Cost of Goods Sold', period)}")
        e.value("gross_profit", gp, f"cached formula {where('Gross Profit', period)}")
        e.value("net_income.reported", ni, f"cached formula {where('Net Income', period)}")
        e.change("revenue.total", ti, ti_p, "Total Income", prev_key)
        e.value("gross_profit.mom_abs", gp - gp_p, f"{gp} - {gp_p}")
        e.value("opex.total.mom_abs", opex - cell("Total Operating Expenses", prev_key),
                "Total Operating Expenses June - May")
        e.value("gross_margin_pct.mom_pts", gp / ti * 100 - gp_p / ti_p * 100,
                "gross margin June - May, in points", "percent")
        e.value("operating_margin_pct", (gp - opex) / ti * 100,
                "operating income / Total Income x 100", "percent")
        yb = year_before(period)
        e.value("revenue.total.yoy_abs", ti - cell("Total Income", yb),
                f"Total Income {period} - {yb}")

        window = window_of(period)
        r = sum(cell("Total Income", k) for k in window)
        c = sum(cell("Total Cost of Goods Sold", k) for k in window)
        g = sum(cell("Gross Profit", k) for k in window)
        o = sum(cell("Total Operating Expenses", k) for k in window)
        span = f"sum of the cached row over {window[0]}..{window[-1]}"
        e.value("revenue.total.ttm", r, f"Total Income {span}")
        e.value("cogs.total.ttm", c, f"Total Cost of Goods Sold {span}")
        e.value("gross_profit.ttm", g, f"Gross Profit {span}")
        e.value("opex.total.ttm", o, f"Total Operating Expenses {span}")
        e.value("operating_income.ttm", g - o, "TTM Gross Profit - TTM opex")
        e.value("gross_margin_pct.ttm", g / r * 100, "TTM GP / TTM revenue x 100",
                "percent")

        for label, metric in (("Total S & M Payroll", "opex.group.s_and_m_payroll"),
                              ("Total Marketing", "opex.group.marketing")):
            e.value(metric, cell(label, period), f"cached formula {where(label, period)}")
            e.value(f"{metric}.mom_abs", cell(label, period) - cell(label, prev_key),
                    f"{label} June - May")

        # Spot checks: single input cells, read directly.
        for label in ("Usage Fees",):
            base = f"revenue.stream.{slug(label)}"
            e.value(base, cell(label, period), f"spot check {where(label, period)}")
            e.change(base, cell(label, period), cell(label, prev_key), label, prev_key)
        for label in ("Events & Conferences", "Bad Debts", "Legal Expenses"):
            base = f"opex.line.{slug(label)}"
            e.value(base, cell(label, period), f"spot check {where(label, period)}")
            e.change(base, cell(label, period), cell(label, prev_key), label, prev_key)

        bs_period = period
        for label, metric in (("Total Assets", "balance_sheet.assets"),
                              ("Total Liabilities", "balance_sheet.liabilities"),
                              ("Total Equity", "balance_sheet.equity"),
                              ("Total Liabilities and Equity",
                               "balance_sheet.liabilities_and_equity"),
                              ("Total Cash and Cash Equivalents", "balance_sheet.cash")):
            e.value(metric, cell(label, bs_period, "BS"),
                    f"cached formula {where(label, bs_period, 'BS')}")

    evidence = {}
    if not subset:
        window = window_of(period)
        for label in ("Income Tax Benefit", "Stock-Based Compensation Expense"):
            nonzero = {k: cell(label, k) for k in window if cell(label, k)}
            evidence[f"{label} non-zero months in TTM window"] = nonzero
    return {
        "source": ("cached formula results and single cells of the ABC workbook, read "
                   "with openpyxl by evals/tools/build_expected.py"),
        "ttm": {"complete": True, "window": window_of(period)},
        "values": e.values,
        "unavailable": e.unavailable,
        "evidence": evidence,
        "manual_spot_check": ("Open the workbook and confirm: Income Statement C14 Total "
                              "Income 195,898.16; C11 Usage Fees 166,781.50; C65 Events & "
                              "Conferences 29,294.58; C31 Bad Debts -1,806.24; Balance "
                              "Sheet D37 Total Assets 4,191,851.55 = D71 Total Liabilities "
                              "and Equity."),
    }


# ---- test workbooks: year, whole file, file selection ----------------------------
#
# scripts/make_test_workbooks.py writes plain values (no formulas), so the cells are read
# directly. Every rule below is written out from docs/metrics_schema.md, not imported:
#
# * Section membership is positional: the rows strictly between the config's header
#   label and total label. A row with a number in any month is a line item.
# * A section's month value is the sum of its line items' numeric cells; text or an empty
#   cell is missing, never zero. No numeric cell at all -> no value.
# * Gross profit = revenue - COGS; operating income = gross profit - opex; a margin needs
#   revenue > 0. Categories: the config's match_exact, then match_contains, first match
#   wins, else the fallback.
# * A year total sums the months present; a month missing inside the year's span gives no
#   total. A quarter needs all three months. Year on year needs every month present this
#   year to be present the year before. Percentages need a strictly positive base.

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
               "Nov", "Dec"]


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class Book:
    """One income statement read with openpyxl: rows, month columns and sections."""

    def __init__(self, path: Path, cfg: dict):
        self.cfg = cfg
        spec = cfg["sheets"]["income_statement"]
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        rows = [list(r) for r in wb[spec["name"]].iter_rows(values_only=True)]
        header = rows[int(spec["header_row"]) - 1]
        self.cols = {}
        for c, v in enumerate(header):
            m = re.fullmatch(r"([A-Z][a-z]{2}) (\d{4})", str(v or "").strip())
            if m:
                self.cols[f"{m.group(2)}-{MONTH_NAMES.index(m.group(1)) + 1:02d}"] = c
        self.months = sorted(self.cols)
        self.rows = []                       # (row number, label, raw cells by month)
        for i, r in enumerate(rows[int(spec["header_row"]):], start=int(spec["header_row"]) + 1):
            label = next((str(x).strip() for x in r[:2] if isinstance(x, str) and x.strip()),
                         None)
            if label:
                self.rows.append((i, label, {k: r[c] if c < len(r) else None
                                             for k, c in self.cols.items()}))
        self.number = {lbl: n for n, lbl, _ in self.rows}

    def section_items(self, key: str) -> list[tuple[int, str, dict]]:
        sec = next(s for s in self.cfg["income_statement"]["sections"] if s["key"] == key)
        top, bottom = self.number[sec["header_label"]], self.number[sec["total_label"]]
        return [(n, lbl, cells) for n, lbl, cells in self.rows
                if top < n < bottom and any(_num(v) is not None for v in cells.values())]

    def category(self, label: str) -> str:
        text = label.casefold()
        rules = self.cfg.get("opex_categories") or []
        for rule in rules:
            if any(text == str(x).casefold() for x in rule.get("match_exact") or []):
                return rule["name"]
        for rule in rules:
            if any(str(x).casefold() in text for x in rule.get("match_contains") or []):
                return rule["name"]
        return self.cfg.get("fallback_opex_category", "Other Operating Expenses")

    def month(self, key: str) -> dict:
        """Every figure for one month, by metric name. None means no value."""
        def total(items):
            vals = [_num(cells.get(key)) for _, _, cells in items]
            vals = [v for v in vals if v is not None]
            return sum(vals) if vals else None
        out = {}
        rev_items, cogs_items, opex_items = (self.section_items(s)
                                             for s in ("revenue", "cogs", "opex"))
        out["revenue.total"] = total(rev_items)
        for _, lbl, cells in rev_items:
            out[f"revenue.stream.{slug(lbl)}"] = _num(cells.get(key))
        out["cogs.total"] = total(cogs_items)
        out["opex.total"] = total(opex_items)
        cats: dict[str, list] = {}
        for item in opex_items:
            cats.setdefault(self.category(item[1]), []).append(item)
        for name, items in cats.items():
            out[f"opex.category.{slug(name)}"] = total(items)
        r, c, o = out["revenue.total"], out["cogs.total"], out["opex.total"]
        out["gross_profit"] = None if r is None or c is None else r - c
        g = out["gross_profit"]
        out["operating_income"] = None if g is None or o is None else g - o
        for margin, num in (("gross_margin_pct", "gross_profit"),
                            ("operating_margin_pct", "operating_income")):
            out[margin] = (out[num] / r * 100 if r and r > 0 and out[num] is not None
                           else None)
        ni = self.cfg["income_statement"].get("reported_lines", {}).get("net_income")
        if ni in self.number:
            cells = next(c for n, lbl, c in self.rows if lbl == ni)
            out["net_income.reported"] = _num(cells.get(key))
        return out


HEADLINE5 = ["revenue.total", "cogs.total", "gross_profit", "opex.total", "operating_income"]
MARGIN_NUM = {"gross_margin_pct": "gross_profit", "operating_margin_pct": "operating_income"}


def _year_cov(year: int, months: list[str]) -> dict:
    present = [p for p in months if p.startswith(f"{year}-")]
    inside = [f"{year}-{m:02d}" for m in range(1, 13)
              if present and present[0] <= f"{year}-{m:02d}" <= present[-1]]
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
        status, label = "partial", f"partial year, {n} of 12 months ({present[0]} to {present[-1]})"
    return {"present": present, "gaps": gaps, "status": status, "label": label}


def _year_totals(e: Expect, fig: dict, months: list[str], year: int, prefix: str,
                 detail: bool) -> None:
    cov = _year_cov(year, months)
    names = list(HEADLINE5) + (["net_income.reported"]
                               if "net_income.reported" in fig[months[0]] else [])
    if detail:
        names += sorted({k for p in cov["present"] for k in fig[p]
                         if k.startswith(("revenue.stream.", "opex.category."))})
    if cov["gaps"]:
        for name in names + list(MARGIN_NUM):
            e.na(prefix + name, "missing_month_in_span",
                 f"{', '.join(cov['gaps'])} missing inside {year}: no total")
        return
    for name in names:
        vals = [fig[p].get(name) for p in cov["present"]]
        if any(v is None for v in vals):
            e.na(prefix + name, "missing_input", "a month has no value")
            continue
        e.value(prefix + name, sum(vals),
                f"sum of {len(vals)} monthly value(s) {cov['present'][0]}..{cov['present'][-1]}")
    rev = sum(fig[p]["revenue.total"] for p in cov["present"])
    for margin, num in MARGIN_NUM.items():
        top = sum(fig[p][num] for p in cov["present"])
        e.value(prefix + margin, top / rev * 100, f"year {num} / year revenue x 100",
                "percent")


def test_workbook_expected(case: dict) -> dict:
    inp, eng = case["input"], case.get("engine") or {}
    cfg = yaml.safe_load((ROOT / "config" / "clients" / f"{inp['client_id']}.yaml")
                         .read_text(encoding="utf-8"))
    book = Book(ROOT / inp["workbook"], cfg)
    months, e = book.months, Expect()
    fig = {p: book.month(p) for p in months}
    scope = inp.get("scope", "month")
    out: dict = {"source": ("cells of the generated test workbook, read with openpyxl by "
                            "evals/tools/build_expected.py; rules written out there")}

    if scope == "month":
        _month_values(e, fig, months, inp["period"], others=_other_books(case, cfg))
        out["ttm"] = {"complete": all(k in months for k in window_of(inp["period"])),
                      "window": window_of(inp["period"])}
    elif scope == "year":
        year = int(inp["year"])
        cov = _year_cov(year, months)
        _year_totals(e, fig, months, year, "", detail=True)
        for q in range(1, 5):
            qm = [f"{year}-{m:02d}" for m in range(3 * q - 2, 3 * q + 1)]
            for name in HEADLINE5:
                if all(p in months for p in qm):
                    e.value(f"{name}.q{q}", sum(fig[p][name] for p in qm),
                            f"Q{q}: sum of {qm[0]}..{qm[-1]}")
                else:
                    e.na(f"{name}.q{q}", "incomplete_quarter",
                         f"Q{q} lacks {[p for p in qm if p not in months]}")
        prior = [f"{year - 1}-{p[5:]}" for p in cov["present"]]
        comparable = not cov["gaps"] and all(p in months for p in prior)
        for name in HEADLINE5:
            if comparable:
                cur = sum(fig[p][name] for p in cov["present"])
                base = sum(fig[p][name] for p in prior)
                e.value(f"{name}.prior_year", base, f"same months of {year - 1}")
                e.value(f"{name}.yoy_abs", cur - base, f"{cur} - {base}")
                e.pct(f"{name}.yoy_pct", cur, base, name, f"{year - 1} same months")
            else:
                reason = "missing_month_in_span" if cov["gaps"] else "not_comparable"
                for suffix in ("yoy_abs", "yoy_pct", "prior_year"):
                    e.na(f"{name}.{suffix}", reason,
                         "a month missing inside the year" if cov["gaps"]
                         else f"prior-year months {prior} not all in the file")
        for p in cov["present"]:
            for name in ("revenue.total", "opex.total", "operating_income",
                         "gross_margin_pct"):
                e.value(f"monthly.{p}.{name}", fig[p][name], f"{name} for {p}",
                        "percent" if name.endswith("_pct") else "currency")
        e.value("coverage.months_present", float(len(cov["present"])), "months present")
        out["coverage"] = {"status": cov["status"], "label": cov["label"],
                           "months_present_count": len(cov["present"]),
                           "gaps_inside_span": cov["gaps"]}
        out["doc_fields"] = _extreme_fields(fig, cov["present"])
    else:
        first, last = months[0], months[-1]
        for year in sorted({int(p[:4]) for p in months}):
            _year_totals(e, fig, months, year, f"year.{year}.", detail=False)
        for name in HEADLINE5:
            a, b = fig[first][name], fig[last][name]
            e.value(f"{name}.start_to_end_abs", b - a, f"{last} {b} - {first} {a}")
            e.pct(f"{name}.start_to_end_pct", b, a, name, first)
        for margin in MARGIN_NUM:
            e.value(f"{margin}.start_to_end_pts", fig[last][margin] - fig[first][margin],
                    f"{margin} {last} - {first}, in points", "percent")
        for p in months:
            e.value(f"monthly.{p}.revenue.total", fig[p]["revenue.total"], f"revenue {p}")
        spanned = (int(last[:4]) - int(first[:4])) * 12 + int(last[5:]) - int(first[5:]) + 1
        out["coverage"] = {"first": first, "last": last, "months_present_count": len(months),
                           "calendar_months_spanned": spanned, "gaps": [
                               k for k in _month_range(first, last) if k not in months]}
        out["doc_fields"] = _extreme_fields(fig, months)
    if eng.get("cross_check"):
        _cross_check(e, eng["cross_check"])
    cells = _span_cell_warnings(book, inp, cfg, scope)
    if cells and any(cells["expected"].values()):
        out["cell_warnings"] = cells
    out.update({"values": e.values, "unavailable": e.unavailable})
    out.update(_extra_runs_expected(case, cfg))
    return out


def _month_range(first: str, last: str) -> list[str]:
    out, key = [], first
    while key <= last:
        out.append(key)
        y, m = int(key[:4]), int(key[5:])
        key = f"{y + (m == 12):04d}-{m % 12 + 1:02d}"
    return out


def _extreme_fields(fig: dict, months: list[str]) -> list[dict]:
    """The highest and lowest month, ties to the earliest, as doc paths to check."""
    fields = []
    for name in ("revenue.total", "operating_income"):
        have = [(p, fig[p][name]) for p in months if fig[p][name] is not None]
        hi = sorted(have, key=lambda c: (-c[1], c[0]))[0]
        lo = sorted(have, key=lambda c: (c[1], c[0]))[0]
        fields += [{"path": ["extremes", name, "highest", "period"], "expected": hi[0]},
                   {"path": ["extremes", name, "highest", "value"], "expected": hi[1]},
                   {"path": ["extremes", name, "lowest", "period"], "expected": lo[0]},
                   {"path": ["extremes", name, "lowest", "value"], "expected": lo[1]}]
    return fields


def _cross_check(e: Expect, want: dict) -> None:
    """Refuse to write expected values that disagree with the case's stated figures."""
    got = {v["metric"]: v["expected"] for v in e.values}
    for metric, value in want.items():
        if abs(got.get(metric, float("nan")) - value) > CURRENCY_TOL:
            raise SystemExit(f"cross-check failed: {metric} = {got.get(metric)}, "
                             f"case.yaml says {value}")


def _other_books(case: dict, cfg: dict) -> dict:
    """Other workbooks in the case's client folder: name -> months. Only for notes."""
    out = {}
    for rel in case["input"].get("other_workbooks") or []:
        out[Path(rel).name] = Book(ROOT / rel, cfg).months
    return out


def _month_values(e: Expect, fig: dict, months: list[str], period: str,
                  others: dict) -> None:
    """One month's headline figures and their month-over-month changes."""
    cur, prev_key = fig[period], prior_of(period)
    for name in HEADLINE5 + ["gross_margin_pct", "operating_margin_pct"]:
        e.value(name, cur[name], f"{name} for {period}",
                "percent" if name.endswith("_pct") else "currency")
    for name in [k for k in cur if k.startswith("revenue.stream.")]:
        e.value(name, cur[name], f"{name} for {period}")
    if prev_key in months:
        for name in HEADLINE5:
            e.change(name, cur[name], fig[prev_key][name], name, prev_key)
    else:
        elsewhere = [n for n, ms in others.items() if prev_key in ms]
        reason = "in_other_file" if elsewhere else "missing_input"
        for name in HEADLINE5:
            for suffix in ("mom_abs", "mom_pct", "prior"):
                e.na(f"{name}.{suffix}", reason,
                     f"{prev_key} is " + (f"only in {elsewhere}" if elsewhere
                                          else "not in the source"))


def _extra_runs_expected(case: dict, cfg: dict) -> dict:
    """Expected values for a case's extra CLI runs that read a named workbook."""
    runs = {}
    for run in (case.get("engine") or {}).get("extra_runs", []):
        if not run.get("expect_from"):
            continue
        book = Book(ROOT / run["expect_from"], cfg)
        fig = {p: book.month(p) for p in book.months}
        e = Expect()
        others = {Path(rel).name: Book(ROOT / rel, cfg).months
                  for rel in [case["input"]["workbook"],
                              *(case["input"].get("other_workbooks") or [])]
                  if Path(rel).name != Path(run["expect_from"]).name}
        _month_values(e, fig, book.months, run["period"], others)
        runs[run["id"]] = {"workbook": run["expect_from"], "values": e.values,
                           "unavailable": e.unavailable}
    out = {}
    if runs:
        out["extra_runs"] = runs
    others = case["input"].get("other_workbooks") or []
    if others:
        out["other_inputs_content_sha256"] = {rel: workbook_fingerprint(ROOT / rel)
                                              for rel in others}
    return out


def _span_cell_warnings(book: Book, inp: dict, cfg: dict, scope: str) -> dict | None:
    """Cell-level warnings over the months a year or whole-file run covers."""
    if scope == "month":
        return None
    enabled = _enabled_cell_checks(inp["client_id"])
    keys = book.months
    span = [p for p in keys if scope == "all" or p.startswith(f"{inp['year']}-")]
    expected_negative = {str(x).casefold() for x in cfg.get("expected_negative_lines") or []}
    sheet = cfg["sheets"]["income_statement"]["name"]
    found: dict[str, list[dict]] = {code: [] for code, on in enabled.items() if on}

    def entry(n, lbl, key, raw):
        return {"sheet": sheet,
                "cell": f"{openpyxl.utils.get_column_letter(book.cols[key] + 1)}{n}",
                "label": lbl, "period": key, "raw_value": raw}

    for section in ("revenue", "cogs", "opex"):
        for n, lbl, cells in book.section_items(section):
            for i, key in enumerate(keys):
                if key not in span:
                    continue
                v = cells.get(key)
                if "non_numeric_cell" in found and isinstance(v, str) and v.strip():
                    found["non_numeric_cell"].append(entry(n, lbl, key, v.strip()))
                if ("blank_cell" in found and v is None and 0 < i < len(keys) - 1
                        and _num(cells.get(keys[i - 1])) is not None
                        and _num(cells.get(keys[i + 1])) is not None):
                    found["blank_cell"].append(entry(n, lbl, key, None))
                if ("negative_line_item" in found and _num(v) is not None and _num(v) < 0
                        and lbl.casefold() not in expected_negative):
                    found["negative_line_item"].append(entry(n, lbl, key, _num(v)))
    return {"derivation": ("workbook cells read with openpyxl over the months the scope "
                           "covers; the config's data_quality.cell_checks over the "
                           "documented defaults"),
            "enabled": enabled, "expected": found}


# ---- driver -------------------------------------------------------------

def build(case_dir: Path) -> dict | None:
    case = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
    inp = case["input"]
    if "workbook" not in inp:
        return None                          # case 12: CLI behaviour only
    path = ROOT / inp["workbook"]
    if not path.exists():
        print(f"  {case['id']}: SKIPPED, workbook not present at {path}")
        return None
    generated = case.get("builder") == "test_workbook"
    if case.get("builder") == "test_workbook":
        body = test_workbook_expected(case)
    elif case.get("synthetic"):
        body = synthetic_expected(inp["client_id"], inp["period"])
    else:
        body = abc_expected(case, subset=case["id"].startswith("13_"))
    out = {
        "case_id": case["id"],
        "client_id": inp["client_id"],
        **({"period": inp["period"]} if "period" in inp else {}),
        **({"scope": inp["scope"]} if "scope" in inp else {}),
        **({"year": inp["year"]} if "year" in inp else {}),
        "generated_by": "evals/tools/build_expected.py",
        "independent_of_engine": True,
        # A generated workbook's bytes carry a creation time; its cells do not.
        **({"input_content_sha256": workbook_fingerprint(path)} if generated
           else {"input_sha256": sha256(path)}),
        **body,
    }
    (case_dir / "expected.json").write_text(json.dumps(out, indent=2) + "\n",
                                            encoding="utf-8")
    print(f"  {case['id']}: {len(out['values'])} values, "
          f"{len(out['unavailable'])} expected n/a")
    return out


def main(argv: list[str]) -> int:
    ensure_generated_workbooks()
    dirs = sorted(p for p in CASES.iterdir() if (p / "case.yaml").exists())
    if argv:
        dirs = [d for d in dirs if any(d.name.startswith(a) for a in argv)]
    for d in dirs:
        build(d)
    if "finance_engine" in sys.modules:
        print("ERROR: finance_engine was imported; expected values are not independent")
        return 1
    print("finance_engine was not imported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
