"""Shared plumbing for the eval runners: cases, CLI calls, results and baselines.

Results files are plain JSON so a baseline can be diffed by eye as well as by
`compare_results`. A baseline is a results file copied into `evals/baseline/` with the
date and Git commit in its name; the newest one of each kind is the default comparison.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "evals" / "cases"
RUNS_DIR = ROOT / "evals" / "runs"
BASELINE_DIR = ROOT / "evals" / "baseline"

PASS, FAIL, SKIP = "pass", "fail", "skip"


# ---- cases ---------------------------------------------------------------

def load_cases(selected: list[str] | None = None) -> list[dict]:
    """Every case, or those whose id starts with one of `selected` ("04" works)."""
    cases = []
    for d in sorted(CASES_DIR.iterdir()):
        if not (d / "case.yaml").exists():
            continue
        if selected and not any(d.name.startswith(s) for s in selected):
            continue
        case = yaml.safe_load((d / "case.yaml").read_text(encoding="utf-8"))
        expected_path = d / "expected.json"
        case["_dir"] = d
        case["_expected"] = (json.loads(expected_path.read_text(encoding="utf-8"))
                             if expected_path.exists() else None)
        cases.append(case)
    if selected and not cases:
        raise SystemExit(f"No case matches {selected}. Cases live in {CASES_DIR}.")
    return cases


def workbook_fingerprint(path: Path) -> str:
    """SHA-256 of a workbook's cell contents: every sheet, cell, formula and cached value.

    Used for the generated test workbooks (cases 14 to 18). openpyxl writes the creation
    time into the file, so regenerating identical cells gives different bytes; the cells
    are what the engine reads, and this is what the case records. openpyxl only.
    """
    import openpyxl

    h = hashlib.sha256()
    for data_only in (False, True):
        wb = openpyxl.load_workbook(path, data_only=data_only, read_only=True)
        try:
            for ws in wb.worksheets:
                h.update(f"\x00sheet:{ws.title}".encode())
                for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    for c, value in enumerate(row, start=1):
                        if value is None:
                            continue
                        text = value.isoformat() if hasattr(value, "isoformat") else repr(value)
                        h.update(f"\x00{r},{c}:{type(value).__name__}:{text}".encode())
        finally:
            wb.close()
    return h.hexdigest()


def ensure_generated_workbooks(root: Path | None = None) -> list[str]:
    """Write any missing workbook that scripts/make_test_workbooks.py generates.

    data/clients/ is git-ignored, so a fresh clone has none of them. Existing files are
    left alone. Returns the paths written.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_test_workbooks

    written = make_test_workbooks.ensure(root or ROOT)
    for path in written:
        print(f"generated missing test workbook: {path}")
    return written


def scope_args(inp: dict) -> list[str]:
    """The CLI flags that select a case's scope and workbook.

    A month case (no `scope`) gives `--period P`, exactly as before. `scope: year` gives
    `--scope year --year Y`, `scope: all` gives `--scope all`, and `file:` adds
    `--file NAME` (`--workbook NAME` for check-commentary, via `workbook_flag`).
    """
    scope = inp.get("scope", "month")
    if scope == "year":
        args = ["--scope", "year", "--year", str(inp["year"])]
    elif scope == "all":
        args = ["--scope", "all"]
    else:
        args = ["--period", str(inp["period"])]
    if inp.get("file"):
        args += ["--file", inp["file"]]
    return args


def output_rel(inp: dict) -> Path:
    """Where a case's outputs land, relative to the output directory."""
    scope = inp.get("scope", "month")
    leaf = {"year": f"year-{inp.get('year')}", "all": "all"}.get(scope, inp.get("period"))
    base = Path(inp["client_id"])
    if inp.get("file"):
        base = base / Path(inp["file"]).stem
    return base / str(leaf)


# ---- processes -----------------------------------------------------------

def run_engine(args: list[str], out_dir: Path, root: Path | None = None,
               env_extra: dict | None = None, timeout: int = 300):
    """`python -m finance_engine <args>` with output redirected into `out_dir`."""
    env = dict(os.environ)
    env["FINANCE_OUTPUT_DIR"] = str(out_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("LLM_PROVIDER", None)
    env.update(env_extra or {})
    cmd = [sys.executable, "-m", "finance_engine", *args]
    if root is not None:
        cmd += ["--root", str(root)]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout, proc.stderr


def git_commit() -> tuple[str, bool]:
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                    capture_output=True, text=True).stdout.strip())
        return head or "unknown", dirty
    except OSError:
        return "unknown", True


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def new_run_dir(kind: str) -> tuple[str, Path]:
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = RUNS_DIR / f"{kind}_{run_id}"
    path.mkdir(parents=True, exist_ok=True)
    return run_id, path


# ---- checks and results ----------------------------------------------------

class CaseResult:
    def __init__(self, case_id: str):
        self.case_id = case_id
        self.checks: list[dict] = []
        self.values: dict[str, object] = {}
        self.skipped: str | None = None
        self.extra: dict = {}

    def check(self, check_id: str, passed: bool, detail: str = "") -> bool:
        self.checks.append({"id": check_id, "passed": bool(passed), "detail": detail})
        return passed

    def skip(self, reason: str) -> "CaseResult":
        self.skipped = reason
        return self

    @property
    def status(self) -> str:
        if self.skipped:
            return SKIP
        return PASS if self.checks and all(c["passed"] for c in self.checks) else FAIL

    def to_dict(self) -> dict:
        out = {"status": self.status, "checks": self.checks, "values": self.values}
        if self.skipped:
            out["skip_reason"] = self.skipped
        out.update(self.extra)
        return out


