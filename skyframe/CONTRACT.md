# SkyFrame internal contract (backend ↔ frontend ↔ tests)

Units everywhere: kN, m, tonne, s. E in kPa. Loads kN / kN/m. Mass tonne.

## Python engine interface

```python
from skyframe.core.model import BuildingModel
from skyframe.core.builder import quick_building
from skyframe.engine.opensees_engine import OpenSeesEngine

model = quick_building(...)          # or hand-built BuildingModel
engine = OpenSeesEngine(model)
results = engine.run()               # runs all static cases + combos + modal
d = results.to_dict()                # JSON-safe dict, shape below
```

`OpenSeesEngine` must also expose:
- `engine.run_static(case_name) -> CaseResults` (single case)
- `engine.run_modal(num_modes=None) -> ModalResults`

## results.to_dict() JSON shape

```jsonc
{
  "model_name": "…",
  "nodes": {"<tag>": [x, y, z]},                 // every FE node
  "members": [{"uid": "C1-A1", "kind": "column|beam|brace", "section": "COL",
                "ni": <tagI>, "nj": <tagJ>, "story": "Story1"}],
  "supports": ["<tag>", …],                      // restrained node tags
  "story_order": ["Story1", "Story2", …],        // bottom → top
  "story_elev": {"Story1": 3.2, …},
  "cases": {
    "<case>": {
      "node_disp": {"<tag>": [ux, uy, uz, rx, ry, rz]},   // m, rad
      "reactions": {"<tag>": [FX, FY, FZ, MX, MY, MZ]},   // kN, kN·m (support nodes only)
      "base": {"FX":0,"FY":0,"FZ":0,"MX":0,"MY":0,"MZ":0},// total base reaction
      "member_forces": {"<uid>": [Ni,Vyi,Vzi,Ti,Myi,Mzi, Nj,Vyj,Vzj,Tj,Myj,Mzj]}, // local
      "story": {"<story>": {"ux":0,"uy":0,"drift_x":0,"drift_y":0,
                             "shear_x":0,"shear_y":0}}    // drift = ratio (Δ/h)
    }
  },
  "combos": { "<combo>": <same shape as a case> },
  "modal": {
    "periods": [T1, T2, …],          // s
    "frequencies": [f1, …],          // Hz
    "participation": [{"mode":1, "T":0.8, "ux":0.82, "uy":0.0, "rz":0.01}], // mass ratios 0..1
    "shapes": {"1": {"<tag>": [6 dof values]}}
  }
}
```

## Flask HTTP API (module `skyframe.api.server`, `create_app()` factory)

| Method | Path              | Body / Response |
|--------|-------------------|-----------------|
| GET    | `/`               | SPA (serves `skyframe/api/web/index.html`; static files under `/static/<path>` from same dir) |
| GET    | `/api/health`     | `{"status":"ok","opensees":true}` |
| GET    | `/api/model`      | current `model.to_dict()` |
| POST   | `/api/model/quick`| body = quick_building kwargs (all optional): `{name, bays_x, bay_width_x, bays_y, bay_width_y, stories, story_height, E, column_size, beam_b, beam_h, dead_udl, live_udl, quake_coeff, base_fixity}` → new `model.to_dict()` |
| POST   | `/api/analyze`    | no body → `results.to_dict()` (runs on current model). 400 with `{"error": "…"}` on failure |

Server keeps ONE current model in memory (default: `quick_building()` at startup).
`python -m skyframe.api.server` runs on port 8600.

---

# v0.2 additions — drawing, shells/walls/slabs, meshing, member analysis

## Model additions (`skyframe/core/model.py`)

```python
@dataclass ShellSection:   # ElasticMembranePlateSection in OpenSees
    name: str; material: str; thickness: float          # m
@dataclass ShellRegion:    # planar quad region (wall or slab)
    uid: str; kind: str            # "wall" | "slab"
    behavior: str                  # "shell" (meshed FE) | "membrane" (slabs only: no FE,
                                   #   two-way tributary load distribution to edge beams)
    section: str                   # ShellSection name (unused for membrane)
    corners: List[Tuple[x,y,z]]    # 4 corners, planar, counter-clockwise
    mesh_size: float = 1.0         # m target element size (shell behavior)
    story: str = ""
@dataclass AreaLoad:               # in LoadPattern.area_loads
    region_uid: str; q: float      # kPa, positive DOWNWARD (gravity)
# FrameMember gains: releases: str = ""   # any of "Mi","Mj" tokens comma-sep →
#   moment released about BOTH local y & z at that end (OpenSees -releasez/-releasey)
# MemberUDL replaced-by/extended-with MemberLoad list in LoadPattern.member_loads:
@dataclass MemberLoad:
    member_uid: str
    kind: str = "udl"              # "udl" | "point" | "trapezoid"
    w: float = 0.0                 # kN/m at start (udl/trapezoid), kN for point (P)
    w2: float = 0.0                # kN/m at end (trapezoid)
    a: float = 0.0                 # start position (fraction 0..1 of length); point: position
    b: float = 1.0                 # end position (fraction)
    direction: str = "gravity"     # "gravity" (global -Z) | "local_y" | "global_x|y|z"
# LoadPattern gains: area_loads: List[AreaLoad]; member_udls kept as alias/legacy or migrated.
# BuildingModel gains: shell_sections: Dict[str, ShellSection]; shells: List[ShellRegion]
# BuildingModel.from_dict(d) classmethod: full round-trip of to_dict().
```

## Meshing (`skyframe/core/mesh.py`)

`mesh_model(model) -> MeshedModel`:
- Each shell-behavior ShellRegion → structured quad mesh (nx×ny from mesh_size).
- Node dedup/merge across regions and frame member endpoints (tol 1e-6).
- **Compatibility**: any frame member whose axis lies along shell-region edges is
  SPLIT into segments so its intermediate nodes coincide with edge mesh nodes.
  Split segments keep the parent uid; engine re-aggregates station results so
  reported member results always refer to the ORIGINAL member.
- Membrane slabs are NOT meshed: their area loads become trapezoid/triangle
  MemberLoads on edge beams (two-way tributary at 45°), load total conserved.

## Engine additions

- ShellMITC4 + ElasticMembranePlateSection for meshed regions; shell self-weight
  ignored v0.2 (mass unchanged: diaphragm/story mass model).
- Member releases via elasticBeamColumn '-releasez'/'-releasey' codes.
- MemberLoad handling: full-span udl → eleLoad beamUniform; point → beamPoint;
  partial/trapezoid → EXACT equivalent end forces (closed-form fixed-end forces
  applied as reversed nodal loads) + Python-side member-end-force correction.
- Area loads on shell regions → consistent nodal loads (tributary area × q).
- Station results: 11 equally spaced stations per ORIGINAL member computed by
  statics from end forces + member loads (exact for prismatic members).

## results.to_dict() additions

```jsonc
{
  "shell_quads": [{"region": "W1", "nodes": [n1,n2,n3,n4]}],   // meshed FE quads
  "cases": { "<case>": {
      "member_stations": {"<uid>": {"x": [0,...,L], "N": [...], "V2": [...],
                                     "V3": [...], "T": [...], "M2": [...], "M3": [...]}},
      // node_disp already covers shell nodes
  }}
}
```

## API additions

| Method | Path         | Body / Response |
|--------|--------------|-----------------|
| POST   | `/api/model` | full model dict (BuildingModel.from_dict) → echoed model dict; 400 {"error"} on invalid |

## Drawing UI (frontend)

Draw mode with plan-view story editor: tools Column / Beam / Wall / Slab / Select
/ Erase, section & load assignment on selection, story selector + "apply to
similar stories", saves via POST /api/model. 3D renders shell regions as
translucent filled quads (mesh lines once analyzed), deformed shells, and a
member-detail panel with N/V2/M3 diagrams from member_stations.

## model.to_dict() (already implemented, see `skyframe/core/model.py`)

Members carry `pi`/`pj` coordinate triples; the UI draws from `to_dict()` of
model (geometry) + results (deformations keyed by node tag; node coords in
results.nodes).

---

# v0.3 additions — save/open, response spectrum, P-Delta, section library

## Model save/open API

Saved models are `<name>.skyframe.json` files (the exact `model.to_dict()`
JSON) in a models directory: default `~/.skyframe/models`, overridable via
the `SKYFRAME_MODELS_DIR` environment variable; created on demand.  Names
must match `[A-Za-z0-9 _-]{1,60}` (else 400).

| Method | Path                      | Body / Response |
|--------|---------------------------|-----------------|
| GET    | `/api/models`             | `[{"name", "mtime", "stories", "members"}, …]` (mtime = epoch seconds; stories/members = counts) |
| POST   | `/api/models/<name>`      | saves the CURRENT model → its listing entry; 400 `{"error"}` on a bad name |
| POST   | `/api/models/<name>/open` | loads the file into the current model (`BuildingModel.from_dict`) → model dict; 404 if missing, 400 if the file is invalid |
| DELETE | `/api/models/<name>`      | `{"deleted": name}`; 404 if missing |

## Response-spectrum analysis (RSA)

```python
@dataclass ResponseSpectrumCase:
    name: str
    direction: str                 # "X" | "Y"
    spectrum: List[[T, Sa]]        # T in s, Sa in g; LINEAR interpolation at
                                   #   modal periods, clamped to end values
    num_modes: int = 0             # 0 = all computed modes
    combo_method: str = "CQC"      # "CQC" | "SRSS"
    damping: float = 0.05          # constant modal damping ratio (CQC)
    scale: float = 1.0             # multiplies Sa
# BuildingModel gains: rs_cases: Dict[str, ResponseSpectrumCase]
#   (+ add_rs_case(...)); included in to_dict()/from_dict() as "rs_cases".
# RS cases may NOT appear inside LoadCombos (v0.3 keeps them separate).
```

Engine (`engine.run_response_spectrum(name) -> CaseResults`, also run by
`engine.run()`): exact modal statics — per mode the equivalent static force
vector `f_i = Γ_i · Sa_i · g · M · φ_i` is applied as nodal loads and solved
through the ordinary linear static pipeline, then every quantity is combined
across modes (CQC with the standard constant-damping correlation
coefficient, or SRSS).  Story drifts are combined per mode (ETABS-style),
NOT recomputed from combined displacements.  All RSA results are POSITIVE
envelopes.

`results.to_dict()` gains `"rs_cases": {"<name>": <same shape as a case>}`
(node_disp / reactions / base / member_forces / story / member_stations, all
combined absolute values; story shear = combined cumulative modal force).

Modal `participation` entries gain the participation FACTORS `"gamma_x"` /
`"gamma_y"` per mode (`Γ = L/M*`; sign follows the eigenvector
normalisation — `Γ·φ` is normalisation-invariant).

## P-Delta static cases

```python
# LoadCase gains:
#   pdelta: bool = False
#   pdelta_gravity: Dict[str, float] | None = None   # pattern -> factor;
#       None => the case's own patterns ARE the gravity state
```

When `pdelta` is on, ALL frame members use `geomTransf('PDelta', …)` — the
linearized "lean-column" geometric stiffness (−P/L on the transverse sway
translations) — and the case is solved with Newton
(`test NormDispIncr 1e-8 20`).  With a distinct `pdelta_gravity` state the
engine runs two stages: gravity first, `loadConst -time 0.0`, then the
case's own loads; the reported result is the case's INCREMENT past the
gravity state (standard linearized-P-Delta case output).  A pure-gravity
P-Delta case (`pdelta_gravity=None`) is a single reported stage.
Non-convergence raises a clear error.  Combos that superpose a P-Delta case
still combine linearly but carry
`"warning": "superposition includes a P-Delta (nonlinear) case; …"` in the
combo's results dict (the key is absent for all-linear combos).

## Steel section library

`skyframe/core/sections_library.py`: 22 common AISC W-shapes (W8x31 …
W36x150) with A, I33, I22, J (and drawing b/h) converted exactly from the
AISC Manual (15th ed.) imperial values to SI (m).  API:

| Method | Path                    | Response |
|--------|-------------------------|----------|
| GET    | `/api/sections/library` | `[{"name", "A", "I33", "I22", "J", "b", "h"}, …]` (SI units) |

`FrameSection.from_library("W12x26", material)` builds a ready-to-add
section from the library.

---

# v0.4 additions — envelope combos, mass source, stiffness modifiers, auto wind, linear time history, column orientation, shell forces

## Envelope load combos

```python
# LoadCombo gains:
#   combo_type: str = "add"        # "add" | "envelope"
```

* ``"add"`` — linear result superposition (unchanged v0.1 behavior).
* ``"envelope"`` — per-quantity **min/max over the LISTED cases**, each
  case's results multiplied by its factor first.  Envelope combos may
  reference static load cases ONLY (no RS/TH cases, no other combos).
* Results shape: ``results["combos"][name]`` keeps the standard case shape
  holding the **MAX** values, plus a nested ``"min"`` key with the same
  shape (node_disp / reactions / base / member_forces / story /
  member_stations) holding the **MINIMA**.  ``shell_forces`` are NOT
  enveloped in v0.4 (key absent on envelope combos).
* An envelope that lists a P-Delta case carries a ``"warning"`` string
  (factored nonlinear results enter the envelope unchanged).

## Mass source

```python
# BuildingModel gains:
#   mass_source: Dict[str, float]   # pattern -> factor, e.g. {"DEAD": 1.0, "LIVE": 0.25}
```

``compute_story_masses`` uses ``mass_source`` when non-empty, else falls
back to the legacy ``mass_from_patterns`` (which is retained and still
serialised).  ``quick_building`` sets ``mass_source = {"DEAD": 1.0}``.
``from_dict`` without a ``"mass_source"`` key (pre-v0.4 files) keeps the
legacy behavior exactly.  Explicit ``story_masses`` still win per story.

## Stiffness modifiers

```python
# FrameSection gains (all default 1.0, must be finite and > 0):
#   mod_A, mod_I33, mod_I22, mod_J
# ShellSection gains:
#   mod: float = 1.0     # single modifier, scales the section E
```

The engine multiplies A/I33/I22/J by their modifiers when creating
``elasticBeamColumn`` elements (and inside the exact fixed-end-force
member-load path, so member loads stay exact).  ``ShellSection.mod`` scales
E of the ``ElasticMembranePlateSection``: membrane AND flexural stiffness
scale together — ElasticMembranePlateSection has a single modulus, so
independent membrane/flexural modifiers are NOT offered (a silent
approximation was rejected).  Full ``to_dict``/``from_dict`` round-trip;
absent keys default to 1.0.

## Auto wind pattern (ASCE 7-style)

```python
from skyframe.core.builder import make_wind_pattern, wind_kz, wind_qz
make_wind_pattern(model, name, direction,        # "X" | "Y"
                  basic_wind_speed,              # V, m/s
                  exposure="C",                  # "B" | "C" | "D"
                  cp_total=1.3, importance=1.0) -> LoadPattern
```

* ``Kz = 2.01 (z/zg)^(2/alpha)`` with ASCE 7-16 Table 26.10-1 parameters
  B: alpha=7.0, zg=365.76 m; C: 9.5/274.32; D: 11.5/213.36; z floored at
  4.6 m (applied to ALL exposures — documented simplification; ASCE uses
  9.14 m for B).
* ``qz = 0.613 Kz Kzt Kd V^2 I`` Pa -> kPa, with Kzt = 1.0, Kd = 0.85.
* Story force at each story level: ``F = qz(story top elev) * cp_total *
  trib_height * width`` where trib_height = half story below + half story
  above (top story: half itself), width = plan extent PERPENDICULAR to the
  wind from ``model.plan_extents()``.
* Creates/replaces ``model.patterns[name]`` with kind ``"wind"`` carrying
  only ``story_forces`` (fx for "X", fy for "Y").

| Method | Path                 | Body / Response |
|--------|----------------------|-----------------|
| POST   | `/api/pattern/wind`  | `{name?, direction?, V, exposure?, Cp?, importance?}` -> updated model dict; 400 `{"error"}` on bad input.  Defaults: name "WIND", direction "X", exposure "C", Cp 1.3, importance 1.0. |

## Linear time-history analysis

```python
@dataclass TimeHistoryCase:      # model.th_cases: Dict[str, TimeHistoryCase]
    name: str
    direction: str               # "X" | "Y"
    accel: List[float]           # ground accel, m/s^2, sample k at t = k*dt
    dt: float                    # s
    damping: float = 0.05        # Rayleigh target ratio
    scale: float = 1.0           # multiplies accel
# BuildingModel.add_th_case(...); serialised under "th_cases"; TH cases may
# NOT enter load combos.
```

Engine ``run_time_history(name) -> THResults`` (also run by ``run()``):

* uniform ground excitation (OpenSees ``UniformExcitation`` + ``Path``
  time series), Newmark constant-average acceleration (gamma=1/2, beta=1/4,
  unconditionally stable), one step per record sample at the record dt,
  ``algorithm Linear`` (elastic model);
* Rayleigh damping ``C = a0 M + a1 K`` fitted to ``damping`` at modes 1 and
  min(3, n): ``a0 = 2 z w_i w_j/(w_i+w_j)``, ``a1 = 2 z/(w_i+w_j)``;
* recorded per step (entry k = state at t=(k+1)dt): story ux/uy (diaphragm
  masters, else story-node average) and total base reactions FX/FY.

``results["th_cases"][name]`` shape:

```jsonc
{
  "t": [dt, 2*dt, ...],
  "story_ux": {"Story1": [...]}, "story_uy": {...},   // m
  "base_FX": [...], "base_FY": [...],                 // kN (reactions)
  "peaks": {                                          // peak ABSOLUTE values
    "story": {"Story1": {"ux":0,"uy":0,"drift_x":0,"drift_y":0,
                          "shear_x":0,"shear_y":0}},  // drift = ratio
    "base": {"FX":0,"FY":0}
  }
}
```

Peak story shears come from inertia-force equilibrium (story/nodal masses x
total accelerations, cumulative from the top; damping forces neglected —
documented approximation).  **Step cap:** ``engine.run()`` skips ALL TH
cases when their total step count exceeds 20 000 and sets a top-level
``"warning"`` string in the results; ``run_time_history`` itself is never
capped.  RS/combos never include TH cases.

## Column orientation angle

```python
# FrameMember gains:
#   angle: float = 0.0    # degrees, right-hand rotation of the local y/z
#                         # axes about the member axis (local +x)
```

The engine rotates the default local triad by ``angle`` and feeds the
rotated local z as the ``geomTransf`` vecxz — for a vertical rectangular
column, ``angle=90`` exactly swaps the sway stiffness directions.  Applies
to all members; ``"local_y"`` member loads follow the rotated axes.
``add_member(..., angle=...)``; round-trips through to_dict/from_dict.

## Shell element forces

``results["cases"][case]["shell_forces"]`` (also on additive combos, by
superposition; key ABSENT when the model has no meshed shells, on envelope
combos, and on RS/TH results):

```jsonc
"shell_forces": {"<quad_index>": [Nxx, Nyy, Nxy, Mxx, Myy, Mxy, Vxz, Vyz]}
```

``quad_index`` is the index into ``results["shell_quads"]`` (whose entries
already carry the ``region`` mapping).  Values are the average of the 4
ShellMITC4 gauss-point stress resultants (= centroid values for a bilinear
field), in the ELEMENT local system: membrane forces kN/m, moments kN*m/m,
transverse shears kN/m.  P-Delta two-stage cases report the increment past
the gravity state, consistent with every other quantity.

## Top-level results additions

* ``"th_cases": {"<name>": <TH shape above>}`` (always present, may be {})
* ``"warning": "..."`` — present only when set (e.g. TH step cap).

---

# v0.5 additions — openings, pushover, diaphragm option, link elements

## Wall/slab openings

```python
@dataclass Opening:              # ShellRegion gains openings: List[Opening]
    u0: float; v0: float; u1: float; v1: float
    # rectangle in REGION-PARAMETRIC coordinates: u runs along the
    # corner 0 -> 1 edge, v along corner 0 -> 3, all fractions 0..1;
    # must satisfy 0 <= u0 < u1 <= 1 and 0 <= v0 < v1 <= 1.
```

* Openings are validated (bounds + pairwise NON-overlap) and round-trip
  through ``to_dict()``/``from_dict()`` (serialised as
  ``{"u0","v0","u1","v1"}`` dicts under ``"openings"``; absent key = none).
* **Mesher (shell behavior)**: each opening bound snaps to the NEAREST
  structured mesh line (``round(u*nx)`` / ``round(v*ny)``); elements whose
  parametric cell lies inside the snapped rectangle are omitted; grid
  points used by no kept element are never created (**no orphan nodes**).
  An opening smaller than one element after snapping warns and is skipped.
* **Area loads** on meshed regions act through the kept elements'
  tributary areas only: load conservation is EXACT, base FZ = q x (meshed
  net area).
* **Membrane slabs**: the distributed total drops uniformly by the exact
  opening area ratio (``ShellRegion.opening_area`` — the bilinear map
  sends a parametric rectangle to a straight-edged planar quad, so its
  shoelace area is exact).  The two-way tributary SHAPE is unchanged —
  documented approximation, a ``UserWarning`` is emitted.  Conservation
  stays exact: total = q x net_area.
* ``ShellRegion.opening_area`` / ``.net_area`` properties; story-mass
  bookkeeping for area loads uses ``net_area``.

## Nonlinear static pushover

```python
@dataclass PushoverCase:        # model.pushover_cases: Dict[str, PushoverCase]
    name: str
    direction: str               # "X" | "Y"
    gravity: Dict[str, float] = {}      # pattern -> factor, applied first
    target_drift: float = 0.02          # roof drift ratio
    steps: int = 100
    hinges: str = "column_base"         # "column_base" | "all_ends"
    My: Dict[str, float] = {}           # member uid -> yield moment (kN*m)
    default_My: float | None = None     # applies to eligible members w/o entry
    hardening: float = 0.02             # post-yield stiffness ratio, [0, 1)
# BuildingModel.add_pushover_case(...); serialised under "pushover_cases".
```

**Hinge idealization (documented exactly):** members named by ``My`` (or
covered by ``default_My``) get a zeroLength rotational spring between the
member end and a duplicated coincident node; all other members stay fully
elastic — no hinge is inserted.  ``"column_base"`` hinges the LOWER end of
each eligible column; ``"all_ends"`` both ends of every eligible member.
Per hinge: Steel01 (bilinear) about BOTH member bending axes with yield
moment ``My`` and elastic stiffness ``k_theta = n*6EI/L`` (n = 10, the
standard stiff-hinge idealization — elastic member-end series softening is
the factor n/(n+1)); Steel01 hardening ratio ``b = h/(n+1-h*n)`` so the
member-end SERIES post-yield/elastic stiffness ratio is exactly the case's
``hardening`` h; stiff elastic torsion ``k = n*max(6EI22, 6EI33, GJ)/L``;
translations tied exactly with ``equalDOF`` (Transformation handler).

