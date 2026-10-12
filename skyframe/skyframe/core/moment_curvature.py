"""Section Designer moment-curvature analysis (Wave C).

CONTRACT "Anisotropic shells, material angle, Section Designer
moment-curvature, layered shells with any material, results CSV folder".

:func:`moment_curvature` integrates a :class:`~skyframe.core.
sections_designer.DesignerSection` under a constant axial load ``P``
(compression POSITIVE, kN) for an increasing curvature about a neutral
axis at ``angle`` (deg; 0 = bending about the section 3-axis, the depth
coordinate is ``z``; 90 = about the 2-axis, depth ``y``):

* plane sections: ``eps(v) = eps0 - phi (v - vc)`` with ``v = z cos(a) +
  y sin(a)`` and ``vc`` the GROSS centroid - positive curvature puts the
  +v face in compression;
* every polygon is cut into ``n_strips`` bands of equal height
  perpendicular to ``v`` (exact band areas / centroids by polygon
  clipping); a band takes the stress at its centroid strain (midpoint
  rule, error O(1/n^2)); holes subtract with the base material's law;
  rebar are points (the displaced concrete is not deducted - the same
  rule as the PMM surface);
* ``eps0`` solves ``sum sigma A = -P`` (bracketed root, the root nearest
  the previous step) at each curvature;
* the moment about the gross centroid ``M = -sum sigma A (v - vc)``
  (positive = compression on the +v face) and the cross moment about the
  perpendicular axis ``M_perp = sum sigma A (u - uc)`` are reported.

Stress-strain models (:mod:`skyframe.core.stress_strain` laws,
compression negative):

=================  ========================================================
concrete           "simple" Hognestad parabola to eps_c0, linear to 0.85
                   f'c at eps_cu (default); "mander" unconfined Mander;
                   "mander_confined" Mander with f'cc = k f'c, eps_cc =
                   eps_c0 (1 + 5 (k - 1)), ultimate eps_cu_conf;
                   "park" Kent-Park; "material" the material's own
                   stress_strain (legacy law when null)
steel              "simple" bilinear (E, fy, hardening b); "epp" elastic -
                   perfectly plastic (b = 0); "park" Park strain hardening
                   (fu = Material.fu or 1.25 fy); "material"
=================  ========================================================

A material is "concrete" or "steel" by its ``material_type``
(:func:`skyframe.core.stress_strain.kind_of`; ``other`` uses its own
law).  Concrete tension is ignored unless ``ft`` > 0 is given.

Limit states (the curve ends at the first): concrete crushing - the
extreme compression strain of a concrete polygon reaches ``eps_cu``
(``eps_cu_conf`` for "mander_confined"); steel rupture - a steel strain
reaches ``eps_su``.  Both the first-yield point (the first rebar - or
steel fibre when there are no bars - reaching ``fy / Es`` in tension) and
the ultimate point are located exactly by bisection on the curvature.
The Caltrans idealised elastic-plastic curve passes through first yield
and conserves the area under the curve up to ultimate:
``Mp = k_e (phi_u - sqrt(phi_u^2 - 2 Area / k_e))``, ``k_e = My / phi_y``.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from skyframe.core import stress_strain as _ss
from skyframe.core.sections_designer import (DesignerSection, clip_polygon,
                                             polygon_area, polygon_integrals,
                                             validate_designer_section)

CONCRETE_MODELS = ("simple", "mander", "mander_confined", "park", "material")
STEEL_MODELS = ("simple", "epp", "park", "material")
DEFAULTS = {"eps_c0": 0.002, "eps_cu": 0.0035, "fcc_ratio": 1.3,
            "eps_cu_conf": 0.012, "eps_su": 0.09, "eps_sh": 0.01, "b": 0.01,
            "ft": 0.0}
N_STRIPS = 200
N_STEPS = 60


class _Law:
    """Uniaxial stress function + limit strains of one material."""

    def __init__(self, fn, kind: str, Es: float, fy: Optional[float],
                 eps_lim: float, label: str):
        self.fn = fn
        self.kind = kind
        self.Es = Es
        self.fy = fy
        self.eps_lim = eps_lim
        self.label = label


def _fake(mat, model: str, params: dict):
    return SimpleNamespace(name=mat.name, E=float(mat.E), nu=mat.nu,
                           material_type=mat.material_type,
                           fc=getattr(mat, "fc", None),
                           fy=getattr(mat, "fy", None),
                           fu=getattr(mat, "fu", None),
                           stress_strain={"model": model,
                                          "hysteresis": "kinematic",
                                          "params": params})


def make_law(mat, concrete: str, steel: str, p: dict) -> _Law:
    """The :class:`_Law` of one material for the chosen models."""
    kind = _ss.kind_of(getattr(mat, "material_type", "concrete"))
    if kind == "concrete":
        if concrete not in CONCRETE_MODELS:
            raise ValueError(f"concrete model must be one of "
                             f"{CONCRETE_MODELS}, got {concrete!r}")
        fc = _ss.default_strength(mat)
        ecu = p["eps_cu"]
        if concrete == "material":
            law = _ss.material_law(mat) or _ss.legacy_law(mat)
            ecu = float(law.params.get("eps_cu", law.params.get("eps_u",
                                                                ecu)))
        elif concrete == "mander_confined":
            k = float(p["fcc_ratio"])
            if not k >= 1.0:
                raise ValueError("fcc_ratio must be >= 1")
            ecc = p["eps_c0"] * (1.0 + 5.0 * (k - 1.0))
            ecu = float(p["eps_cu_conf"])
            prm = {"eps_c0": ecc, "eps_cu": ecu}
            if p["ft"] > 0:
                prm["ft"] = p["ft"]
            law = _ss.material_law(_fake(mat, "mander", prm),
                                   strength=k * fc)
        else:
            prm = {"eps_c0": p["eps_c0"], "eps_cu": ecu}
            if p["ft"] > 0:
                prm["ft"] = p["ft"]
            law = _ss.material_law(_fake(mat, concrete, prm))
            if concrete == "park":
                ecu = float(law.params["eps_u"])
        return _Law(law.exact, "concrete", float(mat.E), None, ecu,
                    f"{concrete} (f'c {fc:.6g} kPa)")
    if kind == "steel":
        if steel not in STEEL_MODELS:
            raise ValueError(f"steel model must be one of {STEEL_MODELS}, "
                             f"got {steel!r}")
        fy = _ss.default_strength(mat)
        if steel == "material":
            law = _ss.material_law(mat) or _ss.legacy_law(mat)
        else:
            prm = {"b": 0.0 if steel == "epp" else p["b"],
                   "eps_sh": p["eps_sh"], "eps_su": p["eps_su"]}
            law = _ss.material_law(_fake(mat, "park" if steel == "park"
                                         else "simple", prm))
        return _Law(law.exact, "steel", float(mat.E), fy, p["eps_su"],
                    f"{steel} (fy {fy:.6g} kPa)")
    law = _ss.material_law(mat) or _ss.legacy_law(mat)
    return _Law(law.exact, "other", float(mat.E), None, float("inf"),
                "material law")


def _rot(y: float, z: float, a: float) -> Tuple[float, float]:
    c, s = math.cos(a), math.sin(a)
    return y * c - z * s, z * c + y * s           # (u, v)


def _bands(verts_uv: List[list], n: int) -> List[Tuple[float, float, float]]:
    """[(area, u_c, v_c)] of ``n`` equal-height bands of a CCW polygon."""
    vs = [p[1] for p in verts_uv]
    lo, hi = min(vs), max(vs)
    out = []
    for k in range(n):
        a, b = lo + (hi - lo) * k / n, lo + (hi - lo) * (k + 1) / n
        part = clip_polygon(verts_uv, a, "above")
        if part:
            part = clip_polygon(part, b, "below")
        if not part:
            continue
        A, Su, Sv, _, _ = polygon_integrals(part)
        if abs(A) < 1e-16:
            continue
        out.append((A, Su / A, Sv / A))
    return out


def _normalise(p: Optional[dict]) -> dict:
    out = dict(DEFAULTS)
    for k, v in (p or {}).items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown moment-curvature parameter {k!r} "
                             f"(allowed: {sorted(DEFAULTS)})")
        if not (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v)):
            raise ValueError(f"parameter {k} must be a finite number")
        out[k] = float(v)
    for k in ("eps_c0", "eps_cu", "eps_cu_conf", "eps_su", "eps_sh"):
        if out[k] <= 0:
            raise ValueError(f"{k} must be > 0")
    if out["eps_cu"] <= out["eps_c0"]:
        raise ValueError("eps_cu must exceed eps_c0")
    return out


class _Section:
    """Fibres of one designer section about one neutral-axis angle."""

    def __init__(self, ds: DesignerSection, materials, angle: float,
                 concrete: str, steel: str, p: dict, n_strips: int):
        a = math.radians(angle)
        self.laws: Dict[str, _Law] = {}

        def law(name):
            if name not in self.laws:
                self.laws[name] = make_law(materials[name], concrete,
                                           steel, p)
            return self.laws[name]

        A = Su = Sv = 0.0
        polys = []
        for sgn, verts, m in ds.signed_polygons():
            uv = [list(_rot(y, z, a)) for y, z in verts]
            if polygon_area(uv) < 0.0:
                uv = list(reversed(uv))
            ar, su, sv, _, _ = polygon_integrals(uv)
            A += sgn * ar
            Su += sgn * su
            Sv += sgn * sv
            polys.append((sgn, uv, ds.material if sgn < 0 else m))
        self.uc, self.vc = Su / A, Sv / A
        self.A = A
        # fibres: (area (signed), u, v, law name, is_bar)
        self.fib: List[Tuple[float, float, float, str, bool]] = []
        vmin, vmax = float("inf"), float("-inf")
        # solid polygon depth ranges (vmin, vmax, material): extreme-fibre
        # strains for crushing / rupture / first yield
        self.conc_v: List[Tuple[float, float, str]] = []
        for sgn, uv, m in polys:
            law(m)
            for ar, uu, vv in _bands(uv, n_strips):
                self.fib.append((sgn * ar, uu, vv, m, False))
            vs = [q[1] for q in uv]
            if sgn > 0:
                vmin, vmax = min(vmin, min(vs)), max(vmax, max(vs))
                self.conc_v.append((min(vs), max(vs), m))
        for r in ds.rebar:
            uu, vv = _rot(float(r["y"]), float(r["z"]), a)
            law(r["material"])
            self.fib.append((float(r["area"]), uu, vv, r["material"], True))
        self.vmin, self.vmax = vmin, vmax
        self.depth = vmax - vmin
        self.bars = [f for f in self.fib if f[4]]

    def strain(self, eps0: float, phi: float, v: float) -> float:
        return eps0 - phi * (v - self.vc)

    def forces(self, eps0: float, phi: float) -> Tuple[float, float, float]:
        N = M = Mp = 0.0
        for ar, uu, vv, m, _bar in self.fib:
            s = self.laws[m].fn(eps0 - phi * (vv - self.vc)) * ar
            N += s
            M -= s * (vv - self.vc)
            Mp += s * (uu - self.uc)
        return N, M, Mp

    def solve_eps0(self, phi: float, P: float, guess: float) -> float:
        f = lambda e: self.forces(e, phi)[0] + P          # noqa: E731
        f0 = f(guess)
        if f0 == 0.0:
            return guess
        step = 1e-5
        lo = hi = guess
        flo = fhi = f0
        for _ in range(80):
            lo, hi = guess - step, guess + step
            flo, fhi = f(lo), f(hi)
            if flo <= 0.0 <= f0 or f0 <= 0.0 <= flo:
                hi, fhi = guess, f0
                break
            if fhi >= 0.0 >= f0 or f0 >= 0.0 >= fhi:
                lo, flo = guess, f0
                break
            step *= 1.6
            if step > 0.5:                 # |strain| > 0.5: unphysical
                raise ValueError(
                    "moment-curvature: no axial equilibrium (the axial "
                    "load exceeds the section capacity at this curvature)")
        else:
            raise ValueError("moment-curvature: no axial equilibrium (the "
                             "axial load exceeds the section capacity at "
                             "this curvature)")
        for _ in range(200):                       # bisection (robust)
            mid = 0.5 * (lo + hi)
            fm = f(mid)
            if (fm <= 0.0) == (flo <= 0.0):
                lo, flo = mid, fm
            else:
                hi, fhi = mid, fm
            if abs(hi - lo) < 1e-15:
                break
        return 0.5 * (lo + hi)

    # ---------------------------------------------------- state measures
    def extremes(self, eps0: float, phi: float) -> dict:
        """Extreme concrete compression strain, max steel tension / max
        steel compression strains and the governing ratios."""
        ecmin = 0.0
        crush = 0.0
        for v0, v1, m in self.conc_v:
            lw = self.laws[m]
            if lw.kind != "concrete":
                continue
            e = min(self.strain(eps0, phi, v0), self.strain(eps0, phi, v1))
            ecmin = min(ecmin, e)
            crush = max(crush, -e / lw.eps_lim)
        es_t = -float("inf")
        rup = 0.0
        yld = -float("inf")
        # steel points: bars + the extreme fibres of steel polygons
        pts = [(vv, m, True) for ar, uu, vv, m, bar in self.fib
               if bar and self.laws[m].kind == "steel"]
        for v0, v1, m in self.conc_v:
            if self.laws[m].kind == "steel":
                pts += [(v0, m, False), (v1, m, False)]
        for vv, m, bar in pts:
            e = self.strain(eps0, phi, vv)
            lw = self.laws[m]
            es_t = max(es_t, e)
            rup = max(rup, abs(e) / lw.eps_lim)
        # first-yield measure: rebar (tension) when present, else the
        # extreme fibre of a steel polygon (either sign)
        bars = [q for q in pts if q[2]]
        for vv, m, bar in (bars or pts):
            lw = self.laws[m]
            e = self.strain(eps0, phi, vv)
            yld = max(yld, (e if bars else abs(e)) / (lw.fy / lw.Es))
        return {"eps_c": ecmin, "crush": crush,
                "eps_s": es_t if es_t > -float("inf") else None,
                "rupture": rup, "yield": yld}


def moment_curvature(ds: DesignerSection, materials, P: float = 0.0,
                     angle: float = 0.0, concrete: str = "simple",
                     steel: str = "simple", params: Optional[dict] = None,
                     n_strips: int = N_STRIPS, n_steps: int = N_STEPS
                     ) -> dict:
    """Moment-curvature curve of a designer section (module docstring).

    Returns ``{name, P, angle, concrete, steel, params, points: [{phi, M,
    M_perp, eps0, eps_c, eps_s, na_depth}], yield: {...} | None,
    ultimate: {...}, idealized: {...} | None, limit, laws, units}``
    (phi 1/m, M kN-m, strains -; ``na_depth`` = depth of the neutral axis
    below the compression face, m, null when the section is entirely in
    one sign)."""
    validate_designer_section(ds, materials)
    for nm, v in (("P", P), ("angle", angle)):
        if not (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v)):
            raise ValueError(f"{nm} must be a finite number")
    if not (isinstance(n_strips, int) and 10 <= n_strips <= 2000):
        raise ValueError("n_strips must be an integer in [10, 2000]")
    if not (isinstance(n_steps, int) and 10 <= n_steps <= 1000):
        raise ValueError("n_steps must be an integer in [10, 1000]")
    p = _normalise(params)
    sec = _Section(ds, materials, float(angle), concrete, steel, p,
                   n_strips)
    P = float(P)
    has_conc = any(sec.laws[m].kind == "concrete" for _a, _b, m in
                   sec.conc_v)

    def state(phi, guess):
        e0 = sec.solve_eps0(phi, P, guess)
        N, M, Mp = sec.forces(e0, phi)
        return e0, M, Mp, sec.extremes(e0, phi)

    def measure(ex):
        return max(ex["crush"], ex["rupture"])

    # curvature scale: concrete crushing over ~0.25 depth, or steel yield
    eps_ref = p["eps_cu_conf"] if concrete == "mander_confined" else \
        p["eps_cu"]
    if not has_conc:
        eps_ref = p["eps_su"]
    phi_end = eps_ref / max(0.05 * sec.depth, 1e-6)
    pts = []
    e0 = sec.solve_eps0(0.0, P, 0.0)
    N0, M0, Mp0 = sec.forces(e0, 0.0)
    pts.append((0.0, e0, M0, Mp0, sec.extremes(e0, 0.0)))
    phi = 0.0
    limit = None
    for k in range(1, 20 * n_steps):
        # quadratic spacing (dense before yield), then constant steps
        phi_new = phi_end * (min(k, n_steps) / n_steps) ** 2 + \
            max(0, k - n_steps) * phi_end * (2 * n_steps - 1) / n_steps ** 2
        e_new, M, Mp, ex = state(phi_new, pts[-1][1])
        if measure(ex) >= 1.0:
            lo, hi, g = phi, phi_new, pts[-1][1]
            for _ in range(60):                     # bisection on phi
                mid = 0.5 * (lo + hi)
                em, Mm, Mpm, exm = state(mid, g)
                if measure(exm) >= 1.0:
                    hi = mid
                else:
                    lo, g = mid, em
                if hi - lo < 1e-12 * max(hi, 1e-12):
                    break
            e_u, M_u, Mp_u, ex_u = state(hi, g)
            pts.append((hi, e_u, M_u, Mp_u, ex_u))
            limit = ("concrete crushing" if ex_u["crush"] >= ex_u["rupture"]
                     else "steel rupture")
            break
        pts.append((phi_new, e_new, M, Mp, ex))
        phi = phi_new
    else:
        limit = "curvature limit (no crushing / rupture reached)"

    def row(t):
        ph, e, M, Mp, ex = t
        na = None
        if ph > 0:
            # eps(v) = 0 at v = vc + e/ph; depth below the +v face
            v0 = sec.vc + e / ph
            if sec.vmin < v0 < sec.vmax:
                na = sec.vmax - v0
        return {"phi": ph, "M": M, "M_perp": Mp, "eps0": e,
                "eps_c": ex["eps_c"], "eps_s": ex["eps_s"], "na_depth": na}

    rows = [row(t) for t in pts]
    # first yield (bisection on the yield measure)
    y = None
    ylist = [t for t in pts if t[4]["yield"] >= 1.0]
    if ylist and len(pts) > 1:
        k = pts.index(ylist[0])
        if k > 0:
            lo, hi, g = pts[k - 1][0], pts[k][0], pts[k - 1][1]
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                em, Mm, Mpm, exm = state(mid, g)
                if exm["yield"] >= 1.0:
                    hi = mid
                else:
                    lo, g = mid, em
            e_y, M_y, Mp_y, ex_y = state(hi, g)
            y = row((hi, e_y, M_y, Mp_y, ex_y))
            rows.insert(k, y)                      # the curve includes it
    ult = rows[-1]
    ideal = None
    if y is not None and y["phi"] > 0 and ult["phi"] > y["phi"]:
        area = 0.0
        for a_, b_ in zip(rows[:-1], rows[1:]):
            area += 0.5 * (a_["M"] + b_["M"]) * (b_["phi"] - a_["phi"])
        ke = y["M"] / y["phi"]
        disc = ult["phi"] ** 2 - 2.0 * area / ke
        if ke > 0 and disc >= 0:
            Mp = ke * (ult["phi"] - math.sqrt(disc))
            ideal = {"Mp": Mp, "phi_y": Mp / ke, "phi_u": ult["phi"],
                     "k_e": ke, "ductility": ult["phi"] / (Mp / ke),
                     "area": area}
    return {
        "name": ds.name, "P": P, "angle": float(angle),
        "concrete": concrete, "steel": steel, "params": p,
        "points": rows, "yield": y, "ultimate": ult, "idealized": ideal,
        "limit": limit,
        "laws": {n: {"kind": lw.kind, "label": lw.label,
                     "eps_limit": (None if math.isinf(lw.eps_lim)
                                   else lw.eps_lim)}
                 for n, lw in sec.laws.items()},
        "centroid": {"u": sec.uc, "v": sec.vc}, "depth": sec.depth,
        "units": {"phi": "1/m", "M": "kN-m", "P": "kN"},
    }


def table_rows(out: dict) -> List[dict]:
    """Flat rows (one per curve point) for CSV / the dialog table."""
    rows = []
    for i, r in enumerate(out["points"]):
        ev = ""
        if out.get("yield") and abs(r["phi"] - out["yield"]["phi"]) < 1e-15:
            ev = "first yield"
        if i == len(out["points"]) - 1:
            ev = out.get("limit") or "ultimate"
        rows.append(dict(r, step=i, event=ev))
    return rows
