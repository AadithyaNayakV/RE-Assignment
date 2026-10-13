"""Cloud commentary providers (Phase 5C, part 1): generate, validate, retry, fall back.

No real network call is made anywhere in this file. The SDK clients are fakes; where the
SDK call shape itself is checked, a fake `google.genai` / `anthropic` module is put in
sys.modules for the duration of the test.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
import yaml

from finance_engine.cli import EXIT_OK, main
from finance_engine.commentary import orchestrator, providers
from finance_engine.commentary.deterministic import DeterministicProvider
from finance_engine.commentary.orchestrator import validate, write_commentary
from finance_engine.commentary.providers import (ClaudeAPIProvider, GeminiProvider,
                                                 ProviderSettings, ProvidersConfig,
                                                 classify)
from finance_engine.config import ClientConfig
from finance_engine.credentials import EnvCredentialStore, redact
from finance_engine.metrics import build_metrics
from finance_engine.report import build_report
from finance_engine.sources.excel import ExcelSource
from finance_engine.spans import build_all_metrics, build_year_metrics
from tests.helpers import build_monthly_workbook, write_monthly_config

SECRET = "AIzaSyTESTSECRET0123456789abcdefghij"
CLAUDE_SECRET = "sk-ant-api03-TESTSECRET-0123456789"


# ---- fixtures and fakes ---------------------------------------------------------------

@pytest.fixture
def root(tmp_path):
    write_monthly_config(tmp_path / "config" / "clients")
    src = tmp_path / "data" / "clients" / "multi_co" / "source"
    src.mkdir(parents=True)
    build_monthly_workbook(src / "book.xlsx", [f"2025-{m:02d}" for m in range(1, 13)])
    return tmp_path


def _doc(root, scope="month"):
    cfg = ClientConfig.load_path(root / "config" / "clients" / "multi_co.yaml")
    ds = ExcelSource(root / "data" / "clients" / "multi_co" / "source" / "book.xlsx").load(cfg)
    if scope == "year":
        return build_year_metrics(ds, cfg, 2025, root)
    if scope == "all":
        return build_all_metrics(ds, cfg, root)
    return build_metrics(ds, cfg, "2025-06", project_root=root)


def _good(doc):
    """A commentary that passes every check: the template's own text."""
    return DeterministicProvider().generate(doc)


def _config(gemini=True, claude=False, allow_cloud=True, attempts=2):
    def spec(name, enabled, env):
        return ProviderSettings(name=name, enabled=enabled, cloud=True,
                                model="test-model-1", max_attempts=attempts,
                                api_key_env=env)
    return ProvidersConfig(order=["gemini", "claude-api", "deterministic"],
                           allow_cloud=allow_cloud,
                           providers={"gemini": spec("gemini", gemini, "GEMINI_API_KEY"),
                                      "claude-api": spec("claude-api", claude,
                                                         "ANTHROPIC_API_KEY")})


def _creds(**values):
    return EnvCredentialStore(["GEMINI_API_KEY", "ANTHROPIC_API_KEY"],
                              environ={"GEMINI_API_KEY": SECRET,
                                       "ANTHROPIC_API_KEY": CLAUDE_SECRET, **values})


