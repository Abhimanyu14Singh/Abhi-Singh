# SkyFrame vs ETABS v22: parity matrix re-audit (2026-10-12)

Source: `skyframe/docs/etabs_parity_matrix.json` (232 rows). Output: `matrix.json` next to this file (242 rows: 232 re-verified plus 10 new). The rule is strict: a row is full only when the engine and the UI both exist, and the gap is empty. A row that covers only part of the ETABS feature is partial. `pending: true` marks the three buckling/scalability rows that another branch is still landing; they are unchanged. `flag` marks values entered from memory or not verified (see docs/REVIEW_FLAGS.md).

## Counts by status

| Status | Before (JSON) | After |
|---|---|---|
| full | 163 | 152 |
| partial | 34 | 64 |
| missing | 18 | 5 |
| beyond | 7 | 11 |
| out_of_scope | 10 | 10 |
| total | 232 | 242 |

docs/ETABS_PARITY.md still shows the original 2026-10-11 audit (124 / 49 / 42 / 7 / 10). The JSON had already been partly updated by later merges.

Status changes on the original 232 rows: 34 rows went up to full (23 from partial, 11 from missing), 5 went from missing to partial, and 1 (Rhino/Grasshopper) went from missing to beyond. 45 rows went down from full to partial. Of those, 40 were rated full but still listed a gap, and 5 were rated full with no gap. Checking the code for these rows found real limits: section-cut, pier and spandrel forces are computed only for static cases and additive combos; time-history cases have no shell forces or stresses; floor_mesh exists only in the engine; and there are no distributed frame moments and no local-1 or local-3 distributed loads.

Rows with a flag: 46.

## Remaining partial and missing rows (exact gaps)

