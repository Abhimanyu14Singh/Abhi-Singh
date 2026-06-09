# ETABS Connector

`etabs_connector.py` is the only file that talks to ETABS. Everything else in
the dashboard reads from the `dcc.Store` dict that this module produces.

---

## Connection

### `get_etabs_model() → (SapModel | None, error_msg | None)`

Attempts connection in two ways:

**Attempt 1 — ETABSv1 Python module**

CSI ships a `ETABSv1.py` wrapper with ETABS v23 at:

```
C:\Program Files\Computers and Structures\ETABS 23\ETABSv1.py
```

The connector adds that path to `sys.path` and calls:

```python
helper = ETABSv1.cHelper(ETABSv1.Helper())
myETABS = helper.GetObject("CSI.ETABS.API.ETABSObject")
_sap_model = myETABS.SapModel
```

**Attempt 2 — comtypes fallback**

If the ETABSv1 module is missing (older install or different path):

```python
import comtypes.client
helper = comtypes.client.CreateObject("ETABSv1.Helper")
import comtypes.gen.ETABSv1 as ETABSv1
helper = helper.QueryInterface(ETABSv1.cHelper)
myETABS = helper.GetObject("CSI.ETABS.API.ETABSObject")
_sap_model = myETABS.SapModel
```

Both paths use the Windows COM ProgID `"CSI.ETABS.API.ETABSObject"` — they
connect to the ETABS instance that is already running, not launch a new one.

The `SapModel` object is also stored in a module-level `_sap_model` variable
so `get_frame_forces()` can use it for on-demand calls after ATTACH.

### Prerequisites

- ETABS v23 must be **open** (not just installed).
- A `.edb` model must be **loaded** in ETABS.
- The **analysis must have been run** (`Analyze → Run All`) — result requests
  return zero rows if no results are in memory.
- The dashboard process and ETABS must run as the **same Windows user**.

---

## Data Extraction

### `extract_all_data(SapModel) → dict`

Calls all private extraction functions and assembles the result dict:

```python
data = {
    "model_info":    _extract_model_info(SapModel),
    "stories":       _extract_stories(SapModel),
    "joints":        _extract_joints(SapModel),
    "frames":        _extract_frames(SapModel),
    "shells":        _extract_shells(SapModel),
    "load_cases":    _extract_load_cases(SapModel),
    "load_patterns": _extract_load_patterns(SapModel),
    "load_combos":   _extract_load_combos(SapModel),
    "results":       _extract_all_results(SapModel),
    "status":        "ok",
}
```

If any top-level exception is raised, `status` is set to
`"error: <full traceback>"` and the error is displayed in the toast.

---

## Extracted Fields Reference

### Model Info — `SapModel.*`

| Field | ETABS API call | Notes |
|---|---|---|
| `filename` | `SapModel.GetModelFilename(False)` | Basename only |
| `units` | `SapModel.GetPresentUnits()` | Mapped via `UNITS_MAP` |
| `num_joints` | `SapModel.PointObj.GetNameList()` | `[1]` element of return tuple |
| `num_frames` | `SapModel.FrameObj.GetNameList()` | |
| `num_shells` | `SapModel.AreaObj.GetNameList()` | |
| `num_stories` | `SapModel.Story.GetNameList()` | |
| `num_load_cases` | `SapModel.LoadCases.GetNameList()` | |
| `num_load_combos` | `SapModel.LoadCombos.GetNameList()` | |

### Units Map

ETABS returns an integer code from `GetPresentUnits()`. The mapping:

| Code | String |
|---|---|
| 1 | lb, in, °F |
| 2 | lb, ft, °F |
| 3 | kip, in, °F |
| 4 | kip, ft, °F |
| 5 | kN, mm, °C |
| 6 | kN, m, °C |
| 7 | kgf, mm, °C |
| 8 | kgf, m, °C |
| 9 | N, mm, °C |
| 10 | N, m, °C |
| 11 | Tonf, mm, °C |
| 12 | Tonf, m, °C |
| 13 | kN, cm, °C |
| 14 | kgf, cm, °C |
| 15 | N, cm, °C |
| 16 | Tonf, cm, °C |

### Stories — `SapModel.Story.*`

Per story: `GetElevation(name)`, `GetHeight(name)`.

Fields stored: `name`, `elevation`, `height` (all rounded to 4 decimal places).

### Joints — `SapModel.PointObj.*`

All joint names from `GetNameList()`, then for each:
`GetCoordCartesian(name)` → `x, y, z`.

