"""ETABS ``.e2k`` (v20+ text) EXPORT of a SkyFrame model.

``export_e2k(model, units=("KN", "M"), include=None, extensions=True)``
returns ``(text, report)``.  The text is written in the ETABS e2k dialect
(``$``-headed sections, ``KEYWORD "name" KEY value`` records) that
:func:`skyframe.io.e2k_read.read_e2k` reads back.  Everything ETABS can
express is written as native records; whatever remains (SkyFrame-only
fields, values the native records lose) is carried, when ``extensions`` is
on, as ``$SF {json}`` comment lines (ETABS treats ``$`` lines as comments)
so that export -> import reproduces ``model.to_dict()`` exactly in kN-m (to
1e-12 relative in other unit systems).

See CONTRACT "Model exchange: E2K / IFC / DXF / Excel" for the record
dialect and the list of features that cannot be expressed in E2K.
"""

from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Tuple

from skyframe.io.exchange_common import (
    G_SI, Units, diff_patches, fmt, model_dict, q, summarize_patches)

from skyframe.io import e2k_ext as _e2kx  # noqa: E402  (Wave B records)
from skyframe.io import e2k_nonlinear as _e2knl  # noqa: E402  (Wave C)

from skyframe.io import e2k_ggrid as _e2kgg  # noqa: E402  (Wave C general grids)

__all__ = ["export_e2k", "INCLUDE_KEYS", "STEEL_DB_FILES", "SHAPE_NAMES"]

E2K_VERSION = "20.0.0"

# what the options dialog may switch off (everything on by default)
INCLUDE_KEYS = ("grids", "loads", "cases", "combos", "groups", "mass_source")

# SkyFrame steel database id -> the ETABS property file it resolves from
STEEL_DB_FILES = {"aisc": "AISC16.xml", "eurocode": "Euro.xml",
                  "bs": "BSShapes2006.xml", "as_nzs": "Australia-NZ.xml",
                  "is808": "Indian.xml"}

# SkyFrame parametric type -> ETABS SHAPE name
SHAPE_NAMES = {"I": "Steel I/Wide Flange", "I_mono": "Steel I/Wide Flange",
               "channel": "Steel Channel", "angle": "Steel Angle",
               "double_angle": "Steel Double Angle", "tee": "Steel Tee",
               "rect_tube": "Steel Tube", "round_tube": "Steel Pipe",
               "rect_solid": "Rectangular", "round_solid": "Circle",
               "plate": "Steel Plate", "tee_concrete": "Concrete Tee",
               "L_concrete": "Concrete L"}

MAT_TYPES = {"steel": "Steel", "concrete": "Concrete", "rebar": "Rebar",
             "tendon": "Tendon", "masonry": "Masonry",
             "aluminum": "Aluminum", "coldformed": "ColdFormed",
             "other": "NoDesign"}

PATTERN_TYPES = {"dead": "Dead", "live": "Live", "quake": "Seismic",
                 "wind": "Wind", "other": "Other", "notional": "Notional",
                 "prestress": "Prestress"}
from skyframe.core.combos_codes import PATTERN_KIND_LABELS as _PKL  # noqa
PATTERN_TYPES.update({k: v for k, v in _PKL.items()      # pattern types
                      if k not in PATTERN_TYPES})

SHELL_MODEL_TYPES = {"shell": "ShellThin", "shell_thin": "ShellThin",
                     "shell_thick": "ShellThick", "membrane": "Membrane",
                     "plate_thin": "PlateThin", "plate_thick": "PlateThick"}

COMBO_TYPES = {"add": "Linear Add", "envelope": "Envelope",
               "abs": "Absolute Add", "srss": "SRSS", "range": "Range Add"}

RS_METHODS = {"CQC": "CQC", "SRSS": "SRSS", "ABS": "ABS", "GMC": "GMC",
              "NRC10": "NRC 10 Percent", "DSC": "Double Sum"}

PDELTA_METHODS = {"none": "NONE", "non_iterative_mass": "NONITERATIVE",
                  "iterative_loads": "ITERATIVE"}

FRAME_LOAD_DIRS = {"gravity": "GRAV", "local_y": "2", "global_x": "X",
                   "global_y": "Y", "global_z": "Z"}
AREA_LOAD_DIRS = {"gravity": "GRAV", "global_x": "X", "global_y": "Y",
                  "global_z": "Z", "local_1": "1", "local_2": "2",
                  "local_3": "3"}

_DOFS = ("UX", "UY", "UZ", "RX", "RY", "RZ")
_LDOFS = ("U1", "U2", "U3", "R1", "R2", "R3")
_ZTOL = 1e-9


# --------------------------------------------------------------------------- #
# geometry helpers shared with the reader
# --------------------------------------------------------------------------- #
def link_axis_perm(pi, pj) -> Optional[Tuple[int, int, int]]:
    """Global translation index (0=X,1=Y,2=Z) of ETABS link local 1/2/3 for
    an axis-aligned link (default ETABS link axes), or None."""
    d = [pj[k] - pi[k] for k in range(3)]
    n = math.sqrt(sum(c * c for c in d))
    if n < 1e-9 or (abs(d[0]) < 1e-9 and abs(d[1]) < 1e-9):
        return (2, 0, 1)                     # vertical / zero length
    if abs(d[2]) > 1e-9:
        return None
    if abs(d[1]) < 1e-9:
        return (0, 2, 1)                     # along X: 2 = Z, 3 = -/+Y
    if abs(d[0]) < 1e-9:
        return (1, 2, 0)                     # along Y: 2 = Z, 3 = +/-X
    return None


def section_kind(sd: dict, model) -> str:
    """How a frame section dict is written: builtup | designer | db |
    param | rect | general."""
    from skyframe.core.model import FrameSection
    name = sd["name"]
    if sd.get("shape") == "builtup" and name in (model.builtup_sections or {}):
        return "builtup"
    if name in (model.designer_sections or {}):
        return "designer"
    if sd.get("kind", "prismatic") != "prismatic":
        return "general"
    sec = model.sections[name]
    keys = ("A", "I33", "I22", "J", "b", "h", "As2", "As3", "shape", "dims",
            "principal")

    def same(other) -> bool:
        return all(getattr(other, k) == getattr(sec, k) for k in keys)

    if sec.db_ref and sec.db_ref.get("standard") in STEEL_DB_FILES:
        try:
            from skyframe.core.steel_db import section_from_db
            ref = section_from_db(sec.db_ref["standard"], sec.db_ref["name"],
                                  sec.material, name)
            if same(ref):
                return "db"
        except Exception:                      # noqa: BLE001
            pass
    if sec.shape in SHAPE_NAMES and isinstance(sec.dims, dict):
        try:
            from skyframe.core.section_ext import section_from_dims
            ref = section_from_dims(name, sec.material, sec.shape, sec.dims)
            if same(ref) and not sec.db_ref:
                return "param"
        except Exception:                      # noqa: BLE001
            pass
    if sec.shape is None and sec.b > 0 and sec.h > 0 \
            and sec.As2 is None and sec.As3 is None:
        ref = FrameSection.rectangular(name, sec.material, sec.b, sec.h)
        if same(ref):
            return "rect"
    return "general"


