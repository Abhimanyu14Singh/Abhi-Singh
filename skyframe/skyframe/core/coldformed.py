"""Cold-formed steel shapes: lipped C, lipped Z and hat (Wave B).

CONTRACT "Orthotropic materials, slab / deck property types, cold-formed and
composite sections, named line / area springs".  Registered in
:mod:`skyframe.core.shape_props` (``SHAPE_TYPES``) like every parametric
shape, so a FrameSection with ``shape`` + ``dims`` gets its properties
from here (and the finite-element ``torsion = "exact"`` option).

dims (m), all out-to-out like the AISI Manual Part I tables:

``C_lipped``  d depth, b flange width, dl lip length, t thickness
              [, r inside bend radius = 0]
``Z_lipped``  same keys (top flange toward +3, bottom flange toward -3)
``hat``       d depth, b crown width, bf flange width (web outside face to
              tip), t [, r]

Placement (x = local 3, y = local 2): C - web on the LEFT with its back at
x = 0, flanges toward +3, lips turned inward; Z - web centre line on
x = 0; hat - symmetric about x = 0, crown at the top, flanges at the
bottom turned outward.

Method - the AISI Manual "linear method" with ROUNDED corners: the section
is its centre line (straight elements + 90-degree arcs of centre-line
radius ``r + t/2``) times the thickness t.  A, the centroid, I33 = int
y^2 dA, I22 = int x^2 dA, I23 are exact line integrals (closed-form
trigonometric integrals on the arcs) plus the t^3/12 terms of each piece
about its own mid-line; J = t^3 L / 3; the shear centre and the warping
constant Cw come from the thin-walled sectorial coordinate (Vlasov) on a
fine discretisation of the centre line (arcs 64 chords); S = I / c with
c the extreme FIBRE (outline, incl. t/2); Z (plastic) by the equal-area
axis on the discretised centre line.  The finite-element option
(``torsion = "exact"``) meshes the true outline (:func:`regions`).

Shear areas: C / Z ``As2 = d t``, ``As3 = 5/3 b t``; hat ``As2 = 2 d t``,
``As3 = 5/6 (b + 2 bf) t``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

TYPES = ("C_lipped", "Z_lipped", "hat")
DIM_KEYS = {
    "C_lipped": (("d", "b", "dl", "t"), ("r",)),
    "Z_lipped": (("d", "b", "dl", "t"), ("r",)),
    "hat": (("d", "b", "bf", "t"), ("r",)),
}
SHEAR_AREA_FORMULAS = {
    "C_lipped": "As2 = d*t; As3 = 5/3*b*t",
    "Z_lipped": "As2 = d*t; As3 = 5/3*b*t",
    "hat": "As2 = 2*d*t; As3 = 5/6*(b + 2*bf)*t",
}
ARC_CHORDS = 64


def check(typ: str, g: Dict[str, float]) -> str:
    """'' or a geometry problem (dims already > 0)."""
    t, r = g["t"], g.get("r", 0.0)
    rc = r + t / 2.0
    if typ in ("C_lipped", "Z_lipped"):
        if 2 * (r + t) >= g["d"]:
            return "the bends do not fit in the depth"
        if 2 * (r + t) >= g["b"]:
            return "the bends do not fit in the flange width"
        if g["dl"] <= r + t:
            return "the lip is shorter than the bend (dl > r + t)"
        if 2 * g["dl"] >= g["d"]:
            return "the lips overlap (2 dl >= d)"
    else:
        if 2 * (r + t) >= g["d"]:
            return "the bends do not fit in the depth"
        if 2 * (r + t) >= g["b"]:
            return "the bends do not fit in the crown width"
        if g["bf"] <= rc + t / 2:
            return "the flange is shorter than the bend"
    return ""


# --------------------------------------------------------------------------- #
# centre line
# --------------------------------------------------------------------------- #
def centerline(typ: str, g: Dict[str, float]) -> List[Tuple[float, float]]:
    """Sharp-corner centre-line polyline (placement coordinates)."""
    d, t = g["d"], g["t"]
    h = t / 2.0
    if typ == "C_lipped":
        b, dl = g["b"], g["dl"]
        return [(b - h, d - dl), (b - h, d - h), (h, d - h), (h, h),
                (b - h, h), (b - h, dl)]
    if typ == "Z_lipped":
        b, dl = g["b"], g["dl"]
        return [(b - t, d - dl), (b - t, d - h), (0.0, d - h), (0.0, h),
                (-(b - t), h), (-(b - t), dl)]
    b, bf = g["b"], g["bf"]
    xw = b / 2.0 - h
    return [(-(b / 2.0 + bf), h), (-xw, h), (-xw, d - h), (xw, d - h),
            (xw, h), (b / 2.0 + bf, h)]


def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return dx / n, dy / n


def pieces(typ: str, g: Dict[str, float]) -> List[tuple]:
    """[("line", p0, p1) | ("arc", cx, cy, R, a0, a1)] in path order."""
    pts = centerline(typ, g)
    rc = g.get("r", 0.0) + g["t"] / 2.0
    out: List[tuple] = []
    start = pts[0]
    for i in range(1, len(pts) - 1):
        P = pts[i]
        u_in = _unit(pts[i - 1], P)
        u_out = _unit(P, pts[i + 1])
        if g.get("r", 0.0) <= 0.0:
            out.append(("line", start, P))
            start = P
            continue
        T1 = (P[0] - rc * u_in[0], P[1] - rc * u_in[1])
        T2 = (P[0] + rc * u_out[0], P[1] + rc * u_out[1])
        C = (T1[0] + rc * u_out[0], T1[1] + rc * u_out[1])
        out.append(("line", start, T1))
        a0 = math.atan2(T1[1] - C[1], T1[0] - C[0])
        a1 = math.atan2(T2[1] - C[1], T2[0] - C[0])
        cr = u_in[0] * u_out[1] - u_in[1] * u_out[0]
        da = a1 - a0
        if cr > 0:            # counter-clockwise turn
            while da <= 0:
                da += 2 * math.pi
        else:
            while da >= 0:
                da -= 2 * math.pi
        out.append(("arc", C[0], C[1], rc, a0, a0 + da))
        start = T2
    out.append(("line", start, pts[-1]))
    return out


def _moments(pcs, t: float) -> Dict[str, float]:
    """Raw integrals (about the origin) of the thin-walled section."""
    L = Sx = Sy = Ixx = Iyy = Ixy = 0.0
    k3 = t ** 3 / 12.0
    for p in pcs:
        if p[0] == "line":
            (x0, y0), (x1, y1) = p[1], p[2]
            l = math.hypot(x1 - x0, y1 - y0)
            if l <= 0:
                continue
            xm, ym, dx, dy = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
            nx, ny = -dy / l, dx / l
            L += l
            Sx += l * xm
            Sy += l * ym
            Ixx += t * l * (ym * ym + dy * dy / 12.0) + k3 * l * ny * ny
            Iyy += t * l * (xm * xm + dx * dx / 12.0) + k3 * l * nx * nx
            Ixy += t * l * (xm * ym + dx * dy / 12.0) + k3 * l * nx * ny
        else:
            _, cx, cy, R, a, b = p
            s = 1.0 if b > a else -1.0
            D = b - a
            ic = math.sin(b) - math.sin(a)
            is_ = -math.cos(b) + math.cos(a)
            icc = D / 2 + (math.sin(2 * b) - math.sin(2 * a)) / 4
            iss = D / 2 - (math.sin(2 * b) - math.sin(2 * a)) / 4
            isc = (math.sin(b) ** 2 - math.sin(a) ** 2) / 2
            f = R * s
            l = abs(D) * R
            L += l
            Sx += f * (cx * D + R * ic)
            Sy += f * (cy * D + R * is_)
            Ixx += t * f * (cy * cy * D + 2 * cy * R * is_ + R * R * iss) \
                + k3 * f * iss
            Iyy += t * f * (cx * cx * D + 2 * cx * R * ic + R * R * icc) \
                + k3 * f * icc
            Ixy += t * f * (cx * cy * D + cx * R * is_ + cy * R * ic
                            + R * R * isc) + k3 * f * isc
    return {"L": L, "Sx": Sx, "Sy": Sy, "Ixx": Ixx, "Iyy": Iyy, "Ixy": Ixy}


def nodes(pcs, n_line: int = 1) -> List[Tuple[float, float]]:
    """Discretised centre line (arcs ARC_CHORDS chords)."""
    out: List[Tuple[float, float]] = []
    for p in pcs:
        if p[0] == "line":
            (x0, y0), (x1, y1) = p[1], p[2]
            seg = [(x0 + (x1 - x0) * k / n_line, y0 + (y1 - y0) * k / n_line)
                   for k in range(n_line + 1)]
        else:
            _, cx, cy, R, a, b = p
            seg = [(cx + R * math.cos(a + (b - a) * k / ARC_CHORDS),
                    cy + R * math.sin(a + (b - a) * k / ARC_CHORDS))
                   for k in range(ARC_CHORDS + 1)]
        for q in seg:
            if not out or math.hypot(q[0] - out[-1][0],
                                     q[1] - out[-1][1]) > 1e-15:
                out.append(q)
    return out


def _sectorial(nd, t, xc, yc, Ixx, Iyy, Ixy):
    """(xs, ys, Cw) relative to the centroid (thin-walled, Vlasov)."""
    X = [p[0] - xc for p in nd]
    Y = [p[1] - yc for p in nd]
    w = [0.0]
    for i in range(len(nd) - 1):
        w.append(w[-1] + X[i] * Y[i + 1] - X[i + 1] * Y[i])
    Iwx = Iwy = 0.0
    for i in range(len(nd) - 1):
        l = math.hypot(X[i + 1] - X[i], Y[i + 1] - Y[i])
        a = t * l / 6.0
        Iwx += a * (2 * X[i] * w[i] + X[i] * w[i + 1] + X[i + 1] * w[i]
                    + 2 * X[i + 1] * w[i + 1])
        Iwy += a * (2 * Y[i] * w[i] + Y[i] * w[i + 1] + Y[i + 1] * w[i]
                    + 2 * Y[i + 1] * w[i + 1])
    # w_S = w + ys x - xs y ; int w_S x dA = int w_S y dA = 0
    det = (-Ixy) * Ixy - Iyy * (-Ixx)
    xs = ((-Iwx) * Ixy - Iyy * (-Iwy)) / det
    ys = ((-Ixy) * (-Iwy) - (-Ixx) * (-Iwx)) / det
    ws = [w[i] + ys * X[i] - xs * Y[i] for i in range(len(nd))]
    A = sum(t * math.hypot(X[i + 1] - X[i], Y[i + 1] - Y[i])
            for i in range(len(nd) - 1))
    wm = sum(t * math.hypot(X[i + 1] - X[i], Y[i + 1] - Y[i])
             * (ws[i] + ws[i + 1]) / 2 for i in range(len(nd) - 1)) / A
    Cw = 0.0
    for i in range(len(nd) - 1):
        l = math.hypot(X[i + 1] - X[i], Y[i + 1] - Y[i])
        a, b = ws[i] - wm, ws[i + 1] - wm
        Cw += t * l * (a * a + a * b + b * b) / 3.0
    return xs, ys, Cw


def _plastic(nd, t, axis: int) -> float:
    """Plastic modulus (thin-walled): axis 1 -> about local 3 (uses y)."""
    segs = []
    for i in range(len(nd) - 1):
        l = math.hypot(nd[i + 1][0] - nd[i][0], nd[i + 1][1] - nd[i][1])
        segs.append((nd[i][axis], nd[i + 1][axis], t * l))
    total = sum(s[2] for s in segs)

    def below(p):
        s = 0.0
        for a, b, w in segs:
            lo, hi = min(a, b), max(a, b)
            if hi - lo < 1e-15:
                s += w if a <= p else 0.0
            else:
                s += w * min(max(p - lo, 0.0), hi - lo) / (hi - lo)
        return s
    lo = min(min(a, b) for a, b, _ in segs)
    hi = max(max(a, b) for a, b, _ in segs)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if below(mid) < total / 2:
            lo = mid
        else:
            hi = mid
    p = 0.5 * (lo + hi)
    Z = 0.0
    for a, b, w in segs:
        lo_, hi_ = min(a, b), max(a, b)
        if hi_ - lo_ < 1e-15:
            Z += w * abs(a - p)
            continue
        # int |y - p| over a uniform distribution on [lo_, hi_]
        if p <= lo_:
            m = (lo_ + hi_) / 2 - p
        elif p >= hi_:
            m = p - (lo_ + hi_) / 2
        else:
            m = ((p - lo_) ** 2 + (hi_ - p) ** 2) / (2 * (hi_ - lo_))
        Z += w * m
    return Z


def properties(typ: str, g: Dict[str, float]) -> Dict[str, float]:
    """Linear-method properties (see module doc) in placement coords."""
    t = g["t"]
    pcs = pieces(typ, g)
    m = _moments(pcs, t)
    A = t * m["L"]
    xc, yc = m["Sx"] / m["L"], m["Sy"] / m["L"]
    I33 = m["Ixx"] - A * yc * yc
    I22 = m["Iyy"] - A * xc * xc
    I23 = m["Ixy"] - A * xc * yc
    nd = nodes(pcs, 40)
    xs, ys, Cw = _sectorial(nd, t, xc, yc, I33, I22, I23)
    # exact symmetry: C about the horizontal axis, hat about the vertical
    # one, Z point-symmetric (shear centre = centroid, I23 exact)
    if typ == "C_lipped":
        ys, I23 = 0.0, 0.0
    elif typ == "hat":
        xs, I23 = 0.0, 0.0
    else:
        xs = ys = 0.0
    ol = outline(typ, g)
    xsO = [p[0] for p in ol]
    ysO = [p[1] for p in ol]
    c2 = max(max(ysO) - yc, yc - min(ysO))
    c3 = max(max(xsO) - xc, xc - min(xsO))
    return {"A": A, "I33": I33, "I22": I22, "I23": I23,
            "J": m["L"] * t ** 3 / 3.0, "Cw": Cw,
            "S33": I33 / c2, "S22": I22 / c3,
            "Z33": _plastic(nd, t, 1), "Z22": _plastic(nd, t, 0),
            "x0": xs, "y0": ys, "cx": xc, "cy": yc,
            "b": max(xsO) - min(xsO), "h": max(ysO) - min(ysO),
            "L": m["L"]}


def shear_areas(typ: str, g: Dict[str, float]) -> Tuple[float, float]:
    t = g["t"]
    if typ == "hat":
        return 2.0 * g["d"] * t, 5.0 / 6.0 * (g["b"] + 2.0 * g["bf"]) * t
    return g["d"] * t, 5.0 / 3.0 * g["b"] * t


# --------------------------------------------------------------------------- #
# outline (for the drawing and the finite-element exact option)
# --------------------------------------------------------------------------- #
def outline(typ: str, g: Dict[str, float]) -> List[Tuple[float, float]]:
    """Closed outline polygon (counter-clockwise) of the formed strip."""
    pcs = pieces(typ, g)
    nd = nodes(pcs, 1)
    h = g["t"] / 2.0
    n = len(nd)

    def offset(sign):
        out = []
        for i in range(n):
            if i == 0:
                u = _unit(nd[0], nd[1])
                nrm = (-u[1], u[0])
                out.append((nd[0][0] + sign * h * nrm[0],
                            nd[0][1] + sign * h * nrm[1]))
                continue
            if i == n - 1:
                u = _unit(nd[-2], nd[-1])
                nrm = (-u[1], u[0])
                out.append((nd[-1][0] + sign * h * nrm[0],
                            nd[-1][1] + sign * h * nrm[1]))
                continue
            u1 = _unit(nd[i - 1], nd[i])
            u2 = _unit(nd[i], nd[i + 1])
            n1 = (-u1[1], u1[0])
            n2 = (-u2[1], u2[0])
            bx, by = n1[0] + n2[0], n1[1] + n2[1]
            bn = math.hypot(bx, by)
            bx, by = bx / bn, by / bn
            c = bx * n1[0] + by * n1[1]
            out.append((nd[i][0] + sign * h * bx / c,
                        nd[i][1] + sign * h * by / c))
        return out
    left = offset(1.0)
    right = offset(-1.0)
    poly = left + list(reversed(right))
    a = sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
            - poly[(i + 1) % len(poly)][0] * poly[i][1]
            for i in range(len(poly)))
    if a < 0:
        poly.reverse()
    return poly


def regions(typ: str, g: Dict[str, float]) -> List[dict]:
    """:mod:`skyframe.core.section_fem` regions of the true outline."""
    return [{"outer": [list(p) for p in outline(typ, g)]}]


# --------------------------------------------------------------------------- #
# AISI Manual Part I closed-form formulas (independent check, lipped C)
# --------------------------------------------------------------------------- #
def aisi_lipped_c(D: float, B: float, C: float, t: float, R: float
                  ) -> Dict[str, float]:
    """AISI Cold-Formed Steel Design Manual, Part I ("Formulas for C
    section with lips", linear method, alpha = 1, rounded corners)::

        r = R + t/2, a = D - (2r + t), b = B - (2r + t), c = C - (r + t/2),
        u = 1.57 r, A = t (a + 2b + 2u + 2(c + u)),
        Ix = 2t [0.0417 a^3 + b (a/2 + r)^2 + u (a/2 + 0.637 r)^2
                 + 0.149 r^3 + 0.0833 c^3 + c/4 (a - c)^2
                 + u (a/2 + 0.637 r)^2 + 0.149 r^3]

    (the Manual's rounded constants; u is evaluated as pi/2 r).  Returns
    {A, Ix} - an independent cross-check of :func:`properties`."""
    r = R + t / 2.0
    a = D - (2.0 * r + t)
    b = B - (2.0 * r + t)
    c = C - (r + t / 2.0)
    u = math.pi / 2.0 * r
    A = t * (a + 2 * b + 2 * u + 2 * (c + u))
    Ix = 2 * t * (0.0417 * a ** 3 + b * (a / 2 + r) ** 2
                  + u * (a / 2 + 0.637 * r) ** 2 + 0.149 * r ** 3
                  + 0.0833 * c ** 3 + c / 4 * (a - c) ** 2
                  + u * (a / 2 + 0.637 * r) ** 2 + 0.149 * r ** 3)
    return {"A": A, "Ix": Ix}
