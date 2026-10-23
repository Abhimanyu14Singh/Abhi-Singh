"""ETABS side of the time-dependent group (cases_x_timedep.py).  Runs on the ETABS machine, imported
by run_etabs.py; ``R`` is the run_etabs module.

C1, C2  the concrete material gets ACI 209R-92 time-dependent properties and a one-stage staged
        construction case adds the structure at the given age, loads it, and holds it for the stage
        duration with time-dependent material properties on.
C3      N6's elastic-perfectly-plastic M3 column hinges.  ETABS 23's API has no FrameObj.SetHingeAssigns
        and no PropHinge, so the hinge tables are found at run time (every table key with "hinge"),
        their fields read (GetAllFieldsInTable) and a user hinge property and its assignments written
        through them; then the stock pushover case is defined.

ETABS 23's API sets neither of these directly, so the table keys and field names are discovered,
not assumed.  Every table that is tried is described in the log (key, import type, fields), and
when nothing works the run stops with what was offered and what ETABS answered.
"""

from __future__ import annotations

import math

MAT_CONCRETE = 2                 # eMatType.Concrete
HINGE = "EPP_M3"                 # the user hinge property C3 defines


# ── small table helpers (never raise; return what happened) ─────────────────
def _norm(s) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum() or ch == "/")


def _import_types(sm) -> dict:
    """{table key: ImportType} (0 not importable, 1 not interactively, 2 unlocked model, 3 always)."""
    try:
        t = sm.DatabaseTables.GetAvailableTables(0, [], [], [])
        return {str(k): int(v) for k, v in zip(t[1], t[3])}
    except Exception:
        return {}


def _fields(sm, key: str):
    """(field keys, importable flags) of a table, from GetAllFieldsInTable."""
    try:
        f = sm.DatabaseTables.GetAllFieldsInTable(key, 0, 0, [], [], [], [], [])
        keys = [str(x) for x in f[2]]
        imp = [bool(x) for x in f[6]] if len(f) > 7 else [True] * len(keys)
        return keys, imp
    except Exception as exc:
        return [f"(fields not read: {exc})"], []


def describe(sm, R, keys, types=None) -> list:
    """One line per table: key, import type, fields (importable ones marked *)."""
    types = types if types is not None else _import_types(sm)
    out = []
    for k in keys:
        fk, imp = _fields(sm, k)
        fl = ", ".join(f + ("*" if i else "") for f, i in zip(fk, imp)) if imp else ", ".join(fk)
        out.append(f"[{k}] import type {types.get(k, '?')}; fields (* importable): {fl}")
    for line in out:
        R.say("    " + line)
    return out


def _open(sm, key: str):
    got = sm.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])
    code = got[-1] if isinstance(got, (list, tuple)) else got
    if code != 0:
        return None, f"GetTableForEditingArray({key!r}) returned {code}"
    return (got[0], [str(f) for f in got[1]], int(got[2]), [str(v) for v in got[3]]), ""


def _write(sm, key: str, version, fields, n, data) -> str:
    """Set and apply an edited table; '' when ETABS took it, else what it said."""
    dt = sm.DatabaseTables
    try:
        r = dt.SetTableForEditingArray(key, version, fields, n, data)
        code = r[-1] if isinstance(r, (list, tuple)) else r
        if code != 0:
            err = f"SetTableForEditingArray returned {code}"
        else:
            r = dt.ApplyEditedTables(True, 0, 0, 0, 0, "")
            # out arguments: fatal errors, errors, warnings, info messages, import log
            err = "" if (r[-1] == 0 and not r[0] and not r[1]) else (
                f"ApplyEditedTables returned {r[-1]}: {r[0]} fatal errors, {r[1]} errors, {r[2]} warnings: "
                f"{str(r[4])[:600]}")
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    if err:
        try:
            dt.CancelTableEditing()                    # leave no half-edited table behind
        except Exception:
            pass
    return err


