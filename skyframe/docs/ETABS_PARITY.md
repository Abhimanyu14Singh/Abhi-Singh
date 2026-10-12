# SkyFrame → ETABS v22 analysis parity: roadmap R1–R4

Source: `docs/etabs_parity_matrix.json` (232 rows), audited on 2026-10-11 against
SkyFrame v1.21 (`/home/user/Abhi-Singh/skyframe`). Evidence paths in the matrix
are relative to the package root `skyframe/skyframe/` (`js/` means
`api/web/js/`). "CONTRACT §X" refers to a section of `skyframe/CONTRACT.md`.

**Rating rules.** A row is "full" only when both the engine and the UI exist.
A row is "partial" when the feature is engine-only, UI-only, or narrower than
ETABS; the gap text says which. Rows that ETABS covers with design or
detailing are marked "out_of_scope". The Tools > Code Checks that already exist
are listed but not planned.

**Effort scale** (one agent-day is about one focused engineer-day):
S ≤ 2 days, M = 3–5 days, L = 6–15 days, XL > 15 days.

## Where SkyFrame stands

| Status | Rows |
|---|---|
| full | 124 |
| partial | 49 |
| missing | 42 |
| beyond ETABS | 7 |
| out of scope (design) | 10 |

The analysis core is already close to ETABS:
- P-Delta presets.
- All six RS modal-combination methods.
- Nonlinear static analysis with case chaining.
- User staged construction.
- Direct-integration nonlinear TH (multi-component).
- Pushover distributions with the ASCE 41 target displacement.
- Links, including friction-pendulum and triple-pendulum isolators.
- User and fiber hinges.
- Towers and multiple diaphragms.
- Polygon meshing and edge constraints.

The remaining gaps fall into five groups:

1. **RS completeness.** RS cases have no accidental eccentricity, no vertical
   or angled excitation, and no shell forces. The directional combination has
   SRSS and 100/30 only.
2. **Code breadth in generators.** These cover RS functions, auto
   combinations, and wind and seismic codes outside ASCE, EC8, IS 1893 and
   NBCC.
3. **Engine features with no UI.** These are shell types, one-way
   distribution, curved frames, wall mesh divisions, load sets and damping
   options.
4. **Output breadth.** Results tables cover only joint, story and modal
   output. There is no element-force table, no .xlsx export, and no spandrel
   output.
5. **Exchange.** e2k import reads only a subset. No exporter exists for e2k,
   DXF, IFC or SAFE.

## Phase overview

| Phase | Theme | Items | Effort (agent-days) | Parity statement at exit |
|---|---|---|---|---|
| **R1** | Correct, complete linear seismic and wind workflow; expose what exists | 28 | about 45–60 | A standard ETABS linear building study (gravity, ELF or wind, RS with eccentricity, combinations, drift/irregularity, element-force tables, Excel out) reproduces end-to-end for ASCE 7, EC8, IS 1893 and NBCC models, with matching table names |
| **R2** | Nonlinear depth, code breadth, model exchange | 34 | about 110–150 | ETABS nonlinear workflows (ASCE 41 pushover with auto hinges on beams, columns and braces, FEMA 440 / N2, FNA isolated buildings on a Ritz basis) match. e2k round trip lets an ETABS model be imported, run and compared |
| **R3** | Scale, long-term effects, open ecosystem | 23 | about 120–170 | Production-size models (≥ 100k DOF) run at ETABS-class speed. Creep and shrinkage use material laws. Buckling includes shells. IFC is round-tripped. Plugins are possible |
| **R4** | Long horizon or low ROI | 6 | > 100 | Niche parity: Revit link, BucklingFEM, tendon elements, frequency-dependent links, Access |
| **Track G** | Rhino/Grasshopper interoperability (planned separately) | 1 slot | reserved | (defined by the separate plan) |

"ETABS parity for analysis" grows cumulatively: R1 ⊂ R2 ⊂ R3. Design and
detailing stay out of scope at every phase.

---

## R1: Correct, complete linear workflow (next 1–2 releases)

**Theme.** Close the RS correctness gaps that change design forces. Broaden
the generators that every project uses. Expose finished engine features in
the UI. Bring tables and Excel to ETABS level. Most items are S.

**Ordered items**

1. **RS accidental eccentricity** (high, S). Add `ecc` to
   `ResponseSpectrumCase`. Apply ± story torques derived from RS story forces
   and envelope them. Touches `run_response_spectrum`.
2. **RS shell forces** (high, S). Combine per-mode `shell_forces` with the
   existing CQC/SRSS machinery in `_combine_rsa` / `modalcombo`.
   **Shell contours on RS and envelopes** (high, M) follow from it, together
   with **shell stresses** (S11/S22/S12, top and bottom, von Mises) (med, S).
   - Dependency: item 2 must land before contours.
3. **RS vertical (U3) and angled excitation** (med, S), plus **CQC3 / ABS
   directional combination** (med, S).
   - Dependency: the vertical mass participation in modal must be checked
     first.
