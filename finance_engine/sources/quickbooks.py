"""QuickBooks source: reads a saved Profit and Loss snapshot, never the network.

`pull-quickbooks` saves Intuit's raw `ProfitAndLoss` response (one column per month) as a
JSON file in `data/clients/<id>/source/`. This reader turns that file into the same
`Dataset` the Excel reader produces, so validation, metrics, reconciliation, the guard and
the reports run unchanged. The file is hashed (SHA-256) and selectable with `--file`, like
a workbook.

How the report maps onto the model (built from a real sandbox response; see
tests/fixtures/qb_pl_monthly.json):

* **Rows, in report order.** Each section header, account row and summary row becomes
  one line, numbered from 2 (row 1 is the column headings), as if the report were
  exported to a sheet. Column A holds the label; B onwards the months, oldest first.
* **Sections** come from the config's anchors exactly as for Excel: the rows between
  "Income" and "Total Income" are revenue, and so on. Intuit's own structure supplies
  the rows; the label never decides the section.
* **Sub-accounts.** A nested account group ("Job Expenses" > "Job Materials") is
  flattened. Its accounts become lines labelled by their path
  ("Job Expenses: Job Materials: Plants and Soil"), so the same account name under Income
  and Expenses never collides. Its own total row is kept as a `subtotal` line that is
  never summed, so nothing is counted twice; the section total still reconciles every
  account. A parent account's own postings, which Intuit puts in the group's heading
  row, become a line "<parent> (own amount)". An account named like its own heading
  ("Cost of Goods Sold" inside "Cost of Goods Sold") is labelled "<name> (account)".
* **A blank cell is zero.** QuickBooks leaves a cell empty when an account had no
  activity that month, and its own totals count it as 0.00 ("Total Income 0.00" over a
  column of blanks). This reader does the same, and says so in the client's
  classification note. It is the source's definition of zero, not a value filled in.
* **Other Income and Other Expenses** (below Net Operating Income) sit outside the three
  sections, so they are never folded into revenue or operating expenses. Net income as
  reported is Intuit's Net Income, which includes them.
* **A section QuickBooks leaves out.** QuickBooks drops a whole group from the report when
  nothing was posted to it in the range (a range with no cost of goods sold has no "Cost
  of Goods Sold" header, rows or total). For the groups the client config lists under
  `source.may_be_omitted`, the reader puts the section back where Intuit would have sent
  it: its header, one line "<section> (left out by QuickBooks)" holding 0.00 for every
  month (with a note in its trace), and its total row with no values, marked as not
  provided. A reconciliation check against that total is listed as "not provided by
  QuickBooks", never as a pass or a failure, and a warning says the section was counted
  as 0. A left-out summary row (Gross Profit, Net Income...) gets no values at all:
  Intuit's own totals are never filled in.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
from pathlib import Path

from openpyxl.utils import get_column_letter

from finance_engine.config import ClientConfig
from finance_engine.model import Cell, Dataset, LayoutError, LineItem, Period, Sheet
from finance_engine.sources.base import DataSource
from finance_engine.sources.excel import apply_income_statement_structure

SHEET_NAME = "ProfitAndLoss"

#: Intuit's top-level row groups of a Profit and Loss report, in the order it sends them.
GROUP_ORDER = ("Income", "COGS", "GrossProfit", "Expenses", "NetOperatingIncome",
               "OtherIncome", "OtherExpenses", "NetOtherIncome", "NetIncome")


class QuickBooksSnapshotSource(DataSource):
    source_type = "quickbooks"

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.exists():
            raise LayoutError(f"QuickBooks snapshot not found: {self.path}")
        self._sha256: str | None = None

    def sha256(self) -> str:
        if self._sha256 is None:
            self._sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        return self._sha256

    def describe(self) -> dict:
        return {"type": self.source_type, "path": str(self.path),
                "filename": self.path.name, "sha256": self.sha256()}

    def load(self, config: ClientConfig) -> Dataset:
        try:
            report = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LayoutError(f"{self.path.name} is not a readable QuickBooks snapshot: "
                              f"{' '.join(str(exc).split())[:160]}") from None
        warnings: list[dict] = []
        sheet = read_profit_and_loss(report, self.path.name, config, warnings)
        apply_income_statement_structure(sheet, config)
        return Dataset(client_id=config.client_id, company_name=config.company_name,
                       currency=config.currency, source_type=self.source_type,
                       source_path=str(self.path), source_sha256=self.sha256(),
                       income_statement=sheet, source_warnings=warnings)


def snapshot_header(path: Path) -> dict:
    """What a snapshot says about itself, read even when its rows cannot be analysed:
    the months it was asked for (`start`, `end` as YYYY-MM) and when it was pulled
    (`pulled_at`, Intuit's report time, timezone-aware). Missing values are None."""
    out = {"start": None, "end": None, "pulled_at": None}
    try:
        header = json.loads(Path(path).read_text(encoding="utf-8")).get("Header") or {}
    except (OSError, ValueError, AttributeError):
        header = {}
    for key, name in (("start", "StartPeriod"), ("end", "EndPeriod")):
        try:
            out[key] = dt.date.fromisoformat(str(header.get(name)))
        except ValueError:
            pass
    try:
        when = dt.datetime.fromisoformat(str(header.get("Time")))
        out["pulled_at"] = when if when.tzinfo else None
    except ValueError:
        pass
    if out["pulled_at"] is None:                   # a file Intuit did not stamp
        out["pulled_at"] = dt.datetime.fromtimestamp(Path(path).stat().st_mtime,
                                                     dt.timezone.utc) \
            if Path(path).exists() else None
    return out


def omitted_groups(report: dict, config: ClientConfig) -> list[tuple[str, str]]:
    """(config key, Intuit group) for each group the config allows QuickBooks to leave
    out and this report does leave out, in Intuit's order."""
    allowed = (config.raw.get("source") or {}).get("may_be_omitted") or {}
    known = {s["key"] for s in config.sections} | set(config.reported_lines)
    for key, group in allowed.items():
        if group not in GROUP_ORDER or key not in known:
            raise LayoutError(f"config/clients/{config.client_id}.yaml: "
                              f"source.may_be_omitted maps {key!r} to {group!r}; the key "
                              f"must be a section or reported line and the value one of "
                              f"{', '.join(GROUP_ORDER)}.")
    present = {r.get("group") for r in (report.get("Rows") or {}).get("Row") or []}
    return sorted(((k, g) for k, g in allowed.items() if g not in present),
                  key=lambda kg: GROUP_ORDER.index(kg[1]))


def read_profit_and_loss(report: dict, name: str, config: ClientConfig,
                         warnings: list[dict] | None = None) -> Sheet:
    header = report.get("Header") or {}
    if header.get("ReportName") != "ProfitAndLoss":
        raise LayoutError(f"{name}: expected a QuickBooks ProfitAndLoss report, got "
                          f"{header.get('ReportName')!r}.")
    if header.get("SummarizeColumnsBy") != "Month":
        raise LayoutError(f"{name}: the report must have one column per month "
                          f"(SummarizeColumnsBy=Month), got "
                          f"{header.get('SummarizeColumnsBy')!r}. Pull it again with "
                          f"pull-quickbooks.")
    span = f"{header.get('StartPeriod')} to {header.get('EndPeriod')}"
    options = {o.get("Name"): o.get("Value") for o in header.get("Option") or []}
    rows = (report.get("Rows") or {}).get("Row") or []
    if str(options.get("NoReportData", "")).lower() == "true" or not rows:
        raise LayoutError(f"{name}: QuickBooks has no Profit and Loss data between "
                          f"{span}. Pull a range with activity in it.")
    currency = header.get("Currency")
    if currency and config.currency and currency != config.currency:
        raise LayoutError(f"{name}: the report is in {currency}, but "
                          f"config/clients/{config.client_id}.yaml says {config.currency}.")

    columns = (report.get("Columns") or {}).get("Column") or []
    months: list[tuple[int, Period]] = []          # (column index, period)
    for index, column in enumerate(columns):
        if column.get("ColType") != "Money":
            continue
        meta = {m.get("Name"): m.get("Value") for m in column.get("MetaData") or []}
        if "StartDate" not in meta:               # the Total column
            continue
        start = dt.date.fromisoformat(meta["StartDate"])
        end = dt.date.fromisoformat(meta["EndDate"])
        last_day = calendar.monthrange(start.year, start.month)[1]
        if start.day != 1 or end != start.replace(day=last_day):
            raise LayoutError(f"{name}: column {column.get('ColTitle')!r} covers "
                              f"{start}..{end}, not a whole calendar month. Pull whole "
                              f"months (pull-quickbooks does).")
        months.append((index, Period(start.year, start.month, column.get("ColTitle", ""))))
    if not months:
        raise LayoutError(f"{name}: no monthly columns between {span}.")
    ordinals = [p.ordinal() for _, p in months]
    if ordinals != sorted(set(ordinals)):
        raise LayoutError(f"{name}: the month columns are not strictly oldest first.")

    sheet = Sheet(
        name=SHEET_NAME, periods=[p for _, p in months],
        titles=[f"QuickBooks Online {header.get('ReportName')}",
                f"{span}, {header.get('ReportBasis', '')} basis, {currency or ''}".strip()],
        period_columns={p.key: get_column_letter(i + 1) for i, p in months})
    counter = [1]                                  # row 1: the column headings

    def line(label: str, cells_raw: list, role: str) -> None:
        counter[0] += 1
        row = counter[0]
        cells = {}
        for index, period in months:
            raw = cells_raw[index].get("value") if index < len(cells_raw) else None
            if raw is None:
                continue
            value = _amount(raw, name, label, period.key)
            cells[period.key] = Cell(sheet=SHEET_NAME, row=row,
                                     column=get_column_letter(index + 1), label=label,
                                     period=period.key, value=value)
        sheet.line_items.append(LineItem(sheet=SHEET_NAME, row=row, label=label,
                                         role=role, cells=cells))

    def walk(items: list, path: list[str], top: bool, heading: str = "") -> None:
        for item in items:
            kind = item.get("type")
            if kind == "Data":
                cells_raw = item.get("ColData") or []
                label = _label(cells_raw)
                # Intuit tells a section heading from an account by row type; the model
                # tells them apart by label. An account named like its own section
                # ("Cost of Goods Sold" inside "Cost of Goods Sold") is marked, so the
                # heading stays an unambiguous anchor.
                if label == heading or label in path:
                    label = f"{label} (account)"
                line(": ".join(path + [label]) if path else label, cells_raw, "item")
                continue
            # A Section: header row (if any), its rows, then its summary row.
            head = (item.get("Header") or {}).get("ColData") or []
            summary = (item.get("Summary") or {}).get("ColData") or []
            title = _label(head) if head else ""
            if head:
                heading_label = ": ".join(path + [title]) if (path and not top) else title
                line(heading_label, [{}], "header")
                # A parent account's own postings come in its heading row (Intuit:
                # "Landscaping Services" 642.50, its total 3,939.08 = 642.50 + the
                # sub-accounts). They are an account line of their own, so the section
                # still reconciles and the heading stays a label-only anchor.
                if not top and any(str(c.get("value") or "").strip() for c in head[1:]):
                    line(f"{heading_label} (own amount)", head, "item")
            inner = path + [title] if (title and not top) else path
            walk((item.get("Rows") or {}).get("Row") or [], inner, top=False, heading=title)
            if summary:
                total = _label(summary)
                # A nested group's total is a subtotal, never summed again. A top-level
                # summary (Total Income, Gross Profit, Net Income...) is a total row the
                # config's anchors turn into a section total or a reported line.
                if top:
                    line(total, summary, "item")
                else:
                    line(f"Total {': '.join(inner)}", summary, "subtotal")

    # ---- groups QuickBooks left out (only those the config allows) ----
    pending = omitted_groups(report, config)
    sections = {s["key"]: s for s in config.sections}
    notes = warnings if warnings is not None else []

    def put_back(key: str) -> None:
        sec = sections.get(key)
        if sec is not None:
            told = (f"QuickBooks left out the {sec['header_label']} section for this "
                    f"range; counted as 0")
            line(sec["header_label"], [{}], "header")
            counter[0] += 1
            label = f"{sec['header_label']} (left out by QuickBooks)"
            sheet.line_items.append(LineItem(
                sheet=SHEET_NAME, row=counter[0], label=label, role="item",
                cells={p.key: Cell(sheet=SHEET_NAME, row=counter[0],
                                   column=get_column_letter(i + 1), label=label,
                                   period=p.key, value=0.0, note=told + ".")
                       for i, p in months}))
            counter[0] += 1
            sheet.line_items.append(LineItem(
                sheet=SHEET_NAME, row=counter[0], label=sec["total_label"], role="item",
                omitted_by_source=f"not provided by QuickBooks: the report has no "
                                  f"{sec['total_label']} row for this range"))
            notes.append({"code": "quickbooks_section_omitted", "message": told + ".",
                          "affects": (f"{key}.",)})
            return
        label = config.reported_lines[key]
        counter[0] += 1
        sheet.line_items.append(LineItem(
            sheet=SHEET_NAME, row=counter[0], label=label, role="item",
            omitted_by_source=f"not provided by QuickBooks: the report has no {label} "
                              f"row for this range"))
        notes.append({"code": "quickbooks_row_omitted",
                      "message": f"QuickBooks left out its {label} row for this range. It "
                                 f"is not filled in: checks against it are listed as not "
                                 f"provided by QuickBooks.",
                      "affects": (key, f"{key}.")})

    def put_back_before(group: str | None) -> None:
        if group not in GROUP_ORDER:
            return
        while pending and GROUP_ORDER.index(pending[0][1]) < GROUP_ORDER.index(group):
            put_back(pending.pop(0)[0])

    for item in rows:
        put_back_before(item.get("group"))
        walk([item], [], top=True)
    while pending:
        put_back(pending.pop(0)[0])
    return sheet


def _label(cells: list) -> str:
    return " ".join(str((cells[0] if cells else {}).get("value") or "").split())


def _amount(raw, name: str, label: str, period: str) -> float:
    text = str(raw).strip().replace(",", "")
    if text == "":
        return 0.0                                 # QuickBooks' own zero; see the docstring
    try:
        return float(text)
    except ValueError:
        raise LayoutError(f"{name}: {label!r} for {period} holds {raw!r}, not an amount.") \
            from None