def _value_for(field: str, rules):
    """The first rule whose words all occur in the field (normalised) and whose 'not' words do not."""
    f = _norm(field)

    def has(w):                                        # "=w" exact, "w$" ends with, else contains
        return f == w[1:] if w.startswith("=") else f.endswith(w[:-1]) if w.endswith("$") else w in f
    for words, value, *nots in rules:
        if all(has(w) for w in words) and not any(n in f for n in (nots[0] if nots else ())):
            return value
    return None


def upsert(sm, key: str, rows: list, rules_of, name_words=("name", "material", "label", "unique")) -> tuple:
    """Write ``rows`` (one rules list each, see _value_for) into table ``key``: a row whose identifying
    field already holds the row's name is changed in place, else a row is appended (unmatched fields
    left at the table's value or blank).  Returns (error or '', what was written)."""
    opened, err = _open(sm, key)
    if opened is None:
        return err, ""
    version, fields, n, data = opened
    nf = len(fields)
    wrote = []
    for rules in rules_of(rows):
        vals = {i: _value_for(f, rules) for i, f in enumerate(fields)}
        idx = next((i for i, f in enumerate(fields) if any(w in _norm(f) for w in name_words)
                    and vals.get(i) is not None), None)
        target = None
        if idx is not None:
            target = next((k for k in range(n) if data[k * nf + idx].lower() == str(vals[idx]).lower()), None)
        if target is None:
            data += [""] * nf
            target, n = n, n + 1
        for i, v in vals.items():
            if v is not None:
                data[target * nf + i] = str(v)
        wrote.append("{" + ", ".join(f"{fields[i]}={v}" for i, v in vals.items() if v is not None) + "}")
    return _write(sm, key, version, fields, n, data), "; ".join(wrote)


def _row_of(sm, key: str, name: str) -> str:
    opened, err = _open(sm, key)
    if opened is None:
        return err
    version, fields, n, data = opened
    nf = len(fields)
    rows = [data[k * nf:(k + 1) * nf] for k in range(n) if name.lower() in [v.lower() for v in data[k * nf:(k + 1) * nf]]]
    return "; ".join("{" + ", ".join(f"{f}={v}" for f, v in zip(fields, r) if v != "") + "}" for r in rows) or "(no row)"


def _api_names(obj, *words) -> list:
    try:
        return [n for n in dir(obj) if not n.startswith("_") and any(w.lower() in n.lower() for w in words)]
    except Exception:
        return []


# ── C1, C2: time-dependent material ─────────────────────────────────────────
def set_concrete(sm, R, mt: dict) -> None:
    """Make the material a Concrete type (time-dependent properties are a concrete feature) with the
    definition's E and nu; read back."""
    nm = mt["name"]
    R.ok(sm.PropMaterial.SetMaterial(nm, MAT_CONCRETE), f"material {nm} as concrete")
    R.ok(sm.PropMaterial.SetMPIsotropic(nm, mt["E"], mt["nu"], float(mt.get("alpha", 0.0))), f"material {nm} E, nu")
    R.ok(sm.PropMaterial.SetWeightAndMass(nm, 1, mt.get("unitWeight", 0.0)), f"material {nm} weight")
    back = sm.PropMaterial.GetMPIsotropic(nm, 0.0, 0.0, 0.0, 0.0)
    R.ok(back, f"read back material {nm}")
    if abs(back[0] - mt["E"]) > 1e-6 * mt["E"] or abs(back[1] - mt["nu"]) > 1e-9:
        raise R.Stop(f"material {nm}: E, nu read back {back[0]}, {back[1]}; set {mt['E']}, {mt['nu']}")
    R.say(f"  material {nm}: concrete, E {back[0]}, nu {back[1]} (read back)")


