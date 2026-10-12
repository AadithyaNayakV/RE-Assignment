# SAVED QuickBooks sandbox output (not live)

Use this only if the sandbox or the token is down during the demo, and say so out loud:
"this is the output I saved earlier, not a live call."

Captured 2026-10-11 by calling `get_company_info` with `{"params": {}}` through the MCP
server (once via Claude Code, once by `scripts/preflight_check.py`). Redacted: email
addresses, entity Id, SyncToken, realm ID and every token. Sandbox data, not a client.

| Field | Value |
|---|---|
| CompanyName / LegalName | Sandbox Company US 209f |
| Country | US |
| CompanyStartDate | 2026-10-10 |
| FiscalYearStartMonth | January |
| OfferingSku / SubscriptionStatus | QuickBooks Online Plus / SUBSCRIBED |

Server: Intuit's official `intuit/quickbooks-online-mcp-server`, 142 tools registered, 71
read-type visible to Claude (the 71 write tools are denied in `.claude/settings.json`).
