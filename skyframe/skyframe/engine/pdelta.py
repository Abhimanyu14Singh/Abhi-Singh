"""Model-wide P-Delta options (ETABS Define > P-Delta Options) — engine side.

``BuildingModel.pdelta_options["method"]``:

* ``"none"`` (default) — nothing here runs; every build is byte-identical
  to the pre-existing engine (per-case ``LoadCase.pdelta`` / ``geometric``
  flags keep working exactly as before).
* ``"non_iterative_mass"`` (ETABS "based on mass") — story-level
  ``P_i = g * sum(mass above the bottom of story i)`` (the translational
  nodal masses of the BUILT model, ``asm.mass_map``), applied as a linear
  NEGATIVE lateral story stiffness ``-P_i/h_i`` in global X and Y.  No
  iteration.
* ``"iterative_loads"`` — the gravity combination ``load_factors`` is
  solved LINEARLY, each frame element's axial force ``N`` is extracted, the
  element "string" geometric stiffness ``N/L`` (tension-positive, so
  compression softens) is added on the element's two transverse relative
  translations, and the gravity combination is re-solved on ``K + Kg(N)``;
  repeated until ``max|N_k - N_{k-1}| / max|N_k| <= tolerance`` or
  ``max_iterations`` solves (iteration 1 is the plain ``K`` solve).  The
  LAST axial forces define the frozen ``Kg``.

HOW THE STIFFNESS IS APPLIED (one mechanism for every analysis).  ``Kg`` is
assembled as explicit linear ``zeroLength`` elements carrying negative
(compression) ``uniaxialMaterial Elastic`` stiffnesses between two
(generally non-coincident) nodes, oriented on the element axis (string:
local directions 2/3 = the two transverse translations) or on global X/Y
(story spring).  A zeroLength between non-coincident nodes is a pure
relative-translation spring without moment coupling — exactly the
linearized "lean-column" ``P-Delta`` term that OpenSees' ``PDelta``
geomTransf adds (``-P/L`` on the transverse sway translations, no
rotational terms), so an iterative_loads column reproduces the per-case
``pdelta`` two-stage result to round-off.  The springs are added at the end
of EVERY linear-transformation, hinge-free :meth:`OpenSeesEngine._build`,
so static cases, the modal eigen solve (``K + Kg`` vs ``M``), response
spectrum (its modes and its exact modal-static solves), linear time history
(Rayleigh fit + transient), the frequency-domain cases, load participation,
the pushover load-distribution modes and the stability check all see the
IDENTICAL stiffness — the reason for this choice over a ``loadConst``-held
PDelta-transform state (which would make static cases report increments
past a stressed state and could not be reused by the linear-algorithm /
frequency pipelines).  The springs are built with ``-doRayleigh 1`` so a
stiffness-proportional Rayleigh term uses the same ``K + Kg`` as the modes
it is fitted to (zeroLength defaults to no Rayleigh contribution).

Story springs (``non_iterative_mass``): between the story's rigid-diaphragm
master and the master of the level below (for the first story: an existing
support node fixed in UX and UY at/below the story bottom, so the spring's
ground force lands in the reported reactions and base totals keep
equalling the applied loads).  A story without such a pair falls back to
distributing ``P_i`` over the vertical frame columns of the story in
proportion to their axial stiffness ``EA/L`` (string springs on each column
element, ``-P_share/L_e``); a story with neither gets no spring (listed in
the results ``pdelta.skipped_stories``).  The story-spring form carries no
torsional P-Delta term.

Not affected (documented): builds with a ``PDelta``/``Corotational``
transformation (static cases with their own ``pdelta``/``geometric`` flag —
the per-case nonlinear flow WINS and is not double counted), hinged builds
(pushover, nonlinear hinged time history), staged construction (its stage
sub-models carry no options), linear buckling and load-dependent Ritz
vectors (self-contained numpy assemblies).  Shell elements contribute no
geometric stiffness under ``iterative_loads`` (frame/truss elements only);
``non_iterative_mass`` story springs cover walls through the diaphragm
masters.
"""

from __future__ import annotations

import contextlib
import io
import math
from typing import Dict, List, Optional, Tuple

import openseespy.opensees as ops

from skyframe.core.model import G_ACCEL
from skyframe.core.pdelta_options import effective_method, normalized

# uniaxial material tags for the geometric-stiffness springs (the engine's
# own tags start at 9 and grow by one per material)
_MAT_BASE = 10_000_000
_TOL = 1e-6


# --------------------------------------------------------------------------- #
# hooks called by OpenSeesEngine._build
# --------------------------------------------------------------------------- #
def prepare(eng, transf_name: str, hinge_case) -> bool:
    """Called at the START of ``_build``.  Returns True when this build must
    receive the model-wide geometric stiffness (then :func:`inject` runs at
    its end).  For ``iterative_loads`` the converged plan is computed here,
    BEFORE the outer build wipes the domain (the iteration's own builds run
    with the hook disabled)."""
    if effective_method(eng.model) == "none":
        return False
    if getattr(eng, "_pdelta_busy", False):
        return False
    if transf_name != "Linear" or hinge_case is not None:
        return False
    if (effective_method(eng.model) == "iterative_loads"
            and getattr(eng, "_pdelta_plan", None) is None):
        _iterate(eng)
    return True


