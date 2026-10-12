"""Structural-system template library (D4, core/templates_sys.py).

Every template builds, validates and runs linear static + modal; and

* story / element counts follow from the parameters;
* the DEAD base reaction equals a hand take-off (member lengths x A x
  gamma, shell areas x t x gamma) and the seismic mass equals the hand
  seismic weight (DEAD without columns - the documented story-mass
  convention: columns span stories and are not lumped - + SDL + 0.25 LIVE);
* the first period lies in a plausible band around the ASCE 7 approximate
  period ``Ta = Ct hn^x`` (0.2 Ta .. 3 Ta);
* doubly symmetric plans have every story CM at the geometric centre;

and the closed-form checks:

* 2-D portal (frame_2d, 1 bay x 1 story): lateral stiffness and the
  gravity corner / base moments from slope-deflection with Timoshenko
  members (phi = 12 EI / (G As L^2));
* chevron CBF / EBF (1 story, pinned beams, braces and column bases):
  brace force = F_face / (2 cos(theta)) exactly (rigid diaphragm ->
  equal and opposite brace forces), EBF link shear = N sin(theta);
* base isolation: isolation-layer drift = V / sum(k_iso) exactly and the
  isolator stiffness K = 4 pi^2 M / T^2.

No CSI example result is claimed or reproduced.
"""

import json
import math
import os
import shutil
import subprocess
import warnings

import pytest

from skyframe.core import material_db, steel_db
from skyframe.core.model import BuildingModel, NodalLoad, MemberUDL, \
    StoryForce
from skyframe.core.templates import (TEMPLATES, build_template, catalogue,
                                     normalize_params)
from skyframe.core.templates_sys import (SPECS, SYSTEM_IDS, braced_bays,
                                         deck_section, diagrid_nodes,
                                         equivalent_shape,
                                         isolator_stiffness,
                                         outrigger_levels, podium_layout,
                                         shear_wall_lines, shed_geometry)
from skyframe.engine.opensees_engine import OpenSeesEngine

G = 9.80665
GS = material_db.material("astm_aci", "A992")["unit_weight"] / 1000.0
GC = material_db.material("astm_aci", "4000psi")["unit_weight"] / 1000.0
E_S = material_db.material_from_db("astm_aci", "A992").E

SMALL = {
    "frame_2d": {"stories": 2},
    "steel_smf": {"stories": 2, "bays_x": 3, "bays_y": 3},
    "steel_cbf": {"stories": 2},
    "steel_ebf": {"stories": 2},
    "steel_deck": {"stories": 2},
    "flat_slab_perimeter": {"stories": 2, "bays_x": 2, "bays_y": 2},
    "rc_dual_core": {"stories": 3, "bays_x": 3, "bays_y": 3},
    "rc_shear_wall": {"stories": 3},
    "rc_framed_tube": {"stories": 4, "bays_x": 3, "bays_y": 3},
    "outrigger": {"stories": 6, "bays_x": 3, "bays_y": 3},
    "podium_tower": {"stories": 2, "tower_stories": 3, "bays_x": 5,
                     "bays_y": 3, "tower_bays_x": 1, "tower_bays_y": 1},
    "base_isolated": {"stories": 2},
    "industrial_shed": {},
    "diagrid": {"stories": 5, "bays_x": 4, "bays_y": 4},
}


def _w(name):
    """kN/m of an AISC shape (A992)."""
    return steel_db.shape("aisc", name)["props"]["A"] * GS


def _rc(b, h):
    return b * h * GC


@pytest.fixture(scope="module")
def runs():
    """One small model + results per template (shared by the tests)."""
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for t in SYSTEM_IDS:
            m, s = build_template(t, SMALL[t])
            out[t] = (m, s, OpenSeesEngine(m).run().to_dict())
    return out


