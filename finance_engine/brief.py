"""A compact, Python-formatted view of metrics.json for a commentary writer.

`metrics.json` is the contract, but for a real client it is large (ABC's June 2026 file is
~860 KB, mostly per-line traces). A writer — the Claude skill, or a person — needs the
figures, not the traces. This module prints them, already formatted, each next to the
metric name it came from.

Two rules, the same as the rest of the engine:

* **No arithmetic.** Every figure printed here is a value already in the document. The
  brief only selects and formats; it never derives a number the document does not hold.
* **Python formats every figure.** A writer quotes these strings as they are, so the
  precision, sign and scale of a number are never the writer's decision.
"""

from __future__ import annotations

from finance_engine.formatting import (Formatter, na_with_reason, not_provided_note,
                                       one_off_multiple_reason, significance_rule)

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

BALANCE_SHEET = [
    ("Cash and cash equivalents", "balance_sheet.cash"),
    ("Accounts receivable", "balance_sheet.accounts_receivable"),
    ("Deferred revenue", "balance_sheet.deferred_revenue"),
    ("Total assets", "balance_sheet.assets"),
    ("Total liabilities", "balance_sheet.liabilities"),
    ("Total equity", "balance_sheet.equity"),
    ("Total liabilities and equity", "balance_sheet.liabilities_and_equity"),
]

_SUFFIXES = (".mom_abs", ".mom_pct", ".mom_pts", ".yoy_abs", ".yoy_pct", ".ttm",
             ".share_of_revenue_pct", ".share_of_opex_pct")


def build_brief(doc: dict, metric_filter: str | None = None) -> str:
    fmt = Formatter(doc.get("currency_symbol", "$"), doc.get("currency", "USD"))
    m = {x["name"]: x for x in doc.get("metrics", [])}
    if metric_filter:
        return _filtered(doc, m, fmt, metric_filter)
    if doc.get("scope") in ("year", "all"):
        # Month documents have no `scope` key, so the month brief below is unchanged.
        from finance_engine.span_brief import build_span_brief
        return build_span_brief(doc)
    parts = [
        _header(doc),
        _caveats(doc),
        _status(doc),
        _reconciliation_failures(doc, fmt),
        _issues(doc),
        _headline(doc, m, fmt),
        _streams(doc, m, fmt),
        _categories(doc, m, fmt),
        _groups(doc, m, fmt),
        _ttm(doc, m, fmt),
        _movements(doc, fmt),
        _one_offs(doc, fmt),
        _window_one_offs(doc, fmt),
        _balance_sheet(doc, m, fmt),
        _notes(doc),
    ]
    return "\n\n".join(p for p in parts if p).rstrip() + "\n"


# ---- sections ----------------------------------------------------------

def _header(doc) -> str:
    src = doc.get("source") or {}
    return "\n".join([
        f"# Brief: {doc.get('company_name')} — {doc.get('period_label')} "
        f"(`{doc.get('period')}`)",
        "",
        f"- client_id: `{doc.get('client_id')}`",
        f"- prior period (every MoM figure compares against it): "
        f"`{doc.get('prior_period')}`",
        f"- currency: {doc.get('currency')}",
        f"- source: `{src.get('path')}` (sha256 `{str(src.get('sha256'))[:16]}...`), "
        f"{src.get('period_count')} months available",
        "",
        "Every figure below is a value in metrics.json, formatted by Python. Quote figures "
        "exactly as printed. A figure that is not printed here or in "
        "`brief --metric <name>` is not in metrics.json: do not derive it.",
    ])


def _caveats(doc) -> str:
    """Comparisons that need care. First, so a writer cannot miss them; only when any."""
    block = doc.get("comparison_caveats")
    if not block:
        return ""
    period = doc.get("period")
    lines = ["## Comparison caveats: state each one in the summary", "",
             "Each change below rests on a comparison month that does not reconcile. "
             "State it in the summary with the measure and the month, and say it should "
             "not be read as a real movement. Give no cause.", ""]
    for item in block.get("items") or []:
        lines.append(f"- `{item['measure']}` ({item['label']}) vs {item['against']} "
                     f"({'prior month' if item['comparison'] == 'mom' else 'year ago'}): "
                     f"{item['text'].split(': ', 1)[-1]}")
    lines.append("")
    lines.append(f"- Reporting month {period}: "
                 + ("reconciles." if block.get("period_reconciles") else
                    f"does NOT reconcile ({', '.join(block.get('period_failed_checks') or [])} "
                    f"failed)."))
    return "\n".join(lines)


