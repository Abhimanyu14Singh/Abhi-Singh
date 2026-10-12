"""Slab property types (ribbed / waffle / drop / deck), section-level load
distribution and drop panels (Wave B).

CONTRACT "Orthotropic materials, slab / deck property types, cold-formed and
composite sections, named line / area springs".

``ShellSection.slab`` (None = the plain slab / wall, byte-identical)::

    {"type": "ribbed", "overall_depth": h, "slab_thickness": hf,
     "stem_width_top": wt, "stem_width_bottom": wb, "rib_spacing": s,
     "rib_dir": 1 | 2}
    {"type": "waffle", "overall_depth", "slab_thickness", "stem_width_top",
     "stem_width_bottom", "rib_spacing_1", "rib_spacing_2"}
    {"type": "deck_filled", "slab_depth": tc, "rib_depth": hr,
     "rib_width_top": wrt, "rib_width_bottom": wrb, "rib_spacing": sr,
     "deck_thickness": td, "deck_unit_weight": wd, "rib_dir": 1 | 2}
    {"type": "deck_unfilled", "rib_depth", "rib_width_top",
     "rib_width_bottom", "rib_spacing", "deck_thickness",
     "deck_unit_weight", "rib_dir"}
    {"type": "deck_solid", "slab_depth": t [, "rib_dir"]}
    {"type": "drop"}

All lengths in m, ``deck_unit_weight`` in kN/m^2.  ``rib_dir`` = the local
axis the ribs (deck flutes = the deck span) run along.  ``rib_spacing_1``
of a waffle is measured ALONG local 1 (between the ribs parallel to local
2), ``rib_spacing_2`` along local 2.

Equivalent plate (per unit width, rib direction = "1" below, swapped for
``rib_dir`` 2; section material E, nu, G = E / 2(1 + nu), isotropic):

* T-strip of one pitch s: flange s x hf on top of a trapezoidal stem
  (top width wt, bottom width wb, depth hr = h - hf).  ``Ar = (wt + wb) / 2
  hr``; ``I_T`` = moment of inertia of the T about ITS OWN centroid
  (exact trapezoid: ``hr^3 (wb^2 + 4 wb wt + wt^2) / 36 (wb + wt)`` plus
  parallel-axis terms).
* ribbed: ``A = hf Q + E Ar / s e1 e1``, ``D11 = E I_T / s``, ``D22 =
  E hf^3 / 12 (1 - nu^2)``, ``D12 = nu D22``, ``D66 = G hf^3 / 12`` (flange
  twisting only), ``S13 = 5/6 G (hf + Ar / s)``, ``S23 = 5/6 G hf``.  Each
  direction is taken about its own centroid (the eccentric-rib
  membrane-bending coupling is not modelled).
* waffle: both directions: ``A11 = Q11 hf + E Ar / s2``, ``A22 = Q22 hf +
  E Ar / s1``, ``D11 = E I_T(s2) / s2``, ``D22 = E I_T(s1) / s1``, ``D12 =
  nu E hf^3 / 12 (1 - nu^2)``, ``D66 = G hf^3 / 12``.
* deck_filled: the ribbed rule with the concrete above the flutes as the
  flange (tc) and the concrete in the flutes as stems (wrt at the top,
  wrb at the bottom) - the steel deck is not counted in the stiffness.
* deck_unfilled (steel sheet td, material = the deck steel): along the
  flutes ``A11 = E td Ld / sr`` (developed length per pitch ``Ld = (sr -
  wrt) + wrb + 2 sqrt(hr^2 + ((wrt - wrb) / 2)^2)``), across them the
  corrugated-sheet value ``A22 = E td^3 / 6 hr^2``, in-plane shear ``A66 =
  G td`` (ETABS "deck shear thickness"), no Poisson coupling; ``D11 = E
  I_deck / sr`` (thin-walled profile about its centroid), ``D22 = E td^3 /
  12``, ``D66 = G td^3 / 12``, ``S13 = S23 = 5/6 G td``.
* deck_solid / drop: plain isotropic slab of the section thickness.

Self-weight / mass: ``ShellSection.thickness`` of these types is the
WEIGHT-EQUIVALENT thickness of the section material (every self-weight /
mass path multiplies thickness x unit weight): ribbed ``hf + Ar / s``,
waffle ``hf + Ar / s1 + Ar / s2 - wbar^2 hr / (s1 s2)``, filled deck ``tc
+ Ar / sr + wd / gamma_c``, unfilled deck ``wd / gamma_s``, solid deck
``tc``.  :func:`sync_thickness` keeps it equal to the geometry (only
rewritten when it differs by more than 1e-9 relative).

Section-level one-way distribution (``ShellSection.distribution`` /
``one_way_dir``, None = unset): every MEMBRANE slab using the section
inherits it unless the slab carries its own value - a region whose
``distribution`` / ``one_way_dir`` differ from the defaults, or whose
``distribution_override`` is True, keeps its own value (the per-slab
override).  An inherited one-way on a slab that cannot take it (polygon /
non-convex / shell behaviour) falls back to two-way with a warning.

Drop panels: a slab region whose section has ``slab.type == "drop"`` is a
THICKNESS OVERRIDE region.  It is not meshed by itself: every shell element
of a coplanar SHELL-behaviour slab whose centroid lies inside the drop
outline takes the drop region (its section stiffness, self-weight, mass and
area loads) - the boundary snaps to the host mesh like an opening.  The
host's ``net_area`` excludes the exact geometric overlap and the drop
region's ``net_area`` is that overlap, so region-level weight / mass totals
count the drop zone once, at the drop thickness.  Area loads assigned to a
host do not reach its overridden elements (assign them to the drop panel
too, as in ETABS); the drop outline must be convex.
"""

