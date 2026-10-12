"""ETABS ``.e2k`` full reader (the counterpart of :mod:`e2k_write`).

``read_e2k(text, units=("KN","M"), apply_extensions=True)`` returns
``(model, warnings)``.  Records are dispatched by their KEYWORD (ETABS
treats the ``$`` headers as comments), so files written by ETABS v20+ and
by SkyFrame's exporter are both read.  Supported records: UNITS, TOWER,
STORY (HEIGHT / ELEV / TOWER), GRIDSYSTEM / GRID, DIAPHRAGM, MATERIAL,
FRAMESECTION (Concrete Rectangular, steel / concrete parametric shapes,
database shapes via FROMFILE, General), SDSECTION (-> built-up sections),
SHELLPROP, LINKPROP, POINTSPRING, AREASPRING, POINT, LINE (COLUMN / BEAM /
BRACE), AREA (FLOOR / PANEL, openings), LINK, POINTASSIGN, LINEASSIGN,
AREAASSIGN, LINKASSIGN, LOADPATTERN, SEISMIC / WIND user story loads,
POINTLOAD, LINELOAD, AREALOAD, SHELLUNIFORMLOADSET, FUNCTION, ACTIVEDOF,
PDELTA, PDELTALOAD, MASSSOURCE, MASSSOURCELOAD, LOADCASE, COMBO, GROUP and
the SkyFrame ``$SF {json}`` residual patches.  Anything else becomes a
warning; the reader never raises on content.

The legacy subset reader :func:`skyframe.io.e2k.import_e2k` is unchanged.
"""

from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Tuple

from skyframe.io import e2k_ext as _e2kx
from skyframe.io.e2k import _kv, _num, _Tok, _tokenize
from skyframe.io.exchange_common import G_SI, Units, apply_patches

__all__ = ["read_e2k"]

_TYPE_KIND = {"DEAD": "dead", "SUPER DEAD": "dead", "SUPERDEAD": "dead",
              "LIVE": "live", "REDUCIBLE LIVE": "live", "ROOF LIVE": "live",
              "SEISMIC": "quake", "QUAKE": "quake", "WIND": "wind",
              "NOTIONAL": "notional", "PRESTRESS": "prestress",
              "OTHER": "other"}
_MAT_KIND = {"STEEL": "steel", "CONCRETE": "concrete", "REBAR": "rebar",
             "TENDON": "tendon", "MASONRY": "masonry",
             "ALUMINUM": "aluminum", "COLDFORMED": "coldformed",
             "NODESIGN": "other", "OTHER": "other"}
_SHELL_TYPE = {"SHELLTHIN": "shell_thin", "SHELLTHICK": "shell_thick",
               "MEMBRANE": "membrane", "PLATETHIN": "plate_thin",
               "PLATETHICK": "plate_thick", "LAYERED": "shell_thick"}
_COMBO = {"LINEAR ADD": "add", "ENVELOPE": "envelope",
          "ABSOLUTE ADD": "abs", "SRSS": "srss", "RANGE ADD": "range",
          "RANGE": "range"}
_RS = {"CQC": "CQC", "SRSS": "SRSS", "ABS": "ABS", "GMC": "GMC",
       "NRC 10 PERCENT": "NRC10", "DOUBLE SUM": "DSC"}
_PDM = {"NONE": "none", "NONITERATIVE": "non_iterative_mass",
        "ITERATIVE": "iterative_loads"}
_FDIR = {"GRAV": "gravity", "GRAVITY": "gravity", "2": "local_y",
         "X": "global_x", "Y": "global_y", "Z": "global_z"}
_ADIR = {"GRAV": "gravity", "GRAVITY": "gravity", "X": "global_x",
         "Y": "global_y", "Z": "global_z", "1": "local_1", "2": "local_2",
         "3": "local_3"}
_LINK_TYPES = {"LINEAR": "elastic", "DAMPER": "damper", "GAP": "gap",
               "HOOK": "hook", "RUBBER ISOLATOR": "isolator",
               "FRICTION ISOLATOR": "fp_isolator",
               "T/C FRICTION ISOLATOR": "triple_fp",
               "MULTILINEAR ELASTIC": "multilinear"}
_DB_FILES = {"AISC": "aisc", "EURO": "eurocode", "BSSHAPES": "bs",
             "BS": "bs", "AUSTRALIA": "as_nzs", "INDIAN": "is808",
             "IS": "is808"}
_DOFS = ("UX", "UY", "UZ", "RX", "RY", "RZ")
_LDOFS = ("U1", "U2", "U3", "R1", "R2", "R3")


def _shape_type(shape: str, kv: dict) -> Optional[str]:
    s = shape.upper()
    table = {"STEEL I/WIDE FLANGE": "I", "I/WIDE FLANGE": "I",
             "STEEL CHANNEL": "channel", "CHANNEL": "channel",
             "STEEL ANGLE": "angle", "ANGLE": "angle",
             "STEEL DOUBLE ANGLE": "double_angle",
             "DOUBLE ANGLE": "double_angle", "STEEL TEE": "tee",
             "TEE": "tee", "STEEL TUBE": "rect_tube", "BOX/TUBE": "rect_tube",
             "STEEL BOX": "rect_tube", "STEEL PIPE": "round_tube",
             "PIPE": "round_tube", "STEEL RECTANGULAR": "rect_solid",
             "RECTANGULAR": "rect_solid", "STEEL ROD": "round_solid",
             "STEEL CIRCLE": "round_solid", "CIRCLE": "round_solid",
             "CONCRETE CIRCLE": "round_solid", "STEEL PLATE": "plate",
             "CONCRETE TEE": "tee_concrete", "CONCRETE L": "L_concrete"}
    typ = table.get(s)
    if typ == "I" and "BB" in kv:
        return "I_mono"
    return typ


def _dims(typ: str, g) -> dict:
    """Parametric dims (SI) from ETABS keywords; ``g(key)`` -> SI or None."""
    out: dict = {}

    def put(k, v):
        if v is not None:
            out[k] = v
    if typ == "I":
        put("d", g("D")), put("bf", g("B")), put("tf", g("TF"))
        put("tw", g("TW")), put("r", g("FILLETRADIUS"))
    elif typ == "I_mono":
        put("d", g("D")), put("bf_top", g("B")), put("tf_top", g("TF"))
        put("bf_bot", g("BB")), put("tf_bot", g("TFB")), put("tw", g("TW"))
    elif typ in ("channel", "tee"):
        put("d", g("D")), put("bf", g("B")), put("tf", g("TF"))
        put("tw", g("TW"))
    elif typ == "angle":
        put("d", g("D")), put("b", g("B")), put("t", g("TF") or g("TW"))
    elif typ == "double_angle":
        put("d", g("D")), put("b", g("B")), put("t", g("TF") or g("TW"))
        put("s", g("DIS"))
    elif typ == "rect_tube":
        put("d", g("D")), put("b", g("B")), put("t", g("TF") or g("TW"))
        put("r_out", g("CORNERRADIUS"))
    elif typ == "round_tube":
        put("D", g("D")), put("t", g("TW") or g("TF"))
    elif typ == "rect_solid":
        put("d", g("D")), put("b", g("B"))
    elif typ == "plate":
        put("d", g("D")), put("b", g("B"))
    elif typ == "round_solid":
        put("D", g("D"))
    elif typ in ("tee_concrete", "L_concrete"):
        put("bf", g("B")), put("hf", g("TF")), put("bw", g("TW"))
        put("h", g("D"))
    return out