# --------------------------------------------------------------------------- #
# hand take-offs: (total DEAD self weight, column self weight, story count,
# frame count, shell count) for the SMALL parameter sets
# --------------------------------------------------------------------------- #
def _hand(t, p):
    """Hand weights (kN): DEAD self weight, its column part; SDL / LIVE
    area totals; expected counts."""
    nx, ny, bx, by = p["bays_x"], p["bays_y"], p["bay_x"], p["bay_y"]
    Lx, Ly = nx * bx, ny * by
    ns = p["stories"]
    h1 = p["first_story_height"] or p["story_height"]
    hs = [h1] + [p["story_height"]] * (ns - 1)
    H = sum(hs)
    sdl, live = p["sdl"], p["live"]
    if t == "frame_2d":
        cols = (nx + 1) * H * _w("W14X90")
        beams = nx * bx * ns * _w("W21X44")
        tw = p["trib_width"]
        return dict(dead=cols + beams, cols=cols, sdl=sdl * tw * Lx * ns,
                    live=live * tw * Lx * (ns - 1), stories=ns,
                    frames=ns * (2 * nx + 1), shells=0)
    if t == "steel_smf":
        per = 2 * (nx + ny)
        inner = (nx - 1) * (ny - 1)
        cols = H * (per * _w("W14X176") + inner * _w("W14X61"))
        mfb = 2 * (Lx + Ly) * _w("W24X84")
        gb = ((ny - 1) * Lx + (nx - 1) * Ly) * _w("W18X35")
        slab = p["slab"] * Lx * Ly * GC
        return dict(dead=cols + ns * (mfb + gb + slab), cols=cols,
                    sdl=sdl * Lx * Ly * ns, live=live * Lx * Ly * (ns - 1),
                    stories=ns,
                    frames=ns * ((nx + 1) * (ny + 1) + (ny + 1) * nx
                                 + (nx + 1) * ny),
                    shells=ns * nx * ny)
    if t in ("steel_cbf", "steel_ebf"):
        cols = (nx + 1) * (ny + 1) * H * _w("W14X90")
        beam = "W18X50" if t == "steel_cbf" else "W21X68"
        beams = ((ny + 1) * Lx + (nx + 1) * Ly) * _w(beam)
        e = p["link_length"] if t == "steel_ebf" else 0.0
        nb = len(braced_bays(nx))
        braces = sum(2 * nb * 2 * math.hypot((bx - e) / 2.0, h) * 2
                     for h in hs) * _w("HSS8X8X1/2")
        # 2 faces x nb bays x 2 braces, both directions (x2)
        slab = p["slab"] * Lx * Ly * GC
        # chevron: braced beams split in 2 (CBF) / 3 (EBF) pieces
        extra = (1 if t == "steel_cbf" else 2) * 4 * nb
        return dict(dead=cols + ns * (beams + slab) + braces, cols=cols,
                    sdl=sdl * Lx * Ly * ns, live=live * Lx * Ly * (ns - 1),
                    stories=ns,
                    frames=ns * ((nx + 1) * (ny + 1) + (ny + 1) * nx
                                 + (nx + 1) * ny + extra + 8 * nb),
                    shells=ns * nx * ny)
    if t == "steel_deck":
        n = p["infill_beams"]
        cols = (nx + 1) * (ny + 1) * H * _w("W14X120")
        gir = ((ny + 1) * Lx + (nx + 1) * Ly) * _w("W24X68")
        inf = n * nx * ny * by * _w("W16X31")          # infill along Y
        d = deck_section(p["deck_slab"], p["deck_rib"])
        ar = 0.5 * (d["rib_width_top"] + d["rib_width_bottom"]) \
            * d["rib_depth"]
        tw = d["slab_depth"] + ar / d["rib_spacing"] \
            + d["deck_unit_weight"] / GC
        deck = tw * Lx * Ly * GC
        return dict(dead=cols + ns * (gir + inf + deck), cols=cols,
                    sdl=sdl * Lx * Ly * ns, live=live * Lx * Ly * (ns - 1),
                    stories=ns,
                    frames=ns * ((nx + 1) * (ny + 1)
                                 + (ny + 1) * nx * (n + 1) + (nx + 1) * ny
                                 + n * nx * ny),
                    shells=ns * nx * ny * (n + 1))
    if t == "flat_slab_perimeter":
        cols = (nx + 1) * (ny + 1) * H * _rc(0.6, 0.6)
        beams = 2 * (Lx + Ly) * _rc(0.4, 0.7)
        slab = 0.25 * Lx * Ly * GC
        return dict(dead=cols + ns * (beams + slab), cols=cols,
                    sdl=sdl * Lx * Ly * ns, live=live * Lx * Ly * (ns - 1),
                    stories=ns,
                    frames=ns * ((nx + 1) * (ny + 1) + 2 * (nx + ny)),
                    shells=ns * nx * ny)
    if t == "rc_dual_core":                 # 3 x 3 bays, 1 x 1 core
        cols = ((nx + 1) * (ny + 1) - 4) * H * _rc(0.7, 0.7)
        beams = ((ny + 1) * Lx + (nx + 1) * Ly - 2 * (bx + by)) \
            * _rc(0.45, 0.7)
        walls = 2 * (bx + by) * 0.40 * H * GC
        A = Lx * Ly - bx * by
        return dict(dead=cols + ns * (beams + 0.2 * A * GC) + walls,
                    cols=cols, sdl=sdl * A * ns, live=live * A * (ns - 1),
                    stories=ns,
                    frames=ns * ((nx + 1) * (ny + 1) - 4
                                 + (ny + 1) * nx + (nx + 1) * ny - 4),
                    shells=ns * (nx * ny - 1 + 4))
    if t == "rc_shear_wall":                # 4 x 3 bays (7.5 x 6)
        pl, o, d = p["pier_length"], p["opening"], p["spandrel_depth"]
        plan = p["planar_length"]
        h = p["story_height"]
        walls = ns * 0.30 * GC * (2 * (2 * pl * h + o * d)
                                  + 2 * plan * h)
        beams = ns * _rc(0.35, 0.6) * ((ny + 1) * Lx - 2 * (2 * pl + o)
                                       + (nx + 1) * Ly - 2 * plan)
        ncol = (nx + 1) * (ny + 1) - 2 - 4      # x = 15 (2 faces), y = 6, 12
        cols = ncol * H * _rc(0.5, 0.5)
        return dict(dead=cols + beams + walls + ns * 0.2 * Lx * Ly * GC,
                    cols=cols, sdl=sdl * Lx * Ly * ns,
                    live=live * Lx * Ly * (ns - 1), stories=ns,
                    frames=None, shells=ns * (2 * 5 + 2 + 8 * 3))
    if t == "rc_framed_tube":               # 3 x 3 bays, n = 3, core
        n = p["tube_subdiv"]
        ntube = 4 * n * nx
        cols = ntube * H * _rc(0.5, 0.9)
        spd = 2 * (Lx + Ly) * _rc(0.5, 1.0)
        beams = (2 * Lx - 2 * bx + 2 * Ly - 2 * by) * _rc(0.45, 0.7)
        walls = 2 * (bx + by) * 0.45 * H * GC
        A = Lx * Ly - bx * by
        return dict(dead=cols + ns * (spd + beams + 0.2 * A * GC) + walls,
                    cols=cols, sdl=sdl * A * ns, live=live * A * (ns - 1),
                    stories=ns, frames=ns * (2 * ntube + 4 * (nx - 1)),
                    shells=ns * (nx * ny - 1 + 4))
    if t == "outrigger":                    # 3 x 3, levels 3 and 6
        lv = outrigger_levels(p["outrigger_levels"], ns)
        assert lv == [3, 6]
        cols = 12 * H * _w("W14X370")
        seg = ((ny + 1) * Lx + (nx + 1) * Ly - 2 * (bx + by))
        chord_st = {2, 3, 5, 6}
        beams = sum(seg * (_w("W14X257") if s in chord_st else _w("W18X40"))
                    for s in range(1, ns + 1))
        diag = 0.0
        for k in lv:
            hk = hs[k - 1]
            diag += (8 + 24) * math.hypot(bx, hk) * _w("W14X176")
        walls = 2 * (bx + by) * 0.6 * H * GC
        A = Lx * Ly - bx * by
        return dict(dead=cols + beams + diag + walls + ns * 0.13 * A * GC,
                    cols=cols, sdl=sdl * A * ns, live=live * A * (ns - 1),
                    stories=ns, frames=None, shells=ns * (8 + 4))
    if t == "podium_tower":                 # 5 x 3 podium, 1 x 1 tower
        tn, th = p["tower_stories"], p["tower_story_height"]
        ppts, pbm = (nx + 1) * (ny + 1), (ny + 1) * Lx + (nx + 1) * Ly
        cols_p = ppts * H * _rc(0.7, 0.7)
        cols_t = 4 * tn * th * _rc(0.7, 0.7)
        beams = (ns * pbm + tn * 4 * bx) * _rc(0.45, 0.7)
        A = ns * Lx * Ly + tn * bx * by
        return dict(dead=cols_p + cols_t + beams + 0.2 * A * GC,
                    cols=cols_p + cols_t, sdl=sdl * A,
                    live=live * (A - bx * by), stories=ns + tn,
                    frames=ns * (ppts + (ny + 1) * nx + (nx + 1) * ny)
                    + tn * 8,
                    shells=ns * nx * ny + tn)
    if t == "base_isolated":
        pts = (nx + 1) * (ny + 1)
        gl = (ny + 1) * Lx + (nx + 1) * Ly
        cols = pts * H * _rc(0.6, 0.6)
        dead = (cols + gl * _rc(0.6, 1.0) + ns * gl * _rc(0.4, 0.65)
                + (ns + 1) * 0.2 * Lx * Ly * GC)
        return dict(dead=dead, cols=cols, sdl=sdl * Lx * Ly * (ns + 1),
                    live=live * Lx * Ly * ns, stories=ns + 1,
                    frames=pts * ns + (ns + 1) * ((ny + 1) * nx
                                                  + (nx + 1) * ny),
                    shells=(ns + 1) * nx * ny)
    if t == "industrial_shed":              # portal, 1 x 24 m, 6 x 6 m
        g = shed_geometry(p)
        nf = ny + 1
        raf = 2 * math.hypot(g["span"] / 2.0, g["ridge"] - g["eave"])
        cols = 2 * nf * g["eave"] * _w("W18X76")
        dead = (cols + nf * raf * _w("W18X50")
                + 3 * ny * by * _w("W8X31")
                + 8 * math.hypot(by, g["eave"]) * _w("HSS4X4X1/4")
                + 0.0007 * GS * raf * Ly)
        return dict(dead=dead, cols=cols, sdl=sdl * raf * Ly, live=0.0,
                    stories=1, frames=2 * nf + 2 * nf + 3 * ny + 8,
                    shells=2 * ny)
    if t == "diagrid":                      # 4 panels x 6 m, 5 stories
        a, L = bx, Lx
        mods = [2 * p["story_height"], 2 * p["story_height"],
                p["story_height"]]
        diag = 4 * 2 * nx * sum(math.hypot(a / 2.0, hm) for hm in mods)
        cs = p["core_size"]
        dead = (diag * _w("W14X176") + 4 * H * _w("W14X176")
                + ns * 4 * L * _w("W24X84") + 4 * cs * 0.5 * H * GC
                + ns * 0.13 * (L * L - cs * cs) * GC)
        A = L * L - cs * cs
        return dict(dead=dead, cols=4 * H * _w("W14X176"), sdl=sdl * A * ns,
                    live=live * A * (ns - 1), stories=ns, frames=None,
                    shells=ns * 8)
    raise AssertionError(t)


