# SkyFrame changelog

## 1.9.0

### Wave 24 — code breadth
- **Eurocode design**: EN 1993-1-1 steel checks (cross-section, exact
  Table 6.1/6.2 buckling curves, §6.3.3 interaction with Annex B
  factors) and EN 1992-1-1 concrete checks (0.8x stress block with
  material factors, VRd,c/VRd,s/VRd,max) selectable per check —
  AISC 360 / ACI 318 remain the bit-identical defaults.
- **NBCC 2020 lateral loads**: auto wind (Iw·q·Ce·Cg·Cp with exposure
  power laws) and seismic ELF (piecewise design spectrum, V floor/cap,
  Ft top force) pattern generators.
- **AISC 341 seismic checks**: strong-column/weak-beam ratios and
  panel-zone demand/capacity at every beam-column joint.
- **Wind on shell surfaces**: per-region Cp with an auto pattern
  (exact q·Cp·area resultants, wall normals from mesh tributary areas).
- **Composite camber recommendation**: 0.8·dead deflection floored to
  5 mm steps with industry thresholds.
- 559 tests (27 new).

## 1.8.0

### Wave 23 — meshing & advanced shells
- **Auto edge constraints**: turn on one switch and mismatched shell
  meshes (and frame ends landing mid-edge) zip together via stiff
  interpolation tie chains — validated by a two-region wall matching
  its monolithic twin to ~5% (vs +19% disconnected) and exact patch
  equilibrium.
- **Nonlinear layered shell walls**: per-layer concrete/steel shell
  sections (LayeredShell with real tension-softening concrete laws) for
  pushover and nonlinear time history; genuine cracking and post-peak
  softening, elastic analyses unchanged.
- **Line springs & compression-only area springs**: grounded soil lines
  under wall edges and subgrade area springs under slabs, both with
  uplift-releasing (compression-only) behavior — reactions match
  tributary closed forms exactly.
- **Semi-rigid diaphragm load distribution**: story forces on
  diaphragm-free floors distribute over the slab mesh automatically,
  with the accidental-torsion couple field preserved (base shear and
  torque match the rigid-diaphragm run to 1e-9).
- 532 tests (25 new).

## 1.7.0

### Wave 22 — section designer, fiber hinges & seismic devices
- **Section Designer**: arbitrary polygon sections (holes + discrete
  rebar) with exact shoelace properties, transformed-section frame
  properties, and P-M interaction surfaces (ACI strain compatibility
  for concrete via exact Whitney-block polygon clipping; full-plastic
  surfaces for steel) — designer sections mirror into ordinary frame
  sections and drive analysis, checks, and fiber hinges.
- **Fiber PMM hinges**: members flagged "fiber_pmm" run asce41
  pushovers as force-based elements with HingeRadau fiber hinge zones
  (lp = 0.5h) built from the designer section, the steel W library, or
  rectangular-RC + perimeter bars; per-step curvature-based plastic
  rotations classified against the ASCE 41 acceptance tables.
- **Device library II**: single friction pendulum (singleFPBearing,
  F = mu·N + N·d/R), triple friction pendulum (TripleFrictionPendulum,
  fully-sliding tangent W/(R2+R3) exact) and multilinear backbone links
  (exact at every backbone point), on the v0.15 link machinery.
- 507 tests (26 new).

## 1.6.0

### Wave 21 — composite floors & slab design
- **Composite beam design** (AISC 360-16 Ch. I3): effective width per
  I3.1a from the real floor geometry, full and partial composite
  strength through all three plastic-neutral-axis cases, I8.2a stud
  strengths, unshored pre-composite checks, and lower-bound-inertia
  live-load deflections — per-beam D/C tables with stud counts and
  percent-composite.
- **RC slab strip design**: ETABS-style column/middle strips derived
  from the support lines, strip moments integrated from the shell
  results, and required reinforcement from an exact closed-form
  inversion of the Whitney block (with 0.0018bh minimums and spacing
  caps), with per-region strip diagrams.
- **Walking vibration** (AISC Design Guide 11): fn = 0.18√(g/Δ) on the
  sustained-load deflection and the Eq. 4-1 peak-acceleration check
  with selectable occupancy limits; failing beams pulse in the 3D view.
- 481 tests (22 new).

## 1.5.0

### Wave 20 — nonlinear production tools
- **Automatic ASCE 41-17 plastic hinges**: flag members "Auto M3" and a
  pushover case in asce41 mode builds trilinear moment-rotation
  backbones from the code tables (steel Table 9-7.1 with compactness
  interpolation, concrete beams Table 10-7), tracks every hinge's
  rotation, moment, and acceptance state (IO/LS/CP/collapse) per step,
  and shows state chips in the pushover results.
- **Performance point**: ASCE 41 §7.4.3 coefficient method — equal-area
  bilinear idealization and target displacement δt overlaid on the
  capacity curve with the full C0/C1/C2 coefficient readout and hinge
  state counts at δt.
