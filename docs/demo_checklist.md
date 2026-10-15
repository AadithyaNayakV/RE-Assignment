# Demo checklist

Each item: what to run, what you should see, whether I verified it and how, and one line
for the interviewer. **Verified** means I ran it on 2026-10-11 and saw the stated result.
Where I could not run something (an interactive Claude Code screen), it says so.

Run everything from the repo root.

---

### 1. Show the ABC workbook
- **Do:** open `data/clients/abc_inc/source/ABC Inc - Financial Statements.xlsx`; point at
  Income Statement `C14` (Total Income) and `C11` (Usage Fees).
- **Expect:** 18 months, newest first; `C14` = 195,898.16.
- **Verified:** yes. Read with openpyxl: row 14 is `Total Income`, C = Jun 2026 = 195,898.16.
  SHA-256 `75da8d679a89551f…` matches `scripts/reference_hashes.json`.
- **Say:** "This is the supplied file. The engine opens it read-only and I check its hash
  before and after every run."

### 2. Run the skill on ABC Jun 2026; show report.md
- **Do:** in Claude Code, type `Analyse ABC Inc for June 2026`.
- **Expect:** the skill runs `analyze`, `brief`, writes `output/clients/abc_inc/2026-06/report.md`,
  runs `check-commentary` and ends with PASS. About 90 to 110 s.
- **Verified:** yes, twice in this build. Headless eval run: 13 turns, 92 s, passed 46/46
  checks. Interactive run in this session: guard PASS 294/294 then 313/313.
- **Say:** "Claude writes the words. Python wrote every number, and the guard checked them."

### 3. Numbers match metrics.json and the workbook
- **Do:** `python -m finance_engine brief --client abc_inc --period 2026-06`, then compare
  Total revenue with the workbook `C14`.
- **Expect:** `$195,898.16` in the report, the brief, `metrics.json` and the workbook.
  Reconciliation 144/144.
- **Verified:** yes (item 1's cell, the report line `Total revenue was $195,898.16`, and
  `python -m finance_engine analyze` printing `144/144 passed`). Level 1 also checks 61
  ABC values against the workbook's own cached totals.
- **Say:** "The engine sums line items itself and reconciles to the workbook's formula
  totals, so a wrong config would fail loudly."

### 4. A synthetic company with a different layout (config only)
- **Do:** `python -m finance_engine report --client company_h_zero_negative_values --period 2026-06`
- **Expect:** works. Labels in columns C/D, data from F, header row 7, sheet "Profit and
  Loss", all set in `config/clients/company_h_zero_negative_values.yaml`.
- **Verified:** yes. Eval case 07 passes 71/71. Rehearsal C added a whole new client with
  one YAML and no code change (108/108 reconciliation).
- **Say:** "A new company is a YAML file, not new code."

### 5. Missing-data and one-off cases
- **Do:** `python -m finance_engine report --client company_g_missing_data --period 2026-06`;
  then `... --client company_f_one_time_expense --period 2025-11`.
- **Expect:** G: two absent months, `ttm_window_incomplete`, every TTM figure `n/a`, exit 2
  by design. F: Legal Settlement $285,000.00 flagged as a possible one-off, no multiple
  because its average is zero.
- **Verified:** yes. Eval cases 06 and 09 pass (56/56, 61/61); the Claude-written reports
  for both passed Level 2.
- **Say:** "Missing data is never turned into zero, and an incomplete year is never called
  TTM."

### 6. The guard rejects an invented number
- **Do:** write a line with a made-up figure to a file and run
  `python -m finance_engine check-commentary --client abc_inc --period 2026-06 --file bad.md`
- **Expect:** `REJECTED — 1 of 4 number(s)… '$41,234.56' … closest supported value 43,645.59`,
  exit 3. A wrong direction ("Revenue rose $12,452.80") is rejected too:
  `'rose' before '$12,452.80': the matching change is negative`.
- **Verified:** yes, both outputs captured today.
- **Say:** "Even if the model invents a figure, it cannot reach the report."

### 6b. Deterministic fallback: a valid labelled report with no AI
- **Do:** `python -m finance_engine report --client abc_inc --period 2026-06` (the default
  provider is `deterministic`). To show the fallback, add `--provider ollama`.
