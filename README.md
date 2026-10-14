# Financial Trend Analysis Engine

Monthly financial trend analysis for RE Financial Services. It reads a client's financial
statements, calculates verified metrics in Python, and a Claude Code skill writes a **draft
report for a human reviewer**. Python is the only source of numbers; Claude only describes
them, and a guard rejects any figure that is not in `metrics.json`.

## Deliverables

The brief (`docs/Build Exercise - AI Solutions Builder.pdf`) asks for four things.

| # | Deliverable | Where | Status |
|---|---|---|---|
| 1 | **The working connection**: QuickBooks sandbox to Claude | `.mcp.json` (config), `.claude/settings.json` (read-only permission rules), `quickbooks-online-mcp-server/` (Intuit's official server), `docs/quickbooks_setup.md` (both paths, choice, rejected options, credentials, team hosting), `tests/integration/test_quickbooks_mcp.py`, `scripts/preflight_check.py`. Also a direct read-only reader for the CLI: `pull-quickbooks`, `finance_engine/sources/quickbooks_api.py`, `tests/test_quickbooks.py`, `scripts/qb_smoke.py` | Working against the sandbox, local only |
| 2 | **The skill file(s)** | `.claude/skills/monthly-financial-analysis/` (`SKILL.md` and `reference/`), backed by `finance_engine/` and `config/clients/*.yaml` | Working |
| 3 | **The eval cases and grading approach** | `evals/cases/` (19 cases), `evals/grading.md`, `evals/run_engine_evals.py`, `evals/run_skill_evals.py`, `evals/baseline/`, `evals/CHANGELOG.md`, `evals/evaluation_report.md` | Working |
| 4 | **The one-page architecture note** | `docs/architecture_one_page.md` (about 450 words); detail in `docs/architecture.md`, hosting plan in `docs/future_hosting.md` | Written |

Also: `docs/requirements_checklist.md` (every requirement with evidence and status),
`docs/decisions.md` (why each choice), `docs/demo_checklist.md` and
`docs/live_demo_runbook.md` (the live readout).

**Not done:** the brief's *stretch goal* (loading the workbook into the sandbox and running
the skill through the live connection). Nothing is deployed anywhere.

## Setup from scratch

Windows PowerShell shown; on macOS or Linux use `python3 -m venv ~/venvs/re-assignment`
and `source ~/venvs/re-assignment/bin/activate`.

1. **Make a virtual environment OUTSIDE OneDrive** (OneDrive syncs and locks thousands of
   small files in a venv, which breaks installs and slows everything):

   ```powershell
   py -3.12 -m venv C:\venvs\re-assignment
   C:\venvs\re-assignment\Scripts\Activate.ps1
   python -c "import sys; print(sys.prefix)"     # must print C:\venvs\re-assignment
   ```

2. **Install** the engine's requirements, and the optional ones for cloud commentary and
   the web UI:

   ```powershell
   python -m pip install -r requirements.txt
   python -m pip install -r requirements-optional.txt   # google-genai, anthropic, fastapi, uvicorn, python-multipart
   ```

3. **Settings:** `copy .env.example .env` (placeholders only; never commit `.env`). The
   engine needs no key. Keys are only for the optional Gemini / Claude API commentary and
   the QuickBooks reader (see their sections below).

4. **Test data:** `python scripts/make_test_workbooks.py` (the synthetic test clients;
   the tests also build them when missing). Put the supplied ABC workbook at
   `data/clients/abc_inc/source/ABC Inc - Financial Statements.xlsx` if you have it.

5. **Run the tests:** `python -m pytest -q` (UI tests skip if fastapi is not installed),
   then `python evals/run_engine_evals.py --compare` (free).

6. **Run the CLI:** `python -m finance_engine report --client test_clean --period 2026-01`.

7. **Run the UI:** `python -m finance_engine serve`, open `http://127.0.0.1:8765`.
   Local only (it refuses any other host), **no sign-in**: reviewer names are typed in and
   self-declared. Check it end to end with `python scripts/ui_smoke.py`. A guided click
   path: `docs/ui_walkthrough.md`.

**Every UI action has a terminal equivalent** (full table: `docs/ui.md`):

