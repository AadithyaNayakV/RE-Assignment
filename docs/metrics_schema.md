# `metrics.json` — the contract

`metrics.json` is the only interface between the calculation engine and everything
downstream: the deterministic commentary provider, the number guard, the report builder,
and any future provider (a local model, the Claude API, a Claude skill). Nothing
downstream reads the workbook.

That boundary is the whole design. A commentary provider receives verified numbers with
their provenance and may describe them. It cannot calculate, because it has no inputs to
calculate from, and `guard.py` rejects any number in its output that is not in this file.

**Current version: `schema_version: 1`.** Any change that removes a field or changes a
field's meaning increments it. Adding a field does not.

---

## Top level

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | int | Contract version. Consumers should refuse a version they do not know. |
| `generated_at_utc` | string | ISO-8601 UTC, second precision. The only field that changes between two otherwise identical runs. |
| `client_id` | string | Keys everything: config, data directory, output directory. |
| `company_name` | string | Display name from the client config. |
| `period` | string | Reporting month, `YYYY-MM`. |
| `period_label` | string | Display form, e.g. `Jun 2026`. |
| `prior_period` | string | The month every `mom_*` metric compares against. Always `period` minus one calendar month, whether or not the source contains it. |
| `currency` / `currency_symbol` | string | From config. |
| `units_scale` | number | `1` means the figures are plain currency units. Any other value means the workbook is scaled, and validation warns if the workbook's own headings disagree. |
| `source` | object | Provenance — see below. |
| `ttm` | object | `{months, window, complete}`. |
| `methods` | object | `method_id` → one-line description of the calculation. |
| `metrics` | array | Every metric. See below. |
| `movements` | object | Ranked month-over-month changes and possible one-offs. |
| `seasonality` | object | Whether seasonality was assessed, and if not, why not. |
| `reconciliation` | object | Our totals against the workbook's own totals. |
| `data_quality_issues` | array | Errors and warnings. |
| `classification_notes` | array | Deliberate presentation facts about this client's statements. |
| `limitations` | array | What this document cannot tell you. |

### `source`

```json
{
  "config_path": "config/clients/abc_inc.yaml",
  "type": "excel",
  "path": "data/clients/abc_inc/source/ABC Inc - Financial Statements.xlsx",
  "sha256": "75da8d679a89551f...",
  "sheets": ["Income Statement", "Balance Sheet"],
  "sheet_headings": {"Income Statement": ["Income Statement (Profit and Loss)", "ABC Inc", "..."]},
  "periods_available": ["2025-01", "...", "2026-06"],
  "period_count": 18
}
```

The `sha256` is what makes a report auditable: it identifies exactly which file produced
these numbers. `periods_available` is always oldest-first, whatever order the workbook
stored its columns in.

**`source.selection`** appears only when the run chose between several workbooks in the
client's own folder (`--file NAME`, or a config without `source.filename` whose folder
holds several workbooks). A run that never asked about files has no such key, so its
document is unchanged.

```json
"selection": {
  "mode": "explicit",                       // explicit (--file) | auto (chosen by period)
  "chosen_by": "--file",
  "output_key": "test_multi_second_half",   // the file stem in the output path
  "other_workbooks": [{"filename": "test_multi_first_half.xlsx", "first_period": "2025-01",
                       "last_period": "2025-06", "period_count": 6, "periods": ["..."]}],
  "rule": "Only the chosen workbook is read. ..."
}
```

Only the chosen workbook is read. A comparison month (prior month, year-ago month, a TTM
month, a prior-year month) that exists only in another workbook is n/a with
`unavailable_reason: "in_other_file"` and a note naming the file. Files are never merged.

### `ttm`

```json
{"months": 12, "window": ["2025-07", "...", "2026-06"], "complete": true}
```

`window` is the **calendar** window required, not the months that happen to exist. When
`complete` is `false`, every TTM metric is `null` with `unavailable_reason:
"incomplete_window"`. A shorter window is never summed and presented as a TTM.

