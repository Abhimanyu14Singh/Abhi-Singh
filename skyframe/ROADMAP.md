# SkyFrame → ETABS-parity roadmap

Goal: the core ETABS workflow — **model → draw → assign → analyze → review results**
— on the open-source OpenSees solver, with every analysis feature pinned by
scrutinized benchmark tests.

## Phase 1 — Analysis core: shells, meshing, member analysis  ⏳ in progress
- [x] 3D elastic frames, rigid diaphragms, static cases + combos, modal (v0.1)
- [ ] Shell walls & slabs (ShellMITC4 + ElasticMembranePlateSection)
- [ ] Auto-mesher: structured quads, node dedup, **frame–shell edge compatibility**
      (beams/columns split to match mesh nodes; results re-aggregated per member)
- [ ] Membrane slabs: ETABS-style two-way 45° tributary distribution to edge beams
- [ ] Member end releases (moment, either end) via release condensation
- [ ] Member loads: point, partial UDL, trapezoid — *exact* fixed-end-force
      treatment, not approximations
- [ ] 11-station internal force diagrams (N, V2, V3, T, M2, M3) per member
- [ ] Area loads (kPa) on slabs, consistent nodal loading for meshed shells
- [ ] `POST /api/model` full-model round-trip (draw → solve)

## Phase 2 — ETABS-style drawing UI  ⏳ in progress
- [ ] Draw mode: plan-view story editor with grid snapping
- [ ] Tools: Select, Column, Beam, Wall, Slab, Erase (keyboard shortcuts)
- [ ] Story selector + "apply to all stories"
- [ ] Assignments panel: frame section, releases, line loads; shell section,
      behavior (shell/membrane), mesh size, area loads — per load pattern
- [ ] Section & material manager
- [ ] 3D: translucent shells, mesh display, deformed shells
- [ ] Click-a-member N/V/M diagram panel

## Phase 3 — Integration & scrutinized validation  ⏭ next
- [ ] Deep member-analysis suite: fixed-end moments, releases (3wL/8, wL²/8),
      triangular-load maxima (wL²/9√3), continuous beam vs three-moment equation,
      station diagram closed forms
- [ ] Shell suite: membrane patch test, Navier plate deflection series,
      Timoshenko deep-beam wall, Cook's skewed membrane, mesh-convergence
      assertions, exact load conservation
- [ ] End-to-end: draw → save → mesh → analyze → diagrams, verified in browser
- [ ] All suites green in CI-style run; docs updated

## Phase 4 — Parity increment 2  (backlog, in priority order)
1. Model save/open (.skyframe JSON files) + example model gallery
2. Load pattern / case / combo editor UI
3. Response-spectrum analysis (modal combination, CQC) — OpenSees-native
4. P-Delta (OpenSees PDelta/Corotational transforms)
5. Steel/concrete section libraries (W-shapes, standard rebar sizes)
6. Wall/slab openings; elevation-view drawing
7. Nonlinear static pushover (OpenSees fiber hinges) — the feature ETABS
   charges a premium for, free in OpenSees

Not chasing: RC detailing/design-code checks, DXF import, licensing dongles.