def _td_rules(td: dict, law_name: str, yes="Yes", no="No"):
    yn = lambda b: yes if b else no
    vs = float(td["vs"])
    return [
        # ("shrinkage" itself contains "age": the start of drying is named by start / drying / ts)
        (("shrink", "start"), float(td["shrinkStart"])), (("drying",), float(td["shrinkStart"])),
        (("shrink", "time"), float(td["shrinkStart"]), ("consider", "include")), (("=ts",), float(td["shrinkStart"])),
        (("material",), td["material"]), (("name",), td["material"]),
        (("humid",), float(td["rh"])), (("=rh",), float(td["rh"])),
        (("notional",), 2.0 * vs), (("v/s",), vs), (("vs",), vs, ("vsfactor",)), (("volume",), vs),
        (("cement",), "Type " + td["cement"], ("content", "factor")),
        (("curing",), td["curing"].capitalize()), (("cure", "type"), td["curing"].capitalize()),
        (("creep", "type"), "Full Integration"), (("creep", "method"), "Full Integration"),
        (("creep", "analysis"), "Full Integration"),
        (("creep",), yn(td["creep"])), (("shrink",), yn(td["shrinkage"])),
        (("aging",), yn(td["aging"])), (("age",), yn(td["aging"]), ("percent", "average", "start", "load")),
        (("strength",), yn(td["aging"])),
        (("type",), law_name), (("model",), law_name), (("code",), law_name),
    ]


LAW_NAMES = {"ACI 209R-92": ("ACI 209R-92", "ACI209R-92", "ACI 209R-1992", "ACI 209")}


def set_time_dependent(sm, R, td: dict) -> str:
    """ACI 209R-92 creep / shrinkage on the material: an API call where one exists (none known in
    ETABS 23), else the material time-dependent tables."""
    tried = []
    api = _api_names(sm.PropMaterial, "time", "creep", "shrink")
    R.say(f"  PropMaterial time-dependent methods: {api or 'none'}")
    types = _import_types(sm)
    keys = [k for k in (types or {k: 0 for k in R.table_keys(sm)})
            if "material" in k.lower() and any(w in k.lower() for w in ("time", "creep", "shrink"))]
    if not keys:
        raise R.Stop("time-dependent material: ETABS 23's API has no setter (PropMaterial offers "
                     f"{api or 'no time/creep/shrink methods'}) and no material table mentions time, creep or "
                     "shrinkage; material tables offered: "
                     + "; ".join(k for k in (types or R.table_keys(sm)) if "material" in k.lower()))
    R.say(f"  time-dependent material tables: {keys}")
    described = describe(sm, R, keys, types)
    # a general table (no code name in its key) first, then the ACI 209 one
    keys.sort(key=lambda k: ("209" in k, k))
    done = []
    for key in keys:
        if any(c in key for c in ("CEB", "GL2000", "2010", "Eurocode", "AS ", "JTG", "User")):
            continue                                   # tables for other code models
        ok_key = False
        for law in LAW_NAMES[td["law"]]:
            for yes, no in (("Yes", "No"), ("True", "False")):
                err, wrote = upsert(sm, key, [td], lambda rows: [_td_rules(td, law, yes, no) for _ in rows],
                                    name_words=("material", "name"))
                if not err:
                    ok_key = True
                    break
                tried.append(f"{key} ({law}, {yes}/{no}): {err} [wrote {wrote}]")
            if ok_key:
                break
        if ok_key:
            done.append(f"{key}: {_row_of(sm, key, td['material'])}")
    if not done:
        raise R.Stop("time-dependent material properties: no table took them. Tried: " + " | ".join(tried[-6:])
                     + " || tables: " + " || ".join(described)
                     + f" || PropMaterial methods: {R._methods(sm.PropMaterial)}")
    for d in done:
        R.say(f"  time-dependent (read back) {d}")
    return f"{td['law']} through {len(done)} table(s)"


