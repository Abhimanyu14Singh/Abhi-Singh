# SkyFrame programmatic API

SkyFrame has three ways in for scripts and other programs:

| Layer | Use it for | Where |
|---|---|---|
| REST API `/api/v1/…` + OpenAPI 3 | other languages, services, plugins | `skyframe/api/openapi.py`, `GET /api/openapi.json` |
| OAPI facade `skyframe.oapi` | porting ETABS / SAP2000 Python scripts | `skyframe/oapi/` |
| Native Python `skyframe.client` | new Python scripts | `docs/PUBLIC_API.md` |

All three run the same engine, so a model gives the same numbers whichever
way it is sent.

## 1. REST API (versioned)

* Every endpoint is served under `/api/v1/<path>`. The old unversioned
  `/api/<path>` stays as an alias, and the web UI uses it. The alias is a
  path rewrite in front of Flask, so the two can never drift apart.
* `GET /api/openapi.json` (or `/api/v1/openapi.json`) returns an
  **OpenAPI 3.0.3** document generated from the live route table at
  request time, so plugin routes are included. Each operation has:
  * an `operationId` and a summary from the view's docstring;
  * the endpoint's CONTRACT.md text as its `description`;
  * path parameters taken from the route converters;
  * request and response schemas:
    * exact component schemas (`Model`, `AnalysisResults`, `CaseResults`,
      `ModalResults`, `Error`, `ExportResponse`, `ImportResponse`,
      `Plugin` …) for the core and interop endpoints;
    * for every other endpoint, the `{key, key?, …}` shapes parsed from
      CONTRACT.md (`?` marks an optional key).
* Errors: `400 {"error": "…"}`, `404` for an unknown item. Endpoints with
  `soft_errors: true` instead return `200 {"ok": false, "error"}`.
* Units are SI everywhere: kN, m, tonne, s, kPa.

The spec passes the official `openapi-spec-validator` and a built-in
structural check (`skyframe.api.openapi.validate_spec`) in
`tests/test_api_openapi.py`.

```bash
curl -s localhost:8600/api/v1/model/quick -H 'Content-Type: application/json' -d '{"stories": 3}'
curl -s -X POST localhost:8600/api/v1/analyze | jq '.cases.DEAD.base'
curl -s localhost:8600/api/openapi.json | jq '.paths | keys | length'
```

## 2. OAPI-compatible facade (`skyframe.oapi`)

The facade copies the CSI OAPI as seen from Python through `comtypes`:

* the same object tree (`SapModel.File`, `PropMaterial`, `PropFrame` …),
  method names and argument order;
* integer return codes (`0` = success);
* `ByRef` outputs returned as `[out1, …, outN, ret]`, with `ret` last as
  modern comtypes does it.

### Porting a script: change one line

```python
import comtypes.client                     # before (Windows + ETABS/SAP2000)
from skyframe.oapi import comtypes         # after  (SkyFrame)
```

`comtypes.client.CreateObject('ETABSv1.Helper')`, `helper.QueryInterface(
comtypes.gen.ETABSv1.cHelper)`, `CreateObjectProgID(...)`, `GetObject`,
`ApplicationStart` / `ApplicationExit` and `comtypes.COMError` all resolve
to SkyFrame. The SAP2000 names (`SAP2000v1.Helper`,
`CSI.SAP2000.API.SapObject`) work too.

`tests/data/oapi_sap2000_example2.py.txt` is CSI's SAP2000 OAPI "Example 2"
Python script. It runs with only its import line changed. The seven
displacements it compares against CSI's independent hand results
(-0.02639, 0.06296, 0.06296, -0.2963, 0.3125, 0.11556, 0.00651 in) all
match to better than 0.05 % (largest difference 0.021 %, on case 7).

You can also start without comtypes: `from skyframe.oapi import SapModel;
sm = SapModel()`.

### Supported surface

