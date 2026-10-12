"""Wall fiber hinges (P-M3 per pier per story) — OpenSees side (Wave B).

In a HINGED build every planar rectangular shell wall of a pier listed in
``model.wall_hinges`` becomes a "wide column" per story it spans:

* its shell quads stay (node connectivity, loads) but get a GHOST
  ``ElasticMembranePlateSection`` with ``WALL_GHOST_RATIO`` x E — the wall
  stiffness is carried by the column below;
* per story: a ``forceBeamColumn`` from the bottom to the top CENTRE node
  of the wall, ``HingeRadau`` with the wall FIBER section at both ends
  (hinge length ``lp = lp_ratio * lw`` capped at half the story height)
  and an elastic interior (gross E, A = t lw, I = t lw^3 / 12), both
  aggregated with an elastic shear spring G (5/6) t lw on the in-plane
  shear; local y runs along the wall length, so in-plane bending is the
  element Mz (M3);
* rigid arms: stiff ``elasticBeamColumn`` elements (``WALL_ARM_FACTOR`` x
  the wall section) from each centre node to every wall mesh node on that
  level line (elements, not MP constraints, so diaphragm slaves never
  chain constraints).

Fiber section: Concrete01 (fpc = fc, eps0 = 2 fc / Ec, 0.2 fc at 0.006, no
tension) over ``n_fibers`` x 2 cells; vertical bars Steel01 (fy, 200 GPa,
b = 0.01): the distributed ``rho_v t lw`` as ``n_fibers`` bars plus
``rho_b t lb`` at each boundary centre.  Recording piggy-backs on the B10
user-hinge Recorder (``asm.user_hinges`` entries with kind "wall").
"""

from __future__ import annotations

import math
import warnings
from typing import Dict, List

import openseespy.opensees as ops

from skyframe.core import wall_hinges as _cwh
from skyframe.engine.interaction_hinges import next_sec_tag

WALL_GHOST_RATIO = 1.0e-4
WALL_ARM_FACTOR = 100.0
_INTEG_TAG0 = 300000
_TOL = 1.0e-6


def plan(eng, hinge_case) -> Dict[str, dict]:
    """region uid -> {label, def, geo, levels} ({} unless used)."""
    model = eng.model
    wh = getattr(model, "wall_hinges", None) or {}
    if hinge_case is None or not wh:
        return {}
    out: Dict[str, dict] = {}
    elev = {s.name: s.elevation for s in model.stories}
    for label, d in wh.items():
        dn = _cwh.normalize(label, d)
        regs = _cwh.wall_regions(model, label)
        if not regs:
            warnings.warn(f"wall hinge {label!r}: no shell wall carries "
                          "this pier label", UserWarning)
        for r in regs:
            geo = _cwh.geometry(r)
            if geo is None:
                warnings.warn(f"wall hinge {label!r}: wall {r.uid!r} is not "
                              "a planar rectangle without openings; left "
                              "as a shell", UserWarning)
                continue
            levels = sorted({geo["z0"], geo["z1"]} | {
                z for z in elev.values()
                if geo["z0"] + _TOL < z < geo["z1"] - _TOL})
            out[r.uid] = {"label": label, "def": dn, "geo": geo,
                          "levels": levels, "region": r}
    return out


def quad_section(eng, asm, wp: Dict[str, dict], region, stag_q: int,
                 cache: dict) -> int:
    """Section tag for a quad of ``region``: the ghost section for a
    hinged wall, else ``stag_q`` unchanged."""
    if region.uid not in wp:
        return stag_q
    key = region.section
    if key not in cache:
        ssec = eng.model.shell_sections[region.section]
        mat = eng.model.materials[ssec.material]
        tag = next_sec_tag(asm)
        ops.section("ElasticMembranePlateSection", tag,
                    mat.E * ssec.mod * WALL_GHOST_RATIO, mat.nu,
                    ssec.total_thickness, 0.0)
        cache[key] = tag
    return cache[key]


def _fiber_section(tag: int, lw: float, t: float, fc: float, Ec: float,
                   G: float, wd: dict, mtag: int) -> int:
    mtag += 1
    ops.uniaxialMaterial("Concrete01", mtag, -fc, -2.0 * fc / Ec,
                         -0.2 * fc, -_cwh.EPS_CU)
    cm = mtag
    mtag += 1
    ops.uniaxialMaterial("Steel01", mtag, wd["fy"], _cwh.ES, _cwh.STEEL_B)
    sm = mtag
    J = lw * t ** 3 / 3.0
    ops.section("Fiber", tag, "-GJ", G * J)
    ops.patch("rect", cm, wd["n_fibers"], 2, -lw / 2.0, -t / 2.0,
              lw / 2.0, t / 2.0)
    for y, A in _cwh.section_layout(lw, t, wd):
        ops.fiber(y, 0.0, A, sm)
    return mtag