# --------------------------------------------------------------------------- #
# catalogue / parameters
# --------------------------------------------------------------------------- #
def test_catalogue_lists_every_system_with_meta():
    cat = catalogue()
    ids = [c["id"] for c in cat]
    assert ids == list(TEMPLATES)
    assert ids[:5] == ["waffle", "ribbed", "two_way_beams",
                       "staggered_truss", "flat_slab_drop"]
    assert ids[5:] == list(SYSTEM_IDS) and len(SYSTEM_IDS) == 14
    for c in cat[5:]:
        assert c["system"] is True and c["description"] and c["etabs"]
        keys = {m["key"] for m in c["meta"]}
        assert keys == set(c["params"])
        assert c["category"] in ("Steel", "Concrete", "Tall building",
                                 "Special")
    json.dumps(cat)                                  # JSON-safe


def test_normalize_rejects_bad_input():
    with pytest.raises(ValueError, match="unknown parameter"):
        normalize_params("steel_smf", {"bogus": 1})
    with pytest.raises(ValueError, match="must be one of"):
        normalize_params("steel_cbf", {"brace_type": "k"})
    with pytest.raises(ValueError, match="centred core"):
        normalize_params("rc_dual_core", {"bays_x": 4})
    with pytest.raises(ValueError, match="odd number"):
        normalize_params("outrigger", {"bays_x": 4})
    with pytest.raises(ValueError, match="level"):
        normalize_params("outrigger", {"outrigger_levels": "1"})
    with pytest.raises(ValueError, match="square"):
        normalize_params("diagrid", {"bays_x": 5, "bays_y": 6})
    with pytest.raises(ValueError, match="do not fit"):
        normalize_params("podium_tower", {"towers": 2, "bays_x": 5})
    with pytest.raises(ValueError, match="not in the aisc database"):
        build_template("steel_smf", {"mf_beam": "W99X1", "stories": 1})
    with pytest.raises(ValueError, match="expected steel"):
        build_template("steel_smf", {"steel_material": "4000psi",
                                     "stories": 1})
    p = normalize_params("steel_smf", {"material_standard": "eurocode"})
    assert (p["steel_material"], p["concrete_material"]) == ("S355",
                                                             "C30/37")


