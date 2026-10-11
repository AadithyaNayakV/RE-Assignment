# PHASE 1 — PROJECT FOUNDATION AND FINANCE ENGINE

Act as a senior Python engineer. I am doing the RE Financial Services "AI Solutions Builder" build exercise. Financial accuracy matters more than speed. Python does ALL calculations. AI only explains verified numbers.

My setup: Windows, VS Code, Claude Code with a **Claude Pro subscription (no Claude API key, no paid APIs)**. I build locally now; hosting and extra features come later, so the design must allow adding them **without rewriting**.

Complete this phase in small steps, run tests, fix failures, and report actual results. Do not stop after making a plan.

---

## 0. Inspect first

1. Inspect the current directory, Git status, existing code and config.
2. Find the cloned `quickbooks-online-mcp-server` repo and leave it untouched.
3. Find the assignment PDF and `ABC Inc - Financial Statements.xlsx`. If missing, tell me where to put them.
4. Never overwrite my `.env`, OAuth tokens or MCP config.
5. Reuse existing working code. Do not invent spreadsheet contents, financial values or completed functionality.
6. Inspect the actual workbook: worksheets, periods, formulas, column names, categories, layout.
7. If the assignment PDF is available, create a first version of `docs/requirements_checklist.md`.
8. Do not deploy anything or create paid cloud resources in any phase unless I ask.

## 1. Architecture (follow this exactly)

The core rule: **every part communicates through one file, `metrics.json`.**

```
Data source (Excel now, QuickBooks later)
   -> finance_engine: read -> validate -> calculate -> metrics.json
   -> commentary provider (Claude via skill now / template now / Ollama or API later)
   -> guard (number check) -> report.md
```

Rules:
- `finance_engine` contains NO AI, NO web server, NO QuickBooks code.
- Everything is keyed by `client_id` (config, data folders, outputs).
- External dependencies sit behind small interfaces: `DataSource`, `CommentaryProvider`, `CredentialStore`.
- All settings come from environment variables (e.g. `LLM_PROVIDER=deterministic`).
- Do NOT add Gemini or any paid/remote AI provider. Do NOT assume any API key exists.

Folder structure (adapt only if existing code requires it):

```
CLAUDE.md
.env.example   .gitignore
config/clients/abc_inc.yaml
finance_engine/
  sources/base.py  sources/excel.py
  validation.py  metrics.py  reconciliation.py
  commentary/base.py  commentary/deterministic.py
  guard.py  report.py  cli.py
data/clients/abc_inc/source/        (original workbook, never modified)
data/synthetic/                      (generated test workbooks)
output/clients/<client_id>/<period>/ (metrics.json, report.md)
tests/
docs/
```

## 2. CLAUDE.md (project memory)

Create a short `CLAUDE.md` with: project purpose, folder map, "Python is the only source of numbers", "never calculate in commentary", "never print or commit secrets", how to run tests, and the `metrics.json` contract. Keep it under ~60 lines.

## 3. Facts about the ABC workbook (verify these yourself, then handle them)

- Two sheets: `Income Statement` (labels in column B, months from column C) and `Balance Sheet` (labels in column C, dates from column D). Offsets differ.
- Months are **newest first**: Jun 2026 back to Jan 2025 (18 months). A complete TTM window exists only for Dec 2025 – Jun 2026.
- Formula rows with cached values: Total Income, Total Cost of Goods Sold, Gross Profit, Total S & M Payroll, Total Marketing, Total Operating Expenses, Operating Income, Net Income. Use them for reconciliation.
- "S & M Payroll" and "Marketing" are subtotal groups inside Operating Expenses. Do not double-count.
- Interest Income, Interest Expense, Bad Debts (can be negative), Income Tax Benefit sit inside Operating Expenses, so the workbook's "Operating Income" equals Net Income. Report this as a classification note; do not silently reclassify.
- Many zero rows: MoM % from a zero base must be shown as "n/a", not infinity.

## 4. Per-client mapping config

`config/clients/abc_inc.yaml` describes: sheet names, label column, first data column, date order, which rows are revenue / COGS / opex / subtotals / totals, and reconciliation rules. The engine reads the config, so a new company = a new YAML file, not new code. If a layout cannot be safely interpreted, report it instead of guessing.

## 5. Ingestion and validation

- Read `.xlsx` with openpyxl (formulas and cached values). Never modify source files.
- Keep traceability: sheet, row, label, period, original value.
- Detect missing months, duplicate periods/categories, invalid numbers, blank vs zero. Never turn missing into zero.
- Handle Excel formulas (read cached values; report formulas whose values cannot be read), negative amounts and zeros carefully.
- Identify currency and unit information where available.
- Check that the correct workbook and period were selected, that dates are valid, that sign conventions and categories are understood, and flag incomplete or contradictory information.
- Validation produces **errors** (block the affected metric) and **warnings** (continue, but show). Unrelated metrics still compute.
- If a critical calculation or reconciliation fails, stop that analysis and explain why. Never substitute estimates.
- Reconcile computed totals against the workbook's own totals (tolerance for rounding, documented). Check Assets = Liabilities + Equity on the balance sheet.

