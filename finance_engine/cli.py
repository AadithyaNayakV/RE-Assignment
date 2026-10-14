"""Command line interface.

    python -m finance_engine analyze          --client abc_inc --period 2026-06
    python -m finance_engine report           --client abc_inc --period 2026-06
    python -m finance_engine check-commentary --client abc_inc --period 2026-06 --file x.md
    python -m finance_engine brief            --client abc_inc --period 2026-06
    python -m finance_engine list-periods     --client abc_inc
    python -m finance_engine init-client      --file PATH.xlsx --client-id ID --name "Co"
    python -m finance_engine pull-quickbooks  --client qb_sandbox --start 2025-10 --end 2026-10
    python -m finance_engine report --client qb_sandbox --period 2026-09 \
        --source quickbooks --start 2025-10 --end 2026-10      # pull, then analyse

Onboarding, review and status (each is also a web UI action; see service/parity.py):

    python -m finance_engine init-client --draft --file PATH.xlsx --client-id ID --name "Co"
    python -m finance_engine validate-config  --client ID [--draft] [--from edited.yaml]
    python -m finance_engine approve-config   --client ID --by "A. Reviewer"
    python -m finance_engine list-clients | list-files --client ID | show-report --client ID ...
    python -m finance_engine review submit|approve|send-back|status|log --client ID --period P --by NAME
    python -m finance_engine review queue
    python -m finance_engine list-evals | provider-status | quickbooks-status
    python -m finance_engine serve            # the local web app, 127.0.0.1 only

`--period latest` resolves to the newest month in the source (the top line of
list-periods) and prints `period: latest -> YYYY-MM` to stderr.

Choosing a workbook, when a client's folder holds several (see `sources/selection.py`):

    --file NAME        analyze, report, brief, list-periods (alias --workbook)
    --workbook NAME    check-commentary, whose --file is the commentary being checked

With a workbook named this way, outputs go to `output/clients/<client>/<file-stem>/...`.
Without it, everything behaves and is written exactly as before.

Scopes (analyze, report, brief, check-commentary):

    --scope month --period YYYY-MM     the default; output/clients/<client>/<period>/
    --scope year  --year YYYY          output/clients/<client>/year-YYYY/
    --scope all                        output/clients/<client>/all/

`--json` prints one machine-readable result on stdout; progress notes go to stderr. A
failure prints `{"ok": false, "error": ...}` on stdout as well as the message on stderr.

Exit codes are meaningful, so the CLI can gate a pipeline:

    0  success
    1  a usage, config or layout problem — nothing was written
    2  completed, but reconciliation or validation failed; output marks the failure
    3  the guard rejected the commentary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from finance_engine.brief import build_brief
from finance_engine.commentary.base import ProviderUnavailable, get_provider
from finance_engine.commentary.providers import ORCHESTRATED
from finance_engine.config import ClientConfig, ConfigError, Settings
from finance_engine.formatting import check_count, not_provided_note
from finance_engine.guard import NumberGuard
from finance_engine.metrics import build_metrics
from finance_engine.model import LayoutError
from finance_engine.report import build_report
from finance_engine.sources.excel import ExcelSource
from finance_engine.sources.selection import (SourceChoice, SourceSelectionError, Want,
                                              choose, hint_for_missing_period,
                                              is_own_folder, list_workbooks,
                                              source_folder, workbook_inventory)
from finance_engine.spans import SpanError, build_all_metrics, build_year_metrics
from finance_engine.init_client import InitError
from finance_engine.sources.quickbooks_api import QuickBooksError

EXIT_OK, EXIT_USAGE, EXIT_DATA, EXIT_GUARD = 0, 1, 2, 3
LATEST = "latest"


class UsageError(Exception):
    """A bad command-line value. Reported in one line, never as a traceback."""


def build_parser() -> argparse.ArgumentParser:
    """Every command and flag. The web UI's parity registry is checked against this."""
    parser = argparse.ArgumentParser(
        prog="python -m finance_engine",
        description="Deterministic monthly financial trend analysis. "
                    "Python calculates every number.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p, period_required=True, workbook_flags=("--file", "--workbook"),
                   scoped=True):
        p.add_argument("--client", required=True, help="client_id, e.g. abc_inc")
        # Required for month scope, checked after parsing so that --scope year and
        # --scope all can omit it. A month run without it fails exactly as before.
        p.add_argument("--period", required=False,
                       help="reporting month as YYYY-MM, e.g. 2026-06, or 'latest' "
                            "for the newest month in the source")
        p.set_defaults(period_required=period_required)
        if scoped:
            p.add_argument("--scope", choices=["month", "year", "all"], default="month",
                           help="month (default), a calendar year, or the whole file")
            p.add_argument("--year", type=int, default=None,
                           help="calendar year for --scope year, e.g. 2025")
        p.add_argument("--root", default=None,
                       help="project root (defaults to the working directory)")
        p.add_argument(*workbook_flags, dest="workbook", default=None, metavar="NAME",
                       help="which workbook in the client's source folder to read "
                            "(default: the config's source.filename)")
        p.add_argument("--json", action="store_true",
                       help="print one JSON result on stdout; notes go to stderr")

    p_analyze = sub.add_parser("analyze", help="write metrics.json")
    add_common(p_analyze)

    p_report = sub.add_parser("report", help="write metrics.json and report.md")
    add_common(p_report)
    p_report.add_argument("--source", choices=["file", "quickbooks"], default="file",
                          help="quickbooks: pull a new sandbox snapshot first (with "
                               "--start and --end), then analyse that snapshot")
    p_report.add_argument("--start", default=None, help="first month to pull, YYYY-MM")
    p_report.add_argument("--end", default=None, help="last month to pull, YYYY-MM")
    p_report.add_argument("--provider", default=None,
                          help="commentary provider (default: $LLM_PROVIDER or "
                               "'deterministic'). auto, gemini and claude-api use "
                               "config/providers.yaml: checked, retried, and always "
                               "falling back to the deterministic report")

    p_check = sub.add_parser("check-commentary",
                             help="check a text file's numbers against metrics.json")
    add_common(p_check, workbook_flags=("--workbook",))
    p_check.add_argument("--file", required=True, help="text or markdown file to check")

    p_brief = sub.add_parser("brief",
                             help="print the figures in an existing metrics.json, "
                                  "formatted for a commentary writer")
    add_common(p_brief)
    p_brief.add_argument("--metric", default=None,
                         help="print only metrics whose name contains this text")

    p_periods = sub.add_parser("list-periods", help="list the months in a client's source")
    add_common(p_periods, period_required=False, scoped=False)

    p_pull = sub.add_parser("pull-quickbooks",
                            help="save a read-only QuickBooks sandbox Profit and Loss "
                                 "snapshot (one column per month)")
    p_pull.add_argument("--client", required=True, help="a client with source.type "
                                                         "quickbooks, e.g. qb_sandbox")
    p_pull.add_argument("--start", required=True, help="first month, YYYY-MM")
    p_pull.add_argument("--end", required=True, help="last month, YYYY-MM")
    p_pull.add_argument("--root", default=None,
                        help="project root (defaults to the working directory)")
    p_pull.add_argument("--json", action="store_true",
                        help="print one JSON result on stdout; notes go to stderr")

    p_init = sub.add_parser("init-client",
                            help="draft config/clients/<ID>.yaml by inspecting a workbook")
    p_init.add_argument("--file", required=True, help="the workbook to inspect (.xlsx)")
    p_init.add_argument("--client-id", required=True, help="new client_id, e.g. acme_ltd")
    p_init.add_argument("--name", required=True, help='company name, e.g. "Acme Ltd"')
    p_init.add_argument("--force", action="store_true",
                        help="replace an existing config or draft for this client_id")
    p_init.add_argument("--root", default=None,
                        help="project root (defaults to the working directory)")
    p_init.add_argument("--json", action="store_true",
                        help="print one JSON result on stdout; notes go to stderr")
    p_init.add_argument("--draft", action="store_true",
                        help="write data/clients/<ID>/draft/config.yaml and "
                             "draft_summary.json for approve-config, never config/clients")

    def add_plain(p):
        p.add_argument("--root", default=None,
                       help="project root (defaults to the working directory)")
        p.add_argument("--json", action="store_true",
                       help="print one JSON result on stdout; notes go to stderr")

    add_plain(sub.add_parser("list-clients", help="list every client config and every "
                                                  "draft waiting for approval"))
    p_files = sub.add_parser("list-files", help="list a client's source files and the "
                                                "months each holds")
    p_files.add_argument("--client", required=True, help="client_id, e.g. abc_inc")
    add_plain(p_files)

    p_show = sub.add_parser("show-report", help="the status of an existing report: "
                                                "reconciliation, number check, provider, "
                                                "source fingerprint, review status")
    add_common(p_show)

    p_val = sub.add_parser("validate-config", help="check a client config with the real "
                                                   "loader, reader and reconciliation")
    p_val.add_argument("--client", required=True, help="client_id, e.g. acme_ltd")
    p_val.add_argument("--draft", action="store_true",
                       help="validate data/clients/<ID>/draft/config.yaml instead of the "
                            "live config")
    p_val.add_argument("--from", dest="from_file", default=None, metavar="FILE",
                       help="with --draft: replace the draft with this edited YAML first")
    add_plain(p_val)

    p_appr = sub.add_parser("approve-config", help="re-validate a draft config and make it "
                                                   "live, recording who approved it")
    p_appr.add_argument("--client", required=True, help="client_id, e.g. acme_ltd")
    p_appr.add_argument("--by", required=True, help='your name, e.g. "A. Reviewer" '
                                                    '(self-declared; there is no sign-in)')
    add_plain(p_appr)

    p_rev = sub.add_parser("review", help="review workflow for one report: mark-reviewed, "
                                          "send-back, status, log; queue lists everything "
                                          "Ready for review. `report` submits a new report "
                                          "automatically; submit is only for a report.md a "
                                          "person edited")
    p_rev.add_argument("action", choices=["mark-reviewed", "send-back", "status", "log",
                                          "queue", "submit"])
    p_rev.add_argument("--client", default=None, help="client_id (all actions but queue)")
    p_rev.add_argument("--period", default=None, help="reporting month, YYYY-MM")
    p_rev.add_argument("--scope", choices=["month", "year", "all"], default="month")
    p_rev.add_argument("--year", type=int, default=None)
    p_rev.add_argument("--file", dest="workbook", default=None, metavar="NAME",
                       help="the workbook the report was run with --file NAME")
    p_rev.add_argument("--by", default=None, help='your name (self-declared; no sign-in); '
                                                  'required for mark-reviewed and send-back')
    p_rev.add_argument("--comment", default=None, help="required for send-back")
    add_plain(p_rev)

    add_plain(sub.add_parser("list-evals", help="the golden cases and their newest saved "
                                                "baseline results"))
    add_plain(sub.add_parser("provider-status", help="the commentary provider chain: "
                                                     "configured yes/no and the names of "
                                                     "missing variables, never a value"))
    add_plain(sub.add_parser("quickbooks-status", help="the read-only QuickBooks sandbox "
                                                       "reader: configured yes/no and the "
                                                       "names of missing variables"))
    p_chart = sub.add_parser("chart-data", help="the monthly series the UI charts, as a "
                                                "table, read from the engine's files")
    p_chart.add_argument("--client", required=True, help="client_id, e.g. test_clean")
    p_chart.add_argument("--file", dest="workbook", default=None, metavar="NAME",
                         help="the workbook the whole-file report was run with")
    p_chart.add_argument("--year", type=int, default=None,
                         help="only this calendar year's months")
    add_plain(p_chart)
    p_serve = sub.add_parser("serve", help="start the local web app on 127.0.0.1")
    p_serve.add_argument("--host", default="127.0.0.1",
                         help="must be 127.0.0.1: the app is local only")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--root", default=None,
                         help="project root (defaults to the working directory)")
    parser.subparsers_action = sub
    return parser


