# Decisions

Each key choice: what I chose, what I rejected and why, and where to see it. Written so I
can explain every one as my own.

## 1. Connector: Intuit's official MCP server, local over stdio
- **Chose:** `intuit/quickbooks-online-mcp-server`, run locally by Claude Code, OAuth 2.0
  against the sandbox.
- **Why:** it is Intuit's own repo, already handles the OAuth code flow, access-token
  refresh and refresh-token rotation, and ships its own tests (639 passed when I ran them).
  `npm run auth` writes tokens into its own git-ignored `.env`, so I never handle one.
- **Rejected:** a native Claude connector (I found none, and say only that I found none);
  community MCP servers (no Intuit backing, unreviewed, I did not test them); a custom
  server on the Accounting API (I would own OAuth, rotation and 29 entity wrappers for
  no gain).
- **Least privilege:** `.claude/settings.json` denies every `create`, `update` and `delete`
  tool, in both underscore and hyphen spellings. I found the hyphenated `create-bill` and
  similar had slipped past the first version of that rule and fixed it.
- **Local, not hosted:** sandbox OAuth accepts only `localhost` redirects. Team hosting is
  planned (`docs/future_hosting.md`), not built. See `docs/quickbooks_setup.md`.

## 2. Engine and skill are separate: Python calculates, Claude describes
- **Chose:** `finance_engine` computes every number and writes `metrics.json`. The skill
  reads a formatted brief of it and writes words. A guard rejects any figure not in the file.
- **Why:** the brief is about trust in a financial analysis. A model that does arithmetic
  can be confidently wrong in a way no one can audit. This way a wrong number cannot reach
  the report, and the engine is testable without any model.
- **Rejected:** letting Claude read the workbook and compute (unauditable, and it varies
  run to run); a skill that is only a prompt (nothing to test, nothing to reuse for the
  next client).
- **Cost:** the guard proves a number exists, not that it sits in the right sentence. I
  measured and published that limit instead of claiming safety.

## 3. No Gemini, no paid or remote model API
- **Chose:** commentary comes from Claude Code itself on a Pro plan, plus a deterministic
  template provider that needs nothing. No API key is assumed to exist anywhere.
- **Why:** the project rule, and a template fallback means the firm still gets a correct
  draft if every model is down. It is also the control for the evals.
- **Rejected:** Gemini and any metered API. Ollama and a paid Claude API stay as documented
  `CommentaryProvider` slots (`LLM_PROVIDER`), unbuilt.

## 4. `metrics.json` is the only contract
- **Chose:** one versioned JSON document (`docs/metrics_schema.md`) between every stage.
  Each metric carries its method id, source cells, warnings and an `unavailable_reason`.
- **Why:** providers, the guard and the report builder never touch the workbook, so any can
  be swapped. A blank is never zero, a percentage change from a zero or negative base is
  `n/a`, and an incomplete TTM window is blocked, never shortened.
- **Rejected:** the report builder reading the workbook directly (two sources of truth).
- **Found by the evals:** the report was computing prior-month totals itself. It now reads
  an engine metric (`<base>.prior`).

## 5. One company is one YAML file
- **Chose:** layout, section anchors, categories, thresholds and expected-negative lines in
  `config/clients/<id>.yaml`. Section membership comes from the workbook's structure, never
  from a line's name alone.
- **Why:** the brief asks to point it at another company "without a rewrite". Ten synthetic
  companies across three layouts run with no code change.
- **Cost:** each real client's layout still needs a person to write and reconcile its YAML.
  That is the first thing that breaks at ten clients.

## 6. Eval design: three levels, deterministic first
- **Chose:** Level 1 compares the engine to expected values derived **independently** (the
  generator's inputs; the workbook's own cached totals), with a builder that refuses to run
  if the engine was imported. Level 2 applies deterministic checks to the written report
  (guard, sections, hedged causes, direction words, forbidden claims). Level 3 is a 1 to 5
  rubric that is advisory and never decides pass or fail.
- **Why:** "evals that can tell better from worse" needs graders that are themselves
  trustworthy. A free deterministic control run on the template report calibrates each
  check before any Pro usage is spent. A regression demo proves a break is caught.
- **Rejected:** an LLM judge as the main signal (variable and circular); expected values
  copied from engine output (agreement would prove nothing).
- **Honest cost:** the checker had its own bugs (CHANGELOG #7 to #13). Each fix has a test
  proving the check still catches the fault, and nothing was loosened just to pass.

## 7. Local only, nothing deployed
- **Chose:** run on one laptop; document hosting, HTTPS, authentication, authorization,
  secrets and tenant isolation as plans.
- **Why:** the brief says reasoning matters more than a deployment, and the ground rules
  keep everything inside the sandbox. I do not claim production readiness or tenant
  isolation, because neither exists.
