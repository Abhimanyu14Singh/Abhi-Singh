"""User-defined plastic hinges (B10) — OpenSees side.

See :mod:`skyframe.core.user_hinges` (property format, envelope
conversion, state classification) and CONTRACT "User-defined hinges and
hinge overwrites".  Active in every HINGED build (``_build(hinge_case=...)``
— pushover, nonlinear static, nonlinear time history); every linear /
eigen analysis sees the elastic member (an interior hinge location only
adds a mesh node there).

Each lumped hinge (M3 / M2 / P / V2) is a ``zeroLength`` element between
the member's node at the hinge location (the joint / support node at an
end, the split node inside the span) and a duplicated node the member
segment connects to instead.  All six local DOFs (``-orient`` = member
local x, y) carry a material: the hinge DOF (P -> 1, V2 -> 2, M2 -> 5,
M3 -> 6) the backbone material, the other five a stiff ``Elastic``
(``UH_TIE_FACTOR`` x the member stiffness — springs instead of equalDOF
ties, so a hinge at a rigid-diaphragm slave never chains MP constraints).

Backbone material (envelope from ``core.user_hinges.build_envelope``):

* ``kinematic`` / ``isotropic`` — ``MultiLinear`` (OpenSees kinematic
  multi-linear; isotropic is APPROXIMATED by it, see ISOTROPIC_NOTE);
* ``takeda`` / ``pivot`` / ``concrete`` — classic ``Hysteretic`` when
  both sides have the same 2 or 3 envelope points, else ``HystereticSM``
  (the up-to-7-point generalisation; identical to Hysteretic on a
  3-point envelope), pinching / damage / beta from HYST_DEFAULTS or the
  property's ``hysteresis_params``.
"""

from __future__ import annotations

import math
import warnings
from typing import Dict, List, Tuple

import openseespy.opensees as ops

from skyframe.core import user_hinges as _cu

_STATION_TOL = 1.0e-5          # m — interior hinge station vs mesh node


# --------------------------------------------------------------- planning
def member_plan(eng) -> Dict[str, List[dict]]:
    """uid -> [{"property", "prop", "d", "t", "placed"}] lumped user
    hinges of every member (PMM_fiber hinges excluded)."""
    model = eng.model
    props = getattr(model, "hinge_properties", {}) or {}
    plan: Dict[str, List[dict]] = {}
    for m in model.members:
        if not _cu.is_user(m):
            continue
        lst = []
        for h in m.hinges:
            p = props[h["property"]]
            if p["type"] == "PMM_fiber":
                continue
            d = float(h["relative_distance"])
            lst.append({"property": h["property"], "prop": p, "d": d,
                        "t": d * m.length, "placed": False})
        if lst:
            plan[m.uid] = lst
    return plan


def drop_auto(model, hinge_plan: dict) -> None:
    """Members with a user hinge list replace the case's automatic
    (column_base / all_ends) hinges."""
    users = {m.uid for m in model.members if _cu.is_user(m)}
    if users:
        for key in [k for k in hinge_plan if k[0] in users]:
            del hinge_plan[key]


def fiber_plan(eng, case) -> Dict[str, dict]:
    """uid -> fiber backbone for members whose user hinges are PMM_fiber
    (the v0.21 HingeRadau fiber member, in ANY hinged build)."""
    model = eng.model
    props = getattr(model, "hinge_properties", {}) or {}
    out: Dict[str, dict] = {}
    for m in model.members:
        if not _cu.is_user(m):
            continue
        fp = [props[h["property"]] for h in m.hinges
              if props[h["property"]]["type"] == "PMM_fiber"]
        if not fp:
            continue
        p = fp[0]
        from skyframe.design.hinges import auto_backbone
        hp = dict(getattr(case, "hinge_params", {}) or {})
        kw = {k: hp[k] for k in ("expected_factor", "rho", "rho_prime",
                                 "fy_bar") if k in hp}
        bb = auto_backbone(model, m, **kw)
        sec = model.sections[m.section]
        mat = model.materials[sec.material]
        if bb is None:
            if sec.h <= 0.0 and not getattr(model, "designer_sections",
                                            {}).get(m.section):
                warnings.warn(f"PMM_fiber hinge: member {m.uid!r} has no "
                              "fiber-able section; left elastic",
                              UserWarning)
                continue
            My = p["scale"]["yield_value"]
            thy = My * m.length / (6.0 * mat.E * sec.I33 * sec.mod_I33)
            bb = {"kind": "user_fiber", "My": My, "thy": thy, "a": 0.0,
                  "b": 0.0, "c": 0.0, "IO": math.inf, "LS": math.inf,
                  "CP": math.inf}
        else:
            bb = dict(bb, kind="user_fiber")
        acc = p.get("acceptance") or {}
        yd = p["scale"]["yield_deformation"]
        sf = 1.0 if yd is None else bb["thy"] if yd == "auto" else yd
        for k, v in acc.items():
            bb[k] = v * sf
        ov = getattr(m, "hinge_overwrites", None) or {}
        if ov.get("relative_length"):
            bb["lp"] = ov["relative_length"] * m.length
        out[m.uid] = bb
    return out


