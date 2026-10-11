"""Frame local axes: ETABS convention (CONTRACT "Frame local axes: ETABS
convention"; skyframe.core.frame_axes).

Closed-form checks on a cantilever column b = 0.3, h = 0.6 (h along local
2, I33 = b h^3/12 strong, I22 = h b^3/12 weak) under a tip load:

* ETABS convention, angle 0: local 2 = +X, so Fx bends about local 3
  (strong): delta = F L^3 / (3 E b h^3/12); Fy bends about local 2 (weak).
* legacy convention, angle 0: local 2 = -Y, so Fx is the WEAK axis and Fy
  the strong axis (today's behaviour, unchanged).
* ETABS angle 90 == legacy angle 0 for this (doubly symmetric) section.
* converting legacy -> etabs (vertical angle -> angle - 90) reproduces the
  legacy local axes bit-for-bit, hence identical results (exact JSON hash
  for angle 0 members, 1e-12 relative for arbitrary angles).
"""

import copy
import hashlib
import json
import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core import frame_axes as fa
from skyframe.core.builder import quick_building
from skyframe.core.model import (BuildingModel, FrameMember, FrameSection,
                                 Material, MemberLoad, NodalLoad,
                                 PointSupport)

E = 25_000_000.0
NU = 0.2
B, H = 0.3, 0.6
L = 4.0
P = 10.0
I_STRONG = B * H ** 3 / 12.0
I_WEAK = H * B ** 3 / 12.0
D_STRONG = P * L ** 3 / (3.0 * E * I_STRONG)
D_WEAK = P * L ** 3 / (3.0 * E * I_WEAK)


def _close(a, b, tol=1e-12):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _column_model(conv="legacy", angle=0.0, topdown=False, loads=None):
    m = BuildingModel(name="fa")
    if conv is not None:
        m.frame_axis_convention = conv
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("C", E=E, nu=NU))
    m.set_stories([L])
    m.add_section(FrameSection.rectangular("S", "C", B, H))
    pi, pj = ((0, 0, 0), (0, 0, L)) if not topdown else ((0, 0, L), (0, 0, 0))
    m.add_member("column", "S", pi, pj, uid="C1", angle=angle)
    m.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    pat = m.pattern("P", "other")
    for ld in (loads or [NodalLoad((0, 0, L), fx=P)]):
        if isinstance(ld, NodalLoad):
            pat.nodal_loads.append(ld)
        else:
            pat.member_loads.append(ld)
    m.add_case("P", {"P": 1.0})
    return m


def _tip(m):
    d = OpenSeesEngine(m).run().to_dict()
    nid = next(k for k, p in d["nodes"].items() if abs(p[2] - L) < 1e-9)
    return d["cases"]["P"]["node_disp"][nid]


