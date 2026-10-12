"""OAPI-compatible facade (skyframe.oapi) -- CONTRACT "Programmatic API:
OAPI facade, /api/v1 and OpenAPI".

* the CSI SAP2000 OAPI "Example 2" Python script runs with ONLY its import
  line changed and reproduces the seven independent (hand) results of the
  CSI verification to < 0.05 %;
* an ETABS-style portal-frame script gives exactly the REST results of the
  same model, and the closed-form sway stiffness;
* return codes, comtypes [byref..., ret] lists, unit conversion, Save /
  OpenFile, stories and areas.
"""

import math
import os
import runpy

import pytest

from skyframe.oapi import SapModel as new_sap_model
from skyframe.oapi import comtypes as oapi_comtypes

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)


def _run_script(path, cwd):
    old = os.getcwd()
    os.chdir(cwd)
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert src.count("import comtypes.client\n") == 1
        src = src.replace("import comtypes.client\n",
                          "from skyframe.oapi import comtypes\n", 1)
        ns = {"__name__": "__main__"}
        exec(compile(src, path, "exec"), ns)
        return ns
    finally:
        os.chdir(old)


def test_sap2000_example2_runs_unchanged_but_import(tmp_path, capsys):
    ns = _run_script(os.path.join(HERE, "data",
                                  "oapi_sap2000_example2.py.txt"), tmp_path)
    sap, ind = ns["SapResult"], ns["IndResult"]
    # the independent results of CSI's verification (4 significant digits)
    for got, want in zip(sap, ind):
        assert got == pytest.approx(want, rel=5e-4), (sap, ind)
    # File.Save wrote the model where the script asked
    assert os.path.isfile(os.path.join(str(tmp_path), ns["ModelPath"]))
    assert "0.3125" in capsys.readouterr().out


def _rect_props(b, h, E, nu):
    A = b * h
    I = b * h ** 3 / 12.0
    return A, I, E / (2 * (1 + nu)), 5.0 / 6.0 * A