def shape_keys(typ: str, dims: dict) -> List[Tuple[str, float]]:
    """ETABS dimension keywords for a parametric shape (SI values)."""
    g = dims.get
    if typ == "I":
        out = [("D", g("d")), ("B", g("bf")), ("TF", g("tf")),
               ("TW", g("tw"))]
        if g("r") is not None:
            out.append(("FILLETRADIUS", g("r")))
        return out
    if typ == "I_mono":
        return [("D", g("d")), ("B", g("bf_top")), ("TF", g("tf_top")),
                ("TW", g("tw")), ("BB", g("bf_bot")), ("TFB", g("tf_bot"))]
    if typ in ("channel", "tee"):
        return [("D", g("d")), ("B", g("bf")), ("TF", g("tf")),
                ("TW", g("tw"))]
    if typ == "angle":
        return [("D", g("d")), ("B", g("b")), ("TF", g("t")), ("TW", g("t"))]
    if typ == "double_angle":
        out = [("D", g("d")), ("B", g("b")), ("TF", g("t")), ("TW", g("t"))]
        if g("s") is not None:
            out.append(("DIS", g("s")))
        return out
    if typ == "rect_tube":
        out = [("D", g("d")), ("B", g("b")), ("TF", g("t")), ("TW", g("t"))]
        if g("r_out") is not None:
            out.append(("CORNERRADIUS", g("r_out")))
        return out
    if typ == "round_tube":
        return [("D", g("D")), ("TW", g("t"))]
    if typ in ("rect_solid", "plate"):
        return [("D", g("d")), ("B", g("b"))]
    if typ == "round_solid":
        return [("D", g("D"))]
    if typ in ("tee_concrete", "L_concrete"):
        return [("D", g("h")), ("B", g("bf")), ("TF", g("hf")),
                ("TW", g("bw"))]
    return []


# --------------------------------------------------------------------------- #
# writer
# --------------------------------------------------------------------------- #
class _Levels:
    """Story levels per tower: [(name, elevation)] bottom-up incl. base."""

    def __init__(self, model):
        from skyframe.core import towers as _tw
        self.model = model
        self.towers: List[dict] = []         # [{"name", "base", "levels"}]
        names = {s.name for s in model.stories}
        if _tw.has_towers(model):
            st = _tw.story_tower_map(model)
            for i, t in enumerate(model.towers):
                sts = [s for s in model.stories if st[s.name] == t["name"]]
                base_name = "Base" if i == 0 else f"Base_{t['name']}"
                while base_name in names:
                    base_name += "_"
                names.add(base_name)
                self.towers.append({
                    "name": t["name"],
                    "base": (base_name, float(t.get("base_elevation", 0.0))),
                    "stories": sts})
        else:
            sts = list(model.stories)
            base = (sts[0].elevation - sts[0].height) if sts else 0.0
            base_name = "Base"
            while base_name in names:
                base_name += "_"
            self.towers.append({"name": "", "base": (base_name, base),
                                "stories": sts})
        for t in self.towers:
            t["levels"] = [t["base"]] + [(s.name, s.elevation)
                                         for s in t["stories"]]
        self.story_tower = {s.name: t["name"] for t in self.towers
                            for s in t["stories"]}
        self.base_names = {t["base"][0] for t in self.towers}

    def tower_of(self, story: str, tower_tag: str = "") -> dict:
        name = tower_tag or self.story_tower.get(story, None)
        for t in self.towers:
            if t["name"] == name:
                return t
        return self.towers[0]

    @staticmethod
    def locate(levels, z: float) -> Optional[Tuple[int, float]]:
        """(level index, dz below that level) of elevation z."""
        for k, (_n, e) in enumerate(levels):
            if e == z:
                return k, 0.0
        for k, (_n, e) in enumerate(levels):
            if abs(e - z) <= _ZTOL * max(1.0, abs(z)):
                return k, 0.0
        for k, (_n, e) in enumerate(levels):
            if e > z:
                return k, e - z
        return None