def _hash(d) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# the axis table
# --------------------------------------------------------------------------- #
class TestAxisTable:
    def test_vertical_up(self):
        x, y, z, v, vert = fa.frame_local_axes((0, 0, 0), (0, 0, 3), 0, "legacy")
        assert vert and _close(x, (0, 0, 1)) and _close(y, (0, -1, 0))
        assert _close(z, (1, 0, 0)) and _close(v, (1, 0, 0))
        x, y, z, v, vert = fa.frame_local_axes((0, 0, 0), (0, 0, 3), 0, "etabs")
        assert _close(y, (1, 0, 0)) and _close(z, (0, 1, 0))
        assert _close(v, (0, 1, 0))           # geomTransf vecxz = (0, 1, 0)

    def test_vertical_down(self):
        x, y, z, _, _ = fa.frame_local_axes((0, 0, 3), (0, 0, 0), 0, "legacy")
        assert _close(x, (0, 0, -1)) and _close(y, (0, 1, 0))
        assert _close(z, (1, 0, 0))
        # ETABS: local 1 stays i->j (down), 2 = +X, 3 = 1 x 2 = -Y
        x, y, z, v, _ = fa.frame_local_axes((0, 0, 3), (0, 0, 0), 0, "etabs")
        assert _close(x, (0, 0, -1)) and _close(y, (1, 0, 0))
        assert _close(z, (0, -1, 0)) and _close(v, (0, -1, 0))

    @pytest.mark.parametrize("conv", ["legacy", "etabs"])
    def test_non_vertical_identical(self, conv):
        # beam along +X: 2 = +Z, 3 = -Y; brace: 2 in the vertical plane, up
        x, y, z, _, vert = fa.frame_local_axes((0, 0, 0), (6, 0, 0), 0, conv)
        assert not vert and _close(y, (0, 0, 1)) and _close(z, (0, -1, 0))
        ref = fa.frame_local_axes((0, 0, 0), (3, 4, 5), 17.0, "legacy")
        assert fa.frame_local_axes((0, 0, 0), (3, 4, 5), 17.0, conv) == ref
        x, y, z, _, _ = fa.frame_local_axes((0, 0, 0), (3, 4, 5), 0, conv)
        assert y[2] > 0 and abs(y[0] * 4 - y[1] * 3) < 1e-12   # vertical plane

    @pytest.mark.parametrize("ang", [30.0, 90.0, -45.0, 180.0])
    def test_angle_right_hand_rule(self, ang):
        x0, y0, z0, _, _ = fa.frame_local_axes((0, 0, 0), (0, 0, 3), 0, "etabs")
        x, y, z, v, _ = fa.frame_local_axes((0, 0, 0), (0, 0, 3), ang, "etabs")
        a = math.radians(ang)
        exp = tuple(math.cos(a) * y0[k] + math.sin(a) * z0[k] for k in range(3))
        assert _close(y, exp, 1e-15) and v == z
        # positive angle turns 2 towards 3 (counter-clockwise about +1 = +Z)
        if ang == 90.0:
            assert y == (0.0, 1.0, 0.0) and z == (-1.0, 0.0, 0.0)

    def test_legacy_branch_is_the_old_engine_code(self):
        """The legacy branch reproduces the pre-change engine function."""
        def old(pi, pj, ang):
            d = tuple(pj[k] - pi[k] for k in range(3))
            x = fa._unit(d)
            vertical = math.hypot(d[0], d[1]) < 1e-6
            vecxz = (1.0, 0.0, 0.0) if vertical else fa._unit((x[1], -x[0], 0.0))
            y = fa._unit(fa._cross(vecxz, x))
            z = fa._cross(x, y)
            if abs(ang) > 1e-12:
                a = math.radians(ang)
                ca, sa = math.cos(a), math.sin(a)
                y2 = tuple(ca * y[k] + sa * z[k] for k in range(3))
                z2 = tuple(ca * z[k] - sa * y[k] for k in range(3))
                y, z = fa._unit(y2), fa._unit(z2)
                vecxz = z
            return x, y, z, vecxz, vertical
        for pi, pj in (((0, 0, 0), (0, 0, 3)), ((1, 2, 3), (1, 2, 0)),
                       ((0, 0, 0), (5, 0, 0)), ((0, 0, 0), (2, -3, 4))):
            for ang in (0.0, 15.0, 90.0, -120.0):
                assert fa.frame_local_axes(pi, pj, ang, "legacy") == old(pi, pj, ang)

    def test_engine_and_buckling_use_the_member_stamp(self):
        from skyframe.core.buckling import _local_axes as bk_axes
        m = FrameMember("C", "column", "S", (0, 0, 0), (0, 0, 3))
        assert engine_mod._local_axes(m)[1] == (0.0, -1.0, 0.0)
        m.axis_convention = "etabs"
        assert engine_mod._local_axes(m)[1] == (1.0, 0.0, 0.0)
        assert engine_mod._local_axes(m)[3] == (0.0, 1.0, 0.0)
        assert bk_axes(m)[1] == (1.0, 0.0, 0.0)


# --------------------------------------------------------------------------- #
# model field / serialisation
# --------------------------------------------------------------------------- #
class TestModelField:
    def test_defaults_and_absent_key(self):
        m = quick_building()
        assert m.frame_axis_convention == "legacy"
        d = m.to_dict()
        assert "frame_axis_convention" not in d
        m2 = BuildingModel.from_dict(d)
        assert m2.frame_axis_convention == "legacy"
        assert json.dumps(m2.to_dict(), sort_keys=True) == json.dumps(d, sort_keys=True)
        assert all(x.axis_convention == "legacy" for x in m2.members)

    def test_etabs_serialised_and_stamped(self):
        m = quick_building()
        fa.as_new_model(m)
        d = m.to_dict()
        assert d["frame_axis_convention"] == "etabs"
        m2 = BuildingModel.from_dict(d)
        assert m2.frame_axis_convention == "etabs"
        assert all(x.axis_convention == "etabs" for x in m2.members)
        assert m2.to_dict() == d
        assert "axis_convention" not in d["members"][0]   # model-level only

    def test_bad_value(self):
        d = quick_building().to_dict()
        d["frame_axis_convention"] = "sap"
        with pytest.raises(ValueError):
            BuildingModel.from_dict(d)
        m = quick_building()
        m.frame_axis_convention = "x"
        with pytest.raises(ValueError):
            m.validate()

    def test_add_member_stamps(self):
        m = _column_model("etabs")
        assert m.members[0].axis_convention == "etabs"

    def test_engine_stamps_when_field_set_late(self):
        m = _column_model("legacy")
        m.frame_axis_convention = "etabs"          # set after add_member
        u = _tip(m)
        assert u[0] == pytest.approx(D_STRONG, rel=1e-9)