**Solve:** gravity stage (Newton, ``NormDispIncr 1e-6 50``) then
``loadConst -time 0``; the push is displacement-controlled
(``DisplacementControl``) on the roof control DOF — the TOP story's
diaphragm master, else the topmost structural node — with a UNIT reference
force there, in ``target_drift * roof_elev / steps`` equal increments,
Newton with a NewtonLineSearch retry per failed step; on repeated failure
the run stops early and returns the partial curve plus a warning.  Base
shear = -(sum of support reactions in the push direction, gravity share
subtracted); support reactions include the hinge-duplicate nodes of
supported originals (the equalDOF tie routes the element shear there).

```jsonc
results["pushover"][name] = {      // key always present in results (may {})
  "roof_disp":  [ ... ],           // m, past the gravity state
  "base_shear": [ ... ],           // kN
  "roof_drift": [ ... ],           // roof_disp / roof elevation
  "hinge_rotations": {"<uid>": peak_abs_rotation},   // rad, both axes
  "warnings": [ ... ]              // e.g. early stop on non-convergence
}
```

``engine.run_pushover(name)`` runs one case (cached, never capped);
``engine.run()`` includes all pushover cases only when the model has any
AND their combined steps <= 2000 (else all are skipped and a top-level
``"warning"`` is set).

## Semi-rigid / no diaphragm option

```python
# BuildingModel gains:
#   diaphragm: str = "rigid"                 # "rigid" | "none" (global)
#   story_diaphragm: Dict[str, str] = {}     # per-story overrides
```

Effective mode per story: ``story_diaphragm[story]`` if set, else the
global ``diaphragm`` — with the legacy boolean ``rigid_diaphragms=False``
still forcing the global default to ``"none"`` (backward compatible; both
fields serialise; pre-v0.5 files keep their exact behavior).  ``"none"``
creates NO rigid-diaphragm constraint for that story; story mass lumps
onto the story nodes (ux/uy split equally) exactly as before for
master-less stories, and reported story ux/uy fall back to the story-node
mean.  **Semi-rigid IS "none" + the slab modeled as a meshed shell**: the
slab's real membrane stiffness plays the diaphragm role (no fake
constraint is offered — a stiff t=0.4 slab reproduces the rigid-diaphragm
T1 within a few percent).

## Link elements

```python
@dataclass LinkMember:           # model.links: List[LinkMember]
    uid: str
    pi: (x, y, z); pj: (x, y, z)
    stiffness: List[float]       # [kx, ky, kz, krx, kry, krz]
                                 # kN/m, kN*m/rad, GLOBAL axes (v0.5)
# BuildingModel.add_link(pi, pj, stiffness, uid=""); serialised under
# "links"; entries must be finite, >= 0, at least one > 0.
```

Engine: one OpenSees ``zeroLength`` element per link with an elastic
uniaxial material per non-zero entry (default orientation = global axes).
It is a PURE spring: the element length carries no rigid-arm moment
transfer, so two kx springs in series give exactly u = P(1/k1 + 1/k2).
Endpoints merge with existing FE nodes (1e-6) or create new nodes; a node
connected ONLY to links gets its zero-stiffness DOFs auto-restrained
(diaphragm-tied ux/uy/rz of prospective slaves are left to the diaphragm),
so the system stays regular.  Links carry no mass and report no member
forces in v0.5.

## API

No new endpoints: ``POST /api/model`` round-trips every v0.5 field
(``openings``, ``pushover_cases``, ``diaphragm``, ``story_diaphragm``,
``links``) and ``POST /api/analyze`` returns the ``"pushover"`` results
block documented above.

---

# v0.6 additions — engine: staged construction, nonlinear time history

## Staged construction (sequential gravity)

```python
@dataclass StagedCase:          # model.staged_cases: Dict[str, StagedCase]
    name: str
    pattern: str = "DEAD"                # gravity pattern that is staged
    stages: str = "per_story"            # the ONLY v0.6 mode
    include_live: Dict[str, float] = {}  # pattern -> factor, applied at the
                                         # END on the full structure, UNSTAGED
# BuildingModel.add_staged_case(...); serialised under "staged_cases"
# (absent key = none, pre-v0.6 files unchanged).  Staged cases may NOT
# enter load combos.
```

**Method (documented exactly — REBUILD-AND-ACCUMULATE element staging):**
``engine.run_staged(name)`` (also run by ``engine.run()``, cached, never
capped) solves one FRESH OpenSees model per stage k containing ONLY
stories 1..k — members, shells, supports, diaphragms and links of those
stories — under ONLY story k's gravity loads from ``pattern``, then
ACCUMULATES the per-stage linear increments: member end forces and
station forces per uid, reactions and structural-node displacements
matched by coordinates (1e-6), diaphragm-master values and story results
by story name, base totals by summation.  Members/regions not yet built
in a stage receive no increment; node displacements accumulate the
increments measured in each stage's fresh geometry (geometry updating is
ignored — the standard linear staged-analysis assumption), which is what
produces the "slab built level" effect: for a 2-story axial stack the
staged top-node settlement excludes the stage-1 shortening P1*L/EA that
one-shot analysis includes.  Load attribution: member loads follow their
member's story (falling back to the story owning the member's topmost
endpoint), area loads their region's story (same fallback), nodal loads
the story owning their z (story k covers (elev_{k-1}, elev_k]), story
forces their named story.  ``include_live`` is then applied on the FULL
structure in one unstaged increment.  Every partial structure 1..k must
be stable on its own (a story propped only by later construction cannot
be staged).

**Comparison:** the engine also solves the one-shot application of the
same TOTAL loads (staged pattern at 1.0 + include_live factors) on the
full structure internally.

```jsonc
results["staged"][name] = {        // top-level "staged" key always present
  // ... the standard static-case shape holding the ACCUMULATED final
  // state: node_disp / reactions / base / member_forces / story /
  // member_stations (shell_forces are NOT reported for staged cases —
  // quad indexing is not stable across stage meshes), PLUS:
  "comparison": {
    "column_axial_max_diff_pct": 0.0,  // max |N_staged - N_oneshot| over
                                       // column end-i axials, % of the
                                       // largest one-shot column axial
                                       // (0.0 with no columns / all-zero)
    "oneshot_case": { ... }            // full static-case shape of the
                                       // internal one-shot solve
  }
}
```

For LINEAR elastic response, statically determinate quantities (e.g.
column axials of a symmetric gravity stack) are one-shot identical, while
statically indeterminate quantities (e.g. beam end moments in a multi-
story frame) genuinely differ — both are pinned by closed-form tests.

## Nonlinear time history

```python
# TimeHistoryCase gains (all round-trip; absent keys = linear, pre-v0.6):
#   nonlinear: bool = False
#   gravity: Dict[str, float] = {}     # pattern -> factor, static stage first
#   hinges: str = "column_base"        # "column_base" | "all_ends" (v0.5)
#   My: Dict[str, float] = {}          # member uid -> yield moment (kN*m)
#   default_My: float | None = None
#   hardening: float = 0.02            # post-yield ratio, [0, 1)
# add_th_case(...) accepts all of the above; validation mirrors the
# pushover-case rules (unknown members/patterns, ranges).
```

When ``nonlinear`` is set, ``run_time_history``:

* builds the model with the SAME Steel01 zeroLength hinge springs as a
  v0.5 pushover case (identical hinge plan, ``k_theta = n*6EI/L`` with
  n = 10, ``b = h/(n+1-h*n)``, equalDOF translations — CONTRACT v0.5);
* applies the ``gravity`` combination statically first (Newton,
  ``NormDispIncr 1e-8, 25``) and holds it (``loadConst -time 0``); ALL
  reported series are the response PAST the gravity state;
* integrates with Newmark constant-average acceleration + Newton
  (``test NormDispIncr 1e-8, 25``), one NewtonLineSearch retry per failed
  step (both failing raises);
* Rayleigh damping: a0/a1 fitted to ``damping`` at modes 1 and min(3, n)
  of the INITIAL ELASTIC model (hinges excluded), applied with
  COMMITTED-stiffness proportionality (``betaKcomm``) — the standard
  hinge-model choice; initial-stiffness proportionality would put
  spurious post-yield damping moments ``a1*k_theta*theta_dot`` (which can
  exceed My) on the stiff hinge springs.  Linear cases keep the exact
  v0.4 behavior (identical for an elastic response);
* base_FX/base_FY sum the support reactions PLUS the hinge-duplicate
  nodes of supported originals (the equalDOF tie routes the element shear
  there, as in pushover).

``results["th_cases"][name]`` keeps the exact v0.4 shape for linear cases
and gains, for nonlinear cases only:

```jsonc
{
  "hinge_rotations": {"<uid>": peak_abs_rotation},  // rad, both axes, all
                                                    // steps; every hinged
                                                    // member has an entry
  "yielded": ["<uid>", ...]   // sorted; hinge exceeded My/k_theta about
                              // either bending axis at any step
}
```

**Step cap:** unchanged and COMBINED — ``engine.run()`` skips ALL TH
cases (linear and nonlinear together) when their total step count
exceeds 20 000, with the same top-level ``"warning"``;
``run_time_history`` itself is never capped.

## API

No new endpoints: ``POST /api/model`` round-trips ``staged_cases`` and
the nonlinear TimeHistoryCase fields; ``POST /api/analyze`` returns the
``"staged"`` results block and the nonlinear TH keys documented above.

---

# v0.6 additions — API for design checks and model importers

## HTTP API

| Method | Path                  | Body / Response |
|--------|-----------------------|-----------------|
| POST   | `/api/design/steel`   | `{case, [Fy,kx,ky,Lb]}` → runs analysis, `{preliminary:true, case, checks:[MemberCheck], summary}`. 400 on missing case / no OpenSees |
| POST   | `/api/design/concrete`| `{case, rebar:{uid:RebarLayout}, [fc]}` → `{preliminary:true, case, checks:[ConcreteCheck], summary}` |
| POST   | `/api/import/dxf`     | `{text, stories:[h…], [column_section,beam_section,wall_section,unit_scale]}` → `{model, warnings}` (becomes current model) |
| POST   | `/api/import/e2k`     | `{text}` → `{model, warnings}` |
| POST   | `/api/import/ifc`     | `{text}` → `{model, warnings}` |

All design/import results carry `preliminary: true` where applicable; every
importer returns non-fatal parse issues in `warnings` (never 500). Design
endpoints run a fresh analysis of the current model before checking.

---

# v0.7 additions — self-weight loads + ASCE 7-16 code helpers

## Self-weight (`skyframe/core/model.py`, engine)

```python
# LoadPattern gains (ETABS-style: a pattern applies self-weight * factor):
#   self_weight_factor: float = 0.0
```

* When a pattern in the active case has `self_weight_factor != 0`, the engine
  adds each material's real self-weight (`Material.unit_weight`, kN/m^3):
  * every FRAME member gets a global -Z distributed load
    `factor * A * unit_weight` (kN/m over its length), applied through the
    exact member-load path via the `"global_z"` direction so vertical
    COLUMNS pick up their axial self-weight (they are not skipped like a
    plain "gravity" load) — nominal `A` (stiffness modifiers do NOT scale
    weight);
  * every SHELL region that resolves to a `ShellSection` gets an area load
    `factor * thickness * unit_weight` (kN/m^2, downward) through the exact
    area-load path (shell FE tributary or membrane two-way distribution).
* `compute_story_masses` includes a self-weight pattern automatically when
  it is in `mass_source` (beams + shells on the story; columns are excluded,
  matching the existing member-UDL rule).
* `to_dict`/`from_dict` round-trip `self_weight_factor`; absent key
  (pre-v0.7) = 0.0.
* Builder helper: `add_self_weight(model, pattern="SW", factor=1.0)` creates
  (or updates) a kind-`"dead"` self-weight pattern plus a matching
  single-pattern case and returns the pattern.

## Code helpers (`skyframe/core/codes.py`, NEW — ASCE 7-16)

```python
site_coefficients(Ss, S1, site_class="D") -> (Fa, Fv)
    # Tables 11.4-1 / 11.4-2 (classes A-E), linear interpolation across the
    # Ss/S1 breakpoints, clamped at the end columns.
spectrum_parameters(Ss, S1, site_class="D") -> (SDS, SD1, SMS, SM1, T0, Ts)
    # SMS=Fa*Ss, SM1=Fv*S1, SDS=2/3 SMS, SD1=2/3 SM1, T0=0.2 SD1/SDS,
    # Ts=SD1/SDS.
asce7_spectrum(Ss, S1, site_class="D", TL=8.0) -> [[T, Sa_g], ...]
    # multilinear design spectrum: ramp 0.4 SDS -> SDS on [0,T0], flat SDS on
    # [T0,Ts], SD1/T on [Ts,TL], SD1*TL/T^2 beyond; corner points sampled
    # exactly. Suitable for a ResponseSpectrumCase.
make_rs_case_from_code(model, name, direction, Ss, S1, site_class="D",
                       R=8.0, Ie=1.0, TL=8.0, num_modes=0,
                       combo_method="CQC", damping=0.05) -> ResponseSpectrumCase
    # spectrum = asce7_spectrum(...); the design reduction Ie/R (§12.9.1.1)
    # is carried on the case `scale` (raw spectrum stays inspectable).
asce7_combinations(model, standard="LRFD") -> {name: {case: factor}}
    # ASCE 7-16 §2.3 (LRFD) or §2.4 (ASD) combos, cases matched by pattern
    # kind (dead/live/quake/wind). ±E / ±W sign variants. A term whose kind
    # has no matching case is dropped with a UserWarning (no DEAD => empty).
apply_asce7_combinations(model, standard="LRFD") -> {name: {case: factor}}
    # same dict, also added to model.combos.
asce7_elf(model, SDS, SD1, R, Ie=1.0, Ct=0.0466, x=0.9, direction="X",
          name="ELF") -> LoadPattern
    # §12.8: Ta=Ct*hn^x (hn=top elevation); Cs=min(SDS/(R/Ie),
    # SD1/(Ta(R/Ie))) floored at max(0.044 SDS Ie, 0.01); V=Cs*W (W from
    # story masses); Fx=V*(w h^k)/sum(w h^k), k=1 (T<=0.5), 2 (T>=2.5),
    # linear between. Stores a kind-"quake" story-force pattern.
```

LRFD combos for a model with DEAD/LIVE/EQX/EQY: `1.4D`; `1.2D+1.6L`;
`1.2D+1.0L±1.0EQX`; `0.9D±1.0EQX`; and the EQY variants. ASD uses `D`,
`D+L`, `D±0.7EQ`, `D+0.75L±0.525EQ`, `0.6D±0.7EQ` (wind uses 0.6W factors).

## API additions

| Method | Path                     | Body / Response |
|--------|--------------------------|-----------------|
| POST   | `/api/pattern/selfweight`| `{name?, factor?}` (defaults "SW"/1.0) → updated model dict; 400 on bad input |
| POST   | `/api/combos/asce7`      | `{standard?}` ("LRFD"|"ASD", default LRFD) → updated model dict; 400 on bad standard |
| POST   | `/api/case/rs-code`      | `{name, direction?, Ss, S1, site_class?, R?, Ie?}` → updated model dict; 400 on bad input |
| POST   | `/api/pattern/elf`       | `{name?, SDS, SD1, R, Ie?, direction?}` → updated model dict; 400 on bad input |

All four mutate the single current in-memory model and return `model.to_dict()`.

---

# v0.8 additions — foundation springs, accidental torsion, thermal loads, CM/CR

## Point spring supports (`skyframe/core/model.py`, engine)

```python
@dataclass SpringSupport:
    point: Tuple[float, float, float]
    stiffness: List[float]   # [kx, ky, kz, krx, kry, krz], kN/m & kN*m/rad,
                             # GLOBAL axes; finite, >= 0, at least one > 0
# BuildingModel gains: spring_supports: List[SpringSupport]
#   (+ add_spring_support(point, stiffness)); to_dict/from_dict round-trip
#   ("spring_supports"; absent key = none, pre-v0.8 files unchanged).
```

Engine: for each spring the real (structural) node at ``point`` is located
(proximity 1e-6, created if absent), a **co-located fully-fixed ground node**
is added, and a ``zeroLength`` element (ground → real) carries one elastic
uniaxial material per NON-ZERO stiffness entry (``-dir`` over those DOFs,
global orientation).  The real node is left FREE in the sprung DOFs — any
base fixity (explicit or automatic) on a sprung DOF is CLEARED so the spring
is the sole restraint there; a newly created isolated spring node has its
non-sprung DOFs fixed to stay regular.  The spring reaction ``-k*disp`` is
added to the real node's case ``reactions`` (the sprung DOFs read 0 from
OpenSees, so no double count), the real node counts as a support, and it
enters the base totals — so `spring reactions + other reactions balance the
applied load`.  Springs also work in P-Delta cases (incremental reaction) and
RS cases (per-mode).

## Accidental torsion (ASCE 7-16 §12.8.4.2)

```python
# LoadPattern gains (whole-pattern flags):
#   accidental_torsion: bool = False
#   ecc: float = 0.05     # finite, >= 0
```

When a pattern has ``accidental_torsion`` and a story force is applied to a
rigid-diaphragm **master**, the engine ALSO applies a story torque at the
master's rz DOF: ``Mz = fx*ecc*Ly + fy*ecc*Lx`` (``Ly``/``Lx`` = the plan
extents perpendicular to each force component, from ``plan_extents()``).
v0.8 applies **+ecc only** (positive torsion); the ± enveloping is a
combos-level concern.  Story forces on master-less stories carry no torsion
(documented).  Both fields round-trip (absent keys = defaults).

## Temperature (thermal) loads

```python
@dataclass ThermalLoad:            # in LoadPattern.thermal_loads
    member_uid: str
    dT: float = 0.0                # temperature change, deg C (positive rise)
# LoadPattern gains: thermal_loads: List[ThermalLoad]
# BuildingModel gains: thermal_alpha: float = 1.2e-5  (/degC)
#   (+ add_thermal_load(pattern, member_uid, dT)); all round-trip.
```

Engine: an axial thermal fixed-end force ``N = E*A_eff*alpha*dT`` (compression
positive when restrained; ``A_eff`` includes the ``mod_A`` modifier) is
applied through the exact member-load path — each segment's local vector
``f0 = [+N, 0.., -N, 0..]`` is applied REVERSED as nodal loads (the free
thermal expansion) and recorded as the segment end-force correction.  Hence a
fully-fixed member reads ``N = -E*A*alpha*dT`` (compression) exactly, a
one-end-free member reads ``N = 0`` with free elongation ``alpha*dT*L``, and
member station ``N`` reflects the axial force.  ``dT`` is multiplied by the
case's pattern scale.

## Center of mass / center of rigidity per story (ETABS diagnostic)

Top-level results gain ``story_props`` (present ONLY when the model has rigid
diaphragms; keys omitted otherwise), per diaphragm story:

```jsonc
"story_props": {"<story>": {"cm_x":0,"cm_y":0,"cr_x":0,"cr_y":0}}
```

* **CM** is the centroid of the story's gravity load (the same contributions
  ``compute_story_masses`` uses — member/area/nodal/self-weight loads weighted
  by their positions); explicit ``story_masses`` (no spatial info) or a
  load-free story fall back to the plan center.
* **CR** by the unit-load method on the SAME assembled elastic model: a unit
  ``Fx``, a unit ``Fy``, and a unit torque ``Mz`` are applied in turn at each
  story master and the master rotation ``rz`` is read (``theta_x``,
  ``theta_y``, ``phi``).  With ``phi`` = rz per unit torque, the CR
  eccentricities from the master are ``e_y = theta_x/phi`` and
  ``e_x = -theta_y/phi``; ``cr_x = master_x + e_x``, ``cr_y = master_y + e_y``
  — the point about which a story shear produces no diaphragm rotation.  For a
  symmetric building CR coincides with CM and the plan center; a stiff shear
  wall on one side shifts CR toward the wall.

## API additions

| Method | Path                  | Body / Response |
|--------|-----------------------|-----------------|
| POST   | `/api/support/spring` | `{point:[x,y,z], stiffness:[6]}` → adds a spring, returns model dict; 400 on bad input |
| POST   | `/api/pattern/thermal`| `{pattern, loads:[{member_uid, dT}]}` → adds thermal loads, returns model dict; 400 on bad input |

`POST /api/model` round-trips ``spring_supports``, ``thermal_alpha``, and the
pattern ``accidental_torsion``/``ecc``/``thermal_loads`` fields; the
``/api/analyze`` results carry ``story_props`` automatically.

---

# v0.9 additions — rigid-end offsets, seismic irregularity diagnostics, design envelopes

## Rigid-end offsets (`skyframe/core/model.py`, engine)

```python
# FrameMember gains (ETABS-style rigid-zone; all round-trip, absent = defaults):
#   rigid_i: float = 0.0        # rigid-zone LENGTH (m) at end i
#   rigid_j: float = 0.0        # rigid-zone LENGTH (m) at end j
#   rigid_factor: float = 1.0   # fraction of the offset taken as rigid (0..1)
# Properties: rigid_offset_i = rigid_factor*rigid_i,
#             rigid_offset_j = rigid_factor*rigid_j.
# add_member(..., rigid_i=, rigid_j=, rigid_factor=); validation: rigid_i/j >= 0,
#   rigid_factor in [0, 1], and rigid_offset_i + rigid_offset_j < length (a
#   member must keep a positive clear span).
```

Engine: a member with a non-zero effective offset keeps its real end NODES at
`pi`/`pj`; the elastic ``elasticBeamColumn`` spans the CLEAR length
`L_clear = L - rigid_factor*(rigid_i + rigid_j)` between two intermediate
offset nodes, each tied to the real end node by a **very-stiff
elasticBeamColumn rigid arm** (E/G scaled by `RIGID_LINK_FACTOR = 1e6` — the
"stiff element" choice from the two options; results match the exact rigid-link
value to ~1e-6, well within the 1e-4 test tolerance).  `rigid_factor = 0`
reproduces the plain member EXACTLY.  Rigid offsets are supported only on
single-segment members (a member split for shell-edge compatibility raises a
clear error).  Reported member end forces / stations come from the elastic
(clear-span) element; member loads on an offset member act over the clear span
(documented).  Works in linear, P-Delta, and combo runs.

Hand-checked closed forms (all rigid-link precision): a horizontal cantilever
with a rigid zone at the FIXED end (rigid_i = a) has tip deflection
`P*(L - rf*a)^3 / (3 E I33)`; a rigid zone at the LOADED end additionally
transfers the load's offset moment `M = P*a`
(`P l^3/3EI + P a l^2/EI + P a^2 l/EI`, l = L-a); a fixed-fixed beam with equal
end offsets a has central-point-load midspan deflection
`P*(L-2a)^3/(192 E I33)`.

