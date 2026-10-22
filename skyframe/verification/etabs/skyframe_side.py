"""Build a verification definition (kip, ft) in SkyFrame, run it, and return
results in the same shape ``run_etabs.py`` exports from ETABS:

    {"program", "version", "units": "kip-ft",
     "cases": {case: {"joints": [{"xyz": [x, y, z], "u": [UX, UY, UZ, RX, RY, RZ]}],
                      "base": {"FX", "FY", "FZ", "MX", "MY", "MZ"},
                      "frames": {id: [{"station", "P", "V2", "V3", "T", "M2", "M3"}]}}}}

SkyFrame works in kN and m; the conversion is done here, both ways.
"""

from __future__ import annotations

from typing import Dict

FT = 0.3048                     # m
KIP = 4.4482216152605           # kN
KSF = KIP / FT**2               # kPa
KIPFT = KIP * FT                # kN m


def build(case: dict):
    from skyframe.core.model import BuildingModel, FrameSection, Material, MemberLoad, NodalLoad, PointSupport

    m = BuildingModel(name=case["id"])
    m.frame_axis_convention = "etabs"           # ETABS default local axes (cases.py settings)
    m.rigid_diaphragms = False
    m.num_modes = 0
    for mt in case["materials"]:
        m.add_material(Material(mt["name"], E=mt["E"] * KSF, nu=mt["nu"],
                                unit_weight=mt.get("unitWeight", 0.0) * KIP / FT**3))
    for s in case["sections"]:
        sec = FrameSection(s["name"], s["material"], A=s["A"] * FT**2, I33=s["I33"] * FT**4,
                           I22=s["I22"] * FT**4, J=s["J"] * FT**4)
        if s.get("As2") or s.get("As3"):
            sec.As2, sec.As3 = s["As2"] * FT**2, s["As3"] * FT**2
        md = s.get("modifiers") or {}
        sec.mod_A, sec.mod_I33 = md.get("A", 1.0), md.get("I33", 1.0)
        sec.mod_I22, sec.mod_J = md.get("I22", 1.0), md.get("J", 1.0)
        m.add_section(sec)
    m.set_stories([st["height"] * FT for st in case["stories"]])
    for f in case["frames"]:
        kind = "column" if f["i"][:2] == f["j"][:2] else "beam"
        mem = m.add_member(kind, f["section"], tuple(c * FT for c in f["i"]), tuple(c * FT for c in f["j"]),
                           uid=f["id"], angle=f.get("angle", 0.0))
        mem = mem if mem is not None else next(x for x in m.members if x.uid == f["id"])
        if f.get("releases"):
            # springs: translational kip/ft -> kN/m, rotational kip-ft/rad -> kN m/rad
            conv = {"P": KIP / FT, "V2": KIP / FT, "V3": KIP / FT, "T": KIPFT, "M2": KIPFT, "M3": KIPFT}
            mem.end_releases = {end: {d: v * conv[d] for d, v in dofs.items()}
                                for end, dofs in f["releases"].items()}
        if f.get("rigid"):
            r = f["rigid"]
            mem.rigid_i, mem.rigid_j = r.get("i", 0.0) * FT, r.get("j", 0.0) * FT
            mem.rigid_factor = r.get("factor", 1.0)
    for s in case["supports"]:
        m.supports.append(PointSupport(tuple(c * FT for c in s["at"]), tuple(int(r) for r in s["restraint"])))
    for p in case["patterns"]:
        m.pattern(p["name"], "other")
        m.add_case(p["name"], {p["name"]: 1.0})
    for ld in case.get("pointLoads", []):
        f = ld["f"]
        m.pattern(ld["pattern"], "other").nodal_loads.append(NodalLoad(
            tuple(c * FT for c in ld["at"]), fx=f[0] * KIP, fy=f[1] * KIP, fz=f[2] * KIP,
            mx=f[3] * KIPFT, my=f[4] * KIPFT, mz=f[5] * KIPFT))
    for ld in case.get("distLoads", []):
        m.pattern(ld["pattern"], "other").member_loads.append(
            MemberLoad(ld["frame"], "udl", ld["w"] * KIP / FT, direction="gravity"))
    return m


def run(case: dict) -> dict:
    from skyframe import __version__
    from skyframe.engine.opensees_engine import OpenSeesEngine

    d = OpenSeesEngine(build(case)).run().to_dict()
    nodes = {t: [c / FT for c in xyz] for t, xyz in d["nodes"].items()}
    out: Dict[str, dict] = {}
    for cname, c in d["cases"].items():
        joints = [{"xyz": nodes[t], "u": [u[0] / FT, u[1] / FT, u[2] / FT, u[3], u[4], u[5]]}
                  for t, u in c["node_disp"].items()]
        b = c["base"]
        base = {k: b[k] / KIP for k in ("FX", "FY", "FZ")}
        base.update({k: b[k] / KIPFT for k in ("MX", "MY", "MZ")})
        frames = {}
        for uid, st in c.get("member_stations", {}).items():
            rows = []
            for k, x in enumerate(st["x"]):
                rows.append({"station": x / FT, "P": st["N"][k] / KIP, "V2": st["V2"][k] / KIP,
                             "V3": st["V3"][k] / KIP, "T": st["T"][k] / KIPFT,
                             "M2": st["M2"][k] / KIPFT, "M3": st["M3"][k] / KIPFT})
            frames[uid] = rows
        out[cname] = {"joints": joints, "base": base, "frames": frames}
    return {"program": "SkyFrame", "version": __version__, "units": "kip-ft", "cases": out}
