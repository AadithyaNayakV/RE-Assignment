# Architecture on one page

Monthly trend analysis for RE Financial Services: a draft report for a human reviewer.
**Python is the only source of numbers.** Everything below runs on one laptop; nothing is
deployed. Built and tested unless marked **[PLANNED]**.

## Files

- `finance_engine/`: reads a workbook or a saved QuickBooks sandbox snapshot, validates,
  calculates, reconciles to the source's own totals, writes `metrics.json`, the only
  contract between stages. `guard.py` rejects any figure in commentary that is not in it.
- `config/clients/<id>.yaml`: one company per file. `init-client --draft` drafts it from
  a workbook; `approve-config` makes it live after a person approves it.
- `finance_engine/commentary/`: the deterministic template, plus optional Gemini and
  Claude API providers (`config/providers.yaml`) that see only the compact brief, are
  checked, retried, and always fall back to the template.
- `finance_engine/service/` and `finance_engine/web/`, `ui/`: the local web UI. Thin
  routes; each one listed with its CLI command; it never calculates.
- `evals/`: 19 golden cases, Level 1 (engine) and Level 2 (report) runners, baselines.

## Skills and memory

- `.claude/skills/monthly-financial-analysis/`: Claude Code writes commentary from the
  brief, then runs the guard. It never calculates.
- `CLAUDE.md` is the project memory (the rules, folder map, commands).
- Review state is files: `review/review_log.json` beside each report (append-only,
  hash-chained, with a SHA-256 of report.md and metrics.json per decision) and
  `data/clients/<id>/config_approval.json`. On this machine only; names are
  self-declared.
- **[PLANNED]** per-client reviewer notes the skill reads between months; today a
  send-back comment reaches only the cloud commentary writer.

## Hosting

- **Now:** local. `serve` binds 127.0.0.1, refuses other hosts, cross-origin writes and
  non-UI files, and returns secret names, never values. No sign-in.
- **[PLANNED]:** the same FastAPI app behind the firm's SSO over HTTPS, with real
  per-user roles; review logs and approvals in durable shared storage; secrets in a
  secret manager behind the existing `CredentialStore`; the QuickBooks MCP server hosted
  for the team; Claude via Amazon Bedrock as one more provider. None of this exists or
  has been tried.

## Ten clients, kept apart

- **Now:** everything is keyed by a validated `client_id` (`^[a-z0-9_]{2,40}$`):
  `config/clients/<id>.yaml`, `data/clients/<id>/`, `output/clients/<id>/`. A run reads
  one client and one file; files are never merged. An eval check fails a report that
  names another client. One background job per client at a time.
- **[PLANNED]:** access control per client, per-client audit logs, a secret-manager
  entry and QuickBooks token per client. **Today nothing stops a local user reading any
  client's folder.**

## What breaks first at ten clients

1. Writing and reconciling each new layout's config (now drafted and approved, still a
   person's work).
2. Ten rotating QuickBooks tokens.
3. Reviewer load: ten drafts a month are ten reviews.
4. The guard proves a figure exists, not that it is in the right sentence.
