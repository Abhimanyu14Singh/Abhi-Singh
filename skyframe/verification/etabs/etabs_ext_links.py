"""ETABS side of the links and shells group (cases_x_links.py, K1-K4).  The runner fetches this
file next to run_etabs.py and calls:

    build(sm, case, R, point, names)       after the stock build (R = the run_etabs module)
    results(sm, case, R, names, res)       after the stock results: res["values"][key]

ETABS 23's API lacks many setters, so every call is checked with hasattr and, where it fails, the
run stops with R.Stop naming the methods the object offers and the fields of the database tables
that could do the job, so the next run can be fixed from the log.

    K1  PropLink.SetHook + LinkObj.AddByCoord
    K2  Results.AreaForceShell at the centre joint (PointObj.GetElm for its analysis point)
    K3  GroupDef.SetGroup, AreaObj/PointObj.SetGroupAssign, the "Section Cut Definitions" database
        table, Results.SectionCutAnalysis (else the "Section Cut Forces - Analysis" table)
    K4  PropMaterial.SetMaterial / SetOSteel_1 / SetSSCurve (elastic-perfectly-plastic steel),
        PropArea.SetShellLayer_1 (a layered wall section, S22 nonlinear, S11 and S12 linear)
"""

from __future__ import annotations

import math

TOL = 1e-6


# ── build ───────────────────────────────────────────────────────────────────
def build(sm, case: dict, R, point, names: dict) -> None:
    for lk in case.get("hookLinks", []):
        R.say(f"  hook link {lk['id']}: {_hook(sm, R, lk)}")
    for name, lw in (case.get("layeredWalls") or {}).items():
        sec = next(a for a in case["areaSections"] if a["name"] == name)
        R.say(f"  layered wall section {name}: {_layered(sm, R, case, sec, lw)}")
    for sc in case.get("sectionCuts", []):
        R.say(f"  section cut {sc['name']}: {_section_cut(sm, R, sc)}")


def _hook(sm, R, lk: dict) -> str:
    """A hook link: axial (U1) only, tension-only once the pair has opened by the slack."""
    pl = sm.PropLink
    if not hasattr(pl, "SetHook"):
        raise R.Stop(f"hook link: PropLink has no SetHook | PropLink offers: {R._methods(pl)}")
    k, s = float(lk["k"]), float(lk["slack"])
    name = f"HOOK_{lk['id']}"
    dof, fixed = [True] + [False] * 5, [False] * 6
    nonlin = [True] + [False] * 5
    ke, ce, kk, dis = [k] + [0.0] * 5, [0.0] * 6, [k] + [0.0] * 5, [s] + [0.0] * 5
    tried = []
    if not (R._try("PropLink.SetHook", tried, lambda: pl.SetHook(name, dof, fixed, nonlin, ke, ce, kk, dis,
                                                                 0.0, 0.0, "", ""))
            or R._try("PropLink.SetHook (short)", tried, lambda: pl.SetHook(name, dof, fixed, nonlin, ke, ce, kk,
                                                                            dis, 0.0, 0.0))):
        raise R.Stop("hook link property refused: " + "; ".join(tried) + f" | PropLink offers: {R._methods(pl)}")
    back = ""
    if hasattr(pl, "GetHook"):
        try:
            g = pl.GetHook(name, [], [], [], [], [], [], [], 0.0, 0.0, "", "")
            if g[-1] == 0:
                # [DOF, Fixed, Nonlinear, Ke, Ce, k, dis, DJ2, DJ3, Notes, GUID, ret]
                gk, gd = float(g[5][0]), float(g[6][0])
                if abs(gk - k) > 1e-6 * k or abs(gd - s) > 1e-9:
                    raise R.Stop(f"hook link {name}: reads back k {gk}, opening {gd}; set {k}, {s}")
                back = " (read back)"
        except R.Stop:
            raise
        except Exception as exc:                     # reading back is a bonus
            back = f" (not read back: {exc})"
    (xi, yi, zi), (xj, yj, zj) = lk["i"], lk["j"]
    r = None
    for label, call in (("LinkObj.AddByCoord", lambda: sm.LinkObj.AddByCoord(xi, yi, zi, xj, yj, zj, "", False,
                                                                            name, lk["id"], "Global")),
                        ("LinkObj.AddByCoord (short)", lambda: sm.LinkObj.AddByCoord(xi, yi, zi, xj, yj, zj, "",
                                                                                    False, name))):
        try:
            r = call()
            if (r[-1] if isinstance(r, (list, tuple)) else r) == 0:
                break
            tried.append(f"{label} returned {r[-1]}")
            r = None
        except Exception as exc:
            tried.append(f"{label}: {exc}")
    if r is None:
        raise R.Stop("hook link refused: " + "; ".join(tried) + f" | LinkObj offers: {R._methods(sm.LinkObj)}")
    return f"hook k {k}, opening {s}{back}, link {r[0]} from {lk['i']} to {lk['j']}"


