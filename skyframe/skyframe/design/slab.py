"""PRELIMINARY ACI 318 slab strip flexural design (v0.20, ETABS-style).

For every horizontal MESHED SHELL slab region with analysis results, the
per-quad gauss-averaged bending moments (the v0.4 ``shell_forces``
recovery) are integrated over ETABS-style COLUMN and MIDDLE strips in the
two plan directions, and the required flexural reinforcement is solved by
inverting the Whitney rectangular stress block in closed form.

* Strip layout (ACI 318 §8.4.1, regular rectangular slabs) — per design
  direction, the SUPPORT LINES are the distinct plan coordinates of the
  columns supporting the slab (a column END node in the slab plane inside
  its polygon — the punching-module detection).  Around each support line
  a COLUMN strip extends ``min(L1, L2)/4`` to each side, with ``L1`` the
  largest span between the PERPENDICULAR support lines and ``L2`` the
  distance to the adjacent PARALLEL support line on that side (an edge
  line without a neighbour uses twice its distance to the slab edge as
  L2 — documented convention); everything between the column strips (and
  between the outermost strips and the slab edges) is a MIDDLE strip.
  IRREGULAR regions use their plan BOUNDING BOX (documented
  simplification); a slab with no supporting columns is treated as one
  full-width middle strip per direction.
* Strip moments — at each design section (both ends + midspan of every
  span between perpendicular support lines) the strip design moment is::

      Mu = mean(M_dir over the quads of the section row) * strip width

  where the "section row" is the strip's quads (element centroid inside
  the strip band) nearest the section station.  This is the mid-quad
  rectangle rule for the exact integral ``int M dx`` across the strip —
  exact for a field constant across the strip, O(h^2) otherwise
  (documented accuracy).  ``M_dir``: Mxx reinforces spans along the
  element LOCAL x axis, Myy the local y axis; the structured mesher's
  local x follows the region's corner-0 -> corner-1 edge, so the module
  maps Mxx to the global direction that edge is most aligned with
  (regions drawn at a plan skew keep their local mapping — documented).
* Rebar (per metre of strip width) — closed-form Whitney inversion of
  ``phi*Mn = phi*As*fy*(d - a/2)``, ``a = As*fy/(0.85*fc'*b)``: with
  ``R = 0.85*fc'*b`` and ``T = As*fy``::

      T^2 - 2*R*d*T + 2*R*Mu/phi = 0
      T = R*d - sqrt((R*d)^2 - 2*R*Mu/phi)       (the smaller root)

  A negative discriminant means the section cannot deliver Mu (status
  "NG").  ``phi`` is then made CONSISTENT with the ACI 21.2 net tensile
  strain by fixed-point iteration on :func:`beam_flexure` (a slab section
  is virtually always tension-controlled, so the first pass at phi = 0.9
  already terminates); at convergence ``beam_flexure(...)["phiMn"] == Mu``
  EXACTLY.  Since both terms of T scale linearly with b, the per-metre
  and whole-strip solutions coincide exactly.  ``d = t_slab - cover``
  (cover default 0.03 m, the v0.18 punching convention);
  ``As_min = 0.0018*b*h`` (ACI 24.4.3.2/7.6.1.1 temperature & shrinkage
  minimum for fy = 420 MPa deformed bars — documented); governing bar
  spacing ``s = Ab / max(As_req, As_min)`` capped at ``min(3h, 0.45 m)``
  (ACI 8.7.2.2).

THESE ARE PRELIMINARY SCREENING VALUES, NOT A FINAL CODE CHECK: no
unbalanced-moment transfer, no ACI direct-design/equivalent-frame checks,
no deflection, no crack control, one bar size, isotropic d both ways
(documented simplification — the outer layer's d is larger).  Every
result carries ``"preliminary": True``.

Units: SkyFrame SI — kN, m, kPa; ``As`` is reported in mm^2/m (the one
deliberate non-SI convenience, conversion written explicitly).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from skyframe.design.concrete import _case_block, beam_flexure
from skyframe.design.punching import _point_in_polygon, default_gravity_case
from skyframe.design.wall import fc_from_E

__all__ = [
    "check_slab_strips", "required_steel", "strip_layout",
    "AS_MIN_RATIO", "DEFAULT_BAR_D", "DEFAULT_COVER_SLAB",
]

AS_MIN_RATIO = 0.0018       # ACI 24.4.3.2: temperature & shrinkage minimum
DEFAULT_BAR_D = 0.016       # m, 16 mm bar
DEFAULT_COVER_SLAB = 0.03   # m, d = t - 0.03 (the v0.18 punching convention)
_TOL = 1e-6


# --------------------------------------------------------------------------- #
# rebar inversion (module-level so tests can exercise it directly)
# --------------------------------------------------------------------------- #
def required_steel(Mu: float, d: float, fc: float, fy: float, *,
                   b: float = 1.0, phi_tc: float = 0.9,
                   max_iter: int = 40) -> Optional[dict]:
    """As [m^2] such that ``phi*Mn == Mu`` exactly, or None when impossible.

    Closed-form quadratic inversion of the Whitney block at fixed phi
    (see module docstring), then a fixed-point update of phi from
    :func:`beam_flexure`'s net-tensile-strain rule until self-consistent.
    Returns ``{"As", "phi", "a", "tension_controlled"}``; ``Mu <= 0``
    returns As = 0.  None when the discriminant goes negative (the
    section cannot reach Mu at the current phi — report "NG").
    """
    if d <= 0.0 or fc <= 0.0 or fy <= 0.0 or b <= 0.0:
        raise ValueError("required_steel: b, d, fc, fy must be > 0")
    if Mu <= 0.0:
        return {"As": 0.0, "phi": phi_tc, "a": 0.0,
                "tension_controlled": True}
    R = 0.85 * fc * b
    phi = phi_tc
    for _ in range(max_iter):
        disc = (R * d) ** 2 - 2.0 * R * Mu / phi
        if disc < 0.0:
            return None
        T = R * d - math.sqrt(disc)
        As = T / fy
        fx = beam_flexure(b, d, As, fc, fy, phi_tc=phi_tc)
        if abs(fx["phi"] - phi) <= 1e-12:
            return {"As": As, "phi": phi, "a": fx["a"],
                    "tension_controlled": bool(fx["tension_controlled"])}
        phi = fx["phi"]
    return None                                   # pragma: no cover


# --------------------------------------------------------------------------- #
# strip layout
# --------------------------------------------------------------------------- #
def strip_layout(lo: float, hi: float, lines_par: List[float],
                 L1: float) -> List[dict]:
    """Column/middle strip bands across [lo, hi] (transverse coordinates).

    ``lines_par`` = parallel support-line coordinates (sorted, deduped by
    the caller); ``L1`` = the perpendicular span entering min(L1, L2)/4.
    Returns ``[{"strip": "column"|"middle", "line": float|None,
    "band": (b0, b1)}]`` in ascending band order.  See the module
    docstring for the edge-line L2 convention.
    """
    bands: List[dict] = []
    for k, t in enumerate(lines_par):
        L2_dn = (t - lines_par[k - 1]) if k > 0 else 2.0 * (t - lo)
        L2_up = (lines_par[k + 1] - t) if k + 1 < len(lines_par) \
            else 2.0 * (hi - t)
        b0 = max(t - min(L1, L2_dn) / 4.0, lo)
        b1 = min(t + min(L1, L2_up) / 4.0, hi)
        if b1 - b0 > _TOL:
            bands.append({"strip": "column", "line": float(t),
                          "band": (b0, b1)})
    out: List[dict] = []
    cursor = lo
    for cb in bands:
        b0, b1 = cb["band"]
        if b0 - cursor > _TOL:
            out.append({"strip": "middle", "line": None,
                        "band": (cursor, b0)})
        out.append(cb)
        cursor = max(cursor, b1)
    if hi - cursor > _TOL:
        out.append({"strip": "middle", "line": None, "band": (cursor, hi)})
    if not out:                                   # no support lines at all
        out.append({"strip": "middle", "line": None, "band": (lo, hi)})
    return out


def _support_points(model, sl: dict) -> List[Tuple[float, float]]:
    """Plan (x, y) of every column end supporting one slab (punching rule)."""
    pts: List[Tuple[float, float]] = []
    for m in model.members:
        if m.kind != "column":
            continue
        for end in (m.pi, m.pj):
            if abs(float(end[2]) - sl["z"]) > _TOL:
                continue
            x, y = float(end[0]), float(end[1])
            if _point_in_polygon(x, y, sl["poly"]):
                pts.append((x, y))
    return pts


def _distinct(vals: List[float]) -> List[float]:
    """Sorted coordinates deduped at 1e-6."""
    out: List[float] = []
    for v in sorted(vals):
        if not out or v - out[-1] > _TOL:
            out.append(v)
    return out


def _sections(lines_run: List[float], lo: float, hi: float) -> List[float]:
    """Design-section stations: both ends + midspan of every span between
    perpendicular support lines (deduped); a single/absent line falls back
    to the ends + middle of the region extent."""
    if len(lines_run) < 2:
        return [lo, (lo + hi) / 2.0, hi]
    st: List[float] = []
    for a, b in zip(lines_run, lines_run[1:]):
        for s in (a, (a + b) / 2.0, b):
            if not st or s - st[-1] > _TOL:
                st.append(s)
    return st


# --------------------------------------------------------------------------- #
# quad geometry from the results dict
# --------------------------------------------------------------------------- #
def _quad_data(d_res: dict, blk: dict, region_uid: str) -> List[dict]:
    """[{cx, cy, Mxx, Myy}] per quad of one region, from shell_quads/nodes/
    shell_forces (keys tolerated as int or str — JSON round trips)."""
    nodes = d_res.get("nodes") or {}
    coords: Dict[int, Tuple[float, float]] = {}
    for tag, p in nodes.items():
        coords[int(tag)] = (float(p[0]), float(p[1]))
    forces = blk.get("shell_forces") or {}
    out: List[dict] = []
    for qi, q in enumerate(d_res.get("shell_quads") or []):
        if q.get("region") != region_uid:
            continue
        f = forces.get(qi, forces.get(str(qi)))
        if f is None:
            continue
        pts = [coords[int(t)] for t in q["nodes"]]
        out.append({"cx": sum(p[0] for p in pts) / 4.0,
                    "cy": sum(p[1] for p in pts) / 4.0,
                    "Mxx": float(f[3]), "Myy": float(f[4])})
    return out


def _section_moment(quads: List[dict], band: Tuple[float, float],
                    station: float, run_key: str, par_key: str,
                    m_key: str) -> Optional[float]:
    """Mean moment (kN*m/m) over the strip's quad row nearest a station.

    ``run_key``/``par_key`` pick the centroid coordinate along the strip
    run / across it; quads with centroid inside the band (1e-6 inclusive)
    are filtered, then the row with the minimum |c_run - station| is
    averaged (ties within 1e-6 pooled).  None when the band holds no quad.
    """
    b0, b1 = band
    sel = [q for q in quads if b0 - _TOL <= q[par_key] <= b1 + _TOL]
    if not sel:
        return None
    dmin = min(abs(q[run_key] - station) for q in sel)
    row = [q for q in sel if abs(q[run_key] - station) <= dmin + _TOL]
    return sum(q[m_key] for q in row) / len(row)


# --------------------------------------------------------------------------- #
# main API
# --------------------------------------------------------------------------- #
def check_slab_strips(model, results, case: Optional[str] = None, *,
                      fc_prime: Optional[float] = None,
                      fy: float = 420_000.0,
                      bar_d: float = DEFAULT_BAR_D,
                      cover: float = DEFAULT_COVER_SLAB,
                      phi_tc: float = 0.9) -> List[dict]:
    """ETABS-style column/middle strip flexural design per slab region.

    Parameters
    ----------
    model    : the BuildingModel the results were computed from.
    results  : engine results object or CONTRACT.md dict (must carry
               ``shell_quads``, ``nodes`` and per-case ``shell_forces``).
    case     : static case / additive combo supplying the moments;
               default = the first DEAD-classified case.  Unknown raises.
    fc_prime : slab fc' (kPa); default from the slab concrete's E.
    fy       : rebar yield (kPa); bar_d: bar diameter (m) for spacing;
               cover: ``d = t - cover`` (m, 0 < cover < 1).

    Returns one region dict per horizontal shell slab (see CONTRACT
    v0.20 for the exact shape) — an EMPTY list when the model has none.
    """
    if not (isinstance(cover, (int, float)) and not isinstance(cover, bool)
            and math.isfinite(cover) and 0.0 < cover < 1.0):
        raise ValueError(f"'cover' must be in METRES, 0 < cover < 1 "
                         f"(got {cover!r} — millimetres?)")
    for name, v in (("fy", fy), ("bar_d", bar_d)):
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) or v <= 0.0):
            raise ValueError(f"{name!r} must be a finite value > 0, "
                             f"got {v!r}")
    if fc_prime is not None and not (
            isinstance(fc_prime, (int, float))
            and not isinstance(fc_prime, bool)
            and math.isfinite(fc_prime) and fc_prime > 0.0):
        raise ValueError(f"'fc_prime' must be a finite value > 0 (kPa), "
                         f"got {fc_prime!r}")

    slabs: List[dict] = []
    for r in model.shells:
        if r.kind != "slab" or r.behavior != "shell":
            continue
        zs = [float(p[2]) for p in r.corners]
        if max(zs) - min(zs) > _TOL:
            continue
        ssec = model.shell_sections.get(r.section)
        if ssec is None:
            continue
        mat = model.materials.get(ssec.material)
        fc = (float(fc_prime) if fc_prime is not None
              else fc_from_E(float(mat.E)) if mat is not None else 0.0)
        poly = [(float(p[0]), float(p[1])) for p in r.corners]
        slabs.append({"region": r, "z": zs[0], "poly": poly,
                      "t": float(ssec.thickness), "fc": fc})
    if not slabs:
        return []

    if case is None:
        case = default_gravity_case(model)
    d_res = results.to_dict() if hasattr(results, "to_dict") else results
    blk = _case_block(d_res, case)               # raises KeyError on unknown

    Ab = math.pi * bar_d ** 2 / 4.0              # m^2 per bar
    out: List[dict] = []
    for sl in slabs:
        region = sl["region"]
        h = sl["t"]
        d_eff = h - cover
        entry = {"uid": region.uid, "story": region.story, "case": case,
                 "t": h, "d": d_eff, "fc": sl["fc"],
                 "directions": {"x": [], "y": []},
                 "notes": [], "preliminary": True}
        out.append(entry)
        if d_eff <= 0.0:
            entry["notes"].append(f"effective depth d = {d_eff:.3f} m <= 0"
                                  " (slab thinner than the cover "
                                  "deduction) — not designed")
            continue
        if sl["fc"] <= 0.0:
            entry["notes"].append("slab fc' could not be resolved (pass "
                                  "fc_prime) — not designed")
            continue
        quads = _quad_data(d_res, blk, region.uid)
        if not quads:
            entry["notes"].append("no shell_forces for this region in the "
                                  "requested case — not designed")
            continue
        xs = [p[0] for p in sl["poly"]]
        ys = [p[1] for p in sl["poly"]]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        sup = _support_points(model, sl)
        sup_x = _distinct([p[0] for p in sup])
        sup_y = _distinct([p[1] for p in sup])
        if not sup:
            entry["notes"].append("no supporting columns detected — whole "
                                  "region designed as one middle strip "
                                  "per direction")
        # local-x (Mxx) follows the corner0 -> corner1 edge (mesher u)
        c = region.corners
        u = (float(c[1][0]) - float(c[0][0]), float(c[1][1]) - float(c[0][1]))
        mxx_is_x = abs(u[0]) >= abs(u[1])
        if not mxx_is_x:
            entry["notes"].append("region local x runs along global Y: "
                                  "Mxx/Myy mapping swapped accordingly")

        as_min = AS_MIN_RATIO * 1.0 * h          # m^2 per metre of width
        s_cap = min(3.0 * h, 0.45)               # m (ACI 8.7.2.2)

        for direction in ("x", "y"):
            if direction == "x":                 # strips RUN along x
                lo, hi = bbox[1], bbox[3]        # transverse = y
                r_lo, r_hi = bbox[0], bbox[2]
                lines_par, lines_run = sup_y, sup_x
                run_key, par_key = "cx", "cy"
                m_key = "Mxx" if mxx_is_x else "Myy"
            else:                                # strips RUN along y
                lo, hi = bbox[0], bbox[2]        # transverse = x
                r_lo, r_hi = bbox[1], bbox[3]
                lines_par, lines_run = sup_x, sup_y
                run_key, par_key = "cy", "cx"
                m_key = "Myy" if mxx_is_x else "Mxx"
            # span L1 between perpendicular lines (bbox extent fallback)
            if len(lines_run) >= 2:
                L1 = max(b - a for a, b in zip(lines_run, lines_run[1:]))
            else:
                L1 = r_hi - r_lo
            stations = _sections(lines_run, r_lo, r_hi)
            for band in strip_layout(lo, hi, lines_par, L1):
                b0, b1 = band["band"]
                width = b1 - b0
                strip = {"strip": band["strip"], "line": band["line"],
                         "band": [b0, b1], "width": width, "sections": []}
                entry["directions"][direction].append(strip)
                for s in stations:
                    mu = _section_moment(quads, (b0, b1), s, run_key,
                                         par_key, m_key)
                    if mu is None:
                        continue                 # empty band row
                    Mu_strip = mu * width
                    sol = required_steel(abs(mu), d_eff, sl["fc"], fy,
                                         b=1.0, phi_tc=phi_tc)
                    sec_d = {"x": float(s), "Mu": Mu_strip, "mu": mu,
                             "As_min": as_min * 1e6}      # m^2/m -> mm^2/m
                    if sol is None:
                        sec_d.update({"As_req": None, "spacing": None,
                                      "status": "NG"})
                        strip["sections"].append(sec_d)
                        continue
                    as_req = sol["As"]                     # m^2 per metre
                    as_gov = max(as_req, as_min)
                    sec_d.update({
                        "As_req": as_req * 1e6,            # mm^2/m
                        "spacing": min(Ab / as_gov, s_cap),
                        "min_governs": bool(as_min >= as_req),
                        "status": "OK",
                    })
                    strip["sections"].append(sec_d)
    return out
