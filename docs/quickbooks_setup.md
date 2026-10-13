# QuickBooks Online sandbox: two read-only paths

This project reads QuickBooks Online in two separate ways. Both are **sandbox only** and
**read-only**, and each uses its own Intuit app and its own refresh token.

| | Path 1: MCP server (Phase 2) | Path 2: direct reader (Phase 5C) |
|---|---|---|
| Used by | Claude Code, interactively (`mcp__quickbooks__*` tools) | The CLI (`pull-quickbooks`, `report --source quickbooks`) and any UI built on it |
| Code | Intuit's `quickbooks-online-mcp-server/` (Node) | `finance_engine/sources/quickbooks_api.py` (fetch) and `sources/quickbooks.py` (snapshot reader), Python standard library only |
| What it reads | Any read tool (company info, reports, entities) | One report: Profit and Loss, one column per month |
| How it is read-only | `.claude/settings.json` denies every create/update/delete tool (the server itself registers them) | The code has no write function: every API request is a GET; the only POST is the OAuth token refresh to Intuit's token endpoint. Both are checked by tests |
| Sandbox only | The server's `.env` says `QUICKBOOKS_ENVIRONMENT=sandbox` | The host must be `sandbox-quickbooks.api.intuit.com` and `QBO_ENVIRONMENT` must be `sandbox`; anything else is refused before a request is made |
| Credentials | `quickbooks-online-mcp-server/.env` (`QUICKBOOKS_*`, written by `npm run auth`) | Root `.env`: `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_REFRESH_TOKEN`, `QBO_REALM_ID`, `QBO_ENVIRONMENT` |
| Rotated refresh token saved to | `quickbooks-online-mcp-server/.env` | `.secrets/quickbooks_reader_token.json` (git-ignored) |

**Why both exist.** The MCP server lets Claude explore a company during a conversation.
The report pipeline needs something else: Python must be the only source of numbers, and
a run must be reproducible. So the direct reader saves the raw response as a snapshot file
with a SHA-256, and the engine analyses that file exactly as it analyses a workbook. It
never analyses a live response. The pipeline also works without Claude Code or Node.

**Why the tokens are not shared.** Intuit rotates the refresh token when it is used. If
two programs used one token, each refresh would replace the token the other one holds, and
one of them would eventually fail with `invalid_grant`. The reader therefore has its own
Intuit development app, its own refresh token and its own token file. It never reads or
writes `quickbooks-online-mcp-server/.env`.

