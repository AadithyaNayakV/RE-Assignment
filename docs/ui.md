# The local web UI

```bash
python -m finance_engine serve            # then open http://127.0.0.1:8765
```

A browser front end for the same engine. It is a convenience, not a second system:
every action calls the service layer (`finance_engine/service/`), which runs the same
code, or the same CLI command, as the terminal. Optional packages: fastapi, uvicorn and
python-multipart (`requirements-optional.txt`). Without them the engine and the CLI work
as before, and the UI tests skip.

## The rules it follows

- **The UI never calculates.** No sums, differences, percentages, rounding or comparing
  of money values in the browser or in the web layer. Every figure on screen is a string
  the engine formatted (`finance_engine/formatting.py`) or a value copied from
  `metrics.json`. Chart.js picks its own axis tick marks; those are scale marks, not
  figures. `tests/test_web_app.py::TestNoCalculationInTheUi` scans the JS for `reduce(`,
  `Math.round(`, `* 100`, `/ 100`, `toFixed(`, `parseFloat(` and arithmetic next to a
  metric field. That scan is **best effort**: it catches the usual ways arithmetic gets
  written, not every possible one.
- **Every route has a terminal command.** `finance_engine/service/parity.py` is the
  table; a test fails if the app registers a route that is not in it, or the table lists
  one the app lacks, and every command in it must parse with the real CLI parser.
- **The system picks the commentary provider** (the `config/providers.yaml` chain). There
  is no model picker. The one privacy control is **Keep data on this machine**, which is
  exactly `--provider deterministic`, ticked by default. Untick it and the page first
  says what would leave: the compact brief only (never the workbook or metrics.json), and
  the last send-back comment, if the last review action was a send-back, as reviewer notes
  for the writer. Keeping data on this machine sends neither. After the run the badges
  show the provider, its attempt and "data left this machine", read from the report's own
  footer.
- **commentary_log.json is shown only when report.md carries the cloud footer**, the same
  rule as `show-report`: a later local run leaves an older cloud log beside a new report,
  and that log no longer describes it.
- **Local only.** The server binds to 127.0.0.1 and refuses any other host; requests must
  come from a loopback address with a 127.0.0.1 or localhost Host header. There is **no
  sign-in**. Reviewer names are typed in and **self-declared**.
- **No cross-origin changes.** Every POST must carry the header `X-Finance-UI: 1` (a
  cross-site form cannot send it), and a browser Origin, when sent, must be this server.
- **Only `ui/` is served.** `.env`, `.secrets/`, `config/`, `data/` and `output/` are never
  served as files; reports and metrics come back only through the routes below, for a
  validated client id. Responses carry a strict Content-Security-Policy (`'self'` only).
- **No secret in any response.** Status routes return "configured yes/no" and the names
  of missing variables. Every JSON response passes through a redactor that knows the
  values of the key and token variables; tests seed fake values and scan every response.
- **Text is never parsed as HTML.** The page builds DOM nodes with `textContent` (there is
  no `innerHTML` anywhere), so a workbook cell holding `<script>` is shown as text. Tested
  with a hostile workbook and with the markdown renderer run under Node.

## Limits

| Limit | Value |
|---|---|
| Upload types | `.xlsx`, `.xlsm` only, checked as real Excel zips |
| Upload size | 25 MB; at most 200 MB uncompressed, 2,000 parts, and no part expanding more than 200 to 1 (zip bomb) |
| Upload location | `data/clients/<id>/source/<sanitised name>` only; an existing different file is never overwritten |
| Client id | `^[a-z0-9_]{2,40}$` |
| Background jobs | one running job per client (and one for the evals); log capped at 400 lines; timeout 15 min (reports), 30 min (evals, month batches), 2 min (QuickBooks pull) |
| Jobs | kept in memory until the server stops |
| Reviewer name | at most 80 characters; comment at most 4,000 |

## Route to terminal command

`{client}`, `--period`, `--file` stand for the values chosen on the page. A month can be
replaced by `--scope year --year YYYY` or `--scope all`.