def set_td_staged(sm, case: dict, R, point, names: dict, st: dict) -> str:
    """One stage: add every frame and joint at age st['age'] (days), load st['pattern'] if any, hold
    for st['duration'] days; time-dependent material properties on."""
    g = "TDALL"
    t = []
    if not R._try("GroupDef.SetGroup", t, lambda: sm.GroupDef.SetGroup(g)):
        raise R.Stop("group refused: " + "; ".join(t))
    for f in case["frames"]:
        R.ok(sm.FrameObj.SetGroupAssign(names[f["id"]], g, False, 0), f"group {g}: {f['id']}")
        for p in (f["i"], f["j"]):
            R.ok(sm.PointObj.SetGroupAssign(point(p), g, False, 0), f"group {g}: point {p}")
    nm, ss = st["name"], sm.LoadCases.StaticNonlinearStaged
    if not R._try("StaticNonlinearStaged.SetCase", t, lambda: ss.SetCase(nm)):
        raise R.Stop("staged case refused: " + "; ".join(t) + f" | offers: {R._methods(ss)}")
    dur = float(st["duration"])
    if not (R._try("SetStageDefinitions_2", t, lambda: ss.SetStageDefinitions_2(nm, 1, [dur], [True], [""], [""]))
            or R._try("SetStageDefinitions_1", t, lambda: ss.SetStageDefinitions_1(nm, 1, [dur], [True], [""], [""]))
            or R._try("SetStageDefinitions", t, lambda: ss.SetStageDefinitions(nm, 1, [dur], [True], [""]))):
        raise R.Stop("stage definition refused: " + "; ".join(t) + f" | offers: {R._methods(ss)}")
    age = float(st["age"])
    if st.get("pattern"):
        # operation 1 = add structure (with the age of what is added), 3 = load objects if new
        args = (nm, 1, 2, [1, 3], ["Group", "Group"], [g, g], [age, 0.0], ["", "Load"], ["", st["pattern"]],
                [0.0, 1.0])
    else:
        args = (nm, 1, 1, [1], ["Group"], [g], [age], [""], [""], [0.0])
    if not (R._try("SetStageData_2", t, lambda: ss.SetStageData_2(*args))
            or R._try("SetStageData_1", t, lambda: ss.SetStageData_1(*args))
            or R._try("SetStageData", t, lambda: ss.SetStageData(*args))):
        raise R.Stop("stage data refused: " + "; ".join(t[-3:]) + f" | offers: {R._methods(ss)}")
    if hasattr(ss, "SetGeometricNonlinearity"):
        R._try("SetGeometricNonlinearity", t, lambda: ss.SetGeometricNonlinearity(nm, 0))
    # time-dependent material properties on for the case
    how = None
    for label, call in (("SetMaterialNonlinearity(name, True)", lambda: ss.SetMaterialNonlinearity(nm, True)),
                        ("SetMaterialNonlinearity(name, True, True, True)",
                         lambda: ss.SetMaterialNonlinearity(nm, True, True, True)),
                        ("SetMaterialNonlinearity(name, True, True, True, True)",
                         lambda: ss.SetMaterialNonlinearity(nm, True, True, True, True))):
        if hasattr(ss, "SetMaterialNonlinearity") and R._try(label, t, call):
            how = label
            break
    if how is None:
        key = R.find_table(sm, "load case", "staged") or R.find_table(sm, "staged")
        if key:
            try:
                how = R.edit_table_row(sm, key, nm, {"timedep|timedependent|timedepmatprop|materialtime": "Yes"})
            except R.Stop as exc:
                t.append(str(exc))
        if how is None:
            raise R.Stop("staged case: time-dependent material properties could not be switched on: "
                         + "; ".join(t[-4:]) + f" | StaticNonlinearStaged offers: {R._methods(ss)}"
                         + (" | staged tables: " + " || ".join(describe(sm, R, [key])) if key else ""))
    back = ""
    if hasattr(ss, "GetMaterialNonlinearity"):
        for call in (lambda: ss.GetMaterialNonlinearity(nm, False),
                     lambda: ss.GetMaterialNonlinearity(nm, False, False, False)):
            try:
                back = f"; read back {list(call())}"
                break
            except Exception:
                pass
    return (f"one stage of {dur:g} days: add at age {age:g} days" + (f", load {st['pattern']}" if st.get("pattern")
                                                                       else "") + f"; time-dependent on ({how}{back})")


# ── C3: frame hinges through the tables ─────────────────────────────────────
# elastic-perfectly-plastic backbone, moment / SF and plastic rotation / SF (SF rotation 1 rad)
EPP_POINTS = [("A", 0.0, 0.0), ("B", 1.0, 0.0), ("C", 1.0, 0.5), ("D", 0.2, 0.55), ("E", 0.2, 1.0)]


