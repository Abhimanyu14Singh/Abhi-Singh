"""Step-by-step (incremental) creep / shrinkage / relaxation integration for
user-defined staged construction — ``StagedCase.time_dependent.method ==
"step"``.  CONTRACT "Time-dependent materials, incremental creep and
tendon elements".

Method (Bazant's step-by-step superposition — the "general method" of
CEB / fib, exact integration of the linear Volterra creep law up to the
time discretisation):

* every concrete FRAME member has a compliance ``J(t, tau)`` from its
  material law (:mod:`skyframe.core.timedep`; the staged case's own
  ``creep_coeff`` / ``shrinkage`` / ``aging`` ACI 209 curves when the
  material has none) and a casting time ``t_c = T_add - age_days``;
* the time axis is the stage timeline: the OPERATIONS of stage s
  (add / remove / load, tendon stressing) act instantaneously at ``T_s``
  and are solved with the instantaneous modulus ``1/J(T_s, T_s)``; the
  stage duration is then divided into ``steps_per_stage`` creep steps
  with doubling length (``T_s + D (2^k - 1)/(2^n - 1)``) plus every
  ``output_times`` entry inside it; after the last stage the interval up
  to ``t_eval`` (default = end of the last stage) is integrated the same
  way;
* creep step ``i`` (t_{i-1} -> t_i, mid-time tau_i) is one linear solve of
  the active structure with the effective modulus ``E''_i = 1/J(t_i,
  tau_i)`` on creeping members and the imposed (stress-free) strain
  increments

      d_eps''_i(x) = sum_j d_sigma_j(x) [J(t_i, tau_j) - J(t_{i-1}, tau_j)]
                     - [eps_sh(t_i) - eps_sh(t_{i-1})]

  for the axial strain (``sigma = N/A``) and the same sum with
  ``M3/I33`` and ``M2/I22`` for the two curvatures, where ``d_sigma_j``
  are the section-force increments of every earlier solve (operations at
  ``tau_j = T_s``, creep steps at their mid-time).  The increments are
  piecewise linear along each FE segment (segment-end values) and enter
  through exact fixed-end vectors (axial ``EA eps``; curvature ``int B^T
  EI kappa dx``) — the member results are corrected like temperature
  loads, so the reported forces are the true stress resultants;
* tendon ELEMENTS (``model_as: "elements"``) are stressed by the first
  ``load`` operation of their pattern once all their hosts are active
  (scale = the operation scale), stay in every later solve as bonded
  stiffness (creep / shrinkage shortening of the concrete reduces their
  force) and lose ``Ap sigma_pi [rho(t_i - t_s) - rho(t_{i-1} - t_s)]``
  per step to relaxation when their material defines ``relaxation``
  (``sigma_pi`` = the element stress right after transfer);
* shells: ageing modulus only (no creep / shrinkage strains); the
  ``include_live`` increment is elastic (E28) and outside the history.

A load held constant on a determinate member therefore gives exactly
``eps(t) = sigma J(t, t0)`` (= ``sigma/E(t0) (1 + phi)`` for ACI 209 and
user curves) whatever the step count.

Results (``StagedCreepResults``): the standard user-staged shape plus
``shortening`` (per story: ``uz_elastic`` = operations only,
``uz_time_dependent`` = total, ``delta``) and ``time_history``::

    {"method": "step",
     "times": [t, ...],                     # every solve / grid time (days)
     "stage": [stage name at that time],
     "story_uz": {story: [mean column-top uz (m) at each time]},
     "tendon_force": {uid: [mean element force (kN) at each time]},
     "outputs": [{"t", "node_disp", "member_forces", "base",
                  "tendon_force": {uid: [P per element]}}]}   # output_times
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core import timedep as _tdm
from skyframe.core import tendons as _td
from skyframe.core.groups import (GROUP_KINDS, normalize_group,
                                  normalize_stages)
from skyframe.core.model import (BuildingModel, LoadCase, LoadPattern,
                                 NodalLoad)
from skyframe.engine.opensees_engine import (CaseResults, OpenSeesEngine,
                                             StagedResults, _TOL,
                                             _local_axes, _pkey)
from skyframe.engine import tendon_elements as _tel
from skyframe.engine.staged_user import (_active_keys, _apply_td_scales,
                                         _expand_load, _pattern_empty,
                                         _submodel)

_ZERO6 = np.zeros(6)
_FORCE_KEYS = ("FX", "FY", "FZ", "MX", "MY", "MZ")
_NONE_PAT = "__tdn_none__"


@dataclass
class StagedCreepResults(StagedResults):
    """:class:`StagedResults` + the step-method ``time_history``."""

    time_history: Optional[dict] = None

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.time_history is not None:
            d["time_history"] = self.time_history
        return d


def is_step(sc) -> bool:
    td = getattr(sc, "time_dependent", None)
    return isinstance(td, dict) and td.get("method") == "step"


# --------------------------------------------------------------------------- #
# imposed-strain loads (hook in OpenSeesEngine._apply_pattern)
# --------------------------------------------------------------------------- #
def _interp(xs, vs, x):
    return float(np.interp(x, xs, vs))


def apply_imposed(eng, asm, imposed: Dict[str, dict], scale: float) -> None:
    """Imposed (stress-free) axial strain / curvature increments per frame
    member: ``imposed[uid] = {"x": [...], "eps": [...], "kz": [...],
    "ky": [...]}`` sampled along the member (``kz = v''`` in the local x-y
    plane, ``ky = w''`` in the local x-z plane).  Each FE segment receives
    the exact equivalent nodal vector ``int B^T D eps0 dx`` of the
    piecewise-linear field and records ``f0 = -eq`` as its end-force
    correction (the temperature-load convention)."""
    model = eng.model
    for uid, d in imposed.items():
        member = eng._members_by_uid.get(uid)
        if member is None:
            continue
        sec = model.sections[member.section]
        mat = model.materials[sec.material]
        E = mat.E
        EA = E * sec.A * sec.mod_A
        EIz = E * sec.I33 * sec.mod_I33
        EIy = E * sec.I22 * sec.mod_I22
        bend = not member.release_tokens()
        xax, yax, zax, _, _ = _local_axes(member)
        R = np.array([xax, yax, zax], dtype=float)      # rows = local axes
        xs = d["x"]
        for seg in asm.mesh.segments[uid]:
            L = seg.length
            xa = seg.x0 + 1e-9 * L
            xb = seg.x0 + L - 1e-9 * L
            ea, eb = _interp(xs, d["eps"], xa), _interp(xs, d["eps"], xb)
            eq = np.zeros(12)
            em = 0.5 * (ea + eb) * scale
            eq[0], eq[6] = -EA * em, EA * em
            if bend:
                kzi = _interp(xs, d["kz"], xa) * scale
                kzj = _interp(xs, d["kz"], xb) * scale
                kyi = _interp(xs, d["ky"], xa) * scale
                kyj = _interp(xs, d["ky"], xb) * scale
                vz = EIz * (kzj - kzi) / L
                eq[1], eq[5], eq[7], eq[11] = vz, -EIz * kzi, -vz, EIz * kzj
                vy = EIy * (kyj - kyi) / L
                eq[2], eq[4], eq[8], eq[10] = vy, EIy * kyi, -vy, -EIy * kyj
            if not np.any(eq):
                continue
            for node, o in ((seg.ni, 0), (seg.nj, 6)):
                fg = R.T @ eq[o:o + 3]
                mg = R.T @ eq[o + 3:o + 6]
                em_ = eng._ecc_node_moment(member, seg, node, tuple(fg))
                ops.load(asm.pz_load_tag.get((uid, node), node + 1),
                         *[float(v) for v in fg],
                         *[float(a + b) for a, b in zip(mg, em_)])
            key = (uid, seg.index)
            eng._seg_fef[key] = eng._seg_fef.get(key, np.zeros(12)) - eq


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def _grid(t0: float, t1: float, n: int, extra: List[float]) -> List[float]:
    """Creep-step end times in (t0, t1]: doubling steps + extra times."""
    if t1 <= t0:
        return []
    D = t1 - t0
    pts = {t0 + D * (2.0 ** k - 1.0) / (2.0 ** n - 1.0)
           for k in range(1, n + 1)}
    pts |= {t for t in extra if t0 < t <= t1}
    pts.add(t1)
    return sorted(pts)


def run_step_staged(engine: OpenSeesEngine, name: str
                    ) -> StagedCreepResults:
    model = engine.model
    sc = model.staged_cases[name]
    model._validate_staged_case(sc)
    td = sc.time_dependent
    stages = normalize_stages(sc.stages)
    groups = {n: normalize_group(g) for n, g in model.groups.items()}
    lookup = {"members": {m.uid: m for m in model.members},
              "shells": {r.uid: r for r in model.shells},
              "links": {lk.uid: lk for lk in model.links}}
    warn: List[str] = []
    nsteps = int(td.get("steps_per_stage", _tdm.DEFAULT_STEPS_PER_STAGE))
    out_times = sorted({float(t) for t in td.get("output_times") or []})

    # ---- timeline
    t_start, t = [], 0.0
    for st in stages:
        t_start.append(t)
        t += st["duration_days"]
    t_total = t
    t_final = (t_total if td.get("t_eval") is None
               else float(td["t_eval"]))
    if t_final < t_total - 1e-12:
        raise ValueError(f"Staged case {name!r}: t_eval ({t_final:g} d) is "
                         f"before the end of the last stage ({t_total:g} d)")

    # ---- per-material laws
    fallback = _tdm.staged_fallback_props(td.get("creep_coeff", 2.0),
                                          td.get("shrinkage", 300e-6),
                                          td.get("aging", False))
    mfilter = td.get("materials")
    mtd = getattr(model, "material_time_dependent", None) or {}

    def law_of(mname: str) -> Optional[dict]:
        p = _tdm.props_of(model, mname)
        if p is not None:
            return p
        if mfilter is not None:
            return fallback if mname in mfilter else None
        return None if mtd else fallback

    mem_law: Dict[str, dict] = {}         # creeping members
    mem_props: Dict[str, Tuple[float, float, float, float, object]] = {}
    for m in model.members:
        sec = model.sections.get(m.section)
        if sec is None or getattr(m, "axial_limit", "both") != "both":
            continue
        mat = model.materials[sec.material]
        p = law_of(sec.material)
        if p is None:
            continue
        mem_law[m.uid] = p
        mem_props[m.uid] = (mat.E, sec.A * sec.mod_A,
                            sec.I33 * sec.mod_I33, sec.I22 * sec.mod_I22,
                            mat.fc)
    shell_law: Dict[str, Tuple[dict, float, object]] = {}
    for r in model.shells:
        ss = model.shell_sections.get(r.section)
        if ss is None:
            continue
        p = law_of(ss.material)
        if p is not None:
            mat = model.materials[ss.material]
            shell_law[r.uid] = (p, mat.E, mat.fc)
    creep_mats = set(model.materials)        # clones gate on the scale map

    def J(uid: str, t_age: float, tau_age: float) -> float:
        E28, _A, _I3, _I2, fc = mem_props[uid]
        return _tdm.law_compliance(mem_law[uid], E28, t_age, tau_age, fc)

    def eps_sh(uid: str, age: float) -> float:
        if age <= 0.0:
            return 0.0
        return _tdm.law_shrinkage(mem_law[uid], age, mem_props[uid][4])

    staged_pts = set()
    for st in stages:
        for op in st["operations"]:
            if op["op"] in ("add", "remove"):
                staged_pts.update(_pkey(tuple(p))
                                  for p in groups[op["group"]]["points"])

    # ---- full model (node tags + one-shot comparison)
    every = {k: set(lookup[k]) for k in GROUP_KINDS}
    full_keys = _active_keys(model, every, lookup)
    full = _submodel(model, every, full_keys, model.supports,
                     model.spring_supports, model.line_springs, "full")
    from skyframe.core import towers_ext as _twx
    full.stories = list(model.stories)
    _twx.carry(model, full)
    full.story_diaphragm = dict(model.story_diaphragm)
    oneshot_pat = LoadPattern("__oneshot__", "other")
    for st in stages:
        for op in st["operations"]:
            if op["op"] == "load":
                g = groups[op["group"]]
                _expand_load(model, model.patterns[op["pattern"]],
                             float(op["scale"]), g,
                             {k: set(g[k]) for k in GROUP_KINDS}, lookup,
                             oneshot_pat, [])
    full.patterns = dict(model.patterns)
    full.patterns["__oneshot__"] = oneshot_pat
    facs = {"__oneshot__": 1.0}
    for p, f in sc.include_live.items():
        facs[p] = facs.get(p, 0.0) + f
    full.cases = {"__oneshot__": LoadCase("__oneshot__", facs)}
    eng_full = OpenSeesEngine(full)
    oneshot = eng_full.run_static("__oneshot__")
    full_asm = eng_full._asm
    full_zmin = min((c[2] for c in full_asm.struct_coords.values()),
                    default=0.0)

    # ---- state
    acc = {"disp": {}, "inst": {}, "master": {}, "reac": {}, "mf": {},
           "st": {}, "story": {}, "ele": {}}
    active = {k: set() for k in GROUP_KINDS}
    active_pts: set = set()
    t_cast: Dict[Tuple[str, str], float] = {}
    history: Dict[str, List[tuple]] = {}      # uid -> [(tau, xs, sN, s3, s2)]
    tendons = [_td.normalize_tendon(x) for x in
               (getattr(model, "tendons", None) or [])]
    stressed: Dict[str, dict] = {}            # uid -> state

    def supports_for(keys: set, label: str):
        def ok(p):
            k = _pkey(tuple(p))
            return k in keys and (k not in staged_pts or k in active_pts)
        sups = [s for s in model.supports if ok(s.point)]
        springs = [s for s in model.spring_supports if ok(s.point)]
        lsp = [ls for ls in model.line_springs if ok(ls.p1) and ok(ls.p2)]
        if model.supports and not sups and not springs:
            raise ValueError(f"Staged case {name!r}: {label}: no active "
                             "support")
        if not model.supports:
            zmin = min(k[2] for k in keys)
            if zmin > full_zmin + _TOL and not springs:
                raise ValueError(
                    f"Staged case {name!r}: {label}: the active structure "
                    f"does not reach the base level z={full_zmin:g}")
        return sups, springs, lsp

    def seg_profile(eng, asm, uid: str):
        """(xs, N, Mz, My) piecewise-linear internal forces from the
        corrected segment end forces of the live domain."""
        xs, N, Mz, My = [], [], [], []
        for seg in asm.mesh.segments[uid]:
            etag = asm.seg_ele.get((uid, seg.index))
            if etag is None:
                return None
            f = np.asarray(ops.eleResponse(etag, "localForce"), dtype=float)
            fef = eng._seg_fef.get((uid, seg.index))
            if fef is not None:
                f = f + fef
            xs += [seg.x0, seg.x0 + seg.length]
            N += [-f[0], f[6]]
            Mz += [-f[5], f[11]]
            My += [-f[4], f[10]]
        return xs, N, Mz, My

    def solve(sub: BuildingModel, pat: LoadPattern, tau: Optional[float],
              inst: bool, tendon_apply: Dict[str, float]):
        """One linear increment; accumulates, records stress history at
        ``tau`` and updates the stressed-tendon forces."""
        sub.patterns = dict(model.patterns)
        sub.patterns[pat.name] = pat
        sub.patterns[_NONE_PAT] = LoadPattern(_NONE_PAT, "other")
        sub.cases = {"__ustage__": LoadCase("__ustage__", {pat.name: 1.0})}
        eng = OpenSeesEngine(sub)
        res = eng.run_static("__ustage__")
        from skyframe.engine import convergence as _cvg
        _cvg.adopt_child(engine, name, eng)
        asm = eng._asm
        # creep history (live domain: segment end forces)
        if tau is not None:
            for uid in active["members"]:
                if uid not in mem_law or uid not in eng._members_by_uid:
                    continue
                prof = seg_profile(eng, asm, uid)
                if prof is None:
                    continue
                xs, N, Mz, My = prof
                _E, A, I3, I2, _fc = mem_props[uid]
                history.setdefault(uid, []).append(
                    (tau, np.asarray(xs), np.asarray(N) / A,
                     np.asarray(Mz) / I3, np.asarray(My) / I2))
        for tg, c in asm.struct_coords.items():
            k = _pkey(c)
            v = np.asarray(res.node_disp[tg])
            acc["disp"][k] = acc["disp"].get(k, _ZERO6) + v
            if inst:
                acc["inst"][k] = acc["inst"].get(k, _ZERO6) + v
        for s_name, mt in asm.masters.items():
            acc["master"][s_name] = (acc["master"].get(s_name, _ZERO6)
                                     + np.asarray(res.node_disp[mt]))
        for tg in asm.support_tags:
            k = _pkey(asm.node_coords[tg])
            acc["reac"][k] = acc["reac"].get(k, _ZERO6) + np.asarray(
                res.reactions[tg])
        for uid, f in res.member_forces.items():
            acc["mf"][uid] = acc["mf"].get(uid, np.zeros(12)) + np.asarray(f)
        for uid, st in res.member_stations.items():
            e = acc["st"].setdefault(uid, {"x": list(st["x"])})
            for k3 in ("N", "V2", "V3", "T", "M2", "M3"):
                if len(e.get(k3, ())) not in (0, len(st[k3])):
                    continue           # station layout changed: keep first
                e[k3] = e.get(k3, np.zeros(len(st[k3]))) + np.asarray(st[k3])
        for s_name, vals in res.story.items():
            sa = acc["story"].setdefault(s_name, {k4: 0.0 for k4 in vals})
            for k4, v in vals.items():
                sa[k4] += v
        mlook = lookup["members"]
        for uid, f in res.member_forces.items():
            m = mlook.get(uid)
            if m is None:
                continue
            xax, yax, zax, _, _ = _local_axes(m)
            f = np.asarray(f)
            ent = acc["ele"].setdefault(("members", uid), {})
            for p, o in ((m.pi, 0), (m.pj, 6)):
                F = f[o] * np.asarray(xax) + f[o + 1] * np.asarray(yax) \
                    + f[o + 2] * np.asarray(zax)
                M = f[o + 3] * np.asarray(xax) + f[o + 4] * np.asarray(yax) \
                    + f[o + 5] * np.asarray(zax)
                k = _pkey(tuple(p))
                ent[k] = ent.get(k, _ZERO6) + np.concatenate([F, M])

        def ele_nodal(kind, uid, etag):
            nodes = ops.eleNodes(etag)
            fv = ops.eleForce(etag)
            ndf = len(fv) // len(nodes)
            ent = acc["ele"].setdefault((kind, uid), {})
            for i, nt in enumerate(nodes):
                c = asm.node_coords.get(nt)
                if c is None:
                    continue
                v = np.zeros(6)
                v[:min(ndf, 6)] = fv[i * ndf:i * ndf + min(ndf, 6)]
                k = _pkey(c)
                ent[k] = ent.get(k, _ZERO6) + v

        for uid, etag in asm.link_ele.items():
            ele_nodal("links", uid, etag)
        for q, etag in zip(asm.shell_quads, asm.quad_ele):
            ele_nodal("shells", q["region"], etag)
        # tendon element forces
        for uid, stt in stressed.items():
            if not stt["element"]:
                continue
            dl = _tel.elongations(eng, uid, res.node_disp)
            if not dl:
                continue
            info = _tel._info(eng)[uid]["elements"]
            if stt["P"] is None:
                stt["P"] = np.zeros(len(info))
                stt["k"] = np.array([e["k"] for e in info])
            applied = tendon_apply.get(uid)
            if isinstance(applied, (list, np.ndarray)):
                inc = np.asarray(applied, dtype=float)
            elif applied:          # stressing solve: this engine's P (x op
                inc = np.array([e["P"] for e in info])      # scale)
            else:
                inc = np.zeros(len(info))
            stt["P"] = stt["P"] + inc + stt["k"] * np.asarray(dl)
        return res, asm

    def full_state(label: str) -> CaseResults:
        node_disp = {tg: [float(v) for v in acc["disp"].get(_pkey(c),
                                                           _ZERO6)]
                     for tg, c in full_asm.struct_coords.items()}
        for s_name, mt in full_asm.masters.items():
            node_disp[mt] = [float(v) for v in
                             acc["master"].get(s_name, _ZERO6)]
        reactions = {tg: [float(v) for v in
                          acc["reac"].get(_pkey(full_asm.node_coords[tg]),
                                          _ZERO6)]
                     for tg in full_asm.support_tags}
        base = {k: 0.0 for k in _FORCE_KEYS}
        for k, r in acc["reac"].items():
            x, y, z = k
            fx, fy, fz, mx, my, mz = (float(v) for v in r)
            base["FX"] += fx
            base["FY"] += fy
            base["FZ"] += fz
            base["MX"] += mx + y * fz - z * fy
            base["MY"] += my + z * fx - x * fz
            base["MZ"] += mz + x * fy - y * fx
        member_forces, stations = {}, {}
        for m in model.members:
            member_forces[m.uid] = [float(v) for v in
                                    acc["mf"].get(m.uid, np.zeros(12))]
            st = acc["st"].get(m.uid)
            if st is None or "N" not in st:
                ref = oneshot.member_stations.get(m.uid)
                if ref is None:
                    continue
                st = {"x": list(ref["x"]),
                      **{k: np.zeros(len(ref["x"]))
                         for k in ("N", "V2", "V3", "T", "M2", "M3")}}
            stations[m.uid] = {k: (list(v) if k == "x"
                                   else [float(x) for x in v])
                               for k, v in st.items()}
        empty = {"ux": 0.0, "uy": 0.0, "drift_x": 0.0, "drift_y": 0.0,
                 "shear_x": 0.0, "shear_y": 0.0}
        story = {s.name: dict(acc["story"].get(s.name, empty))
                 for s in model.stories}
        return CaseResults(label, node_disp, reactions, base, member_forces,
                           story, stations)

    # ---- column tops per story (shortening / time history)
    sidx = {s.name: i for i, s in enumerate(model.stories)}
    story_tops: Dict[str, List[tuple]] = {}
    for m in model.members:
        if m.kind != "column":
            continue
        j = engine._stage_of_member(m, sidx)
        if 0 <= j < len(model.stories):
            top = max((m.pi, m.pj), key=lambda p: p[2])
            story_tops.setdefault(model.stories[j].name, []).append(
                (m.uid, _pkey(top)))

    th = {"method": "step", "times": [], "stage": [],
          "story_uz": {s: [] for s in story_tops},
          "tendon_force": {}, "outputs": []}

    def record(tnow: float, stage_name: str) -> None:
        th["times"].append(float(tnow))
        th["stage"].append(stage_name)
        for s, lst in story_tops.items():
            vals = [acc["disp"].get(k, _ZERO6)[2] for uid, k in lst
                    if uid in active["members"]]
            th["story_uz"][s].append(float(np.mean(vals)) if vals else None)
        for uid in [t_["uid"] for t_ in tendons]:
            stt = stressed.get(uid)
            P = (float(np.mean(stt["P"])) if stt and stt.get("P") is not None
                 else None)
            th["tendon_force"].setdefault(uid, []).append(P)
        if any(abs(tnow - to) < 1e-9 for to in out_times) and not any(
                abs(o["t"] - tnow) < 1e-9 for o in th["outputs"]):
            snap = full_state(f"{name} @ {tnow:g} d")
            th["outputs"].append({
                "t": float(tnow),
                "node_disp": {str(tg): v for tg, v in snap.node_disp.items()},
                "member_forces": dict(snap.member_forces),
                "base": dict(snap.base),
                "tendon_force": {u: [float(v) for v in s_["P"]]
                                 for u, s_ in stressed.items()
                                 if s_.get("P") is not None}})

    def build_sub(keys: set, label: str, scales: Dict[tuple, float],
                  tendon_pats: Dict[str, Tuple[str, float]],
                  override: Optional[dict] = None) -> BuildingModel:
        sups, springs, lsp = supports_for(keys, label)
        sub = _submodel(model, active, keys, sups, springs, lsp, label)
        tl = []
        for tdd in tendons:
            uid = tdd["uid"]
            if uid in tendon_pats:
                pat_name, fac = tendon_pats[uid]
                c = dict(tdd)
                c["pattern"] = pat_name
                c["jacking_stress"] = tdd["jacking_stress"] * fac
                tl.append(c)
        sub.tendons = tl
        if override:
            sub._tde_override = override
        if scales:
            _apply_td_scales(sub, scales, creep_mats)
        return sub

    def tendon_pats_live(new: Dict[str, float], pat_name: str
                         ) -> Dict[str, Tuple[str, float]]:
        """Tendons present in a solve: element tendons already stressed
        (stiffness only) + the ones stressed now (loads at op scale)."""
        out = {}
        for uid, stt in stressed.items():
            if uid in new:
                out[uid] = (pat_name, new[uid])
            elif stt["element"]:
                out[uid] = (_NONE_PAT, 1.0)
        return out

    def inst_scales(tnow: float) -> Dict[tuple, float]:
        sc_ = {}
        for uid in active["members"]:
            if uid in mem_law:
                age = tnow - t_cast[("members", uid)]
                if age <= 0.0:
                    raise ValueError(f"Staged case {name!r}: member {uid!r} "
                                     f"is loaded at age {age:g} d")
                sc_[("members", uid)] = (1.0 / J(uid, age, age)
                                         / mem_props[uid][0])
        for uid in active["shells"]:
            if uid in shell_law:
                p, E28, fc = shell_law[uid]
                age = max(tnow - t_cast[("shells", uid)], 1e-6)
                sc_[("shells", uid)] = _tdm.law_modulus(p, E28, age, fc) / E28
        return sc_

    def creep_step(ta: float, tb: float, label: str) -> None:
        keys = _active_keys(model, active, lookup)
        if not keys:
            return
        tm = 0.5 * (ta + tb)
        imposed: Dict[str, dict] = {}
        scales: Dict[tuple, float] = {}
        for uid in active["members"]:
            if uid not in mem_law:
                continue
            tc = t_cast[("members", uid)]
            hist = history.get(uid, [])
            E28 = mem_props[uid][0]
            scales[("members", uid)] = 1.0 / J(uid, tb - tc, tm - tc) / E28
            if not hist:
                xs = np.array([0.0, lookup["members"][uid].length])
                eps = np.zeros(2)
                kz = np.zeros(2)
                ky = np.zeros(2)
            else:
                xs = hist[-1][1]
                eps = np.zeros(len(xs))
                kz = np.zeros(len(xs))
                ky = np.zeros(len(xs))
                for tau, hx, sN, s3, s2 in hist:
                    dJ = (J(uid, tb - tc, tau - tc)
                          - J(uid, ta - tc, tau - tc))
                    if dJ == 0.0:
                        continue
                    same = len(hx) == len(xs) and np.allclose(hx, xs)
                    a = sN if same else np.interp(xs, hx, sN)
                    b = s3 if same else np.interp(xs, hx, s3)
                    c = s2 if same else np.interp(xs, hx, s2)
                    eps += a * dJ
                    kz += b * dJ
                    ky -= c * dJ
            dsh = eps_sh(uid, tb - tc) - eps_sh(uid, ta - tc)
            eps = eps - dsh
            if np.any(eps) or np.any(kz) or np.any(ky):
                imposed[uid] = {"x": [float(v) for v in xs],
                                "eps": [float(v) for v in eps],
                                "kz": [float(v) for v in kz],
                                "ky": [float(v) for v in ky]}
        for uid in active["shells"]:
            if uid in shell_law:
                p, E28, fc = shell_law[uid]
                age = max(tm - t_cast[("shells", uid)], 1e-6)
                scales[("shells", uid)] = _tdm.law_modulus(p, E28, age,
                                                           fc) / E28
        # tendon relaxation (element tendons with a relaxation law)
        override, apply_vec = {}, {}
        for uid, stt in stressed.items():
            r = stt.get("relax")
            if not stt["element"] or r is None or stt.get("P") is None:
                continue
            ts = stt["t_str"]
            sig = stt["sigma_pi"]
            rb = np.array([_tdm.relaxation_ratio(r, s, tb - ts, stt["fpk"],
                                                 stt["fpy"]) for s in sig])
            ra = np.array([_tdm.relaxation_ratio(r, s, ta - ts, stt["fpk"],
                                                 stt["fpy"]) for s in sig])
            dP = -stt["Ap"] * sig * (rb - ra)
            if np.any(dP):
                override[uid] = [float(v) for v in dP]
                apply_vec[uid] = dP
        if not imposed and not override:
            return
        pat = LoadPattern("__creep__", "other")
        pat.imposed_strains = imposed
        tpats = {uid: ("__creep__" if uid in override else _NONE_PAT, 1.0)
                 for uid, stt in stressed.items() if stt["element"]}
        sub = build_sub(keys, label, scales, tpats, override)
        solve(sub, pat, tm, False, apply_vec)

    stage_out: List[dict] = []
    tnow = 0.0
    for si, st in enumerate(stages):
        T_s = t_start[si]
        tnow = T_s
        release: Dict[tuple, np.ndarray] = {}
        loads = []
        for op in st["operations"]:
            g = groups[op["group"]]
            if op["op"] == "add":
                for kind in GROUP_KINDS:
                    for uid in g[kind]:
                        active[kind].add(uid)
                        t_cast[(kind, uid)] = T_s - float(op["age_days"])
                active_pts.update(_pkey(tuple(p)) for p in g["points"])
            elif op["op"] == "remove":
                for kind in GROUP_KINDS:
                    for uid in g[kind]:
                        active[kind].discard(uid)
                        for k, f in acc["ele"].pop((kind, uid), {}).items():
                            release[k] = release.get(k, _ZERO6) + f
                        if kind == "members":
                            acc["mf"].pop(uid, None)
                            acc["st"].pop(uid, None)
                            history.pop(uid, None)
                active_pts.difference_update(_pkey(tuple(p))
                                             for p in g["points"])
            else:
                loads.append(op)
        keys = _active_keys(model, active, lookup)
        pat = LoadPattern("__ustage_pat__", "other")
        new_tendons: Dict[str, float] = {}
        for op in loads:
            g = groups[op["group"]]
            objs = {k: set(g[k]) & active[k] for k in GROUP_KINDS}
            _expand_load(model, model.patterns[op["pattern"]],
                         float(op["scale"]), g, objs, lookup, pat, warn)
            for tdd in tendons:
                uid = tdd["uid"]
                if uid in stressed or tdd["pattern"] != op["pattern"]:
                    continue
                kind = ("members" if _td.host_kind(model, tdd) == "frame"
                        else "shells")
                if set(tdd["host"]) <= active[kind]:
                    mat = _td.tendon_material(model, tdd)
                    tmat = model.materials.get(tdd["material"])
                    relax = None
                    if tmat is not None:
                        pr = _tdm.props_of(model, tmat.name)
                        relax = pr.get("relaxation") if pr else None
                    stressed[uid] = {
                        "element": tdd.get("model_as") == "elements",
                        "t_str": T_s, "P": None, "relax": relax,
                        "Ap": tdd["area"], "fpk": getattr(mat, "fu", None),
                        "fpy": getattr(mat, "fy", None)}
                    new_tendons[uid] = float(op["scale"])
        label = f"stage {st['name']}"
        dropped = [nl for nl in pat.nodal_loads
                   if _pkey(tuple(nl.point)) not in keys]
        if dropped:
            warn.append(f"{label}: {len(dropped)} joint load(s) at points "
                        "with no active object were skipped")
            pat.nodal_loads = [nl for nl in pat.nodal_loads
                               if _pkey(tuple(nl.point)) in keys]
        if keys:
            for k, f in release.items():
                if k in keys and np.any(f):
                    pat.nodal_loads.append(NodalLoad(k, *[float(v)
                                                          for v in f]))
            if not _pattern_empty(pat) or release or new_tendons:
                sub = build_sub(keys, label, inst_scales(T_s),
                                tendon_pats_live(new_tendons, pat.name))
                _, asm = solve(sub, pat, T_s, True,
                               {u: f for u, f in new_tendons.items()})
                live = {_pkey(asm.node_coords[tg]) for tg in asm.support_tags}
                acc["reac"] = {k: v for k, v in acc["reac"].items()
                               if k in live}
            for uid in new_tendons:
                stt = stressed[uid]
                if stt["element"] and stt.get("P") is not None:
                    stt["sigma_pi"] = np.asarray(stt["P"]) / stt["Ap"]
        elif not _pattern_empty(pat):
            raise ValueError(f"Staged case {name!r}: {label}: loads applied "
                             "with no active objects")
        else:
            acc["reac"] = {}
        record(T_s, st["name"])
        prev = T_s
        for tb in _grid(T_s, T_s + st["duration_days"], nsteps, out_times):
            creep_step(prev, tb, f"{label} creep {tb:g} d")
            prev = tb
            record(tb, st["name"])
        tnow = prev
        snap = full_state(f"{name} @ {st['name']}")
        stage_out.append({
            "name": st["name"], "duration_days": st["duration_days"],
            "t_start": T_s, "t_end": T_s + st["duration_days"],
            "active": {k: sorted(active[k]) for k in GROUP_KINDS},
            "node_disp": {str(tg): v for tg, v in snap.node_disp.items()},
            "reactions": {str(tg): v for tg, v in snap.reactions.items()},
            "base": dict(snap.base),
            "member_forces": {u: v for u, v in snap.member_forces.items()},
        })
    # long-term interval after the last stage
    prev = t_total
    for tb in _grid(t_total, t_final, nsteps, out_times):
        creep_step(prev, tb, f"long term {tb:g} d")
        prev = tb
        record(tb, "(long term)")

    final_keys = _active_keys(model, active, lookup)
    if final_keys and sc.include_live:
        sub = build_sub(final_keys, "include_live", {},
                        tendon_pats_live({}, _NONE_PAT))
        live = LoadPattern("__ustage_none__", "other")
        sub.patterns = dict(model.patterns)
        sub.patterns[live.name] = live
        sub.patterns[_NONE_PAT] = LoadPattern(_NONE_PAT, "other")
        sub.cases = {"__ustage__": LoadCase("__ustage__",
                                            {live.name: 1.0,
                                             **dict(sc.include_live)})}
        eng = OpenSeesEngine(sub)
        res = eng.run_static("__ustage__")
        asm = eng._asm
        for tg, c in asm.struct_coords.items():
            k = _pkey(c)
            v = np.asarray(res.node_disp[tg])
            acc["disp"][k] = acc["disp"].get(k, _ZERO6) + v
            acc["inst"][k] = acc["inst"].get(k, _ZERO6) + v
        for s_name, mt in asm.masters.items():
            acc["master"][s_name] = (acc["master"].get(s_name, _ZERO6)
                                     + np.asarray(res.node_disp[mt]))
        for tg in asm.support_tags:
            k = _pkey(asm.node_coords[tg])
            acc["reac"][k] = acc["reac"].get(k, _ZERO6) + np.asarray(
                res.reactions[tg])
        for uid, f in res.member_forces.items():
            acc["mf"][uid] = acc["mf"].get(uid, np.zeros(12)) + np.asarray(f)
        for uid, st_ in res.member_stations.items():
            e = acc["st"].setdefault(uid, {"x": list(st_["x"])})
            for k3 in ("N", "V2", "V3", "T", "M2", "M3"):
                if len(e.get(k3, ())) not in (0, len(st_[k3])):
                    continue
                e[k3] = e.get(k3, np.zeros(len(st_[k3]))) + np.asarray(
                    st_[k3])

    case = full_state(name)
    if warn:
        case.warning = "; ".join(sorted(set(warn)))
        for w in sorted(set(warn)):
            warnings.warn(f"Staged case {name!r}: {w}", UserWarning)
    cols = [m.uid for m in model.members if m.kind == "column"]
    pct = 0.0
    if cols:
        ref = max(abs(oneshot.member_forces[u][0]) for u in cols)
        if ref > 0.0:
            pct = 100.0 * max(abs(case.member_forces[u][0]
                                  - oneshot.member_forces[u][0])
                              for u in cols) / ref
    shortening = {}
    for s, lst in story_tops.items():
        ks = [k for _u, k in lst]
        uz_t = float(np.mean([acc["disp"].get(k, _ZERO6)[2] for k in ks]))
        uz_e = float(np.mean([acc["inst"].get(k, _ZERO6)[2] for k in ks]))
        shortening[s] = {"uz_elastic": uz_e, "uz_time_dependent": uz_t,
                         "delta": uz_t - uz_e}
    th["tendons"] = {u: {"P": [float(v) for v in s_["P"]],
                         "sigma_pi": [float(v) for v in
                                      s_.get("sigma_pi", [])],
                         "t_stress": s_["t_str"]}
                     for u, s_ in stressed.items()
                     if s_.get("P") is not None}
    return StagedCreepResults(name, case, oneshot, pct,
                              shortening=shortening, stages=stage_out,
                              time_history=th)
