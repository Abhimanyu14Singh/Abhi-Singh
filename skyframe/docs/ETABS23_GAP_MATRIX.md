# ETABS v23 → SkyFrame gap matrix

Compiled 2026-07-06 from CSI's feature pages, ETABS v22/v23 release notes
(ReleaseNotesETABSv2200–2320.pdf), docs.csiamerica.com help pages, and the
CSI Knowledge Base. Drives the Wave 19+ roadmap.

**Legend:** ✅ covered · 🟡 partial · ❌ MISSING ·
Feasibility = effort to implement on the OpenSeesPy backend.

## 1. Modeling / Drawing

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| Story-based modeling w/ story recognition | Floor-by-floor data entry; story concept drives loads, drifts, reports | ✅ | — |
| Similar Stories editing | Draw/assign on one plan, auto-replicates to all "similar" stories | ❌ | easy — pure UI/data-model replication layer |
| Model templates (steel deck, flat slab, waffle, perimeter frame…) | New-model wizard generates grids, stories, sections, loads | ❌ | easy — generator scripts on existing primitives |
| Multiple/rotated/cylindrical grid systems | General grid systems per structure | ✅ | — |
| Towers (multiple towers over shared podium) | Independent story/grid systems per tower | ❌ | medium — data-model refactor; solver-side trivial |
| Section Designer (arbitrary drawn sections) | Draw any cross-section; properties, PMM surface, fiber model | ❌ | medium — OpenSees fiber `section` is a natural fit |
| Wall stacks (multistory templates, auto pier/spandrel labels) | One-click multilevel wall configs | 🟡 walls + pier forces exist | easy — templating over existing wall objects |
| Curved beams / curved & sloped walls | Curved frame and shell geometry | ❌ | medium — polyline segmentation |
| PT tendon objects | Tendons w/ profile, losses, equivalent loads | ❌ | hard (equivalent balanced loads: medium) |
| Springs: point / line / area, nonlinear area springs | Elastic + compression-only soil supports | 🟡 point + Winkler | easy — zeroLength + ENT distributed to mesh nodes |
| Link library incl. multilinear elastic/plastic | General 6-DOF nonlinear connectors | 🟡 linear/damper/gap/hook/bilinear isolator | easy — twoNodeLink + Hysteretic/MultiLinear |
| Insertion points / cardinal points | Offset analytical line to flange/edge points | 🟡 orientation + rigid offsets | easy — joint offsets via rigid links |
| Semi-rigid diaphragms (explicit, auto load spreading) | In-plane slab flexibility w/ auto lateral distribution | 🟡 rigid/none + shell slabs give physics | easy — distribute auto-lateral to slab mesh nodes |
| Panel zones, releases, T/C-only, modifiers, mass sources | Standard frame toolkit | ✅ | — |

## 2. Meshing

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| Auto floor mesh (general polygons + openings) | Compatible floor meshing | ✅ | — |
| Wall auto mesh | Auto rectangular meshing | ✅ | — |
| Auto edge constraints (line constraints) | "Zipper" ties mismatched meshes without node matching | ❌ | medium — interpolation equalDOF tie of hanging nodes |
| Frame floor meshing options | Load-transfer meshing control | 🟡 | easy |

## 3. Loads & Load Patterns

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| Auto wind: ASCE 7-22/-16, NBCC 2020/2025 (v23.2), EC1, AS/NZS, IS 875… | Code-parameterized wind | 🟡 ASCE 7 only | easy/medium per code |
| Auto seismic ELF: ASCE 7-22, NBCC, EC8+annexes, AS 1170.4, TSC, KDS (v23.2) | Code static seismic | 🟡 ASCE 7 only | easy/medium per code |
| RS function library (~30 codes) | Built-in code spectra | 🟡 ASCE 7 + EC8 | easy |
| Wind exposure from shell objects w/ Cp | Object-by-object wind pressure | ❌ | easy — pressure × Cp on shell faces |
| Auto lateral distribution on semi-rigid diaphragms | Multi-node force+torsion distribution | 🟡 | easy |
| Pattern live load factor (skip loading) | Auto skip-live patterning for design moments | ❌ | medium — pattern generation + envelope |
| Tendon loads (primary + hyperstatic) | PT load cases incl. secondary effects | ❌ | medium (equivalent-load route) |
| Notional, accidental torsion, thermal, mass source, LL reduction | Standard | ✅ | — |