- **[partial] Interface & workflow / General (arbitrary line) grids**: General grids are not written to E2K and cannot be the primary grid system; editing/dimension tools for them are plan-view only
- **[partial] Interface & workflow / Model explorer tree**: No right-click context menu in the Model Explorer tree (ETABS edits definitions and objects from the tree's context menu)
- **[partial] Interface & workflow / Edit shells: divide, merge, chop, expand/shrink, reshape**: Divide / merge / chop / expand-shrink refuse shells that contain openings; the vertex reshaper works in plan view only
- **[partial] Interface & workflow / Snap options**: Object snaps work in the plan view only (elevation and 3D drawing use grid snap); no extension or parallel snaps
- **[partial] Interface & workflow / Display tables (Show Tables)**: Far fewer tables than ETABS. Missing: Joint Velocities / Accelerations, Assembled Joint Masses, Mass Summary by Story, Material List / takeoff, Story Max/Avg Displacements, a Modal Load Participation table in the catalogue, nonlinear-link rows in Element Forces - Links (device links are only warned)
- **[partial] Interface & workflow / Interactive database editing**: No tables for section cuts, temperature loads (frame/shell/joint), ground displacements, P-Delta / active-DOF / analysis / damping options, hyperstatic cases, towers, panel zones, nonprismatic / built-up / Section Designer sections, joint patterns, frame hinge assignments, floor_mesh options; staged-construction table holds only name + pattern (user stages not editable)
- **[partial] Interface & workflow / Units**: No per-quantity custom units or decimal-places control (presets only)
- **[partial] Interface & workflow / Save / open / recent / file format**: No auto-save / backup versions
- **[partial] Geometry objects / Pier labels**: Pier labels on frames (column-as-pier) not supported; pier forces cover in-plane P, V, M only
- **[partial] Geometry objects / Floor auto-mesh**: floor_mesh options (cookie-cut, mesh at beams/walls/grids) are engine/JSON-only: no dialog and no DB-table column; no 'no auto mesh (as drawn)' option
- **[partial] Geometry objects / Auto edge constraints**: Model-wide switch only; no per-shell Assign > Shell > Auto Edge Constraints
- **[partial] Properties / Orthotropic / anisotropic materials**: No anisotropic (normal-shear coupled) materials; no material angle (material axes fixed to the shell element local axes); frames use only E1 / G12
- **[partial] Properties / Material libraries**: No Chinese (GB), Canadian (CSA), Japanese, Korean, Russian or other regional sets
- **[partial] Properties / Steel section database**: No Chinese, Japanese, Korean, Canadian (CISC) or Russian section tables
- **[partial] Properties / Section Designer**: No moment-curvature analysis / plot output
- **[partial] Properties / Layered / nonlinear shells**: Only concrete and steel layer kinds; layer stresses in linear runs are an approximation from a homogeneous section
- **[partial] Properties / Link: linear**: No coupled 6x6 stiffness matrix
- **[partial] Properties / Link: multilinear elastic**: Per-DOF definition limited to the shear pair + vertical kv (no independent U1..R3 curves)
- **[partial] Properties / Link: multilinear plastic (kinematic, Takeda, Pivot)**: No Degrading, Isotropic, Concrete or BRB-hardening hysteresis options on the multilinear plastic link
- **[partial] Properties / Link: rubber isolator**: No high-damping rubber (HDR) isolator model
- **[partial] Properties / Link: friction isolator (single/double/triple pendulum)**: Velocity-dependent (slow/fast) friction not exposed
- **[partial] Properties / Link: T/C friction isolator**: Isotropic friction and radius (no per-direction mu, R); no friction in tension
- **[partial] Properties / Auto hinges ASCE 41-17 (frames)**: Column P-M2-M3 hinges hold P at the gravity value (no axial variation during the push); ASCE 41-23 tables reuse the 41-17 numbers
- **[partial] Properties / Auto hinges other codes (FEMA 356, Caltrans, EC8-3, TBI)**: Caltrans and TBI hinge tables not implemented
- **[partial] Properties / User-defined hinge properties**: Parametric PMM hinges hold P at its gravity value; M2-M3 yield contour is an ellipse through the 0/90 degree curves; no strength drop past C for interacting hinges
- **[partial] Loads / Auto seismic Eurocode 8**: No national-annex parameter sets (user enters NA values)
- **[partial] Loads / Auto seismic NBCC**: Edition-specific Mv / J tables simplified
- **[partial] Loads / Auto seismic other codes (TSC 2018, KDS 2022, Mexican, Philippine NSCP, IBC legacy, UBC)**: No legacy IBC / UBC / ASCE 7-10 generators and none for other ETABS-listed codes (e.g. Italian NTC, Colombian NSR-10, Peruvian E.030, Chilean NCh433, SNI, SBC)
- **[partial] Loads / Accidental eccentricity (static)**: Per-diaphragm / per-story eccentricity overrides exist for RS cases only, not for static seismic load patterns
- **[partial] Loads / Auto wind other codes (GB 50009, TSC, KDS, Mexican, etc.)**: No BS 6399, API 4F, Italian NTC or other remaining ETABS wind codes; GB 50009 beta_z uses a linear mode shape
- **[partial] Loads / Frame loads (distributed, point, moment; local/global/projected)**: No distributed (uniform / trapezoidal) moment loads; distributed forces only in gravity, local 2 and global X/Y/Z (no local 1 or local 3 direction)
- **[partial] Loads / Live load reduction**: Beams are not reduced; pier tributary area is plan area / vertical elements unless given
- **[partial] Loads / Response spectrum functions: ASCE 7**: USGS ordinates are typed by the user (no site-coordinate lookup)
- **[partial] Loads / Response spectrum functions: other codes (IS 1893, NBCC, NZS 1170.5, AS 1170.4, GB 50011, etc.)**: Only IS 1893, NBCC 2020, NZS 1170.5, AS 1170.4 and GB 50011 beyond ASCE/EC8: no TSC 2018, KDS, NSCP, Mexican, Italian NTC or older-edition spectra, although several of these exist as auto-seismic generators
- **[partial] Loads / Response spectrum functions: user / from file**: No 'from file' import for RS functions (points typed or pasted); no frequency-vs-value input
- **[partial] Analysis / Modal analysis (eigen)**: No user frequency shift / cutoff or convergence-tolerance controls on the eigen case
- **[partial] Analysis / Linear time history - modal superposition**: Not a distinct load case type: run on demand from the TH results tab (FNA with no device nonlinearity), not part of Run All and not referenceable in combinations
- **[partial] Analysis / Nonlinear direct-integration time history**: Initial conditions only from the case's own gravity stage or a load-control nonlinear static chain without hinges; cannot continue from staged construction or a hinged nonlinear static case
- **[partial] Analysis / Fast Nonlinear Analysis (FNA)**: Takeda / Pivot multilinear links, BRB, bilinear damper and T/C friction isolator links are not supported in FNA; hinged cases raise; FNA is run on demand from the TH results tab rather than as its own load case type
- **[partial] Analysis / Damping options**: Composite (material-proportional) ratios not applied to RS or steady-state cases
- **[partial] Analysis / Pushover control (displacement / load), multiple states**: Control DOF limited to UX/UY at a joint/story (no other DOFs, no generalized displacement)
- **[partial] Analysis / Pushover target displacement: ASCE 41 coefficient method**: Cm fixed at 1.0; TL long-period branch omitted
- **[partial] Analysis / Pushover: capacity spectrum (ATC-40 / FEMA 440 EL)**: FEMA 440 displacement modification (improved coefficient method) not offered as a separate method
- **[partial] Analysis / Staged construction with creep/shrinkage**: Shells get modulus ageing only (no creep / shrinkage strain); members with end releases creep axially only; step method needs user-defined stages (no per-story auto sequence)
- **[partial] Analysis / Linear buckling** (pending): Include shells (geometric stiffness of plates) and links
- **[partial] Analysis / Buckling from a stressed / staged state**: Frames only (shells and links excluded), as for linear buckling
- **[missing] Analysis / BucklingFEM (shell-FEM member buckling)** (pending): Research-grade; defer
- **[partial] Analysis / Set load cases to run / run control**: No model lock / unlock after analysis
- **[partial] Analysis / Large-model scalability** (pending): Benchmark and optimize 50k-200k DOF models (sparse eigen, multithreaded MUMPS, out-of-core), sparse stability check
- **[partial] Results & output / Deformed shape and animation**: No video / animation export (ETABS Create Video)
- **[partial] Results & output / Frame force diagrams**: Link force diagrams not drawn in 3D; no pier / spandrel force diagrams on the model
- **[partial] Results & output / Shell force contours**: Time-history cases record no shell forces, so no TH shell contours / envelopes; corner values average element centroids (no Gauss-point extrapolation)
- **[partial] Results & output / Shell stresses (S11, S22, S12, top/bottom, von Mises)**: No stresses for time-history cases; stresses derived from resultants (+-6M/t^2), layer stresses in linear runs are an approximation; ribbed/deck stresses use the weight-equivalent thickness
- **[partial] Results & output / Section cut forces**: Computed for static cases and additive combos only: no RS, TH, nonlinear, envelope or staged section-cut forces
- **[partial] Results & output / Pier forces**: Static cases and additive combos only (no RS / TH / nonlinear / envelope pier forces); in-plane P, V, M only (no V3, T, M2); no pier force diagrams
- **[partial] Results & output / Spandrel forces**: Static cases and additive combos only (no RS / TH / envelope); no 3D spandrel force diagram
- **[partial] Results & output / Export to Microsoft Access**: No native Access .mdb/.accdb file (needs the proprietary Jet/ACE engine)
- **[partial] Interoperability / ETABS .e2k import**: ETABS nonlinear-static / pushover / staged / frequency cases, hinge assignments, nonlinear link and spring properties, thermal loads and code auto-lateral records (only 'User Loads' story forces) are not read natively; unknown records become warnings
- **[partial] Interoperability / ETABS .e2k export**: Pushover / staged / NL static / frequency / hyperstatic cases, hinges, nonlinear links, thermal loads, section cuts, panel zones etc. travel only as $SF comments that ETABS ignores; RS mode counts / GMC / DSC options and TH damping/DI options are not written as ETABS records
- **[partial] Interoperability / DXF/DWG import**: No DWG (binary) import
- **[partial] Interoperability / Revit link**: A native Revit add-in (CSiXRevit equivalent) is a separate product needing the Revit API on Windows; cannot be built or tested here (out of scope)
- **[partial] Interoperability / Steel interop (Tekla, CIS/2, SDNF, IDEA StatiCa)**: No native Tekla link; SDNF / CIS/2 / IDEA IOM are documented subsets
- **[missing] Analysis / Link effective (linear) stiffness and damping**: Add k_eff / c_eff per DOF to link properties and use them in modal, RS and linear cases
- **[missing] Analysis / Generalized displacements**: Add generalized displacement definitions, results, plot-function traces and pushover monitoring
- **[partial] Analysis / Response spectrum modal damping options**: No interpolated, proportional or per-mode override damping for RS cases
- **[missing] Loads / Diaphragm design forces (ASCE 7 §12.10 Fpx)**: Add an Fpx diaphragm load-pattern generator (12.10.1 and the 12.10.3 alternative)
- **[partial] Results & output / Joint velocity / acceleration output tables**: No Joint Velocities / Joint Accelerations tables in Show Tables / export
- **[partial] Results & output / Mass output tables (Mass Summary by Story, Assembled Joint Masses)**: No Mass Summary by Story or Assembled Joint Masses table
- **[missing] Results & output / Material list / quantity takeoff**: Add Material List tables (by story, section, object type)

## Beyond ETABS

- **Command search palette and keyboard-first workflow**: Ctrl+K command palette over every menu act, F1 shortcut overlay, quick-access toolbar  
  ETABS: No command palette (ribbon/menus, limited shortcuts).
- **Guided model workflow checklist**: Help > Model Workflow: next-step checklist  
  ETABS: None.
- **Accuracy advisor and mesh sensitivity study**: Accuracy Advisor (modelling-choice warnings) and Mesh Sensitivity Study with refinement + Richardson extrapolation  
  ETABS: None (user judgement).
- **Automatic model repair**: Analyze > Fix Model: merge, split at crossings, clean duplicates with preview  
  ETABS: Only Merge Joints / Check Model warnings.
- **Load takedown / gravity reaction summary**: Gravity takedown per support grouped by grid with balance check, bubble plan  
  ETABS: No dedicated takedown (reactions + tributary tables only).
- **Model results comparison tooling**: Tolerance comparator for result JSON (used in equivalence tests; not user-facing)  
  ETABS: None built in. Caveat: Python / test utility only: no endpoint or UI ('compare runs')
- **Rhino / Grasshopper interoperability**: Rhino .3dm import (rhino3dm), live-linked .3dm file with change detection / auto apply, and a Grasshopper plugin over the skyframe.exchange/1 schema: layer mapping rules, UserText roles, arcs/NURBS to curved frames, merge/replace sessions, model repair, results (displacements, forces, reactions, periods, drifts) back to Grasshopper  
  ETABS: No first-party Rhino/Grasshopper link (third-party tools via OAPI).
- **Open model file format**: Model is plain JSON (.skyframe) with documented schema (CONTRACT)  
  ETABS: Binary .EDB (text .e2k export available).
- **ETABS-OAPI-compatible Python facade plus versioned REST / OpenAPI**: skyframe.oapi runs ETABS-style SapModel scripts (File, PropMaterial, PropFrame, PropArea, PointObj, FrameObj, AreaObj, Story, LoadPatterns, LoadCases, RespCombo, Analyze, Results with CSI return codes and comtypes-style byref lists) on any OS without ETABS; every endpoint also served under /api/v1 with a generated OpenAPI 3 document  
  ETABS: CSI OAPI is a Windows COM/.NET interface (Python via comtypes) to a running ETABS; no REST service or machine-readable API schema.
- **Exact numpy section solver (J, Cw, shear centre, shear areas)**: In-house constrained-Delaunay Tri6 warping solver (numpy only) giving exact J, Cw, shear centre and shear areas for parametric, database, built-up and Section Designer outlines, with fillets and multi-material modular ratios; live readout in the section editors  
  ETABS: ETABS reports section properties from its own internal routines; no user-visible solver or mesh-convergence control for arbitrary built-up / parametric shapes.
- **Cross-platform and headless operation**: Flask server + browser SPA on Windows, macOS and Linux; native webview desktop launcher, --server-only headless mode for CI / remote use; pure-Python client  
  ETABS: Windows-only desktop application.

## New rows added

- [missing] Analysis / Link effective (linear) stiffness and damping
- [missing] Analysis / Generalized displacements
- [partial] Analysis / Response spectrum modal damping options
- [missing] Loads / Diaphragm design forces (ASCE 7 §12.10 Fpx)
- [partial] Results & output / Joint velocity / acceleration output tables
- [partial] Results & output / Mass output tables (Mass Summary by Story, Assembled Joint Masses)
- [missing] Results & output / Material list / quantity takeoff
- [beyond] Interoperability / ETABS-OAPI-compatible Python facade plus versioned REST / OpenAPI
- [beyond] Properties / Exact numpy section solver (J, Cw, shear centre, shear areas)
- [beyond] Interface & workflow / Cross-platform and headless operation
