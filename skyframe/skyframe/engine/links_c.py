"""OpenSees builders for the Wave-C link properties.

Semantics and parameters: :mod:`skyframe.core.links_c`.  Called from the
link loop of ``OpenSeesEngine._build`` with one line (:func:`routed` /
:func:`build`); returns the advanced material / element / friction-model
/ node tag counters so the engine's tag namespaces stay contiguous.

Routing (every other link keeps its legacy builder, bit-identical):

* the Wave-C link types (``linear_coupled``, ``multilinear_elastic``,
  ``multilinear_degrading|isotropic|concrete|brb``, ``hdr_isolator``);
* an existing friction type carrying ``mu_fast`` (velocity-dependent
  friction) or a ``tc_fp_isolator`` with ``mu3`` / ``R3`` / ``mu_t``;
* any nonlinear link with ``k_eff`` in a LINEAR build (no ``hinge_case``):
  the effective linear link replaces it (:func:`effective`).
"""

from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core import links_c as _lc
from skyframe.engine import link_rayleigh as _lrl

BEARING_ROT_STIFF = 10.0       # same tiny bearing rotational stiffness
GAP_YIELD_HUGE = 1.0e12        # never-reached yield (gap / hook laws)
SPRING_LEN = 1.0               # m, flexible length of a coupled-K spring
BEARING_ITER = 100             # bearing internal shear iteration: the
BEARING_TOL = 1.0e-6           # default 1e-12 stalls on stiff series pairs


def nonlinear_build(eng, hinge_case) -> bool:
    """True in a NONLINEAR analysis build (pushover, nonlinear static,
    nonlinear time history — every ``_build(hinge_case=...)``)."""
    return hinge_case is not None or bool(
        getattr(eng, "_wc_force_nonlinear", False))


def routed(eng, lk, ltype: str, prm: dict, hinge_case) -> bool:
    if ltype in _lc.C_LINK_TYPES:
        return True
    if ltype == "elastic":
        return False
    if "k_eff" in prm and not nonlinear_build(eng, hinge_case):
        return True
    return _lc.new_params_used(ltype, prm)


def _flag(eng, asm, etag, kind, dirs, two_node):
    if two_node:
        return _lrl.flag(eng, asm, etag, kind, dirs, "twoNodeLink")
    return _lrl.flag(eng, asm, etag, kind, dirs)


def _local_element(eng, asm, etag: int, ni: int, nj: int, mats: List[int],
                   dirs: List[int], R: np.ndarray, zero: bool,
                   kind: str) -> None:
    """zeroLength (zero length) / twoNodeLink oriented on the local axes."""
    if zero:
        ops.element("zeroLength", etag, ni, nj, "-mat", *mats, "-dir",
                    *dirs, "-orient", *[float(v) for v in R[0]],
                    *[float(v) for v in R[1]],
                    *_flag(eng, asm, etag, kind, dirs, False))
    else:
        ops.element("twoNodeLink", etag, ni, nj, "-mat", *mats, "-dir",
                    *dirs, "-orient", *[float(v) for v in R[1]],
                    *_flag(eng, asm, etag, kind, dirs, True))


