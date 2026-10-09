"""Pushover load distribution and control (ETABS Define > Load Cases >
Nonlinear Static parity).

:func:`plan_push` turns a :class:`~skyframe.core.model.PushoverCase` into a
:class:`PushPlan` — the lateral reference load actually pushed (keyed by
LOCATION so it survives the hinge rebuild's tag shifts), its total in the
push direction ``v_ref`` (so base shear = lambda * v_ref exactly, by
statics) and the normalised story-force shape reported back to the user.
:func:`resolve_control` picks the monitored joint/DOF and target.  The
default ``roof_point`` distribution returns ``None`` from
:func:`plan_push`, and the engine then runs the pre-feature unit roof push
bit-identically.

Distributions (all lateral forces act in the push ``direction`` only):

* ``roof_point`` — unit force at the roof control node (pre-feature);
* ``pattern`` — the named load pattern's loads, scaled by lambda; v_ref is
  the pattern's net applied force in the push direction, measured as minus
  the summed support reactions of a linear elastic solve of the pattern;
* ``mode`` — node force f = m * phi (push-direction mass and mode-shape
  component) of mode ``mode_number`` of the model's own elastic eigen
  solve; ``mode_number = None`` picks the mode with the largest effective
  modal mass ratio in the push direction (ASCE 41 "first mode");
* ``uniform_accel`` — f = m (a uniform acceleration field);
* ``triangular`` — f = m * h^k, h = node elevation above the base: the
  ASCE 7-16 Eq. 12.8-12 vertical distribution C_vx = w_x h_x^k / sum w h^k
  realised node-by-node (a story force is split over its nodes by mass).
  ``k = None`` uses ASCE 7 §12.8.3: k = 1 for T <= 0.5 s, 2 for T >= 2.5 s,
  linear between, with T the period of the dominant mode in the push
  direction.

Every mass/shape distribution is normalised so the applied forces sum to
exactly v_ref = sum(f) = 1 (unit base shear at lambda = 1).  Masses on
restrained DOFs (supports) carry no push force.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import openseespy.opensees as ops

from skyframe.core.model import LoadCase

_TOL = 1e-6

Key = Tuple[str, object]        # ("master", story) | ("node", (x, y, z))


def asce7_k(period: float) -> float:
    """ASCE 7-16 §12.8.3 distribution exponent k(T)."""
    if period <= 0.5:
        return 1.0
    if period >= 2.5:
        return 2.0
    return 1.0 + (period - 0.5) / 2.0


@dataclass
class PushPlan:
    """The lateral reference load of one pushover (see module doc)."""

    kind: str
    loads: List[Tuple[Key, float]] = field(default_factory=list)
    v_ref: float = 1.0
    story_forces: Dict[str, float] = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    def apply(self, engine, asm, dof: int, case) -> None:
        """Add the reference load to the ACTIVE OpenSees pattern."""
        if self.kind == "pattern":
            engine._apply_pattern(asm, case.pattern, 1.0)
            return
        for key, f in self.loads:
            if key[0] == "master":
                tag = asm.masters[key[1]]
            else:
                tag = engine._find_node(asm, key[1])
            vec = [0.0] * 6
            vec[dof - 1] = f
            ops.load(tag, *vec)

    def to_dict(self) -> dict:
        return {"type": self.kind,
                "story_forces": {s: float(v)
                                 for s, v in self.story_forces.items()},
                "normalization": "sum of applied push forces = 1 "
                                 "(base shear = lambda * reference_base_"
                                 "shear)",
                "reference_base_shear": float(self.v_ref),
                "params": dict(self.params)}


def roof_point_dict(top_story: str) -> dict:
    """Distribution report of the default unit roof push."""
    return PushPlan("roof_point", story_forces={top_story: 1.0},
                    params={}).to_dict()


def _key_of(asm, tag: int) -> Key:
    for s, mt in asm.masters.items():
        if mt == tag:
            return ("master", s)
    return ("node", tuple(asm.node_coords[tag]))


def _story_of_z(model, z: float) -> Optional[str]:
    for s in model.stories:
        if abs(s.elevation - z) < _TOL:
            return s.name
    return None


def _push_masses(asm, dof: int) -> Dict[int, float]:
    """tag -> push-direction mass on UNRESTRAINED dofs."""
    out: Dict[int, float] = {}
    for (t, d), m in asm.mass_map.items():
        if d != dof or m <= 0.0:
            continue
        if asm.node_restraints.get(t, (0,) * 6)[d - 1]:
            continue
        out[t] = out.get(t, 0.0) + m
    return out


def _eigen(engine, asm, dof: int, want: Optional[int]) -> dict:
    """Eigen solve on ``asm``: dominant (or requested) mode in ``dof``."""
    model = engine.model
    n_massed = asm.free_massed_dofs()
    n = min(max(int(model.num_modes), int(want or 1)), n_massed)
    if n <= 0:
        raise ValueError("pushover: the model has no free mass (no modes)")
    if want is not None and want > n:
        raise ValueError(f"pushover: mode_number {want} exceeds the {n} "
                         "modes available")
    engine._setup_analysis(asm)
    lambdas = engine._solve_eigen(n, n_massed, asm)
    m_dir = sum(_push_masses(asm, dof).values())
    best, best_ratio = 1, -1.0
    ratios = []
    for j in range(1, n + 1):
        L = mgen = 0.0
        for (t, d), m in asm.mass_map.items():
            if asm.node_restraints.get(t, (0,) * 6)[d - 1]:
                continue
            phi = ops.nodeEigenvector(t, j, d)
            mgen += m * phi * phi
            if d == dof:
                L += m * phi
        r = (L * L / mgen / m_dir) if (mgen > 0.0 and m_dir > 0.0) else 0.0
        ratios.append(r)
        if r > best_ratio + 1e-12:
            best, best_ratio = j, r
    mode = want or best
    phi = {t: ops.nodeEigenvector(t, mode, dof)
           for t in _push_masses(asm, dof)}
    period = 2.0 * math.pi / math.sqrt(lambdas[mode - 1])
    return {"mode": mode, "period": period, "phi": phi,
            "mass_ratio": ratios[mode - 1]}


def _story_report(model, asm, forces: Dict[int, float]) -> Dict[str, float]:
    rep = {s.name: 0.0 for s in model.stories}
    for t, f in forces.items():
        s = _story_of_z(model, asm.node_coords[t][2])
        if s is not None:
            rep[s] += f
    return rep


def plan_push(engine, case) -> Optional[PushPlan]:
    """The case's push plan; ``None`` for the default ``roof_point``."""
    kind = getattr(case, "load_distribution", "roof_point")
    if kind == "roof_point":
        return None
    model = engine.model
    dof = 1 if case.direction == "X" else 2
    asm = engine._build()                       # elastic, no hinges
    if kind == "pattern":
        return _plan_pattern(engine, asm, case, dof)

    masses = _push_masses(asm, dof)
    if not masses:
        raise ValueError(f"Pushover case {case.name!r}: "
                         f"load_distribution {kind!r} needs lateral mass "
                         f"in {case.direction}")
    params: dict = {}
    if kind == "uniform_accel":
        raw = dict(masses)
    elif kind == "mode":
        eg = _eigen(engine, asm, dof, case.mode_number)
        raw = {t: m * eg["phi"][t] for t, m in masses.items()}
        params = {"mode_number": eg["mode"], "period": eg["period"],
                  "mass_ratio": eg["mass_ratio"]}
    else:                                       # triangular
        z0 = min(c[2] for c in asm.struct_coords.values())
        if case.k is not None:
            k = float(case.k)
            params = {"k": k}
        else:
            eg = _eigen(engine, asm, dof, None)
            k = asce7_k(eg["period"])
            params = {"k": k, "period": eg["period"],
                      "mode_number": eg["mode"]}
        raw = {t: m * max(asm.node_coords[t][2] - z0, 0.0) ** k
               for t, m in masses.items()}
    total = sum(raw.values())
    scale_abs = sum(abs(v) for v in raw.values())
    if scale_abs <= 0.0 or abs(total) < 1e-9 * scale_abs:
        raise ValueError(f"Pushover case {case.name!r}: the {kind!r} "
                         "distribution has no net force in "
                         f"{case.direction}")
    forces = {t: v / total for t, v in raw.items() if v != 0.0}
    loads = [(_key_of(asm, t), f) for t, f in sorted(forces.items())]
    v_ref = sum(f for _, f in loads)
    return PushPlan(kind, loads, v_ref,
                    _story_report(model, asm, forces), params)


