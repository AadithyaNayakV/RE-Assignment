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

## 3. Commentary: the template always, cloud models optional (revised in Phase 5C)
- **Chose:** commentary from Claude Code itself on a Pro plan (the skill), the
  deterministic template that needs nothing, and since Phase 5C, optional cloud
  providers for the CLI: Gemini (a Google AI Studio key) and the Claude API (opt-in and
  paid, billed separately from Claude Pro, off by default). No API key is assumed to
  exist; a missing key skips the provider.
- **The rule that did not change:** no AI ever produces or changes a number. A cloud
  model receives only the compact brief and writes commentary text; the guard, the
  causal-wording check and the reconciliation-month check run on it in process, a
  failure is retried and then handed to the next provider, and the deterministic report
  is always the final fallback.
- **Why:** the user asked for commentary without Claude Code. The template fallback
  means the firm still gets a correct draft if every model is down or keeps failing the
  checks; it is also the control for the evals.
- **Before Phase 5C:** Gemini and any metered API were rejected outright, with Ollama and
  a paid Claude API kept as unbuilt `CommentaryProvider` slots. Ollama is still not
  built.

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

## 8. QuickBooks for the pipeline: a direct read-only reader, saved as snapshots (Phase 5C)
- **Chose:** a small Python reader (`sources/quickbooks_api.py`) that refreshes its own
  token and GETs one report, the monthly Profit and Loss, from the sandbox. The raw
  response is saved as a snapshot file, and the engine reads that file through the
  `DataSource` interface like a workbook.
- **Why:** the MCP server serves Claude Code; the pipeline needs Python as the only source
  of numbers and a reproducible input with a hash. A separate Intuit app and token file
  avoid two programs rotating one refresh token.
- **Other Income / Other Expenses:** kept outside revenue, COGS and operating expenses.
  Operating income is Intuit's Net Operating Income; net income as reported is Intuit's Net
  Income, which includes them. Folding them in would make operating figures disagree with
  Intuit's own report.
- **Blank cells:** read as 0, because QuickBooks leaves a cell blank for no activity and its
  own totals count it as zero. This departs from the workbook rule (blank is not zero) for
  this source only, and the client config's classification note says so.
- **Rejected:** analysing the live response directly (not reproducible); reusing the MCP
  server's token (rotation conflicts); production access (out of scope; refused in code).
- **Not done:** loading the ABC workbook into the sandbox; a secrets manager (planned).

## 9. The local UI: FastAPI, plain JS modules, vendored Chart.js (Phase 5D)
- **Chose:** a FastAPI app (`finance_engine/web/app.py`) of thin routes over a service
  layer (`finance_engine/service/`) that the new CLI commands also use; a front end of
  plain HTML, CSS and ES modules in `ui/` with no framework and no build step; Chart.js
  4.5.1 vendored in `ui/static/vendor/` with its licence and SHA-256.
- **Why FastAPI:** Python already owns every number, so the server must be Python and
  must call the same engine and service code as the terminal. FastAPI gives typed
  routes, a TestClient for testing every route in-process, and uvicorn for a local
  server, in three pinned packages (fastapi, uvicorn, python-multipart for uploads).
  Starlette's StaticFiles refuses path traversal; the app adds loopback-only, Host and
  Origin checks, a required `X-Finance-UI` header on writes, a strict CSP, and a
  redactor on every JSON body.
- **Why no framework or build step:** the UI only places strings the engine formatted;
  it needs no state library. No npm install, no bundler, nothing to audit or rebuild,
  and a reviewer can read every file. DOM nodes are built with `textContent`, never
  `innerHTML`, so workbook text cannot become markup.
- **Why vendored Chart.js:** one file, MIT, widely used, accessible enough with a
  "view as table" toggle; no CDN, so the app works offline and loads nothing from
  outside 127.0.0.1. Upgrades are a deliberate file replacement checked by a hash test.
- **The rule that did not change:** the UI never calculates. Charts plot values copied
  from `metrics.json`; tooltips and tables show the engine's own display strings. A test
  scans the JS for arithmetic (best effort, not a proof).
- **Rejected:** React or another SPA framework (a build chain for no gain); a server
  that renders HTML templates (a second place where figures could be formatted);
  Streamlit or similar (runs arbitrary Python per widget, hard to lock to loopback and
  to keep from calculating); a CDN for Chart.js (outside calls, version drift).
- **Not built:** sign-in, durable shared storage for review records, hosting. Marked
  [PLANNED] in `docs/ui.md`.
