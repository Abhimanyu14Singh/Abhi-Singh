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

v0.15 advanced link types (seismic protection devices): axial viscous
DAMPERS (ViscousDamper Maxwell material — no static force, Newton transient),
GAP/HOOK contact links (ElasticPPGap along the link axis), and bilinear
base ISOLATORS (Steel01 shear both horizontal directions + elastic
vertical).  gap/hook/isolator make static cases nonlinear (Newton, like
v0.12 axial-only members); any device makes TH cases Newton.  v0.15 wall
PIERS: labeled walls report per-story in-plane P/V/M from EXACT nodal
free-body cuts of the shell elements (``results["piers"]``).

v0.16 beam deflection recovery: every static case / additive combo reports
``member_deflections`` — EXACT closed-form transverse deflection stations
(local y/z) recovered per segment from the end node displacements plus the
Macaulay double integral of the statics-exact moment field (see
``_member_outputs``); ``results["deflection_checks"]`` flags beams whose
relative-to-chord deflection exceeds ``L / model.deflection_limit``.

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

from skyframe.core.buckling import BucklingResult, buckling_analysis
from skyframe.core.mesh import MeshedModel, Segment, mesh_model
from skyframe.core.model import (DAMPER_DEFAULT_ALPHA, DAMPER_DEFAULT_K,
                                 G_ACCEL, ISOLATOR_DEFAULT_KV, BuildingModel,
                                 FrameMember, FrameSection, LoadCase,
                                 LoadCombo, LoadPattern,
                                 ResponseSpectrumCase, SectionCut,
                                 ShellRegion)

# time-history step cap for engine.run(): if the model's TH cases together
# exceed this many integration steps they are skipped in run() (a warning is
# carried in the results); run_time_history() itself is never capped.
TH_STEP_CAP = 20000

# v0.5 pushover: run() skips ALL pushover cases (with a results warning)
# when their combined step count exceeds this cap; run_pushover() itself is
# never capped.
PUSHOVER_STEP_CAP = 2000

# v0.10 linear buckling: run() solves at most this many buckling systems (each
# is a cheap self-contained numpy eigen-solve); extra cases are skipped in
# run() with a results warning (run_buckling() itself is never capped).
BUCKLING_CASE_CAP = 25

# v0.5 stiff-hinge idealization factor n: the zeroLength hinge spring's
# elastic rotational stiffness is k_theta = n * 6EI/L about each bending
# axis (elastic series softening of the member-end rotational stiffness
# 6EI/L is the factor n/(n+1) ~ 0.909 at n = 10); Steel01 hardening ratio
# b = h / (n + 1 - h*n) makes the member-end SERIES post-yield/elastic
# stiffness ratio exactly the case's ``hardening`` h.
HINGE_STIFFNESS_FACTOR = 10.0

# v0.9 rigid-end offsets: the rigid arm connecting a member's real end node to
# the offset (flexible-element) node is a very-stiff elasticBeamColumn whose
# E/G are the member's scaled by this factor (a "stiff element" rigid link —
# CONTRACT v0.9 documents this choice over rigidLid constraints).  1e6 makes
# the arm ~1e6x stiffer than the flexible span, so results match the exact
# rigid-link value to well within the 1e-4 test tolerance.
RIGID_LINK_FACTOR = 1.0e6

_TOL = 1e-6
_N_STATIONS = 11

# v0.12 tension/compression-only members: a non-"both" member is modelled as a
# 2-force Truss whose uniaxialMaterial('Elastic', E, 0, Eneg) carries load in
# ONE direction only.  The released direction keeps a TINY residual modulus
# (this fraction of E) so the released brace does not create a rigid-body
# mechanism (the system stays regular); at 1e-6 the released member sheds
# > 99.99% of its force in a redundant path (verified) while the active
# direction is exact.  A case with any such member is solved with Newton.
AXIAL_ONLY_RATIO = 1.0e-6

# v0.15 gap/hook device links use ElasticPPGap with this (never-reached)
# yield force so the closed contact stays elastic: F = k * (closing - gap).
GAP_YIELD_HUGE = 1.0e12

# v0.15 device link types that make a STATIC case nonlinear (Newton): their
# tangent switches with the displacement state.  Dampers are excluded — a
# Maxwell damper carries no static force and no static/eigen stiffness
# (verified empirically on openseespy 3.7.1.2: a static solve with a
# ViscousDamper element matches the damper-free solve exactly, and the eigen
# frequencies are unchanged), so damper-only models keep the linear path.
NONLINEAR_STATIC_LINK_TYPES = ("gap", "hook", "isolator")

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
# v0.17 panel zones (beam-column joint modeling)
# --------------------------------------------------------------------------- #
def _panel_zone_joints(model: BuildingModel
                       ) -> Dict[Vec3, Dict[str, list]]:
    """Interior beam-column joints: coordinate key -> member ends meeting it.

    A joint is any deduped point where at least one COLUMN end and one BEAM
    end connect.  Axial-only (Truss) members never participate (they carry
    no bending, so a joint model is meaningless on them).  Returns
    ``{_pkey(point): {"column": [(member, "i"|"j"), ...], "beam": [...]}}``
    for qualifying joints only.
    """
    ends: Dict[Vec3, Dict[str, list]] = {}
    for m in model.members:
        if m.kind not in ("column", "beam"):
            continue
        if getattr(m, "axial_limit", "both") != "both":
            continue
        for end, p in (("i", m.pi), ("j", m.pj)):
            ends.setdefault(_pkey(p), {"column": [], "beam": []}
                            )[m.kind].append((m, end))
    return {key: kinds for key, kinds in ends.items()
            if kinds["column"] and kinds["beam"]}


def compute_panel_zone_offsets(model: BuildingModel
                               ) -> Dict[str, Tuple[float, float]]:
    """Effective rigid end-zone lengths per member for panel_zones "rigid".

    v0.17 automatic rigid end zones (ETABS-style rigid panel-zone
    assumption), computed from the joint geometry — the model is NEVER
    mutated; the engine applies these internally through the v0.9
    rigid-offset machinery:

    * at every interior beam-column joint (see :func:`_panel_zone_joints`),
      each COLUMN end connecting there gets a rigid offset equal to HALF THE
      DEEPEST CONNECTING BEAM depth (``max(beam section h) / 2``), and each
      BEAM end gets HALF THE DEEPEST CONNECTING COLUMN depth
      (``max(column section h) / 2``) — the simple documented rule (the
      column depth perpendicular to each beam is not resolved per direction
      in v0.17);
    * USER-SET explicit offsets win PER MEMBER END: an end with
      ``rigid_i > 0`` (resp. ``rigid_j > 0``) keeps its own effective offset
      ``rigid_factor * rigid_i`` untouched;
    * sections without a drawing depth (``h == 0``) contribute no offset;
    * if the combined offsets would consume a member's whole length the
      member reverts to its user-set offsets with a ``UserWarning``.

    Returns ``{uid: (offset_i, offset_j)}`` (m) for EVERY member (members
    away from joints simply carry their user offsets).
    """
    out: Dict[str, Tuple[float, float]] = {
        m.uid: (m.rigid_offset_i, m.rigid_offset_j) for m in model.members}
    computed: Dict[Tuple[str, str], float] = {}
    for key, kinds in _panel_zone_joints(model).items():
        max_beam_h = max((model.sections[m.section].h
                          for m, _ in kinds["beam"]), default=0.0)
        max_col_h = max((model.sections[m.section].h
                         for m, _ in kinds["column"]), default=0.0)
        for m, end in kinds["column"]:
            if max_beam_h > 0.0:
                computed[(m.uid, end)] = max(
                    computed.get((m.uid, end), 0.0), max_beam_h / 2.0)
        for m, end in kinds["beam"]:
            if max_col_h > 0.0:
                computed[(m.uid, end)] = max(
                    computed.get((m.uid, end), 0.0), max_col_h / 2.0)
    for m in model.members:
        off_i, off_j = out[m.uid]
        if m.rigid_i <= 0.0 and (m.uid, "i") in computed:
            off_i = computed[(m.uid, "i")]
        if m.rigid_j <= 0.0 and (m.uid, "j") in computed:
            off_j = computed[(m.uid, "j")]
        if (off_i, off_j) == out[m.uid]:
            continue
        if off_i + off_j >= m.length - 1e-9:
            warnings.warn(
                f"panel zones: member {m.uid!r} rigid end zones "
                f"({off_i + off_j:.4g} m) would leave no clear span "
                f"(length {m.length:.4g} m); centerline kept", UserWarning)
            continue
        out[m.uid] = (off_i, off_j)
    return out


def compute_panel_zone_springs(model: BuildingModel) -> Dict[Vec3, dict]:
    """Elastic scissors panel-zone spring per interior beam-column joint.

    v0.17 scissors (Krawinkler/Charney-style ELASTIC panel idealization):
    the joint's panel-shear stiffness is condensed into one rotational
    spring

        K_theta = G * d_c * d_b * t_p

    with ``G`` the governing column material's shear modulus (kPa), ``d_c``
    the governing column depth (section ``h``), ``d_b`` the DEEPEST
    connecting beam depth, and ``t_p`` the panel thickness taken as the
    governing column section ``b`` (for rectangular sections the full web
    IS the panel; a doubler-plate term is out of scope in v0.17).  The
    governing column at a joint is the deepest VERTICAL column connecting
    there; joints with no vertical column, or with any of d_c/d_b/t_p == 0
    (sections without drawing dimensions), are skipped with a
    ``UserWarning``.  This is the ELASTIC panel stiffness only — yielding
    of the panel (Krawinkler's trilinear law) is not modeled.

    Returns ``{_pkey(point): {"point", "K", "G", "d_c", "d_b", "t_p"}}``.
    """
    out: Dict[Vec3, dict] = {}
    for key, kinds in _panel_zone_joints(model).items():
        vcols = []
        for m, _end in kinds["column"]:
            d = (m.pj[0] - m.pi[0], m.pj[1] - m.pi[1])
            if math.hypot(d[0], d[1]) < _TOL:
                vcols.append(m)
        if not vcols:
            warnings.warn(f"scissors panel zone at {tuple(key)} skipped: "
                          "no vertical column at the joint", UserWarning)
            continue
        col = max(vcols, key=lambda m: model.sections[m.section].h)
        csec = model.sections[col.section]
        d_c, t_p = csec.h, csec.b
        d_b = max(model.sections[m.section].h for m, _ in kinds["beam"])
        if d_c <= 0.0 or d_b <= 0.0 or t_p <= 0.0:
            warnings.warn(
                f"scissors panel zone at {tuple(key)} skipped: section "
                "drawing dimensions (b/h) are required to size the panel",
                UserWarning)
            continue
        G = model.materials[csec.material].G
        out[key] = {"point": tuple(key), "K": G * d_c * d_b * t_p,
                    "G": G, "d_c": d_c, "d_b": d_b, "t_p": t_p}
    return out


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


def _defl_double_integral(V_i: float, M_i: float,
                          records: Sequence[SpanLoad], comp: int,
                          xi: float) -> float:
    """Closed-form ``I(xi) = int_0^xi (xi - t) * Mb(t) dt`` for one bending
    plane of a prismatic Euler segment (v0.16 deflection recovery).

    ``Mb(t)`` is the internal bending moment of the plane's 2D beam
    reconstructed by statics from the (corrected) local end-i forces and the
    recorded span loads — exactly the field the station results integrate:

        Mb(t) = -M_i + t*V_i + sum_pt p*(t - x0)_+          (point loads)
                + Macaulay terms of each trapezoid record.

    For the local x-y plane pass ``V_i = fi[1], M_i = fi[5], comp = 1``
    (then ``Mb == M3`` and ``EI33 * v'' = Mb``); for the x-z plane pass
    ``V_i = fi[2], M_i = -fi[4], comp = 2`` (the (uz, -ry) plane behaves as
    a standard 2D beam whose conjugate end moment is ``-fi[4]``; then
    ``EI22 * w'' = Mb``).  A linear-varying load ``q<t-a>^n`` contributes
    ``q <xi-a>^(n+4) / ((n+1)(n+2)(n+3)(n+4))`` (Macaulay integration), so
    the whole expression is exact — no quadrature.
    """
    val = -M_i * xi * xi / 2.0 + V_i * xi ** 3 / 6.0
    for rec in records:
        if rec[0] == "point":
            p, x0 = rec[1][comp], rec[2]
            if xi > x0:
                val += p * (xi - x0) ** 3 / 6.0
        else:
            w1, w2 = rec[1][comp], rec[2][comp]
            xa, xb = rec[3], rec[4]
            span = xb - xa
            if span < 1e-12:
                continue
            s = (w2 - w1) / span
            if xi > xa:
                dxa = xi - xa
                val += w1 * dxa ** 4 / 24.0 + s * dxa ** 5 / 120.0
            if xi > xb:
                dxb = xi - xb
                val -= w2 * dxb ** 4 / 24.0 + s * dxb ** 5 / 120.0
    return val


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
# v0.13 section cuts (force integration across a plane) — pure post-processing
# --------------------------------------------------------------------------- #
_SECTION_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}
_CUT_TOL = 1e-9


