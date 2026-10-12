# Financial Trend Analysis Engine

Monthly financial trend analysis for RE Financial Services. It reads a client's financial
statements, calculates verified metrics in Python, and a Claude Code skill writes a **draft
report for a human reviewer**. Python is the only source of numbers; Claude only describes
them, and a guard rejects any figure that is not in `metrics.json`.

## Deliverables

The brief (`docs/Build Exercise - AI Solutions Builder.pdf`) asks for four things.

| # | Deliverable | Where | Status |
|---|---|---|---|
| 1 | **The working connection**: QuickBooks sandbox to Claude | `.mcp.json` (config), `.claude/settings.json` (read-only permission rules), `quickbooks-online-mcp-server/` (Intuit's official server), `docs/quickbooks_setup.md` (choice, rejected options, credentials, team hosting), `tests/integration/test_quickbooks_mcp.py`, `scripts/preflight_check.py` | Working against the sandbox, local only |
| 2 | **The skill file(s)** | `.claude/skills/monthly-financial-analysis/` (`SKILL.md` and `reference/`), backed by `finance_engine/` and `config/clients/*.yaml` | Working |
| 3 | **The eval cases and grading approach** | `evals/cases/` (13 cases), `evals/grading.md`, `evals/run_engine_evals.py`, `evals/run_skill_evals.py`, `evals/baseline/`, `evals/CHANGELOG.md`, `evals/evaluation_report.md` | Working |
| 4 | **The one-page architecture note** | `docs/architecture.md` (hosting plan: `docs/future_hosting.md`) | Written |

Also: `docs/requirements_checklist.md` (every requirement with evidence and status),
`docs/decisions.md` (why each choice), `docs/demo_checklist.md` and
`docs/live_demo_runbook.md` (the live readout).

**Not done:** the brief's *stretch goal* (loading the workbook into the sandbox and running
the skill through the live connection). Nothing is deployed anywhere.

## Prerequisites

- Python 3.10 or newer
- Node.js 18 or newer and npm (QuickBooks MCP server only)
- Claude Code, signed in on a Pro plan (the skill; no API key is used)
- The supplied ABC workbook, for the ABC cases (it is client data and not in the repo)

## Install

```bash
pip install -r requirements.txt
```

Put the supplied workbook at
`data/clients/abc_inc/source/ABC Inc - Financial Statements.xlsx`. The engine opens it
read-only and never modifies it. Without it, the ABC cases report SKIP, never PASS.

## Generate the 10 synthetic workbooks

They are already committed. To regenerate them, their configs and expected values:

```bash
python scripts/generate_synthetic.py
```

Everything it makes is labelled SYNTHETIC and says nothing about a real client.

## Environment variables

```bash
cp .env.example .env      # placeholders only; never commit .env
```

`finance_engine` needs no key and no secret. Defaults work as they are:
`LLM_PROVIDER=deterministic`, `FINANCE_CONFIG_DIR`, `FINANCE_DATA_DIR`, `FINANCE_OUTPUT_DIR`.

## Run the engine

```bash
python -m finance_engine list-periods --client abc_inc
python -m finance_engine analyze --client abc_inc --period 2026-06    # writes metrics.json
python -m finance_engine report  --client abc_inc --period 2026-06    # + report.md, no AI
python -m finance_engine brief   --client abc_inc --period 2026-06    # figures, formatted
python -m finance_engine check-commentary --client abc_inc --period 2026-06 --file notes.md
```

Choose a client with `--client` (any file name in `config/clients/`, without `.yaml`) and a
month with `--period YYYY-MM` (`list-periods` shows which months exist). `--period latest`
picks the newest month in the source and prints `period: latest -> YYYY-MM`. Output goes to
`output/clients/<client>/<period>/`. Exit codes: 0 ok, 1 bad usage or config, 2 data-quality
or reconciliation failure, 3 the guard rejected the text.

Adding a company is adding `config/clients/<id>.yaml` and its workbook. No code changes.

## The skill

Project skills in `.claude/skills/` load automatically when Claude Code starts in this
folder. Check it is visible and use it:

```text
/skills                                        # lists monthly-financial-analysis
Analyse ABC Inc for June 2026                  # or: /monthly-financial-analysis abc_inc 2026-06
```

Claude runs `analyze`, reads the `brief`, writes `report.md`, and must pass
`check-commentary` before finishing. It stops cleanly on an unknown client or bad period.
No Claude available? `python -m finance_engine report ...` writes a labelled, guarded
report with no AI.

## Tests and evals

```bash
python -m pytest -q                                    # full suite
python -m pytest -q -m mutation                        # injected faults must be caught
python evals/run_engine_evals.py --compare             # Level 1, free, run on every change
python evals/run_skill_evals.py --provider deterministic   # Level 2 control, free
python evals/run_skill_evals.py 04                     # Level 2, one case via Claude (Pro usage)
python evals/regression_demo.py both                   # prove a deliberate break is caught
```

How grading works, thresholds and the rubric: `evals/grading.md`. Real results:
`evals/evaluation_report.md`.

## QuickBooks MCP setup and check

The server is Intuit's official repo, cloned into `quickbooks-online-mcp-server/`.

```bash
git clone https://github.com/intuit/quickbooks-online-mcp-server.git quickbooks-online-mcp-server
cd quickbooks-online-mcp-server && git checkout 31a1dd5 && npm install && npm run build
npm run auth        # browser OAuth; the server writes its own git-ignored .env
```

The OAuth app's redirect URI must be `http://localhost:8000/callback`. Then, from the repo
root, approve the `quickbooks` server once via `/mcp` in Claude Code. Check everything:

```bash
python scripts/preflight_check.py          # ~80 s; see docs/live_demo_runbook.md
QBO_INTEGRATION=1 QBO_LIVE=1 python -m pytest tests/integration -v   # opt-in live tests
```

Claude can only call read tools; every create/update/delete tool is denied.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No config for client 'x'` | The client has no YAML. The error lists the clients that do. |
| `Period must look like YYYY-MM` / `period_not_in_source` | Use `list-periods` to see the available months. |
| ABC cases say SKIP | The workbook is not at `data/clients/abc_inc/source/`. |
| `check-commentary` says REJECTED | A figure is not in `metrics.json`, or a direction word contradicts its change. The output names the line. |
| QuickBooks `401` / `invalid_grant` | Token lapsed: `cd quickbooks-online-mcp-server && npm run auth`. |
| `quickbooks` missing in `/mcp` | Run `npm run build` there, then approve the server once. |
| Skill runs fail with "session limit" | Pro usage is exhausted until the reset time; use the deterministic report. |
| `claude` not found by the eval runner | `--claude-bin <path>` or set `CLAUDE_BIN`. |

## Limitations

- **The guard proves a figure exists in `metrics.json`, not that it sits in the right
  sentence.** Rounded percentages collide (about 15% false-pass, measured). Claim checks
  are pattern-based and miss causes phrased in words they do not list.
- **One Claude run per eval case.** Output varies between runs; variance is not measured.
- **Local only.** No authentication, authorization, TLS or tenant isolation. Clients are
  separated by path and convention. Not production-ready (`docs/future_hosting.md`).
- **The skill needs a Pro session.** A full 13-case run costs about $4 to $5 API-equivalent.
- **Sandbox only.** The QuickBooks data is Intuit's sample data, not evidence about a client.
- **Excel only.** `finance_engine` has no QuickBooks reader yet; the `DataSource` interface
  is where it would go.
- **Stretch goal not attempted.**
