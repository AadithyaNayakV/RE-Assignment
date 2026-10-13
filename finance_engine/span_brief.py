"""The compact brief for the year and whole-file scopes.

Built the same way as the month brief (`brief.py`): every figure printed is a value already
in metrics.json, formatted by Python, next to the metric name it came from. No arithmetic.
"""

from __future__ import annotations

from finance_engine.brief import _cell, _cell_signed, _issues, _notes, _reconciliation_failures
from finance_engine.formatting import Formatter

HEADLINE = [
    ("Total revenue", "revenue.total"),
    ("Cost of goods sold", "cogs.total"),
    ("Gross profit", "gross_profit"),
    ("Gross margin", "gross_margin_pct"),
    ("Total operating expenses", "opex.total"),
    ("Operating income", "operating_income"),
    ("Operating margin", "operating_margin_pct"),
    ("Net income (as reported)", "net_income.reported"),
]
CURRENCY = ["revenue.total", "cogs.total", "gross_profit", "opex.total", "operating_income"]


def build_span_brief(doc: dict) -> str:
    fmt = Formatter(doc.get("currency_symbol", "$"), doc.get("currency", "USD"))
    m = {x["name"]: x for x in doc.get("metrics", [])}
    parts = [_header(doc), _status(doc), _reconciliation_failures(doc, fmt), _issues(doc)]
    if doc.get("scope") == "year":
        parts += [_totals(doc, m, fmt), _family(doc, m, fmt, "revenue.stream.",
                                                "Revenue streams for the year"),
                  _family(doc, m, fmt, "opex.category.",
                          "Operating expenses by category for the year"),
                  _quarters(doc, m, fmt)]
    else:
        parts += [_years(doc, m, fmt), _start_to_end(doc, m, fmt)]
    parts += [_monthly(doc, m, fmt), _extremes(doc, m, fmt), _notes(doc)]
    return "\n\n".join(p for p in parts if p).rstrip() + "\n"


def _header(doc) -> str:
    src = doc.get("source") or {}
    return "\n".join([
        f"# Brief: {doc.get('company_name')} — {doc.get('period_label')}",
        "",
        f"- client_id: `{doc.get('client_id')}`",
        f"- scope: `{doc.get('scope')}`" + (f", year {doc.get('year')}"
                                            if doc.get("scope") == "year" else ""),
        f"- currency: {doc.get('currency')}",
        f"- source: `{src.get('path')}` (sha256 `{str(src.get('sha256'))[:16]}...`), "
        f"{src.get('period_count')} months in the file",
        "",
        "Every figure below is a value in metrics.json, formatted by Python. Quote figures "
        "exactly as printed. A figure that is not printed here or in "
        "`brief --metric <name>` is not in metrics.json: do not derive it. A partial "
        "year is never scaled or annualised.",
    ])


def _status(doc) -> str:
    recon = doc.get("reconciliation") or {}
    cov = doc.get("coverage") or {}
    issues = doc.get("data_quality_issues") or []
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    blocked = [x for x in doc.get("metrics", []) if x.get("validation_status") == "blocked"]
    seasonality = doc.get("seasonality") or {}
    lines = ["## Status", ""]
    if doc.get("scope") == "year":
        gaps = cov.get("gaps_inside_span") or []
        lines.append(f"- Coverage: {cov.get('status')} — {cov.get('label')}")
        lines.append("- Year totals: "
                     + ("n/a: a month is missing inside the year "
                        f"({', '.join(gaps)}); do not present any total" if gaps
                        else "available"))
        yoy = doc.get("yoy") or {}
        lines.append("- Year on year: " + ("compared, same months in both years"
                                           if yoy.get("comparable")
                                           else "NOT compared. " + yoy.get("note", "")))
    else:
        lines.append(f"- Coverage: {cov.get('label')}; missing inside the file: "
                     f"{', '.join(cov.get('gaps') or []) or 'none'}")
    lines += [
        f"- Reconciliation: {'PASS' if recon.get('all_passed') else 'FAIL'} — "
        f"{recon.get('checks_failed', 0)} of {recon.get('checks_run', 0)} checks failed",
        f"- Data-quality errors: {len(errors)}; warnings: {len(warnings)}; "
        f"blocked metrics: {len(blocked)}",
        "- Seasonality: " + ("assessed" if seasonality.get("assessed")
                             else f"NOT assessed. {seasonality.get('reason', '')}".strip()),
    ]
    return "\n".join(lines)


def _totals(doc, m, fmt) -> str:
    year = doc.get("year")
    lines = ["## Year totals", "",
             f"| Measure | metric | {year} | prior year, same months | change | % / pts |",
             "| --- | --- | --: | --: | --: | --: |"]
    for label, key in HEADLINE:
        if key not in m:
            continue
        change = m.get(f"{key}.yoy_pts") or m.get(f"{key}.yoy_abs")
        pct = None if key.endswith("_pct") else m.get(f"{key}.yoy_pct")
        lines.append(f"| {label} | `{key}` | {fmt.metric(m[key])} | "
                     f"{_cell(fmt, m.get(key + '.prior_year'))} | "
                     f"{_cell_signed(fmt, change)} | {_cell_signed(fmt, pct)} |")
    return "\n".join(lines)