def build(eng, asm, wp: Dict[str, dict], tag: int, mtag: int, etag: int,
          transf_name: str):
    """Wide-column elements + rigid arms; returns (tag, mtag, etag)."""
    if not wp:
        return tag, mtag, etag
    model = eng.model
    mesh = asm.mesh
    tag = max([tag] + [int(t) for t in ops.getNodeTags()])
    integ = getattr(asm, "_wh_integ", _INTEG_TAG0)
    story_at = {round(s.elevation, 6): s.name for s in model.stories}
    for ruid, w in wp.items():
        r, geo, wd = w["region"], w["geo"], w["def"]
        ssec = model.shell_sections[r.section]
        mat = model.materials[ssec.material]
        t = ssec.total_thickness
        lw = geo["lw"]
        E, G = mat.E, mat.G
        fc = wd["fc"] or getattr(mat, "fc", None)
        if not fc:
            from skyframe.design.wall import fc_from_E
            fc = fc_from_E(E)
        nodes = {n for q in mesh.quads if q.region == ruid for n in q.nodes}
        cx = geo["x0"][0] + geo["u"][0] * lw / 2.0
        cy = geo["x0"][1] + geo["u"][1] * lw / 2.0
        centre: Dict[float, int] = {}
        Aa = WALL_ARM_FACTOR * t * lw
        Ia = WALL_ARM_FACTOR * t * lw ** 3 / 12.0
        for z in w["levels"]:
            on = [n for n in nodes if abs(mesh.points[n][2] - z) < 1e-6]
            if not on:
                continue
            mid = [n for n in on if math.hypot(mesh.points[n][0] - cx,
                                               mesh.points[n][1] - cy)
                   < 1e-6]
            if mid:                       # a mesh node sits at the centre
                c_tag = mid[0] + 1
            else:
                tag += 1
                ops.node(tag, cx, cy, z)
                c_tag = tag
            centre[z] = c_tag
            # rigid arms centre -> level nodes (stiff elastic beams)
            for n in on:
                if n + 1 == c_tag:
                    continue
                etag += 1
                ops.geomTransf("Linear", etag, 0.0, 0.0, 1.0)
                ops.element("elasticBeamColumn", etag, c_tag, n + 1, Aa, E,
                            G, Ia, Ia, Ia, etag)
        zs = sorted(centre)
        for k, (zb, zt) in enumerate(zip(zs, zs[1:])):
            sname = story_at.get(round(zt, 6), "")
            if wd["stories"] is not None and sname not in wd["stories"]:
                # not hinged at this story: elastic wide column instead
                fiber = False
            else:
                fiber = True
            H = zt - zb
            lp = min(wd["lp_ratio"] * lw, 0.5 * H)
            Av = 5.0 / 6.0 * t * lw
            mtag += 1
            ops.uniaxialMaterial("Elastic", mtag, G * Av)
            vmat = mtag
            esec = next_sec_tag(asm)
            ops.section("Elastic", esec, E, t * lw, t * lw ** 3 / 12.0,
                        lw * t ** 3 / 12.0, G, lw * t ** 3 / 3.0)
            eagg = next_sec_tag(asm)
            ops.section("Aggregator", eagg, vmat, "Vy", "-section", esec)
            etag += 1
            n_x = (geo["n"][0], geo["n"][1], 0.0)
            ops.geomTransf(transf_name if transf_name else "Linear", etag,
                           *n_x)
            integ += 1
            if fiber:
                fsec = next_sec_tag(asm)
                mtag = _fiber_section(fsec, lw, t, fc, E, G, wd, mtag)
                fagg = next_sec_tag(asm)
                ops.section("Aggregator", fagg, vmat, "Vy", "-section", fsec)
                ops.beamIntegration("HingeRadau", integ, fagg, lp, fagg, lp,
                                    eagg)
            else:
                ops.beamIntegration("Lobatto", integ, eagg, 3)
            ops.element("forceBeamColumn", etag, centre[zb], centre[zt],
                        etag, integ)
            if not fiber:
                continue
            fy = wd["fy"]
            bars = _cwh.section_layout(lw, t, wd)
            asm.user_hinges.append({
                "uid": f"{w['label']}@{sname or k}", "end": "i",
                "relative_distance": 0.0, "property": w["label"],
                "kind": "wall", "ele": etag, "prop": {"type": "P-M3"},
                "env": {"pier": w["label"], "story": sname, "region": ruid,
                        "lw": lw, "t": t, "fc": fc, "Ec": E, "fy": fy,
                        "lp": lp, "H": H, "bars": bars,
                        "kappa_y": _cwh.yield_curvature(fy, lw),
                        "As": sum(a for _, a in bars),
                        "confined": wd["confined"]}})
    asm._wh_integ = integ
    return tag, mtag, etag


