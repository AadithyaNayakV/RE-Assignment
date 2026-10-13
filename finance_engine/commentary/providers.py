"""Cloud commentary providers (Gemini, the Claude API) and their configuration.

These are only ever used through `orchestrator.write_commentary`, for
`--provider auto | gemini | claude-api`. The existing `--provider` values keep their
existing path through `base.get_provider`, untouched.

A provider here receives the compact brief (never metrics.json) and a system prompt, and
returns text. It does no arithmetic and is trusted with nothing: the orchestrator checks
its output exactly as `check-commentary` would. Anything that stops a provider from
answering (package not installed, no key, disabled, invalid model, quota, rate limit,
timeout, connection, rejected request, refusal) raises `ProviderSkip`, which means "try
the next provider", never a crash. Keys are read by name from a `CredentialStore` and
are never put into a message.

The SDKs (`google-genai`, `anthropic`) are optional and imported only when a call is
about to be made; see requirements-optional.txt.
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from finance_engine.credentials import CredentialStore

#: --provider values handled by the orchestrator. Every other value keeps its old path.
ORCHESTRATED = ("auto", "gemini", "claude-api")
DETERMINISTIC = "deterministic"
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{1,80}")


class ProviderSkip(Exception):
    """The provider cannot answer now. `category` says why; `detail` is redacted."""

    def __init__(self, category: str, detail: str):
        super().__init__(f"{category}: {detail}")
        self.category = category
        self.detail = detail


@dataclass
class Completion:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    truncated: bool = False
    #: The model the response says answered, when it says; None when it does not.
    model: str | None = None
    #: Thinking tokens, when the API reports them apart from the visible output.
    thinking_tokens: int | None = None


@dataclass
class ProviderSettings:
    name: str
    enabled: bool = False
    cloud: bool = True
    model: str = ""
    timeout_seconds: float = 60.0
    max_attempts: int = 2
    max_output_tokens: int = 8192
    api_key_env: str = ""
    refusal_fallback: bool = False
    #: Gemini thinking control. Gemini 3 models take `thinking_level` (low | medium |
    #: high); Gemini 2.5 models take `thinking_budget` (tokens). Only one is sent, and
    #: thinking tokens count against max_output_tokens.
    thinking_level: str | None = None
    thinking_budget: int | None = None
    #: Retries of a transient failure (HTTP 5xx, 429, timeout) before the provider is
    #: skipped, with these waits. They are not check attempts.
    transient_retries: int = 2
    transient_backoff_seconds: tuple[float, ...] = (2.0, 5.0)

    @classmethod
    def from_raw(cls, name: str, raw: dict) -> "ProviderSettings":
        raw = raw or {}
        return cls(name=name, enabled=bool(raw.get("enabled", False)),
                   cloud=bool(raw.get("cloud", True)), model=str(raw.get("model") or ""),
                   timeout_seconds=float(raw.get("timeout_seconds", 60)),
                   max_attempts=max(1, int(raw.get("max_attempts", 2))),
                   max_output_tokens=int(raw.get("max_output_tokens", 8192)),
                   api_key_env=str(raw.get("api_key_env") or ""),
                   refusal_fallback=bool(raw.get("refusal_fallback", False)),
                   thinking_level=(str(raw["thinking_level"])
                                   if raw.get("thinking_level") else None),
                   thinking_budget=(int(raw["thinking_budget"])
                                    if raw.get("thinking_budget") is not None else None),
                   transient_retries=max(0, int(raw.get("transient_retries", 2))),
                   transient_backoff_seconds=tuple(
                       float(x) for x in (raw.get("transient_backoff_seconds")
                                          or (2.0, 5.0))))


@dataclass
class ProvidersConfig:
    order: list[str] = field(default_factory=lambda: [DETERMINISTIC])
    allow_cloud: bool = False
    providers: dict[str, ProviderSettings] = field(default_factory=dict)
    path: str = ""
    note: str = ""

    @classmethod
    def load(cls, path: Path) -> "ProvidersConfig":
        """Read config/providers.yaml. Missing or unreadable: no cloud provider at all."""
        path = Path(path)
        if not path.exists():
            return cls(path=str(path), note=f"{path.name} not found: only the "
                                             f"deterministic provider is available.")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            return cls(path=str(path), note=f"{path.name} is not valid YAML "
                                             f"({' '.join(str(exc).split())[:120]}): only "
                                             f"the deterministic provider is available.")
        providers = {name: ProviderSettings.from_raw(name, spec)
                     for name, spec in (raw.get("providers") or {}).items()}
        order = [str(x) for x in (raw.get("order") or [DETERMINISTIC])]
        return cls(order=order, allow_cloud=bool(raw.get("allow_cloud", False)),
                   providers=providers, path=str(path))

    def key_names(self) -> list[str]:
        """Every environment-variable NAME the providers may read. Nothing else is read."""
        return [p.api_key_env for p in self.providers.values() if p.api_key_env]


# ---- error classification -----------------------------------------------------

def classify(exc: BaseException, redact) -> ProviderSkip:
    """Map any SDK or network exception to a skip, with a redacted, bounded message."""
    name = type(exc).__name__
    # Both SDKs carry the HTTP status: anthropic as `status_code`, google-genai as `code`.
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "code", None)
    status = status if isinstance(status, int) else None
    lowered = name.lower()
    text = redact(" ".join(str(exc).split()))[:200]
    if "timeout" in lowered:
        category, what = "timeout", "the request timed out"
    elif "connection" in lowered or (isinstance(exc, OSError) and status is None):
        category, what = "connection", "could not reach the service"
    elif status == 429 or "ratelimit" in lowered or "resource_exhausted" in text.lower():
        category, what = "rate_limited", "rate limit or quota exhausted"
    elif status == 404 or "notfound" in lowered:
        category, what = "invalid_model", ("model or endpoint not found: check the model "
                                           "name in config/providers.yaml")
    elif status in (401, 403) or "authentication" in lowered or "permission" in lowered:
        category, what = "auth", "the key was rejected or lacks permission"
    elif status is not None and 400 <= status < 500:
        category, what = "rejected", "the request was rejected"
    elif status is not None and status >= 500:
        category, what = "unavailable", "the service is unavailable"
    else:
        category, what = "error", "the call failed"
    code = f" (HTTP {status})" if status else ""
    return ProviderSkip(category, f"{what}{code}; {name}: {text}" if text else
                        f"{what}{code}; {name}")


# ---- providers -------------------------------------------------------------------------

class CloudProvider:
    """A provider that sends the brief to a remote API. Subclasses make one call."""

    provider_id = "cloud"
    package = ""            # pip name, for the skip message
    import_name = ""        # module that must be importable

    def __init__(self, settings: ProviderSettings, credentials: CredentialStore,
                 client_factory=None):
        self.settings = settings
        self.credentials = credentials
        self.client_factory = client_factory

    @property
    def data_leaves_machine(self) -> bool:
        return self.settings.cloud

    def unavailable_reason(self, allow_cloud: bool) -> str | None:
        """Why this provider must be skipped before any call, or None if it can run."""
        s = self.settings
        if not s.enabled:
            return "disabled in config/providers.yaml"
        if s.cloud and not allow_cloud:
            return "allow_cloud is false in config/providers.yaml"
        if not s.model or not _MODEL_ID.fullmatch(s.model):
            return f"invalid model name {s.model!r} in config/providers.yaml"
        if not s.api_key_env:
            return "no api_key_env named in config/providers.yaml"
        if self.credentials.get(s.api_key_env) is None:
            return f"{s.api_key_env} is not set (environment or .env)"
        if self.client_factory is None and not _importable(self.import_name):
            return (f"package {self.package} is not installed "
                    f"(pip install -r requirements-optional.txt)")
        return None

    def complete(self, system: str, prompt: str) -> Completion:
        key = self.credentials.get(self.settings.api_key_env)
        try:
            client = self._client(key)
            return self._call(client, system, prompt)
        except ProviderSkip:
            raise
        except Exception as exc:                        # every SDK error becomes a skip
            raise classify(exc, self.credentials.redact) from None

    def _client(self, key: str):
        raise NotImplementedError

    def _call(self, client, system: str, prompt: str) -> Completion:
        raise NotImplementedError


class GeminiProvider(CloudProvider):
    """Google's Gemini API through `google-genai`. Keys from Google AI Studio."""

    provider_id = "gemini"
    package = "google-genai"
    import_name = "google.genai"

    def _client(self, key: str):
        if self.client_factory is not None:
            return self.client_factory(api_key=key, settings=self.settings)
        from google import genai                     # optional; imported only here
        # HttpOptions.timeout is in milliseconds.
        return genai.Client(api_key=key, http_options={
            "timeout": int(self.settings.timeout_seconds * 1000)})

    def thinking_config(self) -> dict | None:
        """`thinking_level` for Gemini 3 models, else `thinking_budget`, else none."""
        if self.settings.thinking_level:
            return {"thinking_level": self.settings.thinking_level}
        if self.settings.thinking_budget is not None:
            return {"thinking_budget": self.settings.thinking_budget}
        return None

    def _call(self, client, system: str, prompt: str) -> Completion:
        config = {"system_instruction": system,
                  "max_output_tokens": self.settings.max_output_tokens,
                  "temperature": 0.2}
        thinking = self.thinking_config()
        if thinking:
            config["thinking_config"] = thinking
        response = client.models.generate_content(
            model=self.settings.model, contents=prompt, config=config)
        text = getattr(response, "text", None) or ""
        usage = getattr(response, "usage_metadata", None)
        finish = ""
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish = str(getattr(candidates[0], "finish_reason", "") or "")
        reported = getattr(response, "model_version", None)
        thoughts = getattr(usage, "thoughts_token_count", None)
        if thoughts is None:
            thoughts = getattr(usage, "total_thought_tokens", None)
        return Completion(text=text,
                          input_tokens=getattr(usage, "prompt_token_count", None),
                          output_tokens=getattr(usage, "candidates_token_count", None),
                          truncated="MAX_TOKENS" in finish.upper(),
                          model=str(reported) if reported else None,
                          thinking_tokens=thoughts if isinstance(thoughts, int) else None)


