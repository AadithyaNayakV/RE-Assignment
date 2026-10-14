"""Review workflow for one report: files only, no database.

    python -m finance_engine report --client test_clean --period 2026-01   # Ready for review
    python -m finance_engine review mark-reviewed --client test_clean --period 2026-01 --by "B. Partner"
    python -m finance_engine review send-back --client test_clean --period 2026-01 --by "B. Partner" \
        --comment "Re-check the rent line"
    python -m finance_engine review status    --client test_clean --period 2026-01
    python -m finance_engine review log       --client test_clean --period 2026-01
    python -m finance_engine review queue                      # everything Ready for review
    python -m finance_engine review submit    --client test_clean --period 2026-01 --by "A. Writer"
                                               # only for a report.md a person edited

Statuses: **Ready for review** (`ready_for_review`), **Changes requested**
(`changes_requested`), **Reviewed** (`reviewed`). Generating a report submits it
automatically: `report` appends a `submit` entry by `system` (written by: the commentary
provider), so a new report is Ready for review with no human step. A reviewer then marks
it as reviewed (a name is required) or sends it back (a comment is required). After a
send-back, regenerating the report (the comment reaches a cloud writer as reviewer notes)
makes it Ready for review again.

The log lives next to the report it is about, in `<report folder>/review/review_log.json`,
and is append-only: each entry records the time (UTC), the name, the action, the comment,
the SHA-256 of report.md and metrics.json at that moment, and the commentary provider.
Each entry also carries the SHA-256 of the entry before it, so an edited or deleted entry
shows as a broken chain. A copy of report.md is kept under `review/versions/` at every
submit.

A decision is about the exact bytes it saw. If report.md or metrics.json changes outside
`report` (a person edits it), the status reads `not in review (changed since ...)` and the
old decision does not carry over: whoever edited it submits that version with
`review submit --by NAME`, or the report is regenerated. A report generated before
automatic submission (no log) is also not in review until it is regenerated.

Logs written before this flow say `approve` and `approved`; they are read as
`mark_reviewed` and Reviewed.

Names are typed in and self-declared. There is no sign-in; this runs on one machine.
Real sign-in is [PLANNED] for hosting.

No figure is read, compared or computed here: only file hashes and the log.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

NOT_IN_REVIEW, READY, CHANGES_REQUESTED, REVIEWED = (
    "not_in_review", "ready_for_review", "changes_requested", "reviewed")
STATES = (NOT_IN_REVIEW, READY, CHANGES_REQUESTED, REVIEWED)
#: What a person reads for each state.
LABELS = {NOT_IN_REVIEW: "not in review", READY: "ready for review",
          CHANGES_REQUESTED: "changes requested", REVIEWED: "reviewed"}
SUBMIT, MARK_REVIEWED, SEND_BACK = "submit", "mark_reviewed", "send_back"
ACTIONS = (SUBMIT, MARK_REVIEWED, SEND_BACK)
#: The action older logs recorded for "mark as reviewed"; read, never written.
_LEGACY_MARK_REVIEWED = "approve"
#: The name on the automatic submission `report` makes. Not accepted from a person.
SYSTEM = "system"
#: What a person reads for each logged action (older logs included).
ACTION_LABELS = {SUBMIT: "Submitted", MARK_REVIEWED: "Marked as reviewed",
                 _LEGACY_MARK_REVIEWED: "Marked as reviewed", SEND_BACK: "Sent back"}

#: The state each action leads to.
_AFTER = {SUBMIT: READY, MARK_REVIEWED: REVIEWED, _LEGACY_MARK_REVIEWED: REVIEWED,
          SEND_BACK: CHANGES_REQUESTED}
#: Which (effective) states an action may start from.
_FROM = {SUBMIT: (NOT_IN_REVIEW, CHANGES_REQUESTED), MARK_REVIEWED: (READY,),
         SEND_BACK: (READY,)}

LOG_NAME = "review_log.json"
MAX_NAME = 80
MAX_COMMENT = 4000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WRITTEN_BY = re.compile(r"_Written by the `([^`]+)` commentary provider")
_FOOTER = re.compile(r"_Commentary written by the `([^`]+)` provider on its (\w+) attempt\. "
                     r"Data left this machine: (yes|no)")


class ReviewError(Exception):
    """A refused review action. Nothing was written."""


SETTINGS_FILE = "config/review.yaml"
DEFAULT_SETTINGS = {"require_different_reviewer": True}
#: The earlier name of the setting, still read.
_LEGACY_SETTINGS = {"require_different_approver": "require_different_reviewer"}


def load_settings(root: Path) -> dict:
    """`config/review.yaml`, over the defaults. A missing file keeps the defaults."""
    import yaml
    path = Path(root) / SETTINGS_FILE
    settings = dict(DEFAULT_SETTINGS)
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ReviewError(f"{SETTINGS_FILE} must hold a YAML mapping.")
        raw = {_LEGACY_SETTINGS.get(k, k): v for k, v in raw.items()}
        unknown = set(raw) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ReviewError(f"{SETTINGS_FILE}: unknown setting(s) {sorted(unknown)}.")
        settings.update({k: bool(v) for k, v in raw.items()})
    return settings


@dataclass
class Status:
    state: str              # ready_for_review | changes_requested | reviewed | not_in_review
    label: str              # what a person reads, e.g. "ready for review"
    changed: bool           # report.md or metrics.json differs from the last decision
    last: dict | None       # the last log entry
    report_sha256: str | None
    metrics_sha256: str | None
    log_intact: bool
    entries: int

    def to_dict(self) -> dict:
        return {"state": self.state, "label": self.label, "changed_since_last_action":
                self.changed, "last_entry": self.last, "report_sha256": self.report_sha256,
                "metrics_sha256": self.metrics_sha256, "log_intact": self.log_intact,
                "entries": self.entries}


# ---- files -------------------------------------------------------------------------

def _sha(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry_sha(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def review_dir(report_dir: Path) -> Path:
    return Path(report_dir) / "review"


def read_log(report_dir: Path) -> list[dict]:
    path = review_dir(report_dir) / LOG_NAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"{path} is not readable JSON ({exc}). The log is append-only; "
                          f"restore it rather than editing it.") from None
    return list(data.get("entries") or [])


def chain_intact(entries: list[dict]) -> bool:
    prev = None
    for e in entries:
        if e.get("prev_entry_sha256") != prev:
            return False
        prev = _entry_sha(e)
    return True


def provider_of(report_dir: Path) -> dict:
    """The commentary provider, attempt and data-left flag, as report.md states them."""
    path = Path(report_dir) / "report.md"
    if not path.exists():
        return {"provider": None, "attempt": None, "data_left_machine": None}
    text = path.read_text(encoding="utf-8")
    footer = _FOOTER.search(text)
    if footer:
        return {"provider": footer.group(1), "attempt": footer.group(2),
                "data_left_machine": footer.group(3) == "yes"}
    written = _WRITTEN_BY.search(text)
    # Without the orchestrator's footer the run used a local provider: nothing was sent.
    return {"provider": written.group(1) if written else None, "attempt": "first",
            "data_left_machine": False if written else None}


# ---- status -------------------------------------------------------------------------

def status(report_dir: Path) -> Status:
    report_dir = Path(report_dir)
    entries = read_log(report_dir)
    report_sha = _sha(report_dir / "report.md")
    metrics_sha = _sha(report_dir / "metrics.json")
    intact = chain_intact(entries)
    if not entries:
        return Status(NOT_IN_REVIEW, LABELS[NOT_IN_REVIEW], False, None, report_sha,
                      metrics_sha, intact, 0)
    last = entries[-1]
    recorded = _AFTER[last["action"]]
    changed = (last.get("report_sha256") != report_sha
               or last.get("metrics_sha256") != metrics_sha)
    if not changed:
        state, label = recorded, LABELS[recorded]
    else:                                      # edited outside `report`: not in review
        since = {REVIEWED: "it was reviewed", READY: "it was submitted",
                 CHANGES_REQUESTED: "the send-back"}[recorded]
        state, label = NOT_IN_REVIEW, f"{LABELS[NOT_IN_REVIEW]} (changed since {since})"
    if not intact:
        label += " — WARNING: review log chain is broken"
    return Status(state, label, changed, last, report_sha, metrics_sha, intact,
                  len(entries))


def last_send_back(report_dir: Path) -> dict | None:
    """The last entry, if it is a send-back: its comment becomes reviewer notes."""
    entries = read_log(report_dir)
    if entries and entries[-1].get("action") == SEND_BACK:
        return entries[-1]
    return None


# ---- actions ------------------------------------------------------------------------

def clean_name(name: str | None) -> str:
    text = " ".join(str(name or "").split())
    if not text:
        raise ReviewError("--by NAME is required: type the reviewer's name. Names are "
                          "self-declared; there is no sign-in.")
    if len(text) > MAX_NAME or _CONTROL.search(text):
        raise ReviewError(f"--by must be a plain name of at most {MAX_NAME} characters.")
    if text.casefold() == SYSTEM:
        raise ReviewError(f"{SYSTEM!r} is the name on automatic submissions; type your "
                          f"own name.")
    return text


def current_submission(entries: list[dict]) -> dict:
    """The submit entry for the version now in review ({} when there is none)."""
    return next((e for e in reversed(entries) if e.get("action") == SUBMIT), {})


def human_submitted(entries: list[dict]) -> str | None:
    """The person who submitted (and so edited or answered for) the current version, or
    None when `report` submitted it automatically."""
    sub = current_submission(entries)
    return None if not sub or sub.get("automatic") else sub.get("name")


def submit_generated(report_dir: Path, now: dt.datetime | None = None) -> dict | None:
    """Called by `report` right after it writes report.md: the new version is Ready for
    review, submitted by `system`. Returns the entry, or None when this exact version is
    already Ready for review."""
    current = status(report_dir)
    if current.state not in _FROM[SUBMIT]:        # this exact version is already in review
        return None
    return act(report_dir, SUBMIT, SYSTEM, now=now, automatic=True)


def clean_comment(comment: str | None) -> str:
    text = str(comment or "").replace("\r\n", "\n").strip()
    if len(text) > MAX_COMMENT:
        raise ReviewError(f"--comment is longer than {MAX_COMMENT} characters.")
    if _CONTROL.search(text):
        raise ReviewError("--comment contains control characters.")
    return text


def act(report_dir: Path, action: str, name: str, comment: str | None = None,
        now: dt.datetime | None = None, settings: dict | None = None,
        automatic: bool = False) -> dict:
    """Append one decision to the log and return the new entry. Refusals raise.

    `settings` is `load_settings(root)`; without it the defaults apply. `automatic` is
    only for `submit_generated` (the name is then `system`).
    """
    settings = {**DEFAULT_SETTINGS, **{_LEGACY_SETTINGS.get(k, k): v
                                       for k, v in (settings or {}).items()}}
    report_dir = Path(report_dir)
    if action not in ACTIONS:
        raise ReviewError(f"Unknown review action {action!r}; use one of {ACTIONS}.")
    who = SYSTEM if automatic else clean_name(name)
    text = clean_comment(comment)
    if action == SEND_BACK and not text:
        raise ReviewError("send-back needs a non-empty --comment saying what to change.")
    if not (report_dir / "report.md").exists() or not (report_dir / "metrics.json").exists():
        raise ReviewError(f"No report in {report_dir}. Run `report` for this client and "
                          f"scope first.")
    entries = read_log(report_dir)
    if not chain_intact(entries):
        raise ReviewError(f"{review_dir(report_dir) / LOG_NAME} has been edited (its hash "
                          f"chain is broken). Restore it; no new decision was recorded.")
    current = status(report_dir)
    if current.state not in _FROM[action]:
        allowed = " or ".join(LABELS[s] for s in _FROM[action])
        verb = action.replace("_", "-")
        raise ReviewError(f"Cannot {verb}: the report is {current.label}; {verb} needs "
                          f"{allowed}.")
    if action == MARK_REVIEWED and settings["require_different_reviewer"]:
        # Only a person who submitted (or edited) this version is held to it; a version
        # `report` submitted automatically can be marked by anyone.
        submitter = human_submitted(entries)
        if submitter and submitter.casefold() == who.casefold():
            raise ReviewError(
                f"Cannot mark as reviewed: {who} submitted this version, and "
                f"{SETTINGS_FILE} sets require_different_reviewer: true. Ask someone else "
                f"to review it, or change that setting. (Names are self-declared; this is "
                f"a nudge, not a security control.)")
    when = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    entry = {
        "time_utc": when.replace(microsecond=0).isoformat(),
        "name": who,
        "name_is_self_declared": not automatic,
        "action": action,
        "comment": text,
        "report_sha256": current.report_sha256,
        "metrics_sha256": current.metrics_sha256,
        **{k: v for k, v in provider_of(report_dir).items()},
        "prev_entry_sha256": _entry_sha(entries[-1]) if entries else None,
    }
    if automatic:
        entry["automatic"] = True
    folder = review_dir(report_dir)
    if action == SUBMIT:
        versions = folder / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        stamp = when.strftime("%Y%m%dT%H%M%SZ")
        copy = versions / f"report_{stamp}_{current.report_sha256[:12]}.md"
        n = 1
        while copy.exists():
            n += 1
            copy = versions / f"report_{stamp}_{current.report_sha256[:12]}_{n}.md"
        copy.write_bytes((report_dir / "report.md").read_bytes())
        entry["version_file"] = f"versions/{copy.name}"
    _write_log(report_dir, entries + [entry])
    return entry


def _write_log(report_dir: Path, entries: list[dict]) -> None:
    folder = review_dir(report_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOG_NAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "note": "Append-only review log. Names are self-declared (no sign-in). Each "
                "entry carries the SHA-256 of the entry before it.",
        "entries": entries}, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def versions(report_dir: Path) -> list[str]:
    folder = review_dir(report_dir) / "versions"
    if not folder.exists():
        return []
    return sorted(p.name for p in folder.glob("*.md"))


# ---- every report folder --------------------------------------------------------------

def report_dirs(output_dir: Path) -> list[Path]:
    """Every folder under output/clients that holds a report.md (any client, any scope)."""
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return []
    return sorted(p.parent for p in output_dir.rglob("report.md")
                  if "review" not in p.relative_to(output_dir).parts)


def queue(output_dir: Path) -> list[dict]:
    """Every report that is Ready for review, across clients."""
    out = []
    for folder in report_dirs(output_dir):
        try:
            st = status(folder)
        except ReviewError:
            continue
        if st.state == READY:
            rel = folder.relative_to(output_dir).parts
            out.append({"client_id": rel[0], "folder": "/".join(rel),
                        "file_key": rel[1] if len(rel) == 3 else None, "leaf": rel[-1],
                        "submitted_by": st.last.get("name"),
                        "automatic": bool(st.last.get("automatic")),
                        "submitted_at_utc": st.last.get("time_utc"),
                        "provider": st.last.get("provider")})
    return out
