"""report.md for the year and whole-file scopes.

The same rules as the month report (`report.py`): a draft for a human reviewer, Python
formats every number and recalculates none, a section with no data says so, warnings and
blocked metrics sit near the top, and every figure is a value in metrics.json.

Wording rules for this file: state what changed, never why. No "because", "due to",
"caused", "driven by" or "reason" (the eval check `causal_words_outside_questions`).
"""

from __future__ import annotations

from finance_engine.formatting import Formatter
from finance_engine.report import (NOT_AVAILABLE_NOTE, _commentary, _data_quality,
                                   _limitations, fmt_tolerance)

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
CURRENCY_HEADLINE = ["revenue.total", "cogs.total", "gross_profit", "opex.total",
                     "operating_income"]


def build_span_report(doc: dict, commentary: str = "", provider_id: str = "deterministic",
                      guard_result: dict | None = None,
                      commentary_from_brief: bool = False) -> str:
    fmt = Formatter(doc.get("currency_symbol", "$"), doc.get("currency", "USD"))
    m = {x["name"]: x for x in doc.get("metrics", [])}
    if doc.get("scope") == "year":
        parts = [
            _header(doc), _status(doc, m, guard_result), _source(doc),
            _commentary(commentary, provider_id, guard_result, commentary_from_brief),
            _year_coverage(doc),
            _revenue(doc, m, fmt), _margins(doc, m, fmt), _expenses(doc, m, fmt),
            _quarters(doc, m, fmt), _year_on_year(doc, m, fmt),
            _monthly_table(doc, m, fmt), _extremes(doc, m, fmt),
            _reconciliation(doc, fmt), _data_quality(doc), _limitations(doc),
            _checklist(doc, m),
        ]
    else:
        parts = [
            _header(doc), _status(doc, m, guard_result), _source(doc),
            _commentary(commentary, provider_id, guard_result, commentary_from_brief),
            _all_coverage(doc),
            _years(doc, m, fmt), _start_to_end(doc, m, fmt), _monthly_table(doc, m, fmt),
            _seasonality(doc), _reconciliation(doc, fmt), _data_quality(doc),
            _limitations(doc), _checklist(doc, m),
        ]
    return "\n\n".join(p for p in parts if p).rstrip() + "\n"


# ---- header and status --------------------------------------------------------

def _header(doc) -> str:
    if doc.get("scope") == "year":
        title = f"# {doc.get('company_name')} — annual financial summary, {doc.get('year')}"
        line = f"**Year:** {doc.get('period_label')}  "
    else:
        title = f"# {doc.get('company_name')} — whole-file financial summary"
        line = f"**Coverage:** {doc.get('period_label')}  "
    return "\n".join([
        title, "",
        line,
        f"**Client:** `{doc.get('client_id')}`  ",
        f"**Currency:** {doc.get('currency')}  ",
        f"**Generated:** {doc.get('generated_at_utc')}  ",
        f"**metrics.json schema:** v{doc.get('schema_version')}",
        "",
        "> **This is a draft for a human reviewer, not a reviewed financial statement.**",
        "> Every figure is calculated by Python from the source workbook and reconciled",
        "> against the workbook's own totals. A partial year is the total of the months",
        "> present only: nothing is scaled, annualised or estimated. The commentary",
        "> describes the figures and never explains them.",
    ])