def results_doc(kind: str, run_id: str, results: list[CaseResult], **meta) -> dict:
    commit, dirty = git_commit()
    statuses = [r.status for r in results]
    return {
        "kind": kind,
        "run_id": run_id,
        "date_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        **meta,
        "summary": {"passed": statuses.count(PASS), "failed": statuses.count(FAIL),
                    "skipped": statuses.count(SKIP), "total": len(statuses)},
        "cases": {r.case_id: r.to_dict() for r in results},
    }


def print_results(doc: dict) -> None:
    for case_id, res in doc["cases"].items():
        failed = [c for c in res["checks"] if not c["passed"]]
        mark = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[res["status"]]
        print(f"  {mark}  {case_id}  ({len(res['checks']) - len(failed)}"
              f"/{len(res['checks'])} checks)"
              + (f"  {res.get('skip_reason')}" if res["status"] == SKIP else ""))
        for c in failed:
            print(f"          x {c['id']}: {c['detail']}")
    s = doc["summary"]
    print(f"\n{s['passed']} passed, {s['failed']} failed, {s['skipped']} skipped "
          f"(commit {doc['git_commit']}{' + uncommitted changes' if doc['git_dirty'] else ''})")


def save_results(doc: dict, run_dir: Path) -> Path:
    path = run_dir / "results.json"
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def save_baseline(doc: dict) -> Path:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    date = doc["date_utc"][:10]
    label = doc.get("baseline_label") or doc["kind"]
    # "-dirty" when the tree had uncommitted changes: the commit alone would not
    # reproduce this result.
    commit = doc["git_commit"] + ("-dirty" if doc.get("git_dirty") else "")
    path = BASELINE_DIR / f"{label}_{date}_{commit}.json"
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def latest_baseline(label: str) -> Path | None:
    found = sorted(BASELINE_DIR.glob(f"{label}_*.json"))
    return found[-1] if found else None


def compare_results(current: dict, baseline: dict, rel_tol: float = 1e-9) -> dict:
    """Newly failing / newly passing cases, check flips, and changed values."""
    out = {"baseline_commit": baseline.get("git_commit"),
           "baseline_date": baseline.get("date_utc"),
           "newly_failing": [], "newly_passing": [], "check_flips": [],
           "changed_values": [], "new_cases": [], "missing_cases": []}
    cur, base = current["cases"], baseline["cases"]
    for case_id in sorted(set(cur) | set(base)):
        if case_id not in base:
            out["new_cases"].append(case_id)
            continue
        if case_id not in cur:
            out["missing_cases"].append(case_id)
            continue
        c, b = cur[case_id], base[case_id]
        if b["status"] == PASS and c["status"] == FAIL:
            out["newly_failing"].append(case_id)
        if b["status"] == FAIL and c["status"] == PASS:
            out["newly_passing"].append(case_id)
        base_checks = {x["id"]: x["passed"] for x in b.get("checks", [])}
        for x in c.get("checks", []):
            if x["id"] in base_checks and base_checks[x["id"]] != x["passed"]:
                out["check_flips"].append({
                    "case": case_id, "check": x["id"],
                    "was": "pass" if base_checks[x["id"]] else "fail",
                    "now": "pass" if x["passed"] else "fail", "detail": x["detail"]})
        for metric, value in c.get("values", {}).items():
            old = b.get("values", {}).get(metric, "<absent>")
            if not _same(old, value, rel_tol):
                out["changed_values"].append({"case": case_id, "metric": metric,
                                              "baseline": old, "now": value})
    out["regressed"] = bool(out["newly_failing"] or any(
        f["now"] == "fail" for f in out["check_flips"]))
    return out


def _same(a, b, rel_tol: float) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= max(1e-9, rel_tol * max(abs(a), abs(b)))
    return a == b


def print_comparison(cmp: dict) -> None:
    print(f"\nCompared with baseline {cmp['baseline_commit']} ({cmp['baseline_date']}):")
    if not any(cmp[k] for k in ("newly_failing", "newly_passing", "check_flips",
                                "changed_values", "new_cases", "missing_cases")):
        print("  no change.")
        return
    for key, title in (("newly_failing", "NEWLY FAILING"),
                       ("newly_passing", "newly passing"),
                       ("new_cases", "new cases"), ("missing_cases", "missing cases")):
        if cmp[key]:
            print(f"  {title}: {', '.join(cmp[key])}")
    for f in cmp["check_flips"]:
        print(f"  {f['case']}: {f['check']} {f['was']} -> {f['now']}  {f['detail']}")
    for v in cmp["changed_values"][:40]:
        print(f"  {v['case']}: {v['metric']} {v['baseline']} -> {v['now']}")
    if len(cmp["changed_values"]) > 40:
        print(f"  ... {len(cmp['changed_values']) - 40} more changed values")