def check_member(m, uh: List[dict], has_offset: bool, ecc) -> None:
    if getattr(m, "axial_limit", "both") != "both":
        raise ValueError(f"Member {m.uid}: a plastic hinge cannot be placed "
                         "on an axial-only member")
    if any(0.0 < h["d"] < 1.0 for h in uh) and (has_offset
                                                or ecc is not None):
        raise ValueError(f"Member {m.uid}: interior user hinges are not "
                         "supported with rigid end / insertion offsets")


def dup_nodes(uh: List[dict], m, seg, last: int, mesh, ni_tag: int,
              nj_tag: int, tag: int, pending: list):
    """Insert the duplicated node(s) of the user hinges that sit at this
    segment's ends; returns (ni_tag, nj_tag, tag)."""
    for h in uh:
        d = h["d"]
        if d <= 0.0:
            at_i = seg.index == 0
            at_j = False
        elif d >= 1.0:
            at_i, at_j = False, seg.index == last
        else:
            at_i = seg.index > 0 and abs(seg.x0 - h["t"]) <= _STATION_TOL
            at_j = False
        if at_i:
            tag += 1
            ops.node(tag, *mesh.points[seg.ni])
            pending.append((m, h, ni_tag, tag))
            ni_tag = tag
            h["placed"] = True
        elif at_j:
            tag += 1
            ops.node(tag, *mesh.points[seg.nj])
            pending.append((m, h, nj_tag, tag))
            nj_tag = tag
            h["placed"] = True
    return ni_tag, nj_tag, tag


# --------------------------------------------------------------- materials
def _hyst_params(prop: dict) -> dict:
    base = dict(_cu.HYST_DEFAULTS[prop["hysteresis"]])
    base.update(prop.get("hysteresis_params") or {})
    return base


def backbone_material(prop: dict, env: dict, mtag: int) -> Tuple[int, str]:
    """Create the hinge-DOF material; returns (tag, OpenSees name)."""
    mtag += 1
    pos = env["positive"]["points"]
    neg = env["negative"]["points"]
    if prop["hysteresis"] in ("kinematic", "isotropic"):
        flat = [v for p in pos for v in p]
        ops.uniaxialMaterial("MultiLinear", mtag, *flat)
        return _parallel(env, mtag, "MultiLinear")
    hp = _hyst_params(prop)
    tail = [hp["pinch_x"], hp["pinch_y"], hp["damage1"], hp["damage2"],
            hp["beta"]]
    if len(pos) == len(neg) and len(pos) in (2, 3):
        args = []
        for d, f in pos:
            args += [f, d]
        for d, f in neg:
            args += [-f, -d]
        ops.uniaxialMaterial("Hysteretic", mtag, *args, *tail)
        return _parallel(env, mtag, "Hysteretic")
    pe = [v for d, f in pos for v in (f, d)]
    ne = [v for d, f in neg for v in (-f, -d)]
    ops.uniaxialMaterial("HystereticSM", mtag, "-posEnv", *pe,
                         "-negEnv", *ne, "-pinch", tail[0], tail[1],
                         "-damage", tail[2], tail[3], "-beta", tail[4])
    return _parallel(env, mtag, "HystereticSM")


def _parallel(env: dict, mtag: int, name: str) -> Tuple[int, str]:
    """Backbone || Elastic(UH_PARALLEL_RATIO * k_member): a flat plateau /
    the residual of a failed hinge keeps a (negligible) positive tangent,
    so a hinge whose DOF has no other stiffness (an axial / shear hinge,
    a plateau mechanism) never makes the tangent EXACTLY singular."""
    ops.uniaxialMaterial("Elastic", mtag + 1,
                         _cu.UH_PARALLEL_RATIO * env["k_member"])
    ops.uniaxialMaterial("Parallel", mtag + 2, mtag, mtag + 1)
    return mtag + 2, name


