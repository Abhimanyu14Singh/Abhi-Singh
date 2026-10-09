"""Check Model (ETABS Analyze > Check Model) + stability diagnostics.

Each defect test builds a small model with exactly that defect and asserts
the exact issue code and objects reported.
"""

import time

import pytest

from skyframe.core.builder import quick_building
from skyframe.core.checks import (STABILITY_MAX_DOFS, check_model,
                                  stability_diagnostics)
from skyframe.core.model import (AreaLoad, BuildingModel, FrameMember,
                                 FrameSection, LoadCase, LoadCombo,
                                 Material, MemberLoad,
                                 NodalLoad, PointSupport, ShellRegion,
                                 ShellSection)


# --------------------------------------------------------------------- utils
def base_model(stories=(3.0,)) -> BuildingModel:
    m = BuildingModel(name="chk")
    m.materials["C"] = Material("C", E=3.0e7, nu=0.2, unit_weight=24.0)
    m.add_section(FrameSection.rectangular("S", "C", 0.4, 0.4))
    m.add_shell_section(ShellSection("SL", "C", 0.2))
    if stories:
        m.set_stories(list(stories))
    return m


def portal(fixity="fixed", releases="", rigid=True) -> BuildingModel:
    m = base_model()
    m.base_fixity = fixity
    m.rigid_diaphragms = rigid
    m.add_member("column", "S", (0, 0, 0), (0, 0, 3), uid="C1")
    m.add_member("column", "S", (5, 0, 0), (5, 0, 3), uid="C2")
    m.add_member("beam", "S", (0, 0, 3), (5, 0, 3), uid="B1",
                 releases=releases)
    return m


def box_frame() -> BuildingModel:
    """1x1 bay, 1 story, 4 columns + 4 beams (clean)."""
    m = base_model()
    pts = [(0, 0), (6, 0), (6, 5), (0, 5)]
    for k, (x, y) in enumerate(pts):
        m.add_member("column", "S", (x, y, 0), (x, y, 3), uid=f"C{k + 1}")
    for k in range(4):
        (x1, y1), (x2, y2) = pts[k], pts[(k + 1) % 4]
        m.add_member("beam", "S", (x1, y1, 3), (x2, y2, 3), uid=f"B{k + 1}")
    return m


def raw_shell(m, uid, corners, kind="slab", behavior="membrane",
              section="SL", mesh_size=1.0):
    """Append a shell WITHOUT add_shell's validation (defect injection)."""
    r = ShellRegion(uid, kind, behavior, section,
                    [tuple(float(v) for v in c) for c in corners],
                    mesh_size=mesh_size)
    m.shells.append(r)
    return r


def issues_of(res, code):
    return [i for i in res["issues"] if i["code"] == code]


def codes(res):
    return {i["code"] for i in res["issues"]}


# ------------------------------------------------------------- clean models
def test_clean_portal_has_no_issues():
    res = check_model(portal())
    assert res["summary"]["errors"] == 0
    assert res["summary"]["warnings"] == 0
    assert res["issues"] == []
    assert res["summary"]["ok"] is True


def test_clean_frame_with_slab_and_wall_has_no_errors():
    m = box_frame()
    m.add_shell("slab", "membrane", "SL",
                [(0, 0, 3), (6, 0, 3), (6, 5, 3), (0, 5, 3)], uid="S1")
    m.add_shell("wall", "shell", "SL",
                [(0, 0, 0), (6, 0, 0), (6, 0, 3), (0, 0, 3)], uid="W1")
    p = m.pattern("DEAD", "dead")
    p.area_loads.append(AreaLoad("S1", 5.0))
    m.add_case("D", {"DEAD": 1.0})
    m.add_combo("U", {"D": 1.4})
    res = check_model(m)
    assert res["summary"]["errors"] == 0, res["issues"]
    assert res["summary"]["warnings"] == 0, res["issues"]


def test_ten_story_five_by_five_check_is_fast_and_clean():
    m = quick_building(bays_x=5, bays_y=5, stories=10)
    t0 = time.perf_counter()
    res = check_model(m)
    dt = time.perf_counter() - t0
    assert dt < 2.0, dt
    assert res["summary"]["errors"] == 0
    assert res["summary"]["n_frames"] == len(m.members) == 960