from __future__ import annotations

import math
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np

SLAB_TYPES = ("ribbed", "waffle", "deck_filled", "deck_unfilled",
              "deck_solid", "drop")
SLAB_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "ribbed": (("overall_depth", "slab_thickness", "stem_width_top",
                "stem_width_bottom", "rib_spacing"), ("rib_dir",)),
    "waffle": (("overall_depth", "slab_thickness", "stem_width_top",
                "stem_width_bottom", "rib_spacing_1", "rib_spacing_2"), ()),
    "deck_filled": (("slab_depth", "rib_depth", "rib_width_top",
                     "rib_width_bottom", "rib_spacing", "deck_thickness",
                     "deck_unit_weight"), ("rib_dir",)),
    "deck_unfilled": (("rib_depth", "rib_width_top", "rib_width_bottom",
                       "rib_spacing", "deck_thickness", "deck_unit_weight"),
                      ("rib_dir",)),
    "deck_solid": (("slab_depth",), ("rib_dir",)),
    "drop": ((), ()),
}
#: types whose elastic stiffness is the equivalent orthotropic plate
STIFF_TYPES = ("ribbed", "waffle", "deck_filled", "deck_unfilled")
DECK_TYPES = ("deck_filled", "deck_unfilled", "deck_solid")
DISTRIBUTIONS = ("two_way", "one_way")
_REL = 1e-9