- **Pattern (skip) live loading**: one click derives odd/even-span live
  patterns along every continuous beam run plus a PATTERN-LL envelope
  combo (1.2D + 1.6L arrangements) so beam design reads the worst
  arrangement automatically.
- **Auto construction sequence**: one-click story-by-story staged
  gravity case.
- 458 tests (23 new).

## 1.4.0

### Wave 19 — concrete wall & slab design, drift optimization
- **Shear wall design** (ACI 318): uniform-reinforcing pier PMM check by
  strip strain compatibility, §11.5.4.3 shear strength with both α_c
  branches and the 0.66√f'c cap, and §18.10.6.3 stress-based
  boundary-element triggers — per-pier D/C tables with boundary badges
  and CSV export.
- **Punching shear checks**: two-way shear at every column supporting a
  meshed slab (critical section b0 at d/2, min-of-three ACI Table
  22.6.5.2 stresses), with plan halo glyphs on failing columns.
- **Drift optimizer**: unit-load virtual-work diagrams rank each
  member's contribution to roof drift (verified by the unit-load
  theorem to machine precision), colored in the 3D viewer with a
  top-10 table.
- 435 tests (32 new).

## 1.3.0

### Wave 18 — vertical seismic component & panel zones
- **Vertical seismic component Ev**: the ASCE 7 auto-combination
  generator accepts SDS and folds Ev = 0.2·SDS·D into the seismic
  combos per §12.4.2.3 ((1.2+0.2·SDS)D / (0.9−0.2·SDS)D and the ASD
  analogues), with a live factor preview in the auto-combos card.
- **Panel zones**: ETABS-style beam-column joint modeling — automatic
  rigid end zones (half the deepest connecting member depth) or an
  elastic scissors panel spring (Krawinkler K = G·dc·db·tp via a
  duplicated joint node), selectable per model with joint glyphs in
  the plan and 3D views. Centerline remains the default and is
  bit-identical to prior releases.
- 403 tests (25 new).

## 1.2.0

### Wave 17 — serviceability & design depth
- **Beam deflection recovery**: exact per-member deflection lines from
  the statics moment field, a deflection diagram in the member panel,
  and a Serviceability tab with L/limit checks (editable limit).
- **ASCE 7-16 §4.7 live-load reduction**: per-column tributary factors,
  applied as a design-stage demand reduction with an on/off toggle.
- **Biaxial concrete columns**: Bresler reciprocal-load method with a
  load-contour fallback, method chips in the design table.
- 378 tests (22 new).

## 1.1.0

### Wave 16 — seismic protection devices & wall piers
- **Link device types**: viscous dampers (time-history damping, zero
  static stiffness), gap/hook contacts, and bilinear base isolators —
  with device glyphs and per-type parameter forms in the UI. Verified
  against closed forms and an independent numpy bilinear integrator.
- **Wall piers**: pier labels on walls (or auto-label) produce per-story
  P/V/M design forces from an exact nodal free-body cut of the shell
  elements, with a Wall Piers results tab, CSV and report section.
- 356 tests (16 new).

## 1.0.0

First stable release. SkyFrame is a personal ETABS-style building analysis
studio on the open-source OpenSees solver, delivered as a self-contained
desktop app. Every analysis feature is pinned by a closed-form or published
benchmark — **340 tests**.

### Modelling
- Plan and elevation drawing: columns, beams, braces, walls, slabs
- **Multiple grid systems**: orthogonal, rotated (angled wings), and radial
- Shell walls & slabs (ShellMITC4) with auto quad meshing and **openings**;
  frame–shell node compatibility
- Membrane slabs with two-way tributary load distribution
- Rigid and semi-rigid (none) diaphragms, per-story override
- Point spring supports, **Winkler elastic foundations**, link elements
- Member end releases, orientation angle, rigid end-offsets,
  tension/compression-only behaviour
- Section & material managers, stiffness modifiers, steel W-shape library

### Loads
- Point / partial / trapezoid member loads, area loads
- Self-weight, thermal, wind (ASCE 7), ELF, notional (AISC), accidental
  torsion, mass source
- Named response-spectrum / time-history function library (+ ASCE 7 & EC8
  presets), automatic ASCE 7 LRFD/ASD load-combination generation

### Analysis (OpenSees)
- Linear static, additive & envelope combinations, modal
- Response spectrum (CQC/SRSS, §12.5 directional), linear & nonlinear
  time history, P-Delta, nonlinear static pushover, staged construction
- **Linear buckling** (geometric-stiffness eigenproblem)

### Design
- Preliminary AISC 360 steel & ACI 318 concrete checks over all combos
- **Auto section optimization** (lightest passing W-shape)

### Results & output
- Story drifts/shears, base reactions, 11-station member force diagrams
- Deformed / mode / buckling animation, shell force contours
- Center of mass & rigidity, story stiffness, ASCE 7 §12.3 irregularity
- **Section cuts**, gravity load takedown, printable report, CSV export

### Interop & delivery
- DXF, ETABS `.e2k`, and IFC geometry import
- Native desktop app (pywebview) + local standalone build
  (`packaging/build.sh`), no cloud CI required