| Route | Terminal equivalent |
|---|---|
| `GET /api/clients` | `python -m finance_engine list-clients --json` |
| `GET /api/clients/{client}/files` | `python -m finance_engine list-files --client {client} --json` |
| `GET /api/clients/{client}/periods` | `python -m finance_engine list-periods --client {client} --file NAME --json` |
| `GET /api/report` | `python -m finance_engine show-report --client {client} --period P --file NAME --json` |
| `GET /api/brief` | `python -m finance_engine brief --client {client} --period P` |
| `GET /api/export/report.md`, `/api/export/metrics.json` | `show-report` prints both paths |
| `GET /api/charts` | `python -m finance_engine chart-data --client {client} --file NAME --json` |
| `POST /api/jobs/analyze-months` | `python -m finance_engine analyze --client {client} --period P --file NAME` per month without a month file |
| `GET /api/cloud-notice` | `python -m finance_engine provider-status` |
| `POST /api/jobs/report` | `python -m finance_engine report --client {client} --period P --provider deterministic` (or `auto` when "Keep data on this machine" is unticked) |
| `POST /api/jobs/analyze` | `python -m finance_engine analyze --client {client} --period P` |
| `GET /api/jobs`, `GET /api/jobs/{id}` | the same command, run in the foreground |
| `POST /api/onboarding/draft` | `python -m finance_engine init-client --draft --file book.xlsx --client-id ID --name "Co"` |
| `GET /api/onboarding/{client}/draft` | `python -m finance_engine validate-config --client ID --draft` |
| `POST /api/onboarding/{client}/validate` | `python -m finance_engine validate-config --client ID --draft --from edited.yaml` |
| `POST /api/onboarding/{client}/approve` | `python -m finance_engine approve-config --client ID --by "Name"` |
| `GET /api/review/status`, `/log`, `/version` | `python -m finance_engine review status` / `review log --client {client} --period P` |
| `POST /api/review/mark-reviewed`, `/send-back` | `python -m finance_engine review mark-reviewed` / `send-back --comment "..."` with `--client --period --by` |
| `GET /api/review/queue` (the list and its `count`, polled every 30 s for the top bar) | `python -m finance_engine review queue` |
| `GET /api/evals` | `python -m finance_engine list-evals` |
| `POST /api/jobs/evals` | `python evals/run_engine_evals.py --compare` then `python evals/run_skill_evals.py --provider deterministic --compare` |
| `GET /api/quickbooks/status` | `python -m finance_engine quickbooks-status` |
| `POST /api/jobs/quickbooks-pull` | `python -m finance_engine pull-quickbooks --client qb_sandbox --start YYYY-MM --end YYYY-MM` |
| `GET /api/providers` | `python -m finance_engine provider-status` |

There is **no route** for the paid Claude skill evals (`python evals/run_skill_evals.py
04`, Claude Pro usage), `scripts/provider_smoke.py` or `scripts/qb_smoke.py`. The evals
page shows the command and says to run it yourself.

## Pages

The top bar holds the five pages and the theme switch (system, light or dark, remembered
in the browser when storage is available); below 1000 px it folds into a Menu button.

