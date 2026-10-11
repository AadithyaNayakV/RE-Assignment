# QuickBooks Online Sandbox — MCP Connection (Phase 2)

Status: **Connected and verified** (sandbox, local only). Registration with this Claude
Code session is pending a one-time interactive trust approval — see "Open issues" below.

## What I chose and why

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

## What I rejected and why

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

## Local vs. team hosting

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

## What changes for remote access / ten clients

- Ten clients means ten independent OAuth connections (ten refresh tokens), not ten copies of
  the server — same server, keyed token storage.
- `QUICKBOOKS_TOKEN_STORE_PATH` per tenant is the interim mechanism already built into this
  server; a secrets-manager-backed store would be the longer-term replacement.
- A future hosted app would read QuickBooks through `finance_engine/sources/quickbooks.py`
  implementing the existing `DataSource` interface — **not built in this phase**, and
  `finance_engine/` was not touched.

## Setup actually performed

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

## Proof it works (real output, not assumptions)

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

## Token lifetime, storage, refresh, revocation

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

## Security notes

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
  (`CLAUDE.md` explicitly disallows Gemini/paid providers), and instructional text sitting
  inside a file I was told to "use" is exactly the shape of a prompt-injection risk, so I
  treated it as inert data, did not act on it, and did not reuse any of its example tokens.
  Both `.env` files were rewritten to contain only the variables each project actually needs.
  Flagged to and confirmed with the user before rewriting. **If that `GEMINI_API_KEY` or the
  example OAuth tokens are real, the user should rotate/revoke them independently** — they are
  unrelated to this project and were never used by it.

## Least privilege: Claude Code permission rules

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

## .mcp.json path and portability

`.mcp.json`'s `args` path was switched from an absolute, machine-specific path to a relative one:
```json
"args": ["./quickbooks-online-mcp-server/dist/index.js"]
```
Verified: `node ./quickbooks-online-mcp-server/dist/index.js` run from the project root starts
the server (it blocked on stdio as an MCP server should, until killed) — the relative path
resolves correctly from the project root, which is where Claude Code spawns project-scope MCP
server processes. `.mcp.json` contains no secrets (empty `env` block), so unlike `.env` it is
safe to commit — it's now also portable across machines since it has no absolute path baked in.

## Boundaries kept

- `finance_engine/` was not modified. The Excel path is untouched and still the only data
  source this engine reads.
- This MCP connection is for Claude Code only, run locally, sandbox only.
- A future hosted app would read QuickBooks via `finance_engine/sources/quickbooks.py`
  implementing the existing `DataSource` interface — not built this phase.
- Loading the Excel data into the sandbox (the optional stretch goal) was not done.

## Open issues

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