def _epp_steel(sm, R, mat: dict, fy: float, name: str) -> str:
    """A new Steel material ``name`` with the definition material's E and nu and an elastic-
    perfectly-plastic stress-strain curve (yield fy, plateau to a strain of 2): a user curve where
    ETABS takes one, else the parametric simple curve with strain hardening pushed beyond any strain
    the run reaches."""
    pm = sm.PropMaterial
    E, nu = float(mat["E"]), float(mat["nu"])
    R.ok(pm.SetMaterial(name, 1), f"material {name} as steel")                 # eMatType.Steel
    R.ok(pm.SetMPIsotropic(name, E, nu, 0.0), f"material {name} E, nu")
    R.ok(pm.SetWeightAndMass(name, 1, 0.0), f"material {name} weight")
    back = pm.GetMPIsotropic(name, 0.0, 0.0, 0.0)
    R.ok(back, f"read back material {name}")
    if abs(back[0] - E) > 1e-9 * E or abs(back[1] - nu) > 1e-12:
        raise R.Stop(f"material {name}: reads back E {back[0]}, nu {back[1]}; set {E}, {nu}")
    tried = []
    ey = fy / E
    if not hasattr(pm, "SetOSteel_1"):
        raise R.Stop(f"material {name}: PropMaterial has no SetOSteel_1 | offers: {R._methods(pm)}")
    # user curve (SSType 0), kinematic hysteresis (1)
    if R._try("SetOSteel_1 (user)", tried, lambda: pm.SetOSteel_1(name, fy, 1.25 * fy, fy, 1.25 * fy, 0, 1,
                                                                  0.02, 0.1, 0.2, -0.1, 0.0)) \
            and hasattr(pm, "SetSSCurve"):
        strain = [-2.0, -ey, 0.0, ey, 2.0]
        stress = [-fy, -fy, 0.0, fy, fy]
        for ids in ([-2, -1, 0, 1, 2], [0, 0, 0, 0, 0]):
            if R._try(f"SetSSCurve ids {ids}", tried, lambda: pm.SetSSCurve(name, 5, ids, strain, stress, 0.0)):
                return f"steel, user stress-strain {list(zip(strain, stress))} (SetOSteel_1 + SetSSCurve)"
    # parametric simple (SSType 1): plateau to a hardening strain of 2, Fu barely above Fy
    if R._try("SetOSteel_1 (parametric)", tried, lambda: pm.SetOSteel_1(name, fy, 1.0001 * fy, fy, 1.0001 * fy, 1, 1,
                                                                        2.0, 2.5, 3.0, -0.1, 0.0)):
        return "steel, parametric simple curve: Fy plateau to a strain of 2 (SetOSteel_1); " + "; ".join(tried)
    raise R.Stop(f"material {name}: no elastic-perfectly-plastic curve accepted: " + "; ".join(tried)
                 + f" | PropMaterial offers: {R._methods(pm)}")


