# Future hosting: documented, not built

**Nothing in this file exists in the repo.** No Dockerfile is committed, no service runs,
no cloud resource was created, nothing is published or exposed. This is the plan, so the
design question "where would this run?" has a written answer.

The local Claude Code MCP connection (`.mcp.json`, stdio, one laptop) is a **separate
thing** from a future hosted app's QuickBooks integration. The hosted app would not use
Claude Code's MCP connection. It would read QuickBooks through the direct reader,
`finance_engine/sources/quickbooks_api.py` (fetch) and `sources/quickbooks.py` (the
`DataSource` for a saved snapshot), with its own OAuth app. **Those two files exist since
Phase 5C, for the sandbox only, with one token file**; per-client tokens and a hosted
service do not.

## Configuration by environment variable only

Already true today (`finance_engine/config.py`, `.env.example`):
`LLM_PROVIDER`, `FINANCE_CONFIG_DIR`, `FINANCE_DATA_DIR`, `FINANCE_OUTPUT_DIR`. Since
Phase 5C the optional cloud commentary providers read `GEMINI_API_KEY` and
`ANTHROPIC_API_KEY` by name through a `CredentialStore` (environment, then a git-ignored
`.env`); no API key is assumed to exist, and the Claude API is off unless enabled in
`config/providers.yaml`. A hosted version would put those keys in a secrets manager
instead of `.env` (not built). Ollama is not built.

## AWS option [PLANNED, not built, not verified]

- **Secrets:** AWS Secrets Manager, one entry per client for the QuickBooks refresh token
  and one for each API key, read through a new `CredentialStore` implementation. The
  reader's token rotation would write back to the same entry. Nothing reads AWS today.
- **Claude via Amazon Bedrock:** a further `CommentaryProvider` next to `gemini` and
  `claude-api`, receiving the same brief and passing the same checks, with the
  deterministic report still the final fallback. Not built; model availability and
  pricing on Bedrock were not checked.

## Dockerfile (sketch)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY finance_engine/ finance_engine/
COPY config/ config/
USER nobody
ENTRYPOINT ["python", "-m", "finance_engine"]
```

Client data and output are **mounted volumes**, never baked into the image. No `.env` is
copied in; secrets arrive as environment variables from the platform's secret store.

## Health endpoint (sketch)

```python
@app.get("/health")
def health():
    return {"status": "ok", "engine": finance_engine.__version__,
            "llm_provider": os.environ.get("LLM_PROVIDER", "deterministic")}
```

It must not touch client data, QuickBooks or any secret, and must return nothing a caller
could use to enumerate clients.

## What a real deployment needs (none of it exists)

| Need | Plan |
|---|---|
| HTTPS | Terminate TLS at a managed load balancer or reverse proxy. Never plain HTTP. |
| Authentication | The firm's SSO (OIDC) in front of the service and the hosted MCP server. |
| Authorization | A user-to-client mapping; every request checks it. A request for a `client_id` the user may not see returns 404, not 403. |
| Secrets | A secret manager with one QuickBooks token entry per client. Rotation handled by the service, not by hand-editing `.env`. |
| Tenant isolation | Separate storage prefix per client, per-client credentials, per-client logs, and a test that proves client A's token cannot read client B's folder. |
| Audit | Log who ran what for which client, and any QuickBooks read. Never log token values. |

## What we do not claim

This repo has **no authentication, no authorization, no TLS and no tenant isolation**,
and it is not production-ready. Separation between clients today is by path and
convention (`docs/architecture.md`, section 5).