def _finite(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def slab_type(ssec) -> Optional[str]:
    s = getattr(ssec, "slab", None)
    return s.get("type") if isinstance(s, dict) else None


# --------------------------------------------------------------------------- #
# validation / normalisation
# --------------------------------------------------------------------------- #
def normalize_slab(s) -> Optional[dict]:
    if s is None:
        return None
    out = {"type": str(s.get("type"))}
    for k, v in s.items():
        if k == "type":
            continue
        out[k] = int(v) if k == "rib_dir" else float(v)
    return out


def validate_slab(ssec, mat) -> None:
    s = getattr(ssec, "slab", None)
    if s is None:
        return
    nm = ssec.name
    if not isinstance(s, dict) or s.get("type") not in SLAB_TYPES:
        raise ValueError(f"Shell section {nm}: slab.type must be one of "
                         f"{list(SLAB_TYPES)}")
    typ = s["type"]
    req, opt = SLAB_KEYS[typ]
    unknown = set(s) - {"type"} - set(req) - set(opt)
    if unknown:
        raise ValueError(f"Shell section {nm}: unknown slab key(s) "
                         f"{sorted(unknown)} for type {typ!r} (allowed: "
                         f"{list(req + opt)})")
    for k in req:
        v = s.get(k)
        if not (_finite(v) and v > 0.0):
            raise ValueError(f"Shell section {nm}: slab.{k} must be a finite "
                             f"value > 0 (got {v!r})")
    if s.get("rib_dir", 1) not in (1, 2):
        raise ValueError(f"Shell section {nm}: slab.rib_dir must be 1 or 2")
    g = s.get
    bad = ""
    if typ in ("ribbed", "waffle"):
        if g("slab_thickness") >= g("overall_depth"):
            bad = "slab_thickness must be smaller than overall_depth"
        spac = ([g("rib_spacing")] if typ == "ribbed"
                else [g("rib_spacing_1"), g("rib_spacing_2")])
        if max(g("stem_width_top"), g("stem_width_bottom")) >= min(spac):
            bad = "the stems are wider than the rib spacing"
    elif typ in ("deck_filled", "deck_unfilled"):
        if max(g("rib_width_top"), g("rib_width_bottom")) > g("rib_spacing"):
            bad = "the rib widths exceed the rib spacing"
        if g("deck_thickness") >= g("rib_depth"):
            bad = "deck_thickness must be smaller than rib_depth"
    if bad:
        raise ValueError(f"Shell section {nm}: {bad}")
    if typ != "drop" and getattr(ssec, "layered", None):
        raise ValueError(f"Shell section {nm}: a {typ} slab cannot be "
                         "layered")
    if getattr(ssec, "laminate", None) is not None:
        raise ValueError(f"Shell section {nm}: slab types and laminate are "
                         "exclusive")
    if typ in STIFF_TYPES + ("deck_solid",) and mat is not None:
        if getattr(mat, "symmetry", "isotropic") == "orthotropic":
            raise ValueError(f"Shell section {nm}: a {typ} section needs an "
                             "isotropic material")
        if not mat.unit_weight > 0.0 and typ in ("deck_filled",
                                                  "deck_unfilled"):
            raise ValueError(f"Shell section {nm}: the deck material needs "
                             "a unit weight > 0")
    if mat is not None and typ != "drop":
        t = weight_thickness(ssec, mat)
        if abs(ssec.thickness - t) > 1e-6 * t:
            raise ValueError(f"Shell section {nm}: thickness must be the "
                             f"weight-equivalent {t:.6g} m of the {typ} "
                             f"geometry (got {ssec.thickness!r})")


def validate_distribution(ssec) -> None:
    d = getattr(ssec, "distribution", None)
    if d is not None and d not in DISTRIBUTIONS:
        raise ValueError(f"Shell section {ssec.name}: distribution must be "
                         f"two_way|one_way (got {d!r})")
    w = getattr(ssec, "one_way_dir", None)
    if w is not None and w not in (1, 2):
        raise ValueError(f"Shell section {ssec.name}: one_way_dir must be 1 "
                         f"or 2 (got {w!r})")


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
def _trap(wt: float, wb: float, hr: float) -> Tuple[float, float, float]:
    """(area, centroid from the bottom, I about own centroid) of a
    trapezoid of top width wt, bottom width wb, depth hr."""
    a = 0.5 * (wt + wb) * hr
    yc = hr * (wb + 2.0 * wt) / (3.0 * (wb + wt))
    i = hr ** 3 * (wb * wb + 4.0 * wb * wt + wt * wt) / (36.0 * (wb + wt))
    return a, yc, i


def tee_strip(s: float, hf: float, wt: float, wb: float, hr: float
              ) -> Dict[str, float]:
    """One pitch of a ribbed plate: {Ar, A, ybar (from bottom), I} with I
    the moment of inertia of the T about its own centroid."""
    ar, yr, ir = _trap(wt, wb, hr)
    af = s * hf
    yf = hr + hf / 2.0
    A = af + ar
    yb = (af * yf + ar * yr) / A
    I = s * hf ** 3 / 12.0 + af * (yf - yb) ** 2 + ir + ar * (yr - yb) ** 2
    return {"Ar": ar, "A": A, "ybar": yb, "I": I}


def deck_profile(sr: float, wrt: float, wrb: float, hr: float,
                 td: float) -> Dict[str, float]:
    """Thin-walled steel deck, one pitch: {Ld, ybar, I} (centre-line)."""
    l1 = sr - wrt
    l2 = wrb
    lw = math.sqrt(hr * hr + ((wrt - wrb) / 2.0) ** 2)
    L = l1 + l2 + 2.0 * lw
    yb = (l1 * hr + 2.0 * lw * hr / 2.0) / L
    I = td * (l1 * (hr - yb) ** 2 + l2 * yb * yb
              + 2.0 * lw * (hr * hr / 12.0 + (hr / 2.0 - yb) ** 2))
    return {"Ld": L, "ybar": yb, "I": I}


def weight_thickness(ssec, mat) -> float:
    """Weight-equivalent thickness of the section material (m)."""
    s = getattr(ssec, "slab", None) or {}
    typ = s.get("type")
    g = s.get
    if typ == "ribbed":
        hr = g("overall_depth") - g("slab_thickness")
        ar = 0.5 * (g("stem_width_top") + g("stem_width_bottom")) * hr
        return g("slab_thickness") + ar / g("rib_spacing")
    if typ == "waffle":
        hr = g("overall_depth") - g("slab_thickness")
        wbar = 0.5 * (g("stem_width_top") + g("stem_width_bottom"))
        ar = wbar * hr
        s1, s2 = g("rib_spacing_1"), g("rib_spacing_2")
        return (g("slab_thickness") + ar / s1 + ar / s2
                - wbar * wbar * hr / (s1 * s2))
    if typ == "deck_filled":
        ar = 0.5 * (g("rib_width_top") + g("rib_width_bottom")) * g(
            "rib_depth")
        return (g("slab_depth") + ar / g("rib_spacing")
                + g("deck_unit_weight") / mat.unit_weight)
    if typ == "deck_unfilled":
        return g("deck_unit_weight") / mat.unit_weight
    if typ == "deck_solid":
        return g("slab_depth")
    return ssec.thickness


def sync_thickness(ssec, mat) -> None:
    """Set ``thickness`` to the weight-equivalent value when it differs by
    more than 1e-9 relative (an exact client value is kept bit-for-bit)."""
    if slab_type(ssec) in (None, "drop") or mat is None:
        return
    try:
        t = weight_thickness(ssec, mat)
    except (TypeError, ZeroDivisionError, KeyError):
        return
    if abs(ssec.thickness - t) > _REL * abs(t):
        ssec.thickness = t


# --------------------------------------------------------------------------- #
# equivalent resultant stiffness
# --------------------------------------------------------------------------- #
def _swap(A: np.ndarray) -> np.ndarray:
    P = np.array([[0, 1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)
    return P @ A @ P


def equivalent_resultants(ssec, mat) -> Optional[Tuple[np.ndarray,
                                                       np.ndarray,
                                                       float, float]]:
    """(A, D, S13, S23) per unit width of a STIFF_TYPES section (element
    local axes), else None.  ``mat`` is the (isotropic) section material
    at its plain E (no modifiers)."""
    typ = slab_type(ssec)
    if typ not in STIFF_TYPES:
        return None
    s = ssec.slab
    g = s.get
    E, nu = float(mat.E), float(mat.nu)
    G = E / (2.0 * (1.0 + nu))
    c = E / (1.0 - nu * nu)
    if typ == "waffle":
        hf = g("slab_thickness")
        hr = g("overall_depth") - hf
        wt, wb = g("stem_width_top"), g("stem_width_bottom")
        s1, s2 = g("rib_spacing_1"), g("rib_spacing_2")
        t1 = tee_strip(s2, hf, wt, wb, hr)      # ribs parallel to local 1
        t2 = tee_strip(s1, hf, wt, wb, hr)      # ribs parallel to local 2
        A = np.array([[c * hf + E * t1["Ar"] / s2, c * nu * hf, 0.0],
                      [c * nu * hf, c * hf + E * t2["Ar"] / s1, 0.0],
                      [0.0, 0.0, G * hf]])
        Dp = hf ** 3 / 12.0
        D = np.array([[E * t1["I"] / s2, c * nu * Dp, 0.0],
                      [c * nu * Dp, E * t2["I"] / s1, 0.0],
                      [0.0, 0.0, G * Dp]])
        return (A, D, 5.0 / 6.0 * G * (hf + t1["Ar"] / s2),
                5.0 / 6.0 * G * (hf + t2["Ar"] / s1))
    if typ in ("ribbed", "deck_filled"):
        if typ == "ribbed":
            hf = g("slab_thickness")
            hr = g("overall_depth") - hf
            wt, wb, sp = (g("stem_width_top"), g("stem_width_bottom"),
                          g("rib_spacing"))
        else:
            hf, hr = g("slab_depth"), g("rib_depth")
            wt, wb, sp = (g("rib_width_top"), g("rib_width_bottom"),
                          g("rib_spacing"))
        t = tee_strip(sp, hf, wt, wb, hr)
        A = np.array([[c * hf + E * t["Ar"] / sp, c * nu * hf, 0.0],
                      [c * nu * hf, c * hf, 0.0],
                      [0.0, 0.0, G * hf]])
        Dp = hf ** 3 / 12.0
        D = np.array([[E * t["I"] / sp, c * nu * Dp, 0.0],
                      [c * nu * Dp, c * Dp, 0.0],
                      [0.0, 0.0, G * Dp]])
        S13, S23 = 5.0 / 6.0 * G * (hf + t["Ar"] / sp), 5.0 / 6.0 * G * hf
    else:                                       # deck_unfilled
        hr, td, sr = g("rib_depth"), g("deck_thickness"), g("rib_spacing")
        p = deck_profile(sr, g("rib_width_top"), g("rib_width_bottom"), hr,
                         td)
        A = np.diag([E * td * p["Ld"] / sr, E * td ** 3 / (6.0 * hr * hr),
                     G * td])
        D = np.diag([E * p["I"] / sr, E * td ** 3 / 12.0,
                     G * td ** 3 / 12.0])
        S13 = S23 = 5.0 / 6.0 * G * td
    if int(g("rib_dir", 1) or 1) == 2:
        return _swap(A), _swap(D), S23, S13
    return A, D, S13, S23


def summary(ssec, mat) -> dict:
    """Display / API summary of a slab-type section."""
    out = {"type": slab_type(ssec), "weight_thickness":
           weight_thickness(ssec, mat) if mat is not None else None}
    r = equivalent_resultants(ssec, mat) if mat is not None else None
    if r is not None:
        A, D, S13, S23 = r
        out.update({k: float(v) for k, v in {
                    "A11": A[0, 0], "A22": A[1, 1], "A12": A[0, 1],
                    "A66": A[2, 2], "D11": D[0, 0], "D22": D[1, 1],
                    "D12": D[0, 1], "D66": D[2, 2], "S13": S13,
                    "S23": S23}.items()})
    return out


# --------------------------------------------------------------------------- #
# section-level one-way distribution
# --------------------------------------------------------------------------- #
def region_has_own(region) -> bool:
    return (getattr(region, "distribution", "two_way") != "two_way"
            or getattr(region, "one_way_dir", 1) != 1
            or bool(getattr(region, "distribution_override", False)))


def effective_distribution(model, region) -> Tuple[str, int]:
    """(distribution, one_way_dir) the load transfer of a membrane slab
    uses: the region's own value (override) or the section's."""
    own = (getattr(region, "distribution", "two_way"),
           getattr(region, "one_way_dir", 1))
    if region_has_own(region):
        return own
    ssec = model.shell_sections.get(region.section)
    sd = getattr(ssec, "distribution", None) if ssec is not None else None
    if sd is None:
        return own
    sdir = getattr(ssec, "one_way_dir", None) or 1
    if sd == "one_way":
        from .shell_types import validate_region_distribution
        from types import SimpleNamespace
        probe = SimpleNamespace(uid=region.uid, kind=region.kind,
                                behavior=region.behavior,
                                corners=region.corners,
                                openings=region.openings,
                                distribution="one_way", one_way_dir=sdir)
        try:
            validate_region_distribution(probe)
        except ValueError as exc:
            if region.behavior == "membrane" and region.kind == "slab":
                warnings.warn(f"{exc} - section {region.section!r} one-way "
                              "distribution not inherited (two-way used)")
            return ("two_way", 1)
    return (sd, sdir)


def distribution_view(model, region):
    """``region`` itself, or a shallow copy carrying the inherited one-way
    span direction (what :func:`skyframe.core.oneway.distribution` reads)."""
    d, w = effective_distribution(model, region)
    if (getattr(region, "distribution", "two_way"),
            getattr(region, "one_way_dir", 1)) == (d, w):
        return region
    import copy
    v = copy.copy(region)
    v.distribution, v.one_way_dir = d, w
    return v


# --------------------------------------------------------------------------- #
# drop panels
# --------------------------------------------------------------------------- #
def is_drop(model, region) -> bool:
    if region.kind != "slab":
        return False
    ssec = model.shell_sections.get(region.section)
    return ssec is not None and slab_type(ssec) == "drop"


def drop_regions(model) -> List:
    return [r for r in model.shells if is_drop(model, r)]


def _plane(corners):
    c = np.array([tuple(map(float, p)) for p in corners])
    o = c[0]
    n = np.zeros(3)
    for i in range(len(c)):
        n += np.cross(c[i] - o, c[(i + 1) % len(c)] - o)
    nn = np.linalg.norm(n)
    n = n / nn if nn > 0 else np.array([0.0, 0.0, 1.0])
    e1 = c[1] - c[0]
    e1 = e1 - n * (e1 @ n)
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    return o, e1, e2, n


def _to2d(pts, frame):
    o, e1, e2, _ = frame
    return [((np.array(p, dtype=float) - o) @ e1,
             (np.array(p, dtype=float) - o) @ e2) for p in pts]


def _area2(poly) -> float:
    s = 0.0
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def _ccw(poly):
    return poly if _area2(poly) >= 0 else list(reversed(poly))


def _clip(subject, clip):
    """Sutherland-Hodgman: subject (any simple) clipped by a CONVEX ccw
    clip polygon."""
    out = list(subject)
    for i in range(len(clip)):
        a, b = clip[i], clip[(i + 1) % len(clip)]
        inp, out = out, []
        if not inp:
            break

        def inside(p):
            return ((b[0] - a[0]) * (p[1] - a[1])
                    - (b[1] - a[1]) * (p[0] - a[0])) >= -1e-12

        def inter(p, q):
            dx1, dy1 = q[0] - p[0], q[1] - p[1]
            dx2, dy2 = b[0] - a[0], b[1] - a[1]
            den = dx1 * dy2 - dy1 * dx2
            if abs(den) < 1e-300:
                return q
            tt = ((a[0] - p[0]) * dy2 - (a[1] - p[1]) * dx2) / den
            return (p[0] + tt * dx1, p[1] + tt * dy1)
        for j in range(len(inp)):
            p, q = inp[j - 1], inp[j]
            if inside(q):
                if not inside(p):
                    out.append(inter(p, q))
                out.append(q)
            elif inside(p):
                out.append(inter(p, q))
    return out


def _inside(pt, poly) -> bool:
    x, y = pt
    c = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            if x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                c = not c
    return c


def _coplanar(host, drop, tol=1e-6) -> bool:
    fr = _plane(host.corners)
    o, _, _, n = fr
    return all(abs((np.array(p, dtype=float) - o) @ n) < tol
               for p in drop.corners)


def _convex(poly2) -> bool:
    sg = 0
    n = len(poly2)
    for i in range(n):
        a, b, c = poly2[i], poly2[(i + 1) % n], poly2[(i + 2) % n]
        cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cr) < 1e-14:
            continue
        s = 1 if cr > 0 else -1
        if sg and s != sg:
            return False
        sg = s
    return True


def hosts_of(model, drop) -> List[Tuple[object, float]]:
    """[(host region, exact overlap area m^2)] of a drop region."""
    out = []
    for r in model.shells:
        if r is drop or r.kind != "slab" or r.behavior != "shell" \
                or is_drop(model, r):
            continue
        if not _coplanar(r, drop):
            continue
        fr = _plane(r.corners)
        hp = _ccw(_to2d(r.corners, fr))
        dp = _ccw(_to2d(drop.corners, fr))
        if not _convex(dp):
            continue
        a = abs(_area2(_clip(hp, dp))) if len(hp) >= 3 else 0.0
        if a > 1e-12:
            out.append((r, a))
    return out


def assign_drop_overlaps(model) -> None:
    """Stamp ``_drop_overlap`` (hosts) / ``_drop_area`` (drops) used by
    :attr:`ShellRegion.net_area`.  No-op (no attribute touched) when the
    model has no drop panel."""
    drops = drop_regions(model)
    stamped = [r for r in model.shells
               if "_drop_overlap" in r.__dict__ or "_drop_area" in r.__dict__]
    for r in stamped:
        r.__dict__.pop("_drop_overlap", None)
        r.__dict__.pop("_drop_area", None)
    for d in drops:
        tot = 0.0
        for h, a in hosts_of(model, d):
            h._drop_overlap = getattr(h, "_drop_overlap", 0.0) + a
            tot += a
        d._drop_area = tot


def validate_drops(model) -> None:
    for d in drop_regions(model):
        if d.behavior != "shell":
            raise ValueError(f"Shell {d.uid}: a drop panel needs shell "
                             "behavior")
        fr = _plane(d.corners)
        if not _convex(_to2d(d.corners, fr)):
            raise ValueError(f"Shell {d.uid}: a drop panel outline must be "
                             "convex")
        if d.openings:
            raise ValueError(f"Shell {d.uid}: a drop panel cannot have "
                             "openings")
        if not hosts_of(model, d):
            raise ValueError(f"Shell {d.uid}: the drop panel overlaps no "
                             "coplanar shell-behavior slab")
    assign_drop_overlaps(model)


def retag_quads(model, quads, region_trib, points) -> None:
    """Hand the host elements under each drop outline to the drop region
    (quad.region and the nodal tributary areas).  No-op without drops."""
    drops = drop_regions(model)
    if not drops:
        return
    from .mesh import _polygon_area3d
    for d in drops:
        hosts = {h.uid for h, _ in hosts_of(model, d)}
        trib_d = region_trib.setdefault(d.uid, {})
        if not hosts:
            continue
        fr = _plane(d.corners)
        dp = _ccw(_to2d(d.corners, fr))
        n_hit = 0
        for q in quads:
            if q.region not in hosts:
                continue
            pts = [points[i] for i in q.nodes]
            cen = tuple(sum(p[k] for p in pts) / len(pts) for k in range(3))
            if not _inside(_to2d([cen], fr)[0], dp):
                continue
            share = _polygon_area3d([tuple(p) for p in pts]) / len(pts)
            th = region_trib[q.region]
            for i in q.nodes:
                th[i] = th.get(i, 0.0) - share
                if abs(th[i]) < 1e-12 * max(1.0, share):
                    del th[i]
                trib_d[i] = trib_d.get(i, 0.0) + share
            q.region = d.uid
            n_hit += 1
        if n_hit == 0:
            raise ValueError(f"Shell {d.uid}: the drop panel is smaller than "
                             "one host mesh element - refine the host mesh")
