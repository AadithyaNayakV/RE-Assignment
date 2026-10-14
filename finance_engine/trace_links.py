"""Which figures in a report's prose can be linked to exactly one metric.

The web UI makes a number in the report clickable (to open its source cells) only when
this module says the link is unambiguous. It is engine code on purpose: matching a written
figure to a metric compares money values, and the UI and the web layer never do that.

How a link is decided, with nothing guessed:

1. The guard (`NumberGuard.check`, unchanged) parses every figure in the prose and finds
   the allowed value it matches (`closest_allowed`), within its own tolerance.
2. A figure is linked only if it is supported, written as currency or a percentage (a bare
   number such as a count or a row is never linked), and exactly ONE metric in
   metrics.json holds that matched value (its value or reported value, either sign), with
   a unit of the same kind.
3. Anything else (no match, two or more metrics with that value, a bare number) stays
   plain text.

Table rows (lines starting with "|") are left out: their figures are listed, with their
traces, in the Numbers tab.
"""

from __future__ import annotations

from finance_engine.guard import CURRENCY_BUCKET, PERCENT_BUCKET, NumberGuard

_PERCENT_UNITS = {"percent", "percentage_points"}


def _owners(doc: dict) -> dict[str, dict[float, set[str]]]:
    """bucket -> magnitude -> the names of the metrics holding it."""
    owners: dict[str, dict[float, set[str]]] = {CURRENCY_BUCKET: {}, PERCENT_BUCKET: {}}
    for metric in doc.get("metrics", []):
        bucket = PERCENT_BUCKET if metric.get("unit") in _PERCENT_UNITS else CURRENCY_BUCKET
        for field in ("value", "reported_value"):
            v = metric.get(field)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                owners[bucket].setdefault(abs(float(v)), set()).add(metric["name"])
    return owners


def prose_text(report_md: str) -> str:
    """report.md with table rows emptied. Line numbers, and columns within every kept
    line, are unchanged. (Emptied, not filled with spaces: long runs of blanks make the
    guard's number pattern backtrack for minutes.)"""
    return "\n".join("" if line.lstrip().startswith("|") else line
                     for line in report_md.split("\n"))


def prose_links(report_md: str, doc: dict) -> list[dict]:
    """[{line, start, end, text, metric}] with 1-based lines and 0-based columns."""
    text = prose_text(report_md)
    owners = _owners(doc)
    result = NumberGuard(doc).check(text)
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)
    links = []
    for f in result.supported:
        if f.kind not in owners or f.closest_allowed is None:
            continue
        names = owners[f.kind].get(abs(float(f.closest_allowed)), set())
        if len(names) != 1:
            continue                                     # ambiguous or none: plain text
        start = text.find(f.text, f.position)
        if start < 0:
            continue
        written = f.text
        if written.startswith("("):                      # the bracket is not the figure
            start, written = start + 1, written[1:]
        written = written.removesuffix(")")
        col = start - line_starts[f.line - 1]
        links.append({"line": f.line, "start": col, "end": col + len(written),
                      "text": written, "metric": next(iter(names))})
    return links
