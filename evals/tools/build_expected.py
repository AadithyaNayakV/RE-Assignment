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
    if case.get("synthetic"):
        body = synthetic_expected(inp["client_id"], inp["period"])
    else:
        body = abc_expected(case, subset=case["id"].startswith("13_"))
    out = {
        "case_id": case["id"],
        "client_id": inp["client_id"],
        "period": inp["period"],
        "generated_by": "evals/tools/build_expected.py",
        "independent_of_engine": True,
        "input_sha256": sha256(path),
        **body,
    }
    (case_dir / "expected.json").write_text(json.dumps(out, indent=2) + "\n",
                                            encoding="utf-8")
    print(f"  {case['id']}: {len(out['values'])} values, "
          f"{len(out['unavailable'])} expected n/a")
    return out


def main(argv: list[str]) -> int:
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
