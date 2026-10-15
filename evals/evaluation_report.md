> Historical record from 2026-10-11; the suite now has 20 cases. See evals/README.md.

# Evaluation report

Actual results, 2026-10-11, on commit `a18e32d` plus the uncommitted Phase 3 changes,
hence the `-dirty` suffix on the baseline files. Every number below comes from a results
file under `evals/runs/` or `evals/baseline/`. Nothing is estimated.

## Summary

| Run | Result | Evidence |
|---|---|---|
| Level 1 engine, 13 cases | **13 / 13 pass**, 0 skipped, ~28 s | `evals/baseline/engine_2026-10-11_a18e32d-dirty.json` |
| Level 2 deterministic control, 13 cases | **12 / 12 pass**, 1 skipped (case 12 is skill-only) | `evals/baseline/skill_deterministic_2026-10-11_a18e32d-dirty.json` |
| Level 2 Claude skill, 13 cases, **as first graded** | **12 / 13 pass**: case 06 failed one check | `evals/runs/skill_2026-10-11T11-03-32Z/results.json` |
| Level 2 Claude skill, same reports **re-graded** after fixing that false positive | **13 / 13 pass** | `evals/baseline/skill_2026-10-11_a18e32d-dirty.json` (`rechecked_from` names the original run) |
| Level 3 rubric | **not run** | optional; see below |
| Regression demo, data break | **detected** | `evals/runs/regression_demo_2026-10-11T11-04-13Z/` |
| Regression demo, code break | **detected, then reverted** | `evals/runs/regression_demo_2026-10-11T11-22-01Z/` |
| pytest | **611 passed, 2 skipped** (the two opt-in QuickBooks tiers) | `python -m pytest -q` |

No valid committed baseline exists yet. The three baselines above were taken from an
uncommitted tree and are named `-dirty` so they cannot be mistaken for a commit. After
committing, re-save them with `--save-baseline`.

## Level 1: engine

All 13 cases pass. 754 checks in total; per case:

| Case | Checks | Notes |
|---|---:|---|
| 01 ABC Inc, Jun 2026 | 57 | Values from the workbook's cached formula totals and spot cells. 144/144 reconciliation. |
| 02 normal growth | 64 | Nothing significant, as designed. |
| 03 revenue decline | 59 | |
| 04 expense spike (Jan 2026) | 64 | Legal Expenses significant (+$34,000.00, +1,700%) and a possible one-off (18.0x). |
| 05 margin decline | 54 | |
| 06 missing months | 56 | TTM null with `incomplete_window`; exit 2 as designed. |
| 07 zero / negative bases | 71 | 4 % changes null with `zero_base` / `negative_base`. |
| 08 seasonal (Jan 2026) | 59 | Retail Revenue significant; seasonality not assessed. |
| 09 one-time expense (Nov 2025) | 61 | Legal Settlement significant and a possible one-off; TTM n/a. |
| 10 multiple revenue streams | 106 | All six streams' values, shares and changes. |
| 11 balance sheet | 68 | A = L + E; `balance_sheet_balances` ran and passed. |
| 12 unknown client / bad period | 18 | 5 CLI calls: right exit code and message, no traceback. |
| 13 deterministic fallback | 17 | Falls back from `ollama`, labelled `deterministic`, whole report passes the guard. |

## Level 2: the skill, through Claude Code

Run: `python evals/run_skill_evals.py` → `evals/runs/skill_2026-10-11T11-03-32Z/`.
Model `claude-opus-5-5`. Headless with project settings only, default permission mode,
no MCP servers, and tools limited to `python -m finance_engine …`, Read, Write, Edit,
Glob and Skill.

