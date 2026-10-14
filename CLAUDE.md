# Financial Trend Analysis Engine

Monthly financial trend analysis for RE Financial Services. Reads a client's financial
statements, calculates verified metrics, and produces a **draft report for a human reviewer**.

## The one hard rule

**Python is the only source of numbers.** `finance_engine` calculates everything.
Commentary providers (the deterministic template, and optionally Gemini or the Claude API)
receive only `metrics.json` or the compact brief built from it, and may *describe* those
numbers. A provider must never calculate, estimate, or invent a figure.
`guard.py` enforces this by checking every number in commentary text against `metrics.json`.

**The web UI never calculates either.** The browser and the web layer
(`finance_engine/web/`, `finance_engine/service/`, `ui/`) do no sums, differences,
percentages, rounding or comparing of money values. They show values copied from
`metrics.json` and strings the engine formatted. If the UI needs a number the files do
not hold, the engine must produce it; the UI must not compute it. Every web route has a
CLI equivalent, listed in `finance_engine/service/parity.py` (a test enforces both).

## Pipeline

```
Data source (Excel workbook, or a saved QuickBooks sandbox snapshot)
  -> finance_engine: read -> validate -> calculate -> metrics.json
  -> CommentaryProvider (deterministic template; optional Gemini / Claude API,
     checked, retried, deterministic fallback)
  -> guard (number check) -> report.md
```

`metrics.json` is the only contract between stages. See `docs/metrics_schema.md`.

## Folder map

| Path | Purpose |
|---|---|
| `config/clients/<client_id>.yaml` | Per-client sheet layout, section anchors, categories, thresholds. A new company is a new YAML file, not new code. |
| `finance_engine/sources/` | `DataSource` interface, `excel.py` reader (openpyxl), `quickbooks.py` (reads a saved QuickBooks P&L snapshot), `quickbooks_api.py` (read-only sandbox fetch for `pull-quickbooks`), `factory.py` |
| `finance_engine/validation.py` | Errors (block a metric) vs warnings (continue, but show) |
| `finance_engine/metrics.py` | All calculations; emits `metrics.json` |
| `finance_engine/reconciliation.py` | Computed totals vs the workbook's own formula totals |
| `finance_engine/commentary/` | `CommentaryProvider` interface, `deterministic.py`, and the optional cloud providers (`providers.py`, `orchestrator.py`, `writer_prompt.md`; configured in `config/providers.yaml`) |
| `finance_engine/guard.py` | Rejects numbers not supported by `metrics.json` |
| `finance_engine/report.py` | Builds `report.md` |
| `data/clients/<id>/source/` | Original workbooks — **never modified** |
| `data/synthetic/` | Generated SYNTHETIC test workbooks (never evidence about a real client) |
| `output/clients/<id>/<period>/` | `metrics.json`, `report.md` |
| `finance_engine/brief.py` | Python-formatted view of metrics.json for a commentary writer (no arithmetic) |
| `finance_engine/spans.py`, `monthly.py` | `--scope year` / `--scope all` documents, built from one month's figures at a time |
| `finance_engine/sources/selection.py` | Which workbook a run reads when a client's folder holds several (`--file`); never merged |
| `finance_engine/init_client.py` | `init-client`: drafts and validates a new client's YAML from its workbook |
| `.claude/skills/monthly-financial-analysis/` | The skill: Claude Code writes the commentary from the brief, then runs the guard |
| `evals/` | 19 golden cases, Level 1 (engine) and Level 2 (report) runners, baselines. See `evals/grading.md` |
| `finance_engine/onboarding.py` | `init-client --draft`, `validate-config`, `approve-config`: a new config goes live only after a named person approves it |
| `finance_engine/review.py`, `config/review.yaml` | Review workflow (draft, in_review, changes_requested, approved), files only; `review/review_log.json` beside each report |
| `finance_engine/trace_links.py` | Which figures in a report's prose map to exactly one metric (for click-to-trace); engine code, the guard is unchanged |
| `finance_engine/service/` | Service layer shared by the new CLI commands and the web app: reads, jobs, uploads, the route-to-CLI parity table. No money maths |
| `finance_engine/web/`, `ui/` | The local web app (`serve`, 127.0.0.1 only, no sign-in): FastAPI routes; plain HTML/CSS/JS modules; Chart.js vendored in `ui/static/vendor/`. See `docs/ui.md` |