## Story stiffness + seismic irregularity diagnostics

Top-level results gain two blocks, computed by PURE post-processing of the
already-solved cases (no new solves), for each static case AND additive combo
that carries a non-zero story shear:

```jsonc
"story_stiffness": {
  "<case>": {"<story>": {"kx": 0, "ky": 0}}      // k = V_story / Delta
},
"irregularity": {
  "<case>": {"<story>": {
     "tors_ratio_x": 1.0, "tors_ratio_y": 1.0,   // ASCE 7 §12.3.2.1
     "flag": "none|torsional|extreme",           // 1.2 / 1.4 thresholds
     "stiff_ratio": 0.0,   // k_story / k_story_above (null on the top story)
     "soft_flag": "|soft|extreme_soft"           // Table 12.3-2 §12.3.2.2
  }}
}
```

* **Story lateral stiffness** `k = V_story / Delta` uses the interstory drift
  DISPLACEMENT `Delta` (m) = story disp minus story-below disp; verified against
  `Sum 12 E I / h^3` on a guided single-column shear frame.  `kx`/`ky` are 0
  when that direction has no drift or no shear.
* **Torsional-irregularity ratio** = `delta_max / delta_avg` of the two
  diaphragm ends transverse to the loading axis, with
  `delta_end = u ± rz*(B/2)` from the diaphragm master's translation `u` and
  rotation `rz` (B = plan extent perpendicular to the force: `Ly` for x,
  `Lx` for y).  `flag` = "torsional" at ratio >= 1.2, "extreme" at >= 1.4.
  Master-less stories report ratio 1.0 (no rotation info).
* **Soft-story stiffness ratio** compares each story's stiffness (in the
  case's dominant loading direction) with the story above (`stiff_ratio`) and
  the average of the three above; `soft_flag` = "soft" (< 70% of the story
  above OR < 80% of avg-of-3) / "extreme_soft" (< 60% OR < 70% of avg-of-3).
  The top story has `stiff_ratio = null` and an empty `soft_flag`.

## Design over all load combinations (envelope)

```python
# skyframe/design/steel.py
check_members_envelope(model, results, combos: list[str] | None = None, **kw)
    -> list[MemberCheck]
# skyframe/design/concrete.py
check_concrete_members_envelope(model, results, rebar, combos=None, **kw)
    -> list[ConcreteCheck]
```

Each runs the per-combo check for every name in `combos` (default: every name
in `results["combos"]`) and returns, per member, the check with the LARGEST
demand/capacity ratio, tagged with the new field `governing_combo` (added to
`MemberCheck`/`ConcreteCheck` and their `to_dict()`; default `""`).  A member
that is N/A in every combo keeps the first combo's check; a single-combo
envelope equals that combo's checks (plus the tag).  `summarize(...)` works on
the returned envelope list unchanged.

## API additions

`POST /api/design/steel` and `POST /api/design/concrete` gain an optional
`"combos"` field: `true` runs the envelope over ALL combos in the freshly-run
analysis, a `["name", ...]` list over the named combos; the single-case path
(`"case"`) is unchanged.  The request needs `"case"` OR `"combos"` (else 400).
Response items carry `governing_combo`; the response echoes `"combos"`.

`POST /api/model` round-trips the FrameMember `rigid_i`/`rigid_j`/
`rigid_factor` fields; `POST /api/analyze` returns the `story_stiffness` and
`irregularity` blocks automatically (present when the model has a lateral case
with story shear).

---

# v0.10 additions — linear buckling, RS directional combination, notional loads

## Linear (eigenvalue) buckling analysis (`skyframe/core/buckling.py`, NEW)

A SELF-CONTAINED numpy implementation, deliberately independent of OpenSees.

```python
from skyframe.core.buckling import buckling_analysis, BucklingResult
buckling_analysis(model, gravity: Dict[pattern, factor], num_modes=6)
    -> BucklingResult
```

Method: assemble the global elastic stiffness `K` and the consistent 12x12
geometric stiffness `Kg` of the 3D frame (`elasticBeamColumn` members only),
using the SAME local-axis convention and 12x12 elastic beam stiffness as the
OpenSees engine (so K matches `elasticBeamColumn` to machine precision).
Member axial forces `N` under the reference gravity come from a linear static
solve (gravity reduced to equivalent nodal loads; axial extracted per member).
`Kg` is formed from those `N` (tension-positive; consistent geometric
sub-matrices in both bending planes, axial/torsional geometric terms omitted —
the standard beam-column form). The generalized eigenproblem

    K phi = lambda (-Kg) phi

is solved with numpy ONLY (no scipy dependency): Cholesky `K = L L^T` reduces
the pair to the symmetric standard problem `C psi = mu psi`,
`C = L^-1 (-Kg) L^-T`, `mu = 1/lambda`, via `numpy.linalg.eigh`; the largest
positive `mu` give the smallest positive `lambda` (critical multipliers on the
reference gravity). The `num_modes` smallest positive `lambda` and their
per-node 6-dof mode shapes are returned.

Modelling scope (v0.10, documented):
* only frame members enter K/Kg; shell regions and links are SKIPPED with a
  warning;
* rigid diaphragm constraints are IGNORED — the frame is analysed bare
  (standard "no rigid floor" buckling assumption);
* member end releases are IGNORED (member treated continuous) with a warning
  (release condensation is not applied in v0.10);
* gravity reduction: nodal loads act directly; member distributed/point
  gravity loads and self-weight are lumped to their end nodes (exact for
  column axial by vertical equilibrium); area/story/thermal loads are skipped
  with a warning.

```jsonc
BucklingResult.to_dict() = {
  "factors": [lambda1, lambda2, ...],       // sorted ascending, positive
  "modes":   {"1": {"<node>": [6 dof]}},    // per mode, per node
  "gravity": {"<pattern>": factor},
  "warnings": [ ... ]
}
```

Model:
```python
@dataclass BucklingCase:                    # model.buckling_cases: Dict[str, ..]
    name: str
    gravity: Dict[str, float] = {}          # pattern -> factor (>= 1 pattern)
    num_modes: int = 6                       # >= 1
# BuildingModel.add_buckling_case(name, gravity, num_modes=6); round-trips
#   through to_dict/from_dict as "buckling_cases" (absent key = none).
```

Engine: `engine.run_buckling(name) -> BucklingResult` (never capped);
`engine.run()` includes `results["buckling"][name] = BucklingResult.to_dict()`
for every buckling case (top-level `"buckling"` key ALWAYS present, may be
`{}`), solving at most `BUCKLING_CASE_CAP = 25` systems in `run()` (extras
skipped with a top-level `"warning"`).

Hand-checked closed forms (Euler, meshing the column into segments): a
pinned-pinned column -> `Pcr = pi^2 E I / L^2` (< 1% at >= 8 segments,
converging from above); fixed-free cantilever -> `pi^2 E I / (2L)^2` (< 2%);
fixed-fixed -> `pi^2 E I / (0.5L)^2` (< 2%); the first pinned-pinned mode is a
half-sine.

## Response-spectrum directional combination (ASCE 7-16 §12.5)

```python
engine.run_rs_directional(name_x, name_y, method="100_30"|"SRSS", name="")
    -> CaseResults
```

Combines two EXISTING RS cases (X and Y) into a directional envelope, per
response quantity `q`:
* `"100_30"` -> `max(|qx| + 0.3|qy|, 0.3|qx| + |qy|)`;
* `"SRSS"`   -> `sqrt(qx^2 + qy^2)`.
The base RS results are positive envelopes, so the combination is positive.
Applied to every quantity (node_disp / reactions / base / member_forces /
story / member_stations).

```python
# BuildingModel.rs_combos: {name: {"name_x", "name_y", "method"}}
# BuildingModel.add_rs_combo(name, name_x, name_y, method="100_30"); both RS
#   cases must exist; method in ("100_30","SRSS"). Round-trips as "rs_combos".
```

Engine `run()` includes each directional combo under `results["rs_cases"][name]`
(same case shape) ALONGSIDE the base RS cases.

## Notional loads (AISC 360 direct-analysis / stability)

```python
from skyframe.core.model import make_notional_pattern, story_gravity_loads
make_notional_pattern(model, name, direction="X"|"Y", coeff=0.002,
                      gravity_pattern="DEAD") -> LoadPattern
```

Applies a lateral story force `Ni = coeff * W_story` at each story, where
`W_story` is the vertical gravity load tributary to that level from
`gravity_pattern` (`story_gravity_loads(model, pattern)`: beams/area/nodal
loads + beam & shell self-weight on the story, columns excluded — the same
contributions `compute_story_masses` uses, returned in kN). The result is a
kind-`"notional"` story-force `LoadPattern` (`fx` for "X", `fy` for "Y") stored
under `name` (replacing an existing pattern). `sum(Ni) == coeff * W_total`.

## API additions

| Method | Path                        | Body / Response |
|--------|-----------------------------|-----------------|
| POST   | `/api/case/rs-directional`  | `{name, name_x, name_y, method?}` -> adds an rs_combo, returns model dict; 400 on bad method / unknown RS case |
| POST   | `/api/pattern/notional`     | `{name?, direction?, coeff?, gravity_pattern?}` (defaults "NOTIONAL"/"X"/0.002/"DEAD") -> adds the pattern, returns model dict; 400 on bad input |

`POST /api/model` round-trips `buckling_cases` and `rs_combos`;
`POST /api/analyze` returns the `"buckling"` block automatically and the
directional RS combos inside `"rs_cases"`.

---

# v0.11 additions — Winkler elastic foundation on members, gravity load takedown

## Winkler elastic foundation (`skyframe/core/model.py`, mesher, engine)

```python
# FrameMember gains (both default 0.0; round-trip; absent = defaults):
#   foundation_ks: float = 0.0      # subgrade modulus (kN/m^3)
#   foundation_width: float = 0.0   # bearing width b (m)
# Property: foundation_k_line = ks*width when ks>0 AND width>0, else 0.0
#   (kN/m per metre of length — the distributed vertical line-spring modulus).
# add_member(..., foundation_ks=, foundation_width=); validation: both finite
#   and >= 0.
```

When `foundation_k_line > 0` a member rests on a Winkler bed of vertical
(global -Z) grounded springs.

* **Mesher** (`skyframe/core/mesh.py`): a foundation member is DISCRETIZED into
  N equal segments (intermediate nodes inserted into the global point pool),
  merged with any shell-edge split cuts.  N is chosen so the segment length
  `<= min(L / 8, lc / 4)` where the Hetenyi characteristic length
  `lc = (4 E I33 / k_line)^0.25` (`I33` includes `mod_I33`); N is floored at 8
  and capped at 40.  Controls are the module globals
  `FOUNDATION_MIN_SEGMENTS = 8`, `FOUNDATION_MAX_SEGMENTS = 40`,
  `FOUNDATION_SEGS_PER_LC = 4.0` (exposed for convergence testing);
  `foundation_segment_count(model, member)` returns N (1 when inactive).  A
  non-foundation member is never split by this path (numbering unchanged, so
  pre-v0.11 models mesh identically).
* **Engine**: a grounded Z spring of stiffness `k_line * tributary_length`
  (tributary = half of each adjacent segment) is lumped at every node of the
  discretized member, reusing the v0.8 grounded-`zeroLength`-to-fixed-node
  spring mechanism (Z dof only).  The sprung Z dof is cleared of any base
  fixity, the spring reaction `-k*uz` enters the case `reactions` and base
  totals, and the node counts as a support.  Member end forces / 11-station
  results still aggregate to the ORIGINAL member over its full length (the
  existing split-member re-aggregation); member loads distribute exactly over
  the sub-segments through the existing exact load path.
* Rigid end offsets are NOT supported together with a foundation (the member
  is multi-segment): the existing single-segment offset guard raises a clear
  error.

Hand-checked closed forms (Hetenyi beam on elastic foundation,
`beta = (k_line / (4 E I33))^0.25`, central point load P): max deflection
`y_max = P*beta/(2*k_line)`, max moment `M_max = P/(4*beta)` — matched within
3% at the automatic (fine) discretization on a long beam (L >> lc), the error
converging monotonically to the closed form as N grows; a rigid-ish short
footing settles nearly uniformly at `w = P/(k_line*L)` (1%); the soil-spring
reactions sum exactly to the applied downward load.

## Gravity load takedown / support-reaction summary

Top-level results gain `"takedown"` (ALWAYS present, may be `{}`), computed by
pure post-processing of the already-solved reactions for every static case and
every ADDITIVE combo:

```jsonc
"takedown": {
  "<case|combo>": {
    "supports": [{"node": tag, "grid": "A-1"|"", "x": 0, "y": 0,
                  "FZ": 0, "FX": 0, "FY": 0}],   // one per support node
    "total_FZ":   0.0,   // sum of support FZ reactions (kN)
    "applied_FZ": 0.0,   // INDEPENDENT sum of applied gravity (kN)
    "balance_ok": true   // |total_FZ - applied_FZ| within tolerance
  }
}
```

* Every real support node (base supports AND spring / foundation-spring nodes)
  appears once, labelled by its NEAREST grid intersection (`x_labels`-`y_labels`,
  e.g. corner `"A-1"`) when the model has a grid, else `""` (story = Base).
* `applied_FZ` is summed straight from the load definitions with the SAME
  vertical-load rules the engine applies (gravity member loads skip vertical
  members; self-weight and `global_z` loads act on all members; area loads use
  the meshed net tributary / membrane net area), so `balance_ok` is a genuine
  independent check.  A combo's `applied_FZ` folds the case pattern factors
  linearly, matching the linearly-superposed per-support reactions
  (`1.2*deadFZ + 1.6*liveFZ`, etc.).

## API additions

| Method | Path                      | Body / Response |
|--------|---------------------------|-----------------|
| POST   | `/api/member/foundation`  | `{member_uids:[...], ks, width}` -> sets `foundation_ks`/`foundation_width` on the named members, returns the model dict; 400 on unknown member, missing/negative `ks`/`width`, or empty list |

`POST /api/model` round-trips the FrameMember `foundation_ks`/`foundation_width`
fields; `POST /api/analyze` returns the `"takedown"` block automatically.

---

# v0.12 additions — auto steel section optimization, tension/compression-only members

## Auto steel section optimization (`skyframe/design/steel.py`)

Single-pass, demand-based sizing of steel W-shape members against the same
preliminary AISC 360-16 interaction check as `check_members`.

```python
@dataclass
class SectionSuggestion:
    uid: str
    current_section: str
    suggested_section: str = ""          # lightest passing library W-shape
    current_ratio: float | None = None   # H1 ratio of the CURRENT section
    suggested_ratio: float | None = None  # H1 ratio of the suggested section
    weight_kg_per_m: float = 0.0          # suggested A * 7850 (kg/m)
    status: str = "n/a"                   # "ok" | "no_section_passes" | "n/a"

optimize_members(model, results, case_or_combo, candidates=None, *,
                 target_ratio=0.95, Fy=345_000.0, kx=1.0, ky=1.0, Lb=None,
                 phi_b=0.9, phi_c=0.9, iterate=0) -> list[SectionSuggestion]
apply_suggestions(model, suggestions) -> model
```

* For every frame member with an analysis demand in `case_or_combo` (Pu,
  Mu33, Mu22 extracted EXACTLY as `check_members` does — `member_forces[0]`
  is +compression, moments are max |M| over stations/ends), pick the LIGHTEST
  library W-shape (candidates default to the whole library, sorted by
  area/weight ascending; deterministic name tie-break) whose governing AISC
  H1 interaction ratio is `<= target_ratio` for that demand.  The scoring
  reuses the `check_members` capacity math (E3 / D2 / F2 / F6 / H1) with the
  candidate's own library properties, so a candidate is evaluated without
  being added to the model.
* `status`: `"ok"` (a passing section found), `"no_section_passes"` (the
  demand exceeds every candidate at target — `suggested_section` stays `""`),
  `"n/a"` (member has no forces in the results).  `current_ratio` is the H1
  ratio of the member's CURRENT section when it is a recognised library
  W-shape, else `None`.
* **Single-pass caveat (documented):** the optimizer uses the CURRENT
  analysis demands.  A rigorous redesign re-analyses after resizing (member
  stiffnesses change, so forces redistribute); one re-analyse + re-optimize
  loop converges for typical cases.  `iterate=1` performs exactly ONE such
  loop — it applies the suggestions to a DEEP COPY of the model (the caller's
  model is never mutated), re-runs the engine, and re-optimizes; the returned
  suggestions then reflect the resized model.
* `apply_suggestions` assigns each `"ok"` suggestion's section onto its
  member, creating any missing library `FrameSection` via
  `FrameSection.from_library` (inheriting the member's current-section
  material, else any model material); returns the mutated model.
* Exported from `skyframe.design` and `skyframe.design.steel`.

## Tension/compression-only members (braces, cables, ties)

```python
# FrameMember gains (round-trips; absent key / pre-v0.12 = "both"):
#   axial_limit: str = "both"     # "both" | "tension" | "compression"
# add_member(..., axial_limit="both"); validation: must be one of the three.
```

A member with `axial_limit != "both"` is modelled as a 2-force **Truss**
(axial-only, no bending/shear/torsion).  The uniaxial material is
`uniaxialMaterial('Elastic', E, 0.0, Eneg)` — `E` is the tangent for tension
(+strain), `Eneg` for compression (−strain):

* **tension-only** — full `E` in tension, `E * AXIAL_ONLY_RATIO` (1e-6) in
  compression (the tie/cable goes slack in compression);
* **compression-only** — the mirror (full `E` in compression, tiny in
  tension: a strut that releases in tension).

The tiny residual modulus keeps the released direction from creating a
rigid-body mechanism (the system stays regular); in a redundant load path the
released member sheds > 99.99% of its force while the active direction is
exact (a determinate single member still carries its load — there is no
alternate path).  Because these materials switch stiffness by strain sign the
response is NONLINEAR: **a static case containing ANY non-"both" member is
solved with Newton** (`_setup_nonlinear_analysis`: `test NormDispIncr 1e-8`,
`algorithm Newton`), reusing the pushover / nonlinear-TH path.

Reported results for an axial-only member: `member_forces` is
`[-N, 0,0,0,0,0, N, 0,0,0,0,0]` (N tension-positive; `member_forces[0]`
follows the engine +compression convention, as for beams); all 11 stations
carry the constant axial `N` (tension-positive) with V2/V3/T/M2/M3 = 0.
Axial-only members do NOT support shell-edge splitting, rigid end offsets,
plastic hinges, or transverse/thermal member loads (a distributed load on one
— including self-weight — is skipped: a Truss rejects `eleLoad`).  A truss-only
node's rotations are auto-restrained (a Truss adds no rotational stiffness);
translational truss-mechanism stability remains the user's modelling
responsibility, as for any truss model.

**Hand-checks:** a single-bay X-braced frame under a lateral H — the stretched
diagonal carries `T = H / cos(theta)` (`cos(theta) = B / L_diag`) and the
other diagonal deactivates (~0), base FX balancing H (1e-6); a tension-only
tie under a compressive demand carries < 1e-3 of the equivalent elastic
member; the compression-only mirror likewise.

## API

| Method | Path                    | Body / Response |
|--------|-------------------------|-----------------|
| POST   | `/api/design/optimize`  | `{case, target_ratio?, candidates?, apply?, Fy?, kx?, ky?, Lb?}` -> runs analysis + `optimize_members`; `{case, applied, suggestions:[SectionSuggestion]}` (and `"model"` = updated model dict when `apply` true).  400 on missing `case`, bad `candidates`, `target_ratio <= 0`, or no OpenSees |

`POST /api/model` round-trips the FrameMember `axial_limit` field (400 on a
value other than `both`/`tension`/`compression`).

---

# v0.13 additions — section cuts, named RS/TH function library, EC8 preset

## Section cuts (force integration across a plane)

```python
@dataclass SectionCut:              # model.section_cuts: List[SectionCut]
    name: str
    axis: str                        # "x" | "y" | "z" (the cut-plane normal)
    coord: float                     # plane is  axis == coord
    x_range: List[float] | None = None   # optional [lo, hi] bounding box that
    y_range: List[float] | None = None   #   limits the cut extent (a crossing
    z_range: List[float] | None = None   #   counts only if inside every range)
# BuildingModel.add_section_cut(name, axis, coord, x_range=, y_range=,
#   z_range=); round-trips as "section_cuts" (absent key = none).
```

**Engine (pure post-processing — no extra solve, cheap summation from the
already-computed `member_stations`).**  For each cut and each **static case +
additive combo**, every FRAME member that SPANS `coord` along `axis` (and
whose crossing point lies inside the optional ranges) contributes: the
member's 11-station internal forces (N, V2, V3, T, M2, M3) are LINEARLY
interpolated at the exact crossing station and transformed local→global via
the engine's own `_local_axes` triad, then summed into a resultant.

**Sign convention (documented, ETABS-style):** the resultant is the internal
force that the material on the NEGATIVE-coordinate side of the plane exerts on
the material on the POSITIVE side (per member, `sign = -sign(coord_j -
coord_i)`).  For a horizontal `z` cut this is "force from below supporting
above": a downward tip load P gives `FZ = +P`; a mid-height cut reports
`FZ = +weight above`; a lateral +H applied above gives the shear the lower
part feeds the upper part, `FX = -H` (so `|FX| = H`, story-shear equilibrium).
A cantilever cut at distance a has `|MY| = P*(L-a)` (v0.13 sign: `MY =
-P*(L-a)`).  Moments are taken about the cut CENTROID (mean of the crossing
points), so a single crossing member reports its own station moment.

```jsonc
results["section_cuts"]["<case|combo>"]["<cut name>"] = {
  "FX":0,"FY":0,"FZ":0,        // total transmitted force (kN), global axes
  "MX":0,"MY":0,"MZ":0,        // moment about the cut centroid (kN*m)
  "n_members": 0,              // frame members crossing (and in-range)
  "n_shells":  0,              // shells crossing (counted, NOT integrated)
  "warnings": [ ... ]          // set when n_shells>0 (shells excluded)
}
```

Top-level `"section_cuts"` key is ALWAYS present (may be `{}`).  **Shells that
cross a cut are COUNTED (`n_shells`) but EXCLUDED from the resultant in v0.13
(frame members only) with an explicit warning — documented limitation.**  The
post-processing never alters or slows the existing case/combo results.

## Named RS / TH function library (reusable spectra & records)

