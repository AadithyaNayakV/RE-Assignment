"""The preflight script's decision logic, tested without QuickBooks or Claude.

The live checks are exercised by running `python scripts/preflight_check.py`. These tests
cover the parts that only matter when something is broken: recognising an auth failure
(so the re-authorisation steps are printed) and refusing a changed workbook.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("preflight_check",
                                              ROOT / "scripts" / "preflight_check.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


@pytest.mark.parametrize("error", [
    "Request failed with status code 401",
    '{"error":"invalid_grant"}',
    "Token expired, please re-authorise",
    "Unauthorized",
    "the refresh token is invalid",
])
def test_auth_failures_trigger_the_reauth_steps(error):
    assert preflight.AUTH_FAILURE.search(error)


@pytest.mark.parametrize("error", [
    "Invalid arguments for tool get_company_info: Required at params",
    "ECONNREFUSED 127.0.0.1:8000",
    "timeout of 30000ms exceeded",
])
def test_other_failures_do_not_suggest_reauthorising(error):
    assert not preflight.AUTH_FAILURE.search(error)


def test_reauth_steps_name_the_commands_and_no_secret():
    steps = preflight.REAUTH_STEPS
    assert "npm run auth" in steps and "localhost:8000/callback" in steps
    assert "secret" not in steps.lower().replace("never paste a token", "")


def test_find_value_reads_json_inside_a_tool_result():
    result = {"content": [{"type": "text", "text": json.dumps(
        {"CompanyInfo": {"CompanyName": "Sandbox Company US 209f"}})}]}
    assert preflight.find_value(result, "CompanyName") == "Sandbox Company US 209f"
    assert preflight.find_value(result, "Nope") is None


def test_the_reference_hash_is_a_sha256():
    ref = json.loads((ROOT / "scripts" / "reference_hashes.json").read_text(encoding="utf-8"))
    assert len(ref["abc_inc_workbook_sha256"]) == 64


def test_a_changed_workbook_fails_the_hash_check(tmp_path, monkeypatch, capsys):
    fake = tmp_path / "ABC.xlsx"
    fake.write_bytes(b"not the original")
    monkeypatch.setattr(preflight, "workbook_path", lambda: fake)
    preflight.results.clear()
    preflight.check_workbook_hash("workbook hash unchanged")
    assert preflight.results[-1][0] == "FAIL"
    assert "DOES NOT MATCH" in preflight.results[-1][2]


def test_a_missing_workbook_is_a_skip_not_a_pass(monkeypatch):
    monkeypatch.setattr(preflight, "workbook_path", lambda: None)
    preflight.results.clear()
    preflight.check_workbook_hash("workbook hash unchanged")
    assert preflight.results[-1][0] == "SKIP"
