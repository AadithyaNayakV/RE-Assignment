"""Template commentary. No AI, no network, always available.

This is the fallback that makes the rest of the system honest: if every model provider is
unreachable, the pipeline still produces a usable draft. It is also the control case for
evaluation — a model provider that reads worse than this one is not worth shipping.

Every sentence is assembled from values already in the metrics document and formatted by
`formatting.Formatter`. The provider does no arithmetic of its own, which is why its
output passes the guard by construction rather than by luck.
"""

from __future__ import annotations

from finance_engine.commentary.base import CommentaryProvider
from finance_engine.formatting import Formatter, na_with_reason


class DeterministicProvider(CommentaryProvider):
    provider_id = "deterministic"
    always_available = True

    def generate(self, metrics_doc: dict) -> str:
        doc = metrics_doc
        fmt = Formatter(doc.get("currency_symbol", "$"), doc.get("currency", "USD"))
        m = {x["name"]: x for x in doc.get("metrics", [])}
        period = doc.get("period_label", doc.get("period", ""))
        prior = doc.get("prior_period", "")

        blocks = [
            self._headline(doc, m, fmt, period, prior),
            self._revenue(doc, m, fmt, period, prior),
            self._costs(doc, m, fmt, period, prior),
            self._movements(doc, m, fmt, period, prior),
            self._one_offs(doc, fmt),
            self._watch_items(doc, m, fmt),
        ]
        return "\n\n".join(b for b in blocks if b).strip() + "\n"

    # ---- sections ------------------------------------------------------

    def _headline(self, doc, m, fmt, period, prior) -> str:
        rev, opex = m.get("revenue.total"), m.get("opex.total")
        gp, oi = m.get("gross_profit"), m.get("operating_income")
        gm = m.get("gross_margin_pct")
        lines = [
            "### Summary",
            "",
            f"For {period}, total revenue was **{fmt.metric(rev)}** "
            f"({fmt.metric_signed(m.get('revenue.total.mom_abs'))}, "
            f"{fmt.metric_signed(m.get('revenue.total.mom_pct'))} versus {prior}).",
            "",
            f"Gross profit was {fmt.metric(gp)} on a gross margin of {fmt.metric(gm)} "
            f"({fmt.metric(m.get('gross_margin_pct.mom_pts'))} versus {prior}). "
            f"Operating expenses were {fmt.metric(opex)} "
            f"({fmt.metric_signed(m.get('opex.total.mom_abs'))} versus {prior}), "
            f"leaving operating income of {fmt.metric(oi)}.",
        ]
        ttm = m.get("revenue.total.ttm")
        if ttm and ttm.get("value") is not None:
            lines += [
                "",
                f"On a trailing twelve-month basis revenue was {fmt.metric(ttm)} and "
                f"operating expenses were {fmt.metric(m.get('opex.total.ttm'))}.",
            ]
            window_one_offs = (doc.get("movements") or {}).get("ttm_window_one_offs") or []
            if window_one_offs:
                named = "; ".join(
                    f"{c['label']} {fmt.money(c['current'])} in {c['period']}"
                    for c in window_one_offs[:3]
                )
                lines += [
                    "",
                    f"**Those trailing-twelve-month figures are not a run rate.** The "
                    f"window contains {len(window_one_offs)} item(s) that stand far "
                    f"above the rest of it: {named}. They are included in every "
                    f"trailing-twelve-month total above. The workbook gives no reason "
                    f"for any of them.",
                ]
        else:
            window = (doc.get("ttm") or {}).get("window") or []
            span = f"{window[0]} to {window[-1]}" if window else "the required window"
            lines += [
                "",
                f"Trailing-twelve-month figures are not available: the source does not "
                f"cover {span}. No shorter window is presented in their place.",
            ]

        notes = doc.get("classification_notes") or []
        if notes:
            lines += ["", "**Classification note.** " + " ".join(notes)]
        return "\n".join(lines)

    def _revenue(self, doc, m, fmt, period, prior) -> str:
        """Prose only. The report owns the tables; commentary describes them."""
        streams = sorted(
            (x for x in doc["metrics"]
             if x["name"].startswith("revenue.stream.")
             and not x["name"].endswith((".mom_abs", ".mom_pct"))),
            key=lambda x: (x.get("value") is None, -(x.get("value") or 0)),
        )
        if not streams:
            return ""
        largest = streams[0]
        # The share is read from metrics.json, never computed here. A commentary provider
        # that does its own arithmetic is exactly what the guard exists to catch.
        share = m.get(f"{largest['name']}.share_of_revenue_pct")

        lines = ["### Revenue", ""]
        sentence = (f"{largest.get('label')} is the largest stream at "
                    f"{fmt.metric(largest)}")
        if share is not None and share.get("value") is not None:
            sentence += f", {fmt.metric(share)} of the month's revenue"
        lines.append(sentence + ".")

        movers = [
            s for s in streams
            if (m.get(f"{s['name']}.mom_abs") or {}).get("value") not in (None, 0)
        ]
        movers.sort(key=lambda s: abs(m[f"{s['name']}.mom_abs"]["value"]), reverse=True)
        if movers:
            described = "; ".join(
                f"{s.get('label')} {fmt.metric_signed(m[f'{s['name']}.mom_abs'])} "
                f"to {fmt.metric(s)}"
                for s in movers[:3]
            )
            lines += ["", f"The streams that moved most against {prior}: {described}."]

        unavailable = [
            s for s in streams
            if (m.get(f"{s['name']}.mom_pct") or {}).get("unavailable_reason")
            in ("zero_base", "negative_base")
        ]
        if unavailable:
            names = ", ".join(s.get("label", "") for s in unavailable)
            lines += ["", f"No percentage change is shown for {names} because the "
                          f"{prior} figure was zero or negative. For those lines the "
                          f"absolute change is the meaningful comparison."]
        return "\n".join(lines)

    def _costs(self, doc, m, fmt, period, prior) -> str:
        cats = sorted(
            (x for x in doc["metrics"]
             if x["name"].startswith("opex.category.")
             and not x["name"].endswith((".mom_abs", ".mom_pct", ".ttm",
                                         ".share_of_opex_pct"))),
            key=lambda x: (x.get("value") is None, -(x.get("value") or 0)),
        )
        if not cats:
            return ""
        lines = ["### Costs", ""]
        top = cats[0]
        lines.append(
            f"The largest operating expense category is {top.get('label')} at "
            f"{fmt.metric(top)} "
            f"({fmt.metric(m.get(top['name'] + '.share_of_opex_pct'))} of operating "
            f"expenses)."
        )
        movers = [
            c for c in cats
            if (m.get(f"{c['name']}.mom_abs") or {}).get("value") not in (None, 0)
        ]
        movers.sort(key=lambda c: abs(m[f"{c['name']}.mom_abs"]["value"]), reverse=True)
        if movers:
            ups = [c for c in movers if m[f"{c['name']}.mom_abs"]["value"] > 0][:2]
            downs = [c for c in movers if m[f"{c['name']}.mom_abs"]["value"] < 0][:2]
            if ups:
                lines += ["", "Categories that rose: " + "; ".join(
                    f"{c.get('label')} {fmt.metric_signed(m[f'{c['name']}.mom_abs'])}"
                    for c in ups) + "."]
            if downs:
                lines += ["", "Categories that fell: " + "; ".join(
                    f"{c.get('label')} {fmt.metric_signed(m[f'{c['name']}.mom_abs'])}"
                    for c in downs) + "."]

        groups = [
            x for x in doc["metrics"]
            if x["name"].startswith("opex.group.")
            and x["name"] != "opex.group.ungrouped"
            and not x["name"].endswith((".mom_abs", ".mom_pct", ".ttm"))
        ]
        if groups:
            named = ", ".join(f"{g.get('label')} at {fmt.metric(g)}" for g in groups)
            lines += ["", f"The workbook presents subtotal groups inside operating "
                          f"expenses: {named}. Their members are counted once, not in "
                          f"addition to the subtotal row."]
        return "\n".join(lines)

    def _movements(self, doc, m, fmt, period, prior) -> str:
        mv = doc.get("movements") or {}
        significant = mv.get("significant") or []
        thresholds = mv.get("thresholds") or {}
        bar = (f"{fmt.percent(thresholds.get('significant_mom_pct'))} or "
               f"{fmt.money(thresholds.get('significant_mom_abs'))}")
        if not significant:
            return ("### What moved\n\nNo line cleared the significance thresholds "
                    f"({bar} month-over-month) between {prior} and {period}.")
        lines = [
            "### What moved",
            "",
            f"{len(significant)} line(s) moved by at least {bar} between {prior} and "
            f"{period}. These are **confirmed figures from the workbook**; the workbook "
            f"records no reason for any of them. Largest absolute changes:",
            "",
        ]
        for c in significant[:5]:
            if c.get("sign_change"):
                qualifier = f"**{c.get('direction', 'sign change')}**"
            else:
                qualifier = (fmt.signed_percent(c["change_pct"])
                             if c.get("change_pct") is not None
                             else na_with_reason(
                                 c.get("change_pct_unavailable_reason")))
            lines.append(
                f"- **{c['label']}** {fmt.signed_money(c['change_abs'])} "
                f"({qualifier}), from {fmt.money(c['previous'])} to "
                f"{fmt.money(c['current'])}."
            )
        if len(significant) > 5:
            # Deliberately unnumbered: "len(significant) - 5" would be arithmetic done by
            # the provider, which is precisely what it is not allowed to do.
            lines += ["", "The remaining lines that cleared the thresholds are in the "
                          "movements table below."]
        return "\n".join(lines)

    def _one_offs(self, doc, fmt) -> str:
        items = (doc.get("movements") or {}).get("possible_one_offs") or []
        if not items:
            return ("### Possible one-offs\n\nNo expense line stood far enough above its "
                    "own trailing average to be flagged as a possible one-off.")
        lines = [
            "### Possible one-offs",
            "",
            "Each line below is large relative to its own recent average. That is a "
            "**magnitude observation only** — the workbook contains no information about "
            "cause, so none of these is a confirmed one-off. A reviewer should confirm or "
            "dismiss each:",
            "",
        ]
        for c in items[:5]:
            ratio = c.get("multiple_of_average")
            comparison = (
                f"({ratio:,.1f}x)" if ratio is not None
                else "(no comparable spend in the trailing window, so no multiple)"
            )
            lines.append(
                f"- **{c['label']}** at {fmt.money(c['current'])} against a trailing "
                f"average of {fmt.money(c['comparison_average'])} {comparison}."
            )
        return "\n".join(lines)

    def _watch_items(self, doc, m, fmt) -> str:
        lines = ["### What a reviewer should check"]
        bullets: list[str] = []

        errors = [i for i in doc.get("data_quality_issues", [])
                  if i.get("severity") == "error"]
        warnings = [i for i in doc.get("data_quality_issues", [])
                    if i.get("severity") == "warning"]
        recon = doc.get("reconciliation") or {}

        if errors:
            bullets.append(
                f"**{len(errors)} data-quality error(s)** blocked one or more metrics. "
                f"Affected figures are shown as n/a, never estimated."
            )
        if warnings:
            bullets.append(
                f"{len(warnings)} data-quality warning(s) were raised. Figures still "
                f"compute but the warnings are listed in full in the report."
            )
        if recon.get("all_passed"):
            bullets.append(
                f"All {recon.get('checks_run')} reconciliation checks passed against the "
                f"workbook's own totals, so the section structure was read as intended."
            )
        else:
            bullets.append(
                f"**{recon.get('checks_failed')} of {recon.get('checks_run')} "
                f"reconciliation checks failed.** Figures that depend on the affected "
                f"totals should not be relied on until the difference is explained."
            )

        seasonality = doc.get("seasonality") or {}
        if not seasonality.get("assessed"):
            bullets.append(seasonality.get("reason", ""))

        blocked = [x for x in doc.get("metrics", [])
                   if x.get("validation_status") == "blocked"]
        if blocked:
            bullets.append(
                f"{len(blocked)} metric(s) are blocked and reported as n/a rather than "
                f"filled in with an estimate."
            )

        bullets.append(
            "The engine reads the workbook only. It can state what changed; it cannot "
            "state why. Every causal explanation in the final report must come from the "
            "reviewer."
        )
        lines += ["", *(f"- {b}" for b in bullets if b)]
        return "\n".join(lines)
