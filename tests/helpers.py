"""Builders for tiny workbooks whose correct answers are worked out by hand.

The numbers in `SMALL_*` below were chosen so every expected result can be computed on
paper — see `tests/test_metrics_handcalc.py`, where the expectations are written as
literals with their arithmetic shown. Nothing here imports the engine's calculation code,
so a test built on these fixtures cannot pass just because the engine agrees with itself.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from openpyxl import Workbook

# Three months of a deliberately small company.
#   Periods (oldest first):      2025-01  2025-02  2025-03
SMALL_WIDGETS = [100.0, 200.0, 0.0]
SMALL_SERVICES = [50.0, 0.0, 25.0]
SMALL_MATERIALS = [30.0, 40.0, 5.0]
SMALL_RENT = [10.0, 10.0, 10.0]
SMALL_SALARY_A = [20.0, 20.0, 20.0]
SMALL_SALARY_B = [5.0, 5.0, 0.0]
SMALL_MARKETING = [0.0, 50.0, 100.0]
SMALL_PERIODS = ["2025-01", "2025-02", "2025-03"]
SMALL_LABELS = ["Jan 2025", "Feb 2025", "Mar 2025"]


def build_small_workbook(path: Path, *, newest_first: bool = False,
                         blank_cells: list[tuple[str, int]] | None = None,
                         drop_period_index: int | None = None,
                         duplicate_period: bool = False) -> Path:
    """Write a minimal income statement with the same shape as a real one.

    Args:
        newest_first: reverse the month columns, to prove the config drives orientation.
        blank_cells: (label, period_index) pairs to leave genuinely empty, for
            blank-versus-zero tests.
        drop_period_index: omit one month entirely, creating a calendar gap.
        duplicate_period: repeat a month header, which the reader must refuse.
    """
    blanks = set(blank_cells or [])
    columns = list(range(3))
    if drop_period_index is not None:
        columns = [i for i in columns if i != drop_period_index]
    labels = [SMALL_LABELS[i] for i in columns]
    order = list(reversed(columns)) if newest_first else columns
    header_labels = list(reversed(labels)) if newest_first else labels
    if duplicate_period:
        order = order + [order[-1]]
        header_labels = header_labels + [header_labels[-1]]

    wb = Workbook()
    ws = wb.active
    ws.title = "P&L"
    ws["A1"] = "Income Statement"
    ws["A2"] = "Tiny Co"
    ws["B4"] = "Account"
    for offset, label in enumerate(header_labels):
        ws.cell(4, 3 + offset, label)

    def write(row: int, label: str, values: list[float], label_col: int = 2) -> None:
        ws.cell(row, label_col, label)
        for offset, idx in enumerate(order):
            if (label, idx) in blanks:
                continue
            ws.cell(row, 3 + offset, values[idx])

    def formula(row: int, label: str, template: str, label_col: int = 1) -> None:
        ws.cell(row, label_col, label)
        for offset in range(len(order)):
            col = chr(ord("C") + offset)
            ws.cell(row, 3 + offset, "=" + template.replace("{c}", col))

    ws["A6"] = "Income"
    write(7, "Widgets", SMALL_WIDGETS)
    write(8, "Services", SMALL_SERVICES)
    formula(9, "Total Income", "SUM({c}7:{c}8)")

    ws["A11"] = "Cost of Goods Sold"
    write(12, "Materials", SMALL_MATERIALS)
    formula(13, "Total Cost of Goods Sold", "SUM({c}12:{c}12)")

    formula(15, "Gross Profit", "({c}9 - {c}13)", label_col=2)

    ws["A17"] = "Operating Expenses"
    write(18, "Rent", SMALL_RENT)
    ws.cell(19, 2, "Salaries")               # subtotal group header, no values
    write(20, "Salary A", SMALL_SALARY_A)
    write(21, "Salary B", SMALL_SALARY_B)
    formula(22, "Total Salaries", "SUM({c}20:{c}21)", label_col=2)
    write(23, "Marketing spend", SMALL_MARKETING)
    formula(24, "Total Operating Expenses", "(SUM({c}18,{c}23) + {c}22)")

    formula(26, "Operating Income", "({c}15 - {c}24)", label_col=2)
    formula(28, "Net Income", "({c}26 + 0)", label_col=2)

    # openpyxl writes formulas but no cached results, so supply the cached values the
    # engine will actually read — exactly as Excel would have left them.
    cached = _cached_small_values(order)
    wb2_path = Path(path)
    wb.save(wb2_path)
    _inject_cached_values(wb2_path, cached, len(order))
    return wb2_path


def _cached_small_values(order: list[int]) -> dict[int, list[float]]:
    """The formula rows' correct results, computed here from the inputs directly."""
    out: dict[int, list[float]] = {9: [], 13: [], 15: [], 22: [], 24: [], 26: [], 28: []}
    for idx in order:
        income = SMALL_WIDGETS[idx] + SMALL_SERVICES[idx]
        cogs = SMALL_MATERIALS[idx]
        gross = income - cogs
        salaries = SMALL_SALARY_A[idx] + SMALL_SALARY_B[idx]
        opex = SMALL_RENT[idx] + SMALL_MARKETING[idx] + salaries
        out[9].append(income)
        out[13].append(cogs)
        out[15].append(gross)
        out[22].append(salaries)
        out[24].append(opex)
        out[26].append(gross - opex)
        out[28].append(gross - opex)
    return out