def _interp_station(st: Dict[str, List[float]], xq: float,
                    key: str) -> float:
    """Linear interpolation of a member-station column at distance ``xq``
    from end i (clamped to the tabulated ends)."""
    xs = st["x"]
    vals = st[key]
    if xq <= xs[0]:
        return vals[0]
    if xq >= xs[-1]:
        return vals[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= xq <= xs[i + 1]:
            dx = xs[i + 1] - xs[i]
            if dx <= 0.0:
                return vals[i + 1]
            t = (xq - xs[i]) / dx
            return vals[i] + t * (vals[i + 1] - vals[i])
    return vals[-1]


def _within_ranges(pt: Vec3, cut: SectionCut) -> bool:
    """True if ``pt`` lies inside every provided bounding-box range of the
    cut (a ``None`` range imposes no limit on that axis)."""
    for idx, r in ((0, cut.x_range), (1, cut.y_range), (2, cut.z_range)):
        if r is None:
            continue
        if not (float(r[0]) - _TOL <= pt[idx] <= float(r[1]) + _TOL):
            return False
    return True


def _shell_crosses(region: ShellRegion, cut: SectionCut) -> bool:
    """True if the shell region straddles the cut plane (corners on both
    sides) and overlaps the optional bounding-box ranges."""
    ai = _SECTION_AXIS_INDEX[cut.axis]
    cs = [c[ai] for c in region.corners]
    if min(cs) > cut.coord + _CUT_TOL or max(cs) < cut.coord - _CUT_TOL:
        return False
    # require both sides (a region merely touching the plane is not a cross)
    if not (min(cs) < cut.coord - _CUT_TOL and max(cs) > cut.coord + _CUT_TOL):
        return False
    for idx, r in ((0, cut.x_range), (1, cut.y_range), (2, cut.z_range)):
        if r is None:
            continue
        vals = [c[idx] for c in region.corners]
        if max(vals) < float(r[0]) - _TOL or min(vals) > float(r[1]) + _TOL:
            return False
    return True


def compute_section_cut(model: BuildingModel, cut: SectionCut,
                        case: "CaseResults") -> dict:
    """Integrate frame internal forces across a cut plane (v0.13).

    For each FRAME member that spans ``cut.coord`` along ``cut.axis`` (and
    whose crossing point lies inside the optional bounding-box ranges), the
    member's 11-station internal forces are LINEARLY interpolated at the exact
    crossing station and transformed local -> global.  Each contribution is
    oriented so the resultant is the internal force that the NEGATIVE-side
    material exerts on the POSITIVE-side material (sign = -sign(coord_j -
    coord_i)); the totals are summed with moments taken about the cut centroid
    (the mean of the crossing points).

    Shells that cross the plane are COUNTED (``n_shells``) but their forces are
    NOT integrated in v0.13 — a warning is emitted so the exclusion is
    explicit (frame-only integration; see CONTRACT v0.13).
    """
    ai = _SECTION_AXIS_INDEX[cut.axis]
    c = float(cut.coord)
    contribs: List[Tuple[Vec3, Vec3, Vec3]] = []
    for m in model.members:
        ci, cj = m.pi[ai], m.pj[ai]
        span = abs(cj - ci)
        if span <= _CUT_TOL:                     # member lies in the plane
            continue
        lo, hi = (ci, cj) if ci <= cj else (cj, ci)
        if not (lo - _CUT_TOL <= c <= hi + _CUT_TOL):
            continue
        frac = min(max((c - ci) / (cj - ci), 0.0), 1.0)
        r_cross: Vec3 = tuple(m.pi[k] + frac * (m.pj[k] - m.pi[k])
                              for k in range(3))  # type: ignore[assignment]
        if not _within_ranges(r_cross, cut):
            continue
        st = case.member_stations.get(m.uid)
        if not st or not st.get("x"):
            continue
        x_cross = frac * m.length
        N = _interp_station(st, x_cross, "N")
        V2 = _interp_station(st, x_cross, "V2")
        V3 = _interp_station(st, x_cross, "V3")
        T = _interp_station(st, x_cross, "T")
        M2 = _interp_station(st, x_cross, "M2")
        M3 = _interp_station(st, x_cross, "M3")
        xax, yax, zax, _, _ = _local_axes(m)
        F: Vec3 = tuple(N * xax[k] + V2 * yax[k] + V3 * zax[k]
                        for k in range(3))        # type: ignore[assignment]
        Mv: Vec3 = tuple(T * xax[k] + M2 * yax[k] + M3 * zax[k]
                         for k in range(3))       # type: ignore[assignment]
        sign = -1.0 if (cj - ci) > 0 else 1.0
        contribs.append((r_cross,
                         tuple(sign * v for v in F),      # type: ignore
                         tuple(sign * v for v in Mv)))    # type: ignore
    n_shells = sum(1 for r in model.shells if _shell_crosses(r, cut))
    if contribs:
        centroid = tuple(sum(rc[k] for rc, _, _ in contribs) / len(contribs)
                         for k in range(3))
    else:
        centroid = (0.0, 0.0, 0.0)
    FX = FY = FZ = MX = MY = MZ = 0.0
    for r_cross, F, Mv in contribs:
        FX += F[0]; FY += F[1]; FZ += F[2]
        d = (r_cross[0] - centroid[0], r_cross[1] - centroid[1],
             r_cross[2] - centroid[2])
        MX += Mv[0] + d[1] * F[2] - d[2] * F[1]
        MY += Mv[1] + d[2] * F[0] - d[0] * F[2]
        MZ += Mv[2] + d[0] * F[1] - d[1] * F[0]
    warns: List[str] = []
    if n_shells:
        warns.append(
            f"{n_shells} shell(s) cross this cut but are EXCLUDED from the "
            "resultant (v0.13 integrates frame members only)")
    return {"FX": FX, "FY": FY, "FZ": FZ, "MX": MX, "MY": MY, "MZ": MZ,
            "n_members": len(contribs), "n_shells": int(n_shells),
            "warnings": warns}


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
    truss_uids: set = field(default_factory=set)   # v0.12 axial-only members
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
    spring_nodes: Dict[int, List[float]] = field(default_factory=dict)
    #   v0.8: real node tag -> 6 grounded-spring stiffnesses (0 where none);
    #   the spring reaction (-k*disp) is added to that node's case reactions
    anchor_tags: set = field(default_factory=set)
    #   v0.15: grounded anchors of advanced device links (fully fixed nodes
    #   connected ONLY to damper/gap/hook/isolator links); reported as
    #   supports, excluded from story node sets / diaphragm slaving
    pz_joints: Dict[int, dict] = field(default_factory=dict)
    #   v0.17 scissors panel zones: original joint node tag ->
    #   {"dup": duplicate tag, "K": spring stiffness, "ele": zeroLength tag}.
    #   Beams connect to the duplicate; a rotational spring (global rx/ry)
    #   plus an equalDOF tie (ux/uy/uz/rz) bridges original <-> duplicate.
    pz_load_tag: Dict[Tuple[str, int], int] = field(default_factory=dict)
    #   v0.17: (member uid, mesh point index) -> panel-zone duplicate tag —
    #   nodal loads of the exact FEF/thermal member-load paths on a
    #   redirected beam end must land on the node the element actually
    #   connects to (else the fixed-end MOMENT share would bypass the
    #   panel spring)

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
    shell_nodal: Dict[int, List[float]] = field(default_factory=dict)
    #   v0.15 (internal, NOT serialised): quad index -> 24 global nodal
    #   resisting-force components (6 dof x 4 nodes) captured at solve time
    #   for the exact wall-pier free-body cuts; populated only when the
    #   model has pier-labeled walls
    member_deflections: Dict[str, Dict[str, List[float]]] = field(
        default_factory=dict)
    #   v0.16: uid -> {"x": [0..L], "dy": [...], "dz": [...]} — exact
    #   transverse deflections (LOCAL y / z, m) at the 11 member stations,
    #   recovered in closed form for prismatic members (static cases and
    #   additive combos; empty for RS/TH/envelopes)

    def to_dict(self) -> dict:
        d = {
            "node_disp": {str(t): list(v) for t, v in self.node_disp.items()},
            "reactions": {str(t): list(v) for t, v in self.reactions.items()},
            "base": dict(self.base),
            "member_forces": {u: list(v) for u, v in self.member_forces.items()},
            "story": {s: dict(v) for s, v in self.story.items()},
            "member_stations": {u: {k: list(v) for k, v in st.items()}
                                for u, st in self.member_stations.items()},
            "member_deflections": {u: {k: list(v) for k, v in md.items()}
                                   for u, md in
                                   self.member_deflections.items()},
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
    story_props: Dict[str, Dict[str, float]] = field(default_factory=dict)
    #   v0.8: per-story center of mass / center of rigidity (diaphragms only)
    story_stiffness: Dict[str, Dict[str, Dict[str, float]]] = field(
        default_factory=dict)
    #   v0.9: per lateral case/story lateral stiffness {"kx","ky"} = V/Δ
    irregularity: Dict[str, Dict[str, Dict[str, object]]] = field(
        default_factory=dict)
    #   v0.9: per lateral case/story ASCE 7 torsional + soft-story flags
    buckling: Dict[str, dict] = field(default_factory=dict)
    #   v0.10: per buckling-case BucklingResult.to_dict()
    takedown: Dict[str, dict] = field(default_factory=dict)
    #   v0.11: per static case / additive combo gravity load takedown
    section_cuts: Dict[str, Dict[str, dict]] = field(default_factory=dict)
    #   v0.13: per static case / additive combo -> cut name -> resultant dict
    piers: Dict[str, Dict[str, Dict[str, Dict[str, float]]]] = field(
        default_factory=dict)
    #   v0.15: per static case / additive combo -> pier label -> story ->
    #   {"P", "V", "M"} in-plane wall pier design forces
    deflection_checks: Dict[str, List[dict]] = field(default_factory=dict)
    #   v0.16: per static case / additive combo -> beam serviceability
    #   entries [{uid, story, L, max_abs_dy, ratio_str, limit, ok}]
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
            "buckling": {n: dict(b) for n, b in self.buckling.items()},
            "takedown": {n: dict(t) for n, t in self.takedown.items()},
            "section_cuts": {c: {n: dict(v) for n, v in cuts.items()}
                             for c, cuts in self.section_cuts.items()},
            "piers": {c: {p: {s: dict(v) for s, v in by_story.items()}
                          for p, by_story in by_pier.items()}
                      for c, by_pier in self.piers.items()},
            "deflection_checks": {c: [dict(e) for e in entries]
                                  for c, entries in
                                  self.deflection_checks.items()},
            "modal": self.modal.to_dict(),
        }
        if self.story_props:
            d["story_props"] = {s: dict(v)
                                for s, v in self.story_props.items()}
        if self.story_stiffness:
            d["story_stiffness"] = {
                c: {s: dict(v) for s, v in by_story.items()}
                for c, by_story in self.story_stiffness.items()}
        if self.irregularity:
            d["irregularity"] = {
                c: {s: dict(v) for s, v in by_story.items()}
                for c, by_story in self.irregularity.items()}
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
        # v0.17 panel_zones == "rigid": effective per-member rigid offsets
        # (computed lazily; the user's model is never mutated)
        self._pz_offsets: Optional[Dict[str, Tuple[float, float]]] = None

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
        # v0.10 response-spectrum directional combinations (ASCE 7 §12.5):
        # combine two existing RS cases into a directional envelope, reported
        # alongside the base RS cases in results["rs_cases"].
        for cname, spec in model.rs_combos.items():
            rs_cases[cname] = self.run_rs_directional(
                spec["name_x"], spec["name_y"],
                method=spec.get("method", "100_30"), name=cname)
        th_cases: Dict[str, THResults] = {}
        warning = ""
        if model.th_cases:
            total_steps = sum(len(self._resolve_th_record(c)[0])
                              for c in model.th_cases.values())
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
        # v0.10 linear buckling: self-contained numpy solve per case (cheap);
        # cap the number of systems run in run() for safety.
        buckling: Dict[str, dict] = {}
        if model.buckling_cases:
            for i, name in enumerate(model.buckling_cases):
                if i >= BUCKLING_CASE_CAP:
                    bwarn = (f"buckling cases beyond {BUCKLING_CASE_CAP} "
                             "skipped in run() (call run_buckling per case)")
                    warning = f"{warning}; {bwarn}" if warning else bwarn
                    break
                buckling[name] = self.run_buckling(name).to_dict()
        story_props = self._compute_story_props()
        asm = self._asm if self._asm is not None else self._build()
        # v0.9 seismic diagnostics over static cases + additive combos
        diag_src = dict(cases)
        for cname, cb in model.combos.items():
            if cb.combo_type == "add" and cname in combos:
                diag_src[cname] = combos[cname]
        story_stiffness, irregularity = self._seismic_diagnostics(asm, diag_src)
        # v0.11 gravity load takedown: per static case + additive combo
        takedown: Dict[str, dict] = {}
        for cname, cr in cases.items():
            takedown[cname] = self._takedown(asm, cr,
                                             model.cases[cname].patterns)
        for cname, cb in model.combos.items():
            if cb.combo_type != "add" or cname not in combos:
                continue
            eff: Dict[str, float] = {}
            for base_case, f in cb.cases.items():
                for p, pf in model.cases[base_case].patterns.items():
                    eff[p] = eff.get(p, 0.0) + f * pf
            takedown[cname] = self._takedown(asm, combos[cname], eff)
        # v0.13 section cuts: pure post-processing of member_stations over
        # static cases + additive combos (cheap summation, no extra solve).
        section_cuts: Dict[str, Dict[str, dict]] = {}
        if model.section_cuts:
            cut_src = dict(cases)
            for cname, cb in model.combos.items():
                if cb.combo_type == "add" and cname in combos:
                    cut_src[cname] = combos[cname]
            for cs_name, cr in cut_src.items():
                section_cuts[cs_name] = {
                    cut.name: compute_section_cut(model, cut, cr)
                    for cut in model.section_cuts}
        # v0.15 wall piers: exact free-body cuts of labeled walls per story,
        # for static cases + additive combos (pure post-processing of the
        # shell nodal forces captured at solve time).
        piers: Dict[str, Dict[str, Dict[str, Dict[str, float]]]] = {}
        if self._piers_enabled():
            pier_src = dict(cases)
            for cname, cb in model.combos.items():
                if cb.combo_type == "add" and cname in combos:
                    pier_src[cname] = combos[cname]
            piers = self._compute_piers(asm, pier_src)
        # v0.16 beam serviceability: relative-to-chord deflection checks per
        # static case + additive combo (pure post-processing of the exact
        # member_deflections stations).
        defl_src = dict(cases)
        for cname, cb in model.combos.items():
            if cb.combo_type == "add" and cname in combos:
                defl_src[cname] = combos[cname]
        deflection_checks = {cname: self._deflection_checks(cr)
                             for cname, cr in defl_src.items()
                             if cr.member_deflections}
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
            story_props=story_props,
            story_stiffness=story_stiffness,
            irregularity=irregularity,
            buckling=buckling,
            takedown=takedown,
            section_cuts=section_cuts,
            piers=piers,
            deflection_checks=deflection_checks,
            warning=warning,
        )

    def _deflection_checks(self, cr: CaseResults) -> List[dict]:
        """Beam serviceability entries for one case/combo (v0.16).

        Per BEAM with deflection stations: the max ABSOLUTE local-y
        deflection RELATIVE TO THE CHORD (the straight line between the two
        end deflections — end settlements/joint displacements do not count
        against the span limit), the classic ``L/n`` ratio string, and an
        ok flag against ``L / model.deflection_limit``.
        """
        model = self.model
        limit_den = float(getattr(model, "deflection_limit", 360.0))
        out: List[dict] = []
        for m in model.members:
            if m.kind != "beam":
                continue
            md = cr.member_deflections.get(m.uid)
            if not md or not md.get("dy"):
                continue
            L = m.length
            dy = md["dy"]
            xs = md["x"]
            d0, d1 = dy[0], dy[-1]
            rel = [v - (d0 + (d1 - d0) * (x / L))
                   for v, x in zip(dy, xs)]
            max_abs = max(abs(v) for v in rel)
            allowed = L / limit_den
            ratio_str = (f"L/{L / max_abs:.0f}" if max_abs > 1e-12
                         else "L/inf")
            out.append({
                "uid": m.uid, "story": m.story, "L": float(L),
                "max_abs_dy": float(max_abs),
                "ratio_str": ratio_str,
                "limit": f"L/{limit_den:g}",
                "ok": bool(max_abs <= allowed * (1.0 + 1e-12)),
            })
        return out

    def _axial_only_present(self) -> bool:
        """True if any member is tension/compression-only (v0.12)."""
        return any(getattr(m, "axial_limit", "both") != "both"
                   for m in self.model.members)

    def _device_links_present(self) -> bool:
        """True if any link is an advanced device (v0.15)."""
        return any(getattr(lk, "link_type", "elastic") != "elastic"
                   for lk in self.model.links)

    def _nonlinear_static_links_present(self) -> bool:
        """True if any link makes STATIC cases nonlinear (gap/hook/isolator;
        dampers carry no static force — see NONLINEAR_STATIC_LINK_TYPES)."""
        return any(getattr(lk, "link_type", "elastic")
                   in NONLINEAR_STATIC_LINK_TYPES
                   for lk in self.model.links)

    def _piers_enabled(self) -> bool:
        """True when any wall region gets pier-force output (v0.15)."""
        model = self.model
        auto = getattr(model, "auto_pier_walls", False)
        return any(r.kind == "wall" and (getattr(r, "pier", "") or auto)
                   for r in model.shells)

    # ------------------------------------------- v0.17 panel zones
    def _panel_offsets(self) -> Dict[str, Tuple[float, float]]:
        """Effective member offsets for ``panel_zones == "rigid"`` (cached).

        Starts from :func:`compute_panel_zone_offsets` and reverts any
        member whose offsets were AUTO-computed but which the mesher split
        (shell-edge compatibility / Winkler discretization) back to its
        user offsets with a warning — the v0.9 offset machinery supports
        single-segment members only.  Empty for other panel_zones modes.
        """
        if self._pz_offsets is None:
            if getattr(self.model, "panel_zones", "none") == "rigid":
                off = compute_panel_zone_offsets(self.model)
                if self._mesh is None:
                    self._mesh = mesh_model(self.model)
                for m in self.model.members:
                    user = (m.rigid_offset_i, m.rigid_offset_j)
                    if (off[m.uid] != user
                            and len(self._mesh.segments[m.uid]) != 1):
                        warnings.warn(
                            f"panel zones: member {m.uid!r} is split into "
                            "multiple segments; automatic rigid end zones "
                            "skipped (centerline kept)", UserWarning)
                        off[m.uid] = user
                self._pz_offsets = off
            else:
                self._pz_offsets = {}
        return self._pz_offsets

    def _member_offsets(self, m: FrameMember) -> Tuple[float, float]:
        """(offset_i, offset_j) the engine actually applies to a member."""
        return self._panel_offsets().get(
            m.uid, (m.rigid_offset_i, m.rigid_offset_j))

    def panel_zone_joints(self) -> List[dict]:
        """Scissors panel-zone joints of the built model (v0.17).

        Each entry is ``{"point": [x, y, z], "orig": tag, "dup": tag,
        "K": K_theta}``; empty unless ``model.panel_zones == "scissors"``.
        """
        asm = self._asm if self._asm is not None else self._build()
        return [{"point": list(asm.node_coords[orig]), "orig": orig,
                 "dup": info["dup"], "K": info["K"]}
                for orig, info in asm.pz_joints.items()]

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

        # v0.12: a model with any tension/compression-only member is nonlinear
        # (the Truss materials switch stiffness by strain sign) -> Newton.
        # v0.15: gap/hook/isolator device links likewise (state-dependent
        # tangent); dampers alone keep the linear path (no static force).
        if self._axial_only_present() or self._nonlinear_static_links_present():
            self._setup_nonlinear_analysis(asm)
        else:
            self._setup_analysis(asm)
        if ops.analyze(1) != 0:
            raise RuntimeError(f"Static analysis failed for case {case_name!r}")

        node_disp = {t: list(ops.nodeDisp(t)) for t in asm.node_coords}
        ops.reactions()
        reactions = {t: list(ops.nodeReaction(t)) for t in asm.support_tags}
        self._add_spring_reactions(asm, node_disp, reactions)
        base = self._base_totals(asm, reactions)
        member_forces, member_stations, member_deflections = \
            self._member_outputs(asm, node_disp=node_disp)
        story = self._story_results(asm, case, node_disp)
        shell_forces = self._shell_outputs(asm)
        shell_nodal = (self._shell_nodal(asm) if self._piers_enabled()
                       else {})

        result = CaseResults(case_name, node_disp, reactions, base,
                             member_forces, story, member_stations,
                             shell_forces=shell_forces,
                             shell_nodal=shell_nodal,
                             member_deflections=member_deflections)
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

        # --- spring supports (v0.8): resolve real nodes now so the fixity
        #     below can leave the sprung DOFs free (the spring restrains
        #     them).  New (isolated) nodes are created if none coincides.
        spring_specs: List[Tuple[int, List[float]]] = []  # (real tag, stiff)
        spring_cover: Dict[int, set] = {}                  # real tag -> dofs
        created_springs: set = set()
        for sp in model.spring_supports:
            try:
                rt = self._find_node(asm, sp.point)
            except ValueError:
                tag += 1
                p = tuple(float(v) for v in sp.point)
                ops.node(tag, *p)
                asm.node_coords[tag] = p
                asm.struct_coords[tag] = p
                rt = tag
                created_springs.add(rt)
            spring_specs.append((rt, [float(k) for k in sp.stiffness]))
            cover = spring_cover.setdefault(rt, set())
            cover |= {d for d in range(6) if sp.stiffness[d] != 0.0}

        # --- Winkler elastic-foundation line springs (v0.11) ---------------
        # A member with foundation_k_line > 0 rests on a Winkler bed: a
        # grounded vertical (global -Z) spring of stiffness
        # k_line * tributary_length is lumped at each of its (discretized)
        # nodes.  These reuse the grounded-spring machinery below (co-located
        # fixed ground node + zeroLength on the Z dof), so the Z fixity on
        # those nodes is cleared and the spring reaction enters the case
        # reactions and base totals.
        for m in model.members:
            k_line = getattr(m, "foundation_k_line", 0.0)
            if k_line <= 0.0:
                continue
            node_trib: Dict[int, float] = {}
            for seg in mesh.segments[m.uid]:
                node_trib[seg.ni] = node_trib.get(seg.ni, 0.0) + seg.length / 2.0
                node_trib[seg.nj] = node_trib.get(seg.nj, 0.0) + seg.length / 2.0
            for nidx, trib in node_trib.items():
                rt = nidx + 1
                kz = k_line * trib
                if kz <= 0.0:
                    continue
                spring_specs.append((rt, [0.0, 0.0, kz, 0.0, 0.0, 0.0]))
                spring_cover.setdefault(rt, set()).add(2)   # dof index 2 = uz

        # --- supports ----------------------------------------------------
        def record_fix(ntag: int, restr: Sequence[int]) -> None:
            prev = asm.node_restraints.get(ntag, (0,) * 6)
            asm.node_restraints[ntag] = tuple(
                int(bool(a) or bool(b)) for a, b in zip(prev, restr))

        def _clear_sprung(ntag: int, restr: List[int]) -> List[int]:
            # A sprung DOF must NOT be fixed (the spring provides restraint).
            for d in spring_cover.get(ntag, ()):
                restr[d] = 0
            return restr

        if model.supports:
            for sup in model.supports:
                ntag = self._find_node(asm, sup.point)
                restr = _clear_sprung(ntag, [int(bool(r)) for r in
                                             sup.restraints])
                ops.fix(ntag, *restr)
                asm.support_tags.append(ntag)
                record_fix(ntag, restr)
        elif asm.struct_coords:
            z_min = min(c[2] for c in asm.struct_coords.values())
            base_restr = ([1, 1, 1, 1, 1, 1] if model.base_fixity == "fixed"
                          else [1, 1, 1, 0, 0, 0])
            for ntag, c in asm.struct_coords.items():
                if abs(c[2] - z_min) < _TOL:
                    restr = _clear_sprung(ntag, list(base_restr))
                    ops.fix(ntag, *restr)
                    asm.support_tags.append(ntag)
                    record_fix(ntag, restr)

        # A newly created (isolated) spring node has no other stiffness: fix
        # its non-sprung DOFs so the system stays regular.
        for rt in created_springs:
            newfix = [0 if d in spring_cover.get(rt, ()) else 1
                      for d in range(6)]
            if any(newfix):
                ops.fix(rt, *newfix)
                record_fix(rt, newfix)
            if rt not in asm.support_tags:
                asm.support_tags.append(rt)

        # --- v0.17 scissors panel zones: duplicate the joint nodes ----------
        # Beams at an interior beam-column joint connect to a DUPLICATE node;
        # a rotational spring K_theta (about global rx AND ry) plus an
        # equalDOF tie on ux/uy/uz/rz bridges original <-> duplicate (the
        # spring elements are created after the members, once the material
        # tag counter is live).  Restrained/support joints are skipped (the
        # equalDOF tie would hide the beam shear from the support reaction).
        pz_map: Dict[Vec3, Tuple[int, int, dict]] = {}
        if getattr(model, "panel_zones", "none") == "scissors":
            key_tag = {_pkey(c): t for t, c in asm.struct_coords.items()}
            for key, spec in compute_panel_zone_springs(model).items():
                orig = key_tag.get(key)
                if orig is None:                       # pragma: no cover
                    continue
                if (orig in asm.support_tags
                        or any(asm.node_restraints.get(orig, (0,) * 6))):
                    warnings.warn(
                        f"scissors panel zone at {tuple(key)} skipped: the "
                        "joint node is a support/restrained node",
                        UserWarning)
                    continue
                tag += 1
                ops.node(tag, *asm.node_coords[orig])
                asm.node_coords[tag] = asm.node_coords[orig]
                pz_map[key] = (orig, tag, spec)

        # --- frame elements (one per mesh segment) -------------------------
        # rot_presence accumulates each node's 3x3 rotational-stiffness
        # pattern so rotations left unstiffened by moment releases can be
        # auto-restrained (see below).
        etag = 0
        rot_presence: Dict[int, np.ndarray] = {}
        released_nodes: set = set()
        truss_end_nodes: set = set()   # v0.12 axial-only member end nodes

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
            # v0.9 rigid-end offsets: elastic element over the CLEAR span,
            # stiff rigid-link arms to the real end nodes.  Only single-segment
            # members (no shell-edge split) may carry offsets.  v0.17: with
            # panel_zones == "rigid" the effective offsets fold in the
            # automatic joint end zones (user-set offsets win per end).
            off_i, off_j = self._member_offsets(m)
            has_offset = (off_i + off_j) > _TOL
            if has_offset and len(segs) != 1:
                raise ValueError(
                    f"Member {m.uid}: rigid end offsets are not supported on "
                    "members split by shell-edge compatibility")
            # --- v0.12 tension/compression-only members: a 2-force Truss ---
            axial_limit = getattr(m, "axial_limit", "both")
            if axial_limit != "both":
                if len(segs) != 1:
                    raise ValueError(
                        f"Member {m.uid}: axial-only (tension/compression) "
                        "members cannot be split by shell-edge compatibility")
                if has_offset:
                    raise ValueError(
                        f"Member {m.uid}: axial-only members do not support "
                        "rigid end offsets")
                if my_i is not None or my_j is not None:
                    raise ValueError(
                        f"Member {m.uid}: a plastic hinge cannot be placed on "
                        "an axial-only member")
                seg = segs[0]
                ni_tag, nj_tag = seg.ni + 1, seg.nj + 1
                mtag += 1
                # uniaxialMaterial('Elastic', E, eta, Eneg): E for +strain
                # (tension), Eneg for -strain (compression).  Tension-only ->
                # full E in tension, tiny in compression; compression-only ->
                # the mirror.  The tiny residual keeps the system regular.
                if axial_limit == "tension":
                    ops.uniaxialMaterial("Elastic", mtag, mat.E, 0.0,
                                         mat.E * AXIAL_ONLY_RATIO)
                else:  # "compression"
                    ops.uniaxialMaterial("Elastic", mtag,
                                         mat.E * AXIAL_ONLY_RATIO, 0.0, mat.E)
                etag += 1
                ops.element("Truss", etag, ni_tag, nj_tag, A_eff, mtag)
                asm.seg_ele[(m.uid, seg.index)] = etag
                asm.ele_nodes[m.uid] = (ni_tag, nj_tag)
                asm.truss_uids.add(m.uid)
                truss_end_nodes.add(seg.ni)
                truss_end_nodes.add(seg.nj)
                continue
            for seg in segs:
                etag += 1
                beam_etag = etag
                ops.geomTransf("PDelta" if pdelta else "Linear",
                               beam_etag, *vecxz)
                rel_i = "Mi" in toks and seg.index == 0
                rel_j = "Mj" in toks and seg.index == last
                code = (1 if rel_i else 0) + (2 if rel_j else 0)
                extra = ["-releasez", code, "-releasey", code] if code else []
                ni_tag, nj_tag = seg.ni + 1, seg.nj + 1
                # v0.17 scissors panel zones: a BEAM end at a panel-zone
                # joint connects to the joint's duplicate node instead
                # (columns keep the original node — the spring between the
                # pair is the panel flexibility).  FEF/thermal nodal loads
                # of this end are redirected too (asm.pz_load_tag).
                if pz_map and m.kind == "beam":
                    if seg.index == 0:
                        ent = pz_map.get(_pkey(mesh.points[seg.ni]))
                        if ent is not None:
                            ni_tag = ent[1]
                            asm.pz_load_tag[(m.uid, seg.ni)] = ent[1]
                    if seg.index == last:
                        ent = pz_map.get(_pkey(mesh.points[seg.nj]))
                        if ent is not None:
                            nj_tag = ent[1]
                            asm.pz_load_tag[(m.uid, seg.nj)] = ent[1]
                # v0.5 pushover hinges: the member end connects to a
                # duplicated node; the spring bridges original <-> duplicate
                if my_i is not None and seg.index == 0:
                    tag += 1
                    ops.node(tag, *mesh.points[seg.ni])
                    hinge_dups.append((m.uid, "i", m, ni_tag, tag, my_i))
                    ni_tag = tag
                if my_j is not None and seg.index == last:
                    tag += 1
                    ops.node(tag, *mesh.points[seg.nj])
                    hinge_dups.append((m.uid, "j", m, nj_tag, tag, my_j))
                    nj_tag = tag
                # rigid arms: insert an offset node inboard of each offset end
                # and a very-stiff beam from the real/hinge node to it; the
                # elastic element then spans the two offset nodes.
                if has_offset and off_i > _TOL:
                    pI = mesh.points[seg.ni]
                    p_off = (pI[0] + off_i * xax[0], pI[1] + off_i * xax[1],
                             pI[2] + off_i * xax[2])
                    tag += 1
                    ops.node(tag, *p_off)
                    etag += 1
                    ops.geomTransf("PDelta" if pdelta else "Linear",
                                   etag, *vecxz)
                    ops.element("elasticBeamColumn", etag, ni_tag, tag, A_eff,
                                mat.E * RIGID_LINK_FACTOR,
                                mat.G * RIGID_LINK_FACTOR, J_eff, I22_eff,
                                I33_eff, etag)
                    ni_tag = tag
                if has_offset and off_j > _TOL:
                    pJ = mesh.points[seg.nj]
                    p_off = (pJ[0] - off_j * xax[0], pJ[1] - off_j * xax[1],
                             pJ[2] - off_j * xax[2])
                    tag += 1
                    ops.node(tag, *p_off)
                    etag += 1
                    ops.geomTransf("PDelta" if pdelta else "Linear",
                                   etag, *vecxz)
                    ops.element("elasticBeamColumn", etag, tag, nj_tag, A_eff,
                                mat.E * RIGID_LINK_FACTOR,
                                mat.G * RIGID_LINK_FACTOR, J_eff, I22_eff,
                                I33_eff, etag)
                    nj_tag = tag
                ops.element("elasticBeamColumn", beam_etag,
                            ni_tag, nj_tag,
                            A_eff, mat.E, mat.G, J_eff, I22_eff, I33_eff,
                            beam_etag, *extra)
                asm.seg_ele[(m.uid, seg.index)] = beam_etag
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

        # --- v0.17 scissors panel-zone springs -------------------------------
        # zeroLength rotational spring K_theta about BOTH horizontal global
        # axes (rx, ry — the bending axes of the two frame planes; a joint
        # framed in one plane simply never strains the other spring) between
        # the original (column-side) node and the beam-side duplicate; the
        # remaining DOFs are tied rigid with equalDOF (translations + rz).
        for key, (orig, dup, spec) in pz_map.items():
            k_theta = float(spec["K"])
            mats_pz: List[int] = []
            for _ in range(2):
                mtag += 1
                ops.uniaxialMaterial("Elastic", mtag, k_theta)
                mats_pz.append(mtag)
            etag += 1
            ops.element("zeroLength", etag, orig, dup,
                        "-mat", *mats_pz, "-dir", 4, 5)
            ops.equalDOF(orig, dup, 1, 2, 3, 6)
            asm.pz_joints[orig] = {"dup": dup, "K": k_theta, "ele": etag}

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

        # --- link elements (v0.5 elastic, v0.15 devices) ---------------------
        # elastic: zeroLength with one elastic uniaxial material per non-zero
        # stiffness entry; default orientation = GLOBAL axes (local axes ==
        # global for v0.5).  A pure spring: element length carries no
        # rigid-arm moment transfer.
        # v0.15 device links (damper/gap/hook/isolator): a twoNodeLink along
        # the link axis (dir 1 = axial; shear forces of a finite-length
        # isolator transfer their moments in equilibrium), or a zeroLength on
        # the global axes for a zero-length isolator.  Damper elements carry
        # no static force and no eigen stiffness (verified), so they are
        # created in every build.
        link_node_k: Dict[int, List[float]] = {}
        device_nodes: set = set()
        for lk in model.links:
            ni = self._find_node(asm, lk.pi)
            nj = self._find_node(asm, lk.pj)
            ltype = getattr(lk, "link_type", "elastic")
            if ltype == "elastic":
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
                continue
            # ---- v0.15 advanced device links -------------------------------
            prm = getattr(lk, "params", {}) or {}
            p_i = asm.node_coords[ni]
            p_j = asm.node_coords[nj]
            L_lk = math.dist(p_i, p_j)
            if ltype == "damper":
                # Maxwell viscous damper along the link axis
                mtag += 1
                ops.uniaxialMaterial(
                    "ViscousDamper", mtag,
                    float(prm.get("k", DAMPER_DEFAULT_K)),
                    float(prm["cd"]),
                    float(prm.get("alpha", DAMPER_DEFAULT_ALPHA)))
                mats, dirs = [mtag], [1]
            elif ltype in ("gap", "hook"):
                # gap: compression-only contact, engages once the pair
                # CLOSES (axial strain < -gap): ElasticPPGap(k, -huge, -gap).
                # hook: tension mirror: ElasticPPGap(k, +huge, +slack).
                # Sign conventions verified empirically (twoNodeLink dir 1
                # strain = elongation).
                mtag += 1
                if ltype == "gap":
                    ops.uniaxialMaterial("ElasticPPGap", mtag,
                                         float(prm["k"]), -GAP_YIELD_HUGE,
                                         -float(prm["gap"]))
                else:
                    ops.uniaxialMaterial("ElasticPPGap", mtag,
                                         float(prm["k"]), GAP_YIELD_HUGE,
                                         float(prm["slack"]))
                mats, dirs = [mtag], [1]
            else:  # isolator
                k1 = float(prm["k1"])
                b_iso = float(prm["k2"]) / k1
                fy = float(prm["Fy"])
                shear_tags = []
                for _ in range(2):
                    mtag += 1
                    ops.uniaxialMaterial("Steel01", mtag, fy, k1, b_iso)
                    shear_tags.append(mtag)
                mtag += 1
                ops.uniaxialMaterial("Elastic", mtag,
                                     float(prm.get("kv",
                                                   ISOLATOR_DEFAULT_KV)))
                kv_tag = mtag
                if L_lk < _TOL:
                    # zero length: global axes (1, 2 horizontal shear;
                    # 3 vertical)
                    mats, dirs = shear_tags + [kv_tag], [1, 2, 3]
                else:
                    # vertical axis: local 1 = axial (kv), 2/3 = shear
                    mats, dirs = [kv_tag] + shear_tags, [1, 2, 3]
            etag += 1
            if L_lk < _TOL:
                ops.element("zeroLength", etag, ni, nj, "-mat", *mats,
                            "-dir", *dirs)
            else:
                ops.element("twoNodeLink", etag, ni, nj, "-mat", *mats,
                            "-dir", *dirs)
            asm.link_ele[lk.uid] = etag
            device_nodes.update((ni, nj))

        # --- grounded point springs (v0.8) ---------------------------------
        # Per spring: a co-located fully-fixed ground node and a zeroLength
        # element (ground -> real) with one elastic material per non-zero
        # DOF; the sprung DOFs of the real node were left free above.  The
        # spring reaction (-k*disp) is added to the case reactions.
        for rt, kvec in spring_specs:
            dirs = [d + 1 for d in range(6) if kvec[d] != 0.0]
            if not dirs:
                continue
            tag += 1
            gnd = tag
            ops.node(gnd, *asm.node_coords[rt])
            ops.fix(gnd, 1, 1, 1, 1, 1, 1)
            mats: List[int] = []
            for d in dirs:
                mtag += 1
                ops.uniaxialMaterial("Elastic", mtag, abs(float(kvec[d - 1])))
                mats.append(mtag)
            etag += 1
            ops.element("zeroLength", etag, gnd, rt, "-mat", *mats,
                        "-dir", *dirs)
            acc = asm.spring_nodes.setdefault(rt, [0.0] * 6)
            for d in range(6):
                acc[d] += float(kvec[d])
            if rt not in asm.support_tags:
                asm.support_tags.append(rt)

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

        # --- v0.15 device-link ground anchors --------------------------------
        # A node connected ONLY to advanced device links (no frame/shell/
        # elastic-link/grounded-spring stiffness) is a GROUNDED ANCHOR: it is
        # fully fixed (even at a diaphragm elevation — it never joins the
        # diaphragm or story node sets) and reported as a support, so its
        # reaction exposes the device force.
        for t in sorted(device_nodes):
            if ((t - 1) in touched or t in link_node_k
                    or t in spring_cover):
                continue
            prev = asm.node_restraints.get(t, (0,) * 6)
            newfix = [0 if prev[d] else 1 for d in range(6)]
            if any(newfix):
                ops.fix(t, *newfix)
                record_fix(t, newfix)
            asm.anchor_tags.add(t)
            if t not in asm.support_tags:
                asm.support_tags.append(t)

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

        # --- auto-restrain rotations of truss-only nodes (v0.12) -----------
        # A Truss (axial-only member) contributes NO rotational stiffness, so a
        # node attached ONLY through axial-only members would be rotationally
        # singular.  Hold its rotations (rz left to the diaphragm for a
        # prospective slave); nodes that a beam/shell also stiffens are in
        # rot_presence and are skipped.  Translational truss-mechanism
        # stability remains the user's modelling responsibility (as for any
        # truss model).
        for nidx in sorted(truss_end_nodes):
            if nidx in rot_presence:
                continue
            ntag = nidx + 1
            prev = asm.node_restraints.get(ntag, (0,) * 6)
            newfix = [0] * 6
            for dof in (3, 4, 5):
                if prev[dof]:
                    continue
                if dof == 5 and ntag in prospective_slaves:
                    continue
                newfix[dof] = 1
            if any(newfix):
                ops.fix(ntag, *newfix)
                record_fix(ntag, newfix)

        # --- story node sets ----------------------------------------------
        # All FE nodes in the story plane join the set: frame nodes, slab
        # mesh nodes, and wall nodes lying exactly at the story elevation.
        # Wall interior nodes (between story elevations) are excluded by z;
        # device-link ground anchors (v0.15) never join a story.
        for s in model.stories:
            asm.story_nodes[s.name] = [t for t, c in asm.struct_coords.items()
                                       if abs(c[2] - s.elevation) < _TOL
                                       and t not in asm.anchor_tags]

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
        asm.use_transformation = (bool(asm.masters) or bool(hinge_dups)
                                  or bool(pz_map))

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

        # v0.8 temperature loads: axial thermal fixed-end force per member.
        for tl in getattr(pat, "thermal_loads", ()):
            member = self._members_by_uid.get(tl.member_uid)
            if member is None:
                raise ValueError(f"Thermal load references unknown member "
                                 f"{tl.member_uid!r}")
            self._apply_thermal(asm, member, tl.dT * scale)

        for nl in pat.nodal_loads:
            t = self._find_node(asm, nl.point)
            ops.load(t, nl.fx * scale, nl.fy * scale, nl.fz * scale,
                     0.0, 0.0, 0.0)

        acc_tors = getattr(pat, "accidental_torsion", False)
        ecc = getattr(pat, "ecc", 0.05)
        lx, ly = model.plan_extents()
        for sf in pat.story_forces:
            fx, fy = sf.fx * scale, sf.fy * scale
            if sf.story in asm.masters:
                # v0.8 accidental torsion (§12.8.4.2): fx -> Mz = fx*ecc*Ly,
                # fy -> Mz = fy*ecc*Lx, applied at the master's rz dof.
                mz = (fx * ecc * ly + fy * ecc * lx) if acc_tors else 0.0
                ops.load(asm.masters[sf.story], fx, fy, 0.0, 0.0, 0.0, mz)
                continue
            nodes = asm.story_nodes.get(sf.story)
            if not nodes:
                raise ValueError(f"Story force on story {sf.story!r} which has "
                                 "no nodes")
            for t in nodes:
                ops.load(t, fx / len(nodes), fy / len(nodes), 0.0, 0.0, 0.0, 0.0)

        # v0.7 self-weight: an ETABS-style self-weight factor turns each
        # material's real weight into exact loads through the existing member-
        # load and area-load paths — a global -Z member load A*unit_weight
        # (kN/m) on every frame member (applied via the "global_z" direction
        # so vertical columns pick up their axial self-weight instead of being
        # skipped) and an area load thickness*unit_weight (kN/m^2, downward)
        # on every shell region that resolves to a shell section.
        swf = getattr(pat, "self_weight_factor", 0.0)
        if swf:
            for member in model.members:
                sec = model.sections.get(member.section)
                mat = model.materials.get(sec.material) if sec else None
                if sec is None or mat is None:
                    continue
                w_sw = swf * sec.A * mat.unit_weight        # kN/m, downward
                if w_sw == 0.0:
                    continue
                self._apply_member_load(asm, member, "udl", -w_sw * scale,
                                        0.0, 0.0, 1.0, "global_z")
            for region in model.shells:
                ssec = model.shell_sections.get(region.section)
                mat = model.materials.get(ssec.material) if ssec else None
                if ssec is None or mat is None:
                    continue
                q_sw = swf * ssec.thickness * mat.unit_weight   # kN/m^2 down
                if q_sw == 0.0:
                    continue
                self._apply_area_load(asm, region.uid, q_sw * scale)

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
        # v0.12: axial-only (Truss) members carry no transverse load; a
        # distributed/point member load on them (incl. self-weight) is skipped
        # (a Truss element rejects eleLoad).  Documented in CONTRACT v0.12.
        if getattr(member, "axial_limit", "both") != "both":
            return
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
            ops.load(asm.pz_load_tag.get((member.uid, node), node + 1),
                     dvec[0] * p, dvec[1] * p, dvec[2] * p,
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
            # v0.17 scissors: a redirected beam end loads its panel-zone
            # duplicate (the element's actual node) so the fixed-end moment
            # share does not bypass the panel spring
            ops.load(asm.pz_load_tag.get((member.uid, node), node + 1),
                     *fg, *mg)

        key = (member.uid, seg.index)
        self._seg_fef[key] = self._seg_fef.get(key, np.zeros(12)) + f0
        for rec in records:
            self._record(member.uid, seg.index, rec)

    def _apply_thermal(self, asm: _Assembly, member: FrameMember,
                       dT: float) -> None:
        """Apply a uniform temperature change dT (deg C) to a frame member.

        The fully-restrained axial fixed-end force is ``N = E*A*alpha*dT``
        (compression when the member cannot expand).  Following the exact
        member-load path, each segment's local fixed-end vector
        ``f0 = [+N, 0..., -N, 0...]`` is applied REVERSED as nodal loads
        (pushing the ends apart by the free thermal expansion) and recorded
        as the segment's end-force correction, so a fully-fixed member reads
        ``N`` (compression), a free member reads 0, and the free elongation
        is exactly ``alpha*dT*L``.  Internal member nodes cancel; only the
        member's two extreme ends carry a net axial nodal force.
        """
        if dT == 0.0:
            return
        if getattr(member, "axial_limit", "both") != "both":
            return       # v0.12: axial-only Truss members take no thermal FEF
        model = self.model
        sec = model.sections[member.section]
        mat = model.materials[sec.material]
        A_eff = sec.A * sec.mod_A
        alpha = getattr(model, "thermal_alpha", 1.2e-5)
        N = mat.E * A_eff * alpha * dT           # E*A*alpha*dT (kN)
        xax, yax, zax, _, _ = _local_axes(member)

        def to_global(lx: float, ly: float, lz: float) -> Vec3:
            return (lx * xax[0] + ly * yax[0] + lz * zax[0],
                    lx * xax[1] + ly * yax[1] + lz * zax[1],
                    lx * xax[2] + ly * yax[2] + lz * zax[2])

        for seg in asm.mesh.segments[member.uid]:
            f0 = np.zeros(12)
            f0[0] = N
            f0[6] = -N
            for node, ofs in ((seg.ni, 0), (seg.nj, 6)):
                fg = to_global(-f0[ofs], -f0[ofs + 1], -f0[ofs + 2])
                ops.load(asm.pz_load_tag.get((member.uid, node), node + 1),
                         *fg, 0.0, 0.0, 0.0)
            key = (member.uid, seg.index)
            self._seg_fef[key] = self._seg_fef.get(key, np.zeros(12)) + f0

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
                                                List[float]]] = None,
                        node_disp: Optional[Dict[int, List[float]]] = None
                        ) -> Tuple[Dict[str, List[float]],
                                   Dict[str, Dict[str, List[float]]],
                                   Dict[str, Dict[str, List[float]]]]:
        """End forces + 11-station internal forces per ORIGINAL member.

        End forces of a split member are the (corrected) end-i forces of its
        first segment and end-j forces of its last segment.  Station forces
        come from exact statics: corrected segment end-i forces plus the
        recorded span loads integrated in closed form.  ``baseline`` (v0.3,
        P-Delta two-stage runs) holds per-segment local forces of the
        gravity state to subtract, so the case reports its own increment.

        v0.16: when ``node_disp`` is provided (static cases; the RS modal
        pipeline passes None) the third returned dict holds the EXACT
        transverse deflection stations per member (``member_deflections``,
        local y / z, m).  Per segment the elastic line satisfies
        ``EI v'' = Mb(x)`` with the statics-exact moment field, so

            v(xi) = v_i + theta0*xi + I(xi)/EI,
            theta0 = (v_j - v_i - I(L_seg)/EI) / L_seg,

        with ``I(xi)`` the closed-form double integral of ``Mb``
        (:func:`_defl_double_integral`) and ``v_i``/``v_j`` the segment end
        NODE displacements transformed to the member local axes.  Only the
        two end displacements enter — no end rotations — so moment releases
        are handled exactly (the released-end rotation is implied by the
        zero end moment in ``Mb``).  Split members are recovered segment by
        segment (exact).  Axial-only (Truss) members and members with rigid
        end offsets are skipped (no bending / offset nodes are not tracked).
        """
        member_forces: Dict[str, List[float]] = {}
        member_stations: Dict[str, Dict[str, List[float]]] = {}
        member_deflections: Dict[str, Dict[str, List[float]]] = {}
        for m in self.model.members:
            # v0.12 axial-only members are 2-force Truss elements: report the
            # constant axial force (V/M/T = 0).  basicForce is tension-positive;
            # member_forces[0] follows the engine convention (+compression) as
            # for beams, so end i = -N_tension, end j = +N_tension.  Station N
            # is tension-positive (matching the beam station convention).
            if m.uid in asm.truss_uids:
                etag = asm.seg_ele[(m.uid, 0)]
                N = float(ops.eleResponse(etag, "basicForce")[0])
                member_forces[m.uid] = [-N, 0.0, 0.0, 0.0, 0.0, 0.0,
                                        N, 0.0, 0.0, 0.0, 0.0, 0.0]
                L = m.length
                xs = [k * L / (_N_STATIONS - 1) for k in range(_N_STATIONS)]
                zeros = [0.0] * _N_STATIONS
                member_stations[m.uid] = {
                    "x": xs, "N": [N] * _N_STATIONS, "V2": list(zeros),
                    "V3": list(zeros), "T": list(zeros), "M2": list(zeros),
                    "M3": list(zeros)}
                continue
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
            # v0.16 exact transverse deflection stations (static cases only;
            # members with rigid end offsets — user-set OR v0.17 panel-zone
            # computed — are skipped: their elastic element spans untracked
            # offset nodes)
            if (node_disp is not None
                    and sum(self._member_offsets(m)) <= _TOL):
                md = self._member_deflection(m, segs, corrected, xs,
                                             node_disp)
                if md is not None:
                    member_deflections[m.uid] = md
        return member_forces, member_stations, member_deflections

    def _member_deflection(self, m: FrameMember, segs: List[Segment],
                           corrected: Dict[int, List[float]],
                           xs: List[float],
                           node_disp: Dict[int, List[float]]
                           ) -> Optional[Dict[str, List[float]]]:
        """{"x", "dy", "dz"} deflection stations of one member (v0.16).

        Exact elastic-line recovery per segment (see ``_member_outputs``):
        the two segment end NODE displacements (transformed to the member
        local y/z axes) plus the closed-form double integral of the
        statics-exact moment field pin the interior cubic/quintic exactly.
        ``dy``/``dz`` are ABSOLUTE local-y / local-z displacements (m) —
        chord-relative values are derived by the serviceability checks.
        """
        model = self.model
        sec = model.sections[m.section]
        mat = model.materials[sec.material]
        _, I22_eff, I33_eff, _ = self._eff_props(sec)
        EIz = mat.E * I33_eff                     # x-y plane (dy)
        EIy = mat.E * I22_eff                     # x-z plane (dz)
        if EIz <= 0.0 or EIy <= 0.0:              # pragma: no cover
            return None
        _, yax, zax, _, _ = _local_axes(m)

        def tdisp(tag: int, ax: Vec3) -> float:
            d = node_disp.get(tag)
            if d is None:                         # pragma: no cover
                return 0.0
            return d[0] * ax[0] + d[1] * ax[1] + d[2] * ax[2]

        # per-segment elastic-line parameters: (vy_i, th_y, vz_i, th_z)
        params: Dict[int, Tuple[float, float, float, float, tuple, list]] = {}
        for seg in segs:
            fi = corrected[seg.index][:6]
            recs = tuple(self._seg_span_loads.get((m.uid, seg.index), ()))
            Ls = seg.length
            vy_i = tdisp(seg.ni + 1, yax)
            vy_j = tdisp(seg.nj + 1, yax)
            vz_i = tdisp(seg.ni + 1, zax)
            vz_j = tdisp(seg.nj + 1, zax)
            Iy_L = _defl_double_integral(fi[1], fi[5], recs, 1, Ls)
            Iz_L = _defl_double_integral(fi[2], -fi[4], recs, 2, Ls)
            th_y = (vy_j - vy_i - Iy_L / EIz) / Ls
            th_z = (vz_j - vz_i - Iz_L / EIy) / Ls
            params[seg.index] = (vy_i, th_y, vz_i, th_z, recs, fi)

        dy: List[float] = []
        dz: List[float] = []
        for x in xs:
            seg = None
            for s in segs:
                if s.x0 - 1e-9 <= x <= s.x0 + s.length + 1e-9:
                    seg = s
                    break
            if seg is None:                        # numerical safety net
                seg = segs[-1]
            xi = min(max(x - seg.x0, 0.0), seg.length)
            vy_i, th_y, vz_i, th_z, recs, fi = params[seg.index]
            dy.append(float(
                vy_i + th_y * xi
                + _defl_double_integral(fi[1], fi[5], recs, 1, xi) / EIz))
            dz.append(float(
                vz_i + th_z * xi
                + _defl_double_integral(fi[2], -fi[4], recs, 2, xi) / EIy))
        return {"x": list(xs), "dy": dy, "dz": dz}

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

    @staticmethod
    def _shell_nodal(asm: _Assembly,
                     baseline: Optional[Dict[int, List[float]]] = None
                     ) -> Dict[int, List[float]]:
        """Global nodal resisting-force vector per shell quad (v0.15).

        ShellMITC4 'forces' returns 24 values (6 global dof x 4 nodes): the
        element's resisting forces at its nodes.  Summing them over the
        elements on ONE side of a cut line, at the cut-line nodes, gives the
        EXACT force/moment transmitted through the wall across that line
        (the free-body kernel of the pier post-processing).  ``baseline``
        (P-Delta two-stage runs) is subtracted so the case reports its own
        increment.  Captured only when the model has pier-labeled walls.
        """
        out: Dict[int, List[float]] = {}
        for qi, etag in enumerate(asm.quad_ele):
            vals = ops.eleResponse(etag, "forces")
            if len(vals) != 24:                  # pragma: no cover
                continue
            arr = [float(v) for v in vals]
            if baseline is not None and qi in baseline:
                arr = [a - b for a, b in zip(arr, baseline[qi])]
            out[qi] = arr
        return out

    # ------------------------------------------------ v0.15 wall pier forces
    def _pier_regions(self) -> List[Tuple[ShellRegion, str]]:
        """(region, pier label) pairs for every wall that reports piers.

        A wall reports when it carries an explicit ``pier`` label, or when
        ``model.auto_pier_walls`` is set (label = the wall uid).  Membrane-
        behavior walls have no FE and are skipped with a warning.
        """
        model = self.model
        auto = getattr(model, "auto_pier_walls", False)
        out: List[Tuple[ShellRegion, str]] = []
        for r in model.shells:
            if r.kind != "wall":
                continue
            label = getattr(r, "pier", "") or (r.uid if auto else "")
            if not label:
                continue
            if r.behavior != "shell":
                warnings.warn(
                    f"Wall {r.uid!r} (pier {label!r}): pier forces need "
                    "meshed shell behavior; membrane wall skipped")
                continue
            out.append((r, label))
        return out

    def _pier_cuts(self, asm: _Assembly) -> List[dict]:
        """Geometry of every (pier region, story) cut — case independent.

        Per labeled VERTICAL wall region and story it spans, the cut runs
        along the highest mesh node line at (or, when the mesh does not
        align with the story boundary, just below) the story-bottom
        elevation.  Each cut record carries the region's in-plane axes, the
        cut-line elements/nodes and the net-section centroid.
        """
        model = self.model
        ez = np.array([0.0, 0.0, 1.0])
        cuts: List[dict] = []
        for region, label in self._pier_regions():
            quads = [(qi, [np.asarray(asm.node_coords[t], float)
                           for t in q["nodes"]])
                     for qi, q in enumerate(asm.shell_quads)
                     if q["region"] == region.uid]
            if not quads:
                warnings.warn(f"Wall {region.uid!r} (pier {label!r}): no "
                              "meshed elements; skipped")
                continue
            c = [np.asarray(p, float) for p in region.corners]
            n_vec = np.cross(c[1] - c[0], c[3] - c[0])
            n_len = float(np.linalg.norm(n_vec))
            if n_len < 1e-12 or abs(n_vec[2]) / n_len > 1e-3:
                warnings.warn(f"Wall {region.uid!r} (pier {label!r}): not a "
                              "vertical wall; pier forces skipped")
                continue
            nhat = n_vec / n_len
            # in-plane horizontal axis: hhat = ez x nhat, so for the natural
            # corner ordering (bottom edge first, CCW) hhat follows the
            # corner-0 -> corner-1 direction; V is positive along +hhat.
            hhat = np.cross(ez, nhat)
            hhat = hhat / np.linalg.norm(hhat)
            n_inplane = np.cross(hhat, ez)      # in-plane moment axis
            all_z = sorted({round(float(p[2]), 6)
                            for _, pts in quads for p in pts})
            z_lo, z_hi = all_z[0], all_z[-1]
            for s in model.stories:
                z_bot = s.elevation - s.height
                if z_lo >= s.elevation - _TOL or z_hi <= z_bot + _TOL:
                    continue                     # wall does not span story
                below = [z for z in all_z if z <= z_bot + _TOL]
                z_line = max(below) if below else z_lo
                cut_elems: List[Tuple[int, List[int], list]] = []
                for qi, pts in quads:
                    zc = float(np.mean([p[2] for p in pts]))
                    if zc <= z_line + 1e-9:
                        continue
                    ks = [k for k, p in enumerate(pts)
                          if abs(float(p[2]) - z_line) < _TOL]
                    if ks:
                        cut_elems.append((qi, ks, pts))
                if not cut_elems:
                    continue
                # net-section centroid along hhat (tributary-width weighted)
                s_vals = sorted({round(float(p @ hhat), 6)
                                 for _, ks, pts in cut_elems
                                 for k, p in enumerate(pts) if k in ks})
                if len(s_vals) > 1:
                    trib = [(s_vals[min(i + 1, len(s_vals) - 1)]
                             - s_vals[max(i - 1, 0)]) / 2.0
                            for i in range(len(s_vals))]
                    sbar = (sum(sv * tw for sv, tw in zip(s_vals, trib))
                            / sum(trib))
                else:
                    sbar = s_vals[0]
                cuts.append({"label": label, "story": s.name,
                             "region": region.uid, "hhat": hhat,
                             "n_inplane": n_inplane, "z_line": z_line,
                             "z_bot": z_bot, "sbar": sbar,
                             "elems": cut_elems})
        return cuts

    @staticmethod
    def _pier_cut_forces(cut: dict,
                         shell_nodal: Dict[int, List[float]]
                         ) -> Tuple[float, float, float]:
        """(P, V, M) transmitted through one pier cut (exact free body).

        Sums the global nodal resisting forces of the wall elements ABOVE
        the cut line at the cut-line nodes.  Conventions (CONTRACT v0.15):
        P = +sum(fz) (compression positive), V = -sum(f . hhat) (positive
        along +hhat, the sense of a lateral load applied above the cut),
        M = sum((s - sbar)*fz + m . (hhat x ez)) about the net-section
        centroid (drilling nodal moments included — they close the moment
        balance exactly), then transferred from the cut line down to the
        story-bottom elevation with M += V*(z_line - z_bot) (exact when no
        in-plane load acts between the two levels — no nodes exist there).
        """
        hhat = cut["hhat"]
        n_ip = cut["n_inplane"]
        sbar = cut["sbar"]
        P = V = M = 0.0
        for qi, ks, pts in cut["elems"]:
            f = shell_nodal.get(qi)
            if f is None:
                continue
            for k in ks:
                F = f[6 * k: 6 * k + 3]
                Mv = f[6 * k + 3: 6 * k + 6]
                P += F[2]
                V -= float(F[0] * hhat[0] + F[1] * hhat[1] + F[2] * hhat[2])
                s_n = float(pts[k] @ hhat)
                M += (s_n - sbar) * F[2]
                M += float(Mv[0] * n_ip[0] + Mv[1] * n_ip[1]
                           + Mv[2] * n_ip[2])
        M += V * (cut["z_line"] - cut["z_bot"])
        return P, V, M

    def _compute_piers(self, asm: _Assembly,
                       sources: Dict[str, CaseResults]
                       ) -> Dict[str, Dict[str, Dict[str, Dict[str, float]]]]:
        """results['piers']: case/combo -> pier label -> story -> P/V/M."""
        cuts = self._pier_cuts(asm)
        if not cuts:
            return {}
        piers: Dict[str, Dict[str, Dict[str, Dict[str, float]]]] = {}
        for cname, cr in sources.items():
            if not cr.shell_nodal:
                continue
            by_pier: Dict[str, Dict[str, Dict[str, float]]] = {}
            for cut in cuts:
                P, V, M = self._pier_cut_forces(cut, cr.shell_nodal)
                entry = by_pier.setdefault(cut["label"], {}).setdefault(
                    cut["story"], {"P": 0.0, "V": 0.0, "M": 0.0})
                entry["P"] += P
                entry["V"] += V
                entry["M"] += M
            if by_pier:
                piers[cname] = by_pier
        return piers

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
    def _add_spring_reactions(asm: _Assembly,
                             node_disp: Dict[int, List[float]],
                             reactions: Dict[int, List[float]]) -> None:
        """Add each grounded spring's reaction (-k*disp) to its real node.

        The sprung DOFs are free in OpenSees (the spring is an element, not a
        constraint), so ``nodeReaction`` reports 0 there — adding ``-k*disp``
        is exact and never double-counts the fixed (non-sprung) DOFs.
        """
        for t, kvec in asm.spring_nodes.items():
            disp = node_disp.get(t, [0.0] * 6)
            r = reactions.setdefault(t, [0.0] * 6)
            for d in range(6):
                if kvec[d] != 0.0:
                    r[d] += -kvec[d] * disp[d]

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

    # ------------------------------------------ v0.11 gravity load takedown
    def _grid_label(self, x: float, y: float) -> str:
        """Nearest grid intersection label (e.g. "A-1"); "" when no grid."""
        g = self.model.grid
        if not g or not g.x_lines or not g.y_lines:
            return ""
        xi = min(range(len(g.x_lines)),
                 key=lambda i: abs(g.x_lines[i] - x))
        yi = min(range(len(g.y_lines)),
                 key=lambda i: abs(g.y_lines[i] - y))
        return f"{g.x_labels[xi]}-{g.y_labels[yi]}"

    def _area_load_fz(self, asm: _Assembly, region, q: float) -> float:
        """Downward reaction-equivalent FZ (kN) of an area load q (kPa)."""
        if region.behavior == "shell":
            trib = asm.mesh.region_trib.get(region.uid, {})
            return q * sum(trib.values())
        return q * region.net_area

    def _applied_gravity_fz(self, asm: _Assembly,
                            patterns: Dict[str, float]) -> float:
        """Total downward gravity load (kN, reaction-equivalent FZ) applied by
        a pattern combination.

        Independent of the solved reactions — summed straight from the load
        definitions with the SAME vertical-load rules the engine applies
        (gravity member loads skip vertical members; self-weight and
        ``global_z`` loads act on all members; area loads use the meshed net
        tributary / membrane net area) — so it can verify reaction balance.
        """
        model = self.model
        total = 0.0

        def member_fz(m: FrameMember, direction: str, w_total: float) -> float:
            _, yax, _, _, vertical = _local_axes(m)
            if direction == "gravity":
                return 0.0 if vertical else w_total       # global -Z
            if direction == "global_z":
                return -w_total                           # global +Z
            if direction == "local_y":
                return -w_total * yax[2]
            return 0.0                                    # global_x / global_y

        for pname, scale in patterns.items():
            pat = model.patterns.get(pname)
            if pat is None:
                continue
            for udl in pat.member_udls:
                m = self._members_by_uid.get(udl.member_uid)
                if m is not None:
                    total += scale * member_fz(m, "gravity", udl.w * m.length)
            for ml in pat.member_loads:
                m = self._members_by_uid.get(ml.member_uid)
                if m is None:
                    continue
                if ml.kind == "point":
                    w_tot = ml.w
                elif ml.kind == "udl":
                    w_tot = ml.w * (ml.b - ml.a) * m.length
                else:  # trapezoid
                    w_tot = 0.5 * (ml.w + ml.w2) * (ml.b - ml.a) * m.length
                total += scale * member_fz(m, ml.direction, w_tot)
            for nl in pat.nodal_loads:
                total += scale * (-nl.fz)
            swf = getattr(pat, "self_weight_factor", 0.0)
            if swf:
                for m in model.members:
                    sec = model.sections.get(m.section)
                    mat = model.materials.get(sec.material) if sec else None
                    if sec is None or mat is None:
                        continue
                    total += scale * swf * sec.A * mat.unit_weight * m.length
                for region in model.shells:
                    ssec = model.shell_sections.get(region.section)
                    mat = model.materials.get(ssec.material) if ssec else None
                    if ssec is None or mat is None:
                        continue
                    q = swf * ssec.thickness * mat.unit_weight
                    total += scale * self._area_load_fz(asm, region, q)
            for al in pat.area_loads:
                region = model._shell(al.region_uid)
                if region is not None:
                    total += scale * self._area_load_fz(asm, region, al.q)
        return total

    def _takedown(self, asm: _Assembly, cr: CaseResults,
                  patterns: Dict[str, float]) -> dict:
        """Support-reaction takedown for one gravity case/combo (v0.11).

        Per support node (real supports incl. springs): vertical reaction FZ
        (plus FX/FY), labelled by nearest grid intersection.  ``total_FZ`` is
        the reaction sum; ``applied_FZ`` the independently-summed applied
        gravity; ``balance_ok`` checks they match.
        """
        supports: List[dict] = []
        total_fz = 0.0
        for t in sorted(cr.reactions):
            r = cr.reactions[t]
            c = asm.node_coords.get(t, (0.0, 0.0, 0.0))
            fz = float(r[2])
            total_fz += fz
            supports.append({
                "node": int(t), "grid": self._grid_label(c[0], c[1]),
                "x": float(c[0]), "y": float(c[1]),
                "FZ": fz, "FX": float(r[0]), "FY": float(r[1])})
        applied = self._applied_gravity_fz(asm, patterns)
        tol = 1e-6 * max(1.0, abs(applied))
        return {"supports": supports, "total_FZ": float(total_fz),
                "applied_FZ": float(applied),
                "balance_ok": bool(abs(total_fz - applied) <= tol)}

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

    # ------------------------------------------------ v0.8 story diagnostics
    def _compute_story_props(self) -> Dict[str, Dict[str, float]]:
        """Per-story center of mass / center of rigidity (rigid diaphragms).

        CM is the load-distribution centroid of the story mass.  CR is found
        by the standard unit-load method: on the SAME assembled elastic
        model, a unit Fx, a unit Fy, and a unit torque Mz are applied in turn
        at each story's diaphragm master and the master rotation rz is read.
        With ``phi`` = rz per unit torque, the CR eccentricities from the
        master are ``e_y = theta_x/phi`` (rz under unit Fx) and
        ``e_x = -theta_y/phi`` (rz under unit Fy): the point about which a
        story shear produces no diaphragm rotation.  Only stories with a
        master (rigid diaphragm) get an entry; the keys are omitted
        otherwise.
        """
        model = self.model
        asm = self._build()
        if not asm.masters:
            return {}
        master_xy = {s: (asm.node_coords[t][0], asm.node_coords[t][1])
                     for s, t in asm.masters.items()}
        cm = self._story_cm()
        props: Dict[str, Dict[str, float]] = {}
        for s in model.stories:
            if s.name not in master_xy:
                continue
            mx, my = master_xy[s.name]
            theta_x = self._master_rz(s.name, (1.0, 0.0, 0.0, 0.0, 0.0, 0.0))
            theta_y = self._master_rz(s.name, (0.0, 1.0, 0.0, 0.0, 0.0, 0.0))
            phi = self._master_rz(s.name, (0.0, 0.0, 0.0, 0.0, 0.0, 1.0))
            cmx, cmy = cm.get(s.name, (mx, my))
            entry = {"cm_x": float(cmx), "cm_y": float(cmy)}
            if abs(phi) > 1e-30:
                entry["cr_x"] = float(mx - theta_y / phi)
                entry["cr_y"] = float(my + theta_x / phi)
            else:                                       # pragma: no cover
                entry["cr_x"], entry["cr_y"] = float(mx), float(my)
            props[s.name] = entry
        # restore the elastic assembly for the caller (identical geometry)
        self._build()
        return props

    def _master_rz(self, story: str, load_vec: Tuple[float, ...]) -> float:
        """Diaphragm-master rz under a single unit load at that master."""
        asm = self._build()
        mtag = asm.masters[story]
        self._seg_span_loads = {}
        self._seg_fef = {}
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        ops.load(mtag, *load_vec)
        self._setup_analysis(asm)
        if ops.analyze(1) != 0:                          # pragma: no cover
            raise RuntimeError(f"CR unit-load solve failed for story "
                               f"{story!r}")
        return float(ops.nodeDisp(mtag)[5])

    def _story_cm(self) -> Dict[str, Tuple[float, float]]:
        """Story center of mass: the centroid of the story's gravity load."""
        model = self.model
        source = model.effective_mass_source()
        out: Dict[str, Tuple[float, float]] = {}
        for s in model.stories:
            if s.name in model.story_masses:
                out[s.name] = model.plan_center()   # explicit: no spatial info
                continue
            wsum = wx = wy = 0.0

            def add(w: float, x: float, y: float) -> None:
                nonlocal wsum, wx, wy
                wsum += w
                wx += w * x
                wy += w * y

            def mid(m: FrameMember) -> Tuple[float, float]:
                return (0.5 * (m.pi[0] + m.pj[0]), 0.5 * (m.pi[1] + m.pj[1]))

            for pname, fac in source.items():
                pat = model.patterns.get(pname)
                if pat is None:
                    continue
                for udl in pat.member_udls:
                    m = model._member(udl.member_uid)
                    if m is not None and m.story == s.name and \
                            m.kind != "column":
                        add(fac * udl.w * m.length, *mid(m))
                for ml in pat.member_loads:
                    if ml.direction != "gravity":
                        continue
                    m = model._member(ml.member_uid)
                    if m is None or m.story != s.name or m.kind == "column":
                        continue
                    if ml.kind == "point":
                        w = fac * ml.w
                    elif ml.kind == "udl":
                        w = fac * ml.w * (ml.b - ml.a) * m.length
                    else:
                        w = fac * 0.5 * (ml.w + ml.w2) * (ml.b - ml.a) \
                            * m.length
                    add(w, *mid(m))
                for al in pat.area_loads:
                    region = model._shell(al.region_uid)
                    if region is None or \
                            not model._region_on_story(region, s):
                        continue
                    c = region.map_uv(0.5, 0.5)
                    add(fac * al.q * region.net_area, c[0], c[1])
                for nl in pat.nodal_loads:
                    if abs(nl.point[2] - s.elevation) < 1e-6:
                        add(fac * (-nl.fz), nl.point[0], nl.point[1])
                swf = getattr(pat, "self_weight_factor", 0.0)
                if swf:
                    for m in model.members:
                        if m.story != s.name or m.kind == "column":
                            continue
                        sec = model.sections.get(m.section)
                        mat = (model.materials.get(sec.material)
                               if sec else None)
                        if sec is not None and mat is not None:
                            add(fac * swf * sec.A * mat.unit_weight
                                * m.length, *mid(m))
                    for region in model.shells:
                        if not model._region_on_story(region, s):
                            continue
                        ssec = model.shell_sections.get(region.section)
                        mat = (model.materials.get(ssec.material)
                               if ssec else None)
                        if ssec is not None and mat is not None:
                            c = region.map_uv(0.5, 0.5)
                            add(fac * swf * ssec.thickness * mat.unit_weight
                                * region.net_area, c[0], c[1])
            if wsum > 1e-12:
                out[s.name] = (wx / wsum, wy / wsum)
            else:
                out[s.name] = model.plan_center()
        return out

    # ---------------------------------------- v0.9 seismic diagnostics
    @staticmethod
    def _tors_ratio(u: float, rz: float, B: float) -> float:
        """ASCE 7 §12.3.2.1 torsional ratio delta_max/delta_avg of two ends.

        The diaphragm ends transverse to the loading axis are at +/- B/2 from
        the master; their displacements in the loading direction are
        ``u +/- rz*(B/2)``.  Returns 1.0 for a non-rotating (symmetric) story.
        """
        d1 = abs(u + rz * B / 2.0)
        d2 = abs(u - rz * B / 2.0)
        davg = 0.5 * (d1 + d2)
        return max(d1, d2) / davg if davg > 1e-30 else 1.0

    def _seismic_diagnostics(self, asm: _Assembly,
                             results_by_name: Dict[str, CaseResults]
                             ) -> Tuple[Dict[str, Dict[str, Dict[str, float]]],
                                        Dict[str, Dict[str, Dict[str, object]]]]:
        """Per lateral case: story lateral stiffness + ASCE 7 irregularity.

        Pure POST-PROCESSING of the already-solved case results (no new
        solves).  A case is diagnosed only when it carries a non-zero story
        shear.  Story stiffness ``k = V_story / Delta`` uses the interstory
        drift displacement ``Delta`` (m).  Torsional-irregularity ratios come
        from the diaphragm master's translation + rotation (§12.3.2.1); the
        soft-story stiffness ratio compares each story's stiffness (in the
        case's dominant loading direction) with the story above and the
        average of the three above (Table 12.3-2).
        """
        model = self.model
        lx, ly = model.plan_extents()
        stories = list(model.stories)               # bottom -> top
        order = [s.name for s in stories]
        stiffness: Dict[str, Dict[str, Dict[str, float]]] = {}
        irregularity: Dict[str, Dict[str, Dict[str, object]]] = {}
        for name, res in results_by_name.items():
            st = res.story
            if not any(abs(st.get(n, {}).get("shear_x", 0.0)) > 1e-12
                       or abs(st.get(n, {}).get("shear_y", 0.0)) > 1e-12
                       for n in order):
                continue
            kx_by: Dict[str, float] = {}
            ky_by: Dict[str, float] = {}
            prev_ux = prev_uy = 0.0
            sk: Dict[str, Dict[str, float]] = {}
            for s in stories:
                info = st.get(s.name)
                if info is None:
                    continue
                ux, uy = info["ux"], info["uy"]
                dx, dy = ux - prev_ux, uy - prev_uy
                vx, vy = info["shear_x"], info["shear_y"]
                kx = (abs(vx / dx) if abs(dx) > 1e-15 and abs(vx) > 1e-15
                      else 0.0)
                ky = (abs(vy / dy) if abs(dy) > 1e-15 and abs(vy) > 1e-15
                      else 0.0)
                kx_by[s.name] = kx
                ky_by[s.name] = ky
                sk[s.name] = {"kx": kx, "ky": ky}
                prev_ux, prev_uy = ux, uy
            stiffness[name] = sk
            # dominant loading direction picks the soft-story stiffness column
            sum_vx = sum(abs(st.get(n, {}).get("shear_x", 0.0)) for n in order)
            sum_vy = sum(abs(st.get(n, {}).get("shear_y", 0.0)) for n in order)
            k_primary = kx_by if sum_vx >= sum_vy else ky_by
            irr: Dict[str, Dict[str, object]] = {}
            for idx, s in enumerate(stories):
                if s.name not in st:
                    continue
                master = asm.masters.get(s.name)
                if master is not None and master in res.node_disp:
                    d = res.node_disp[master]
                    u_x, u_y, rz = d[0], d[1], d[5]
                else:
                    u_x = st[s.name]["ux"]
                    u_y = st[s.name]["uy"]
                    rz = 0.0
                tr_x = self._tors_ratio(u_x, rz, ly)
                tr_y = self._tors_ratio(u_y, rz, lx)
                trmax = max(tr_x, tr_y)
                flag = ("extreme" if trmax >= 1.4 else
                        "torsional" if trmax >= 1.2 else "none")
                entry: Dict[str, object] = {
                    "tors_ratio_x": tr_x, "tors_ratio_y": tr_y, "flag": flag}
                # soft-story stiffness ratio (Table 12.3-2)
                k_here = k_primary.get(s.name, 0.0)
                k_above = (k_primary.get(order[idx + 1])
                           if idx + 1 < len(order) else None)
                above3 = [k_primary.get(n, 0.0)
                          for n in order[idx + 1:idx + 4]]
                avg3 = (sum(above3) / len(above3)) if above3 else None
                soft_flag = ""
                if k_above and k_above > 1e-15:
                    entry["stiff_ratio"] = k_here / k_above
                    r = k_here / k_above
                    ra = (k_here / avg3) if avg3 and avg3 > 1e-15 else None
                    if r < 0.6 or (ra is not None and ra < 0.7):
                        soft_flag = "extreme_soft"
                    elif r < 0.7 or (ra is not None and ra < 0.8):
                        soft_flag = "soft"
                else:
                    entry["stiff_ratio"] = None
                entry["soft_flag"] = soft_flag
                irr[s.name] = entry
            irregularity[name] = irr
        return stiffness, irregularity

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
        # v0.16: transverse deflection stations superpose linearly (a member
        # present in every part — offset/truss members are absent everywhere)
        member_deflections: Dict[str, Dict[str, List[float]]] = {}
        for uid, md0 in parts[0][0].member_deflections.items():
            entry_d: Dict[str, List[float]] = {"x": list(md0["x"])}
            for key in ("dy", "dz"):
                entry_d[key] = [
                    sum(f * res.member_deflections[uid][key][i]
                        for res, f in parts)
                    for i in range(len(md0[key]))]
            member_deflections[uid] = entry_d
        # shell stress resultants superpose linearly too (v0.4)
        shell_forces: Dict[int, List[float]] = {}
        for qi, v0 in parts[0][0].shell_forces.items():
            shell_forces[qi] = [
                sum(f * res.shell_forces[qi][i] for res, f in parts)
                for i in range(len(v0))]
        # v0.15: shell nodal forces (pier free-body kernel) likewise
        shell_nodal: Dict[int, List[float]] = {}
        for qi, v0 in parts[0][0].shell_nodal.items():
            shell_nodal[qi] = [
                sum(f * res.shell_nodal[qi][i] for res, f in parts)
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
            shell_nodal=shell_nodal,
            member_deflections=member_deflections,
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
        snap_nodal: Dict[int, List[float]] = {}
        two_stage = case.pdelta_gravity is not None
        piers_on = self._piers_enabled()

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
            if piers_on:
                snap_nodal = self._shell_nodal(asm)
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
        # node_disp here is already the case increment; -k*disp gives the
        # incremental spring reaction, consistent with the other quantities.
        self._add_spring_reactions(asm, node_disp, reactions)
        base = self._base_totals(asm, reactions)
        member_forces, member_stations, member_deflections = \
            self._member_outputs(asm, baseline=snap_ele, node_disp=node_disp)
        story = self._story_results(asm, case, node_disp)
        shell_forces = self._shell_outputs(
            asm, baseline=snap_shell if two_stage else None)
        shell_nodal = (self._shell_nodal(
            asm, baseline=snap_nodal if two_stage else None)
            if piers_on else {})
        return CaseResults(case.name, node_disp, reactions, base,
                           member_forces, story, member_stations,
                           shell_forces=shell_forces,
                           shell_nodal=shell_nodal,
                           member_deflections=member_deflections)

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
        sub.spring_supports = [s for s in model.spring_supports
                               if s.point[2] <= elev_k + _TOL]
        sub.thermal_alpha = model.thermal_alpha
        sub.rigid_diaphragms = model.rigid_diaphragms
        sub.diaphragm = model.diaphragm
        sub.story_diaphragm = {s2: v for s2, v in
                               model.story_diaphragm.items() if s2 in names}
        sub.links = [lk for lk in model.links
                     if max(lk.pi[2], lk.pj[2]) <= elev_k + _TOL]
        sub.panel_zones = getattr(model, "panel_zones", "none")   # v0.17
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
        accel, dt = self._resolve_th_record(th)
        nonlinear = bool(getattr(th, "nonlinear", False))
        # v0.15: any advanced device link (damper/gap/hook/isolator) makes
        # the transient solve implicit-nonlinear (Newton), even without
        # hinges — device element forces are state/velocity dependent.
        devices = self._device_links_present()
        use_newton = nonlinear or devices

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
        if use_newton:
            # committed-stiffness proportionality (betaKcomm): the a0/a1
            # FIT comes from the initial elastic modes, but C follows the
            # committed stiffness so the stiff hinge springs cannot
            # generate spurious post-yield damping moments (the well-known
            # betaKinit artifact: a1*k_theta*theta_dot can exceed My).
            # v0.15 device links use the same choice: a gap/isolator whose
            # tangent switches state should not carry current-tangent
            # damping from mid-iteration stiffness.
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

        ops.timeSeries("Path", 1, "-dt", float(dt),
                       "-values", *[float(a) for a in accel],
                       "-factor", float(th.scale))
        ops.pattern("UniformExcitation", 1, dof, "-accel", 1)

        ops.wipeAnalysis()
        ops.constraints("Transformation" if asm.use_transformation
                        else "Plain")
        ops.numberer("RCM")
        ops.system("BandGeneral")
        if use_newton:
            ops.test("NormDispIncr", 1.0e-8, 25)
            ops.algorithm("Newton")
        else:
            ops.algorithm("Linear")
        ops.integrator("Newmark", 0.5, 0.25)
        ops.analysis("Transient")

        massed = [(t, d, m) for (t, d), m in asm.mass_map.items()
                  if d in (1, 2)]
        massed_tags = sorted({t for t, _, _ in massed})
        n = len(accel)
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
            ok = ops.analyze(1, dt)
            if ok != 0 and use_newton:
                ops.algorithm("NewtonLineSearch")
                ok = ops.analyze(1, dt)
                ops.algorithm("Newton")
            if ok != 0:
                raise RuntimeError(
                    f"Time-history analysis failed at step {k + 1}/{n} "
                    f"for case {name!r}")
            t_out.append((k + 1) * dt)
            # story displacements (masters, else story-node average)
            for s in stories:
                ux, uy = story_uxuy(s)
                sux[s.name].append(ux - story0[s.name][0])
                suy[s.name].append(uy - story0[s.name][1])
            # inertia-equilibrium story shears: total accel = relative
            # (nodeAccel) + ground (Path sample at the current time)
            ag = th.scale * (accel[k + 1] if k + 1 < n else 0.0)
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
    def _resolve_rs_spectrum(self, rs) -> List[List[float]]:
        """Effective spectrum points for an RS case (v0.13 function library).

        When ``rs.function`` names a ``model.spectrum_functions`` entry, that
        function's points are used in place of the inline ``rs.spectrum``;
        a missing name is an error.  With no function the inline spectrum is
        returned unchanged (backward compatible).
        """
        fn_name = getattr(rs, "function", "") or ""
        if not fn_name:
            return rs.spectrum
        fn = self.model.spectrum_functions.get(fn_name)
        if fn is None:
            raise ValueError(
                f"RS case {rs.name!r}: spectrum function {fn_name!r} is not "
                "defined in model.spectrum_functions")
        return fn.points

    def _resolve_th_record(self, th) -> Tuple[List[float], float]:
        """Effective (accel, dt) for a TH case (v0.13 function library).

        When ``th.function`` names a ``model.th_functions`` entry, that
        function's values/dt are used in place of the inline ``th.accel``/
        ``th.dt``; a missing name is an error.  The case ``scale`` still
        multiplies the record (applied by the caller).
        """
        fn_name = getattr(th, "function", "") or ""
        if not fn_name:
            return th.accel, th.dt
        fn = self.model.th_functions.get(fn_name)
        if fn is None:
            raise ValueError(
                f"TH case {th.name!r}: time-history function {fn_name!r} is "
                "not defined in model.th_functions")
        return fn.values, fn.dt

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
        spectrum = self._resolve_rs_spectrum(rs)

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
            sa = _interp_spectrum(spectrum, T) * rs.scale * G_ACCEL
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

    def run_rs_directional(self, name_x: str, name_y: str,
                           method: str = "100_30",
                           name: str = "") -> CaseResults:
        """Directional combination of two RS cases (ASCE 7-16 §12.5, v0.10).

        Combines the X-direction RS case ``name_x`` and the Y-direction case
        ``name_y`` per response quantity ``q``:

        * ``"100_30"`` → ``max(|qx| + 0.3|qy|, 0.3|qx| + |qy|)``;
        * ``"SRSS"``   → ``sqrt(qx^2 + qy^2)``.

        The base RS results are already positive envelopes, so the result is
        positive.  Returns a :class:`CaseResults` with the standard case
        shape (node_disp / reactions / base / member_forces / story /
        member_stations).
        """
        if method not in ("100_30", "SRSS"):
            raise ValueError(f"RS directional method must be 100_30|SRSS, "
                             f"got {method!r}")
        rx = self.run_response_spectrum(name_x)
        ry = self.run_response_spectrum(name_y)

        def comb(a: float, b: float) -> float:
            aa, ab = abs(a), abs(b)
            if method == "SRSS":
                return math.sqrt(aa * aa + ab * ab)
            return max(aa + 0.3 * ab, 0.3 * aa + ab)

        def comb_vecs(gx: dict, gy: dict) -> dict:
            return {k: [comb(gx[k][i], gy[k][i]) for i in range(len(v))]
                    for k, v in gx.items()}

        base = {k: comb(rx.base[k], ry.base[k])
                for k in ("FX", "FY", "FZ", "MX", "MY", "MZ")}
        story = {s: {k: comb(rx.story[s][k], ry.story[s][k]) for k in s0}
                 for s, s0 in rx.story.items()}
        member_stations: Dict[str, Dict[str, List[float]]] = {}
        for uid, st0 in rx.member_stations.items():
            entry: Dict[str, List[float]] = {"x": list(st0["x"])}
            for key in ("N", "V2", "V3", "T", "M2", "M3"):
                entry[key] = [comb(rx.member_stations[uid][key][i],
                                   ry.member_stations[uid][key][i])
                              for i in range(len(st0[key]))]
            member_stations[uid] = entry
        return CaseResults(
            name=name or f"{name_x}+{name_y}",
            node_disp=comb_vecs(rx.node_disp, ry.node_disp),
            reactions=comb_vecs(rx.reactions, ry.reactions),
            base=base,
            member_forces=comb_vecs(rx.member_forces, ry.member_forces),
            story=story,
            member_stations=member_stations,
        )

    def run_buckling(self, name: str) -> BucklingResult:
        """Run one linear buckling case (self-contained numpy; never capped).

        Delegates to :func:`skyframe.core.buckling.buckling_analysis`, which
        assembles the elastic and geometric stiffness of the frame, extracts
        member axial forces under the case's reference gravity, and returns
        the smallest positive load factors and mode shapes.
        """
        model = self.model
        if name not in model.buckling_cases:
            raise ValueError(f"Unknown buckling case {name!r}")
        bc = model.buckling_cases[name]
        return buckling_analysis(model, bc.gravity, num_modes=bc.num_modes)

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
        self._add_spring_reactions(asm, node_disp, reactions)
        base = self._base_totals(asm, reactions)
        # deflection recovery is skipped for modal statics (RS results are
        # positive envelopes; deflections are a static-case quantity)
        member_forces, member_stations, _ = self._member_outputs(asm)

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
