"""SkyFrame side of the links and shells group (cases_x_links.py, K1-K4).

    build(case, m, keep)    hook links, nonlinear layered wall sections
    results(case, d, res)   shell moments at a node, section-cut resultants -> res["values"]

SkyFrame works in kN and m; the definitions are kip and ft (conversions from skyframe_side).
"""

from __future__ import annotations

import math

from skyframe_side import FT, KIP, KIPFT, KSF


def build(case: dict, m, keep) -> None:
    for lk in case.get("hookLinks", []):
        # tension-only along i -> j, engaging once the pair has opened by the slack
        m.add_link(tuple(c * FT for c in lk["i"]), tuple(c * FT for c in lk["j"]), uid=lk["id"],
                   link_type="hook", params={"k": lk["k"] * KIP / FT, "slack": lk["slack"] * FT})
    for name, lw in (case.get("layeredWalls") or {}).items():
        _layered_wall(m, name, lw)


def _layered_wall(m, name: str, lw: dict) -> None:
    """The section's material becomes elastic-perfectly-plastic steel (yield fy) and the section a
    layered shell, symmetric through the thickness so membrane and bending do not couple:

        half of an orthotropic layer  (E1 = E along local 1, E2 ~ 0, G12 = G: horizontal and shear)
        the steel layer at angle 90   (uniaxial along local 2, vertical: EPP, Steel01 with b = 0)
        the other half of the orthotropic layer

    In a pushover the vertical stress is carried by the steel layer alone, so the section's plastic
    moment is fy t b^2 / 4 exactly; linear cases use the elastic section."""
    from skyframe.core.model import Material
    sec = m.shell_sections[name]
    steel = m.materials[sec.material]
    steel.material_type = "steel"
    steel.fy = lw["fy"] * KSF
    steel.stress_strain = {"model": "simple", "hysteresis": "kinematic", "params": {"b": 0.0}}
    t = lw["steel_t"] * FT
    G = steel.E / (2 * (1 + steel.nu))
    hname = f"{sec.material}_H"
    if hname not in m.materials:
        m.add_material(Material(hname, E=steel.E, nu=0.0, unit_weight=0.0, material_type="steel",
                                symmetry="orthotropic",
                                ortho={"E2": steel.E * 1e-6, "G12": G, "G13": G, "G23": G}))
    sec.layered = {"layers": [{"t": t / 2, "material": hname, "kind": "orthotropic"},
                              {"t": t, "material": sec.material, "kind": "steel", "angle": 90.0},
                              {"t": t / 2, "material": hname, "kind": "orthotropic"}]}
    m._validate_layered(sec)


def results(case: dict, d: dict, res: dict) -> None:
    vals = res.setdefault("values", {})
    if case.get("shellPoints"):
        vals.update(_shell_points(case, d))
    for sc in case.get("sectionCuts", []):
        vals.update(_section_cut(case, d, sc))


def _shell_points(case: dict, d: dict) -> dict:
    """A shell moment (local axes, kip-ft/ft) at a mesh node: SkyFrame's contour with nodal
    averaging over every element sharing the node (core.shell_results, ETABS-style display)."""
    from skyframe.core import shell_results as SR
    import skyframe_side as S
    model = S.build(case)
    nodes = {int(t): xyz for t, xyz in d["nodes"].items()}
    out = {}
    for key, sp in case["shellPoints"].items():
        con = SR.contour(model, d, sp["case"], sp["comp"], axes="local", averaging="all")
        at = [c * FT for c in sp["at"]]
        got = []
        for i, q in enumerate(d["shell_quads"]):
            if q.get("region") != sp["area"]:
                continue
            for k, tag in enumerate(q["nodes"]):
                if math.dist(nodes[int(tag)], at) < 1e-6:
                    got.append(con["corners"][i][k])
        if not got:
            raise ValueError(f"{case['id']} {key}: no shell node at {sp['at']}")
        if max(got) - min(got) > 1e-9 * max(1.0, abs(max(got))):
            raise ValueError(f"{case['id']} {key}: the averaged node values disagree: {got}")
        out[key] = got[0] / KIP            # kN m / m -> kip-ft / ft
    return out


def _section_cut(case: dict, d: dict, sc: dict) -> dict:
    """The resultant across a horizontal cut through a wall.  SkyFrame's section cuts integrate
    frame members only (shells are counted and excluded, CONTRACT v0.13), so the wall cut is read
    from its pier kernel: the free body of the nodal forces of the elements above the node line
    at a story bottom, moments about the net-section centre.  The definition puts a story boundary
    at the cut, so the pier at that story is the cut."""
    z, story = 0.0, None
    for st in case["stories"]:
        if abs(z - sc["z"]) < 1e-6:
            story = st["name"]
        z += st["height"]
    if story is None:
        raise ValueError(f"{case['id']} {sc['name']}: the cut at z = {sc['z']} is not a story bottom")
    pier = next(a["pier"] for a in case["areas"] if a["id"] == sc["area"])
    e = d["piers"][sc["case"]][pier][story]
    keys = sc["keys"]
    return {keys["V"]: e["V"] / KIP, keys["P"]: e["P"] / KIP, keys["M"]: e["M"] / KIPFT}