def _status(doc) -> str:
    recon = doc.get("reconciliation") or {}
    ttm = doc.get("ttm") or {}
    window = ttm.get("window") or ["?"]
    issues = doc.get("data_quality_issues") or []
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    blocked = [x for x in doc.get("metrics", [])
               if x.get("validation_status") == "blocked"]
    seasonality = doc.get("seasonality") or {}
    lines = [
        "## Status",
        "",
        # Both counts are stored in metrics.json, so a writer can copy this line and pass
        # the number check. The passed count ("56/60 passed") is derived, not stored.
        f"- Reconciliation: {'PASS' if recon.get('all_passed') else 'FAIL'} — "
        f"{recon.get('checks_failed', 0)} of {recon.get('checks_run', 0)} checks failed"
        f"{not_provided_note(doc)}",
        f"- Trailing twelve months: "
        f"{'complete' if ttm.get('complete') else 'INCOMPLETE — every TTM figure is n/a'}"
        f" (window {window[0]}..{window[-1]})",
        f"- Data-quality errors: {len(errors)}; warnings: {len(warnings)}; "
        f"blocked metrics: {len(blocked)}",
        f"- Seasonality: "
        + ("assessed" if seasonality.get("assessed")
           else f"NOT assessed. {seasonality.get('reason', '')}".strip()),
    ]
    return "\n".join(lines)


def _reconciliation_failures(doc, fmt) -> str:
    """Every failed reconciliation check, one row per check and month.

    Without this a writer knows only that some checks failed, and either says it cannot
    tell which (true, but useless to the reviewer) or reads the raw metrics.json, which
    the skill forbids. Every figure here is in metrics.json and passes the guard.
    """
    failures = (doc.get("reconciliation") or {}).get("failures") or []
    if not failures:
        return ""
    lines = ["## Reconciliation failures", "",
             "Name every one of these in the report, with its month. Computed is the "
             "engine's figure for the check; reported is the workbook's own row.", "",
             "| check | description | period | computed | reported | difference |",
             "| --- | --- | --- | --: | --: | --: |"]
    for f in failures:
        lines.append(
            f"| `{f.get('key')}` | {f.get('description', '')} | {f.get('period')} | "
            f"{fmt.money(f.get('computed'))} | {fmt.money(f.get('reported'))} | "
            f"{fmt.signed_money(f.get('difference'))} |"
        )
    return "\n".join(lines)


def _issues(doc) -> str:
    issues = doc.get("data_quality_issues") or []
    if not issues:
        return "## Data-quality issues\n\nNone raised."
    lines = ["## Data-quality issues", "",
             "Show every one of these in the report, with its code."]
    for i in issues:
        lines.append(f"- [{i.get('severity')}] `{i.get('code')}` — {i.get('message')}")
    return "\n".join(lines)


def _headline(doc, m, fmt) -> str:
    prior = doc.get("prior_period")
    lines = ["## Headline", "",
             f"| Measure | metric | {doc.get('period_label')} | {prior} | change "
             f"| % / pts | YoY | YoY % | TTM |",
             "| --- | --- | --: | --: | --: | --: | --: | --: | --: |"]
    for label, key in HEADLINE:
        if key not in m:
            continue
        change = m.get(f"{key}.mom_pts") or m.get(f"{key}.mom_pct")
        lines.append(
            f"| {label} | `{key}` | {fmt.metric(m[key])} | "
            f"{_cell(fmt, m.get(key + '.prior'))} | "
            f"{_cell_signed(fmt, m.get(key + '.mom_abs'))} | "
            f"{_cell_signed(fmt, change)} | "
            f"{_cell_signed(fmt, m.get(key + '.yoy_abs'))} | "
            f"{_cell_signed(fmt, m.get(key + '.yoy_pct'))} | "
            f"{_cell(fmt, m.get(key + '.ttm'))} |"
        )
    lines += ["", "`—` means the engine does not produce that comparison for this "
                  "measure; `n/a (reason)` means it was withheld for the stated reason."]
    return "\n".join(lines)


def _streams(doc, m, fmt) -> str:
    streams = _family(doc, "revenue.stream.")
    if not streams:
        return "## Revenue streams\n\nNone in metrics.json."
    lines = ["## Revenue streams", "",
             "| Stream | metric | value | share of revenue | MoM | MoM % |",
             "| --- | --- | --: | --: | --: | --: |"]
    for s in streams:
        n = s["name"]
        lines.append(
            f"| {s.get('label')} | `{n}` | {fmt.metric(s)} | "
            f"{_cell(fmt, m.get(n + '.share_of_revenue_pct'))} | "
            f"{_cell_signed(fmt, m.get(n + '.mom_abs'))} | "
            f"{_cell_signed(fmt, m.get(n + '.mom_pct'))} |"
        )
    return "\n".join(lines)


