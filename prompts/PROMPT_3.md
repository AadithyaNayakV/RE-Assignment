# PHASE 3 — CLAUDE SKILL AND EVAL SUITE

Read `CLAUDE.md`, `docs/metrics_schema.md` and the existing code first. Reuse `finance_engine` and the QuickBooks MCP config. Do not rewrite working parts.

I use Claude Pro (no API key). **Claude Code itself writes the commentary by following the skill.** No Gemini, no paid API.

## 1. The skill

Create `.claude/skills/monthly-financial-analysis/`:

```
SKILL.md
reference/metric_definitions.md   (what each metric means, from metrics_schema)
reference/report_template.md      (sections and order of the report)
reference/review_checklist.md     (what the human reviewer must confirm)
```

Check the current Claude Code skill format (frontmatter `name` + `description`) and confirm Claude Code discovers it.

`SKILL.md` must tell Claude to:
1. Identify the client_id and period (ask if missing). If the client has no config, stop and say so.
2. Run `python -m finance_engine analyze --client <id> --period <YYYY-MM>`.
3. Read `output/clients/<id>/<period>/metrics.json`. Never calculate numbers itself.
4. Write the report using the template: revenue, margins, opex by category, MoM, TTM, significant movements a business owner should know about.
5. Use only numbers present in metrics.json. Explain why a movement matters; never invent its cause (say "possible", "needs confirmation").
6. Show data-quality warnings and limitations prominently.
7. End with the Reviewer checklist (this is a draft for a human to finish).
8. Run `python -m finance_engine check-commentary ...` on its own text and fix any rejected number before finishing.
9. Save `report.md` in the client's output folder. Never mix clients. Never reveal credentials.
10. Use QuickBooks MCP tools only if the user asks for QuickBooks data and the server is configured.

Keep SKILL.md short and readable; put details in `reference/`. A teammate must be able to add a metric or a company without rewriting it.

## 2. Golden cases (13 cases; the brief's minimum is 5)

`evals/cases/<case_id>/` each with: input (workbook + config), `expected.json` (key values), required flags, forbidden claims, and pass criteria.

Cases:
1. **ABC Inc, Jun 2026** — expected values taken from the workbook's own formula totals and my manual spot checks, NOT from the engine's output.
2. Normal growth (synthetic).
3. Revenue decline (synthetic).
4. Expense spike (synthetic) — the jumping category must be flagged; its cause must not be invented.
5. Margin decline (synthetic).
6. Missing months (synthetic) — TTM must be marked incomplete; no invented values.
7. Zero/negative bases (synthetic) — % change must be "n/a", not a fake number.
8. Seasonal business (synthetic) — seasonality noted only where data supports it.
9. One-time expense (synthetic) — flagged as possible one-off, not a trend.
10. Multiple revenue streams (synthetic) — per-stream figures correct.
11. Balance sheet (synthetic) — Assets = Liabilities + Equity check.
12. Unknown client / bad period — skill must stop cleanly.
13. No AI available (deterministic fallback) — a valid, clearly labelled report is still produced and passes the guard.

Verify expected numbers independently (hand calculation or the workbook's own totals), never by copying engine output.

Rubric must cover: numerical/factual correctness, grounding in source data, completeness of important findings, clarity and usefulness, unsupported claims.

## 3. Grading

- **Level 1, engine (deterministic, free):** compare metrics.json to expected.json with tolerances. Pass/fail.
- **Level 2, skill output (deterministic checks on Claude's report):**
  - every number in the report exists in metrics.json (guard);
  - all required flags/sections present;
  - no forbidden claims (e.g. invented causes, "TTM" on incomplete data);
  - warnings shown when the case has data issues.
- **Level 3, optional rubric:** clarity and usefulness scored 1–5 with a written rubric. Never the only signal.

Document the rubric, scores and pass thresholds in `evals/grading.md`.

## 4. Runners and regression detection

- `evals/run_engine_evals.py` — runs Level 1 on all cases. Fast, free, run on every change.
- `evals/run_skill_evals.py` — for each case runs Claude Code headless, e.g.
  `claude -p "Use the monthly-financial-analysis skill for client <id>, period <YYYY-MM>"`,
  captures the report, applies Level 2 checks. Uses my Pro plan, so keep it to these cases and allow running a single case.
- Save results to `evals/baseline/` with date and Git commit. A compare command shows newly failing cases and changed values vs the baseline.
- Prove regression detection works: make a deliberate temporary break (e.g. a wrong subtotal in a test copy), show the eval fails, then revert.
- `evals/CHANGELOG.md`: record every real bug the evals caught while building (what, which case, before/after). I will be asked about this in the interview.
- `evals/evaluation_report.md`: actual results.

## 5. Automated tests

Add or extend pytest tests for ingestion, calculations, report generation, guard and deterministic fallback. Keep QuickBooks integration tests separate from the deterministic Excel tests (they must be skippable when QuickBooks is unavailable). Do not rewrite working functionality just to match suggested filenames. If no valid baseline exists yet, say so explicitly.

## 6. Final report

Files created/changed, how to activate and test the skill, exact commands, real pass/fail results for both runners, the regression demo result, what the evals caught, remaining limitations. Do not fabricate results.