# ---------------------------------------------------------------- joints
def test_coincident_joints_reported():
    m = portal()
    m.members[2] = FrameMember("B1", "beam", "S", (0.0005, 0.0, 3.0),
                               (5.0, 0.0, 3.0))
    res = check_model(m, tolerance=0.001)
    found = issues_of(res, "JOINT_COINCIDENT")
    assert len(found) == 1
    assert found[0]["severity"] == "warning"
    assert set(found[0]["objects"]) == {"C1", "B1"}
    assert found[0]["location"] == [0.0, 0.0, 3.0]
    # a smaller tolerance no longer sees them as coincident
    assert not issues_of(check_model(m, tolerance=1e-4), "JOINT_COINCIDENT")


def test_zero_length_frame_reported():
    m = portal()
    m.members.append(FrameMember("Z1", "beam", "S", (5, 0, 3),
                                 (5.0004, 0, 3)))
    res = check_model(m)
    found = issues_of(res, "FRAME_ZERO_LENGTH")
    assert [f["objects"] for f in found] == [["Z1"]]
    assert found[0]["severity"] == "error"


def test_duplicate_frames_reported_either_orientation():
    m = portal()
    m.add_member("beam", "S", (5, 0, 3), (0, 0, 3), uid="B2")
    res = check_model(m)
    found = issues_of(res, "FRAME_DUPLICATE")
    assert len(found) == 1 and set(found[0]["objects"]) == {"B1", "B2"}
    assert found[0]["location"] == [2.5, 0.0, 3.0]
    assert not issues_of(res, "FRAME_OVERLAP")


def test_crossing_braces_without_joint():
    m = portal()
    m.add_member("brace", "S", (0, 0, 0), (5, 0, 3), uid="D1")
    m.add_member("brace", "S", (5, 0, 0), (0, 0, 3), uid="D2")
    res = check_model(m)
    found = issues_of(res, "FRAME_INTERSECTION")
    assert len(found) == 1
    assert set(found[0]["objects"]) == {"D1", "D2"}
    assert found[0]["location"] == [2.5, 0.0, 1.5]


def test_frame_end_on_span_of_other_frame():
    m = portal()
    m.add_member("beam", "S", (2.5, 0, 3), (2.5, 4, 3), uid="B2")
    res = check_model(m)
    found = issues_of(res, "FRAME_JOINT_ON_SPAN")
    assert len(found) == 1
    assert found[0]["objects"] == ["B2", "B1"]
    assert found[0]["location"] == [2.5, 0.0, 3.0]


def test_collinear_overlap():
    m = portal()
    m.add_member("beam", "S", (3, 0, 3), (8, 0, 3), uid="B2")
    res = check_model(m)
    found = issues_of(res, "FRAME_OVERLAP")
    assert len(found) == 1 and set(found[0]["objects"]) == {"B1", "B2"}
    assert "2 m" in found[0]["message"]


# ----------------------------------------------------------- connectivity
def test_floating_frame_is_unconnected():
    m = portal()
    m.add_member("beam", "S", (20, 0, 3), (25, 0, 3), uid="BX")
    res = check_model(m)
    found = issues_of(res, "FRAME_UNCONNECTED")
    assert [f["objects"] for f in found] == [["BX"]]
    assert found[0]["severity"] == "error"


def test_unsupported_multi_object_part():
    m = portal()
    m.add_member("beam", "S", (20, 0, 3), (25, 0, 3), uid="BX")
    m.add_member("beam", "S", (25, 0, 3), (25, 5, 3), uid="BY")
    res = check_model(m)
    found = issues_of(res, "STRUCTURE_UNSUPPORTED_PART")
    assert len(found) == 1 and set(found[0]["objects"]) == {"BX", "BY"}
    assert not issues_of(res, "FRAME_UNCONNECTED")


def test_no_supports_at_all():
    m = portal()
    m.supports = [PointSupport((0.0, 0.0, 0.0), (0, 0, 0, 0, 0, 0))]
    res = check_model(m)
    assert [i["code"] for i in res["issues"]
            if i["severity"] == "error"] == ["NO_SUPPORTS"]


def test_story_without_support_path():
    m = portal()
    m.set_stories([3.0, 3.0])
    m.add_member("beam", "S", (0, 0, 6), (5, 0, 6), uid="B6")
    res = check_model(m)
    found = issues_of(res, "STORY_NO_SUPPORT_PATH")
    assert [f["objects"] for f in found] == [["Story2"]]
    assert issues_of(res, "FRAME_UNCONNECTED")[0]["objects"] == ["B6"]