def _db_std(fname: str) -> Optional[str]:
    up = fname.upper()
    for key, sid in _DB_FILES.items():
        if up.startswith(key):
            return sid
    return None


class _Reader:
    def __init__(self, text: str, units):
        self.text = text
        self.w: List[str] = []
        self.u = Units(*units)
        self.recs: Dict[str, list] = {}
        self.patches: List[dict] = []
        self._units_seen = False
        self._scan()

    def _scan(self) -> None:
        unknown: Dict[str, int] = {}
        for raw in self.text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("$SF "):
                try:
                    self.patches.append(json.loads(line[4:]))
                except ValueError as exc:
                    self.w.append(f"bad SkyFrame extension line: {exc}")
                continue
            if line.startswith("$"):
                continue
            toks = _tokenize(line)
            if not toks:
                continue
            head = toks[0].text.upper()
            if head in ("END",):
                continue
            if head == "UNITS" and len(toks) >= 3:
                try:
                    self.u = Units(toks[1].text, toks[2].text)
                except ValueError as exc:
                    self.w.append(f"{exc} — assuming kN m")
                    self.u = Units("KN", "M")
                continue
            self.recs.setdefault(head, []).append(toks)
        known = {"PROGRAM", "TITLE1", "TITLE2", "PREFERENCE", "RLLF",
                 "TOWER", "STORY", "GRIDSYSTEM", "GRID", "DIAPHRAGM",
                 "MATERIAL", "FRAMESECTION", "SDSECTION", "SHELLPROP",
                 "LINKPROP", "POINTSPRING", "AREASPRING", "POINT", "LINE",
                 "AREA", "LINK", "POINTASSIGN", "LINEASSIGN", "AREAASSIGN",
                 "LINKASSIGN", "LOADPATTERN", "SEISMIC", "WIND",
                 "POINTLOAD", "LINELOAD", "AREALOAD", "SHELLUNIFORMLOADSET",
                 "FUNCTION", "ACTIVEDOF", "PDELTA", "PDELTALOAD",
                 "MASSSOURCE", "MASSSOURCELOAD", "LOADCASE", "COMBO",
                 "GROUP"}
        for head, rows in self.recs.items():
            if head not in known:
                unknown[head] = len(rows)
        for head, n in sorted(unknown.items()):
            self.w.append(f"{n} {head} record(s) not supported; ignored")

    # helpers
    def si(self, tok, qty: str) -> Optional[float]:
        v = _num(tok)
        return None if v is None else self.u.to_si(v, qty)

    def rows(self, head: str) -> list:
        return self.recs.get(head, [])


def _bool(tok) -> bool:
    return tok is not None and tok.text.strip().upper() in ("YES", "TRUE")


