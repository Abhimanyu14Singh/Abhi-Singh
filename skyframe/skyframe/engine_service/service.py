"""Local HTTP service: the engine on 127.0.0.1 for a page in the browser.

Routes (JSON; ``/engine`` prefix):

    GET  /engine/version                  {"engine", "api", "skyframe", "solvers"}
    POST /engine/supa/solve               body = Supa FemModel -> 202 {"job"}
    GET  /engine/jobs/<id>?after=N&wait=S  {"state", "steps": [...], "next"}; long-polls
                                          up to S seconds (max 25) for a step after N
    GET  /engine/jobs/<id>/result         200 FemResult | 409 not finished | 422 failed
    POST /engine/jobs/<id>/cancel         {"ok"}

Each solve runs in its own process (spawn), so OpenSees' global state never
leaks between jobs and a cancel simply ends that process.  One job runs at
a time; others queue.

Browsers: a page on a public site calling 127.0.0.1 gets two checks -- CORS,
and Private Network Access (a preflight with
``Access-Control-Request-Private-Network: true``, answered with
``Access-Control-Allow-Private-Network: true``).  Both are answered only
for origins on the allow list (``--allow-origin`` / ``SKYFRAME_ENGINE_ORIGINS``,
comma-separated; ``http://localhost:*`` and ``http://127.0.0.1:*`` always).
Requests carrying any other ``Origin`` are refused with 403, so an arbitrary
website cannot drive the engine.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import queue
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from . import ENGINE_API_VERSION

DEFAULT_PORT = 8766
MAX_BODY = 512 * 1024 * 1024          # 512 MB: a large FemModel is ~50 MB
LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")


def _run_job(kind: str, payload: dict, out: "mp.Queue") -> None:      # child process
    try:
        if kind == "supa":
            from .supa import solve_fem
            res = solve_fem(payload, lambda s: out.put(("step", s)))
        else:
            raise ValueError(f"unknown job kind {kind!r}")
        out.put(("done", res))
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        out.put(("error", f"{exc.__class__.__name__}: {exc}"))


class Job:
    def __init__(self, kind: str, payload: dict) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind, self.payload = kind, payload
        self.state = "queued"
        self.steps: List[dict] = []
        self.result: Optional[dict] = None
        self.error = ""
        self.cancelled = False
        self.proc: Optional[mp.Process] = None
        self.cv = threading.Condition()
        self.created = time.time()

    def step(self, text: str) -> None:
        with self.cv:
            self.steps.append({"n": len(self.steps) + 1, "step": text, "t": round(time.time() - self.created, 2)})
            self.cv.notify_all()

    def finish(self, state: str) -> None:
        with self.cv:
            self.state = state
            self.cv.notify_all()


class Engine:
    """Job queue: one solve at a time, each in a fresh spawned process."""

    def __init__(self, keep: int = 20) -> None:
        self.jobs: Dict[str, Job] = {}
        self.q: "queue.Queue[Job]" = queue.Queue()
        self.keep = keep
        self.ctx = mp.get_context("spawn")
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, kind: str, payload: dict) -> Job:
        j = Job(kind, payload)
        self.jobs[j.id] = j
        old = sorted(self.jobs.values(), key=lambda x: x.created)[:-self.keep]
        for o in old:
            if o.state in ("done", "failed", "cancelled"):
                self.jobs.pop(o.id, None)
        self.q.put(j)
        return j

    def cancel(self, jid: str) -> bool:
        j = self.jobs.get(jid)
        if not j:
            return False
        j.cancelled = True
        if j.proc is not None and j.proc.is_alive():
            j.proc.terminate()
        if j.state == "queued":
            j.finish("cancelled")
        return True

    def _loop(self) -> None:
        while True:
            j = self.q.get()
            if j.cancelled:
                j.finish("cancelled")
                continue
            out = self.ctx.Queue()
            j.state = "running"
            j.step("Starting the engine")
            j.proc = self.ctx.Process(target=_run_job, args=(j.kind, j.payload, out), daemon=True)
            j.proc.start()
            j.payload = {}                       # the child has it; free the memory
            while True:
                try:
                    kind, val = out.get(timeout=0.25)
                except queue.Empty:
                    if not j.proc.is_alive():
                        if j.cancelled:
                            j.finish("cancelled")
                        else:
                            j.error = f"the engine process stopped (exit code {j.proc.exitcode})"
                            j.finish("failed")
                        break
                    continue
                if kind == "step":
                    j.step(val)
                elif kind == "done":
                    j.result = val
                    j.finish("done")
                    break
                else:
                    j.error = val
                    j.finish("failed")
                    break
            j.proc.join(timeout=5)


def _versions() -> dict:
    from skyframe import __version__
    solvers = ["UmfPack"]
    try:
        import pypardiso  # noqa: F401
        solvers.append("PARDISO")
    except Exception:  # noqa: BLE001
        pass
    return {"engine": "SkyFrame (OpenSees)", "api": ENGINE_API_VERSION, "skyframe": __version__,
            "solvers": solvers, "jobs": ["supa"]}


def make_handler(engine: Engine, origins: List[str]):
    allowed = set(o.rstrip("/") for o in origins if o)

    def origin_ok(o: Optional[str]) -> bool:
        if o is None:                      # not a browser page (curl, a desktop app)
            return True
        return bool(LOCAL_ORIGIN.match(o)) or o.rstrip("/") in allowed

    class H(BaseHTTPRequestHandler):
        server_version = "SkyFrameEngine/" + ENGINE_API_VERSION
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):            # quiet; the CLI prints its own line
            pass

        # ── helpers ──
        def _cors(self) -> None:
            o = self.headers.get("Origin")
            if o and origin_ok(o):
                self.send_header("Access-Control-Allow-Origin", o)
                self.send_header("Vary", "Origin")

        def _send(self, code: int, body, ctype: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _guard(self) -> bool:
            o = self.headers.get("Origin")
            if o is not None and not origin_ok(o):
                self._send(403, {"error": f"origin {o} is not allowed; start the engine with --allow-origin {o}"})
                return False
            return True

        # ── verbs ──
        def do_OPTIONS(self):
            o = self.headers.get("Origin")
            if not origin_ok(o):
                self._send(403, {"error": "origin not allowed"})
                return
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not self._guard():
                return
            u = urlparse(self.path)
            parts = [p for p in u.path.split("/") if p]
            if parts == ["engine", "version"]:
                return self._send(200, _versions())
            if len(parts) >= 3 and parts[:2] == ["engine", "jobs"]:
                j = engine.jobs.get(parts[2])
                if not j:
                    return self._send(404, {"error": "no such job"})
                if len(parts) == 4 and parts[3] == "result":
                    if j.state == "done":
                        return self._send(200, j.result)
                    if j.state in ("failed", "cancelled"):
                        return self._send(422, {"state": j.state, "error": j.error or j.state})
                    return self._send(409, {"state": j.state})
                if len(parts) == 3:
                    qs = parse_qs(u.query)
                    after = int((qs.get("after") or ["0"])[0] or 0)
                    wait = min(25.0, float((qs.get("wait") or ["0"])[0] or 0))
                    end = time.time() + wait
                    with j.cv:
                        while (len(j.steps) <= after and j.state in ("queued", "running")
                               and time.time() < end):
                            j.cv.wait(timeout=max(0.0, end - time.time()))
                        steps = j.steps[after:]
                        return self._send(200, {"job": j.id, "state": j.state, "steps": steps,
                                                "next": len(j.steps), "error": j.error})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._guard():
                return
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"error": "model too large"})
            raw = self.rfile.read(n) if n else b""
            if parts == ["engine", "supa", "solve"]:
                try:
                    payload = json.loads(raw or b"{}")
                    from .supa import validate
                    validate(payload)
                except Exception as exc:  # noqa: BLE001
                    return self._send(400, {"error": str(exc)})
                j = engine.submit("supa", payload)
                return self._send(202, {"job": j.id, "state": j.state})
            if len(parts) == 4 and parts[:2] == ["engine", "jobs"] and parts[3] == "cancel":
                return self._send(200, {"ok": engine.cancel(parts[2])})
            return self._send(404, {"error": "not found"})

    return H


def serve(port: int = DEFAULT_PORT, origins: Optional[List[str]] = None,
          ready: Optional[threading.Event] = None) -> ThreadingHTTPServer:
    env = [o.strip() for o in os.environ.get("SKYFRAME_ENGINE_ORIGINS", "").split(",") if o.strip()]
    engine = Engine()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine, (origins or []) + env))
    httpd.daemon_threads = True
    httpd.engine = engine  # type: ignore[attr-defined]
    if ready is not None:
        ready.set()
    return httpd
