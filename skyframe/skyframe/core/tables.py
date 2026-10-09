"""ETABS-style analysis-results tables (Display > Show Tables > Analysis Results).

Pure POST-PROCESSING ("store and compute"): every table is computed from an
already-solved results dict (``AnalysisResults.to_dict()`` shape, see
CONTRACT.md) plus the :class:`~skyframe.core.model.BuildingModel` — the solver
is never re-run.  A small optional *context* (diaphragm master tags, the lumped
mass map, the meshed shell tributary areas) captured from the engine right
after ``run()`` makes the modal / diaphragm tables exact; without it the
context is reconstructed from the model + results (:func:`context_from_results`).

Public API::

    list_tables()                                   -> catalogue
    compute_table(key, results, model, context=None, cases=None)
                                                    -> {key, title, group,
                                                        columns, rows, warnings}
    engine_context(engine)                          -> JSON-safe context dict
    context_from_results(model, results)            -> fallback context

Rows are dicts keyed by column ``key``; every column carries a ``quantity``
(see :data:`QUANTITIES`) so a frontend can convert units.  Static cases and
ADDITIVE combos are the result sources of the case-based tables (envelope
combos and RS/TH results are not linear states, so derived quantities such
as drifts or equilibrium are not defined for them and they are skipped).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .loads_ext import area_load_is_default, area_load_resultant
from .modifiers import frame_weight_mod, shell_weight_mod

TOL = 1e-6

# Quantity tags (base SI-consistent units in parentheses): the frontend maps
# each to its display-unit conversion.
QUANTITIES = {
    "text": "",                    # free text / labels
    "id": "",                      # integer identifiers (joint tags, modes)
    "length": "m",
    "force": "kN",
    "moment": "kN*m",
    "stiffness": "kN/m",
    "ratio": "",                   # dimensionless ratio (drift, mass ratio)
    "percent": "%",
    "angle": "rad",
    "time": "s",                   # periods
    "frequency": "Hz",
    "circular_frequency": "rad/s",
    "eigenvalue": "rad^2/s^2",
    "mass": "tonne",
    "factor": "",                  # participation factors (sqrt(mass) * len)
}


def _col(key: str, label: str, quantity: str) -> dict:
    assert quantity in QUANTITIES, quantity
    return {"key": key, "label": label, "quantity": quantity}


# --------------------------------------------------------------------------- #
# context: masters, mass map, story nodes, shell tributary
# --------------------------------------------------------------------------- #
def engine_context(engine) -> dict:
    """JSON-safe context captured from an engine AFTER ``engine.run()``.

    Reads the engine's elastic assembly (no solve): diaphragm masters, the
    lumped diagonal mass map that the modal participation uses, the story
    node sets, and the meshed shell tributary-area totals.
    """
    asm = getattr(engine, "_asm", None)
    if asm is None:
        return {}
    trib = {}
    mesh = getattr(asm, "mesh", None)
    if mesh is not None:
        trib = {uid: float(sum(t.values()))
                for uid, t in getattr(mesh, "region_trib", {}).items()}
    return {
        "source": "engine",
        "masters": {s: int(t) for s, t in asm.masters.items()},
        "mass": [[int(t), int(d), float(m)]
                 for (t, d), m in sorted(asm.mass_map.items())],
        "story_nodes": {s: [int(t) for t in tags]
                        for s, tags in asm.story_nodes.items()},
        "region_trib": trib,
    }


def _nodes(results: dict) -> Dict[int, Tuple[float, float, float]]:
    return {int(t): (float(c[0]), float(c[1]), float(c[2]))
            for t, c in results.get("nodes", {}).items()}


def _base_elev(model) -> float:
    if model.stories:
        s0 = model.stories[0]
        return float(s0.elevation - s0.height)
    return 0.0


def _structural_tags(results: dict) -> set:
    """Tags referenced by members, shell quads, or supports."""
    tags = set()
    for m in results.get("members", []):
        tags.add(int(m["ni"]))
        tags.add(int(m["nj"]))
    for q in results.get("shell_quads", []):
        tags.update(int(t) for t in q.get("nodes", []))
    tags.update(int(t) for t in results.get("supports", []))
    return tags


def context_from_results(model, results: dict) -> dict:
    """Reconstruct the context from the model + results (no engine).

    Masters are the extra node the engine creates per rigid story at
    ``(plan_center, elevation)`` (the highest such tag that no member,
    shell, or support references).  The mass map mirrors the engine's
    default ``"weight"`` mass source (story mass on the master's ux/uy and
    ``m*(Lx^2+Ly^2)/12`` on rz, or an equal split over the story nodes,
    plus explicit nodal masses).  ``"element_self_mass"`` models cannot be
    reconstructed — the mass is left empty with a warning.
    """
    nodes = _nodes(results)
    struct = _structural_tags(results)
    cx, cy = model.plan_center()
    masters: Dict[str, int] = {}
    for s in model.stories:
        if model.effective_diaphragm(s.name) != "rigid":
            continue
        z = round(s.elevation, 6)
        cands = [t for t, c in nodes.items()
                 if t not in struct and abs(c[0] - cx) < TOL
                 and abs(c[1] - cy) < TOL and abs(c[2] - z) < TOL]
        if cands:
            masters[s.name] = max(cands)
    mtags = set(masters.values())
    story_nodes = {s.name: [t for t, c in nodes.items()
                            if t not in mtags
                            and abs(c[2] - s.elevation) < TOL]
                   for s in model.stories}
    ctx: Dict[str, Any] = {"source": "model", "masters": masters,
                           "story_nodes": story_nodes, "region_trib": {},
                           "mass": []}
    if getattr(model, "mass_source_mode", "weight") != "weight":
        ctx["warnings"] = ["element_self_mass models: mass map not "
                           "reconstructible without the engine context"]
        return ctx
    mass: Dict[Tuple[int, int], float] = {}

    def add(t: int, d: int, v: float) -> None:
        if v:
            mass[(t, d)] = mass.get((t, d), 0.0) + v

    sm = model.compute_story_masses()
    lx, ly = model.plan_extents()
    for s in model.stories:
        m = sm.get(s.name, 0.0)
        if m <= 0.0:
            continue
        if s.name in masters:
            t = masters[s.name]
            add(t, 1, m)
            add(t, 2, m)
            add(t, 6, m * (lx * lx + ly * ly) / 12.0)
        else:
            sn = [t for t in story_nodes[s.name] if t in struct]
            for t in sn:
                add(t, 1, m / len(sn))
                add(t, 2, m / len(sn))
    for nm in getattr(model, "nodal_masses", []):
        t = _find_tag(nodes, nm.point, prefer=struct)
        if t is not None:
            add(t, 1, nm.mx)
            add(t, 2, nm.my)
            add(t, 3, nm.mz)
    ctx["mass"] = [[t, d, v] for (t, d), v in sorted(mass.items()) if v > 0]
    return ctx


def _find_tag(nodes, pt, prefer=()) -> Optional[int]:
    hits = [t for t, c in nodes.items()
            if all(abs(c[k] - float(pt[k])) < TOL for k in range(3))]
    if not hits:
        return None
    pref = [t for t in hits if t in prefer]
    return min(pref or hits)


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
class _Ctx:
    """Everything a table builder needs, resolved once per call."""

    def __init__(self, results: dict, model, context: Optional[dict],
                 cases: Optional[Iterable[str]]):
        self.R = results
        self.model = model
        if not context:
            context = context_from_results(model, results)
        self.context = context
        self.warnings: List[str] = list(context.get("warnings", []))
        self.nodes = _nodes(results)
        self.masters = {s: int(t)
                        for s, t in context.get("masters", {}).items()}
        self.master_tags = set(self.masters.values())
        self.mass = [(int(t), int(d), float(m))
                     for t, d, m in context.get("mass", [])]
        self.story_nodes = {s: [int(t) for t in v] for s, v in
                            context.get("story_nodes", {}).items()}
        self.region_trib = dict(context.get("region_trib", {}))
        self.case_filter = set(cases) if cases else None
        self.stories = list(model.stories)
        self.z_base = _base_elev(model)
        self._grid_pts = None

    # ---- case sources -------------------------------------------------
    def sources(self) -> List[Tuple[str, str, dict]]:
        """(name, case_type, case dict) for static cases + additive combos."""
        out: List[Tuple[str, str, dict]] = []
        model = self.model
        for name, cd in self.R.get("cases", {}).items():
            if self.case_filter is not None and name not in self.case_filter:
                continue
            lc = model.cases.get(name)
            geo = lc.effective_geometric if lc is not None else "linear"
            out.append((name, "LinStatic" if geo == "linear"
                        else "NonStatic", cd))
        for name, cd in self.R.get("combos", {}).items():
            if self.case_filter is not None and name not in self.case_filter:
                continue
            cb = model.combos.get(name)
            if (cb is not None and cb.combo_type != "add") or "min" in cd:
                continue
            out.append((name, "Combination", cd))
        return out

    # ---- geometry -------------------------------------------------------
    def story_at(self, z: float) -> str:
        for s in self.stories:
            if abs(s.elevation - z) < TOL:
                return s.name
        if abs(z - self.z_base) < TOL:
            return "Base"
        return ""

    def grid_label(self, x: float, y: float) -> str:
        if self._grid_pts is None:
            try:
                self._grid_pts = self.model.all_intersections_global()
            except Exception:                         # pragma: no cover
                self._grid_pts = []
        for it in self._grid_pts:
            p = it["point"]
            if abs(p[0] - x) < 1e-3 and abs(p[1] - y) < 1e-3:
                return str(it["label"])
        return ""

    def joints(self) -> List[int]:
        """Structural joints (member ends, shell nodes, supports), with
        coincident duplicates (hinge / panel-zone copies) collapsed onto the
        lowest tag and diaphragm masters excluded."""
        seen: Dict[Tuple[float, float, float], int] = {}
        for t in sorted(_structural_tags(self.R)):
            if t in self.master_tags or t not in self.nodes:
                continue
            c = self.nodes[t]
            key = (round(c[0], 6), round(c[1], 6), round(c[2], 6))
            seen.setdefault(key, t)
        return sorted(seen.values(),
                      key=lambda t: (self.nodes[t][2], self.nodes[t][0],
                                     self.nodes[t][1], t))

    def rigid_disp(self, story: str, cd: dict, x: float, y: float
                   ) -> Optional[Tuple[float, float]]:
        """In-plane (ux, uy) of the rigid diaphragm of ``story`` at (x, y)."""
        mt = self.masters.get(story)
        if mt is None:
            return None
        d = cd.get("node_disp", {}).get(str(mt))
        if d is None:
            return None
        mx, my, _ = self.nodes[mt]
        return (d[0] - d[5] * (y - my), d[1] + d[5] * (x - mx))


def _num(v) -> float:
    return float(v) if v is not None else 0.0


# --------------------------------------------------------------------------- #
# Joint output
# --------------------------------------------------------------------------- #
JOINT_DISP_COLS = [
    _col("story", "Story", "text"), _col("label", "Label", "text"),
    _col("joint", "Unique Name", "id"),
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("ux", "UX", "length"), _col("uy", "UY", "length"),
    _col("uz", "UZ", "length"), _col("rx", "RX", "angle"),
    _col("ry", "RY", "angle"), _col("rz", "RZ", "angle"),
]


def _t_joint_displacements(c: _Ctx) -> List[dict]:
    rows = []
    joints = c.joints()
    for name, ctype, cd in c.sources():
        nd = cd.get("node_disp", {})
        for t in joints:
            d = nd.get(str(t))
            if d is None:
                continue
            x, y, z = c.nodes[t]
            rows.append({"story": c.story_at(z), "label": c.grid_label(x, y),
                         "joint": t, "case": name, "case_type": ctype,
                         "ux": d[0], "uy": d[1], "uz": d[2],
                         "rx": d[3], "ry": d[4], "rz": d[5]})
    return rows


JOINT_DRIFT_COLS = [
    _col("story", "Story", "text"), _col("label", "Label", "text"),
    _col("joint", "Unique Name", "id"),
    _col("joint_below", "Joint Below", "id"),
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("x", "X", "length"), _col("y", "Y", "length"),
    _col("z", "Z", "length"), _col("height", "Height", "length"),
    _col("disp_x", "Disp X", "length"), _col("disp_y", "Disp Y", "length"),
    _col("drift_x", "Drift X", "ratio"), _col("drift_y", "Drift Y", "ratio"),
]


def _t_joint_drifts(c: _Ctx) -> List[dict]:
    """Per story-level joint: drift relative to the joint directly below on
    the same plan position (column line), ``(u - u_below) / dz``."""
    levels = [s.elevation for s in c.stories] + [c.z_base]
    at_level = [t for t in c.joints()
                if any(abs(c.nodes[t][2] - z) < TOL for z in levels)]
    by_xy: Dict[Tuple[float, float], List[int]] = {}
    for t in at_level:
        x, y, _ = c.nodes[t]
        by_xy.setdefault((round(x, 6), round(y, 6)), []).append(t)
    pairs: List[Tuple[int, int]] = []
    for tags in by_xy.values():
        tags.sort(key=lambda t: c.nodes[t][2])
        for lo, hi in zip(tags, tags[1:]):
            if c.nodes[hi][2] - c.nodes[lo][2] > TOL and \
                    c.story_at(c.nodes[hi][2]) not in ("", "Base"):
                pairs.append((hi, lo))
    pairs.sort(key=lambda p: (c.nodes[p[0]][2], c.nodes[p[0]][0],
                              c.nodes[p[0]][1]))
    rows = []
    for name, ctype, cd in c.sources():
        nd = cd.get("node_disp", {})
        for hi, lo in pairs:
            dh, dl = nd.get(str(hi)), nd.get(str(lo))
            if dh is None or dl is None:
                continue
            x, y, z = c.nodes[hi]
            h = z - c.nodes[lo][2]
            rows.append({"story": c.story_at(z), "label": c.grid_label(x, y),
                         "joint": hi, "joint_below": lo, "case": name,
                         "case_type": ctype, "x": x, "y": y, "z": z,
                         "height": h, "disp_x": dh[0], "disp_y": dh[1],
                         "drift_x": (dh[0] - dl[0]) / h,
                         "drift_y": (dh[1] - dl[1]) / h})
    return rows


JOINT_REACTION_COLS = [
    _col("story", "Story", "text"), _col("label", "Label", "text"),
    _col("joint", "Unique Name", "id"),
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("x", "X", "length"), _col("y", "Y", "length"),
    _col("z", "Z", "length"),
    _col("FX", "FX", "force"), _col("FY", "FY", "force"),
    _col("FZ", "FZ", "force"), _col("MX", "MX", "moment"),
    _col("MY", "MY", "moment"), _col("MZ", "MZ", "moment"),
]


def _t_joint_reactions(c: _Ctx) -> List[dict]:
    rows = []
    for name, ctype, cd in c.sources():
        for ts, r in sorted(cd.get("reactions", {}).items(),
                            key=lambda kv: int(kv[0])):
            t = int(ts)
            x, y, z = c.nodes.get(t, (0.0, 0.0, 0.0))
            rows.append({"story": c.story_at(z), "label": c.grid_label(x, y),
                         "joint": t, "case": name, "case_type": ctype,
                         "x": x, "y": y, "z": z,
                         "FX": r[0], "FY": r[1], "FZ": r[2],
                         "MX": r[3], "MY": r[4], "MZ": r[5]})
    return rows


# --------------------------------------------------------------------------- #
# Story output
# --------------------------------------------------------------------------- #
STORY_DRIFT_COLS = [
    _col("story", "Story", "text"), _col("case", "Output Case", "text"),
    _col("case_type", "Case Type", "text"),
    _col("ux", "UX", "length"), _col("uy", "UY", "length"),
    _col("drift_x", "Drift X", "ratio"), _col("drift_y", "Drift Y", "ratio"),
]


def _t_story_drifts(c: _Ctx) -> List[dict]:
    rows = []
    for name, ctype, cd in c.sources():
        st = cd.get("story", {})
        for s in reversed(c.stories):              # ETABS lists top -> down
            e = st.get(s.name)
            if e is None:
                continue
            rows.append({"story": s.name, "case": name, "case_type": ctype,
                         "ux": e["ux"], "uy": e["uy"],
                         "drift_x": e["drift_x"], "drift_y": e["drift_y"]})
    return rows


STORY_STIFF_COLS = [
    _col("story", "Story", "text"), _col("case", "Output Case", "text"),
    _col("case_type", "Case Type", "text"),
    _col("shear_x", "Shear X", "force"), _col("drift_x", "Drift X", "length"),
    _col("stiff_x", "Stiff X", "stiffness"),
    _col("shear_y", "Shear Y", "force"), _col("drift_y", "Drift Y", "length"),
    _col("stiff_y", "Stiff Y", "stiffness"),
]


def _t_story_stiffness(c: _Ctx) -> List[dict]:
    """ETABS Story Stiffness: story shear / interstory drift DISPLACEMENT,
    for the lateral (non-zero story shear) cases — the same definition as the
    engine's ``story_stiffness`` block, with the shear and drift exposed."""
    rows = []
    for name, ctype, cd in c.sources():
        st = cd.get("story", {})
        if not any(abs(st.get(s.name, {}).get("shear_x", 0.0)) > 1e-12 or
                   abs(st.get(s.name, {}).get("shear_y", 0.0)) > 1e-12
                   for s in c.stories):
            continue
        prev_ux = prev_uy = 0.0
        out = []
        for s in c.stories:
            e = st.get(s.name)
            if e is None:
                continue
            dx, dy = e["ux"] - prev_ux, e["uy"] - prev_uy
            vx, vy = e["shear_x"], e["shear_y"]
            kx = abs(vx / dx) if abs(dx) > 1e-15 and abs(vx) > 1e-15 else 0.0
            ky = abs(vy / dy) if abs(dy) > 1e-15 and abs(vy) > 1e-15 else 0.0
            out.append({"story": s.name, "case": name, "case_type": ctype,
                        "shear_x": vx, "drift_x": dx, "stiff_x": kx,
                        "shear_y": vy, "drift_y": dy, "stiff_y": ky})
            prev_ux, prev_uy = e["ux"], e["uy"]
        rows.extend(reversed(out))
    return rows


