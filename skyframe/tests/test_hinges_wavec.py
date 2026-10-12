"""Wave-C hinges: P-M2-M3 capacity that tracks the axial load during the
push, strength drop past C for interacting hinges, axial-updated auto
P-M2-M3 hinges, Caltrans SDC and TBI auto-hinge tables.

CONTRACT "Wave C hinges".  Hand values:

* diamond surface ``M3y(P) = 60 (1 - |P| / 3000)`` (kN m, kN);
* backbone ratios B 1.0 -> C 1.1 at 0.03 -> D 0.4 -> E 0.4 at 0.06;
* the capacity of step k+1 is ``r(dp_k) * M3y(P_k)`` (explicit update);
* W14x90 ASCE 41-17 column ``M_y(P) = Z Fye (1 - P / (2 Pye))``.
"""

import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core import interaction_hinges as cih  # noqa: E402
from skyframe.core import user_hinges as uh  # noqa: E402
from skyframe.core.model import NodalLoad  # noqa: E402
from skyframe.core.nonlinear_static import NonlinearStaticCase  # noqa: E402
from skyframe.design import hinge_tables as ht  # noqa: E402
from skyframe.design import hinge_tables_c as htc  # noqa: E402
from skyframe.design.steel import design_properties  # noqa: E402
from skyframe.engine import nonlinear_static as nls  # noqa: E402

from test_wave_b_hinges import (_diamond, _rc_col, _rc_square,  # noqa: E402
                                _steel_col, FY)

BB_DROP = [[0, 0], [0, 1.0], [0.03, 1.1], [0.03, 0.4], [0.06, 0.4]]


def _my3(P):
    return max(60.0 * (1.0 - abs(P) / 3000.0), 0.0)


def _push(prop, vert=-100.0, target=0.25, steps=100):
    """Cantilever, lateral (Y) + vertical load growing together under
    displacement control: the hinge axial force rises with the push."""
    m = _rc_square()
    m.add_hinge_property("PM", prop)
    m.members[0].hinges = [{"property": "PM", "relative_distance": 0.0}]
    m.pattern("L", "other").nodal_loads.append(
        NodalLoad((0, 0, 3.0), fy=1.0, fz=vert))
    m.nonlinear_static_cases["N"] = NonlinearStaticCase(
        "N", loads=[{"pattern": "L", "scale": 1.0}],
        load_application="displacement_control",
        control_point=[0, 0, 3.0], control_dof="UY", target_disp=target,
        steps=steps)
    m.validate()
    res = nls.run_case(OpenSeesEngine(m), "N")
    return [h for h in res.hinge_detail if h["type"] == "PMM"][0]


def test_capacity_ratio_hand_points():
    p = {"backbone": BB_DROP, "drop_strength": "drops"}
    assert cih.capacity_ratio(p, 0.0) == 1.0
    assert cih.capacity_ratio(p, 0.015) == pytest.approx(1.05)
    assert cih.capacity_ratio(p, 0.03) == pytest.approx(1.1)
    assert cih.capacity_ratio(p, 0.0300001) == pytest.approx(0.4)
    assert cih.capacity_ratio(p, 0.05) == pytest.approx(0.4)
    assert cih.capacity_ratio(p, 0.07) == cih.RESIDUAL_RATIO
    h = {"backbone": BB_DROP, "drop_strength": "holds"}
    assert cih.capacity_ratio(h, 0.06) == pytest.approx(1.2)  # B-C line


def test_pmm_capacity_tracks_axial_force_during_push():
    hd = _push({"type": "PMM", "surface": _diamond(),
                "backbone": [[0, 0], [0, 1.0], [0.3, 1.1]],
                "axial": {"source": "analysis"}})
    P, cap, r = hd["P_step"], hd["capacity_M3"], hd["capacity_ratio"]
    m3 = [abs(x) for x in hd["m3"]]
    assert hd["axial_update"] and hd["capacity_update"]
    # P grows with the push (100 kN per kN of shear)
    assert abs(P[-1]) > abs(P[10]) > 0.0
    # the capacity is r(dp) * M3y(P) on the hand interaction curve
    for k in range(len(P)):
        assert cap[k] == pytest.approx(r[k] * _my3(P[k]), rel=1e-12)
    # yielded steps sit ON the curve at the previous step's P
    on = 0
    for k in range(1, len(P)):
        if hd["hinge_state"][k] != "A-B":
            assert m3[k] == pytest.approx(cap[k - 1], rel=1e-6)
            on += 1
    assert on > 20
    # ... and the capacity really changed with P along the push: a hinge
    # holding the gravity P (= 0 here) would yield at 60 kN m
    assert cap[0] == pytest.approx(60.0)
    assert max(m3[6:]) < 0.65 * 60.0      # one-step lag at first yield
    # explicit-lag error stays within the per-step change of M3y(P)
    k = len(P) - 1
    assert abs(m3[k] - cap[k]) <= abs(cap[k] - cap[k - 1]) * 1.01 + 1e-6