## Boundaries

- **No AI ever produces or changes a number.** Reading, validation, calculation,
  reconciliation, the brief and every table are plain Python. AI only writes commentary
  text, through the providers in `finance_engine/commentary/`, and that text must pass
  the guard. The only web server is the optional local UI (`finance_engine/web/`,
  started by `serve`); the engine never imports it.
- QuickBooks is read **read-only, sandbox only**: `sources/quickbooks_api.py` makes GETs
  plus the OAuth token refresh, nothing else, with its own token store
  (`.secrets/quickbooks_reader_token.json`), never the MCP server's. The engine analyses a
  saved snapshot file, never a live response. See `docs/quickbooks_setup.md`.
- Everything is keyed by `client_id`.
- External dependencies sit behind interfaces: `DataSource`, `CommentaryProvider`, `CredentialStore`.
- Settings come from environment variables (`LLM_PROVIDER=deterministic` by default).
- Cloud commentary is optional. Gemini runs only when `GEMINI_API_KEY` is set;
  `claude-api` is **opt-in and paid** (billed separately from Claude Pro; `enabled: false`
  by default in `config/providers.yaml`). Never assume an API key exists: a missing key
  skips the provider. **The deterministic report is always the final fallback.**
- The web UI is local only: it binds 127.0.0.1, refuses other hosts and cross-origin
  writes, serves only `ui/`, and never returns a secret value. There is no sign-in:
  reviewer names are self-declared. Review logs and approvals are files on this machine
  (git-ignored). Sign-in and durable storage are [PLANNED].

## Rules for calculations

- Full precision internally; round only for display.
- A % change from a zero or missing base is `None` and displays as `n/a` — never infinity.
- Missing data is never turned into zero. Blank and `0` are different.
- An incomplete TTM window is never presented as complete.
- Never classify a line as revenue/COGS/opex from its name alone — section membership comes
  from the config's structural anchors.
- If a critical calculation or reconciliation fails, block that metric and explain why.
  Never substitute an estimate.

## Security

- Never print or commit secrets. `.env` and token files are git-ignored.
- `.env.example` holds placeholders only.

## Running

```bash
python -m finance_engine analyze --client abc_inc --period 2026-06
python -m finance_engine report  --client abc_inc --period 2026-06
python -m finance_engine check-commentary --client abc_inc --period 2026-06 --file notes.md
python -m finance_engine brief   --client abc_inc --period 2026-06   # figures for a writer
python -m finance_engine report  --client test_clean --scope year --year 2025   # or --scope all
python -m finance_engine analyze --client test_multi --period 2025-06 --file test_multi_second_half.xlsx
python -m finance_engine init-client --file book.xlsx --client-id acme_ltd --name "Acme Ltd"
python -m finance_engine pull-quickbooks --client qb_sandbox --start 2025-10 --end 2026-10
python -m finance_engine report  --client qb_sandbox_fixture --period 2026-09   # case 19's snapshot
python -m finance_engine init-client --draft --file book.xlsx --client-id acme_ltd --name "Acme Ltd"
python -m finance_engine validate-config --client acme_ltd --draft
python -m finance_engine approve-config  --client acme_ltd --by "A. Reviewer"
python -m finance_engine review submit   --client test_clean --period 2026-01 --by "A. Writer"
python -m finance_engine review approve  --client test_clean --period 2026-01 --by "B. Partner"
python -m finance_engine review queue
python -m finance_engine list-clients | list-files --client ID | show-report --client ID --period P
python -m finance_engine chart-data --client test_clean | list-evals | provider-status | quickbooks-status
python -m finance_engine serve                       # http://127.0.0.1:8765, local only
# every command above also takes --json (one result on stdout; notes on stderr)
python -m pytest -q          # full test suite
python -m pytest -q -m mutation   # mutation check (expects injected faults to be caught)
python evals/run_engine_evals.py --compare            # Level 1, free; run on every change
python evals/run_skill_evals.py --provider deterministic   # Level 2 control, free
python evals/run_skill_evals.py 04                    # Level 2 via Claude Code (Pro usage)
python scripts/ui_smoke.py                            # start the web app, hit every route, stop
```
