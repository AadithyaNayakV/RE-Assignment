"""Level 1: the engine against independently derived expected values. Free and fast.

    python evals/run_engine_evals.py                      # all cases
    python evals/run_engine_evals.py 04 06                # cases whose id starts 04 / 06
    python evals/run_engine_evals.py --compare            # also diff against the baseline
    python evals/run_engine_evals.py --save-baseline      # record this run as the baseline
    python evals/run_engine_evals.py --root <dir>         # run against another tree
                                                          # (used by regression_demo.py)

Each case runs the real CLI (`python -m finance_engine ...`) in a subprocess, writing into
`evals/runs/` rather than `output/`, then checks metrics.json against
`evals/cases/<case>/expected.json` and the structural expectations in `case.yaml`.
Exit status: 0 all pass, 1 any case failed (or regressed with --compare).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.common import (ROOT, CaseResult, compare_results, latest_baseline,  # noqa: E402
                          load_cases, new_run_dir, output_rel, print_comparison,
                          print_results, results_doc, run_engine, save_baseline,
                          save_results, scope_args, sha256, workbook_fingerprint,
                          ensure_generated_workbooks)


def evaluate(case: dict, out_dir: Path, root: Path | None) -> CaseResult:
    r = CaseResult(case["id"])
    eng = case.get("engine") or {}
    if eng.get("cli_checks"):
        base = root or ROOT
        missing = [p for p in eng.get("requires", []) if not (base / p).exists()]
        if missing:
            return r.skip(f"input not present: {', '.join(missing)}")
        if eng.get("prepare") == "round_trip_fixture":
            _prepare_round_trip_fixture(base)
        return _cli_checks(case, r, out_dir, root)

    inp, expected = case["input"], case["_expected"]
    base = root or ROOT
    workbook = base / inp["workbook"]
    if not workbook.exists():
        return r.skip(f"workbook not present: {inp['workbook']}")
    if expected is None:
        r.check("expected_json_present", False, "run evals/tools/build_expected.py")
        return r

    # A generated workbook is compared by its cells (its bytes carry a creation time);
    # a supplied or committed one by its bytes.
    if "input_content_sha256" in expected:
        actual = workbook_fingerprint(workbook)
        r.check("input_unchanged", actual == expected["input_content_sha256"],
                f"cell fingerprint {actual[:16]}... vs expected "
                f"{expected['input_content_sha256'][:16]}...")
    else:
        actual_sha = sha256(workbook)
        r.check("input_unchanged", actual_sha == expected["input_sha256"],
                f"workbook sha256 {actual_sha[:16]}... vs expected "
                f"{expected['input_sha256'][:16]}...")
    for rel, want in (expected.get("other_inputs_content_sha256") or {}).items():
        other = base / rel
        got = workbook_fingerprint(other) if other.exists() else "<missing>"
        r.check(f"input_unchanged:{Path(rel).name}", got == want,
                f"cell fingerprint {got[:16]}... vs expected {want[:16]}...")

    command = eng.get("command", "analyze")
    code, stdout, stderr = run_engine(
        [command, "--client", inp["client_id"], *scope_args(inp)],
        out_dir, root, eng.get("env"))
    r.check("exit_code", code == eng.get("exit_code", 0),
            f"exit {code}, expected {eng.get('exit_code', 0)}"
            + (f"; stderr: {stderr.strip()[:300]}" if code != eng.get("exit_code", 0) else ""))
    r.check("no_traceback", "Traceback" not in stderr, stderr.strip()[-300:])
    if eng.get("stderr"):
        r.check("stderr_message", re.search(eng["stderr"], stderr) is not None,
                f"stderr should match {eng['stderr']!r}")

    metrics_path = out_dir / output_rel(inp) / "metrics.json"
    if not r.check("metrics_written", metrics_path.exists(), str(metrics_path)):
        return r
    doc = json.loads(metrics_path.read_text(encoding="utf-8"))
    _check_values(r, doc, expected)
    _check_span(r, doc, eng, expected)

    ttm = doc.get("ttm") or {}
    if "ttm" in expected:                       # month cases; a year or file has no TTM
        r.check("ttm_window", ttm.get("window") == expected["ttm"]["window"],
                f"{ttm.get('window', ['?'])[0]}..{ttm.get('window', ['?'])[-1]}")
    if "ttm_complete" in eng:
        r.check("ttm_complete", ttm.get("complete") is eng["ttm_complete"],
                f"complete={ttm.get('complete')}, expected {eng['ttm_complete']}")

    recon = doc.get("reconciliation") or {}
    r.values["reconciliation.checks_failed"] = recon.get("checks_failed")
    if "reconciliation_passed" in eng:
        failures = "; ".join(f"{f.get('key')} {f.get('period')} diff {f.get('difference')}"
                             for f in (recon.get("failures") or [])[:5])
        r.check("reconciliation", recon.get("all_passed") is eng["reconciliation_passed"],
                f"{recon.get('checks_run', 0) - recon.get('checks_failed', 0)}"
                f"/{recon.get('checks_run')} passed" + (f"; {failures}" if failures else ""))
    ran = {c.get("key") for c in recon.get("by_check", [])}
    for key in eng.get("reconciliation_checks_include", []):
        passed = any(c.get("key") == key and c.get("passed")
                     for c in recon.get("by_check", []))
        r.check(f"reconciliation_check:{key}", passed,
                "ran and passed" if passed else f"checks run: {sorted(ran)}")

    issues = doc.get("data_quality_issues") or []
    codes = [i["code"] for i in issues]
    errors = [i["code"] for i in issues if i["severity"] == "error"]
    r.values["data_quality_codes"] = sorted(codes)
    if "max_errors" in eng:
        r.check("max_errors", len(errors) <= eng["max_errors"], f"errors: {errors}")
    for code_ in eng.get("required_codes", []):
        r.check(f"code_present:{code_}", code_ in codes, f"codes: {codes}")
    for code_ in eng.get("forbidden_codes", []):
        r.check(f"code_absent:{code_}", code_ not in codes, f"codes: {codes}")
    for code_, want in (eng.get("warning_rows") or {}).items():
        issue = next((i for i in issues if i["code"] == code_), None)
        rows = {row.get("label") for row in ((issue or {}).get("details") or {})
                .get("rows", [])}
        for label in want.get("includes", []):
            r.check(f"warning_names:{code_}:{label}", label in rows, f"rows: {sorted(rows)}")
        for label in want.get("excludes", []):
            r.check(f"warning_omits:{code_}:{label}", label not in rows,
                    f"rows: {sorted(rows)}")
        if want.get("message_excludes") and issue:
            r.check(f"warning_wording:{code_}",
                    re.search(want["message_excludes"], issue["message"], re.I) is None,
                    issue["message"][:200])

    # Cell-level warnings, against cells derived independently by build_expected.py.
    # Order-free: each enabled code must name exactly the expected cells; each disabled
    # code must not appear at all.
    cw = expected.get("cell_warnings")
    if cw:
        def key(c):
            return (c.get("sheet"), c.get("cell"), c.get("label"), c.get("period"),
                    c.get("raw_value"))
        for code_, on in cw["enabled"].items():
            issue = next((i for i in issues if i["code"] == code_), None)
            got = sorted(map(key, (issue or {}).get("cells", [])), key=str)
            if not on:
                r.check(f"cell_warning_off:{code_}", issue is None, f"codes: {codes}")
                continue
            want = sorted(map(key, cw["expected"].get(code_, [])), key=str)
            r.check(f"cell_warning:{code_}", got == want and (issue is None) == (not want)
                    and (issue is None or issue["severity"] == "warning"),
                    f"got {got}, expected {want}")

    mv = doc.get("movements") or {}
    sig = [x["label"] for x in mv.get("significant", [])]
    one = [x["label"] for x in mv.get("possible_one_offs", [])]
    win = [x["label"] for x in mv.get("ttm_window_one_offs", [])]
    r.values["significant"] = sig
    r.values["possible_one_offs"] = one
    for label in eng.get("significant_includes", []):
        r.check(f"significant:{label}", label in sig, f"significant: {sig}")
    if "significant_count" in eng:
        r.check("significant_count", len(sig) == eng["significant_count"],
                f"{len(sig)} significant: {sig}")
    for label in eng.get("possible_one_offs_include", []):
        r.check(f"possible_one_off:{label}", label in one, f"possible one-offs: {one}")
    if "possible_one_offs_count" in eng:
        r.check("possible_one_offs_count", len(one) == eng["possible_one_offs_count"],
                f"{len(one)}: {one}")
    for label in eng.get("ttm_window_one_offs_include", []):
        r.check(f"ttm_window_one_off:{label}", label in win, f"window items: {win}")
    if "seasonality_assessed" in eng:
        got = (doc.get("seasonality") or {}).get("assessed")
        r.check("seasonality_assessed", got is eng["seasonality_assessed"],
                f"assessed={got}")

    for run in eng.get("extra_runs", []):
        _extra_run(r, run, expected, out_dir, root)

    if command == "report":
        report_path = metrics_path.parent / "report.md"
        if r.check("report_written", report_path.exists(), str(report_path)):
            text = report_path.read_text(encoding="utf-8")
            for pattern in eng.get("report_must_match", []):
                r.check(f"report_matches:{pattern[:40]}",
                        re.search(pattern, text) is not None, pattern)
            if eng.get("report_guard_passes"):
                from finance_engine.guard import NumberGuard
                g = NumberGuard(doc).check(text)
                r.check("report_guard", g.passed,
                        f"{len(g.supported)}/{g.checked} numbers traced"
                        + ("" if g.passed else f"; {g.summary()[:400]}"))
    return r


def _check_values(r: CaseResult, doc: dict, expected: dict, prefix: str = "") -> None:
    """Expected values and expected n/a results, against one metrics.json."""
    m = {x["name"]: x for x in doc["metrics"]}
    for e in expected.get("values", []):
        metric = m.get(e["metric"])
        value = None if metric is None else metric.get("value")
        r.values[prefix + e["metric"]] = value
        cid = f"{prefix}value:{e['metric']}"
        if metric is None:
            r.check(cid, False, "metric missing from metrics.json")
        elif value is None:
            r.check(cid, False,
                    f"n/a ({metric.get('unavailable_reason')}), expected {e['expected']}")
        else:
            diff = abs(value - e["expected"])
            r.check(cid, diff <= e["tolerance"],
                    f"got {value}, expected {e['expected']} (diff {diff:.6g}, "
                    f"tol {e['tolerance']})")
    for e in expected.get("unavailable", []):
        metric = m.get(e["metric"])
        got = (None if metric is None
               else (metric.get("value"), metric.get("unavailable_reason")))
        r.values[prefix + e["metric"]] = None if got is None else got[0]
        r.check(f"{prefix}n/a:{e['metric']}",
                got is not None and got[0] is None and got[1] == e["reason"],
                f"got {got}, expected n/a ({e['reason']})")


def _check_span(r: CaseResult, doc: dict, eng: dict, expected: dict) -> None:
    """Year and whole-file checks: coverage, blocked totals, series, provenance."""
    for key, want in (expected.get("coverage") or {}).items():
        got = (doc.get("coverage") or {}).get(key)
        r.check(f"coverage:{key}", got == want, f"got {got!r}, expected {want!r}")
    for field in expected.get("doc_fields", []):
        got = doc
        for key in field["path"]:
            got = got.get(key) if isinstance(got, dict) else None
        want = field["expected"]
        same = (abs(got - want) <= 0.01 if isinstance(got, (int, float))
                and isinstance(want, (int, float)) else got == want)
        r.check(f"field:{'.'.join(field['path'])}", same, f"got {got!r}, expected {want!r}")
    if "scope" in eng:
        r.check("scope", doc.get("scope", "month") == eng["scope"], str(doc.get("scope")))
    if "source_filename" in eng:
        got = (doc.get("source") or {}).get("filename")
        r.check("source_filename", got == eng["source_filename"], str(got))
    for pattern in eng.get("forbidden_metric_patterns", []):
        hits = [x["name"] for x in doc["metrics"] if re.search(pattern, x["name"], re.I)]
        r.check(f"no_metric_matching:{pattern}", not hits, f"found {hits[:5]}")
    for code_, details in (eng.get("issue_details") or {}).items():
        issue = next((i for i in doc.get("data_quality_issues", [])
                      if i["code"] == code_), None)
        for key, want in details.items():
            got = ((issue or {}).get("details") or {}).get(key)
            r.check(f"issue_detail:{code_}:{key}", got == want,
                    f"got {got!r}, expected {want!r}")
    m = {x["name"]: x for x in doc["metrics"]}
    for status, names in (eng.get("validation_status") or {}).items():
        for name in names:
            got = (m.get(name) or {}).get("validation_status")
            r.check(f"status:{name}", got == status, f"got {got}, expected {status}")
    if "series_points" in eng:
        measures = (doc.get("series") or {}).get("measures") or []
        counts = {s["measure"]: len(s["points"]) for s in measures}
        r.check("series_points",
                bool(counts) and set(counts.values()) == {eng["series_points"]}
                and len(counts) == eng.get("series_measures", len(counts)),
                f"points per measure: {counts}")


def _extra_run(r: CaseResult, run: dict, expected: dict, out_dir: Path,
               root: Path | None) -> None:
    """A further CLI call for the same case: its exit, message and, if any, its values."""
    rid = run["id"]
    code, stdout, stderr = run_engine([str(a) for a in run["args"]], out_dir, root)
    r.values[f"{rid}.exit_code"] = code
    r.check(f"{rid}:exit_code", code == run["exit_code"],
            f"exit {code}, expected {run['exit_code']}; stderr: {stderr.strip()[:200]}")
    r.check(f"{rid}:no_traceback", "Traceback" not in stderr, stderr.strip()[-200:])
    if run.get("stderr"):
        r.check(f"{rid}:message", re.search(run["stderr"], stderr) is not None,
                f"stderr should match {run['stderr']!r}; got {stderr.strip()[:300]!r}")
    if run.get("must_not_exist"):
        path = out_dir / run["must_not_exist"]
        r.check(f"{rid}:nothing_written", not path.exists(), str(path))
    if run.get("metrics"):
        path = out_dir / run["metrics"] / "metrics.json"
        if r.check(f"{rid}:metrics_written", path.exists(), str(path)):
            doc = json.loads(path.read_text(encoding="utf-8"))
            if "source_filename" in run:
                got = (doc.get("source") or {}).get("filename")
                r.check(f"{rid}:source_filename", got == run["source_filename"], str(got))
            _check_values(r, doc, (expected.get("extra_runs") or {}).get(rid, {}),
                          prefix=f"{rid}:")


def _prepare_round_trip_fixture(base: Path) -> None:
    """Case 20's snapshot holds client figures, so it is git-ignored: write the offline
    one if it is missing (an existing file, e.g. a real pull, is left alone)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import load_workbook_to_sandbox as loader

    path = loader.fixture_path(base)
    if not path.exists():
        loader.write_fixture(base)
        print(f"generated missing round-trip fixture: {path}")


