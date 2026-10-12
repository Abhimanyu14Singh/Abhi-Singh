"""OpenSees builders for the wave-B link types (Plastic (Wen), BRB,
bilinear damper, T/C friction isolator).

Semantics and parameters: :mod:`skyframe.core.links_ext`.  Called from
``OpenSeesEngine._build`` with one line; returns the advanced material /
element / friction-model tag counters so the engine's tag namespaces stay
contiguous.
"""

from __future__ import annotations

import math
from typing import List, Tuple

import openseespy.opensees as ops

from skyframe.core import links_ext as _lx
from skyframe.engine import link_rayleigh as _lrl

BEARING_ROT_STIFF = 10.0     # same tiny bearing rotational stiffness
GAP_YIELD_HUGE = 1.0e12      # never-reached yield (gap / hook links)


def _g(prm, ltype, key) -> float:
    return float(prm.get(key, _lx.DEFAULTS[ltype].get(key, 0.0)))


def wen_material(prm: dict, mtag: int) -> int:
    """``BoucWen`` uniaxial material of a Plastic (Wen) DOF; returns tag."""
    k, fy = float(prm["k"]), float(prm["Fy"])
    r, ex = _g(prm, "plastic_wen", "ratio"), _g(prm, "plastic_wen", "exp")
    uy = fy / k
    g = 0.5 / uy ** ex                 # z in displacement units: z_d = uy z
    mtag += 1
    ops.uniaxialMaterial("BoucWen", mtag, r, k, ex, g, g, 1.0, 0.0, 0.0, 0.0)
    return mtag


def brb_material(prm: dict, mtag: int) -> int:
    bt, bc = _lx.brb_hardening(prm)
    r0 = _g(prm, "brb", "R0")
    mtag += 1
    ops.uniaxialMaterial("Steel4", mtag, float(prm["Fy"]), float(prm["k"]),
                         "-asym", "-kin", bt, r0, _lx.STEEL4_R1,
                         _lx.STEEL4_R2, bc, r0, _lx.STEEL4_R1, _lx.STEEL4_R2)
    return mtag


def damper_material(prm: dict, mtag: int) -> int:
    mtag += 1
    ops.uniaxialMaterial("BilinearOilDamper", mtag,
                         _g(prm, "damper_bilinear", "k"), float(prm["cd"]),
                         float(prm["Fr"]), _g(prm, "damper_bilinear", "ratio"))
    return mtag


def _element(eng, asm, etag: int, ni: int, nj: int, mats: List[int],
             dirs: List[int], zero: bool, kind: str) -> None:
    if zero:
        ops.element("zeroLength", etag, ni, nj, "-mat", *mats, "-dir", *dirs,
                    *_lrl.flag(eng, asm, etag, kind, dirs))
    else:
        ops.element("twoNodeLink", etag, ni, nj, "-mat", *mats, "-dir",
                    *dirs, *_lrl.flag(eng, asm, etag, kind, dirs,
                                      "twoNodeLink"))


def build(eng, asm, lk, ltype: str, prm: dict, ni: int, nj: int,
          mtag: int, etag: int, frn_tag: int) -> Tuple[int, int, int]:
    """Create the element(s) of one wave-B link; returns
    ``(mtag, etag, frn_tag)`` and records ``asm.link_ele[uid]``."""
    p_i, p_j = asm.node_coords[ni], asm.node_coords[nj]
    zero = math.dist(p_i, p_j) < 1e-6
    if ltype == "tc_fp_isolator":
        if p_i[2] > p_j[2]:
            ni, nj = nj, ni                   # node i = LOWER node
        frn_tag += 1
        ops.frictionModel("Coulomb", frn_tag, float(prm["mu"]))
        mtag += 1
        ops.uniaxialMaterial("Elastic", mtag, _g(prm, ltype, "kv"))
        p_tag = mtag
        rot = []
        for _ in range(3):
            mtag += 1
            ops.uniaxialMaterial("Elastic", mtag, BEARING_ROT_STIFF)
            rot.append(mtag)
        etag += 1
        args = ["singleFPBearing", etag, ni, nj, frn_tag, float(prm["R"]),
                _g(prm, ltype, "k_init"), "-P", p_tag, "-T", rot[0],
                "-My", rot[1], "-Mz", rot[2]]
        if zero:
            args += ["-orient", 0.0, 0.0, 1.0, 1.0, 0.0, 0.0]
        ops.element(*args)
        asm.link_ele[lk.uid] = etag
        kt = _g(prm, ltype, "kt")
        if kt > 0.0:                          # tension hold-down (hook)
            mtag += 1
            ops.uniaxialMaterial("ElasticPPGap", mtag, kt, GAP_YIELD_HUGE,
                                 _g(prm, ltype, "gap_t"))
            etag += 1
            _element(eng, asm, etag, ni, nj, [mtag], [3 if zero else 1],
                     zero, "hook_link")
            hd = getattr(asm, "link_holddown", None)
            if hd is None:
                hd = {}
                asm.link_holddown = hd
            hd[lk.uid] = etag
        return mtag, etag, frn_tag
    if ltype == "plastic_wen" and not _lx.is_axial(ltype, p_i, p_j):
        shear = []
        for _ in range(2):
            mtag = wen_material(prm, mtag)
            shear.append(mtag)
        mtag += 1
        ops.uniaxialMaterial("Elastic", mtag, _g(prm, ltype, "kv"))
        mats = shear + [mtag] if zero else [mtag] + shear
        etag += 1
        _element(eng, asm, etag, ni, nj, mats, [1, 2, 3], zero,
                 "hysteretic_link")
        asm.link_ele[lk.uid] = etag
        return mtag, etag, frn_tag
    # axial (twoNodeLink dir 1): Wen brace, BRB, bilinear damper
    if ltype == "plastic_wen":
        mtag, kind = wen_material(prm, mtag), "hysteretic_link"
    elif ltype == "brb":
        mtag, kind = brb_material(prm, mtag), "hysteretic_link"
    else:
        mtag, kind = damper_material(prm, mtag), "damper_link"
    etag += 1
    _element(eng, asm, etag, ni, nj, [mtag], [1], False, kind)
    asm.link_ele[lk.uid] = etag
    return mtag, etag, frn_tag
