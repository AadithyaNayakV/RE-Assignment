# Architecture on one page

Monthly trend analysis for RE Financial Services: a draft report for a human reviewer.
**Python is the only source of numbers.** Everything below runs on one laptop; nothing is deployed. Built and tested unless marked **[PLANNED]**.

## How it runs today

- **Data in:** the Excel workbook, or QuickBooks (sandbox). `pull-quickbooks` saves a read-only snapshot of the monthly Profit and Loss; Claude Code can also read QuickBooks live through the MCP server (read tools only). `scripts/load_workbook_to_sandbox.py` put the workbook into the sandbox; `round-trip` checked that QuickBooks gives the same figures as Excel.
- **Three ways to run it:** (1) terminal commands (`report`, `pull-quickbooks`, `round-trip`, `check-commentary`); (2) Claude Code: ask "Analyse ABC Inc for June 2026" or type `/monthly-financial-analysis`; (3) the local web UI (`serve`), which calls the same commands.
- **Who writes the commentary today:** Claude Code (through the skill) or Gemini; if neither is used, the deterministic template. Every draft passes the guard before it becomes `report.md`.
- **The skill:** `.claude/skills/monthly-financial-analysis/SKILL.md` loads by itself when Claude Code opens the project. It tells Claude to run the engine, read the brief, write commentary, run the guard. A new company needs a new config file, not a new skill.

## Files

- `finance_engine/`: reads a workbook or a saved QuickBooks sandbox snapshot, validates, calculates, reconciles to the source's own totals, writes `metrics.json`, the only contract between stages. `guard.py` rejects any figure in commentary that is not in it.
- `config/clients/<id>.yaml`: one company per file. `init-client --draft` drafts it from a workbook; `approve-config` makes it live after a person approves it.
- `finance_engine/commentary/`: the deterministic template, plus an optional Gemini provider (`config/providers.yaml`) that sees only the compact brief, is checked, retried, and always falls back to the template. A Claude API provider exists in the code but is switched off and not used; no local model is used.
- `finance_engine/service/`, `finance_engine/web/`, `ui/`: the local web UI. Thin routes, each listed with its CLI command; it never calculates.
- `evals/`: 20 golden cases, Level 1 (engine) and Level 2 (report) runners, baselines.
- `scripts/load_workbook_to_sandbox.py`: the only QuickBooks write (sandbox only, run by a person on 2026-10-15; the round trip matched).
- `.mcp.json` starts the QuickBooks MCP server (no secret in it); `.claude/settings.json` denies its create, update and delete tools, so Claude sees only read tools.

## Memory and review

- `CLAUDE.md` is the project memory (the rules, folder map, commands). Claude Code's per-user auto-memory is not relied on: everything the skill needs is in the repo (`SKILL.md`, its `reference/` files, `CLAUDE.md`).
- Review state is files: `review/review_log.json` beside each report (append-only, hash-chained, with a SHA-256 of report.md and metrics.json per decision) and `data/clients/<id>/config_approval.json`. On this machine only; names are self-declared.
- **[PLANNED]** per-client reviewer notes the skill reads between months; today a send-back comment reaches only the cloud commentary writer.

## Hosting

- **Now:** local. `serve` binds 127.0.0.1, refuses other hosts, cross-origin writes and non-UI files, and returns secret names, never values. No sign-in.
- **[PLANNED]:** the same FastAPI app behind the firm's SSO over HTTPS, with real per-user roles; review logs and approvals in durable shared storage; secrets in a secret manager behind the existing `CredentialStore`; the QuickBooks MCP server hosted for the team; a Claude API or Amazon Bedrock provider, or a local model, as later options. None of this exists or has been tried.

## Ten clients, kept apart

- **Now:** everything is keyed by a validated `client_id` (`^[a-z0-9_]{2,40}$`): `config/clients/<id>.yaml`, `data/clients/<id>/`, `output/clients/<id>/`. A run reads one client and one file; files are never merged. An eval check fails a report that names another client. One background job per client at a time.
- **[PLANNED]:** access control per client, per-client audit logs, a secret-manager entry and QuickBooks token per client. **Today nothing stops a local user reading any client's folder.**

## What breaks first at ten clients

1. **QuickBooks:** the reader has one set of `QBO_*` variables and one token file (`.secrets/quickbooks_reader_token.json`), so every QuickBooks client shares one sandbox company and one token. Ten clients means ten rotating tokens.
2. **Config work:** writing and reconciling each new layout's config (now drafted and approved, still a person's work).
3. **No login, and review logs on local disk.**
4. **Review is human by design, and that is a strength.** At ten clients it needs real sign-in and the review queue, so each draft has a named reviewer.
5. **The guard** proves a figure exists, not that it is in the right sentence.