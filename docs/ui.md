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
| `POST /api/review/submit`, `/approve`, `/send-back` | `python -m finance_engine review submit` / `approve` / `send-back --comment "..."` with `--client --period --by` |
| `GET /api/review/queue` | `python -m finance_engine review queue` |
| `GET /api/evals` | `python -m finance_engine list-evals` |
| `POST /api/jobs/evals` | `python evals/run_engine_evals.py --compare` then `python evals/run_skill_evals.py --provider deterministic --compare` |
| `GET /api/quickbooks/status` | `python -m finance_engine quickbooks-status` |
| `POST /api/jobs/quickbooks-pull` | `python -m finance_engine pull-quickbooks --client qb_sandbox --start YYYY-MM --end YYYY-MM` |
| `GET /api/providers` | `python -m finance_engine provider-status` |

There is **no route** for the paid Claude skill evals (`python evals/run_skill_evals.py
04`, Claude Pro usage), `scripts/provider_smoke.py` or `scripts/qb_smoke.py`. The evals
page shows the command and says to run it yourself.

## Pages

- **Reports** (home): client, file ("all files" means the configured default; files are
  never merged), scope (month, year, whole file) and period. Badges, each with text and an
  icon as well as colour: reconciliation (all pass, or "K of M failed"), number check
  (X/Y traced), guard result, provider and attempt, data left this machine, scope label
  (for example "partial year, 8 of 12 months"), source SHA-256 (short), review status.
  The report is rendered from `report.md`. A figure in the prose is a button that opens
  its trace **only** when `finance_engine/trace_links.py` (engine code; the guard is
  unchanged) finds exactly one metric holding the value the guard matched; an ambiguous
  figure stays plain. Side tabs: Numbers, Warnings (cell-level warnings with sheet and
  cell, the reconciliation failure table, filter by code, each with a trace), Charts,
  Trace (sheet, cell, raw value, method), Review.
- **Charts**: revenue, gross profit and operating income; net income (as reported); the
  two margins. The first and last come from the whole-file `metrics.json` (`series`).
  **Net income is not in that series**, so it is read from each month's own
  `metrics.json` when one exists for the same source file (same SHA-256); a month without
  one is a gap labelled "not analysed", and the "Analyse those months" button runs
  `analyze` only for months that have no month file (an existing one is never rewritten,
  so no review decision is voided). A month missing from the source is a gap, never
  zero; a month that failed reconciliation gets a cross marker and is listed; a partial
  year is labelled and never annualised. Every chart has "View as table". Series differ
  by colour, dash and marker shape.
- **Add company**: upload, review and edit the drafted YAML (errors link to their line),
  Validate, Approve (name required), then generate with the cloud notice and "Keep data
  on this machine". It ends on the Review tab.
- **Review queue**: everything in review, across clients.
- **Evals**: the 19 golden cases with their newest saved baseline result, and "Run free
  evals" with a live log. No button for paid evals.
- **Sources and settings**: QuickBooks status (sandbox badge, names of missing
  variables), "Pull from QuickBooks" with a date range and a confirm tick (read-only, the
  same code as `pull-quickbooks`; the new snapshot then appears in the file list), the
  provider chain read-only, and the theme (light, dark or system, remembered in the
  browser when storage is available).
- **Export**: download report.md and metrics.json; "Print / save as PDF" uses a print
  stylesheet that keeps the badges and the report only.

## Review records

Review logs (`output/.../review/review_log.json`, with copies of each submitted report in
`review/versions/`) and config approvals (`data/clients/<id>/config_approval.json`) are
files on **this machine only**, in git-ignored folders. They are append-only and
hash-chained, but nothing stops someone with file access from deleting them. By default
the approver must be a different name from the submitter (`config/review.yaml`,
`require_different_approver: true`); because names are self-declared, that is a nudge
against approving your own work, **not a security control**.

## [PLANNED]

- Real sign-in (SSO) and per-user roles, for hosting. Today there is none.
- Durable, shared storage for review logs and approvals (a database or a write-once
  store) for hosting. Today they are local files.
- Persisting background jobs across a server restart.
- Reviewer notes for the Claude Code skill path (today they reach the cloud providers
  through `--provider auto` only).
- An automated browser test suite (Playwright). Not installed; the browser click-through
  for this build was run by hand with headless Edge (see the final report).
