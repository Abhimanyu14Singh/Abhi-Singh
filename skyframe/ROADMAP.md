# SkyFrame → ETABS-parity roadmap

Goal: the core ETABS workflow — **model → draw → assign → analyze → review results**
— on the open-source OpenSees solver, with every analysis feature pinned by
scrutinized benchmark tests.

## Phase 1 — Analysis core: shells, meshing, member analysis  ✅ done
- [x] 3D elastic frames, rigid diaphragms, static cases + combos, modal (v0.1)
- [x] Shell walls & slabs (ShellMITC4 + ElasticMembranePlateSection)
- [x] Auto-mesher: structured quads, node dedup, **frame–shell edge compatibility**
      (beams/columns split to match mesh nodes; results re-aggregated per member)
- [x] Membrane slabs: ETABS-style two-way 45° tributary distribution to edge beams
- [x] Member end releases (moment, either end) via release condensation
- [x] Member loads: point, partial UDL, trapezoid — *exact* fixed-end-force
      treatment, not approximations
- [x] 11-station internal force diagrams (N, V2, V3, T, M2, M3) per member
- [x] Area loads (kPa) on slabs, consistent nodal loading for meshed shells
- [x] `POST /api/model` full-model round-trip (draw → solve)

## Phase 2 — ETABS-style drawing UI  ✅ done
- [x] Draw mode: plan-view story editor with grid snapping
- [x] Tools: Select, Column, Beam, Wall, Slab, Erase (keyboard shortcuts)
- [x] Story selector + "apply to all stories"
- [x] Assignments panel: frame section, releases, line loads; shell section,
      behavior (shell/membrane), mesh size, area loads — per load pattern
- [x] Section & material manager
- [x] 3D: translucent shells, mesh display, deformed shells
- [x] Click-a-member N/V/M diagram panel

## Phase 3 — Integration & scrutinized validation  ✅ done (42 tests green)
- [x] Deep member-analysis suite: fixed-end moments, releases (3wL/8, wL²/8),
      triangular-load maxima (wL²/9√3), continuous beam vs three-moment equation,
      station diagram closed forms
- [x] Shell suite: membrane patch test, Navier plate deflection series,
      Timoshenko deep-beam wall, Cook's skewed membrane, mesh-convergence
      assertions, exact load conservation
- [x] End-to-end: draw → save → mesh → analyze → diagrams, verified in browser
- [x] All suites green in CI-style run; docs updated

## Wave 5 — ETABS gap closure I  ✅ done
- [x] Envelope load combinations (min/max)
- [x] Mass source (e.g. DEAD + 0.25·LIVE)
- [x] Stiffness modifiers (cracked sections; frame + shell)
- [x] Auto wind lateral pattern (ASCE 7-style Kz profile)
- [x] Linear time-history analysis (Newmark, Rayleigh damping)
- [x] Column orientation angle
- [x] Shell internal forces + 3D force contours
- [x] Grid & story editors; brace drawing tool (with X-pair)
- [x] Printable report generator; CSV export everywhere
- [x] Wind / time-history / envelope / mass-source / modifier UIs

## Wave 5.5 — parallel modules  ✅ done
- [x] Preliminary steel design checks (AISC 360 elastic interaction, clearly
      labelled preliminary — not a stamped-design replacement)
- [x] DXF import (grid + members from LINE entities on named layers)

## Wave 6 — ETABS gap closure II  ✅ done
- [x] Wall/slab openings (mesher + model + UI)
- [x] Elevation-view drawing mode
- [x] Nonlinear static pushover (displacement-controlled, fiber/plastic hinges)
- [x] Semi-rigid diaphragm option
- [x] Link elements (linear springs/dampers between points)

## Wave 7 — remaining gaps  ✅ done (engine + modules + UI)
- [x] Staged construction: sequential story-by-story gravity application
      with comparison to one-shot analysis (+ case editor UI)
- [x] Concrete design (preliminary): rebar input, ACI 318 flexural + shear
      capacity checks, demand/capacity table (+ Design tab UI)
