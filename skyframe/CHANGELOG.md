# SkyFrame changelog

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
