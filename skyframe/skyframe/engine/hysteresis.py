"""OpenSees builders for named point springs and the hysteresis link types.

Pure construction helpers called from ``OpenSeesEngine._build``; the
semantics (laws, parameters, approximations) are documented in
:mod:`skyframe.core.springprops`.  Every helper takes the running material /
element tag counters and returns the advanced values, so the engine's tag
namespaces stay contiguous.
"""

from __future__ import annotations

from typing import List, Tuple

import openseespy.opensees as ops

from skyframe.core.springprops import (AXIAL_ONLY_RATIO, BW_DEFAULTS,
                                       GAP_YIELD_HUGE, PIVOT_DEFAULT_PINCH_X,
                                       PIVOT_DEFAULT_PINCH_Y, DofLaw,
                                       SpringSpec)

# defaults shared with the v0.15/v0.21 device links (kept in sync by value)
ISOLATOR_DEFAULT_KV = 1.0e7
FP_DEFAULT_KINIT = 1.0e5
BEARING_ROT_STIFF = 10.0


# ------------------------------------------------------------ point springs
def _law_material(law: DofLaw, mtag: int) -> int:
    """Create the uniaxial material of one local DOF; returns its tag."""
    r = AXIAL_ONLY_RATIO
    if law.kind == "linear":
        mtag += 1
        ops.uniaxialMaterial("Elastic", mtag, float(law.k))
    elif law.kind == "c_only":
        mtag += 1
        ops.uniaxialMaterial("Elastic", mtag, law.k * r, 0.0, float(law.k))
    elif law.kind == "t_only":
        mtag += 1
        ops.uniaxialMaterial("Elastic", mtag, float(law.k), 0.0, law.k * r)
    elif law.kind == "gap":
        ops.uniaxialMaterial("ElasticPPGap", mtag + 1, float(law.k),
                             -GAP_YIELD_HUGE, -float(law.gap))
        ops.uniaxialMaterial("Elastic", mtag + 2, law.k * r)
        ops.uniaxialMaterial("Parallel", mtag + 3, mtag + 1, mtag + 2)
        mtag += 3
    else:  # curve
        mtag += 1
        ops.uniaxialMaterial("ElasticMultiLinear", mtag,
                             "-strain", *[float(v) for v in law.xs],
                             "-stress", *[float(v) for v in law.ys])
    return mtag


def build_named_spring(spec: SpringSpec, gnd: int, rt: int, mtag: int,
                       etag: int) -> Tuple[int, int]:
    """zeroLength(ground -> real) with one material per active LOCAL dof
    and ``-orient`` for non-global axes.  Returns (mtag, etag)."""
    mats: List[int] = []
    dirs: List[int] = []
    for d, law in enumerate(spec.laws):
        if law is None:
            continue
        mtag = _law_material(law, mtag)
        mats.append(mtag)
        dirs.append(d + 1)
    etag += 1
    args = ["zeroLength", etag, gnd, rt, "-mat", *mats, "-dir", *dirs]
    if not spec.identity_axes:
        args += ["-orient", *[float(v) for v in spec.axes[0]],
                 *[float(v) for v in spec.axes[1]]]
    ops.element(*args)
    return mtag, etag


# ------------------------------------------------------------ links
def _hysteretic_args(prm: dict) -> List[float]:
    pos = [[float(v) for v in p] for p in prm["points"]]
    neg = ([[float(v) for v in p] for p in prm["points_neg"]]
           if "points_neg" in prm else [[-d, -f] for d, f in pos])
    out: List[float] = []
    for d, f in pos:
        out += [f, d]
    for d, f in neg:
        out += [f, d]
    return out


def shear_material(ltype: str, prm: dict, mtag: int) -> int:
    """One horizontal-shear material of a material-based hysteresis link."""
    mtag += 1
    if ltype == "multilinear_kinematic":
        pts = [float(v) for p in prm["points"] for v in p]
        ops.uniaxialMaterial("MultiLinear", mtag, *pts)
    elif ltype == "multilinear_takeda":
        ops.uniaxialMaterial("Hysteretic", mtag, *_hysteretic_args(prm),
                             1.0, 1.0, 0.0, 0.0, 0.0)
    else:  # multilinear_pivot
        ops.uniaxialMaterial(
            "Hysteretic", mtag, *_hysteretic_args(prm),
            float(prm.get("pinch_x", PIVOT_DEFAULT_PINCH_X)),
            float(prm.get("pinch_y", PIVOT_DEFAULT_PINCH_Y)),
            0.0, 0.0, float(prm.get("unload_beta", 0.0)))
    return mtag


def material_link(ltype: str, prm: dict, mtag: int,
                  zero_length: bool) -> Tuple[List[int], List[int], int]:
    """(mats, dirs, mtag) with the isolator layout: hysteresis on both
    horizontal shear dirs, elastic vertical kv."""
    shear = []
    for _ in range(2):
        mtag = shear_material(ltype, prm, mtag)
        shear.append(mtag)
    mtag += 1
    ops.uniaxialMaterial("Elastic", mtag,
                         float(prm.get("kv", ISOLATOR_DEFAULT_KV)))
    if zero_length:
        return shear + [mtag], [1, 2, 3], mtag
    return [mtag] + shear, [1, 2, 3], mtag


def bearing_link(ltype: str, prm: dict, ni: int, nj: int, mtag: int,
                 etag: int, frn_tag: int,
                 zero_length: bool) -> Tuple[int, int, int]:
    """Dedicated bearing element (node i = LOWER node).  Returns
    (mtag, etag, frn_tag)."""
    mtag += 1
    ops.uniaxialMaterial("Elastic", mtag,
                         float(prm.get("kv", ISOLATOR_DEFAULT_KV)))
    p_tag = mtag
    rot = []
    for _ in range(3):
        mtag += 1
        ops.uniaxialMaterial("Elastic", mtag, BEARING_ROT_STIFF)
        rot.append(mtag)
    etag += 1
    tail = ["-P", p_tag, "-T", rot[0], "-My", rot[1], "-Mz", rot[2]]
    if zero_length:
        tail += ["-orient", 0.0, 0.0, 1.0, 1.0, 0.0, 0.0]
    if ltype == "friction_spring":
        frn_tag += 1
        ops.frictionModel("Coulomb", frn_tag, float(prm["mu"]))
        ops.element("flatSliderBearing", etag, ni, nj, frn_tag,
                    float(prm.get("k_init", FP_DEFAULT_KINIT)), *tail)
    else:  # rubber_isolator_bouc_wen
        g = {k: float(prm.get(k, v)) for k, v in BW_DEFAULTS.items()}
        ops.element("elastomericBearingBoucWen", etag, ni, nj,
                    float(prm["k_init"]), float(prm["qd"]), g["alpha1"],
                    g["alpha2"], g["mu"], g["eta"], g["beta"], g["gamma"],
                    *tail)
    return mtag, etag, frn_tag