#: Commands that take no period and are dispatched before scope checks.
_PLAIN = ("init-client", "pull-quickbooks", "list-clients", "list-files", "validate-config",
          "approve-config", "review", "list-evals", "provider-status", "quickbooks-status",
          "chart-data", "serve")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    sub = parser.subparsers_action
    args = parser.parse_args(argv)
    if getattr(args, "scope", "month") == "month" and getattr(args, "period_required", False) \
            and args.period is None:
        sub.choices[args.command].error("the following arguments are required: --period")
    from finance_engine.onboarding import OnboardingError
    from finance_engine.review import ReviewError
    from finance_engine.service.commands import CommandError
    from finance_engine.service.core import ServiceError
    try:
        if args.command in _PLAIN:
            return _plain_command(args)
        if args.command == "report" and args.source == "quickbooks":
            _pull_for_report(args)
        _check_scope_args(args)
        if args.command != "list-periods" and args.period == LATEST:
            args.period = _resolve_latest(args)
        if args.command == "analyze":
            return _analyze(args)
        if args.command == "report":
            return _report(args)
        if args.command == "check-commentary":
            return _check_commentary(args)
        if args.command == "brief":
            return _brief(args)
        if args.command == "list-periods":
            return _list_periods(args)
        if args.command == "show-report":
            return _show_report(args)
    except (ConfigError, LayoutError, UsageError, SourceSelectionError, SpanError,
            InitError, QuickBooksError, FileNotFoundError, OnboardingError, ReviewError,
            CommandError, ServiceError) as exc:
        # A layout or config problem is reported, never worked around.
        print(f"error: {exc}", file=sys.stderr)
        if getattr(args, "json", False):
            _emit_json({"ok": False, "command": args.command, "error": str(exc),
                        "exit_code": EXIT_USAGE})
        return EXIT_USAGE
    return EXIT_USAGE