**Not done.** Loading the ABC workbook into the sandbox (the brief's stretch goal) was not
attempted. The sandbox holds Intuit's sample company, so everything read from it is test
data, not evidence about a client.

## Path 2: the direct reader

### Use

```bash
python -m finance_engine pull-quickbooks --client qb_sandbox --start 2025-10 --end 2026-10
python -m finance_engine report --client qb_sandbox --period 2026-09 --file <snapshot>.json
python -m finance_engine report --client qb_sandbox --period 2026-09 \
    --source quickbooks --start 2025-10 --end 2026-10      # pull, then analyse that pull
python scripts/qb_smoke.py      # by hand: one refresh, one GET, prints no secret, saves nothing
```

A pull makes one token refresh and one GET of the Profit and Loss report for whole
calendar months `--start`..`--end`. The response is checked with the snapshot reader
**before** anything is written, so a range with no data, a non-monthly report or a wrong
currency gives a clear message and leaves no file. The response is then saved byte for
byte as `data/clients/qb_sandbox/source/qb_pl_<start>_<end>_<UTC time>.json`. A snapshot is
never overwritten. When the folder holds several snapshots, the usual file rules apply
(`--file`, or the one snapshot that contains the period); snapshots are never merged.

### Credentials and token handling

- The five variables above go in the root `.env` (git-ignored). `.env.example` lists the
  names only.
- `QBO_REFRESH_TOKEN` is the **seed**. On every pull the reader tries the token saved in
  `.secrets/quickbooks_reader_token.json` first, then the seed. Intuit's rotated token is
  saved back to that file atomically (temp file, then replace).
- The client secret, both tokens, the realm ID and the authorization header are redacted
  from every error message, including an error body from Intuit that echoes them. The realm
  ID is replaced with `<realm>` in any URL that is shown. Tests feed fake secrets through a
  fake server that echoes them, and check that none appears in an exception, stdout,
  stderr, the snapshot or any output file.

### Re-authorising

When a pull says "The token refresh was refused", the stored token and the seed have
both expired or been revoked (refresh tokens last about 100 days). Then:

1. Authorise the reader's **own** Intuit development app against the sandbox company again.
   For example, use Intuit's OAuth 2.0 Playground: choose that app and the
   `com.intuit.quickbooks.accounting` scope. Do not use the MCP server's app or its token.
2. Put the new refresh token in `QBO_REFRESH_TOKEN`, and the company's realm ID in
   `QBO_REALM_ID`, in the root `.env`.
3. Run `python scripts/qb_smoke.py`. The stale stored token is refused, and the reader
   falls back to the new seed and saves the token Intuit rotates. You may also delete
   `.secrets/quickbooks_reader_token.json` first.

Intuit offers no read-only scope for the Accounting API. The reader is read-only because
of its code (GET only, tested), not because of the scope.

### How the report is mapped (`config/clients/qb_sandbox.yaml`)

- Sections come from Intuit's own groups: Income, Cost of Goods Sold and Expenses. Rows
  between a section's header and its total belong to it. The label never decides.
- **Other Income and Other Expenses stay outside all three sections.** Operating income is
  Intuit's Net Operating Income. Net income as reported is Intuit's Net Income, which
  includes them. A classification note in `metrics.json` says so.
- Nested accounts are labelled with their path, for example
  `Job Expenses: Job Materials: Plants and Soil`. A parent account's own postings (shown in
  its section header row) become a line, `<account> (own amount)`. An account named like
  its section becomes `<name> (account)`. Nested subtotals are kept but never summed.
- **A blank cell is read as 0.** QuickBooks leaves a cell blank when an account had no
  activity, and its own totals count that blank as zero. This is the one place where the
  engine's "blank is not zero" rule does not apply, and the config's classification note
  says so.
- Every section sum is reconciled against Intuit's own total, for every month.

### Verified

- One real pull from the sandbox (2026-10-12): Oct 2025 to Oct 2026, 13 monthly columns.
  For 2026-09, 65 of 65 reconciliation checks passed, and the deterministic report's number
  check passed (38 of 38 numbers traced).
- That response, scrubbed of every identifier, is `tests/fixtures/qb_pl_monthly.json`. It
  drives `tests/test_quickbooks.py` (fake Intuit server, no network) and golden case
  `19_quickbooks_sandbox`. That case's expected values are summed from the JSON with plain
  `json` in `evals/tools/build_expected.py` and checked there against Intuit's own totals.
- `scripts/qb_smoke.py` is for a person to run by hand. It is not run by the tests or the
  evals.

## Path 1: the MCP server (Phase 2)

The rest of this document is the Phase 2 record of the MCP connection. Statements such as
"`finance_engine/` was not modified" describe that phase. Path 2 above was added in
Phase 5C.

Status: **Connected and verified** (sandbox, local only). Registration with this Claude
Code session is pending a one-time interactive trust approval — see "Open issues" below.

### What I chose and why

**The cloned server** at `quickbooks-online-mcp-server/` (package `@qboapi/qbo-mcp-server`).
Confirmed via `git remote -v` inside that directory:
```
origin  https://github.com/intuit/quickbooks-online-mcp-server.git (fetch)
origin  https://github.com/intuit/quickbooks-online-mcp-server.git (push)
```
This **is** the official `intuit/` repository (not a community fork) — the npm package name
(`@qboapi/qbo-mcp-server`) differs from the GitHub org, but the git origin confirms it's cloned
directly from Intuit's own repo. It already existed in the repo per the Phase 1 brief, so this
phase connects it rather than building a new one.

