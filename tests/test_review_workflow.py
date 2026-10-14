"""Review workflow (Phase 5G): a generated report is Ready for review; a reviewer marks it
as reviewed or sends it back; regenerating after a send-back makes it Ready again.

Files only. Every decision records the SHA-256 of report.md and metrics.json; a change to
either outside `report` takes the report out of review. Names are self-declared. A
send-back comment can reach a cloud commentary writer as plain reviewer notes; the
deterministic template and every golden output are unaffected.
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


def _legacy(folder: Path) -> None:
    """Remove the review folder: a report generated before automatic submission."""
    import shutil
    shutil.rmtree(review.review_dir(folder))


class TestStateMachine:
    def test_a_generated_report_is_ready_for_review(self, folder):
        st = review.status(folder)
        assert (st.state, st.label, st.entries) == ("ready_for_review",
                                                    "ready for review", 1)
        e = review.read_log(folder)[0]
        assert (e["action"], e["name"], e["automatic"], e["name_is_self_declared"]) == \
            ("submit", "system", True, False)
        assert e["provider"] == "deterministic"                 # written by the provider
        copy = review.review_dir(folder) / e["version_file"]
        assert copy.read_bytes() == (folder / "report.md").read_bytes()

    def test_mark_as_reviewed(self, folder):
        review.act(folder, review.MARK_REVIEWED, "B. Partner", "Looks right")
        st = review.status(folder)
        assert (st.state, st.label) == ("reviewed", "reviewed")

    def test_send_back_then_regenerate_is_ready_again(self, root, folder, capsys):
        review.act(folder, review.SEND_BACK, "B. Partner", "Re-check the rent line")
        assert review.status(folder).label == "changes requested"
        _report(root)
        st = review.status(folder)
        assert st.state == "ready_for_review" and st.last["automatic"] is True
        assert [e["action"] for e in review.read_log(folder)] == ["submit", "send_back",
                                                                  "submit"]

    @pytest.mark.parametrize("steps, refused", [
        ([], review.SUBMIT),                                  # already ready for review
        ([review.MARK_REVIEWED], review.MARK_REVIEWED),
        ([review.MARK_REVIEWED], review.SEND_BACK),
        ([review.MARK_REVIEWED], review.SUBMIT),
        ([review.SEND_BACK], review.MARK_REVIEWED),
        ([review.SEND_BACK], review.SEND_BACK),
    ])
    def test_refused_transitions_write_nothing(self, folder, steps, refused):
        for step in steps:
            review.act(folder, step, "B. Partner", "a comment")
        before = review.read_log(folder)
        with pytest.raises(ReviewError, match="Cannot"):
            review.act(folder, refused, "C. Third", "a comment")
        assert review.read_log(folder) == before

    @pytest.mark.parametrize("comment", [None, "", "   \n  "])
    def test_send_back_needs_a_comment(self, folder, comment):
        with pytest.raises(ReviewError, match="non-empty --comment"):
            review.act(folder, review.SEND_BACK, "B. Partner", comment)
        assert review.status(folder).state == "ready_for_review"

    @pytest.mark.parametrize("name", [None, "", "   ", "x" * 81, "bad\x07name", "System",
                                      " SYSTEM "])
    def test_a_name_is_required_and_system_is_reserved(self, folder, name):
        before = review.read_log(folder)
        with pytest.raises(ReviewError):
            review.act(folder, review.MARK_REVIEWED, name)
        assert review.read_log(folder) == before

    def test_no_report_no_review(self, tmp_path):
        with pytest.raises(ReviewError, match="No report"):
            review.act(tmp_path, review.MARK_REVIEWED, "B. Partner")

    def test_a_report_from_before_automatic_review_is_not_in_review(self, root, folder):
        _legacy(folder)
        st = review.status(folder)
        assert (st.state, st.label, st.entries) == ("not_in_review", "not in review", 0)
        assert review.queue(root / "output" / "clients") == []
        with pytest.raises(ReviewError, match="needs ready for review"):
            review.act(folder, review.MARK_REVIEWED, "B. Partner")

    def test_submit_generated_does_nothing_for_a_version_already_in_review(self, folder):
        assert review.submit_generated(folder) is None
        assert len(review.read_log(folder)) == 1


class TestHashes:
    def test_entry_records_who_what_when_and_the_hashes(self, folder):
        e = review.act(folder, review.MARK_REVIEWED, "  B.   Partner ", "checked")
        assert e["name"] == "B. Partner" and e["name_is_self_declared"] is True
        assert e["action"] == "mark_reviewed" and e["comment"] == "checked"
        assert "automatic" not in e
        assert e["time_utc"].endswith("+00:00")
        assert e["report_sha256"] == _sha(folder / "report.md")
        assert e["metrics_sha256"] == _sha(folder / "metrics.json")
        assert e["provider"] == "deterministic" and e["data_left_machine"] is False
        assert e["prev_entry_sha256"] == review._entry_sha(review.read_log(folder)[0])

    @pytest.mark.parametrize("changed", ["report.md", "metrics.json"])
    def test_an_edit_takes_a_reviewed_report_out_of_review(self, folder, changed):
        review.act(folder, review.MARK_REVIEWED, "B. Partner")
        with (folder / changed).open("a", encoding="utf-8") as fh:
            fh.write("\n")
        st = review.status(folder)
        assert st.state == "not_in_review"
        assert st.label == "not in review (changed since it was reviewed)"
        with pytest.raises(ReviewError, match="changed since it was reviewed"):
            review.act(folder, review.MARK_REVIEWED, "B. Partner")
        review.act(folder, review.SUBMIT, "A. Writer")     # whoever edited it submits it
        assert review.status(folder).state == "ready_for_review"

    def test_regenerating_a_reviewed_report_puts_it_back_in_review(self, root, folder,
                                                                   capsys):
        review.act(folder, review.MARK_REVIEWED, "B. Partner")
        write_monthly_config(root / "config" / "clients",
                             overrides={"thresholds": {"significant_mom_pct": 99.0}})
        _report(root)
        st = review.status(folder)
        assert st.label == "ready for review" and st.last["automatic"] is True

    def test_an_edit_while_ready_blocks_the_decision(self, folder):
        (folder / "report.md").write_text("edited", encoding="utf-8")
        assert review.status(folder).label == "not in review (changed since it was submitted)"
        for action in (review.MARK_REVIEWED, review.SEND_BACK):
            with pytest.raises(ReviewError):
                review.act(folder, action, "B. Partner", "x")

    def test_the_log_is_append_only_and_chained(self, root, folder, capsys):
        first = review.read_log(folder)
        review.act(folder, review.SEND_BACK, "B. Partner", "fix wording")
        _report(root)
        log = review.read_log(folder)
        assert log[:1] == first and len(log) == 3
        assert review.chain_intact(log)

    def test_an_edited_log_is_detected_and_blocks_new_decisions(self, root, folder,
                                                                capsys):
        review.act(folder, review.SEND_BACK, "B. Partner", "fix wording")
        path = review.review_dir(folder) / review.LOG_NAME
        doc = json.loads(path.read_text("utf-8"))
        doc["entries"][0]["comment"] = "rewritten history"
        path.write_text(json.dumps(doc), encoding="utf-8")
        assert "chain is broken" in review.status(folder).label
        with pytest.raises(ReviewError, match="hash chain is broken"):
            review.act(folder, review.SUBMIT, "A. Writer")
        capsys.readouterr()
        _report(root)                                  # the report is still written...
        assert "warning: not put in review" in capsys.readouterr().err
        assert json.loads(path.read_text("utf-8")) == doc     # ...and the log untouched


class TestLegacyLogs:
    def test_an_approve_entry_reads_as_reviewed(self, folder):
        entries = review.read_log(folder)
        st = review.status(folder)
        legacy = {"time_utc": "2026-10-01T00:00:00+00:00", "name": "B. Partner",
                  "name_is_self_declared": True, "action": "approve", "comment": "",
                  "report_sha256": st.report_sha256, "metrics_sha256": st.metrics_sha256,
                  "prev_entry_sha256": review._entry_sha(entries[-1])}
        review._write_log(folder, entries + [legacy])
        assert (review.status(folder).state, review.status(folder).label) == \
            ("reviewed", "reviewed")

    def test_the_old_setting_name_is_still_read(self, root):
        (root / "config" / "review.yaml").write_text(
            "require_different_approver: false\n", encoding="utf-8")
        assert review.load_settings(root) == {"require_different_reviewer": False}


class TestDifferentReviewer:
    def test_an_automatic_submission_can_be_marked_by_anyone(self, folder):
        assert review.human_submitted(review.read_log(folder)) is None
        review.act(folder, review.MARK_REVIEWED, "A. Writer")
        assert review.status(folder).state == "reviewed"

    def test_a_person_cannot_mark_a_version_they_submitted(self, folder):
        (folder / "report.md").write_text("edited by hand", encoding="utf-8")
        review.act(folder, review.SUBMIT, "A. Writer")
        with pytest.raises(ReviewError, match="require_different_reviewer: true"):
            review.act(folder, review.MARK_REVIEWED, "  a.   WRITER ")
        assert review.status(folder).state == "ready_for_review"
        review.act(folder, review.MARK_REVIEWED, "B. Partner")
        assert review.status(folder).state == "reviewed"

    def test_the_setting_can_be_turned_off(self, folder):
        (folder / "report.md").write_text("edited by hand", encoding="utf-8")
        review.act(folder, review.SUBMIT, "A. Writer")
        review.act(folder, review.MARK_REVIEWED, "A. Writer",
                   settings={"require_different_reviewer": False})
        assert review.status(folder).state == "reviewed"

    def test_regenerating_clears_the_human_submitter(self, root, folder, capsys):
        (folder / "report.md").write_text("edited by hand", encoding="utf-8")
        review.act(folder, review.SUBMIT, "A. Writer")
        review.act(folder, review.SEND_BACK, "B. Partner", "fix it")
        _report(root)
        review.act(folder, review.MARK_REVIEWED, "A. Writer")      # system submitted it

    def test_config_file(self, root, folder, capsys):
        assert review.load_settings(root) == {"require_different_reviewer": True}
        (root / "config" / "review.yaml").write_text(
            "require_different_reviewer: false\n", encoding="utf-8")
        assert review.load_settings(root) == {"require_different_reviewer": False}
        (folder / "report.md").write_text("edited by hand", encoding="utf-8")
        args = ["--client", "multi_co", "--period", PERIOD, "--root", str(root)]
        assert main(["review", "submit", "--by", "A. Writer", *args]) == EXIT_OK
        assert main(["review", "mark-reviewed", "--by", "A. Writer", *args]) == EXIT_OK
        (root / "config" / "review.yaml").write_text("bogus: true\n", encoding="utf-8")
        with pytest.raises(ReviewError, match="unknown setting"):
            review.load_settings(root)

    def test_the_repo_setting_is_off(self):
        # config/review.yaml lets the submitter mark their own version as reviewed; the
        # code default (no file) is still on.
        from tests.test_service_layer import ROOT
        assert review.load_settings(ROOT)["require_different_reviewer"] is False

    def test_cli_refusal_names_the_setting(self, root, folder, capsys):
        (folder / "report.md").write_text("edited by hand", encoding="utf-8")
        args = ["--client", "multi_co", "--period", PERIOD, "--root", str(root)]
        main(["review", "submit", "--by", "A. Writer", *args])
        assert main(["review", "mark-reviewed", "--by", "A. Writer", *args]) == EXIT_USAGE
        assert "config/review.yaml sets require_different_reviewer: true" in \
            capsys.readouterr().err


class TestQueueAndScopes:
    def test_the_queue_holds_reports_ready_for_review(self, root, folder, capsys):
        other = _report(root, "other_co")
        _report(root, "multi_co", "--scope", "year", "--year", "2025")
        review.act(other, review.MARK_REVIEWED, "B. Partner")
        queue = review.queue(root / "output" / "clients")
        assert sorted(q["folder"] for q in queue) == ["multi_co/2025-06",
                                                      "multi_co/year-2025"]
        assert all(q["submitted_by"] == "system" and q["automatic"] for q in queue)
        from finance_engine.service import core
        data = core.review_queue(root)
        assert data["count"] == 2 and len(data["ready_for_review"]) == 2

    def test_a_review_folder_is_never_mistaken_for_a_report(self, folder):
        dirs = review.report_dirs(folder.parents[2] / "clients")
        assert dirs == [folder]


class TestCli:
    def _rv(self, root, *args):
        return main(["review", *args, "--client", "multi_co", "--period", PERIOD,
                     "--root", str(root)])

    def test_report_says_it_is_ready_for_review(self, root, capsys):
        assert main(["report", "--client", "multi_co", "--period", PERIOD, "--provider",
                     "deterministic", "--root", str(root), "--json"]) in (EXIT_OK, EXIT_DATA)
        assert json.loads(capsys.readouterr().out)["review"] == "ready for review"

    def test_send_back_mark_reviewed_status_log(self, root, folder, capsys):
        assert self._rv(root, "send-back", "--by", "B. Partner") == EXIT_USAGE
        assert "non-empty --comment" in capsys.readouterr().err
        assert self._rv(root, "send-back", "--by", "B. Partner", "--comment",
                        "Re-check rent") == EXIT_OK
        assert "status: changes requested" in capsys.readouterr().out
        assert self._rv(root, "status", "--json") == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["state"] == "changes_requested" and payload["entries"] == 2
        _report(root)
        assert self._rv(root, "mark-reviewed", "--by", "B. Partner") == EXIT_OK
        assert "status: reviewed" in capsys.readouterr().out
        assert self._rv(root, "log") == EXIT_OK
        out = capsys.readouterr().out
        assert "send_back" in out and "mark_reviewed" in out and "Re-check rent" in out

    def test_mark_reviewed_without_a_name_is_refused(self, root, folder, capsys):
        assert self._rv(root, "mark-reviewed") == EXIT_USAGE
        assert "--by NAME is required" in capsys.readouterr().err

    def test_approve_is_not_a_review_action(self, root, folder, capsys):
        with pytest.raises(SystemExit):
            self._rv(root, "approve", "--by", "B. Partner")

    def test_year_scope_and_queue(self, root, folder, capsys):
        _report(root, "multi_co", "--scope", "year", "--year", "2025")
        review.act(folder, review.MARK_REVIEWED, "B. Partner")
        capsys.readouterr()
        assert main(["review", "queue", "--json", "--root", str(root)]) == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        assert [q["leaf"] for q in data["ready_for_review"]] == ["year-2025"]
        assert data["count"] == 1

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
        st = review.status(folder)
        assert st.label == "ready for review" and st.last["provider"] == "gemini"

    def test_no_notes_without_a_send_back(self, root, folder, monkeypatch, capsys):
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
        review.act(folder, review.SEND_BACK, "B. Partner", "Lead with the rent line.")
        _report(root)
        assert "shown beside the draft" in capsys.readouterr().err
        assert {n: _masked(folder / n) for n in before} == before
        assert not (folder / "commentary_log.json").exists()
