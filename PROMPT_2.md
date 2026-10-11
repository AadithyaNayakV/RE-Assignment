# PHASE 2 — QUICKBOOKS ONLINE SANDBOX CONNECTION (MCP)

Act as a senior integration engineer. Connect my QuickBooks Online **sandbox** to Claude Code using the official MCP server I cloned:
https://github.com/intuit/quickbooks-online-mcp-server

My setup: Windows, VS Code, Claude Code (Claude Pro), Node.js, Intuit developer account + sandbox company + app already created. A `.env` may exist. Run locally only in this phase. Do not deploy.

If the repo needs a particular runtime, account setting or manual OAuth step, explain the exact step instead of inventing a workaround.

Read `CLAUDE.md` first and follow the project rules. Do not change `finance_engine/`; the Excel path must keep working without QuickBooks.

## 1. Inspect before changing

Read the repo's README, package.json scripts, auth instructions, env variable names and tool list. Check Git status. Do not guess callback URLs, commands or variable names. Preserve existing working config.

## 2. Configure

Following the repo's documented steps:
1. Install dependencies and build.
2. Configure sandbox credentials and company (realm) ID via environment variables / `.env` (never hard-coded).
3. Complete the documented OAuth flow. If I must click something in a browser, give me the exact step.
4. Register the server with Claude Code using its supported MCP config (project `.mcp.json` or `claude mcp add`). The config file must reference env variables, not contain secrets. Check whether `.mcp.json` would be committed and make it safe.
5. Explain how access tokens (short-lived) and refresh tokens are stored and refreshed in this implementation, and how to revoke them.

Never ask me to paste secrets into chat. Never print tokens.

## 3. Prove it works (evidence, not assumptions)

- The server starts.
- `claude mcp list` (or equivalent) shows it; Claude Code lists its tools.
- One real **read-only** request succeeds (e.g. company info, or a P&L / account list).
- The returned company name matches my sandbox company.
Record the commands and the (secret-free) output in the docs. Do not claim success just because it builds.

## 4. Security

- Sandbox only. `.env`, token files and any credential cache in `.gitignore`. `.env.example` with placeholders.
- Scan the repo and Git history for accidental secrets.
- Document least privilege (read-only usage where possible), token lifetime, refresh, revocation.

## 5. Write docs/quickbooks_setup.md

Include:
- **What I chose and why**: the official Intuit MCP server.
- **What I rejected and why**: (a) a native Claude connector for QuickBooks, if one exists (verify, do not assume), (b) community open-source QuickBooks MCP servers, (c) a custom MCP on the QuickBooks Accounting API. Compare effort, maintenance, security, control. Do not claim I tested options I only considered.
- **Team hosting answer** (the brief asks this directly): where this runs if a whole team, not just my laptop, uses it, e.g. a hosted MCP server over HTTPS behind company login, OAuth tokens per client in a secret manager, audit logging, one QuickBooks connection per client. Mark this as **planned, not implemented**.
- Why local hosting is right for this prototype, and what changes for remote access or ten clients.
- Exact setup and verification commands, actual results, open issues.

## 6. Keep the boundaries

- The local MCP connection is for Claude Code. A future hosted app would read QuickBooks through `finance_engine/sources/quickbooks.py` (same `DataSource` interface). Mention this in the docs; do not build it now.
- Loading the Excel data into the sandbox is an optional stretch goal. Do not do it in this phase.

If OAuth or anything external blocks you, finish the safe parts and give me precise manual steps. Report the real connection status at the end.