def read_e2k(text: str, units=("KN", "M"),
             apply_extensions: bool = True):
    """Read e2k text -> ``(BuildingModel, warnings)`` (see module doc)."""
    from skyframe.core.model import BuildingModel, MODAL_CASE
    r = _Reader(text, units)
    w = r.w
    d: dict = {"name": "E2K Import", "frame_axis_convention": "etabs"}
    for t in r.rows("TITLE2"):
        if len(t) > 1:
            d["name"] = t[1].text

    # ---------------------------------------------------- towers + stories
    tower_names = [t[1].text for t in r.rows("TOWER") if len(t) > 1]
    raw: Dict[str, list] = {}                # tower -> [(name, h|None, elev)]
    order: List[str] = []
    for t in r.rows("STORY"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        tw = kv["TOWER"].text if "TOWER" in kv else ""
        if tw not in raw:
            raw[tw] = []
            order.append(tw)
        raw[tw].append((t[1].text, r.si(kv.get("HEIGHT"), "length"),
                        r.si(kv.get("ELEV"), "length")))
    levels: Dict[str, float] = {}            # level name -> elevation
    level_list: Dict[str, List[str]] = {}    # tower -> level names bottom-up
    stories: List[dict] = []
    towers: List[dict] = []
    tw_order = [tn for tn in tower_names if tn in raw] + \
        [tn for tn in order if tn not in tower_names]
    for ti, tw in enumerate(tw_order):
        rows = list(reversed(raw[tw]))       # bottom-up
        base_name, base = "Base", 0.0
        if rows and rows[0][1] is None:
            base_name, _h, e = rows.pop(0)
            base = e if e is not None else 0.0
        levels[base_name] = base
        names = [base_name]
        elev = base
        tstories = []
        for name, h, e in rows:
            if h is None or h <= 0.0:
                if e is not None and e > elev:
                    h = e - elev
                else:
                    w.append(f"story {name!r} has no positive HEIGHT; "
                             "ignored")
                    continue
            elev = elev + h
            levels[name] = elev
            names.append(name)
            sd = {"name": name, "height": h, "elevation": elev}
            if tw and ti > 0:
                sd["tower"] = tw
            stories.append(sd)
            tstories.append(name)
        level_list[tw] = names
        if tw:
            towers.append({"name": tw, "grid_system": None,
                           "base_elevation": base, "stories": tstories})
    d["stories"] = stories
    if towers and tower_names:
        d["towers"] = towers
    story_names = {s["name"] for s in stories}
    level_tower = {n: tw for tw, ns in level_list.items() for n in ns}

    def level_index(name: str) -> Tuple[str, int]:
        tw = level_tower.get(name)
        if tw is None:
            return "", -1
        return tw, level_list[tw].index(name)

    def level_at(tw: str, k: int) -> Optional[str]:
        ns = level_list.get(tw, [])
        return ns[k] if 0 <= k < len(ns) else None

    # ---------------------------------------------------------------- grids
    grids: Dict[str, dict] = {}
    for t in r.rows("GRIDSYSTEM"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        typ = (kv["TYPE"].text.upper() if "TYPE" in kv else "CARTESIAN")
        grids[t[1].text] = {
            "name": t[1].text,
            "kind": "radial" if typ.startswith("CYL") else "orthogonal",
            "origin": [r.si(kv.get("UX"), "length") or 0.0,
                       r.si(kv.get("UY"), "length") or 0.0],
            "rotation": _num(kv.get("ROTATION")) or 0.0,
            "x_lines": [], "y_lines": [], "radii": [], "theta_deg": []}
    for t in r.rows("GRID"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        g = grids.setdefault(t[1].text, {
            "name": t[1].text, "kind": "orthogonal", "origin": [0.0, 0.0],
            "rotation": 0.0, "x_lines": [], "y_lines": [], "radii": [],
            "theta_deg": []})
        dirn = kv["DIR"].text.upper() if "DIR" in kv else "X"
        if dirn == "T":
            v = _num(kv.get("COORD"))
        else:
            v = r.si(kv.get("COORD"), "length")
        if v is None:
            continue
        key = {"X": "x_lines", "Y": "y_lines", "R": "radii",
               "T": "theta_deg"}.get(dirn)
        if key:
            g[key].append(v)
    if grids:
        d["grid_systems"] = list(grids.values())

    # ------------------------------------------------------------ materials
    mats: Dict[str, dict] = {}
    for t in r.rows("MATERIAL"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        m = mats.setdefault(t[1].text, {"name": t[1].text, "E": None})
        if "TYPE" in kv:
            m["material_type"] = _MAT_KIND.get(kv["TYPE"].text.upper(),
                                               "other")
        for key, fld, qty in (("WEIGHTPERVOLUME", "unit_weight",
                               "unit_weight"),
                              ("MASSPERVOLUME", "mass_density",
                               "mass_density"),
                              ("E", "E", "stress"), ("FC", "fc", "stress"),
                              ("FY", "fy", "stress"), ("FU", "fu", "stress"),
                              ("FYE", "_fye", "stress"),
                              ("EFFFY", "_fye", "stress"),
                              ("FUE", "_fue", "stress"),
                              ("EFFFU", "_fue", "stress")):
            if key in kv and r.si(kv[key], qty) is not None:
                m[fld] = r.si(kv[key], qty)
        if "U" in kv and _num(kv["U"]) is not None:
            m["nu"] = _num(kv["U"])
        if "A" in kv and _num(kv["A"]) is not None:
            m["alpha"] = _num(kv["A"])
        if "SYMTYPE" in kv:
            m["symmetry"] = "uniaxial" if kv["SYMTYPE"].text.upper() == \
                "UNIAXIAL" else "isotropic"
        if "LTWTCONC" in kv:
            m["lightweight"] = _bool(kv["LTWTCONC"])
        if "LTWTFACT" in kv:
            m["lam"] = _num(kv["LTWTFACT"])
        if "DAMPRATIO" in kv:
            m["damping"] = _num(kv["DAMPRATIO"])
    for m in mats.values():
        if m.get("E") is None:
            w.append(f"material {m['name']!r} has no E — 25 GPa used")
            m["E"] = 25e6
        fye, fue = m.pop("_fye", None), m.pop("_fue", None)
        if fye is not None and m.get("fy"):
            m["Ry"] = round(fye / m["fy"], 12)
        elif fue is not None and m.get("fu"):
            m["Ry"] = round(fue / m["fu"], 12)
    d["materials"] = mats

    def ensure_mat(name: str) -> str:
        if name not in mats:
            w.append(f"material {name!r} not defined — 25 GPa concrete used")
            mats[name] = {"name": name, "E": 25e6}
        return name

    # ------------------------------------------------------- frame sections
    from skyframe.core.model import FrameSection
    secs: Dict[str, dict] = {}
    sd_secs: Dict[str, dict] = {}
    for t in r.rows("SDSECTION"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        s = sd_secs.setdefault(t[1].text, {"name": t[1].text,
                                           "material": "", "components": {}})
        if "SHAPE" in kv and _num(kv["SHAPE"]) is not None:
            idx = int(_num(kv["SHAPE"]))
            s["components"][idx] = kv
        elif "MATERIAL" in kv:
            s["material"] = kv["MATERIAL"].text
    mods: Dict[str, dict] = {}
    for t in r.rows("FRAMESECTION"):
        if len(t) < 2:
            continue
        name = t[1].text
        kv = _kv(t, 2)
        if "SHAPE" not in kv:
            mm = mods.setdefault(name, {})
            for key, fld in (("AMOD", "mod_A"), ("A2MOD", "mod_As2"),
                             ("A3MOD", "mod_As3"), ("JMOD", "mod_J"),
                             ("I2MOD", "mod_I22"), ("I3MOD", "mod_I33"),
                             ("MMOD", "mod_mass"), ("WMOD", "mod_weight")):
                if key in kv and _num(kv[key]) is not None:
                    mm[fld] = _num(kv[key])
            continue
        mat = ensure_mat(kv["MATERIAL"].text if "MATERIAL" in kv else "CONC")
        shape = kv["SHAPE"].text
        g = (lambda k, kv=kv: r.si(kv.get(k), "length"))
        sec = None
        if shape.upper() in ("CONCRETE RECTANGULAR",) and g("D") and g("B"):
            sec = FrameSection.rectangular(name, mat, g("B"), g("D"))
        elif shape.upper() == "GENERAL":
            A = r.si(kv.get("AREA"), "area")
            if A:
                sec = FrameSection(
                    name, mat, A, r.si(kv.get("I33"), "inertia") or 0.0,
                    r.si(kv.get("I22"), "inertia") or 0.0,
                    r.si(kv.get("J"), "inertia") or 0.0,
                    b=g("B") or 0.0, h=g("D") or 0.0)
                as2 = r.si(kv.get("AS2"), "area")
                as3 = r.si(kv.get("AS3"), "area")
                sec.As2 = as2 if as2 else None
                sec.As3 = as3 if as3 else None
                if name in sd_secs:
                    sec.shape = "builtup"
        else:
            typ = _shape_type(shape, kv)
            if typ is not None and "FROMFILE" in kv:
                std = _db_std(kv["FROMFILE"].text)
                dbn = kv["DBSECTNAME"].text if "DBSECTNAME" in kv else name
                if std:
                    try:
                        from skyframe.core.steel_db import section_from_db
                        sec = section_from_db(std, dbn, mat, name)
                    except Exception as exc:          # noqa: BLE001
                        w.append(f"frame section {name!r}: database shape "
                                 f"{dbn!r} not found ({exc}); dimensions "
                                 "used")
            if sec is None and typ is not None:
                try:
                    from skyframe.core.section_ext import section_from_dims
                    sec = section_from_dims(name, mat, typ, _dims(typ, g))
                except Exception as exc:              # noqa: BLE001
                    w.append(f"frame section {name!r}: shape {shape!r} "
                             f"dimensions invalid ({exc})")
        if sec is None:
            w.append(f"frame section {name!r}: shape {shape!r} not "
                     "supported — default 0.3x0.6 rectangle used")
            sec = FrameSection.rectangular(name, mat, 0.3, 0.6)
        secs[name] = sec.to_dict()
    for name, mm in mods.items():
        if name in secs:
            secs[name].update(mm)
    d["sections"] = secs

    # built-up sections from SD records
    bus: Dict[str, dict] = {}
    for name, s in sd_secs.items():
        if name not in secs:
            continue
        comps = []
        for idx in sorted(s["components"]):
            kv = s["components"][idx]
            g = (lambda k, kv=kv: r.si(kv.get(k), "length"))
            st = kv["SHAPETYPE"].text.upper() if "SHAPETYPE" in kv else ""
            c: dict
            if st in ("STEEL PLATE", "PLATE") and "DBSECTNAME" not in kv:
                c = {"kind": "plate", "b": g("B"), "t": g("D"),
                     "x": g("XC") or 0.0, "y": g("YC") or 0.0,
                     "angle": _num(kv.get("ROTATION")) or 0.0}
            else:
                typ = _shape_type(st, kv)
                if typ is None:
                    w.append(f"SD section {name!r}: shape type {st!r} "
                             "skipped")
                    continue
                if "DBSECTNAME" in kv and _db_std(kv["FROMFILE"].text
                                                  if "FROMFILE" in kv
                                                  else ""):
                    c = {"kind": "db",
                         "standard": _db_std(kv["FROMFILE"].text),
                         "name": kv["DBSECTNAME"].text}
                else:
                    c = {"kind": "param", "type": typ,
                         "dims": _dims(typ, g)}
                c.update({"x": g("XC") or 0.0, "y": g("YC") or 0.0,
                          "rotation": _num(kv.get("ROTATION")) or 0.0,
                          "mirror": _bool(kv.get("MIRROR2"))})
            m_ = kv["MATERIAL"].text if "MATERIAL" in kv else ""
            if m_ and m_ != (s["material"] or secs[name]["material"]):
                c["material"] = m_
            comps.append(c)
        bus[name] = {"name": name,
                     "material": s["material"] or secs[name]["material"],
                     "components": comps, "axes": "geometric",
                     "torsion": "auto"}
    if bus:
        d["builtup_sections"] = bus

    # ----------------------------------------------------------- shell props
    sprops: Dict[str, dict] = {}
    for t in r.rows("SHELLPROP"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        p = sprops.setdefault(t[1].text, {"name": t[1].text})
        if "MATERIAL" in kv:
            p["material"] = ensure_mat(kv["MATERIAL"].text)
        elif "CONCMATERIAL" in kv:
            p["material"] = ensure_mat(kv["CONCMATERIAL"].text)
        for key in ("WALLTHICKNESS", "SLABTHICKNESS", "DECKSLABDEPTH"):
            if key in kv:
                p["thickness"] = r.si(kv[key], "length")
        if "MODELINGTYPE" in kv:
            p["_model"] = kv["MODELINGTYPE"].text.upper()
        if "PROPTYPE" in kv:
            p["_prop"] = kv["PROPTYPE"].text.upper()
        for key, fld in (("F11MOD", "f11"), ("F22MOD", "f22"),
                         ("F12MOD", "f12"), ("M11MOD", "m11"),
                         ("M22MOD", "m22"), ("M12MOD", "m12"),
                         ("V13MOD", "v13"), ("V23MOD", "v23"),
                         ("MMOD", "mass"), ("WMOD", "weight")):
            if key in kv and _num(kv[key]) is not None:
                p[fld] = _num(kv[key])
    shell_secs: Dict[str, dict] = {}
    membrane_props = set()
    alias: Dict[str, str] = {}
    for name, p in sprops.items():
        mtype = p.pop("_model", "SHELLTHIN")
        ptype = p.pop("_prop", "SLAB")
        if mtype == "MEMBRANE" and ptype != "WALL":
            membrane_props.add(name)
            if name.endswith("~M") and name[:-2] in sprops:
                alias[name] = name[:-2]
                continue
        else:
            p["shell_type"] = _SHELL_TYPE.get(mtype, "shell_thin")
        p.setdefault("material", ensure_mat("CONC"))
        p.setdefault("thickness", 0.2)
        if p["thickness"] is None or p["thickness"] <= 0:
            p["thickness"] = 0.2
        shell_secs[name] = p
    d["shell_sections"] = shell_secs

    # ----------------------------------------------------------- springs
    psprings: Dict[str, List[float]] = {}
    for t in r.rows("POINTSPRING"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        psprings[t[1].text] = [
            r.si(kv.get(dof), "stiffness" if j < 3 else "rot_stiffness")
            or 0.0 for j, dof in enumerate(_DOFS)]
    asprings: Dict[str, dict] = {}
    for t in r.rows("AREASPRING"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        asprings[t[1].text] = {
            "kz": r.si(kv.get("U3"), "area_spring") or 0.0,
            "compression_only": "COMPRESSION" in (
                kv["NONLINEAROPT3"].text.upper() if "NONLINEAROPT3" in kv
                else "")}
    lprops: Dict[str, dict] = {}
    for t in r.rows("LINKPROP"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        p = lprops.setdefault(t[1].text, {"type": "elastic", "k": {}})
        if "TYPE" in kv:
            p["type"] = _LINK_TYPES.get(kv["TYPE"].text.upper(), "elastic")
        if "DOF" in kv and "KE" in kv:
            dof = kv["DOF"].text.upper()
            if dof in _LDOFS:
                j = _LDOFS.index(dof)
                p["k"][j] = r.si(kv["KE"], "stiffness" if j < 3
                                 else "rot_stiffness") or 0.0

    # ----------------------------------------------------------- points
    pts: Dict[str, Tuple[float, float, float]] = {}
    for t in r.rows("POINT"):
        if len(t) < 4:
            continue
        x, y = r.si(t[2], "length"), r.si(t[3], "length")
        dz = r.si(t[4], "length") if len(t) > 4 else 0.0
        if x is None or y is None:
            w.append(f"POINT {t[1].text!r}: coordinates are not numbers")
            continue
        pts[t[1].text] = (x, y, dz or 0.0)

    def xyz(pname: str, level: str) -> Optional[List[float]]:
        p = pts.get(pname)
        if p is None or level not in levels:
            return None
        return [p[0], p[1], levels[level] - p[2] if p[2] else levels[level]]

    # ----------------------------------------------------------- lines
    lines: Dict[str, Tuple[str, str, str, int]] = {}
    for t in r.rows("LINE"):
        if len(t) < 5:
            continue
        kind = t[2].text.upper()
        nst = int(_num(t[5])) if len(t) > 5 and _num(t[5]) is not None \
            else (1 if kind == "COLUMN" else 0)
        lines[t[1].text] = (kind, t[3].text, t[4].text, nst)
    assigns: List[Tuple[str, str, dict]] = []
    label_stories: Dict[str, int] = {}
    for t in r.rows("LINEASSIGN"):
        if len(t) < 3:
            continue
        assigns.append((t[1].text, t[2].text, _kv(t, 3)))
        label_stories[t[1].text] = label_stories.get(t[1].text, 0) + 1
    members: List[dict] = []
    member_story: Dict[Tuple[str, str], str] = {}
    seen = set()
    for label, story, kv in assigns:
        conn = lines.get(label)
        if conn is None:
            w.append(f"line assign for unknown line {label!r} skipped")
            continue
        tw, top = level_index(story)
        if top < 0:
            w.append(f"line {label!r} assigned to unknown story {story!r}")
            continue
        kind, p1, p2, nst = conn
        if nst >= 0:
            li, lj = level_at(tw, top - nst), story
        else:
            li, lj = story, level_at(tw, top + nst)
        a, b = xyz(p1, li or ""), xyz(p2, lj or "")
        if a is None or b is None:
            w.append(f"line {label!r}@{story!r}: unknown point/level; "
                     "skipped")
            continue
        uid = label if label_stories[label] == 1 else f"{label}-{story}"
        if uid in seen:
            w.append(f"duplicate line assign {label!r} @ {story!r} ignored")
            continue
        seen.add(uid)
        sec = kv["SECTION"].text if "SECTION" in kv else None
        if sec is None or sec not in secs:
            w.append(f"line {label!r}: section {sec!r} unknown — default "
                     "0.3x0.6 rectangle used")
            sec = sec or "E2K-DEFAULT"
            secs[sec] = FrameSection.rectangular(
                sec, ensure_mat(next(iter(mats), "CONC")), 0.3,
                0.6).to_dict()
        # Wave B: per-DOF RELEASE + PARTIALFIXITY (io/e2k_ext.py)
        rel_tok, end_rel = _e2kx.parse_releases(
            kv, lambda v, qty: r.u.to_si(float(v), qty)
            if _num(_Tok(v)) is not None else None)
        md = {"uid": uid,
              "kind": {"COLUMN": "column", "BEAM": "beam"}.get(kind,
                                                               "brace"),
              "section": sec, "pi": a, "pj": b,
              "story": story if story in story_names else "",
              "releases": rel_tok,
              "angle": _num(kv.get("ANGLE")) or 0.0,
              "rigid_i": r.si(kv.get("LENOFFI"), "length") or 0.0,
              "rigid_j": r.si(kv.get("LENOFFJ"), "length") or 0.0,
              "rigid_factor": (_num(kv.get("RIGIDZONE"))
                               if "RIGIDZONE" in kv else 1.0)}
        if end_rel:
            md["end_releases"] = end_rel
        if "CARDINALPT" in kv and _num(kv["CARDINALPT"]) is not None:
            md["cardinal_point"] = int(_num(kv["CARDINALPT"]))
        if "TRANSFORMSTIFFNESSFOROFFSETS" in kv and \
                not _bool(kv["TRANSFORMSTIFFNESSFOROFFSETS"]):
            md["no_transform_stiffness"] = True
        members.append(md)
        member_story[(label, story)] = uid
    d["members"] = members

    # ----------------------------------------------------------- areas
    areas: Dict[str, Tuple[str, List[str], List[int]]] = {}
    for t in r.rows("AREA"):
        if len(t) < 4:
            continue
        n = int(_num(t[3]) or 0)
        names = [x.text for x in t[4:4 + n]]
        offs = [int(_num(x) or 0) for x in t[4 + n:4 + 2 * n]]
        offs += [0] * (n - len(offs))
        areas[t[1].text] = (t[2].text.upper(), names, offs)
    shells: List[dict] = []
    openings: List[Tuple[str, List[List[float]]]] = []
    area_story: Dict[str, str] = {}
    area_labels: Dict[str, int] = {}
    aassigns = [(t[1].text, t[2].text, _kv(t, 3))
                for t in r.rows("AREAASSIGN") if len(t) >= 3]
    for label, _s, _k in aassigns:
        area_labels[label] = area_labels.get(label, 0) + 1
    for label, story, kv in aassigns:
        a = areas.get(label)
        tw, top = level_index(story)
        if a is None or top < 0:
            w.append(f"area assign {label!r}@{story!r}: unknown area or "
                     "story; skipped")
            continue
        typ, names, offs = a
        corners = [xyz(p, level_at(tw, top - o) or "")
                   for p, o in zip(names, offs)]
        if any(c is None for c in corners):
            w.append(f"area {label!r}: unknown point; skipped")
            continue
        if _bool(kv.get("OPENING")):
            openings.append((label, corners))
            continue
        uid = label if area_labels[label] == 1 else f"{label}-{story}"
        sec = kv["SECTION"].text if "SECTION" in kv else ""
        behavior = "shell"
        if sec in membrane_props:
            behavior = "membrane" if typ != "PANEL" else "shell"
            sec = alias.get(sec, sec)
            if behavior == "shell" and sec in shell_secs:
                shell_secs[sec]["shell_type"] = "membrane"
        if sec not in shell_secs:
            w.append(f"area {label!r}: shell property {sec!r} unknown — "
                     "0.2 m default used")
            shell_secs[sec or "E2K-SHELL"] = {
                "name": sec or "E2K-SHELL", "material": ensure_mat("CONC"),
                "thickness": 0.2, "shell_type": "shell_thin"}
            sec = sec or "E2K-SHELL"
        sd = {"uid": uid, "kind": "wall" if typ == "PANEL" else "slab",
              "behavior": behavior, "section": sec, "corners": corners,
              "mesh_size": 1.0,
              "story": story if story in story_names else "",
              "openings": []}
        if "DIAPH" in kv:
            sd["diaphragm"] = kv["DIAPH"].text
        if "SPRINGPROP" in kv and kv["SPRINGPROP"].text in asprings:
            sd["area_spring"] = dict(asprings[kv["SPRINGPROP"].text])
        if "PIER" in kv:
            sd["pier"] = kv["PIER"].text
        if "MESHMAXSIZE" in kv and r.si(kv["MESHMAXSIZE"], "length"):
            sd["mesh_size"] = r.si(kv["MESHMAXSIZE"], "length")
        shells.append(sd)
        area_story[label] = uid
    for label, corners in openings:
        parent = None
        if "~O" in label:
            parent = next((s for s in shells
                           if s["uid"] == label.rsplit("~O", 1)[0]), None)
        if parent is None:
            parent = _containing(shells, corners)
        if parent is None:
            w.append(f"opening {label!r}: no containing area; skipped")
            continue
        parent["openings"].append(_opening(parent["corners"], corners))
    d["shells"] = shells

    # ----------------------------------------------------------- links
    links = []
    lassign = {t[1].text: _kv(t, 2) for t in r.rows("LINKASSIGN")
               if len(t) >= 2}
    from skyframe.io.e2k_write import link_axis_perm
    for t in r.rows("LINK"):
        if len(t) < 2:
            continue
        kv = {}
        toks = t[2:]
        for i, tok in enumerate(toks):
            if tok.text.upper() in ("POINT1", "POINT2") and i + 2 < len(toks):
                kv[tok.text.upper()] = (toks[i + 1].text, toks[i + 2].text)
        if "POINT1" not in kv or "POINT2" not in kv:
            continue
        a, b = xyz(*kv["POINT1"]), xyz(*kv["POINT2"])
        if a is None or b is None:
            w.append(f"link {t[1].text!r}: unknown point; skipped")
            continue
        pa = lassign.get(t[1].text, {})
        pname = pa["PROPERTY"].text if "PROPERTY" in pa else \
            (pa["LINKPROP"].text if "LINKPROP" in pa else "")
        prop = lprops.get(pname, {"type": "elastic", "k": {}})
        k6 = [0.0] * 6
        perm = link_axis_perm(a, b) or (2, 0, 1)
        for j, kv_ in prop["k"].items():
            gi = perm[j % 3] + (3 if j >= 3 else 0)
            k6[gi] = kv_
        links.append({"uid": t[1].text, "pi": a, "pj": b, "stiffness": k6,
                      "link_type": prop["type"], "params": {}})
        if prop["type"] != "elastic":
            w.append(f"link {t[1].text!r}: {prop['type']} parameters not "
                     "in e2k — defaults needed")
    d["links"] = links

    # ------------------------------------------------------- point assigns
    supports, springs, jdiaph, nmasses = [], [], [], []
    jaxes: List[dict] = []
    diaph_types = {t[1].text: ("semi_rigid" if len(t) > 3 and
                               t[3].text.upper().startswith("SEMI")
                               else "rigid")
                   for t in r.rows("DIAPHRAGM") if len(t) >= 2}
    for t in r.rows("POINTASSIGN"):
        if len(t) < 3:
            continue
        kv = _kv(t, 3)
        p = xyz(t[1].text, t[2].text)
        if p is None:
            continue
        if "RESTRAINT" in kv:
            labs = kv["RESTRAINT"].text.upper().split()
            restr = [1 if dof in labs else 0 for dof in _DOFS]
            if any(restr):
                supports.append({"point": p, "restraints": restr})
        if "SPRINGPROP" in kv and kv["SPRINGPROP"].text in psprings:
            springs.append({"point": p,
                            "stiffness": psprings[kv["SPRINGPROP"].text]})
        if "DIAPH" in kv:
            jdiaph.append({"point": p, "diaphragm": kv["DIAPH"].text})
        ang_ = _e2kx.parse_joint_axes(kv)          # Wave B joint axes
        if ang_ is not None:
            jaxes.append({"point": p, "angles": ang_})
        mass = {}
        for key, fld, qty in (("MASSUX", "mx", "mass"),
                              ("MASSUY", "my", "mass"),
                              ("MASSUZ", "mz", "mass"),
                              ("MASSRX", "mrx", "rot_mass"),
                              ("MASSRY", "mry", "rot_mass"),
                              ("MASSRZ", "mrz", "rot_mass")):
            if key in kv:
                mass[fld] = r.si(kv[key], qty) or 0.0
        if mass:
            nm = {"point": p, "mx": 0.0, "my": 0.0, "mz": 0.0}
            nm.update(mass)
            nmasses.append(nm)
    _base_fixity(d, supports, members, shells, links)
    d["spring_supports"] = springs
    if jaxes:
        d["joint_local_axes"] = jaxes
    d["nodal_masses"] = nmasses
    _diaphragms(d, jdiaph, diaph_types, stories, shells, pts)

    # ---------------------------------------------------------- patterns
    pats: Dict[str, dict] = {}
    for t in r.rows("LOADPATTERN"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        typ = kv["TYPE"].text.upper() if "TYPE" in kv else "OTHER"
        pats[t[1].text] = {"name": t[1].text,
                           "kind": _e2kx.pattern_kind(typ),  # Wave B
                           "member_udls": [], "nodal_loads": [],
                           "story_forces": [], "member_loads": [],
                           "area_loads": [], "thermal_loads": [],
                           "accidental_torsion": False, "ecc": 0.05,
                           "self_weight_factor":
                               _num(kv.get("SELFWEIGHT")) or 0.0}

    def pat(name: str) -> Optional[dict]:
        if name not in pats:
            w.append(f"load on unknown pattern {name!r} skipped")
            return None
        return pats[name]
    for head in ("SEISMIC", "WIND"):
        for t in r.rows(head):
            if len(t) < 3:
                continue
            p = pats.get(t[1].text)
            if p is None:
                continue
            kv = _kv(t, 2)
            if "STORY" in kv:
                p["story_forces"].append({
                    "story": kv["STORY"].text,
                    "fx": r.si(kv.get("FX"), "force") or 0.0,
                    "fy": r.si(kv.get("FY"), "force") or 0.0})
            else:
                kv = _kv(t, 3)
                if "ECCRATIO" in kv:
                    p["accidental_torsion"] = True
                    p["ecc"] = _num(kv["ECCRATIO"])
    for t in r.rows("POINTLOAD"):
        if len(t) < 3:
            continue
        kv = _kv(t, 3)
        p = pat(kv["LC"].text) if "LC" in kv else None
        xyz_ = xyz(t[1].text, t[2].text)
        if p is None or xyz_ is None:
            continue
        if "TYPE" in kv and kv["TYPE"].text.upper() != "FORCE":
            w.append(f"point load type {kv['TYPE'].text!r} not supported")
            continue
        p["nodal_loads"].append({
            "point": xyz_, **{k.lower(): r.si(kv.get(k), "force" if k[0] ==
                                               "F" else "moment") or 0.0
                              for k in ("FX", "FY", "FZ", "MX", "MY",
                                        "MZ")}})
    for t in r.rows("LINELOAD"):
        if len(t) < 3:
            continue
        kv = _kv(t, 3)
        uid = member_story.get((t[1].text, t[2].text))
        p = pat(kv["LC"].text) if "LC" in kv else None
        if p is None or uid is None:
            continue
        typ = kv["TYPE"].text.upper() if "TYPE" in kv else "UNIFF"
        dirn = _FDIR.get(kv["DIR"].text.upper() if "DIR" in kv else "GRAV")
        if dirn is None:
            w.append(f"frame load direction {kv['DIR'].text!r} on {uid!r} "
                     "not supported")
            continue
        if typ == "UNIFF":
            wv = r.si(kv.get("FVAL"), "line_force") or 0.0
            if dirn == "gravity":
                p["member_udls"].append({"member_uid": uid, "w": wv})
            else:
                p["member_loads"].append({"member_uid": uid, "kind": "udl",
                                          "w": wv, "w2": 0.0, "a": 0.0,
                                          "b": 1.0, "direction": dirn})
        elif typ == "TRAPF":
            w1 = r.si(kv.get("FSTART"), "line_force") or 0.0
            w2 = r.si(kv.get("FEND"), "line_force") or 0.0
            a = _num(kv.get("RDSTART")) or 0.0
            b = _num(kv.get("RDEND")) if "RDEND" in kv else 1.0
            if w1 == w2:
                p["member_loads"].append({"member_uid": uid, "kind": "udl",
                                          "w": w1, "w2": 0.0, "a": a,
                                          "b": b, "direction": dirn})
            else:
                p["member_loads"].append({"member_uid": uid,
                                          "kind": "trapezoid", "w": w1,
                                          "w2": w2, "a": a, "b": b,
                                          "direction": dirn})
        elif typ == "POINTF":
            p["member_loads"].append({
                "member_uid": uid, "kind": "point",
                "w": r.si(kv.get("FVAL"), "force") or 0.0, "w2": 0.0,
                "a": _num(kv.get("RDIST")) or 0.0, "b": 1.0,
                "direction": dirn})
        else:
            w.append(f"frame load type {typ!r} not supported")
    load_sets: Dict[str, dict] = {}
    for t in r.rows("SHELLUNIFORMLOADSET"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        if "LOADPAT" in kv:
            load_sets.setdefault(t[1].text, {})[kv["LOADPAT"].text] = \
                r.si(kv.get("VALUE"), "pressure") or 0.0
    if load_sets:
        d["shell_load_sets"] = load_sets
    for t in r.rows("AREALOAD"):
        if len(t) < 3:
            continue
        uid = area_story.get(t[1].text)
        if uid is None:
            continue
        kv = _kv(t, 3)
        typ = kv["TYPE"].text.upper() if "TYPE" in kv else "UNIFF"
        if typ == "UNIFLOADSET":
            setname = t[5].text if len(t) > 5 else ""
            for s in shells:
                if s["uid"] == uid:
                    s["load_set"] = setname
            continue
        p = pat(kv["LC"].text) if "LC" in kv else None
        dirn = _ADIR.get(kv["DIR"].text.upper() if "DIR" in kv else "GRAV")
        if p is None or dirn is None:
            continue
        ad = {"region_uid": uid, "q": r.si(kv.get("FVAL"), "pressure")
              or 0.0}
        if dirn != "gravity":
            ad["direction"] = dirn
        p["area_loads"].append(ad)
    d["patterns"] = pats

    # ---------------------------------------------------------- options
    for t in r.rows("ACTIVEDOF"):
        if len(t) > 1:
            d["active_dof"] = [x for x in t[1].text.upper().split()
                               if x in _DOFS]
    po = None
    for t in r.rows("PDELTA"):
        kv = _kv(t, 1)
        meth = _PDM.get(kv["METHOD"].text.upper() if "METHOD" in kv
                        else "NONE", "none")
        if meth != "none":
            po = {"method": meth, "load_factors": {},
                  "max_iterations": int(_num(kv.get("MAXITER")) or 2),
                  "tolerance": _num(kv.get("TOL")) or 0.001,
                  "include_in": "all_linear"}
    for t in r.rows("PDELTALOAD"):
        if po is not None and len(t) >= 2:
            po["load_factors"][t[1].text] = _num(_kv(t, 2).get("SF")) or 1.0
    if po is not None:
        d["pdelta_options"] = po
    for t in r.rows("MASSSOURCE"):
        kv = _kv(t, 2)
        if "ISDEFAULT" in kv and not _bool(kv["ISDEFAULT"]):
            continue
        d["mass_options"] = {
            "self_mass": _bool(kv.get("INCLUDEELEMENTS")) if
            "INCLUDEELEMENTS" in kv else True,
            "patterns": _bool(kv.get("INCLUDELOADS")) if
            "INCLUDELOADS" in kv else True,
            "include_lateral": _bool(kv.get("INCLUDELATERALMASS")) if
            "INCLUDELATERALMASS" in kv else True,
            "include_vertical": _bool(kv.get("INCLUDEVERTICALMASS")),
            "lump_at_stories": _bool(kv.get("LUMPATSTORIES")) if
            "LUMPATSTORIES" in kv else True}
        src = {}
        for t2 in r.rows("MASSSOURCELOAD"):
            if len(t2) >= 4 and t2[1].text == t[1].text and \
                    _num(t2[3]) is not None:
                src[t2[2].text] = _num(t2[3])
        d["mass_source"] = src
        break

    # ---------------------------------------------------------- functions
    spec_fn: Dict[str, dict] = {}
    hist_fn: Dict[str, dict] = {}
    for t in r.rows("FUNCTION"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        name = t[1].text
        if "FUNCTYPE" in kv:
            if kv["FUNCTYPE"].text.upper().startswith("SPEC"):
                spec_fn[name] = {"name": name, "points": [],
                                 "damping": _num(kv.get("DAMPRATIO"))
                                 if "DAMPRATIO" in kv else 0.05}
            else:
                hist_fn[name] = {"name": name, "values": [], "_t": [],
                                 "dt": _num(kv.get("TIMESTEP"))}
            continue
        if name in spec_fn:
            per = kv.get("PERIOD") or kv.get("TIMEVAL") or kv.get("TIME")
            if per is not None and "VALUE" in kv:
                spec_fn[name]["points"].append([_num(per),
                                                _num(kv["VALUE"])])
        elif name in hist_fn:
            tm = kv.get("TIME") or kv.get("TIMEVAL")
            if tm is not None and "VALUE" in kv:
                hist_fn[name]["_t"].append(_num(tm))
                hist_fn[name]["values"].append(r.si(kv["VALUE"], "accel"))
    for f in hist_fn.values():
        ts = f.pop("_t")
        if f["dt"] is None:
            f["dt"] = (ts[1] - ts[0]) if len(ts) > 1 else 0.01
    spec_fn = {k: v for k, v in spec_fn.items() if v["points"]}
    hist_fn = {k: v for k, v in hist_fn.items() if v["values"]}
    if spec_fn:
        d["spectrum_functions"] = spec_fn
    if hist_fn:
        d["th_functions"] = hist_fn

    # ---------------------------------------------------------- load cases
    cases_raw: Dict[str, dict] = {}
    for t in r.rows("LOADCASE"):
        if len(t) < 2:
            continue
        c = cases_raw.setdefault(t[1].text, {"type": "", "pats": {},
                                             "accel": [], "kv": {}})
        kv = _kv(t, 2)
        if "TYPE" in kv:
            c["type"] = kv["TYPE"].text.upper()
        if "LOADPAT" in kv:
            c["pats"][kv["LOADPAT"].text] = _num(kv.get("SF")) or 0.0
        if "ACCEL" in kv:
            c["accel"].append((kv["ACCEL"].text.upper(),
                               kv["FUNC"].text if "FUNC" in kv else "",
                               _num(kv.get("SF")) or 1.0))
            c.setdefault("ang", []).append(_num(kv.get("ANG")) or 0.0)
        for k, v in kv.items():
            if k not in ("LOADPAT", "SF", "ACCEL", "FUNC", "ANG"):
                c["kv"][k] = v
    cases, rs_cases, th_cases, bk = {}, {}, {}, {}
    g_file = G_SI / r.u.factor("accel")
    num_modes = 0
    for name, c in cases_raw.items():
        typ, kv = c["type"], c["kv"]
        if typ.startswith("MODAL"):
            num_modes = int(_num(kv.get("MAXMODES")) or 12)
        elif typ in ("LINEAR STATIC", "NONLINEAR STATIC"):
            geo = "linear"
            if "NLGEOMTYPE" in kv:
                gt = kv["NLGEOMTYPE"].text.upper()
                geo = "pdelta" if gt == "PDELTA" else (
                    "corotational" if "LARGE" in gt else "linear")
            cases[name] = {"name": name, "patterns": c["pats"],
                           "pdelta": False, "pdelta_gravity": None,
                           "geometric": geo}
        elif typ == "RESPONSE SPECTRUM":
            for acc, fn, sf in c["accel"][:1]:
                f = spec_fn.get(fn)
                rs_cases[name] = {
                    "name": name, "direction": "Y" if acc in ("U2", "UY")
                    else ("Z" if acc in ("U3", "UZ") else "X"),  # RS U3
                    "spectrum": [] if f else [[0.0, 0.0]],
                    "function": fn if f else "",
                    "scale": sf / g_file,
                    "combo_method": _RS.get(kv["MODALCOMBO"].text.upper(),
                                            "CQC") if "MODALCOMBO" in kv
                    else "CQC",
                    "damping": _num(kv.get("CONSTDAMP")) if "CONSTDAMP"
                    in kv else 0.05}
                # Wave B: several ACCEL rows -> loads; ANG / ECCRATIO /
                # DIRCOMBO (io/e2k_ext.py)
                _e2kx.rs_read(rs_cases[name], c["accel"], c.get("ang", []),
                              kv, g_file, spec_fn)
        elif "HISTORY" in typ:
            for acc, fn, sf in c["accel"][:1]:
                f = hist_fn.get(fn)
                if f is None:
                    w.append(f"TH case {name!r}: function {fn!r} unknown")
                    continue
                th_cases[name] = {
                    "name": name, "direction": "Y" if acc in ("U2", "UY")
                    else "X", "accel": [], "dt": f["dt"],
                    "function": fn, "scale": sf,
                    "damping": _num(kv.get("CONSTDAMP")) if "CONSTDAMP"
                    in kv else 0.05,
                    "nonlinear": typ.startswith("NONLINEAR")}
        elif typ == "BUCKLING":
            bk[name] = {"name": name, "gravity": c["pats"],
                        "num_modes": int(_num(kv.get("BUCKLINGMODES")) or 6)}
        else:
            w.append(f"load case {name!r} of type {typ!r} not supported")
    d["num_modes"] = num_modes
    d["cases"] = cases
    d["rs_cases"] = rs_cases
    d["th_cases"] = th_cases
    d["buckling_cases"] = bk

    combos: Dict[str, dict] = {}
    for t in r.rows("COMBO"):
        if len(t) < 2:
            continue
        kv = _kv(t, 2)
        c = combos.setdefault(t[1].text, {"name": t[1].text, "cases": {},
                                          "combo_type": "add"})
        if "TYPE" in kv:
            c["combo_type"] = _COMBO.get(kv["TYPE"].text.upper(), "add")
        for key in ("LOADCASE", "LOADCOMBO"):
            if key in kv:
                c["cases"][kv[key].text] = _num(kv.get("SF")) or 0.0
    d["combos"] = combos

    # ---------------------------------------------------------- groups
    groups: Dict[str, dict] = {}
    link_ids = {lk["uid"] for lk in links}
    for t in r.rows("GROUP"):
        if len(t) < 2:
            continue
        g = groups.setdefault(t[1].text, {"members": [], "shells": [],
                                          "links": [], "points": [],
                                          "color": ""})
        kind = t[2].text.upper() if len(t) > 2 else ""
        if kind == "COLOR" and len(t) > 3:
            g["color"] = t[3].text
        elif kind == "LINE" and len(t) > 4:
            uid = member_story.get((t[3].text, t[4].text))
            if uid:
                g["members"].append(uid)
        elif kind == "AREA" and len(t) > 4:
            uid = area_story.get(t[3].text)
            if uid:
                g["shells"].append(uid)
        elif kind == "LINK" and len(t) > 3 and t[3].text in link_ids:
            g["links"].append(t[3].text)
        elif kind == "POINT" and len(t) > 4:
            p = xyz(t[3].text, t[4].text)
            if p:
                g["points"].append(p)
    if groups:
        d["groups"] = groups

    # ---------------------------------------------------------- build
    try:
        mdl = BuildingModel.from_dict(d)
    except Exception as exc:                          # noqa: BLE001
        if r.patches and apply_extensions:
            mdl = BuildingModel.from_dict(apply_patches(d, r.patches))
            return mdl, w
        raise ValueError(f"e2k import produced an invalid model: {exc}") \
            from exc
    if r.patches and apply_extensions:
        mdl = BuildingModel.from_dict(apply_patches(mdl.to_dict(),
                                                    r.patches))
    elif r.patches:
        w.append(f"{len(r.patches)} SkyFrame extension line(s) ignored")
    return mdl, w


# --------------------------------------------------------------------------- #
def _base_fixity(d, supports, members, shells, links) -> None:
    """Restraints identical at every lowest-z object point and nowhere else
    -> automatic base fixity (fixed / pinned); otherwise explicit."""
    zs = [p[2] for m in members for p in (m["pi"], m["pj"])] + \
        [c[2] for s in shells for c in s["corners"]] + \
        [p[2] for lk in links for p in (lk["pi"], lk["pj"])]
    d["base_fixity"] = "fixed"
    d["supports"] = supports
    if not supports or not zs:
        return
    zmin = min(zs)
    base_pts = {tuple(p) for m in members for p in (m["pi"], m["pj"])
                if abs(p[2] - zmin) < 1e-9}
    base_pts |= {tuple(c) for s in shells for c in s["corners"]
                 if abs(c[2] - zmin) < 1e-9}
    base_pts |= {tuple(p) for lk in links for p in (lk["pi"], lk["pj"])
                 if abs(p[2] - zmin) < 1e-9}
    restr = {tuple(s["restraints"]) for s in supports}
    sup_pts = {tuple(s["point"]) for s in supports}
    if len(restr) == 1 and sup_pts == base_pts:
        r = next(iter(restr))
        if r == (1, 1, 1, 1, 1, 1):
            d["base_fixity"] = "fixed"
            d["supports"] = []
        elif r == (1, 1, 1, 0, 0, 0):
            d["base_fixity"] = "pinned"
            d["supports"] = []


def _diaphragms(d, jdiaph, types, stories, shells, pts) -> None:
    """Legacy one-diaphragm-per-story detection (see CONTRACT)."""
    elev = {s["name"]: s["elevation"] for s in stories}
    shell_d = {s.get("diaphragm") for s in shells if s.get("diaphragm")}
    # all object points per story elevation
    allp: Dict[float, set] = {}
    for m in d["members"]:
        for p in (m["pi"], m["pj"]):
            allp.setdefault(p[2], set()).add(tuple(p))
    for s in shells:
        for c in s["corners"]:
            allp.setdefault(c[2], set()).add(tuple(c))
    for lk in d["links"]:
        for p in (lk["pi"], lk["pj"]):
            allp.setdefault(p[2], set()).add(tuple(p))
    by_name: Dict[str, Dict[float, set]] = {}
    for jd in jdiaph:
        by_name.setdefault(jd["diaphragm"], {}).setdefault(
            jd["point"][2], set()).add(tuple(jd["point"]))
    cands = [name for name, per_z in by_name.items()
             if name not in shell_d and types.get(name, "rigid") == "rigid"
             and all(per_z[z] == allp.get(z, set()) for z in per_z)]
    legacy_name = None
    if "D1" in cands:
        legacy_name = "D1"
    elif len(by_name) == 1 and cands:
        legacy_name = cands[0]
    covered = set()
    named_j = []
    for jd in jdiaph:
        if jd["diaphragm"] == legacy_name:
            covered.add(jd["point"][2])
        else:
            named_j.append(jd)
    named_types = {n: {"type": t} for n, t in types.items()
                   if n != legacy_name and (n in shell_d or any(
                       j["diaphragm"] == n for j in named_j))}
    d["diaphragms"] = named_types
    d["joint_diaphragms"] = named_j
    named_z = {j["point"][2] for j in named_j} | \
        {s["corners"][0][2] for s in shells if s.get("diaphragm")}
    sd = {}
    any_rigid = False
    for s in stories:
        z = s["elevation"]
        if z in covered:
            any_rigid = True
        elif z not in named_z:
            sd[s["name"]] = "none"
    if any_rigid or not stories:
        d["rigid_diaphragms"] = True
        d["diaphragm"] = "rigid"
        d["story_diaphragm"] = sd
    else:
        d["rigid_diaphragms"] = False
        d["diaphragm"] = "rigid"
        d["story_diaphragm"] = {}


def _containing(shells, corners):
    """First region whose plane holds the opening and contains its
    centroid (plan / elevation test on the dominant projection)."""
    import numpy as np
    cen = np.mean(np.array(corners, float), axis=0)
    for s in shells:
        c = np.array(s["corners"], float)
        n = np.cross(c[1] - c[0], c[-1] - c[0])
        if np.linalg.norm(n) < 1e-12:
            continue
        n = n / np.linalg.norm(n)
        if any(abs(np.dot(np.array(p) - c[0], n)) > 1e-6 for p in corners):
            continue
        ax = int(np.argmax(np.abs(n)))
        keep = [i for i in range(3) if i != ax]
        poly = c[:, keep]
        x, y = cen[keep]
        inside = False
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            if (y1 > y) != (y2 > y) and \
                    x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
        if inside:
            return s
    return None


def _opening(region_corners, corners) -> dict:
    """u/v rectangle for a 4-corner parallelogram region when the opening
    maps to an axis-aligned u/v box, else a polygon opening."""
    import numpy as np
    if len(region_corners) == 4 and len(corners) == 4:
        c = np.array(region_corners, float)
        e1, e3 = c[1] - c[0], c[3] - c[0]
        para = np.allclose(c[2], c[1] + e3, atol=1e-9)
        if para:
            M = np.array([[e1 @ e1, e1 @ e3], [e1 @ e3, e3 @ e3]])
            uv = []
            for p in corners:
                rhs = np.array([e1 @ (np.array(p) - c[0]),
                                e3 @ (np.array(p) - c[0])])
                uv.append(np.linalg.solve(M, rhs))
            us = sorted({round(float(a[0]), 12) for a in uv})
            vs = sorted({round(float(a[1]), 12) for a in uv})
            if len(us) == 2 and len(vs) == 2:
                return {"u0": us[0], "v0": vs[0], "u1": us[1], "v1": vs[1]}
    return {"u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0,
            "polygon": [list(p) for p in corners]}
