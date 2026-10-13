"""Template commentary for the year and whole-file scopes.

The same rules as `deterministic.py`: every sentence is assembled from values already in
the metrics document and formatted by `formatting.Formatter`, so the text passes the
guard by construction. The templates pick and order figures (the largest, the highest);
they never add, subtract or average one.

Wording: state what changed, never why. Direction words come from the sign of the figure
they describe, and the amount after them is unsigned ("rose $13,000.00 (+27.1%)").
"""

from __future__ import annotations

from finance_engine.formatting import Formatter

CURRENCY_HEADLINE = ["revenue.total", "cogs.total", "gross_profit", "opex.total",
                     "operating_income"]


def span_commentary(doc: dict) -> str:
    fmt = Formatter(doc.get("currency_symbol", "$"), doc.get("currency", "USD"))
    m = {x["name"]: x for x in doc.get("metrics", [])}
    if doc.get("scope") == "year":
        blocks = [_year_summary(doc, m, fmt), _year_on_year(doc, m, fmt),
                  _quarters(doc, m, fmt), _months(doc, m, fmt), _watch_items(doc)]
    else:
        blocks = [_all_summary(doc, m, fmt), _calendar_years(doc, m, fmt),
                  _series(doc, m, fmt), _watch_items(doc)]
    return "\n\n".join(b for b in blocks if b).strip() + "\n"


# ---- helpers -----------------------------------------------------------------

def _value(m, name):
    return (m.get(name) or {}).get("value")


def _moved(fmt, m, abs_name, pct_name=None) -> str:
    """'rose $1.00 (+2.0%)', 'fell $1.00 (-2.0%)' or 'was unchanged', from the figures."""
    change = _value(m, abs_name)
    if change is None:
        return f"is not available ({fmt.metric(m.get(abs_name))})"
    if change == 0:
        return "was unchanged"
    word = "rose" if change > 0 else "fell"
    text = f"{word} {fmt.money(abs(change))}"
    pct = m.get(pct_name) if pct_name else None
    if pct is not None:
        text += f" ({fmt.metric_signed(pct)})"
    return text


def _span(months) -> str:
    months = list(months or [])
    if not months:
        return "no months"
    return months[0] if len(months) == 1 else f"{months[0]}..{months[-1]}"


def _moved_pts(fmt, m, name) -> str:
    delta = _value(m, name)
    if delta is None:
        return f"is not available ({fmt.metric(m.get(name))})"
    if delta == 0:
        return "was unchanged"
    return f"{'rose' if delta > 0 else 'fell'} {abs(delta):,.1f} pts"


# ---- year --------------------------------------------------------------------

def _year_summary(doc, m, fmt) -> str:
    cov = doc.get("coverage") or {}
    year = doc.get("year")
    lines = ["### Summary", ""]
    if _value(m, "revenue.total") is None:
        gaps = cov.get("gaps_inside_span") or []
        lines.append(
            f"The {year} totals are not available: {', '.join(gaps) or 'a month'} "
            f"{'is' if len(gaps) <= 1 else 'are'} missing inside the year, and a total is "
            f"never summed over the months that remain. The monthly figures and any "
            f"complete quarter are unaffected.")
        return "\n".join(lines)
    partial = cov.get("status") != "complete"
    lead = (f"For {year}, {cov.get('label')}, " if partial
            else f"For {year}, a full year, ")
    lines.append(
        f"{lead}total revenue was **{fmt.metric(m.get('revenue.total'))}**. Cost of goods "
        f"sold was {fmt.metric(m.get('cogs.total'))}, leaving gross profit of "
        f"{fmt.metric(m.get('gross_profit'))} and a gross margin of "
        f"{fmt.metric(m.get('gross_margin_pct'))}. Operating expenses were "
        f"{fmt.metric(m.get('opex.total'))}, leaving operating income of "
        f"{fmt.metric(m.get('operating_income'))} (an operating margin of "
        f"{fmt.metric(m.get('operating_margin_pct'))}).")
    if partial:
        lines += ["", f"These totals cover {fmt.metric(m.get('coverage.months_present'))} "
                      f"of {fmt.metric(m.get('coverage.months_in_year'))} months only. They "
                      f"are not scaled, annualised or extrapolated to a full year."]
    notes = doc.get("classification_notes") or []
    if notes:
        lines += ["", "**Classification note.** " + " ".join(notes)]
    return "\n".join(lines)


