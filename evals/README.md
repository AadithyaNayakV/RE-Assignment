# evals

Golden cases (`cases/`), the two runners, saved baselines (`baseline/`) and run results
(`runs/`, git-ignored). `python -m finance_engine list-evals` lists every case and its
newest saved results.

- How grading works: `grading.md`
- What the evals caught: `CHANGELOG.md` (full log) and `docs/what_evals_caught.md` (short list)
- The 2026-10-11 results, kept as a historical record: `evaluation_report.md`

## Saved baselines

`--compare` diffs a run against a saved baseline in `baseline/`. The newest of each kind:

| Kind | File | Saved | Result in it |
|---|---|---|---|
| Engine, Level 1 | `engine_2026-10-15_72095b6-dirty.json` | 2026-10-15 | 20/20 pass |
| Deterministic commentary, Level 2 control | `skill_deterministic_2026-10-15_72095b6-dirty.json` | 2026-10-15 | 18 pass, 2 skip |
| Claude-written commentary, Level 2 (paid) | `skill_2026-10-11_a18e32d-dirty.json` | 2026-10-11, **not re-saved since** | 13/13 for cases 01 to 13 as re-graded then; those reports do not pass today's checks (see below) |

The 2026-10-15 files end in `-dirty` because the tree held uncommitted documentation
changes when they were saved; the code was commit `72095b6`. Against them,
`run_engine_evals.py --compare` reports "no change". The 2026-10-11 baselines are kept.
`list-evals` reads the newest file of each kind, so its Claude column still comes from the
2026-10-11 file.

**Known issue:** for a paid run, `run_skill_evals.py --compare` picks the newest
`skill_*.json`, a pattern that also matches `skill_deterministic_*.json`, so it compares a
Claude run against the deterministic baseline. Until that is fixed, name the Claude
baseline yourself:
`python evals/run_skill_evals.py 01 --compare --baseline evals/baseline/skill_2026-10-11_a18e32d-dirty.json`.

## What is and isn't graded today

| What | Command | Cost | Latest result |
|---|---|---|---|
| Engine, Level 1 | `python evals/run_engine_evals.py --compare` | Free. Run it on every change | 20/20 pass (2026-10-15) |
| Deterministic commentary, Level 2 control | `python evals/run_skill_evals.py --provider deterministic --compare` | Free. Run it on every change | 18 pass, 2 skip (2026-10-15). Case 12 is skill-only; case 20 has no commentary step |
| Claude-written commentary, Level 2 | `python evals/run_skill_evals.py` (all cases) or `... 04` (one case) | Costs money: it uses Claude Pro usage, and prints the API-equivalent cost at the end | Case 01 passed 47/47 on 2026-10-15 after the causal-word fix (`runs/skill_2026-10-15T07-54-57Z`, about $0.52 API-equivalent). The other Claude cases have not been re-run against the current checks. See below |
| Level 3 rubric | `python evals/run_skill_evals.py 01 --rubric` | Costs money | Not run yet |

**Claude-written commentary, the truth as of 2026-10-15:**

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
- **2026-10-15:** the paid run of case 01 failed 46/47 on `causal_words_outside_questions`
  (Claude wrote "because" outside the reviewer questions;
  `runs/skill_2026-10-15T06-13-34Z`). The guard's word list and the eval check now share
  one list, the skill's wording rules got examples, and a test was added. The rerun passed
  47/47 (`runs/skill_2026-10-15T07-54-57Z`). One run only; output varies between runs.
- **Not run through Claude yet:** cases 14 to 17. In a Claude run, cases 13, 18 and 19
  are graded on the template report (`mode: deterministic` in their `case.yaml`), so
  Claude writes no commentary for them. Case 20 (the QuickBooks round trip) has no
  commentary step at all.

So one case (01) passes the current checks with a Claude-written report; the other
Claude cases have not been re-run against them. To get a current answer, run the paid
command yourself:

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