def test_helpers_closed_form():
    assert braced_bays(3) == [1] and braced_bays(4) == [1, 2]
    assert braced_bays(1) == [0]
    assert outrigger_levels("mid,top", 30) == [15, 30]
    assert outrigger_levels("10; 20", 30) == [10, 20]
    assert diagrid_nodes(2, 6.0, 0) == [0.0, 6.0, 12.0]
    assert diagrid_nodes(2, 6.0, 1) == [0.0, 3.0, 9.0, 12.0]
    assert podium_layout({"towers": 2, "bays_x": 9, "bays_y": 5,
                          "tower_bays_x": 3, "tower_bays_y": 3}) == \
        [(0, 1), (6, 1)]
    xs, ys, e = shear_wall_lines(normalize_params("rc_shear_wall", {}))
    assert (e["xa"], e["xo0"], e["xo1"], e["xb"]) == (11.25, 14.25, 15.75,
                                                      18.75)
    assert (e["ya"], e["yb"]) == (6.0, 12.0)
    assert isolator_stiffness(1000.0, 4, 2.0) == pytest.approx(
        math.pi ** 2 * 1000.0 / 4.0, rel=1e-14)
    # another database: the closest W-like I shape by (A, I33)
    he = equivalent_shape("eurocode", "W14X90")
    assert steel_db.shape("eurocode", he)["type"] == "I"


# --------------------------------------------------------------------------- #
# every template: build, validate, counts, weights, mass, period, symmetry
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("t", SYSTEM_IDS)
def test_defaults_build_and_validate(t):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m, s = build_template(t, {})
    m.validate()
    d = m.to_dict()
    assert json.dumps(BuildingModel.from_dict(d).to_dict()) == json.dumps(d)
    assert s["template"] == t and s["stories"] == len(m.stories)
    assert {"DEAD", "SDL", "LROOF", "EQX", "RSX"} <= set(m.cases) | \
        set(m.rs_cases)
    assert m.mass_source["DEAD"] == 1.0 and m.mass_source["SDL"] == 1.0
    assert m.frame_axis_convention == "etabs"
    assert "RSF" in m.spectrum_functions and m.combos
    if t != "frame_2d":
        assert "EQY" in m.cases and "RSY" in m.rs_cases


@pytest.mark.parametrize("t", SYSTEM_IDS)
def test_runs_static_and_modal(t, runs):
    m, s, r = runs[t]
    st = r["case_status"]
    for c in list(m.cases) + list(m.rs_cases) + ["MODAL"]:
        assert st[c] == "finished", (c, st[c])
    assert r["modal"]["periods"] and all(T > 0 for T in
                                        r["modal"]["periods"])


@pytest.mark.parametrize("t", SYSTEM_IDS)
def test_counts_and_dead_weight_vs_hand(t, runs):
    m, s, r = runs[t]
    p = s["params"]
    hd = _hand(t, p)
    assert len(m.stories) == hd["stories"]
    if hd["frames"] is not None:
        assert len(m.members) == hd["frames"]
    assert len(m.shells) == hd["shells"]
    fz = {c: r["cases"][c]["base"]["FZ"] for c in r["cases"]}
    assert fz["DEAD"] == pytest.approx(hd["dead"], rel=1e-6)
    assert fz["SDL"] == pytest.approx(hd["sdl"], rel=1e-6)
    assert fz.get("LIVE", 0.0) == pytest.approx(hd["live"], rel=1e-6)


@pytest.mark.parametrize("t", SYSTEM_IDS)
def test_seismic_mass_vs_hand_estimate(t, runs):
    m, s, r = runs[t]
    hd = _hand(t, s["params"])
    # documented story-mass rule: columns are not lumped to stories
    W = (hd["dead"] - hd["cols"]) + hd["sdl"] + 0.25 * hd["live"]
    M = sum(m.compute_story_masses().values())
    assert M * G == pytest.approx(W, rel=0.02)