- [x] ETABS .e2k import (stories, points, line objects, sections, patterns)
      with import warnings (+ Import dialog UI)
- [x] Nonlinear time history (Steel01 hinges, Newmark + Newton) — validated
      on SDOF elastoplastic benchmark (+ nonlinear toggle UI)
- [x] IFC geometry reader (storeys, columns/beams, rectangular extrusions)
- [x] REST API for all design checks and importers; UI panels for each

## Wave 8 — ASCE 7 code tools  ✅ done
- [x] Self-weight loads (real member + shell density) with UI card
- [x] ASCE 7-16 design response spectrum + code RS case (live preview UI)
- [x] Automatic LRFD / ASD load-combination generation (UI card)
- [x] Equivalent-lateral-force seismic pattern (UI card)
- [x] REST endpoints + Loads-editor "Code tools (ASCE 7)" section
- [x] Local standalone build (packaging/build.sh) — no cloud CI needed

## Wave 9 — foundations & seismic diagnostics  ✅ done
- [x] Point spring supports (6-DOF foundation springs) + draw tool/glyph
- [x] Accidental torsion (ASCE 7 §12.8.4 story torque at ±ecc)
- [x] Temperature (thermal axial) loads on members
- [x] Center of mass / center of rigidity per story + plan display
- [x] REST endpoints; all fields round-trip through /api/model

## Wave 10 — rigid offsets, irregularity, design envelope  ✅ done
- [x] Rigid end-offsets (beam/column rigid zones via rigid links)
- [x] Story lateral stiffness (V/drift) output
- [x] ASCE 7 §12.3 torsional-irregularity + soft-story diagnostics
- [x] Design over all load combinations (governing combo per member)
- [x] UI: offset inputs + glyph, diagnostics chips, design envelope toggle

## Wave 11 — advanced analysis  ✅ done
- [x] Linear buckling analysis (numpy geometric-stiffness, Euler-validated)
- [x] Response-spectrum directional combination (ASCE 7 §12.5: 100/30, SRSS)
- [x] Notional loads (AISC direct-analysis stability)
- [x] UI: buckling tab with animated mode shapes, RS-directional + notional cards

## Wave 12 — foundations & load takedown  ✅ done
- [x] Winkler elastic foundations on members (soil springs, Hetenyi-validated)
- [x] Gravity load takedown per support, grouped by grid, with balance check
- [x] UI: foundation assignment + soil glyph, takedown tab with bubble plan

## Wave 13 — design automation  ✅ done
- [x] Auto steel section optimization (lightest passing W-shape per member)
- [x] Tension / compression-only members (braces, cables, ties; nonlinear)
- [x] UI: optimize panel with apply, axial-behavior property + badge

## Wave 14 — section cuts & function library  ✅ done
- [x] Section cuts (integrate member forces across a plane; statics-validated)
- [x] Named response-spectrum / time-history function library (+ EC8 preset)
- [x] UI: cut manager + 3D plane + forces tab, function library manager

## Wave 15 — grid systems  ✅ done
- [x] Multiple grid systems (orthogonal + rotated wings + radial)
- [x] Origin/rotation transforms, cross-system snapping (backward compatible)
- [x] UI: grid-system manager, multi-grid render + snap in plan/3D

## Wave 16 — protection devices & wall piers  ✅ done (v1.1)
- [x] Viscous damper / gap / hook / isolator link types (device glyph UI)
- [x] Wall pier labels with per-story P/V/M design forces + results tab

## Wave 17 — serviceability & design depth  ✅ done (v1.2)
- [x] Exact beam deflection recovery + L/limit serviceability checks
- [x] ASCE 7-16 §4.7 live-load reduction (design-stage, per-column)
- [x] Biaxial concrete columns (Bresler / load contour)