- **Expect:** a full report, labelled "`deterministic` commentary provider", number check
  PASS 66/66; with `--provider ollama`, a warning "Falling back to the deterministic provider".
- **Verified:** yes. Eval case 13 (17/17 Level 1, 29/29 Level 2). Backup file:
  `demo_backup/abc_inc_2026-06_deterministic_report.md`.
- **Say:** "If every model is down, the firm still gets a correct draft."

### 7. Claude Code lists the QuickBooks MCP tools
- **Do:** in Claude Code type `/mcp`, select `quickbooks`, view tools. Terminal check:
  `claude mcp list`.
- **Expect:** `quickbooks … ✔ Connected`; 71 read tools visible (142 registered; the 71
  create/update/delete tools are denied).
- **Verified:** partly. `claude mcp list` shows Connected, and the server lists 142 tools
  over stdio (preflight). I could not open the interactive `/mcp` screen from here, so
  rehearse it once yourself.
- **Say:** "Claude can read QuickBooks but cannot write to it. That is enforced in settings."

### 8. A live read-only sandbox request
- **Do:** in Claude Code: `Use the quickbooks tools to get the company info.`
- **Expect:** `Sandbox Company US 209f`, country US.
- **Verified:** yes, through the MCP server by `scripts/preflight_check.py` (18.6 s) and by
  `QBO_INTEGRATION=1 QBO_LIVE=1 pytest tests/integration` (6 passed). The tool takes
  `{"params": {}}`, not `{}`. Saved fallback:
  `demo_backup/quickbooks_sandbox_get_company_info_SAVED.md`.
- **Say:** "Local official server, OAuth against the sandbox, tokens refresh by themselves."

### 9. Engine evals, one skill eval, and the baseline compare
- **Do:**
  `python evals/run_engine_evals.py --compare` (about 30 s), then
  `python evals/run_skill_evals.py 04` (about 90 s, Pro usage).
- **Expect:** 20 passed. `--compare` lists cases 14 to 20 as new until the baseline is
  re-saved; nothing regressed. Then 04 passes.
- **Verified:** yes. Level 1 20/20 (2026-10-15). Full Claude Level 2 run (2026-10-11, the 13
  cases that existed then): first graded 12/13, then 13/13
  after fixing a checker false positive (documented). ABC single-case: 46/46.
- **Say:** "A fault in my own checker is a defect too, so each fix has a test that proves the
  check still catches what it exists for."

### 10. Connection choice, rejected options, team hosting
- **Say:** Intuit's official MCP server, local over stdio, because it already does OAuth
  rotation and has 639 passing tests; rejected a native connector (I found none), community
  servers (no Intuit backing) and a custom server (would mean writing OAuth and 29 entity
  wrappers). For a team: host it over HTTPS behind SSO, one token store per client in a
  secret manager. **Planned, not built.** See `docs/quickbooks_setup.md`, `docs/decisions.md`.
- **Verified:** documentation only.

### 11. Ten-client separation
- **Say:** everything is keyed by `client_id`: config, data, output, QuickBooks token store.
  Separation is by path and convention today; the skill is told one client per run and an
  eval fails a report naming another client. **No access control exists yet.** Say this
  plainly. See `docs/architecture.md` sections 5 and 6.
- **Verified:** documentation, plus the `client_isolation` eval check.

### 12. Live modification rehearsal
Three changes you might be asked for, each done once for real and reverted with Git on
2026-10-11. Exact steps are in `docs/live_demo_runbook.md`.

| Change | Files | Proof | Result |
|---|---|---|---|
| Change the significance threshold | `config/clients/abc_inc.yaml` | `analyze` + `brief`, then `run_engine_evals.py 01` | ABC significant movements 11 → 6; evals still pass (values unchanged); reverted |
| Add a metric (`opex_pct_of_revenue`) | `finance_engine/metrics.py`, `brief.py`, `evals/tools/build_expected.py`, `metric_definitions.md` | `build_expected.py 02`, `run_engine_evals.py 02` | 65/65 pass (42.57% = 79,004 / 185,600); a wrong denominator made it FAIL; reverted |
| Add a new company | `config/clients/<id>.yaml` only | `list-periods`, `report`, full engine evals | reconciliation 108/108, guard PASS, 13/13 evals (all 13 cases on 2026-10-11; 19 now); removed |
