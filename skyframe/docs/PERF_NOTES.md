# SkyFrame engine performance notes (v0.26)

Honest, measured performance pass over `OpenSeesEngine.run()`.  Everything
here was profiled/timed on the CI container (Linux, 4 cores, Python 3.11,
openseespy 3.7.x); absolute numbers move machine to machine but the shapes
and the ratios are what matter.  All timings are `time.perf_counter`
wall-clock, **median of 3** after one warm-up run.

## The three profile models

| model | contents | solves inside one `run()` |
| --- | --- | --- |
| `frame` | `quick_building(stories=8)` — 232 members, rigid diaphragms | 4 static cases, 4 combos, 12 modes, 8x3 CR unit loads |
| `shell` | 2-story 2x2-bay frame + 2 walls + 2 slabs meshed at 1.0 m (~430 ShellMITC4 quads, ~6k dof) | 4 static cases, 4 combos, 6 modes, 2x3 CR unit loads |
| `cases` | `quick_building(stories=8)` + 8 extra static cases + 8 extra combos | 12 static cases, 12 combos, 12 modes, 8x3 CR unit loads |

(The shell model at 0.6 m mesh — 20x20 quads per slab — has the same
hotspot shape but a single `run()` exceeds 20 minutes on this container
because of the dense-eigen cost below, so the 1.0 m variant is the one
profiled and timed.)

## What the profiles actually showed (pre-optimization)

`cProfile` top cumulative hotspots, one `run()` each:

**frame** (1.14 s profiled / 0.64 s wall): `_compute_story_props` 0.66 s —
24 of the 31(!) full `_build()` domain rebuilds in the run, one per
`_master_rz` unit-load solve; `_build` total 0.52 s; `ops.analyze` (28
solves) 0.26 s; `_assign_mass` -> `compute_story_masses` re-derived per
rebuild 0.23 s, mostly `BuildingModel._member` linear scans (34 816 calls,
0.22 s); `_superpose` combo assembly 0.16 s; `_member_outputs` station
recovery 0.14 s.

**shell** (17.8 s profiled / ~18.1 s wall): `ops.eigen` **12.1 s (68 %)**
— the model's massed free DOFs equal the requested mode count, so the
eigen policy lands on the dense `fullGenLapack` solver over all ~6k
equations; `ops.analyze` (10 solves) 5.3 s, of which the 6 CR unit-load
solves are 3.3 s (each one re-factorizes a ~6k-dof band matrix);
`_build` x13 only 0.24 s — python overhead is NOISE here, the solver
kernel calls dominate.

**cases** (1.98 s profiled / 1.01 s wall): `run_static` x12 with a full
rebuild each 0.75 s; `_compute_story_props` 0.65 s (24 rebuilds);
`_superpose` x12 0.48 s (repeated `res.member_stations[uid][key][i]`
chains, 700k+ genexpr calls); `_member_outputs` 0.42 s;
`compute_story_masses` per rebuild 0.28 s (+0.27 s `_member` scans).

## What was optimized (and what was NOT)

1. **Elastic-domain reuse** (`_elastic_domain`, the big one).  Linear/
   Newton static case solves and the CR (`_master_rz`) unit-load solves
   now reuse the already-assembled OpenSees domain: remove the previous
   load pattern + time series, `ops.reset()` (revert-to-start), re-load,
   re-analyze — instead of `ops.wipe()` + full element-by-element rebuild
   per solve.  The `frame` run drops from 31 rebuilds to 3; `cases` from
   39 to 3.  Reuse is gated by a module-level ownership token that ONLY
   the two whitelisted flows set and that ANY `_build()` call clears
   first, so hinged/P-Delta/corotational/TH/pushover/eigen builds can
   never be mistaken for a reusable elastic domain, and interleaved
   engines stay isolated (tested).  Verified **bit-identical** empirically
   before the code was written: consecutive reset-reuse solves reproduce
   fresh-build node displacements, reactions and element local forces
   EXACTLY on frame and shell models.  `SKYFRAME_SLOW_PATH=1` preserves
   the rebuild-per-case path (used by the identity tests and available as
   an escape hatch).
2. **Combo superposition lookup hoisting** (`_superpose`).  The per-index
   `sum(f * res.X[uid][key][i] for res, f in parts)` genexprs became a
   pre-fetched column accumulator whose first step is `0.0 + f0*x` —
   exactly `sum()`'s leading `0 +` step (including `-0.0` normalization)
   with the remaining parts added in the same order, so the float results
   are bit-identical by construction.
3. **Pure-geometry memos** in station/deflection recovery: `_local_axes`
   per member uid and `_eff_props` per section name (the model is frozen
   once the engine exists — already the class contract), plus an
   unsplit-member fast path for the per-station segment search (an
   unsplit member's only segment always satisfies the search predicate).

**Deliberately NOT touched**: the eigen solver choice (the shell model's
12 s `fullGenLapack` solve is the dominant cost there, but any solver
change alters the modal floats — out of bounds for a bit-identical pass);
factorization reuse across CR unit-load solves inside OpenSees (same
reason: not reachable without changing solver behavior); numpy
vectorization of superposition (different summation order = different
last-bit floats).

## Identity guarantees (how "bit-identical" was checked)

* One-time, against the ORIGINAL pre-v0.26 engine: full `run().to_dict()`
  JSON of all three profile models, original code vs optimized code —
  `==` on everything except `modal`.
* Continuously, in `tests/test_perf_api.py`: `SKYFRAME_SLOW_PATH=1` vs
  optimized full-dict equality on four model shapes (frame, shell-heavy,
  many-cases, and a tension-only-brace model covering the Newton static
  path), plus engine-interleaving isolation.
* The `modal` block is the ONE exception and it is **pre-existing**: the
  ARPACK eigen solve inside OpenSees is not run-to-run deterministic —
  two runs of the UNMODIFIED engine already differ in the last float
  bits and eigenvector signs (verified on the original code before any
  optimization existed).  Modal periods are asserted to 1e-9 relative;
  the optimization never touches the modal path (`run_modal` always does
  a full rebuild).

## Measured results (median of 3, same machine, same models)

| model | original | optimized | speedup |
| --- | --- | --- | --- |
| frame | 0.574 s | 0.351 s | **1.64x** |
| cases | 0.958 s | 0.600 s | **1.60x** |
| shell | 18.081 s | 17.973 s | 1.01x |

(raw: frame 0.563/0.574/0.590 -> 0.343/0.351/0.365; cases
0.951/0.958/0.980 -> 0.594/0.600/0.614; shell 17.813/18.081/18.138 ->
17.842/17.973/18.093)

The shell number is honest: that model is ~95 % native solver kernels
(dense eigen + band factorizations), which this pass deliberately does
not touch.  The frame/cases shapes — the typical interactive building —
get the 1.6x.

CI guard: `tests/test_perf_api.py::test_optimized_run_is_faster_than_slow_path`
asserts optimized median <= **0.9x** the slow-path median on the frame
model — a deliberately generous bound (measured ~0.65x) so the test stays
stable on noisy runners while still catching a dead optimization.
