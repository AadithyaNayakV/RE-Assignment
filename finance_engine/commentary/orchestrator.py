"""Generate, validate, retry, fall back: commentary for `--provider auto|gemini|claude-api`.

For each provider in order:

1. Skip it, with a logged reason, if it cannot run (disabled, cloud not allowed, no key,
   package not installed, invalid model name).
2. Send it the writer prompt and the compact brief (never metrics.json).
3. Validate its text in process, with the checks `check-commentary` and the evals run:
   every number and direction word (the guard), causal wording outside "Questions for
   the reviewer", and every failed reconciliation month named.
4. On failure, send the SAME provider the exact violation messages and its own previous
   draft, up to `max_attempts`. A transient failure (HTTP 5xx, 429, a timeout) is first
   retried `transient_retries` times with a short backoff; those retries are not check
   attempts. Then it, or any other call failure (connection, quota, refusal, rejected
   request), skips to the next provider; it never crashes the run.

The last step is always the deterministic provider, so a valid report is always
produced. Everything that leaves this module (log, footer, notes) is redacted.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from pathlib import Path

from finance_engine.brief import build_brief
from finance_engine.commentary.deterministic import DeterministicProvider
from finance_engine.commentary.providers import (DETERMINISTIC, PROVIDER_CLASSES,
                                                 ProvidersConfig, ProviderSkip)
from finance_engine.credentials import CredentialStore, redact
from finance_engine.guard import GuardResult, NumberGuard
from finance_engine.wording import (SIGNED_AFTER_DIRECTION, causal_units, period_named,
                                    units, unstated_caveats)

PROMPT_PATH = Path(__file__).with_name("writer_prompt.md")

#: Client factories by provider name, for tests: a fake replaces the real SDK client.
CLIENT_FACTORIES: dict = {}

_SERVICE = {"gemini": "Google's Gemini API", "claude-api": "Anthropic's Claude API"}

#: Call failures worth one more try after a short wait: overload (HTTP 5xx), rate limit
#: (HTTP 429) and timeouts. Everything else skips the provider at once.
TRANSIENT = ("unavailable", "rate_limited", "timeout")
_ORDINAL = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
            "ninth", "tenth"]


@dataclass
class CommentaryResult:
    text: str
    provider_id: str
    attempt: int
    guard: GuardResult
    log: dict
    data_left_machine: bool
    notes: list[str] = field(default_factory=list)

    @property
    def footer(self) -> str:
        ordinal = (_ORDINAL[self.attempt - 1] if 1 <= self.attempt <= len(_ORDINAL)
                   else "final")
        sent = [p["provider"] for p in self.log["providers"] if p.get("data_sent")]
        if sent:
            services = " and ".join(_SERVICE.get(p, p) for p in dict.fromkeys(sent))
            left = (f"yes: the brief was sent to {services}"
                    + ("" if self.provider_id in sent else ", whose drafts were not used"))
        else:
            left = "no"
        return (f"_Commentary written by the `{self.provider_id}` provider on its "
                f"{ordinal} attempt. Data left this machine: {left}. Every attempt is "
                f"in `commentary_log.json`._")

    def summary(self) -> dict:
        """The part of the log that `--json` prints."""
        return {"provider_used": self.provider_id, "attempt": self.attempt,
                "data_left_machine": self.data_left_machine,
                "providers": [{k: p[k] for k in ("provider", "status", "reason")
                               if k in p} | {"attempts": len(p.get("attempts", []))}
                              for p in self.log["providers"]],
                "tokens": self.log["tokens"]}


# ---- validation -----------------------------------------------------------------

def validate(text: str, doc: dict, require_periods: bool = True) -> list[str]:
    """Every reason `text` may not go into the report, as messages a writer can act on."""
    problems: list[str] = []
    if not text.strip():
        return ["The commentary is empty."]
    guard = NumberGuard(doc).check(text)
    for f in guard.unsupported:
        problems.append(f"Line {f.line}: {f.text!r} is not a figure in the brief. Copy "
                        f"figures exactly as the brief prints them, or leave this out.")
    for e in guard.direction_errors:
        problems.append(f"Line {e['line']}: {e['word']!r} before {e['text']!r} contradicts "
                        f"the sign of that change ({e['problem']}). Use the direction word "
                        f"that matches the sign.")
    for unit in causal_units(text, doc):
        problems.append(f"Causal wording outside 'Questions for the reviewer': "
                        f"{' '.join(unit.split())[:160]!r}. Say where a change sits, not "
                        f"why; put any possible cause as a question in that section.")
    for unit in units(text):
        if SIGNED_AFTER_DIRECTION.search(unit):
            problems.append(f"A direction word is followed by a signed figure: "
                            f"{' '.join(unit.split())[:120]!r}. Write the direction in "
                            f"the word and the amount unsigned (\"fell 2.6 pts\", not "
                            f"\"narrowed by -2.6 pts\").")
    for item in unstated_caveats(text, doc):
        problems.append(f"State this comparison caveat in the summary, naming the measure "
                        f"and {item['against']}, with no cause: {item['text']}.")
    if require_periods:
        failed = sorted({f["period"] for f in
                         (doc.get("reconciliation") or {}).get("failures", [])
                         if f.get("period")})
        for period in failed:
            if not period_named(text, period):
                problems.append(f"A reconciliation check failed in {period}; name that "
                                f"month and the failed check.")
    return problems


# ---- prompts --------------------------------------------------------------------

def _scope_line(doc: dict) -> str:
    scope = doc.get("scope", "month")
    what = {"year": "a calendar year", "all": "the whole file"}.get(scope, "one month")
    return (f"Client {doc.get('client_id')}, {what}: {doc.get('period_label')}.")


def first_prompt(doc: dict, brief: str) -> str:
    return (f"{_scope_line(doc)} Write the three commentary sections for this brief, "
            f"following your instructions.\n\n<brief>\n{brief.rstrip()}\n</brief>\n")


def retry_prompt(doc: dict, brief: str, draft: str, problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (first_prompt(doc, brief)
            + f"\nYour previous draft was rejected by the checks. Problems:\n{listed}\n\n"
              f"Your previous draft:\n<draft>\n{draft.rstrip()}\n</draft>\n\n"
              f"Write the whole commentary again, fixing every problem listed.\n")


# ---- the run ----------------------------------------------------------------------

def provider_order(requested: str, config: ProvidersConfig) -> list[str]:
    requested = requested.strip().lower()
    order = list(config.order) if requested == "auto" else [requested]
    order = [p for p in order if p != DETERMINISTIC]
    return order + [DETERMINISTIC]


def _call_with_retries(provider, name, settings, system, prompt, sleep, note, clean):
    """One check attempt's call: (completion or None, transient retries, final skip)."""
    retries: list[dict] = []
    backoff = list(settings.transient_backoff_seconds) or [0.0]
    for n in range(settings.transient_retries + 1):
        try:
            return provider.complete(system, prompt), retries, None
        except ProviderSkip as skip:
            if skip.category not in TRANSIENT or n == settings.transient_retries:
                return None, retries, skip
            wait = backoff[min(n, len(backoff) - 1)]
            retries.append({"error": skip.category, "detail": clean(skip.detail),
                            "waited_seconds": wait})
            note(f"commentary: {name} {skip.category}; retrying in {wait:g} s "
                 f"({n + 1} of {settings.transient_retries})")
            sleep(wait)
    return None, retries, None                          # unreachable


