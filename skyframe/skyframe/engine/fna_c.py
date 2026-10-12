"""FNA (Fast Nonlinear Analysis) support for the Wave-C link options.

Hooked into :func:`skyframe.engine.fna._devices` with two lines:
:func:`records` builds the device records of a link the legacy driver
does not know (it returns ``None`` for every legacy link, which keeps its
bit-identical path), and :func:`apply_eff` makes a link's effective
stiffness ``k_eff`` the linear part kept in the FNA basis (ETABS: the
modes / Ritz vectors of an FNA case use the link effective stiffness; the
nonlinear device force beyond it is the pseudo-force).

Supported here: ``linear_coupled`` (linear, kept in the basis),
``multilinear_elastic`` (translational curves), Takeda / pivot /
degrading / concrete multilinear plastic (:class:`PeakOriented`, the
OpenSees ``Hysteretic`` rule without damage), BRB / ``multilinear_brb``
(sharp bilinear asymmetric kinematic), the bilinear damper, friction
bearings with velocity / normal-force dependent friction, and the T/C
friction isolator (per-direction friction and radius, friction in
tension).  ``hdr_isolator`` and ``multilinear_isotropic`` raise
``NotImplementedError`` (use direct integration).
"""

from __future__ import annotations

import copy
import math
from typing import List, Optional

import numpy as np

from skyframe.core import fna_devices_c as _dc
from skyframe.core import links_c as _lc

_TOL = 1e-6
_PO_TYPES = ("multilinear_takeda", "multilinear_pivot",
             "multilinear_degrading", "multilinear_concrete")


def _nie(th, lt, uid, why=""):
    return NotImplementedError(
        f"FNA case {th.name!r}: link type {lt!r} ({uid!r}) is not "
        f"supported{why} — use direct integration (run_time_history)")


def _coupled(lk, K) -> object:
    from skyframe.core.model import LinkMember
    return LinkMember(lk.uid, tuple(lk.pi), tuple(lk.pj), [0.0] * 6,
                      "linear_coupled", {"K": [list(map(float, r))
                                               for r in K]})


def _mu_fn(prm: dict, key: str = "mu"):
    p = dict(prm)
    if key != "mu":
        p["mu"] = float(prm[key])
        if "mu_fast" in prm:
            p["mu_fast"] = float(prm["mu_fast"]) * p["mu"] / float(prm["mu"])
    return lambda v, N: _lc.friction_mu(p, v, N)


def _bearing_axis(lk):
    pi, pj = tuple(lk.pi), tuple(lk.pj)
    return (pj, pi) if pi[2] > pj[2] else (pi, pj)


