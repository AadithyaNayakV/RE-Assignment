"""Run this 30 minutes before the live demo.

    python scripts/preflight_check.py              # everything (about 2 minutes)
    python scripts/preflight_check.py --quick      # skip the full pytest run
    python scripts/preflight_check.py --no-claude-call   # no Pro usage at all

Every line it prints is a real result from a real check. A check that cannot run says
SKIP and why, never PASS. Exit status 0 only if nothing FAILED.

Checks, in order:
  1. Python and its dependencies import; the full test suite passes.
  2. The original ABC workbook's SHA-256 matches the reference in
     scripts/reference_hashes.json.
  3. `analyze` and `report` run on ABC Jun 2026, each within a time limit.
  4. The workbook hash is still unchanged after step 3.
  5. QuickBooks: the MCP server starts, and a read-only `get_company_info` returns the
     sandbox company name. If it fails, the exact re-authorisation steps are printed.
  6. Claude Code discovers the QuickBooks MCP server and the skill.

It never prints a token, a secret or a .env value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

CLIENT, PERIOD = "abc_inc", "2026-06"
SERVER_DIR = ROOT / "quickbooks-online-mcp-server"
SERVER_ENTRY = SERVER_DIR / "dist" / "index.js"
SKILL = "monthly-financial-analysis"
AUTH_FAILURE = re.compile(r"\b401\b|unauthori[sz]ed|invalid_grant|invalid_token|"
                          r"token (has )?expired|expired token|refresh token", re.I)

REAUTH_STEPS = """\
    The QuickBooks token needs re-authorising. Do this (about 2 minutes):
      1. cd quickbooks-online-mcp-server
      2. npm run auth            (opens a browser URL; sign in to the Intuit sandbox)
      3. Approve the app. The server writes the new tokens into its own .env.
      4. Re-run: python scripts/preflight_check.py
    If the browser step fails, check that http://localhost:8000/callback is listed under
    Redirect URIs for the app in the Intuit developer portal. Never paste a token anywhere."""

results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "", seconds: float | None = None) -> None:
    timing = f" ({seconds:.1f}s)" if seconds is not None else ""
    print(f"  [{status:4}] {name}{timing}" + (f"\n         {detail}" if detail else ""))
    results.append((status, name, detail))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def engine(*args: str, timeout: int = 120) -> tuple[int, str, str, float]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    start = time.time()
    proc = subprocess.run([sys.executable, "-m", "finance_engine", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout, proc.stderr, time.time() - start


# ---- 1. environment and tests ------------------------------------------------------

def check_environment(quick: bool) -> None:
    print("\n1. Python environment and tests")
    old = sys.version_info < (3, 10)
    record("FAIL" if old else "PASS", f"Python {sys.version.split()[0]}",
           "needs 3.10 or newer" if old else "")
    missing = []
    for module in ("openpyxl", "yaml", "pytest"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    record("FAIL" if missing else "PASS", "dependencies import",
           f"missing: {missing}. Run: pip install -r requirements.txt" if missing else "")
    if quick:
        record("SKIP", "full test suite", "--quick given; run `python -m pytest -q` yourself")
        return
    start = time.time()
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600, stdin=subprocess.DEVNULL)
    tail = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
    record("PASS" if proc.returncode == 0 else "FAIL", "full test suite", tail,
           time.time() - start)


# ---- 2 and 4. original workbook hash -------------------------------------------------

def workbook_path() -> Path | None:
    found = sorted((ROOT / "data" / "clients" / CLIENT / "source").glob("*.xlsx"))
    return found[0] if found else None


def check_workbook_hash(label: str) -> None:
    path = workbook_path()
    if path is None:
        record("SKIP", label, f"no workbook in data/clients/{CLIENT}/source (client data is "
                              f"git-ignored; copy it back to run this check)")
        return
    reference = json.loads((ROOT / "scripts" / "reference_hashes.json")
                           .read_text(encoding="utf-8"))["abc_inc_workbook_sha256"]
    actual = sha256(path)
    record("PASS" if actual == reference else "FAIL", label,
           f"{path.name}: sha256 {actual[:16]}... "
           + ("matches the reference" if actual == reference else
              f"DOES NOT MATCH reference {reference[:16]}..."))


# ---- 3. engine on ABC -------------------------------------------------------------------

def check_engine() -> None:
    print("\n3. Engine on ABC Jun 2026")
    if workbook_path() is None:
        record("SKIP", "analyze / report", "ABC workbook not present")
        return
    for command, limit in (("analyze", 30), ("report", 45)):
        code, out, err, seconds = engine(command, "--client", CLIENT, "--period", PERIOD)
        facts = " | ".join(line.strip() for line in out.splitlines()
                           if "reconciliation" in line or "number check" in line)
        ok = code == 0 and seconds <= limit
        why = facts or err.strip()[:200]
        if code == 0 and seconds > limit:
            why += f"  (took longer than {limit}s)"
        record("PASS" if ok else "FAIL", f"python -m finance_engine {command}", why, seconds)


# ---- 5. QuickBooks -----------------------------------------------------------------------

class Stdio:
    """Minimal newline-delimited JSON-RPC client for the MCP server over stdio."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen([shutil.which("node"), str(SERVER_ENTRY)], cwd=SERVER_DIR,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(x) for x in self.proc.stdout],
                         daemon=True).start()
        self.next_id = 0

    def request(self, method: str, params: dict, timeout: float = 90) -> dict:
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.next_id,
                                          "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        while True:
            message = json.loads(self.lines.get(timeout=timeout))
            if message.get("id") == self.next_id:
                return message

    def notify(self, method: str) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        self.proc.kill()