def test_nodal_load_and_support_off_joint():
    m = portal()
    m.supports = [PointSupport((0.0, 0.0, 0.0), (1, 1, 1, 1, 1, 1)),
                  PointSupport((5.0, 0.0, 0.0), (1, 1, 1, 1, 1, 1)),
                  PointSupport((9.0, 9.0, 0.0), (1, 1, 1, 1, 1, 1))]
    p = m.pattern("L")
    p.nodal_loads.append(NodalLoad((1.0, 1.0, 3.0), fx=10.0))
    m.add_case("L", {"L": 1.0})
    res = check_model(m)
    found = issues_of(res, "JOINT_UNCONNECTED")
    assert {tuple(f["objects"]) for f in found} == {("support[2]",), ("L",)}
    assert all(f["severity"] == "error" for f in found)


# ------------------------------------------------------------------ shells
def test_warped_shell():
    m = box_frame()
    raw_shell(m, "S1", [(0, 0, 3), (6, 0, 3), (6, 5, 3), (0, 5, 3.05)])
    res = check_model(m)
    found = issues_of(res, "SHELL_WARPED")
    assert [f["objects"] for f in found] == [["S1"]]
    assert found[0]["location"] == [0.0, 5.0, 3.05]
    # validate()'s own planarity message is not duplicated as INVALID_DATA
    assert not issues_of(res, "INVALID_DATA")


def test_shell_aspect_ratio_warning_and_error():
    m = base_model(stories=None)
    m.supports = [PointSupport((0.0, 0.0, 0.0), (1, 1, 1, 1, 1, 1))]
    raw_shell(m, "A6", [(0, 0, 0), (12, 0, 0), (12, 2, 0), (0, 2, 0)])
    raw_shell(m, "A12", [(0, 10, 0), (25, 10, 0), (25, 12, 0), (0, 12, 0)])
    raw_shell(m, "OK", [(0, 20, 0), (4, 20, 0), (4, 22, 0), (0, 22, 0)])
    # shell behavior: element aspect (1 x 0.08 m elements) -> error
    raw_shell(m, "W", [(0, 30, 0), (4, 30, 0), (4, 30.08, 0), (0, 30.08, 0)],
              behavior="shell", mesh_size=1.0)
    res = check_model(m)
    ar = {f["objects"][0]: f["severity"]
          for f in issues_of(res, "SHELL_ASPECT_RATIO")}
    assert ar == {"A6": "warning", "A12": "error", "W": "error"}


def test_concave_self_intersecting_zero_area_and_duplicate_shells():
    m = base_model(stories=None)
    m.supports = [PointSupport((0.0, 0.0, 0.0), (1, 1, 1, 1, 1, 1))]
    raw_shell(m, "CC", [(0, 0, 0), (4, 0, 0), (1, 1, 0), (0, 4, 0)])
    raw_shell(m, "BT", [(10, 0, 0), (14, 0, 0), (10, 4, 0), (14, 4, 0)])
    raw_shell(m, "ZA", [(20, 0, 0), (24, 0, 0), (28, 0.0001, 0),
                        (22, 0.0001, 0)])
    raw_shell(m, "D1", [(30, 0, 0), (33, 0, 0), (33, 3, 0), (30, 3, 0)])
    raw_shell(m, "D2", [(33, 0, 0), (33, 3, 0), (30, 3, 0), (30, 0, 0)])
    res = check_model(m)
    assert issues_of(res, "SHELL_CONCAVE")[0]["objects"] == ["CC"]
    assert issues_of(res, "SHELL_CONCAVE")[0]["location"] == [1.0, 1.0, 0.0]
    assert [f["objects"] for f in issues_of(res, "SHELL_SELF_INTERSECTING")] \
        == [["BT"]]
    assert [f["objects"] for f in issues_of(res, "SHELL_ZERO_AREA")] \
        == [["ZA"]]
    dup = issues_of(res, "SHELL_DUPLICATE")
    assert len(dup) == 1 and set(dup[0]["objects"]) == {"D1", "D2"}