Reasons to use it over the alternatives below:
- Full CRUD coverage (README says 145 tools; the built server registers 142, see
  "Least privilege" below; 29 entities, 11 reports) with a documented Jest suite
  (396 tests, claimed 100% coverage — I did not re-run their suite, see "Open issues").
- Implements the OAuth 2.0 authorization-code flow, access-token refresh, and refresh-token
  rotation itself (`src/clients/quickbooks-client.ts`), rather than us having to write that
  against Intuit's raw Accounting API.
- Ships an `npm run auth` helper that runs the browser handshake and writes tokens straight
  into its own `.env` — no manual token copy-paste.

### What I rejected and why

- **A native Claude connector for QuickBooks.** I looked and did not find one in Claude's
  current first-party connector list. I did not exhaustively test this claim against a live
  connector directory beyond what's visible in this environment — if one exists, I didn't
  find it, so I'm not asserting its absence with certainty, only that it wasn't a usable
  option I could find.
- **Community open-source QuickBooks MCP servers (other than this one).** Several exist on
  GitHub/npm. I considered but did not install or test any of them — no Intuit backing,
  unclear maintenance/security review, and we already had a well-tested one cloned. Not
  worth the extra review burden for this phase.
- **A custom MCP server built directly on the QuickBooks Accounting API.** Most control, but
  means writing and maintaining OAuth handling, token rotation, and CRUD wrappers for 29
  entity types ourselves. Rejected for effort/maintenance reasons given a maintained
  alternative was already available.

### Local vs. team hosting