def inject(eng, asm) -> None:
    """Called at the END of a qualifying ``_build`` (after the masses)."""
    plan = getattr(eng, "_pdelta_plan", None)
    if plan is None:                      # non_iterative_mass: from masses
        plan = _mass_plan(eng, asm)
        eng._pdelta_plan = plan
    _add_springs(plan["springs"])


def info(eng) -> dict:
    """Results summary (``AnalysisResults.pdelta``); {} when inactive."""
    method = effective_method(eng.model)
    if method == "none":
        return {}
    plan = getattr(eng, "_pdelta_plan", None)
    if plan is None:
        eng._build()                      # computes the plan
        plan = eng._pdelta_plan
    out = {k: v for k, v in plan.items() if k != "springs"}
    out["method"] = method
    out["n_springs"] = len(plan["springs"])
    own = [n for n, c in eng.model.cases.items()
           if c.effective_geometric != "linear"]
    if own:
        out["cases_own_geometric"] = own
    return out


# --------------------------------------------------------------------------- #
# spring construction
# --------------------------------------------------------------------------- #
def _add_springs(springs: List[dict]) -> None:
    if not springs:
        return
    from skyframe.engine.opensees_engine import _quiet_native_output
    etag = max(int(t) for t in ops.getEleTags()) if ops.getEleTags() else 0
    mtag = _MAT_BASE
    # zeroLength between non-coincident nodes prints a native length
    # warning on setDomain — intended here (a pure relative-translation
    # spring), so it is silenced (native fds AND the Python-level streams
    # openseespy's opserr may write through).
    with _quiet_native_output(), \
            contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        for sp in springs:
            mats = []
            for _ in sp["dirs"]:
                mtag += 1
                ops.uniaxialMaterial("Elastic", mtag, float(sp["k"]))
                mats.append(mtag)
            etag += 1
            ops.element("zeroLength", etag, sp["ni"], sp["nj"],
                        "-mat", *mats, "-dir", *sp["dirs"],
                        "-orient", *sp["x"], *sp["yp"],
                        "-doRayleigh", 1)


def _string(ni: int, nj: int, N: float) -> Optional[dict]:
    """String geometric stiffness ``N/L`` (tension +) on the two transverse
    relative translations of the element ni -> nj."""
    ci, cj = ops.nodeCoord(ni), ops.nodeCoord(nj)
    d = [b - a for a, b in zip(ci, cj)]
    L = math.sqrt(sum(v * v for v in d))
    if L <= _TOL:
        return None
    x = [v / L for v in d]
    yp = [1.0, 0.0, 0.0] if abs(x[0]) < 0.9 else [0.0, 1.0, 0.0]
    return {"ni": ni, "nj": nj, "k": N / L, "dirs": (2, 3),
            "x": x, "yp": yp}


def _story_spring(ni: int, nj: int, P: float, h: float) -> dict:
    """Story spring ``-P/h`` on global X and Y between two nodes."""
    return {"ni": ni, "nj": nj, "k": -P / h, "dirs": (1, 2),
            "x": [1.0, 0.0, 0.0], "yp": [0.0, 1.0, 0.0]}


# --------------------------------------------------------------------------- #
# iterative_loads
# --------------------------------------------------------------------------- #
def _element_axials(eng, asm) -> Dict[Tuple[str, int], float]:
    """Tension-positive axial force per frame element (segment): the mean
    of the two (fixed-end-force corrected) end axials, exactly the
    constant-N reduction the buckling base state uses."""
    out: Dict[Tuple[str, int], float] = {}
    for key, etag in asm.seg_ele.items():
        if key[0] in asm.truss_uids:
            out[key] = float(ops.eleResponse(etag, "basicForce")[0])
            continue
        f = [float(v) for v in ops.eleResponse(etag, "localForce")]
        fef = eng._seg_fef.get(key)
        if fef is not None:
            f = [v + float(c) for v, c in zip(f, fef)]
        out[key] = 0.5 * (-f[0] + f[6])
    return out


def _strings_from_axials(asm, axials: Dict[Tuple[str, int], float]
                         ) -> List[dict]:
    springs: List[dict] = []
    for key in sorted(axials):
        N = axials[key]
        if N == 0.0:
            continue
        ni, nj = (int(t) for t in ops.eleNodes(asm.seg_ele[key]))
        sp = _string(ni, nj, N)
        if sp is not None:
            springs.append(sp)
    return springs


def _gravity_solve(eng, asm, factors: Dict[str, float]) -> None:
    eng._seg_span_loads = {}
    eng._seg_fef = {}
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for pat, f in factors.items():
        eng._apply_pattern(asm, pat, float(f))
    if (eng._axial_only_present()
            or eng._nonlinear_static_links_present()
            or eng._compression_only_springs_present()):
        eng._setup_nonlinear_analysis(asm)
    else:
        eng._setup_analysis(asm)
    if ops.analyze(1) != 0:
        raise RuntimeError("P-Delta options (iterative_loads): the gravity "
                           f"combination {factors} failed to solve — the "
                           "load level may exceed the buckling capacity")