# --------------------------------------------------------------------------- #
# closed-form cantilever
# --------------------------------------------------------------------------- #
class TestCantilever:
    def test_etabs_fx_strong(self):
        u = _tip(_column_model("etabs"))
        assert u[0] == pytest.approx(D_STRONG, rel=1e-9)
        assert abs(u[1]) < 1e-15

    def test_legacy_fx_weak_as_today(self):
        u = _tip(_column_model("legacy"))
        assert u[0] == pytest.approx(D_WEAK, rel=1e-9)

    def test_fy_opposite(self):
        fy = [NodalLoad((0, 0, L), fy=P)]
        assert _tip(_column_model("etabs", loads=fy))[1] == pytest.approx(D_WEAK, rel=1e-9)
        assert _tip(_column_model("legacy", loads=fy))[1] == pytest.approx(D_STRONG, rel=1e-9)

    def test_etabs_angle90_equals_legacy_angle0(self):
        for ld in ([NodalLoad((0, 0, L), fx=P)], [NodalLoad((0, 0, L), fy=P)]):
            ue = _tip(_column_model("etabs", 90.0, loads=ld))
            ul = _tip(_column_model("legacy", 0.0, loads=ld))
            assert ue == pytest.approx(ul, rel=1e-9, abs=1e-15)
        assert _tip(_column_model("etabs", 90.0))[0] == pytest.approx(D_WEAK, rel=1e-9)

    def test_top_down_column_etabs(self):
        # drawn top-down: 2 = +X still -> Fx bends the strong axis
        u = _tip(_column_model("etabs", topdown=True))
        assert u[0] == pytest.approx(D_STRONG, rel=1e-9)

    def test_local_member_load_follows_local2(self):
        # local_y UDL w on a cantilever column: tip delta = w L^4 / 8 E I33,
        # along +X under ETABS, along -Y under legacy
        w = 2.0
        ld = [MemberLoad("C1", "udl", w, 0.0, 0.0, 1.0, "local_y")]
        ue = _tip(_column_model("etabs", loads=ld))
        ul = _tip(_column_model("legacy", loads=ld))
        exp = w * L ** 4 / (8 * E * I_STRONG)
        assert ue[0] == pytest.approx(exp, rel=1e-9) and abs(ue[1]) < 1e-14
        assert ul[1] == pytest.approx(-exp, rel=1e-9) and abs(ul[0]) < 1e-14