| In the UI | In the terminal (`python -m finance_engine ...`) |
|---|---|
| Choose client, file, period | `list-clients`, `list-files --client ID`, `list-periods --client ID [--file NAME]` |
| See a report, its badges and figures | `show-report --client ID --period P [--file NAME]`, `brief ...` |
| Generate (Keep data on this machine) | `report --client ID --period P --provider deterministic` (unticked: `--provider auto`) |
| Year / whole file | `--scope year --year YYYY`, `--scope all` on the commands above |
| Charts, "view as table" | `chart-data --client ID [--file NAME] [--year YYYY]` |
| Add company: upload, validate, approve | `init-client --draft --file book.xlsx --client-id ID --name "Co"`, `validate-config --client ID --draft [--from edited.yaml]`, `approve-config --client ID --by "Name"` |
| Review: a generated report is Ready for review; mark as reviewed, send back, log, queue | `review mark-reviewed`, `send-back`, `status`, `log` with `--client ID --period P --by "Name"` (send-back also `--comment "..."`); `review queue`; `review submit` only for a hand-edited report.md |
| Evals panel, Run free evals | `list-evals`; `python evals/run_engine_evals.py --compare` and `python evals/run_skill_evals.py --provider deterministic --compare` |
| QuickBooks status and pull | `quickbooks-status`; `pull-quickbooks --client qb_sandbox --start YYYY-MM --end YYYY-MM` |
| Provider chain | `provider-status` |
| Download report.md / metrics.json | the paths `show-report` prints |

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
`init-client` drafts that YAML from the workbook (below).

### Years, the whole file, several workbooks

```bash
python -m finance_engine report --client test_clean --scope year --year 2025   # year-2025/
python -m finance_engine report --client test_clean --scope all                # all/
python -m finance_engine list-periods --client test_multi                      # each workbook
python -m finance_engine analyze --client test_multi --period 2025-06 --file test_multi_second_half.xlsx
```

`--scope year --year YYYY` totals a calendar year from its monthly figures, with quarters,
same-months year on year, a monthly table and the highest and lowest months. A partial
year is labelled ("year to date, 1 of 12 months") and never annualised; a month missing
inside the year makes the totals n/a. `--scope all` adds a monthly series for charts, a
total per calendar year and the first-to-last-month change. When a client's folder holds
several workbooks, `--file NAME` picks one (outputs go to
`output/clients/<client>/<file-stem>/...`); without it the engine uses the config's
`source.filename`, or, if the config names none, the one workbook holding the requested
period, and stops if more than one does. Files are never combined. Every command takes
`--json` for one machine-readable result on stdout.

**`--file` or `--workbook`?** Both name the workbook to read. `analyze`, `report`,
`brief` and `list-periods` accept either. `check-commentary` accepts only `--workbook`,
because its `--file` was already the commentary being checked, and that meaning is kept:

```bash
python -m finance_engine analyze --client test_multi --period 2025-06 --file test_multi_second_half.xlsx
python -m finance_engine check-commentary --client test_multi --period 2025-06 \
    --workbook test_multi_second_half.xlsx --file report.md
```

### A new client from a workbook

```bash
python -m finance_engine init-client --file path/to/book.xlsx --client-id acme_ltd --name "Acme Ltd"
```

It finds the income-statement sheet, the header row, label and data columns, date order
and format, the revenue / COGS / operating-expense sections with their subtotal groups,
and the Gross Profit, Operating Income and Net Income rows; proposes operating-expense
categories from common words (each marked a guess); copies the workbook to
`data/clients/acme_ltd/source/` (the original is untouched); then validates the draft
with the real engine. It writes `config/clients/acme_ltd.yaml` only if the config loads,
the periods read, the latest month analyses and every reconciliation check passes;
otherwise `acme_ltd.draft.yaml` with each problem listed. The file starts with what was
detected, how confident each value is, and what needs your decision. It never overwrites
a config without `--force`. Automated tests cover test_clean and Companies A and B (the
generated config gives the same figures as the hand-written one); Company J and ABC were
also run by hand and validated. It does not configure balance sheets, and it refuses a
workbook with no row of month headers.

## Commentary from Gemini or the Claude API (optional, no Claude Code needed)

`report` can have the commentary written by a cloud model instead of the template:

```bash
pip install -r requirements-optional.txt          # google-genai, anthropic (optional)
python -m finance_engine report --client test_clean --period 2026-01 --provider auto
python -m finance_engine report --client test_clean --period 2026-01 --provider gemini
```

What happens, every time:

- The model receives only the compact `brief` (month, year or whole file) and the writer
  prompt in `finance_engine/commentary/writer_prompt.md`, never metrics.json, the
  workbook or a key. It writes three sections (Summary, What moved, Questions for the
  reviewer). Every table, status line and check in the report stays Python-made.
- Its text is checked in process: every number and direction word (the same guard as
  `check-commentary`), causal wording outside the reviewer questions, and every failed
  reconciliation month named. A failure is sent back to the same model with the exact
  problems, up to `max_attempts` (2) times; then the next provider in
  `config/providers.yaml` is tried. **The last step is always the deterministic
  template, so a model that keeps failing the checks simply falls back to the
  deterministic report. That is the safe behaviour, not an error.**
- A transient failure (HTTP 5xx, 429, a timeout) is retried twice, after about 2 and
  then 5 seconds; these retries are not check attempts and are recorded in the log.
  After that, or for any other failure (a missing package or key, a disabled provider,
  an invalid model name, a rejected request), the provider is skipped with one line on
  stderr. Keys are never printed, logged or written.