def _iterate(eng) -> None:
    opts = normalized(eng.model.pdelta_options)
    factors = {p: float(f) for p, f in opts["load_factors"].items()}
    max_it = int(opts["max_iterations"])
    tol = float(opts["tolerance"])
    springs: List[dict] = []
    prev: Optional[Dict[Tuple[str, int], float]] = None
    history: List[float] = []
    converged = False
    it = 0
    eng._pdelta_busy = True
    try:
        for it in range(1, max_it + 1):
            asm = eng._build()
            _add_springs(springs)
            _gravity_solve(eng, asm, factors)
            axials = _element_axials(eng, asm)
            springs = _strings_from_axials(asm, axials)
            if prev is not None:
                nmax = max((abs(v) for v in axials.values()), default=0.0)
                dmax = max((abs(axials[k] - prev.get(k, 0.0))
                            for k in axials), default=0.0)
                rel = dmax / nmax if nmax > 0.0 else 0.0
                history.append(rel)
                if rel <= tol:
                    converged = True
                    break
            prev = axials
    finally:
        eng._pdelta_busy = False
        eng._seg_span_loads = {}
        eng._seg_fef = {}
        eng._asm = None                    # the outer build re-sets it
        ops.wipe()
    eng._pdelta_plan = {
        "springs": springs,
        "iterations": it,
        "converged": converged if max_it > 1 else False,
        "relative_change": history,
        "load_factors": factors,
    }


# --------------------------------------------------------------------------- #
# non_iterative_mass
# --------------------------------------------------------------------------- #
def _mass_plan(eng, asm) -> dict:
    model = eng.model
    # translational mass per node (max of the UX / UY lumps)
    node_mass: Dict[int, float] = {}
    for (t, d), m in asm.mass_map.items():
        if d in (1, 2):
            node_mass[t] = max(node_mass.get(t, 0.0), float(m))
    zs = {t: float(ops.nodeCoord(t)[2]) for t in node_mass}
    fixed_xy = [t for t in asm.support_tags
                if asm.node_restraints.get(t, (0,) * 6)[0]
                and asm.node_restraints.get(t, (0,) * 6)[1]]
    springs: List[dict] = []
    story_P: Dict[str, float] = {}
    skipped: List[str] = []
    stories = list(model.stories)
    for i, s in enumerate(stories):
        z_top = float(s.elevation)
        h = float(s.height)
        z_bot = z_top - h
        if h <= _TOL:
            continue
        P = G_ACCEL * sum(m for t, m in node_mass.items()
                          if zs[t] > z_bot + _TOL)
        story_P[s.name] = P
        if P == 0.0:
            continue
        top = asm.masters.get(s.name)
        low: Optional[int] = None
        if top is not None:
            if i > 0:
                low = asm.masters.get(stories[i - 1].name)
            else:
                cands = [t for t in fixed_xy
                         if float(ops.nodeCoord(t)[2]) <= z_bot + _TOL]
                if cands:
                    mx, my = ops.nodeCoord(top)[:2]
                    low = min(cands, key=lambda t: (
                        (ops.nodeCoord(t)[0] - mx) ** 2
                        + (ops.nodeCoord(t)[1] - my) ** 2, t))
        if top is not None and low is not None:
            springs.append(_story_spring(low, top, P, h))
            continue
        col = _column_strings(eng, asm, z_bot, z_top, P)
        if col:
            springs.extend(col)
        else:
            skipped.append(s.name)
    out = {"springs": springs, "story_P": story_P}
    if skipped:
        out["skipped_stories"] = skipped
    return out


def _column_strings(eng, asm, z_bot: float, z_top: float,
                    P: float) -> List[dict]:
    """``P`` shared over the story's vertical frame columns by ``EA/L``."""
    model = eng.model
    cols = []
    for m in model.members:
        if m.uid in asm.truss_uids:
            continue
        dz = m.pj[2] - m.pi[2]
        L = m.length
        if L <= _TOL or abs(dz) < 0.99 * L:
            continue
        lo, hi = min(m.pi[2], m.pj[2]), max(m.pi[2], m.pj[2])
        if lo < z_bot - _TOL or hi > z_top + _TOL:
            continue
        sec = model.sections[m.section]
        E = model.materials[sec.material].E
        cols.append((m, E * sec.A * sec.mod_A / L))
    wsum = sum(w for _, w in cols)
    springs: List[dict] = []
    if wsum <= 0.0:
        return springs
    for m, w in cols:
        N = -P * w / wsum                       # compression (tension +)
        idx = sorted(k[1] for k in asm.seg_ele if k[0] == m.uid)
        for j in idx:
            ni, nj = (int(t) for t in ops.eleNodes(asm.seg_ele[(m.uid, j)]))
            sp = _string(ni, nj, N)
            if sp is not None:
                springs.append(sp)
    return springs