# ---- scope ---------------------------------------------------------------

def _scope(args) -> str:
    return getattr(args, "scope", "month") or "month"


def _check_scope_args(args) -> None:
    scope = _scope(args)
    if scope == "month":
        if getattr(args, "year", None) is not None:
            raise UsageError("--year applies to --scope year. For a month use --period "
                             "YYYY-MM.")
        return
    if args.period is not None:
        raise UsageError(f"--period applies to --scope month, not --scope {scope}."
                         + (" Use --year YYYY." if scope == "year" else ""))
    if scope == "year":
        if args.year is None:
            raise UsageError("--scope year needs --year YYYY, e.g. --year 2025.")
        if not 1900 <= args.year <= 9999:
            raise UsageError(f"--year must be a four-digit year, got {args.year}.")
    elif args.year is not None:
        raise UsageError("--year applies to --scope year, not --scope all.")


def _leaf(args) -> str:
    """The output folder name for this run: the period, year-YYYY, or all."""
    scope = _scope(args)
    if scope == "year":
        return f"year-{args.year:04d}"
    if scope == "all":
        return "all"
    return args.period


def _want(args) -> Want:
    scope = _scope(args)
    if scope == "year":
        return Want("year", args.year)
    if scope == "all":
        return Want("all")
    return Want("period", args.period)


def _build(args, dataset, config, root, choice) -> dict:
    scope = _scope(args)
    kwargs = _file_kwargs(choice)
    if scope == "year":
        return build_year_metrics(dataset, config, args.year, project_root=root, **kwargs)
    if scope == "all":
        return build_all_metrics(dataset, config, project_root=root, **kwargs)
    return build_metrics(dataset, config, args.period, project_root=root, **kwargs)


def _span_lines(doc: dict) -> list[str]:
    """The analyze summary for year and whole-file runs."""
    cov = doc["coverage"]
    recon = doc["reconciliation"]
    issues = doc["data_quality_issues"]
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    blocked = [m for m in doc["metrics"] if m["validation_status"] == "blocked"]
    if doc["scope"] == "year":
        span = cov.get("span") or ["?", "?"]
        coverage = f"{doc['year']}: {cov['label']} ({span[0]}..{span[1]})"
    else:
        coverage = cov["label"] + (f"; gaps: {', '.join(cov['gaps'])}" if cov["gaps"]
                                   else "")
    lines = [f"  metrics:        {len(doc['metrics'])}",
             f"  coverage:       {coverage}",
             f"  reconciliation: {check_count(recon)} (tolerance {recon['tolerance_abs']})"
             f"{not_provided_note(doc)}",
             f"  data quality:   {len(errors)} error(s), {len(warnings)} warning(s)"]
    if blocked:
        lines.append(f"  blocked:        {len(blocked)} metric(s) reported as n/a")
    return lines


# ---- output helpers ----------------------------------------------------

def _emit_json(payload: dict) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _say(args, text: str = "", err: bool = False) -> None:
    """Human-readable progress. With --json it moves to stderr, keeping stdout clean."""
    print(text, file=sys.stderr if (err or getattr(args, "json", False)) else sys.stdout)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


# ---- loading -------------------------------------------------------------

def _root(args) -> Path:
    return Path(args.root) if args.root else Path.cwd()


def _load(args, want: Want | None = None
          ) -> tuple[ClientConfig, Settings, SourceChoice, ExcelSource, Path]:
    from finance_engine.sources.factory import open_source

    root = _root(args)
    settings = Settings.from_env(root)
    config = ClientConfig.load(args.client, settings)
    choice = choose(config, settings, root, getattr(args, "workbook", None), want)
    if choice.note:
        print(choice.note, file=sys.stderr)
    return config, settings, choice, open_source(choice.path, config), root


def _file_kwargs(choice: SourceChoice) -> dict:
    """What a file-aware run adds to metrics.json. Empty for every other run."""
    if not choice.file_aware:
        return {}
    return {"periods_elsewhere": choice.periods_elsewhere(),
            "selection": choice.describe()}


def _output_key(args) -> str | None:
    """The file stem in the output path, when a workbook was named on the command line."""
    name = getattr(args, "workbook", None)
    if not name:
        return None
    if Path(name).name != name:
        raise UsageError(f"--file takes a workbook name, not a path: {name!r}.")
    return Path(name).stem


def _output_dir(settings: Settings, client_id: str, leaf: str,
                output_key: str | None = None) -> Path:
    base = settings.output_dir / client_id
    out = (base / output_key / leaf) if output_key else (base / leaf)
    out.mkdir(parents=True, exist_ok=True)
    return out


