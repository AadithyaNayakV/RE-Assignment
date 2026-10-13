# Requirements checklist (audit)

Source of truth: `docs/Build Exercise - AI Solutions Builder.pdf`. Audited 2026-10-11.
Status: **PASS** (done, with evidence) · **PARTIAL** (done with a stated gap) ·
**BLOCKED** (cannot be done from here) · **NOT STARTED**. Nothing is marked PASS without
evidence in the right-hand column.

## Part 1: Connect the QuickBooks sandbox to Claude

| Requirement | Status | Where | Evidence |
|---|---|---|---|
| Connection works against the sandbox | PASS | `.mcp.json`, `quickbooks-online-mcp-server/` | `claude mcp list`: quickbooks Connected. Live `get_company_info` returned "Sandbox Company US 209f" in `scripts/preflight_check.py` and in `QBO_INTEGRATION=1 QBO_LIVE=1 pytest tests/integration` (6 passed). Local only. |
| Choice documented: chosen, rejected, why | PASS | `docs/quickbooks_setup.md`, `docs/decisions.md` | Official Intuit server chosen; native connector, community servers and a custom server rejected with reasons. |
| Where it would run for a whole team | PARTIAL | `docs/quickbooks_setup.md`, `docs/future_hosting.md` | The answer is written (hosted MCP over HTTPS behind SSO, per-client tokens in a secret manager). **Not built or deployed.** The brief says reasoning matters more than having deployed one. |
| Credentials handled throughout | PARTIAL | `.gitignore`, `.env.example`, `docs/quickbooks_setup.md` | `.env` ignored in both repos; `.mcp.json` has an empty `env`; no secret in tracked files or in git history (scan below); the server rotates its own refresh token. **Gap:** the refresh token sits in plaintext in the server's git-ignored `.env`; a secret manager is planned, not built. |
| Read-only access enforced | PASS | `.claude/settings.json` | All create/update/delete tools denied in both spellings; `tests/integration/test_quickbooks_mcp.py` checks it (3 config tests always run). 71 of 142 tools visible. |

## Part 2: A skill that produces the monthly trend analysis

| Requirement | Status | Where | Evidence |
|---|---|---|---|
| A Claude skill, discovered by Claude Code | PASS | `.claude/skills/monthly-financial-analysis/` | Headless session init lists `monthly-financial-analysis` (preflight: 31 skills visible). Invoked by the `Skill` tool in every eval run. |
| Revenue, margins, opex by category | PASS | `finance_engine/metrics.py`, `report_template.md` | ABC Jun 2026 report: 9 opex categories, 6 revenue streams, gross and operating margin. |
| Month-over-month and trailing-twelve-month views | PASS | same | MoM/YoY/TTM metrics; TTM blocked rather than shortened when the window is incomplete (case 06: all TTM `n/a`). |
| Short commentary flagging movements an owner should know | PASS | the report's Summary and Significant movements | ABC: 11 of 46 lines flagged; causes only as hedged questions. |
| For a human reviewer to finish | PASS | `reference/review_checklist.md` | Every report is labelled a draft and ends with a checklist (eval check `reviewer_checklist`). |
| A teammate could read, maintain and extend it | PASS | `SKILL.md` (short), `reference/`, `docs/metrics_schema.md`, `CLAUDE.md` | New-metric and new-company procedures are in `metric_definitions.md`. Both were done for real in the rehearsals (`docs/live_demo_runbook.md`). |
| Portable to a different company without a rewrite | PASS | `config/clients/*.yaml` | 10 synthetic clients, 3 layouts, no code change; rehearsal C added an 11th with one YAML. |
| (Phase 5B) Draft a new client's config from its workbook | PASS | `finance_engine/init_client.py`, `tests/test_init_client.py` | `init-client` detects sheet, header row, columns, date order and format, sections, subtotal groups and reported lines; validates with the real loader, reader, analysis and reconciliation before writing `<id>.yaml`, else `<id>.draft.yaml` with the problems. For test_clean and Companies A and B the generated config gives the same metric values as the hand-written one. Unhandled layouts (a non-month column between months, no COGS section, a subtotal without a header) become drafts; a non-financial or unreadable file is refused. Balance sheets are not configured. |
| (Phase 5B) Year, whole-file and multi-workbook analysis | PASS | `finance_engine/spans.py`, `finance_engine/sources/selection.py`, cases 14 to 18 | Year totals, quarters, same-months year on year, monthly table, extremes; a partial year labelled and never annualised; a gap blocks the totals. Whole file: monthly series, per-year totals, start-to-end change. `--file` and period-based workbook choice; never merged. Month output unchanged (byte-for-byte comparison against the previous commit). |
| **Stretch:** load the workbook into the sandbox and run end to end | NOT STARTED | n/a | Deliberately not attempted. |

