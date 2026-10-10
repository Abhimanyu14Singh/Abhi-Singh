"""Open-structure wind (ETABS Assign > Frame Loads > Open Structure Wind
Parameters) — hand line loads on a lattice tower at several heights."""
import json
import math

import pytest

from skyframe.core import openwind as ow
from skyframe.core.builder import quick_building
from skyframe.core.model import BuildingModel, FrameSection, Material
from skyframe.core.tables import applied_pattern_totals

V = 40.0                                   # m/s
ZG22_C = 2460 * 0.3048                     # ASCE 7-22 Table 26.11-1, exp. C
ZMIN = 15 * 0.3048


def kz22(z):                               # ASCE 7-22 Table 26.10-1 eq.
    return 2.41 * (max(z, ZMIN) / ZG22_C) ** (2 / 9.8)


def qz22(z):                               # kPa, Kzt = Ke = 1 (no Kd)
    return 0.613 * kz22(z) * V ** 2 / 1000.0


def kz16(z):                               # ASCE 7-16 (builder.wind_kz)
    return 2.01 * (max(z, 4.6) / 274.32) ** (2 / 9.5)


def qz16(z):                               # kPa, Kd 0.85 inside qz (7-16)
    return 0.613 * kz16(z) * 0.85 * V ** 2 / 1000.0


B = 4.0
LEG_H, BR_H = 0.3, 0.15                    # section depths (exposed width)


def tower(assign=True):
    """3-panel square lattice tower, 4 m wide, panels 10 m tall: legs,
    horizontal struts at every panel top and one face diagonal per face."""
    m = BuildingModel(name="tower")
    m.diaphragm = "none"
    m.add_material(Material("STL", E=2.0e8, nu=0.3, unit_weight=77.0))
    m.add_section(FrameSection.rectangular("LEG", "STL", 0.2, LEG_H))
    m.add_section(FrameSection.rectangular("BR", "STL", 0.1, BR_H))
    m.set_stories([10.0, 10.0, 10.0])
    pts = [(0, 0), (B, 0), (B, B), (0, B)]
    for k in range(3):
        z0, z1 = 10.0 * k, 10.0 * (k + 1)
        sn = m.stories[k].name
        for i, (x, y) in enumerate(pts):
            m.add_member("column", "LEG", (x, y, z0), (x, y, z1), story=sn,
                         uid=f"L{k}{i}")
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            m.add_member("beam", "BR", (*a, z1), (*b, z1), story=sn,
                         uid=f"S{k}{i}")
            m.add_member("brace", "BR", (*a, z0), (*b, z1), story=sn,
                         uid=f"D{k}{i}")
    if assign:
        ow.assign(m, [mm.uid for mm in m.members], {})
    return m


def rows(summary):
    return {r["uid"]: r for r in summary["members"]}


# --------------------------------------------------------------- Kz / qz
def test_kz_table_values():
    # ASCE 7-22 Table 26.10-1 (exp. C): 0-15 ft 0.85, ~33 ft 1.00
    assert kz22(0.0) == pytest.approx(0.8512, abs=1e-4)
    assert kz22(10.0) == pytest.approx(0.99856, abs=1e-5)
    assert kz22(30.0) == pytest.approx(1.24953, abs=1e-5)


# ------------------------------------------------------ hand line loads
@pytest.mark.parametrize("k,z", [(0, 10.0), (1, 20.0), (2, 30.0)])
def test_strut_line_load_at_height(k, z):
    """Struts along Y (S{k}1: (4,0)->(4,4), S{k}3) are normal to an X
    wind: w = qz Kd G Cf b = qz * 0.85 * 0.85 * 2.0 * 0.15 (7-22)."""
    s = ow.compute(tower(), V=V, code="asce7_22", direction="X")
    r = rows(s)
    w = qz22(z) * 0.85 * 0.85 * 2.0 * BR_H
    for uid in (f"S{k}1", f"S{k}3"):
        assert r[uid]["proj"] == pytest.approx(1.0)
        assert r[uid]["w_i"] == pytest.approx(w, rel=1e-12)
        assert r[uid]["w_j"] == pytest.approx(w, rel=1e-12)
        assert r[uid]["F"] == pytest.approx(w * B, rel=1e-12)
        assert r[uid]["segments"] == [[0.0, 1.0, r[uid]["w_i"],
                                       r[uid]["w_i"]]]
    # struts along X are parallel to the wind -> no load at all
    assert f"S{k}0" not in r and f"S{k}2" not in r


