# Grading

Three levels. Levels 1 and 2 are deterministic and decide pass/fail. Level 3 is an
optional opinion and never decides anything on its own.

| Level | What is graded | Runner | Cost | Decides pass/fail |
|---|---|---|---|---|
| 1 | The engine's `metrics.json` against independently derived values | `evals/run_engine_evals.py` | free, ~40 s for 19 cases | **yes** |
| 2 | The `report.md` the skill writes (or the deterministic fallback) | `evals/run_skill_evals.py` | Pro usage per case; free with `--provider deterministic` | **yes** |
| 3 | Clarity and usefulness, scored 1–5 by a read-only Claude judge | `run_skill_evals.py --rubric` | Pro usage | **no**, advisory only |

## Pass thresholds

- **A case passes only if every one of its checks passes.** There is no partial credit
  and no weighted score. A financial report with one wrong number is wrong.
- **A run passes only if every non-skipped case passes.** A skipped case is reported as
  SKIP, never counted as a pass. Cases 01 and 13 skip on a machine without the ABC
  workbook, which is git-ignored client data; they are the only cases that need a file
  that is neither in git nor generated. Cases 14 to 18 read workbooks under
  `data/clients/` (also git-ignored) that `scripts/make_test_workbooks.py` generates: both
  runners and the pytest session write any that are missing (never touching an existing
  one), so they do not skip on a fresh clone. Their bytes carry a creation time, so
  `input_unchanged` for these cases compares a fingerprint of the cell contents
  (`input_content_sha256`, `evals/common.py::workbook_fingerprint`), not the file hash.
- **Regression:** `--compare` fails the run if any case newly fails, or any check flips
  from pass to fail, against the newest baseline in `evals/baseline/`. Changed values are
  listed even when every check still passes, so a drift inside tolerance is still seen.
- **The deterministic control must pass Level 2.** `run_skill_evals.py --provider
  deterministic` grades the template report with the same checks. A check that fails on
  the template is a broken check and is fixed before any Claude run is graded with it.
- **Level 3:** no threshold. A score below 3 on either axis is flagged for the human
  reviewer to read, and does not fail the case.

## Level 1: engine

Each case runs the real CLI in a subprocess (`python -m finance_engine analyze ...`),
writing to `evals/runs/` and never to `output/`, then checks:

| Check | Tolerance / rule |
|---|---|
| `input_unchanged` | Workbook sha256 equals the one the expected values were built from; for a generated workbook (cases 14 to 18), the fingerprint of its cells. |
| `exit_code`, `no_traceback`, `stderr_message` | Exact exit code. No Python traceback, ever. |
| `value:<metric>` | Currency within **0.01** (one cent, the reconciliation tolerance); percent and points within **0.0005**. |
| `n/a:<metric>` | `value` is null **and** `unavailable_reason` is the expected one (`zero_base`, `negative_base`, `sign_change`, `incomplete_window`). |
| `ttm_window`, `ttm_complete` | Exact. |
| `reconciliation`, `reconciliation_check:<key>` | `all_passed` as expected; named checks ran and passed. |
| `code_present`, `code_absent`, `max_errors` | Data-quality codes. |
| `significant:<label>`, `possible_one_off:<label>`, `ttm_window_one_off:<label>`, counts | The flags a reader depends on. |
| `seasonality_assessed` | Exact. |
| Case 12 `cli_checks` | Each bad input: exit code, message, no traceback, no output for an unknown client. |
| Case 13 `report_*` | Fallback warning, report labelled `deterministic`, the whole report passes the guard. |
| `coverage:<key>`, `field:<path>` | Year and whole-file cases: the coverage status, label, counts and gaps, and the highest / lowest months, exactly. |
| `status:<metric>`, `issue_detail:<code>:<key>` | A total blocked over a gap is `blocked`; the error names the missing month. |
| `no_metric_matching:<pattern>` | No metric named like an annualised, extrapolated or run-rate figure. |
| `series_points` | Whole file: seven measures, one point per month. |
| `scope`, `source_filename`, `input_unchanged:<file>` | The document's scope and the workbook it says it read; every input workbook unchanged. |
| Case 18 `extra_runs` | File selection: a period in both workbooks stops (exit 1, both named, nothing written); a period in neither stops with each file's periods; a period in one is read from it; `--file` forces one, and its month-over-month figures are `n/a (in_other_file)`. |

**Where expected values come from.** `evals/tools/build_expected.py` writes every
`expected.json`. It imports nothing from `finance_engine` and refuses to finish if
`finance_engine` was imported:

- **Synthetic cases:** the generator's own inputs (`scripts/generate_synthetic.py`).
  Margins, % changes, shares and TTM sums are derived by hand-written arithmetic
  following `docs/metrics_schema.md`.
- **QuickBooks snapshot (case 19):** the raw Profit and Loss JSON
  (`tests/fixtures/qb_pl_monthly.json`), read with `json` alone. Each section is the sum
  of every account amount under it, nested accounts and parent accounts' own amounts
  included, a blank cell counted as QuickBooks' zero; each sum must equal Intuit's own
  total before `expected.json` is written. `input_unchanged` compares the file's sha256.
- **Test workbooks (cases 14 to 18):** the cells of the generated workbook, read with
  openpyxl. Section membership, line-item sums, categories, year totals, quarters, year on
  year, extremes and the cell-level warnings are re-derived in `build_expected.py` from
  the rules in `docs/metrics_schema.md`. Case 14 also states its totals in `case.yaml`
  (they equal test_clean's 2025-12 TTM figures), and the builder refuses to write values
  that disagree.
- **ABC Inc:** the workbook's own cached formula totals (Total Income, Total COGS, Gross
  Profit, Total Operating Expenses, Net Income, the subtotal rows, the balance-sheet
  totals), plus single cells read as spot checks. `manual_spot_check` in
  `evals/cases/01_abc_inc_2026_06/expected.json` lists cells to confirm by hand in Excel.

