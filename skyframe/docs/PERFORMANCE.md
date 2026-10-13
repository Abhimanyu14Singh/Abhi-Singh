# SkyFrame analysis performance on real building sizes (v1.18)

This document covers one `POST /api/analyze`-equivalent run — `OpenSeesEngine(model).run()` followed by `results.to_dict()`, `tables.engine_context` and the JSON dump — on realistic buildings. It records:

* where the time went;
* what changed;
* the before/after numbers;
* how result equivalence was checked.

The contract is in CONTRACT.md "Analysis performance, solver policy and convergence strategy". The tests are in `tests/test_perf_robust.py`.

The follow-up pass on meshed models (parallel solve families, one domain build per run, a faster mesher) is at the end of this document: [Meshed models: parallel solve families](#meshed-models-parallel-solve-families-v119). Its contract is CONTRACT.md "Meshed-model performance: parallel solves and fewer rebuilds"; its tests are in `tests/test_meshed_perf.py`.

The older v0.26 notes, about small models, are in `docs/PERF_NOTES.md`.

## Benchmark models

All models come from `core/builder.quick_building`:

* 6 m bays and 3.2 m stories;
* rigid diaphragms;
* DEAD, LIVE, EQX and EQY cases, 4 combos and 12 modes;
* two response-spectrum cases, RSX (CQC) and RSY.

The "meshed" variants add:

* a 0.2 m shell slab in every bay of every story, meshed at 1.5 m (4x4 quads per bay), with an SDL area load and case;
* a two-wall core on the middle bay lines, 0.3 m thick, meshed at 1.5 m.

| model | nodes | frame members | shell quads |
| --- | ---: | ---: | ---: |
| 10s-5x5-frame | 406 | 960 | 0 |
| 20s-5x5-frame | 776 | 1 920 | 0 |
| 20s-8x6-frame | 1 343 | 3 460 | 0 |
| 40s-5x5-frame | 1 516 | 3 840 | 0 |
| 10s-5x5-meshed | 4 562 | 960 | 4 160 |
| 20s-5x5-meshed | 9 082 | 1 920 | 8 320 |
| 20s-8x6-meshed | 16 789 | 3 460 | 15 680 |

The scripts were `models.py` (the factory), `phases.py` (per-phase timer) and `prof.py` (cProfile). They wrap these functions with inclusive wall timers:

* `mesh_model`, `_build`;
* `run_static`, `run_modal`, `run_response_spectrum`, `_modal_static`, `_combine_rsa`, `_combine`;
* `_compute_story_props`, `_seismic_diagnostics`, `_takedown`, `_member_outputs`, `_shell_outputs`, `_deflection_checks`;
* `ops.analyze`, `ops.eigen`;
* `to_dict`, plus the context and JSON step.

Measurement conditions:

* Linux container with 4 shared cores, Python 3.11, openseespy 3.7.1;
* `OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=1`;
* one run per model.

Other jobs shared the machine, so absolute times move by about ±40 %. The ratios are what matter.

## What the profiles showed (before)

**The linear solver dominated everything.** On a rigid-diaphragm building the Transformation constraint handler couples every slaved node of a floor to its master. The band of the reduced stiffness is therefore essentially a whole floor, and `BandGeneral` costs about n·b².

* **20s-8x6-frame:** 1 368 s of the 1 454 s total went into 88 `ops.analyze` calls (15.5 s each).
  * 953 s were the 60 center-of-rigidity (CR) unit-load solves of `_compute_story_props`.
  * 423 s were the 2 × 12 RS modal statics. Each of these also ran a full domain rebuild, 29 `_build` calls in total.
* **10s-5x5-meshed:**
  * a single static solve did not finish in minutes;
  * the Arpack eigen solve ran for more than 11 min at 4.5 GB before it was stopped;
  * Arpack's Arnoldi space is capped at rank(M) — few massed DOFs — and its shift-invert solves go through the same band storage.
* **20s-5x5-meshed:** the legacy engine **crashes**. The process is killed by SIGSEGV inside the BandGeneral allocation about 12 s into the run.

The Python side added a smaller layer:

* `compute_story_masses` ran once per rebuild, and its member lookup was O(stories × loads × members): 180 000 `_member` linear scans, 4.9 s on the 10-story frame.
* RS CQC built per-quantity Python lists.
* Station positions were recomputed for every case.

## What changed

Every item below is either bit-identical to the old code or equal to it to solver round-off. The next section has the evidence.

1. **Solver per problem size** (`engine/solver_policy.py`).
   * Below 300 estimated equations the solver stays `RCM + BandGeneral` — the exact legacy path. 300 is the measured crossover of BandGeneral and UmfPack.
   * At or above 300 equations it switches to `RCM + UmfPack`.
   * Crossover measurements:

     | eq | BandGeneral | UmfPack |
     | ---: | ---: | ---: |
     | 255 | 5.7 ms | 4.0 ms |
     | 1 110 | 298 ms | 30 ms |
     | 2 232 (meshed) | 3 039 ms | 133 ms |

   * Also measured: AMD + UmfPack (no faster), Mumps (sequential, slower), SparseGeneral (slower) and ProfileSPD (much slower).
   * `SparseSYM` was rejected because it returns wrong displacements under Transformation constraints.
2. **One factorization, many right-hand sides.** Linear static cases, RS modal statics and CR unit loads run on the reused elastic domain with one `Linear -factorOnce` UmfPack analysis. Results are bit-identical to refactoring per case. BandGeneral's LAPACK band solver cannot do this: the second solve fails.
3. **Exact condensed modal analysis** (`engine/modal_condensed.py`).
   * The mass is lumped, so `u_m = λ G_mm M_m u_m` exactly.
   * The method takes one solve per massed DOF (3 per story), a dense `eigh`, and one solve per mode for the full shape.
   * Periods agree with Arpack to about 1e-13. Shapes are mass-normalized with a deterministic sign.
4. **No re-solves for CR and RS.**
   * The CR unit loads are the same solves as the condensation columns, so they are memoized.
   * RS uses the unit modal responses from step 3, scaled by `Γ·Sa/λ`, instead of 2 × 12 extra solve + output passes.
5. **No rebuild per RS mode.** `_modal_static` reuses the domain instead of calling `_build` per mode: 29 builds became 5.
6. **Python hot loops, bit-exact.**
   * uid→object maps built once in `compute_story_masses` and `story_gravity_loads`, plus engine uid lookups;
   * station positions memoized per member;
   * RS CQC/SRSS evaluated row by row on a contiguous matrix, with the same `v @ rho @ v` per quantity;
   * RS scaling done in numpy, which uses the same elementwise IEEE products.
7. **Mesh cache across engines** (`core/mesh_cache.py`). A repeated analyze or table request on an unchanged geometry skips meshing — 4.5 s on 20s-5x5-meshed. Hits return a pickled copy, which is bit-identical.
8. **Model-size warnings** (`engine/model_size.py`), and a fallback from a forced band solver to UmfPack when the band storage estimate exceeds the memory cap. The run warns instead of segfaulting.

## Before / after (whole run, seconds)

"After" is shown for two runs of the same code (identical result hashes). The first run was on a quieter machine; the second ran while other jobs loaded the machine. "Before" was measured at moderate load.

| model | before | after | speedup |
| --- | ---: | ---: | ---: |
| 10s-5x5-frame | 29.7 | 2.9 – 5.0 | 6 – 10x |
| 20s-5x5-frame | 279.5 | 7.4 – 11.5 | 24 – 38x |
| 20s-8x6-frame | 1 454.4 | 15.9 – 25.5 | 57 – 91x |
| 40s-5x5-frame | 2 955.8 | 17.8 – 30.9 | 96 – 166x |
| 10s-5x5-meshed | >52 min (aborted, ~4.6 GB RSS) | 18.5 – 26.8 | >115x |
| **20s-5x5-meshed** | **crash** (SIGSEGV after 12 s) | **68.4 – 83.5** | **∞ (now runs)** |
| 20s-8x6-meshed | not run (crashes like the 20s-5x5 model) | 127.1 – 184.9 | — |

The meshed rows are now 2.2 – 2.7x faster again; see [the meshed-model pass](#meshed-models-parallel-solve-families-v119).

Peak RSS is about the same for frames: 866 → 712 MB on 20s-8x6. Meshed runs now fit in 0.4 – 1.4 GB.

### Phase breakdown (s), 20s-8x6-frame, before → after (quiet run)

| phase | before | after |
| --- | ---: | ---: |
| `ops.analyze` (calls) | 1 367.5 (88) | 5.8 (76) |
| `_build` (calls) | 55.3 (29) | 0.4 (5) |
| modal (`run_modal`: eigen → condensation) | 20.0 | 7.0 |
| RS (`run_response_spectrum`, incl. CQC) | 423.0 | 4.0 |
| CR (`_compute_story_props`) | 953.0 | 0.07 |
| static cases (`run_static`) | 57.7 | 1.4 |
| `to_dict` + context + JSON | 3.9 | 2.8 |

### Phase breakdown (s), 20s-5x5-meshed, after (quiet run)

| phase | time |
| --- | ---: |
| mesh | 5.3 |
| build × 5 | 6.8 |
| `ops.analyze` × 77 (about 0.6 s each) | 45.9 |
| modal (60 + 12 solves + unit responses) | 46.1 |
| RS | 4.2 |
| CR | 0.5 |
| static | 13.4 |
| `to_dict` + JSON | 4.1 |

What remains is the OpenSees per-solve element loop (formUnbalance + update over 8 320 shells), not the factorization. The next lever would be fewer condensation solves: block Lanczos at about 24 solves instead of 60, at the price of exactness.

## Equivalence evidence

* **Byte-identical below the threshold.** SHA-256 of the full results JSON, old engine vs new:
  * `quick_building()`: `2cc39c29d04bdf67` = `2cc39c29d04bdf67`;
  * a 5-story model with RSX (CQC), RSY (SRSS), linear and nonlinear TH, pushover with hinges, staged and two-stage P-Delta: `27191100fcf84e3b` = `27191100fcf84e3b`.

  The 1 632 pre-existing tests still pass.
* **Benchmarks vs the old engine**, using `core/results_compare.compare`. The comparator is relative per quantity family and modal sign- and degenerate-subspace-invariant. Worst relative difference over every numeric result:

  | model | worst relative difference |
  | --- | ---: |
  | 20s-8x6-frame | 2.6e-13 |
  | 40s-5x5-frame | 2.8e-13 |
  | 20s-5x5-frame | 1.1e-11 |
  | small meshed model | 4.3e-13 |

* **10s-5x5-frame: 8.7e-9.** This comes from the old engine. The plan is doubly symmetric, so modes 1/2 have equal periods and Arpack returns a mixed X/Y basis. The legacy CQC then leaks about 1e-9 of the X response into Y quantities (for example RSX base MX = 0.0055 vs exactly 0). The new engine's pure modes give the physically exact zero.
* **Excluded from the comparison:** the ASCE 7 `tors_ratio_*` and `flag` of the unloaded direction on symmetric plans. These are ratios of about 1e-19 m round-off drifts in both versions, and the old engine already flags "extreme" torsion there at random. This is filed as a separate fix.
* **Tests above the threshold.** `tests/test_perf_robust.py` asserts that the default run equals the forced legacy run (`solver: band`, `eigen_solver: legacy`) to 1e-9 on an eccentric 6-story frame and a meshed slab and wall model. It also asserts that factor-once is bit-identical to fresh factorizations.

## Robustness: one convergence strategy

Before this change, each nonlinear path had its own retry logic:

* static Newton: none;
* P-Delta stages: none;
* corotational stages, pushover, nonlinear static and TH: one `NewtonLineSearch` retry;
* nonlinear static: a 10× substep;
* direct-integration options: their own halving.

All of them now go through `engine/convergence.Strategy`:

* `legacy` (the default) reproduces each path op for op, which the tests prove bit-identical.
* `robust` (opt-in) adds:
  * an algorithm × test ladder: Newton → ModifiedNewton → KrylovNewton → NewtonLineSearch → BFGS, with NormDispIncr, then EnergyIncr;
  * recursive step halving with a configurable cap;
  * a per-case `convergence` report.

Pinned examples:

* **Softening-hardening spring.** The curve runs 0 → (0.01, 100) → (0.04, 10) → (0.05, 210) kN. With load through the snap, legacy Newton plus line search fails. Robust mode converges to the closed form u = 0.04 + (H − 10)/20 000, using KrylovNewton or NewtonLineSearch.
* **Halving only.** A 40-segment concave curve with a 4-iteration cap and the Newton-only ladder: legacy fails, and robust halving converges to the exact segment root.

## Model-size safety

* `run()` attaches a `model_size` report and appends warnings — it never fails — beyond any of these limits:
  * 60 000 or 250 000 estimated equations;
  * 20 000 shells;
  * 400 RS modal statics;
  * 2e8 TH step × equation products;
  * 2e7 result values.
* A forced band solver over its memory cap degrades to UmfPack with a warning.
* `size_warnings: false` turns the warnings off.

## Meshed models: parallel solve families (v1.19)

After the v1.18 pass a meshed 20-story run still took about a minute. This pass makes meshed runs 2.2 – 2.7x faster again. Every result is **byte-identical** to v1.18: the same SHA-256 of the full results JSON on every benchmark.

### Profile before (20s-5x5-meshed, quiet machine, 58.8 s)

Counts per run, from `phases.py` and cProfile:

| what | count | time |
| --- | ---: | ---: |
| `ops.analyze` (5 static + 60 condensation columns + 12 mode shapes) | 77 | 39.3 s (0.51 s each) |
| `_build` (static, MODAL, RSX, RSY, CR) | 5 | 0.9 s + 4.4 s meshing |
| `ops.reactions` (5 static + 12 unit modal responses) | 17 | 2.1 s |
| `eleResponse` (shell `forces` + `stresses`, frame `localForce`) | 178 400 | 1.0 s |
| `_member_outputs` / `_shell_outputs` | 17 / 5 | 1.8 s / 1.0 s |
| RS (scaling + CQC/SRSS of 2 cases) | — | 3.9 s |
| `to_dict` + context + JSON | — | 3.8 s |

Findings:

* **The solves are the cost, not the extraction.** Result extraction (`eleResponse` for every element and case, reactions, station recovery) is about 5 s of the 59 s. The 77 solves are 39 s.
* **A solve is the element loop, not the factorization.** With factor-once, each `ops.analyze` is a forward/back substitution plus OpenSees' per-element passes over all 8 320 shells: zero the loads, form the unbalance (`getResistingForce`), update and commit. openseespy offers no multi-right-hand-side solve, so these passes are paid once per load vector.
* **The solves are independent.** Each runs on the reused elastic domain, reset to its initial state, with the same factorization. Its result does not depend on which solves ran before it (the v0.26 reuse and factor-once invariants).
* **Redundant rebuilds.** `run_modal` rebuilt the domain before looking at its cache. Both RS cases therefore paid a full build only to get a cache hit, and the CR step paid one more because those builds ended the domain reuse.
* **Meshing** spent 4.4 s in `_split_member`, which tested every shell node against every frame member (1 920 × 9 000 Python checks).

### Strategies evaluated

| strategy | verdict |
| --- | --- |
| Process-parallel independent solves, each worker with its own OpenSees instance | **Done.** Largest win. See below. |
| Avoid rebuilding between case families | **Done.** 5 builds → 1. |
| Reduce redundant eigen solves | The condensed path has no eigen solve. The redundant work was the RS cases' modal rebuild, now removed. |
| Exact bounding-box pre-filter in the mesher | **Done.** Meshing 4.4 → 0.7 s (20s-5x5) and 14.1 → 1.6 s (20s-8x6), with the identical mesh. |
| One factorization, many right-hand sides | Already in place since v1.18 (`Linear -factorOnce`). The per-solve cost left is the element loop, which no openseespy command skips. |
| Batch / vectorised shell-force recovery from nodal displacements | **Rejected.** Extraction is under 10 % of a run. Reproducing ShellMITC4's resultants in numpy means re-implementing its assumed-shear (MITC) and drilling interpolation, which is not exact by construction. The `forces` warm-up call before `stresses` is required (stale-stress quirk). Extraction now runs inside the parallel workers anyway. |
| Mode shapes as linear combinations of the stored condensation columns (12 fewer solves) | **Rejected.** Equal only to round-off, not byte-identical. Each unit modal response still needs a domain evaluation (reactions, member forces), so the gain is small. |

### What changed

1. **Parallel solve families** (`engine/parallel.py`). Four families of independent work run on several processes:
   * the plain linear static cases;
   * the condensation columns `G_mm` (one unit-load solve per massed DOF; they also fill the CR memo);
   * the mode-shape solves with their unit modal responses;
   * the solve-free RS cases (scaling and CQC/SRSS of the stored unit responses).

   How a family runs:
   * The parent builds the domain and factors the stiffness. The static family's first case does this. The later families start "warm" on the live factorization.
   * `os.fork()` then gives each worker a copy-on-write image of the whole interpreter: the OpenSees domain, the factored UmfPack system and the engine. No worker rebuilds or refactors anything.
   * Each worker solves its share and sends its Python results back pickled (exact for floats). It then leaves with `os._exit`.
   * The parent solves its own share meanwhile and merges by item index.
   * The plan is deterministic: item `k` of `n` goes to lane `(n − 1 − k) mod L`. Lane 0 is the parent, which ends with the last item, so the interpreter is left exactly as the serial loop leaves it.
   * A worker that raises, dies or cannot be forked has its items re-solved by the parent. An analysis error then surfaces as in the serial run.

   When it is used:
   * only for the sparse factor-once policy (≥ 300 estimated equations); small models keep the serial legacy path;
   * only on Linux (`fork`);
   * `analysis_options.parallel`: `"auto"` (default) uses `min(cores, 8)` lanes when at least 4 cores are available; `"off"` or `1` is serial; an int sets the lane count;
   * `SKYFRAME_PARALLEL=0` and `SKYFRAME_SLOW_PATH=1` force serial.
2. **One domain build per run.** On large models `run_modal` answers from the live reused elastic domain, which is the same build `_build()` would make. A cache hit (the RS cases) needs no build; a miss runs the condensed solve on the reset domain. The CR solves keep the reuse alive.
3. **Exact mesher pre-filter** (`core/mesh._NearPoints`). A shell node can cut a member only when it lies within `_TOL` of a foot point strictly inside the member. So only nodes inside the member's bounding box, grown by `2·_TOL`, can pass. That box is tested in numpy; the unchanged per-node test runs on the survivors, in the original order.

Per-run counts after (20s-5x5-meshed):

* 20 `ops.analyze` in the parent and 57 in the workers: still 77 solves in all, each run once;
* `_build` × 1;
* 10 forks over the four families.

### Before / after, meshed benchmarks (whole run, seconds)

Same container: 4 cores, `OMP_NUM_THREADS=1`, one run per model, measured back to back on a quiet machine. "Extra memory" is the largest drop of the system's MemAvailable during the run, workers included.

| model | before (v1.18) | after | speedup | SHA-256 of results JSON (before = after) | extra memory after |
| --- | ---: | ---: | ---: | --- | ---: |
| 10s-5x5-meshed | 19.4 | 8.9 | 2.2x | `08833779d88351fe` | 0.5 GB |
| **20s-5x5-meshed** | **58.8** | **21.4** | **2.7x** | `c89070360fdbb475` | 1.0 GB |
| **20s-8x6-meshed** | **133.7** | **48.7** | **2.7x** | `93b196a6d2669ba8` | 1.6 GB |
| 10s-5x5-frame | 3.4 | 2.5 | 1.4x | `bb57a435a1fcb374` | 0.25 GB |
| 20s-8x6-frame | 16.7 | 11.0 | 1.5x | `ebd68caf5772f596` | 1.1 GB |

The v1.18 table above was measured on a busier machine (68 – 84 s and 127 – 185 s for the two 20-story meshed models). The "before" column here is the same v1.18 code re-measured under the "after" conditions.

Phase breakdown (s), before → after:

| phase | 20s-5x5-meshed | 20s-8x6-meshed |
| --- | ---: | ---: |
| meshing (inside the first `_build`) | 4.4 → 0.7 | 14.1 → 1.6 |
| `_build` (calls), meshing included | 5.4 (5) → 0.7 (1) | 16.2 (5) → 2.2 (1) |
| static cases (`run_static`, 5 cases) | 10.9 → 4.0 | 31.5 → 10.8 |
| modal (60 + 12 solves and unit responses) | 39.6 → 11.2 | 87.1 → 25.3 |
| RS (2 cases) | 3.9 → 1.4 | 7.0 → 3.8 |
| CR (`_compute_story_props`) | 0.29 → 0.02 | 0.38 → 0.06 |
| `run()` total | 55.0 → 18.2 | 126.6 → 42.4 |
| `to_dict` + context + JSON (unchanged code) | 3.8 → 3.2 | 7.1 → 6.4 |

What remains:

* More than half of a meshed run's wall time is still OpenSees' per-element loop (20s-5x5: 11.3 s of 21.4 s in the parent alone). It is now spread over 4 processes.
* On 4 cores one solve slows from 0.51 s to about 0.7 s (20s-5x5) when four run at once, because they compete for memory bandwidth. Four lanes therefore give about 3x on the solve phases, not 4x.
* The first static case (build, factorization, outputs) and the results JSON stay serial.

### Equivalence evidence

* **Byte-identical, every benchmark.** The SHA-256 of the full results JSON is unchanged for all five benchmark models (table above). `core/results_compare.compare` therefore reports a worst relative difference of 0. `quick_building()` keeps `2cc39c29d04bdf67`.
* `tests/test_meshed_perf.py` pins:
  * the parallel run (4 lanes, 2 lanes, "auto") is byte-identical to `parallel: "off"`. This holds on the meshed slab and wall model, and on a mixed model with piers, a P-Delta case between linear cases, model-wide P-Delta, a section cut, an RS directional combo, linear TH, staged construction and an RS case with its own mode count;
  * a worker that fails or dies is re-solved by the parent, with identical results;
  * the plan is closed form;
  * a meshed run makes exactly one build and `static cases + massed DOFs + modes` solves;
  * small models never fork;
  * the option validates and serializes only when it is not the default;
  * the mesher pre-filter gives exactly the brute-force member splits (a diagonal brace through a wall mesh node, closed-form cut point).
* The existing above-threshold test (`test_fast_path_equals_legacy_path`: default run vs forced `band` + `legacy` eigen to 1e-9) now also covers the parallel path.

## Large models: 50k–220k equations (v1.24)

This pass benchmarks production-size meshed buildings and removes the two
costs that grow worst with size: the linear solver refactoring on every
solve, and the band Arpack eigen solver. The contract is CONTRACT.md
"Buckling with shells and links, sparse eigen and BucklingFEM". The tests
are in `tests/test_buckling_fem.py`.

### Benchmark models

All four come from the `perf/models.py` factory:

* `quick_building` with 8 × 6 bays of 6 m;
* every bay has a 0.2 m slab and there is a two-wall 0.3 m core;
* DEAD, LIVE, EQX, EQY and SDL cases, 4 combos, 12 modes, RSX and RSY.

The "no diaphragm" variants set `rigid_diaphragms = False` and
`diaphragm = "none"`. Mass then sits on every slab node, so the exact
massed-DOF condensation (capped at 600 massed DOFs) cannot apply.

| model | mesh | nodes | shells | equations | free massed DOFs |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20s-8x6-meshed (rigid diaphragms) | 1.5 m | 16 789 | 15 680 | 50 760 | 60 |
| 40s-8x6-meshed (rigid diaphragms) | 1.5 m | 33 509 | 31 360 | 101 520 | 120 |
| 10s-8x6-meshed, no diaphragm | 1.5 m | 8 419 | 7 840 | 50 100 | 16 500 |
| 20s-8x6-meshed, no diaphragm | 1.0 m | 36 893 | 35 280 | 220 920 | 72 520 |

Measurement conditions:

* the same 4-core container as above, `OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=1`;
* one `OpenSeesEngine(model).run()` plus `to_dict` plus JSON per model;
* "before" is the base commit (v1.23) and "after" is this change, on the
  same machine, back to back;
* scripts in the scratch `perf/` folder: `bench_large.py` (timers on
  `ops.analyze`, `ops.eigen`, `run_static`, `run_modal`,
  `run_response_spectrum`) and `cmp_res.py` (`core/results_compare`).

### What the profiles showed (before)

* **UmfPack refactors on every solve.** The engine already ran factor-once
  (`algorithm Linear -factorOnce`). Even so, this openseespy build's UmfPack
  repeats the numeric factorization on every `analyze`. Measured with a
  repeated identical solve:
  * 27 000 equations: 0.9 s per solve;
  * 75 000 equations: 6.9 s per solve, against 9.1 s for the first solve.

  MUMPS keeps its LU factors. Its repeat solves cost 0.09 s and 0.3 s on
  the same models, which is mostly OpenSees' element loop. On the v1.19
  meshed models the per-solve cost was therefore the factorization, not
  the element loop as assumed there.
* **Band Arpack on mass-on-every-node models.** On the 10-story
  no-diaphragm model `ops.eigen` took 226 s of the 368 s run. That is two
  calls: the modal case and the RS refresh.
* **MUMPS threads.** The OpenSees binary links `libgomp`, but a 75 000
  equation factorization took 7.6 s with `OMP_NUM_THREADS=1` and 7.1 s
  with 4 threads. The bundled reference BLAS is single-threaded, so no
  multithreaded path is enabled (`solver_policy.threads_info()`).

### What changed

1. **MUMPS factor reuse above 20 000 equations** (`engine/solver_policy.py`).
   * `"auto"` picks MUMPS at `MUMPS_EQ_THRESHOLD = 20000` estimated
     equations and above, and `FACTOR_ONCE_SYSTEMS` includes it.
   * A factor-once MUMPS solve whose pattern carries element loads fails in
     MUMPS' substitution (error -3). `analyze_reused` then re-factors once,
     quietly, and retries. Nodal-load flows reuse the factors: condensation
     columns, mode shapes, CR, Lanczos.
   * Below the threshold nothing changes, and the results stay
     byte-identical.
2. **Sparse shift-invert Lanczos modal analysis** (`engine/lanczos.py`,
   `engine/sparse_modal.py`). It runs when the policy picked a sparse
   system and the model has more than 600 free massed DOFs.
   * Lanczos in the K inner product with full re-orthogonalisation.
   * Locking restarts, for equal-period pairs.
   * Subspace-iteration refinement on fresh solves.
   * Every step is one MUMPS forward/back solve.
3. **Sparse stability check** (`core/stability_sparse.py`). Above the
   dense cap it replaces the "too large" skip. It adds soft grounded
   springs and runs Lanczos for the near-null modes.

### Before / after (whole run, seconds)

| model | equations | before | after | speedup | worst relative difference |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20s-8x6-meshed | 50 760 | 68.0 | 50.8 | 1.3x | 4.0e-12 |
| 40s-8x6-meshed | 101 520 | 256.0 | 127.2 | 2.0x | 8.2e-12 |
| 10s-8x6-meshed, no diaphragm | 50 100 | 367.6 | 74.7 | 4.9x | 6.9e-12 (1) |
| 20s-8x6-meshed 1.0 m, no diaphragm | 220 920 | > 1 800 (stopped) | 343.8 | > 5.2x | — |

The 220 920-equation run before this change was stopped at the 30-minute cap, still inside the band Arpack eigen solve.

(1) The only other difference is one deflection-check string, `L/83520217`
vs `L/83520216`. It is the rounded display of a ~1e-11 m deflection, so the
ratio is round-off noise.

The worst relative difference comes from `core/results_compare.compare`
over every numeric result. It is modal sign- and degenerate-subspace
invariant. The ASCE 7 torsion ratios and flags, the warning text and the
`model_size` block are ignored (see the v1.18 note above). Every benchmark
is equal to 1e-9; the requirement is ≤ 1e-9 above the thresholds.

Phase breakdown (s), before → after:

| phase | 20s-8x6 | 40s-8x6 | 10s-8x6 no diaphragm |
| --- | ---: | ---: | ---: |
| `ops.analyze` (all solves) | 39.2 → 15.3 | 190.7 → 47.4 | 115.9 → 39.8 |
| `ops.eigen` | — | — | 226.2 → 0 (Lanczos) |
| modal (`run_modal`) | 38.6 → 16.3 | 184.8 → 48.5 | 262.4 → 30.9 |
| RS cases | 6.1 → 4.7 | 9.5 → 10.6 | 81.9 → 26.3 |
| static cases | 13.7 → 12.5 | 40.0 → 31.1 | 18.9 → 7.8 |
| peak RSS (MB) | 1 378 → 1 493 | 2 697 → 2 927 | 729 → 819 |

For a 60-story wall-and-floor tower (the R3 exit test), the 220 920-equation
row is the size to look at: modal with 72 520 massed DOFs, two RS cases and
five static cases run in under 6 minutes at 2.8 GB.

### New sparse analyses on large models

Both runs are on the 10s-8x6-meshed model with rigid diaphragms: 25 380
equations, 7 840 shells.

| analysis | before | after |
| --- | --- | --- |
| Check Stability (`/api/check/stability`) | skipped: "about 25 380 equations exceed the dense-analysis cap of 3000" | sparse check, stable, 155 s (205 solves) |
| FEM buckling case (DEAD + SDL, 4 modes, shells included) | not available (frame-only numpy solver) | 25.2 s: 67 solves, λ = 24.2, 27.4, 30.7, 35.1; 670 frame + 7 834 shell geometric stiffness blocks |

### Equivalence evidence

* **Byte-identical below the thresholds.** SHA-256 of the full results
  JSON and the model JSON, base commit vs this change:
  * `quick_building()` with a frame buckling case and an RS case;
  * a 3-story meshed slab and wall model;
  * the dense stability report of a 2 × 2 × 3 building.

  All are identical (`487a9924bdd0293b`, `85fef422517164e6`,
  `f29d3528bf8c3177`), and the full test suite passes.
* **Above the thresholds:** ≤ 8.2e-12 on every benchmark (table above).
* **Lanczos vs OpenSees eigen:** `test_lanczos_modal_equals_legacy` forces
  both paths on a quick building with an RS case, and the worst difference
  is 4e-14.
* **MUMPS vs UmfPack displacements:** 6e-14 (rigid diaphragms) and 3e-13
  (no diaphragm) on the 10-story meshed building.

## GPU 3D renderer (E3)

View › Renderer ▸ **GPU (WebGL2)** is a three.js r170 backend for the 3D view. three.js is vendored under `api/web/vendor/three/0.170.0/`, so the renderer works offline. **Classic (Canvas 2D)** stays the default; the reasons are in "Status" below.

The contract is in CONTRACT.md "GPU 3D renderer (E3)". The tests are in `tests/test_gpu_renderer.py`. The Playwright scripts that produced every number below are in `tools/gpu_e3/`:

* `parity.mjs` — screenshot diff plus pick and box equality;
* `bench.mjs` — frame times;
* `scale.mjs` — the 282 k-member scale test;
* `gpusmoke.mjs` — GPU-mode smoke checks.

Each script expects a server on `$PORT` and model / result JSON files next to it.

### What changed

The Classic view is a Canvas-2D wireframe. Every frame it:

* projects every vertex in JavaScript;
* painter-sorts every item;
* issues one path draw per item.

On the 50-story dual-system tower that is 4 400 frames and 1 400 shells. The owner measured 52 ms per frame for lines and 76 ms for the extruded view.

The GPU backend works differently:

* **Geometry is uploaded once.** It is rebuilt only when the model, the display options or the selection-only set change.
  * All frame centerlines are one instanced draw: screen-space wide lines with per-instance colour, width and pick id.
  * Shells are one triangle buffer per behaviour group. Openings are triangulated.
  * Extruded members use one instanced draw per section profile. The profile is extruded once, and each instance gets the axis, local-2 and local-3 columns plus an origin. These are taken from the VX geometry cache: `polygeom.memberAxes` (incl. `frame_axis_convention`), the cardinal point and the interpolated joint offsets. Nonprismatic members use the exact VX rings.
* **Camera changes cost almost nothing.** Orbit, pan and zoom only change the camera and uniforms. A depth buffer replaces the painter's sort.
* **Frames are rendered on demand.** `viewer._dirty = true` schedules one frame. Inertia and mode or buckling animation keep frames going only while they run. An idle view renders nothing.
* **Context settings.** The WebGL context uses `powerPreference: "high-performance"`, MSAA and `devicePixelRatio`.
* **Contours.** The scalar is a vertex attribute and the colormap runs in the fragment shader. Switching the component or the case uploads one `Float32Array`. A3 contour cells are captured once in world space.
* **Deformed shapes and mode animation run in the vertex shader** as displacement attribute × scale uniform.
  * Hermite member curves are sampled into 8 sub-segments at f = 1, the same as Classic.
  * Extruded deformed members are 5-station instanced prisms.
  * Animating a mode changes one uniform per frame.
* **Picking.**
  * Click and hover use an ID buffer: the frame centerlines rendered off screen once per frame, with a (2·thr+1)² pixel window read back. The Classic distance rule, applied in Classic painter order, picks among those candidates.
  * Ctrl+drag window and crossing selection uses a world-space uniform grid index. This gesture is new in both renderers.
* **Labels.** The VX object labels are decluttered: off-screen labels and labels that overlap an already placed one are skipped, using a 6 px occupancy grid.
* **Everything else is the Classic code**, run on the transparent 2D canvas above the WebGL canvas: links, supports, badges, releases, every module overlay hook (force diagrams, wc diagrams, tendons, hinges, …) and the triad. Grid labels go on a 2D under-layer, so solids hide them as before. Badge passes that need screen segments (rigid zones, ΔT, T/C-only, releases) project only their own members.
* **Fallback.** Without WebGL2, or when the context is lost, the view stays on or returns to Classic, with a toast and no console output.
* **Desktop app.** On Windows, the desktop shell (`skyframe/desktop.py`) appends `--ignore-gpu-blocklist --enable-gpu-rasterization --force_high_performance_gpu` to `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`. A value already set by the user is kept, and `SKYFRAME_NO_GPU_FLAGS=1` opts out.

### Forcing the RTX GPU on Windows

Edge WebView2 normally uses the GPU that Windows assigns to the executable. On a laptop or a desktop with an iGPU, that can be the integrated adapter. If it is, a one-time toast names the adapter; View › Renderer › Graphics Adapter shows it at any time.

To pin the discrete GPU:

1. Open Windows **Settings › System › Display › Graphics**.
2. Click **Add desktop app** and add **SkyFrame.exe**.
3. Also add **msedgewebview2.exe**, which draws the page. It is in `C:\Program Files (x86)\Microsoft\EdgeWebView\Application\<version>\`.
4. For each one, choose **Options › High performance** (the RTX 5090).
5. Restart SkyFrame.

Hardware acceleration in WebView2 is on by default; the switches above only stop a GPU blocklist from turning it off.

### Measured here

**Conditions:**

* this container: 4 shared cores, **no GPU**;
* Chromium headless with **SwiftShader**, a CPU software rasterizer (`--use-angle=swiftshader`);
* 3D canvas 1360 × 829 CSS px, DPR 1, MSAA on;
* tower = `templates_sys.build_system("rc_dual_core", {"stories": 50})`, which has 4 400 frames and 1 400 shells;
* results computed offline with the engine (2 020 FE shell quads) and injected into the page.

Times are per frame while orbiting (0.02 rad steps). Classic is `_render()` plus a 1-pixel `getImageData`. GPU is the whole frame (WebGL draw + 2D overlay) plus a 1-pixel `readPixels`, so the CPU waits for the software rasterizer.

| scenario | Classic median | GPU main-thread CPU | GPU frame incl. SwiftShader raster | GPU draw calls | GPU triangles |
| --- | ---: | ---: | ---: | ---: | ---: |
| lines | 70 ms | 2.4 ms | 537 ms | 9 | 39 484 |
| extruded | 102 ms | 2.3 ms | 758 ms | 9 | 111 384 |
| contours M11 | 69 ms | 1.9 ms | 384 ms | 6 | 41 024 |
| contours + extruded | 82 ms | 3.3 ms | 856 ms | 9 | 111 424 |
| deformed (DEAD) | 185 ms | 1.5 ms | 1 367 ms | 7 | 110 624 |
| mode 1 animation | 196 ms | 1.1 ms | 1 245 ms | 7 | 110 624 |
| mode 1 animation, extruded | 146 ms | 1.5 ms | 848 ms | 8 | 198 624 |

The GPU triangle counts include the fat-line quads: two triangles per centerline segment.

**Reading the table:**

* **The main thread is freed.** The GPU column "main-thread CPU" is the JavaScript + WebGL command time per frame. It is 1–3 ms against 70–200 ms for Classic, and it does not depend on the GPU.
* **The SwiftShader column is the CPU emulating a GPU**, which is pessimistic by orders of magnitude. The frame is dominated by fill rate: about 50 stacked translucent floor plates, 4× MSAA, rasterized on 4 shared cores.
* **Draw calls and triangles transfer to a real GPU.** These include the fat-line quads.

**Projected for the owner's RTX 5090 (not measured here).**

| item | estimate |
| --- | --- |
| geometry per frame | 40 k–200 k triangles in under 10 draw calls; fractions of a millisecond of vertex work on a GPU that does billions of triangles per second |
| overdraw | the 50 stacked translucent plates on 1360 × 829 × 4 samples are about 2.3·10⁸ blended samples, well under 1 ms at the card's fill rate |
| CPU per frame | the 1–3 ms main-thread cost measured here, lower on the Core Ultra 9 285K |

So 60 fps (under 16 ms) holds with a large margin for the tower in lines, extruded, contour and animated-mode views.

**Scale (several hundred thousand elements).** The tower was replicated 8 × 8 in plan and set directly on the viewer: 281 600 frames and 89 600 shells.

| item | Classic | GPU |
| --- | --- | --- |
| frame time | 151 s for one frame | — |
| one-off geometry build + upload | — | 0.77 s |
| main-thread CPU per orbit frame | — | 16 ms, mostly module overlay hooks that walk every member (e.g. `npOverlay`); the GPU pass itself is a few ms |
| draw calls / triangles | — | 8 calls · 1.46 M triangles |
| SwiftShader raster | — | 20 s per frame (projected: a few ms on an RTX 5090) |
| click pick | — | 1.5 ms (the ID-buffer pass itself is 8.6 s in SwiftShader; it runs once per changed frame) |
| Ctrl+drag box selecting 51 563 members | — | 0.32 s; the grid index cut the candidates to 64 886 of 281 600 |

**Parity with Classic.** Each scenario was screenshotted in both renderers at the same camera, with the same `vx` options and overlays. A pixel counts as different when a channel differs by more than 48/255. The first number is % of the view; "of drawn" is % of the pixels either renderer drew on.

Model: 5-story rc_dual_core (440 frames, 140 shells; results injected).

| scenario | % view | of drawn | notes |
| --- | ---: | ---: | --- |
| lines | 0.02 | 0.1 % | |
| colour by section | 0.33 | 1.6 % | |
| extruded | 0.88 | 3.9 % | |
| extruded, colour by section, 50 % shell transparency | 3.90 | 17.4 % | translucent slabs over solids: depth order instead of painter order |
| cut plane X | 0.03 | 0.2 % | |
| extruded + cut plane Z | 0.44 | 2.8 % | |
| highlight | 0.24 | 1.1 % | |
| joints + local axes | 1.91 | 9.0 % | joints / axes are on top in both; line AA differs |
| object labels | 4.64 | 21.7 % | by design: decluttered |
| deformed | 1.10 | 9.1 % | thin cyan lines, AA only |
| deformed extruded | 0.28 | 1.5 % | |
| mode 1 | 1.06 | 8.5 % | |
| mode 2 extruded | 0.65 | 3.3 % | |
| contours M11 | 0.17 | 0.8 % | |
| contours N22 + extruded | 1.89 | 8.4 % | |

**Picking:**

* 1 647 click samples: 1 500 random points plus points near every third member midpoint, with an 8 px threshold.
* GPU and Classic returned the **same member (or none) for 1 647 / 1 647** samples, including 445 hits.
* 6 window / crossing boxes, from 1 to 440 members: **6 / 6 identical**.

**Other checks:**

* menu verification (`verify8620.mjs`, adapted to the test port): 41/41 mock and 41/41 live. View has 14 entries, within the limit.
* GPU-mode smoke (mock + live): menu switching, persistence, deformed and mode layers, no frames when idle, frames while animating, extruded prisms, Ctrl+drag selection, no-WebGL fallback, zero console errors: 18/18.
* full pytest suite green.

### Status

The six phases are implemented and pass the checks above:

1. frames, shells, supports, camera and render on demand;
2. extruded view, display options and colour-by;
3. picking, selection, snaps and 3D draw;
4. deformed shapes and mode animation;
5. contours;
6. diagrams, piers, links, loads, labels and the cut plane — through the overlay pass.

**Classic stays the default** because some items are not yet proven on the owner's hardware or are not pixel-identical:

* GPU frame times on a real RTX GPU have not been measured.
* Labels are decluttered.
* Links, supports and module diagrams are drawn on top of the scene, not depth-sorted with it. This is the same as Classic for diagrams, and different for links.
* Pipe and tube inner walls are drawn.
* Lines and fills on the same plane can tie in the depth buffer.

Switching the default is a one-line change in `js/gpu/gpuhook.js` (`g3Read`).
