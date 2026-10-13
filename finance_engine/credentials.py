"""The `CredentialStore` boundary: secrets by NAME, never by value in logs.

Only the variable names a caller lists are ever read: from the process environment
first, then from a git-ignored `.env` in the project root. Nothing else in `.env` is
parsed into memory, and no value is ever printed. `redact()` removes every value this
store handed out, plus anything shaped like a well-known key or bearer token, from text
before it reaches a log line, an exception, a JSON file or a report.
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from pathlib import Path

#: Shapes of secrets worth removing even when this process never loaded them.
_SECRET_SHAPES = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                    # Google API keys
    re.compile(r"sk-ant-[0-9A-Za-z_\-]{10,}"),                 # Anthropic keys
    re.compile(r"(?i)\bbearer\s+[0-9A-Za-z._\-~+/=]{10,}"),     # authorization headers
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)"
               r"(\"?\s*[:=]\s*\"?)[^\s\"'&,;}]{6,}"),
]
REDACTED = "[REDACTED]"


class CredentialStore(ABC):
    """Hands out secrets by name. Implementations must never log a value."""

    @abstractmethod
    def get(self, name: str) -> str | None:
        """The value for `name`, or None. Blank counts as missing."""

    @abstractmethod
    def loaded_values(self) -> list[str]:
        """Every value handed out so far, so text can be redacted."""

    def redact(self, text) -> str:
        return redact(text, self.loaded_values())


class EnvCredentialStore(CredentialStore):
    """Environment variables first, then `NAME=value` lines of a `.env` file.

    `allowed` is the complete list of names this store may read. A name not in it is
    never read, even if asked for.
    """

    def __init__(self, allowed: list[str] | tuple[str, ...], env_file: Path | None = None,
                 environ: dict | None = None):
        self.allowed = tuple(dict.fromkeys(allowed))
        self.env_file = Path(env_file) if env_file else None
        self._environ = os.environ if environ is None else environ
        self._file_values: dict[str, str] | None = None
        self._handed_out: list[str] = []

    def _file(self) -> dict[str, str]:
        if self._file_values is None:
            self._file_values = {}
            if self.env_file and self.env_file.exists():
                for line in self.env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, _, value = line.partition("=")
                    name = name.strip().removeprefix("export ").strip()
                    if name in self.allowed:
                        self._file_values[name] = value.strip().strip('"').strip("'")
        return self._file_values

    def get(self, name: str) -> str | None:
        if name not in self.allowed:
            return None
        value = self._environ.get(name)
        if value is None or not str(value).strip():
            value = self._file().get(name)
        value = str(value).strip() if value is not None else None
        if not value:
            return None
        if value not in self._handed_out:
            self._handed_out.append(value)
        return value

    def present(self, name: str) -> bool:
        """Whether `name` has a value. For status lines; never returns the value."""
        return self.get(name) is not None

    def loaded_values(self) -> list[str]:
        return list(self._handed_out)


def redact(text, secrets: list[str] | tuple[str, ...] = ()) -> str:
    """`text` with every known secret value and every secret-shaped string removed."""
    out = "" if text is None else str(text)
    for value in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        out = out.replace(value, REDACTED)
    for pattern in _SECRET_SHAPES:
        out = pattern.sub(lambda m: (m.group(1) + m.group(2) + REDACTED)
                          if m.lastindex and m.lastindex >= 2 else REDACTED, out)
    return out