def _hinge_common(mp: float):
    """Rules for the property-level fields of the user hinge (any layout)."""
    common = [
        (("name",), HINGE), (("property",), HINGE, ("type",)), (("hinge",), HINGE, ("type", "length", "dof")),
        (("dof",), "M3"), (("degree",), "M3"),
        (("symm",), "Yes"), (("hyst",), "Isotropic"),
        (("sf", "rot"), 1.0), (("scale", "rot"), 1.0), (("useyield",), "No"),
        (("sf", "mom"), mp), (("scale", "mom"), mp), (("sf",), mp, ("rot",)),
        (("control",), "Deformation Controlled"), (("ductil",), "Ductile"),
        (("type",), "Moment - Rotation"),
        (("=io",), 0.01), (("=ls",), 0.02), (("=cp",), 0.03),
    ]
    for letter, m, r in EPP_POINTS:                    # one-row layouts: MomentB, RotationB, ...
        lo = letter.lower()
        common += [(("mom", lo + "$"), m, ("sf", "scale")), (("m" + lo + "/sf",), m),
                   (("rot", lo + "$"), r, ("sf", "scale"))]
    return common


def _hinge_point_rows(mp: float):
    """One row per backbone point, for tables with a Point field."""
    common = _hinge_common(mp)
    return [[(("point",), letter), (("mom", "/sf"), m), (("rot", "/sf"), r), (("m/sf",), m),
             (("mom",), m, ("sf", "scale")), (("force",), m, ("sf", "scale")), (("rot",), r, ("sf", "scale"))]
            + common for letter, m, r in EPP_POINTS]


def _assign_rules(fname: str, label: str, story: str, rel: float, prop: str):
    return [(("unique",), fname), (("label",), label), (("frame",), label, ("rel", "dist")),
            (("story",), story), (("rel",), rel), (("dist",), rel), (("hinge",), prop, ("dist", "rel", "type")),
            (("property",), prop), (("prop",), prop), (("type",), "User Defined", ("auto",)),
            (("dof",), "M3")]


