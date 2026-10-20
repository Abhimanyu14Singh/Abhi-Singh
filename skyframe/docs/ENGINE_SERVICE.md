# SkyFrame engine service

The SkyFrame analysis engine (OpenSees) as a self-contained part, separate from SkyFrame's own UI. It
runs on the user's machine; host apps call it over HTTP on `127.0.0.1`. The first host is Supa (the
browser app in `SP Files/supa-fem-kit`); SomOptLite and SkyFrame's own UI come later.

Package: `skyframe/engine_service/` · tests: `tests/test_engine_service_*.py` · Supa drop-in:
`integrations/supa/`.

## Run it

```
python -m skyframe.engine_service serve --allow-origin https://<supa's address>
python -m skyframe.engine_service solve path/to/x.model.json      # writes x.out.json
python -m skyframe.engine_service version
```

The service listens on `http://127.0.0.1:8766` (`--port` to change). It only ever binds to the
loopback address, so other machines cannot reach it.

## Routes (API 0.1.0)

| Route | Answer |
| --- | --- |
| `GET /engine/version` | `{"engine", "api", "skyframe", "solvers", "jobs"}` |
| `POST /engine/supa/solve` | Body: a Supa `FemModel`. `202 {"job"}`; `400 {"error"}` when the model is malformed (bad node index, wrong array length...) |
| `GET /engine/jobs/<id>?after=N&wait=S` | `{"state", "steps", "next", "error"}`. Long-polls up to `S` s (max 25) for a step after the `N`th. States: `queued`, `running`, `done`, `failed`, `cancelled` |
| `GET /engine/jobs/<id>/result` | `200` the `FemResult`; `409` not finished; `422` failed or cancelled |
| `POST /engine/jobs/<id>/cancel` | `{"ok"}`; the job's process is stopped |

Each solve runs in its own process, so a cancel is immediate and OpenSees state never carries
between jobs. One job runs at a time; later ones queue.

### Calling it from a web page

A page on a public site that calls `127.0.0.1` meets two browser checks:

1. **CORS.** The service answers with `Access-Control-Allow-Origin` only for allowed origins:
   `http://localhost:*`, `http://127.0.0.1:*`, and each `--allow-origin` (or the comma-separated
   `SKYFRAME_ENGINE_ORIGINS` environment variable). A request with any other `Origin` gets `403`, so an
   unrelated website cannot drive the engine.
2. **Private Network Access** (Chrome, Edge). The preflight carries
   `Access-Control-Request-Private-Network: true`; the service answers
   `Access-Control-Allow-Private-Network: true` for allowed origins.

Chrome, Edge and Firefox treat `http://127.0.0.1` as a secure address, so an `https` page may call it.
Safari blocks it as mixed content; Supa then falls back to its built-in solver.

## Supa drop-in

`integrations/supa/src/solve.ts` replaces the kit's `src/solve.ts`. The signature is unchanged,
`solveFem(model, progress?) => FemResult`, still synchronous, so `src/worker.ts` is untouched. It uses
synchronous `XMLHttpRequest`, which browsers allow inside a Web Worker. Install and fallback rules are
in `integrations/supa/README.md`.

### How the engine solves Supa's model

Supa's `FemModel` is built in OpenSees as Supa describes it, in Supa's units (kip, ft):

| Supa | OpenSees |
| --- | --- |
| element `i, j, E, G, A, Iy, Iz, J` | `elasticBeamColumn` (Euler-Bernoulli) |
| `yh` (local y hint), local z = x × y | `geomTransf Linear` with vecxz = local z |
| `ri` / `rj` (bending released at that end, both axes) | the element's own `-releasez` / `-releasey` (exact static condensation); torsion is never released |
| `dia[]` | `rigidDiaphragm` about z; master node at the given centre, held in uz, rx, ry |
| `fixed[]` | `fix` all six DOF |
| `q[case]` (global k/ft) | `eleLoad -beamUniform`, turned to local axes |
| `nodeLoads`, `diaLoads` (fx, fy, mz at the centre) | `load` on the node / diaphragm master |
| six cases | one `Linear -factorOnce` analysis: one factorisation, six back-substitutions |

Results follow Supa's own rules: `pieces[src][case]` from the element end forces plus the load along
it, sampled at five points; a wall's strips summed side by side, a member's pieces the largest; N
tension positive. Every `FemResult` field keeps its meaning; one optional field is added, `engine`
(solver, API version, seconds, stability report).

### What it does not do: hide mechanisms

Supa's built-in solver puts a small spring on every free DOF. The engine does not. Before solving it
runs a stability check (`stability.py`):

- **Groups not connected to the ground** (through elements and diaphragms): held, reported, no results.
- **Translations no element resists** at a node (pin-ended members meeting in a line, say): held in
  that direction and reported with the objects that meet there. Their load has no path to the ground,
  so the balance check shows it as missing reaction.
- **Rotations no element resists** (joints where every member is pinned): indeterminate but carrying
  no load, because the model has no applied moments. Held by a negligible spring and counted in the notes.

After the solve, nodes moving more than 1 ft under D or L are reported as Supa does (`loose`,
`looseNodes`), with a note that separates a soft but stable structure from a mechanism.

### Demo tower, against Supa's own result

`SP Files/supa-fem-kit/models/demo.model.json`: 11,665 nodes, 26,795 elements, 76 diaphragms, 35,283
equations. The kit's own `npm run check` and `npm run solve` were run with the drop-in and the engine;
nothing in the kit's tools or worker was changed.

| Case | Out of balance | Drift | N | M | V | Max movement |
| --- | --- | --- | --- | --- | --- | --- |
| D | 0.000% | 0.01% | 0.02% | 0.69% | 0.03% | 0.93% |
| L | 0.000% | 0.01% | 0.04% | 0.69% | 0.03% | 0.92% |
| Wx, Wy, Ex, Ey | 0.000% | ≤ 0.01% | 0.00% | 0.00% | 0.00% | 0.00% |

Differences are the largest difference over the largest value, against `demo.result.json`. Lateral
cases agree to round-off. The gravity differences come from Supa's springs: they prop up the soft
framing slightly, and the engine has none. Solve time: 13.7 s here (Supa's solver: about 6 s in Node).

The check found no mechanism in the demo. Its 1,448 "unheld" nodes are real: floor framing of system
`ademo101` on the podium levels at 15, 30 and 45 ft, 18 ft steel beams whose far ends have no column
or wall under them (72 free member ends; the worst node moves 72.7 ft under dead load). That is a
modelling issue in Supa's framing generator, now named in the result's notes.

## Formats

Versioned JSON Schemas (draft 2020-12) in `skyframe/engine_service/schema/`:

| File | What |
| --- | --- |
| `supa_fem_model.schema.json` | Supa's `FemModel`, input to `POST /engine/supa/solve` |
| `supa_fem_result.schema.json` | Supa's `FemResult` plus the optional `engine` field |
| `analysis_model.schema.json` | **Draft** object-level model for the next step: the host sends frames, wall and floor areas, sections, patterns, code story forces, mass source, cases and combinations by stable object id; the engine meshes and loads it. No route takes it yet. |
| `examples/two_story_core.analysis_model.json` | A small example that validates |

Versioning: the API version is `major.minor.patch`. A host checks `GET /engine/version` and refuses
a different major version; adding optional fields is a minor change.