```python
@dataclass SpectrumFunction:       # model.spectrum_functions: Dict[str, ..]
    name: str
    points: List[[T, Sa_g]]        # same meaning as an RS-case spectrum
    damping: float = 0.05          # metadata (CQC uses the CASE damping)
@dataclass TimeHistoryFunction:    # model.th_functions: Dict[str, ..]
    name: str
    values: List[float]            # ground accel record, m/s^2
    dt: float                      # s
# BuildingModel.add_spectrum_function(name, points, damping=0.05);
# BuildingModel.add_th_function(name, values, dt).  Both round-trip.
```

`ResponseSpectrumCase` gains `function: str = ""` and `TimeHistoryCase` gains
`function: str = ""`.  When set, the case USES the named function's
points / values+dt in place of its own inline `spectrum` / `accel`+`dt` (the
case `scale`, `num_modes`, `combo_method`, `damping` still apply).  The engine
resolves the reference (`_resolve_rs_spectrum` / `_resolve_th_record`); a
missing name raises a clear `ValueError`, and `model.validate()` also rejects
a case whose `function` is undefined (→ `POST /api/model` 400).  A case with
no function keeps its inline data and behaves EXACTLY as before (an RS case
referencing a function yields identical results to the same points inline,
1e-12).  Cases may leave `spectrum`/`accel` empty when a function is named.

## Eurocode 8 elastic response spectrum preset (EN 1998-1 §3.2.2.2, Type 1)

```python
from skyframe.core.model import (eurocode8_spectrum, eurocode8_se,
                                 eurocode8_damping_correction, EC8_TYPE1_GROUND)
eurocode8_spectrum(ag, ground_type="A", damping=0.05, T_max=4.0, dT=0.05)
    -> [[T, Sa_g], ...]
```

`ag` is the design ground acceleration in units of **g** (so `Sa` is in g).
`EC8_TYPE1_GROUND` holds Table 3.2 `(S, TB, TC, TD)` per ground type A-E
(A: 1.00/0.15/0.4/2.0, B: 1.20/0.15/0.5/2.0, C: 1.15/0.20/0.6/2.0,
D: 1.35/0.20/0.8/2.0, E: 1.40/0.15/0.5/2.0).  The elastic branches (eqs.
3.2-3.5): ramp `ag*S*[1+T/TB*(2.5eta-1)]` on [0,TB], plateau `2.5*ag*S*eta`
on [TB,TC], `2.5*ag*S*eta*(TC/T)` on [TC,TD], `2.5*ag*S*eta*(TC*TD/T^2)`
beyond; `eta = sqrt(10/(5+xi)) >= 0.55`, xi in % (eta=1 at 5%).  The sampled
point list includes every corner exactly (0, TB, TC, 2*TC, TD, T_max) so
`Sa(TC) == plateau` and `Sa(2*TC) == plateau/2` hold to machine precision;
suitable for a `SpectrumFunction` / `ResponseSpectrumCase`.

## API

| Method | Path               | Body / Response |
|--------|--------------------|-----------------|
| POST   | `/api/section-cut` | `{name, axis, coord, x_range?, y_range?, z_range?}` → adds a cut, returns model dict; 400 on bad axis / missing coord / bad range |

`POST /api/model` round-trips `section_cuts`, `spectrum_functions`,
`th_functions`, and the RS/TH-case `function` fields (400 when a case names an
undefined function); `POST /api/analyze` returns the `"section_cuts"` block
automatically.

---

# v0.14 additions — multiple / rotated / radial grid systems

Grids are DRAFTING AIDS ONLY.  Members store absolute GLOBAL coordinates, so
grid systems never enter the analysis engine — this wave is a model +
snapping-helper feature; the engine and all prior results are unchanged.

## GridSystem redesign (`skyframe/core/model.py`, backward compatible)

```python
@dataclass
class GridSystem:
    x_lines: List[float] = []       # LOCAL-frame x grid coords (orthogonal)
    y_lines: List[float] = []       # LOCAL-frame y grid coords (orthogonal)
    name: str = "G1"
    origin: Tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0           # degrees, CCW about origin
    kind: str = "orthogonal"        # "orthogonal" | "radial"
    radii: List[float] = []         # radial: concentric-circle radii (m, > 0)
    theta_deg: List[float] = []     # radial: spoke angles (degrees)
```

* Constructing `GridSystem(x_lines, y_lines)` positionally still works;
  `x_labels` (`A, B, ...`) and `y_labels` (`1, 2, ...`) are unchanged.
* **Local → global transform** (documented): rotate CCW by `rotation` about
  the origin, then translate — `gx = ox + lx*cos - ly*sin`,
  `gy = oy + lx*sin + ly*cos` (`GridSystem.to_global(lx, ly)`).
* `lines_global()` → drawable segments in GLOBAL coords, each
  `{"label", "points": [[x, y], ...]}`: orthogonal grid lines / radial spokes
  are 2-point segments; radial circles are closed polylines (72 segments).
  Circles are labelled `R1, R2, ...`; spokes by angle (e.g. `"30°"`).
* `intersections_global()` → labelled snap points, each `{"label", "point"}`:
  orthogonal line crossings `"A-1"`; radial circle×spoke crossings
  `"R1-30°"`.
* `snap(px, py, tol)` → the nearest intersection `{"label", "point"}` within
  `tol`, else `None`.
* `to_dict()` emits every field (plus derived `x_labels`/`y_labels` so old
  readers keep working); `GridSystem.from_dict(d)` restores them (absent new
  keys default: name `"G1"`, origin `(0,0)`, rotation `0`, kind
  `"orthogonal"`, empty radii/theta).

## BuildingModel

```python
grid: Optional[GridSystem] = None            # PRIMARY/legacy grid (unchanged)
grid_systems: List[GridSystem] = []          # v0.14 full list
```

* `effective_grids()` → `grid_systems` if any, else `[grid]` if set, else `[]`.
* `all_grid_lines_global()` / `all_intersections_global()` aggregate across
  every grid system (each entry gains a `"system"` key = the grid name).
* `snap(px, py, tol)` → nearest intersection across ALL grid systems (tagged
  with `"system"`), else `None`.
* `set_grid_system(gs)` adds `gs`, or replaces the system with the same
  `name`; migrates a legacy single `grid` into `grid_systems` on first use and
  keeps `grid` pointed at the primary (first) system.
* **Serialisation:** `to_dict()` emits BOTH `grid` (the primary/first system,
  for old readers) and `grid_systems` (the full list).  `from_dict()`: if
  `grid_systems` is present it is used and `grid` is set to the first entry;
  else a legacy `grid` is wrapped as a one-element `grid_systems` (both are
  populated).  A model with no grid emits `grid: null`, `grid_systems: []`.
* **plan_extents / plan_center** consider the UNION of every grid system's
  global line extents (an orthogonal grid contributes only with BOTH line
  families, a radial grid only with radii; otherwise the member-bounding-box
  fallback applies).  A single orthogonal grid at origin/rotation 0 reproduces
  the pre-v0.14 extents exactly.
* **Validation** (`validate()`): `kind` ∈ {orthogonal, radial}; origin two
  finite numbers; rotation finite; a radial grid needs ≥ 1 radius (all
  finite > 0) and finite `theta_deg`.

## API

| Method | Path        | Body / Response |
|--------|-------------|-----------------|
| POST   | `/api/grid` | `{name, kind?, origin?, rotation?, x_lines?, y_lines?, radii?, theta_deg?}` → adds/replaces the named grid system, returns model dict; 400 on bad input (e.g. radial without `radii`) |

`kind` defaults to `"orthogonal"`, `origin` to `[0, 0]`, `rotation` to `0`.
`POST /api/model` round-trips `grid_systems` (and the legacy `grid`) through
`from_dict`.

## Backward-compat rules (summary)

* Positional `GridSystem(x_lines, y_lines)` and `x_labels`/`y_labels` unchanged;
  builders/importers that set `model.grid` need no change.
* `to_dict()` keeps the legacy `grid` key (now with extra fields) AND adds
  `grid_systems`; `from_dict()` reads either.  `from_dict(to_dict(m))` is an
  exact round-trip; `quick_building()` plan extents/center are unchanged.
* The engine is untouched: grid geometry never affects analysis (members carry
  global coordinates); the full pre-existing suite stays green.

---

# v0.15 additions — advanced link types (seismic devices), wall piers

## Advanced link types (`skyframe/core/model.py`, engine)

```python
# LinkMember gains (both round-trip; absent keys / pre-v0.15 = elastic):
#   link_type: str = "elastic"     # "elastic"|"damper"|"gap"|"hook"|"isolator"
#   params: Dict[str, float] = {}  # device parameters (below)
# add_link(pi, pj, stiffness=None, uid="", link_type="elastic", params=None)
#   — `stiffness` is the v0.5 6-entry global spring vector, REQUIRED (at
#   least one entry > 0) for "elastic" and unused for the device types.
LINK_TYPES = ("elastic", "damper", "gap", "hook", "isolator")
DAMPER_DEFAULT_ALPHA = 1.0; DAMPER_DEFAULT_K = 1e6; ISOLATOR_DEFAULT_KV = 1e7
```

Device parameter sets (validated for completeness — a missing required key,
an unknown key, or an out-of-range value raises / `POST /api/model` 400s):

* **damper** `{cd (kN*s/m, required > 0), alpha (default 1.0, in (0, 2]),
  k (series spring, default 1e6 kN/m)}` — an AXIAL Maxwell viscous damper
  along the link axis: `uniaxialMaterial('ViscousDamper', k, cd, alpha)` in
  a `twoNodeLink` on the link axis (dir 1 = axial).  A damper carries **no
  static force and no eigen stiffness** (verified empirically on openseespy
  3.7.1.2: static results with/without the damper element are IDENTICAL and
  the modal frequencies are unchanged), so damper elements exist in every
  build and static/modal paths stay linear.  The two points must be
  distinct (the axis defines the damper direction).
* **gap** `{k (kN/m, > 0), gap (m, >= 0)}` — compression-only contact along
  the link axis that engages once the pair CLOSES by more than `gap`:
  `uniaxialMaterial('ElasticPPGap', k, -1e12, -gap)` (huge yield = the
  closed contact stays elastic, force `k*(closing - gap)`).  **hook**
  `{k, slack}` is the tension mirror: `ElasticPPGap(k, +1e12, +slack)`,
  engaging after the pair OPENS by more than `slack`.  Sign conventions
  verified empirically (twoNodeLink dir-1 strain = elongation).
* **isolator** `{k1 (kN/m, > 0), k2 (0 <= k2 < k1), Fy (kN, > 0),
  kv (vertical, default 1e7)}` — a base-isolation bearing: bilinear
  `Steel01(Fy, k1, b=k2/k1)` shear in BOTH horizontal directions, elastic
  vertical `kv`, rotations free (uncoupled rectangular yield surface —
  the standard two-spring idealization).  The link axis must be VERTICAL:
  finite length -> `twoNodeLink` (dir 1 = axial `kv`, dirs 2/3 = shear;
  shear moments transfer in equilibrium), zero length -> `zeroLength` on
  the global axes (1/2 = shear, 3 = vertical).  Static response: initial
  stiffness `k1` below `Fy`, bilinear beyond; TH: hysteretic.

**Solve routing:** a model containing any gap/hook/isolator link solves
every static case with Newton (the v0.12 axial-only machinery,
`NONLINEAR_STATIC_LINK_TYPES`); dampers alone keep the linear static path
(no static force).  ANY device link makes every TH case run Newton
(`NormDispIncr 1e-8, 25`, NewtonLineSearch retry per failed step) with
committed-stiffness Rayleigh proportionality (`betaKcomm`), even with no
hinges; pure-elastic-link models keep the exact pre-v0.15 behavior.

**Ground anchors:** a node connected ONLY to device links (no frame/shell/
elastic-link/grounded-spring stiffness) is a GROUNDED ANCHOR — fully fixed
(never joins a rigid diaphragm or story node set) and reported as a
support, so its reaction exposes the device force and enters the base
totals (a gap link's contact force shows up as `reactions[anchor]`).

Hand-checks (all verified in tests): free-vibration log-decrement damping
`zeta_add = cd/(2 m wn)` recovered to ~0.1% at zeta = 0.08; gap/hook force
zero at half-gap and `k*(delta - gap)` past it (1e-6 vs a numpy two-spring
solve); isolator elastic period `2*pi*sqrt(m/(4 k1))` (1e-3), bilinear
static law exact, and nonlinear-TH peak within 2% (measured ~0.0%) of an
independent numpy bilinear-kinematic Newmark integrator.

## Wall piers (per-story wall design forces)

```python
# ShellRegion gains: pier: str = ""    # pier label; round-trips
# BuildingModel gains: auto_pier_walls: bool = False
#   (True: every unlabeled wall is auto-labeled with its uid)
```

For each labeled meshed VERTICAL wall (kind "wall", behavior "shell"), each
story it spans, and each static case + additive combo, the engine reports
in-plane pier resultants integrated across a horizontal cut at the story
bottom:

```jsonc
results["piers"]["<case|combo>"]["<pier label>"]["<story>"] =
    {"P": 0.0, "V": 0.0, "M": 0.0}      // kN, kN, kN*m
```

**Method (EXACT free body, not gauss sampling):** at solve time the engine
captures each shell element's 24-component global nodal resisting-force
vector (ShellMITC4 `'forces'`).  The cut runs along the highest mesh node
line at (or, for a non-aligned mesh, just below) the story-bottom
elevation; summing the nodal forces of the wall elements ABOVE the line at
the cut-line nodes gives the exact transmitted force.  Element local axes
never enter (the wall-local orientation of the mesher's quads — element
local x along the region's u direction — was verified empirically, but the
nodal-force kernel is orientation-free; gauss-resultant integration was
rejected: it missed the clamped-base shear by ~8%).

* Axes: `hhat = ez x nhat` (`nhat` = the CCW-corner plane normal) — for the
  natural bottom-edge-first corner ordering `hhat` follows corner 0 -> 1.
* `P = +sum(fz)` — POSITIVE = COMPRESSION (documented sign).
* `V = -sum(f . hhat)` — positive along `+hhat` (the sense of a lateral
  load applied above the cut).
* `M = sum((s - s_bar) * fz + m . (hhat x ez))` about the net-section
  centroid `s_bar` (tributary-width-weighted mean of the cut-node
  positions): the vertical-force couple PLUS the nodal drilling moments
  (which close the balance exactly — verified: a V0 = 50 kN top load gives
  M = 150.000000 kN*m at a 3 m cut).  Out-of-plane plate moments never
  enter the in-plane M.  The moment is then transferred from the cut line
  to the story-bottom elevation via `M += V*(z_line - z_bot)` (exact — no
  nodes, hence no loads, exist between the two levels).
* Walls sharing a pier label are summed per story (they should be
  co-planar/parallel); membrane-behavior walls (no FE) and non-vertical
  walls are skipped with a warning.  Top-level `"piers"` key is ALWAYS
  present (may be `{}`); combo piers superpose exactly (nodal forces are
  combined linearly first).

Hand-checks: cantilever wall base pier V = V0 and P = P0 to 1e-9 (spec
ceilings 2% / 1%), M = V0*h to 1e-9 (spec 4%) — cf. the v0.4 wall Nxy
section-cut precedent, now exact; a 2-story wall carries V = V0 at both
story cuts and M = V0*h2 / V0*(h1+h2) at the story-2 / base cuts.

## API

No new endpoints: `POST /api/model` round-trips `link_type`/`params` (400
on a bad type or bad/incomplete params via `validate()`), `pier`, and
`auto_pier_walls`; `POST /api/analyze` returns the `"piers"` block
automatically.

---

# v0.16 additions — beam deflection recovery + serviceability, live-load reduction, biaxial concrete columns

## Beam transverse deflection recovery (engine)

For every STATIC case and every ADDITIVE combo, each case block gains

```jsonc
"member_deflections": {"<uid>": {"x": [0, ..., L],   // the 11 stations (m)
                                 "dy": [ ... ],       // LOCAL y deflection (m)
                                 "dz": [ ... ]}}      // LOCAL z deflection (m)
```

**Method (EXACT for prismatic Euler members, closed form — no quadrature).**
Per mesh segment the elastic line satisfies ``EI v'' = Mb(x)`` with the
statics-exact bending-moment field already used by the stations (corrected
local end-i forces + recorded span loads).  The two integration constants
come from the segment end NODE displacements transformed to the member local
axes:

    v(xi)  = v_i + theta0*xi + I(xi)/EI,
    theta0 = (v_j - v_i - I(L_seg)/EI) / L_seg,
    I(xi)  = closed-form Macaulay double integral of Mb
             (point load: p<xi-x0>^3/6; linear-varying distributed load:
              w1<xi-xa>^4/24 + s<xi-xa>^5/120 - w2<xi-xb>^4/24 - s<xi-xb>^5/120).

Only end DISPLACEMENTS enter — no end rotations — so moment releases are
exact (the released-end rotation is implied by the zero end moment in
``Mb``); the x-z plane uses the conjugate pair ``(V_i = fi[2],
Mb_i = -fi[4])``.  Split members (shell-edge / foundation discretization)
are recovered segment by segment (exact); stiffness modifiers enter through
the effective EI.  Skipped: axial-only (Truss) members (no bending) and
members with rigid end offsets (the elastic element spans untracked offset
nodes) — no entry is emitted.  Additive combos superpose dy/dz linearly;
RS/TH/envelope results carry an empty ``member_deflections`` (a positive
envelope of a signed deflection is not a serviceability quantity).
P-Delta cases report the same linear-statics recovery of their (incremental)
state — approximate inside the span, consistent with their stations.

Hand-checked closed forms (all 1e-6, most machine precision): SS UDL
``5wL^4/384EI`` at midspan; fixed-fixed UDL ``wL^4/384EI`` (and the full
elastic line ``w x^2 (L-x)^2 / 24EI``); SS central point load ``PL^3/48EI``;
cantilever tip load ``PL^3/3EI`` with the whole station curve equal to the
analytic cubic ``P x^2 (3L-x)/6EI``; a portal-frame beam (ends rotate AND
settle) matches an independent numpy fine-mesh Euler FE with the same end
conditions pointwise.

## Beam serviceability checks

```python
# BuildingModel gains (round-trips; absent key = 360.0; finite > 0):
#   deflection_limit: float = 360.0     # limit is L / deflection_limit
```

Top-level results gain ``deflection_checks`` (ALWAYS present, may be ``{}``),
per static case + additive combo, BEAMS only:

```jsonc
"deflection_checks": {"<case|combo>": [{
    "uid": "B1", "story": "Story1", "L": 6.0,
    "max_abs_dy": 0.0025,        // max |dy RELATIVE TO THE CHORD| (m) — the
                                 // straight line between the two end
                                 // deflections; support settlement / joint
                                 // displacement does not count against the span
    "ratio_str": "L/2400",       // L / max_abs_dy, rounded ("L/inf" at ~0)
    "limit": "L/360",            // from model.deflection_limit
    "ok": true                   // max_abs_dy <= L / deflection_limit
}]}
```

## ASCE 7-16 §4.7 live-load reduction (`skyframe/core/codes.py`)

```python
live_load_reduction(model) -> {column uid: {"KLL", "At", "R", "n_stories"}}
```

SI form of Eq. 4.7-1: ``R = 0.25 + 4.57/sqrt(KLL*At)`` (At in m^2), clamped
to **[0.4, 1.0]** — floor **0.5** for a column supporting ONE floor, 0.4 for
two or more (§4.7.2).  ``KLL = 4`` UNIFORMLY (`LIVE_KLL_COLUMN` — the Table
4.7-1 value for interior and exterior columns without cantilever slabs; the
R = 1 cap embodies the 400 ft^2 = 37.1 m^2 applicability threshold, where
the formula crosses 1).  ``At`` = tributary plan area x stories supported
at/above the column top:

* **Tributary rule** — half of every beam span framing into the column top,
  per plan direction: ``At_floor = (sum Lx/2) * (sum Ly/2)`` (interior
  column of 6 m bays: 36 m^2; corner: 9 m^2; edge: 18 m^2).  Beams classify
  x/y by dominant plan direction (skewed framing approximated).
* **Fallback / limits** — a column with no attached beams in both directions
  at its top elevation (irregular layouts, transfer levels, walls-only
  floors) falls back to ``plan_area / n_columns_at_that_level`` with a
  ``UserWarning``; a degenerate model (no plan area) reports R = 1.

**This is a DESIGN-STAGE reduction — analysis results are never modified.**
The helper

```python
reduce_live_demands(results_dict, model, *, live_case="LIVE", reduction=None)
```

returns a DEEP-COPIED results dict in which each column's member demands
(12 end forces + all station columns) have their live-attributable share
scaled by that column's R.  Linearity makes the attribution exact:
``q_adj = q_total + (R-1) * f_live * q_LIVE`` with ``f_live`` = 1 for the
live case itself, the combo's LIVE-case factor for ADDITIVE combos, 0 for
other plain cases; envelope combos and RS cases are left UNCHANGED
(documented limitation — their live share is not a single linear factor).
Raises on an unknown ``live_case``.

## Biaxial concrete column check — Bresler (`skyframe/design/concrete.py`)

`ConcreteCheck` gains ``Mu22`` (max |M2| demand), ``biaxial: bool``,
``ratio_biaxial: float|None``, ``method: "uniaxial"|"bresler"|"contour"``
(all serialised).  A column goes BIAXIAL when BOTH Mu33 and Mu22 exceed
``BIAXIAL_TRIGGER = 5%`` of the respective axis's PEAK uniaxial phiMn.  The
M2-axis interaction diagram reuses the SAME strain-compatibility code with
b/h swapped and the SAME symmetric two-face layout (documented
approximation: equal steel about both axes).

* ``Pu >= 0.1 fc' Ag`` — **Bresler reciprocal load method** (Bresler 1960;
  PCA Notes / ACI R10.3.6): ``1/phiPn_b = 1/phiPn_x + 1/phiPn_y - 1/phiP0``
  with ``phiPn_x/y`` the COMPRESSION-side axial capacity of each uniaxial
  diagram at that axis's moment demand (`axial_capacity_at_moment`) and
  ``phiP0 = 0.65 * P0`` the UNCAPPED pure-compression design point (the
  0.80 tied-column cap is an accidental-eccentricity device and stays on
  the uniaxial diagram; Bresler's identity needs the true P0).
  ``ratio_biaxial = Pu / phiPn_b``.
* ``Pu < 0.1 fc' Ag`` — **load-contour fallback** (the reciprocal method's
  documented applicability limit): ``(Mu33/phiMnx)^1.5 + (Mu22/phiMny)^1.5
  <= 1`` with phiMn at the demand axial level (`moment_capacity_at_axial`);
  exponent ``CONTOUR_EXPONENT = 1.5`` (PCA range 1.15-1.55).
  ``ratio_biaxial`` = the contour value.

