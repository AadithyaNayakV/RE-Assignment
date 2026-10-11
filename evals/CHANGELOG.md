# What the evals caught

Every real defect the eval suite found while it was being built, with the case that found
it and the before/after. "Real" means it changed code or data outside the eval suite, or it
was a fault in the suite that would have produced a wrong verdict. Dated 2026-10-11, all
on top of commit `a18e32d`.

## Defects in the product

### 1. A malformed period crashed with a Python traceback
- **Found by:** case 12 (`month_out_of_range`, `malformed_period`), Level 1.
- **Before:** `analyze --period 2026-13` raised `ValueError: Month out of range` as a full
  traceback. The exit code was 1 only because the interpreter died. An existing test
  (`tests/test_cli.py::test_malformed_period`) asserted the `ValueError`, so the suite
  had locked the crash in.
- **After:** `error: Month out of range in '2026-13'. Use --period YYYY-MM, e.g. 2026-06.`
  on stderr, exit 1, no traceback (`finance_engine/cli.py`, `UsageError`). The test now
  asserts the clean message.

### 2. The report did arithmetic outside the engine
- **Found by:** case 13, Level 1 `report_guard`. This was the first time the guard ran over
  a *whole* `report.md`; until then it had only checked the commentary section.
- **Before:** `report.py` built the Month-over-month table's prior-month column as
  `current - change`. That put five figures in every report that are not in metrics.json.
  ABC June 2026: `$208,350.96`, `$68,669.53`, `$139,681.43`, `$478,781.03`,
  `-$339,099.60`. Guard on ABC's report: **REJECTED, 6 of 331** (these five plus #3).
- **After:** the engine emits `<base>.prior` (`prior_period_value_v1`) for the five
  headline measures and the report reads it. Guard: **PASS, 331/331**. The values agree
  with the workbook's own May 2026 totals. Documented in `docs/metrics_schema.md`;
  additive, so `schema_version` stays 1.

### 3. The guard read "SHA-256" and "sha256" as the number 256
- **Found by:** case 13 Level 1 (`SHA-256:` in the report header). Then case 01 Level 2:
  Claude's report wrote `sha256 75da…`, `check-commentary` **REJECTED 2 of 341**, and
  Claude spent about 8 turns investigating the guard instead of finishing.
- **Before:** only `sha256<hash>` with no space was masked.
- **After:** the `SHA-?256` label is masked. Claude's final check: **PASS, 339/339**.
  The skill now says to rephrase a rejected non-financial token instead of debugging the
  guard.

### 4. The guard read timestamps, hash tails and its own counts as figures
- **Found by:** the Level 2 deterministic control (`--provider deterministic`) on cases
  02–11.
- **Before:** `Generated: 2026-10-11T10:48:47` left `48` and `47`. A digest ending
  `…a5847036` yielded `5847036`. "30/30 numbers traced" and "All 30 numbers in this
  section" quoted the guard's own result back. Each synthetic report failed with 6–9
  unsupported numbers. ABC passed only by coincidence: its 888 allowed values happen to
  include 47, 48 and 30.
- **After:** ISO timestamps, hex digests of 16+ characters and the guard's own count
  phrases are masked. Every deterministic report passes the guard on its full text.

