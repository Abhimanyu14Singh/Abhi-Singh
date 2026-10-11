# SkyFrame analysis performance on real building sizes (v1.18)

This document covers one `POST /api/analyze`-equivalent run — `OpenSeesEngine(model).run()` followed by `results.to_dict()`, `tables.engine_context` and the JSON dump — on realistic buildings. It records:

* where the time went;
* what changed;
* the before/after numbers;
* how result equivalence was checked.

The contract is in CONTRACT.md "Analysis performance, solver policy and convergence strategy". The tests are in `tests/test_perf_robust.py`.

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
