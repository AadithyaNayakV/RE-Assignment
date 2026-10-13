"""The `CommentaryProvider` boundary.

A provider turns a metrics document into prose. It is handed `metrics.json` and nothing
else — never the workbook, never a database connection, never the raw rows beyond the
traces already inside the document. That restriction is what makes the stage safe to hand
to a language model later: the worst a provider can do is describe verified numbers badly,
not invent new ones.

The contract, in order of importance:

1. **Never calculate.** Every figure must come from the document. If a comparison is not
   in `metrics.json`, the provider says so rather than working it out.
2. **Never invent a cause.** The workbook records amounts, not reasons. A provider may
   report that a line rose; it may not say why.
3. **Separate confirmed facts from possible explanations.** Anything that is not a
   verified figure must be visibly marked as an observation or a question for the
   reviewer.
4. **Never invent a category or a line name.** Use the labels in the document.

`guard.py` enforces rule 1 mechanically, whatever the provider. Rules 2-4 are enforced by
prompt and review for a model provider, and structurally for the deterministic one.

### Cloud providers (Phase 5C)

`get_provider` below resolves the original values only (deterministic, template, none;
ollama and claude_api still fall back to the template, as before). The cloud providers,
Gemini and the Claude API, live in `providers.py` and are run by `orchestrator.py` for
`--provider auto | gemini | claude-api`. They receive the compact brief, never the
workbook, and never produce a number: the orchestrator checks their text with the guard,
the causal-wording check and the failed-reconciliation-month check, retries, falls back,
and always ends with the deterministic provider. The Claude API is opt-in and paid.
Ollama is not built.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ProviderUnavailable(Exception):
    """The provider's backend could not be reached or is not configured.

    Signals the caller to fall back to the deterministic provider.
    """


class CommentaryRejected(Exception):
    """The provider produced text containing numbers absent from metrics.json."""

    def __init__(self, message: str, guard_result=None):
        super().__init__(message)
        self.guard_result = guard_result


class CommentaryProvider(ABC):
    """Turns a metrics document into reviewer-facing prose."""

    #: Recorded in the report so a reader knows what wrote the commentary.
    provider_id: str = "unknown"

    #: True when the provider needs no network and no credentials, so it can always run.
    always_available: bool = False

    @abstractmethod
    def generate(self, metrics_doc: dict) -> str:
        """Write commentary from `metrics_doc`.

        Args:
            metrics_doc: a parsed `metrics.json`, the provider's only input.

        Returns:
            Markdown text. Every number in it must appear in `metrics_doc`.

        Raises:
            ProviderUnavailable: the backend is unreachable or unconfigured.
            CommentaryRejected: the provider's own guard check failed.
        """

    def describe(self) -> dict:
        return {"provider_id": self.provider_id,
                "always_available": self.always_available}


def get_provider(name: str) -> CommentaryProvider:
    """Resolve a provider by the `LLM_PROVIDER` value.

    The original values only; `auto`, `gemini` and `claude-api` go through
    `orchestrator.write_commentary` instead. Unknown names fail loudly rather than
    silently falling back, so a misconfigured environment is visible immediately.
    """
    from finance_engine.commentary.deterministic import DeterministicProvider

    key = (name or "deterministic").strip().lower()
    if key in ("deterministic", "template", "", "none"):
        return DeterministicProvider()
    if key in ("ollama", "claude_api", "claude", "anthropic"):
        raise ProviderUnavailable(
            f"Commentary provider {key!r} is a documented extension point but is not "
            f"implemented in Phase 1. Set LLM_PROVIDER=deterministic."
        )
    raise ProviderUnavailable(
        f"Unknown commentary provider {name!r}. Available: deterministic."
    )