def _plan_pattern(engine, asm, case, dof: int) -> PushPlan:
    """v_ref of a pattern push = its net applied force (linear solve)."""
    model = engine.model
    engine._seg_span_loads = {}
    engine._seg_fef = {}
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    engine._apply_pattern(asm, case.pattern, 1.0)
    if (engine._axial_only_present()
            or engine._nonlinear_static_links_present()
            or engine._compression_only_springs_present()):
        engine._setup_nonlinear_analysis(asm)
    else:
        engine._setup_analysis(asm)
    if ops.analyze(1) != 0:
        raise RuntimeError(f"Pushover case {case.name!r}: linear solve of "
                           f"pattern {case.pattern!r} failed")
    node_disp = {t: list(ops.nodeDisp(t)) for t in asm.node_coords}
    ops.reactions()
    reactions = {t: list(ops.nodeReaction(t)) for t in asm.support_tags}
    engine._add_spring_reactions(asm, node_disp, reactions)
    base = engine._base_totals(asm, reactions)
    v_ref = -base["FX" if dof == 1 else "FY"]
    engine._seg_span_loads = {}
    engine._seg_fef = {}
    if abs(v_ref) < 1e-9:
        raise ValueError(f"Pushover case {case.name!r}: pattern "
                         f"{case.pattern!r} has no net lateral load in "
                         f"{case.direction}")
    # story shape: lateral story forces + nodal loads of the pattern
    shears = engine._story_shears(LoadCase("_po_", {case.pattern: 1.0}))
    idx = 0 if dof == 1 else 1
    stories = model.stories
    rep: Dict[str, float] = {}
    for i, s in enumerate(stories):
        above = shears[stories[i + 1].name][idx] if i + 1 < len(stories) \
            else 0.0
        rep[s.name] = (shears[s.name][idx] - above) / v_ref
    return PushPlan("pattern", [], v_ref, rep, {"pattern": case.pattern})


