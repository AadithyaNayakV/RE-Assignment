# PHASE 5A: SMALL FIXES AND HONESTY PASS

Read CLAUDE.md first. Do not break anything that already works. Do ONE task at a time, in the order below. After each task run the full test suite. If a test that passed before now fails, fix it or revert that task before moving on.

Baseline before you start: 650 passed, 3 skipped. Record your own baseline first and report the new total at the end.

## Hard rules
- Do NOT commit or push. Leave changes in the working tree.
- Do NOT run `claude -p` skill evals or anything that costs money.
- Never print .env contents or tokens. Never open or echo .env files.
- The original ABC workbook (SHA-256 75da8d679a89551f0e46b3563b7cfc724d9aaeabe0444b0214d455a0b335a0ea) must stay byte-identical. Verify at the end.
- The engine's computed values must not change. If any existing golden case, expected.json or test output changes because of your work, STOP and tell me which one and why. Do not edit expected files to make tests pass.
- Do not mention any feature as existing unless it is built and tested.

## Task 1: tidy the test-workbook generator
`make_test_files.py` is in the repo root (it builds data/clients/test_clean and test_errors). Move it to `scripts/make_test_workbooks.py`, keep behaviour identical, and make sure running it from the repo root still writes the same two files. If the file is not in the repo, say so and skip this task.

## Task 2: remove the duplicated n/a text in the report
The deterministic report prints `Year on year: n/a (no comparable value) (n/a (no comparable value))`. Find where it is built (likely finance_engine/report.py) and print the reason once. Add a test. Check for the same duplication anywhere else in the report.

## Task 3: `--period latest`
For analyze, report, brief and check-commentary, accept `--period latest`, which resolves to the newest period in the source (use the same logic as list-periods). Print the resolved period, for example `period: latest -> 2026-01`. An explicit YYYY-MM must behave exactly as before. Add tests, including a source with a gap in months.

## Task 4: cell-level data-quality checks
Today a text cell like "n/a" or a blank inside a data row is only caught indirectly, as a reconciliation failure. Add explicit data-quality issues that name the cell. Find where issues are produced (likely finance_engine/validation.py).

New issue codes, all severity "warning", all with the existing fields plus an optional additive field `cells`: a list of {sheet, cell, label, period, raw_value}.
- `non_numeric_cell`: a period cell on a line-item row that holds text which is not a number. Do NOT flag whatever the engine already treats as an accounting zero (for example "-"); find and reuse the current rule. Message: states the cell address and that the value is treated as missing, not zero.
- `blank_cell`: a line item has a value in the previous and the next period but is blank in this one.
- `negative_line_item`: a negative value on a revenue, COGS or operating-expense line item. Message must say it may be a legitimate credit and needs confirmation.

Each issue's `affects` must name the totals that depend on it (for example opex.total for that period). These are warnings only: do not change any computed value and do not block metrics.

Make the three checks switchable per client under `data_quality.cell_checks` in the YAML, default on. If switching them on changes ANY existing golden case or test output, stop and report.

Tests: build tiny workbooks inside the test using tmp_path (do not depend on data/clients/test_errors). Cover: one text cell, one internal blank, one negative, and a clean workbook that produces none of the three codes. Also confirm the deterministic report lists the new warnings, and regenerate `test_errors` for 2025-10 and 2025-11 and show me the new lines.

## Task 5: skill wording and stopping rule
In .claude/skills/monthly-financial-analysis/SKILL.md:
- Add an explicit stopping rule next to step 9: if check-commentary still fails after the second attempt, run the deterministic fallback command, say so in the report, and STOP. Never hand-edit metrics.json, never loop a third time.
- Add a writing rule: causes appear ONLY as questions in "Questions for the reviewer", worded as possible and needing confirmation. Outside that section do not use "because", "due to", "caused", "the reason" or "driven by". Say where a change sits ("the increase is in Subscriptions") instead of why.
Add the same causal phrases to the forbidden-claims list in evals/report_checks.py with unit tests. First confirm the deterministic report text does not contain them. If it does, tell me and stop on this item.

## Task 6: documentation honesty
- `init-client` does not exist. Find every mention (use grep over *.py and *.md): SKILL.md, docs/architecture.md, the demo checklist (item 11b), docs/requirements_checklist.md, eval case 12b. Replace each with the real manual procedure (copy an existing config/clients/*.yaml and edit it) and mark the command as [PLANNED]. If eval case 12b depends on the missing command, mark it skipped with that reason. Do not make it pass trivially.
- Tool counts: the QuickBooks server registers 142 tools, 77 visible, 65 hidden. Search architecture.md, README, docs/quickbooks_setup.md, requirements_checklist.md, the demo docs and CLAUDE.md for conflicting numbers (for example 71 + 71) and make them consistent.
- QUICKBOOKS_TOKEN_STORE_PATH: check the installed server's own source or README (never its .env) for this variable. If you cannot verify it, remove the claim from our docs.
- In docs/architecture.md add a clearly marked [PLANNED] line for per-client reviewer notes (memory).

## Task 7: final verification
Run the full tests and the engine evals (not the paid skill evals). Verify the ABC workbook SHA-256. Run `git status --short` and check that no .env, token, or key file is untracked-but-not-ignored.

## Final report (keep it short)
A table: task, status, files changed, tests added, anything needing my decision. Then the new test total, the SHA result, and the git status summary.