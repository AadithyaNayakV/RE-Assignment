"""Causal-wording detection, shared by the engine and the eval suite.

Moved verbatim from `evals/report_checks.py` so that the commentary orchestrator can run
the same check the Level 2 evals run, without the engine importing `evals` (which itself
imports the engine). `report_checks` imports these back under their old names, so its
behaviour is unchanged.

`causal_units(text, doc)` is the check: the text units outside "Questions for the
reviewer" that contain a causal word, after quoted labels and source wording are removed.
"""

from __future__ import annotations

import re

# Causal words that may appear ONLY under "Questions for the reviewer", hedged or not.
# Elsewhere the report says where a change sits, never why (SKILL.md step 5).
# "reason"/"reasons" is matched as a whole word, so "the whole reason" and "one reason"
# fail too. The one exception is "no reason(s)": "the workbook records no reason for any
# movement" denies a cause rather than stating one, and the deterministic template says
# it in every report. Without the exception the control fails six cases.
CAUSAL_WORDS = re.compile(r"\b(because|due to|caused|driven by)\b|(?<!\bno )\breasons?\b",
                          re.I)


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


def period_named(text: str, key: str) -> bool:
    """`2025-08`, `Aug 2025` or `August 2025` appears in the text."""
    import calendar
    year, month = int(key[:4]), int(key[5:7])
    forms = [re.escape(key),
             rf"{calendar.month_abbr[month]}\.? {year}",
             rf"{calendar.month_name[month]} {year}"]
    return re.search(rf"\b({'|'.join(forms)})\b", text, re.I) is not None


def outside_section(text: str, heading: str) -> str:
    """`text` minus the section whose heading (any level) matches, up to the next
    heading of the same or a higher level. Unchanged if there is no such section."""
    m = re.search(rf"(?im)^(#{{1,6}})\s+{heading}\s*$", text)
    if not m:
        return text
    level = len(m.group(1))
    nxt = re.search(rf"(?m)^#{{1,{level}}}\s", text[m.end():])
    end = m.end() + nxt.start() if nxt else len(text)
    return text[:m.start()] + text[end:]


def labels(doc: dict) -> list[str]:
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


def without(unit: str, labels: list[str]) -> str:
    """Remove quoted labels and source wording before looking for claims."""
    for label in labels:
        unit = unit.replace(label, " ")
    return unit


def causal_units(text: str, doc: dict) -> list[str]:
    """Units outside "Questions for the reviewer" with a causal word: the eval check
    `causal_words_outside_questions`, exactly."""
    found = labels(doc)
    return [u for u in units(outside_section(text, r"questions for the reviewer"))
            if CAUSAL_WORDS.search(without(u, found))]


# ---- direction words with signed amounts ---------------------------------------------

#: House style: the direction lives in the word, the amount is unsigned.
#: "fell $12,452.80 (-6.0%)", never "fell -$12,452.80" or "narrowed by -2.6 pts".
SIGNED_AFTER_DIRECTION = re.compile(
    r"\b(fell|decreased|declined|dropped|down|narrowed|rose|increased|grew|up|widened|"
    r"climbed)\s+(by\s+)?[*_]*[+\-−]\s?[$€£₹]?\d", re.I)


# ---- comparison caveats ---------------------------------------------------------------

#: How a writer may name each headline measure.
MEASURE_TERMS = {
    "revenue.total": ("revenue",),
    "cogs.total": ("cost of goods sold", "cogs", "cost of sales"),
    "gross_profit": ("gross profit",),
    "gross_margin_pct": ("gross margin",),
    "opex.total": ("operating expense", "opex"),
    "operating_income": ("operating income", "operating profit", "operating loss"),
    "operating_margin_pct": ("operating margin",),
}
#: Words that show the caveat itself is being stated, not just the figure.
CAVEAT_WORDS = re.compile(
    r"reconcil|blank|non-numeric|not a number|caveat|caution|with care|not be read|"
    r"rests? on|unreliable|does not add up", re.I)


def commentary_section(report: str) -> str:
    """The text under `## Commentary` when the report has one; the whole text otherwise
    (a skill-written report, or commentary text on its own)."""
    m = re.search(r"(?m)^##\s+Commentary\s*$", report)
    if not m:
        return report
    nxt = re.search(r"(?m)^##\s", report[m.end():])
    return report[m.end(): m.end() + nxt.start()] if nxt else report[m.end():]


def unstated_caveats(text: str, doc: dict) -> list[dict]:
    """Caveats in `doc` the commentary does not state: no single sentence or bullet names
    the measure, the comparison month and the caveat itself."""
    items = (doc.get("comparison_caveats") or {}).get("items") or []
    if not items:
        return []
    found = units(commentary_section(text))
    missing = []
    for item in items:
        terms = MEASURE_TERMS.get(item["measure"], ()) + (item["measure"],)
        stated = any(any(t in u.casefold() for t in terms)
                     and period_named(u, item["against"]) and CAVEAT_WORDS.search(u)
                     for u in found)
        if not stated:
            missing.append(item)
    return missing

