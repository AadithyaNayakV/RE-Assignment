# UI walkthrough: 12 steps

A click path through the local web app with synthetic and test data only. About 15
minutes. Each step names its terminal equivalent; `docs/ui.md` has the full table.

**Before you start:** `python -m finance_engine serve`, then open
`http://127.0.0.1:8765` in a browser. The banner says the app is local, has no sign-in,
and that names are self-declared.

| # | Do this | You should see | Terminal equivalent |
|---|---|---|---|
| 1 | **Reports**: choose company *Test Clean Co (SYNTHETIC)*, scope **Month**, month **2026-01**. | The badges (Checks all pass 65/65, Number check pass 34/34 traced, Written by deterministic, Data left this machine: no, Review) and the rendered report. If no report exists yet, the page says "No report yet" and shows **Generate a report** with **Keep data on this machine** ticked. A report made before automatic review shows Review: Not in review. | `python -m finance_engine show-report --client test_clean --period 2026-01` |
| 2 | Open **Generate the report again** and click **Generate report**. | A loading line, then "Report written: it is ready for review"; the page reloads, the Review badge and tab say **Ready for review**, and the top bar's **Review queue** count goes up. | `python -m finance_engine report --client test_clean --period 2026-01 --provider deterministic` (prints `review: ready for review`) |
| 3 | Untick **Keep data on this machine** (do not run). | The notice changes to what would leave: the compact brief, and reviewer notes if the last review action was a send-back; never the workbook. The terminal line switches to `--provider auto`. Tick it again. | `python -m finance_engine provider-status` |
| 4 | In the report's Summary, click an underlined figure such as **$61,000.00**. | The **Source cells** tab: metric `revenue.total`, its method, and the source cells (sheet, cell such as C7, value in the cell). Figures that match more than one metric are not underlined. | `python -m finance_engine show-report --client test_clean --period 2026-01 --json` (each metric's `source_rows`) |
| 5 | Open **Numbers**, type `margin` in the filter, click **Show** on Gross margin. | Every value as the engine formatted it, with n/a reasons where a value is unavailable. | `python -m finance_engine show-report --client test_clean --period 2026-01 --json` |
| 6 | Switch company to *Test Errors Co (SYNTHETIC)*, month **2025-12**, tab **Warnings**. Filter by `non_numeric_cell`. | Cell warnings with sheet and cell (F20, value `"n/a"`; E22 shown as a blank cell), and **Failed checks** with Python's total, the workbook total and the difference. **Show** on a row opens the cell or the rows checked. | `python -m finance_engine brief --client test_errors --period 2025-12` |
| 7 | Back on Test Clean, scope **Whole file**, tab **Charts**. Click **View as table** on one chart. | Three charts (revenue/gross profit/operating income, net income, margins) with dashes and marker shapes as well as colours; the table shows each month's value and status. A gap or failed month would be listed under the chart. If net income months are "not analysed", **Analyse those months** fills only the missing month files. | `python -m finance_engine chart-data --client test_clean` |
| 8 | Scope **Year**, year **2025**, tab **Charts**. | The label "2025: full year, 12 of 12 months" and "nothing is annualised". (Test Multi's first-half file shows a partial year.) | `python -m finance_engine chart-data --client test_clean --year 2025` |
| 9 | Month **2026-01**, tab **Review – Ready for review** (after step 2). Open **Review queue** from the top bar, then **Open review** on the row. | "Next step: review this report", the log's first entry *system (automatic), Submitted*, written by the provider. There is no Submit button: generating the report submitted it. | `python -m finance_engine review queue`, `review status --client test_clean --period 2026-01` |
| 10 | Type a name and a comment, **Send back** (confirm). Then **Regenerate with this comment**, and finally type a name and **Mark as reviewed** (confirm). | Changes requested, with the comment and a note that the template writer cannot follow comments; after regenerating, Ready for review again (the queue count updates within 30 seconds, or at once on this page); then **Reviewed**. Send back without a comment, or Mark as reviewed without a name, is refused. The append-only log and the stored versions appear under the form; **View** opens a stored copy. | `review send-back --client test_clean --period 2026-01 --by "B. Partner" --comment "..."`, `report --client test_clean --period 2026-01`, `review mark-reviewed ... --by "B. Partner"`, `review log ...` |
| 11 | **Add company**: id `walk_co`, name *Walk Co (SYNTHETIC)*, upload a copy of the synthetic `data/clients/test_clean/source/test_clean.xlsx` (copy it out of the repo first, for example to your Desktop; `python scripts/make_test_workbooks.py test_clean` rebuilds it if missing). Validate, then **Approve config** with your name, then **Generate report**. | Detected sheet and rows, "Checks 65/65 passed", the editable YAML (break `date_order` to see an error linked to its line, then undo), the approval record, and finally the new client's **Review** tab. | `init-client --draft ...`, `validate-config --client walk_co --draft`, `approve-config --client walk_co --by "Name"`, `report --client walk_co --period 2026-01` |
| 12 | **Evals**, then **Sources and settings**. | The 19 golden cases with their saved baseline results; **Run free evals** (optional, a few minutes); no button for paid evals, only the command. QuickBooks: sandbox badge, configured yes/no, names of missing variables; the pull needs a date range and the confirm tick. Provider chain read-only. Switch the theme to Dark, reload: it stays dark. | `python -m finance_engine list-evals`, `quickbooks-status`, `provider-status` |

**Clean up after:** delete `config/clients/walk_co.yaml`, `data/clients/walk_co/` and
`output/clients/walk_co/`, and the `review/` folders the walkthrough created under
`output/clients/test_clean/2026-01/`.

**Print to PDF:** on any report, **Print or save as PDF** gives the report only, black
on white (the print stylesheet hides the controls, badges and tabs).

**Same person:** anyone can mark a report as reviewed, including the person who submitted
it (`config/review.yaml`, `require_different_reviewer: false`). Set it to `true` and a
person who submitted a hand-edited version (`review submit --by "A. Writer"`) must have
someone else mark it; names are self-declared, so this is a nudge.

**QuickBooks (optional):** company *QuickBooks Sandbox Company*: the **Snapshot** list shows
"Pulled 14 Oct 2026, 11:15 – Jul 2026 to Sep 2026", newest first; a snapshot that cannot be
read is greyed out at the bottom with a one-line reason. With none chosen, the newest
readable one is used and the line under the list says so
(`python -m finance_engine list-files --client qb_sandbox`).