class Script:
    """Answers in order; an exception instance is raised instead of answered."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.systems: list[str] = []

    def next(self, system, prompt):
        self.prompts.append(prompt)
        self.systems.append(system)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def gemini_factory(script: Script, model_version=None, thoughts=None, seen=None):
    def factory(api_key, settings):
        def generate_content(model, contents, config):
            if seen is not None:
                seen.append(config)
            text = script.next(config["system_instruction"], contents)
            return types.SimpleNamespace(
                text=text, candidates=[types.SimpleNamespace(finish_reason="STOP")],
                model_version=model_version,
                usage_metadata=types.SimpleNamespace(prompt_token_count=1200,
                                                     candidates_token_count=300,
                                                     thoughts_token_count=thoughts))
        return types.SimpleNamespace(models=types.SimpleNamespace(
            generate_content=generate_content))
    return factory


def claude_factory(script: Script, stop="end_turn", answered_by=None):
    def factory(api_key, settings):
        def create(model, max_tokens, system, messages, **extra):
            text = script.next(system, messages[0]["content"])
            return types.SimpleNamespace(
                stop_reason=stop, model=answered_by,
                content=[types.SimpleNamespace(type="text", text=text)],
                usage=types.SimpleNamespace(input_tokens=900, output_tokens=250))
        api = types.SimpleNamespace(create=create)
        return types.SimpleNamespace(messages=api, beta=types.SimpleNamespace(messages=api))
    return factory


class APIError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class RateLimitError(Exception):
    status_code = 429


class NotFoundError(Exception):
    status_code = 404


def _run(doc, script_g=None, script_c=None, **kw):
    factories = {}
    if script_g is not None:
        factories["gemini"] = gemini_factory(script_g)
    if script_c is not None:
        factories["claude-api"] = claude_factory(script_c)
    return write_commentary(doc, kw.pop("requested", "auto"), kw.pop("config", _config()),
                            kw.pop("creds", _creds()), factories=factories,
                            sleep=kw.pop("sleep", lambda seconds: None))


def _status(result):
    return {p["provider"]: p["status"] for p in result.log["providers"]}


# ---- the flow ---------------------------------------------------------------------------

class TestFlow:
    def test_passes_first_time(self, root):
        doc = _doc(root)
        script = Script(_good(doc))
        result = _run(doc, script)
        assert result.provider_id == "gemini" and result.attempt == 1
        assert result.data_left_machine is True
        assert _status(result) == {"gemini": "used"}
        assert result.log["tokens"] == {"calls": 1, "input": 1200, "output": 300,
                                        "thinking": 0}
        assert "only the compact brief" not in script.prompts[0]
        assert "<brief>" in script.prompts[0] and '"metrics"' not in script.prompts[0]
        assert "Questions for the reviewer" in script.systems[0]
        assert "`gemini` provider on its first attempt" in result.footer
        assert "Data left this machine: yes" in result.footer

    def test_fails_the_guard_then_passes_on_retry(self, root):
        doc = _doc(root)
        bad = _good(doc) + "\nRevenue was $123,456.78 for the month.\n"
        script = Script(bad, _good(doc))
        result = _run(doc, script)
        assert result.provider_id == "gemini" and result.attempt == 2
        first = result.log["providers"][0]["attempts"][0]
        assert first["passed"] is False
        assert any("'$123,456.78' is not a figure in the brief" in v
                   for v in first["violations"])
        retry = script.prompts[1]
        for violation in first["violations"]:
            assert violation in retry                # the exact messages, sent back
        assert "<draft>" in retry and "$123,456.78" in retry
        assert "second attempt" in result.footer

    def test_causal_wording_is_a_violation(self, root):
        doc = _doc(root)
        bad = _good(doc).replace("### Revenue", "Revenue rose because of a price rise.\n\n"
                                               "### Revenue", 1)
        script = Script(bad, _good(doc))
        result = _run(doc, script)
        violations = result.log["providers"][0]["attempts"][0]["violations"]
        assert any("Causal wording outside 'Questions for the reviewer'" in v
                   for v in violations)
        assert result.attempt == 2

    def test_fails_twice_then_the_next_provider_is_used(self, root):
        doc = _doc(root)
        bad = _good(doc) + "\nRevenue was $1.11.\n"
        gem, cla = Script(bad, bad), Script(_good(doc))
        result = _run(doc, gem, cla, config=_config(claude=True))
        assert _status(result) == {"gemini": "failed", "claude-api": "used"}
        assert len(gem.prompts) == 2 and len(cla.prompts) == 1
        assert result.provider_id == "claude-api"
        assert result.log["tokens"] == {"calls": 3, "input": 1200 * 2 + 900,
                                        "output": 300 * 2 + 250, "thinking": 0}

    def test_all_providers_fail_and_the_deterministic_report_is_produced(self, root):
        doc = _doc(root)
        bad = _good(doc) + "\nRevenue was $1.11.\n"
        result = _run(doc, Script(bad, bad), Script(bad, bad), config=_config(claude=True))
        assert _status(result) == {"gemini": "failed", "claude-api": "failed",
                                   "deterministic": "used"}
        assert result.provider_id == "deterministic" and result.text == _good(doc)
        assert result.guard.passed
        assert result.data_left_machine is True
        assert "whose drafts were not used" in result.footer

    def test_requested_single_provider_then_deterministic(self, root):
        doc = _doc(root)
        result = _run(doc, script_c=Script(_good(doc)), requested="claude-api",
                      config=_config(gemini=True, claude=True))
        assert [p["provider"] for p in result.log["providers"]] == ["claude-api"]
        assert result.provider_id == "claude-api"

    def test_truncated_output_is_a_violation(self, root):
        doc = _doc(root)
        script = Script(_good(doc), _good(doc))
        factory = gemini_factory(script)

        def truncating(api_key, settings):
            client = factory(api_key, settings)
            original = client.models.generate_content

            def generate_content(**kw):
                response = original(**kw)
                if len(script.prompts) == 1:
                    response.candidates[0].finish_reason = "MAX_TOKENS"
                return response
            client.models.generate_content = generate_content
            return client
        result = write_commentary(doc, "auto", _config(), _creds(),
                                  factories={"gemini": truncating})
        first = result.log["providers"][0]["attempts"][0]
        assert any("cut off at the token limit" in v for v in first["violations"])
        assert result.attempt == 2

    def test_failed_reconciliation_months_must_be_named(self, tmp_path):
        write_monthly_config(tmp_path / "config" / "clients")
        src = tmp_path / "data" / "clients" / "multi_co" / "source"
        src.mkdir(parents=True)
        # A failure in April, a month the December commentary never mentions.
        build_monthly_workbook(src / "book.xlsx", [f"2025-{m:02d}" for m in range(1, 13)],
                               overrides={("Total Income", "2025-04"): 9999.0})
        cfg = ClientConfig.load_path(tmp_path / "config" / "clients" / "multi_co.yaml")
        doc = build_metrics(ExcelSource(src / "book.xlsx").load(cfg), cfg, "2025-12",
                            project_root=tmp_path)
        assert "2025-04" not in _good(doc)
        problems = validate(_good(doc), doc)
        assert any("failed in 2025-04" in p for p in problems)
        assert validate(_good(doc) + "\nRevenue items failed to reconcile in 2025-04.\n",
                        doc) == []
        # The template is the final fallback; the report's own table names the months.
        assert validate(_good(doc), doc, require_periods=False) == []

    @pytest.mark.parametrize("scope", ["month", "year", "all"])
    def test_every_scope(self, root, scope):
        doc = _doc(root, scope)
        script = Script(_good(doc))
        result = _run(doc, script)
        assert result.provider_id == "gemini", result.log
        assert result.guard.passed
        assert "<brief>" in script.prompts[0]
        assert "# Brief:" in script.prompts[0]


# ---- skips --------------------------------------------------------------------------

class TestSkips:
    def test_a_disabled_provider_is_skipped(self, root):
        doc = _doc(root)
        result = _run(doc, Script(), config=_config(gemini=False))
        entry = result.log["providers"][0]
        assert entry["status"] == "skipped" and "disabled" in entry["reason"]
        assert result.provider_id == "deterministic" and not result.data_left_machine
        assert "Data left this machine: no" in result.footer

    def test_an_unkeyed_provider_is_skipped(self, root):
        doc = _doc(root)
        creds = EnvCredentialStore(["GEMINI_API_KEY"], environ={})
        result = _run(doc, Script(), creds=creds)
        assert result.log["providers"][0]["reason"] == \
            "GEMINI_API_KEY is not set (environment or .env)"

    def test_a_missing_package_is_skipped(self, root, monkeypatch):
        doc = _doc(root)
        monkeypatch.setattr(providers, "_importable", lambda module: False)
        result = write_commentary(doc, "auto", _config(), _creds(), factories={})
        assert "package google-genai is not installed" in \
            result.log["providers"][0]["reason"]

    def test_allow_cloud_false_skips_every_cloud_provider(self, root):
        doc = _doc(root)
        result = _run(doc, Script(), Script(),
                      config=_config(claude=True, allow_cloud=False))
        assert _status(result) == {"gemini": "skipped", "claude-api": "skipped",
                                   "deterministic": "used"}
        assert all("allow_cloud is false" in p["reason"]
                   for p in result.log["providers"][:2])
        assert not result.data_left_machine

    @pytest.mark.parametrize("error, category", [
        (RateLimitError("slow down"), "rate_limited"),
        (APIError("RESOURCE_EXHAUSTED: quota", 429), "rate_limited"),
        (NotFoundError("models/nope is not found"), "invalid_model"),
        (APIError("API key not valid", 400), "rejected"),
        (TimeoutError("timed out"), "timeout"),
        (ConnectionError("no route"), "connection"),
    ])
    def test_a_call_error_is_a_skip_not_a_crash(self, root, error, category):
        doc = _doc(root)
        # Transient errors are retried twice first, so the fake fails three times.
        result = _run(doc, Script(error, error, error), Script(_good(doc)),
                      config=_config(claude=True))
        entry = result.log["providers"][0]
        assert entry["status"] == "skipped" and entry["reason"].startswith(category)
        assert result.provider_id == "claude-api"

    def test_an_invalid_model_name_is_skipped_before_any_call(self, root):
        doc = _doc(root)
        config = _config()
        config.providers["gemini"].model = "not a model name!"
        result = _run(doc, Script(), config=config)
        assert "invalid model name" in result.log["providers"][0]["reason"]
        assert not result.data_left_machine

    def test_a_refusal_is_a_skip_and_no_model_answered(self, root):
        doc = _doc(root)
        script = Script(_good(doc))
        result = write_commentary(
            doc, "claude-api", _config(claude=True), _creds(),
            factories={"claude-api": claude_factory(script, stop="refusal")})
        entry = result.log["providers"][0]
        assert entry["reason"].startswith("refusal")
        assert entry["attempts"][0]["model"] is None
        assert entry["attempts"][0]["model_reported_by"] == "no model answered"
        assert result.provider_id == "deterministic"


# ---- the model that answered --------------------------------------------------------

class TestAnsweringModel:
    def test_the_log_records_the_model_each_response_names(self, root):
        doc = _doc(root)
        bad = _good(doc) + "\nRevenue was $1.11.\n"
        result = write_commentary(
            doc, "auto", _config(claude=True), _creds(), factories={
                "gemini": gemini_factory(Script(bad, bad), model_version="gemini-x-002"),
                "claude-api": claude_factory(Script(_good(doc)),
                                             answered_by="claude-sonnet-5-5")})
        gemini, claude = result.log["providers"][:2]
        assert [a["model"] for a in gemini["attempts"]] == ["gemini-x-002"] * 2
        assert all(a["model_reported_by"] == "response" for a in gemini["attempts"])
        assert claude["attempts"][0]["model"] == "claude-sonnet-5-5"
        assert gemini["model"] == "test-model-1"           # the configured name, apart

    def test_a_response_that_names_no_model_is_marked(self, root):
        doc = _doc(root)
        result = _run(doc, Script(_good(doc)))
        attempt = result.log["providers"][0]["attempts"][0]
        assert attempt["model"] == "test-model-1"
        assert attempt["model_reported_by"] == "config (the response named no model)"


# ---- secrets ----------------------------------------------------------------------

class TestSecrets:
    def test_classify_never_includes_the_key(self):
        skip = classify(APIError(f"API key {SECRET} not valid; Bearer {SECRET}", 400),
                        lambda s: redact(s, [SECRET]))
        assert SECRET not in str(skip) and SECRET not in skip.detail
        assert "[REDACTED]" in skip.detail

    def test_shapes_are_redacted_even_when_never_loaded(self):
        text = (f"key={SECRET} x-api-key: {CLAUDE_SECRET} Authorization: Bearer "
                f"abcDEF0123456789xyz refresh_token=AB12cd34EF56gh78")
        cleaned = redact(text)
        for needle in (SECRET, CLAUDE_SECRET, "abcDEF0123456789xyz", "AB12cd34EF56gh78"):
            assert needle not in cleaned

    def test_a_secret_never_reaches_logs_reports_json_or_stderr(self, root, monkeypatch,
                                                               capsys):
        doc = _doc(root)
        (root / "config" / "providers.yaml").write_text(yaml.safe_dump({
            "order": ["gemini", "claude-api", "deterministic"], "allow_cloud": True,
            "providers": {
                "gemini": {"enabled": True, "model": "test-model-1", "max_attempts": 2,
                           "api_key_env": "GEMINI_API_KEY"},
                "claude-api": {"enabled": True, "model": "test-model-2",
                               "max_attempts": 1, "api_key_env": "ANTHROPIC_API_KEY"}}}),
            encoding="utf-8")
        monkeypatch.setenv("GEMINI_API_KEY", SECRET)
        monkeypatch.setenv("ANTHROPIC_API_KEY", CLAUDE_SECRET)
        good_with_secret = _good(doc) + f"\nNote for the reviewer: {CLAUDE_SECRET}\n"
        monkeypatch.setattr(orchestrator, "CLIENT_FACTORIES", {
            "gemini": gemini_factory(Script(
                APIError(f"API key {SECRET} rejected, header x-goog-api-key: {SECRET}",
                         400))),
            "claude-api": claude_factory(Script(good_with_secret))})
        code = main(["report", "--client", "multi_co", "--period", "2025-06",
                     "--provider", "auto", "--json", "--root", str(root)])
        captured = capsys.readouterr()
        assert code == 2 or code == EXIT_OK
        out = root / "output" / "clients" / "multi_co" / "2025-06"
        texts = {"stdout": captured.out, "stderr": captured.err,
                 "report.md": (out / "report.md").read_text("utf-8"),
                 "commentary_log.json": (out / "commentary_log.json").read_text("utf-8"),
                 "metrics.json": (out / "metrics.json").read_text("utf-8")}
        for where, text in texts.items():
            assert SECRET not in text, where
            assert CLAUDE_SECRET not in text, where
        assert "[REDACTED]" in texts["commentary_log.json"]

    def test_env_file_only_listed_names_are_read(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# comment\nGEMINI_API_KEY=from-file-123\nOTHER_SECRET=nope\n"
                       "export ANTHROPIC_API_KEY=\"quoted-456\"\n", encoding="utf-8")
        store = EnvCredentialStore(["GEMINI_API_KEY", "ANTHROPIC_API_KEY"], env_file=env,
                                   environ={})
        assert store.get("GEMINI_API_KEY") == "from-file-123"
        assert store.get("ANTHROPIC_API_KEY") == "quoted-456"
        assert store.get("OTHER_SECRET") is None
        assert "OTHER_SECRET" not in (store._file() or {})
        wins = EnvCredentialStore(["GEMINI_API_KEY"], env_file=env,
                                  environ={"GEMINI_API_KEY": "from-env"})
        assert wins.get("GEMINI_API_KEY") == "from-env"
        blank = EnvCredentialStore(["GEMINI_API_KEY"], environ={"GEMINI_API_KEY": "  "})
        assert blank.get("GEMINI_API_KEY") is None


# ---- the real SDK call shapes, against fake modules ------------------------------------

class TestSdkCallShapes:
    def test_gemini(self, monkeypatch):
        seen = {}

        class Client:
            def __init__(self, api_key, http_options):
                seen.update(api_key=api_key, http_options=http_options)
                self.models = types.SimpleNamespace(generate_content=self.generate)

            def generate(self, model, contents, config):
                seen.update(model=model, contents=contents, config=config)
                return types.SimpleNamespace(
                    text="ok", candidates=[],
                    usage_metadata=types.SimpleNamespace(prompt_token_count=5,
                                                         candidates_token_count=2))
        google = types.ModuleType("google")
        genai = types.ModuleType("google.genai")
        genai.Client = Client
        google.genai = genai
        monkeypatch.setitem(sys.modules, "google", google)
        monkeypatch.setitem(sys.modules, "google.genai", genai)
        settings = ProviderSettings("gemini", True, True, "gemini-test", 60, 2, 1000,
                                    "GEMINI_API_KEY")
        done = GeminiProvider(settings, _creds()).complete("SYSTEM", "PROMPT")
        assert seen["http_options"] == {"timeout": 60000}        # milliseconds
        assert seen["model"] == "gemini-test" and seen["contents"] == "PROMPT"
        assert seen["config"]["system_instruction"] == "SYSTEM"
        assert seen["config"]["max_output_tokens"] == 1000
        assert (done.text, done.input_tokens, done.output_tokens) == ("ok", 5, 2)

    def test_claude_api(self, monkeypatch):
        seen = {}

        class Messages:
            def create(self, **kw):
                seen.update(kw)
                return types.SimpleNamespace(
                    stop_reason="end_turn",
                    content=[types.SimpleNamespace(type="text", text="ok")],
                    usage=types.SimpleNamespace(input_tokens=7, output_tokens=3))

        class Anthropic:
            def __init__(self, api_key, timeout, max_retries):
                seen.update(timeout=timeout, max_retries=max_retries)
                self.messages = Messages()
                self.beta = types.SimpleNamespace(messages=Messages())
        fake = types.ModuleType("anthropic")
        fake.Anthropic = Anthropic
        monkeypatch.setitem(sys.modules, "anthropic", fake)
        settings = ProviderSettings("claude-api", True, True, "claude-sonnet-5-5", 120, 2,
                                    16000, "ANTHROPIC_API_KEY", refusal_fallback=True)
        done = ClaudeAPIProvider(settings, _creds()).complete("SYSTEM", "PROMPT")
        assert seen["max_retries"] == 0 and seen["timeout"] == 120.0
        assert seen["model"] == "claude-sonnet-5-5" and seen["max_tokens"] == 16000
        assert seen["system"] == "SYSTEM"
        assert seen["messages"] == [{"role": "user", "content": "PROMPT"}]
        assert seen["betas"] == ["server-side-fallback-2026-07-01"]
        assert seen["fallbacks"] == "default"
        assert (done.text, done.input_tokens, done.output_tokens) == ("ok", 7, 3)

    def test_claude_api_without_the_refusal_fallback(self, monkeypatch):
        seen = {"beta": False}

        class Messages:
            def __init__(self, beta):
                self.beta = beta

            def create(self, **kw):
                seen.update(kw, beta=self.beta)
                return types.SimpleNamespace(
                    stop_reason="end_turn", model="claude-sonnet-5-5",
                    content=[types.SimpleNamespace(type="text", text="ok")],
                    usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))

        class Anthropic:
            def __init__(self, api_key, timeout, max_retries):
                self.messages = Messages(beta=False)
                self.beta = types.SimpleNamespace(messages=Messages(beta=True))
        fake = types.ModuleType("anthropic")
        fake.Anthropic = Anthropic
        monkeypatch.setitem(sys.modules, "anthropic", fake)
        settings = ProviderSettings("claude-api", True, True, "claude-sonnet-5-5", 120, 2,
                                    16000, "ANTHROPIC_API_KEY", refusal_fallback=False)
        done = ClaudeAPIProvider(settings, _creds()).complete("SYSTEM", "PROMPT")
        assert seen["beta"] is False
        assert "betas" not in seen and "fallbacks" not in seen
        assert done.model == "claude-sonnet-5-5"


# ---- existing behaviour, and the committed config ------------------------------------

class TestExistingProvidersUnchanged:
    @pytest.mark.parametrize("provider", ["deterministic", "ollama", "claude_api",
                                          "gemini-pro"])
    def test_no_log_no_footer(self, root, provider, capsys):
        main(["report", "--client", "multi_co", "--period", "2025-06",
              "--provider", provider, "--root", str(root)])
        out = root / "output" / "clients" / "multi_co" / "2025-06"
        assert not (out / "commentary_log.json").exists()
        report = (out / "report.md").read_text("utf-8")
        assert "Data left this machine" not in report
        doc = json.loads((out / "metrics.json").read_text("utf-8"))
        commentary = DeterministicProvider().generate(doc)
        from finance_engine.guard import NumberGuard
        expected = build_report(doc, commentary, "deterministic",
                                NumberGuard(doc).check(commentary).to_dict())
        assert report == expected

    def test_the_committed_config(self):
        root = Path(__file__).resolve().parents[1]
        config = ProvidersConfig.load(root / "config" / "providers.yaml")
        assert config.order == ["gemini", "claude-api", "deterministic"]
        assert config.allow_cloud is True
        assert config.providers["gemini"].enabled is True
        assert config.providers["gemini"].max_attempts == 2
        claude = config.providers["claude-api"]
        assert claude.enabled is False and claude.model == "claude-sonnet-5-5"
        assert claude.refusal_fallback is False
        assert claude.max_attempts == 2
        assert sorted(config.key_names()) == ["ANTHROPIC_API_KEY", "GEMINI_API_KEY"]
        text = (root / "config" / "providers.yaml").read_text("utf-8")
        assert "AIza" not in text and "sk-ant" not in text

    def test_a_missing_config_means_deterministic_only(self, root):
        doc = _doc(root)
        config = ProvidersConfig.load(root / "config" / "nope.yaml")
        result = write_commentary(doc, "auto", config, _creds(), factories={})
        assert result.provider_id == "deterministic"
        assert result.log["providers"][-1]["provider"] == "deterministic"

    def test_the_wording_check_is_shared(self):
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root))
        from evals import report_checks
        from finance_engine import wording
        assert report_checks.CAUSAL_WORDS is wording.CAUSAL_WORDS
        assert report_checks.units is wording.units


# ---- transient retries --------------------------------------------------------------

class TestTransientRetries:
    @staticmethod
    def _go(doc, *answers, config=None):
        waits: list[float] = []
        script = Script(*answers)
        result = _run(doc, script, Script(_good(doc)), config=config or _config(claude=True),
                      sleep=waits.append)
        return result, script, waits

    def test_a_503_is_retried_then_succeeds(self, root):
        doc = _doc(root)
        result, script, waits = self._go(doc, APIError("overloaded", 503), _good(doc))
        assert result.provider_id == "gemini" and result.attempt == 1
        assert waits == [2.0] and len(script.prompts) == 2
        attempt = result.log["providers"][0]["attempts"][0]
        assert attempt["passed"] and len(attempt["transient_retries"]) == 1
        assert attempt["transient_retries"][0]["error"] == "unavailable"
        assert attempt["transient_retries"][0]["waited_seconds"] == 2.0
        assert result.log["transient_retries"] == 1

    def test_backoff_is_two_then_five_seconds(self, root):
        doc = _doc(root)
        result, _, waits = self._go(doc, RateLimitError("slow"), TimeoutError("late"),
                                    _good(doc))
        assert waits == [2.0, 5.0] and result.provider_id == "gemini"
        errors = [r["error"] for r in
                  result.log["providers"][0]["attempts"][0]["transient_retries"]]
        assert errors == ["rate_limited", "timeout"]

    def test_still_failing_after_two_retries_skips_the_provider(self, root):
        doc = _doc(root)
        e = APIError("overloaded", 503)
        result, script, waits = self._go(doc, e, e, e)
        entry = result.log["providers"][0]
        assert entry["status"] == "skipped" and entry["reason"].startswith("unavailable")
        assert len(script.prompts) == 3 and waits == [2.0, 5.0]
        assert len(entry["attempts"]) == 1                    # one check attempt
        assert len(entry["attempts"][0]["transient_retries"]) == 2
        assert result.provider_id == "claude-api"

    @pytest.mark.parametrize("error", [APIError("bad request", 400),
                                       NotFoundError("no such model"),
                                       ConnectionError("no route")])
    def test_other_errors_are_not_retried(self, root, error):
        doc = _doc(root)
        result, script, waits = self._go(doc, error)
        assert waits == [] and len(script.prompts) == 1
        assert result.log["providers"][0]["status"] == "skipped"

    def test_retries_are_not_check_attempts(self, root):
        doc = _doc(root)
        bad = _good(doc) + "\nRevenue was $1.11.\n"
        result, script, waits = self._go(doc, APIError("overloaded", 503), bad, _good(doc))
        entry = result.log["providers"][0]
        assert result.attempt == 2 and len(entry["attempts"]) == 2
        assert len(entry["attempts"][0]["transient_retries"]) == 1
        assert entry["attempts"][1]["transient_retries"] == []
        # The retried call resent the first prompt, not a retry-with-violations prompt.
        assert script.prompts[0] == script.prompts[1] and "<draft>" in script.prompts[2]

    def test_the_committed_config(self):
        config = ProvidersConfig.load(Path(__file__).resolve().parents[1]
                                      / "config" / "providers.yaml")
        for name in ("gemini", "claude-api"):
            assert config.providers[name].transient_retries == 2
            assert config.providers[name].transient_backoff_seconds == (2.0, 5.0)


# ---- thinking -----------------------------------------------------------------------

class TestThinking:
    def _settings(self, **kw):
        return ProviderSettings("gemini", True, True, "gemini-test", 60, 2, 16384,
                                "GEMINI_API_KEY", **kw)

    @pytest.mark.parametrize("kw, expected", [
        ({"thinking_level": "low"}, {"thinking_level": "low"}),
        ({"thinking_budget": 1024}, {"thinking_budget": 1024}),
        ({"thinking_level": "low", "thinking_budget": 1024}, {"thinking_level": "low"}),
        ({}, None),
    ])
    def test_the_thinking_config_sent(self, kw, expected):
        seen: list[dict] = []
        provider = GeminiProvider(self._settings(**kw), _creds(),
                                  gemini_factory(Script("ok"), seen=seen))
        provider.complete("SYSTEM", "PROMPT")
        assert seen[0].get("thinking_config") == expected
        assert seen[0]["max_output_tokens"] == 16384

    def test_thinking_tokens_are_logged_apart(self, root):
        doc = _doc(root)
        result = write_commentary(doc, "gemini", _config(), _creds(), factories={
            "gemini": gemini_factory(Script(_good(doc)), thoughts=1500)})
        attempt = result.log["providers"][0]["attempts"][0]
        assert attempt["thinking_tokens"] == 1500 and attempt["output_tokens"] == 300
        assert result.log["tokens"]["thinking"] == 1500

    def test_the_committed_gemini_config(self):
        config = ProvidersConfig.load(Path(__file__).resolve().parents[1]
                                      / "config" / "providers.yaml")
        gemini = config.providers["gemini"]
        assert gemini.max_output_tokens >= 4096
        assert (gemini.thinking_level is None) != (gemini.thinking_budget is None)


# ---- the writer prompt ------------------------------------------------------------------

def test_the_writer_prompt_lists_the_banned_words_and_a_data_quality_example():
    text = orchestrator.PROMPT_PATH.read_text(encoding="utf-8")
    for word in ("`because`", "`due to`", "`caused`", "`driven by`", "`reason`"):
        assert word in text, word
    assert "\"`no_balance_sheet`: no balance sheet was found in the source.\"" in text
    from finance_engine.wording import CAUSAL_WORDS
    example = "`no_balance_sheet`: no balance sheet was found in the source."
    assert not CAUSAL_WORDS.search(example)
    assert CAUSAL_WORDS.search("`no_balance_sheet` is present because no balance sheet "
                               "was found.")


# ---- the smoke script, with fakes ----------------------------------------------------------

def _smoke_module():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts" / "provider_smoke.py"
    spec = importlib.util.spec_from_file_location("provider_smoke_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ClosingClient:
    """Like google-genai's client: closing it (here, when it is garbage collected) makes
    every later request fail, and models.list() fetches its pages lazily."""

    closed_calls = 0

    def __init__(self, api_key):
        # As in the SDK, `models` holds the inner API client, not this object, so an
        # unreferenced client can be collected while its pager is still being read.
        self._api = types.SimpleNamespace(closed=False)
        self.models = _FakeModels(self._api)

    def close(self):
        type(self).closed_calls += 1
        self._api.closed = True

    def __del__(self):
        self._api.closed = True


class _FakeModels:
    def __init__(self, api):
        self._api = api

    def list(self):
        api = self._api

        def pages():
            for name, actions in (("models/gemini-text", ["generateContent"]),
                                  ("models/gemini-embed", ["embedContent"]),
                                  ("models/gemini-text-2", ["countTokens",
                                                            "generateContent"])):
                if api.closed:
                    raise RuntimeError("Cannot send a request, as the client has been "
                                       "closed.")
                yield types.SimpleNamespace(name=name, supported_actions=actions)
        return pages()


@pytest.fixture
def fake_genai(monkeypatch):
    import importlib.machinery
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.__spec__ = importlib.machinery.ModuleSpec("google.genai", None)
    genai.Client = _ClosingClient
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    _ClosingClient.closed_calls = 0
    return genai


class TestSmokeScript:
    def test_the_fake_reproduces_the_closed_client_error(self, fake_genai):
        with pytest.raises(RuntimeError, match="client has been closed"):
            list(fake_genai.Client(api_key="k").models.list())

    def test_list_models_reads_the_list_before_closing(self, fake_genai):
        smoke = _smoke_module()
        lines: list[str] = []
        code = smoke.list_models(_config(), _creds(), out=lines.append)
        assert code == 0, lines
        names = [l.strip() for l in lines if l.startswith("  ")]
        assert names == ["gemini-text", "gemini-text-2"]       # generateContent only
        assert _ClosingClient.closed_calls == 1
        assert not any(SECRET in l for l in lines)

    def test_default_mode_makes_exactly_one_call(self, root, monkeypatch):
        smoke = _smoke_module()
        doc = _doc(root)
        bad = _good(doc) + "\nRevenue was $1.11.\n"
        script = Script(APIError("overloaded", 503), bad, _good(doc))
        monkeypatch.setattr(orchestrator, "CLIENT_FACTORIES",
                            {"gemini": gemini_factory(script)})
        lines: list[str] = []
        code = smoke.smoke("gemini", doc, _config(), _creds(), out=lines.append)
        assert len(script.prompts) == 1 and code == 2     # one call: the 503, no retry
        assert any("attempt 1: FAIL (unavailable)" in l for l in lines)

    def test_full_runs_the_real_orchestrator(self, root, monkeypatch):
        smoke = _smoke_module()
        doc = _doc(root)
        bad = _good(doc) + "\nRevenue was $1.11.\n"
        script = Script(bad, _good(doc))
        monkeypatch.setattr(orchestrator, "CLIENT_FACTORIES",
                            {"gemini": gemini_factory(script, model_version="gemini-x")})
        lines: list[str] = []
        code = smoke.smoke("gemini", doc, _config(), _creds(), full=True,
                           out=lines.append)
        assert code == 0 and len(script.prompts) == 2
        assert "<draft>" in script.prompts[1]                 # violations sent back
        text = "\n".join(lines)
        assert "attempt 1: FAIL" in text and "attempt 2: PASS" in text
        assert "'$1.11' is not a figure in the brief" in text
        assert "answered by gemini-x (response)" in text
        assert "report would use: gemini (attempt 2)" in text
        assert SECRET not in text

    def test_a_disabled_claude_api_is_not_run(self, root):
        smoke = _smoke_module()
        lines: list[str] = []
        code = smoke.smoke("claude-api", _doc(root), _config(claude=False), _creds(),
                           out=lines.append)
        assert code == 1 and "billed separately" in lines[0]

