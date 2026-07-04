"""OpenSeesPy analysis engine for SkyFrame.

Translates the solver-agnostic :class:`~skyframe.core.model.BuildingModel`
into an OpenSees domain (ndm=3, ndf=6) and runs:

* linear static load cases (``run_static``),
* load combos by pure result superposition (``combo_type == "add"``) or by
  per-quantity min/max envelopes over the listed cases (v0.4
  ``combo_type == "envelope"``),
* eigenvalue / modal analysis (``run_modal``),
* response-spectrum analysis (``run_response_spectrum``, v0.3),
* P-Delta static cases (``LoadCase.pdelta``, v0.3),
* linear time-history cases (``run_time_history``, v0.4): Newmark
  constant-average-acceleration direct integration of the elastic model
  under uniform ground excitation, Rayleigh damping fitted at modes 1 and
  min(3, n),
* nonlinear static pushover cases (``run_pushover``, v0.5):
  displacement-controlled push on the roof control DOF after a held
  gravity stage, with bilinear (Steel01) zeroLength rotational hinge
  springs at the locations a :class:`PushoverCase` names (see
  ``CONTRACT.md`` v0.5 for the exact stiff-hinge idealization),
* staged-construction cases (``run_staged``, v0.6): sequential
  story-by-story gravity by the REBUILD-AND-ACCUMULATE method — at stage
  k a fresh model of stories 1..k is solved under only story k's gravity
  loads and the increments are accumulated; an internal one-shot solve of
  the same total loads feeds the reported comparison,
* nonlinear time-history cases (``TimeHistoryCase.nonlinear``, v0.6):
  the v0.5 pushover hinge machinery (Steel01 zeroLength springs) under
  Newmark + Newton (``NormDispIncr 1e-8, 25``; NewtonLineSearch retry),
  optional held gravity stage, Rayleigh damping fitted to the INITIAL
  elastic modes and applied with committed-stiffness proportionality
  (``betaKcomm`` — avoids spurious hinge damping moments).

v0.5 also adds: shell-region OPENINGS (meshed around, exact net-area load
conservation — handled in :mod:`skyframe.core.mesh`), the per-story
``diaphragm`` "rigid"|"none" option (semi-rigid = "none" + a meshed shell
slab), and LINK elements (zeroLength springs on global axes; link-only
nodes get their zero-stiffness DOFs auto-restrained).

v0.4 additions in results: frame stiffness modifiers (``FrameSection.mod_*``)
and the shell modifier (``ShellSection.mod``, scales E) are applied when the
elements are created; member local axes honour ``FrameMember.angle``
(rotation about the member axis, degrees); shell stress resultants
(gauss-point averages, ``shell_forces``) are reported per static case and
superposed into additive combos (envelope combos and RS/TH cases skip them).

v0.3 response-spectrum analysis is EXACT modal statics: for each mode i the
equivalent static force vector ``f_i = Gamma_i * Sa_i * g * M * phi_i`` is
applied as nodal loads and solved through the ordinary linear static
pipeline, so every reported quantity (displacements, reactions, member
forces, stations, story values) is the true modal response
``r_i = Gamma_i * Sa_i * g / omega_i^2`` on that quantity's influence line.
Modal responses are then combined per quantity with CQC (constant damping
ratio; standard correlation coefficient) or SRSS — positive envelopes.

v0.3 P-Delta cases use ``geomTransf('PDelta', ...)`` on ALL frame members
(the linearized "lean-column" geometric stiffness ``-P/L`` on the transverse
sway DOFs) and a Newton solve: the gravity state (``pdelta_gravity`` or, if
``None``, the case's own patterns) is applied first, held constant
(``loadConst``), then the case's own loads are solved on the gravity-
stiffened geometry; two-stage runs report the case's INCREMENTAL response.

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
from skyframe.core.model import (G_ACCEL, BuildingModel, FrameMember,
                                 FrameSection, LoadCase, LoadCombo,
                                 LoadPattern, ResponseSpectrumCase,
                                 ShellRegion)

# time-history step cap for engine.run(): if the model's TH cases together
# exceed this many integration steps they are skipped in run() (a warning is
# carried in the results); run_time_history() itself is never capped.
TH_STEP_CAP = 20000

# v0.5 pushover: run() skips ALL pushover cases (with a results warning)
# when their combined step count exceeds this cap; run_pushover() itself is
# never capped.
PUSHOVER_STEP_CAP = 2000

# v0.5 stiff-hinge idealization factor n: the zeroLength hinge spring's
# elastic rotational stiffness is k_theta = n * 6EI/L about each bending
# axis (elastic series softening of the member-end rotational stiffness
# 6EI/L is the factor n/(n+1) ~ 0.909 at n = 10); Steel01 hardening ratio
# b = h / (n + 1 - h*n) makes the member-end SERIES post-yield/elastic
# stiffness ratio exactly the case's ``hardening`` h.
HINGE_STIFFNESS_FACTOR = 10.0

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

    v0.4: ``member.angle`` (degrees) rotates the default y/z pair about the
    member axis (right-hand rule about local +x); the rotated local z serves
    as vecxz (it lies in the local x-z plane by construction).
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
    ang = getattr(member, "angle", 0.0) or 0.0
    if abs(ang) > 1e-12:
        a = math.radians(ang)
        ca, sa = math.cos(a), math.sin(a)
        y2 = (ca * y[0] + sa * z[0], ca * y[1] + sa * z[1],
              ca * y[2] + sa * z[2])
        z2 = (ca * z[0] - sa * y[0], ca * z[1] - sa * y[1],
              ca * z[2] - sa * y[2])
        y, z = _unit(y2), _unit(z2)
        vecxz = z
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
# response-spectrum helpers
# --------------------------------------------------------------------------- #
def _interp_spectrum(spectrum: Sequence[Sequence[float]], T: float) -> float:
    """Sa(T) by LINEAR interpolation of [[T, Sa], ...], clamped at the ends."""
    pts = sorted((float(p[0]), float(p[1])) for p in spectrum)
    if T <= pts[0][0]:
        return pts[0][1]
    if T >= pts[-1][0]:
        return pts[-1][1]
    for (t0, s0), (t1, s1) in zip(pts, pts[1:]):
        if t0 <= T <= t1:
            if t1 - t0 < 1e-12:
                return s1
            return s0 + (s1 - s0) * (T - t0) / (t1 - t0)
    return pts[-1][1]  # pragma: no cover - unreachable


def _cqc_matrix(omegas: Sequence[float], zeta: float) -> np.ndarray:
    """CQC cross-correlation matrix for constant modal damping ``zeta``.

    Standard Der Kiureghian coefficient (beta = omega_i / omega_j):
    ``rho_ij = 8 z^2 (1+b) b^1.5 / ((1-b^2)^2 + 4 z^2 b (1+b)^2)``;
    ``rho_ii = 1``.
    """
    n = len(omegas)
    rho = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            b = omegas[i] / omegas[j]
            num = 8.0 * zeta * zeta * (1.0 + b) * b ** 1.5
            den = (1.0 - b * b) ** 2 + 4.0 * zeta * zeta * b * (1.0 + b) ** 2
            rho[i, j] = rho[j, i] = num / den
    return rho


def _modal_combine(values: Sequence[float], rho: np.ndarray) -> float:
    """Positive CQC/SRSS envelope of one quantity's per-mode values."""
    v = np.asarray(values)
    return float(math.sqrt(max(float(v @ rho @ v), 0.0)))


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
    quad_ele: List[int] = field(default_factory=list)   # etag per shell quad
    masters: Dict[str, int] = field(default_factory=dict)         # story -> master
    story_nodes: Dict[str, List[int]] = field(default_factory=dict)
    mass_map: Dict[Tuple[int, int], float] = field(default_factory=dict)  # (tag, dof)
    node_restraints: Dict[int, Tuple[int, ...]] = field(default_factory=dict)
    use_transformation: bool = False
    hinge_ele: Dict[Tuple[str, str], int] = field(default_factory=dict)
    #   v0.5 pushover: (member uid, "i"|"j") -> zeroLength hinge element tag
    hinge_dup_of: Dict[int, int] = field(default_factory=dict)
    #   v0.5 pushover: duplicated hinge node tag -> original node tag
    hinge_rot_yield: Dict[Tuple[str, str], Tuple[float, float]] = field(
        default_factory=dict)
    #   v0.6: (uid, end) -> (My/k22, My/k33) yield rotations of the hinge
    #   springs about the local y and z bending axes
    link_ele: Dict[str, int] = field(default_factory=dict)   # v0.5 links

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
    warning: str = ""                           # e.g. P-Delta superposition
    shell_forces: Dict[int, List[float]] = field(default_factory=dict)
    #   v0.4: quad index (into shell_quads) -> 8 gauss-averaged stress
    #   resultants [Nxx, Nyy, Nxy, Mxx, Myy, Mxy, Vxz, Vyz] (kN/m, kN*m/m)
    minima: Optional["CaseResults"] = None      # v0.4 envelope combos: minima

    def to_dict(self) -> dict:
        d = {
            "node_disp": {str(t): list(v) for t, v in self.node_disp.items()},
            "reactions": {str(t): list(v) for t, v in self.reactions.items()},
            "base": dict(self.base),
            "member_forces": {u: list(v) for u, v in self.member_forces.items()},
            "story": {s: dict(v) for s, v in self.story.items()},
            "member_stations": {u: {k: list(v) for k, v in st.items()}
                                for u, st in self.member_stations.items()},
        }
        if self.shell_forces:
            d["shell_forces"] = {str(i): list(v)
                                 for i, v in self.shell_forces.items()}
        if self.minima is not None:
            d["min"] = self.minima.to_dict()
        if self.warning:
            d["warning"] = self.warning
        return d


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
class THResults:
    """Results of one time-history case (v0.4 linear, v0.6 nonlinear).

    Time series are sampled AFTER each integration step, i.e. entry k is
    the state at t = (k+1)*dt.  ``base_FX``/``base_FY`` are total base
    reactions (element resisting forces at the supports).  Story shears in
    ``peaks`` come from inertia-force equilibrium (story masses times total
    accelerations, cumulative from the top; damping forces neglected).

    v0.6 nonlinear cases additionally report ``hinge_rotations`` (peak
    absolute hinge spring rotation per member uid across both bending axes
    and all steps, rad) and ``yielded`` (uids of members whose hinge
    exceeded its yield rotation My/k_theta about either axis); with a
    ``gravity`` stage all series are the response PAST the gravity state.
    Both keys appear in ``to_dict()`` only for nonlinear cases (the v0.4
    linear shape is unchanged).
    """

    name: str
    t: List[float]
    story_ux: Dict[str, List[float]]
    story_uy: Dict[str, List[float]]
    base_FX: List[float]
    base_FY: List[float]
    peaks: dict           # {"story": {story: {ux..shear_y}}, "base": {FX,FY}}
    nonlinear: bool = False                                     # v0.6
    hinge_rotations: Dict[str, float] = field(default_factory=dict)  # v0.6
    yielded: List[str] = field(default_factory=list)            # v0.6

    def to_dict(self) -> dict:
        d = {
            "t": list(self.t),
            "story_ux": {s: list(v) for s, v in self.story_ux.items()},
            "story_uy": {s: list(v) for s, v in self.story_uy.items()},
            "base_FX": list(self.base_FX),
            "base_FY": list(self.base_FY),
            "peaks": {
                "story": {s: dict(v)
                          for s, v in self.peaks.get("story", {}).items()},
                "base": dict(self.peaks.get("base", {})),
            },
        }
        if self.nonlinear:
            d["hinge_rotations"] = {u: float(r) for u, r in
                                    self.hinge_rotations.items()}
            d["yielded"] = list(self.yielded)
        return d