def assign_hinges(sm, case: dict, R, names: dict, po: dict) -> str:
    """Define the user hinge property and assign it at both ends of every hinged frame, through the
    hinge tables ETABS offers; R.Stop with what was found when nothing takes."""
    mps = set(po["hinges"].values())
    if len(mps) != 1:
        raise R.Stop("C3 hinges: one Mp per case is supported")
    mp = float(mps.pop())
    api = {"FrameObj": _api_names(sm.FrameObj, "hinge"), "PropHinge": hasattr(sm, "PropHinge"),
           "PropFrame": _api_names(sm.PropFrame, "hinge")}
    R.say(f"  hinge API: {api}")
    types = _import_types(sm)
    keys = [k for k in (types or {k: 0 for k in R.table_keys(sm)}) if "hinge" in k.lower()]
    R.say(f"  hinge tables ({len(keys)}):")
    described = describe(sm, R, keys, types)
    assign_keys = [k for k in keys if "assign" in k.lower() and "frame" in k.lower()
                   and "overwrite" not in k.lower()] or [k for k in keys if "assign" in k.lower()]
    def_keys = [k for k in keys if k not in assign_keys and "frame" in k.lower()
                and any(w in k.lower() for w in ("definition", "propert", "user", "moment", "deformation"))
                and not any(w in k.lower() for w in ("overwrite", "generated", "result", "state", "summary"))]
    tried = []

    def fail(why: str):
        return R.Stop(f"frame hinges: {why}. Tried: " + " | ".join(tried[-8:]) + f" || API: {api} || hinge tables "
                      "(import type: 0 not importable, 1 not interactively, 2 unlocked, 3 always): "
                      + " || ".join(described))

    if not keys:
        raise fail("ETABS offers no table with 'hinge' in its key")
    if not assign_keys:
        raise fail("no hinge assignment table")
    # 1. the user hinge property: general row(s) first, then the backbone points
    defined = False
    for key in sorted(def_keys, key=lambda k: ("point" in k.lower() or "backbone" in k.lower(), k)):
        fk, _ = _fields(sm, key)
        per_point = any("point" in _norm(f) for f in fk)
        rows = _hinge_point_rows(mp) if per_point else [_hinge_common(mp)]
        err, wrote = upsert(sm, key, rows, lambda rr: rr, name_words=("__none__",))   # new property: append
        if err:
            tried.append(f"define {key}: {err} [wrote {wrote[:300]}]")
        else:
            defined = True
            R.say(f"  hinge property {HINGE} in {key}: {_row_of(sm, key, HINGE)[:600]}")
    # 2. assignments at both ends of each hinged frame
    rows = []
    for fid in po["hinges"]:
        fname = names.get(fid, fid)
        label, story = fid, case["stories"][0]["name"]
        try:
            got = sm.FrameObj.GetLabelFromName(fname, "", "")
            if got[-1] == 0:
                label, story = str(got[0]), str(got[1])
        except Exception:
            pass
        rows += [(fname, label, story, rel) for rel in (0.0, 1.0)]
    props = [HINGE] if defined else []
    props += ["Auto M3"] if not defined else []          # tried only to learn the field layout, never kept
    assigned = None
    for key in assign_keys:
        for prop in props:
            err, wrote = upsert(sm, key, rows, lambda rr, p=prop: [_assign_rules(*r, p) for r in rr],
                                name_words=("__none__",))  # always append: one row per hinge
            if err:
                tried.append(f"assign {prop} in {key}: {err} [wrote {wrote[:300]}]")
                continue
            back = _row_of(sm, key, names.get(next(iter(po["hinges"])), ""))
            if prop != HINGE:
                tried.append(f"assign {prop} in {key} accepted ({back[:200]}) but no user property exists")
                continue
            assigned = f"{key}: {back[:600]}"
            break
        if assigned:
            break
    if not defined:
        raise fail(f"the user hinge property {HINGE} (M3, elastic-perfectly-plastic, Mp {mp:g}) could not be "
                   f"defined (definition tables found: {def_keys or 'none'})")
    if not assigned:
        raise fail(f"{HINGE} defined but not assigned (assignment tables: {assign_keys})")
    R.say(f"  hinges (read back) {assigned}")
    return f"{HINGE} (Mp {mp:g}) at both ends of {', '.join(po['hinges'])}"


# ── hooks ───────────────────────────────────────────────────────────────────
def build(sm, case, R, point, names) -> None:
    td = case.get("timeDependent")
    if td:
        mt = next(m for m in case["materials"] if m["name"] == td["material"])
        set_concrete(sm, R, mt)
        R.say(f"  time-dependent material {td['material']}: {set_time_dependent(sm, R, td)}")
    st = case.get("tdStaged")
    if st:
        R.say(f"  staged case {st['name']}: {set_td_staged(sm, case, R, point, names, st)}")
    for po in case.get("extPushover", []):
        R.say(f"  hinges: {assign_hinges(sm, case, R, names, po)}")
        bare = {k: v for k, v in po.items() if k != "hinges"}      # the stock case, hinges already in
        R.say(f"  pushover {po['name']}: {R.set_pushover(sm, case, bare, point)}")


def _uz(res: dict, cname: str, at):
    c = res["cases"].get(cname) or {}
    js = c.get("joints") or []
    if not js:
        return None
    best = min(js, key=lambda j: math.dist(j["xyz"], at))
    return best["u"][2] if math.dist(best["xyz"], at) < 1e-3 else None


def results(sm, case, R, names, res) -> None:
    """The same named scalars as sf_ext_timedep.values."""
    keys = {c.get("key") for c in case["checks"] if c["quantity"] == "value"}
    st = case.get("tdStaged") or {}
    if not st:
        return
    top = max((p for f in case["frames"] for p in (f["i"], f["j"])), key=lambda p: p[2])
    u_td = _uz(res, st["name"], top)
    if "phi" in keys and st.get("pattern"):
        u_el = _uz(res, st["pattern"], top)
        if u_td is not None and u_el:
            res["values"]["phi"] = u_td / u_el - 1.0
    if "eps_sh" in keys and u_td is not None:
        res["values"]["eps_sh"] = -u_td / top[2]