def _layered(sm, R, case: dict, sec: dict, lw: dict) -> str:
    """One steel layer, thickness t, on the wall's mid-surface: S22 (vertical) nonlinear with the
    material's EPP curve, S11 and S12 linear.  The same physics as SkyFrame's vertical EPP steel
    layer plus an elastic horizontal-and-shear layer."""
    mat = next(m for m in case["materials"] if m["name"] == sec["material"])
    epp = f"{mat['name']}_EPP"
    how_mat = _epp_steel(sm, R, mat, float(lw["fy"]), epp)
    pa, name, t = sm.PropArea, sec["name"], float(lw["steel_t"])
    if not hasattr(pa, "SetShellLayer_1"):
        keys = [k for k in R.table_keys(sm) if "layer" in k.lower()]
        raise R.Stop(f"layered shell: PropArea has no SetShellLayer_1 | PropArea offers: {R._methods(pa)} | "
                     f"layer tables: {keys} {_fields(sm, keys)}")
    # Name, NumberLayers, LayerName, Dist, Thickness, MyType (1 shell), NumIntegrationPts, MatProp, MatAng,
    # S11Type, S22Type, S12Type (0 linear, 1 nonlinear, 2 inactive)
    args = (name, 1, ["STEEL"], [0.0], [t], [1], [2], [epp], [0.0], [0], [1], [0])
    tried = []
    done = R._try("PropArea.SetShellLayer_1", tried, lambda: pa.SetShellLayer_1(*args))
    if not done:
        # make it a layered wall first (eShellType 6 = layered), then the layers
        for label, call in (("PropArea.SetWall layered", lambda: pa.SetWall(name, 1, 6, epp, t)),
                            ("PropArea.SetShell_1 layered", lambda: pa.SetShell_1(name, 6, True, epp, 0.0, t, t))):
            if R._try(label, tried, call) and R._try("PropArea.SetShellLayer_1", tried,
                                                     lambda: pa.SetShellLayer_1(*args)):
                done = True
                break
    if not done:
        keys = [k for k in R.table_keys(sm) if "layer" in k.lower()]
        raise R.Stop("layered shell refused: " + "; ".join(tried) + f" | PropArea offers: {R._methods(pa)} | "
                     f"layer tables: {keys} {_fields(sm, keys)}")
    back = ""
    if hasattr(pa, "GetShellLayer_1"):
        g = pa.GetShellLayer_1(name, 0, [], [], [], [], [], [], [], [], [], [])
        if g[-1] == 0:
            # [n, LayerName, Dist, Thickness, MyType, NumIntPts, MatProp, MatAng, S11, S22, S12, ret]
            n, th, s22 = g[0], list(g[3]), list(g[9])
            if n != 1 or abs(th[0] - t) > 1e-9 or int(s22[0]) != 1:
                raise R.Stop(f"layered shell {name}: reads back {n} layers, thickness {th}, S22 type {s22}")
            back = f"; read back: 1 layer, t {th[0]}, S11/S22/S12 types {g[8][0]}/{g[9][0]}/{g[10][0]}"
    return f"material {epp}: {how_mat}; one shell layer t {t}, S22 nonlinear, S11 and S12 linear{back}"


def _fields(sm, keys: list) -> str:
    out = []
    for k in keys[:6]:
        try:
            f = sm.DatabaseTables.GetAllFieldsInTable(k, 0, 0, [], [], [], [], [])
            out.append(f"[{k}: {', '.join(str(x) for x in f[2])}]")
        except Exception as exc:
            out.append(f"[{k}: {exc}]")
    return " ".join(out)


def _coords(sm, R, pn: str):
    c = sm.PointObj.GetCoordCartesian(pn, 0, 0, 0, "Global")
    R.ok(c, f"coordinates of point {pn}")
    return [c[0], c[1], c[2]]


