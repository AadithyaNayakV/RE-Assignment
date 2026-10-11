"""Number guard: every figure in commentary must trace back to metrics.json.

The guard is provider-agnostic on purpose. It takes text and a metrics document, and
knows nothing about who wrote the text — a template, Claude, a local model, or a human.
That is what makes it a real control rather than a courtesy check: swapping the commentary
provider cannot weaken it.

How it decides:

* Every number in the text is extracted, including currency symbols, thousands
  separators, parenthesised negatives, percentages and magnitude suffixes ("$2.3M").
* The allowed set is built from the metrics document: metric values, the workbook's own
  reported values, every traced source-row value, movement and reconciliation figures,
  the configured thresholds, and the structural numbers a report legitimately cites
  (period years, month numbers, row numbers, counts).
* A number matches if some allowed value agrees with it *at the precision it was written
  to*. "70.2%" matches 70.1516...; "$196k" matches 195,898.16. Precision is derived from
  the text, so a vaguer number is held to a looser bar and an exact one to a tight bar.

Two deliberate limitations, both reported rather than hidden:

* Magnitudes are compared, not signs, because prose says "fell 6.0%" where the metric is
  -5.98. Direction words remain a reviewer's job.
* A number that happens to coincide with an unrelated allowed value passes. The guard
  proves a figure is *supported*, not that it was used in the right sentence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Smallest meaningful currency difference — one cent.
MIN_TOLERANCE = 0.011

_SUFFIX_SCALE = {
    "k": 1_000.0, "thousand": 1_000.0, "thousands": 1_000.0,
    "m": 1_000_000.0, "mm": 1_000_000.0, "million": 1_000_000.0, "millions": 1_000_000.0,
    "bn": 1_000_000_000.0, "b": 1_000_000_000.0,
    "billion": 1_000_000_000.0, "billions": 1_000_000_000.0,
}

# A number, optionally currency-prefixed, parenthesised, signed, and followed by a
# magnitude word or a percent sign.
_NUMBER = re.compile(
    r"""
    (?P<open>\()?                                  # parenthesised negative
    \s*
    (?P<presign>[-+−])?                            # sign before the symbol: -$1,234.56
    \s*
    (?P<currency>[$€£₹]|(?:US|AU|CA)?\$)?          # currency symbol
    \s*
    (?P<sign>[-+−])?                               # sign after the symbol: $-1,234.56
    (?P<digits>\d{1,3}(?:,\d{3})+(?:\.\d+)?        # 1,234.56
              |\d+\.\d+                            # 1234.56
              |\d+)                                # 1234
    \s*
    (?P<suffix>k|m|mm|bn|b|thousands?|millions?|billions?)?\b
    \s*
    (?P<pct>%|\s*percent(?:age)?\b)?
    \s*
    (?P<close>\))?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Spans that are never financial claims: dates, period keys, section/row references.
_IGNORE_SPANS = [
    # ISO timestamps, before the date rule: "2026-10-11T10:48:47+00:00" otherwise
    # leaves "48" and "47" behind as figures.
    re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
               r"(?:Z|[+-]\d{2}:\d{2})?"),
    re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b"),                       # 2026-06
    # Hex digests: a hash ending "...a5847036" otherwise yields the figure 5847036.
    re.compile(r"\b[0-9a-f]{16,}\b", re.I),
    # The guard's own result quoted back in a report: "30/30 numbers traced",
    # "All 30 numbers in this section ...". Counts of the check, not financial claims.
    re.compile(r"\b\d+\s*/\s*\d+\s+number(?:s|\(s\))?\s+traced", re.I),
    re.compile(r"(?<![a-z])all\s+\d+\s+numbers\s+in\s+this\s+section", re.I),
    re.compile(r"\b(?:row|rows|line|lines)\s+\d+(?:\s*[-,]\s*\d+)*", re.I),
    re.compile(r"\b(?:schema[_ ]version|v)\s*\d+\b", re.I),
    re.compile(r"\bsha256[:=]?\s*[0-9a-f]{8,}\b", re.I),
    re.compile(r"\bSHA-?256\b", re.I),       # the label itself: "-256" is not a figure
    re.compile(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}\b", re.I
    ),
    re.compile(
        r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
        r"(?:\s+\d{4})?\b", re.I
    ),
    re.compile(r"\|\s*-{3,}\s*\|"),                                  # markdown rules
    # Markdown list markers. Without this, "- 1 data-quality warning" parses as the
    # number -1, because the bullet looks exactly like a minus sign.
    re.compile(r"(?m)^[ \t]*(?:[-*+]|\d{1,3}\.)[ \t]+"),
    re.compile(r"(?m)^[ \t]*-\s*\[[ xX]\][ \t]*"),                   # task-list boxes
    re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+"),                          # headings
]