## 6. Metrics (only where the data supports them)

- Revenue: total, by stream, MoM absolute and %, TTM.
- COGS, gross profit, gross margin.
- Operating expenses by category and by group; MoM; largest increases and decreases; TTM.
- Operating income and margin (with the classification note above).
- Significant movements (configurable threshold), possible one-offs (a spike vs the trailing average, labelled "possible", never a stated cause), seasonality only if the data supports it.
- Key balance-sheet trends (cash, AR, deferred revenue) where available.

Never classify a line as revenue, COGS or opex from its name alone; use the client config. Never use AI-generated values as calculation inputs.

Safeguards: zero/negative denominators, missing months, incomplete TTM never presented as complete, full precision internally, round only for display.

## 7. metrics.json contract

Define a versioned schema (e.g. `schema_version: 1`). Each metric: name, client_id, period, value, unit, source rows, method id, validation status, warnings. Plus a top-level list of data-quality issues and limitations. Document the schema in `docs/metrics_schema.md`. This file is the contract every commentary provider and the skill will use.

## 8. Commentary and guard

- `commentary/base.py`: a `CommentaryProvider` interface (input: metrics.json; output: commentary text).
- `commentary/deterministic.py`: template-based commentary, no AI. This is the always-available fallback.
- Providers receive only metrics.json (verified values, comparisons, periods, evidence, warnings, limitations), never the whole workbook.
- Commentary must separate confirmed facts from possible explanations, and must never invent causes, figures or categories.
- Leave a clear extension point (documented, not implemented) for future `ollama.py` and `claude_api.py`. Claude via the skill is built in Phase 3.
- `guard.py`: extracts every number from any commentary text and checks it against metrics.json (with allowed rounding). Unsupported numbers = reject. The guard must not depend on which provider wrote the text.

## 9. Report and CLI

- `report.py` builds `report.md` from metrics.json plus commentary: company and period, source and data-quality status, summary, revenue, margins, expenses by category, MoM, TTM, significant movements, reconciliation results, limitations, and a **Reviewer checklist** (the output is a draft for a human reviewer).
- Show warnings prominently. Do not generate unsupported metrics just to fill a section. Python formats all numbers.
- `cli.py`:
  - `python -m finance_engine analyze --client abc_inc --period 2026-06` writes metrics.json.
  - `python -m finance_engine report --client abc_inc --period 2026-06` writes report.md (deterministic commentary).
  - `python -m finance_engine check-commentary --client abc_inc --period 2026-06 --file <text>` runs the guard.

## 10. Synthetic workbooks (after ABC works)

Do not modify or recreate the original ABC workbook. Generate 10 clearly labelled SYNTHETIC workbooks in `data/synthetic/` with openpyxl, each with its own YAML config and ~18 months of data:

1. `company_a_normal_growth.xlsx` — normal growth, stable expenses
2. `company_b_revenue_decline.xlsx` — revenue falling over several months
3. `company_c_expense_spike.xlsx` — one expense category jumps
4. `company_d_margin_decline.xlsx` — shrinking margins
5. `company_e_seasonal_business.xlsx` — seasonal revenue pattern
6. `company_f_one_time_expense.xlsx` — one large one-off expense
7. `company_g_missing_data.xlsx` — missing months / incomplete data
8. `company_h_zero_negative_values.xlsx` — zeros, negatives, tricky % changes
9. `company_i_multiple_revenue_streams.xlsx` — several revenue streams and expense categories
10. `company_j_balance_sheet.xlsx` — income statement plus balance sheet

Requirements: realistic but fictional, internally consistent, include source totals and formulas where appropriate, clear periods/headings/sheet names. Vary layouts (at least two different from ABC, e.g. oldest-first months, different columns) to prove the config approach. Store each workbook's independently computed expected values in a separate file. Never use synthetic results as evidence of ABC Inc performance.

## 11. Tests

- Hand-calculated small examples, not copies of production formulas.
- ABC: engine totals must match the workbook's own formula totals for all 18 months.
- Missing months, duplicates, zero/negative bases, rounding, TTM windows.
- Guard: rejects invented numbers, accepts correct ones.
- Mutation check: test-only code injects a wrong calculation and confirms the tests fail.

## 12. Order of work

1. Inspect  2. ABC config + ingestion  3. Validation  4. Metrics  5. Tests + reconciliation  6. metrics.json + guard + deterministic commentary  7. Report + CLI  8. Synthetic workbooks + their tests  9. Run full suite  10. Fix and document remaining issues.

Do not build the QuickBooks connection, the skill, the architecture note or any deployment in this phase.

## 13. Security

`.env` and token files in `.gitignore`. `.env.example` with placeholders only. Never print secrets.

## 14. Final report

Files created/changed, exact commands, which workbooks were tested, reconciliation results for ABC, test pass/fail counts (real ones), mutation-check result, known limitations and unresolved discrepancies. Tests are evidence within their coverage, not proof that every result is correct. Do not claim a workbook was tested if it was not. Do not claim anything you did not run.