class ClaudeAPIProvider(CloudProvider):
    """Anthropic's Messages API through `anthropic`. Billed separately from Claude Pro."""

    provider_id = "claude-api"
    package = "anthropic"
    import_name = "anthropic"

    def _client(self, key: str):
        if self.client_factory is not None:
            return self.client_factory(api_key=key, settings=self.settings)
        import anthropic                              # optional; imported only here
        # Attempts are counted by the orchestrator, so the SDK must not retry on its own.
        return anthropic.Anthropic(api_key=key, timeout=float(self.settings.timeout_seconds),
                                   max_retries=0)

    def _call(self, client, system: str, prompt: str) -> Completion:
        request = dict(model=self.settings.model,
                       max_tokens=self.settings.max_output_tokens,
                       system=system,
                       messages=[{"role": "user", "content": prompt}])
        if self.settings.refusal_fallback:
            response = client.beta.messages.create(
                **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        else:
            response = client.messages.create(**request)
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise ProviderSkip("refusal", "the model declined the request")
        text = "".join(getattr(b, "text", "") for b in (response.content or [])
                       if getattr(b, "type", "") == "text")
        usage = getattr(response, "usage", None)
        reported = getattr(response, "model", None)
        return Completion(text=text,
                          input_tokens=getattr(usage, "input_tokens", None),
                          output_tokens=getattr(usage, "output_tokens", None),
                          truncated=stop == "max_tokens",
                          model=str(reported) if reported else None)


PROVIDER_CLASSES = {"gemini": GeminiProvider, "claude-api": ClaudeAPIProvider}


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):            # a missing parent package raises
        return False
