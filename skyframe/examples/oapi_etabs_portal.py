"""ETABS-style OAPI script running on SkyFrame (docs/API.md).

Everything below the import line is plain ETABS OAPI Python: start the
program through the helper, build a one-bay portal frame (kN, m), assign
loads, run, and read joint displacements, base reactions and frame forces.
On Windows with ETABS, replace the first import by ``import comtypes.client``
and the same script drives ETABS.
"""
from skyframe.oapi import comtypes          # was: import comtypes.client

helper = comtypes.client.CreateObject('ETABSv1.Helper')
helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
myETABSObject = helper.CreateObjectProgID("CSI.ETABS.API.ETABSObject")
myETABSObject.ApplicationStart()
SapModel = myETABSObject.SapModel

ret = SapModel.InitializeNewModel()
ret = SapModel.File.NewBlank()
kN_m_C = 6
ret = SapModel.SetPresentUnits(kN_m_C)

# material + sections
ret = SapModel.PropMaterial.SetMaterial('STEEL', 1)
ret = SapModel.PropMaterial.SetMPIsotropic('STEEL', 200e6, 0.3, 1.17e-5)
ret = SapModel.PropFrame.SetISection('COL', 'STEEL', 0.30, 0.30, 0.015, 0.010, 0.30, 0.015)
ret = SapModel.PropFrame.SetRectangle('BEAM', 'STEEL', 0.50, 0.30)

# geometry: two 4 m columns, one 6 m beam
H, L = 4.0, 6.0
[Col1, ret] = SapModel.FrameObj.AddByCoord(0, 0, 0, 0, 0, H, '', 'COL', 'C1', 'Global')
[Col2, ret] = SapModel.FrameObj.AddByCoord(L, 0, 0, L, 0, H, '', 'COL', 'C2', 'Global')
[Beam, ret] = SapModel.FrameObj.AddByCoord(0, 0, H, L, 0, H, '', 'BEAM', 'B1', 'Global')

# fixed bases
[Base1, Top1, ret] = SapModel.FrameObj.GetPoints(Col1, '', '')
[Base2, Top2, ret] = SapModel.FrameObj.GetPoints(Col2, '', '')
for p in (Base1, Base2):
    ret = SapModel.PointObj.SetRestraint(p, [True] * 6)

# loads: gravity UDL on the beam, lateral point load at the left top joint
ret = SapModel.LoadPatterns.Add('DEAD', 1, 0, True)
ret = SapModel.LoadPatterns.Add('WIND', 6, 0, True)
ret = SapModel.FrameObj.SetLoadDistributed(Beam, 'DEAD', 1, 10, 0, 1, 20.0, 20.0)
ret = SapModel.PointObj.SetLoadForce(Top1, 'WIND', [50.0, 0, 0, 0, 0, 0])
ret = SapModel.RespCombo.Add('1.2D+1.6W', 0)
ret = SapModel.RespCombo.SetCaseList('1.2D+1.6W', 0, 'DEAD', 1.2)
ret = SapModel.RespCombo.SetCaseList('1.2D+1.6W', 0, 'WIND', 1.6)

ret = SapModel.Analyze.RunAnalysis()

ret = SapModel.Results.Setup.DeselectAllCasesAndCombosForOutput()
ret = SapModel.Results.Setup.SetCaseSelectedForOutput('WIND')
[NumberResults, Obj, Elm, LoadCase, StepType, StepNum,
 U1, U2, U3, R1, R2, R3, ret] = SapModel.Results.JointDispl(Top1, 0)
drift = U1[0]

[NumberResults, LoadCase, StepType, StepNum, Fx, Fy, Fz, Mx, My, Mz,
 gx, gy, gz, ret] = SapModel.Results.BaseReact()
base_fx = Fx[0]

ret = SapModel.Results.Setup.DeselectAllCasesAndCombosForOutput()
ret = SapModel.Results.Setup.SetCaseSelectedForOutput('DEAD')
ret = SapModel.Results.Setup.SetComboSelectedForOutput('1.2D+1.6W')
[NumberResults, Obj, ObjSta, Elm, ElmSta, LoadCase, StepType, StepNum,
 P, V2, V3, T, M2, M3, ret] = SapModel.Results.FrameForce(Beam, 0)

if __name__ == "__main__":
    print(f"lateral drift (WIND) = {drift:.6e} m, base Fx = {base_fx:.3f} kN")
    for case, x, m3 in zip(LoadCase, ObjSta, M3):
        print(f"{case:>10s}  x = {x:5.2f} m  M3 = {m3:9.3f} kN*m")
    ret = myETABSObject.ApplicationExit(False)