def _year_on_year(doc, m, fmt) -> str:
    yoy = doc.get("yoy") or {}
    lines = ["### Year on year", ""]
    if not yoy.get("comparable"):
        lines.append("Not compared: the same months are not in both years in this file, "
                     "and no other comparison is put in its place.")
        return "\n".join(lines)
    lines.append(
        f"Comparing {_span(yoy.get('months'))} with the same months one year earlier "
        f"({_span(yoy.get('prior_year_months'))}), total revenue {_moved(fmt, m, 'revenue.total.yoy_abs', 'revenue.total.yoy_pct')}, "
        f"operating expenses {_moved(fmt, m, 'opex.total.yoy_abs', 'opex.total.yoy_pct')} "
        f"and operating income "
        f"{_moved(fmt, m, 'operating_income.yoy_abs', 'operating_income.yoy_pct')}. "
        f"Gross margin {_moved_pts(fmt, m, 'gross_margin_pct.yoy_pts')}.")
    return "\n".join(lines)


def _quarters(doc, m, fmt) -> str:
    quarters = doc.get("quarters") or []
    if not quarters:
        return ""
    complete = [q for q in quarters if q["complete"]]
    incomplete = [q for q in quarters if not q["complete"]]
    lines = ["### Quarters", ""]
    if complete:
        lines.append("Revenue by complete quarter: " + "; ".join(
            f"{q['quarter']} {fmt.metric(m.get('revenue.total.' + q['quarter'].lower()))}"
            for q in complete) + ".")
    if incomplete:
        if complete:
            lines.append("")
        lines.append("Not complete, so shown as n/a: " + "; ".join(
            f"{q['quarter']} (lacks {', '.join(q['missing'])})" for q in incomplete) + ".")
    return "\n".join(lines)


def _months(doc, m, fmt) -> str:
    months = (doc.get("coverage") or {}).get("months_present") or []
    lines = ["### Months", ""]
    lines += _extremes_sentences(doc, m, fmt)
    moves = [(p, m.get(f"monthly.{p}.revenue.total.mom_abs")) for p in months]
    moves = [(p, x) for p, x in moves if x and x.get("value")]
    if moves:
        p, x = max(moves, key=lambda pm: abs(pm[1]["value"]))
        moved = _moved(fmt, m, f"monthly.{p}.revenue.total.mom_abs",
                       f"monthly.{p}.revenue.total.mom_pct")
        lines += ["", f"The largest month-over-month change in revenue was in {p}, against "
                      f"the month before: revenue {moved}, to "
                      f"{fmt.metric(m.get(f'monthly.{p}.revenue.total'))}."]
    return "\n".join(lines)


def _extremes_sentences(doc, m, fmt) -> list[str]:
    ext = doc.get("extremes") or {}
    out = []
    for key, what in (("revenue.total", "monthly revenue"),
                      ("operating_income", "monthly operating income")):
        entry = ext.get(key) or {}
        hi, lo = entry.get("highest"), entry.get("lowest")
        if not hi or not lo:
            continue
        if hi["period"] == lo["period"]:
            out.append(f"Only one month has a value for {what}: "
                       f"{fmt.metric(m.get(f'extremes.{key}.highest'))} in {hi['period']}.")
            continue
        out.append(f"The highest {what} was {fmt.metric(m.get(f'extremes.{key}.highest'))} "
                   f"in {hi['period']}, and the lowest "
                   f"{fmt.metric(m.get(f'extremes.{key}.lowest'))} in {lo['period']}.")
    return out


# ---- whole file ----------------------------------------------------------------

