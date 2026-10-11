# PHASE 4 — ARCHITECTURE NOTE, README, DEMO PREP, FINAL AUDIT

Read the assignment PDF (source of truth), `CLAUDE.md` and the whole project. Do not rebuild working parts. Fix gaps and verify.

## 1. docs/architecture.md (about one page)

Cover what the brief asks, in this order:
1. **Project structure — files, skills, memory**: `finance_engine/` (Python, all numbers), `metrics.json` contract, `.claude/skills/monthly-financial-analysis/` (instructions Claude follows), `CLAUDE.md` (project memory), `config/clients/*.yaml`, `evals/`.
2. **Data flow** (small diagram): source -> engine -> metrics.json -> commentary (Claude via skill now / template / Ollama or API later) -> guard -> report.
3. **QuickBooks connection**: local official MCP server now; OAuth and credential handling.
4. **Hosting choices**: now = local; later = engine behind a small FastAPI service, commentary via a local Ollama model or a paid API (config switch, no rewrite), hosted MCP server for the team, secrets in a secret manager.
5. **Ten clients**: client_id everywhere; separate config, data folder, outputs, logs and QuickBooks tokens per client; per-client access control; the skill refuses to mix clients; evals run per client.
6. **What breaks first at ten clients** (honest list, e.g. per-client mapping configs, token refresh management, Pro usage limits for skill runs, manual review load).
7. Clearly mark **implemented now** vs **planned**.

## 2. README.md

Prerequisites, install, generating the 10 synthetic workbooks, `.env` setup, running the engine and report, choosing client and period, running tests and both eval runners, QuickBooks MCP setup and check, activating the skill, troubleshooting, limitations.
Add a top section **"Deliverables"** mapping the brief's 4 deliverables to file paths.

## 3. docs/demo_checklist.md

For each item: commands/actions, expected result, verified status (with evidence), one-line explanation for the interviewer.
1. Show the ABC workbook.
2. Run the skill in Claude Code on ABC Jun 2026; show report.md.
3. Show numbers in the report match metrics.json and the workbook totals.
4. Run on a synthetic company with a different layout (config only, no code change).
5. Missing-data and one-off cases.
6. Guard rejecting an invented number.
6b. Deterministic fallback: produce a valid labelled report with no AI.
7. Claude Code listing QuickBooks MCP tools.
8. A live read-only sandbox request.
9. Run engine evals and one skill eval; show pass/fail and the baseline compare.
10. Explain connection choice, rejected options and team hosting.
11. Explain ten-client separation.
12. **Live modification rehearsal**: 3 small changes I might be asked for (e.g. change the significance threshold, add a metric, add a new company config). For each: files to touch, and proof by re-running evals.

## 3b. Live demo readiness (the final round is a live demo of the real system, not slides)

Create `docs/live_demo_runbook.md` and a script `scripts/preflight_check` (Python or PowerShell) that I run 30 minutes before the call. It must check, with real results:
- Python environment and dependencies work; all tests pass.
- The QuickBooks token is valid or refreshes successfully; a read-only sandbox request returns my company name. If it fails, print the exact re-authorization steps.
- Claude Code discovers the QuickBooks MCP server and the skill.
- `python -m finance_engine analyze` and `report` run on ABC Jun 2026 within a reasonable time.
- The original workbook hash is unchanged.

The runbook must include:
- The exact demo order with commands to copy-paste (from demo_checklist.md), and roughly how long each step takes.
- Backups if something fails live: pre-generated reports and eval results saved in `demo_backup/`, the deterministic report if Claude is slow or my Pro limit is reached, and saved QuickBooks output if the sandbox is down. Say honestly which one is a backup.
- Tips: avoid using up Pro usage on the demo day before the call; close other apps; keep the terminal font large; never show `.env` or tokens on screen-share.
- The live modification rehearsal: for each practice change, the exact files to edit, the command to re-run evals, and the expected result. Do each rehearsal once for real, then revert with Git, and record that it worked.

## 4. Ready for later, without deploying

- Configuration only through environment variables; complete `.env.example`.
- Document (do not build unless trivial) a Dockerfile and a future FastAPI health endpoint.
- Document future HTTPS, authentication, authorization, secret management and tenant isolation.
- Keep the local Claude Code MCP connection separate from a future hosted app's QuickBooks integration.
- Do not claim production-grade tenant isolation, authentication or readiness unless it exists and is tested.
- Do not create cloud resources, publish anything or expose services.

## 5. Audit

Create/update `docs/requirements_checklist.md`: each brief requirement with location, evidence and status (PASS / PARTIAL / BLOCKED / NOT STARTED). Include: sandbox connection working, credential handling, skill, revenue/margin/opex/MoM/TTM, commentary, maintainable and portable skill, ≥5 golden cases, grading, regression detection, one-page architecture, hosting and ten-client answer, stretch goal status.

Run all tests and evals. Check Git status, `.gitignore`, config files and Git history for secrets (e.g. search history for "secret", "token", "refresh"). Confirm the original workbook is unchanged (compare hash).

## 6. Submission package

- `docs/decisions.md`: a short log of each key choice (connector, engine/skill split, no Gemini, metrics.json contract, eval design), what I rejected and why, so I can explain every choice as my own.
- Prepare the repo for submission (GitHub repo or zip): no `.env`, tokens or `node_modules`; README "Deliverables" section at the top; the original workbook only if I confirm it may be included.
- Stretch goal status stated honestly (done / not attempted).

## 7. Final report

What is done, key files and commands, real test/eval results, QuickBooks evidence, remaining manual steps, incomplete requirements, and a clear split: local implementation vs deployment preparation vs actual deployment (none). Do not mark anything complete without evidence.