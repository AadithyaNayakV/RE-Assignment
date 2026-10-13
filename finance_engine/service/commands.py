"""The exact CLI command behind each service action, as argv and as typed text.

Every value is checked before it is put on a command line, and values are passed as
`--flag=value` so a value can never be read as another option. The display string is the
command a person would type in the terminal for the same result.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from finance_engine.onboarding import check_client_id

PERIOD = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
FILE_NAME = re.compile(r"[A-Za-z0-9 ._()-]{1,200}")
SOURCE_SUFFIXES = (".xlsx", ".xlsm", ".json")
SCOPES = ("month", "year", "all")


class CommandError(ValueError):
    """A value that cannot go on a command line. Nothing was run."""


def _quote(value: str) -> str:
    return f'"{value}"' if (not value or re.search(r"[\s\"'&|<>^]", value)) else value


def finance(*pairs: tuple[str, str | None] | str) -> tuple[list[str], str]:
    """argv and display text for `python -m finance_engine <command> <flags>`."""
    argv = [sys.executable, "-m", "finance_engine"]
    shown = ["python", "-m", "finance_engine"]
    for item in pairs:
        if isinstance(item, str):
            argv.append(item)
            shown.append(item)
            continue
        flag, value = item
        if value is None:
            argv.append(flag)
            shown.append(flag)
        else:
            argv.append(f"{flag}={value}")
            shown += [flag, _quote(str(value))]
    return argv, " ".join(shown)


def script(path: str, *args: str) -> tuple[list[str], str]:
    return [sys.executable, path, *args], " ".join(["python", path, *args])


def check_period(period: str) -> str:
    if not isinstance(period, str) or not (PERIOD.fullmatch(period) or period == "latest"):
        raise CommandError(f"period {period!r}: use YYYY-MM, e.g. 2026-06.")
    return period


def check_year(year) -> int:
    try:
        value = int(year)
    except (TypeError, ValueError):
        raise CommandError(f"year {year!r}: use a four-digit year.") from None
    if not 1900 <= value <= 9999:
        raise CommandError(f"year {year!r}: use a four-digit year.")
    return value


def check_file_name(name: str) -> str:
    if (not isinstance(name, str) or not FILE_NAME.fullmatch(name) or name.startswith(("-", "."))
            or Path(name).name != name or not name.lower().endswith(SOURCE_SUFFIXES)):
        raise CommandError(f"file {name!r}: give the name of a file in the client's source "
                           f"folder, not a path.")
    return name


def check_client(client_id: str) -> str:
    try:
        return check_client_id(client_id)
    except Exception as exc:
        raise CommandError(str(exc)) from None


def scope_pairs(scope: str = "month", period: str | None = None, year=None,
                file: str | None = None) -> list[tuple[str, str]]:
    if scope not in SCOPES:
        raise CommandError(f"scope {scope!r}: use month, year or all.")
    pairs: list[tuple[str, str]] = []
    if scope == "month":
        pairs.append(("--period", check_period(period)))
    elif scope == "year":
        pairs += [("--scope", "year"), ("--year", str(check_year(year)))]
    else:
        pairs.append(("--scope", "all"))
    if file:
        pairs.append(("--file", check_file_name(file)))
    return pairs


def report_command(client: str, scope: str = "month", period: str | None = None,
                   year=None, file: str | None = None, keep_data_local: bool = True,
                   command: str = "report") -> tuple[list[str], str]:
    """`report` (or `analyze`). Keep data on this machine is exactly --provider
    deterministic; otherwise the system's chain, --provider auto."""
    pairs: list = [command, ("--client", check_client(client)),
                   *scope_pairs(scope, period, year, file)]
    if command == "report":
        pairs.append(("--provider", "deterministic" if keep_data_local else "auto"))
    pairs.append(("--json", None))
    return finance(*pairs)


def pull_command(client: str, start: str, end: str) -> tuple[list[str], str]:
    return finance("pull-quickbooks", ("--client", check_client(client)),
                   ("--start", check_period(start)), ("--end", check_period(end)),
                   ("--json", None))


ENGINE_EVALS = "evals/run_engine_evals.py"
SKILL_EVALS = "evals/run_skill_evals.py"


def free_eval_commands() -> list[tuple[list[str], str]]:
    """Level 1 and the deterministic Level 2 control: free, no model, no network."""
    return [script(ENGINE_EVALS, "--compare"),
            script(SKILL_EVALS, "--provider", "deterministic", "--compare")]


#: Shown, never run: the paid Level 2 skill evals use Claude Pro usage.
PAID_EVAL_COMMAND = "python evals/run_skill_evals.py 04"