def new_hist() -> dict:
    return {"rot": [], "moment": [], "rot_plastic": [], "curvature": [],
            "axial": [], "shear": [], "hinge_state": [], "state": []}


def record(spec: dict, hh: dict, peak, yielded) -> None:
    env = spec["env"]
    e = spec["ele"]
    bd = ops.eleResponse(e, "basicDeformation")
    bf = ops.eleResponse(e, "basicForce")
    sd = ops.eleResponse(e, "section", 1, "deformation")
    rot = float(bd[1]) if len(bd) > 1 else 0.0
    N = float(bf[0]) if bf else 0.0
    Mi = float(bf[1]) if len(bf) > 1 else 0.0
    Mj = float(bf[2]) if len(bf) > 2 else 0.0
    kz = float(sd[1]) if len(sd) > 1 else 0.0
    hh["rot"].append(rot)
    hh["moment"].append(Mi)
    hh["curvature"].append(kz)
    hh["axial"].append(-N)                     # compression positive
    hh["shear"].append(abs(Mi + Mj) / env["H"])
    hh["rot_plastic"].append(max(0.0, abs(kz) - env["kappa_y"])
                             * env["lp"])
    if abs(rot) > peak.get(spec["uid"], 0.0):
        peak[spec["uid"]] = abs(rot)
    if yielded is not None and abs(kz) > env["kappa_y"]:
        yielded.add(spec["uid"])


def detail(spec: dict, hh: dict) -> dict:
    """Classify on the Table 10-19 row from the gravity axial force (step
    0) and the peak shear; states use the curvature-based plastic
    rotation (as the v0.21 fiber hinges)."""
    from skyframe.core import user_hinges as _cu
    env = spec["env"]
    fc = env["fc"]
    t, lw = env["t"], env["lw"]
    P0 = hh["axial"][0] if hh["axial"] else 0.0
    Vmax = max(hh["shear"], default=0.0)
    axial_ratio = P0 / (t * lw * fc)                  # As = As' (sym.)
    sq_psi = math.sqrt(fc / 6.894757)                  # sqrt(fc' psi)
    shear_psi = (Vmax / (t * lw) / 6.894757) / sq_psi if sq_psi else 0.0
    row = _cwh.table_10_19(axial_ratio, shear_psi, env["confined"])
    states = []
    for tp in hh["rot_plastic"]:
        if tp <= 0.0:
            st = "A-B"
        elif tp <= row["IO"]:
            st = "B-IO"
        elif tp <= row["LS"]:
            st = "IO-LS"
        elif tp <= row["CP"]:
            st = "LS-CP"
        elif tp <= row["a"]:
            st = ">CP"
        elif tp <= row["b"]:
            st = "C-D"
        else:
            st = ">E"
        states.append(st)
    My = _cwh.moment_curvature(lw, t, fc, env["Ec"], env["fy"],
                               env["bars"], P0, [env["kappa_y"]],
                               strips=400)[0]
    return {"uid": spec["uid"], "end": "i", "relative_distance": 0.0,
            "property": env["pier"], "type": "P-M3", "user": True,
            "kind": "wall", "pier": env["pier"], "story": env["story"],
            "region": env["region"], "fiber": True, "lp": env["lp"],
            "kappa_y": env["kappa_y"], "My": abs(My), "thy": None,
            "a": row["a"], "b": row["b"], "c": row["c"], "IO": row["IO"],
            "LS": row["LS"], "CP": row["CP"], "axial_ratio": axial_ratio,
            "shear_stress_psi": shear_psi, "table": "ASCE 41-17 Table 10-19",
            "preliminary": True,
            **{k: list(v) for k, v in hh.items()},
            "hinge_state": states,
            "state": [_cu.LEGACY_STATE[s] for s in states]}