STORY_FORCE_COLS = [
    _col("story", "Story", "text"), _col("case", "Output Case", "text"),
    _col("case_type", "Case Type", "text"),
    _col("location", "Location", "text"),
    _col("P", "P", "force"), _col("VX", "VX", "force"),
    _col("VY", "VY", "force"), _col("T", "T", "moment"),
    _col("MX", "MX", "moment"), _col("MY", "MY", "moment"),
    _col("n_members", "Members Cut", "id"),
    _col("n_shells", "Shells Cut (excluded)", "id"),
]


def _local_axes_fn():
    from skyframe.engine.opensees_engine import _local_axes   # lazy import
    return _local_axes


def _interp(st: Dict[str, List[float]], xq: float, key: str) -> float:
    xs, vals = st["x"], st[key]
    if xq <= xs[0]:
        return vals[0]
    if xq >= xs[-1]:
        return vals[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= xq <= xs[i + 1]:
            dx = xs[i + 1] - xs[i]
            if dx <= 0.0:
                return vals[i + 1]
            return vals[i] + (xq - xs[i]) / dx * (vals[i + 1] - vals[i])
    return vals[-1]                                     # pragma: no cover


def _story_cut(c: _Ctx, cd: dict, zc: float, axes) -> dict:
    """Resultant of everything ABOVE the horizontal plane z = zc, i.e. the
    force the upper part exerts on the lower part (= -(lower on upper)),
    moments about (plan center x, y, zc).  Frame members only."""
    cx, cy = c.model.plan_center()
    stations = cd.get("member_stations", {})
    F = [0.0, 0.0, 0.0]
    M = [0.0, 0.0, 0.0]
    n = 0
    for m in c.model.members:
        zi, zj = m.pi[2], m.pj[2]
        lo, hi = min(zi, zj), max(zi, zj)
        if not (lo < zc < hi):
            continue
        st = stations.get(m.uid)
        if not st or not st.get("x"):
            continue
        frac = (zc - zi) / (zj - zi)
        r = [m.pi[k] + frac * (m.pj[k] - m.pi[k]) for k in range(3)]
        xq = frac * m.length
        N, V2, V3 = (_interp(st, xq, k) for k in ("N", "V2", "V3"))
        T, M2, M3 = (_interp(st, xq, k) for k in ("T", "M2", "M3"))
        xa, ya, za, _, _ = axes(m)
        # station forces are the action of the j-side on the i-side cut
        # face convention of the engine (see compute_section_cut); the
        # upper part acting on the lower part flips sign when i is above.
        sign = 1.0 if (zj - zi) > 0 else -1.0
        f = [sign * (N * xa[k] + V2 * ya[k] + V3 * za[k]) for k in range(3)]
        mv = [sign * (T * xa[k] + M2 * ya[k] + M3 * za[k]) for k in range(3)]
        d = (r[0] - cx, r[1] - cy, r[2] - zc)
        F = [F[k] + f[k] for k in range(3)]
        M[0] += mv[0] + d[1] * f[2] - d[2] * f[1]
        M[1] += mv[1] + d[2] * f[0] - d[0] * f[2]
        M[2] += mv[2] + d[0] * f[1] - d[1] * f[0]
        n += 1
    return {"P": F[2], "VX": F[0], "VY": F[1], "T": M[2], "MX": M[0],
            "MY": M[1], "n_members": n}


def _shells_crossing(model, zc: float) -> int:
    k = 0
    for r in model.shells:
        zs = [p[2] for p in r.corners]
        if min(zs) < zc - TOL and max(zs) > zc + TOL:
            k += 1
    return k


def _t_story_forces(c: _Ctx) -> List[dict]:
    """ETABS Story Forces at the Top and Bottom of every story: the resultant
    of the loads above a horizontal cut (P, VX, VY global; T, MX, MY about
    the plan center at the cut elevation), integrated from the frame members
    crossing the cut.  Gravity gives P < 0; a +X lateral load gives VX > 0
    and MY > 0 (overturning)."""
    axes = _local_axes_fn()
    rows = []
    eps = 1e-6
    for name, ctype, cd in c.sources():
        out = []
        prev = c.z_base
        for s in c.stories:
            h = s.elevation - prev
            for loc, zc in (("Top", s.elevation - eps * max(h, 1.0)),
                            ("Bottom", prev + eps * max(h, 1.0))):
                f = _story_cut(c, cd, zc, axes)
                nsh = _shells_crossing(c.model, zc)
                out.append({"story": s.name, "case": name,
                            "case_type": ctype, "location": loc, **f,
                            "n_shells": nsh})
            prev = s.elevation
        # ETABS order: top story first, Top before Bottom
        for i in range(len(out) - 2, -1, -2):
            rows.extend(out[i:i + 2])
    if any(r["n_shells"] for r in rows):
        c.warnings.append("shell elements cross some story cuts and are "
                          "EXCLUDED from the story forces (frame members "
                          "only — see n_shells)")
    return rows


# --------------------------------------------------------------------------- #
# Diaphragm output
# --------------------------------------------------------------------------- #
DIA_CM_COLS = [
    _col("story", "Story", "text"), _col("diaphragm", "Diaphragm", "text"),
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("ux", "UX", "length"), _col("uy", "UY", "length"),
    _col("rz", "RZ", "angle"),
    _col("x", "X", "length"), _col("y", "Y", "length"),
    _col("z", "Z", "length"),
]


def _cm_xy(c: _Ctx, story: str) -> Tuple[float, float]:
    sp = c.R.get("story_props", {}).get(story)
    if sp is not None:
        return float(sp["cm_x"]), float(sp["cm_y"])
    return c.model.plan_center()


def _named_dia(c: _Ctx) -> Dict[str, Dict[str, dict]]:
    """results["diaphragms"]["stories"] (named diaphragms) or {}."""
    return (c.R.get("diaphragms") or {}).get("stories") or {}


def _t_diaphragm_cm(c: _Ctx) -> List[dict]:
    rows = []
    named = _named_dia(c)
    for name, ctype, cd in c.sources():
        for s in reversed(c.stories):
            if s.name in named:                 # named diaphragms
                for dn, e in sorted(named[s.name].items()):
                    mt = e.get("master")
                    d = (cd.get("node_disp", {}).get(str(mt))
                         if mt is not None else None)
                    if d is None:
                        continue
                    x, y = e["cm_x"], e["cm_y"]
                    mx, my = e["x"], e["y"]
                    rows.append({"story": s.name, "diaphragm": dn,
                                 "case": name, "case_type": ctype,
                                 "ux": d[0] - d[5] * (y - my),
                                 "uy": d[1] + d[5] * (x - mx),
                                 "rz": d[5], "x": x, "y": y,
                                 "z": s.elevation})
                continue
            mt = c.masters.get(s.name)
            if mt is None:
                continue
            d = cd.get("node_disp", {}).get(str(mt))
            if d is None:
                continue
            x, y = _cm_xy(c, s.name)
            ux, uy = c.rigid_disp(s.name, cd, x, y)
            rows.append({"story": s.name, "diaphragm": "D1", "case": name,
                         "case_type": ctype, "ux": ux, "uy": uy,
                         "rz": d[5], "x": x, "y": y, "z": s.elevation})
    return rows


DIA_DRIFT_COLS = [
    _col("story", "Story", "text"), _col("case", "Output Case", "text"),
    _col("case_type", "Case Type", "text"),
    _col("direction", "Direction", "text"),
    _col("max_disp", "Max Disp", "length"),
    _col("avg_disp", "Avg Disp", "length"),
    _col("disp_ratio", "Disp Ratio", "ratio"),
    _col("max_drift", "Max Drift", "ratio"),
    _col("avg_drift", "Avg Drift", "ratio"),
    _col("drift_ratio", "Drift Ratio", "ratio"),
    _col("label_max", "Label (max)", "id"),
]


def _below_disp(c: _Ctx, cd: dict, idx: int, x: float, y: float
                ) -> Tuple[float, float]:
    """In-plane displacement at (x, y) on the level below story ``idx``."""
    nd = cd.get("node_disp", {})
    if idx > 0:
        below = c.stories[idx - 1]
        rd = c.rigid_disp(below.name, cd, x, y)
        if rd is not None:
            return rd
        zb = below.elevation
    else:
        zb = c.z_base
    for t, (nx, ny, nz) in c.nodes.items():
        if t in c.master_tags:
            continue
        if abs(nx - x) < TOL and abs(ny - y) < TOL and abs(nz - zb) < TOL:
            d = nd.get(str(t))
            if d is not None:
                return d[0], d[1]
    if idx > 0:
        e = cd.get("story", {}).get(c.stories[idx - 1].name, {})
        return e.get("ux", 0.0), e.get("uy", 0.0)
    return 0.0, 0.0


def _t_diaphragm_drifts(c: _Ctx) -> List[dict]:
    """ETABS Diaphragm Max Over Avg Drifts (+ displacements): per rigid
    diaphragm, the drift / displacement at the two plan extreme points
    transverse to each direction (rigid-body field of the master), ``ratio =
    max(|d1|,|d2|) / ((|d1|+|d2|)/2)`` — the ASCE 7 §12.3.2.1 torsional
    ratio (1.0 for a non-rotating diaphragm)."""
    rows = []
    for name, ctype, cd in c.sources():
        for idx in range(len(c.stories) - 1, -1, -1):
            s = c.stories[idx]
            if s.name not in c.masters or \
                    c.rigid_disp(s.name, cd, 0.0, 0.0) is None:
                continue
            pts = [(t, c.nodes[t]) for t in c.story_nodes.get(s.name, [])
                   if t in c.nodes]
            if not pts:
                continue
            h = s.height if s.height > 0 else 1.0
            for direction, comp, other in (("X", 0, 1), ("Y", 1, 0)):
                # extreme points transverse to the loading direction
                lo = min(pts, key=lambda p: (p[1][other], p[0]))
                hi = max(pts, key=lambda p: (p[1][other], -p[0]))
                vals = []
                for t, (x, y, _) in (lo, hi):
                    u = c.rigid_disp(s.name, cd, x, y)[comp]
                    ub = _below_disp(c, cd, idx, x, y)[comp]
                    vals.append((t, u, (u - ub) / h))
                du = [abs(v[1]) for v in vals]
                dd = [abs(v[2]) for v in vals]
                avg_u, avg_d = sum(du) / 2.0, sum(dd) / 2.0
                if avg_u < 1e-14 and avg_d < 1e-14:
                    continue
                imax = 0 if dd[0] >= dd[1] else 1
                rows.append({
                    "story": s.name, "case": name, "case_type": ctype,
                    "direction": direction,
                    "max_disp": max(du), "avg_disp": avg_u,
                    "disp_ratio": max(du) / avg_u if avg_u > 1e-30 else 1.0,
                    "max_drift": max(dd), "avg_drift": avg_d,
                    "drift_ratio": max(dd) / avg_d if avg_d > 1e-30 else 1.0,
                    "label_max": vals[imax][0]})
    return rows


CMCR_COLS = [
    _col("story", "Story", "text"), _col("diaphragm", "Diaphragm", "text"),
    _col("mass", "Mass", "mass"),
    _col("cm_x", "XCM", "length"), _col("cm_y", "YCM", "length"),
    _col("cr_x", "XCR", "length"), _col("cr_y", "YCR", "length"),
]


def _t_centers(c: _Ctx) -> List[dict]:
    sp = c.R.get("story_props", {})
    mass_by_tag = {}
    for t, d, m in c.mass:
        if d == 1:
            mass_by_tag[t] = mass_by_tag.get(t, 0.0) + m
    rows = []
    named = _named_dia(c)
    for s in reversed(c.stories):
        if s.name in named:                     # named diaphragms
            for dn, e in sorted(named[s.name].items()):
                rows.append({"story": s.name, "diaphragm": dn,
                             "mass": e["mass"],
                             "cm_x": e["cm_x"], "cm_y": e["cm_y"],
                             "cr_x": e.get("cr_x"), "cr_y": e.get("cr_y")})
            continue
        e = sp.get(s.name)
        if e is None:
            continue
        rows.append({"story": s.name, "diaphragm": "D1",
                     "mass": mass_by_tag.get(c.masters.get(s.name), 0.0),
                     "cm_x": e["cm_x"], "cm_y": e["cm_y"],
                     "cr_x": e.get("cr_x"), "cr_y": e.get("cr_y")})
    return rows


# --------------------------------------------------------------------------- #
# Base reactions / equilibrium
# --------------------------------------------------------------------------- #
BASE_COLS = [
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("FX", "FX", "force"), _col("FY", "FY", "force"),
    _col("FZ", "FZ", "force"), _col("MX", "MX", "moment"),
    _col("MY", "MY", "moment"), _col("MZ", "MZ", "moment"),
    _col("X", "X", "length"), _col("Y", "Y", "length"),
    _col("Z", "Z", "length"),
]


def resultant_location(F: List[float], M: List[float], z_ref: float
                       ) -> Tuple[Optional[float], ...]:
    """Point on the central axis of the force system (F, M about origin).

    The axis is ``r0 + t F`` with ``r0 = F x M / |F|^2``.  When the resultant
    has a vertical component it is intersected with the base plane
    ``z = z_ref`` (the X/Y "centroid of reactions"); a purely horizontal
    resultant reports ``r0`` (its Z is the height of the line of action).
    None when |F| ~ 0 (a pure couple has no line of action).
    """
    f2 = F[0] ** 2 + F[1] ** 2 + F[2] ** 2
    if f2 < 1e-18:
        return (None, None, None)
    r0 = [(F[1] * M[2] - F[2] * M[1]) / f2,
          (F[2] * M[0] - F[0] * M[2]) / f2,
          (F[0] * M[1] - F[1] * M[0]) / f2]
    if abs(F[2]) > 1e-9 * math.sqrt(f2):
        t = (z_ref - r0[2]) / F[2]
        r0 = [r0[k] + t * F[k] for k in range(3)]
    return tuple(r0)


def _t_base_reactions(c: _Ctx) -> List[dict]:
    rows = []
    for name, ctype, cd in c.sources():
        b = cd.get("base", {})
        F = [_num(b.get(k)) for k in ("FX", "FY", "FZ")]
        M = [_num(b.get(k)) for k in ("MX", "MY", "MZ")]
        X, Y, Z = resultant_location(F, M, c.z_base)
        rows.append({"case": name, "case_type": ctype,
                     "FX": F[0], "FY": F[1], "FZ": F[2],
                     "MX": M[0], "MY": M[1], "MZ": M[2],
                     "X": X, "Y": Y, "Z": Z})
    return rows


def _member_axes_y(m) -> Tuple[Tuple[float, float, float], bool]:
    _, ya, _, _, vertical = _local_axes_fn()(m)
    return ya, vertical


def applied_pattern_totals(model, pattern: str,
                           region_trib: Optional[Dict[str, float]] = None
                           ) -> List[float]:
    """Total applied force [FX, FY, FZ] (kN, global, FZ < 0 = down) of one
    load pattern, summed straight from the load definitions with the
    engine's rules (gravity member loads skip vertical members; self-weight
    acts on every member and shell; shell area loads use the meshed net
    tributary when known, else the net area; thermal loads are
    self-equilibrated)."""
    pat = model.patterns[pattern]
    trib = region_trib or {}
    F = [0.0, 0.0, 0.0]
    members = {m.uid: m for m in model.members}

    def member_load(m, direction: str, w: float) -> None:
        ya, vertical = _member_axes_y(m)
        if direction == "gravity":
            if not vertical:
                F[2] -= w
        elif direction == "global_x":
            F[0] += w
        elif direction == "global_y":
            F[1] += w
        elif direction == "global_z":
            F[2] += w
        elif direction == "local_y":
            for k in range(3):
                F[k] += w * ya[k]

    def area(region) -> float:
        if region.behavior == "shell" and region.uid in trib:
            return trib[region.uid]
        return region.net_area

    for udl in pat.member_udls:
        m = members.get(udl.member_uid)
        if m is not None:
            member_load(m, "gravity", udl.w * m.length)
    for ml in pat.member_loads:
        m = members.get(ml.member_uid)
        if m is None or ml.kind == "moment":    # a couple has no net force
            continue
        if ml.kind == "point":
            w = ml.w
        elif ml.kind == "udl":
            w = ml.w * (ml.b - ml.a) * m.length
        else:
            w = 0.5 * (ml.w + ml.w2) * (ml.b - ml.a) * m.length
        member_load(m, ml.direction, w)
    for nl in pat.nodal_loads:
        F[0] += nl.fx
        F[1] += nl.fy
        F[2] += nl.fz
    for sf in pat.story_forces:
        F[0] += sf.fx
        F[1] += sf.fy
    for al in pat.area_loads:
        region = model._shell(al.region_uid)
        if region is None:
            continue
        if area_load_is_default(al):
            F[2] -= al.q * area(region)
        else:                       # direction / projected / joint pattern
            R = area_load_resultant(region, al)
            for k in range(3):
                F[k] += R[k]
    swf = getattr(pat, "self_weight_factor", 0.0)
    if swf:
        for m in model.members:
            sec = model.sections.get(m.section)
            mat = model.materials.get(sec.material) if sec else None
            if sec is not None and mat is not None:
                from .nonprismatic import member_area as _np_area
                F[2] -= (swf * _np_area(model, m, sec)
                         * mat.unit_weight * m.length
                         * frame_weight_mod(sec))
        for region in model.shells:
            ssec = model.shell_sections.get(region.section)
            mat = model.materials.get(ssec.material) if ssec else None
            if ssec is not None and mat is not None:
                F[2] -= (swf * ssec.total_thickness * mat.unit_weight
                         * area(region) * shell_weight_mod(ssec))
    return F


def _err_pct(applied: List[float], reaction: List[float]) -> float:
    na = math.sqrt(sum(a * a for a in applied))
    res = math.sqrt(sum((a + r) ** 2 for a, r in zip(applied, reaction)))
    if na < 1e-12:
        return 0.0 if res < 1e-9 else 100.0
    return 100.0 * res / na


PATTERN_COLS = [
    _col("pattern", "Load Pattern", "text"), _col("type", "Type", "text"),
    _col("self_weight", "Self Wt Multiplier", "ratio"),
    _col("FX", "Applied FX", "force"), _col("FY", "Applied FY", "force"),
    _col("FZ", "Applied FZ", "force"),
    _col("case", "Check Case", "text"),
    _col("react_FX", "Reaction FX", "force"),
    _col("react_FY", "Reaction FY", "force"),
    _col("react_FZ", "Reaction FZ", "force"),
    _col("error_pct", "Error", "percent"),
]


def _pattern_totals_cache(c: _Ctx) -> Dict[str, List[float]]:
    cache = getattr(c, "_pat_tot", None)
    if cache is None:
        cache = {p: applied_pattern_totals(c.model, p, c.region_trib)
                 for p in c.model.patterns}
        c._pat_tot = cache
    return cache


def _t_load_patterns(c: _Ctx) -> List[dict]:
    """Per load pattern: total applied FX/FY/FZ and — when a static case
    applies that pattern ALONE — the base-reaction sum (normalized by the
    case factor) and the equilibrium error |applied + reaction| / |applied|."""
    tot = _pattern_totals_cache(c)
    rows = []
    cases = c.R.get("cases", {})
    for pname, pat in c.model.patterns.items():
        F = tot[pname]
        row = {"pattern": pname, "type": pat.kind,
               "self_weight": getattr(pat, "self_weight_factor", 0.0),
               "FX": F[0], "FY": F[1], "FZ": F[2], "case": "",
               "react_FX": None, "react_FY": None, "react_FZ": None,
               "error_pct": None}
        for cname, lc in c.model.cases.items():
            if list(lc.patterns) != [pname] or cname not in cases:
                continue
            if c.case_filter is not None and cname not in c.case_filter:
                continue
            f = lc.patterns[pname]
            if abs(f) < 1e-15:
                continue
            b = cases[cname].get("base", {})
            R = [_num(b.get(k)) / f for k in ("FX", "FY", "FZ")]
            err = _err_pct(F, R)
            if (getattr(pat, "ground_displacements", None)
                    and max(abs(v) for v in F) < 1e-9):
                err = None      # settlement only: self-equilibrated reactions
            row.update(case=cname, react_FX=R[0], react_FY=R[1],
                       react_FZ=R[2], error_pct=err)
            break
        rows.append(row)
    return rows


EQUIL_COLS = [
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("applied_FX", "Applied FX", "force"),
    _col("applied_FY", "Applied FY", "force"),
    _col("applied_FZ", "Applied FZ", "force"),
    _col("react_FX", "Reaction FX", "force"),
    _col("react_FY", "Reaction FY", "force"),
    _col("react_FZ", "Reaction FZ", "force"),
    _col("error_pct", "Error", "percent"),
]


def _case_pattern_factors(model, name: str) -> Optional[Dict[str, float]]:
    if name in model.cases:
        return dict(model.cases[name].patterns)
    cb = model.combos.get(name)
    if cb is None:
        return None
    eff: Dict[str, float] = {}
    for cname, f in cb.cases.items():
        sub = _case_pattern_factors(model, cname)
        if sub is None:
            return None
        for p, pf in sub.items():
            eff[p] = eff.get(p, 0.0) + f * pf
    return eff


def _t_equilibrium(c: _Ctx) -> List[dict]:
    """Applied loads vs base reactions per static case / additive combo."""
    tot = _pattern_totals_cache(c)
    rows = []
    for name, ctype, cd in c.sources():
        fac = _case_pattern_factors(c.model, name)
        if fac is None:
            continue
        A = [sum(f * tot[p][k] for p, f in fac.items() if p in tot)
             for k in range(3)]
        b = cd.get("base", {})
        R = [_num(b.get(k)) for k in ("FX", "FY", "FZ")]
        rows.append({"case": name, "case_type": ctype,
                     "applied_FX": A[0], "applied_FY": A[1],
                     "applied_FZ": A[2], "react_FX": R[0], "react_FY": R[1],
                     "react_FZ": R[2], "error_pct": _err_pct(A, R)})
    return rows


# --------------------------------------------------------------------------- #
# Modal results
# --------------------------------------------------------------------------- #
def _modal(c: _Ctx) -> Tuple[List[float], Dict[int, Dict[int, List[float]]]]:
    md = c.R.get("modal", {}) or {}
    periods = [float(t) for t in md.get("periods", [])]
    shapes = {int(m): {int(t): v for t, v in sh.items()}
              for m, sh in (md.get("shapes", {}) or {}).items()}
    return periods, shapes


MODAL_PERIOD_COLS = [
    _col("case", "Case", "text"), _col("mode", "Mode", "id"),
    _col("period", "Period", "time"), _col("frequency", "Frequency",
                                            "frequency"),
    _col("circ_freq", "CircFreq", "circular_frequency"),
    _col("eigenvalue", "Eigenvalue", "eigenvalue"),
]


def _t_modal_periods(c: _Ctx) -> List[dict]:
    periods, _ = _modal(c)
    rows = []
    for i, T in enumerate(periods, start=1):
        w = 2.0 * math.pi / T
        rows.append({"case": "Modal", "mode": i, "period": T,
                     "frequency": 1.0 / T, "circ_freq": w,
                     "eigenvalue": w * w})
    return rows


def _influence(c: _Ctx) -> Dict[str, Dict[Tuple[int, int], float]]:
    """Unit rigid-body influence vectors over the massed (node, dof) pairs.

    UX/UY/UZ: unit ground translation.  RZ: the engine's existing definition
    (the rotational dof only — diaphragm rotational inertia), so RZ ratios
    match ``modal.participation[*].rz``.  RX/RY: unit rotation about the
    global X / Y axis through (plan center, base elevation) — translational
    masses enter with their lever arms (rocking participation)."""
    cx, cy = c.model.plan_center()
    zb = c.z_base
    out: Dict[str, Dict[Tuple[int, int], float]] = {
        k: {} for k in ("UX", "UY", "UZ", "RX", "RY", "RZ")}
    for t, d, _ in c.mass:
        x, y, z = c.nodes.get(t, (cx, cy, zb))
        key = (t, d)
        if d == 1:
            out["UX"][key] = 1.0
            out["RY"][key] = z - zb
        elif d == 2:
            out["UY"][key] = 1.0
            out["RX"][key] = -(z - zb)
        elif d == 3:
            out["UZ"][key] = 1.0
            out["RX"][key] = y - cy
            out["RY"][key] = -(x - cx)
        elif d == 4:
            out["RX"][key] = 1.0
        elif d == 5:
            out["RY"][key] = 1.0
        elif d == 6:
            out["RZ"][key] = 1.0
    return out


DIRS = ("UX", "UY", "UZ", "RX", "RY", "RZ")


def _modal_quantities(c: _Ctx) -> List[dict]:
    """Per mode: modal mass, participation factors, mass ratios."""
    periods, shapes = _modal(c)
    if not periods:
        return []
    if not c.mass:
        c.warnings.append("no mass map available: modal mass tables empty")
        return []
    infl = _influence(c)
    mvec = {(t, d): m for t, d, m in c.mass}
    totals = {k: sum(mvec[key] * r * r for key, r in infl[k].items())
              for k in DIRS}
    out = []
    for i, T in enumerate(periods, start=1):
        phi = shapes.get(i, {})

        def ph(t: int, d: int) -> float:
            v = phi.get(t)
            return float(v[d - 1]) if v is not None else 0.0

        den = sum(m * ph(t, d) ** 2 for (t, d), m in mvec.items())
        e = {"mode": i, "period": T, "modal_mass": den,
             "modal_stiffness": den * (2.0 * math.pi / T) ** 2}
        for k in DIRS:
            L = sum(mvec[key] * r * ph(*key) for key, r in infl[k].items())
            e["gamma_" + k] = L / den if den > 0 else 0.0
            e["ratio_" + k] = ((L * L / den) / totals[k]
                               if den > 0 and totals[k] > 0 else 0.0)
        ke = {"UX": 0.0, "UY": 0.0, "UZ": 0.0, "RZ": 0.0}
        for (t, d), m in mvec.items():
            k = {1: "UX", 2: "UY", 3: "UZ", 6: "RZ"}.get(d)
            if k:
                ke[k] += m * ph(t, d) ** 2
        s = sum(ke.values())
        e["dir"] = {k: (v / s if s > 0 else 0.0) for k, v in ke.items()}
        out.append(e)
    return out


MASS_RATIO_COLS = [
    _col("case", "Case", "text"), _col("mode", "Mode", "id"),
    _col("period", "Period", "time")] + [
    _col(k, label, "ratio") for k, label in (
        ("UX", "UX"), ("UY", "UY"), ("UZ", "UZ"),
        ("SumUX", "Sum UX"), ("SumUY", "Sum UY"), ("SumUZ", "Sum UZ"),
        ("RX", "RX"), ("RY", "RY"), ("RZ", "RZ"),
        ("SumRX", "Sum RX"), ("SumRY", "Sum RY"), ("SumRZ", "Sum RZ"))]


def _t_mass_ratios(c: _Ctx) -> List[dict]:
    rows = []
    cum = {k: 0.0 for k in DIRS}
    for e in _modal_quantities(c):
        row = {"case": "Modal", "mode": e["mode"], "period": e["period"]}
        for k in DIRS:
            cum[k] += e["ratio_" + k]
            row[k] = e["ratio_" + k]
            row["Sum" + k] = cum[k]
        rows.append(row)
    return rows


PART_FACTOR_COLS = [
    _col("case", "Case", "text"), _col("mode", "Mode", "id"),
    _col("period", "Period", "time")] + [
    _col(k, k, "factor") for k in DIRS] + [
    _col("modal_mass", "Modal Mass", "mass"),
    _col("modal_stiffness", "Modal Stiff", "stiffness")]


def _t_part_factors(c: _Ctx) -> List[dict]:
    rows = []
    for e in _modal_quantities(c):
        row = {"case": "Modal", "mode": e["mode"], "period": e["period"],
               "modal_mass": e["modal_mass"],
               "modal_stiffness": e["modal_stiffness"]}
        for k in DIRS:
            row[k] = e["gamma_" + k]
        rows.append(row)
    return rows


DIR_FACTOR_COLS = [
    _col("case", "Case", "text"), _col("mode", "Mode", "id"),
    _col("period", "Period", "time"),
    _col("UX", "UX", "ratio"), _col("UY", "UY", "ratio"),
    _col("UZ", "UZ", "ratio"), _col("RZ", "RZ", "ratio"),
    _col("dominant", "Dominant", "text"),
]


def _t_dir_factors(c: _Ctx) -> List[dict]:
    """ETABS Modal Direction Factors: the share of each mode's kinetic energy
    (``sum m phi^2`` per dof family) in UX / UY / UZ / RZ — rows sum to 1;
    ``dominant`` flags the torsional (RZ) modes."""
    rows = []
    for e in _modal_quantities(c):
        d = e["dir"]
        dom = max(("UX", "UY", "UZ", "RZ"), key=lambda k: d[k])
        rows.append({"case": "Modal", "mode": e["mode"],
                     "period": e["period"], **d,
                     "dominant": dom if sum(d.values()) > 0 else ""})
    return rows


# --------------------------------------------------------------------------- #
# catalogue
# --------------------------------------------------------------------------- #
_G = "Analysis Results"
_TABLES: List[Tuple[str, str, str, List[dict], Callable[[_Ctx], List[dict]]]] = [
    ("joint_displacements", "Joint Displacements",
     f"{_G} > Joint Output > Displacements", JOINT_DISP_COLS,
     _t_joint_displacements),
    ("joint_drifts", "Joint Drifts",
     f"{_G} > Joint Output > Displacements", JOINT_DRIFT_COLS,
     _t_joint_drifts),
    ("joint_reactions", "Joint Reactions",
     f"{_G} > Joint Output > Reactions", JOINT_REACTION_COLS,
     _t_joint_reactions),
    ("story_drifts", "Story Drifts",
     f"{_G} > Structure Output > Story Output", STORY_DRIFT_COLS,
     _t_story_drifts),
    ("story_forces", "Story Forces",
     f"{_G} > Structure Output > Story Output", STORY_FORCE_COLS,
     _t_story_forces),
    ("story_stiffness", "Story Stiffness",
     f"{_G} > Structure Output > Story Output", STORY_STIFF_COLS,
     _t_story_stiffness),
    ("diaphragm_cm_displacements", "Diaphragm Center Of Mass Displacements",
     f"{_G} > Structure Output > Diaphragm Output", DIA_CM_COLS,
     _t_diaphragm_cm),
    ("diaphragm_max_avg_drifts", "Diaphragm Max Over Avg Drifts",
     f"{_G} > Structure Output > Diaphragm Output", DIA_DRIFT_COLS,
     _t_diaphragm_drifts),
    ("centers_mass_rigidity", "Centers Of Mass And Rigidity",
     f"{_G} > Structure Output > Other Output Items", CMCR_COLS,
     _t_centers),
    ("base_reactions", "Base Reactions",
     f"{_G} > Structure Output > Base Reactions", BASE_COLS,
     _t_base_reactions),
    ("load_pattern_summary", "Load Pattern Totals And Equilibrium",
     f"{_G} > Structure Output > Base Reactions", PATTERN_COLS,
     _t_load_patterns),
    ("load_case_equilibrium", "Load Case Equilibrium Check",
     f"{_G} > Structure Output > Base Reactions", EQUIL_COLS,
     _t_equilibrium),
    ("modal_periods", "Modal Periods And Frequencies",
     f"{_G} > Modal Results", MODAL_PERIOD_COLS, _t_modal_periods),
    ("modal_mass_ratios", "Modal Participating Mass Ratios",
     f"{_G} > Modal Results", MASS_RATIO_COLS, _t_mass_ratios),
    ("modal_participation_factors", "Modal Participation Factors",
     f"{_G} > Modal Results", PART_FACTOR_COLS, _t_part_factors),
    ("modal_direction_factors", "Modal Direction Factors",
     f"{_G} > Modal Results", DIR_FACTOR_COLS, _t_dir_factors),
]
TABLES = {k: (title, group, cols, fn) for k, title, group, cols, fn in _TABLES}


def list_tables() -> List[dict]:
    """Catalogue: ``[{key, title, group, columns: [{key, label, quantity}]}]``
    in ETABS table-tree order."""
    return [{"key": k, "title": t, "group": g,
             "columns": [dict(col) for col in cols]}
            for k, t, g, cols, _ in _TABLES]


def compute_table(key: str, results: dict, model, context: Optional[dict] = None,
                  cases: Optional[Iterable[str]] = None) -> dict:
    """Compute one table from a results dict + model (no solve).

    ``cases`` optionally restricts the case-based tables to those names.
    KeyError on an unknown table key.
    """
    if key not in TABLES:
        raise KeyError(key)
    title, group, cols, fn = TABLES[key]
    c = _Ctx(results, model, context, cases)
    rows = fn(c)
    return {"key": key, "title": title, "group": group,
            "columns": [dict(col) for col in cols], "rows": rows,
            "warnings": list(dict.fromkeys(c.warnings))}