## 4. Analysis Types

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| Linear static, modal, RS, lin/nonlin TH, P-Delta, pushover, buckling, staged | Core case types | ✅ | — |
| Ritz vectors (load-dependent) | Efficient basis for RS/FNA | ❌ | hard — custom Lanczos/Ritz on exported K,M |
| FNA (Fast Nonlinear Analysis) | Modal-superposition TH, nonlinearity in links only | ❌ | hard — no FNA in OpenSees; direct integration is the substitute |
| Buckling from nonlinear/staged state | Eigen buckling on stressed configuration | 🟡 linear only | medium |
| Large-displacement geometric NL | Corotational geometric NL | 🟡 P-Delta yes | easy — OpenSees Corotational transform |
| Staged construction w/ creep/shrinkage/aging | Long-term material effects | 🟡 staging yes | medium — TDConcrete / manual E(t) |
| Auto Construction Sequence case | One-click story-by-story gravity sequence | ❌ | easy — automation over existing staging |
| BucklingFEM plugin (v23 NEW) | Shell-FEM member local+global buckling | ❌ | hard |
| Floor cracking nonlinear analysis | Cracked + long-term slab deflections | ❌ | medium — iterative Ieff per shell |
| Walking-vibration serviceability (AISC DG11) | Footfall response from modal results | ❌ | medium — modal post-processing |

## 5. Dynamics

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| RS CQC/SRSS + directional; multiple mass sources | Standard | ✅ | — |
| Modal damping (per-mode), Rayleigh, per-material proportional | Flexible damping | 🟡 | easy — modalDamping, region Rayleigh |
| Base isolation via FNA | Production-speed isolated TH | 🟡 direct integration | hard (FNA row) |

## 6. Nonlinear Modeling

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| Auto hinges per ASCE 41-13/-17/-23 | Table-driven backbones + acceptance criteria | ❌ | medium — generator; hinges → zeroLength/beamWithHinges |
| Fiber PMM hinges | Coupled P-M-M fiber sections | ❌ | easy — OpenSees is fiber-native |
| Hinge overwrites: length, auto-subdivide (v23.1) | Plastic hinge length control | ❌ | easy |
| Nonlinear layered shell | Multi-layer wall/slab material model | ❌ | medium — OpenSees LayeredShell exists |
| Friction pendulum isolators: single/double/triple | FP bearing behavior | ❌ | medium — singleFPBearing / TripleFrictionPendulum built-in |
| Rubber/T-C isolators, coupled biaxial plasticity | Coupled shear bearings | 🟡 bilinear | easy — elastomericBearingPlasticity |
| Pushover performance point (FEMA 440 / ASCE 41) | Target-displacement overlays | 🟡 pushover yes | easy — post-processing |
| Hysteresis/energy output per element | Energy dissipation tracking | ❌ | easy — recorders |

## 7. Design

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| AISC 360-22/-16 full (Direct Analysis, τb, K2) | Complete steel design loop | 🟡 preliminary | medium |
| AISC 341 seismic (SCWB, panel zones, braces) | SFRS seismic checks | ❌ | medium |
| International steel: EN 1993-1-1:2022 (v23.2), EC3, CSA S16:24 (v23.1), IS 800, AS 4100… | Multi-code steel | ❌ | medium each |
| Steel joist design (SJI) | Auto joist selection | ❌ | easy/medium |
| Auto-select lists + interactive design | Iterate analysis section = design section | 🟡 auto-optimize yes | easy |
| ACI 318-19/-25 (v23.0) incl. joint shear, SCWB | Full seismic RC frame design | 🟡 preliminary + Bresler | medium |
| International RC: EC2-2023 (v23.2), CSA A23.3-24 (v23.0), IS 456, KDS… | Multi-code RC | ❌ | medium each |
| Concrete shell design (EC2 sandwich) | Shell reinforcement design | ❌ | medium — Wood-Armer/sandwich |
| Composite beam design (studs, camber, partial, vibration, price) | Composite floor design | ❌ | medium/hard |
| Composite column design | Encased/filled columns | ❌ | medium |
| SpeedCore / C-PSW/CF walls | Composite plate wall design | ❌ | hard |
| **Shear wall design module** (pier/spandrel, uniform/general reinforcing, boundary zones) | Wall reinforcement design & checking | ❌ forces only | medium — PMM per wall leg + code shear/boundary checks |
| RC slab flexural design (strips + FEM) | Strip moments → rebar | ❌ | medium |
| **Punching shear check** w/ overwrites, stud rails | Two-way shear D/C at columns | ❌ | medium |
| PT slab design | Full post-tensioned design | ❌ | hard |

