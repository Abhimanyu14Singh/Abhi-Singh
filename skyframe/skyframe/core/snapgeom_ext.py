"""Extension / parallel snaps and 3D snap geometry (Wave C).

Exact mirror of ``js/wc_snapgeom.js`` (CONTRACT "Wave C interface ...
§ Snapping"); the drawing aids live in the browser, this module pins the
closed forms in the test-suite.

* :func:`snap_ext_par` - 2D extension snap (foot of W on the infinite line
  of a segment, beyond an end) and parallel snap (foot of W on the line
  through the pending point P parallel to a segment), ranked ext < par,
  then by distance;
* :func:`line_ray` - closest points of the line ``A + t u`` and the ray
  ``O + s d`` (2 x 2 Gram solve);
* :func:`seg_seg` - closest points of two 3D segments (intersection snap
  when their distance vanishes);
* :func:`plane_hit` - ray / plane intersection (vertex reshaper in 3D).
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

PRIORITY = {"joint": 0, "inter": 1, "mid": 2, "perp": 3, "ext": 4,
            "par": 5}


def _sub(a, b):
    return [a[i] - b[i] for i in range(len(a))]


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(len(a)))


def _norm(a):
    return math.sqrt(_dot(a, a))


def snap_ext_par(w, segs: Sequence, tol: float, modes: dict,
                 frm=None, max_ext: float = math.inf) -> Optional[dict]:
    """Best extension / parallel snap of 2D point ``w`` or None."""
    p = (float(w[0]), float(w[1]))
    cands = []
    if modes.get("ext"):
        for a, b in segs:
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            if not L2 > 1e-18:
                continue
            t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
            if -1e-9 <= t <= 1 + 1e-9:
                continue
            fx, fy = a[0] + t * dx, a[1] + t * dy
            d = math.hypot(p[0] - fx, p[1] - fy)
            gap = (-t if t < 0 else t - 1) * math.sqrt(L2)
            if d <= tol and gap <= max_ext:
                cands.append({"x": fx, "y": fy, "kind": "ext", "d": d})
    if modes.get("par") and frm is not None:
        for a, b in segs:
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy)
            if not L > 1e-9:
                continue
            ux, uy = dx / L, dy / L
            s = (p[0] - frm[0]) * ux + (p[1] - frm[1]) * uy
            fx, fy = frm[0] + s * ux, frm[1] + s * uy
            d = math.hypot(p[0] - fx, p[1] - fy)
            if d <= tol and abs(s) > tol:
                cands.append({"x": fx, "y": fy, "kind": "par", "d": d})
    if not cands:
        return None
    cands.sort(key=lambda c: (PRIORITY[c["kind"]], c["d"]))
    return cands[0]


def line_ray(A, u, O, d) -> Optional[dict]:
    """Closest points of line ``A + t u`` and ray ``O + s d``."""
    w0 = _sub(A, O)
    a, b, c = _dot(u, u), _dot(u, d), _dot(d, d)
    dd, e = _dot(u, w0), _dot(d, w0)
    den = a * c - b * b
    if not abs(den) > 1e-15 * a * c:
        return None
    t = (b * e - c * dd) / den
    s = (a * e - b * dd) / den
    p = [A[i] + t * u[i] for i in range(3)]
    q = [O[i] + s * d[i] for i in range(3)]
    return {"t": t, "s": s, "dist": _norm(_sub(p, q)), "p": p}


def seg_seg(a, b, c, e) -> Optional[dict]:
    """Closest points of segments ab and ce (None when parallel or the
    unconstrained closest points fall outside either segment)."""
    u, v, w0 = _sub(b, a), _sub(e, c), _sub(a, c)
    A, B, C = _dot(u, u), _dot(u, v), _dot(v, v)
    D, E = _dot(u, w0), _dot(v, w0)
    den = A * C - B * B
    if not den > 1e-18 * A * C:
        return None
    t = (B * E - C * D) / den
    s = (A * E - B * D) / den
    if t < -1e-9 or t > 1 + 1e-9 or s < -1e-9 or s > 1 + 1e-9:
        return None
    t, s = min(1.0, max(0.0, t)), min(1.0, max(0.0, s))
    p = [a[i] + t * u[i] for i in range(3)]
    q = [c[i] + s * v[i] for i in range(3)]
    return {"t": t, "u": s, "dist": _norm(_sub(p, q)),
            "p": [(p[i] + q[i]) / 2.0 for i in range(3)]}


def plane_hit(O, d, P0, n) -> Optional[list]:
    den = _dot(d, n)
    if abs(den) < 1e-12:
        return None
    s = _dot(_sub(P0, O), n) / den
    if not s > 0:
        return None
    return [O[i] + s * d[i] for i in range(3)]