| Case | First grading | Checks | Turns | Seconds | API-equiv. $ |
|---|---|---:|---:|---:|---:|
| 01 ABC Inc | PASS | 43/43 | 17 | 107 | 0.51 |
| 02 normal growth | PASS | 37/37 | 13 | 62 | 0.32 |
| 03 revenue decline | PASS | 36/36 | 14 | 75 | 0.33 |
| 04 expense spike | PASS | 38/38 | 14 | 94 | 0.37 |
| 05 margin decline | PASS | 36/36 | 16 | 88 | 0.37 |
| 06 missing months | **FAIL** | 36/37 | 15 | 79 | 0.37 |
| 07 zero / negative bases | PASS | 38/38 | 24 | 121 | 0.50 |
| 08 seasonal | PASS | 35/35 | 17 | 94 | 0.42 |
| 09 one-time expense | PASS | 37/37 | 14 | 67 | 0.35 |
| 10 multiple streams | PASS | 39/39 | 14 | 81 | 0.38 |
| 11 balance sheet | PASS | 34/34 | 14 | 73 | 0.34 |
| 12 stop: unknown client | PASS | | 4 | 15 | 0.09 |
| 12 stop: month 13 | PASS | 10/10 for the three | 4 | 16 | 0.09 |
| 12 stop: 2027-01 not in source | PASS | | 4 | 16 | 0.09 |
| 13 deterministic fallback | PASS | 29/29 | no Claude call | | 0 |
| **Total** | **12 / 13** | | | ~19 min | **4.27** |

The dollar figure is the CLI's `total_cost_usd`: an API-equivalent measure of Pro-plan
usage, not a charge.