def _inject_cached_values(path: Path, cached: dict[int, list[float]], width: int) -> None:
    """Rewrite the saved file so formula cells carry both a formula and a cached value.

    openpyxl cannot hold both at once in one workbook object, so the file is saved with
    formulas and then the cached results are written into the sheet XML directly.
    """
    import re
    import shutil
    import zipfile

    tmp = path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(path) as zin:
        names = zin.namelist()
        sheet_name = next(n for n in names if n.endswith("sheet1.xml"))
        data = {n: zin.read(n) for n in names}

    xml = data[sheet_name].decode("utf-8")
    for row, values in cached.items():
        for offset in range(width):
            col = chr(ord("C") + offset)
            ref = f"{col}{row}"
            value = values[offset]
            # openpyxl emits an empty self-closing <v /> next to the formula, so both
            # that form and a populated <v>...</v> have to be matched.
            pattern = re.compile(
                rf'(<c r="{ref}"[^>]*>)(<f>.*?</f>)(?:<v\s*/>|<v>.*?</v>)?(</c>)'
            )
            xml, n = pattern.subn(
                lambda mm: f"{mm.group(1)}{mm.group(2)}<v>{value!r}</v>{mm.group(3)}", xml
            )
            if n == 0:
                raise AssertionError(f"could not inject cached value at {ref}")
    data[sheet_name] = xml.encode("utf-8")

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, blob in data.items():
            zout.writestr(name, blob)
    shutil.move(str(tmp), str(path))


SMALL_CONFIG = {
    "client_id": "tiny_co",
    "company_name": "Tiny Co",
    "currency": "USD",
    "currency_symbol": "$",
    "units_scale": 1,
    "source": {"type": "excel", "filename": "tiny.xlsx"},
    "sheets": {
        "income_statement": {
            "name": "P&L",
            "header_row": 4,
            "label_columns": ["A", "B"],
            "first_data_column": "C",
            "last_data_column": "E",
            "date_order": "oldest_first",
            "date_formats": ["%b %Y"],
        }
    },
    "income_statement": {
        "sections": [
            {"key": "revenue", "header_label": "Income", "total_label": "Total Income"},
            {"key": "cogs", "header_label": "Cost of Goods Sold",
             "total_label": "Total Cost of Goods Sold"},
            {"key": "opex", "header_label": "Operating Expenses",
             "total_label": "Total Operating Expenses",
             "subtotal_groups": [
                 {"key": "salaries", "name": "Salaries",
                  "header_label": "Salaries", "total_label": "Total Salaries"}
             ]},
        ],
        "reported_lines": {
            "gross_profit": "Gross Profit",
            "operating_income": "Operating Income",
            "net_income": "Net Income",
        },
    },
    "opex_categories": [
        {"name": "People", "match_contains": ["salary"]},
        {"name": "Marketing", "match_contains": ["marketing"]},
        {"name": "Occupancy", "match_contains": ["rent"]},
    ],
    "fallback_opex_category": "Other Operating Expenses",
    "reconciliation": {
        "tolerance_abs": 0.01,
        "checks": [
            {"key": "revenue_items_vs_total", "description": "revenue items vs total"},
            {"key": "cogs_items_vs_total", "description": "cogs items vs total"},
            {"key": "opex_items_vs_total", "description": "opex items vs total"},
            {"key": "opex_group_subtotals", "description": "group subtotals"},
            {"key": "gross_profit", "description": "gross profit"},
            {"key": "operating_income", "description": "operating income"},
        ],
    },
    "classification_notes": [],
    "thresholds": {
        "significant_mom_pct": 25.0,
        "significant_mom_abs": 40.0,
        "oneoff_multiple": 3.0,
        "oneoff_min_abs": 30.0,
        "oneoff_trailing_months": 6,
        "top_movements": 5,
    },
    "ttm": {"months": 12},
}