- `commentary_log.json` is written next to `report.md`: every provider tried, each
  attempt's pass or fail and violations, token counts per call, and
  `data_left_machine`. The report ends with one line naming the provider, the attempt
  and whether data left the machine. `--json` includes the same summary.

The existing `--provider` values (`deterministic` and the rest) are unchanged and write
neither the log nor the footer.

**Gemini key (Google AI Studio).** Sign in at <https://aistudio.google.com/>, choose
*Get API key*, create a key, and put it in the git-ignored `.env` in the project root:

```text
GEMINI_API_KEY=<your key>
```

Then find a model your key can use and set it as `providers.gemini.model` in
`config/providers.yaml` (the committed default is marked unverified, because Gemini
model IDs change):

```bash
python scripts/provider_smoke.py --list-models
python scripts/provider_smoke.py --provider gemini --client test_clean --period 2026-01
```

The smoke script makes one real call and prints only pass or fail, the violations, the
model that answered and the token counts; add `--full` to run the real orchestrator for
that provider (check retries with the violations sent back, and transient retries).
Gemini counts thinking tokens against `max_output_tokens`, so `config/providers.yaml`
sets `thinking_level: low` (Gemini 3 models; Gemini 2.5 models use `thinking_budget`
instead) and `max_output_tokens: 16384`; thinking tokens are logged separately when the
API reports them.

**The Claude API is separate from Claude Pro, and is OFF by default.** It needs its own
key and prepaid credits from the Anthropic Console (<https://console.anthropic.com/>),
billed per token apart from any Claude Pro plan. It runs only after you set
`providers.claude-api.enabled: true` in `config/providers.yaml` and
`ANTHROPIC_API_KEY=<your key>` in `.env`. The configured model is `claude-sonnet-5-5`;
Anthropic's server-side refusal fallback is off (`refusal_fallback: false`), so a refusal
skips to the next provider. `commentary_log.json` records the model that actually
answered each attempt. `allow_cloud: false` in the same file stops every cloud provider at once.

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

## QuickBooks direct reader (CLI, read-only)

A second, separate path for the report pipeline: Python pulls the sandbox Profit and Loss
(one column per month), saves the raw response as a snapshot file, and the engine analyses
that file like a workbook. It uses its own Intuit app and token, never the MCP server's.
Set `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_REFRESH_TOKEN`, `QBO_REALM_ID` and
`QBO_ENVIRONMENT=sandbox` in the root `.env` (names in `.env.example`), then:

```bash
python -m finance_engine pull-quickbooks --client qb_sandbox --start 2025-10 --end 2026-10
python -m finance_engine report --client qb_sandbox --period 2026-09 --file <snapshot>.json
python -m finance_engine report --client qb_sandbox --period 2026-09 \
    --source quickbooks --start 2025-10 --end 2026-10      # pull, then analyse that pull
python scripts/qb_smoke.py                                  # by hand: one refresh, one GET
```

GET only (the token refresh is the one POST), sandbox host only, secrets redacted from
every message. Other Income and Other Expenses stay outside revenue and operating
expenses; a blank QuickBooks cell is read as 0, as Intuit's own totals do. Details,
re-authorising and the mapping: `docs/quickbooks_setup.md`. Case 19 runs on a scrubbed
copy of a real pull (`tests/fixtures/qb_pl_monthly.json`, client `qb_sandbox_fixture`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No config for client 'x'` | The client has no YAML. The error lists the clients that do. |
| `Period must look like YYYY-MM` / `period_not_in_source` | Use `list-periods` to see the available months. |
| `... is in 2 workbooks ... choose one with --file NAME` | The client's folder holds several workbooks with that month; pass `--file`. |
| `init-client` wrote a `.draft.yaml` | Validation failed; the problems are listed at the top of the draft. |
| ABC cases say SKIP | The workbook is not at `data/clients/abc_inc/source/`. |
| `check-commentary` says REJECTED | A figure is not in `metrics.json`, or a direction word contradicts its change. The output names the line. |
| QuickBooks `401` / `invalid_grant` | Token lapsed: `cd quickbooks-online-mcp-server && npm run auth`. |
| `pull-quickbooks`: "The token refresh was refused" | The reader's own token lapsed: re-authorise its Intuit app and update `QBO_REFRESH_TOKEN` (`docs/quickbooks_setup.md`, "Re-authorising"). |
| `QuickBooks has no Profit and Loss data between ...` | The sandbox has no activity in that range; nothing was saved. Pick another range. |
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
- **The skill needs a Pro session.** A full 13-case run cost about $4 to $5 API-equivalent;
  the five Phase 5B cases (14 to 18) have not been run through Claude.
- **Sandbox only.** The QuickBooks data is Intuit's sample data, not evidence about a client.
- **QuickBooks: sandbox Profit and Loss only.** The direct reader pulls one report, from
  the sandbox only; production, the balance sheet and other reports are not read. A
  blank QuickBooks cell is read as 0 (Intuit's convention), unlike a blank workbook cell.
- **Stretch goal not attempted.**
