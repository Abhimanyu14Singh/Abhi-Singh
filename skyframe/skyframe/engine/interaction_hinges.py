"""Parametric interacting hinges (PMM / M2M3) — OpenSees side (Wave B).

See :mod:`skyframe.core.interaction_hinges` for the property format and
the documented idealization.  Each hinge is TWO elements between the
member's node at the hinge station and the duplicated node (the B10
``dup_nodes`` machinery):

* ``zeroLengthSection`` with an ``Elliptical`` section (codes ``My Mz`` =
  member M2 / M3 with ``-orient`` = member local x, y): elastic stiffness
  ``k_e`` per axis (``k_elastic`` or ``UH_RIGID_FACTOR * 6EI/L``), yield
  ellipse (M2/M2y)^2 + (M3/M3y)^2 = 1 at the hinge's axial force, linear
  kinematic hardening H = (r_C - 1) My / d_C from the backbone B -> C;
* ``zeroLength`` with stiff ``Elastic`` springs (``UH_TIE_FACTOR`` x the
  member stiffness, as B10) on local P, V2, V3, T.

When the axial force lies outside the surface (both yield moments 0) the
hinge is RIGID (stiff elastic on all six DOFs) and a warning names it.
"""

from __future__ import annotations

import math
import warnings
from typing import List

import openseespy.opensees as ops

from skyframe.core import interaction_hinges as _cih
from skyframe.core import user_hinges as _cu

SEC_TAG0 = 300000            # Elliptical section tags (shells < 1e5,
                             # fiber 1e5.., ND materials 2e5..)


def next_sec_tag(asm) -> int:
    t = getattr(asm, "_wave_b_sec", SEC_TAG0) + 1
    asm._wave_b_sec = t
    return t


def _sf(prop: dict, My: float, km: float) -> float:
    yd = prop["scale"]["yield_deformation"]
    return 1.0 if yd is None else (My / km if yd == "auto" else yd)


def build(eng, asm, m, h, orig: int, dup: int, mtag: int, etag: int):
    """Create the interacting hinge of plan entry ``h``; returns tags."""
    from skyframe.engine.opensees_engine import _local_axes
    from skyframe.engine import auto_hinges as _ah
    prop = h["prop"]
    model = eng.model
    sec = model.sections[m.section]
    mat = model.materials[sec.material]
    A, I22, I33, J = eng._eff_props(sec)
    L = m.length
    E, G = mat.E, mat.G
    km2 = 6.0 * E * I22 / L
    km3 = 6.0 * E * I33 / L
    ke = prop.get("k_elastic")
    ke2 = ke or _cu.UH_RIGID_FACTOR * km2
    ke3 = ke or _cu.UH_RIGID_FACTOR * km3
    if prop["type"] == "PMM":
        P, src = _ah.pmm_axial(eng, m, h["d"], prop)
    else:
        P, src = 0.0, "n/a"
    ell = _cih.ellipse_at(prop["surface"], P)
    M2y, M3y = ell["M2y"], ell["M3y"]
    k_tr = _cu.UH_TIE_FACTOR * (E * A / L
                                + 12.0 * E * max(I22, I33) / L ** 3)
    k_rot = _cu.UH_TIE_FACTOR * max(6.0 * E * I22, 6.0 * E * I33,
                                    G * J) / L
    xax, yax, _, _, _ = _local_axes(m)
    rigid = not (M2y > 0.0 and M3y > 0.0)
    if rigid:
        warnings.warn(f"interacting hinge {h['property']!r} on member "
                      f"{m.uid!r}: P = {P:.4g} lies outside the interaction "
                      "surface (zero yield moment); hinge made rigid",
                      UserWarning)
        mats = []
        for d in range(1, 7):
            mtag += 1
            ops.uniaxialMaterial("Elastic", mtag, k_tr if d <= 3 else k_rot)
            mats.append(mtag)
        etag += 1
        ops.element("zeroLength", etag, orig, dup, "-mat", *mats,
                    "-dir", 1, 2, 3, 4, 5, 6, "-orient", *xax, *yax)
        return mtag, etag
    sf2 = _sf(prop, M2y, km2)
    sf3 = _sf(prop, M3y, km3)
    h2 = _cih.hardening(prop, M2y, sf2)
    h3 = _cih.hardening(prop, M3y, sf3)
    H2 = max(h2["H"], _cu.UH_PARALLEL_RATIO * km2)
    H3 = max(h3["H"], _cu.UH_PARALLEL_RATIO * km3)
    from skyframe.engine import pmm_update as _pmu       # wave C
    upd = _pmu.active(prop)
    if upd:          # hardening / drop come from the capacity updates
        H2 = _cu.UH_PARALLEL_RATIO * km2
        H3 = _cu.UH_PARALLEL_RATIO * km3
    stag = next_sec_tag(asm)
    ops.section("Elliptical", stag, ke2, ke3, M2y, M3y, 0.0, H2, H3,
                "My", "Mz")
    etag += 1
    ops.element("zeroLengthSection", etag, orig, dup, stag,
                "-orient", *xax, *yax)
    sec_ele = etag
    mats = []
    for d in (1, 2, 3, 4):
        mtag += 1
        ops.uniaxialMaterial("Elastic", mtag, k_tr if d <= 3 else k_rot)
        mats.append(mtag)
    etag += 1
    from skyframe.engine import link_rayleigh as _lrl
    ops.element("zeroLength", etag, orig, dup, "-mat", *mats,
                "-dir", 1, 2, 3, 4, "-orient", *xax, *yax,
                *_lrl.flag(eng, asm, etag, "user_hinge", (1, 2, 3, 4)))
    d = h["d"]
    end = "i" if d <= 0.0 else "j" if d >= 1.0 else f"{d:g}"
    ust = (_pmu.register(asm, sec_ele, etag, d >= 1.0, M2y, M3y, P)
           if upd else None)
    asm.user_hinges.append({
        "uid": m.uid, "end": end, "relative_distance": d,
        "property": h["property"], "prop": prop, "kind": "interaction",
        "ele": sec_ele, "orig": orig, "dup": dup, "material": "Elliptical",
        "env": {"k_e2": ke2, "k_e3": ke3, "M2y": M2y, "M3y": M3y,
                "P": P, "axial_source": src, "sf2": sf2, "sf3": sf3,
                "H2": H2, "H3": H3, "fit": ell["fit"],
                "markers": _cih.markers(prop, 1.0),
                "tol": 1.0e-6 * max(M2y / ke2 / sf2, M3y / ke3 / sf3),
                **({"update": ust} if ust is not None else {})},
        **({"auto": h["auto"]} if "auto" in h else {})})
    return mtag, etag


