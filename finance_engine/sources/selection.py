"""Which workbook a run reads, when a client has more than one.

A client's own folder, `data/clients/<client_id>/source/`, may hold several workbooks that
share one config layout (say, one per financial year). Exactly one of them is read per run.
Files are **never merged**, and no comparison ever reaches into a second file.

How the file is chosen:

* `--file NAME` — that workbook, from the client's source folder. Outputs then go under
  `output/clients/<client>/<file-stem>/...` so two files' results never overwrite each
  other.
* No `--file`, and the config names `source.filename` — that file, exactly as before this
  module existed. Nothing about these runs changes.
* No `--file`, no `source.filename`, one workbook in the folder — that workbook, as before.
* No `--file`, no `source.filename`, several workbooks in the client's own folder — the
  one that contains the requested period (or year). Exactly one match is used and named on
  stderr; several matches stop the run and list them; no match stops the run and lists the
  periods each file covers.

A *shared* folder (the synthetic clients all use `data/synthetic`) holds other clients'
workbooks, so only the configured file is ever considered there.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from finance_engine.config import ClientConfig, Settings
from finance_engine.model import LayoutError, Period


class SourceSelectionError(Exception):
    """No single workbook could be chosen. Reported in one line; nothing is written."""


@dataclass
class Want:
    """What the run needs from a workbook: one month, any month of a year, or anything."""

    kind: str                    # "period" | "year" | "all"
    value: str | int | None = None

    def matches(self, periods: list[str]) -> bool:
        if self.kind == "period":
            return self.value in periods
        if self.kind == "year":
            return any(p.startswith(f"{int(self.value):04d}-") for p in periods)
        return bool(periods)

    def describe(self) -> str:
        if self.kind == "period":
            return f"period {self.value}"
        if self.kind == "year":
            return f"year {self.value}"
        return "the whole file"


@dataclass
class WorkbookInfo:
    filename: str
    path: Path
    periods: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def span(self) -> str:
        if self.error:
            return f"unreadable with this client's config: {self.error}"
        if not self.periods:
            return "no months found"
        return f"{self.periods[0]}..{self.periods[-1]}, {len(self.periods)} month(s)"

    def to_dict(self) -> dict:
        d = {"filename": self.filename,
             "first_period": self.periods[0] if self.periods else None,
             "last_period": self.periods[-1] if self.periods else None,
             "period_count": len(self.periods),
             "periods": list(self.periods)}
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class SourceChoice:
    path: Path
    #: config | only | explicit | auto — how the file was chosen.
    mode: str
    #: The file stem used in output paths. Set only for an explicit `--file`.
    output_key: str | None = None
    #: The other workbooks in the client's own folder, with their periods. Filled only
    #: when the run is file-aware (explicit or auto); empty otherwise, so a run that never
    #: asked about files produces exactly the output it always did.
    siblings: list[WorkbookInfo] = field(default_factory=list)
    #: A line for stderr naming the file that was chosen, when the engine chose it.
    note: str | None = None

    @property
    def file_aware(self) -> bool:
        return self.mode in ("explicit", "auto")

    def periods_elsewhere(self) -> dict[str, list[str]]:
        """Period -> the other workbooks that hold it. Never used as data, only as a note."""
        out: dict[str, list[str]] = {}
        for info in self.siblings:
            for p in info.periods:
                out.setdefault(p, []).append(info.filename)
        return out

    def describe(self) -> dict | None:
        """The `source.selection` block of metrics.json. None for a non-file-aware run."""
        if not self.file_aware:
            return None
        return {
            "mode": self.mode,
            "chosen_by": ("--file" if self.mode == "explicit" else
                          "the only workbook in the folder containing the request"),
            "output_key": self.output_key,
            "other_workbooks": [i.to_dict() for i in self.siblings],
            "rule": ("Only the chosen workbook is read. A comparison month that exists only "
                     "in another workbook is n/a; files are never merged."),
        }


# ---- folders ---------------------------------------------------------------

def source_folder(config: ClientConfig, settings: Settings, root: Path) -> Path:
    if config.source_directory:
        return root / config.source_directory
    return settings.data_dir / config.client_id / "source"


def own_folder(config: ClientConfig, settings: Settings) -> Path:
    return settings.data_dir / config.client_id / "source"


def is_own_folder(config: ClientConfig, settings: Settings, root: Path) -> bool:
    """True when the folder belongs to this client alone, so every workbook in it is
    this client's. `data/synthetic` is shared and is never treated this way."""
    try:
        return (source_folder(config, settings, root).resolve()
                == own_folder(config, settings).resolve())
    except OSError:
        return False


def list_workbooks(folder: Path, pattern: str = "*.xlsx") -> list[Path]:
    """Source files, sorted, without Excel's `~$` lock files. `pattern` is `*.json` for
    a QuickBooks client (its snapshots)."""
    if not folder.exists():
        return []
    return sorted(p for p in folder.glob(pattern) if not p.name.startswith("~$"))