def build_springs(eng, asm, pending: list, plan: Dict[str, List[dict]],
                  mtag: int, etag: int) -> Tuple[int, int]:
    """zeroLength hinge elements for every placed user hinge."""
    from skyframe.engine.opensees_engine import _local_axes
    model = eng.model
    for uid, lst in plan.items():
        for h in lst:
            if not h["placed"]:
                warnings.warn(f"user hinge {h['property']!r} of member "
                              f"{uid!r} at {h['d']!r} has no mesh node; "
                              "skipped", UserWarning)
    for m, h, orig, dup in pending:
        prop = h["prop"]
        sec = model.sections[m.section]
        mat = model.materials[sec.material]
        A, I22, I33, J = eng._eff_props(sec)
        L = m.length
        E, G = mat.E, mat.G
        km = _cu.k_member(prop["type"], E, A, I22, I33, L)
        env = _cu.build_envelope(prop, km)
        k_tr = _cu.UH_TIE_FACTOR * (E * A / L
                                    + 12.0 * E * max(I22, I33) / L ** 3)
        k_rot = _cu.UH_TIE_FACTOR * max(6.0 * E * I22, 6.0 * E * I33,
                                        G * J) / L
        hdir = _cu.HINGE_DIR[prop["type"]]
        mats = []
        mat_name = ""
        for d in range(1, 7):
            if d == hdir:
                mtag, mat_name = backbone_material(prop, env, mtag)
            else:
                mtag += 1
                ops.uniaxialMaterial("Elastic", mtag,
                                     k_tr if d <= 3 else k_rot)
            mats.append(mtag)
        xax, yax, _, _, _ = _local_axes(m)
        etag += 1
        ops.element("zeroLength", etag, orig, dup, "-mat", *mats,
                    "-dir", 1, 2, 3, 4, 5, 6, "-orient", *xax, *yax)
        d = h["d"]
        end = "i" if d <= 0.0 else "j" if d >= 1.0 else f"{d:g}"
        asm.user_hinges.append({
            "uid": m.uid, "end": end, "relative_distance": d,
            "property": h["property"], "prop": prop, "env": env,
            "ele": etag, "dir": hdir, "material": mat_name,
            "orig": orig, "dup": dup})
    return mtag, etag


# --------------------------------------------------------------- recording
class Recorder:
    """Per-step user-hinge histories (deformation / force / plastic
    deformation / ETABS state / legacy state)."""

    def __init__(self, asm):
        self.asm = asm
        self.hist = [{"rot": [], "moment": [], "rot_plastic": [],
                      "hinge_state": [], "state": []}
                     for _ in getattr(asm, "user_hinges", [])]

    def record(self, peak: Dict[str, float], yielded=None) -> None:
        for spec, hh in zip(self.asm.user_hinges, self.hist):
            k = spec["dir"] - 1
            defo = ops.eleResponse(spec["ele"], "deformation")
            # zeroLength 'basicForce' = the material forces in -dir order
            # (verified: 'force' is the 12 GLOBAL nodal forces)
            frc = ops.eleResponse(spec["ele"], "basicForce")
            d = float(defo[k]) if len(defo) > k else 0.0
            F = float(frc[k]) if len(frc) > k else 0.0
            env = spec["env"]
            st = _cu.classify(d, F, env, spec["prop"].get("acceptance"))
            hh["rot"].append(d)
            hh["moment"].append(F)
            hh["rot_plastic"].append(max(0.0, abs(d) - abs(F) / env["k_e"]))
            hh["hinge_state"].append(st)
            hh["state"].append(_cu.LEGACY_STATE[st])
            if spec["prop"]["type"] in ("M2", "M3"):
                if abs(d) > peak.get(spec["uid"], 0.0):
                    peak[spec["uid"]] = abs(d)
            if yielded is not None and st != "A-B":
                yielded.add(spec["uid"])

    def detail(self) -> List[dict]:
        out: List[dict] = []
        for spec, hh in zip(self.asm.user_hinges, self.hist):
            prop, env = spec["prop"], spec["env"]
            acc = prop.get("acceptance") or {}
            pos = env["positive"]
            mk = pos["markers"]
            n = pos["n_user"]
            pts = pos["points"]
            sf = env["sf_d"]
            out.append({
                "uid": spec["uid"], "end": spec["end"],
                "relative_distance": spec["relative_distance"],
                "property": spec["property"], "type": prop["type"],
                "user": True, "kind": "user",
                "My": env["F_B"], "thy": pts[0][0],
                "a": mk[1] if len(mk) > 1 else None,
                "b": mk[n - 1] if n > 2 else None,
                "c": pts[n - 1][1] / env["F_B"],
                "IO": (acc["IO"] * sf if "IO" in acc else None),
                "LS": (acc["LS"] * sf if "LS" in acc else None),
                "CP": (acc["CP"] * sf if "CP" in acc else None),
                "k_elastic": env["k_e"],
                "hysteresis": prop["hysteresis"],
                "drop_strength": prop["drop_strength"],
                "material": spec["material"],
                "envelope": {"positive": [list(p) for p in pos["points"]],
                             "negative": [list(p) for p in
                                          env["negative"]["points"]]},
                **({"approximation": _cu.ISOTROPIC_NOTE}
                   if prop["hysteresis"] == "isotropic" else {}),
                **{k: list(v) for k, v in hh.items()}})
        return out