# ------------------------------------------------------------- references
def test_missing_sections_and_materials_all_reported():
    m = portal()
    m.sections["BAD"] = FrameSection.rectangular("BAD", "NOMAT", 0.3, 0.3)
    m.members[0].section = "NOSEC"
    m.members[1].section = "BAD"
    m.shell_sections["SL"].material = "GONE"
    raw_shell(m, "W1", [(0, 0, 0), (5, 0, 0), (5, 0, 3), (0, 0, 3)],
              kind="wall", behavior="shell", section="MISSING")
    with pytest.raises(ValueError):
        m.validate()                      # validate stops at the first
    res = check_model(m)
    assert issues_of(res, "FRAME_SECTION_MISSING")[0]["objects"] == ["C1"]
    assert {tuple(f["objects"]) for f in
            issues_of(res, "SECTION_MATERIAL_MISSING")} == {("BAD",), ("SL",)}
    assert issues_of(res, "SHELL_SECTION_MISSING")[0]["objects"] == ["W1"]


def test_invalid_data_fallback_and_duplicate_uid():
    m = portal()
    m.materials["C"].E = -1.0
    m.members.append(FrameMember("B1", "beam", "S", (0, 0, 1.5),
                                 (5, 0, 1.5)))
    res = check_model(m)
    inv = issues_of(res, "INVALID_DATA")
    assert any(i["objects"] == ["C"] and "E must be" in i["message"]
               for i in inv)
    assert issues_of(res, "DUPLICATE_UID")[0]["objects"] == ["B1"]


# ------------------------------------------------------------------- loads
def test_load_pattern_case_combo_issues():
    m = portal()
    m.pattern("EMPTY")
    full = m.pattern("DEAD", "dead")
    full.member_loads.append(MemberLoad("B1", w=10.0))
    full.member_loads.append(MemberLoad("NOPE", w=1.0))
    m.add_case("D", {"DEAD": 1.0, "EMPTY": 1.0})
    m.cases["X"] = LoadCase("X", {"GHOST": 1.0})
    m.combos["U"] = LoadCombo("U", {"D": 1.2, "MISSING": 1.6})
    res = check_model(m)
    assert issues_of(res, "PATTERN_EMPTY")[0]["objects"] == ["EMPTY"]
    assert issues_of(res, "CASE_EMPTY_PATTERN")[0]["objects"] == ["D", "EMPTY"]
    assert issues_of(res, "CASE_PATTERN_MISSING")[0]["objects"] == ["X",
                                                                    "GHOST"]
    assert issues_of(res, "COMBO_CASE_MISSING")[0]["objects"] == ["U",
                                                                  "MISSING"]
    assert issues_of(res, "LOAD_TARGET_MISSING")[0]["objects"] == ["DEAD",
                                                                   "NOPE"]
    assert res["summary"]["by_code"]["PATTERN_EMPTY"] == 1


def test_summary_counts_and_severity_order():
    m = portal()
    m.add_member("beam", "S", (20, 0, 3), (25, 0, 3), uid="BX")
    m.pattern("EMPTY")
    res = check_model(m)
    s = res["summary"]
    assert s["errors"] == 1 and s["warnings"] == 1
    assert [i["severity"] for i in res["issues"]] == ["error", "warning"]
    assert s["ok"] is False


# --------------------------------------------------------------- stability
def test_released_portal_mechanism_flags_roof_sway():
    m = portal(fixity="pinned", releases="Mi,Mj")
    res = stability_diagnostics(m)
    assert res["stable"] is False and res["n_mechanisms"] >= 1
    assert res["condition_number"] is None
    roof_ux = {tuple(d["point"]) for d in res["unstable_dofs"]
               if d["dof"] == "UX" and d["point"][2] == 3.0}
    assert roof_ux == {(0.0, 0.0, 3.0), (5.0, 0.0, 3.0)}
    mech = [i for i in res["issues"] if i["code"] == "STABILITY_MECHANISM"]
    assert len(mech) == res["n_mechanisms"]
    assert all(i["severity"] == "error" for i in mech)
    # every mechanism names a joint, DOF and its objects
    for mode in res["mechanisms"]:
        d = mode["dofs"][0]
        assert d["dof"] in ("UX", "UY", "UZ", "RX", "RY", "RZ")
        assert d["objects"]


def test_released_portal_without_diaphragm_also_unstable():
    m = portal(fixity="pinned", releases="Mi,Mj", rigid=False)
    res = stability_diagnostics(m)
    assert res["stable"] is False
    assert any(d["dof"] == "UX" and d["point"] == [5.0, 0.0, 3.0]
               for d in res["unstable_dofs"])


