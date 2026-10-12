"""Level 2: deterministic checks on a written report.

The checks know nothing about who wrote the report. The same function grades a report
Claude wrote through the skill and one the deterministic template wrote, so the template
is the control: a check the template fails is a broken check, not a bad model.

Generic checks (every report):
  guard, required_sections, issues_shown_early, draft_label, provider_label,
  reviewer_checklist, causal_language_hedged, causal_words_outside_questions,
  cause_vocabulary_hedged,
  seasonality_not_asserted, one_offs_hedged, ttm_incomplete_*, impossible_values,
  client_isolation, no_credentials, reconciliation_failure_shown,
  reconciliation_failures_named
Case checks (from case.yaml `skill:`): required_figures, required_flags,
  forbidden_claims, required_sections_extra.

Text checks work per *unit*: a table row, a list item, or a sentence. A causal or
seasonal claim and its hedge must sit in the same unit.
"""

from __future__ import annotations

import re
from pathlib import Path

from finance_engine.formatting import Formatter
from finance_engine.guard import NumberGuard

# ---- vocabulary ---------------------------------------------------------------

HEDGE = re.compile(
    r"(possib|potential|\bmay\b|\bmight\b|\bcould\b|perhaps|\bconfirm\b|confirmation|"
    r"to be confirmed|unconfirmed|whether|\bif\b|unknown|not known|no reason|records? no|"
    r"does not record|doesn't record|cannot|can't|not state|\?)", re.I)

CAUSAL = re.compile(
    r"\b(due to|driven by|caused by|owing to|attributable to|as a result of|"
    r"resulted from|thanks to|on the back of|because of|led to|reflecting)\b", re.I)

# Causal words that may appear ONLY under "Questions for the reviewer", hedged or not.
# Elsewhere the report says where a change sits, never why (SKILL.md step 5).
# "reason"/"reasons" is matched as a whole word, so "the whole reason" and "one reason"
# fail too. The one exception is "no reason(s)": "the workbook records no reason for any
# movement" denies a cause rather than stating one, and the deterministic template says
# it in every report. Without the exception the control fails six cases.
CAUSAL_WORDS = re.compile(r"\b(because|due to|caused|driven by)\b|(?<!\bno )\breasons?\b",
                          re.I)

# Business events a writer might reach for. None of them is ever in the workbook.
CAUSE_WORDS = re.compile(
    r"\b(lawsuits?|litigation|legal dispute|court case|new hires?|hiring|layoffs?|"
    r"redundanc\w*|headcount (growth|increase|reduction|cuts?)|price (increase|rise|cut)s?|"
    r"pricing change|churn(ed)?|lost (a |the )?(customer|client|contract)|"
    r"(new|signed|won) (a )?(customer|client|contract|deal)s?|marketing campaign|"
    r"ad campaign|product launch|holiday season|christmas|black friday|promotional|"
    r"inflation|supply chain|market conditions|economic (downturn|slowdown)|recession|"
    r"competitors?|acquisition|merger|restructur\w*|office move|relocation|"
    r"peak season|weather|pandemic|covid)\b", re.I)

# Naming what a figure *is* states its cause: "Bad Debts swung to a credit: a reversal".
# The workbook cannot know that. Allowed only with a hedge: "may be a reversal".
_CAUSE_LABELS = (r"(reversal|recovery|refund|write[- ]?back|mis-?posting|"
                 r"reclassification|timing difference|one[- ]off|one[- ]time item)")
CAUSE_LABEL = re.compile(
    rf"\b(is|was|are|were|represents?|reflects?|being|as)\s+(an?\s+|the\s+)?{_CAUSE_LABELS}\b"
    rf"|[:—–]\s*(an?\s+)?{_CAUSE_LABELS}\b", re.I)