---

## A metric

```json
{
  "name": "revenue.total",
  "client_id": "abc_inc",
  "period": "2026-06",
  "value": 195898.16,
  "unit": "currency",
  "method_id": "sum_section_items_v1",
  "validation_status": "ok",
  "source_rows": [
    {"sheet": "Income Statement", "row": 8, "column": "C",
     "label": "Platform Revenue", "period": "2026-06", "original_value": 26666.66}
  ],
  "warnings": [],
  "label": "Total revenue",
  "reported_value": 195898.16,
  "notes": ["6 revenue line(s) summed."]
}
```

| Field | Notes |
|---|---|
| `name` | Stable identifier. Families are described below. |
| `value` | `null` when unavailable. **Never** `0`, `Infinity` or `NaN` as a stand-in. Full float precision — rounding happens only at display time. |
| `unit` | `currency`, `percent`, `percentage_points`, or `count`. The guard uses this to decide what a written figure may match. |
| `method_id` | Key into the top-level `methods` object. Every metric's method is registered; an unknown id is a bug. |
| `validation_status` | `ok`, `warning`, or `blocked`. `blocked` always comes with `value: null`. |
| `source_rows` | Traceability: sheet, row, column, label, period and the original cell value. A derived metric may have an empty list, with its inputs named in `notes`. |
| `warnings` | Messages from validation warnings that apply to this metric, carried through to the report. |
| `unavailable_reason` | Present only when `value` is `null`. See below. |
| `reported_value` | The workbook's own figure for the same concept, where one exists. Recorded for transparency and **never used as a calculation input** — reconciliation compares the two. |
| `label` | Human-readable name for display. |
| `notes` | Explanation of the basis, including classification notes where relevant. |

### `unavailable_reason`

| Value | Meaning |
|---|---|
| `zero_base` | A percentage change or margin whose denominator was zero. Undefined, so withheld. Displays as `n/a (prior period was zero)`. |
| `negative_base` | The denominator was negative. The arithmetic would succeed but read backwards — a worsening would look like an improvement — so it is withheld and the absolute change is the meaningful comparison. |
| `sign_change` | The line crossed zero. Bad Debts moving from +12,935 to -1,806 is a swing from a charge to a credit; reporting that as "-113.96%" invites a reader to treat a sign change as a 114% cost saving. The absolute change and the stated direction carry the information instead. A fall to *exactly* zero stays -100%, which is well defined. |
| `missing_input` | An input was blank or absent. Blank is never treated as zero. |
| `incomplete_window` | A TTM window the source does not fully cover. |
| `blocked_by_validation` | A validation **error** applies to this metric. |
| `not_available` | The concept does not exist in this source, e.g. a balance-sheet line in a workbook with no balance sheet. |
| `in_other_file` | The comparison month exists, but only in another workbook of the same client. Displays as `n/a (prior period is in a different file)`. Never read across files. |
| `missing_month_in_span` | Year and whole-file scopes: a month is missing *inside* the year's span, so its totals are blocked (error `year_months_missing`). |
| `incomplete_quarter` | A quarter without all three of its months. |
| `not_comparable` | Year on year without the same months in both years. |

### `validation_status`

- **`ok`** — no issue applies.
- **`warning`** — computed, but one or more warnings apply and travel with it.
- **`blocked`** — a validation error applies. `value` is `null`. Unrelated metrics still
  compute: an incomplete TTM window blocks only `*.ttm`, not the monthly figures.

---

## Metric names

Dot-separated families. `<slug>` is a label lowercased with non-alphanumerics collapsed to
underscores (`Marketing/Advertising: Ad Spend` → `marketing_advertising_ad_spend`).