def resolve_gravity(model, case) -> Dict[str, float]:
    """The gravity stage: ``gravity`` or the ``start_from`` case patterns."""
    sf = getattr(case, "start_from", None)
    if sf and sf in model.cases:
        return dict(model.cases[sf].patterns)
    if sf:                      # a nonlinear static case: chained state
        return {}
    return case.gravity


def drive_node(asm, mon: int) -> int:
    """The node DisplacementControl drives for monitored joint ``mon``.

    A rigid-diaphragm SLAVE has no free ux/uy equation under the
    Transformation handler (DisplacementControl on it is meaningless), so
    its story master is driven instead; the capacity curve still records
    the slave joint's own displacement.
    """
    for s, mt in asm.masters.items():
        if mon in asm.story_nodes.get(s, ()):
            return mt
    return mon


def resolve_control(engine, asm, case, ctrl: int, dof: int, H: float
                    ) -> Tuple[int, int, float]:
    """(monitored node tag, dof, control height) for the push.

    Default: the roof control node / push dof / roof elevation (the
    pre-feature values, returned untouched).
    """
    mon, mdof, Hc = ctrl, dof, H
    if case.control_dof is not None:
        mdof = 1 if case.control_dof == "UX" else 2
    if case.control_story is not None:
        sname = case.control_story
        if sname in asm.masters:
            mon = asm.masters[sname]
        else:
            nodes = asm.story_nodes.get(sname) or []
            if not nodes:
                raise ValueError(f"Pushover case {case.name!r}: control "
                                 f"story {sname!r} has no nodes")
            mon = min(nodes)
        Hc = asm.node_coords[mon][2]
    elif case.control_point is not None:
        mon = engine._find_node(asm, tuple(case.control_point))
        Hc = asm.node_coords[mon][2]
    if Hc <= 0.0:
        Hc = H
    return mon, mdof, Hc


def setup_load_control(asm, inc: float) -> None:
    """The pushover Newton analysis with LoadControl(``inc``)."""
    ops.wipeAnalysis()
    ops.constraints("Transformation" if asm.use_transformation
                    else "Plain")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-6, 50)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", inc)
    ops.analysis("Static")