def _status(doc, m, guard_result) -> str:
    recon = doc.get("reconciliation") or {}
    issues = doc.get("data_quality_issues") or []
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    blocked = [x for x in doc.get("metrics", []) if x.get("validation_status") == "blocked"]
    cov = doc.get("coverage") or {}
    if doc.get("scope") == "year":
        coverage = ("complete" if cov.get("status") == "complete" else "**partial**",
                    cov.get("label", ""))
        totals = m.get("revenue.total") or {}
        rows = [("Coverage", *coverage),
                ("Year totals",
                 "available" if totals.get("value") is not None else "**not available**",
                 "summed from the monthly figures" if totals.get("value") is not None
                 else "a month inside the year is missing; see Data quality")]
        yoy = doc.get("yoy") or {}
        rows.append(("Year on year", "compared" if yoy.get("comparable") else "not compared",
                     "same months in both years" if yoy.get("comparable")
                     else "the same months are not in both years in this file"))
    else:
        rows = [("Coverage", "contiguous" if cov.get("contiguous") else "**gaps**",
                 cov.get("label", ""))]
    rows += [
        # Both counts are stored in metrics.json; a passed count would be derived.
        ("Reconciliation", "PASS" if recon.get("all_passed") else "**FAIL**",
         (f"all {recon.get('checks_run', 0)} checks passed" if recon.get("all_passed")
          else f"{recon.get('checks_failed', 0)} of {recon.get('checks_run', 0)} checks "
               f"failed")
         + f" in the months covered (tolerance {fmt_tolerance(recon)})"),
        ("Data-quality errors", "none" if not errors else f"**{len(errors)}**",
         "; ".join(i.get("code", "") for i in errors) or "—"),
        ("Data-quality warnings", "none" if not warnings else str(len(warnings)),
         "; ".join(i.get("code", "") for i in warnings) or "—"),
        ("Blocked metrics", "none" if not blocked else str(len(blocked)),
         "shown as n/a, never estimated" if blocked else "—"),
    ]
    if guard_result is not None:
        rows.append(("Commentary number check",
                     "PASS" if guard_result.get("passed") else "**REJECTED**",
                     f"{guard_result.get('numbers_supported', 0)}"
                     f"/{guard_result.get('numbers_checked', 0)} numbers traced to "
                     f"metrics.json"))
    lines = ["## Status at a glance", "", "| Check | Result | Detail |", "| --- | --- | --- |"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    return "\n".join(lines)


def _source(doc) -> str:
    src = doc.get("source") or {}
    periods = src.get("periods_available") or []
    lines = [
        "## Source", "",
        f"- **File:** `{src.get('path')}`",
        f"- **SHA-256:** `{src.get('sha256')}`",
        f"- **Config:** `{src.get('config_path')}`",
        f"- **Sheets read:** {', '.join(src.get('sheets') or []) or '—'}",
        (f"- **Months in the file:** {src.get('period_count')} ({periods[0]} to "
         f"{periods[-1]})") if periods else "- **Months in the file:** none",
    ]
    selection = src.get("selection")
    if selection:
        others = selection.get("other_workbooks") or []
        lines.append(f"- **Workbook chosen by:** {selection.get('chosen_by')}")
        if others:
            lines.append("- **Other workbooks in the folder (not read):** "
                         + "; ".join(f"`{o['filename']}`" for o in others))
    lines += ["", "Only this workbook is read, opened read-only and never modified. Files "
                  "are never combined."]
    return "\n".join(lines)


# ---- coverage -------------------------------------------------------------------

def _year_coverage(doc) -> str:
    cov = doc.get("coverage") or {}
    lines = ["## Coverage", "",
             f"- **Status:** {cov.get('status')} — {cov.get('label')}",
             f"- **Months present:** {', '.join(cov.get('months_present') or []) or 'none'}",
             f"- **Months not in the file:** "
             f"{', '.join(cov.get('months_absent') or []) or 'none'}",
             f"- **Missing inside the year:** "
             f"{', '.join(cov.get('gaps_inside_span') or []) or 'none'}"]
    if cov.get("gaps_inside_span"):
        lines += ["", "**The year totals and the year-on-year comparison are n/a.** A "
                      "month is missing inside the year, and a total is never summed over "
                      "the months that remain. Quarters with all three months present are "
                      "still shown."]
    elif cov.get("status") != "complete":
        lines += ["", "These totals cover the months present only. They are **not** "
                      "scaled, annualised or extrapolated to a full year."]
    return "\n".join(lines)


def _all_coverage(doc) -> str:
    cov = doc.get("coverage") or {}
    lines = ["## Coverage", "",
             f"- **First month:** {cov.get('first')}",
             f"- **Last month:** {cov.get('last')}",
             f"- **Months present:** {cov.get('months_present_count')}",
             f"- **Missing inside the file:** {', '.join(cov.get('gaps') or []) or 'none'}"]
    return "\n".join(lines)


# ---- year sections --------------------------------------------------------------

def _revenue(doc, m, fmt) -> str:
    year = doc.get("year")
    streams = [x for x in doc.get("metrics", [])
               if x["name"].startswith("revenue.stream.") and x["name"].count(".") == 2]
    if not streams:
        return "## Revenue\n\n" + NOT_AVAILABLE_NOTE
    streams.sort(key=lambda x: (x.get("value") is None, -(x.get("value") or 0)))
    lines = ["## Revenue", "", f"| Stream | {year} |", "| --- | --: |"]
    lines += [f"| {s.get('label')} | {fmt.metric(s)} |" for s in streams]
    lines.append(f"| **Total revenue** | **{fmt.metric(m.get('revenue.total'))}** |")
    return "\n".join(lines)


def _margins(doc, m, fmt) -> str:
    year = doc.get("year")
    lines = ["## Margins and profitability", "",
             f"| Measure | {year} |", "| --- | --: |"]
    for label, key in HEADLINE:
        if key in m:
            lines.append(f"| {label} | {fmt.metric(m[key])} |")
    notes = doc.get("classification_notes") or []
    if notes:
        lines += ["", "**Classification note.** " + " ".join(notes)]
    return "\n".join(lines)


def _expenses(doc, m, fmt) -> str:
    year = doc.get("year")
    cats = [x for x in doc.get("metrics", [])
            if x["name"].startswith("opex.category.") and x["name"].count(".") == 2]
    if not cats:
        return "## Operating expenses\n\n" + NOT_AVAILABLE_NOTE
    cats.sort(key=lambda x: (x.get("value") is None, -(x.get("value") or 0)))
    lines = ["## Operating expenses", "", "### By category", "",
             f"| Category | {year} |", "| --- | --: |"]
    lines += [f"| {c.get('label')} | {fmt.metric(c)} |" for c in cats]
    lines.append(f"| **Total** | **{fmt.metric(m.get('opex.total'))}** |")
    lines += ["", "Categories are assigned by the client config, not by line name alone, "
                  "and sum to the total above."]
    return "\n".join(lines)


def _quarters(doc, m, fmt) -> str:
    quarters = doc.get("quarters") or []
    if not quarters:
        return ""
    head = " | ".join(q["quarter"] for q in quarters)
    lines = ["## Quarters", "", f"| Measure | {head} |",
             "| --- |" + " --: |" * len(quarters)]
    for key in CURRENCY_HEADLINE:
        label = next(lbl for lbl, k in HEADLINE if k == key)
        cells = " | ".join(fmt.metric(m.get(f"{key}.q{i + 1}")) for i in range(len(quarters)))
        lines.append(f"| {label} | {cells} |")
    incomplete = [q for q in quarters if not q["complete"]]
    lines += ["", "A quarter is shown only when all three of its months are in the file; "
                  "an incomplete quarter is n/a, never a part-quarter."]
    if incomplete:
        lines.append("Incomplete: " + "; ".join(
            f"{q['quarter']} lacks {', '.join(q['missing'])}" for q in incomplete) + ".")
    return "\n".join(lines)


def _year_on_year(doc, m, fmt) -> str:
    yoy = doc.get("yoy") or {}
    year = doc.get("year")
    if not yoy.get("comparable"):
        missing = yoy.get("missing_in_prior_year") or []
        detail = (f" The prior-year months it would need are not all in this file: "
                  f"{', '.join(missing)}." if missing else "")
        return "\n".join([
            "## Year on year", "",
            f"**Not compared.** Year on year is shown only when the same months exist in "
            f"both years in this file.{detail} No other comparison is put in its place."])
    months = yoy.get("months") or []
    prior = yoy.get("prior_year_months") or []
    lines = ["## Year on year", "",
             f"{len(months)} month(s), {months[0]}..{months[-1]}, against the same months "
             f"of the prior year, {prior[0]}..{prior[-1]}.", "",
             f"| Measure | {year} | Prior year | Change | % change |",
             "| --- | --: | --: | --: | --: |"]
    for key in CURRENCY_HEADLINE:
        label = next(lbl for lbl, k in HEADLINE if k == key)
        lines.append(f"| {label} | {fmt.metric(m.get(key))} | "
                     f"{fmt.metric(m.get(f'{key}.prior_year'))} | "
                     f"{fmt.metric_signed(m.get(f'{key}.yoy_abs'))} | "
                     f"{fmt.metric_signed(m.get(f'{key}.yoy_pct'))} |")
    for key in ("gross_margin_pct", "operating_margin_pct"):
        label = next(lbl for lbl, k in HEADLINE if k == key)
        lines.append(f"| {label} | {fmt.metric(m.get(key))} | "
                     f"{fmt.metric(m.get(f'{key}.prior_year'))} | "
                     f"{fmt.metric(m.get(f'{key}.yoy_pts'))} | — |")
    lines += ["", "A percentage change is shown only where the prior-year figure was "
                  "strictly positive."]
    return "\n".join(lines)


def _monthly_table(doc, m, fmt) -> str:
    months = [p for p in (doc.get("coverage") or {}).get("months_present") or []]
    if not months:
        return ""
    title = "## Monthly table" if doc.get("scope") == "year" else "## Monthly series"
    lines = [title, "",
             "Each month as the month report computes it. Changes are against the "
             "immediately preceding calendar month in this file.", "",
             "| Month | Revenue | Change | % change | Gross profit | Gross margin | "
             "Operating expenses | Operating income | Change | Operating margin |",
             "| --- | --: | --: | --: | --: | --: | --: | --: | --: | --: |"]
    for p in months:
        base = f"monthly.{p}."
        lines.append(
            f"| {p} | {fmt.metric(m.get(base + 'revenue.total'))} | "
            f"{fmt.metric_signed(m.get(base + 'revenue.total.mom_abs'))} | "
            f"{fmt.metric_signed(m.get(base + 'revenue.total.mom_pct'))} | "
            f"{fmt.metric(m.get(base + 'gross_profit'))} | "
            f"{fmt.metric(m.get(base + 'gross_margin_pct'))} | "
            f"{fmt.metric(m.get(base + 'opex.total'))} | "
            f"{fmt.metric(m.get(base + 'operating_income'))} | "
            f"{fmt.metric_signed(m.get(base + 'operating_income.mom_abs'))} | "
            f"{fmt.metric(m.get(base + 'operating_margin_pct'))} |")
    lines += ["", "Cost of goods sold for each month is in metrics.json as "
                  "`monthly.<YYYY-MM>.cogs.total`."]
    return "\n".join(lines)


def _extremes(doc, m, fmt) -> str:
    ext = doc.get("extremes") or {}
    if not ext:
        return ""
    lines = ["## Best and worst months", "",
             "Factual only: the highest and lowest month among the months with a value. "
             "Ties go to the earliest month.", "",
             "| Measure | Highest | Month | Lowest | Month |", "| --- | --: | --- | --: | --- |"]
    for key, label in (("revenue.total", "Total revenue"),
                       ("operating_income", "Operating income")):
        entry = ext.get(key) or {}
        hi, lo = entry.get("highest") or {}, entry.get("lowest") or {}
        lines.append(f"| {label} | {fmt.metric(m.get(f'extremes.{key}.highest'))} | "
                     f"{hi.get('period', '—')} | "
                     f"{fmt.metric(m.get(f'extremes.{key}.lowest'))} | "
                     f"{lo.get('period', '—')} |")
    return "\n".join(lines)


# ---- whole-file sections ----------------------------------------------------------

def _years(doc, m, fmt) -> str:
    years = doc.get("years") or []
    if not years:
        return ""
    lines = ["## Calendar years", "",
             "A total per calendar year. A year that is not complete is labelled and "
             "covers its months present only; a year with a month missing inside it "
             "has no total.", "",
             "| Year | Coverage | Revenue | Gross profit | Gross margin | Operating "
             "expenses | Operating income | Operating margin |",
             "| --- | --- | --: | --: | --: | --: | --: | --: |"]
    for y in years:
        p = y["metric_prefix"]
        lines.append(f"| {y['year']} | {y['label']} | {fmt.metric(m.get(p + 'revenue.total'))} | "
                     f"{fmt.metric(m.get(p + 'gross_profit'))} | "
                     f"{fmt.metric(m.get(p + 'gross_margin_pct'))} | "
                     f"{fmt.metric(m.get(p + 'opex.total'))} | "
                     f"{fmt.metric(m.get(p + 'operating_income'))} | "
                     f"{fmt.metric(m.get(p + 'operating_margin_pct'))} |")
    return "\n".join(lines)


def _start_to_end(doc, m, fmt) -> str:
    ste = doc.get("start_to_end") or {}
    first, last = ste.get("first"), ste.get("last")
    if not first:
        return ""
    lines = ["## First month to last month", "",
             f"{last} against {first}. A percentage change needs a strictly positive "
             f"first-month value.", "",
             f"| Measure | {first} | {last} | Change | % change |",
             "| --- | --: | --: | --: | --: |"]
    for key in CURRENCY_HEADLINE:
        label = next(lbl for lbl, k in HEADLINE if k == key)
        lines.append(f"| {label} | {fmt.metric(m.get(f'monthly.{first}.{key}'))} | "
                     f"{fmt.metric(m.get(f'monthly.{last}.{key}'))} | "
                     f"{fmt.metric_signed(m.get(f'{key}.start_to_end_abs'))} | "
                     f"{fmt.metric_signed(m.get(f'{key}.start_to_end_pct'))} |")
    for key in ("gross_margin_pct", "operating_margin_pct"):
        label = next(lbl for lbl, k in HEADLINE if k == key)
        lines.append(f"| {label} | {fmt.metric(m.get(f'monthly.{first}.{key}'))} | "
                     f"{fmt.metric(m.get(f'monthly.{last}.{key}'))} | "
                     f"{fmt.metric(m.get(f'{key}.start_to_end_pts'))} | — |")
    return "\n".join(lines)


def _seasonality(doc) -> str:
    s = doc.get("seasonality") or {}
    if s.get("assessed"):
        return ("## Seasonality — not asserted\n\nThe file has enough months for each "
                "calendar month to "
                "appear twice. No seasonal pattern is stated here; any pattern is for the "
                "reviewer to confirm.")
    return f"## Seasonality — not asserted\n\n**Not assessed.** {s.get('reason', '')}"


# ---- shared tail --------------------------------------------------------------------

def _reconciliation(doc, fmt) -> str:
    recon = doc.get("reconciliation") or {}
    by_check = recon.get("by_check") or []
    if not by_check:
        return "## Reconciliation\n\n" + NOT_AVAILABLE_NOTE
    lines = ["## Reconciliation", "",
             f"Independently computed totals against the workbook's own totals, for every "
             f"month covered here. Tolerance {fmt_tolerance(recon)} (absolute "
             f"{doc.get('currency')}).", "",
             "| Check | Result | Periods | Max difference |", "| --- | --- | --: | --: |"]
    for c in by_check:
        lines.append(f"| {c.get('description')} | {'PASS' if c.get('passed') else '**FAIL**'} "
                     f"| {c.get('periods_checked')} | {c.get('max_abs_difference'):.10f} |")
    failures = recon.get("failures") or []
    if failures:
        lines += ["", "### Failures", "",
                  "| Check | Period | Computed | Reported | Difference |",
                  "| --- | --- | --: | --: | --: |"]
        for f in failures:
            lines.append(f"| {f.get('key')} | {f.get('period')} | "
                         f"{fmt.money(f.get('computed'))} | {fmt.money(f.get('reported'))} "
                         f"| {fmt.signed_money(f.get('difference'))} |")
        lines += ["", "**Figures that depend on the affected totals should not be relied on "
                      "until these differences are explained.**"]
    else:
        lines += ["", f"All {recon.get('checks_run')} checks passed in the months covered."]
    return "\n".join(lines)


def _checklist(doc, m) -> str:
    recon = doc.get("reconciliation") or {}
    issues = doc.get("data_quality_issues") or []
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    blocked = [x for x in doc.get("metrics", []) if x.get("validation_status") == "blocked"]
    src = doc.get("source") or {}
    cov = doc.get("coverage") or {}
    what = (f"year {doc.get('year')}" if doc.get("scope") == "year"
            else "the whole file")
    items = [f"Confirm the source file and scope are the ones you meant: "
             f"`{src.get('filename')}`, {what}."]
    if doc.get("scope") == "year" and cov.get("status") != "complete":
        items.append("Note that this year is not complete; its totals cover the months "
                     "present only and must not be read as a full year.")
    if errors:
        items.append(f"Resolve the {len(errors)} data-quality error(s); {len(blocked)} "
                     f"metric(s) are blocked until then.")
    if warnings:
        items.append(f"Read the {len(warnings)} data-quality warning(s) and decide whether "
                     f"any changes your conclusions.")
    if not recon.get("all_passed"):
        items.append(f"**Investigate the {recon.get('checks_failed')} failed reconciliation "
                     f"check(s) before using this report.**")
    items += [
        "Check the largest monthly movements against what you know about the business, "
        "and supply the context the workbook cannot hold.",
        "Sign off, or send back with the specific figures you want re-checked.",
    ]
    lines = ["## Reviewer checklist", "",
             "This report is a draft. It is not finished until a reviewer has worked "
             "through the following.", ""]
    lines += [f"- [ ] {i}" for i in items]
    return "\n".join(lines)