def test_post_c_strength_drop_matches_backbone():
    hd = _push({"type": "PMM", "surface": _diamond(), "backbone": BB_DROP,
                "axial": {"source": "analysis"}, "drop_strength": "drops"})
    st = hd["hinge_state"]
    assert "C-D" in st or "D-E" in st
    m3 = [abs(x) for x in hd["m3"]]
    P = hd["P_step"]
    k_de = [k for k in range(1, len(st)) if st[k] == "D-E"
            and st[k - 1] == "D-E"]
    assert k_de
    for k in k_de[2:]:
        # residual plateau 0.4 * M3y(P) of the previous step
        assert m3[k] == pytest.approx(0.4 * _my3(P[k - 1]), rel=1e-6)
    assert hd["drop_strength"] == "drops"
    # past E: residual
    if ">E" in st:
        k = st.index(">E") + 1
        assert m3[k] <= 1.1e-3 * 60.0


def test_drop_with_constant_axial_value():
    hd = _push({"type": "PMM", "surface": _diamond(), "backbone": BB_DROP,
                "axial": {"source": "value", "value": -900.0},
                "drop_strength": "drops"}, vert=0.0)
    m3 = [abs(x) for x in hd["m3"]]
    st = hd["hinge_state"]
    k = [i for i in range(2, len(st)) if st[i] == "D-E" and st[i - 1]
         == "D-E"]
    assert k and m3[k[-1]] == pytest.approx(0.4 * 0.7 * 60.0, rel=1e-6)
    assert not hd["axial_update"]


def test_legacy_pmm_unchanged_keys():
    p = uh.normalize_property("PM", {"type": "PMM", "surface": _diamond(),
                                     "backbone": [[0, 0], [0, 1.0],
                                                  [0.03, 1.1]]})
    assert p["drop_strength"] == "holds"
    assert p["axial"]["source"] == "gravity"
    with pytest.raises(ValueError, match="source"):
        uh.normalize_property("PM", {"type": "PMM", "surface": _diamond(),
                                     "backbone": [[0, 0], [0, 1.0]],
                                     "axial": {"source": "bogus"}})


def test_auto_pmm_axial_update_tracks_P():
    """W14x90 ASCE 41-17 auto P-M2-M3 hinge with axial_update: the
    generated surface reproduces M_y(P) = Z Fye (1 - P/(2 Pye)) and the
    capacity follows the column axial force during the push."""
    m = _steel_col(P=0.0)
    m.add_hinge_property("AC", {"type": "auto", "auto": {
        "code": "ASCE41-17", "component": "steel_column", "dof": "PMM",
        "axial_update": True}})
    m.members[0].hinges = [{"property": "AC", "relative_distance": 0.0}]
    m.pattern("L", "other").nodal_loads.append(
        NodalLoad((0, 0, 3.5), fy=1.0, fz=-3.5))
    m.nonlinear_static_cases["N"] = NonlinearStaticCase(
        "N", loads=[{"pattern": "L", "scale": 1.0}],
        load_application="displacement_control",
        control_point=[0, 0, 3.5], control_dof="UY", target_disp=0.15,
        steps=60)
    m.validate()
    res = nls.run_case(OpenSeesEngine(m), "N")
    hd = [h for h in res.hinge_detail if h["type"] == "PMM"][0]
    pr = design_properties("W14x90")
    Fye = 1.1 * FY
    Pye = pr["A"] * Fye
    for P, c, r in zip(hd["P_step"], hd["capacity_M3"],
                       hd["capacity_ratio"]):
        Pc = max(-P, 0.0)
        assert Pc < 0.17 * Pye             # below the 0.2 Pye kink segment
        # sampled surface (81 points): exact on segments without the
        # 0.2 Pye kink, else within the chord error
        assert c == pytest.approx(r * pr["Zx"] * Fye * (1 - Pc / (2 * Pye)),
                                  rel=1e-9)
    assert hd["auto"]["axial_update"] and "preliminary" in hd
    assert abs(hd["P_step"][-1]) > 100.0