def build(eng, asm, lk, ltype: str, prm: dict, ni: int, nj: int,
          mtag: int, etag: int, frn_tag: int, tag: int, hinge_case
          ) -> Tuple[int, int, int, int]:
    """Create the element(s) of one routed link; returns
    ``(mtag, etag, frn_tag, tag)`` and records ``asm.link_ele[uid]``."""
    p_i, p_j = asm.node_coords[ni], asm.node_coords[nj]
    zero = math.dist(p_i, p_j) < 1e-6
    if (ltype not in _lc.LINEAR_TYPES and "k_eff" in prm
            and not nonlinear_build(eng, hinge_case)):
        mtag, etag = effective(eng, asm, lk, prm, ni, nj, mtag, etag)
        return mtag, etag, frn_tag, tag
    if ltype == "linear_coupled":
        etag = coupled(asm, lk, prm, ni, nj, etag)
        return mtag, etag, frn_tag, tag
    if ltype == "multilinear_elastic":
        R = _lc.local_axes(p_i, p_j)
        mats, dirs = [], []
        cv = prm["curves"]
        kl = prm.get("k_lin") or [0.0] * 6
        from skyframe.core.springprops import normalize_curve
        for i, d in enumerate(_lc.DOF_LABELS):
            if d in cv:
                xs, ys = normalize_curve(cv[d])
                mtag += 1
                ops.uniaxialMaterial("ElasticMultiLinear", mtag,
                                     "-strain", *xs, "-stress", *ys)
            elif kl[i] > 0.0:
                mtag += 1
                ops.uniaxialMaterial("Elastic", mtag, float(kl[i]))
            else:
                continue
            mats.append(mtag)
            dirs.append(i + 1)
        etag += 1
        _local_element(eng, asm, etag, ni, nj, mats, dirs, R, zero,
                       "multilinear_link")
        asm.link_ele[lk.uid] = etag
        return mtag, etag, frn_tag, tag
    if ltype in _lc.PLASTIC_TYPES:
        if _lc.is_axial(p_i, p_j) and not zero:
            mtag = plastic_material(ltype, prm, mtag)
            etag += 1
            ops.element("twoNodeLink", etag, ni, nj, "-mat", mtag, "-dir", 1,
                        *_flag(eng, asm, etag, "hysteretic_link", [1], True))
            asm.link_ele[lk.uid] = etag
            return mtag, etag, frn_tag, tag
        shear = []
        for _ in range(2):
            mtag = plastic_material(ltype, prm, mtag)
            shear.append(mtag)
        mtag += 1
        ops.uniaxialMaterial("Elastic", mtag,
                             float(prm.get("kv", _lc.ISOLATOR_DEFAULT_KV)))
        R = _lc.local_axes(p_i, p_j)
        etag += 1
        _local_element(eng, asm, etag, ni, nj, [mtag] + shear, [1, 2, 3], R,
                       zero, "hysteretic_link")
        asm.link_ele[lk.uid] = etag
        return mtag, etag, frn_tag, tag
    if ltype == "hdr_isolator":
        if p_i[2] > p_j[2]:
            ni, nj = nj, ni                         # node i = LOWER node
        etag += 1
        args = ["HDR", etag, ni, nj,
                *[float(prm[k]) for k in _lc.HDR_REQUIRED[:6]],
                int(round(float(prm["n"]))),
                *[float(prm[k]) for k in _lc.HDR_REQUIRED[7:]],
                0.0, 0.0, 1.0, 1.0, 0.0, 0.0]
        opt = [k for k in ("kc", "phi_m", "ac")]
        if any(k in prm for k in opt):
            args += [float(prm.get(k, _lc.DEFAULTS["hdr_isolator"][k]))
                     for k in opt]
        ops.element(*args)
        asm.link_ele[lk.uid] = etag
        return mtag, etag, frn_tag, tag
    return friction(eng, asm, lk, ltype, prm, ni, nj, mtag, etag, frn_tag,
                    tag)


# ---------------------------------------------------------------- effective
def effective(eng, asm, lk, prm, ni, nj, mtag, etag) -> Tuple[int, int]:
    """Linear link with k_eff (+ viscous c_eff) per local DOF."""
    p_i, p_j = asm.node_coords[ni], asm.node_coords[nj]
    zero = math.dist(p_i, p_j) < 1e-6
    k, c = _lc.eff_of(prm)
    mats, dirs = [], []
    for d in range(6):
        if k[d] <= 0.0 and c[d] <= 0.0:
            continue
        mtag += 1
        ops.uniaxialMaterial("Elastic", mtag, float(k[d]), float(c[d]))
        mats.append(mtag)
        dirs.append(d + 1)
    R = _lc.local_axes(p_i, p_j)
    etag += 1
    _local_element(eng, asm, etag, ni, nj, mats, dirs, R, zero,
                   "elastic_link")
    asm.link_ele[lk.uid] = etag
    eff = getattr(asm, "link_effective", None)
    if eff is None:
        eff = asm.link_effective = {}
    eff[lk.uid] = {"ele": etag, "k_eff": k, "c_eff": c, "dirs": dirs}
    return mtag, etag


