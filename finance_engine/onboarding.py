"""A new company in two steps, so a person approves its config before it goes live.

    python -m finance_engine init-client --draft --file book.xlsx --client-id acme_ltd --name "Acme Ltd"
    python -m finance_engine validate-config --client acme_ltd --draft [--from edited.yaml]
    python -m finance_engine approve-config  --client acme_ltd --by "A. Reviewer"

1. `init-client --draft` inspects the workbook exactly as `init-client` does, copies it to
   `data/clients/<id>/source/`, and writes `data/clients/<id>/draft/config.yaml` and
   `draft_summary.json` (detected sheet and rows, the check results, warnings). Nothing
   under `config/clients/` is touched, so the engine cannot load the draft yet.
2. `validate-config` loads a config with the real loader, reads the source with it, and
   analyses the latest month with its reconciliation. Errors carry a YAML line number
   when one can be found. `--from FILE` first replaces the draft with an edited copy.
3. `approve-config` re-validates the draft and, only if it passes, copies it byte for byte
   to `config/clients/<id>.yaml` and writes `data/clients/<id>/config_approval.json` (who,
   when, the SHA-256 of the YAML). A draft that failed validation and has not changed
   since is refused without re-running anything.

Plain `init-client` (without `--draft`) keeps its own behaviour. Names are self-declared;
there is no sign-in ([PLANNED] for hosting).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

import yaml

from finance_engine.init_client import (InitError, _comment_block, _rel, copy_workbook,
                                        detect)

#: Client ids for everything created through the review workflow and the UI.
CLIENT_ID = re.compile(r"[a-z0-9_]{2,40}")
_KEY_PATH = re.compile(r"\b([a-z_]+(?:\.[a-z_]+)+)\b")


class OnboardingError(Exception):
    """A refused onboarding step. Nothing was written."""


def check_client_id(client_id: str) -> str:
    if not isinstance(client_id, str) or not CLIENT_ID.fullmatch(client_id):
        raise OnboardingError(f"client id {client_id!r}: use 2 to 40 lowercase letters, "
                              f"digits and underscores (^[a-z0-9_]{{2,40}}$).")
    return client_id


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _write_json(path: Path, doc: dict) -> None:
    _write_atomic(path, json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8"))


def draft_dir(data_dir: Path, client_id: str) -> Path:
    return Path(data_dir) / client_id / "draft"


def draft_config_path(data_dir: Path, client_id: str) -> Path:
    return draft_dir(data_dir, client_id) / "config.yaml"


def approval_path(data_dir: Path, client_id: str) -> Path:
    return Path(data_dir) / client_id / "config_approval.json"


# ---- step 1: draft ------------------------------------------------------------------

def init_draft(source: Path, client_id: str, company_name: str, root: Path,
               data_dir: Path, force: bool = False) -> dict:
    """Detect, copy the workbook, validate, write the draft and its summary."""
    from finance_engine.init_client import _validate

    check_client_id(client_id)
    if not str(company_name or "").strip():
        raise OnboardingError("--name must not be empty.")
    cfg_path = draft_config_path(data_dir, client_id)
    if cfg_path.exists() and not force:
        raise OnboardingError(f"{cfg_path} already exists. Re-run with --force to replace "
                              f"the draft; nothing was changed.")
    draft = detect(Path(source), client_id, company_name.strip())   # raises before writes
    dest = copy_workbook(Path(source), data_dir, client_id)
    problems, validation = (draft.problems, {}) if draft.problems else \
        _validate(draft.config, dest, root)
    draft.problems = list(problems)
    ok = not draft.problems
    body = yaml.safe_dump(draft.config, sort_keys=False, allow_unicode=True)
    header = (f"# DRAFT, not live. Approve with: python -m finance_engine approve-config "
              f"--client {client_id} --by \"<your name>\"\n")
    text = header + _comment_block(draft, Path(source), "config" if ok else "draft",
                                   validation) + body
    _write_atomic(cfg_path, text.encode("utf-8"))
    sheet = draft.config["sheets"]["income_statement"]
    summary = {
        "client_id": client_id,
        "company_name": company_name.strip(),
        "drafted_at_utc": _now(),
        "status": "valid" if ok else "invalid",
        "config_path": _rel(cfg_path, root),
        "config_sha256": _sha_bytes(cfg_path.read_bytes()),
        "workbook": _rel(dest, root),
        "workbook_sha256": _sha_bytes(dest.read_bytes()),
        "sheet": sheet["name"],
        "header_row": sheet["header_row"],
        "first_data_column": sheet["first_data_column"],
        "last_data_column": sheet.get("last_data_column"),
        "periods": {"first": draft.periods[0] if draft.periods else None,
                    "last": draft.periods[-1] if draft.periods else None,
                    "count": len(draft.periods)},
        "sections": [{"key": s["key"], "header_label": s["header_label"],
                      "total_label": s["total_label"],
                      "subtotal_groups": [g["name"] for g in s.get("subtotal_groups") or []]}
                     for s in draft.config["income_statement"]["sections"]],
        "reported_lines": draft.config["income_statement"]["reported_lines"],
        "detected": [{"what": x.what, "value": x.value, "confidence": x.confidence,
                      "how": x.how} for x in draft.detected],
        "categories": draft.categories,
        "needs_decision": draft.decisions,
        "problems": draft.problems,
        "checks": _checks(validation),
        "validation": validation,
        "warnings": validation.get("warnings", []),
        "next_command": (f"python -m finance_engine approve-config --client {client_id} "
                         f"--by \"<your name>\"" if ok else
                         f"python -m finance_engine validate-config --client {client_id} "
                         f"--draft"),
    }
    _write_json(draft_dir(data_dir, client_id) / "draft_summary.json", summary)
    # The draft's own check result, so approve-config refuses an unchanged failed draft
    # (init-client's layout problems included, which the engine alone may not catch).
    _write_json(draft_dir(data_dir, client_id) / "validation.json",
                {"config_sha256": summary["config_sha256"],
                 "validated_at_utc": summary["drafted_at_utc"], "ok": ok,
                 "errors": [{"message": p, "line": None} for p in draft.problems],
                 "checks": summary["checks"], "by": "init-client --draft"})
    return summary


def _checks(validation: dict) -> str | None:
    """The reconciliation result as the engine words it, e.g. "65/65 passed"."""
    from finance_engine.formatting import check_count
    if "checks_run" not in validation:
        return None
    return check_count(validation)


# ---- step 2: validate ---------------------------------------------------------------

def _line_of(text: str, message: str) -> int | None:
    """The 1-based YAML line of the first dotted key path named in `message`."""
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return None
    for path in _KEY_PATH.findall(message):
        current, line = node, None
        for part in path.split("."):
            if not isinstance(current, yaml.MappingNode):
                break
            hit = next(((k, v) for k, v in current.value
                        if isinstance(k, yaml.ScalarNode) and k.value == part), None)
            if hit is None:
                break
            line, current = hit[0].start_mark.line + 1, hit[1]
        if line is not None:
            return line
    return None


def validate_config(client_id: str, root: Path, settings, draft: bool = False,
                    from_text: str | None = None) -> dict:
    """Load, read and analyse with the real engine. Never raises for a bad config."""
    from finance_engine.config import ClientConfig, ConfigError
    from finance_engine.metrics import build_metrics
    from finance_engine.model import LayoutError
    from finance_engine.sources.factory import open_source
    from finance_engine.sources.selection import SourceSelectionError, choose

    check_client_id(client_id)
    path = (draft_config_path(settings.data_dir, client_id) if draft
            else settings.config_dir / f"{client_id}.yaml")
    if from_text is not None:
        if not draft:
            raise OnboardingError("--from replaces the draft; it needs --draft. A live "
                                  "config changes only through approve-config.")
        if not path.exists():
            raise OnboardingError(f"No draft for {client_id}: run init-client --draft "
                                  f"first.")
        _write_atomic(path, from_text.replace("\r\n", "\n").encode("utf-8"))
    if not path.exists():
        raise OnboardingError(f"No {'draft ' if draft else ''}config for {client_id} at "
                              f"{path}.")
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8", errors="replace")
    result = {"client_id": client_id, "draft": draft, "config_path": _rel(path, root),
              "config_sha256": _sha_bytes(raw_bytes), "validated_at_utc": _now(),
              "ok": False, "errors": [], "warnings": [], "periods": [], "latest": None,
              "checks": None}
    errors = result["errors"]

    def finish() -> dict:
        result["ok"] = not errors
        if draft:
            _write_json(draft_dir(settings.data_dir, client_id) / "validation.json",
                        {k: result[k] for k in ("config_sha256", "validated_at_utc", "ok",
                                                "errors", "checks")})
        return result

    try:
        yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        context = getattr(exc, "context_mark", None)
        where = (f" ({exc.context} at line {context.line + 1})"
                 if context is not None and getattr(exc, "context", None) else "")
        errors.append({"message": f"Not valid YAML: {getattr(exc, 'problem', None) or exc}"
                                  f"{where}",
                       "line": mark.line + 1 if mark else None,
                       "context_line": context.line + 1 if context else None})
        return finish()
    try:
        config = ClientConfig.load_path(path)
    except ConfigError as exc:
        message = str(exc).replace(str(path), path.name)
        errors.append({"message": message, "line": _line_of(text, message)})
        return finish()
    if config.client_id != client_id:
        errors.append({"message": f"client_id is {config.client_id!r} but this is the "
                                  f"config for {client_id!r}.",
                       "line": _line_of(text, "client_id.x") or 1})
        return finish()
    try:
        choice = choose(config, settings, root, None, None)
        dataset = open_source(choice.path, config).load(config)
    except (LayoutError, SourceSelectionError, FileNotFoundError, KeyError, ValueError,
            OSError) as exc:
        message = " ".join(str(exc).split())[:400]
        errors.append({"message": f"Reading the source failed: {message}",
                       "line": _line_of(text, message)})
        return finish()
    keys = dataset.period_keys()
    result["periods"] = keys
    if not keys:
        errors.append({"message": "The source has no months.", "line": None})
        return finish()
    result["latest"] = keys[-1]
    try:
        doc = build_metrics(dataset, config, keys[-1], project_root=root)
    except (LayoutError, KeyError, ValueError) as exc:
        errors.append({"message": f"analyze {keys[-1]} failed: {exc}", "line": None})
        return finish()
    recon = doc["reconciliation"]
    from finance_engine.formatting import check_count
    result["checks"] = check_count(recon)
    for f in recon.get("failures", []):
        errors.append({"message": f"Reconciliation failed: {f['key']} {f['period']} "
                                  f"(computed {f['computed']}, workbook {f['reported']}).",
                       "line": None})
    for issue in doc["data_quality_issues"]:
        if issue["severity"] == "error" and issue["code"] != "ttm_window_incomplete":
            errors.append({"message": f"analyze {keys[-1]}: {issue['code']}: "
                                      f"{issue['message']}", "line": None})
        elif issue["severity"] == "warning":
            result["warnings"].append({"code": issue["code"], "message": issue["message"]})
    return finish()


# ---- step 3: approve ----------------------------------------------------------------

def approve_config(client_id: str, by: str, root: Path, settings) -> dict:
    from finance_engine.review import ReviewError, clean_name

    check_client_id(client_id)
    try:
        who = clean_name(by)
    except ReviewError as exc:
        raise OnboardingError(str(exc)) from None
    path = draft_config_path(settings.data_dir, client_id)
    if not path.exists():
        raise OnboardingError(f"No draft for {client_id} at {path}. Run init-client --draft "
                              f"first.")
    sha = _sha_bytes(path.read_bytes())
    last = draft_dir(settings.data_dir, client_id) / "validation.json"
    if last.exists():
        prior = json.loads(last.read_text(encoding="utf-8"))
        if prior.get("config_sha256") == sha and not prior.get("ok"):
            raise OnboardingError(
                f"The draft has not changed since it failed validation at "
                f"{prior.get('validated_at_utc')}: "
                + "; ".join(e["message"] for e in prior.get("errors", [])[:3])
                + ". Edit it, then validate again.")
    live = settings.config_dir / f"{client_id}.yaml"
    previous = _sha_bytes(live.read_bytes()) if live.exists() else None
    if previous == sha:
        raise OnboardingError(f"{_rel(live, root)} already equals this draft; nothing to "
                              f"approve.")
    result = validate_config(client_id, root, settings, draft=True)
    if not result["ok"]:
        raise OnboardingError("The draft failed validation, so it was not approved: "
                              + "; ".join(e["message"] for e in result["errors"][:3]))
    _write_atomic(live, path.read_bytes())
    record_path = approval_path(settings.data_dir, client_id)
    history = []
    if record_path.exists():
        old = json.loads(record_path.read_text(encoding="utf-8"))
        history = list(old.pop("history", [])) + [old]
    record = {
        "client_id": client_id,
        "approved_by": who,
        "name_is_self_declared": True,
        "approved_at_utc": _now(),
        "config_path": _rel(live, root),
        "config_sha256": sha,
        "previous_config_sha256": previous,
        "draft_path": _rel(path, root),
        "validation": {"checks": result["checks"], "latest": result["latest"],
                       "periods": len(result["periods"]),
                       "warnings": [w["code"] for w in result["warnings"]]},
        "history": history,
    }
    _write_json(record_path, record)
    return record


__all__ = ["CLIENT_ID", "OnboardingError", "InitError", "check_client_id", "init_draft",
           "validate_config", "approve_config", "draft_config_path", "approval_path"]
