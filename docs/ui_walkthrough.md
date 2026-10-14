# UI walkthrough: 12 steps

A click path through the local web app with synthetic and test data only. About 15
minutes. Each step names its terminal equivalent; `docs/ui.md` has the full table.

**Before you start:** `python -m finance_engine serve`, then open
`http://127.0.0.1:8765` in a browser. The banner says the app is local, has no sign-in,
and that names are self-declared.

| # | Do this | You should see | Terminal equivalent |
|---|---|---|---|
| 1 | **Reports**: choose client *Test Clean Co (SYNTHETIC)*, scope **Month**, month **2026-01**. | The Generate panel with **Keep data on this machine** ticked and "Data stays here", then the badges (Reconciliation all pass 65/65, Number check 34/34 traced, Guard PASS, Provider deterministic, Data left this machine: no, Review: draft) and the rendered report. If no report exists yet, an info box names the command to run. | `python -m finance_engine show-report --client test_clean --period 2026-01` |
| 2 | Click **Generate report**. | A live log, then "Report written" and the page reloads with fresh badges. | `python -m finance_engine report --client test_clean --period 2026-01 --provider deterministic` |
| 3 | Untick **Keep data on this machine** (do not run). | The notice changes to what would leave: the compact brief, and reviewer notes if the last review action was a send-back; never the workbook. The terminal line switches to `--provider auto`. Tick it again. | `python -m finance_engine provider-status` |
| 4 | In the report's Summary, click an underlined figure such as **$61,000.00**. | The **Trace** tab: metric `revenue.total`, its method, and the source cells (sheet, cell such as C7, raw value). Figures that match more than one metric are not underlined. | `python -m finance_engine show-report --client test_clean --period 2026-01 --json` (each metric's `source_rows`) |
| 5 | Open **Numbers**, type `margin` in the filter, click **Trace** on Gross margin. | Every value as the engine formatted it, with n/a reasons where a value is unavailable. | `python -m finance_engine show-report --client test_clean --period 2026-01 --json` |
| 6 | Switch client to *Test Errors Co (SYNTHETIC)*, month **2025-12**, tab **Warnings**. Filter by `non_numeric_cell`. | Cell-level warnings with sheet and cell (F20, raw value `"n/a"`; E22 shown as a blank cell), and the reconciliation failure table with computed, workbook and difference. **Trace** on a row shows the cell or the rows checked. | `python -m finance_engine brief --client test_errors --period 2025-12` |
| 7 | Back on Test Clean, scope **Whole file**, tab **Charts**. Click **View as table** on one chart. | Three charts (revenue/gross profit/operating income, net income, margins) with dashes and marker shapes as well as colours; the table shows each month's value and status. A gap or failed month would be listed under the chart. If net income months are "not analysed", **Analyse those months** fills only the missing month files. | `python -m finance_engine chart-data --client test_clean` |
| 8 | Scope **Year**, year **2025**, tab **Charts**. | The label "2025: full year, 12 of 12 months" and "nothing is annualised". (Test Multi's first-half file shows a partial year.) | `python -m finance_engine chart-data --client test_clean --year 2025` |
| 9 | Month **2026-01**, tab **Review**. Type a name, **Submit for review**. Then type the **same** name and click **Approve**. | Status in_review. The approval is refused with a message naming `config/review.yaml` (`require_different_approver: true`). | `python -m finance_engine review submit --client test_clean --period 2026-01 --by "A. Writer"` |
| 10 | Type a different name, write a comment, **Send back**; then submit again and **Approve** with the second name. Open **Review queue** in between. | changes_requested, then in_review (the queue lists it), then approved. The append-only log and the stored versions appear under the form; **View** opens a stored copy. | `python -m finance_engine review send-back ... --comment "..."`, `review queue`, `review approve ... --by "B. Partner"`, `review log ...` |
| 11 | **Add company**: id `walk_co`, name *Walk Co (SYNTHETIC)*, upload a copy of the synthetic `data/clients/test_clean/source/test_clean.xlsx` (copy it out of the repo first, for example to your Desktop; `python scripts/make_test_workbooks.py test_clean` rebuilds it if missing). Validate, then **Approve config** with your name, then **Generate report**. | Detected sheet and rows, "Checks 65/65 passed", the editable YAML (break `date_order` to see an error linked to its line, then undo), the approval record, and finally the new client's **Review** tab. | `init-client --draft ...`, `validate-config --client walk_co --draft`, `approve-config --client walk_co --by "Name"`, `report --client walk_co --period 2026-01` |
| 12 | **Evals**, then **Sources and settings**. | The 19 golden cases with their saved baseline results; **Run free evals** (optional, a few minutes); no button for paid evals, only the command. QuickBooks: sandbox badge, configured yes/no, names of missing variables; the pull needs a date range and the confirm tick. Provider chain read-only. Switch the theme to Dark, reload: it stays dark. | `python -m finance_engine list-evals`, `quickbooks-status`, `provider-status` |

**Clean up after:** delete `config/clients/walk_co.yaml`, `data/clients/walk_co/` and
`output/clients/walk_co/`, and the `review/` folders the walkthrough created under
`output/clients/test_clean/2026-01/`.

**Print to PDF:** on any report, **Print / save as PDF** gives the badges and the report
only (the print stylesheet hides the controls and tabs).
