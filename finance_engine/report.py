"""Builds report.md from metrics.json plus commentary.

The report is a **draft for a human reviewer**, and says so. Rules it follows:

* Python formats every number. Nothing here recalculates.
* A section with no supporting data says so rather than being filled with something
  weaker. No metric is invented to complete a heading.
* Warnings and blocked metrics appear near the top, not buried in an appendix.
* Every table cell traces to a metric in metrics.json.
"""

from __future__ import annotations

from finance_engine.formatting import (Formatter, na_with_reason,
                                       one_off_multiple_reason,
                                       significance_rule)

NOT_AVAILABLE_NOTE = "_No supporting data for this section._"


def build_report(metrics_doc: dict, commentary: str = "",
                 provider_id: str = "deterministic",
                 guard_result: dict | None = None) -> str:
    doc = metrics_doc
    fmt = Formatter(doc.get("currency_symbol", "$"), doc.get("currency", "USD"))
    m = {x["name"]: x for x in doc.get("metrics", [])}

    parts = [
        _header(doc),
        _status(doc, guard_result),
        _source(doc),
        _commentary(commentary, provider_id, guard_result),
        _revenue(doc, m, fmt),
        _margins(doc, m, fmt),
        _expenses(doc, m, fmt),
        _mom_summary(doc, m, fmt),
        _ttm(doc, m, fmt),
        _ttm_one_offs(doc, fmt),
        _movements(doc, fmt),
        _one_offs(doc, fmt),
        _balance_sheet(doc, m, fmt),
        _reconciliation(doc, fmt),
        _data_quality(doc),
        _limitations(doc),
        _checklist(doc),
    ]
    return "\n\n".join(p for p in parts if p).rstrip() + "\n"


# ---- sections ----------------------------------------------------------

def _header(doc) -> str:
    return "\n".join([
        f"# {doc.get('company_name')} — monthly financial trend analysis",
        "",
        f"**Period:** {doc.get('period_label')} (`{doc.get('period')}`)  ",
        f"**Client:** `{doc.get('client_id')}`  ",
        f"**Currency:** {doc.get('currency')}  ",
        f"**Generated:** {doc.get('generated_at_utc')}  ",
        f"**metrics.json schema:** v{doc.get('schema_version')}",
        "",
        "> **This is a draft for a human reviewer, not a reviewed financial statement.**",
        "> Every figure is calculated by Python from the source workbook and reconciled",
        "> against the workbook's own totals. The commentary describes those figures and",
        "> never explains them — the workbook contains no information about causes.",
    ])