def _resolve_latest(args) -> str:
    """`--period latest` -> the newest month in the source, the top row of list-periods.

    Always read from the source, also for brief and check-commentary, so "latest" means
    the same month for every command. The resolved period goes to stderr: stdout carries
    the brief and the --json result.
    """
    config, _, _, source, _ = _load(args)
    keys = source.load(config).period_keys()
    if not keys:
        raise UsageError(f"--period latest: no months found in {source.path}.")
    print(f"period: {LATEST} -> {keys[-1]}", file=sys.stderr)
    return keys[-1]


def _validate_period(period: str) -> None:
    from finance_engine.model import Period
    try:
        Period.from_key(period)
    except ValueError as exc:
        raise UsageError(f"{exc}. Use --period YYYY-MM, e.g. 2026-06.") from None


def _missing_period_hint(config, settings, root, choice, doc) -> None:
    """Point at a sibling workbook when the requested month is in it instead."""
    if not any(i["code"] == "period_not_in_source" for i in doc["data_quality_issues"]):
        return
    hint = hint_for_missing_period(config, settings, root, choice.path, doc["period"])
    if hint:
        print(hint, file=sys.stderr)


def _summary(doc: dict, exit_code: int, outputs: dict[str, str]) -> dict:
    """The --json result for analyze and report."""
    issues = doc["data_quality_issues"]
    recon = doc["reconciliation"]
    src = doc["source"]
    return {
        "ok": exit_code in (EXIT_OK, EXIT_DATA),
        "exit_code": exit_code,
        "client_id": doc["client_id"],
        "scope": doc.get("scope", "month"),
        "period": doc.get("period"),
        "source": {"filename": src.get("filename"), "path": src.get("path"),
                   "sha256": src.get("sha256"),
                   "selection_mode": (src.get("selection") or {}).get("mode", "config")},
        "outputs": outputs,
        "metrics": len(doc["metrics"]),
        "blocked_metrics": sum(1 for m in doc["metrics"]
                               if m["validation_status"] == "blocked"),
        "reconciliation": {"all_passed": recon["all_passed"],
                           "checks_run": recon["checks_run"],
                           "checks_failed": recon["checks_failed"]},
        "data_quality": {
            "errors": [i["code"] for i in issues if i["severity"] == "error"],
            "warnings": [i["code"] for i in issues if i["severity"] == "warning"],
        },
    }


# ---- commands ----------------------------------------------------------

def _analyze(args) -> int:
    if _scope(args) == "month":
        _validate_period(args.period)
    config, settings, choice, source, root = _load(args, _want(args))
    dataset = source.load(config)
    doc = _build(args, dataset, config, root, choice)

    out = _output_dir(settings, config.client_id, _leaf(args), choice.output_key)
    path = out / "metrics.json"
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    recon = doc["reconciliation"]
    errors = [i for i in doc["data_quality_issues"] if i["severity"] == "error"]
    warnings = [i for i in doc["data_quality_issues"] if i["severity"] == "warning"]
    blocked = [m for m in doc["metrics"] if m["validation_status"] == "blocked"]

    _say(args, f"wrote {path}")
    if _scope(args) != "month":
        for line in _span_lines(doc):
            _say(args, line)
        for i in errors:
            print(f"  ERROR {i['code']}: {i['message']}", file=sys.stderr)
        code = EXIT_DATA if (errors or recon["checks_failed"]) else EXIT_OK
        if args.json:
            _emit_json({"command": "analyze",
                        **_summary(doc, code, {"metrics": _rel(path, root)})})
        return code
    _say(args, f"  metrics:        {len(doc['metrics'])}")
    _say(args, f"  periods in src: {doc['source']['period_count']} "
               f"({doc['source']['periods_available'][0]}"
               f"..{doc['source']['periods_available'][-1]})")
    _say(args, f"  reconciliation: {check_count(recon)} "
               f"(tolerance {recon['tolerance_abs']}){not_provided_note(doc)}")
    _say(args, f"  ttm window:     {doc['ttm']['window'][0]}..{doc['ttm']['window'][-1]} "
               f"({'complete' if doc['ttm']['complete'] else 'INCOMPLETE'})")
    _say(args, f"  data quality:   {len(errors)} error(s), {len(warnings)} warning(s)")
    if blocked:
        _say(args, f"  blocked:        {len(blocked)} metric(s) reported as n/a")
    for i in errors:
        print(f"  ERROR {i['code']}: {i['message']}", file=sys.stderr)
    _missing_period_hint(config, settings, root, choice, doc)

    code = EXIT_DATA if (errors or recon["checks_failed"]) else EXIT_OK
    if args.json:
        _emit_json({"command": "analyze",
                    **_summary(doc, code, {"metrics": _rel(path, root)})})
    return code


