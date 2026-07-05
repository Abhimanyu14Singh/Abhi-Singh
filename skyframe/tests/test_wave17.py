"""Wave 17 backend tests (v0.16):

1. Beam deflection recovery + serviceability checks: exact closed-form
   transverse deflection stations per member (Hermite/elastic-line recovery
   from end displacements + the statics-exact moment field), pinned against
   the classic closed forms —
     * SS beam UDL:            dy_mid = 5 w L^4 / 384 EI       (1e-6 rel)
     * fixed-fixed UDL:        dy_mid = w L^4 / 384 EI         (1e-6)
     * SS central point load:  dy_mid = P L^3 / 48 EI          (1e-6)
     * cantilever tip load:    dy_tip = P L^3 / 3 EI and the WHOLE station
       curve equals the analytic cubic  v(x) = -P x^2 (3L - x) / 6 EI
   plus a frame beam whose ends rotate/translate, matched against an
   INDEPENDENT numpy fine-mesh Euler FE with the same end conditions and
   load (1e-4); additive-combo superposition; the deflection_checks
   ratio-string / ok-flag math hand-verified; model.deflection_limit
   round-trip.
2. ASCE 7-16 §4.7 live-load reduction: R = 0.25 + 4.57/sqrt(KLL*At)
   hand-computed (KLL = 4, At = 36 m^2, 1 story -> 0.63083...), clamp floor
   0.5 (one floor) / 0.4 (multi-story) and the R = 1 cap; interior vs
   corner tributary areas of quick_building hand-derived (half-bay rule);
   the design-stage demand reduction is EXACT (linear attribution:
   q_adj = q + (R-1)*f*q_LIVE, 1e-9) and a pure-axial concrete ratio
   scales exactly as hand-computed.
3. Biaxial concrete column check (Bresler): square symmetric column with
   equal demands both axes -> 1/phiPn_b = 2/phiPn_x - 1/phiP0 verified to
   1e-9 against an independent polyline intersection; load-contour
   fallback at low axial hand-verified; a pure-uniaxial demand keeps the
   v0.6 path bit-identical (1e-12 regression).
4. API: GET /api/live-reduction; design endpoints accept
   live_reduction/live_case; /api/analyze carries member_deflections +
   deflection_checks; deflection_limit round-trips.

Every expected number is hand-derived (beam closed forms, Macaulay
integration, ASCE 7 Eq. 4.7-1, Bresler's identities), independent of the
modules under test.  Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import numpy as np
import pytest

from skyframe.core.builder import quick_building
from skyframe.core.codes import live_load_reduction, reduce_live_demands
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 MemberLoad, MemberUDL, NodalLoad,
                                 PointSupport)
from skyframe.design.concrete import (PHI_COMPRESSION, RebarLayout,
                                      check_concrete_members)
from skyframe.engine.opensees_engine import OpenSeesEngine

E_CONC = 25e6           # kPa
B_SEC, H_SEC = 0.3, 0.6
I_SEC = B_SEC * H_SEC ** 3 / 12.0
L_BEAM = 6.0


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _beam_model(name, restr_i, restr_j, L=L_BEAM):
    """One horizontal beam at z = 3 m with explicit end supports."""
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", B_SEC, H_SEC))
    mdl.set_stories([3.0])
    mdl.add_member("beam", "BM", (0, 0, 3.0), (L, 0, 3.0),
                   story="Story1", uid="B1")
    mdl.supports.append(PointSupport((0, 0, 3.0), restr_i))
    mdl.supports.append(PointSupport((L, 0, 3.0), restr_j))
    return mdl


PIN = (1, 1, 1, 1, 0, 0)      # translations + torsion held, bending free
FIX = (1, 1, 1, 1, 1, 1)


# --------------------------------------------------------------------------- #
# 1. deflection recovery — closed forms
# --------------------------------------------------------------------------- #
def test_ss_beam_udl_midspan_deflection():
    w = 20.0
    mdl = _beam_model("ss-udl", PIN, PIN)
    mdl.pattern("D", "dead").member_udls.append(MemberUDL("B1", w))
    mdl.add_case("D", {"D": 1.0})
    res = OpenSeesEngine(mdl).run_static("D")
    md = res.member_deflections["B1"]
    assert md["x"][5] == pytest.approx(L_BEAM / 2.0, rel=1e-12)
    d_hand = 5.0 * w * L_BEAM ** 4 / (384.0 * E_CONC * I_SEC)
    assert -md["dy"][5] == pytest.approx(d_hand, rel=1e-6)
    # end stations sit on the (zero-displacement) supports
    assert abs(md["dy"][0]) < 1e-12 and abs(md["dy"][-1]) < 1e-12
    # no out-of-plane load: dz identically zero
    assert max(abs(v) for v in md["dz"]) < 1e-12


def test_fixed_fixed_udl_midspan_deflection():
    w = 20.0
    mdl = _beam_model("ff-udl", FIX, FIX)
    mdl.pattern("D", "dead").member_udls.append(MemberUDL("B1", w))
    mdl.add_case("D", {"D": 1.0})
    res = OpenSeesEngine(mdl).run_static("D")
    md = res.member_deflections["B1"]
    d_hand = w * L_BEAM ** 4 / (384.0 * E_CONC * I_SEC)
    assert -md["dy"][5] == pytest.approx(d_hand, rel=1e-6)
    # hand value of the fixed-fixed elastic line at station 2 (x = L/5):
    # v = w x^2 (L - x)^2 / 24 EI
    x = md["x"][2]
    assert x == pytest.approx(L_BEAM / 5.0, rel=1e-12)
    v_hand = w * x * x * (L_BEAM - x) ** 2 / (24.0 * E_CONC * I_SEC)
    assert -md["dy"][2] == pytest.approx(v_hand, rel=1e-6)
    assert md["dy"][2] == pytest.approx(md["dy"][8], rel=1e-9)


def test_ss_beam_central_point_load_deflection():
    P = 50.0
    mdl = _beam_model("ss-pt", PIN, PIN)
    mdl.pattern("P", "other").member_loads.append(
        MemberLoad("B1", kind="point", w=P, a=0.5))
    mdl.add_case("P", {"P": 1.0})
    res = OpenSeesEngine(mdl).run_static("P")
    md = res.member_deflections["B1"]
    d_hand = P * L_BEAM ** 3 / (48.0 * E_CONC * I_SEC)
    assert -md["dy"][5] == pytest.approx(d_hand, rel=1e-6)


def test_cantilever_tip_load_curve_matches_analytic_cubic():
    P = 40.0
    mdl = BuildingModel(name="cant")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", B_SEC, H_SEC))
    mdl.set_stories([3.0])
    mdl.add_member("beam", "BM", (0, 0, 3.0), (L_BEAM, 0, 3.0),
                   story="Story1", uid="B1")
    mdl.supports.append(PointSupport((0, 0, 3.0), FIX))
    mdl.pattern("P", "other").nodal_loads.append(
        NodalLoad((L_BEAM, 0, 3.0), fz=-P))
    mdl.add_case("P", {"P": 1.0})
    res = OpenSeesEngine(mdl).run_static("P")
    md = res.member_deflections["B1"]
    EI = E_CONC * I_SEC
    tip_hand = P * L_BEAM ** 3 / (3.0 * EI)
    assert -md["dy"][-1] == pytest.approx(tip_hand, rel=1e-6)
    # STATION CURVE pointwise: v(x) = -P x^2 (3L - x) / 6EI  (analytic cubic)
    for x, dy in zip(md["x"], md["dy"]):
        v_hand = -P * x * x * (3.0 * L_BEAM - x) / (6.0 * EI)
        assert dy == pytest.approx(v_hand, rel=1e-6, abs=1e-12 * tip_hand)


def _fine_mesh_fe(L, EI, w_y, v_i, th_i, v_j, th_j, n=100):
    """Independent numpy Euler-beam FE: n elements, (v, theta) DOFs,
    prescribed end displacements AND rotations, consistent UDL load.
    Returns the nodal deflections (n+1 values)."""
    le = L / n
    k = EI / le ** 3 * np.array([
        [12.0, 6 * le, -12.0, 6 * le],
        [6 * le, 4 * le * le, -6 * le, 2 * le * le],
        [-12.0, -6 * le, 12.0, -6 * le],
        [6 * le, 2 * le * le, -6 * le, 4 * le * le]])
    ndof = 2 * (n + 1)
    K = np.zeros((ndof, ndof))
    F = np.zeros(ndof)
    fe = w_y * np.array([le / 2.0, le * le / 12.0, le / 2.0,
                         -le * le / 12.0])       # consistent UDL vector
    for e in range(n):
        dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        K[np.ix_(dofs, dofs)] += k
        F[dofs] += fe
    presc = {0: v_i, 1: th_i, 2 * n: v_j, 2 * n + 1: th_j}
    free = [d for d in range(ndof) if d not in presc]
    u = np.zeros(ndof)
    for d, val in presc.items():
        u[d] = val
    rhs = F[free] - K[np.ix_(free, list(presc))] @ np.array(
        [presc[d] for d in presc])
    u[free] = np.linalg.solve(K[np.ix_(free, free)], rhs)
    return u[0::2]


def test_frame_beam_matches_independent_fine_mesh_fe():
    """Portal-frame beam (ends rotate AND settle): the engine's relative-to-
    chord max deflection equals an independent numpy fine-mesh Euler FE of
    the same member under the same end conditions + load (1e-4)."""
    w, H = 30.0, 3.0
    mdl = BuildingModel(name="portal")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", B_SEC, H_SEC))
    mdl.set_stories([H])
    for x in (0.0, L_BEAM):
        mdl.add_member("column", "COL", (x, 0, 0), (x, 0, H),
                       story="Story1", uid=f"C{int(x > 0) + 1}")
        mdl.supports.append(PointSupport((x, 0, 0), FIX))
    mdl.add_member("beam", "BM", (0, 0, H), (L_BEAM, 0, H),
                   story="Story1", uid="B1")
    mdl.pattern("D", "dead").member_udls.append(MemberUDL("B1", w))
    mdl.add_case("D", {"D": 1.0})
    eng = OpenSeesEngine(mdl)
    res = eng.run_static("D")
    md = res.member_deflections["B1"]

    # end conditions in the beam LOCAL axes (beam along +X: local y = +Z,
    # local z = -Y; theta_local_z = -ry)
    coords = eng._asm.node_coords
    tag_i = next(t for t, c in coords.items()
                 if abs(c[0]) < 1e-9 and abs(c[2] - H) < 1e-9)
    tag_j = next(t for t, c in coords.items()
                 if abs(c[0] - L_BEAM) < 1e-9 and abs(c[2] - H) < 1e-9)
    di, dj = res.node_disp[tag_i], res.node_disp[tag_j]
    v_i, v_j = di[2], dj[2]                       # local y = global Z
    th_i, th_j = -di[4], -dj[4]                   # local z rot = -ry
    # sanity: the joints really rotate and settle
    assert abs(th_i) > 1e-6 and abs(v_i) > 1e-8

    v_fe = _fine_mesh_fe(L_BEAM, E_CONC * I_SEC, -w, v_i, th_i, v_j, th_j,
                         n=100)
    xs_fe = np.linspace(0.0, L_BEAM, 101)
    chord_fe = v_fe[0] + (v_fe[-1] - v_fe[0]) * xs_fe / L_BEAM
    max_fe = float(np.max(np.abs(v_fe - chord_fe)))

    dy = md["dy"]
    chord = [dy[0] + (dy[-1] - dy[0]) * x / L_BEAM for x in md["x"]]
    max_eng = max(abs(a - b) for a, b in zip(dy, chord))
    assert max_eng == pytest.approx(max_fe, rel=1e-4)
    # stations coincide with every 10th FE node: compare pointwise too
    for k in range(11):
        assert dy[k] == pytest.approx(float(v_fe[10 * k]), rel=1e-4,
                                      abs=1e-9 * max_fe)
    # the serviceability entry reports the same relative-to-chord max
    checks = {e["uid"]: e for e in eng.run().deflection_checks["D"]}
    assert checks["B1"]["max_abs_dy"] == pytest.approx(max_eng, rel=1e-9)


def test_combo_deflections_superpose_exactly():
    mdl = _beam_model("combo", PIN, PIN)
    mdl.pattern("D", "dead").member_udls.append(MemberUDL("B1", 20.0))
    mdl.pattern("L", "live").member_udls.append(MemberUDL("B1", 10.0))
    mdl.add_case("D", {"D": 1.0})
    mdl.add_case("L", {"L": 1.0})
    mdl.add_combo("U", {"D": 1.2, "L": 1.6})
    res = OpenSeesEngine(mdl).run()
    dd = res.cases["D"].member_deflections["B1"]["dy"]
    dl = res.cases["L"].member_deflections["B1"]["dy"]
    du = res.combos["U"].member_deflections["B1"]["dy"]
    for a, b, c in zip(dd, dl, du):
        assert c == pytest.approx(1.2 * a + 1.6 * b, abs=1e-15)
    # combos get deflection_checks too (linear: 1.2*20 + 1.6*10 = 40 kN/m)
    entry = {e["uid"]: e for e in res.deflection_checks["U"]}["B1"]
    d_hand = 5.0 * 40.0 * L_BEAM ** 4 / (384.0 * E_CONC * I_SEC)
    assert entry["max_abs_dy"] == pytest.approx(d_hand, rel=1e-6)


def test_deflection_checks_ratio_math_and_limit():
    """Hand-verified L/n string + ok flag: SS UDL gives max_abs_dy =
    5wL^4/384EI = 2.5 mm on L = 6 m -> "L/2400"; ok at L/360, NG at
    L/3000 (allowed 2 mm < 2.5 mm)."""
    w = 20.0
    d_hand = 5.0 * w * L_BEAM ** 4 / (384.0 * E_CONC * I_SEC)   # 0.0025 m
    assert d_hand == pytest.approx(0.0025, rel=1e-9)

    def run_with(limit):
        mdl = _beam_model("chk", PIN, PIN)
        mdl.deflection_limit = limit
        mdl.pattern("D", "dead").member_udls.append(MemberUDL("B1", w))
        mdl.add_case("D", {"D": 1.0})
        res = OpenSeesEngine(mdl).run()
        return {e["uid"]: e for e in res.deflection_checks["D"]}["B1"]

    e360 = run_with(360.0)
    assert e360["L"] == pytest.approx(L_BEAM)
    assert e360["story"] == "Story1"
    assert e360["max_abs_dy"] == pytest.approx(d_hand, rel=1e-6)
    assert e360["ratio_str"] == "L/2400"        # 6 / 0.0025 = 2400
    assert e360["limit"] == "L/360"
    assert e360["ok"] is True                   # 2.5 mm <= 6/360 = 16.7 mm
    e3000 = run_with(3000.0)
    assert e3000["limit"] == "L/3000"
    assert e3000["ok"] is False                 # 2.5 mm > 6/3000 = 2 mm


def test_deflection_limit_roundtrip_and_validation():
    mdl = _beam_model("rt", PIN, PIN)
    mdl.deflection_limit = 480.0
    back = BuildingModel.from_dict(mdl.to_dict())
    assert back.deflection_limit == 480.0
    # pre-v0.16 file (no key) keeps the default
    d = mdl.to_dict()
    d.pop("deflection_limit")
    assert BuildingModel.from_dict(d).deflection_limit == 360.0
    d["deflection_limit"] = -5.0
    with pytest.raises(ValueError, match="deflection_limit"):
        BuildingModel.from_dict(d)


def test_axial_only_member_skipped_from_deflections():
    """A tension-only brace (Truss) has no bending: no deflection entry,
    while the ordinary members still report."""
    mdl = _beam_model("truss", PIN, PIN)
    mdl.add_member("brace", "BM", (0, 0, 3.0), (L_BEAM, 3.0, 3.0),
                   story="Story1", uid="T1", axial_limit="tension")
    mdl.supports.append(PointSupport((L_BEAM, 3.0, 3.0), FIX))
    mdl.pattern("D", "dead").member_udls.append(MemberUDL("B1", 5.0))
    mdl.add_case("D", {"D": 1.0})
    res = OpenSeesEngine(mdl).run_static("D")
    assert "B1" in res.member_deflections
    assert "T1" not in res.member_deflections


# --------------------------------------------------------------------------- #
# 2. ASCE 7-16 §4.7 live-load reduction
# --------------------------------------------------------------------------- #
def test_live_reduction_hand_values_quick_building():
    """quick_building 3x2 bays @ 6 m, 4 stories — half-bay tributaries:

    * interior top-story column (C4-B2): At = 6*6 = 36 m^2, 1 story ->
      R = 0.25 + 4.57/sqrt(4*36) = 0.25 + 4.57/12 = 0.630833...
    * corner top-story column (C4-A1): At = 3*3 = 9 -> KLL*At = 36 ->
      R = 0.25 + 4.57/6 = 1.0117 -> capped at 1.0
    * interior story-1 column (C1-B2): 4 supported floors, At = 144 ->
      R = 0.25 + 4.57/24 = 0.440417 (above the 0.4 multi-story floor)
    * edge top-story column (C4-B1): At = 6*3 = 18 ->
      R = 0.25 + 4.57/sqrt(72) = 0.788617...
    """
    mdl = quick_building(bays_x=3, bay_width_x=6.0, bays_y=2,
                         bay_width_y=6.0, stories=4)
    red = live_load_reduction(mdl)
    interior = red["C4-B2"]
    assert interior["KLL"] == 4.0
    assert interior["At"] == pytest.approx(36.0, rel=1e-12)
    assert interior["R"] == pytest.approx(0.25 + 4.57 / 12.0, rel=1e-12)
    assert red["C4-A1"]["At"] == pytest.approx(9.0, rel=1e-12)
    assert red["C4-A1"]["R"] == 1.0                       # cap
    assert red["C1-B2"]["At"] == pytest.approx(144.0, rel=1e-12)
    assert red["C1-B2"]["R"] == pytest.approx(0.25 + 4.57 / 24.0, rel=1e-12)
    assert red["C4-B1"]["At"] == pytest.approx(18.0, rel=1e-12)
    assert red["C4-B1"]["R"] == pytest.approx(
        0.25 + 4.57 / math.sqrt(4.0 * 18.0), rel=1e-12)
    # every column reported, beams never
    assert set(red) == {m.uid for m in mdl.members if m.kind == "column"}


def _big_bay_model(stories):
    """One interior column with four 40 m beams at every floor: huge
    tributary (dx = dy = 40 -> At_floor = 1600 m^2) to exercise the clamp
    floors."""
    mdl = BuildingModel(name="big")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.5, 0.5))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.6))
    mdl.set_stories([3.0] * stories)
    for si, s in enumerate(mdl.stories):
        z0, z1 = s.elevation - s.height, s.elevation
        mdl.add_member("column", "COL", (0, 0, z0), (0, 0, z1),
                       story=s.name, uid=f"C{si + 1}")
        for k, (dx, dy) in enumerate([(40, 0), (-40, 0), (0, 40), (0, -40)]):
            mdl.add_member("beam", "BM", (0, 0, z1), (dx, dy, z1),
                           story=s.name, uid=f"B{si + 1}-{k}")
    return mdl


def test_live_reduction_clamp_floors():
    # one floor: R floored at 0.5 (raw 0.25 + 4.57/80 = 0.307)
    red1 = live_load_reduction(_big_bay_model(1))
    assert red1["C1"]["At"] == pytest.approx(1600.0, rel=1e-12)
    assert 0.25 + 4.57 / math.sqrt(4 * 1600.0) < 0.5
    assert red1["C1"]["R"] == 0.5
    # two floors supported: floor drops to 0.4
    red2 = live_load_reduction(_big_bay_model(2))
    assert red2["C1"]["n_stories"] == 2.0
    assert red2["C1"]["R"] == 0.4
    assert red2["C2"]["R"] == 0.5           # top column: one floor -> 0.5


def test_live_reduction_fallback_warns_without_beams():
    mdl = BuildingModel(name="lonely")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.set_stories([3.0])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3.0),
                   story="Story1", uid="C1")
    with pytest.warns(UserWarning, match="falls back"):
        red = live_load_reduction(mdl)
    assert "C1" in red      # fallback At (plan area 0 -> R = 1, no crash)
    assert red["C1"]["R"] == 1.0


def test_reduce_live_demands_exact_linear_attribution():
    """q_adj = q_total + (R - 1) * f * q_LIVE, verified entry by entry to
    1e-9 on a synthetic two-case + combo results dict."""
    mdl = quick_building(bays_x=1, bay_width_x=6.0, bays_y=1,
                         bay_width_y=6.0, stories=1)
    uid = "C1-A1"
    mf_d = [100.0, 1.0, 2.0, 0.5, 3.0, 4.0, -100.0, -1.0, -2.0, -0.5, 5.0, 6.0]
    mf_l = [40.0, 0.4, 0.8, 0.2, 1.2, 1.6, -40.0, -0.4, -0.8, -0.2, 2.0, 2.4]
    st_d = {"x": [0.0, 3.2], "N": [-100.0, -100.0], "V2": [1.0, 1.0],
            "V3": [2.0, 2.0], "T": [0.5, 0.5], "M2": [3.0, 5.0],
            "M3": [4.0, 6.0]}
    st_l = {"x": [0.0, 3.2], "N": [-40.0, -40.0], "V2": [0.4, 0.4],
            "V3": [0.8, 0.8], "T": [0.2, 0.2], "M2": [1.2, 2.0],
            "M3": [1.6, 2.4]}
    combo = {  # 1.2D + 1.6L, folded by hand
        "member_forces": {uid: [1.2 * a + 1.6 * b
                                for a, b in zip(mf_d, mf_l)]},
        "member_stations": {uid: {k: ([1.2 * a + 1.6 * b for a, b in
                                       zip(st_d[k], st_l[k])]
                                      if k != "x" else list(st_d["x"]))
                                  for k in st_d}}}
    d = {"cases": {"DEAD": {"member_forces": {uid: mf_d},
                            "member_stations": {uid: st_d}},
                   "LIVE": {"member_forces": {uid: mf_l},
                            "member_stations": {uid: st_l}}},
         "combos": {"1.2D + 1.6L": combo}}
    R = 0.61
    red = {uid: {"KLL": 4.0, "At": 30.0, "R": R, "n_stories": 1.0}}
    adj = reduce_live_demands(d, mdl, live_case="LIVE", reduction=red)
    # DEAD untouched; LIVE scaled by R; combo live share scaled by 1.6*(R-1)
    assert adj["cases"]["DEAD"]["member_forces"][uid] == mf_d
    for got, lv in zip(adj["cases"]["LIVE"]["member_forces"][uid], mf_l):
        assert got == pytest.approx(R * lv, abs=1e-12)
    got_combo = adj["combos"]["1.2D + 1.6L"]["member_forces"][uid]
    for g, tot, lv in zip(got_combo, combo["member_forces"][uid], mf_l):
        assert g == pytest.approx(tot + (R - 1.0) * 1.6 * lv, abs=1e-9)
    st_adj = adj["combos"]["1.2D + 1.6L"]["member_stations"][uid]
    for key in ("N", "V2", "V3", "T", "M2", "M3"):
        for g, tot, lv in zip(st_adj[key],
                              combo["member_stations"][uid][key],
                              st_l[key]):
            assert g == pytest.approx(tot + (R - 1.0) * 1.6 * lv, abs=1e-9)
    # the original dict is never mutated
    assert d["combos"]["1.2D + 1.6L"]["member_forces"][uid] == \
        combo["member_forces"][uid]
    # unknown live case is an error
    with pytest.raises(ValueError, match="live case"):
        reduce_live_demands(d, mdl, live_case="NOPE", reduction=red)


def test_design_ratio_scales_exactly_with_reduction():
    """Pure-axial concrete column: radial ratio = Pu/phiPn_max is LINEAR in
    Pu, so the reduced-path ratio equals the hand value to 1e-9."""
    mdl = BuildingModel(name="ax")
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.set_stories([3.0])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3.0),
                   story="Story1", uid="C1")
    mdl.pattern("DEAD", "dead")
    mdl.pattern("LIVE", "live")
    mdl.add_case("DEAD", {"DEAD": 1.0})
    mdl.add_case("LIVE", {"LIVE": 1.0})
    mdl.add_combo("U", {"DEAD": 1.2, "LIVE": 1.6})
    P_d, P_l, R, f = 900.0, 500.0, 0.61, 1.6
    zero11 = [0.0] * 11
    d = {"cases": {"DEAD": {"member_forces": {"C1": [P_d] + zero11}},
                   "LIVE": {"member_forces": {"C1": [P_l] + zero11}}},
         "combos": {"U": {"member_forces":
                          {"C1": [1.2 * P_d + 1.6 * P_l] + zero11}}}}
    lay = {"C1": RebarLayout(n_top=3, n_bot=3, bar_dia=0.020)}
    red = {"C1": {"KLL": 4.0, "At": 30.0, "R": R, "n_stories": 1.0}}
    adj = reduce_live_demands(d, mdl, live_case="LIVE", reduction=red)
    chk0 = check_concrete_members(mdl, d, "U", lay)[0]
    chk1 = check_concrete_members(mdl, adj, "U", lay)[0]
    phiPn_max = chk0.pm_points[0][1]
    Pu_red = 1.2 * P_d + f * R * P_l
    assert chk1.Pu == pytest.approx(Pu_red, abs=1e-9)
    assert chk1.ratio == pytest.approx(Pu_red / phiPn_max, rel=1e-9)
    # exact hand scaling of the unreduced ratio
    assert chk1.ratio == pytest.approx(
        chk0.ratio * Pu_red / (1.2 * P_d + f * P_l), rel=1e-9)


# --------------------------------------------------------------------------- #
# 3. biaxial concrete column check (Bresler)
# --------------------------------------------------------------------------- #
FC, FY = 30_000.0, 420_000.0
SQ = 0.4                                   # square column side
LAY = dict(n_top=3, n_bot=3, bar_dia=0.020)


def _col_model():
    mdl = BuildingModel(name="bx")
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", SQ, SQ))
    mdl.set_stories([3.0])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3.0),
                   story="Story1", uid="C1")
    return mdl


def _col_results(Pu, M3, M2):
    return {"cases": {"U": {
        "member_forces": {"C1": [Pu, 0, 0, 0, 0, M3, -Pu, 0, 0, 0, -M2, 0]},
        "member_stations": {"C1": {
            "x": [0.0, 3.0], "N": [-Pu, -Pu], "V2": [0, 0], "V3": [0, 0],
            "T": [0, 0], "M2": [M2, M2], "M3": [M3, M3]}}}}}


def _p_at_m(points, Mu):
    """Independent polyline intersection: largest P at M = Mu."""
    best = None
    for (M1, P1), (M2, P2) in zip(points[:-1], points[1:]):
        if (M1 - Mu) * (M2 - Mu) <= 0 and abs(M2 - M1) > 1e-14:
            t = (Mu - M1) / (M2 - M1)
            P = P1 + t * (P2 - P1)
            best = P if best is None else max(best, P)
    return best


def _m_at_p(points, Pu):
    best = None
    for (M1, P1), (M2, P2) in zip(points[:-1], points[1:]):
        if (P1 - Pu) * (P2 - Pu) <= 0 and abs(P2 - P1) > 1e-14:
            t = (Pu - P1) / (P2 - P1)
            M = M1 + t * (M2 - M1)
            best = M if best is None else max(best, M)
    return best


def test_bresler_square_equal_demands_hand_check():
    """Square symmetric column, equal moments both axes, Pu > 0.1 fc Ag:
    by symmetry phiPn_x = phiPn_y, so 1/phiPn_b = 2/phiPn_x - 1/phiP0 —
    verified to 1e-9 with an independent polyline intersection and the
    hand P0 = 0.85 fc (Ag - Ast) + fy Ast."""
    Pu, M = 1000.0, 60.0
    Ag = SQ * SQ
    assert Pu >= 0.1 * FC * Ag                 # Bresler branch applies (480)
    mdl = _col_model()
    chk = check_concrete_members(mdl, _col_results(Pu, M, M), "U",
                                 {"C1": RebarLayout(**LAY)}, fc=FC)[0]
    assert chk.biaxial is True
    assert chk.method == "bresler"
    # independent: Pnx from the reported polyline; P0 by hand
    Pnx = _p_at_m(chk.pm_points, M)
    As_face = 3.0 * math.pi * 0.020 ** 2 / 4.0
    Ast = 2.0 * As_face
    P0 = 0.85 * FC * (Ag - Ast) + FY * Ast
    phiP0 = PHI_COMPRESSION * P0
    Pnb = 1.0 / (2.0 / Pnx - 1.0 / phiP0)
    assert chk.ratio_biaxial == pytest.approx(Pu / Pnb, rel=1e-9)
    # the governing ratio can never fall below the uniaxial radial value
    assert chk.ratio >= chk.ratio_biaxial - 1e-12
    assert chk.status in ("OK", "NG")
    # serialised fields present
    dd = chk.to_dict()
    assert dd["method"] == "bresler" and dd["biaxial"] is True
    assert dd["Mu22"] == pytest.approx(M)


def test_contour_fallback_low_axial_hand_check():
    """Pu < 0.1 fc Ag -> load contour (Mux/phiMnx)^1.5 + (Muy/phiMny)^1.5
    with phiMn at the demand axial level, hand-verified (square: the two
    capacities are equal by symmetry)."""
    Pu, M = 100.0, 60.0
    assert Pu < 0.1 * FC * SQ * SQ
    mdl = _col_model()
    chk = check_concrete_members(mdl, _col_results(Pu, M, M), "U",
                                 {"C1": RebarLayout(**LAY)}, fc=FC)[0]
    assert chk.method == "contour"
    Mcap = _m_at_p(chk.pm_points, Pu)
    hand = 2.0 * (M / Mcap) ** 1.5
    assert chk.ratio_biaxial == pytest.approx(hand, rel=1e-9)


def test_uniaxial_demand_keeps_v06_path_unchanged():
    """M2 = 0 (and M2 below the 5% trigger): method 'uniaxial', no biaxial
    fields, and the ratio equals the M2 = 0 result to 1e-12."""
    Pu, M = 1000.0, 60.0
    mdl = _col_model()
    lay = {"C1": RebarLayout(**LAY)}
    chk0 = check_concrete_members(mdl, _col_results(Pu, M, 0.0), "U", lay,
                                  fc=FC)[0]
    assert chk0.method == "uniaxial"
    assert chk0.biaxial is False and chk0.ratio_biaxial is None
    assert any("M2 not considered" in n for n in chk0.notes)
    # a tiny M2 below the 5% trigger changes NOTHING about the ratio
    chk1 = check_concrete_members(
        mdl, _col_results(Pu, M, 1.0e-3), "U", lay, fc=FC)[0]
    assert chk1.method == "uniaxial"
    assert chk1.ratio == pytest.approx(chk0.ratio, abs=1e-12)
    assert chk1.equation == chk0.equation == "P-M"
    assert chk1.status == chk0.status


def test_biaxial_engine_end_to_end():
    """A real corner-loaded column (moments both axes from the analysis)
    goes biaxial through the engine + check pipeline."""
    mdl = _col_model()
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.supports.append(PointSupport((0, 0, 0), FIX))
    pat = mdl.pattern("F", "other")
    pat.nodal_loads.append(NodalLoad((0, 0, 3.0), fx=20.0, fy=15.0,
                                     fz=-800.0))
    mdl.add_case("F", {"F": 1.0})
    res = OpenSeesEngine(mdl).run()
    chk = check_concrete_members(mdl, res, "F",
                                 {"C1": RebarLayout(**LAY)}, fc=FC)[0]
    assert chk.biaxial is True
    assert chk.method in ("bresler", "contour")
    # vertical column: local y = -Y, local z = +X, so the fy load bends
    # about local z (M3 = 15*3) and the fx load about local y (M2 = 20*3)
    assert chk.Mu == pytest.approx(15.0 * 3.0, rel=1e-6)
    assert chk.Mu22 == pytest.approx(20.0 * 3.0, rel=1e-6)
    assert chk.ratio_biaxial is not None and chk.ratio_biaxial > 0.0


# --------------------------------------------------------------------------- #
# 4. API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_live_reduction_endpoint(api_client):
    r = api_client.post("/api/model/quick",
                        json={"bays_x": 3, "bays_y": 2, "stories": 4,
                              "bay_width_x": 6.0, "bay_width_y": 6.0})
    assert r.status_code == 200
    resp = api_client.get("/api/live-reduction")
    assert resp.status_code == 200
    factors = resp.get_json()["factors"]
    assert factors["C4-B2"]["R"] == pytest.approx(0.25 + 4.57 / 12.0,
                                                  rel=1e-9)
    assert factors["C4-A1"]["R"] == 1.0


def test_api_design_with_live_reduction(api_client):
    r = api_client.post("/api/model/quick",
                        json={"bays_x": 1, "bays_y": 1, "stories": 1,
                              "bay_width_x": 6.0, "bay_width_y": 6.0})
    assert r.status_code == 200
    body = {"case": "DEAD", "live_reduction": True, "live_case": "LIVE"}
    resp = api_client.post("/api/design/steel", json=body)
    assert resp.status_code == 200
    payload = resp.get_json()
    assert "live_reduction" in payload
    assert set(payload["live_reduction"]) == \
        {f"C1-{a}{b}" for a in "AB" for b in "12"}
    # without the flag the key is absent
    resp2 = api_client.post("/api/design/steel", json={"case": "DEAD"})
    assert "live_reduction" not in resp2.get_json()
    # unknown live case -> 400
    bad = api_client.post("/api/design/steel",
                          json={"case": "DEAD", "live_reduction": True,
                                "live_case": "NOPE"})
    assert bad.status_code == 400
    assert "NOPE" in bad.get_json()["error"]


def test_api_analyze_carries_deflections_and_checks(api_client):
    r = api_client.post("/api/model/quick",
                        json={"bays_x": 1, "bays_y": 1, "stories": 1,
                              "bay_width_x": 6.0, "bay_width_y": 6.0})
    assert r.status_code == 200
    resp = api_client.post("/api/analyze")
    assert resp.status_code == 200
    d = resp.get_json()
    md = d["cases"]["DEAD"]["member_deflections"]
    assert "BX1-A1" in md
    assert len(md["BX1-A1"]["dy"]) == 11
    checks = {e["uid"]: e for e in d["deflection_checks"]["DEAD"]}
    assert "BX1-A1" in checks and "C1-A1" not in checks   # beams only
    entry = checks["BX1-A1"]
    assert entry["limit"] == "L/360"
    assert entry["ratio_str"].startswith("L/")
    assert isinstance(entry["ok"], bool)
    # combos carry them too
    assert "member_deflections" in d["combos"]["1.2D + 1.6L"]
    assert "1.2D + 1.6L" in d["deflection_checks"]


def test_api_model_roundtrips_deflection_limit(api_client):
    mdl = _beam_model("api-rt", PIN, PIN)
    mdl.deflection_limit = 240.0
    resp = api_client.post("/api/model", json=mdl.to_dict())
    assert resp.status_code == 200
    assert resp.get_json()["deflection_limit"] == 240.0
    got = api_client.get("/api/model").get_json()
    assert got["deflection_limit"] == 240.0