def test_hand_numbers_10m_30m():
    s = rows(ow.compute(tower(), V=V, code="asce7_22", direction="X"))
    # 0.979388 kPa * 0.7225 * 2 * 0.15 = 0.212282 kN/m at 10 m
    assert s["S01"]["w_i"] == pytest.approx(0.212282, abs=2e-6)
    # 1.225537 kPa * 0.7225 * 2 * 0.15 = 0.265635 kN/m at 30 m
    assert s["S21"]["w_i"] == pytest.approx(0.265635, abs=2e-6)


def test_leg_trapezoids_follow_qz():
    """Legs: 4 equal trapezoids with exact w(z) at 0, 2.5, 5, 7.5, 10 m
    (+ panel offsets); the 15 ft Kz floor makes the first one uniform."""
    s = ow.compute(tower(), V=V, code="asce7_22", direction="X")
    r = rows(s)
    for k in range(3):
        leg = r[f"L{k}0"]
        assert leg["proj"] == pytest.approx(1.0)
        for (a, b, wa, wb) in leg["segments"]:
            za, zb = 10.0 * k + 10.0 * a, 10.0 * k + 10.0 * b
            assert wa == pytest.approx(qz22(za) * 0.7225 * 2 * LEG_H,
                                       rel=1e-12)
            assert wb == pytest.approx(qz22(zb) * 0.7225 * 2 * LEG_H,
                                       rel=1e-12)
        exact = sum(0.5 * (wa + wb) * (b - a) * 10.0
                    for a, b, wa, wb in leg["segments"])
        assert leg["F"] == pytest.approx(exact, rel=1e-12)
        # piecewise-linear vs the true integral of the power law: < 0.5 %
        n = 2000
        true = sum(qz22(10.0 * k + 10.0 * (i + 0.5) / n) for i in range(n)
                   ) * 10.0 / n * 0.7225 * 2 * LEG_H
        assert leg["F"] == pytest.approx(true, rel=5e-3)
    seg0 = r["L00"]["segments"][0]
    assert seg0[2] == seg0[3]               # 0..2.5 m below the 15 ft floor


def test_diagonal_projection():
    """Diagonal in a face parallel to the wind: t = (4, 0, 10)/sqrt(116),
    proj = sqrt(1 - (t.d)^2) = 10/sqrt(116); in a face normal to the wind
    (t . d = 0) proj = 1."""
    r = rows(ow.compute(tower(), V=V, direction="X"))
    assert r["D00"]["proj"] == pytest.approx(10 / math.sqrt(116), rel=1e-12)
    assert r["D01"]["proj"] == pytest.approx(1.0, rel=1e-12)
    # z at end i = 0 m: w = qz(0) * 0.7225 * 2 * 0.15 * proj
    assert r["D00"]["w_i"] == pytest.approx(
        qz22(0.0) * 0.7225 * 2 * BR_H * 10 / math.sqrt(116), rel=1e-12)


def test_asce7_16_code():
    """7-16: qz = 0.613 Kz Kzt Kd Ke V^2 (Kd inside qz), F = qz G Cf Af."""
    r = rows(ow.compute(tower(), V=V, code="asce7_16", direction="X"))
    assert r["S11"]["qz_i"] == pytest.approx(qz16(20.0), rel=1e-12)
    assert r["S11"]["w_i"] == pytest.approx(qz16(20.0) * 0.85 * 2 * BR_H,
                                            rel=1e-12)
    assert r["S11"]["qz_i"] == pytest.approx(0.965560, abs=2e-6)