# ASCE 7-22 Table 12.8-2 (Ct, x)
_CT = {"steel_smf": (0.0724, 0.8), "steel_deck": (0.0724, 0.8),
       "frame_2d": (0.0724, 0.8), "steel_ebf": (0.0731, 0.75),
       "flat_slab_perimeter": (0.0466, 0.9), "podium_tower": (0.0466, 0.9)}


@pytest.mark.parametrize("t", SYSTEM_IDS)
def test_first_period_plausible(t, runs):
    m, s, r = runs[t]
    T1 = r["modal"]["periods"][0]
    if t == "base_isolated":
        Tt = s["params"]["isolator_period"]
        assert Tt <= T1 <= 1.15 * Tt
        return
    hn = max(st.elevation for st in m.stories)
    ct, x = _CT.get(t, (0.0488, 0.75))
    Ta = ct * hn ** x
    assert 0.2 * Ta <= T1 <= 3.0 * Ta, (T1, Ta)


@pytest.mark.parametrize("t", [t for t in SYSTEM_IDS
                               if t not in ("industrial_shed",
                                            "podium_tower")])
def test_center_of_mass_at_geometric_centre(t, runs):
    m, s, r = runs[t]
    p = s["params"]
    if t == "diagrid":
        cx = cy = p["bays_x"] * p["bay_x"] / 2.0
    else:
        cx = p["bays_x"] * p["bay_x"] / 2.0
        cy = p["bays_y"] * p["bay_y"] / 2.0 if t != "frame_2d" else 0.0
    sp = r["story_props"]
    assert set(sp) == {st.name for st in m.stories}
    for v in sp.values():
        assert v["cm_x"] == pytest.approx(cx, abs=1e-6)
        assert v["cm_y"] == pytest.approx(cy, abs=1e-6)


def test_podium_towers_cm_and_tower_feature(runs):
    m, s, r = runs["podium_tower"]
    p = s["params"]
    assert [t["name"] for t in m.towers] == ["P", "T1"]
    assert s["towers"] == ["P", "T1"]
    sp = r["story_props"]
    for st in m.stories:
        if st.tower == "P":
            c = (p["bays_x"] * p["bay_x"] / 2.0, p["bays_y"] * p["bay_y"]
                 / 2.0)
        else:
            c = (p["bays_x"] * p["bay_x"] / 2.0, p["bays_y"] * p["bay_y"]
                 / 2.0)                       # one centred tower
        assert (sp[st.name]["cm_x"], sp[st.name]["cm_y"]) == pytest.approx(
            c, abs=1e-6)
    tw = r["towers"]
    assert set(tw) == {"P", "T1"}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m2, s2 = build_template("podium_tower", {"towers": 2, "bays_x": 9,
                                                 "tower_bays_x": 3,
                                                 "tower_stories": 2,
                                                 "stories": 1})
    assert [t["name"] for t in m2.towers] == ["P", "T1", "T2"]
    xs1 = {mm.pi[0] for mm in m2.members if mm.tower == "T1"}
    xs2 = {mm.pi[0] for mm in m2.members if mm.tower == "T2"}
    L = 9 * 8.0
    assert sorted(round(L - x, 6) for x in xs1) == sorted(xs2)


def test_walls_carry_pier_and_spandrel_labels(runs):
    m, s, r = runs["rc_shear_wall"]
    assert s["piers"] == ["P1", "P2", "P3", "P4", "PX1", "PX2"]
    assert s["spandrels"] == ["S1", "S2"] and m.spandrel_labels == ["S1",
                                                                     "S2"]
    for lab in ("P1", "PX1"):
        assert lab in r["piers"]["EQX"]
    for t in ("rc_dual_core", "rc_framed_tube", "outrigger", "diagrid"):
        assert runs[t][1]["piers"] == ["P1", "P2", "P3", "P4"]
    # the X walls (coupled piers) take most of the EQX base shear
    base = abs(r["cases"]["EQX"]["base"]["FX"])
    st1 = m.stories[0].name
    pr = r["piers"]["EQX"]
    vx = sum(pr[pl][st1]["V"] for pl in ("P1", "P2", "P3", "P4"))
    assert 0.5 * base < vx < base
    # symmetric pair of coupled walls: equal pier shears, opposite axial
    # forces in the two piers of one wall (coupling action)
    assert pr["P1"][st1]["V"] == pytest.approx(pr["P3"][st1]["V"],
                                               rel=1e-6)
    assert pr["P1"][st1]["P"] == pytest.approx(-pr["P2"][st1]["P"],
                                               rel=1e-6)
    # the Y-direction planar walls take no EQX shear (in-plane only)
    for pl in ("PX1", "PX2"):
        assert abs(pr[pl][st1]["V"]) < 1e-6 * base
    basey = abs(r["cases"]["EQY"]["base"]["FY"])
    vy = sum(abs(r["piers"]["EQY"][pl][st1]["V"]) for pl in ("PX1", "PX2"))
    assert 0.5 * basey < vy < basey