# --------------------------------------------------------------------------- #
# conversion keeps results
# --------------------------------------------------------------------------- #
def _rect_frame(angle_cols=(0.0, 0.0, 30.0, 0.0)):
    """Portal-ish model: 4 rectangular columns (one at 30 deg, one drawn
    top-down), beams, nodal Fx/Fy, local member loads, a local joint
    offset and a cardinal point — everything that depends on local axes."""
    m = BuildingModel(name="rect")
    m.rigid_diaphragms = False
    m.num_modes = 3
    m.add_material(Material("C", E=E, nu=NU, unit_weight=24.0))
    m.set_stories([3.5])
    m.add_section(FrameSection.rectangular("COL", "C", B, H))
    m.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
    pts = [(0, 0), (6, 0), (6, 5), (0, 5)]
    for k, ((x, y), a) in enumerate(zip(pts, angle_cols)):
        if k == 3:   # drawn top-down
            m.add_member("column", "COL", (x, y, 3.5), (x, y, 0), uid=f"C{k}",
                         angle=a)
        else:
            m.add_member("column", "COL", (x, y, 0), (x, y, 3.5), uid=f"C{k}",
                         angle=a, cardinal_point=(8 if k == 1 else 10))
        m.supports.append(PointSupport((x, y, 0), (1,) * 6))
    for k in range(4):
        (x0, y0), (x1, y1) = pts[k], pts[(k + 1) % 4]
        m.add_member("beam", "BM", (x0, y0, 3.5), (x1, y1, 3.5), uid=f"B{k}")
    m.members[0].joint_offsets = {"i": [0.0, 0.1, 0.0], "j": [0.0, 0.1, 0.0],
                                  "system": "local"}
    pat = m.pattern("L", "other")
    pat.nodal_loads.append(NodalLoad((6, 5, 3.5), fx=12.0, fy=-7.0))
    pat.member_loads.append(MemberLoad("C0", "udl", 3.0, 0, 0, 1, "local_y"))
    pat.member_loads.append(MemberLoad("C3", "point", 5.0, 0, 0.4, 1, "local_y"))
    pat.member_loads.append(MemberLoad("B1", "udl", 10.0, 0, 0, 1, "gravity"))
    m.add_case("L", {"L": 1.0})
    return m


def _results(m):
    return OpenSeesEngine(m).run().to_dict()


class TestConversion:
    def test_angle_rule(self):
        assert fa.converted_angle(0.0) == -90.0
        assert fa.converted_angle(90.0) == 0.0
        assert fa.converted_angle(-120.0) == 150.0
        assert fa.converted_angle(270.0) == 180.0 - 360.0

    def test_hash_identical_angle0(self):
        m = _rect_frame((0.0, 0.0, 0.0, 0.0))
        r0 = _results(m)
        m2 = BuildingModel.from_dict(m.to_dict())
        rep = fa.convert_model_to_etabs(m2)
        assert rep["converted"] and m2.frame_axis_convention == "etabs"
        assert sorted(r["uid"] for r in rep["members"]) == ["C0", "C1", "C2", "C3"]
        assert all(r["new_angle"] == -90.0 for r in rep["members"])
        r1 = _results(BuildingModel.from_dict(m2.to_dict()))
        assert _hash(r0) == _hash(r1)

    def test_tolerance_any_angle(self):
        m = _rect_frame((0.0, 0.0, 30.0, 0.0))
        r0 = _results(m)
        d, rep = fa.convert_dict_to_etabs(m.to_dict())
        assert d["frame_axis_convention"] == "etabs"
        r1 = _results(BuildingModel.from_dict(d))

        def walk(a, b):
            if isinstance(a, (int, float)) and not isinstance(a, bool):
                assert a == pytest.approx(b, rel=1e-9, abs=1e-12)
            elif isinstance(a, dict):
                assert a.keys() == b.keys()
                for k in a:
                    walk(a[k], b[k])
            elif isinstance(a, list):
                assert len(a) == len(b)
                for x, y in zip(a, b):
                    walk(x, y)
        walk(r0["cases"], r1["cases"])
        walk(r0["modal"], r1["modal"])

    def test_cantilever_delta_identical(self):
        m = _column_model("legacy")
        u0 = _tip(m)
        fa.convert_model_to_etabs(m)
        assert m.members[0].angle == -90.0
        assert _tip(m) == u0
        assert u0[0] == pytest.approx(D_WEAK, rel=1e-9)

    def test_noop_on_etabs_and_curved_untouched(self):
        m = _column_model("etabs", angle=10.0)
        rep = fa.convert_model_to_etabs(m)
        assert not rep["converted"] and m.members[0].angle == 10.0

    def test_axes_identical_after_conversion(self):
        for pi, pj in (((0, 0, 0), (0, 0, 3)), ((0, 0, 3), (0, 0, 0))):
            for ang in (0.0, 30.0, 90.0, -150.0):
                a = fa.frame_local_axes(pi, pj, ang, "legacy")
                b = fa.frame_local_axes(pi, pj, fa.converted_angle(ang), "etabs")
                for u, v in zip(a[:4], b[:4]):
                    assert _close(u, v, 1e-15)


