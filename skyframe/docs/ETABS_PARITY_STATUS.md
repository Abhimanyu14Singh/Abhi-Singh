# SkyFrame vs ETABS v22: parity status (v1.26.1, 2026-10-13, results-presentation audit)

Source: `skyframe/docs/etabs_parity_matrix.json`. A row is full only when the engine and the UI both exist and the gap is empty. Rows with a `flag` hold values entered from memory or not checked against a third-party program (see docs/REVIEW_FLAGS.md).

## Counts by status

| Status | Re-audit (v1.24, start of Wave C) | Now (v1.26.1) |
|---|---|---|
| full | 152 | 197 |
| partial | 64 | 27 |
| missing | 5 | 4 |
| beyond | 11 | 11 |
| out_of_scope | 10 | 10 |
| total | 242 | 249 |

## What closed since the re-audit

- **Buckling and scale (B6):** linear buckling with shells, links and releases; buckling from a stressed state; BucklingFEM member buckling; MUMPS above 20k equations and Lanczos modal for large models.
- **Interface (C1):** general grids in E2K / as primary / in 3D; Model Explorer context menus; shell edits with openings; elevation and 3D object snaps, extension and parallel snaps; per-quantity units; auto-save with versions; model lock after a run.
- **Tables (C2):** joint velocities and accelerations, assembled masses, mass summaries, material takeoff, story max/avg displacements, modal load participation, generalized displacements, nonlinear link rows; 32 new database-edit tables.
- **Geometry and loads (C3):** frame pier labels; floor auto-mesh dialog with 'as drawn'; per-area edge constraints; distributed moments and local-1/3 loads; beam live-load reduction; static eccentricity overrides; ASCE 7 Fpx.
- **Codes and libraries (C4):** EC8 national-annex presets; NBCC Mv/J; IBC/UBC/NTC seismic; BS 6399, API 4F, NTC wind; TSC/KDS/NSCP/NTC spectra; spectrum from file; USGS lookup; GB/CSA/JIS/KS/GOST materials; Canadian/Japanese/Korean/Russian steel.
- **Links and hinges (C5):** coupled 6x6 links, per-DOF multilinear elastic, Degrading/Concrete/Isotropic/BRB hysteresis, HDR isolator, velocity-dependent friction, friction in tension, effective stiffness and damping, P-varying PMM hinges, strength drop past C, Caltrans and TBI tables.
- **Analysis (C6):** eigen shift/cutoff/tolerance, modal TH and FNA as case types, NL TH from a staged/nonlinear state, more FNA link laws, per-mode damping options for RS/SS, any-DOF pushover control, ASCE 41 Cm/TL, FEMA 440 displacement modification, shell creep and shrinkage.
- **Results (C7):** section-cut, pier and spandrel forces for RS, TH, nonlinear, staged and envelopes; TH shell forces; 3D pier/spandrel/link diagrams; video export; Compare Results.
- **E2K and interop (C8):** native E2K records for nonlinear static, hinges, links, staged, frequency and thermal data; DWG guidance; Tekla/Revit coverage via SDNF, CIS/2 and IFC.
- **Properties (C9):** anisotropic materials and material angle; Section Designer moment-curvature; any-material layered shells; CSV table export.
- **Owner requests (D1-D4):** ETABS-style Draw Walls / Quick Draw Walls; tabbed Display Options per window; unit-aware section editors; centre-of-mass fix (CR verified); Template Library with 14 structural-system templates.

- **Performance (E1-E3, v1.26):** Windows-capable parallel solves (persistent spawn worker pool, 8 performance-core lanes on a 285K); Intel MKL PARDISO for every linear analysis (one multithreaded Cholesky reused by statics, modes, Ritz, RS, CR and buckling; modal 163 s -> 8 s on a 220k-equation model); GPU (WebGL2 / Direct3D on Windows) 3D renderer under View > Renderer, with GPU picking, shader contours and mode animation.

- **Results-presentation audit (2026-10-13):** compared against how ETABS presents results and handles views. Six rows went from full to partial (selection, multiple windows, views, deformed shape and animation, shell contours, plus the frame-diagram gap), and seven rows were added (member detail window, step/envelope selector, pop-out windows, displacement contours, table formats, developed elevations, strip forces). No code regressed; the earlier ratings were too generous about the user-facing side.

## Remaining partial rows (exact gaps)

