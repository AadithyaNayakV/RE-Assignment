# evals

Golden cases (`cases/`), the two runners, saved baselines (`baseline/`) and run results
(`runs/`, git-ignored). `python -m finance_engine list-evals` lists every case and its
newest saved results.

- How grading works: `grading.md`
- What the evals caught: `CHANGELOG.md` (full log) and `docs/what_evals_caught.md` (short list)
- The 2026-10-11 results, kept as a historical record: `evaluation_report.md`

## What is and isn't graded today

| What | Command | Cost | Latest result |
|---|---|---|---|
| Engine, Level 1 | `python evals/run_engine_evals.py --compare` | Free. Run it on every change | 20/20 pass (2026-10-15) |
| Deterministic commentary, Level 2 control | `python evals/run_skill_evals.py --provider deterministic --compare` | Free. Run it on every change | 18 pass, 2 skip (2026-10-15). Case 12 is skill-only; case 20 has no commentary step |
| Claude-written commentary, Level 2 | `python evals/run_skill_evals.py` (all cases) or `... 04` (one case) | Costs money: it uses Claude Pro usage, and prints the API-equivalent cost at the end | See below |
| Level 3 rubric | `python evals/run_skill_evals.py 01 --rubric` | Costs money | Not run yet |

**Claude-written commentary, the truth as of 2026-10-14:**

- **Run once, on 2026-10-11, for the 13 cases that existed then.** As first graded,
  12 of 13 passed. After a false positive in the checker was fixed, 13 of 13 passed
  (`runs/skill_2026-10-11T11-03-32Z`, baseline `skill_2026-10-11_a18e32d-dirty.json`).
  Case 01 was then run again after the review fixes: 46/46
  (`runs/skill_2026-10-11T14-50-30Z`).
- **Those reports do not pass today's checks.** Several checks were added after those
  runs:
  - `direction_amounts_unsigned` and `reviewer_questions_label_figures`, on 2026-10-11
    after the full run;
  - `causal_words_outside_questions`, on 2026-10-12.

  Re-grading the saved reports with `--recheck` on 2026-10-14 (no Claude call):
  - The full run: 1 of 13 pass.
  - The case 01 rerun: 46 of 47 checks pass, so the case fails. The failing check is
    `causal_words_outside_questions`.

  `list-evals` still shows "claude pass" for cases 01 to 13 because it reads that old
  baseline.
- **Not run through Claude yet:** cases 14 to 17. In a Claude run, cases 13, 18 and 19
  are graded on the template report (`mode: deterministic` in their `case.yaml`), so
  Claude writes no commentary for them. Case 20 (the QuickBooks round trip) has no
  commentary step at all.

So no Claude-written report has passed the current checks yet. To get a current answer,
run the paid command yourself:

```bash
python evals/run_skill_evals.py            # every case; Claude is called for 01-12 and 14-17
python evals/run_skill_evals.py 01 04      # or a few cases
```

It ends with a pass/fail table (case, result, checks passed, who wrote the report,
Claude cost) and the paths where it saved `results.json` and each case's `report.md`,
`metrics.json` and transcript. To make that run the new comparison point, add
`--save-baseline`. Exit status: 0 only if every case passed.

To re-grade saved reports after changing a check, without calling Claude:
`python evals/run_skill_evals.py --recheck evals/runs/<run>`.
