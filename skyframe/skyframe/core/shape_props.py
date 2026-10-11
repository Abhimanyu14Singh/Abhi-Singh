"""Parametric frame-section properties from dimensions (v1.21).

One function, :func:`shape_properties`, turns a section ``type`` + ``dims``
dict (SI metres) into the analysis / display properties used by
:class:`skyframe.core.model.FrameSection`.  Every value is computed in
closed form from the idealised outline; nothing is tabulated here.

Coordinates and placement (CONTRACT "Steel section database and
parametric shapes")
    x = local 3 (width, horizontal in the section picture), y = local 2
    (depth, vertical).  Each type has a STANDARD PLACEMENT, the one the
    preview (js/secpreview.js) draws:

    ``I`` / ``I_mono``  web on the vertical symmetry line, flanges top/bottom
    ``channel``         web on the LEFT (back at x = 0), flanges toward +3
    ``angle``           heel at the origin, leg ``d`` along +2, leg ``b``
                        along +3
    ``double_angle``    two angles back to back (gap ``s``) on the vertical
                        symmetry line, outstanding legs ``b`` at the TOP
    ``tee``             flange at the top, stem down
    ``tee_concrete``    flange ``bf`` x ``hf`` at the top, web ``bw`` down,
                        total depth ``h``
    ``L_concrete``      like ``tee_concrete`` but the web is flush with the
                        LEFT edge of the flange (spandrel / edge beam)

dims (all in m)
    I            d, bf, tf, tw [, r fillet - recorded, not integrated]
    I_mono       d, bf_top, tf_top, bf_bot, tf_bot, tw
    channel      d, bf, tf, tw
    angle        d, b, t
    double_angle d, b, t [, s gap = 0, orientation "LLBB"|"SLBB"|"EQUAL"]
    tee          d, bf, tf, tw
    rect_tube    d, b, t [, r_out outside corner radius = 0]
    round_tube   D, t
    rect_solid   d, b          (``plate`` is the same rectangle)
    round_solid  D
    tee_concrete bf, hf, bw, h
    L_concrete   bf, hf, bw, h

Formulas
    * A, centroid, I33 = int y^2 dA, I22 = int x^2 dA, I23 = int x y dA and
      the plastic moduli Z33 / Z22 (plastic neutral axis = equal-area axis)
      are EXACT integrals of the sharp-cornered outline (sums of
      rectangles; rounded tube corners and circles in closed form).  Rolled
      shape fillets (dims ``r``) are not integrated - that is the main
      reason tabulated values differ by ~1-3 % (CONTRACT tolerances).
    * S33 = I33 / c2max, S22 = I22 / c3max (extreme fibre from centroid).
    * J: open thin-walled sections J = sum(b t^3) / 3 over the plate
      mid-lines as in AISC Design Guide 9 (I: (2 bf tf^3 + (d - tf) tw^3)/3,
      channel: (2 (bf - tw/2) tf^3 + (d - tf) tw^3)/3, tee:
      (bf tf^3 + (d - tf/2) tw^3)/3, angle: (d - t/2 + b - t/2) t^3/3);
      rect_tube: Bredt's closed thin-walled formula J = 4 Am^2 t / pm on
      the wall mid-line (rounded corners r_out - t/2); round_tube / solid:
      exact polar moment pi (D^4 - d^4) / 32; rect_solid: Roark's
      formula (same as ``FrameSection.rectangular``, <0.2 % from the exact
      Saint-Venant series); concrete T / L: sum of Roark rectangle J for the
      split that gives the larger total (flange + web below it, or the
      full-depth web + flange overhangs - ACI 318 R22.7 practice; 5-10 %
      below the exact finite-element value, i.e. conservative-soft).
    * Cw (warping constant, informational - the elastic frame element has
      no warping DOF): I: tf bf^3 ho^2 / 24 (ho = d - tf); I_mono:
      ho^2 It Ib / (It + Ib); channel (DG9): tf b'^3 h'^2 / 12 * (3 b' tf +
      2 h' tw) / (6 b' tf + h' tw); tee: bf^3 tf^3/144 + d'^3 tw^3/36;
      angle: t^3 (b'^3 + d'^3) / 36; closed / solid sections 0.
    * Shear areas (``SHEAR_AREA_FORMULAS``): I / I_mono / channel: As2 =
      d tw, As3 = 5/3 bf tf (5/6 of both flanges); tee: As2 = d tw,
      As3 = 5/6 bf tf; angle: As2 = 5/6 d t, As3 = 5/6 b t; double angle:
      twice the angle; rect_tube: As2 = 2 d t, As3 = 2 b t; round_tube:
      A/2; rect_solid / plate: 5/6 A; round_solid: 0.9 A; concrete T / L:
      As2 = 5/6 bw h, As3 = 5/6 bf hf.
    * Shear centre (x0, y0) relative to the centroid: channel
      e = 3 b'^2 tf / (6 b' tf + h' tw) from the web mid-line (thin-walled,
      b' = bf - tw/2, h' = d - tf); tee / double angle / concrete T: the
      flange mid-thickness on the axis; angle / concrete L: the
      intersection of the leg mid-lines; I_mono: y = y_top - ho Ib/(It+Ib);
      doubly symmetric sections: the centroid.
    * Unsymmetric sections (angle, L_concrete) also return the principal
      values ``I_major`` / ``I_minor`` and ``alpha`` (rad): the angle from
      local 3 to the MAJOR principal axis, positive toward +2, in the
      standard placement.

Pure Python, no third-party dependency (``sectionproperties`` is used only
by the tests as an independent cross-check).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Rect = Tuple[float, float, float, float]          # x0, x1, y0, y1

#: every section ``type`` this module understands (steel + concrete)
SHAPE_TYPES = ("I", "I_mono", "channel", "angle", "double_angle", "tee",
               "rect_tube", "round_tube", "rect_solid", "round_solid",
               "plate", "tee_concrete", "L_concrete")

#: required / optional dimension keys per type
DIM_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "I": (("d", "bf", "tf", "tw"), ("r",)),
    "I_mono": (("d", "bf_top", "tf_top", "bf_bot", "tf_bot", "tw"), ()),
    "channel": (("d", "bf", "tf", "tw"), ("r",)),
    "angle": (("d", "b", "t"), ("r",)),
    "double_angle": (("d", "b", "t"), ("s",)),
    "tee": (("d", "bf", "tf", "tw"), ("r",)),
    "rect_tube": (("d", "b", "t"), ("r_out",)),
    "round_tube": (("D", "t"), ()),
    "rect_solid": (("d", "b"), ()),
    "plate": (("d", "b"), ()),
    "round_solid": (("D",), ()),
    "tee_concrete": (("bf", "hf", "bw", "h"), ()),
    "L_concrete": (("bf", "hf", "bw", "h"), ()),
}

#: human-readable shear-area rules (documented in CONTRACT / SECTION_SOURCES)
SHEAR_AREA_FORMULAS = {
    "I": "As2 = d*tw; As3 = 5/3*bf*tf",
    "I_mono": "As2 = d*tw; As3 = 5/6*(bf_top*tf_top + bf_bot*tf_bot)",
    "channel": "As2 = d*tw; As3 = 5/3*bf*tf",
    "tee": "As2 = d*tw; As3 = 5/6*bf*tf",
    "angle": "As2 = 5/6*d*t; As3 = 5/6*b*t",
    "double_angle": "As2 = 5/3*d*t; As3 = 5/3*b*t",
    "rect_tube": "As2 = 2*d*t; As3 = 2*b*t",
    "round_tube": "As2 = As3 = A/2",
    "rect_solid": "As2 = As3 = 5/6*A",
    "plate": "As2 = As3 = 5/6*A",
    "round_solid": "As2 = As3 = 0.9*A",
    "tee_concrete": "As2 = 5/6*bw*h; As3 = 5/6*bf*hf",
    "L_concrete": "As2 = 5/6*bw*h; As3 = 5/6*bf*hf",
}


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _num(dims: dict, key: str, typ: str, *, optional: bool = False,
         allow_zero: bool = False) -> float:
    v = dims.get(key)
    if v is None and optional:
        return 0.0
    if isinstance(v, bool) or not isinstance(v, (int, float)) \
            or not math.isfinite(v):
        raise ValueError(f"{typ}: dimension {key!r} must be a finite number "
                         f"(got {v!r})")
    v = float(v)
    if v < 0.0 or (v == 0.0 and not (optional or allow_zero)):
        raise ValueError(f"{typ}: dimension {key!r} must be > 0 (got {v!r})")
    return v


def validate_dims(typ: str, dims: dict) -> Dict[str, float]:
    """Checked float dims (required > 0, optional >= 0) + geometry rules.

    Raises ``ValueError`` with a plain-language message."""
    if typ not in DIM_KEYS:
        raise ValueError(f"unknown section type {typ!r}; expected one of "
                         f"{list(SHAPE_TYPES)}")
    if not isinstance(dims, dict):
        raise ValueError(f"{typ}: dims must be an object")
    req, opt = DIM_KEYS[typ]
    out = {k: _num(dims, k, typ) for k in req}
    for k in opt:
        if dims.get(k) is not None:
            out[k] = _num(dims, k, typ, optional=True)
    g = out.get
    bad = None
    if typ in ("I", "channel", "tee"):
        nf = 1 if typ == "tee" else 2
        if nf * g("tf") >= g("d"):
            bad = "the flanges are thicker than the depth"
        elif g("tw") >= g("bf"):
            bad = "the web is wider than the flange"
    elif typ == "I_mono":
        if g("tf_top") + g("tf_bot") >= g("d"):
            bad = "the flanges are thicker than the depth"
        elif g("tw") >= min(g("bf_top"), g("bf_bot")):
            bad = "the web is wider than a flange"
    elif typ in ("angle", "double_angle"):
        if g("t") >= min(g("d"), g("b")):
            bad = "the thickness must be smaller than both legs"
    elif typ == "rect_tube":
        if 2 * g("t") >= min(g("d"), g("b")):
            bad = "the wall is thicker than half the tube"
        elif g("r_out", 0.0) > min(g("d"), g("b")) / 2:
            bad = "the corner radius exceeds half the tube"
    elif typ == "round_tube":
        if 2 * g("t") >= g("D"):
            bad = "the wall is thicker than the radius"
    elif typ in ("tee_concrete", "L_concrete"):
        if g("hf") >= g("h"):
            bad = "the flange is deeper than the whole section"
        elif g("bw") > g("bf"):
            bad = "the web is wider than the flange"
    if bad:
        raise ValueError(f"{typ}: {bad} ({_fmt(out)})")
    return out


def _fmt(d: dict) -> str:
    return ", ".join(f"{k}={v:g}" for k, v in d.items())


# --------------------------------------------------------------------------- #
# exact integrals of a set of axis-aligned rectangles
# --------------------------------------------------------------------------- #
def _rect_set(rects: Sequence[Rect]) -> dict:
    A = Sx = Sy = 0.0
    for x0, x1, y0, y1 in rects:
        a = (x1 - x0) * (y1 - y0)
        A += a
        Sx += a * (x0 + x1) / 2
        Sy += a * (y0 + y1) / 2
    cx, cy = Sx / A, Sy / A
    I33 = I22 = I23 = 0.0
    for x0, x1, y0, y1 in rects:
        w, h = x1 - x0, y1 - y0
        a = w * h
        dx, dy = (x0 + x1) / 2 - cx, (y0 + y1) / 2 - cy
        I33 += w * h ** 3 / 12 + a * dy * dy
        I22 += h * w ** 3 / 12 + a * dx * dx
        I23 += a * dx * dy
    xs = [v for r in rects for v in r[:2]]
    ys = [v for r in rects for v in r[2:]]
    return {"A": A, "cx": cx, "cy": cy, "I33": I33, "I22": I22, "I23": I23,
            "c2": max(max(ys) - cy, cy - min(ys)),
            "c3": max(max(xs) - cx, cx - min(xs)),
            "Z33": _plastic(rects, 2), "Z22": _plastic(rects, 0),
            "xmin": min(xs), "xmax": max(xs), "ymin": min(ys),
            "ymax": max(ys)}


def _plastic(rects: Sequence[Rect], k: int) -> float:
    """Plastic modulus about the equal-area axis normal to coordinate k
    (k = 2: y, i.e. bending about local 3; k = 0: x, about local 2)."""
    # spans: (lo, hi, width perpendicular)
    if k == 0:
        spans = [(r[0], r[1], r[3] - r[2]) for r in rects]
    else:
        spans = [(r[2], r[3], r[1] - r[0]) for r in rects]
    total = sum((hi - lo) * w for lo, hi, w in spans)

    def below(p: float) -> float:
        return sum(w * min(max(p - lo, 0.0), hi - lo) for lo, hi, w in spans)

    # area-below is piecewise linear: locate the breakpoint interval exactly
    pts = sorted({v for lo, hi, _ in spans for v in (lo, hi)})
    p = pts[-1]
    for a, b in zip(pts, pts[1:]):
        fa, fb = below(a), below(b)
        if fb >= total / 2:
            p = a + (total / 2 - fa) / (fb - fa) * (b - a) if fb > fa else a
            break
    Z = 0.0
    for lo, hi, w in spans:
        if p <= lo:
            Z += w * ((hi - p) ** 2 - (lo - p) ** 2) / 2
        elif p >= hi:
            Z += w * ((p - lo) ** 2 - (p - hi) ** 2) / 2
        else:
            Z += w * ((p - lo) ** 2 + (hi - p) ** 2) / 2
    return Z


def _roark_J(b: float, h: float) -> float:
    """Roark rectangle torsion constant (== FrameSection.rectangular)."""
    a, c = max(b, h) / 2.0, min(b, h) / 2.0
    return a * c ** 3 * (16.0 / 3.0 - 3.36 * (c / a)
                         * (1.0 - c ** 4 / (12.0 * a ** 4)))


def rect_J_exact(b: float, h: float, terms: int = 50) -> float:
    """Exact Saint-Venant torsion constant of a solid rectangle (series)."""
    a, t = max(b, h), min(b, h)
    s = sum(math.tanh(n * math.pi * a / (2 * t)) / n ** 5
            for n in range(1, 2 * terms, 2))
    return a * t ** 3 * (1.0 / 3.0 - 64.0 / math.pi ** 5 * (t / a) * s)


# --------------------------------------------------------------------------- #
# rounded rectangle (rect_tube) closed forms
# --------------------------------------------------------------------------- #
def _round_rect(b: float, h: float, r: float) -> Tuple[float, float, float,
                                                       float, float]:
    """(A, I33, I22, Z33, Z22) of a b x h rectangle (b along 3, h along 2)
    with all four corners rounded to radius r, about its centre."""
    sp_a = (1.0 - math.pi / 4.0) * r * r                 # one spandrel
    sp_m = r ** 3 * (5.0 / 6.0 - math.pi / 4.0)          # int u dA (u from corner)
    sp_i = r ** 4 * (1.0 - 5.0 * math.pi / 16.0)         # int u^2 dA

    def corner_I(half: float) -> float:   # 4 spandrels about the centre line
        return 4.0 * (half * half * sp_a - 2.0 * half * sp_m + sp_i)

    A = b * h - 4.0 * sp_a
    I33 = b * h ** 3 / 12.0 - corner_I(h / 2.0)
    I22 = h * b ** 3 / 12.0 - corner_I(b / 2.0)

    def Z(bb: float, hh: float) -> float:
        return (bb * hh * hh / 4.0
                - 4.0 * (hh / 2.0 - r) * (1.0 - math.pi / 4.0) * r * r
                - 2.0 / 3.0 * r ** 3)
    return A, I33, I22, Z(b, h), Z(h, b)


# --------------------------------------------------------------------------- #
# per-type geometry
# --------------------------------------------------------------------------- #
def _rects(typ: str, g: Dict[str, float]) -> List[Rect]:
    if typ == "I":
        d, bf, tf, tw = g["d"], g["bf"], g["tf"], g["tw"]
        return [(-bf / 2, bf / 2, 0.0, tf), (-tw / 2, tw / 2, tf, d - tf),
                (-bf / 2, bf / 2, d - tf, d)]
    if typ == "I_mono":
        d, tw = g["d"], g["tw"]
        bt, tt, bb, tb = g["bf_top"], g["tf_top"], g["bf_bot"], g["tf_bot"]
        return [(-bb / 2, bb / 2, 0.0, tb), (-tw / 2, tw / 2, tb, d - tt),
                (-bt / 2, bt / 2, d - tt, d)]
    if typ == "channel":
        d, bf, tf, tw = g["d"], g["bf"], g["tf"], g["tw"]
        return [(0.0, bf, 0.0, tf), (0.0, tw, tf, d - tf),
                (0.0, bf, d - tf, d)]
    if typ == "angle":
        d, b, t = g["d"], g["b"], g["t"]
        return [(0.0, b, 0.0, t), (0.0, t, t, d)]
    if typ == "double_angle":
        d, b, t, s = g["d"], g["b"], g["t"], g.get("s", 0.0)
        out: List[Rect] = []
        for sg in (-1.0, 1.0):
            xa, xb = sg * s / 2, sg * (s / 2 + b)
            xw = sg * (s / 2 + t)
            out.append((min(xa, xb), max(xa, xb), d - t, d))       # outstanding
            out.append((min(xa, xw), max(xa, xw), 0.0, d - t))     # b-to-b leg
        return out
    if typ == "tee":
        d, bf, tf, tw = g["d"], g["bf"], g["tf"], g["tw"]
        return [(-tw / 2, tw / 2, 0.0, d - tf), (-bf / 2, bf / 2, d - tf, d)]
    if typ in ("rect_solid", "plate"):
        return [(-g["b"] / 2, g["b"] / 2, -g["d"] / 2, g["d"] / 2)]
    if typ == "tee_concrete":
        bf, hf, bw, h = g["bf"], g["hf"], g["bw"], g["h"]
        return [(-bw / 2, bw / 2, 0.0, h - hf), (-bf / 2, bf / 2, h - hf, h)]
    if typ == "L_concrete":
        bf, hf, bw, h = g["bf"], g["hf"], g["bw"], g["h"]
        return [(0.0, bw, 0.0, h - hf), (0.0, bf, h - hf, h)]
    raise ValueError(typ)


def shape_properties(typ: str, dims: dict) -> dict:
    """Section properties (SI) of a parametric shape; see module docstring.

    Returns ``{A, I33, I22, I23, J, Cw, S33, S22, Z33, Z22, As2, As3, r33,
    r22, x0, y0, cx, cy, b, h}`` plus ``I_major, I_minor, alpha`` for the
    unsymmetric types.  ``b`` / ``h`` are the outline width / depth used
    for drawing (FrameSection.b / h)."""
    g = validate_dims(typ, dims)
    if typ == "round_tube":
        D, t = g["D"], g["t"]
        di = D - 2 * t
        A = math.pi * (D * D - di * di) / 4.0
        I = math.pi * (D ** 4 - di ** 4) / 64.0
        Z = (D ** 3 - di ** 3) / 6.0
        return _finish(typ, g, A, I, I, 0.0, 2.0 * I, 0.0, I / (D / 2),
                       I / (D / 2), Z, Z, 0.0, 0.0, D / 2, D / 2, D, D)
    if typ == "round_solid":
        D = g["D"]
        A = math.pi * D * D / 4.0
        I = math.pi * D ** 4 / 64.0
        return _finish(typ, g, A, I, I, 0.0, 2.0 * I, 0.0, I / (D / 2),
                       I / (D / 2), D ** 3 / 6.0, D ** 3 / 6.0, 0.0, 0.0,
                       D / 2, D / 2, D, D)
    if typ == "rect_tube":
        d, b, t, ro = g["d"], g["b"], g["t"], g.get("r_out", 0.0)
        ri = max(ro - t, 0.0)
        Ao, I33o, I22o, Z33o, Z22o = _round_rect(b, d, ro)
        Ai, I33i, I22i, Z33i, Z22i = _round_rect(b - 2 * t, d - 2 * t, ri)
        A, I33, I22 = Ao - Ai, I33o - I33i, I22o - I22i
        rm = max(ro - t / 2, 0.0)
        bm, dm = b - t, d - t
        Am = bm * dm - (4.0 - math.pi) * rm * rm
        pm = 2.0 * (bm + dm) - (8.0 - 2.0 * math.pi) * rm
        J = 4.0 * Am * Am * t / pm
        return _finish(typ, g, A, I33, I22, 0.0, J, 0.0, I33 / (d / 2),
                       I22 / (b / 2), Z33o - Z33i, Z22o - Z22i, 0.0, 0.0,
                       b / 2, d / 2, b, d)
    p = _rect_set(_rects(typ, g))
    cx, cy = p["cx"], p["cy"]
    J, Cw, sc = _torsion(typ, g, cx, cy)
    return _finish(typ, g, p["A"], p["I33"], p["I22"], p["I23"], J, Cw,
                   p["I33"] / p["c2"], p["I22"] / p["c3"], p["Z33"],
                   p["Z22"], sc[0] - cx, sc[1] - cy, cx, cy,
                   p["xmax"] - p["xmin"], p["ymax"] - p["ymin"])


def _torsion(typ: str, g: Dict[str, float], cx: float, cy: float
             ) -> Tuple[float, float, Tuple[float, float]]:
    """(J, Cw, shear centre (x, y) in placement coordinates)."""
    if typ == "I":
        d, bf, tf, tw = g["d"], g["bf"], g["tf"], g["tw"]
        ho = d - tf
        return ((2 * bf * tf ** 3 + ho * tw ** 3) / 3.0,
                tf * bf ** 3 * ho * ho / 24.0, (cx, cy))
    if typ == "I_mono":
        d, tw = g["d"], g["tw"]
        bt, tt, bb, tb = g["bf_top"], g["tf_top"], g["bf_bot"], g["tf_bot"]
        ho = d - (tt + tb) / 2
        It, Ib = tt * bt ** 3 / 12.0, tb * bb ** 3 / 12.0
        J = (bt * tt ** 3 + bb * tb ** 3 + ho * tw ** 3) / 3.0
        return J, ho * ho * It * Ib / (It + Ib), \
            (cx, d - tt / 2 - ho * Ib / (It + Ib))
    if typ == "channel":
        d, bf, tf, tw = g["d"], g["bf"], g["tf"], g["tw"]
        bp, hp = bf - tw / 2, d - tf
        J = (2 * bp * tf ** 3 + hp * tw ** 3) / 3.0
        Cw = (tf * bp ** 3 * hp * hp / 12.0 * (3 * bp * tf + 2 * hp * tw)
              / (6 * bp * tf + hp * tw))
        e = 3 * bp * bp * tf / (6 * bp * tf + hp * tw)
        return J, Cw, (tw / 2 - e, cy)
    if typ == "tee":
        d, bf, tf, tw = g["d"], g["bf"], g["tf"], g["tw"]
        dp = d - tf / 2
        return ((bf * tf ** 3 + dp * tw ** 3) / 3.0,
                bf ** 3 * tf ** 3 / 144.0 + dp ** 3 * tw ** 3 / 36.0,
                (cx, d - tf / 2))
    if typ == "angle":
        d, b, t = g["d"], g["b"], g["t"]
        dp, bp = d - t / 2, b - t / 2
        return ((dp + bp) * t ** 3 / 3.0, t ** 3 * (bp ** 3 + dp ** 3) / 36.0,
                (t / 2, t / 2))
    if typ == "double_angle":
        d, b, t = g["d"], g["b"], g["t"]
        dp, bp = d - t / 2, b - t / 2
        return 2.0 * (dp + bp) * t ** 3 / 3.0, 0.0, (cx, d - t / 2)
    if typ in ("rect_solid", "plate"):
        return _roark_J(g["b"], g["d"]), 0.0, (cx, cy)
    if typ in ("tee_concrete", "L_concrete"):
        bf, hf, bw, h = g["bf"], g["hf"], g["bw"], g["h"]
        nov = 2 if typ == "tee_concrete" else 1          # flange overhangs
        ov = (bf - bw) / nov
        J = max(_roark_J(bf, hf) + _roark_J(bw, h - hf),          # split A
                _roark_J(bw, h) + (nov * _roark_J(ov, hf)          # split B
                                   if ov > 0 else 0.0))
        sc = (cx if typ == "tee_concrete" else bw / 2, h - hf / 2)
        return J, 0.0, sc
    raise ValueError(typ)


def shear_areas(typ: str, g: Dict[str, float], A: float
                ) -> Tuple[float, float]:
    """(As2, As3) per ``SHEAR_AREA_FORMULAS`` (dims already validated)."""
    if typ == "I" or typ == "channel":
        return g["d"] * g["tw"], 5.0 / 3.0 * g["bf"] * g["tf"]
    if typ == "I_mono":
        return g["d"] * g["tw"], 5.0 / 6.0 * (g["bf_top"] * g["tf_top"]
                                              + g["bf_bot"] * g["tf_bot"])
    if typ == "tee":
        return g["d"] * g["tw"], 5.0 / 6.0 * g["bf"] * g["tf"]
    if typ == "angle":
        return 5.0 / 6.0 * g["d"] * g["t"], 5.0 / 6.0 * g["b"] * g["t"]
    if typ == "double_angle":
        return 5.0 / 3.0 * g["d"] * g["t"], 5.0 / 3.0 * g["b"] * g["t"]
    if typ == "rect_tube":
        return 2.0 * g["d"] * g["t"], 2.0 * g["b"] * g["t"]
    if typ == "round_tube":
        return A / 2.0, A / 2.0
    if typ in ("rect_solid", "plate"):
        return 5.0 / 6.0 * A, 5.0 / 6.0 * A
    if typ == "round_solid":
        return 0.9 * A, 0.9 * A
    if typ in ("tee_concrete", "L_concrete"):
        return 5.0 / 6.0 * g["bw"] * g["h"], 5.0 / 6.0 * g["bf"] * g["hf"]
    raise ValueError(typ)


def principal(I33: float, I22: float, I23: float) -> Tuple[float, float,
                                                           float]:
    """(I_major, I_minor, alpha): alpha = angle (rad) from local 3 to the
    major principal axis, positive toward +2 (I_u = I33 cos^2 + I22 sin^2
    - 2 I23 sin cos for an axis at angle u)."""
    c = (I33 + I22) / 2.0
    r = math.hypot((I33 - I22) / 2.0, I23)
    alpha = 0.5 * math.atan2(-2.0 * I23, I33 - I22)
    return c + r, c - r, alpha


def _finish(typ, g, A, I33, I22, I23, J, Cw, S33, S22, Z33, Z22, x0, y0,
            cx, cy, b, h) -> dict:
    As2, As3 = shear_areas(typ, g, A)
    out = {"A": A, "I33": I33, "I22": I22, "I23": I23, "J": J, "Cw": Cw,
           "S33": S33, "S22": S22, "Z33": Z33, "Z22": Z22,
           "As2": As2, "As3": As3, "r33": math.sqrt(I33 / A),
           "r22": math.sqrt(I22 / A), "x0": x0, "y0": y0, "cx": cx,
           "cy": cy, "b": b, "h": h}
    if typ in ("angle", "L_concrete"):
        out["I_major"], out["I_minor"], out["alpha"] = principal(I33, I22,
                                                                 I23)
    return out


def section_kwargs(typ: str, dims: dict) -> dict:
    """FrameSection keyword values (A, I33, I22, J, b, h, As2, As3) for a
    parametric shape."""
    p = shape_properties(typ, dims)
    return {k: p[k] for k in ("A", "I33", "I22", "J", "b", "h", "As2",
                              "As3")}


def principal_of(typ: str, dims: dict) -> Optional[dict]:
    """``{"I_major", "I_minor", "alpha"}`` for unsymmetric types, else None."""
    if typ not in ("angle", "L_concrete"):
        return None
    p = shape_properties(typ, dims)
    return {"I_major": p["I_major"], "I_minor": p["I_minor"],
            "alpha": p["alpha"]}
