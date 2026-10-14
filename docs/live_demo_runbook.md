# Live demo runbook

The final round is a 45-minute live readout of the **real system**, not slides. Timings are
measured on this machine on 2026-10-11. Details and evidence for each step are in
`docs/demo_checklist.md`.

## 30 minutes before the call

```bash
python scripts/preflight_check.py            # about 80 s, uses about $0.05 of Pro usage
python scripts/preflight_check.py --no-claude-call   # same, zero Pro usage
```

It checks, with real results: dependencies and the full test suite (650 passed, 3 skipped on 2026-10-11); the ABC
workbook hash before and after the engine runs; `analyze` and `report` timing; a live
read-only QuickBooks call returning the sandbox company name; and that Claude Code sees the
QuickBooks server and the skill. Exit 0 and the word `READY` means go.

**If QuickBooks fails with 401 / invalid_grant**, the script prints these steps:
`cd quickbooks-online-mcp-server`, `npm run auth`, sign in and approve in the browser,
re-run the preflight. The Intuit app must list `http://localhost:8000/callback` as a
redirect URI. Allow 5 minutes. Do not leave this to the last minute.

## Demo order (copy and paste)

| # | Step | Command or action | Time |
|---|---|---|---|
| 1 | The workbook | Open `data/clients/abc_inc/source/ABC Inc - Financial Statements.xlsx` | 1 min |
| 2 | The skill, live | In Claude Code: `Analyse ABC Inc for June 2026`, then open `output/clients/abc_inc/2026-06/report.md` | 2 min |
| 3 | Numbers match | `python -m finance_engine brief --client abc_inc --period 2026-06`; compare Total revenue with workbook `C14` | 2 min |
| 4 | Different layout | `python -m finance_engine report --client company_h_zero_negative_values --period 2026-06` | 1 min |
| 5 | Missing data, one-off | `python -m finance_engine report --client company_g_missing_data --period 2026-06` then `... --client company_f_one_time_expense --period 2025-11` | 2 min |
| 6 | Guard rejects | `python -m finance_engine check-commentary --client abc_inc --period 2026-06 --file bad.md` (prepare `bad.md` with `Legal Expenses were $41,234.56.`) | 1 min |
| 6b | No-AI fallback | `python -m finance_engine report --client abc_inc --period 2026-06 --provider ollama` | 1 min |
| 7 | QuickBooks tools | `/mcp` in Claude Code, open `quickbooks`; or `claude mcp list` | 1 min |
| 8 | Live sandbox read | `Use the quickbooks tools to get the company info` | 1 min |
| 9 | Evals | `python evals/run_engine_evals.py --compare`, then `python evals/run_skill_evals.py 04` | 3 min |
| 10 to 11 | Choices and ten clients | Talk through `docs/architecture.md`, `docs/decisions.md` | 5 min |
| 12 | Live modification | One of the three rehearsals below | 5 min |

About 25 minutes of demo, leaving time for questions. Run step 6b **after** step 2: it
overwrites `report.md` with the template version.

If asked about years, the whole file or several workbooks: `python -m finance_engine report
--client test_clean --scope year --year 2025` (or `--scope all`), and
`python -m finance_engine list-periods --client test_multi`.

**`--file` or `--workbook`?** Both name the workbook to read. `analyze`, `report`,
`brief` and `list-periods` accept either. `check-commentary` accepts only `--workbook`,
because its `--file` was already the commentary being checked, and that meaning is kept:

```bash
python -m finance_engine analyze --client test_multi --period 2025-06 --file test_multi_second_half.xlsx
python -m finance_engine check-commentary --client test_multi --period 2025-06 \
    --workbook test_multi_second_half.xlsx --file report.md
```

## If something fails live: backups

Say plainly which is a backup. Files are in `demo_backup/` (see its README).

| Failure | Backup | What it really is |
|---|---|---|
| Claude is slow, or the Pro limit is reached | `demo_backup/abc_inc_2026-06_claude_skill_report.md` | A **saved** report the skill wrote earlier in the 46/46 eval run. Not live. |
| Same, and you want a live run anyway | `python -m finance_engine report --client abc_inc --period 2026-06` | A **live** run of the no-AI template. It is the fallback, not the skill. |
| Sandbox or token is down | `demo_backup/quickbooks_sandbox_get_company_info_SAVED.md` | **Saved** output, not a live call. |
| Evals are slow or fail | `demo_backup/eval_results_*.json` | **Saved** results. The Level 2 file is the re-graded 13/13; the first grading was 12/13. |