**The case 06 failure was the checker's, not the report's.** The report said "No TTM,
annualised or run-rate figure is given.", which states the rule. The
`forbidden:eleven_month_ttm` pattern matched "annualised" and had no negation exemption.
Fixed (CHANGELOG #13), with a test that the claim "The annualised figure is shown…" is
still caught. The same saved reports were then re-graded with `--recheck` (no new Claude
call): 13 / 13.

Every Claude run, from the transcripts:
- discovered the skill at start-up and invoked it through the `Skill` tool;
- ran `analyze`, `brief` and `check-commentary`, and finished with a guard **PASS**;
- never read the raw metrics.json (`read_raw_metrics_json: false` in every case);
- for case 12, wrote nothing and explained why. For example: "I stopped without writing
  a report: the client `acme_widgets` has no config… These clients do…", and
  "**2026-13 isn't a valid period** because there is no month 13."

An earlier single-case run of 01 (`evals/runs/skill_2026-10-11T10-56-06Z/`, 21 turns,
$0.79) is where CHANGELOG #3 and #9–#11 were found. As first graded it scored 39/43; after
the checker fixes, the same report re-grades at 43/43.

## Level 3: rubric

Not run, to save Pro usage. It is wired up (`--rubric`, rubric text in `grading.md`) and
never affects pass/fail. My own reading of the ABC report (01): data quality and
limitations come first; every figure matches the brief; each of the ten possible causes is
a specific question ending "Needs confirmation". A reviewer would still edit:
- the double-negative style "fell -$12,452.80";
- one comparative inference: that the revenue fall "sits almost entirely in the largest
  stream". The figures are verified; the comparison is Claude's.

That second kind of statement is exactly what Level 2 cannot see.

## Regression detection

**Data break, on a test copy.** `python evals/regression_demo.py data`. One cached formula
result in a *copy* of case 04's workbook, Total Operations Payroll Jan 2026 (cell H26),
changed from 62,650.00 to 63,650.00. Line items untouched; repository untouched.

```
FAIL  04_expense_spike  (61/64 checks)
  x input_unchanged: workbook sha256 a0e7ed27819bfd91... vs expected 33387b8cd67f190a...
  x exit_code: exit 2, expected 0
  x reconciliation: 106/108 passed; opex_items_vs_total 2026-01 diff 1000.0;
                    opex_group_subtotals 2026-01 diff -1000.0
same case, real workbook:  PASS  04_expense_spike  (64/64 checks)
```

Every value check still passed, correctly: the engine sums line items and never trusts the
workbook's subtotal. Only reconciliation could see this fault, and it did.

**Code break, temporary.** `python evals/regression_demo.py code`. In
`finance_engine/model.py`, `range(months - 1, -1, -1)` became `range(months, 0, -1)`, so
the TTM window ends one month early: a plausible-looking off-by-one.

```
1 passed, 12 failed
Compared with baseline a18e32d: NEWLY FAILING: 01, 02, 03, 04, 05, 06, 07, 08, 09, 10, 11, 13
  e.g. 01 value:revenue.total.ttm: got 2298633.0, expected 2303975.51 (diff 5342.51)
       04 code_present:ttm_includes_possible_one_offs: the January spike left the window
reverted finance_engine/model.py: byte-identical = True
after revert: 13 passed, 0 failed; compared with baseline: no change.
```

Case 12 does not touch TTM, so it rightly kept passing. `git diff finance_engine/model.py`
is empty after the demo.

## Remaining limitations

- **The guard checks existence, not meaning.** A correct figure in the wrong sentence
  passes, and loosely rounded percentages collide (~15% false-pass, measured). Level 2's
  `figure:` checks look for the brief's exact strings, which narrows this but does not
  close it.
- **Claim checks are pattern-based.** They catch the causes, seasonality and one-off
  wording they name. An invented cause in words outside the lexicon would pass. The
  reviewer checklist exists for that.
- **One run per case.** Claude's output varies between runs. A single pass is evidence,
  not a guarantee. Repeat runs (`python evals/run_skill_evals.py 04` several times) would
  measure the variance; that was not done, to save usage.
- **The checker was corrected during this work** (CHANGELOG #7–#13). Every correction
  fixed a false failure, each has a test proving the check still catches the fault, and
  no case was made to pass by deleting a check.
- **ABC cases skip without the workbook.** It is git-ignored client data. On a fresh clone
  cases 01 and 13 report SKIP, never PASS.

## Update: review of the ABC Jun 2026 report (same day, on top of `fd60402`)

Four review findings were fixed (CHANGELOG #14–#17) and four checks added: Level 2
`cause_label_hedged`, `direction_amounts_unsigned` and `reviewer_questions_label_figures`,
plus a guard direction check that also runs in Level 2's `guard`. Level 1 case 01 gained
`warning_names`, `warning_omits` and `warning_wording`.

| Run | Result | Evidence |
|---|---|---|
| pytest | **637 passed, 3 skipped** | `python -m pytest -q` |
| Level 1 engine, 13 cases | **13 / 13**; case 01 now 61 checks | `evals/baseline/engine_2026-10-11_fd60402-dirty.json` |
| Level 2 deterministic control | **12 / 12**, 1 skipped | `evals/baseline/skill_deterministic_2026-10-11_fd60402-dirty.json` |
| The previous ABC report, under the new checks | **36 / 39**: exactly the three new checks fail | before the fixes |
| ABC skill eval, first attempt | **42 / 46, FAIL** | `evals/runs/skill_2026-10-11T12-36-29Z/` |
| ABC skill eval, after tightening SKILL.md | **46 / 46, PASS**: 13 turns, 92 s, $0.50 API-equivalent | `evals/runs/skill_2026-10-11T14-50-30Z/` |
| Regenerated ABC report (`output/clients/abc_inc/2026-06/report.md`) | guard **PASS 313/313**, 23 direction words checked; Level 2 **39/39** | |

**The first skill attempt failed for two separate reasons.**

- **The Pro session limit cut the run off** after it wrote the report and before
  `check-commentary` ("You've hit your session limit"). That is why `claude_completed` and
  `ran_check_commentary` failed. It was not a skill fault. The run had also spent three
  turns on chained commands that default permission mode refuses. SKILL.md now asks for
  one command per call.
- **The report itself broke two of the new rules**: "rose +3.1 pts" and six unlabelled
  figures in the reviewer questions. The new checks caught both. SKILL.md now states both
  rules with examples, and the re-run passed 46/46.

**The skill baseline was not re-saved.** `skill_2026-10-11_a18e32d-dirty.json` is still the
newest full Claude run. A one-case run should not replace a thirteen-case baseline, and a
full re-run would spend about $5 of Pro usage. Re-save it with
`python evals/run_skill_evals.py --save-baseline` when that is worth it.