| Pattern | Unit | Notes |
|---|---|---|
| `revenue.total` | currency | Summed from line items, not read from the workbook's total. |
| `revenue.stream.<slug>` | currency | One revenue line. |
| `revenue.stream.<slug>.share_of_revenue_pct` | percent | Share of the month's revenue. |
| `cogs.total`, `cogs.line.<slug>` | currency | |
| `gross_profit` | currency | `revenue.total − cogs.total`. |
| `gross_margin_pct` | percent | |
| `gross_margin_pct.mom_pts` | percentage_points | Margins are compared in **points**, never as a percentage of a percentage. |
| `opex.total` | currency | Sum of every leaf line, each counted once. |
| `opex.group.<key>` | currency | A subtotal group as the workbook presents it. |
| `opex.group.ungrouped` | currency | Lines that sit directly in the section. |
| `opex.category.<slug>` | currency | Config-assigned presentation category. Categories partition every opex line exactly once, so they sum to `opex.total`. |
| `opex.category.<slug>.share_of_opex_pct` | percent | |
| `opex.line.<slug>` | currency | One operating expense line. |
| `operating_income` | currency | `gross_profit − opex.total`. |
| `operating_margin_pct` | percent | |
| `net_income.reported` | currency | Read from the workbook's own Net Income row. |
| `balance_sheet.<key>` | currency | `cash`, `accounts_receivable`, `deferred_revenue`, `assets`, `liabilities`, `equity`, `liabilities_and_equity`. |
| `<base>.prior` | same as base | The prior-month value the comparison used. Emitted only for the five headline measures (`revenue.total`, `cogs.total`, `gross_profit`, `opex.total`, `operating_income`) so a report can show it without computing `current − change`. |
| `<base>.mom_abs` | same as base | Current minus prior month. |
| `<base>.mom_pct` | percent | Requires a strictly positive prior month. |
| `<base>.yoy_abs` / `.yoy_pct` | currency / percent | Against the same month a year earlier. |
| `<base>.ttm` | currency | Requires a complete window. |

### Why `revenue.total` is summed rather than read

The workbook has its own `Total Income` row. The engine does not use it as the metric
value; it sums the line items itself and records the workbook's figure in
`reported_value`. Reconciliation then compares them. If the config pointed at the wrong
rows, the two would disagree and the run would fail loudly rather than produce a
plausible-looking report.

---

## `movements`

```json
{
  "thresholds": {"significant_mom_pct": 25.0, "significant_mom_abs": 10000.0, "rule": "..."},
  "significant": [ ... ],
  "top_increases": [ ... ],
  "top_decreases": [ ... ],
  "possible_one_offs": [ ... ]
}
```

A movement entry carries `scope`, `label`, `category`, `group`, `sheet`, `row`,
`previous_period`, `previous`, `current`, `change_abs`, `change_pct`,
`change_pct_unavailable_reason` and `metric_name`. Every number in a movement is also a
metric, so nothing here is calculated that is not already verified.

A movement entry also carries `sign_change` (bool) and `direction` (`increase`,
`decrease`, `unchanged`, or `sign change: charge to credit` / `sign change: credit to
charge`), plus `candidates_considered` at the parent level so a reader can see how much
was filtered out.

A line is **significant** if:

* its absolute change clears `significant_mom_abs` (large on its own), **or**
* its percentage change clears `significant_mom_pct` **and** its absolute change clears
  `significant_mom_min_abs`, **or**
* it crossed zero and its absolute change clears `significant_mom_min_abs`.

The absolute floor on the percentage route is load-bearing. Without it a line moving from
400 to 600 is "+50%" and crowds out the movements that actually moved the month: on ABC's
June 2026 data the unfiltered rule returned 21 lines, 14 of which changed by under 2,000.
With the floor at 1,000 it returns 11 from 46 candidates.

### One-off entries

`possible_one_offs` and `ttm_window_one_offs` are produced by **one implementation**
(`MetricsBuilder._scan_one_offs`) and share one schema. They differ only in which months
they test and what each month is compared against:

| | Months tested | Compared against |
|---|---|---|
| `possible_one_offs` | the reporting month | the trailing `oneoff_trailing_months` before it |
| `ttm_window_one_offs` | every month in the TTM window | the average of the *other* months in that window |

Both entries carry: `label`, `section`, `category`, `group`, `group_key`, `sheet`, `row`,
`period`, `current`, `comparison_average`, `comparison_basis`, `comparison_months`,
`excess_over_average`, `multiple_of_average`, `multiple_unavailable_reason`,
`metric_name`, `assessment`.

`comparison_basis` is free text naming exactly what the average was taken over, so a
reader never has to guess which months a multiple is relative to.

Both are **magnitude-based**: `abs()` governs the floor and the multiple, because a large
credit distorts a total exactly as much as a large charge. An earlier version tested
`current >= min_abs` in the monthly scan, which meant a 1.2M credit could never qualify
however large it was.

Two further things to note:

- `multiple_of_average` is `null` when the trailing average was zero or negative, with
  `multiple_unavailable_reason` explaining which. A line that is normally zero and
  suddenly carries a large charge is the clearest one-off there is, and it has no
  meaningful multiple — so its size is reported and no ratio is invented.
- `assessment` always says "possible". The workbook records amounts, not reasons. A spike
  is a pattern in the data, never an explanation of it.

### `ttm_window_one_offs`

`possible_one_offs` looks only at the reporting month. A trailing-twelve-month total,
though, silently carries whatever happened eleven months ago — so this is a separate scan
across the **whole TTM window**.

It matters on the real data. ABC's December 2025 holds a **1,244,430 income tax benefit**
and **334,661 of stock-based compensation**, each zero in every other month, plus a
**80,343 bonus** against a window average of about 7,150. None of it appears anywhere in
the June figures, yet all of it is inside `opex.total.ttm` and `operating_income.ttm`.

Detection is **leave-one-out**: each month is compared against the average of the *other*
months in the window, so a single large month cannot hide inside its own baseline.
Magnitudes are used throughout, because a large credit distorts an annual total exactly as
much as a large charge. The floor is `oneoff_window_min_abs` (default 25,000), separate
from the monthly `oneoff_min_abs`.

Each entry carries `label`, `section`, `period`, `value`,
`window_average_excluding_period`, `excess_over_average`, `months_compared`, `row` and
`metric_name`.

When anything is found, a validation **warning** (`ttm_includes_possible_one_offs`) is
raised against **the named TTM metrics whose totals actually include those lines** — not
a blanket `*.ttm`. All four of ABC's items are operating expenses, so the warning lands
on `opex.total.ttm`, `operating_income.ttm`, the three affected `opex.category.*.ttm` and
`opex.group.marketing.ttm`: six metrics. `revenue.total.ttm`, `cogs.total.ttm` and
`gross_profit.ttm` stay clean, because no operating-expense item can move them. The
message names the section involved rather than claiming "every trailing-twelve-month
figure".

It is a warning rather than an error: the TTM total is arithmetically correct, it just
must not be read as a monthly rate.

Note that `net_income` has no TTM metric to tag — the engine emits only
`net_income.reported`, read from the workbook's own monthly row.

## `seasonality`

```json
{"assessed": false, "months_available": 18, "months_required": 24, "reason": "..."}
```

Seasonality is only asserted with at least 24 months, so each calendar month appears
twice. With 18 months it is explicitly **not** assessed, and the reason travels into the
report. This holds even for data that is seasonal by construction — see
`tests/test_synthetic.py::test_seasonality_is_still_not_asserted_without_two_full_years`.

## `reconciliation`

```json
{
  "tolerance_abs": 0.01,
  "all_passed": true,
  "checks_run": 144,
  "checks_failed": 0,
  "by_check": [{"key": "...", "description": "...", "periods_checked": 18,
                "periods_failed": [], "max_abs_difference": 1e-10, "passed": true}],
  "failures": []
}
```

