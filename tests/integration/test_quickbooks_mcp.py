"""QuickBooks MCP connection. Kept apart from the deterministic Excel tests.

Three tiers, so the default `python -m pytest -q` never needs QuickBooks:

* **Config safety** — always runs, no network: the project's Claude Code settings deny
  every QuickBooks write tool, `.mcp.json` points at the local server, and the skill does
  not pre-approve any QuickBooks tool.
* **Server** — `QBO_INTEGRATION=1`: starts the local MCP server over stdio and lists its
  tools. No QuickBooks API call is made.
* **Live sandbox read** — `QBO_INTEGRATION=1 QBO_LIVE=1`: calls `get_company_info`
  against the sandbox. This uses (and may refresh) the stored OAuth tokens.

Nothing here prints a token or a credential. finance_engine has no QuickBooks code; these
tests cover the Claude Code side of the connection only.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = ROOT / "quickbooks-online-mcp-server"
SERVER_ENTRY = SERVER_DIR / "dist" / "index.js"
WRITE_VERBS = ("create", "update", "delete")

pytestmark = pytest.mark.quickbooks


# ---- config safety (always runs) ------------------------------------------------

def test_project_settings_deny_every_quickbooks_write():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    perms = settings["permissions"]
    for verb in WRITE_VERBS:
        assert f"mcp__quickbooks__{verb}_*" in perms["deny"]
        assert f"mcp__quickbooks__{verb}-*" in perms["deny"]
    for allowed in perms["allow"]:
        if allowed.startswith("mcp__quickbooks__"):
            assert not any(v in allowed for v in WRITE_VERBS), allowed


def test_mcp_config_runs_the_local_server_without_inline_secrets():
    cfg = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    server = cfg["mcpServers"]["quickbooks"]
    assert server["command"] == "node"
    assert server["args"] == ["./quickbooks-online-mcp-server/dist/index.js"]
    assert server.get("env", {}) == {}, "credentials must come from the server's own .env"


def test_skill_does_not_preapprove_quickbooks_tools():
    text = (ROOT / ".claude" / "skills" / "monthly-financial-analysis" / "SKILL.md") \
        .read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---")[1])
    assert "mcp__quickbooks" not in str(front.get("allowed-tools", ""))


# ---- live tiers (opt-in) ------------------------------------------------------------

needs_server = pytest.mark.skipif(
    os.environ.get("QBO_INTEGRATION") != "1" or shutil.which("node") is None
    or not SERVER_ENTRY.exists(),
    reason="set QBO_INTEGRATION=1 with node installed and the server built "
           "(quickbooks-online-mcp-server/dist/index.js)")
needs_live = pytest.mark.skipif(os.environ.get("QBO_LIVE") != "1",
                                reason="set QBO_LIVE=1 to call the QuickBooks sandbox")


class _Stdio:
    """Minimal newline-delimited JSON-RPC client for an MCP stdio server."""

    def __init__(self):
        self.proc = subprocess.Popen(
            [shutil.which("node"), str(SERVER_ENTRY)], cwd=SERVER_DIR,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8")
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.next_id = 0

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)

    def request(self, method: str, params: dict | None = None, timeout: float = 60):
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method,
                    "params": params or {}})
        while True:
            msg = json.loads(self.lines.get(timeout=timeout))
            if msg.get("id") == self.next_id:
                return msg

    def notify(self, method: str):
        self._send({"jsonrpc": "2.0", "method": method})

    def _send(self, msg: dict):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def close(self):
        self.proc.kill()


@pytest.fixture(scope="module")
def server():
    client = _Stdio()
    init = client.request("initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"}})
    assert "result" in init, init
    client.notify("notifications/initialized")
    yield client
    client.close()


@needs_server
def test_server_lists_the_read_tools_the_project_allows(server):
    tools = {t["name"] for t in server.request("tools/list")["result"]["tools"]}
    for name in ("get_company_info", "get_profit_and_loss", "get_balance_sheet",
                 "search_accounts"):
        assert name in tools


AUTH_FAILURE = re.compile(r"\b401\b|unauthori[sz]ed|invalid_grant|invalid_token|"
                          r"token (has )?expired|expired token|refresh token", re.I)


def _error_text(reply: dict) -> str:
    """The server's own error text, from a JSON-RPC error or an isError tool result."""
    if "error" in reply:
        return json.dumps(reply["error"])
    result = reply.get("result") or {}
    if result.get("isError"):
        return " ".join(c.get("text", "") for c in result.get("content") or []) \
            or json.dumps(result)
    return ""


def _failure_message(error: str) -> str:
    message = f"get_company_info failed: {error[:500]}"
    if AUTH_FAILURE.search(error):
        message += ("\nThis looks like an OAuth token problem: run `npm run auth` in "
                    "quickbooks-online-mcp-server to re-authorise the sandbox.")
    return message


@needs_server
def test_get_company_info_takes_a_params_object(server):
    """The call shape below follows the server's own schema, not a guess."""
    tools = {t["name"]: t for t in server.request("tools/list")["result"]["tools"]}
    schema = tools["get_company_info"]["inputSchema"]
    assert schema.get("required") == ["params"]
    assert schema["properties"]["params"]["type"] == "object"


@needs_server
@needs_live
def test_live_sandbox_read_returns_the_company(server):
    reply = server.request("tools/call",
                           {"name": "get_company_info", "arguments": {"params": {}}},
                           timeout=120)
    error = _error_text(reply)
    assert not error, _failure_message(error)
    assert "CompanyName" in json.dumps(reply["result"]), \
        f"no CompanyName in the reply: {json.dumps(reply['result'])[:500]}"
