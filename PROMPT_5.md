# PHASE 5 (OPTIONAL) — STRETCH GOAL: RUN THE SKILL THROUGH THE LIVE QUICKBOOKS CONNECTION

Only do this after Phases 1–4 pass. Read `CLAUDE.md` and `docs/quickbooks_setup.md` first. Do not break anything that already works.

Goal from the brief: load the workbook's data into my QuickBooks **sandbox** and run the same skill end-to-end through the live connection.

1. Check which write tools the installed QuickBooks MCP server (or the QuickBooks API) supports for sandbox data (e.g. accounts, journal entries). Do not guess.
2. Plan the smallest useful load: e.g. 3–6 months of ABC Income Statement data as monthly journal entries mapped to sandbox accounts. Write the mapping in a config file. Sandbox only, never a real company.
3. Make the loader re-runnable and safe (dry-run mode first; tag entries so they can be identified and removed).
4. Add `finance_engine/sources/quickbooks.py` implementing the same `DataSource` interface, reading a P&L report for those months and producing the same normalized data as the Excel source.
5. Run the skill with source = QuickBooks and compare its metrics.json with the Excel-based metrics.json for the same months. Differences must be explained (e.g. sandbox sample data already present).
6. Add one golden case: "QuickBooks source matches Excel source for the loaded months" (kept separate and skippable).
7. Document the steps, real results and limitations in `docs/quickbooks_setup.md` and update `docs/requirements_checklist.md`.

Never print tokens. Do not claim success without the comparison result.