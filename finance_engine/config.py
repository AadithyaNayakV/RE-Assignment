"""Client config loading and process settings.

Two kinds of configuration, deliberately separated:

* **Client config** — how one company's workbook is laid out and classified. Lives in
  `config/clients/<client_id>.yaml`. Adding a company is adding one of these files.
* **Settings** — process-level knobs that vary by environment, from env vars only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REQUIRED_TOP_LEVEL = ("client_id", "company_name", "sheets", "income_statement")


class ConfigError(Exception):
    """The client config is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True)
class Settings:
    """Process settings. Env vars only — no secrets are read or stored here."""

    llm_provider: str
    config_dir: Path
    data_dir: Path
    output_dir: Path

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Settings":
        base = root or Path.cwd()
        return cls(
            llm_provider=os.environ.get("LLM_PROVIDER", "deterministic").strip().lower(),
            config_dir=base / os.environ.get("FINANCE_CONFIG_DIR", "config/clients"),
            data_dir=base / os.environ.get("FINANCE_DATA_DIR", "data/clients"),
            output_dir=base / os.environ.get("FINANCE_OUTPUT_DIR", "output/clients"),
        )


class ClientConfig:
    """Typed, validated access to one client's YAML. Raises rather than guessing."""

    def __init__(self, raw: dict[str, Any], path: Path):
        self.raw = raw
        self.path = path
        self._validate()

    # ---- construction -------------------------------------------------

    @classmethod
    def load(cls, client_id: str, settings: Settings) -> "ClientConfig":
        path = settings.config_dir / f"{client_id}.yaml"
        if not path.exists():
            available = sorted(p.stem for p in settings.config_dir.glob("*.yaml"))
            raise ConfigError(
                f"No config for client {client_id!r} at {path}. "
                f"Available clients: {', '.join(available) or 'none'}"
            )
        return cls.load_path(path)

    @classmethod
    def load_path(cls, path: Path) -> "ClientConfig":
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a YAML mapping at the top level")
        return cls(raw, path)

    def _validate(self) -> None:
        missing = [k for k in REQUIRED_TOP_LEVEL if k not in self.raw]
        if missing:
            raise ConfigError(f"{self.path} is missing required keys: {', '.join(missing)}")

        for sheet_key in ("income_statement",):
            if sheet_key not in self.raw["sheets"]:
                raise ConfigError(f"{self.path}: sheets.{sheet_key} is required")

        for sheet_key, spec in self.raw["sheets"].items():
            for field_name in ("name", "header_row", "label_columns",
                               "first_data_column", "date_order"):
                if field_name not in spec:
                    raise ConfigError(
                        f"{self.path}: sheets.{sheet_key}.{field_name} is required"
                    )
            if spec["date_order"] not in ("newest_first", "oldest_first"):
                raise ConfigError(
                    f"{self.path}: sheets.{sheet_key}.date_order must be "
                    f"'newest_first' or 'oldest_first', got {spec['date_order']!r}"
                )

        sections = self.raw["income_statement"].get("sections")
        if not sections:
            raise ConfigError(f"{self.path}: income_statement.sections must not be empty")
        keys = [s.get("key") for s in sections]
        if len(keys) != len(set(keys)):
            raise ConfigError(f"{self.path}: duplicate section keys in {keys}")
        for sec in sections:
            for field_name in ("key", "header_label", "total_label"):
                if not sec.get(field_name):
                    raise ConfigError(
                        f"{self.path}: every income_statement section needs {field_name}"
                    )

    # ---- accessors ----------------------------------------------------

    @property
    def client_id(self) -> str:
        return str(self.raw["client_id"])

    @property
    def company_name(self) -> str:
        return str(self.raw["company_name"])

    @property
    def currency(self) -> str:
        return str(self.raw.get("currency", "USD"))

    @property
    def currency_symbol(self) -> str:
        return str(self.raw.get("currency_symbol", ""))

    @property
    def units_scale(self) -> float:
        return float(self.raw.get("units_scale", 1))

    @property
    def source_filename(self) -> str | None:
        src = self.raw.get("source") or {}
        return src.get("filename")

    @property
    def source_type(self) -> str:
        src = self.raw.get("source") or {}
        return str(src.get("type", "excel"))

    @property
    def source_directory(self) -> str | None:
        """Optional project-relative directory holding the source file.

        Default is `<data_dir>/<client_id>/source`. The synthetic test clients set this
        to `data/synthetic`, so they share one folder instead of needing a per-client
        tree. Real clients keep their own directory.
        """
        src = self.raw.get("source") or {}
        value = src.get("directory")
        return str(value) if value else None

    @property
    def is_synthetic(self) -> bool:
        """True for generated test data. Keeps it out of client-facing summaries."""
        return bool(self.raw.get("synthetic", False))

    def sheet_spec(self, key: str) -> dict[str, Any] | None:
        return self.raw["sheets"].get(key)

    @property
    def sections(self) -> list[dict[str, Any]]:
        return list(self.raw["income_statement"]["sections"])

    def section(self, key: str) -> dict[str, Any] | None:
        for sec in self.sections:
            if sec.get("key") == key:
                return sec
        return None

    @property
    def reported_lines(self) -> dict[str, str]:
        return dict(self.raw["income_statement"].get("reported_lines") or {})

    @property
    def balance_sheet(self) -> dict[str, Any]:
        return dict(self.raw.get("balance_sheet") or {})

    @property
    def opex_categories(self) -> list[dict[str, Any]]:
        return list(self.raw.get("opex_categories") or [])

    @property
    def fallback_opex_category(self) -> str:
        return str(self.raw.get("fallback_opex_category", "Other Operating Expenses"))

    @property
    def tolerance_abs(self) -> float:
        return float((self.raw.get("reconciliation") or {}).get("tolerance_abs", 0.01))

    @property
    def classification_notes(self) -> list[str]:
        notes = self.raw.get("classification_notes") or []
        return [" ".join(str(n).split()) for n in notes]

    @property
    def ttm_months(self) -> int:
        return int((self.raw.get("ttm") or {}).get("months", 12))

    def threshold(self, name: str, default: float) -> float:
        return float((self.raw.get("thresholds") or {}).get(name, default))

    def categorise_opex(self, label: str) -> str:
        """Assign a presentation category. First matching rule wins, config order.

        Only ever called for rows the *structure* already placed in opex.
        """
        text = str(label).casefold()
        for rule in self.opex_categories:
            for exact in rule.get("match_exact") or []:
                if text == str(exact).casefold():
                    return str(rule["name"])
        for rule in self.opex_categories:
            for needle in rule.get("match_contains") or []:
                if str(needle).casefold() in text:
                    return str(rule["name"])
        return self.fallback_opex_category
