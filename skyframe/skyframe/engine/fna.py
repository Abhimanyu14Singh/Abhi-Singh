"""Extended Fast Nonlinear Analysis driver (FNA breadth).

CONTRACT "FNA breadth: FP / TFP / multilinear / Wen links, nonlinear
dampers, gravity initial conditions, Ritz basis".

``OpenSeesEngine.run_fna`` routes a case here (:func:`wants`) when it
uses anything the v0.24 FNA did not support:

* a link of type ``fp_isolator`` / ``triple_fp`` / ``multilinear`` /
  ``multilinear_kinematic`` / ``friction_spring`` /
  ``rubber_isolator_bouc_wen`` / ``plastic_wen`` (when that type exists)
  or a ``damper`` with ``alpha != 1``;
* gravity initial conditions — ``TimeHistoryCase.initial_case`` (a
  nonlinear static predecessor; ETABS "continue from state at end of
  nonlinear case") or the legacy ``gravity`` pattern combination (run as
  an in-memory 10-step load-control nonlinear static case);
* a named modal case (``TimeHistoryCase.modal_case``: mass source, mode
  count, eigen OR Ritz basis — Ritz ``links`` starts resolve to the
  device DOFs).

Every other case keeps the legacy v0.24 code path (bit-identical).  The
driver follows the legacy run step by step — linearized model (devices
removed or replaced by their linear-elastic part), basis, modified
Gram-Schmidt M-orthonormalization, Rayleigh / modal damping, device
deformation rows, :func:`skyframe.core.fna_devices.fna_integrate`, the
same ``THResults`` assembly — so a case routed here with only legacy
devices reproduces the legacy numbers to round-off.

Gravity initial state: the predecessor runs on a copy of the FULL
(device) model (:func:`skyframe.engine.nonlinear_static.run_case`); its
end-state node displacements give each device row's deformation ``d0``
(bearing axial loads ``N = -kv * d0_z``, compression positive), every
device is driven monotonically to ``d0`` (:func:`fna_devices.preload`)
and the modal response is the increment PAST the gravity state (the
direct-integration ``gravity`` convention).  Bearings without a gravity
state take ``N`` from ``P0`` (fp_isolator) / ``W`` (triple_fp); a
friction-pendulum bearing with neither raises ``ValueError``.

Linear part kept in the basis per device (pseudo-force = the rest):
damper (any alpha) / gap / hook — none; isolator ``k1``; fp_isolator
``k_init + N/R`` (stick); friction_spring ``k_init``; triple_fp the
series stick stiffness; multilinear the first backbone slope; Wen the
initial tangent; vertical ``kv`` of every bearing (elastic, no
pseudo-force).
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from skyframe.core import fna as _fna
from skyframe.core import fna_devices as _fd

_LEGACY = ("elastic", "gap", "hook", "isolator")
_BEARINGS = ("isolator", "fp_isolator", "triple_fp", "multilinear",
             "multilinear_kinematic", "friction_spring",
             "rubber_isolator_bouc_wen")
_GRAVITY_CASE = "__FNA_GRAVITY__"
_TOL = 1e-6


def _alpha(prm) -> float:
    from skyframe.core.model import DAMPER_DEFAULT_ALPHA
    return float(prm.get("alpha", DAMPER_DEFAULT_ALPHA))


def wants(eng, th) -> bool:
    """True when the case needs this extended driver."""
    if getattr(th, "modal_case", "") or getattr(th, "initial_case", "") \
            or th.gravity:
        return True
    from skyframe.core.model import LinkMember
    for lk in eng.model.links:
        lt = getattr(lk, "link_type", "elastic")
        if lt in _LEGACY:
            continue
        if lt == "damper" and abs(_alpha(LinkMember.coerce_params(
                lk.params)) - 1.0) <= 1e-12:
            continue
        return True
    return False


# --------------------------------------------------------------------------- #
# gravity initial state
# --------------------------------------------------------------------------- #
def _initial_state(eng, th):
    """``disp_at(point) -> 6-list | None`` at the end of the predecessor,
    or None without initial conditions."""
    from skyframe.core.nonlinear_static import NonlinearStaticCase
    from skyframe.engine import nonlinear_static as _nls
    from skyframe.engine.opensees_engine import OpenSeesEngine
    from skyframe.engine import wavec_th as _wth     # Wave C: staged start
    _st = _wth.fna_staged_disp_at(eng, th)
    if _st is not None:
        return _st
    name = getattr(th, "initial_case", "") or ""
    if not name and not th.gravity:
        return None
    m = copy.deepcopy(eng.source_model)
    for c in m.th_cases.values():
        c.modal_case = ""
        c.initial_case = ""
    if not name:
        name = _GRAVITY_CASE
        m.nonlinear_static_cases = dict(m.nonlinear_static_cases or {})
        m.nonlinear_static_cases[name] = NonlinearStaticCase(
            name, loads=[{"pattern": p, "scale": float(f)}
                         for p, f in th.gravity.items()], steps=10)
    eg = OpenSeesEngine(m)
    res = _nls.run_case(eg, name)
    if not res.converged:
        raise RuntimeError(f"FNA case {th.name!r}: the initial-condition "
                           f"case {name!r} did not converge")
    node_disp = res.case.node_disp
    asm = eg._asm if eg._asm is not None else eg._build()  # stable tags

    def disp_at(point) -> Optional[List[float]]:
        try:
            t = eg._find_node(asm, point)
        except ValueError:
            return None
        return list(node_disp.get(t, [0.0] * 6))
    return disp_at


# --------------------------------------------------------------------------- #
# device records
# --------------------------------------------------------------------------- #
def _bearing_axis(lk) -> Tuple[tuple, tuple]:
    """(lower, upper) end points of a vertical bearing."""
    pi, pj = tuple(lk.pi), tuple(lk.pj)
    return (pj, pi) if pi[2] > pj[2] else (pi, pj)


def _wen_params(lt: str, prm: dict):
    """(k_lin, q, uy, beta, gamma, eta, a2k, mu, k_init)."""
    from skyframe.core.springprops import BW_DEFAULTS
    if lt == "rubber_isolator_bouc_wen":
        g = {k: float(prm.get(k, v)) for k, v in BW_DEFAULTS.items()}
        k0 = float(prm["k_init"])
        qd = float(prm["qd"])
        a1 = g["alpha1"]
        uy = qd / ((1.0 - a1) * k0) if a1 < 1.0 else qd / k0
        return (a1 * k0, qd, uy, g["beta"], g["gamma"], g["eta"],
                g["alpha2"] * k0, g["mu"], k0)
    # plastic_wen (ETABS Plastic (Wen)); tolerant param names
    def pick(*keys, default=None):
        for k in keys:
            if k in prm:
                return float(prm[k])
        if default is None:
            raise NotImplementedError(
                f"plastic_wen link: missing one of {keys} — use direct "
                "integration (run_time_history)")
        return default
    k = pick("k", "k_init", "k1")
    fy = pick("Fy", "fy", "yield")
    r = pick("ratio", "r", "post_yield_ratio", "alpha", default=0.0)
    ex = pick("exp", "exponent", "n", "yield_exponent", default=2.0)
    return (r * k, (1.0 - r) * fy, fy / k, 0.5, 0.5, ex, 0.0, 2.0, k)


def _devices(eng, th, model, disp_at):
    """Linearized model + device list.

    Returns ``(lin, recs)``; each rec is ``(device, rowspec, k_lin_rows,
    point_pair, start_kind, link_uid)`` with ``rowspec`` a list of
    ``("axial", e) | ("dof", d0)`` row definitions on ``(pi, pj)``."""
    from skyframe.core.model import (DAMPER_DEFAULT_K, FP_DEFAULT_KINIT,
                                     ISOLATOR_DEFAULT_KV, TFP_DEFAULT_UY,
                                     LinkMember)
    lin = copy.deepcopy(model)
    lin.links = []
    recs = []
    for lk in model.links:
        lt = getattr(lk, "link_type", "elastic")
        prm = LinkMember.coerce_params(getattr(lk, "params", {}) or {})
        if lt == "elastic":
            lin.links.append(copy.deepcopy(lk))
            continue
        from skyframe.engine import fna_links_ext as _flx   # Wave C links
        if _flx.add(th, lk, lt, prm, disp_at, lin, recs):
            continue
        pi, pj = tuple(lk.pi), tuple(lk.pj)
        if lt in ("damper", "gap", "hook") or (
                lt == "plastic_wen" and abs(pi[0] - pj[0])
                + abs(pi[1] - pj[1]) > _TOL):
            dx = np.array(pj, dtype=float) - np.array(pi, dtype=float)
            e = dx / float(np.linalg.norm(dx))
            if lt == "damper":
                k = float(prm.get("k", DAMPER_DEFAULT_K))
                if abs(_alpha(prm) - 1.0) <= 1e-12:
                    dev = _fd._Scalar(_fna.MaxwellDamper(k, prm["cd"]))
                else:
                    dev = _fd.NonlinearDamper(k, prm["cd"], _alpha(prm))
            elif lt == "gap":
                dev = _fd._Scalar(_fna.GapComponent(prm["k"], prm["gap"]))
            elif lt == "hook":
                dev = _fd._Scalar(_fna.HookComponent(prm["k"],
                                                     prm["slack"]))
            else:
                kl, q, uy, be, ga, eta, a2k, mu, _k0 = _wen_params(lt, prm)
                dev = _fd.WenComponent(kl, q, uy, rows=1, beta=be, gamma=ga,
                                       eta=eta, a2k=a2k, mu=mu)
            recs.append((dev, [("axial", e)], [0.0], (pi, pj), "axial", lk.uid))
            continue
        if lt not in _BEARINGS and lt != "plastic_wen":
            raise NotImplementedError(
                f"FNA case {th.name!r}: link type {lt!r} ({lk.uid!r}) is "
                "not supported — use direct integration "
                "(run_time_history)")
        lo, up = _bearing_axis(lk)
        kv = float(prm.get("kv", ISOLATOR_DEFAULT_KV))
        d0z = 0.0
        if disp_at is not None:
            ul, uu = disp_at(lo), disp_at(up)
            d0z = (uu[2] if uu else 0.0) - (ul[2] if ul else 0.0)
        N_grav = -kv * d0z
        xy = [("dof", 0), ("dof", 1)]
        xyz = xy + [("dof", 2)]
        if lt == "isolator":
            k1 = float(prm["k1"])
            lin.links.append(LinkMember(lk.uid, pi, pj,
                                        [k1, k1, kv, 0.0, 0.0, 0.0]))
            for d0 in (0, 1):
                recs.append((_fd._Scalar(_fna.BilinearComponent(
                    prm["k1"], prm["k2"], prm["Fy"])), [("dof", d0)],
                    [k1], (pi, pj), "shear", lk.uid))
            continue
        if lt in ("fp_isolator", "friction_spring"):
            k0 = float(prm.get("k_init", FP_DEFAULT_KINIT))
            R = float(prm["R"]) if lt == "fp_isolator" else math.inf
            if disp_at is not None:
                N0, Nref = 0.0, N_grav
            elif lt == "fp_isolator" and "P0" in prm:
                N0 = Nref = float(prm["P0"])
            else:
                raise ValueError(
                    f"FNA case {th.name!r}: {lt} {lk.uid!r} needs its axial "
                    "load — give the case gravity initial conditions "
                    "(initial_case / gravity) or the link param P0")
            kl = k0 + (0.0 if math.isinf(R) else Nref / R)
            dev = _fd.FrictionPendulum(R, float(prm["mu"]), k0, N0, kv)
            lin.links.append(LinkMember(lk.uid, lo, up,
                                        [kl, kl, kv, 0.0, 0.0, 0.0]))
            recs.append((dev, xyz, [kl, kl, kv], (lo, up), "shear", lk.uid))
        elif lt == "triple_fp":
            L1, L2, L3 = (float(prm[k]) for k in ("R1", "R2", "R3"))
            mus = [float(prm[k]) for k in ("mu1", "mu2", "mu3")]
            ds = [float(prm[k]) for k in ("d1", "d2", "d3")]
            uy = float(prm.get("uy", TFP_DEFAULT_UY))
            if disp_at is not None:
                W0, Wref = 0.0, N_grav
            else:
                W0 = Wref = float(prm["W"])
            dev = _fd.TripleFP(L1, L2, L3, *mus, *ds, W0, uy, kv)
            comp = 0.0
            for el in dev.els:
                comp += 1.0 / (el.mu * Wref / el.uy + Wref / el.Reff) \
                    if Wref > 0.0 else 0.0
            kl = 1.0 / comp if comp > 0.0 else 0.0
            lin.links.append(LinkMember(lk.uid, lo, up,
                                        [kl, kl, kv, 0.0, 0.0, 0.0]))
            recs.append((dev, xyz, [kl, kl, kv], (lo, up), "shear", lk.uid))
        elif lt in ("multilinear", "multilinear_kinematic"):
            dev = _fd.MultilinearMasing(prm["points"], rows=2)
            kl = dev.k_init
            lin.links.append(LinkMember(lk.uid, lo, up,
                                        [kl, kl, kv, 0.0, 0.0, 0.0]))
            recs.append((dev, xy, [kl, kl], (lo, up), "shear", lk.uid))
        else:                                   # Wen bearings
            kl_, q, uy, be, ga, eta, a2k, mu, k0 = _wen_params(lt, prm)
            dev = _fd.WenComponent(kl_, q, uy, rows=2, beta=be, gamma=ga,
                                   eta=eta, a2k=a2k, mu=mu)
            lin.links.append(LinkMember(lk.uid, lo, up,
                                        [k0, k0, kv, 0.0, 0.0, 0.0]))
            recs.append((dev, xy, [k0, k0], (lo, up), "shear", lk.uid))
    return lin, recs


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def run(eng, name: str):
    """Extended FNA of TH case ``name`` (cached in ``eng._fna_cache``)."""
    from skyframe.engine import modal_cases as _mcx
    from skyframe.engine import thmulti as _thm
    from skyframe.engine import towers_ext as _twx
    from skyframe.engine.opensees_engine import (_TOL as _ETOL,
                                                 OpenSeesEngine, THResults)
    model = eng.model
    th = model.th_cases[name]
    if getattr(th, "nonlinear", False):
        raise NotImplementedError(
            f"FNA case {name!r}: hinge nonlinearity is not supported by "
            "modal superposition — use direct integration "
            "(run_time_history)")
    accel, dt = eng._resolve_th_record(th)
    mc = _thm.plan_for(eng, th)
    if mc is not None:
        accel, dt = mc.zero_accel(), mc.dt
    disp_at = _initial_state(eng, th)
    lin, recs = _devices(eng, th, model, disp_at)

    # ---- basis --------------------------------------------------------
    spec = None
    if getattr(th, "modal_case", ""):
        lin, spec = _mcx.prepare_model(lin, th.modal_case)
        if spec["stiffness_case"]:
            raise ValueError(f"FNA case {name!r}: modal case "
                             f"{th.modal_case!r} uses a nonlinear "
                             "stiffness_case — FNA needs the elastic basis")
    eng2 = OpenSeesEngine(lin)
    if spec is not None:
        spec = dict(spec)
        spec["_link_points"] = [(r[4], r[3][0], r[3][1]) for r in recs
                                if r[4] == "axial" or r[1][0] == ("dof", 0)
                                ]
        _mcx.attach_spec(eng2, spec)
    modal = eng2.run_modal()
    if not modal.periods:
        raise RuntimeError(f"FNA case {name!r}: the linearized model has no "
                           "dynamic modes (no mass on unrestrained DOFs)")
    asm2 = eng2._asm
    nm = len(modal.periods)
    w = np.array([2.0 * math.pi / T for T in modal.periods])
    mass_items = [((t, d), mv) for (t, d), mv in asm2.mass_map.items()]
    shp: List[Dict[int, np.ndarray]] = [
        {t: np.asarray(p, dtype=float) for t, p in
         modal.shapes[i + 1].items()} for i in range(nm)]

    def mdot(sa, sb) -> float:
        return sum(mv * sa[t][d - 1] * sb[t][d - 1]
                   for (t, d), mv in mass_items)

    for i in range(nm):                       # modified Gram-Schmidt
        for j in range(i):
            cij = mdot(shp[i], shp[j])
            if cij != 0.0:
                for t in shp[i]:
                    shp[i][t] = shp[i][t] - cij * shp[j][t]
        den = mdot(shp[i], shp[i])
        s = 1.0 / math.sqrt(den) if den > 0.0 else 0.0
        for t in shp[i]:
            shp[i][t] = shp[i][t] * s

    def nphi(i: int, t: int, dof0: int) -> float:
        return float(shp[i][t][dof0])

    dof = 1 if th.direction == "X" else 2
    L_dir = np.array([sum(mv * nphi(i, t, d - 1)
                          for (t, d), mv in mass_items if d == dof)
                      for i in range(nm)])
    gammas = L_dir
    M_dir = sum(mv for (t, d), mv in mass_items if d == dof)
    p_ext = None
    if mc is not None:
        p_ext, L_mc, M_mc = mc.fna_modal_loads(eng2, asm2, shp, nm,
                                               mass_items)
        mc.check_vertical(lin, asm2.mass_map)

    if getattr(th, "damping_model", "rayleigh") == "modal":
        zetas = np.array(eng._padded_zetas(th, nm))
    else:
        w_i = w[0]
        w_j = w[min(3, nm) - 1]
        a0 = 2.0 * th.damping * w_i * w_j / (w_i + w_j)
        a1 = 2.0 * th.damping / (w_i + w_j)
        zetas = 0.5 * (a0 / w + a1 * w)

    # ---- device rows --------------------------------------------------
    def node_modes(point):
        try:
            t = eng2._find_node(asm2, point)
        except ValueError:
            return None
        return [shp[i][t] for i in range(nm)]

    devices: List[object] = []
    b_rows: List[np.ndarray] = []
    k_lin: List[float] = []
    d0: List[float] = []
    for dev, rowspec, kls, (pa, pb), _kind, _uid in recs:
        sa, sb = node_modes(pa), node_modes(pb)
        ua = disp_at(pa) if disp_at is not None else None
        ub = disp_at(pb) if disp_at is not None else None
        for (rk, rv), kl in zip(rowspec, kls):
            row = np.zeros(nm)
            for i in range(nm):
                vb = sb[i][:3] if sb is not None else np.zeros(3)
                va = sa[i][:3] if sa is not None else np.zeros(3)
                row[i] = float((vb - va) @ rv) if rk == "axial" \
                    else float(vb[rv] - va[rv])
            b_rows.append(row)
            k_lin.append(float(kl))
            ga = np.zeros(3) if ua is None else np.array(ua[:3])
            gb = np.zeros(3) if ub is None else np.array(ub[:3])
            d0.append(float((gb - ga) @ rv) if rk == "axial"
                      else float(gb[rv] - ga[rv]))
        devices.append(dev)
    B = np.array(b_rows) if b_rows else None
    D0 = np.array(d0) if d0 else np.zeros(0)
    F0_parts = []
    r0 = 0
    for dev in devices:
        sl = slice(r0, r0 + dev.rows)
        F0_parts.append(_fd.preload(dev, D0[sl]) if disp_at is not None
                        else np.asarray(dev.trial(
                            D0[sl], np.zeros(dev.rows), dt), dtype=float))
        r0 += dev.rows
    F0 = np.concatenate(F0_parts) if F0_parts else np.zeros(0)
    axial0: Dict[str, float] = {}             # bearing axial loads N0
    r0 = 0
    for dev, rec in zip(devices, recs):
        if hasattr(dev, "normal"):
            axial0[rec[5]] = float(dev.normal(D0[r0 + 2]))
        r0 += dev.rows

    ag = [float(th.scale) * float(a) for a in accel]
    q, qd, qdd, fdev = _fd.fna_integrate(
        w, zetas, gammas, ag, dt, devices, B,
        (k_lin if k_lin else None), d0=D0, F0=F0, p_ext=p_ext)

    # ---- THResults (the legacy assembly) -------------------------------
    stories = model.stories
    n = len(accel)
    t_out = [(k + 1) * dt for k in range(n)]

    def story_rows(dof0: int) -> Dict[str, np.ndarray]:
        rows: Dict[str, np.ndarray] = {}
        for s in stories:
            row = np.zeros(nm)
            if s.name in asm2.masters:
                t = asm2.masters[s.name]
                for i in range(nm):
                    row[i] = nphi(i, t, dof0)
            else:
                nodes = asm2.story_nodes.get(s.name, [])
                if nodes:
                    for i in range(nm):
                        row[i] = (sum(nphi(i, t, dof0) for t in nodes)
                                  / len(nodes))
            rows[s.name] = row
        return rows

    rows_x = story_rows(0)
    rows_y = story_rows(1)
    sux = {s.name: [float(v) for v in q @ rows_x[s.name]] for s in stories}
    suy = {s.name: [float(v) for v in q @ rows_y[s.name]] for s in stories}
    ag_end = np.array([ag[k + 1] if k + 1 < n else 0.0 for k in range(n)])
    svx: Dict[str, List[float]] = {}
    svy: Dict[str, List[float]] = {}
    for s in stories:
        for d, out in ((1, svx), (2, svy)):
            row = np.array([
                sum(mv * nphi(i, t, d - 1) for (t, dd), mv in mass_items
                    if dd == d
                    and asm2.node_coords[t][2] >= s.elevation - _ETOL)
                for i in range(nm)])
            m_above = sum(mv for (t, dd), mv in mass_items
                          if dd == d
                          and asm2.node_coords[t][2] >= s.elevation - _ETOL)
            if mc is None:
                ag_term = ag_end * (m_above if d == dof else 0.0)
            else:
                ag_term = mc.ag_end[d] * m_above - mc.ext_above(
                    d, s.elevation, asm2.node_coords)
            out[s.name] = [float(v) for v in -(qdd @ row + ag_term)]
    r_dir = qdd @ L_dir + (qd * (2.0 * zetas * w)) @ L_dir + M_dir * ag_end
    bfx = [float(v) for v in (r_dir if dof == 1 else np.zeros(n))]
    bfy = [float(v) for v in (r_dir if dof == 2 else np.zeros(n))]
    if mc is not None:
        cz = 2.0 * zetas * w
        bfx, bfy, mc.fz = (
            [float(v) for v in mc.fna_base(d, qdd, qd, cz, L_mc, M_mc)]
            for d in (1, 2, 3))

    def peak(vals: Sequence[float]) -> float:
        return max(abs(v) for v in vals) if len(vals) else 0.0

    peaks_story: Dict[str, Dict[str, float]] = {}
    prev_ux = [0.0] * n
    prev_uy = [0.0] * n
    for s in stories:
        h = s.height if s.height > 0 else 1.0
        ux_s, uy_s = sux[s.name], suy[s.name]
        peaks_story[s.name] = {
            "ux": peak(ux_s), "uy": peak(uy_s),
            "drift_x": peak([(u - p) / h for u, p in zip(ux_s, prev_ux)]),
            "drift_y": peak([(u - p) / h for u, p in zip(uy_s, prev_uy)]),
            "shear_x": peak(svx[s.name]), "shear_y": peak(svy[s.name]),
        }
        prev_ux, prev_uy = ux_s, uy_s
    peaks = {"story": peaks_story, "base": {"FX": peak(bfx), "FY": peak(bfy)}}
    result = THResults(name, t_out, sux, suy, bfx, bfy, peaks,
                       nonlinear=False)
    if mc is not None:
        result.extra.update(mc.extra())
    result.extra["fna"] = _fna_extra(th, spec, modal, recs, disp_at,
                                     eng2)
    result.extra["fna"]["bearing_axial"] = axial0
    _twx.fna(eng, th, result, svx, svy, eng2, shp, w, mass_items, q,
             (fdev - F0) if len(F0) else fdev, B, k_lin)
    eng._fna_cache[name] = result
    return result


def _fna_extra(th, spec, modal, recs, disp_at, eng2) -> dict:
    """``THResults.extra["fna"]`` — basis / device / initial-state summary."""
    kinds = {}
    for dev, _rs, _kl, _pp, _k, _u in recs:
        nm_ = type(dev).__name__ if not isinstance(dev, _fd._Scalar) \
            else type(dev.law).__name__
        kinds[nm_] = kinds.get(nm_, 0) + 1
    return {"basis": (spec["type"] if spec else "eigen"),
            "modal_case": getattr(th, "modal_case", "") or "",
            "modes": len(modal.periods),
            "periods": [float(T) for T in modal.periods],
            "initial_case": (getattr(th, "initial_case", "")
                             or ("gravity" if th.gravity else "")),
            "devices": kinds,
            "warnings": list(eng2.__dict__.get("_ritz_warnings", []))}
