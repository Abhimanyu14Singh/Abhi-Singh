"""Wave H — extra OS windows for the desktop shell (pop-out view windows).

``skyframe/desktop.py`` hands an instance of :class:`DesktopWindows` to
pywebview as the ``js_api`` of the main window and of every pop-out, so the
page can call (``window.pywebview.api.*``, each returns a Promise)::

    open_window(wid, query, title, x, y, width, height) -> {"ok", "id"}
    close_window(wid)                                    -> {"ok"}
    list_windows()                                       -> ["w2", ...]
    screens()                                            -> [{"x","y","width","height"}]
    selftest_result(payload)                             -> {"ok"}

Why not ``window.open``?  pywebview handles WebView2's NewWindowRequested
itself: a ``window.open`` from the page is sent to the system browser (or
replaces the current page), so it never becomes a second app window.  The
pop-out therefore goes through ``webview.create_window`` with the SAME local
server URL plus ``?hpop=<window id>&hs=<channel>`` (the page renders that
single view and syncs with the main window - js/h_sync.js).

Rules: a pop-out URL is always the app's own local URL (only the query
string comes from the page, and only whitelisted keys); window ids match
``w\\d{1,3}``; closing the main window closes every pop-out
(:meth:`close_all`, wired to the main window's ``events.closed``);
positions / sizes are remembered by the page per monitor layout
(localStorage) and passed back here as x / y / width / height.

``--selftest-windows`` (frozen-build check): the main window loads
``?mock=1&hselftest=1``; the page pops out one window, waits for the sync
handshake and a selection round trip in both directions, reports through
:meth:`selftest_result`, and the process exits 0 (pass) / 1 (fail or
timeout).  It needs a desktop session (WebView2 on Windows); see
docs/UI_LAYOUT.md "Windows and pop-outs" for what to check by hand.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

WID_RE = re.compile(r"^w\d{1,3}$")
QUERY_KEYS = ("hpop", "hs", "mock", "renderer", "hsync", "hselftest", "desktop")
MAX_WINDOWS = 16


class DesktopWindows:
    """js_api bridge: open / close extra pywebview windows."""

    def __init__(self, webview_mod: Any, base_url: str,
                 title: str = "SkyFrame") -> None:
        self._webview = webview_mod
        self._base = base_url.split("?", 1)[0]
        self._title = title
        self._wins: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._selftest: Optional[dict] = None
        self._selftest_evt = threading.Event()
        self._on_result: Optional[Callable[[dict], None]] = None

    # ------------------------------------------------------------ helpers
    def _url(self, query: Any) -> str:
        q = {}
        if isinstance(query, dict):
            for k in QUERY_KEYS:
                v = query.get(k)
                if v is None or v is False or v == "":
                    continue
                v = str(v)
                if len(v) > 80 or not re.match(r"^[A-Za-z0-9_.-]*$", v):
                    raise ValueError(f"bad query value for {k!r}")
                q[k] = v
        return self._base + ("?" + urlencode(q) if q else "")

    @staticmethod
    def _int(v, lo, hi) -> Optional[int]:
        try:
            v = int(round(float(v)))
        except (TypeError, ValueError):
            return None
        return max(lo, min(hi, v))

    # ------------------------------------------------------------ js api
    def open_window(self, wid, query=None, title=None, x=None, y=None,
                    width=None, height=None) -> dict:
        wid = str(wid or "")
        if not WID_RE.match(wid):
            return {"ok": False, "error": "bad window id"}
        try:
            url = self._url(query)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            if wid in self._wins:
                return {"ok": True, "id": wid, "existing": True}
            if len(self._wins) >= MAX_WINDOWS:
                return {"ok": False, "error": "too many windows"}
        kw = {"width": self._int(width, 320, 10000) or 900,
              "height": self._int(height, 240, 10000) or 640,
              "min_size": (320, 240), "js_api": self}
        xx, yy = self._int(x, -20000, 20000), self._int(y, -20000, 20000)
        if xx is not None and yy is not None:
            kw["x"], kw["y"] = xx, yy
        t = str(title or f"{self._title} — window {wid[1:]}")[:120]
        win = self._webview.create_window(t, url, **kw)
        with self._lock:
            self._wins[wid] = win
        try:
            win.events.closed += (lambda *_a, _w=wid: self._forget(_w))
        except Exception:  # noqa: BLE001 - events are optional (fakes / old pywebview)
            pass
        return {"ok": True, "id": wid}

    def _forget(self, wid: str) -> None:
        with self._lock:
            self._wins.pop(wid, None)

    def close_window(self, wid) -> dict:
        with self._lock:
            win = self._wins.pop(str(wid), None)
        if win is None:
            return {"ok": False}
        try:
            win.destroy()
        except Exception:  # noqa: BLE001 - already gone
            pass
        return {"ok": True}

    def list_windows(self) -> List[str]:
        with self._lock:
            return sorted(self._wins)

    def screens(self) -> List[dict]:
        """Monitor rectangles (pywebview ``webview.screens``; x / y from the
        native frame when the platform exposes it)."""
        out = []
        try:
            scr = self._webview.screens
            scr = scr() if callable(scr) else scr
        except Exception:  # noqa: BLE001 - GUI not ready
            scr = []
        for s in scr or []:
            x = y = 0
            fr = getattr(s, "frame", None)
            b = getattr(fr, "Bounds", None) if fr is not None else None
            if b is not None:
                x, y = getattr(b, "X", 0), getattr(b, "Y", 0)
            elif isinstance(fr, (tuple, list)) and len(fr) >= 2:
                x, y = fr[0], fr[1]
            out.append({"x": int(x), "y": int(y),
                        "width": int(getattr(s, "width", 0)),
                        "height": int(getattr(s, "height", 0))})
        return out

    def close_all(self) -> None:
        for wid in self.list_windows():
            self.close_window(wid)

    def selftest_result(self, payload=None) -> dict:
        self._selftest = payload if isinstance(payload, dict) else {"ok": False, "error": "no payload"}
        self._selftest_evt.set()
        if self._on_result:
            self._on_result(self._selftest)
        return {"ok": True}

    def wait_selftest(self, timeout: float) -> Optional[dict]:
        self._selftest_evt.wait(timeout)
        return self._selftest


def attach_main(main_window: Any, windows: DesktopWindows) -> None:
    """Closing the main window closes every pop-out."""
    try:
        main_window.events.closed += (lambda *_a: windows.close_all())
    except Exception:  # noqa: BLE001 - events are optional
        pass