# ---------------------------------------------------------------- coupled K
def _vecxz(e: np.ndarray) -> List[float]:
    ax = int(np.argmin(np.abs(e)))
    v = np.zeros(3)
    v[ax] = 1.0
    return [float(x) for x in v]


def coupled(asm, lk, prm, ni, nj, etag) -> int:
    """Exact parallel-spring realisation of the 6x6 K (core.links_c.decompose):
    each spring an ``elasticBeamColumn`` (E = G = 1, flexible length
    SPRING_LEN) placed on its line by ``-jntOffset`` (j's offsets carry
    the arm X_i - X_j, so d is measured at node i)."""
    Xi = np.asarray(asm.node_coords[ni], float)
    Xj = np.asarray(asm.node_coords[nj], float)
    parts = _lc.decompose(_lc.global_K(prm["K"], Xi, Xj))
    ell = SPRING_LEN
    tags = []

    def spring(e, r, A, J):
        nonlocal etag
        etag += 1
        oi = r - 0.5 * ell * e
        oj = (Xi - Xj) + r + 0.5 * ell * e
        ops.geomTransf("Linear", etag, *_vecxz(e), "-jntOffset",
                       *[float(v) for v in oi], *[float(v) for v in oj])
        ops.element("elasticBeamColumn", etag, ni, nj, float(A), 1.0, 1.0,
                    float(J), 0.0, 0.0, etag)
        tags.append(etag)
    for k, e, r in parts["lines"]:
        spring(np.asarray(e, float), np.asarray(r, float), k * ell, 0.0)
    for k, a in parts["torsions"]:
        spring(np.asarray(a, float), np.zeros(3), 0.0, k * ell)
    if not tags:
        return etag
    asm.link_ele[lk.uid] = tags[0]
    multi = getattr(asm, "link_multi_ele", None)
    if multi is None:
        multi = asm.link_multi_ele = {}
    multi[lk.uid] = tags
    return etag


# ---------------------------------------------------------------- plastic
def plastic_material(ltype: str, prm: dict, mtag: int) -> int:
    """One uniaxial material of a multilinear plastic hysteresis option."""
    g = lambda k: _lc._g(prm, ltype, k)               # noqa: E731
    mtag += 1
    if ltype in ("multilinear_degrading", "multilinear_concrete"):
        ops.uniaxialMaterial("Hysteretic", mtag, *_lc.hysteretic_args(prm),
                             g("pinch_x"), g("pinch_y"), g("damage1"),
                             g("damage2"), g("unload_beta"))
    elif ltype == "multilinear_isotropic":
        p = _lc.isotropic_params(prm)
        ops.uniaxialMaterial("Steel01", mtag, p["fy"], p["E0"], p["b"],
                             p["a1"], p["a2"], p["a3"], p["a4"])
    else:                                             # multilinear_brb
        p = _lc.brb_params(prm)
        args = ["Steel4", mtag, p["fy"], p["E0"], "-asym", "-kin", p["bt"],
                p["R0"], _lc.STEEL4_R1, _lc.STEEL4_R2, p["bc"], p["R0"],
                _lc.STEEL4_R1, _lc.STEEL4_R2]
        if p["iso_b"] > 0.0:
            bi, rho = p["iso_b"], p["iso_rho"]
            args += ["-iso", bi, rho, 0.1 * bi, 1.0, 1.0,
                     bi, rho, 0.1 * bi, 1.0]
        ops.uniaxialMaterial(*args)
    return mtag


# ---------------------------------------------------------------- friction
def _bearing(etag, ni, nj, frn, R, k0, p_tag, rot, zero, flat=False):
    args = (["flatSliderBearing", etag, ni, nj, frn, k0] if flat
            else ["singleFPBearing", etag, ni, nj, frn, R, k0])
    args += ["-P", p_tag, "-T", rot[0], "-My", rot[1], "-Mz", rot[2]]
    args += ["-iter", BEARING_ITER, BEARING_TOL]
    if zero:
        args += ["-orient", 0.0, 0.0, 1.0, 1.0, 0.0, 0.0]
    ops.element(*args)


