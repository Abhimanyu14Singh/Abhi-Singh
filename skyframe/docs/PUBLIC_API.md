# SkyFrame public Python API (`skyframe.client`)

`skyframe.client` is the stable scripting facade — the ETABS-OAPI
counterpart.  Every function is a THIN wrapper over the same machinery the
web endpoints call: no new logic, same numbers, same shapes.  Import it as

```python
import skyframe.client as sky
```

All examples below are runnable as-is (they are executed by
`tests/test_perf_api.py::test_public_api_doc_examples` on every CI run).

## Surface

| Function | Purpose |
| --- | --- |
| `quick_building(...)` | generate a regular moment-frame model (re-export of `skyframe.core.builder.quick_building`) |
| `open_model(path)` / `save_model(model, path)` | JSON model files (the web UI gallery format) |
| `run(model)` | every analysis the model defines -> `AnalysisResults` |
| `run_modal(model, num_modes=None)` | eigen analysis only -> `ModalResults` |
| `run_ritz(model, n=None, direction="X")` | load-dependent Ritz vectors |
| `run_fna(model, case)` | Fast Nonlinear Analysis of a TH case |
| `run_pushover(model, case)` | nonlinear static pushover |
| `run_cracked(model, case, cracked_ratio=0.35, ...)` | iterative cracked-slab stiffness |
| `design_steel(model, case=..., combos=..., code=None, **kw)` | AISC 360 / EC3 member checks |
| `design_concrete(model, rebar, case=..., fc=None, code=None)` | ACI 318 / EC2 member checks |
| `design_wall(model, combos=None, **kw)` | ACI 318 shear-wall pier checks |
| `design_punching(model, case=None, fc_prime=None, cover=None)` | ACI two-way shear checks |
| `to_dataframe(results, table)` | flat list-of-dict tables: `"drifts"`, `"reactions"`, `"member_forces"`, `"design"` |

Units are SkyFrame-wide SI: kN, kN·m, kPa, m, tonnes, seconds.

## Build, run, read drifts

```python
import skyframe.client as sky

model = sky.quick_building(stories=3, bays_x=2, bays_y=2)
results = sky.run(model)

# per-case, per-story drift table (list of plain dicts)
drifts = sky.to_dataframe(results, "drifts")
assert {"case", "story", "ux", "uy", "drift_x", "drift_y",
        "shear_x", "shear_y"} <= set(drifts[0])
eqx = [r for r in drifts if r["case"] == "EQX"]
assert len(eqx) == 3                      # one row per story
assert max(abs(r["drift_x"]) for r in eqx) > 0.0

# modal results ride along
assert len(results.modal.periods) == model.num_modes
```

`sky.to_dataframe(...)` returns plain `list[dict]` — `pandas.DataFrame(rows)`
away from a dataframe, but with no pandas dependency.

## Reactions and member forces

```python
import skyframe.client as sky

model = sky.quick_building(stories=2)
results = sky.run(model)

reactions = sky.to_dataframe(results, "reactions")
dead = [r for r in reactions if r["case"] == "DEAD"]
assert abs(sum(r["FZ"] for r in dead)
           - results.cases["DEAD"].base["FZ"]) < 1e-6

forces = sky.to_dataframe(results, "member_forces")
row = forces[0]
assert {"case", "member", "story", "end",
        "N", "V2", "V3", "T", "M2", "M3"} <= set(row)
# combos appear alongside cases
assert any(r["case"] == "1.2D + 1.6L" for r in forces)
```

## Save / reload round trip

```python
import os, tempfile
import skyframe.client as sky

model = sky.quick_building(stories=2, name="RoundTrip")
path = os.path.join(tempfile.mkdtemp(), "roundtrip.skyframe.json")
sky.save_model(model, path)
again = sky.open_model(path)
assert again.name == "RoundTrip"
assert len(again.members) == len(model.members)
assert sky.run(again).cases["DEAD"].base["FZ"] == \
       sky.run(model).cases["DEAD"].base["FZ"]
```

## Steel design checks

```python
import skyframe.client as sky

model = sky.quick_building(stories=2)
results = sky.run(model)          # reuse one analysis for many checks

steel = sky.design_steel(model, case="1.2D + 1.6L", Fy=345_000.0,
                         results=results)
rows = sky.to_dataframe(steel, "design")
assert rows and {"uid", "ratio", "status"} <= set(rows[0])
assert steel["summary"]["n"] == len(rows) == len(model.members)

# EC3 instead of AISC 360: same shapes, Eurocode checks
ec3 = sky.design_steel(model, case="1.2D + 1.6L", code="EC3",
                       results=results)
assert ec3["code"] == "EC3" and ec3["checks"]
```

(`quick_building`'s rectangular concrete sections report `status ==
"N/A"` here — only recognised library W-shapes get AISC/EC3 ratios; use
`design_concrete` on this model for real numbers.)

## Concrete design checks

```python
import skyframe.client as sky

model = sky.quick_building(stories=2)
rebar = {m.uid: {"n_top": 3, "n_bot": 3, "bar_dia": 0.020}
         for m in model.members if m.kind == "beam"}
conc = sky.design_concrete(model, rebar, case="1.2D + 1.6L")
rows = sky.to_dataframe(conc, "design")
assert len(rows) == len(model.members)     # every member gets a row
checked = [r for r in rows if r["uid"] in rebar]
assert checked and all(r["status"] in ("OK", "NG") for r in checked)
```

`design_wall` / `design_punching` mirror their endpoints the same way:
walls need pier-labeled shell walls in the model; punching returns an empty
`columns` list on models without meshed shell slabs:

```python
import skyframe.client as sky

model = sky.quick_building(stories=2)
punch = sky.design_punching(model)              # no slabs in this model
assert punch["columns"] == []
```

## Specialised runs

```python
import skyframe.client as sky

model = sky.quick_building(stories=2)
modal = sky.run_modal(model)                    # eigen only — fast
assert modal.periods[0] > 0.0

ritz = sky.run_ritz(model, n=2, direction="X")  # Ritz vectors
assert len(ritz.periods) == 2   # the X-reachable subspace of 2 stories
```

`run_fna(model, case)` needs a `model.th_cases` entry, `run_pushover(model,
case)` a `model.pushover_cases` entry, and `run_cracked(model, case)` meshed
shell slabs — see `CONTRACT.md` (v0.24/v0.25) for those case definitions;
each returns the same results object the corresponding endpoint serialises.

## Stability

* Everything importable from `skyframe.client` (`skyframe.client.__all__`)
  is the public surface; additions will be backwards compatible.
* `run(model).to_dict()` is exactly the `POST /api/analyze` payload — the
  web UI and the scripting API can never disagree.
* Performance notes (and the `SKYFRAME_SLOW_PATH=1` escape hatch for the
  v0.26 solver-domain reuse) live in `docs/PERF_NOTES.md`.