Each value carries a `derivation` string saying exactly how it was obtained.

## Level 2: the report

`evals/report_checks.py`, applied to whatever wrote the report.

### Rubric dimensions and the checks that cover them

| Dimension | Deterministic checks |
|---|---|
| **Numerical / factual correctness** | `guard`: every number traces to metrics.json at the precision written. `figure:<metric>`: the case's key figures appear in the brief's format. `forbidden:*` direction checks, e.g. "revenue grew" when it fell. Level 1 has already verified metrics.json itself against independent values. |
| **Grounding in source data** | `guard`. `provider_label`: the report says who wrote it. `draft_label`. Source file and sha256 are in the header by template. `client_isolation`: no other client's id or name appears. |
| **Completeness of important findings** | `section:*`: all ten template sections. `flag:*`: case-specific findings, such as the spiking line, missing months, zero-base n/a and the balance check. `issue_shown_early:<code>`: every data-quality code appears before the Revenue section. `reviewer_checklist`: at least 3 open items. `ttm_incomplete_stated`. |
| **Unsupported claims** | `causal_language_hedged`: "due to", "driven by" and similar need a hedge in the same sentence. `cause_vocabulary_hedged`: lawsuit, churn, new hires and similar need a hedge. `seasonality_not_asserted`: when not assessed. `one_offs_hedged`. `ttm_incomplete_no_figures`. `impossible_values`. `forbidden:*` per case. |
| **Clarity and usefulness** | Level 3 only. |

Year and whole-file reports use their own section list (`SPAN_SECTIONS` in
`report_checks.py`: coverage, quarters, year on year, monthly table or series, calendar
years, first month to last month, seasonality, reconciliation) in place of the month
sections, and skip the TTM checks. Every other check applies unchanged, including
`guard` on the whole report and `causal_words_outside_questions`.

Hedges are words like *possible, may, might, could, if, whether, needs confirmation*,
or a question mark, in the **same sentence**. Hard-wrapped lines are joined before
splitting into sentences. Line labels and text quoted from the source or config are
removed before looking for claims, so a line named "Legal Settlement" or a workbook
titled "seasonal peak" is not counted as the writer asserting something.

### Process checks (Claude runs only)

`skill_discovered` (from the session's init event), `skill_invoked` (a `Skill` tool
call), `claude_completed`, `ran_analyze`, `ran_check_commentary`,
`report_written_this_run` and `metrics_written_this_run` (mtime after the run started).
For case 12: `no_report_written`, `no_output_for_unknown_client`, and
`reply_explains_stop`.

Runs use `--setting-sources project --permission-mode default --strict-mcp-config` and
allow only `python -m finance_engine ...` (Bash or PowerShell), Read, Write, Edit, Glob
and Skill. A run is therefore graded on the tools the skill declares, not on whatever
the operator's user settings allow.

### What Level 2 cannot see

- The guard proves a figure exists in metrics.json, not that it sits in the right
  sentence. Rounded percentages collide easily (about 15% false-pass for a random
  1-dp percentage, measured in `tests/test_guard.py`).
- Pattern checks catch the claims they name. An invented cause phrased in words the
  lexicon does not list gets through. That is why the reviewer checklist exists and why
  Level 3 is advisory.

## Level 3: rubric (optional)

`python evals/run_skill_evals.py <case> --rubric` asks Claude, with Read as its only
tool and no skill, to score the saved report against the rubric below. Scores are stored
under `rubric` in the results file. They never change a case's status.

<!-- rubric:start -->
Score two things from 1 to 5. Judge only what the report says, not whether you agree
with the business.

CLARITY: can a business owner read it once and know what happened?
5 = every section opens with the point, figures are quoted consistently, no jargon
    without explanation, tables and prose agree.
4 = clear, with one or two places that need re-reading.
3 = understandable, but the important points are buried or repeated.
2 = hard to follow; the reader has to reconstruct what matters.
1 = confusing or contradictory.

USEFULNESS: does it help the reviewer act?
5 = the significant movements, data-quality problems and limitations are prominent;
    every possible explanation is a specific, checkable question; nothing important
    is missing; no padding.
4 = useful, with a minor gap or some padding.
3 = the facts are there but the reviewer must work out what to check.
2 = misses an important movement or limitation, or buries it.
1 = misleading: states causes as facts, hides data problems, or gives false comfort.
<!-- rubric:end -->

## Commands

```bash
python evals/tools/build_expected.py                 # rebuild expected.json (inputs changed)
python evals/run_engine_evals.py                     # Level 1, all cases
python evals/run_engine_evals.py 04 --compare        # one case, diff against baseline
python evals/run_engine_evals.py --save-baseline     # record a baseline
python evals/run_skill_evals.py --provider deterministic   # Level 2 control, free
python evals/run_skill_evals.py 04                   # Level 2, one case through Claude Code
python evals/run_skill_evals.py                      # Level 2, all cases (Pro usage)
python evals/run_skill_evals.py 01 --rubric          # + Level 3
python evals/run_skill_evals.py --recheck evals/runs/skill_<ts>   # re-grade, no Claude call
python evals/regression_demo.py both                 # prove a break is caught
```

Real results are in `evaluation_report.md`. Bugs the evals caught are in `CHANGELOG.md`.
