"""Command line interface.

    python -m finance_engine analyze          --client abc_inc --period 2026-06
    python -m finance_engine report           --client abc_inc --period 2026-06
    python -m finance_engine check-commentary --client abc_inc --period 2026-06 --file x.md
    python -m finance_engine list-periods     --client abc_inc

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

from finance_engine.commentary.base import ProviderUnavailable, get_provider
from finance_engine.config import ClientConfig, ConfigError, Settings
from finance_engine.guard import NumberGuard
from finance_engine.metrics import build_metrics
from finance_engine.model import LayoutError
from finance_engine.report import build_report
from finance_engine.sources.excel import ExcelSource

EXIT_OK, EXIT_USAGE, EXIT_DATA, EXIT_GUARD = 0, 1, 2, 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m finance_engine",
        description="Deterministic monthly financial trend analysis. "
                    "Python calculates every number.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p, period_required=True):
        p.add_argument("--client", required=True, help="client_id, e.g. abc_inc")
        p.add_argument("--period", required=period_required,
                       help="reporting month as YYYY-MM, e.g. 2026-06")
        p.add_argument("--root", default=None,
                       help="project root (defaults to the working directory)")

    p_analyze = sub.add_parser("analyze", help="write metrics.json")
    add_common(p_analyze)

    p_report = sub.add_parser("report", help="write metrics.json and report.md")
    add_common(p_report)
    p_report.add_argument("--provider", default=None,
                          help="commentary provider (default: $LLM_PROVIDER or "
                               "'deterministic')")

    p_check = sub.add_parser("check-commentary",
                             help="check a text file's numbers against metrics.json")
    add_common(p_check)
    p_check.add_argument("--file", required=True, help="text or markdown file to check")
    p_check.add_argument("--json", action="store_true", help="emit the result as JSON")

    p_periods = sub.add_parser("list-periods", help="list the months in a client's source")
    add_common(p_periods, period_required=False)

    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            return _analyze(args)
        if args.command == "report":
            return _report(args)
        if args.command == "check-commentary":
            return _check_commentary(args)
        if args.command == "list-periods":
            return _list_periods(args)
    except (ConfigError, LayoutError) as exc:
        # A layout or config problem is reported, never worked around.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_USAGE


# ---- commands ----------------------------------------------------------

def _load(args) -> tuple[ClientConfig, Settings, object, Path]:
    root = Path(args.root) if args.root else Path.cwd()
    settings = Settings.from_env(root)
    config = ClientConfig.load(args.client, settings)
    if config.source_directory:
        source_dir = root / config.source_directory
    else:
        source_dir = settings.data_dir / config.client_id / "source"
    filename = config.source_filename
    if filename:
        path = source_dir / filename
    else:
        candidates = sorted(source_dir.glob("*.xlsx"))
        if not candidates:
            raise FileNotFoundError(f"No .xlsx found in {source_dir}")
        path = candidates[0]
    if not path.exists():
        raise FileNotFoundError(
            f"Source workbook not found: {path}. Put the client's workbook there, or set "
            f"source.filename in {config.path}."
        )
    return config, settings, ExcelSource(path), root


def _output_dir(settings: Settings, client_id: str, period: str) -> Path:
    out = settings.output_dir / client_id / period
    out.mkdir(parents=True, exist_ok=True)
    return out


def _validate_period(period: str) -> None:
    from finance_engine.model import Period
    Period.from_key(period)


def _analyze(args) -> int:
    _validate_period(args.period)
    config, settings, source, root = _load(args)
    dataset = source.load(config)
    doc = build_metrics(dataset, config, args.period, project_root=root)

    out = _output_dir(settings, config.client_id, args.period)
    path = out / "metrics.json"
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    recon = doc["reconciliation"]
    errors = [i for i in doc["data_quality_issues"] if i["severity"] == "error"]
    warnings = [i for i in doc["data_quality_issues"] if i["severity"] == "warning"]
    blocked = [m for m in doc["metrics"] if m["validation_status"] == "blocked"]

    print(f"wrote {path}")
    print(f"  metrics:        {len(doc['metrics'])}")
    print(f"  periods in src: {doc['source']['period_count']} "
          f"({doc['source']['periods_available'][0]}"
          f"..{doc['source']['periods_available'][-1]})")
    print(f"  reconciliation: {recon['checks_run'] - recon['checks_failed']}"
          f"/{recon['checks_run']} passed (tolerance {recon['tolerance_abs']})")
    print(f"  ttm window:     {doc['ttm']['window'][0]}..{doc['ttm']['window'][-1]} "
          f"({'complete' if doc['ttm']['complete'] else 'INCOMPLETE'})")
    print(f"  data quality:   {len(errors)} error(s), {len(warnings)} warning(s)")
    if blocked:
        print(f"  blocked:        {len(blocked)} metric(s) reported as n/a")
    for i in errors:
        print(f"  ERROR {i['code']}: {i['message']}", file=sys.stderr)

    return EXIT_DATA if (errors or recon["checks_failed"]) else EXIT_OK


def _report(args) -> int:
    _validate_period(args.period)
    config, settings, source, root = _load(args)
    dataset = source.load(config)
    doc = build_metrics(dataset, config, args.period, project_root=root)

    out = _output_dir(settings, config.client_id, args.period)
    metrics_path = out / "metrics.json"
    metrics_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                            encoding="utf-8")

    provider_name = args.provider or settings.llm_provider
    try:
        provider = get_provider(provider_name)
    except ProviderUnavailable as exc:
        print(f"warning: {exc} Falling back to the deterministic provider.",
              file=sys.stderr)
        provider = get_provider("deterministic")

    commentary = provider.generate(doc)

    # The guard runs on every provider's output, including the deterministic one. A
    # provider that cannot pass it has no business writing a client report.
    guard = NumberGuard(doc).check(commentary)
    report_md = build_report(doc, commentary, provider.provider_id, guard.to_dict())
    report_path = out / "report.md"
    report_path.write_text(report_md, encoding="utf-8")

    recon = doc["reconciliation"]
    errors = [i for i in doc["data_quality_issues"] if i["severity"] == "error"]

    print(f"wrote {metrics_path}")
    print(f"wrote {report_path}")
    print(f"  provider:       {provider.provider_id}")
    print(f"  number check:   {'PASS' if guard.passed else 'REJECTED'} "
          f"({len(guard.supported)}/{guard.checked} numbers traced)")
    print(f"  reconciliation: {recon['checks_run'] - recon['checks_failed']}"
          f"/{recon['checks_run']} passed")
    if not guard.passed:
        print(guard.summary(), file=sys.stderr)
        return EXIT_GUARD
    return EXIT_DATA if (errors or recon["checks_failed"]) else EXIT_OK


def _check_commentary(args) -> int:
    _validate_period(args.period)
    root = Path(args.root) if args.root else Path.cwd()
    settings = Settings.from_env(root)
    metrics_path = settings.output_dir / args.client / args.period / "metrics.json"
    if not metrics_path.exists():
        raise FileNotFoundError(
            f"{metrics_path} not found. Run 'analyze --client {args.client} "
            f"--period {args.period}' first."
        )
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


def _list_periods(args) -> int:
    config, settings, source, root = _load(args)
    dataset = source.load(config)
    keys = dataset.period_keys()
    print(f"{config.company_name} ({config.client_id}) — {len(keys)} month(s)")
    print(f"source: {dataset.source_path}")
    ttm = config.ttm_months
    for key in reversed(keys):
        from finance_engine.model import trailing_window
        window = trailing_window(keys, key, ttm)
        complete = all(p in set(keys) for p in window)
        print(f"  {key}  ttm {'complete  ' if complete else 'INCOMPLETE'}"
              f"  ({window[0]}..{window[-1]})")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
