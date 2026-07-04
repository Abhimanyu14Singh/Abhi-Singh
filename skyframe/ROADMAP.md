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

## Phase 4 — Parity increment 2  ✅ done
- [x] 1. Model save/open (.skyframe JSON files) with File menu + gallery
- [x] 2. Load pattern / case / combo editor UI (with per-case P-Delta toggle)
- [x] 3. Response-spectrum analysis (CQC/SRSS, spectrum editor + live preview)
- [x] 4. P-Delta (OpenSees PDelta transforms, two-stage gravity solve)
- [x] 5. Steel section library (22 AISC W-shapes + picker UI)
- [x] 6. Wall/slab openings; elevation-view drawing (Wave 6)
- [x] 7. Nonlinear static pushover (OpenSees fiber hinges) (Wave 6)

## Remaining UI surfacing (backend + API done; needs front-end panels)
The Wave 7 analysis features are fully implemented, tested, and reachable
over HTTP; they still need dedicated UI panels:
- [ ] Staged-construction case editor + results view (`run_staged`)
- [ ] Nonlinear time-history toggle + hysteresis plot (engine done)
- [ ] Steel / concrete design-check tables (`/api/design/*`)
- [ ] Import dialogs for DXF / e2k / IFC (`/api/import/*`)
