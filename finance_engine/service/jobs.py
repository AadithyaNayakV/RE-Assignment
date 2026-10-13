"""Background jobs for the long actions: reports, evals and QuickBooks pulls.

A job has an id, a status, a capped log and a timeout. At most one job runs per key (a
client id, or "evals"), so two runs never write the same client's folders at once. A job
runs either a list of commands (each a CLI command in a subprocess, exactly what a person
would type, killed with its children on timeout) or a Python callable (used for the
QuickBooks pull, whose network transport tests replace with a fake).

The CLI never uses this: every command stays synchronous in the terminal. Jobs live in
memory only, for the life of the server process. Every log line and result is passed
through `redact` before it is stored.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

QUEUED, RUNNING, SUCCEEDED, FAILED, TIMEOUT, ERROR = ("queued", "running", "succeeded",
                                                      "failed", "timeout", "error")
FINISHED = (SUCCEEDED, FAILED, TIMEOUT, ERROR)
MAX_LOG_LINES = 400
MAX_LINE_CHARS = 1000
MAX_STDOUT_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 900.0


class JobConflict(Exception):
    """A job is already running for this key."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Job:
    id: str
    key: str
    title: str
    commands: list[list[str]]
    display: list[str]
    timeout: float
    status: str = QUEUED
    exit_code: int | None = None
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    result: dict | None = None
    error: str | None = None
    log: collections.deque = field(default_factory=lambda: collections.deque(
        maxlen=MAX_LOG_LINES))
    dropped_lines: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def add(self, line: str) -> None:
        line = line.rstrip("\r\n")
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + " ...[line cut]"
        with self._lock:
            if len(self.log) == self.log.maxlen:
                self.dropped_lines += 1
            self.log.append(line)

    def to_dict(self) -> dict:
        with self._lock:
            log = list(self.log)
        return {"id": self.id, "key": self.key, "title": self.title,
                "status": self.status, "exit_code": self.exit_code,
                "started_at_utc": self.started_at_utc,
                "finished_at_utc": self.finished_at_utc, "timeout_seconds": self.timeout,
                "commands": self.display, "result": self.result, "error": self.error,
                "log": log, "log_dropped_lines": self.dropped_lines,
                "log_cap_lines": MAX_LOG_LINES}


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