The reported ``ratio`` = max(uniaxial M3 radial ratio, ratio_biaxial);
``status`` follows it.  A pure-uniaxial demand (either moment under the 5%
trigger) keeps the v0.6 path BIT-IDENTICAL (method "uniaxial",
ratio_biaxial null).  New exports: `axial_capacity_at_moment`,
`moment_capacity_at_axial`, `BIAXIAL_TRIGGER`, `CONTOUR_EXPONENT` (also via
`skyframe.design`).

Hand-checks: square symmetric column with equal demands both axes →
``1/phiPn_b = 2/phiPn_x - 1/phiP0`` verified to 1e-9 against an independent
polyline intersection + hand P0; contour value hand-verified at low axial;
uniaxial regression 1e-12.

## API additions

| Method | Path                  | Body / Response |
|--------|-----------------------|-----------------|
| GET    | `/api/live-reduction` | → `{"factors": {uid: {KLL, At, R, n_stories}}}` for the current model (pure geometry, no analysis) |

`POST /api/design/steel` and `POST /api/design/concrete` accept optional
``"live_reduction": true`` + ``"live_case": "LIVE"`` — demands are adjusted
via `reduce_live_demands` before checking (single-case AND combo-envelope
paths) and the response carries the ``"live_reduction"`` factors; 400 on an
unknown live case.  `POST /api/model` round-trips ``deflection_limit``;
`POST /api/analyze` returns ``member_deflections`` (per static case /
additive combo) and the top-level ``deflection_checks`` block automatically.

# v0.17 additions — vertical seismic component (Ev), panel zones

## Vertical seismic component Ev (`skyframe/core/codes.py`)

```python
def asce7_combinations(model, standard="LRFD", SDS=None): ...
# SDS: Optional[float], finite >= 0 (bool rejected); None (default)
#   reproduces the pre-v0.17 output EXACTLY, name-for-name.
```

When ``SDS`` is given, the SEISMIC combinations fold the vertical seismic
component ``Ev = 0.2 * SDS * D`` (ASCE 7-16 §12.4.2.2 Eq. 12.4-4a) into the
DEAD factor per §12.4.2.3, redundancy ``rho = 1``:

* LRFD (basic combos 6 / 7)::

      (1.2 + 0.2*SDS) D + 1.0 L ± 1.0 QE     # E = Eh + Ev
      (0.9 - 0.2*SDS) D ± 1.0 QE             # E = Eh - Ev

* ASD (§2.4.5 combos 8 / 9 / 10)::

      (1.0 + 0.14*SDS)  D ± 0.7 QE
      (1.0 + 0.105*SDS) D ± 0.525 QE + 0.75 L
      (0.6 - 0.14*SDS)  D ± 0.7 QE

The ``±`` sign variants reverse only the HORIZONTAL component; Ev's sign is
fixed by the combination form (additive in gravity-heavy combos, subtractive
in uplift combos).  WIND and gravity-only combinations are UNCHANGED (Ev is
seismic-only).  Combo names carry the effective dead factor via ``%g``
formatting (e.g. ``SDS=1.0`` → ``1.4D+1.0L+1.0EQX``, ``0.7D+1.0EQX``).
``apply_asce7_combinations`` forwards ``SDS`` unchanged.

Hand-checks: SDS=1.0 LRFD dead factors 1.4/0.7 exact; SDS=0.5 ASD
1.07/1.0525/0.53; SDS=0 ≡ SDS=None bit-identical.

## Panel zones (`skyframe/core/model.py`, `skyframe/engine/opensees_engine.py`)

```python
PANEL_ZONE_OPTIONS = ("none", "rigid", "scissors")
# BuildingModel gains (round-trips; absent key = "none"; validate() raises
# ValueError on any other value):
#   panel_zones: str = "none"
```

Model-level ETABS-style beam-column joint assumption, applied INTERNALLY by
the engine at build time — the user's model data is NEVER mutated:

* ``"none"`` — centerline modeling; the exact pre-v0.17 behavior.
* ``"rigid"`` — automatic rigid end zones at every interior beam-column
  joint (a deduped point where >= 1 column end and >= 1 beam end meet;
  axial-only members never participate).  Rule (``compute_panel_zone_offsets``,
  returns ``{uid: (off_i, off_j)}`` for EVERY member):
  each COLUMN end at the joint gets ``max(connecting beam h) / 2``; each
  BEAM end gets ``max(connecting column h) / 2``.  USER-SET explicit
  offsets (``rigid_i/rigid_j > 0``) win per member end; sections with
  ``h == 0`` contribute nothing; if the combined offsets would consume the
  whole member length, or the mesher split the member (shell-edge /
  Winkler discretization — the v0.9 offset machinery is single-segment
  only), the member reverts to its user offsets with a ``UserWarning``.
  Offsets flow through the EXACT v0.9 rigid-offset transform.
* ``"scissors"`` — elastic scissors panel-zone spring (Krawinkler/Charney
  idealization).  Per interior joint (``compute_panel_zone_springs``):

      K_theta = G * d_c * d_b * t_p

  ``G`` = governing column material shear modulus (kPa); ``d_c``/``t_p`` =
  that column section's ``h``/``b`` (rectangular web IS the panel — no
  doubler term in v0.17); ``d_b`` = deepest connecting beam ``h``.  The
  governing column is the deepest VERTICAL column at the joint.  In the
  built model the joint node is DUPLICATED: beams connect to the duplicate,
  a zeroLength rotational spring (global rx AND ry) of stiffness K_theta
  plus an equalDOF tie on ux/uy/uz/rz bridges original <-> duplicate.
  Fixed-end member-load moments on a redirected beam end land on the
  duplicate (else they would bypass the spring).  Skipped with a
  ``UserWarning``: joints with no vertical column, sections without
  drawing dimensions (b/h == 0), and support/restrained joints (the
  equalDOF tie would hide beam shear from the reaction).  ELASTIC panel
  stiffness only — Krawinkler's trilinear panel yielding is out of scope.

```python
engine.panel_zone_joints() -> List[dict]
#   [{"point": [x,y,z], "orig": tag, "dup": tag, "K": K_theta}, ...];
#   empty unless model.panel_zones == "scissors".
```

Hand-checks: rigid mode on a portal frame matches the SAME model with the
equivalent explicit rigid_i/rigid_j offsets to machine precision; scissors
K_theta hand-computed G*d_c*d_b*t_p exact; a stiff-spring scissors model
converges to the centerline model as K -> inf; "none" is bit-identical to
pre-v0.17 results.

## API additions

`POST /api/combos/asce7` accepts optional ``SDS`` (>= 0; 400 on negative /
non-finite / non-numeric) and forwards it to the combo generator.
`POST /api/model` round-trips ``panel_zones`` ("none" | "rigid" |
"scissors"; 400 on any other value via ``validate()``).

---

# v0.18 additions — shear wall design, punching shear, virtual-work diagrams

No new model fields: all three features are request-driven post-processing
(design preferences travel in the request, ETABS-style; the model dict is
byte-identical to v0.17).

## Shear wall design (`skyframe/design/wall.py`)