# House style: the direction lives in the word, the amount is unsigned.
# "fell $12,452.80 (-6.0%)", never "fell -$12,452.80".
SIGNED_AFTER_DIRECTION = re.compile(
    r"\b(fell|decreased|declined|dropped|down|narrowed|rose|increased|grew|up|widened|"
    r"climbed)\s+(by\s+)?[*_]*[+\-−]\s?[$€£₹]?\d", re.I)

FIGURE = re.compile(r"[+\-−]?[$€£₹]\s?\d[\d,]*(\.\d+)?|[+\-−]?\d[\d,]*(\.\d+)?\s?(%|pts\b)")
FIGURE_LABEL = re.compile(r"\b(change|level)\b[^$€£₹\d]{0,25}$", re.I)

SEASONAL = re.compile(r"\bseason(al|ality|ally)?\b|peak season|holiday", re.I)
SEASONAL_OK = re.compile(
    r"(not (been )?(assessed|asserted|confirmed|established)|\b(no|not|never|none)\b|"
    r"cannot|can't|"
    r"\bcould\b|possib|\bmay\b|\bmight\b|confirm|unconfirmed|insufficient|not enough|"
    r"too (few|short)|\b24\b|two (full )?years|\b18 months|only \d+ months|without|"
    r"\?)", re.I)

ONE_OFF = re.compile(r"\bone[- ]offs?\b|\bone[- ]time\b|non[- ]recurring", re.I)
ONE_OFF_OK = re.compile(
    r"(possib|potential|\bmay\b|\bmight\b|\bcould\b|flag|candidate|confirm|dismiss|"
    r"magnitude|on size|size alone|whether|\bif\b|\bno\b|\bnone\b|nothing|not a confirmed|\?)",
    re.I)

TTM_TERM = re.compile(r"\bTTM\b|trailing[- ]twelve|trailing 12|last twelve months|\bLTM\b|"
                      r"annuali[sz]ed|run[- ]rate", re.I)
CURRENCY_AMOUNT = re.compile(r"[$€£₹]\s?\d")
TTM_NA = re.compile(r"(not available|unavailable|incomplete|\bn/a\b|cannot be|isn't|"
                    r"is not|no trailing|not presented|not shown|not calculated)", re.I)

IMPOSSIBLE = re.compile(r"#DIV/0|∞|\binfinit[ey]\b|\bNaN\b|\binf%", re.I)
NEGATION = re.compile(r"\b(never|not|no)\b", re.I)
CREDENTIALS = re.compile(
    r"(client_secret|refresh_token|access_token|QBO_CLIENT_(ID|SECRET)\s*=\s*\S|"
    r"ANTHROPIC_API_KEY\s*=\s*\S|\bBearer\s+[A-Za-z0-9\-_.]{20,}|eyJ[A-Za-z0-9_-]{20,}|"
    r"\bsk-[A-Za-z0-9]{20,})")

SECTIONS = {
    "data_quality": r"data quality",
    "revenue": r"^revenue\b",
    "margins": r"^margins?\b",
    "operating_expenses": r"^operating expenses",
    "month_over_month": r"^month[- ]over[- ]month",
    "ttm": r"^trailing twelve months",
    "significant_movements": r"^significant movements",
    "one_offs": r"possible one-offs?",
    "limitations": r"limitations",
    "reviewer_checklist": r"^reviewer checklist",
    "balance_sheet": r"^balance sheet",
}
GENERIC_SECTIONS = [k for k in SECTIONS if k != "balance_sheet"]

PROVIDER_LABEL = {
    "skill": r"Claude Code|monthly-financial-analysis",
    "deterministic": r"`deterministic` commentary provider|deterministic",
}


# ---- text units ------------------------------------------------------------------

