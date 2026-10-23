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
        table (else SapModel.SectCut's Set/Add...Group method), Results.SectionCutAnalysis (else the
        "Section Cut Forces - Analysis" table)
    K4  PropMaterial.SetMaterial / SetOSteel_1 / SetSSCurve (elastic-perfectly-plastic steel),
        PropArea.SetShellLayer_1 (a layered wall section, S22 nonlinear, S11 and S12 linear);
        read-backs: AreaObj.GetProperty per area, PointObj.GetConnectivity of the control and loaded
        joints, StaticNonlinear.SetSolControlParameters / SetResultsSaved, and after the run the
        linear push against the elastic hand sway, the case status, base shear and control
        displacement at every step, and ETABS's .LOG lines when the push shows no response
"""

from __future__ import annotations

import math

TOL = 1e-6
# Layered-shell component behaviour codes of PropArea.SetShellLayer_1 (S11Type, S22Type, S12Type):
# 0 inactive, 1 linear, 2 nonlinear.  The first ETABS run used 0/1/0 meaning "linear, nonlinear,
# linear" and ETABS read it as S11 and S12 INACTIVE, S22 linear: a wall of vertical fibres with no
# horizontal or shear stiffness (1 kip pushed the top 5653 ft; the log: structure unstable).
LAYER_INACTIVE, LAYER_LINEAR, LAYER_NONLINEAR = 0, 1, 2


# ── build ───────────────────────────────────────────────────────────────────
def build(sm, case: dict, R, point, names: dict) -> None:
    for lk in case.get("hookLinks", []):
        R.say(f"  hook link {lk['id']}: {_hook(sm, R, lk)}")
    for name, lw in (case.get("layeredWalls") or {}).items():
        sec = next(a for a in case["areaSections"] if a["name"] == name)
        R.say(f"  layered wall section {name}: {_layered(sm, R, case, sec, lw)}")
        _check_wall_assignments(sm, R, case, name)
    for po in case.get("pushover", []) if case.get("layeredWalls") else []:
        _check_push(sm, R, case, po)
        R.say(f"  pushover {po['name']} solution control: {_solution_control(sm, R, po)}")
    for sc in case.get("sectionCuts", []):
        R.say(f"  section cut {sc['name']}: {_section_cut(sm, R, sc)}")


# ── K4 read-backs: which section the areas carry, what the push acts on ─────
def _check_wall_assignments(sm, R, case: dict, sec: str) -> None:
    """Every area object of the definition must carry the layered section: read back each one's
    property and reassign any that does not (AreaObj.GetProperty / SetProperty)."""
    areas = sm.AreaObj.GetNameList(0, [])
    R.ok(areas, "list areas")
    have = {}
    for an in areas[1]:
        g = sm.AreaObj.GetProperty(an, "")
        R.ok(g, f"property of area {an}")
        have.setdefault(str(g[0]), []).append(an)
    R.say(f"    area objects by section (read back): {{{', '.join(f'{k}: {len(v)}' for k, v in have.items())}}}")
    wrong = [an for k, v in have.items() if k != sec for an in v]
    for an in wrong:
        R.ok(sm.AreaObj.SetProperty(an, sec, 0), f"assign {sec} to area {an}")
    if wrong:
        R.say(f"    reassigned {len(wrong)} area objects to {sec}")
    want = sum(a.get("mesh", [1, 1])[0] * a.get("mesh", [1, 1])[1] for a in case["areas"])
    if len(areas[1]) != want:
        raise R.Stop(f"layered wall: {len(areas[1])} area objects, the definition has {want}")


def _connected(sm, R, pn: str) -> str:
    """What a joint connects to (PointObj.GetConnectivity: object type 2 frame, 3 link?, 5 area)."""
    if not hasattr(sm.PointObj, "GetConnectivity"):
        return f"(no GetConnectivity; PointObj offers {R._methods(sm.PointObj)[:300]})"
    g = sm.PointObj.GetConnectivity(pn, 0, [], [], [])
    if g[-1] != 0:
        return f"(GetConnectivity returned {g[-1]})"
    return f"{g[0]} objects: " + ", ".join(f"type {t} {n}" for t, n in zip(g[1], g[2]))


def _check_push(sm, R, case: dict, po: dict) -> None:
    """The control joint and every joint the push pattern loads must be wall joints."""
    pts = sm.PointObj.GetNameList(0, [])
    R.ok(pts, "list points")
    at = {p: _coords(sm, R, p) for p in pts[1]}
    find = lambda xyz: next((p for p, c in at.items() if math.dist(c, xyz) < TOL), None)
    ctrl = find(po["control"])
    R.say(f"    control joint {ctrl} at {po['control']}: {_connected(sm, R, ctrl) if ctrl else 'NOT FOUND'}")
    loaded = [ld for ld in case.get("pointLoads", []) if ld["pattern"] == po["pattern"]]
    bad, read = [], []
    for ld in loaded:
        pn = find(ld["at"])
        con = _connected(sm, R, pn) if pn else "NOT FOUND"
        if pn is None or con.startswith("0 objects"):
            bad.append(f"{ld['at']}: {con}")
        if hasattr(sm.PointObj, "GetLoadForce") and pn:
            try:
                # [n, PointName, LoadPat, LCStep, CSys, F1, F2, F3, M1, M2, M3, ret]
                g = sm.PointObj.GetLoadForce(pn, 0, [], [], [], [], [], [], [], [], [], [], 0)
                if g[-1] == 0:
                    fx = sum(g[5][k] for k in range(g[0]) if str(g[2][k]) == po["pattern"])
                    read.append(f"{pn} {fx:g}" + ("" if abs(fx - ld["f"][0]) < 1e-9 else f" (set {ld['f'][0]:g})"))
            except Exception as exc:
                read.append(f"{pn}: {exc}")
    R.say(f"    push pattern {po['pattern']}: {len(loaded)} loaded joints"
          + (f"; NOT ON THE WALL: {'; '.join(bad)}" if bad else ", all connected to the wall")
          + (f"; FX read back: {', '.join(read)}" if read else ""))
    if ctrl is None or bad:
        raise R.Stop(f"pushover {po['name']}: control joint {ctrl}, load problems {bad}")


def _solution_control(sm, R, po: dict) -> str:
    """A layered-shell pushover that yields with a flat plateau: more steps and null steps than the
    defaults, Newton iteration without event-to-event stepping, and many saved states."""
    sn, nm, out, tried = sm.LoadCases.StaticNonlinear, po["name"], [], []
    if hasattr(sn, "SetSolControlParameters"):
        # MaxTotalSteps, MaxFailedSubSteps, MaxIterCS, MaxIterNR, TolConvD, UseEventStepping, TolEventD,
        # MaxLineSearchPerIter, TolLineSearch, LineSearchStepFact
        if R._try("SetSolControlParameters", tried, lambda: sn.SetSolControlParameters(
                nm, 1000, 200, 10, 40, 1e-4, False, 0.01, 20, 0.1, 1.618)):
            out.append("1000 steps, 200 failed substeps, 40 Newton iterations, no event stepping")
    if hasattr(sn, "SetResultsSaved"):
        if R._try("SetResultsSaved", tried, lambda: sn.SetResultsSaved(nm, True, int(po["steps"]),
                                                                       2 * int(po["steps"]), True)):
            out.append(f"multiple states saved ({po['steps']} to {2 * int(po['steps'])})")
    if hasattr(sn, "GetSolControlParameters"):
        try:
            g = sn.GetSolControlParameters(nm, 0, 0, 0, 0, 0.0, False, 0.0, 0, 0.0, 0.0)
            out.append(f"read back {list(g[:-1])}")
        except Exception as exc:
            tried.append(f"GetSolControlParameters: {exc}")
    return "; ".join(out + tried) or f"StaticNonlinear offers: {R._methods(sn)}"


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
    # S11Type, S22Type, S12Type: LAYER_INACTIVE / LINEAR / NONLINEAR (see the constants)
    args = (name, 1, ["STEEL"], [0.0], [t], [1], [2], [epp], [0.0], [LAYER_LINEAR], [LAYER_NONLINEAR],
            [LAYER_LINEAR])
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
            if n != 1 or abs(th[0] - t) > 1e-9 or int(s22[0]) != LAYER_NONLINEAR:
                raise R.Stop(f"layered shell {name}: reads back {n} layers, thickness {th}, S22 type {s22}")
            back = (f"; read back: 1 layer, t {th[0]}, type {g[4][0]}, {g[5][0]} integration points, material "
                    f"{g[6][0]}, S11/S22/S12 types {g[8][0]}/{g[9][0]}/{g[10][0]}")
    try:                                         # the wall property's own shell type after layering
        w = pa.GetWall(name, 0, 0, "", 0.0, 0, "", "")
        back += f"; GetWall: wall type {w[0]}, shell type {w[1]} (6 = layered), material {w[2]}, t {w[3]}"
    except Exception as exc:
        back += f"; GetWall: {exc}"
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
    where = f"group {grp} ({len(above)} areas above z = {z}, {len(on_line)} joints on it)"
    api = getattr(sm, "SectCut", None)
    R.say(f"    SapModel.SectCut offers: {R._methods(api)}" if api is not None else "    SapModel has no SectCut")
    keys = R.table_keys(sm)
    cut_tables = [k for k in keys if "section cut" in k.lower()]
    R.say(f"    section-cut tables: {cut_tables} {_fields(sm, cut_tables)}")
    key = next((k for k in cut_tables if k.lower() == "section cut definitions"), "") \
        or R.find_table(sm, "section cut", "definition")
    tried = []
    if key:
        try:
            return f"{where}, {_cut_by_table(sm, R, sc, grp, key)}"
        except R.Stop as exc:
            tried.append(str(exc))
    else:
        tried.append("no 'Section Cut Definitions' table")
    if api is not None:
        got = _cut_by_api(sm, R, api, sc, grp, tried)
        if got:
            return f"{where}, {got}"
    raise R.Stop(f"section cut {sc['name']}: " + " | ".join(tried)
                 + f" | SectCut offers: {R._methods(api) if api is not None else '-'} | tables with 'cut': "
                 + ", ".join(k for k in keys if "cut" in k.lower()))


def _cut_by_api(sm, R, api, sc: dict, grp: str, tried: list) -> str:
    """SapModel.SectCut: its group-based setter, whatever ETABS 23 calls it, tried with the argument
    lists such a call takes (name, group[, result type 0 analysis][, location x y z, rotations])."""
    x, y, z = (float(v) for v in sc["at"])
    nm = sc["name"]
    cands = [m for m in dir(api) if not m.startswith("_") and "group" in m.lower()
             and m.lower().startswith(("set", "add"))]
    arglists = [(nm, grp), (nm, grp, 0), (nm, grp, 0, x, y, z), (nm, grp, 0, x, y, z, 0.0, 0.0, 0.0),
                (nm, grp, True, x, y, z, 0.0, 0.0, 0.0)]
    for m in cands:
        for args in arglists:
            if R._try(f"SectCut.{m}{len(args)}", tried, lambda: getattr(api, m)(*args)):
                listed = ""
                if hasattr(api, "GetNameList"):
                    try:
                        g = api.GetNameList(0, [])
                        listed = f"; SectCut.GetNameList: {list(g[1])}"
                    except Exception as exc:
                        listed = f"; GetNameList: {exc}"
                return f"SectCut.{m}{args}{listed}"
    if not cands:
        tried.append("SectCut has no Set/Add...Group method")
    return ""


def _cut_by_table(sm, R, sc: dict, grp: str, key: str) -> str:
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
        raise R.Stop(f"table {key} refused the row {row}: {exc} | fields: {_fields(sm, [key])}")
    back = sm.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])
    R.ok(back, f"read back table {key}")
    nf = len(back[1])
    rows = [list(back[3][k * nf:(k + 1) * nf]) for k in range(back[2])]
    mine = [r for r in rows if str(r[0]).lower() == sc["name"].lower()]
    if not mine:
        raise R.Stop(f"table {key}: no row {sc['name']} after the edit; rows {rows}")
    return f"table {key}: " + ", ".join(f"{f} = {v}" for f, v in zip(back[1], mine[0]))


# ── results ─────────────────────────────────────────────────────────────────
def results(sm, case: dict, R, names: dict, res: dict) -> None:
    vals = res.setdefault("values", {})
    for key, sp in (case.get("shellPoints") or {}).items():
        vals[key] = _shell_point(sm, R, case, key, sp)
    for sc in case.get("sectionCuts", []):
        vals.update(_cut_forces(sm, R, sc))
    for po in case.get("pushover", []) if case.get("layeredWalls") else []:
        _linear_push(sm, R, case, po)
        _pushover_curve(sm, R, po, res)


def _linear_push(sm, R, case: dict, po: dict) -> None:
    """The push pattern as a linear case: the elastic layered wall's top sway against the hand value
    PH^3/3EI + PH/(5/6 G A) (one steel layer of thickness t; a near-zero or huge sway shows that
    the wall itself, not the nonlinear solution, is wrong)."""
    try:
        lw = next(iter(case["layeredWalls"].values()))
        mat = case["materials"][0]
        E, nu, t = mat["E"], mat["nu"], lw["steel_t"]
        xs = [c[0] for a in case["areas"] for c in a["corners"]]
        zs = [c[2] for a in case["areas"] for c in a["corners"]]
        b, H = max(xs) - min(xs), max(zs) - min(zs)
        P = sum(ld["f"][0] for ld in case["pointLoads"] if ld["pattern"] == po["pattern"])
        hand = P * H**3 / (3 * E * t * b**3 / 12) + P * H / (5 / 6 * E / (2 * (1 + nu)) * t * b)
        _select(sm, R, po["pattern"])
        pts = sm.PointObj.GetNameList(0, [])
        ctrl = next((p for p in pts[1] if math.dist(_coords(sm, R, p), po["control"]) < TOL), None)
        jd = sm.Results.JointDispl(ctrl, 0, 0, [], [], [], [], [], [], [], [], [], [], [])
        R.ok(jd, f"{po['pattern']} displacement at {ctrl}")
        br = sm.Results.BaseReact(0, [], [], [], [], [], [], [], [], [], 0, 0, 0)
        R.ok(br, f"{po['pattern']} base reactions")
        ux = jd[6][0]
        flag = "" if 0.5 < abs(ux) / hand < 2 else (
            f"  WALL STIFFNESS WRONG ({abs(ux) / hand:.3g} x the hand sway): check the layer behaviour codes "
            f"(S11/S22/S12 set {LAYER_LINEAR}/{LAYER_NONLINEAR}/{LAYER_LINEAR}, meant linear/nonlinear/linear)")
        R.say(f"  linear {po['pattern']} ({P:g} kip): top UX {ux:.6g} ft (elastic wall by hand {hand:.6g}), "
              f"base FX {br[4][0]:.6g}{flag}")
    except Exception as exc:
        R.say(f"  linear {po['pattern']} check not read: {exc}")


def _pushover_curve(sm, R, po: dict, res: dict) -> None:
    """Log the pushover as ETABS solved it: the case status, the base shear and the control joint's
    displacement at every saved step, and ETABS's analysis log for the case.  Stored in
    res["pushoverCurve"]; nothing here changes the exported results."""
    nm = po["name"]
    try:
        st = sm.Analyze.GetCaseStatus(0, [], [])
        status = {1: "not run", 2: "could not start", 3: "not finished", 4: "finished"}
        R.say(f"  case {nm} status: " + ", ".join(f"{n} {status.get(s, s)}" for n, s in zip(st[1], st[2])
                                                    if n == nm))
    except Exception as exc:
        R.say(f"  case status not read: {exc}")
    curve = {"step": [], "FX": [], "U1": []}
    try:
        R.ok(sm.Results.Setup.SetOptionNLStatic(2), "nonlinear static results: step by step")
        _select(sm, R, nm)
        br = sm.Results.BaseReact(0, [], [], [], [], [], [], [], [], [], 0, 0, 0)
        R.ok(br, f"base reactions {nm} by step")
        curve["step"] = [float(br[3][k]) for k in range(br[0])]
        curve["FX"] = [float(br[4][k]) for k in range(br[0])]
        pts = sm.PointObj.GetNameList(0, [])
        ctrl = next((p for p in pts[1] if math.dist(_coords(sm, R, p), po["control"]) < TOL), None)
        if ctrl:
            jd = sm.Results.JointDispl(ctrl, 0, 0, [], [], [], [], [], [], [], [], [], [], [])
            R.ok(jd, f"control joint {ctrl} displacement by step")
            curve["U1"] = [float(jd[6][k]) for k in range(jd[0])]
    except Exception as exc:
        R.say(f"  pushover {nm}: step-by-step results not read: {exc}")
    finally:
        try:
            R.ok(sm.Results.Setup.SetOptionNLStatic(3), "nonlinear static results: last step")
        except Exception:
            pass
    res["pushoverCurve"] = {nm: curve}
    n = len(curve["FX"])
    R.say(f"  pushover {nm}: {n} saved steps; step, U1, base FX: "
          + "; ".join(f"{curve['step'][k]:g} {curve['U1'][k] if k < len(curve['U1']) else float('nan'):.4g} "
                      f"{curve['FX'][k]:.5g}" for k in range(n)))
    peak = max((abs(v) for v in curve["FX"]), default=0.0)
    if n <= 1 or peak < 1e-3:
        R.say(f"  pushover {nm}: NO RESPONSE ({n} steps, peak |FX| {peak:.3g}): ETABS's analysis log follows")
        _analysis_log(sm, R, nm)


def _analysis_log(sm, R, nm: str) -> None:
    """The lines of ETABS's <model>.LOG about the case, its warnings and its errors."""
    from pathlib import Path
    try:
        path = Path(str(sm.GetModelFilename(True)))
        log = next((p for p in (path.with_suffix(".LOG"), path.with_suffix(".log")) if p.exists()), None)
        if log is None:
            R.say(f"    no analysis log next to {path}")
            return
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        words = (nm.lower(), "warn", "error", "unstable", "converge", "null", "fail", "terminat", "step")
        hits = [ln.strip() for ln in lines if ln.strip() and any(w in ln.lower() for w in words)]
        for ln in hits[-60:]:
            R.say(f"    LOG {ln}")
    except Exception as exc:
        R.say(f"    analysis log not read: {exc}")


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