## Part 3: Eval suite

| Requirement | Status | Where | Evidence |
|---|---|---|---|
| At least 5 golden cases (data state + expected output) | PASS (18) | `evals/cases/` | 18 cases, each with `case.yaml` (flags, forbidden claims, pass criteria) and, except case 12, `expected.json` (a derivation per value). Cases 14 to 18 (Phase 5B: year, year to date, gap, whole file, two workbooks) pass Level 1 and the deterministic Level 2; they have not been run through Claude. |
| Expected values independent of the engine | PASS | `evals/tools/build_expected.py` | Built from the generator's inputs and the workbook's own cached totals; refuses to finish if `finance_engine` is imported; a test enforces it. |
| Concrete grading approach | PASS | `evals/grading.md` | Three levels, thresholds, rubric. |
| Regression detection | PASS | `evals/baseline/`, `evals/regression_demo.py` | A wrong subtotal in a test copy was caught by reconciliation; a temporary TTM off-by-one made 12 of 13 cases newly fail; reverted byte-identical. |
| Evals actually caught things | PASS | `evals/CHANGELOG.md` | 17 numbered items: 10 product defects or gaps (#1 to #6, #14 to #17) and 7 faults in the eval suite itself (#7 to #13). |
| Results | PARTIAL | `evals/evaluation_report.md` | Level 1 13/13; Level 2 Claude run 12/13 as first graded, 13/13 after fixing a checker false positive. **Gaps:** one Claude run per case (variance unmeasured); Level 3 rubric not run; the full-run skill baseline predates the review fixes (re-saving needs about $5 of Pro usage). |

## Part 4: One-page architecture note

| Requirement | Status | Where | Evidence |
|---|---|---|---|
| Structure: files, skills, memory | PASS | `docs/architecture.md` §1 | `CLAUDE.md` is the project memory. |
| Hosting choices | PASS | §4, `docs/future_hosting.md` | Local now; hosting plan marked PLANNED. |
| What changes at ten clients; keeping data separate | PARTIAL | §5, §6 | Separation by `client_id` paths, per-client token store, an eval isolation check. **No access control, audit or tenant isolation exists**; the note says so. |
| Implemented vs planned marked | PASS | `docs/architecture.md` | Every row tagged NOW or PLANNED. |

## Ground rules and live readout

| Requirement | Status | Evidence |
|---|---|---|
| Stay inside the sandbox and the supplied workbook | PASS | No live account connected; QuickBooks is an Intuit sandbox; every other client is labelled SYNTHETIC. |
| Be ready to explain every choice | PASS | `docs/decisions.md`. |
| Live demo of the real system | PASS | `docs/demo_checklist.md`, `docs/live_demo_runbook.md`, `scripts/preflight_check.py` (13/13 on 2026-10-11, 83 s). I did not open the interactive `/mcp` screen; rehearse it once. |
| One modification on the spot | PASS | Three rehearsals done and reverted (threshold, new metric, new company). |

## Audit checks run 2026-10-11

| Check | Result |
|---|---|
| Tests | `python -m pytest -q`: **650 passed, 3 skipped** (the skips are opt-in QuickBooks tiers). Mutation subset: 18 passed. |
| Engine evals | 13/13; compare with baseline: no change. |
| Skill eval, ABC | 46/46 after the review fixes (13 turns, 92 s). |
| Preflight | 13 passed, 0 failed, 0 skipped. |
| Original ABC workbook | SHA-256 `75da8d679a89551f…` equals the reference in `scripts/reference_hashes.json` and the `source.sha256` in `metrics.json`; unchanged after the engine ran. |
| `.env` and tokens in git | None tracked: `git ls-files` shows only `.env.example`. The root `.env` is ignored (`.gitignore:2`); the server's `.env` is ignored inside its own repo. |
| Git history, all commits | No `.env`, token, key or credential file was ever added. Searching for assignments such as `refresh_token=<16+ characters>` finds one hit: a fake placeholder in `tests/test_eval_checks.py` that exists to prove the credential check fires. No real value. |
| Keys in tracked files | None. `ANTHROPIC_API_KEY` and `OLLAMA_BASE_URL` are blank placeholders. |
| `.gitignore` | Covers `.env*` (except the example), `*.pem`, `*.key`, token and credential JSON, `data/clients/`, `*.xlsx` (except synthetic), `output/`, `evals/runs/`, `demo_backup/abc_*`. |
| `node_modules` | Not tracked. |
| **Repo issue found** | `quickbooks-online-mcp-server` is recorded as a gitlink (commit `31a1dd5`) with no `.gitmodules`, so a fresh clone gets an empty folder. Fixed by adding `.gitmodules` (Intuit's URL, same commit, which equals upstream `HEAD` today); the README documents clone and build. Uncommitted until you commit it. |
