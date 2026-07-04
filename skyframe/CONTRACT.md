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
