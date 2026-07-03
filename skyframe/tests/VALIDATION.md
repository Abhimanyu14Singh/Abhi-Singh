# SkyFrame validation matrix

Every expected value is either an **officially published OpenSees example
result** or is **derived in the test itself** from closed-form mechanics or an
independent numpy stiffness/eigen computation — the same independent-hand-
solution methodology CSI uses in the ETABS *Software Verification* manual.
No fabricated numbers.

Run: `cd skyframe && python3 -m pytest tests/ -v` → **22 passed**.

## 1. Solver validation (`test_opensees_install.py`) — OpenSees itself

| Benchmark | Source | Assertion |
|---|---|---|
| Basic Truss Example (3-bar truss, E=3000 ksi) | OpenSees wiki, official example; published output | node-4 `ux = 0.53009277 in`, `uy = -0.17789364 in` @ 1e-6 rel; plus independent numpy stiffness solution @ 1e-10 |
| Basic Truss reactions | static equilibrium | ΣFx = −100 kip, ΣFy = +50 kip @ 1e-8 |
| 2-DOF spring-mass chain eigenvalues | closed-form 2×2 (K, M) via `numpy.linalg.eigvalsh` | `ops.eigen` matches @ 1e-8 rel |

## 2. Engine validation (`test_analytical_benchmarks.py`) — SkyFrame vs independent solutions

| Benchmark | Independent solution | Assertion |
|---|---|---|
| Cantilever column, tip load (ETABS-verification style) | δ = PL³/3EI, θ = PL²/2EI | @ 1e-3 rel; base FX = −P, \|MY\| = P·L |
| Simply supported beam, UDL | δ_mid = 5wL⁴/384EI, R = wL/2, M_mid = wL²/8 | @ 1e-3 rel (reactions @ 1e-6) |
| Fixed-base portal frame, lateral load | full 2D frame stiffness hand-assembled in numpy (axial + flexure, 6 free DOFs) | displacements @ **1e-8 rel** (independent-code cross-validation) |
| 3-story shear building, modal | tridiagonal shear-building eigenproblem via numpy | each period matched within 1 %; Σ UX participation ≥ 0.999 |
| SDOF cantilever + tip mass | T = 2π√(m/(3EI/L³)) | @ 0.1 %; participation closure Σux = Σuy = 1 |
| Load combination superposition | linearity | combo == Σ factor·case @ 1e-10 |
| Whole-building equilibrium (default `quick_building()`) | statics | DEAD base FZ = Σw·L @ 1e-6 rel; EQX base FX = −V; Story1 shear = V |

## 3. Model-layer validation (`test_model_layer.py`)

Member-count formulas across three building configurations, story elevations,
story mass = Σw·L/g hand calc, explicit mass override, equivalent-static
pattern total V = C·W with F_i ∝ m_i·h_i, rectangular section A/I33/I22
exact and J vs Roark's β·s⁴ (0.2 %), G = E/2(1+ν), error paths on unknown
material/section/pattern/case names, `to_dict()` JSON-safety and required
keys per `CONTRACT.md`.

## Relationship to ETABS

CSI's ETABS *Software Verification* examples validate the program against
independent hand calculations of exactly the classes tested above (cantilever
and frame statics, eigenvalue problems). SkyFrame's suite reproduces that
methodology in executable form: the solver (OpenSees) is pinned to its own
published example outputs, and the SkyFrame modelling layer is pinned to
independent closed-form/numpy solutions. Any model that ETABS and OpenSees
both solve correctly will agree with SkyFrame within linear-analysis
tolerances, because all three are validated against the same mechanics.