Uniform-reinforcing pier checks driven by the EXISTING v0.15 per-story pier
P/V/M free-body forces (``results["piers"]``).  Geometry per pier label from
the labeled wall regions (``pier_geometry``): ``Lw`` = horizontal in-plane
extent (corner projections onto ``hhat = ez x nhat``; walls sharing a label
SUM their extents per story), ``t`` = the first region's shell-section
thickness (mixed thickness noted), ``hs`` = story height, ``hw`` = TOTAL
stack height (z extent over the label's regions).  ``fc'`` = explicit
``fc_prime`` (kPa) or DERIVED from the wall concrete's modulus by inverting
ACI 19.2.2.1 (``fc_from_E``: ``fc'[MPa] = (E[MPa]/4700)^2``, conversions
explicit).  Defaults: ``rho_v = rho_h = 0.0025`` (ACI 11.6.1 minimum web
ratios), ``fy = 420_000`` kPa.

* **PMM** (``wall_interaction`` / ``wall_section_forces``) — rectangular
  section ``Lw x t``, UNIFORMLY DISTRIBUTED vertical steel ``Ast =
  rho_v*Lw*t`` split into ``N_STRIPS = 240`` midpoint strips (spec floor
  200; midpoint is exact on the linear strain ramp, the two yield-kink
  cells err at O(1/n^2) — measured ~3e-7 vs the exact smeared closed
  form).  Strain compatibility with the ACI rectangular block (``a =
  beta1*c``, beta1 per Table 22.2.2.4.3 via ``concrete.beta1``); phi per
  ACI 21.2 from the EXTREME-bar strain (outermost strip centroid,
  ``d_t = Lw*(1 - 0.5/n)``) via ``concrete.phi_from_strain``.  Diagram
  polyline: capped pure compression ``phiPn_max = 0.80*0.65*(0.85*fc'*
  (Ag - Ast) + fy*Ast)`` (tied cap, displaced concrete ignored at the
  sweep points — the same documented approximations as the v0.6 column),
  a 30-point descending-c sweep with the balanced point inserted, the
  bisected ``Pn = 0`` pure-bending point, and pure tension ``-fy*Ast``.
  ``ratio_pmm`` = RADIAL D/C in (|M|, P) space via the SAME
  ``_radial_ratio`` the column check uses; ``capacity_point`` =
  ``[|M|, P]/ratio``, the boundary crossing.  IN-PLANE bending only —
  out-of-plane wall bending is OUT OF SCOPE in v0.18.
* **Shear** (``wall_shear_strength``) — ACI 318-19 Eq. 11.5.4.3::

      alpha_c(psi) = 3.0 (hw/lw <= 1.5) | 2.0 (>= 2.0) | linear between
      vc [kPa] = alpha_c * 0.083 * lambda * sqrt(fc'[MPa]) * 1000
      Vn [kN]  = Acv * (vc + rho_h*fy),  Acv = Lw*t,  phi_v = 0.75
      cap (§18.10.4.4): Vn <= 0.66*sqrt(fc'[MPa])*1000*Acv

  (0.083/0.66 are the standard SI transcriptions of the 1/8 sqrt-psi
  coefficients; ``hw`` = the pier STACK height, ``lw`` = that story's Lw).
* **Boundary trigger** (``boundary_element_check``) — ACI §18.10.6.3:
  ``sigma = P/Ag + |M|*(Lw/2)/(t*Lw^3/12)``; required where ``sigma >
  0.2*fc'``.  Enveloped over ALL supplied combos (``sigma_max`` +
  ``sigma_combo``) — an "under any combo" condition, while the reported
  P/V/M/ratios come from the GOVERNING combo (largest
  max(ratio_pmm, ratio_shear)), exactly like the other design envelopes.

```python
check_wall_piers(model, results, combos=None, *, rho_v=0.0025,
                 rho_h=0.0025, fy=420000.0, fc_prime=None, ...)
    -> List[WallPierCheck]     # combos default: all ADDITIVE combos;
                               # static case names are accepted too (the
                               # piers block carries both); ValueError on
                               # no pier forces / unknown name / bad param
```

`WallPierCheck.to_dict()` (one entry per pier per story, story order):

```jsonc
{"pier": "P1", "story": "Story1", "Lw": 3.0, "t": 0.2, "hs": 3.0,
 "hw": 3.0, "hw_over_lw": 1.0, "alpha_c": 3.0, "fc": 28293.3,
 "fy": 420000.0, "rho_v": 0.0025, "rho_h": 0.0025,
 "P": 120.0, "V": 50.0, "M": 150.0,          // governing combo's (P +compr)
 "ratio_pmm": 0.02, "ratio_shear": 0.046, "phiVn": 1086.2,
 "shear_capped": false,
 "capacity_point": [7500.0, 6000.0],         // [phiMn, phiPn] on the ray
 "pm_points": [[0.0, 8263.7], ...],          // design polyline
 "boundary_required": false, "sigma_max": 700.0, "sigma_limit": 5658.7,
 "sigma_combo": "U", "status": "OK", "governing_combo": "U",
 "combo": "U",                    // ALIAS of governing_combo (web tables)
 "notes": [...], "preliminary": true}
```

Hand-checks (tests/test_wave19.py): P0/cap/pure-tension longhand exact;
balanced + pure-bending + general-c strip results vs an INDEPENDENT
closed-form smeared-steel integration (1e-3 spec, ~3e-7 measured); phi
transition band vs the ACI linear interpolation exact; both alpha_c
plateaus, the 1.75 midpoint (2.5) and the 0.66 cap longhand; sigma both
sides of 0.2fc'; engine-driven combo envelope P = 120 / V = 50 / M = 150
on the exact Wave-16 cantilever pier.

## Punching shear (`skyframe/design/punching.py`)

Two-way shear at every column supporting a MESHED SHELL slab (horizontal
``kind == "slab", behavior == "shell"`` region; membrane slabs have no FE
and are not checked).  Support detection: a column END node in the slab
plane (z within 1e-6) and inside its plan polygon (boundary INCLUSIVE);
columns drawn THROUGH a plane are not detected (split at stories, the
ETABS practice).

* ``Vu`` = the axial STEP at the level: ``C_below(top) - C_above(bottom)``
  from the member STATIONS (station N is tension-positive -> ``C = -N``;
  member end forces are +compression — CONTRACT v0.2/v0.6 signs).
  Positive = the floor delivers load DOWNWARD into the column stack; a
  transfer/mat gives a negative step (noted) and the check rates ``|Vu|``.
* Critical section (§22.6.4.1): ``c1, c2`` = the column section's drawing
  ``h, b``; ``d = t_slab - cover`` (``cover`` in METRES, default 0.03 m =
  25 mm clear + half a 16 mm bar, documented simplification; validated
  ``0 < cover < 1`` so an accidental millimetre value fails loudly);
  ``b0 = 2*(c1+d) + 2*(c2+d)``.
* ``vc`` = min of the three Table 22.6.5.2 formulas, SI transcriptions of
  the psi originals (4 | 2+4/beta | 2+alpha_s*d/b0)::

      vc1 = 0.33*lam*sqrt(fc'[MPa]);  vc2 = 0.17*(1 + 2/beta)*lam*sqrt(fc')
      vc3 = 0.083*(2 + alpha_s*d/b0)*lam*sqrt(fc')      [MPa -> kPa *1000]

  ``alpha_s = 40`` — INTERIOR columns only in v0.18 (edge/corner
  classification OUT of scope; every supported column is rated interior,
  noted on each result).  Unbalanced-moment transfer (gamma_v) and the
  318-19 size factor lambda_s are OUT of scope (318-14 forms).
  ``vu = |Vu|/(b0*d)``; ``ratio = vu/(0.75*vc)``.  fc' from ``fc_prime``
  or the SLAB material's E via ``fc_from_E``.

```python
check_punching(model, results, case=None, *, fc_prime=None, cover=0.03,
               phi=0.75, lam=1.0) -> List[PunchingCheck]
# case default: the first DEAD-classified case (default_gravity_case);
# unknown case -> KeyError; NO shell slabs -> [] (empty, not an error)
```

`PunchingCheck.to_dict()`:

```jsonc
{"uid": "C1", "story": "Story1", "slab": "S1", "case": "GRAV",
 "Vu": 90.0, "vu": 232.2, "vc": 1807.5, "phi_vc": 1355.6,
 "b0": 2.28, "d": 0.17, "c1": 0.4, "c2": 0.4, "beta": 1.0,
 "fc": 30000.0, "ratio": 0.1713, "status": "OK",   // "N/A": no b/h or d<=0
 "notes": [...], "preliminary": true}
```

Hand-checks: 4-column flat plate — ``Vu = q*B^2/4 = 90`` by statics +
symmetry alone (1e-9); b0/d/vu/min-of-three/ratio longhand; beta = 4
column makes vc2 govern; 2-story stack reports the STEP (90), not the
accumulated axial (180).

## Virtual-work drift diagrams (engine)

```python
OpenSeesEngine(model).run_virtual_work(case_name, direction="X"|"Y") ->
  {"case", "direction", "contributions": {uid: e}, "total", "roof_disp"}
```

Solves the REAL linear static case and a UNIT virtual lateral load at the
TOP story (a copy of the model gains a ``__VW_UNIT__`` pattern/case — the
user's model is NEVER mutated; the unit load flows through the standard
story-force path: the rigid-diaphragm master when the top story has one,
else an equal split over the top-story nodes).  Per frame member::

    e_uid = int_0^L [ N_r*N_v/EA + M3_r*M3_v/EI33 + M2_r*M2_v/EI22
                      + T_r*T_v/GJ ] dx

over the 11 stations (effective = modifier-scaled section properties; NO
shear term — the Euler elasticBeamColumn has no shear flexibility, so a
V^2/GAs term would break the identity).  Quadrature: composite SIMPSON on
the 11 equally-spaced stations — EXACT whenever the station fields are
piecewise-cubic, i.e. nodal/point-at-station loads (linear x linear) and
full-span UDLs on the real case (quadratic x linear = cubic); partial/
trapezoid records with off-station breakpoints, split members and rigid
end offsets integrate approximately (documented).  ``roof_disp`` = the
virtual load's work on the REAL displacements (master ux/uy, or the mean
over the equally-loaded top-story nodes); by the unit-load theorem
``total == roof_disp`` for frame-only models — contributions are NOT
rescaled, the identity IS the correctness check; shells/links/springs
deform without a station integral, so their share appears as
``roof_disp - total`` (documented limitation).  Raises ``ValueError`` on
an unknown case, bad direction, P-Delta case, or a nonlinear model
(tension/compression-only members, gap/hook/isolator links).

Hand-checks: cantilever ``total == roof_disp == F*L^3/3EI`` at rel 1e-12;
two tied cantilevers (I2 = 2*I1) split e_i/total = k_i/(k1+k2) = 1/3, 2/3
(fixed-free k_i = 3EI_i/h^3: e_i = (F*k_i/K)(k_i/K)h^3/3EI_i = F*k_i/K^2);
asymmetric UDL portal holds the identity to machine precision (cubic
products — Simpson exact).

## API additions

| Method | Path                        | Body / Response |
|--------|-----------------------------|-----------------|
| POST   | `/api/design/wall`          | `{combos?: [names] (default: all additive combos), rho_v?, rho_h?, fy?, fc_prime?}` → `{"preliminary": true, "combos", "piers": [WallPierCheck...], "summary": {n, ok, ng, na, max_ratio, governing, boundary_stories, preliminary}}`; 400 on bad params / unknown combo / no pier forces (label walls or set `auto_pier_walls`) |
| POST   | `/api/design/punching`      | `{case?: name (default: first DEAD-classified case), fc_prime?, cover? (m)}` → `{"preliminary": true, "case", "columns": [PunchingCheck...]}`; 400 on an unknown case / a `cover` outside (0, 1) m; `columns: []` (200) when the model has no shell slabs |
| POST   | `/api/results/virtual-work` | `{case: name, direction: "X"\|"Y"}` → `{"contributions": {uid: e}, "total", "roof_disp", "case", "direction"}`; 400 on unknown case / bad direction / P-Delta or nonlinear model |

All three run the engine on the CURRENT model server-side (the design
endpoints call ``run()`` like ``/api/design/steel``); no analysis results
travel in the request.

# v0.19 additions — ASCE 41 hinges, performance point, pattern live loading, auto construction sequence

## Automatic ASCE 41-17 plastic hinges (`skyframe/design/hinges.py`)

```python
MEMBER_HINGE_OPTIONS = ("none", "auto_m3")
# FrameMember gains (round-trips; absent key = "none"; validate() raises):
#   hinges: str = "none"
# PushoverCase.hinges gains the "asce41" option; PushoverCase gains
#   hinge_params: Dict[str, float] = {}     # keys: expected_factor (1.1),
#                                           # rho (0.01), rho_prime (0.0),
#                                           # fy_bar (420000 kPa); > 0,
#                                           # unknown keys raise
```

In ``hinges == "asce41"`` pushover mode, ``My``/``default_My``/``hardening``
are IGNORED; every member with ``FrameMember.hinges == "auto_m3"`` gets a
trilinear M3 hinge at BOTH ends (axial-only members and members with
neither a library steel section nor rectangular drawing dims are skipped
with a ``UserWarning``).  Backbones (all PRELIMINARY-flagged):

* STEEL (section name resolves in the design-property library) —
  Table 9-7.1 beam-flexure ladders::

      compact  (bf/2tf <= 0.30 rt, h/tw <= 2.45 rt):  a=9thy  b=11thy c=0.6
                                                       IO=1thy LS=9 CP=11thy
      slender  (bf/2tf >= 0.38 rt OR h/tw >= 3.76 rt): a=4thy  b=6thy  c=0.2
                                                       IO=.25  LS=3  CP=4thy

  (rt = sqrt(E/Fy); LINEAR interpolation, the WORSE of flange/web governs,
  reported as ``slenderness_t`` in [0, 1]; h = d - 2tf).  ``My = Zx Fye``,
  ``thy = Zx Fye L / (6 E I)`` (Eq. 9-1), ``Fye = expected_factor * Fy``
  (Fy = material.fy if present else 345 MPa A992).  COLUMNS use the same
  beam ladder with NO axial interaction (documented v0.19 idealization).
* CONCRETE (drawing dims b/h > 0) — Table 10-7 conforming/low-shear rows,
  ABSOLUTE plastic rotations:: (rho-rho')/rho_bal <= 0: a=.025 b=.05 c=.2
  IO=.010 LS=.025 CP=.05;  >= 0.5: a=.020 b=.04 c=.2 IO=.005 LS=.020
  CP=.04 (linear between).  ``My = Mn`` from `beam_flexure` with
  ``As = rho * b * (0.9 h)``; fc from material.fc else inverted ACI
  19.2.2.1 E = 4700 sqrt(fc') MPa.

Engine wiring: the v0.5 duplicated-node zeroLength machinery, but the
STRONG-axis spring is a ``Hysteretic`` trilinear envelope (weak axis stays
elastic at k22 — an M3 hinge)::

    k = HINGE_STIFFNESS_FACTOR * 6EI33/L        (n = 10 stiff-hinge series)
    1: ( My,     ty = My/k )
    2: ( My + HINGE_HARDENING_RATIO*(6EI33/L)*a,  ty + a )   # 3% hardening
    3: ( c*My,   ty + b )                        # then OpenSees' last branch

Per-step acceptance states from `hinge_state(rot, backbone, k)`: bands are
PLASTIC rotations shifted by ty — "elastic" | "IO" | "LS" | "CP" |
"collapse" (beyond CP).  ``PushoverResults`` gains ``hinge_detail``
(serialized as ``"hinges"``): ``[{uid, end, My, thy, a, b, c, IO, LS, CP,
kind, rot: [per step], moment: [per step], state: [per step]}]``; empty
outside asce41 mode — legacy modes are BIT-IDENTICAL to v0.18.

Hand-checks: W18x50 compactness longhand -> compact row exact; synthetic
slender/midpoint sections pin the other ladder + interpolation; cantilever
initial stiffness == 1/(L^3/3EI + L^2/k) to 1e-6; first yield bracketed by
V*L crossing My within one displacement step; hardening-branch moments ==
My + 0.03(6EI/L)(rot-ty) at every step on the branch; recorded states ==
hinge_state() re-applied.  NOTE the engine axes convention: a VERTICAL
member's local z is global X, so its M3 hinge yields under a Y push.

## Performance point (`skyframe/design/performance.py`)

Pure functions, ASCE 41-17 §7.4.3 coefficient method:

* ``bilinearize(disp, shear)`` — §7.4.3.2.4: curve used to PEAK shear;
  ``Ke`` = secant at 0.6Vy, ``Vy`` from the equal-area identity (linear in
  Vy at frozen Ke — the Vy^2 terms cancel), fixed-point iterated; EXACT on
  a bilinear input.  Returns {Ki, Ke, Vy, dy, du, Vu, area}.
* ``target_displacement(disp, shear, Ti, W, SDS, SD1, site_class="D",
  num_stories=1)`` — Eq. 7-28 ``delta_t = C0 C1 C2 Sa (Te/2pi)^2 g``:
  ``Te = Ti sqrt(Ki/Ke)``; C0 from Table 7-5 "Other buildings"
  (1/1.2/1.3/1.4/1.5 at 1/2/3/5/10+ stories, interpolated); ``mu = Sa /
  (Vy/W)`` (Cm = 1); ``C1 = 1 + (mu-1)/(a Te^2)`` (a = 130/90/60 by site
  class, Te clamped at 0.2 s, = 1 beyond 1 s); ``C2 = 1 +
  ((mu-1)/Te)^2/800`` (= 1 beyond 0.7 s); Sa from the SDS/SD1 two-branch
  design spectrum (TL branch omitted).  g = 9.81.

Hand-checks: exact-bilinear recovery (Ke/Vy/dy 1e-9), every coefficient
longhand at Ti = 0.5 s (delta_t to 1e-9), C0 midpoints, Sa branches,
site-class a-factors, degenerate-curve errors.

## Pattern (skip) live loading (`skyframe/core/patterning.py`)

* ``beam_spans(model) -> {beam uid: span index}`` — beams grouped by LINE
  (unit direction + perpendicular offset), chained through shared
  endpoints; a geometric gap restarts numbering; isolated beams are span 0.
* ``generate_pattern_live(model, live_pattern)`` — derives
  ``<name>__ODD`` (spans 0, 2, ...) / ``<name>__EVEN`` (spans 1, 3, ...)
  live patterns from the member_loads/member_udls; loads not on beams and
  nodal/story/area/thermal/self-weight loads stay ONLY in the all-spans
  pattern (``UserWarning``).  Idempotent; ValueError on unknown/non-live.
* ``pattern_live_envelope(model, live_pattern, dead_factor=1.2,
  live_factor=1.6)`` — also creates factored CASES ``PLL_ALL/ODD/EVEN``
  (the dead-classified case's patterns * 1.2 + that live pattern * 1.6)
  and the ``PATTERN-LL`` ENVELOPE combo over them (envelope combos take
  scaled CASES, so each case must be a complete gravity sum).  Requires a
  dead-classified case.

Hand-checks: 3-span run indices 0/1/2, gap restart, odd/even load split;
fixed-fixed decoupled spans give |M| = wL^2/24 midspan / wL^2/12 ends on
the loaded-middle case, ~0 on the skip case, and the envelope equals the
per-station max/min over the three cases to machine precision.

## API additions

| Method | Path | Body / Response |
|--------|------|-----------------|
| POST | `/api/results/performance-point` | `{case, SDS, SD1, site_class?, W?}` → bilinearization + Te/Sa/mu/C0/C1/C2/delta_t + `step` (nearest converged step) + `roof_disp_at_step` + `hinge_summary` {elastic/IO/LS/CP/collapse counts}; Ti = dominant modal period in the push direction; W defaults to (story + nodal masses) * g; 400 unknown case / missing SDS/SD1 / degenerate curve |
| POST | `/api/loads/pattern-live` | `{live_pattern?}` (default: the live-classified pattern) → model dict; 400 no live pattern / unknown |
| POST | `/api/case/auto-sequence` | `{name? ("SEQ"), pattern? (dead-classified), include_live?}` → model dict (wraps `add_staged_case` per-story); 400 unknown pattern |

`POST /api/model` round-trips ``FrameMember.hinges`` and
``PushoverCase.hinge_params``; `POST /api/analyze` pushover blocks carry
the ``hinges`` array (asce41 mode).

## v0.19 engine fixes (found by live probing, regression-tested)

* **Pushover base shear = load factor.**  The push is a single unit
  reference force at the control DOF, so the total base shear is EXACTLY
  the pattern-2 load factor (statics).  The old v0.5 recording summed
  ``nodeReaction()`` over supports + support hinge duplicates — the
  Transformation handler leaves that meaningless on nodes whose
  translations were condensed by the hinge equalDOF tie (it read ~0 kN on
  diaphragm buildings; single-support models happened to work).
* **Hinges in a rigid-diaphragm plane.**  A hinge duplicate whose original
  is a diaphragm slave can be tied neither by equalDOF (CHAINS MP
  constraints: dup -> orig -> master — Transformation cannot condense
  chains; singular system) nor by slaving the dup to the diaphragm (a
  node may be the constrained node of only ONE MP constraint; the
  leftover uz equalDOF is dropped and the element-less original's uz
  dangles — singular again).  Fix: slave originals get a STIFF zeroLength
  ELEMENT translation tie, ``k_tie = HINGE_TIE_FACTOR (1e8) x the
  member-end stiffness`` (relative softening ~1e-8, below every pinned
  tolerance); everything else keeps the exact v0.5 equalDOF.  Regression:
  two hinged-base columns under a rigid diaphragm (each a base-spring
  cantilever, in parallel) match ``K = 2/(L^3/3EI + L^2/k)`` to 1e-6.
  Panel-zone ties are UNCHANGED in v0.19 (their diaphragm interaction is
  scheduled with the Wave 23 shell work).
* **Elastic bilinearization fallback.**  A capacity curve that never
  yields (near-linear, or the equal-area yield lands at/beyond the curve
  end) degenerates to ``Ke = Ki, Vy = Vu`` with ``elastic: true`` in the
  response instead of erroring — conservative in the C1/C2 strength
  ratio, and the UI can annotate it.

---

# v0.20 additions — composite beams, slab strip design, walking vibration

No new model fields: all three features are request-driven post-processing
of the existing analysis outputs (design preferences travel in the request,
ETABS-style; the model dict is byte-identical to v0.19).  Units: SI
everywhere — kN, m, kPa (every MPa <-> kPa conversion explicit at the point
of use); the ONE deliberate non-SI convenience is slab ``As`` in mm^2/m.

## Composite beam design (`skyframe/design/composite.py`)

AISC 360-16 Chapter I3 checks for simply-supported interior steel floor
beams acting compositely with a concrete slab on steel deck (headed
studs).  ``check_composite_beams(model, results, combos=None, *,
fc_prime?, t_slab?, hr=0.075, stud_d=0.019, stud_Fu=450_000, rib_spacing=
0.3, shored=False, Fy=345_000, phi_b=0.9, dead_case?, live_case?)`` returns
one check per BEAM member (model order).

* **Applicability screen** — checked beams are horizontal (|dz| <= 1e-6),
  carry a library W-shape (name + 1% area validation, the v0.6 steel
  rule), support a slab (horizontal ``kind == "slab", behavior == "shell"``
  region in the beam plane whose plan polygon contains the beam MIDPOINT —
  the v0.18 punching geometry, boundary inclusive), and have analysis
  demands; everything else reports ``applicable: false`` + ``reason``.
  ``t_slab`` defaults to the supporting slab's shell-section thickness,
  ``fc'`` to the slab concrete's E inverted through ACI 19.2.2.1
  (``fc_from_E``) — both overridable per request.
* **Effective width (I3.1a)** — per side ``min(span/8, s_neighbor/2,
  edge_distance)``: ``s_neighbor`` = perpendicular plan distance to the
  nearest PARALLEL beam at the same elevation with its midpoint in the
  same slab; ``edge_distance`` = ray distance from the beam midpoint to
  the slab polygon's BOUNDING BOX (documented simplification for
  non-rectangular slabs).  ``beff`` = sum of the two sides.
* **Flexure (I3.2a, plastic distribution — all three PNA cases)** — with
  ``tc = t_slab - hr`` (solid slab above the ribs; rib concrete ignored)::

      Cf = min(As*Fy, 0.85*fc'*beff*tc);  C = min(Cf, sumQn)  [partial]
      a  = C/(0.85*fc'*beff)   (<= tc by construction)
      A_c = (As*Fy - C)/(2*Fy)          # steel area above the PNA
        A_c = 0       -> PNA in slab;  <= bf*tf -> flange;  else web
      Mn = C*(t_slab - a/2) + Fy*As*d/2 + 2*Fy*A_c*y_c

  the exact force sum about the top of steel written as a superposition
  (whole shape yielded in tension at -d/2, then a 2*Fy compression
  correction on A_c at its centroid y_c; PNA-in-slab reduces to the
  textbook ``As*Fy*(d/2 + t_slab - a/2)``); ``phi_b = 0.90``.  Web taken
  at constant tw below the flange (the k-region fillet area deepens the
  PNA slightly — forces exact, documented).
* **Studs (I8.2a)** — ``Qn = min(0.5*Asa*sqrt(fc'*Ec), Rg*Rp*Asa*Fu)``
  with Rg = 1.0, Rp = 0.75 (one stud per rib, welded through deck),
  ``Ec = 4700*sqrt(fc'[MPa])`` MPa; in kPa*m^2 the concrete branch is
  directly kN.  One stud per rib: ``n_studs = floor((span/2)/
  rib_spacing)`` per shear span, ``sumQn = n_studs*Qn``;
  ``ratio_composite = sumQn/Cf`` (< 0.25 noted per the I3.2d.1 user note).
* **Demand** — ``Mu`` = max |M3| over the member stations ENVELOPED over
  the combos (default: every ADDITIVE combo — the v0.9 envelope pattern;
  static case names accepted; ValueError when the model has none and no
  names are passed); ``ratio = Mu/phiMn_partial``.
* **Pre-composite (unshored)** — ``precomp_Mu = 1.4 * M_dead`` (first
  DEAD-classified case) vs the F2 capacity at ``Lb = 0`` (the deck braces
  the compression flange during casting, documented) = ``phi_b*Fy*Zx``;
  ``shored: true`` (or no dead case) skips with a note (``null``).
* **Deflection** — ``I_tr`` = elastic transformed inertia (n = Es/Ec,
  solid slab tc over beff, parallel-axis about the elastic NA);
  ``I_equiv = Is + sqrt(sumQn/Cf)*(I_tr - Is)`` (AISC Commentary
  Eq. C-I3-4, ratio clamped to [0, 1]); ``defl_LL`` = the v0.16 EXACT
  bare-steel chord-relative max |dy| of the LIVE-classified case scaled
  by ``Is/I_equiv`` (exact for prismatic beams — the elastic line is
  proportional to 1/I; documented approximation: composite I applied
  over the full span).  Checked against ``span/model.deflection_limit``.

`CompositeBeamCheck.to_dict()`:

```jsonc
{"uid": "B1", "story": "S1", "section": "W18x50",
 "applicable": true, "reason": "",
 "span": 6.0, "beff": 1.1, "tc": 0.075, "fc": 30000.0,
 "C_full": 2103.75, "PNA_case": "web",       // "slab"|"flange"|"web"
 "phiMn_full": 993.4, "n_studs": 10, "Qn": 95.69, "sumQn": 956.9,
 "ratio_composite": 0.4549, "phiMn_partial": 757.6,
 "Mu": 90.0, "ratio": 0.1188,
 "precomp_Mu": 63.0, "precomp_ratio": 0.1226,   // null when shored
 "I_s": 3.32985e-4, "I_tr": 1.0847e-3, "I_equiv": 7.2945e-4,
 "defl_LL": 5.783e-4, "defl_limit": 0.016667, "defl_limit_ok": true,
 "governing_combo": "U", "status": "OK",     // "OK"|"NG"|"N/A"
 "notes": [...], "preliminary": true}
```

Hand-checks (tests/test_wave21.py): PNA-in-slab longhand on W18x50
(As*Fy = 3271.929 kN, a = 0.0570226 m, Mn = 1276.335 kN*m, 1e-12);
contrived fc'/beff pin the flange (Cc = 1530, x = 0.013252 <= tf) and web
(Cc = 510, x = 0.152521) cases against the typed force sums; partial
sumQn = 0.5*As*Fy makes the studs govern (A_c = As/4 -> flange);
Qn concrete branch at fc' = 20 MPa (91.915 kN) vs steel at 30 MPa
(95.691 kN); beff = 1.1/1.0/0.9/1.5 across neighbour-, edge- and
span/8-governed sides; C-I3-4 endpoints (sumQn = Cf -> I_tr, -> 0 -> Is,
quarter -> midpoint via sqrt); a statics-exact engine model (one-quad
slab that never splits the beams) pins Mu = wL^2/8 = 90, the pre-composite
ratio, and defl_LL = 5wL^4/(384*E*I_equiv) end to end.

## Slab strip flexural design (`skyframe/design/slab.py`)

ETABS-style column/middle strip design of every horizontal MESHED SHELL
slab from the v0.4 per-quad gauss-averaged ``shell_forces``.
``check_slab_strips(model, results, case=None, *, fc_prime?, fy=420_000,
bar_d=0.016, cover=0.03, phi_tc=0.9)``; case defaults to the first
DEAD-classified case (unknown -> KeyError); NO shell slabs -> ``[]``.

* **Strips (ACI §8.4.1)** — per direction, support lines = distinct plan
  coordinates of the columns supporting the slab (v0.18 punching
  detection); column strip = ``min(L1, L2)/4`` each side of each line
  (L1 = largest perpendicular-line span, L2 = distance to the adjacent
  parallel line; an edge line uses 2x its edge distance as L2 —
  documented); the gaps are middle strips.  Irregular regions use their
  plan BOUNDING BOX; no columns -> one full-width middle strip.
* **Sections** — both ends + midspan of every span between perpendicular
  support lines (deduped stations; bbox extent when < 2 lines).
* **Strip moment** — ``Mu = mean(M_dir over the strip's quad row nearest
  the station) * width`` — the mid-quad rectangle rule for ``int M dx``
  across the strip (exact for a transversely constant field, O(h^2)
  otherwise, documented).  Mxx reinforces spans along the element LOCAL
  x (the mesher's corner-0 -> corner-1 direction); the module maps it to
  the global direction that edge is most aligned with (note emitted when
  swapped).
* **Rebar** — closed-form Whitney inversion, per metre (b = 1; the
  solution is exactly width-linear so per-metre == whole-strip)::

      T = 0.85*fc'*d - sqrt((0.85*fc'*d)^2 - 2*0.85*fc'*|mu|/phi)
      As_req = T/fy      (discriminant < 0 -> "NG": section too thin)

  phi made consistent with the ACI 21.2 strain rule by fixed-point on
  ``beam_flexure`` (slabs are tension-controlled, one pass); at
  convergence ``beam_flexure(...)["phiMn"] == Mu`` EXACTLY.
  ``d = t - cover`` (0.03 m default, the v0.18 convention);
  ``As_min = 0.0018*b*h`` (ACI 24.4.3.2 temperature minimum, fy 420);
  ``spacing = Ab/max(As_req, As_min)`` capped at ``min(3h, 0.45)``
  (ACI 8.7.2.2).  ``As`` in mm^2/m (*1e6 from m^2/m, explicit).

Per-region shape:

```jsonc
{"uid": "S1", "story": "S1", "case": "G", "t": 0.2, "d": 0.17,
 "fc": 30000.0,
 "directions": {"x": [                         // strips RUNNING along x
    {"strip": "column", "line": 0.0,           // "middle" has line: null
     "band": [0.0, 1.0], "width": 1.0,
     "sections": [{"x": 0.0, "Mu": 12.0,       // strip total, kN*m
                   "mu": 12.0,                 // per metre, kN*m/m
                   "As_req": 189.3,            // mm^2/m (null when NG)
                   "As_min": 360.0,            // mm^2/m
                   "spacing": 0.45,            // m (3h/450 capped)
                   "min_governs": true, "status": "OK"}, ...]}, ...],
  "y": [...]},
 "notes": [...], "preliminary": true}
```

Hand-checks: min(L1,L2)/4 layout typed for the corner-supported square
(bands (0,1)/(1,3)/(3,4)) and a 3-line run; UNIFORM synthetic Mxx/Myy over
the real 2x2 mesh make every section's Mu equal moment x width to 1e-12
(the area-weighted hand sum); the inversion round-trips ``beam_flexure``
to 1e-9 at Mu = 100 and 120; As_min (360 mm^2/m) governs a low-moment
section with the 0.45 m spacing cap; Mu past the tension-controlled
ceiling 0.9*R*d^2/2 reports NG.

## Walking vibration (`skyframe/design/vibration.py`)

AISC Design Guide 11 Chapter 4 walking screen per slab-supporting beam.
``check_vibration(model, results, case=None, live_case=None, *,
live_factor=0.11, beta=0.03, ap_limit=0.005, P0=0.29, g=9.81)``; case
defaults to the first DEAD-classified case (KeyError on unknown),
live_case to the first LIVE-classified one (dead-only with a note when
absent).

* ``delta`` = |chord-relative midspan dy(dead) + live_factor * dy(live)|
  from the v0.16 exact per-case deflection stations — the DG11 sustained
  load ``Dead + live_factor*Live`` (0.11 ~ the DG11 office assumption,
  documented); bare-steel I is conservative (documented).
* ``fn = 0.18*sqrt(g/delta)`` (DG11 Eq. 3-3).
* ``W_eff = W_beam * beff_v/trib`` — ``W_beam = |V2(0) - V2(L)|`` under
  the sustained combo (the exact station-shear drop = the total carried
  load), ``beff_v`` = the composite effective width, ``trib`` = the
  summed per-side available widths; this implements DG11's
  ``w * B * L`` with w recovered from the beam's own carried load
  (documented simplification of the modal panel weight).
* ``ap/g = P0*exp(-0.35*fn)/(beta*W_eff)`` (DG11 Eq. 4-1, P0 = 0.29 kN);
  ``status = OK`` iff ``ap/g <= ap_limit`` (0.005 offices, Table 4-1);
  fn < 3 Hz noted (outside the walking fit).

`VibrationCheck.to_dict()`:

```jsonc
{"uid": "B1", "story": "S1", "applicable": true, "reason": "",
 "fn": 10.904, "delta_mid": 2.6733e-3, "W_eff": 63.3,
 "ap_over_g": 3.3607e-3, "limit": 0.005, "status": "OK",
 "notes": [...], "preliminary": true}
```

Hand-checks: the pure chain at delta = 0.005 typed to 1e-12
(fn = 0.18*sqrt(9.81/0.005) = 7.97301 Hz, ap/g = 0.29*exp(-0.35*fn)/
(0.03*100)); the engine beam pins delta = 5*10.55*6^4/(384*2e8*I) =
2.673261e-3 m, W_eff = 10.55*6 = 63.3 kN, fn = 10.9040 Hz, ap/g =
3.3607e-3 (OK at 0.005, flips NG at 0.003); beta doubling exactly halves
ap/g.

## API additions

| Method | Path                      | Body / Response |
|--------|---------------------------|-----------------|
| POST   | `/api/design/composite`   | `{combos?: [names] (default: all additive combos), fc_prime?, t_slab?, hr?, stud_d?, stud_Fu?, rib_spacing?, shored?: bool}` → `{"preliminary": true, "combos", "params": {fc_prime, t_slab, hr, stud_d, stud_Fu, rib_spacing, shored} (RESOLVED: request values over the defaults; fc_prime/t_slab null = derived per slab), "beams": [CompositeBeamCheck...], "summary": {n, ok, ng, na, max_ratio, governing, preliminary}}`; `beams: []` (200) when the model has no beam members; 400 on bad params / unknown combo names / no additive combos |
| POST   | `/api/design/slab`        | `{case?: name (default: first DEAD-classified case), fc_prime?, bar_d?, cover? (m)}` → `{"preliminary": true, "case", "regions": [...]}`; `regions: []` (200) when the model has no shell slabs; 400 on an unknown case / bad params |
| POST   | `/api/results/vibration`  | `{case?: dead case, live_case?, live_factor?, beta?, ap_limit?}` → `{"preliminary": true, "case", "live_case", "beams": [VibrationCheck...]}`; 400 on unknown case names / bad params |

All three run the engine on the CURRENT model server-side (like the other
design endpoints); no analysis results travel in the request.

---

# v0.21 additions — Section Designer, fiber PMM hinges, device library II

Units: SI everywhere — m, kN, kPa; section coordinates in the local
``(y, z)`` plane with **z the depth direction** (``I33 = int z^2 dA``,
``I22 = int y^2 dA`` — a ``b x h`` rectangle drawn y in [-b/2, b/2], z in
[-h/2, h/2] reproduces ``b*h^3/12`` exactly).

## Section Designer (`skyframe/core/sections_designer.py`)

```python
# BuildingModel gains (round-trips; validate() checks every entry):
#   designer_sections: Dict[str, DesignerSection] = {}
# add_designer_section(ds)     — validates, stores, and creates/updates a
#   FrameSection of the SAME NAME in model.sections (preserving existing
#   mod_* factors) so designer sections feed the analysis pipeline
#   unchanged; remove_designer_section(name) deletes both (KeyError on an
#   unknown name, ValueError while a member still uses it).
DESIGNER_BASES = ("concrete", "steel")
```

`DesignerSection.to_dict()` (exact round trip):

```jsonc
{"name": "D1", "material": "conc",      // BASE material (reference E)
 "base": "concrete",                     // "concrete" | "steel" (PMM path)
 "polygons": [{"vertices": [[y, z], ...],   // >= 3, simple (validated by
               "material": "conc",          //   pairwise edge-crossing +
               "hole": false}, ...],        //   zero-area tests)
 "rebar": [{"y": 0.0, "z": 0.24, "area": 1e-3, "material": "bar"}, ...]}
```

**Properties (EXACT shoelace closed forms — no quadrature).**  Signed
polygon integrals ``A = 1/2 sum cr_i``, ``S = 1/6 sum (p_i + p_{i+1})
cr_i``, ``I = 1/12 sum (p_i^2 + p_i p_{i+1} + p_{i+1}^2) cr_i`` with
``cr_i = y_i z_{i+1} - y_{i+1} z_i``; every polygon normalized CCW, holes
enter with sign -1.  ``I33``/``I22`` about the GROSS centroid.  Torsion is
the St. Venant polygon APPROXIMATION ``J ~ A^4/(40 Ip)``, ``Ip = I33 +
I22`` (documented approximate; exact for a circle).  Rebar is SEPARATE
from the gross area (point areas on top of the base material, displaced
material not deducted); the derived FrameSection carries the TRANSFORMED
values with ``n = E_bar/E_base``:  ``A_tr = A + sum (n-1) As``, centroid
shift included, ``I_tr = I_gross(transferred to the transformed centroid)
+ sum (n-1) As d^2`` (bars have no own inertia), ``J`` gross only,
``b``/``h`` = solid-polygon bounding box.  ``section_properties()``
returns ``{A, Cy, Cz, I33, I22, Ip, J, b, h, A_tr, Cy_tr, Cz_tr, I33_tr,
I22_tr, As_total, n_bars}``.

**Polygon clipping (`clip_polygon(verts, z0, keep="above"|"below")`).**
EXACT single-half-plane Sutherland-Hodgman against the horizontal line
``z = z0`` (crossings interpolated exactly, cut chord in the outline,
``[]`` when nothing remains) — the kernel of the PMM block integrals and
the engine's fiber strips.

**PMM surface (`pmm_surface(ds, materials, axis="33"|"22")`).**
Returns ``{"base", "axis", "points": [[phiMn, phiPn], ...], "detail":
[{label, c, Pn, Mn, eps_t, phi, phiPn, phiMn}, ...]}`` ordered pure
compression -> pure tension; axis "22" runs the identical machinery with
y as the depth coordinate.

* concrete — ACI strain compatibility, IDENTICAL assumptions to
  ``design.concrete.column_interaction`` (verified: a rectangular
  designer section reproduces its pure-compression / eps_t = 0 /
  balanced / pure-bending / pure-tension points to ~1e-13): Whitney
  block ``a = beta1*c`` integrated EXACTLY over the clipped polygons
  (0.85 fc'), bar strains ``0.003 (c - depth)/c`` clamped at +-fy
  (displaced concrete ignored), ``phi = phi_from_strain`` with the
  0.80*0.65 tied cap on the closed-form pure-compression point
  ``Pn0 = 0.85 fc' (Ag - Ast) + sum fy As``; ``fc'`` = material ``fc``
  attr or ``fc_from_E``; bar ``fy`` attr or 420 MPa; moments about the
  GROSS centroid; a c-sweep (16 + the labeled exact points, pure bending
  bisected) fills the diagram.  Requires >= 1 rebar point (ValueError).
* steel — NOMINAL full-plastic distribution (phi = 1.0): section clipped
  at a PNA sweep, +-Fy on the two sides (Fy = material ``fy`` attr or
  345 MPa; rebar at its own +-fy); the P = 0 point is bisected so a
  rectangle gives ``M = Fy b h^2/4 = Fy Zp`` EXACTLY.

## Fiber PMM hinges (engine + `design/hinges.py`)

```python
MEMBER_HINGE_OPTIONS = ("none", "auto_m3", "fiber_pmm")   # round-trips
```

In an ``asce41`` pushover a ``fiber_pmm`` member becomes a
``forceBeamColumn`` with ``beamIntegration('HingeRadau', fiberSec, lp,
fiberSec, lp, elasticInterior)``, ``lp = 0.5 h`` (documented hinge
length).  The standard two-point Gauss-Radau hinge rule puts the FIBER
section at x = 0 / x = L with weight ``lp`` each; the elastic interior
(the member's modifier-scaled A/I/J at the material E) carries points
``8lp/3`` & ``L - 8lp/3`` (weight ``3lp``) plus two-point Gauss over
``[4lp, L - 4lp]`` — so the elastic tip flexibility is the 6-point sum
``f = sum w_i (x_i/L - 1)^2 / EI(x_i)`` the tests pin.  Fiber source, in
priority order (OpenSees section coords: local y = DEPTH, so a designer
(y, z) maps to fibers at (z - Cz, y - Cy) about the gross centroid):

1. member's section name matches a DESIGNER SECTION — exact per-material
   grid cells (FIBER_HINGE_STRIPS = 20 depth bands x FIBER_HINGE_LATERAL
   = 4 width cells; cell area/centroid from the exact polygon clip;
   holes subtract PER MATERIAL GROUP — a hole entry should carry the
   material of the solid it pierces) + one fiber per rebar point;
2. LIBRARY W SHAPE — Steel01(Fye = expected_factor*Fy, E, b = 0.01)
   patches: 2 depth x 4 width cells per flange + 20 x 2 web cells
   (design-table dims);
3. RECTANGULAR CONCRETE (drawing b/h) — Concrete01 over 20 x 4 cells
   + 8 perimeter bars (3 per face row at depth +-0.4h, 2 side bars at
   mid-depth; each ``rho*b*(0.9h)/3`` with rho/fy_bar from hinge_params,
   defaults 0.01/420 MPa — a face row is exactly the Table 10-7
   ``As = rho b d_eff``).

The WIDTH split exists because a force-based element inverts the section
stiffness: a single fiber column across the width leaves the weak axis
singular and blows up ``ForceBeamColumn3d::update`` (observed).  Width
splitting never moves a band's depth centroid, so the strong-axis
closed forms above are unaffected (the weak-axis fiber inertia runs
~(1 - 1/4^2) soft — the hinge is an M3-dominant idealization).

Concrete01 is UNCONFINED: ``fpc = fc`` (material ``fc`` attr or
``fc_from_E``), ``eps0 = 2 fpc / E`` — so the INITIAL FIBER TANGENT
(= 2 fpc/eps0) EQUALS the material E — residual ``0.2 fpc`` at 0.006.
Midpoint strips make a rectangle's discretized inertia EXACTLY
``b h^3/12 (1 - 1/n^2)`` (the pinned closed form).  Steel01 hardening
1% everywhere.

Recording: per converged step each end's BASIC rotation/moment from
``eleResponse('basicDeformation'/'basicForce')`` (3D basic order
``[N, Mz_i, Mz_j, My_i, My_j, T]`` — VERIFIED on openseespy 3.7.1: the
end-i basic Mz of a cantilever equals V*L exactly by statics).  States
use the SAME Table 9-7.1/10-7 acceptance criteria (``auto_backbone`` —
the backbone supplies ONLY the criteria; the resisting moment comes from
the fibers) applied to the CURVATURE-BASED plastic hinge rotation

    theta_pl = max(0, |kappa_z| - kappa_y) * lp,   kappa_y = My/(E I33)

(``kappa_z`` from the end integration point's section 'deformation'
response — point 1 = end i, 6 = end j; ``kappa_y`` the GROSS-section
yield curvature) via ``hinge_state(theta_pl, bb, k = inf)`` — the yield
shift removed so the Table bands read plastic rotations directly.  A
moment-free end therefore stays "elastic" no matter the chord rotation,
and theta_pl clamps at EXACTLY 0 below kappa_y; because kappa_y is
normalized on the gross EI while the cracked fiber section curves more,
the first transition lands BELOW My (measured ~0.49 My on the 0.4 x 0.4
test column — documented conservatism).  ``hinge_detail`` entries carry
the v0.19 keys + ``"fiber": true``, ``lp``, ``kappa_y`` and the per-step
``rot_plastic`` list.  Members that are split by shell edges,
rigid-offset, or released are left elastic with a ``UserWarning``;
``auto_m3`` members and every legacy mode are BIT-IDENTICAL to v0.19
(fiber code runs only for fiber_pmm members in asce41 pushovers).

**v0.21 engine/design fix** — ``auto_backbone``'s concrete fc'
E-inversion missed the kPa -> MPa conversion ((E/4700)^2*1000 instead of
``fc_from_E`` = ((E/1000)/4700)^2*1000, a 1e6 inflation); it now calls
``fc_from_E``.  Only reachable for materials with no explicit ``fc``
attribute (no shipped test exercised it).

## Device library II (`LINK_TYPES` + engine)

```python
LINK_TYPES = ("elastic", "damper", "gap", "hook", "isolator",
              "fp_isolator", "triple_fp", "multilinear")   # v0.21: last 3
FP_DEFAULT_KINIT = 1e5; TFP_DEFAULT_UY = 1e-3
TFP_DEFAULT_MINFV = 0.1; TFP_DEFAULT_TOL = 1e-5
```

Validation mirrors v0.15 exactly (missing required key / unknown key /
out-of-range value -> ValueError, `POST /api/model` 400); all three
require a VERTICAL axis (or zero length), the v0.15 isolator rule.  The
axis-vertical/Newton-routing rules extend unchanged:
``NONLINEAR_STATIC_LINK_TYPES`` now includes all three (static Newton +
TH Newton like gap/hook/isolator); link-only nodes are grounded anchors.

* **fp_isolator** `{R (m, > 0), mu (> 0), k_init (kN/m, default 1e5),
  kv (default 1e7), P0? (kN, > 0)}` — single friction pendulum:
  ``frictionModel('Coulomb', mu)`` + ``element('singleFPBearing', tag,
  iLower, jUpper, frnTag, R, k_init, '-P', Elastic(kv), '-T'/'-My'/'-Mz',
  Elastic(10.0))`` (node i = the LOWER node; zero length adds
  ``'-orient', 0,0,1, 1,0,0``).  Post-slip lateral law ``F = mu*N +
  N*d/R`` with N the axial load FROM THE ANALYSIS (verified 0.03%/0.5%
  at d = 0.001/0.1 — the small excess is the element's exact
  large-displacement kinematics).  ``P0`` is INFORMATIONAL only (echoed
  for UI/hand checks; the element never reads it).
* **triple_fp** `{R1, R2, R3 (effective radii, m), mu1, mu2, mu3,
  d1, d2, d3 (capacities, m), W (kN) — all > 0; uy (default 1e-3),
  kv (1e7), kvt (= kv), minFv (0.1), tol (1e-5)}` —
  ``element('TripleFrictionPendulum', tag, i, j, frn1, frn2, frn3,
  Elastic(kv), rotZ, rotX, rotY = Elastic(10.0), R1, R2, R3, d1, d2, d3,
  W, uy, kvt, minFv, tol)`` (exact documented arg order; R1 = inner
  effective radius).  Fully-sliding tangent ``W/(R2 + R3)`` — verified
  EXACT (400.00 kN/m for W = 1000, R2 = R3 = 1.25).  The uy = 1e-4 +
  tol = 1e-10 combination stalls the element internally (probed) — hence
  the defaults.
* **multilinear** `{points: [[d1, F1], [d2, F2], ...] (>= 2 pairs,
  d strictly increasing, d1 > 0), kv (default 1e7)}` —
  ``uniaxialMaterial('MultiLinear', d1, F1, ...)`` on BOTH horizontal
  shear directions of the isolator-layout twoNodeLink (finite vertical:
  dirs 1 = Elastic(kv) axial, 2/3 = MultiLinear; zero length: zeroLength
  with global 1/2 = MultiLinear, 3 = kv).  Backbone force EXACT at every
  [d, F] point and piecewise-linear between (verified: 50/85/120/150 kN
  at 0.01/0.03/0.05/0.20 m); flat beyond the last point.  ``points`` is
  the ONE non-scalar param value (serialised as the nested list).

## API

| Method | Path | Body / Response |
|--------|------|-----------------|
| POST | `/api/sections/designer` | `{action: "upsert", section: {name, material, base?, polygons, rebar}}` → `{"model": <model dict>, "properties": <section_properties()>}`; `{action: "delete", name}` (or `section.name`) → `{"model": ...}`; 400 on bad action/polygons/materials, unknown name, or a section still used by a member |
| POST | `/api/sections/designer/pmm` | `{name, axis?: "33"\|"22"}` → `{name, axis, base, points: [[phiMn, phiPn], ...], detail: [...], properties: {...}}` (non-finite closed-form sentinels null); 400 unknown name / bad axis / concrete without rebar |

`POST /api/model` round-trips ``designer_sections``, ``hinges ==
"fiber_pmm"`` and the three new link types (400 via ``validate()``);
asce41 pushover ``hinges`` entries gain ``"fiber": true`` for fiber
hinges.

---

# v0.22 additions — edge constraints, layered shells, line/area springs, semi-rigid distribution

Units: SI everywhere — m, kN, kPa.

## Auto edge constraints (`BuildingModel.edge_constraints`, the "zipper")

```python
edge_constraints: bool = False      # round-trips; validate(): must be bool
EDGE_TIE_FACTOR = 1.0e6             # engine tie-beam scale
```

`False` (default) keeps every pre-v0.22 result bit-identical.  `True`:
at BUILD time the engine finds every **hanging node** — a mesh point
(shell node OR frame node) lying strictly inside another shell's element
edge within the 1e-6 pool tolerance without being one of that edge's end
nodes (`skyframe.core.mesh.edge_tie_chains(mesh)`: all quad edges,
deduplicated by unordered end-node pair, each keeping the first
contributing region's uid; returns `(region_uid, end_a, end_b,
[hangs sorted by edge position])`, deterministic order).  Structured
meshing guarantees a region never hangs on its own edges, so every chain
is a genuine mesh-mismatched interface (shell/shell T-junction or a
frame member end landing mid-edge).

**Tie realization** — a stiff `elasticBeamColumn` CHAIN
`end_a -> hangs -> end_b` laid along the edge, sized from the edge
region's shell section (`t` = `total_thickness`, `E_s = E*mod`,
`G_s = G*mod`) per unit width times the FULL edge length `L_e`:

    A_tie = EDGE_TIE_FACTOR * t * L_e            # membrane   t*E per m
    I_tie = EDGE_TIE_FACTOR * t^3/12 * L_e       # bending  t^3*E/12 per m
    J_tie =                   t^3/12 * L_e       # soft placeholder torsion

The chain's OUTER ends are moment-RELEASED (`-releasez/-releasey`): a
pinned rigid bar holds the interior nodes on the rotated chord and
splits the interface force `(1-t, t)` — the interpolation-tie kinematics
— without coupling end-node rotations.  (Unpinned chains merge across
collinear edges into one spuriously rigid line: measured -43% tip
deflection on the validation wall vs -4.7% pinned.)  Elements only — no
MP constraints, so no Transformation-handler constraint chains (the
v0.19 lesson).  Documented over-stiffness: the chain also suppresses the
edge's own axial stretch mode (pure interpolation would allow it) —
relative parasitic stiffening ~1/EDGE_TIE_FACTOR per tied dof plus that
one per-edge stretch mode; equilibrium is EXACT regardless (internal
elements).  `_Assembly.edge_ties` records
`{"region", "chain": [tags], "eles": [tags]}` per zipped edge.

Hand-pins (tests): two-region 4 x 3 x 0.2 cantilever wall (left 1 x 2 @
1.5 m, right 2 x 3 @ 1.0 m) under 100 kN tip shear — exactly 3 chains
(left edges (0,1.5)/(1.5,3) x=2 catch right nodes z=1/z=2; right edge
(1,2) catches left z=1.5); tip 1.2784e-4 monolithic / 1.5263e-4 untied
(+19.4%) / 1.2187e-4 tied (-4.7%); reactions balance 1e-9 in every
variant; a beam end at (2,0,1.5) inside edge (2,0,1)-(2,0,2) is a
SINGULAR mechanism untied and carries 10 kN into the wall tied.

## Nonlinear layered shell walls (`ShellSection.layered`)

```jsonc
{"layers": [{"t": m, "material": name, "kind": "concrete"|"steel",
             "angle": 0|90}, ...]}      // angle: steel only, default 0
```

Validated (`validate()` / `add_shell_section`): dict with the single key
`layers`; non-empty list; per layer `t` finite > 0, known material,
`kind` in `SHELL_LAYER_KINDS = ("concrete", "steel")`, `angle` only on
steel and only 0|90.  Round-trips exactly.
`ShellSection.total_thickness` = summed layer `t` when layered, else
`thickness`.

**Linear analyses** keep `ElasticMembranePlateSection(E*mod, nu,
total_thickness)` — a layered model is displacement-identical to the
elastic model at the summed thickness (`thickness` is ignored while
`layered` is set); self-weight also uses `total_thickness`.  Bit-
identical legacy path when `layered is None`.

**Nonlinear builds** (exactly when the engine builds with a
`hinge_case` — pushovers and nonlinear THs) emit `section('LayeredShell')`
with every model layer split into `SHELL_SUBLAYERS = 4` equal sublayers
(LayeredShell requires >= 3 layers; the split refines bending stress
recovery, membrane response unchanged):

* **concrete** — `PlaneStressUserMaterial` is NOT compiled into the
  shipped openseespy binary ("PSUMAT ... SOURCE CODE RESTRICTED",
  probed), so the DOCUMENTED REPLACEMENT is `ASDConcrete3D(E_eff =
  E*mod, nu)` statically condensed via `PlaneStress` and wrapped in
  `PlateFromPlaneStress` with out-of-plane shear modulus
  `E_eff/(2(1+nu))`.  Exact piecewise-linear laws from the material
  (`fc'` = material `fc` attr or `fc_from_E(E)`; `ft = LAYERED_FT_RATIO
  (0.1) * fc'`; `et = ft/E_eff`, `ec = fc'/E_eff`):

      tension     (-Te/-Ts/-Td): (0,0,0) -> (et, ft, 0)
                    -> (20*et, 0.05*ft, 0.95)
      compression (-Ce/-Cs/-Cd): (0,0,0) -> (ec, fc', 0)
                    -> (10*ec, 1.05*fc', 0)

  The first segment slope is EXACTLY `E_eff`, so the pre-crack layered
  stiffness EQUALS the elastic shell's (pinned: first pushover step
  secant = elastic 254165.46 kN/m to 1e-4 on the 2 x 3 x 0.2 wall,
  fc' = 30 MPa); the capacity curve then peaks at 258.90 kN (0.68x the
  elastic extrapolation at that displacement) and descends below half
  the peak — genuine cracking.
* **steel** — `PlateRebar` wrapping `Steel01(fy = material fy attr or
  LAYERED_FY_DEFAULT = 420 MPa, E_eff, b = FIBER_STEEL_HARDENING)` at
  the layer `angle` in degrees from the element local x axis (0 =
  along corner0->corner1, 90 = perpendicular; verified: a 90-degree
  layer pulled along local x is singular, along local y exact).  A
  steel-only panel in uniform stretch reproduces `u = 2P*W/(E t H)`
  and the same-thickness elastic panel (nu = 0) to 1e-3.

nDMaterial tags live from `_ND_MAT_TAG0 = 200000` (own OpenSees
namespace; kept clear of everything anyway).

## Line springs + area springs

```python
# BuildingModel.line_springs: List[LineSpring] (round-trips; validated)
LineSpring(p1, p2, kz=0.0, kx=0.0, ky=0.0, compression_only=False)
#   kz/kx/ky in kN/m PER METER of line, GLOBAL axes; finite, >= 0, at
#   least one > 0; p1 != p2; compression_only requires kz > 0.
# ShellRegion.area_spring: Optional[dict] (round-trips; validated)
{"kz": kN/m per m^2 (> 0), "compression_only": bool = False}
#   shell behavior only (membrane slabs have no mesh nodes).
```

Engine (v0.11 Winkler pattern): a line spring discretizes over the
EXISTING FE nodes on the p1->p2 segment (error if none) — node i owns
the tributary `[mid(t_{i-1},t_i), mid(t_i,t_{i+1})]` with the first/last
intervals extended to p1/p2, so `sum(trib) == |p2-p1|` exactly; an area
spring lumps `kz x nodal tributary area` at every mesh node of the
region.  Linear springs reuse the v0.8 grounded zeroLength machinery
(reaction `-k*disp`, node counts as a support, sprung dof freed from
base fixity).  `compression_only` (vertical only; kx/ky stay linear)
uses the v0.12 `Elastic(kz*AXIAL_ONLY_RATIO, 0, kz)` material —
settlement at full kz, uplift at the 1e-6 residual — recorded in
`_Assembly.ent_springs` and reported with the EXACT same law
(`-kz*uz` settling, `-1e-6*kz*uz` uplifting).  Any compression-only
spring routes static cases to Newton
(`_compression_only_springs_present`), like axial-only members.

Closed forms (tests): rigid plate (4 x 4, kz = 1000, E = 25e11, t = 2)
under central 100 kN settles `P/(kz*A)` = 6.25 mm uniformly (< 1e-6;
the bending residual scales 1/(E t^3) — pushing E higher instead
ill-conditions the penalty and WORSENS it, measured); nodal reactions =
kz x tributary x u exactly (corner/edge/interior 1.5625/3.125/6.25 kN =
1:2:4); wall base line spring reacts the [0.5, 1, 1, 1, 0.5] m
tributaries exactly ([10, 20, 20, 20, 10] kN under 80 kN); eccentric
compression-only cases: uplifted nodes read exactly the residual path
(pinned 8.0e-6 kN), the contact zone carries rigid-body statics
([8, 8] kN), equilibrium closes to 1e-9.  NOTE (documented): exact
closed-form checks on compression-only beds need REALISTIC stiffness —
Newton's displacement test (1e-8) stops early on quasi-rigid (E ~ 1e9+)
models with kN-scale force residuals at the free dofs.

## Semi-rigid diaphragm auto distribution (story forces)

Pre-v0.22 a story force on a "none"-diaphragm story was split EQUALLY
over the story nodes.  v0.22: when the story HAS meshed shell-slab
nodes in its plane (`kind == "slab"`, `behavior == "shell"`;
`_story_slab_nodes`), the force spreads over THOSE nodes weighted by
tributary mass (`mass_map` ux entries; plain node count when the story
carries no mass), and the accidental-torsion moment (v0.8:
`Mz_x = fx*ecc*Ly`, `Mz_y = fy*ecc*Lx`) is realized as the linear
ANTISYMMETRIC force-couple field about the weighted node centroid
(x̄, ȳ):

    fx_i = fx*w_i/W - mu*w_i*(y_i - ȳ),   mu = Mz_x / sum w_i (y_i - ȳ)^2
    fy_i = fy*w_i/W + nu*w_i*(x_i - x̄),   nu = Mz_y / sum w_i (x_i - x̄)^2

— zero net force added, exact moment; a zero-spread direction warns and
skips its couple.  Stories WITHOUT slab mesh nodes keep the exact
pre-v0.22 equal split (bit-identical legacy).  Reported story shears
(`_story_shears`) are unchanged (same totals).

Pins: 4-column + 6 x 6 slab building, 100 kN EQX @ 5% ecc — semi-rigid
base FX = -100 and base MZ = 270 kN*m match the rigid-diaphragm run to
1e-9 (270 = -(-100*3 + 30)); symmetric building: mirrored ux equal, uy
antisymmetric, mirror-line uy = 0; semi-rigid drift 4.7959e-4 >= rigid
4.7417e-4 (flexible diaphragm bounds from above, +1.14%); ALL mass on
one slab node == a nodal load there (1e-15); 6 x 0.5 strip: base torque
-(-0.25*100 + 100*0.05*0.5) = 22.5 kN*m exact.

## API

No new endpoints.  `POST /api/model` round-trips `edge_constraints`,
`ShellSection.layered`, `line_springs`, and `ShellRegion.area_spring`
(400 via `validate()`).

# v0.23 additions — EC3/EC2 checks, NBCC lateral, AISC 341 SMF, Cp shell wind, camber

Units everywhere: kN, m, kPa, tonne, s.  Every design result still
carries `"preliminary": true`.

## Eurocode 3 steel checks (`skyframe.design.steel_ec3`)

`check_members_ec3(model, results, case_or_combo, *, fy=355000, kx=1,
ky=1, E=2.1e8)` — EN 1993-1-1:2005 member screens over the SAME demand
pipeline as `design.steel` (`_case_block`/`_demands`; library-W-shape
recognition by name + 1% area).  `gamma_M0 = gamma_M1 = 1.0`
(recommended values).  EC3 y-y (major) = SkyFrame local 3:

* cross-section: `Npl,Rd = A*fy`, `Mpl,Rd = Wpl*fy` per axis (the AISC
  table Zx/Zy), combined by the CONSERVATIVE LINEAR Eq. (6.2)
  `N/Npl + My/Mpl,y + Mz/Mpl,z <= 1` (the exact §6.2.9 plastic
  interaction is deferred, documented);
* shear `Vpl,Rd = Av*fy/sqrt(3)` with `Av = d*tw` (the exact rolled
  formula `A - 2*b*tf + (tw+2r)*tf` needs the root radius r the table
  lacks — documented simplification);
* §6.3.1 flexural buckling: `Ncr = pi^2*E*I/(k*L)^2`,
  `lambda_bar = sqrt(A*fy/Ncr)`, imperfection alpha from the Table 6.1
  curves (a0/a/b/c/d = 0.13/0.21/0.34/0.49/0.76), curve per the Table
  6.2 rolled-I rule with tf <= 40 mm (h/b > 1.2 -> a/b, else b/c;
  y/z), `phi = 0.5*(1 + alpha*(lambda - 0.2) + lambda^2)`,
  `chi = 1/(phi + sqrt(phi^2 - lambda^2)) <= 1` (chi = 1 exactly for
  lambda <= 0.2);
* §6.3.3 Eqs. (6.61)/(6.62) with `chi_LT = 1` (LTB DEFERRED, kc = 1)
  and Annex B Table B.1 (non-susceptible) factors,
  `Cmy = Cmz = 0.9` fixed (Table B.3 sway value):
  `kyy = 0.9*(1 + min(lambda_y - 0.2, 0.8)*n_y)`,
  `kzz = 0.9*(1 + min(2*lambda_z - 0.6, 1.4)*n_z)`, `kyz = 0.6*kzz`,
  `kzy = 0.6*kyy`, `n = NEd/(chi*A*fy/gamma_M1)`;
* governing `ratio`/`equation` = max of "6.2", "6.2.6" (shear),
  "6.61", "6.62"; TENSION members get the cross-section rows only.

Result rows (`MemberCheckEC3.to_dict()`): `{uid, section, kind, Pu,
Mu33, Mu22, Vu, NplRd, NbRd, MplRd33, MplRd22, VplRd, chi_y, chi_z,
ratio, equation, status, notes, preliminary, governing_combo}`.
`check_members_ec3_envelope` / `summarize_ec3` mirror the AISC pair.
Pins: chi(lambda = 1, curve b): phi = 0.5*(1 + 0.34*0.8 + 1) = 1.136,
chi = 1/(1.136 + sqrt(1.136^2 - 1)) = 0.5970231915935528; W18x50
Npl/Mpl,y/Mpl,z/Vpl exact from the published imperial dims.

## Eurocode 2 concrete checks (`skyframe.design.concrete_ec2`)

`check_concrete_members_ec2(model, results, case_or_combo, rebar, *,
fck=30000)` — EN 1992-1-1:2004, same call/result shapes as the ACI
module (`RebarLayout.fy` read as fyk; the `phiMn_*`/`phiVn` fields
carry the EC2 DESIGN resistances — no phi, the material factors
`gamma_C = 1.5` / `gamma_S = 1.15` live in `fcd`/`fyd`; fck <= 50 MPa
ENFORCED, the <= C50 laws only):

* beam flexure: `x = As*fyd/(0.8*b*fcd)` (lambda = 0.8, eta = 1.0),
  `MRd = As*fyd*(d - 0.4x)`; note when x/d > 0.45; NG when
  `As < As,min = max(0.26*fctm/fyk, 0.0013)*b*d`,
  `fctm = 0.30*fck[MPa]^(2/3)`;
* shear: `VRd,c = max(0.12*k*(100*rho_l*fck)^(1/3),
  0.035*k^1.5*sqrt(fck))*b*d` (MPa stresses, k = 1 + sqrt(200/d[mm])
  <= 2, rho_l <= 0.02); `VRd,s = (Asw/s)*0.9d*fywd*2.5` capped by
  `VRd,max = b*0.9d*nu1*fcd/(2.5 + 0.4)`, `nu1 = 0.6*(1 - fck/250)`;
  governing capacity `max(VRd,c, min(VRd,s, VRd,max))` — EC2 does NOT
  add Vc to Vs (documented difference from ACI);
* columns: uniaxial local-3 interaction via the SAME two-face
  strain-compatibility machinery as `design.concrete` with the EC2
  block (eta*fcd over 0.8c, eps_cu2 = 0.0035, steel clamped to fyd) —
  documented differences from ACI: no phi, no 0.80 compression cap
  (pure compression = `fcd*(Ag - Ast) + fyd*Ast` exactly), 0.0035 vs
  0.003; biaxial §5.8.9 deferred (note emitted).  Demand rated by the
  same radial rule.

`check_concrete_members_ec2_envelope` / `summarize_ec2` mirror the ACI
pair.

## NBCC 2020 lateral (`skyframe.core.codes`)

* `nbcc_ce(z, exposure)` — Table 4.1.7.1 power laws: open
  `(h/10)^0.2 >= 0.9`, rough `0.7*(h/12)^0.3 >= 0.7` (intermediate
  interpolation not implemented).
* `nbcc_wind_pattern(model, q, exposure="open", name="NWIND",
  direction="X", cp_total=1.3, Iw=1.0)` — static procedure
  `p = Iw*q*Ce(z)*Cg*Cp` with the reference velocity pressure `q`
  passed DIRECTLY in kPa, `Cg = 2.0`, `cp_total` = combined windward
  0.8 + leeward 0.5 = 1.3 (documented; both faces at Ce of the loaded
  level).  Same tributary story areas as `make_wind_pattern`; kind
  "wind".
* `nbcc_spectrum_value(T, Sa02, Sa05, Sa10, Sa20)` — S(T) from the
  four site-adjusted ordinates: plateau S(0.2) below 0.2 s, LINEAR IN
  log T between the octave points (documented simplification of the
  4.1.8.4 interpolation), `S(2.0)*(2/T)` beyond 2 s (S(5)/S(10) not
  requested — documented).
* `nbcc_seismic_elf(model, Sa02, Sa05, Sa10, Sa20, RdRo, Ie=1,
  system="other", direction="X", name="NELF")` — 4.1.8.11:
  `Ta = coeff*hn^0.75` (steel_mf 0.085 / concrete_mf 0.075 / other
  0.05), `V = S(Ta)*Mv*Ie*W/(Rd*Ro)` with `Mv = 1` FIXED (documented),
  floored at `S(2.0)*Mv*Ie*W/(RdRo)` and capped at
  `max(2/3*S(0.2), S(0.5))*Ie*W/(RdRo)` (the Rd >= 1.5 condition on
  the cap cannot be checked from the RdRo product — applied
  unconditionally, documented); distribution `w*h/sum(w*h)` with the
  top force `Ft = 0.07*Ta*V <= 0.25V` when Ta > 0.7 s.  Kind "quake".

## AISC 341 SMF joint screens (`skyframe.design.seismic341`)

`check_seismic341(model, results, case_or_combo, columns="auto", *,
Fy=345000, Ry=1.1, phi_pz=1.0)` — over the v0.17
`_panel_zone_joints` joint set (dedup point with >= 1 flexural column
end + >= 1 beam end), restricted to joints touching a designated
column (`columns` list of uids, or "auto" = all; unknown uids raise):

* SCWB (E3-1): `sum(Zc*(Fy - Puc/Ag)) / sum(1.1*Ry*Fy*Zb) >= 1` with
  Puc = `max(end-i N, 0)` from the chosen combo; major-axis Z assumed
  both sides; Muv and the E3.4a exemptions not applied (documented);
* panel zone: demand `Ru = sum(Mpb*)/(db - tf_beam)` (deepest beam) vs
  `phi*0.6*Fy*dc*tw*(1 + 3*bcf*tcf^2/(db*dc*tw))` (Eq. J10-11,
  `panel_zone_capacity`, phi = 1.0, Pr <= 0.75Pc assumed, NO doubler
  — documented), column = deepest VERTICAL column at the joint.

Rows: `{point, columns, beams, sum_Mpc, sum_Mpb, scwb_ratio,
pz_demand, pz_capacity, pz_ratio, status, notes, preliminary}`; joints
with any non-W-shape/demand-less member report "N/A" + note.
Pin: W14x90 column (Puc = 500) + one W18x50 beam:
scwb = 1.1757732622985737; capacity/demand from the J10-11/E3-1
longhand forms exactly.

## Cp wind on shells + camber

* `ShellRegion.wind_cp: Optional[float] = None` (round-trips; validated
  finite-or-None).  `make_shell_wind_pattern(model, q, name="SWIND")`
  (`skyframe.core.builder`): every region with `wind_cp` set gets
  `p = q*Cp` — SLAB regions as one `AreaLoad(q*Cp)` (gravity-down
  positive: positive Cp presses DOWN on the slab, negative = uplift);
  WALL regions (AreaLoads are gravity-only) as per-mesh-node
  `NodalLoad`s `F_i = q*Cp*A_trib,i` along the region's unit plane
  normal from the CCW corner ordering (`(c1-c0) x (c3-c0)`,
  normalized; positive Cp pushes ALONG the normal).  The tributary
  areas are the exact mesh quarter-element areas, so the resultant is
  EXACTLY `q*Cp*net_area`; the nodal loads are BAKED at the current
  mesh (regenerate after mesh_size/opening changes — the engine meshes
  deterministically, so unchanged models always find the nodes).
  Kind "wind"; ValueError on q <= 0 or when NO region carries wind_cp.
  Pin: 4 x 3 wall, q = 0.5, Cp = 0.8 -> resultant exactly 4.8 kN along
  -Y for the documented corner ordering; engine base reactions balance
  it to 1e-9.
* Composite camber (`skyframe.design.composite`): every applicable
  beam row gains `camber` + `defl_DL`.  `defl_DL` = the DEAD case's
  chord-relative bare-`Is` deflection (the v0.16 recovery integrates
  the member's own EI, so it IS the bare-steel value; the slab's
  effect on END displacements is the documented approximation);
  `camber_recommendation(delta, span)` = `0.8*delta` rounded DOWN to
  5 mm increments, ZERO when the rounded value < 20 mm or span <
  7.5 m (industry fabrication rule of thumb, documented — not a code
  requirement).  `camber` is null when no dead-case deflection is
  recoverable.  Pin: 9 m W18x50 under w = 40 kN/m ->
  delta = 5wL^4/384EI = 51.31 mm -> camber 40 mm; 6 m span -> 0.

## API

* `POST /api/design/steel` body gains `code: "AISC360" (default) |
  "EC3"`; `POST /api/design/concrete` gains `code: "ACI318" | "EC2"`
  (`fc` = fck, rebar `fy` = fyk under EC2).  Same response shape; the
  `code` key is echoed back EXACTLY WHEN the request carries it — a
  code-less request/response is BIT-IDENTICAL to pre-v0.23.  400 on an
  unknown code.
* `POST /api/pattern/nbcc-wind` `{q, exposure?, name?, direction?,
  Cp?, Iw?}`, `POST /api/pattern/nbcc-elf` `{Sa02, Sa05, Sa10, Sa20,
  RdRo, Ie?, system?, name?, direction?}`, `POST
  /api/pattern/shell-wind` `{q, name?}` — each returns the updated
  model dict, 400 on bad/missing parameters (shell-wind also 400 when
  no region has wind_cp).
* `POST /api/design/seismic341` `{combo?: name (default: first
  additive combo), columns?: [uids]|"auto", Fy?, Ry?}` ->
  `{preliminary, combo, columns, joints: [...], summary: {n, ok, ng,
  na, min_scwb, max_pz_ratio, preliminary}}`; 400 on an unknown
  combo/uid.
* `POST /api/model` round-trips `shells[*].wind_cp`;
  `POST /api/design/composite` rows carry `camber`/`defl_DL`.

# v0.24 additions — analysis parity I

Units everywhere: kN, m, kPa, tonne, s.  Four ANALYSIS features (no
design): load-dependent Ritz vectors, Fast Nonlinear Analysis, per-mode
(modal) damping for time-history cases, and an eigen-solver policy fix.

## Load-dependent Ritz vectors (`skyframe.core.ritz`)

`ritz_analysis(model, n, direction="X"|"Y"|"XY")` (engine wrapper
`OpenSeesEngine.run_ritz(n=None, direction="X")`) — the classic WYD /
Leger sequence, SELF-CONTAINED numpy on the SAME engine-identical
frame stiffness the buckling module assembles
(`skyframe.core.buckling.assemble_elastic_stiffness`, extracted in
this version — `buckling_analysis` behavior unchanged) and the SAME
diagonal mass rule as the engine's `_assign_mass` (story masses on
diaphragm masters with `Izz = m*(lx^2+ly^2)/12`, else spread over the
story nodes; `NodalMass` entries on their own dofs):

* start vector `x_1 = K^-1 f` with `f = M r_dir` (mass-proportional;
  "XY" alternates an X and a Y chain), then `x_{i+1} = K^-1 M u_i`;
  each candidate two-pass Gram-Schmidt M-ORTHOGONALIZED against all
  previous vectors and M-normalized; a chain whose candidate's M-norm
  collapses below 1e-8 of its pre-orthogonalization value ends (the
  load-reachable subspace is exhausted — FEWER than `n` vectors come
  back, with a warning);
* reduced eigenproblem `K_r = U^T K U`, `M_r = I` -> Ritz values /
  vectors, ascending; `RitzResults` carries ModalResults-compatible
  `periods` / `frequencies` / `participation` (same entry shape:
  `{mode, T, ux, gamma_x, uy, gamma_y, rz}`) / `shapes` (module-own
  node tags), plus `direction` and `warnings`.
* RIGID DIAPHRAGMS ARE SUPPORTED (unlike buckling, which ignores
  them): per rigid story a master at `plan_center()` and the exact
  rigid-body condensation `ux_s = ux_M - (y_s-cy)*rz_M`,
  `uy_s = uy_M + (x_s-cx)*rz_M`, `rz_s = rz_M` assembled into
  `T` with `K_c = T^T K T`, `M_c = T^T M T` — the same elimination the
  engine's Transformation handler performs.  Modelling scope otherwise
  matches buckling v0.10 HONESTLY: frame members only (shells/links
  SKIPPED with a warning — wall/link-stiffened systems will read too
  soft), releases/offsets ignored with a warning.
* The matrix-level core `ritz_vectors(K, M, F, n, return_basis=False)`
  is public — `F` may be ANY load block (the "user vector" entry).
* Pins: a 2x2 hand problem (K = [[3000,-1000],[-1000,1000]],
  M = diag(4,2)) reproduces the longhand first WYD vector and BOTH
  exact eigenvalues (2 Ritz vectors span the full space); on frames
  the first Ritz period matches `run_modal` to < 0.1% (measured:
  machine precision on a 3-story diaphragm building) and the TOTAL
  n-vector ux participation is >= the n-mode eigen total (the Ritz
  guarantee), both asserted against `run_modal` on the same models.

## FNA — Fast Nonlinear Analysis (`OpenSeesEngine.run_fna(case)`)

For TH cases whose ONLY nonlinearity is in device links: classic
Wilson FNA — modal superposition of the LINEAR structure with the
nonlinear link forces as pseudo-forces, fixed-point iterated per time
step (`skyframe.core.fna.fna_modal_th`; Newmark constant-average
acceleration per mode at the record dt, OpenSees-identical step/start
conventions; stalled sweeps under-relax 0.5 after 10 iterations;
non-convergence raises naming `run_time_history` as the fallback).

* Linearized modal basis: `eng.run_modal()` of a deepcopy whose device
  links are removed (damper/gap/hook — zero elastic part) or replaced
  by their linear-elastic part (isolator -> elastic link
  `[k1, k1, kv]`); `model.num_modes` modes; the retained shapes are
  M-orthonormalized by modified Gram-Schmidt because the dense eigen
  fallback returns NON-M-orthogonal vectors inside DEGENERATE clusters
  (measured phi_1^T M phi_2 = -0.32 on a doubly-symmetric isolated
  block; without the fix the FNA fixed point drifts exponentially).
* Device laws in pure numpy (`skyframe.core.fna`), matching the
  wave-16-validated element laws: Maxwell damper alpha = 1
  (trapezoidal rule on `dF/dt = k(v - F/cd)`), gap `F = k(d+gap)` for
  `d < -gap`, hook mirror, bilinear kinematic isolator (Steel01 law,
  independent x/y shears, elastic vertical kv in the basis).
* HONEST SCOPE (each raises `NotImplementedError` naming direct
  integration): hinged (`nonlinear`) cases, `gravity` stages, damper
  `alpha != 1`, `fp_isolator`, `triple_fp`, `multilinear`.
* Returns the SAME `THResults` shape as `run_th` (cached per engine):
  story series from the linearized model's masters / story-node
  averages, story-shear peaks by the same inertia-equilibrium rule,
  base series `FX = sum_i L_i*(qdd_i + 2*zeta_i*w_i*qd_i) + Mx*ag` —
  exactly the elastic-resisting-force reaction sum OpenSees reports
  (VERIFIED: OpenSees support reactions carry NO damping share,
  `R = -k*u` on an SDOF).  Modal truncation documented: the ground-
  acceleration mass term is complete, modes beyond `num_modes` absent.
* Pins: a device-free / elastic-link case matches linear `run_th` to
  ~1e-15 (FNA with zero nonlinearity IS modal superposition — same
  integrator, same damping diagonal); the wave-16 isolated block
  matches direct integration to ~2e-7 peak/residual (spec < 2%) and
  the wave-16 hand bilinear integrator to the same tolerance; a
  grounded damper decay matches `run_th` to ~2e-4; the module-level
  integrator passes an energy-balance check (input = kinetic + strain
  + hysteretic + viscous to < 0.1%).
* API: `POST /api/analyze/fna {case}` (a NEW ENDPOINT was chosen over
  a TH-case flag so `run()`/`/api/analyze` semantics stay untouched)
  -> the TH results dict + `{"case", "method": "FNA"}`; 400 with the
  NotImplementedError text on unsupported cases.  `run()` never runs
  FNA implicitly.

## Modal damping (`TimeHistoryCase.damping_model` / `modal_zeta`)

* `damping_model: "rayleigh" (default) | "modal"`, `modal_zeta:
  Optional[List[float]]` — per-mode ratios, TRUNCATED/PADDED with the
  last value to the computed mode count; empty/None means the flat
  `damping` in every mode.  Validated (`damping_model` in the pair,
  each ratio finite in (0, 1)); round-trips through
  `to_dict`/`from_dict` (pre-v0.24 files stay Rayleigh).
* Engine (`run_time_history`): `"modal"` runs an eigen solve IN the
  transient domain (`min(num_modes, massed dofs)` modes — the stored
  eigenpairs survive `wipeAnalysis`) and applies
  `ops.modalDamping(*zetas)`; modes beyond the computed count are
  UNDAMPED (documented).  The default `"rayleigh"` path is
  BIT-IDENTICAL to pre-v0.24 (same `ops.rayleigh` calls).  FNA uses
  the per-mode ratios natively.
* Pins: SDOF free decay with `modal_zeta=[0.05]` -> log-decrement
  0.05 to 1% (measured 0.05003); a 2-mass column with
  `modal_zeta=[0.02, 0.10]` shows INDEPENDENT per-mode decay (response
  projected onto the mass-weighted mode shapes, each mode's log
  decrement matches its own zeta); FNA under modal damping matches
  the OpenSees modal-damping run to ~1e-15.

## Eigen-solver policy (`_solve_eigen`)

Investigated: the default shift-invert Band-Arpack solver's Arnoldi
space is capped at rank(M) — it MATHEMATICALLY cannot return
`n == n_massed` modes (`_saupd info = -9999`), and that is exactly the
default small-model case (num_modes 12 == 4 stories x 3 diaphragm
dofs), which is why the dense fallback and its native "VERY SLOW"
console warning fired on every such modal run.  New policy:

* `n < n_massed`: default Arpack first; on failure/garbage ONE retry
  with `-genBandArpack` on a freshly rebuilt SOE (openseespy exposes
  NO explicit shift argument — the "small shift" retry is realised as
  a clean-state rerun, documented); only then `-fullGenLapack`.
* `n == n_massed`: dense LAPACK is REQUIRED (not a bug) — on tiny
  models (< 200 estimated dofs, `EIGEN_QUIET_DOF_CAP`) the native
  warning is suppressed via an fd-level redirect and downgraded to a
  `logging.debug` note; bigger models keep the visible warning.
* Results are UNCHANGED by policy: Arpack and fullGenLapack
  eigenvalues agree to < 1e-8 relative (asserted in the suite).
  Honest timing note: models with `n < n_massed` already took the fast
  Arpack path pre-v0.24 (quick_building(stories=8), 12 modes: ~0.02 s
  Arpack vs ~0.20 s dense), so this change removes console noise and
  adds a retry — it does not speed up runs that were already on
  Arpack, and the `n == n_massed` dense case cannot be avoided.

## API

* `POST /api/analyze/ritz` `{n?, direction?}` ->
  `RitzResults.to_dict()`; 400 on bad parameters.
* `POST /api/analyze/fna` `{case}` -> TH results dict +
  `{"case", "method": "FNA"}`; 400 on unknown case / unsupported
  feature (message names `run_time_history`).
* `POST /api/model` round-trips `th_cases[*].damping_model` /
  `modal_zeta`; 400 on a bad damping_model or ratios outside (0, 1).