def new_hist() -> dict:
    return {"rot": [], "moment": [], "rot_plastic": [], "hinge_state": [],
            "state": [], "rot2": [], "rot3": [], "m2": [], "m3": []}


def record(spec: dict, hh: dict, peak, yielded) -> None:
    env = spec["env"]
    defo = ops.eleResponse(spec["ele"], "deformation")
    frc = ops.eleResponse(spec["ele"], "basicForce")
    t2 = float(defo[0]) if len(defo) > 0 else 0.0
    t3 = float(defo[1]) if len(defo) > 1 else 0.0
    m2 = float(frc[0]) if len(frc) > 0 else 0.0
    m3 = float(frc[1]) if len(frc) > 1 else 0.0
    p2 = t2 - m2 / env["k_e2"]
    p3 = t3 - m3 / env["k_e3"]
    dpn = math.hypot(p2 / env["sf2"], p3 / env["sf3"])
    st = _cih.classify(dpn, env["markers"], spec["prop"].get("acceptance"),
                       1.0, env["tol"])
    rot = math.hypot(t2, t3)
    hh["rot"].append(rot)
    hh["moment"].append(math.hypot(m2, m3))
    hh["rot_plastic"].append(math.hypot(p2, p3))
    hh["hinge_state"].append(st)
    hh["state"].append(_cu.LEGACY_STATE[st])
    hh["rot2"].append(t2)
    hh["rot3"].append(t3)
    hh["m2"].append(m2)
    hh["m3"].append(m3)
    if rot > peak.get(spec["uid"], 0.0):
        peak[spec["uid"]] = rot
    if yielded is not None and st != "A-B":
        yielded.add(spec["uid"])
    if "update" in env:                         # wave C capacity update
        from skyframe.engine import pmm_update as _pmu
        _pmu.update(spec, dpn, hh)


def detail(spec: dict, hh: dict) -> dict:
    prop, env = spec["prop"], spec["env"]
    acc = prop.get("acceptance") or {}
    mk = env["markers"]
    sf3 = env["sf3"]
    return {
        "uid": spec["uid"], "end": spec["end"],
        "relative_distance": spec["relative_distance"],
        "property": spec["property"], "type": prop["type"], "user": True,
        "kind": "user_interaction", "My": env["M3y"],
        "My2": env["M2y"], "My3": env["M3y"], "P": env["P"],
        "axial_source": env["axial_source"],
        "thy": env["M3y"] / env["k_e3"],
        "a": mk[1] * sf3 if len(mk) > 1 else None,
        "b": mk[-1] * sf3 if len(mk) > 2 else None, "c": None,
        "IO": acc["IO"] * sf3 if "IO" in acc else None,
        "LS": acc["LS"] * sf3 if "LS" in acc else None,
        "CP": acc["CP"] * sf3 if "CP" in acc else None,
        "acceptance_normalized": dict(acc),
        "deformation_scale": [env["sf2"], env["sf3"]],
        "k_elastic": [env["k_e2"], env["k_e3"]],
        "hardening": [env["H2"], env["H3"]],
        "hysteresis": "kinematic",
        "drop_strength": prop.get("drop_strength", "holds"),
        "material": "Elliptical", "surface_fit": env["fit"],
        **({"capacity_update": True, "axial_update":
            (prop["axial"] or {}).get("source") == "analysis"}
           if "update" in env else {}),
        **({"auto": spec["auto"], "preliminary":
            spec["auto"].get("preliminary", False)}
           if "auto" in spec else {}),
        **{k: list(v) for k, v in hh.items()}}


def specs(asm) -> List[dict]:
    return [s for s in getattr(asm, "user_hinges", [])
            if s.get("kind") == "interaction"]
