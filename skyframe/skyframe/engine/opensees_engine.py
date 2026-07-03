"""OpenSeesPy analysis engine for SkyFrame.

Translates the solver-agnostic :class:`~skyframe.core.model.BuildingModel`
into an OpenSees domain (ndm=3, ndf=6) and runs:

* linear static load cases (``run_static``),
* load combos by pure result superposition,
* eigenvalue / modal analysis (``run_modal``).

The result objects (:class:`CaseResults`, :class:`ModalResults`,
:class:`AnalysisResults`) serialise via ``to_dict()`` to the exact JSON
shape documented in ``CONTRACT.md``.

Units follow the model everywhere: kN, m, tonne, s (E in kPa).
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import openseespy.opensees as ops

from skyframe.core.model import BuildingModel, FrameMember, LoadCase

_TOL = 1e-6

Vec3 = Tuple[float, float, float]


# --------------------------------------------------------------------------- #
# small vector helpers
# --------------------------------------------------------------------------- #
def _unit(v: Vec3) -> Vec3:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _pkey(p: Tuple[float, float, float]) -> Vec3:
    """Node dedup key: coordinates rounded to 1e-6."""
    return (round(float(p[0]), 6), round(float(p[1]), 6), round(float(p[2]), 6))


def _local_axes(member: FrameMember) -> Tuple[Vec3, Vec3, Vec3, Vec3, bool]:
    """Local axis triad (x, y, z), the geomTransf vecxz, and a vertical flag.

    Convention: local x runs i -> j.  For non-vertical members
    ``vecxz = (dy, -dx, 0)`` normalised, which puts local y along global +Z
    for horizontal members so gravity bends the member about local z (I33,
    the major axis).  Vertical members use ``vecxz = (1, 0, 0)``.
    """
    d = (member.pj[0] - member.pi[0],
         member.pj[1] - member.pi[1],
         member.pj[2] - member.pi[2])
    x = _unit(d)
    vertical = math.hypot(d[0], d[1]) < _TOL
    if vertical:
        vecxz: Vec3 = (1.0, 0.0, 0.0)
    else:
        vecxz = _unit((x[1], -x[0], 0.0))
    y = _unit(_cross(vecxz, x))
    z = _cross(x, y)
    return x, y, z, vecxz, vertical


# --------------------------------------------------------------------------- #
# assembled-domain bookkeeping
# --------------------------------------------------------------------------- #
@dataclass
class _Assembly:
    """Python-side maps for the OpenSees domain built from a BuildingModel."""

    node_coords: Dict[int, Vec3] = field(default_factory=dict)   # all FE nodes
    struct_coords: Dict[int, Vec3] = field(default_factory=dict)  # excl. masters
    support_tags: List[int] = field(default_factory=list)
    ele_tags: Dict[str, int] = field(default_factory=dict)        # uid -> element
    ele_nodes: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    masters: Dict[str, int] = field(default_factory=dict)         # story -> master
    story_nodes: Dict[str, List[int]] = field(default_factory=dict)
    mass_map: Dict[Tuple[int, int], float] = field(default_factory=dict)  # (tag, dof)
    use_transformation: bool = False


# --------------------------------------------------------------------------- #
# result objects
# --------------------------------------------------------------------------- #
@dataclass
class CaseResults:
    """Results of one static load case (or one combo, by superposition)."""

    name: str
    node_disp: Dict[int, List[float]]           # tag -> [ux..rz] (m, rad)
    reactions: Dict[int, List[float]]           # support tag -> [FX..MZ]
    base: Dict[str, float]                      # total base reaction
    member_forces: Dict[str, List[float]]       # uid -> 12 local end forces
    story: Dict[str, Dict[str, float]]          # story -> ux/uy/drift/shear

    def to_dict(self) -> dict:
        return {
            "node_disp": {str(t): list(v) for t, v in self.node_disp.items()},
            "reactions": {str(t): list(v) for t, v in self.reactions.items()},
            "base": dict(self.base),
            "member_forces": {u: list(v) for u, v in self.member_forces.items()},
            "story": {s: dict(v) for s, v in self.story.items()},
        }


@dataclass
class ModalResults:
    """Eigenvalue analysis results."""

    periods: List[float]                        # s
    frequencies: List[float]                    # Hz
    participation: List[Dict[str, float]]       # per-mode mass ratios
    shapes: Dict[int, Dict[int, List[float]]]   # mode -> tag -> 6 dof values

    def to_dict(self) -> dict:
        return {
            "periods": list(self.periods),
            "frequencies": list(self.frequencies),
            "participation": [dict(p) for p in self.participation],
            "shapes": {str(m): {str(t): list(v) for t, v in sh.items()}
                       for m, sh in self.shapes.items()},
        }


@dataclass
class AnalysisResults:
    """Full analysis bundle: geometry, all cases, combos, and modal."""

    model_name: str
    nodes: Dict[int, Vec3]
    members: List[dict]
    supports: List[int]
    story_order: List[str]
    story_elev: Dict[str, float]
    cases: Dict[str, CaseResults]
    combos: Dict[str, CaseResults]
    modal: ModalResults

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "nodes": {str(t): list(c) for t, c in self.nodes.items()},
            "members": [dict(m) for m in self.members],
            "supports": [str(t) for t in self.supports],
            "story_order": list(self.story_order),
            "story_elev": dict(self.story_elev),
            "cases": {n: c.to_dict() for n, c in self.cases.items()},
            "combos": {n: c.to_dict() for n, c in self.combos.items()},
            "modal": self.modal.to_dict(),
        }


# --------------------------------------------------------------------------- #
# the engine
# --------------------------------------------------------------------------- #
class OpenSeesEngine:
    """Runs a :class:`BuildingModel` through OpenSeesPy.

    Each analysis rebuilds the OpenSees domain from scratch (``ops.wipe()``),
    so the global interpreter state never leaks between runs.  Static case
    results are cached per engine instance so combos superpose each case's
    single solution.
    """

    def __init__(self, model: BuildingModel):
        self.model = model
        self._case_cache: Dict[str, CaseResults] = {}
        self._members_by_uid: Dict[str, FrameMember] = {m.uid: m for m in model.members}
        self._asm: Optional[_Assembly] = None

    # ------------------------------------------------------------------ API
    def run(self) -> AnalysisResults:
        """Run every load case, every combo, and modal analysis."""
        model = self.model
        cases = {name: self.run_static(name) for name in model.cases}
        combos = {name: self._combine(name, combo.cases)
                  for name, combo in model.combos.items()}
        modal = self.run_modal()
        asm = self._asm if self._asm is not None else self._build()
        members = [{"uid": m.uid, "kind": m.kind, "section": m.section,
                    "ni": asm.ele_nodes[m.uid][0], "nj": asm.ele_nodes[m.uid][1],
                    "story": m.story}
                   for m in model.members]
        return AnalysisResults(
            model_name=model.name,
            nodes=dict(asm.node_coords),
            members=members,
            supports=list(asm.support_tags),
            story_order=[s.name for s in model.stories],
            story_elev=model.story_elevations(),
            cases=cases,
            combos=combos,
            modal=modal,
        )

    def run_static(self, case_name: str) -> CaseResults:
        """Solve one linear static load case (cached per engine instance)."""
        if case_name in self._case_cache:
            return self._case_cache[case_name]
        model = self.model
        if case_name not in model.cases:
            raise ValueError(f"Unknown load case {case_name!r}")
        case = model.cases[case_name]

        asm = self._build()
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        for pat_name, scale in case.patterns.items():
            self._apply_pattern(asm, pat_name, scale)

        self._setup_analysis(asm)
        if ops.analyze(1) != 0:
            raise RuntimeError(f"Static analysis failed for case {case_name!r}")

        node_disp = {t: list(ops.nodeDisp(t)) for t in asm.node_coords}
        ops.reactions()
        reactions = {t: list(ops.nodeReaction(t)) for t in asm.support_tags}
        base = self._base_totals(asm, reactions)
        member_forces = {uid: list(ops.eleResponse(etag, "localForce"))
                         for uid, etag in asm.ele_tags.items()}
        story = self._story_results(asm, case, node_disp)

        result = CaseResults(case_name, node_disp, reactions, base,
                             member_forces, story)
        self._case_cache[case_name] = result
        return result

    def run_modal(self, num_modes: Optional[int] = None) -> ModalResults:
        """Eigenvalue analysis: periods, frequencies, shapes, participation."""
        model = self.model
        asm = self._build()
        n_massed = len(asm.mass_map)
        n = min(num_modes or model.num_modes, n_massed)
        if n <= 0:
            return ModalResults([], [], [], {})

        self._setup_analysis(asm)
        lambdas = self._solve_eigen(n, n_massed)
        periods = [2.0 * math.pi / math.sqrt(lam) for lam in lambdas]
        frequencies = [1.0 / t for t in periods]
        shapes = {mode: {t: list(ops.nodeEigenvector(t, mode))
                         for t in asm.node_coords}
                  for mode in range(1, n + 1)}
        participation = self._participation(asm, periods, shapes)
        return ModalResults(periods, frequencies, participation, shapes)

    # --------------------------------------------------------- model assembly
    def _build(self) -> _Assembly:
        """(Re)build the OpenSees domain from the BuildingModel."""
        model = self.model
        ops.wipe()
        ops.model("basic", "-ndm", 3, "-ndf", 6)
        asm = _Assembly()

        # --- nodes: unique member endpoints -----------------------------
        key2tag: Dict[Vec3, int] = {}
        tag = 0
        for m in model.members:
            for p in (m.pi, m.pj):
                key = _pkey(p)
                if key not in key2tag:
                    tag += 1
                    key2tag[key] = tag
                    ops.node(tag, *key)
                    asm.node_coords[tag] = key
        asm.struct_coords = dict(asm.node_coords)

        # --- supports ----------------------------------------------------
        if model.supports:
            for sup in model.supports:
                ntag = self._find_node(asm, sup.point)
                ops.fix(ntag, *[int(bool(r)) for r in sup.restraints])
                asm.support_tags.append(ntag)
        elif asm.struct_coords:
            z_min = min(c[2] for c in asm.struct_coords.values())
            restr = ([1, 1, 1, 1, 1, 1] if model.base_fixity == "fixed"
                     else [1, 1, 1, 0, 0, 0])
            for ntag, c in asm.struct_coords.items():
                if abs(c[2] - z_min) < _TOL:
                    ops.fix(ntag, *restr)
                    asm.support_tags.append(ntag)

        # --- elements ----------------------------------------------------
        etag = 0
        for m in model.members:
            etag += 1
            sec = model.sections[m.section]
            mat = model.materials[sec.material]
            ni, nj = key2tag[_pkey(m.pi)], key2tag[_pkey(m.pj)]
            _, _, _, vecxz, _ = _local_axes(m)
            ops.geomTransf("Linear", etag, *vecxz)
            ops.element("elasticBeamColumn", etag, ni, nj,
                        sec.A, mat.E, mat.G, sec.J, sec.I22, sec.I33, etag)
            asm.ele_tags[m.uid] = etag
            asm.ele_nodes[m.uid] = (ni, nj)

        # --- story node sets ----------------------------------------------
        for s in model.stories:
            asm.story_nodes[s.name] = [t for t, c in asm.struct_coords.items()
                                       if abs(c[2] - s.elevation) < _TOL]

        # --- rigid diaphragms ----------------------------------------------
        if model.rigid_diaphragms:
            cx, cy = model.plan_center()
            for s in model.stories:
                slaves = asm.story_nodes[s.name]
                if len(slaves) < 2:
                    continue
                # round z exactly like node keys so OpenSees sees the master
                # in the same horizontal plane as its slaves
                elev = round(s.elevation, 6)
                tag += 1
                ops.node(tag, cx, cy, elev)
                ops.fix(tag, 0, 0, 1, 1, 1, 0)
                ops.rigidDiaphragm(3, tag, *slaves)
                asm.masters[s.name] = tag
                asm.node_coords[tag] = (cx, cy, elev)
        asm.use_transformation = bool(asm.masters)

        # --- mass -----------------------------------------------------------
        self._assign_mass(asm)
        self._asm = asm
        return asm

    def _assign_mass(self, asm: _Assembly) -> None:
        """Lump story + explicit nodal masses; record the diagonal mass map."""
        model = self.model
        node_mass: Dict[int, List[float]] = {}

        def add(ntag: int, dof: int, value: float) -> None:
            if value == 0.0:
                return
            node_mass.setdefault(ntag, [0.0] * 6)[dof - 1] += value

        story_masses = model.compute_story_masses()
        lx, ly = model.plan_extents()
        for s in model.stories:
            m = story_masses.get(s.name, 0.0)
            if m <= 0.0:
                continue
            if s.name in asm.masters:
                master = asm.masters[s.name]
                add(master, 1, m)
                add(master, 2, m)
                add(master, 6, m * (lx * lx + ly * ly) / 12.0)
            else:
                nodes = asm.story_nodes[s.name]
                if not nodes:
                    warnings.warn(f"Story {s.name!r}: mass {m} t has no nodes "
                                  "to lump onto; ignored")
                    continue
                each = m / len(nodes)
                for t in nodes:
                    add(t, 1, each)
                    add(t, 2, each)

        for nm in model.nodal_masses:
            t = self._find_node(asm, nm.point)
            add(t, 1, nm.mx)
            add(t, 2, nm.my)
            add(t, 3, nm.mz)

        for t, mv in node_mass.items():
            ops.mass(t, *mv)
            for dof in range(1, 7):
                if mv[dof - 1] > 0.0:
                    asm.mass_map[(t, dof)] = mv[dof - 1]

    def _find_node(self, asm: _Assembly, point: Tuple[float, float, float]) -> int:
        """Structural node whose coordinates match `point` within 1e-6."""
        for t, c in asm.struct_coords.items():
            if (abs(c[0] - point[0]) < _TOL and abs(c[1] - point[1]) < _TOL
                    and abs(c[2] - point[2]) < _TOL):
                return t
        raise ValueError(f"No FE node at point {tuple(point)}")

    # ------------------------------------------------------------- loading
    def _apply_pattern(self, asm: _Assembly, pat_name: str, scale: float) -> None:
        """Add one scaled load pattern into the active OpenSees pattern."""
        model = self.model
        if pat_name not in model.patterns:
            raise ValueError(f"Unknown load pattern {pat_name!r}")
        pat = model.patterns[pat_name]

        for udl in pat.member_udls:
            member = self._members_by_uid.get(udl.member_uid)
            if member is None:
                raise ValueError(f"UDL references unknown member {udl.member_uid!r}")
            xax, yax, zax, _, vertical = _local_axes(member)
            if vertical:
                warnings.warn(f"Gravity UDL on vertical member {member.uid!r} "
                              "is purely axial; skipped")
                continue
            g: Vec3 = (0.0, 0.0, -udl.w * scale)  # global, positive w = down
            ops.eleLoad("-ele", asm.ele_tags[member.uid], "-type", "-beamUniform",
                        _dot(g, yax), _dot(g, zax), _dot(g, xax))

        for nl in pat.nodal_loads:
            t = self._find_node(asm, nl.point)
            ops.load(t, nl.fx * scale, nl.fy * scale, nl.fz * scale,
                     0.0, 0.0, 0.0)

        for sf in pat.story_forces:
            fx, fy = sf.fx * scale, sf.fy * scale
            if sf.story in asm.masters:
                ops.load(asm.masters[sf.story], fx, fy, 0.0, 0.0, 0.0, 0.0)
                continue
            nodes = asm.story_nodes.get(sf.story)
            if not nodes:
                raise ValueError(f"Story force on story {sf.story!r} which has "
                                 "no nodes")
            for t in nodes:
                ops.load(t, fx / len(nodes), fy / len(nodes), 0.0, 0.0, 0.0, 0.0)

    # ------------------------------------------------------------- analysis
    @staticmethod
    def _setup_analysis(asm: _Assembly) -> None:
        ops.constraints("Transformation" if asm.use_transformation else "Plain")
        ops.numberer("RCM")
        ops.system("BandGeneral")
        ops.algorithm("Linear")
        ops.integrator("LoadControl", 1.0)
        ops.analysis("Static")

    @staticmethod
    def _base_totals(asm: _Assembly,
                     reactions: Dict[int, List[float]]) -> Dict[str, float]:
        """Total base reaction; moments taken about the global origin."""
        base = {"FX": 0.0, "FY": 0.0, "FZ": 0.0, "MX": 0.0, "MY": 0.0, "MZ": 0.0}
        for t, r in reactions.items():
            x, y, z = asm.node_coords[t]
            fx, fy, fz, mx, my, mz = r
            base["FX"] += fx
            base["FY"] += fy
            base["FZ"] += fz
            base["MX"] += mx + y * fz - z * fy
            base["MY"] += my + z * fx - x * fz
            base["MZ"] += mz + x * fy - y * fx
        return base

    def _story_shears(self, case: LoadCase) -> Dict[str, Tuple[float, float]]:
        """Cumulative applied lateral force at & above each story (kN)."""
        model = self.model
        elevs = model.story_elevations()
        shears: Dict[str, Tuple[float, float]] = {}
        for s in model.stories:
            vx = vy = 0.0
            for pat_name, scale in case.patterns.items():
                pat = model.patterns[pat_name]
                for sf in pat.story_forces:
                    if elevs.get(sf.story, -math.inf) >= s.elevation - _TOL:
                        vx += sf.fx * scale
                        vy += sf.fy * scale
                for nl in pat.nodal_loads:
                    if nl.point[2] >= s.elevation - _TOL:
                        vx += nl.fx * scale
                        vy += nl.fy * scale
            shears[s.name] = (vx, vy)
        return shears

    def _story_results(self, asm: _Assembly, case: LoadCase,
                       node_disp: Dict[int, List[float]]) -> Dict[str, Dict[str, float]]:
        """Per-story displacement, drift ratio, and applied story shear."""
        shears = self._story_shears(case)
        story: Dict[str, Dict[str, float]] = {}
        prev_ux = prev_uy = 0.0
        for s in self.model.stories:  # bottom -> top
            if s.name in asm.masters:
                d = node_disp[asm.masters[s.name]]
                ux, uy = d[0], d[1]
            else:
                nodes = asm.story_nodes[s.name]
                ux = sum(node_disp[t][0] for t in nodes) / len(nodes) if nodes else 0.0
                uy = sum(node_disp[t][1] for t in nodes) / len(nodes) if nodes else 0.0
            h = s.height if s.height > 0 else 1.0
            vx, vy = shears[s.name]
            story[s.name] = {
                "ux": ux, "uy": uy,
                "drift_x": (ux - prev_ux) / h,
                "drift_y": (uy - prev_uy) / h,
                "shear_x": vx, "shear_y": vy,
            }
            prev_ux, prev_uy = ux, uy
        return story

    # --------------------------------------------------------------- combos
    def _combine(self, name: str, factors: Dict[str, float]) -> CaseResults:
        """Linear superposition of already-solved case results."""
        parts = [(self.run_static(case_name), f) for case_name, f in factors.items()]

        def comb_vecs(get) -> dict:
            first = get(parts[0][0])
            out = {k: [0.0] * len(v) for k, v in first.items()}
            for res, f in parts:
                for k, v in get(res).items():
                    acc = out[k]
                    for i, x in enumerate(v):
                        acc[i] += f * x
            return out

        base = {k: sum(f * res.base[k] for res, f in parts)
                for k in ("FX", "FY", "FZ", "MX", "MY", "MZ")}
        story: Dict[str, Dict[str, float]] = {}
        for s_name, s0 in parts[0][0].story.items():
            story[s_name] = {k: sum(f * res.story[s_name][k] for res, f in parts)
                             for k in s0}
        return CaseResults(
            name=name,
            node_disp=comb_vecs(lambda r: r.node_disp),
            reactions=comb_vecs(lambda r: r.reactions),
            base=base,
            member_forces=comb_vecs(lambda r: r.member_forces),
            story=story,
        )

    # ---------------------------------------------------------------- modal
    @staticmethod
    def _eigen_ok(vals, n: int) -> bool:
        if not vals or len(vals) < n:
            return False
        return all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0
                   for v in list(vals)[:n])

    def _solve_eigen(self, n: int, n_massed: int) -> List[float]:
        """Eigenvalues; falls back to -fullGenLapack on failure.

        The default (Arpack) solver requires strictly fewer modes than
        generalized-eigenvalue pairs, so when ``n`` equals the number of
        massed dofs we go straight to the dense solver.
        """
        vals = None
        if n < n_massed:
            try:
                vals = ops.eigen(n)
            except Exception:
                vals = None
        if not self._eigen_ok(vals, n):
            vals = ops.eigen("-fullGenLapack", n)
        if not self._eigen_ok(vals, n):
            raise RuntimeError(f"Eigenvalue analysis failed for {n} modes")
        return [float(v) for v in list(vals)[:n]]

    def _participation(self, asm: _Assembly, periods: List[float],
                       shapes: Dict[int, Dict[int, List[float]]]
                       ) -> List[Dict[str, float]]:
        """Modal mass-participation ratios for ux, uy, rz.

        Uses the Python-side diagonal mass map (mass lives on diaphragm
        masters and explicitly-massed nodes), so slave-node eigenvector
        values never enter the sums.
        """
        totals = {dof: sum(m for (t, d), m in asm.mass_map.items() if d == dof)
                  for dof in (1, 2, 6)}
        out: List[Dict[str, float]] = []
        for i, period in enumerate(periods, start=1):
            phi = shapes[i]
            den = sum(m * phi[t][d - 1] ** 2 for (t, d), m in asm.mass_map.items())
            entry: Dict[str, float] = {"mode": i, "T": period}
            for dof, key in ((1, "ux"), (2, "uy"), (6, "rz")):
                if den <= 0.0 or totals[dof] <= 0.0:
                    entry[key] = 0.0
                    continue
                num = sum(m * phi[t][d - 1]
                          for (t, d), m in asm.mass_map.items() if d == dof)
                entry[key] = (num * num / den) / totals[dof]
            out.append(entry)
        return out