@dataclass
class Finding:
    text: str
    value: float
    position: int
    line: int
    context: str
    supported: bool
    kind: str = "any"
    closest_allowed: float | None = None
    closest_difference: float | None = None
    tolerance: float = 0.0

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "parsed_value": self.value,
            "line": self.line,
            "position": self.position,
            "context": self.context,
            "supported": self.supported,
            "quantity_kind": self.kind,
            "closest_allowed_value": self.closest_allowed,
            "difference_from_closest": self.closest_difference,
            "tolerance_applied": self.tolerance,
        }


@dataclass
class GuardResult:
    passed: bool
    checked: int
    unsupported: list[Finding] = field(default_factory=list)
    supported: list[Finding] = field(default_factory=list)
    allowed_count: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "numbers_checked": self.checked,
            "numbers_supported": len(self.supported),
            "numbers_unsupported": len(self.unsupported),
            "allowed_values_available": self.allowed_count,
            "unsupported": [f.to_dict() for f in self.unsupported],
            "notes": self.notes,
        }

    def summary(self) -> str:
        if self.passed:
            return (f"PASS — {len(self.supported)}/{self.checked} number(s) traced to "
                    f"metrics.json.")
        lines = [f"REJECTED — {len(self.unsupported)} of {self.checked} number(s) are "
                 f"not supported by metrics.json:"]
        for f in self.unsupported:
            closest = (f"closest supported value {f.closest_allowed:,.2f} "
                       f"(off by {f.closest_difference:,.2f})"
                       if f.closest_allowed is not None else "no comparable value")
            lines.append(f"  line {f.line}: {f.text!r} -> parsed {f.value:,.2f}; {closest}")
            lines.append(f"      context: ...{f.context}...")
        return "\n".join(lines)


class NumberGuard:
    def __init__(self, metrics_doc: dict):
        self.doc = metrics_doc
        self.buckets = collect_allowed_values(metrics_doc)
        self._all = set().union(*self.buckets.values()) if self.buckets else set()

    @classmethod
    def from_path(cls, path: Path) -> "NumberGuard":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def check(self, text: str) -> GuardResult:
        findings: list[Finding] = []
        masked = _mask_ignored(text)
        for match in _NUMBER.finditer(masked):
            parsed = _parse_match(match)
            if parsed is None:
                continue
            value, tolerance, kind = parsed
            candidates = self._candidates(kind)
            # Magnitudes, as documented: every allowed value is also stored as its
            # absolute value, so matching |written| makes the sign irrelevant both ways.
            # Matching the signed value rejected "(2)" or "-$2,800.00" against a
            # positive metric while accepting "+5.98%" against a negative one.
            closest, diff = _closest(candidates, abs(value))
            supported = diff is not None and diff <= tolerance
            findings.append(Finding(
                text=match.group(0).strip(),
                value=value,
                position=match.start(),
                line=masked.count("\n", 0, match.start()) + 1,
                context=_context(text, match.start(), match.end()),
                supported=supported,
                kind=kind,
                closest_allowed=closest,
                closest_difference=diff,
                tolerance=tolerance,
            ))

        unsupported = [f for f in findings if not f.supported]
        return GuardResult(
            passed=not unsupported,
            checked=len(findings),
            unsupported=unsupported,
            supported=[f for f in findings if f.supported],
            allowed_count=len(self._all),
            notes=[
                "A number written as a percentage is only matched against percentage "
                "metrics, and a currency amount only against currency metrics.",
                "Magnitudes are compared, not signs: prose writes \"fell 6.0%\" where the "
                "metric is -5.98. A reviewer must still confirm the stated direction.",
                "The guard proves a figure is supported by metrics.json, not that it was "
                "used in the right sentence or attached to the right concept. A small "
                "unlabelled integer is only weakly constrained.",
            ],
        )

    def _candidates(self, kind: str) -> set[float]:
        if kind == PERCENT_BUCKET:
            return self.buckets[PERCENT_BUCKET] | self.buckets[STRUCTURAL_BUCKET]
        if kind == CURRENCY_BUCKET:
            return self.buckets[CURRENCY_BUCKET]
        return self._all