def _cli_checks(case: dict, r: CaseResult, out_dir: Path, root: Path | None) -> CaseResult:
    for chk in case["engine"]["cli_checks"]:
        code, stdout, stderr = run_engine([str(a) for a in chk["args"]], out_dir, root)
        cid = chk["id"]
        r.values[f"{cid}.exit_code"] = code
        r.check(f"{cid}:exit_code", code == chk["exit_code"],
                f"exit {code}, expected {chk['exit_code']}; stderr: {stderr.strip()[:200]}")
        r.check(f"{cid}:no_traceback", "Traceback" not in stderr, stderr.strip()[-200:])
        if chk.get("stderr"):
            r.check(f"{cid}:message", re.search(chk["stderr"], stderr) is not None,
                    f"stderr should match {chk['stderr']!r}; got {stderr.strip()[:200]!r}")
        for n, pattern in enumerate(chk.get("stdout") or []):
            r.check(f"{cid}:stdout_{n + 1}", re.search(pattern, stdout) is not None,
                    f"stdout should match {pattern!r}; got {stdout.strip()[-300:]!r}")
        if chk.get("must_not_exist"):
            path = out_dir / chk["must_not_exist"]
            r.check(f"{cid}:no_output_for_unknown_client", not path.exists(), str(path))
        if chk.get("all_metrics_blocked"):
            args = [str(a) for a in chk["args"]]
            client, period = args[args.index("--client") + 1], args[args.index("--period") + 1]
            path = out_dir / client / period / "metrics.json"
            if r.check(f"{cid}:metrics_written", path.exists(), str(path)):
                doc = json.loads(path.read_text(encoding="utf-8"))
                values = [x["value"] for x in doc["metrics"]]
                r.check(f"{cid}:all_metrics_blocked",
                        all(v is None for v in values),
                        f"{sum(v is not None for v in values)} metric(s) have a value")
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cases", nargs="*", help="case id prefixes; default all")
    ap.add_argument("--root", type=Path, default=None,
                    help="project tree holding config/ and data/ (default: this repo)")
    ap.add_argument("--compare", action="store_true", help="diff against the baseline")
    ap.add_argument("--baseline", type=Path, default=None,
                    help="baseline file (default: newest evals/baseline/engine_*.json)")
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--label", default="engine",
                    help="results label, used in the baseline file name")
    args = ap.parse_args(argv)

    run_id, run_dir = new_run_dir("engine")
    out_dir = run_dir / "out"
    print(f"Level 1 engine evals -> {run_dir}")
    ensure_generated_workbooks(args.root.resolve() if args.root else None)
    results = [evaluate(c, out_dir, args.root.resolve() if args.root else None)
               for c in load_cases(args.cases)]
    doc = results_doc("engine", run_id, results, baseline_label=args.label,
                      root=str(args.root) if args.root else None)
    print_results(doc)
    print(f"results: {save_results(doc, run_dir)}")

    regressed = False
    if args.compare:
        path = args.baseline or latest_baseline(args.label)
        if path is None:
            print("\nNo baseline exists yet. Create one with --save-baseline.")
        else:
            cmp = compare_results(doc, json.loads(path.read_text(encoding="utf-8")))
            print_comparison(cmp)
            regressed = cmp["regressed"]
            (run_dir / "comparison.json").write_text(json.dumps(cmp, indent=2),
                                                     encoding="utf-8")
    if args.save_baseline:
        print(f"baseline saved: {save_baseline(doc)}")
    return 1 if (doc["summary"]["failed"] or regressed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