4. **RS functions by code** (high, S each). Expose the IS 1893 and NBCC
   spectra that already exist in `autolateral.py` / `codes.py`. Add the
   ASCE 7-22 MPRS function and the EC8 Type 2 / design (q) spectrum.
5. **Auto load combinations** (high, S). Add EN 1990, IS 875-5 / IS 456,
   NBCC 2020 and AS/NZS 1170.0 next to `asce7_combinations`.
   - Dependency: item 6.
6. **Load pattern types** (med, S). Add superdead, reducible live, roof live,
   snow and temperature, so the combination generators and live-load
   reduction can classify patterns.
7. **ASCE 7 wind: four load cases** (high, S). Cover the eccentric and
   combined cases of Fig. 27.3-8 in both ASCE 7-16 and 7-22 generators.
8. **Time-history function input** (high, M). File import (PEER AT2,
   time-value pairs, header skip) plus sine, ramp and periodic generators.
   Spectral matching is deferred to R2 or R3.
9. **Engine-only → UI wiring** (S each, high value in total):
   - Shell type selector: thin, thick, plate, membrane FE (`shell_types.py`).
   - One-way distribution with span direction (`oneway.py`).
   - Draw > Curved Beam (`curved.py`).
   - Wall mesh n1×n2 divisions.
   - Shell uniform load sets.
   - Add every new field to the DB-edit tables as well.
10. **Frame releases per component and partial fixity** (med, M). Add P, V2,
    V3, T, M2 and M3 release flags plus partial-fixity springs. This touches
    the frame builder and the closed-form recovery.
11. **Spandrel labels and spandrel forces** (med, M). Reuse the pier and
    section-cut integration.
12. **Tables** (high, M). Add these to Show Tables:
    - Element Forces for frames, shells and links.
    - Pier/Spandrel Forces.
    - Section Cut Forces.
    - Hinge States.
    - TH/pushover step tables.

    In the same item, close the **DB-edit coverage** gaps (med, M) and add
    **.xlsx export and import** (med, S).
13. **Master / similar stories** (high, M). Add Story master/similar fields
    and a draw/assign/edit scope selector.
14. **Selection and snapping** (med, S). Add intersecting-line, by-type and
    by-label selection. Snap to joints, ends, midpoints, intersections and
    perpendiculars.
15. **Housekeeping** (S). Correct `js/helpdocs.js` "dis-pushover", which
    claims ATC-40/FEMA 440. Correct the stale `docs/ETABS23_GAP_MATRIX.md`.

**Dependencies.**
- Item 2 must land before the contour work.
- Item 6 must land before item 5.
- Item 12 builds on `core/tables.py`.
- Item 11 shares the pier code with the existing piers tab.

**Exit test.** Re-create a CSI verification-style 10-story RC building with
walls (RS with 5% eccentricity, ELF, wind, 1.2D+1.0E combos). Compare story
shears, drifts, pier forces and shell forces to an ETABS run. Add this as a
`tests/verification/` case.

---

## R2: Nonlinear depth, code breadth, exchange

**Theme.** Make the performance-based workflow first-class. Broaden the
national codes. Let ETABS users move models in and out.

**Ordered items**

1. **Auto ASCE 41 hinges, complete** (high, M).
   - Steel and RC column P-M2-M3 rows.
   - Brace axial hinges (Table 9-8).
   - RC shear hinges.
   - ASCE 41-23 deltas.
   - Drop the "preliminary" flag once these are verified.
2. **User hinges: V3, T, and parametric (non-fiber) P-M2-M3 / M2-M3
   interaction** (med, M).
   - Dependency: item 1 shares the engine hinge builder, so build them
     together.
3. **Pushover performance methods** (med, M+S):
   - FEMA 440 equivalent linearization / ATC-40 capacity spectrum.
   - EN 1998 N2.
   - ADRS plot.
4. **Links** (med, S each): Plastic (Wen) per DOF and a BRB law (asymmetric
   hardening).
5. **Ritz as a usable basis** (high, M). Include shells and links in the Ritz
   assembly. Allow RS and FNA to select a Ritz modal case.
   - Dependency: item 6.
6. **Multiple modal cases and multiple mass sources** (med, M). Add a
   `ModalCase` dictionary and let RS/TH reference it by name.
7. **FNA breadth** (high, L):
   - FP, TFP, multilinear and Wen links.
   - Dampers with alpha ≠ 1.
   - Gravity initial conditions through a nonlinear static predecessor.
   - Dependencies: items 4, 5 and 6.
8. **Damping** (med, S). UI for the Rayleigh option on springs, links and
   hinges. Material-proportional (composite) modal damping.
9. **Code generators** (med, S each):
   - Seismic: NZS 1170.5, AS 1170.4.
   - Wind: EN 1991-1-4, IS 875-3, AS/NZS 1170.2.
   - Live-load reduction per NBCC, EC1 and IS 875.
   - Irregularity checks: weight, vertical geometric, weak story, EC8/IS
     regularity.
