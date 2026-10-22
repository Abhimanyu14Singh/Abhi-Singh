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
KIPS2FT = KIP / FT              # tonne per kip-s2/ft
G_FT = 9.80665 / FT             # ft/s2


def build(case: dict, only=None):
    """``only``: run just these analysis cases (pattern cases, the modal case, nonlinear cases) --
    the speed benchmark times the linear and nonlinear parts separately."""
    keep = (lambda n: True) if only is None else (lambda n: n in only)
    from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection, Material, MemberLoad, NodalLoad,
                                     NodalMass, PointSupport, ShellSection, SpringSupport, ThermalLoad)

    m = BuildingModel(name=case["id"])
    m.frame_axis_convention = "etabs"           # ETABS default local axes (cases.py settings)
    m.rigid_diaphragms = False
    m.num_modes = case.get("modal", {}).get("modes", 0) if keep(case.get("modal", {}).get("case")) else 0
    for mt in case["materials"]:
        # alpha is per deg F and temperatures are deg F: the product alpha dT is unit-free
        m.add_material(Material(mt["name"], E=mt["E"] * KSF, nu=mt["nu"],
                                unit_weight=mt.get("unitWeight", 0.0) * KIP / FT**3, alpha=mt.get("alpha")))
    for s in case["sections"]:
        sec = FrameSection(s["name"], s["material"], A=s["A"] * FT**2, I33=s["I33"] * FT**4,
                           I22=s["I22"] * FT**4, J=s["J"] * FT**4, b=s["width"] * FT, h=s["depth"] * FT)
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
        if f.get("tcLimit"):
            mem.axial_limit = f["tcLimit"]
        if f.get("cardinal", 10) != 10:
            mem.cardinal_point = int(f["cardinal"])
        if case.get("stationSpacing"):
            mem.output_stations = {"max_spacing": case["stationSpacing"] * FT}
        if f.get("releases"):
            # springs: translational kip/ft -> kN/m, rotational kip-ft/rad -> kN m/rad
            conv = {"P": KIP / FT, "V2": KIP / FT, "V3": KIP / FT, "T": KIPFT, "M2": KIPFT, "M3": KIPFT}
            mem.end_releases = {end: {d: v * conv[d] for d, v in dofs.items()}
                                for end, dofs in f["releases"].items()}
        if f.get("rigid"):
            r = f["rigid"]
            mem.rigid_i, mem.rigid_j = r.get("i", 0.0) * FT, r.get("j", 0.0) * FT
            mem.rigid_factor = r.get("factor", 1.0)
    for a in case.get("areaSections", []):
        sec = ShellSection(a["name"], a["material"], a["thickness"] * FT)
        sec.shell_type = {"thin": "shell_thin", "thick": "shell_thick", "membrane": "shell"}[a["shell"]]
        md = a.get("modifiers") or {}
        for k in ("f11", "f22", "f12", "m11", "m22", "m12", "v13", "v23", "mass", "weight"):
            setattr(sec, k, md.get(k, 1.0))
        m.add_shell_section(sec)
    m.edge_constraints = bool(case.get("edgeConstraints"))
    for a in case.get("areas", []):
        corners = [tuple(c * FT for c in p) for p in a["corners"]]
        kind = "slab" if len({round(p[2], 6) for p in corners}) == 1 else "wall"
        if a.get("toFrame"):
            r = m.add_shell(kind, "membrane", a["section"], corners, uid=a["id"])
            r.distribution = "one_way" if a["toFrame"] == "one" else "two_way"
            r.one_way_dir = 1
        else:
            r = m.add_shell(kind, "shell", a["section"], corners, uid=a["id"])
            n1, n2 = a.get("mesh", [1, 1])
            r.mesh_divisions = {"n1": int(n1), "n2": int(n2)}
        if a.get("pier"):
            r.pier = a["pier"]
    for s in case["supports"]:
        m.supports.append(PointSupport(tuple(c * FT for c in s["at"]), tuple(int(r) for r in s["restraint"])))
    for p in case["patterns"]:
        m.pattern(p["name"], "other").self_weight_factor = p.get("selfWeight", 0.0)
        if keep(p["name"]):
            m.add_case(p["name"], {p["name"]: 1.0})
    for ld in case.get("pointLoads", []):
        f = ld["f"]
        m.pattern(ld["pattern"], "other").nodal_loads.append(NodalLoad(
            tuple(c * FT for c in ld["at"]), fx=f[0] * KIP, fy=f[1] * KIP, fz=f[2] * KIP,
            mx=f[3] * KIPFT, my=f[4] * KIPFT, mz=f[5] * KIPFT))
    for ld in case.get("distLoads", []):
        m.pattern(ld["pattern"], "other").member_loads.append(
            MemberLoad(ld["frame"], "udl", ld["w"] * KIP / FT, direction="gravity"))
    for ld in case.get("frameLoads", []):
        pat = m.pattern(ld["pattern"], "other")
        if ld["type"] == "point":
            pat.member_loads.append(MemberLoad(ld["frame"], "point", ld["P"] * KIP, a=ld["a"], direction="gravity"))
        else:
            pat.member_loads.append(MemberLoad(ld["frame"], "trapezoid", ld["w1"] * KIP / FT, ld["w2"] * KIP / FT,
                                               a=ld["a"], b=ld["b"], direction="gravity"))
    for ld in case.get("tempLoads", []):
        m.pattern(ld["pattern"], "other").thermal_loads.append(ThermalLoad(ld["frame"], dT=ld["dT"]))
    for ld in case.get("areaLoads", []):
        m.pattern(ld["pattern"], "other").area_loads.append(AreaLoad(ld["area"], ld["q"] * KSF))

    for d in case.get("diaphragms", []):
        m.diaphragms[d["name"]] = {"type": "rigid"}
        m.joint_diaphragms += [{"point": [c * FT for c in p], "diaphragm": d["name"]} for p in d["points"]]
    tr, rot = KIP / FT, KIPFT                                # spring kip/ft, kip-ft/rad
    for sp in case.get("springs", []):
        k = sp["k"]
        m.spring_supports.append(SpringSupport(tuple(c * FT for c in sp["at"]),
                                               [k[0] * tr, k[1] * tr, k[2] * tr, k[3] * rot, k[4] * rot, k[5] * rot]))
    for ms in case.get("masses", []):
        q = ms["m"]
        m.nodal_masses.append(NodalMass(tuple(c * FT for c in ms["at"]), q[0] * KIPS2FT, q[1] * KIPS2FT,
                                        q[2] * KIPS2FT, q[3] * KIPFT, q[4] * KIPFT, q[5] * KIPFT))
    if case.get("massSource"):
        src = case["massSource"]
        m.mass_options = dict(m.mass_options, self_mass=bool(src.get("elements")), patterns=bool(src.get("patterns")))
        m.mass_source = dict(src.get("patterns") or {})
        if src.get("elements"):                  # ETABS element self-mass: mass per volume x volume
            m.mass_source_mode = "element_self_mass"
    spectra = {f["name"]: f for f in case.get("spectra", [])}
    for rs in case.get("rsCases", []):
        fn = spectra[rs["function"]]
        # SkyFrame takes Sa in g; the definition's scale turns g into ft/s2
        m.add_rs_case(rs["name"], rs["dir"], fn["points"], combo_method=rs["combine"], damping=rs["damping"],
                      scale=rs["scale"] / G_FT)
    for pc in case.get("pdeltaCases", []):
        m.add_case(pc["name"], pc["loads"], geometric="pdelta")
    geo = {"none": "linear", "pdelta": "pdelta", "large": "corotational"}
    for nc in case.get("nlCases", []):
        # "none": SkyFrame solves any case with tension-only members by Newton iteration
        if not keep(nc["name"]):
            continue
        m.add_case(nc["name"], nc["loads"], geometric=geo[nc["geometry"]])
    for bc in case.get("bucklingCases", []):
        m.add_buckling_case(bc["name"], bc["loads"], num_modes=bc["modes"])
    for cb in case.get("combos", []):
        m.add_combo(cb["name"], cb["cases"], "envelope" if cb["type"] == "envelope" else "add")
    return m