def write_commentary(doc: dict, requested: str, config: ProvidersConfig,
                     credentials: CredentialStore, factories: dict | None = None,
                     notify=None, sleep=time.sleep) -> CommentaryResult:
    factories = CLIENT_FACTORIES if factories is None else factories
    secrets = credentials.loaded_values
    clean = lambda s: redact(s, secrets())                         # noqa: E731
    notes: list[str] = []

    def note(message: str) -> None:
        message = clean(message)
        notes.append(message)
        if notify:
            notify(message)

    system = PROMPT_PATH.read_text(encoding="utf-8")
    brief = build_brief(doc)
    order = provider_order(requested, config)
    log: dict = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                              .isoformat(),
        "requested_provider": requested,
        "order": order,
        "allow_cloud": config.allow_cloud,
        "config": Path(config.path).name if config.path else None,
        "checks": ["numbers and direction words (the guard)",
                   "causal wording outside 'Questions for the reviewer'",
                   "every failed reconciliation month named"],
        "providers": [],
    }
    if config.note:
        note(f"commentary: {config.note}")

    for name in order[:-1]:
        entry = {"provider": name, "status": "skipped", "data_sent": False, "attempts": []}
        log["providers"].append(entry)
        settings = config.providers.get(name)
        cls = PROVIDER_CLASSES.get(name)
        if settings is None or cls is None:
            entry["reason"] = f"{name!r} is not a configured provider"
            note(f"commentary: {name} skipped ({entry['reason']})")
            continue
        entry["model"] = settings.model
        provider = cls(settings, credentials, factories.get(name))
        reason = provider.unavailable_reason(config.allow_cloud)
        if reason:
            entry["reason"] = reason
            note(f"commentary: {name} skipped ({reason})")
            continue
        draft, problems = "", []
        for attempt in range(1, settings.max_attempts + 1):
            prompt = (first_prompt(doc, brief) if attempt == 1
                      else retry_prompt(doc, brief, draft, problems))
            entry["data_sent"] = entry["data_sent"] or provider.data_leaves_machine
            completion, retries, skip = _call_with_retries(
                provider, name, settings, system, prompt, sleep, note, clean)
            if skip is not None:
                entry.update(status="skipped", reason=f"{skip.category}: {skip.detail}")
                entry["attempts"].append({"attempt": attempt, "passed": False,
                                          "error": skip.category, "model": None,
                                          "model_reported_by": "no model answered",
                                          "transient_retries": retries})
                note(f"commentary: {name} skipped ({clean(skip.detail)})")
                break
            draft = clean(completion.text)
            problems = validate(draft, doc)
            if completion.truncated:
                problems.append("The output was cut off at the token limit; write a "
                                "shorter commentary, and think briefly.")
            entry["attempts"].append({
                "attempt": attempt, "passed": not problems, "violations": problems,
                # The model that actually answered, as the response names it; the
                # configured name only when the response names none, and then marked.
                "model": completion.model or settings.model,
                "model_reported_by": "response" if completion.model else
                                     "config (the response named no model)",
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                # Reported apart from the visible output when the API gives it; Gemini
                # counts thinking against max_output_tokens.
                "thinking_tokens": completion.thinking_tokens,
                "transient_retries": retries})
            if not problems:
                entry.update(status="used", reason="passed every check")
                note(f"commentary: {name} passed the checks on attempt {attempt}")
                return _finish(doc, draft, name, attempt, log, notes, clean)
            note(f"commentary: {name} attempt {attempt} failed the checks "
                 f"({len(problems)} problem(s))")
        else:
            entry.update(status="failed",
                         reason=f"failed the checks on all {settings.max_attempts} "
                                f"attempt(s)")

    text = DeterministicProvider().generate(doc)
    problems = validate(text, doc, require_periods=False)
    log["providers"].append({
        "provider": DETERMINISTIC, "status": "used", "data_sent": False,
        "reason": "the last step: the template, which never leaves this machine",
        "attempts": [{"attempt": 1, "passed": not problems, "violations": problems}]})
    note("commentary: used the deterministic provider")
    return _finish(doc, text, DETERMINISTIC, 1, log, notes, clean)


def _finish(doc, text, provider_id, attempt, log, notes, clean) -> CommentaryResult:
    calls = [a for p in log["providers"] for a in p.get("attempts", [])
             if "input_tokens" in a]
    log["used"] = {"provider": provider_id, "attempt": attempt}
    log["data_left_machine"] = any(p.get("data_sent") for p in log["providers"])
    log["tokens"] = {
        "calls": len(calls),
        "input": sum(a.get("input_tokens") or 0 for a in calls),
        "output": sum(a.get("output_tokens") or 0 for a in calls),
        "thinking": sum(a.get("thinking_tokens") or 0 for a in calls)}
    log["transient_retries"] = sum(len(a.get("transient_retries") or [])
                                   for p in log["providers"]
                                   for a in p.get("attempts", []))
    log = _redact_tree(log, clean)
    guard = NumberGuard(doc).check(text)
    return CommentaryResult(text=clean(text), provider_id=provider_id, attempt=attempt,
                            guard=guard, log=log,
                            data_left_machine=log["data_left_machine"], notes=notes)


def _redact_tree(value, clean):
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        return [_redact_tree(v, clean) for v in value]
    if isinstance(value, dict):
        return {k: _redact_tree(v, clean) for k, v in value.items()}
    return value
