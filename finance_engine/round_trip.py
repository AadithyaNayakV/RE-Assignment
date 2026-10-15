"""Round trip: the engine on a workbook against the engine on its QuickBooks copy.

`scripts/load_workbook_to_sandbox.py` posts a workbook's figures into the QuickBooks
sandbox as one journal entry per month; `pull-quickbooks` saves them back as a snapshot.
If loading, pulling and reading lost or changed nothing, the engine must produce the same
figures from both sources. This module runs `build_metrics` on each source for every month
of the workbook and compares the headline figures:

* revenue, cost of goods sold, gross profit, operating expenses, operating income and
  every operating-expense category: to the cent (an absolute difference under 0.005);
* gross margin: within 0.000001 percentage points (both sides divide the same cents);
* net income: the workbook's own Net Income row, against Intuit's Net Income when the
  snapshot provides it, else against the operating income of the loaded accounts (the
  loader posts nothing below operating income, so for those accounts they are the same);
* each of those as a trailing-twelve-month total where both sides have a complete window.

A figure missing on both sides matches ("n/a" on both); missing on one side does not.
Nothing is estimated: a difference is reported, never explained away.
"""

from __future__ import annotations

from pathlib import Path

from finance_engine.config import ClientConfig, Settings
from finance_engine.metrics import build_metrics
from finance_engine.sources.factory import open_source
from finance_engine.sources.selection import Want, choose

MONEY_TOL = 0.005
PCT_TOL = 1e-6

HEADLINE = ("revenue.total", "cogs.total", "gross_profit", "gross_margin_pct",
            "opex.total", "operating_income")
TTM = ("revenue.total.ttm", "cogs.total.ttm", "gross_profit.ttm", "gross_margin_pct.ttm",
       "opex.total.ttm", "operating_income.ttm")


def _load(client_id: str, root: Path, file_name: str | None = None):
    settings = Settings.from_env(root)
    config = ClientConfig.load(client_id, settings)
    choice = choose(config, settings, root, file_name, Want("all"))
    source = open_source(choice.path, config)
    return config, source.load(config), choice.path


def _values(doc: dict) -> dict[str, float | None]:
    return {m["name"]: m["value"] for m in doc["metrics"]}


def _names(excel: dict, qb: dict) -> list[str]:
    cats = sorted({n for n in (*excel, *qb) if n.startswith("opex.category.")
                   and n.count(".") == 2})
    return [*HEADLINE, *cats, "net_income", *TTM, *(f"{c}.ttm" for c in cats)]


def _net_income(values: dict) -> tuple[float | None, str]:
    if values.get("net_income.reported") is not None:
        return values["net_income.reported"], "net_income.reported"
    return values.get("operating_income"), "operating_income"


def _compare(name: str, a: float | None, b: float | None) -> dict:
    tol = PCT_TOL if "margin_pct" in name else MONEY_TOL
    if a is None or b is None:
        same = a is None and b is None
        diff = None
    else:
        diff = b - a
        same = abs(diff) < tol
    return {"metric": name, "excel": a, "quickbooks": b, "difference": diff,
            "match": same}


def round_trip(excel_client: str, qb_client: str, root: Path,
               qb_file: str | None = None) -> dict:
    """Compare every month of `excel_client`'s workbook with `qb_client`'s snapshot."""
    root = Path(root)
    x_cfg, x_ds, x_path = _load(excel_client, root)
    q_cfg, q_ds, q_path = _load(qb_client, root, qb_file)
    months = x_ds.period_keys()
    missing = [p for p in months if p not in q_ds.period_keys()]
    rows, mismatches = [], []
    for period in months:
        if period in missing:
            rows.append({"period": period, "compared": 0, "mismatched": None,
                         "note": "not in the QuickBooks snapshot"})
            continue
        xv = _values(build_metrics(x_ds, x_cfg, period, project_root=root))
        qv = _values(build_metrics(q_ds, q_cfg, period, project_root=root))
        results = []
        for name in _names(xv, qv):
            if name == "net_income":
                b, used = _net_income(qv)
                res = _compare("net_income", xv.get("net_income.reported"), b)
                res["quickbooks_from"] = used
            else:
                res = _compare(name, xv.get(name), qv.get(name))
            results.append(res)
        bad = [r for r in results if not r["match"]]
        mismatches += [{"period": period, **r} for r in bad]
        rows.append({"period": period, "compared": len(results), "mismatched": len(bad),
                     "ttm_compared": sum(1 for r in results if r["metric"].endswith(".ttm")
                                         and r["excel"] is not None)})
    ok = not missing and not mismatches
    return {"ok": ok, "excel_client": excel_client, "qb_client": qb_client,
            "workbook": x_path.name, "snapshot": q_path.name,
            "months": months, "months_missing_from_snapshot": missing,
            "figures_compared": sum(r["compared"] for r in rows),
            "rows": rows, "mismatches": mismatches,
            "tolerance": {"money": MONEY_TOL, "margin_pct_points": PCT_TOL},
            "snapshot_warnings": [w["message"] for w in q_ds.source_warnings]}


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def summary_lines(result: dict, limit: int = 20) -> list[str]:
    lines = [f"Round trip: {result['excel_client']} ({result['workbook']}) vs "
             f"{result['qb_client']} ({result['snapshot']})",
             "",
             f"  {'month':<8}  {'figures':>7}  {'with TTM':>8}  result"]
    for row in result["rows"]:
        if row.get("note"):
            lines.append(f"  {row['period']:<8}  {'-':>7}  {'-':>8}  MISSING ({row['note']})")
            continue
        verdict = "match" if not row["mismatched"] else f"{row['mismatched']} DIFFER"
        lines.append(f"  {row['period']:<8}  {row['compared']:>7}  "
                     f"{row['ttm_compared']:>8}  {verdict}")
    if result["mismatches"]:
        lines += ["", f"  {'month':<8}  {'metric':<44}  {'excel':>16}  {'quickbooks':>16}"]
        for m in result["mismatches"][:limit]:
            lines.append(f"  {m['period']:<8}  {m['metric']:<44}  {_fmt(m['excel']):>16}  "
                         f"{_fmt(m['quickbooks']):>16}")
        if len(result["mismatches"]) > limit:
            lines.append(f"  ... {len(result['mismatches']) - limit} more (--json lists all)")
    for w in result["snapshot_warnings"]:
        lines.append(f"  note: {w}")
    n = len(result["months"])
    lines.append("")
    if result["ok"]:
        lines.append(f"RESULT: all {n} months match ({result['figures_compared']} figures, "
                     f"money to the cent).")
    else:
        lines.append(f"RESULT: MISMATCH - {len(result['mismatches'])} figure(s) differ, "
                     f"{len(result['months_missing_from_snapshot'])} month(s) missing.")
    return lines