Fields stored: `name`, `x`, `y`, `z`.

### Frames — `SapModel.FrameObj.*`

All frame names, then for each:
- `GetPoints(name)` → `point_i`, `point_j` (joint names)
- `GetSection(name)` → `section` property name

End coordinates (`xi/yi/zi`, `xj/yj/zj`) are looked up from the joint coordinate
table built during joint extraction (avoids a second API call per frame).

### Shells — `SapModel.AreaObj.*`

All area object names, then for each:
`GetPoints(name)` → list of corner joint names.

Corner coordinates are looked up from the same joint table.

### Load Cases — `SapModel.LoadCases.GetNameList()`

Returns a flat list of strings: `["DEAD", "LIVE", "EX", "EY", "MODAL", ...]`

### Load Patterns — `SapModel.LoadPatterns.*`

Per pattern: `GetLoadType(name)` → integer type code, `GetSelfWtMultiplier(name)`.

Type codes are mapped via `LOAD_TYPE_MAP`:
`1→Dead, 2→Super Dead, 3→Live, 4→Roof Live, 5→Snow, 6→Wind, 7→Seismic, ...`

### Load Combinations — `SapModel.LoadCombos.GetNameList()`

Returns a flat list of combo names: `["1.2D+1.6L", "0.9D+1.0EX", ...]`

---

## Results Extraction

Before extracting results, all load cases and combos are selected for output:

```python
SapModel.Results.Setup.DeselectAllCasesAndCombosForOutput()
for c in cases:
    SapModel.Results.Setup.SetCaseSelectedForOutput(c)
for c in combos:
    SapModel.Results.Setup.SetComboSelectedForOutput(c)
```

### Base Reactions — `SapModel.Results.BaseReact()`

Returns arrays: `load_case, step_type, step_num, Fx, Fy, Fz, Mx, My, Mz` (plus ground origin coords, discarded).

One row per load case / step combination.

### Story Drifts — `SapModel.Results.StoryDrifts()`

Returns arrays: `story, load_case, step_type, step_num, direction, drift, label, x, y, z`.

`direction` is `"X"` or `"Y"`. `drift` is the dimensionless interstory drift ratio.
`label` is the joint name at which the maximum drift occurs.

### Story Forces — `SapModel.Results.StoryForces()`

Returns: `story, load_case, step_type, step_num, location, Px, Py, Vx, Vy, T, Mx, My`.

`location` is `"Bottom"` or `"Top"`. Values are in the model's current units.

### Modal Periods — `SapModel.Results.ModalPeriod()`

Returns: `load_case, step_type, step_num, period, frequency, circ_freq, eigenvalue`.

Mode number is inferred as `i + 1` from the array index.

### Modal Mass Ratios — `SapModel.Results.ModalParticipatingMassRatios()`

Returns: `Ux, Uy, Uz, Rx, Ry, Rz` (individual) and `sum_Ux, sum_Uy, sum_Rx, sum_Ry` (cumulative).

Used to check whether 90% mass participation is reached.

### Joint Displacements — `SapModel.Results.JointDispl(name, 0)`

Called per-joint: `U1, U2, U3, R1, R2, R3` (translations and rotations in local axes).

**Sampling**: If the model has more than 200 joints, a uniformly-spaced subset
of 200 joints is extracted using `numpy.linspace`. This bounds the data size
regardless of model size.

---

## On-Demand Frame Forces

### `get_frame_forces(frame_name) → list[dict]`

This function is **not** called during ATTACH. It is called from
`callbacks/charts/frame_forces_cb.py` when the user clicks "Extract Forces".

It uses the module-level `_sap_model` that was stored during `get_etabs_model()`.

Returns arrays: `station, P, V2, V3, T, M2, M3` for every section cut along
the frame element across all selected load cases.

`station` is the distance from the I-end of the element in model units.

---

## Error Handling Pattern

Every internal extraction function uses a `try/except` block and returns an
empty list `[]` or `{}` on failure, rather than raising. This means one
broken API call (e.g. no results run yet) does not abort the entire ATTACH.

The outer `extract_all_data()` has a single broad try/except that catches
anything the inner functions missed and sets `status = "error: <traceback>"`.

The `_safe(call, default)` helper is used for API calls where the return code
must be checked (ETABS returns `0` for success, non-zero for failure):

```python
def _safe(call, default=None):
    result = call()
    if isinstance(result, tuple) and result[0] != 0:
        return default
    return result
```
