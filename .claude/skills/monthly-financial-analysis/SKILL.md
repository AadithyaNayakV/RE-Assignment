---
name: monthly-financial-analysis
description: Draft a monthly financial trend analysis report (revenue, margins, operating expenses by category, month-over-month, trailing twelve months, significant movements) for one client and one month, using only the verified figures finance_engine writes to metrics.json. Use when asked to analyse, review or write commentary on a client's monthly financials, e.g. "analyse abc_inc for June 2026" or "monthly report for company_c_expense_spike 2026-01". Also covers a calendar year or the whole file ("the 2025 summary for test_clean"), and drafting a config for a new client's workbook with init-client.
allowed-tools: Bash(python -m finance_engine:*), PowerShell(python -m finance_engine:*), Read, Write, Edit, Glob
---

# Monthly financial analysis

You write a **draft** for a human reviewer. Python (`finance_engine`) calculates every
number; you describe those numbers. You never calculate, estimate or invent one.

Run **one `python -m finance_engine` command per tool call**. Do not chain commands with
`;`, `&&` or `echo`; chained commands need extra approval and waste turns.

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
   by Python, each next to its metric name. (Example amounts in this file are written
   "USD 1,806.24": the skill loader treats a dollar sign followed by a digit as an argument
   placeholder and rewrites it. In the report, copy each amount exactly as the brief
   prints it, currency symbol included.) For anything not in the brief, use
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
   **Causes appear ONLY as questions in "Questions for the reviewer"**, each worded as
   possible and needing confirmation. Everywhere else in the report, do not use
   "because", "due to", "caused", "the reason" or "driven by". Say *where* a change sits,
   not *why*: write "the increase is in Subscriptions", never "driven by Subscriptions".
   **Never say what a figure *is*.** "a reversal", "a recovery", "a refund", "a one-off",
   "a misposting", "a timing difference" are causes. Write "may be a reversal; needs
   confirmation", never "is a reversal" or ": a reversal". A sign change is described by
   its direction only: "swung from a USD 12,935.29 charge to a USD 1,806.24 credit".
   **Write directions in words, amounts unsigned:** in prose, the figure after fell, rose,
   up, down, narrowed, widened, increased or decreased never carries + or -. Write
   "fell USD 12,452.80 (-6.0%)", "rose 3.1 pts" and "up USD 5,342.51 (+2.8%)", never
   "fell -USD 12,452.80" or "up +3.1 pts". Tables keep the brief's signs. `check-commentary`
   rejects a direction word that contradicts its change ("rose" on a fall), and treats
   "the loss narrowed" as income rising.
   **In "Questions for the reviewer", put the word `change` or `level` directly before
   every figure**, including each figure in a list: "Bad Debts: level USD 12,935.29 in
   2026-05; level -USD 1,806.24 in Jun 2026; change -USD 14,741.53."

6. **Put data quality first.** Every data-quality issue, by its code, goes near the top.
   So does every row of the brief's "Reconciliation failures" table, each with its check
   and its month (the eval check `reconciliation_failures_named` fails a report that
   leaves a month out). So do blocked metrics, an incomplete TTM window and the
   seasonality limitation. Where the brief says `n/a (…)`, copy it with its bracketed note, never 0.

7. **End with the reviewer checklist** from `reference/review_checklist.md`.