class TestCurved:
    def test_curved_chords_convention_independent(self):
        from skyframe.core.curved import expand_members
        res = []
        for conv in ("legacy", "etabs"):
            m = BuildingModel(name="arc")
            m.frame_axis_convention = conv
            m.add_material(Material("C", E=E, nu=NU))
            m.set_stories([4.0])
            m.add_section(FrameSection.rectangular("S", "C", B, H))
            mem = m.add_member("beam", "S", (0, 0, 0), (0, 0, 4.0), uid="A")
            # 7 chords: the middle chord (symmetric about the apex) is
            # exactly VERTICAL, so its default axes differ per convention
            mem.curve = {"type": "arc", "via": [1.0, 0.0, 2.0], "segments": 7}
            m2 = copy.deepcopy(m)
            expand_members(m2)
            assert any(engine_mod._local_axes(c)[4] for c in m2.members)
            res.append([engine_mod._local_axes(c)[:3] for c in m2.members])
        for a, b in zip(*res):
            for u, v in zip(a, b):
                assert _close(u, v, 1e-12)


class TestInsertion:
    def test_cardinal_point_follows_local2(self):
        from skyframe.core.insertion import member_joint_offsets
        out = {}
        for conv in ("legacy", "etabs"):
            m = _column_model(conv)
            m.members[0].cardinal_point = 8          # top centre: +h/2 on 2
            ei, ej = member_joint_offsets(m, m.members[0])
            out[conv] = ei
        # the joint sits at the top-centre -> the axis is offset -h/2 on 2
        assert _close(out["etabs"], (-H / 2, 0.0, 0.0), 1e-12)
        assert _close(out["legacy"], (0.0, H / 2, 0.0), 1e-12)


# --------------------------------------------------------------------------- #
# API new-model paths
# --------------------------------------------------------------------------- #
class TestApi:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
        from skyframe.api import server
        app = server.create_app()
        app.config["TESTING"] = True
        saved = server._state["model"]
        yield app.test_client(), server
        server._state["model"] = saved

    def test_quick_and_startup_are_etabs(self, client):
        c, server = client
        assert server.quick_building().frame_axis_convention == "legacy"
        r = c.post("/api/model/quick", json={"stories": 2})
        assert r.status_code == 200 and r.get_json()["frame_axis_convention"] == "etabs"
        assert c.get("/api/model/info").get_json()["frame_axis_convention"] == "etabs"

    def test_post_model_legacy_unchanged(self, client):
        c, _ = client
        d = quick_building().to_dict()
        r = c.post("/api/model", json=d).get_json()
        assert "frame_axis_convention" not in r
        assert json.dumps(r, sort_keys=True) == json.dumps(
            BuildingModel.from_dict(d).to_dict(), sort_keys=True)
        assert c.get("/api/model/info").get_json()["frame_axis_convention"] == "legacy"

    def test_convert_endpoint(self, client):
        c, server = client
        d = _rect_frame((0.0, 15.0, 0.0, 0.0)).to_dict()
        c.post("/api/model", json=d)
        r = c.post("/api/model/convert-axes", json={})
        j = r.get_json()
        assert r.status_code == 200 and j["model"]["frame_axis_convention"] == "etabs"
        angs = {mm["uid"]: mm["angle"] for mm in j["model"]["members"]}
        assert angs["C0"] == -90.0 and angs["C1"] == -75.0 and angs["B0"] == 0.0
        assert len(j["report"]["members"]) == 4
        assert server._state["model"].frame_axis_convention == "etabs"
        bad = c.post("/api/model/convert-axes", json={"model": {"materials": 5}})
        assert bad.status_code == 400

    def test_importers_are_etabs(self):
        from skyframe.io.e2k import import_e2k
        text = """$ STORIES - IN SEQUENCE FROM TOP
  STORY "S1"  HEIGHT 3.5
  STORY "BASE"  ELEV 0
$ MATERIAL PROPERTIES
  MATERIAL  "CONC"  TYPE "Concrete"  E 25000000  U 0.2
$ FRAME SECTIONS
  FRAMESECTION  "COL"  MATERIAL "CONC"  SHAPE "Concrete Rectangular"  D 0.6  B 0.3
$ POINT COORDINATES
  POINT "1"  0 0
$ LINE CONNECTIVITIES
  LINE  "C1"  COLUMN  "1"  "1"  1
$ LINE ASSIGNS
  LINEASSIGN  "C1"  "S1"  SECTION "COL"
$ END OF MODEL FILE
"""
        mdl, _w = import_e2k(text)
        assert mdl.frame_axis_convention == "etabs"
        assert all(m.axis_convention == "etabs" for m in mdl.members)