def _rot_mats(mtag):
    rot = []
    for _ in range(3):
        mtag += 1
        ops.uniaxialMaterial("Elastic", mtag, BEARING_ROT_STIFF)
        rot.append(mtag)
    return rot, mtag


def friction(eng, asm, lk, ltype, prm, ni, nj, mtag, etag, frn_tag, tag):
    """fp_isolator / friction_spring / tc_fp_isolator with the Wave-C
    friction keys (velocity / normal-force dependent friction, per-
    direction friction and radius, friction in tension)."""
    p_i, p_j = asm.node_coords[ni], asm.node_coords[nj]
    if p_i[2] > p_j[2]:
        ni, nj = nj, ni                             # node i = LOWER node
    zero = math.dist(p_i, p_j) < 1e-6
    k0 = float(prm.get("k_init", _lc.FP_DEFAULT_KINIT))
    kv = float(prm.get("kv", _lc.ISOLATOR_DEFAULT_KV))
    flat = ltype == "friction_spring"
    per_dir = ltype == "tc_fp_isolator" and ("mu3" in prm or "R3" in prm)
    if per_dir:
        raise NotImplementedError(
            f"Link {lk.uid} (tc_fp_isolator): per-direction friction / "
            "radius (mu3, R3) is supported by FNA (run_fna); OpenSees has no "
            "orthotropic friction bearing for direct integration")
    frn_tag += 1
    ops.frictionModel(*_lc.friction_model_args(prm, frn_tag))
    mtag += 1
    ops.uniaxialMaterial("Elastic", mtag, kv)
    p_tag = mtag
    rot, mtag = _rot_mats(mtag)
    etag += 1
    _bearing(etag, ni, nj, frn_tag, None if flat else float(prm["R"]),
             k0, p_tag, rot, zero, flat=flat)
    main = etag
    asm.link_ele[lk.uid] = main
    kt = float(prm.get("kt", 0.0))
    if ltype == "tc_fp_isolator" and kt > 0.0:
        gap = float(prm.get("gap_t", 0.0))
        hook_k = kt
        if "mu_t" in prm:
            # friction in TENSION: a second bearing whose axial material
            # is NEGATIVE in tension past the gap, sigma = -kt (d - gap)
            # (zero below it).  singleFPBearing reads that as compression
            # N = kt (d - gap) and slides with mu_t N + N u / R_t; when
            # sigma >= 0 (link closed / compressed) it lifts off with
            # zero force (verified).  Its -kt axial tangent is offset by
            # a 2 kt hook in parallel, so the net hold-down is +kt.
            frn_tag += 1
            ops.frictionModel(*_lc.friction_model_args(prm, frn_tag,
                                                       "mu_t"))
            mtag += 1
            ops.uniaxialMaterial("Elastic", mtag, -kt, 0.0, 0.0)
            if gap > 0.0:
                mtag += 1
                ops.uniaxialMaterial("InitStrainMaterial", mtag, mtag - 1,
                                     -gap)
            p_tag = mtag
            rot, mtag = _rot_mats(mtag)
            etag += 1
            _bearing(etag, ni, nj, frn_tag,
                     float(prm.get("R_t", prm["R"])), k0, p_tag, rot, zero)
            tens = getattr(asm, "link_tension_bearing", None)
            if tens is None:
                tens = asm.link_tension_bearing = {}
            tens[lk.uid] = etag
            hook_k = 2.0 * kt
        mtag += 1
        ops.uniaxialMaterial("ElasticPPGap", mtag, hook_k, GAP_YIELD_HUGE,
                             gap)
        etag += 1
        if zero:
            ops.element("zeroLength", etag, ni, nj, "-mat", mtag, "-dir", 3,
                        *_lrl.flag(eng, asm, etag, "hook_link", [3]))
        else:
            ops.element("twoNodeLink", etag, ni, nj, "-mat", mtag, "-dir", 1,
                        *_lrl.flag(eng, asm, etag, "hook_link", [1],
                                   "twoNodeLink"))
        hd = getattr(asm, "link_holddown", None)
        if hd is None:
            hd = asm.link_holddown = {}
        hd[lk.uid] = etag
    return mtag, etag, frn_tag, tag
