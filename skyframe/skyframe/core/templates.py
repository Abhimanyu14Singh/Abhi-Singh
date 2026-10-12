"""ETABS-style structural model templates (File > New Model).

CONTRACT "Model templates, general grids, shell editing and drawing aids".

Five templates complete the start-screen set (2D frame, steel moment frame,
flat plate, braced frame, shear-wall core):

* ``waffle``          - columns + a two-way WAFFLE slab (ETABS "Waffle Slab"
  slab property): every bay is one shell region whose section carries the
  exact equivalent-stiffness modifiers of the ribbed cross-section;
* ``ribbed``          - columns, beams on every grid line and a one-way
  RIBBED slab (ribs parallel to global X = shell local axis 1);
* ``two_way_beams``   - columns, beams on every grid line, solid two-way slab;
* ``staggered_truss`` - steel staggered-truss system: perimeter columns on
  the two long faces, story-deep transverse trusses on alternate grid lines
  in alternate stories (checkerboard), one-way membrane floors spanning
  between the truss chords;
* ``flat_slab_drop``  - columns + flat slab with DROP PANELS: the slab is
  tessellated by the grid lines and the drop-panel edges so every drop
  panel is its own (thicker) region and all regions share full edges
  (conforming meshes, no hanging nodes).

Every template sets up, through the existing generators:

* load patterns ``DEAD`` (self weight 1 + the superimposed dead area load),
  ``LIVE`` (area load) and the code seismic patterns ``EQX`` / ``EQY``
  (:func:`skyframe.core.autolateral.generate`);
* a code response-spectrum function ``RSF`` (:func:`skyframe.core.
  spectra_codes.make_spectrum_function`) and RS cases ``RSX`` / ``RSY``;
* the mass source ``{DEAD: 1, LIVE: live_mass_factor}``;
* the code's default design combinations (ASCE 7 via :func:`skyframe.core.
  codes.apply_asce7_combinations`, EN 1990 / IS 456 via :func:`skyframe.
  core.combos_codes.apply_code_combinations`).

``code`` is one of :data:`CODES`; ``"none"`` keeps only DEAD / LIVE.

Waffle / ribbed equivalent shell (per rib: flange ``s`` x ``tf``, web
``bw`` x ``(D - tf)``; section thickness = overall depth ``D``)::

    A_T  = s tf + bw (D - tf)
    ybar = (s tf^2/2 + bw (D - tf)(tf + (D - tf)/2)) / A_T      (from top)
    I_T  = s tf^3/12 + s tf (ybar - tf/2)^2
           + bw (D - tf)^3/12 + bw (D - tf)(tf + (D - tf)/2 - ybar)^2
    rib direction:   f = A_T/(s D),  m = I_T/(s D^3/12),  v = bw/s
    other direction: f = tf/D,       m = (tf/D)^3,         v = tf/D
    in-plane shear   f12 = tf/D (topping only)
    twisting         m12 = sqrt(m11 m22)        (Huber orthotropic plate)
    weight = mass = V/D,  V = tf + bw (D - tf)/s            (ribbed)
                          V = tf + bw (D - tf)(2/s - bw/s^2) (waffle)

All quantities are SI (m, kN, kPa).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from .model import (AreaLoad, BuildingModel, FrameSection, GridSystem,
                    Material, ShellSection, library_material)

TEMPLATES = ("waffle", "ribbed", "two_way_beams", "staggered_truss",
             "flat_slab_drop")
CODES = ("asce7", "ec8", "is1893", "none")

TEMPLATE_LABELS = {
    "waffle": "Waffle Slab",
    "ribbed": "Ribbed Slab (one-way joists)",
    "two_way_beams": "Two-Way Slab with Beams",
    "staggered_truss": "Staggered Truss",
    "flat_slab_drop": "Flat Slab with Drop Panels",
}

G = 9.80665

# common parameters: key -> (default, kind, lo, hi)   kind: int|len|num|str
_COMMON = {
    "name": ("", "str", None, None),
    "bays_x": (3, "int", 1, 20),
    "bays_y": (3, "int", 1, 20),
    "bay_x": (8.0, "len", 1.0, 30.0),
    "bay_y": (8.0, "len", 1.0, 30.0),
    "stories": (4, "int", 1, 60),
    "story_height": (3.5, "len", 2.0, 10.0),
    "first_story_height": (None, "len", 2.0, 12.0),
    "sdl": (1.5, "num", 0.0, 50.0),          # kPa superimposed dead
    "live": (2.5, "num", 0.0, 50.0),         # kPa live
    "live_mass_factor": (0.25, "num", 0.0, 1.0),
    "mesh_size": (None, "len", 0.25, 10.0),  # shell target size (None=auto)
    "code": ("asce7", "str", None, None),
    # seismic parameters (used by the chosen code)
    "SDS": (1.0, "num", 0.0, 5.0),
    "SD1": (0.6, "num", 0.0, 5.0),
    "R": (5.0, "num", 0.5, 12.0),
    "Ie": (1.0, "num", 0.5, 2.0),
    "ag": (0.25, "num", 0.0, 2.0),           # EC8 agR (g)
    "q": (3.0, "num", 0.5, 8.0),             # EC8 behaviour factor
    "ground_type": ("B", "str", None, None),
    "Z": (0.24, "num", 0.0, 1.0),            # IS 1893 zone factor
    "soil": ("II", "str", None, None),
}
_SPECIFIC = {
    "waffle": {"column": (0.6, "len", 0.2, 3.0),
               "depth": (0.40, "len", 0.1, 2.0),
               "top": (0.10, "len", 0.04, 0.5),
               "rib_width": (0.15, "len", 0.05, 1.0),
               "rib_spacing": (0.90, "len", 0.2, 3.0)},
    "ribbed": {"column": (0.6, "len", 0.2, 3.0),
               "beam_b": (0.40, "len", 0.1, 3.0),
               "beam_h": (0.60, "len", 0.1, 3.0),
               "depth": (0.35, "len", 0.1, 2.0),
               "top": (0.075, "len", 0.04, 0.5),
               "rib_width": (0.15, "len", 0.05, 1.0),
               "rib_spacing": (0.75, "len", 0.2, 3.0)},
    "two_way_beams": {"column": (0.6, "len", 0.2, 3.0),
                      "beam_b": (0.40, "len", 0.1, 3.0),
                      "beam_h": (0.60, "len", 0.1, 3.0),
                      "slab": (0.18, "len", 0.05, 1.0)},
    "staggered_truss": {"panels": (4, "int", 2, 12)},
    "flat_slab_drop": {"column": (0.6, "len", 0.2, 3.0),
                       "slab": (0.25, "len", 0.05, 1.0),
                       "drop_thickness": (0.15, "len", 0.02, 1.0),
                       "drop_width": (None, "len", 0.3, 15.0)},
}
_STAGGERED_DEFAULTS = {"bays_x": 6, "bays_y": 1, "bay_x": 6.0, "bay_y": 18.0,
                       "story_height": 3.0, "R": 3.25}


def catalogue() -> List[dict]:
    """``[{id, label, params: {key: default}}]`` for the UI (JSON-safe)."""
    out = []
    for t in TEMPLATES:
        p = {k: v[0] for k, v in _COMMON.items()}
        p.update({k: v[0] for k, v in _SPECIFIC[t].items()})
        if t == "staggered_truss":
            p.update(_STAGGERED_DEFAULTS)
        p["name"] = TEMPLATE_LABELS[t]
        out.append({"id": t, "label": TEMPLATE_LABELS[t], "params": p})
    return out


def normalize_params(template: str, params: Optional[dict]) -> dict:
    """Every parameter validated with defaults filled (ValueError)."""
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}; one of "
                         f"{TEMPLATES}")
    params = dict(params or {})
    spec = dict(_COMMON)
    spec.update(_SPECIFIC[template])
    bad = sorted(k for k in params if k not in spec)
    if bad:
        raise ValueError(f"{template}: unknown parameter(s) {bad}")
    out: Dict[str, object] = {}
    for key, (default, kind, lo, hi) in spec.items():
        if template == "staggered_truss" and key in _STAGGERED_DEFAULTS:
            default = _STAGGERED_DEFAULTS[key]
        v = params.get(key, default)
        if v is None:
            out[key] = None
            continue
        if kind == "str":
            if not isinstance(v, str):
                raise ValueError(f"{template}: {key} must be a string")
            out[key] = v.strip()
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
    if out["code"] not in CODES:
        raise ValueError(f"code must be one of {CODES}")
    out["name"] = out["name"] or TEMPLATE_LABELS[template]
    if template in ("waffle", "ribbed"):
        if out["top"] >= out["depth"]:
            raise ValueError(f"{template}: top slab must be thinner than "
                             "the overall depth")
        if out["rib_width"] >= out["rib_spacing"]:
            raise ValueError(f"{template}: rib width must be less than the "
                             "rib spacing")
    if template == "staggered_truss" and out["stories"] < 2:
        raise ValueError("staggered_truss: needs at least 2 stories")
    return out


# --------------------------------------------------------------------------- #
# equivalent ribbed sections
# --------------------------------------------------------------------------- #
def rib_properties(depth: float, top: float, rib_width: float,
                   rib_spacing: float) -> dict:
    """Per-rib T-section properties (see module docstring)."""
    D, tf, bw, s = depth, top, rib_width, rib_spacing
    hw = D - tf
    A = s * tf + bw * hw
    ybar = (s * tf * tf / 2.0 + bw * hw * (tf + hw / 2.0)) / A
    I = (s * tf ** 3 / 12.0 + s * tf * (ybar - tf / 2.0) ** 2
         + bw * hw ** 3 / 12.0 + bw * hw * (tf + hw / 2.0 - ybar) ** 2)
    return {"A": A, "ybar": ybar, "I": I}


def ribbed_modifiers(depth: float, top: float, rib_width: float,
                     rib_spacing: float, two_way: bool) -> dict:
    """Shell modifiers of the equivalent ``depth``-thick section."""
    D, tf, bw, s = depth, top, rib_width, rib_spacing
    rp = rib_properties(D, tf, bw, s)
    f_rib = rp["A"] / (s * D)
    m_rib = rp["I"] / (s * D ** 3 / 12.0)
    f_top, m_top = tf / D, (tf / D) ** 3
    if two_way:
        vol = tf + bw * (D - tf) * (2.0 / s - bw / s ** 2)
        mods = {"f11": f_rib, "f22": f_rib, "m11": m_rib, "m22": m_rib,
                "v13": bw / s, "v23": bw / s}
    else:
        vol = tf + bw * (D - tf) / s
        mods = {"f11": f_rib, "f22": f_top, "m11": m_rib, "m22": m_top,
                "v13": bw / s, "v23": f_top}
    mods["f12"] = f_top
    mods["m12"] = math.sqrt(mods["m11"] * mods["m22"])
    mods["weight"] = mods["mass"] = vol / D
    return mods


# --------------------------------------------------------------------------- #
# building blocks
# --------------------------------------------------------------------------- #
def _alpha(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _base(p: dict, steel: bool = False) -> Tuple[BuildingModel, list, list]:
    mdl = BuildingModel(name=str(p["name"]))
    if steel:
        mat = library_material("A992Fy50")
        mdl.add_material(mat)
        mdl.add_material(Material("CONC", E=25_000_000.0, nu=0.2,
                                  unit_weight=24.0))
    else:
        mdl.add_material(Material("CONC", E=25_000_000.0, nu=0.2,
                                  unit_weight=24.0))
    xs = [round(i * p["bay_x"], 9) for i in range(p["bays_x"] + 1)]
    ys = [round(j * p["bay_y"], 9) for j in range(p["bays_y"] + 1)]
    mdl.grid = GridSystem(xs, ys)
    h1 = p["first_story_height"] or p["story_height"]
    mdl.set_stories([h1] + [p["story_height"]] * (p["stories"] - 1))
    return mdl, xs, ys


def _mesh(p: dict, lx: float, ly: float) -> float:
    if p["mesh_size"]:
        return float(p["mesh_size"])
    return round(max(0.5, min(lx, ly) / 4.0), 6)


def _columns(mdl, xs, ys, sec, only_y=None):
    for si, st in enumerate(mdl.stories):
        zt, zb = st.elevation, st.elevation - st.height
        for yi, y in enumerate(ys):
            if only_y is not None and yi not in only_y:
                continue
            for xi, x in enumerate(xs):
                mdl.add_member("column", sec, (x, y, zb), (x, y, zt),
                               story=st.name,
                               uid=f"C{si + 1}-{_alpha(xi)}{yi + 1}")


def _grid_beams(mdl, xs, ys, sec, y_only=None, x_lines=True):
    for si, st in enumerate(mdl.stories):
        z = st.elevation
        for yi, y in enumerate(ys):
            if y_only is not None and yi not in y_only:
                continue
            for xi in range(len(xs) - 1):
                mdl.add_member("beam", sec, (xs[xi], y, z), (xs[xi + 1], y, z),
                               story=st.name,
                               uid=f"BX{si + 1}-{_alpha(xi)}{yi + 1}")
        if not x_lines:
            continue
        for xi, x in enumerate(xs):
            for yi in range(len(ys) - 1):
                mdl.add_member("beam", sec, (x, ys[yi], z), (x, ys[yi + 1], z),
                               story=st.name,
                               uid=f"BY{si + 1}-{_alpha(xi)}{yi + 1}")


def _bay_slabs(mdl, p, xs, ys, section, behavior="shell",
               prefix="F") -> List[str]:
    uids = []
    for si, st in enumerate(mdl.stories):
        z = st.elevation
        for yi in range(len(ys) - 1):
            for xi in range(len(xs) - 1):
                x0, x1, y0, y1 = xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]
                r = mdl.add_shell(
                    "slab", behavior, section,
                    [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)],
                    mesh_size=_mesh(p, x1 - x0, y1 - y0), story=st.name,
                    uid=f"{prefix}{si + 1}-{_alpha(xi)}{yi + 1}")
                uids.append(r.uid)
    return uids


# --------------------------------------------------------------------------- #
# templates
# --------------------------------------------------------------------------- #
def _waffle(p):
    mdl, xs, ys = _base(p)
    mdl.add_section(FrameSection.rectangular("COL", "CONC", p["column"],
                                             p["column"]))
    mods = ribbed_modifiers(p["depth"], p["top"], p["rib_width"],
                            p["rib_spacing"], two_way=True)
    mdl.add_shell_section(ShellSection("WAFFLE", "CONC", p["depth"], **mods))
    _columns(mdl, xs, ys, "COL")
    return mdl, _bay_slabs(mdl, p, xs, ys, "WAFFLE")


def _ribbed(p):
    mdl, xs, ys = _base(p)
    mdl.add_section(FrameSection.rectangular("COL", "CONC", p["column"],
                                             p["column"]))
    mdl.add_section(FrameSection.rectangular("BEAM", "CONC", p["beam_b"],
                                             p["beam_h"]))
    mods = ribbed_modifiers(p["depth"], p["top"], p["rib_width"],
                            p["rib_spacing"], two_way=False)
    mdl.add_shell_section(ShellSection("RIBBED", "CONC", p["depth"], **mods))
    _columns(mdl, xs, ys, "COL")
    _grid_beams(mdl, xs, ys, "BEAM")
    return mdl, _bay_slabs(mdl, p, xs, ys, "RIBBED")


def _two_way(p):
    mdl, xs, ys = _base(p)
    mdl.add_section(FrameSection.rectangular("COL", "CONC", p["column"],
                                             p["column"]))
    mdl.add_section(FrameSection.rectangular("BEAM", "CONC", p["beam_b"],
                                             p["beam_h"]))
    mdl.add_shell_section(ShellSection("SLAB", "CONC", p["slab"]))
    _columns(mdl, xs, ys, "COL")
    _grid_beams(mdl, xs, ys, "BEAM")
    return mdl, _bay_slabs(mdl, p, xs, ys, "SLAB")


def drop_panel_lines(lines: List[float], half: float) -> List[float]:
    """Grid lines plus every drop-panel edge (clipped, sorted, unique)."""
    lo, hi = lines[0], lines[-1]
    vals = set(round(v, 9) for v in lines)
    for v in lines:
        for e in (v - half, v + half):
            if lo + 1e-9 < e < hi - 1e-9:
                vals.add(round(e, 9))
    return sorted(vals)


def _flat_drop(p):
    mdl, xs, ys = _base(p)
    mdl.add_section(FrameSection.rectangular("COL", "CONC", p["column"],
                                             p["column"]))
    mdl.add_shell_section(ShellSection("SLAB", "CONC", p["slab"]))
    mdl.add_shell_section(ShellSection("DROP", "CONC",
                                       p["slab"] + p["drop_thickness"]))
    _columns(mdl, xs, ys, "COL")
    dw = p["drop_width"] or min(p["bay_x"], p["bay_y"]) / 3.0
    if dw >= min(p["bay_x"], p["bay_y"]):
        raise ValueError("flat_slab_drop: drop_width must be less than the "
                         "bay width")
    X = drop_panel_lines(xs, dw / 2.0)
    Y = drop_panel_lines(ys, dw / 2.0)
    mesh = p["mesh_size"] or round(max(0.5, min(p["bay_x"], p["bay_y"])
                                       / 4.0), 6)
    uids = []
    for si, st in enumerate(mdl.stories):
        z = st.elevation
        k = 0
        for j in range(len(Y) - 1):
            for i in range(len(X) - 1):
                x0, x1, y0, y1 = X[i], X[i + 1], Y[j], Y[j + 1]
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                drop = (any(abs(cx - x) < dw / 2.0 for x in xs)
                        and any(abs(cy - y) < dw / 2.0 for y in ys))
                k += 1
                r = mdl.add_shell(
                    "slab", "shell", "DROP" if drop else "SLAB",
                    [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)],
                    mesh_size=mesh, story=st.name,
                    uid=f"{'DP' if drop else 'F'}{si + 1}-{k}")
                uids.append(r.uid)
    return mdl, uids


def staggered_lines(n_lines: int, n_stories: int) -> Dict[int, List[int]]:
    """Grid line index -> stories (0-based) carrying a truss: story ``s``
    (s >= 1; the first story is open) when ``(s + i)`` is odd."""
    return {i: [s for s in range(1, n_stories) if (s + i) % 2 == 1]
            for i in range(n_lines)}


def _staggered(p):
    mdl, xs, ys = _base(p, steel=True)
    for nm in ("W14x90", "W18x50", "W10x33", "W8x31"):
        mdl.add_section(FrameSection.from_library(nm, "A992Fy50"))
    mdl.add_shell_section(ShellSection("DECK", "CONC", 0.15))
    Ly = ys[-1]
    yA, yB = 0, len(ys) - 1
    _columns(mdl, xs, ys, "W14x90", only_y={yA, yB})
    _grid_beams(mdl, xs, ys, "W18x50", y_only={yA, yB}, x_lines=False)
    trusses = staggered_lines(len(xs), len(mdl.stories))
    n = int(p["panels"])
    yk = [Ly * k / n for k in range(n + 1)]
    lab = [_alpha(i) for i in range(len(xs))]
    for i, x in enumerate(xs):
        for si in trusses[i]:
            st = mdl.stories[si]
            zt, zb = st.elevation, st.elevation - st.height
            t = f"{si + 1}-{lab[i]}"
            for k in range(n):
                mdl.add_member("beam", "W10x33", (x, yk[k], zt),
                               (x, yk[k + 1], zt), story=st.name,
                               uid=f"TT{t}{k + 1}")
                bot_story = mdl.stories[si - 1].name
                mdl.add_member("beam", "W10x33", (x, yk[k], zb),
                               (x, yk[k + 1], zb), story=bot_story,
                               uid=f"TB{t}{k + 1}")
                if 0 < k:
                    mdl.add_member("column", "W8x31", (x, yk[k], zb),
                                   (x, yk[k], zt), story=st.name,
                                   uid=f"TV{t}{k}")
                if k < n / 2:
                    a, b = (x, yk[k], zb), (x, yk[k + 1], zt)
                else:
                    a, b = (x, yk[k], zt), (x, yk[k + 1], zb)
                mdl.add_member("brace", "W8x31", a, b, story=st.name,
                               uid=f"TD{t}{k + 1}")
        # plain transverse floor beams where no chord sits at a level
        for si, st in enumerate(mdl.stories):
            has = (si in trusses[i]) or ((si + 1) in trusses[i])
            if has:
                continue
            z = st.elevation
            for k in range(n):
                mdl.add_member("beam", "W18x50", (x, yk[k], z),
                               (x, yk[k + 1], z), story=st.name,
                               uid=f"BT{si + 1}-{lab[i]}{k + 1}")
    uids = []
    for si, st in enumerate(mdl.stories):
        z = st.elevation
        for xi in range(len(xs) - 1):
            x0, x1 = xs[xi], xs[xi + 1]
            r = mdl.add_shell("slab", "membrane", "DECK",
                              [(x0, 0.0, z), (x1, 0.0, z), (x1, Ly, z),
                               (x0, Ly, z)], story=st.name,
                              uid=f"F{si + 1}-{lab[xi]}")
            r.distribution, r.one_way_dir = "one_way", 1
            uids.append(r.uid)
    return mdl, uids


_BUILDERS = {"waffle": _waffle, "ribbed": _ribbed,
             "two_way_beams": _two_way, "staggered_truss": _staggered,
             "flat_slab_drop": _flat_drop}


# --------------------------------------------------------------------------- #
# loads, mass source, seismic, RS and combos (existing generators)
# --------------------------------------------------------------------------- #
def setup_loads(mdl: BuildingModel, p: dict, slab_uids: List[str]) -> dict:
    """Patterns / cases / mass source / code seismic / RS / combos.
    Returns a summary ``{patterns, cases, rs_cases, combos, code}``."""
    import warnings

    dead = mdl.pattern("DEAD", "dead")
    dead.self_weight_factor = 1.0
    live = mdl.pattern("LIVE", "live")
    for uid in slab_uids:
        if p["sdl"]:
            dead.area_loads.append(AreaLoad(uid, float(p["sdl"])))
        if p["live"]:
            live.area_loads.append(AreaLoad(uid, float(p["live"])))
    mdl.add_case("DEAD", {"DEAD": 1.0})
    mdl.add_case("LIVE", {"LIVE": 1.0})
    mdl.mass_source = {"DEAD": 1.0}
    if p["live_mass_factor"]:
        mdl.mass_source["LIVE"] = float(p["live_mass_factor"])
    mdl.num_modes = min(3 * len(mdl.stories), 12)
    code = p["code"]
    if code == "none":
        return _summary(mdl, code)
    from . import autolateral as al
    from . import spectra_codes as sc
    if code == "asce7":
        for d in ("X", "Y"):
            al.generate(mdl, "asce7_22", f"EQ{d}", SDS=p["SDS"],
                        SD1=p["SD1"], R=p["R"], Ie=p["Ie"], direction=d)
        sc.make_spectrum_function(mdl, "RSF", "asce7_22_two_period",
                                  {"SDS": p["SDS"], "SD1": p["SD1"]})
        scale = G * p["Ie"] / p["R"]
    elif code == "ec8":
        for d in ("X", "Y"):
            al.generate(mdl, "ec8", f"EQ{d}", ag=p["ag"], q=p["q"],
                        ground_type=p["ground_type"], direction=d)
        sc.make_spectrum_function(mdl, "RSF", "ec8",
                                  {"agR": p["ag"], "ground_type":
                                   p["ground_type"], "kind": "design",
                                   "q": p["q"]})
        scale = G
    else:  # is1893
        for d in ("X", "Y"):
            al.generate(mdl, "is1893", f"EQ{d}", Z=p["Z"], R=p["R"],
                        I=p["Ie"], soil=p["soil"], direction=d)
        sc.make_spectrum_function(mdl, "RSF", "is1893_2016",
                                  {"Z": p["Z"], "soil": p["soil"],
                                   "I": p["Ie"], "R": p["R"]})
        scale = G
    for d in ("X", "Y"):
        mdl.add_case(f"EQ{d}", {f"EQ{d}": 1.0})
        mdl.add_rs_case(f"RS{d}", d, function="RSF", scale=scale)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if code == "asce7":
            from .codes import apply_asce7_combinations
            apply_asce7_combinations(mdl, "LRFD")
        else:
            from .combos_codes import apply_code_combinations
            apply_code_combinations(mdl, "en1990" if code == "ec8"
                                    else "is456")
    return _summary(mdl, code)


def _summary(mdl, code):
    return {"code": code, "patterns": list(mdl.patterns),
            "cases": list(mdl.cases), "rs_cases": list(mdl.rs_cases),
            "combos": list(mdl.combos),
            "mass_source": dict(mdl.mass_source)}


def build_template(template: str, params: Optional[dict] = None
                   ) -> Tuple[BuildingModel, dict]:
    """``(model, summary)``; the model is validated (``validate()``)."""
    p = normalize_params(template, params)
    mdl, slabs = _BUILDERS[template](p)
    summary = setup_loads(mdl, p, slabs)
    summary.update({"template": template, "params": p,
                    "members": len(mdl.members), "shells": len(mdl.shells),
                    "stories": len(mdl.stories)})
    mdl.validate()
    return mdl, summary