- **Reports** (home): company, file ("Default file" is the configured default; files are
  never merged), scope (month, year, whole file) and period, in one row that wraps. For a
  QuickBooks company the file list is **Snapshot**: each pull labelled by the engine as
  "Pulled 14 Oct 2026, 11:15 – Jul 2026 to Sep 2026" (this machine's time; the months
  are read from the snapshot itself, so never "?"), newest first, and any snapshot that
  cannot be read in a disabled "Cannot be read" group at the bottom with a one-line
  reason. With none chosen, the newest readable snapshot is used and one line says so;
  `report` without `--file` does the same (`sources/selection.py`). Then
  the status badges, most important first, each with a word and an icon as well as
  colour, and a "?" with one sentence of help: **Checks** (reconciliation: all pass, or
  "K of M failed"), **Number check** (the guard result and X/Y traced), **Written by**
  (the commentary provider and its attempt), **Data left this machine**, **Review** (the
  review status). A quieter line below gives the scope label (for example "partial year,
  8 of 12 months") and the **File fingerprint** (the source SHA-256, short).
  With no report yet, the page says so and shows "Generate a report"; once a report
  exists, that panel folds into "Generate the report again" and its button is secondary,
  because the next step is the review. The report is rendered from `report.md`. A figure
  in the prose is a button that opens its trace **only** when
  `finance_engine/trace_links.py` (engine code; the guard is unchanged) finds exactly one
  metric holding the value the guard matched; an ambiguous figure stays plain. Side tabs:
  Numbers, Warnings (data-quality issues, cell warnings with sheet and cell, and **Failed
  checks**, the reconciliation failure table; filter by code), Charts, **Source cells**
  (the trace: sheet, cell, raw value, method), and Review. The Review tab shows the status
  on the tab itself ("Review – Ready for review") and starts with a one-line **Next
  step**. See "Review flow" below.
- **Charts** follow the report on screen. A **month** report shows two bar charts from
  that month's own `metrics.json`: the month against the prior month (revenue, cost of
  goods sold, gross profit, operating expenses, operating income; the prior figures are
  the engine's `<measure>.prior` metrics) and operating expenses by category, with each
  category's share as the engine formatted it. A **year** report shows monthly lines from
  that year's own `metrics.json` (`monthly.<YYYY-MM>.*`); if only the whole-file report
  exists, its months for that year are used. The **whole file** shows the lines below.
  Terminal: `chart-data --client ID --period YYYY-MM`, `--scope year --year YYYY`, or no
  scope for the whole file.
- **Line charts**: revenue, gross profit and operating income; net income (as reported);
  the two margins. The first and last come from the whole-file `metrics.json` (`series`)
  or the year's own `metrics.json`.
  **Net income is not in that series**, so it is read from each month's own
  `metrics.json` when one exists for the same source file (same SHA-256); a month without
  one is a gap labelled "not analysed", and the "Analyse those months" button runs
  `analyze` only for months that have no month file (an existing one is never rewritten,
  so no review decision is voided). A month missing from the source is a gap, never
  zero; a month that failed reconciliation gets a cross marker and is listed as "checks
  failed"; a partial year is labelled and never annualised. Every chart has "View as
  table". Series differ by colour, dash and marker shape.
- **Add company**: upload, review and edit the drafted YAML (errors link to their line),
  Validate, Approve config (name required, then a confirmation), then generate with the
  cloud notice and "Keep data on this machine". It ends on the Review tab.
- **Review queue**: every report Ready for review, across companies. The top bar shows
  the count next to the page name and in the page title, refreshed every 30 seconds
  without a reload.
- **Evals**: the 19 golden cases ("Test cases") with their newest saved baseline result
  (Level 2 "Template report" is the deterministic control), and "Run free evals" with a
  live log. No button for paid evals.
- **Sources and settings**: QuickBooks status (sandbox badge, names of missing
  variables), "Pull from QuickBooks" with a date range, a confirm tick and a confirmation
  dialog (read-only, the same code as `pull-quickbooks`; the new snapshot then appears in
  the file list), and the provider chain read-only ("Who writes the commentary").
- **Export**: download report.md and metrics.json; "Print or save as PDF" uses a print
  stylesheet that keeps the report only, black on white.

### Labels and engine terms

The pages use plain words; the engine, the CLI and these docs keep the engine terms.

| On the page | Engine term |
|---|---|
| Checks, Failed checks | reconciliation, reconciliation failures |
| Number check | guard (`guard.py`) |
| Written by, Writer | commentary provider |
| Source cells | trace (`source_rows`) |
| File fingerprint | source SHA-256 |
| Company | client (`client_id`) |
| Template report | deterministic provider / control |
| Ready for review, Changes requested, Reviewed | `ready_for_review`, `changes_requested`, `reviewed` |
| Mark as reviewed | `review mark-reviewed` (`mark_reviewed` in the log) |
| Snapshot (QuickBooks) | a saved `ProfitAndLoss` JSON in `data/clients/<id>/source/` |
| 12-month total incomplete | TTM incomplete |

### Look

`ui/static/styles.css` keeps one font stack (plus monospace for cell references, file
names, hashes and commands), four text sizes (14, 16, 20, 24 px), two weights (400, 600),
no capitals by CSS, one accent colour and three status colours, all as variables for
light and dark, and spacing on a 4 / 8 / 12 / 16 / 24 / 32 px scale.
`tests/test_ui_style.py` enforces these, including WCAG contrast (4.5:1 text, 3:1
control borders and icons) in both themes.

## Review flow

Three statuses: **Ready for review**, **Changes requested**, **Reviewed**.

1. **Generating a report submits it.** `report` (and so the Generate button) appends a
   `submit` entry by `system`, marked automatic, with the commentary provider as "written
   by". The report is Ready for review at once and appears in the queue. There is no
   Submit button.
2. **The reviewer** types a name and either **Mark as reviewed** or **Send back** (a
   comment is required). Both ask for a confirmation first. The word "approve" is used
   only for a new company's config.
3. **After Send back** the status is Changes requested. **Regenerate with this comment**
   runs `report` again: a cloud writer receives the comment as reviewer notes (the page
   first shows what would leave this machine); the deterministic template cannot follow
   comments, and the page says so. The regenerated report is Ready for review again.
4. **The same-person rule** (`config/review.yaml`, `require_different_reviewer`) is
   **off** in this repo: the person who submitted a version may also mark it as reviewed.
   Set it to `true` to turn it on. It then applies only when a person submitted or edited
   the current version: a hand-edited report.md is submitted from the terminal
   (`review submit --by NAME`), and that person cannot then mark it as reviewed. A version
   `report` submitted can be marked by anyone.

A report.md or metrics.json changed by hand after the last review step is **not in
review** until someone submits that version from the terminal or regenerates it. A report
made before automatic submission (no review log) is also not in review until it is
regenerated, so old outputs do not flood the queue. Logs written before this flow say
`approve`; they are read as "Marked as reviewed".

| Status | Engine state | Terminal |
|---|---|---|
| Ready for review | `ready_for_review` | `report ...` (automatic), or `review submit --by NAME` for an edited version |
| Changes requested | `changes_requested` | `review send-back --by NAME --comment "..."` |
| Reviewed | `reviewed` | `review mark-reviewed --by NAME` |
| Not in review | `not_in_review` | edited by hand, or made before automatic submission |

## Review records

Review logs (`output/.../review/review_log.json`, with copies of each submitted report in
`review/versions/`) and config approvals (`data/clients/<id>/config_approval.json`) are
files on **this machine only**, in git-ignored folders. They are append-only and
hash-chained, but nothing stops someone with file access from deleting them. Because
names are self-declared, the same-person rule is a nudge against reviewing your own
work, **not a security control**.

## [PLANNED]

- Real sign-in (SSO) and per-user roles, for hosting. Today there is none.
- Durable, shared storage for review logs and approvals (a database or a write-once
  store) for hosting. Today they are local files.
- Persisting background jobs across a server restart.
- Reviewer notes for the Claude Code skill path (today they reach the cloud providers
  through `--provider auto` only).
- An automated browser test suite (Playwright). Not installed; the browser click-through
  for this build was run by hand with headless Edge (see the final report).
