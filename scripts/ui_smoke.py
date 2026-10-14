"""Smoke test of the local web app: start it, request every page and route, stop it.

    python scripts/ui_smoke.py            # about 1 minute
    python scripts/ui_smoke.py --keep     # leave the throwaway project folder for a look

It runs against a THROWAWAY project folder in the system temp directory, holding one
generated SYNTHETIC test client (`smoke_co`, built by scripts/make_test_workbooks.py), so
no real client file and no file in this repo is read or written. Free and offline:

* every report runs with "Keep data on this machine" (`--provider deterministic`);
* the QuickBooks pull route is only asked WITHOUT the confirm flag (it must refuse), so
  no request to Intuit is ever made;
* the evals route is not started here (it runs the real eval suite; `pytest` covers the
  route with a stand-in command).

Exit status 0 when every check passed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CLIENT = "smoke_co"
NOT_STARTED = {("POST", "/api/jobs/evals"): "runs the real eval suite; pytest covers the route",
               }


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_root() -> Path:
    import make_test_workbooks
    root = Path(tempfile.mkdtemp(prefix="ui_smoke_"))
    (root / "config" / "clients").mkdir(parents=True)
    for name in ("providers.yaml", "review.yaml"):
        shutil.copy(ROOT / "config" / name, root / "config" / name)
    make_test_workbooks.build(str(root / "smoke_book.xlsx"), False,
                              "Smoke Co (SYNTHETIC)")
    return root


class Client:
    def __init__(self, base: str):
        self.base = base
        self.results: list[tuple[bool, str, str]] = []
        self.hit: set[tuple[str, str]] = set()

    def request(self, method: str, path: str, body=None, form=None, route=None,
                expect=(200,), quiet=False):
        headers = {"X-Finance-UI": "1"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if form is not None:
            boundary = uuid.uuid4().hex
            parts = []
            for key, value in form.items():
                if isinstance(value, tuple):
                    filename, content = value
                    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; '
                                 f'name="{key}"; filename="{filename}"\r\nContent-Type: '
                                 f'application/octet-stream\r\n\r\n'.encode() + content
                                 + b"\r\n")
                else:
                    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; '
                                 f'name="{key}"\r\n\r\n{value}\r\n'.encode())
            data = b"".join(parts) + f"--{boundary}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                status, text = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode("utf-8", "replace")
        ok = status in expect
        if not quiet or not ok:
            self.results.append((ok, f"{method} {path}", f"{status}"))
        if route:
            self.hit.add((method, route))
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def wait(self, job: dict) -> dict:
        while job.get("status") not in ("succeeded", "failed", "timeout", "error"):
            time.sleep(0.5)
            job = self.request("GET", f"/api/jobs/{job['id']}", route="/api/jobs/{job_id}",
                               quiet=True)
        self.results.append((job["status"] == "succeeded", f"  job {job['title']}",
                             job["status"]))
        return job


def run(c: Client, root: Path) -> None:
    q = urllib.parse.urlencode
    where = {"client": CLIENT, "period": "2026-01"}
    for page in ("/", "/static/js/app.js", "/static/styles.css", "/static/print.css",
                 "/static/theme-init.js", "/static/vendor/chart.umd.min.js",
                 "/static/js/pages/reports.js"):
        c.request("GET", page)
    for path in ("/.env", "/config/providers.yaml", "/static/../.env"):
        c.request("GET", path, expect=(404,))

    # add a company: upload, read the draft, validate, approve
    book = (root / "smoke_book.xlsx").read_bytes()
    c.request("POST", "/api/onboarding/draft", form={"client_id": CLIENT,
              "name": "Smoke Co (SYNTHETIC)", "file": ("smoke_book.xlsx", book)},
              route="/api/onboarding/draft")
    draft = c.request("GET", f"/api/onboarding/{CLIENT}/draft",
                      route="/api/onboarding/{client}/draft")
    c.request("POST", f"/api/onboarding/{CLIENT}/validate", body={"yaml": draft["yaml"]},
              route="/api/onboarding/{client}/validate")
    c.request("POST", f"/api/onboarding/{CLIENT}/approve", body={"by": "Smoke Approver"},
              route="/api/onboarding/{client}/approve")

    # clients, files, periods
    c.request("GET", "/api/clients", route="/api/clients")
    c.request("GET", f"/api/clients/{CLIENT}/files", route="/api/clients/{client}/files")
    c.request("GET", f"/api/clients/{CLIENT}/periods", route="/api/clients/{client}/periods")

    # reports: notice, analyze, report (local), whole file, every month for net income
    c.request("GET", f"/api/cloud-notice?{q({**where, 'keep_data_local': 'false'})}",
              route="/api/cloud-notice")
    c.wait(c.request("POST", "/api/jobs/analyze", body=where, route="/api/jobs/analyze",
                     expect=(202,)))
    c.wait(c.request("POST", "/api/jobs/report", body={**where, "keep_data_local": True},
                     route="/api/jobs/report", expect=(202,)))
    c.wait(c.request("POST", "/api/jobs/report", body={"client": CLIENT, "scope": "all",
                                                       "keep_data_local": True},
                     expect=(202,)))
    c.wait(c.request("POST", "/api/jobs/analyze-months", body={"client": CLIENT},
                     route="/api/jobs/analyze-months", expect=(202,)))
    c.request("GET", "/api/jobs", route="/api/jobs")
    c.request("GET", f"/api/report?{q(where)}", route="/api/report")
    c.request("GET", f"/api/report?{q({'client': CLIENT, 'scope': 'all'})}")
    c.request("GET", f"/api/brief?{q(where)}", route="/api/brief")
    c.request("GET", f"/api/export/report.md?{q(where)}", route="/api/export/report.md")
    c.request("GET", f"/api/export/metrics.json?{q(where)}",
              route="/api/export/metrics.json")
    c.request("GET", f"/api/charts?{q({'client': CLIENT})}", route="/api/charts")
    c.request("GET", f"/api/charts?{q({'client': CLIENT, 'year': 2025})}")
    c.request("GET", f"/api/charts?{q({**where, 'scope': 'month'})}")
    c.request("GET", f"/api/charts?{q({'client': CLIENT, 'scope': 'year', 'year': 2025})}")

    # review: the report job put the report in review (as "system"); send it back,
    # regenerate it with the comment, then mark it as reviewed
    queue = c.request("GET", "/api/review/queue", route="/api/review/queue")
    count = queue.get("count") if isinstance(queue, dict) else None
    c.results.append((bool(count), "  generated reports are ready for review",
                      f"count {count}"))
    c.request("POST", "/api/review/send-back", body={**where, "by": "Smoke Reviewer"},
              expect=(400,))                                 # a comment is required
    c.request("POST", "/api/review/send-back", body={**where, "by": "Smoke Reviewer",
                                                      "comment": "Smoke comment"},
              route="/api/review/send-back")
    c.request("POST", "/api/review/mark-reviewed", body={**where, "by": "Smoke Reviewer"},
              expect=(400,))                                 # changes requested first
    c.wait(c.request("POST", "/api/jobs/report", body={**where, "keep_data_local": True},
                     expect=(202,)))                         # regenerate with the comment
    c.request("POST", "/api/review/mark-reviewed", body={**where, "by": "Smoke Reviewer"},
              route="/api/review/mark-reviewed")
    c.request("POST", "/api/review/approve", body={**where, "by": "Smoke Reviewer"},
              expect=(404, 405))                             # no approve for reports
    status = c.request("GET", f"/api/review/status?{q(where)}", route="/api/review/status")
    c.request("GET", f"/api/review/log?{q(where)}", route="/api/review/log")
    c.request("GET", f"/api/review/version?{q({**where, 'name': status['versions'][0]})}",
              route="/api/review/version")

    # evals, sources, settings
    c.request("GET", "/api/evals", route="/api/evals")
    c.request("GET", "/api/quickbooks/status", route="/api/quickbooks/status")
    c.request("POST", "/api/jobs/quickbooks-pull", body={"client": CLIENT, "start": "2025-01",
              "end": "2025-02"}, route="/api/jobs/quickbooks-pull", expect=(400,))
    c.request("GET", "/api/providers", route="/api/providers")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keep", action="store_true", help="keep the throwaway folder")
    args = ap.parse_args(argv)
    from finance_engine.service import parity

    root = make_root()
    port = free_port()
    env = dict(os.environ, LLM_PROVIDER="deterministic", PYTHONIOENCODING="utf-8")
    server = subprocess.Popen([sys.executable, "-m", "finance_engine", "serve", "--port",
                               str(port), "--root", str(root)], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    c = Client(f"http://127.0.0.1:{port}")
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(c.base + "/", timeout=2)
                break
            except OSError:
                if server.poll() is not None:
                    print(server.stderr.read().decode("utf-8", "replace"))
                    return 1
                time.sleep(0.5)
        run(c, root)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)

    failed = [r for r in c.results if not r[0]]
    for ok, what, status in c.results:
        print(f"{'PASS' if ok else 'FAIL'}  {status:>9}  {what}")
    routes = {(r["method"], r["path"]) for r in parity.ROUTES}
    missing = sorted(routes - c.hit - set(NOT_STARTED))
    print(f"\n{len(c.results) - len(failed)}/{len(c.results)} checks passed; "
          f"{len(c.hit & routes)}/{len(routes)} routes requested.")
    for key, why in NOT_STARTED.items():
        print(f"  not started: {' '.join(key)} ({why})")
    if missing:
        print(f"  NOT REQUESTED: {missing}")
    print(f"server stopped; throwaway folder {'kept: ' + str(root) if args.keep else 'removed'}")
    return 0 if not failed and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
