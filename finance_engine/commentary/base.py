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

### Adding a provider later (documented, not implemented)

Phase 1 ships `deterministic.py` only. Two more are planned and both slot in here without
touching the engine:

* `ollama.py` — a local model over HTTP. Reads `OLLAMA_BASE_URL`. Needs no API key, so it
  keeps the "runs on a laptop with no paid services" property.
* `claude_api.py` — the Anthropic API. Reads `ANTHROPIC_API_KEY` from the environment via
  a `CredentialStore`, never from a config file or a literal.

Both must: accept only the metrics document, return text, and pass their own output
through `guard.check_commentary` before returning it. A provider that cannot reach its
backend must raise `ProviderUnavailable` so the caller can fall back to the deterministic
provider rather than emit nothing.
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

    Only the deterministic provider exists in Phase 1. Unknown names fail loudly rather
    than silently falling back, so a misconfigured environment is visible immediately.
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