def test_etabs_portal_closed_form_and_rest(tmp_path):
    ns = runpy.run_path(os.path.join(ROOT, "examples",
                                     "oapi_etabs_portal.py"))
    SapModel = ns["SapModel"]
    # ---- equilibrium: base Fx balances the 50 kN lateral load
    assert ns["base_fx"] == pytest.approx(-50.0, abs=1e-9)
    # ---- REST: the same model through POST /api/model + /api/analyze
    os.environ.setdefault("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    c = create_app().test_client()
    model = SapModel.to_skyframe()
    assert c.post("/api/model", json=model.to_dict()).status_code == 200
    rest = c.post("/api/analyze").get_json()
    res = SapModel.skyframe_results.to_dict()
    assert rest["cases"]["WIND"]["base"] == res["cases"]["WIND"]["base"]
    assert rest["cases"]["DEAD"]["member_stations"] == \
        res["cases"]["DEAD"]["member_stations"]
    # facade JointDispl == REST node_disp of that joint
    tag = next(t for t, xyz in rest["nodes"].items()
               if xyz == [0.0, 0.0, 4.0])
    assert ns["drift"] == rest["cases"]["WIND"]["node_disp"][tag][0]
    # facade FrameForce rows == REST stations (DEAD rows first)
    st = rest["cases"]["DEAD"]["member_stations"]["B1"]
    n = len(st["x"])
    assert ns["M3"][:n] == st["M3"] and ns["ObjSta"][:n] == st["x"]
    combo = rest["combos"]["1.2D+1.6W"]["member_stations"]["B1"]["M3"]
    assert ns["M3"][n:] == pytest.approx(combo, rel=1e-12, abs=1e-9)
    assert set(ns["LoadCase"]) == {"DEAD", "1.2D+1.6W"}


def test_portal_sway_closed_form():
    """Rigid-beam portal: drift = H / (2 k), k = 12EI/h^3 / (1 + phi),
    phi = 12 E I / (G As h^2) (CSI rectangle As = 5/6 A)."""
    sm = new_sap_model()
    sm.InitializeNewModel(6)
    sm.File.NewBlank()
    E, nu, b, h, Hh = 30e6, 0.2, 0.4, 0.4, 3.5
    assert sm.PropMaterial.SetMaterial("C", 2) == 0
    assert sm.PropMaterial.SetMPIsotropic("C", E, nu, 1e-5) == 0
    assert sm.PropFrame.SetRectangle("COL", "C", h, b) == 0
    assert sm.PropFrame.SetRectangle("RIGID", "C", 1.0, 1.0) == 0
    # axially rigid columns: no overturning shortening in the closed form
    assert sm.PropFrame.SetModifiers("COL", [1e6, 1, 1, 1, 1, 1, 1, 1])[-1] \
        == 0
    [_v, ret] = sm.PropFrame.SetModifiers("RIGID",
                                          [1e4, 1e4, 1e4, 1, 1, 1e6, 1, 1])
    assert ret == 0
    [c1, r1] = sm.FrameObj.AddByCoord(0, 0, 0, 0, 0, Hh, "", "COL")
    [c2, r2] = sm.FrameObj.AddByCoord(5, 0, 0, 5, 0, Hh, "", "COL")
    [bm, r3] = sm.FrameObj.AddByCoord(0, 0, Hh, 5, 0, Hh, "", "RIGID")
    assert (r1, r2, r3) == (0, 0, 0) and (c1, c2, bm) == ("1", "2", "3")
    for f in (c1, c2):
        p1, _p2, ret = sm.FrameObj.GetPoints(f)
        assert sm.PointObj.SetRestraint(p1, [True] * 6)[-1] == 0
    _a, top, _ = sm.FrameObj.GetPoints(c1)
    assert sm.LoadPatterns.Add("LAT", 8, 0, True) == 0
    assert sm.LoadPatterns.Add("LAT", 8) == 1           # duplicate name
    sm.PointObj.SetLoadForce(top, "LAT", [100.0, 0, 0, 0, 0, 0])
    assert sm.Analyze.RunAnalysis() == 0
    sm.Results.Setup.DeselectAllCasesAndCombosForOutput()
    assert sm.Results.Setup.SetCaseSelectedForOutput("LAT") == 0
    out = sm.Results.JointDispl(top, 0)
    assert out[0] >= 1 and out[-1] == 0
    A, I, G, As = _rect_props(b, h, E, nu)
    phi = 12 * E * I / (G * As * Hh ** 2)
    k = 12 * E * I / Hh ** 3 / (1 + phi)
    assert out[6][0] == pytest.approx(100.0 / (2 * k), rel=1e-4)
    # column end moments ~ V h / 2 (contraflexure at mid height)
    ff = sm.Results.FrameForce(c1, 0)
    m3 = ff[13]
    assert abs(m3[0]) == pytest.approx(50.0 * Hh / 2, rel=2e-3)
    assert ff[9][0] == pytest.approx(-50.0, rel=2e-3) or \
        ff[9][0] == pytest.approx(50.0, rel=2e-3)
    br = sm.Results.BaseReact()
    assert br[4][0] == pytest.approx(-100.0, rel=1e-9)
    jr = sm.Results.JointReact(sm.FrameObj.GetPoints(c2)[0], 0)
    assert jr[6][0] == pytest.approx(-50.0, rel=2e-3)


def test_units_and_return_conventions():
    sm = new_sap_model()
    sm.InitializeNewModel(6)                       # kN-m
    sm.PropMaterial.SetMaterial("S", 1)
    sm.PropFrame.SetRectangle("R", "S", 0.5, 0.3)
    [n, ret] = sm.FrameObj.AddByCoord(0, 0, 0, 6, 0, 0, "", "R", "B")
    assert (n, ret) == ("B", 0)
    assert sm.FrameObj.AddByCoord(0, 0, 0, 6, 0, 0, "", "R", "B")[-1] == 1
    assert sm.FrameObj.AddByCoord(0, 0, 0, 1, 0, 0, "", "NOPE")[-1] == 1
    assert sm.FrameObj.SetSection("missing", "R") == 1
    [num, names, ret] = sm.FrameObj.GetNameList()
    assert (num, names, ret) == (1, ["B"], 0)
    # coordinates come back in the present units
    sm.SetPresentUnits(3)                          # kip-in
    p1, p2, _ = sm.FrameObj.GetPoints("B")
    x, y, z, ret = sm.PointObj.GetCoordCartesian(p2)
    assert x == pytest.approx(6 / 0.0254, rel=1e-12) and ret == 0
    assert sm.GetPresentUnits() == 3 and sm.GetDatabaseUnits() == 6
    assert sm.SetPresentUnits(99) == 1
    # comtypes shim: helper / app object / GetActiveObject
    h = oapi_comtypes.client.CreateObject("ETABSv1.Helper")
    app = h.QueryInterface(oapi_comtypes.gen.ETABSv1.cHelper) \
        .CreateObjectProgID("CSI.ETABS.API.ETABSObject")
    assert app.ApplicationStart() == 0 and app.SapModel.File.NewBlank() == 0
    assert oapi_comtypes.client.GetActiveObject("x").SapModel is not None


def test_units_kip_in_same_answer():
    """The portal built in kip-in gives the kN-m displacement converted."""
    def build(u, L, F):
        sm = new_sap_model()
        sm.InitializeNewModel(u)
        sm.PropMaterial.SetMaterial("S", 1)
        sm.PropMaterial.SetMPIsotropic("S", 200e6 * L ** 2 / F,
                                       0.3, 1.17e-5 / (1.8 if u == 3
                                                       else 1.0))
        sm.PropFrame.SetRectangle("R", "S", 0.4 / L, 0.3 / L)
        a = sm.FrameObj.AddByCoord(0, 0, 0, 0, 0, 3 / L, "", "R")[0]
        b = sm.FrameObj.AddByCoord(0, 0, 3 / L, 4 / L, 0, 3 / L, "", "R")[0]
        sm.PointObj.SetRestraint(sm.FrameObj.GetPoints(a)[0], [True] * 6)
        sm.LoadPatterns.Add("P", 8, 0, True)
        sm.FrameObj.SetLoadDistributed(b, "P", 1, 10, 0, 1, 5.0 * L / F,
                                       5.0 * L / F)
        sm.Analyze.RunAnalysis()
        sm.Results.Setup.DeselectAllCasesAndCombosForOutput()
        sm.Results.Setup.SetCaseSelectedForOutput("P")
        tip = sm.FrameObj.GetPoints(b)[1]
        return sm.Results.JointDispl(tip, 0)[8][0]
    kip, inch = 4.4482216152605, 0.0254
    si = build(6, 1.0, 1.0)
    us = build(3, inch, kip)
    assert us * inch == pytest.approx(si, rel=1e-9)
    # tip of the beam cantilevered from the column top moves down
    assert si < 0


def test_save_open_roundtrip_and_stories(tmp_path):
    sm = new_sap_model()
    sm.InitializeNewModel(6)
    sm.File.NewGridOnly(2, 3.0, 4.0, 2, 2, 6.0, 6.0)
    [n, names, el, hs, *_rest, ret] = sm.Story.GetStories()
    assert names == ["Base", "Story1", "Story2"] and el == [0.0, 4.0, 7.0]
    sm.PropMaterial.SetMaterial("C", 2)
    sm.PropFrame.SetRectangle("C40", "C", 0.4, 0.4)
    sm.PropArea.SetSlab("SL", 0, 1, "C", 0.2)
    for z0, z1 in ((0, 4), (4, 7)):
        for x in (0, 6):
            for y in (0, 6):
                f = sm.FrameObj.AddByCoord(x, y, z0, x, y, z1, "", "C40")[0]
                if z0 == 0:
                    sm.PointObj.SetRestraint(sm.FrameObj.GetPoints(f)[0],
                                             [True] * 6)
    X, Y, Z = [0, 6, 6, 0], [0, 0, 6, 6], [4, 4, 4, 4]
    [X, Y, Z, area, ret] = sm.AreaObj.AddByCoord(4, X, Y, Z, "", "SL")
    assert ret == 0 and area == "1"
    sm.LoadPatterns.Add("SDL", 2, 0, True)
    assert sm.AreaObj.SetLoadUniform(area, "SDL", 3.0, 10) == 0
    sm.Diaphragm.SetDiaphragm("D1", False)
    assert sm.AreaObj.SetDiaphragm(area, "D1") == 0
    assert sm.Analyze.RunAnalysis() == 0
    sm.Results.Setup.DeselectAllCasesAndCombosForOutput()
    sm.Results.Setup.SetCaseSelectedForOutput("SDL")
    fz = sm.Results.BaseReact()[6][0]
    assert fz == pytest.approx(3.0 * 36.0, rel=1e-9)   # q * area
    m = sm.to_skyframe()
    assert m.story_diaphragm == {"Story1": "rigid", "Story2": "none"}
    assert {mm.story for mm in m.members} == {"Story1", "Story2"}
    drifts = sm.Results.StoryDrifts()
    assert drifts[0] == 0 or drifts[-1] == 0
    path = str(tmp_path / "m.sdb")
    assert sm.File.Save(path) == 0
    sm2 = new_sap_model()
    assert sm2.File.OpenFile(path) == 0
    assert sm2.Analyze.RunAnalysis() == 0
    sm2.Results.Setup.DeselectAllCasesAndCombosForOutput()
    sm2.Results.Setup.SetCaseSelectedForOutput("SDL")
    assert sm2.Results.BaseReact()[6][0] == fz
    assert sm2.to_skyframe().to_dict() == m.to_dict()


def test_modal_and_releases():
    sm = new_sap_model()
    sm.InitializeNewModel(6)
    sm.PropMaterial.SetMaterial("S", 1)
    sm.PropFrame.SetISection("W", "S", 0.4, 0.2, 0.015, 0.01, 0.2, 0.015)
    a = sm.FrameObj.AddByCoord(0, 0, 0, 0, 0, 3, "", "W")[0]
    b = sm.FrameObj.AddByCoord(0, 0, 3, 5, 0, 3, "", "W")[0]
    c = sm.FrameObj.AddByCoord(5, 0, 3, 5, 0, 0, "", "W")[0]
    for f in (a, c):
        sm.PointObj.SetRestraint(sm.FrameObj.GetPoints(f)[0], [True] * 6)
    rel = sm.FrameObj.SetReleases(b, [False] * 5 + [True],
                                  [False] * 5 + [True], [0] * 6, [0] * 6)
    assert rel[-1] == 0
    sm.LoadPatterns.Add("G", 1, 1.0, True)
    sm.LoadCases.ModalEigen.SetNumberModes("MODAL", 3)
    assert sm.Analyze.RunAnalysis() == 0
    per = sm.Results.ModalPeriod()
    assert per[0] == 3 and all(t > 0 for t in per[4])
    assert per[5][0] == pytest.approx(1 / per[4][0])
    mp = sm.Results.ModalParticipatingMassRatios()
    assert mp[0] == 3
    # pinned-pinned beam (both M3 released): midspan M3 = w L^2 / 8 of
    # its self weight, end moments zero
    sm.Results.Setup.DeselectAllCasesAndCombosForOutput()
    sm.Results.Setup.SetCaseSelectedForOutput("G")
    ff = sm.Results.FrameForce(b, 0)
    m3 = ff[13]
    mdl = sm.to_skyframe()
    w = mdl.sections["W"].A * mdl.materials["S"].unit_weight
    assert abs(m3[0]) < 1e-6 and abs(m3[-1]) < 1e-6
    assert max(m3) == pytest.approx(w * 25 / 8, rel=2e-4)