**Why local is right for this prototype:** sandbox OAuth only accepts `http://localhost`
redirect URIs — Intuit rejects localhost for production apps — so a single developer running
the handshake on their own machine is the simplest path, and it matches the brief ("Run
locally only in this phase. Do not deploy.").

**If a whole team (not just my laptop) needs this — planned, NOT implemented:**
- Run the server as a hosted MCP endpoint over HTTPS, behind the company's own login/SSO,
  rather than as a per-developer stdio subprocess.
- One QuickBooks OAuth connection (one refresh token) **per client**, not shared — matches
  this project's existing `client_id`-keyed design in `finance_engine`.
- Store each client's refresh token in a secrets manager (e.g. AWS Secrets Manager / Vault),
  not a flat `.env` file. The server already supports pointing at an arbitrary token file via
  `QUICKBOOKS_TOKEN_STORE_PATH` (must be set in the host process env, not inside `.env` —
  it's resolved before `.env` loads); a hosted version would set that per tenant.
- Audit log every tool call: who, which client, which entity, read vs. write.
- Production's OAuth handshake needs a public HTTPS callback — the README documents an
  ngrok tunnel for one-off re-auth, or a small deployed callback handler; after that initial
  handshake, only the refresh token matters for day-to-day use.

### What changes for remote access / ten clients

- Ten clients means ten independent OAuth connections (ten refresh tokens), not ten copies of
  the server — same server, keyed token storage.
- `QUICKBOOKS_TOKEN_STORE_PATH` per tenant is the interim mechanism already built into this
  server; a secrets-manager-backed store would be the longer-term replacement.
- A future hosted app would read QuickBooks through `finance_engine/sources/quickbooks.py`
  implementing the existing `DataSource` interface — not built in Phase 2. **Since Phase
  5C** that reader exists for the sandbox (Path 2 above); per-client token storage in a
  secrets manager is still planned.

### Setup actually performed

1. **Install + build** (already `npm install`ed from Phase 1 setup; rebuilt to confirm):
   ```
   cd quickbooks-online-mcp-server
   npm run build
   ```
   Result: `tsc && shx chmod +x dist/*.js` — succeeded, no errors.
   (Note: `node`/`npm` were not on PATH in the Git Bash shell in this environment — PowerShell
   was used for every Node/npm command.)

2. **Credentials.** `quickbooks-online-mcp-server/.env` was created (gitignored, confirmed via
   `git check-ignore -v`) with:
   ```
   QUICKBOOKS_CLIENT_ID=...
   QUICKBOOKS_CLIENT_SECRET=...
   QUICKBOOKS_REFRESH_TOKEN=        (blank — filled by npm run auth)
   QUICKBOOKS_REALM_ID=             (blank — filled by npm run auth)
   QUICKBOOKS_REDIRECT_URI=http://localhost:8000/callback
   QUICKBOOKS_ENVIRONMENT=sandbox
   ```
   The client ID/secret came from the project's root `.env`, which was itself cleaned up in
   this phase — see "Security notes" below.

3. **Redirect URI.** User added `http://localhost:8000/callback` under the Intuit app's
   Settings → Redirect URIs (confirmed by user before proceeding).

4. **OAuth handshake:**
   ```
   npm run auth
   ```
   Output (real, this run):
   ```
   [auth-server] Listening on :::8000 (family: IPv6)
   === QuickBooks Authorization ===
   Open this URL in a browser to authorize: https://appcenter.intuit.com/connect/oauth2?...
   Waiting for callback...
   [auth-server] GET /callback?code=...&state=...&realmId=<redacted>
   ✓ Successfully authenticated with QuickBooks!
   Tokens have been saved to your .env file.
   ```
   `QUICKBOOKS_REFRESH_TOKEN` and `QUICKBOOKS_REALM_ID` were written to
   `quickbooks-online-mcp-server/.env` by the server itself. No token value is reproduced here.

5. **Register with Claude Code:**
   ```
   claude mcp add quickbooks --scope project -- node "<repo>/quickbooks-online-mcp-server/dist/index.js"
   ```
   This wrote the project's `.mcp.json`:
   ```json
   {
     "mcpServers": {
       "quickbooks": {
         "type": "stdio",
         "command": "node",
         "args": ["<repo>/quickbooks-online-mcp-server/dist/index.js"],
         "env": {}
       }
     }
   }
   ```
   `env` is deliberately empty — **no secret is in `.mcp.json`**. The server loads its own
   `quickbooks-online-mcp-server/.env` via `dotenv` at startup (path resolved relative to the
   compiled module, not the caller's cwd), so `.mcp.json` only needs to know where `dist/index.js`
   lives.

### Proof it works (real output, not assumptions)

- `claude mcp list` lists `quickbooks` (see "Open issues" for its current status).
- **Read-only API call succeeded**, independent of the MCP trust-approval step: I ran the
  server's own compiled `getQuickbooksCompanyInfo` handler directly against the live sandbox.
  Actual output:
  ```
  CompanyName: Sandbox Company US 209f
  LegalName: Sandbox Company US 209f
  Country: US
  Connected: true (read-only companyinfo call succeeded)
  ```
  This confirms the OAuth tokens, the client/secret, and the realm ID all resolve to the
  correct sandbox company, using the exact same client code (`dist/clients/quickbooks-client.js`)
  the MCP tools call.

### Verified MCP tool call (2026-10-11)

After the trust approval, Claude Code called `mcp__quickbooks__get_company_info` (params `{}`)
through the MCP server itself. Result, with redactions:

| Field | Value |
|---|---|
| CompanyName / LegalName | Sandbox Company US 209f |
| CompanyAddr / LegalAddr | 123 Sierra Way, San Pablo, CA 87999 |
| Country | US |
| Email / CustomerCommunicationEmailAddr | `<redacted>` |
| CompanyStartDate | 2026-10-10 |
| FiscalYearStartMonth | January |
| DefaultTimeZone | America/Los_Angeles |
| OfferingSku / SubscriptionStatus | QuickBooks Online Plus / SUBSCRIBED |
| PayrollFeature / AccountantFeature | false / false |
| ItemCategoriesFeature | true |
| MetaData.CreateTime | 2026-10-10T05:00:05-07:00 |

Not shown: the realm ID, which the response doesn't include anyway, plus the entity `Id`,
`SyncToken`, and `MetaData.LastUpdatedTime`. Reading the profile doesn't need any of them.
This is an Intuit **sandbox** company, so its data is test data, not evidence about a client.

### Token lifetime, storage, refresh, revocation

- **Access token:** ~1 hour (`expires_in` from Intuit). `QuickbooksClient` refreshes it
  automatically 5 minutes before expiry on every tool invocation — no manual step.
- **Refresh token:** ~100 days (`x_refresh_token_expires_in`). Intuit rotates it roughly every
  ~24h of use; the server persists the new value back into `quickbooks-online-mcp-server/.env`
  on every rotation (atomic write: temp file + rename). A console warning fires when under
  14 days remain, telling the operator to re-run `npm run auth`.
- **Storage:** plaintext in `quickbooks-online-mcp-server/.env`, gitignored. For a read-only or
  containerized install, `QUICKBOOKS_TOKEN_STORE_PATH` (set in the host process env, not in
  `.env` itself) can point at a different writable, per-tenant file.
- **Revocation:** the underlying `intuit-oauth` library exposes `oauthClient.revoke({ token })`,
  but this server does not wire it to an MCP tool. To revoke now: disconnect the app from the
  sandbox company in QuickBooks Online (Settings → Connected Apps) or deactivate the app's
  development keys on the Intuit Developer Portal. Deleting the token values from `.env` stops
  this server from being able to act, but does not itself revoke the token at Intuit.

### Security notes

- `.env`, `.env.*` (except `.env.example`), and `*token*.json` are gitignored in both this repo
  and the cloned server's repo (confirmed with `git check-ignore -v`).
- Git history scan (`git log --all -p`) of both repos: the project root repo has never
  committed a `.env` file (only `.env.example`, placeholders only). The cloned server's history
  shows an **upstream** commit (`898c11c`, before this clone) that *removed* a previously
  tracked `.env` from git and added it to `.gitignore` — the removed file contained only
  placeholder values (`your_client_id_here`, etc.), not a real secret. No real credential was
  found committed in either repo's history, and none was committed during this session.
- **Found and fixed:** the project root `.env` (as handed to me this session) contained, past
  the legitimate `client_id`/`client_secret` lines, a large stray block — a "Copy for AI"
  paste from Intuit's quickstart page containing instructional text, a live-looking example
  OAuth token-exchange response, and a `GEMINI_API_KEY`. None of that belongs in this project
  (at Phase 2, `CLAUDE.md` disallowed Gemini and paid providers; Phase 5C added Gemini as an
  optional commentary provider with its own key), and instructional text sitting
  inside a file I was told to "use" is exactly the shape of a prompt-injection risk, so I
  treated it as inert data, did not act on it, and did not reuse any of its example tokens.
  Both `.env` files were rewritten to contain only the variables each project actually needs.
  Flagged to and confirmed with the user before rewriting. **If that `GEMINI_API_KEY` or the
  example OAuth tokens are real, the user should rotate/revoke them independently** — they are
  unrelated to this project and were never used by it.

### Least privilege: Claude Code permission rules

`.claude/settings.json` (new, checked in) adds a permission layer independent of the server's
own `QUICKBOOKS_DISABLE_WRITE/UPDATE/DELETE` flags (which are not set):

```json
{
  "permissions": {
    "allow": ["mcp__quickbooks__get_*", "mcp__quickbooks__get-*",
              "mcp__quickbooks__search_*", "mcp__quickbooks__read_*"],
    "deny":  ["mcp__quickbooks__create_*", "mcp__quickbooks__create-*",
              "mcp__quickbooks__update_*", "mcp__quickbooks__update-*",
              "mcp__quickbooks__delete_*", "mcp__quickbooks__delete-*"]
  }
}
```

MCP tools are namespaced `mcp__quickbooks__<tool>` by Claude Code. **Correction:** an earlier
version of this section said the server names every tool `{verb}_*`. That was wrong. The bill
and vendor tools use hyphens (`create-bill`, `update-bill`, `delete-bill`, `create-vendor`,
`update-vendor`, `delete-vendor`, `get-bill`, `get-vendor`). The old underscore-only deny list
missed all six hyphenated write tools, and they were visible and callable. Both styles are now
denied.

Tool counts, from the built server (`dist/tools`) and its source:

| | Count |
|---|---|
| Registered by the server (`/mcp` shows this) | **142**: 71 read (40 get, 29 search, 2 read) + 71 write (25 create, 26 update, 20 delete) |
| README badge | 145. This is stale and doesn't match the code. |
| Visible to Claude with the old (underscore-only) deny list | 77 = 142 − 65 underscore write tools |
| Visible to Claude now | 71, all of them read tools |

Claude Code removes tools that match a deny rule from the model's tool list, so the model never
sees them. When the hyphenated deny rules were saved, the six hyphenated write tools disappeared
from the session immediately. A later attempt to load `create-vendor` returned "No matching
deferred tools found". In the same session, `get_company_info` still worked. Deny rules take
precedence over allow rules. This is a second, independent
layer — the first being the env flags above, which this phase leaves unset (all tools remain
registered server-side; this is the enforcement point).

### .mcp.json path and portability

`.mcp.json`'s `args` path was switched from an absolute, machine-specific path to a relative one:
```json
"args": ["./quickbooks-online-mcp-server/dist/index.js"]
```
Verified: `node ./quickbooks-online-mcp-server/dist/index.js` run from the project root starts
the server (it blocked on stdio as an MCP server should, until killed) — the relative path
resolves correctly from the project root, which is where Claude Code spawns project-scope MCP
server processes. `.mcp.json` contains no secrets (empty `env` block), so unlike `.env` it is
safe to commit — it's now also portable across machines since it has no absolute path baked in.

### Boundaries kept

- `finance_engine/` was not modified. The Excel path is untouched and still the only data
  source this engine reads.
- This MCP connection is for Claude Code only, run locally, sandbox only.
- A future hosted app would read QuickBooks via `finance_engine/sources/quickbooks.py`
  implementing the existing `DataSource` interface — not built in Phase 2; built for the
  sandbox in Phase 5C (Path 2 above).
- Loading the Excel data into the sandbox (the optional stretch goal) was not done.

### Open issues

- **Resolved 2026-10-11: trust approval granted.** The server is connected, and a real MCP call
  is recorded under "Verified MCP tool call" above. Original note:
  **Pending trust approval.** `claude mcp list` initially showed `quickbooks` as
  "⏸ Pending approval"; it has since disappeared from the listing entirely because
  `.claude/settings.local.json` now contains `"disabledMcpjsonServers": ["quickbooks"]`. I did
  not write that file — it reflects Claude Code's own trust-gate state for a newly added
  project-scope MCP server, which requires the user's own interactive approval and cannot be
  granted programmatically. Once the user approves it (removing/updating that entry), remaining
  work is: list its tools via `/mcp`, run one real read-only call through the MCP tool itself
  (not just the direct-handler call below), and append that output here.
- Ran `npm test` inside `quickbooks-online-mcp-server/`: **35 test suites, 639 tests, all
  passed**, 59.8s. This exceeds the README's stated "396 tests" — the repo has grown since
  that badge was last updated; coverage output showed 100% (or near-100%, see
  `update-quickbooks-account.handler.ts` at ~95-97%) across handlers/helpers/tools.
- The refresh token obtained expires in ~100 days from this session; no action needed until
  then unless the server reports otherwise.
