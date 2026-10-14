"""Thin functions over the engine, returning plain JSON-able data.

No money maths: every figure returned here is a value the engine already wrote
(metrics.json, report.md, commentary_log.json) or a count of files. Formatting that
already exists in Python (`check_count`, the brief) is reused, never re-implemented.
Secrets never leave: provider and QuickBooks status return "configured yes/no" and the
NAMES of missing variables only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import yaml

from finance_engine import onboarding, review
from finance_engine.config import ClientConfig, ConfigError, Settings
from finance_engine.service import commands
from finance_engine.service.commands import CommandError
from finance_engine.trace_links import prose_links


class ServiceError(Exception):
    """A request the service refuses, with the engine's own message."""


def _settings(root: Path) -> Settings:
    return Settings.from_env(Path(root))


def _rel(path: Path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _sha(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _config(root: Path, client_id: str) -> tuple[ClientConfig, Settings]:
    commands.check_client(client_id)
    settings = _settings(root)
    try:
        return ClientConfig.load(client_id, settings), settings
    except ConfigError as exc:
        raise ServiceError(str(exc)) from None


# ---- clients, files, periods ---------------------------------------------------------

def list_clients(root: Path) -> dict:
    """Every live config, and every draft waiting for approval."""
    settings = _settings(root)
    clients, live = [], set()
    for path in sorted(settings.config_dir.glob("*.yaml")):
        if path.name.endswith(".draft.yaml"):
            continue
        entry = {"client_id": path.stem, "config_path": _rel(path, root),
                 "id_valid": bool(onboarding.CLIENT_ID.fullmatch(path.stem))}
        try:
            cfg = ClientConfig.load_path(path)
            entry.update(company_name=cfg.company_name, source_type=cfg.source_type,
                         synthetic=cfg.is_synthetic or "SYNTHETIC" in cfg.company_name,
                         error=None)
        except ConfigError as exc:
            entry.update(company_name=None, source_type=None, synthetic=None,
                         error=str(exc))
        approval = onboarding.approval_path(settings.data_dir, path.stem)
        if approval.exists():
            rec = json.loads(approval.read_text(encoding="utf-8"))
            entry["approval"] = {k: rec.get(k) for k in ("approved_by", "approved_at_utc",
                                                         "config_sha256")}
            entry["approval"]["config_matches"] = rec.get("config_sha256") == _sha(path)
        clients.append(entry)
        live.add(path.stem)
    drafts = []
    if settings.data_dir.exists():
        for summary in sorted(settings.data_dir.glob("*/draft/draft_summary.json")):
            doc = json.loads(summary.read_text(encoding="utf-8"))
            drafts.append({"client_id": doc.get("client_id"),
                           "company_name": doc.get("company_name"),
                           "status": doc.get("status"), "checks": doc.get("checks"),
                           "drafted_at_utc": doc.get("drafted_at_utc"),
                           "live": doc.get("client_id") in live})
    return {"clients": clients, "drafts": drafts}


def list_files(root: Path, client_id: str) -> dict:
    """The client's source files with the months each holds, and which is the default.

    For a QuickBooks client each snapshot also gets the label the picker shows ("Pulled
    14 Oct 2026, 11:15 – Jul 2026 to Sep 2026", local time), newest pull first, and an
    unreadable one a one-line reason. The default is the newest readable snapshot."""
    from finance_engine.sources.selection import (SourceSelectionError, choose,
                                                  snapshot_inventory, source_folder,
                                                  workbook_inventory)
    config, settings = _config(root, client_id)
    try:
        default = choose(config, settings, root, None, None).path.name
    except (SourceSelectionError, FileNotFoundError):
        default = None
    quickbooks = config.source_type == "quickbooks"
    inventory = workbook_inventory(config, settings, root)
    if quickbooks:
        inventory = snapshot_inventory([i.path for i in inventory], config)
    files = []
    for info in inventory:
        entry = {**info.to_dict(), "sha256": _sha(info.path),
                 "default": info.filename == default}
        if quickbooks:
            entry.update(snapshot_picker_entry(info))
        files.append(entry)
    out = {"client_id": client_id, "company_name": config.company_name,
           "source_type": config.source_type,
           "source_folder": _rel(source_folder(config, settings, root), root),
           "default_file": default, "files": files}
    if quickbooks:
        chosen = next((f for f in files if f["default"]), None)
        out["default_note"] = (f"No file chosen: using the newest readable snapshot, "
                               f"{chosen['label']}." if chosen else
                               "No snapshot can be read. Pull a new one in Sources and "
                               "settings.")
    return out


def _month_label(day) -> str:
    return f"{day:%b} {day.year}"


def snapshot_picker_entry(info) -> dict:
    """The picker's text for one QuickBooks snapshot, formatted here, never in the UI:
    when it was pulled (this machine's local time) and the months it was asked for (read
    from the snapshot itself, even when it cannot be analysed)."""
    from finance_engine.model import Period
    from finance_engine.sources.quickbooks import snapshot_header
    head = snapshot_header(info.path)
    first, last = head["start"], head["end"]
    if info.periods:                              # the months actually read win
        first = _date_of(Period.from_key(info.periods[0]))
        last = _date_of(Period.from_key(info.periods[-1]))
    if first and last:
        months = (_month_label(first) if (first.year, first.month) == (last.year, last.month)
                  else f"{_month_label(first)} to {_month_label(last)}")
    else:
        months = "months not stated in the file"
    pulled = head["pulled_at"]
    when = pulled.astimezone() if pulled else None
    pulled_text = (f"Pulled {when.day} {when:%b} {when.year}, {when:%H:%M}" if when
                   else "Pull time unknown")
    entry = {"label": f"{pulled_text} – {months}", "months_label": months,
             "pulled_at_utc": (pulled.astimezone(dt.timezone.utc).replace(microsecond=0)
                               .isoformat() if pulled else None),
             "readable": not info.error}
    if info.error:
        entry["reason"] = one_line_reason(info.error, info.filename)
    return entry


def _date_of(period):
    return dt.date(period.year, period.month, 1)


def one_line_reason(error: str, filename: str = "") -> str:
    """The first sentence of an engine message, without the file and sheet prefix."""
    text = " ".join(str(error).split())
    for prefix in (f"{filename}: " if filename else None, "ProfitAndLoss: "):
        if prefix and text.startswith(prefix):
            text = text[len(prefix):]
    sentence = text.split(". ")[0].rstrip(".")
    return sentence if len(sentence) <= 140 else sentence[:137] + "..."


def list_periods(root: Path, client_id: str, file: str | None = None) -> dict:
    """The months in one file (the default file when `file` is None), newest first, as
    `list-periods --json` gives them."""
    from finance_engine.model import trailing_window
    from finance_engine.sources.factory import open_source
    from finance_engine.sources.selection import SourceSelectionError, choose
    from finance_engine.model import LayoutError
    config, settings = _config(root, client_id)
    if file:
        commands.check_file_name(file)
    try:
        choice = choose(config, settings, root, file, None)
        keys = open_source(choice.path, config).load(config).period_keys()
    except (SourceSelectionError, FileNotFoundError, LayoutError) as exc:
        raise ServiceError(str(exc)) from None
    rows = []
    for key in reversed(keys):
        window = trailing_window(keys, key, config.ttm_months)
        rows.append({"period": key, "ttm_complete": all(p in set(keys) for p in window),
                     "ttm_window": [window[0], window[-1]]})
    return {"client_id": client_id, "file": choice.path.name, "selection_mode": choice.mode,
            "periods": rows, "years": sorted({k[:4] for k in keys}, reverse=True)}


# ---- one report ---------------------------------------------------------------------

def report_dir(root: Path, client_id: str, scope: str = "month",
               period: str | None = None, year=None, file: str | None = None) -> Path:
    """The folder `report` writes for these arguments (as cli._output_dir builds it)."""
    commands.check_client(client_id)
    commands.scope_pairs(scope, period, year, file)            # validates every value
    settings = _settings(root)
    leaf = {"month": period, "year": f"year-{commands.check_year(year) if year else 0:04d}",
            "all": "all"}[scope]
    base = settings.output_dir / client_id
    return (base / Path(file).stem / leaf) if file else (base / leaf)


_STATUS_ROW = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")


def status_rows(report_md: str) -> list[dict]:
    """The "Status at a glance" table of report.md, as the engine wrote it."""
    rows, inside = [], False
    for line in report_md.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Status at a glance"
            continue
        m = _STATUS_ROW.match(line) if inside else None
        if not m or m.group(1) in ("Check",) or set(m.group(1)) <= {"-", " "}:
            continue
        rows.append({"check": m.group(1), "result": m.group(2).replace("**", ""),
                     "detail": m.group(3).replace("**", "")})
    return rows


def cell_warnings(doc: dict) -> list[dict]:
    """Every data-quality issue that names a cell, one row per cell."""
    out = []
    for issue in doc.get("data_quality_issues") or []:
        for cell in issue.get("cells") or []:
            out.append({"code": issue.get("code"), "severity": issue.get("severity"),
                        "sheet": cell.get("sheet"), "cell": cell.get("cell"),
                        "label": cell.get("label"), "period": cell.get("period"),
                        "raw_value": cell.get("raw_value"), "message": issue.get("message"),
                        "affects": issue.get("affects") or []})
    return out


_CHANGE = (".mom_abs", ".mom_pct", ".mom_pts", ".yoy_abs", ".yoy_pct", ".yoy_pts",
           ".start_to_end_abs", ".start_to_end_pct", ".start_to_end_pts")


def display(value, unit: str, name: str = "", symbol: str = "$") -> str:
    """A metric value as the engine's formatting writes it. Python formats every figure
    the UI shows; the browser only places these strings."""
    from finance_engine import formatting as f
    if value is None:
        return f.NA
    change = name.endswith(_CHANGE)
    if unit == "currency":
        return f.signed_money(value, symbol) if change else f.money(value, symbol)
    if unit == "percent":
        return f.signed_percent(value) if change else f.percent(value)
    if unit == "percentage_points":
        return f.points(value)
    return f"{value:,g}"


def metric_display(doc: dict) -> dict:
    """name -> {display, reason}: the value as text, and why it is n/a when it is."""
    from finance_engine.formatting import reason
    symbol = doc.get("currency_symbol", "$")
    out = {}
    for m in doc.get("metrics", []):
        out[m["name"]] = {
            "display": display(m.get("value"), m.get("unit"), m["name"], symbol),
            "reported_display": (display(m["reported_value"], m.get("unit"), "", symbol)
                                 if m.get("reported_value") is not None else None),
            "reason": reason(m.get("unavailable_reason")) if m.get("value") is None
            else ""}
    return out


def failures_display(doc: dict) -> list[dict]:
    """The reconciliation failures with their amounts as the engine formats them."""
    from finance_engine.formatting import money, signed_money
    symbol = doc.get("currency_symbol", "$")
    return [{**f, "computed_display": money(f.get("computed"), symbol),
             "reported_display": money(f.get("reported"), symbol),
             "difference_display": signed_money(f.get("difference"), symbol)}
            for f in (doc.get("reconciliation") or {}).get("failures") or []]


def get_report(root: Path, client_id: str, scope: str = "month",
               period: str | None = None, year=None, file: str | None = None) -> dict:
    from finance_engine.brief import build_brief
    from finance_engine.formatting import check_count, not_provided_note

    folder = report_dir(root, client_id, scope, period, year, file)
    metrics_path = folder / "metrics.json"
    if not metrics_path.exists():
        what = commands.finance("report", ("--client", client_id),
                                *commands.scope_pairs(scope, period, year, file))[1]
        raise ServiceError(f"No report yet for these settings ({_rel(folder, root)}). "
                           f"Run: {what}")
    doc = json.loads(metrics_path.read_text(encoding="utf-8"))
    report_path = folder / "report.md"
    report_md = report_path.read_text(encoding="utf-8") if report_path.exists() else None
    rows = status_rows(report_md) if report_md else []
    number_check = next((r for r in rows if r["check"] == "Commentary number check"), None)
    provider = review.provider_of(folder)
    log_path = folder / "commentary_log.json"
    commentary_log = None
    if log_path.exists() and provider.get("data_left_machine") is not None \
            and report_md and "_Commentary written by the `" in report_md:
        commentary_log = json.loads(log_path.read_text(encoding="utf-8"))
    recon = doc.get("reconciliation") or {}
    st = review.status(folder) if report_md else None
    src = doc.get("source") or {}
    return {
        "client_id": doc.get("client_id"),
        "company_name": doc.get("company_name"),
        "scope": doc.get("scope", "month"),
        "period": doc.get("period"),
        "period_label": doc.get("period_label"),
        "folder": _rel(folder, root),
        "files": {"metrics": _rel(metrics_path, root),
                  "report": _rel(report_path, root) if report_md else None,
                  "commentary_log": _rel(log_path, root) if commentary_log else None},
        "source": {"filename": src.get("filename"), "path": src.get("path"),
                   "type": src.get("type"), "sha256": src.get("sha256"),
                   "selection": src.get("selection")},
        "badges": {
            "reconciliation": {"all_passed": recon.get("all_passed"),
                               "checks_run": recon.get("checks_run"),
                               "checks_failed": recon.get("checks_failed"),
                               "label": check_count(recon) + not_provided_note(doc)},
            "number_check": number_check,
            "provider": provider,
            "scope_label": doc.get("period_label"),
            "source_sha256": src.get("sha256"),
            "review": st.label if st else "no report.md yet",
        },
        "status_rows": rows,
        "metrics_doc": doc,
        "metric_display": metric_display(doc),
        "prose_links": prose_links(report_md, doc) if report_md else [],
        "cell_warnings": cell_warnings(doc),
        "reconciliation_failures": failures_display(doc),
        "brief": build_brief(doc),
        "report_md": report_md,
        "commentary_log": commentary_log,
        "review": ({**st.to_dict(), "versions": review.versions(folder),
                    "log": review.read_log(folder)} if st else None),
    }


# ---- review -------------------------------------------------------------------------

def review_action(root: Path, client_id: str, action: str, by: str,
                  comment: str | None = None, scope: str = "month",
                  period: str | None = None, year=None, file: str | None = None) -> dict:
    folder = report_dir(root, client_id, scope, period, year, file)
    try:
        entry = review.act(folder, action, by, comment,
                           settings=review.load_settings(root))
    except review.ReviewError as exc:
        raise ServiceError(str(exc)) from None
    return {"entry": entry, "status": review.status(folder).to_dict()}


def review_status(root: Path, client_id: str, scope: str = "month",
                  period: str | None = None, year=None, file: str | None = None) -> dict:
    folder = report_dir(root, client_id, scope, period, year, file)
    try:
        st = review.status(folder)
        log = review.read_log(folder)
    except review.ReviewError as exc:
        raise ServiceError(str(exc)) from None
    writer = review.provider_of(folder)
    log = [{**e, "action_label": review.ACTION_LABELS.get(e.get("action"), e.get("action"))}
           for e in log]
    return {"folder": _rel(folder, root), **st.to_dict(), "log": log,
            "versions": review.versions(folder), "writer": writer,
            # The template cannot act on a send-back comment; a cloud writer gets it as
            # reviewer notes when the report is regenerated without "Keep data local".
            "writer_follows_comments": writer.get("provider") not in (None, "deterministic"),
            "submitted_by_person": review.human_submitted(log),
            "require_different_reviewer":
                review.load_settings(root)["require_different_reviewer"]}


def review_queue(root: Path) -> dict:
    """Everything Ready for review, with the scope, period and file to reopen each report,
    and how many there are (the top bar shows the count)."""
    out_dir = _settings(root).output_dir
    entries = []
    for q in review.queue(out_dir):
        leaf = q["leaf"]
        where = ({"scope": "all"} if leaf == "all" else
                 {"scope": "year", "year": leaf[5:]} if leaf.startswith("year-") else
                 {"scope": "month", "period": leaf})
        file = None
        if q["file_key"]:
            doc = json.loads((out_dir / q["folder"] / "metrics.json")
                             .read_text(encoding="utf-8"))
            file = (doc.get("source") or {}).get("filename")
        entries.append({**q, **where, "file": file})
    return {"ready_for_review": entries, "count": len(entries)}


# ---- onboarding ---------------------------------------------------------------------

def draft_client(root: Path, workbook: Path, client_id: str, name: str,
                 force: bool = False) -> dict:
    from finance_engine.init_client import InitError
    try:
        return onboarding.init_draft(workbook, client_id, name, Path(root),
                                     _settings(root).data_dir, force=force)
    except (onboarding.OnboardingError, InitError) as exc:
        raise ServiceError(str(exc)) from None


def draft_config_text(root: Path, client_id: str) -> dict:
    commands.check_client(client_id)
    settings = _settings(root)
    path = onboarding.draft_config_path(settings.data_dir, client_id)
    if not path.exists():
        raise ServiceError(f"No draft for {client_id}. Upload a workbook first.")
    summary = path.with_name("draft_summary.json")
    return {"client_id": client_id, "config_path": _rel(path, root),
            "yaml": path.read_text(encoding="utf-8"),
            "summary": json.loads(summary.read_text(encoding="utf-8"))
            if summary.exists() else None}


def validate_config(root: Path, client_id: str, draft: bool = True,
                    from_text: str | None = None) -> dict:
    try:
        return onboarding.validate_config(client_id, Path(root), _settings(root),
                                          draft=draft, from_text=from_text)
    except onboarding.OnboardingError as exc:
        raise ServiceError(str(exc)) from None


def approve_config(root: Path, client_id: str, by: str) -> dict:
    try:
        return onboarding.approve_config(client_id, by, Path(root), _settings(root))
    except onboarding.OnboardingError as exc:
        raise ServiceError(str(exc)) from None


# ---- providers and QuickBooks: names and yes/no only ---------------------------------

def provider_status(root: Path) -> dict:
    """The commentary chain from config/providers.yaml. Never a key value."""
    from finance_engine.commentary.providers import ProvidersConfig, _importable
    from finance_engine.credentials import EnvCredentialStore

    path = Path(root) / "config" / "providers.yaml"
    if not path.exists():
        return {"config": None, "order": ["deterministic"], "allow_cloud": False,
                "providers": [], "note": "config/providers.yaml not found"}
    config = ProvidersConfig.load(path)
    store = EnvCredentialStore(config.key_names(), env_file=Path(root) / ".env")
    packages = {"gemini": "google.genai", "claude-api": "anthropic"}
    rows = []
    for name in config.order:
        if name == "deterministic":
            rows.append({"provider": name, "cloud": False, "enabled": True,
                         "configured": True, "missing_variables": [],
                         "package_installed": True})
            continue
        s = config.providers.get(name)
        if s is None:
            rows.append({"provider": name, "configured": False, "enabled": False,
                         "cloud": True, "missing_variables": [], "package_installed": None})
            continue
        key_name = getattr(s, "api_key_env", None)
        has_key = bool(key_name) and store.present(key_name)
        installed = _importable(packages.get(name, name))
        rows.append({"provider": name, "cloud": bool(getattr(s, "cloud", True)),
                     "enabled": bool(s.enabled), "model": s.model,
                     "configured": bool(s.enabled and has_key and installed
                                        and config.allow_cloud),
                     "missing_variables": [] if has_key or not key_name else [key_name],
                     "package_installed": installed})
    return {"config": _rel(path, root), "order": list(config.order),
            "allow_cloud": config.allow_cloud, "providers": rows,
            "what_leaves": "Only the compact brief (Python-formatted figures from "
                           "metrics.json) is sent to a cloud provider. The workbook and "
                           "metrics.json never leave this machine.",
            "keep_local_equivalent": "--provider deterministic"}


def quickbooks_status(root: Path) -> dict:
    from finance_engine.credentials import EnvCredentialStore
    from finance_engine.sources.quickbooks_api import (DEFAULT_TOKEN_STORE, ENV_NAMES,
                                                       SANDBOX_HOST)
    store = EnvCredentialStore(list(ENV_NAMES), env_file=Path(root) / ".env")
    token_file = (Path(root) / DEFAULT_TOKEN_STORE).exists()
    required = [n for n in ENV_NAMES if n != "QBO_ENVIRONMENT"]
    # The reader's own token file replaces QBO_REFRESH_TOKEN once it exists.
    missing = [n for n in required if not store.present(n)
               and not (n == "QBO_REFRESH_TOKEN" and token_file)]
    environment = "sandbox" if not store.present("QBO_ENVIRONMENT") else (
        "sandbox" if store.get("QBO_ENVIRONMENT").strip().lower() == "sandbox"
        else "not sandbox (refused)")
    settings = _settings(root)
    clients = []
    for path in sorted(settings.config_dir.glob("*.yaml")):
        try:
            cfg = ClientConfig.load_path(path)
        except ConfigError:
            continue
        if cfg.source_type == "quickbooks":
            clients.append(cfg.client_id)
    return {"configured": not missing and environment == "sandbox",
            "missing_variables": missing, "environment": environment,
            "sandbox_only": True, "host": SANDBOX_HOST, "read_only": True,
            "token_store_present": token_file, "clients": clients}


# ---- long actions, as background jobs --------------------------------------------------

def secret_names(root: Path) -> list[str]:
    """Every variable NAME whose value must never appear in a response or a job log."""
    from finance_engine.commentary.providers import ProvidersConfig
    from finance_engine.sources.quickbooks_api import ENV_NAMES
    path = Path(root) / "config" / "providers.yaml"
    names = ProvidersConfig.load(path).key_names() if path.exists() else []
    # QBO_ENVIRONMENT is "sandbox", not a secret: redacting it would blank every path
    # that contains the word.
    return list(dict.fromkeys([*names, "GEMINI_API_KEY", "ANTHROPIC_API_KEY",
                               *(n for n in ENV_NAMES if n != "QBO_ENVIRONMENT")]))


def make_redactor(root: Path):
    """A function that removes every known secret value (and secret-shaped text)."""
    from finance_engine.credentials import EnvCredentialStore, redact
    store = EnvCredentialStore(secret_names(root), env_file=Path(root) / ".env")
    values = [v for v in (store.get(n) for n in store.allowed) if v]
    return lambda text: redact(text, values)


def cloud_notice(root: Path, keep_data_local: bool, client_id: str | None = None,
                 scope: str = "month", period: str | None = None, year=None,
                 file: str | None = None) -> dict:
    """What leaves this machine for a report run, said before the run.

    With a report named, it also says whether the last send-back comment would go to the
    writer as reviewer notes (it does on a cloud run; "Keep data on this machine" avoids
    it, because the template takes no notes).
    """
    notes = None
    if client_id:
        try:
            notes = review.last_send_back(report_dir(root, client_id, scope, period,
                                                     year, file))
        except (review.ReviewError, CommandError):
            notes = None
    notes_line = ("The last send-back comment"
                  + (f" (by {notes['name']})" if notes else "")
                  + " is sent too, as reviewer notes for the writer."
                  if notes else
                  "If the last review action on this report was a send-back, its comment "
                  "is sent too, as reviewer notes for the writer.")
    if keep_data_local:
        return {"keep_data_local": True, "provider_flag": "--provider deterministic",
                "leaves_machine": False, "reviewer_notes_pending": bool(notes),
                "reviewer_notes_sent": False,
                "text": "Nothing leaves this machine: the deterministic template writes "
                        "the commentary. Reviewer notes are not sent (the template does "
                        "not take them; they stay beside the draft in the review log)."}
    status = provider_status(root)
    could = [p["provider"] for p in status["providers"]
             if p["cloud"] and p["configured"]]
    return {"keep_data_local": False, "provider_flag": "--provider auto",
            "leaves_machine": bool(could), "cloud_providers_that_may_run": could,
            "reviewer_notes_pending": bool(notes),
            "reviewer_notes_sent": bool(could and notes),
            "text": ("If a cloud provider runs, the compact brief is sent "
                     f"(to: {', '.join(could)}). {notes_line} The workbook and "
                     "metrics.json never leave this machine. Tick \"Keep data on this "
                     "machine\" to send nothing." if could else
                     "No cloud provider is configured, so the deterministic template "
                     "will write the commentary and nothing leaves this machine.")}


# ---- charts: read, never computed ------------------------------------------------------

#: The line-chart measures, in the order the whole-file series lists them.
CHART_MEASURES = ("revenue.total", "gross_profit", "operating_income", "gross_margin_pct",
                  "operating_margin_pct")


def chart_data(root: Path, client_id: str, file: str | None = None, year=None,
               scope: str | None = None, period: str | None = None) -> dict:
    """The charts for one report, read from the engine's own files. Nothing is computed.

    * No `scope` (or "all"): monthly lines from the whole-file metrics.json (`series`),
      optionally only one calendar year of it (`year`).
    * "year": monthly lines from that year's own metrics.json (`monthly.<YYYY-MM>.*`);
      if the year report has not been run, the whole-file one for that year is used.
    * "month": bar charts from that month's own metrics.json: the month against the prior
      month (`<measure>` and `<measure>.prior`), and operating expenses by category.

    Net income by month is in neither the whole-file nor the year file, so it is READ from
    each month's own metrics.json (`net_income.reported`) when one exists for the same
    source file (same SHA-256); a month without one is a gap marked "not analysed".
    Months missing from the source are gaps (never zero). Every value comes with the
    engine's display string; nothing is summed, averaged, annualised or rounded here.
    """
    commands.check_client(client_id)
    if file:
        commands.check_file_name(file)
    if scope not in (None, "all", "month", "year"):
        raise ServiceError(f"scope {scope!r}: use month, year or all.")
    if scope == "month":
        return _month_charts(root, client_id, period, file)
    if scope == "year":
        if year is None:
            raise ServiceError("Charts for a year need the year, e.g. year=2025.")
        folder = report_dir(root, client_id, "year", year=year, file=file)
        if (folder / "metrics.json").exists():
            return _year_charts(root, client_id, folder, year, file)
        if not (report_dir(root, client_id, "all", file=file) / "metrics.json").exists():
            what = commands.finance("report", ("--client", client_id),
                                    *commands.scope_pairs("year", None, year, file))[1]
            raise ServiceError(f"Charts for a year read that year's report, which has not "
                               f"been run for these settings. Run: {what}")
    folder = report_dir(root, client_id, "all", file=file)
    path = folder / "metrics.json"
    if not path.exists():
        what = commands.finance("report", ("--client", client_id), ("--scope", "all"),
                                *([("--file", file)] if file else []))[1]
        raise ServiceError(f"Charts read the whole-file report, which has not been run "
                           f"for these settings. Run: {what}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    symbol = doc.get("currency_symbol", "$")
    cov = doc.get("coverage") or {}
    gaps = list(cov.get("gaps") or [])
    periods = sorted(set(doc["series"]["periods"]) | set(gaps))   # calendar order
    label, year_status = doc.get("period_label"), None
    if year is not None:
        y = f"{commands.check_year(year):04d}-"
        periods = [p for p in periods if p.startswith(y)]
        entry = next((e for e in doc.get("years") or [] if f"{e.get('year')}-" == y), None)
        year_status = entry.get("status") if entry else "absent"
        label = (f"{year}: {entry.get('label')}" if entry
                 else f"{year}: no months in this file")
    failed = _failed_periods(doc)

    series = []
    for m in doc["series"]["measures"]:
        if m["measure"] not in CHART_MEASURES:
            continue
        by_period = {pt["period"]: pt for pt in m["points"]}
        points = []
        for p in periods:
            pt = by_period.get(p)
            points.append(_line_point(
                p, pt["value"] if pt else None, (pt or {}).get("unavailable_reason"),
                f"monthly.{p}.{m['measure']}", m["unit"], symbol, failed,
                missing=pt is None))
        series.append({"measure": m["measure"], "label": m["label"], "unit": m["unit"],
                       "source": "whole-file metrics.json (series)", "points": points})
    return _line_result(root, client_id, file, year, "year" if year is not None else "all",
                        label, periods, gaps, failed, year_status, symbol, doc, series)


def _failed_periods(doc: dict) -> list[str]:
    return sorted({f["period"] for f in (doc.get("reconciliation") or {})
                   .get("failures") or [] if f.get("period")})


def _line_point(period, value, unavailable, metric, unit, symbol, failed, missing=False):
    """One point of a line chart, with the engine's display string and its status."""
    from finance_engine.formatting import reason
    if missing:
        return {"period": period, "value": None, "metric": None,
                "status": "gap: month missing from the source",
                "reconciliation_failed": period in failed, "display": "n/a"}
    return {"period": period, "value": value, "metric": metric,
            "status": "ok" if value is not None else f"n/a: {reason(unavailable)}",
            "reconciliation_failed": period in failed,
            "display": display(value, unit, "", symbol)}


def _year_charts(root: Path, client_id: str, folder: Path, year, file) -> dict:
    """Monthly lines from a year report's own metrics.json (`monthly.<YYYY-MM>.*`)."""
    from finance_engine.monthly import LABELS
    doc = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    symbol = doc.get("currency_symbol", "$")
    cov = doc.get("coverage") or {}
    gaps = list(cov.get("gaps_inside_span") or [])
    periods = sorted(set(cov.get("months_present") or []) | set(gaps))
    failed = _failed_periods(doc)
    by_name = {m["name"]: m for m in doc.get("metrics", [])}
    series = []
    for measure in CHART_MEASURES:
        unit = "percent" if measure.endswith("_pct") else "currency"
        points = []
        for p in periods:
            m = by_name.get(f"monthly.{p}.{measure}")
            points.append(_line_point(
                p, m["value"] if m else None, (m or {}).get("unavailable_reason"),
                f"monthly.{p}.{measure}", unit, symbol, failed, missing=m is None))
        series.append({"measure": measure, "label": LABELS[measure], "unit": unit,
                       "source": f"the {year} report's metrics.json (monthly.<YYYY-MM>.*)",
                       "points": points})
    label = f"{year}: {cov.get('label') or 'no months in this file'}"
    return _line_result(root, client_id, file, year, "year", label, periods, gaps, failed,
                        cov.get("status"), symbol, doc, series)


def _line_result(root, client_id, file, year, scope, label, periods, gaps, failed,
                 year_status, symbol, doc, series) -> dict:
    sha = (doc.get("source") or {}).get("sha256")
    net = _net_income_points(root, client_id, file, periods, gaps, sha, failed, symbol)
    series.append({"measure": "net_income.reported", "label": "Net income (as reported)",
                   "unit": "currency",
                   "source": "each month's own metrics.json (net_income.reported)",
                   "points": net})
    missing_months = [pt["period"] for pt in net
                      if pt["status"].startswith("not analysed")]
    return {"client_id": client_id, "file": file, "year": year, "scope": scope,
            "kind": "line", "label": label,
            "periods": periods, "gaps": [p for p in gaps if p in periods],
            "reconciliation_failed": [p for p in failed if p in periods],
            "year_status": year_status, "partial_year": year_status == "partial",
            "never_annualised": True, "currency_symbol": symbol,
            "source_sha256": sha, "series": series,
            "net_income_months_not_analysed": missing_months}


def _net_income_points(root, client_id, file, periods, gaps, sha, failed, symbol) -> list:
    from finance_engine.formatting import reason
    net, base = [], report_dir(root, client_id, "all", file=file).parent
    for p in periods:
        month = base / p / "metrics.json"
        value, text, name = None, "not analysed: no month report for this source", None
        if p in gaps:
            text = "gap: month missing from the source"
        elif month.exists():
            mdoc = json.loads(month.read_text(encoding="utf-8"))
            if (mdoc.get("source") or {}).get("sha256") != sha:
                text = "not used: that month report read a different source file"
            else:
                m = next((x for x in mdoc.get("metrics", [])
                          if x["name"] == "net_income.reported"), None)
                if m is None:
                    text = "n/a: the source has no reported net income line"
                else:
                    value, name = m.get("value"), "net_income.reported"
                    text = ("ok" if value is not None else
                            f"n/a: {reason(m.get('unavailable_reason'))}")
        net.append({"period": p, "value": value, "metric": name, "status": text,
                    "reconciliation_failed": p in failed,
                    "display": display(value, "currency", "", symbol)})
    return net


_CATEGORY = re.compile(r"opex\.category\.[a-z0-9_]+")


def _prior_label(period: str | None) -> str:
    """'2025-12' -> 'Dec 2025' (text only)."""
    if not period:
        return "prior month"
    return _month_label(dt.date(int(period[:4]), int(period[5:7]), 1))


def _month_charts(root: Path, client_id: str, period, file) -> dict:
    """Bar charts from one month report's own metrics.json. Values are copied, never
    derived: the prior month's figures are the engine's `<measure>.prior` metrics."""
    from finance_engine.formatting import reason
    from finance_engine.monthly import HEADLINE, LABELS
    if not period or period == "latest":
        raise ServiceError("Charts for a month need the month as YYYY-MM.")
    commands.check_period(period)
    path = report_dir(root, client_id, "month", period=period, file=file) / "metrics.json"
    if not path.exists():
        what = commands.finance("report", ("--client", client_id),
                                *commands.scope_pairs("month", period, None, file))[1]
        raise ServiceError(f"Charts for a month read that month's report, which has not "
                           f"been run for these settings. Run: {what}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    symbol = doc.get("currency_symbol", "$")
    by_name = {m["name"]: m for m in doc.get("metrics", [])}
    prior = doc.get("prior_period")
    failed = _failed_periods(doc)

    def bar(name: str, unit: str = "currency") -> dict:
        m = by_name.get(name)
        if m is None:
            return {"value": None, "display": "n/a", "metric": None,
                    "status": "n/a: not in this month's metrics.json"}
        value = m.get("value")
        return {"value": value, "display": display(value, unit, "", symbol), "metric": name,
                "status": ("ok" if value is not None else
                           f"n/a: {reason(m.get('unavailable_reason'))}")}

    this_label, prior_label = doc.get("period_label") or period, _prior_label(prior)
    compare = {
        "id": "month_vs_prior", "kind": "bar",
        "title": f"{this_label} against {prior_label}",
        "categories": [LABELS[k] for k in HEADLINE],
        "series": [{"label": this_label, "unit": "currency",
                    "points": [bar(k) for k in HEADLINE]},
                   {"label": prior_label, "unit": "currency",
                    "points": [bar(f"{k}.prior") for k in HEADLINE]}],
        "source": "this month's metrics.json (<measure> and <measure>.prior)",
    }
    names = [m["name"] for m in doc.get("metrics", []) if _CATEGORY.fullmatch(m["name"])]
    points = []
    for name in names:
        pt, share = bar(name), bar(f"{name}.share_of_opex_pct", "percent")
        if share["value"] is not None:
            pt["detail"] = f"{share['display']} of operating expenses"
        points.append(pt)
    charts = [compare]
    if names:
        charts.append({
            "id": "opex_by_category", "kind": "bar", "horizontal": True,
            "title": f"Operating expenses by category, {this_label}",
            "categories": [by_name[n].get("label") or n for n in names],
            "series": [{"label": this_label, "unit": "currency", "points": points}],
            "source": "this month's metrics.json (opex.category.*)",
        })
    return {"client_id": client_id, "file": file, "scope": "month", "kind": "bar",
            "period": period, "prior_period": prior, "label": this_label,
            "currency_symbol": symbol, "never_annualised": True,
            "source_sha256": (doc.get("source") or {}).get("sha256"),
            "reconciliation_failed": [p for p in failed if p in (period, prior)],
            "charts": charts}


def start_analyze_months(jobs, root: Path, client_id: str, file: str | None = None):
    """`analyze` for each month of the file that has no month metrics.json yet, so the
    net income chart can read them. Existing month files are never rewritten (that would
    change their hashes and void a review decision)."""
    data = chart_data(root, client_id, file)
    todo = data["net_income_months_not_analysed"]
    if not todo:
        raise ServiceError("Every month already has a month report for this source.")
    pairs = [commands.report_command(client_id, "month", p, None, file, True, "analyze")
             for p in todo]
    return jobs.start_commands(client_id, f"analyze {len(todo)} month(s) {client_id}",
                               [a for a, _ in pairs], [s for _, s in pairs], timeout=1800)


def review_version(root: Path, client_id: str, name: str, scope: str = "month",
                   period: str | None = None, year=None, file: str | None = None) -> dict:
    """One stored copy of report.md from review/versions/, by its listed name."""
    folder = report_dir(root, client_id, scope, period, year, file)
    if name not in review.versions(folder):
        raise ServiceError(f"No stored version {name!r} for this report.")
    path = review.review_dir(folder) / "versions" / name
    return {"name": name, "path": _rel(path, root),
            "text": path.read_text(encoding="utf-8")}


def start_report(jobs, root: Path, client_id: str, scope: str = "month",
                 period: str | None = None, year=None, file: str | None = None,
                 keep_data_local: bool = True, command: str = "report"):
    argv, shown = commands.report_command(client_id, scope, period, year, file,
                                          keep_data_local, command)
    _config(root, client_id)                       # an unknown client fails here, fast
    return jobs.start_commands(client_id, f"{command} {client_id}", [argv], [shown])


def start_free_evals(jobs, root: Path):
    pairs = commands.free_eval_commands()
    return jobs.start_commands("evals", "free evals", [a for a, _ in pairs],
                               [s for _, s in pairs], timeout=1800)


def start_quickbooks_pull(jobs, root: Path, client_id: str, start: str, end: str,
                          transport=None):
    """The same code as `pull-quickbooks`, run in the server process so tests can hand
    it a fake transport. Read-only and sandbox-only, as the reader enforces."""
    from finance_engine.sources.quickbooks_api import pull_snapshot
    _, shown = commands.pull_command(client_id, start, end)
    config, settings = _config(root, client_id)
    if config.source_type != "quickbooks":
        raise ServiceError(f"{client_id} is not a QuickBooks client.")

    def run(log):
        log(f"pulling the sandbox Profit and Loss {start}..{end} (read-only)")
        result = pull_snapshot(config, Path(root), settings.data_dir, start, end,
                               transport=transport)
        log(f"saved {result['filename']}")
        return {"filename": result["filename"], "sha256": result["sha256"],
                "first_period": result["first_period"],
                "last_period": result["last_period"],
                "period_count": result["period_count"],
                "snapshot": _rel(result["path"], root)}

    return jobs.start_callable(client_id, f"pull-quickbooks {client_id}", run, [shown],
                               timeout=120)


# ---- evals ---------------------------------------------------------------------------

def list_evals(root: Path) -> dict:
    """The golden cases and their result in the newest saved baselines (files only)."""
    cases_dir = Path(root) / "evals" / "cases"
    base_dir = Path(root) / "evals" / "baseline"

    def newest(label: str) -> tuple[str | None, dict]:
        if not base_dir.exists():
            return None, {}
        found = sorted(p for p in base_dir.glob(f"{label}_*.json")
                       if not (label == "skill" and p.name.startswith("skill_deterministic")))
        if not found:
            return None, {}
        return found[-1].name, json.loads(found[-1].read_text(encoding="utf-8"))

    baselines = {k: newest(k) for k in ("engine", "skill_deterministic", "skill")}
    cases = []
    case_dirs = sorted(p for p in cases_dir.iterdir() if (p / "case.yaml").exists()) \
        if cases_dir.exists() else []
    for d in case_dirs:
        case = yaml.safe_load((d / "case.yaml").read_text(encoding="utf-8")) or {}
        row = {"id": d.name, "title": case.get("title"), "synthetic": case.get("synthetic"),
               "client_id": (case.get("input") or {}).get("client_id")}
        for label, (_, doc) in baselines.items():
            row[label] = ((doc.get("cases") or {}).get(d.name) or {}).get("status")
        cases.append(row)
    return {"cases": cases,
            "baselines": {k: v[0] for k, v in baselines.items()},
            "free_commands": [c[1] for c in commands.free_eval_commands()],
            "paid_command": commands.PAID_EVAL_COMMAND,
            "paid_note": "Uses Claude Pro usage; run it yourself in a terminal. The UI "
                         "has no button for it."}


__all__ = ["ServiceError", "CommandError", "list_clients", "list_files", "list_periods",
           "report_dir", "get_report", "review_action", "review_status", "review_queue",
           "draft_client", "draft_config_text", "validate_config", "approve_config",
           "provider_status", "quickbooks_status", "list_evals"]
