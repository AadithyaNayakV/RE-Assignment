"""Review workflow (Phase 5D, part 1): draft -> in_review -> approved / changes_requested.

Files only. Every decision records the SHA-256 of report.md and metrics.json; a change to
either makes the old decision stop counting. Names are self-declared. A send-back comment
can reach a cloud commentary writer as plain reviewer notes; the deterministic template
and every golden output are unaffected.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import yaml

from finance_engine import review
from finance_engine.cli import EXIT_DATA, EXIT_OK, EXIT_USAGE, main
from finance_engine.commentary import orchestrator
from finance_engine.commentary.orchestrator import first_prompt
from finance_engine.review import ReviewError
from tests.helpers import build_monthly_workbook, write_monthly_config
from tests.test_commentary_providers import SECRET, Script, _good, gemini_factory

PERIOD = "2025-06"
TS = [re.compile(r'"generated_at_utc": "[^"]*"'), re.compile(r"\*\*Generated:\*\* [^\n]*")]


@pytest.fixture
def root(tmp_path):
    write_monthly_config(tmp_path / "config" / "clients")
    write_monthly_config(tmp_path / "config" / "clients", client_id="other_co")
    for cid in ("multi_co", "other_co"):
        src = tmp_path / "data" / "clients" / cid / "source"
        src.mkdir(parents=True)
        build_monthly_workbook(src / "book.xlsx", [f"2025-{m:02d}" for m in range(1, 13)])
    return tmp_path


def _report(root, client="multi_co", *scope, provider="deterministic") -> Path:
    scope = scope or ("--period", PERIOD)
    code = main(["report", "--client", client, *scope, "--provider", provider,
                 "--root", str(root)])
    assert code in (EXIT_OK, EXIT_DATA)
    leaf = {"--period": scope[-1], "--scope": "all"}.get(scope[0])
    if "--year" in scope:
        leaf = f"year-{scope[-1]}"
    return root / "output" / "clients" / client / leaf


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _masked(path: Path) -> str:
    text = path.read_text("utf-8")
    for pattern in TS:
        text = pattern.sub("<TS>", text)
    return text


@pytest.fixture
def folder(root, capsys):
    out = _report(root)
    capsys.readouterr()
    return out


class TestStateMachine:
    def test_a_new_report_is_a_draft(self, folder):
        st = review.status(folder)
        assert (st.state, st.label, st.entries) == ("draft", "draft", 0)

    def test_submit_then_approve(self, folder):
        e = review.act(folder, review.SUBMIT, "A. Writer")
        assert review.status(folder).state == "in_review"
        copy = review.review_dir(folder) / e["version_file"]
        assert copy.read_bytes() == (folder / "report.md").read_bytes()
        review.act(folder, review.APPROVE, "B. Partner", "Looks right")
        assert review.status(folder).state == "approved"

    def test_send_back_then_resubmit(self, folder):
        review.act(folder, review.SUBMIT, "A. Writer")
        review.act(folder, review.SEND_BACK, "B. Partner", "Re-check the rent line")
        assert review.status(folder).state == "changes_requested"
        review.act(folder, review.SUBMIT, "A. Writer")
        assert review.status(folder).state == "in_review"

    @pytest.mark.parametrize("steps, refused", [
        ([], review.APPROVE),
        ([], review.SEND_BACK),
        ([review.SUBMIT], review.SUBMIT),
        ([review.SUBMIT, review.APPROVE], review.APPROVE),
        ([review.SUBMIT, review.APPROVE], review.SUBMIT),
        ([review.SUBMIT, review.APPROVE], review.SEND_BACK),
        ([review.SUBMIT, review.SEND_BACK], review.APPROVE),
        ([review.SUBMIT, review.SEND_BACK], review.SEND_BACK),
    ])
    def test_refused_transitions_write_nothing(self, folder, steps, refused):
        for step in steps:
            review.act(folder, step, "A. Person", "a comment")
        before = review.read_log(folder)
        with pytest.raises(ReviewError, match="Cannot"):
            review.act(folder, refused, "A. Person", "a comment")
        assert review.read_log(folder) == before

    @pytest.mark.parametrize("comment", [None, "", "   \n  "])
    def test_send_back_needs_a_comment(self, folder, comment):
        review.act(folder, review.SUBMIT, "A. Writer")
        with pytest.raises(ReviewError, match="non-empty --comment"):
            review.act(folder, review.SEND_BACK, "B. Partner", comment)
        assert review.status(folder).state == "in_review"

    @pytest.mark.parametrize("name", [None, "", "   ", "x" * 81, "bad\x07name"])
    def test_a_name_is_required(self, folder, name):
        with pytest.raises(ReviewError):
            review.act(folder, review.SUBMIT, name)
        assert review.read_log(folder) == []

    def test_no_report_no_review(self, tmp_path):
        with pytest.raises(ReviewError, match="No report"):
            review.act(tmp_path, review.SUBMIT, "A. Writer")


class TestHashes:
    def test_entry_records_who_what_when_and_the_hashes(self, folder):
        e = review.act(folder, review.SUBMIT, "  A.   Writer ", "first pass")
        assert e["name"] == "A. Writer" and e["name_is_self_declared"] is True
        assert e["action"] == "submit" and e["comment"] == "first pass"
        assert e["time_utc"].endswith("+00:00")
        assert e["report_sha256"] == _sha(folder / "report.md")
        assert e["metrics_sha256"] == _sha(folder / "metrics.json")
        assert e["provider"] == "deterministic" and e["data_left_machine"] is False
        assert e["prev_entry_sha256"] is None

    @pytest.mark.parametrize("changed", ["report.md", "metrics.json"])
    def test_a_change_invalidates_the_approval(self, folder, changed):
        review.act(folder, review.SUBMIT, "A. Writer")
        review.act(folder, review.APPROVE, "B. Partner")
        with (folder / changed).open("a", encoding="utf-8") as fh:
            fh.write("\n")
        st = review.status(folder)
        assert st.state == "draft" and st.label == "draft (changed since approval)"
        with pytest.raises(ReviewError, match="changed since approval"):
            review.act(folder, review.APPROVE, "B. Partner")
        review.act(folder, review.SUBMIT, "A. Writer")      # a new submission is needed
        assert review.status(folder).state == "in_review"

    def test_regenerating_with_a_changed_config_invalidates_the_approval(self, root,
                                                                         folder, capsys):
        review.act(folder, review.SUBMIT, "A. Writer")
        review.act(folder, review.APPROVE, "B. Partner")
        write_monthly_config(root / "config" / "clients",
                             overrides={"thresholds": {"significant_mom_pct": 99.0}})
        _report(root)
        assert review.status(folder).label == "draft (changed since approval)"

    def test_a_change_while_in_review_blocks_the_decision(self, folder):
        review.act(folder, review.SUBMIT, "A. Writer")
        (folder / "report.md").write_text("edited", encoding="utf-8")
        assert review.status(folder).label == "draft (changed since submission)"
        for action in (review.APPROVE, review.SEND_BACK):
            with pytest.raises(ReviewError):
                review.act(folder, action, "B. Partner", "x")

    def test_the_log_is_append_only_and_chained(self, folder):
        review.act(folder, review.SUBMIT, "A. Writer")
        first = review.read_log(folder)
        review.act(folder, review.SEND_BACK, "B. Partner", "fix wording")
        review.act(folder, review.SUBMIT, "A. Writer")
        log = review.read_log(folder)
        assert log[:1] == first and len(log) == 3
        assert review.chain_intact(log)

    def test_an_edited_log_is_detected_and_blocks_new_decisions(self, folder):
        review.act(folder, review.SUBMIT, "A. Writer", "original")
        review.act(folder, review.SEND_BACK, "B. Partner", "fix wording")
        path = review.review_dir(folder) / review.LOG_NAME
        doc = json.loads(path.read_text("utf-8"))
        doc["entries"][0]["comment"] = "rewritten history"
        path.write_text(json.dumps(doc), encoding="utf-8")
        assert "chain is broken" in review.status(folder).label
        with pytest.raises(ReviewError, match="hash chain is broken"):
            review.act(folder, review.SUBMIT, "A. Writer")


class TestQueueAndScopes:
    def test_queue_lists_in_review_across_clients(self, root, folder, capsys):
        other = _report(root, "other_co")
        year = _report(root, "multi_co", "--scope", "year", "--year", "2025")
        review.act(folder, review.SUBMIT, "A. Writer")
        review.act(other, review.SUBMIT, "A. Writer")
        review.act(year, review.SUBMIT, "A. Writer")
        review.act(other, review.APPROVE, "B. Partner")
        queue = review.queue(root / "output" / "clients")
        assert sorted(q["folder"] for q in queue) == ["multi_co/2025-06",
                                                      "multi_co/year-2025"]

    def test_a_review_folder_is_never_mistaken_for_a_report(self, folder):
        review.act(folder, review.SUBMIT, "A. Writer")
        dirs = review.report_dirs(folder.parents[2] / "clients")
        assert dirs == [folder]


class TestCli:
    def _rv(self, root, *args):
        return main(["review", *args, "--client", "multi_co", "--period", PERIOD,
                     "--root", str(root)])

    def test_submit_send_back_status_log(self, root, folder, capsys):
        assert self._rv(root, "submit", "--by", "A. Writer") == EXIT_OK
        assert "status: in_review" in capsys.readouterr().out
        assert self._rv(root, "send-back", "--by", "B. Partner") == EXIT_USAGE
        assert "non-empty --comment" in capsys.readouterr().err
        assert self._rv(root, "send-back", "--by", "B. Partner", "--comment",
                        "Re-check rent") == EXIT_OK
        capsys.readouterr()
        assert self._rv(root, "status", "--json") == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["state"] == "changes_requested" and payload["entries"] == 2
        assert self._rv(root, "log") == EXIT_OK
        out = capsys.readouterr().out
        assert "submit" in out and "send_back" in out and "Re-check rent" in out

    def test_approve_without_a_name_is_refused(self, root, folder, capsys):
        self._rv(root, "submit", "--by", "A. Writer")
        assert self._rv(root, "approve") == EXIT_USAGE
        assert "--by NAME is required" in capsys.readouterr().err

    def test_year_scope_and_queue(self, root, folder, capsys):
        _report(root, "multi_co", "--scope", "year", "--year", "2025")
        assert main(["review", "submit", "--client", "multi_co", "--scope", "year",
                     "--year", "2025", "--by", "A. Writer", "--root", str(root)]) == EXIT_OK
        capsys.readouterr()
        assert main(["review", "queue", "--json", "--root", str(root)]) == EXIT_OK
        queue = json.loads(capsys.readouterr().out)["in_review"]
        assert [q["leaf"] for q in queue] == ["year-2025"]

    def test_a_bad_client_id_is_refused(self, root, capsys):
        assert main(["review", "status", "--client", "../multi_co", "--period", PERIOD,
                     "--root", str(root)]) == EXIT_USAGE


class TestReviewerNotes:
    def _providers(self, root, monkeypatch):
        (root / "config" / "providers.yaml").write_text(yaml.safe_dump({
            "order": ["gemini", "deterministic"], "allow_cloud": True,
            "providers": {"gemini": {"enabled": True, "model": "test-model-1",
                                     "max_attempts": 1, "api_key_env": "GEMINI_API_KEY"}}}),
            encoding="utf-8")
        monkeypatch.setenv("GEMINI_API_KEY", SECRET)

    def test_the_last_send_back_reaches_a_cloud_writer(self, root, folder, monkeypatch,
                                                       capsys):
        review.act(folder, review.SUBMIT, "A. Writer")
        review.act(folder, review.SEND_BACK, "B. Partner", "Lead with the rent line.")
        self._providers(root, monkeypatch)
        doc = json.loads((folder / "metrics.json").read_text("utf-8"))
        script = Script(_good(doc))
        monkeypatch.setattr(orchestrator, "CLIENT_FACTORIES",
                            {"gemini": gemini_factory(script)})
        _report(root, provider="auto")
        err = capsys.readouterr().err
        assert "reviewer notes: passing the send-back comment by B. Partner" in err
        assert "<reviewer_notes>\nLead with the rent line.\n</reviewer_notes>" in \
            script.prompts[0]
        log = json.loads((folder / "commentary_log.json").read_text("utf-8"))
        assert log["reviewer_notes"]["passed_to_writer"] is True
        assert "the brief and the reviewer's notes were sent" in \
            (folder / "report.md").read_text("utf-8")
        assert review.status(folder).label == "draft (changed since send-back)"

    def test_no_notes_without_a_send_back(self, root, folder, monkeypatch, capsys):
        review.act(folder, review.SUBMIT, "A. Writer")
        self._providers(root, monkeypatch)
        doc = json.loads((folder / "metrics.json").read_text("utf-8"))
        script = Script(_good(doc))
        monkeypatch.setattr(orchestrator, "CLIENT_FACTORIES",
                            {"gemini": gemini_factory(script)})
        _report(root, provider="auto")
        assert "<reviewer_notes>" not in script.prompts[0]
        assert "reviewer_notes" not in json.loads(
            (folder / "commentary_log.json").read_text("utf-8"))

    def test_the_prompt_without_notes_is_unchanged(self, folder):
        doc = json.loads((folder / "metrics.json").read_text("utf-8"))
        assert first_prompt(doc, "BRIEF") == first_prompt(doc, "BRIEF", None) \
            == first_prompt(doc, "BRIEF", "  ")

    def test_the_deterministic_report_ignores_the_review_folder(self, root, folder,
                                                                capsys):
        before = {n: _masked(folder / n) for n in ("report.md", "metrics.json")}
        review.act(folder, review.SUBMIT, "A. Writer")
        review.act(folder, review.SEND_BACK, "B. Partner", "Lead with the rent line.")
        _report(root)
        assert "shown beside the draft" in capsys.readouterr().err
        assert {n: _masked(folder / n) for n in before} == before
        assert not (folder / "commentary_log.json").exists()
