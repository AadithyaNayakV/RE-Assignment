"""A new company in two steps (Phase 5D, part 1): init-client --draft, then approve-config.

The draft never touches config/clients/, so the engine cannot load it until a person
approves it. Approval re-validates with the real engine, copies the YAML byte for byte and
records who approved it, when, and the YAML's SHA-256. Workbooks here are generated test
books, never client data.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from finance_engine.cli import EXIT_DATA, EXIT_OK, EXIT_USAGE, main
from finance_engine.config import ClientConfig, ConfigError, Settings
from finance_engine.onboarding import check_client_id, OnboardingError
from tests.test_init_client import TestLayoutsItCannotHandle

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import make_test_workbooks  # noqa: E402  (openpyxl only)


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def book(tmp_path):
    path = tmp_path / "in" / "acme book.xlsx"
    path.parent.mkdir()
    make_test_workbooks.build(str(path), False, "Acme Test Co (SYNTHETIC)")
    return path


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "root"
    (r / "config" / "clients").mkdir(parents=True)
    return r


def _draft(root, book, cid="acme_ltd", *extra):
    return main(["init-client", "--draft", "--file", str(book), "--client-id", cid,
                 "--name", "Acme Ltd", "--root", str(root), *extra])


def _paths(root, cid="acme_ltd"):
    d = root / "data" / "clients" / cid
    return d / "draft" / "config.yaml", d / "draft" / "draft_summary.json", \
        root / "config" / "clients" / f"{cid}.yaml", d / "config_approval.json"


class TestDraft:
    def test_writes_the_draft_and_summary_only(self, root, book, capsys):
        before = _sha(book)
        assert _draft(root, book) == EXIT_OK
        out = capsys.readouterr().out
        draft, summary, live, approval = _paths(root)
        assert draft.exists() and summary.exists()
        assert not live.exists() and not approval.exists()
        assert "Not live" in out and "approve-config --client acme_ltd" in out
        s = json.loads(summary.read_text("utf-8"))
        assert s["status"] == "valid" and s["checks"] == "65/65 passed"
        assert s["sheet"] == "Income Statement" and s["periods"]["count"] == 13
        assert s["config_sha256"] == _sha(draft)
        assert s["workbook"] == "data/clients/acme_ltd/source/acme book.xlsx"
        assert "no_balance_sheet" in s["warnings"]
        assert _sha(book) == before                       # the original is untouched
        with pytest.raises(ConfigError):
            ClientConfig.load("acme_ltd", Settings.from_env(root))

    def test_an_existing_draft_needs_force(self, root, book, capsys):
        assert _draft(root, book) == EXIT_OK
        assert _draft(root, book) == EXIT_USAGE
        assert "--force" in capsys.readouterr().err
        assert _draft(root, book, "acme_ltd", "--force") == EXIT_OK

    def test_a_layout_it_cannot_handle_is_an_invalid_draft(self, root, tmp_path, capsys):
        odd = TestLayoutsItCannotHandle()._statement(tmp_path / "odd.xlsx", cogs=False)
        assert _draft(root, odd, "odd_co") == EXIT_DATA
        s = json.loads(_paths(root, "odd_co")[1].read_text("utf-8"))
        assert s["status"] == "invalid"
        assert any("No cost of goods sold total" in p for p in s["problems"])
        capsys.readouterr()
        assert main(["approve-config", "--client", "odd_co", "--by", "A. Reviewer",
                     "--root", str(root)]) == EXIT_USAGE
        assert "has not changed since it failed validation" in capsys.readouterr().err
        assert not _paths(root, "odd_co")[2].exists()

    @pytest.mark.parametrize("cid", ["a", "Acme", "acme-ltd", "../acme", "x" * 41,
                                     "acme ltd", "acme/ltd"])
    def test_client_ids_outside_the_pattern_are_refused(self, root, book, cid, capsys):
        assert _draft(root, book, cid) == EXIT_USAGE
        assert not (root / "data").exists()

    def test_the_pattern(self):
        assert check_client_id("ab") == "ab" and check_client_id("x" * 40)
        for bad in ("", "a", "A1", "a.b", "a\\b", "x" * 41, None):
            with pytest.raises(OnboardingError):
                check_client_id(bad)


class TestValidate:
    def test_a_valid_draft(self, root, book, capsys):
        _draft(root, book)
        capsys.readouterr()
        assert main(["validate-config", "--client", "acme_ltd", "--draft", "--json",
                     "--root", str(root)]) == EXIT_OK
        v = json.loads(capsys.readouterr().out)["validation"]
        assert v["ok"] and v["checks"] == "65/65 passed" and v["latest"] == "2026-01"
        record = json.loads((_paths(root)[0].parent / "validation.json").read_text("utf-8"))
        assert record["ok"] and record["config_sha256"] == _sha(_paths(root)[0])

    def test_a_yaml_syntax_error_has_its_line(self, root, book, tmp_path, capsys):
        _draft(root, book)
        text = _paths(root)[0].read_text("utf-8").splitlines()
        text.insert(4, "broken: [unclosed")
        edited = tmp_path / "edited.yaml"
        edited.write_text("\n".join(text), encoding="utf-8")
        capsys.readouterr()
        assert main(["validate-config", "--client", "acme_ltd", "--draft", "--from",
                     str(edited), "--json", "--root", str(root)]) == EXIT_DATA
        errors = json.loads(capsys.readouterr().out)["validation"]["errors"]
        assert errors[0]["message"].startswith("Not valid YAML")
        assert errors[0]["context_line"] == 5 and errors[0]["line"] >= 5

    def test_a_loader_error_points_at_the_key(self, root, book, tmp_path, capsys):
        _draft(root, book)
        text = _paths(root)[0].read_text("utf-8").replace("date_order: newest_first",
                                                          "date_order: sideways")
        line = next(i for i, l in enumerate(text.splitlines(), 1) if "date_order:" in l)
        edited = tmp_path / "edited.yaml"
        edited.write_text(text, encoding="utf-8")
        capsys.readouterr()
        assert main(["validate-config", "--client", "acme_ltd", "--draft", "--from",
                     str(edited), "--root", str(root)]) == EXIT_DATA
        out = capsys.readouterr().out
        assert "INVALID" in out and f"error line {line}:" in out and "date_order" in out

    def test_an_anchor_missing_from_the_workbook_is_an_error(self, root, book, tmp_path,
                                                             capsys):
        _draft(root, book)
        text = _paths(root)[0].read_text("utf-8").replace("header_label: Income\n",
                                                          "header_label: No Such Row\n")
        edited = tmp_path / "edited.yaml"
        edited.write_text(text, encoding="utf-8")
        capsys.readouterr()
        assert main(["validate-config", "--client", "acme_ltd", "--draft", "--from",
                     str(edited), "--root", str(root)]) == EXIT_DATA
        assert "Reading the source failed" in capsys.readouterr().out

    def test_from_needs_draft(self, root, book, tmp_path, capsys):
        _draft(root, book)
        (tmp_path / "e.yaml").write_text("x: 1", encoding="utf-8")
        assert main(["validate-config", "--client", "acme_ltd", "--from",
                     str(tmp_path / "e.yaml"), "--root", str(root)]) == EXIT_USAGE

    def test_a_live_config(self, capsys):
        assert main(["validate-config", "--client", "test_clean", "--root",
                     str(ROOT)]) == EXIT_OK
        assert "VALID" in capsys.readouterr().out


class TestApprove:
    def test_approve_makes_the_draft_live(self, root, book, capsys):
        _draft(root, book)
        capsys.readouterr()
        draft, _, live, approval = _paths(root)
        assert main(["approve-config", "--client", "acme_ltd", "--by", "A. Reviewer",
                     "--json", "--root", str(root)]) == EXIT_OK
        rec = json.loads(capsys.readouterr().out)["approval"]
        assert live.read_bytes() == draft.read_bytes()
        stored = json.loads(approval.read_text("utf-8"))
        assert stored == rec
        assert rec["approved_by"] == "A. Reviewer" and rec["name_is_self_declared"]
        assert rec["config_sha256"] == _sha(live) and rec["previous_config_sha256"] is None
        assert rec["approved_at_utc"].endswith("+00:00")
        assert rec["validation"]["checks"] == "65/65 passed"
        assert main(["report", "--client", "acme_ltd", "--period", "2026-01",
                     "--provider", "deterministic", "--root", str(root)]) == EXIT_OK

    def test_a_name_is_required(self, root, book, capsys):
        _draft(root, book)
        assert main(["approve-config", "--client", "acme_ltd", "--by", "  ",
                     "--root", str(root)]) == EXIT_USAGE
        assert not _paths(root)[2].exists()

    def test_an_unchanged_failed_draft_is_refused_without_rerunning(self, root, book,
                                                                     tmp_path, capsys,
                                                                     monkeypatch):
        _draft(root, book)
        text = _paths(root)[0].read_text("utf-8").replace("date_order: newest_first",
                                                          "date_order: sideways")
        (tmp_path / "bad.yaml").write_text(text, encoding="utf-8")
        main(["validate-config", "--client", "acme_ltd", "--draft", "--from",
              str(tmp_path / "bad.yaml"), "--root", str(root)])
        capsys.readouterr()
        from finance_engine import onboarding
        monkeypatch.setattr(onboarding, "validate_config",
                            lambda *a, **k: pytest.fail("must not re-validate"))
        assert main(["approve-config", "--client", "acme_ltd", "--by", "A. Reviewer",
                     "--root", str(root)]) == EXIT_USAGE
        assert "has not changed since it failed validation" in capsys.readouterr().err
        assert not _paths(root)[2].exists()

    def test_an_invalid_draft_never_goes_live(self, root, book, tmp_path, capsys):
        _draft(root, book)
        draft = _paths(root)[0]
        draft.write_text(draft.read_text("utf-8").replace("date_order: newest_first",
                                                          "date_order: sideways"),
                         encoding="utf-8")                 # edited by hand, not validated
        assert main(["approve-config", "--client", "acme_ltd", "--by", "A. Reviewer",
                     "--root", str(root)]) == EXIT_USAGE
        assert "failed validation" in capsys.readouterr().err
        assert not _paths(root)[2].exists()

    def test_reapproving_the_same_yaml_is_refused_and_history_is_kept(self, root, book,
                                                                       tmp_path, capsys):
        _draft(root, book)
        main(["approve-config", "--client", "acme_ltd", "--by", "A. Reviewer",
              "--root", str(root)])
        assert main(["approve-config", "--client", "acme_ltd", "--by", "A. Reviewer",
                     "--root", str(root)]) == EXIT_USAGE
        assert "already equals this draft" in capsys.readouterr().err
        draft, _, live, approval = _paths(root)
        first = _sha(live)
        draft.write_text(draft.read_text("utf-8").replace("oneoff_multiple: 3.0",
                                                          "oneoff_multiple: 4.0"),
                         encoding="utf-8")
        assert main(["approve-config", "--client", "acme_ltd", "--by", "B. Partner",
                     "--root", str(root)]) == EXIT_OK
        rec = json.loads(approval.read_text("utf-8"))
        assert rec["approved_by"] == "B. Partner"
        assert rec["previous_config_sha256"] == first
        assert [h["approved_by"] for h in rec["history"]] == ["A. Reviewer"]

    def test_plain_init_client_is_unchanged(self, root, book, capsys):
        assert main(["init-client", "--file", str(book), "--client-id", "plain_co",
                     "--name", "Plain", "--root", str(root)]) == EXIT_OK
        assert (root / "config" / "clients" / "plain_co.yaml").exists()
        assert not (root / "data" / "clients" / "plain_co" / "draft").exists()