class _Writer:
    def __init__(self, model, units: Units, include: Dict[str, bool]):
        from skyframe.core import frame_axes as _fa
        if _fa.model_convention(model) != "etabs":
            import copy
            model = copy.deepcopy(model)
            _fa.convert_model_to_etabs(model)
        self.m = model
        self.u = units
        self.inc = include
        self.lines: List[str] = []
        self.skipped: List[str] = []
        self.counts: Dict[str, int] = {}
        self.lv = _Levels(model)
        self.points: Dict[Tuple[float, float, float], str] = {}
        self.point_order: List[Tuple[float, float, float]] = []
        # 3D point -> (point name, level name)
        self.p3: Dict[Tuple[float, float, float], Tuple[str, str]] = {}
        self.obj_pts: set = set()            # object (not opening) points

    # ---- output helpers ------------------------------------------------- #
    def sec(self, title: str) -> None:
        self.lines.append("")
        self.lines.append(f"$ {title}")

    def rec(self, text: str) -> None:
        self.lines.append("  " + text)

    def n(self, v, qty: str = "none") -> str:
        return fmt(self.u.from_si(v, qty))

    def count(self, key: str, k: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + k

    # ---- points --------------------------------------------------------- #
    def point(self, xyz, levels) -> Optional[Tuple[str, str]]:
        """(point name, level name) for a 3D point within tower levels."""
        x, y, z = (float(v) for v in xyz)
        loc = _Levels.locate(levels, z)
        if loc is None:
            return None
        k, dz = loc
        key = (x, y, dz)
        if key not in self.points:
            self.points[key] = str(len(self.points) + 1)
            self.point_order.append(key)
        res = (self.points[key], levels[k][0])
        self.p3.setdefault((x, y, z), res)
        if not getattr(self, "_in_opening", False):
            self.obj_pts.add((x, y, z))
        return res

    def level_index(self, levels, name: str) -> int:
        for k, (n, _e) in enumerate(levels):
            if n == name:
                return k
        return 0

    # ---- sections ------------------------------------------------------- #
    def write(self) -> str:
        m = self.m
        self.lines.append(f"$ File {m.name}.e2k exported by SkyFrame "
                          "(ETABS v20+ text format)")
        self.sec("PROGRAM INFORMATION")
        self.rec(f'PROGRAM  "ETABS"  VERSION {q(E2K_VERSION)}')
        self.sec("CONTROLS")
        self.rec(f"UNITS  {q(self.u.force)}  {q(self.u.length)}  \"C\"")
        self.rec(f"TITLE2  {q(m.name)}")
        self._towers_stories()
        if self.inc.get("grids", True):
            self._grids()
        self._diaphragm_names()
        self._materials()
        self._frame_sections()
        self._shell_props()
        self._link_props()
        self._spring_props()
        _e2knl.write_hinge_props(self)           # Wave C: FRAME HINGE PROPS
        self._geometry()
        _e2knl.write_hinge_assigns(self)         # Wave C: HINGEASSIGN
        if self.inc.get("loads", True):
            self._patterns()
            self._loads()
        self._analysis_options()
        if self.inc.get("mass_source", True):
            self._mass_source()
        if self.inc.get("cases", True):
            self._functions()
            _e2knl.write_freq_functions(self)    # Wave C: freq. functions
            self._cases()
        if self.inc.get("combos", True):
            self._combos()
        if self.inc.get("groups", True):
            self._groups()
        return "\n".join(self.lines)

    def _towers_stories(self) -> None:
        from skyframe.core import towers as _tw
        multi = _tw.has_towers(self.m)
        if multi:
            self.sec("TOWERS")
            for t in self.lv.towers:
                self.rec(f"TOWER  {q(t['name'])}")
        self.sec("STORIES - IN SEQUENCE FROM TOP")
        for t in self.lv.towers:
            tw = f"  TOWER {q(t['name'])}" if multi else ""
            for s in reversed(t["stories"]):
                self.rec(f"STORY {q(s.name)}  HEIGHT "
                         f"{self.n(s.height, 'length')}{tw}  "
                         "MASTERSTORY \"Yes\"")
            bn, be = t["base"]
            self.rec(f"STORY {q(bn)}  ELEV {self.n(be, 'length')}{tw}")
        self.count("stories", len(self.m.stories))

    def _grids(self) -> None:
        grids = self.m.effective_grids()
        if not grids:
            return
        self.sec("GRIDS")
        for g in grids:
            typ = "CYLINDRICAL" if g.kind == "radial" else "CARTESIAN"
            if g.kind == "general":                   # Wave C (io/e2k_ggrid)
                typ = _e2kgg.GENERAL_TYPE
            extra = ""
            if g.origin[0] or g.origin[1]:
                extra += (f"  UX {self.n(g.origin[0], 'length')}"
                          f"  UY {self.n(g.origin[1], 'length')}")
            if g.rotation:
                extra += f"  ROTATION {fmt(g.rotation)}"
            self.rec(f"GRIDSYSTEM {q(g.name)}  TYPE {q(typ)}  "
                     f"BUBBLESIZE 1.25{extra}")
            if g.kind == "general":                   # Wave C (io/e2k_ggrid)
                _e2kgg.write_lines(self, g)
            elif g.kind == "radial":
                for i, r in enumerate(g.radii):
                    self.rec(f"GRID {q(g.name)}  LABEL {q(f'R{i + 1}')}  "
                             f"DIR \"R\"  COORD {self.n(r, 'length')}  "
                             "VISIBLE \"Yes\"  BUBBLELOC \"End\"")
                for t in g.theta_deg:
                    self.rec(f"GRID {q(g.name)}  LABEL {q(fmt(t))}  "
                             f"DIR \"T\"  COORD {fmt(t)}  VISIBLE \"Yes\"  "
                             "BUBBLELOC \"End\"")
            else:
                for lab, x in zip(g.x_labels, g.x_lines):
                    self.rec(f"GRID {q(g.name)}  LABEL {q(lab)}  DIR \"X\"  "
                             f"COORD {self.n(x, 'length')}  VISIBLE \"Yes\""
                             "  BUBBLELOC \"End\"")
                for lab, y in zip(g.y_labels, g.y_lines):
                    self.rec(f"GRID {q(g.name)}  LABEL {q(lab)}  DIR \"Y\"  "
                             f"COORD {self.n(y, 'length')}  VISIBLE \"Yes\""
                             "  BUBBLELOC \"Start\"")
        self.count("grid_systems", len(grids))

    # diaphragms: legacy per-story rigid -> one rigid diaphragm "D1"
    def _diaphragm_plan(self) -> None:
        m = self.m
        self.legacy_diaph = None
        named_stories = set()
        for jd in m.joint_diaphragms or []:
            z = float(jd["point"][2])
            for s in m.stories:
                if abs(s.elevation - z) <= 1e-9:
                    named_stories.add(s.name)
        for r in m.shells:
            if getattr(r, "diaphragm", ""):
                named_stories.add(r.story)
        self.legacy_stories = []
        for s in m.stories:
            if s.name in named_stories:
                continue
            mode = m.story_diaphragm.get(s.name) or \
                ("none" if not m.rigid_diaphragms else m.diaphragm)
            if mode == "rigid":
                self.legacy_stories.append(s.name)
        if self.legacy_stories:
            name = "D1"
            while name in (m.diaphragms or {}):
                name += "_"
            self.legacy_diaph = name

    def _diaphragm_names(self) -> None:
        self._diaphragm_plan()
        items = []
        if self.legacy_diaph:
            items.append((self.legacy_diaph, "RIGID"))
        for name, dd in (self.m.diaphragms or {}).items():
            items.append((name, "SEMIRIGID" if dd.get("type") == "semi_rigid"
                          else "RIGID"))
        if not items:
            return
        self.sec("DIAPHRAGM NAMES")
        for name, typ in items:
            self.rec(f"DIAPHRAGM {q(name)}    TYPE {typ}")
        self.count("diaphragms", len(items))

    def _materials(self) -> None:
        self.sec("MATERIAL PROPERTIES")
        for mt in self.m.materials.values():
            nm = q(mt.name)
            typ = MAT_TYPES.get(mt.material_type, "NoDesign")
            extra = ""
            if mt.mass_density is not None:
                extra = f"  MASSPERVOLUME {self.n(mt.mass_density, 'mass_density')}"
            self.rec(f"MATERIAL  {nm}    TYPE {q(typ)}    WEIGHTPERVOLUME "
                     f"{self.n(mt.unit_weight, 'unit_weight')}{extra}")
            a = f"  A {fmt(mt.alpha)}" if mt.alpha is not None else ""
            sym = "Uniaxial" if mt.symmetry == "uniaxial" else "Isotropic"
            self.rec(f"MATERIAL  {nm}    SYMTYPE {q(sym)}  E "
                     f"{self.n(mt.E, 'stress')}  U {fmt(mt.nu)}{a}")
            parts = []
            if mt.fy is not None:
                parts.append(f"FY {self.n(mt.fy, 'stress')}")
            if mt.fu is not None:
                parts.append(f"FU {self.n(mt.fu, 'stress')}")
            if mt.fy is not None:
                parts.append(f"FYE {self.n(mt.Ry * mt.fy, 'stress')}")
            if mt.fu is not None:
                parts.append(f"FUE {self.n(mt.Ry * mt.fu, 'stress')}")
            if parts:
                self.rec(f"MATERIAL  {nm}    " + "  ".join(parts))
            if mt.fc is not None or mt.lightweight:
                fc = f"FC {self.n(mt.fc, 'stress')}  " if mt.fc is not None \
                    else ""
                lw = '"Yes"' if mt.lightweight else '"No"'
                lam = f"  LTWTFACT {fmt(mt.lam)}" if mt.lightweight else ""
                self.rec(f"MATERIAL  {nm}    {fc}LTWTCONC {lw}{lam}")
            if mt.damping:
                self.rec(f"MATERIAL  {nm}    DAMPRATIO {fmt(mt.damping)}")
            if mt.stress_strain:
                self.skipped.append(f"material {mt.name}: stress-strain "
                                    "curve (not expressible)")
        self.count("materials", len(self.m.materials))

    def _general(self, sd) -> str:
        n = self.n
        return (f"SHAPE \"General\"  D {n(sd.get('h', 0.0), 'length')}  "
                f"B {n(sd.get('b', 0.0), 'length')}  "
                f"AREA {n(sd['A'], 'area')}  "
                f"AS2 {n(sd.get('As2') or 0.0, 'area')}  "
                f"AS3 {n(sd.get('As3') or 0.0, 'area')}  "
                f"I33 {n(sd['I33'], 'inertia')}  "
                f"I22 {n(sd['I22'], 'inertia')}  I23 0  "
                f"J {n(sd['J'], 'inertia')}")

    def _frame_sections(self) -> None:
        m = self.m
        if not m.sections:
            return
        self.sec("FRAME SECTIONS")
        sd_records: List[Tuple[str, dict]] = []
        for name, sec in m.sections.items():
            sd = sec.to_dict()
            kind = section_kind(sd, m)
            head = f"FRAMESECTION  {q(name)}  MATERIAL {q(sec.material)}  "
            if kind == "rect":
                self.rec(head + f"SHAPE \"Concrete Rectangular\"  D "
                         f"{self.n(sec.h, 'length')}  B "
                         f"{self.n(sec.b, 'length')}")
            elif kind in ("db", "param"):
                shp = SHAPE_NAMES[sec.shape]
                mt = m.materials.get(sec.material)
                if shp in ("Rectangular", "Circle"):
                    conc = mt is not None and mt.material_type == "concrete"
                    if shp == "Rectangular":
                        shp = "Concrete Rectangular" if conc \
                            else "Steel Rectangular"
                    else:
                        shp = "Concrete Circle" if conc else "Steel Rod"
                dims = "  ".join(f"{k} {self.n(v, 'length')}"
                                 for k, v in shape_keys(sec.shape, sec.dims)
                                 if v is not None)
                extra = ""
                if kind == "db":
                    extra = (f"  FROMFILE {q(STEEL_DB_FILES[sec.db_ref['standard']])}"
                             f"  DBSECTNAME {q(sec.db_ref['name'])}")
                self.rec(head + f"SHAPE {q(shp)}  {dims}{extra}")
            else:
                self.rec(head + self._general(sd))
                if kind == "builtup":
                    sd_records.append((name, m.builtup_sections[name]))
                elif kind == "general" and sd.get("kind", "prismatic") \
                        != "prismatic":
                    self.skipped.append(f"frame section {name}: nonprismatic"
                                        " segments written as a General "
                                        "section of the first segment")
            mods = [("AMOD", sec.mod_A), ("A2MOD", sec.mod_As2),
                    ("A3MOD", sec.mod_As3), ("JMOD", sec.mod_J),
                    ("I2MOD", sec.mod_I22), ("I3MOD", sec.mod_I33),
                    ("MMOD", sec.mod_mass), ("WMOD", sec.mod_weight)]
            if any(v != 1.0 for _k, v in mods):
                self.rec(f"FRAMESECTION  {q(name)}  " + "  ".join(
                    f"{k} {fmt(v)}" for k, v in mods))
        self.count("frame_sections", len(m.sections))
        if sd_records:
            self.sec("SECTION DESIGNER SECTIONS")
            for name, bs in sd_records:
                self._sd_section(name, bs)

    def _sd_section(self, name: str, bs) -> None:
        bd = bs.to_dict() if hasattr(bs, "to_dict") else dict(bs)
        comps = bd.get("components") or []
        self.rec(f"SDSECTION {q(name)}  TYPE \"FRAME\"  DESIGNTYPE "
                 f"\"STEEL\"  NUMSHAPES {len(comps)}  MATERIAL "
                 f"{q(bd.get('material', ''))}")
        for i, c in enumerate(comps, start=1):
            mat = c.get("material") or bd.get("material", "")
            head = f"SDSECTION {q(name)}  SHAPE {i}  MATERIAL {q(mat)}  "
            xc = f"XC {self.n(c.get('x', 0.0), 'length')}  YC " \
                 f"{self.n(c.get('y', 0.0), 'length')}"
            if c.get("kind") == "plate":
                self.rec(head + f"SHAPETYPE \"STEEL PLATE\"  D "
                         f"{self.n(c['t'], 'length')}  B "
                         f"{self.n(c['b'], 'length')}  {xc}  ROTATION "
                         f"{fmt(c.get('angle', 0.0))}")
                continue
            typ = c.get("type")
            dims = c.get("dims") or {}
            shp = SHAPE_NAMES.get(typ, "Steel I/Wide Flange").upper()
            body = "  ".join(f"{k} {self.n(v, 'length')}"
                             for k, v in shape_keys(typ, dims)
                             if v is not None)
            mir = '  MIRROR2 "Yes"' if c.get("mirror") else ""
            db = ""
            if c.get("kind") == "db":
                db = (f"  FROMFILE {q(STEEL_DB_FILES.get(c.get('standard'), ''))}"
                      f"  DBSECTNAME {q(c.get('name', ''))}")
            self.rec(head + f"SHAPETYPE {q(shp)}  {body}  {xc}  ROTATION "
                     f"{fmt(c.get('rotation', 0.0))}{mir}{db}")

    def _shell_usage(self) -> Dict[str, Dict[str, set]]:
        use: Dict[str, Dict[str, set]] = {}
        for r in self.m.shells:
            u = use.setdefault(r.section, {"kind": set(), "behavior": set()})
            u["kind"].add(r.kind)
            u["behavior"].add(r.behavior)
        return use

    def _shell_props(self) -> None:
        m = self.m
        if not m.shell_sections:
            return
        self.sec("SLAB AND WALL PROPERTIES")
        use = self._shell_usage()
        self.shell_alias: Dict[str, str] = {}
        for name, ss in m.shell_sections.items():
            u = use.get(name, {"kind": {"slab"}, "behavior": {"shell"}})
            variants = []
            if "shell" in u["behavior"] or not u["behavior"]:
                variants.append((name, False))
            if "membrane" in u["behavior"]:
                alias = name if "shell" not in u["behavior"] else name + "~M"
                self.shell_alias[name] = alias
                variants.append((alias, True))
            for pname, membrane in variants:
                wall = "wall" in u["kind"] and "slab" not in u["kind"]
                mtype = "Membrane" if membrane else \
                    SHELL_MODEL_TYPES.get(ss.shell_type, "ShellThin")
                tkey = "WALLTHICKNESS" if wall else "SLABTHICKNESS"
                ptype = "Wall" if wall else "Slab"
                slab = "" if wall else '  SLABTYPE "Slab"'
                self.rec(f"SHELLPROP  {q(pname)}  PROPTYPE  {q(ptype)}  "
                         f"MATERIAL {q(ss.material)}  MODELINGTYPE "
                         f"{q(mtype)}{slab}  {tkey} "
                         f"{self.n(ss.total_thickness, 'length')}")
                mods = [("F11MOD", ss.f11), ("F22MOD", ss.f22),
                        ("F12MOD", ss.f12), ("M11MOD", ss.m11),
                        ("M22MOD", ss.m22), ("M12MOD", ss.m12),
                        ("V13MOD", ss.v13), ("V23MOD", ss.v23),
                        ("MMOD", ss.mass), ("WMOD", ss.weight)]
                if any(v != 1.0 for _k, v in mods):
                    self.rec(f"SHELLPROP  {q(pname)}  " + "  ".join(
                        f"{k} {fmt(v)}" for k, v in mods))
            if ss.layered:
                self.skipped.append(f"shell section {name}: layered "
                                    "definition (not expressible)")
            if ss.mod != 1.0:
                self.skipped.append(f"shell section {name}: modifier mod="
                                    f"{ss.mod:g} (not expressible)")
        self.count("shell_sections", len(m.shell_sections))

    def _link_props(self) -> None:
        self.link_props: Dict[str, str] = {}
        if not self.m.links:
            return
        self.sec("LINK PROPERTIES")
        types = {"elastic": "LINEAR", "damper": "DAMPER", "gap": "GAP",
                 "hook": "HOOK", "isolator": "RUBBER ISOLATOR",
                 "fp_isolator": "FRICTION ISOLATOR",
                 "triple_fp": "T/C FRICTION ISOLATOR",
                 "multilinear": "MULTILINEAR ELASTIC"}
        for lk in self.m.links:
            pname = f"LP_{lk.uid}"
            self.link_props[lk.uid] = pname
            typ = types.get(lk.link_type, "LINEAR")
            if lk.link_type != "elastic":        # Wave C: native NL links
                self.rec(f"LINKPROP  {q(pname)}  "
                         + _e2knl.link_type_header(lk))
                for row_ in _e2knl.link_prop_rows(lk, pname, self.u):
                    self.rec(row_)
                continue
            self.rec(f"LINKPROP  {q(pname)}  TYPE {q(typ)}")
            perm = link_axis_perm(lk.pi, lk.pj)
            if perm is None:
                self.skipped.append(f"link {lk.uid}: skewed axis, global "
                                    "stiffness not expressible in link "
                                    "local axes")
                perm = (2, 0, 1)
            k = [float(v) for v in lk.stiffness]
            for li, gi in enumerate(perm):
                for off, qty in ((0, "stiffness"), (3, "rot_stiffness")):
                    kv = k[gi + off]
                    if kv:
                        self.rec(f"LINKPROP  {q(pname)}  DOF "
                                 f"{q(_LDOFS[li + off])}  FIXED \"No\"  KE "
                                 f"{self.n(kv, qty)}")
        self.count("links", len(self.m.links))

    def _spring_props(self) -> None:
        m = self.m
        self.spring_names: List[str] = []
        recs = []
        used = set(m.spring_properties or {})
        for name, prop in (m.spring_properties or {}).items():
            k = prop.get("k") or [0.0] * 6
            recs.append((name, k, prop.get("kind") == "linear"
                         and not prop.get("local_axes")))
        for i, sp in enumerate(m.spring_supports):
            if sp.property is not None:
                self.spring_names.append(sp.property)
                continue
            name = f"PSPR{i + 1}"
            while name in used:
                name += "_"
            used.add(name)
            self.spring_names.append(name)
            recs.append((name, sp.stiffness, True))
            if sp.angle_deg:
                self.skipped.append(f"point spring at {tuple(sp.point)}: "
                                    "plan angle (not expressible)")
        self.area_springs: Dict[str, str] = {}
        arecs = []
        for r in m.shells:
            if r.area_spring:
                name = f"ASPR_{r.uid}"
                self.area_springs[r.uid] = name
                arecs.append((name, r.area_spring))
        if not recs and not arecs:
            return
        self.sec("SPRING PROPERTIES")
        for name, k, plain in recs:
            body = "  ".join(
                f"{d} {self.n(v, 'stiffness' if j < 3 else 'rot_stiffness')}"
                for j, (d, v) in enumerate(zip(_DOFS, k)))
            self.rec(f"POINTSPRING  {q(name)}  NONLINEARSPECOPTION  "
                     f"\"LINKS\"  {body}")
            if not plain:
                self.skipped.append(f"spring property {name}: nonlinear "
                                    "kind / local axes (not expressible)")
        for name, asp in arecs:
            co = '  NONLINEAROPT3  "Compression Only"' \
                if asp.get("compression_only") else ""
            self.rec(f"AREASPRING  {q(name)}  U3  "
                     f"{self.n(asp['kz'], 'area_spring')}{co}")

    # ---- geometry --------------------------------------------------------- #
    def _member_levels(self, mem) -> list:
        return self.lv.tower_of(mem.story, getattr(mem, "tower", ""))[
            "levels"]

    def _geometry(self) -> None:
        m = self.m
        line_recs: List[str] = []
        line_assigns: List[str] = []
        self.obj_story: Dict[str, str] = {}
        for mem in m.members:
            levels = self._member_levels(mem)
            a = self.point(mem.pi, levels)
            b = self.point(mem.pj, levels)
            if a is None or b is None:
                self.skipped.append(f"frame {mem.uid}: above the top story "
                                    "(not expressible)")
                continue
            ki = self.level_index(levels, a[1])
            kj = self.level_index(levels, b[1])
            top = max(ki, kj)
            nst = kj - ki
            kind = {"column": "COLUMN", "beam": "BEAM"}.get(mem.kind, "BRACE")
            line_recs.append(f"LINE  {q(mem.uid)}  {kind}  {q(a[0])}  "
                             f"{q(b[0])}  {nst}")
            story = levels[top][0]
            self.obj_story[mem.uid] = story
            parts = [f"SECTION {q(mem.section)}"]
            if mem.angle:
                parts.append(f"ANGLE {fmt(mem.angle)}")
            # Wave B: per-DOF RELEASE + PARTIALFIXITY (io/e2k_ext.py; a
            # legacy Mi/Mj pin gives the legacy "M2I M3I" text)
            parts += _e2kx.release_parts(mem, self.n)
            if mem.rigid_i or mem.rigid_j:
                parts.append(f"LENOFFI {self.n(mem.rigid_i, 'length')}  "
                             f"LENOFFJ {self.n(mem.rigid_j, 'length')}")
            if mem.rigid_factor != 1.0 or mem.rigid_i or mem.rigid_j:
                parts.append(f"RIGIDZONE {fmt(mem.rigid_factor)}")
            if mem.cardinal_point != 10:
                parts.append(f"CARDINALPT {int(mem.cardinal_point)}")
            if mem.no_transform_stiffness:
                parts.append('TRANSFORMSTIFFNESSFOROFFSETS "No"')
            line_assigns.append(f"LINEASSIGN  {q(mem.uid)}  {q(story)}  "
                                + "  ".join(parts))
        self.count("frames", len(line_recs))

        area_recs: List[str] = []
        area_assigns: List[str] = []
        n_open = 0
        for r in m.shells:
            levels = self.lv.tower_of(r.story, getattr(r, "tower", ""))[
                "levels"]
            pts = [self.point(c, levels) for c in r.corners]
            if any(p is None for p in pts):
                self.skipped.append(f"area {r.uid}: above the top story "
                                    "(not expressible)")
                continue
            ks = [self.level_index(levels, p[1]) for p in pts]
            top = max(ks)
            typ = "PANEL" if r.kind == "wall" else "FLOOR"
            names = "  ".join(q(p[0]) for p in pts)
            below = "  ".join(str(top - k) for k in ks)
            area_recs.append(f"AREA {q(r.uid)}  {typ}  {len(pts)}  {names}"
                             f"  {below}")
            story = levels[top][0]
            self.obj_story[r.uid] = story
            sec = r.section
            if r.behavior == "membrane":
                sec = getattr(self, "shell_alias", {}).get(sec, sec)
            parts = [f"SECTION {q(sec)}"]
            if r.diaphragm:
                parts.append(f"DIAPH {q(r.diaphragm)}")
            if r.uid in getattr(self, "area_springs", {}):
                parts.append(f"SPRINGPROP {q(self.area_springs[r.uid])}")
            if r.pier:
                parts.append(f"PIER {q(r.pier)}")
            if r.mesh_size != 1.0:
                parts.append(f"OBJMESHTYPE \"MAXSIZE\"  MESHMAXSIZE "
                             f"{self.n(r.mesh_size, 'length')}")
            area_assigns.append(f"AREAASSIGN  {q(r.uid)}  {q(story)}  "
                                + "  ".join(parts))
            for oi, op in enumerate(r.openings, start=1):
                if op.polygon:
                    ocs = [tuple(p) for p in op.polygon]
                else:
                    ocs = [r.map_uv(op.u0, op.v0), r.map_uv(op.u1, op.v0),
                           r.map_uv(op.u1, op.v1), r.map_uv(op.u0, op.v1)]
                self._in_opening = True
                ops_ = [self.point(c, levels) for c in ocs]
                self._in_opening = False
                if any(p is None for p in ops_):
                    continue
                oks = [self.level_index(levels, p[1]) for p in ops_]
                otop = max(oks)
                oname = f"{r.uid}~O{oi}"
                area_recs.append(
                    f"AREA {q(oname)}  {typ}  {len(ops_)}  "
                    + "  ".join(q(p[0]) for p in ops_) + "  "
                    + "  ".join(str(otop - k) for k in oks))
                area_assigns.append(f"AREAASSIGN  {q(oname)}  "
                                    f"{q(levels[otop][0])}  OPENING \"Yes\"")
                n_open += 1
        self.count("areas", len(m.shells))
        self.count("openings", n_open)

        link_recs: List[str] = []
        for lk in m.links:
            levels = self.lv.tower_of("", getattr(lk, "tower", ""))["levels"]
            a = self.point(lk.pi, levels)
            b = self.point(lk.pj, levels)
            if a is None or b is None:
                self.skipped.append(f"link {lk.uid}: above the top story")
                continue
            link_recs.append(f"LINK  {q(lk.uid)}  POINT1 {q(a[0])}  "
                             f"{q(a[1])}  POINT2 {q(b[0])}  {q(b[1])}")

        # point assigns (supports, springs, diaphragms)
        passign: Dict[Tuple[str, str], List[str]] = {}
        dl = self.lv.towers[0]["levels"]

        def _pa(xyz, part, what):
            p = self.p3.get(tuple(float(v) for v in xyz)) or \
                self.point(xyz, dl)
            if p is None:
                self.skipped.append(f"{what} at {tuple(xyz)}: above the top "
                                    "story")
                return
            passign.setdefault(p, []).append(part)

        if m.supports:
            for sup in m.supports:
                labs = " ".join(d for d, r_ in zip(_DOFS, sup.restraints)
                                if r_)
                _pa(sup.point, f"RESTRAINT {q(labs)}", "support")
        else:
            # automatic base fixity: restrain every point at the lowest z
            zmin = min((k[2] for k in self.obj_pts), default=None)
            labs = "UX UY UZ RX RY RZ" if m.base_fixity == "fixed" \
                else "UX UY UZ"
            for xyz, p in list(self.p3.items()):
                if xyz not in self.obj_pts:
                    continue
                if zmin is not None and abs(xyz[2] - zmin) < 1e-9:
                    passign.setdefault(p, []).append(f"RESTRAINT {q(labs)}")
        for sp, name in zip(m.spring_supports, self.spring_names):
            _pa(sp.point, f"SPRINGPROP {q(name)}", "spring")
        for jd in m.joint_diaphragms or []:
            _pa(jd["point"], f"DIAPH {q(jd['diaphragm'])}", "diaphragm")
        if self.legacy_diaph:
            for xyz, p in list(self.p3.items()):
                if p[1] in self.legacy_stories and \
                        self.points_dz(p[0]) == 0.0:
                    lst = passign.setdefault(p, [])
                    if not any(x.startswith("DIAPH") for x in lst):
                        lst.append(f"DIAPH {q(self.legacy_diaph)}")
        for nm in m.nodal_masses:
            parts = []
            for key, d in (("mx", "UX"), ("my", "UY"), ("mz", "UZ")):
                if getattr(nm, key):
                    parts.append(f"MASS{d} {self.n(getattr(nm, key), 'mass')}")
            for key, d in (("mrx", "RX"), ("mry", "RY"), ("mrz", "RZ")):
                if getattr(nm, key, 0.0):
                    parts.append(f"MASS{d} "
                                 f"{self.n(getattr(nm, key), 'rot_mass')}")
            if parts:
                _pa(nm.point, "  ".join(parts), "nodal mass")

        for pt_, txt_ in _e2kx.joint_axes_parts(m):  # Wave B joint axes
            _pa(pt_, txt_, "joint local axes")

        self.sec("POINT COORDINATES")
        for key in self.point_order:
            x, y, dz = key
            tail = f"  {self.n(dz, 'length')}" if dz else ""
            self.rec(f"POINT {q(self.points[key])}  {self.n(x, 'length')}  "
                     f"{self.n(y, 'length')}{tail}")
        self.count("points", len(self.point_order))
        if line_recs:
            self.sec("LINE CONNECTIVITIES")
            for t in line_recs:
                self.rec(t)
        if area_recs:
            self.sec("AREA CONNECTIVITIES")
            for t in area_recs:
                self.rec(t)
        if link_recs:
            self.sec("LINK CONNECTIVITIES")
            for t in link_recs:
                self.rec(t)
        if passign:
            self.sec("POINT ASSIGNS")
            for (pn, st), parts in passign.items():
                self.rec(f"POINTASSIGN  {q(pn)}  {q(st)}  "
                         + "  ".join(parts))
        if line_assigns:
            self.sec("LINE ASSIGNS")
            for t in line_assigns:
                self.rec(t)
        if area_assigns:
            self.sec("AREA ASSIGNS")
            for t in area_assigns:
                self.rec(t)
        if link_recs:
            self.sec("LINK ASSIGNS")
            for lk in m.links:
                if lk.uid in self.link_props:
                    self.rec(f"LINKASSIGN  {q(lk.uid)}  PROPERTY "
                             f"{q(self.link_props[lk.uid])}")
        if m.line_springs:
            self.skipped.append(f"{len(m.line_springs)} line spring(s) "
                                "between points (not expressible)")

    def points_dz(self, pname: str) -> float:
        for key, n in self.points.items():
            if n == pname:
                return key[2]
        return 0.0

    # ---- loads ------------------------------------------------------------ #
    def _patterns(self) -> None:
        m = self.m
        if not m.patterns:
            return
        self.sec("LOAD PATTERNS")
        for p in m.patterns.values():
            self.rec(f"LOADPATTERN {q(p.name)}  TYPE  "
                     f"{q(PATTERN_TYPES.get(p.kind, 'Other'))}  SELFWEIGHT  "
                     f"{fmt(p.self_weight_factor)}")
        self.count("load_patterns", len(m.patterns))
        user = [p for p in m.patterns.values() if p.story_forces]
        if user:
            self.sec("USER STORY LOADS")
            for p in user:
                kw = "WIND" if p.kind == "wind" else "SEISMIC"
                ecc = f"  ECCRATIO {fmt(p.ecc)}" if p.accidental_torsion \
                    else ""
                self.rec(f"{kw} {q(p.name)}  \"User Loads\"{ecc}")
                for sf in p.story_forces:
                    dname = self.legacy_diaph or "D1"
                    self.rec(f"{kw} {q(p.name)}  STORY {q(sf.story)}  "
                             f"DIAPHRAGM {q(dname)}  FX "
                             f"{self.n(sf.fx, 'force')}  FY "
                             f"{self.n(sf.fy, 'force')}  MZ 0")

    def _loads(self) -> None:
        m = self.m
        pl, fl, al = [], [], []
        dl = self.lv.towers[0]["levels"]
        for p in m.patterns.values():
            lc = q(p.name)
            for nl in p.nodal_loads:
                pt = self.p3.get(tuple(float(v) for v in nl.point)) or \
                    self.point(nl.point, dl)
                if pt is None:
                    self.skipped.append(f"point load at {nl.point}: above "
                                        "top story")
                    continue
                pl.append(
                    f"POINTLOAD  {q(pt[0])}  {q(pt[1])}  LC {lc}  TYPE "
                    f"\"FORCE\"  FX {self.n(nl.fx, 'force')}  FY "
                    f"{self.n(nl.fy, 'force')}  FZ {self.n(nl.fz, 'force')}"
                    f"  MX {self.n(nl.mx, 'moment')}  MY "
                    f"{self.n(nl.my, 'moment')}  MZ {self.n(nl.mz, 'moment')}")
            for ml in p.all_member_loads():
                st = self.obj_story.get(ml.member_uid)
                if st is None:
                    continue
                head = f"LINELOAD  {q(ml.member_uid)}  {q(st)}  "
                d = FRAME_LOAD_DIRS.get(ml.direction)
                if d is None or ml.kind not in ("udl", "trapezoid", "point"):
                    self.skipped.append(f"frame load {ml.kind}/"
                                        f"{ml.direction} on {ml.member_uid} "
                                        "(not expressible)")
                    continue
                if ml.kind == "udl" and ml.a == 0.0 and ml.b == 1.0:
                    fl.append(head + f"TYPE \"UNIFF\"  DIR {q(d)}  LC {lc}  "
                              f"FVAL {self.n(ml.w, 'line_force')}")
                elif ml.kind in ("udl", "trapezoid"):
                    w2 = ml.w if ml.kind == "udl" else ml.w2
                    fl.append(head + f"TYPE \"TRAPF\"  DIR {q(d)}  LC {lc}  "
                              f"FSTART {self.n(ml.w, 'line_force')}  FEND "
                              f"{self.n(w2, 'line_force')}  RDSTART "
                              f"{fmt(ml.a)}  RDEND {fmt(ml.b)}")
                else:
                    fl.append(head + f"TYPE \"POINTF\"  DIR {q(d)}  LC {lc}  "
                              f"FVAL {self.n(ml.w, 'force')}  RDIST "
                              f"{fmt(ml.a)}")
                if getattr(ml, "projected", False):
                    self.skipped.append(f"projected flag of a frame load on "
                                        f"{ml.member_uid}")
            for a in p.area_loads:
                st = self.obj_story.get(a.region_uid)
                d = AREA_LOAD_DIRS.get(a.direction)
                if st is None or d is None:
                    continue
                al.append(f"AREALOAD  {q(a.region_uid)}  {q(st)}  TYPE "
                          f"\"UNIFF\"  DIR {q(d)}  LC {lc}  FVAL "
                          f"{self.n(a.q, 'pressure')}")
                if a.joint_pattern is not None or a.projected:
                    self.skipped.append(f"area load on {a.region_uid}: "
                                        "joint pattern / projected flag")
            if p.thermal_loads or p.shell_thermal_loads or \
                    p.joint_temperatures:          # Wave C: TEMPERATURE
                tp_, tf_, ta_ = _e2knl.thermal_rows(self, p)
                pl += tp_
                fl += tf_
                al += ta_
            if p.ground_displacements:
                self.skipped.append(f"pattern {p.name}: ground displacements"
                                    " (not expressible)")
        sets = self.m.shell_load_sets or {}
        for r in m.shells:
            if r.load_set and r.uid in self.obj_story:
                al.append(f"AREALOAD  {q(r.uid)}  {q(self.obj_story[r.uid])}"
                          f"  TYPE \"UNIFLOADSET\"  {q(r.load_set)}")
        if pl:
            self.sec("POINT OBJECT LOADS")
            for t in pl:
                self.rec(t)
        if fl:
            self.sec("FRAME OBJECT LOADS")
            for t in fl:
                self.rec(t)
        if sets:
            self.sec("SHELL UNIFORM LOAD SETS")
            for name, pats in sets.items():
                for pat, val in pats.items():
                    self.rec(f"SHELLUNIFORMLOADSET {q(name)}  LOADPAT "
                             f"{q(pat)}  VALUE {self.n(val, 'pressure')}")
        if al:
            self.sec("SHELL OBJECT LOADS")
            for t in al:
                self.rec(t)
        self.count("loads", len(pl) + len(fl) + len(al))

    # ---- analysis options / mass source ------------------------------------ #
    def _analysis_options(self) -> None:
        m = self.m
        self.sec("ANALYSIS OPTIONS")
        self.rec(f"ACTIVEDOF {q(' '.join(m.active_dof))}")
        po = m.pdelta_options or {}
        meth = PDELTA_METHODS.get(po.get("method", "none"), "NONE")
        extra = ""
        if meth == "ITERATIVE":
            extra = (f"  MAXITER {int(po.get('max_iterations', 2))}  TOL "
                     f"{fmt(po.get('tolerance', 0.001))}")
        self.rec(f"PDELTA  METHOD {q(meth)}{extra}")
        for pat, f in (po.get("load_factors") or {}).items():
            self.rec(f"PDELTALOAD  {q(pat)}  SF {fmt(f)}")

    def _mass_source(self) -> None:
        m = self.m
        mo = m.effective_mass_options()
        yn = (lambda b: '"Yes"' if b else '"No"')
        src = m.effective_mass_source()
        self.sec("MASS SOURCE")
        self.rec(f"MASSSOURCE  \"MsSrc1\"    INCLUDEELEMENTS "
                 f"{yn(mo.get('self_mass', True))}    INCLUDEADDEDMASS "
                 f"\"Yes\"    INCLUDELOADS {yn(mo.get('patterns', True))}"
                 f"    INCLUDELATERALMASS {yn(mo.get('include_lateral'))}"
                 f"    INCLUDEVERTICALMASS {yn(mo.get('include_vertical'))}"
                 f"    LUMPATSTORIES {yn(mo.get('lump_at_stories', True))}"
                 "    ISDEFAULT \"Yes\"")
        for pat, f in src.items():
            self.rec(f"MASSSOURCELOAD  \"MsSrc1\"  {q(pat)}  {fmt(f)}")

    # ---- functions, cases, combos ----------------------------------------- #
    def _functions(self) -> None:
        m = self.m
        self.fn_name: Dict[Tuple[str, str], str] = {}
        specs = [(f.name, f.points, f.damping)
                 for f in m.spectrum_functions.values()]
        names = set(m.spectrum_functions) | set(m.th_functions)
        for rs in m.rs_cases.values():
            if not rs.function:
                nm = f"{rs.name}_SPEC"
                while nm in names:
                    nm += "_"
                names.add(nm)
                self.fn_name[("rs", rs.name)] = nm
                specs.append((nm, rs.spectrum, rs.damping))
        hists = [(f.name, f.values, f.dt) for f in m.th_functions.values()]
        for th in m.th_cases.values():
            if not th.function and th.accel:
                nm = f"{th.name}_TH"
                while nm in names:
                    nm += "_"
                names.add(nm)
                self.fn_name[("th", th.name)] = nm
                hists.append((nm, th.accel, th.dt))
        if not specs and not hists:
            return
        self.sec("FUNCTIONS")
        for nm, pts, damp in specs:
            self.rec(f"FUNCTION {q(nm)}  FUNCTYPE \"SPECTRUM\"  SPECTYPE "
                     f"\"USER\"  DAMPRATIO {fmt(damp)}")
            for t, sa in pts:
                self.rec(f"FUNCTION {q(nm)}  PERIOD {fmt(t)}  VALUE "
                         f"{fmt(sa)}")
        for nm, vals, dt in hists:
            self.rec(f"FUNCTION {q(nm)}  FUNCTYPE \"HISTORY\"  HISTTYPE "
                     f"\"USER\"  TIMESTEP {fmt(dt)}")
            for k, v in enumerate(vals):
                self.rec(f"FUNCTION {q(nm)}  TIME {fmt(k * dt)}  VALUE "
                         f"{self.n(v, 'accel')}")
        self.count("functions", len(specs) + len(hists))

    def _cases(self) -> None:
        from skyframe.core.model import MODAL_CASE
        m = self.m
        self.sec("LOAD CASES")
        n = 0
        if m.num_modes > 0:
            self.rec(f"LOADCASE {q(MODAL_CASE)}  TYPE  \"Modal - Eigen\"  "
                     "INITCOND  \"PRESET\"")
            self.rec(f"LOADCASE {q(MODAL_CASE)}  MAXMODES  {int(m.num_modes)}"
                     "  MINMODES  1  EIGENSHIFTFREQ  0  EIGENCUTOFF  0  "
                     "EIGENTOL  1E-09")
            n += 1
        for c in m.cases.values():
            geo = c.effective_geometric
            if geo == "linear":
                self.rec(f"LOADCASE {q(c.name)}  TYPE  \"Linear Static\"  "
                         "INITCOND  \"PRESET\"")
            else:
                self.rec(f"LOADCASE {q(c.name)}  TYPE  \"Nonlinear Static\""
                         "  INITCOND  \"NONE\"")
            for pat, f in c.patterns.items():
                self.rec(f"LOADCASE {q(c.name)}  LOADPAT  {q(pat)}  SF  "
                         f"{fmt(f)}")
            if geo != "linear":
                g = "PDelta" if geo == "pdelta" else "PDelta plus Large Displ"
                self.rec(f"LOADCASE {q(c.name)}  NLGEOMTYPE  {q(g)}")
            n += 1
        g_file = G_SI / self.u.factor("accel")
        for rs in m.rs_cases.values():
            fn = rs.function or self.fn_name.get(("rs", rs.name), "")
            self.rec(f"LOADCASE {q(rs.name)}  TYPE  \"Response Spectrum\"  "
                     f"MODALCASE  {q(MODAL_CASE)}")
            # Wave B: one ACCEL row per direction (+ ANG), ECCRATIO,
            # DIRCOMBO (io/e2k_ext.py; legacy cases: the legacy row)
            for row_ in _e2kx.rs_acc_rows(rs, q, fmt, g_file, fn):
                self.rec(row_)
            for row_ in _e2kx.rs_extra_records(rs, q, fmt):
                self.rec(row_)
            self.rec(f"LOADCASE {q(rs.name)}  MODALCOMBO  "
                     f"{q(RS_METHODS.get(rs.combo_method, 'CQC'))}")
            self.rec(f"LOADCASE {q(rs.name)}  MODALDAMPTYPE  \"Constant\"  "
                     f"CONSTDAMP  {fmt(rs.damping)}")
            n += 1
        for th in m.th_cases.values():
            fn = th.function or self.fn_name.get(("th", th.name), "")
            typ = "Nonlinear Direct Integration History" if th.nonlinear \
                else "Linear Direct Integration History"
            nsteps = len(m.th_functions[fn].values) if fn in m.th_functions \
                else len(th.accel)
            dt = m.th_functions[fn].dt if fn in m.th_functions else th.dt
            self.rec(f"LOADCASE {q(th.name)}  TYPE  {q(typ)}  INITCOND  "
                     "\"PRESET\"")
            acc = "U1" if th.direction == "X" else "U2"
            self.rec(f"LOADCASE {q(th.name)}  ACCEL  {q(acc)}  FUNC  {q(fn)}"
                     f"  SF  {fmt(th.scale)}")
            self.rec(f"LOADCASE {q(th.name)}  NUMSTEPS  {nsteps}  STEPSIZE  "
                     f"{fmt(dt)}")
            self.rec(f"LOADCASE {q(th.name)}  MODALDAMPTYPE  \"Constant\"  "
                     f"CONSTDAMP  {fmt(th.damping)}")
            n += 1
        for bc in m.buckling_cases.values():
            self.rec(f"LOADCASE {q(bc.name)}  TYPE  \"Buckling\"  INITCOND "
                     " \"PRESET\"")
            for pat, f in bc.gravity.items():
                self.rec(f"LOADCASE {q(bc.name)}  LOADPAT  {q(pat)}  SF  "
                         f"{fmt(f)}")
            self.rec(f"LOADCASE {q(bc.name)}  BUCKLINGMODES  "
                     f"{int(bc.num_modes)}")
            n += 1
        # Wave C: pushover / nonlinear static / staged / steady-state /
        # PSD cases as native LOADCASE records (io/e2k_nonlinear.py)
        used_ = set(m.case_kinds()) | {MODAL_CASE}
        n += _e2knl.write_cases(self, used_)
        self.count("load_cases", n)

    def _combos(self) -> None:
        m = self.m
        if not m.combos:
            return
        self.sec("LOAD COMBINATIONS")
        for c in m.combos.values():
            self.rec(f"COMBO {q(c.name)}  TYPE {q(COMBO_TYPES.get(c.combo_type, 'Linear Add'))}")
            for case, f in c.cases.items():
                kw = "LOADCOMBO" if case in m.combos else "LOADCASE"
                self.rec(f"COMBO {q(c.name)}  {kw} {q(case)}  SF {fmt(f)}")
        self.count("load_combinations", len(m.combos))
        if m.rs_combos:
            self.skipped.append(f"{len(m.rs_combos)} RS directional "
                                "combination(s) (not expressible)")

    def _groups(self) -> None:
        m = self.m
        if not m.groups:
            return
        self.sec("GROUPS")
        for name, g in m.groups.items():
            col = f"  COLOR {q(g['color'])}" if g.get("color") else ""
            self.rec(f"GROUP {q(name)}{col}")
            for uid in g.get("members", []):
                if uid in self.obj_story:
                    self.rec(f"GROUP {q(name)}  LINE {q(uid)}  "
                             f"{q(self.obj_story[uid])}")
            for uid in g.get("shells", []):
                if uid in self.obj_story:
                    self.rec(f"GROUP {q(name)}  AREA {q(uid)}  "
                             f"{q(self.obj_story[uid])}")
            for uid in g.get("links", []):
                self.rec(f"GROUP {q(name)}  LINK {q(uid)}")
            dl = self.lv.towers[0]["levels"]
            for pt in g.get("points", []):
                p = self.p3.get(tuple(float(v) for v in pt)) or \
                    self.point(pt, dl)
                if p is not None:
                    self.rec(f"GROUP {q(name)}  POINT {q(p[0])}  {q(p[1])}")
        self.count("groups", len(m.groups))


# --------------------------------------------------------------------------- #
# public
# --------------------------------------------------------------------------- #
def _native(model, units: Units, include: Dict[str, bool]):
    w = _Writer(model, units, include)
    text = w.write()
    return w, text


def export_e2k(model, units=("KN", "M"), include: Optional[dict] = None,
               extensions: bool = True) -> Tuple[str, dict]:
    """Write ``model`` (BuildingModel or dict) as ETABS e2k text.

    Returns ``(text, report)``; ``report`` = ``{"format": "e2k", "units",
    "written": {kind: count}, "skipped": [str], "extensions": {key: n},
    "exact": bool}`` — ``exact`` is True when the file re-imports to the
    identical ``to_dict()`` (always the case with extensions in kN-m).
    """
    from skyframe.core.model import BuildingModel
    from skyframe.io.e2k_read import read_e2k
    if isinstance(model, dict):
        model = BuildingModel.from_dict(model)
    if isinstance(units, str):
        from skyframe.io.exchange_common import UNIT_PRESETS
        units = UNIT_PRESETS.get(units, ("KN", "M"))
    u = Units(*units)
    inc = {k: True for k in INCLUDE_KEYS}
    inc.update({k: bool(v) for k, v in (include or {}).items()
                if k in INCLUDE_KEYS})
    w, text = _native(model, u, inc)
    report = {"format": "e2k", "units": [u.force, u.length],
              "written": dict(w.counts),
              "skipped": list(dict.fromkeys(w.skipped)),
              "extensions": {}, "exact": False}
    target = model_dict(model)
    try:
        native_model, _warn = read_e2k(text, apply_extensions=False)
        native = model_dict(native_model)
    except Exception as exc:                      # noqa: BLE001
        native = None
        report["skipped"].append(f"native re-import failed: {exc}")
    if native is not None:
        rel = 0.0 if u.is_si else 1e-12
        ops = diff_patches(native, target, rel)
        report["extensions"] = summarize_patches(ops)
        report["native_exact"] = not ops
        if extensions and ops:
            ext = ["", "$ SKYFRAME EXTENSIONS (comments; ETABS ignores "
                   "them, SkyFrame restores the exact model)"]
            for op in ops:
                ext.append("$SF " + json.dumps(op, separators=(",", ":"),
                                               allow_nan=False))
            text = text + "\n" + "\n".join(ext)
        if extensions or not ops:
            check, _w = read_e2k(text)
            from skyframe.io.exchange_common import equal
            report["exact"] = equal(model_dict(check), target,
                                    0.0 if u.is_si else 1e-12)
    text += "\n\n  END\n$ END OF MODEL FILE\n"
    return text, report
