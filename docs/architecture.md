# Architecture

One page. **[NOW]** means built and tested in this repo. **[PLANNED]** means designed and
documented, not built. Nothing here is deployed anywhere; everything runs on one laptop.

## 1. Project structure: files, skills, memory

| Piece | Role | |
|---|---|---|
| `finance_engine/` | Python. Reads the workbook, validates, **calculates every number**, reconciles to the workbook's own totals, writes `metrics.json`. No AI, no web server, no QuickBooks code. | NOW |
| `metrics.json` | The only contract between stages (`docs/metrics_schema.md`). Every metric carries its method, source cells and warnings. | NOW |
| `.claude/skills/monthly-financial-analysis/` | The instructions Claude Code follows: `SKILL.md` (10 steps) plus `reference/` (metric definitions, report template, reviewer checklist). Claude writes the commentary; it never calculates. | NOW |
| `finance_engine/guard.py` | Rejects any figure in the text that is not in `metrics.json`, and any direction word that contradicts a change's sign. Runs on every provider's output. | NOW |
| `CLAUDE.md` | Project memory: the one hard rule (Python is the only source of numbers), folder map, boundaries, commands. Loaded into every Claude Code session. | NOW |
| `config/clients/<id>.yaml` | One company = one file: sheet layout, section anchors, categories, thresholds, expected-negative lines. | NOW |
| `evals/` | 13 golden cases; Level 1 (engine) and Level 2 (report) runners; baselines; regression demo. | NOW |
| `.mcp.json`, `.claude/settings.json` | The QuickBooks MCP connection and its read-only permission rules. | NOW |
| Per-client reviewer notes (memory) | A file per client (for example `config/clients/<id>.notes.md`) where a reviewer records what they confirmed: known credits, expected one-offs, causes they supplied. The skill would read it so the next month's questions skip what is already answered. Not built: today no reviewer input is kept between runs. | PLANNED |

## 2. Data flow

```
Excel workbook ─┐                       ┌─ Claude Code + skill  (now, Pro plan, no API key)
(QuickBooks     ├─► finance_engine ─► metrics.json ─► commentary ─┼─ deterministic template (now, always available)
 later: DataSource)  validate·calculate·reconcile   │            └─ Ollama / paid API      (PLANNED, config switch)
                                                    └────────────────► guard ─► report.md ─► human reviewer
```

The model can only describe numbers it was handed. A figure it invents fails the guard.

## 3. QuickBooks connection [NOW, local only]

Intuit's official `quickbooks-online-mcp-server`, run locally over stdio by Claude Code.
OAuth 2.0 authorization-code flow against a sandbox company; the server refreshes the
access token (about 1 hour) and rotates the refresh token (about 100 days) itself, writing
it to its own git-ignored `.env`. `.mcp.json` holds no secret. Claude can only use the 71
read tools; the 71 write tools are denied in `.claude/settings.json`. Detail and the
rejected alternatives: `docs/quickbooks_setup.md`.

## 4. Hosting

**Now:** everything local. **Later [PLANNED]:** the engine behind a small FastAPI service
(`/health`, `/analyze`); commentary by a local Ollama model or a paid API, chosen by
`LLM_PROVIDER` with no rewrite; the MCP server hosted over HTTPS behind the firm's SSO for
the team; secrets in a secret manager, not a file. Sketch: `docs/future_hosting.md`.

## 5. Ten clients

Everything is keyed by `client_id`: `config/clients/<id>.yaml`, `data/clients/<id>/`,
`output/clients/<id>/<period>/`, one QuickBooks token store per client
(`QUICKBOOKS_TOKEN_STORE_PATH`, supported by the server today). **[NOW]:** separation by
path and convention; the skill is told to handle one client per run and never cite another;
an eval check fails a report that names another client; evals run per client.
**[PLANNED]:** per-client access control, per-client logs and audit trail, a secret
manager entry per client. **Today nothing enforces who may read which client's folder.**

## 6. What breaks first at ten clients

1. **Per-client mapping configs.** Each new workbook layout needs a YAML written and
   reconciled by a person. This is the real scaling cost, not compute.
2. **Token management.** Ten refresh tokens that rotate and expire; one lapsed token
   silently takes a client offline.
3. **Pro usage limits.** One skill run costs about $0.30 to $0.50 API-equivalent and a full
   13-case eval about $4 to $5. We hit the session limit once during this build.
4. **Reviewer load.** The report is a draft. Ten drafts a month are ten human reviews.
5. **Guard blind spots.** It proves a number exists, not that it sits in the right sentence.
   More clients means more chances for that to matter.
6. **No tenant isolation** (section 5). Fine for one analyst, not for ten clients.
