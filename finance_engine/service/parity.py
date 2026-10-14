"""Every web route and the terminal command that does the same thing.

Rule: everything the UI does must also work from the terminal. Tests read this table:
every route the web app registers must appear here, and every `cli` entry must parse
with the real CLI parser (or name a script that exists). `{placeholders}` are filled with
sample values by the test.

`kind` is "action" for a route that does something, "read" for one that shows files the
engine already wrote, and "support" for UI plumbing (polling a job) whose terminal
equivalent is simply running the command in the foreground: `cli` then names that
command family.
"""

from __future__ import annotations

FE = "python -m finance_engine"
SCOPE = "--period {period}"            # or --scope year --year YYYY, or --scope all
FILE = "--file {file}"

ROUTES: list[dict] = [
    # ---- clients, files, periods
    {"method": "GET", "path": "/api/clients", "kind": "read",
     "cli": f"{FE} list-clients --json"},
    {"method": "GET", "path": "/api/clients/{client}/files", "kind": "read",
     "cli": f"{FE} list-files --client {{client}} --json"},
    {"method": "GET", "path": "/api/clients/{client}/periods", "kind": "read",
     "cli": f"{FE} list-periods --client {{client}} {FILE} --json"},
    # ---- one report
    {"method": "GET", "path": "/api/report", "kind": "read",
     "cli": f"{FE} show-report --client {{client}} {SCOPE} {FILE} --json"},
    {"method": "GET", "path": "/api/brief", "kind": "read",
     "cli": f"{FE} brief --client {{client}} {SCOPE} {FILE}"},
    {"method": "GET", "path": "/api/export/report.md", "kind": "read",
     "cli": f"{FE} show-report --client {{client}} {SCOPE} {FILE}"},
    {"method": "GET", "path": "/api/export/metrics.json", "kind": "read",
     "cli": f"{FE} show-report --client {{client}} {SCOPE} {FILE}"},
    {"method": "GET", "path": "/api/charts", "kind": "read",
     "cli": f"{FE} chart-data --client {{client}} {FILE} --json"},
    {"method": "POST", "path": "/api/jobs/analyze-months", "kind": "action",
     "cli": f"{FE} analyze --client {{client}} --period {{period}} {FILE}"},
    {"method": "GET", "path": "/api/cloud-notice", "kind": "read",
     "cli": f"{FE} provider-status"},
    {"method": "POST", "path": "/api/jobs/report", "kind": "action",
     "cli": f"{FE} report --client {{client}} {SCOPE} {FILE} --provider deterministic"},
    {"method": "POST", "path": "/api/jobs/analyze", "kind": "action",
     "cli": f"{FE} analyze --client {{client}} {SCOPE} {FILE}"},
    # Polling a background job: in the terminal the same command runs in the
    # foreground and prints its own progress, so its equivalent is the command itself.
    {"method": "GET", "path": "/api/jobs/{job_id}", "kind": "support",
     "cli": f"{FE} report --client {{client}} {SCOPE} --provider deterministic"},
    {"method": "GET", "path": "/api/jobs", "kind": "support",
     "cli": f"{FE} report --client {{client}} {SCOPE} --provider deterministic"},
    # ---- add a company
    {"method": "POST", "path": "/api/onboarding/draft", "kind": "action",
     "cli": f"{FE} init-client --draft --file {{workbook}} --client-id {{client}} "
            f"--name {{name}}"},
    {"method": "GET", "path": "/api/onboarding/{client}/draft", "kind": "read",
     "cli": f"{FE} validate-config --client {{client}} --draft"},
    {"method": "POST", "path": "/api/onboarding/{client}/validate", "kind": "action",
     "cli": f"{FE} validate-config --client {{client}} --draft --from {{yaml_file}}"},
    {"method": "POST", "path": "/api/onboarding/{client}/approve", "kind": "action",
     "cli": f"{FE} approve-config --client {{client}} --by {{name}}"},
    # ---- review
    {"method": "GET", "path": "/api/review/status", "kind": "read",
     "cli": f"{FE} review status --client {{client}} {SCOPE}"},
    {"method": "GET", "path": "/api/review/log", "kind": "read",
     "cli": f"{FE} review log --client {{client}} {SCOPE}"},
    {"method": "POST", "path": "/api/review/submit", "kind": "action",
     "cli": f"{FE} review submit --client {{client}} {SCOPE} --by {{name}}"},
    {"method": "POST", "path": "/api/review/approve", "kind": "action",
     "cli": f"{FE} review approve --client {{client}} {SCOPE} --by {{name}}"},
    {"method": "POST", "path": "/api/review/send-back", "kind": "action",
     "cli": f"{FE} review send-back --client {{client}} {SCOPE} --by {{name}} "
            f"--comment {{comment}}"},
    {"method": "GET", "path": "/api/review/version", "kind": "read",
     "cli": f"{FE} review log --client {{client}} {SCOPE}"},
    {"method": "GET", "path": "/api/review/queue", "kind": "read",
     "cli": f"{FE} review queue"},
    # ---- evals (free only: there is no route for the paid skill evals)
    {"method": "GET", "path": "/api/evals", "kind": "read", "cli": f"{FE} list-evals"},
    {"method": "POST", "path": "/api/jobs/evals", "kind": "action",
     "cli": "python evals/run_engine_evals.py --compare && "
            "python evals/run_skill_evals.py --provider deterministic --compare"},
    # ---- sources and settings
    {"method": "GET", "path": "/api/quickbooks/status", "kind": "read",
     "cli": f"{FE} quickbooks-status"},
    {"method": "POST", "path": "/api/jobs/quickbooks-pull", "kind": "action",
     "cli": f"{FE} pull-quickbooks --client {{client}} --start {{start}} --end {{end}}"},
    {"method": "GET", "path": "/api/providers", "kind": "read",
     "cli": f"{FE} provider-status"},
]

#: Sample values for the placeholders, for the parity test.
SAMPLES = {"client": "test_clean", "period": "2026-01", "file": "test_clean.xlsx",
           "workbook": "book.xlsx", "name": "A_Reviewer", "comment": "recheck",
           "yaml_file": "edited.yaml", "start": "2025-10", "end": "2026-09"}

#: Never routes: these spend money or Pro usage, so they are terminal-only by design.
TERMINAL_ONLY = ["python evals/run_skill_evals.py",
                 "python scripts/provider_smoke.py", "python scripts/qb_smoke.py"]


def route_for(method: str, path: str) -> dict | None:
    return next((r for r in ROUTES if r["method"] == method and r["path"] == path), None)