Checks run for **every period in the source**, not just the reporting month, so a layout
problem affecting only older columns is still caught. For ABC that is 144 checks across
18 months.

The tolerance is absolute currency and exists for one reason: the workbook's own cached
`SUM()` results carry IEEE-754 error. ABC's `Total Income` for Apr 2026 caches as
`204512.41999999998` against an exact item sum of `204512.42`. The largest disagreement
observed across all 144 ABC checks is under `1e-6`. Anything beyond a cent is a real
discrepancy, and `tests/test_mutation.py` confirms a deliberate two-cent error is caught.

## `data_quality_issues`

```json
{"severity": "warning", "code": "negative_expense_line", "message": "...",
 "affects": ["opex.", "cogs."], "details": {"rows": [...]}}
```

`affects` patterns: an exact metric name, a `prefix.`, a `*.suffix`, or `*` for a fault
that invalidates the whole run.

**Prefer exact names.** A prefix is convenient and usually too broad: `negative_opex_line`
once used `"opex."` and so attached a note about Bad Debts to all ~270 `opex.*` metrics,
including `opex.line.travel`. It now names the metrics that genuinely include the
offending lines — the lines themselves, their category, their subtotal group, and the
section total — which is 15 metrics for ABC's June 2026 (Bad Debts only; see below). `finance_engine/naming.py` builds
these lists, and is imported by both `validation` and `metrics` so the naming convention
has exactly one definition.

A warning on a figure it cannot affect is worse than no warning: it teaches the reader to
skip warnings.

**Expected negative lines.** Some lines are negative by nature: Interest Income or an
Income Tax Benefit presented inside operating expenses. A client config can list them:

```yaml
expected_negative_lines:
  - Interest Income
  - Income Tax Benefit
```

The three negative-line checks (`negative_revenue_line`, `negative_opex_line`,
`negative_cogs_line`) skip listed lines, matched on the label, case-insensitive. Every
other negative line still warns. A listed line keeps its sign and its value; only the
warning is suppressed. For ABC's June 2026 the warning therefore names Bad Debts alone,
where it used to name Interest Income too.

The messages never say what a negative line *is*. They used to say "typically a recovery
or reversal", which was wrong for Interest Income and a guess for everything else. They
now say it "may be a recovery, a reversal or a misposting; that needs confirmation".

Codes `negative_opex_line` and `negative_cogs_line` are raised separately, each scoped to
its own section.

**Cell-level checks.** Three warnings name the exact cell instead of leaving the problem to
surface as a reconciliation failure on a total:

| Code | Raised for |
|---|---|
| `non_numeric_cell` | A line-item cell holding text the reader cannot parse as a number (`"n/a"`, `"TBC"`, a typed `"-"`). Text such as `"1,234"`, `"(500)"` or `"$75"` is a number and is not flagged. There is no accounting-zero rule: a typed `"-"` is read as missing, so it is flagged. A numeric 0 merely *formatted* to show "-" is stored as 0 and is not text. |
| `blank_cell` | A line-item cell that is empty while the months either side of it have values. Edge months are not "internal". Text and unreadable formulas are not double-reported here. |
| `negative_line_item` | A negative revenue, COGS or operating-expense cell. Lines in `expected_negative_lines` are skipped. |

Scope: leaf line items in the revenue, COGS and opex sections, in the months the report
reads (the trailing window ending at the reporting month, plus the year-ago month).
`affects` is period-aware: a cell in the reporting month names the line, its section
total, category, group and the profit lines; a prior-month cell names their `.prior` and
month-over-month metrics; any cell in the window names the `.ttm` metrics; the year-ago
cell names `.yoy_*`. These are warnings only. No value changes and nothing is blocked
(`tests/test_cell_checks.py` asserts both).

Each such issue carries an additive `cells` list:

```json
{"severity": "warning", "code": "non_numeric_cell", "message": "...",
 "affects": ["opex.total", "..."],
 "cells": [{"sheet": "Income Statement", "cell": "F20", "label": "Marketing",
            "period": "2025-10", "raw_value": "n/a"}]}
```

Switch per client in the YAML, one switch for all three or one per check:

```yaml
data_quality:
  cell_checks: false         # all three off; or a mapping, e.g. {negative_line_item: true}
```

Defaults (`ClientConfig.CELL_CHECKS_DEFAULT`): `non_numeric_cell` and `blank_cell` **on**,
`negative_line_item` **off**. In a mapping, a check left out keeps its default.
`test_errors` turns `negative_line_item` on.

Golden case 06 expects its one internal blank (`Income Statement!J8`, Services Revenue,
2025-10). `evals/tools/build_expected.py` derives it from the generator's inputs, without
importing the engine, into `expected.json` → `cell_warnings`, and the Level 1 runner checks
that each enabled code names exactly those cells and each disabled code is absent.

Codes: `period_not_in_source`, `missing_months`, `ttm_window_incomplete`,
`year_months_missing` (year and whole-file scopes),
`ttm_includes_possible_one_offs`, `formula_value_unreadable`, `blank_not_zero`,
`duplicate_line_labels`, `negative_revenue_line`, `negative_opex_line`,
`negative_cogs_line`, `opex_category_unmatched`,
`possible_unit_scaling`, `no_balance_sheet`, `statement_period_mismatch`,
`balance_sheet_period_missing`, `non_numeric_cell`, `blank_cell`, `negative_line_item`.

---

## Year and whole-file documents (`--scope year`, `--scope all`)

Written to `output/clients/<client>/year-YYYY/metrics.json` and `.../all/metrics.json`
(with `<file-stem>/` after the client when `--file` is used), by
`finance_engine/spans.py`. The same conventions as a month document: every figure is a
metric with `name`, `value`, `unit`, `method_id`, `validation_status`, `source_rows`,
`warnings` and `notes`; full precision; `null` with a reason, never 0. They differ from a
month document in these top-level fields:

| Field | Year | Whole file |
|---|---|---|
| `scope` | `"year"` | `"all"` |
| `period` / `period_label` | `"2025"` / `"2025 (full year, 12 of 12 months)"` | `"all"` / `"Jan 2025 to Jan 2026, 13 months"` |
| `year` | `2025` | — |
| `coverage` | see below | see below |
| `quarters`, `yoy`, `extremes` | yes | `extremes` only |
| `years`, `start_to_end`, `series` | — | yes |
| `ttm`, `prior_period`, `movements` | absent | absent |

`reconciliation` and `data_quality_issues` cover only the months in scope; the cell-level
checks run over every one of those months.

Every figure is built from **one month's figures at a time** (`finance_engine/monthly.py`),
computed the way the month report computes them, then summed. A test checks that each
month's figures equal the month document's for every month of five workbooks.

### Coverage

```json
"coverage": {"scope": "year", "year": 2026, "months_present": ["2026-01"],
             "months_present_count": 1, "months_absent": ["2026-02", "..."],
             "gaps_inside_span": [], "span": ["2026-01", "2026-01"],
             "status": "partial", "label": "year to date, 1 of 12 months",
             "totals_available": true}
```

`status` is `complete` (12 months) or `partial`. `label` is `full year, 12 of 12 months`;
`year to date, N of 12 months` (the span starts in January); `partial year, N of 12 months
(first to last)`; or `incomplete year, N of 12 months, with <months> missing inside it`.
**A partial year is never scaled, annualised or extrapolated.** A month missing *inside*
the span raises the error `year_months_missing`, which blocks every year total and the
year-on-year comparison through the normal mechanism (`validation_status: "blocked"`,
`unavailable_reason: "missing_month_in_span"`). Quarters that have all three months still
compute.

