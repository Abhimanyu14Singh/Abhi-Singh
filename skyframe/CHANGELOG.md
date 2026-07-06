# SkyFrame changelog

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
