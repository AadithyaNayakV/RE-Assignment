"""One real call to a cloud commentary provider. Run by hand; never run by the tests.

    python scripts/provider_smoke.py --list-models                      # Gemini models
    python scripts/provider_smoke.py --provider gemini --client test_clean --period 2026-01
    python scripts/provider_smoke.py --provider claude-api --client test_clean --period 2026-01
    python scripts/provider_smoke.py --provider gemini --client test_clean --scope year --year 2025
    python scripts/provider_smoke.py --provider gemini --client test_clean --period 2026-01 --full

By default it makes exactly one call (max_attempts 1, no transient retry). With --full it
runs the real orchestrator for that one provider: every check attempt (the violations sent
back) and transient retry from config/providers.yaml. Either way it prints only: pass or
fail, the check violations, the model that answered, and the token counts. It prints no
key, no prompt, no brief and no commentary text. The Claude API is billed separately from a Claude Pro plan, so it runs
only when `claude-api.enabled` is true in config/providers.yaml.

Needs `pip install -r requirements-optional.txt` and the key in the environment or .env.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finance_engine.commentary.orchestrator import write_commentary  # noqa: E402
from finance_engine.commentary.providers import (ProvidersConfig,  # noqa: E402
                                                 _importable)
from finance_engine.credentials import EnvCredentialStore  # noqa: E402


def _doc(args):
    from finance_engine.config import ClientConfig, Settings
    from finance_engine.metrics import build_metrics
    from finance_engine.sources.excel import ExcelSource
    from finance_engine.sources.selection import Want, choose
    from finance_engine.spans import build_all_metrics, build_year_metrics

    settings = Settings.from_env(ROOT)
    config = ClientConfig.load(args.client, settings)
    want = (Want("year", args.year) if args.scope == "year" else
            Want("all") if args.scope == "all" else Want("period", args.period))
    choice = choose(config, settings, ROOT, args.file, want)
    dataset = ExcelSource(choice.path).load(config)
    if args.scope == "year":
        return build_year_metrics(dataset, config, args.year, ROOT)
    if args.scope == "all":
        return build_all_metrics(dataset, config, ROOT)
    return build_metrics(dataset, config, args.period, project_root=ROOT)


def _generates_text(model) -> bool:
    """Only models that list generateContent among their supported actions."""
    actions = getattr(model, "supported_actions", None) or []
    return "generateContent" in actions


def list_models(config: ProvidersConfig, credentials: EnvCredentialStore, out=print) -> int:
    settings = config.providers.get("gemini")
    key = credentials.get(settings.api_key_env) if settings else None
    if not key:
        out(f"not run: {settings.api_key_env if settings else 'GEMINI_API_KEY'} is not set")
        return 1
    if not _importable("google.genai"):
        out("not run: google-genai is not installed (pip install -r requirements-optional.txt)")
        return 1
    from google import genai
    client = None
    try:
        # Keep a reference to the client while the pager is read. A pager outliving
        # an unreferenced client fails with "Cannot send a request, as the client has
        # been closed" when it fetches the next page.
        client = genai.Client(api_key=key)
        models = list(client.models.list())
    except Exception as exc:                         # print the kind, never the key
        out(f"failed: {type(exc).__name__}: {credentials.redact(exc)[:200]}")
        return 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:                        # closing is best effort
                pass
    names = [str(getattr(m, "name", m)).removeprefix("models/") for m in models
             if _generates_text(m)]
    out(f"configured model: {settings.model}")
    if not names:
        out("  (no model in the list reports generateContent)")
    for name in names:
        out(f"  {name}")
    out("Set providers.gemini.model in config/providers.yaml to one of these.")
    return 0


def smoke(provider: str, doc: dict, config: ProvidersConfig,
          credentials: EnvCredentialStore, full: bool = False, out=print) -> int:
    """One real call, or with `full` the real orchestrator for this one provider."""
    settings = config.providers.get(provider)
    if settings is None:
        out(f"not run: {provider} is not in config/providers.yaml")
        return 1
    if not settings.enabled:
        out(f"not run: {provider} is disabled in config/providers.yaml"
            + (" (the Claude API is billed separately; set enabled: true on purpose)"
               if provider == "claude-api" else ""))
        return 1
    if not full:
        settings.max_attempts = 1                  # exactly one real call
        settings.transient_retries = 0

    result = write_commentary(doc, provider, config, credentials,
                              notify=out if full else None)
    entry = result.log["providers"][0]
    attempts = entry.get("attempts") or []
    if entry["status"] == "skipped" and not attempts:
        out(f"not run: {entry.get('reason')}")
        return 1
    out(f"provider: {provider} (configured model {entry.get('model')})")
    for a in attempts:
        retries = a.get("transient_retries") or []
        out(f"attempt {a['attempt']}: {'PASS' if a.get('passed') else 'FAIL'}"
            + (f" ({a['error']})" if a.get("error") else "")
            + f"; answered by {a.get('model')} ({a.get('model_reported_by')})"
            + (f"; {len(retries)} transient retr{'y' if len(retries) == 1 else 'ies'}"
               if retries else ""))
        for violation in a.get("violations") or []:
            out(f"  - {violation}")
        out(f"  tokens: input {a.get('input_tokens')}, output {a.get('output_tokens')}, "
            f"thinking {a.get('thinking_tokens')}")
    out(f"result:   {'PASS' if entry['status'] == 'used' else 'FAIL'} "
        f"({entry.get('reason')})")
    if full:
        out(f"report would use: {result.provider_id} (attempt {result.attempt}); "
            f"data left this machine: {'yes' if result.data_left_machine else 'no'}")
    return 0 if entry["status"] == "used" else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--provider", choices=["gemini", "claude-api"], default="gemini")
    ap.add_argument("--list-models", action="store_true", help="Gemini models for your key")
    ap.add_argument("--full", action="store_true",
                    help="run the real orchestrator for this provider: every check "
                         "attempt and transient retry from config/providers.yaml")
    ap.add_argument("--client")
    ap.add_argument("--period")
    ap.add_argument("--scope", choices=["month", "year", "all"], default="month")
    ap.add_argument("--year", type=int)
    ap.add_argument("--file", default=None, help="workbook name, as for the CLI")
    args = ap.parse_args(argv)

    config = ProvidersConfig.load(ROOT / "config" / "providers.yaml")
    credentials = EnvCredentialStore(config.key_names(), env_file=ROOT / ".env")
    if args.list_models:
        return list_models(config, credentials)
    if not args.client or (args.scope == "month" and not args.period) \
            or (args.scope == "year" and not args.year):
        ap.error("--client and --period (or --scope year --year Y, or --scope all) are needed")
    return smoke(args.provider, _doc(args), config, credentials, full=args.full)


if __name__ == "__main__":
    raise SystemExit(main())