def run(case: dict) -> dict:
    from skyframe import __version__
    from skyframe.engine.opensees_engine import OpenSeesEngine

    d = OpenSeesEngine(build(case)).run().to_dict()
    nodes = {t: [c / FT for c in xyz] for t, xyz in d["nodes"].items()}
    out: Dict[str, dict] = {}
    every = dict(d["cases"])
    every.update(d.get("rs_cases") or {})
    for cname, c in (d.get("combos") or {}).items():
        every[cname] = c
        if c.get("min"):
            every[cname + ":min"] = c["min"]
    for cname, c in every.items():
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
        drifts = [{"story": st, "dir": dr, "drift": v.get("drift_" + dr.lower(), 0.0)}
                  for st, v in (c.get("story") or {}).items() for dr in ("X", "Y")]
        piers = [{"story": st, "name": pn, "location": "bottom", "P": v.get("P", 0.0) / KIP,
                  "V2": v.get("V", 0.0) / KIP, "M3": v.get("M", 0.0) / KIPFT}
                 for pn, by_story in ((d.get("piers") or {}).get(cname) or {}).items() for st, v in by_story.items()]
        out[cname] = {"joints": joints, "base": base, "frames": frames, "drifts": drifts, "piers": piers}
    res = {"program": "SkyFrame", "version": __version__, "units": "kip-ft", "cases": out}
    if case.get("bucklingCases"):
        res["buckling"] = {n: list(b.get("factors", [])) for n, b in (d.get("buckling") or {}).items()}
    if case.get("modal"):
        md = d.get("modal") or {}
        res["modal"] = {"periods": list(md.get("periods", [])),
                        "ratios": [{"UX": p.get("ux", 0.0), "UY": p.get("uy", 0.0), "RZ": p.get("rz", 0.0)}
                                   for p in md.get("participation", [])]}
    return res