def test_member_params_cf_width_shielding_include():
    m = tower()
    ow.assign(m, ["S01"], {"cf": 1.2, "width": 0.5, "shielding": 0.6})
    ow.assign(m, ["S03"], {"include": False})
    r = rows(ow.compute(m, V=V, direction="X", G=0.9, Kd=0.9))
    assert r["S01"]["w_i"] == pytest.approx(
        qz22(10.0) * 0.9 * 0.9 * 1.2 * 0.5 * 0.6, rel=1e-12)
    assert r["S01"]["cf"] == 1.2 and r["S01"]["width"] == 0.5
    assert "S03" not in r


def test_selection_modes():
    m = tower(assign=False)
    assert ow.compute(m, V=V)["members"] == []
    with pytest.raises(ValueError, match="no frame member"):
        ow.generate(m, "OW", V=V)
    s_all = ow.compute(m, V=V, members="all")
    s_list = ow.compute(m, V=V, members=["S01"])
    assert len(s_all["members"]) == 30          # 36 minus 6 X-struts
    assert [r["uid"] for r in s_list["members"]] == ["S01"]


def test_tower_solidity_cf():
    """ASCE 7 Table 29.4-2: square 4.0e^2 - 5.9e + 4.0 (e=0.2 -> 2.98),
    triangle 3.4e^2 - 4.7e + 3.4 (e=0.2 -> 2.596)."""
    assert ow.trussed_tower_cf(0.2, "square") == pytest.approx(2.98)
    assert ow.trussed_tower_cf(0.2, "triangle") == pytest.approx(2.596)
    r = rows(ow.compute(tower(), V=V, tower={"shape": "square",
                                             "solidity": 0.2}))
    assert r["S01"]["cf"] == pytest.approx(2.98)


def test_angle_45_components():
    m = tower()
    s = ow.compute(m, V=V, angle=45.0)
    assert s["FX"] == pytest.approx(s["FY"], rel=1e-12)
    pat = ow.generate(m, "OW45", V=V, angle=45.0)
    dirs = {ml.direction for ml in pat.member_loads}
    assert dirs == {"global_x", "global_y"}
    tot = applied_pattern_totals(m, "OW45")
    assert tot[0] == pytest.approx(s["FX"], rel=1e-12)
    assert tot[1] == pytest.approx(s["FY"], rel=1e-12)


def test_negative_direction():
    s = ow.compute(tower(), V=V, direction="-Y")
    assert s["d"] == [0.0, -1.0]
    assert s["FY"] < 0 and s["FX"] == 0.0


def test_generate_writes_loads_and_case():
    m = tower()
    s = ow.compute(m, V=V, direction="X")
    pat = ow.generate(m, "OWX", V=V, direction="X")
    assert pat.kind == "wind"
    assert m.cases["OWX"].patterns == {"OWX": 1.0}
    strut = [ml for ml in pat.member_loads if ml.member_uid == "S21"]
    assert len(strut) == 1 and strut[0].kind == "udl"
    assert strut[0].direction == "global_x"
    leg = [ml for ml in pat.member_loads if ml.member_uid == "L20"]
    assert [(ml.a, ml.b) for ml in leg] == [(0.0, 0.25), (0.25, 0.5),
                                           (0.5, 0.75), (0.75, 1.0)]
    assert all(ml.kind == "trapezoid" for ml in leg)
    assert applied_pattern_totals(m, "OWX")[0] == pytest.approx(s["FX"],
                                                                rel=1e-12)
    # regenerating replaces the pattern and keeps the case
    ow.generate(m, "OWX", V=30.0)
    assert len(m.patterns["OWX"].member_loads) == len(pat.member_loads)