def read_periods(path: Path, config: ClientConfig) -> WorkbookInfo:
    """The months one workbook (or snapshot) holds, read with this client's config."""
    from finance_engine.sources.factory import open_source
    try:
        keys = open_source(path, config).load(config).period_keys()
        return WorkbookInfo(path.name, path, keys)
    except (LayoutError, KeyError, ValueError, OSError, zipfile.BadZipFile) as exc:
        return WorkbookInfo(path.name, path, [], " ".join(str(exc).split())[:200])


def workbook_inventory(config: ClientConfig, settings: Settings,
                       root: Path) -> list[WorkbookInfo]:
    """Every workbook this client owns, with its periods.

    For a shared folder only the configured file is the client's, so only it is listed.
    """
    from finance_engine.sources.factory import source_pattern
    folder = source_folder(config, settings, root)
    pattern = source_pattern(config)
    if is_own_folder(config, settings, root):
        paths = list_workbooks(folder, pattern)
    elif config.source_filename and (folder / config.source_filename).exists():
        paths = [folder / config.source_filename]
    else:
        paths = list_workbooks(folder, pattern)[:1]
    return [read_periods(p, config) for p in paths]


# ---- the choice ------------------------------------------------------------

def _listing(infos: list[WorkbookInfo]) -> str:
    return "; ".join(f"{i.filename} ({i.span})" for i in infos) or "none"


def choose(config: ClientConfig, settings: Settings, root: Path,
           file_name: str | None, want: Want | None) -> SourceChoice:
    from finance_engine.sources.factory import source_pattern
    folder = source_folder(config, settings, root)
    own = is_own_folder(config, settings, root)
    pattern = source_pattern(config)

    if file_name:
        if Path(file_name).name != file_name or file_name in (".", ".."):
            raise SourceSelectionError(
                f"--file takes a workbook name inside {folder}, not a path: {file_name!r}.")
        path = folder / file_name
        if not path.exists():
            names = [p.name for p in list_workbooks(folder, pattern)]
            raise SourceSelectionError(
                f"--file {file_name!r} is not in {folder}. Workbooks there: "
                f"{', '.join(names) or 'none'}.")
        siblings = ([read_periods(p, config) for p in list_workbooks(folder, pattern)
                     if p != path] if own else [])
        return SourceChoice(path, "explicit", output_key=path.stem, siblings=siblings)

    filename = config.source_filename
    if filename:
        path = folder / filename
        if not path.exists():
            raise FileNotFoundError(
                f"Source workbook not found: {path}. Put the client's workbook there, or set "
                f"source.filename in {config.path}."
            )
        return SourceChoice(path, "config")

    candidates = list_workbooks(folder, pattern)
    if not candidates:
        raise FileNotFoundError(f"No {pattern.lstrip('*')} found in {folder}"
                                + (". Pull one with pull-quickbooks."
                                   if config.source_type == "quickbooks" else ""))
    if len(candidates) == 1 or not own:
        return SourceChoice(candidates[0], "only")

    infos = [read_periods(p, config) for p in candidates]
    if want is None:
        raise SourceSelectionError(
            f"{folder} holds {len(infos)} workbooks and the request does not name a period "
            f"to choose between them: {_listing(infos)}. Choose one with --file NAME.")
    matches = [i for i in infos if want.matches(i.periods)]
    if len(matches) > 1:
        raise SourceSelectionError(
            f"{want.describe().capitalize()} is in {len(matches)} workbooks: "
            f"{_listing(matches)}. Files are never merged; choose one with --file NAME.")
    if not matches:
        raise SourceSelectionError(
            f"No workbook in {folder} contains {want.describe()}. Periods each file "
            f"covers: {_listing(infos)}.")
    chosen = matches[0]
    return SourceChoice(
        chosen.path, "auto", siblings=[i for i in infos if i is not chosen],
        note=(f"source: {chosen.filename} (the only workbook in {folder} that contains "
              f"{want.describe()})"))


def hint_for_missing_period(config: ClientConfig, settings: Settings, root: Path,
                            chosen: Path, period: str) -> str | None:
    """A stderr hint when a configured file lacks the period and a sibling has it.

    Only a hint: the run itself is unchanged (it reports `period_not_in_source`).
    """
    if not is_own_folder(config, settings, root):
        return None
    try:
        Period.from_key(period)
    except ValueError:
        return None
    from finance_engine.sources.factory import source_pattern
    holders = [read_periods(p, config)
               for p in list_workbooks(chosen.parent, source_pattern(config))
               if p != chosen]
    names = [i.filename for i in holders if period in i.periods]
    if not names:
        return None
    return (f"hint: {period} is in other workbook(s) in {chosen.parent}: "
            f"{', '.join(names)}. Use --file NAME to read one of them.")
