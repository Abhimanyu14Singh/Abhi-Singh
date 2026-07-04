"""OpenSeesPy analysis engine for SkyFrame.

Translates the solver-agnostic :class:`~skyframe.core.model.BuildingModel`
into an OpenSees domain (ndm=3, ndf=6) and runs:

* linear static load cases (``run_static``),
* load combos by pure result superposition,
* eigenvalue / modal analysis (``run_modal``).

v0.2: shell regions are meshed (:mod:`skyframe.core.mesh`) into ShellMITC4
elements with ElasticMembranePlateSection; frame members are split so their
nodes match shell edge meshes (results are re-aggregated per ORIGINAL
member); member end releases map to elasticBeamColumn ``-releasez`` /
``-releasey`` codes; general MemberLoads (partial UDL / point / trapezoid,
several directions) are applied exactly — full-span UDLs through
``beamUniform``, point loads through ``beamPoint`` (unreleased elements
only: OpenSees does NOT condense beamPoint for released ends), and
everything else through exact consistent fixed-end forces (release-condensed
via the 12x12 local elastic stiffness) applied as reversed nodal loads with
Python-side member-end-force correction.  11 exact statics stations per
original member are reported per case.

The result objects (:class:`CaseResults`, :class:`ModalResults`,
:class:`AnalysisResults`) serialise via ``to_dict()`` to the exact JSON
shape documented in ``CONTRACT.md``.

Units follow the model everywhere: kN, m, tonne, s (E in kPa).
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core.mesh import MeshedModel, Segment, mesh_model
from skyframe.core.model import BuildingModel, FrameMember, LoadCase

_TOL = 1e-6
_N_STATIONS = 11

Vec3 = Tuple[float, float, float]

# 3-point Gauss-Legendre (exact through degree-5 polynomials): the
# consistent-load integrand (cubic Hermite x linear load) is quartic.
_GAUSS3 = ((-math.sqrt(3.0 / 5.0), 5.0 / 9.0),
           (0.0, 8.0 / 9.0),
           (math.sqrt(3.0 / 5.0), 5.0 / 9.0))

# span-load record layouts (segment-local x, member-local force components):
#   ("trap", (wx1, wy1, wz1), (wx2, wy2, wz2), xa, xb)   distributed, linear
#   ("point", (px, py, pz), x0)                          concentrated
SpanLoad = tuple


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
# exact member-load mechanics (Euler-Bernoulli, prismatic)
# --------------------------------------------------------------------------- #
def _local_stiffness(E: float, G: float, A: float, Iy: float, Iz: float,
                     J: float, L: float) -> np.ndarray:
    """12x12 local elastic stiffness of a 3D Euler beam element.

    DOF order per node: (ux, uy, uz, rx, ry, rz), end i then end j —
    matching elasticBeamColumn's local force ordering.
    """
    k = np.zeros((12, 12))
    ea, gj = E * A / L, G * J / L
    k[np.ix_((0, 6), (0, 6))] = ea * np.array([[1.0, -1.0], [-1.0, 1.0]])
    k[np.ix_((3, 9), (3, 9))] = gj * np.array([[1.0, -1.0], [-1.0, 1.0]])
    for (dofs, EI, s) in (((1, 5, 7, 11), E * Iz, 1.0),
                          ((2, 4, 8, 10), E * Iy, -1.0)):
        c = EI / L ** 3
        kb = c * np.array([
            [12.0, s * 6 * L, -12.0, s * 6 * L],
            [s * 6 * L, 4 * L * L, -s * 6 * L, 2 * L * L],
            [-12.0, -s * 6 * L, 12.0, -s * 6 * L],
            [s * 6 * L, 2 * L * L, -s * 6 * L, 4 * L * L]])
        k[np.ix_(dofs, dofs)] += kb
    return k


def _consistent_load(records: Sequence[SpanLoad], L: float) -> np.ndarray:
    """Consistent (work-equivalent) nodal load 12-vector for span loads.

    For an Euler element the Hermite-consistent load vector reproduces the
    EXACT fixed-end forces (Hermite cubics are the exact homogeneous
    solutions), so ``fixed-end forces = -_consistent_load(...)``.
    """
    f = np.zeros(12)

    def add_point(px: float, py: float, pz: float, x: float) -> None:
        xi = x / L
        n1 = 1.0 - 3.0 * xi ** 2 + 2.0 * xi ** 3
        n2 = x * (1.0 - xi) ** 2
        n3 = 3.0 * xi ** 2 - 2.0 * xi ** 3
        n4 = x * xi * (xi - 1.0)
        f[0] += px * (1.0 - xi)
        f[6] += px * xi
        f[1] += py * n1
        f[5] += py * n2
        f[7] += py * n3
        f[11] += py * n4
        f[2] += pz * n1
        f[4] -= pz * n2
        f[8] += pz * n3
        f[10] -= pz * n4

    for rec in records:
        if rec[0] == "point":
            (px, py, pz), x0 = rec[1], rec[2]
            add_point(px, py, pz, x0)
        else:
            w1, w2, xa, xb = rec[1], rec[2], rec[3], rec[4]
            span = xb - xa
            if span < 1e-12:
                continue
            for g, wt in _GAUSS3:
                x = 0.5 * (xa + xb) + 0.5 * span * g
                s = (x - xa) / span
                scale = 0.5 * span * wt
                add_point(scale * (w1[0] + (w2[0] - w1[0]) * s),
                          scale * (w1[1] + (w2[1] - w1[1]) * s),
                          scale * (w1[2] + (w2[2] - w1[2]) * s), x)
    return f


def _condensed_fef(records: Sequence[SpanLoad], L: float, E: float, G: float,
                   A: float, Iy: float, Iz: float, J: float,
                   released: Sequence[int]) -> np.ndarray:
    """Exact fixed-end force 12-vector of the ACTUAL element (with releases).

    Standard release condensation: with released DOFs R free of load
    (moment = 0) and the others clamped,
    ``f_rel = f_ff - K[:, R] @ K[R, R]^-1 @ f_ff[R]``.
    """
    f0 = -_consistent_load(records, L)
    rel = sorted(set(released))
    if rel:
        K = _local_stiffness(E, G, A, Iy, Iz, J, L)
        krr = K[np.ix_(rel, rel)]
        f0 = f0 - K[:, rel] @ np.linalg.solve(krr, f0[rel])
    return f0


def _section_forces(fi: Sequence[float], records: Sequence[SpanLoad],
                    x: float) -> Tuple[float, float, float, float, float, float]:
    """Internal forces (N, V2, V3, T, M2, M3) at distance ``x`` from end i.

    Exact statics of the piece [0, x]: ``fi`` are the (corrected) local
    forces acting ON the element at end i; ``records`` are the span loads.
    Sign convention: N positive in tension, M3 positive sagging for gravity
    on a horizontal member (local y up).
    """
    fx, fy, fz = fi[0], fi[1], fi[2]
    mx = fi[3]
    my = fi[4] + x * fi[2]
    mz = fi[5] - x * fi[1]
    for rec in records:
        if rec[0] == "point":
            (px, py, pz), x0 = rec[1], rec[2]
            if x0 <= x + 1e-9:
                fx += px
                fy += py
                fz += pz
                my += (x - x0) * pz
                mz += (x0 - x) * py
        else:
            w1, w2, xa, xb = rec[1], rec[2], rec[3], rec[4]
            q = min(x, xb)
            span = xb - xa
            if q <= xa + 1e-12 or span < 1e-12:
                continue
            s = (q - xa) / span
            wq = tuple(w1[k] + (w2[k] - w1[k]) * s for k in range(3))
            h = q - xa
            rx = 0.5 * (w1[0] + wq[0]) * h          # force resultants
            ry = 0.5 * (w1[1] + wq[1]) * h
            rz = 0.5 * (w1[2] + wq[2]) * h
            # first moments about the origin: int x' w(x') dx' over [xa, q]
            sy = h / 6.0 * (w1[1] * (2 * xa + q) + wq[1] * (xa + 2 * q))
            sz = h / 6.0 * (w1[2] * (2 * xa + q) + wq[2] * (xa + 2 * q))
            fx += rx
            fy += ry
            fz += rz
            my += x * rz - sz                        # int (x - x') wz dx'
            mz += sy - x * ry                        # int (x' - x) wy dx'
    return (-fx, -fy, -fz, -mx, -my, -mz)


# --------------------------------------------------------------------------- #
# assembled-domain bookkeeping
# --------------------------------------------------------------------------- #
@dataclass
class _Assembly:
    """Python-side maps for the OpenSees domain built from a BuildingModel."""

    mesh: Optional[MeshedModel] = None
    node_coords: Dict[int, Vec3] = field(default_factory=dict)   # all FE nodes
    struct_coords: Dict[int, Vec3] = field(default_factory=dict)  # excl. masters
    support_tags: List[int] = field(default_factory=list)
    seg_ele: Dict[Tuple[str, int], int] = field(default_factory=dict)
    ele_nodes: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    shell_quads: List[dict] = field(default_factory=list)
    masters: Dict[str, int] = field(default_factory=dict)         # story -> master
    story_nodes: Dict[str, List[int]] = field(default_factory=dict)
    mass_map: Dict[Tuple[int, int], float] = field(default_factory=dict)  # (tag, dof)
    node_restraints: Dict[int, Tuple[int, ...]] = field(default_factory=dict)
    use_transformation: bool = False

    def free_massed_dofs(self) -> int:
        """Number of massed (node, dof) pairs that are NOT restrained.

        This is the number of generalized eigenpairs the model actually has;
        masses lumped onto fully-fixed nodes contribute none.
        """
        none = (0,) * 6
        return sum(1 for (t, d) in self.mass_map
                   if not self.node_restraints.get(t, none)[d - 1])


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
    member_stations: Dict[str, Dict[str, List[float]]] = field(
        default_factory=dict)                   # uid -> x/N/V2/V3/T/M2/M3

    def to_dict(self) -> dict:
        return {
            "node_disp": {str(t): list(v) for t, v in self.node_disp.items()},
            "reactions": {str(t): list(v) for t, v in self.reactions.items()},
            "base": dict(self.base),
            "member_forces": {u: list(v) for u, v in self.member_forces.items()},
            "story": {s: dict(v) for s, v in self.story.items()},
            "member_stations": {u: {k: list(v) for k, v in st.items()}
                                for u, st in self.member_stations.items()},
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
    shell_quads: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "nodes": {str(t): list(c) for t, c in self.nodes.items()},
            "members": [dict(m) for m in self.members],
            "supports": [str(t) for t in self.supports],
            "story_order": list(self.story_order),
            "story_elev": dict(self.story_elev),
            "shell_quads": [dict(q) for q in self.shell_quads],
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
    single solution.  The model is treated as frozen once the engine is
    constructed (mesh and lookups are computed once).
    """

    def __init__(self, model: BuildingModel):
        self.model = model
        self._case_cache: Dict[str, CaseResults] = {}
        self._members_by_uid: Dict[str, FrameMember] = {m.uid: m for m in model.members}
        self._asm: Optional[_Assembly] = None
        self._mesh: Optional[MeshedModel] = None
        # per-case span-load bookkeeping, keyed by (parent uid, segment index)
        self._seg_span_loads: Dict[Tuple[str, int], List[SpanLoad]] = {}
        self._seg_fef: Dict[Tuple[str, int], np.ndarray] = {}

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
            shell_quads=list(asm.shell_quads),
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
        self._seg_span_loads = {}
        self._seg_fef = {}
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
        member_forces, member_stations = self._member_outputs(asm)
        story = self._story_results(asm, case, node_disp)

        result = CaseResults(case_name, node_disp, reactions, base,
                             member_forces, story, member_stations)
        self._case_cache[case_name] = result
        return result

    def run_modal(self, num_modes: Optional[int] = None) -> ModalResults:
        """Eigenvalue analysis: periods, frequencies, shapes, participation."""
        model = self.model
        asm = self._build()
        # only masses on unrestrained dofs yield generalized eigenpairs;
        # a model whose masses all sit on fixed nodes has no dynamics
        n_massed = asm.free_massed_dofs()
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
        """(Re)build the OpenSees domain from the (meshed) BuildingModel."""
        model = self.model
        if self._mesh is None:
            self._mesh = mesh_model(model)
        mesh = self._mesh

        ops.wipe()
        ops.model("basic", "-ndm", 3, "-ndf", 6)
        asm = _Assembly(mesh=mesh)

        # --- nodes: every deduped mesh point (frame endpoints, split points,
        #     shell mesh nodes); tag = pool index + 1 ----------------------
        tag = 0
        for i, p in enumerate(mesh.points):
            tag = i + 1
            ops.node(tag, *p)
            asm.node_coords[tag] = p
        asm.struct_coords = dict(asm.node_coords)

        # --- supports ----------------------------------------------------
        def record_fix(ntag: int, restr: Sequence[int]) -> None:
            prev = asm.node_restraints.get(ntag, (0,) * 6)
            asm.node_restraints[ntag] = tuple(
                int(bool(a) or bool(b)) for a, b in zip(prev, restr))

        if model.supports:
            for sup in model.supports:
                ntag = self._find_node(asm, sup.point)
                restr = [int(bool(r)) for r in sup.restraints]
                ops.fix(ntag, *restr)
                asm.support_tags.append(ntag)
                record_fix(ntag, restr)
        elif asm.struct_coords:
            z_min = min(c[2] for c in asm.struct_coords.values())
            restr = ([1, 1, 1, 1, 1, 1] if model.base_fixity == "fixed"
                     else [1, 1, 1, 0, 0, 0])
            for ntag, c in asm.struct_coords.items():
                if abs(c[2] - z_min) < _TOL:
                    ops.fix(ntag, *restr)
                    asm.support_tags.append(ntag)
                    record_fix(ntag, restr)

        # --- frame elements (one per mesh segment) -------------------------
        # rot_presence accumulates each node's 3x3 rotational-stiffness
        # pattern so rotations left unstiffened by moment releases can be
        # auto-restrained (see below).
        etag = 0
        rot_presence: Dict[int, np.ndarray] = {}
        released_nodes: set = set()

        def rot_add(nidx: int, mat3: np.ndarray) -> None:
            rot_presence[nidx] = rot_presence.get(nidx, np.zeros((3, 3))) + mat3

        eye3 = np.eye(3)
        for m in model.members:
            sec = model.sections[m.section]
            mat = model.materials[sec.material]
            xax, _, _, vecxz, _ = _local_axes(m)
            toks = m.release_tokens()
            segs = mesh.segments[m.uid]
            last = len(segs) - 1
            torsion_only = np.outer(xax, xax)
            for seg in segs:
                etag += 1
                ops.geomTransf("Linear", etag, *vecxz)
                rel_i = "Mi" in toks and seg.index == 0
                rel_j = "Mj" in toks and seg.index == last
                code = (1 if rel_i else 0) + (2 if rel_j else 0)
                extra = ["-releasez", code, "-releasey", code] if code else []
                ops.element("elasticBeamColumn", etag,
                            seg.ni + 1, seg.nj + 1,
                            sec.A, mat.E, mat.G, sec.J, sec.I22, sec.I33,
                            etag, *extra)
                asm.seg_ele[(m.uid, seg.index)] = etag
                for node, released in ((seg.ni, rel_i), (seg.nj, rel_j)):
                    rot_add(node, torsion_only if released else eye3)
                    if released:
                        released_nodes.add(node)
            asm.ele_nodes[m.uid] = (segs[0].ni + 1, segs[-1].nj + 1)

        # --- shell elements -------------------------------------------------
        if mesh.quads:
            sec_tags: Dict[str, int] = {}
            stag = 0
            for name, ssec in model.shell_sections.items():
                stag += 1
                mat = model.materials[ssec.material]
                # rho = 0: shell self-weight/mass ignored in v0.2
                ops.section("ElasticMembranePlateSection", stag,
                            mat.E, mat.nu, ssec.thickness, 0.0)
                sec_tags[name] = stag
            regions = {r.uid: r for r in model.shells}
            for quad in mesh.quads:
                etag += 1
                node_tags = [n + 1 for n in quad.nodes]
                region = regions[quad.region]
                ops.element("ShellMITC4", etag, *node_tags,
                            sec_tags[region.section])
                asm.shell_quads.append({"region": quad.region,
                                        "nodes": node_tags})
                for n in quad.nodes:
                    rot_add(n, eye3)   # shells stiffen all three rotations

        # --- auto-restrain rotations left unstiffened by releases ----------
        # A node attached ONLY through moment-released member ends has zero
        # stiffness about axes perpendicular to those members: OpenSees would
        # see a singular system.  Like ETABS, hold the NODE rotation (the
        # member end still rotates freely behind its release).  rz of
        # prospective rigid-diaphragm slaves is left to the diaphragm tie.
        if released_nodes:
            prospective_slaves: set = set()
            if model.rigid_diaphragms:
                for s in model.stories:
                    plane = [t for t, c in asm.struct_coords.items()
                             if abs(c[2] - s.elevation) < _TOL]
                    if len(plane) >= 2:
                        prospective_slaves.update(plane)
            for nidx in sorted(released_nodes):
                ntag = nidx + 1
                S = rot_presence[nidx]
                prev = asm.node_restraints.get(ntag, (0,) * 6)
                scale_ = max(float(np.trace(S)), 1.0)
                newfix = [0] * 6
                for g in range(3):
                    dof = 3 + g
                    if prev[dof]:
                        continue
                    if dof == 5 and ntag in prospective_slaves:
                        continue
                    if S[g, g] < 1e-9 * scale_:
                        newfix[dof] = 1
                if any(newfix):
                    ops.fix(ntag, *newfix)
                    record_fix(ntag, newfix)

        # --- story node sets ----------------------------------------------
        # All FE nodes in the story plane join the set: frame nodes, slab
        # mesh nodes, and wall nodes lying exactly at the story elevation.
        # Wall interior nodes (between story elevations) are excluded by z.
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
                asm.node_restraints[tag] = (0, 0, 1, 1, 1, 0)
        asm.use_transformation = bool(asm.masters)

        # --- zero-free-DOF guard --------------------------------------------
        # A model whose every node is fully restrained (e.g. a single
        # fixed-fixed beam loaded through eleLoads) yields an empty SOE,
        # which hard-crashes every OpenSees linear solver.  Add one benign,
        # well-conditioned spring DOF (u = 0 under zero load) so the solve
        # is regular; the dummy nodes are excluded from all result maps.
        fully_fixed = {t for t, r in asm.node_restraints.items() if all(r)}
        if (not asm.masters and asm.struct_coords
                and all(t in fully_fixed for t in asm.struct_coords)):
            p0 = next(iter(asm.struct_coords.values()))
            d1, d2 = tag + 1, tag + 2
            tag += 2
            ops.node(d1, *p0)
            ops.node(d2, *p0)
            ops.fix(d1, 1, 1, 1, 1, 1, 1)
            ops.fix(d2, 0, 1, 1, 1, 1, 1)
            ops.uniaxialMaterial("Elastic", 1, 1.0)
            etag += 1
            ops.element("zeroLength", etag, d1, d2, "-mat", 1, "-dir", 1)

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
            self._apply_member_load(asm, member, "udl", udl.w * scale, 0.0,
                                    0.0, 1.0, "gravity")

        for ml in pat.member_loads:
            member = self._members_by_uid.get(ml.member_uid)
            if member is None:
                raise ValueError(f"Member load references unknown member "
                                 f"{ml.member_uid!r}")
            self._apply_member_load(asm, member, ml.kind, ml.w * scale,
                                    ml.w2 * scale, ml.a, ml.b, ml.direction)

        for al in pat.area_loads:
            self._apply_area_load(asm, al.region_uid, al.q * scale)

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

    # ----------------------------------------------- member load machinery
    def _apply_member_load(self, asm: _Assembly, member: FrameMember,
                           kind: str, w: float, w2: float, a: float, b: float,
                           direction: str) -> None:
        """Apply one (already case-scaled) MemberLoad to a member's segments.

        * full-span UDL  -> ``beamUniform`` per segment (OpenSees condenses
          the uniform load correctly for released ends — verified);
        * point          -> ``beamPoint`` on unreleased segments; condensed
          FEF path on released segments (OpenSees does NOT condense
          beamPoint); loads at segment nodes go straight to the node;
        * partial UDL / trapezoid -> exact condensed fixed-end forces
          applied as reversed nodal loads.

        Every applied load is recorded per segment (member-local components,
        segment-local x) for the exact statics station results, and every
        FEF-path load also feeds the member end-force correction.
        """
        xax, yax, zax, _, vertical = _local_axes(member)
        if direction == "gravity" and vertical:
            warnings.warn(f"Gravity load on vertical member {member.uid!r} "
                          "is purely axial; skipped")
            return
        if direction == "local_y":
            comps: Vec3 = (0.0, 1.0, 0.0)
            dvec = yax
        else:
            dvec = {"gravity": (0.0, 0.0, -1.0), "global_x": (1.0, 0.0, 0.0),
                    "global_y": (0.0, 1.0, 0.0),
                    "global_z": (0.0, 0.0, 1.0)}[direction]
            comps = (_dot(dvec, xax), _dot(dvec, yax), _dot(dvec, zax))

        segs = asm.mesh.segments[member.uid]
        last = len(segs) - 1
        toks = member.release_tokens()

        def seg_release(seg: Segment) -> List[int]:
            rel: List[int] = []
            if "Mi" in toks and seg.index == 0:
                rel += [4, 5]
            if "Mj" in toks and seg.index == last:
                rel += [10, 11]
            return rel

        L = member.length
        if kind == "point":
            self._apply_point_load(asm, member, segs, seg_release, comps,
                                   dvec, w, a * L)
            return

        if kind == "udl" and a <= _TOL and b >= 1.0 - _TOL:
            # full-span uniform: exact through OpenSees internal load
            for seg in segs:
                etag = asm.seg_ele[(member.uid, seg.index)]
                ops.eleLoad("-ele", etag, "-type", "-beamUniform",
                            comps[1] * w, comps[2] * w, comps[0] * w)
                self._record(member.uid, seg.index,
                             ("trap",
                              (comps[0] * w, comps[1] * w, comps[2] * w),
                              (comps[0] * w, comps[1] * w, comps[2] * w),
                              0.0, seg.length))
            return

        # partial UDL / trapezoid: exact condensed fixed-end forces
        xa_p, xb_p = a * L, b * L
        if xb_p - xa_p < 1e-12:
            return
        wa, wb = (w, w) if kind == "udl" else (w, w2)
        for seg in segs:
            lo = max(xa_p, seg.x0)
            hi = min(xb_p, seg.x0 + seg.length)
            if hi - lo < 1e-12:
                continue
            w_lo = wa + (wb - wa) * (lo - xa_p) / (xb_p - xa_p)
            w_hi = wa + (wb - wa) * (hi - xa_p) / (xb_p - xa_p)
            rec: SpanLoad = ("trap",
                             (comps[0] * w_lo, comps[1] * w_lo, comps[2] * w_lo),
                             (comps[0] * w_hi, comps[1] * w_hi, comps[2] * w_hi),
                             lo - seg.x0, hi - seg.x0)
            self._apply_fef(asm, member, seg, [rec], seg_release(seg))

    def _apply_point_load(self, asm: _Assembly, member: FrameMember,
                          segs: List[Segment], seg_release, comps: Vec3,
                          dvec: Vec3, p: float, x_p: float) -> None:
        """Concentrated load P at distance x_p from the parent's end i."""
        seg = None
        for s in segs:
            if s.x0 - 1e-9 <= x_p <= s.x0 + s.length + 1e-9:
                seg = s
                break
        if seg is None:
            seg = segs[-1]
        xi = min(max(x_p - seg.x0, 0.0), seg.length)
        if xi < _TOL or xi > seg.length - _TOL:
            # load lands on a node: apply it there directly (global coords)
            node = seg.ni if xi < _TOL else seg.nj
            ops.load(node + 1, dvec[0] * p, dvec[1] * p, dvec[2] * p,
                     0.0, 0.0, 0.0)
            return
        rec: SpanLoad = ("point", (comps[0] * p, comps[1] * p, comps[2] * p),
                         xi)
        released = seg_release(seg)
        if released:
            # OpenSees does not condense beamPoint for released ends
            self._apply_fef(asm, member, seg, [rec], released)
        else:
            etag = asm.seg_ele[(member.uid, seg.index)]
            ops.eleLoad("-ele", etag, "-type", "-beamPoint",
                        comps[1] * p, comps[2] * p, xi / seg.length,
                        comps[0] * p)
            self._record(member.uid, seg.index, rec)

    def _apply_fef(self, asm: _Assembly, member: FrameMember, seg: Segment,
                   records: List[SpanLoad], released: Sequence[int]) -> None:
        """Apply span loads as reversed condensed fixed-end nodal forces."""
        model = self.model
        sec = model.sections[member.section]
        mat = model.materials[sec.material]
        f0 = _condensed_fef(records, seg.length, mat.E, mat.G, sec.A,
                            sec.I22, sec.I33, sec.J, released)
        xax, yax, zax, _, _ = _local_axes(member)

        def to_global(lx: float, ly: float, lz: float) -> Vec3:
            return (lx * xax[0] + ly * yax[0] + lz * zax[0],
                    lx * xax[1] + ly * yax[1] + lz * zax[1],
                    lx * xax[2] + ly * yax[2] + lz * zax[2])

        for node, ofs in ((seg.ni, 0), (seg.nj, 6)):
            fg = to_global(-f0[ofs], -f0[ofs + 1], -f0[ofs + 2])
            mg = to_global(-f0[ofs + 3], -f0[ofs + 4], -f0[ofs + 5])
            ops.load(node + 1, *fg, *mg)

        key = (member.uid, seg.index)
        self._seg_fef[key] = self._seg_fef.get(key, np.zeros(12)) + f0
        for rec in records:
            self._record(member.uid, seg.index, rec)

    def _record(self, uid: str, seg_index: int, rec: SpanLoad) -> None:
        self._seg_span_loads.setdefault((uid, seg_index), []).append(rec)

    def _apply_area_load(self, asm: _Assembly, region_uid: str,
                         q: float) -> None:
        """Area load q (kPa, positive down, already case-scaled)."""
        region = self.model._shell(region_uid)
        if region is None:
            raise ValueError(f"Area load references unknown shell region "
                             f"{region_uid!r}")
        mesh = asm.mesh
        if region.behavior == "shell":
            for pidx, trib_area in mesh.region_trib[region_uid].items():
                ops.load(pidx + 1, 0.0, 0.0, -q * trib_area, 0.0, 0.0, 0.0)
            return
        # membrane: pre-computed two-way tributary loads (per unit q)
        for tl in mesh.membrane_loads[region_uid]:
            member = self._members_by_uid[tl.member_uid]
            self._apply_member_load(asm, member, "trapezoid", tl.w1 * q,
                                    tl.w2 * q, tl.a, tl.b, "gravity")
        for pidx, w_node in mesh.membrane_nodal[region_uid].items():
            ops.load(pidx + 1, 0.0, 0.0, -q * w_node, 0.0, 0.0, 0.0)

    # ----------------------------------------------- per-member results
    def _member_outputs(self, asm: _Assembly
                        ) -> Tuple[Dict[str, List[float]],
                                   Dict[str, Dict[str, List[float]]]]:
        """End forces + 11-station internal forces per ORIGINAL member.

        End forces of a split member are the (corrected) end-i forces of its
        first segment and end-j forces of its last segment.  Station forces
        come from exact statics: corrected segment end-i forces plus the
        recorded span loads integrated in closed form.
        """
        member_forces: Dict[str, List[float]] = {}
        member_stations: Dict[str, Dict[str, List[float]]] = {}
        for m in self.model.members:
            segs = asm.mesh.segments[m.uid]
            corrected: Dict[int, List[float]] = {}
            for seg in segs:
                etag = asm.seg_ele[(m.uid, seg.index)]
                f = ops.eleResponse(etag, "localForce")
                fef = self._seg_fef.get((m.uid, seg.index))
                if fef is not None:
                    f = [float(v + c) for v, c in zip(f, fef)]
                else:
                    f = [float(v) for v in f]
                corrected[seg.index] = f
            last = len(segs) - 1
            member_forces[m.uid] = corrected[0][:6] + corrected[last][6:]

            L = m.length
            xs = [k * L / (_N_STATIONS - 1) for k in range(_N_STATIONS)]
            cols: Dict[str, List[float]] = {k: [] for k in
                                            ("N", "V2", "V3", "T", "M2", "M3")}
            for x in xs:
                seg = None
                for s in segs:
                    if s.x0 - 1e-9 <= x <= s.x0 + s.length + 1e-9:
                        seg = s
                        break
                if seg is None:            # numerical safety net
                    seg = segs[-1]
                xi = min(max(x - seg.x0, 0.0), seg.length)
                recs = self._seg_span_loads.get((m.uid, seg.index), ())
                vals = _section_forces(corrected[seg.index][:6], recs, xi)
                for key, v in zip(("N", "V2", "V3", "T", "M2", "M3"), vals):
                    cols[key].append(float(v))
            member_stations[m.uid] = {"x": xs, **cols}
        return member_forces, member_stations

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
        """Cumulative applied lateral force at & above each story (kN).

        Area loads and gravity member loads are vertical and do not enter;
        lateral loads applied through shell nodes arrive as nodal loads or
        story forces and are already counted.
        """
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
        member_stations: Dict[str, Dict[str, List[float]]] = {}
        for uid, st0 in parts[0][0].member_stations.items():
            entry: Dict[str, List[float]] = {"x": list(st0["x"])}
            for key in ("N", "V2", "V3", "T", "M2", "M3"):
                entry[key] = [
                    sum(f * res.member_stations[uid][key][i]
                        for res, f in parts)
                    for i in range(len(st0[key]))]
            member_stations[uid] = entry
        return CaseResults(
            name=name,
            node_disp=comb_vecs(lambda r: r.node_disp),
            reactions=comb_vecs(lambda r: r.reactions),
            base=base,
            member_forces=comb_vecs(lambda r: r.member_forces),
            story=story,
            member_stations=member_stations,
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
