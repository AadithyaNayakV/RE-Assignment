# Architecture

One page. **[NOW]** means built and tested in this repo. **[PLANNED]** means designed and
documented, not built. Nothing here is deployed anywhere; everything runs on one laptop.

## 1. Project structure: files, skills, memory

| Piece | Role | |
|---|---|---|
| `finance_engine/` | Python. Reads the workbook, validates, **calculates every number**, reconciles to the workbook's own totals, writes `metrics.json`. No AI ever produces or changes a number; AI only writes commentary text, through the providers, and that text must pass the guard. Reads a workbook or a saved QuickBooks sandbox snapshot (`sources/quickbooks.py`); `pull-quickbooks` fetches that snapshot read-only (`sources/quickbooks_api.py`: GETs plus the token refresh, sandbox host only, its own token store). | NOW |
| `metrics.json` | The only contract between stages (`docs/metrics_schema.md`). Every metric carries its method, source cells and warnings. | NOW |
| `.claude/skills/monthly-financial-analysis/` | The instructions Claude Code follows: `SKILL.md` (10 steps) plus `reference/` (metric definitions, report template, reviewer checklist). Claude writes the commentary; it never calculates. | NOW |
| `finance_engine/guard.py` | Rejects any figure in the text that is not in `metrics.json`, and any direction word that contradicts a change's sign. Runs on every provider's output. | NOW |
| `CLAUDE.md` | Project memory: the one hard rule (Python is the only source of numbers), folder map, boundaries, commands. Loaded into every Claude Code session. | NOW |
| `config/clients/<id>.yaml` | One company = one file: sheet layout, section anchors, categories, thresholds, expected-negative lines. `init-client` drafts it from a workbook and validates it with the engine before writing it; anything it is unsure of is listed for a person to decide. | NOW |
| Scopes and files | `--scope month` (default), `year` and `all` (`finance_engine/spans.py`, built from one month's figures at a time); `--file` picks one workbook when a client's folder holds several (`sources/selection.py`). Files are never combined. Every command has `--json` for a UI. | NOW |
| `evals/` | 19 golden cases; Level 1 (engine) and Level 2 (report) runners; baselines; regression demo. | NOW |
| Local web UI (`finance_engine/web/`, `finance_engine/service/`, `ui/`) | `serve` on 127.0.0.1 only, no sign-in. Reports with badges and click-to-trace, warnings, charts, review workflow, add-company wizard, free evals, read-only QuickBooks pull. Every route has a CLI equivalent; the UI never calculates. `docs/ui.md`, one-page view: `docs/architecture_one_page.md`. | NOW |
| Review workflow and config approval | `review submit/approve/send-back`, `init-client --draft` then `approve-config`. Files on this machine only, names self-declared; the approver must differ from the submitter by default (a nudge, not security). Sign-in and durable storage: PLANNED. | NOW |
| `.mcp.json`, `.claude/settings.json` | The QuickBooks MCP connection and its read-only permission rules. | NOW |
| Per-client reviewer notes (memory) | A file per client (for example `config/clients/<id>.notes.md`) where a reviewer records what they confirmed: known credits, expected one-offs, causes they supplied. The skill would read it so the next month's questions skip what is already answered. Not built: today no reviewer input is kept between runs. | PLANNED |

## 2. Data flow

```
Excel workbook ─┐                       ┌─ Claude Code + skill  (now, Pro plan, no API key)
QuickBooks      ├─► finance_engine ─► metrics.json ─► commentary ─┼─ deterministic template (now, always available)
 P&L snapshot ──┘  validate·calculate·reconcile   │            └─ Gemini / Claude API    (now, optional; checked, retried,
                                                    │                                       deterministic fallback; Claude API opt-in, paid)
                                                    └────────────────► guard ─► report.md ─► human reviewer
```

The model can only describe numbers it was handed. A figure it invents fails the guard.
The cloud providers receive only the compact brief; a draft that fails the checks is
retried, then the next provider is tried, and the deterministic report is always the
last step. Ollama is not built.

## 3. QuickBooks connection [NOW, local only]

Intuit's official `quickbooks-online-mcp-server`, run locally over stdio by Claude Code.
OAuth 2.0 authorization-code flow against a sandbox company; the server refreshes the
access token (about 1 hour) and rotates the refresh token (about 100 days) itself, writing
it to its own git-ignored `.env`. `.mcp.json` holds no secret. Claude can only use the 71
read tools; the 71 write tools are denied in `.claude/settings.json`. Detail and the
rejected alternatives: `docs/quickbooks_setup.md`.

**Direct reader [NOW, sandbox only].** For the CLI pipeline, `pull-quickbooks` makes one
token refresh and one GET of the monthly Profit and Loss and saves the raw response as a
snapshot file (with a SHA-256) that the engine reads like a workbook. GET only, sandbox
host only, its own Intuit app and token file (`.secrets/`, git-ignored), never the MCP
server's: Intuit rotates refresh tokens, so two programs cannot share one. Secrets are
redacted from every message. Both paths and why: `docs/quickbooks_setup.md`.

## 4. Hosting

**Now:** everything local; the web UI is a FastAPI app bound to 127.0.0.1 with no
sign-in. **Later [PLANNED]:** the same app behind HTTPS and the firm's SSO with per-user
roles, review records in durable shared storage, and a `/health` endpoint; commentary by a local Ollama model or a paid API, chosen by
`LLM_PROVIDER` with no rewrite; the MCP server hosted over HTTPS behind the firm's SSO for
the team; secrets in a secret manager, not a file. Sketch: `docs/future_hosting.md`.

**AWS option [PLANNED, not built, not verified].** Secrets (the QuickBooks tokens and any
API key) in AWS Secrets Manager behind the existing `CredentialStore` interface, instead of
`.env`; Claude reached through Amazon Bedrock as one more `CommentaryProvider`, with the
same checks, retries and deterministic fallback. No AWS account, resource or code exists
for this; nothing has been tried against AWS.

## 5. Ten clients

Everything is keyed by `client_id`: `config/clients/<id>.yaml`, `data/clients/<id>/`,
`output/clients/<id>/<period>/`, one QuickBooks token store per client
(`QUICKBOOKS_TOKEN_STORE_PATH` for the MCP server, supported today; the direct reader has
one token file today, so a second QuickBooks client would need a per-client path, not
built). **[NOW]:** separation by
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
