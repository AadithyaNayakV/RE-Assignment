"""Level 2 (and optional Level 3): grade the report the skill writes.

    python evals/run_skill_evals.py 04                     # one case through Claude Code
    python evals/run_skill_evals.py                        # every case (uses Pro usage)
    python evals/run_skill_evals.py --recheck evals/runs/<run>   # re-grade, no Claude call
    python evals/run_skill_evals.py --provider deterministic   # free control run, no Claude
    python evals/run_skill_evals.py 01 --rubric            # add the Level 3 rubric score
    python evals/run_skill_evals.py --compare / --save-baseline

For each case this runs Claude Code headless:

    claude -p "Use the monthly-financial-analysis skill for client <id>, period <YYYY-MM>."

with only the tools the skill needs (`python -m finance_engine ...`, Read, Write, Edit,
Glob, Skill) and no MCP servers. It then reads the report.md the skill wrote, and applies
the deterministic checks in `evals/report_checks.py`. The transcript, report and
metrics.json of every run are copied into `evals/runs/` for inspection.

`--provider deterministic` applies the same checks to the template provider's report.
That is the control: it costs nothing, and a check that fails on the template is a
broken check.

Claude runs one case at a time, because they share a Pro plan's usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.common import (ROOT, SKIP, CaseResult, compare_results, latest_baseline,  # noqa: E402
                          load_cases, new_run_dir, output_rel, print_comparison,
                          print_results, results_doc, run_engine, save_baseline,
                          save_results, scope_args, ensure_generated_workbooks)
from evals.report_checks import check_report, other_clients  # noqa: E402

SKILL = "monthly-financial-analysis"
ALLOWED_TOOLS = ",".join(["Bash(python -m finance_engine:*)",
                          "PowerShell(python -m finance_engine:*)",
                          "Read", "Write", "Edit", "Glob", "Skill"])
OUTPUT = ROOT / "output" / "clients"
CONFIG_DIR = ROOT / "config" / "clients"


# ---- Claude Code headless ------------------------------------------------------

def find_claude(explicit: str | None = None) -> str:
    """--claude-bin, $CLAUDE_BIN, PATH, then the usual Windows install locations."""
    for candidate in (explicit, os.environ.get("CLAUDE_BIN"), shutil.which("claude")):
        if candidate and Path(candidate).exists():
            return candidate
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    home = Path.home()
    for pattern_root, pattern in (
            (local / "Microsoft" / "WinGet" / "Packages", "Anthropic.ClaudeCode_*/claude.exe"),
            (home / ".local" / "bin", "claude.exe"),
            (Path(os.environ.get("APPDATA", "")) / "npm", "claude.cmd")):
        found = sorted(pattern_root.glob(pattern)) if pattern_root.exists() else []
        if found:
            return str(found[-1])
    raise SystemExit("Claude Code not found. Pass --claude-bin or set CLAUDE_BIN, or use "
                     "--provider deterministic.")


CLAUDE_BIN: str | None = None


def run_claude(prompt: str, transcript: Path, timeout: int, model: str | None,
               tools: str = ALLOWED_TOOLS) -> dict:
    exe = CLAUDE_BIN or find_claude()
    # Only project settings and an explicit permission mode, so a run is graded on the
    # tools the skill declares and not on whatever the operator's user settings allow.
    cmd = [exe, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--setting-sources", "project", "--permission-mode", "default",
           "--strict-mcp-config", "--allowedTools", tools, "--max-turns", "60"]
    if model:
        cmd += ["--model", model]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              stdin=subprocess.DEVNULL)
        stdout, stderr, timed_out = proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) \
            else (exc.stdout or "")
        stderr, timed_out = "timed out", True
    transcript.write_text(stdout, encoding="utf-8")
    return _parse_stream(stdout, stderr, timed_out, time.time() - started)


def _parse_stream(stdout: str, stderr: str, timed_out: bool, seconds: float) -> dict:
    info = {"skills": [], "tool_uses": [], "result": "", "cost_usd": None,
            "num_turns": None, "is_error": timed_out, "timed_out": timed_out,
            "seconds": round(seconds, 1), "model": None, "stderr": stderr[-500:]}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            info["skills"] = event.get("skills") or []
            info["model"] = event.get("model")
        elif event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    info["tool_uses"].append({"name": block.get("name"),
                                              "input": block.get("input") or {}})
        elif event.get("type") == "result":
            info["result"] = event.get("result") or ""
            info["cost_usd"] = event.get("total_cost_usd")
            info["num_turns"] = event.get("num_turns")
            info["is_error"] = bool(event.get("is_error")) or timed_out
    return info


def _bash_commands(info: dict) -> list[str]:
    """Shell commands, whichever shell tool ran them (Windows uses PowerShell)."""
    return [u["input"].get("command", "") for u in info["tool_uses"]
            if u["name"] in ("Bash", "PowerShell")]


def _skill_invoked(info: dict) -> bool:
    return any(u["name"] == "Skill" and SKILL in json.dumps(u["input"])
               for u in info["tool_uses"])


def _record_process(r: CaseResult, info: dict) -> None:
    r.check("skill_discovered", SKILL in info["skills"],
            f"skills at start-up: {info['skills'][:6]}...")
    r.check("skill_invoked", _skill_invoked(info), "a Skill tool call naming the skill")
    r.check("claude_completed", not info["is_error"],
            "timed out" if info["timed_out"] else info["stderr"][-200:])
    r.extra["claude"] = {k: info[k] for k in ("model", "cost_usd", "num_turns", "seconds")}
    r.extra["claude"]["bash_commands"] = _bash_commands(info)
    r.extra["claude"]["read_raw_metrics_json"] = any(
        u["name"] == "Read" and str(u["input"].get("file_path", "")).endswith("metrics.json")
        for u in info["tool_uses"])
    r.extra["claude"]["final_reply"] = info["result"][-1500:]


# ---- one case --------------------------------------------------------------------

def evaluate(case: dict, provider: str, run_dir: Path, args) -> CaseResult:
    r = CaseResult(case["id"])
    spec = case.get("skill") or {}
    if not spec.get("run"):
        return r.skip("case has no skill evaluation")
    case_dir = run_dir / case["id"]
    case_dir.mkdir(parents=True, exist_ok=True)

    if spec.get("stop_runs"):
        if provider != "claude":
            return r.skip("stop behaviour is the skill's; covered by Level 1 for the CLI")
        return _stop_runs(case, r, case_dir, args)

    inp = case["input"]
    if not (ROOT / inp["workbook"]).exists():
        return r.skip(f"workbook not present: {inp['workbook']}")

    mode = "deterministic" if (provider == "deterministic"
                               or spec.get("mode") == "deterministic") else "skill"
    if mode == "deterministic":
        out = run_dir / "out"
        env = (case.get("engine") or {}).get("env") or {}
        args_ = ["report", "--client", inp["client_id"], *scope_args(inp)]
        if not env:
            args_ += ["--provider", "deterministic"]
        code, _, stderr = run_engine(args_, out, env_extra=env)
        r.check("report_command", code in (0, 2), f"exit {code}: {stderr.strip()[:200]}")
        report = out / output_rel(inp) / "report.md"
        metrics = report.with_name("metrics.json")
    else:
        report = OUTPUT / output_rel(inp) / "report.md"
        metrics = report.with_name("metrics.json")
        prompt = spec.get("prompt") or (f"Use the {SKILL} skill for client "
                                        f"{inp['client_id']}, period {inp['period']}.")
        started = time.time()
        info = run_claude(prompt, case_dir / "transcript.jsonl", args.timeout, args.model)
        _record_process(r, info)
        cmds = " ".join(_bash_commands(info))
        r.check("ran_analyze", "finance_engine analyze" in cmds, "skill step 2")
        r.check("ran_check_commentary", "check-commentary" in cmds, "skill step 8")
        fresh = report.exists() and report.stat().st_mtime >= started
        if not r.check("report_written_this_run", fresh, str(report)):
            return r
        r.check("metrics_written_this_run",
                metrics.exists() and metrics.stat().st_mtime >= started, str(metrics))

    if not report.exists() or not metrics.exists():
        r.check("report_exists", False, str(report))
        return r
    shutil.copy(report, case_dir / "report.md")
    shutil.copy(metrics, case_dir / "metrics.json")
    text = report.read_text(encoding="utf-8")
    doc = json.loads(metrics.read_text(encoding="utf-8"))
    for c in check_report(text, doc, case, mode,
                          other_clients(CONFIG_DIR, inp["client_id"])):
        r.check(c["id"], c["passed"], c["detail"])
    guard = next(c for c in r.checks if c["id"] == "guard")
    r.values["guard"] = guard["detail"].split(";")[0]
    r.values["report_chars"] = len(text)

    if args.rubric:
        r.extra["rubric"] = rubric_score(case_dir / "report.md", case_dir, args)
    return r


def recheck(case: dict, old_run: Path, old_results: dict) -> CaseResult:
    """Re-grade a saved run with the current checks. No Claude call, no usage.

    Used after a *checker* change, so the same report can be graded again. The
    freshness checks cannot be re-derived from files, so they are carried over from the
    original results.
    """
    r = CaseResult(case["id"])
    saved = old_run / case["id"]
    old = (old_results.get("cases") or {}).get(case["id"]) or {}
    if not (saved / "report.md").exists():
        if old.get("checks"):
            # Stop runs (case 12) leave no report to re-grade; their checks stand.
            for c in old["checks"]:
                r.check(c["id"], c["passed"], c["detail"] + " (from the original run)")
            r.extra["claude"] = old.get("claude", {})
            return r
        return r.skip(old.get("skip_reason") or "no saved report in that run")
    transcript = saved / "transcript.jsonl"
    if transcript.exists():
        info = _parse_stream(transcript.read_text(encoding="utf-8"), "", False, 0)
        _record_process(r, info)
        cmds = " ".join(_bash_commands(info))
        r.check("ran_analyze", "finance_engine analyze" in cmds, "skill step 2")
        r.check("ran_check_commentary", "check-commentary" in cmds, "skill step 8")
    for c in old.get("checks", []):
        if c["id"] in ("report_written_this_run", "metrics_written_this_run",
                       "report_command"):
            r.check(c["id"], c["passed"], c["detail"] + " (from the original run)")
    text = (saved / "report.md").read_text(encoding="utf-8")
    doc = json.loads((saved / "metrics.json").read_text(encoding="utf-8"))
    mode = "deterministic" if not transcript.exists() else "skill"
    for c in check_report(text, doc, case, mode,
                          other_clients(CONFIG_DIR, case["input"]["client_id"])):
        r.check(c["id"], c["passed"], c["detail"])
    r.extra["rechecked_from"] = str(old_run)
    return r


def _stop_runs(case: dict, r: CaseResult, case_dir: Path, args) -> CaseResult:
    for run in case["skill"]["stop_runs"]:
        rid = run["id"]
        report = OUTPUT / run["client_id"] / run["period"] / "report.md"
        client_dir_existed = (OUTPUT / run["client_id"]).exists()
        started = time.time()
        info = run_claude(run["prompt"], case_dir / f"{rid}.transcript.jsonl",
                          args.timeout, args.model)
        r.check(f"{rid}:skill_invoked", _skill_invoked(info), "Skill tool call")
        r.check(f"{rid}:no_report_written",
                not (report.exists() and report.stat().st_mtime >= started), str(report))
        if not client_dir_existed:
            r.check(f"{rid}:no_output_for_unknown_client",
                    not (OUTPUT / run["client_id"]).exists(),
                    str(OUTPUT / run["client_id"]))
        r.check(f"{rid}:reply_explains_stop",
                re.search(run["reply_must_match"], info["result"], re.I) is not None,
                f"reply: {info['result'][:300]!r}")
        r.extra.setdefault("claude", {})[rid] = {
            k: info[k] for k in ("model", "cost_usd", "num_turns", "seconds", "result")}
    return r


# ---- Level 3: optional rubric ---------------------------------------------------

def rubric_text() -> str:
    grading = (ROOT / "evals" / "grading.md").read_text(encoding="utf-8")
    m = re.search(r"<!-- rubric:start -->(.*?)<!-- rubric:end -->", grading, re.S)
    return m.group(1).strip() if m else ""


def rubric_score(report: Path, case_dir: Path, args) -> dict:
    """Ask Claude, read-only, to score clarity and usefulness. Never a pass/fail input."""
    prompt = (
        "You are grading a draft monthly financial report for a human reviewer. Do not "
        "use any skill. Read only the file "
        f"{report.relative_to(ROOT).as_posix()}. Apply this rubric:\n\n{rubric_text()}\n\n"
        "Reply with only a JSON object: {\"clarity\": 1-5, \"usefulness\": 1-5, "
        "\"rationale\": \"<two sentences>\"}.")
    info = run_claude(prompt, case_dir / "rubric.transcript.jsonl", args.timeout,
                      args.model, tools="Read")
    m = re.search(r"\{.*\}", info["result"], re.S)
    try:
        scores = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        scores = {}
    scores["judge_cost_usd"] = info["cost_usd"]
    return scores or {"error": info["result"][:300]}


# ---- driver ----------------------------------------------------------------------

def print_table(doc: dict, run_dir: Path) -> None:
    """One row per case, then where everything was saved."""
    rows = [("Case", "Result", "Checks", "Report written by", "Claude cost (USD)")]
    for case_id, res in doc["cases"].items():
        checks = res["checks"]
        claude = res.get("claude") or {}
        cost = claude.get("cost_usd")
        rows.append((case_id, res["status"].upper(),
                     f"{sum(c['passed'] for c in checks)}/{len(checks)}" if checks else "-",
                     "-" if res["status"] == SKIP else ("Claude" if claude else "template"),
                     "-" if cost is None else f"{cost:.2f}"))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    print()
    for n, row in enumerate(rows):
        print("  " + "  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
        if n == 0:
            print("  " + "  ".join("-" * w for w in widths))
    s = doc["summary"]
    print(f"\nRESULT: {'PASS' if not s['failed'] else 'FAIL'} - {s['passed']} passed, "
          f"{s['failed']} failed, {s['skipped']} skipped")
    print(f"Saved: {run_dir / 'results.json'}")
    print(f"       each case's report.md, metrics.json and transcript: "
          f"{run_dir}{os.sep}<case>")

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cases", nargs="*", help="case id prefixes; default all")
    ap.add_argument("--provider", choices=["claude", "deterministic"], default="claude")
    ap.add_argument("--model", default=None, help="passed to claude --model")
    ap.add_argument("--claude-bin", default=None, help="path to the claude executable")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per Claude run")
    ap.add_argument("--rubric", action="store_true", help="add the Level 3 rubric score")
    ap.add_argument("--recheck", type=Path, default=None, metavar="RUN_DIR",
                    help="re-grade the reports saved in an earlier run; no Claude call")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--baseline", type=Path, default=None)
    ap.add_argument("--save-baseline", action="store_true")
    args = ap.parse_args(argv)

    global CLAUDE_BIN
    if args.provider == "claude" and not args.recheck:
        CLAUDE_BIN = find_claude(args.claude_bin)
    label = "skill" if args.provider == "claude" else "skill_deterministic"
    run_id, run_dir = new_run_dir(label)
    print(f"Level 2 report evals ({args.provider}) -> {run_dir}")
    if args.provider == "claude" and not args.recheck:
        print("  Calls Claude Code headless, one case at a time. This uses your Claude Pro "
              "usage;\n  the API-equivalent cost is printed at the end.")
    if not args.recheck:
        ensure_generated_workbooks()
    results = []
    old_results = {}
    if args.recheck:
        old_results = json.loads((args.recheck / "results.json").read_text(encoding="utf-8"))
        print(f"  re-grading saved reports from {args.recheck}")
    for case in load_cases(args.cases):
        print(f"  running {case['id']} ...", flush=True)
        if args.recheck:
            results.append(recheck(case, args.recheck, old_results))
        else:
            results.append(evaluate(case, args.provider, run_dir, args))
    doc = results_doc("skill", run_id, results, baseline_label=label,
                      provider=args.provider)
    costs = [c.get("claude", {}).get("cost_usd") for c in doc["cases"].values()]
    doc["claude_cost_usd_equivalent"] = round(sum(x for x in costs if x), 4)
    print_results(doc)
    if args.recheck:
        doc["rechecked_from"] = str(args.recheck)
        print("Re-graded saved reports: no Claude call was made in this run.")
    elif args.provider == "claude":
        print(f"Claude usage, API-equivalent cost reported by the CLI: "
              f"${doc['claude_cost_usd_equivalent']}")
    save_results(doc, run_dir)
    print_table(doc, run_dir)

    regressed = False
    if args.compare:
        path = args.baseline or latest_baseline(label)
        if path is None:
            print("\nNo baseline exists yet. Create one with --save-baseline.")
        else:
            cmp = compare_results(doc, json.loads(path.read_text(encoding="utf-8")))
            print_comparison(cmp)
            regressed = cmp["regressed"]
    if args.save_baseline:
        print(f"baseline saved: {save_baseline(doc)}")
    return 1 if (doc["summary"]["failed"] or regressed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