def _closest(candidates: set[float], value: float) -> tuple[float | None, float | None]:
    if not candidates:
        return None, None
    best = min(candidates, key=lambda c: abs(c - value))
    return best, abs(best - value)


#: Allowed values are bucketed by what kind of quantity they are, so a number written as
#: a percentage can only be matched by a percentage and a currency amount only by a
#: currency amount. Without this, a figure like "churn hit 3.7%" would pass by colliding
#: with any one of several hundred unrelated values.
CURRENCY_BUCKET = "currency"
PERCENT_BUCKET = "percent"
STRUCTURAL_BUCKET = "structural"


def collect_allowed_values(doc: dict) -> dict[str, set[float]]:
    """Every number a report may legitimately cite, bucketed by quantity kind."""
    buckets: dict[str, set[float]] = {
        CURRENCY_BUCKET: set(), PERCENT_BUCKET: set(), STRUCTURAL_BUCKET: set()
    }

    def add(bucket: str, v) -> None:
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            return
        # Both signs are allowed: prose says "fell 6.0%" where the metric is -5.98.
        buckets[bucket].add(float(v))
        buckets[bucket].add(abs(float(v)))

    percent_units = {"percent", "percentage_points"}
    for metric in doc.get("metrics", []):
        bucket = PERCENT_BUCKET if metric.get("unit") in percent_units else CURRENCY_BUCKET
        add(bucket, metric.get("value"))
        add(bucket, metric.get("reported_value"))
        # Source rows are always currency amounts, whatever the metric derived from them.
        for row in metric.get("source_rows", []):
            add(CURRENCY_BUCKET, row.get("original_value"))
            add(STRUCTURAL_BUCKET, row.get("row"))

    movements = doc.get("movements") or {}
    add(STRUCTURAL_BUCKET, movements.get("candidates_considered"))
    add(STRUCTURAL_BUCKET, (movements.get("ttm_window_one_offs_summary") or {})
        .get("count"))
    for key in ("significant", "top_increases", "top_decreases",
                "possible_one_offs", "ttm_window_one_offs"):
        for entry in movements.get(key, []):
            for field_name in ("previous", "current", "change_abs",
                               "comparison_average", "excess_over_average"):
                add(CURRENCY_BUCKET, entry.get(field_name))
            add(PERCENT_BUCKET, entry.get("change_pct"))
            add(STRUCTURAL_BUCKET, entry.get("multiple_of_average"))
            add(STRUCTURAL_BUCKET, entry.get("row"))
    thresholds = movements.get("thresholds") or {}
    add(PERCENT_BUCKET, thresholds.get("significant_mom_pct"))
    add(CURRENCY_BUCKET, thresholds.get("significant_mom_abs"))
    add(CURRENCY_BUCKET, thresholds.get("significant_mom_min_abs"))

    recon = doc.get("reconciliation") or {}
    add(CURRENCY_BUCKET, recon.get("tolerance_abs"))
    add(STRUCTURAL_BUCKET, recon.get("checks_run"))
    add(STRUCTURAL_BUCKET, recon.get("checks_failed"))
    for entry in recon.get("by_check", []):
        add(STRUCTURAL_BUCKET, entry.get("periods_checked"))
        add(CURRENCY_BUCKET, entry.get("max_abs_difference"))
    for entry in recon.get("failures", []):
        for field_name in ("computed", "reported", "difference"):
            add(CURRENCY_BUCKET, entry.get(field_name))

    # Numbers a report cites when describing its own basis.
    source = doc.get("source") or {}
    add(STRUCTURAL_BUCKET, source.get("period_count"))
    for key in source.get("periods_available", []):
        year, _, month = str(key).partition("-")
        if year.isdigit():
            add(STRUCTURAL_BUCKET, int(year))
        if month.isdigit():
            add(STRUCTURAL_BUCKET, int(month))
    add(STRUCTURAL_BUCKET, (doc.get("ttm") or {}).get("months"))
    add(STRUCTURAL_BUCKET, doc.get("schema_version"))
    add(STRUCTURAL_BUCKET, doc.get("units_scale"))

    for issue in doc.get("data_quality_issues", []):
        details = issue.get("details") or {}
        for row in details.get("rows", []) or []:
            add(CURRENCY_BUCKET, row.get("value"))
            add(STRUCTURAL_BUCKET, row.get("row"))

    # Counts that describe the document itself. A report legitimately says "3
    # data-quality warnings" or "21 lines moved", and those are facts about
    # metrics.json rather than claims about the client's finances.
    metrics = doc.get("metrics", [])
    issues = doc.get("data_quality_issues", [])
    add(STRUCTURAL_BUCKET, len(metrics))
    add(STRUCTURAL_BUCKET, len(issues))
    for severity in ("error", "warning"):
        add(STRUCTURAL_BUCKET,
            sum(1 for i in issues if i.get("severity") == severity))
    for status in ("ok", "warning", "blocked"):
        add(STRUCTURAL_BUCKET,
            sum(1 for m in metrics if m.get("validation_status") == status))
    for key in ("significant", "top_increases", "top_decreases", "possible_one_offs"):
        add(STRUCTURAL_BUCKET, len(movements.get(key, [])))
    seasonality = doc.get("seasonality") or {}
    add(STRUCTURAL_BUCKET, seasonality.get("months_available"))
    add(STRUCTURAL_BUCKET, seasonality.get("months_required"))

    return buckets