def _categories(doc, m, fmt) -> str:
    cats = _family(doc, "opex.category.")
    if not cats:
        return "## Operating expenses by category\n\nNone in metrics.json."
    lines = ["## Operating expenses by category", "",
             "Categories come from the client config and sum to total operating "
             "expenses.", "",
             "| Category | metric | value | share of opex | MoM | MoM % | TTM |",
             "| --- | --- | --: | --: | --: | --: | --: |"]
    for c in cats:
        n = c["name"]
        lines.append(
            f"| {c.get('label')} | `{n}` | {fmt.metric(c)} | "
            f"{_cell(fmt, m.get(n + '.share_of_opex_pct'))} | "
            f"{_cell_signed(fmt, m.get(n + '.mom_abs'))} | "
            f"{_cell_signed(fmt, m.get(n + '.mom_pct'))} | "
            f"{_cell(fmt, m.get(n + '.ttm'))} |"
        )
    return "\n".join(lines)


def _groups(doc, m, fmt) -> str:
    groups = _family(doc, "opex.group.")
    if not groups:
        return ""
    lines = ["## Operating expense subtotal groups (as the workbook presents them)", "",
             "| Group | metric | value | MoM | MoM % |",
             "| --- | --- | --: | --: | --: |"]
    for g in groups:
        n = g["name"]
        lines.append(
            f"| {g.get('label')} | `{n}` | {fmt.metric(g)} | "
            f"{_cell_signed(fmt, m.get(n + '.mom_abs'))} | "
            f"{_cell_signed(fmt, m.get(n + '.mom_pct'))} |"
        )
    return "\n".join(lines)


def _ttm(doc, m, fmt) -> str:
    ttm = doc.get("ttm") or {}
    window = ttm.get("window") or ["?"]
    if not ttm.get("complete"):
        return "\n".join([
            "## Trailing twelve months", "",
            f"**NOT AVAILABLE.** A complete {ttm.get('months')}-month window "
            f"({window[0]}..{window[-1]}) is not covered by the source. Every TTM "
            f"metric is n/a. Do not present any shorter window, sum or average as a TTM "
            f"figure.",
        ])
    lines = ["## Trailing twelve months", "",
             f"Window {window[0]}..{window[-1]} ({ttm.get('months')} months, complete).",
             "", "| Measure | metric | TTM |", "| --- | --- | --: |"]
    carried: list[str] = []
    for label, key in HEADLINE:
        metric = m.get(f"{key}.ttm")
        if metric is None:
            continue
        lines.append(f"| {label} | `{key}.ttm` | {fmt.metric(metric)} |")
        for w in metric.get("warnings", []):
            if w not in carried:
                carried.append(w)
    if carried:
        lines.append("")
        lines += [f"- Warning carried by these TTM figures: {w}" for w in carried]
    return "\n".join(lines)


def _movements(doc, fmt) -> str:
    mv = doc.get("movements") or {}
    significant = mv.get("significant") or []
    rule = significance_rule(mv.get("thresholds") or {}, fmt.symbol)
    head = (f"## Significant movements vs {doc.get('prior_period')}\n\n"
            f"A line qualifies on {rule}. {len(significant)} of "
            f"{mv.get('candidates_considered', 0)} changed lines qualified.")
    if not significant:
        return head + "\n\nNone qualified."
    lines = [head, "",
             "| Line | section | category | previous | current | change | % change "
             "| direction | metric |",
             "| --- | --- | --- | --: | --: | --: | --: | --- | --- |"]
    for c in significant:
        pct = (fmt.signed_percent(c["change_pct"]) if c.get("change_pct") is not None
               else na_with_reason(c.get("change_pct_unavailable_reason")))
        lines.append(
            f"| {c['label']} | {c.get('scope', '').replace('_', ' ')} | "
            f"{c.get('category') or '—'} | {fmt.money(c.get('previous'))} | "
            f"{fmt.money(c.get('current'))} | {fmt.signed_money(c.get('change_abs'))} | "
            f"{pct} | {c.get('direction', '')} | `{c.get('metric_name', '')}` |"
        )
    return "\n".join(lines)