def find_value(obj, key: str):
    """First string value stored under `key`, searching nested JSON and JSON-in-strings."""
    if isinstance(obj, dict):
        if isinstance(obj.get(key), str):
            return obj[key]
        for value in obj.values():
            hit = find_value(value, key)
            if hit:
                return hit
    elif isinstance(obj, list):
        for value in obj:
            hit = find_value(value, key)
            if hit:
                return hit
    elif isinstance(obj, str) and obj.lstrip().startswith(("{", "[")):
        try:
            return find_value(json.loads(obj), key)
        except json.JSONDecodeError:
            return None
    return None


def check_quickbooks() -> None:
    print("\n5. QuickBooks sandbox (read-only)")
    if shutil.which("node") is None:
        record("FAIL", "node is on PATH", "install Node.js, then `npm install && npm run build` "
                                          "in quickbooks-online-mcp-server")
        return
    if not SERVER_ENTRY.exists():
        record("FAIL", "MCP server is built", f"{SERVER_ENTRY.relative_to(ROOT)} is missing. Run "
                                              f"`npm install && npm run build` in "
                                              f"quickbooks-online-mcp-server")
        return
    client = None
    start = time.time()
    try:
        client = Stdio()
        init = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "preflight", "version": "0"}})
        client.notify("notifications/initialized")
        tools = client.request("tools/list", {})["result"]["tools"]
        names = {t["name"] for t in tools}
        record("PASS" if "get_company_info" in names else "FAIL",
               "MCP server starts and lists tools", f"{len(names)} tools registered "
               f"({sum(n.startswith(('get', 'search', 'read')) for n in names)} read-type)")
        reply = client.request("tools/call", {"name": "get_company_info",
                                              "arguments": {"params": {}}}, timeout=120)
        result = reply.get("result") or {}
        error = (json.dumps(reply["error"]) if "error" in reply else
                 " ".join(c.get("text", "") for c in result.get("content") or [])
                 if result.get("isError") else "")
        if error:
            record("FAIL", "read-only get_company_info", error[:300], time.time() - start)
            if AUTH_FAILURE.search(error):
                print(REAUTH_STEPS)
            return
        name = find_value(result, "CompanyName")
        record("PASS" if name else "FAIL", "read-only get_company_info returns the company",
               f"CompanyName: {name}" if name else "reply had no CompanyName",
               time.time() - start)
    except Exception as exc:                      # noqa: BLE001 - report, never crash
        record("FAIL", "QuickBooks check", f"{type(exc).__name__}: {str(exc)[:200]}")
        if AUTH_FAILURE.search(str(exc)):
            print(REAUTH_STEPS)
    finally:
        if client:
            client.close()


# ---- 6. Claude Code discovery -----------------------------------------------------------

def check_claude(no_call: bool) -> None:
    print("\n6. Claude Code discovers the QuickBooks server and the skill")
    try:
        from evals.run_skill_evals import find_claude
        exe = find_claude()
    except SystemExit as exc:
        record("FAIL", "claude executable found", str(exc))
        return
    record("PASS", "claude executable found", Path(exe).name)

    skill_md = ROOT / ".claude" / "skills" / SKILL / "SKILL.md"
    head = skill_md.read_text(encoding="utf-8").split("---")[1] if skill_md.exists() else ""
    ok = f"name: {SKILL}" in head and "description:" in head
    record("PASS" if ok else "FAIL", "SKILL.md has name and description frontmatter",
           str(skill_md.relative_to(ROOT)))

    start = time.time()
    proc = subprocess.run([exe, "mcp", "list"], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120,
                          stdin=subprocess.DEVNULL)
    line = next((l for l in proc.stdout.splitlines() if "quickbooks" in l.lower()), "")
    connected = "connected" in line.lower() and "fail" not in line.lower()
    record("PASS" if connected else "FAIL", "`claude mcp list` shows quickbooks connected",
           line.strip()[:160] or "quickbooks not listed. Check .mcp.json and approve the "
                                 "server once via /mcp in Claude Code", time.time() - start)

    if no_call:
        record("SKIP", "headless session lists the skill", "--no-claude-call given")
        return
    start = time.time()
    proc = subprocess.run(
        [exe, "-p", "Reply with the single word OK.", "--output-format", "stream-json",
         "--verbose", "--max-turns", "1", "--strict-mcp-config"], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=180, stdin=subprocess.DEVNULL)
    skills, cost = [], None
    for raw in proc.stdout.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            skills = event.get("skills") or []
        if event.get("type") == "result":
            cost = event.get("total_cost_usd")
    record("PASS" if SKILL in skills else "FAIL", f"a Claude Code session lists '{SKILL}'",
           f"{len(skills)} skills visible" + (f"; this check used ${cost:.3f} API-equivalent"
                                             if cost else ""), time.time() - start)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true", help="skip the full pytest run")
    ap.add_argument("--no-claude-call", action="store_true",
                    help="do not start a headless Claude session (no Pro usage)")
    args = ap.parse_args()

    print(f"Preflight, {time.strftime('%Y-%m-%d %H:%M:%S')}, repo {ROOT}")
    check_environment(args.quick)
    print("\n2. Original workbook")
    check_workbook_hash("workbook hash unchanged")
    check_engine()
    print("\n4. Original workbook, after the engine ran")
    check_workbook_hash("workbook hash still unchanged")
    check_quickbooks()
    check_claude(args.no_claude_call)

    failed = [r for r in results if r[0] == "FAIL"]
    skipped = [r for r in results if r[0] == "SKIP"]
    print(f"\n{len(results) - len(failed) - len(skipped)} passed, {len(failed)} failed, "
          f"{len(skipped)} skipped.")
    if failed:
        print("NOT READY. Fix these before the call:")
        for _, name, _ in failed:
            print(f"  - {name}")
        return 1
    print("READY." + (" (Some checks were skipped; see above.)" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
