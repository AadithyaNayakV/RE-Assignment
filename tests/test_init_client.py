"""`init-client` (Phase 5B, task 6): draft a config by inspecting a workbook.

The generated config is judged by the engine itself: for test_clean it must give exactly
the metric values the hand-written config gives, and for Companies A and B the same. A
layout it cannot handle is reported as a draft with its problems, never forced; a workbook
that is not a monthly income statement is refused with nothing written.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml
from openpyxl import Workbook

from finance_engine.cli import EXIT_DATA, EXIT_OK, EXIT_USAGE, main
from finance_engine.config import ClientConfig, Settings
from finance_engine.metrics import build_metrics
from finance_engine.sources.excel import ExcelSource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import make_test_workbooks  # noqa: E402  (openpyxl only)


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _values(config: ClientConfig, workbook: Path, period: str) -> dict[str, float | None]:
    doc = build_metrics(ExcelSource(workbook).load(config), config, period)
    return {m["name"]: m["value"] for m in doc["metrics"]}


def _init(root: Path, workbook: Path, client_id: str, *extra) -> int:
    return main(["init-client", "--file", str(workbook), "--client-id", client_id,
                 "--name", f"Test {client_id}", "--root", str(root), *extra])


def _assert_same_values(generated: dict, hand: dict) -> None:
    assert set(generated) == set(hand), sorted(set(generated) ^ set(hand))
    for name, value in hand.items():
        if value is None:
            assert generated[name] is None, name
        else:
            assert generated[name] == pytest.approx(value, abs=1e-9), name


@pytest.fixture
def clean_book(tmp_path):
    """test_clean's workbook, built by the same script that made the real one."""
    path = tmp_path / "in" / "test_clean.xlsx"
    path.parent.mkdir()
    make_test_workbooks.build(str(path), False, "Test Clean Co (SYNTHETIC)")
    return path


class TestMatchesTheHandWrittenConfigs:
    @pytest.mark.parametrize("period", ["2026-01", "2025-12", "2025-06"])
    def test_test_clean(self, tmp_path, clean_book, capsys, period):
        root = tmp_path / "root"
        assert _init(root, clean_book, "tc_draft") == EXIT_OK
        out = capsys.readouterr().out
        assert "Validation passed" in out and "Next: python -m finance_engine report" in out
        generated = ClientConfig.load("tc_draft", Settings.from_env(root))
        hand = ClientConfig.load("test_clean", Settings.from_env(ROOT))
        copy = root / "data" / "clients" / "tc_draft" / "source" / "test_clean.xlsx"
        _assert_same_values(_values(generated, copy, period),
                            _values(hand, clean_book, period))

    @pytest.mark.parametrize("client_id", ["company_a_normal_growth",
                                           "company_b_revenue_decline"])
    def test_companies_a_and_b(self, tmp_path, capsys, client_id):
        book = ROOT / "data" / "synthetic" / f"{client_id}.xlsx"
        before = _sha(book)
        assert _init(tmp_path, book, "drafted") == EXIT_OK
        assert _sha(book) == before                       # the original is never modified
        generated = ClientConfig.load("drafted", Settings.from_env(tmp_path))
        hand = ClientConfig.load(client_id, Settings.from_env(ROOT))
        assert generated.raw["income_statement"]["sections"][2]["subtotal_groups"] == \
            hand.raw["income_statement"]["sections"][2]["subtotal_groups"]
        copy = tmp_path / "data" / "clients" / "drafted" / "source" / book.name
        _assert_same_values(_values(generated, copy, "2026-06"),
                            _values(hand, book, "2026-06"))


class TestTheYaml:
    def test_comment_block_and_guesses(self, tmp_path, clean_book):
        _init(tmp_path, clean_book, "tc_draft")
        text = (tmp_path / "config" / "clients" / "tc_draft.yaml").read_text("utf-8")
        head = text.split("\n\n")[0]
        assert head.startswith("# Client config for tc_draft: drafted by init-client")
        for detected in ("income-statement sheet: Income Statement [high]",
                         "header row: 5 [high]", "date order: newest_first [high]",
                         "label columns: ['A', 'B'] [high]",
                         "revenue section: 'Income' (row 6) .. 'Total Income' (row 9) [high]",
                         "operating-expense categories: 4 proposed for 4 line(s) [low]",
                         "currency: USD ($) [low]"):
            assert detected in head, detected
        assert "every one a GUESS" in head and "# Needs your decision:" in head
        assert "Currency: no currency symbol" in head
        raw = yaml.safe_load(text)
        assert raw["fallback_opex_category"] == "Uncategorised (needs review)"
        assert raw["source"] == {"type": "excel", "filename": "test_clean.xlsx"}

    def test_json(self, tmp_path, clean_book, capsys):
        assert _init(tmp_path, clean_book, "tc_draft", "--json") == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True and payload["status"] == "config"
        assert payload["config_path"] == "config/clients/tc_draft.yaml"
        assert payload["workbook_copy"] == "data/clients/tc_draft/source/test_clean.xlsx"
        assert payload["periods"][-1] == "2026-01"
        assert payload["next_command"] == ("python -m finance_engine report --client "
                                           "tc_draft --period 2026-01")
        assert {d["what"] for d in payload["detected"]} >= {"header row", "date order"}


