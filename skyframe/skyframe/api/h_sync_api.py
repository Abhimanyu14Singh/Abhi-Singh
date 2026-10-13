"""Wave H: window sync relay and the compare-view difference endpoint.
Registered by ``create_app`` with one line.  CONTRACT "Windows, pop-outs
and side-by-side compare (Wave H)".

Window sync relay (fallback transport of ``js/h_sync.js``)
----------------------------------------------------------
Pop-out windows normally talk to the main window over a same-origin
``BroadcastChannel``.  When that channel does not reach the other window
(two pywebview / WebView2 windows that do not share a browser profile, or
a pop-out that the OS opened in another browser) the windows fall back to
this in-memory relay on the local server::

    POST /api/hsync/<channel>          body {"msg": {...}}  -> {"seq": n}
    GET  /api/hsync/<channel>?after=<seq>&wait=<s>
                                       -> {"seq": last, "msgs": [{"seq", "msg"}]}

``wait`` (0..25 s, default 0) long-polls until a message newer than
``after`` arrives.  Each channel keeps the last :data:`MAX_MSGS` messages
and at most :data:`MAX_BYTES` of JSON (oldest dropped first); channels idle
for :data:`IDLE_S` seconds are forgotten.  Messages are opaque to the
server: it never reads or changes the model.  ``channel`` must match
``[A-Za-z0-9_-]{4,64}``.

Compare view
------------
``POST /api/results/compare/fields`` body::

    {"a": <results> | "current", "b": <results> | "current",
     "case_a": "c:DEAD", "case_b": "c:LIVE" | null, "env": "max",
     "rtol": 1e-6, "atol": 0.0}

returns :func:`skyframe.core.compare_view.compare_fields` (difference
``B - A`` of joint displacements, reactions and frame stations, matched
joints, per-quantity stats and the Compare Results flag count).
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Dict

from flask import jsonify, request

from skyframe.core import compare_view as _cv
from skyframe.core import results_diff as _rd

CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
MAX_MSGS = 500
MAX_BYTES = 64 * 1024 * 1024
MAX_MSG_BYTES = 48 * 1024 * 1024
MAX_WAIT = 25.0
IDLE_S = 600.0
MAX_CHANNELS = 64


class Relay:
    """Bounded per-channel message log with long-poll reads."""

    def __init__(self, max_msgs: int = MAX_MSGS, max_bytes: int = MAX_BYTES,
                 idle_s: float = IDLE_S, clock: Callable[[], float] = time.monotonic):
        self.max_msgs = max_msgs
        self.max_bytes = max_bytes
        self.idle_s = idle_s
        self.clock = clock
        self._cv = threading.Condition()
        self._ch: Dict[str, dict] = {}

    def _get(self, name: str) -> dict:
        now = self.clock()
        for k in [k for k, c in self._ch.items() if now - c["t"] > self.idle_s]:
            del self._ch[k]
        c = self._ch.get(name)
        if c is None:
            if len(self._ch) >= MAX_CHANNELS:
                oldest = min(self._ch, key=lambda k: self._ch[k]["t"])
                del self._ch[oldest]
            c = {"seq": 0, "msgs": [], "bytes": 0, "t": now}
            self._ch[name] = c
        c["t"] = now
        return c

    def post(self, name: str, msg: Any) -> int:
        size = len(json.dumps(msg, separators=(",", ":")))
        if size > MAX_MSG_BYTES:
            raise ValueError(f"message too large ({size} bytes)")
        with self._cv:
            c = self._get(name)
            c["seq"] += 1
            c["msgs"].append((c["seq"], msg, size))
            c["bytes"] += size
            while c["msgs"] and (len(c["msgs"]) > self.max_msgs
                                 or c["bytes"] > self.max_bytes):
                _s, _m, sz = c["msgs"].pop(0)
                c["bytes"] -= sz
            self._cv.notify_all()
            return c["seq"]

    def read(self, name: str, after: int = 0, wait: float = 0.0) -> dict:
        deadline = self.clock() + max(0.0, min(MAX_WAIT, wait))
        with self._cv:
            while True:
                c = self._get(name)
                msgs = [{"seq": s, "msg": m} for s, m, _z in c["msgs"] if s > after]
                if msgs or self.clock() >= deadline:
                    return {"seq": c["seq"], "msgs": msgs}
                self._cv.wait(timeout=min(0.5, max(0.01, deadline - self.clock())))


def register(app, state: Dict[str, Any], lookup: Callable) -> None:
    relay = Relay()
    app.config["SKYFRAME_HSYNC_RELAY"] = relay        # tests / desktop selftest

    def bad(msg, code=400):
        return jsonify({"error": str(msg)}), code

    @app.post("/api/hsync/<channel>")
    def hsync_post(channel):
        """Relay one window-sync message (pop-out <-> main window)."""
        if not CHANNEL_RE.match(channel):
            return bad("bad channel name")
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("msg"), dict):
            return bad("body must be {\"msg\": {...}}")
        try:
            return jsonify({"seq": relay.post(channel, body["msg"])})
        except ValueError as exc:
            return bad(exc, 413)

    @app.get("/api/hsync/<channel>")
    def hsync_read(channel):
        """Messages after ``after`` (long-poll up to ``wait`` seconds)."""
        if not CHANNEL_RE.match(channel):
            return bad("bad channel name")
        try:
            after = int(request.args.get("after", 0))
            wait = float(request.args.get("wait", 0))
        except ValueError:
            return bad("'after' / 'wait' must be numbers")
        if wait != wait or wait < 0:
            return bad("'wait' must be >= 0")
        return jsonify(relay.read(channel, after, wait))

    def side(v):
        if v == "current":
            res, _ctx = lookup(state["model"])
            if res is None:
                raise ValueError("no current results: run the analysis first")
            return res
        return _rd.unwrap(v)

    @app.post("/api/results/compare/fields")
    def results_compare_fields():
        """Difference fields B - A for the side-by-side compare view."""
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return bad("body must be a JSON object")
        try:
            a = side(body.get("a", "current"))
            bb = body.get("b", "current")
            b = a if bb == body.get("a", "current") and bb == "current" else side(bb)
            out = _cv.compare_fields(
                a, b, str(body.get("case_a") or ""),
                (str(body["case_b"]) if body.get("case_b") else None),
                env=str(body.get("env") or "max"),
                rtol=float(body.get("rtol", 1e-6)),
                atol=float(body.get("atol", 0.0)))
            return jsonify(out)
        except (ValueError, TypeError) as exc:
            return bad(exc)