def records(th, lk, lt: str, prm: dict, disp_at, lin) -> Optional[list]:
    """Device records of a Wave-C-handled link (``None`` = legacy)."""
    from skyframe.core.model import (DAMPER_DEFAULT_K, FP_DEFAULT_KINIT,
                                     ISOLATOR_DEFAULT_KV, LinkMember)
    from skyframe.core import links_ext as _lx
    pi, pj = tuple(lk.pi), tuple(lk.pj)
    if lt in _lc.LINEAR_TYPES:
        lin.links.append(copy.deepcopy(lk))
        return []
    if lt in ("hdr_isolator", "multilinear_isotropic"):
        raise _nie(th, lt, lk.uid)
    if lt == "multilinear_elastic":
        from skyframe.core.springprops import normalize_curve
        R = _lc.local_axes(pi, pj)
        cv = prm["curves"]
        kl = list(prm.get("k_lin") or [0.0] * 6)
        laws, rows, kls = [], [], []
        diag = [0.0] * 6
        for i, d in enumerate(_lc.DOF_LABELS):
            if d in cv:
                if i >= 3:
                    raise _nie(th, lt, lk.uid, " with a rotational curve")
                xs, ys = normalize_curve(cv[d])
                laws.append((xs, ys))
                rows.append(("axial", R[i]))
                dev_k = _dc.ElasticCurves([(xs, ys)]).k_init[0]
                kls.append(dev_k)
                diag[i] = dev_k
            else:
                diag[i] = kl[i]
        lin.links.append(_coupled(lk, np.diag(diag)))
        if not laws:
            return []
        return [(_dc.ElasticCurves(laws), rows, kls, (pi, pj), "axial",
                 lk.uid)]
    axial = _lc.is_axial(pi, pj) and math.dist(pi, pj) > 1e-9
    if lt in _PO_TYPES or lt in ("multilinear_brb", "brb"):
        if lt == "brb":
            bt, bc = _lx.brb_hardening(prm)
            k0, fy = float(prm["k"]), float(prm["Fy"])
            make = lambda rows: _dc.AsymKinematic(k0, fy, bt, bc,  # noqa
                                                  rows=rows)
            axial = True
        elif lt == "multilinear_brb":
            p = _lc.brb_params(prm)
            k0 = p["E0"]
            make = lambda rows: _dc.AsymKinematic(  # noqa: E731
                p["E0"], p["fy"], p["bt"], p["bc"], rows=rows)
        else:
            if prm.get("damage1", 0.0) or prm.get("damage2", 0.0):
                raise _nie(th, lt, lk.uid, " with damage1 / damage2")
            if lt == "multilinear_takeda":
                px, py, be = 1.0, 1.0, 0.0
            elif lt == "multilinear_pivot":
                from skyframe.core.springprops import (PIVOT_DEFAULT_PINCH_X,
                                                       PIVOT_DEFAULT_PINCH_Y)
                px = float(prm.get("pinch_x", PIVOT_DEFAULT_PINCH_X))
                py = float(prm.get("pinch_y", PIVOT_DEFAULT_PINCH_Y))
                be = float(prm.get("unload_beta", 0.0))
            else:
                px, py, be = (_lc._g(prm, lt, k) for k in
                              ("pinch_x", "pinch_y", "unload_beta"))
            pts = prm["points"]
            k0 = float(pts[0][1]) / float(pts[0][0])
            make = lambda rows: _dc.PeakOriented(  # noqa: E731
                pts, prm.get("points_neg"), px, py, be, rows=rows)
        if axial:
            e = np.array(pj, float) - np.array(pi, float)
            e = e / float(np.linalg.norm(e))
            lin.links.append(_coupled(lk, np.diag([k0, 0, 0, 0, 0, 0])))
            return [(make(1), [("axial", e)], [k0], (pi, pj), "axial",
                     lk.uid)]
        lo, up = _bearing_axis(lk)
        kv = float(prm.get("kv", ISOLATOR_DEFAULT_KV))
        lin.links.append(LinkMember(lk.uid, lo, up,
                                    [k0, k0, kv, 0.0, 0.0, 0.0]))
        return [(make(2), [("dof", 0), ("dof", 1)], [k0, k0], (lo, up),
                 "shear", lk.uid)]
    if lt == "damper_bilinear":
        e = np.array(pj, float) - np.array(pi, float)
        e = e / float(np.linalg.norm(e))
        dev = _dc.BilinearDamper(float(prm.get("k", DAMPER_DEFAULT_K)),
                                 float(prm["cd"]), float(prm["Fr"]),
                                 float(prm.get("ratio", 0.0)))
        return [(dev, [("axial", e)], [0.0], (pi, pj), "axial", lk.uid)]
    if lt == "tc_fp_isolator" or (lt in _lc.FRICTION_TYPES
                                  and "mu_fast" in prm):
        lo, up = _bearing_axis(lk)
        kv = float(prm.get("kv", ISOLATOR_DEFAULT_KV))
        k0 = float(prm.get("k_init", FP_DEFAULT_KINIT))
        d0z = 0.0
        if disp_at is not None:
            ul, uu = disp_at(lo), disp_at(up)
            d0z = (uu[2] if uu else 0.0) - (ul[2] if ul else 0.0)
        N_grav = -kv * d0z
        tc = lt == "tc_fp_isolator"
        if disp_at is not None:
            N0, Nref = 0.0, max(N_grav, 0.0)
        elif lt == "fp_isolator" and "P0" in prm:
            N0 = Nref = float(prm["P0"])
        else:
            raise ValueError(
                f"FNA case {th.name!r}: {lt} {lk.uid!r} needs its axial "
                "load — give the case gravity initial conditions "
                "(initial_case / gravity)"
                + (" or the link param P0" if lt == "fp_isolator" else ""))
        R2 = math.inf if lt == "friction_spring" else float(prm["R"])
        R3 = float(prm.get("R3", R2)) if tc else R2
        per_dir = tc and ("mu3" in prm or "R3" in prm)
        dev = _dc.FrictionBearing(
            R2, R3, _mu_fn(prm), k0, kv, per_dir=per_dir,
            mu3_fn=_mu_fn(prm, "mu3") if "mu3" in prm else None,
            kt=float(prm.get("kt", 0.0)) if tc else 0.0,
            gap=float(prm.get("gap_t", 0.0)) if tc else 0.0,
            mu_t_fn=_mu_fn(prm, "mu_t") if "mu_t" in prm else None,
            R_t=float(prm.get("R_t", prm.get("R", math.inf))),
            N0=N0, tc=tc)
        k2 = k0 + (0.0 if math.isinf(R2) else Nref / R2)
        k3 = k0 + (0.0 if math.isinf(R3) else Nref / R3)
        lin.links.append(LinkMember(lk.uid, lo, up,
                                    [k2, k3, kv, 0.0, 0.0, 0.0]))
        return [(dev, [("dof", 0), ("dof", 1), ("dof", 2)], [k2, k3, kv],
                 (lo, up), "shear", lk.uid)]
    return None


def apply_eff(model, lin, recs: List[tuple]) -> None:
    """Links with ``k_eff``: the effective stiffness is the linear part
    kept in the basis (rows' k_lin and the linearised model's link)."""
    from skyframe.core.model import LinkMember
    eff = {}
    for lk in model.links:
        prm = LinkMember.coerce_params(getattr(lk, "params", {}) or {})
        if "k_eff" in prm and getattr(lk, "link_type", "elastic") \
                not in _lc.LINEAR_TYPES:
            eff[lk.uid] = (lk, [float(x) for x in prm["k_eff"]])
    if not eff:
        return
    lin.links[:] = [l for l in lin.links if l.uid not in eff] + [
        _coupled(lk, np.diag(k)) for lk, k in eff.values()]
    for n, rec in enumerate(recs):
        uid = rec[5]
        if uid not in eff:
            continue
        lk, k = eff[uid]
        R = _lc.local_axes(lk.pi, lk.pj)
        Kt = R.T @ np.diag(k[:3]) @ R
        kl = []
        for kind, val in rec[1]:
            if kind == "axial":
                e = np.asarray(val, float)
                kl.append(float(e @ Kt @ e))
            else:
                kl.append(float(Kt[val, val]))
        recs[n] = (rec[0], rec[1], kl, rec[3], rec[4], rec[5])
