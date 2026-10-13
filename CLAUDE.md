# Financial Trend Analysis Engine

Monthly financial trend analysis for RE Financial Services. Reads a client's financial
statements, calculates verified metrics, and produces a **draft report for a human reviewer**.

## The one hard rule

**Python is the only source of numbers.** `finance_engine` calculates everything.
Commentary providers (templates now, Claude/Ollama later) receive only `metrics.json` and
may *describe* those numbers. A provider must never calculate, estimate, or invent a figure.
`guard.py` enforces this by checking every number in commentary text against `metrics.json`.

## Pipeline

```
Data source (Excel now, QuickBooks later)
  -> finance_engine: read -> validate -> calculate -> metrics.json
  -> CommentaryProvider (deterministic templates now)
  -> guard (number check) -> report.md
```

`metrics.json` is the only contract between stages. See `docs/metrics_schema.md`.

## Folder map

| Path | Purpose |
|---|---|
| `config/clients/<client_id>.yaml` | Per-client sheet layout, section anchors, categories, thresholds. A new company is a new YAML file, not new code. |
| `finance_engine/sources/` | `DataSource` interface + `excel.py` reader (openpyxl) |
| `finance_engine/validation.py` | Errors (block a metric) vs warnings (continue, but show) |
| `finance_engine/metrics.py` | All calculations; emits `metrics.json` |
| `finance_engine/reconciliation.py` | Computed totals vs the workbook's own formula totals |
| `finance_engine/commentary/` | `CommentaryProvider` interface + `deterministic.py` |
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
| `evals/` | 18 golden cases, Level 1 (engine) and Level 2 (report) runners, baselines. See `evals/grading.md` |

## Boundaries

- `finance_engine` contains **no AI, no web server, no QuickBooks code**.
- Everything is keyed by `client_id`.
- External dependencies sit behind interfaces: `DataSource`, `CommentaryProvider`, `CredentialStore`.
- Settings come from environment variables (`LLM_PROVIDER=deterministic` by default).
- No Gemini, no paid/remote AI provider. Never assume an API key exists.

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
# every command above also takes --json (one result on stdout; notes on stderr)
python -m pytest -q          # full test suite
python -m pytest -q -m mutation   # mutation check (expects injected faults to be caught)
python evals/run_engine_evals.py --compare            # Level 1, free; run on every change
python evals/run_skill_evals.py --provider deterministic   # Level 2 control, free
python evals/run_skill_evals.py 04                    # Level 2 via Claude Code (Pro usage)
```
