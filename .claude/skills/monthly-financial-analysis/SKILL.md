---
name: monthly-financial-analysis
description: Draft a monthly financial trend analysis report (revenue, margins, operating expenses by category, month-over-month, trailing twelve months, significant movements) for one client and one month, using only the verified figures finance_engine writes to metrics.json. Use when asked to analyse, review or write commentary on a client's monthly financials, e.g. "analyse abc_inc for June 2026" or "monthly report for company_c_expense_spike 2026-01".
allowed-tools: Bash(python -m finance_engine:*), PowerShell(python -m finance_engine:*), Read, Write, Edit, Glob
---

# Monthly financial analysis

You write a **draft** for a human reviewer. Python (`finance_engine`) calculates every
number; you describe those numbers. You never calculate, estimate or invent one.

Reference files (read when you reach the step that needs them):
- `reference/metric_definitions.md`: what each metric and flag means.
- `reference/report_template.md`: sections, order and wording rules for `report.md`.
- `reference/review_checklist.md`: the reviewer checklist that ends every report.

## Steps

1. **Identify the client and period.** You need a `client_id` and a month `YYYY-MM`.
   If either is missing or ambiguous, ask; do not guess. Run
   `python -m finance_engine list-periods --client <id>`. If it fails with
   "No config for client", **stop**: say the client has no config, list the available
   clients from the error, and write nothing. If the period is malformed or not listed,
   **stop** and say which months are available.

2. **Calculate.** Run `python -m finance_engine analyze --client <id> --period <YYYY-MM>`.
   - Exit 0: continue.
   - Exit 2: it finished, but there are data-quality errors or reconciliation failures.
     Continue, and put them at the top of the report.
   - Exit 1: **stop** and report the error message. Do not work around it.

3. **Read the figures.** Run `python -m finance_engine brief --client <id> --period <YYYY-MM>`.
   It prints the values in `output/clients/<id>/<period>/metrics.json`, already formatted
   by Python, each next to its metric name. For anything not in the brief, use
   `brief ... --metric <text>` (for example `--metric opex.line.legal`). Do not read the raw
   metrics.json into context: it is large and holds the same numbers.
   **Never do arithmetic.** If a figure you want is not printed, leave it out or say it is
   not available. Do not add, subtract, average or re-round anything.

4. **Write the report** following `reference/report_template.md`: revenue, margins,
   operating expenses by category, month over month, TTM, and the significant movements a
   business owner should know about. Quote figures exactly as the brief prints them.

5. **Explain why a movement matters, not why it happened.** The workbook records amounts,
   not reasons. Any cause must be marked "possible" and "needs confirmation", and phrased
   as a question for the reviewer. Never state a cause as fact.

6. **Put data quality first.** Every data-quality issue, by its code, goes near the top.
   So do reconciliation failures, blocked metrics, an incomplete TTM window and the
   seasonality limitation. Write "n/a" with the reason where the brief says n/a, never 0.

7. **End with the reviewer checklist** from `reference/review_checklist.md`.

8. **Check your numbers.** Save the report to `output/clients/<id>/<period>/report.md`, then run
   `python -m finance_engine check-commentary --client <id> --period <YYYY-MM> --file output/clients/<id>/<period>/report.md`.
   If it says REJECTED, fix or remove each listed figure (use the brief's exact string)
   and re-run until it says PASS. If a rejected "number" is not a financial figure
   (part of a label, a hash or a name), rephrase that text. Do not debug the guard.
   Never finish with a rejected report.

9. **Finish.** Reply with the report path, the check-commentary result, and the
   data-quality headline. One client per report: never read, cite or compare another
   client's figures. Never print or repeat credentials, tokens or `.env` contents.

10. **QuickBooks.** Use the QuickBooks MCP tools (read-only) only when the user asks for
    QuickBooks data and the `quickbooks` server is configured. QuickBooks figures are not
    in metrics.json, so the guard cannot verify them. Keep them out of `report.md` unless
    the user asks for them, and label them as unreconciled if they go in.

## If something is unavailable

If you cannot finish (for example the user wants no AI text), the deterministic fallback
is `python -m finance_engine report --client <id> --period <YYYY-MM> --provider deterministic`.
It writes a complete template report that has already passed the guard, and labels itself
as such.