def _report(args) -> int:
    if _scope(args) == "month":
        _validate_period(args.period)
    config, settings, choice, source, root = _load(args, _want(args))
    dataset = source.load(config)
    doc = _build(args, dataset, config, root, choice)

    out = _output_dir(settings, config.client_id, _leaf(args), choice.output_key)
    metrics_path = out / "metrics.json"
    metrics_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                            encoding="utf-8")

    provider_name = args.provider or settings.llm_provider
    outcome = None
    if (provider_name or "").strip().lower() in ORCHESTRATED:
        # auto | gemini | claude-api: generate, validate, retry, fall back. Every other
        # value keeps the path below, exactly as before.
        outcome = _orchestrated_commentary(doc, provider_name, root,
                                           _reviewer_notes(out))
        commentary, provider_id, guard = outcome.text, outcome.provider_id, outcome.guard
        report_md = build_report(doc, commentary, provider_id, guard.to_dict(),
                                 footer=outcome.footer,
                                 commentary_from_brief=provider_id != "deterministic")
    else:
        try:
            provider = get_provider(provider_name)
        except ProviderUnavailable as exc:
            print(f"warning: {exc} Falling back to the deterministic provider.",
                  file=sys.stderr)
            provider = get_provider("deterministic")
        provider_id = provider.provider_id
        commentary = provider.generate(doc)

        # The guard runs on every provider's output, including the deterministic one. A
        # provider that cannot pass it has no business writing a client report.
        guard = NumberGuard(doc).check(commentary)
        report_md = build_report(doc, commentary, provider_id, guard.to_dict())
        if _reviewer_notes(out, quiet=True):
            print("reviewer notes: the last send-back comment is shown beside the draft "
                  "(review log); the template provider does not take notes.",
                  file=sys.stderr)
    report_path = out / "report.md"
    report_path.write_text(report_md, encoding="utf-8")
    log_path = None
    if outcome is not None:
        log_path = out / "commentary_log.json"
        log_path.write_text(json.dumps(outcome.log, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    review_state = _submit_for_review(out)

    recon = doc["reconciliation"]
    errors = [i for i in doc["data_quality_issues"] if i["severity"] == "error"]

    _say(args, f"wrote {metrics_path}")
    _say(args, f"wrote {report_path}")
    if log_path is not None:
        _say(args, f"wrote {log_path}")
    _say(args, f"  provider:       {provider_id}")
    _say(args, f"  number check:   {'PASS' if guard.passed else 'REJECTED'} "
               f"({len(guard.supported)}/{guard.checked} numbers traced)")
    _say(args, f"  reconciliation: {check_count(recon)}{not_provided_note(doc)}")
    if review_state:
        _say(args, f"  review:         {review_state}")
    if _scope(args) == "month":
        _missing_period_hint(config, settings, root, choice, doc)
    else:
        _say(args, f"  coverage:       {doc['period_label']}")
    if not guard.passed:
        print(guard.summary(), file=sys.stderr)
        code = EXIT_GUARD
    else:
        code = EXIT_DATA if (errors or recon["checks_failed"]) else EXIT_OK
    if args.json:
        payload = _summary(doc, code, {"metrics": _rel(metrics_path, root),
                                       "report": _rel(report_path, root)})
        payload["ok"] = code in (EXIT_OK, EXIT_DATA)
        payload["provider"] = provider_id
        if outcome is not None:
            payload["outputs"]["commentary_log"] = _rel(log_path, root)
            payload["commentary"] = outcome.summary()
        payload["number_check"] = {"passed": guard.passed, "checked": guard.checked,
                                   "supported": len(guard.supported)}
        payload["review"] = review_state
        _emit_json({"command": "report", **payload})
    return code


def _submit_for_review(out: Path) -> str | None:
    """A new report is Ready for review: `report` submits it as `system`. A broken or
    unreadable review log is reported and left alone; the report itself still stands."""
    from finance_engine.review import ReviewError, status, submit_generated
    try:
        submit_generated(out)
        return status(out).label
    except ReviewError as exc:
        print(f"warning: not put in review: {exc}", file=sys.stderr)
        return None


def _reviewer_notes(out: Path, quiet: bool = False) -> str | None:
    """The last send-back comment for this report folder, if the last review action was a
    send-back. Plain text for the writer; it never changes a figure or the guard."""
    from finance_engine.review import ReviewError, last_send_back
    try:
        entry = last_send_back(out)
    except ReviewError as exc:
        print(f"warning: review log unreadable, no reviewer notes passed: {exc}",
              file=sys.stderr)
        return None
    if not entry or not entry.get("comment"):
        return None
    if not quiet:
        print(f"reviewer notes: passing the send-back comment by {entry.get('name')} "
              f"({entry.get('time_utc')}) to the commentary writer", file=sys.stderr)
    return entry["comment"]


def _orchestrated_commentary(doc: dict, provider_name: str, root: Path,
                             reviewer_notes: str | None = None):
    """Commentary through config/providers.yaml; notes on stderr, keys never printed."""
    from finance_engine.commentary.orchestrator import write_commentary
    from finance_engine.commentary.providers import ProvidersConfig
    from finance_engine.credentials import EnvCredentialStore

    config = ProvidersConfig.load(root / "config" / "providers.yaml")
    credentials = EnvCredentialStore(config.key_names(), env_file=root / ".env")
    return write_commentary(doc, provider_name, config, credentials,
                            notify=lambda line: print(line, file=sys.stderr),
                            reviewer_notes=reviewer_notes)


def _metrics_path(args) -> Path:
    """The metrics.json an earlier `analyze` wrote for this client and scope."""
    scope = _scope(args)
    if scope == "month":
        _validate_period(args.period)
    root = _root(args)
    settings = Settings.from_env(root)
    base = settings.output_dir / args.client
    key = _output_key(args)
    metrics_path = (base / key if key else base) / _leaf(args) / "metrics.json"
    if not metrics_path.exists():
        hint = f" --file {args.workbook}" if key else ""
        what = {"month": f"--period {args.period}",
                "year": f"--scope year --year {args.year}",
                "all": "--scope all"}[scope]
        raise FileNotFoundError(
            f"{metrics_path} not found. Run 'analyze --client {args.client} "
            f"{what}{hint}' first."
        )
    return metrics_path


def _check_commentary(args) -> int:
    metrics_path = _metrics_path(args)
    text_path = Path(args.file)
    if not text_path.exists():
        raise FileNotFoundError(f"Commentary file not found: {text_path}")

    guard = NumberGuard.from_path(metrics_path)
    result = guard.check(text_path.read_text(encoding="utf-8"))

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"checked {text_path} against {metrics_path}")
        print(result.summary())
        if result.passed:
            for note in result.notes:
                print(f"  note: {note}")
    return EXIT_OK if result.passed else EXIT_GUARD


def _brief(args) -> int:
    path = _metrics_path(args)
    doc = json.loads(path.read_text(encoding="utf-8"))
    text = build_brief(doc, args.metric)
    if args.json:
        _emit_json({"ok": True, "command": "brief", "exit_code": EXIT_OK,
                    "client_id": doc.get("client_id"), "scope": doc.get("scope", "month"),
                    "period": doc.get("period"),
                    "metrics_path": _rel(path, _root(args)), "brief": text})
        return EXIT_OK
    sys.stdout.reconfigure(encoding="utf-8")
    print(text, end="")
    return EXIT_OK


def _list_periods(args) -> int:
    from finance_engine.model import trailing_window

    root = _root(args)
    settings = Settings.from_env(root)
    config = ClientConfig.load(args.client, settings)
    folder = source_folder(config, settings, root)
    try:
        choice = choose(config, settings, root, args.workbook, None)
    except SourceSelectionError:
        if args.workbook:
            raise
        choice = None              # several workbooks and no default: list them all
    # The per-file listing is only needed when the folder really holds several of this
    # client's workbooks, or for --json; otherwise the output is exactly as before.
    from finance_engine.sources.factory import open_source, source_pattern
    many = (is_own_folder(config, settings, root)
            and len(list_workbooks(folder, source_pattern(config))) > 1)
    inventory = workbook_inventory(config, settings, root) if (many or args.json
                                                               or choice is None) else []

    rows: list[dict] = []
    lines: list[str] = []
    if choice is not None:
        dataset = open_source(choice.path, config).load(config)
        keys = dataset.period_keys()
        lines.append(f"{config.company_name} ({config.client_id}) — {len(keys)} month(s)")
        lines.append(f"source: {dataset.source_path}")
        ttm = config.ttm_months
        for key in reversed(keys):
            window = trailing_window(keys, key, ttm)
            complete = all(p in set(keys) for p in window)
            rows.append({"period": key, "ttm_complete": complete,
                         "ttm_window": [window[0], window[-1]]})
            lines.append(f"  {key}  ttm {'complete  ' if complete else 'INCOMPLETE'}"
                         f"  ({window[0]}..{window[-1]})")
    else:
        lines.append(f"{config.company_name} ({config.client_id}) — {len(inventory)} "
                     f"workbooks in {folder}; choose one with --file NAME")
    if many or choice is None:
        if choice is not None:
            lines.append("")
            lines.append(f"workbooks in {folder}:")
        for info in inventory:
            mark = "  (read by default)" if choice is not None and \
                info.path == choice.path and not args.workbook else ""
            mark = "  (selected)" if args.workbook and info.filename == args.workbook \
                else mark
            lines.append(f"  {info.filename}  {info.span}{mark}")

    if args.json:
        _emit_json({
            "ok": True, "command": "list-periods", "exit_code": EXIT_OK,
            "client_id": config.client_id, "company_name": config.company_name,
            "source_folder": _rel(folder, root),
            "selected": choice.path.name if choice is not None else None,
            "selection_mode": choice.mode if choice is not None else None,
            "periods": rows,
            "files": [{**i.to_dict(),
                       "selected": choice is not None and i.path == choice.path}
                      for i in inventory],
        })
        for line in lines:
            print(line, file=sys.stderr)
        return EXIT_OK
    for line in lines:
        print(line)
    return EXIT_OK


def _pull(args) -> dict:
    from finance_engine.model import Period
    from finance_engine.sources.quickbooks_api import pull_snapshot

    for flag, value in (("--start", args.start), ("--end", args.end)):
        if not value:
            raise UsageError(f"{flag} YYYY-MM is required to pull from QuickBooks.")
        try:
            Period.from_key(value)
        except ValueError as exc:
            raise UsageError(f"{flag}: {exc}. Use YYYY-MM, e.g. 2026-09.") from None
    root = _root(args)
    settings = Settings.from_env(root)
    config = ClientConfig.load(args.client, settings)
    return pull_snapshot(config, root, settings.data_dir, args.start, args.end)


def _pull_quickbooks(args) -> int:
    """Read-only: one token refresh, one GET, one new snapshot file."""
    root = _root(args)
    result = _pull(args)
    _say(args, f"wrote {result['path']}")
    _say(args, f"  sha256:         {result['sha256']}")
    _say(args, f"  months:         {result['first_period']}..{result['last_period']} "
               f"({result['period_count']})")
    _say(args, f"  next:           python -m finance_engine report --client {args.client} "
               f"--period {result['last_period']} --file {result['filename']}")
    if args.json:
        _emit_json({"ok": True, "command": "pull-quickbooks", "exit_code": EXIT_OK,
                    "client_id": args.client, "snapshot": _rel(result["path"], root),
                    "filename": result["filename"], "sha256": result["sha256"],
                    "first_period": result["first_period"],
                    "last_period": result["last_period"],
                    "period_count": result["period_count"]})
    return EXIT_OK


def _pull_for_report(args) -> None:
    """`report --source quickbooks`: pull, then analyse exactly that snapshot."""
    if args.workbook:
        raise UsageError("--source quickbooks pulls a new snapshot; do not also pass --file.")
    result = _pull(args)
    print(f"pulled {result['filename']} (sha256 {result['sha256'][:16]}...)",
          file=sys.stderr)
    args.workbook = result["filename"]


def _init_client(args) -> int:
    from finance_engine.init_client import init_client, next_command, summary_lines

    root = _root(args)
    settings = Settings.from_env(root)
    if args.draft:
        return _init_draft(args, root, settings)
    result = init_client(Path(args.file), args.client_id, args.name, root,
                         settings.config_dir, settings.data_dir, force=args.force)
    lines = summary_lines(result, root)
    code = EXIT_OK if result.ok else EXIT_DATA
    if args.json:
        d = result.draft
        _emit_json({
            "ok": result.ok, "command": "init-client", "exit_code": code,
            "status": result.status, "client_id": args.client_id,
            "config_path": _rel(result.config_path, root),
            "workbook_copy": _rel(result.workbook_copy, root),
            "periods": d.periods,
            "detected": [{"what": x.what, "value": x.value, "confidence": x.confidence,
                          "how": x.how} for x in d.detected],
            "categories": d.categories,
            "needs_decision": d.decisions,
            "problems": d.problems,
            "validation": result.validation,
            "next_command": next_command(result),
        })
        for line in lines:
            print(line, file=sys.stderr)
        return code
    for line in lines:
        print(line)
    return code


# ---- onboarding, review, status and the web app (Phase 5D) -----------------------------
#
# Each of these is the terminal equivalent of a web UI action (see
# finance_engine/service/parity.py). They read files or call finance_engine.service;
# none of them calculates a figure.

def _plain_command(args) -> int:
    handlers = {"init-client": _init_client, "pull-quickbooks": _pull_quickbooks,
                "list-clients": _list_clients, "list-files": _list_files,
                "validate-config": _validate_config, "approve-config": _approve_config,
                "review": _review, "list-evals": _list_evals,
                "provider-status": _provider_status,
                "quickbooks-status": _quickbooks_status, "chart-data": _chart_data,
                "serve": _serve}
    return handlers[args.command](args)


def _done(args, command: str, payload: dict, lines: list[str], code: int = EXIT_OK) -> int:
    if args.json:
        _emit_json({"ok": code in (EXIT_OK, EXIT_DATA), "command": command,
                    "exit_code": code, **payload})
        for line in lines:
            print(line, file=sys.stderr)
        return code
    sys.stdout.reconfigure(encoding="utf-8")
    for line in lines:
        print(line)
    return code


def _init_draft(args, root: Path, settings: Settings) -> int:
    from finance_engine.onboarding import init_draft
    s = init_draft(Path(args.file), args.client_id, args.name, root, settings.data_dir,
                   force=args.force)
    lines = [f"Drafted {s['config_path']} from {s['workbook']} (the original is untouched).",
             f"Detected: sheet {s['sheet']!r}, header row {s['header_row']}, "
             f"{s['periods']['count']} month(s)"
             + (f" ({s['periods']['first']} to {s['periods']['last']})"
                if s['periods']['count'] else "") + ".",
             f"Checks: {s['checks'] or 'not run'}; status {s['status'].upper()}."]
    lines += [f"  problem: {p}" for p in s["problems"]]
    lines += [f"  warning: {w}" for w in s["warnings"]]
    lines += [f"  decide: {d}" for d in s["needs_decision"]]
    lines.append("Not live: config/clients/ is unchanged until approve-config.")
    lines.append(f"Next: {s['next_command']}")
    return _done(args, "init-client", {"draft": s}, lines,
                 EXIT_OK if s["status"] == "valid" else EXIT_DATA)


def _list_clients(args) -> int:
    from finance_engine.service.core import list_clients
    data = list_clients(_root(args))
    lines = [f"{len(data['clients'])} client config(s):"]
    for c in data["clients"]:
        approved = (f"  approved by {c['approval']['approved_by']}"
                    f"{'' if c['approval']['config_matches'] else ' (config changed since)'}"
                    if c.get("approval") else "")
        lines.append(f"  {c['client_id']:<36} {c.get('source_type') or '?':<10} "
                     f"{c.get('company_name') or c.get('error')}{approved}")
    if data["drafts"]:
        lines.append(f"{len(data['drafts'])} draft(s) waiting for approve-config:")
        for d in data["drafts"]:
            lines.append(f"  {d['client_id']:<36} {d['status']:<8} checks "
                         f"{d['checks'] or 'not run'}{'  (replaces a live config)' if d['live'] else ''}")
    return _done(args, "list-clients", data, lines)


def _list_files(args) -> int:
    from finance_engine.service.core import list_files
    data = list_files(_root(args), args.client)
    lines = [f"{data['company_name']} ({args.client}): {len(data['files'])} file(s) in "
             f"{data['source_folder']}"]
    for f in data["files"]:
        if "label" in f:                       # a QuickBooks snapshot, newest pull first
            lines.append(f"  {f['label']}  {f['filename']}"
                         f"{'  (default)' if f['default'] else ''}"
                         + ("" if f["readable"] else f"  cannot be read: {f['reason']}"))
            continue
        span = (f"{f['first_period']}..{f['last_period']}, {f['period_count']} month(s)"
                if f["period_count"] else f.get("error") or "no months found")
        lines.append(f"  {f['filename']}  {span}  sha256 {str(f['sha256'])[:12]}"
                     f"{'  (default)' if f['default'] else ''}")
    if data.get("default_note"):
        lines.append(data["default_note"])
    return _done(args, "list-files", data, lines)


def _show_report(args) -> int:
    from finance_engine.service.core import get_report
    _output_key(args)
    data = get_report(_root(args), args.client, _scope(args), args.period, args.year,
                      args.workbook)
    b = data["badges"]
    nc = b["number_check"]
    p = b["provider"]
    lines = [f"{data['company_name']} — {b['scope_label']}",
             f"  folder:         {data['folder']}",
             f"  reconciliation: {b['reconciliation']['label']}",
             f"  number check:   " + (f"{nc['result']} ({nc['detail']})" if nc else
                                      "no report.md yet"),
             f"  provider:       {p['provider'] or '-'} (attempt: {p['attempt'] or '-'})",
             f"  data left this machine: "
             + ({True: "yes", False: "no", None: "-"}[p["data_left_machine"]]),
             f"  scope:          {b['scope_label']}",
             f"  source sha256:  {str(b['source_sha256'])[:16]}",
             f"  review:         {b['review']}",
             f"  report.md:      {data['files']['report'] or '-'}",
             f"  metrics.json:   {data['files']['metrics']}"]
    if data["cell_warnings"]:
        lines.append(f"  cell warnings:  {len(data['cell_warnings'])}")
    return _done(args, "show-report", data, lines)


def _validate_config(args) -> int:
    from finance_engine.onboarding import validate_config
    root = _root(args)
    text = None
    if args.from_file:
        text = Path(args.from_file).read_text(encoding="utf-8")
    r = validate_config(args.client, root, Settings.from_env(root), draft=args.draft,
                        from_text=text)
    lines = [f"{'VALID' if r['ok'] else 'INVALID'}: {r['config_path']}"
             + (f" — analyze {r['latest']} reconciled {r['checks']}" if r["checks"] else "")]
    lines += [f"  error{' line ' + str(e['line']) if e['line'] else ''}: {e['message']}"
              for e in r["errors"]]
    lines += [f"  warning {w['code']}: {w['message']}" for w in r["warnings"]]
    if r["ok"] and args.draft:
        lines.append(f"Next: python -m finance_engine approve-config --client {args.client} "
                     f"--by \"<your name>\"")
    return _done(args, "validate-config", {"validation": r}, lines,
                 EXIT_OK if r["ok"] else EXIT_DATA)


def _approve_config(args) -> int:
    from finance_engine.onboarding import approve_config
    root = _root(args)
    rec = approve_config(args.client, args.by, root, Settings.from_env(root))
    lines = [f"Approved by {rec['approved_by']} (self-declared) at {rec['approved_at_utc']}.",
             f"Live: {rec['config_path']} (sha256 {rec['config_sha256'][:16]}...)",
             f"Checks: {rec['validation']['checks']} for {rec['validation']['latest']}.",
             f"Next: python -m finance_engine report --client {args.client} --period "
             f"{rec['validation']['latest']}"]
    return _done(args, "approve-config", {"approval": rec}, lines)


def _review(args) -> int:
    from finance_engine import review
    from finance_engine.service import core
    root = _root(args)
    if args.action == "queue":
        data = core.review_queue(root)
        lines = [f"{data['count']} report(s) ready for review:"]
        lines += [f"  {q['folder']}  submitted by {q['submitted_by']} at "
                  f"{q['submitted_at_utc']}" for q in data["ready_for_review"]]
        return _done(args, "review", {"action": "queue", **data}, lines)
    if not args.client:
        raise UsageError(f"review {args.action} needs --client.")
    _check_scope_args(args)
    if _scope(args) == "month" and not args.period:
        raise UsageError(f"review {args.action} needs --period YYYY-MM (or --scope year "
                         f"--year YYYY, or --scope all).")
    where = dict(scope=_scope(args), period=args.period, year=args.year, file=args.workbook)
    if args.action in ("status", "log"):
        data = core.review_status(root, args.client, **where)
        lines = [f"{data['folder']}: {data['label']}"]
        if args.action == "log" or data["last_entry"]:
            entries = data["log"] if args.action == "log" else data["log"][-1:]
            for e in entries:
                lines.append(f"  {e['time_utc']}  {e['action']:<13}  {e['name']}"
                             + (f"  — {e['comment']}" if e.get("comment") else "")
                             + f"  (report {str(e.get('report_sha256'))[:12]}, "
                               f"provider {e.get('provider')})")
        if not data["log_intact"]:
            lines.append("  WARNING: the review log's hash chain is broken.")
        return _done(args, "review", {"action": args.action, **data}, lines)
    action = {"submit": review.SUBMIT, "mark-reviewed": review.MARK_REVIEWED,
              "send-back": review.SEND_BACK}[args.action]
    data = core.review_action(root, args.client, action, args.by, args.comment, **where)
    e = data["entry"]
    lines = [f"{args.action}: recorded for {e['name']} (self-declared) at {e['time_utc']}.",
             f"  status: {data['status']['label']}",
             f"  report.md sha256 {e['report_sha256'][:16]}..., metrics.json sha256 "
             f"{e['metrics_sha256'][:16]}..."]
    if e.get("version_file"):
        lines.append(f"  copy kept: review/{e['version_file']}")
    return _done(args, "review", {"action": args.action, **data}, lines)


def _list_evals(args) -> int:
    from finance_engine.service.core import list_evals
    data = list_evals(_root(args))
    lines = [f"{len(data['cases'])} golden cases (newest baselines: "
             + ", ".join(f"{k}={v or 'none'}" for k, v in data["baselines"].items()) + ")"]
    for c in data["cases"]:
        lines.append(f"  {c['id']:<32} engine {c['engine'] or '-':<5} "
                     f"deterministic {c['skill_deterministic'] or '-':<5} "
                     f"claude {c['skill'] or '-'}")
    lines.append("Free: " + " ; ".join(data["free_commands"]))
    lines.append(f"Paid (run it yourself; uses Claude Pro usage): {data['paid_command']}")
    return _done(args, "list-evals", data, lines)


def _provider_status(args) -> int:
    from finance_engine.service.core import provider_status
    data = provider_status(_root(args))
    lines = [f"order: {' -> '.join(data['order'])}; allow_cloud: {data['allow_cloud']}"]
    for p in data["providers"]:
        lines.append(f"  {p['provider']:<14} enabled {'yes' if p['enabled'] else 'no':<4}"
                     f"configured {'yes' if p['configured'] else 'no':<4}"
                     + (f"missing: {', '.join(p['missing_variables'])}"
                        if p["missing_variables"] else "")
                     + ("" if p.get("package_installed") in (True, None)
                        else "  package not installed"))
    lines.append(f"What leaves: {data.get('what_leaves', '-')}")
    return _done(args, "provider-status", data, lines)


def _quickbooks_status(args) -> int:
    from finance_engine.service.core import quickbooks_status
    data = quickbooks_status(_root(args))
    lines = [f"QuickBooks (sandbox only, read-only): configured "
             f"{'yes' if data['configured'] else 'no'}",
             f"  environment: {data['environment']}; token file present: "
             f"{'yes' if data['token_store_present'] else 'no'}",
             f"  QuickBooks clients: {', '.join(data['clients']) or 'none'}"]
    if data["missing_variables"]:
        lines.append(f"  missing variables: {', '.join(data['missing_variables'])}")
    return _done(args, "quickbooks-status", data, lines)


def _chart_data(args) -> int:
    from finance_engine.service.core import chart_data
    data = chart_data(_root(args), args.client, args.workbook, args.year)
    lines = [f"{args.client}: {data['label']} (monthly; never annualised)"]
    if data["gaps"]:
        lines.append(f"  gaps (missing from the source): {', '.join(data['gaps'])}")
    if data["reconciliation_failed"]:
        lines.append(f"  reconciliation failed in: "
                     f"{', '.join(data['reconciliation_failed'])}")
    for s in data["series"]:
        lines.append(f"  {s['label']}  [{s['source']}]")
        for pt in s["points"]:
            mark = "  RECONCILIATION FAILED" if pt["reconciliation_failed"] else ""
            lines.append(f"    {pt['period']}  {pt['display']:>16}  {pt['status']}{mark}")
    return _done(args, "chart-data", data, lines)


LOOPBACK = "127.0.0.1"


def _serve(args) -> int:
    host = "127.0.0.1" if args.host == "localhost" else args.host
    if host != LOOPBACK:
        raise UsageError(f"--host {args.host!r} refused: the web app binds to {LOOPBACK} "
                         f"only. It has no sign-in, so it must never listen on a network.")
    try:
        from finance_engine.web import app as web_app      # Part 2
    except ImportError as exc:
        raise UsageError(f"The web app cannot start: {exc}. It needs the optional "
                         f"packages (pip install -r requirements-optional.txt) and the "
                         f"finance_engine/web module.") from None
    return web_app.serve(_root(args), host, args.port)


if __name__ == "__main__":
    sys.exit(main())
