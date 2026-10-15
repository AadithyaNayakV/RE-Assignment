# PHASE 5B: FILE SELECTION, YEAR AND WHOLE-FILE ANALYSIS, NEW-CLIENT COMMAND

Read CLAUDE.md first. Phase 5A must already be committed. Do not break anything that already works. Do ONE task at a time, in the order below, and run the full test suite after each. If a test that passed before now fails, fix or revert that task before moving on.

Record the test baseline first (it should be 709 passed, 3 skipped) and report the new total at the end.

## Hard rules
- Do NOT commit or push.
- Do NOT run `claude -p` skill evals or anything that costs money. The free deterministic Level 2 comparison and the engine evals are fine.
- Never print .env contents or tokens.
- The original ABC workbook must stay byte-identical. Verify its SHA-256 against scripts/reference_hashes.json (do not use any hash typed in this prompt).
- MONTH BEHAVIOUR MUST NOT CHANGE. Every existing command without the new flags must produce identical output and write to the same paths. If any existing golden case, expected.json or test output changes, STOP and tell me which one and why. Never edit expected files to make tests pass.
- Python is the only source of numbers. Never estimate, extrapolate, annualise, or fill a gap.
- All new report and commentary text must pass the existing causal-wording check (causal_words_outside_questions): state what changed, never why. No "because", "due to", "caused", "driven by", or the word "reason" except the existing "no reason" disclaimer form. Causes appear only as questions in "Questions for the reviewer".
- The UI will call these functions later, so every new or extended command should support a `--json` option that prints one clean machine-readable result to stdout (progress notes go to stderr), and failures must give a clear message and a non-zero exit code.
- Do not describe anything as existing unless it is built and tested.