def _family(doc, m, fmt, prefix: str, title: str) -> str:
    rows = [x for x in doc.get("metrics", [])
            if x["name"].startswith(prefix) and x["name"].count(".") == 2]
    if not rows:
        return ""
    rows.sort(key=lambda x: (x.get("value") is None, -(x.get("value") or 0)))
    lines = [f"## {title}", "", "| Line | metric | value |", "| --- | --- | --: |"]
    lines += [f"| {x.get('label')} | `{x['name']}` | {fmt.metric(x)} |" for x in rows]
    return "\n".join(lines)


def _quarters(doc, m, fmt) -> str:
    quarters = doc.get("quarters") or []
    if not quarters:
        return ""
    lines = ["## Quarters", "",
             "A quarter has a value only when all three months are in the file.", "",
             "| Measure | " + " | ".join(f"{q['quarter']} (`<metric>.{q['quarter'].lower()}`)"
                                         for q in quarters) + " |",
             "| --- |" + " --: |" * len(quarters)]
    for key in CURRENCY:
        label = next(lbl for lbl, k in HEADLINE if k == key)
        lines.append(f"| {label} | " + " | ".join(
            fmt.metric(m.get(f"{key}.q{i + 1}")) for i in range(len(quarters))) + " |")
    return "\n".join(lines)


def _years(doc, m, fmt) -> str:
    years = doc.get("years") or []
    lines = ["## Calendar years", "",
             "| Year | coverage | metric prefix | revenue | gross profit | gross margin | "
             "opex | operating income |", "| --- | --- | --- | --: | --: | --: | --: | --: |"]
    for y in years:
        p = y["metric_prefix"]
        lines.append(f"| {y['year']} | {y['label']} | `{p}` | "
                     f"{fmt.metric(m.get(p + 'revenue.total'))} | "
                     f"{fmt.metric(m.get(p + 'gross_profit'))} | "
                     f"{fmt.metric(m.get(p + 'gross_margin_pct'))} | "
                     f"{fmt.metric(m.get(p + 'opex.total'))} | "
                     f"{fmt.metric(m.get(p + 'operating_income'))} |")
    return "\n".join(lines)


def _start_to_end(doc, m, fmt) -> str:
    ste = doc.get("start_to_end") or {}
    first, last = ste.get("first"), ste.get("last")
    lines = [f"## First month ({first}) to last month ({last})", "",
             "| Measure | metric | change | % / pts |", "| --- | --- | --: | --: |"]
    for label, key in HEADLINE[:7]:
        if key.endswith("_pct"):
            lines.append(f"| {label} | `{key}.start_to_end_pts` | "
                         f"{_cell_signed(fmt, m.get(key + '.start_to_end_pts'))} | — |")
        else:
            lines.append(f"| {label} | `{key}.start_to_end_abs` | "
                         f"{_cell_signed(fmt, m.get(key + '.start_to_end_abs'))} | "
                         f"{_cell_signed(fmt, m.get(key + '.start_to_end_pct'))} |")
    return "\n".join(lines)


def _monthly(doc, m, fmt) -> str:
    months = (doc.get("coverage") or {}).get("months_present") or []
    lines = ["## Months (`monthly.<YYYY-MM>.<measure>`)", "",
             "Changes are against the immediately preceding calendar month in this file.",
             "",
             "| Month | revenue | MoM | MoM % | gross margin | opex | operating income | "
             "MoM | operating margin |",
             "| --- | --: | --: | --: | --: | --: | --: | --: | --: |"]
    for p in months:
        b = f"monthly.{p}."
        lines.append(f"| {p} | {fmt.metric(m.get(b + 'revenue.total'))} | "
                     f"{_cell_signed(fmt, m.get(b + 'revenue.total.mom_abs'))} | "
                     f"{_cell_signed(fmt, m.get(b + 'revenue.total.mom_pct'))} | "
                     f"{fmt.metric(m.get(b + 'gross_margin_pct'))} | "
                     f"{fmt.metric(m.get(b + 'opex.total'))} | "
                     f"{fmt.metric(m.get(b + 'operating_income'))} | "
                     f"{_cell_signed(fmt, m.get(b + 'operating_income.mom_abs'))} | "
                     f"{fmt.metric(m.get(b + 'operating_margin_pct'))} |")
    return "\n".join(lines)


def _extremes(doc, m, fmt) -> str:
    ext = doc.get("extremes") or {}
    if not ext:
        return ""
    lines = ["## Highest and lowest months", "",
             "| Measure | highest | month | lowest | month |",
             "| --- | --: | --- | --: | --- |"]
    for key, label in (("revenue.total", "Total revenue"),
                       ("operating_income", "Operating income")):
        e = ext.get(key) or {}
        lines.append(f"| {label} | {fmt.metric(m.get(f'extremes.{key}.highest'))} | "
                     f"{(e.get('highest') or {}).get('period', '—')} | "
                     f"{fmt.metric(m.get(f'extremes.{key}.lowest'))} | "
                     f"{(e.get('lowest') or {}).get('period', '—')} |")
    return "\n".join(lines)