8. **Check your numbers.** Save the report to `output/clients/<id>/<period>/report.md`, then run
   `python -m finance_engine check-commentary --client <id> --period <YYYY-MM> --file output/clients/<id>/<period>/report.md`.
   If it says REJECTED, fix or remove each listed figure (use the brief's exact string)
   and run the check once more. That is the second and last attempt. If a rejected
   "number" is not a financial figure (part of a label, a hash or a name), rephrase that
   text. Do not debug the guard. Never finish with a rejected report.

   **Stopping rule.** If check-commentary still says REJECTED after the second attempt:
   1. Run the deterministic fallback:
      `python -m finance_engine report --client <id> --period <YYYY-MM> --provider deterministic`.
      It overwrites `report.md` with the template report, which has already passed the guard.
   2. Say so in the report: add one line under the title, "The drafted commentary did not
      pass the number check, so this is the deterministic template report." Use no figures
      in that line.
   3. **STOP.** Do not attempt a third time. Never hand-edit `metrics.json`.

9. **Finish.** Reply with the report path, the check-commentary result, and the
   data-quality headline. If the stopping rule in step 8 applied, say that the
   deterministic fallback was used and why. One client per report: never read, cite or
   compare another client's figures. Never print or repeat credentials, tokens or `.env`
   contents.

10. **QuickBooks.** Use the QuickBooks MCP tools (read-only) only when the user asks for
    QuickBooks data and the `quickbooks` server is configured. QuickBooks figures are not
    in metrics.json, so the guard cannot verify them. Keep them out of `report.md` unless
    the user asks for them, and label them as unreconciled if they go in.

## Year and whole-file scopes

Use these when the user asks for a calendar year ("the 2025 summary") or the whole file.
Follow steps 1 to 10 above with the substitutions below. Every rule above still applies:
step 5 (causes only as questions in "Questions for the reviewer"; no "because", "due to",
"caused", "the reason" or "driven by" anywhere else), step 6 (data quality first), and
the step 8 stopping rule, two attempts and then the deterministic fallback.

| | Month | Year | Whole file |
| --- | --- | --- | --- |
| Scope flags (analyze, brief, check-commentary, report) | `--period YYYY-MM` | `--scope year --year YYYY` | `--scope all` |
| Report path | `output/clients/<id>/<period>/report.md` | `output/clients/<id>/year-YYYY/report.md` | `output/clients/<id>/all/report.md` |

In step 1, the year must appear in `list-periods`; if it does not, **stop** and say which
months the source holds. Rules that only arise in these scopes:

- A partial year is the total of the months present only. Use the brief's own label
  ("year to date, N of 12 months") and never scale, annualise or extrapolate it.
- If the brief says the year totals are n/a because a month is missing inside the year,
  say so, name the month, and present no total.
- Quarters and year on year: only what the brief prints. An incomplete quarter and a year
  without the same months in both years are n/a; put nothing in their place.
- The highest and lowest months are facts: give the month and the figure, never a cause.

**Several workbooks.** If an engine command stops because the client's folder holds
several workbooks covering the request, ask the user which file to use, then pass it as
`--file NAME` to analyze, brief and report (`--workbook NAME` to check-commentary, whose
`--file` is the report being checked). Outputs then sit under
`output/clients/<id>/<file-stem>/...`. Never combine figures from two files.

## A new client (init-client)

If the user hands you a workbook for a client that has no config (step 1 fails with
"No config for client"), and asks you to set it up, run
`python -m finance_engine init-client --file <path.xlsx> --client-id <id> --name "<Company>"`.
It inspects the layout, copies the workbook into `data/clients/<id>/source/`, and
validates the draft with the real engine. Then:

- Exit 0: `config/clients/<id>.yaml` was written. Tell the user the "needs your decision"
  items at the top of that file (currency, categories, anything ambiguous); they are
  guesses until a person confirms them. Then continue from step 1.
- Exit 2: only `config/clients/<id>.draft.yaml` was written, because validation failed.
  **Stop**, list the problems it printed, and write no report. Never edit the draft
  into shape yourself, and never pass `--force` unless the user asks.
- Exit 1: the file is unreadable, not a monthly income statement, or a config already
  exists. Report the message and stop.

## If something is unavailable

If you cannot finish (for example the user wants no AI text), the deterministic fallback
is `python -m finance_engine report --client <id> --period <YYYY-MM> --provider deterministic`.
It writes a complete template report that has already passed the guard, and labels itself
as such.