def _status(doc, guard_result) -> str:
    recon = doc.get("reconciliation") or {}
    issues = doc.get("data_quality_issues") or []
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    blocked = [x for x in doc.get("metrics", [])
               if x.get("validation_status") == "blocked"]

    rows = [
        ("Reconciliation",
         "PASS" if recon.get("all_passed") else "**FAIL**",
         f"{recon.get('checks_run', 0) - recon.get('checks_failed', 0)}"
         f"/{recon.get('checks_run', 0)} checks passed across all periods "
         f"(tolerance {fmt_tolerance(recon)})"),
        ("Data-quality errors",
         "none" if not errors else f"**{len(errors)}**",
         "; ".join(i.get("code", "") for i in errors) or "—"),
        ("Data-quality warnings",
         "none" if not warnings else str(len(warnings)),
         "; ".join(i.get("code", "") for i in warnings) or "—"),
        ("Blocked metrics",
         "none" if not blocked else str(len(blocked)),
         "shown as n/a, never estimated" if blocked else "—"),
        ("Trailing twelve months",
         "complete" if (doc.get("ttm") or {}).get("complete") else "**incomplete**",
         f"window {(doc.get('ttm') or {}).get('window', ['?'])[0]}"
         f"..{(doc.get('ttm') or {}).get('window', ['?'])[-1]}"),
    ]
    if guard_result is not None:
        rows.append((
            "Commentary number check",
            "PASS" if guard_result.get("passed") else "**REJECTED**",
            f"{guard_result.get('numbers_supported', 0)}"
            f"/{guard_result.get('numbers_checked', 0)} numbers traced to metrics.json",
        ))

    lines = ["## Status at a glance", "", "| Check | Result | Detail |",
             "| --- | --- | --- |"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    return "\n".join(lines)


def fmt_tolerance(recon: dict) -> str:
    tol = recon.get("tolerance_abs")
    return f"{tol}" if tol is not None else "—"


def _source(doc) -> str:
    src = doc.get("source") or {}
    periods = src.get("periods_available") or []
    lines = [
        "## Source",
        "",
        f"- **File:** `{src.get('path')}`",
        f"- **SHA-256:** `{src.get('sha256')}`",
        f"- **Config:** `{src.get('config_path')}`",
        f"- **Sheets read:** {', '.join(src.get('sheets') or []) or '—'}",
        f"- **Months available:** {src.get('period_count')} "
        f"({periods[0]} to {periods[-1]})" if periods else "- **Months available:** none",
    ]
    headings = src.get("sheet_headings") or {}
    for sheet, titles in headings.items():
        if titles:
            lines.append(f"- **{sheet} heading:** {' / '.join(titles)}")
    lines.append("")
    lines.append("The source workbook is opened read-only and never modified.")
    return "\n".join(lines)


def _commentary(commentary: str, provider_id: str, guard_result) -> str:
    if not commentary.strip():
        return ""
    lines = ["## Commentary", "",
             f"_Written by the `{provider_id}` commentary provider from metrics.json._"]
    if guard_result is not None:
        if guard_result.get("passed"):
            lines.append(
                f"_All {guard_result.get('numbers_checked', 0)} numbers in this section "
                f"were checked against metrics.json and traced successfully._"
            )
        else:
            lines.append(
                f"_**Number check rejected this commentary:** "
                f"{guard_result.get('numbers_unsupported')} figure(s) could not be traced "
                f"to metrics.json. Treat the text below with suspicion._"
            )
    lines += ["", commentary.strip()]
    return "\n".join(lines)


def _revenue(doc, m, fmt) -> str:
    period, prior = doc.get("period_label"), doc.get("prior_period")
    streams = sorted(
        (x for x in doc["metrics"]
         if x["name"].startswith("revenue.stream.")
         and not x["name"].endswith((".mom_abs", ".mom_pct", ".share_of_revenue_pct"))),
        key=lambda x: (x.get("value") is None, -(x.get("value") or 0)),
    )
    if not streams:
        return "## Revenue\n\n" + NOT_AVAILABLE_NOTE
    lines = ["## Revenue", "",
             f"| Stream | {period} | Share | vs {prior} | % change |",
             "| --- | --: | --: | --: | --: |"]
    for s in streams:
        lines.append(
            f"| {s.get('label')} | {fmt.metric(s)} | "
            f"{fmt.metric(m.get(s['name'] + '.share_of_revenue_pct'))} | "
            f"{fmt.metric_signed(m.get(s['name'] + '.mom_abs'))} | "
            f"{fmt.metric_signed(m.get(s['name'] + '.mom_pct'))} |"
        )
    total = m.get("revenue.total")
    lines.append(
        f"| **Total revenue** | **{fmt.metric(total)}** | — | "
        f"**{fmt.metric_signed(m.get('revenue.total.mom_abs'))}** | "
        f"**{fmt.metric_signed(m.get('revenue.total.mom_pct'))}** |"
    )
    lines += ["",
              f"- Trailing twelve months: {fmt.metric(m.get('revenue.total.ttm'))}",
              f"- Year on year: {fmt.metric_signed(m.get('revenue.total.yoy_abs'))} "
              f"({fmt.metric_signed(m.get('revenue.total.yoy_pct'))})"]
    if total and total.get("reported_value") is not None:
        lines.append(
            f"- The workbook's own Total Income row reads "
            f"{fmt.money(total['reported_value'])}; the figure above is independently "
            f"summed from the line items and reconciled against it."
        )
    return "\n".join(lines)


def _margins(doc, m, fmt) -> str:
    prior = doc.get("prior_period")
    rows = [
        ("Total revenue", "revenue.total", "revenue.total.mom_abs", None),
        ("Cost of goods sold", "cogs.total", "cogs.total.mom_abs", None),
        ("Gross profit", "gross_profit", "gross_profit.mom_abs", None),
        ("Gross margin", "gross_margin_pct", None, "gross_margin_pct.mom_pts"),
        ("Total operating expenses", "opex.total", "opex.total.mom_abs", None),
        ("Operating income", "operating_income", "operating_income.mom_abs", None),
        ("Operating margin", "operating_margin_pct", None,
         "operating_margin_pct.mom_pts"),
        ("Net income (as reported)", "net_income.reported", None, None),
    ]
    lines = ["## Margins and profitability", "",
             f"| Measure | {doc.get('period_label')} | vs {prior} |",
             "| --- | --: | --: |"]
    for label, value_key, abs_key, pts_key in rows:
        metric = m.get(value_key)
        if metric is None:
            continue
        if pts_key:
            change = fmt.metric(m.get(pts_key))
        elif abs_key:
            change = fmt.metric_signed(m.get(abs_key))
        else:
            change = "—"
        lines.append(f"| {label} | {fmt.metric(metric)} | {change} |")

    notes = doc.get("classification_notes") or []
    if notes:
        lines += ["", "**Classification note.** " + " ".join(notes)]
    return "\n".join(lines)


def _expenses(doc, m, fmt) -> str:
    period, prior = doc.get("period_label"), doc.get("prior_period")
    cats = sorted(
        (x for x in doc["metrics"]
         if x["name"].startswith("opex.category.")
         and not x["name"].endswith((".mom_abs", ".mom_pct", ".ttm",
                                     ".share_of_opex_pct"))),
        key=lambda x: (x.get("value") is None, -(x.get("value") or 0)),
    )
    if not cats:
        return "## Operating expenses\n\n" + NOT_AVAILABLE_NOTE

    lines = ["## Operating expenses", "", "### By category", "",
             f"| Category | {period} | Share | vs {prior} | % change | TTM |",
             "| --- | --: | --: | --: | --: | --: |"]
    for c in cats:
        lines.append(
            f"| {c.get('label')} | {fmt.metric(c)} | "
            f"{fmt.metric(m.get(c['name'] + '.share_of_opex_pct'))} | "
            f"{fmt.metric_signed(m.get(c['name'] + '.mom_abs'))} | "
            f"{fmt.metric_signed(m.get(c['name'] + '.mom_pct'))} | "
            f"{fmt.metric(m.get(c['name'] + '.ttm'))} |"
        )
    total = m.get("opex.total")
    lines.append(
        f"| **Total** | **{fmt.metric(total)}** | — | "
        f"**{fmt.metric_signed(m.get('opex.total.mom_abs'))}** | "
        f"**{fmt.metric_signed(m.get('opex.total.mom_pct'))}** | "
        f"**{fmt.metric(m.get('opex.total.ttm'))}** |"
    )
    lines += ["", "Categories are assigned by the client config, not by line name alone. "
                  "They partition every operating expense line exactly once, so they sum "
                  "to the total above."]

    groups = sorted(
        (x for x in doc["metrics"]
         if x["name"].startswith("opex.group.")
         and not x["name"].endswith((".mom_abs", ".mom_pct", ".ttm"))),
        key=lambda x: (x.get("value") is None, -(x.get("value") or 0)),
    )
    if groups:
        lines += ["", "### By subtotal group", "",
                  "As the workbook presents them. Group members are counted once; the "
                  "workbook's subtotal rows are not added again.", "",
                  f"| Group | {period} | vs {prior} | % change |",
                  "| --- | --: | --: | --: |"]
        for g in groups:
            lines.append(
                f"| {g.get('label')} | {fmt.metric(g)} | "
                f"{fmt.metric_signed(m.get(g['name'] + '.mom_abs'))} | "
                f"{fmt.metric_signed(m.get(g['name'] + '.mom_pct'))} |"
            )
    return "\n".join(lines)


def _mom_summary(doc, m, fmt) -> str:
    prior, period = doc.get("prior_period"), doc.get("period_label")
    rows = [
        ("Total revenue", "revenue.total"),
        ("Cost of goods sold", "cogs.total"),
        ("Gross profit", "gross_profit"),
        ("Total operating expenses", "opex.total"),
        ("Operating income", "operating_income"),
    ]
    lines = ["## Month over month", "",
             f"| Measure | {prior} | {period} | Change | % change |",
             "| --- | --: | --: | --: | --: |"]
    for label, key in rows:
        current = m.get(key)
        change = m.get(f"{key}.mom_abs")
        if current is None:
            continue
        # The prior figure is the engine's `<key>.prior` metric. It used to be rebuilt
        # here as current - change, which put five untraceable numbers in every report.
        lines.append(
            f"| {label} | {fmt.metric(m.get(f'{key}.prior'))} | {fmt.metric(current)} | "
            f"{fmt.metric_signed(change)} | "
            f"{fmt.metric_signed(m.get(f'{key}.mom_pct'))} |"
        )
    lines += ["", "A percentage change is shown only where the prior month was strictly "
                  "positive. A zero or negative base is reported as n/a with the reason, "
                  "never as an infinite or sign-flipped percentage."]
    return "\n".join(lines)


def _ttm(doc, m, fmt) -> str:
    ttm = doc.get("ttm") or {}
    window = ttm.get("window") or []
    span = f"{window[0]} to {window[-1]}" if window else "—"
    if not ttm.get("complete"):
        return "\n".join([
            "## Trailing twelve months", "",
            f"**Not available.** A complete {ttm.get('months')}-month window ending "
            f"{doc.get('period')} requires {span}, which the source does not cover. "
            f"No shorter window is presented in its place.",
        ])
    rows = [
        ("Total revenue", "revenue.total.ttm"),
        ("Cost of goods sold", "cogs.total.ttm"),
        ("Gross profit", "gross_profit.ttm"),
        ("Gross margin", "gross_margin_pct.ttm"),
        ("Total operating expenses", "opex.total.ttm"),
        ("Operating income", "operating_income.ttm"),
    ]
    lines = ["## Trailing twelve months", "",
             f"Window: {span} ({ttm.get('months')} months, complete).", "",
             "| Measure | TTM |", "| --- | --: |"]
    for label, key in rows:
        metric = m.get(key)
        if metric is not None:
            lines.append(f"| {label} | {fmt.metric(metric)} |")

    # A warning carried by the TTM metrics belongs next to the numbers, not in an
    # appendix the reader reaches after already drawing a conclusion.
    carried: list[str] = []
    for _, key in rows:
        for warning in (m.get(key) or {}).get("warnings", []):
            if warning not in carried:
                carried.append(warning)
    if carried:
        lines.append("")
        lines += [f"> **Note.** {w}" for w in carried]
    return "\n".join(lines)


def _movements(doc, fmt) -> str:
    mv = doc.get("movements") or {}
    significant = mv.get("significant") or []
    thresholds = mv.get("thresholds") or {}
    prior, period = doc.get("prior_period"), doc.get("period_label")
    rule = significance_rule(thresholds, fmt.symbol)
    if not significant:
        return ("## Significant movements\n\nNo line met the significance test between "
                f"{prior} and {period}. A line qualifies on {rule}.")
    lines = [
        "## Significant movements", "",
        f"Lines that met the significance test between {prior} and {period}, largest "
        f"absolute change first — {len(significant)} of "
        f"{mv.get('candidates_considered', 0)} lines that changed at all. A line "
        f"qualifies on {rule}.", "",
        f"| Line | Section | Category | {prior} | {period} | Change | % change | "
        f"Direction |",
        "| --- | --- | --- | --: | --: | --: | --: | --- |",
    ]
    for c in significant:
        pct = (fmt.signed_percent(c["change_pct"]) if c.get("change_pct") is not None
               else na_with_reason(c.get("change_pct_unavailable_reason")))
        lines.append(
            f"| {c['label']} | {c['scope'].replace('_', ' ')} | {c.get('category') or '—'} "
            f"| {fmt.money(c['previous'])} | {fmt.money(c['current'])} | "
            f"{fmt.signed_money(c['change_abs'])} | {pct} | "
            f"{c.get('direction', '')} |"
        )
    crossed = [c for c in significant if c.get("sign_change")]
    lines += ["", "These are confirmed figures from the workbook. The workbook records no "
                  "reason for any movement, so no cause is stated here."]
    if crossed:
        lines += [
            "",
            f"{len(crossed)} line(s) **crossed zero** between {prior} and {period}: "
            + "; ".join(f"{c['label']} ({fmt.money(c['previous'])} to "
                        f"{fmt.money(c['current'])})" for c in crossed)
            + ". No percentage is shown for these: a swing between a charge and a credit "
              "is not a percentage reduction, and reporting one would invite the reader "
              "to treat a reversal as a cost saving.",
        ]
    return "\n".join(lines)


def _ttm_one_offs(doc, fmt) -> str:
    """Items inside the TTM window large enough to distort every TTM figure."""
    items = (doc.get("movements") or {}).get("ttm_window_one_offs") or []
    if not items:
        return ""
    summary = (doc.get("movements") or {}).get("ttm_window_one_offs_summary") or {}
    ttm = doc.get("ttm") or {}
    window = ttm.get("window") or []
    span = f"{window[0]} to {window[-1]}" if window else "the window"
    figures = summary.get("affected_figures_phrase", "")
    lines = [
        "## Items inside the trailing-twelve-month window", "",
        f"These {len(items)} item(s) sit inside {span} and stand far above the rest of "
        f"the window. The trailing-twelve-month **{figures}** figures include them and "
        f"are therefore **not a run rate**; the other trailing-twelve-month figures are "
        f"unaffected. As always this is a magnitude observation: the workbook records no "
        f"reason for any of them.", "",
        "| Line | Month | Amount | Average of the other months | Difference | "
        "Multiple |",
        "| --- | --- | --: | --: | --: | --: |",
    ]
    for c in items:
        lines.append(
            f"| {c['label']} | {c['period']} | {fmt.money(c['current'])} | "
            f"{fmt.money(c['comparison_average'])} | "
            f"{fmt.signed_money(c['excess_over_average'])} | "
            f"{_multiple(c)} |"
        )
    return "\n".join(lines)


def _one_offs(doc, fmt) -> str:
    movements = doc.get("movements") or {}
    items = movements.get("possible_one_offs") or []
    window_items = movements.get("ttm_window_one_offs") or []
    summary = movements.get("ttm_window_one_offs_summary") or {}
    if not items:
        lines = [
            "## Possible one-offs", "",
            f"No line in {doc.get('period_label')} itself stood far enough above its own "
            f"trailing average to be flagged.",
        ]
        if window_items:
            # Saying "none found" without this would contradict the window table above.
            lines += [
                "",
                f"This is not the same as saying there are none in the period under "
                f"review: the wider trailing-twelve-month window "
                f"({summary.get('window_start')} to {summary.get('window_end')}) contains "
                f"{len(window_items)}, listed under *Items inside the "
                f"trailing-twelve-month window* above. None of them falls in "
                f"{doc.get('period_label')}, which is why this month's scan is empty.",
            ]
        return "\n".join(lines)
    lines = [
        "## Possible one-offs", "",
        "Each line is large relative to its own trailing average. This is a **magnitude "
        "observation only** — the workbook contains no information about cause, so none "
        "of these is a confirmed one-off.", "",
        "| Line | Category | This month | Trailing average | Multiple |",
        "| --- | --- | --: | --: | --: |",
    ]
    for c in items:
        lines.append(
            f"| {c['label']} | {c.get('category') or '—'} | {fmt.money(c['current'])} | "
            f"{fmt.money(c['comparison_average'])} | {_multiple(c)} |"
        )
    lines += ["", "A line with no comparable spend in the trailing window has no "
                  "meaningful multiple; its size is the whole of the observation."]
    return "\n".join(lines)


def _multiple(entry: dict) -> str:
    """"3.4x", or n/a naming why the average gives no multiple.

    The generic reason text is written for a month-over-month base ("prior period was
    zero"), which is the wrong sentence when the denominator is an average over several
    months.
    """
    ratio = entry.get("multiple_of_average")
    if ratio is None:
        why = one_off_multiple_reason(entry.get("multiple_unavailable_reason"))
        return f"n/a ({why})" if why else "n/a"
    return f"{ratio:,.1f}x"


def _balance_sheet(doc, m, fmt) -> str:
    keys = [
        ("Cash and cash equivalents", "balance_sheet.cash"),
        ("Accounts receivable", "balance_sheet.accounts_receivable"),
        ("Deferred revenue", "balance_sheet.deferred_revenue"),
        ("Total assets", "balance_sheet.assets"),
        ("Total liabilities", "balance_sheet.liabilities"),
        ("Total equity", "balance_sheet.equity"),
    ]
    present = [(label, key) for label, key in keys if m.get(key) is not None]
    if not present:
        return "## Balance sheet\n\n" + NOT_AVAILABLE_NOTE
    prior = doc.get("prior_period")
    lines = ["## Balance sheet", "",
             f"| Line | {doc.get('period_label')} | vs {prior} | % change |",
             "| --- | --: | --: | --: |"]
    for label, key in present:
        lines.append(
            f"| {label} | {fmt.metric(m.get(key))} | "
            f"{fmt.metric_signed(m.get(key + '.mom_abs'))} | "
            f"{fmt.metric_signed(m.get(key + '.mom_pct'))} |"
        )
    return "\n".join(lines)


def _reconciliation(doc, fmt) -> str:
    recon = doc.get("reconciliation") or {}
    by_check = recon.get("by_check") or []
    if not by_check:
        return "## Reconciliation\n\n" + NOT_AVAILABLE_NOTE
    lines = [
        "## Reconciliation", "",
        f"Independently computed totals against the workbook's own formula totals, for "
        f"every month in the source. Tolerance {fmt_tolerance(recon)} "
        f"(absolute {doc.get('currency')}), which exists only to absorb the float noise "
        f"in the workbook's cached results.", "",
        "| Check | Result | Periods | Max difference |",
        "| --- | --- | --: | --: |",
    ]
    for c in by_check:
        lines.append(
            f"| {c.get('description')} | {'PASS' if c.get('passed') else '**FAIL**'} | "
            f"{c.get('periods_checked')} | {c.get('max_abs_difference'):.10f} |"
        )
    failures = recon.get("failures") or []
    if failures:
        lines += ["", "### Failures", "",
                  "| Check | Period | Computed | Reported | Difference |",
                  "| --- | --- | --: | --: | --: |"]
        for f in failures:
            lines.append(
                f"| {f.get('key')} | {f.get('period')} | "
                f"{fmt.money(f.get('computed'))} | {fmt.money(f.get('reported'))} | "
                f"{fmt.signed_money(f.get('difference'))} |"
            )
        lines += ["", "**Figures that depend on the affected totals should not be relied "
                      "on until these differences are explained.**"]
    else:
        lines += ["", f"All {recon.get('checks_run')} checks passed. This is evidence that "
                      f"the configured section structure matches the workbook, within the "
                      f"checks listed above — not proof that every derived figure is "
                      f"correct."]
    return "\n".join(lines)


def _data_quality(doc) -> str:
    issues = doc.get("data_quality_issues") or []
    if not issues:
        return "## Data quality\n\nNo data-quality issues were raised."
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    lines = ["## Data quality", ""]
    if errors:
        lines += [f"### Errors ({len(errors)})", "",
                  "Each error blocks the metrics it affects. Blocked metrics are reported "
                  "as n/a and are never estimated.", ""]
        for i in errors:
            lines.append(f"- **`{i.get('code')}`** — {i.get('message')}")
        lines.append("")
    if warnings:
        lines += [f"### Warnings ({len(warnings)})", "",
                  "Affected metrics still compute. The warning travels with them.", ""]
        for i in warnings:
            lines.append(f"- **`{i.get('code')}`** — {i.get('message')}")
    return "\n".join(lines)


def _limitations(doc) -> str:
    limits = doc.get("limitations") or []
    seasonality = doc.get("seasonality") or {}
    lines = ["## Limitations", ""]
    lines += [f"- {l}" for l in limits]
    if not seasonality.get("assessed") and seasonality.get("reason"):
        lines.append(f"- {seasonality['reason']}")
    return "\n".join(lines)


def _checklist(doc) -> str:
    recon = doc.get("reconciliation") or {}
    issues = doc.get("data_quality_issues") or []
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    blocked = [x for x in doc.get("metrics", [])
               if x.get("validation_status") == "blocked"]
    one_offs = (doc.get("movements") or {}).get("possible_one_offs") or []
    significant = (doc.get("movements") or {}).get("significant") or []

    items = [
        f"Confirm the source file and period are the ones you meant: "
        f"`{(doc.get('source') or {}).get('filename') or (doc.get('source') or {}).get('path')}`, "
        f"{doc.get('period_label')}.",
        f"Check the {len(significant)} significant movement(s) against what you know "
        f"about the month, and supply the causes — the engine cannot.",
    ]
    if one_offs:
        items.append(
            f"Confirm or dismiss the {len(one_offs)} possible one-off(s). Each is flagged "
            f"on magnitude alone."
        )
    if errors:
        items.append(
            f"Resolve the {len(errors)} data-quality error(s); {len(blocked)} metric(s) "
            f"are blocked until then."
        )
    if warnings:
        items.append(f"Read the {len(warnings)} data-quality warning(s) and decide whether "
                     f"any changes your conclusions.")
    if not recon.get("all_passed"):
        items.append(
            f"**Investigate the {recon.get('checks_failed')} failed reconciliation "
            f"check(s) before using this report.**"
        )
    if not (doc.get("ttm") or {}).get("complete"):
        items.append("Note that no trailing-twelve-month figures are available for this "
                     "period.")
    items += [
        "Confirm the classification note on operating income matches how this client's "
        "results are normally presented.",
        "Add any context the workbook cannot contain: contracts signed or lost, headcount "
        "changes, timing differences, accruals and reclassifications.",
        "Sign off, or send back with the specific figures you want re-checked.",
    ]
    lines = ["## Reviewer checklist", "",
             "This report is a draft. It is not finished until a reviewer has worked "
             "through the following.", ""]
    lines += [f"- [ ] {i}" for i in items]
    return "\n".join(lines)