- **[partial] Interface & workflow / Plan, elevation and 3D views**: Plan and elevation views show no analysis results (only hinge-state dots): no deformed shape, force diagrams, contours or reactions; no developed (unfolded) elevations
- **[partial] Interface & workflow / Multiple simultaneous view windows**: Panes are fixed CSS tiles inside the main window (1-4, no resize); 3D panes show only the deformed/mode overlay (no force diagrams, contours, hinge states, display options); no floating / pop-out windows or second-monitor use; ETABS allows any number of windows with Arrange Windows (10 tiled + tabs)
- **[partial] Interface & workflow / Selection tools**: 3D picking covers frame members only (no joints, slabs, walls, links, springs, tendons); a plain click in the main 3D view does not select; the main 3D view does not highlight the selection; Intersecting Line and window/crossing mode are plan-only; no right-click menu in 3D panes; Properties panel is not available next to the 3D view
- **[partial] Geometry objects / Pier labels**: Frame pier labels done (Wave C3); pier forces still in-plane P, V, M only (no V3, T, M2)
- **[partial] Properties / Steel section database**: Canadian, Japanese, Korean and Russian tables added (Wave C4); no Chinese GB/T 11263 tables
- **[partial] Properties / Link: T/C friction isolator**: Friction in tension added; per-direction mu/R works in FNA only (OpenSees has no orthotropic friction bearing)
- **[partial] Properties / User-defined hinge properties**: Axial update and post-C strength drop done (Wave C5); M2-M3 yield contour is still an ellipse
- **[partial] Loads / Auto seismic other codes (TSC 2018, KDS 2022, Mexican, Philippine NSCP, IBC legacy, UBC)**: IBC 2006/09/12, UBC 97, Italian NTC 2018 added (Wave C4); Colombia, Peru, Chile, Indonesia, Saudi and ASCE 7-10 standalone not added
- **[partial] Loads / Auto wind other codes (GB 50009, TSC, KDS, Mexican, etc.)**: BS 6399-2, API 4F, Italian NTC wind added (Wave C4); GB 50009 beta_z still uses a linear mode shape
- **[partial] Analysis / Fast Nonlinear Analysis (FNA)**: Takeda/Pivot/BRB/coupled/T-C and other links run in FNA (Waves C5/C6); frame-hinge cases still refused; HDR and isotropic links need direct integration
- **[partial] Analysis / Staged construction with creep/shrinkage**: Shell creep/shrinkage added (Wave C6, opt-in); members with end releases creep axially only; step method needs user stages
- **[partial] Analysis / Large-model scalability**: Multithreaded solver closed by PARDISO (E2); the per-case OpenSees element loop for result recovery (one step per case) remains single-threaded
- **[partial] Results & output / Deformed shape and animation**: No live on-screen animation of deformed shapes or multi-step (TH / pushover / staged) results with step selector and speed control (only mode shapes animate, fixed 1.8 s; TH only via video export); no wire-shadow / cubic-curve options as named controls; 3D only
- **[partial] Results & output / Frame force diagrams**: Force diagrams cover static, combo and RS cases only (no TH step, nonlinear or staged steps, no envelope pair); values shown only at the member maximum (no Show Values on Diagram / Values at Controlling Stations); 3D only; in-plane components not tied to 2D views
- **[partial] Results & output / Shell force contours**: Contours are 3D-only (not in plan or elevation); no contours drawn on the deformed shape; averaging does not follow view visibility as ETABS does
- **[partial] Results & output / Shell stresses (S11, S22, S12, top/bottom, von Mises)**: Stresses still derived from resultants; no TH shell stresses
- **[partial] Results & output / Pier forces**: All case types and 3D diagrams done (Wave C7); in-plane P, V, M only
- **[partial] Interoperability / ETABS .e2k import**: Nonlinear static, hinges, links, staged, frequency and thermal records now native (Wave C8); code auto-lateral records and nonlinear point-spring properties still not read
- **[partial] Interoperability / ETABS .e2k export**: Nonlinear/staged/thermal now native records (Wave C8); hyperstatic cases, section cuts, panel zones, wall hinges, pushover performance and RS/TH options still $SF-only
- **[partial] Interoperability / DXF/DWG import**: DWG detected with conversion guidance (Wave C8); no converter on the build platform
- **[partial] Interoperability / Revit link**: IFC both ways documented (Wave C8); native add-in needs Windows-only Revit API
- **[partial] Interoperability / Steel interop (Tekla, CIS/2, SDNF, IDEA StatiCa)**: SDNF both ways, CIS/2 in, IFC both ways (Wave C8); native Tekla link needs Windows-only API
- **[partial] Analysis / Link effective (linear) stiffness and damping**: k_eff / c_eff used in modal, RS and linear cases (Wave C5); effective damping not converted to modal damping for RS / FNA
- **[partial] Analysis / Generalized displacements**: Definitions, dialog, DB tables and results table done (Wave C2); no plot-function traces or pushover monitoring
- **[partial] Results & output / Member results detail window (right-click diagrams)**: No scroll-for-values cursor, no Show Max marker, no step selector, no applied-load or deflection panels, not reachable from the right-click menu
- **[missing] Results & output / Displacement contours on objects**: Not implemented
- **[partial] Results & output / Step / envelope selector for multi-step results in graphics**: No step/time or envelope selector for deformed shapes, frame/pier/shell diagrams on TH, pushover or staged cases
- **[missing] Interface & workflow / Developed (unfolded) elevations**: Not implemented
- **[missing] Interface & workflow / Floating / pop-out windows and multi-monitor**: No pop-out windows; desktop shell has a single window
- **[partial] Interface & workflow / Table format, filter and sort (saved formats)**: No multi-criteria filters, no saved formats, no column-header quick filter
- **[missing] Results & output / Strip forces**: Not implemented (closest: section cuts)

Four of these need software that cannot be built or run here: native Revit and Tekla add-ins (Windows-only APIs), DWG conversion (no LibreDWG on the build platform), and ETABS itself to confirm E2K spellings.