def _section_cut(sm, R, sc: dict) -> str:
    """A section cut by group: the area objects just above the line z and the joints on it (the cut
    force is what those elements exert on those joints).  ETABS 23's API has no section-cut setter,
    so the definition goes into the "Section Cut Definitions" database table."""
    z, grp = float(sc["z"]), f"G_{sc['name']}"
    R.ok(sm.GroupDef.SetGroup(grp), f"group {grp}")
    pts = sm.PointObj.GetNameList(0, [])
    R.ok(pts, "list points")
    on_line = [p for p in pts[1] if abs(_coords(sm, R, p)[2] - z) < TOL]
    for p in on_line:
        R.ok(sm.PointObj.SetGroupAssign(p, grp, False, 0), f"group {grp}: point {p}")
    areas = sm.AreaObj.GetNameList(0, [])
    R.ok(areas, "list areas")
    above = []
    for an in areas[1]:
        ap = sm.AreaObj.GetPoints(an, 0, [])
        R.ok(ap, f"points of area {an}")
        zs = [_coords(sm, R, p)[2] for p in ap[1][:ap[0]]]
        if abs(min(zs) - z) < TOL and max(zs) > z + TOL:
            above.append(an)
            R.ok(sm.AreaObj.SetGroupAssign(an, grp, False, 0), f"group {grp}: area {an}")
    if not on_line or not above:
        raise R.Stop(f"section cut {sc['name']}: {len(on_line)} joints on z = {z}, {len(above)} areas above it")
    key = R.find_table(sm, "section cut", "definition")
    if not key:
        raise R.Stop(f"section cut: no 'Section Cut Definitions' table and no API (SapModel offers: "
                     f"{R._methods(sm)}); tables: {', '.join(R.table_keys(sm))[:1500]}")
    got = sm.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])
    R.ok(got, f"open table {key}")
    fields = [str(f) for f in got[1]]
    R.say(f"    table {key} fields: {', '.join(fields)}")
    x, y, zc = sc["at"]
    row = {}
    for f in fields:
        fl = f.lower().replace(" ", "")
        if fl == "name":
            row[f.lower()] = sc["name"]
        elif "definedby" in fl:
            row[f.lower()] = "Group"
        elif fl in ("group", "groupname"):
            row[f.lower()] = grp
        elif "resulttype" in fl:
            row[f.lower()] = "Analysis"
        elif "default" in fl:
            row[f.lower()] = "No"                    # moments about the point given below
        elif fl.endswith("x") and ("loc" in fl or "global" in fl or "coord" in fl or fl == "x"):
            row[f.lower()] = x
        elif fl.endswith("y") and ("loc" in fl or "global" in fl or "coord" in fl or fl == "y"):
            row[f.lower()] = y
        elif fl.endswith("z") and ("loc" in fl or "global" in fl or "coord" in fl or fl == "z") \
                and "rot" not in fl:
            row[f.lower()] = zc
        elif "rot" in fl or "angle" in fl:
            row[f.lower()] = 0.0
    try:
        R._edit_table(sm, key, [row])
    except Exception as exc:
        raise R.Stop(f"section cut {sc['name']}: table {key} refused the row {row}: {exc} | fields: "
                     f"{_fields(sm, [key])}")
    return (f"group {grp} ({len(above)} areas above z = {z}, {len(on_line)} joints on it), table {key}: "
            + ", ".join(f"{k} = {v}" for k, v in row.items()))


# ── results ─────────────────────────────────────────────────────────────────
def results(sm, case: dict, R, names: dict, res: dict) -> None:
    vals = res.setdefault("values", {})
    for key, sp in (case.get("shellPoints") or {}).items():
        vals[key] = _shell_point(sm, R, case, key, sp)
    for sc in case.get("sectionCuts", []):
        vals.update(_cut_forces(sm, R, sc))


def _select(sm, R, cname: str) -> None:
    R.ok(sm.Results.Setup.DeselectAllCasesAndCombosForOutput(), "deselect output")
    R.ok(sm.Results.Setup.SetCaseSelectedForOutput(cname), f"select {cname}")


def _shell_point(sm, R, case: dict, key: str, sp: dict) -> float:
    """M11 or M22 (local axes, per unit length) at a joint: the mean of the values every element
    sharing the joint reports there (ETABS's AreaForceShell gives element forces at element joints)."""
    _select(sm, R, sp["case"])
    # the joint object at the point (looked up: the model is locked after the run) and its analysis point
    pts = sm.PointObj.GetNameList(0, [])
    R.ok(pts, "list points")
    pobj = next((p for p in pts[1] if math.dist(_coords(sm, R, p), sp["at"]) < TOL), None)
    if pobj is None:
        raise R.Stop(f"{key}: no joint at {sp['at']}")
    pelm = pobj
    if hasattr(sm.PointObj, "GetElm"):
        try:
            g = sm.PointObj.GetElm(pobj, "")
            if g[-1] == 0 and g[0]:
                pelm = g[0]
        except Exception:
            pass
    f = sm.Results.AreaForceShell("All", 2, 0, *([[]] * 23))
    R.ok(f, f"shell forces {sp['case']}")
    col = {"M11": 14, "M22": 15}[sp["comp"]]
    n = f[0]
    rows = [f[col][k] for k in range(n) if str(f[3][k]) in (pelm, pobj) and str(f[4][k]) == sp["case"]]
    if not rows:
        sample = sorted({str(f[3][k]) for k in range(min(n, 200))})[:20]
        raise R.Stop(f"{key}: no AreaForceShell rows at joint {pobj} (analysis point {pelm}) for {sp['case']}; "
                     f"{n} rows, point names like {sample}")
    v = sum(rows) / len(rows)
    R.say(f"  {key}: {sp['comp']} at joint {pobj} = {v:.6g} (mean of {len(rows)} element values: "
          f"{', '.join(f'{x:.6g}' for x in rows)})")
    return v