## Wave 18 — vertical seismic & panel zones  ✅ done (v1.3)
- [x] Vertical seismic component Ev = 0.2·SDS·D in ASCE 7 auto-combos
- [x] Panel zones: automatic rigid joint zones + elastic scissors springs

## Phase 4 — Parity increment 2  ✅ done
- [x] 1. Model save/open (.skyframe JSON files) with File menu + gallery
- [x] 2. Load pattern / case / combo editor UI (with per-case P-Delta toggle)
- [x] 3. Response-spectrum analysis (CQC/SRSS, spectrum editor + live preview)
- [x] 4. P-Delta (OpenSees PDelta transforms, two-stage gravity solve)
- [x] 5. Steel section library (22 AISC W-shapes + picker UI)
- [x] 6. Wall/slab openings; elevation-view drawing (Wave 6)
- [x] 7. Nonlinear static pushover (OpenSees fiber hinges) (Wave 6)

## Status — v1.8 released 🎉
All waves above are complete with engine + REST API + UI, pinned by a
532-test suite. The app builds standalone locally (`packaging/build.sh`).

## Scheduled waves toward ETABS v23 parity
Driven by `docs/ETABS23_GAP_MATRIX.md` (compiled from CSI ETABS v23 docs
and release notes). No gap is skipped — everything below is scheduled.

### Wave 19 — concrete wall & slab design (top-2 gaps)  ✅ done (v1.4)
- [x] Shear wall design: pier PMM (uniform reinforcing), ACI 318 shear,
      §18.10.6.3 boundary-element check, D/C table
- [x] Punching shear checks at columns (ACI 318 two-way shear, b0 at d/2)
- [x] Energy / virtual-work drift-optimization diagram

### Wave 20 — nonlinear production tools  ✅ done (v1.5)
- [x] Auto ASCE 41-17 frame hinges (steel Table 9-7.1 / concrete Table
      10-7 M3 backbones, per-step acceptance states)
- [x] Pushover performance point (ASCE 41 coefficient method)
- [x] Pattern (skip) live loading + auto construction-sequence case
- [ ] Fiber PMM hinges → moved to Wave 22 (built on the Section Designer
      fiber-section infrastructure — not skipped, rescheduled)

### Wave 21 — composite & slab flexure  ✅ done (v1.6)
- [x] Composite beam design (studs, partial composite, DG11 vibration)
- [x] RC slab strip flexural design; walking-vibration check
- [ ] Camber recommendation → moved to Wave 24 (steel design depth —
      not skipped, rescheduled)

### Wave 22 — sections & devices  ✅ done (v1.7)
- [x] Section Designer (arbitrary fiber sections, PMM surface)
- [x] Fiber PMM hinges (from Wave 20, on the fiber-section machinery)
- [x] Friction-pendulum isolators (single/triple), multilinear links
      (elastomeric bearings = the existing v0.15 bilinear "isolator"
      idealization — no separate type needed)

### Wave 23 — meshing & advanced shells
- [x] Auto edge constraints (mismatched-mesh zipper)
- [x] Nonlinear layered shell walls; line springs / nonlinear area springs
- [x] Semi-rigid diaphragm auto lateral-load distribution

### Wave 24 — code breadth & steel design depth
- [ ] ASCE 7-22 updates; EC2/EC3 frame design; NBCC wind/seismic
- [ ] Wind exposure from shell objects (Cp); AISC 341 seismic checks
- [ ] Composite beam camber recommendation (from Wave 21)

### Wave 25 — speed, API & tables ("better speed" goal)
- [ ] Engine profiling + multithreaded solver wiring; UI render profiling
- [ ] Public documented Python API; interactive database tables
- [ ] Model templates + Similar Stories; report generator upgrade

### Wave 26 — long-horizon
- [ ] PT tendons (equivalent-load) + PT slab stress checks
- [ ] Cracked/long-term slab deflections (creep & shrinkage)
- [ ] Ritz/FNA feasibility study; DXF/IFC export; Towers

## Possible future waves (not yet scheduled)
- Native Windows local build (same build.sh on a Windows host)