| Object | Methods |
|---|---|
| `SapModel` | `InitializeNewModel(Units)`, `SetPresentUnits` / `GetPresentUnits`, `GetDatabaseUnits`, `SetModelIsLocked` / `GetModelIsLocked`, `GetModelFilename`, `GetVersion` |
| `File` | `NewBlank`, `NewGridOnly(NumberStorys, TypicalStoryHeight, BottomStoryHeight, …)`, `Save(FileName)`, `OpenFile(FileName)`, `GetFilePath` |
| `PropMaterial` | `SetMaterial(Name, MatType)`, `AddMaterial`, `SetMPIsotropic`, `GetMPIsotropic`, `SetWeightAndMass`, `GetWeightAndMass`, `SetOSteel_1`, `SetOConcrete_1`, `SetORebar_1`, `GetNameList`, `Count`, `Delete` |
| `PropFrame` | `SetRectangle`, `GetRectangle`, `SetCircle`, `SetPipe`, `SetTube`, `SetISection`, `SetChannel`, `SetTee`, `SetAngle`, `SetGeneral`, `ImportProp` (AISC16 / Euro / BS / AU-NZ / Indian XML names), `SetModifiers`, `GetModifiers`, `GetSectProps`, `GetNameList`, `Count`, `Delete` |
| `PropArea` | `SetShell_1` (SAP2000), `SetWall`, `SetSlab` (ETABS), `SetModifiers`, `GetNameList`, `Count` |
| `PointObj` | `AddCartesian`, `GetCoordCartesian`, `SetRestraint`, `GetRestraint`, `DeleteRestraint`, `SetSpring`, `SetMass`, `SetLoadForce`, `DeleteLoadForce`, `SetDiaphragm`, `SetGroupAssign`, `GetNameList`, `Count` |
| `FrameObj` | `AddByCoord`, `AddByPoint`, `GetPoints`, `SetSection`, `GetSection`, `SetLocalAxes`, `GetLocalAxes`, `SetReleases` (with partial fixity), `SetLoadDistributed`, `SetLoadPoint`, `DeleteLoadDistributed`, `DeleteLoadPoint`, `SetGroupAssign`, `Delete`, `GetNameList`, `GetNameListOnStory`, `GetLabelFromName`, `Count` |
| `AreaObj` | `AddByCoord`, `AddByPoint`, `GetPoints`, `SetProperty`, `SetLoadUniform`, `SetDiaphragm`, `SetAutoMesh`, `SetGroupAssign`, `Delete`, `GetNameList`, `Count` |
| `Story` | `SetStories`, `SetStories_2`, `GetStories`, `GetNameList`, `GetElevation`, `GetHeight` |
| `Diaphragm` / `GroupDef` | `SetDiaphragm`, `GetNameList` / `SetGroup`, `GetNameList`, `Delete` |
| `LoadPatterns` | `Add(Name, MyType, SelfWTMultiplier, AddAnalysisCase)`, `Delete`, `SetSelfWTMultiplier`, `GetSelfWTMultiplier`, `SetLoadType`, `GetLoadType`, `GetNameList`, `Count` |
| `LoadCases` | `StaticLinear.SetCase` / `SetLoads` / `GetLoads`, `ModalEigen.SetCase` / `SetNumberModes` / `GetNumberModes`, `GetNameList`, `GetTypeOAPI`, `Delete`, `Count` |
| `RespCombo` | `Add(Name, ComboType 0..4)`, `SetCaseList`, `GetCaseList`, `Delete`, `GetNameList`, `Count` |
| `Analyze` | `RunAnalysis`, `CreateAnalysisModel`, `SetRunCaseFlag`, `GetCaseStatus`, `SetActiveDOF`, `GetActiveDOF`, `SetSolverOption_*` (accepted, no effect) |
| `Results.Setup` | `DeselectAllCasesAndCombosForOutput`, `SetCaseSelectedForOutput`, `SetComboSelectedForOutput`, `Get…SelectedForOutput`, `SetOption*` |
| `Results` | `JointDispl`, `JointDisplAbs`, `JointReact`, `FrameForce`, `BaseReact`, `ModalPeriod`, `ModalParticipatingMassRatios`, `StoryDrifts` |
| `View` / `SelectObj` | `RefreshView`, `RefreshWindow` / `ClearSelection`, `All` (no-ops) |

Units follow CSI `eUnits` 1…16 (lb_in_F … Ton_cm_C). Inputs are converted
from the present units and results are converted back. The thermal
coefficient follows the temperature unit.

Load directions are the CSI ones:

* `Dir` 1–3 are local. Local 1 and 3 have no native SkyFrame direction, so
  they are resolved exactly into global components.
* 4–6 are global X, Y, Z; 7–9 are the projected X, Y, Z; 10 is gravity; 11
  is projected gravity.

Releases U1…R3 map to P V2 V3 T M2 M3, with partial-fixity springs.

`SapModel.to_skyframe()` returns the `BuildingModel` that `RunAnalysis`
solves, and `SapModel.skyframe_results` returns its `AnalysisResults`. If
you post `to_skyframe().to_dict()` to `POST /api/v1/model` and then call
`POST /api/v1/analyze`, you get exactly the facade's numbers (tested on
`examples/oapi_etabs_portal.py`).

### Deviations from CSI (by design or not yet supported)

* **Output selection.** Every case and combo is selected until the first
  `DeselectAllCasesAndCombosForOutput` or `Set…SelectedForOutput` call.
  CSI starts with nothing selected.
* **New models.** `NewBlank` creates the `MODAL` case (12 modes) but no
  load pattern. When there is no restraint at all, SkyFrame's automatic
  fixity at the lowest level applies, where CSI would report an unstable
  model.
* **Default materials.** `SetMaterial` uses the CSI US defaults: steel
  29000 ksi and 490 pcf; concrete 3605 ksi and 150 pcf.
* **Mass.** Mass comes from element self-mass
  (`mass_source_mode = "element_self_mass"`) plus joint masses.
* **Diaphragms.** A rigid diaphragm assigned to points or areas of a story
  becomes that story's rigid diaphragm. A semi-rigid diaphragm is the slab
  itself.
* **Element results.** `Elm` / `ElmSta` repeat the object name and station.
  Envelope combos return `Max` / `Min` rows. Modal displacements use
  `StepType = "Mode"`.
* **Distributed moments.** `SetLoadDistributed` with `MyType = 2`
  (distributed moments) returns 1.
* **Other files.** `File.Save` writes SkyFrame JSON, whatever the extension
  (`.sdb`, `.edb` …). `OpenFile` reads it back, or reads any SkyFrame
  model; a plain model's member and area loads are not re-registered.
* **Not provided.** COM events, the display and design APIs, and
  nonlinear, RS, TH and pushover case setters are not provided. Use
  `skyframe.client` or the REST API for those.