## Tips

- **Do not spend Pro usage the day before or the morning of the call.** The full 13-case
  skill run costs about $4 to $5 API-equivalent and I hit the session limit once while
  building. Run at most `run_skill_evals.py 04` during the demo.
- Close other apps and browser tabs. Keep the terminal font large.
- **Never show `.env` or `quickbooks-online-mcp-server/.env` on screen.** Do not `cat` them,
  do not open them in the editor, and keep the editor sidebar collapsed on that folder.
  Never paste a token into chat. Turn on screen-share only after the terminal is clear.
- Be ready to say what is not built: the stretch goal, hosting, authentication and tenant
  isolation, and the Ollama and paid-API providers.

## The web UI demo (Phase 5D)

Start it: `python -m finance_engine serve`, open `http://127.0.0.1:8765`. The full click
path is `docs/ui_walkthrough.md`; the route-to-command table is `docs/ui.md`. Say up
front: local only, no sign-in, reviewer names are self-declared, the UI never calculates.

| # | Step | In the UI | Terminal equivalent | Time |
|---|---|---|---|---|
| 1 | New company from scratch | **Add company**: id `demo_co`, name, upload a synthetic workbook (a copy of `data/clients/test_clean/source/test_clean.xlsx`); show the detected rows and "65/65 passed"; break `date_order` in the YAML and **Validate** to show the line-numbered error; fix it, **Validate**, **Approve config** with your name | `init-client --draft --file book.xlsx --client-id demo_co --name "Demo Co"`, `validate-config --client demo_co --draft`, `approve-config --client demo_co --by "Name"` | 4 min |
| 2 | Report | Step 4 of the wizard: show the cloud notice (untick, read it, tick again), **Generate report**. It lands on the Review tab as **Ready for review**, and the top bar's Review queue count goes up | `report --client demo_co --period 2026-01 --provider deterministic` | 1 min |
| 3 | Badges and trace | Read the badge row; click an underlined figure in the Summary to open its source cell | `show-report --client demo_co --period 2026-01` | 2 min |
| 4 | Review | **Review queue** in the top bar, open the report: no Submit button (generating submitted it, as `system`). **Send back** with a comment; show "Changes requested" and the note that the template cannot follow comments; **Regenerate with this comment** (back to Ready for review); **Mark as reviewed** with your name; show the log and a stored version | `review queue`, `review send-back --client demo_co --period 2026-01 --by "Name" --comment "..."`, `report --client demo_co --period 2026-01`, `review mark-reviewed ... --by "Name"`, `review log ...` | 3 min |
| 5 | Warnings and charts | Switch to *Test Errors Co*, 2025-12, **Warnings** (cell F20, the failure table); then a whole-file **Charts** view with **View as table** | `brief --client test_errors --period 2025-12`, `chart-data --client test_clean` | 2 min |

Clean up after: delete `config/clients/demo_co.yaml`, `data/clients/demo_co/` and
`output/clients/demo_co/`. If the browser misbehaves, every step works from the terminal
column above.

## Six likely live modifications

Each names the exact file to change and the commands to rehearse it. Revert with
`git checkout <file>` (and delete any new file or output folder).