The whole-file coverage carries `first`, `last`, `months_present`, `months_present_count`,
`calendar_months_spanned`, `gaps`, `contiguous` and `label`; a gap also raises the warning
`missing_months`.

### Metric names

| Pattern | Scope | Unit | Notes |
|---|---|---|---|
| `revenue.total`, `revenue.stream.<slug>`, `cogs.total`, `gross_profit`, `opex.total`, `opex.category.<slug>`, `operating_income`, `net_income.reported` | year | currency | Year totals: the sum of the monthly values (`span_sum_v1`). |
| `gross_margin_pct`, `operating_margin_pct` | year | percent | Year total over year revenue. |
| `<measure>.q1` .. `.q4` | year | currency | The five headline measures, per calendar quarter; `incomplete_quarter` without all three months. |
| `<measure>.prior_year`, `.yoy_abs`, `.yoy_pct`; margins `.prior_year`, `.yoy_pts` | year | | Only when every month present this year is also in the file a year earlier (Jan-Jun 2026 against Jan-Jun 2025 is allowed). Otherwise `not_comparable`, or `in_other_file`. Strictly positive base for a percentage. |
| `monthly.<YYYY-MM>.<measure>` | both | | The month's figure for the five headline measures and two margins. |
| `monthly.<YYYY-MM>.<measure>.mom_abs` / `.mom_pct`; margins `.mom_pts` | both | | Against the immediately preceding calendar month in the same file. |
| `extremes.<measure>.highest` / `.lowest` | both | currency | Revenue and operating income: the month with the highest / lowest value; ties go to the earliest. The month is in `period` and in the top-level `extremes` object. |
| `year.<YYYY>.<measure>` | all | | A total per calendar year: the five headline measures, the margins and net income, with the year logic above (blocked over a gap). The `years` list gives each year's coverage and `metric_prefix`. |
| `<measure>.start_to_end_abs` / `_pct`; margins `.start_to_end_pts` | all | | The last month against the first. Strictly positive base for a percentage. |
| `coverage.months_present`, `.months_missing`, `.months_in_year` | both | count | The numbers a coverage label quotes. |

### `series` (whole file): the chart contract

A stable, simple shape for the UI. One entry per measure, one point per month in the
file, oldest first. Each point is the `monthly.<YYYY-MM>.<measure>` metric, restated.

```json
"series": {
  "periods": ["2025-01", "...", "2026-01"],
  "measures": [
    {"measure": "revenue.total", "label": "Total revenue", "unit": "currency",
     "metric_pattern": "monthly.<YYYY-MM>.revenue.total",
     "points": [{"period": "2025-01", "value": 48000.0, "validation_status": "ok"},
                {"period": "2025-02", "value": null, "validation_status": "blocked",
                 "unavailable_reason": "blocked_by_validation"}]}
  ]
}
```

`measures` is always the seven measures `revenue.total`, `cogs.total`, `gross_profit`,
`opex.total`, `operating_income` (currency) and `gross_margin_pct`,
`operating_margin_pct` (percent), in that order. `unavailable_reason` is present only on a
point whose `value` is `null`. A month missing from the file has no point; the gap is in
`coverage.gaps`.

---

## The guard

`guard.py` extracts every number from a block of text and checks it against this document.
It takes text plus a metrics document and knows nothing about who wrote the text, so
swapping the commentary provider cannot weaken it.

**Allowed values are bucketed by quantity kind** — currency, percent, structural — and a
number can only match its own kind. Without that, "churn hit 3.7%" passes by colliding
with any one of several hundred unrelated values.

A number matches if some allowed value agrees with it **at the precision it was written
to**: half of the last written digit, scaled by any magnitude suffix. So `70.2%` matches
a stored `70.1516...`, and `$196k` matches `195,898.16`, while `$195,998.16` does not
match `$195,898.16`.

### Measured limits

