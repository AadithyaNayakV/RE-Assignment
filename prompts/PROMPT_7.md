# PHASE 5C: COMMENTARY VIA GEMINI / CLAUDE API (NO CLAUDE CODE NEEDED), AND A READ-ONLY QUICKBOOKS SOURCE

Read CLAUDE.md, docs/quickbooks_setup.md and docs/architecture.md first. Phases 5A and 5B must already be committed. Do Part 1 completely, with the full suite green, before starting Part 2. Run the full tests after each task. If a test that passed before now fails, fix or revert that task. Record the baseline first (expect 847 passed, 3 skipped) and report the new total at the end.

## Hard rules
- Do NOT commit or push. Do NOT run `claude -p` skill evals or anything paid.
- Never print, log, or put into any exception, report or JSON file an API key, token, client secret, refresh token or authorization header. Never open or echo .env files; take variable NAMES from .env.example only.
- No real network calls in tests: use fakes and recorded fixtures. Real calls only in smoke scripts that I run by hand.
- Existing behaviour must not change: the current `--provider` values, month/year/all outputs and paths. New values are additive. If any existing golden output changes, STOP and tell me which one.
- The ABC workbook must stay byte-identical (check against scripts/reference_hashes.json).
- Python is the only source of numbers. New report text must pass the causal-wording check.
- The Claude API costs money and is billed separately from my Claude Pro plan: it must be OFF by default and run only when I switch it on.
- Do not describe anything as existing unless it is built and tested.

# PART 1: COMMENTARY PROVIDERS

## Task 1: provider interface and configuration
Find how `--provider` and the deterministic provider are implemented. Add provider values `auto`, `gemini` and `claude-api` (keep every existing value as it is). Do not build an Ollama or other local provider.
Create config/providers.yaml (committed, no secrets):
- `order: [gemini, claude-api, deterministic]`
- `allow_cloud: true`
- gemini: `enabled: true`, model name, timeout, `max_attempts: 2`, `max_output_tokens`
- claude-api: `enabled: false`, model `claude-sonnet-5-5`, timeout, `max_attempts: 2`, `max_output_tokens`
- the NAMES of the environment variables for keys (GEMINI_API_KEY, ANTHROPIC_API_KEY).
Read keys from the environment or a git-ignored .env, using a small loader that only reads the listed names. Make `google-genai` and `anthropic` optional packages, imported lazily and listed in requirements-optional.txt. A missing package, missing key, disabled provider, invalid model name, exhausted quota or rejected request means "skip this provider" with a log line (never including the key), never a crash.
Gemini model IDs change over time: do not guess. Put a clearly marked default in the config and have the smoke script (Task 4) list the models available to my key so I can set a valid one.

## Task 2: writer prompt
Create one writer prompt file (for example finance_engine/commentary/writer_prompt.md) distilled from the writing rules and report template in .claude/skills/monthly-financial-analysis (rules and format only, not the command steps). The provider writes only the commentary sections (summary, what moved, questions for the reviewer); the engine embeds them in its Python-built report exactly as the deterministic provider does, so tables and status stay Python-made. The prompt must say: copy numbers exactly as in the brief with the house-style "level"/"change" labels; no causes outside the reviewer questions; the banned causal words; name every failed reconciliation period; state data-quality problems first. The model receives only the compact `brief` output (month, year or all), never the full metrics.json.

## Task 3: validate, retry, fall back
For each enabled provider in order: generate, then validate in process with the same guard check-commentary uses (numbers and direction words), the causal-wording check, and "every failed reconciliation period is named". On failure send the SAME provider the exact violation messages (not extra data) and retry up to max_attempts. Then go to the next provider. The last step is always the deterministic provider, so a valid report is always produced. Treat timeouts, connection errors, rate limits and unavailable providers as "skip", never as a crash. Share the causal-phrase detection between evals and the engine without circular imports; if that is not possible without a risky refactor, tell me and duplicate it with a test that keeps both lists identical.
Write `commentary_log.json` next to the report: providers tried, attempts, pass/fail with violation messages, the provider used, token counts per call (counts only), and `data_left_machine: true/false`. Add one line to the report footer naming the provider and attempt, and whether data left the machine. `--json` output includes the same summary.

