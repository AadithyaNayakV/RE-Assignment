# Future hosting: documented, not built

**Nothing in this file exists in the repo.** No Dockerfile is committed, no service runs,
no cloud resource was created, nothing is published or exposed. This is the plan, so the
design question "where would this run?" has a written answer.

The local Claude Code MCP connection (`.mcp.json`, stdio, one laptop) is a **separate
thing** from a future hosted app's QuickBooks integration. The hosted app would not use
Claude Code's MCP connection. It would read QuickBooks through
`finance_engine/sources/quickbooks.py`, a new `DataSource` implementation, with its own
OAuth app and per-client tokens. That file does not exist yet.

## Configuration by environment variable only

Already true today (`finance_engine/config.py`, `.env.example`):
`LLM_PROVIDER`, `FINANCE_CONFIG_DIR`, `FINANCE_DATA_DIR`, `FINANCE_OUTPUT_DIR`. The
planned additions are `OLLAMA_BASE_URL` and `ANTHROPIC_API_KEY`, read through a
`CredentialStore`. Neither provider is implemented, and no API key is assumed to exist.

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