def _mask_ignored(text: str) -> str:
    """Blank out spans that are structurally not financial claims, preserving offsets."""
    masked = text
    for pattern in _IGNORE_SPANS:
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    return masked


def _parse_match(match: re.Match) -> tuple[float, float, str] | None:
    digits = match.group("digits")
    if digits is None:
        return None
    cleaned = digits.replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None

    decimals = len(cleaned.split(".")[1]) if "." in cleaned else 0
    suffix = (match.group("suffix") or "").lower()
    scale = _SUFFIX_SCALE.get(suffix, 1.0)
    value *= scale

    # Parentheses mean "negative" only in the absence of an explicit sign: "(1,234)" is
    # negative, but "(+158.7%)" is a positive figure that happens to sit in brackets.
    # A sign may appear on either side of the currency symbol: formatting.money writes
    # "-$330,756.08", while some sources write "$-330,756.08".
    explicit_sign = match.group("presign") or match.group("sign")
    bracketed = bool(match.group("open")) and bool(match.group("close"))
    if explicit_sign in ("-", "−"):
        value = -value
    elif bracketed and not explicit_sign:
        value = -value

    # The precision the number was written to sets the bar: half of the last written
    # digit, scaled by any magnitude suffix.
    tolerance = max(MIN_TOLERANCE, 0.5 * (10.0 ** -decimals) * scale)

    # What kind of quantity was written decides which allowed values may match it.
    if match.group("pct"):
        kind = PERCENT_BUCKET
    elif match.group("currency") or suffix or "," in digits:
        kind = CURRENCY_BUCKET
    else:
        kind = "any"
    return value, tolerance, kind


def _context(text: str, start: int, end: int, width: int = 45) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    return " ".join(text[left:right].split())


def check_commentary(text: str, metrics_doc: dict) -> GuardResult:
    """Convenience entry point used by the CLI and by commentary providers."""
    return NumberGuard(metrics_doc).check(text)