## Task 1: choose which source file to analyse
A client may have several workbooks in data/clients/<client>/source/, all sharing the same config layout. No merging. Read how `source` is defined in finance_engine/config.py first.
- Add `--file NAME` to analyze, report, brief, check-commentary and list-periods. Without it, behaviour is exactly as today (the config's filename).
- If `--file` is omitted and the source folder contains several workbooks, find the ones that contain the requested period (or year). Exactly one match: use it and print which file was chosen (to stderr). Several matches: stop and list them. No match: say so and list the periods each file covers.
- With `--file`, write outputs to output/clients/<client>/<file-stem>/<period>/ (the year and all scopes below follow the same pattern). Without `--file`, keep today's paths.
- Every comparison (previous month, year-on-year, 12-month window, year totals) uses only the chosen file. A comparison month that exists only in another file is n/a with the note "prior period is in a different file". Never read across files.
- metrics.json and reports record the file name and SHA-256 actually used.
- `list-periods` with no `--file` lists each file in the folder with its first and last period.
- Tests (tmp_path workbooks): two files with different periods; period only in file A; period in both files (must stop); no match; `--file` forcing one; a comparison month in the other file gives n/a; an existing single-file client is unchanged.

## Task 2: year scope
Add `--scope month|year|all` (default `month`, today's behaviour) to analyze, report, brief and check-commentary, plus `--year YYYY` for scope year. `--period` stays for month scope.

`--scope year --year YYYY` writes output/clients/<client>/year-YYYY/metrics.json and report.md (with `<file-stem>/` in the path when `--file` is used). Follow the existing metrics.json style: name, value, unit, method_id, validation_status, source_rows, warnings, notes. Contents:
- Coverage: months present in that year, months missing, status `complete` (12 contiguous months) or `partial` (labelled "year to date, N of 12 months"). A partial year is never scaled or annualised.
- Totals for the year: revenue (total and per stream), COGS, gross profit, gross margin, opex (total and per category), operating income, operating margin, net income as reported. Totals are sums of monthly values the engine already computes.
- If a month is missing inside the span (a gap, not just a short year), the year totals are n/a with the reason, using the same blocked-metric mechanism as the 12-month window. List the missing months.
- Quarters Q1 to Q4: totals where all three months exist, n/a for an incomplete quarter.
- Year-on-year: compare to the prior year ONLY when the same set of months exists in both years (Jan-Jun 2026 versus Jan-Jun 2025 is allowed; anything else is n/a with the note). Use the existing strictly-positive-base rule for percentages.
- A monthly table for the year with month-over-month change, plus the best and worst month by revenue and by operating income (factual only).
- Reconciliation result for the months in that year, and the data-quality issues that fall in those months (including the cell-level ones from 5A).

## Task 3: whole-file scope
`--scope all` writes output/clients/<client>/all/metrics.json and report.md:
- Coverage: first and last period, number of months, gaps.
- A monthly series for every period: revenue, COGS, gross profit, opex, operating income, gross margin, operating margin, with validation_status per point. The UI will draw charts from this, so keep the shape simple and stable and document it in docs/metrics_schema.md.
- A total per calendar year (complete or labelled year to date), reusing Task 2 logic.
- Start-to-end change between the first and last period (absolute and percent, strictly-positive-base rule).
- Seasonality: assessed only with at least 24 months (existing rule), otherwise "not assessed" with the reason.
- Reconciliation across all months and all data-quality issues.

## Task 4: commentary and guard for the new scopes
- The deterministic provider writes commentary for year and all scopes with the same style and rules as months.
- check-commentary works for the new scopes with the smallest change that lets it read the right metrics file. Every number in the text must trace to that JSON and direction words must match signs, exactly as for months.
- `brief --scope year|all` produces the compact brief the same way it does for months.
- In SKILL.md add a short section for year and all scopes that reuses the existing steps and rules (same stopping rule, same causal-wording rule). Do not rewrite the month instructions.
- Run the causal-wording check over the new deterministic reports and fix any wording it flags in the new text only.

## Task 5: golden cases
Add golden cases whose expected values are built independently of finance_engine by extending evals/tools/build_expected.py in its existing style (read the xlsx with openpyxl only; never import finance_engine). Use workbooks from scripts/make_test_workbooks.py and existing synthetic cases:
1. Complete year: test_clean, year 2025. Cross-check revenue 642000, COGS 114600, gross profit 527400, opex 426000, operating income 101400 (these equal its existing 2025-12 trailing-twelve-month figures).
2. Partial year: test_clean, year 2026 is labelled year to date, 1 of 12 months, with no annualised figure.
3. Gap inside a year: test_errors, year 2025 (June missing) gives n/a totals with the reason and the missing month listed.
4. Whole file: test_clean, scope all (13 months, per-year totals, seasonality not assessed).
5. File selection: two small tmp-style workbooks for one client, a period found in exactly one file, plus a period in both files that must stop.
Register them the same way as the existing cases, make sure the engine eval runner includes them, add report-level checks for the new reports (numbers traced, required flags, forbidden causal wording), and update the case count wherever it is documented. Do not re-save any baseline.

## Task 6: init-client command
Build `python -m finance_engine init-client --file PATH.xlsx --client-id ID --name "Company Name"` (with `--json`).
- Inspect the workbook and detect: the income-statement sheet, the header row (first row with at least 3 parseable period labels), the label columns, the first data column, the date order (newest first or oldest first), the date format(s), the sections (revenue, COGS, operating expenses) with their header and total labels, and the reported lines (Gross Profit, Operating Income, Net Income).
- Read finance_engine/config.py to learn exactly what a valid config needs, including that every opex line must belong to exactly one category. Propose categories by match_contains on common words and put unmatched lines in a clearly named fallback category. Mark every guess.
- Copy the workbook to data/clients/<ID>/source/. Never modify the original.
- Validate the draft: load it with the real config loader, run list-periods, analyze the latest period, and check reconciliation. Only if all of that passes write config/clients/<ID>.yaml. Otherwise write config/clients/<ID>.draft.yaml and print each problem. Never overwrite an existing config without `--force`.
- The YAML starts with a comment block listing each detected value, how confident it is, and a "needs your decision" list. Be honest: if something was ambiguous say so rather than picking silently.
- Print a short plain-language summary of what was detected and the next command to run.
- Tests: for test_clean, the generated config must give the same metric values as the hand-written config/clients/test_clean.yaml for the same period (compare values; ignore timestamps, paths and hashes). Try the Company A and Company B workbooks too; where detection cannot handle a layout, report that plainly instead of forcing it. A non-financial or unreadable workbook must fail with a clear message.
- Document the command in SKILL.md, docs/architecture.md, README.md and docs/requirements_checklist.md.

## Task 7: final verification
Run the full tests, the engine evals, and the free deterministic Level 2 comparison (no paid evals). Verify the ABC SHA-256 against scripts/reference_hashes.json. Run `git status --short` and confirm no secret file is untracked and unignored. Run these by hand and show the key lines of output:
  python -m finance_engine report --client test_clean --scope year --year 2025 --provider deterministic
  python -m finance_engine report --client test_errors --scope year --year 2025 --provider deterministic
  python -m finance_engine report --client test_clean --scope all --provider deterministic
  python -m finance_engine list-periods --client test_clean --json

## Final report (short)
A table: task, status, files changed, tests added, decisions needed from me. Then the new test total, the golden case count, the SHA result, any difference from the baselines (explain each), and the git status summary.