def _all_summary(doc, m, fmt) -> str:
    cov = doc.get("coverage") or {}
    ste = doc.get("start_to_end") or {}
    lines = ["### Summary", "",
             f"The file covers {cov.get('first')} to {cov.get('last')}, "
             f"{fmt.metric(m.get('coverage.months_present'))} months"
             + (f", with {', '.join(cov.get('gaps'))} missing inside it"
                if cov.get("gaps") else ", with no month missing inside it") + "."]
    first, last = ste.get("first"), ste.get("last")
    if first and last and first != last:
        lines += ["", f"From the first month ({first}) to the last ({last}), total revenue "
                      f"{_moved(fmt, m, 'revenue.total.start_to_end_abs', 'revenue.total.start_to_end_pct')} "
                      f"to {fmt.metric(m.get(f'monthly.{last}.revenue.total'))}, and "
                      f"operating income "
                      f"{_moved(fmt, m, 'operating_income.start_to_end_abs', 'operating_income.start_to_end_pct')} "
                      f"to {fmt.metric(m.get(f'monthly.{last}.operating_income'))}. Gross "
                      f"margin {_moved_pts(fmt, m, 'gross_margin_pct.start_to_end_pts')}."]
    notes = doc.get("classification_notes") or []
    if notes:
        lines += ["", "**Classification note.** " + " ".join(notes)]
    return "\n".join(lines)


def _calendar_years(doc, m, fmt) -> str:
    years = doc.get("years") or []
    if not years:
        return ""
    lines = ["### Calendar years", ""]
    for y in years:
        p = y["metric_prefix"]
        if _value(m, p + "revenue.total") is None:
            lines.append(f"- {y['year']} ({y['label']}): no total, since a month is missing "
                         f"inside the year.")
            continue
        tail = "" if y["status"] == "complete" else " Months present only; not annualised."
        lines.append(f"- {y['year']} ({y['label']}): total revenue "
                     f"{fmt.metric(m.get(p + 'revenue.total'))}, operating income "
                     f"{fmt.metric(m.get(p + 'operating_income'))}.{tail}")
    return "\n".join(lines)


def _series(doc, m, fmt) -> str:
    lines = ["### Monthly series", ""]
    lines += _extremes_sentences(doc, m, fmt)
    lines += ["", "Every month is listed in the report's monthly series table, with its "
                  "change on the month before."]
    return "\n".join(lines)


# ---- shared ----------------------------------------------------------------------

def _watch_items(doc) -> str:
    issues = doc.get("data_quality_issues") or []
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    recon = doc.get("reconciliation") or {}
    blocked = [x for x in doc.get("metrics", []) if x.get("validation_status") == "blocked"]
    bullets = []
    if errors:
        bullets.append(f"**{len(errors)} data-quality error(s)** blocked one or more "
                       f"metrics. Affected figures are shown as n/a, never estimated.")
    if warnings:
        bullets.append(f"{len(warnings)} data-quality warning(s) were raised. Figures "
                       f"still compute but the warnings are listed in full in the report.")
    if recon.get("all_passed"):
        bullets.append(f"All {recon.get('checks_run')} reconciliation checks passed in the "
                       f"months covered.")
    else:
        bullets.append(f"**{recon.get('checks_failed')} of {recon.get('checks_run')} "
                       f"reconciliation checks failed.** Figures that depend on the "
                       f"affected totals should not be relied on until the difference is "
                       f"explained.")
    if blocked:
        bullets.append(f"{len(blocked)} metric(s) are blocked and reported as n/a rather "
                       f"than filled in with an estimate.")
    seasonality = doc.get("seasonality") or {}
    if not seasonality.get("assessed") and seasonality.get("reason"):
        bullets.append(seasonality["reason"])
    bullets.append("The engine reads the workbook only. It can state what changed; it "
                   "cannot state why. Every explanation in the final report must come from "
                   "the reviewer.")
    return "\n".join(["### What a reviewer should check", ""] + [f"- {b}" for b in bullets])