#: Plain-value monthly workbooks (no formulas), for tests that need many months or several
#: files. Each month's figures depend only on the month, so the same month carries the
#: same numbers in every file that holds it:
#:   Sales    = 1000 + 10 x (months since Jan 2025)     Services = 200
#:   Hosting  = 100                                     Payroll  = 300     Rent = 50
#: so revenue = 1200 + 10n, COGS = 100, gross profit = 1100 + 10n, opex = 350,
#: operating income = 750 + 10n, where n is months since Jan 2025.
MONTHLY_CONFIG = {
    "client_id": "multi_co",
    "company_name": "Multi Co",
    "currency": "USD",
    "currency_symbol": "$",
    "units_scale": 1,
    "source": {"type": "excel"},
    "sheets": {"income_statement": {
        "name": "Income Statement", "header_row": 5, "label_columns": ["A", "B"],
        "first_data_column": "C", "date_order": "newest_first",
        "date_formats": ["%b %Y"]}},
    "income_statement": {
        "sections": [
            {"key": "revenue", "header_label": "Income", "total_label": "Total Income"},
            {"key": "cogs", "header_label": "Cost of Goods Sold",
             "total_label": "Total Cost of Goods Sold"},
            {"key": "opex", "header_label": "Operating Expenses",
             "total_label": "Total Operating Expenses", "subtotal_groups": []},
        ],
        "reported_lines": {"gross_profit": "Gross Profit",
                           "operating_income": "Operating Income",
                           "net_income": "Net Income"},
    },
    "opex_categories": [{"name": "People", "match_contains": ["payroll"]},
                        {"name": "Occupancy", "match_contains": ["rent"]}],
    "fallback_opex_category": "Other Operating Expenses",
    "reconciliation": {"tolerance_abs": 0.01},
    "classification_notes": [],
    "ttm": {"months": 12},
}


def monthly_values(key: str) -> dict[str, float]:
    """The figures `build_monthly_workbook` writes for one month, by hand."""
    year, month = int(key[:4]), int(key[5:])
    n = (year - 2025) * 12 + (month - 1)
    sales, services, hosting, payroll, rent = 1000.0 + 10 * n, 200.0, 100.0, 300.0, 50.0
    revenue = sales + services
    gross = revenue - hosting
    opex = payroll + rent
    return {"Sales": sales, "Services": services, "Total Income": revenue,
            "Hosting": hosting, "Total Cost of Goods Sold": hosting,
            "Gross Profit": gross, "Payroll": payroll, "Rent": rent,
            "Total Operating Expenses": opex, "Operating Income": gross - opex,
            "Net Income": gross - opex}


def build_monthly_workbook(path: Path, months: list[str],
                           overrides: dict[tuple[str, str], object] | None = None) -> Path:
    """An income statement holding exactly `months` (YYYY-MM), newest first.

    `overrides` maps (label, month) to a replacement cell value: a number, text, or None
    for a genuinely empty cell. Totals are not recomputed, as a careless edit would not.
    """
    import calendar
    overrides = overrides or {}
    ordered = sorted(months, reverse=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Income Statement"
    ws["A1"] = "Multi Co"
    ws["A2"] = "Income Statement"
    for j, key in enumerate(ordered):
        ws.cell(5, 3 + j, f"{calendar.month_abbr[int(key[5:])]} {key[:4]}")
    layout = [("A", "Income"), ("B", "Sales"), ("B", "Services"), ("A", "Total Income"),
              None, ("A", "Cost of Goods Sold"), ("B", "Hosting"),
              ("A", "Total Cost of Goods Sold"), None, ("A", "Gross Profit"), None,
              ("A", "Operating Expenses"), ("B", "Payroll"), ("B", "Rent"),
              ("A", "Total Operating Expenses"), None, ("A", "Operating Income"),
              ("A", "Net Income")]
    headers = {"Income", "Cost of Goods Sold", "Operating Expenses"}
    row = 6
    for entry in layout:
        if entry is not None:
            col, label = entry
            ws[f"{col}{row}"] = label
            if label not in headers:
                for j, key in enumerate(ordered):
                    value = overrides.get((label, key), monthly_values(key)[label])
                    ws.cell(row, 3 + j, value)
        row += 1
    wb.save(path)
    return Path(path)


def write_monthly_config(dir_path: Path, client_id: str = "multi_co",
                         overrides: dict | None = None) -> Path:
    import copy

    cfg = copy.deepcopy(MONTHLY_CONFIG)
    cfg["client_id"] = client_id
    if overrides:
        _deep_update(cfg, overrides)
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"{client_id}.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def write_small_config(dir_path: Path, overrides: dict | None = None,
                       client_id: str = "tiny_co") -> Path:
    import copy

    cfg = copy.deepcopy(SMALL_CONFIG)
    cfg["client_id"] = client_id
    if overrides:
        _deep_update(cfg, overrides)
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"{client_id}.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def _deep_update(target: dict, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