def test_engine_equilibrium():
    pytest.importorskip("openseespy")
    from skyframe.engine.opensees_engine import OpenSeesEngine
    m = tower()
    s = ow.compute(m, V=V, direction="X")
    ow.generate(m, "OWX", V=V, direction="X")
    res = OpenSeesEngine(m).run().to_dict()
    base = res["cases"]["OWX"]["base"]
    assert base["FX"] == pytest.approx(-s["FX"], rel=1e-9)
    assert abs(base["FY"]) < 1e-8 and abs(base["FZ"]) < 1e-8


def test_roundtrip_and_validation():
    m = tower()
    ow.assign(m, ["S01"], {"cf": 1.5, "width": 0.4})
    d = m.to_dict()
    md = next(x for x in d["members"] if x["uid"] == "S01")
    assert md["open_wind"] == {"include": True, "cf": 1.5, "width": 0.4,
                               "shielding": 1.0}
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert json.dumps(m2.to_dict()) == json.dumps(d)
    for bad in ({"cf": 0}, {"shielding": 1.5}, {"width": "x"},
                {"foo": 1}, {"include": 1}):
        d2 = json.loads(json.dumps(d))
        d2["members"][0]["open_wind"] = bad
        with pytest.raises(ValueError):
            BuildingModel.from_dict(d2)
    with pytest.raises(ValueError):
        ow.compute(m, V=V, direction="Z")
    with pytest.raises(ValueError):
        ow.compute(m, V=V, code="asce7_10")
    with pytest.raises(ValueError):
        ow.compute(m, V=-1.0)


def test_default_model_unchanged():
    d = quick_building().to_dict()
    assert all("open_wind" not in md for md in d["members"])
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert json.dumps(m2.to_dict()) == json.dumps(d)


def test_auto_width_without_depth():
    m = tower()
    m.sections["BR"].h = 0.0
    r = rows(ow.compute(m, V=V))
    assert r["S01"]["width"] == pytest.approx(0.1)   # falls back to b
    m.sections["BR"].b = 0.0
    with pytest.raises(ValueError, match="no depth"):
        ow.compute(m, V=V)


# ---------------------------------------------------------------- server
def test_server_endpoints():
    pytest.importorskip("openseespy")
    from skyframe.api import server
    app = server.create_app()
    c = app.test_client()
    m = tower(assign=False)
    assert c.post("/api/model", json=m.to_dict()).status_code == 200
    uids = [mm.uid for mm in m.members]
    r = c.post("/api/member/open-wind", json={"uids": uids, "params": {}})
    assert r.status_code == 200
    assert r.get_json()["members"][0]["open_wind"]["width"] == "auto"
    assert c.post("/api/member/open-wind",
                  json={"uids": ["nope"], "params": {}}).status_code == 400
    p = c.post("/api/pattern/open-wind/preview",
               json={"V": V, "direction": "X"}).get_json()
    assert rows(p)["S01"]["w_i"] == pytest.approx(
        qz22(10.0) * 0.7225 * 2 * BR_H, rel=1e-12)
    assert c.post("/api/pattern/open-wind",
                  json={"direction": "X"}).status_code == 400
    assert c.post("/api/pattern/open-wind",
                  json={"V": V, "bogus": 1}).status_code == 400
    r = c.post("/api/pattern/open-wind",
               json={"name": "OWX", "V": V, "direction": "X"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["patterns"]["OWX"]["kind"] == "wind"
    assert "OWX" in d["cases"]
    res = c.post("/api/analyze").get_json()
    assert res["cases"]["OWX"]["base"]["FX"] == pytest.approx(-p["FX"],
                                                             rel=1e-9)
    # clearing
    r = c.post("/api/member/open-wind", json={"uids": uids, "params": None})
    assert all("open_wind" not in mm for mm in r.get_json()["members"])