These are real numbers from `tests/test_guard.py::TestMeasuredLimits`, run against ABC's
June 2026 document (888 allowed values: 685 currency, 110 percent, 99 structural):

| Written as | False-pass rate |
|---|---|
| Currency to 2 decimals, random in `[0, 500k]` | **0%** (0 / 2000) |
| Percentage to 1 decimal, random in `[0, 100]` | **~15%** (≈306 / 2000) |
| Whole `$Nk`, random in `[1, 500]` | **~15%** |

So the guard is effectively airtight on precisely written currency, and weak on loosely
rounded figures — because a hundred-odd legitimate percentages densely cover 0–100. Two
further limits, both reported in every guard result rather than hidden:

- **Magnitudes are compared**, because prose writes "fell $12,452.80 (-6.0%)" where the
  metric is `-12,452.80`. The house style puts the direction in the word and leaves the
  amount unsigned. **Direction words are checked**: a word directly before a change
  figure must agree with the sign of the change it matches. "fell / decreased / narrowed
  / down / lower" need a negative change; "rose / increased / widened / up / higher" a
  positive one. "The loss narrowed" inverts, because a narrowing loss is a positive
  change in income. "fell to $X" names a level and is not checked. A parenthetical
  straight after a checked figure inherits its direction, so "fell $12,452.80 (+6.0%)"
  is rejected. A figure whose magnitude matches changes of both signs (6.3% is both
  Usage Fees' -6.3% and Software's +6.3%) gets no verdict. Any sign use outside these
  rules is still a reviewer's job.
- **The guard proves a figure is supported, not that it was used in the right sentence.**
  It cannot catch a correct number attached to the wrong concept. That is a semantic
  check, not a numeric one.

Three additions for the year and whole-file scopes. None changes a month result:

- `.yoy_pts` and `.start_to_end_abs` / `_pct` / `_pts` count as *changes* for the
  direction check, so "fell 7.8 pts" is checked against the start-to-end figure. Month
  documents never carry these names.
- `Q1`..`Q4` are quarter labels, masked like dates, not figures.
- A value exactly on a rounding half-step may be written rounded either way, and
  nothing wider passes. The float comparison `|0.8 - 0.75| <= 0.05` evaluates
  0.05000000000000004 and rejected a correctly rounded "0.8%". A figure now also passes
  if it equals the round-half-up or round-half-down of the stored value's exact binary
  value at the precision written, in exact decimal arithmetic. Those two differ only on
  an exact half-step; 0.7500000001 accepts "0.8" and not "0.7". There is no general
  slack, and `tolerance_applied` is unchanged. At two or more decimals the existing
  one-cent floor (`MIN_TOLERANCE`) is the bar, as before. Tests:
  `tests/test_guard.py::TestRoundingHalfSteps`.

The honest summary: the guard makes it very hard to state a currency amount that is not
in the data, and it does not make a model's prose correct. The deterministic provider is
safe because it is structurally incapable of arithmetic; the guard is defence in depth
for the providers that come later.

---

## Adding a commentary provider

Implement `CommentaryProvider` in `finance_engine/commentary/`. The contract:

1. **Never calculate.** Every figure comes from this document.
2. **Never invent a cause.** Report that a line rose; do not say why.
3. **Separate confirmed facts from possible explanations.**
4. **Never invent a category or line name.** Use the labels in the document.
5. Pass your own output through `guard.check_commentary` before returning it.
6. Raise `ProviderUnavailable` if the backend is unreachable, so the caller can fall back
   to the deterministic provider rather than emit nothing.

Rule 1 is enforced mechanically for every provider. Rules 2–4 are structural for the
deterministic provider and must be enforced by prompt and review for a model provider.

Planned (documented, not implemented): `ollama.py` reading `OLLAMA_BASE_URL`, and
`claude_api.py` reading `ANTHROPIC_API_KEY` from the environment via a `CredentialStore`
— never from a config file or a literal.
