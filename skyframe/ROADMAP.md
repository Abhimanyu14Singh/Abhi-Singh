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

## Phase 4 — Parity increment 2  ✅ items 1-5 done
- [x] 1. Model save/open (.skyframe JSON files) with File menu + gallery
- [x] 2. Load pattern / case / combo editor UI (with per-case P-Delta toggle)
- [x] 3. Response-spectrum analysis (CQC/SRSS, spectrum editor + live preview)
- [x] 4. P-Delta (OpenSees PDelta transforms, two-stage gravity solve)
- [x] 5. Steel section library (22 AISC W-shapes + picker UI)
- [ ] 6. Wall/slab openings; elevation-view drawing
- [ ] 7. Nonlinear static pushover (OpenSees fiber hinges)

### Original backlog (superseded above)
1. Model save/open (.skyframe JSON files) + example model gallery
2. Load pattern / case / combo editor UI
3. Response-spectrum analysis (modal combination, CQC) — OpenSees-native
4. P-Delta (OpenSees PDelta/Corotational transforms)
5. Steel/concrete section libraries (W-shapes, standard rebar sizes)
6. Wall/slab openings; elevation-view drawing
7. Nonlinear static pushover (OpenSees fiber hinges) — the feature ETABS
   charges a premium for, free in OpenSees

Not chasing: RC detailing/design-code checks, DXF import, licensing dongles.