def _one_offs(doc, fmt) -> str:
    items = (doc.get("movements") or {}).get("possible_one_offs") or []
    if not items:
        return (f"## Possible one-offs in {doc.get('period_label')}\n\n"
                f"None flagged in the reporting month itself.")
    lines = [f"## Possible one-offs in {doc.get('period_label')}", "",
             "Flagged on size against the line's own trailing average. Magnitude only — "
             "never a confirmed one-off, never a cause.", "",
             "| Line | category | this month | comparison average | multiple | basis |",
             "| --- | --- | --: | --: | --: | --- |"]
    for c in items:
        lines.append(
            f"| {c['label']} | {c.get('category') or '—'} | {fmt.money(c['current'])} | "
            f"{fmt.money(c.get('comparison_average'))} | {_multiple(c)} | "
            f"{c.get('comparison_basis', '')} |"
        )
    return "\n".join(lines)


def _window_one_offs(doc, fmt) -> str:
    mv = doc.get("movements") or {}
    items = mv.get("ttm_window_one_offs") or []
    if not items:
        return ""
    summary = mv.get("ttm_window_one_offs_summary") or {}
    lines = ["## Large items inside the TTM window", "",
             f"These sit inside the TTM window, so the TTM "
             f"**{summary.get('affected_figures_phrase', '')}** figures include them and "
             f"are not a run rate. Other TTM figures are unaffected.", "",
             "| Line | month | amount | average of the other months | multiple |",
             "| --- | --- | --: | --: | --: |"]
    for c in items:
        lines.append(
            f"| {c['label']} | {c['period']} | {fmt.money(c['current'])} | "
            f"{fmt.money(c.get('comparison_average'))} | {_multiple(c)} |"
        )
    return "\n".join(lines)


def _balance_sheet(doc, m, fmt) -> str:
    present = [(label, key) for label, key in BALANCE_SHEET if key in m]
    if not present:
        return "## Balance sheet\n\nNot in this source."
    lines = ["## Balance sheet", "",
             f"| Line | metric | {doc.get('period_label')} | MoM | MoM % |",
             "| --- | --- | --: | --: | --: |"]
    for label, key in present:
        lines.append(
            f"| {label} | `{key}` | {fmt.metric(m[key])} | "
            f"{_cell_signed(fmt, m.get(key + '.mom_abs'))} | "
            f"{_cell_signed(fmt, m.get(key + '.mom_pct'))} |"
        )
    checks = [c for c in (doc.get("reconciliation") or {}).get("by_check", [])
              if c.get("key") == "balance_sheet_balances"]
    if checks:
        lines += ["", f"- Assets = Liabilities + Equity check: "
                      f"{'PASS' if checks[0].get('passed') else 'FAIL'} across "
                      f"{checks[0].get('periods_checked')} months."]
    return "\n".join(lines)


def _notes(doc) -> str:
    lines = ["## Classification notes and limitations", ""]
    for n in doc.get("classification_notes") or []:
        lines.append(f"- Classification: {n}")
    for n in doc.get("limitations") or []:
        lines.append(f"- Limitation: {n}")
    seasonality = doc.get("seasonality") or {}
    if not seasonality.get("assessed") and seasonality.get("reason"):
        lines.append(f"- Limitation: {seasonality['reason']}")
    return "\n".join(lines)


def _filtered(doc, m, fmt, needle: str) -> str:
    hits = [x for x in doc.get("metrics", []) if needle in x["name"]]
    lines = [f"# Metrics matching `{needle}` — {doc.get('client_id')} {doc.get('period')}",
             "", "| metric | label | value | status | warnings |",
             "| --- | --- | --: | --- | --- |"]
    for x in hits:
        warnings = "; ".join(x.get("warnings") or []) or "—"
        lines.append(f"| `{x['name']}` | {x.get('label', '')} | {fmt.metric(x)} | "
                     f"{x.get('validation_status')} | {warnings} |")
    if not hits:
        lines.append("| (no metric matches) | | | | |")
    return "\n".join(lines) + "\n"


# ---- helpers -----------------------------------------------------------

def _family(doc, prefix: str) -> list[dict]:
    rows = [x for x in doc.get("metrics", [])
            if x["name"].startswith(prefix) and not x["name"].endswith(_SUFFIXES)]
    return sorted(rows, key=lambda x: (x.get("value") is None, -(x.get("value") or 0)))


def _cell(fmt, metric) -> str:
    return "—" if metric is None else fmt.metric(metric)


def _cell_signed(fmt, metric) -> str:
    return "—" if metric is None else fmt.metric_signed(metric)


def _multiple(entry: dict) -> str:
    ratio = entry.get("multiple_of_average")
    if ratio is None:
        why = one_off_multiple_reason(entry.get("multiple_unavailable_reason"))
        return f"n/a ({why})" if why else "n/a"
    return f"{ratio:,.1f}x"
