"""QuickBooks snapshots (Phase 5G): sections QuickBooks leaves out, and which snapshot a
run reads.

QuickBooks drops a whole group from its Profit and Loss when nothing was posted to it in
the range. Four real sandbox pulls (tests/fixtures/qb_omitted_cogs/, Intuit's sample
company, no credentials) have no "Cost of Goods Sold" group at all and used to stop with
"section 'cogs' header label 'Cost of Goods Sold' not found". They now read: the section
counts as 0 with a warning, and the check against Intuit's absent total is "not provided
by QuickBooks", never a pass or a failure. Only groups the config lists under
`source.may_be_omitted` (qb_sandbox.yaml) are put back; every other config is unchanged.
No network.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest

from finance_engine.cli import EXIT_DATA, EXIT_OK, main
from finance_engine.config import ClientConfig
from finance_engine.model import LayoutError
from finance_engine.reconciliation import reconcile
from finance_engine.sources.quickbooks import (QuickBooksSnapshotSource, read_profit_and_loss,
                                               snapshot_header)
from finance_engine.sources.excel import apply_income_statement_structure

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "clients" / "qb_sandbox.yaml"
FIXTURE_CONFIG = ROOT / "config" / "clients" / "qb_sandbox_fixture.yaml"
FULL = ROOT / "tests" / "fixtures" / "qb_pl_monthly.json"
OMITTED = sorted((ROOT / "tests" / "fixtures" / "qb_omitted_cogs").glob("*.json"))
WARNING = "QuickBooks left out the Cost of Goods Sold section for this range; counted as 0."


def _cfg(path=CONFIG) -> ClientConfig:
    return ClientConfig.load_path(path)


def _groups(path: Path) -> list[str]:
    rows = json.loads(path.read_text("utf-8"))["Rows"]["Row"]
    return [r.get("group") for r in rows]


def _sheet(report: dict, cfg: ClientConfig, warnings=None):
    sheet = read_profit_and_loss(report, "x.json", cfg, warnings)
    apply_income_statement_structure(sheet, cfg)
    return sheet


# ---- the four snapshots that used to fail ---------------------------------------------------

class TestTheFourSnapshots:
    def test_there_are_four_and_none_has_a_cogs_group(self):
        assert len(OMITTED) == 4
        for path in OMITTED:
            groups = _groups(path)
            assert "COGS" not in groups
            assert {"Income", "GrossProfit", "Expenses", "NetOperatingIncome",
                    "NetIncome"} <= set(groups)

    def test_they_failed_without_the_mapping(self):
        """The fixture client has no `may_be_omitted`: the old message, unchanged."""
        for path in OMITTED:
            with pytest.raises(LayoutError, match="'Cost of Goods Sold' not found"):
                QuickBooksSnapshotSource(path).load(_cfg(FIXTURE_CONFIG))

    @pytest.mark.parametrize("path", OMITTED, ids=lambda p: p.name)
    def test_each_now_reads_and_reconciles(self, path):
        cfg = _cfg()
        ds = QuickBooksSnapshotSource(path).load(cfg)
        assert [w["message"] for w in ds.source_warnings] == [WARNING]
        sheet = ds.income_statement
        months = sheet.period_keys()
        zero = [i for i in sheet.in_section("cogs")]
        assert [i.label for i in zero] == ["Cost of Goods Sold (left out by QuickBooks)"]
        assert {p: zero[0].value(p) for p in months} == {p: 0.0 for p in months}
        assert zero[0].trace(months[0])["note"] == WARNING
        total = sheet.section_total("cogs")
        assert total.cells == {} and "not provided by QuickBooks" in total.omitted_by_source
        # put back where Intuit sends it: after Total Income, before Gross Profit
        rows = {i.label: i.row for i in sheet.line_items}
        assert rows["Total Income"] < rows["Cost of Goods Sold"] < \
            rows["Total Cost of Goods Sold"] < rows["Gross Profit"]

        recon = reconcile(ds, cfg).to_dict()
        assert recon["all_passed"] and recon["checks_failed"] == 0
        assert recon["checks_not_provided"] == len(months)
        assert {r["key"] for r in recon["not_provided"]} == {"cogs_items_vs_total"}
        assert all(r["not_provided"] and r["reported"] is None
                   for r in recon["not_provided"])
        cogs = next(c for c in recon["by_check"] if c["key"] == "cogs_items_vs_total")
        assert cogs["periods_checked"] == 0 and cogs["periods_not_provided"] == months
        gp = next(c for c in recon["by_check"] if c["key"] == "gross_profit")
        assert gp["passed"] and gp["periods_checked"] == len(months)

    @pytest.mark.parametrize("path", OMITTED[:2], ids=lambda p: p.name)
    def test_a_report_says_counted_as_0_and_not_provided(self, path, tmp_path, monkeypatch,
                                                         capsys):
        root = _qb_root(tmp_path, [path])
        monkeypatch.setenv("FINANCE_OUTPUT_DIR", "output/clients")
        period = json.loads(path.read_text("utf-8"))["Header"]["EndPeriod"][:7]
        code = main(["report", "--client", "qb_sandbox", "--period", period, "--provider",
                     "deterministic", "--root", str(root)])
        out = capsys.readouterr()
        assert code == EXIT_DATA, out.err             # two months: the TTM is incomplete
        assert "2 not provided by QuickBooks" in out.out
        folder = root / "output" / "clients" / "qb_sandbox" / period
        doc = json.loads((folder / "metrics.json").read_text("utf-8"))
        assert [i["code"] for i in doc["data_quality_issues"]
                if i["severity"] == "error"] == ["ttm_window_incomplete"]
        assert doc["reconciliation"]["checks_failed"] == 0
        m = {x["name"]: x for x in doc["metrics"]}
        assert m["cogs.total"]["value"] == 0.0
        assert m["gross_profit"]["value"] == m["revenue.total"]["value"]
        assert any(i["code"] == "quickbooks_section_omitted" and i["message"] == WARNING
                   for i in doc["data_quality_issues"])
        md = (folder / "report.md").read_text("utf-8")
        assert "| not provided by QuickBooks |" in md
        assert "| Commentary number check | PASS |" in md
        assert WARNING in md


# ---- other groups QuickBooks can leave out ---------------------------------------------------

def _without(group: str) -> dict:
    report = json.loads(FULL.read_text("utf-8"))
    report["Rows"]["Row"] = [r for r in report["Rows"]["Row"] if r.get("group") != group]
    return report


class TestOtherOmittedGroups:
    def test_expenses_go_back_between_gross_profit_and_net_operating_income(self):
        warnings: list = []
        sheet = _sheet(_without("Expenses"), _cfg(), warnings)
        rows = {i.label: i.row for i in sheet.line_items}
        assert rows["Gross Profit"] < rows["Expenses"] < rows["Total Expenses"] < \
            rows["Net Operating Income"]
        assert [i.label for i in sheet.in_section("opex")] == \
            ["Expenses (left out by QuickBooks)"]
        assert [w["code"] for w in warnings] == ["quickbooks_section_omitted"]

    def test_a_left_out_summary_row_is_never_filled_in(self):
        warnings: list = []
        cfg = _cfg()
        sheet = _sheet(_without("GrossProfit"), cfg, warnings)
        gp = sheet.reported("Gross Profit")
        assert gp.cells == {} and gp.omitted_by_source
        assert [w["code"] for w in warnings] == ["quickbooks_row_omitted"]

        class DS:                                   # the minimum reconcile() reads
            income_statement = sheet
            balance_sheet = None
        recon = reconcile(DS(), cfg).to_dict()
        keys = {r["key"] for r in recon["not_provided"]}
        assert keys == {"gross_profit", "operating_income"}
        assert recon["checks_failed"] == 0

    def test_the_mapping_is_checked(self, tmp_path):
        text = CONFIG.read_text("utf-8").replace("cogs: COGS", "cogs: CostOfSales")
        path = tmp_path / "qb_sandbox.yaml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(LayoutError, match="may_be_omitted maps 'cogs' to 'CostOfSales'"):
            read_profit_and_loss(json.loads(FULL.read_text("utf-8")), "x.json",
                                 ClientConfig.load_path(path))

    def test_a_full_snapshot_reads_exactly_as_before(self):
        """Nothing left out: the same rows with the mapping as without it."""
        report = json.loads(FULL.read_text("utf-8"))
        with_map = _sheet(copy.deepcopy(report), _cfg())
        without = _sheet(copy.deepcopy(report), _cfg(FIXTURE_CONFIG))
        flat = lambda s: [(i.row, i.label, i.role, i.section,          # noqa: E731
                           {p: c.value for p, c in i.cells.items()}) for i in s.line_items]
        assert flat(with_map) == flat(without)


# ---- which snapshot a run reads, and how the picker names it ----------------------------------

def _qb_root(tmp_path: Path, snapshots: list[Path], times: list[str] | None = None) -> Path:
    root = tmp_path / "root"
    (root / "config" / "clients").mkdir(parents=True)
    for name in ("providers.yaml", "review.yaml"):
        shutil.copy(ROOT / "config" / name, root / "config" / name)
    shutil.copy(CONFIG, root / "config" / "clients" / "qb_sandbox.yaml")
    src = root / "data" / "clients" / "qb_sandbox" / "source"
    src.mkdir(parents=True)
    for k, path in enumerate(snapshots):
        data = json.loads(path.read_text("utf-8"))
        if times:
            data["Header"]["Time"] = times[k]
        (src / path.name).write_text(json.dumps(data), encoding="utf-8")
    return root


def _unreadable(src: Path) -> Path:
    bad = json.loads(FULL.read_text("utf-8"))
    bad["Header"]["SummarizeColumnsBy"] = "Total"
    bad["Header"]["Time"] = "2026-10-20T09:00:00+00:00"             # the newest pull
    path = src / "qb_pl_2026-01_2026-03_bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    return path


LABEL = re.compile(r"^Pulled \d{1,2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2} – (.+)$")


class TestPicker:
    TIMES = ["2026-10-12T19:49:45-07:00", "2026-10-14T00:39:33-07:00"]

    def _root(self, tmp_path):
        root = _qb_root(tmp_path, [FULL, OMITTED[0]], self.TIMES)
        _unreadable(root / "data" / "clients" / "qb_sandbox" / "source")
        return root

    def test_labels_newest_first_and_unreadable_last(self, tmp_path):
        from finance_engine.service import core
        data = core.list_files(self._root(tmp_path), "qb_sandbox")
        files = data["files"]
        assert [f["readable"] for f in files] == [False, True, True]   # newest pull first
        readable = [f for f in files if f["readable"]]
        assert [LABEL.match(f["label"]).group(1) for f in readable] == \
            ["May 2026 to Jun 2026", "Oct 2025 to Oct 2026"]
        assert all("?" not in f["label"] for f in files)
        bad = files[0]
        assert LABEL.match(bad["label"]).group(1) == "Oct 2025 to Oct 2026"  # from its header
        assert bad["reason"].startswith("the report must have one column per month")
        assert "\n" not in bad["reason"] and len(bad["reason"]) <= 140
        assert data["default_file"] == OMITTED[0].name
        assert data["default_note"].startswith("No file chosen: using the newest readable "
                                               "snapshot, Pulled ")
        assert [f["pulled_at_utc"] for f in readable] == ["2026-10-14T07:39:33+00:00",
                                                          "2026-10-13T02:49:45+00:00"]

    def test_the_label_is_this_machines_local_time(self, tmp_path):
        import datetime as dt
        from finance_engine.service import core
        data = core.list_files(self._root(tmp_path), "qb_sandbox")
        when = dt.datetime.fromisoformat(self.TIMES[1]).astimezone()
        assert data["files"][1]["label"].startswith(
            f"Pulled {when.day} {when:%b} {when.year}, {when:%H:%M} – ")

    def test_no_file_reads_the_newest_readable_snapshot(self, tmp_path, monkeypatch, capsys):
        root = self._root(tmp_path)
        monkeypatch.setenv("FINANCE_OUTPUT_DIR", "output/clients")
        assert main(["report", "--client", "qb_sandbox", "--scope", "all", "--provider",
                     "deterministic", "--root", str(root)]) in (EXIT_OK, EXIT_DATA)
        err = capsys.readouterr().err
        assert (f"source: {OMITTED[0].name} (the newest readable QuickBooks snapshot; "
                f"no --file given)") in err
        doc = json.loads((root / "output" / "clients" / "qb_sandbox" / "all" /
                          "metrics.json").read_text("utf-8"))
        assert doc["source"]["filename"] == OMITTED[0].name
        assert doc["source"]["selection"]["mode"] == "newest"

    def test_a_month_only_an_older_snapshot_holds(self, tmp_path, monkeypatch, capsys):
        root = self._root(tmp_path)
        monkeypatch.setenv("FINANCE_OUTPUT_DIR", "output/clients")
        assert main(["report", "--client", "qb_sandbox", "--period", "2025-11",
                     "--provider", "deterministic", "--root", str(root)]) in (EXIT_OK,
                                                                              EXIT_DATA)
        assert "that holds period 2025-11" in capsys.readouterr().err

    def test_list_periods_without_a_file_uses_the_newest(self, tmp_path):
        from finance_engine.service import core
        data = core.list_periods(self._root(tmp_path), "qb_sandbox")
        assert data["file"] == OMITTED[0].name and data["selection_mode"] == "newest"
        assert [p["period"] for p in data["periods"]] == ["2026-06", "2026-05"]

    def test_the_terminal_lists_the_same_labels(self, tmp_path, capsys):
        assert main(["list-files", "--client", "qb_sandbox",
                     "--root", str(self._root(tmp_path))]) == EXIT_OK
        out = capsys.readouterr().out
        assert "cannot be read: the report must have one column per month" in out
        assert "No file chosen: using the newest readable snapshot" in out

    def test_nothing_readable(self, tmp_path):
        from finance_engine.service import core
        root = _qb_root(tmp_path, [])
        src = root / "data" / "clients" / "qb_sandbox" / "source"
        _unreadable(src)
        shutil.copy(src / "qb_pl_2026-01_2026-03_bad.json", src / "qb_pl_bad_2.json")
        data = core.list_files(root, "qb_sandbox")
        assert data["default_file"] is None and "No snapshot can be read" in \
            data["default_note"]
        with pytest.raises(core.ServiceError, match="None of the 2 QuickBooks snapshots"):
            core.list_periods(root, "qb_sandbox")

    def test_a_snapshot_header_is_read_even_when_unreadable(self, tmp_path):
        bad = _unreadable(tmp_path)
        head = snapshot_header(bad)
        assert (str(head["start"]), str(head["end"])) == ("2025-10-01", "2026-10-31")
        assert head["pulled_at"].isoformat() == "2026-10-20T09:00:00+00:00"