def test_fixed_models_have_no_mechanism():
    for m in (portal(), quick_building(bays_x=2, bays_y=2, stories=3)):
        res = stability_diagnostics(m)
        assert res["stable"] is True
        assert res["n_mechanisms"] == 0 and res["mechanisms"] == []
        assert res["condition_number"] > 1.0
        assert res["max_diagonal_ratio"] < 1e3
        assert not [i for i in res["issues"] if i["severity"] == "error"]


def test_pinned_frames_moment_beams_stable_planar_portal_not():
    # 3D box of moment frames on pinned bases: stable both ways
    m = box_frame()
    m.base_fixity = "pinned"
    res = stability_diagnostics(m)
    assert res["stable"] is True and res["n_mechanisms"] == 0
    # a single planar pinned portal is a mechanism OUT of its plane (UY)
    res = stability_diagnostics(portal(fixity="pinned"))
    assert res["stable"] is False
    top = [d for d in res["unstable_dofs"] if d["point"][2] == 3.0
           and d["dof"] in ("UX", "UY")]
    assert {d["dof"] for d in top} == {"UY"}


def test_ill_conditioning_reported_with_location():
    m = base_model(stories=None)
    m.materials["HARD"] = Material("HARD", E=3.0e16, nu=0.2,
                                   unit_weight=24.0)
    m.add_section(FrameSection.rectangular("H", "HARD", 0.4, 0.4))
    m.supports = [PointSupport((0.0, 0.0, 0.0), (1, 1, 1, 1, 1, 1))]
    m.add_member("beam", "S", (0, 0, 0), (4, 0, 0), uid="SOFT")
    m.add_member("beam", "H", (4, 0, 0), (8, 0, 0), uid="RIGID")
    res = stability_diagnostics(m)
    assert res["stable"] is True
    assert res["max_diagonal_ratio"] > 1e8
    assert res["max_diagonal_ratio_at"]["point"] == [8.0, 0.0, 0.0]
    assert res["max_diagonal_ratio_at"]["objects"] == ["RIGID"]
    ill = [i for i in res["issues"] if i["code"] == "STABILITY_ILL_CONDITIONED"]
    assert ill and ill[0]["severity"] == "warning"


def test_stability_size_cap_and_invalid_model():
    res = stability_diagnostics(portal(), max_dofs=5)
    assert res["skipped"] is True
    assert res["issues"][0]["code"] == "STABILITY_TOO_LARGE"
    assert STABILITY_MAX_DOFS == 3000
    m = portal()
    m.members[0].section = "NOSEC"
    res = stability_diagnostics(m)
    assert res["built"] is False
    assert res["issues"][0]["code"] == "STABILITY_BUILD_FAILED"


# --------------------------------------------------------------------- API
@pytest.fixture()
def client():
    from skyframe.api import server as srv
    app = srv.create_app()
    app.config["TESTING"] = True
    old = srv._state["model"]
    with app.test_client() as c:
        yield c, srv
    srv._state["model"] = old


def test_api_check_with_model_body_and_current_model(client):
    c, srv = client
    bad = portal()
    bad.add_member("beam", "S", (5, 0, 3), (0, 0, 3), uid="B2")
    d = bad.to_dict()
    d["members"][0]["section"] = "NOSEC"   # invalid -> lenient load
    r = c.post("/api/check", json={"model": d, "tolerance_m": 0.002})
    assert r.status_code == 200
    body = r.get_json()
    assert {"FRAME_DUPLICATE", "FRAME_SECTION_MISSING"} <= codes(body)
    assert body["summary"]["tolerance_m"] == 0.002
    assert set(body["issues"][0]) == {"severity", "code", "message",
                                      "objects", "location"}
    srv._state["model"] = portal()
    r = c.post("/api/check", json={})
    assert r.status_code == 200 and r.get_json()["summary"]["errors"] == 0
    assert c.post("/api/check", json={"tolerance_m": -1}).status_code == 400
    assert c.post("/api/check",
                  json={"model": "nope"}).status_code == 400


def test_api_check_stability(client):
    c, srv = client
    d = portal(fixity="pinned", releases="Mi,Mj").to_dict()
    r = c.post("/api/check/stability", json={"model": d})
    assert r.status_code == 200
    body = r.get_json()
    assert body["stable"] is False and body["n_mechanisms"] >= 1
    srv._state["model"] = portal()
    body = c.post("/api/check/stability", json={}).get_json()
    assert body["stable"] is True
    assert c.post("/api/check/stability",
                  json={"max_dofs": 0}).status_code == 400