| # | Modification | File to change | Rehearse with |
|---|---|---|---|
| 1 | A new metric (for example opex as % of revenue) | `finance_engine/metrics.py` (emit it after `operating_margin_pct` with an existing method), `finance_engine/brief.py` (`HEADLINE` row), `evals/tools/build_expected.py` (expected value), `.claude/skills/monthly-financial-analysis/reference/metric_definitions.md` | `python evals/tools/build_expected.py 02`, `python evals/run_engine_evals.py 02`; then in the UI, Numbers tab: filter by the new name |
| 2 | A threshold change | `config/clients/<id>.yaml`, `thresholds:` (for example `significant_mom_min_abs`) | `python -m finance_engine analyze --client abc_inc --period 2026-06`, `python -m finance_engine brief --client abc_inc --period 2026-06`, `python evals/run_engine_evals.py 01` |
| 3 | A new client | none by hand: upload in **Add company**, or `init-client --draft` | `python -m finance_engine init-client --draft --file book.xlsx --client-id new_co --name "New Co"`, `validate-config --client new_co --draft`, `approve-config --client new_co --by "Name"`, `report --client new_co --period YYYY-MM` |
| 4 | A changed mapping (a line moved to another opex category, or a section anchor) | `config/clients/<id>.yaml` (`opex_categories[].match_contains`, or `income_statement.sections[]`), or edit the draft YAML in the wizard and **Validate** | `python -m finance_engine validate-config --client <id>`, then `report --client <id> --period YYYY-MM`; a broken anchor shows as a reconciliation failure, never a silent change |
| 5 | A new data-quality check | `finance_engine/validation.py` (add the check, emitting a warning with a `code` and, for a cell, `cells`), `finance_engine/config.py` (`CELL_CHECKS_DEFAULT` if it is a cell check), a test in `tests/test_cell_checks.py` | `python -m pytest -q tests/test_cell_checks.py`, `python -m finance_engine report --client test_errors --period 2025-12`; in the UI, Warnings tab: filter by the new code |
| 6 | A wording rule (a banned causal phrase) | `finance_engine/wording.py` (the `CAUSAL_WORDS` pattern; the evals share it), and `finance_engine/commentary/writer_prompt.md` if the providers should be told | `python -m pytest -q tests/test_report_wording.py tests/test_eval_checks.py`, `python evals/run_skill_evals.py --provider deterministic` (free) |

## The live modification rehearsal

Each was done once for real on 2026-10-11, then reverted with Git. Revert with
`git checkout <files>` (and delete any new file).

### A. Change the significance threshold
- **Edit:** `config/clients/abc_inc.yaml`, `thresholds.significant_mom_min_abs: 1000.0` to `3000.0`.
  (The other floor, `significant_mom_abs` 10000 to 5000, changes nothing for ABC June 2026.)
- **Run:** `python -m finance_engine analyze --client abc_inc --period 2026-06`, then
  `python -m finance_engine brief --client abc_inc --period 2026-06 | grep "qualifies on"`,
  then `python evals/run_engine_evals.py 01`.
- **Expect:** "6 of 46 changed lines qualified" (it was 11); case 01 still passes 61/61,
  because values are unchanged and the case only requires three named lines to stay flagged.
- **Recorded:** done; reverted with `git checkout config/clients/abc_inc.yaml`; 11 again.

### B. Add a metric (operating expenses as % of revenue)
- **Edit:** `finance_engine/metrics.py` (after `operating_margin_pct`, emit
  `opex_pct_of_revenue` with `ratio_pct(opex, rev)` and the existing `margin_pct_v1`
  method); `finance_engine/brief.py` (add a row to `HEADLINE`); `evals/tools/build_expected.py`
  (one `e.value(...)` line); `.claude/skills/monthly-financial-analysis/reference/metric_definitions.md` (a table row).
- **Run:** `python evals/tools/build_expected.py 02`, then `python evals/run_engine_evals.py 02`.
- **Expect:** 65/65, with `opex_pct_of_revenue` = 42.5668 (79,004 / 185,600). Change the
  denominator to `gp` and it must FAIL (`got 51.79, expected 42.57`).
- **Recorded:** done both ways; reverted with `git checkout` on those four files plus
  `evals/cases/02_normal_growth/expected.json`. SKILL.md did not change.

### C. Add a new company
- **Edit:** only `config/clients/company_k_demo.yaml`: copy an existing config, change
  `client_id`, `company_name` and `source.filename`.
- **Run:** `python -m finance_engine list-periods --client company_k_demo`, then
  `python -m finance_engine report --client company_k_demo --period 2026-06`, then
  `python evals/run_engine_evals.py`.
- **Expect:** 18 months, reconciliation 108/108, guard PASS, and every engine case (19) still passes.
- **Recorded:** done; the file and its output folder were deleted.
