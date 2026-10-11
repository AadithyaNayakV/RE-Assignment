# Metric definitions

A summary of `docs/metrics_schema.md` (the authoritative contract) for writing the report.
Every metric is calculated by `finance_engine`. This file says what a metric **means**,
so you describe it correctly. It never tells you how to calculate one, because you don't.

## Families

| Metric name | Unit | Meaning |
|---|---|---|
| `revenue.total` | currency | Sum of the revenue lines, reconciled to the workbook's own Total Income row. |
| `revenue.stream.<slug>` | currency | One revenue line. |
| `revenue.stream.<slug>.share_of_revenue_pct` | percent | That stream's share of the month's revenue. |
| `cogs.total`, `cogs.line.<slug>` | currency | Cost of goods sold, total and per line. |
| `gross_profit` | currency | Revenue minus COGS. |
| `gross_margin_pct` | percent | Gross profit as a percentage of revenue. |
| `opex.total` | currency | Every operating expense line, each counted once. |
| `opex.category.<slug>` | currency | A presentation category set by the client config. Categories partition opex, so they sum to `opex.total`. |
| `opex.category.<slug>.share_of_opex_pct` | percent | The category's share of operating expenses. |
| `opex.group.<key>` | currency | A subtotal group as the workbook presents it; `ungrouped` = lines outside any group. |
| `opex.line.<slug>` | currency | One operating expense line. |
| `operating_income` | currency | Gross profit minus operating expenses. |
| `operating_margin_pct` | percent | Operating income as a percentage of revenue. |
| `net_income.reported` | currency | The workbook's own Net Income row, read, not calculated. |
| `balance_sheet.<key>` | currency | `cash`, `accounts_receivable`, `deferred_revenue`, `assets`, `liabilities`, `equity`, `liabilities_and_equity`. |

Suffixes on any base metric:

| Suffix | Meaning |
|---|---|
| `.mom_abs` | This month minus the prior calendar month. |
| `.mom_pct` | The same as a percentage of the prior month. Only when the prior month was strictly positive. |
| `.mom_pts` | Change in a margin, in **percentage points**. |
| `.yoy_abs`, `.yoy_pct` | Against the same month a year earlier. |
| `.ttm` | Sum over the trailing twelve calendar months. Only when all twelve exist. |

## When a value is `n/a`

| `unavailable_reason` | Write it as | Meaning |
|---|---|---|
| `zero_base` | n/a (prior period was zero) | A % change from zero is undefined. |
| `negative_base` | n/a (prior period was negative) | A % of a negative base reads backwards. Use the absolute change. |
| `sign_change` | n/a (crossed zero) | The line swung between a charge and a credit. Describe the swing, not a %. |
| `missing_input` | n/a (no comparable value) | An input was blank or absent. Blank is not zero. |
| `incomplete_window` | n/a (window incomplete) | The TTM window is not fully covered. |
| `blocked_by_validation` | n/a (blocked by a data-quality error) | A data-quality error applies. |
| `not_available` | n/a (not present in the source) | The concept does not exist in this source. |

`validation_status`: `ok`; `warning` (computed, and a warning travels with it, so
mention it); `blocked` (value is n/a and must not be estimated).

## Movements and flags

- **Significant movement.** A line whose month-over-month change clears the configured
  absolute threshold, or clears the % threshold *and* the absolute floor, or crossed zero
  by at least the floor. The brief prints the exact rule. It means the change is large;
  it does not mean the change is bad, unusual or explained.
- **Possible one-off.** The reporting month's value is large compared with the line's own
  trailing average. Flagged on magnitude only. Always "possible".
- **Large item inside the TTM window.** A month inside the TTM window that is far above the
  other months of the window. The TTM figures that include it are **not a run rate**.
- **Seasonality.** Only assessed with at least 24 months, so each calendar month appears
  twice. If the brief says "NOT assessed", no movement may be called seasonal.
- **Reconciliation.** The engine's own totals against the workbook's formula totals, for
  every month, within a tolerance of about a cent. A failure means the figures that depend
  on that total must not be relied on.

## Data-quality codes

| Code | Meaning for the reader |
|---|---|
| `period_not_in_source` | The month asked for is not in the workbook. Everything is blocked. |
| `missing_months` | Some months are absent from the source; comparisons across them are n/a. |
| `ttm_window_incomplete` | The TTM window is not fully covered; every TTM figure is n/a. |
| `ttm_includes_possible_one_offs` | Named TTM figures include large items and are not a run rate. |
| `formula_value_unreadable` | A formula cell had no cached value; the affected figure is n/a. |
| `blank_not_zero` | A line is blank this month but has values in others. Treated as missing. |
| `duplicate_line_labels` | The same label appears on more than one row; each row is kept separate. |
| `negative_revenue_line` | A revenue line is negative (e.g. refunds). Kept with its sign. |
| `negative_opex_line`, `negative_cogs_line` | An expense line is negative (e.g. a recovery or reversal). Kept with its sign, never treated as income. |
| `opex_category_unmatched` | An expense line matched no configured category and went to the fallback category. |
| `possible_unit_scaling` | The workbook headings suggest the figures are in thousands or millions. |
| `no_balance_sheet` | The source has no balance sheet; balance-sheet metrics are n/a. |
| `statement_period_mismatch`, `balance_sheet_period_missing` | The statements disagree about which months exist. |

`docs/metrics_schema.md` holds the full list. If the brief shows a code that is not in
this table, show its message as printed.

## Extending this skill

**A new company** is one file, `config/clients/<client_id>.yaml`, plus its workbook. The
skill does not change.

**A new metric:**
1. Calculate it in `finance_engine/metrics.py` and register its `method_id`.
2. Show it in `finance_engine/brief.py`, so writers see it formatted.
3. Add a row to the Families table above.
4. Add a heading to `report_template.md` only if it needs its own section.
5. Add its expected value to a golden case in `evals/cases/` and run
   `python evals/run_engine_evals.py`.

`SKILL.md` does not change in either case.