def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def units(text: str) -> list[str]:
    """Table rows and headings whole; prose and list items split into sentences.

    Hard-wrapped lines are joined first. A writer who wraps at 90 columns puts "No
    movement is" on one line and "described as seasonal." on the next, and checking
    the lines separately separates the claim from its negation.
    """
    blocks: list[str] = []
    current: list[str] = []

    def flush():
        if current:
            blocks.append(" ".join(current))
            current.clear()

    for raw in strip_comments(text).splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"\|?[\s:|-]+\|?", line):
            flush()
        elif line.startswith(("|", "#")):
            flush()
            blocks.append(line)
        elif re.match(r"([-*+]|\d{1,3}\.)\s", line) or line.startswith(">"):
            flush()
            current.append(line)
        else:
            current.append(line)
    flush()

    out = []
    for block in blocks:
        if block.startswith(("|", "#")):
            out.append(block)
        else:
            out += [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z*_`(\[])", block) if s]
    return out


def headings(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(1).strip())
            for m in re.finditer(r"(?m)^#{1,3}\s+(.+)$", strip_comments(text))]


# ---- the checks ------------------------------------------------------------------

def check_report(text: str, doc: dict, case: dict, provider: str,
                 other_clients: list[tuple[str, str]] | None = None) -> list[dict]:
    """All Level 2 checks. Returns [{id, passed, detail}]."""
    results: list[dict] = []
    skill_spec = case.get("skill") or {}

    def add(check_id: str, passed: bool, detail: str = "") -> None:
        results.append({"id": check_id, "passed": bool(passed), "detail": detail})

    body = strip_comments(text)
    all_units = units(body)
    labels = _labels(doc)

    # Grounding: every number traces to metrics.json.
    g = NumberGuard(doc).check(body)
    add("guard", g.passed, f"{len(g.supported)}/{g.checked} numbers traced"
        + ("" if g.passed else "; " + "; ".join(
            f"line {f.line}: {f.text!r}" for f in g.unsupported[:8])))

    # Completeness: sections, in the right order for data quality.
    heads = headings(body)
    wanted = GENERIC_SECTIONS + list(skill_spec.get("required_sections_extra", []))
    for key in wanted:
        found = any(re.search(SECTIONS[key], h, re.I) for _, h in heads)
        add(f"section:{key}", found, "present" if found else
            f"no heading matching {SECTIONS[key]!r}")

    revenue_pos = next((pos for pos, h in heads if re.search(SECTIONS["revenue"], h, re.I)),
                       len(body))
    codes = [i["code"] for i in doc.get("data_quality_issues", [])]
    for code in codes:
        pos = body.find(code)
        add(f"issue_shown_early:{code}", 0 <= pos < revenue_pos,
            "shown before the Revenue section" if 0 <= pos < revenue_pos else
            ("missing from the report" if pos < 0 else "only after the Revenue section"))

    head = "\n".join(body.splitlines()[:25])
    add("draft_label", re.search(r"\bdraft\b", head, re.I) is not None,
        "the first 25 lines must say this is a draft")
    expected_label = skill_spec.get("provider_label", provider)
    add("provider_label", re.search(PROVIDER_LABEL[expected_label], body) is not None,
        f"must say it was written by {expected_label}")

    checklist = re.split(r"(?im)^#{1,3}\s+reviewer checklist\s*$", body)
    items = len(re.findall(r"(?m)^\s*[-*] \[ \]", checklist[-1])) if len(checklist) > 1 else 0
    add("reviewer_checklist", items >= 3, f"{items} unchecked item(s) under the heading")

    # Unsupported claims.
    bad = [u for u in all_units if CAUSAL.search(u) and not HEDGE.search(u)]
    add("causal_language_hedged", not bad, _show(bad))
    bad = [u for u in units(_outside_section(body, r"questions for the reviewer"))
           if CAUSAL_WORDS.search(_without(u, labels))]
    add("causal_words_outside_questions", not bad,
        'causes belong only in "Questions for the reviewer": ' + _show(bad) if bad else "")
    bad = [u for u in all_units
           if CAUSE_WORDS.search(_without(u, labels)) and not HEDGE.search(u)]
    add("cause_vocabulary_hedged", not bad, _show(bad))
    if not (doc.get("seasonality") or {}).get("assessed"):
        bad = [u for u in all_units
               if SEASONAL.search(_without(u, labels)) and not SEASONAL_OK.search(u)]
        add("seasonality_not_asserted", not bad, _show(bad))
    bad = [u for u in all_units if ONE_OFF.search(u) and not ONE_OFF_OK.search(u)]
    add("one_offs_hedged", not bad, _show(bad))
    bad = [u for u in all_units
           if CAUSE_LABEL.search(_without(u, labels)) and not HEDGE.search(u)]
    add("cause_label_hedged", not bad,
        "say what a line may be, not what it is: " + _show(bad) if bad else "")
    bad = [u for u in all_units if SIGNED_AFTER_DIRECTION.search(u)]
    add("direction_amounts_unsigned", not bad,
        'write "fell $12,452.80", not "fell -$12,452.80": ' + _show(bad) if bad else "")
    questions = _section(body, r"questions for the reviewer")
    if questions is not None:
        unlabelled, last_end = [], -1
        for m in FIGURE.finditer(questions):
            before = questions[max(0, m.start() - 40):m.start()]
            # "(-6.3%)" straight after a labelled "-$11,142.74" shares its label.
            follows_labelled = (last_end >= 0 and
                                questions[last_end:m.start()].strip(" *_") == "(")
            if FIGURE_LABEL.search(before) or follows_labelled:
                last_end = m.end()
            else:
                unlabelled.append(m.group(0))
                last_end = -1
        add("reviewer_questions_label_figures", not unlabelled,
            "every figure in the reviewer questions needs 'change' or 'level' before it: "
            + ", ".join(unlabelled[:6]) if unlabelled else "")
    if not (doc.get("ttm") or {}).get("complete"):
        bad = [u for u in all_units
               if TTM_TERM.search(u) and CURRENCY_AMOUNT.search(u) and not TTM_NA.search(u)]
        add("ttm_incomplete_no_figures", not bad, _show(bad))
        stated = any(TTM_TERM.search(u) and TTM_NA.search(u) for u in all_units)
        add("ttm_incomplete_stated", stated, "must say TTM is not available")
    # "never as an infinite percentage" is the rule being stated, not a violation.
    bad = [u for u in all_units
           if IMPOSSIBLE.search(u) and not NEGATION.search(u)]
    add("impossible_values", not bad, _show(bad))
    if not (doc.get("reconciliation") or {}).get("all_passed", True):
        shown = re.search(r"reconciliation[^\n]{0,200}\b(fail|FAIL)", body, re.I)
        add("reconciliation_failure_shown", shown is not None,
            "a failed reconciliation must be stated")
        failed_periods = sorted({f["period"] for f in
                                 (doc.get("reconciliation") or {}).get("failures", [])
                                 if f.get("period")})
        if failed_periods:
            unnamed = [p for p in failed_periods if not _period_named(body, p)]
            add("reconciliation_failures_named", not unnamed,
                "every month with a failed reconciliation check must be named; missing: "
                + ", ".join(unnamed) if unnamed else "")

    # Scope and safety.
    leaks = [name for cid, name in (other_clients or [])
             if re.search(rf"\b{re.escape(cid)}\b", body)
             or (name and name in body)]
    add("client_isolation", not leaks, f"mentions other clients: {leaks}")
    add("no_credentials", CREDENTIALS.search(body) is None, "credential-like text found")

    # Case-specific: numbers, flags, forbidden claims.
    fmt = Formatter(doc.get("currency_symbol", "$"), doc.get("currency", "USD"))
    m = {x["name"]: x for x in doc.get("metrics", [])}
    for name in skill_spec.get("required_figures", []):
        forms = renderings(m.get(name), fmt)
        hit = next((f for f in forms if f in body), None)
        add(f"figure:{name}", hit is not None,
            f"found {hit!r}" if hit else f"none of {forms} in the report")
    for flag in skill_spec.get("required_flags", []):
        found = re.search(flag["pattern"], body, re.I | re.M) is not None
        add(f"flag:{flag['id']}", found, flag["description"])
    for claim in skill_spec.get("forbidden_claims", []):
        unless = re.compile(claim["unless"], re.I) if claim.get("unless") else None
        bad = [u for u in all_units if re.search(claim["pattern"], u, re.I)
               and not (unless and unless.search(u))]
        add(f"forbidden:{claim['id']}", not bad, claim["description"] + " " + _show(bad))
    return results


def renderings(metric: dict | None, fmt: Formatter) -> list[str]:
    """How a figure may legitimately appear: the brief's own format, sign dropped."""
    if metric is None or metric.get("value") is None:
        return ["<metric missing or n/a>"]
    v, unit = abs(metric["value"]), metric.get("unit")
    if unit == "percent":
        return [fmt.percent(v)]
    if unit == "percentage_points":
        return [f"{v:,.1f} pts", f"{v:,.1f} percentage points"]
    return [fmt.money(v)]


def other_clients(config_dir: Path, client_id: str) -> list[tuple[str, str]]:
    import yaml
    out = []
    for p in sorted(Path(config_dir).glob("*.yaml")):
        if p.stem == client_id:
            continue
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        out.append((p.stem, str(raw.get("company_name", ""))))
    return out


def _section(text: str, heading: str) -> str | None:
    """The body of the `##` section whose heading matches, or None if absent."""
    m = re.search(rf"(?im)^##\s+{heading}\s*$", text)
    if not m:
        return None
    nxt = re.search(r"(?m)^##\s", text[m.end():])
    return text[m.end(): m.end() + nxt.start()] if nxt else text[m.end():]


def _period_named(text: str, key: str) -> bool:
    """`2025-08`, `Aug 2025` or `August 2025` appears in the text."""
    import calendar
    year, month = int(key[:4]), int(key[5:7])
    forms = [re.escape(key),
             rf"{calendar.month_abbr[month]}\.? {year}",
             rf"{calendar.month_name[month]} {year}"]
    return re.search(rf"\b({'|'.join(forms)})\b", text, re.I) is not None


def _outside_section(text: str, heading: str) -> str:
    """`text` minus the section whose heading (any level) matches, up to the next
    heading of the same or a higher level. Unchanged if there is no such section."""
    m = re.search(rf"(?im)^(#{{1,6}})\s+{heading}\s*$", text)
    if not m:
        return text
    level = len(m.group(1))
    nxt = re.search(rf"(?m)^#{{1,{level}}}\s", text[m.end():])
    end = m.end() + nxt.start() if nxt else len(text)
    return text[:m.start()] + text[end:]


def _labels(doc: dict) -> list[str]:
    """Text the writer quotes rather than claims: line labels and source/config wording.

    A line called "Legal Settlement" is a name, not a claimed cause, and a workbook
    titled "Scenario: pronounced Nov/Dec seasonal peak" is the source speaking, not
    the writer asserting seasonality.
    """
    found = {x.get("label") for x in doc.get("metrics", []) if x.get("label")}
    mv = doc.get("movements") or {}
    for key in ("significant", "possible_one_offs", "ttm_window_one_offs"):
        found |= {e.get("label") for e in mv.get(key, []) if e.get("label")}
    for titles in ((doc.get("source") or {}).get("sheet_headings") or {}).values():
        found |= {str(t) for t in titles or []}
    found |= {str(n) for n in doc.get("classification_notes") or []}
    found |= {str(i.get("message", "")) for i in doc.get("data_quality_issues") or []}
    # Units are sentences, so quoted text must be removable sentence by sentence too.
    for text in list(found):
        found |= set(re.split(r"(?<=[.!?])\s+", text))
    return sorted((l for l in found if len(l) > 2), key=len, reverse=True)


def _without(unit: str, labels: list[str]) -> str:
    """Remove quoted labels and source wording before looking for claims."""
    for label in labels:
        unit = unit.replace(label, " ")
    return unit


def _show(units_: list[str]) -> str:
    return " | ".join(u[:160] for u in units_[:3])
