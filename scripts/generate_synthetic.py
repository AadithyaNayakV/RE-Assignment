"""Generates ten SYNTHETIC test workbooks, their configs, and their expected values.

    python scripts/generate_synthetic.py

Every file produced here is fictional. The company names are labelled SYNTHETIC in the
workbook headings and in the config, because a figure from one of these must never be
mistaken for a figure about a real client. **No result from a synthetic workbook is
evidence about ABC Inc.**

Three things are written per scenario:

* `data/synthetic/<id>.xlsx` — the workbook, with formulas and cached results.
* `config/clients/<id>.yaml` — its mapping config, so the CLI can run it unchanged.
* `data/synthetic/expected/<id>.json` — expected values computed **here**, straight from
  the generator's own inputs, without importing anything from `finance_engine`. That
  independence is the point: `tests/test_synthetic.py` compares the engine against these
  files, so agreement means two separate implementations concur.

Layouts are deliberately varied. Scenarios A-E and I-J follow the ABC shape
(newest-first, labels in A/B, data from C). F and G are oldest-first. H puts labels in
C/D with data from F and a different header row. A config change is all that differs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xlsx_builder import SyntheticWorkbook  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK_DIR = ROOT / "data" / "synthetic"
EXPECTED_DIR = WORKBOOK_DIR / "expected"
CONFIG_DIR = ROOT / "config" / "clients"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
          "Dec"]
N = 18


# ---- period helpers ----------------------------------------------------

def periods(start_year: int = 2025, start_month: int = 1, count: int = N) -> list[str]:
    out = []
    year, month = start_year, start_month
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def label_of(period: str, style: str = "%b %Y") -> str:
    year, month = int(period[:4]), int(period[5:])
    if style == "%b %Y":
        return f"{MONTHS[month - 1]} {year}"
    if style == "%b-%y":
        return f"{MONTHS[month - 1]}-{year % 100:02d}"
    raise ValueError(style)


def ramp(start: float, step: float, count: int = N) -> list[float]:
    return [round(start + step * i, 2) for i in range(count)]


def flat(value: float, count: int = N) -> list[float]:
    return [round(float(value), 2)] * count


def seasonal(base: float, amplitude: float, peaks: tuple[int, ...],
             period_keys: list[str]) -> list[float]:
    """A repeatable seasonal shape: named calendar months are lifted."""
    out = []
    for index, key in enumerate(period_keys):
        month = int(key[5:])
        lift = amplitude if month in peaks else 0.0
        drift = base * 0.004 * index
        out.append(round(base + lift + drift, 2))
    return out


def spike(values: list[float], index: int, amount: float) -> list[float]:
    out = list(values)
    out[index] = round(out[index] + amount, 2)
    return out


# ---- scenario definitions ----------------------------------------------

def scenarios() -> list[dict]:
    p = periods()
    last = p[-1]

    def base(cid, name, note, **over):
        spec = {
            "client_id": cid,
            "company_name": f"{name} (SYNTHETIC)",
            "scenario": note,
            "periods": p,
            "reporting_period": last,
            "sheet_name": "Income Statement",
            "header_row": 5,
            "section_col": "A",
            "item_col": "B",
            "first_data_col": "C",
            "date_order": "newest_first",
            "date_style": "%b %Y",
            "groups": [],
            "balance_sheet": None,
        }
        spec.update(over)
        return spec

    out: list[dict] = []

    # 1. normal growth, stable expenses
    out.append(base(
        "company_a_normal_growth", "Company A", "steady revenue growth, stable costs",
        revenue={"Subscription Revenue": ramp(120000, 2500),
                 "Services Revenue": ramp(18000, 300)},
        cogs={"Hosting": ramp(9000, 120), "Support Payroll": flat(22000)},
        opex_direct={"Rent": flat(7500), "Software & SaaS": ramp(6000, 80),
                     "Accounting & Tax": flat(3200), "Travel": flat(1800)},
        groups=[{"name": "Sales Payroll", "items": {
            "Sales - Payroll": ramp(48000, 400),
            "Sales - Payroll Taxes": ramp(3800, 32)}}],
    ))

    # 2. revenue falling over several months
    decline = ramp(185000, 1200)[:6] + [round(192200 - 7800 * i, 2) for i in range(12)]
    out.append(base(
        "company_b_revenue_decline", "Company B", "revenue declining for twelve months",
        revenue={"Licence Revenue": decline,
                 "Maintenance Revenue": ramp(26000, -150)},
        cogs={"Delivery Payroll": flat(31000), "Third-Party Licences": ramp(12000, -60)},
        opex_direct={"Rent": flat(9000), "Marketing/Advertising": ramp(14000, -200),
                     "General Expenses": flat(2400)},
        groups=[{"name": "Engineering Payroll", "items": {
            "Engineering - Payroll": flat(76000),
            "Engineering - Payroll Taxes": flat(6100)}}],
    ))

    # 3. one expense category jumps and stays high
    legal = flat(2000)
    legal = [v if i < 12 else round(v + 34000, 2) for i, v in enumerate(legal)]
    out.append(base(
        "company_c_expense_spike", "Company C", "legal costs jump in month 13 and persist",
        revenue={"Platform Revenue": ramp(150000, 900),
                 "Usage Fees": ramp(60000, 700)},
        cogs={"Hosting": ramp(14000, 150), "Payment Processing": ramp(4200, 40)},
        opex_direct={"Legal Expenses": legal, "Rent": flat(8200),
                     "Software & SaaS": flat(7400), "Insurance": flat(2600)},
        groups=[{"name": "Operations Payroll", "items": {
            "Operations - Payroll": flat(58000),
            "Operations - Payroll Taxes": flat(4650)}}],
    ))

    # 4. margins shrink: COGS grows faster than revenue
    out.append(base(
        "company_d_margin_decline", "Company D", "COGS outgrows revenue, margin shrinks",
        revenue={"Product Revenue": ramp(200000, 1500)},
        cogs={"Manufacturing": ramp(80000, 4200), "Freight": ramp(12000, 650)},
        opex_direct={"Rent": flat(11000), "Marketing/Advertising": flat(16000),
                     "Accounting & Tax": flat(4000)},
        groups=[{"name": "Admin Payroll", "items": {
            "Admin - Payroll": flat(42000),
            "Admin - Payroll Taxes": flat(3360)}}],
    ))

    # 5. seasonal revenue, peaking in Nov and Dec
    out.append(base(
        "company_e_seasonal_business", "Company E", "pronounced Nov/Dec seasonal peak",
        revenue={"Retail Revenue": seasonal(90000, 145000, (11, 12), p),
                 "Wholesale Revenue": seasonal(40000, 38000, (10, 11), p)},
        cogs={"Cost of Goods": seasonal(48000, 72000, (11, 12), p),
              "Fulfilment": seasonal(9000, 14000, (11, 12), p)},
        opex_direct={"Rent": flat(13000),
                     "Marketing/Advertising": seasonal(12000, 26000, (10, 11), p),
                     "Software & SaaS": flat(4100)},
        groups=[{"name": "Store Payroll", "items": {
            "Store - Payroll": seasonal(52000, 24000, (11, 12), p),
            "Store - Payroll Taxes": seasonal(4160, 1920, (11, 12), p)}}],
    ))

    # 6. a single large one-off expense
    out.append(base(
        "company_f_one_time_expense", "Company F",
        "one large settlement in a single month; oldest-first layout",
        date_order="oldest_first",
        revenue={"Consulting Revenue": ramp(140000, 800),
                 "Retainer Revenue": flat(45000)},
        cogs={"Contractor Costs": ramp(38000, 300)},
        opex_direct={"Legal Settlement": spike(flat(0.0), 10, 285000.0),
                     "Rent": flat(6800), "Software & SaaS": flat(5200),
                     "Travel": ramp(2200, 45)},
        groups=[{"name": "Delivery Payroll", "items": {
            "Delivery - Payroll": flat(64000),
            "Delivery - Payroll Taxes": flat(5120)}}],
    ))

    # 7. missing months and blank cells
    missing_periods = [k for k in p if k not in ("2025-04", "2025-09")]
    revenue_g = ramp(110000, 1100, len(missing_periods))
    services_g = list(ramp(21000, 180, len(missing_periods)))
    services_g[7] = None                       # a genuinely blank month, not a zero
    out.append(base(
        "company_g_missing_data", "Company G",
        "two months absent from the workbook and one blank cell; oldest-first layout",
        periods=missing_periods,
        reporting_period=missing_periods[-1],
        date_order="oldest_first",
        revenue={"Subscription Revenue": revenue_g, "Services Revenue": services_g},
        cogs={"Hosting": ramp(8000, 90, len(missing_periods))},
        opex_direct={"Rent": flat(7000, len(missing_periods)),
                     "Software & SaaS": flat(3900, len(missing_periods))},
        groups=[{"name": "Support Payroll", "items": {
            "Support - Payroll": flat(36000, len(missing_periods)),
            "Support - Payroll Taxes": flat(2880, len(missing_periods))}}],
    ))

    # 8. zeros, negatives, and awkward percentage bases
    zero_then_value = [0.0] * 9 + ramp(5000, 450, 9)
    value_then_zero = ramp(9000, -600, 9) + [0.0] * 9
    refunds = [0.0, -1200.0, 0.0, -3400.0, 0.0, 0.0, -890.0, 0.0, 0.0,
               -5200.0, 0.0, 0.0, -760.0, 0.0, -2300.0, 0.0, 0.0, -1450.0]
    bad_debts = [1500.0, -1500.0, 0.0, 4200.0, -820.0, 0.0, 2600.0, 0.0, -310.0,
                 0.0, 1800.0, -950.0, 0.0, 3100.0, 0.0, -420.0, 0.0, 2200.0]
    out.append(base(
        "company_h_zero_negative_values", "Company H",
        "zeros, negative lines and zero-base percentage changes; labels in C/D, data from F",
        sheet_name="Profit and Loss",
        header_row=7,
        section_col="C",
        item_col="D",
        first_data_col="F",
        date_style="%b-%y",
        revenue={"Core Revenue": ramp(95000, 700),
                 "New Product Revenue": zero_then_value,
                 "Legacy Revenue": value_then_zero,
                 "Refunds and Credits": refunds},
        cogs={"Hosting": ramp(7200, 60), "Support Payroll": flat(18000)},
        opex_direct={"Bad Debts": bad_debts, "Rent": flat(5400),
                     "Marketing/Advertising": zero_then_value,
                     "Interest Income": flat(-650.0)},
        groups=[{"name": "Product Payroll", "items": {
            "Product - Payroll": flat(44000),
            "Product - Payroll Taxes": flat(3520)}}],
    ))

    # 9. many revenue streams and expense categories
    out.append(base(
        "company_i_multiple_revenue_streams", "Company I",
        "six revenue streams and two subtotal groups",
        revenue={"Platform Revenue": ramp(88000, 600),
                 "Usage Fees": ramp(64000, 850),
                 "Professional Services": ramp(23000, -120),
                 "Partnership Income": seasonal(7000, 4000, (3, 6, 9, 12), p),
                 "White-Label": flat(5600),
                 "Training Revenue": ramp(4000, 150)},
        cogs={"Hosting": ramp(11000, 130), "Referral Fees": ramp(3100, 55),
              "Support Payroll": flat(26000), "Payment Processing": ramp(2900, 40)},
        opex_direct={"Rent": flat(9400), "Accounting & Tax": flat(5100),
                     "Legal Expenses": flat(1900), "Insurance": flat(3300),
                     "Travel": ramp(3000, 70), "Software & SaaS": ramp(8800, 110),
                     "Depreciation": flat(1250), "Bank Service Charges": flat(310)},
        groups=[
            {"name": "S & M Payroll", "items": {
                "Sales - Payroll": ramp(51000, 350),
                "Sales - Payroll Taxes": ramp(4080, 28),
                "Marketing - Payroll": flat(28000),
                "Marketing - Payroll Taxes": flat(2240)}},
            {"name": "Marketing", "items": {
                "Marketing/Advertising: Ad Spend": ramp(14000, 260),
                "Marketing/Advertising: Content": flat(5000),
                "Events & Conferences": spike(flat(1200), 14, 41000.0)}},
        ],
    ))

    # 10. income statement plus a balance sheet
    out.append(base(
        "company_j_balance_sheet", "Company J",
        "income statement plus a balance sheet that balances",
        revenue={"Subscription Revenue": ramp(165000, 1400),
                 "Services Revenue": ramp(32000, 260)},
        cogs={"Hosting": ramp(13000, 140), "Support Payroll": flat(28000)},
        opex_direct={"Rent": flat(10500), "Software & SaaS": ramp(7100, 95),
                     "Accounting & Tax": flat(4300), "Insurance": flat(2900)},
        groups=[{"name": "Engineering Payroll", "items": {
            "Engineering - Payroll": ramp(82000, 500),
            "Engineering - Payroll Taxes": ramp(6560, 40)}}],
        balance_sheet={
            "sheet_name": "Balance Sheet",
            "assets": {
                "Cash": {"Checking Account": ramp(240000, 5200),
                         "Savings Account": flat(150000)},
                "Receivables": {"Accounts Receivable": ramp(310000, 2400),
                                "Prepaid Expense": flat(46000)},
            },
            "liabilities": {"Accounts Payable": ramp(72000, 900),
                            "Deferred Revenue": ramp(286000, 3100),
                            "Accrued Expenses": flat(38000)},
        },
    ))
    return out


# ---- independent expected-value computation ----------------------------

def compute_expected(spec: dict) -> dict:
    """Totals and comparisons worked out from the generator's own inputs.

    Deliberately written without importing `finance_engine`, so the engine and this
    function are two independent implementations of the same arithmetic.
    """
    keys = spec["periods"]
    revenue, cogs = spec["revenue"], spec["cogs"]
    opex_lines = dict(spec["opex_direct"])
    for group in spec["groups"]:
        opex_lines.update(group["items"])

    def total(block: dict[str, list], index: int) -> float | None:
        present = [values[index] for values in block.values()
                   if values[index] is not None]
        return round(sum(present), 2) if present else None

    per_period = {}
    for index, key in enumerate(keys):
        rev = total(revenue, index)
        cost = total(cogs, index)
        opex = total(opex_lines, index)
        gross = None if rev is None or cost is None else round(rev - cost, 2)
        operating = None if gross is None or opex is None else round(gross - opex, 2)
        per_period[key] = {
            "total_revenue": rev,
            "total_cogs": cost,
            "gross_profit": gross,
            "total_opex": opex,
            "operating_income": operating,
            "gross_margin_pct": (None if not rev or rev <= 0 or gross is None
                                 else round(gross / rev * 100.0, 6)),
            "revenue_by_stream": {name: values[index]
                                  for name, values in revenue.items()},
            "opex_by_line": {name: values[index]
                             for name, values in opex_lines.items()},
            "group_subtotals": {
                group["name"]: (
                    round(sum(v[index] for v in group["items"].values()
                              if v[index] is not None), 2)
                    if any(v[index] is not None for v in group["items"].values())
                    else None
                )
                for group in spec["groups"]
            },
        }

    reporting = spec["reporting_period"]
    index = keys.index(reporting)
    prior_key = _prior(reporting)
    prior = per_period.get(prior_key)

    def mom(field: str) -> dict:
        current = per_period[reporting][field]
        previous = prior[field] if prior else None
        if current is None or previous is None:
            return {"abs": None, "pct": None, "reason": "missing_input"}
        change = round(current - previous, 2)
        if previous == 0:
            return {"abs": change, "pct": None, "reason": "zero_base"}
        if previous < 0:
            return {"abs": change, "pct": None, "reason": "negative_base"}
        return {"abs": change, "pct": round(change / previous * 100.0, 6),
                "reason": None}

    window = _trailing(reporting, 12)
    complete = all(k in keys for k in window)
    ttm = {}
    if complete:
        for field in ("total_revenue", "total_cogs", "total_opex"):
            values = [per_period[k][field] for k in window]
            ttm[field] = (None if any(v is None for v in values)
                          else round(sum(values), 2))

    return {
        "note": ("SYNTHETIC test data. Expected values computed by "
                 "scripts/generate_synthetic.py without using finance_engine. "
                 "Never evidence about a real client."),
        "client_id": spec["client_id"],
        "company_name": spec["company_name"],
        "scenario": spec["scenario"],
        "periods": keys,
        "period_count": len(keys),
        "reporting_period": reporting,
        "prior_period": prior_key,
        "layout": {
            "sheet_name": spec["sheet_name"],
            "header_row": spec["header_row"],
            "section_label_column": spec["section_col"],
            "item_label_column": spec["item_col"],
            "first_data_column": spec["first_data_col"],
            "date_order": spec["date_order"],
        },
        "ttm": {"window": window, "complete": complete, "totals": ttm},
        "month_over_month": {
            "total_revenue": mom("total_revenue"),
            "total_opex": mom("total_opex"),
            "gross_profit": mom("gross_profit"),
        },
        "per_period": per_period,
        "has_balance_sheet": spec["balance_sheet"] is not None,
    }


def _prior(period: str) -> str:
    year, month = int(period[:4]), int(period[5:])
    month -= 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def _trailing(period: str, months: int) -> list[str]:
    out, key = [], period
    for _ in range(months):
        out.append(key)
        key = _prior(key)
    return list(reversed(out))


# ---- writing -----------------------------------------------------------

def write_workbook(spec: dict, expected: dict, path: Path) -> None:
    keys = spec["periods"]
    order = list(reversed(keys)) if spec["date_order"] == "newest_first" else keys
    index_of = {key: keys.index(key) for key in keys}

    def reorder(values: list) -> list:
        return [values[index_of[key]] for key in order]

    wb = SyntheticWorkbook()
    sheet = wb.sheet(
        spec["sheet_name"],
        header_row=spec["header_row"],
        first_data_column=spec["first_data_col"],
        periods=order,
        date_labels=[label_of(k, spec["date_style"]) for k in order],
        titles=[f"{spec['sheet_name']} — SYNTHETIC TEST DATA",
                spec["company_name"],
                f"Scenario: {spec['scenario']}",
                f"For the month ended {label_of(spec['reporting_period'])}"],
        section_label_column=spec["section_col"],
        item_label_column=spec["item_col"],
    )

    sheet.header("Income")
    for name, values in spec["revenue"].items():
        sheet.item(name, reorder(values))
    first = sheet.rows[next(iter(spec["revenue"]))].row
    last = sheet.rows[list(spec["revenue"])[-1]].row
    sheet.formula("Total Income", f"SUM({{c}}{first}:{{c}}{last})",
                  reorder([expected["per_period"][k]["total_revenue"] for k in keys]))
    income_row = sheet.rows["Total Income"].row
    sheet.skip()

    sheet.header("Cost of Goods Sold")
    for name, values in spec["cogs"].items():
        sheet.item(name, reorder(values))
    first = sheet.rows[next(iter(spec["cogs"]))].row
    last = sheet.rows[list(spec["cogs"])[-1]].row
    sheet.formula("Total Cost of Goods Sold", f"SUM({{c}}{first}:{{c}}{last})",
                  reorder([expected["per_period"][k]["total_cogs"] for k in keys]))
    cogs_row = sheet.rows["Total Cost of Goods Sold"].row
    sheet.skip()

    sheet.formula("Gross Profit", f"({{c}}{income_row} - {{c}}{cogs_row})",
                  reorder([expected["per_period"][k]["gross_profit"] for k in keys]),
                  in_section_column=False)
    gross_row = sheet.rows["Gross Profit"].row
    sheet.skip()

    sheet.header("Operating Expenses")
    direct_ranges: list[str] = []
    group_rows: list[int] = []
    run_start = None
    for name, values in spec["opex_direct"].items():
        row = sheet.item(name, reorder(values)).row
        if run_start is None:
            run_start = row
        run_end = row
    if run_start is not None:
        direct_ranges.append(f"{{c}}{run_start}:{{c}}{run_end}")

    for group in spec["groups"]:
        sheet.group_header(group["name"])
        member_rows = [sheet.item(name, reorder(values)).row
                       for name, values in group["items"].items()]
        subtotal = reorder([expected["per_period"][k]["group_subtotals"][group["name"]]
                            for k in keys])
        sheet.formula(f"Total {group['name']}",
                      f"SUM({{c}}{member_rows[0]}:{{c}}{member_rows[-1]})",
                      subtotal, in_section_column=False)
        group_rows.append(sheet.rows[f"Total {group['name']}"].row)

    parts = ",".join(direct_ranges)
    addends = "".join(f" + {{c}}{row}" for row in group_rows)
    sheet.formula("Total Operating Expenses", f"(SUM({parts}){addends})",
                  reorder([expected["per_period"][k]["total_opex"] for k in keys]))
    opex_row = sheet.rows["Total Operating Expenses"].row
    sheet.skip()

    sheet.formula("Operating Income", f"({{c}}{gross_row} - {{c}}{opex_row})",
                  reorder([expected["per_period"][k]["operating_income"] for k in keys]),
                  in_section_column=False)
    operating_row = sheet.rows["Operating Income"].row
    sheet.skip()
    sheet.formula("Net Income", f"({{c}}{operating_row} + 0)",
                  reorder([expected["per_period"][k]["operating_income"] for k in keys]),
                  in_section_column=False)

    if spec["balance_sheet"]:
        _write_balance_sheet(wb, spec, keys, order, reorder, expected)

    wb.save(path)


def _write_balance_sheet(wb, spec, keys, order, reorder, expected) -> None:
    bs = spec["balance_sheet"]
    sheet = wb.sheet(
        bs["sheet_name"],
        header_row=spec["header_row"],
        first_data_column="D",
        periods=order,
        date_labels=[label_of(k) for k in order],
        titles=[f"{bs['sheet_name']} — SYNTHETIC TEST DATA", spec["company_name"]],
        section_label_column="A",
        item_label_column="C",
    )

    asset_group_rows: list[int] = []
    sheet.header("Assets")
    for group_name, lines in bs["assets"].items():
        rows = [sheet.item(name, reorder(values)).row for name, values in lines.items()]
        totals = [round(sum(values[keys.index(k)] for values in lines.values()), 2)
                  for k in keys]
        sheet.formula(f"Total {group_name}", f"SUM({{c}}{rows[0]}:{{c}}{rows[-1]})",
                      reorder(totals), in_section_column=False)
        asset_group_rows.append(sheet.rows[f"Total {group_name}"].row)

    asset_totals = [
        round(sum(sum(v[keys.index(k)] for v in lines.values())
                  for lines in bs["assets"].values()), 2)
        for k in keys
    ]
    sheet.formula("Total Assets",
                  "(" + " + ".join(f"{{c}}{r}" for r in asset_group_rows) + ")",
                  reorder(asset_totals))
    sheet.skip()

    sheet.header("Liabilities and Equity")
    liability_rows = [sheet.item(name, reorder(values)).row
                      for name, values in bs["liabilities"].items()]
    liability_totals = [
        round(sum(v[keys.index(k)] for v in bs["liabilities"].values()), 2)
        for k in keys
    ]
    sheet.formula("Total Liabilities",
                  f"SUM({{c}}{liability_rows[0]}:{{c}}{liability_rows[-1]})",
                  reorder(liability_totals), in_section_column=False)
    liabilities_row = sheet.rows["Total Liabilities"].row

    # Equity is the plug that makes the sheet balance, which is what a real balance sheet
    # does via retained earnings.
    equity_totals = [round(a - l, 2) for a, l in zip(asset_totals, liability_totals)]
    sheet.item("Retained Earnings", reorder(equity_totals))
    equity_row = sheet.rows["Retained Earnings"].row
    sheet.formula("Total Equity", f"({{c}}{equity_row} + 0)", reorder(equity_totals),
                  in_section_column=False)
    equity_total_row = sheet.rows["Total Equity"].row

    sheet.formula("Total Liabilities and Equity",
                  f"({{c}}{liabilities_row} + {{c}}{equity_total_row})",
                  reorder(asset_totals))


def write_config(spec: dict, path: Path) -> None:
    sheets = {
        "income_statement": {
            "name": spec["sheet_name"],
            "header_row": spec["header_row"],
            "label_columns": [spec["section_col"], spec["item_col"]],
            "first_data_column": spec["first_data_col"],
            "date_order": spec["date_order"],
            "date_formats": [spec["date_style"]],
        }
    }
    if spec["balance_sheet"]:
        sheets["balance_sheet"] = {
            "name": spec["balance_sheet"]["sheet_name"],
            "header_row": spec["header_row"],
            "label_columns": ["A", "C"],
            "first_data_column": "D",
            "date_order": spec["date_order"],
            "date_formats": ["%b %Y"],
        }

    config = {
        "client_id": spec["client_id"],
        "company_name": spec["company_name"],
        "currency": "USD",
        "currency_symbol": "$",
        "units_scale": 1,
        "synthetic": True,
        "scenario": spec["scenario"],
        "source": {"type": "excel", "filename": f"{spec['client_id']}.xlsx",
                   "directory": "data/synthetic"},
        "sheets": sheets,
        "income_statement": {
            "sections": [
                {"key": "revenue", "header_label": "Income",
                 "total_label": "Total Income"},
                {"key": "cogs", "header_label": "Cost of Goods Sold",
                 "total_label": "Total Cost of Goods Sold"},
                {"key": "opex", "header_label": "Operating Expenses",
                 "total_label": "Total Operating Expenses",
                 **({"subtotal_groups": [
                     {"key": _slug(g["name"]), "name": g["name"],
                      "header_label": g["name"],
                      "total_label": f"Total {g['name']}"}
                     for g in spec["groups"]]} if spec["groups"] else {})},
            ],
            "reported_lines": {
                "gross_profit": "Gross Profit",
                "operating_income": "Operating Income",
                "net_income": "Net Income",
            },
        },
        "opex_categories": [
            {"name": "Payroll & People Costs",
             "match_contains": ["payroll", "bonus", "benefits", "workers comp"]},
            {"name": "Marketing & Advertising",
             "match_contains": ["marketing/advertising", "events & conferences"]},
            {"name": "Contractors", "match_contains": ["contractor"]},
            {"name": "Software, Hosting & AI",
             "match_contains": ["software", "saas", "hosting"]},
            {"name": "Professional & Legal Fees",
             "match_contains": ["accounting", "legal", "consulting", "settlement"]},
            {"name": "Occupancy & Insurance", "match_contains": ["rent", "insurance"]},
            {"name": "Travel, Meals & Office",
             "match_contains": ["travel", "meals", "office", "general expenses"]},
            {"name": "Finance, Tax & Non-Cash",
             "match_contains": ["interest", "depreciation", "bad debts", "tax",
                                "bank service"]},
        ],
        "fallback_opex_category": "Other Operating Expenses",
        "reconciliation": {
            "tolerance_abs": 0.01,
            "checks": [
                {"key": "revenue_items_vs_total",
                 "description": "Sum of revenue line items equals Total Income"},
                {"key": "cogs_items_vs_total",
                 "description": "Sum of COGS line items equals Total Cost of Goods Sold"},
                {"key": "opex_items_vs_total",
                 "description": "Direct opex plus group subtotals equal Total Operating "
                                "Expenses"},
                {"key": "opex_group_subtotals",
                 "description": "Each subtotal group's members equal its subtotal row"},
                {"key": "gross_profit",
                 "description": "Total Income minus Total COGS equals Gross Profit"},
                {"key": "operating_income",
                 "description": "Gross Profit minus Total Operating Expenses equals "
                                "Operating Income"},
                {"key": "balance_sheet_balances",
                 "description": "Total Assets equals Total Liabilities and Equity"},
            ],
        },
        "classification_notes": [
            f"SYNTHETIC test company ({spec['scenario']}). Generated by "
            f"scripts/generate_synthetic.py. No figure here describes a real business.",
        ],
        "thresholds": {
            "significant_mom_pct": 25.0,
            "significant_mom_abs": 10000.0,
            "significant_mom_min_abs": 1000.0,
            "oneoff_multiple": 3.0,
            "oneoff_min_abs": 5000.0,
            "oneoff_trailing_months": 6,
            "oneoff_window_min_abs": 25000.0,
            "top_movements": 5,
        },
        "ttm": {"months": 12},
    }
    if spec["balance_sheet"]:
        config["balance_sheet"] = {
            "reported_totals": {
                "assets": "Total Assets",
                "liabilities_and_equity": "Total Liabilities and Equity",
                "liabilities": "Total Liabilities",
                "equity": "Total Equity",
            },
            "trend_lines": [
                {"key": "cash", "label": "Total Cash"},
                {"key": "accounts_receivable", "label": "Accounts Receivable"},
                {"key": "deferred_revenue", "label": "Deferred Revenue"},
            ],
        }
    path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def main() -> int:
    WORKBOOK_DIR.mkdir(parents=True, exist_ok=True)
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    built = []
    for spec in scenarios():
        cid = spec["client_id"]
        expected = compute_expected(spec)
        workbook_path = WORKBOOK_DIR / f"{cid}.xlsx"
        write_workbook(spec, expected, workbook_path)
        write_config(spec, CONFIG_DIR / f"{cid}.yaml")
        (EXPECTED_DIR / f"{cid}.json").write_text(
            json.dumps(expected, indent=2), encoding="utf-8"
        )
        built.append((cid, spec["scenario"], len(spec["periods"]),
                      spec["date_order"], expected["ttm"]["complete"]))
        print(f"  {cid:38s} {len(spec['periods']):2d} months  "
              f"{spec['date_order']:13s} ttm={'yes' if expected['ttm']['complete'] else 'NO'}")

    print(f"\n{len(built)} synthetic workbooks written to {WORKBOOK_DIR}")
    print(f"configs written to {CONFIG_DIR}")
    print(f"expected values written to {EXPECTED_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