def _cut_forces(sm, R, sc: dict) -> dict:
    """The cut's F1..M3 in the load case: Results.SectionCutAnalysis, else the analysis section-cut
    forces table.  V = |(F1, F2)|, P = F3, M = |(M1, M2)| (a horizontal cut, global axes)."""
    _select(sm, R, sc["case"])
    row, tried = None, []
    if hasattr(sm.Results, "SectionCutAnalysis"):
        try:
            g = sm.Results.SectionCutAnalysis(0, [], [], [], [], [], [], [], [], [], [])
            R.ok(g, f"section cut forces {sc['case']}")
            # [n, SCut, LoadCase, StepType, StepNum, F1, F2, F3, M1, M2, M3, ret]
            for k in range(g[0]):
                if str(g[1][k]).lower() == sc["name"].lower() and str(g[2][k]) == sc["case"]:
                    row = [g[c][k] for c in range(5, 11)]
            if row is None:
                tried.append(f"SectionCutAnalysis: no row {sc['name']} / {sc['case']} in {list(g[1])[:10]}")
        except Exception as exc:
            tried.append(f"SectionCutAnalysis: {exc}")
    else:
        tried.append("Results has no SectionCutAnalysis")
    if row is None:
        row = _cut_table(sm, R, sc, tried)
    F1, F2, F3, M1, M2, M3 = row
    R.say(f"  section cut {sc['name']} {sc['case']}: F1 {F1:.6g}, F2 {F2:.6g}, F3 {F3:.6g}, "
          f"M1 {M1:.6g}, M2 {M2:.6g}, M3 {M3:.6g}")
    k = sc["keys"]
    return {k["V"]: math.hypot(F1, F2), k["P"]: F3, k["M"]: math.hypot(M1, M2)}


def _cut_table(sm, R, sc: dict, tried: list) -> list:
    key = R.find_table(sm, "section cut", "forces", "analysis") or R.find_table(sm, "section cut", "forces")
    if not key:
        raise R.Stop("section cut forces: " + "; ".join(tried) + " and no section-cut forces table; "
                     f"Results offers: {R._methods(sm.Results)}")
    dt = sm.DatabaseTables
    try:
        if hasattr(dt, "SetLoadCasesSelectedForDisplay"):
            dt.SetLoadCasesSelectedForDisplay([sc["case"]])
        g = dt.GetTableForDisplayArray(key, [], "All", 0, [], 0, [])
        R.ok(g, f"read table {key}")
        fields, n, data = [str(x).lower() for x in g[2]], g[3], list(g[4])
        nf = len(fields)
        col = lambda *ws: next(i for i, f in enumerate(fields) if f in ws)
        cn, cc = col("sectioncut", "name"), col("outputcase", "loadcase", "case")
        cf = [col(c) for c in ("f1", "f2", "f3", "m1", "m2", "m3")]
        for r in range(n):
            rec = data[r * nf:(r + 1) * nf]
            if rec[cn].lower() == sc["name"].lower() and rec[cc] == sc["case"]:
                return [float(rec[c]) for c in cf]
        tried.append(f"table {key}: no row {sc['name']} / {sc['case']} (fields {fields})")
    except R.Stop:
        raise
    except Exception as exc:
        tried.append(f"table {key}: {exc}")
    raise R.Stop("section cut forces: " + "; ".join(tried) + f" | Results offers: {R._methods(sm.Results)}")