def test_shed_has_no_diaphragm_and_roof_live_only(runs):
    m, s, r = runs["industrial_shed"]
    assert s["diaphragm"] == "none" and "LIVE" not in m.patterns
    assert m.patterns["LROOF"].kind == "roof_live"
    ms = m.compute_story_masses()
    assert list(ms) == ["Roof"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mt, st = build_template("industrial_shed", {"roof": "truss",
                                                    "bays_y": 2})
    r2 = OpenSeesEngine(mt).run().to_dict()
    n = st["params"]["truss_panels"]
    assert sum(1 for mm in mt.members if mm.uid.startswith("TD-1-")) == n
    # vertical equilibrium: LROOF reaction = q x roof area
    assert r2["cases"]["LROOF"]["base"]["FZ"] == pytest.approx(
        0.96 * 24.0 * 12.0, rel=1e-9)


def test_steel_deck_one_way_onto_infill(runs):
    m, s, r = runs["steel_deck"]
    deck = m.shell_sections["DECK"]
    assert deck.slab["type"] == "deck_filled"
    regs = [x for x in m.shells if x.section == "DECK"]
    assert all(x.distribution == "one_way" and x.one_way_dir == 1
               for x in regs)
    # every infill beam is pinned at both ends
    ib = [mm for mm in m.members if mm.uid.startswith("IB")]
    assert ib and all(mm.release_tokens() == {"Mi", "Mj"} for mm in ib)


def test_units_and_other_databases():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m, s = build_template("steel_cbf", {"stories": 1, "units": "kip-ft",
                                            "material_standard": "eurocode",
                                            "steel_db": "eurocode",
                                            "code": "ec8"})
    assert m.display_units == "kip-ft"
    assert set(m.materials) == {"S355", "C30/37"}
    assert all(sec.material == "S355" for sec in m.sections.values())
    assert any(c.startswith("EN-") for c in m.combos)


# --------------------------------------------------------------------------- #
# closed-form checks
# --------------------------------------------------------------------------- #
def _timo(E, G_, I, As, L):
    phi = 12.0 * E * I / (G_ * As * L * L)
    return phi


def _k2d(E, G_, A, I, As, p1, p2):
    """Global 6x6 Timoshenko plane-frame stiffness (u, w, theta) x 2."""
    import numpy as np
    dx, dz = p2[0] - p1[0], p2[1] - p1[1]
    L = math.hypot(dx, dz)
    ph = _timo(E, G_, I, As, L)
    a = E * A / L
    b = 12 * E * I / (L ** 3 * (1 + ph))
    c = 6 * E * I / (L ** 2 * (1 + ph))
    d = (4 + ph) * E * I / (L * (1 + ph))
    e = (2 - ph) * E * I / (L * (1 + ph))
    k = np.array([[a, 0, 0, -a, 0, 0], [0, b, c, 0, -b, c],
                  [0, c, d, 0, -c, e], [-a, 0, 0, a, 0, 0],
                  [0, -b, -c, 0, b, -c], [0, c, e, 0, -c, d]])
    cs, sn = dx / L, dz / L
    t = np.array([[cs, sn, 0], [-sn, cs, 0], [0, 0, 1]])
    T = np.zeros((6, 6))
    T[:3, :3] = T[3:, 3:] = t
    return T.T @ k @ T


def _portal_sway_stiffness(E, G_, col, beam, h, L):
    """Fixed-base portal, beam axially rigid (diaphragm): unit horizontal
    load at the top -> 1 / u.  Free dofs: u, w1, t1, w2, t2."""
    import numpy as np
    # node dof map into the reduced vector [u, w1, t1, w2, t2]
    maps = {"t1": [0, 1, 2], "t2": [0, 3, 4]}
    Kr = np.zeros((5, 5))
    for (na, pa), (nb, pb_), (A, I, As) in (
            (("b1", (0, 0)), ("t1", (0, h)), col),
            (("b2", (L, 0)), ("t2", (L, h)), col),
            (("t1", (0, h)), ("t2", (L, h)), beam)):
        k = _k2d(E, G_, A, I, As, pa, pb_)
        dofs = (maps.get(na, [None] * 3) + maps.get(nb, [None] * 3))
        for i, gi in enumerate(dofs):
            for j, gj in enumerate(dofs):
                if gi is not None and gj is not None:
                    Kr[gi, gj] += k[i, j]
    f = np.zeros(5)
    f[0] = 1.0
    return 1.0 / np.linalg.solve(Kr, f)[0]


def test_portal_frame_closed_form():
    """1-bay 1-story fixed-base portal (frame_2d): lateral stiffness and
    gravity corner/base moments by slope-deflection with Timoshenko
    members (the engine includes shear deformation via As2)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m, _ = build_template("frame_2d", {"bays_x": 1, "stories": 1,
                                           "code": "none"})
    h, L = 3.5, 6.0
    c, b = m.sections["W14X90"], m.sections["W21X44"]
    mat = m.materials["A992"]
    E, Gm = mat.E, mat.E / (2.0 * (1.0 + mat.nu))
    pc = _timo(E, Gm, c.I33, c.As2, h)
    pb = _timo(E, Gm, b.I33, b.As2, L)
    EIc, EIb = E * c.I33, E * b.I33
    push = m.pattern("PUSH", "other")
    push.nodal_loads.append(NodalLoad((0.0, 0.0, h), fx=100.0))
    m.add_case("PUSH", {"PUSH": 1.0})
    udl = m.pattern("UDL", "other")
    udl.member_udls.append(MemberUDL("B1-A", 10.0))
    m.add_case("UDL", {"UDL": 1.0})
    r = OpenSeesEngine(m).run().to_dict()
    kc = EIc / (h * (1.0 + pc))
    # sway, axially rigid columns (slope-deflection, antisymmetric joints)
    D = 1.0
    th = (6.0 * kc * D / h) / ((4.0 + pc) * kc
                                + 6.0 * EIb / (L * (1.0 + pb)))
    V = EIc / (h * h * (1.0 + pc)) * (12.0 * D / h - 6.0 * th)
    K_rigid = 2.0 * V / D
    # the same with axial column shortening (overturning): 5-dof direct
    # stiffness (u shared by the rigid diaphragm, w1 th1 w2 th2)
    K_exact = _portal_sway_stiffness(E, Gm, (c.A, c.I33, c.As2),
                                     (b.A, b.I33, b.As2), h, L)
    ux = r["cases"]["PUSH"]["story"]["Story1"]["ux"]
    assert 100.0 / ux == pytest.approx(K_exact, rel=1e-6)
    assert K_exact == pytest.approx(K_rigid, rel=5e-3)   # axial: < 0.5 %
    # gravity (symmetric, no sway): w L^2 / 12 shared by column + beam
    w = 10.0
    th = (w * L * L / 12.0) / ((4.0 + pc) * kc + 2.0 * EIb / L)
    m_top = (4.0 + pc) * kc * th
    m_base = (2.0 - pc) * kc * th
    mf = r["cases"]["UDL"]["member_forces"]["B1-A"]
    assert abs(mf[5]) == pytest.approx(m_top, rel=1e-6)
    reac = r["cases"]["UDL"]["reactions"]
    assert sorted(abs(v[4]) for v in reac.values()) == pytest.approx(
        [m_base, m_base], rel=1e-6)
    assert sum(v[2] for v in reac.values()) == pytest.approx(w * L,
                                                             rel=1e-9)


@pytest.mark.parametrize("t", ["steel_cbf", "steel_ebf"])
def test_chevron_brace_forces_closed_form(t):
    """1 story, 3 x 3 bays, one braced bay per face; pinned beams /
    braces / column bases -> the braces carry the story shear: |N| =
    (F/2) / (2 cos theta); EBF link shear = |N| sin theta (b - e) / b."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m, s = build_template(t, {"stories": 1, "code": "none"})
    p = s["params"]
    F = 300.0
    pat = m.pattern("PUSH", "other")
    pat.story_forces.append(StoryForce("Story1", fx=F))
    m.add_case("PUSH", {"PUSH": 1.0})
    r = OpenSeesEngine(m).run().to_dict()
    e = p["link_length"] if t == "steel_ebf" else 0.0
    h, b = p["story_height"], p["bay_x"]
    dx = (b - e) / 2.0
    cos, sin = dx / math.hypot(dx, h), h / math.hypot(dx, h)
    N = (F / 2.0) / (2.0 * cos)
    mf = r["cases"]["PUSH"]["member_forces"]
    xb = [k for k in mf if k.startswith("DX")]
    yb = [k for k in mf if k.startswith("DY")]
    assert len(xb) == 4 and len(yb) == 4
    for k in xb:
        assert abs(mf[k][0]) == pytest.approx(N, rel=1e-4)
    for k in yb:
        assert abs(mf[k][0]) == pytest.approx(0.0, abs=1e-6 * N)
    assert r["cases"]["PUSH"]["base"]["FX"] == pytest.approx(-F, rel=1e-9)
    if t == "steel_ebf":
        links = [k for k in mf if k.startswith("LKX")]
        assert len(links) == 2
        # link-end node: brace vertical N sin(theta) = V_link + V_out; the
        # outer beam (pinned at the column, length a = (b - e)/2) and the
        # antisymmetric link share the node moment V_link e/2 = V_out a
        # -> V_link = N sin(theta) (b - e) / b
        a = (b - e) / 2.0
        v_link = N * sin * (b - e) / b
        for k in links:
            assert abs(mf[k][1]) == pytest.approx(v_link, rel=1e-4)
            assert abs(mf[k][5]) == pytest.approx(v_link * e / 2.0,
                                                  rel=1e-4)
            assert abs(mf[k][11]) == pytest.approx(v_link * e / 2.0,
                                                   rel=1e-4)
        for k in [k for k in mf if k.startswith("BBX")]:
            assert abs(mf[k][1]) == pytest.approx(v_link * e / (2 * a),
                                                  rel=1e-4)


def test_base_isolation_closed_form(runs):
    m, s, r = runs["base_isolated"]
    p = s["params"]
    n = (p["bays_x"] + 1) * (p["bays_y"] + 1)
    assert len(m.links) == n and s["links"] == n
    M = sum(m.compute_story_masses().values())
    k = isolator_stiffness(M, n, p["isolator_period"])
    assert s["isolator_keff"] == pytest.approx(k, rel=1e-9)
    assert all(lk.stiffness[0] == lk.stiffness[1] == pytest.approx(k)
               for lk in m.links)
    # isolation-layer drift under EQX = V / sum(k) exactly
    V = abs(r["cases"]["EQX"]["base"]["FX"])
    ux = r["cases"]["EQX"]["story"]["ISO"]["ux"]
    assert abs(ux) == pytest.approx(V / (n * k), rel=1e-6)
    # rigid-superstructure period 2 pi sqrt(M/K) = target; the flexible
    # superstructure lengthens it slightly
    T1 = r["modal"]["periods"][0]
    assert p["isolator_period"] < T1 < 1.1 * p["isolator_period"]


def test_api_catalogue_and_build(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    client = create_app().test_client()
    cat = client.get("/api/model/templates").get_json()
    assert [c["id"] for c in cat][5:] == list(SYSTEM_IDS)
    r = client.post("/api/model/template", json={
        "template": "steel_cbf", "params": {"stories": 1,
                                            "brace_type": "x"},
        "dry_run": True})
    body = r.get_json()
    assert r.status_code == 200, body
    assert body["summary"]["template"] == "steel_cbf"
    assert body["model"]["frame_axis_convention"] == "etabs"
    bad = client.post("/api/model/template", json={
        "template": "steel_cbf", "params": {"brace_type": "k"}})
    assert bad.status_code == 400


def test_diagrid_square_follows_x_and_meta_flags():
    p = normalize_params("diagrid", {"bays_x": 4, "bay_x": 7.0})
    assert (p["bays_y"], p["bay_y"]) == (4, 7.0)
    ent = {c["id"]: c for c in catalogue()}
    meta = {m["key"]: m for m in ent["industrial_shed"]["meta"]}
    assert meta["stories"].get("hidden") and meta["bay_x"]["label"] == "Span"
    assert meta["sdl"]["unit"] == "pressure"
    assert meta["pitch"]["unit"] == "none"
    smf = {m["key"]: m for m in ent["steel_smf"]["meta"]}
    assert smf["slab"]["unit"] == "dim" and smf["bay_x"]["unit"] == "length"
    assert smf["mf_beam"]["aisc_default"] == "W24X84"
    iso = {m["key"]: m for m in ent["base_isolated"]["meta"]}
    assert iso["isolator_kv"]["unit"] == "stiffness"


def test_mock_data_file_is_up_to_date():
    """js/mock_tpl_library_data.js is generated from the backend."""
    import os
    import sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(here, "tools"))
    try:
        from gen_mock_tpl_library import render
    finally:
        sys.path.pop(0)
    path = os.path.join(here, "skyframe", "api", "web", "js",
                        "mock_tpl_library_data.js")
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == render()


_NODE = (shutil.which("node")
         or ("/opt/node22/bin/node" if os.path.exists("/opt/node22/bin/node")
             else None))


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_js_geometry_port_matches_backend(tmp_path):
    """js/mock_tpl_library.js (geometry js/tpl_lib_geom.js) builds the same
    uids / end points / releases / angles / sections / stories / shell
    corners and pier-spandrel labels as the backend (?mock=1 parity)."""
    cases = {"steel_cbf": {"stories": 2, "brace_type": "x"},
             "steel_deck": {"stories": 2, "infill_dir": "X"},
             "rc_framed_tube": {"stories": 2, "bays_x": 4, "bays_y": 4},
             "podium_tower": {"stories": 1, "tower_stories": 2,
                              "bays_x": 9, "towers": 2,
                              "tower_bays_x": 2},
             "industrial_shed": {"roof": "truss", "bays_y": 2}}
    for t in SYSTEM_IDS:
        cases.setdefault(t, SMALL[t])
    ref = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for t, prm in cases.items():
            m, s = build_template(t, dict(prm, code="none"))
            ref[t] = {
                "members": {mm.uid: [[*mm.pi, *mm.pj], mm.releases,
                                     mm.angle, mm.section, mm.story]
                            for mm in m.members},
                "shells": {r.uid: [[v for c in r.corners for v in c],
                                   r.pier, r.spandrel, r.section,
                                   r.behavior] for r in m.shells},
                "links": sorted(lk.uid for lk in m.links),
                "stories": [[st.name, round(st.elevation, 6)]
                            for st in m.stories]}
    web = os.path.join(os.path.dirname(__file__), "..", "skyframe", "api",
                       "web", "js", "mock_tpl_library.js")
    script = tmp_path / "run.mjs"
    script.write_text(
        "import { mockTplSysBuild as B } from "
        + json.dumps("file://" + os.path.abspath(web)) + ";\n"
        "const C = " + json.dumps(cases) + ";\n"
        "const r6 = v => Math.round(v * 1e6) / 1e6;\n"
        "const out = {};\n"
        "for (const [t, p] of Object.entries(C)) {\n"
        "  const m = B(t, {...p, code: 'none'}).model;\n"
        "  out[t] = {\n"
        "   members: Object.fromEntries(m.members.map(x => [x.uid,"
        " [[...x.pi, ...x.pj], x.releases, x.angle, x.section, x.story]])),\n"
        "   shells: Object.fromEntries(m.shells.map(x => [x.uid,"
        " [x.corners.flat(), x.pier || '', x.spandrel || '', x.section,"
        " x.behavior]])),\n"
        "   links: m.links.map(x => x.uid).sort(),\n"
        "   stories: m.stories.map(s => [s.name, r6(s.elevation)])};\n"
        "}\nconsole.log(JSON.stringify(out));\n")
    res = subprocess.run([_NODE, str(script)], capture_output=True,
                         text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    js = json.loads(res.stdout)
    for t in cases:
        a, b = ref[t], js[t]
        assert a["links"] == b["links"] and a["stories"] == b["stories"], t
        for k in ("members", "shells"):
            assert set(a[k]) == set(b[k]), (t, k)
            for uid, ea in a[k].items():
                eb = b[k][uid]
                assert ea[0] == pytest.approx(eb[0], abs=1e-6), (t, uid)
                assert ea[1:] == eb[1:], (t, uid)
