"""Structural-system template library (File > New Model from Template).

CONTRACT "Structural-system template library (D4)".

One parametric model per lateral system, laid out the way the CSI ETABS
New-Model quick templates and the ETABS example / verification buildings
are laid out (grids, stories, sections from the steel databases and the
material library, gravity loads, mass source, diaphragms, code ELF + RS
cases, default combinations and pier / spandrel labels).  The models are
our own parametric renditions of those structural systems; no CSI example
result is reproduced or claimed.

==========================  ==================================================
id                          system
==========================  ==================================================
``frame_2d``                planar steel moment frame (XZ, 2-D active DOF)
``steel_smf``               steel special moment frame: perimeter moment
                            frames + pinned interior gravity framing
``steel_cbf``               steel concentrically braced frame (chevron / X)
``steel_ebf``               steel eccentrically braced frame (mid-span link)
``steel_deck``              ETABS "Steel Deck": girders on the grid, pinned
                            infill beams, composite deck one-way slabs
``flat_slab_perimeter``     ETABS "Flat Slab with Perimeter Beams"
``rc_dual_core``            RC dual system: core walls + moment frame
``rc_shear_wall``           RC bearing walls: coupled X walls (piers +
                            spandrels) and planar Y walls
``rc_framed_tube``          RC framed tube (closely spaced perimeter columns,
                            deep spandrels) with optional inner core
``outrigger``               concrete core + steel frame with outrigger and
                            belt trusses
``podium_tower``            podium with one or two towers (Define > Towers)
``base_isolated``           RC frame on linear isolator links
``industrial_shed``         steel portal-frame shed or long-span roof truss
``diagrid``                 steel diagrid tower around a concrete core
==========================  ==================================================

The five slab templates of :mod:`skyframe.core.templates` (waffle,
ribbed, two-way slab with beams, staggered truss, flat slab with drop
panels) complete the set.

Loads (every template): ``DEAD`` (kind dead, self weight 1), ``SDL``
(superdead area load ``sdl`` on every floor and roof), ``LIVE`` (live area
load ``live`` on the floors below the roof) and ``LROOF`` (roof_live area
load ``roof_live`` on the roof); one linear static case per pattern; mass
source ``{DEAD: 1, SDL: 1, LIVE: live_mass_factor}``.  The code loads
(``EQX`` / ``EQY`` auto lateral, function ``RSF`` with ``RSX`` / ``RSY``
and the default combinations) come from
:func:`skyframe.core.templates.apply_code_loads`.  Floors are rigid
diaphragms (story default) except the shed roof (no diaphragm).

Units: every input is SI (m, kN, kPa); ``units`` only sets the model's
display units.  Steel sections are database names (``steel_db``); a
section left ``None`` takes the template's AISC default, or for another
database the shape of the same type closest to it in (A, I33).  Materials
come from the material library (``material_standard``); ``None`` takes the
standard's default grade.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from . import material_db as _mdb
from . import steel_db as _sdb
from .model import (DISPLAY_UNITS, AreaLoad, BuildingModel, FrameSection,
                    GridSystem, MemberUDL, PointSupport, ShellSection, Story)

G = 9.80665
PINNED = "Mi,Mj"

# per-standard default grades: (structural steel, concrete)
MATERIAL_DEFAULTS = {
    "astm_aci": ("A992", "4000psi"),
    "eurocode": ("S355", "C30/37"),
    "is": ("E250", "M30"),
    "bs": ("S355-t16", "C40-BS8110"),
    "as_nzs": ("350-AS3678", "N40"),
}
STEEL_DBS = ("aisc", "eurocode", "is808", "bs", "as_nzs")


def _base_common() -> dict:
    """``key -> (default, kind, lo, hi, label, group)`` shared by all."""
    from .templates import _COMMON
    lab = {"name": "Model name", "bays_x": "Bays along X",
           "bays_y": "Bays along Y", "bay_x": "Bay width X",
           "bay_y": "Bay width Y", "stories": "Stories",
           "story_height": "Typical story height",
           "first_story_height": "First story height",
           "sdl": "Superimposed dead SDL", "live": "Floor live LIVE",
           "live_mass_factor": "Live mass factor",
           "mesh_size": "Shell mesh size", "code": "Code"}
    grp = {"name": "geometry", "bays_x": "geometry", "bays_y": "geometry",
           "bay_x": "geometry", "bay_y": "geometry", "stories": "geometry",
           "story_height": "geometry", "first_story_height": "geometry",
           "sdl": "loads", "live": "loads", "live_mass_factor": "loads",
           "mesh_size": "advanced"}
    out = {}
    for k, (d, kind, lo, hi) in _COMMON.items():
        out[k] = (d, kind, lo, hi, lab.get(k, k), grp.get(k, "code"))
    out["roof_live"] = (1.0, "num", 0.0, 50.0, "Roof live LROOF", "loads")
    out["units"] = ("kN-m", "choice", DISPLAY_UNITS, None, "Display units",
                    "materials")
    out["material_standard"] = ("astm_aci", "choice",
                                tuple(MATERIAL_DEFAULTS), None,
                                "Material library", "materials")
    out["steel_material"] = (None, "str", None, None, "Steel grade",
                             "materials")
    out["concrete_material"] = (None, "str", None, None, "Concrete grade",
                                "materials")
    out["steel_db"] = ("aisc", "choice", STEEL_DBS, None,
                       "Steel section database", "materials")
    return out


def _sec(d, label):
    """Steel section parameter (database name; None = template default)."""
    return (None, "steel", d, None, label, "sections")


def _len(d, lo, hi, label, group="sections"):
    return (d, "len", lo, hi, label, group)


def _int(d, lo, hi, label, group="system"):
    return (d, "int", lo, hi, label, group)


def _num(d, lo, hi, label, group="system"):
    return (d, "num", lo, hi, label, group)


def _choice(d, opts, label, group="system"):
    return (d, "choice", tuple(opts), None, label, group)


# --------------------------------------------------------------------------- #
# the library: id -> spec
# --------------------------------------------------------------------------- #
SPECS: Dict[str, dict] = {
    "frame_2d": {
        "label": "2D Steel Moment Frame",
        "etabs": "Grid Only + 2-D frame (UX, UZ, RY active)",
        "category": "Steel",
        "symmetric": "x",
        "uses": ("steel",),
        "description": "Planar moment-resisting frame in the XZ plane: "
                       "columns at every grid line, rigid beams, fixed "
                       "bases.  SDL / LIVE / LROOF are line loads on the "
                       "beams (area load x tributary width).  The classic "
                       "first ETABS exercise and a closed-form check case.",
        "defaults": {"bays_x": 3, "bays_y": 1, "bay_x": 6.0, "bay_y": 6.0,
                     "stories": 4, "story_height": 3.5, "R": 8.0},
        "hidden": ("bays_y", "bay_y"),
        "params": {"column": _sec("W14X90", "Column"),
                   "beam": _sec("W21X44", "Beam"),
                   "trib_width": _len(6.0, 0.5, 30.0, "Tributary width "
                                      "(area loads -> beam UDL)",
                                      "loads")},
    },
    "steel_smf": {
        "label": "Steel Special Moment Frame",
        "etabs": "Steel Deck template, perimeter moment frames",
        "category": "Steel",
        "symmetric": "xy",
        "uses": ("steel", "concrete"),
        "description": "Perimeter special moment frames (rigid beam-column "
                       "joints, strong axis in the frame plane) carry all "
                       "lateral load; interior beams are pinned and "
                       "interior gravity columns are pinned at the base.  "
                       "Concrete-on-deck floors are two-way membrane slabs "
                       "(rigid diaphragms).",
        "defaults": {"bays_x": 4, "bays_y": 4, "bay_x": 9.0, "bay_y": 9.0,
                     "stories": 5, "story_height": 4.0,
                     "first_story_height": 4.5, "R": 8.0, "live": 2.4},
        "params": {"mf_column": _sec("W14X176", "Moment-frame column"),
                   "mf_beam": _sec("W24X84", "Moment-frame beam"),
                   "gravity_column": _sec("W14X61", "Gravity column"),
                   "gravity_beam": _sec("W18X35", "Gravity beam"),
                   "slab": _len(0.13, 0.05, 0.5, "Slab (deck) thickness")},
    },
    "steel_cbf": {
        "label": "Steel Concentrically Braced Frame",
        "etabs": "Steel Deck template + braced bays",
        "category": "Steel",
        "symmetric": "xy",
        "uses": ("steel", "concrete"),
        "description": "Pin-connected steel frame braced in the middle bay(s) "
                       "of every face (chevron / inverted-V or X braces).  "
                       "Beams and braces are pinned and the column bases "
                       "are pinned, so the braces carry the story shear.",
        "defaults": {"bays_x": 3, "bays_y": 3, "bay_x": 8.0, "bay_y": 8.0,
                     "stories": 5, "story_height": 3.8, "R": 6.0,
                     "live": 2.4},
        "params": {"brace_type": _choice("chevron", ("chevron", "x"),
                                         "Brace configuration"),
                   "column": _sec("W14X90", "Column"),
                   "beam": _sec("W18X50", "Beam"),
                   "brace": _sec("HSS8X8X1/2", "Brace"),
                   "slab": _len(0.13, 0.05, 0.5, "Slab (deck) thickness")},
    },
    "steel_ebf": {
        "label": "Steel Eccentrically Braced Frame",
        "etabs": "Steel Deck template + EBF bays",
        "category": "Steel",
        "symmetric": "xy",
        "uses": ("steel", "concrete"),
        "description": "Inverted-V braces meeting the beam at the ends of a "
                       "central shear link of length e (the link is its own "
                       "frame object, uid LK...).  Beams pinned at the "
                       "columns, braces pinned, column bases pinned.",
        "defaults": {"bays_x": 3, "bays_y": 3, "bay_x": 8.0, "bay_y": 8.0,
                     "stories": 5, "story_height": 3.8, "R": 8.0,
                     "live": 2.4},
        "params": {"link_length": _len(0.9, 0.2, 4.0, "Link length e",
                                       "system"),
                   "column": _sec("W14X90", "Column"),
                   "beam": _sec("W21X68", "Beam / link"),
                   "brace": _sec("HSS8X8X1/2", "Brace"),
                   "slab": _len(0.13, 0.05, 0.5, "Slab (deck) thickness")},
    },
    "steel_deck": {
        "label": "Steel Deck Composite Floor",
        "etabs": "Steel Deck template",
        "category": "Steel",
        "symmetric": "xy",
        "uses": ("steel", "concrete"),
        "description": "ETABS Steel Deck layout: girders on every grid line, "
                       "pinned infill (secondary) beams in each bay, a "
                       "filled composite deck spanning one-way onto the "
                       "infill beams (deck_filled slab property).  "
                       "Moment frames on the perimeter or on every grid "
                       "line.",
        "defaults": {"bays_x": 3, "bays_y": 3, "bay_x": 9.0, "bay_y": 9.0,
                     "stories": 4, "story_height": 4.0, "R": 8.0,
                     "live": 2.4},
        "params": {"infill_beams": _int(2, 0, 8, "Infill beams per bay"),
                   "infill_dir": _choice("Y", ("X", "Y"),
                                         "Infill beams run along"),
                   "moment_frames": _choice("perimeter",
                                            ("perimeter", "all"),
                                            "Moment frames on"),
                   "column": _sec("W14X120", "Column"),
                   "girder": _sec("W24X68", "Girder"),
                   "infill": _sec("W16X31", "Infill beam"),
                   "deck_slab": _len(0.0635, 0.03, 0.3,
                                     "Concrete above flutes tc"),
                   "deck_rib": _len(0.0762, 0.02, 0.3, "Rib depth hr")},
    },
    "flat_slab_perimeter": {
        "label": "Flat Slab with Perimeter Beams",
        "etabs": "Flat Slab with Perimeter Beams template",
        "category": "Concrete",
        "symmetric": "xy",
        "uses": ("concrete",),
        "description": "RC columns with a meshed two-way flat slab (shell) "
                       "and moment-connected perimeter (spandrel) beams; "
                       "the slab-column frames resist lateral load.",
        "defaults": {"bays_x": 3, "bays_y": 3, "bay_x": 7.5, "bay_y": 7.5,
                     "stories": 5, "story_height": 3.5, "R": 3.0,
                     "live": 2.4},
        "params": {"column": _len(0.6, 0.2, 3.0, "Column size"),
                   "beam_b": _len(0.40, 0.1, 3.0, "Perimeter beam width"),
                   "beam_h": _len(0.70, 0.1, 3.0, "Perimeter beam depth"),
                   "slab": _len(0.25, 0.05, 1.0, "Slab thickness")},
    },
    "rc_dual_core": {
        "label": "RC Dual System (Core + Moment Frame)",
        "etabs": "Flat Slab / Two-Way template + core walls",
        "category": "Concrete",
        "symmetric": "xy",
        "uses": ("concrete",),
        "description": "Central box core of shear walls (one pier label per "
                       "wall leg, P1-P4) plus special moment frames on "
                       "every grid line; two-way membrane slabs, rigid "
                       "diaphragms.  The core occupies the central bay(s).",
        "defaults": {"bays_x": 5, "bays_y": 5, "bay_x": 8.0, "bay_y": 8.0,
                     "stories": 10, "story_height": 3.5,
                     "first_story_height": 4.5, "R": 7.0, "live": 2.4},
        "params": {"core_bays_x": _int(1, 1, 9, "Core bays X"),
                   "core_bays_y": _int(1, 1, 9, "Core bays Y"),
                   "wall": _len(0.40, 0.15, 2.0, "Core wall thickness"),
                   "column": _len(0.70, 0.2, 3.0, "Column size"),
                   "beam_b": _len(0.45, 0.1, 3.0, "Beam width"),
                   "beam_h": _len(0.70, 0.1, 3.0, "Beam depth"),
                   "slab": _len(0.20, 0.05, 1.0, "Slab thickness")},
    },
    "rc_shear_wall": {
        "label": "RC Shear-Wall Building",
        "etabs": "Two-Way template + Draw Walls (coupled / planar)",
        "category": "Concrete",
        "symmetric": "xy",
        "uses": ("concrete",),
        "description": "Bearing-wall building: on both long faces a coupled "
                       "wall (two piers joined by a spandrel over a door "
                       "opening, ETABS pier labels P1-P4 and spandrel "
                       "labels S1-S2); planar walls centred on both end "
                       "faces (PX1, PX2); gravity columns and beams "
                       "elsewhere.",
        "defaults": {"bays_x": 4, "bays_y": 3, "bay_x": 7.5, "bay_y": 6.0,
                     "stories": 8, "story_height": 3.2, "R": 5.0,
                     "live": 2.0},
        "params": {"pier_length": _len(3.0, 0.5, 20.0, "Coupled pier length",
                                       "system"),
                   "opening": _len(1.5, 0.3, 10.0, "Door opening width",
                                   "system"),
                   "spandrel_depth": _len(0.9, 0.2, 3.0, "Spandrel depth",
                                          "system"),
                   "planar_length": _len(6.0, 0.5, 30.0,
                                         "Planar wall length", "system"),
                   "wall": _len(0.30, 0.1, 2.0, "Wall thickness"),
                   "column": _len(0.50, 0.2, 3.0, "Column size"),
                   "beam_b": _len(0.35, 0.1, 3.0, "Beam width"),
                   "beam_h": _len(0.60, 0.1, 3.0, "Beam depth"),
                   "slab": _len(0.20, 0.05, 1.0, "Slab thickness")},
    },
    "rc_framed_tube": {
        "label": "RC Framed Tube",
        "etabs": "Two-Way template, tube perimeter + core",
        "category": "Concrete",
        "symmetric": "xy",
        "uses": ("concrete",),
        "description": "High-rise framed tube: closely spaced perimeter "
                       "columns (each bay subdivided) tied by deep spandrel "
                       "beams act as a perforated tube; interior gravity "
                       "columns and beams; optional central core walls "
                       "(tube-in-tube).",
        "defaults": {"bays_x": 4, "bays_y": 4, "bay_x": 9.0, "bay_y": 9.0,
                     "stories": 20, "story_height": 3.6,
                     "first_story_height": 4.5, "R": 5.0, "live": 2.4},
        "params": {"tube_subdiv": _int(3, 1, 6, "Tube columns per bay"),
                   "inner_core": _choice("yes", ("yes", "no"),
                                         "Inner core (tube-in-tube)"),
                   "tube_column_b": _len(0.50, 0.2, 3.0,
                                         "Tube column width (normal)"),
                   "tube_column_h": _len(0.90, 0.2, 3.0,
                                         "Tube column depth (along face)"),
                   "spandrel_b": _len(0.50, 0.1, 3.0, "Spandrel width"),
                   "spandrel_h": _len(1.00, 0.1, 3.0, "Spandrel depth"),
                   "column": _len(0.80, 0.2, 3.0, "Interior column size"),
                   "beam_b": _len(0.45, 0.1, 3.0, "Interior beam width"),
                   "beam_h": _len(0.70, 0.1, 3.0, "Interior beam depth"),
                   "wall": _len(0.45, 0.15, 2.0, "Core wall thickness"),
                   "slab": _len(0.20, 0.05, 1.0, "Slab thickness")},
    },
    "outrigger": {
        "label": "Core + Outrigger and Belt Truss",
        "etabs": "Steel Deck template + core walls + outriggers",
        "category": "Tall building",
        "symmetric": "xy",
        "uses": ("steel", "concrete"),
        "description": "Tall building: concrete core walls with a pinned "
                       "steel gravity frame; story-deep steel outrigger "
                       "trusses on the core grid lines engage the perimeter "
                       "columns through belt trusses around the perimeter "
                       "at the outrigger levels.",
        "defaults": {"bays_x": 5, "bays_y": 5, "bay_x": 9.0, "bay_y": 9.0,
                     "stories": 30, "story_height": 3.8,
                     "first_story_height": 5.0, "R": 6.0, "live": 2.4},
        "params": {"outrigger_levels": ("mid,top", "str", None, None,
                                        "Outrigger stories (numbers, "
                                        "'mid', 'top')", "system"),
                   "wall": _len(0.60, 0.15, 2.0, "Core wall thickness"),
                   "column": _sec("W14X370", "Perimeter column"),
                   "beam": _sec("W18X40", "Gravity beam"),
                   "chord": _sec("W14X257", "Outrigger / belt chord"),
                   "diagonal": _sec("W14X176", "Outrigger / belt diagonal"),
                   "slab": _len(0.13, 0.05, 0.5, "Slab (deck) thickness")},
    },
    "podium_tower": {
        "label": "Podium + Tower(s)",
        "etabs": "Define > Towers (multiple towers on a podium)",
        "category": "Concrete",
        "symmetric": "xy",
        "uses": ("concrete",),
        "description": "Wide low-rise podium (tower P) carrying one central "
                       "tower or two towers placed symmetrically "
                       "(Define > Towers, each with its own grid, stories "
                       "and diaphragms).  RC moment frames, membrane "
                       "slabs.",
        "defaults": {"bays_x": 7, "bays_y": 5, "bay_x": 8.0, "bay_y": 8.0,
                     "stories": 3, "story_height": 4.5, "R": 7.0,
                     "live": 3.0},
        "params": {"towers": _int(1, 1, 2, "Towers"),
                   "tower_bays_x": _int(3, 1, 20, "Tower bays X"),
                   "tower_bays_y": _int(3, 1, 20, "Tower bays Y"),
                   "tower_stories": _int(10, 1, 80, "Tower stories"),
                   "tower_story_height": _len(3.5, 2.0, 10.0,
                                              "Tower story height",
                                              "geometry"),
                   "column": _len(0.70, 0.2, 3.0, "Column size"),
                   "beam_b": _len(0.45, 0.1, 3.0, "Beam width"),
                   "beam_h": _len(0.70, 0.1, 3.0, "Beam depth"),
                   "slab": _len(0.20, 0.05, 1.0, "Slab thickness")},
    },
    "base_isolated": {
        "label": "Base-Isolated Building",
        "etabs": "Two-Way template + isolator links (Link/Support "
                 "properties)",
        "category": "Special",
        "symmetric": "xy",
        "uses": ("concrete",),
        "description": "RC moment-frame superstructure on a stiff base "
                       "diaphragm supported by one isolator link per column "
                       "(linear effective stiffness sized for the target "
                       "isolated period, vertical stiffness kv).  The "
                       "isolation story is 'ISO'; link bottoms are the "
                       "only supports.",
        "defaults": {"bays_x": 4, "bays_y": 3, "bay_x": 7.5, "bay_y": 7.5,
                     "stories": 5, "story_height": 3.5, "R": 2.0,
                     "live": 2.4},
        "params": {"isolator_period": _num(2.5, 0.5, 6.0,
                                           "Target isolated period (s)"),
                   "isolator_kv": _num(1.0e7, 1.0e4, 1.0e9,
                                       "Isolator vertical stiffness "
                                       "(kN/m)"),
                   "isolator_height": _len(0.5, 0.1, 3.0,
                                           "Isolation layer height",
                                           "system"),
                   "column": _len(0.60, 0.2, 3.0, "Column size"),
                   "beam_b": _len(0.40, 0.1, 3.0, "Beam width"),
                   "beam_h": _len(0.65, 0.1, 3.0, "Beam depth"),
                   "base_beam_b": _len(0.60, 0.1, 3.0, "Base beam width"),
                   "base_beam_h": _len(1.00, 0.1, 3.0, "Base beam depth"),
                   "slab": _len(0.20, 0.05, 1.0, "Slab thickness")},
    },
    "industrial_shed": {
        "label": "Industrial Shed (Portal / Roof Truss)",
        "etabs": "Grid Only + portal frames / long-span roof truss",
        "category": "Steel",
        "symmetric": "xy",
        "uses": ("steel",),
        "description": "Single-story steel shed: pitched portal frames or "
                       "long-span Pratt roof trusses on fixed-base columns "
                       "at every frame line along Y, eave struts and ridge "
                       "purlin, X-bracing in the end bays of both side "
                       "walls.  Roof cladding spans one-way between frames "
                       "(membrane, no diaphragm).",
        "defaults": {"bays_x": 1, "bays_y": 6, "bay_x": 24.0, "bay_y": 6.0,
                     "stories": 1, "story_height": 7.0, "R": 3.5,
                     "sdl": 0.3, "live": 0.0, "roof_live": 0.96},
        "hidden": ("bays_x", "stories", "first_story_height", "live",
                   "live_mass_factor"),
        "labels": {"bay_x": "Span", "bays_y": "Frame bays along Y",
                   "bay_y": "Frame spacing", "story_height": "Eave height"},
        "params": {"roof": _choice("portal", ("portal", "truss"),
                                   "Roof system"),
                   "pitch": _num(10.0, 0.0, 30.0, "Roof pitch (deg)"),
                   "truss_depth": _len(2.0, 0.5, 6.0, "Truss depth",
                                       "system"),
                   "truss_panels": _int(8, 2, 24, "Truss panels"),
                   "column": _sec("W18X76", "Column"),
                   "rafter": _sec("W18X50", "Rafter"),
                   "chord": _sec("W8X31", "Truss chord"),
                   "web": _sec("HSS4X4X1/4", "Truss web"),
                   "strut": _sec("W8X31", "Eave strut / purlin"),
                   "brace": _sec("HSS4X4X1/4", "Wall brace")},
    },
    "diagrid": {
        "label": "Diagrid Tower",
        "etabs": "Grid Only + diagrid perimeter + core walls",
        "category": "Tall building",
        "symmetric": "xy",
        "uses": ("steel", "concrete"),
        "description": "Square tower whose perimeter is a steel diagrid "
                       "(diagonals spanning a two-story module, nodes "
                       "alternating by half a panel, corner columns) with "
                       "perimeter ring beams at every floor around a "
                       "concrete core.",
        "defaults": {"bays_x": 6, "bays_y": 6, "bay_x": 6.0, "bay_y": 6.0,
                     "stories": 20, "story_height": 4.0, "R": 6.0,
                     "live": 2.4},
        "hidden": ("bays_y", "bay_y"),
        "labels": {"bays_x": "Panels per face", "bay_x": "Panel width"},
        "params": {"core_size": _len(12.0, 3.0, 60.0, "Core size",
                                     "system"),
                   "wall": _len(0.50, 0.15, 2.0, "Core wall thickness"),
                   "diagonal": _sec("W14X176", "Diagonal"),
                   "ring": _sec("W24X84", "Ring beam"),
                   "corner": _sec("W14X176", "Corner column"),
                   "slab": _len(0.13, 0.05, 0.5, "Slab (deck) thickness")},
    },
}

SYSTEM_IDS: Tuple[str, ...] = tuple(SPECS)

# AISC defaults of every steel-section parameter (spec "steel" lo field)
_ORDER_GROUPS = ("geometry", "system", "sections", "materials", "loads",
                 "code", "advanced")


def _spec(template: str) -> dict:
    out = _base_common()
    out.update(SPECS[template]["params"])
    return out


_UNIT_KIND = {"sdl": "pressure", "live": "pressure", "roof_live": "pressure",
              "isolator_kv": "stiffness", "isolator_period": "period"}


def _unit_kind(key: str, kind: str, group: str) -> str:
    """js/units.js quantity kind of a parameter (display conversion)."""
    if key in _UNIT_KIND:
        return _UNIT_KIND[key]
    if kind == "len":
        return "dim" if group == "sections" else "length"
    return "none"


def param_meta(template: str) -> List[dict]:
    """UI metadata ``[{key, kind, label, group, unit, min, max, options}]``
    (``unit`` = the js/units.js quantity kind)."""
    meta = []
    spec = SPECS[template]
    hidden = set(spec.get("hidden", ()))
    labels = spec.get("labels", {})
    for k, (d, kind, lo, hi, label, group) in _spec(template).items():
        e = {"key": k, "kind": kind, "label": labels.get(k, label),
             "group": group, "unit": _unit_kind(k, kind, group)}
        if k in hidden:
            e["hidden"] = True
        if kind == "choice":
            e["options"] = list(lo)
        elif kind == "steel":
            e["aisc_default"] = lo
        else:
            if lo is not None:
                e["min"] = lo
            if hi is not None:
                e["max"] = hi
        meta.append(e)
    meta.sort(key=lambda e: _ORDER_GROUPS.index(e["group"]))
    return meta


def catalogue_entry(template: str) -> dict:
    s = SPECS[template]
    p = {k: v[0] for k, v in _spec(template).items()}
    p.update(s["defaults"])
    p["name"] = s["label"]
    return {"id": template, "label": s["label"], "params": p,
            "category": s["category"], "etabs": s["etabs"],
            "description": s["description"], "system": True,
            "meta": param_meta(template)}


def normalize_params(template: str, params: Optional[dict]) -> dict:
    """Every parameter validated with defaults filled (ValueError)."""
    if template not in SPECS:
        raise ValueError(f"unknown template {template!r}")
    params = dict(params or {})
    spec = _spec(template)
    defaults = SPECS[template]["defaults"]
    bad = sorted(k for k in params if k not in spec)
    if bad:
        raise ValueError(f"{template}: unknown parameter(s) {bad}")
    out: Dict[str, object] = {}
    for key, (default, kind, lo, hi, _l, _g) in spec.items():
        default = defaults.get(key, default)
        v = params.get(key, default)
        if v is None:
            out[key] = None
            continue
        if kind in ("str", "steel", "choice"):
            if not isinstance(v, str):
                raise ValueError(f"{template}: {key} must be a string")
            v = v.strip()
            if kind == "choice" and v not in lo:
                raise ValueError(f"{template}: {key} must be one of "
                                 f"{list(lo)} (got {v!r})")
            out[key] = (v or None) if kind == "steel" else v
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(v):
            raise ValueError(f"{template}: {key} must be a number")
        if kind == "int":
            if int(v) != v:
                raise ValueError(f"{template}: {key} must be an integer")
            v = int(v)
        else:
            v = float(v)
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise ValueError(f"{template}: {key} must be in [{lo}, {hi}] "
                             f"(got {v})")
        out[key] = v
    if template == "diagrid":                 # square plan: Y follows X
        if "bays_y" not in params:
            out["bays_y"] = out["bays_x"]
        if "bay_y" not in params:
            out["bay_y"] = out["bay_x"]
    from .templates import CODES
    if out["code"] not in CODES:
        raise ValueError(f"code must be one of {CODES}")
    out["name"] = out["name"] or SPECS[template]["label"]
    std = out["material_standard"]
    out["steel_material"] = out["steel_material"] or MATERIAL_DEFAULTS[std][0]
    out["concrete_material"] = (out["concrete_material"]
                                or MATERIAL_DEFAULTS[std][1])
    _check_system(template, out)
    return out


def _check_system(t: str, p: dict) -> None:
    def need(cond, msg):
        if not cond:
            raise ValueError(f"{t}: {msg}")
    if t == "rc_dual_core":
        need(p["core_bays_x"] < p["bays_x"] and p["core_bays_y"]
             < p["bays_y"], "the core must leave at least one bay of frame")
        need((p["bays_x"] - p["core_bays_x"]) % 2 == 0
             and (p["bays_y"] - p["core_bays_y"]) % 2 == 0,
             "bays - core bays must be even (a centred core)")
    if t == "rc_framed_tube" and p["inner_core"] == "yes":
        need(p["bays_x"] % 2 == p["bays_y"] % 2,
             "bays_x and bays_y must have the same parity for the core")
        lo = 3 if p["bays_x"] % 2 else 4
        need(p["bays_x"] >= lo and p["bays_y"] >= lo,
             "inner core needs >= 3 bays each way (odd) or >= 4 (even)")
    if t == "rc_shear_wall":
        Lx = p["bays_x"] * p["bay_x"]
        Ly = p["bays_y"] * p["bay_y"]
        need(2 * p["pier_length"] + p["opening"] < Lx,
             "coupled wall (2 piers + opening) longer than the building")
        need(p["planar_length"] < Ly, "planar wall longer than the end face")
        need(p["spandrel_depth"] < min(p["story_height"],
                                       p["first_story_height"]
                                       or p["story_height"]) - 0.5,
             "spandrel depth must leave a 0.5 m door opening")
    if t == "outrigger":
        need(p["bays_x"] >= 3 and p["bays_y"] >= 3
             and p["bays_x"] % 2 == 1 and p["bays_y"] % 2 == 1,
             "needs an odd number (>= 3) of bays each way (central core bay)")
        outrigger_levels(p["outrigger_levels"], p["stories"])
    if t == "podium_tower":
        nt = p["towers"]
        need(p["tower_bays_x"] * nt + (nt - 1) <= p["bays_x"]
             and p["tower_bays_y"] <= p["bays_y"],
             "the tower(s) do not fit on the podium")
        if nt == 1:
            need((p["bays_x"] - p["tower_bays_x"]) % 2 == 0
                 and (p["bays_y"] - p["tower_bays_y"]) % 2 == 0,
                 "podium bays - tower bays must be even (centred tower)")
        else:
            need((p["bays_y"] - p["tower_bays_y"]) % 2 == 0,
                 "podium bays_y - tower bays_y must be even")
    if t == "diagrid":
        need(p["bays_x"] == p["bays_y"] and p["bay_x"] == p["bay_y"],
             "the diagrid tower is square: bays_x = bays_y, bay_x = bay_y")
        need(p["bays_x"] >= 2, "needs >= 2 panels per face")
        need(p["core_size"] < 0.8 * p["bays_x"] * p["bay_x"],
             "core larger than 80 % of the plan")
    if t == "industrial_shed":
        need(p["stories"] == 1, "a shed has exactly one story")
        need(p["bays_y"] >= 2, "needs >= 2 bays along Y (frame spacing)")
        if p["roof"] == "truss":
            need(p["truss_panels"] % 2 == 0, "truss_panels must be even")
    if t == "steel_ebf":
        need(p["link_length"] < 0.5 * min(p["bay_x"], p["bay_y"]),
             "link length must be < half the bay")


def outrigger_levels(spec: str, n: int) -> List[int]:
    """Story numbers (1-based, sorted, unique) from 'mid,top,12'."""
    out = set()
    for tok in str(spec).replace(";", ",").split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        if tok == "mid":
            out.add(max(2, (n + 1) // 2))
        elif tok == "top":
            out.add(n)
        else:
            try:
                k = int(tok)
            except ValueError:
                raise ValueError(f"outrigger: bad level {tok!r} (use story "
                                 "numbers, 'mid' or 'top')") from None
            if not 2 <= k <= n:
                raise ValueError(f"outrigger: level {k} must be in "
                                 f"[2, {n}]")
            out.add(k)
    if not out:
        raise ValueError("outrigger: at least one outrigger level needed")
    return sorted(out)


# --------------------------------------------------------------------------- #
# builder context
# --------------------------------------------------------------------------- #
def _alpha(i: int) -> str:
    from .templates import _alpha as a
    return a(i)


def _r(v: float) -> float:
    return round(float(v), 9)


class _Ctx:
    """Model + resolved materials / sections + the floor regions."""

    def __init__(self, t: str, p: dict):
        self.t, self.p = t, p
        self.m = BuildingModel(name=str(p["name"]))
        self.m.display_units = p["units"]
        uses = SPECS[t]["uses"]
        self.steel = self.conc = None
        if "steel" in uses:
            self.steel = self._mat(p["steel_material"], ("steel",))
        if "concrete" in uses:
            self.conc = self._mat(p["concrete_material"], ("concrete",))
        self.floors: List[Tuple[str, bool]] = []     # (shell uid, is_roof)
        self.beam_lines: List[Tuple[str, float, bool]] = []  # 2-D UDLs

    def _mat(self, name, kinds):
        std = self.p["material_standard"]
        try:
            rec = _mdb.material(std, name)
        except KeyError:
            raise ValueError(f"{self.t}: material {name!r} is not in the "
                             f"{std} library") from None
        if rec.get("kind") not in kinds:
            raise ValueError(f"{self.t}: material {name!r} is "
                             f"{rec.get('kind')}, expected {kinds[0]}")
        mat = _mdb.material_from_db(std, name)
        self.m.add_material(mat)
        return mat.name

    # ---- sections ------------------------------------------------------
    def steel_sec(self, key: str) -> str:
        """Steel section of parameter ``key`` (database shape)."""
        name = self.p[key]
        db = self.p["steel_db"]
        if not name:
            aisc = _spec(self.t)[key][2]
            name = aisc if db == "aisc" else equivalent_shape(db, aisc)
        try:
            rec = _sdb.shape(db, name)
        except KeyError:
            raise ValueError(f"{self.t}: {key}: shape {name!r} is not in the "
                             f"{db} database") from None
        nm = rec["name"]
        if nm not in self.m.sections:
            self.m.add_section(_sdb.section_from_db(db, nm, self.steel))
        return nm

    def rect(self, name: str, b: float, h: float) -> str:
        if name not in self.m.sections:
            self.m.add_section(FrameSection.rectangular(name, self.conc,
                                                        float(b), float(h)))
        return name

    def shell_sec(self, name: str, t: float, mat: Optional[str] = None
                  ) -> str:
        if name not in self.m.shell_sections:
            self.m.add_shell_section(ShellSection(name, mat or self.conc,
                                                  float(t)))
        return name

    # ---- geometry ------------------------------------------------------
    def stories(self, heights=None, names=None) -> None:
        p = self.p
        if heights is None:
            h1 = p["first_story_height"] or p["story_height"]
            heights = [h1] + [p["story_height"]] * (p["stories"] - 1)
        self.m.set_stories(list(heights), names)

    def grid(self, xs, ys) -> None:
        self.m.grid = GridSystem([_r(x) for x in xs], [_r(y) for y in ys])

    def member(self, kind, sec, a, b, story, uid, **kw):
        a = tuple(_r(v) for v in a)
        b = tuple(_r(v) for v in b)
        return self.m.add_member(kind, sec, a, b, story=story, uid=uid, **kw)

    def slab(self, sec, corners, story, uid, roof, behavior="membrane",
             one_way_dir=None, mesh=None):
        corners = [tuple(_r(v) for v in c) for c in corners]
        r = self.m.add_shell("slab", behavior, sec, corners,
                             mesh_size=mesh or 1.0, story=story, uid=uid)
        if one_way_dir:
            r.distribution, r.one_way_dir = "one_way", int(one_way_dir)
        self.floors.append((r.uid, roof))
        return r

    def wall(self, sec, corners, story, uid, pier="", spandrel="",
             mesh=None):
        corners = [tuple(_r(v) for v in c) for c in corners]
        r = self.m.add_shell("wall", "shell", sec, corners,
                             mesh_size=mesh or self.wall_mesh(),
                             story=story, uid=uid)
        if pier:
            r.pier = pier
        if spandrel:
            r.spandrel = spandrel
        return r

    def wall_mesh(self) -> float:
        if self.p["mesh_size"]:
            return float(self.p["mesh_size"])
        h = min(s.height for s in self.m.stories)
        return round(max(0.5, min(h / 2.0, 2.0)), 6)

    def is_roof(self, si: int) -> bool:
        return si == len(self.m.stories) - 1


def equivalent_shape(db: str, aisc_name: str) -> str:
    """The ``db`` shape of the same type closest to the AISC shape in
    ``|ln(A/A0)| + |ln(I33/I330)|``."""
    ref = _sdb.shape("aisc", aisc_name)
    A0, I0 = ref["props"]["A"], ref["props"]["I33"]
    best, score = None, math.inf
    for rec in _sdb.shapes(db):
        if rec.get("type") != ref.get("type"):
            continue
        pr = rec.get("props") or {}
        A, I = pr.get("A"), pr.get("I33")
        if not A or not I or A <= 0 or I <= 0:
            continue
        s = abs(math.log(A / A0)) + abs(math.log(I / I0))
        if s < score:
            best, score = rec["name"], s
    if best is None:
        raise ValueError(f"no {ref.get('type')} shape in the {db} database")
    return best


def _lines(n: int, s: float) -> List[float]:
    return [_r(i * s) for i in range(n + 1)]


def _between(v: float, a: float, b: float, tol: float = 1e-6) -> bool:
    return a - tol <= v <= b + tol


# --------------------------------------------------------------------------- #
# steel frame builders
# --------------------------------------------------------------------------- #
def _frame_2d(c: _Ctx) -> None:
    p, m = c.p, c.m
    xs = _lines(p["bays_x"], p["bay_x"])
    c.grid(xs, [0.0])
    c.stories()
    col, beam = c.steel_sec("column"), c.steel_sec("beam")
    for si, st in enumerate(m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        for xi, x in enumerate(xs):
            c.member("column", col, (x, 0, zb), (x, 0, zt), st.name,
                     f"C{si + 1}-{_alpha(xi)}")
        for xi in range(len(xs) - 1):
            b = c.member("beam", beam, (xs[xi], 0, zt), (xs[xi + 1], 0, zt),
                         st.name, f"B{si + 1}-{_alpha(xi)}")
            c.beam_lines.append((b.uid, float(p["trib_width"]),
                                 c.is_roof(si)))
    m.active_dof = ["UX", "UZ", "RY"]


def _perimeter_angle(x, y, xs, ys) -> float:
    """Column angle: strong axis in its perimeter frame's plane (ETABS
    axes: angle 0 -> depth along X).  Corners alternate (point symmetry)."""
    on_x = abs(y - ys[0]) < 1e-9 or abs(y - ys[-1]) < 1e-9   # X-face
    on_y = abs(x - xs[0]) < 1e-9 or abs(x - xs[-1]) < 1e-9   # Y-face
    if on_x and on_y:
        first = (abs(x - xs[0]) < 1e-9) == (abs(y - ys[0]) < 1e-9)
        return 0.0 if first else 90.0
    return 90.0 if on_y else 0.0


def _deck_slabs(c: _Ctx, xs, ys, sec, prefix="F", skip=None):
    for si, st in enumerate(c.m.stories):
        z = st.elevation
        for yi in range(len(ys) - 1):
            for xi in range(len(xs) - 1):
                x0, x1, y0, y1 = xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]
                if skip and skip(xi, yi, x0, x1, y0, y1):
                    continue
                c.slab(sec, [(x0, y0, z), (x1, y0, z), (x1, y1, z),
                             (x0, y1, z)], st.name,
                       f"{prefix}{si + 1}-{_alpha(xi)}{yi + 1}",
                       c.is_roof(si))


def _grid_frame(c: _Ctx, xs, ys, col_of, beam_of, col_kw=None,
                skip_col=None, skip_beam=None):
    """Columns at every grid point and beams on every grid line;
    ``col_of(x, y) -> section``, ``beam_of(axis, line_coord, a, b) ->
    (section, releases)``."""
    m = c.m
    for si, st in enumerate(m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        for yi, y in enumerate(ys):
            for xi, x in enumerate(xs):
                if skip_col and skip_col(x, y):
                    continue
                kw = dict(col_kw(x, y, si) if col_kw else {})
                c.member("column", col_of(x, y), (x, y, zb), (x, y, zt),
                         st.name, f"C{si + 1}-{_alpha(xi)}{yi + 1}", **kw)
        for yi, y in enumerate(ys):
            for xi in range(len(xs) - 1):
                a, b = xs[xi], xs[xi + 1]
                if skip_beam and skip_beam("X", y, a, b):
                    continue
                sec, rel = beam_of("X", y, a, b)
                c.member("beam", sec, (a, y, zt), (b, y, zt), st.name,
                         f"BX{si + 1}-{_alpha(xi)}{yi + 1}", releases=rel)
        for xi, x in enumerate(xs):
            for yi in range(len(ys) - 1):
                a, b = ys[yi], ys[yi + 1]
                if skip_beam and skip_beam("Y", x, a, b):
                    continue
                sec, rel = beam_of("Y", x, a, b)
                c.member("beam", sec, (x, a, zt), (x, b, zt), st.name,
                         f"BY{si + 1}-{_alpha(xi)}{yi + 1}", releases=rel)


def _steel_smf(c: _Ctx) -> None:
    p = c.p
    xs, ys = _lines(p["bays_x"], p["bay_x"]), _lines(p["bays_y"], p["bay_y"])
    c.grid(xs, ys)
    c.stories()
    mfc, mfb = c.steel_sec("mf_column"), c.steel_sec("mf_beam")
    gc, gb = c.steel_sec("gravity_column"), c.steel_sec("gravity_beam")
    slab = c.shell_sec("SLAB", p["slab"])

    def perim(x, y):
        return (abs(x - xs[0]) < 1e-9 or abs(x - xs[-1]) < 1e-9
                or abs(y - ys[0]) < 1e-9 or abs(y - ys[-1]) < 1e-9)

    def col_kw(x, y, si):
        if perim(x, y):
            return {"angle": _perimeter_angle(x, y, xs, ys)}
        return {"releases": "Mi"} if si == 0 else {}

    def beam_of(axis, v, a, b):
        edge = (ys if axis == "X" else xs)
        if abs(v - edge[0]) < 1e-9 or abs(v - edge[-1]) < 1e-9:
            return mfb, ""
        return gb, PINNED

    _grid_frame(c, xs, ys, lambda x, y: mfc if perim(x, y) else gc, beam_of,
                col_kw=col_kw)
    _deck_slabs(c, xs, ys, slab)


def braced_bays(n: int) -> List[int]:
    """Middle bay (odd n) or the two middle bays (even n)."""
    return sorted({(n - 1) // 2, n // 2})


def _steel_braced(c: _Ctx, ebf: bool) -> None:
    p = c.p
    xs, ys = _lines(p["bays_x"], p["bay_x"]), _lines(p["bays_y"], p["bay_y"])
    c.grid(xs, ys)
    c.stories()
    col, beam = c.steel_sec("column"), c.steel_sec("beam")
    br = c.steel_sec("brace")
    slab = c.shell_sec("SLAB", p["slab"])
    bx, by = braced_bays(p["bays_x"]), braced_bays(p["bays_y"])
    chevron = ebf or p.get("brace_type") == "chevron"
    e = float(p["link_length"]) if ebf else 0.0

    def braced(axis, v, a, b):
        lines, bays, grid = (ys, bx, xs) if axis == "X" else (xs, by, ys)
        if not (abs(v - lines[0]) < 1e-9 or abs(v - lines[-1]) < 1e-9):
            return False
        return any(abs(a - grid[i]) < 1e-9 for i in bays)

    def skip_beam(axis, v, a, b):
        return chevron and braced(axis, v, a, b)

    _grid_frame(c, xs, ys, lambda x, y: col,
                lambda *a: (beam, PINNED),
                col_kw=lambda x, y, si: ({"releases": "Mi"} if si == 0
                                         else {}) | (
                    {"angle": _perimeter_angle(x, y, xs, ys)}),
                skip_beam=skip_beam)
    m = c.m
    for si, st in enumerate(m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        for axis, lines, bays, grid in (("X", ys, bx, xs), ("Y", xs, by, ys)):
            for li in (0, len(lines) - 1):
                v = lines[li]
                for bi in bays:
                    a, b = grid[bi], grid[bi + 1]

                    def P(u, z):
                        return (u, v, z) if axis == "X" else (v, u, z)
                    tag = f"{axis}{si + 1}-{_alpha(bi) if axis == 'X' else bi + 1}{li + 1}"
                    if chevron:
                        mid = 0.5 * (a + b)
                        l0, l1 = mid - e / 2.0, mid + e / 2.0
                        if ebf:
                            c.member("beam", beam, P(a, zt), P(l0, zt),
                                     st.name, f"BB{tag}a", releases="Mi")
                            c.member("beam", beam, P(l0, zt), P(l1, zt),
                                     st.name, f"LK{tag}")
                            c.member("beam", beam, P(l1, zt), P(b, zt),
                                     st.name, f"BB{tag}b", releases="Mj")
                        else:
                            c.member("beam", beam, P(a, zt), P(mid, zt),
                                     st.name, f"BB{tag}a", releases="Mi")
                            c.member("beam", beam, P(mid, zt), P(b, zt),
                                     st.name, f"BB{tag}b", releases="Mj")
                        c.member("brace", br, P(a, zb), P(l0, zt), st.name,
                                 f"D{tag}a", releases=PINNED)
                        c.member("brace", br, P(b, zb), P(l1, zt), st.name,
                                 f"D{tag}b", releases=PINNED)
                    else:
                        c.member("brace", br, P(a, zb), P(b, zt), st.name,
                                 f"D{tag}a", releases=PINNED)
                        c.member("brace", br, P(b, zb), P(a, zt), st.name,
                                 f"D{tag}b", releases=PINNED)
    _deck_slabs(c, xs, ys, slab)


def deck_section(tc: float, hr: float) -> dict:
    """Filled composite deck geometry (ETABS Deck: 3" x 12" pitch type)."""
    return {"type": "deck_filled", "slab_depth": float(tc),
            "rib_depth": float(hr), "rib_width_top": 0.1778,
            "rib_width_bottom": 0.1270, "rib_spacing": 0.3048,
            "deck_thickness": 0.000912, "deck_unit_weight": 0.10,
            "rib_dir": 1}


def _steel_deck(c: _Ctx) -> None:
    from .slab_props import sync_thickness
    p, m = c.p, c.m
    xs, ys = _lines(p["bays_x"], p["bay_x"]), _lines(p["bays_y"], p["bay_y"])
    c.grid(xs, ys)
    c.stories()
    col, gir, inf = (c.steel_sec("column"), c.steel_sec("girder"),
                     c.steel_sec("infill"))
    ss = ShellSection("DECK", c.conc, 0.1)
    ss.slab = deck_section(p["deck_slab"], p["deck_rib"])
    sync_thickness(ss, m.materials[c.conc])
    m.add_shell_section(ss)
    n = int(p["infill_beams"])
    along_y = p["infill_dir"] == "Y"
    allmf = p["moment_frames"] == "all"

    def perim_line(axis, v):
        lines = ys if axis == "X" else xs
        return abs(v - lines[0]) < 1e-9 or abs(v - lines[-1]) < 1e-9

    def angle(x, y):
        return _perimeter_angle(x, y, xs, ys) if not allmf else 0.0

    for si, st in enumerate(m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        for yi, y in enumerate(ys):
            for xi, x in enumerate(xs):
                c.member("column", col, (x, y, zb), (x, y, zt), st.name,
                         f"C{si + 1}-{_alpha(xi)}{yi + 1}",
                         angle=angle(x, y))
        # girders on grid lines, split where infill beams frame in
        for axis, lines, grid in (("X", ys, xs), ("Y", xs, ys)):
            split = n if (axis == "X") == along_y else 0
            for li, v in enumerate(lines):
                rel_end = "" if (allmf or perim_line(axis, v)) else None
                for gi in range(len(grid) - 1):
                    a, b = grid[gi], grid[gi + 1]
                    pts = [a + (b - a) * k / (split + 1)
                           for k in range(split + 2)]
                    for k in range(len(pts) - 1):
                        rel = []
                        if rel_end is None and k == 0:
                            rel.append("Mi")
                        if rel_end is None and k == len(pts) - 2:
                            rel.append("Mj")
                        P = ((lambda u: (u, v, zt)) if axis == "X"
                             else (lambda u: (v, u, zt)))
                        c.member("beam", gir, P(pts[k]), P(pts[k + 1]),
                                 st.name,
                                 f"G{axis}{si + 1}-{_alpha(gi) if axis == 'X' else gi + 1}"
                                 f"{li + 1}-{k + 1}",
                                 releases=",".join(rel))
        # infill beams + deck strips
        for yi in range(len(ys) - 1):
            for xi in range(len(xs) - 1):
                x0, x1, y0, y1 = xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]
                if along_y:
                    cuts = [x0 + (x1 - x0) * k / (n + 1) for k in range(n + 2)]
                    for k in range(1, n + 1):
                        c.member("beam", inf, (cuts[k], y0, zt),
                                 (cuts[k], y1, zt), st.name,
                                 f"IB{si + 1}-{_alpha(xi)}{yi + 1}-{k}",
                                 releases=PINNED)
                    for k in range(n + 1):
                        c.slab("DECK", [(cuts[k], y0, zt),
                                        (cuts[k + 1], y0, zt),
                                        (cuts[k + 1], y1, zt),
                                        (cuts[k], y1, zt)], st.name,
                               f"F{si + 1}-{_alpha(xi)}{yi + 1}-{k + 1}",
                               c.is_roof(si), one_way_dir=1)
                else:
                    cuts = [y0 + (y1 - y0) * k / (n + 1) for k in range(n + 2)]
                    for k in range(1, n + 1):
                        c.member("beam", inf, (x0, cuts[k], zt),
                                 (x1, cuts[k], zt), st.name,
                                 f"IB{si + 1}-{_alpha(xi)}{yi + 1}-{k}",
                                 releases=PINNED)
                    for k in range(n + 1):
                        # first edge along Y: local 1 = Y = deck span
                        c.slab("DECK", [(x0, cuts[k], zt),
                                        (x0, cuts[k + 1], zt),
                                        (x1, cuts[k + 1], zt),
                                        (x1, cuts[k], zt)], st.name,
                               f"F{si + 1}-{_alpha(xi)}{yi + 1}-{k + 1}",
                               c.is_roof(si), one_way_dir=1)


# --------------------------------------------------------------------------- #
# concrete builders
# --------------------------------------------------------------------------- #
def _flat_slab_perimeter(c: _Ctx) -> None:
    p, m = c.p, c.m
    xs, ys = _lines(p["bays_x"], p["bay_x"]), _lines(p["bays_y"], p["bay_y"])
    c.grid(xs, ys)
    c.stories()
    col = c.rect("COL", p["column"], p["column"])
    pb = c.rect("PBEAM", p["beam_b"], p["beam_h"])
    slab = c.shell_sec("SLAB", p["slab"])

    def perim(axis, v):
        lines = ys if axis == "X" else xs
        return abs(v - lines[0]) < 1e-9 or abs(v - lines[-1]) < 1e-9

    _grid_frame(c, xs, ys, lambda x, y: col, lambda *a: (pb, ""),
                skip_beam=lambda axis, v, a, b: not perim(axis, v))
    mesh = (p["mesh_size"]
            or round(max(0.5, min(p["bay_x"], p["bay_y"]) / 4.0), 6))
    for si, st in enumerate(m.stories):
        z = st.elevation
        for yi in range(len(ys) - 1):
            for xi in range(len(xs) - 1):
                x0, x1, y0, y1 = xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]
                c.slab(slab, [(x0, y0, z), (x1, y0, z), (x1, y1, z),
                              (x0, y1, z)], st.name,
                       f"F{si + 1}-{_alpha(xi)}{yi + 1}", c.is_roof(si),
                       behavior="shell", mesh=mesh)


def _core_box(c: _Ctx, x0, x1, y0, y1, xs, ys, sec, prefix="CW"):
    """Box core walls along the grid lines bounding [x0,x1]x[y0,y1]; one
    region per grid bay per story; piers P1 (south) P2 (east) P3 (north)
    P4 (west)."""
    gx = [x for x in xs if _between(x, x0, x1)]
    gy = [y for y in ys if _between(y, y0, y1)]
    for si, st in enumerate(c.m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        k = 0
        for pier, pts in (("P1", [(x, y0) for x in gx]),
                          ("P2", [(x1, y) for y in gy]),
                          ("P3", [(x, y1) for x in gx]),
                          ("P4", [(x0, y) for y in gy])):
            for a, b in zip(pts, pts[1:]):
                k += 1
                c.wall(sec, [(a[0], a[1], zb), (b[0], b[1], zb),
                             (b[0], b[1], zt), (a[0], a[1], zt)], st.name,
                       f"{prefix}{si + 1}-{k}", pier=pier)


def _rc_dual_core(c: _Ctx) -> None:
    p = c.p
    xs, ys = _lines(p["bays_x"], p["bay_x"]), _lines(p["bays_y"], p["bay_y"])
    c.grid(xs, ys)
    c.stories()
    col = c.rect("COL", p["column"], p["column"])
    bm = c.rect("BEAM", p["beam_b"], p["beam_h"])
    slab = c.shell_sec("SLAB", p["slab"])
    wall = c.shell_sec("WALL", p["wall"])
    i0 = (p["bays_x"] - p["core_bays_x"]) // 2
    j0 = (p["bays_y"] - p["core_bays_y"]) // 2
    cx0, cx1 = xs[i0], xs[i0 + p["core_bays_x"]]
    cy0, cy1 = ys[j0], ys[j0 + p["core_bays_y"]]

    def in_core(x, y):          # on or inside the core outline
        return _between(x, cx0, cx1) and _between(y, cy0, cy1)

    def skip_beam(axis, v, a, b):
        if axis == "X":
            return in_core(a, v) and in_core(b, v)
        return in_core(v, a) and in_core(v, b)

    _grid_frame(c, xs, ys, lambda x, y: col, lambda *a: (bm, ""),
                skip_col=in_core, skip_beam=skip_beam)
    _core_box(c, cx0, cx1, cy0, cy1, xs, ys, wall)
    _deck_slabs(c, xs, ys, slab, skip=lambda xi, yi, x0, x1, y0, y1:
                in_core(0.5 * (x0 + x1), 0.5 * (y0 + y1)))


def shear_wall_lines(p: dict) -> Tuple[List[float], List[float], dict]:
    """Grid lines (regular + wall edges) and the wall extents of the
    rc_shear_wall plan."""
    Lx, Ly = p["bays_x"] * p["bay_x"], p["bays_y"] * p["bay_y"]
    pl, o = p["pier_length"], p["opening"]
    xa = Lx / 2.0 - o / 2.0 - pl
    xo0, xo1 = Lx / 2.0 - o / 2.0, Lx / 2.0 + o / 2.0
    xb = xo1 + pl
    ya, yb = Ly / 2.0 - p["planar_length"] / 2.0, \
        Ly / 2.0 + p["planar_length"] / 2.0
    xs = sorted({_r(v) for v in _lines(p["bays_x"], p["bay_x"])
                 + [xa, xo0, xo1, xb]})
    ys = sorted({_r(v) for v in _lines(p["bays_y"], p["bay_y"]) + [ya, yb]})
    ext = {"xa": _r(xa), "xo0": _r(xo0), "xo1": _r(xo1), "xb": _r(xb),
           "ya": _r(ya), "yb": _r(yb), "Lx": _r(Lx), "Ly": _r(Ly)}
    return xs, ys, ext


def _rc_shear_wall(c: _Ctx) -> None:
    p, m = c.p, c.m
    xs, ys, e = shear_wall_lines(p)
    gx = _lines(p["bays_x"], p["bay_x"])
    gy = _lines(p["bays_y"], p["bay_y"])
    c.grid(xs, ys)
    c.stories()
    col = c.rect("COL", p["column"], p["column"])
    bm = c.rect("BEAM", p["beam_b"], p["beam_h"])
    slab = c.shell_sec("SLAB", p["slab"])
    wall = c.shell_sec("WALL", p["wall"])
    Lx, Ly = e["Lx"], e["Ly"]
    d = float(p["spandrel_depth"])

    def on_xface(y):
        return abs(y) < 1e-9 or abs(y - Ly) < 1e-9

    def on_yface(x):
        return abs(x) < 1e-9 or abs(x - Lx) < 1e-9

    def in_wall(x, y):
        if on_xface(y) and _between(x, e["xa"], e["xb"]):
            return True
        return on_yface(x) and _between(y, e["ya"], e["yb"])

    for si, st in enumerate(m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        # columns at regular grid points outside the walls
        for yi, y in enumerate(gy):
            for xi, x in enumerate(gx):
                if in_wall(x, y):
                    continue
                c.member("column", col, (x, y, zb), (x, y, zt), st.name,
                         f"C{si + 1}-{_alpha(xi)}{yi + 1}")
        # beams: X-direction on the regular y lines, Y-direction on the
        # regular x lines, every segment between consecutive grid points
        # that is not inside a wall / opening
        k = 0
        for y in gy:
            for a, b in zip(xs, xs[1:]):
                if in_wall(a, y) and in_wall(b, y):
                    continue
                k += 1
                c.member("beam", bm, (a, y, zt), (b, y, zt), st.name,
                         f"BX{si + 1}-{k}")
        for x in gx:
            for a, b in zip(ys, ys[1:]):
                if in_wall(x, a) and in_wall(x, b):
                    continue
                k += 1
                c.member("beam", bm, (x, a, zt), (x, b, zt), st.name,
                         f"BY{si + 1}-{k}")
        # coupled walls on the two X faces: piers split at the spandrel
        # soffit so every region shares full edges (conforming meshes).
        # Only the lower panel carries the pier label: the engine cuts every
        # labelled region at its own base, so labelling the short panel
        # beside the spandrel too would add a second cut at the soffit.
        zs = zt - d
        for fi, y in enumerate((0.0, Ly)):
            pl, pr = f"P{2 * fi + 1}", f"P{2 * fi + 2}"
            spd = f"S{fi + 1}"
            for lab, a, b in ((pl, e["xa"], e["xo0"]),
                              (pr, e["xo1"], e["xb"])):
                c.wall(wall, [(a, y, zb), (b, y, zb), (b, y, zs),
                              (a, y, zs)], st.name,
                       f"W{si + 1}-{lab}L", pier=lab)
                c.wall(wall, [(a, y, zs), (b, y, zs), (b, y, zt),
                              (a, y, zt)], st.name,
                       f"W{si + 1}-{lab}U")
            c.wall(wall, [(e["xo0"], y, zs), (e["xo1"], y, zs),
                          (e["xo1"], y, zt), (e["xo0"], y, zt)], st.name,
                   f"W{si + 1}-{spd}", spandrel=spd)
        # planar walls on the two Y faces
        for fi, x in enumerate((0.0, Lx)):
            c.wall(wall, [(x, e["ya"], zb), (x, e["yb"], zb),
                          (x, e["yb"], zt), (x, e["ya"], zt)], st.name,
                   f"W{si + 1}-PX{fi + 1}", pier=f"PX{fi + 1}")
    m.spandrel_labels = ["S1", "S2"]
    _deck_slabs(c, xs, ys, slab)


def _rc_framed_tube(c: _Ctx) -> None:
    p, m = c.p, c.m
    gx, gy = _lines(p["bays_x"], p["bay_x"]), _lines(p["bays_y"], p["bay_y"])
    n = int(p["tube_subdiv"])
    tx = _lines(p["bays_x"] * n, p["bay_x"] / n)
    ty = _lines(p["bays_y"] * n, p["bay_y"] / n)
    Lx, Ly = gx[-1], gy[-1]
    c.grid(gx, gy)
    c.stories()
    # tube columns: depth along the face (rect h runs along local 2)
    tcx = c.rect("TCOL", p["tube_column_b"], p["tube_column_h"])
    spd = c.rect("SPANDREL", p["spandrel_b"], p["spandrel_h"])
    col = c.rect("COL", p["column"], p["column"])
    bm = c.rect("BEAM", p["beam_b"], p["beam_h"])
    slab = c.shell_sec("SLAB", p["slab"])
    core = p["inner_core"] == "yes"
    if core:
        wall = c.shell_sec("WALL", p["wall"])
        if p["bays_x"] % 2:
            i0, j0 = p["bays_x"] // 2, p["bays_y"] // 2
            cx0, cx1, cy0, cy1 = gx[i0], gx[i0 + 1], gy[j0], gy[j0 + 1]
        else:
            i0, j0 = p["bays_x"] // 2 - 1, p["bays_y"] // 2 - 1
            cx0, cx1, cy0, cy1 = gx[i0], gx[i0 + 2], gy[j0], gy[j0 + 2]

    def in_core(x, y):
        return core and _between(x, cx0, cx1) and _between(y, cy0, cy1)

    def perim(x, y):
        return (abs(x) < 1e-9 or abs(x - Lx) < 1e-9 or abs(y) < 1e-9
                or abs(y - Ly) < 1e-9)

    ring = ([(x, 0.0) for x in tx] + [(Lx, y) for y in ty[1:]]
            + [(x, Ly) for x in reversed(tx[:-1])]
            + [(0.0, y) for y in reversed(ty[1:-1])])
    for si, st in enumerate(m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        for k, (x, y) in enumerate(ring):
            # depth along the face: X faces angle 0, Y faces 90; corners
            # alternate like the moment-frame corners
            ang = _perimeter_angle(x, y, [0.0, Lx], [0.0, Ly])
            c.member("column", tcx, (x, y, zb), (x, y, zt), st.name,
                     f"TC{si + 1}-{k + 1}", angle=ang)
        for k in range(len(ring)):
            a, b = ring[k], ring[(k + 1) % len(ring)]
            c.member("beam", spd, (a[0], a[1], zt), (b[0], b[1], zt),
                     st.name, f"SP{si + 1}-{k + 1}")
        for yi, y in enumerate(gy):
            for xi, x in enumerate(gx):
                if perim(x, y) or in_core(x, y):
                    continue
                c.member("column", col, (x, y, zb), (x, y, zt), st.name,
                         f"C{si + 1}-{_alpha(xi)}{yi + 1}")
        for yi, y in enumerate(gy[1:-1], start=1):
            for xi in range(len(gx) - 1):
                a, b = gx[xi], gx[xi + 1]
                if in_core(a, y) and in_core(b, y):
                    continue
                c.member("beam", bm, (a, y, zt), (b, y, zt), st.name,
                         f"BX{si + 1}-{_alpha(xi)}{yi + 1}")
        for xi, x in enumerate(gx[1:-1], start=1):
            for yi in range(len(gy) - 1):
                a, b = gy[yi], gy[yi + 1]
                if in_core(x, a) and in_core(x, b):
                    continue
                c.member("beam", bm, (x, a, zt), (x, b, zt), st.name,
                         f"BY{si + 1}-{_alpha(xi)}{yi + 1}")
    if core:
        _core_box(c, cx0, cx1, cy0, cy1, gx, gy, wall)
    _deck_slabs(c, gx, gy, slab, skip=lambda xi, yi, x0, x1, y0, y1:
                in_core(0.5 * (x0 + x1), 0.5 * (y0 + y1)))


# --------------------------------------------------------------------------- #
# tall / special builders
# --------------------------------------------------------------------------- #
def _outrigger(c: _Ctx) -> None:
    p, m = c.p, c.m
    xs, ys = _lines(p["bays_x"], p["bay_x"]), _lines(p["bays_y"], p["bay_y"])
    c.grid(xs, ys)
    c.stories()
    col, bm = c.steel_sec("column"), c.steel_sec("beam")
    ch, dg = c.steel_sec("chord"), c.steel_sec("diagonal")
    slab = c.shell_sec("SLAB", p["slab"])
    wall = c.shell_sec("WALL", p["wall"])
    i0, j0 = p["bays_x"] // 2, p["bays_y"] // 2
    cx0, cx1, cy0, cy1 = xs[i0], xs[i0 + 1], ys[j0], ys[j0 + 1]
    levels = outrigger_levels(p["outrigger_levels"], p["stories"])

    def in_core(x, y):
        return _between(x, cx0, cx1) and _between(y, cy0, cy1)

    def core_line(axis, v):
        return (abs(v - cy0) < 1e-9 or abs(v - cy1) < 1e-9) if axis == "X" \
            else (abs(v - cx0) < 1e-9 or abs(v - cx1) < 1e-9)

    def perim(axis, v):
        lines = ys if axis == "X" else xs
        return abs(v - lines[0]) < 1e-9 or abs(v - lines[-1]) < 1e-9

    # chords: floor beams at the top / bottom of every outrigger story on
    # the core lines (outriggers) and the perimeter (belt trusses)
    chord_z = set()
    for k in levels:
        st = m.stories[k - 1]
        chord_z.update({_r(st.elevation), _r(st.elevation - st.height)})
    for si, st in enumerate(m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        chord_level = _r(zt) in chord_z
        for yi, y in enumerate(ys):
            for xi, x in enumerate(xs):
                if in_core(x, y):
                    continue
                c.member("column", col, (x, y, zb), (x, y, zt), st.name,
                         f"C{si + 1}-{_alpha(xi)}{yi + 1}",
                         angle=_perimeter_angle(x, y, xs, ys)
                         if (x in (xs[0], xs[-1]) or y in (ys[0], ys[-1]))
                         else 0.0, releases="Mi" if si == 0 else "")
        for axis, lines, grid in (("X", ys, xs), ("Y", xs, ys)):
            for li, v in enumerate(lines):
                for gi in range(len(grid) - 1):
                    a, b = grid[gi], grid[gi + 1]
                    pa = (a, v) if axis == "X" else (v, a)
                    pb = (b, v) if axis == "X" else (v, b)
                    if in_core(*pa) and in_core(*pb):
                        continue
                    is_chord = chord_level and (core_line(axis, v)
                                                or perim(axis, v))
                    c.member("beam", ch if is_chord else bm,
                             (*pa, zt), (*pb, zt), st.name,
                             f"B{axis}{si + 1}-{_alpha(gi) if axis == 'X' else gi + 1}{li + 1}",
                             releases="" if is_chord else PINNED)
        # outrigger / belt diagonals of this story
        if (si + 1) in levels:
            k = 0
            for axis, lines, grid in (("X", ys, xs), ("Y", xs, ys)):
                for v in lines:
                    if not (core_line(axis, v) or perim(axis, v)):
                        continue
                    for gi in range(len(grid) - 1):
                        a, b = grid[gi], grid[gi + 1]
                        pa = (a, v) if axis == "X" else (v, a)
                        pb = (b, v) if axis == "X" else (v, b)
                        if in_core(*pa) and in_core(*pb):
                            continue
                        if core_line(axis, v) and not perim(axis, v):
                            # outrigger arm: one diagonal per bay rising
                            # toward the core (mirror-symmetric arms)
                            mid = 0.5 * (grid[0] + grid[-1])
                            up_at_b = abs(b - mid) < abs(a - mid)
                            lo, hi = (pa, pb) if up_at_b else (pb, pa)
                            pairs = [(lo, hi)]
                            tag = "OT"
                        else:
                            # belt truss: X diagonals in every bay
                            pairs = [(pa, pb), (pb, pa)]
                            tag = "BT"
                        for lo, hi in pairs:
                            k += 1
                            c.member("brace", dg, (*lo, zb), (*hi, zt),
                                     st.name, f"{tag}{si + 1}-{k}",
                                     releases=PINNED)
    _core_box(c, cx0, cx1, cy0, cy1, xs, ys, wall)
    _deck_slabs(c, xs, ys, slab, skip=lambda xi, yi, x0, x1, y0, y1:
                in_core(0.5 * (x0 + x1), 0.5 * (y0 + y1)))


def podium_layout(p: dict) -> List[Tuple[int, int]]:
    """``[(first bay x, first bay y)]`` of every tower on the podium grid."""
    tbx, tby = p["tower_bays_x"], p["tower_bays_y"]
    j0 = (p["bays_y"] - tby) // 2
    if p["towers"] == 1:
        return [((p["bays_x"] - tbx) // 2, j0)]
    gap = p["bays_x"] - 2 * tbx
    e = gap // 4 if gap >= 4 else 0       # symmetric end margin
    return [(e, j0), (p["bays_x"] - e - tbx, j0)]


def _rc_frame_block(c, xs, ys, stories, col, bm, slab, prefix, tower="",
                    has_roof=True):
    for st in stories:
        zt, zb = st.elevation, st.elevation - st.height
        roof = has_roof and st is stories[-1]
        for yi, y in enumerate(ys):
            for xi, x in enumerate(xs):
                mm = c.member("column", col, (x, y, zb), (x, y, zt), st.name,
                              f"{prefix}C{st.name}-{_alpha(xi)}{yi + 1}")
                mm.tower = tower
        for yi, y in enumerate(ys):
            for xi in range(len(xs) - 1):
                mm = c.member("beam", bm, (xs[xi], y, zt),
                              (xs[xi + 1], y, zt), st.name,
                              f"{prefix}BX{st.name}-{_alpha(xi)}{yi + 1}")
                mm.tower = tower
        for xi, x in enumerate(xs):
            for yi in range(len(ys) - 1):
                mm = c.member("beam", bm, (x, ys[yi], zt),
                              (x, ys[yi + 1], zt), st.name,
                              f"{prefix}BY{st.name}-{_alpha(xi)}{yi + 1}")
                mm.tower = tower
        for yi in range(len(ys) - 1):
            for xi in range(len(xs) - 1):
                x0, x1, y0, y1 = xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]
                r = c.slab(slab, [(x0, y0, zt), (x1, y0, zt), (x1, y1, zt),
                                  (x0, y1, zt)], st.name,
                           f"{prefix}F{st.name}-{_alpha(xi)}{yi + 1}", roof)
                r.tower = tower


def _podium_tower(c: _Ctx) -> None:
    p, m = c.p, c.m
    xs, ys = _lines(p["bays_x"], p["bay_x"]), _lines(p["bays_y"], p["bay_y"])
    col = c.rect("COL", p["column"], p["column"])
    bm = c.rect("BEAM", p["beam_b"], p["beam_h"])
    slab = c.shell_sec("SLAB", p["slab"])
    gp = GridSystem(xs, ys, name="GP")
    m.grid_systems = [gp]
    m.grid = gp
    h1 = p["first_story_height"] or p["story_height"]
    hp = [h1] + [p["story_height"]] * (p["stories"] - 1)
    z = 0.0
    pod = []
    for i, h in enumerate(hp):
        z += h
        s = Story(f"P{i + 1}", h, z)
        s.tower = "P"
        m.stories.append(s)
        pod.append(s)
    zp = z
    towers = [{"name": "P", "grid_system": "GP", "base_elevation": 0.0}]
    blocks = [(xs, ys, pod, "", "P")]
    for ti, (i0, j0) in enumerate(podium_layout(p)):
        tn = f"T{ti + 1}"
        tx = xs[i0:i0 + p["tower_bays_x"] + 1]
        ty = ys[j0:j0 + p["tower_bays_y"] + 1]
        g = GridSystem([_r(x - tx[0]) for x in tx],
                       [_r(y - ty[0]) for y in ty], name="G" + tn,
                       origin=(tx[0], ty[0]))
        m.grid_systems.append(g)
        z = zp
        sts = []
        for k in range(p["tower_stories"]):
            z += p["tower_story_height"]
            s = Story(f"{tn}-{k + 1}", p["tower_story_height"], _r(z))
            s.tower = tn
            m.stories.append(s)
            sts.append(s)
        towers.append({"name": tn, "grid_system": "G" + tn,
                       "base_elevation": _r(zp)})
        blocks.append((tx, ty, sts, tn, tn))
    m.towers = towers
    for xs_, ys_, sts, prefix, tn in blocks:
        # the podium roof is an occupied plaza (LIVE); tower tops are roofs
        _rc_frame_block(c, xs_, ys_, sts, col, bm, slab, prefix, tn,
                        has_roof=tn != "P")


def isolator_stiffness(total_mass_t: float, n: int, T: float) -> float:
    """Per-isolator effective stiffness (kN/m): ``K = 4 pi^2 M / T^2``
    shared equally by ``n`` isolators."""
    return 4.0 * math.pi ** 2 * total_mass_t / (T * T) / n


def _base_isolated(c: _Ctx) -> None:
    p, m = c.p, c.m
    xs, ys = _lines(p["bays_x"], p["bay_x"]), _lines(p["bays_y"], p["bay_y"])
    c.grid(xs, ys)
    h1 = p["first_story_height"] or p["story_height"]
    hiso = float(p["isolator_height"])
    c.stories([hiso, h1] + [p["story_height"]] * (p["stories"] - 1),
              ["ISO"] + [f"Story{i + 1}" for i in range(p["stories"])])
    col = c.rect("COL", p["column"], p["column"])
    bm = c.rect("BEAM", p["beam_b"], p["beam_h"])
    bb = c.rect("BASEBEAM", p["base_beam_b"], p["base_beam_h"])
    slab = c.shell_sec("SLAB", p["slab"])
    iso = m.stories[0]
    z0 = iso.elevation
    for yi, y in enumerate(ys):
        for xi in range(len(xs) - 1):
            c.member("beam", bb, (xs[xi], y, z0), (xs[xi + 1], y, z0),
                     iso.name, f"BBX-{_alpha(xi)}{yi + 1}")
    for xi, x in enumerate(xs):
        for yi in range(len(ys) - 1):
            c.member("beam", bb, (x, ys[yi], z0), (x, ys[yi + 1], z0),
                     iso.name, f"BBY-{_alpha(xi)}{yi + 1}")
    for yi in range(len(ys) - 1):
        for xi in range(len(xs) - 1):
            x0, x1, y0, y1 = xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]
            c.slab(slab, [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0),
                          (x0, y1, z0)], iso.name,
                   f"FB-{_alpha(xi)}{yi + 1}", False)
    for si, st in enumerate(m.stories[1:], start=1):
        zt, zb = st.elevation, st.elevation - st.height
        for yi, y in enumerate(ys):
            for xi, x in enumerate(xs):
                c.member("column", col, (x, y, zb), (x, y, zt), st.name,
                         f"C{si}-{_alpha(xi)}{yi + 1}")
        for yi, y in enumerate(ys):
            for xi in range(len(xs) - 1):
                c.member("beam", bm, (xs[xi], y, zt), (xs[xi + 1], y, zt),
                         st.name, f"BX{si}-{_alpha(xi)}{yi + 1}")
        for xi, x in enumerate(xs):
            for yi in range(len(ys) - 1):
                c.member("beam", bm, (x, ys[yi], zt), (x, ys[yi + 1], zt),
                         st.name, f"BY{si}-{_alpha(xi)}{yi + 1}")
        for yi in range(len(ys) - 1):
            for xi in range(len(xs) - 1):
                x0, x1, y0, y1 = xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]
                c.slab(slab, [(x0, y0, zt), (x1, y0, zt), (x1, y1, zt),
                              (x0, y1, zt)], st.name,
                       f"F{si}-{_alpha(xi)}{yi + 1}", c.is_roof(si))
    # isolators (stiffness set after the loads exist: needs the mass)
    for yi, y in enumerate(ys):
        for xi, x in enumerate(xs):
            m.add_link((x, y, 0.0), (x, y, z0), [1.0, 1.0, p["isolator_kv"],
                                                 0.0, 0.0, 0.0],
                       uid=f"ISO-{_alpha(xi)}{yi + 1}")
            m.supports.append(PointSupport((x, y, 0.0), (1, 1, 1, 1, 1, 1)))


def _size_isolators(c: _Ctx) -> None:
    m, p = c.m, c.p
    M = sum(m.compute_story_masses().values())
    k = isolator_stiffness(M, len(m.links), float(p["isolator_period"]))
    for lk in m.links:
        lk.stiffness[0] = lk.stiffness[1] = _r(k)
    c.extra["isolator_keff"] = _r(k)
    c.extra["isolated_mass"] = M


def shed_geometry(p: dict) -> dict:
    span = p["bays_x"] * p["bay_x"]
    eave = p["story_height"]
    rise = 0.5 * span * math.tan(math.radians(p["pitch"])) \
        if p["roof"] == "portal" else 0.0
    return {"span": _r(span), "eave": _r(eave), "ridge": _r(eave + rise),
            "length": _r(p["bays_y"] * p["bay_y"])}


def _industrial_shed(c: _Ctx) -> None:
    p, m = c.p, c.m
    g = shed_geometry(p)
    span, eave, ridge = g["span"], g["eave"], g["ridge"]
    ys = _lines(p["bays_y"], p["bay_y"])
    c.grid([0.0, span / 2.0, span] if p["roof"] == "portal"
           else [0.0, span], ys)
    c.stories([eave], ["Roof"])
    st = m.stories[0]
    col, strut, wbr = (c.steel_sec("column"), c.steel_sec("strut"),
                       c.steel_sec("brace"))
    clad = c.shell_sec("CLADDING", 0.0007, mat=c.steel)
    xm = span / 2.0
    roof_lines = []                   # (x positions, z positions) per frame
    for yi, y in enumerate(ys):
        tag = f"{yi + 1}"
        for xi, x in enumerate((0.0, span)):
            c.member("column", col, (x, y, 0.0), (x, y, eave), st.name,
                     f"C-{'AB'[xi]}{tag}", angle=0.0)
        if p["roof"] == "portal":
            raf = c.steel_sec("rafter")
            c.member("beam", raf, (0.0, y, eave), (xm, y, ridge), st.name,
                     f"R-{tag}a")
            c.member("beam", raf, (xm, y, ridge), (span, y, eave), st.name,
                     f"R-{tag}b")
        else:
            chd, web = c.steel_sec("chord"), c.steel_sec("web")
            n = int(p["truss_panels"])
            dz = float(p["truss_depth"])
            zt, zb = eave + dz, eave
            px = [span * i / n for i in range(n + 1)]
            # end posts (truss depth) on the columns
            c.member("column", web, (0.0, y, zb), (0.0, y, zt), st.name,
                     f"TP-{tag}a", releases=PINNED)
            c.member("column", web, (span, y, zb), (span, y, zt), st.name,
                     f"TP-{tag}b", releases=PINNED)
            for i in range(n):
                c.member("beam", chd, (px[i], y, zt), (px[i + 1], y, zt),
                         st.name, f"TT-{tag}-{i + 1}")
                c.member("beam", chd, (px[i], y, zb), (px[i + 1], y, zb),
                         st.name, f"TB-{tag}-{i + 1}")
                if 0 < i:
                    c.member("column", web, (px[i], y, zb), (px[i], y, zt),
                             st.name, f"TV-{tag}-{i}", releases=PINNED)
                # Pratt: diagonals slope down toward mid-span
                if i < n // 2:
                    a, b = (px[i], y, zt), (px[i + 1], y, zb)
                else:
                    a, b = (px[i], y, zb), (px[i + 1], y, zt)
                c.member("brace", web, a, b, st.name, f"TD-{tag}-{i + 1}",
                         releases=PINNED)
            ridge = zt
    # longitudinal members between frames + cladding
    for yi in range(len(ys) - 1):
        y0, y1 = ys[yi], ys[yi + 1]
        tag = f"{yi + 1}"
        top = eave if p["roof"] == "portal" else eave + p["truss_depth"]
        for xi, x in enumerate((0.0, span)):
            c.member("beam", strut, (x, y0, top), (x, y1, top), st.name,
                     f"ES-{'AB'[xi]}{tag}", releases=PINNED)
            if p["roof"] == "truss":
                c.member("beam", strut, (x, y0, eave), (x, y1, eave),
                         st.name, f"EB-{'AB'[xi]}{tag}", releases=PINNED)
        if p["roof"] == "portal":
            c.member("beam", strut, (xm, y0, ridge), (xm, y1, ridge),
                     st.name, f"RP-{tag}", releases=PINNED)
            planes = [[(0.0, y0, eave), (0.0, y1, eave), (xm, y1, ridge),
                       (xm, y0, ridge)],
                      [(xm, y0, ridge), (xm, y1, ridge), (span, y1, eave),
                       (span, y0, eave)]]
        else:
            planes = [[(0.0, y0, top), (0.0, y1, top), (span, y1, top),
                       (span, y0, top)]]
        for k, pl in enumerate(planes):
            c.slab(clad, pl, st.name, f"RF-{tag}{'ab'[k]}", True,
                   one_way_dir=1)
        # side-wall X bracing in the two end bays
        if yi in (0, len(ys) - 2):
            for xi, x in enumerate((0.0, span)):
                c.member("brace", wbr, (x, y0, 0.0), (x, y1, eave), st.name,
                         f"WB-{'AB'[xi]}{tag}a", releases=PINNED)
                c.member("brace", wbr, (x, y1, 0.0), (x, y0, eave), st.name,
                         f"WB-{'AB'[xi]}{tag}b", releases=PINNED)
    m.diaphragm = "none"
    m.rigid_diaphragms = False


def diagrid_nodes(n: int, a: float, level: int) -> List[float]:
    """Face positions of the diagrid nodes at module level ``level``:
    grid points (even) or mid-panel points (odd), corners always."""
    L = n * a
    if level % 2 == 0:
        return [_r(i * a) for i in range(n + 1)]
    return [0.0] + [_r((i + 0.5) * a) for i in range(n)] + [_r(L)]


def _diagrid(c: _Ctx) -> None:
    p, m = c.p, c.m
    n, a = p["bays_x"], p["bay_x"]
    L = n * a
    xs = _lines(n, a)
    c.grid(xs, xs)
    c.stories()
    dg, rg, cc = (c.steel_sec("diagonal"), c.steel_sec("ring"),
                  c.steel_sec("corner"))
    slab = c.shell_sec("SLAB", p["slab"])
    wall = c.shell_sec("WALL", p["wall"])
    cs = float(p["core_size"])
    c0, c1 = _r(L / 2.0 - cs / 2.0), _r(L / 2.0 + cs / 2.0)
    m.grid = GridSystem(sorted({*xs, c0, c1}), sorted({*xs, c0, c1}))
    nst = len(m.stories)
    zlev = [0.0] + [s.elevation for s in m.stories]

    def face_pt(f, u, z):
        """Face f (0 S, 1 E, 2 N, 3 W) position u (0..L, ccw) -> xyz."""
        return [(u, 0.0, z), (L, u, z), (L - u, L, z), (0.0, L - u, z)][f]

    floor_nodes = {k: set() for k in range(nst + 1)}  # level -> {(f, u)}
    for mod in range((nst + 1) // 2):
        kb, kt = 2 * mod, min(2 * mod + 2, nst)
        kmid = kb + 1 if kt - kb == 2 else None
        nb, nt = diagrid_nodes(n, a, mod), diagrid_nodes(n, a, mod + 1)
        zb, zt = zlev[kb], zlev[kt]
        for f in range(4):
            dk = 0
            for u in nb:
                for v in nt:
                    if abs(abs(v - u) - a / 2.0) > 1e-6:
                        continue
                    dk += 1
                    st = m.stories[kt - 1]
                    if kmid is None:
                        c.member("brace", dg, face_pt(f, u, zb),
                                 face_pt(f, v, zt), st.name,
                                 f"DG{kb}-{f}-{dk}")
                        floor_nodes[kb].add((f, u))
                        floor_nodes[kt].add((f, v))
                        continue
                    um = _r(0.5 * (u + v))
                    zm = zlev[kmid]
                    c.member("brace", dg, face_pt(f, u, zb),
                             face_pt(f, um, zm), m.stories[kmid - 1].name,
                             f"DG{kb}-{f}-{dk}a")
                    c.member("brace", dg, face_pt(f, um, zm),
                             face_pt(f, v, zt), st.name, f"DG{kb}-{f}-{dk}b")
                    floor_nodes[kb].add((f, u))
                    floor_nodes[kmid].add((f, um))
                    floor_nodes[kt].add((f, v))
    # corner columns (every story) and ring beams (every floor)
    for si, st in enumerate(m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        for f in range(4):
            c.member("column", cc, face_pt(f, 0.0, zb), face_pt(f, 0.0, zt),
                     st.name, f"CC{si + 1}-{f}")
            us = sorted({u for (ff, u) in floor_nodes[si + 1] if ff == f}
                        | {0.0, _r(L)})
            for k in range(len(us) - 1):
                c.member("beam", rg, face_pt(f, us[k], zt),
                         face_pt(f, us[k + 1], zt), st.name,
                         f"RB{si + 1}-{f}-{k + 1}", releases="")
    # core walls (one region per side per story) + trapezoid floor plates
    for si, st in enumerate(m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        cpts = [(c0, c0), (c1, c0), (c1, c1), (c0, c1)]
        for k in range(4):
            (xa, ya), (xb, yb) = cpts[k], cpts[(k + 1) % 4]
            c.wall(wall, [(xa, ya, zb), (xb, yb, zb), (xb, yb, zt),
                          (xa, ya, zt)], st.name, f"CW{si + 1}-{k + 1}",
                   pier=f"P{k + 1}")
        outer = [(0.0, 0.0), (L, 0.0), (L, L), (0.0, L)]
        for k in range(4):
            (oa, ob), (ia, ib) = ((outer[k], outer[(k + 1) % 4]),
                                  (cpts[k], cpts[(k + 1) % 4]))
            c.slab(slab, [(oa[0], oa[1], zt), (ob[0], ob[1], zt),
                          (ib[0], ib[1], zt), (ia[0], ia[1], zt)], st.name,
                   f"F{si + 1}-{k + 1}", c.is_roof(si))


_BUILDERS = {
    "frame_2d": _frame_2d,
    "steel_smf": _steel_smf,
    "steel_cbf": lambda c: _steel_braced(c, ebf=False),
    "steel_ebf": lambda c: _steel_braced(c, ebf=True),
    "steel_deck": _steel_deck,
    "flat_slab_perimeter": _flat_slab_perimeter,
    "rc_dual_core": _rc_dual_core,
    "rc_shear_wall": _rc_shear_wall,
    "rc_framed_tube": _rc_framed_tube,
    "outrigger": _outrigger,
    "podium_tower": _podium_tower,
    "base_isolated": _base_isolated,
    "industrial_shed": _industrial_shed,
    "diagrid": _diagrid,
}


# --------------------------------------------------------------------------- #
# loads, mass source and code loads
# --------------------------------------------------------------------------- #
def _loads(c: _Ctx) -> None:
    m, p = c.m, c.p
    dead = m.pattern("DEAD", "dead")
    dead.self_weight_factor = 1.0
    sdl = m.pattern("SDL", "superdead")
    has_floor_live = any(not roof for _u, roof in c.floors) or any(
        not roof for _u, _w, roof in c.beam_lines)
    live = m.pattern("LIVE", "live") if (p["live"] and has_floor_live) \
        else None
    lroof = m.pattern("LROOF", "roof_live") if p["roof_live"] else None
    for uid, roof in c.floors:
        if p["sdl"]:
            sdl.area_loads.append(AreaLoad(uid, float(p["sdl"])))
        if roof and lroof is not None:
            lroof.area_loads.append(AreaLoad(uid, float(p["roof_live"])))
        elif not roof and live is not None:
            live.area_loads.append(AreaLoad(uid, float(p["live"])))
    for uid, w, roof in c.beam_lines:       # 2-D frame: tributary UDLs
        if p["sdl"]:
            sdl.member_udls.append(MemberUDL(uid, _r(p["sdl"] * w)))
        if roof and lroof is not None:
            lroof.member_udls.append(MemberUDL(uid, _r(p["roof_live"] * w)))
        elif not roof and live is not None:
            live.member_udls.append(MemberUDL(uid, _r(p["live"] * w)))
    for name in ("DEAD", "SDL", "LIVE", "LROOF"):
        if name in m.patterns:
            m.add_case(name, {name: 1.0})
    m.mass_source = {"DEAD": 1.0, "SDL": 1.0}
    if live is not None and p["live_mass_factor"]:
        m.mass_source["LIVE"] = float(p["live_mass_factor"])
    m.num_modes = min(3 * len(m.stories), 12)
    if c.t == "frame_2d":
        m.num_modes = min(len(m.stories), 6)
    if c.t == "industrial_shed":
        m.num_modes = 12


def _summary(c: _Ctx, base: dict) -> dict:
    m = c.m
    piers = sorted({r.pier for r in m.shells if getattr(r, "pier", "")})
    spd = sorted({r.spandrel for r in m.shells
                  if getattr(r, "spandrel", "")}
                 | {mm.spandrel for mm in m.members
                    if getattr(mm, "spandrel", "")})
    base.update({"template": c.t, "params": c.p,
                 "members": len(m.members), "shells": len(m.shells),
                 "links": len(m.links), "stories": len(m.stories),
                 "piers": piers, "spandrels": spd,
                 "towers": [t["name"] for t in m.towers],
                 "diaphragm": ("none" if m.diaphragm == "none"
                               or not m.rigid_diaphragms else "rigid"),
                 "system": SPECS[c.t]["label"]})
    base.update(c.extra)
    return base


def build_system(template: str, params: Optional[dict] = None
                 ) -> Tuple[BuildingModel, dict]:
    """``(model, summary)`` of a structural-system template; the model is
    validated and uses the ETABS frame-axis convention."""
    from . import frame_axes as _fa
    from .templates import apply_code_loads
    p = normalize_params(template, params)
    c = _Ctx(template, p)
    c.extra = {}
    _fa.as_new_model(c.m)
    _BUILDERS[template](c)
    _fa.as_new_model(c.m)
    _loads(c)
    if template == "base_isolated":
        _size_isolators(c)
    dirs = ("X",) if template == "frame_2d" else ("X", "Y")
    summary = apply_code_loads(c.m, p, dirs)
    summary = _summary(c, summary)
    c.m.validate()
    return c.m, summary