class JobManager:
    def __init__(self, root: Path, redact: Callable[[str], str] | None = None,
                 env: dict | None = None):
        self.root = Path(root)
        self.redact = redact or (lambda s: s)
        self.env = env
        self._jobs: dict[str, Job] = {}
        self._running: dict[str, str] = {}
        self._lock = threading.Lock()

    # ---- starting -------------------------------------------------------------------

    def _register(self, key: str, title: str, commands, display, timeout) -> Job:
        with self._lock:
            running = self._running.get(key)
            if running and self._jobs[running].status not in FINISHED:
                raise JobConflict(f"A job is already running for {key!r} ({running}). "
                                  f"Wait for it to finish.")
            job = Job(uuid.uuid4().hex[:12], key, title, commands, display,
                      float(timeout or DEFAULT_TIMEOUT))
            self._jobs[job.id] = job
            self._running[key] = job.id
            return job

    def start_commands(self, key: str, title: str, commands: list[list[str]],
                       display: list[str] | None = None,
                       timeout: float | None = None) -> Job:
        """Run `commands` one after another; stop at the first that exits 1 or worse
        than 2 (2 means 'written, with data problems marked')."""
        shown = [self.redact(s) for s in (display or [" ".join(c) for c in commands])]
        job = self._register(key, title, commands, shown, timeout)
        threading.Thread(target=self._run_commands, args=(job,), daemon=True).start()
        return job

    def start_callable(self, key: str, title: str, func: Callable, display: list[str],
                       timeout: float | None = None) -> Job:
        """Run `func(log)` in a thread. On timeout the job is marked and its result
        discarded; the thread itself ends when its own network timeout fires."""
        job = self._register(key, title, [], display, timeout)
        threading.Thread(target=self._run_callable, args=(job, func), daemon=True).start()
        return job

    # ---- reading --------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(self._jobs.values())

    def wait(self, job_id: str, timeout: float | None = None) -> Job:
        job = self._jobs[job_id]
        job._done.wait(timeout)
        return job

    # ---- running --------------------------------------------------------------------

    def _env(self) -> dict:
        env = dict(os.environ if self.env is None else self.env)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        # The same finance_engine as this process, whatever the project root.
        code = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = os.pathsep.join(p for p in (code, env.get("PYTHONPATH")) if p)
        return env

    def _finish(self, job: Job, status: str, exit_code=None, error=None) -> None:
        job.status = status
        job.exit_code = exit_code
        job.error = self.redact(error) if error else None
        job.finished_at_utc = _now()
        job._done.set()

    def _run_commands(self, job: Job) -> None:
        job.status, job.started_at_utc = RUNNING, _now()
        deadline = _monotonic() + job.timeout
        code = 0
        try:
            for argv, shown in zip(job.commands, job.display):
                job.add(f"$ {shown}")
                kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                          if os.name == "nt" else {"start_new_session": True})
                proc = subprocess.Popen(argv, cwd=self.root, env=self._env(),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        stdin=subprocess.DEVNULL, **kwargs)
                out: list[bytes] = []
                readers = [threading.Thread(target=self._pump, daemon=True,
                                            args=(proc.stdout, job, out)),
                           threading.Thread(target=self._pump, daemon=True,
                                            args=(proc.stderr, job, None))]
                for t in readers:
                    t.start()
                try:
                    code = proc.wait(timeout=max(0.0, deadline - _monotonic()))
                except subprocess.TimeoutExpired:
                    _kill_tree(proc)
                    for t in readers:
                        t.join(timeout=5)
                    job.add(f"timed out after {job.timeout:g} s; the command was stopped")
                    return self._finish(job, TIMEOUT, None, "timed out")
                for t in readers:
                    t.join(timeout=10)
                job.add(f"exit code {code}")
                parsed = _json_tail(b"".join(out))
                if parsed is not None:
                    job.result = json.loads(self.redact(json.dumps(parsed)))
                if code not in (0, 2):
                    return self._finish(job, FAILED, code)
            self._finish(job, SUCCEEDED, code)
        except Exception as exc:                     # a job never takes the server down
            self._finish(job, ERROR, None, f"{type(exc).__name__}: {exc}")

    def _pump(self, stream, job: Job, keep: list | None) -> None:
        size = 0
        for raw in iter(stream.readline, b""):
            if keep is not None and size < MAX_STDOUT_BYTES:
                keep.append(raw)
                size += len(raw)
            job.add(self.redact(raw.decode("utf-8", errors="replace")))
        stream.close()

    def _run_callable(self, job: Job, func: Callable) -> None:
        job.status, job.started_at_utc = RUNNING, _now()
        box: dict = {}

        def target():
            try:
                box["result"] = func(lambda line: job.add(self.redact(str(line))))
            except Exception as exc:
                box["error"] = f"{exc}"

        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        worker.join(job.timeout)
        if worker.is_alive():
            job.add(f"timed out after {job.timeout:g} s; the result will be discarded")
            return self._finish(job, TIMEOUT, None, "timed out")
        if "error" in box:
            job.add(self.redact(box["error"]))
            return self._finish(job, FAILED, 1, box["error"])
        job.result = json.loads(self.redact(json.dumps(box.get("result"), default=str)))
        self._finish(job, SUCCEEDED, 0)


def _monotonic() -> float:
    import time
    return time.monotonic()


def _json_tail(data: bytes) -> dict | None:
    """The `--json` document a CLI command printed on stdout, if there is one."""
    text = data.decode("utf-8", errors="replace").strip()
    if not text.startswith("{"):
        start = text.find("\n{")
        if start < 0:
            return None
        text = text[start + 1:]
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


PYTHON = sys.executable