@dataclass
class PushoverResults:
    """Results of one nonlinear static pushover case (v0.5).

    Per converged step: roof (control DOF) displacement past the gravity
    state (m), total base shear = -(sum of support reactions in the push
    direction, gravity share subtracted) (kN), and the roof drift ratio
    (roof displacement / roof elevation).  ``hinge_rotations`` holds the
    peak absolute hinge spring rotation per member uid across both bending
    axes and all steps (rad).  A non-converged step stops the run early:
    the partial curve is returned and a warning is appended.
    """

    name: str
    roof_disp: List[float]
    base_shear: List[float]
    roof_drift: List[float]
    hinge_rotations: Dict[str, float]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "roof_disp": list(self.roof_disp),
            "base_shear": list(self.base_shear),
            "roof_drift": list(self.roof_drift),
            "hinge_rotations": {u: float(r)
                                for u, r in self.hinge_rotations.items()},
            "warnings": list(self.warnings),
        }


@dataclass
class StagedResults:
    """Results of one staged-construction case (v0.6).

    ``case`` holds the ACCUMULATED final state (per-stage increments summed
    across the sequential story-by-story analysis, plus the unstaged
    ``include_live`` increment) in the standard static-case shape;
    ``oneshot`` is the internally-solved one-shot application of the same
    total loads on the full structure.  ``column_axial_max_diff_pct`` is
    the maximum |N_staged - N_oneshot| over all column end-i axial forces,
    as a percentage of the largest one-shot column axial (0.0 when the
    model has no columns or all one-shot column axials are zero).
    """

    name: str
    case: CaseResults
    oneshot: CaseResults
    column_axial_max_diff_pct: float

    def to_dict(self) -> dict:
        d = self.case.to_dict()
        d["comparison"] = {
            "column_axial_max_diff_pct":
                float(self.column_axial_max_diff_pct),
            "oneshot_case": self.oneshot.to_dict(),
        }
        return d


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
    rs_cases: Dict[str, CaseResults] = field(default_factory=dict)
    th_cases: Dict[str, THResults] = field(default_factory=dict)
    pushover: Dict[str, PushoverResults] = field(default_factory=dict)
    staged: Dict[str, StagedResults] = field(default_factory=dict)  # v0.6
    warning: str = ""                    # e.g. TH cases skipped (step cap)

    def to_dict(self) -> dict:
        d = {
            "model_name": self.model_name,
            "nodes": {str(t): list(c) for t, c in self.nodes.items()},
            "members": [dict(m) for m in self.members],
            "supports": [str(t) for t in self.supports],
            "story_order": list(self.story_order),
            "story_elev": dict(self.story_elev),
            "shell_quads": [dict(q) for q in self.shell_quads],
            "cases": {n: c.to_dict() for n, c in self.cases.items()},
            "combos": {n: c.to_dict() for n, c in self.combos.items()},
            "rs_cases": {n: c.to_dict() for n, c in self.rs_cases.items()},
            "th_cases": {n: c.to_dict() for n, c in self.th_cases.items()},
            "pushover": {n: p.to_dict() for n, p in self.pushover.items()},
            "staged": {n: s.to_dict() for n, s in self.staged.items()},
            "modal": self.modal.to_dict(),
        }
        if self.warning:
            d["warning"] = self.warning
        return d


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
        self._rs_cache: Dict[str, CaseResults] = {}
        self._th_cache: Dict[str, THResults] = {}
        self._po_cache: Dict[str, PushoverResults] = {}
        self._staged_cache: Dict[str, StagedResults] = {}
        self._modal_cache: Dict[int, ModalResults] = {}
        self._members_by_uid: Dict[str, FrameMember] = {m.uid: m for m in model.members}
        self._asm: Optional[_Assembly] = None
        self._mesh: Optional[MeshedModel] = None
        # per-case span-load bookkeeping, keyed by (parent uid, segment index)
        self._seg_span_loads: Dict[Tuple[str, int], List[SpanLoad]] = {}
        self._seg_fef: Dict[Tuple[str, int], np.ndarray] = {}

    # ------------------------------------------------------------------ API
    def run(self) -> AnalysisResults:
        """Run every load case, combo, RS case, TH case, and modal analysis.

        v0.4: time-history cases are skipped (with a top-level results
        warning) when their total step count exceeds ``TH_STEP_CAP``.
        """
        model = self.model
        cases = {name: self.run_static(name) for name in model.cases}
        combos = {name: self._combine(name, combo)
                  for name, combo in model.combos.items()}
        modal = self.run_modal()
        rs_cases = {name: self.run_response_spectrum(name)
                    for name in model.rs_cases}
        th_cases: Dict[str, THResults] = {}
        warning = ""
        if model.th_cases:
            total_steps = sum(len(c.accel) for c in model.th_cases.values())
            if total_steps > TH_STEP_CAP:
                warning = (f"time-history cases skipped: {total_steps} total "
                           f"integration steps exceed the {TH_STEP_CAP}-step "
                           "cap (run them individually with "
                           "run_time_history)")
            else:
                th_cases = {name: self.run_time_history(name)
                            for name in model.th_cases}
        pushover: Dict[str, PushoverResults] = {}
        if model.pushover_cases:
            total_po = sum(c.steps for c in model.pushover_cases.values())
            if total_po > PUSHOVER_STEP_CAP:
                po_warn = (f"pushover cases skipped: {total_po} combined "
                           f"steps exceed the {PUSHOVER_STEP_CAP}-step cap "
                           "(run them individually with run_pushover)")
                warning = f"{warning}; {po_warn}" if warning else po_warn
            else:
                pushover = {name: self.run_pushover(name)
                            for name in model.pushover_cases}
        staged = {name: self.run_staged(name)
                  for name in model.staged_cases}
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
            rs_cases=rs_cases,
            th_cases=th_cases,
            pushover=pushover,
            staged=staged,
            warning=warning,
        )

    def run_static(self, case_name: str) -> CaseResults:
        """Solve one static load case (cached per engine instance)."""
        if case_name in self._case_cache:
            return self._case_cache[case_name]
        model = self.model
        if case_name not in model.cases:
            raise ValueError(f"Unknown load case {case_name!r}")
        case = model.cases[case_name]
        if case.pdelta:
            result = self._run_static_pdelta(case)
            self._case_cache[case_name] = result
            return result

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
        shell_forces = self._shell_outputs(asm)

        result = CaseResults(case_name, node_disp, reactions, base,
                             member_forces, story, member_stations,
                             shell_forces=shell_forces)
        self._case_cache[case_name] = result
        return result

    def run_modal(self, num_modes: Optional[int] = None) -> ModalResults:
        """Eigenvalue analysis: periods, frequencies, shapes, participation.

        Cached per effective mode count so RS cases reuse the eigen solve.
        """
        model = self.model
        asm = self._build()
        # only masses on unrestrained dofs yield generalized eigenpairs;
        # a model whose masses all sit on fixed nodes has no dynamics
        n_massed = asm.free_massed_dofs()
        n = min(num_modes or model.num_modes, n_massed)
        if n <= 0:
            return ModalResults([], [], [], {})
        if n in self._modal_cache:
            return self._modal_cache[n]

        self._setup_analysis(asm)
        lambdas = self._solve_eigen(n, n_massed)
        periods = [2.0 * math.pi / math.sqrt(lam) for lam in lambdas]
        frequencies = [1.0 / t for t in periods]
        shapes = {mode: {t: list(ops.nodeEigenvector(t, mode))
                         for t in asm.node_coords}
                  for mode in range(1, n + 1)}
        participation = self._participation(asm, periods, shapes)
        result = ModalResults(periods, frequencies, participation, shapes)
        self._modal_cache[n] = result
        return result

    # --------------------------------------------------------- model assembly
    def _hinge_plan(self, case) -> Dict[Tuple[str, str], float]:
        """(member uid, "i"|"j") -> yield moment for a pushover case (v0.5).

        ``column_base``: hinge at the LOWER end of every eligible column;
        ``all_ends``: hinges at both ends of every eligible member.  A
        member is eligible when ``case.My`` names it or ``default_My`` is
        set (columns only for ``column_base``); all other members stay
        elastic — no hinge is inserted.
        """
        plan: Dict[Tuple[str, str], float] = {}
        for m in self.model.members:
            my = case.My.get(m.uid, case.default_My)
            if my is None:
                continue
            if case.hinges == "column_base":
                if m.kind != "column":
                    continue
                end = "i" if m.pi[2] <= m.pj[2] else "j"
                plan[(m.uid, end)] = float(my)
            else:  # all_ends
                plan[(m.uid, "i")] = float(my)
                plan[(m.uid, "j")] = float(my)
        return plan

    def _build(self, pdelta: bool = False, hinge_case=None) -> _Assembly:
        """(Re)build the OpenSees domain from the (meshed) BuildingModel.

        ``hinge_case`` (v0.5, pushover only): a :class:`PushoverCase` whose
        hinge plan inserts zeroLength rotational springs (Steel01 about
        both bending axes, stiff elastic torsion, translations tied with
        equalDOF) between duplicated nodes at the hinge locations.  Hinged
        builds are NOT stored as ``self._asm`` (the elastic assembly stays
        the engine-wide reference).
        """
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
        mtag = 9                       # uniaxial material tags (guard uses 1)
        hinge_plan: Dict[Tuple[str, str], float] = (
            self._hinge_plan(hinge_case) if hinge_case is not None else {})
        # (uid, end, member, orig node tag, dup node tag, My)
        hinge_dups: List[tuple] = []
        for m in model.members:
            sec = model.sections[m.section]
            mat = model.materials[sec.material]
            A_eff, I22_eff, I33_eff, J_eff = self._eff_props(sec)
            xax, _, _, vecxz, _ = _local_axes(m)
            toks = m.release_tokens()
            segs = mesh.segments[m.uid]
            last = len(segs) - 1
            torsion_only = np.outer(xax, xax)
            my_i = hinge_plan.get((m.uid, "i"))
            my_j = hinge_plan.get((m.uid, "j"))
            for seg in segs:
                etag += 1
                ops.geomTransf("PDelta" if pdelta else "Linear", etag, *vecxz)
                rel_i = "Mi" in toks and seg.index == 0
                rel_j = "Mj" in toks and seg.index == last
                code = (1 if rel_i else 0) + (2 if rel_j else 0)
                extra = ["-releasez", code, "-releasey", code] if code else []
                ni_tag, nj_tag = seg.ni + 1, seg.nj + 1
                # v0.5 pushover hinges: the member end connects to a
                # duplicated node; the spring bridges original <-> duplicate
                if my_i is not None and seg.index == 0:
                    tag += 1
                    ops.node(tag, *mesh.points[seg.ni])
                    hinge_dups.append((m.uid, "i", m, seg.ni + 1, tag, my_i))
                    ni_tag = tag
                if my_j is not None and seg.index == last:
                    tag += 1
                    ops.node(tag, *mesh.points[seg.nj])
                    hinge_dups.append((m.uid, "j", m, seg.nj + 1, tag, my_j))
                    nj_tag = tag
                ops.element("elasticBeamColumn", etag,
                            ni_tag, nj_tag,
                            A_eff, mat.E, mat.G, J_eff, I22_eff, I33_eff,
                            etag, *extra)
                asm.seg_ele[(m.uid, seg.index)] = etag
                for node, released in ((seg.ni, rel_i), (seg.nj, rel_j)):
                    rot_add(node, torsion_only if released else eye3)
                    if released:
                        released_nodes.add(node)
            asm.ele_nodes[m.uid] = (segs[0].ni + 1, segs[-1].nj + 1)

        # --- pushover hinge springs (v0.5) ---------------------------------
        # Steel01 (bilinear) about both member bending axes with
        # k_theta = n*6EI/L (n = HINGE_STIFFNESS_FACTOR) and hardening
        # ratio b = h/(n+1-h*n); stiff elastic torsion; translations tied
        # exactly with equalDOF (needs the Transformation handler).
        for uid, end, m, orig_tag, dup_tag, my in hinge_dups:
            sec = model.sections[m.section]
            mat = model.materials[sec.material]
            A_eff, I22_eff, I33_eff, J_eff = self._eff_props(sec)
            L = m.length
            n_f = HINGE_STIFFNESS_FACTOR
            k22 = n_f * 6.0 * mat.E * I22_eff / L
            k33 = n_f * 6.0 * mat.E * I33_eff / L
            kt = n_f * max(6.0 * mat.E * I22_eff, 6.0 * mat.E * I33_eff,
                           mat.G * J_eff) / L
            h = hinge_case.hardening
            b = h / (n_f + 1.0 - h * n_f)
            xax, yax, _, _, _ = _local_axes(m)
            mtag += 1
            ops.uniaxialMaterial("Elastic", mtag, kt)
            t_tag = mtag
            mtag += 1
            ops.uniaxialMaterial("Steel01", mtag, my, k22, b)
            y_tag = mtag
            mtag += 1
            ops.uniaxialMaterial("Steel01", mtag, my, k33, b)
            z_tag = mtag
            etag += 1
            ops.element("zeroLength", etag, orig_tag, dup_tag,
                        "-mat", t_tag, y_tag, z_tag, "-dir", 4, 5, 6,
                        "-orient", *xax, *yax)
            ops.equalDOF(orig_tag, dup_tag, 1, 2, 3)
            asm.hinge_ele[(uid, end)] = etag
            asm.hinge_dup_of[dup_tag] = orig_tag
            asm.hinge_rot_yield[(uid, end)] = (my / k22, my / k33)

        # --- shell elements -------------------------------------------------
        if mesh.quads:
            sec_tags: Dict[str, int] = {}
            stag = 0
            for name, ssec in model.shell_sections.items():
                stag += 1
                mat = model.materials[ssec.material]
                # rho = 0: shell self-weight/mass ignored in v0.2
                # v0.4: ssec.mod scales E (membrane AND flexural stiffness)
                ops.section("ElasticMembranePlateSection", stag,
                            mat.E * ssec.mod, mat.nu, ssec.thickness, 0.0)
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
                asm.quad_ele.append(etag)
                for n in quad.nodes:
                    rot_add(n, eye3)   # shells stiffen all three rotations

        # --- link elements (v0.5) -------------------------------------------
        # zeroLength with one elastic uniaxial material per non-zero
        # stiffness entry; default orientation = GLOBAL axes (local axes ==
        # global for v0.5).  A pure spring: element length carries no
        # rigid-arm moment transfer.
        link_node_k: Dict[int, List[float]] = {}
        for lk in model.links:
            ni = self._find_node(asm, lk.pi)
            nj = self._find_node(asm, lk.pj)
            dirs = [d + 1 for d in range(6) if lk.stiffness[d] > 0.0]
            mats: List[int] = []
            for d in dirs:
                mtag += 1
                ops.uniaxialMaterial("Elastic", mtag,
                                     float(lk.stiffness[d - 1]))
                mats.append(mtag)
            etag += 1
            ops.element("zeroLength", etag, ni, nj, "-mat", *mats,
                        "-dir", *dirs)
            asm.link_ele[lk.uid] = etag
            for t in (ni, nj):
                acc = link_node_k.setdefault(t, [0.0] * 6)
                for d in range(6):
                    acc[d] += float(lk.stiffness[d])

        # --- prospective rigid-diaphragm slaves (v0.5: per-story option) ---
        prospective_slaves: set = set()
        for s in model.stories:
            if model.effective_diaphragm(s.name) != "rigid":
                continue
            plane = [t for t, c in asm.struct_coords.items()
                     if abs(c[2] - s.elevation) < _TOL]
            if len(plane) >= 2:
                prospective_slaves.update(plane)

        # --- auto-restrain DOFs of link-only nodes (v0.5) -------------------
        # A node connected ONLY to links has zero stiffness on every DOF
        # its links do not spring: restrain those (translations AND
        # rotations), leaving diaphragm-tied dofs (ux/uy/rz of prospective
        # slaves) to the diaphragm constraint.
        touched = {seg.ni for segs in mesh.segments.values() for seg in segs}
        touched |= {seg.nj for segs in mesh.segments.values() for seg in segs}
        touched |= {n for q in mesh.quads for n in q.nodes}
        for t in sorted(link_node_k):
            if (t - 1) in touched:
                continue                       # frame/shell stiffness present
            prev = asm.node_restraints.get(t, (0,) * 6)
            k6 = link_node_k[t]
            newfix = [0] * 6
            for d in range(6):
                if prev[d] or k6[d] > 0.0:
                    continue
                if d in (0, 1, 5) and t in prospective_slaves:
                    continue
                newfix[d] = 1
            if any(newfix):
                ops.fix(t, *newfix)
                record_fix(t, newfix)

        # --- auto-restrain rotations left unstiffened by releases ----------
        # A node attached ONLY through moment-released member ends has zero
        # stiffness about axes perpendicular to those members: OpenSees would
        # see a singular system.  Like ETABS, hold the NODE rotation (the
        # member end still rotates freely behind its release).  rz of
        # prospective rigid-diaphragm slaves is left to the diaphragm tie.
        if released_nodes:
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

        # --- rigid diaphragms (v0.5: per-story "rigid" | "none") ------------
        cx, cy = model.plan_center()
        for s in model.stories:
            if model.effective_diaphragm(s.name) != "rigid":
                continue
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
        asm.use_transformation = bool(asm.masters) or bool(hinge_dups)

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
        if hinge_case is None:
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

    @staticmethod
    def _eff_props(sec: FrameSection) -> Tuple[float, float, float, float]:
        """(A, I22, I33, J) with the v0.4 stiffness modifiers applied."""
        return (sec.A * sec.mod_A, sec.I22 * sec.mod_I22,
                sec.I33 * sec.mod_I33, sec.J * sec.mod_J)

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
        A_eff, I22_eff, I33_eff, J_eff = self._eff_props(sec)
        f0 = _condensed_fef(records, seg.length, mat.E, mat.G, A_eff,
                            I22_eff, I33_eff, J_eff, released)
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
    def _member_outputs(self, asm: _Assembly,
                        baseline: Optional[Dict[Tuple[str, int],
                                                List[float]]] = None
                        ) -> Tuple[Dict[str, List[float]],
                                   Dict[str, Dict[str, List[float]]]]:
        """End forces + 11-station internal forces per ORIGINAL member.

        End forces of a split member are the (corrected) end-i forces of its
        first segment and end-j forces of its last segment.  Station forces
        come from exact statics: corrected segment end-i forces plus the
        recorded span loads integrated in closed form.  ``baseline`` (v0.3,
        P-Delta two-stage runs) holds per-segment local forces of the
        gravity state to subtract, so the case reports its own increment.
        """
        member_forces: Dict[str, List[float]] = {}
        member_stations: Dict[str, Dict[str, List[float]]] = {}
        for m in self.model.members:
            segs = asm.mesh.segments[m.uid]
            corrected: Dict[int, List[float]] = {}
            for seg in segs:
                etag = asm.seg_ele[(m.uid, seg.index)]
                f = ops.eleResponse(etag, "localForce")
                if baseline:
                    snap = baseline.get((m.uid, seg.index))
                    if snap is not None:
                        f = [v - s for v, s in zip(f, snap)]
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

    # ----------------------------------------------- per-shell-quad results
    @staticmethod
    def _shell_outputs(asm: _Assembly,
                       baseline: Optional[Dict[int, List[float]]] = None
                       ) -> Dict[int, List[float]]:
        """Gauss-averaged stress resultants per shell quad (v0.4).

        ShellMITC4 'stresses' returns 8 resultants x 4 gauss points
        [Nxx, Nyy, Nxy, Mxx, Myy, Mxy, Vxz, Vyz] in the ELEMENT local
        system (membrane kN/m, moments kN*m/m); the 4 gauss values are
        averaged (= centroid value for a bilinear field).  ``baseline``
        (P-Delta two-stage runs) is subtracted so the case reports its own
        increment.

        openseespy quirk (verified empirically on 3.7.1.2): the 'stresses'
        response of an element is stale until that element's resisting
        force has been queried, so 'forces' is requested (and discarded)
        first for every quad.
        """
        out: Dict[int, List[float]] = {}
        for qi, etag in enumerate(asm.quad_ele):
            ops.eleResponse(etag, "forces")          # warm-up (see docstring)
            vals = ops.eleResponse(etag, "stresses")
            if len(vals) != 32:                      # pragma: no cover
                continue
            avg = np.asarray(vals, dtype=float).reshape(4, 8).mean(axis=0)
            if baseline is not None and qi in baseline:
                avg = avg - np.asarray(baseline[qi])
            out[qi] = [float(v) for v in avg]
        return out

    # ------------------------------------------------------------- analysis
    @staticmethod
    def _setup_analysis(asm: _Assembly) -> None:
        ops.wipeAnalysis()
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
    def _combine(self, name: str, combo: LoadCombo) -> CaseResults:
        """Solve a combo: additive superposition or min/max envelope."""
        if combo.combo_type == "envelope":
            return self._envelope(name, combo.cases)
        return self._superpose(name, combo.cases)

    def _superpose(self, name: str, factors: Dict[str, float]) -> CaseResults:
        """Linear superposition of already-solved case results."""
        parts = [(self.run_static(case_name), f) for case_name, f in factors.items()]
        warning = ""
        if any(self.model.cases[c].pdelta for c in factors):
            warning = ("superposition includes a P-Delta (nonlinear) case; "
                       "linear combination is approximate")

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
        # shell stress resultants superpose linearly too (v0.4)
        shell_forces: Dict[int, List[float]] = {}
        for qi, v0 in parts[0][0].shell_forces.items():
            shell_forces[qi] = [
                sum(f * res.shell_forces[qi][i] for res, f in parts)
                for i in range(len(v0))]
        return CaseResults(
            name=name,
            node_disp=comb_vecs(lambda r: r.node_disp),
            reactions=comb_vecs(lambda r: r.reactions),
            base=base,
            member_forces=comb_vecs(lambda r: r.member_forces),
            story=story,
            member_stations=member_stations,
            warning=warning,
            shell_forces=shell_forces,
        )

    def _envelope(self, name: str, factors: Dict[str, float]) -> CaseResults:
        """Per-quantity min/max envelope over the listed (factored) cases.

        The returned CaseResults holds the MAXIMA in the standard fields
        and the MINIMA as a nested CaseResults in ``minima`` (serialised
        under ``"min"``).  Envelopes cover node_disp / reactions / base /
        member_forces / story / member_stations; shell stress resultants
        are NOT enveloped in v0.4 (component-wise min/max of a tensor field
        is not a meaningful design envelope) and are left empty.
        """
        parts = [(self.run_static(case_name), f)
                 for case_name, f in factors.items()]
        warning = ""
        if any(self.model.cases[c].pdelta for c in factors):
            warning = ("envelope includes a P-Delta (nonlinear) case; its "
                       "factored results enter the envelope unchanged")

        def env_vecs(get) -> Tuple[dict, dict]:
            first = get(parts[0][0])
            mx = {k: [-math.inf] * len(v) for k, v in first.items()}
            mn = {k: [math.inf] * len(v) for k, v in first.items()}
            for res, f in parts:
                for k, v in get(res).items():
                    hi, lo = mx[k], mn[k]
                    for i, x in enumerate(v):
                        fx = f * x
                        if fx > hi[i]:
                            hi[i] = fx
                        if fx < lo[i]:
                            lo[i] = fx
            return mx, mn

        disp_mx, disp_mn = env_vecs(lambda r: r.node_disp)
        reac_mx, reac_mn = env_vecs(lambda r: r.reactions)
        mf_mx, mf_mn = env_vecs(lambda r: r.member_forces)
        base_mx = {k: max(f * res.base[k] for res, f in parts)
                   for k in ("FX", "FY", "FZ", "MX", "MY", "MZ")}
        base_mn = {k: min(f * res.base[k] for res, f in parts)
                   for k in ("FX", "FY", "FZ", "MX", "MY", "MZ")}
        story_mx: Dict[str, Dict[str, float]] = {}
        story_mn: Dict[str, Dict[str, float]] = {}
        for s_name, s0 in parts[0][0].story.items():
            story_mx[s_name] = {k: max(f * res.story[s_name][k]
                                       for res, f in parts) for k in s0}
            story_mn[s_name] = {k: min(f * res.story[s_name][k]
                                       for res, f in parts) for k in s0}
        st_mx: Dict[str, Dict[str, List[float]]] = {}
        st_mn: Dict[str, Dict[str, List[float]]] = {}
        for uid, st0 in parts[0][0].member_stations.items():
            e_mx: Dict[str, List[float]] = {"x": list(st0["x"])}
            e_mn: Dict[str, List[float]] = {"x": list(st0["x"])}
            for key in ("N", "V2", "V3", "T", "M2", "M3"):
                vals = [[f * res.member_stations[uid][key][i]
                         for res, f in parts]
                        for i in range(len(st0[key]))]
                e_mx[key] = [max(v) for v in vals]
                e_mn[key] = [min(v) for v in vals]
            st_mx[uid] = e_mx
            st_mn[uid] = e_mn
        minima = CaseResults(
            name=name + " (min)", node_disp=disp_mn, reactions=reac_mn,
            base=base_mn, member_forces=mf_mn, story=story_mn,
            member_stations=st_mn)
        return CaseResults(
            name=name, node_disp=disp_mx, reactions=reac_mx, base=base_mx,
            member_forces=mf_mx, story=story_mx, member_stations=st_mx,
            warning=warning, minima=minima)

    # ----------------------------------------------------- P-Delta statics
    def _run_static_pdelta(self, case: LoadCase) -> CaseResults:
        """Solve one P-Delta case (Newton on the PDelta transformation).

        Two-stage when ``pdelta_gravity`` names a distinct gravity state:
        gravity is applied and held (``loadConst``), then the case's own
        loads are solved; the reported response is the INCREMENT past the
        gravity state (the standard linearized-P-Delta case result).  With
        ``pdelta_gravity is None`` the case's own patterns are the gravity
        state and a single nonlinear stage is reported in full.
        """
        model = self.model
        asm = self._build(pdelta=True)
        self._seg_span_loads = {}
        self._seg_fef = {}

        snap_disp: Dict[int, List[float]] = {}
        snap_reac: Dict[int, List[float]] = {}
        snap_ele: Dict[Tuple[str, int], List[float]] = {}
        snap_shell: Dict[int, List[float]] = {}
        two_stage = case.pdelta_gravity is not None

        if two_stage:
            ops.timeSeries("Linear", 1)
            ops.pattern("Plain", 1, 1)
            for pat_name, scale in case.pdelta_gravity.items():
                self._apply_pattern(asm, pat_name, scale)
            self._setup_nonlinear_analysis(asm)
            if ops.analyze(1) != 0:
                raise RuntimeError(
                    f"P-Delta analysis failed to converge for case "
                    f"{case.name!r} (gravity stage: patterns "
                    f"{case.pdelta_gravity})")
            ops.loadConst("-time", 0.0)
            snap_disp = {t: list(ops.nodeDisp(t)) for t in asm.node_coords}
            ops.reactions()
            snap_reac = {t: list(ops.nodeReaction(t))
                         for t in asm.support_tags}
            for key, etag in asm.seg_ele.items():
                snap_ele[key] = [float(v)
                                 for v in ops.eleResponse(etag, "localForce")]
            snap_shell = self._shell_outputs(asm)
            # stage-1 span-load bookkeeping must not leak into the case's
            # reported member forces/stations (they belong to gravity)
            self._seg_span_loads = {}
            self._seg_fef = {}

        ops.timeSeries("Linear", 2)
        ops.pattern("Plain", 2, 2)
        for pat_name, scale in case.patterns.items():
            self._apply_pattern(asm, pat_name, scale)
        self._setup_nonlinear_analysis(asm)
        if ops.analyze(1) != 0:
            raise RuntimeError(
                f"P-Delta analysis failed to converge for case "
                f"{case.name!r}. The load level may exceed the elastic "
                "buckling capacity of the gravity state, or the model may "
                "be unstable; reduce loads or check supports.")

        node_disp = {t: [d - s for d, s in
                         zip(ops.nodeDisp(t), snap_disp.get(t, (0.0,) * 6))]
                     for t in asm.node_coords}
        ops.reactions()
        reactions = {t: [r - s for r, s in
                         zip(ops.nodeReaction(t), snap_reac.get(t, (0.0,) * 6))]
                     for t in asm.support_tags}
        base = self._base_totals(asm, reactions)
        member_forces, member_stations = self._member_outputs(
            asm, baseline=snap_ele)
        story = self._story_results(asm, case, node_disp)
        shell_forces = self._shell_outputs(
            asm, baseline=snap_shell if two_stage else None)
        return CaseResults(case.name, node_disp, reactions, base,
                           member_forces, story, member_stations,
                           shell_forces=shell_forces)

    @staticmethod
    def _setup_nonlinear_analysis(asm: _Assembly) -> None:
        ops.wipeAnalysis()
        ops.constraints("Transformation" if asm.use_transformation else "Plain")
        ops.numberer("RCM")
        ops.system("BandGeneral")
        ops.test("NormDispIncr", 1.0e-8, 20)
        ops.algorithm("Newton")
        ops.integrator("LoadControl", 1.0)
        ops.analysis("Static")

    # ------------------------------------------------------------- pushover
    @staticmethod
    def _setup_pushover_analysis(asm: _Assembly, ctrl: Optional[int] = None,
                                 dof: int = 1, du: float = 0.0) -> None:
        """Newton (NormDispIncr 1e-6, 50) static analysis; LoadControl for
        the gravity stage, DisplacementControl(ctrl, dof, du) for the push."""
        ops.wipeAnalysis()
        ops.constraints("Transformation" if asm.use_transformation
                        else "Plain")
        ops.numberer("RCM")
        ops.system("BandGeneral")
        ops.test("NormDispIncr", 1.0e-6, 50)
        ops.algorithm("Newton")
        if ctrl is None:
            ops.integrator("LoadControl", 1.0)
        else:
            ops.integrator("DisplacementControl", ctrl, dof, du)
        ops.analysis("Static")

    def run_pushover(self, name: str) -> PushoverResults:
        """Run one nonlinear static pushover case (cached per engine).

        Stages (see :class:`PushoverCase` for the hinge idealization):

        1. gravity: the case's ``gravity`` pattern combination is applied
           with Newton and held constant (``loadConst -time 0``);
        2. push: a UNIT reference force at the roof control DOF (top-story
           diaphragm master, else the topmost structural node; lowest tag
           breaks ties) is scaled by DisplacementControl in
           ``target_drift * roof_elevation / steps`` equal increments.

        Per converged step the roof displacement (past the gravity state),
        the total base shear (support reactions, gravity share subtracted)
        and every hinge spring rotation are recorded.  On a step that fails
        to converge (Newton, then a NewtonLineSearch retry) the run stops
        early and returns the partial curve with a warning.
        """
        if name in self._po_cache:
            return self._po_cache[name]
        model = self.model
        if name not in model.pushover_cases:
            raise ValueError(f"Unknown pushover case {name!r}")
        case = model.pushover_cases[name]
        if not model.stories:
            raise ValueError(f"Pushover case {name!r}: the model has no "
                             "stories (no roof to control)")

        asm = self._build(hinge_case=case)
        self._seg_span_loads = {}
        self._seg_fef = {}
        dof = 1 if case.direction == "X" else 2
        top = model.stories[-1]
        if top.name in asm.masters:
            ctrl = asm.masters[top.name]
        else:
            zmax = max(c[2] for c in asm.struct_coords.values())
            ctrl = min(t for t, c in asm.struct_coords.items()
                       if abs(c[2] - zmax) < _TOL)
        H = top.elevation
        if H <= 0.0:
            raise ValueError(f"Pushover case {name!r}: roof elevation must "
                             "be > 0")

        # base-shear nodes: supports PLUS hinge duplicates of supported
        # nodes — the equalDOF translation tie routes the element shear to
        # the duplicate's reaction record, not the retained support's
        support_set = set(asm.support_tags)
        base_tags = list(asm.support_tags) + [
            d for d, o in asm.hinge_dup_of.items() if o in support_set]

        warn_list: List[str] = []
        base0 = 0.0
        d0 = 0.0
        if case.gravity:
            ops.timeSeries("Linear", 1)
            ops.pattern("Plain", 1, 1)
            for pat_name, scale in case.gravity.items():
                self._apply_pattern(asm, pat_name, scale)
            self._setup_pushover_analysis(asm)
            if ops.analyze(1) != 0:
                raise RuntimeError(f"Pushover case {name!r}: gravity stage "
                                   "failed to converge")
            ops.loadConst("-time", 0.0)
            ops.reactions()
            base0 = sum(ops.nodeReaction(t)[dof - 1] for t in base_tags)
            d0 = ops.nodeDisp(ctrl, dof)

        ops.timeSeries("Linear", 2)
        ops.pattern("Plain", 2, 2)
        vec = [0.0] * 6
        vec[dof - 1] = 1.0
        ops.load(ctrl, *vec)
        du = case.target_drift * H / case.steps
        self._setup_pushover_analysis(asm, ctrl=ctrl, dof=dof, du=du)

        roof_disp: List[float] = []
        base_shear: List[float] = []
        hinge_rot: Dict[str, float] = {}
        for k in range(case.steps):
            ok = ops.analyze(1)
            if ok != 0:
                ops.algorithm("NewtonLineSearch")
                ok = ops.analyze(1)
                ops.algorithm("Newton")
            if ok != 0:
                warn_list.append(
                    f"pushover stopped early at step {k}/{case.steps}: "
                    "the solution did not converge (partial capacity "
                    "curve returned)")
                break
            roof_disp.append(float(ops.nodeDisp(ctrl, dof)) - d0)
            ops.reactions()
            v = -(sum(ops.nodeReaction(t)[dof - 1]
                      for t in base_tags) - base0)
            base_shear.append(float(v))
            for (uid, _end), etag in asm.hinge_ele.items():
                defo = ops.eleResponse(etag, "deformation")
                rot = (max(abs(defo[1]), abs(defo[2]))
                       if len(defo) >= 3 else 0.0)
                if rot > hinge_rot.get(uid, 0.0):
                    hinge_rot[uid] = float(rot)

        result = PushoverResults(
            name, roof_disp, base_shear, [u / H for u in roof_disp],
            hinge_rot, warn_list)
        self._po_cache[name] = result
        return result

    # ------------------------------------------------- staged construction
    def _stage_of_z(self, z: float) -> int:
        """0-based story index owning elevation ``z`` (story k covers
        (elev_{k-1}, elev_k]; z at/below the first elevation -> story 0;
        above the roof -> the top story)."""
        for i, s in enumerate(self.model.stories):
            if z <= s.elevation + _TOL:
                return i
        return len(self.model.stories) - 1

    def _stage_of_member(self, m: FrameMember,
                         sidx: Dict[str, int]) -> int:
        """Construction stage of a member: its ``story`` when set, else the
        story owning its topmost endpoint elevation."""
        if m.story in sidx:
            return sidx[m.story]
        return self._stage_of_z(max(m.pi[2], m.pj[2]))

    def _stage_of_region(self, r: ShellRegion,
                         sidx: Dict[str, int]) -> int:
        """Construction stage of a shell region (story, else topmost z)."""
        if r.story in sidx:
            return sidx[r.story]
        return self._stage_of_z(max(c[2] for c in r.corners))

    def _split_staged_pattern(self, pat: LoadPattern) -> List[LoadPattern]:
        """Split one gravity pattern into per-story stage patterns.

        Attribution: member loads follow their member's stage; area loads
        follow their region's stage; nodal loads the story owning their z;
        story forces their named story.
        """
        model = self.model
        sidx = {s.name: i for i, s in enumerate(model.stories)}
        n = len(model.stories)
        parts = [LoadPattern(f"__stage{i + 1}__", pat.kind)
                 for i in range(n)]
        for ml in pat.all_member_loads():     # includes legacy member_udls
            m = self._members_by_uid.get(ml.member_uid)
            if m is None:
                raise ValueError(f"Staged pattern {pat.name!r}: load "
                                 f"references unknown member "
                                 f"{ml.member_uid!r}")
            parts[self._stage_of_member(m, sidx)].member_loads.append(ml)
        for al in pat.area_loads:
            region = model._shell(al.region_uid)
            if region is None:
                raise ValueError(f"Staged pattern {pat.name!r}: area load "
                                 f"references unknown shell region "
                                 f"{al.region_uid!r}")
            parts[self._stage_of_region(region, sidx)].area_loads.append(al)
        for nl in pat.nodal_loads:
            parts[self._stage_of_z(nl.point[2])].nodal_loads.append(nl)
        for sf in pat.story_forces:
            parts[sidx.get(sf.story, n - 1)].story_forces.append(sf)
        return parts

    def _stage_submodel(self, k: int) -> BuildingModel:
        """Fresh BuildingModel containing only stories 1..k+1 (0-based k):
        members, shells, supports, links, and diaphragm settings of the
        included stories; no loads/cases (the caller sets them)."""
        model = self.model
        stories = model.stories[:k + 1]
        names = {s.name for s in stories}
        elev_k = stories[-1].elevation
        sidx = {s.name: i for i, s in enumerate(model.stories)}
        sub = BuildingModel(name=f"{model.name} [stage {k + 1}]")
        sub.materials = dict(model.materials)
        sub.sections = dict(model.sections)
        sub.shell_sections = dict(model.shell_sections)
        sub.grid = model.grid
        sub.stories = list(stories)
        sub.members = [m for m in model.members
                       if self._stage_of_member(m, sidx) <= k]
        sub.shells = [r for r in model.shells
                      if self._stage_of_region(r, sidx) <= k]
        sub.base_fixity = model.base_fixity
        sub.supports = [s for s in model.supports
                        if s.point[2] <= elev_k + _TOL]
        sub.rigid_diaphragms = model.rigid_diaphragms
        sub.diaphragm = model.diaphragm
        sub.story_diaphragm = {s2: v for s2, v in
                               model.story_diaphragm.items() if s2 in names}
        sub.links = [lk for lk in model.links
                     if max(lk.pi[2], lk.pj[2]) <= elev_k + _TOL]
        sub.num_modes = 0
        return sub

    def run_staged(self, name: str) -> StagedResults:
        """Run one staged-construction case (v0.6, cached per engine).

        REBUILD-AND-ACCUMULATE sequential gravity: for each stage k a
        FRESH OpenSees model containing only stories 1..k is built and
        solved (linear static) under ONLY story k's gravity loads from the
        case's pattern; member end forces, station forces, reactions, base
        totals, story values, and node displacements are ACCUMULATED across
        stages (structural nodes matched by coordinates, diaphragm masters
        by story).  Each stage's increments are measured in that stage's
        fresh geometry — geometry updating is ignored, the standard
        assumption of linear staged analysis.  Members/regions not yet
        built in a stage receive no increment.  ``include_live`` patterns
        are then applied on the FULL structure in one unstaged increment.
        Finally the one-shot application of the same total loads is solved
        internally for the reported comparison.

        Every partial structure 1..k must be stable on its own (a story
        propped only by later construction cannot be staged).
        """
        if name in self._staged_cache:
            return self._staged_cache[name]
        model = self.model
        if name not in model.staged_cases:
            raise ValueError(f"Unknown staged case {name!r}")
        sc = model.staged_cases[name]
        if not model.stories:
            raise ValueError(f"Staged case {name!r}: the model has no "
                             "stories to stage")
        if sc.pattern not in model.patterns:
            raise ValueError(f"Staged case {name!r}: unknown pattern "
                             f"{sc.pattern!r}")
        n = len(model.stories)
        parts = self._split_staged_pattern(model.patterns[sc.pattern])

        disp_acc: Dict[Vec3, np.ndarray] = {}
        master_acc: Dict[str, np.ndarray] = {}
        reac_acc: Dict[Vec3, np.ndarray] = {}
        base_acc = {key: 0.0 for key in ("FX", "FY", "FZ", "MX", "MY", "MZ")}
        mf_acc: Dict[str, np.ndarray] = {}
        st_acc: Dict[str, dict] = {}
        story_acc: Dict[str, Dict[str, float]] = {}

        def accumulate(eng: "OpenSeesEngine", res: CaseResults) -> None:
            asm = eng._asm
            for t, c in asm.struct_coords.items():
                key = _pkey(c)
                disp_acc[key] = (disp_acc.get(key, np.zeros(6))
                                 + np.asarray(res.node_disp[t]))
            for s_name, mt in asm.masters.items():
                master_acc[s_name] = (master_acc.get(s_name, np.zeros(6))
                                      + np.asarray(res.node_disp[mt]))
            for t in asm.support_tags:
                key = _pkey(asm.node_coords[t])
                reac_acc[key] = (reac_acc.get(key, np.zeros(6))
                                 + np.asarray(res.reactions[t]))
            for key2 in base_acc:
                base_acc[key2] += res.base[key2]
            for uid, f in res.member_forces.items():
                mf_acc[uid] = mf_acc.get(uid, np.zeros(12)) + np.asarray(f)
            for uid, st in res.member_stations.items():
                entry = st_acc.setdefault(uid, {"x": list(st["x"])})
                for key3 in ("N", "V2", "V3", "T", "M2", "M3"):
                    entry[key3] = (entry.get(key3,
                                             np.zeros(len(st[key3])))
                                   + np.asarray(st[key3]))
            for s_name, vals in res.story.items():
                acc = story_acc.setdefault(s_name,
                                           {k4: 0.0 for k4 in vals})
                for k4, v in vals.items():
                    acc[k4] += v

        for k in range(n):
            sub = self._stage_submodel(k)
            sub.patterns = {"__stage__": parts[k]}
            sub.cases = {"__stage__": LoadCase("__stage__",
                                               {"__stage__": 1.0})}
            eng = OpenSeesEngine(sub)
            accumulate(eng, eng.run_static("__stage__"))

        if sc.include_live:
            sub = self._stage_submodel(n - 1)      # full structure
            sub.patterns = dict(model.patterns)
            sub.cases = {"__live__": LoadCase("__live__",
                                              dict(sc.include_live))}
            eng = OpenSeesEngine(sub)
            accumulate(eng, eng.run_static("__live__"))

        # one-shot comparison: the same total loads applied at once on the
        # full structure (stage-n geometry == the full model geometry, so
        # its FE node tags match the parent results exactly)
        sub = self._stage_submodel(n - 1)
        sub.patterns = dict(model.patterns)
        facs = {sc.pattern: 1.0}
        for p, f in sc.include_live.items():
            facs[p] = facs.get(p, 0.0) + f
        sub.cases = {"__oneshot__": LoadCase("__oneshot__", facs)}
        eng_full = OpenSeesEngine(sub)
        oneshot = eng_full.run_static("__oneshot__")
        full_asm = eng_full._asm

        zeros6 = np.zeros(6)
        node_disp = {t: [float(v) for v in disp_acc.get(_pkey(c), zeros6)]
                     for t, c in full_asm.struct_coords.items()}
        for s_name, mt in full_asm.masters.items():
            node_disp[mt] = [float(v) for v in
                             master_acc.get(s_name, zeros6)]
        reactions = {t: [float(v) for v in
                         reac_acc.get(_pkey(full_asm.node_coords[t]),
                                      zeros6)]
                     for t in full_asm.support_tags}
        member_forces = {uid: [float(v) for v in vec]
                         for uid, vec in mf_acc.items()}
        member_stations = {
            uid: {k5: (list(v) if k5 == "x" else [float(x) for x in v])
                  for k5, v in st.items()}
            for uid, st in st_acc.items()}
        empty_story = {"ux": 0.0, "uy": 0.0, "drift_x": 0.0, "drift_y": 0.0,
                       "shear_x": 0.0, "shear_y": 0.0}
        story = {s.name: dict(story_acc.get(s.name, empty_story))
                 for s in model.stories}
        case = CaseResults(name, node_disp, reactions, base_acc,
                           member_forces, story, member_stations)

        cols = [m.uid for m in model.members if m.kind == "column"]
        pct = 0.0
        if cols:
            ref = max(abs(oneshot.member_forces[u][0]) for u in cols)
            if ref > 0.0:
                pct = 100.0 * max(
                    abs(member_forces[u][0] - oneshot.member_forces[u][0])
                    for u in cols) / ref
        result = StagedResults(name, case, oneshot, pct)
        self._staged_cache[name] = result
        return result

    # -------------------------------------------------------- time history
    def run_time_history(self, name: str) -> THResults:
        """Run one time-history case (cached per engine instance).

        Direct integration under uniform ground excitation
        (``UniformExcitation`` with a ``Path`` time series whose sample k
        applies at t = k*dt):

        * Newmark constant-average acceleration (gamma=1/2, beta=1/4,
          unconditionally stable), one step per record sample at the
          record dt;
        * Rayleigh damping fitted to the case's damping ratio at modes 1
          and min(3, n) of the INITIAL ELASTIC model (hinges excluded):
          ``a0 = 2 z wi wj/(wi+wj)``, ``a1 = 2 z/(wi+wj)`` (for a single
          mode this reduces to ``a0 = z*w``, ``a1 = z/w``).  Linear cases
          use current-stiffness proportionality (identical for elastic);
          nonlinear cases apply the same fitted a0/a1 with
          COMMITTED-stiffness proportionality (``betaKcomm``), the
          standard hinge-model choice — initial-stiffness proportionality
          would put spurious post-yield damping moments
          ``a1*k_theta*theta_dot`` (easily exceeding My) on the stiff
          hinge springs;
        * per step: story displacements (diaphragm masters, else the story
          node average), base reactions, and massed-node accelerations for
          the inertia-equilibrium story shears in ``peaks``.

        v0.6 nonlinear cases (``TimeHistoryCase.nonlinear``): the model is
        built with the SAME Steel01 zeroLength hinge springs as a pushover
        case (see CONTRACT v0.5 for the stiff-hinge idealization); the
        case's ``gravity`` pattern combination is applied statically first
        (Newton) and held (``loadConst``); the transient solve uses Newton
        (``NormDispIncr 1e-8, 25``) with a NewtonLineSearch retry per
        failed step; all reported series are the response PAST the gravity
        state; ``hinge_rotations``/``yielded`` are recorded per member.
        Base reactions include the hinge-duplicate nodes of supported
        originals (the equalDOF tie routes the element shear there).
        """
        if name in self._th_cache:
            return self._th_cache[name]
        model = self.model
        if name not in model.th_cases:
            raise ValueError(f"Unknown time-history case {name!r}")
        th = model.th_cases[name]
        nonlinear = bool(getattr(th, "nonlinear", False))

        modal = self.run_modal()      # INITIAL elastic modes (no hinges)
        if not modal.periods:
            raise RuntimeError(
                f"TH case {name!r}: the model has no dynamic modes "
                "(no mass on unrestrained DOFs)")
        omegas = [2.0 * math.pi / T for T in modal.periods]
        w_i = omegas[0]
        w_j = omegas[min(3, len(omegas)) - 1]
        a0 = 2.0 * th.damping * w_i * w_j / (w_i + w_j)
        a1 = 2.0 * th.damping / (w_i + w_j)

        asm = self._build(hinge_case=th if nonlinear else None)
        self._seg_span_loads = {}
        self._seg_fef = {}
        if nonlinear:
            # committed-stiffness proportionality (betaKcomm): the a0/a1
            # FIT comes from the initial elastic modes, but C follows the
            # committed stiffness so the stiff hinge springs cannot
            # generate spurious post-yield damping moments (the well-known
            # betaKinit artifact: a1*k_theta*theta_dot can exceed My)
            ops.rayleigh(a0, 0.0, 0.0, a1)
        else:
            ops.rayleigh(a0, a1, 0.0, 0.0)     # elastic: K == Kinit
        dof = 1 if th.direction == "X" else 2
        stories = model.stories

        # base-reaction nodes: supports plus hinge duplicates of supported
        # originals (same routing as pushover base shear)
        support_set = set(asm.support_tags)
        base_tags = list(asm.support_tags) + [
            d for d, o in asm.hinge_dup_of.items() if o in support_set]

        def story_uxuy(s) -> Tuple[float, float]:
            if s.name in asm.masters:
                d = ops.nodeDisp(asm.masters[s.name])
                return d[0], d[1]
            nodes = asm.story_nodes[s.name]
            if not nodes:
                return 0.0, 0.0
            return (sum(ops.nodeDisp(t, 1) for t in nodes) / len(nodes),
                    sum(ops.nodeDisp(t, 2) for t in nodes) / len(nodes))

        def base_fxfy() -> Tuple[float, float]:
            fx = fy = 0.0
            for t in base_tags:
                r = ops.nodeReaction(t)
                fx += r[0]
                fy += r[1]
            return fx, fy

        # gravity stage (v0.6): applied statically, held constant; the
        # reported series are the increments past this state
        story0 = {s.name: (0.0, 0.0) for s in stories}
        base0 = (0.0, 0.0)
        if th.gravity:
            ops.timeSeries("Linear", 10)
            ops.pattern("Plain", 10, 10)
            for pat_name, scale in th.gravity.items():
                self._apply_pattern(asm, pat_name, scale)
            ops.wipeAnalysis()
            ops.constraints("Transformation" if asm.use_transformation
                            else "Plain")
            ops.numberer("RCM")
            ops.system("BandGeneral")
            ops.test("NormDispIncr", 1.0e-8, 25)
            ops.algorithm("Newton")
            ops.integrator("LoadControl", 1.0)
            ops.analysis("Static")
            if ops.analyze(1) != 0:
                raise RuntimeError(f"TH case {name!r}: gravity stage "
                                   "failed to converge")
            ops.loadConst("-time", 0.0)
            story0 = {s.name: story_uxuy(s) for s in stories}
            ops.reactions()
            base0 = base_fxfy()

        ops.timeSeries("Path", 1, "-dt", float(th.dt),
                       "-values", *[float(a) for a in th.accel],
                       "-factor", float(th.scale))
        ops.pattern("UniformExcitation", 1, dof, "-accel", 1)

        ops.wipeAnalysis()
        ops.constraints("Transformation" if asm.use_transformation
                        else "Plain")
        ops.numberer("RCM")
        ops.system("BandGeneral")
        if nonlinear:
            ops.test("NormDispIncr", 1.0e-8, 25)
            ops.algorithm("Newton")
        else:
            ops.algorithm("Linear")
        ops.integrator("Newmark", 0.5, 0.25)
        ops.analysis("Transient")

        massed = [(t, d, m) for (t, d), m in asm.mass_map.items()
                  if d in (1, 2)]
        massed_tags = sorted({t for t, _, _ in massed})
        n = len(th.accel)
        t_out: List[float] = []
        sux: Dict[str, List[float]] = {s.name: [] for s in stories}
        suy: Dict[str, List[float]] = {s.name: [] for s in stories}
        svx: Dict[str, List[float]] = {s.name: [] for s in stories}
        svy: Dict[str, List[float]] = {s.name: [] for s in stories}
        bfx: List[float] = []
        bfy: List[float] = []
        hinge_rot: Dict[str, float] = {uid: 0.0
                                       for (uid, _e) in asm.hinge_ele}
        yielded: set = set()
        for k in range(n):
            ok = ops.analyze(1, th.dt)
            if ok != 0 and nonlinear:
                ops.algorithm("NewtonLineSearch")
                ok = ops.analyze(1, th.dt)
                ops.algorithm("Newton")
            if ok != 0:
                raise RuntimeError(
                    f"Time-history analysis failed at step {k + 1}/{n} "
                    f"for case {name!r}")
            t_out.append((k + 1) * th.dt)
            # story displacements (masters, else story-node average)
            for s in stories:
                ux, uy = story_uxuy(s)
                sux[s.name].append(ux - story0[s.name][0])
                suy[s.name].append(uy - story0[s.name][1])
            # inertia-equilibrium story shears: total accel = relative
            # (nodeAccel) + ground (Path sample at the current time)
            ag = th.scale * (th.accel[k + 1] if k + 1 < n else 0.0)
            agx = ag if dof == 1 else 0.0
            agy = ag if dof == 2 else 0.0
            acc = {t: ops.nodeAccel(t) for t in massed_tags}
            for s in stories:
                vx = -sum(m * (acc[t][0] + agx) for t, d_, m in massed
                          if d_ == 1
                          and asm.node_coords[t][2] >= s.elevation - _TOL)
                vy = -sum(m * (acc[t][1] + agy) for t, d_, m in massed
                          if d_ == 2
                          and asm.node_coords[t][2] >= s.elevation - _TOL)
                svx[s.name].append(vx)
                svy[s.name].append(vy)
            ops.reactions()
            fx, fy = base_fxfy()
            bfx.append(fx - base0[0])
            bfy.append(fy - base0[1])
            # hinge tracking (nonlinear): peak rotation + yield detection
            for (uid, _end), etag in asm.hinge_ele.items():
                defo = ops.eleResponse(etag, "deformation")
                if len(defo) < 3:
                    continue
                ry, rz = abs(defo[1]), abs(defo[2])
                rot = max(ry, rz)
                if rot > hinge_rot.get(uid, 0.0):
                    hinge_rot[uid] = float(rot)
                ry_y, rz_y = asm.hinge_rot_yield[(uid, _end)]
                if ry > ry_y * (1.0 + 1e-9) or rz > rz_y * (1.0 + 1e-9):
                    yielded.add(uid)

        def peak(vals: Sequence[float]) -> float:
            return max(abs(v) for v in vals) if len(vals) else 0.0

        peaks_story: Dict[str, Dict[str, float]] = {}
        prev_ux = [0.0] * n
        prev_uy = [0.0] * n
        for s in stories:  # bottom -> top
            h = s.height if s.height > 0 else 1.0
            ux_s, uy_s = sux[s.name], suy[s.name]
            peaks_story[s.name] = {
                "ux": peak(ux_s), "uy": peak(uy_s),
                "drift_x": peak([(u - p) / h
                                 for u, p in zip(ux_s, prev_ux)]),
                "drift_y": peak([(u - p) / h
                                 for u, p in zip(uy_s, prev_uy)]),
                "shear_x": peak(svx[s.name]), "shear_y": peak(svy[s.name]),
            }
            prev_ux, prev_uy = ux_s, uy_s
        peaks = {"story": peaks_story,
                 "base": {"FX": peak(bfx), "FY": peak(bfy)}}
        result = THResults(name, t_out, sux, suy, bfx, bfy, peaks,
                           nonlinear=nonlinear, hinge_rotations=hinge_rot,
                           yielded=sorted(yielded))
        self._th_cache[name] = result
        return result

    # --------------------------------------------------- response spectrum
    def run_response_spectrum(self, name: str) -> CaseResults:
        """Run one response-spectrum case (cached per engine instance).

        Exact modal statics: per mode the equivalent static force vector
        ``f_i = Gamma_i * Sa_i * g * M * phi_i`` is solved through the
        ordinary linear static pipeline; per-mode (signed) results are then
        combined per quantity with CQC or SRSS into positive envelopes.
        """
        if name in self._rs_cache:
            return self._rs_cache[name]
        model = self.model
        if name not in model.rs_cases:
            raise ValueError(f"Unknown response-spectrum case {name!r}")
        rs = model.rs_cases[name]

        n_req = rs.num_modes if rs.num_modes > 0 else None
        modal = self.run_modal(n_req)
        if not modal.periods:
            raise RuntimeError(
                f"RS case {name!r}: the model has no dynamic modes "
                "(no mass on unrestrained DOFs)")
        asm = self._asm  # tags are stable across rebuilds (same mesh)
        mass_map = dict(asm.mass_map)
        gamma_key = "gamma_x" if rs.direction == "X" else "gamma_y"

        omegas: List[float] = []
        per_mode: List[CaseResults] = []
        for i, T in enumerate(modal.periods, start=1):
            omega = 2.0 * math.pi / T
            gamma = modal.participation[i - 1][gamma_key]
            sa = _interp_spectrum(rs.spectrum, T) * rs.scale * G_ACCEL
            phi = modal.shapes[i]
            loads = {(t, dof): gamma * sa * m * phi[t][dof - 1]
                     for (t, dof), m in mass_map.items()}
            omegas.append(omega)
            per_mode.append(self._modal_static(loads))

        rho = (np.eye(len(omegas)) if rs.combo_method == "SRSS"
               else _cqc_matrix(omegas, rs.damping))
        result = self._combine_rsa(name, per_mode, rho)
        self._rs_cache[name] = result
        return result

    def _modal_static(self, loads: Dict[Tuple[int, int], float]) -> CaseResults:
        """Linear static solve under explicit (node, dof) -> value loads."""
        asm = self._build()
        self._seg_span_loads = {}
        self._seg_fef = {}
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        for (t, dof), v in loads.items():
            if v == 0.0:
                continue
            vec = [0.0] * 6
            vec[dof - 1] = v
            ops.load(t, *vec)
        self._setup_analysis(asm)
        if ops.analyze(1) != 0:
            raise RuntimeError("Modal static analysis failed")

        node_disp = {t: list(ops.nodeDisp(t)) for t in asm.node_coords}
        ops.reactions()
        reactions = {t: list(ops.nodeReaction(t)) for t in asm.support_tags}
        base = self._base_totals(asm, reactions)
        member_forces, member_stations = self._member_outputs(asm)

        # story values: modal story shear = cumulative applied modal force
        # at & above each story (loads live on massed nodes / masters)
        story: Dict[str, Dict[str, float]] = {}
        prev_ux = prev_uy = 0.0
        for s in self.model.stories:
            if s.name in asm.masters:
                d = node_disp[asm.masters[s.name]]
                ux, uy = d[0], d[1]
            else:
                nodes = asm.story_nodes[s.name]
                ux = (sum(node_disp[t][0] for t in nodes) / len(nodes)
                      if nodes else 0.0)
                uy = (sum(node_disp[t][1] for t in nodes) / len(nodes)
                      if nodes else 0.0)
            vx = sum(v for (t, dof), v in loads.items()
                     if dof == 1 and asm.node_coords[t][2] >= s.elevation - _TOL)
            vy = sum(v for (t, dof), v in loads.items()
                     if dof == 2 and asm.node_coords[t][2] >= s.elevation - _TOL)
            h = s.height if s.height > 0 else 1.0
            story[s.name] = {"ux": ux, "uy": uy,
                             "drift_x": (ux - prev_ux) / h,
                             "drift_y": (uy - prev_uy) / h,
                             "shear_x": vx, "shear_y": vy}
            prev_ux, prev_uy = ux, uy
        return CaseResults("", node_disp, reactions, base, member_forces,
                           story, member_stations)

    @staticmethod
    def _combine_rsa(name: str, parts: List[CaseResults],
                     rho: np.ndarray) -> CaseResults:
        """CQC/SRSS-combine per-mode results, quantity by quantity."""

        def comb_vecs(get) -> dict:
            first = get(parts[0])
            return {k: [_modal_combine([get(p)[k][i] for p in parts], rho)
                        for i in range(len(v))]
                    for k, v in first.items()}

        base = {k: _modal_combine([p.base[k] for p in parts], rho)
                for k in ("FX", "FY", "FZ", "MX", "MY", "MZ")}
        story = {s: {k: _modal_combine([p.story[s][k] for p in parts], rho)
                     for k in s0}
                 for s, s0 in parts[0].story.items()}
        member_stations: Dict[str, Dict[str, List[float]]] = {}
        for uid, st0 in parts[0].member_stations.items():
            entry: Dict[str, List[float]] = {"x": list(st0["x"])}
            for key in ("N", "V2", "V3", "T", "M2", "M3"):
                entry[key] = [
                    _modal_combine([p.member_stations[uid][key][i]
                                    for p in parts], rho)
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
        # eigenvalues below 1e-100 (rad/s)^2 are numerical garbage from a
        # non-converged Arpack run (periods ~1e50 s), not physics: reject
        # them so the dense fallback solver takes over.
        if not vals or len(vals) < n:
            return False
        return all(isinstance(v, (int, float)) and math.isfinite(v)
                   and v > 1e-100 for v in list(vals)[:n])

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

        v0.3: each entry also carries the modal participation FACTORS
        ``gamma_x`` / ``gamma_y`` (``Gamma = L / (phi^T M phi)``, sign
        follows the eigenvector normalisation; ``Gamma * phi`` is
        normalisation-invariant and is what RSA uses).
        """
        totals = {dof: sum(m for (t, d), m in asm.mass_map.items() if d == dof)
                  for dof in (1, 2, 6)}
        out: List[Dict[str, float]] = []
        for i, period in enumerate(periods, start=1):
            phi = shapes[i]
            den = sum(m * phi[t][d - 1] ** 2 for (t, d), m in asm.mass_map.items())
            entry: Dict[str, float] = {"mode": i, "T": period}
            for dof, key in ((1, "ux"), (2, "uy"), (6, "rz")):
                gamma_key = {1: "gamma_x", 2: "gamma_y"}.get(dof)
                if den <= 0.0 or totals[dof] <= 0.0:
                    entry[key] = 0.0
                    if gamma_key:
                        entry[gamma_key] = 0.0
                    continue
                num = sum(m * phi[t][d - 1]
                          for (t, d), m in asm.mass_map.items() if d == dof)
                entry[key] = (num * num / den) / totals[dof]
                if gamma_key:
                    entry[gamma_key] = num / den
            out.append(entry)
        return out