## Task 4: tests and smoke script
Fake providers in tests: passes first time; fails the guard then passes on retry (and the retry prompt contains the violations); fails twice then the next provider is used; all providers fail then the deterministic report is produced; a disabled, unkeyed or missing-package provider is skipped; allow_cloud false skips cloud providers; a rate-limit or invalid-model error is skipped; a secret placed in an environment variable never appears in logs, reports, JSON files or exceptions; works for month, year and all scopes.
Add scripts/provider_smoke.py (run by hand): `--provider gemini|claude-api --client X --period Y`, plus `--list-models` for Gemini. It makes one real call and prints only pass/fail, the violations and token counts. Document in the README how to get a Gemini key from Google AI Studio, how to set the key in .env, and, separately, that the Claude API needs its own key and prepaid credits in the Anthropic Console, billed apart from Claude Pro. Say plainly that a model that keeps failing the guard falls back to the deterministic report, which is the safe behaviour.

# PART 2: READ-ONLY QUICKBOOKS SOURCE (SANDBOX ONLY)

## Task 5: design and real response
Design: a `pull-quickbooks` command saves the raw Profit and Loss JSON (monthly columns) as a snapshot in data/clients/<id>/source/, and a QuickBooks DataSource reads that snapshot into the same normalized model the Excel source produces. The engine therefore always analyses a local file (reproducible, hashable with SHA-256, selectable with --file); only the pull step touches the network. Add `--source quickbooks --start YYYY-MM --end YYYY-MM` on report as a convenience that pulls and then analyses.
Check whether a DataSource interface already exists; if not, introduce it without changing any output. Read docs/quickbooks_setup.md and .env.example for the variable names. If credentials are missing, STOP after listing exactly which variables I must set. Do not invent a response shape from memory.
With credentials, pull one real response from the sandbox and keep a scrubbed copy (no realm ID, tokens or headers) as tests/fixtures/qb_pl_monthly.json. Build the parser from that real response.

## Task 6: client, auth and safety
- Add config/clients/qb_sandbox.yaml (source type quickbooks, section mapping, opex categories, thresholds, reconciliation checks against Intuit's own totals).
- The reader uses GET requests only. The single exception is the OAuth token refresh POST, which is whitelisted. The base URL host must be the sandbox host; refuse anything else with a clear message (production is out of scope).
- The reader keeps its own token store in a git-ignored file (add it to .gitignore) and saves a rotated refresh token there. Do not touch or reuse the MCP server's token store. Document why (rotation can invalidate a shared token).
- Decide and document how Other Income and Other Expense are treated (not folded into revenue or operating expenses; reported net income is Intuit's figure) and say so in the report's classification note.
- Tests: no write-capable function exists in the module; an AST/grep test that only the token refresh uses a non-GET call; a non-sandbox host is refused; fake tokens never appear in exceptions, logs, JSON or reports even when the fake server returns an error that echoes them; no data in range gives a clear message.

## Task 7: golden case and smoke script
Add a golden case from the recorded fixture, with expected values built independently from the raw JSON using plain json (no finance_engine import) in evals/tools/build_expected.py. Register it and update the case count. Add scripts/qb_smoke.py (run by hand, read-only, no secrets in output) that pulls the sandbox P&L and runs the engine on it.

## Task 8: docs
Update docs/quickbooks_setup.md with the two paths (the MCP server inside Claude Code, and this direct read-only reader for the CLI and later the UI), why both exist, how each is kept read-only, token handling, and how to re-authorise. Update README, docs/architecture.md and docs/requirements_checklist.md. State honestly: sandbox data can be pulled and analysed; loading the ABC workbook into the sandbox was not done. Add a clearly marked [PLANNED] note for AWS hosting: keys in a secrets manager instead of .env, and Claude via Amazon Bedrock as a possible provider. Mark it as not built and not verified.

## Task 9: final verification
Full tests, engine evals, the free deterministic Level 2 comparison, a byte-for-byte month comparison against the last commit, the ABC hash, `git status --short` (no secret file untracked and unignored). Run the real smoke tests only if credentials exist; otherwise report them as not run.

## Final report (short)
A table: task, status, files changed, tests added, decisions needed from me. Then test total, golden case count, hash result, what changed versus the baselines, and what I must do by hand (keys, credentials).