def test_auto_axial_update_validation():
    with pytest.raises(ValueError, match="PMM"):
        ht.normalize_auto({"code": "ASCE41-17", "component": "steel_column",
                           "dof": "M3", "axial_update": True})
    a = ht.normalize_auto({"code": "ASCE41-17", "component": "steel_column",
                           "dof": "PMM"})
    assert "axial_update" not in a                       # legacy shape


# --------------------------------------------------------------- tables
def _auto(code, comp, dof, **params):
    return uh.normalize_property("A", {"type": "auto", "auto": {
        "code": code, "component": comp, "dof": dof,
        "params": params}})["auto"]


def test_caltrans_column_hinge():
    m = _rc_col(P=0.0)
    auto = _auto("Caltrans", "rc_column", "M3", rho_t=0.008, db=0.025)
    P = 1500.0
    specs, why = ht.generate(m, m.members[0], auto, P)
    assert not why
    s = specs[0]
    inp = s["meta"]["inputs"]
    fce = max(1.3 * 30_000.0, htc.CT_FC_MIN)
    assert inp["fce"] == pytest.approx(fce)
    fye = htc.CT_FY
    b, h, L = 0.4, 0.5, 3.0
    phi_y = 2.1 * (fye / ht.ES_REBAR) / h
    eps_cu = 0.004 + 1.4 * 0.016 * fye * 0.09 / (1.5 * fce)
    Lp = max(0.08 * L + 0.022 * 475.0 * 0.025, 0.044 * 475.0 * 0.025)
    assert inp["phi_y"] == pytest.approx(phi_y)
    assert inp["eps_cu"] == pytest.approx(eps_cu)
    assert inp["Lp"] == pytest.approx(Lp)
    th_p = Lp * (eps_cu / inp["c_u"] - phi_y)
    assert s["pos"]["a"] == pytest.approx(th_p)
    assert s["acc"]["CP"] == pytest.approx(th_p)
    assert s["acc"]["IO"] == pytest.approx(0.25 * th_p)
    # c_u: axial equilibrium of the 0.85 f'cc block + bars at P
    d = 0.9 * h
    Mp = ht.rc_section_Mn(b, h, fce, fye, 0.01 * b * d, 0.01 * b * d, d,
                          0.1 * h, P)
    assert s["pos"]["F_B"] == pytest.approx(Mp)
    assert s["meta"]["preliminary"] and s["meta"]["code"] == "Caltrans"
    assert ht.TABLE_INFO[("Caltrans", "rc_column")]["verified"] is False


def test_tbi_tables():
    m = _rc_col(P=0.0)
    P = 800.0
    t = ht.generate(m, m.members[0], _auto("TBI", "rc_column", "M3"), P)[0][0]
    a = ht.generate(m, m.members[0], _auto("ASCE41-17", "rc_column", "M3",
                                           fc_factor=1.3, fy_factor=1.17),
                    P)[0][0]
    for k in ("F_B", "a", "b", "c"):
        assert t["pos"][k] == pytest.approx(a["pos"][k], rel=1e-12)
    assert t["meta"]["code"] == "TBI" and t["meta"]["preliminary"]
    cb = ht.generate(m, m.members[0], _auto("TBI", "rc_coupling_beam",
                                            "M3"), 0.0)[0][0]
    assert (cb["pos"]["a"], cb["pos"]["b"], cb["pos"]["c"]) == (
        0.030, 0.050, 0.80)
    assert cb["acc"] == {"IO": 0.006, "LS": 0.018, "CP": 0.030}
    cat = ht.catalog()
    assert "Caltrans" in cat["codes"] and "TBI" in cat["codes"]
    assert set(cat["codes"]["TBI"]) == {"rc_beam", "rc_column",
                                        "rc_coupling_beam"}


def test_caltrans_pushover_runs():
    m = _rc_col(P=600.0)
    m.add_hinge_property("CT", {"type": "auto", "auto": {
        "code": "Caltrans", "component": "rc_column", "dof": "M3"}})
    m.members[0].hinges = [{"property": "CT", "relative_distance": 0.0}]
    m.add_pushover_case("PO", "Y", gravity={"D": 1.0}, target_drift=0.03,
                        steps=30)
    m.validate()
    r = OpenSeesEngine(m).run_pushover("PO")
    h = [x for x in r.hinge_detail if x.get("auto")][0]
    assert h["auto"]["code"] == "Caltrans" and h["preliminary"]
    assert max(r.base_shear) > 0.0