10. **Exchange** (high, L + L + M + S):
    - e2k import, broadened: shells, braces, steel sections, releases, loads,
      diaphragms, cases and combos.
    - e2k export.
    - DXF import, broadened: polylines and 3DFACE to shells.
    - DXF export.
    - Value: round-trip comparison against ETABS becomes routine.
11. **Programmatic API** (high, M). Stabilize the REST schema (versioned
    OpenAPI). Add an optional OAPI-style facade (`SapModel.FrameObj.AddByCoord`
    and similar) so existing ETABS Python scripts port with few edits.
    - Dependency: Track G (Rhino/Grasshopper) needs this.
12. **Modelling productivity** (med, S–M):
    - Richer templates (waffle, ribbed, two-way with beams) that also set up
      code loads, RS and mass source.
    - Quick-draw in region and drawing in 3D.
    - Named views.
    - Wall stacks.
    - Ramp tool.
    - Joint local axes for restraints.
    - Named line and area spring properties.
    - Deck sections (equivalent orthotropic membrane plus one-way
      distribution).
    - Report export to .docx.

**Exit test.** Run the ASCE 41 pushover of a steel SMF and an RC frame with
auto hinges. Run FNA on a triple-FP isolated building. Import an ETABS
example e2k, run it and diff it against the ETABS tables.

---

## R3: Scale, long-term effects, ecosystem

**Theme.** Production-size models, time-dependent behaviour and an open
ecosystem.

**Ordered items**

1. **Large-model scalability** (high, L).
   - Benchmark at 50k–200k DOF.
   - Sparse/shift-invert eigen and multithreaded MUMPS.
   - Sparse stability check (the current dense check is capped at 3000 DOF).
   - Profile shell-heavy models, which today are native-solver-bound at about
     1.0x.
2. **Buckling with shells and links** (med, L). Assemble plate geometric
   stiffness, or move buckling into OpenSees.
3. **Time-dependent materials** (med, M), then **incremental creep in staged
   construction** (med, L). Per-material CEB-FIP 2010, ACI 209 and GL2000
   laws, plus tendon relaxation.
4. **IFC import (walls, slabs, profile mapping, IFC4) and IFC export** (med,
   L + L).
5. **Plugin framework** (med, M). A documented manifest that loads JS menu
   items and Python hooks, building on the existing `sky.menu` registry.
6. **Further hinge tables** (med, M): FEMA 356, EC8-3 chord rotation. Add
   **wall fiber hinges** (med, M) as a lighter alternative to layered shells.
7. **Breadth backlog** (low, S–M each):
   - GB 50011, TSC 2018 and KDS seismic and wind generators.
   - Orthotropic materials.
   - Ribbed and waffle slabs.
   - T/C friction isolator.
   - Bilinear damper.
   - Cold-formed shapes.
   - SRC/CFT sections.
   - General grids.
   - Multi-window views.
   - Edit-shell operations.
   - Auto relabel.
   - SAFE (f2k) floor export.

**Exit test.** A 60-story tower with walls and floors (≥ 150k DOF) runs
modal, RS and staged construction in ETABS-comparable wall-clock time.
Column shortening matches a CEB-FIP reference.

---

## R4: Long horizon / low ROI

1. Revit link (XL). Prefer an IFC round trip and judge demand first.
2. Tendons as stiffness elements (L).
3. BucklingFEM-style shell-FEM member buckling (XL).
4. Frequency-dependent link properties (M).
5. Steel interop: SDNF, CIS/2, Tekla, IDEA StatiCa (L).
6. Access export (S). SQLite is the better substitute.

---

## Track G: Rhino / Grasshopper interoperability (reserved slot)

Planned separately. This roadmap only reserves the slot and records its
interfaces:
- **Starts:** alongside R2.
- **Depends on:** the R2 item 11 public API/REST schema stabilization, and
  the JSON model schema (`model.to_dict`), which is already open.
- **Likely consumers:** curved frames (R1 item 9), polygon shells and
  results tables (R1 item 12).
- **Matrix row:** "Rhino / Grasshopper interoperability" (missing, high, L,
  slot R2).
- No scope, ordering or effort is fixed here.

---

## Cross-cutting rules (carry over from current practice)

- Every new model field must reproduce legacy results byte-identically when
  unset. Each field gets a CONTRACT section and a validator hook.
- Every engine feature ships with its UI in the same phase. That is how this
  roadmap avoids adding new "engine-only" partial rows.
- Each phase adds `tests/verification/` cases against closed-form solutions or
  published ETABS/SAP2000 verification problems.


## Closed by A8 (results output)

Display tables (element / wall / nonlinear-step tables), spandrel labels, spandrel forces, export to Excel (.xlsx), export to Microsoft Access (closed with SQLite: .mdb/.accdb needs the proprietary Jet/ACE engine; SQLite opens in Access via ODBC and in every data tool) and report generation (.docx, selectable tables, per-case 3D snapshots). See CONTRACT "Results tables export, spandrels and Word report (A8)".
