"""FastAPI routes over the service layer. Local only, no sign-in, no calculation.

    python -m finance_engine serve            # http://127.0.0.1:8765

Rules this module enforces (each tested in tests/test_web_app.py):

* **Loopback only.** `serve` binds 127.0.0.1 and refuses any other host. Every request
  must also come from a loopback address and carry a Host header naming 127.0.0.1 or
  localhost (a DNS-rebinding page gets 400).
* **No cross-origin changes.** Every POST must carry `X-Finance-UI: 1` (a header a
  cross-site form cannot send) and, when the browser sends an Origin, that Origin must be
  this server.
* **Only `ui/` is served** (index.html and ui/static). .env, .secrets, config, data and
  output are never served as files; reports and metrics are only returned through the
  routes below, for a validated client id.
* **No secret in any response.** Every JSON body passes through a redactor that knows the
  values of every key and token variable; status routes return names only.
* **No calculation.** Routes return what the engine wrote or formatted. Every route is
  listed in `finance_engine/service/parity.py` with the CLI command that does the same.

Names typed into the review forms are self-declared. Real sign-in is [PLANNED].
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from finance_engine import review
from finance_engine.init_client import InitError
from finance_engine.onboarding import OnboardingError
from finance_engine.service import core, uploads
from finance_engine.service.commands import CommandError
from finance_engine.service.jobs import JobConflict, JobManager

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
LOOPBACK_CLIENTS = {"127.0.0.1", "::1"}
UI_HEADER = "x-finance-ui"
MAX_BODY = uploads.MAX_UPLOAD_BYTES + 256 * 1024
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                "img-src 'self' data:; connect-src 'self'; "
                                "object-src 'none'; base-uri 'none'; form-action 'self'; "
                                "frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
_ERRORS = (core.ServiceError, CommandError, OnboardingError, InitError,
           review.ReviewError, uploads.UploadError, FileNotFoundError, ValueError)


def _host_name(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):
        return host[:host.find("]") + 1]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def create_app(root: Path, jobs: JobManager | None = None,
               qb_transport=None) -> FastAPI:
    root = Path(root).resolve()
    redact = core.make_redactor(root)
    jobs = jobs or JobManager(root, redact=redact)
    app = FastAPI(title="Financial Trend Analysis (local)", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.root, app.state.jobs, app.state.qb_transport = root, jobs, qb_transport

    def reply(data, status: int = 200) -> Response:
        text = redact(json.dumps(data, ensure_ascii=False, default=str))
        return Response(text, status_code=status,
                        media_type="application/json; charset=utf-8")

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = _host_name(request.headers.get("host", ""))
        client = request.client.host if request.client else ""
        if host not in LOOPBACK_HOSTS:
            return _secure(reply({"error": "Host not allowed: this app answers only on "
                                           "127.0.0.1."}, 400))
        if client not in LOOPBACK_CLIENTS:
            return _secure(reply({"error": "Only this machine may connect."}, 403))
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get(UI_HEADER) != "1":
                return _secure(reply({"error": "Refused: state-changing requests must "
                                               "come from this app's own pages."}, 403))
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc.lower() != \
                    request.headers.get("host", "").lower():
                return _secure(reply({"error": "Refused: cross-origin request."}, 403))
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_BODY:
                return _secure(reply({"error": "The upload is larger than 25 MB."}, 413))
        if request.method == "OPTIONS":
            return _secure(Response(status_code=405))
        return _secure(await call_next(request))

    async def refused(request: Request, exc: Exception):
        # The engine's own message, shown in a plain box by the UI.
        return _secure(reply({"error": str(exc)}, 409 if isinstance(exc, JobConflict)
                             else 400))

    for kind in (JobConflict, *_ERRORS):
        app.add_exception_handler(kind, refused)

    # ---- pages ----------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(UI_DIR / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")

    # ---- helpers --------------------------------------------------------------------

    def where(q) -> dict:
        year = q.get("year")
        return {"scope": q.get("scope") or "month", "period": q.get("period") or None,
                "year": int(year) if year not in (None, "") and str(year).isdigit()
                else (year or None), "file": q.get("file") or None}

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("The request body must be JSON.") from None
        if not isinstance(data, dict):
            raise ValueError("The request body must be a JSON object.")
        return data

    def need(q, name: str) -> str:
        value = q.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required.")
        return value

    # ---- clients, files, periods ------------------------------------------------------

    @app.get("/api/clients")
    def clients():
        return reply(core.list_clients(root))

    @app.get("/api/clients/{client}/files")
    def files(client: str):
        return reply(core.list_files(root, client))

    @app.get("/api/clients/{client}/periods")
    def periods(client: str, file: str | None = None):
        return reply(core.list_periods(root, client, file or None))

    # ---- one report ---------------------------------------------------------------------

    @app.get("/api/report")
    async def report(request: Request):
        q = request.query_params
        data = await run_in_threadpool(core.get_report, root, need(q, "client"), **where(q))
        return reply(data)

    @app.get("/api/brief")
    async def brief(request: Request):
        q = request.query_params
        data = await run_in_threadpool(core.get_report, root, need(q, "client"), **where(q))
        return reply({"brief": data["brief"]})

    def _export(request: Request, kind: str) -> Response:
        q = request.query_params
        client = need(q, "client")
        folder = core.report_dir(root, client, **where(q))
        name = {"md": "report.md", "json": "metrics.json"}[kind]
        path = folder / name
        if not path.exists():
            raise core.ServiceError(f"No {name} for these settings yet.")
        leaf = folder.name if folder.parent.name == client else \
            f"{folder.parent.name}_{folder.name}"
        media = "text/markdown" if kind == "md" else "application/json"
        return Response(redact(path.read_text(encoding="utf-8")),
                        media_type=f"{media}; charset=utf-8",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{client}_{leaf}_{name}"'})

    @app.get("/api/export/report.md")
    def export_md(request: Request):
        return _export(request, "md")

    @app.get("/api/export/metrics.json")
    def export_json(request: Request):
        return _export(request, "json")

    @app.get("/api/charts")
    def charts(client: str, file: str | None = None, year: int | None = None):
        return reply(core.chart_data(root, client, file or None, year))

    @app.get("/api/cloud-notice")
    def cloud_notice(request: Request):
        q = request.query_params
        keep = q.get("keep_data_local", "true") != "false"
        client = q.get("client") or None
        return reply(core.cloud_notice(root, keep, client, **(where(q) if client else {})))

    # ---- jobs -------------------------------------------------------------------------

    @app.post("/api/jobs/report")
    async def job_report(request: Request):
        b = await body(request)
        job = core.start_report(jobs, root, need(b, "client"), **where(b),
                                keep_data_local=b.get("keep_data_local", True) is not False)
        return reply(job.to_dict(), 202)

    @app.post("/api/jobs/analyze")
    async def job_analyze(request: Request):
        b = await body(request)
        job = core.start_report(jobs, root, need(b, "client"), **where(b),
                                command="analyze")
        return reply(job.to_dict(), 202)

    @app.post("/api/jobs/analyze-months")
    async def job_analyze_months(request: Request):
        b = await body(request)
        job = core.start_analyze_months(jobs, root, need(b, "client"), b.get("file") or None)
        return reply(job.to_dict(), 202)

    @app.post("/api/jobs/evals")
    async def job_evals(request: Request):
        await body(request)
        return reply(core.start_free_evals(jobs, root).to_dict(), 202)

    @app.post("/api/jobs/quickbooks-pull")
    async def job_pull(request: Request):
        b = await body(request)
        if b.get("confirm") is not True:
            raise ValueError("Confirm the pull first: it reads the QuickBooks sandbox "
                             "(read-only) and saves a new snapshot file.")
        job = core.start_quickbooks_pull(jobs, root, need(b, "client"), need(b, "start"),
                                         need(b, "end"), transport=app.state.qb_transport)
        return reply(job.to_dict(), 202)

    @app.get("/api/jobs")
    def job_list():
        return reply({"jobs": [j.to_dict() for j in reversed(jobs.list())]})

    @app.get("/api/jobs/{job_id}")
    def job_get(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            return reply({"error": f"No job {job_id!r} (jobs are kept in memory until "
                                   f"the server stops)."}, 404)
        return reply(job.to_dict())

    # ---- add a company -------------------------------------------------------------------

    @app.post("/api/onboarding/draft")
    async def onboarding_draft(request: Request):
        form = await request.form(max_files=1, max_fields=8, max_part_size=64 * 1024)
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise ValueError("Choose an Excel workbook (.xlsx or .xlsm) to upload.")
        data = await upload.read(uploads.MAX_UPLOAD_BYTES + 1)
        client = str(form.get("client_id") or "")
        name = str(form.get("name") or "")
        force = str(form.get("force") or "") == "true"
        saved = await run_in_threadpool(uploads.save_upload, core._settings(root).data_dir,
                                        client, upload.filename or "", data)
        summary = await run_in_threadpool(core.draft_client, root, saved, client, name,
                                          force)
        return reply({"summary": summary, **core.draft_config_text(root, client)})

    @app.get("/api/onboarding/{client}/draft")
    def onboarding_get(client: str):
        return reply(core.draft_config_text(root, client))

    @app.post("/api/onboarding/{client}/validate")
    async def onboarding_validate(client: str, request: Request):
        b = await body(request)
        text = b.get("yaml")
        if text is not None and not isinstance(text, str):
            raise ValueError("yaml must be text.")
        result = await run_in_threadpool(core.validate_config, root, client, True, text)
        return reply(result)

    @app.post("/api/onboarding/{client}/approve")
    async def onboarding_approve(client: str, request: Request):
        b = await body(request)
        record = await run_in_threadpool(core.approve_config, root, client, b.get("by"))
        return reply(record)

    # ---- review --------------------------------------------------------------------------

    @app.get("/api/review/status")
    def review_status(request: Request):
        q = request.query_params
        return reply(core.review_status(root, need(q, "client"), **where(q)))

    @app.get("/api/review/log")
    def review_log(request: Request):
        q = request.query_params
        data = core.review_status(root, need(q, "client"), **where(q))
        return reply({"log": data["log"], "versions": data["versions"]})

    @app.get("/api/review/version")
    def review_version(request: Request):
        q = request.query_params
        return reply(core.review_version(root, need(q, "client"), need(q, "name"),
                                         **where(q)))

    def _review(action: str):
        async def handler(request: Request):
            b = await body(request)
            return reply(core.review_action(root, need(b, "client"), action, b.get("by"),
                                            b.get("comment"), **where(b)))
        return handler

    app.post("/api/review/submit")(_review(review.SUBMIT))
    app.post("/api/review/approve")(_review(review.APPROVE))
    app.post("/api/review/send-back")(_review(review.SEND_BACK))

    @app.get("/api/review/queue")
    def review_queue():
        return reply(core.review_queue(root))

    # ---- evals, sources, settings ----------------------------------------------------------

    @app.get("/api/evals")
    def evals():
        return reply(core.list_evals(root))

    @app.get("/api/quickbooks/status")
    def qb_status():
        return reply(core.quickbooks_status(root))

    @app.get("/api/providers")
    def providers():
        return reply(core.provider_status(root))

    @app.api_route("/{rest:path}", methods=["GET", "POST"], include_in_schema=False)
    def not_found(rest: str):
        return reply({"error": "Not found."}, 404)

    return app


def _secure(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def serve(root: Path, host: str, port: int) -> int:
    if host != "127.0.0.1":
        raise SystemExit("The web app binds to 127.0.0.1 only.")
    import uvicorn
    print(f"Serving on http://127.0.0.1:{port}  (local only, no sign-in; Ctrl+C stops)")
    uvicorn.run(create_app(root), host=host, port=port, log_level="warning",
                server_header=False, proxy_headers=False)
    return 0