### 5. The guard checked signs, despite documenting that it does not
- **Found by:** the Level 2 deterministic control. `### Warnings (2)` was parsed as −2.
- **Before:** every allowed value was also stored as its absolute value, but the written
  value was matched *with* its sign. So `-5.98%` against a stored `5.98` passed, while
  `(2)` against a stored `2`, or `-$195,898.16` against `$195,898.16`, failed. That
  contradicts the module docstring and `docs/metrics_schema.md` ("magnitudes are
  compared, not signs").
- **After:** `|written|` is matched, as documented. New tests confirm that a wrong figure
  is still rejected whatever its sign (`$195,998.16` and `-$195,998.16` both fail).

### 6. The report printed a "100.0%" that is not a metric
- **Found by:** the Level 2 deterministic control (cases 02, 03, 06, 08, 09, 10, 11).
- **Before:** `report.py` hard-coded `100.0%` in the share column of the Total revenue
  and Total opex rows.
- **After:** `—`. The share column holds only metric values.

## Defects in the eval suite itself

These would have produced wrong verdicts. The control run and the first real Claude run
exposed them.

| # | Fault | Found by | Fix |
|---|---|---|---|
| 7 | `impossible_values` matched the rule text "never as an infinite percentage" | deterministic control, all cases | negated sentences are exempt |
| 8 | Patterns too narrow for valid wording: "(+$2,800.00", "Total Assets equals Total Liabilities and Equity", a one-off named on the line after its heading | deterministic control, cases 02, 09, 11 | patterns widened; still fail on the bad cases in `tests/test_eval_checks.py` |
| 9 | Hard-wrapped lines were checked separately, splitting a claim from its hedge ("No movement … is" / "described as seasonal.") | first Claude run, case 01: 2 false failures | lines joined into paragraphs before sentence split; `if` counts as a hedge |
| 10 | Only `Bash` calls were counted, but Claude Code on Windows runs commands through `PowerShell` | first Claude run, case 01: `ran_analyze` false failure | both shells counted; the skill's `allowed-tools` lists both |
| 11 | Headless runs inherited the operator's `auto` permission mode, so Claude could run ad-hoc `python -c` | first Claude run, case 01 transcript | `--setting-sources project --permission-mode default`: graded on the skill's declared tools only |
| 12 | `claude.exe` not found from Python (winget path only on the bash PATH) | first attempt; no Claude call made | `--claude-bin`, `$CLAUDE_BIN`, then known install paths |
| 13 | `forbidden:eleven_month_ttm` matched the report stating the rule: "No TTM, annualised or run-rate figure is given." | full Claude run, case 06: the run's only failure (12/13) | negated sentences exempt; the positive claim is still caught |

Faults 7–11 and 13 were fixed in the checker, then the saved report was re-graded with
`--recheck`, with no new Claude call. Each fix has a test in `tests/test_eval_checks.py`
showing the check still fails on the fault it exists for, so a check was never loosened
until it passed.

## Found in review of the ABC Jun 2026 report (2026-10-11, on top of `fd60402`)

| # | What | Kind | Before | After |
|---|---|---|---|---|
| 14 | A cause stated as fact: "swung from a charge to a credit: a reversal, not a cost saving" | gap the evals now catch | no check; engine warnings and `report.py` also said "typically a recovery or reversal" and "treat a reversal as a cost saving" | wording in `validation.py`, `report.py`, `abc_inc.yaml`, `metric_definitions.md` now says "may be … needs confirmation"; SKILL.md rule; Level 2 `cause_label_hedged` fails "is a reversal" / ": a reversal" without a hedge |
| 15 | `negative_opex_line` said negative lines "typically mean a recovery or reversal", which is wrong for Interest Income and Income Tax Benefit | bug | ABC warning named Bad Debts and Interest Income; reached 18 metrics | config `expected_negative_lines` (ABC: Interest Income, Income Tax Benefit); warning names Bad Debts only, hedged, 15 metrics; tests in `test_validation.py::TestExpectedNegativeLines`; Level 1 case 01 `warning_names` / `warning_omits` / `warning_wording` |
| 16 | Direction words were never checked; "fell -$12,452.80" double negatives | gap the evals now catch | guard compared magnitudes only | guard rejects a direction word that contradicts the sign of the change it sits next to ("loss narrowed" inverts; "fell to $X" is a level); tests in `test_guard.py::TestDirectionWords`; Level 2 `direction_amounts_unsigned`; template rules |
| 17 | Reviewer questions mixed changes and levels without labels; no explanation of operating margin worsening while the loss narrowed | gap the evals now catch | — | Level 2 `reviewer_questions_label_figures`; template rule; the ABC report adds one sentence explaining the margin |

Follow-up, same review. The first headless ABC run after #14–#17 scored 42/46. Two of
its four failures were real skill behaviour, both caught by the new checks: "rose
+3.1 pts" and unlabelled figures in the reviewer questions. SKILL.md now spells out both
rules with examples. The other two failures came from the Pro session limit ending the
run before `check-commentary`. That run had also wasted three turns on chained shell
commands, so SKILL.md now asks for one command per call. Re-run: **46/46**.