## 8. Detailing

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| Integrated Detailing menu | Auto rebar detailing, schedules, 3D cages | ❌ | hard |
| CSiDetail / CSiXCAD | Drawing production to CAD/BIM | ❌ | not-feasible short-term |

## 9. Results / Output / Reporting

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| 500+ database tables, dockable/sortable | Every input/result as table | 🟡 CSV | easy/medium — table framework |
| Interactive database editing | Two-way table ↔ model editing | ❌ | medium |
| Report generator (ToC, figures, per-code reports) | One-click professional report | 🟡 printable report | easy |
| Energy / virtual-work diagrams | Ranks elements whose stiffening best cuts drift | ❌ | easy — unit-load virtual work |
| Story plots, drifts, shears, CM/CR, section cuts | Standard | ✅ (soil pressure 🟡) | — |
| Plot functions (TH traces) | Time traces of any response | 🟡 | easy |
| Named views, animations, video export | Presentation output | 🟡 | easy |

## 10. Interoperability

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| Revit via CSiXRevit; direct EXR exchange (v23.0 NEW) | BIM round-trip | ❌ | hard — IFC is the practical path |
| SAFE story-level import (v23.0 NEW) | Floor design round-trip | n/a | n/a |
| IFC 2x3/IFC4 import + export | OpenBIM | 🟡 import only | medium — export writer |
| DXF/DWG import + export | CAD exchange | 🟡 import only | easy — DXF writer |
| Tekla, CIS/2, SDNF, STAAD import | Steel/BIM formats | ❌ | medium |
| Access/Excel export; e2k text model | Data exchange | 🟡 CSV + e2k import | easy |
| IDEA StatiCa connection link | Connection design export | ❌ | medium |

## 11. API / Scripting

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| CSI OAPI (COM; Python/C#/VB) | Full programmatic model CRUD + results | ❌ | easy — SkyFrame is Python-native; expose documented API |
| In-process plugin framework | User plugins in UI | ❌ | medium |

## 12. Performance / Model Exploration

| ETABS v23 feature | What it does | SkyFrame | Feasibility |
|---|---|---|---|
| SAPFire multi-threaded solvers, parallel eigen+Ritz | Large-model speedups | 🟡 | medium — multithreaded solver options + profiling |
| Model Explorer tree | One-tree navigation | 🟡 | easy |
| Cloud Sign-in licensing (v23.0) | Business feature | n/a | n/a |

## ETABS v23 NEW features (release notes)

- **v23.0.0** (Oct 2025): ACI 318-25 wall/frame/slab design; CSA A23.3-24 wall
  design; ASCE 7-22 + NBCC 2020 auto lateral; Cloud Sign-in; SAFE story-level
  import; direct EXR exchange; BucklingFEM plugin.
- **v23.1** (Nov–Dec 2025): browser sign-in; hinge-length overwrites;
  CSA S16 stability design method.
- **v23.2.0** (Feb 2026): EC2-2023 concrete design; EN 1993-1-1:2022 steel;
  KDS 2022 Korean suite; NBCC 2025 auto wind.

## Top 15 missing features (ranked)

1. Shear wall design module (medium) → **Wave 19**
2. Slab flexural design + punching shear (medium) → **Wave 19/21**
3. Auto ASCE 41 hinge generation (medium) → **Wave 20**
4. Composite beam design (medium/hard) → **Wave 21**
5. Ritz vectors + FNA (hard) → **Wave 26** (exploration)
6. Code breadth: ASCE 7-22, EC2/EC3, NBCC, CSA (medium) → **Wave 24**
7. Section Designer w/ fiber + PMM (medium) → **Wave 22**
8. Auto edge constraints (medium) → **Wave 23**
9. Public scripting API (easy) → **Wave 25**
10. Interactive database tables (medium) → **Wave 25**
11. PT tendons + PT slab design (hard) → **Wave 26**
12. Friction pendulum isolators (medium) → **Wave 22**
13. Nonlinear layered shell walls (medium) → **Wave 23**
14. Cracked/long-term slab deflections (medium) → **Wave 26**
15. Pattern live loading + auto construction sequence (easy/medium) → **Wave 20**

Plus a dedicated **performance wave** (multithreaded solver wiring, profiling,
UI speed) per the "better speed" goal → **Wave 25**.