class TestLayoutsItCannotHandle:
    def _statement(self, path: Path, *, total_column=False, cogs=True, orphan_total=False):
        wb = Workbook()
        ws = wb.active
        ws.title = "P&L"
        heads = ["Jan 2025", "Feb 2025", "Mar 2025"]
        if total_column:
            heads.insert(2, "Total")                  # a non-month column between months
        for j, h in enumerate(heads):
            ws.cell(1, 2 + j, h)
        rows = [("Income", None), ("Sales", 100), ("Total Income", 100)]
        if cogs:
            rows += [("Cost of Goods Sold", None), ("Hosting", 10),
                     ("Total Cost of Goods Sold", 10)]
        rows += [("Operating Expenses", None), ("Rent", 20)]
        if orphan_total:
            rows += [("Total Facilities", 20)]
        rows += [("Total Operating Expenses", 20 + (20 if orphan_total else 0))]
        for i, (label, value) in enumerate(rows, start=2):
            ws.cell(i, 1, label)
            if value is not None:
                for j in range(len(heads)):
                    ws.cell(i, 2 + j, value)
        wb.save(path)
        return path

    @pytest.mark.parametrize("kind, message", [
        ({"total_column": True}, "non-month cells between the month columns"),
        ({"cogs": False}, "No cost of goods sold total found"),
        ({"orphan_total": True}, "no 'facilities' header row sits above it"),
    ])
    def test_reported_as_a_draft(self, tmp_path, capsys, kind, message):
        book = self._statement(tmp_path / "odd.xlsx", **kind)
        before = _sha(book)
        code = _init(tmp_path / "root", book, "odd_co")
        out = capsys.readouterr().out
        assert code == EXIT_DATA
        assert "Validation FAILED" in out and message in out
        clients = tmp_path / "root" / "config" / "clients"
        assert (clients / "odd_co.draft.yaml").exists()
        assert not (clients / "odd_co.yaml").exists()
        assert message in (clients / "odd_co.draft.yaml").read_text("utf-8")
        assert _sha(book) == before

    def test_a_failed_reconciliation_makes_a_draft(self, tmp_path, capsys):
        book = self._statement(tmp_path / "off.xlsx")
        from openpyxl import load_workbook
        wb = load_workbook(book)
        wb.active["B4"] = 999                          # Total Income no longer adds up
        wb.save(book)
        assert _init(tmp_path / "root", book, "off_co") == EXIT_DATA
        assert "Reconciliation failed: revenue_items_vs_total 2025-01" in \
            capsys.readouterr().out


class TestRefusals:
    def test_a_non_financial_workbook(self, tmp_path, capsys):
        wb = Workbook()
        wb.active["A1"] = "Team lunch rota"
        wb.active["A2"] = "Monday"
        wb.active["B2"] = "Priya"
        book = tmp_path / "rota.xlsx"
        wb.save(book)
        assert _init(tmp_path / "root", book, "rota") == EXIT_USAGE
        err = capsys.readouterr().err
        assert "does not look like a monthly income statement" in err
        assert not (tmp_path / "root" / "config").exists()
        assert not (tmp_path / "root" / "data").exists()

    @pytest.mark.parametrize("name, body, message", [
        ("broken.xlsx", b"this is not a zip file", "Cannot read broken.xlsx"),
        ("table.csv", b"a,b\n1,2\n", "not an .xlsx workbook"),
    ])
    def test_an_unreadable_file(self, tmp_path, capsys, name, body, message):
        path = tmp_path / name
        path.write_bytes(body)
        assert _init(tmp_path / "root", path, "bad_co", "--json") == EXIT_USAGE
        captured = capsys.readouterr()
        assert message in captured.err
        assert json.loads(captured.out)["ok"] is False

    def test_a_missing_file(self, tmp_path, capsys):
        assert _init(tmp_path, tmp_path / "nope.xlsx", "x_co") == EXIT_USAGE
        assert "not found" in capsys.readouterr().err

    def test_a_bad_client_id(self, tmp_path, clean_book, capsys):
        assert _init(tmp_path, clean_book, "Bad Id!") == EXIT_USAGE
        assert "--client-id" in capsys.readouterr().err

    def test_never_overwrites_without_force(self, tmp_path, clean_book, capsys):
        clients = tmp_path / "config" / "clients"
        clients.mkdir(parents=True)
        (clients / "tc_draft.yaml").write_text("# mine\n", "utf-8")
        assert _init(tmp_path, clean_book, "tc_draft") == EXIT_USAGE
        assert "--force" in capsys.readouterr().err
        assert (clients / "tc_draft.yaml").read_text("utf-8") == "# mine\n"
        assert not (tmp_path / "data").exists()           # nothing copied either
        assert _init(tmp_path, clean_book, "tc_draft", "--force") == EXIT_OK
        assert (clients / "tc_draft.yaml").read_text("utf-8").startswith(
            "# Client config for tc_draft")

    def test_a_different_workbook_of_the_same_name_is_not_replaced(self, tmp_path,
                                                                   clean_book, capsys):
        dest = tmp_path / "data" / "clients" / "tc_draft" / "source"
        dest.mkdir(parents=True)
        (dest / "test_clean.xlsx").write_bytes(b"someone else's file")
        assert _init(tmp_path, clean_book, "tc_draft") == EXIT_USAGE
        assert "never overwritten" in capsys.readouterr().err
        assert (dest / "test_clean.xlsx").read_bytes() == b"someone else's file"
