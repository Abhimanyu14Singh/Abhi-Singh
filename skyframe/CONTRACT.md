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

# v0.25 additions — analysis parity II

Units everywhere: kN, m, kPa, tonne, s.  Four ANALYSIS features (no
design): large-displacement (corotational) geometric nonlinearity,
buckling from a stressed/staged base state, floor-cracking iterative
stiffness, and time-dependent (creep/shrinkage) staged construction.

## Large-displacement geometric nonlinearity (`LoadCase.geometric`)

`LoadCase.geometric: "linear" (default) | "pdelta" | "corotational"`.
PRECEDENCE (exact): a non-`"linear"` `geometric` WINS; with `geometric
== "linear"` (or the key absent — every pre-v0.25 file) the legacy
`pdelta` bool decides (`True -> "pdelta"`).  BOTH fields stay
serialized (`to_dict` keeps `pdelta` unchanged); the engine consults
only `LoadCase.effective_geometric`.  `PushoverCase.geometric` takes
the same options (default `"linear"` = the exact pre-v0.25 build) for
large-displacement pushover.  `add_case`/`add_pushover_case` accept
`geometric=`; validation rejects anything outside the option tuple.

* Engine: `"corotational"` builds every frame member on
  `geomTransf('Corotational', ...)` and reuses the EXACT two-stage
  P-Delta flow (`pdelta_gravity` applies identically; the reported
  response is the increment past the held gravity state).  The ONLY
  solver difference: each corotational stage ramps its load in
  `CORO_INCREMENTS = 10` equal LoadControl increments with a
  NewtonLineSearch retry per increment (a single Newton step to a
  finitely rotated state frequently diverges).  The `"pdelta"` path is
  BIT-IDENTICAL to the pre-v0.25 pdelta bool (same OpenSees calls,
  single load step — asserted in the suite).
* HONEST LIMITATION (probed on openseespy 3.7.1): the 3D Corotational
  transformation cannot carry elasticBeamColumn `-releasez/-releasey`
  codes (singular system).  In a corotational build, RELEASED segments
  (member-end moment releases; the release-coded outer elements of
  v0.22 edge-tie chains) fall back to the PDelta transformation with a
  `warnings.warn` naming them.  `eleLoad -beamUniform` on corotational
  members works (verified).
* Superpose/envelope warnings and the virtual-work LINEAR-case guard
  now key on `effective_geometric != "linear"` (message text still
  contains "P-Delta" for compatibility).
* Pins: Mattiasson (1981) elastica cantilever, PL^2/EI = 1 (w/L =
  0.30172, u/L = 0.05643), 10 corotational elements to < 1% (measured
  0.04% / 0.25%); the deep PL^2/EI = 3 regime against an in-suite RK4
  elastica ODE reference (itself reproducing the Mattiasson row) to
  < 1%; corotational == linear at tiny load to 1e-9 relative;
  corotational ~ P-Delta at moderate gravity (5%); the elastic
  corotational pushover slope = the exact base-hinge series formula
  `EI/L^3 / (1/3 + 1/60)`.

## Buckling from a stressed/staged state (`BucklingCase.base_case`)

`BucklingCase.base_case: Optional[str] = None` — the name of a STATIC
load case (validated to exist).  Pre-v0.25 behavior (`None`): the
buckling case's own `gravity` dict, solved LINEARLY inside the numpy
buckling module, is the ONLY axial state.  With a base case the engine
solves it through the FULL engine first (its own P-Delta/corotational/
tension-only behavior — the CONVERGED state) and extracts per-member
reference axials as the MEAN of the tension-positive station values
N(x) (exact for end-loaded columns; the natural constant-N reduction
of a linearly varying field otherwise).  These feed
`buckling_analysis(model, gravity, num_modes, base_N, base_label)` as
the FIXED base geometric stiffness in the standard TWO-LOAD-SET form

    (K + Kg(N_base)) phi = lambda (-Kg(N_gravity)) phi

so LAMBDA MULTIPLIES THE BUCKLING `gravity` LOADS ONLY, GIVEN the base
state (the base state is held, never scaled).  `N_gravity` still comes
from the module's internal linear solve on the UNSTRESSED K (the
classic linearized treatment).  Kg is linear in N, so for a shared
distribution `lambda(base P0) = lambda(no base) - P0` EXACTLY.
`BucklingResult` gains `base_case` (serialized).

* A base state at/beyond the buckling load makes `K + Kg(N_base)`
  indefinite: the Cholesky fails and the result carries factors = []
  plus the explicit warning "base axial state is at or beyond the
  buckling load ..." (no silent garbage).
* Pins: Euler cantilever column — `lambda0 ~ pi^2 EI/(2L)^2` to 1%
  (1-element consistent Kg) and, with base `P0 = 0.5 lambda0` fed
  through a real static case, remaining multiplier `= 0.5 lambda0` to
  1e-9 relative (measured 4e-12 absolute on `lambda0 - P0 - lambda`);
  the engine force path (2-story column, per-story loads) equals the
  hand-fed `base_N` to 1e-12; staged-final-state equivalence follows
  from the v0.6 staged == one-shot force pins.

## Floor-cracking iterative stiffness (`skyframe.core.cracked`)

`cracked_analysis(model, case, cracked_ratio=0.35, fr_factor=0.62,
max_iter=10, tol=0.02)` / engine wrapper `run_cracked(...)` — iterate
a static case; after each solve every SLAB shell quad's extreme-fiber
bending stress `sigma = 6*max(|Mxx|, |Myy|)/t^2` is checked against
the modulus of rupture `fr = fr_factor*sqrt(fc') MPa` (ACI 318
19.2.3; fc' from the material `fc` attr or the established ACI
19.2.2.1 E-inversion `fc_from_E`); quads over fr get their stiffness
scaled by the FLAT `cracked_ratio` (Branson SIMPLIFIED — the full
`Ie = (Mcr/Ma)^3` interpolation is deliberately NOT applied,
documented) and the case re-solves until the cracked set stabilizes.

* GRANULARITY DELIVERED: PER-QUAD factors (`OpenSeesEngine.
  _quad_stiff_scale`: one extra ElasticMembranePlateSection per
  distinct (section, factor) pair, assigned quad by quad).  The
  section has ONE modulus, so membrane scales WITH bending — the same
  documented limitation as `ShellSection.mod`; transverse plate
  bending (the cracking driver) is unaffected by the membrane share.
* Iteration: MONOTONE (a cracked quad stays cracked — terminates in
  <= n_quads passes up to `max_iter`); converged on (a) a stable set,
  or (b) max |uz| changing < `tol` relative between passes (marginal
  flip guard, reported in the warnings); non-convergence at `max_iter`
  returns the last state with an explicit warning.  Walls never crack
  here (slab quads only); membrane slabs have no quads.
* Returns `CrackedResults`: the FINAL iteration's CaseResults +
  `cracking` map `{quad index: {region, cracked, Ma, Mcr}}` (kN*m/m;
  indices match `shell_forces`/`shell_quads`), `iterations` (solve
  count), `converged`, echoed parameters.
* Pins: below cracking -> ONE iteration, BIT-IDENTICAL to the elastic
  case (the empty-scale build IS the standard build); far above
  cracking on a simply supported strip -> every quad cracked and
  deflection EXACTLY `1/cracked_ratio` x elastic (1e-9); `Mcr =
  fr*t^2/6` hand value to 1e-12; a threshold load cracks ONLY the
  midspan band (0 < cracked < all).
* API: `POST /api/analyze/cracked {case, cracked_ratio?}` -> final
  case results dict + `{"cracking", "iterations", "converged",
  "cracked_ratio", "fr_factor", "cracked_warnings", "case", "method":
  "cracked"}`; 400 on unknown case / bad ratio.

## Time-dependent staged construction (`StagedCase.time_dependent`)

`StagedCase.time_dependent: Optional[dict] = None` — `None` keeps the
elastic staged run BIT-IDENTICAL to pre-v0.25.  Keys: `days_per_story`
(REQUIRED, > 0), `creep_coeff` phi_inf (default 2.0), `shrinkage`
eps_sh_inf (default 300e-6), `aging` bool (default False), `t_eval`
days (default None = t -> infinity), `materials` list (concrete
filter; default ALL materials).  Method: AGE-ADJUSTED EFFECTIVE
MODULUS (deterministic hand method — NOT OpenSees TDConcrete), chi =
0.8 (`TD_CHI`), ACI 209 curves as module functions `aci209_creep`
(phi_inf*(t/(10+t))^0.6, t = days under load), `aci209_shrinkage`
(eps_inf*t/(35+t), t = days since cast), `aci209_modulus_growth`
(sqrt(t/(4+0.85t))).

* TIMELINE (exact): story j (0-based) is CAST at `j*d`; stage k's
  loads ARRIVE at `(k+1)*d` (one cycle later — the story above is
  starting), so a story is at least d days old when loaded.  At stage
  k, story j's concrete members carry `E_adj = E(t0)/(1 +
  chi*phi(t_eval - (k+1)*d))` with `E(t0) = E28 *
  aci209_modulus_growth((k+1-j)*d)` when `aging` (else E28; the ACI
  loading-age phi correction 1.25*t0^-0.118 is NOT applied — phi_inf
  is uniform, documented).  Implementation: per-stage submodels get
  per-story CLONED materials/sections (members) and shell sections
  with `mod` scaled (shells) — the parent model is never mutated.
  `include_live` stays at E28 (short-term loads do not creep) and the
  one-shot comparison stays elastic (it measures sequencing, not
  creep).  A finite `t_eval` before a stage's load arrival clamps that
  stage's creep to 0 (all stages are still applied; mid-construction
  snapshots are out of scope).
* SHRINKAGE: one extra increment on the full structure — every
  concrete FRAME member of story j gets the exact thermal equivalence
  `dT = -eps_sh(t_eval - j*d)/thermal_alpha` (member-only: the thermal
  machinery does not load shells, documented) on the age-adjusted
  stiffness `E28/(1 + chi*phi(t_eval - j*d))` (a gradually developing
  strain, the standard AAEM treatment; no aging growth factor).
* REPORT: `StagedResults.shortening` (serialized `"shortening"`) —
  per story `{uz_elastic, uz_time_dependent, delta}` = mean cumulative
  uz over that story's column TOPS, from the time-dependent pass vs an
  internally re-run ELASTIC staged pass (`delta` = creep + shrinkage
  share).  Rebuild-and-accumulate note: a story-k node accumulates
  only from stages >= k (cast to design elevation — the staged pins
  encode this).
* Pins (all exact): held load at t = inf -> `delta_total/
  delta_elastic = 1 + chi*phi_inf` to 1e-12 (the AAEM closed form);
  finite `t_eval` follows `1 + chi*phi(t_eval - d)` on the ACI curve
  to 1e-12; an UNLOADED column's shrinkage shortening `= eps_sh(t)*L`
  to 1e-9 (determinate -> E-independent, thermal equivalence exact,
  both t = inf and the t/(35+t) half-value at 35 d); the 2-story
  creep total = the longhand stage sum; aging hand factors
  `1/growth((k+1-j)d)` reproduce the engine to 1e-12; phi = eps = 0
  time-dependent == elastic to 1e-15; `materials` filter leaves a
  steel story's share exactly elastic.

## API

* `POST /api/analyze/cracked {case, cracked_ratio?}` (above).
* `POST /api/model` round-trips `cases[*].geometric`,
  `pushover_cases[*].geometric`, `buckling_cases[*].base_case`,
  `staged_cases[*].time_dependent` (pre-v0.25 files load unchanged:
  absent keys default to linear/None).

# v0.26 additions — performance + public API

Units everywhere: kN, m, kPa, tonne, s.  No new mechanics: a measured
engine performance pass (results BIT-IDENTICAL, see below) and the
public Python scripting facade `skyframe.client`.

## Engine performance (`docs/PERF_NOTES.md` for profiles + numbers)

* ELASTIC-DOMAIN REUSE: linear/Newton static case solves and the
  center-of-rigidity unit-load solves reuse the already-assembled
  OpenSees domain — previous load pattern + time series removed,
  `ops.reset()` (revert-to-start), re-load, re-solve — instead of a
  full `ops.wipe()` rebuild per solve.  Ownership token: set ONLY by
  `OpenSeesEngine._elastic_domain` (a weakref to the engine whose
  DEFAULT elastic build lives in the interpreter); ANY `_build()` call
  clears it first, so hinged / P-Delta / corotational / TH / pushover /
  eigen builds are never mistaken for a reusable domain and interleaved
  engines stay isolated (tested).  Verified bit-identical (node disps,
  reactions, element local forces) on frame and shell models before the
  code was written; guarded continuously by the full-dict identity
  tests in `tests/test_perf_api.py`.
* `SKYFRAME_SLOW_PATH=1` (env, read per solve) forces the pre-v0.26
  rebuild-per-case path — the A/B reference for the identity tests and
  an escape hatch.
* `_superpose` lookup hoisting (same float ops, same order — the
  accumulator's first step `0.0 + f0*x` IS `sum()`'s leading step) and
  pure-geometry memos (`_local_axes` per member, `_eff_props` per
  section, unsplit-member station fast path): bit-identical by
  construction, covered by the same identity tests.
* HONEST BOUNDS: measured medians 1.64x (8-story frame), 1.60x
  (12-case model), 1.01x (shell-heavy — that model is ~95% native
  eigen/factorization kernels, deliberately untouched: changing the
  eigen solver would change the modal floats).  KNOWN PRE-EXISTING
  NON-DETERMINISM, now documented: the ARPACK eigen solve is not
  run-to-run deterministic (last-bit floats, eigenvector signs) even in
  the unmodified engine; the identity tests therefore compare
  `results["modal"]` periods to 1e-9 relative and everything else
  EXACTLY.  CI timing guard: optimized median <= 0.9x slow-path median
  (generous; measured ~0.65x).

## Public Python API (`skyframe.client`, `docs/PUBLIC_API.md`)

THIN wrappers only (no new logic; the endpoint and the facade can never
disagree): `quick_building` (re-export), `open_model(path)` /
`save_model(model, path)` (the gallery JSON format), `run(model)` ->
`AnalysisResults` (`.to_dict()` == the `POST /api/analyze` payload),
`run_modal(model, num_modes=None)`, `run_ritz(model, n=None,
direction="X")`, `run_fna(model, case)`, `run_pushover(model, case)`,
`run_cracked(model, case, cracked_ratio=0.35, fr_factor=0.62,
max_iter=10, tol=0.02)`, `design_steel` / `design_concrete` /
`design_wall` / `design_punching` (same kwargs and payload shapes as
their endpoints; optional `results=` reuses an existing `run()` bundle),
and `to_dataframe(results, table)` -> list-of-dicts for `"drifts"` |
`"reactions"` | `"member_forces"` (one row per case/combo per story /
support node / member end) | `"design"` (flattens a design payload's
checks/piers/columns).  Everything in `skyframe.client.__all__` is the
stable surface.  Every fenced python example in `docs/PUBLIC_API.md` is
executed by `tests/test_perf_api.py::test_public_api_doc_examples`.

## API

* No endpoint changes.  `POST /api/analyze` payloads are bit-identical
  to v0.25 (modal caveat above applies to consecutive runs of ANY
  version).

# v1.12 additions — ETABS material-property parity (analysis-only)

Expands the `Material` dataclass toward ETABS material definitions.  ALL
new fields are optional and keyword-defaulted; the positional signature
`Material(name, E, nu, unit_weight)` is preserved and old models load
unchanged (`material_type="concrete"`, every optional `None`), so every
engine fallback fires exactly as before — the full pre-change test suite
is byte-identical.  Scope is ANALYSIS-ONLY: no design-code fields (phi
factors, detailing, code family) are added, and no case/combo type
changes.

## `Material` (`skyframe.core.model`)

New fields appended AFTER `unit_weight` (defaults in parens):

```
material_type: str  = "concrete"   # one of MATERIAL_TYPES
symmetry:      str  = "isotropic"  # "isotropic" | "uniaxial"
mass_density:  float|None = None   # tonne/m^3; None => unit_weight/g
alpha:         float|None = None   # 1/degC;   None => model.thermal_alpha
fc:            float|None = None   # kPa (concrete f'c)
fy:            float|None = None   # kPa (steel/rebar/tendon Fy)
fu:            float|None = None   # kPa (steel/rebar/tendon Fu)
Ry:            float = 1.1         # expected/specified yield ratio
damping:       float = 0.0         # per-material modal damping ratio
lightweight:   bool  = False       # lightweight-concrete flag
lam:           float = 1.0         # lightweight lambda (fr knockdown)
color:         str   = ""          # display hex swatch (cosmetic)
notes:         str   = ""          # free text (cosmetic)
```

Module constants: `MATERIAL_TYPES = ("steel","concrete","rebar","tendon",
"masonry","aluminum","coldformed","other")`, `MATERIAL_SYMMETRY =
("isotropic","uniaxial")` (orthotropic/anisotropic are REJECTED by
validation — out of analysis scope).

Derived read-only properties: `G = E/(2*(1+nu))` (unchanged);
`mass_per_volume` -> `mass_density if set else unit_weight/G_ACCEL` (the
SINGLE source both self-mass paths read); `Fye` -> `Ry*fy` (None when fy
None); `Fue` -> `Ry*fu` (None when fu None).

`to_dict` = `asdict(self)` plus echoed derived `G`, `mass_per_volume`,
`Fye`, `Fue` (for UI/report; never re-read on load).  Optional None
fields serialize as JSON `null`.  `from_dict` reads every new key
defensively (numeric optionals through `_optf` -> None on absent/null,
else `float`), so `BuildingModel.from_dict(old.to_dict())` round-trips
all fields exactly.

Validation (in `BuildingModel.validate`, per material): `material_type`
in `MATERIAL_TYPES`; `symmetry` in `MATERIAL_SYMMETRY`; `E` finite `> 0`;
isotropic `nu` in `[0, 0.5)` (skipped for uniaxial); `mass_density` (if
set) finite `> 0`; `alpha` (if set) finite; `fc`/`fy`/`fu` (if set)
finite `> 0`; `Ry` in `(0, 2]`; `damping` in `[0, 1)`; `lam` finite
`> 0`.

Library: `default_material_library()` -> the four ETABS built-ins
(`A992Fy50` steel, `4000Psi` concrete, `A615Gr60` rebar, `A416Gr270`
tendon) in SI consistent units; `library_material(name)` and
`BuildingModel.add_library_material(name)` (KeyError on unknown name).

## Engine wiring (analysis wins; None/default is bit-identical)

* **Per-material thermal alpha.**  `_apply_thermal` and the staged
  shrinkage->dT pattern use `mat.alpha` when set, else
  `model.thermal_alpha` (precedence: material over model-wide).  The
  `eps_sh -> dT` identity `alpha*dT*L = -eps_sh*L` holds for any alpha,
  so the same alpha is used for the load and the fixed-end force.
* **fc / fy pickup.**  No code change at the six consumer sites
  (engine layered shell + fiber PMM, `cracked.py`, `hinges.py`): they
  already read `getattr(mat,'fc'/'fy',...) or <fallback>`.  With the
  attribute now present, a set value flows through and `None` still
  falls back (`None or X == X`).
* **Ry -> expected strength.**  `hinges.auto_backbone` defaults
  `expected_factor` to `mat.Ry`; `PushoverCase.hinge_params['expected
  _factor']` (and the function kwarg) still override.  `Ry == 1.1` is
  bit-identical.
* **Lightweight lambda.**  `cracked.py` modulus of rupture
  `fr = fr_factor * mat.lam * sqrt(fc'[MPa])`.  `lam == 1.0` is
  bit-identical.
* **mass_density -> self-mass.**  `BuildingModel.mass_source_mode`
  (`"weight"` | `"element_self_mass"`, one of `MASS_SOURCE_MODES`,
  round-tripped).  `"weight"` (default) is the unchanged gravity-derived
  story-mass path.  `"element_self_mass"` lumps `mat.mass_per_volume *
  volume` (frame `A*L`, shell `t*net_area`) as translational nodal mass
  (dof 1..3), split equally over element end/corner nodes, decoupling
  mass from weight.  Both paths read `Material.mass_per_volume`, so with
  no material overriding `mass_density` the element total equals the
  weight/g total.

`fu` is stored/round-tripped and reserved for the fiber/hinge hardening
ceiling (no fallback change until consumed).

## API

* `GET  /api/materials/library` — the four ETABS default materials as
  `{name: Material.to_dict()}` (derived `G`/`Fye`/`Fue`/`mass_per_volume`
  echoed).
* `POST /api/materials/library/<name>` — add one library material to the
  current model (404 on unknown name).
* `POST /api/model` round-trips every new `Material` field and
  `model.mass_source_mode`; pre-v1.12 files load unchanged.

Tests: `tests/test_materials.py` (38 cases) — round-trip, per-type
library defaults, back-compat minimal/null load, validation, per-material
alpha changing a thermal axial force (None == model default), mass_density
scaling a modal period (None == weight/g, 2x -> sqrt(2) period, element
total == rho*V), and fc/fy/Ry/lambda flowing into the cracked-slab Mcr and
the ASCE-41 hinge backbone with the None fallback preserved.

---

# v1.13 additions — analysis control (cases to run, active DOF, mass source, material curves, display units)

Analysis only.  A model that sets none of the new fields produces
results byte-identical to v1.12 (every pre-v1.13 result key/value is
unchanged; verified on a bundle of static / combo / modal / RS / TH /
pushover / staged / buckling / shell models).  Old JSON files load
unchanged (every new key absent -> its default).  Tests:
`tests/test_wave_analysis3.py` (78 cases).

## 1. Set Load Cases to Run

```
BuildingModel.cases_not_run: List[str] = []
```

Names of ANY case kind: static `cases`, `rs_cases`, `th_cases`,
`pushover_cases`, `staged_cases`, `buckling_cases`, plus the reserved
name `"MODAL"` (the eigen analysis; a user case that is itself named
"MODAL" shadows it).  Validation: list of strings, no duplicates, every
name must exist (`BuildingModel.case_kinds()` lists them) -> else
`ValueError` (400 on `POST /api/model`).

`OpenSeesEngine.run()` / `POST /api/analyze`:

* not-run cases are skipped (absent from `cases` / `rs_cases` /
  `th_cases` / `pushover` / `staged` / `buckling`; `MODAL` not run ->
  `modal` is the empty modal block);
* dependencies run anyway (ETABS behaviour) and are reported
  `"run_as_dependency"` with a note in `warning`: RS and TH cases need
  `MODAL`; a buckling case with `base_case` needs that static case (its
  results then appear in `cases`).  Pushover / staged / TH gravity stages
  reference PATTERNS, not cases, so they create no case dependency;
* combos (`combos`) referencing a case without results are NOT computed:
  `combo_status[name] = "skipped"` + a `warning` note (not an error).
  RS directional combos (`rs_combos`) likewise when an RS case is missing;
* a case whose analysis raises (`RuntimeError`/`ValueError`/
  `ArithmeticError`) is reported `"failed"` (+ warning) and the run
  continues (previously the whole request failed with 400);
* TH / pushover cases skipped by the existing step caps are `"not_run"`.

New result keys (always present):

```
"case_status":  {case_name: "finished" | "not_run" | "run_as_dependency" | "failed"}
                # every case of every kind + "MODAL"
"combo_status": {combo_name: "finished" | "skipped"}
                # every LoadCombo + every RS directional combo
```

## 2. Set Active Degrees of Freedom

```
BuildingModel.active_dof: List[str] = ["UX","UY","UZ","RX","RY","RZ"]
```

Order-independent subset of `DOF_LABELS`.  Presets (`ACTIVE_DOF_PRESETS`):
`full_3d` = all six; `xz_plane` = `["UX","UZ","RY"]`; `yz_plane` =
`["UY","UZ","RX"]`; `xy_plane` = `["UX","UY","RZ"]`.  Validation:
non-empty, only those labels, no duplicates.

Engine (`_restrain_inactive_dofs`, end of every `_build`, before the
zero-free-DOF guard and mass assignment): every inactive DOF of every
node is fixed, merged with what exists — DOFs already fixed (supports,
auto-restraints, grounded spring/anchor nodes) are skipped (no duplicate
`ops.fix`), and DOFs constrained by an MP constraint (rigid-diaphragm
slaves' UX/UY/RZ, equalDOF ties of hinge / panel-zone duplicates) are
skipped — they follow their retained node, which is restrained instead
(a diaphragm master receives the inactive in-plane DOFs).  Mass on
inactive DOFs is dropped.  All six active -> no-op (bit-identical).
Not honoured by the self-contained numpy solvers (`run_buckling`'s
`buckling_analysis`, Ritz vectors) — they stay 3D.

## 3. Mass Source options

```
BuildingModel.mass_options = {          # always fully emitted by to_dict
  "self_mass": true,         # self-mass part
  "patterns": true,          # load-pattern part (mass_source / mass_from_patterns)
  "include_lateral": true,   # UX, UY (+ diaphragm RZ) mass
  "include_vertical": false, # UZ mass
  "lump_at_stories": true    # lateral mass at story master / story nodes (pre-v1.13)
}
```

Missing keys -> defaults; unknown keys / non-bool values -> `ValueError`;
at least one of `include_lateral` / `include_vertical` must be true; at
least one of `self_mass` / `patterns` must be true unless explicit
`nodal_masses` or `story_masses` exist.

* **Split** (`mass_source_mode == "weight"`, `compute_story_masses`): the
  SELF part is the self-weight of `self_weight_factor` patterns in the
  mass source (beams + slabs of the story; columns excluded — unchanged
  rule); the PATTERN part is member UDLs / gravity member loads / area
  loads / nodal loads of mass-source patterns.  Both on = the original
  summation order (bit-identical).  ELF / Ritz / performance-point
  consumers of `compute_story_masses` see the same toggles.
* **include_vertical**: UZ mass at the nodes where the mass arises
  (tributary, see below; a diaphragm master is UZ-restrained).  Explicit
  story masses go equally to the story nodes for UZ.
* **lump_at_stories = false**: lateral mass also stays at those tributary
  nodes (diaphragm slaves are condensed to the master by the
  Transformation handler).  Tributary rule (`_tributary_masses`): member
  loads and beam self-weight -> FE segment end nodes by the exact static
  lever rule; area loads / slab self-weight -> meshed-shell nodes by
  tributary area (membrane regions: their two-way load path), scaled so
  each region totals exactly `q * net_area`; nodal loads -> that node;
  anything without a node -> equal split over the story nodes.  Story
  totals equal the lumped totals (to fp rounding).
* Explicit `story_masses` (no location) are always included and stay
  story-lumped (lateral) — they follow the direction flags; explicit
  `nodal_masses` are always added as given (mx, my, mz).
* `mass_source_mode == "element_self_mass"` (v1.12): `self_mass` gates the
  element mass, `include_lateral` its UX/UY part; its UZ part is kept as
  in v1.12 regardless of `include_vertical`, and pattern (superimposed)
  mass is not added in that mode (v1.12 rule, kept for byte-identity).

**Serialization change (back-compatible):** `to_dict()["story_masses"]`
remains the EFFECTIVE per-story mass (unchanged values); the new key
`explicit_story_masses` carries only the user overrides.  `from_dict`
uses `explicit_story_masses` when present (so a GET->POST round trip no
longer freezes derived masses into explicit ones and the mass toggles
keep working); files without it keep the legacy rule (`story_masses`
read as explicit).  Clients that edit explicit story masses edit
`explicit_story_masses`.

## 4. Material stress-strain curves (`skyframe.core.stress_strain`)

```
Material.stress_strain: dict | null = null
{ "model": "default" | "simple" | "mander" | "park" | "user",
  "hysteresis": "kinematic" | "takeda" | "pivot" | "elastic",   # default kinematic
  "params": {...},          # concrete: eps_c0 (0.002), eps_cu (0.0035), ft (0 kPa), eps_tu (10 ft/E0)
                            # steel:    eps_sh (0.01), eps_su (0.09), b (0.01)
  "points": [[strain, stress_kPa], ...]   # "user" only
}
```

Compression negative.  Strength: concrete f'c = `Material.fc` else the
ACI E-inversion `fc_from_E(E)`; steel fy = `Material.fy` else 345 MPa
(steel/coldformed/aluminum) / 420 MPa (rebar/tendon); each consumer may
override it with its own resolved value (e.g. the expected Fye of a
W-shape hinge).  Park fu = `Material.fu` else 1.25 fy.

Applicability (validated): concrete/masonry -> default, simple, mander,
park, user; steel/rebar/tendon/coldformed/aluminum -> default, simple,
park, user; other -> default, user.  Also validated: unknown keys /
params, `points` only for user, user points strictly increasing strain,
include `[0, 0]`, stress signs match strain signs, <= 200 points;
`eps_cu > eps_c0`, `0 <= ft < f'c`, `eps_tu > ft/E0`, mander needs
`E > f'c/eps_c0`, Kent-Park needs `eps_50u > eps_c0`, park steel needs
`eps_sh > fy/E`, `eps_su > eps_sh`, `fu > fy`, `0 <= b < 1`.

Backbones (exact, `UniaxialLaw.exact`):

* concrete **simple** — Hognestad parabola `f'c[2x - x^2]`, `x = eps/eps_c0`,
  then linear to 0.85 f'c at eps_cu, held beyond (Concrete01 rule).
  Initial tangent E0 = 2 f'c / eps_c0 (Hognestad, not `Material.E`).
* concrete **park** — Kent-Park unconfined: same parabola; post-peak slope
  `-Z f'c`, `Z = 0.5/(eps_50u - eps_c0)`, `eps_50u = (3 + 0.29 f'c)/(145 f'c - 1000)`
  (f'c MPa); residual 0.2 f'c from eps_20 = eps_c0 + 0.8/Z (eps_cu unused).
* concrete **mander** — Mander unconfined (Popovics):
  `f'c x r/(r - 1 + x^r)`, `r = E/(E - f'c/eps_c0)`, zero beyond eps_cu.
* concrete tension (ft > 0): linear E0 to ft, then linear (simple/park,
  Concrete02) or `ft*0.1^((e-et)/(eps_tu-et))` (mander, Concrete04) to
  eps_tu, zero beyond.  ft = 0 -> no tension.
* steel **simple** — elastic-plastic with hardening `fy + bE(eps - fy/E)`
  (Steel01).
* steel **park** — elastic, plateau to eps_sh, Park (1975) curve
  `fy[(m u + 2)/(60 u + 2) + u(60 - m)/(2(30r + 1)^2)]`, `u = eps - eps_sh`,
  `r = eps_su - eps_sh`, `m = ((fu/fy)(30r+1)^2 - 60r - 1)/(15 r^2)`; fu held
  beyond eps_su.  Symmetric.
* **user** — linear interpolation of the points, last stress held beyond.
* **default** — identical to null (legacy law; hysteresis ignored).

OpenSees mapping (`UniaxialLaw.ops_material`) — honest approximations:

| hysteresis | concrete simple/park | concrete mander | steel simple | steel park | user |
|---|---|---|---|---|---|
| kinematic | Concrete01 (Concrete02 if ft>0) — native cyclic rule | Concrete04 | Steel01 | MultiLinear (18 stations of the Park curve + flat cap; exact at stations) | HystereticSM, pinch 1/1, no degradation (peak-oriented reloading — approximates kinematic) |
| takeda | HystereticSM, <= 7-point envelope/side, unloading k*mu^-0.4 (beta 0.4), no pinching — "Takeda-like" | same | same | same | same |
| pivot | HystereticSM, pinchX 0.5 / pinchY 0.3 — pivot-LIKE pinching, not the Dowell pivot rule | same | same | same | same |
| elastic | ElasticMultiLinear (24 stations/side + flat cap): nonlinear elastic, no dissipation | same | same | same | same |

HystereticSM envelopes are exact at their stations, linear between, flat
beyond (cap point); HystereticSM needs the first two segments of each
side rising, so a plateau/softening side gets a midpoint split; zero
stresses are floored at 1e-6 x peak; a side without stress (concrete
with ft = 0) gets a negligible stub.  `UniaxialLaw.stress(e)` is the
ENGINE-EFFECTIVE monotonic backbone (exact formula for native materials,
the interpolated points otherwise) — it equals the OpenSees material
under monotonic loading to ~1e-15 (pinned for every model x hysteresis).

Consumers that HONOUR `stress_strain` (null/"default" -> legacy law,
bit-identical):

* fiber PMM hinges (`fiber_pmm` members in asce41 pushovers):
  designer-section base polygons (concrete and steel bases), designer
  rebar points (their own material), library W-shape steel (strength =
  expected Fye), rectangular RC concrete;
* layered shells (nonlinear builds): steel layers (law wrapped in
  PlateRebar, hysteresis honoured); concrete layers -> ASDConcrete3D with
  `-Ce/-Cs` and `-Te/-Ts` point lists sampled from the law, clipped onto
  / below the elastic line E*mod (pre-crack stiffness unchanged),
  floored at 1e-3 f'c, zero damage — ASDConcrete3D's own cyclic rule, so
  `hysteresis` is NOT used for layered concrete; ft = 0 laws keep a
  negligible 1e-3 f'c tension branch.

Consumers that do NOT use material curves (unchanged): the 8 rectangular
RC fiber-hinge bars (rho / fy_bar from `hinge_params`, E = 200 GPa — not
tied to a Material); lumped ASCE 41 `auto_m3` hinges and v0.5/v0.6
Steel01 pushover / nonlinear-TH hinge springs (moment-rotation
backbones); axial-only Truss members; links / isolators; every linear
analysis (elastic sections).

### `POST /api/materials/curve`

Body `{"material": <Material dict>}` (or `{"name": <material in the
current model>}`; optional `"n"`, default 80, 8..1000).  Response:

```
{"strain": [...], "stress": [...],     # sorted, ~n+key points, compression + tension, kPa
 "model": "default"|"simple"|"mander"|"park"|"user",
 "hysteresis": str, "opensees": "Concrete01"|"Concrete02"|"Concrete04"|"Steel01"|
               "MultiLinear"|"HystereticSM"|"ElasticMultiLinear",
 "notes": [str, ...]}
```

Computed by the same `UniaxialLaw.stress` the engine materials follow;
null / "default" returns the legacy fiber law (concrete family:
Concrete01(f'c, 2f'c/E, 0.2f'c, 0.006); steels: Steel01(fy, E, 0.01)).
400 on an invalid material / stress_strain / unknown name.

## 5. Display units

```
BuildingModel.display_units: str = "kN-m"   # kN-m | kN-mm | N-mm | tonf-m | kip-ft | kip-in
```

Persistence only (validated, round-tripped); the engine ignores it — the
model stays SI (kN, m, kPa, tonne, degC).

### `GET /api/units` (`skyframe.core.units.units_table`)

```
{"base": {"force": "kN", "length": "m", "stress": "kPa", "mass": "tonne",
          "temperature": "C", "time": "s"},
 "sets": {set: {"force": [label, factor_from_kN],
                "length": [label, factor_from_m],
                "temperature": "C" | "F",
                "labels": {quantity: label}, "factors": {quantity: factor}}},
 "quantities": {quantity: {"force": f, "length": l, "si": si_unit}},
 "temperature": {"C": {"scale": 1, "offset": 0}, "F": {"scale": 1.8, "offset": 32}},
 "thermal_coefficient": {"C": 1, "F": 1/1.8},
 "constants": {"kip_kN": 4.4482216152605, "tonf_kN": 9.80665, "ft_m": 0.3048, "in_m": 0.0254},
 "default": "kN-m"}
```

Display value = SI value x `force_factor^f x length_factor^l`.  Sets:
kN-m (kN 1, m 1, C); kN-mm (kN 1, mm 1000, C); N-mm (N 1000, mm 1000, C);
tonf-m (tonf 1/9.80665, m 1, C); kip-ft (kip 1/4.4482216152605,
ft 1/0.3048, F); kip-in (kip 1/4.4482216152605, in 1/0.0254, F).
Quantities: force, length, displacement, moment, stress, modulus,
area_load, line_load, unit_weight, area, volume, inertia,
section_modulus, mass (kN s^2/m), mass_density, rotational_mass,
acceleration, velocity, translational_stiffness, rotational_stiffness,
line_spring, area_spring, rotation, strain, time.

# Analysis results tables (ETABS "Display > Show Tables > Analysis Results")

Module `skyframe.core.tables` (NEW) — pure POST-PROCESSING ("store and
compute"): every table is computed from an already-solved
`results.to_dict()` payload + the `BuildingModel`; the solver is never re-run.
`POST /api/analyze` payloads are byte-identical (the endpoint only
additionally remembers its payload server-side).

```python
from skyframe.core import tables
tables.list_tables()                       # catalogue (below)
tables.compute_table(key, results, model, context=None, cases=None)
    # -> {key, title, group, columns: [{key,label,quantity}], rows: [{col: v}],
    #     warnings: [str]};  KeyError on an unknown key
tables.engine_context(engine)              # after engine.run(): JSON-safe
    # {masters: {story: tag}, mass: [[tag, dof, m]], story_nodes: {story:
    #  [tags]}, region_trib: {shell uid: meshed tributary area}}
tables.context_from_results(model, results)  # fallback when no engine context
```

The context makes the modal/diaphragm tables exact; without it masters are
re-identified (the unreferenced node at `(plan_center, elevation)` of each
rigid story) and the mass map is rebuilt from the default `"weight"` mass
source (identical to the engine's — tested).  `element_self_mass` models
need the engine context (warning + empty modal mass tables otherwise).

**Result sources.** Case-based tables iterate the static cases
(`case_type` "LinStatic", or "NonStatic" for P-Delta/corotational) and the
ADDITIVE combos ("Combination"); envelope combos and RS/TH results are not
linear states, so derived quantities (drift, equilibrium, story forces) are
not defined for them and they are skipped.  `cases: [names]` restricts the
rows.  Rows are ordered ETABS-style (top story first).

**Quantities** (base unit; the frontend converts): `text`, `id`, `length`
(m), `force` (kN), `moment` (kN*m), `stiffness` (kN/m), `ratio`, `percent`,
`angle` (rad), `time` (s), `frequency` (Hz), `circular_frequency` (rad/s),
`eigenvalue` (rad^2/s^2), `mass` (tonne), `factor` (participation factor).

## Catalogue (group prefix "Analysis Results > ")

| key | title | group | columns (`key`:quantity) |
|-----|-------|-------|--------------------------|
| `joint_displacements` | Joint Displacements | Joint Output > Displacements | `story`:text, `label`:text, `joint`:id, `case`:text, `case_type`:text, `ux`:length, `uy`:length, `uz`:length, `rx`:angle, `ry`:angle, `rz`:angle |
| `joint_drifts` | Joint Drifts | Joint Output > Displacements | `story`:text, `label`:text, `joint`:id, `joint_below`:id, `case`:text, `case_type`:text, `x`:length, `y`:length, `z`:length, `height`:length, `disp_x`:length, `disp_y`:length, `drift_x`:ratio, `drift_y`:ratio |
| `joint_reactions` | Joint Reactions | Joint Output > Reactions | `story`:text, `label`:text, `joint`:id, `case`:text, `case_type`:text, `x`:length, `y`:length, `z`:length, `FX`:force, `FY`:force, `FZ`:force, `MX`:moment, `MY`:moment, `MZ`:moment |
| `story_drifts` | Story Drifts | Structure Output > Story Output | `story`:text, `case`:text, `case_type`:text, `ux`:length, `uy`:length, `drift_x`:ratio, `drift_y`:ratio |
| `story_forces` | Story Forces | Structure Output > Story Output | `story`:text, `case`:text, `case_type`:text, `location`:text, `P`:force, `VX`:force, `VY`:force, `T`:moment, `MX`:moment, `MY`:moment, `n_members`:id, `n_shells`:id |
| `story_stiffness` | Story Stiffness | Structure Output > Story Output | `story`:text, `case`:text, `case_type`:text, `shear_x`:force, `drift_x`:length, `stiff_x`:stiffness, `shear_y`:force, `drift_y`:length, `stiff_y`:stiffness |
| `diaphragm_cm_displacements` | Diaphragm Center Of Mass Displacements | Structure Output > Diaphragm Output | `story`:text, `diaphragm`:text, `case`:text, `case_type`:text, `ux`:length, `uy`:length, `rz`:angle, `x`:length, `y`:length, `z`:length |
| `diaphragm_max_avg_drifts` | Diaphragm Max Over Avg Drifts | Structure Output > Diaphragm Output | `story`:text, `case`:text, `case_type`:text, `direction`:text, `max_disp`:length, `avg_disp`:length, `disp_ratio`:ratio, `max_drift`:ratio, `avg_drift`:ratio, `drift_ratio`:ratio, `label_max`:id |
| `centers_mass_rigidity` | Centers Of Mass And Rigidity | Structure Output > Other Output Items | `story`:text, `diaphragm`:text, `mass`:mass, `cm_x`:length, `cm_y`:length, `cr_x`:length, `cr_y`:length |
| `base_reactions` | Base Reactions | Structure Output > Base Reactions | `case`:text, `case_type`:text, `FX`:force, `FY`:force, `FZ`:force, `MX`:moment, `MY`:moment, `MZ`:moment, `X`:length, `Y`:length, `Z`:length |
| `load_pattern_summary` | Load Pattern Totals And Equilibrium | Structure Output > Base Reactions | `pattern`:text, `type`:text, `self_weight`:ratio, `FX`:force, `FY`:force, `FZ`:force, `case`:text, `react_FX`:force, `react_FY`:force, `react_FZ`:force, `error_pct`:percent |
| `load_case_equilibrium` | Load Case Equilibrium Check | Structure Output > Base Reactions | `case`:text, `case_type`:text, `applied_FX`:force, `applied_FY`:force, `applied_FZ`:force, `react_FX`:force, `react_FY`:force, `react_FZ`:force, `error_pct`:percent |
| `modal_periods` | Modal Periods And Frequencies | Modal Results | `case`:text, `mode`:id, `period`:time, `frequency`:frequency, `circ_freq`:circular_frequency, `eigenvalue`:eigenvalue |
| `modal_mass_ratios` | Modal Participating Mass Ratios | Modal Results | `case`:text, `mode`:id, `period`:time, `UX`:ratio, `UY`:ratio, `UZ`:ratio, `SumUX`:ratio, `SumUY`:ratio, `SumUZ`:ratio, `RX`:ratio, `RY`:ratio, `RZ`:ratio, `SumRX`:ratio, `SumRY`:ratio, `SumRZ`:ratio |
| `modal_participation_factors` | Modal Participation Factors | Modal Results | `case`:text, `mode`:id, `period`:time, `UX`:factor, `UY`:factor, `UZ`:factor, `RX`:factor, `RY`:factor, `RZ`:factor, `modal_mass`:mass, `modal_stiffness`:stiffness |
| `modal_direction_factors` | Modal Direction Factors | Modal Results | `case`:text, `mode`:id, `period`:time, `UX`:ratio, `UY`:ratio, `UZ`:ratio, `RZ`:ratio, `dominant`:text |

## Definitions

* **Joints** = member ends, shell-mesh nodes and supports (coincident hinge /
  panel-zone duplicates collapsed onto the lowest tag; diaphragm masters
  excluded).  `label` = the grid intersection within 1 mm, else "".
  `story` = "Base" at the base elevation, "" off the story levels.
* **joint_drifts**: per story-level joint with a joint DIRECTLY BELOW at the
  same plan position (next lower level, incl. the base):
  `drift = (u - u_below) / (z - z_below)` (signed).  Joints without a joint
  below are omitted.  Cantilever: `drift_x = (P L^3/3EI)/L` exactly.
* **story_stiffness**: ETABS Story Stiffness for cases with a non-zero story
  shear: `shear` = the engine's applied cumulative story shear, `drift` =
  interstory displacement (m), `stiff = |shear/drift|` — the same values as
  the engine's `story_stiffness` block (tested equal).
* **story_forces**: resultant of everything ABOVE a horizontal cut just below
  the floor (`Top`) and just above the floor below (`Bottom`) of each story:
  `P, VX, VY` global (gravity: `P < 0`; a +X load: `VX > 0`), `T, MX, MY`
  about `(plan center, cut z)` (a +X load above gives `MY > 0`).  Integrated
  from the member stations of the FRAME members crossing the cut (local ->
  global with the engine's `_local_axes`).  Shells crossing a cut are
  counted in `n_shells` and EXCLUDED (warning) — documented limitation.
  The Bottom of story 1 equals minus the base reaction (tested).
* **diaphragm_cm_displacements**: per rigid story (diaphragm "D1"):
  rigid-body displacement of the master at the CM from `story_props`
  (`ux = u_m - rz*(y_cm - y_m)`, `uy = v_m + rz*(x_cm - x_m)`).
* **diaphragm_max_avg_drifts**: per rigid story and direction, the two plan
  extreme diaphragm points transverse to the direction (min/max y for X,
  min/max x for Y) — displacement from the rigid-body field and drift
  relative to the level below at the same plan point (rigid-body of the lower
  diaphragm, else the joint below, else the lower story average; the base
  joint or 0 for the first story) over the story height.
  `ratio = max(|d1|,|d2|) / ((|d1|+|d2|)/2)` (ASCE 7 §12.3.2.1); `disp_ratio`
  equals the engine's `irregularity.tors_ratio_*` when the diaphragm points
  span the plan extents.  Rows where both averages are 0 are omitted.
* **centers_mass_rigidity**: `story_props` + the master's lumped mass.
* **base_reactions**: `results.cases[*].base` (moments about the origin) plus
  the resultant location: the central axis `r0 = F x M / |F|^2`, intersected
  with the base plane when `FZ != 0` (=> the reaction centroid X, Y), else
  `r0` (Z = height of a horizontal resultant).  `null` for a pure couple.
* **load_pattern_summary**: per pattern the applied `FX, FY, FZ` summed from
  the load definitions with the engine's rules (gravity member loads skip
  vertical members; `local_y` loads along the member's local y; self-weight
  on every member + shell; meshed shells use the meshed tributary area);
  when a static case applies that pattern ALONE, its base reaction divided by
  the case factor and `error_pct = 100 |applied + reaction| / |applied|`.
* **load_case_equilibrium**: the same check per static case / additive combo
  (combo pattern factors folded linearly).
* **modal_***: from `results.modal` + the mass map. `modal_mass = phi^T M phi`,
  `modal_stiffness = omega^2 * modal_mass`, `Gamma_k = L_k / modal_mass`,
  mass ratio `= (L_k^2/modal_mass) / (r_k^T M r_k)`.  Influence vectors
  `r_k`: unit translation for UX/UY/UZ; RZ = the rotational dof only (the
  engine's definition — UX/UY/RZ ratios and UX/UY factors equal
  `modal.participation[*].ux/uy/rz/gamma_x/gamma_y`, tested); RX/RY = unit
  rotation about the global X/Y axis through (plan center, base elevation),
  translational masses entering with their lever arms (rocking).
  **modal_direction_factors**: share of the mode's kinetic energy
  `sum m phi^2` in UX/UY/UZ/RZ (rows sum to 1); `dominant` = the largest
  ("RZ" = torsional mode).

## API

| Method | Path | Body / Response |
|--------|------|-----------------|
| GET/POST | `/api/tables/list` | `{tables: [catalogue entries]}` |
| POST | `/api/tables/<key>` | `{results?: <analyze dict>, cases?: [names]}` -> `{key, title, group, columns, rows, warnings}`; 404 unknown key, 400 bad `cases` / failure |

Without `results` the server reuses the payload + engine context stored by
the last `POST /api/analyze` when the current model is unchanged (compared
by its serialized dict); otherwise it analyses the model ONCE and stores the
result.  Tests: `tests/test_tables.py` (27 cases).

## Check Model and stability diagnostics

Module `skyframe.core.checks` (ETABS *Analyze > Check Model* parity).
Analysis-only; never mutates the model.

### `POST /api/check`

Body `{model?: <model dict>, tolerance_m?: float in (0, 1] (default 0.001)}`.
`model` is loaded LENIENTLY (`load_model_lenient`: `from_dict` without the
final `validate()`, so every defect is reported instead of only the first)
and does NOT replace the current model. When `model` is omitted, the current
model is checked. Returns 400 on a bad tolerance or an unparseable model.
Python: `check_model(model, tolerance=0.001)`.

Response:

```
{"issues": [{"severity": "error"|"warning"|"info", "code": str,
             "message": str, "objects": [uid/name, ...],
             "location": [x, y, z] | null}, ...],      # errors first
 "summary": {"errors", "warnings", "info": int, "ok": bool (no errors),
             "by_code": {code: count}, "n_joints", "n_frames", "n_shells",
             "n_links", "n_supported_joints": int, "tolerance_m": float,
             "elapsed_s": float}}
```

Joints are the distinct frame/link end points and shell corners. They are
deduplicated exactly as the engine does it (coordinates rounded to 1e-6).
Proximity and intersection searches use a uniform spatial hash (10-story
5x5-bay quick_building, 960 frames: ~0.3 s).

| code | severity | meaning |
|---|---|---|
| `JOINT_COINCIDENT` | warning | distinct joints within `tolerance_m` (not merged by the analysis) |
| `FRAME_ZERO_LENGTH` | error | frame length < `tolerance_m` |
| `FRAME_DUPLICATE` | error | frames joining the same two (tolerance-merged) joints |
| `FRAME_OVERLAP` | error | collinear frames overlapping by > `tolerance_m` |
| `FRAME_INTERSECTION` | warning | frames crossing at interior points without a shared joint |
| `FRAME_JOINT_ON_SPAN` | warning | a frame end lies on another frame's span, and that frame is not divided there (objects: `[ending frame, spanning frame]`) |
| `FRAME_UNCONNECTED` / `SHELL_UNCONNECTED` / `LINK_UNCONNECTED` | error | a single object connected to nothing and unsupported |
| `STRUCTURE_UNSUPPORTED_PART` | error | a group of connected objects with no path to any support |
| `NO_SUPPORTS` | error | no restrained joint and no grounded spring at all |
| `JOINT_UNCONNECTED` | error / warning | a support (error), nodal load (error), spring support or nodal mass (warning) at a point that is not a joint/FE node |
| `STORY_NO_SUPPORT_PATH` | error | no object at the story elevation is connected to a support |
| `STORY_NO_VERTICAL_ELEMENTS` | warning | no column/brace/wall/link reaches down from the story |
| `STORY_EMPTY` | info | the story elevation has no joints |
| `SHELL_CORNER_COUNT` | error | the shell does not have exactly 4 corners |
| `SHELL_SELF_INTERSECTING` | error | bow-tie corner order |
| `SHELL_ZERO_AREA` | error | area <= `tolerance_m` x longest edge, or an edge < `tolerance_m` |
| `SHELL_WARPED` | error | corner 4 is off the plane of corners 1-3 by > 1e-6 m (the model's planarity tolerance, which the analysis enforces) |
| `SHELL_CONCAVE` | error | concave quad (location = reflex corner) |
| `SHELL_ASPECT_RATIO` | warning > 4, error > 10 | shell behavior: aspect ratio of the meshed elements (the mesher's nx/ny); membrane: longest/shortest region edge |
| `SHELL_DUPLICATE` | error | shells with the same corner joints |
| `DUPLICATE_UID` | error | a frame/shell/link uid used twice |
| `FRAME_SECTION_MISSING` / `SHELL_SECTION_MISSING` | error | the object references a missing section |
| `SECTION_MATERIAL_MISSING` | error | a frame or shell section references a missing material |
| `INVALID_DATA` | error | any other `validate()` sub-check failure, reported per object (skipped for objects already flagged with a specific error) |
| `PATTERN_EMPTY` | warning | a pattern with no loads and no self-weight |
| `CASE_NO_PATTERNS` | warning | a static case that applies no pattern |
| `CASE_EMPTY_PATTERN` | warning | a case references an empty pattern |
| `CASE_PATTERN_MISSING` | error | a case (or its `pdelta_gravity`) references a missing pattern |
| `LOAD_TARGET_MISSING` | error | a member/area/thermal load or story force on a missing frame/shell/story |
| `COMBO_EMPTY` | warning | a combination with no cases |
| `COMBO_CASE_MISSING` | error | a combination references a missing case |
| `COMBO_CASE_INVALID` | error | a combination references an RS/TH/staged case or a combo |

Supports: explicit `supports` with any restraint; otherwise the engine's
automatic base (joints at the lowest z). Spring supports, line springs, area
springs and Winkler foundation members also count as supports.

### `POST /api/check/stability`

Body `{model?, max_dofs?: int in [1, 6000] (default 3000)}`.  Python:
`stability_diagnostics(model, max_dofs=3000)`.

How it works:

* The domain is built through the engine's normal elastic `_build()`; no case
  is solved.
* `K` is assembled with `system FullGeneral` and `numberer Plain` (plus the
  Transformation constraint handler when the engine uses it) and extracted
  with `printA`.
* Mechanisms are the eigenvalues of the Jacobi-scaled `D^-1/2 K D^-1/2` that
  are below `1e-12 x` the largest one.
* Mode shapes are expanded to physical joint DOFs. Rigid-diaphragm slave
  joints are computed from their master (`ux = um - rz*dy`,
  `uy = vm + rz*dx`), and masters are not listed. Rotations are weighted by
  the median frame length.
* The null space is orthonormalized, so `unstable_dofs[].participation` (the
  projector diagonal) does not depend on which eigenbasis numpy returns.
* The dense linear algebra runs with one OpenBLAS thread.

```
{"stable": bool|null, "built": bool, "skipped": bool, "n_equations": int,
 "n_mechanisms": int,
 "mechanisms": [{"mode", "scaled_eigenvalue",
                 "dofs": [{"node", "point", "dof": "UX".."RZ",
                           "participation", "objects", "diaphragm"?}]}],
 "unstable_dofs": [<same dof entries>],            # whole null space
 "condition_number": float|null (raw K, null when singular),
 "scaled_condition_number": float|null,
 "scaled_condition_number_nonsingular": float|null,
 "digits_lost": float|null,
 "max_diagonal_ratio": float|null,   # max K_ii / D_ii of LDL^T
 "max_diagonal_ratio_at": {"node", "point", "dof", "objects", "equation"},
 "n_ill_conditioned_equations": int,   # diagonal ratio > 1e8
 "lowest_eigenvalues": [scaled, up to 6], "thresholds": {...},
 "issues": [...same issue shape...], "message": str, "elapsed_s"}
```

Codes:

* `STABILITY_MECHANISM` (error): one per mechanism, up to 10, located at the
  dominant joint/DOF.
* `STABILITY_ILL_CONDITIONED` (warning): diagonal ratio > 1e8 or condition
  number > 1e12. Reported only when there is no mechanism.
* `STABILITY_TOO_LARGE` (info): the estimated number of equations exceeds
  `max_dofs`, so the dense analysis is skipped.
* `STABILITY_BUILD_FAILED` (error): the model is invalid or the build failed.

The diagonal ratio comes from a Cholesky factorization of
`K + 1e-14 max(diag) I`. A singular K therefore still factors, and the
near-zero pivot shows where the problem is.

Tests: `tests/test_checks.py`.

# Joint moments, ground displacement, shell load directions and joint patterns

ETABS Assign > Joint Loads / Shell Loads parity (analysis-only).  Every new
field is optional with a default that reproduces the previous behavior; a
model without them serializes AND solves byte-identically (verified on a
frame + shell + membrane model: `to_dict()` JSON and the full
`run().to_dict()` results are bit-for-bit equal to the pre-feature build).
Bulk logic lives in `skyframe.core.loads_ext`.

## Joint moments — `NodalLoad.mx / my / mz`

kN*m about the GLOBAL axes (right-hand rule), default 0, scaled with the
pattern like `fx/fy/fz`.  JSON: `{"point", "fx", "fy", "fz"}` plus
`"mx"/"my"/"mz"` ONLY when non-zero.

## Ground displacement — `LoadPattern.ground_displacements`

```
"ground_displacements": [            # key omitted when empty
  {"point": [x, y, z], "ux": m, "uy": m, "uz": m,
   "rx": rad, "ry": rad, "rz": rad}  # all default 0, global axes
]
```

* `point` must be an explicit `PointSupport`, a `SpringSupport`, or (only
  when the model has no explicit supports) lie at the automatic base level
  (lowest member/shell z) — otherwise `validate()` raises
  `"... is not at a support or spring point"`.  The engine re-checks that
  the node is a support.
* Imposed with `ops.sp(node, dof, value*scale)` inside the active load
  pattern on every **restrained** dof; for a **point spring** the value is
  imposed on the spring's grounded node (the spring's ground end moves) for
  the sprung dofs, and the spring reaction becomes `-k*(u - u_ground)`.
  A non-zero value on a FREE dof is ignored with a `UserWarning`
  ("... is on a free dof; ignored").
* Any pattern carrying ground displacements switches the model's
  constraint handler to `Transformation` (OpenSees' Plain handler rejects
  non-homogeneous sp on fixed dofs); models without them are untouched.
* Staged construction: ground displacements of a staged pattern act in
  stage 1.  The takedown `balance_ok` compares reactions with APPLIED
  gravity only, so self-equilibrated settlement reactions are expected
  to show there.

## Shell (area) loads — `AreaLoad.direction / projected / joint_pattern`

```
{"region_uid": "S1", "q": kPa,
 "direction": "gravity"|"global_x"|"global_y"|"global_z"
              |"local_1"|"local_2"|"local_3",           # omitted if gravity
 "projected": true,                                     # omitted if false
 "joint_pattern": {"type": "linear", "a": .., "b": .., "c": .., "d": ..,
                   "zero_negative": bool, "zero_positive": bool}}  # or absent
```

* `gravity` keeps the old meaning (positive = global -Z).  `global_*`:
  positive along +axis.  `local_*`: shell load axes from the corner
  ordering — `e3 = unit((c1-c0) x (c3-c0))` (same normal as the v0.23 wind
  walls), `e1 = unit(Z x e3)` (horizontal; global +X for a horizontal
  region), `e2 = e3 x e1` (up-slope / up the wall).  `local_3` is the
  normal pressure.
* `projected` (gravity / global directions only): q is per area projected
  normal to the load direction — intensity scaled by `|n . d|` (a gravity
  load on a sloped roof totals `q * plan area`).
* `joint_pattern`: the load intensity is `q * p(x, y, z)` with
  `p = a x + b y + c z + d` (ETABS joint pattern, e.g. hydrostatic
  `q = gamma, c = -1, d = z_surface`); `zero_negative` / `zero_positive`
  clip p to one sign (not both).
* Distribution (any non-default load, shell behavior): CONSISTENT nodal
  forces `F_i = int N_i q p(x) [|n.d|] d dA` over each kept mesh quad
  (bilinear shape functions, 3x3 Gauss) — exact resultant AND first moment
  for a linear pattern; a clipped pattern is exact when the zero line is a
  mesh line.  The default load keeps the quarter-area tributary path.
* Membrane slabs accept only uniform `gravity` loads (`projected` allowed,
  scales q by |n_z|); other directions / joint patterns raise.
* Mass source / story gravity / takedown use the downward component of
  the load (`loads_ext.area_load_weight`; exactly `q*net_area` for
  defaults).

## Frame concentrated moments — `MemberLoad(kind="moment")`

`w` = couple (kN*m) at fraction `a` (0..1); `direction` in
`local_x|local_y|local_z` (aliases `local_1|2|3`) or `global_x|y|z` — the
axis the couple acts about.  `w2`/`b` unused.  On a segment node it is a
joint moment; inside a segment it is a `("moment", (mx, my, mz), x0)` span
record through the exact condensed fixed-end path (Hermite-derivative
consistent load, release condensation), and the statics stations
(moment jump of the couple) and the closed-form deflection stations honor
it.  Couples carry no net force (skipped by gravity sums and buckling).
Axial-only (truss) members skip it like other member loads.

Tests: `tests/test_loads_ext.py` (26 cases): cantilever tip couple
`theta = ML/EI`, `delta = ML^2/2EI` (major and minor axes, torsion);
member couple on cantilever / fixed-fixed (`R = 1.5M/L`, `M/4` end
moments) / released end (`R = 9M/8L`); 2-span settlement
`R_B = -6EI D/L^3`, `M_B = 3EI D/L^2`, end settlement, rotation settlement
`4EI th/L`, spring ground-end settlement `R = D/(L^3/6EI + 1/k)`;
hydrostatic wall resultant `gamma h^2 b/2` at `h/3` (with and without
clipping); local_3 == global components on an inclined roof and a wall;
global_x reaction `p*A`; projected gravity `q*plan area`; defaults
serialize byte-identically; full model round trip.

# Direct-integration options + energy (ETABS TH parity, analysis-only)

Pure option logic: `skyframe/core/di_options.py`; OpenSees runtime +
energy: `skyframe/engine/thoptions.py`.  Applies to
`OpenSeesEngine.run_time_history` (direct integration) only; FNA
(`run_fna`) ignores these fields.

## New optional `TimeHistoryCase` fields (all round-tripped)

All default to `None` / `False`; with every one unset the engine runs the
unchanged legacy code path (Newmark 1/2-1/4, legacy Rayleigh/modal,
`NormDispIncr 1e-8, 25` + NewtonLineSearch retry) — bit-identical
results, and `to_dict()` omits the keys (pre-existing files byte-equal).
`add_th_case(..., integration=, di_damping=, solver=, energy=)`.

* `integration`: `{"method": "newmark"|"hht"|"wilson"|"central_difference",
  "gamma": 0.5, "beta": 0.25, "alpha": 0.0, "theta": 1.4}`
  * newmark -> `integrator Newmark gamma beta`; gamma in [0.5, 1],
    beta in (0, 0.5].
  * hht -> `integrator HHT (1+alpha) gamma beta`; alpha in [-1/3, 0]
    (HHT sign convention); omitted gamma/beta default to
    `1/2 - alpha`, `(1 - alpha)^2/4`.
  * wilson -> `integrator WilsonTheta theta` (available in this
    openseespy build); theta in [1, 2] (>= 1.37 unconditionally stable).
  * central_difference -> `integrator CentralDifference` with the Linear
    algorithm and no substepping.  Requires mass on EVERY free equation
    (checked with `systemSize`/`nodeDOFs`; else ValueError naming the
    massless count — typical frames with massless rotations are rejected).
  * Conditionally stable schemes (`2 beta < gamma`, incl. central
    difference) are checked BEFORE the transient: `T_min` = shortest
    period of the built domain (dense eigen of all massed dofs);
    `dt >= T_min * Omega_crit/(2 pi)` with `Omega_crit = 1/sqrt(gamma/2 -
    beta)` (central difference: `T_min/pi`) raises `ValueError` quoting
    the computed limit.
* `di_damping` (replaces the legacy Rayleigh fit when set; cannot be
  combined with `damping_model "modal"`, which stays the modal option):
  * `{"type": "rayleigh_by_periods", "T1", "xi1", "T2", "xi2",
    "stiffness"}` -> `a1 = 2(xi2 w2 - xi1 w1)/(w2^2 - w1^2)`,
    `a0 = 2 w1 w2 (xi1 w2 - xi2 w1)/(w2^2 - w1^2)`; exact ratios at T1/T2;
    negative coefficients rejected.
  * `{"type": "rayleigh_coefficients", "mass_coeff", "stiffness_coeff",
    "stiffness"}`.
  * `stiffness` basis `"initial"|"current"|"committed"` ->
    `ops.rayleigh(a0,a1,0,0)` / `(a0,0,a1,0)` / `(a0,0,0,a1)`; default
    `"current"` for linear and `"committed"` for hinge/device cases (the
    legacy choices).
* `solver`: `{"max_iterations": 25, "tolerance": 1e-8, "test":
  "NormDispIncr"|"NormUnbalance"|"EnergyIncr", "max_halvings": 0}`.
  A record step that fails (after the NewtonLineSearch retry) is re-solved
  as two half steps, recursively, down to `dt/2**max_halvings` (<= 12).
  Unknown keys rejected.
* `energy: bool` — record the energy time series.

## Results (`THResults.extra`, merged into `to_dict()` only when set)

```
th_cases[case]["direct_integration"] = {
  "integration": {normalized}, "solver": {normalized},
  "substepped_steps": [{"step": k (1-based), "t": s, "halvings": depth}],
  "damping"?: {"type", "stiffness", "a0", "a1", (T1, xi1, T2, xi2),
               "ratio_at_modes": [xi at each initial modal period]},
  "dt_limit"?: s, "T_min"?: s }          # conditionally stable schemes
th_cases[case]["energy"] = {             # cumulative, kN*m
  "t", "input", "kinetic", "strain", "damping", "hysteretic",
  "error", "error_normalized": [one per record step],
  "max_abs_error_normalized", "normalization", "units", "method" }
```

The results key is the existing `th_cases` (ETABS "time_history[case]").

## Energy method (relative frame; nodal work over all domain nodes)

`P = P_grav - M iota ag(t)` (held gravity-stage loads + ground inertia);
`f_S = R0 + unbal - alphaM M v` from `ops.reactions()` (`unbal` read AFTER
each reactions call, which re-applies loads at the current time; the
`alphaM M v` term is an OpenSees `Node::resetReactionForce` quirk);
applied Rayleigh damping `f_D = R1 + unbal - M a - f_S` from
`ops.reactions('-dynamic')` (elements with Rayleigh off, e.g. zeroLength
hinges, contribute nothing — as in the solve); modal damping = the
equilibrium residual `P - M a - f_S`.  Trapezoidal increments
`0.5 (f_k + f_k+1) . du` (exactly consistent with Newmark average
acceleration); kinetic `0.5 v'Mv`; hysteretic = nonlinear-element
(zeroLength hinges, device links) basic work minus `F^2/(2 k0)` (initial
material tangent); strain = resisting-force work - hysteretic (includes
the work of held gravity forces); error = input - (kinetic + strain +
damping + hysteretic), normalised by the run maximum energy.  Central
difference uses its exact discrete balance (central increments
`(u_n+1 - u_n-1)/2`, leapfrog kinetic, damping at the central velocity —
OpenSees reports a different nodal velocity; samples at t_n).  Constraint
forces (diaphragms, equalDOF) do no net work.  Limitations: fiber hinges
are not split (their work counts as strain); for HHT / Wilson /
linear-acceleration Newmark the error is the scheme's numerical
dissipation + O(dt^2).

Validation (`tests/test_th_options.py`, 45 cases): every integrator vs an
independent numpy implementation (1e-7 of peak); Newmark avg-accel
|lambda| = 1 and `w_bar dt = 2 atan(w dt/2)`; HHT alpha=-0.1 and Wilson
spectral radius/frequency == amplification-matrix eigenvalues (1e-6);
stability limits; Rayleigh-by-periods exact ratios + engine log
decrement; energy balance to round-off for Newmark/central difference,
`E_D == int c v^2 dt` (Rayleigh and modal) within 1 %, hysteretic energy
on a yielding hinge; substepping recovery; byte-identical defaults;
model/API round-trip.

# Frequency-domain analysis (steady-state, PSD)

ETABS "Steady State" and "Power Spectral Density" load cases (analysis
only).  Model side: `skyframe/core/frequency_cases.py`; solver:
`skyframe/engine/frequency.py`.  Units: kN, m, tonne, s; frequencies Hz.

## Model fields (`BuildingModel`, all default `{}`)

Each key is emitted by `to_dict()` ONLY when non-empty, so models without
frequency-domain data serialise byte-identically; missing keys load as
`{}`.  Validated by `BuildingModel.validate()` (ValueError).

```json
"frequency_functions": {
  "<name>": {"name": "<name>", "kind": "steady_state" | "psd",
             "points": [[f_hz, value], ...]}
},
"steady_state_cases": {"<name>": <FrequencyCase>},
"psd_cases":          {"<name>": <FrequencyCase>}
```

* Function: >= 2 points, f >= 0 strictly increasing, finite; PSD values
  >= 0.  LINEAR interpolation in f, ZERO outside `[f_first, f_last]`.
  `steady_state`: load amplitude multiplier.  `psd`: ONE-SIDED PSD per Hz
  of the load multiplier, (load)^2/Hz.

```json
<FrequencyCase> = {
  "name": "<name>",
  "loads": [{"pattern": "<pattern name>" | "accel",
             "direction": "" | "UX" | "UY" | "UZ",   // accel only
             "scale": 1.0,
             "function": "<frequency function>" | "",  // "" = constant 1
             "phase_deg": 0.0}],
  "freq_start_hz": 0.0, "freq_end_hz": 10.0,
  "n_freq": 101,              // linspace points (0 = none, 1 = start only)
  "frequencies": [],          // extra explicit frequencies (Hz)
  "modal_refine": true,       // add f_i*(1 + k*zeta), k in 0, +-{0.1..13}
  "method": "modal" | "direct",
  "num_modes": 0,             // modal method; 0 = model.num_modes
  "damping": 0.05,            // (0, 1)
  "damping_type": "modal" | "hysteretic",
  "output_points": [[x, y, z], ...]
}
```

* Pattern load: the pattern's static spatial distribution x
  `scale * fn(f)`.  `"accel"`: uniform ground acceleration
  `scale * fn(f)` in m/s^2 (for g-based functions use
  `scale = 9.80665`); responses are RELATIVE to the ground.
* A PSD load MUST name a `psd` function; a steady-state load may name a
  `steady_state` function or `""` (constant 1).  Function kind must match.
* Grid = linspace U `frequencies` U modal refinement points inside
  `[freq_start_hz, freq_end_hz]`, sorted, de-duplicated, <= 20000 points.
* `damping_type`: `"modal"` = viscous modal ratio,
  `H_i = 1/(w_i^2 - w^2 + 2 i zeta w_i w)`; `"hysteretic"` = complex
  stiffness `K(1 + 2 i zeta)`, `H_i = 1/(w_i^2 (1 + 2 i zeta) - w^2)`.
* `method`: `"modal"` = modal superposition over `num_modes` modes;
  `"direct"` = ALL eigenmodes + the static residual-flexibility
  correction (exactly the complex direct solution of the condensed
  system; massless DOFs respond statically, scaled by `1/(1+2 i zeta)`
  for hysteretic damping).  Capped at 400 massed DOFs.
* Python helpers: `add_frequency_function(model, name, kind, points)`,
  `add_steady_state_case(model, name, loads, **fields)`,
  `add_psd_case(model, name, loads, **fields)`.

## Method

Eigen modes of the standard elastic build (rigid diaphragms, springs,
shells, links respected), mass-normalised (MGS against the diagonal mass
map).  Per mode one static solve under `w_i^2 M phi_i` gives the mode's
response field (joint displacements, ELASTIC support reactions incl.
spring reactions, base totals, story ux/uy/drifts).  Modal load factor:
pattern `p_ij = w_i^2 phi_i^T M u_j` (`u_j` = static pattern
displacement, = `phi_i^T F_j` exactly); accel `p_ij = -phi_i^T M r_d`.
Response `Z(f) = sum_j a_j(f) [sum_i p_ij H_i Phi_i + h_res R_j]`.
Reactions exclude the damping share (same as TH base series).

* Steady state: `a_j = scale_j fn_j(f) e^{i phase_j}`; amplitude `|Z|`,
  phase `atan2(Im Z, Re Z)` in degrees — a response LAGGING the load has a
  NEGATIVE phase (SDOF viscous: `-atan2(2 zeta r, 1 - r^2)`).
* PSD — CORRELATION ASSUMPTION: all loads of one case are FULLY
  CORRELATED (one underlying process): `a_j = scale_j sqrt(S_j(f))
  e^{i phase_j}`.  Response PSD `|Z|^2` (contains every modal cross term,
  the exact CQC-style double sum), RMS `= sqrt(trapz(|Z|^2, f))` over the
  grid (area outside `[freq_start, freq_end]` is not counted).  For
  uncorrelated inputs use one PSD case each and SRSS the RMS values.
  Crandall check: SDOF + white base acceleration W0 ((m/s^2)^2/Hz,
  one-sided) -> `sigma_u^2 = W0 / (8 zeta w^3)` (= `pi G0/(4 zeta w^3)`
  with G0 = W0/2pi per rad/s).

## Results

`/api/analyze` adds `"steady_state": {case: SS}` and `"psd": {case: PSD}`
ONLY when the model has such cases (otherwise the results dict is
byte-identical).  Node tags are strings; `nf = len(frequencies_hz)`;
`[6]` = `[ux, uy, uz, rx, ry, rz]` (reactions `[FX..MZ]`).

```json
SS = {
  "case": "<name>", "type": "steady_state", "method": "modal" | "direct",
  "damping": {"type": "modal" | "hysteretic", "ratio": 0.05},
  "modes_used": n, "modal_frequencies_hz": [n],
  "frequencies_hz": [nf],
  "node_disp": {"<tag>": {"amp": [nf][6], "phase_deg": [nf][6]}},
      // output_points joints; EVERY joint when output_points is empty
  "base":  {"FX".."MZ": {"amp": [nf], "phase_deg": [nf]}},
  "story": {"<story>": {"ux"|"uy"|"drift_x"|"drift_y":
                          {"amp": [nf], "phase_deg": [nf]}}},
  "peaks": {
    "node_disp":         {"<tag>": [6]},   // max |Z| over f, every joint
    "node_disp_freq_hz": {"<tag>": [6]},   // f at that peak
    "reactions":         {"<support tag>": [6]},
    "base":              {"FX".."MZ": peak},
    "base_freq_hz":      {"FX".."MZ": f},
    "story":             {"<story>": {"ux","uy","drift_x","drift_y"}}
  },
  "warnings": []
}

PSD = {
  "case", "type": "psd", "method", "damping", "modes_used",
  "modal_frequencies_hz", "frequencies_hz",      // as SS
  "correlation": "full",
  "rms": {
    "node_disp": {"<tag>": [6]},                 // every joint
    "reactions": {"<support tag>": [6]},
    "base":      {"FX".."MZ": rms},
    "story":     {"<story>": {"ux","uy","drift_x","drift_y"}}
  },
  "psd": {                                       // response PSD curves
    "node_disp": {"<tag>": [nf][6]},             // output_points only
    "base":      {"FX".."MZ": [nf]},
    "story":     {"<story>": {"ux"|"uy"|"drift_x"|"drift_y": [nf]}}
  },
  "warnings": []
}
```

Engine: `OpenSeesEngine.run_steady_state(name) -> SS`,
`OpenSeesEngine.run_psd(name) -> PSD` (cached per engine).

## API

* `POST /api/analyze/steady_state` — body `{"case": "<name>"}` -> `SS`.
* `POST /api/analyze/psd` — body `{"case": "<name>"}` -> `PSD`.
* 400 on a missing/unknown case name, a model without dynamic modes, or
  OpenSeesPy unavailable.

Tests: `tests/test_frequency.py` — SDOF amplitude/phase sweep (1e-9),
resonance peak at `r = sqrt(1-2 zeta^2)`, hysteretic, ground
acceleration, base/story outputs; 2-DOF vs hand modal superposition and
numpy complex direct solves (viscous + hysteretic), truncation, residual
on massless DOFs, direct f=0 == static case on a rigid-diaphragm
building; PSD Crandall (fine grid 4e-7, refined coarse grid < 2%), force
PSD, response-curve exactness, g^2/Hz units, full-correlation cross
terms; linearity/phase/cancellation, zero loads, function interpolation,
grid; round trip, back-compat byte identity, validation, API.

## Pushover load distribution and control (`PushoverCase`, ETABS Nonlinear Static parity)

New optional `PushoverCase` fields (appended; all round-tripped through
`to_dict`/`from_dict`, emitted by `to_dict` ONLY when they differ from the
default, so pre-feature model files serialize byte-identically; every
default reproduces the pre-feature unit roof push bit-identically —
verified list-for-list on frame/asce41/P-Delta/no-diaphragm pushovers):

| field | default | meaning |
|---|---|---|
| `load_distribution` | `"roof_point"` | `roof_point` \| `pattern` \| `mode` \| `uniform_accel` \| `triangular` |
| `pattern` | `None` | load pattern pushed by `pattern` (required there) |
| `mode_number` | `None` | `mode`: 1-based mode; `None` = the mode with the largest effective modal-mass ratio in `direction` (ASCE 41 "first mode") |
| `k` | `None` | `triangular` exponent; `None` = ASCE 7-16 §12.8.3 k(T) (1 for T<=0.5 s, 2 for T>=2.5 s, linear between; T = dominant-mode period in `direction`) |
| `control_story` | `None` | monitored joint = that story's diaphragm master (else its lowest-tag node) |
| `control_point` | `None` | monitored joint = the structural node at `[x, y, z]` (exclusive with `control_story`) |
| `control_dof` | `None` | `"UX"` \| `"UY"`; `None` = the push `direction` |
| `target_disp` | `None` | target control displacement (m, non-zero, signed); `None` = `target_drift * H_ctrl` |
| `control_mode` | `"displacement_control"` | or `"load_control"` (LoadControl, `target_load/steps` per step) |
| `target_load` | `1.0` | `load_control` final load factor lambda |
| `start_from` | `None` | name of a static `LoadCase`; its `patterns` become the existing gravity stage (applied, held with `loadConst -time 0`) — exclusive with `gravity` |

**Distributions** (forces only in the push `direction`;
`skyframe/engine/pushover_distribution.py`).  `mode` / `uniform_accel` /
`triangular` act per massed node of the elastic build (masses on
restrained DOFs excluded): `f = m*phi` (phi from the engine's own elastic
eigen solve), `f = m`, `f = m*h^k` (h = node elevation above the lowest
structural node — ASCE 7 Eq. 12.8-12 C_vx realised node-by-node, i.e. a
story force split over its nodes by mass), normalised so `sum f = 1`;
base shear = lambda * `reference_base_shear` (= sum f = 1).  `pattern`
pushes the pattern at scale lambda; `reference_base_shear` = the
pattern's net applied force in the push direction, measured as minus the
summed support (+ spring) reactions of a linear elastic solve of the
pattern, so base shear = lambda * reference_base_shear exactly (statics).
`roof_point` is the pre-feature unit force at the roof control node
(always the roof, even with another monitored joint).

**Control.**  H_ctrl = the monitored node's elevation (roof elevation for
the default).  `roof_disp` / `roof_drift` are the MONITORED joint's
displacement past the gravity state / that over H_ctrl.  Displacement
control on a rigid-diaphragm SLAVE joint drives its story master (a slave
has no free equation under Transformation); the joint's own displacement
is still recorded.  `start_from` uses only the static case's pattern
factors — the pushover's own `geometric` governs the gravity stage.

**Results** (`PushoverResults.control` / `.distribution`; additive keys of
`to_dict()`, always present; every pre-feature key unchanged):

```json
"capacity_curve": {"node": 45, "dof": "UX", "mode": "displacement_control",
                   "height": 9.6, "start_from": null,
                   "disp": [...], "base_shear": [...]},
"distribution": {"type": "mode",
                 "story_forces": {"Story1": 0.634, "Story2": 0.366},
                 "normalization": "sum of applied push forces = 1 (base shear = lambda * reference_base_shear)",
                 "reference_base_shear": 1.0,
                 "params": {"mode_number": 2, "period": 0.0799, "mass_ratio": 0.995}}
```

`story_forces` = applied push force per story normalised by
`reference_base_shear` (nodes off story planes are loaded but not listed;
for `pattern` only story forces + nodal loads enter the shape).
`params`: `mode` {mode_number, period, mass_ratio}; `triangular` {k[,
period, mode_number when k is automatic]}; `pattern` {pattern};
`roof_point` / `uniform_accel` {}.

Validation (`_validate_pushover_distribution`, ValueError): unknown
distribution / control_mode / control_dof, missing/unknown pattern,
mode_number < 1, k <= 0, unknown control_story, control_story +
control_point, malformed control_point, zero/non-finite target_disp or
target_load, unknown start_from, start_from + gravity.
`add_pushover_case(..., **opts)` takes the new fields as keywords
(unknown keyword -> TypeError).  The API needs no change: `POST /api/model`
round-trips the fields via `from_dict`, and `/api/analyze` pushover blocks
carry the new keys.

Tests: `tests/test_pushover_distribution.py` (37 cases) — elastic 2-story
shear building: initial stiffness = hand 1/sum(V_i/k_i) for roof_point,
uniform_accel, triangular (k = 1, 2) and mode (1e-3); uniform ∝ masses;
triangular = ASCE 7 C_vx (k = 1, 2) and automatic k(T); mode = m*phi of
the engine's own eigen solve (1e-8) and of the hand 2-DOF eig (1e-3);
pattern = the linear static case at the matching lambda (1e-6); load
control; monitored story / slave point / target_disp; start_from ==
gravity dict; default == explicit roof_point; model round-trip;
validation.

# Modal combination methods + load participation (v1.13, analysis-only)

Bulk in `skyframe/engine/modalcombo.py`; the engine carries two small hooks
(`run_response_spectrum` dispatch, `run()` load-participation call).

## `ResponseSpectrumCase` (`skyframe.core.model`)

| field | default | meaning |
|---|---|---|
| `combo_method` | `"CQC"` | one of `RS_COMBO_METHODS = CQC, SRSS, ABS, GMC, NRC10, DSC` |
| `gmc_f1` / `gmc_f2` | `1.0` / `33.0` Hz | Gupta rigid frequencies, `0 < f1 < f2` (GMC and `rigid_response`) |
| `dsc_td` | `20.0` s | Rosenblueth strong-motion duration, `> 0` (DSC) |
| `rigid_response` | `false` | periodic + rigid split for any method |
| `include_missing_mass` | `false` | residual-mass static correction as a rigid mode |

`add_rs_case(...)` accepts the same keyword arguments.  The new keys are
written by `to_dict` only when they differ from their defaults, so legacy
model JSON stays byte-identical; `from_dict` reads them with the defaults
above.

## Combination rules (signed modal values `r_i`, `w_i`, `f_i = w_i/2pi`, damping `z`)

* `CQC`  `sqrt(sum rho_ij r_i r_j)`, Der Kiureghian `rho` (b = w_i/w_j).
* `SRSS` `sqrt(sum r_i^2)`.
* `ABS`  `sum |r_i|`.
* `NRC10` `sqrt(sum r_i^2 + 2 sum_{i<j close} |r_i r_j|)`, close when
  `|f_j - f_i| <= 0.10 min(f_i, f_j)` (Reg. Guide 1.92).
* `DSC`  `sqrt(sum eps_ij r_i r_j)`,
  `eps_ij = 1/(1 + ((w'_i - w'_j)/(z'_i w_i + z'_j w_j))^2)`,
  `w' = w sqrt(1-z^2)`, `z' = z + 2/(td w)`.
* `GMC` (Gupta) `alpha_i = ln(f_i/f1)/ln(f2/f1)` clamped to [0, 1];
  rigid `R_r = sum alpha_i r_i` (algebraic), periodic
  `sqrt(1-alpha_i^2) r_i` combined by CQC -> `R_p`;
  `R = sqrt(R_r^2 + R_p^2)`.
* `rigid_response = true` applies the same split to any method (periodic
  part combined by that method).
* `include_missing_mass = true`: residual load
  `f = Sa(T=0) g (M iota - sum_i Gamma_i M phi_i)` (all massed DOFs, ZPA =
  spectrum at T = 0) is solved statically and added ALGEBRAICALLY to `R_r`
  (rigid mode); then `R = sqrt(R_r^2 + R_p^2)`.  With every mode computed
  the residual vanishes.

**Defaults are byte-identical:** `CQC`/`SRSS` with neither option take the
unchanged legacy `_combine_rsa` path (`modalcombo.is_legacy`).  All
results remain positive envelopes in the standard `CaseResults` shape, so
the v0.10 directional combinations (`rs_combos`, `run_rs_directional`,
`100_30` / `SRSS`) compose with every method unchanged.

## Modal load participation (on demand)

Computing the ratios costs 3 + n_patterns extra linear solves, so a plain
`POST /api/analyze` / `run()` does **not** include them.  Get them with:

* `POST /api/analyze/load_participation` (no body) -> the object below for
  the current model (400 on error); or
* `OpenSeesEngine.run_load_participation()` (same object), or
  `run(load_participation=True)`, which adds it as
  `results["modal"]["load_participation"]` (existing keys untouched).

```json
{"acceleration": {"UX": {"static": 100.0, "dynamic": 100.0},
                  "UY": {"static": 0.0,   "dynamic": 0.0},
                  "UZ": {"static": 0.0,   "dynamic": 0.0}},
 "patterns":     {"<pattern name>": {"static": 96.43, "dynamic": 100.0}}}
```

Percent, ETABS "Modal Load Participation Ratios" (Wilson), for the modes
of `run()`'s modal analysis:

* static  `= sum_i (phi_i^T r)^2 / (w_i^2 m_i) / (r^T K^-1 r)`
* dynamic `= sum_i (phi_i^T r_m)^2 / m_i / (r_m^T M^-1 r_m)`

`m_i = phi_i^T M phi_i`.  Accelerations: `r = M iota` (UX/UY/UZ on mass
dofs 1/2/3); dynamic == cumulative modal mass ratio.  Patterns (unit
scale): `r` = the equivalent nodal load vector OpenSees assembles (nodal
loads minus frame fixed-end forces), `r^T K^-1 r = r . u_r`,
`phi_i^T r = w_i^2 phi_i^T M u_r`; `r_m` = `r` on massed DOFs with
rigid-diaphragm slave loads condensed onto the master (ux, uy, rz).  A
direction with no mass (or a zero pattern) reports 0; a pattern whose
solve fails is omitted.  The key is absent when the model has no modes.
The solves reuse the standard elastic domain (v0.26 reuse token).

Tests: `tests/test_modalcombo.py` — hand-computed CQC/SRSS/ABS/NRC10/DSC/
GMC/rigid-split numbers, engine exactness on the 2-mass cantilever,
missing mass restoring the full-mass static base shear `(m1+m2) Sa g`
under rigid excitation, directional composition, byte-identical legacy
CQC, load participation (100% with all modes, closed-form truncated
ratio, cantilever UDL 27/28 = 96.43% static, diaphragm condensation).

## Frame shear deformation and full property modifiers; shell membrane/bending modifiers

Analysis-only.  Every new field is appended to its dataclass, round-trips
through `to_dict`/`from_dict` (absent keys load as the defaults), and the
defaults reproduce the previous results bit-for-bit (pinned by tests that
compare full result dicts).  Solver-agnostic math: `skyframe.core.modifiers`;
shell OpenSees wiring: `skyframe.engine.shell_modifiers`.

### `FrameSection` (new fields)

```jsonc
"sections": {"<name>": { ...existing...,
  "As2": null,                 // m^2 shear area, local 2 (V2, major-axis bending); null = auto/none
  "As3": null,                 // m^2 shear area, local 3 (V3, minor-axis bending)
  "shear_deformation": false,  // true = auto-fill unset As2/As3 from the shape
  "mod_As2": 1.0, "mod_As3": 1.0,   // > 0, scale the shear areas
  "mod_mass": 1.0,             // >= 0, element self-MASS multiplier
  "mod_weight": 1.0            // >= 0, self-WEIGHT load multiplier
}}
```

* **Enabling.** Shear deformation is ON when `shear_deformation` is true OR
  `As2`/`As3` is set.  Otherwise the member stays the Euler-Bernoulli
  `elasticBeamColumn` (the legacy path).
* **Auto shear areas** (only for an enabled section, only for unset areas):
  an AISC library W-shape (matched by section name) gives `As2 = d*tw` (web)
  and `As3 = 5/3*bf*tf` (5/6 of both flanges), using the AISC 15th ed.
  Table 1-1 `tw`/`tf` values held in `skyframe.core.modifiers`.  A section
  with drawing `b, h > 0` gives `5/6*A` in both directions (rectangle rule;
  Section Designer mirrors use their bbox `b/h`, so they get this rule too).
  A direction that cannot be resolved is treated as shear-RIGID
  (`Av = 1e10*A`).  If neither direction resolves, the member falls back to
  Euler with a warning.
* **Element.** An enabled member becomes OpenSees `ElasticTimoshenkoBeam`
  `(E, G, A*mod_A, J*mod_J, I22*mod_I22, I33*mod_I33, Avy, Avz)` with
  `Avy = As2*mod_As2` and `Avz = As3*mod_As3`.  The shear areas are used
  directly, so any shear coefficient is already inside `As`.
* **Euler fallback** (`UserWarning` naming the reason and the members).
  Each case below was probed on the shipped openseespy:
  * moment releases (`releases` non-empty): the element has no `-release`;
  * `PDelta` / `Corotational` builds (P-Delta / large-displacement cases,
    and pushovers that use them): the element ignores geometric stiffness
    (probed: a compressed member gives the same tip deflection under
    Linear and PDelta), so the whole build uses Euler for these members;
  * no shear area resolvable.
  Axial-only (Truss) and fiber-PMM hinge members keep their own element
  types, unchanged.
* **What works on Timoshenko members:**
  * rigid end offsets (the arms stay stiff `elasticBeamColumn`);
  * shell-edge splits, pushover hinge duplicates and panel zones;
  * modal, response-spectrum and time-history analysis.
* **Member loads.**
  * Full-span UDL uses native `beamUniform` (probed exact, including the
    axial `wx`).
  * `beamPoint` is rejected by ElasticTimoshenkoBeam, so span point loads,
    partial UDLs and trapezoids take the exact fixed-end-force path.  This
    uses Timoshenko interdependent shape functions with
    `phi = 12EI/(G*Av*L_seg^2)` per bending plane (exact by Betti's
    theorem).
* **Recovery.**
  * Station forces are unchanged (pure statics).
  * `member_deflections` adds the exact shear term
    `-(Mb(x) - Mb(0))/(G*Av)` (`v'' = Mb/EI - Mb''/(G Av)`).
  * `run_virtual_work` adds `V2*v2/(G*Avy) + V3*v3/(G*Avz)`, so the
    unit-load identity still holds.
* **Not covered:** `skyframe.core.buckling` / `ritz` (numpy Euler
  stiffness) ignore shear deformation.  `assemble_elastic_stiffness`
  appends a warning listing the affected members.

### Mass / weight modifiers (frame `mod_mass`/`mod_weight`, shell `mass`/`weight`)

* **`mod_weight` / shell `weight`** scale that element's self-weight load.
  This covers:
  * the engine self-weight member/area loads;
  * self-weight totals and centroids;
  * the weight-derived story mass in `compute_story_masses`;
  * `story_gravity_loads` (notional loads);
  * the buckling reference gravity.
* **`mod_mass` / shell `mass`** scale that element's self-MASS in
  `mass_source_mode == "element_self_mass"` (`mass_per_volume * volume *
  mod`).  In the default `"weight"` mode, story mass comes from the
  (weight-modified) loads.  There is no element self-mass to scale, so a
  non-1 mass modifier there raises a `UserWarning` (ETABS semantics: the
  mass modifier acts on element self mass).  It is never silently ignored.

### `ShellSection` (new fields; element local axes 1 = x, 2 = y, 3 = normal)

```jsonc
"shell_sections": {"<name>": { ...existing...,
  "f11": 1.0, "f22": 1.0, "f12": 1.0,   // membrane: N11, N22, N12 stiffness
  "m11": 1.0, "m22": 1.0, "m12": 1.0,   // bending:  M11, M22, M12 stiffness
  "v13": 1.0, "v23": 1.0,               // transverse shear: V13 (Vxz), V23 (Vyz)
  "mass": 1.0, "weight": 1.0            // >= 0 (see above); stiffness factors > 0
}}
```

Target resultant stiffness, applied after `mod` and any cracked-slab scale
(`E' = E*mod*scale`, `Q` = isotropic plane-stress matrix of `E', nu`):

* membrane `A = h * Q~(f11, f22, f12)`;
* bending `B = h^3/12 * Q~(m11, m22, m12)`;
* shear `S13 = v13 * 5/6*G'*h`, `S23 = v23 * 5/6*G'*h`.

`Q~` scales `Q11` by `k11`, `Q22` by `k22`, `Q66` by `k12`, and the Poisson
coupling `Q12` by `sqrt(k11*k22)`.  That keeps it positive definite and
gives a plain factor when `k11 == k22`.  **All eight factors are honoured
exactly** in linear builds, by one of three paths:

1. All eight factors are 1.0: the legacy
   `ElasticMembranePlateSection(E', nu, h, 0)` call (bit-identical).
2. `f11 == f22 == f12 = f` and `m11 == m22 == m12 == v13 == v23 = b`:
   `ElasticMembranePlateSection(E'*f, nu, h, 0, Ep_mod = b/f)`.  Probed:
   `Ep_mod` scales plate bending AND transverse shear and leaves membrane
   stiffness untouched.  This covers uniform membrane-only and uniform
   (bending + shear) cases, e.g. f = 0.25 or b = 0.25 exactly.
3. Anything else (directional 11/22/12 factors, or shear factors that
   differ from the bending factor, e.g. the ETABS cracked slab
   `m11 = m22 = m12 = 0.25` with shear untouched): an exactly equivalent
   3-layer orthotropic `LayeredShell`, built as
   `ElasticOrthotropic` + `PlateFiber`.
   * Skin / core / skin layers of equal thickness `t`, with
     `t^2 = 2*max eig(A^-1 B)`, `D_skin = B/(2t^3)` and
     `D_core = (A - B/t^2)/t`.
   * The layer transverse shear moduli are `S/(3t)`.
   * This is exact because LayeredShell integrates each layer at its
     mid-plane and sums shear as `sum(G_k t_k)`.  Both were probed: a forced
     layered build reproduces path 2 to 1e-9.
   * The `ElasticOrthotropic` `Gyz` slot carries `G13` (LayeredShell routes
     the section's gamma_13 there; probed).

Not honoured, with a `UserWarning`: nonlinear builds of `layered` (v0.22)
sections, where only `mod` scales E.  Edge-tie chains (v0.22) keep
`E*mod`.  `ShellSection.mod` itself is unchanged.

### API

No new endpoints: `POST /api/model` / `GET /api/model` carry every new
field through `BuildingModel.to_dict`/`from_dict`.  Validation
(`add_section`, `add_shell_section`, `validate`) raises `ValueError` naming
the field when a value is bad:

* `mod_As2`, `mod_As3` and the shell `f/m/v` factors must be finite and > 0;
* the `mass`/`weight` modifiers must be finite and >= 0;
* `As2`/`As3` must be null or finite and > 0;
* `shear_deformation` must be a bool.

Tests: `tests/test_modifiers_shear.py` (40 cases).

## Insertion point, joint offsets and automatic end offsets

ETABS *Assign > Frame > Insertion Point* and *End Length Offsets >
Automatic from Connectivity* (gaps B4 + B5).  Logic lives in
`skyframe.core.insertion`; the engine only adds `-jntOffset` to the
member `geomTransf` calls and feeds automatic lengths to the v0.9 rigid
end-offset machinery.  All defaults reproduce the previous results
byte-identically (no `-jntOffset` is emitted, no offsets are computed).

### `FrameMember` (new fields, JSON keys of the same name)

```json
{"cardinal_point": 10,
 "joint_offsets": null,
 "no_transform_stiffness": false,
 "end_offsets": "manual",
 "auto_rigid_factor": 0.0}
```

* `cardinal_point` — int 1..11, the section point that lies on the
  reference line `pi -> pj`.  ETABS numbering: 1/2/3 = bottom
  left/center/right, 4/5/6 = middle, 7/8/9 = top, 10 = centroid (default),
  11 = shear center.  Bottom/top are the -2/+2 sides (local y), left/right
  are the -3/+3 sides (local z).  Offsets come from the section bounding
  box: `h` along local 2, `b` along local 3.  This covers rectangles, the
  AISC library and Section Designer sections, which all carry b/h.  The
  centroid is taken at the box centre, and 11 = 10 (the built-in shapes
  are doubly symmetric).  A section with b = h = 0 falls back to the
  centroid with a `UserWarning`.
* `joint_offsets` — `null` or
  `{"i": [d1,d2,d3], "j": [d1,d2,d3], "system": "global"|"local"}`.
  These are extra rigid offsets from each joint to the element end, in
  global X/Y/Z or member local 1/2/3.  A missing end is `[0,0,0]`, and
  the stored form is always normalised to all three keys.
* `no_transform_stiffness` — the ETABS "Do not transform frame stiffness
  for offsets from centroid" option.  When true, the cardinal-point
  eccentricity is NOT applied to the stiffness.  Joint offsets still apply.
* `end_offsets` — `"manual"` (default) uses `rigid_i`/`rigid_j`/
  `rigid_factor` as before.  `"auto"` computes them from connectivity.
* `auto_rigid_factor` — the rigid-zone factor in [0, 1] for `"auto"`.
  The default 0 matches ETABS: fully flexible.

Validation (`add_member`, `validate`, `checks`) raises `ValueError` in these
cases:

* `cardinal_point` is not an int in 1..11 (bools are rejected);
* `joint_offsets` is not a dict, has unknown keys, has an end that is not
  three finite numbers, or has a bad `system`;
* `end_offsets` is not `manual`/`auto`;
* `auto_rigid_factor` is outside [0, 1];
* an axial-only (`axial_limit != "both"`) member has an insertion point or
  joint offsets.

### Stiffness model

Element-end eccentricity, in global coordinates:
`e_end = joint_offset_end - (cy*y + cz*z)`, where `(cy, cz)` is the
cardinal point relative to the centroid.  It is applied as a RIGID offset
through `geomTransf <Linear|PDelta> ... -jntOffset e_i e_j`.

* Split members (shell-edge compatibility) get `e` linearly interpolated
  at each segment end.
* Members with rigid end zones get the eccentricity on both the rigid arms
  and the clear-span element.
* OpenSees `Corotational` ignores joint offsets, so eccentric segments fall
  back to `PDelta` and are named in the existing Corotational fallback
  warning.
* Fiber-PMM hinge members that are eccentric stay elastic, with the
  existing warning.
* Span loads act on the eccentric axis.  `eleLoad` loads are transformed by
  OpenSees.  Reversed fixed-end forces (partial/trapezoid/point/thermal)
  and point loads at a member end are applied at the joint with the extra
  couple `e x F`.
* Unequal i/j joint offsets skew the element axis.  Loads and local axes
  still follow the reference-line triad, so keep such offsets small
  relative to L.  Equal offsets, which include every cardinal point, are
  exact.

### Automatic end offsets (`end_offsets == "auto"`)

For each end, take the other frame members with an END at the same joint
and skip any that are collinear.  The offset length is

    L_end = max_n 0.5 * (h_n * |x_m . y_n| + b_n * |x_m . z_n|)

This is the half-extent of the connecting section measured along this
member.  A beam framing into a column gets half the column dimension in the
beam direction, which follows the column `angle`.  A column end under a
beam gets half the beam depth.  A secondary beam framing into a girder gets
half the girder width.

* A user-set `rigid_i`/`rigid_j > 0` wins per end, with its own
  `rigid_factor`.
* The effective rigid zone is `auto_rigid_factor * L_end`.  It is fed to
  the v0.9 machinery, which models the elastic element over the clear span
  with stiff arms.
* If the zones would consume the member, the centerline is kept with a
  `UserWarning`.
* Members split by the mesher keep their manual offsets, with a
  `UserWarning`.
* `"auto"` takes precedence over `panel_zones == "rigid"` for that member.

`skyframe.core.insertion.end_offset_report(model)` returns the ETABS
"Frame End Length Offsets" table:

```json
{"<uid>": {"mode": "auto", "length_i": 0.2, "length_j": 0.2,
           "rigid_factor": 0.0, "rigid_i": 0.0, "rigid_j": 0.0,
           "clear_span": 5.6}}
```

`clear_span = L - length_i - length_j`, the face-to-face span, is
reported even when the factor is 0.

### Force reporting

* `member_forces` and `member_stations` are the forces of the frame's OWN
  axis, which is the eccentric centroidal element axis, as in ETABS.  Each
  frame reports its own axial force and moment, so for a cp 8 beam on a
  slab the composite couple appears as an axial force in the beam plus an
  opposite one in the slab/partner member.
* With end offsets, the end forces are those at the clear-span element
  ends (the faces), unchanged from v0.9.  Stations keep the v0.9 layout.
* `member_deflections` is not produced for eccentric members, just as for
  members with rigid offsets, because the element-end translations differ
  from the joint translations.

### Validation (`tests/test_insertion.py`, 39 cases)

* Simply supported cp 8 beam under a UDL:
  * pin-roller: `5wL^4/384EI` with no axial force, identical to cp 10;
  * pin-pin: thrust `N = 2EAe theta/L` with
    `theta = theta0/(1 + Ae^2/I)`, and midspan deflection
    `5wL^4/384EI - N e L^2/8EI` (1e-6);
  * cp 2 flips the sign of N.
* Cp 2 slab and cp 8 girder linked at the joints, in pure bending:
  `ry = ML/(E Iz_eff)`, `Iz_eff = I1+I2+A1 d1^2+A2 d2^2` (1e-6).  The same
  pair over 12 bays under a UDL lands within 1.3 % of
  `5wL^4/384E Iz_eff`.
* Offset cantilever: `uz = PL^3/3EI + P a^2 L/GJ`, `rx = PaL/GJ`,
  `T = Pa` (1e-6).
* Axial member loads on a cp 8 cantilever give a base moment `W e` on all
  three load paths.
* A cp 8 beam on a meshed shell slab is more than 30 % stiffer.
* Portal frame:
  * auto offsets equal manual offsets of the same lengths, with
    identical member forces;
  * auto factor 0 matches the centerline exactly;
  * factors 0 and 1 bracket a model with explicit 10x-stiff joint zones.
* Byte-identical defaults on a `quick_building`, plus round-trip and
  validation tests.

## Load combinations: RS/TH/nested members and ABS/SRSS/Range types

ETABS Define > Load Combinations parity.  Model helpers:
`skyframe.core.combos_ext`; evaluation: `skyframe.engine.combos_ext`.
Supersedes the v0.4 rule "envelope/add combos reference static cases
only"; every combo that was valid before (add/envelope over static cases,
the "legacy" combos) keeps the original engine path and is
byte-identical.

### Model

```python
# LoadCombo.combo_type: "add" | "envelope" | "abs" | "srss" | "range"
#   (COMBO_TYPES; abs/srss/range/envelope need >= 1 member)
# LoadCombo.cases: {member name: factor (finite)}
```

Member lookup order (first match wins): static case, RS case, RS
directional combo (`model.rs_combos`), TH case (linear, nonlinear or
direct-integration options), staged case (its accumulated final state =
`results["staged"][name]` minus `comparison`), another combo (nesting).
Rejected with `ValueError` "Combo X: <kind> case 'N' cannot enter a load
combo (...)": pushover, buckling, steady_state, psd, the reserved MODAL
case.  Unknown -> "Combo X: unknown case N".  Nested combos are
cycle-checked on `add_combo` and on `validate()`/`from_dict`:
"Combo X: circular combo reference A -> C -> B -> A" (self reference:
"cannot reference itself").  JSON round-trip unchanged (`combos` block).
Check Model: `COMBO_CASE_INVALID` now only flags rejected kinds.

### Rules (per output quantity)

Each member with factor `f` contributes an interval `[lo, hi]`:

| member | hi | lo |
|---|---|---|
| signed result `x` (static case, staged final state, single-valued add combo) | `f*x` | `f*x` |
| RS / RS-directional (unsigned magnitude `r`) | `+abs(f)*r` | `-abs(f)*r` |
| max/min result (envelope or any max/min combo; TH envelope) | `max(f*max, f*min)` | `min(f*max, f*min)` |

RS signs are lost (ETABS behaviour): `1.2D + 1.0E` and `1.2D - 1.0E` give
the same pair, `max = 1.2D + E`, `min = 1.2D - E`.

| combo_type | max | min |
|---|---|---|
| `add` (Linear Add) | `sum(hi_i)` | `sum(lo_i)` |
| `envelope` | `max(hi_i)` | `min(lo_i)` |
| `abs` (Absolute Add) | `V = sum(max(abs(hi_i), abs(lo_i)))` | `-V` |
| `srss` | `V = sqrt(sum(max(abs(hi_i), abs(lo_i))^2))` | `-V` |
| `range` (Range Add) | `sum(max(hi_i, 0))` | `sum(min(lo_i, 0))` |

Nested `add` combos are flattened into their leaf members first (leaf
factors multiply down the tree; duplicates add).  This is exact under the
interval rules.  Nested non-add combos enter as one max/min member.

### Results shape

* Single-valued: an `add` combo whose flattened leaves are all static
  cases or staged final states.  Standard case shape, no `"min"`.  Static
  leaves only: the original `_superpose`, so a nested combo is
  bit-identical to the equivalent flat combo.  Staged leaves: every field
  recorded by all parts is summed (node_disp, reactions, base,
  member_forces, story, member_stations, member_deflections,
  shell_forces).
* Every other combo is a max/min pair in the existing envelope-combo shape.
  The MAX values sit in the standard keys and a nested `"min"` block has
  the same keys.  Combined quantities: `node_disp` (6 dof), `reactions`
  (6), `base` {FX..MZ}, `member_forces` (12 local end forces),
  `story` {ux, uy, drift_x, drift_y, shear_x, shear_y} and
  `member_stations` {N, V2, V3, T, M2, M3} at the 11 stations (`x` copied).
  `shell_forces` are absent and `member_deflections` is `{}` (same as
  envelope combos).
* Time-history members: the TH output records only story series and base
  FX/FY.  A TH member contributes story `ux`/`uy` = [min, max] of the signed
  series, `drift_x`/`drift_y` = [min, max] of the signed inter-story drift
  series (the same h rule as the TH peaks), `shear_x`/`shear_y` =
  +/- the recorded peak (no per-step shear series is stored), and base
  `FX`/`FY` = [min, max] of the signed series.  A combo with a TH member
  (directly or nested) keeps ONLY these quantities: `node_disp`,
  `reactions`, `member_forces` and `member_stations` are `{}`, and `base`
  holds just FX/FY.  The combo then has a `"warning"` that starts with
  "time-history member(s) record only ...".

### Run control (`run()`)

Legacy combos are evaluated where they always were.  Extended combos are
evaluated after the static, RS (and RS-directional), TH and staged runs.
`results["combos"]` and `combo_status` are then put back in model order
(the RS directional combos follow in `combo_status`, as before).  A combo
is `"skipped"` when any member, checked recursively, did not run: a case
that is not run, failed or was capped, or a nested combo that was skipped.
The top-level warning gets "combo 'X' skipped: member(s) [...] not run".

### Downstream consumers

* Engine takedown, section cuts, piers, deflection checks and seismic
  diagnostics (story stiffness/irregularity) use the single-valued combos
  only; max/min combos are skipped.  For legacy combos this is the same
  set as before ("add").  The takedown also needs static-only leaves:
  nested static add combos get flattened pattern factors, and combos with
  staged leaves are skipped.
* `core.tables`: no change.  Its sources already skip any combo block that
  has `"min"`.  Equilibrium folds nested pattern factors recursively and
  leaves out combos that have staged leaves.
* `core.codes.reduce_live_demands`: max/min combos are left unchanged.  A
  single-valued nested combo uses its flattened LIVE factor.
* Design defaults (`check_wall_piers`, composite deflection, the
  `/api/design/seismic341` default combo) take single-valued add combos
  only.  Explicitly named max/min combos in the design envelope helpers
  read the MAX block, the same rule as envelope combos.

Tests: `tests/test_combos_ext.py`.

## P-Delta options (model-wide)

ETABS Define > P-Delta Options (analysis-only).  Model side:
`skyframe.core.pdelta_options`; engine side: `skyframe.engine.pdelta`.

### Model field `BuildingModel.pdelta_options`

```json
{"method": "none",            // "none" | "non_iterative_mass" | "iterative_loads"
 "load_factors": {},          // {pattern: factor}, the iterative_loads gravity combo
 "max_iterations": 2,         // int 1..100 (iterative_loads)
 "tolerance": 0.001,          // finite > 0, relative (iterative_loads)
 "include_in": "all_linear"}  // only value
```

* `to_dict` emits `pdelta_options` ONLY when it differs from these
  defaults, so default models (and their results) are byte-identical.
* `from_dict`: absent means defaults; missing keys are filled with the
  defaults.
* `validate` (also `POST /api/model`, 400) raises `ValueError` for: a
  non-object, unknown keys, a bad `method` or `include_in`, a
  `load_factors` entry naming an unknown pattern or a non-finite factor,
  `max_iterations` that is not an integer in 1..100 (bools rejected),
  `tolerance <= 0` or non-finite, and `iterative_loads` with an empty or
  all-zero `load_factors`.

### Methods

* `"none"`: the pre-existing behaviour (per-case `LoadCase.pdelta` /
  `geometric` only).
* `"non_iterative_mass"` (ETABS "based on mass"): for each story,
  `P_i = g * sum(m)` over the built model's translational nodal masses
  (`max(UX, UY)` lump) located above the story bottom (`elevation -
  height`).  `P_i` is applied as a story spring `-P_i/h_i` on global X
  and Y.
  * Where the story has a rigid-diaphragm master and so does the level
    below, the spring runs between the two masters.
  * For the first story, the lower end is an existing support node fixed
    in UX and UY at the story bottom (the one nearest the master).
  * Otherwise `P_i` is shared over the story's vertical frame columns in
    proportion to `E*A*mod_A/L`, as string springs on each column element.
  * A story with neither gets no spring; it is listed in
    `pdelta.skipped_stories`.
  * The method has no iteration and no torsional P-Delta term.
* `"iterative_loads"`:
  1. Iteration 1 solves the `load_factors` combination linearly on `K`.
  2. Each frame/truss element's tension-positive axial force `N` is read
     (the mean of the FEF-corrected end axials).
  3. The string stiffness `N/L` is added on the element's two transverse
     relative translations, and the combination is re-solved on
     `K + Kg(N)`.
  4. The loop stops when `max|N_k - N_(k-1)| / max|N_k| <= tolerance` or
     after `max_iterations` solves.
  5. The last `N` defines the frozen `Kg`.

  Shells carry no `Kg` under this method.

### How `Kg` enters every linear analysis

`Kg` is built as explicit linear `zeroLength` elements between
(non-coincident) nodes.  Their `uniaxialMaterial Elastic` stiffness is
negative in compression, and they are built with `-doRayleigh 1`.
* A zeroLength is a pure relative-translation spring.  It is exactly the
  lean-column `-P/L` term that OpenSees' `PDelta` geomTransf adds, so an
  iterative_loads column equals the per-case two-stage `pdelta` result to
  round-off.
* The springs are appended at the end of every `_build` that uses the
  `Linear` transformation and has no hinges.  Static cases, combos, modal
  (eigen of `K + Kg` vs `M`), response spectrum (its modes and its
  modal-static solves), linear time history (Rayleigh fit and transient),
  steady-state/PSD, load participation, the seismic diagnostics/story
  stiffness, the pushover load-distribution modes and the stability check
  therefore all use the IDENTICAL stiffness.
* We chose this over holding a PDelta-transform state with `loadConst`.
  That approach would make static cases report increments past a stressed
  state, and the linear/frequency pipelines could not reuse it.

**Precedence:** a static case with its own `pdelta=True` or `geometric` of
`"pdelta"` or `"corotational"` keeps its unchanged nonlinear two-stage flow
on the PDelta/Corotational transformation.  The model-wide `Kg` is NOT
added to it, so nothing is counted twice.  Such cases are listed in
`pdelta.cases_own_geometric`.

**Not affected:**
* hinged builds (pushover, hinged nonlinear time history);
* staged construction (its stage sub-models carry no options);
* linear buckling, which stays its own `K + lambda*Kg` numpy
  eigenproblem (with `base_case`, the base static case is solved with the
  option active);
* load-dependent Ritz vectors (numpy assembly).

No instability check is made beyond solver failure.  When `P` exceeds the
story capacity, `K + Kg` is indefinite: modal raises, but the linear
static solve still returns.

### Results

`AnalysisResults.to_dict()["pdelta"]` is emitted only when the method is
not "none":

```json
{"method": "iterative_loads", "n_springs": 18,
 "iterations": 2, "converged": true, "relative_change": [1.7e-15],
 "load_factors": {"DEAD": 1.0, "LIVE": 0.25},
 "cases_own_geometric": ["EXPD"]}
```

`non_iterative_mass` gives `{"method", "n_springs", "story_P": {story: kN},
"skipped_stories"?: [...], "cases_own_geometric"?: [...]}`.

Tests: `tests/test_pdelta_options.py`.

## Multi-component and load-pattern time histories

Pure logic: `skyframe/core/th_components.py`; OpenSees runtime:
`skyframe/engine/thmulti.py`.  Applies to `run_time_history` (direct
integration, linear and nonlinear, all v1.13 integration / damping /
solver / energy options) and `run_fna` (modal superposition = linear
modal TH when no device links).

### `TimeHistoryCase.components` (optional, round-tripped)

Default `None`: the key is omitted from `to_dict()` and the engine runs
the unchanged single-record path (`direction` + `accel`/`dt` or
`function`) — byte-identical results.  When set, `direction` is ignored
and `accel`/`dt` (or the case `function`) serve only as the default record
of ground components that name no function.
`add_th_case(..., components=[...])`.

```
components: [
  {"direction": "UX"|"UY"|"UZ",          # ground acceleration component
   "function"?: th_functions name,      # omitted/"" -> the case record
   "scale"?: 1.0, "angle_deg"?: 0.0, "time_shift"?: 0.0},
  {"pattern": load-pattern name,        # load-pattern component
   "function": th_functions name,       # time function f(t) (required)
   "scale"?: 1.0, "time_shift"?: 0.0}
]
```

* `angle_deg` rotates a horizontal component counter-clockwise in plan:
  UX at theta acts along `(cos theta, sin theta)`, UY along
  `(-sin theta, cos theta)`; UZ must have 0.  The case `scale` multiplies
  every component (effective factor = case scale * component scale).
* `time_shift` (s, >= 0): the component is 0 before `t = time_shift`.
* Common grid: `dt` = smallest component record dt; each record is
  resampled onto `t_k = k dt` with the OpenSees Path rule (linear, 0
  outside); steps `n = max ceil((shift + (n_c - 1) dt_c)/dt) + 1`
  (= `len(accel)` for one unshifted component); outputs at `(k+1) dt`.
* Validation (ValueError): non-empty list of objects; ground XOR pattern
  keys; unknown keys; direction in UX|UY|UZ; named functions/patterns must
  exist; pattern components need `function`; finite scale/angle/shift,
  shift >= 0; a ground component without `function` needs a case record.
  At run time a pattern with `ground_displacements` is rejected.

### Engine

* Direct integration: one `UniformExcitation` per ground component and
  nonzero global direction cosine (`Path -dt dt -values <resampled> 0.0
  -factor scale*cos`; the trailing 0.0 keeps the last sample when the
  accumulated domain time lands a few ulps past it); one `Plain` pattern
  per load-pattern component with a `Path` series of its time function
  holding the pattern loads (nodal, member, area, thermal, story forces)
  at factor 1.  Gravity stage, hinges, devices and the DI options behave
  as before.
* FNA / modal: modal loads `p_i(t) = -sum_d Gamma_id ag_d(t) +
  sum_p f_p(t) phi_i' P_p` added to the uncoupled Newmark modal equations
  (`fna_modal_th(..., p_ext=)`).  `P_p` = consistent nodal load of the
  pattern (nodal + element-load equivalent forces, read as
  `-nodeReaction` with the pattern at factor 1 at u = 0).  Base series
  `R_d = sum_i L_id (qdd_i + 2 zeta_i w_i qd_i) + M_d ag_d - iota_d' P(t)`.
  Modal truncation applies (exact for the full massed-dof basis).
* UZ components act on UZ mass only: `mass_options.include_vertical`
  (tributary vertical mass) and/or explicit nodal `mz`.  A UserWarning is
  issued when a UZ component is requested and the model has no UZ mass
  ("no vertical (UZ) mass") or `include_vertical` is false (only explicit
  nodal `mz` carry it).
* Story shears (peaks): `sum_above (f_p(t) P_p - m a_total)` — the legacy
  inertia rule extended by the applied pattern loads at nodes at/above
  the story elevation (element-load equivalents sit at member end nodes).

### Results (same `THResults` shape)

```
th_cases[case]["multi_component"] = {          # only when components set
  "dt": s, "n_steps": n,
  "components": [{"direction", "function", "effective_scale",
                  "angle_deg", "time_shift", "cosines": [cx, cy, cz]}
                 | {"pattern", "function", "effective_scale",
                    "time_shift"}],
  "base_FZ": [kN per step, past the gravity state] }
```

### Energy

With components the tracker's external load is
`P = P_grav - M sum_d iota_d ag_d(t) + sum_p f_p(t) P_p`, so `input`
includes the work of the pattern loads; the element-load part
`f_p(t) p0_p` (carried by OpenSees inside the element resisting forces)
is moved to the external side, keeping `strain = 0.5 u'Ku` for an
elastic structure.  Balance is round-off for Newmark average acceleration.

Validation (`tests/test_th_multicomp.py`, 45 cases): 0 deg + 90 deg
components == vector sum of the single-direction runs (DI and modal,
~1e-15); a component at theta == cos theta X + sin theta Y (and UY at
theta == -sin X + cos Y); time_shift == zero-padded record; UZ on an axial
mass-spring column == numpy Newmark (1e-14, DI and FNA); harmonic pattern
load `p0 sin(W t)` == closed-form steady state + transient (2.3e-4 of
peak at dt = 1 ms) and == numpy Newmark (1e-12); FNA == DI with pattern
loads on a 2-story frame (8e-14); ground + pattern == superposition;
quasi-static story shears == applied story forces; energy input == the
independent trapezoidal work of P (nodal and member-UDL patterns), balance
5.5e-14 (Newmark) / 1.1e-3 (HHT alpha = -0.05) on a mixed frame case;
single unrotated component == legacy run bit-for-bit (DI, energy,
nonlinear); round-trip, API and validation.

## Polygon shells and auto mesh

`ShellRegion.corners` takes **3..N** corners: a planar, simple (non
self-intersecting) polygon, convex or concave, listed counter-clockwise
about the intended normal.  A region with **exactly 4 corners and only
rectangular openings keeps the legacy structured quad path bit-for-bit**
(same mesh, numbering, results).  Every other region (3 or 5+ corners, or
any polygon opening) is a *polygon region* (`skyframe.core.polymesh`).

### Model JSON

```jsonc
// shells[i]
{"corners": [[x, y, z], ...],            // >= 3 (4 = legacy quad path)
 "openings": [
   {"u0": 0.1, "v0": 0.2, "u1": 0.3, "v1": 0.4},          // rectangular
   {"u0": 0, "v0": 0, "u1": 1, "v1": 1,                    // polygon hole:
    "polygon": [[x, y, z], ...]}                           // u/v ignored
 ], ...}
```

* `Opening.polygon` (optional, >= 3 in-plane points) is serialized only
  when set, so legacy openings keep exactly `{u0, v0, u1, v1}`.
* On a polygon region, rectangular openings use the polygon's local
  bounding box as the parametric frame (`ShellRegion.map_uv`): `u` runs
  along `e1` and `v` along `e2` (see the frame below).
* Validation (`add_shell` / `validate`) raises `ValueError` for:
  * fewer than 3 corners;
  * a zero-length edge or zero area;
  * a vertex more than 1e-6 m off the best-fit (Newell) plane;
  * self-intersecting or touching edges;
  * openings that are degenerate, outside the region, crossing its
    boundary, or overlapping each other.
* `area`, `opening_area` and `net_area` are exact (vector shoelace).

### Check Model

For polygon regions, the corner-count check becomes polygon validity:

| Code | Severity | When |
|---|---|---|
| `SHELL_CORNER_COUNT` | error | fewer than 3 corners |
| `SHELL_ZERO_AREA` | error | degenerate polygon |
| `SHELL_WARPED` | error | a vertex is off the best-fit plane by more than 1e-6 m |
| `SHELL_SELF_INTERSECTING` | error | any two edges cross or touch |

Concave polygons are valid: `SHELL_CONCAVE` stays a 4-corner-only check.
4-corner regions are checked exactly as before.

### Local frame

The frame is `polymesh.region_frame`:

* `e3` is the unit Newell normal of the corner order.
* Horizontal regions use `e1 = +X`, `e2 = +-Y`.
* All other regions use `e1 = Z x e3` (horizontal) and `e2 = e3 x e1`, so
  vertical walls have `v = z`.

These are the axes of `loads_ext.shell_local_axes`; `local_3` is `e3`.  The
wind Cp normal (`builder._region_normal`), the pier wall normal and the
directional area loads all use the Newell normal for polygons.  4-corner
regions keep `(c1 - c0) x (c3 - c0)`.

### Auto mesh (behavior `"shell"`)

1. **Hard mesh lines** (u = const and v = const) pass through:
   * every polygon and opening vertex;
   * every frame end, link end, support / spring point and nodal-load
     point lying in the plane inside the region;
   * every point where a frame member **pierces** the plane inside the
     region (that member is then split there by the existing frame-splitting
     at shell nodes);
   * the in-region ends of every **constraint segment**: frame members
     lying in the plane (beams), coplanar neighbour shells' edges, and the
     in-plane traces of other shells (a wall meeting a slab, a slab meeting
     a wall).
2. **Optional lines** come from grid lines (every grid system's straight
   lines, as vertical planes) and, for non-horizontal regions, story
   elevations.  They are used only when their in-plane trace is parallel to
   `u` or `v`; oblique grid lines are ignored.  An optional line closer
   than `0.1 * mesh_size` to another line is dropped.
3. Each interval between consecutive lines is divided into
   `ceil(length / mesh_size)` equal parts.  `mesh_size` is therefore a
   maximum element size; the legacy quad path keeps `round`.
4. Every cell is cut by the polygon, opening and constraint-segment chords
   crossing it into **convex pieces**.  Pieces whose centroid is inside the
   region and outside every opening are kept.  Chord intersections with a
   mesh line use a single per-line formula, so neighbouring cells share
   nodes exactly.
5. **Conformity pass**, run after every region has meshed:
   * any pool point (another shell's node, a frame or link end) lying
     strictly inside a polygon-piece edge is inserted into that piece;
   * polygon meshes therefore have no hanging nodes against frames,
     neighbouring polygons (any mesh sizes) or walls;
   * a legacy 4-corner neighbour can still carry polygon nodes on its own
     edges; the v0.22 `edge_constraints` zipper ties them.
6. **Element generation**:
   * a triangle piece becomes one `ShellDKGT`;
   * a convex quadrilateral becomes one `ShellMITC4`;
   * a convex k-gon becomes a quad fan, plus one triangle when k is odd;
   * a piece that received inserted nodes becomes a triangle fan from a
     clean corner, or from an added centroid node.
   Elements run counter-clockwise about `e3`, and the first edge is the one
   that best follows `+e1` (element local x approximately `e1`).
   `polymesh.QUAD_DOMINANT = False` (a module knob) splits every quad into
   two triangles.  Triangles use the region's section: elastic, modifiers,
   cracked-slab scaled, or `LayeredShell` in nonlinear builds.
7. **Tributary areas**:
   * each node gets 1/3 of each triangle's exact area and 1/4 of each
     quad's;
   * the region total is rescaled to the exact `net_area`, a correction of
     about 1e-8 that removes the 1e-6 m coordinate rounding of points on
     oblique edges;
   * uniform area loads, self-weight, area springs and wall wind Cp
     therefore conserve `q * net_area` exactly.
   * Directional and joint-pattern area loads integrate consistently:
     3x3 Gauss on quads and a 6-point degree-4 rule on triangles.
   * The mesh-free resultant (`area_load_resultant`) integrates over an
     ear-clipped triangulation (8x8 sub-triangles each).

### Membrane polygon slabs

Polygon membrane slabs use a **nearest-edge tributary rule**:

* every point of the slab, net of openings, loads the boundary edge
  nearest to it, at the foot of its perpendicular;
* for convex polygons these zones are the faces of the 45-degree roof
  (straight skeleton), so on a rectangle the rule reproduces the 4-corner
  two-way trapezoids;
* the field is integrated numerically
  (`polymesh.MEMBRANE_SAMPLES = 80` cells across the short bounding-box
  side), binned into at least 8 step bins per edge and emitted as UDL
  pieces on the edge beams;
* the totals are rescaled so the load conserves `q * net_area` exactly;
* openings genuinely re-route the load (unlike the 4-corner uniform-ratio
  approximation).

The missing-beam policy is the 4-corner policy generalised to N edges:

* an edge with no beam passes its share to its adjacent edges' beams;
* if neither adjacent edge has a beam, the share goes to the edge's corner
  nodes;
* with no beams at all, the load is split equally among the N corner
  nodes;
* the uncovered remainder of a partly covered edge goes to its corner
  nodes.

### Results shape (the only change)

`results["shell_quads"]` keeps one entry per shell element, in element
order: `{"region": uid, "nodes": [tags]}`.

* Quad entries are unchanged (4 tags, same keys).
* A polygon-mesh triangle is an entry with **3 node tags**; draw it as a
  triangle.
* `shell_forces[i]` (8 values `[Nxx, Nyy, Nxy, Mxx, Myy, Mxy, Vxz, Vyz]`,
  element local axes) stays aligned with the `shell_quads` index.
* For triangles the values are the average of the 4 `ShellDKGT`
  integration points, which is the centroid value for linear fields.
  `Vxz` and `Vyz` are 0 because DKGT is a Kirchhoff (thin) plate.
* The internal per-element nodal force vectors used by piers have 18
  values for triangles, against 24 for quads.

### Consumers

Every consumer below works with polygon regions:

* self-weight / mass (net area, corner nodes for element self mass);
* rigid diaphragm and semi-rigid membership (all mesh nodes at the story
  elevation);
* piers (Newell normal; triangle cut elements);
* section cuts (corner extents);
* wind Cp walls;
* cracked slabs (per element, triangles included);
* layered walls;
* area springs.

### Validation (`tests/test_polygon_shells.py`)

* **Simply supported square plate** (4 x 4 x 0.05 m, mesh 0.25), against
  Timoshenko `w = 0.00406 q a^4 / D`:

  | Mesh | Ratio |
  |---|---|
  | two triangular regions (quads + triangles) | 1.004 |
  | triangle-only | 0.998 |
  | quads | 1.002 |

* **L-plan cantilever core** (12 m tall, two 3 m legs, both 5-corner
  polygons), against unsymmetric bending plus web shear by hand: tip ratio
  0.994 with no diaphragm and 0.972 with rigid diaphragms.
* A 5-corner rectangle matches the 4-corner quad path: same base reaction,
  and deflection within 3% (both shell and membrane).
* Load equilibrium and area / self-weight sums (L, T and U shapes, a
  triangle, polygon openings) are exact to 1e-9.

## Nonlinear static load cases and case chaining

ETABS Define > Load Cases > Nonlinear Static (gap audit A4) and
"Initial conditions: continue from state at end of nonlinear case" (A5).
Model side `skyframe/core/nonlinear_static.py`, solver
`skyframe/engine/nonlinear_static.py`. A model that uses neither field
serializes, validates and analyzes byte-identically to before (no new
model or results key).

### Model JSON (both keys emitted only when used)

```
"nonlinear_static_cases": {
  "<name>": {
    "name": "<name>",
    "loads": [{"pattern": "DEAD", "scale": 1.0}, ...],  # >= 1; patterns may repeat (scales add)
    "load_application": "load_control" | "displacement_control",
    "steps": 10,                       # int 1..10000
    "control_point": [x, y, z] | null, # monitored joint, OR
    "control_story": "Story3" | null,  # its diaphragm master (else lowest-tag story node)
                                       # neither: the pushover roof control node
    "control_dof": "UX"|"UY"|"UZ"|"RX"|"RY"|"RZ",
    "target_disp": null | m or rad,    # displacement_control: required, != 0; the TOTAL
                                       # monitored displacement at the end of the case
                                       # (including the start_from state)
    "geometric": "none" | "p_delta" | "large_displacement",  # Linear | PDelta | Corotational
    "start_from": null | "<nonlinear static case>",
    "hinges": "column_base"|"all_ends"|"asce41", "My": {uid: kN*m},
    "default_My": null | kN*m, "hardening": 0.02, "hinge_params": {...}
  }
},
"modal_from_case": null | "<nonlinear static case>"
```

The hinge fields are exactly the `PushoverCase` ones and go through the
same hinge-insertion machinery (`_build(hinge_case=case)`): Steel01
column_base/all_ends springs with `My`/`default_My`, or `asce41`
member-assigned `auto_m3` / `fiber_pmm` hinges. The default (no My)
inserts no hinge. Tension/compression-only members, gap/hook/isolator
links, compression-only springs and nonlinear layered shells behave as in
a pushover. The model-wide P-Delta options do NOT apply (a hinged build,
as for pushover); use `geometric`. Python:
`model.add_nonlinear_static_case(name, loads, **fields)` (`loads` may be
the list or a `{pattern: scale}` dict).

Validation (`ValueError`): unknown pattern / story / member, empty loads,
bad enums, steps out of range, control_point and control_story both
given, displacement_control without target_disp, name already used by
another case kind (or MODAL), `start_from` unknown or itself, a
`start_from` CYCLE ("circular start_from reference A -> B -> A"), a chain
mixing `geometric` values, `modal_from_case` not a nonlinear static case,
a pushover whose `start_from` names a nonlinear static case of a different
geometric (none<->linear, p_delta<->pdelta,
large_displacement<->corotational).

### Solution (one domain per chain)

The chain `[root, ..., target]` (following `start_from`) is solved in ONE
OpenSees domain, built once with the TARGET case's hinge definition and the
chain's transformation. Stage k adds its loads in Plain pattern `100 + k`
(Linear series) and is solved with Newton (NormDispIncr 1e-8, 50 its;
NewtonLineSearch retry, then a 10-substep retry) by LoadControl(1/steps)
or DisplacementControl(monitored DOF, (target - start)/steps; a diaphragm
slave drives its master for UX/UY/RZ); then `loadConst -time 0` and the
next stage continues from that END STATE. A non-converging START stage
fails the case (`case_status` "failed"); a non-converging TARGET stage
returns the last converged state with `converged: false` and a warning.

### Results JSON (`results["nonlinear_static"][name]`, only when any ran)

The final CUMULATIVE state (start_from chain included) in the static-case
shape — `node_disp, reactions, base, member_forces, member_stations,
member_deflections, story` (+ `shell_forces`) — plus:

```
"nonlinear": {
  "chain": ["GRAV", "PUSH"], "load_application": ..., "geometric": ...,
  "converged": true, "final_load_factor": 1.0,       # lambda of THIS case's loads
  "history": {"step": [0..n], "load_factor": [...],  # entry 0 = initial (start) state
              "disp": [...],                         # monitored DOF, total
              "base": {"FX": [...], ..., "MZ": [...]},  # total base reaction per step
              "monitored": {"node": tag, "dof": "UX", "point": [x, y, z]}},
  "hinge_rotations": {uid: peak |rot| over this case},
  "yielded": [uids],              # Steel01: |rot| > My/k; asce41/fiber: state != elastic
  "hinges": [...],                # asce41 / fiber per-hinge histories (pushover shape)
  "warnings": [...]
}
```

Reactions fold a support's hinge duplicate into the support node (the
Transformation handler leaves the column shear on the duplicate); spring
reactions are added as in static cases. Member stations use the applied
span loads scaled by each stage's final load factor. Story shears are those
of the effective pattern factors sum(stage scale x lambda).

### Chaining into pushover and modal

* `PushoverCase.start_from` may name a nonlinear static case: the push
  continues in the same domain from that chain's end state (pushover
  hinges govern the build; the capacity-curve displacement is measured
  past that state; base shear = push load factor x reference, unchanged).
* `modal_from_case`: the MODAL eigen solve (and everything consuming it:
  RS, modal TH/FNA, frequency-domain cases, load participation, the
  nonlinear TH Rayleigh fit) uses the TANGENT stiffness at the end of that
  case's chain (P-Delta stressed and/or yielded state); masses unchanged.

### Run control and combos

`case_kinds()` gains `"nonlinear_static"`. Dependencies (transitive,
status `run_as_dependency`): a nonlinear static case needs its
`start_from`; a pushover needs a nonlinear static `start_from`; MODAL
needs `modal_from_case`. Each case re-solves its own chain (results do not
depend on run order). Combos: REJECTED — a nonlinear static case in any
load combination raises "nonlinear static results are a path-dependent
state, not superposable; define a nonlinear static case with the combined
loads instead" (`core/combos_ext._REJECT`). Nonlinear static results are
not fed to takedown / section cuts / piers / deflection checks / seismic
diagnostics.

### Validation (`tests/test_nonlinear_static.py`)

* Elastic load control == linear static (1e-9, steps 1 and 7);
  `p_delta` == per-case P-Delta (1e-7; tip = 1/(3EI/L^3 - P/L) to 1e-6);
  `large_displacement` == per-case corotational (1e-6).
* EPP cantilever (My 30 kN*m, L 3 m, target 0.03 m): V = 10.000 = My/L,
  base moment 30.000, hinge rotation 0.0082222 = My/k_t + (d - d_y)/L.
* Tension-only X brace (H 10, 4 x 3 m): tension 12.49999 = H/cos,
  compression -1.3e-5 (slack).
* Chained gravity -> lateral == combined case == linear static; 3-level
  P-Delta chain == combined P-Delta case; pushover from a nonlinear
  gravity case == pushover with the same `gravity`.
* Modal from a P-Delta gravity case (P = 0.6 Pcr_s): T = 0.72552 s vs
  elastic 0.45886 s; == T0/sqrt(1 - P/Pcr_s) and == model-wide
  `iterative_loads` (1e-6); 2-story frame translational modes ==
  iterative (1e-4); yielded-hinge state matches the hand series-spring
  SDOF (1e-3).
* Cycle detection, validation, run-control dependency status, combo
  rejection, round trip, byte-identical defaults.

## Multiple diaphragms per story and additional mass

ETABS Define > Diaphragms, Assign > Joint/Shell > Diaphragms and Assign >
Joint/Frame/Shell > Additional Mass.  Model side: `skyframe/core/diaphragms.py`;
engine side: `skyframe/engine/diaphragms.py`.  Every key below is emitted only
when it differs from its default, and a model that does not use them gives
byte-identical results.

### Model JSON

```json
"diaphragms": {"DA": {"type": "rigid"}, "DB": {"type": "semi_rigid"}},
"joint_diaphragms": [{"point": [0, 0, 3], "diaphragm": "DA"}],
"shells":   [{"...": "...", "diaphragm": "DA", "additional_mass": 0.3}],
"members":  [{"...": "...", "additional_mass": 0.4,
              "additional_mass_mode": "lumped"}],
"nodal_masses": [{"point": [0, 0, 3], "mx": 0, "my": 0, "mz": 0,
                  "mrx": 0, "mry": 0, "mrz": 50}]
```

| Field | Meaning |
|---|---|
| `diaphragms` | `{name: {"type": "rigid" \| "semi_rigid"}}` |
| `joint_diaphragms` | `[{"point": [x, y, z], "diaphragm": name}]`; the point must be at a story elevation |
| `ShellRegion.diaphragm` | name, or `""` for none; horizontal regions only |
| `FrameMember.additional_mass` | t/m, >= 0 |
| `FrameMember.additional_mass_mode` | `"lumped"` gives `mL/2` at each member end; `"distributed"` gives `mL_seg/2` at the ends of every FE segment |
| `ShellRegion.additional_mass` | t/m^2, >= 0; `q * net_area` is spread over the mesh nodes by tributary area (membrane regions: equal split over the corner nodes) |
| `NodalMass.mrx/mry/mrz` | t·m^2 rotational inertia, >= 0 |

Validation raises `ValueError` for:

* an unknown diaphragm type;
* an unknown or unassigned name;
* a joint that is not at a story elevation;
* a joint assigned to two diaphragms;
* a non-horizontal shell assigned to a diaphragm;
* a negative or non-finite mass;
* a bad `additional_mass_mode`.

### Engine semantics

**Named stories.** A story is named when at least one of its joints or
shells carries an assignment. On a named story the assignments replace the
legacy "the whole plane is one diaphragm" rule, and `diaphragm` /
`story_diaphragm` are ignored there.

* A shell assignment captures every structural node that lies inside or on
  the slab polygon at that elevation.
* A joint assignment wins over a shell assignment.
* Unassigned nodes are free, as in ETABS.

**Rigid diaphragms.** Each `rigid` diaphragm with at least 2 nodes gets its
own master and its own `rigidDiaphragm` constraint.

* The master sits at the diaphragm centre of mass. The mass weights are the
  derived tributary story mass plus the lateral additional mass. With no
  such mass, the master sits at the plan bounding-box centre.
* A named story with exactly one diaphragm is also registered in
  `asm.masters[story]`, so every story-keyed consumer works unchanged.
* Stories with several diaphragms are not registered in `asm.masters`.
  Story-level displacements on those stories use the existing node-average
  fallback.

**Semi-rigid diaphragms.** A `semi_rigid` diaphragm adds no constraint. Its
slab carries the in-plane stiffness, as in the v0.22 semi-rigid treatment.

**Story mass (rigid, `lump_at_stories`).** Derived story mass:

* `m_d` is the sum of the tributary masses of the diaphragm's nodes.
* `J_d = m_d (a_d^2 + b_d^2)/12 + m_d |c_d - master|^2`, where `a_d` and
  `b_d` are the diaphragm's plan extents and `c_d` is its tributary
  centroid.

Explicit `story_masses` are split by diaphragm bounding-box area. Free nodes
and semi-rigid nodes keep their tributary mass.

**Additional mass.** Additional mass goes straight into the mass matrix,
never through load patterns.

* `include_lateral` puts it on UX/UY; `include_vertical` puts it on UZ at
  the nodes.
* With `lump_at_stories`, the lateral part of an item moves to the rigid
  master when all its nodes are slaves of that one master. Its exact moment
  of inertia about the master moves with it:
  * a frame segment gives `m (d^2 + L_h^2/12)`;
  * a slab gives `q × polar second moment of the polygon`, minus openings.
    A rectangle about its centroid gives `m (a^2 + b^2)/12`.
* Any other slave lump moves as a point mass, `m r^2`.
* Without `lump_at_stories`, the lateral mass stays at the nodes.

Explicit `NodalMass` translations are unchanged and are always added as
given. `mrx` and `mry` go on the node's RX and RY. `mrz` goes on RZ, or on
the master's RZ when the node is a rigid slave.

**Story forces on a named story.** The force is split over the story's
diaphragms, plus a free-node group, in proportion to lateral mass (node
count when the story is massless).

* A rigid diaphragm takes its share at its master. Accidental torsion is
  `Mz = F_d·ecc·(the diaphragm's OWN transverse extent)`.
* Semi-rigid diaphragms and free nodes use the v0.22 mass-weighted field
  with an antisymmetric torsion couple.

**P-Delta (`non_iterative_mass`) on a story with several diaphragms.**

* `P_d = g × the mass above the story bottom inside the diaphragm's plan
  box`.
* The spring runs to the master of the same name on the story below. On the
  first story it runs to the nearest support fixed in X and Y.
* If neither exists, the fallback is column strings, restricted to the
  columns inside the box.
* `results.pdelta.diaphragm_P = {story: {name: P_d}}`.

### Results (only when named diaphragms are assigned)

```json
"diaphragms": {
  "definitions": {"DA": {"type": "rigid"}},
  "stories": {"S1": {"DA": {"type": "rigid", "master": 17, "n_nodes": 4,
      "mass": 18.35, "mass_rz": 110.13, "cm_x": 3.0, "cm_y": 3.0,
      "extent_x": 6.0, "extent_y": 6.0, "x": 3.0, "y": 3.0,
      "cr_x": 3.0, "cr_y": 3.0, "k_theta": 1.609e6}}},
  "cases":    {"EQX": {"S1": {"DA": {"ux": 0, "uy": 0, "rz": 0,
                 "drift_x": 0, "drift_y": 0,
                 "tors_ratio_x": 1.0, "tors_ratio_y": 1.0}}}},
  "combos":   {"...": "same shape (envelopes omitted)"},
  "rs_cases": {"...": "same shape"}
}
```

**Per-story values.**

* `mass` and `cm` come from the built lateral mass map (master plus member
  nodes).
* `mass_rz` is the master's RZ inertia.
* `cr` and `k_theta = 1/phi` come from the unit-load method at each master.
* A semi-rigid diaphragm has `master: null`. Its displacements are node
  averages.

**Per-case values.**

* Drift is measured against the same-name diaphragm on the story below,
  read off that master's rigid field at this master's location. When there
  is no same-name diaphragm, the reference is the story-level result below,
  or the base.
* `irregularity[case][story]` for stories with several diaphragms takes the
  maximum torsional ratio over their diaphragms.

**Unchanged.** The story-keyed `story` results are byte-identical. The
per-diaphragm entries are a sibling key, not a `"diaphragms"` sub-dict
inside `story`, because a sub-dict would break combo superposition and
envelopes.

**Tables.** `centers_mass_rigidity` and `diaphragm_cm_displacements` emit one
row per named diaphragm, labelled with its name. Legacy stories still use
`"D1"`.

### Not covered (documented)

* The load-dependent Ritz analysis (self-contained frame-only numpy
  assembly) keeps the legacy one-diaphragm-per-story mass.
* Time-history, FNA and pushover story series on stories with several
  diaphragms use the story node-average fallback.
* Load-participation condensation (`modalcombo`) uses story masters only.
* The `diaphragm_max_avg_drifts` table lists only single-master stories.

### Validation (`tests/test_diaphragms_mass.py`, 30 tests)

* **Two towers on one level, with separate diaphragms.** Periods equal each
  tower solved alone to 1e-9:
  `[0.092812, 0.092812, 0.085069, 0.085069, 0.051977, 0.047207]` s.
  One shared diaphragm couples the towers: 3 modes,
  `[0.094784, 0.087812, 0.059839]` s.
* **Static displacement and accidental torsion.** Each tower's `ux` and `rz`
  equal the tower solved alone under its mass share of the force. They also
  equal `F_d·0.05·L_d/k_theta`.
* **Centre of mass.** With a beam UDL, `CM_y = 2.25` m, matching the hand
  value.
* **Two-story drift.** Drift per diaphragm is `(u2 - u1)/h`.
* **P-Delta.** `P_d = g·m_d`, and `ux` equals the tower alone with P-Delta.
* **Irregularity.** The story ratio is the maximum over the diaphragms.
* **Mixed rigid and semi-rigid diaphragms.** The model runs and reaches
  equilibrium.
* **Named single diaphragm.** It gives the same periods as legacy.
* **Joint rotational inertia.** `T = 2π sqrt(J/kθ)`: 0.0350224 s for both
  the solve and the hand value.
* **Frame additional mass.** `mL` adds exactly to the total mass, and
  `T1/T0 = sqrt((M0 + mL)/M0) = 1.216553`. Beam-line `J` is
  `m(d^2 + L^2/12)`.
* **Shell additional mass.** `qA` adds exactly. `J = m(a^2 + b^2)/12`:
  184.8, matching the hand value.
* **Mass options.** The include_lateral, include_vertical and
  lump_at_stories options are each checked.
* **Round trip and legacy output.** Every new field round-trips. Legacy
  serialization and results are unchanged. All validation errors are
  checked.

## Groups and user-defined staged construction

ETABS Define > Groups and user-defined Staged Construction.  Every default
reproduces the previous results byte-identically: `groups` is emitted by
`to_dict` only when non-empty, `SectionCut.group` only when set, and a
`StagedCase` with `stages: "per_story"` runs the unchanged v0.6/v0.25 path.

### Model JSON

```
"groups": {"<name>": {"members": [uid], "shells": [uid], "links": [uid],
                      "points": [[x, y, z]], "color": "<str>"}}
"section_cuts": [{..., "group": "<name>"}]        # optional
"staged_cases": {"<name>": {"name", "pattern", "include_live",
    "time_dependent",
    "stages": "per_story" | [
      {"name": str, "duration_days": float >= 0 (default 0),
       "operations": [
         {"op": "add",    "group": str, "age_days": float >= 0 (default 28)},
         {"op": "remove", "group": str},
         {"op": "load",   "group": str, "pattern": str, "scale": float (1)}]}]}}
```

Validation: group references must exist (no duplicates; points are finite
`[x, y, z]`; unknown keys rejected); stage names unique; an `add` may only
name inactive objects and a `remove` only active ones (the sequence is
simulated); `load` patterns must exist.  For a stage list `pattern` is not
used and `time_dependent.days_per_story` is not required (still required
for `per_story`).

### Model helpers (`skyframe.core.groups`, wrapped on `BuildingModel`)

`add_group`, `delete_group(name, force=False)` (refused while a section
cut / staged case references it; `force` clears those references),
`rename_group(old, new)` (updates cuts and stage operations),
`prune_groups()` (drops references to deleted members/shells/links;
returns `{group: {kind: [removed uids]}}`), `rename_object_in_groups(kind,
old, new)`; module-level also `remove_object_from_groups`,
`move_point_in_groups`, `groups_of`, and `resolve_group(model, name)` ->
`{"members": [FrameMember], "shells", "links", "points"}`.  API: `POST
/api/groups` with `{action: "upsert"|"delete"|"rename"|"prune", name,
group?, new_name?, force?}` returns the model dict (400 on error); `POST
/api/section-cut` accepts `group`.

### Section cuts by group, group resultants

A cut with `group` integrates only the group's members crossing the plane
(only its shells are counted); same result shape and sign convention.
`group_end_resultant(model, case, group, axis="z", about=None)` sums the
group's member internal forces at their LOW ends along `axis`, oriented
like a cut (gravity -> positive FZ), moments about `about` (default: the
centroid of the low ends): `{FX..MZ, n_members, about}`.  For a base-level
column group with `about=(0,0,0)` it equals the base reaction totals.

### User-defined stages (`skyframe.engine.staged_user`)

Rebuild-and-accumulate, linear, geometry updating ignored (as per_story):

* the active structure starts EMPTY.  `add` activates a group's
  members/shells/links/points; objects enter UNSTRESSED (each stage solves
  a fresh model of the active objects under only that stage's increments;
  node displacements accumulate by coordinate).  An explicit support or
  point spring at a point listed in any add/remove group is active only
  while that point is; other supports are active while an object end /
  corner exists at their point.  Without explicit supports the auto base
  fixity applies and every stage's active structure must reach the full
  model's base level.
* `remove` applies the ACCUMULATED global end forces the removed objects
  exerted (members: the 12 member end forces at pi/pj rotated to global;
  links/shells: OpenSees element resisting forces at their nodes) as joint
  loads on the remaining structure at every joint still present; their
  accumulated forces are discarded and lost supports drop their reaction.
* `load` applies pattern x scale to the group's ACTIVE objects: member,
  area and thermal loads of the group's objects, self-weight expanded into
  explicit member/area loads of those objects, and joint loads only at the
  group's explicit `points` (skipped with a warning while no active object
  is there).  Story forces and ground displacements are skipped (warning).
* all operations of a stage are solved together in one increment.
  `include_live` is applied at the end on the final active structure.

Results (`StagedResults`): `case` = final state as a normal CaseResults
on the FULL model's node tags (objects inactive at the end report zeros;
base totals recomputed from the live reactions), so load combinations
accept it like any staged case; `comparison.oneshot_case` = the full model
under the same total (group-filtered) loads at once; `shortening` is
absent; NEW `stages` (cumulative state at the end of each stage):

```
"stages": [{"name", "duration_days", "t_start", "t_end",
            "active": {"members": [...], "shells": [...], "links": [...]},
            "node_disp": {tag: [6]}, "reactions": {tag: [6]},
            "base": {FX..MZ}, "member_forces": {uid: [12]}}]
```

Time-dependent (`time_dependent`, AAEM, chi = 0.8, ACI 209 curves): stage
s starts at `T_s` = sum of earlier `duration_days`; an object added in
stage a with `age_days` was cast at `T_a - age_days`.  Stage-s increments
(loads and release forces) use `E_adj = E(t0) / (1 + chi*phi(t_eval -
T_s))` on concrete objects (`E(t0)` = ACI 209 aging growth at the object's
age at `T_s` when `aging`).  `t_eval` defaults to the END of the last
stage (sum of durations; infinity when 0).  A final shrinkage increment
on the final active structure gives each concrete member `-eps_sh(t_eval
- t_cast)` via the thermal equivalence with `E/(1 + chi*phi(t_eval -
t_cast))`.  No separate elastic pass / shortening report.

Limitations: member removal transfers forces at the member's two END
joints only (interior mesh-node connections and rigid-end offsets are
ignored in the transfer); loads already applied to a removed SHELL stay on
its boundary joints; support / joint-load activity is decided from object
end points and shell corners (not interior mesh nodes).

### Validation (`tests/test_groups_staged.py`, 35 tests)

* 3-story column built story by story, P = 100/200/300 kN, EA = 4.8e6 kN:
  top-of-story uz = -sum_{k>=j} P_k * j*h/EA exactly (roof -0.5625 mm vs
  all-at-once -0.875 mm); identical to the per_story mode.
* Two-span beam (2 x 5 m, w = 10 kN/m) on a temporary 0.2x0.2 m prop:
  stage-1 prop force = compatibility hand value (61.80 kN) to 1e-9; after
  `remove` the beam is exactly simply supported: M_mid = 125 kNm, R = 50
  kN each, delta = 5w(2L)^4/384EI = 13.889 mm; prop base reaction -> 0.
* `load` op == static case on the erected structure (forces/disps 1e-9),
  scale splitting, group filtering, self-weight.
* Group section cut == member end forces; LEFT + RIGHT == full cut; group
  end resultant == group cut at the base == base reactions.
* Creep ratio 1 + 0.8*phi(t) with t from the stage durations (and an
  explicit t_eval); shrinkage `eps_sh(88 d)*L` for a member added 28 days
  old; aging changes results.
* per_story byte-identical; groups / stage lists round-trip; validation and
  consistency helpers; combos with a user staged case; API endpoint.

## Frame auto-mesh and output stations

ETABS *Assign > Frame > Frame Auto Mesh Options* and *Assign > Frame >
Output Stations* (`skyframe/core/framemesh.py`).  Every default reproduces
the earlier results byte for byte.  When a field is unset it is left out
of the JSON.

### Model JSON

```json
"frame_auto_mesh": {"at_intermediate_joints": false, "at_intersections": false,
                    "max_length": null, "min_segments": null},
"members": [{"uid": "B1", "...": "...",
             "auto_mesh": {"at_intermediate_joints": true,
                           "at_intersections": true,
                           "max_length": 1.0, "min_segments": 2},
             "output_stations": {"max_spacing": 0.5}}]
```

* `frame_auto_mesh` is the model-wide default.  Absent or `null` turns
  everything off.  A member's `auto_mesh` replaces it completely, and any
  keys the member leaves out are off.  Unknown keys are rejected.
  `max_length` must be finite and > 0.  `min_segments` must be an
  integer >= 1.
* `output_stations` is either `{"max_spacing": s}` (s > 0) or
  `{"min_number": n}` (an integer n >= 2).  Exactly one key is allowed.
  Absent or `null` gives the fixed 11 equally spaced stations.

### Auto mesh: what gets divided

The analysis member is divided, not the drawn object.

* **Intermediate joints.** These are joints within 1e-3 m
  (`AUTO_MESH_TOL`, the same as Check Model's default tolerance) of the
  axis and strictly inside the span.  A joint is any of the following:
  * a member end or link end
  * a point support or spring support
  * a shell corner

  The joint point itself becomes a segment node, so the other object is
  connected there.  A frame end landing on a span (a T-junction) is handled
  here.
* **Intersections.** Two non-parallel members cross when their axes come
  within 1e-3 m and the closest points lie strictly inside both spans.
  Both members are divided at the midpoint of the two closest points as
  soon as **either** one has `at_intersections`, so the crossing is always
  connected.
* **`min_segments`** adds the equal-division points of the whole member.
* **`max_length`** then divides each piece between consecutive cuts into
  `ceil(len / max_length)` equal parts.
* Each segment station is measured from the 1e-6-rounded node of the point
  pool, so segment lengths match the FE geometry exactly.  Splitting
  therefore leaves static results unchanged to round-off, because the
  elements are exact.
* **Axial-only members** (tension or compression) are never divided,
  either as the meshed member or as an intersection partner.  A
  `UserWarning` is raised, and the crossing stays unconnected.
* **Conformity with shells.** Auto-mesh points join the point pool
  **before** the shell meshers run:
  * Polygon shells insert them into their element edges during the
    conformity pass.
  * On 4-corner structured shells, a point that is not already a mesh node
    is a hanging node.  It is tied only when `edge_constraints` is on.

  Shell-node cuts and Winkler cuts are merged as before.  The mesh exposes
  `MeshedModel.auto_split`, the set of member uids divided by the auto
  mesh.

### Rigid end offsets and insertion points on split members

On a member divided by the auto mesh, rigid end offsets are kept in all
three cases: manual `rigid_i`/`rigid_j`, `end_offsets: "auto"`, and panel
zones set to `"rigid"`.

* The offsets are kept when the i offset fits inside the **first**
  segment and the j offset fits inside the **last** segment.  The rigid
  arms then sit on those two end segments only.
* If an offset does not fit, the earlier behaviour applies:
  * Auto and panel-zone offsets are dropped with a warning.
  * Manual offsets raise `ValueError`.
* Insertion points and joint offsets use the per-segment `offset_at`
  interpolation, as before.
* End forces match the unsplit member.  Interior stations are exact
  statics of the offset model, in which span loads act on the clear span
  only (the v0.9 convention).
* Shell-split members without auto mesh keep their earlier behaviour.

### Output stations

* `station_xs(model, member, segs)` builds the station list from three
  sources:
  * the equal stations of the option (`ceil(L/s)` intervals, or `n - 1`
    intervals for `min_number`)
  * every analysis segment end
  * every concentrated load point (`MemberLoad` of kind `"point"` or
    `"moment"`, from **any** pattern)

  The list is sorted, and duplicates within 1e-9·L are merged.  It is the
  same for every case, so combinations and RS/TH/staged envelopes still
  superpose station by station.
* `member_stations[uid]["x"]`, the six force columns and
  `member_deflections[uid]` all have the variable length.  Force recovery
  and deflection recovery are exact at any station, as before.
* Results remain one entry per drawn object, stitched along the original
  member.  Consumers already iterate generically over the station list:
  * section cuts (linear interpolation, exact at a station)
  * deflection checks
  * design-free envelopes
  * result tables
* **Virtual work.** With variable stations the integral uses non-uniform
  composite Simpson (`integrate_stations`).  This is exact for quadratics
  over each pair of intervals, and a trailing odd interval uses the
  quadratic through the last three stations.  The default 11 stations keep
  the uniform Simpson rule.
* **Vibration screen.** The midspan value is read at the station at L/2,
  or linearly interpolated when there is none.
* **`element_self_mass`.** Mass of an auto-split member is lumped per
  segment.  The weight-derived tributary mass already worked per segment.

### Check Model

* `FRAME_INTERSECTION` no longer fires for a crossing connected by the auto
  mesh: either member has `at_intersections`, both members can be meshed,
  and the gap is at most 1e-3 m.
* `FRAME_JOINT_ON_SPAN` no longer fires when the spanned member has
  `at_intermediate_joints`.

### Validation (`tests/test_frame_automesh.py`, 35 tests)

* **Two crossing beams with no shared joint.** Beam A is 8 m with stiffness
  I, beam B is 6 m with stiffness 3I, and P = 100 kN acts at the crossing.
  * Without auto mesh the beams are independent: A carries 50/50 and B
    carries nothing.
  * With `at_intersections` they act as a grillage.  Each beam's share is
    `P_A = P·k_A/(k_A+k_B)` with `k = 48EI/L³`.  The reactions, both
    midspan moments and the common deflection `P_A/k_A` match to 1e-8.
* **Secondary beam framing into a girder's span.** With
  `at_intermediate_joints` the girder carries R = wL/2 at midspan (end
  reactions R/2, M = RL/4, exact).  A point support on a span gives the
  two-span continuous beam: the middle reaction is 5wL/4.
* **`max_length` / `min_segments`.** End forces, stations and deflections
  match the unsplit member to 1e-10.  The first mode of a simply supported
  beam with distributed mass approaches `(π/2L²)·√(EI/m)` monotonically.
  The relative error by number of segments is:

  | Segments | Relative error |
  |---|---|
  | 2 | 7.3e-3 |
  | 4 | 3.1e-4 |
  | 8 | 1.7e-5 |
  | 16 | 1.0e-6 |

  At 2 segments the result equals the hand single-mass value
  `√(48EI/L³/M)/2π`.
* **Output stations.** `max_spacing` 1.0 on a 5 m beam gives 7 stations:
  6 equal stations plus the point load at 1.7 m.  At the load,
  `M = P·a·b/L` is exact.  `min_number` together with segment ends gives
  the expected merged list.  Combinations, section cuts and virtual work
  (total = roof displacement to 1e-9) all accept variable station counts.
* **Defaults and round trip.** Defaults are byte-identical: the model JSON
  is unchanged and the results JSON is identical.  The model also survives
  a round trip.  Validation errors are covered for both fields.

## Named spring properties and link hysteresis types

ETABS Define > Spring Properties > Point Springs (B9) and extra link
hysteresis types (B11).  Logic: `skyframe/core/springprops.py` (validation,
local axes, exact spring force laws) and `skyframe/engine/hysteresis.py`
(OpenSees builders).  Every default reproduces the previous results
byte-identically: `spring_properties` is emitted only when non-empty, a
`SpringSupport` emits `property` / `angle_deg` only when set, and supports
without them take the unchanged v0.8 path.

### Model JSON

```json
"spring_properties": {
  "FDN": {"kind": "compression_only", "k": [0, 0, 50000, 0, 0, 0],
          "nonlinear_dof": "U3"},
  "SOIL": {"kind": "multilinear",
           "curves": {"U1": [[0.01, 100], [0.03, 150]]}},
  "GAP": {"kind": "gap", "k": [0, 0, 5000, 0, 0, 0], "gap": 0.01},
  "ROT": {"kind": "linear", "k": [1500, 4000, 0, 0, 0, 0],
          "local_axes": {"angle_deg": 35}}
},
"spring_supports": [
  {"point": [0, 0, 0], "stiffness": [0, 0, 0, 0, 0, 0],
   "property": "FDN", "angle_deg": 30.0},
  {"point": [5, 0, 0], "stiffness": [800, 0, 0, 0, 0, 0]}
]
```

* `kind`: `linear | multilinear | compression_only | tension_only | gap`.
* `k`: 6 LOCAL stiffnesses `[k1, k2, k3, kr1, kr2, kr3]` (kN/m, kN*m/rad).
  Required except for `multilinear`, where it is optional and applies to
  DOFs without a curve.
* `curves` (multilinear only): `{"U1".."R3": [[d, F], ...]}`, with d strictly
  increasing.  All-positive curves are mirrored through the origin;
  otherwise the curve must contain `[0, 0]`.  The spring is nonlinear
  ELASTIC (`ElasticMultiLinear`): it loads and unloads on the curve, and the
  end segments extrapolate past the last points.
* `nonlinear_dof` (c-only / t-only / gap, default `"U3"`): the DOF that
  carries the nonlinear law; the other DOFs with `k > 0` are linear.
  Compression is negative local deformation (settlement on U3).
* `compression_only`: full k for u < 0 and a residual 1e-6 k for u >= 0
  (`Elastic` with `Eneg`, the v0.22 pattern).  `tension_only` is the
  mirror.
* `gap` (m, >= 0): force `k (u + gap)` once u < -gap, else 0
  (`ElasticPPGap` in `Parallel` with a residual 1e-6 k).
* `local_axes`: `{"angle_deg": a}` (plan rotation about +Z) or a 3x3
  matrix whose rows are the local 1/2/3 unit vectors (orthonormal,
  right-handed).  `SpringSupport.angle_deg` rotates the property axes
  further about global Z.  Inline `stiffness` with `angle_deg != 0` and no
  property is a rotated linear spring.
* With `property` set, inline `stiffness` must be all zero (it may be
  omitted on load).  An unknown property name fails `validate()`.
* Python API: `model.add_spring_property(name, prop)` and
  `model.add_spring_support(point, stiffness=None, property=None,
  angle_deg=0.0)`.

### Engine

A named spring is a co-located fixed ground node plus a `zeroLength` from
the ground node to the real node.  The element has one material per active
local DOF, plus `-orient e1 e2` when the axes are not global.  The coupled
GLOBAL DOFs are left free at the real node (as for v0.8 springs) and are
registered for ground displacements, and the node counts as a support.  The
reported reaction is exact: `-R^T F_local(R u)` with the force law above.

Analysis participation (ETABS behaviour):

* Nonlinear springs act nonlinearly in these analyses:
  * static load cases, which run with Newton like compression-only
    line/area springs;
  * pushover and P-Delta/staged cases;
  * direct-integration time history, which runs with Newton like device
    links.
* Eigen-based analyses (modal, response spectrum, buckling, Ritz, the FNA
  basis) use the initial tangent:
  * k for linear DOFs and for the compression side of c-only;
  * the 1e-6 residual for an open gap;
  * the first curve slope at the origin for multilinear.
* In a two-stage P-Delta or corotational case, the reported spring reaction
  is the law evaluated on the case-increment displacement.  This is the
  same convention as the v0.22 compression-only springs, and it is exact
  only for linear springs.

### Link hysteresis types (`LinkMember.link_type`)

`LINK_TYPES` is now the 8 previous types followed by these 5.  All 5 use the
isolator layout: the link axis is vertical or zero length, the hysteresis
acts on BOTH horizontal shear directions, and the vertical direction is
elastic `kv` (default 1e7).  They count as nonlinear-static (Newton) and
device (TH Newton) links.  FNA rejects them, so use direct integration.

| type | params (required; optional=default) | OpenSees |
|---|---|---|
| `multilinear_kinematic` | `points`; `kv` | `MultiLinear` (symmetric; kinematic rule: unload at k0 over 2*F1, then the translated backbone).  A 2-point backbone is exactly bilinear kinematic hardening. |
| `multilinear_takeda` | `points`; `points_neg`, `kv` | `Hysteretic` with pinchX=pinchY=1, no damage, beta=0.  Unloading is parallel to k0; the branch then aims at the opposite peak, or at the opposite first point if that side never yielded. |
| `multilinear_pivot` | `points`; `points_neg`, `pinch_x=0.5`, `pinch_y=0.25`, `unload_beta=0`, `kv` | APPROXIMATION of the Dowell pivot model with the pinching `Hysteretic` material.  It unloads at `k0*mu^-unload_beta` to zero force (d0), reloads through the pinch point `(d0 + pinch_x (d* - d0), pinch_y Fmax)` with `d* = dmax - (1 - pinch_y) Fmax / k0`, then goes on to the peak.  The ETABS alpha/beta pivots are not reproduced literally. |
| `friction_spring` | `mu`; `k_init=1e5`, `kv` | `flatSliderBearing` with Coulomb friction: slip force `mu*N` (N = instantaneous compressive axial load), stick stiffness `k_init`, no restoring stiffness.  A Coulomb approximation of the ETABS friction spring (no preload or parallel spring). |
| `rubber_isolator_bouc_wen` | `k_init`, `qd`; `alpha1=0`, `alpha2=0`, `mu=2`, `eta=1`, `beta=0.5`, `gamma=0.5`, `kv` | `elastomericBearingBoucWen` with characteristic strength `qd`.  The large-displacement force tends to `qd + alpha1 k_init u`. |

* `points` and `points_neg` are `[[d, F], ...]` lists:
  * On the positive side d and F are > 0, and d moves strictly away from 0.
  * When `points_neg` is omitted it mirrors `points`; when given it must
    have the same count.
  * Takeda and pivot take 2..3 points per side (a `Hysteretic` limit).
    Kinematic takes any count >= 1.
* Validation rejects unknown or missing keys and requires the axis to be
  vertical or zero length.  Value ranges:
  * pivot: `0 < pinch_x, pinch_y <= 1` and `unload_beta >= 0`;
  * friction: `mu > 0` and `k_init > 0`;
  * Bouc-Wen: `k_init > 0`, `qd > 0`, `0 <= alpha1 < 1`, `alpha2 >= 0`,
    `mu, eta > 0` and `beta + gamma > 0`;
  * all types: `kv > 0`.

### Validation (`tests/test_springs_hysteresis.py`, 51 tests)

* Rotated linear spring (support angle, property angle, property matrix,
  inline angle): the base displacement equals `R^T K^-1 R H` to 1e-9.
* Compression-only foundation springs under overturning: the uplifting
  spring releases (|R| < 1e-4 M/a) and the other carries M/a (1e-5).  A
  linear spring gives +-M/(2a), and tension-only is the mirror.
* Gap spring: `u = -(P + k g)/(k (1 + 1e-6))` (1e-9).
* Multilinear spring:
  * static Newton points lie on the curve to 1e-9, including
    extrapolation;
  * pushover points satisfy `u = curve^-1(V) + V L^3/3EI` (1e-8);
  * the modal period is `2 pi sqrt(m (1/k0 + L^3/3EI))` (1e-6).
* Single-zeroLength cyclic drives (+-D):
  * kinematic: the unloading slope is k0 = 1e4, and the loop energy is
    `4 (Fy - k2 dy)(D - dy) = 2.0` (1e-6);
  * Takeda: F(0) = -33.33 toward the opposite yield point, then +30.0
    toward the opposite peak (1e-9);
  * pivot: the branch passes through the pinch point (0.001875, 37.5),
    where the slope kinks (1e-6); energy ranks 0 < pivot < Takeda <
    kinematic;
  * Bouc-Wen: F(0) = +-qd (1%), F(D) = qd + alpha1 k D (0.1%), and the
    energy is `4 qd (D - dy)` (3%);
  * friction: the slip force is `mu N` (0.1%), and the energy is
    `4 mu N (D - mu N/k)` (2%).
* Engine integration:
  * an isolated block on each material type has period `2 pi sqrt(m/4k0)`
    and elastic static drift `F/4k0`;
  * the bearing types run under gravity plus lateral load;
  * a TH with a nonlinear named spring runs;
  * round trips match, and validation rejects bad inputs.

## Pushover / nonlinear static with spring supports

A plastic hinge at a column base (`column_base` / `all_ends` with `My` /
`default_My`, or `asce41` `auto_m3`) connects the member end to a
DUPLICATE node.  The support's grounded spring (inline `SpringSupport`
stiffness or a named `spring_properties` entry) stays between the ground
node and the ORIGINAL support node.  So the spring, the base hinge and the
column act in SERIES.  This holds in pushover, nonlinear static and
nonlinear time history (all use `_build(hinge_case=...)`).

* Bug fixed: the duplicate's translations were tied to the original with
  `equalDOF`.  When the original carried a PARTIAL restraint on the tied
  dofs (ux sprung and so free, uy/uz fixed), openseespy 3.7.1's
  Transformation handler held the duplicate's free translation at zero.
  The spring was bypassed and the fixed-base stiffness was reported.  A
  dof dropped by Set Active DOFs is the same partial restraint, which is
  why a planar (UX, UZ, RY) `all_ends` portal read about 0 base shear.
* Rule (`skyframe.engine.hinge_ties.partial_sp_checker`): a hinge tie
  whose original is restrained on SOME but not ALL of ux/uy/uz (support
  restraints after sprung dofs are cleared, plus inactive dofs) uses the
  v0.19 stiff zeroLength tie element.  That element is k_tie = 1e8 x the
  member end stiffness, and its relative softening is about 1e-8.  Fully
  restrained and fully free originals keep the exact equalDOF.  Models
  without such a node are byte-identical.
* Not affected: fiber PMM hinges (no duplicate node), staged construction
  (no hinges), panel-zone ties (restrained joints are skipped), and spring
  models without hinges.
* Hand checks (`tests/test_pushover_springs.py`, with k_th = 10 x 6EI/L):
  * translational spring: `1/K = 1/k_s + L^3/3EI + L^2/k_th` (1e-6);
  * rotational spring: `1/K = L^2/k_r + L^3/3EI + L^2/k_th` (1e-6);
  * with hardening 0, `V_max = My/L` exactly;
  * a multilinear spring follows `u = curve^-1(V) + V (L^3/3EI + L^2/k_th)`;
  * a compression-only spring settles by `W/kz` under gravity;
  * in nonlinear static displacement control the base FX equals
    -lambda (the spring reaction is included);
  * the nonlinear TH period is `2 pi sqrt(m/K)` (2e-3);
  * auto_m3 and fiber W18x50 on a spring are in series (1e-6).

## Post-tensioning tendons (as loads) and hyperstatic case

ETABS "model tendons as loads" (analysis only, no design checks).  Code:
`skyframe/core/tendons.py` (mechanics, validation) and
`skyframe/engine/tendons.py` (load application, hyperstatic case).  With no
tendons and no hyperstatic cases every model dict and results JSON is
byte-identical to before (neither new key is emitted).

### Model

`BuildingModel.tendons` (list; `to_dict` key `"tendons"` only when
non-empty) holds tendon objects.  `validate()` normalises them in place to
the canonical form:

```
{"uid": "T1",
 "points": [[x, y, z], ...],     # >= 2 profile points (m)
 "sags": [s, ...],               # one per segment, m (0 = straight)
 "drape_dir": [0, 0, -1],        # sag direction (default: down)
 "material": "A416Gr270",        # model material, else default library
 "area": 0.00099,                # m^2, > 0
 "jacking_stress": 1.395e6,      # kPa, > 0;  P0 = jacking_stress * area
 "jacking_end": "start" | "end" | "both",
 "losses": {"friction_mu": 0, "wobble_k": 0,      # -, 1/m
            "anchor_set": 0,                       # m (draw-in)
            "long_term_fraction": 0},              # 0 <= f < 1
 "host": ["B1", ...],            # frame uids OR shell-region uids
 "pattern": "PT",                # existing load pattern
 "n_sub": 16}                    # sub-pieces per segment, 1..200
```

Input shortcuts: `"profile": {"start", "end", "sag"}` (one parabola) or a
list of contiguous such objects (`"segments"` is an alias); `"host"` may be
a single uid string; `sags` defaults to zeros (a polyline).

`BuildingModel.hyperstatic_cases` (`{name: {"case": <static case>}}`,
emitted only when non-empty) adds the case kind `"hyperstatic"` to
`case_kinds()`.  The case can be listed in `cases_not_run`; a running
hyperstatic case runs its static case as a dependency.

Validation rejects: bad or duplicate uid, unknown pattern or material,
zero-length segment, a drape_dir parallel to the chord of a curved
segment, area/jacking_stress <= 0, unknown jacking_end or losses keys,
negative losses, long_term_fraction >= 1, and mixed or unknown hosts.  It
also rejects axial-only host members and a tendon point that projects onto
no host member axis.  A hyperstatic case must reference an existing static
case that applies at least one tendon pattern, and its name must not be
used by another case.

### Mechanics

* Segment k runs from `A = points[k]` to `B = points[k+1]`, with chord
  `Lc = |B - A|`.  Its shape is
  `p(u) = A + u (B - A) + 4 s u (1 - u) d`, where `d` is the unit part of
  drape_dir perpendicular to the chord.
* The tendon force vector is `T(u) = P p'(u) / Lc` (small-slope basis):
  its chord component is P.  On a straight segment this is the exact
  tendon force.
* Losses are measured from the jacking end along the arc length `x`, with
  `alpha` the cumulative tangent-angle change (exact for parabolas, kinks
  included):
  * friction: `P_f = P0 exp(-(mu alpha + k x))`;
  * anchor set: `P = min(P_f, 2 P* - P_f)`, where
    `2 int max(P_f - P*, 0) dx = anchor_set * E * area`.  The set length
    is where `P_f = P*`, or the whole tendon;
  * `"both"`: the larger of the two one-end profiles;
  * long-term: `P_eff = (1 - long_term_fraction) P`.
* Equivalent loads: the tendon is cut into `n_sub` sub-pieces per segment,
  and also split where the host member changes.  Each sub-piece carries
  the P of its mid-point.  The concrete receives:
  * point forces `T_right - T_left` at every sub-piece end.  These are
    the anchors (`+T(0)` at the start, `-T(1)` at the end), kink forces
    and friction steps;
  * on curved pieces, the uniform curvature load `-(8 P s / Lc^2) d` per
    unit chord length (the uplift `w = 8 P e / L^2`).

  The set is exactly self-equilibrated (zero net force and moment).
* Frame hosts receive these loads as member loads (global point, partial
  UDL and concentrated moment) at the projections of the tendon points on
  the member axis, plus the eccentricity couples `(p - c) x F`.  The
  distributed couple of an eccentric curvature load is lumped at each
  sub-piece mid-point, which is the only discretisation error.
* Shell hosts get nodal loads at the nearest mesh node of the host
  regions, with the transfer couple.  Equilibrium is exact.  The lumping is
  intended for a straight plan path through the slab.
* Every analysis that applies the pattern applies the tendon loads, via
  `OpenSeesEngine._apply_pattern`.  This covers static, P-Delta, nonlinear
  static, model-wide P-Delta, combos (by superposition) and so on.  Two
  limitations: tendon loads carry no mass, and they are not seen by the
  closed-form buckling geometric stiffness or the story-split staged
  pattern.

### Results

* `results["tendons"][uid]` (only when tendons exist) contains:
  * `pattern`, `P0`, `length`;
  * `anchor_set_length: {"start", "end"}`;
  * `stations: {"s", "alpha", "P", "points"}`, at the sub-piece
    boundaries, with P effective after all losses;
  * `segment_uplift` (`8 P_avg s / Lc^2` per segment);
  * `equivalent_loads: {"points": [{point, force}],
    "lines": [{segment, u1, u2, force}], "net_force", "net_moment"}`.
* `results["hyperstatic"][name]` (only for run cases) contains
  `{"case", "tendon_patterns": {pattern: scale}, "reactions": {tag: [6]},
  "base", "members": {uid: {"x", "hosts_tendon", "total", "primary",
  "secondary"}}}`.  Each of total/primary/secondary is
  `{N, V2, V3, T, M2, M3}` at the member's output stations, in the
  engine's station sign convention.
  * Primary forces are those of the concrete section under the tendon
    force alone: `F = -T` at the tendon point p, so `N = -T.x` and
    `M = (p - c) x (-T)` in the local axes.  A tendon below the centroid
    therefore gives `M3 = -P e`.
  * At an anchor, a member end or an interior FE node, the station uses
    the sub-piece on the member side.
  * Secondary = total - primary.  Primary reactions are zero, so the
    secondary reactions are the PT case reactions.

### Validation (`tests/test_tendons.py`, 30 tests)

* Simply supported beam with a parabolic drape (P = 1000, e = 0.2,
  L = 10):
  * the uplift is `w = 8Pe/L^2 = 16`, and the anchors are
    `(+-P, 0, -4eP/L)` (1e-12);
  * `M3(x) = -P e(x)`, with midspan `-Pe = -200` (1e-12);
  * `N = -P` and `V2 = 4eP(L-2x)/L^2`;
  * secondary forces and reactions are zero (1e-8).
* Two-span continuous beam with sag e per span:
  * total `M = -3wLx/8 + wx^2/2` and primary `-P e(x)`;
  * secondary `Pe x/L`, which is Pe = 200 at the interior support;
  * secondary reactions `Pe/L, -2Pe/L, Pe/L` (1e-9).
* Linear transformation (interior eccentricity +0.1): total moments are
  unchanged to `(delta/L)^2`, and P delta moves from secondary to primary.
* Friction against hand at the quarter points: a parabola (exact arc
  length, `alpha = atan(4e/L) - atan(4e(L-2x)/L^2)`) and a harped polyline
  (kink `2 theta`, kink force `2 P sin(theta)`) (1e-9..1e-12).
* Anchor set length against the exact exponential hand solution (1e-4),
  and within 5% of the linear approximation `sqrt(delta E A / p)`.  The
  lost area equals `delta E A` when the set exceeds the tendon.  Both-end
  jacking is symmetric with minimum `P0 e^{-kL/2}`.  End jacking and the
  long-term fraction are covered.
* Self-equilibrium: a 3D multi-segment tendon (lateral eccentricity,
  kinks, all losses, both ends) has zero net force and moment (1e-9).  On a
  determinate 3D support set its reactions are zero, and the secondary
  N/V/M stations are zero to 1e-6 P L (torsion to the documented lumping
  tolerance).
* Coverage also includes: a host spanning two members split at the node,
  a slab host (reactions sum to zero, midspan camber
  `5 P e L^2 / 48 EI` to 3%), round trip, results keys, cases_not_run
  dependency, scale and combo superposition, validation errors and
  byte-identical defaults.

## Nonprismatic sections and per-joint panel zones

ETABS *Define > Frame Sections > Nonprismatic*
(`skyframe/core/nonprismatic.py`) and *Assign > Joint > Panel Zone*
(`skyframe/core/panelzones.py`).  Every default reproduces the earlier
results byte for byte: prismatic sections serialise exactly as before (no
new keys), and `joint_panel_zones` is left out of the JSON when empty.

### Nonprismatic section JSON

```json
"sections": {
  "NP": {"kind": "nonprismatic", "subdivisions": 16,
         "segments": [
           {"start_section": "DEEP", "end_section": "SHALLOW",
            "length": 1.5, "length_type": "absolute",
            "EI33": "cubic", "EI22": "linear"},
           {"start_section": "SHALLOW", "end_section": "SHALLOW",
            "length": 1.0, "length_type": "relative"}],
         "material": "C", "A": 0.24, "I33": 0.0128, "I22": 0.0018,
         "J": 0.0049, "b": 0.3, "h": 0.8,
         "mod_A": 1.0, "mod_I33": 1.0, "mod_I22": 1.0, "mod_J": 1.0}}
```

* `kind`: `"prismatic"` (the default, not emitted) or `"nonprismatic"`.
* `segments` run from end i to end j.  Each needs `start_section` and
  `end_section`, which must be existing **prismatic** sections with
  A, I33, I22 and J > 0 that all share **one** material.  Defaults:
  `length` 1.0 (must be > 0), `length_type` `"relative"`, and `EI33` /
  `EI22` `"linear"`.  Unknown keys are rejected.
* Segment lengths follow the ETABS rule.  Absolute lengths are metres.
  What is left of the member length is shared by the relative segments in
  proportion to their `length`.  A member is rejected (`ValueError` from
  `validate`) when:
  * its absolute lengths exceed the member length;
  * it has only absolute segments and they do not sum to the member
    length (tolerance 1e-6 m);
  * no length is left for the relative segments.
* `subdivisions` is an integer in [1, 200], default **16**.  It is the
  number of equal analysis sub-elements per **varying** segment.
* `material`, `A`, `I33`, `I22`, `J`, `b` and `h` are **representative**
  values copied from the i-end section by `resolve_sections` during
  `validate`.  They may be left out of hand-written JSON.  Consumers that
  need one value per section use them: drawing, panel-zone sizing,
  pushover hinge springs and design.
* The nonprismatic section's own `mod_A`, `mod_I33`, `mod_I22`, `mod_J`,
  `mod_mass` and `mod_weight` apply to the interpolated properties.  The
  referenced sections' own modifiers are not used.
* Not supported, rejected by `validate`:
  * frame shear deformation (`As2`, `As3`, `shear_deformation`);
  * axial-only (`axial_limit != "both"`) members using a varying section.
* Python helpers: `add_nonprismatic_section(model, name, segments,
  subdivisions=16, **mods)` and `nonprismatic_section(...)`.

### Property variation

Within a segment at fraction `s` (0 at its start, 1 at its end):

* A and J vary linearly;
* I33 and I22 follow `I(s) = (I0^(1/n) + s (I1^(1/n) - I0^(1/n)))^n`, with
  n = 1, 2 or 3 for `linear`, `parabolic` or `cubic` (the ETABS
  convention).  `cubic` is exact for a linear depth taper of a rectangle.

A **uniform** nonprismatic section is one whose start and end sections all
have identical (A, I33, I22, J).  It is never divided, so it gives the
same results as the prismatic section byte for byte.

### Engine

* **Sub-elements.** Every member with a varying section is cut at each
  segment boundary and into `subdivisions` equal pieces per varying
  segment.  The cuts go through the frame auto-mesh path, so:
  * the member joins `MeshedModel.auto_split`;
  * shell-node, Winkler and auto-mesh cuts merge with these cuts;
  * results are stitched back per **drawn** member: end forces, the 11
    stations or user output stations, and the exact deflection stations;
  * releases and rigid end offsets act at the drawn ends only, and
    offsets are kept when they fit in the end sub-elements.
* **Stiffness.** Each analysis segment is an `elasticBeamColumn` with the
  interpolated (A, I22, I33, J) at its midpoint, times the section
  modifiers.  The condensed FEF of released ends, thermal
  `N = E A_seg alpha dT` and the exact deflection recovery all use the
  segment's own properties.  `run_virtual_work` integrates with the
  continuous properties at each station.
* **Weight.** Self-weight is an exact trapezoid `unit_weight * A(x)` over
  each segment, both in the engine and in staged construction.  Story
  self-weight, gravity totals, tables and story CM use the mean area
  `integral(A dx) / L` (`member_area`).
* **Mass.** In `element_self_mass` mode each analysis segment lumps
  `rho A(mid) l / 2` on each of its nodes, which is exact for the linear
  area.  The tributary story-mass path uses the trapezoids.
* **P-Delta** `iterative_loads` column strings share the story load by
  `E / integral(dx / A)`.
* **Buckling and Ritz** (`core/buckling.py`, `core/ritz.py`) run on
  `expanded_model(model)`.  This copy replaces each varying member by
  prismatic sub-members named `"<uid>#np<k>"`.  Member loads keep
  lumping to the drawn ends, and a drawn member's `base_N` applies to all
  of its sub-members.  Mode shapes include the internal nodes.

### Per-joint panel zones JSON

```json
"panel_zones": "none",
"joint_panel_zones": [
  {"point": [0.0, 0.0, 3.0], "property": "from_column",
   "connectivity": "beams_to_panel"},
  {"point": [6.0, 0.0, 3.0], "property": "elastic", "doubler_t": 0.02,
   "connectivity": "beams_to_panel"},
  {"point": [12.0, 0.0, 3.0], "property": "spring", "k": 20000.0,
   "connectivity": "beams_and_braces_to_panel"}]
```

The model-wide `panel_zones` string is unchanged.  The per-joint list is a
separate field, `joint_panel_zones`, because `panel_zones` is already the
model-wide setting.

* `property` (default `from_column`) sets the elastic scissors rotational
  stiffness `K_theta`, which acts about global rx and ry:
  * `from_column`: `G d_c d_b t_p`.  This is the same rule as model-wide
    `"scissors"`: the deepest vertical column gives `d_c = h` and
    `t_p = b`, and `d_b` is the deepest beam at the joint.
  * `elastic`: `G d_c d_b (t_p + doubler_t)`.  `doubler_t` must be >= 0
    and defaults to 0, which equals `from_column`.
  * `spring`: the given `k`, which is required and must be > 0.
  `k` is allowed only with `spring`, and `doubler_t` only with `elastic`.
* `connectivity` (default `beams_to_panel`) names the member kinds whose
  ends at the joint connect to the panel (the duplicate node):
  * `beams_to_panel`: beams.  This is the model-wide scissors topology.
  * `braces_to_panel`: braces.
  * `beams_and_braces_to_panel`: beams and braces.
  All other member ends stay on the joint node.
* Validation (`validate`) normalises the entries in place and rejects:
  * unknown keys;
  * a malformed point;
  * duplicate joints;
  * a joint without at least one panel-side member end and one other
    bending member end;
  * `from_column` or `elastic` without a vertical column and a beam that
    both have section b/h.

### Per-joint panel zones in the engine

* Joints without an override follow the model-wide setting exactly.
* `effective_specs` returns `compute_panel_zone_springs(model)` when
  `panel_zones == "scissors"` and `{}` otherwise.  Overrides then replace
  or add joints.
* With `"rigid"`, an overridden joint gets **no** automatic rigid end
  zones (`compute_panel_zone_offsets(model, skip=...)`): its spring
  replaces them, and every other joint keeps its rigid zones.
* Support or restrained joints are skipped with a `UserWarning`, as for
  model-wide scissors.
* Staged-construction submodels keep the overrides of the included
  stories.
* `OpenSeesEngine.panel_zone_joints()` lists every spring that was built
  (model-wide and overrides).

### Validation (`tests/test_nonprismatic_panelzones.py`, 39 tests)

* **Haunched cantilever** (depth 0.8 to 0.4 m, cubic, tip load).  The tip
  deflection is compared with the virtual-work integral
  `int P (L-x)^2 / (E I(x)) dx`:
  * the error is +0.16% at the default 16 sub-elements;
  * it converges as O(h^2): 11.2%, 2.69%, 0.66%, 0.16% and 0.041% for 2,
    4, 8, 16 and 32 sub-elements.
  The stations match `P (L-x)` exactly, the deflection stations equal the
  tip node, and parabolic variation also matches its integral (0.1% at
  n = 32).
* **Tapered pinned-pinned column** (depth 0.3 to 0.6 m, cubic) under
  linear buckling.  The reference is Euler with I(x), from a
  finite-difference solution of `EI(x) y'' + P y = 0`:
  * the error is -0.11% at the default (-1.9% at n = 4, -0.027% at
    n = 32);
  * the result lies between the Euler loads of the two end sections.
* **Uniform nonprismatic** (start = end, one or several segments).  Static
  results with self-weight, point, trapezoid and thermal loads and a
  release, and buckling, are byte-identical to the prismatic section.
* **Weight and mass.** Base FZ = `gamma int A dx` (1e-8), and the
  fixed-end moment = `int w(x) x dx` (1e-6).  Story mass and element
  self-mass equal `rho int A` (1e-10 / 1e-12), and the end-node mass is
  `rho A(mid) l / 2`.
* **Drawn ends.** With releases Mi and Mj on a symmetric haunch, the end
  moments are 0, `M(mid) = wL^2/8` and the deflection is symmetric.  A
  rigid i-offset gives the virtual-work integral over the clear span
  (0.2%).  A thermal load gives free elongation `alpha dT L` with N = 0.
* **Panel zones.**
  * Without overrides the spec is exactly the model-wide one.
  * On a single-joint frame, model-wide scissors equals a `from_column`
    override byte for byte.
  * An override on the middle joint of a 2-bay frame has the model-wide K
    there, leaves the other joints centerline, and gives a drift between
    the centerline and all-scissors models.
  * A spring panel zone rotates by `M/k` (1e-8), and the tip deflection
    matches the column + spring + beam hand value (0.1%).
  * `elastic` gives `G d_c d_b (t_p + t_d)`.
  * Under `"rigid"` the overridden joint loses its offsets while the
    other joints keep theirs.
  * The brace and beam connectivity variants redirect the right member
    ends.
  * Support joints warn and are skipped.
  * Round trips match, and validation rejects bad inputs.

## Temperature gradients, projected loads and auto-lateral generators (ASCE 7-22, EC8, IS 1893, user)

v1.16 load GENERATION (gap audit D2 + D4).  Bulk logic lives in
`skyframe/core/thermal_ext.py` (data, serialization, validation,
`projected_factor`), `skyframe/engine/thermal_ext.py` (engine loads and
shell output correction) and `skyframe/core/autolateral.py` (generators).
Every default reproduces the pre-v1.16 results and model JSON byte for
byte: zero gradients, `projected=False`, empty shell / joint temperature
lists and `ecc=0` add no keys and no loads (multiplying by the projected
factor 1.0 is exact).

### Frame temperature gradients

`ThermalLoad` gains `grad2` and `grad3` (deg C per metre along the member
local 2 / local 3 axis, ETABS "Temperature Gradient 2-2 / 3-3"; i.e.
`dT_across_depth / h`).  JSON keys appear only when non-zero.

* Free thermal curvature: a fibre at local `y` strains `alpha grad2 y`.
  The member curves away from the hot face: `v'' = -alpha grad2` (local 2
  deflection) and `w'' = -alpha grad3` (local 3).
* Fully restrained fixed-end moments: `Mz0 = E I33 alpha grad2` and
  `My0 = E I22 alpha grad3`.  The local fixed-end vector is
  `f0[5] = -Mz0, f0[11] = +Mz0, f0[4] = +My0, f0[10] = -My0`.
  * Releases condense exactly as in `_condensed_fef`.
  * It is applied REVERSED as nodal loads and added to the segment
    end-force correction (`_seg_fef`), the same path as `_apply_thermal`.
  * Interior segment nodes cancel.
* Results:
  * a free cantilever rotates `alpha g L` at the tip and deflects
    `alpha g L^2 / 2`, with zero internal force;
  * a fixed-fixed member has zero displacement and the constant moment
    `E I alpha g`;
  * a propped cantilever has prop force `3 E I alpha g / (2 L)`.
* Deflection stations: a `("kappa", (0, -Mz0, -My0), 0.0)` span record
  adds the free curvature to the v0.16 closed-form deflection stations,
  which then follow the exact parabola.  The statics, consistent-load and
  bending-moment helpers skip this record.
* Scope: axial-only (Truss) members take no gradient.  Staged-user group
  scaling scales `grad2`/`grad3` together with `dT`.
* `alpha` is the material `alpha`, or else `model.thermal_alpha`.
* `add_thermal_load(pattern, uid, dT, grad2=0, grad3=0)`.
  `POST /api/pattern/thermal` accepts `grad2` / `grad3` per load; `dT`
  may then be omitted (= 0).

### Joint-pattern temperatures

`LoadPattern.joint_temperatures = [JointTemperature(point, dT)]`; the JSON
key `joint_temperatures` is `[{point: [x, y, z], dT}]`.

* A frame member takes the average of its two end-joint values (a joint
  with no value counts as 0) as a uniform `dT` through `_apply_thermal`.
* This is exact for the axial statics of a prismatic member under a
  linear temperature: the free elongation is `alpha T_avg L` and the
  restrained force is `E A alpha T_avg`.
* Shells do not read joint temperatures; use `ShellThermalLoad` for them.
* `POST /api/pattern/joint-temperature {pattern, loads: [{point, dT}]}`.

### Shell temperatures

`LoadPattern.shell_thermal_loads = [ShellThermalLoad(region_uid, dT=0,
grad3=0)]`.

* `dT` is uniform (membrane expansion).
* `grad3` is in deg C/m through the thickness along the region local 3,
  the corner-ordering normal; the +3 face is hotter for `grad3 > 0`.
* Membrane regions are rejected.

Equivalent loads use the boundary-traction form.  For each mesh element,
taken CCW about its own normal `n_q`, each edge A->B adds:

* `N_th Le/2 n_out` at A and B, with `N_th = E_m t alpha dT / (1 - nu)`
  and `n_out = edge_hat x n_q`;
* the couple `m_th/2 (B - A)` at A and B, with
  `m_th = s E_b t^3 alpha grad3 / (12 (1 - nu))` (`s = +1` when `n_q`
  agrees with the region local 3).

Parameters and scope:

* `E_m = E_b = E mod` (times the cracked-slab factor).  With uniform
  shell modifiers, `f11` scales the membrane and `m11` the bending.  With
  orthotropic modifiers, the mean 11/22 factors are used (documented
  approximation).
* Interior edges cancel, so the result is exact for the constant-stress
  patch: a free plate strains `alpha dT` and curves `alpha grad3`
  isotropically.
* Linear static shell-force output subtracts the isotropic thermal
  resultants (`Nxx, Nyy -= N_th`; `Mxx, Myy -= m_th`), so:
  * a free plate reports zero;
  * a clamped plate reports `-N_th` and `m_th` magnitudes.
* Geometric-nonlinear and other analysis paths apply the loads but do
  not apply this output correction.
* `POST /api/pattern/shell-thermal {pattern, loads: [{region_uid, dT?,
  grad3?}]}`.

### Projected frame loads

`MemberLoad.projected` (bool, JSON key only when true) applies to
`udl`/`trapezoid` loads in the `gravity` and `global_x|y|z` directions;
other kinds and directions are rejected.

* The value is per unit length projected on the plane normal to the load
  direction, so the engine applies `w sqrt(1 - (x . d)^2)` per true length.
  For gravity on a rafter at angle theta, the total is `w L cos(theta)`.
* The same factor enters:
  * `_applied_gravity_fz`;
  * mass-from-loads (`compute_story_masses`);
  * the engine's load-mass distribution and mass-centre weights.

### Accidental eccentricity sign

`LoadPattern.ecc` may now be negative: the ETABS "- eccentricity"
variant, with story torque `Mz = F ecc B_perp` carrying that sign.
Validation requires a finite value only.

### Auto lateral generators (`skyframe.core.autolateral`)

`compute(model, code, **params)` returns the hand-calculation summary
`{"code", "direction", "T", "W", "V", <coefficients>, "stories": [{"story",
"h", "w", "F"}]}`.  `generate(model, code, name=None, ecc=0, **params)`
writes a story-force pattern (kind `"quake"`, or `"wind"` for the wind
generator) the way `codes.asce7_elf` does: it replaces any same-name
pattern, sets `fx` for direction X and `fy` for Y, and `ecc != 0` sets
`accidental_torsion=True, ecc=ecc`.

* Weights: `W_i = story mass * g`, or an explicit `weights={story: kN}`.
* Heights: `h_i = Story.elevation`.
* The generators are one-shot, as in v0.7: the forces are stored and not
  regenerated at run time.

Codes:

* `asce7_22`: `SDS, SD1, R, Ie=1, TL=8, S1=0, Ct=0.0466, x=0.9, T=None,
  mprs=None, vs30=None, method=None, short_period_cap=False`.
  * Period (§12.8.2): `Ta = Ct hn^x`.  A supplied T is capped at `Cu Ta`,
    with Cu from Table 12.8-1 (keyed on SD1), linear between rows.
  * `method` 2 (default without `mprs`; §12.8.1.1 Method 2, two-period
    spectrum §11.4.5.2): `Sa = SDS` (T <= Ts), `SD1/T` (T <= TL),
    `SD1 TL/T^2` beyond; `Cs = Sa/(R/Ie)`.
  * `method` 1 (default with `mprs = [[T, Sa], ...]`, the multi-period
    DESIGN spectrum of §11.4.5.1, e.g. the 22-period USGS table):
    Eq. 12.8-2 `Cs = Sa(T)/(R/Ie)`, Sa linearly interpolated between the
    tabulated periods.  Where T is below the period of maximum Sa
    (`T_peak`, the first period reaching it) the maximum Sa is used.
    No SDS cap and no SD1/T cap apply.
  * Beyond the last tabulated period Te (10 s for USGS), per §11.4.5.1
    as revised by Supplement 3: `Sa(Te) Te/T` (T <= TL) and
    `Sa(Te) Te TL/T^2` (T > TL) when TL >= Te, else `Sa(Te) (Te/T)^2`.
    A table ending before 10 s is extended from its own last point
    (documented generalisation).
  * SDS / SD1 with `mprs`: given values win (the USGS-reported SDS/SD1,
    which ETABS also takes as input).  Otherwise §21.4:
    * `SDS = 0.9 max Sa` over 0.2 to 5 s (`mprs_sds`).
    * `SD1 = 0.9 max T Sa(T)` over 1 to 2 s when `vs30 > 1450 ft/s`
      (441.96 m/s), else over 1 to 5 s, and at least `Sa(1 s)`
      (`mprs_sd1`; `vs30` in m/s is required when SD1 is not given).
    * The maximum is exact on the interpolated spectrum, including each
      segment's parabola vertex.
  * `method=2` with `mprs` uses the two-period form with those SDS/SD1;
    ICC/SKGA read §12.8.1.1 as permitting either method.
  * Lower bounds (both methods): `Cs >= max(0.044 SDS Ie, 0.01)`, and
    `Cs >= 0.5 S1/(R/Ie)` when `S1 >= 0.6`.
  * `short_period_cap` (§12.8.1.3, Method 2 only): Cs is computed with
    `max(min(SDS, 1.0), 0.7 SDS)` in place of SDS.  It requires T <= 0.5 s
    and at most 5 stories; the remaining §12.8.1.3 conditions are the
    user's responsibility.
  * Summary keys (only with `mprs`): `method`, `SD1`,
    `SDS_from_mprs`/`SD1_from_mprs` (when derived), and for Method 1
    `Sa_T`, `Sa_max`, `T_peak`.  With the cap: `SDS_cs`.
  * Vertical distribution uses `w h^k`, with k = 1 / 2 and linear between.
* `ec8` (EN 1998-1 §4.3.3.2): `ag` (in g, times `gamma_I`), `q`,
  `ground_type A-E`, `spectrum_type 1|2` (Tables 3.2/3.3; `S/TB/TC/TD`
  may be overridden), `beta=0.2`.
  * `T1 = Ct H^0.75` (0.085 / 0.075 / 0.050), unless given.
  * `Fb = Sd(T1) W lambda`, where `lambda = 0.85` when T1 <= 2 TC and
    there are more than two storeys.
  * `distribution`: `"height"` (z m) or `"mode"` (`mode_shape` `{story:
    s}`).
* `is1893` (2016): `Z, R, I=1, soil I|II|III, T=None, structure
  rc_mrf|steel_mrf|other, d=None, damping_factor=1`.
  * Sa/g follows Fig. 2a (static method).
  * `Ah = (Z/2)(Sa/g)/(R/I)`, at least `Z/2` when T <= 0.1 s.
  * `Vb = max(Ah W, rho W)`, with Table 7 rho (0.7/1.1/1.6/2.4 %, linear
    in Z).
  * Distribution: `Qi = Vb Wi hi^2 / sum Wj hj^2`.
  * For `other`, `T = 0.09 h / sqrt(d)`, where d defaults to the plan
    extent along the load.
* `user_coefficient`: `V = C W`, distributed by `w h^k`.
* `user_loads`: `loads = [{story, fx, fy}]`, applied verbatim.
* `asce7_22_wind` (optional 7-22 directional MWFRS, Ch. 27): `V` (m/s),
  `exposure B|C|D`, `Kzt=1`, `Kd=0.85`, `ze=0`, `cp_total=1.3`,
  `Ke=None`, `G=1.0`, `cp_windward=None`, `cp_leeward=None`.
  * `qz = 0.613 Kz Kzt Ke V^2` (Pa; Eq. 26.10-1.SI).  Kd is not in qz.
  * `Kz = 2.41 (z/zg)^(2/alpha)` (Table 26.10-1), with alpha/zg from
    7-22 Table 26.11-1: B 7.5 / 3280 ft, C 9.8 / 2460 ft, D 11.5 /
    1935 ft.  For z < 15 ft, z = 15 ft is used (every exposure); above
    zg the value stays 2.41.
  * `Ke = exp(-0.000119 ze)` (Table 26.9-1, ze in m), or the given `Ke`
    (e.g. 1.0).
  * Combined form (default): `p = qz Kd G cp_total`.  With the defaults
    `G = 1` and `cp_total = 1.3` the gust factor is folded in, as in
    `make_wind_pattern`, and the leeward suction is taken with qz.
  * Split form (`cp_windward` and `cp_leeward` both given): `p_z = Kd G
    (qz Cp_w + qh |Cp_l|)`, the Eq. 27.3-1 net windward + leeward
    pressure; internal pressure cancels.  qh is taken at the top-story
    elevation.  Use `G = 0.85` (§26.11.1, rigid).
  * Each story's tributary facade area is as in `make_wind_pattern`.
  * Summary adds `qh` and `G` when the split form or `G != 1` is used.

#### ASCE 7-22 research note (v1.16 follow-up)

The full ASCE 7-22 text was not reachable (paywalled; ASCE Amplify, CSI,
Bentley, USGS and ICC pages all failed to download).  Each statement
below was checked against quoted search-result text from the sources
named.

Verified:

* Seismic, §11.4.5.1 multi-period design response spectrum:
  * Sa = 2/3 of the USGS multi-period MCER spectrum at 22 periods (0 to
    10 s).
  * Linear interpolation is used between the tabulated periods.
  * The beyond-10 s extension follows Supplement 3 (TL >= 10: `10/T`,
    then `10 TL/T^2`; TL < 10: `(10/T)^2`).
  * Sources: ASCE Amplify §11.4.5.1 and Supplement 3 snippets, the
    Oregon BCD SAM 24-03, and the QuakeManager ASCE 7-22 page.
* Seismic, §11.4.5.2 two-period spectrum:
  * Permitted where the USGS multi-period spectrum is unavailable, and
    for ELF Method 2.
  * Sources: ICC Building Safety Journal and S. K. Ghosh Associates,
    "Is continued use of the two-period design spectrum ...".
* Seismic, §21.4 SDS / SD1 definitions:
  * SDS = 90 % of max Sa over 0.2 to 5 s.
  * SD1 = 90 % of max T Sa over 1 to 2 s (vs > 1,450 ft/s = 442 m/s) or
    over 1 to 5 s (vs <= 1,450 ft/s), and at least Sa(1 s).
  * SMS = 1.5 SDS and SM1 = 1.5 SD1.  Because every operation is linear,
    applying the rule to the design spectrum gives SDS/SD1 directly.
  * Sources: the ICC/SKGA article and an ASCE 7-22 text excerpt.  These
    secondary sources disagree on whether the 90 % rule is labelled on
    SM1 or SD1; the numeric result is the same.
* Seismic, §12.8.1.1 Method 1 vs Method 2:
  * Method 1: Eq. 12.8-2 `Cs = Sa/(R/Ie)`, with Sa at T from §11.4.5.1.
    "Where T is less than the period at which Sa is maximum, the maximum
    value of Sa shall be used."
  * Method 2: the two-period form, with the SD1/T and SD1 TL/T^2 caps.
  * Lower bounds: `0.044 SDS Ie >= 0.01`, and `0.5 S1/(R/Ie)` for
    S1 >= 0.6 g.
  * Sources: the ICC "Method 2 versus Method 1" evaluation, a 7-22 text
    excerpt, the Simpson Strong-Tie SE blog (2026) ("lower bounds remain
    the same"), and RAM Elements / open-analysis notes.
* Seismic, §12.8.1.3: SDS = 1.0, but not less than 70 % of SDS (7-22
  wording; 7-16 used Ss = 1.5).  Source: 7-22 text excerpt.
* Seismic, ETABS:
  * v21.0.0 implemented ASCE 7-22 auto seismic and the response-spectrum
    function.
  * Method 1 references a response-spectrum function (the multi-period
    spectrum).
  * v22.1.0 exposed an SDS input for Method 1 (before that, SDS was
    input for Method 2 only).
  * v23.0.0 fixed Method 1 with T > Tmax (= Cu Ta).
  * SkyFrame mirrors these inputs: `mprs` is the function, SDS/SD1 may
    be given, and T is capped at Cu Ta.
  * Source: CSI release notes v21.0.0 / v22.1.0 / v23.0.0 / v23.1.0.
* Wind:
  * `Kz = 2.41 (z/zg)^(2/alpha)` for 15 ft <= z <= zg, with the z < 15 ft
    branch at 15 ft.  alpha/zg come from Table 26.11-1, which 7-22 changed
    (ASCE Amplify §26.10.1 snippet; Meca "ASCE 7-22 wind load changes").
  * Exposure B 7.5/3280 ft and D 11.5/1935 ft are confirmed by
    secondary sources.
  * Exposure C 9.8/2460 ft is confirmed indirectly: it reproduces the
    7-22 example value Kz(30 ft, C) = 0.98 and the 0-15 ft table row
    0.57 / 0.85 / 1.03.
  * Kd is removed from qz and applied in the pressure equations
    (Eq. 27.3-1 `p = q Kd G Cp - qi Kd (GCpi)`); Kd = 0.85 for MWFRS.
    Source: ICC "Demystifying Loads" (2024 IBC / ASCE 7-22).
  * Ke (Table 26.9-1) = `exp(-0.0000362 zg[ft])`, the same as 7-16;
    "permitted to take Ke = 1".
  * G = 0.85 for rigid buildings (§26.11.1).

Corrections made:

* The old Method 1 used `min(SDS, descending envelope)`.  It is now
  `Sa(T)`, with the max-Sa rule below the peak period.  This changes V
  whenever Sa(T) > 0.9 max Sa; in the 3-story MPRS test, 315 -> 350 kN.
* SDS from `mprs` is now the exact maximum, not a 0.01 s grid.  SD1 can
  now be derived (it was required before).
* The long-period tail now follows Supplement 3.  It was `1/T` beyond
  the last point.  Both rules give the same Sa up to TL when TL >= the
  last period.
* Kz is capped at zg.
* Optional additions: `Ke` override, `G`, and the split windward qz /
  leeward qh form.

Still caveated (not verified against primary text):

* Whether 7-22 kept the 0.5 S1 floor unchanged for Method 1.  Only
  secondary sources confirm it.
* The exact list of §12.8.1.3 eligibility conditions, beyond <= 5 stories
  and T <= 0.5 s.
* The full numeric 7-22 Table 26.10-1 above 30 ft.  The tests compare
  equation values rounded to two decimals.
* The 7-22 §27.1.5 minimum MWFRS load (16 psf) is not applied.
* The leeward Cp in the default combined `cp_total` (1.3) is a
  convention, not the L/B-dependent Fig. 27.3-1 value.

API:

* `POST /api/pattern/auto-lateral {code, name?, ecc?, <params>}` returns
  the model.
* `POST /api/pattern/auto-lateral/preview` returns the summary and leaves
  the model unchanged.
* Unknown codes and parameters return 400.

Validation (`tests/test_loads_v116.py`, 31 tests):

* Frame gradients:
  * cantilever tip rotation `alpha g L` = 2.4e-3 and deflection
    `alpha g L^2/2` = 4.8e-3, with parabolic stations, in both local
    planes;
  * fixed-fixed: zero displacement and `E I alpha g` = 24 (I33) / 6 (I22)
    kN m;
  * propped cantilever: R = 9 kN, M_A = 36 kN m;
  * an auto-meshed 3-segment member and a scaled case give the same
    answers.
* `dT + grad` superpose (480 kN, 24 kN m).  Joint temperatures
  10 / 30 C give `T_avg = 20`.
* Plates:
  * a determinate plate with a gradient: centre `alpha g a^2/4` =
    6.0e-4 m, corrected moments 0;
  * a clamped plate: `m = E t^3 alpha g/(12(1-nu))` = 114.2857 kN m/m;
  * a uniform dT: corner `alpha dT a`, clamped `N = -E t alpha dT/(1-nu)`;
  * a CW corner order flips the bow.
* Projected loads on a 30 deg rafter: total `w L cos30` = 10.3923 kN
  (12 kN unprojected); projected `global_x` gives `w L sin30`.
* 3-story example (elevations 4/7/10 m; W = 1000/1000/800 kN):
  * ASCE 7-22: V = 350 kN, F = 73.684 / 128.947 / 147.368 kN.  Also
    checked: the capped-period k = 1.009112 case (V = 202.615 kN), the TL
    branch, the 0.5 S1 floor (V = 140 kN) and the MPRS Method 1 (V =
    350 / 267.588 kN).
  * `tests/test_autolateral_asce22.py` (25 tests) uses a 22-period
    USGS-style table:
    * SDS = 0.945.
    * SD1 = 0.671011 (1-2 s, vertex at 1.840909 s) and 0.686942 (1-5 s).
    * Method 1 V = 360.1335 kN (Sa(T) = 1.028953 > SDS).
    * Max-Sa rule below the peak: V = 367.5 kN.
    * Floors: 0.066 / 0.075.
    * Method 2 from the spectrum: V = 330.75 kN.
    * §12.8.1.3 cap: 367.5 / 350 kN.
    * Long-period tail: 0.0277778 / 0.0363636 / 0.01875.
    * Kz is checked against Table 26.10-1 rounding (0.57, 0.85, 0.98,
      1.13, ...) and Ke against Table 26.9-1 (1.00 ... 0.80).
    * Split windward/leeward forces are checked by hand.
  * EC8: Fb = 446.25 kN, F = 93.947 / 164.408 / 187.895 kN.  Mode shape:
    74.375 / 173.542 / 198.333 kN.  The beta floor (140 kN) and the TB
    branch are also checked.
  * IS 1893: Vb = 302.4 kN, Q = 33.368 / 102.190 / 166.841 kN.  The
    rho minimum (19.6 kN) and the `other` period 0.284605 s are also
    checked.
  * User coefficient: V = 280 kN, F3 = 154.483 kN.  User loads are applied
    verbatim.
  * 7-22 wind: Kz(33 ft, C) = 0.99975, Ke(500 m) = 0.942236.
  * ±5 % eccentricity: the base torques straddle the no-eccentricity value
    and differ by `2 V ecc Ly`.
* API round trips pass, and the default shapes and results are
  byte-identical.

## User-defined hinges and hinge overwrites

ETABS Define > Frame Hinge Properties (user-defined backbones), Assign >
Frame > Hinges, and Assign > Frame > Hinge Overwrites (B10).  Logic:
`skyframe/core/user_hinges.py` (validation, (de)serialisation, envelope
conversion, state classification) and `skyframe/engine/user_hinges.py`
(OpenSees builders and per-step recording).  Every default reproduces the
previous results byte-identically: `hinge_properties` is emitted only when
non-empty, `hinge_overwrites` only when set, and a member whose `hinges` is
a string keeps the existing `"none" | "auto_m3" | "fiber_pmm"` meaning.  A
representative model (frame with column_base/all_ends/asce41 pushovers, a
nonlinear TH and a nonlinear static case) hashes identically before and
after, for both `to_dict()` and the full `run().to_dict()`.

### Model JSON

```json
"hinge_properties": {
  "BEAM_M3": {"type": "M3",
              "backbone": [[0, 0], [0, 1.0], [0.02, 1.2], [0.03, 0.6],
                           [0.05, 0.6]],
              "scale": {"yield_value": 250.0, "yield_deformation": null},
              "acceptance": {"IO": 0.005, "LS": 0.01, "CP": 0.015},
              "hysteresis": "takeda", "drop_strength": "drops",
              "k_elastic": null, "hysteresis_params": null},
  "COL_P": {"type": "P",
            "backbone": {"positive": [[0, 0], [0, 1.0], [0.01, 1.1]],
                         "negative": [[0, 0], [0, -0.5], [-0.005, -0.5]]},
            "scale": {"yield_value": 1500.0}, "hysteresis": "pivot"},
  "FIB": {"type": "PMM_fiber", "acceptance": {"IO": 0.004, "LS": 0.01,
                                              "CP": 0.02}}
},
"members": [{"uid": "B1", "...": "...",
             "hinges": [{"property": "BEAM_M3", "relative_distance": 0.05},
                        {"property": "BEAM_M3", "relative_distance": 0.95}],
             "hinge_overwrites": {"auto_subdivide": true,
                                  "relative_length": 0.1}}]
```

* `type`: `M3 | M2 | P | V2 | PMM_fiber`.  The hinge DOF is the member
  LOCAL DOF, using the engine's local axes (`_local_axes`): P = local 1,
  V2 = local 2, M2 = rotation about local 2 and M3 = rotation about
  local 3.  For a vertical column, local 3 is global X, so an X push bends
  the column about M2 and a Y push about M3.
* `backbone`: ETABS points A-B-C-D-E as `[d, f]`.
  * A must be `[0, 0]`.  Then 1 to 5 more points follow, at most 6 in all,
    with non-decreasing d and `f_B > 0`.
  * A list is symmetric.  `{"positive", "negative"}` is two-sided, and the
    negative side may be given with either sign because magnitudes are
    used.
  * `d` is PLASTIC deformation (ETABS convention: the hinge is rigid up to
    B).  The B deformation is ignored, so every point is shifted so that B
    sits at 0.
  * `PMM_fiber` needs no backbone.
* `scale`:
  * force = `f * yield_value` (default 1.0, i.e. absolute values);
  * deformation = `d * SF_d`, where `SF_d` = 1 for `yield_deformation`
    null (absolute), the given value, or `"auto"` = `F_B / k_member`
    (multiples of the member yield deformation).
  * `k_member` is `6EI33/L` (M3), `6EI22/L` (M2), `EA/L` (P) or
    `12EI33/L^3` (V2), using modifier-scaled properties.
* `acceptance` (optional): IO <= LS <= CP plastic deformations, in the same
  scaled units.  A missing level is never reached.
* `hysteresis`: `kinematic` (default) | `takeda` | `pivot` | `isotropic` |
  `concrete`.  Kinematic and isotropic need a symmetric backbone (OpenSees
  MultiLinear is symmetric).
* `drop_strength`: behaviour after the LAST backbone point (C or E).
  * `drops` (default, ETABS "drops to zero"): the strength drops to
    `UH_RESIDUAL = 1e-6 * F_B` and stays there.
  * `holds`: the last force is held.
* `k_elastic` (optional): hinge elastic stiffness.  The default is
  `UH_RIGID_FACTOR * k_member` with `UH_RIGID_FACTOR = 10`, the v0.5
  stiff-hinge n.
* `hysteresis_params` (optional; takeda/pivot/concrete): `pinch_x`,
  `pinch_y`, `damage1`, `damage2`, `beta`.  Defaults:
  * takeda: 1, 1, 0, 0, 0;
  * pivot: 0.5, 0.25, 0, 0, 0 (the B11 pivot defaults);
  * concrete: 0.8, 0.2, 0, 0, 0.
* `FrameMember.hinges` (Assign > Frame > Hinges) can now also be a LIST of
  `{"property", "relative_distance"}` with `relative_distance` in [0, 1],
  measured along the joint-to-joint length.
  * Several hinges per member are allowed (ETABS style, e.g. 0.05 / 0.95),
    but the same property cannot appear twice at the same distance.
  * A list REPLACES the case's automatic hinges for that member
    (column_base / all_ends `My` hinges, auto_m3 / fiber_pmm).  An EMPTY
    list means "no hinges".
  * Rules enforced by `validate()`:
    * unknown property names are rejected;
    * axial-only members cannot take hinges;
    * PMM_fiber hinges must sit at 0 or 1 and cannot be mixed with lumped
      hinges on the same member;
    * interior hinges (0 < d < 1) are rejected on members with rigid end
      offsets or insertion / joint offsets.
* `FrameMember.hinge_overwrites`: `{"auto_subdivide": bool,
  "relative_length": x}` with `0 < x <= 0.5`.
  * `relative_length` sets the HingeRadau plastic-hinge length of a
    PMM_fiber member: `lp = relative_length * L`, instead of `0.5 h`.  For
    lumped (M3/M2/P/V2) hinges it is RESERVED and has no effect, because a
    zeroLength hinge has no length.
  * `auto_subdivide` is accepted and stored but RESERVED: an interior
    lumped hinge always subdivides the member (the mesher adds a node at
    `d * L`), and end hinges never do.
* Python API: `model.add_hinge_property(name, prop)`, then set
  `member.hinges = [...]` and `member.hinge_overwrites = {...}`, or pass
  `add_member(..., hinges=[...])`.

### Engine

* User hinges are active in every HINGED build (`_build(hinge_case=...)`):
  pushover, nonlinear static (including chains and `start_from`) and
  nonlinear time history.
* Linear, eigen, RS and buckling analyses see the elastic member.  An
  interior hinge location only adds a mesh node, which leaves linear results
  unchanged.  Tested: the tip displacement matches the unsplit member to
  1e-9.
* Lumped hinge: a `zeroLength` element between the member's node at the
  hinge location and a DUPLICATED node that the member segment connects
  to instead.  The original node is the joint or support at an end, or the
  split node inside the span.
  * `-orient` uses the member local x and y axes.  All six local DOFs carry
    a material: the hinge DOF gets the backbone material, and the other
    five get `Elastic(UH_TIE_FACTOR * k)` with `UH_TIE_FACTOR = 1e8`, k =
    `EA/L + 12EI/L^3` for translations and `max(6EI22, 6EI33, GJ)/L` for
    rotations.
  * These are springs rather than equalDOF ties, so a hinge at a
    rigid-diaphragm slave never chains MP constraints.
  * Deformation sign: the zeroLength convention (duplicate minus original,
    along the local axis).  An asymmetric backbone's positive side is the
    positive zeroLength deformation.
* Envelope (`core.user_hinges.build_envelope`): each scaled plastic point
  becomes the TOTAL hinge deformation `d = d_p + F/k_e`.
  * A non-increasing step, i.e. a vertical drop such as C -> D at equal
    deformation, is spread over the slope `-UH_DROP_RATIO * k_member` with
    `UH_DROP_RATIO = 0.1`.  A vertical drop has no tangent, and the
    spread slope stays softer than the member, so a cantilever cannot
    snap back.
  * `drops` appends `(d_last + (F_last - F_res)/k_drop, F_res)` and a flat
    tail.  `holds` appends a flat tail at `F_last`.
  * The material therefore has at most 7 points per side.
* Material:
  * `kinematic` uses `MultiLinear`, OpenSees' kinematic multi-linear
    material.
  * `isotropic` is APPROXIMATED by the same MultiLinear: OpenSees has no
    multi-linear isotropic uniaxial material.  The monotonic envelope is
    exact, but on reversal the elastic range is `2 F_y` (kinematic) instead
    of `2 F_max`.  The results carry an `approximation` note.
  * `takeda`, `pivot` and `concrete` use the classic `Hysteretic` when
    both sides have the same 2 or 3 envelope points.  Otherwise they use
    `HystereticSM`, the up-to-7-point generalisation, which is identical
    to Hysteretic on a 3-point envelope (probed).  Both take
    `pinch_x/pinch_y`, `damage1/2` and `beta`.
  * Every backbone material is put in `Parallel` with
    `Elastic(UH_PARALLEL_RATIO * k_member)`, where `UH_PARALLEL_RATIO =
    1e-9`.  This keeps a plateau or failed hinge from making the tangent
    EXACTLY singular, which happens when an axial or shear hinge DOF has
    no other stiffness.  The force error is `<= 1e-9 * k_member * d`.
* Why `UH_RIGID_FACTOR = 10` and not "rigid": probing showed that with
  `k_e / k_plastic > ~1e3`, Newton cycles between the loading and
  unloading branches on a load reversal.  A cyclic chain that solves at
  10x and 30x fails at 100x.  Users who want a stiffer hinge can set
  `k_elastic`.
* PMM_fiber (references the existing v0.21 fiber machinery): in any hinged
  build the member becomes the v0.21 HingeRadau forceBeamColumn, with
  fibers at both ends.
  * The acceptance values (scaled; `"auto"` multiplies by the backbone
    thy) override IO/LS/CP of the ASCE 41 auto backbone.
  * When the auto generator cannot size the section, `My` falls back to
    `scale.yield_value`.
  * `lp` comes from `hinge_overwrites.relative_length`, as above.
  * Results are the v0.21 fiber entries, with `kind: "user_fiber"`.  The
    nonlinear TH does not record fiber detail, as before.
* Not supported: interior hinges with rigid / insertion offsets
  (rejected), and hinges on axial-only members (rejected).

### Results

User hinges append entries to the existing hinge lists:

* pushover `hinges`;
* nonlinear static `nonlinear.hinges`;
* nonlinear TH: a new top-level TH key `hinges`, emitted only when the
  model has user hinges.

Each entry looks like this:

```json
{"uid": "B1", "end": "i" | "j" | "0.05", "relative_distance": 0.05,
 "property": "BEAM_M3", "type": "M3", "user": true, "kind": "user",
 "My": F_B, "thy": d_B, "a": C_plastic, "b": last_point_plastic,
 "c": F_last / F_B, "IO": x|null, "LS": x|null, "CP": x|null,
 "k_elastic": k_e, "hysteresis": "takeda", "drop_strength": "drops",
 "material": "MultiLinear" | "Hysteretic" | "HystereticSM",
 "envelope": {"positive": [[d, F], ...], "negative": [[d, F], ...]},
 "rot": [...], "moment": [...], "rot_plastic": [...],
 "hinge_state": ["A-B", "B-IO", ...], "state": ["elastic", "IO", ...]}
```

* `rot` / `moment`: the hinge deformation and force per converged step.
  For P and V2 hinges these are a displacement and a force; the key names
  match the existing entries.
* `rot_plastic`: `max(0, |d| - |F|/k_e)`.
* `hinge_state` (ETABS): classified on `rot_plastic`, measured on the side
  of the deformation's sign.
  * `A-B` until the hinge yields.
  * Up to the C marker: `B-IO | IO-LS | LS-CP | >CP`, by the acceptance
    levels.
  * Past C: `C-D | D-E | >E`.  The markers are the material points' plastic
    deformations, so a spread vertical drop moves D.  Missing points are
    never reached.
* `state` is the legacy v0.19 vocabulary that existing consumers (the
  server `hinge_summary` and the web viewer) understand:
  * `A-B` -> `elastic`;
  * `B-IO` -> `IO`;
  * `IO-LS` -> `LS`;
  * `LS-CP` -> `CP`;
  * every other state -> `collapse`.
* `hinge_rotations` (peak |d| per uid) includes M2/M3 user hinges.
  `yielded` (nonlinear static / TH) includes every user hinge whose state
  left A-B.

### Validation (`tests/test_user_hinges.py`, 31 tests)

Cantilever with L = 3, b = 0.3 and E = 25 GPa, with a user M3 hinge at the
base (Y push):

* Pushover follows the backbone exactly.  At every step:
  * `theta = (d - V L^3/3EI)/L` equals the recorded hinge rotation
    (1e-6);
  * `V L` equals the envelope at theta (1e-6);
  * the capacity curve passes through the hand breakpoints
    `(M_X/L * L^3/3EI + (d_pX + M_X/k_e) L, M_X/L)` (1e-6).
* The C -> D strength drop reproduces the D plateau `V = M_D/L` (1e-6).
* A vertical C -> D drop is spread at `-0.1 k_member` (exact envelope
  points).  `drops` then fails the hinge after E (`>E` / `collapse`,
  V ~ 0).
* Acceptance-state transitions:
  * every step's state equals the hand classification of
    `d_p = theta - M/k_e`;
  * the sequence is A-B, B-IO, IO-LS, LS-CP, >CP, C-D, D-E;
  * the first yielded step is the first one past the hand `d_B`.
* Two hinges at 0.05 L / 0.95 L of a fixed-guided member:
  * the mechanism shear is `2 My/(0.9 L)` (1e-6);
  * the elastic stiffness is `1/(L^3/12EI + 2 (0.45 L)^2/k_e)` (1e-6);
  * the mesh splits at 0.15 / 2.85.
* A mid-height hinge gives `V = 2 My/L`.
* An M2 hinge (X push) gives `V = My/L`.
* A P hinge (nonlinear static on UZ) caps N at P_y, with elastic
  `N = d/(L/EA + 1/k_e)`.
* A V2 hinge caps V at V_y, with elastic `V = d/(L^3/3EI + 1/k_e)`.
* Cyclic, at material level and through a nonlinear static
  `start_from` chain:
  * both rules unload at k_e;
  * the structural unloading slope is `1/(L^3/3EI + L^2/k_e)` (1e-6);
  * kinematic re-yields at `F_max - 2 F_y` and then follows the hardening
    slope (1e-6);
  * takeda reloads from `theta_0 = theta_max - F_max/k_e` on the line to
    `(-theta_y, -F_y)` (1e-6).
* Material selection: MultiLinear / Hysteretic / HystereticSM.  Every
  hysteresis type reproduces the monotonic envelope (1e-9).
* Envelope conversion hand values, including `yield_deformation: "auto"`.
* Nonlinear TH: a `hinges` entry per step, with yielded uids, moments
  bounded by the envelope, and the peak rotation.
* User hinges replace the case's default_My hinge, and an empty list
  means no hinges.
* PMM_fiber reference: `lp = relative_length * L` versus the default
  `0.5 h`, plus the acceptance override.
* Round trip and validation errors (11 cases).
* Defaults byte-identical: identical model and pushover JSON with an
  unused property, and an unchanged mesh.

## Plot functions, floor response spectra and story response plots

ETABS *Display > Show Plot Functions* / *Story Response Plots* /
*Force-Stress Diagrams* (gap audit E1 + E3 + E2-lite).  Units SI (kN, m,
s, rad); modules `skyframe/core/plotfn.py` (model side, Nigam-Jennings
spectra) and `skyframe/engine/plotfn.py` (OpenSees recorder).

### Model: `TimeHistoryCase.output_requests` (optional)

```json
"th_cases": {"TH1": {..., "output_requests": {
    "joints": [[x, y, z], ...],   // FE node coordinates (1e-6 match)
    "links":  ["I1", ...],        // link uids
    "frames": ["C1", ...],        // frame member uids
    "hinges": true}}}             // every zeroLength hinge spring
```

Every key is optional; the key is OMITTED from `to_dict` when absent
(`null`), and an absent request is byte-identical to the pre-feature model
and results (verified by hashing the model + full `/api/analyze` JSON of a
3-story building with two TH cases before/after).  400 / ValueError on: a
non-object request, an unknown key, a joint that is not `[x, y, z]`
numbers, an unknown link / member uid, a non-boolean `hinges`.  A joint
snaps to the FE node at that point (1e-6), else to the nearest structural
node within 1 mm (display-unit round-off of a point typed in kip-in / ft);
farther than that the run fails (`no FE node at point ... (nor within
1 mm)`).  The reported `point` is always the requested one.
Direct integration only (`/api/analyze`, `run_time_history`); FNA
(`/api/analyze/fna`) ignores the request.

### Results: `th_cases[name].plot_functions` (only when requested)

All series have `len(t) + 1` samples: `plot_functions.t = [0, dt, ...,
n dt]` — sample 0 is the state after the held gravity stage, before the
transient (the legacy `t` starts at `dt`).

```json
"plot_functions": {
  "t": [0.0, 0.01, ...],
  "ground": {"acc":  {"UX": [...], "UY": [...], "UZ": [...]},   // m/s^2
             "vel":  {...}, "disp": {...}},     // exact integration of the
                                                // piecewise-linear record
  "joints": [{"point": [x, y, z], "node": 12,
              "disp": {"UX","UY","UZ","RX","RY","RZ": [...]},  // relative,
              "vel":  {... 6 dofs}, "acc": {... 6 dofs},       // past gravity
              "disp_abs": {"UX","UY","UZ"}, "vel_abs": {...},
              "acc_abs": {...}}],              // = relative + ground
  "links": {"I1": {"type": "isolator", "components": ["P", "V2", "V3"],
                   "deformation": {"P": [...], "V2": [...], "V3": [...]},
                   "force": {...},            // element basic quantities,
                   "work": [...]}},           // TOTAL (gravity included)
  "frames": {"C1": {"P_i", "V2_i", "V3_i", "T_i", "M2_i", "M3_i",
                    "P_j", ..., "M3_j": [...]}},   // local end forces,
                                                   // member_forces convention
  "hinges": {"C1:i": {"rotation": {"R2": [...], "R3": [...]},
                      "moment": {"M2": [...], "M3": [...]}}},  // if requested
  "units": {"disp": "m", "rot": "rad", "vel": "m/s", "acc": "m/s^2",
            "force": "kN", "moment": "kN*m", "work": "kN*m"}}
```

Link component labels (element dir order): elastic -> the non-zero
`U1..R3` global dofs; damper / gap / hook -> `["P"]`; zero-length
isolator / multilinear -> `["UX", "UY", "UZ"]`; finite-length (vertical
twoNodeLink) -> `["P", "V2", "V3"]`; 6-component bearings ->
`["P", "V2", "V3", "T", "M2", "M3"]`; otherwise `c1..cn`.  `work` is the
cumulative basic work `sum 0.5 (F_k + F_k+1).(d_k+1 - d_k)` (the
hysteresis-loop area); `work[-1] - sum F^2/(2 k0)` equals the energy
tracker's hysteretic energy (1e-9).

### `POST /api/plotfn/spectrum`

Body: `{case, point: [x, y, z] | "ground", damping: z | [z, ...]
(default [0.05]), direction?: "X"|"Y"|"Z" (default: the case
direction), periods?: [T, ...] (default: 0 + 99 log-spaced points 0.02 ..
5 s)}`, or a raw series `{accel: [...], dt, damping?, periods?}`.

The joint's ABSOLUTE acceleration comes from the last stored
`/api/analyze` results when that joint was recorded (`source:
"recorded"`); otherwise the case is re-run on a COPY of the model with
the joint added to its `output_requests` (`source: "rerun"`; the current
model is never mutated).  Response:

```json
{"case": "TH1", "point": [0, 0, 9], "direction": "X", "dt": 0.01,
 "source": "recorded", "pga": 3.1,
 "periods": [0.0, 0.02, ...],
 "spectra": [{"damping": 0.05, "Sa": [...], "Sv": [...], "Sd": [...],
              "PSa": [...], "PSv": [...]}],
 "method": "Nigam-Jennings exact piecewise-linear recurrence"}
```

`Sd = max|u|`, `Sv = max|v|` (relative), `Sa = max|2 z w v + w^2 u|`
(absolute acceleration), `PSv = w Sd`, `PSa = w^2 Sd`; `T = 0` gives
`Sa = PSa = pga`, `Sd = Sv = 0`.  Peaks over the sample instants; the
recurrence (Chopra Table 5.2.1) is exact for piecewise-linear input.  400
on an unknown case, a bad point / direction / damping (must be in
[0, 1)) / periods, or a series shorter than 2 samples.

### Frontend (no new backend data)

* *Display > Show Plot Functions...* — case, function list (joint DOF
  rel/abs, link force/deformation, frame force, hinge, base reaction,
  story displacement/drift, energy, ground), vs time or X-vs-Y
  (hysteresis), overlay, zoom/pan, CSV; floor-spectrum tab -> the
  endpoint above.
* *Display > Story Response Plots...* — max story displacement, drift
  ratio, story shear, overturning moment, story stiffness per
  case / combo / RS / TH and direction, from the existing `story`,
  `story_stiffness`, TH `peaks` blocks; drift limit line.
* *Display > Force/Stress Diagrams (3D)* — M3/V2/P diagrams from
  `member_stations` drawn on the 3D members, reaction arrows from
  `reactions`.

### Validation (tests/test_plotfn.py)

* Round trip / canonical form / 6 validation errors; joint snap within
  1 mm (identical series) and the error beyond it; defaults
  byte-identical model and TH results; recording never perturbs the
  legacy keys (identical JSON minus `plot_functions`).
* Series lengths `len(t) + 1`, uniform `dt`; tip joint == story
  displacement (1e-15); base joint: zero relative motion, absolute
  acceleration == the ground record; ground `v = a t`, `u = a t^2/2`
  exactly.
* Undamped SDOF: `|V_column| = m |a_abs|` at every sample; fixed-guided
  column `V = k u` and `M_base = V L/2`.
* Isolator block: trapezoidal loop area == `work`, `sum(work - F^2/2k0)`
  == energy tracker hysteretic energy (1e-9), elastic slope == k1 (1e-6).
* Nonlinear hinge series peak == `hinge_rotations` (1e-12); held gravity
  axial force in `P_i[0]`.
* Nigam-Jennings: step input (z = 0.05) and ramp (z = 0) == closed form
  (1e-13); sampled harmonic (z = 0, 0.05, 0.2) converges O(dt^2) to the
  closed-form transient + steady state; undamped step `Sd = 2 a0/w^2`;
  `PSa = w^2 Sd`, `T = 0 -> pga`; the SDOF engine peak displacement ==
  `Sd(T_sdof)` (2e-3, Newmark period elongation); floor spectrum of the
  base joint == ground spectrum (identical lists).
* API: model round trip, `/api/analyze` carries `plot_functions`,
  spectrum recorded vs rerun vs ground, raw-series mode, 400s.

## Edit and Select utilities (Replicate, Divide, Merge, Align, Move, Extrude, Join, Delete)

These are ETABS **Edit** menu operations. They only edit the model; no
analysis code changes. Each one is a pure function of the model JSON
(`skyframe.core.edit.apply_edit(model_dict, op, selection, params)` returns
`(new_model_dict, summary)`; `apply_edit_model(model, ...)` returns a
validated `BuildingModel`). The SPA mirrors the algorithms in
`js/edit_geom.js` for `?mock=1`.

### Endpoint

`POST /api/edit/<op>` with `op` one of `replicate | divide | merge_joints |
align | move | extrude | join | delete`.

```json
{"selection": {"members": ["B1"], "shells": [], "links": [],
               "points": [[x, y, z]]},
 "params": {...},
 "model": {...},          // optional: edit this model instead of the stored one
 "dry_run": false}        // true: return the result, keep the stored model
```

Response `200`: `{"model": <BuildingModel.to_dict()>, "summary": {...}}`.
The stored model is replaced unless `dry_run` is set. Response `400`
(`{"error"}`): unknown op, unknown selection uid, bad params, or an edited
model that fails `BuildingModel.from_dict` validation.

`summary` shape:

```json
{"op": "divide",
 "created":  {"members": [...], "shells": [...], "links": [...], "points": [[...]]},
 "deleted":  {"members": [...], "shells": [...], "links": [...], "points": [[...]]},
 "modified": {"members": [...], "shells": [...], "links": [...]},
 "warnings": ["..."],
 "new_selection": {"members": [...], "shells": [...], "links": [...], "points": [...]},
 ...op extras...}
```

Op extras:

* `replicate`: `count`.
* `divide`: `divided: {uid: [piece uids]}`. The first piece keeps the
  original uid.
* `merge_joints`: `merged: [{"to": [x,y,z], "from": [[...]]}]` and
  `merged_count`.
* `align`: `moved_points`, or `trimmed: [uids]` for `trim_extend`.
* `move`: `moved_points`.
* `join`: `joined: [{"kept", "removed", "joint"}]` and
  `skipped: [{"joint", "reason"}]`.
* `delete`: `point_records_deleted`.

New uids use the source uid's leading letters plus the next free integer
(`C1-A1` -> `C1`, `C2`, ...), unique across members, shells and links.

### Params

* **replicate**
  * `mode: "linear"`: `{dx, dy, dz, n}`. Copy k is translated by k·d.
  * `mode: "radial"`: `{center: [x,y,z], axis: "x"|"y"|"z", angle (deg), n}`.
    Copy k is rotated by k·angle.
  * `mode: "mirror"`: one of `{plane: "x"|"y"|"z", coord}`,
    `{p1: [x,y], p2: [x,y]}` (a vertical plane through a plan line), or
    `{point, normal}`.
  * `mode: "story"`: `{stories: [names]}`. Each object is copied from its
    own story to every target story, translated by the difference in
    story elevation, and gets the target story's name.
  * Options:
    * `assignments` (default true). False keeps only kind, section and
      geometry. True also copies group membership, pushover `My` and
      point assignments (supports, springs, masses, joint diaphragms,
      panel zones, group points) at selected points.
    * `loads` (default true). Copies member, area and thermal loads on
      copied objects, nodal / ground / temperature loads on selected
      points, and tendons whose hosts are all copied.
  * An object identical to an existing one (same joints) is skipped, with
    a warning.
  * Rotations and mirrors transform global-direction loads: a force is a
    polar vector, R·F; a moment is a pseudo-vector, det(R)·R·M. A
    direction that ends up oblique is split into per-axis entries.
  * Radial copies about Z add the angle to vertical frames' `angle`.
  * Mirror negates `angle`. It also reverses shell corners (c0 kept
    first) so they stay counter-clockwise, and swaps the opening u and v.
* **divide** (selected frames)
  * `mode: "n"`: `{n >= 2}`.
  * `mode: "distance"`: `{distance, from: "i"|"j"}`.
  * `mode: "intersections"`: `{tol = 1e-3}`. Splits at other objects'
    joints lying on the frame and at crossings with other frames.
  * What happens to each part of the frame:
    * Member loads are remapped exactly to the pieces: UDL span clipping,
      trapezoid interpolation, point and moment loads go to the piece
      that contains them.
    * Legacy `member_udls`, thermal loads, groups, tendon `host`
      (expanded in order) and pushover `My` are copied to every piece.
    * Hinge lists move to the piece that contains each
      `relative_distance`.
    * `Mi`, `rigid_i` and joint offset `i` stay on the first piece; `Mj`,
      `rigid_j` and joint offset `j` stay on the last piece.
* **merge_joints**
  * Params: `{tolerance = 0.005 m, <= 10}`.
  * Scope: the joints of the selection, or every joint when nothing is
    selected.
  * Clustering is transitive within the tolerance.
  * The joint kept is, in priority order: a joint with a support or
    spring, then the joint with the most connections, then the
    lexicographically smallest.
  * Everything at the merged joints follows: object ends and corners,
    supports, springs, masses, nodal / ground / temperature loads, joint
    diaphragms, panel zones and group points.
  * Clean-up after the merge:
    * zero-length and duplicate frames are deleted, with their
      references;
    * duplicate shell corners are removed, and shells left with fewer
      than 3 corners are deleted;
    * per-joint records are deduplicated (first wins).
* **align**
  * Point modes move the selected points, or the selection's joints when
    no points are selected:
    * `mode: "coordinate"`: `{axis, value}`;
    * `mode: "line"`: `{p1, p2}` (orthogonal projection);
    * `mode: "plane"`: `{point, normal}`.
  * `mode: "trim_extend"`: `{p1, p2, tol = 1e-3}`. Moves the end of each
    selected frame that is nearer the line to the frame's intersection
    with the line.
* **move**: `{dx, dy, dz}`.
  * The joints of the selection move. Unselected objects that share them
    stretch, as in ETABS.
  * Point records at those joints follow.
  * Tendons whose hosts are all selected translate.
  * Moved objects get their story recomputed.
* **extrude**
  * `mode: "points_to_frames"`: `{dx, dy, dz, n, kind?, section?}`.
    * Each point becomes a chain of n frames.
    * `kind` is automatic when omitted: vertical means column,
      horizontal means beam, anything else is a brace.
    * The default section is the first one whose name contains COL or
      BEAM.
  * `mode: "frames_to_shells"`: `{dx, dy, dz, n, section?, kind?,
    behavior = "shell", mesh_size = 1, delete_source = false}`.
    * Each frame becomes n quads `[pi, pj, pj+d, pi+d]`.
    * The result is a slab (corners counter-clockwise from +Z) when both
      the frame and d are horizontal, and a wall otherwise.
* **join** (selected frames). Two frames are joined when all of these
  hold:
  * they are collinear, meet end-to-end at a joint used by nothing else,
    and that joint carries no point record;
  * they have the same kind, section and assignments;
  * there is no moment release at the shared joint, and their
    temperature loads are equal.
  * The first frame keeps its uid. The loads of the second frame are
    remapped, and loads on a reversed piece are flipped. Equal legacy
    UDLs are kept once, and contiguous identical UDL pieces are
    coalesced, so divide followed by join is byte-identical.
* **delete**
  * Removes the selected objects and every reference to them: member,
    area and thermal loads, groups, pushover `My`, and tendon hosts.
    Tendons left without a host are dropped.
  * Removes point records at joints the deletion orphaned.
  * Selected points delete only the point records there.

### Joints

A joint is identified by its coordinates rounded to 1e-6 m. New
coordinates are rounded to 1e-9 m, so replicated grids stay exact.

Object stories are recomputed by geometry: the story whose level equals
the object's top z, or else the story whose height range contains it.

### Frontend (Edit / Select menus, Undo / Redo)

* **Edit menu**
  * Undo (Ctrl+Z), Redo (Ctrl+Y / Ctrl+Shift+Z).
  * Copy (Ctrl+C), and Paste (Ctrl+V) with an offset dialog. Paste is a
    linear replicate of the copied objects.
  * Replicate…, Divide Frames…, Merge Joints…, Align Points / Trim-Extend…,
    Move…, Extrude…, Join Frames, Delete.
* **Select menu**
  * Select All, Invert Selection, Select by Property, by Story, by Plane,
    Previous Selection, Clear Selection.
* **Undo history**
  * The history is client-side: snapshots of the model JSON, kept per
    top-level key so an edit stores only the keys it changed. It is
    capped at 50 steps.
  * Every model mutation (markDirty / clearDirty / sky:model-changed) is
    recorded, labelled with the last action name.
* Live mode posts the working model, then this endpoint. Mock mode runs
  the same algorithms client-side (`js/edit_geom.js`).

### Validation (tests/test_edit.py, 30 tests)

* Geometry is checked for every mode against closed-form coordinates.
* A bay replicated twice along X gives node displacements and reactions
  equal (1e-9) to the 3-bay frame built directly.
* A beam divided into 4 gives joint results equal (1e-7) to the undivided
  beam.
* Divide followed by join, and replicate followed by deleting the copies,
  are byte-identical round trips.
* The input model is never mutated.
* Defaults: no existing code path changed, so every pre-existing result
  is byte-identical.

## Curved frames, wall/floor mesh options, joint patterns and shell load sets

ETABS Draw > Draw Curved Beams, Assign > Shell > Wall Auto Mesh Options /
Floor Auto Mesh Options, Define > Joint Patterns and Define > Shell
Uniform Load Sets.  Logic: `skyframe/core/curved.py` (arc geometry,
validation, chord expansion), `skyframe/core/shellopts.py` (mesh options,
joint-pattern library, load sets, and `expand_for_analysis`, the
analysis-time model expansion) and `skyframe/engine/curved.py` (results
stitching).  Hooks into existing code are one line each:
`OpenSeesEngine.__init__` analyses `expand_for_analysis(model)`,
`core/mesh.py` takes the structured divisions from
`shellopts.structured_divisions`, and `core/polymesh.py` reads the floor
flags and steps.  Every default reproduces the previous results
byte-identically.  `expand_for_analysis` returns the SAME model object when
nothing in this section is used, every new JSON key is emitted only when
set, and with no options the meshers compute exactly the previous
`round(edge / mesh_size)` and `mesh_size` values.  A frame building and a
shell building (structured slab, polygon slab with an inline joint
pattern, wall with an opening) hash identically before and after, for
both `to_dict()` and `run().to_dict()`.

### Curved frames

```json
"members": [{"uid": "ARC", "kind": "beam", "section": "B",
             "pi": [-10, 0, 0], "pj": [10, 0, 0],
             "curve": {"type": "arc", "via": [0, 0, 10],
                       "segments": null, "local2": "in_plane"}},
            {"uid": "C", "...": "...",
             "curve": {"type": "arc", "center": [0, 0, 5],
                       "plane_normal": [0, 0, 1], "segments": 9,
                       "local2": "normal"}}]
```

* **Three-point form** (`via`): the circle through `pi`, `via` and `pj`.
  The arc runs from `pi` through `via` to `pj`.
* **Center form** (`center` + `plane_normal`):
  * `|pi - c| = |pj - c|` within 1e-6 relative;
  * both ends lie in the plane through `c` normal to `plane_normal`;
  * the arc runs counter-clockwise about `plane_normal`, so the sweep is
    in (0, 360) deg and flipping the normal gives the complementary arc.
* **`segments`**: the number of equal-angle chords.  `null` = automatic,
  `ceil(sweep / 10 deg)`, so a semicircle gets 18.
* **Analysis.** The drawn member is replaced by the straight chords
  `<uid>~0 .. <uid>~(n-1)` in the analysed copy. The user model is never
  mutated.
* **Chord local axes** (consistent along the arc):
  * local 1 runs along the chord;
  * `local2 = "in_plane"` (default): local 2 is radial, in the plane of
    curvature.  Its sign is chosen once per member to agree with the
    straight-member default, so a vertical-plane arch keeps local 2 "up"
    (the chord `angle` is exactly 0).  When the in-plane direction is
    horizontal (a plan-curved member), local 2 points to the centre;
  * `local2 = "normal"`: local 2 runs along the plane normal (a
    plan-curved beam then bends about local 3 under gravity, like a
    straight beam);
  * the member `angle` rotates on top of either convention.
* **What each chord inherits:**
  * the `Mi` / `Mj` releases and `rigid_i` / `rigid_j` go to the first /
    last chord only;
  * the section, foundation, additional mass, cardinal point, auto-mesh
    options and output stations are copied to every chord;
  * thermal loads and group membership are copied too.  Groups list the
    chords in the analysed copy.
* **Not supported** (ValueError):
  * `axial_limit` other than `"both"`;
  * `hinges` / `hinge_overwrites`;
  * `joint_offsets`, or `end_offsets = "auto"`;
  * a nonprismatic section;
  * tendons hosted on the member;
  * a uid that contains `~`, or an existing member uid that collides with
    a chord name.
* **Member loads** are expressed along the ARC:
  * `a` / `b` are fractions of the arc length, and `w` is per unit arc
    length;
  * each chord takes the part of the load on its arc interval, scaled by
    `(S / n) / chord_length`, so the totals are exact;
  * `projected` loads are not scaled, because the chord polygon has the
    arc's projection;
  * `point` / `moment` loads go to the chord that holds their arc
    fraction, at the same chord fraction;
  * legacy `member_udls` become chord `udl` loads.
* **Self weight and mass** act on the chord polygon.  The shortfall is
  `1 - sin(x)/x`, with x the half chord angle (0.13 % at 10 deg).

### Curved-frame results

`run().to_dict()["curved_frames"]` is emitted only when the model has
curved members:

```json
"curved_frames": {"ARC": {
  "chords": ["ARC~0", "..."], "points": [[-10, 0, 0], "..."],
  "s_nodes": [0.0, 1.745, "..."], "arc_length": 31.416, "radius": 10.0,
  "sweep_deg": 180.0, "center": [0, 0, 0], "plane_normal": [0, 1, 0],
  "segments": 18, "local2": "in_plane", "story": "", "section": "B",
  "kind": "beam",
  "cases":   {"W":  {"s": [], "N": [], "V2": [], "V3": [], "T": [],
                     "M2": [], "M3": [], "chord": [],
                     "nodes": {"s": [], "disp": [[0, 0, 0, 0, 0, 0]]}}},
  "combos":  {"C2": {"...": "same shape; envelopes add \"min\""}},
  "rs_cases": {}}}
```

* Station values are the chord stations, in chord local axes.  A chord
  station at chord distance `x` maps to `s = s_k + x (S/n) / c`.
* At each chord joint, `s` appears twice: the left and right values at
  the kink.
* `nodes.disp` holds the global displacements at the n+1 arc points.
* Chord rows of `results["members"]` carry `"curved_parent": uid`.  The
  per-chord `member_stations` / `member_forces` stay as they are.

### Wall / floor auto-mesh options (ShellRegion)

```json
"shells": [{"uid": "W1", "...": "...", "mesh_divisions": {"n1": 4, "n2": 5}},
           {"uid": "W2", "...": "...", "mesh_divisions": {"max_size": 0.7}},
           {"uid": "S1", "kind": "slab", "behavior": "shell", "...": "...",
            "floor_mesh": {"mode": "cookie_cut", "max_size": 1.5,
                           "at_beams": true, "at_walls": true,
                           "at_grids": false}}]
```

* **`mesh_divisions`** (walls and slabs, shell behavior):
  * Structured 4-corner mesher: `n1` cells along corner 0->1 and `n2`
    along 0->3.  `max_size` gives `ceil(edge / max_size)` per direction
    (the legacy rule is `round(edge / mesh_size)`).
  * Polygon mesher: the u / v interval steps are `bbox_u / n1` and
    `bbox_v / n2`.  Each interval between mandatory lines is still split
    into `ceil(len / step)` parts, so the mesh has at least n1 x n2
    cells.  `max_size` is the step in both directions.
  * Check Model's shell aspect-ratio test uses the same divisions.
* **`floor_mesh`** (slabs with shell behavior only; walls and membranes
  raise ValueError).  A slab with `floor_mesh` set takes the polygon
  mesher even with 4 corners.  Missing keys default to `"default"`,
  `null`, `true`, `true`, `true`.  The options map onto the polygon
  mesher's line sets:
  * `at_beams: false`: in-plane frame members are neither constraint
    segments nor forced lines.  Their ends on element edges still join
    through the conformity pass;
  * `at_walls: false`: traces of non-coplanar shells are dropped.  Edges
    of coplanar neighbours are always kept;
  * `at_grids: false`: no optional grid lines;
  * `mode "default"`: constraint segments cut the cells and force mesh
    lines through their ends (the existing polygon behaviour);
  * `mode "cookie_cut"`: the segments cut the cells but force no lines;
  * `mode "rectangular"`: the segments force lines through their ends
    but never cut cells, giving axis-aligned rectangles clipped only by
    the outline and openings;
  * `max_size` replaces `mesh_size` as the maximum element size.
    `mesh_divisions` takes priority over it.

### Joint patterns and shell uniform load sets

```json
"joint_patterns": {"HYDRO": {"type": "linear", "a": 0, "b": 0, "c": -9.81,
                             "d": 29.43, "zero_negative": true}},
"patterns": {"WP": {"area_loads": [{"region_uid": "W1", "q": 1.0,
                                    "joint_pattern": "HYDRO"}]}},
"shell_load_sets": {"OFFICE": {"SDL": 1.5, "LIVE": {"q": 2.4},
                               "WIND": {"q": 0.5, "direction": "local_3"}}},
"shells": [{"uid": "S1", "...": "...", "load_set": "OFFICE"}]
```

* **`AreaLoad.joint_pattern`** is either the inline dict (unchanged) or a
  NAME from `joint_patterns`.  The engine resolves the name to the
  library dict, so the results are identical to the inline form.  An
  unknown name raises ValueError.
* **Library entries** are normalised: a, b, c, d become floats, and the
  zero flags are kept only when true.
* **`ShellRegion.load_set`**: at analysis time each entry
  `{pattern: q | {"q", "direction"?, "projected"?}}` adds one `AreaLoad`
  to that pattern.  The added loads come after the pattern's own area
  loads, in shell order.
* **Load-set validation**:
  * every pattern must exist;
  * every entry must pass the AreaLoad rules (directions, projected, and
    membrane regions take uniform gravity only);
  * the set must exist.
* The user model keeps its `load_set` and joint-pattern names.  Only the
  analysed copy is expanded, so `OpenSeesEngine.source_model` is the user
  model and `.model` is the expanded copy.
* Model-level outputs computed from the user model, such as `to_dict()`
  story masses, do not include load-set loads.

### Validation (tests/test_curved_mesh_sets.py, 36 tests)

Bending-only closed forms (A = 10, I = 1e-3, R = 10, E = 2e8):

* **Two-hinged semicircular arch** under uniform projected load:
  H = 4wR/(3 pi) = 21.2207.
  * 6 chords: 21.199; 18 chords (auto): 21.328, +0.51 %; 72 chords:
    21.227, +0.03 %.  The error decreases monotonically.
  * Fixed supports with `Mi,Mj` releases reproduce it to 1e-6.
* **Fixed semicircular arch**: H = (wR/6)/(pi/2 - 4/pi) = 28.006 and end
  moment M = 2RH/pi - wR^2/4 = 53.28.  72 chords give 28.001 (0.02 %) and
  53.19 (0.17 %).
* **Ring under diametral load**:
  * shortening P R^3/EI (pi/4 - 2/pi) = 7.4389e-3; 3 / 9 / 18 chords per
    quarter give 7.048e-3 / 7.392e-3 / 7.427e-3 (0.16 %);
  * transverse growth P R^3/EI (2/pi - 1/2) = 6.831e-3; 18 chords give
    6.820e-3.
* **Quarter-circle plan cantilever**, tip load P out of plane:
  * delta = PR^3/EI pi/4 + PR^3/GJ (3pi/4 - 2) = 0.062423; 3 / 9 / 18 /
    36 chords give 0.05963 / 0.06210 / 0.06234 / 0.06240;
  * support reactions MX = MY = P R exactly;
  * stitched |M| at s = 0 equals P R sqrt(2) to 1e-9;
  * `local2 = "normal"` gives the same deflection, with the out-of-plane
    moment moved from M2 to M3.
* **Arc loads**: a udl, a partial trapezoid, a point load and a partial
  global-X udl give base totals equal to the arc integrals (1e-8).
* **Mesh options**:
  * wall `n1 x n2` = 4 x 5 gives the exact node lines; `max_size` 0.7 on
    6 x 3 gives 9 x 5;
  * the polygon `n1/n2` steps work and areas are conserved;
  * floor `at_beams`, `cookie_cut`, `rectangular`, `max_size` and
    `at_grids` are pinned by node-set tests;
  * a cookie-cut slab conserves q x A.
* **Joint patterns and load sets**:
  * a named joint pattern equals the inline one bit-for-bit;
  * a load set equals the explicit area loads, and on a membrane slab it
    goes to the beams;
  * JSON round trips and validation errors are covered.
* **Defaults**: `expand_for_analysis(m) is m`, and no new JSON keys are
  emitted.

## Shell element types, one-way slabs and shell benchmarks

Modules: `skyframe/core/shell_types.py` (type table, validation,
serialization), `skyframe/engine/shell_elements.py` (OpenSees wiring),
`skyframe/core/oneway.py` (one-way distribution),
`skyframe/engine/shell_benchmarks.py` (benchmark problems).
Tests: `tests/test_shell_types_oneway.py`.

### ShellSection.shell_type (ETABS Shell-Thin / Shell-Thick / Membrane / Plate)

`ShellSection.shell_type: str = "shell"` (appended field). JSON: the key
`"shell_type"` is emitted in `shell_sections[name]` ONLY when it is not
`"shell"`, so existing model JSON is unchanged; absent = `"shell"`.

| shell_type    | quad element | triangle element | stiffness carried |
|---------------|--------------|------------------|-------------------|
| `shell` (default) | ShellMITC4 | ShellDKGT | membrane + bending (legacy, byte-identical) |
| `shell_thin`  | ShellDKGQ (Kirchhoff) | ShellDKGT (Kirchhoff) | membrane + bending |
| `shell_thick` | ASDShellQ4 (Mindlin)  | ASDShellT3 (Mindlin)  | membrane + bending |
| `membrane`    | ASDShellQ4 | ASDShellT3 | membrane only |
| `plate_thin`  | ShellDKGQ  | ShellDKGT  | bending only |
| `plate_thick` | ASDShellQ4 | ASDShellT3 | bending only |

* `shell` reproduces the pre-feature model exactly: the same
  `ElasticMembranePlateSection` / modifier calls and ShellMITC4 quads
  with ShellDKGT polygon triangles. The SHA-256 of the results JSON and
  the model JSON of a representative model (shell wall, modified shell
  slab, polygon slab with triangles, two membrane slabs, 2 stories) is
  identical before and after the change.
* "Membrane only" and "bending only" use the existing exact shell-modifier
  path (`engine/shell_modifiers.py`). The dropped group is multiplied by
  `RESIDUAL = 1e-6`, never exactly 0, so the DOFs it would leave unstiffened
  stay non-singular. This plays the role of the ETABS automatic restraint
  of unstiffened DOFs.
  * `membrane` multiplies m11, m22, m12, v13 and v23 by 1e-6.
  * `plate_*` multiplies f11, f22 and f12 by 1e-6.
  * User modifiers multiply on top. With uniform user factors, the
    section stays a single `ElasticMembranePlateSection` with `Ep_mod`.
* A layered (nonlinear) section must use `shell`, `shell_thin` or
  `shell_thick`. The others raise `ValueError` at add or validate time,
  as does an unknown type.
* Drilling DOF:
  * ASDShellQ4 and ASDShellT3 carry a proper drilling stiffness, which is
    a membrane quantity and is kept by `membrane`.
  * A frame member framed into a `membrane` wall is therefore never
    singular. Out-of-plane, the junction behaves as a pin, as in ETABS.
  * Test: a beam framed perpendicular into a membrane wall has end moment
    below 1e-3 of PL/4 and midspan moment PL/4 (within 1e-3). The same
    beam framed into a `shell_thick` wall gets a fixed-end moment.
* Warping:
  * ASDShellQ4 handles warped quads; ShellMITC4 and ShellDKGQ do not
    (see the twisted-beam rows below).
  * The region mesher only produces planar quads, because region
    planarity is validated. Warping therefore appears only in curved or
    imported geometry; use `shell_thick` there.
* Result axes:
  * ASDShellQ4 is created with `-local` set to the ShellMITC4 local x
    axis (from the midpoint of side 1-4 to the midpoint of side 2-3).
    Its `shell_forces` resultants are therefore in the same element
    system and Gauss order as the legacy ShellMITC4 (verified to 1e-6).
  * ShellDKGQ reports in its own local system, which for rectangles is
    the ShellMITC4 system.
  * `shell_forces` now also accepts the ASDShellT3 3-Gauss-point
    response (24 values). Legacy elements return 32 values and give
    identical averages.
* Recommendation: `shell_types.RECOMMENDED_SHELL_TYPE = "shell_thick"`
  for NEW models. It is exposed only; the dataclass default stays
  `shell`, so existing models never change.
  * On quads, ASDShellQ4 is never worse than ShellMITC4 at 16x16 (by
    more than 0.01 percentage points).
  * It gives identical results on flat plates, where its bending field
    is MITC4.
  * It is far better on warped geometry (twisted beam −0.06 % against
    −20.8 %).
  * On triangles it is mixed. ASDShellT3 is better on thick plates and
    warped shells, but worse than ShellDKGT on thin flat plates at
    coarse meshes. Use `shell_thin` for thin flat polygon slabs.

### Shell benchmarks (convergence table)

`shell_benchmarks.PROBLEMS[name](element, N)` returns computed / reference.

**Common set-up**
* Each problem is a stand-alone OpenSees model on refinement level N with
  `ElasticMembranePlateSection` (kappa = 5/6).
* Triangle rows split every quad along its 0-2 diagonal.
* Pressure loads are lumped (cell area / 4 per node).

**Problems**

* **Scordelis-Lo roof**
  * R 25, L 50, 40°, t 0.25, E 4.32e8, nu 0, 90 per unit area.
  * Quarter model, N x N mesh.
  * Measured: vertical displacement at the midside of the free edge.
  * Reference: 0.3024.
* **Pinched cylinder with diaphragms**
  * R 300, L 600, t 3, E 3e6, nu 0.3, P 1.
  * Octant model, N x N mesh.
  * Reference: 1.8248e-5.
* **MacNeal-Harder twisted beam**
  * L 12, w 1.1, t 0.32, E 29e6, nu 0.22, 90° twist.
  * (N/2) x 3N mesh; N = 4 is the standard 2 x 12.
  * Unit tip load in-plane (reference 5.424e-3) and out-of-plane
    (reference 1.754e-3).
* **Thin plate**
  * Hard simply supported square plate, a/t = 1000, N x N mesh.
  * Measured: centre deflection.
  * Reference: the Navier series, alpha = 0.0040624 (0.00406 q a^4/D).
* **Thick plate**
  * Same plate at a/t = 10.
  * Reference: the Mindlin solution w = w_K + M_marcus/(kappa G t). The
    Marcus moment at the centre is 0.0737 q a^2, which gives
    w/w_K = 1.0519.
* **Morley 30° skew plate**
  * L 100, L/t = 1000, uniform q, w = 0 on all edges.
  * The rotation about each edge's in-plane normal is held by a penalty
    spring (hard simply supported).
  * Reference: 0.000408 q L^4/D.
  * The obtuse corners are singular, so every element converges slowly.
    ShellDKGQ is at −13.5 % at 16x16 and ShellMITC4 at −11.9 % at 48x48.
* **MacNeal-Harder straight cantilever, trapezoidal mesh**
  * 6 x 0.2 x 0.1, E 1e7, nu 0.3.
  * Mesh is 3N/2 x N/4; N = 4 is the standard 6 x 1. Interior lines are
    alternately skewed ±45°.
  * Unit tip shear in-plane (reference 0.1081) and out-of-plane
    (reference 0.4321).

Error in % at N = 4 / 8 / 16. The values are pinned in the tests to ±0.05
percentage points.

| problem | ShellMITC4 (`shell`) | ShellDKGQ (`shell_thin`) | ASDShellQ4 (`shell_thick`) | ShellDKGT | ASDShellT3 |
|---|---|---|---|---|---|
| Scordelis-Lo | −8.27 / −3.60 / −1.34 | +5.00 / +0.55 / −0.34 | +4.48 / +0.48 / −0.25 | −3.79 / −2.35 / −1.12 | +3.97 / +0.72 / −0.04 |
| pinched cylinder | −65.0 / −27.2 / −7.49 | −36.1 / −4.96 / +1.55 | −61.3 / −24.7 / −6.81 | −39.8 / −7.15 / +1.60 | −48.1 / −17.6 / −4.38 |
| twisted beam in-plane | −77.8 / −49.6 / −20.8 | −62.3 / −32.4 / +25.5 | −0.23 / −0.12 / −0.06 | −1.29 / −0.60 / −0.49 | −0.65 / −0.29 / −0.20 |
| twisted beam out-of-plane | −68.6 / −43.7 / −18.4 | −48.3 / −17.5 / +79.3 | +0.20 / +0.00 / −0.01 | −4.66 / −1.59 / −0.61 | −0.23 / −0.15 / −0.12 |
| SS thin plate | −2.30 / −0.51 / −0.13 | −0.41 / −0.06 / −0.01 | −2.30 / −0.51 / −0.13 | −4.61 / −1.05 / −0.25 | −16.2 / −4.17 / −1.03 |
| SS thick plate (Mindlin) | −1.93 / −0.43 / −0.11 | −5.32 / −4.98 / −4.94 | −1.93 / −0.43 / −0.11 | −9.30 / −5.92 / −5.16 | −13.8 / −3.24 / −0.78 |
| Morley skew | −39.6 / −27.2 / −19.6 | +44.7 / −4.98 / −13.5 | −39.6 / −27.2 / −19.6 | +31.3 / −10.4 / −20.1 | −26.7 / −30.2 / −26.1 |
| cantilever trapezoid in-plane | −97.6 / −93.3 / −79.1 | −19.4 / −6.86 / −2.02 | −94.9 / −89.9 / −75.0 | −82.3 / −53.5 / −25.1 | −56.5 / −35.4 / −26.0 |
| cantilever trapezoid out-of-plane | −3.67 / −1.43 / −0.58 | −1.27 / −0.72 / −0.48 | −3.69 / −1.44 / −0.58 | −2.26 / −1.26 / −0.76 | −3.96 / −1.73 / −0.82 |

**Reading the table**

* **Kirchhoff elements (DKGQ, DKGT)**
  * They stall about 5 % low on the thick plate, which is exactly the
    missing 5.2 % shear term. Use the `*_thick` types for a/t ≲ 20.
  * The DKGQ GQ12 membrane with drilling is by far the best on distorted
    in-plane bending (cantilever trapezoid in-plane).
  * DKGQ is unreliable on warped quads: on the twisted beam it
    overshoots and worsens with refinement (+25 % / +79 % at 16x16).
* **Bilinear membranes (ShellMITC4, ASDShellQ4)**
  * Both lock badly on distorted in-plane bending.
  * For deep in-plane bending of walls, use a fine regular mesh.
* **Pinched cylinder**
  * Every four-node element needs 16x16 or finer.

### One-way slabs (membrane slabs)

`ShellRegion.distribution: str = "two_way"` and
`ShellRegion.one_way_dir: int = 1` (appended fields). JSON keys
`"distribution"` and `"one_way_dir"` are emitted only when they are not
the default.

**Validation**

* `one_way` needs a convex 4-corner `kind="slab"`,
  `behavior="membrane"` region with no polygon openings.
* `one_way_dir` must be 1 or 2.
* The distribution must be `two_way` or `one_way`.
* Anything else raises `ValueError`.
* `two_way` is the existing 45° rule (unchanged).

**Rule** (`core/oneway.py`)

1. **Span direction d.** Region local 1 = unit(c1 − c0), and local 2 =
   n × local 1. `one_way_dir` selects d.
2. **Support edges.** These are the opposite pair (0,2) or (1,3) that d
   crosses most squarely: the larger Σ|t_k·p|, where p = n × d. On a
   tie, local 1 picks (1,3) and local 2 picks (0,2).
3. **Strips.** Strips run parallel to d.
   * A strip whose both ends lie on support edges sends half of
     q·ℓ·dη to each end.
   * A strip with only one supported end sends all of it to that end.
4. **Line load.** Support edge k gets w(s) = factor·ℓ(s)·|t_k·p| kN/m
   per kPa. This is piecewise linear between the corner projections,
   with a step where the factor changes.
5. **Other edges.** Non-support edges (the beams parallel to the span)
   receive nothing.
6. **Exactness.**
   * A parallelogram (or rectangle) is exact.
   * For other quads, strips that miss every support edge are restored
     by a uniform rescale, with a warning.
7. **Openings.** The total is reduced uniformly by the opening-area
   ratio, with a warning. This is the same approximation as the two-way
   rule.
8. **Beams and corners.**
   * Beams are found as in two-way: members collinear with the edge.
   * Any uncovered part of a support edge, including a support edge with
     no beams at all, sends its load to that edge's two corner nodes,
     with a warning.
   * The total is asserted to 1e-8.

**Hand checks (tests)**

* **6 x 4 rectangle, edge 0 along X**
  * dir 1: edges 1 and 3 (the Y-running edges) each get a uniform
    3 kN/m/kPa = q·Lx/2.
  * dir 2: edges 0 and 2 each get 2 = q·Ly/2.
* **Parallelogram (0,0)(6,0)(8,4)(2,4)**
  * dir 1: each slanted edge gets 3·4/√20 = 2.68328, 12 per edge.
  * dir 2: edge 0 gets the profile (0,0)–(2,4) | step | (2,2)–(6,2),
    total 12.
* **Trapezoid (0,0)(6,0)(5,4)(1,4), dir 1**
  * Edge 1 runs from 3·4/√17 down to 2·4/√17, total 10, which is
    A/2 = 10.
* **Engine: 8 x 4 m slab, q = 5 kPa**
  * Set-up: four edge beams released at both ends (simply supported),
    with fixed corner columns.
  * **One-way along X (dir 1).** 100 % goes to YB1 and YB2, the two
    beams that run in Y and that the X-span crosses.
    * Each gets w = q·8/2 = 20 kN/m.
    * Shear drop: 80 kN.
    * M_mid = wL²/8 = 40 kN·m.
    * XB1 and XB2 carry V = M = 0 (below 1e-6 relative).
  * **dir 2.** XB1 and XB2 get 10 kN/m, so M_mid = 80; the YB beams get
    nothing.
  * **Both directions.** Every corner column reacts 40 kN, and base FZ =
    160 = qA (to 1e-9).
  * **Missing support beam.** With YB2 removed, its 16 m² (per kPa)
    lands as 8 + 8 on its corner nodes.

### JSON shapes

```
shell_sections[name]: {..., "shell_type": "shell_thick"}        # omitted if "shell"
shells[i]: {..., "distribution": "one_way", "one_way_dir": 2}   # omitted if defaults
```

Results JSON is unchanged in shape. `cases[c].shell_forces` now also has
entries for ASDShellT3 triangles.

## Open structure wind, model info, run log and report data

### Open structure wind (`skyframe/core/openwind.py`)

ETABS Assign > Frame Loads > Open Structure Wind Parameters plus the
"Open Structure" wind pattern (ASCE 7 Ch. 29, other structures).

**Per-member parameters.** `FrameMember.open_wind` is `null` by default
(not assigned). `to_dict` emits it only when it is set. The stored
(canonical) form is:

```json
"open_wind": {"include": true, "cf": null, "width": "auto", "shielding": 1.0}
```

* `include` (bool): `false` keeps the assignment but leaves the member
  unloaded.
* `cf` (> 0 or null): the drag / force coefficient. `null` means use the
  generator's `cf`.
* `width` (`"auto"` or > 0, in m): the exposed width. `"auto"` means the
  section depth `h`, or `b` when `h` is 0. If neither is set, the
  generator raises an error.
* `shielding` (0 < s <= 1): a shielding / solidity reduction factor.

Unknown keys or bad values are rejected (400 / `ValueError`).

**Generator.** `compute(model, V, code="asce7_22", exposure="C",
direction="X", angle=None, Kzt=1, Kd=0.85, ze=0, Ke=None, G=0.85, cf=2.0,
members="assigned", segments=4, z_ground=0, tower=None)`.

* `code`: `asce7_16` or `asce7_22`.
* `direction`: `X`, `Y`, `-X` or `-Y`. Alternatively give `angle` in
  degrees from +X in plan.
* `members`: `"assigned"` (only members whose `open_wind.include` is
  true), `"all"` (unassigned members use the defaults), or a list of
  uids.
* `tower`: `{shape: "square"|"triangle", solidity: e}`. This replaces the
  default `cf` with the ASCE Table 29.4-2 trussed-tower value:
  `4.0e^2 - 5.9e + 4.0` (square) or `3.4e^2 - 4.7e + 3.4` (triangle).

The load per unit member length, acting along the horizontal wind unit
vector d, is:

```
w(z)  = p(z) * width * shielding * proj,   proj = sqrt(1 - (t . d)^2)
7-16: qz = 0.613 Kz Kzt Kd Ke V^2 / 1000 [kPa]  (builder.wind_kz, 2.01..., 4.6 m floor)
      p  = qz G Cf
7-22: qz = 0.613 Kz Kzt Ke V^2 / 1000           (autolateral.asce22_kz, 2.41..., 15 ft floor)
      p  = qz Kd G Cf
z = point elevation - z_ground;  Ke = autolateral.asce22_ke(ze) unless given
```

* Horizontal members get one `udl` load.
* Other members get `segments` equal `trapezoid` loads. Their end
  ordinates are the exact w(z) at each segment end.
* Each load is written as `global_x` / `global_y` components of d (a zero
  component is skipped).
* A member parallel to the wind (proj below 1e-9) is skipped.

`generate(model, name="OWIND", add_case=True, **params)` replaces the
pattern `name` (kind `"wind"`). If no case of that name exists, it also
adds the linear static case `{name: 1.0}`.

The summary shape:

```json
{"code","V","exposure","angle","d":[dx,dy],"G","Kd","Kzt","Ke","cf_default","segments",
 "members":[{"uid","cf","width","shielding","proj","z_i","z_j","qz_i","qz_j",
             "p_i","p_j","w_i","w_j","F","segments":[[a,b,w_a,w_b],...]}],
 "FX","FY","F"}
```

**Endpoints**

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/member/open-wind` | `{uids:[...], params:{...}\|null}` (null clears; all-or-nothing) | model |
| `POST /api/pattern/open-wind/preview` | `{V, ...generator params}` | summary (model unchanged) |
| `POST /api/pattern/open-wind` | `{name?, add_case?, V, ...}` | model |

Errors return 400: a missing `V`, an unknown key, a bad value, or no
loaded member.

### Run log, model info, report data (`skyframe/core/runlog.py`)

* `POST /api/analyze` now times each case and captures Python warnings.
  **Its response is unchanged (byte-identical).**
  `OpenSeesEngine.case_times` (`{case: seconds}`) is set by `run()` for
  every case that goes through its per-case runner: static, modal, RS,
  TH, nonlinear static, pushover, staged, hyperstatic and buckling.
* `GET /api/analyze/log` returns `{"available": false}` before any run.
  After a run it returns:

```json
{"available": true, "current": <model unchanged since the run>,
 "version": 1, "started": "ISO-8601 UTC", "total_s", "model_name",
 "counts": {"cases","combos","rs_cases","th_cases","modes"},
 "cases": [{"name","kind","status","time_s"|null}],
 "combos": [{"name","status"}],
 "warnings": [{"source": "run"|<case>|"engine", "message"}]}
```

  `warnings` gathers, de-duplicated and in this order:
  1. the results `warning` string, split into its items;
  2. each case's `warning` / `warnings`;
  3. Python warnings raised during the run (`"Category: message"`).

* `GET /api/model/info` returns:
  * `name`, `units`, `display_units`, `members_by_kind`, `shells_by_kind`,
    `height`, `plan`, `diaphragm`, `base_fixity`, `cases_not_run`;
  * `counts`: stories, joints, frames, shells, links, materials,
    frame_sections, shell_sections, load_patterns, load_cases,
    load_combos, rs_cases, th_cases, pushover_cases, staged_cases,
    buckling_cases, nonlinear_static_cases, supports, spring_supports,
    groups, section_cuts, assigned_loads, open_wind_members;
  * `last_analysis`: null, or `{started, total_s, n_warnings, current}`.
* `POST /api/report/data` takes `{tables?: [keys], cases?: [names]}` and
  returns `{model_info, run_log, results_available, tables: {key: <table
  as /api/tables/<key>> | {"error"}}}`.
  * It uses stored results only and **never solves**. With no stored
    results for the current model, `tables` is `{}`.
  * The default tables are: load_pattern_summary, load_case_equilibrium,
    modal_periods, modal_mass_ratios, base_reactions, story_drifts,
    story_forces.
  * The frontend report does not depend on this endpoint.

### Validation

* `tests/test_openwind.py` (19 tests) uses hand line loads on a 3-panel,
  4 m square lattice tower (V = 40 m/s, exposure C, G = Kd = 0.85,
  Cf = 2).
  * Struts normal to the wind at 10 / 20 / 30 m: w = qz Kd G Cf b. At
    10 m this is 0.212282 kN/m; at 30 m it is 0.265635 kN/m.
  * Struts parallel to the wind are not loaded.
  * Leg trapezoids carry the exact qz at the segment ends and are within
    0.5 % of the integrated power law.
  * Face-diagonal projection is 10/sqrt(116).
  * The 7-16 form is checked (qz(20 m) = 0.965560 kPa).
  * Per-member cf / width / shielding / include, the tower Cf, 45 degree
    components and -Y are checked.
  * Pattern totals equal the summary, and engine base FX = -sum F
    (1e-9).
  * Round-trip and validation are checked, plus the endpoints.
* `tests/test_runlog.py` (5 tests) covers counts, warning splitting and
  de-duplication, case times, the stale flag, and report tables
  (equilibrium error < 1e-3 %). It also checks that the analyze payload
  keys are unchanged.
* Defaults: `open_wind` is absent and `run()` results are unchanged. The
  quick_building + RS model and results hashes are identical before and
  after.

## Interactive database editing (model tables)

ETABS "Edit > Interactive Database Editing": the MODEL definition (not
results) shown as editable tables. Backend: `skyframe/core/modeltables.py`;
endpoints: `skyframe/api/modeltables_api.py` (registered by `create_app`).
The module is pure data transformation: no engine or model code changed,
so every existing result is byte-identical.

### Table catalogue (keys, in apply order)

| Group | Keys |
|---|---|
| Model Definition > Properties | `materials`, `frame_sections`, `shell_sections`, `stories`, `grid_lines` |
| Model Definition > Objects | `joints` (derived, read-only), `frame_objects`, `shell_objects`, `links` |
| Model Definition > Assignments | `supports`, `point_springs`, `line_springs`, `diaphragm_definitions`, `story_diaphragms`, `joint_diaphragms`, `groups`, `group_assignments` |
| Model Definition > Loads | `load_patterns`, `joint_loads`, `story_forces`, `frame_distributed_loads` (udl / trapezoid, including legacy `member_udls`), `frame_point_loads`, `area_loads` |
| Model Definition > Load Cases & Combinations | `load_cases` (summary, read-only), `static_cases`, `static_case_loads`, `rs_cases`, `th_cases`, `pushover_cases`, `staged_cases`, `buckling_cases`, `load_combinations`, `combo_members` |
| Model Definition > Properties | `mass_source`, `mass_source_options` (one row) |

Fields that are not columns (for example layered shells, link params, TH
records, function libraries, frame joint offsets) are never touched by an
edit. They are kept on every row that keeps its `_id`.

### Endpoints

Every body may carry `model` (a `to_dict()` object). Without it, the
server's current model is used. A successful apply or import REPLACES the
server model unless `dry_run: true`.

* `POST|GET /api/modeltables/list` ->
  `{tables: [{key, title, group, editable, can_add, can_delete, key_column, rows}], quantities: {kind: SI unit}}`
* `POST /api/modeltables/<key>` ->
  `{key, title, group, editable, can_add, can_delete, key_column, columns, rows}`
  * `columns[i]` is `{key, label, type, quantity, unit, editable, enum?, ref?, required?, optional?}`.
  * `type` is one of `number | int | bool | enum | text | ref | points`.
  * `quantity` is a js/units.js kind (`length`, `dim`, `area`, `inertia`, `force`, `moment`, `line_force`, `pressure`, `stress`, `modulus`, `unit_weight`, `mass_density`, `mass_per_length`, `mass_per_area`, `stiffness`, `rot_stiffness`, `line_spring`, `thermal_coeff`, `period`, `none`, plus `angle_deg` = degrees, not converted). Values are SI.
  * `ref` is `members | shells | links`: an object uid is checked server-side, with no enum sent.
  * `points` is `[[x, y, z], ...]`; text form `"x,y,z; x,y,z"`.
  * Each row is `{<column key>: value, "_id": id}`. `_id` is the original name (dict tables), the list index (list tables), or `[pattern, list, index]` (load tables).
  * Unknown key -> 404.
* `POST /api/modeltables/<key>/apply {rows, model?, dry_run?}`, and
  `POST /api/modeltables/apply {tables: {key: rows}, model?, dry_run?}`
  (several tables at once, applied in catalogue order).
  * 200 -> `{ok: true, model, summary: {key: {added, modified, deleted, renamed?} | {rows, ...}}}`.
  * 400 -> `{error: "<n> errors: <first> [table row r, col]", errors: [{table, row, col, message}]}`.
    `row` is the 0-based index into that table's posted rows, or null. `col` is a column key, or null.
  * With `soft_errors: true` the same payload comes back as 200 with
    `ok: false`. The UI uses this because browsers log every 4xx as a
    console error.
* `POST /api/modeltables/<key>/csv {model?, include_id?=true}` ->
  `{filename, csv}`. The header is `_id` plus the column keys; values are
  SI, using `repr()` floats (an exact round trip), `TRUE`/`FALSE`, and
  `"x,y,z; ..."` for points.
* `POST /api/modeltables/<key>/csv_import {csv, apply?, model?, dry_run?}`
  * Without `apply` -> `{rows}`, parsed but not applied.
  * With `apply` -> the same result as apply.
  * Headers can be column keys or labels, optionally followed by
    `[unit]`. The `_id` column is optional.
* `POST /api/modeltables/export {model?, as_base64?}` -> `application/zip`
  (or `{filename, zip_b64}`). The zip holds one `<key>.csv` per table,
  plus `_index.csv` and `_meta.json` (`model_sha256`). openpyxl is not a
  dependency, so the workbook is a zip of CSVs, not a real .xlsx.
* `POST /api/modeltables/import`, with the zip as the body
  (`application/zip`) or as `{zip_b64, model?, dry_run?}`.
  * Applies every editable table in the zip, atomically.
  * `_id`s are used only when `_meta.model_sha256` matches the base
    model. Otherwise rows match by key (name / uid) or are added.

### Apply semantics (atomic)

1. Rows are parsed and checked, table by table in catalogue order, on a
   deep copy of the base model. Checks:
   * the type of each cell;
   * that enum values exist (materials, sections, stories, patterns,
     cases, groups, diaphragms);
   * that object references exist (`member_uid`, `region_uid`, group
     objects);
   * that required columns are present on new rows;
   * that keys are unique.
   Every failing cell is reported. A cell whose value equals the stored
   value is not rewritten or re-validated, so model -> rows -> apply is
   byte-identical.
2. Row identity:
   * A row with `_id` edits that object. Changing its key renames it.
   * A row without `_id` whose key matches an existing object edits that
     object.
   * Any other row is added. A blank frame / shell / link uid is
     auto-numbered `F1` / `A1` / `L1`.
   * An object with no row is deleted.
3. Only if no row error exists is the edited dict rebuilt with
   `BuildingModel.from_dict` (the normal validation). A failure there
   becomes one error, attributed to the first posted row whose identifier
   (name, uid, member_uid, …) appears in the message.
4. On any error nothing is applied, and the base dict is never mutated.

Cascades:

* **Renames** propagate to references:
  * material -> frame / shell sections and their layers;
  * section -> frames;
  * shell section -> shells;
  * pattern -> static cases, P-Delta gravity, mass source,
    TH / pushover / buckling gravity, staged cases;
  * case / combo -> combos, `cases_not_run`, RS combos, buckling base
    case, modal_from_case;
  * story -> objects, story diaphragms, explicit masses, story forces;
  * uid -> loads, groups, TH / pushover `My`;
  * group -> section cuts;
  * diaphragm -> shells and joints.

  Later tables in the same apply that still use the old name are
  translated.
* **Deleting** a frame, shell or link also deletes its loads and its
  group memberships.
* **Deleting** a material, section, pattern or case that is still used is
  an error.

Edit rules:

* **Frame sections:** editing `b`/`h` of a rectangular prismatic section
  (A == b·h) while A/I33/I22/J stay unchanged recomputes A, I33, I22 and J
  exactly as `FrameSection.rectangular`. Explicit property edits win. A
  new section needs either A/I33/I22/J or b and h.
* **Stories:** if any height or the story order changes, elevations are
  recomputed as cumulative heights from the original base. Object
  coordinates are not moved.
* **Distributed loads:** a legacy `member_udls` entry (or a new row) that
  is uniform gravity over the full span stays in `member_udls`. Anything
  else is stored in `member_loads`. Loads of other kinds keep their list
  positions.
* **Grid lines:** lines are rebuilt for each system. A new system name
  creates an orthogonal system at the origin. Systems are never deleted.
* **Story diaphragm:** the option `default` means no per-story override.

### Frontend

Edit > Interactive Database Editing… (`js/dbedit.js`):

* table tree;
* editable virtual grid with in-cell editors by type (units.js numbers,
  enum selects, checkboxes, text);
* add / delete row;
* Excel TSV paste into ranges;
* per-cell error highlighting with messages;
* CSV and whole-model zip export / import;
* Apply / Cancel.

Apply posts the edited tables to `/api/modeltables/apply`, adopts the
echoed model and records ONE undo step ("Interactive database edit").
`?mock=1` runs the same row logic client-side (`js/mock_dbedit.js`).

### Validation (tests/test_modeltables.py)

* Byte-identical round trip of every editable table, one by one and all
  at once, also through CSV (per table) and the whole-model zip.
* Rectangle recompute equals `FrameSection.rectangular`.
* Cascades, deletes, paste of 10 member sections, adding a load case and
  a combination in one apply.
* Per-cell errors with exact `(table, row, col)`, and nothing applied.
* A table section edit gives the closed-form cantilever deflection
  P·L³ / (3·E·I).
* The HTTP endpoints.

## Independent verification suite (`tests/verification/`, `docs/VERIFICATION.md`)

CSI SAP2000 / ETABS *Software Verification*-style problems (plus NAFEMS /
MacNeal-Harder / textbook benchmarks), one file per family:
`test_verif_frames.py`, `test_verif_shells.py`, `test_verif_dynamics.py`,
`test_verif_nonlinear.py`; shared helpers `_vhelp.py` (`check()` asserts
AND records each comparison) and `_q8.py` (independent numpy Q8
plane-stress solver used as the converged reference for the wall
problems).  `python3 tests/verification/make_verification_md.py` (from the
package root) re-runs the suite with `SKYFRAME_VERIF_JSON` set and renders
`docs/VERIFICATION.md` (problem, source, quantity, reference, SkyFrame,
% error, tolerance, status, note).  Ordinary pytest runs write nothing.

Engine fixes made by the suite (defaults bit-identical; results-JSON hash
of a representative model with pushovers at h = 0.02 / 0.05, a nonlinear
TH at h = 0 and a linear TH unchanged):

* `HINGE_MIN_STEEL01_B = 1e-9` (`engine/opensees_engine.py`): in a
  DISPLACEMENT-controlled pushover the Steel01 post-yield ratio of the
  stiff-hinge springs is floored at 1e-9, so an elastic-perfectly-plastic
  case (`hardening = 0`) no longer goes singular when two yielded hinges
  meet at a node / the mechanism forms; the push now reaches the plastic
  collapse load (portal: 4Mp/h and 3Mp/h to < 2e-7).  Load-controlled
  nonlinear static steps and TH are unchanged (no floor).
* `engine/pdelta.py::_series_column_lines`: `non_iterative_mass`
  column-string sharing treats members stacked end-to-end inside a story
  as ONE series column line (weight `1/sum(L/EA)`, each piece gets the
  line's full `N`); single-member lines keep `EA/L` exactly.

Documented (unchanged) behaviour pinned by the suite: zeroLength hinge
springs carry no Rayleigh term in nonlinear TH (stiffness-proportional
damping acts on the elastic members only); Rayleigh a0/a1 are fitted to
the initial hinge-free modes.

## Story shear from all lateral load types

Static-case story results `story[s].shear_x / shear_y` are exact for every
lateral load type. This covers linear static, P-Delta and corotational
(stage 2) cases, the nonlinear static final state, and through them combos,
`story_stiffness`, irregularity and the `story_stiffness` table. Before
this change, only pattern `story_forces` and `nodal_loads` were counted.
Lateral member loads, open-structure wind, directional or joint-pattern
area loads and tendon loads all read 0 shear, while their drifts were
correct.

### Definition (ETABS)

The shear at a level is the sum of the global X / Y forces applied at and
above that level. Equivalently, it is the horizontal cut force on a plane
just below the level. A force at exactly the level elevation (within
1e-6 m) counts.

### How each load type is counted

Implemented in `skyframe/engine/story_shear.py`.

* **Story forces and pattern nodal loads:** the legacy
  `_story_shears` sum is kept verbatim.
* **Nodal forces from member, area and tendon loads:** these forces are
  recorded from the loads actually put on the OpenSees domain.
  * While a case's patterns are applied, `ops.load` is intercepted inside
    `_apply_member_load`, `_apply_area_load` and
    `tendons.apply_pattern_tendons`.
  * This captures member point loads landing on a node, the consistent
    nodal forces of directional / projected / joint-pattern area loads
    (shell load sets included) and shell-hosted tendon forces.
  * Each force counts at the levels at or below its node.
* **Member span loads:** these are integrated exactly. Every span record
  is used:
  * `beamUniform` / `beamPoint` element loads;
  * fixed-end-path partial UDL / trapezoid / point loads, from any
    direction, from open wind, from frame-hosted tendons and from
    self-weight.

  Each record is converted to global and its horizontal part is integrated
  over the part of the segment at or above the level. A segment from z_i
  to z_j is cut at `x_e = L (e - z_i)/(z_j - z_i)`. A linear load
  contributes its closed-form integral over the overlap. A point load
  counts when its station is at or above the level.

  This is exact for inclined and vertical members that pass through a
  level without a node. The fixed-end nodal forces of span loads are not
  used, so there is no end-node approximation.
* **Excluded (zero shear):**
  * Temperature loads: uniform, gradient, joint and shell. They are
    self-equilibrated imposed strains.
  * Ground displacements: imposed `sp`, which gives reactions only.
  * Moment / curvature span records.
* **Rounding noise:** a horizontal component of at most 1e-12 times the
  load's own magnitude is dropped. This removes the noise from a gravity
  load passed through the member axes, so gravity-only loads add exactly
  nothing.
* **Recording and replay:**
  * Static cases record during the real application (`capture`).
  * A caller without a recording, such as the nonlinear static final
    state, replays the pattern application with `ops.load`, `eleLoad` and
    `sp` stubbed out. The domain and span bookkeeping are untouched, and
    the result is identical (tested).
* **Combos:** combos stay linear in the case shears, because they combine
  the case story rows.
* **Per-diaphragm rows:** these carry no shear and are unchanged.
* **Not changed:** dynamic (RS / TH) peak shears and the pushover
  distribution's `_story_shears` are unchanged.

### Validation

`story_shear.cut_shear(eng, asm, z)` is an independent check. It sums
three contributions on a plane just below z:

* the global end forces at the upper node of every crossing frame segment
  (`localForce` plus the fixed-end correction), plus the span load
  between the plane and that node;
* the shell `forces` at the upper nodes of crossing elements;
* minus the reactions of supports above the plane.

`tests/test_story_shear_all_loads.py` has 19 tests:

* Cantilever with lateral UDL, single and stacked members:
  V = w (H - e), exact.
* Partial trapezoid plus point loads, with a point exactly at a level.
* local_y load on a column.
* Inclined brace straddling a level: w L/2 plus a point above.
* Braced frame wind member loads. The legacy shear was 0.
* Lattice open-wind pattern at 30 degrees, against a hand integration.
* Hydrostatic wall pressure:
  V(e) = B g (H-e)^2/2 + B h (q(e-h)/6 + q(e)/3), exact (consistent
  bilinear loads).
* Slab with global_x area load and a global_y shell load set.
* Tendon in a column: V(e) = 4 P s |2e - L| / L^2 and base shear 0.
* Thermal and ground displacement give 0.
* Combos and a multi-pattern case are linear in the cases.
* P-Delta and corotational cases.
* Replay equals capture.
* Story stiffness equals |shear/drift| in the engine and the table.
* Cut-force equality, to 1e-6, on a 3-story braced frame plus wall plus
  frame with mixed loads (member, area, nodal, story force, self-weight,
  gravity).
* The legacy-only model is bit-identical.
* Gravity on skewed / rotated members adds exactly zero.

Defaults are bit-identical. For models whose lateral loads are only story
forces and nodal loads, run results hash identically before and after:

* quick_building with a P-Delta case and a combo;
* semi-rigid walls / slab with self-weight, area / member gravity,
  thermal, nodal and story loads, and a combo;
* corotational gravity on an inclined brace.

## Rayleigh damping on springs, links and hinges

OpenSees `zeroLength` / `twoNodeLink` elements carry no Rayleigh term
unless they are built with `-doRayleigh`. Until now SkyFrame never set
it, so in direct-integration TH its plastic-hinge springs, grounded
springs, links and panel-zone springs had no damping. Two effects:

* an EPP hinged SDOF ran +0.78 % against the classically damped SDOF;
* a linear TH of a spring-supported or linked model got less damping than
  the case's Rayleigh ratio. A column whose base spring provides 2/3 of
  the flexibility decayed at xi = 3.8 % instead of 5 %.

The fix is a model-level option. It lives in
`skyframe/core/damping_options.py`; the engine side is
`skyframe/engine/link_rayleigh.py`.

    "damping_options": {"rayleigh_on_links": "none" | "elastic_only" | "all",
                        "rayleigh_stiffness_basis": "committed" | "current"
                                                    | "initial"}

* The default `{"none", "committed"}` keeps the old behaviour
  bit-for-bit: every `ops.element` call is unchanged. To prove it, the
  results-JSON SHA-256 of 14 TH runs was compared before and after the
  change and is identical. The runs cover a grounded spring, a named
  multilinear spring, an elastic link, a yielding isolator and a fixed
  base, each linear and with hinged nonlinear analysis, with and without
  energy output. `to_dict` writes the key only when it differs from the
  default. An unknown key or value raises `ValueError`.
* Which element kinds take part. The flag is set only in TH builds;
  static, modal and pushover builds never set it.
  - `elastic_only`: grounded linear point, line and area springs; named
    LINEAR springs; elastic links; elastic panel-zone springs.
  - `all`: everything in `elastic_only`, plus pushover, ASCE 41 and
    user-hinge springs (nonlinear TH), named nonlinear springs (c/t-only,
    gap, multilinear), compression-only vertical springs, and material
    links (isolator, multilinear, takeda / pivot, gap, hook).
  - never:
    - viscous dampers. A damper is already a dashpot, so Rayleigh would
      count its damping twice.
    - dedicated bearing elements (singleFP, triple FP, flat slider,
      Bouc-Wen). They have their own hysteretic dissipation and an
      almost rigid initial stiffness. OpenSees TripleFrictionPendulum
      also has no Rayleigh switch.
    - stiff hinge-tie springs and the zero-free-DOF dummy spring. These
      stand in for constraints.
* The participating elements use the case's stiffness coefficient `a1`
  (from the legacy fit or from `di_damping`), applied on
  `rayleigh_stiffness_basis`. This is done with
  `ops.region(tag, '-eleOnly', *eles, '-rayleigh', 0, betaK, betaK0, betaKc)`,
  which leaves the nodal `a0 M` term untouched.
* Under `modal` damping the option has no effect, because modalDamping
  already damps the sprung modes.
* When anything participates, the results carry
  `THResults.extra["link_rayleigh"] = {"rayleigh_on_links",
  "rayleigh_stiffness_basis", "a1", "elements": {kind: count}}`.
* ETABS / SAP2000 behaviour (CSI). Direct-integration proportional
  damping is applied element by element, and links are included. For the
  stiffness-proportional part, each link chooses which stiffness it uses:
  initial (the default), tangent, or effective. The effective option was
  added for friction isolators, where initial-stiffness damping is
  excessive. Each link also has a modification factor on `cK`. A link's
  "effective damping" is used by LINEAR cases only.
* How the three bases compare:
  - `committed` is close to CSI's tangent option.
  - `initial` matches CSI's default. It keeps `a1 k_theta theta_dot` on
    stiff hinge springs after they yield, and the EPP SDOF then runs
    78 % below the reference. Avoid it for hinges.
  - `current` switches the damping tangent inside the Newton iterations
    and does not converge on yielding Steel01 hinges.
  - For elastic springs and links all three are identical (tested).
* Recommended setting: `{"rayleigh_on_links": "all",
  "rayleigh_stiffness_basis": "committed"}`
  (`RECOMMENDED_DAMPING_OPTIONS`).
* Energy tracker (`engine/thoptions.py`).
  - Root cause of the broken balance: OpenSees refreshes an element's
    committed stiffness `Kc` in `commitState`. The post-step
    `reactions('-dynamic')` read therefore uses `Kc(n+1)`, while
    equilibrium at `t_n+1` was solved with `Kc(n)`. At yield events the
    two differ.
  - Fix: for committed-basis elements the tracker adds the term
    `a1 A^T diag(k_n - k_n+1) A v`. `A` is the basic-deformation matrix,
    built from the element's `xaxis/yaxis/zaxis` responses (twoNodeLink
    with shearDistI = 0.5); `k` are the material tangents.
  - The term is non-zero only on steps where a tangent changes. It is
    also applied for central difference, using the central velocity.
  - Result with the option on: the balance error is about 4e-15 for a
    hinge, a named multilinear spring and an isolator twoNodeLink.
    Without the correction it would be 1.4e-4, 1.0e-3 and 1.1e-4.
* Validation (`tests/test_link_rayleigh.py`):
  - A column on a spring or link in linear TH matches an independent
    Newmark SDOF with `c = a0 m + a1 k_series` to 1e-7. Its log decrement
    gives xi = 5.005 % (legacy: 3.79 %).
  - An EPP hinged SDOF (My = 150; it yields, then vibrates freely) is
    compared with a classical Rayleigh EPP SDOF that uses the same series
    spring and `c = a0 m + a1 k_t,committed`. Errors: peak -0.0014 %, end
    of record -0.017 %, worst point over the whole history 0.041 %
    (legacy: +0.65 % at the end). In the elastic range the match is exact
    to 1e-6.

## Analysis performance, solver policy and convergence strategy

Engine speed on real building sizes, a shared nonlinear convergence strategy,
and model-size safety. Profiles, the before/after table and the method are in
`docs/PERFORMANCE.md`. The tests are in `tests/test_perf_robust.py`.

### Model field `analysis_options` (all keys optional; `{}` = defaults)

```json
{"solver": "auto" | "band" | "umfpack" | "mumps" | "sparse_general",
 "eigen_solver": "auto" | "legacy",
 "convergence": {"mode": "legacy" | "robust",
                 "algorithms": ["Newton", "ModifiedNewton", "KrylovNewton",
                                "NewtonLineSearch", "BFGS"],
                 "tests": [{"type": "NormDispIncr"},
                           {"type": "EnergyIncr", "tolerance": 1e-10,
                            "max_iterations": 100}],
                 "max_halvings": 4},
 "size_warnings": true}
```

* `to_dict` emits the key only when it differs from the defaults, so default
  models serialize byte-identically.
* `from_dict` reads it back, and `validate()` checks it
  (`core/analysis_options.py`).
* Validation rules:
  * Unknown keys or values raise `ValueError`.
  * `algorithms` must be a non-empty subset of the five algorithms above.
  * Test `type` is one of NormDispIncr, EnergyIncr or NormUnbalance.
  * `tolerance` must be > 0.
  * `max_iterations` must be in 1..10000.
  * `max_halvings` must be in 0..12.
* Staged-construction stage models inherit the options.

### Solver policy (`engine/solver_policy.py`)

* With `auto`, the estimated equation count of a build decides the solver.
  The estimate is:
  * free node DOFs,
  * minus 3 for every rigid-diaphragm slave node.
* Below `SPARSE_EQ_THRESHOLD` = 300 equations, `auto` uses
  `numberer RCM` + `system BandGeneral`. This is exactly the previous path,
  so results are bit-identical.
* At or above 300 equations it uses `system UmfPack` (still RCM). 300 is the
  measured crossover of the two solvers.
* On rigid-diaphragm buildings the band of the constrained stiffness is a
  whole floor. A 20-story meshed building made BandGeneral abort the process
  (SIGSEGV after about 12 s).
* UmfPack agrees with the band solution to round-off: 1e-15..1e-13
  relative in measured cases.
* `band`, `umfpack`, `mumps` and `sparse_general` force that system.
* If a forced `band` would need more band storage than the cap
  (`model_size.BAND_MEMORY_CAP` = 2 GB, or half of physical RAM), the engine
  falls back to UmfPack. A results `warning` reports the fallback instead of
  crashing.
* `SparseSYM` is never used: it returned wrong displacements on
  Transformation-constrained models.
* The policy applies to every setup the engine runs:
  * linear statics, P-Delta and corotational statics;
  * pushover, nonlinear static and time history;
  * the eigen pre-setup.
* Factor-once reuse:
  * Linear static cases, response-spectrum modal statics and the CR unit
    loads run on the reused elastic domain with one
    `algorithm Linear -factorOnce` analysis kept alive. Every further load
    case is a forward/back substitution.
  * This is used only for UmfPack, because BandGeneral's band solver cannot
    factor once.
  * The result is bit-identical to a fresh factorization per case
    (tested).
  * Any `_build` or explicit analysis setup ends the reuse.

### Condensed modal analysis (`engine/modal_condensed.py`)

* Mass is lumped nodal only, so `K u = λ M u` restricted to the massed free
  DOFs is exactly `u_m = λ G_mm M_m u_m`.
* `G_mm` is the constrained flexibility: one factor-once static solve per
  massed DOF.
* The eigenpairs come from a dense `eigh` of `M^½ G M^½`.
* Each full mode shape takes one more solve, with load `λ M φ_m`.
* When it is used:
  * The MODAL case of large models only, which means the policy picked a
    sparse system and `n_massed` ≤ 600.
  * `eigen_solver: "legacy"` turns it off.
  * If it finds fewer than `num_modes` finite modes, the legacy eigen runs
    instead.
* Results:
  * Periods agree with the OpenSees eigen to 1e-13.
  * Shapes are mass-normalized (`φᵀMφ = 1`).
  * Signs are deterministic: the largest-magnitude `M^½φ` component is
    positive.
  * Eigenvector signs are arbitrary in every solver. For exactly repeated
    eigenvalues, any basis of the degenerate subspace is valid.
* Two by-products save solves:
  * The unit-load solves give the CR (`story_props`) unit rotations, so
    `_master_rz` reads a memo of the same solve with no new solve.
  * When RS cases exist, each mode's response to `λ M φ` is stored. Every
    RS case scales it by `Γ·Sa/λ` (linear statics) instead of re-solving
    per mode and per case.
  * The scaling is not used with compression-only or nonlinear named
    springs, or with ground displacements.

### Other exact speedups

* `compute_story_masses` and `story_gravity_loads` use uid→object maps
  built once (`BuildingModel._uid_maps`, same first-match semantics). The
  previous loop was O(stories × loads × objects).
* The engine memoizes uid lookups and station positions.
* RS CQC/SRSS combination evaluates each quantity's `v @ rho @ v` on a
  fresh contiguous row. The arithmetic is the same, so results are
  bit-identical.
* `_modal_static` reuses the elastic domain instead of a full rebuild per
  mode.
* Mesh cache across engines (`core/mesh_cache.py`):
  * Keeps the last 2 meshes, keyed by SHA-256 of the pickled model with the
    case-definition attributes blanked.
  * Load patterns are in the key, because point loads place mesh points.
    So are the mesher tuning constants.
  * A hit returns a fresh unpickled copy attached to the live model.
  * `SKYFRAME_MESH_CACHE=0` or `SKYFRAME_SLOW_PATH=1` turns it off.

### Convergence strategy (`engine/convergence.py`)

Every nonlinear step runs through one `Strategy`:

* static cases with axial-only members, gap links or nonlinear springs;
* P-Delta and corotational stages;
* pushover gravity and push steps;
* nonlinear static cases;
* nonlinear time history, with or without direct-integration options;
* staged stage models.

`legacy` mode (the default) is the historical per-path logic, op for op:

* one `analyze`;
* where the path had a retry, `NewtonLineSearch` and then
  `algorithm Newton`;
* no test change and no halving.

Results are bit-identical (tested).

`robust` mode (opt-in):

* The first attempt is unchanged.
* On failure the ladder runs: for each test in `tests` (an entry without
  `tolerance` or `max_iterations` inherits the path's own), every algorithm
  in `algorithms` is tried in order.
* If the ladder fails and the path supplied its integrator (static) or
  `dt` (transient), the step is split in two halves. Each half is solved
  recursively with the full ladder, up to `max_halvings` levels.
* OpenSees reverts a failed `analyze`, so a failed attempt leaves no trace.
* After each step the path's own test and Newton are restored.
* Paths that already substep (nonlinear static cases, direct-integration
  options) get the ladder without halving, and their own substepping runs
  after it.

A run that needs no fallback gives the same results in both modes (tested).

Report: only in `robust` mode, results carry `convergence`:

```json
{"<case>": {"mode": "robust", "steps": N, "fallback_steps": n,
            "failed_steps": f, "halved_steps": h,
            "by_algorithm": {"KrylovNewton": 1, ...},
            "log": [{"step": k, "algorithm": "...", "test": "...",
                     "halvings": lvl}, {"step": k, "failed": true}]}}
```

* A step appears in `log` only when it needed something beyond the first
  attempt.
* `halvings` is the deepest split level.
* A failed case still runs as before:
  * a failed static case becomes `case_status` `"failed"`;
  * a pushover returns a partial curve with a warning;
  * a nonlinear static case stops early.

### Model-size warnings (`engine/model_size.py`)

* `run()` sizes the model:
  * equation estimate, nodes, frame and shell elements, system;
  * static cases, RS modal statics, TH steps;
  * an estimate of the result volume.
* It warns and never fails when any of these is exceeded:
  * 60 000 equations ("large") or 250 000 ("very large");
  * 20 000 shell elements;
  * 400 RS modal statics;
  * TH steps × equations ≥ 2e8;
  * 2e7 result values.
* The warnings are appended to results `warning` and logged.
* The `model_size` block (the report plus `warnings`) is emitted only when a
  warning fires, so smaller models are byte-identical.
* `size_warnings: false` turns it off.

### Equivalence

* `core/results_compare.py` compares results to a relative tolerance per
  quantity family.
* It is modal sign- and degenerate-subspace-invariant.
* `ignore_keys` covers noise-ratio quantities.
* Tests and measurements:
  * Below the threshold, the default run is byte-identical to forced
    `band` + `legacy` eigen, and to the pre-change engine. Measured SHA-256
    of the full results JSON matched for `quick_building()` and for a
    5-story model with RS, linear and nonlinear TH, pushover, staged and
    P-Delta.
  * Above it (eccentric 6-story frame; meshed slab and wall model), full
    results agree with the legacy path to < 1e-9 relative.
  * The benchmark buildings agree with the pre-change engine to
    2.6e-13 .. 1.1e-11. The exception is the doubly symmetric 10-story
    frame at 8.7e-9: a degenerate-mode basis artefact of the legacy path.
* Known noise, not caused by this change:
  * ASCE 7 `tors_ratio_*` and the `flag` of the orthogonal direction on
    doubly symmetric buildings are ratios of round-off drifts in both
    versions. They are not comparable.
  * RS results of exactly repeated modes carry 1e-9-level basis artefacts
    in the legacy Arpack path.

### Before / after (one `/api/analyze`-equivalent run, seconds)

Measured on a shared 4-core container (profiles and method in
`docs/PERFORMANCE.md`). Every model has 12 modes, 2 RS cases, 4 static cases
and 4 combos.

| model (stories, bays) | before | after | speedup |
| --- | ---: | ---: | ---: |
| 10s 5x5 frame | 29.7 | 2.9 – 5.0 | 6 – 10x |
| 20s 5x5 frame | 279.5 | 7.4 – 11.5 | 24 – 38x |
| 20s 8x6 frame | 1 454.4 | 15.9 – 25.5 | 57 – 91x |
| 40s 5x5 frame | 2 955.8 | 17.8 – 30.9 | 96 – 166x |
| 10s 5x5 meshed slabs + core walls | >52 min (aborted, ~4.6 GB RSS) | 18.5 – 26.8 | >115x |
| 20s 5x5 meshed (9 082 nodes, 8 320 quads) | crash (SIGSEGV, 12 s) | 68.4 – 83.5 | runs |
| 20s 8x6 meshed (16 789 nodes, 15 680 quads) | not run (crashes) | 127 – 185 | runs |

## Multiple towers (Define > Towers)

ETABS "Define > Towers" / "Allow multiple towers" (gap audit item C9).
A model may hold several towers, each with its own story list, grid
system and base elevation, optionally sharing a podium and joined by link
beams. Story-based quantities are then computed per tower. With no towers
defined (the default) there is one implicit tower and every model dict,
results dict, table and auto-lateral pattern is byte-identical to before
(sha256 of the representative models' model JSON, full `run()` results
JSON, centre of mass / rigidity and ELF summary unchanged).

Code: `skyframe/core/towers.py` (data, membership, per-tower model
views, per-tower load generators, table helpers) and
`skyframe/engine/towers.py` (FE node membership, per-tower story
results, cut shears, results summary). Shared files only carry one-line
hooks.

### Model JSON

```
"towers": [                                  // optional; absent = none
  {"name": "T1", "grid_system": "G1" | null, "base_elevation": 0.0,
   "stories": ["S1", "S2"]},                 // OUTPUT: names, model order
  ...],
"joint_towers": [{"point": [x, y, z], "tower": "T2"}],   // optional
"stories": [{"name", "height", "elevation", "tower"?}],  // "tower" only
                                                          // when tagged
"members" / "shells" / "links": [{..., "tower"?}]        // only when set
```

* On INPUT a tower's `stories` may be
  * story **names** — references to `stories` entries. They tag the
    story with the tower, except for the first (default) tower, whose
    stories stay untagged so that the round trip is exact;
  * story **definitions** `[{"name", "height"}]`; or
  * a story-**height table** `[h1, h2, ...]`, with names `<tower>-S<i>`.

  Definitions and height tables are appended to `stories`, tagged with the
  tower, at `elevation = base_elevation + cumulative height`.
  `from_dict(to_dict(m))` is exact.
* `towers` and `joint_towers` are emitted only when defined.
  `Story.tower`, `FrameMember.tower`, `ShellRegion.tower` and
  `LinkMember.tower` are emitted only when non-empty.
* Story names stay unique across the whole model. An untagged story
  belongs to the first tower (the default tower).
* Python API: `skyframe.core.towers.define_towers(model, entries)` (same
  input forms); `towers.summary(model)`.

### Validation (`model.validate()` → ValueError)

* Tower names must be non-empty (60 characters at most) and unique.
* `grid_system` must be null or an existing grid system name.
* `base_elevation` must be finite.
* Every tower must have at least one story. Story elevations must
  strictly increase, bottom to top, within each tower.
* Story names must be unique.
* Tags must name existing towers. Tags of any kind without `towers` are
  rejected.
* An object's `tower` tag must not contradict the tower of its `story`.
* Story heights in a definition or height table must be > 0. A story
  name in a definition must not already exist.

### Tower membership

The rules are deterministic.

* **Story:** its `tower` tag, else the default tower.
* **Object** (member, shell or link):
  1. its `tower` tag;
  2. else the tower of its `story`;
  3. else by geometry. Take the towers having a story at the object's
     top elevation, and pick the first whose plan footprint contains the
     plan centroid of the object's top points. If none does, take the
     first such tower, else the default tower.
* **Joint / FE node:**
  1. its `joint_towers` tag;
  2. else a majority vote of the towers of the frame segments, shell
     quads and links meeting at the node. Only towers that have a story
     at the node's elevation take part, and ties go to tower order;
  3. else the footprint;
  4. else the default tower.

  So a link beam tagged T1 that lands on a T2 joint leaves that joint
  in T2. A podium-roof joint, where the podium tower has a story and the
  towers above only have their base, belongs to the podium.
* **Footprint:** the bounding box of the tower's grid system. With no
  grid reference it is the bounding box of the members tied to the tower
  by tag or story.

### Analysis per tower

* **Diaphragms.** Each (tower, story) gets its own rigid diaphragm, over
  only that tower's story-level nodes.
  * The master sits at the tower's plan centre: the centre of its grid
    system, else of its members.
  * Its rotational mass inertia is `m (Lx² + Ly²)/12`, using the tower's
    plan extents.
  * Accidental torsion `Mz = F · ecc · B⊥` also uses the tower's
    extents.
* **Story masses** (`compute_story_masses`, and therefore `story_masses`
  in the model JSON), the engine's centre of mass, tributary masses,
  `story_gravity_loads` and notional loads are all computed on per-tower
  model views.
  * A view holds only the tower's stories, objects, nodal loads, nodal
    masses and story forces, with the tower's own grid as its only grid.
  * As a result, a nodal load at an elevation shared by two towers
    counts once.
* **Story results** (`cases` / `combos` / `rs_cases` `story[s]`):
  * `ux` / `uy`: displacement of the tower-story master (else the average
    of its nodes).
  * `drift_*`: chained bottom to top WITHIN the tower. The first story is
    measured from the tower base:
    * 0 when no story of any tower lies at the base elevation (tower on
      the ground);
    * otherwise the mean displacement of the lower ends of the tower's
      elements at its base, for example the podium roof.
  * `shear_*`: the horizontal free-body cut force on the plane just below
    the level (z = elevation − 1e-9). It is taken through the tower's OWN
    frame segments (with their span loads above the plane), shell quads
    and links crossing the plane, minus the reactions of the tower's
    supports above the plane.

  This is ETABS "story forces" from element forces, so load that link
  beams or a podium carry between towers is attributed where it actually
  flows. For a disconnected tower it equals the legacy applied-load story
  shear by equilibrium. Response spectrum cases use the same cut on every
  per-mode static solve before the CQC/SRSS combination.
* **Centres of mass and rigidity** (`story_props`): one entry per
  tower-story. The unit-load CR method is applied at each tower-story
  master.
* **Irregularities and stiffness** (`story_stiffness`, `irregularity`):
  evaluated per tower. The soft-story comparison looks at the stories
  above within the same tower. Torsion ratios use the tower's plan
  extents.
* **Time history / FNA:** story `ux` / `uy` series come from the
  tower-story masters. Peak drifts are re-chained within each tower.
* **Auto lateral loads, per tower.**
  * `autolateral.compute` / `generate` (all codes, ELF and ASCE 7-22
    wind), the legacy `codes.asce7_elf` and `builder.make_wind_pattern`
    each run once per tower view. Each tower uses its own weights
    `W_T`, its own height `hn` (and so its own period `Ta`), its own
    elevations `h_x` (absolute, above the global base) and its own plan
    width for wind.
  * The resulting story forces are merged into ONE pattern.
  * Story-keyed parameters (`weights`, `loads`, mode shapes) are split per
    tower. Every other parameter applies to all towers.
  * The merged summary keeps the single-tower keys: `V` and `W` are sums,
    `T` is common or null, and `stories` holds all rows, each carrying
    `"tower"`. It adds `"towers": {name: per-tower summary}`.

### Results JSON (only with towers)

```
"towers": {"T1": {"stories": ["S1", ...], "base_elevation": 0.0,
                  "grid_system": "G1" | null,
                  "base_shear": {case|combo|rs case: {"FX", "FY"}}}},
"story_tower": {"S1": "T1", ...}
```

`base_shear` is the shear of the tower's lowest story.

* `POST /api/tables` results tables: every story-keyed table (story
  drifts, forces, stiffness, centres, diaphragm tables) gets a `tower`
  column right after `story`. The story-stiffness drift chain restarts at
  each tower. The model-free context places the masters at the tower
  centres.
* Model tables: Story Definitions edits recompute elevations tower by
  tower (`base_elevation` + cumulative heights).

### Validation (`tests/test_towers.py`)

* **Two identical, fully disconnected towers** (3 stories, 1×1 bay, 40 m
  apart) against the same tower analysed alone:
  * 12 modes = the single tower's 6 periods, each twice (0.523727,
    0.500728, 0.271750 … s; rel 1e-8);
  * every per-tower story row (ux, uy, drift, shear) for DEAD and EQX
    equal to rel 1e-8;
  * CM / CR shifted by exactly 40 m;
  * torsion ratios, flags and story stiffness equal;
  * RS (CQC) story rows equal (rel 1e-6);
  * accidental-torsion master rotations equal;
  * TH (modal damping) peak ux / drift equal (rel 1e-6).

  Control: the same geometry without `towers` couples the towers (A1
  shear 120 kN instead of 60 kN).
* **Rigid podium.** Podium P has 2 stories over x 0–46 m. Tower T1 (3
  stories) and tower T2 (2 stories) stand on the podium roof, with
  `base_elevation` 6 m. Applied loads: T1 10/20/30 kN, T2 15/30 kN, P
  25/50 kN.
  * V(P1) = 180 kN = total base shear = −ΣRx.
  * V(P2) = 155 = 50 + 60 + 45.
  * V(A1) = 60 and V(B1) = 45, so V(A1) + V(B1) + 50 = V(P2).
  * Drift of A1 is measured from the podium roof, whose base nodes move
    with the P2 master.
* **Link beams.** Two identical towers on the ground are joined by link
  beams, and only tower A is loaded (20/40 kN).
  * V(A1) = 30.147 kN and V(B1) = 29.853 kN. They sum to 60 kN = the
    base shear.
  * V(A1) equals the reactions under tower A.
  * The link-beam end joints stay in T2.
* **ELF per tower, checked by hand.** T1 is 3 × 3 m on 6 × 5 m and T2 is
  2 × 3 m on 8 × 4 m.
  * User coefficient C = 0.12, k = 1:
    * W_T1 = 390 + 450 + 510 = 1350 kN, so V = 162 kN and
      F = 22.404 / 51.702 / 87.894 kN;
    * W_T2 = 680 + 760 = 1440 kN, so V = 172.8 kN and
      F = 53.411 / 119.389 kN.
    * After analysis, each tower's base shear equals its own V.
  * ASCE 7-22 (SDS 1.0, SD1 0.4, R 8):
    * Ta = 0.337 s and 0.234 s;
    * Cs = 0.125, so V = 168.75 kN and 180 kN.
  * The legacy ASCE 7-16 ELF, wind (tower width 4 m vs 5 m) and notional
    loads are checked per tower in the same way.

### Limits

* TH / FNA story SHEAR peaks keep the whole-model inertia sum at the
  level elevation. This is correct for a single tower, but it mixes towers
  that share an elevation; drifts and displacements are per tower.
* Named diaphragms (`diaphragms` / `joint_diaphragms`) keep their own
  story-based rules.
* Staged construction stages by elevation, across towers.
* Pushover control uses the last story of `stories`.

## Model repair (Check Model > Fix...)

`skyframe/core/repair.py` repairs what Check Model reports, ETABS-style.

* Each fix is a PURE function `model -> (new_model, report)`.
  - `model` is the `BuildingModel.to_dict()` JSON, or a `BuildingModel`
    (then a validated `BuildingModel` is returned).
  - The input is never mutated.
  - A fix that finds nothing returns the input byte-for-byte.
  - Every fix is idempotent: `f(f(m)) == f(m)`.
* The fixes reuse `core/edit.py` (joint remap, member split with load
  carry-over, dependent cleanup) and the `core/checks.py` geometry
  searches.

### Fixes (canonical order)

| id | repairs | effect |
|---|---|---|
| `merge_joints` | JOINT_COINCIDENT | Joints within `tolerance` become one joint. The rule is the Edit > Merge Joints one: a supported joint wins, then the joint with most objects, then the smallest coordinates. Every reference follows: frame / link ends, shell corners, supports, springs, nodal masses / loads, ground displacements, joint temperatures, joint diaphragms / panel zones, line-spring ends, group points, TH `output_requests.joints`, steady-state / PSD `output_points`, pushover / NLS `control_point`. Members or shells that collapse (zero length, duplicate) are deleted and listed as actions. |
| `remove_zero_length` | FRAME_ZERO_LENGTH | Frames shorter than `tolerance` are deleted. Their two joints are merged first, so the connection survives. Loads on the frame are dropped (`loads_dropped`, warning). |
| `remove_duplicates` | FRAME_DUPLICATE, FRAME_OVERLAP, SHELL_DUPLICATE | Collinear overlapping frames are divided at each other's ends. Then exact duplicates (same two joints / same corner set) are removed. The copy with the most loads is kept (ties: the first). Group / tendon-host / pushover `My` references move to the kept copy. Loads on deleted copies are dropped (`loads_dropped`, warning). |
| `snap_shell_corners` | JOINT_COINCIDENT (shell) | A shell corner within `tolerance` of a frame / link end (else of another shell corner) moves onto it. Frame / link ends never move. A snap that would warp, fold or collapse a shell is skipped (warning). |
| `split_at_joints` | FRAME_JOINT_ON_SPAN, JOINT_UNCONNECTED on a span | Frames are divided at every joint (object end / corner or point record) within `tolerance` of the axis, strictly inside the span. The piece ends are the joint coordinates. |
| `split_at_crossings` | FRAME_INTERSECTION | Opt-in (not in the defaults). Both frames of a crossing are divided at the midpoint of the two closest points. A crossing means: closest approach <= `tolerance`, strictly inside both spans, not parallel. |
| `remove_unconnected_joints` | JOINT_UNCONNECTED | Point records whose point is not a node of the analysis mesh (object ends / corners, shell mesh nodes, frame auto-mesh points) are deleted. This covers supports, springs, nodal masses, joint diaphragms / panel zones, nodal loads, ground displacements, joint temperatures and group points. |
| `report_mechanisms` | STABILITY_MECHANISM | Report only: the model is never changed. Reports `checks.stability_diagnostics` (near-zero-stiffness mechanisms) and the Check Model connectivity errors (NO_SUPPORTS, FRAME/SHELL/LINK_UNCONNECTED, STRUCTURE_UNSUPPORTED_PART, STORY_NO_SUPPORT_PATH). |

* Splitting (both split fixes and overlap resolution) uses
  `edit._split_member`, so loads and assignments carry over:
  - `member_loads` point / moment loads go to the piece that holds them.
    A load exactly at a cut goes once, to `a = 0` of the next piece.
  - `udl` / `trapezoid` loads are split by position. Trapezoid ordinates
    are interpolated at the cuts.
  - Legacy `member_udls` (full span) and `thermal_loads` apply to every
    piece.
  - Moment releases stay on the outer ends. Rigid ends and joint offsets
    stay on the outer pieces. Hinges are mapped by relative distance.
  - Groups, tendon hosts and pushover `My` list all pieces.
  - New uids follow the Edit > Divide rule (`B1` -> `B2`, ...).
* Some members are never divided (a warning is given instead):
  - axial-only members (`axial_limit != "both"`, Truss);
  - curved members;
  - members with a nonprismatic section;
  - members with automatic hinges (`hinges` other than "none").
* `DEFAULT_FIXES` is used when `fixes` is omitted: every fix except
  `split_at_crossings` and `report_mechanisms`. `"all"` means every fix.

### API

`POST /api/repair`

```
body: {"fixes": [ids] | "all" (default DEFAULT_FIXES),
       "tolerance": m in (0, 1] (default 0.001 = Check Model default),
       "model": {...}? (default: the stored model),
       "dry_run": bool (default false),
       "max_dofs": int (stability cap, default 3000)}
200:  {"model": {...validated model JSON...}, "report": {...}}
400:  {"error": "..."}   (unknown fix, bad tolerance, invalid model)
```

* The stored model is replaced unless `dry_run` is true. A dry run is the
  preview: it returns the same `model` / `report` and stores nothing.
* A clean model is echoed byte-identical, with `report.changed == false`.

Report shape:

```
{"changed": bool, "tolerance_m": float, "dry_run": bool,
 "fixes": [ids applied, canonical order],
 "counts": {fix: number of actions},
 "actions": [{"fix", "message", "objects": [uids], "location": [x,y,z]|null,
              ...fix extras: "to"/"merged" (merge), "from"/"to" (snap),
              "pieces"/"at" (splits), "kept"/"removed"/"loads_dropped"
              (duplicates, zero length), "records" (unconnected joints),
              "code"/"severity" (report_mechanisms)}],
 "warnings": [str],
 "results": [{"fix", "title", "changed", "count", "actions", "warnings",
              "created", "deleted", "modified", "stability"?}],
 "created": {"members", "shells", "links", "points"},   # net, whole run
 "deleted": {"members", "shells", "links", "points"},
 "modified": {"members", "shells", "links"},
 "stability": {"stable", "built", "skipped", "n_equations",
               "n_mechanisms", "mechanisms", "unstable_dofs", "message",
               "issues"} | null,       # only with report_mechanisms
 "check_before": {"errors", "warnings", "info", "by_code", "ok"},
 "check_after":  {...same...}}
```

* Undo: the endpoint keeps no history; it only optionally stores the
  model. The SPA adopts the returned model as ONE labelled history step
  ("Repair model"). It uses the same `history.label` + `markDirty` path
  as the Edit menu (`js/history.js`).
* SPA: Analyze > Fix Model… and the "Fix…" button of the Check Model
  panel open the dialog (`js/repairdlg.js`). It has one checkbox per fix,
  a tolerance in the display length unit, a "Preview" button (a dry run
  that lists the actions) and "Apply" (one undo step). `?mock=1` runs
  `js/mock_repair.js`, which gives the same response shape.
* Validation (`tests/test_repair.py`):
  - A beam with a UDL split at an interior joint reproduces the closed
    form exactly: simply supported theta = wL^3/24EI, delta(a) and M(a);
    fixed-fixed M = wL^2/12 and delta(a). End joints, reactions and end
    forces equal the undivided beam to 1e-9.
  - Trapezoid / point / partial-UDL resultants are conserved.
  - A nodal load on the span acts after the split:
    5wL^4/384EI + PL^3/48EI.
  - A pinned beam hanging 0.5 mm off its column is a mechanism. After
    `merge_joints` it is stable. Its column reactions are wL/2 (+ the
    joint load), and the base and beam-end moments are zero.
  - Every fix, and the whole pipeline, is idempotent. Clean models
    (quick building, slab on columns, fixed beam) are unchanged
    byte-for-byte.

## Mesh sensitivity study and accuracy advisor

Two analysis-accuracy tools. Neither one changes the server's current
model or a saved model file. Both are new modules:
`skyframe/core/mesh_study.py`, `skyframe/core/accuracy_advisor.py`, and
the HTTP layer `skyframe/api/accuracy_api.py` (registered by
`create_app` with one line). No model field was added, so every existing
result and `to_dict` is byte-identical (results-JSON SHA-256 of the
quick building and of an SS plate checked before and after).

### POST /api/analyze/mesh_study

    {"levels": [1, 2, 4],          # optional; ints in [1, 16], 2..6 distinct
     "cases": ["DEAD", "MODAL"],   # optional; default below
     "quantities": ["roof_disp", "periods", "base_reactions",
                    "max_shell_moment", "max_frame_moment"],  # optional; all
     "tolerance_pct": 5.0,         # optional; (0, 100]
     "max_elements": 20000,        # optional; finest-level cap
     "model": {...}}               # optional; default the current model

* Default `cases`: every static case whose `effective_geometric` is
  `"linear"`, plus `MODAL` when `num_modes > 0`. Cases listed in
  `cases_not_run` are left out. An explicit P-Delta or large-displacement
  case is a 400 error.
* Errors (400 `{"error"}`): bad levels, unknown case, `MODAL` with
  `num_modes == 0`, unknown quantity, nothing to study, or more than
  `max_elements` shell elements plus frame segments at the finest level.
* Each level runs on a refined DEEP COPY through the normal engine:
  `OpenSeesEngine(copy).run_static(case)` and
  `run_modal(min(num_modes, 3))`. No separate solver is involved.
  - Level 1 is the model as defined. Its values equal a normal run
    exactly (tested).
  - 4-corner structured shells: `mesh_divisions = {"n1": f*nx, "n2":
    f*ny}`, where `(nx, ny)` are the level-1 divisions. The refinement is
    strictly nested.
  - Polygon-mesher shells (3 or 5+ corners, or `floor_mesh` set):
    `mesh_size`, `floor_mesh.max_size` and `mesh_divisions.max_size` are
    divided by `f`; `mesh_divisions.n1/n2` are multiplied by `f`.
  - Membrane-behaviour slabs are never meshed, so they are left alone.
  - Frames: the member's effective auto mesh (its own dict, else the
    model default, else all off) is copied onto the member with
    `min_segments = f * (min_segments or 1)` and `max_length / f`.
    Axial-only (Truss) members are skipped.

Response:

    {"levels": [1, 2, 4], "cases": [...], "quantities": [...],
     "tolerance_pct": 5.0,
     "mesh": [{"level", "n_nodes", "n_shell_elements",
               "n_frame_segments", "elapsed_s"}, ...],
     "rows": [{"case", "quantity", "key", "label",
               "dim": "length"|"time"|"force"|"moment"|"moment_per_length",
               "values": [per level],
               "rel_change_pct": [null, %, ...],   # (v_k - v_k-1)/|v_k|
               "richardson": float|null, "order": float|null,
               "order_assumed": bool,
               "est_error_pct": float|null, "converged": bool,
               "verdict": "converged within 0.19%"
                          | "not converged (est. error 7.1% > 5%)",
               "point": [x, y, z]}],               # roof_disp rows only
     "summary": {"n_rows", "n_converged", "all_converged",
                 "max_est_error_pct"},
     "warnings": [...]}

Units are SI: m, s, kN, kN-m, kN-m/m. `dim` tells the UI which unit
conversion to apply.

Row keys:

| quantity | key(s) | value |
|---|---|---|
| `roof_disp` | `roof_disp:0/1/2` (UX/UY/UZ) | Signed displacement at the roof point. At level 1 the code picks the roof node (highest structural node z) and the component with the largest magnitude, then reports that component at the same point on every level (nearest node if a non-nested mesh lacks it). |
| `periods` | `T1`..`T3` | MODAL periods. |
| `base_reactions` | `base:FX`..`base:MZ` | Base totals. Only components above 1e-6 of the largest force (or moment) at level 1 are reported. |
| `max_shell_moment` | `max_shell_moment` | Max abs Mxx or Myy over all gauss-averaged shell resultants. |
| `max_frame_moment` | `max_frame_moment` | Max abs M2 or M3 over all frame stations. |

Richardson extrapolation (`richardson(levels, values)`):

* The mesh size is `h = 1/level`.
* With 3 or more levels, the observed order `p` comes from the last
  three levels by bisection of
  `(f1-f2)/(f2-f3) = (h1^p - h2^p)/(h2^p - h3^p)`. This handles unequal
  ratios such as 1/2/3.
* With only 2 levels, or a non-monotone sequence, or `p` outside
  [0.5, 6], the code uses `p = 2` instead (`order_assumed: true`).
* The estimate is `f_n + (f_n - f_n-1)/((h_n-1/h_n)^p - 1)`. A flat
  sequence returns `f_n`.
* `est_error_pct = |f_n - est| / |est| * 100`.
* `converged` is `est_error_pct <= tolerance_pct`.
* In the verdict, `X` is rounded UP to 2 significant digits. Values
  below 0.005 % are shown as `<0.01%`.
* The fit is exact on `f = C + A h^p` (tested for p = 1, 1.5 and 2, and
  for 1/2/4 and 1/2/3 levels).

Validation (`tests/test_mesh_study_advisor.py`):

* Hard SS square plate: a = 4, t = 0.05, nu = 0.3, q = 10, ShellMITC4.
  Edges use vertical line-spring penalties, so the support follows every
  mesh level. Levels 1/2/4 give a 4/8/16 mesh.
  - Centre deflection: w/w_ex = 0.9790, 0.9974, 1.0024, rising
    monotonically. Richardson gives 1.0043 (p = 1.87), against Timoshenko
    w = 0.00406 qa^4/D. The 0.4 % excess is Mindlin shear at a/t = 80.
  - Max moment: 6.52, 7.39, 7.60. Richardson gives 7.678, against
    0.0479 qa^2 = 7.664, an error of 0.19 %.
  - Fundamental period (vertical self mass): Richardson is within
    0.2 % of the Navier T11.
  - Base FZ = qa^2 on every level.
* Cantilever wall: 2 x 6 m, t = 0.2, nu = 0, rigid top diaphragm, top
  shear 100 kN.
  - Tip deflection: 1.968e-3, 2.207e-3, 2.278e-3, rising monotonically
    from below. Richardson gives 2.3082e-3 (p = 1.75), against
    PH^3/3EI + PH/(5/6 GA) = 2.304e-3, an error of 0.18 %.
* An elastic frame-only model is mesh independent: every row is flat and
  converged.
* The saved model file and `GET /api/model` stay byte-identical after
  the study runs.

### GET | POST /api/accuracy_advice

* `GET` checks the current model.
* `POST` takes `{"model"?: {...}, "thresholds"?: {...}}`. A body model
  is loaded leniently, the same way Check Model does it.
* The advisor is static: it runs the mesher only and never solves. It
  never raises on model defects. An unknown or non-positive threshold
  returns 400.

Response:

    {"advice": [{"severity": "warning"|"info", "code", "title",
                 "message",        # what was found (counts, worst value)
                 "explanation",    # why it affects accuracy
                 "fix",            # suggested remedy
                 "count",          # objects affected
                 "objects": [uid],                       # <= 50
                 "refs": [{"type": "member"|"shell", "uid"}],
                 "details": [{"uid", ...metric}]}],      # <= 50
     "summary": {"warnings", "info", "by_code": {code: count}, "ok",
                 "n_shells", "n_members"},
     "thresholds": {"aspect_max": 4, "warp_max": 0.01,
                    "min_elements_per_span": 4, "thick_ratio": 0.1,
                    "thin_ratio": 0.02, "opening_ratio": 0.25}}

There is one advice item per code. Warnings sort before info.

| code | severity | trigger | details |
|---|---|---|---|
| `SHELL_ASPECT_RATIO` | warning | Meshed element with longest/shortest side > `aspect_max`. | `max_aspect`, `n_elements` |
| `SHELL_WARPED` | warning | 4-node element warp > `warp_max`. Warp = max corner distance from the mean plane (normal = diagonal cross product) / sqrt(area). | `max_warp`, `n_elements` |
| `SHELL_MESH_COARSE` | warning | Shortest in-plane extent / largest element side < `min_elements_per_span`. | `span`, `element_size`, `elements_across` |
| `MEMBRANE_SLAB_BENDING` | warning | (a) Membrane-behaviour slab with an edge less than 99 % covered by collinear beams. (b) Meshed shell of `shell_type` membrane carrying out-of-plane load: slab area loads, self-weight or a load set; a wall with `wind_cp`. | `reason`, `edges` |
| `PLATE_THIN_ON_THICK` | warning | `shell_thin` or `plate_thin` with t/span > `thick_ratio`. | `shell_type`, `t_over_span` |
| `PLATE_THICK_ON_THIN` | info | `shell_thick` or `plate_thick` with t/span < `thin_ratio`. The legacy `shell` type is never flagged. | `shell_type`, `t_over_span` |
| `RIGID_DIAPHRAGM_OPENINGS` | warning | Slab whose opening area / gross area ≥ `opening_ratio`, under a rigid diaphragm. The diaphragm comes from the named diaphragm type when assigned, else `effective_diaphragm(story)`. | `opening_ratio` |
| `CRACKED_MODIFIERS_MISSING` | warning | Concrete frame section with `mod_I33 == mod_I22 == 1`, or concrete shell section with `mod == m11 == m22 == m12 == 1`. Concrete means `material_type == "concrete"` and E < 60 GPa. | `section` / `shell_section` |
| `MASS_SOURCE_MISSING` | warning | A dynamic analysis is requested (MODAL with `num_modes > 0` and not switched off, or any RS/TH case not switched off) and total mass is 0. Total mass covers story masses from the mass source, nodal masses, additional mass, and element self mass in `element_self_mass` mode. | (no objects) |
| `FRAME_POINT_LOAD_NO_AUTOMESH` | warning | Member (not axial-only) with no effective auto mesh, a point or moment `MemberLoad` at 0 < a < 1, and attached to a meshed shell (an end on a shell mesh node, or split by the shell mesh). | `load_positions`, `shell_joints_at_ends`, `analysis_segments` |

### UI

* Analyze > Mesh Sensitivity Study… opens a dialog. It sets levels,
  cases, quantities and tolerance, then shows a results table and a
  normalised convergence chart.
* Model > Accuracy Advisor… opens a panel listing the advice. Clicking an
  item selects its `refs`.
* Both work in `?mock=1` (`js/mock_accuracy.js`).

## Ratio-based story checks: negligible-drift significance

Some story checks divide one response quantity by another of the same kind.
In a direction a case does not load, both are round-off. One example is Y
under an X force on a doubly symmetric plan, where drifts are about 1e-15 m.
The quotient is then a random number. Before this change it could raise
"extreme" torsional irregularity, or an extreme soft story, at random. Such
a ratio is now reported as not applicable. Logic:
`skyframe/core/ratio_significance.py`.

### Rule

A quantity `q` in a ratio's denominator is negligible when

    |q| < max(1e-9 m, 1e-6 * ref)

* `ref` is the largest story displacement `|ux|`, `|uy|` of the case, over
  all stories and towers (the case's `story` block). It bounds every
  interstory drift (`Delta <= 2 ref`) and sets the round-off scale of a
  linear solve.
* `1e-6` sits about ten orders of magnitude above double-precision
  round-off. On the reference models, noise is at most 4e-10 of the
  threshold and genuine response at least 5e4 of it.
* `1e-9 m` is a floor for cases whose whole response is round-off.

Ratios whose denominator passes the gate use the legacy expressions
unchanged. Results for loaded directions are byte-identical.

| check | where | denominator tested | n/a output |
|---|---|---|---|
| ASCE 7 §12.3.2.1 torsional ratio (Type 1a/1b) | `irregularity[case][story].tors_ratio_x/_y` | `delta_avg = (\|u+rz B/2\| + \|u-rz B/2\|)/2` in that direction | `null` + reason |
| same, per named diaphragm (static cases, additive combos, RS cases) | `diaphragms.{cases,combos,rs_cases}[case][story][diaphragm].tors_ratio_x/_y` | same, per diaphragm | `null` + reason |
| same, story level of multi-diaphragm stories | `irregularity[...]` | max over the applicable diaphragm ratios | `null` + reason only when none applies |
| story stiffness `V / Delta` | `story_stiffness[case][story].kx/ky`, table `story_stiffness` | interstory drift `Delta` | `0.0` + reason (table: `0.0`) |
| soft story (Table 12.3-2) | `irregularity[...].stiff_ratio`, `soft_flag` | `Delta` of this story, the story above, and the 3 above | `null` + reason when this story or the one above is negligible. Negligible stories drop out of the average of 3. |
| Diaphragm Max Over Avg displacement / drift ratio | table `diaphragm_max_avg_drifts`: `disp_ratio`, `drift_ratio` | `avg_disp`; `avg_drift * h` | `null` cell + one table warning |

`flag` is the 1.2 / 1.4 rule over the applicable ratios only. It is "none"
when no ratio applies. `soft_flag` is `""` when `stiff_ratio` is n/a. Flag
values are unchanged, so existing consumers that test `flag !== "none"`
keep working.

### JSON

The n/a slot keeps its key and type slot: `null` for ratios, `0.0` for
stiffness, which was already the "no stiffness" value. The entry gains
`na_reason` (a map from slot key to reason string), present ONLY when at
least one slot is n/a. It is the last key of the entry:

```jsonc
"irregularity": {"EQX": {"Story1": {
   "tors_ratio_x": 1.0000000000000002, "tors_ratio_y": null,
   "flag": "none", "stiff_ratio": 1.5931, "soft_flag": "",
   "na_reason": {"tors_ratio_y": "not applicable (negligible drift)"}}}},
"story_stiffness": {"EQX": {"Story1": {
   "kx": 287241.75, "ky": 0.0,
   "na_reason": {"ky": "not applicable (negligible drift)"}}}},
"diaphragms": {"cases": {"EQX": {"S1": {"DA": {
   "ux": 4.46e-4, "uy": 1.9e-22, "rz": -1.3e-21, "drift_x": 1.5e-4,
   "drift_y": 6.6e-23, "tors_ratio_x": 1.0, "tors_ratio_y": null,
   "na_reason": {"tors_ratio_y": "not applicable (negligible drift)"}}}}}}
```

The only reason string today is `"not applicable (negligible drift)"`.
Clients should display the string, not test for it. Table rows keep exactly
their column keys. The reason goes into the table's `warnings`, for example
`"Disp/Drift Ratio not applicable (negligible drift) in 2 cell(s): ..."`.

### Scope notes

* Story drift ratios `drift_x/drift_y = Delta / h` divide by the story
  height, not by noise, so they are unchanged. CM/CR (`cr = master -
  theta/phi`) divides by the torsional flexibility `phi`, which is
  non-zero for any diaphragm. Round-off in `theta` therefore moves the CR
  by about 1e-12 m and needs no gate. There is no weight/mass
  irregularity check in the backend. Time-history story peaks contain no
  ratios. Per-tower diagnostics use the legacy routine per tower, with the
  reference taken over the whole case.
* RS rows go through the same gate. Under the default band eigen path, a
  degenerate (square-plan) RS case can leak about 1e-4 of the response
  into the orthogonal direction (the umfpack path gives about 1e-9). That
  is above the round-off gate, so the ratio is reported. Its value stays
  about 1.0 because the RS `rz` peak is round-off.
* Determinism: under repeated runs with the same solver, diagnostics are
  byte-identical. Across `analysis_options.solver` = auto/band/umfpack, the
  n/a slots, reasons and flags are identical, and the loaded ratios agree
  to 1e-9.
* UI: n/a slots show "n/a", with the reason as a tooltip, in Results >
  Diagnostics, the report, the CSV export and the Diaphragms card. The
  `?mock=1` results emit the same shape.

## Meshed-model performance: parallel solves and fewer rebuilds

Large models (the sparse factor-once solver policy, ≥ 300 estimated
equations) run their independent solves on several processes and build
the OpenSees domain once per run. Results JSON is unchanged: it is
byte-identical to the serial run and to v1.19.0 (same SHA-256 on every
benchmark). Profiles and numbers are in `docs/PERFORMANCE.md` "Meshed
models: parallel solve families". The tests are in
`tests/test_meshed_perf.py`.

### Model field `analysis_options.parallel` (new key, optional)

```json
{"analysis_options": {"parallel": "auto" | "off" | 1..8}}
```

* `"auto"` (default): `min(cores, 8)` processes when the process may use
  at least 4 cores, otherwise serial.
* `"off"` or `1`: serial.
* An int `2..8`: that many processes, whatever the core count.
* `to_dict` emits `analysis_options` only when it differs from the
  defaults, so `{"parallel": "auto"}` and default models serialize
  byte-identically. `from_dict` round-trips it.
* `validate()` raises `ValueError` on any other value, including bools,
  floats, 0 and 9.
* `SKYFRAME_PARALLEL=0` or `SKYFRAME_SLOW_PATH=1` forces serial.
* Parallel runs need `os.fork` (Linux). Other platforms run serial.

### Engine behaviour (`engine/parallel.py`)

* Four families run in parallel:
  * the plain linear static cases;
  * the condensed-modal unit-load columns (one per massed DOF);
  * the mode-shape solves with their unit modal responses;
  * the solve-free RS cases (legacy CQC/SRSS on cached unit responses).
* Static cases with Newton paths (axial-only members, gap links,
  compression-only springs) or a non-linear `geometric` still run serially.
* A family runs like this:
  * The parent builds the domain and factors it.
  * Each worker is a `fork` of the parent. It inherits the live domain and
    the factorization, solves its items and returns pickled Python
    results.
  * The parent merges them by item index.
* The plan is deterministic: item `k` of `n` goes to lane
  `(n − 1 − k) mod L`, and lane 0 is the parent, which solves the last
  item last.
* A worker that fails or dies has its items re-solved by the parent.
* `OpenSeesEngine.case_times` (run log) includes the prefetched cases'
  own solve times.
* One build per run: on large models `run_modal` reuses the live elastic
  domain. The RS cases' modal cache hits and the CR solves no longer
  rebuild.
* Small (BandGeneral) models are untouched: the serial legacy path,
  byte-identical.

### Mesher

* `core/mesh.mesh_model` filters shell nodes by each member's bounding box
  before the unchanged on-axis test. The resulting mesh is identical.
* Meshing a 20-story 8x6-bay meshed building drops from 14.1 s to 1.6 s.

## Multiple towers — follow-ups

Follow-ups to "Multiple towers (Define > Towers)" (C9). They lift the
limits listed there. Everything is inert without `towers` (and without
the new optional keys): legacy models, results, tables and pushover /
nonlinear static / staged / TH / FNA runs are byte-identical (sha256 of
the model JSON, full `run()` JSON, TH, TH with gravity + plot-function
requests, FNA, pushover, staged and the Story Definitions table / CSV of
a representative 3-story model before and after).

Code: `skyframe/core/towers_ext.py` (model side) and
`skyframe/engine/towers_ext.py` (engine side). Shared files carry
one-line hooks only.

### 1. Time-history and FNA story shear per tower

With towers, the story shear of a direct-integration time history is the
same element cut that static cases use (section "Analysis per tower"):
the horizontal free-body force on the plane just below each level, through
the tower's own frame segments, shell quads and links, minus the reactions
of its supports above the plane. It is evaluated on the converged state
of every step.

* Series are the increment past the held gravity state; sample k is
  t = (k+1) dt, like every TH series.
* Element forces are restoring (resisting) forces. Rayleigh / modal
  viscous damping forces are not element forces, so they are not in the
  cut (ETABS story forces from element forces). The legacy
  inertia-equilibrium rule (`-Σ m a_total` above the level) includes
  them; for an undamped run the two coincide to solver precision.
* FNA: the linear structure's element forces are linear in the modal
  coordinates, so `V(t) = Σ_i q_i(t) V_i`, where `V_i` is the per-tower
  cut of the static solve `K u = ω_i² M φ_i` (solution `φ_i`; the same
  per-mode solve response spectrum uses). Nonlinear device links (damper,
  gap, hook, isolator) crossing a plane of their own tower add their
  force not carried by the linear basis, `F_dev − k_lin d`, at the upper
  end.

Results (TH / FNA, only with towers):

```
"peaks": {"story": {S: {..., "shear_x": peak |V_x| (element cut),
                         "shear_y": ...}}, "base": {...}},   // unchanged
"towers": {"T1": {"stories": ["S1", ...],
                  "base_shear": {"FX": peak |V| of the lowest story,
                                 "FY": ...}}},
"story_shear_method": "element_cut"
```

History on request: `TimeHistoryCase.output_requests.story_shears = true`
(new optional key, boolean; `false` / absent = not recorded). The TH and
FNA results then carry

```
"story_shear_history": {"method": "element_cut" | "inertia",
                        "VX": {story: [len(t) values]},
                        "VY": {story: [...]},
                        "story_tower": {story: tower}}   // only with towers
```

Without towers the requested history is the legacy inertia series
(`method: "inertia"`); peaks are unchanged. A request holding only
`story_shears` records no `plot_functions` block. FNA honours
`story_shears` (it still ignores the other request keys).

Validation (`tests/test_towers_followups.py`):

* **Two identical disconnected towers** against the same tower alone
  (a one-tower model; modal damping, because the Rayleigh fit is anchored
  on modes 1 and 3, which differ between the two models): every sample
  of every per-tower story-shear history equal to 1e-8 (observed: exact),
  peaks and `towers.base_shear` equal. Same for FNA (9 / 18 modes).
* **Cut against the legacy rule.** Single tower, damping 1e-9:
  * the lowest story's cut equals `−base_FX` at every step (1e-9);
  * every story's cut history equals the legacy inertia history
    (1e-6 of the peak).
* **FNA against direct integration.** All modes kept, Rayleigh 5 %:
  FNA cut histories equal the direct-integration cut histories (1e-6 of
  the peak).
* **Coupled towers.** Link beams: `V(A1) + V(B1) = −base_FX` at every
  step. Podium: `V(P1) = −base_FX` at every step, and its peak equals the
  peak base shear.
* **Gravity stage.** A held `gravity` stage leaves the cut history
  unchanged (the series is the increment).
* **Response spectrum against static** (link-beam towers, no rigid
  diaphragm, 4 modes, SRSS, flat Sa = 0.6 g): each per-tower RS story
  shear equals the SRSS of the per-tower static cut shears of
  independent static load cases. Each case applies `f_i = m φ_i Γ_i Sa g`
  as ordinary nodal loads (1e-6).

Note: the legacy inertia rule evaluates the ground acceleration of the
last record sample at `accel[n-1]`, while the OpenSees `Path` series
returns 0 there (round-off past its end), so the legacy peak can be
inflated by `M·accel[n-1]`. The element cut does not have this artefact.
Records ending in 0 are unaffected. The legacy rule is left unchanged.

### 2. Model tables: Story Definitions tower column

With towers, the Story Definitions table (`POST /api/modeltables/stories`,
CSV, zip) has a fourth column:

```
{"key": "tower", "label": "Tower", "type": "enum", "quantity": "none",
 "enum": [tower names], "editable": true}
```

* A row's `tower` is the story's tower. Untagged stories show the
  default (first) tower.
* Writing the default tower removes the tag (the canonical form).
  Writing another tower sets `Story.tower`.
* After an edit, elevations are recomputed tower by tower (as before).
  Every tower's `stories` list is rebuilt from the tags, so renamed,
  added and moved stories keep `towers` consistent.
* An unknown tower is a cell error (`col: "tower"`).
* Unchanged rows, the CSV round trip and the zip round trip return the
  identical model.
* Without towers the column is absent from `columns`, the rows and the
  CSV (legacy output). A stray `tower` cell is ignored.

### 3. Pushover / nonlinear static control tower

There is a new optional field on `PushoverCase` and `NonlinearStaticCase`:

| field | default | meaning |
|---|---|---|
| `control_tower` | `null` | the tower whose top story controls the case |

With `control_tower` set:

* the monitored joint is the tower's top-story diaphragm master, else
  its lowest-tag node at that level;
* for the default `roof_point` pushover distribution, the unit push acts
  there too (`distribution.story_forces = {top story: 1}`);
* `H_ctrl` is that story's elevation (the target is
  `target_drift × H_ctrl`).

`control_tower` is emitted by `to_dict` only when set. The default
(`null`) keeps today's rule: the last story of `stories`.

Validation (ValueError):

* an unknown tower;
* `control_tower` without `towers`;
* `control_tower` together with `control_story` or `control_point`.

Tests:

* Podium, `control_tower = "T1"`: the control node is the A3 master and
  `H = 15 m`. Every step's `V/u` equals `1/f`, where `f` is the A3
  displacement of a linear static unit story force at A3 (1e-6). The
  final displacement is `0.001 × 15` m.
* The default still monitors the B2 master (the last story) at 12 m.
* NLS with `control_tower = "T2"` monitors the B3 master. Its final
  displacement equals the linear static `ux` of B3 (1e-8).
* Round trips and errors are checked.

### 4. Named diaphragms × towers: split per tower

A named diaphragm is split per tower. It never ties two towers together.

* With towers, a story is *named* only when a joint or shell diaphragm
  assignment at its elevation belongs to its OWN tower (joint:
  `point_tower`; shell: `object_tower`). An assignment in tower T1 no
  longer strips the legacy diaphragm off tower T2's story at the same
  elevation.
* Each named story is partitioned over its own tower's nodes only. So one
  name used in several towers gives one diaphragm (own master, mass,
  constraint) per (tower, story), reported under each tower's story in
  `results.diaphragms.stories`.
* `towers_ext.diaphragm_towers(model)` lists, for each name, the towers
  it is split over.

Fix found on the way: the engine's per-model uid lookup memo could be
built inside a per-tower view, and other towers' members were then
missing (named-diaphragm tributary masses of the non-first towers read
0). `_as_view` now isolates the memo.

Tests:

* Twin towers with `DA` assigned on both towers' first-floor joints:
  two groups `(A1, DA)` and `(B1, DA)`, each over its own tower's nodes,
  with different masters.
* The periods equal the legacy per-tower diaphragms (1e-8). EQX shears
  are 60 kN in each tower.
* `DA` on tower T1 only: B1 keeps its rigid diaphragm, and the EQX
  story results equal the model without `DA` (1e-8).

### 5. Staged construction across towers

**`per_story` stages.** The stages follow the tower-stories in ascending
elevation, ties broken by tower order, then model order
(`towers_ext.stage_order`). For the podium example the order is P1, P2,
A1, B1, A2, B2, A3.

* A member, shell or link goes to its `story` when set. Otherwise it goes
  to the story of its OWN tower that owns its top elevation. Nodal loads
  use the joint's tower the same way.
* Every stage sub-model carries the towers that already have a story.
  Each (tower, story) therefore keeps its own diaphragm, and supports,
  springs and panel zones are kept only at joints of built objects.
* The `time_dependent` (AAEM) option follows the same stage order.
* Results keep the model story order.

**User-defined stages** (groups). Each stage sub-model carries the
towers. A story is active when an active point at its elevation belongs
to its tower.

Tests:

* Twin towers per_story: every member force of both towers equals the
  single-tower staged run (1e-8). `FZ` is doubled, and the column axial
  sequence difference is equal.
* Podium per_story:
  * staged `FZ` = one-shot `FZ` = the total gravity load (1e-9);
  * ground-column axial sum equals the one-shot sum;
  * the sequence effect is non-zero.
* User stages "add + load tower A" then "add + load tower B" on
  disconnected towers equal the one-shot static case (1e-7).

Before this follow-up, both modes of a tower model gave singular stage
solves.

### UI

* **TH results tab.** With towers, a "Story shear per tower" block lists
  each tower's stories, their peak |Vx| and |Vy|, and the tower's base
  shear row.
  * When `story_shear_history` is present, it charts Vx / Vy of the
    selected story.
  * A "Record story-shear history" check sets
    `output_requests.story_shears` on the case. It takes effect on the
    next run.
* **Edit > Interactive Database Editing > Story Definitions.** The Tower
  enum column is shown only with towers.
* **Pushover "Load Application" and Nonlinear Static case dialogs.** The
  "Monitored displacement" control offers a "Tower" choice (only with
  towers) with a tower selector, which writes `control_tower`.
* `?mock=1` supports all of the above (`js/mock_towers_fu.js`).

## Frame local axes: ETABS convention

A model field selects how frame members get their local 1/2/3 axes.
`skyframe/core/frame_axes.py` is the only Python implementation and
`js/frameaxes.js` is the only JS one. Every consumer goes through them:

* engine `_local_axes`: geomTransf, fiber / hinge / designer sections,
  auto-mesh segments, member loads, thermal gradients, insertion and
  joint offsets, story shear, tendons, towers, staged and tables;
* `buckling._local_axes` and the chord angles of curved frames;
* the viewer: `polygeom.memberAxes`, the extruded view, the local-axes
  overlay, the 3D force diagrams and the nonprismatic taper overlay.

Panel zones use only the column's scalar b/h, so they need no axes.

### Model field

`frame_axis_convention`: `"legacy"` | `"etabs"`.

* **Absent key.** `from_dict` reads an absent key as `"legacy"`. Old files
  load, analyse and round-trip byte-identically.
* **Serialisation.** `to_dict` writes the key only when it is `"etabs"`.
* **Python default.** The dataclass default is `"legacy"`.
  `quick_building()` and the Python API are unchanged.
* **New models use `"etabs"`.** This covers:
  * the server's startup model and `POST /api/model/quick` (File > New
    templates and Quick Building);
  * `POST /api/import/<fmt>` and the importers `import_dxf`,
    `import_e2k` and `import_ifc`;
  * the mock's `mockModel`.
* **Bad values.** Any other value raises `ValueError` (400 from
  `POST /api/model`).
* **Member stamp.** Each `FrameMember` carries a non-serialised
  `axis_convention` stamp. It is set by `from_dict`, `add_member`, the
  engine entry (when the model says `"etabs"`) and the conversion.

### Local axes by member orientation (angle 0)

| member | legacy 1 | legacy 2 | legacy 3 | etabs 1 | etabs 2 | etabs 3 |
|---|---|---|---|---|---|---|
| beam along +X | +X | +Z | −Y | +X | +Z | −Y |
| beam / brace, general (non-vertical) | i→j | vertical plane, up | 1×2 | i→j | vertical plane, up | 1×2 |
| column drawn bottom-up (i below j) | +Z | −Y | +X | +Z | **+X** | **+Y** |
| column drawn top-down (i above j) | −Z | +Y | +X | −Z | **+X** | **−Y** |

* **Vertical members.** A member is vertical when its plan projection is
  shorter than 1e-6 m.
* **geomTransf `vecxz`.** It is the rotated local 3: legacy `(1,0,0)`;
  etabs `(0,1,0)` drawn bottom-up and `(0,−1,0)` drawn top-down.
* **Section depth.** The depth `h` lies along local 2, so I33 = b·h³/12.
  Under `"etabs"` a column's depth runs along global X at angle 0.
* **Top-down columns.** As in ETABS, local 1 stays i→j and local 2 stays
  +X, so local 3 = 1×2 = −Y. The section's physical orientation does not
  depend on the drawing sense.
* **Angle.** `angle` (degrees) rotates local 2/3 about local 1 by the
  right-hand rule in both conventions. Exact multiples of 90° use exact
  cos / sin under `"etabs"`. The legacy branch keeps its historical
  floating-point evaluation.
* **Curved frames.** The chord angles are measured from each chord's own
  default axes, and the ± sense of the curve's local-2 target is chosen
  from the legacy axes. A curved member is therefore physically identical
  under both conventions.

### Conversion

**UI.** Options > Convert to ETABS Axis Convention… (one undo step).

**API.** `POST /api/model/convert-axes` with body `{model?}`. The default
is the current model, and the converted model becomes current. The
response is:

```
{"model": <model dict>,
 "report": {"converted": bool, "from": "legacy"|"etabs", "to": "etabs",
            "members": [{"uid", "old_angle", "new_angle"}]}}
```

Malformed input returns 400.

**What changes.**
* Every straight vertical member gets `angle − 90`, wrapped to
  [−180, 180), so its local axes, b/h directions and results are
  unchanged.
* Curved members and non-vertical members are untouched.
* An `"etabs"` model is left as it is (`converted: false`).

**Validation** (`tests/test_frame_axes.py`):
* For angle-0 columns, the results JSON has the same SHA-256 before and
  after conversion.
* For any angle, results agree to 1e-9 relative.
* For a cantilever column with b=0.3, h=0.6 under tip Fx:
  * etabs: δ = FL³/(3E·bh³/12) (strong axis);
  * legacy: δ = FL³/(3E·hb³/12) (weak axis);
  * under Fy the roles swap;
  * etabs at angle 90 equals legacy at angle 0;
  * conversion keeps δ identical.

`GET /api/model/info` adds `"frame_axis_convention"`.

### UI

* **Model Info.** Shows the convention.
* **View > Extruded View** (Alt+X; the shortcut registry is checked for
  clashes before registering) and the quick-access toolbar View group's
  **Extrude** toggle turn the extruded view on and off.
* **Local axes overlay.** View > Show Local Axes and the toolbar **Axes**
  toggle draw arrows 1/2/3 (red / green / blue) at member midpoints, in
  both the line and the extruded 3D view.
* **Test hook.** `__sky.frameAxes` exposes `{convention, convert,
  openConvert, toggleExtrude, toggleAxes, axesOf}`.
  `__sky.vx.meshOf(uid)` returns the extruded vertices of each part.

## v1.21 material library

Built-in code material libraries for ANALYSIS: the properties the analysis
uses (E, ν, G, unit weight, mass density, thermal α) plus the reference
strengths (fc / fy / fu) that the existing nonlinear concrete and steel laws
read. There are no design checks.

### Data files

`skyframe/data/materials/<standard>.json` ships as package data (pyproject
`"skyframe" = ["data/materials/*.json"]`; PyInstaller's `collect_all` picks it
up). The five standards:

| id | Standards covered |
|---|---|
| `astm_aci` | US: ASTM, AISC 360-16, ACI 318-19, ADM 2020, AISI S100-16 |
| `eurocode` | EN 1992-1-1, EN 1993-1-1 / 1-3, EN 1999-1-1 |
| `is` | IS 800, IS 456, IS 2062, IS 1786, IS 1161, IS 4923, IS 14268 |
| `bs` | BS EN + UK NA, plus the legacy BS 5950 / BS 8110 grades |
| `as_nzs` | AS 4100, AS 3600-2018, AS/NZS 4671 / 4672 / 4600 |

A file looks like this:

```json
{"standard": "...", "edition": "...", "source": "...", "units": "SI (...)", "notes"?: "...",
 "materials": [
  {"name": "C30/37", "kind": "concrete", "group": "EN 1992-1-1",
   "E": 32836568031.33079, "nu": 0.2, "G": 13681903346.38783,
   "unit_weight": 25000.0, "mass_density": 2549.29, "alpha": 1e-05,
   "fy": null, "fu": null, "fc": 30000000.0,
   "E_basis": "formula", "E_formula": 32836568031.33079, "E_table": 33000000000.0,
   "fcm": 38000000.0, "fcu": 37000000.0,
   "source": "EN 1992-1-1:2004 Table 3.1 ...; nu = 0.2 (3.1.3(4)); ...",
   "formula": "Ecm = 22 (fcm/10)^0.3 GPa = 22 (38/10)^0.3 = 32.84 GPa ..."}]}
```

**Units.** Records use SI base units:

* E, G, fy, fu and fc in Pa;
* `unit_weight` in N/m³;
* `mass_density` in kg/m³;
* `alpha` in 1/K.

**Kinds and names.** `kind` is one of `steel`, `concrete`, `rebar`,
`tendon`, `aluminum` or `cold_formed`. Names are the published designations
and are unique within a file.

**Optional keys:**

* `E_basis`: `formula`, `table`, `code value` or `typical value`.
* `E_formula` and `E_table`: both are stored where the code gives both.
* `G_code`: the code's tabulated G where it differs from E/2(1+ν), for
  example AISC 11,200 ksi or EN 1999 27,000 MPa. The analysis always uses
  E/2(1+ν).
* `Ry`: from AISC 341-16 Table A3.1, for ASTM steels only.
* `fcm` (EC2), `fcu` (cube), `fcmi` (AS 3600) and `notes`.

**Invariants.** The tests pin these:

* `G == E/(2(1+nu))`, which is what `Material.G` derives;
* `unit_weight == mass_density * 9.80665`;
* every record cites a clause or table in `source` / `formula`.

**Which E is used:**

| Code | E used | Kept for reference |
|---|---|---|
| EN 1992 | the formula value (C30/37: 32.84 GPa) | `E_table` (33 GPa) |
| AS 3600 | the Table 3.1.2 value, as the code directs (N32: 30,100 MPa) | `E_formula` (29,910 MPa) |
| ACI | 57,000√f'c psi for psi grades; 4,700√f'c MPa for MPa grades | — |
| IS 456 | 5,000√fck | — |
| BS 8110-2 | eq. 17: 20 + 0.2 fcu GPa | — |

For BS 8110 cube grades, `fc` is the cylinder fck of the EN 206 class with the
same cube strength.

**Unit weights used:**

| Material | Value |
|---|---|
| ACI concrete | 150 pcf = 23.56 kN/m³ |
| EN and IS concrete | 25 kN/m³ (reinforced) |
| BS 8110 and AS 3600 concrete | 2,400 kg/m³ |
| Steel | 7,850 kg/m³ |
| ASTM steel | 490 pcf |

### Mapping onto `Material`

`skyframe.core.material_db.record_to_material` adds **no new Material
fields**, so existing models stay byte-identical. It maps a record as follows:

| Material field | From the record |
|---|---|
| `E`, `fy`, `fu`, `fc` | value / 1000 (kPa) |
| `unit_weight` | value / 1000 (kN/m³) |
| `mass_density` | value / 1000 (t/m³) |
| `nu`, `alpha` | unchanged |
| `material_type` | `kind`, with `cold_formed` → `coldformed` |
| `symmetry` | `uniaxial` for rebar / tendon, otherwise `isotropic` |
| `Ry` | the record's value, else the default 1.1 |
| `color` | the per-type colour swatch |
| `notes` | `"<standard> library: <name> — <source>"` |
| `stress_strain` | `null` |

### Loader

`skyframe/core/material_db.py`:

| Function | Returns |
|---|---|
| `standards()` | `[{id, standard, edition, count, kinds: {kind: n}, notes}]` |
| `materials(standard, kind=None, query=None)` | matching records |
| `material(standard, name)` | one record |
| `material_from_db(standard, name, material_name=None)` | a `Material` |
| `listing(standard, kind=None, query=None)` | records, each with `material` added |
| `import_into(model, standard, names, on_clash="suffix", rename=None)` | `[{name, as, replaced}]` |

* `materials`: `query` is a case-insensitive substring of the name or group.
  An unknown standard raises `KeyError`; an unknown kind raises `ValueError`.
* `listing`: `material` is the exact `Material.to_dict()` an import stores.
* `import_into` handles a name that is already taken according to
  `on_clash`:
  * `"suffix"` adds `NAME-2`, `NAME-3` and so on;
  * `"replace"` overwrites the material, and sections keep the name;
  * `"error"` raises `ValueError`.
* `import_into` is atomic: it resolves every name and validates every
  material before it writes anything.

### API

`skyframe/api/material_db_api.py`, registered with one line in `create_app`.

| Endpoint | Returns | Errors |
|---|---|---|
| `GET /api/material_db/standards` | the `standards()` list | — |
| `GET /api/material_db/materials?standard=&kind=&q=` | `{standard, materials: [record + material]}` | unknown standard 404; bad kind 400 |
| `GET /api/material_db/material?standard=&name=` | `{standard, record, material}` | unknown 404 |
| `POST /api/material_db/import` | `{imported: [{name, as, replaced}], model}` | see below |

The import body is `{standard, names: [..], on_clash?: "suffix"|"replace"|"error",
rename?: {record name: model name}}`. It adds the materials to the current
model. Its errors:

* 404: unknown standard or name;
* 409: a clash when `on_clash` is `"error"`;
* 400: a bad body or empty `names`.

### UI

The UI lives in `js/matlib.js`, `js/mock_matlib.js` and the generated
`js/matlib_data.js`.

**Entry points:**

* **Define › Material Library…** (`act` `def-matlib`, added with
  `sky.menu.add`) works on its own draft and commits it on Import.
* **Define Materials › Add from Library…** (`#dsMatAddStdLib`) imports into
  that dialog's draft. The list's OK commits the draft; Cancel discards it.

**Browser:**

* Pick Standard (`#mlStd`), then kind (`#mlKind`), then search
  (`#mlSearch`). The grade table (`#mlTable`, checkboxes `.ml-pick`) shows
  f'c or Fy, Fu, E and weight in display units (units.js).
* The info line (`#mlInfo`) shows the source clause and formula.
* The side panel (`#mlProps`) shows E, ν, G, weight, mass, α, the strengths
  and the E table / formula values.
* The stress–strain preview (`#mlPreview`) uses the existing `POST
  /api/materials/curve` and the sscurve plot.
* Name clashes: **Add with a “-2” suffix** (`#mlClashSuffix`, the default)
  or **Replace existing** (`#mlClashReplace`).

**Data source.** Live mode uses the GET endpoints. `?mock=1`, or an
unavailable endpoint, uses the bundled `matlib_data.js` instead. A test
asserts that this copy is identical to the JSON files, and its `material`
dicts equal the backend's.

**Test hook.** `__sky.matLib = {open, standards, materials, importInto}`.

## Eigen refinement and tower control height

No JSON key is added, removed or renamed. Two value rules change.

### 1. Verified eigenpairs, canonical degenerate basis

`run_modal`'s legacy eigen path (OpenSees `eigen`; every small model,
`eigen_solver: "legacy"`) now goes through `engine/eigen_refine.py`:

* **Verification.** One inverse iteration `y_i = K^-1 M phi_i` per mode
  (a linear static solve on the standard elastic domain) gives the
  residual `max|lam_i y_i - phi_i| / max|phi_i|` over all DOFs and the
  mass-orthogonality error of the set. When both are `<= 1e-9`, the
  OpenSees eigenpairs are returned unchanged (same objects).
* **Rayleigh-Ritz cleanup** otherwise, on `Z = [lam_i y_i]`:
  `Z^T K Z = Z^T M Phi Lam` and `Z^T M Z` are exact small matrices
  (lumped mass). This repeats at most 3 times. The cause is
  `-fullGenLapack`, which returns non-M-orthogonal vectors inside a
  degenerate pair. On the 2x2-bay quick building the error was 1e-3, and
  `Gamma = L/m` then mis-split the X excitation: an RS X case leaked
  4.4e-4 into Y. Arpack can also return vectors polluted in the massless
  DOFs.
* **Canonical basis of repeated eigenvalues.** Consecutive eigenvalues
  within `1e-9` relative form a cluster. Every solver path, including the
  condensed UmfPack path, rotates the cluster to the same M-orthonormal
  basis. The first mode carries all of the cluster's X participation,
  the next the rest of its Y, then RZ, then the complement. A doubly
  symmetric building gets pure-X and pure-Y modes, so SRSS and ABS no
  longer depend on the solver's arbitrary basis. CQC is invariant anyway,
  because `rho = 1` inside the pair.
* **Cut clusters.** When mode `n+1` repeats mode `n`, the whole cluster is
  solved and made canonical, and its first members are kept. The mode
  count is unchanged.

Measured with the assembled `K`/`M` (`GimmeMCK`) on the quick building,
the 2x2 square building, the twin towers and the twin square towers, for
`n` = 8 and 12 and for band, umfpack and legacy:

* the residual `||K phi - lam M phi|| / ||K phi||` is at most 3e-13;
* the orthogonality error is at most 4e-14;
* RS X leakage into Y is at most 1e-14 with both CQC and SRSS. Before the
  change it was up to 4.5e-4 with CQC and up to 30% on the SRSS base
  shear of the 4-fold cluster.

**Back-compat.**

* Models without repeated periods are byte-identical (results hashes
  checked).
* With repeated periods:
  * periods are unchanged to 1.1e-14;
  * per-mode shapes and participation of a cluster now follow the
    canonical basis;
  * CQC results change only where the old basis was non-orthogonal, which
    removes the leakage. Otherwise they change by at most 5e-15;
  * SRSS results follow the canonical basis.
* Participation SUMS are basis-invariant. They change only where the
  old basis was non-orthogonal. For example, the 2x2 quick building with
  all 12 modes went from ux 1.00000022 and uy 0.99999978 to exactly 1.

### 2. Pushover control height with towers

`control.height` (`H_ctrl`, used by `target_drift x H_ctrl` and by
`roof_drift`) is now the control tower's height:

* with `control_tower`, the top story's elevation minus the tower's
  `base_elevation`;
* with towers and no control tower, the default roof story
  (`stories[-1]`) minus ITS tower's base elevation.

Without towers, or with towers based at 0, nothing changes. On the
podium test model, `control_tower = "T1"` gives `H = 15 - 6 = 9 m`
(it was 15). The default B2 gives `12 - 6 = 6 m` (it was 12).

The Pushover Options dialog's target-displacement placeholder now shows
`target_drift x H_ctrl` with the backend's rule
(`towers_fu.js controlHeight`): tower, roof, story elevation or joint z.
The placeholder updates when the control tower, story or joint changes.

## Built-up sections (v1.21)

Frame sections assembled from components (plates, steel-database shapes,
parametric shapes), stored as `builtup_sections` and MIRRORED by a
same-named `FrameSection` (`shape: "builtup"`), exactly like
`designer_sections`. Core: `skyframe/core/builtup.py` (+ the shape
lookup adapter `skyframe/core/builtup_db.py`: `steel_db` when installed,
else a stub of the `sections_library` W-shapes with the AISC tw/tf of
`core/modifiers.py`, plus designation-only angles). API:
`skyframe/api/builtup_api.py` (registered by `create_app` with one line).
UI: `js/builtup.js`, `js/builtup_geom.js`, `js/mock_builtup.js`,
`builtup.css`.

### Model JSON

`builtup_sections` is emitted only when non-empty (absent = none; every
existing file and result is byte-identical — the quick-building model
hashes the same before and after: model `95a41577bb25e727`, results
`6cbc49fa195fc920`).

```json
"builtup_sections": {
  "PG1": {
    "name": "PG1", "material": "STEEL",
    "components": [
      {"kind": "plate", "b": 0.4, "t": 0.025, "x": 0.0, "y": 0.5875, "angle": 0.0, "label": "Top flange"},
      {"kind": "db", "standard": "aisc", "name": "W14X90", "type": "I",
       "dims": {"d": 0.3556, "bf": 0.3683, "tf": 0.018034, "tw": 0.011176},
       "x": 0.0, "y": 0.0, "rotation": 0.0, "mirror": false,
       "tab": {"A": 0.0170967, "I33": 4.158e-4, "I22": 1.507e-4, "J": 1.690e-6},
       "props_from": "geometry", "material": "A36"},
      {"kind": "param", "type": "angle", "dims": {"d": 0.1524, "b": 0.1016, "t": 0.0127},
       "x": 0.05, "y": 0.0, "rotation": 90.0, "mirror": true}
    ],
    "axes": "geometric", "torsion": "auto",
    "template": {"id": "plate_girder", "params": {}}, "notes": "…"
  }
},
"sections": {"PG1": {"name": "PG1", "material": "STEEL", "A": …, "I33": …,
  "I22": …, "J": …, "b": …, "h": …, "mod_A": 1.0, …, "shape": "builtup"}}
```

* All values are SI (m). Angles (`angle`, `rotation`) are in DEGREES,
  counter-clockwise.
* Coordinates: `x` is the width (horizontal), `y` is the depth (up). Local
  2 runs along the depth and local 3 along the width (the js/secpreview.js
  convention). `I33 = ∫y² dA` and `I22 = ∫x² dA`, both about the
  centroid.
* **Plate.** A `b × t` rectangle centred at `(x, y)`; `b` lies along `x`
  when `angle = 0`.
* **Shapes** (`db` / `param`). `(x, y)` is the centre of the unrotated
  bounding box. `mirror` flips the shape about its local vertical axis,
  then `rotation` is applied about `(x, y)`.
* **Parametric types and their `dims`:**
  * `I` (d, bf, tf, tw)
  * `I_mono` (d, bf_top, tf_top, bf_bot, tf_bot, tw)
  * `channel` (d, bf, tf, tw; web on the left, toes +x)
  * `angle` (d vertical leg, b horizontal leg, t; heel bottom-left)
  * `double_angle` (d, b, t, s, orientation `LLBB`|`SLBB`)
  * `tee` (d, bf, tf, tw)
  * `rect_tube` (d, b, t)
  * `round_tube` (D, t)
  * `rect_solid` (d, b)
  * `round_solid` (D)
  * `plate` (b, t)

  Outlines have no fillets (sharp corners). The aliases `bft/tft/bfb/tfb/OD/h`
  are accepted on input.
* **Database components.** When a database component is saved, its
  `type`, `dims` and tabulated `tab` (A, I33, I22, J, I_major, I_minor;
  any key the record marks as `computed` is excluded) are SNAPSHOT into it,
  so a saved section never changes when the database changes.
  `props_from: "geometry"` uses the fillet-free outline instead of `tab`.
* **`material`.** Optional on a component; it defaults to the section
  material. Components are combined with the modular ratio
  `n = E_i / E_section` (transformed section).
* **`axes`.** `"geometric"` (default) gives `I33 = Ixx` and `I22 = Iyy`;
  `Ixy` is not carried by the frame element. `"principal"` gives
  `I33 = I_major` and `I22 = I_minor`; rotate the member's local axes by
  `theta` yourself.
* **`torsion`.**
  * `"auto"` (default).
  * `"open"`: the sum Σ b·t³/3.
  * `"fem"`: the optional `sectionproperties` package (FEM warping on the
    union outline, single material). When the package is not installed it
    falls back to `"auto"` with a warning. It is never a hard dependency.
* The mirrored FrameSection gets the transformed A, I33, I22, J and the
  bounding box `b`/`h`.
  * Its `As2`/`As3` are NOT set, so the member stays Euler-Bernoulli and
    the engine is unchanged.
  * Re-saving keeps the section's modifiers and shear settings.
  * The name must not clash with a Section Designer section.

### Properties (`section_properties`, identical in js/builtup_geom.js)

**Returned keys:** `{A, cx, cy, Ixx, Iyy, Ixy, I_major, I_minor, theta,
theta_deg, axes, I33, I22, J, J_method, S33, S33_top, S33_bot, S22,
S22_left, S22_right, Z33, Z22, r33, r22, As2, As3, shear_centre:
{x, y, method}|null, x0, y0, b, h, bbox, E_ref, n[], cells[],
warnings[]}`.

**Area properties.** Exact shoelace and circle integrals per component,
with the parallel-axis theorem about the composite centroid. The
principal angle satisfies `tan 2θ = −2Ixy/(Ixx−Iyy)`; θ is the MAJOR
axis, in (−90°, 90°].

**S and Z.**
* S is I divided by the extreme-fibre distance on each face.
* Z uses exact polygon clipping at the plastic neutral axis (equal
  transformed areas). Circles are 360-gons for Z only.

**J_method:**
* `open`: Σ b·t³/3 over the rectangular walls. A database shape uses its
  tabulated J.
* `closed`: tubes. A rect tube uses Bredt `4Am²t/p_m`; a round tube uses
  `π(D⁴−Di⁴)/32`. Solids use Roark and `πD⁴/32`.
* `closed_cell`: ONE rectangular cell formed by axis-aligned components is
  found by rasterising them on their own coordinate grid. Then:
  * `J = 4Am² / Σ(s/t)` on the wall mid-line. Each wall thickness is
    measured outward from the cell.
  * The J of components that do not bound the cell is added.
  * Flange outstands are neglected (conservative).
* `approx`: several cells, or a non-rectangular cell. J = Σ n·J_i
  (conservative), with a warning.
* `fem`: see above.

Stacked plates count as separate walls, so the open sum underestimates
J; `fem` is exact.

**Shear areas** (approximate):
* A wall of aspect ≥ 2 adds `A·sin²φ` to As2 and `A·cos²φ` to As3.
* Compact rectangles and solid rectangles: 5/6 A.
* Solid rounds: 0.9 A.
* Round tubes: A/2.
* Rect tubes: their wall areas.

**Shear centre** (the `method` key):
* `symmetry`: the centroid, when the section is doubly symmetric or
  point-symmetric.
* `monosymmetric`: `y = Σ(I_i y_i)/Σ I_i`, where `I_i` is the wall inertia
  about the vertical symmetry axis. Applies to axis-aligned plate sections
  symmetric about the vertical axis.
* `fem`: from the FEM analysis.
* `null` otherwise.

**Warnings:** components overlap (the area is counted twice); approximate
J; fem unavailable.

**Templates** (`template_section(id, params, name, material)`; the
parameters and defaults are in `TEMPLATES`):
* `plate_girder`: unequal flanges.
* `box_girder`: `s` = web centre spacing.
* `w_cover`: `where` top|bottom|both.
* `double_angle`: gap `s`, LLBB|SLBB.
* `double_channel`: back_to_back|toe_to_toe, `s` = clear gap.
* `starred_angles`.
* `w_channel_cap`: crane beam, channel toes down over the top flange.
* `laced_column`: two channels, toe_out|toe_in, laced|battened. The
  properties are those of the chords only. `notes` states the assumption:
  composite action, NO shear flexibility of the lacing, use E6 modified
  slenderness outside this analysis.
* `tube_stiffener`: a rect or round tube with a fin plate at the top or
  bottom.

### HTTP

* `POST /api/sections/builtup`
  * `{action: "upsert", section}` returns `{model, properties, section}`.
    It stores the section and its mirror.
  * `{action: "delete", name}` returns `{model}`. It responds 400 when the
    name is unknown or a member still uses the section.
  * `{action: "properties", section, materials?|model?}` returns
    `{properties, section}`. The server model is NEVER mutated.
  * Errors respond 400 `{error}`.
* `GET /api/sections/builtup/templates` returns `{templates: [{id, label,
  params}], fem: bool, db_source: "steel_db"|"stub"}`.
* `POST /api/sections/builtup/template {id, params?, name?, material?}`
  returns `{section, properties|null}`.
* The picker endpoints use `steel_db` when present, else the stub:
  * `GET /api/sections/builtup/db/standards` returns `{standards, source}`.
  * `GET /api/sections/builtup/db/families?standard=` returns
    `{families}`.
  * `GET /api/sections/builtup/db/shapes?standard=&family=&q=&limit=`
    returns `{shapes, total}`.
* `POST /api/model` round-trips `builtup_sections` byte-identically.

### Validation (`tests/test_builtup.py`, all derived by hand)

* **Plate girder** (1.2 m, unequal flanges): A, ȳ, I33, I22, S top and
  bottom, Z by the PNA in the web, Σbt³/3, the monosymmetric shear centre,
  and As2/As3. All within 1e-12.
* **Box.** Bredt `4A²t/p` for uniform t; the general mid-line form with
  unequal walls and outstands; the rect-tube component; two channels
  toe-to-toe forming a cell.
* **W14X90 + cover plate(s).** The tabulated W plus the plates, shifted to
  the new centroid.
* **2L 6×4×½** (LLBB and SLBB, gap ⅜"). The single-angle rectangles by
  hand; the AISC 2L tabulated check comes once the merged DB has 2L.
* **Single angle.** Ixy matches the hand value. tan 2θ = −2Ixy/(Ix−Iy).
  Mohr invariants hold. A mirror flips Ixy and θ. Rotating the section
  rotates the tensor. `principal` feeds I_major and I_minor.
* **Starred angles.** Point-symmetric: the shear centre is the centroid
  and Ixy is not zero.
* **Mixed materials.** The modular ratio.
* **sectionproperties** (when installed). A, Ixx, Iyy, Ixy within 1e-9;
  Z within 1e-6. FEM J is within 3% of Bredt for the box and within 5% of
  Σbt³/3 for the plate girder.
* **Engine.** A cantilever made from a built-up plate girder deflects
  PL³/(3E·I33) under a vertical tip load and PL³/(3E·I22) under a lateral
  one, within 1e-6.
* **JS vs Python.** All 9 templates (geometric and principal, one with a
  mixed material) plus a rotated, mirrored mixed section: 450 values agree
  within 6e-16 relative, and the JS templates equal the Python ones.

### UI (js/builtup.js)

**Opening the editor:**
* Define › Section Properties › **Built-up Section…** (act `def-builtup`,
  added through `sky.menu.add`).
* **Add Built-up Section…** (`#dsSecBuiltup`) in Frame Properties.
* Modify on a built-up section reopens the editor. Add Copy opens the
  editor on a copy.
* `__sky.openBuiltup(name?, {copy?})`.

**Canvas:**
* A grid with snapping of the reference point.
* Edge, corner and mid-line snapping to the other components (8 px).
* Wheel zoom, drag-pan and Fit.
* Drag to move.
* Commands: ±90° (R / Shift+R), a typed angle, Mirror (M), Align,
  Duplicate, Delete (Del), and arrow keys to nudge by one grid step.

**Palette:**
* Plate.
* Database shape…: standard → family → search, with the values in
  js/units.js.
* Parametric shape of any type.
* Template gallery with thumbnails and parameter forms.

**Inspector.** All dimensions are in display units (js/units.js `dim`):
* Per component: x, y, angle/rotation, mirror, dims, material, and
  "properties from".
* For the section: name, material, axes, torsion.

**Live properties:** A, I33, I22, J (+ method), centroid, θ, I
major/minor, Ixy, S, Z, r, As2/As3, the shear centre, and warnings.
* They are computed by js/builtup_geom.js.
* Live mode confirms them through `action: "properties"`; the panel then
  shows "backend ✓".

**Canvas overlays:**
* The centroid.
* The principal axes.
* Local 2 (up, green) and 3 (right, blue) arrows. They are rotated by θ
  when `axes` is principal.
* The shear centre.

**Saving:**
* OK writes `builtup_sections[name]` and the mirrored `sections[name]` into
  the working model and marks it dirty, so the section can be assigned
  straight away. In live mode the backend properties are used, including
  FEM.
* A rename retargets the members.
* An unchanged OK and Cancel/Esc write nothing (byte-identical).
* Deleting the section in Frame Properties also removes its
  `builtup_sections` entry.

**Preview.** js/secpreview.js draws the real component outlines for a
built-up section, labelled "Built-up".

**Mock.** `?mock=1` uses js/mock_builtup.js: the same formulas and the
same stub DB.

**Test hook:** `__sky.builtup = {open, close, state, props, serverProps,
commit, select, fit, backend, computeProps}`.

## Steel section database and parametric shapes

Version 1.21. Analysis only, with no design checks. Sources are listed in
`docs/SECTION_SOURCES.md`.

### Database files

`skyframe/data/steel/<id>.json` ships as package data
(`[tool.setuptools.package-data] "skyframe" = ["data/steel/*.json"]`;
PyInstaller `collect_all("skyframe")` picks it up). The loader discovers
**every** `*.json` file in that folder at runtime, so the eurocode / is808
/ bs / as_nzs files are found without a code change. Listing order:
aisc, eurocode, is808, bs, as_nzs, then any other id alphabetically.

```
{"standard": "AISC", "edition": "AISC Shapes Database v16.0",
 "source": "<citation>", "units": "SI (m, m^2, m^4, m^6, kg/m)",
 "shapes": [{"name", "family", "type", "dims": {...}, "props": {...},
             "edi"?: <alternate designation>, "computed": [keys],
             "aisc"?: {<published US values, verbatim>}}]}
```

* `type` is one of `I`, `I_mono`, `channel`, `angle`, `double_angle`,
  `tee`, `rect_tube`, `round_tube`, `rect_solid`, `round_solid`, `plate`,
  plus the concrete-only types `tee_concrete` and `L_concrete`.
* `props` (SI): `A I33 I22 J Cw? S33 S22 Z33 Z22 As2 As3 r33 r22 x0 y0
  mass`.
  * Angles add `I_major I_minor alpha`.
  * AISC extras: `C` (HSS torsional modulus), `eo`, `rts`, `ho`, and
    `cx`/`cy` (the centroid in the standard placement).
* `x0`/`y0` give the shear centre relative to the centroid, along local
  3 / local 2.
* `computed` names every derived key. A key prefixed `dims.` is a derived
  dimension.

**`aisc.json`** holds the complete AISC Shapes Database v16.0: **2,299
shapes** in the families W 289, M 16, S 28, HP 22, C 32, MC 40, L 137,
2L 639, WT 289, MT 14, ST 28, HSS Rect 399, HSS Square 126, HSS Round
189, Pipe 51.

**Column mapping.**
* Ix→I33, Iy→I22, J, Cw, Sx/Sy→S33/S22, Zx/Zy→Z33/Z22, rx/ry→r33/r22.
* Iw/Iz→I_major/I_minor, and alpha = atan(tan α).
* Mass: W × 0.45359237/0.3048 kg/m.

**Dimensions.**
* I-shapes, C/MC and tees: `d bf tf tw`, plus `r = kdes − tf`.
* Angles: `d` is the vertical (AISC long) leg `b`; `b` is the AISC `d`.
* 2L: `d b t s orientation`, where `s` is the gap decoded from the name
  and `orientation` is LLBB, SLBB or EQUAL.
* HSS rect: `d=Ht, b=B, t=tdes, t_nom, r_out = 2·tdes`. This is the AISC
  basis for its tabulated values.
* HSS round and Pipe: `D=OD, t=tdes, t_nom`.

**Derived values.**
* As2/As3 per the rules below.
* 2L `J` = 2 × J of the single angle, because v16.0 tabulates no 2L J.
* Channel `x0 = −(x + eo)`.
* Angle `x0 = t/2 − x`, `y0 = t/2 − y`.
* Tee and 2L `y0 = y − tf/2`, with `cy = d − y`.

The legacy library `skyframe.core.sections_library` keeps its 22-name
API, but now reads the database's `aisc` block. Its results are
byte-identical, with one pinned legacy value: W10x49 keeps `d = 9.98 in`,
where v15/v16 publish 10.0.

### Loader `skyframe/core/steel_db.py`

```
standards() -> [{id, standard, edition, count, source}]
families(standard) -> [family, ...]                (file order)
shapes(standard, family=None, query=None) -> [record]
shape(standard, name) -> record                     (KeyError if unknown)
section_from_db(standard, name, material, section_name=None) -> FrameSection
import_sections(model, standard, names, material) -> [added names]
clear_cache()
```

* `standard` may be given as the id or as the standard name, in any case.
* `query` terms are ANDed and matched as substrings of the name or EDI
  alias, ignoring case and spaces.
* `shape()` also accepts `w14x90` and EDI names.
* `section_from_db` returns tabulated A/I33/I22/J and As2/As3. Setting As2
  and As3 turns on Timoshenko shear deformation for that section.
  * `b`/`h` are the outline width/depth.
  * It also sets `shape = type`, `dims` and `db_ref =
    {"standard": <id>, "name"}`.
  * Angles also carry `principal`.

### HTTP

* `GET /api/steel_db/standards` →
  `[{id, standard, edition, count, source, families}]`.
* `GET /api/steel_db/shapes?standard=&family=&q=&limit=&raw=` →
  `{standard, family, q, total, count, shapes}`.
  * Records omit the verbatim `aisc` block unless `raw=1` is passed.
  * Errors: 404 for an unknown standard, 400 for a bad limit.
* `POST /api/steel_db/import {standard, names[], material, apply?=true}`
  → `{added, skipped, sections: {name: section dict}, model?}`.
  * `apply: false` only builds the dicts. The UI merges them into its
    draft.
  * `apply: true` adds them to the current model. Names that already
    exist are listed in `skipped`, and the response includes `model`.
  * Errors: 400 for a missing or unknown material or empty names, 404 for
    an unknown shape.
* `POST /api/steel_db/shape_props {type, dims}` →
  `{type, props, shear_area_formula}`. Invalid dims return 400.

### FrameSection fields (appended; each is serialised only when set)

| field | meaning |
|---|---|
| `dims` | parametric dimensions (SI; per type below; string values allowed, e.g. `orientation`) |
| `db_ref` | `{"standard", "name"}` of a database shape |
| `principal` | `{"I_major", "I_minor", "alpha"}` of an unsymmetric section (rad) |
| `use_principal` | `true` = principal-axis analysis (emitted only when true) |

**Building a section from dims.** A section object with `shape` +
`dims` and no `A` (`POST /api/model`) gets A, I33, I22, J, b and h
computed from the dimensions. Shear areas stay explicit.

**Validation** (`_validate_section_ext`, run by add_section and
validate):
* dims must be finite and ≥ 0;
* the type's geometry rules apply, e.g. "the flanges are thicker than the
  depth";
* `db_ref` must hold two non-empty strings;
* `principal` must satisfy I_major ≥ I_minor > 0;
* `use_principal` requires `principal`.

**Byte-identity.** A model without these fields, or with a
legacy-library W-shape, has the same model and results SHA-256 as before
v1.21.

### Parametric shapes `skyframe/core/shape_props.py`

`shape_properties(type, dims)` → `{A, I33, I22, I23, J, Cw, S33, S22,
Z33, Z22, As2, As3, r33, r22, x0, y0, cx, cy, b, h}`. Angle and L_concrete
add `I_major, I_minor, alpha`. The JS mirror is `js/secshapes.js`; its
results agree with the backend at relative difference 0.

**Dims per type.**
* `I`: d bf tf tw [r]
* `I_mono`: d bf_top tf_top bf_bot tf_bot tw
* `channel`: d bf tf tw
* `angle`: d b t
* `double_angle`: d b t [s]
* `tee`: d bf tf tw
* `rect_tube`: d b t [r_out]
* `round_tube`: D t
* `rect_solid` / `plate`: d b
* `round_solid`: D
* `tee_concrete` / `L_concrete`: bf hf bw h

**Standard placements.** Coordinates are x = local 3, y = local 2.
* Channel: web on the left, flanges toward +3.
* Angle: heel at the origin, leg d along +2, leg b along +3.
* Tee, 2L, T-beam: flange at the top.
* L-beam: web flush with the left edge of the flange.

**Formulas.**
* **A, I, I23, centroid and Z** are exact integrals of the sharp-cornered
  outline. Rounded tube corners and circles use closed forms. Fillets are
  not integrated.
* **J:**
  * open sections use the AISC DG9 plate sums: I `(2bf·tf³ + (d−tf)tw³)/3`,
    channel `(2(bf−tw/2)tf³ + (d−tf)tw³)/3`, tee `(bf·tf³ + (d−tf/2)tw³)/3`,
    angle `(d−t/2 + b−t/2)t³/3`, 2L twice the angle;
  * rect tube: Bredt `4Am²t/pm` on the mid-line;
  * round: π(D⁴−d⁴)/32;
  * rect solid: Roark (same as `FrameSection.rectangular`);
  * concrete T/L: the larger of the two rectangle splits (ACI practice).
* **Shear areas:**
  * I, channel: `d·tw`, `5/3·bf·tf`;
  * I_mono: `d·tw`, `5/6·Σbf·tf`;
  * tee: `d·tw`, `5/6·bf·tf`;
  * angle: `5/6·d·t`, `5/6·b·t`;
  * 2L: `5/3·d·t`, `5/3·b·t`;
  * rect tube: `2·d·t`, `2·b·t`;
  * round tube: `A/2`;
  * rect solid: `5/6·A`;
  * round solid: `0.9·A`;
  * concrete T/L: `5/6·bw·h`, `5/6·bf·hf`.
* **Channel shear centre:** `e = 3b²tf/(6b·tf + h·tw)` from the web
  mid-line, with b = bf − tw/2 and h = d − tf.

**Validation** (`tests/test_shape_props.py`):
* Closed forms are exact: rectangle; circle; pipe `I = π(D⁴−d⁴)/64`,
  `J = 2I`; sharp tube; I; thin-walled J; channel e; principal
  invariants.
* sectionproperties 3.10 (finite elements, test-only) agrees with A, I,
  I23, centroid and Z to 1e-6. Thin-walled J agrees within 3%, the tube
  within 1%, and concrete T/L J is 5–10% low (conservative).
* Against all 2,299 tabulated shapes, the maximum |error| is:
  * W: A 3%, I33 4.5%, I22 1.5%;
  * HP: 5 / 5.5 / 1.5%;
  * WT: 3 / 2 / 2%;
  * L: 1.5 / 2.5 / 2.5%;
  * 2L: 1.5 / 3 / 3.5%;
  * HSS rect: 0.5 / 1 / 1%; HSS round: 1 / 2 / 2%; Pipe: 3.5%;
  * M: 9.5%;
  * W14X90 alone: within 1.8%.
* Sloped-flange shapes (S, C, MC, ST) are compared on A and I33 only:
  I22 comes out 5–35% high because the outline uses the AISC average
  flange thickness.
* J matches within 20% for W, WT and L (fillet bulbs), and within 0.6%
  for HSS.

### Unsymmetric sections (angles, concrete L): I23 and principal axes

* **Default (ETABS / SAP2000 geometric axes).** The member bends about its
  leg-parallel local axes with the stored I33/I22, and the product of
  inertia I23 is **ignored**.
* **`use_principal: true`.** The engine analyses the section about its
  principal axes:
  * `section_ext.principal_model` returns a deep copy of the model, or
    the model itself when no section uses principal axes, so the default
    path stays bit-identical;
  * in that copy I33 = I_major and I22 = I_minor;
  * every member using the section is stamped
    `FrameMember.section_axis_offset = −degrees(alpha)`. This field is
    never serialised. `frame_axes.member_local_axes` adds it to `angle`,
    so local 3 lies on the major principal axis.
  * Member forces are then reported about the principal axes. Curved
    members are not rotated.
* **Tests.** In leg-axes mode a cantilever deflects `PL³/(3E·I33)` with
  zero cross deflection. In principal mode the tip deflection equals the
  full unsymmetric-bending solution `u = L³/(3E)·K⁻¹F` with
  `K = [[I33, I23], [I23, I22]]`, to 1e-8 in both load directions.

### UI

**Define › Frame Sections › Frame Properties.**
* A new action, **Import from Database…** (`#dsSecDb`), opens
  `#dsSteelDb` (`js/steeldb.js`).
* Inside it:
  * the standard select is `#sdbStd`;
  * family tabs are `#sdbFam-<family>`;
  * the search box is `#sdbSearch`;
  * the table `#sdbTable` shows d, b, t, A, I33, I22, J, S33, Z33 and
    weight in display units;
  * rows are multi-select (tick boxes, plus `#sdbAll` to select all
    shown);
  * clicking a row, or ↑/↓, highlights it, and the preview panel shows
    it;
  * the material select is `#sdbMat`.
* OK builds the sections on the server (`apply:false`) and merges them
  into the draft. Shapes that are already defined are disabled.

**Frame Section Property Data.**
* The Shape select (`#dsSecShape`) offers:
  * Steel: I, I unequal flanges, channel, angle, double angle, tee, rect
    tube, round tube/pipe, solid rectangle, solid circle;
  * Concrete: rectangular b×h (legacy), circle, T-beam, L-beam;
  * General (A/I/J entry).
* Dimension fields are `#ssDim_<key>`, shown in display units. Typing
  recomputes the readouts and the live preview on every keystroke. Each
  committed edit writes the exact properties plus `shape`, `dims` and
  `principal`.
* Readouts: A, I33, I22, J, As2, As3, S, Z, Cw and the shear centre.
* Angles and L-beams add a Principal Axes group with the
  `#ssPrincipal` checkbox, which sets `use_principal`.
* Database sections are read-only. They show their source (`#ssDbNote`),
  and **Edit as Parametric** (`#ssDbEdit`) detaches them.
* OK with defaults is byte-identical.

**Preview (`js/secpreview.js`).** It draws every type from `shape` +
`dims`, through the hook `ssPreview`.

**Mock (`?mock=1`).** `js/mock_steeldb.js` serves the same JSON from an
embedded, verbatim 42-shape subset covering all 15 families
(`js/mock_steeldb_data.js`, generated by `tools/gen_mock_steeldb.py`).

**Playwright.** Mock 38/38, live 42/42:
* import W14X90, HSS20X12X5/8 and L4X3X1/4;
* create a parametric channel and a concrete T-beam;
* check that every type draws;
* round-trip through POST /api/model; the second echo is identical;
* analysis runs;
* kip-in: 0.25 m reads 9.84252 in, 12 in stores 0.3048 m, and the table
  headers read in²/in⁴;
* zero console errors.

## Model exchange: E2K / IFC / DXF / Excel

Export and import of whole models. All code is new and lives in
`skyframe/io/` (`e2k_write.py`, `e2k_read.py`, `ifc_write.py`,
`ifc_read.py`, `dxf_layers.py`, `xlsx.py`, `exchange_common.py`) plus
`skyframe/api/exchange_api.py` (registered by `create_app` with one line).
No model or engine code changed, so a model that is never exported is
byte-identical (quick building: model `95a41577bb25e727`, results
`4b9bdc714c4a23e0`). The legacy readers `import_e2k`, `import_ifc` and
`import_dxf` (and `POST /api/import/<fmt>`) are unchanged.

### E2K (ETABS v20+ text)

`export_e2k(model, units=("KN","M"), include=None, extensions=True)` →
`(text, report)`; `read_e2k(text, units=("KN","M"), apply_extensions=True)`
→ `(model, warnings)`.

* **Units.** `UNITS "<F>" "<L>" "C"` with F in KN, N, KIP, LB, KGF, TONF and
  L in M, MM, CM, FT, IN. Values are converted from SI on export and to SI
  on import (stress F/L², unit weight F/L³, mass F·s²/L, …). The `units`
  argument of `read_e2k` applies only when the file has no UNITS line.
* **Axes.** Records are written in ETABS axes: a `"legacy"` model is
  converted first (vertical members get `angle − 90`, see "Frame local
  axes"). Imported models are `"etabs"`.
* **Records written.** One `$` section each. The reader dispatches on the
  record keyword, as ETABS does, so section headers are cosmetic.
  * `PROGRAM "ETABS" VERSION "20.0.0"`, `TITLE2`.
  * `TOWER` (multi-tower models); `STORY "S" HEIGHT h [TOWER "T"]`
    top-down and one `STORY "Base" ELEV z` per tower (`Base_<tower>` for
    the others).
  * `GRIDSYSTEM` (`CARTESIAN` / `CYLINDRICAL`, `UX UY ROTATION`) + `GRID`
    lines (`DIR X|Y|R|T`).
  * `DIAPHRAGM "D" TYPE RIGID|SEMIRIGID`.
  * `MATERIAL` (TYPE, WEIGHTPERVOLUME, MASSPERVOLUME, SYMTYPE, E, U, A, FY,
    FU, FYE = Ry·fy, FUE, FC, LTWTCONC, LTWTFACT, DAMPRATIO).
  * `FRAMESECTION`:
    * rectangles built by `FrameSection.rectangular` → `SHAPE "Concrete
      Rectangular" D h B b`;
    * steel-database sections → the parametric ETABS shape plus
      `FROMFILE "<file>" DBSECTNAME "<shape>"` (aisc `AISC16.xml`,
      eurocode `Euro.xml`, bs `BSShapes2006.xml`, as_nzs
      `Australia-NZ.xml`, is808 `Indian.xml`); the reader rebuilds them
      with `steel_db.section_from_db`;
    * parametric shapes → `Steel I/Wide Flange` (I; I_mono adds `BB TFB`),
      `Steel Channel`, `Steel Angle`, `Steel Double Angle` (`DIS`),
      `Steel Tee`, `Steel Tube` (`CORNERRADIUS`), `Steel Pipe`,
      `Steel Rectangular` / `Steel Rod` (`Concrete Rectangular` /
      `Concrete Circle` for concrete), `Steel Plate`, `Concrete Tee`,
      `Concrete L`, keys `D B TF TW`; the reader rebuilds them with
      `section_from_dims`;
    * everything else (general, designer, built-up, nonprismatic) →
      `SHAPE "General" D B AREA AS2 AS3 I33 I22 I23 J` (AS2/AS3 = 0 means
      unset);
    * a second line carries `AMOD A2MOD A3MOD JMOD I2MOD I3MOD MMOD WMOD`
      when any modifier differs from 1.
  * `SDSECTION` for built-up sections. Components are `STEEL PLATE`
    (D = t, B = b) or a parametric / database shape, with `XC YC ROTATION
    MIRROR2`. The reader recreates `builtup_sections` and re-snapshots
    database components.
  * `SHELLPROP` (`PROPTYPE Wall|Slab`, `MODELINGTYPE ShellThin |
    ShellThick | Membrane | PlateThin | PlateThick`, thickness, `F11MOD …
    V23MOD MMOD WMOD`). Membrane-behaviour slabs use `Membrane`; a section
    used by both behaviours gets a second property `<name>~M`.
  * `LINKPROP "LP_<uid>" TYPE` + `DOF U1..R3 KE`. Elastic stiffness maps
    from global to the default ETABS link axes: vertical / zero length
    1 = Z, 2 = X, 3 = Y; along X 1 = X, 2 = Z, 3 = Y; along Y 1 = Y,
    2 = Z, 3 = X.
  * `POINTSPRING` (UX..RZ), `AREASPRING` (U3, `NONLINEAROPT3 "Compression
    Only"`).
  * `POINT "n" x y [dz]`, where dz is the distance below the story level
    of the referencing record.
  * `LINE "uid" COLUMN|BEAM|BRACE "pi" "pj" N`: N = levels from i to j, so
    i is N levels below the assigned story; members drawn top-down get a
    negative N.
  * `AREA "uid" FLOOR|PANEL n "p1".."pn" N1..Nn` (levels below the
    assigned story). Openings are separate areas `"<uid>~O<k>"` with
    `AREAASSIGN … OPENING "Yes"`.
  * `LINK "uid" POINT1 "p" "level" POINT2 "p" "level"`.
  * `POINTASSIGN` (`RESTRAINT`, `SPRINGPROP`, `DIAPH`, `MASSUX..MASSRZ`),
    `LINEASSIGN` (`SECTION ANGLE RELEASE "M2I M3I M2J M3J" LENOFFI LENOFFJ
    RIGIDZONE CARDINALPT TRANSFORMSTIFFNESSFOROFFSETS`), `AREAASSIGN`
    (`SECTION DIAPH SPRINGPROP PIER OBJMESHTYPE "MAXSIZE" MESHMAXSIZE`),
    `LINKASSIGN PROPERTY`.
  * Loads:
    * `LOADPATTERN TYPE SELFWEIGHT`;
    * story forces: `SEISMIC|WIND "P" "User Loads" [ECCRATIO e]` +
      `… STORY "S" DIAPHRAGM "D" FX FY MZ`;
    * `POINTLOAD TYPE "FORCE" FX..MZ`;
    * `LINELOAD TYPE "UNIFF" | "TRAPF" (FSTART FEND RDSTART RDEND) |
      "POINTF" (FVAL RDIST)`, `DIR GRAV | 2 | X | Y | Z`;
    * `AREALOAD TYPE "UNIFF" DIR GRAV|X|Y|Z|1|2|3`, and
      `SHELLUNIFORMLOADSET` + `AREALOAD TYPE "UNIFLOADSET" "set"`.
  * `ACTIVEDOF`, `PDELTA METHOD NONE|NONITERATIVE|ITERATIVE [MAXITER TOL]`,
    `PDELTALOAD`.
  * `MASSSOURCE "MsSrc1" INCLUDEELEMENTS INCLUDEADDEDMASS INCLUDELOADS
    INCLUDELATERALMASS INCLUDEVERTICALMASS LUMPATSTORIES ISDEFAULT` +
    `MASSSOURCELOAD`.
  * `FUNCTION FUNCTYPE "SPECTRUM"` (PERIOD / VALUE) and `FUNCTYPE
    "HISTORY" TIMESTEP dt` (TIME / VALUE). Inline RS spectra / TH records
    become `<case>_SPEC` / `<case>_TH`.
  * `LOADCASE`:
    * `"Modal - Eigen"` with `MAXMODES` = num_modes (omitted when 0);
    * `"Linear Static"`, or `"Nonlinear Static" NLGEOMTYPE "PDelta" |
      "PDelta plus Large Displ"`;
    * `"Response Spectrum" MODALCASE ACCEL U1|U2 FUNC SF MODALCOMBO
      CONSTDAMP`, where SF = scale·g in file units;
    * `"Linear | Nonlinear Direct Integration History" ACCEL FUNC SF
      NUMSTEPS STEPSIZE CONSTDAMP`;
    * `"Buckling" LOADPAT BUCKLINGMODES`.
  * `COMBO TYPE "Linear Add" | "Envelope" | "Absolute Add" | "SRSS" |
    "Range Add"`, members `LOADCASE` / `LOADCOMBO … SF`.
  * `GROUP "g" [COLOR]`, `GROUP "g" LINE|AREA "uid" "story"`, `LINK "uid"`,
    `POINT "p" "story"`.
* **Reader rules.**
  * Object uid = the label when the label is assigned at one story, else
    `<label>-<story>`.
  * Base fixity: identical full-fixed or pinned restraints at exactly the
    lowest object points → `base_fixity`; otherwise explicit `supports`.
  * A rigid diaphragm assigned only through points, covering every object
    point of each story it touches (name `D1`, or the only diaphragm), is
    the legacy per-story rigid diaphragm. Stories without one get
    `story_diaphragm = "none"`. Other diaphragms become named diaphragms.
  * Opening areas attach to the `<uid>` of their name, else to the
    containing coplanar area. A parallelogram opening becomes a u/v
    rectangle, anything else a polygon opening.
  * Unknown records and references are warnings, never exceptions.
* **SkyFrame extensions (exact round trip).**
  * The writer re-imports its native text, diffs it against
    `model.to_dict()` and writes every remaining difference as `$SF {"op":
    "set"|"del"|"order", "path": [...], "value"|"keys": …}` comment lines
    (values SI). ETABS treats `$` lines as comments.
  * `read_e2k` replays them (`exchange_common.apply_patches`).
  * In kN-m the round trip is byte-identical; in other units the
    comparison uses 1e-12 relative.
  * `report = {format, units, written: {kind: n}, skipped: [str],
    extensions: {top-level key: n}, exact, native_exact}`.
* **Not expressible in E2K.** These are carried by `$SF` lines, and
  dropped with `extensions=False`:
  * materials: stress-strain curves, colour, notes;
  * frame sections: the shear-deformation flag, the principal-axis flag,
    nonprismatic segments (written as the General props of the section),
    built-up labels / template / notes / axes / torsion, designer-section
    geometry;
  * shells: layered sections, the shell `mod` factor, the legacy shell
    type `shell` (written ShellThin, read `shell_thin`), region mesh
    divisions / floor mesh, one-way distribution, wind Cp, additional
    masses;
  * frames: joint offsets, automatic end offsets, axial limits, hinges,
    Winkler foundation, auto mesh / output stations, curved geometry,
    open-structure wind, tower tags, objects above the top story;
  * links and springs: non-elastic link parameters, skewed elastic links,
    line springs, nonlinear / rotated named point springs;
  * loads: thermal loads, ground displacements, frame moments, projected
    flags, joint patterns;
  * cases: pushover, staged, nonlinear static, steady-state, PSD and
    hyperstatic cases, buckling base cases, static P-Delta gravity, RS mode
    counts and GMC / DSC / rigid / missing-mass options, TH hinge /
    damping-model / DI / multi-component options, RS directional
    combinations, cases not run;
  * model options: explicit story masses, the `mass_from_patterns` alias,
    edge constraints, panel zones, analysis / damping option dictionaries,
    section cuts, display units, the legacy axis convention.

### IFC (IFC4 STEP text, no dependencies)

`export_ifc(model, include={"physical","analysis","supports"})` →
`(text, report)`; `read_ifc(text)` → `(model, warnings)`.

* **Spatial structure.** IfcProject (SI units) → IfcSite → IfcBuilding →
  one IfcBuildingStorey per level (base included, `Elevation`).
* **Materials.** `Pset_MaterialMechanical` (YoungModulus in Pa,
  PoissonRatio) and `Pset_MaterialCommon` (MassDensity in kg/m³).
* **Physical members.** IfcColumn / IfcBeam / IfcMember (Name = uid,
  ObjectType = section, Tag = story) with an IfcExtrudedAreaSolid. The
  solid's Z is local 1, X is −local 3 and Y is local 2. Profiles:
  IfcIShapeProfileDef, IfcAsymmetricIShapeProfileDef, IfcUShapeProfileDef,
  IfcLShapeProfileDef, IfcTShapeProfileDef, IfcRectangleHollowProfileDef,
  IfcCircleHollowProfileDef, IfcCircleProfileDef or IfcRectangleProfileDef.
  `ProfileName` is the database shape name for database sections. Each
  profile carries `Pset_ProfileMechanical` (CrossSectionArea,
  MomentOfInertiaY = I33, MomentOfInertiaZ = I22, TorsionalConstantX = J,
  ShearAreaZ/Y).
* **Walls and slabs.** IfcWall / IfcSlab extruded from the mid-surface,
  with openings as voids.
* **Analysis model.** IfcStructuralAnalysisModel groups:
  * IfcStructuralCurveMember (IfcEdge of the exact end points, `Axis` =
    local 2);
  * IfcStructuralSurfaceMember (outer and opening loops, Thickness);
  * IfcStructuralPointConnection (IfcBoundaryNodeCondition).
* **Import.** Geometry comes from the analysis members (exact), else from
  the physical solids (full 3D placement chains). Profiles map back as
  follows:
  * a ProfileName found in aisc, eurocode, bs, as_nzs or is808 (in that
    order) with the same type → that database section;
  * otherwise the IFC parameters → a parametric shape;
  * a rectangle → `FrameSection.rectangular`, or a general section when
    its Pset_ProfileMechanical differs.
* **Limits.** Double angles, concrete T/L, built-up, designer and general
  sections are written as their bounding rectangle with their properties.
  Loads, cases and combinations are not written.

### DXF (ASCII, AC1009 entities)

`export_dxf(model, include={"frames","shells","links","grids"},
unit_scale=1)` → `(text, report)`; `import_dxf_layers(text,
sections_from=None, unit_scale=1, layer_map=None)` → `(model, warnings)`.

* **Layers.**
  * frames: 3D LINE on `SF-COL|BEAM|BRACE-<section>`;
  * walls / slabs / membrane slabs: 3DFACE (3–4 corners, no openings) or a
    closed 3D POLYLINE on `SF-WALL|SLAB|DECK-<section>`;
  * openings on `SF-OPENING`, links on `SF-LINK`, grid lines (at the base
    level) on `SF-GRID`.
* **Names.** Layer names are sanitised (`<>/\":;?*|=,` and blanks → `_`).
  The header carries one `999` comment `SF-MAP <layer>=<section>` per
  layer, so the import maps layers back to exact section names.
* **Units.** `unit_scale` is drawing units per metre (1000 = mm).
* **Import.**
  * Stories come from the distinct elevations (lowest = base).
  * Sections, materials and link properties come from `sections_from`
    (the server uses the current model). Without it, placeholders are
    used (0.3 × 0.6 m frames, 0.2 m shells) with a warning.
  * `layer_map {layer: {kind, section}}` maps foreign layers.

### Excel (.xlsx, zipfile + minimal OOXML)

`export_xlsx(model, tables=None)` → `(bytes, report)`;
`import_xlsx(model, data)` → `(model|None, errors, summary)`.

* **Writer.** One sheet per `core/modeltables.py` table, with header
  `_id` + `<key> [<SI unit>]`. Numeric cells are exact doubles, booleans
  are boolean cells, everything else is an inline string. Two more sheets:
  `_index`, and `_meta` (`model_sha256`).
* **Reader.** Accepts inline or shared strings and sparse cells. It
  reuses `csv_to_rows` + `apply_tables` (atomic) and honours `_id` only
  when `_meta.model_sha256` matches, exactly the zip-of-CSV rules of
  "Interactive database editing".

### HTTP

* `POST /api/exchange/export/<e2k|ifc|dxf|xlsx>` `{model?, units?,
  include?, extensions?, unit_scale?, tables?}` → `{ok: true, filename,
  mime, content | content_b64, report}`.
* `POST /api/exchange/import/<fmt>` `{text | content_b64, units?,
  unit_scale?, sections_from_current? = true, layer_map?, model?,
  dry_run?, soft_errors?}` → `{ok: true, model, warnings, report}`.
  * `report = {format, read: {stories, materials, frame_sections,
    shell_sections, frames, areas, links, load_patterns, load_cases,
    load_combinations, groups}, extensions?, tables?}`.
  * The model replaces the server model unless `dry_run`.
  * Excel applies the tables to `model` (or the current model).
* Errors: 400 `{error}`, or 200 `{ok: false, error}` with `soft_errors`.
  A text file without frames, areas or links is an error.

### UI (`js/exchange.js`, `js/mock_exchange.js`)

* **Menus.** File › Import ▸ and File › Export ▸. Two submenu nodes are
  added to `js/etabs.js` and filled through `sky.menu.add`; acts are
  `file-xch-imp|exp-e2k|ifc|dxf|xlsx`.
* **Export dialogs** (`#xcExport`):
  * E2K: file units (`#xcUnits`, default = the display units), include
    checkboxes, `$SF` extensions;
  * IFC: physical / analysis / supports;
  * DXF: drawing units (`#xcDxfUnit`) and content;
  * Excel: table groups.
* **Import dialogs** (`#xcImport`): file (`#xcFile`), E2K fallback units,
  DXF drawing units and "sections from the current model".
* **Report.** Each run ends in a report dialog (`#xcReport`): counts, the
  exact flag, extension keys, skipped features and warnings.
* **Model.** Exports never touch the model. E2K / IFC / DXF imports
  replace the working model (with a confirm when it is dirty); Excel
  applies the tables and marks the model dirty.
* **Mock (`?mock=1`).** Files are built client-side. Each embeds the model
  JSON in a comment, so mock round trips are identical; foreign e2k / DXF
  files get a basic parse. Downloads are stubbed into
  `__sky.exchange.downloads`.
* **Test hook.** `__sky.exchange = {FORMATS, openExport, openImport,
  exportNow, importNow, download, showReport, state, downloads,
  lastReport}`.

### Validation (`tests/test_exchange.py`)

* **Representative model.** Two stories; rectangular, W14X90 database,
  channel, general and built-up sections; a wall with an opening, a shell
  slab, a membrane slab; a brace, a link along Y, a rotational spring,
  releases, rigid offsets, a cardinal point and a 30° column; a group;
  dead self weight, UDLs, point / trapezoid / global-X frame loads, area
  loads, a nodal load and story forces; static, RS and TH cases, add and
  envelope combos, the mass source.
  * E2K with extensions: the `to_dict()` SHA-256 is identical; only the
    material colour and the built-up labels / template need `$SF` lines.
  * E2K native records only: full `run()` results are identical (max
    relative difference 0.0).
  * kip-in, N-mm and kgf-m: the model is equal to 1e-12. Static, combo and
    RS results are within 1e-9 (observed 5.5e-11). Modal shapes and the
    Rayleigh-damped TH are within 1e-6 (observed 1.8e-9 and 1.7e-7): the
    iterative eigen solver tolerance amplifies last-bit input changes.
* **ETABS files.** A hand-written ETABS v20 cantilever (W360X60,
  P = 10 kN, L = 4 m) gives `PL³/(3E·I33) + PL/(G·As2)` to 1e-9.
  `FROMFILE "Euro.xml" DBSECTNAME "IPE300"` resolves the eurocode record.
  A two-tower model round-trips stories, tower tags and members natively.
  `import_e2k` is unchanged.
* **IFC.** Member end points, corners, openings and stories are exact;
  angles to 1e-9; W14X90 comes back as the AISC record; channel dims and
  rectangle / general / built-up properties are exact; physical-only files
  to 1e-9.
* **DXF.** Kinds, sections, coordinates, stories and openings are exact;
  a sanitised layer (`BEAM 300/600`) maps back; a mm drawing to 1e-12.
* **Excel.** Byte-identical round trip; a `b` edit recomputes the
  rectangle.
* **HTTP.** Export → import of all four formats; an empty file is a 400.
* **Playwright.** Mock 33/33, live 34/34:
  * the eight dialogs open from the menus;
  * exports leave the model byte-identical;
  * E2K export → import is identical (server SHA-256 in live);
  * kip-in display units set the default E2K units and the file reads
    `UNITS "KIP" "IN"`;
  * IFC, DXF (mm) and Excel imports;
  * an analysis runs on the imported model;
  * legacy File › Import… and Define › Material Library still open;
  * a bad file shows an inline error;
  * zero console errors.

## Exact section solver (v1.22)

`skyframe/core/section_fem.py` is an in-house finite-element solver for
cross-section properties. It needs **numpy only**; the runtime
dependencies stay openseespy, numpy and flask. It replaces the optional
`sectionproperties` path. Built-up sections, Section Designer sections
and parametric shapes use it for the exact torsion constant J, the
warping constant Cw, the shear centre and the shear areas.

**Input.** `solve(regions, nu=0.0, target=600, max_area=None,
use_cache=True)`, where:

* `regions` is a list of `{"outer": [[x, y], ...], "holes": [[[x, y],
  ...], ...], "n": float}`;
* a point's weight is the sum of `n` over the regions that contain it.
  `n` is the modular ratio `E_i / E_ref`. Overlapping regions add up,
  like the closed-form shoelace sums;
* loops may touch, share edges or overlap. The planar arrangement splits
  every edge at every crossing and T-junction, so the mesh conforms;
* `circle_loop(cx, cy, r, n=96)` is a polygon with the circle's exact
  area;
* `shape_regions(type, dims, n=1, fillets=True)` gives the outline of a
  parametric or database shape in its bounding-box-centred frame. It
  includes the rolled root fillets `dims.r` (I, channel, tee, angle) and
  the rounded tube corners `r_out`.

**Mesh.** A constrained Delaunay triangulation in pure Python:
1. incremental Lawson-flip insertion inside a bounding box;
2. constraint segments recovered by midpoint insertion;
3. faces classified by flood fill between constrained edges;
4. Ruppert refinement: encroached subsegments are split, and the
   circumcentre is inserted when a triangle's area is over
   `A_total / target` or its minimum angle is under about 25°.

Every triangle becomes a straight-sided 6-node Tri6.

**Solve.** Pilkey's formulation, the same as sectionproperties, with
6-point degree-4 Gauss quadrature, modulus-weighted:
* warping function ω: `J = Ixx + Iyy − ω·f`;
* shear functions Ψ and Φ, with Poisson's ratio `nu`;
* shear areas `As = Δs² / κ`;
* shear centre by the elasticity method (`xs`, `ys`) and by Trefftz
  (`xs_trefftz`, `ys_trefftz`);
* `Cw` about the Trefftz shear centre.

One node per connected part is pinned, since all right-hand sides are
self-equilibrated. The system is reordered by RCM and factorised once
with a block-banded Cholesky (`numpy.linalg.cholesky` on
bandwidth-sized blocks). Three right-hand sides reuse that factor.

A section made of several disconnected parts gets J as the sum of the
parts. Its `Cw`, `xs`/`ys` and `Asx`/`Asy` are `null`, and a warning is
added.

**Output** (input frame; moments about the weighted centroid;
transformed to `n = 1`):

```
{method: "exact", A, A_geometric, cx, cy, Ixx (=∫y²), Iyy (=∫x²), Ixy,
 I_major, I_minor, theta (rad, major axis from +x), J, Cw|null,
 xs|null, ys|null, xs_trefftz|null, ys_trefftz|null,
 x0|null, y0|null (shear centre − centroid),
 Asx|null, Asy|null (shear along x / y),
 Sx_top, Sx_bot, Sy_left, Sy_right, Zx, Zy, Z_major, Z_minor,
 nu, parts, mesh: {elements, nodes, dofs}, time_ms, hash, cached,
 warnings: [str]}
```

* `MeshError` is a ValueError and is raised for unusable geometry.
* Results are cached (LRU, 256 entries) by the SHA-1 of the rounded
  geometry and options. Callers get copies.
* `cache_info()` returns `{size, hits, misses}`; `cache_clear()` empties
  the cache.
* The default `nu = 0` is sectionproperties' geometric analysis and gives
  a rectangle's 5/6. With ν > 0, Pilkey's coefficient of wide sections
  drops: a square gives 0.8282 at ν = 0.3.

**Typical cost.** Rectangle about 0.1 s (2k DOFs); W14X90 with fillets
about 0.45 s (4.9k DOFs); round tube about 0.1 s.

**Validation** (`tests/test_section_fem.py`):

| Check | Target | Result |
|---|---|---|
| Rectangle J/(b t³), b/t = 1, 2, 10 | 0.1406, 0.2287, 0.3123 (Saint-Venant series), ±0.5 % | within 3e-5 |
| Circle πr⁴/2 | ±0.5 % | 1e-7 |
| Tube π(R⁴ − Ri⁴)/2 | ±0.5 % | 3e-6 |
| Thin I and channel J vs Σbt³/3 | ±1 % | within 0.4 % |
| Channel shear centre e = 3b²tf/(6b·tf + h·tw) | ±1 % | within 0.06 % |
| I-section Cw = Iy·h²/4 | ±0.5 % to ±1 % | within 0.03 % |
| Rectangle As = 5/6·A (ν = 0) | ±0.5 % | within 0.002 % |
| W14X90 (database dims with r = kdes − tf) vs AISC | J 4.06 in⁴, Cw 16000 in⁶, stated tolerance ±2 % | J 4.068 in⁴, Cw 15830 in⁶ (−1.1 %) |
| W14X90 + two 400×20 cover plates | — | exact J / open-sum J = 3.55 |
| sectionproperties 3.10, when installed (angle, channel, W + cover, composite at ν = 0.2) | J, As, Cw, shear centre ±1 % | worst 0.13 % |

**Built-up sections.**
* `torsion` accepts `"auto" | "open" | "fem" | "exact"`. `"fem"` is the
  legacy alias of `"exact"`. Both run `exact_regions(bs, ns)`: the union
  outline, database fillets included, weighted by the modular ratios.
* `section_properties` then reports `J_method: "exact"`, the exact J
  (mirrored into the FrameSection), the shear areas and the shear centre
  (`method: "exact"`). An exact symmetric coordinate is kept.
* It adds `Cw` and `exact: {method, mesh, time_ms, cached, nu, parts,
  hash, Cw}`. Both appear only in exact mode, so auto output is
  unchanged.
* An unmeshable outline falls back to "auto" with a warning.
* `fem_available()` is always true.
* NEW sections default to exact: `POST /api/sections/builtup/template`
  takes `torsion` (default `"exact"`), and the editor opens new sections
  with "exact". `BuiltupSection.from_dict` still defaults to `"auto"`,
  and a saved section keeps its stored method.

**Section Designer.** `DesignerSection.torsion` is `"approx"` (default,
absent from `to_dict`) or `"exact"`.
* Exact mode makes every solid polygon a region with `n = E_poly /
  E_base`. Hole polygons are cut from every solid; rebar is ignored.
* `section_properties` always returns `J_method` (`"approx"` or
  `"exact"`). Exact mode adds `Cw`, `x0`/`y0` (shear centre minus gross
  centroid, y/z), `As2`/`As3`, `exact` and `warnings`. The FrameSection J
  is the exact J.
* `exact_regions(ds, materials)` and `exact_properties(ds, materials)`
  are public.

**Parametric shapes.**
* `shape_properties(type, dims, torsion="closed" | "exact")` always
  returns `J_method` (`"closed_form"` or `"exact"`).
* Exact mode replaces J, Cw, x0/y0 and As2/As3 with the finite-element
  values (fillets `r` included) and adds `exact`. A, I, S and Z stay the
  closed forms.
* `section_kwargs(type, dims, torsion)` passes the method through.
* A FrameSection opts in with `dims.torsion = "exact"`, a string in
  `dims`. `section_ext.validate` accepts `None | "closed" | "exact"`.
  `section_from_dims` and `fill_from_dims` honour it.
* On load, `section_ext.exact_from_dims` sets J, As2 and As3 of such a
  section to the exact values (cached), so the stored method always
  matches the stored numbers.
* Database imports (`steel_db.section_from_db`) keep their tabulated
  values.
* `POST /api/steel_db/shape_props` accepts `torsion` (or
  `dims.torsion`); `props.J_method` is added.

**HTTP.** These calls never mutate the server model:

* `POST /api/sections/exact` takes one of:
  * `{kind: "shape", type, dims}`;
  * `{kind: "builtup", section, materials? | model?}`;
  * `{kind: "designer", section, materials? | model?}`;
  * `{kind: "regions", regions, nu?, target?}`.

  It returns `{kind, method, props}`, where `props` is the
  shape / builtup / designer property dict computed in exact mode, or
  the raw `solve` dict. It answers 400 with `{error}` for bad input.
* `GET /api/sections/exact/info` returns `{solver: "section_fem",
  element: "Tri6", default_target, cache}`.

**UI.**
* `js/secexact.js`: `seExact` (POST, or the mock), `seJMethod`,
  `seDesignerBlock`, `seDesignerNew`, `seShapeExact`. Requests are
  debounced (300 ms) and keyed by geometry.
* `js/mock_secexact.js` (`?mock=1`) answers with the CLOSED-FORM values
  marked `method`/`J_method = "approx"`.
* Built-up editor:
  * `#buTorsion` has Exact / Auto / Open / FEM (legacy), and new sections
    open as Exact;
  * `#buPJ[data-method]` and `#buPJm` show the method, and `#buPCw`
    appears in exact mode;
  * the client computation reports exact as `"approx"` until the
    backend answer arrives.
* Section Designer:
  * `#dsTorsion` (Exact / Approx), with new sections set to Exact;
  * `#dsJ` with `#dsJMethod[data-method]` shows the method;
  * `#dsJx` shows Cw / As2 / As3.
* Parametric shapes: the `#ssExactCb` checkbox and the `#ssExactOut`
  readout (`data-method` is closed-form, exact or approx).
* secpreview adds a "J method" row (`data-prop="J_method"`).
* Hook lines:
  * `secdesigner.js`: import, `newSection`, `save` (the payload now
    carries the base `material`), `_renderProps`;
  * `secshape_ui.js`: import plus the parametric branch;
  * `secpreview.js`: import plus the J-method row;
  * `modeledit.js`: `normalizeDesignerSections` keeps `material`, `base`
    and `torsion`. Before this change it dropped `material`, so a live
    designer upsert failed with 400 and fell back to the mock;
  * `builtup.js` and `builtup_geom.js`: the torsion options.

**Playwright.** Mock 20/20 and live 25/25:
* new built-up, designer and parametric sections use exact, and the
  method is shown;
* saved frame J equals the backend exact J;
* reopening and pressing OK, and Frame Properties OK with defaults, are
  byte-identical;
* the POST /api/model second echo is identical;
* an analysis runs;
* kip-in readouts are in in⁴;
* zero console errors.

## Rhino / Grasshopper exchange

Shared spec `skyframe.exchange/1` (Rhino `.3dm` import via rhino3dm + the
Grasshopper live link). Code: `skyframe/core/rhino_exchange.py` (validation,
role resolution, conversion, stories, merge / replace, results by source),
`skyframe/core/rhino_exchange_fields.py` (model fields),
`skyframe/io/rhino3dm_io.py` (.3dm reader), `skyframe/api/rhino_api.py`
(HTTP), `skyframe/api/web/js/rhinolink.js` + `js/mock_rhinolink.js` (UI).
User guide: `docs/RHINO_LINK.md`. A model that never used the exchange is
byte-identical to before (no new keys are emitted).

### Payload

```json
{"schema": "skyframe.exchange/1",
 "source": {"app": "grasshopper|rhino3dm", "doc": "tower.3dm", "units": "m|mm|cm|ft|in", "tolerance": 0.001},
 "session": "<stable id>",
 "layers": [{"path": "Structure::Beams", "color": "#RRGGBB"}],
 "objects": [{"id": "<guid | guid/k>", "kind": "curve|surface|mesh|point", "layer": "Structure::Beams",
              "attrs": {"sf_section": "W18X50"}, "role": "beam", "props": {"section": "W18X50"},
              "geom": {"type": "line", "p": [[0,0,3000],[6000,0,3000]]}}],
 "warnings": []}
```

* `geom`:
  * curve: `line {p:[a,b]}` | `polyline {p:[...]}` | `arc {p:[start,mid,end]}` | `nurbs {samples:[...], degree?, cv?, weights?, knots?, periodic?}`;
  * surface: `polygon {outer, holes}` | `nurbs_surface {mesh:{v,f}, ...}` (only `mesh` is read);
  * `mesh {v, f}` (faces of 3 or 4 indices);
  * `point {p}`.
* Units: coordinates AND the length props `thickness`, `mesh_size` (also UserText `sf_thickness`, `sf_mesh_size`) are in `source.units`; they are converted to m.
* `role`: `column|beam|brace|frame|wall|slab|shell|support|joint|link|ignore` (`ignore` is an addition). Absent = automatic.
* `props`: `section, material, story, angle, release_i[6], release_j[6], restraint[6], shell_section, thickness, mesh_size, stiffness[6]` (`stiffness`, for links, is an addition). Unknown props give a warning.
* UserText: `sf_role, sf_section, sf_material, sf_story, sf_angle, sf_shell_section, sf_restraint ("111000"), sf_release_i/j ("000011"), sf_thickness, sf_mesh_size`. Keys are case-insensitive; a bad value gives a warning and is ignored.
* `warnings` (optional addition): messages from the .3dm reader.

Errors: a 400 with `{"error": "<path>: <message>"}`, e.g. `exchange.objects[3].geom.p must be a list of at least 2 points`.

### Resolution and conversion (normative)

Each field resolves on its own, highest priority first: object `role`/`props` → UserText → the
first mapping rule whose `layer` glob (case-insensitive `fnmatch` on the full path) matches →
the geometric defaults.

Geometric defaults:
* curves: vertical / horizontal within 1° → column / beam; otherwise brace;
* planar surfaces: |n_z| ≥ cos 1° → slab; ≤ sin 1° → wall; otherwise shell;
* role shell: each face is a slab when |n_z| ≥ cos 45°, else a wall;
* point: support when its layer path contains "support" or it has a restraint (default `111111`); otherwise joint.

Conversion:
* polyline: one member per segment, each segment classified on its own. Arc / NURBS: classified once, by the chord.
* arc: a curved frame (`curve: {type: "arc", via}`; `segments` = ceil(arc length / `mesh_size`) when `mesh_size` is set).
* NURBS: an exact circular arc (all samples within the tolerance) becomes a curved frame. Otherwise straight pieces: Douglas–Peucker on the samples with deviation max(tolerance, length/200), or equal arc-length pieces ≤ `mesh_size`.
* polygon: projected on its Newell plane (skipped with a warning if off-plane by more than max(2·tol, 0.1 mm)); slab normals snapped to +z, slabs counter-clockwise from above; holes become polygon `openings`.
* mesh / `nurbs_surface`: vertices welded within the tolerance; one shell per face; quads off-plane by more than 1e-6 m are split into 2 triangles along the shorter diagonal.
* Default `mesh_size` 1.0 m; default shell thickness 0.2 m.

Sections:
* named section: a model section, else the first steel database containing the shape (imported with the explicit material or the model's first steel material, else `A992Fy50`), else a warning and the default;
* default: a model section whose name contains COL / BEAM / BR, else `W14X90` (column), `W18X50` (beam), `HSS6X6X1/2` (brace) from the AISC database;
* a material different from the section's material gives a copy named `"<section> (<material>)"`;
* materials: exact library name, else a unique prefix (`A992` → `A992Fy50`);
* shell sections: a named one; else an existing section of that thickness and material, else a new `Shell<t>mm`; an unknown name ending in 50–2000 (e.g. `WALL250`) gets t = that many mm when no thickness is given.

Stories (`story_rule` auto):
* clustering: z of horizontal beam / slab parts of objects whose role is beam / frame / slab, gap tolerance = profile `story_tolerance` (default 0.05 m, at least the tolerance); a cluster's level is its mean z;
* the base (lowest existing story bottom, else the lowest z in the model) and existing levels (within the tolerance) are not created;
* new stories are named `Story<n>` (n continues the count); heights are recomputed only when a story is added;
* an object's story: the level equal to its top z, else the story containing it, else the top story (above) or the first story (below); a named rule that does not exist gives a warning and auto.

Repair: `repair(d, ["merge_joints", "remove_zero_length", "remove_duplicates", "snap_shell_corners", "split_at_joints", "split_at_crossings"], tol=clamp(tolerance, 1e-6, 1))`. Before that, pure joints split the straight members passing through them.

### Merge / replace

Key = `(session, id, part)`.

Merge, for a matched object:
* Geometry follows the source. Members split by SkyFrame are moved by the affine map of the old chord `pieces[0].pi → pieces[-1].pj` onto the new one.
* Point records at moved joints follow them (nodal loads, masses, groups, …); ambiguous joints are left in place.
* `section` / `material` / `kind` / `angle` / `releases` / `restraints` / shell `section` / `mesh_size` change only when given explicitly by the source (props / role / UserText; a mapping rule is not explicit).
* `story` is re-derived when the geometry changed.
* Unchanged geometry → status `unchanged`, and the model is identical (idempotent).

Deletion:
* Keys of the session missing from the payload are deleted with `edit._delete_objects` (loads and references cleaned).
* Point records left orphaned are removed, except exchange supports.
* Native objects and other sessions are never touched.

Replace: deletes every object of the session (without orphan cleanup), then creates.

### Model fields (emitted only when set / non-empty)

```json
"members": [{"...": "...", "source_ref": {"session": "S", "id": "guid", "part": 0}}],
"shells": [{"...": "...", "source_ref": {...}}],
"supports": [{"point": [0,0,0], "restraints": [1,1,1,1,1,1], "source_ref": {...}}],
"links": [{"...": "...", "source_ref": {...}}],
"exchange_mappings": {"office": {"rules": [{"layer": "Structure::Beams", "role": "beam", "section": "W18X50",
                                           "material": "...", "shell_section": "...", "story_rule": "auto",
                                           "angle": 0, "thickness": 0.2, "mesh_size": 1.0, "restraint": "111000"}],
                                 "story_tolerance": 0.05}},
"exchange_sessions": {"S": {"app": "rhino3dm", "doc": "tower.3dm", "units": "mm", "tolerance": 0.01,
                            "last_update": "2026-10-12T08:00:00Z", "mapping": "office",
                            "joints": {"<id>#<part>": [x, y, z]}}}
```

* `source_ref` is validated: `session` and `id` are non-empty strings; `part` is an integer ≥ 0 (optional).
* Split pieces share the `source_ref` of the object they came from.
* Profile `thickness` / `mesh_size` / `story_tolerance` are in m.
* `exchange_sessions.<S>.joints` holds the session's pure joints (points without a support).

### HTTP

Every endpoint takes an optional `"model"` in the body: the UI's working model, used instead of the stored one. The result becomes the stored model.

* `GET /api/exchange/status` → `{app: "SkyFrame", version, schema, model, sessions: [{session, objects, members, shells, links, supports, joints, last_update, app, doc, units}], analysis: {has_results}, rhino3dm: {available, version}, revision, link: <link state>}`.
* `POST /api/exchange/preview {exchange, mapping?: name|{rules, story_tolerance?}, mode?: "merge"|"replace"}` → `{summary: {create|update|delete|unchanged: {members, shells, joints, links}}, warnings, objects: [{id, layer, role, section, story, status: create|update|unchanged|skip|delete, rule, parts}], session, mode, stories_created, sections_added, shell_sections_added, repair: {counts, changed}, valid}`. The model is untouched. `joints` counts supports + pure joints; counts are per part (before the repair).
* `POST /api/exchange/apply {exchange, mapping?, mode?, run?}` → `{report, model_summary, model, revision, analysis?}`:
  * `report`: `{schema, session, mode, source, mapping, summary, created|updated|deleted: {members, shells, links, supports, joints}, stories_created, sections_added, shell_sections_added, materials_added, joint_splits, repair: {changed, tolerance_m, counts, warnings, created, deleted, actions}, warnings, objects}`;
  * `model_summary`: `{name, members, shells, links, supports, stories, sessions}`;
  * with `run: true`, `analysis: {ok, case_status, combo_status, warnings}`; the results are stored for `/api/exchange/results` and the tables.
* `GET /api/exchange/results?session=&case=&what=` → `{case, what, session, units: "SI", unit_labels: {length: "m", force: "kN", moment: "kN*m", time: "s"}, items, model?, periods?}`:
  * `case` defaults to the first case (combos are accepted);
  * `displacements`: members `{type: "member", segments: [{uid, member, part, pi, pj, i: [6], j: [6]}]}` (curved members give one segment per chord), points `{type: "point", point, disp: [6]}`, shells `{type: "shell", vertices: [{point, disp: [6]}]}` (corners + opening vertices);
  * `member_forces`: `segments: [{uid, member, part, pi, pj, forces: [12], stations?}]`, in source order (part, then along the chord);
  * `reactions`: `{type: "point", point, reaction: [6]}`;
  * `periods`: `periods: [...]`, `items: {"1": {mode, period, frequency}}`, `model: {periods, frequencies}`;
  * `drifts`: `items` / `model` keyed by story → `{drift_x, drift_y}`; `story`: the full story block;
  * errors: 409 without results for the current model, 404 for an unknown case, 400 for a bad `what`.
* `POST /api/rhino/import`: multipart `file` (+ optional form `session`), or `{path}` → `{exchange, layers: [{path, color, count, kinds}], warnings, mtime}`. 501 without rhino3dm (the message names `pip install "skyframe[rhino]"`).
* `POST /api/rhino/link {path, mapping?, auto_apply?}` / `DELETE` / `GET` → `{linked, path, mtime, current_mtime, exists, changed, session, mapping, auto_apply, last_apply, last_error, revision}`:
  * `changed` = the file mtime differs from the baseline;
  * the baseline is reset by an apply of that session after `/api/rhino/import {path}`;
  * `auto_apply` polls every second (merge, no preview) and bumps `revision`.
* Helpers: `GET /api/rhino/info` → `{available, version, hint}`; `GET /api/exchange/mappings` → `{mappings}`; `POST /api/exchange/mappings {name, profile|null}` → `{mappings, model}`; `POST /api/exchange/sessions/remove {session}` → `{report: {session, deleted: {members, shells, links, supports, joints}, warnings}, model}`.
* CORS: `Access-Control-Allow-Origin` echoes only `http(s)://127.0.0.1|localhost|[::1]` (any port) on `/api/*`; there is no auth (the server is localhost-bound). Flask route functions are prefixed `rhx_`.

UI: File › Import › Rhino (.3dm)…, File › Link Rhino File… and File › Exchange Sessions… (registered via `sky.menu.add`); a status-bar chip **Rhino: linked ✓ / Rhino: changed — Update**. Apply / Remove are one undo step each. `?mock=1` uses canned payloads.

## Shell force contours and shell stresses

ETABS Display › Force/Stress Diagrams › Shell Stresses/Forces parity, and
the Shell Forces / Shell Stresses result tables.  Pure post-processing in
`skyframe/core/shell_results.py` (contours) and
`skyframe/core/shell_tables.py` (tables); the engine and the analyze
payload are untouched (every results JSON stays byte-identical).

### Inputs

Per shell element `i` (`results["shell_quads"][i]`) the 8 Gauss-averaged
resultants `[F11, F22, F12, M11, M22, M12, V13, V23]` (the engine's
`[Nxx, Nyy, Nxy, Mxx, Myy, Mxy, Vxz, Vyz]`, CONTRACT "Shell element
forces") of a SOURCE:

| source | where | state |
|---|---|---|
| static case | `cases[n].shell_forces` | signed |
| staged case | `staged[n].shell_forces` | signed |
| single-valued add combo | `combos[n].shell_forces` (engine superposition) | signed |
| RS case / RS directional combo | `rs_cases[n].shell_forces` — the same `{"<i>": [8]}` shape (unsigned peaks `r`); a source only once the engine emits it | box `[-r, +r]` |
| any other combo | evaluated from `model.combos` | see below |
| TH case | — | not a source ("TH results record no shell forces") |

A source is a list of STATES; a state is a per-element box `lo <= x <= hi`
of the 8 resultants (a signed state is a degenerate box).  Combo rules =
CONTRACT "Load combinations: RS/TH/nested members": `add` sums the member
boxes (nested add combos flattened), `abs` / `srss` / `range` build one box
from the member magnitudes, nested non-add combos enter as their bounding
box, and `envelope` keeps one state PER MEMBER, so every displayed quantity
(principal values and stresses included) is enveloped member by member.
Sign convention: RS signs are lost (`1.2D + E`: max = `1.2D + r`, min =
`1.2D - r`).

### Axes, signs, stresses

* Element axes (verified against OpenSees `stresses` by probing every
  family): quads `e1 = (x2+x3-x1-x4)/2`, `e3 = e1 x (x3+x4-x1-x2)/2`,
  `e2 = e3 x e1` (ShellMITC4, ShellDKGQ, ASDShellQ4 with `-local`);
  ShellDKGT triangles `e1 = x2-x1`, `e3 = e1 x (x3-x1)`; ASDShellT3
  triangles (shell_thick / membrane / plate_thick) `e1' = -e2`, `e2' = e1`.
* `M = ∫ σ z dz` with `z` along `+3`: `σ(z) = N/t + 12 M z / t³`.
  **Top** = `+3` face: `S = F/t + 6M/t²`; **Bottom** = `-3` face:
  `F/t - 6M/t²`; **Mid** = `F/t`.  `S13`/`S23` = `V/t` (average).
  `t` = `ShellSection.total_thickness`.
* **Global axes** (`axes: "global"`): per element an in-plane frame aligned
  with the global axes — slabs (`|n_z| >= cos 45°`): 1 = global X projected
  on the plane, 3 = the upward normal, 2 = 3 x 1; walls: 2 = global Z
  projected (up), 1 = horizontal pointing to +X (+Y for walls along Y),
  3 = 1 x 2.  `N' = B N Bᵀ` (`B_ab = a_a·e_b`); moments and transverse
  shears also take `s = sign(a3·e3)`; Top/Bottom follow `a3`.
* Derived: `Max/Min = (a+b)/2 ± √(((a-b)/2)² + c²)`, `VM = √(a² - ab + b²
  + 3c²)`, `Angle = atan2(2c, a-b)/2` (deg, axis 1 → Max), `VMax =
  hypot(V13, V23)`.  On a box the linear components are exact bounds; the
  nonlinear ones are taken over the 8 corners of the (11, 22, 12) box —
  EXACT for the governing bound (FMax/SMax/FVM upper, FMin/SMin lower),
  a corner estimate for the opposite one; `VMax` exact both ways.  Box
  stresses bound N and M independently (conservative for RS).
* **Layered sections**: linear runs solve them as one elastic section, so
  layer stresses are an APPROXIMATION (always flagged): strain at the layer
  centre from the homogeneous section (`E·mod`, `ν`, summed `t`; stiffness
  modifiers ignored), layers stacked from the `-3` face up; concrete layers
  use their own plane-stress law, steel layers `σ = E_s ε` along the bar
  (`angle` from local 1).  Same-material layers reproduce `N/t + 12Mz/t³`.

### Nodal averaging

`none` = element (centroid) values.  `objects` = at each element corner,
the mean over the elements of the same shell object (region) sharing the
node; `all` = the mean over every element sharing the node whose plane is
parallel (`|n_i·n_j| >= 0.9998`, no mixing across wall/slab junctions).
Neighbour tensors are rotated into the receiving element's output frame
first (exact for a uniform field, any node ordering / flipped normals),
per state; the component is then evaluated from the averaged resultants.
Corner values average the neighbours' CENTROID values (no Gauss-point
extrapolation): a linear field is exact at interior nodes.

### API (`skyframe/api/shell_results_api.py`)

| Method | Path | Body → response |
|---|---|---|
| GET/POST | `/api/shell_results/options` | → `{components: [{key, family, quantity}], locations, axes, averaging, bounds}` |
| POST | `/api/shell_results/sources` | `{results?}` → `{sources: [{name, kind, bounded}], layers, n_elements}` |
| POST | `/api/shell_results/contour` | `{case, component, location?="top", axes?="local", averaging?="none", bound?="max", results?}` → contour |

Results: the body's `results`, else the stored last `/api/analyze`
payload when the model is unchanged, else one analysis (stored) — the
`/api/tables` rule.  400 on bad input / a source without shell results.

```jsonc
{"case": "ENV", "kind": "combo", "bounded": true, "bound": "max",   // bound null when signed
 "component": "S11", "quantity": "stress",   // line_force | line_moment | stress
 "location": "top",                          // null for non-S families
 "axes": "local", "averaging": "objects",
 "values":  [/* per shell_quads index; null where t is unknown */],
 "corners": [[/* per element node, same order as shell_quads[i].nodes */]] | null,
 "range": [min, max],                        // over values and corners
 "approximations": ["..."], "warnings": ["..."]}
```

Components: `F11 F22 F12 FMax FMin FVM FAngle`, `M11 M22 M12 MMax MMin
MVM MAngle`, `V13 V23 VMax`, `S11 S22 S12 SMax SMin SVM SAngle` (location
`top | mid | bot | layer:<k>`), `S13 S23`.  `*Angle` is table-only (null on
max/min sources).

### Tables (registered into `core.tables` by a one-line hook)

| key | title | group |
|---|---|---|
| `shell_forces` | Shell Forces | Analysis Results > Element Output > Area Output |
| `shell_stresses` | Shell Stresses | Analysis Results > Element Output > Area Output |

Columns: `story`, `area` (shell object uid), `element` (shell_quads
index), `case`, `case_type` (LinStatic / NonStatic / LinRespSpec /
Combination / NonStaged), `step_type` ("" signed, "Max"/"Min" for max/min
sources) then `F11 F22 F12 FMax FMin FAngle FVM M11 M22 M12 MMax MMin
MAngle V13 V23 VMax` (shell_forces) or `location` ("Top", "Bottom",
"Layer k (material, kind)") + `S11 S22 S12 SMax SMin SAngle SVM S13 S23`
(shell_stresses; labels S13Avg / S23Avg).  Element-local, unaveraged.
Unlike the other case tables they also list RS cases and max/min combos.
New column quantities: `line_force` (kN/m), `line_moment` (kN·m/m),
`stress` (kPa), `angle_deg` (deg).  Empty rows when the results have no
shell elements.

### UI (`js/shellres.js`, `js/mock_shellres.js`)

Display › Force Diagrams › **Shell Stresses / Forces…** (`dis-shr-contours`,
needs results; dlgkit dialog `#shrDlg`): Case/Combo (`#shrCase`, kind
tagged; Max/Min `#shrBound` for max/min sources), Type (`#shrType`
Resultant Forces / Stresses), Component (`#shrComp`), Location (`#shrLoc`
Top / Middle / Bottom / Layer k), Axes (`#shrAxes`), Contour averaging
(`#shrAvg`), Range (`#shrRange` symmetric / min–max / user with
`#shrUmin` `#shrUmax` in the component's display unit), Colours
(`#shrBands` continuous or 6/10/16 bands), Palette (diverging / rainbow);
OK · Cancel · Apply, "Clear Contours" (`#shrClear`).  The 3D view draws
flat element cells (`none`) or bilinear / barycentric sub-cells from the
corner values (averaged); legend `#shrLegend` in units.js units with the
approximation notes.  View settings only (model untouched; remembered per
browser in localStorage).  `__sky.shellRes = {open, show, clear, state,
sources, contour}`.  `?mock=1` runs the same algorithm in JS (an RS case
without shell forces gets an `|EQX/EQY| × 1.12` stand-in, flagged).
Hooks: `viewer3d.js` (contour provider, item pass, self-drawing items —
3 lines), `app.js` (import + init), `tables.js` (column quantities),
`mock_tables.js` (catalogue + rows).

Tests: `tests/test_shell_results.py` (cantilever plate `S11 = ±6M/t²`
= 5625 kPa at x = 0.25 m, nodal averages = `M(x_node)`; membrane wall
`F11 = 10 kN/m`, `S11 = 50 kPa` in every averaging mode; principal state
(3, -1, 2) → `1 ± √8`, VM 5, 22.5°; uniform field exact under averaging on
a mixed quad / triangle mesh with scrambled frames and flipped normals;
OpenSees axis probes for all five element families; envelope / range /
nested envelope max-min; RS stub (per-mode values CQC-combined with
`modalcombo.cqc_rho` + `_combine_rows`) and `1.2D + E`, abs, srss; layered
concrete / steel layers; tables; API).

## Response spectrum: eccentricity, U3 / angle, directional combination, shell forces

Bulk in `skyframe/core/rs_ext.py` (fields, validation, JSON) and
`skyframe/engine/rs_ext.py` (engine). Shared files carry small hooks only:
`ResponseSpectrumCase` fields + `to_dict` / `from_dict` / `_validate_rs_case` /
`add_rs_case(**ext)` / `_validate_rs_combo` (model.py), the
`run_response_spectrum` dispatch + eccentricity post-step, the
`run_rs_directional` CQC3/ABS dispatch, per-mode shell resultants in
`_modal_static` / `_modal_static_scaled` / `_combine_rsa` /
`modalcombo.combine_case_results` / `modal_condensed._unit_response`, the UZ
participation entry (`_participation`), `CaseResults.rs_info`, the
`parallel.prefetch_rs` serial fallback and five `rs_cases` table columns.

### `ResponseSpectrumCase` fields (JSON keys written only when non-default)

| field | default | meaning |
|---|---|---|
| `direction` | — | `X` (U1) \| `Y` (U2) \| **`Z` (U3, vertical)** |
| `angle` | `0.0` | excitation angle in plan, degrees CCW from +X: U1 = `(cos a, sin a)`, U2 = `(-sin a, cos a)`; U3 ignores it |
| `loads` | `[]` | several directions in ONE case: `[{"dir": "X"\|"Y"\|"Z", "scale": 1.0, "function": ""}]` (each dir at most once; `function` "" = the case's function / inline spectrum; effective scale = `case.scale * load.scale`). Non-empty replaces `direction` (which stays a valid label). |
| `dir_combo` | `"SRSS"` | `SRSS` \| `CQC3` \| `ABS` (directional combination of the loads) |
| `dir_sf` | `1.0` | ABS scale factor in [0, 1] (1 = absolute sum, 0.3 = 100/30) |
| `ecc` | `0.0` | accidental eccentricity ratio in [0, 0.5] |
| `ecc_overrides` | `{}` | `{story \| "story/diaphragm": ratio}` overrides (a named-diaphragm key wins over its story key) |
| `ecc_ax` | `false` | ASCE 7-16 §12.8.4.3 `Ax` torque amplification |

`add_rs_case(..., angle=, loads=, dir_combo=, dir_sf=, ecc=,
ecc_overrides=, ecc_ax=)` accepts the same keywords.

Validation (`ValueError`): bad direction / dir / duplicate dir / unknown
function / non-finite angle or scale; `dir_combo` not in the list; `dir_sf`
outside [0, 1]; `CQC3` without both X and Y loads, or with a modal
combination other than CQC / SRSS / DSC, or with `rigid_response` /
`include_missing_mass`; `ecc` (or an override) outside [0, 0.5]; override of
an unknown story or story/diaphragm; eccentricity with no horizontal load.

`rs_combos[...].method` (`RS_DIRECTIONAL_METHODS`) also accepts `"CQC3"`
(needs two single horizontal cases with perpendicular directions, the same
`num_modes`, `combo_method` in CQC/SRSS/DSC, `damping`, `dsc_td`;
eccentricity of either case is applied per direction, torques by SRSS) and
`"ABS"` (`|Rx| + |Ry|`).

### Method

Per mode one static solve gives `q_i` = the response to `M phi_i` (the
condensed modal path's unit responses are scaled instead). Load `d` along
unit vector `e` with spectrum `S_d`: `r_i = Gamma_i(e) S_d(T_i) scale g q_i`,
`Gamma_i(e) = phi_i^T M iota_e / phi_i^T M phi_i` (`iota_e` = `e` on the
UX/UY/UZ massed DOFs). Each load is combined with the case's modal method
(every v1.13 method, rigid response, missing mass along `e`) into `R_d >= 0`;
then `SRSS`: `sqrt(sum R_d^2)`; `ABS`: `max_k (R_k + sf sum_{j!=k} R_j)`;
`CQC3` (Menun & Der Kiureghian 1998, U1 spectrum S1, U2 spectrum S2, U3 by
SRSS): `R^2 = (P+Q)/2 + sqrt(((P-Q)/2)^2 + C^2) + R_z^2` with
`P = A1 + B2`, `Q = B1 + A2`, `C = C1 - C2`,
`A_k = sum rho_ij S_k,i S_k,j a_i1 a_j1`, `B_k` the same with `a_i2 a_j2`,
`C_k` with `a_i1 a_j2`, `a_i1 = Gamma_i(U1) q_i`, `a_i2 = Gamma_i(U2) q_i`
(the maximum over the excitation angle of the two-component response;
`S1 = S2` gives exactly SRSS; independent of `angle`).

**U3** needs UZ mass: Mass Source `include_vertical = true` (tributary
vertical mass; the condensed modal path keeps it) or nodal `mz`. Without it
the case fails with "U3 (vertical) excitation needs vertical mass".
`results.modal.participation[*]` gains `uz` (mass ratio) and `gamma_z`
whenever `include_vertical` is on or an RS case excites U3 (absent
otherwise — legacy output unchanged).

**Eccentricity.** Torsion groups: each story (rigid master + story nodes;
tower stories use the tower's extents), or each named diaphragm (its bbox)
plus the story's free nodes. The diaphragm force along each horizontal load
direction `e_d` is the case's modal combination of `F_i = Gamma_i(e_d)
S_d(T_i) g sum_{t in group} m_t phi_i(t).e_d` (missing mass joins as the
rigid mode); `T_g,d = ecc_g F_g,d (Lx |e_y| + Ly |e_x|)` (`Ly` for X, `Lx`
for Y — the static accidental-torsion dimensions). Several horizontal loads:
SRSS of the torques (SRSS, CQC3), ABS rule for ABS. All torques are applied
as ONE static load (diaphragm master `rz`; otherwise a pure couple
`f_t = T w_t / I_p (-(y - y_c), x - x_c)` over the story nodes, `w` = UX
mass, else 1) and, `+T` and `-T` being enveloped, every quantity is
`R + |R_T|`. `ecc_ax`: `Ax = (d_max / (1.2 d_avg))^2` clamped to [1, 3] from
the combined displacement along `e_d` (`|ux ex| + |uy ey|`) at the two story
nodes extreme in the perpendicular direction; the torques are multiplied by
`Ax` and solved once more.

### Results

`results.rs_cases[name]` keeps the standard case shape and now ALSO carries
`shell_forces` — **exactly the static shape**:
`{"<quad_index>": [Nxx, Nyy, Nxy, Mxx, Myy, Mxy, Vxz, Vyz]}` (positive
envelopes, combined per resultant with the case's modal / directional rule,
plus `|R_T|` for eccentricity). Present on every RS case, on `rs_combos`
(100_30, SRSS, CQC3, ABS) and on eccentric results whenever the model has
meshed shells (absent otherwise, as for static cases). This replaces the
"ABSENT ... on RS results" note of "Shell element forces" for RS (TH and
envelope combos unchanged; extended load combinations with RS members do not
carry shell forces yet).

Extended / eccentric cases add an `rs` block (absent on legacy cases):

```jsonc
"rs": {
  "loads": [{"dir": "X", "vector": [0.866, 0.5, 0.0], "scale": 9.80665}],
  "dir_combo": "SRSS",
  "ecc": {"ratio": 0.05,
          "forces":  {"<story>|<story>/<diaphragm>": {"X": kN}},
          "torques": {"<story>|<story>/<diaphragm>": kN*m},
          "ax": {"<group>": 1.0}}          // only with ecc_ax
}
```

**Legacy byte-identical:** a case with all new fields at their defaults
takes the unchanged legacy path; every pre-existing result key is
bit-identical (verified by JSON hash on frame, meshed-shell, two-tower and
named-diaphragm models); the only addition is `shell_forces` on RS results
of models with meshed shells.

### DB tables

`rs_cases` gains columns `angle` (deg), `dir_combo`, `dir_sf`, `ecc`,
`ecc_ax` (the multi-load list and per-story overrides are edited in the
dialog / JSON).

### UI

`js/rs_ext.js` hooks into Load Case Data — Response Spectrum
(`js/dlgstd_cases.js`): direction U1 / U2 / U3, excitation angle, a
multi-direction load table, directional combination (SRSS / CQC3 /
Absolute + scale factor) and eccentricity (ratio, ASCE 7 Ax). The RS
directional-combination editor offers CQC3 and Absolute.
`js/mock_rs_ext.js` gives `?mock=1` plausible results (angle cosines,
SRSS / ABS / CQC3 of the directions, eccentricity torques).

## Auto lateral loads: international seismic and wind codes, ASCE 7 wind load cases

**Scope (v1.19).** Define › Auto Lateral Loads gains seven seismic and
five wind generators. It also gains the ASCE 7 Fig. 27.3-8 wind load
cases, with the topographic and gust-effect factors, for 7-16 and 7-22.

The bulk code lives in `core/seismic_codes_ext.py` and
`core/wind_codes_ext.py`. A hook at the end of `core/autolateral.py`
registers them. The existing endpoints, `autolateral.compute` /
`generate`, towers support and the summary shape are therefore
unchanged. With towers, each tower uses its own weights, heights and
facade width.

* `POST /api/pattern/auto-lateral/preview {code, <params>}` → summary.
* `POST /api/pattern/auto-lateral {code, name?, ecc?, <params>}` → model.
* Summary: `{"code", "direction", "T", "W", "V", <coefficients>,
  "stories": [{"story", "h", "w", "F", ...}]}`.
  * Wind rows have `w: null` and `qz` (kPa).
  * Rows of ASCE cases 3/4 also carry `fx` / `fy`.
  * Rows of ASCE cases 2/4 also carry `Mz` (kN·m, informative).
* Patterns: wind codes write kind `"wind"`, seismic codes kind `"quake"`.
* Units are SI throughout: m, m/s, kPa (GB `w0` in kN/m²), kN.

### Seismic codes

Optional parameters are in brackets, with their defaults.

| code | method | parameters |
|---|---|---|
| `nzs1170_5` | NZS 1170.5:2004 §6.2. `Cd = Ch Z R N Sp/kmu ≥ max((Z/20+0.02)R, 0.03R)`. Ft = 0.08 V at the top, 0.92 V by w·h. | `Z` [`mu` 4, `soil` A–E "C", `R` 1, `N` 1, `Sp` (from mu), `T`, `structure`] |
| `as1170_4` | AS 1170.4-2007 §6.2. `V = kp Z Ch(T1) Sp/mu W`, kpZ ≥ 0.08, coefficient ≥ 0.01. Distributed by w·h^k. | `Z` [`kp` 1, `site` Ae–Ee "Ce", `mu` 2, `Sp` 0.77, `T`, `structure`, `kpZ_min` 0.08] |
| `gb50011` | GB 50011-2010 §5.2.1. `FEk = α1 0.85 GE`; δn·FEk at the top. | `T` [`intensity` "6"/"7"/"7.5"/"8"/"8.5"/"9", `level` frequent/rare, `site` I0–IV, `group` 1–3, `alpha_max`, `Tg`, `zeta` 0.05, `geq_factor`, `top_force`] |
| `tsc2018` | TBDY 2018 §4.7. `V = W Sae/Ra ≥ 0.04 W I SDS`; ΔFN = 0.0075 N V at the top. | `SS`, `S1`, `R`, `D` [`I` 1, `site` ZA–ZE, `FS`, `F1`, `T`, `structure`, `TL` 6] |
| `kds41` | KDS 41 17 00:2019 §7.2. `Cs = SD1/((R/IE)T) ≤ SDS/(R/IE)`, ≥ 0.01. | `S`, `R` [`IE`, `Fa`, `Fv`, `SDS`, `SD1`, `T`, `structure`] |
| `nscp2015` | NSCP 2015 §208.5. `V = Cv I W/(R T)`, within the 208-9/10/11 bounds; Ft = 0.07 T V. | `R` [`Z` 0.2/0.4, `soil` SA–SE, `I`, `Na`, `Nv`, `Ca`, `Cv`, `T`, `structure`] |
| `ntc_cdmx` | NTC-Sismo CDMX 2017 §3 / §7. `V = a(T)/(Q' R) W`, distributed by w·h. | `T`, `a0`, `c`, `Ta`, `Tb` [`k` 1, `Q` 2, `R0` 2, `k1` 1, `beta` 1] |

Notes on the seismic table:
* `structure` selects the empirical period formula: `steel_mrf`,
  `concrete_mrf` (default), `ebf` or `other`.
* A given T is capped where the code caps it: KDS at Cu·Ta, TSC at
  1.4·TpA, NSCP at 1.3·TA / 1.4·TA.

### Wind codes

| code | method | parameters |
|---|---|---|
| `asce7_16_wind` | ASCE 7-16 Ch. 27 directional. `qz = 0.613 Kz Kzt Kd Ke V²`, `p = G(qz Cp_w + qh·abs(Cp_l))`. Defaults: G 0.85, Cp_w 0.8, Cp_l from L/B. | `V` [`exposure`, `Kzt`, `Kd`, `ze`, `Ke`, `G`, `cp_total`, `cp_windward`, `cp_leeward`, plus the case and topo/gust keys below] |
| `asce7_22_wind` | The v1.16 generator, extended with the same case and topo/gust keys. Without them it calls the v1.16 function, so the output is identical. | v1.16 parameters plus the keys below |
| `en1991_wind` | EN 1991-1-4. qp(z) per §4.5; cpe D/E from Table 7.1 (by h/d); ze per Fig. 7.4; lack-of-correlation factor 0.85–1.0; cs·cd. | `vb0` [`terrain` "0"/I–IV "II", `cdir`, `cseason`, `rho` 1.25, `co`, `kI`, `cpe_D`, `cpe_E`, `cscd`, `n1` (Annex B), `delta` 0.1, `correlation`, `ze_rule` fig7_4/story] |
| `is875_wind` | IS 875-3:2015. `Vz = Vb k1 k2 k3 k4`, `pd = Kd Ka Kc 0.6 Vz² ≥ 0.7 pz`. | `Vb` [`terrain` 1–4, `k1`, `k3`, `k4`, `Kd` 0.9, `Ka` 1, `Kc` 0.9, `cp_windward` 0.8, `cp_leeward` -0.5] |
| `as1170_2_wind` | AS/NZS 1170.2:2021. `Vdes = max(VR Md Mz,cat Ms Mt, vdes_min)`, `p = 0.6 Vdes² Cfig Cdyn`. | `VR` [`terrain` "1"/"2"/"2.5"/"3"/"4", `Md`, `Ms`, `Mt`, `cpe_windward` 0.7, `cpe_leeward` (from d/b), `Ka`, `Kc`, `Cdyn`, `rho_air` 1.2, `vdes_min` 30] |
| `gb50009_wind` | GB 50009-2012. `wk = βz μs μz w0`; μz from Table 8.2.1; βz from T1 per §8.4.3. | `w0` [`terrain` A–D "B", `mu_s` 1.3, `beta_z`, `T1`, `zeta1` 0.05] |

Story force = net pressure × tributary facade area, the same rule as
`make_wind_pattern`:
* the height is half the story below plus half the story above;
* the width is the plan width normal to the wind.

Windward pressure is taken at the story height (EN: at ze). Leeward
pressure is taken at the roof height.

### ASCE 7 wind load cases, topography and gust (7-16 and 7-22)

`load_case` follows Fig. 27.3-8:

| Case | Loading | Torsion |
|---|---|---|
| 1 | full P | none |
| 2 | 0.75 P | `MT = 0.75 P B e` |
| 3 | 0.75 Px + 0.75 Py | none |
| 4 | 0.5625 × (Px + Py) | `MT = 0.5625 (Px Bx + Py By) e` |

* The case 4 factor is 0.75 × 0.75 = 0.5625; the figure prints 0.563.
* `e = ecc_sign × e_ratio × B`, with `e_ratio` = 0.15.
* `transverse_sign` (±1) sets the sign of the second component in cases
  3 and 4.

Torsion goes through the pattern's accidental torsion:
* The summary carries `case_ecc`. `generate` writes it as
  `pattern.ecc`, unless the user gave `ecc`.
* In case 4 with a negative component, the summary also carries
  `case_ecc_y` so that the two torques add. This value goes into the new
  optional field `LoadPattern.ecc_y`:
  * the engine applies `Mz = fx·ecc·Ly + fy·ecc_y·Lx`;
  * `ecc_y` defaults to `None` and is then omitted from `to_dict`, so
    legacy models stay byte-identical;
  * validation requires `|ecc_y| ≤ 0.5`;
  * the DB-edit "Load Pattern Definitions" table has an optional
    "Ecc. Ratio (Fy)" column.

Topography (§26.8.2, Fig. 26.8-1):
* `topo_feature` (ridge / escarpment / hill), with `topo_H`, `topo_Lh`,
  `topo_x` and `topo_upwind`, gives `Kzt(z) = (1 + K1 K2 K3)²`.
* Kzt = 1 unless H/Lh ≥ 0.2 and H ≥ 15 ft (60 ft for exposure B).
* For H/Lh > 0.5, K1 uses H/Lh = 0.5, and K2 and K3 use Lh = 2H.

Gust:
* `gust: "rigid"` uses Eq. 26.11-6.
* `gust: "flexible"` uses Eq. 26.11-10 and needs `n1` < 1 Hz plus
  `damping`.
* The computed G replaces `G`, and the summary carries `gust_factor`.

`wind_codes_ext.generate_asce7_wind_cases(model, code, base, **p)`
writes 12 patterns:
* `<base>-1X` and `<base>-1Y`;
* `<base>-2X±e` and `<base>-2Y±e`;
* `<base>-3X+Y` and `<base>-3X-Y`;
* `<base>-4X±Y±e`.

The UI's "Create all 12 cases" button posts the same 12 bodies.

**Validation.** `tests/test_autolateral_codes_ext.py` has one
hand-worked example per code. Each test computes base shear, vertical
distribution, pressures and coefficients independently from the code
formulas, with the clauses cited in comments. The file also covers:
* the case 2 torque values;
* the case 3/4 factors (0.75 and 0.5625);
* an analysed case 4 base-torque check: the difference between torsion
  on and torsion off equals −ΣMT;
* the hand values for Kzt and the gust factor, rigid and flexible;
* EN Annex B cs·cd;
* towers;
* the API.

Model and results JSON for legacy models and the v1.16 generators hash
identically before and after this change.

**Limitations.**
* NTC-Sismo omits the §7.2.2 reduced two-term distribution for T > Tb.
* GB 50009 βz uses a linear mode shape φ1 = z/H.
* EN cs·cd uses procedure 1 with a total logarithmic decrement `delta`,
  which must include aerodynamic damping.
* The 7-22 Kzt and gust constants are assumed unchanged from 7-16.
* There are no TSC, KDS or Mexican wind generators.

## Code spectra, design combinations, pattern types and live-load reduction

Modules: `core/spectra_codes.py`, `core/combos_codes.py`,
`core/llr_codes.py`, `api/loadcodes_api.py`; UI `js/loadcodes.js`,
`js/mock_loadcodes.js`, `js/mock_loadcodes_data.js`. Every default is
unchanged: a model without the new keys serializes and solves
byte-identically.

**Model data (additive, emitted only when set).**
* `spectrum_functions[name].source = {"code", "params"}` is the generator
  of a code spectrum (absent = user points). It is validated on load: an
  unknown code or bad params raise ValueError. It is metadata only; the
  solver reads `points`. Editing the points table in the user-function
  dialog drops it.
* `llr_settings = {"code": "asce7_16"|"nbcc2020"|"ec1"|"is875"|"user",
  "params"?: {...}, "kinds"?: [pattern kinds] (default
  ["reducible_live"]), "apply_to"?: ["columns","piers"],
  "pier_areas"?: {pier: m^2 per floor}}`. Params: asce7_16 `{KLL: 4}`;
  nbcc2020 `{occupancy: "general"|"assembly"}`; ec1 `{psi0: 0.7,
  category: "A".."E", method: "alpha_n"|"alpha_A"|"min", A0: 10}`; user
  `{basis: "area"|"floors", curve: [[x, R], ...]}`.
* `patterns[*].kind` may now also be `superdead`, `reducible_live`,
  `roof_live`, `snow`, `temperature`, `pattern_live`, `construction`,
  `rain`, `ice`, `move`, `shrinkage`, `creep`, `settlement`,
  `earth_pressure` or `hyperstatic` (`combos_codes.PATTERN_KINDS`, which is
  also the DB-edit enum). E2K export writes the ETABS names ("Super Dead",
  "Reducible Live", …). E2K import is unchanged.

**Code spectra.** `spectra_codes.spectrum(code, params)` returns
`{code, label, clause, params (all, defaults filled), points: [[T, Sa
g]], summary}`. The points hold every corner period exactly, plus a
0.02 / 0.05 / 0.25 s grid up to `T_max`.

| code | formula |
|---|---|
| `asce7_16` | §11.4.6 via `codes.spectrum_value`; × Ie/R (defaults 1) |
| `asce7_22_mprs` | §11.4.5.1: `ordinates` = 22 Sa at the USGS periods (or [T, Sa] pairs); basis `mcer` → ×2/3; linear interpolation; beyond the last period Sa(Te)Te/T (≤ TL), Sa(Te)Te·TL/T², or (Te/T)² when TL < Te; summary SDS (§21.4, 90 % of max 0.2–5 s), SD1 when vs30 is given |
| `asce7_22_two_period` | §11.4.5.2: SDS(0.4+0.6T/T0), SDS, SD1/T, SD1·TL/T² |
| `ec8` | Type 1/2, ground A–E (Tables 3.2/3.3); elastic Eq. 3.2–3.5 with η = √(10/(5+ξ)) ≥ 0.55; design Eq. 3.13–3.16 (q, β); ag = γI·agR; NA overrides of S/TB/TC/TD |
| `is1893_2016` | Fig. 2b: 1+15T, 2.5, (1.00/1.36/1.67)/T, (0.25/0.34/0.42); × Table 3 damping factor; output `ah` = (Z/2)(I/R)(Sa/g) or `sa_g` |
| `nbcc_2020` | 4.1.8.4(7): max(Sa0.2, Sa0.5) up to 0.2 s, linear between Sa(0.5, 1, 2, 5, 10), S(10) beyond; × Mv·IE/(RdRo) |
| `nzs1170_5` | C(T) = Ch(T)·Z·R·N (Table 3.1 modal Ch, classes A–E); design Cd = C·Sp/kμ ≥ max((Z/20+0.02)R, 0.03R); ZR ≤ 0.7 |
| `as1170_4` | kpZ·Ch(T) (Table 6.4, Ae–Ee; kpZ ≥ 0.08); design × Sp/μ |
| `gb50011` | α(T): 0.45αmax → η2αmax ramp to 0.1 s, plateau to Tg, (Tg/T)^γ η2 αmax to 5Tg, then [η2·0.2^γ − η1(T−5Tg)]αmax; αmax from Table 5.1.4-1, Tg from Table 5.1.4-2 (+0.05 s for rare, intensities 8/9) |

Pinned values:
* ASCE 7-16 equals the existing helper at every point.
* EC8 B, ag 0.25: 0.3 / 0.75 / 0.375 at T = 0 / plateau / 1 s. Design
  with q 3.9: 0.2, 0.19231, and the floor 0.05 at 3 s.
* IS 1893 zone IV, soil II, R 5: plateau 0.06, 0.03264 at 1 s.
* NBCC defaults: 0.848, 0.588 at 0.75 s, 0.029 beyond 10 s.
* NZS class C, Z 0.4: plateau 1.172, 0.47568 at 1 s. Design μ 4:
  0.20510, floor 0.04.
* AS Ce: 0.2944, 0.1 at 1 s.
* GB intensity 8, frequent, site II: 0.072 / 0.16 / 0.062187 at 1 s /
  0.036787 at 2 s.

**Design combinations.** `combos_codes.code_combinations(model, standard,
include_rs=True, **params)` returns `[{name, factors: {case: f}, desc}]`.
`apply_code_combinations(..., replace=True)` adds them as `add` combos.
It first removes earlier `<prefix>(ULS|SEIS|SLSC|SLSF|SLSQ|SLS)?NN`
combos that no other combo references.

Cases are grouped by pattern type:
* G = dead + superdead, L = live + reducible_live, Lr = roof_live,
  S = snow. Each of these groups is summed.
* W = wind, E = quake + RS cases, T = temperature. These enter one case
  at a time. W and static E get ± variants, RS gets + only, and an
  accompanying T may be absent.
* A case whose patterns span groups is skipped with a warning. No G case
  gives `[]` plus a warning. T outside EN 1990 gives a warning.

| standard | templates (prefix) | count, canonical D L ±WX ±WY ±EQX ±EQY |
|---|---|---|
| `en1990` | 6.10: γG G; γG G + γQ Q1 + γQ ψ0 Qi; W lead also γG,inf G + γQ W. 6.10ab: 6.10a + ξγG 6.10b. Seismic G + E + ψ2 Qi. SLS char/freq/QP (`EN-ULS`, `EN-SEIS`, `EN-SLSC/F/Q`). Lr never with S/W | 13 ULS + 4 SEIS |
| `is456` | 1.5(G+IL), 1.2(G+IL±X), 1.5(G±X), 0.9G±1.5X; SLS (`IS456-`) | 25 |
| `is800` | 1.5G+1.5IL1+1.05ILa, 1.2G+1.2IL1+1.05ILa±0.6X, 1.2G+1.2IL1+0.53ILa±1.2X, 1.5G±1.5X, 0.9G±1.5X (`IS800-`) | 33 |
| `nbcc2020` | 1.4D; (1.25 or 0.9)D+1.5L+(1.0S or 0.4W); (1.25 or 0.9)D+1.5S+(1.0L or 0.4W); (1.25 or 0.9)D+1.4W+(0.5L or 0.5S); D+E+0.5L+0.25S; `storage` → 1.0L (`NBCC-`) | 21 |
| `asnzs1170` | 1.35G; 1.2G+1.5Q; 1.2G+Wu+ψcQ; 0.9G+Wu; G+Eu+ψEQ; 1.2G+Su+ψcQ; roof ψ = 0 (`AS-`) | 14 |

Params per standard (`GET /api/codes/combos`):
* en1990: `prefix, equation, live_category, snow_site, gamma_G_sup,
  gamma_G_inf, gamma_Q, xi, psi ({L|Lr|S|W|T: [ψ0, ψ1, ψ2]}), seismic,
  sls`;
* is456 / is800: `prefix, sls`;
* nbcc2020: `prefix, storage`;
* asnzs1170: `prefix, occupancy, psi_c, psi_E, sls`.

**Live-load reduction** (`llr_codes`). R per element comes from the total
tributary area At (m², all floors supported) and the number of floors n:
* ASCE 7-16: 0.25 + 4.57/√(KLL At), within [0.5 or 0.4, 1];
* NBCC: 0.3 + √(9.8/B) (B > 20), or 0.5 + √(20/A) (assembly, A > 80);
* EC1: αA = 5/7ψ0 + A0/A1 ≤ 1 (≥ 0.6 for C/D), αn = (2+(n−2)ψ0)/n
  (n > 2);
* IS 875-2: Table 3 (1.0/0.9/0.8/0.7/0.6/0.5);
* user: the curve.

Columns use the half-bay tributary rule of `codes.live_load_reduction`.
Piers use `pier_areas`, or plan area / (columns + piers at the level).
The reducible cases are the static cases whose patterns are all of
`kinds`. With no such case and `kinds` not given, the `live` cases are
used, with a warning.

`apply_to_results(results, model, settings)` returns:

```
{code, live_cases,
 factors: {columns: {uid: {At, A1, n, R}}, piers: {label: {story: {...}}}},
 cases | combos: {name: {member_forces: {uid: [12]}, member_stations: {uid: {...}}}},
 piers: {name: {label: {story: {P, V, M}}}},
 live_factor, skipped, warnings}
```

It applies q + Σ(R−1)·f·q_live to static cases and single-valued additive
combos; other sources go in `skipped`. Results are never modified.

Results tables (`/api/tables/*`), computed with `model.llr_settings`
(absent → ASCE 7-16 defaults):
* `live_load_reduction_factors`: story, element, name, code, at [area],
  n, rll;
* `live_reduced_forces`: story, element, name, case, case_type, station,
  rll, P, V2, V3, T, M2, M3. Columns are reported at both ends; piers give
  P, V2 = V and M3 = M.

**Endpoints.**
* `GET /api/codes/spectra` → `{codes: [{code, label, clause, params:
  [{key, label, type: number|choice|table, default, quantity?, min?, gt?,
  max?, lt?, nullable?, advanced?, choices?}]}], mprs_periods}`.
* `POST /api/function/spectrum-code/preview {code, params?}` → spectrum.
* `POST /api/function/spectrum-code {name, code, params?, damping?}` →
  model (adds or replaces the function, with `source`).
* `GET /api/codes/combos` → `{standards, pattern_kinds, kind_labels,
  kind_groups}`.
* `POST /api/combos/code/preview {standard, params?, include_rs?,
  model?}` → `{combos, warnings}`. `model` is a draft; nothing is stored.
* `POST /api/combos/code {standard, params?, include_rs?, replace?}` →
  model.
* `POST /api/live-reduction/factors {settings?, model?}` → factors plus
  `live_cases`.
* `POST /api/results/live-reduced {settings?}` → `apply_to_results` of the
  stored last analysis (it runs one when none matches the model).

All of them return 400 `{error}` on bad input.

**UI.**
* Define › Functions › RS tab › "Add From Code…" (`#dsRsFnCode`, dialog
  `dsRsFnCode`): name, code, per-code parameters from the catalogue,
  Advanced overrides, the MPRS 22-row table with "Fill from SDS / SD1",
  and a live plot with a summary. Modify/Show on a function with `source`
  reopens this dialog.
* Define › Load Combinations › "Add Design Combos by Code…"
  (`#dcComboCode` → `dcComboCode`): code, parameters, RS-as-E switch and a
  preview table. Add writes into the draft.
* Load Pattern Data › Type lists the new types.
* Tools › Code Checks › Live Load Reduction Factors… (`des-lc-llr`,
  `dsLlr`).
* Display › Force Diagrams › Live Load Reduced Forces
  (`dis-lc-llrforces`) opens Show Tables.
* Hooks:
  * `dlgstd_defs.js`: import, PAT_TYPES push, RS action + Modify routing,
    source drop on point edit;
  * `dlgstd_cases.js`: import + action;
  * `app.js`: import + init;
  * `tables.js`: area quantity;
  * `mock_tables.js`: the 2 tables.
* `?mock=1` uses the JS ports in `mock_loadcodes.js`. Spectra and combos
  give identical numbers; the LLR tributary area is plan area / columns.


## Frame end releases, partial fixity and joint local axes

ETABS **Assign › Frame › Releases/Partial Fixity** and **Assign › Joint ›
Local Axes**. Model side `skyframe/core/releases.py`, engine side
`skyframe/engine/releases_eng.py`, UI `js/releases.js` + `js/mock_releases.js`.

### Model JSON (SI; both keys omitted when unused → legacy files byte-identical)

```json
"members": [{ "...": "...", "releases": "Mi",
              "end_releases": {"i": {"T": 0.0}, "j": {"V2": 0.0, "M3": 1500.0}} }],
"joint_local_axes": [{"point": [6.0, 0.0, 0.0], "angles": [0.0, 30.0, 0.0]}]
```

* `end_releases[end][dof]` with `end` in `i`/`j` and `dof` in
  `P V2 V3 T M2 M3` (member local axes 1/2/3). The value is the
  **partial-fixity spring**: `0` = fully released, `> 0` = an elastic spring
  between the joint and the member end (kN/m for P/V2/V3, kN·m/rad for
  T/M2/M3). Unlisted DOFs are continuous. A list shorthand
  (`{"j": ["V2"]}`) is read as zero springs. Errors (400): unknown end,
  unknown DOF, negative / non-finite spring.
* The legacy `releases` tokens are the canonical subset: `"Mi"` ≡
  `{"i": {"M2": 0, "M3": 0}}` (and `"Mj"`). The effective release set of an
  end is the union of both. The UI and the DB-edit table write the
  **canonical form**: an end that is exactly the M2+M3 pin with no springs
  is stored as the token, anything else under `end_releases` (and the key
  is dropped when empty).
* `joint_local_axes[k].angles = [a, b, c]` in degrees:
  `R = Rz(a)·Ry(b)·Rx(c)` (a about global Z, then b about the rotated 2
  axis, then c about the rotated 1 axis); local axes 1/2/3 are the columns
  of `R`. Errors: point / angles not 3 finite numbers, duplicate joint.

### Engine (exact condensation, every element path)

Per member END:

* **native** — only full M2 and/or M3 releases (no spring) on an Euler
  `elasticBeamColumn`: the element's own `-releasey` (M2) / `-releasez`
  (M3) codes; exact fixed-end forces condense the same local DOFs
  (`_condensed_fef` with indices 4/5 and 10/11). The legacy Mi/Mj pin is
  exactly this path with the identical element arguments → legacy models
  give byte-identical results (verified: results-JSON SHA-256 of the
  exchange representative model, a released quick building with point /
  partial / thermal loads, and the curved two-hinged arch, before = after).
* **spring** — anything else (P, V2, V3, T, any spring, any release on an
  `ElasticTimoshenkoBeam`): the element end connects to a co-located
  duplicate node joined to the joint by a `zeroLength` oriented on the
  member local axes (`-orient` local 1, local 2). Released DOF: the spring
  `k` (no material when `k = 0`); continuous DOF: a stiff tie
  `1e6 ×` the member's own stiffness in that DOF (EA/L, 12EI/L³, GJ/L,
  4EI/L). The element itself is unreleased, so every load path
  (beamUniform, beamPoint, exact FEF, uniform / gradient thermal) uses the
  unreleased element FEF and its end nodal loads go to the duplicate node
  (`asm.pz_load_tag`); the global solve performs the condensation. Works on
  Euler and Timoshenko elements (the Timoshenko element is kept), frame
  auto-mesh (first / last segment), curved frames (first / last chord),
  nonprismatic members (first / last piece), rigid end offsets (release
  inboard of the rigid arm), P-Delta / Corotational builds (no fallback),
  hinges, model-wide P-Delta (string springs read `eleNodes`).
* Station forces, end forces and the closed-form deflection recovery are
  unchanged code: they read the element end forces + FEF corrections; the
  deflection stations use the member-side duplicate displacement at a
  spring end (a shear release shows its slip).
* Rotations left unstiffened at a joint (all members reach it through
  released rotations) are auto-restrained exactly like the legacy pins.
* Accuracy of the spring path: ties soften a released end by ~1e-6 of its
  own stiffness (quick building with every beam pinned: base-shear
  equilibrium 5e-9, column forces 2e-8, periods 2e-7 vs the native path).
  Builds with ties (or skewed-support penalties) take the exact condensed
  modal solve (`modal_condensed`) instead of a dense eigen solve.
* **Stability (ETABS rules)** — full releases only (a spring > 0 is not a
  release here): P at both ends; V2 at both ends; V3 at both ends; T at
  both ends; M2 at both ends + V3 at an end; M3 at both ends + V2 at an
  end. Check Model reports `RELEASE_UNSTABLE` (warning); the engine warns
  (`UserWarning`, run log) and keeps the solve regular with a residual
  `1e-6 ×` the DOF stiffness on the mechanism DOFs.
* Joint local axes: an explicit restraint group (UX..UZ or RX..RZ) whose
  three flags are equal is axis-independent → exact `ops.fix`; a MIXED
  group at a joint with local axes becomes a grounded penalty spring
  (`1e8 ×` the model's largest member end stiffness) on the restrained
  LOCAL DOFs through the named-spring machinery, whose reaction is exact and
  reported in GLOBAL axes (`reactions`). Point springs at the joint act in
  the joint axes (the spring's own property axes / plan angle are composed
  on top). Joint loads stay stored in GLOBAL components; the UI converts
  loads typed in joint local axes (`joint_load_to_global`, also exposed in
  Python).
* Linear buckling and Ritz vectors (numpy frame-only assemblies) keep
  ignoring every release with their existing warning.

### Closed-form validation (tests/test_releases_partial_fixity.py)

| case | hand result | engine |
|---|---|---|
| propped cantilever, V2 released at mid-span, UDL w, halves a | R_A = R_C = w a, M_A = 0, M_B = w a²/2, tip slip v = (w a⁴/6 − w a⁴/24)/EI | 1e-7 |
| fixed-fixed beam, M3 spring k at i, UDL | M_i = (wL²/12)·k/(k + 4EI/L), M_j = wL²/12 + (2EI/L)θ_i (k/(4EI/L) = 0, 0.25, 1, 10) | 2e-5 |
| same on a Timoshenko member | M_i = FEM·k/(k + (4+φ)EI/((1+φ)L)) | 2e-5 |
| fixed bar, P released at i, ΔT | N = EAαΔT·k/(k + EA/L) → 0 for k = 0 | 2e-5 |
| torque at a joint of AB + BC | split by GJ/L (0.6 T₀); T released at BC's B end → T_AB = T₀ | 1e-7 |
| M3-only release (native) | strong axis wL²/8, weak axis still wL²/12 | 1e-9 |
| roller on a 30° incline (joint axes b = 30°, restraint U3 local) | R_B = (P/2)(tan30°, 0, 1), normal to the incline; A takes −(P/2)tan30° | 1e-6 |
| spring path vs native pin | auto-mesh, curved arch, nonprismatic, P-Delta, modal periods, joint reached only through released ends | 2e-5 |

### DB-edit tables (Model Definition > Assignments)

* `frame_releases` "Frame Releases and Partial Fixity": one row per frame
  (no add / delete), columns `uid`, `pi … m3i`, `pj … m3j` (bool), then
  `k_pi … k_m3j` (`stiffness` / `rot_stiffness`). A spring cell is ignored
  on an unreleased DOF. Writes the canonical form.
* `joint_local_axes` "Joint Local Axes": `x y z angle_a angle_b angle_c`.

### UI

* **Assign › Frame › Releases / Partial Fixity** (`asn-frel`, routed by
  `dlgstd.js` to `#relFrame`): 6 × 2 grid (`#relCb_<end>_<dof>`,
  `#relK_<end>_<dof>` in display units, unit column), "— mixed —" for
  multi-selections, live ETABS stability warning `#relWarn`, Fast Assign
  `#relPresetPin` / `#relPresetNone`, OK · Cancel · Apply. Only touched
  cells are written; untouched OK is byte-identical.
* **Assign › Joint › Local Axes…** (`asn-jlocalaxes`, `#relJla`): angles
  A/B/C (deg) for the joints of the selection, live axis preview
  `#relJlaAxes`; all-zero removes the entry.
* **Assign › Joint Loads › Force / Moment (Joint Local Axes)…**
  (`asn-jforce-local`, `#relJl`): F1..M3 in joint axes → stored global.
* 3D view: a ring near each released end, a double ring for partial
  fixity (`__sky.relDrawViewer`).
* `?mock=1`: `mockValidateReleases` (backend messages) and both DB-edit
  tables in `mock_dbedit.js`; the mock round-trips the keys untouched.
* Hooks: `app.js` (2 imports, 1 init line, 1 mock-validator line),
  `dlgstd.js` (the `asn-frel` route), `mock_dbedit.js` (import + catalogue
  line), `viewer3d.js` (1 draw line), `edit_geom.js` (Divide / Join Frames
  carry `end_releases` exactly like `core/edit.py`: end i stays on the first
  piece, end j on the last; Join refuses a release at the shared joint).

**Playwright.** Mock 20/20 and live 21/21: menus present; 12 + 12 grid;
OK with defaults byte-identical (releases and joint axes); canonical
writes (`Mi,Mj` preset, mixed legacy + new); kip-in display and input
conversion; stability warning; joint axes preview; local joint load
rotated to global; POST /api/model double echo identical; DB-edit tables
(live + mock); live analysis with the feature (V2-released end shear 0);
existing Assign dialogs still open; zero console errors.

---

## Results tables export, spandrels and Word report (A8)

### Spandrel labels (`skyframe/core/spandrels.py`)

* `FrameMember.spandrel`, `ShellRegion.spandrel` (str, `""` = none; walls
  only) — JSON key `"spandrel"` written ONLY when set.
* `BuildingModel.spandrel_labels: [names]` (Define > Spandrel Labels; key
  emitted only when non-empty).  Legacy models stay byte-identical.
* Validation (`ValueError`): label 1–64 chars, no `,`/`;`, no leading
  space; no duplicate defined label; a slab may not carry a label.
* DB-edit table `spandrel_assignments` ("Spandrel Label Assignments",
  group "Model Definition > Assignments"): rows = every frame + wall
  `{object_type, uid (read only), spandrel}`; no add / delete; a blank
  label removes the assignment; CSV / xlsx imports without ids match by
  row order.

### Spandrel forces (`skyframe/engine/spandrels.py`, pure post-processing)

```jsonc
results["spandrels"][case][label][story] = {          // only when labeled
  "Left":  {"P", "V2", "V3", "T", "M2", "M3"},        // kN, kN*m
  "Right": {...}, "length": L, "n_shells": n, "n_frames": n }
```

* Objects sharing a label on one story form ONE spandrel (story = the
  object's `story`, else the story containing its centroid).  Static cases
  + additive combos.
* Local axes: `e1` horizontal in the spandrel plane (wall: `ez x n`,
  frame: plan direction), oriented toward +X (or +Y); `e2 = +Z`;
  `e3 = e1 x e2`.  **Left** = end at the smaller `e1` coordinate.
* Vertical cuts at both ends.  Shells: the exact pier kernel — the sum of
  the spandrel's OWN element nodal resisting forces (`shell_nodal`) at the
  cut nodes; frames: the section-cut kernel — stations interpolated at the
  cut and rotated with `_local_axes`.  Moments about the cut centroid
  (tributary-height-weighted shell cut nodes, else the frame points).
* Reported = internal force on the material LEFT of the cut from the
  material on its right (ETABS frame convention): `P` tension-positive; a
  frame-only spandrel reports exactly its member's `N, V2, M3` stations at
  the ends.  An unloaded spandrel satisfies `V2_L = V2_R`, `P_L = P_R`,
  `M3_R - M3_L = -V2 L` (pinned in tests, also for a wall panel + top beam
  sharing one label).
* Shell nodal forces are now also captured when spandrel labels exist
  (one-line `_piers_enabled()` hook); without labels nothing changes.

### Show Tables registry and new tables (`skyframe/core/tables_results.py`)

`tables.register_table(key, title, group, cols, fn, after=None)` and
`tables.register_context(fn(engine) -> dict)` extend the catalogue;
`tables.REGISTERED` lists the registered keys (these may be empty for a
model without the feature).  Registered here (group prefix "Analysis
Results >"):

| key | ETABS table | columns / source |
|---|---|---|
| `element_forces_frames` | Element Forces - Frames | Story, Frame, Output Case, Case Type, Station, P V2 V3 T M2 M3 (= `member_stations`, `P = N`) |
| `frame_hinge_states` | Frame Hinge States | Story, Frame, Case, Type, Step, Hinge ID, Rel Dist, DOF, Force/Moment, Deformation, Plastic Deformation, Hinge State (A to B / B to C / C to D / D to E / >E), Hinge Status (<=IO / IO to LS / LS to CP / >CP) |
| `element_forces_links` | Element Forces - Links | elastic links `F = k (u_j - u_i)` (global = link axes), I-End / J-End rows; device links are warned |
| `pier_forces` | Pier Forces | Location "Bottom", `P = -results P` (ETABS tension +), V2 = V, M3 = M |
| `spandrel_forces` | Spandrel Forces | Left / Right rows, P..M3, Length |
| `tendon_forces` | Tendon Forces | Station, Angle Change, P, Loss %, X Y Z |
| `section_cut_forces` | Section Cut Forces - Analysis | F1..M3 (= FX..MZ), Frames, Shells Excl. |
| `pushover_curve` | Pushover Curve | Step, Monitored Displ, Base Force, Roof Drift, hinge counts per state and per status, Total |
| `nonlinear_static_steps` | Nonlinear Static Steps | Step, Load Factor, Monitored Displ, base FX..MZ |
| `th_base_reactions_steps` | Base Reactions - Time History Steps | Step, Time, FX, FY |
| `th_story_displacements_steps` | Story Displacements - Time History Steps | Step, Time, Story, UX, UY |

Auto (ASCE 41) hinge plastic deformation = `|rot| - My/k_e`, with `k_e` =
|M|/|rot| on the elastic branch; fiber / user hinges use their own
`rot_plastic` / `hinge_state`.  The nonlinear / TH tables ignore a case
filter that names none of their cases.

### Export endpoints (`skyframe/api/results_export_api.py`)

* `POST /api/tables/export/<xlsx|sqlite>` body `{tables?, cases?, units?
  ("kN-m" …), skip_empty?, results?}` → `{ok, filename, mime,
  content_b64, report: {format, units, tables: {key: rows}, warnings}}`;
  400 on a bad format / units / empty list, 404 on an unknown table.
  * xlsx (`skyframe/io/results_export.py` via `io/xlsx.write_workbook`):
    `_index` sheet, one sheet per table (name = ETABS title, <= 31 chars),
    header `Label [unit]`, numeric cells, `_meta`.
  * sqlite (stdlib `sqlite3`) is the **Microsoft Access substitute**: one
    SQL table per results table (fields = labels without spaces:
    `OutputCase`, `CaseType` …), plus "Table Index", "Field Units" and
    "Program Control".
* `POST /api/report/docx` body = the `skyframe.io.docx.build_docx`
  document `{title, subtitle?, meta?, paper? A4|Letter, landscape?, toc?,
  blocks: [heading{text, level 1-3} | paragraph{text, style?: "note"} |
  table{columns, rows, align?, caption?} | image{png_b64 | data_url,
  width_px?, caption?} | page_break]}` → `{ok, filename, mime,
  content_b64, size}`; 400 on a bad block or a non-PNG image.

### Frontend

`js/spandrels.js` (Define › Spandrel Labels…, Assign › Frame / Shell ›
Spandrel Label…, Display › Force Diagrams › Spandrel Forces…),
`js/resexport.js` (File › Export › Results Tables to Excel / SQLite…, and
Show Tables "⬇ XLSX" / "Export…"), `js/reportplus.js` (Create Report:
table selection, per-case isometric deformed snapshots, Download .docx),
`js/mock_restables.js` (mock tables, spandrels and JS xlsx / docx writers;
the mock SQLite export is an SQL dump).  Hooks: one line each in app.js
(init), mock.js (import + augment), tables.js (`sky:tables-open` event),
mock_tables.js (`registerMockTable`) and four lines in reportx.js.

## Time-history functions, mass sources, modal cases, Ritz basis and FNA breadth

Units: kN, m, tonne, s.  ETABS parity rows closed: *Time-history
functions*, *Mass source* (multiple named), *Multiple modal cases*,
*Ritz vectors* (usable basis), *Fast Nonlinear Analysis (FNA)*.
Every default reproduces the previous results byte-identically
(SHA-256 of `to_dict()` / `run().to_dict()` / legacy `run_fna` /
`ritz_analysis` JSON on a 3-story frame with RS + TH and on the
isolated block: identical before/after).

### Time-history function input (`skyframe.core.thfunc`)

`TimeHistoryFunction.source: Optional[dict]` (provenance; emitted only
when set; validated: `kind` in `file | matched | inline | sine | cosine
| ramp | sawtooth | triangular | user_periodic`).  Values stay SI m/s^2,
sample k at t = k dt.

* `import_function(text, fmt, dt=None, header_lines=0, units=None,
  scale=1.0)` -> `{values, dt, source, warnings}`:
  * `"at2"` — PEER NGA: 4-line header, NPTS / DT from line 4 (classic
    `NPTS=  3909, DT= .0100 SEC` or NGA-West2 `3909 0.0100 NPTS, DT`),
    units from line 3 (`UNITS OF G` -> g); the first NPTS numbers;
  * `"time_value"` — `t a` pairs after `header_lines`; uniform spacing
    kept, otherwise linearly RESAMPLED at `dt` (default: the smallest
    spacing; the engine needs equal steps); `t0 > 0` prepends `(0, 0)`;
  * `"equal"` — values at equal `dt` (required) after `header_lines`;
  * `values = raw * UNIT_FACTORS[units] * scale` with `g` = 9.80665,
    `m/s2`, `cm/s2`, `mm/s2`, `in/s2`, `ft/s2`.
* `generate_function(kind, **p)` -> `{values, dt, source}`: `sine` /
  `cosine` (`period`, `steps_per_cycle` (16), `cycles`, `amplitude`),
  `ramp` (`ramp_time`, `amplitude`, `max_time`, `dt`), `sawtooth`
  (`period`, `ramp_time <= period/4`, `steps_per_cycle` (40), `cycles`,
  `amplitude`; per period (0,0) (tr,A) (T/2-tr,A) (T/2+tr,-A) (T-tr,-A)
  (T,0)), `triangular` ((0,0) (T/4,A) (3T/4,-A) (T,0)), `user_periodic`
  (`values` of one cycle at `dt`, `cycles`, `amplitude`; a closed
  cycle's repeated first sample is not duplicated).
* `response_spectrum(acc, dt, periods, damping=0.05)` — PSA = w^2 max|u|,
  EXACT for linearly interpolated excitation (state-transition kernels
  `e^{At}` of the SDOF, discrete convolution by FFT); periods with fewer
  than 20 samples per cycle are sub-stepped on the same interpolated
  record (peak-sampling error <= 1 - cos(pi/20) = 1.2%).
* `match_spectrum(acc, dt, target, damping=0.05, t_min=0.05,
  t_max=3.0, n_periods=100, max_iter=40, tol=0.04, target_units="g")` —
  a **frequency-domain approximation** (the time-domain wavelet
  adjustment of Al Atik & Abrahamson 2010 is NOT implemented): iterate
  { PSA on `n_periods` log-spaced periods; ratio `target/PSA` mapped to
  frequency, interpolated in log f onto the FFT bins (zero padded to
  2n), faded to 1 over a quarter decade outside the range; Fourier
  amplitudes scaled, phases kept; inverse FFT; baseline correction
  (least-squares quartic of the double-integrated displacement removed
  through its 2nd derivative) } until `max|PSA/target - 1| <= tol`; the
  best iterate is returned as `{values, dt, periods, target, matched,
  original, max_misfit, iterations, source}` (spectra in m/s^2).
* Pins: AT2 embedded sample exact (incl. NGA-West2 header, short-record
  error); time-value uniform / resampled / prepended values exact;
  generator samples in closed form; PSA == an independent fine-step RK4
  to 1e-6 (>= 20 samples / cycle) and within the 1.2% bound below;
  **matching: a synthetic 20 s record matched to a 6-point target over
  0.05-3 s is within +-10% on a DIFFERENT 157-period check grid**
  (seed misfit > 30%).
* API: `POST /api/th_function/import {text, format, dt?, header_lines?,
  units?, scale?}`, `POST /api/th_function/generate {kind, ...}`,
  `POST /api/th_function/match {values+dt | function, target |
  spectrum_function, damping?, t_min?, t_max?, max_iter?, tol?,
  n_periods?, target_units?}`; 400 with the message on bad input.  None
  of them changes the model (the UI writes the function).

### Multiple named mass sources and modal cases (`skyframe.core.modal_cases`)

`BuildingModel` fields (default `{}`, emitted only when non-empty):

```json
"mass_sources": {"HALF": {"mass_source": {"DEAD": 0.5},
                          "mode": "weight",
                          "options": {"self_mass": true, "patterns": true,
                                      "include_lateral": true,
                                      "include_vertical": false,
                                      "lump_at_stories": true}}},
"modal_cases": {"MH": {"type": "eigen", "num_modes": 12,
                       "mass_source": "HALF", "stiffness_case": null},
                "RITZ": {"type": "ritz", "num_modes": 12, "mass_source": "",
                         "stiffness_case": null, "max_cycles": 0,
                         "ritz_starts": [{"type": "accel", "dir": "UX"},
                                         {"type": "pattern", "name": "DEAD"},
                                         {"type": "links"}]}}
```

* The model-level `mass_source` / `mass_from_patterns` /
  `mass_source_mode` / `mass_options` stay the DEFAULT mass source
  (`mass_source: ""` everywhere); explicit `story_masses` overrides,
  `nodal_masses` and diaphragm additional masses apply under every
  source.  The reserved `MODAL` case (`num_modes`, `modal_from_case`)
  is unchanged and stays the default basis.
* `ResponseSpectrumCase.modal_case`, `TimeHistoryCase.modal_case`
  (`""` = MODAL) and `TimeHistoryCase.initial_case` (`""` = none; a
  nonlinear static case) — emitted only when set.
* Validation (`ValueError`): mass-source mode / option booleans /
  patterns exist / factors >= 0 / lateral or vertical / self or
  patterns; modal-case names unique across ALL cases and not `MODAL`;
  `type` in `eigen | ritz`; `1 <= num_modes <= 1000`; mass source
  exists; `stiffness_case` (eigen only) is a nonlinear static case;
  Ritz starts non-empty, `accel` dir in `UX..RZ`, `pattern` exists,
  `links`; `max_cycles >= 0`; RS/TH `modal_case` exists; TH
  `initial_case` is a nonlinear static case.
* `case_kinds()` lists every modal case as `"modal"`.  Set Load Cases to
  Run: an RS / TH case needs its modal case (`MODAL` only when it uses
  the default); a modal case needs its `stiffness_case`; a TH case needs
  its `initial_case` (chains re-resolved).
* Engine (`skyframe.engine.modal_cases`): each modal case runs on a
  cached SUB-ENGINE built on a deep copy of the source model with the
  named mass source applied, `num_modes` = the case's count,
  `modal_from_case` = its `stiffness_case`, and every RS/TH
  `modal_case` / `initial_case` cleared — same mesh, hence the same node
  tags and member uids, so its results slot into the parent run.
  * RS with `modal_case`: run on that sub-engine (mass map + basis of
    the case).  Pinned: equals the same RS on a model whose DEFAULT
    mass source is the named one (1e-12), and a half-mass source scales
    every period by exactly sqrt(0.5) (1e-9).
  * TH direct integration with `modal_case`: mass source + the case's
    first / third periods for the Rayleigh fit (Ritz periods for a Ritz
    case); `damping_model "modal"` keeps its in-domain eigen solve.
  * TH direct integration with `initial_case`: the predecessor chain's
    cumulative pattern factors become the `gravity` stage (load-control
    chains without hinges; otherwise `NotImplementedError` naming FNA).
  * `run()` adds `"modal_cases": {name: ModalResults.to_dict() + {type,
    mass_source, warnings}}` (only when modal cases exist) and their
    `case_status`.
* DB-edit tables (`skyframe.core.modal_cases_tables`, appended to the
  catalogue): `mass_sources` (name, mode, five option booleans),
  `mass_source_patterns` (source, pattern, multiplier), `modal_cases`
  (name, type, modes, mass source, stiffness case, Ritz max cycles,
  Ritz starts as text `UX; UY; pattern:DEAD; links`).  Renames cascade
  (mass source -> modal cases; modal case -> RS/TH; pattern -> named
  sources and Ritz pattern starts); deleting a referenced definition is
  refused.

### Ritz vectors on the full assembly (`skyframe.engine.ritz_engine`)

A Ritz modal case runs the WYD sequence on the ENGINE's elastic
OpenSees domain (frames, shells, elastic links, springs, rigid / named
diaphragms, panel zones — everything the engine builds), one static
solve per vector on the reused factor-once domain:

* starting loads: `accel` -> `M r_dir` on the massed DOFs; `pattern` ->
  the CONSISTENT nodal load vector (pattern applied at u = 0,
  `P = -nodeReaction`); `links` -> per nonlinear link DOF a unit force
  pair (axial devices along the axis; bearings global X and Y) — for
  FNA the device DOFs of the case;
* chains alternate; two-pass M-Gram-Schmidt; a chain ends when its
  candidate's M-norm falls below 1e-16 of the pre-orthogonalization
  value (or carries no mass); `max_cycles > 0` caps vectors per chain;
  fewer than requested -> a warning;
* the load representation of every vector is carried, so
  `K_r = U^T F_U` is a nodal dot product (Betti through the constraint
  transformation); `K_r z = theta z`, `M_r = I`; vectors M-orthonormal,
  sign: largest massed component positive.  Result: `ModalResults`
  keyed by engine node tags.
* Pins: ONE vector from a tip-load pattern reproduces the 3-mass
  cantilever's static displacement EXACTLY by modal superposition
  (2e-16; one eigenmode misses by ~1%); as many vectors as massed DOFs
  == eigen periods (1e-8) and RS on that Ritz basis == RS on eigen
  (1e-8 base shear); a wall (shell) portal: Ritz == eigen first period
  (1e-6) while the legacy frame-only numpy Ritz is > 1.5x too long;
  elastic links included (hand k = 3EI/L^3 + k_link).
* `POST /api/analyze/ritz {n, direction}`: a model WITH shells or links
  (or a body with `starts` / `mass_source`) now uses the engine assembly
  (response gains `"assembly": "engine"`, shapes keyed by engine tags);
  frame-only legacy requests are byte-identical (the numpy path).
  `OpenSeesEngine.run_ritz` / `core.ritz.ritz_analysis` are unchanged.
* `POST /api/analyze/ritz_full {n?, starts?, max_cycles?, mass_source?,
  model?}` and `POST /api/analyze/modal_case {case, model?}` ->
  `ModalResults.to_dict()` + `{case, type, mass_source, warnings}`.

### FNA breadth (`skyframe.engine.fna`, `skyframe.core.fna_devices`)

`run_fna` routes a case to the extended driver when it uses a
`fp_isolator`, `triple_fp`, `multilinear`, `multilinear_kinematic`,
`friction_spring`, `rubber_isolator_bouc_wen` or `plastic_wen` (if that
type exists) link, a `damper` with `alpha != 1`, `gravity`,
`initial_case` or `modal_case`; every other case keeps the v0.24 path
bit-identically (the extended driver reproduces it to 1e-10 when
forced).  Hinged (`nonlinear`) cases and the other hysteresis types
(`multilinear_takeda`, `multilinear_pivot`) still raise
`NotImplementedError` naming `run_time_history`.

Device laws (rows = global deformation measures; the linear part stays
in the basis, the rest is the pseudo-force):

| link | FNA law | linear part in basis |
|---|---|---|
| damper alpha != 1 | Maxwell `k` + `cd sgn(v) abs(v)^alpha`, implicit trapezoid, bracketed Newton | none |
| fp_isolator | biaxial `(N/R) u` + rigid-plastic friction (stick `k_init`, slip `mu N`, radial return); `N = N0 - kv dz` (>= 0) | `k_init + N0/R`, `kv` |
| friction_spring | the same with `R = inf` | `k_init`, `kv` |
| triple_fp | Fenz & Constantinou (2008) series model: (`mu1`, `2 R1`, `2 d1`), (`mu2`, `R2 - R1`, `d2 (R2-R1)/R2`), (`mu3`, `R3 - R1`, `d3 (R3-R1)/R3`), stick `mu_i W / uy`, stops `1e3 W / R_eff`; needs `mu1 < mu2 <= mu3`, `R1 < R2, R3` | series stick stiffness, `kv` |
| multilinear / multilinear_kinematic | Masing (Iwan parallel EPP springs), uncoupled X / Y | first slope |
| rubber_isolator_bouc_wen | biaxial Bouc-Wen `F = a1 k u + qd z (+ a2 k sgn(u) abs(u)^mu)`, `uy = qd / ((1 - a1) k)` | `k_init` |
| plastic_wen | ETABS Plastic (Wen) `F = r k u + (1 - r) Fy z`, `beta = gamma = 0.5`, `eta = exp` | `k` (bearing) / none (axial) |

* Axial load `N` of FP / TFP: from the gravity state (`-kv d0_z`), else
  `P0` (fp_isolator) / `W` (triple_fp); an FP / flat slider with neither
  -> `ValueError` ("needs its axial load").
* Gravity initial conditions: `initial_case` (any nonlinear static
  chain) or `gravity` (an in-memory 10-step load-control nonlinear
  static case) runs on a copy of the FULL device model; end-state
  displacements give each device row `d0`, every device is driven
  monotonically to `d0`, and the pseudo-force is
  `-B^T (F(d0 + B q) - F(d0) - k_lin B q)` — the reported response is
  the increment past the gravity state (the direct-integration
  convention).
* Basis: `modal_case` (eigen or Ritz; Ritz `links` starts = the case's
  device DOFs); a `stiffness_case` modal case is refused (FNA needs the
  elastic basis).
* `THResults.extra["fna"]` (extended driver only): `{basis, modal_case,
  modes, periods, initial_case ("gravity" for the pattern stage),
  devices: {law: count}, warnings, bearing_axial: {link uid: N0}}`.
* Pins (DI = OpenSeesPy direct integration, viscous damping ~0 — the
  modal and initial-stiffness Rayleigh damping models differ once a
  device yields):
  * damper alpha = 0.5 SDOF: FNA vs DI 2e-4 of peak (spec 1%); the
    alpha = 1 law == the v0.24 Maxwell law (1e-12); alpha = 0.5 steady
    state `cd v^alpha` (1e-6);
  * FP: theoretical loop `F(D) = N D/R + mu N` (1e-9), zero-crossing
    `-mu N`, area `4 mu N (D - mu N/k0)` (1e-3), biaxial orbit friction
    `mu N`; gravity-loaded block: FNA vs singleFPBearing DI 0.8% peak
    (spec 2%), FNA vs an independent hand SDOF Newmark-Raphson 2e-8;
    bearing axial = W/4 (1e-6);
  * TFP: regimes I-IV in closed form (`mu1 W + W u/(2R1)`, tangents
    `W/(R1+R2)`, `W/(R2+R3)`, then `W/(R1+R3)` after surface 2's stop;
    2e-3); FNA vs TripleFrictionPendulum DI 0.5% peak (spec 2%);
  * multilinear: backbone exact, Masing unloading `F_max - 2 F_b(du/2)`
    (1e-12); FNA vs MultiLinear DI 1.6e-6;
  * Bouc-Wen: initial tangent `k` and saturation `r k u + (1-r) Fy`
    (1e-4); FNA vs elastomericBearingBoucWen DI 0.3% peak;
  * `initial_case` == the `gravity` stage (1e-9 FNA, 1e-12 DI);
  * Ritz basis (UX + links, <= 6 vectors) FNA vs DI and vs the eigen
    basis < 1% on the bilinear isolated block.

### UI (js/modalx.js, js/modalx_refs.js, js/mock_modalx.js)

* Define › Mass Source… (`def-mass`) opens **Define Mass Sources**
  (`#mxMassSources`, list `#mxMsList`): the model default ("MsSrc
  (default)", edits the legacy fields) + named sources; Add / Copy /
  Modify / Delete (refused while a modal case uses it) → **Mass Source
  Data** (`#mxMassSourceData`).
* Define › Load Cases › Modal… (`def-modal`) opens **Define Modal Cases**
  (`#mxModalCases`, list `#mxMcList`): MODAL (default, opens the existing
  `#dcModalCase`) + named cases; Add New / Add New Ritz / Copy / Modify /
  Delete → **Modal Case Data** (`#mxModalCaseData`: name, Eigen/Ritz,
  mass source, modes, Ritz starting vectors table `#mxMcStarts`, max
  cycles, eigen stiffness case).  Both lists edit a draft and commit
  changed keys only (OK with defaults is byte-identical).
* RS / TH case dialogs: `#dcRsModalCase`, `#dcThModalCase` and
  `#dcThInitial` (keys written only when set).
* Time History Function dialog: group "Define From" with **Import from
  File…** (`#mxThImport`: AT2 / pairs / equal, browse or paste, header
  lines, Δt, file units, scale, preview), **Generate…** (`#mxThGenerate`)
  and **Match to Spectrum…** (`#mxThMatch`: spectrum function, period
  range, damping, misfit + target / matched / seed chart); exact SI
  values are kept (not the 6-digit textarea rounding) with `source`.
* Modal results tab: `#mxModalCaseSel` shows any named modal case of
  `results.modal_cases` in the modal table / 3D mode shapes.
* Hooks: `app.js` (import + init line after the dialog standard),
  `dlgstd_cases.js` (one spread line each in the RS / TH dialogs, via
  `__sky.modalx`), `dlgstd_defs.js` (TH function tools + exact values),
  `modeledit.js` (import + pattern rename / pattern references).
* Playwright: mock 20/20, live 21/21 (dialogs open, edit, save; OK with
  defaults byte-identical; POST /api/model round trip and second echo
  identical; live run reports `modal_cases` and the tab shows RITZ;
  kip-in amplitude 386.0886 in/s² → 9.80665 m/s²; zero console errors).

## Master / similar stories, selection and snapping

W8 (ETABS parity): UI for engine features that had none, master/similar
stories, selection and snapping tools, and DB-edit coverage. Backend:
`core/similar_stories.py`, `core/snapgeom.py`, `core/modeltables_ext.py`
(hooks: three lines in `core/model.py`, one in `core/modeltables.py`).
Frontend: `js/w8_*.js`, `js/mock_w8.js`, `w8.css`. Analysis results are
unchanged: every new key is modelling metadata or an existing engine field.

### Story master / similar (`Story.master`, `Story.similar_to`)

```json
"stories": [{"name": "Story1", "height": 3.2, "elevation": 3.2, "master": true},
            {"name": "Story2", "height": 3.2, "elevation": 6.4, "similar_to": "Story1"}]
```

* Both keys are emitted only when set (`master: true`, non-empty
  `similar_to`), so legacy JSON is byte-identical.
* Validation (ValueError): `master` must be a bool; `similar_to` must name
  another existing story that is a master; a master has no `similar_to`.
* The similar group of a story is its master plus every story similar to
  that master (bottom → top). An independent story is alone.
  `similar_stories.similar_group`, `scope_stories(model, story, "one" |
  "similar" | "all")` and `replicate_members(model, uids, scope)`.
* UI scope selector (plan "Apply to": This story / Similar / All stories,
  `store.storyScope`):
  * drawing is repeated on every story of the scope;
  * plan clicks and boxes also select the matching objects (same kind and
    plan geometry, shifted by the story elevation) on those stories, so
    Assign dialogs and Edit › Copy / Move / Delete act on all of them;
  * the erase tool erases them too.

### Snapping (plan view)

* Modes:
  * `joint`: frame / link / wall ends, column points, slab corners;
  * `mid`: edge mid-points;
  * `inter`: intersections of two plan segments (both parameters in [0, 1]);
  * `perp`: foot of the perpendicular from the first click of a two-click
    tool, inside the segment.
* Inside the snap radius (default 10 px) the priority is joint < inter <
  mid < perp, then distance. Otherwise the unchanged grid snap applies.
* The modes are a per-browser preference (Draw › Snap Options…, status-bar
  chips), never model data.
* `core/snapgeom.py` mirrors `js/w8_snapgeom.js` exactly (node-checked to
  1e-12). The 3-point arc circumcentre equals `curved.arc_geometry`.

### Selection

* Select › Intersecting Line adds every object whose plan trace the line
  crosses.
* Select › Window / Crossing Box: when on, left → right selects objects
  fully inside and right → left anything touched. Off keeps the legacy rule.
* Select › By Object Type / Label (`*`, `?` wildcards) / Frame or Shell
  Section / Link type / Spring property / Load pattern (frame, area,
  thermal loads, load sets, tendon hosts) / Group, with Select or Deselect.

### UI for existing engine fields

* Slab / Wall Property Data:
  * a `ShellSection.shell_type` selector;
  * a load-distribution selector, written to the membrane slabs that use
    the section (`ShellRegion.distribution / one_way_dir`; only eligible
    convex 4-corner regions).
* Assign Shell Properties: auto mesh (mesh size | n1 × n2 | max size →
  `mesh_divisions`), load distribution, uniform load set. Default values
  remove the keys.
* Define › Section Properties › Shell Uniform Load Sets… and Assign › Shell
  Loads › Uniform Load Sets… (`shell_load_sets`, `ShellRegion.load_set`).
* Draw › Frames › Curved Beam (3-point arc: start, end, point on the arc →
  `curve.via`) and Edit › Edit Frames › Curved Frame Geometry… (3rd point, plan radius
  + side, segments, local 2).

### Database-editing coverage (`core/modeltables_ext.py`)

* New tables, all editable; model → rows → apply is byte-identical, also
  through CSV and zip:
  * properties: `spring_properties`, `hinge_properties`, `hinge_backbones`
    (side both | positive | negative), `story_similarity`;
  * functions: `rs_functions`, `rs_function_points`, `th_functions`,
    `th_function_values` (derived time column), `frequency_functions`,
    `frequency_function_points`;
  * objects: `link_properties` (numeric `params`), `frame_curves`;
  * loads: `shell_load_sets`, `tendons`;
  * cases: `nonlinear_static_cases`, `nonlinear_static_loads`,
    `steady_state_cases`, `steady_state_loads`, `psd_cases`, `psd_loads`.
* New columns:
  * `shell_sections.shell_type`;
  * `frame_objects.curve`, text `via x,y,z | center x,y,z normal nx,ny,nz
    [segments N|auto] [local2 normal]`;
  * `shell_objects.mesh_n1 / mesh_n2 / mesh_max / distribution /
    one_way_dir / load_set`.
* Cascades:
  * pattern renames → load sets, NLS / frequency loads, tendons;
  * story renames → `similar_to`, `control_story`;
  * frame / shell uid → tendon hosts;
  * spring / hinge / function renames → their users;
  * deleting a used definition is refused.
* `js/mock_w8.js` mirrors every table; its rows equal the backend's.

### Validation

* `tests/test_w8_dbedit_similar.py` (42 tests) and `tests/test_w8_snapgeom.py`
  (6 tests):
  * similar groups, and the 5-story Similar draw (5 members, right stories
    and levels);
  * table round trips;
  * one-way set through the table: w = q·8/2 = 20 kN/m, M = wL²/8 =
    40 kN·m, base FZ = qA;
  * curve set through the table: R = 3, arc length πR;
  * load set through the table: FZ = qA;
  * snap closed forms.
* Playwright, mock and live 90/90: every dialog opens from its menu, OK
  with defaults is byte-identical, the POST /api/model double echo is
  identical, kip-in inputs convert, live runs succeed, zero console errors.

## Pushover performance methods (ATC-40, FEMA 440 EL, EN 1998 N2) and the ADRS plot

Code: `skyframe/core/pushover_methods.py` (pure math, every formula in its
docstring), `skyframe/engine/pushover_performance.py` (modal properties +
run hook), `skyframe/api/pushover_perf_api.py` (endpoint);
`js/pomethods.js` (dialog + ADRS card), `js/mock_pomethods.js` (exact JS
port for `?mock=1`). Analysis results only: no acceptance checks.

### Model

`PushoverCase.performance` (`pushover_cases[name].performance`):
`None` (default, the key is not emitted, legacy files byte-identical) or

```json
{"methods": ["asce41", "atc40", "fema440", "n2"],
 "spectrum": {"code": "asce7", "SDS": 1.0, "SD1": 0.6, "TL"?: s}
            | {"code": "ec8", "ag": g, "ground_type"?: "A".."E" (C),
               "spectrum_type"?: 1|2 (1), "S"?, "TB"?, "TC"?, "TD"?},
 "site_class": "A".."F" (D), "behavior": "A"|"B"|"C" (A),
 "atc40_procedure": "A"|"B" (A)}
```

Missing keys take the defaults shown. The stored object is kept verbatim
(validated only); unknown keys, an empty or duplicated `methods` list,
non-positive spectrum values or `TB >= TC` raise `ValueError`. EC8 S/TB/TC/TD
default to EN 1998-1 Table 3.2 (type 1) / 3.3 (type 2).

### Method summary (SI; Sa in g)

* **ADRS:** `Sa = V/(alpha1 W)`, `Sd = d_roof/(PF1 phi_roof)`. The values
  come from the elastic mode with the largest effective mass ratio in the
  push direction. `phi_roof` is read at the push's own monitored joint
  (`resolve_control`). `W = g * (total push-direction mass)`.
* **Bilinear (ATC-40 / FEMA 440):** initial slope = secant to the first
  point, equal area up to the trial `dpi`:
  `dy = (2A - dpi api)/(K dpi - api)`.
* **ATC-40:** `beta_eff = kappa beta0 + 5`, with
  `beta0 = 63.7 (ay dpi - dy api)/(api dpi)`. kappa follows Table 8-1
  (A, B, C). SRA and SRV come from Eqs. 8-9 and 8-10 with the Table 8-2
  minimums. Procedure A iterates `dpi <- di` (falling back to bisection).
  Procedure B builds one bilinear at the curve end and intersects the
  locus with it.
* **FEMA 440 EL:** the §6.2.1 "any capacity curve" coefficients for
  `Teff` and `beta_eff` (three ductility ranges),
  `B = 4/(5.6 - ln beta_eff)`, and the MADRS factor
  `M = (Teff/T0)^2 (1 + alpha(mu-1))/mu`. The performance point is where
  the MADRS meets the capacity spectrum, found by the iteration above.
  `d_max_radial` (where the Teff radial line meets the reduced spectrum)
  equals it at convergence.
* **N2 (EN 1998-1 Annex B):** `Gamma = PF1 phi_roof`,
  `m* = alpha1 M / Gamma`, `F* = V/Gamma`, `d* = d/Gamma`. EPP
  idealisation: `F*y` is the peak, `d*m` is the end of the peak plateau,
  `d*y = 2(d*m - E*m/F*y)`. Then `T*` follows, and `d*t` is set by one of:
  * equal displacement for `T* >= TC`;
  * elastic response when `F*y/m* >= Se(T*)`;
  * otherwise `d*et/qu (1 + (qu-1) TC/T*) >= d*et`.

  `dt = Gamma d*t`. The optional B.6 iteration is not applied.
* **ASCE 41:** the existing coefficient method
  (`design/performance.target_displacement`), with Sa(Te) read from the
  selected spectrum. For an ASCE spectrum without TL the results are
  identical. Here `W` is the modal-mass W from the ADRS item above.

### Results

`results.pushover[name].performance` is emitted only when the case has
`performance` (run hook). `POST /api/results/pushover-performance`
`{case, performance?, model?}` returns the same block plus
`case, direction, curve {disp, base_shear}`. It returns 400 for an
unknown case, bad options or fewer than 2 steps. The block:

```json
{"options": {...normalised...},
 "modal": {"mode", "T", "gamma", "phi_ctrl", "pf_phi", "alpha", "mass", "W", "control_node"},
 "spectrum": {"code": "asce7", "SDS", "SD1", "TL", "Ts", "T0"} | {"code": "ec8", "ag", "S", "TB", "TC", "TD", "ground_type", "spectrum_type"},
 "capacity": {"Sd": [...], "Sa": [...]}, "demand": {"Sd": [...], "Sa": [...]},
 "methods": {
   "asce41": {...target_displacement keys...},
   "atc40": {"procedure", "behavior", "converged", "solver", "iterations": [...],
             "beta0", "kappa", "beta_eff", "SRA", "SRV", "bilinear", "reduced_demand",
             "performance_point": {"Sd", "Sa"} | null, "Teff", "locus"? (B), "bilinear_fixed"? (B)},
   "fema440": {"converged", "solver", "iterations", "bilinear", "mu", "alpha", "T0", "Teff", "Tsec",
               "beta_eff", "B", "M", "reduced_demand", "madrs", "performance_point", "d_max_radial"},
   "n2": {"gamma", "m_star", "F_star_y", "d_star_y", "d_star_m", "E_star_m", "T_star", "TC", "Se",
          "d_star_et", "qu", "d_star_t", "rule", "dt", "epp": {"d_star", "F_star"}}},
 "summary": [{"method", "label", "roof_disp", "base_shear", "Sd", "Sa", "Teff", "beta_eff",
              "converged", "beyond_curve", "step", "hinge_summary"?}],
 "warnings": [...]}
```

`roof_disp: null` means the demand stays above the capacity curve, so
there is no performance point. `beyond_curve: true` means the target lies
past the last converged step. A method that fails reports
`{"error": msg}` in `methods` and adds a warning.

### UI

* **Define › Load Cases › Pushover Performance Methods…** (`def-po-methods`)
  opens the dlgkit dialog `poxMethodsDlg`. It has a case selector and these
  groups: Methods, Demand Spectrum (ASCE 7 or EC8, with an Advanced section
  for S/TB/TC/TD), ATC-40, and ASCE 41 site class.
  * Only edited cases are written, in canonical key order.
  * **Clear** removes the key.
  * OK with defaults is byte-identical.
* **Display › Nonlinear Results › Pushover Performance (ADRS)**
  (`dis-po-adrs`) opens the "Performance methods" card in the pushover
  tab.
  * The ADRS plot shows:
    * the capacity spectrum and the 5 % demand;
    * the ATC-40 reduced demand (and the locus for B);
    * the FEMA 440 reduced demand and MADRS;
    * the bilinear representations (FEMA 440 / ATC-40 and ASCE 41);
    * the N2 EPP and the performance points.

    Each series has its own toggle.
  * One results table holds the ASCE 41 / ATC-40 / FEMA 440 / N2 rows,
    with these columns: roof displacement, base shear, Sd, Sa, Teff,
    beta_eff, step and details.
  * A footer gives the modal ADRS factors.
  * **Compute** calls the endpoint and sends the client model.
* `js/helpdocs.js`: `dis-pushover` now describes what exists. `def-pushover`,
  `def-po-methods` and `dis-po-adrs` are added.

### Validation

* `tests/test_pushover_methods.py` (36 tests):
  * FEMA 440 coefficients at mu = 3 / 5 / 8: 15.8 % and 1.496 T0;
    20.28 % and 1.80 T0; Teff/T0 = 2.17522; then B, M and Tsec.
  * ATC-40 kappa for types A/B/C, continuity at 16.25 % and 25 %, the
    SRA/SRV floors.
  * Equal-area bilinear, exact on a bilinear curve.
  * The FEMA 440 iteration matches the hand fixed point
    `dp = Sa(Teff)/B Teff^2 g/4pi^2` to 1e-5 for three spectra.
  * ATC-40 procedures A and B match the hand velocity-region intersection
    for types A, B and C.
  * N2 by hand: Gamma 1.25, m* 100 t, F*y 500 kN, d*m 0.10 m give
    T* = 0.397384 s, qu = 1.692225 and d*t = 0.0409035 m. The
    equal-displacement and elastic branches are also covered.
  * Tables 3.2 and 3.3.
* `tests/test_pushover_performance_engine.py` (5 tests):
  * the 2-DOF shear building's T, PF1 phi_roof, alpha1 and m* match the
    closed-form 2x2 eigen solution (1e-3);
  * the first-mode elastic push in ADRS is the line `Sa g/Sd = w1^2`
    (1e-6);
  * the run hook (no key when unset);
  * model round trip and validation;
  * the endpoint.
* The JS mock reproduces the Python rows exactly (max relative difference
  0) on three option sets.

## Link types: Plastic (Wen), BRB, bilinear damper, T/C friction isolator

Four more `LinkMember.link_type` values (`core/links_ext.py`, OpenSees
builders `engine/links_ext.py`). Every param is a float; optional keys are
written only when set. Units kN, m, s.

| `link_type` | required | optional (default) | element |
|---|---|---|---|
| `plastic_wen` | `k`, `Fy` | `ratio` 0, `exp` 2, `kv` 1e7 | `BoucWen` per DOF |
| `brb` | `k`, `Fy`, `d_bm` | `beta` 1.1, `omega` 1.3, `R0` 20 | `Steel4 -asym -kin` |
| `damper_bilinear` | `cd`, `Fr` | `ratio` 0, `k` 1e6 | `BilinearOilDamper` |
| `tc_fp_isolator` | `R`, `mu` | `k_init` 1e5, `kv` 1e7, `kt` 0, `gap_t` 0 | `singleFPBearing` (+ hold-down) |

* **Plastic (Wen)** (ETABS *Plastic (Wen)*):
  `F = ratio·k·d + (1 − ratio)·Fy·z`, `dz/dd = (k/Fy)(1 − |z|^exp)` while
  `ḋ·z > 0`, else `k/Fy`. Uncoupled per DOF. Layout = the FNA driver's
  (`engine/fna.py`, names `k Fy ratio exp`): a link with a horizontal
  component acts axially (U1, `twoNodeLink` dir 1); a vertical or zero-length
  link carries the law on both shear directions with elastic vertical `kv`
  (`kv` is rejected on an axial link). `BoucWen(alpha = ratio, ko = k,
  n = exp, gamma = beta = 0.5/uy^exp, A = 1, no degradation)`, `uy = Fy/k`.
  The material integrates z implicitly per analysis step, so the law is
  path-dependent: use a multi-step nonlinear static case (a one-step linear
  static case lands ~2 % off the monotonic curve). FNA: Wen pseudo-force
  (2-row coupled for the shear layout — identical for one-directional input).
* **BRB**: elastic `k` to `Fy` (`dy = Fy/k`), then asymmetric kinematic
  hardening `b_t = (ω − 1)Fy / (k(d_bm − dy))`, `b_c = (βω − 1)Fy /
  (k(d_bm − dy))`: the force is `ω·Fy` at `+d_bm` and `−β·ω·Fy` at `−d_bm`
  (AISC 341 adjustment factors). Axial, length > 0. Rules: `d_bm > Fy/k`,
  `ω ≥ 1`, `β·ω ≥ 1`, `R0 > 0` (Menegotto–Pinto transition; `cR1 = 0.925`,
  `cR2 = 0.15`).
* **Bilinear damper** (ETABS *Damper - Bilinear*): Maxwell dashpot with series
  spring `k`; `F = cd·v` for `|v| ≤ Fr/cd`, then `sign(v)(Fr + ratio·cd·(|v| −
  Fr/cd))`. Axial, length > 0, `0 ≤ ratio ≤ 1`. Like the viscous damper it
  carries no static force and no eigen stiffness (verified).
* **T/C friction isolator**: compression = single friction pendulum (slip
  `μN`, restoring `N/R`, stick `k_init`, axial `kv`); in tension the bearing
  lifts off with **zero axial and shear force** (verified). Optional tension
  hold-down `kt` (ElasticPPGap, engages after `gap_t`; a parallel axial
  element at the same nodes; `gap_t` needs `kt > 0`). Vertical or zero length
  (node i = lower node; zero length: `pi` is the ground side).
* Routing: `plastic_wen`, `brb` and `tc_fp_isolator` make static cases
  nonlinear (Newton), as the other device links do. Damping options
  (`damping_options`): Wen and BRB are `hysteretic_link`, the hold-down
  `hook_link`, the damper and the bearing are never damped. FNA supports
  `plastic_wen`; `brb`, `damper_bilinear` and `tc_fp_isolator` raise
  `NotImplementedError` ("use direct integration").
* Plot-function labels: the axial types report `P`.

## Frequency-dependent link properties

`BuildingModel.link_freq_props` (`core/link_freq.py`; solver
`engine/link_freq.py`, two hook lines in `engine/frequency.py`):

```json
{"FD1": {"dofs": {"UX": [[0.0, 2.0e4, 0.0], [5.0, 4.0e4, 50.0]]}, "links": ["EL1"]}}
```

* DOF keys `UX UY UZ RX RY RZ`: global relative deformation `u_j − u_i` of an
  **elastic** link. `AXIAL`: elongation along `pi → pj` of a **damper** link
  (AXIAL only). Other link types are rejected. A link has at most one
  property.
* Rows `[f_hz, K, C]`: TOTAL stiffness and viscous coefficient at `f`. They
  replace the link's elastic value on that DOF (`stiffness[d]`; a damper has
  none) in Steady-State and PSD cases only. Interpolation is linear in f,
  with end values held. Rules: f ≥ 0 strictly increasing, K finite, C ≥ 0.
* Method, per frequency: `(diag(1/H_i(w)) + Bᵀ D(w) B) q = P a`, where
  `D_r = (K_r(f) − K0_r) + i w C_r(f)` and `B` holds the modal deformation
  rows. Then `Z = q Φ` plus the unchanged static residual (method `direct`),
  plus the extra link force `dF = D B q` added to the reactions of grounded
  link ends and to the base totals. This is exact in the retained modal
  subspace. The result `warnings` gets
  `"frequency-dependent link properties applied: FD1:EL1:UX"`.
* Empty dict: nothing is emitted by `to_dict`, and frequency results are
  byte-identical. `from_dict` prunes link uids deleted since the save.

## Material-proportional (composite) modal damping

* `BuildingModel.material_damping = {material: ζ}` (`core/composite_damping.py`),
  ζ in [0, 1), emitted only when non-empty; `from_dict` prunes unknown
  materials.
* `TimeHistoryCase.damping_model` gains `"composite"`. Per mode:
  `ζ_i = Σ_g ζ_g E_g,i / Σ_g E_g,i`, where `E_g,i = ½ φ_iᵀ K_g φ_i` is summed
  over the frame / shell elements of each material (from one static solve
  under `w_i² M φ_i`, on a fresh engine). Links, springs, hinges and
  materials without a ratio take the case's `damping`. The case then runs
  exactly as `"modal"` with `modal_zeta` = these ratios, in direct integration
  (`modalDamping`) and in FNA. The engine swaps the case on a shallow model
  copy and never mutates the user's model. `THResults.extra["composite_damping"]`
  (and therefore `th_cases[name].composite_damping`) is:
  `{default, material_damping, modes: [{period, zeta, fractions: {group: share}, energy_check}]}`,
  where `energy_check = Σ E / (w²/2)` (1 to round-off).
* The ratios are computed on the modes of the full elastic model; FNA applies
  them by mode index to its linearised basis. Not applied to RS or
  steady-state cases.
* `POST /api/damping/composite` `{model?, case?, damping?, num_modes?,
  material_damping?}` returns the same dict (`api/linkdamp_api.py`); a bad
  material gives 400.

### DB-edit tables (`core/linkdamp_tables.py`, one hook line in `modeltables`)

* `material_damping`: (material, zeta);
* `link_freq_props`: names;
* `link_freq_points`: (property, DOF, f, K, C), SI;
* `link_freq_links`: (property, link).
* `links.link_type` lists the new types, and "Link Properties - Parameters"
  edits their params. `th_cases.damping_model` lists `composite`.

### UI

* `js/linkext.js` registers the 4 types in `LINK_TYPES` (glyph letters W R V
  U) and in the Assign › Link › Link Properties dialog (`js/linkhyst.js`
  `LH_EXT` hooks). Previews: Wen loop; BRB backbone with the ±d_bm marks;
  damper force–velocity; T/C axial law. Inline validation mirrors the backend.
* Define › Section Properties › **Frequency-Dependent Link Properties…**
  (`js/linkfreq.js`): a list, then a data dialog with per-DOF [f, K, C]
  tables (frequency / stiffness / damping units), link checkboxes and a
  K(f) / C(f) preview.
* Analyze › Advanced › **Damping Options…** (`js/dampopts.js`): Rayleigh on
  springs / links / hinges (`damping_options`), material ratios table, and
  a per-mode ζ preview.
* TH case dialogs (standard, Loads panel, DI options) offer **Composite
  (material)**. The standard dialog shows the ratios in use plus Preview. The
  modal case dialog has a Composite Modal Damping Preview. The TH results
  meta line shows `composite ζ [...]`.
* Mock: `js/mock_wbdamp.js` (validation, composite preview from element
  volume shares, results, DB-edit tables).

### Validation

`tests/test_links_waveb.py` (47 tests):

* **Wen**: `max |F − F_exact| = 0.43 % Fy` (exact = RK4 of the Wen ODE).
  `F(0.05) = 119.990` against `ratio·k·d + (1−ratio)Fy = 120`. Loop area
  14.819 against 14.833. With exp = 25 the area is 15.195 against the bilinear
  `4(1−ratio)Fy(D−dy) = 15.2`. A 40-step NLS brace lands on the Wen curve
  within 0.2 %. FNA against DI on 4 Wen bearings: ≤ 2 % peak.
* **BRB** (k = 1e5, Fy = 500, d_bm = 0.05, β = 1.15, ω = 1.4): F(±0.025) =
  588.889 / −635.556, F(±0.05) = 700.000 / −805.000. These equal the hand
  backbone to 1e-6.
* **Bilinear damper** (cd = 200, Fr = 50, ratio = 0.1): terminal velocities
  under P = 10 / 40 / 55 / 65 kN are 0.0500 / 0.2000 / 0.5000 / 1.0000 m/s
  (hand P/cd, then v_r + (P − Fr)/(ratio·cd)), within 1e-5. No static force.
* **T/C isolator**: axial −kv·d in compression. Exactly 0 axial and shear
  force in uplift, including while displaced after sliding (μN + Nd/R within
  2 % before lift-off). Hold-down `kt(d − gap_t)`.
* **Frequency-dependent link**, SDOF: `u = F / (K(f) − w²m + iwC(f) +
  2iζw0wm)` with a relative error ≤ 1e-9 (measured 7e-16), and base
  `|K + iwC||u|`. A constant table equal to the elastic K reproduces the plain
  link. A damper AXIAL C works. PSD runs.
* **Composite**: stacked cantilever, A (E = 30 GPa, ζ = 0.05) under B
  (E = 3 GPa, ζ = 0.02). Energy shares are 0.5939 / 0.4061 (hand
  `(H³ − L2³)/E1 : L2³/E2`), ζ = 0.037817 (hand to 1e-9), energy_check = 1.
  A composite TH equals a modal TH with the hand ζ (DI and FNA, 1e-9), and the
  model is not mutated.
* **Legacy**: hashes of model / run / TH / FNA / SS / PSD JSON for a 3-story
  building, an isolated block with a damper, and the SDOF frequency model are
  identical before and after.
* **Playwright** (port 8712): mock 40/40 and live 48/48. Menus open the
  dialogs. OK with defaults is byte-identical. Edits round-trip through
  POST /api/model (double echo identical). kip-in converts (50 kip →
  222.411 kN; K in kip/in). A live run gives TH `composite_damping` and the
  SS warning. Zero console errors.

## Irregularity checks, grouped ASCE 7 combinations, E2K releases / joint axes, joint-local reactions, RS shell forces in combos, TS 498 / KDS / CFE wind, NTC long-period distribution

Wave B loose ends.  Modules: `core/irregularity_ext.py`,
`core/combos_asce7.py`, `io/e2k_ext.py`, `core/joint_local_results.py`,
`engine/combos_ext.py` (shell forces), `core/wind_codes_ext.py`,
`core/seismic_codes_ext.py`, `api/waveb_api.py`; UI `js/waveb.js` (+
`js/mock_waveb.js`).  No model field is added.  Legacy models: results
JSON, tables, ASCE 7 combos and E2K text are byte-identical, with one
deliberate exception (item 5): an EXTENDED combo (any combo that is not a
static-only add / envelope) on a model with shells now also carries
`shell_forces` (and `min.shell_forces`).

### 1. Irregularity checks

`irregularity_ext.compute(model, results=None, options=None)` returns:

```
{"stories": [{story, elevation, height, mass (t), Dx, Dy, cx, cy,
              n_columns, n_braces, n_walls, Vn_x, Vn_y (kN | null),
              strength_source: "estimated"|"user",
              floor: null | {Lx, Ly, cx, cy, area_net, area_outline,
                             area_hull, notch_x, notch_y}}],
 "discontinuities": [{story, element, type: "column"|"wall",
                      kind: "in_plane"|"out_of_plane"|"unsupported",
                      offset (m | null), ratio (offset / wall length),
                      point}],
 "checks": [{code: "ASCE 7"|"EN 1998-1"|"IS 1893", item, check, story,
             case, direction, value (number | null), limit (text),
             status: "ok"|"irregular"|"extreme"|"n/a", note}],
 "summary": {code: {item: worst status}},
 "options": {ec8_tol, raster}, "warnings": []}
```

Options:
* `ec8_tol` (0.25): the EC8 4.2.3.3(3) judgement band.
* `raster` (200 cells): the floor-outline raster.
* `story_strength` `{story: {"x": kN, "y": kN}}`: overrides the strength
  estimate.

Items and limits:

| Code | Item | Rule |
|---|---|---|
| ASCE 7 | Table 12.3-2 Type 1 | Engine soft-story flag (lateral cases only). |
| ASCE 7 | Type 2 weight | m / m_adj > 1.5. A roof lighter than the floor below is ignored. |
| ASCE 7 | Type 3 vertical geometric | SFRS plan extent / adjacent > 1.3, in X and Y. |
| ASCE 7 | Type 4 in-plane discontinuity | Any in-plane offset. |
| ASCE 7 | Type 5a/5b weak story | Vn / Vn_above < 0.80 (< 0.65 extreme). |
| ASCE 7 | Table 12.3-1 Type 1 | Engine torsional ratio ≥ 1.2 (≥ 1.4 extreme). |
| ASCE 7 | Table 12.3-1 Types 2–5 | Type 2: both notch projections > 0.15. Type 3: openings > 50 %. Type 4: out-of-plane offset. Type 5: element not parallel to X/Y (> 1°). |
| EN 1998-1 | 4.2.3.2(4), (5), (6) | (4): recess (hull − outline)/outline ≤ 0.05. (5): Lmax/Lmin ≤ 4. (6): torsional radius not evaluated (`n/a`). |
| EN 1998-1 | 4.2.3.3(2), (3), (5) | (2): no discontinuity. (3): mass and stiffness upper/lower in [1 − tol, 1 + tol]. (5): setbacks per Fig. 4.1. |
| IS 1893 | Table 5 (i) | Dmax/Dmin = r/(2 − r) > 1.5 (> 2.0 extreme). (i)(b): torsional period longer than a translational one. |
| IS 1893 | Table 5 (ii)–(v) | (ii) re-entrant > 0.15. (iii) openings > 50 %. (iv) out-of-plane offsets. (v) non-parallel elements. |
| IS 1893 | Table 6 (i)–(vii) | (i) k < k_above. (ii) W > 1.5 W_below. (iii) D > 1.25 D_adj. (iv) offset > 0.20 L. (v) Vn < Vn_above. (vi) floating columns. (vii) first 3 modes < 65 %, or Tx and Ty within 10 %. |

The strength estimate ignores reinforcement:
* concrete column: `0.17√f'c·0.8A`;
* concrete wall: `0.17√f'c·t·0.8L`, in its plane;
* steel column: `0.6Fy·min(As2, As3)` (A/2 when unset);
* brace: horizontal component of `Fy A`.

A story whose elements lack `fc` / `fy` gets `n/a`.  Response-based rows
use only static cases whose patterns are all `quake` / `wind`.  A ratio
with a negligible denominator is `null` with a reason
(core/ratio_significance.py).

Table `irregularity_checks` ("Irregularity Checks", Analysis Results >
Structure Output > Other Output Items, after Story Stiffness) holds the
`checks` rows.

`POST /api/irregularity` `{options?, results?}` returns the report plus
`has_results`.  Without results in the body it uses the last analysis.
With no analysis at all, only the model-based rows are evaluated; the
model is not analysed.  Bad options give 400.

### 2. Grouped ASCE 7 combinations

`codes.asce7_combinations` switches to `combos_asce7.asce7_grouped` when a
case applies a `superdead`, `reducible_live`, `roof_live` or `snow`
pattern.  Groups follow `combos_codes.classify`:
* D = dead + superdead;
* L = live + reducible live;
* Lr = roof live;
* S = snow.

All cases of a group take the same factor.  `(Lr or S)` expands to one
combination per group present.

LRFD:
* 1.4D
* 1.2D + 1.6L + 0.5(Lr|S)
* 1.2D + 1.6(Lr|S) + (1.0L | ±0.5W)
* 1.2D + 1.0L + 0.5(Lr|S) ± 1.0W
* 0.9D ± 1.0W
* (1.2 + 0.2SDS)D + 1.0L + 0.2S ± 1.0E
* (0.9 − 0.2SDS)D ± 1.0E

ASD:
* D
* D + L
* D + (Lr|S)
* D + 0.75L + 0.75(Lr|S)
* D ± 0.6W
* D + 0.75L + 0.75(Lr|S) ± 0.45W
* 0.6D ± 0.6W
* (1 + 0.14SDS)D ± 0.7E
* (1 + 0.105SDS)D + 0.75L + 0.75S ± 0.525E
* (0.6 − 0.14SDS)D ± 0.7E

Models with only the legacy kinds never reach the grouped generator.  On
such a model it would give exactly the legacy names and factors (tested).

### 3 / 8. E2K records

* `LOADPATTERN … TYPE "Super Dead" | "Reducible Live" | "Roof Live" |
  "Snow" | …` reads back to the same kind.  Every
  `combos_codes.PATTERN_KIND_LABELS` label is accepted, plus the SUPERDEAD
  and REDUCE LIVE aliases.  The legacy subset reader `io/e2k.import_e2k`
  is unchanged.
* `LINEASSIGN … RELEASE "PI V2I V3I TI M2I M3I PJ …"` lists every released
  DOF, partial fixity included.  `PARTIALFIXITY "M3I 1500 V2J 200"` gives
  the springs in file units (force/length, force·length/rad).
  * A legacy pin writes the legacy `"M2I M3I"`.
  * Reading canonicalises: a pure M2+M3 pin becomes a `releases` token,
    anything else goes to `end_releases`.
* `POINTASSIGN … ANGLEA a  ANGLEB b  ANGLEC c` = `joint_local_axes`, in
  degrees.
* RS cases:
  * one `ACCEL` row per direction, with `SF` = scale × load scale × g and
    `FUNC` per load;
  * `ANG` (the angle) on each row;
  * `ECCRATIO`;
  * `DIRCOMBO "SRSS"|"ABS"|"CQC3"`, plus `DIRSF`.

  Importing several rows gives `loads`, with case scale 1.
  `ecc_overrides` and `ecc_ax` stay in `$SF` lines.

### 4. Joint-local reactions

With `joint_local_axes` set, the `joint_reactions` table gains
`local_axes` ("yes" | "") and `F1 F2 F3 M1 M2 M3` = Rᵀ·(FX…MZ).  Joints
without axes use the identity.  Without joint axes the table is
unchanged.

`POST /api/results/joint-local-reactions` `{case?, results?}` returns
`{"cases": {case: {tag: {point, angles, global: [6], local: [6]}}}}` for
static cases and single-valued combos.

### 5. Shell forces in extended combos

`engine/combos_ext.py` flattens `shell_forces` like every other quantity:
* static members are signed;
* RS members are `[−|f|r, +|f|r]`;
* max/min members use their pair.

Add, envelope, abs, srss and range follow the existing interval rules.
TH members record no shell forces, so a combo with one omits them.  The
combo carries `shell_forces` (max) and `min.shell_forces`.  Shell contours
(`core/shell_results.py`) of these combos equal that box.

### 6. Wind generators (`autolateral.compute(model, code, …)`)

| code | Formula | Parameters |
|---|---|---|
| `ts498_wind` | TS 498 Table 4: q(z) = 0.5 / 0.8 / 1.1 / 1.3 kPa (≤ 8, ≤ 20, ≤ 100, > 100 m), × `q_factor`. F = (cp_w + \|cp_l\|) q(z) A. | `cp_windward` 0.8, `cp_leeward` −0.4, `q_factor` 1 |
| `kds41_wind` | V_z = V0 K_D K_zr K_zt I_w, with K_zr = Kb (z/Zb)^α. Zb/Kb/α/Zg: A 20/0.58/0.33/550, B 15/0.81/0.22/450, C 10/1.0/0.15/350, D 5/1.13/0.10/250. q = ½ρV². F = G_D (Cpe1 q_z + \|Cpe2\| q_H) A. | `V0`, `roughness` C, `KD` `Kzt` `Iw` 1, `GD` 2.0, `cpe_windward` 0.8, `cpe_leeward` −0.5, `rho_air` 1.225 |
| `cfe_wind` | V_D = F_T F_rz V_R, with F_rz = c (z/10)^α and z clamped to [10 m, δ]. Categories 1–4: α 0.099/0.128/0.156/0.170, δ 245/315/390/455, c 1.137/1.0/0.881/0.815; `form="ntc"` sets c = 1. q = 0.613 G V_D². Leeward Cpe from d/b. | `VR`, `category` 2, `form` "cfe", `FT` 1, `G` 1, `cpe_windward` 0.8, `cpe_leeward` (auto) |

Default pattern names: WINDTS, WINDKDS, WINDCFE.

### 7. NTC-Sismo long period

`ntc_cdmx` with T > Tb and `long_period="reduced"` (the default):

```
F_i = w_i a/(Q′R) (kh1 h_i + kh2 h_i²)
kh1 = [1 − 0.5r(1 − q)] Σw/Σwh
kh2 = 0.75r(1 − q) Σw/Σwh²
q   = (Tb/T)^r
```

So `V = a/(Q′R) W [1 + 0.25r(1 − q)]`.  The summary adds `q, r, kh1, kh2,
long_period`.
* `r` defaults to 2, the (Tb/T)² decay of the 2017 spectrum.  NTC-2004
  zones use 1/2, 2/3 and 1.
* `long_period="linear"` gives the previous w·h distribution of
  V = a/(Q′R) W.
* T ≤ Tb is unchanged.

### Validation

`tests/test_waveb_loose_ends.py` pins hand values for every item:

| Item | Hand values |
|---|---|
| Irregularity | Mass 2.0 / 0.5 with the roof rule. D ratio 2.0. Setbacks 0.5 / 0.4 / 0.1. Column offset 5 m, wall offset 2/6. Vn = 4 × 0.17√30 × 0.8 × 0.25 × 1000. L floor: outline 64, hull 82, recess 0.28125. Openings 0.64 / 0.36. Dmax/Dmin = 1.25/0.75. |
| ASCE 7 combos | Grouped equals legacy for LRFD/ASD × SDS. |
| E2K | Native and extension round trips in kN-m, kip-ft and N-mm, with equal analysis results. |
| Joint-local reactions | Skewed roller: F1 = 0, F3 = (P/2)/cos30. |
| RS combo shell forces | Box 1.2s ± \|r\|, envelope, SRSS. |
| Wind | TS 498: 12.6 / 10.8 / 8.64 kN. KDS: q = 551.25 Pa. CFE: q = 980.8 Pa. |
| NTC | kh1 = 0.0654971, kh2 = 0.0160920. |

## Time-dependent materials, incremental creep and tendon elements

ETABS Material > Time Dependent Properties, staged construction with
step-by-step creep / shrinkage / relaxation, and tendons modelled as bonded
elements.  Every default reproduces the previous results byte-identically:
`material_time_dependent` is emitted by `to_dict` only when non-empty, the
staged-case keys `method` / `steps_per_stage` / `output_times` only when
set, and the tendon keys `model_as` / `losses.es_factor` only when set
(`"loads"` = the equivalent-load model, unchanged).

### Material laws (`skyframe.core.timedep`)

```
"material_time_dependent": {"<material>": {
   "law": "cebfip2010" | "aci209" | "gl2000" | "user",   # default cebfip2010
   "rh": 70,             # %, 40..100
   "h0": 0.15,           # m, notional size 2Ac/u (V/S = h0/2)
   "cure_days": 7,       # age at the end of curing = start of drying ts
   "cement": "N",        # MC2010 SL|N|R · ACI 209 I|III · GL2000 I|II|III
   "curing": "moist",    # ACI 209 moist|steam
   "fcm": null,          # kPa; null = fc + 8 MPa (MC2010), 1.1 fc + 5 MPa (GL2000)
   "creep": true, "shrinkage": true, "aging": true,
   "phi_u": null, "eps_shu": null,          # ACI 209 overrides
   "creep_curve": [[dt, phi]], "shrinkage_curve": [[dt, eps]],
   "modulus_curve": [[age, E/E28]], "strength_curve": [[age, fc/fc28]],
   "relaxation": null | {"law": "ec2", "class": 1|2|3, "rho1000": %, "fpk"?}
                      | {"law": "aci", "K": 45, "fpy"?}}}
```

Validation normalises every entry (all keys present after a load); a key
must name a model material; unknown keys / laws / cement classes are
rejected.  Material renames move the entry (model tables and the UI).

Functions (ages in days, kPa): `creep_coefficient(p, t, t0)`,
`compliance(p, E28, t, t0)`, `shrinkage_strain(p, t)` (positive
contraction, drying from `cure_days`), `modulus_ratio(p, t)`,
`strength_ratio(p, t)`, `relaxation_ratio(r, sigma_pi, t_days, fpk, fpy)`.

* **fib MC2010** (5.1-63 .. 5.1-81, T = 20 °C): `phi = phi_bc + phi_dc`,
  `phi_bc = 1.8/fcm^0.7 ln((30/t0adj + 0.035)^2 (t - t0) + 1)`,
  `phi_dc = 412/fcm^1.4 (1 - RH/100)/(0.1 h)^(1/3) / (0.1 + t0adj^0.2)
  ((t - t0)/(beta_h + t - t0))^gamma`, `t0adj = t0 (9/(2 + t0^1.2) +
  1)^alpha >= 0.5`; shrinkage `|eps_cas| + |eps_cds|` with `beta_RH =
  -1.55(1 - (RH/100)^3)` (0.25 above 99 beta_s1); `beta_cc = exp(s(1 -
  sqrt(28/t)))`, `E(t) = sqrt(beta_cc) E28`; `J = 1/E(t0) + phi/E28`.
* **ACI 209R-92**: `phi = (t - t0)^0.6/(10 + (t - t0)^0.6) phi_u`,
  `phi_u = 2.35 gamma_la gamma_lambda gamma_vs` (`1.25 t0^-0.118` moist /
  `1.13 t0^-0.094` steam, `1.27 - 0.67 h`, `2/3(1 + 1.13 e^(-0.0213
  V/S))`); `eps_sh = (t - ts)/(35|55 + t - ts) 780e-6 gamma_RH gamma_tc
  gamma_vs`; `fc(t)/fc28 = t/(a + b t)` (a, b per cement / curing),
  `E ~ sqrt(fc)`; `J = (1 + phi)/E(t0)`.
* **GL2000**: `phi28 = Phi(tc)[2 dt^0.3/(dt^0.3 + 14) + sqrt(7/t0)
  sqrt(dt/(dt + 7)) + 2.5(1 - 1.086 h^2) sqrt(dt/(dt + 0.12 (V/S)^2))]`;
  `eps_sh = 900 k sqrt(30/fcm) 1e-6 (1 - 1.18 h^4) sqrt((t - ts)/(t - ts
  + 0.12 (V/S)^2))`; `fcm(t) = exp(s(1 - sqrt(28/t))) fcm28`, `E(t) = 3500
  + 4300 sqrt(fcm(t))` (as a ratio to E28); `J = 1/E(t0) + phi28/E28`.
* **user**: linear interpolation of the tables (clamped);
  `J = (1 + phi(t - t0))/E(t0)`.
* **relaxation**: EN 1992-1-1 3.28-3.30 (`c rho1000 e^(k mu) (t/1000
  h)^(0.75(1 - mu)) 1e-5`, mu = sigma_pi/fpk) or Magura `log10(24 t)/K
  (sigma_pi/fpy - 0.55)`.

### Step-by-step staged construction (`skyframe.engine.staged_creep`)

`StagedCase.time_dependent` gains `method: "aaem" (default, unchanged) |
"step"`, `steps_per_stage` (int 1..50, default 4) and `output_times`
(days); `t_eval` = end time (default: end of the last stage; must not be
earlier).  `method: "step"` needs a user stage list (`per_story` is
refused).  Creeping members: materials with time-dependent properties;
without any, every material (legacy) with the staged case's own ACI 209
curves (`creep_coeff`, `shrinkage`, `aging`); an explicit `materials`
list adds those with the case curves.

Method (Bazant step-by-step superposition): stage operations act at
`T_s` with `E = 1/J(T_s, T_s)`; the stage duration (then `t_eval -
T_end`) is cut into doubling creep steps plus every output time; step i
solves the active structure with `E'' = 1/J(t_i, tau_i)` (tau_i the
mid-step time) and the imposed stress-free strains `sum_j dsigma_j [J(t_i,
tau_j) - J(t_{i-1}, tau_j)] - d eps_sh` for N/A, M3/I33 and M2/I22
(segment-end values, piecewise linear; exact fixed-end vectors, member
results corrected like temperature loads).  Released members creep axially
only.  Shells: ageing modulus only.  Element tendons are stressed by the
first `load` op of their pattern once all hosts are active, stay as bonded
stiffness afterwards and lose `Ap sigma_pi d rho` per step to relaxation.
`include_live` is elastic (E28).

```
results["staged"][name] += {
  "shortening": {story: {"uz_elastic", "uz_time_dependent", "delta"}},
  "time_history": {"method": "step", "times": [...], "stage": [...],
     "story_uz": {story: [mean column-top uz]},
     "tendon_force": {uid: [mean element force]},
     "outputs": [{"t", "node_disp", "member_forces", "base", "tendon_force"}],
     "tendons": {uid: {"P", "sigma_pi", "t_stress"}}}}
```

### Tendon elements (`skyframe.core/engine.tendon_elements`)

`tendons[k].model_as = "elements"`: each sub-piece (`n_sub` per segment,
split at host changes) is a bonded elasticBeamColumn (A = area, E =
tendon E, I = A²/4π) between host nodes with rigid `-jntOffset`s to the
chord end points; frame hosts are split by the mesher under every piece
boundary (`interior_points`), shell hosts use the nearest region node.
The prestress is an initial stress: ±P_e·t_e at the chord ends (P_e = the
load model's piece force after friction / wobble / anchor set / long-term)
whenever the tendon pattern is applied.  Element force in a static case =
`scale P_e + (EA/L) dL` (elastic shortening and live-load changes come out
of the analysis).  `results["tendons"][uid]["element"] = {"n", "points",
"length", "P_initial", "stiffness", "cases": {case: {"P", "P_initial",
"loss", "P_mean"}}}`.  `losses.es_factor` (loads model, frame hosts):
`dP = K_es (Ep/Ec) P (1/A + e2²/I33 + e3²/I22) Ap` per piece.

### API (`skyframe.api.timedep_api`)

* `POST /api/timedep/curves {props, material?, E?, fc?, t0?, times?,
  model?}` → `{t0, times, creep, compliance, shrinkage, modulus_ratio,
  strength_ratio}`;
* `POST /api/timedep/tendon_losses {tendon | uid, model?}` → `{P0, length,
  s, P, anchor_set_length, model_as, elements?}`;
* `POST /api/timedep/relaxation {relaxation, sigma_pi, fpk?, fpy?, times?}`
  → `{times, ratio}`.

Model tables: `material_time_dependent` ("Material Properties - Time
Dependent") and the `tendons` columns `model_as`, `es_factor`.

### UI

Define › Section Properties › Material Time-Dependent Properties…
(`def-td-matprops`; also Material Property Data › Time Dependent…) with
tabs Creep & Shrinkage / User Curves / Relaxation / Preview; the staged
case card's Creep integration select + Time Options… (steps, output times,
end time; durations stay in Stage Data); Tendon Properties › Model tendon
as / ES loss factor and the Tendon Forces element card; Display ›
Nonlinear Results › Staged Time History (Creep)… (`dis-td-history`,
shortening and tendon force vs time).

### Validation (`tests/test_timedep_creep.py`, 35 tests)

* MC2010 / ACI 209 / GL2000 creep, shrinkage and ageing at given t, t0,
  h0, RH from the code equations (1e-12); user curves; EN 1992 / Magura
  relaxation.
* Column under constant load: `eps = sigma/E(t0) (1 + phi)` exactly for
  1, 3, 8 steps and every intermediate time; code-law compliance to 1e-11;
  free shrinkage; cantilever curvature creep (1 + phi) in both axes.
* Two-stage column (MC2010, ageing, shrinkage): superposed loading-age
  compliances to 1e-10.  Composite column: ACI 209 converged step solution
  = AAEM chi = 0.8 to 0.1 %; saturated non-ageing creep = chi = 1.
* Bonded straight tendon: P_eff from the section equations to 1e-9, camber
  `P_eff e L²/(8 EI)` to 1e-9, ES loss = (Ep/Ec) f_cgp Ap; the equivalent
  load model with P_eff gives the same camber.  Friction / anchor set
  (exponential closed form), ES factor, relaxation loss `-dP_r/(1 + Ep Ap
  f)`, creep loss bounds.  Shell-hosted element tendon: zero net
  reactions, camber 5 P_eff e L²/(48 EI) within 5 %.
* Model tables / HTTP endpoints round-trip; legacy models hash-identical
  (results + to_dict) before / after.

## Auto hinges from code tables, V3 / T hinges, parametric interacting hinges and wall fiber hinges (Wave B)

Modules: `design/hinge_tables.py` (table generators), `core/interaction_hinges.py`
+ `engine/interaction_hinges.py` (PMM / M2M3), `engine/auto_hinges.py` (gravity
axial load + auto -> B10 plan entries), `core/wall_hinges.py` +
`engine/wall_hinges.py`, `core/modeltables_hinges.py` (DB-edit),
`api/hinges_api.py`.  Hooks: `_build` (one `auto_hinges.prepare` line before the
wipe; `wall_hinges` plan / ghost-quad / build lines in the shell loop),
`model.py` (`wall_hinges` field, to/from dict, validate), `server.py` (register),
`modeltables_ext.extend_catalogue` (one line).  Every default is byte-identical:
`wall_hinges` is emitted only when non-empty, and models without auto / PMM /
wall hinges never reach the new code (representative model + full `run()` JSON
hashed before / after: identical).

### Model JSON

```jsonc
"hinge_properties": {
  "AC": {"type": "auto",
         "auto": {"code": "ASCE41-17" | "ASCE41-23" | "FEMA356" | "EC8-3",
                  "component": "steel_beam" | "steel_column" | "steel_brace"
                             | "rc_beam" | "rc_column" | "rc_column_shear",
                  "dof": "M3" | "M2" | "PMM" | "P" | "V2" | "V3",
                  "params": {...},          // PARAM_DEFAULTS keys only
                  "gravity": null | {pattern: factor},
                  "P": null | kN},          // fixed P_G (compression +)
         "hysteresis": "kinematic", "k_elastic": null, "hysteresis_params": null},
  "PM": {"type": "PMM" | "M2M3",
         "surface": {"curves": [{"angle": deg, "points": [[P, M], ...]}]},
         "axial": {"source": "gravity" | "value", "value": P, "gravity": null},  // PMM; M2M3: null
         "backbone": [[0,0],[0,1.0],[0.03,1.1]],   // M/My vs plastic rotation
         "scale": {...}, "acceptance": {...}, "hysteresis": "kinematic",
         "drop_strength": "holds", "k_elastic": null, "hysteresis_params": null},
  "V3H": {"type": "V3" | "T", ...B10 lumped property...}
},
"wall_hinges": {"P1": {"rho_v": 0.0025, "rho_b": 0.0, "boundary_ratio": 0.1,
                       "fy": 420000, "fc": null, "n_fibers": 20, "lp_ratio": 0.5,
                       "confined": false, "stories": null}}
```

Assignment uses the existing `FrameMember.hinges` list (`{property,
relative_distance}`).  Validation errors (400): unknown keys, a code without
that component / dof, params out of range (ratios < 0.1, cover < 0.5),
interacting hinges with non-kinematic hysteresis, `drop_strength: "drops"`, an
asymmetric backbone, < 2 curves, a missing 0/180 or 90/270 curve, repeated P,
M2M3 curves that are not a single `[0, M]`, axial on M2M3; wall hinges with
unknown keys / stories, ratios out of range, `n_fibers` outside [4, 200].

### V3 / T

Lumped B10 hinges on local DOF 3 / 4: `k_member` = 12 E I22 / L^3 (V3), G J / L (T).

### Auto hinges (engine)

* `prepare` (start of every HINGED build): for members with an auto property
  whose component uses the axial load (steel / RC column, RC shear) or a PMM
  property with `axial.source == "gravity"`, the gravity mapping (the
  property's own `gravity`, else the hinged case's `gravity`, else none ->
  P_G = 0) is solved LINEARLY on a deep copy of the model (one extra static
  case) and the station axial forces are cached; P_G at the hinge station is
  interpolated (compression +).  This is the ETABS "axial force from
  case/combo" option; the axial force is NOT updated during the push.
* each auto hinge resolves per member into B10 lumped hinges (`PMM` = an M2
  and an M3 hinge in series at the same station, each sized at P_G); backbone
  plastic points A(0,0) B(0,F_B) C(a, F_C) D(a, cF_B) E(b, cF_B), then "drops";
  F_C = F_B + 0.03 k_member a (ASCE 41 9.4.2.2 hardening; none for a brace in
  compression or a force-controlled shear hinge; a flat plateau gets a 1e-6
  relative rise because OpenSees HystereticSM aborts on a flat first segment);
  acceptance = the table IO / LS / CP (absolute plastic).  Two-sided (brace)
  backbones use takeda when kinematic is chosen; an end-j axial hinge swaps
  sides so tension stays positive; states use the side's acceptance
  (`acceptance_negative`).
* a generator that cannot size the member (no library W shape, no rectangular
  dims, force-controlled column) warns and leaves the member hinge-free.

Tables (`TABLE_INFO`; `preliminary` = not `verified`):

| code / component | rule | verified |
|---|---|---|
| ASCE41-17 steel_beam | Table 9-7.1 beam ladder (= v0.19 auto_m3) | yes |
| ASCE41-17 steel_column | Table 9-7.1 columns: a = 0.8(1-r)^2.2/(0.1L/ry+0.8h/tw) - 0.0035, b = 7.4(1-r)^2.3/(0.5L/ry+2.9h/tw) - 0.006, c = 0.9-0.9r; M_pCE = ZFye(1-r/2) (r<0.2), 9/8 ZFye(1-r); IO 0.5a, LS 0.75b, CP b; r>0.6 or a=0 force-controlled | no |
| ASCE41-17 steel_brace | P_CE AISC 360-16 E3 with Fye, T_CE = A Fye; Delta_c / Delta_T multiples per shape class, stocky 2.1 rt / slender 4.2 rt interpolation | no (strength exact) |
| ASCE41-17 rc_beam | Table 10-7 condition i (= v0.19) | yes |
| ASCE41-17 rc_column | Table 10-8: a = 0.042-0.043n+0.63rho_t-0.023VyE/VColOE, b = 0.5/(5+n/0.8/rho_t fc/fyt)-0.01 >= a (n >= 0.1 in b), c = 0.24-0.4n; IO = min(0.15a, 0.005), LS 0.5b, CP 0.7b; Mn by ACI strain compatibility at P_G; EI_eff Table 10-5 (info) | yes |
| ASCE41-17 rc_column_shear | Eq. 10-3 V_ColOE (k_nl = 1, d = 0.8h, M/Vd in [2,4], alpha_col by s/d); force-controlled: plateau 1e-3 L, c = 0.2, IO = LS = CP = 0 | yes |
| FEMA356 steel_beam / steel_column / rc_beam | Table 5-6 (primary; Eqs. 5-2, 5-4; P/PCL 0.2..0.5 footnote rows; > 0.5 force-controlled) / Table 6-7 | no |
| EC8-3 rc_beam / rc_column | Annex A: theta_y (A.10b) with phi_y, My from (A.10)-(A.12); theta_um (A.1) with gamma_el; DL / SD / NC -> IO 0 / LS 0.75 theta_um - theta_y / CP theta_um - theta_y; drops after theta_um | yes |
| ASCE41-23 (all) | the 41-17 values unchanged (41-23 numeric revisions not transcribed) | no |

### Parametric interacting hinges (PMM / M2M3)

Two elements join the hinge station to the duplicate node:

* a `zeroLengthSection` with an OpenSees `Elliptical` section (codes My Mz =
  member M2 / M3; k_e per axis = `k_elastic` or 10 x 6EI/L; yield ellipse
  (M2/M2y)^2 + (M3/M3y)^2 = 1; linear kinematic hardening H = (r_C - 1) My /
  d_C from the backbone B -> C, floored at 1e-9 k_member);
* a stiff `zeroLength` on P, V2, V3, T.

M2y / M3y are the means of the 0/180 and 90/270 curve moments, interpolated at
the hinge's axial force (gravity or fixed; ETABS sign, compression negative).
The other angles are reported against the ellipse (`surface_fit`).  If P lies
outside the surface, the hinge is rigid and a warning is issued.  Points past C
are state markers only, because OpenSees has no degrading coupled-plasticity
section.  States use the normalized resultant plastic rotation.

### Wall fiber hinges

Hinged builds only.  Each planar rectangular (4-corner, no openings) shell wall
of the pier keeps its quads, but with a ghost section (1e-4 E).  Per story it
gets a `forceBeamColumn` between centre nodes at the story levels:

* HingeRadau, with the fiber section at both ends;
* lp = lp_ratio lw, capped at H/2;
* an elastic gross interior;
* both sections aggregated with an elastic in-plane shear G (5/6) t lw.

Stiff elastic arms (100 x) join each centre node to every wall node on that
level line.

Fibers: Concrete01 (fc, eps0 = 2fc/Ec, 0.2fc at 0.006, no tension) over n x 2
cells.  Steel01 bars (fy, 200 GPa, b = 0.01): the distributed rho_v t lw over n
bars, plus rho_b t lb at each boundary centre.  Stories outside `stories` get an
elastic wide column.

States: theta_pl = max(0, |kappa| - kappa_y) lp, with kappa_y = 2 eps_y / lw
(Priestley).  They are classified against ASCE 41-17 Table 10-19, interpolated
in the axial ratio (gravity, step 0) and the peak shear stress (preliminary).
Loads on interior wall nodes act on the ghost shell.

### Results

Entries are appended to the pushover / nonlinear static / TH hinge lists:

* **auto:** the B10 entry, plus `kind: "auto"`, `auto: {code, component,
  table, clause, preliminary, inputs, P_G, axial_source, thy, dof}`,
  `acceptance_negative` and `preliminary`.
* **PMM / M2M3:** `kind: "user_interaction"`, `My2`, `My3`, `P`,
  `axial_source`, `surface_fit`, `k_elastic: [k2, k3]`, `hardening`, `rot2`,
  `rot3`, `m2` and `m3` (`rot` / `moment` are resultants).
* **wall:** `uid: "<pier>@<story>"`, `kind: "wall"`, `type: "P-M3"`, `pier`,
  `story`, `region`, `lp`, `kappa_y`, `My` (hand M at kappa_y), `a b c IO LS
  CP` (Table 10-19), `axial`, `curvature` and `shear`.

### API

* `GET /api/hinges/catalog` -> `{codes: {code: {component: {dofs, table,
  clause, verified, note}}}, params, brace_shapes, hinge_types,
  interaction_types, wall_defaults}`.
* `POST /api/hinges/auto-preview` `{property, members, P_G?, model?}` ->
  `{previews: [{uid, P_G, reason, hinges: [{type, property,
  acceptance_negative, thy, meta}]}]}`; 400 on a bad property / unknown uid.
* `POST /api/hinges/surface-preview` `{property, P?}` -> `{M2y, M3y, P, fit}`.

DB-edit changes:

* "Frame Hinge Properties" gains `auto_code / auto_component / auto_dof /
  auto_params (JSON)`.
* New tables: "Frame Hinge Properties - Interaction Surface" (hinge, angle, P,
  M) and "Wall Hinges (Fiber P-M3)".
* The backbone table no longer writes an empty backbone into a property that
  has none.

### UI

* Define > Frame Hinge Properties offers these types:
  * V3 and T, with the backbone editor;
  * P-M2-M3 / M2-M3: surface table, M2-M3 contour with the fitted ellipse at a
    chosen P, normalized M-theta, acceptance and axial source;
  * Auto: code / component / DOF, table + verified flag, parameters and
    preview.
* Assign > Frame > Hinges > Auto Hinges (Code Tables)… (`asn-hb-auto`).
* Assign > Shell > Wall Hinges (Fiber P-M3)… (`asn-hb-wall`).
* Hinge Results show an info line for each new kind.
* Mock: `js/mock_hingeb.js` (approximate generator, `meta.mock`).

### Validation (`tests/test_wave_b_hinges.py`, 24 tests)

* **Table pins** (each formula re-derived in the test):
  * steel beam = legacy auto_m3;
  * steel column equations at r = 0 / 0.1 / 0.3, and the r > 0.6 cut;
  * brace E3 inelastic branch (KL/r 69) plus the interpolated multiples;
  * RC Mn closed form As fy (d - a/2);
  * Table 10-8 + Eq. 10-3, and the shear hinge;
  * FEMA 356 caps;
  * EC8-3 theta_y / theta_um.
* **Auto P-M column:** W14x90 cantilever with an auto P-M hinge at P_G =
  500 kN.  V L = envelope(theta) at every step (1e-6), and first yield is
  bracketed.
* **Interaction surface:** diamond surface at P = -900 kN.
  * Yield V = 0.7 M0 / L about both axes.
  * Elastic and hardening slopes exact (1e-6).
  * Ellipse fit matches its closed form.
  * A 45-degree material path yields on the ellipse.
* **Brace:** the hinge (mid and end j) caps compression at P_CE (2e-6).
* **V3 / T:** caps and elastic series stiffnesses (1e-6).
* **Wall fiber section vs hand strain-compatibility M-phi:** within 0.5 %.
  * P = 0: compared from 0.05 phi_y.
  * P = 1500 kN: compared from 0.25 phi_y.  Below that, Concrete01 unloading
    of the pre-compressed fibers departs from the monotonic hand envelope
    (measured 1.2 % at 0.01 phi_y).
  * The hand routine is pinned to the parabola closed form (1e-5).
* **Wall pushover:** base moment = V H (0.5 %); linear analyses are unchanged
  by wall hinges.
* **Plumbing:** round trip, DB-edit round trip + edits, API, and byte identity
  with an unused property.
* **Playwright:** mock 35/35, live 39/39.

## Orthotropic materials, slab / deck property types, cold-formed and composite sections, named line / area springs

Wave B property parity. Every new key below is emitted by `to_dict` only when it
is set, so a model without them saves byte-identically (proved by hashing the
model JSON and the full results JSON of a frame + shell + membrane one-way +
line-spring model before and after the change: identical).
Code: `core/ortho.py`, `core/slab_props.py`, `core/coldformed.py`,
`core/composite_col.py`, `core/spring_dist.py`, `core/props_ext.py` (hub),
`core/props_tables.py` (DB-edit), `engine/slab_sections.py`. Hooks: one line
each in `core/model.py`, `core/mesh.py`, `core/shape_props.py`,
`core/springprops.py`, `core/modeltables.py`, `engine/shell_elements.py`,
`engine/opensees_engine.py` (two section calls pass `model.materials`; the
line / area spring loops skip named springs and append their specs).

### Orthotropic materials

```
materials[name] = {..., "symmetry": "orthotropic",
                   "E": E1, "nu": nu12,
                   "ortho": {"E2", "G12", "G13", "G23"}}      # kPa
```

* `MATERIAL_SYMMETRY` is unchanged; validation accepts
  `MATERIAL_SYMMETRY_EXT = ("isotropic", "uniaxial", "orthotropic")`.
  Anisotropic stays out of scope (OpenSees `ElasticOrthotropic` has no
  normal–shear coupling).
* Rules: `ortho` needs symmetry orthotropic and vice versa. Every constant is
  > 0. `nu12 >= 0` and `nu12² E2 / E1 < 1`.
* Material axes 1 / 2 are the **shell element local axes** (there is no
  material angle). Frames made of the material use E = E1 and G = G12
  (`Material.G`).
* Shells: `A = h Q`, `D = h³/12 Q`, `S13 = 5/6 G13 h`, `S23 = 5/6 G23 h`, with
  `Q11 = E1/(1−ν12ν21)`, `Q22 = E2/(1−ν12ν21)`, `Q12 = ν12 E2/(1−ν12ν21)`,
  `Q66 = G12`. These are built exactly as the three-layer
  `ElasticOrthotropic` + `PlateFiber` `LayeredShell`
  (`modifiers.skin_core_layers`). ETABS modifiers, `mod`, the cracked-slab
  scale and the shell-type factors multiply the resultants
  (`engine/slab_sections.py`).
* Presets (`ortho.TIMBER_PRESETS`): EN 338 C24, EN 14080 GL24h (mean values).

### Laminated (CLT) shell sections

```
shell_sections[name].laminate = {"layers": [{"t": m, "material": name,
                                             "angle": 0 | 90}, ...]}
```

* Layers are listed from the bottom up. Classical lamination theory gives
  `A = Σ t Q`, `B = Σ t z̄ Q`, `D = Σ (t³/12 + t z̄²) Q`, and
  `S13 = 5/6 Σ t G13` (likewise `S23`). A 90° layer swaps 1↔2 and G13↔G23.
* An unsymmetric lay-up (B ≠ 0) is rejected.
* `laminate` and `layered` are exclusive.
* `thickness` is the weight-equivalent thickness of the section material:
  `Σ t γ_k / γ_ref`.
* `ortho.CLT_LAYUPS` lists the CLT 100 / 120 / 160 / 200 lay-ups.

### Slab property types

```
shell_sections[name].slab = {"type": "ribbed" | "waffle" | "deck_filled" |
                                     "deck_unfilled" | "deck_solid" | "drop",
                             ...geometry (m), deck_unit_weight (kPa),
                             rib_dir 1 | 2}
```

| type | keys |
|---|---|
| ribbed | overall_depth, slab_thickness, stem_width_top, stem_width_bottom, rib_spacing [, rib_dir] |
| waffle | overall_depth, slab_thickness, stem_width_top, stem_width_bottom, rib_spacing_1 (along local 1), rib_spacing_2 |
| deck_filled | slab_depth, rib_depth, rib_width_top, rib_width_bottom, rib_spacing, deck_thickness, deck_unit_weight [, rib_dir] |
| deck_unfilled | rib_depth, rib_width_top, rib_width_bottom, rib_spacing, deck_thickness, deck_unit_weight [, rib_dir] |
| deck_solid | slab_depth [, rib_dir] |
| drop | — (plain thickness) |

* **Equivalent plate** (per unit width, ribs along local `rib_dir`, section
  material isotropic E, ν, G):
  * Ribbed: `A = hf Q + E Ar/s e₁e₁`, `D11 = E I_T/s` (the T of one pitch
    about its own centroid, exact trapezoidal stem), `D22 = E hf³/12(1−ν²)`,
    `D12 = ν D22`, `D66 = G hf³/12`, `S13 = 5/6 G (hf + Ar/s)`,
    `S23 = 5/6 G hf`.
  * Waffle: the same rule in both directions (ribs ∥ 1 spaced at
    rib_spacing_2).
  * Filled deck: the ribbed rule with the concrete above and in the flutes.
    The steel sheet is not counted.
  * Unfilled deck (section material = the deck steel): `A11 = E td Ld/sr`,
    `A22 = E td³/(6 hr²)`, `A66 = G td`, `D11 = E I_deck/sr`,
    `D22 = E td³/12`, `D66 = G td³/12`, `S = 5/6 G td`.
  * Solid deck and drop panel: a plain isotropic slab.
  * Each direction is taken about its own centroid: there is no eccentric-rib
    coupling.
* **Self-weight and mass:** `thickness` is the weight-equivalent thickness:
  * ribbed: `hf + Ar/s`;
  * waffle: `hf + Ar/s1 + Ar/s2 − w̄² hr/(s1 s2)`;
  * filled deck: `tc + Ar/sr + wd/γc`;
  * unfilled deck: `wd/γs`;
  * solid deck: `tc`.

  It must match the geometry (to 1e-6, validation). `from_dict` rewrites it
  only when it differs by more than 1e-9 relative, so an exact client value
  is kept bit for bit.
* Stress recovery uses the weight-equivalent thickness, which is an
  approximation for ribbed and deck sections.

### One-way distribution as a section property

* `shell_sections[name].distribution` (`two_way` | `one_way`) and
  `one_way_dir` (1 | 2). `None` means unset, and the key is omitted.
* Every membrane slab using the section inherits these values unless it has
  its own. A slab has its own value when its `distribution` / `one_way_dir`
  differ from the defaults, or when `shells[i].distribution_override` is
  `true` (emitted only when true). That own value is the per-slab override.
* An inherited one-way on a slab that cannot take it (polygon, non-convex or
  shell behaviour) falls back to two-way with a warning. An explicit per-slab
  one-way is validated as before.
* Legacy files (no section key) behave exactly as before.

### Drop panels

* A slab region whose section has `slab.type == "drop"` is a
  thickness-override region:
  * It must have shell behaviour, a convex outline and no openings, and it
    must overlap a coplanar shell-behaviour slab.
  * It is not meshed. Every host element whose centroid lies inside the
    outline is handed to the drop region (`ShellQuad.region` and the
    tributary areas), so it takes the drop section's stiffness, self-weight,
    mass and area loads. The boundary snaps to the host mesh, like an
    opening.
* `net_area`:
  * of a host excludes the exact geometric overlap;
  * of the drop region is that overlap.

  Region-level weight and mass totals therefore count the drop zone once.
  These values are stamped by `slab_props.assign_drop_overlaps`, which is
  called from `validate`, `compute_story_masses` and `mesh_model`.
* Area loads on a host do not reach the overridden elements: assign the loads
  to the drop region too.

### Cold-formed shapes

* `SHAPE_TYPES` gains `C_lipped` (d, b, dl, t [, r]), `Z_lipped` (same keys)
  and `hat` (d, b, bf, t [, r]). All dimensions are out-to-out; `r` is the
  inside bend radius.
* Method: AISI linear method with rounded corners (centre line × t):
  * exact line integrals, plus the t³/12 self terms;
  * `J = t³L/3`;
  * shear centre and Cw from the thin-walled sectorial coordinate;
  * S from the extreme fibre;
  * Z from the equal-area axis.
* Shear areas:
  * C / Z: `As2 = d t`, `As3 = 5/3 b t`;
  * hat: `As2 = 2 d t`, `As3 = 5/6 (b + 2bf) t`.
* `torsion = "exact"` meshes the true outline with `section_fem`.
* Z sections return their principal axes.
* `js/cfshapes.js` is the bit-for-bit JS port.

### Composite columns (SRC / CFT)

* A FrameSection with `material` = the steel and `shape` one of:
  * `SRC_I`: b, h, d, bf, tf, tw [, bar_d, n_bars_3, n_bars_2, cover];
  * `CFT_rect`: d, b, t;
  * `CFT_round`: D, t.

  `dims` also hold `"concrete"`, optionally `"rebar"`, and `"code"`
  (`AISC360` | `EC4`).
* `A`, `I33`, `I22` are transformed to E_s:
  * `EI_eff` per AISC 360-16 I2.1b: `Es Is + 0.5 Es Isr + C1 Ec Ic`,
    `C1 = 0.25 + 3(As + Asr)/Ag ≤ 0.7`;
  * per I2.2b: `Es Is + Es Isr + C3 Ec Ic`, `C3 = 0.45 + 3(As + Asr)/Ag ≤ 0.9`;
  * per EN 1994-1-1 6.7.3.3: `Ke = 0.6`.
* `J = Js + (Gc/Gs) Jc`.
* `mod_weight` and `mod_mass` restore the real steel + concrete weight and
  mass.
* Built by `composite_col.composite_section(model, name, type, dims, steel)`
  or `js/wb_composite.js`. The two agree to round-off; the stored numbers are
  authoritative.

### Named line / area spring properties

```
line_spring_properties / area_spring_properties = {name: {
    "kind": "linear" | "compression_only" | "tension_only" | "multilinear",
    "k": [k1, k2, k3],            # line kN/m/m, area kN/m/m²
    "nonlinear_dof": "U3",        # c/t-only
    "curves": {"U3": [[d, f], ...]}}}   # f per m / per m²
line_springs[i].property = name        # inline kx / ky / kz = 0
shells[i].area_spring = {"property": name}    # shell behaviour
```

* U1, U2, U3 are global X, Y, Z.
* Each FE node gets a grounded B9 named spring:
  * the law is scaled by its tributary length or area (`k·trib`, curve
    forces `f·trib`);
  * the tributary rules are those of v0.22.
* Nonlinear kinds route static cases to Newton (`springprops.any_nonlinear`).
  Eigen analyses use the initial tangent.
* Reactions come from the exact force law.

### Database editing (`core/props_tables.py`)

* Columns:
  * `materials`: `ortho_E2`, `ortho_G12`, `ortho_G13`, `ortho_G23`, plus the
    symmetry enum + orthotropic;
  * `shell_sections`: `slab_type` (read-only), `sec_distribution`,
    `sec_one_way_dir`;
  * `shell_objects`: `distribution_override`;
  * `line_springs`: `property`.
* Tables: "Spring Properties - Line" and "Spring Properties - Area".
* Untouched rows → apply is the identity.

### UI (dlgkit standard)

* Material Property Data: Directional Symmetry → Orthotropic, with the
  E1 / E2 / ν12 / G12 / G13 / G23 group and the C24 / GL24h presets
  (`js/wb_ortho.js`).
* Slab / Wall Property Data:
  * Property Type: plain, ribbed, waffle, drop, filled / unfilled / solid
    deck, CLT laminate;
  * geometry fields and a read-only weight thickness;
  * the load distribution is now the section property;
  * secpreview draws the pitch cross-section or the CLT layer stack
    (`js/wb_slab.js`).
* Frame Section Property Data: Cold-Formed shapes in the Shape list, with the
  outline drawing (`js/cfshapes.js`).
* Frame Properties → Add Composite Column… and Define › Section Properties ›
  Composite Column Sections… [`def-wb-composite`] open the SRC / CFT dialog,
  which has a preview (`js/wb_composite.js`).
* Define › Section Properties › Line / Area Spring Properties…
  [`def-wb-springprops`] and Assign › Shell › Line / Area Spring Property…
  [`asn-wb-springprops`] open the line / area spring dialogs, which include a
  multilinear backbone plot (`js/wb_springs.js`).
* Assign › Shell load distribution sets `distribution_override` when the
  section has its own distribution.
* `js/mock_wb.js` mirrors the validation in `?mock=1`.

### Validation (`tests/test_wave_b_props.py`, 27 tests)

* SS orthotropic plate 4 × 3 × 0.06 m against the Navier / Lekhnitskii series:
  * E2 = E1/3: +0.02 %;
  * E2 = E1: −0.09 %;
  * CLT 120 L3s plate: +0.14 %.
* An isotropic material entered as orthotropic reproduces the legacy element
  to 1e-6.
* CLT A / D / S by hand.
* Ribbed I_T = 1.5561667e-3 m⁴ by hand. Waffle weight thickness 0.15344 m.
* Deck by hand:
  * filled: A11 = 2.234375e6 kN/m, D11 = 1968.03 kN·m, weight thickness
    0.090833 m;
  * unfilled: Ld, I_deck, A22, A66.
* Deck strip in cylindrical bending vs 5qL⁴/384(D11 − D12²/D22): −0.27 %.
* Drop panel:
  * self-weight 192 kN exact;
  * net areas 32 / 4 m²;
  * 16 overridden elements;
  * story mass 192/g.
* Lipped C 9 × 2.5 × 0.773 × 0.059 in, R = 0.0885 in:
  * A equals the AISI Manual Part I linear-method formula exactly;
  * Ix within 0.05 % (the Manual's rounded constants);
  * the finite-element outline matches on A / J / x0;
  * the r = 0 limit gives the DG9 channel e and Cw.
* CFT 400 × 400 × 12: C3 = 0.7992, EI_eff = 1.33481e5 kN·m² (AISC I2.2b by
  hand); EC4 Ke; SRC C1 by hand; cantilever PL³/3EI_eff to 1e-6.
* Multilinear area spring: q = 60 kPa on the 0.01 m → 50 kPa, 0.03 m → 80 kPa
  backbone settles 0.016667 m uniformly (base FZ = qA). Below the knee the
  initial slope applies.
* Named linear area / line springs equal the inline ones.
* One-way inherited equals per-slab (80 kN per X beam). The override keeps
  two-way.
* Legacy bytes; DB-edit round trip and edits.

## Model templates, general grids, shell editing and drawing aids

Interface and modelling productivity tools (Wave B). No existing analysis
path changes: every default reproduces today's model JSON and results
byte-identically (sha256 of `to_dict()` and of the full results JSON of
representative frame and shell + RS models, base 224df75 vs. this change).

### Model templates (`skyframe/core/templates.py`, `skyframe/api/wb_iface_api.py`)

* `GET /api/model/templates` -> `[{id, label, params: {key: default}}]`.
* `POST /api/model/template` `{template, params?, dry_run?}` ->
  `{model, summary}`; the stored model is replaced unless `dry_run` (ETABS
  frame-axis convention, like `/api/model/quick`). 400 on an unknown
  template / parameter or an out-of-range value.
* Templates `waffle`, `ribbed`, `two_way_beams`, `staggered_truss`,
  `flat_slab_drop`. Common params `name, bays_x, bays_y, bay_x, bay_y,
  stories, story_height, first_story_height, sdl, live, live_mass_factor,
  mesh_size, code (asce7|ec8|is1893|none), SDS, SD1, R, Ie, ag, q,
  ground_type, Z, soil`; per template `column, beam_b, beam_h, slab, depth,
  top, rib_width, rib_spacing, drop_thickness, drop_width, panels`.
* Every template sets `DEAD` (self weight 1 + SDL area loads), `LIVE`, the
  mass source `{DEAD: 1, LIVE: live_mass_factor}` and, for a code, `EQX/EQY`
  (`autolateral.generate`), function `RSF`
  (`spectra_codes.make_spectrum_function`), RS cases `RSX/RSY` and the code
  combinations (ASCE 7 LRFD / EN 1990 / IS 456).
* Waffle / ribbed: one equivalent shell per bay (thickness = overall depth
  D), T-section closed form (flange s x tf, web bw x (D - tf)): rib
  direction f = A_T/(s D), m = I_T/(s D^3/12), v = bw/s; other direction
  (ribbed) f = tf/D, m = (tf/D)^3; f12 = tf/D; m12 = sqrt(m11 m22)
  (Huber); weight = mass = V/D.
* Drop panels: the slab is tessellated by the grid lines and the panel
  edges (default width min(bay)/3), so all regions share full edges.
* Staggered truss: perimeter columns on the two long faces; story-deep
  Warren trusses with verticals on grid line i in story s >= 1 when s + i is
  odd; plain transverse beams where a level has no chord; one-way membrane
  decks between transverse lines.

### General grids (`skyframe/core/grid_general.py`)

`GridSystem.kind == "general"` with `lines: [{label, p1: [x, y], p2: [x,
y]}]` (local frame; origin / rotation apply). `to_dict` emits `lines` for
general grids only. Snap points: end points (`"L1.1"`, `"L1.2"`) and
crossings (`"L1-L2"`, exact parametric segment solve). `POST /api/grid`
accepts `kind: "general"` + `lines`. At least one line; zero-length lines
and duplicate labels are rejected.

### Workspace keys (`skyframe/core/workspace.py`)

Top-level model keys, each emitted only when non-empty, no analysis effect:
`named_views [{name, view: plan|elev|3d, story?, elev_line?, camera,
display?}]`, `report_templates [{name, tables, figures, cases, options?}]`,
`table_sets [{name, tables}]`, `dimension_lines [{p1, p2, offset, story?,
label?}]`, `reference_points [{name, point}]`, `reference_planes [{name,
axis: x|y|z, coord}]`. Names are unique per key.

### Edit ops (`skyframe/core/edit_shells.py`, `POST /api/edit/<op>`)

Registered into `core.edit` (`ALL_EDIT_OPS`) with the same `{selection,
params, model?, dry_run?}` -> `{model, summary}` shape and undo semantics:

* `shell_divide` `{mode: "n", n1, n2}` | `{mode: "size", max_size}` ->
  `divided: {uid: [pieces]}` (the first piece keeps the uid);
* `shell_merge` (>= 2 coplanar shells, same kind / behavior / section,
  identical area loads, one region without holes) -> `merged`;
* `shell_chop` `{mode: grid|lines|frames|levels, lines?, z?}` -> `chopped`
  (convex shells; concave ones are skipped with a warning);
* `shell_expand` `{distance, edge?}` -> `expanded: {uid: {area_before,
  area_after}}`;
* `shell_reshape` `{mode: corners|move|insert|delete, ...}` -> `area`;
* `ramp` `{p1, p2, width, story_from, story_to, section?, behavior?,
  mesh_size?}` -> `slope, length, area`; story = the upper story; only the
  two level edges join the rigid diaphragms (the engine slaves nodes on a
  story level only);
* `wall_stack` `{shape: straight|L|C|I|T|box, B, D, origin, rotation,
  stories, section?, pier?, mesh_size?}` -> `pier, walls`;
* `relabel` `{kinds?}` -> `renamed, count`: uid = `<label>-<story>`, label
  numbers per plan position ordered by (y, x) per prefix C/B/D/W/F/L;
  loads, groups, tendon hosts and pushover / TH `My` follow.

Divide / merge / chop / expand refuse shells with openings. Pieces conserve
the area exactly and every piece gets a copy of the parent's area loads.

### .rtf report (`skyframe/io/rtf.py`)

`POST /api/report/rtf` takes the `/api/report/docx` document and returns
`{ok, mime: "application/rtf", filename, content_b64, size}`: RTF with
heading styles + outline levels, TOC field, tables with repeated header
rows, embedded PNG (`\pngblip`), `\uN?` escapes.

### Frontend (`js/wb_*.js`, mock `js/mock_wb.js` + `js/wb_shellgeom.js`)

File › New Model from Template… (+ start-screen cards); Define › Stories,
Grids & Towers › General Grid…; View › Named Views ▸ and Windows ▸ (1–4
panes, shared selection, independent cameras); Edit › Edit Shells ▸
(divide, merge, chop, expand / shrink, vertex reshaper); Edit › Auto
Relabel All…; Draw › Quick Draw ▸, Frames › Frames in 3D View, Floors &
Walls › Ramp… / Wall Stack…, Dimensions & References ▸; Create Report
template group + Download .rtf; Show Tables table sets.

### Validation

* `tests/test_wb_iface.py` (31): template DEAD reactions equal hand sums,
  rib modifiers (D 0.40, tf 0.10, bw 0.15, s 0.90 -> f 0.375,
  m 0.3359375), drop-panel area 256/9, general-grid crossings, workspace
  round trips and 400s, exact areas for divide / chop / merge, divide ->
  merge byte-identical, expand A + L d and (a + 2d)(b + 2d), ramp
  diaphragm membership, wall-stack piers in the results, relabel leaves
  base reactions unchanged, RTF content, HTTP endpoints.
* Playwright in mock and live modes for every tool.


## Programmatic API, plugins, and SAP2000 / SAFE / steel / Revit interop

All of this is new code. The shared files get only hooks:

* `server.py`:
  * the plugin pre / post hooks in `POST /api/analyze` (2 lines);
  * three `register` lines;
  * one import.
* `ifc_write.py`: `revit` hooks.
* `js/app.js`: 2 imports and 2 init lines.
* `js/etabs.js`: the empty `Tools › Plugins` submenu node.

Legacy behaviour is byte-identical. For the quick building (4 stories,
3 × 2 bays):

* the model SHA-256 is `1960628077e1ff4a`;
* the `POST /api/analyze` SHA-256 is `8cc6df37e1d22071`;
* the `/api/tables/list` SHA-256 is `9c85e4fcb2577a36`;

which are the same before and after. The default IFC text is also
unchanged.

### Versioned REST API and OpenAPI (`skyframe/api/openapi.py`)

* **`/api/v1/<path>`.** Every `/api/<path>` is also served under
  `/api/v1/<path>`. A WSGI rewrite sets
  `environ["skyframe.api_version"] = "v1"`.
* **`GET /api/openapi.json`.** Also served as `/api/v1/openapi.json`. It
  returns an OpenAPI 3.0.3 document built from the live URL map:
  * paths are `/api/v1/...` with `{param}`; `servers: [{"url": "/"}]`;
  * `operationId = <method>_<endpoint>`;
  * the tag is the first path segment;
  * `description` is the CONTRACT.md text for that endpoint;
  * `400` / `404` responses use `#/components/schemas/Error`.
* **Component schemas.** `Error`, `SoftError`, `Health`, `Material`,
  `FrameSection`, `Story`, `FrameMember`, `ShellRegion`, `LoadPattern`,
  `Model`, `CaseResults`, `ModalResults`, `AnalysisResults`,
  `ExportResponse`, `ImportResponse`, `Plugin`.
* **Other endpoints.** Request and response schemas come from the
  `{key, key?: [..], …}` shapes in CONTRACT.md (`?` = optional; `[` →
  array, `{` → object). A response written as "model dict" or
  `results.to_dict()` maps to `Model` / `AnalysisResults`.
* **Extending.** Feature modules can publish exact shapes with
  `add_override(method, rule, request, response, summary)`.
* **Validation.** `validate_spec(spec)` checks the structure (version,
  info, unique operationIds, path parameters, dangling `$ref`). The test
  also runs `openapi_spec_validator.validate` when it is installed.
  Coverage: 133 paths and 142 operations at this commit, 114 with
  CONTRACT text and 71 with a typed 200 schema.

### OAPI facade (`skyframe/oapi/`; docs/API.md)

* **Entry points.**
  * `from skyframe.oapi import comtypes`, then
    `comtypes.client.CreateObject('ETABSv1.Helper' | 'SAP2000v1.Helper')`
    → `QueryInterface(comtypes.gen.*.cHelper)` →
    `CreateObjectProgID(...)` → `ApplicationStart()` → `.SapModel`.
  * Or directly: `skyframe.oapi.SapModel(units=6)`.
* **Conventions.** Methods return `0` (or non-zero on failure). A method
  with CSI `ByRef` arguments returns `[byrefs..., ret]`. Values are in the
  present `eUnits` 1..16. The surface table is in docs/API.md.
* **SkyFrame model.** `SapModel.to_skyframe()` builds the `BuildingModel`
  that `RunAnalysis` solves:
  * `frame_axis_convention = "etabs"`;
  * `rigid_diaphragms = False`;
  * `mass_source_mode = "element_self_mass"`;
  * `num_modes` from the modal case;
  * `supports` from the restraints;
  * frame kind from geometry (vertical = column, horizontal = beam, else
    brace);
  * story = the story at the member's top end;
  * per-story rigid diaphragms from `Diaphragm` assignments.

  `SapModel.skyframe_results` is the `AnalysisResults` of the last run.
* **Frame loads.** CSI `Dir` maps as follows:
  * 2 → `local_y`;
  * 1 / 3 → exact global components of local 1 / 3;
  * 4–6 → `global_x|y|z`;
  * 7–9 → the projected global directions;
  * 10 → `gravity`; 11 → projected `gravity`.
* **Sections.** Rectangles get the CSI shear areas `As2 = As3 = 5/6 A`.
  A zero shear-area modifier removes the shear area (`As = None`).
* **Results.**
  * `JointDispl` / `JointReact` / `FrameForce` / `BaseReact` /
    `ModalPeriod` / `ModalParticipatingMassRatios` / `StoryDrifts` use the
    CSI array order.
  * `FrameForce` uses the engine station convention, which is the CSI one
    (P tension +, gravity beam V2 −/+ at i/j, M3 sagging +).
  * Envelope combos give `Max` / `Min` rows. Modal displacements use
    `StepType = "Mode"`.
* **Validation (`tests/test_oapi_facade.py`).**
  * CSI SAP2000 "Example 2", with only its import line changed, matches
    all 7 independent results to < 0.05 % (largest 0.021 %).
  * An ETABS-style portal (`examples/oapi_etabs_portal.py`) gives exactly
    the REST `/api/analyze` numbers for `to_skyframe()`.
  * Rigid-beam portal sway equals `H / (2·12EI/h³/(1+Φ))`,
    `Φ = 12EI/(G As h²)`, to 1e-4.
  * A pinned-pinned beam under self-weight gives midspan `wL²/8`.
  * A slab under a uniform load gives `FZ = qA`.
  * Building the same model in kip-in gives the kN-m result to 1e-9.
  * `File.Save` / `OpenFile` reproduce the results.

### Plugins (`skyframe/core/plugins.py`, `skyframe/api/plugins_api.py`; docs/PLUGINS.md)

* **Manifest.** `plugin.json` keys: `id, name, version, description,
  author, skyframe (">=x.y"), python, js[], css[], hooks[pre_analysis |
  post_analysis], endpoints[], tables[{key, title, columns[{key, label,
  quantity}]}], menus[], permissions[model:read | model:write |
  results:read | network | files], enabled_by_default`.
* **Folders.** `$SKYFRAME_PLUGINS_DIR` (pathsep list), or
  `~/.skyframe/plugins` plus `<repo>/plugins`. The enabled state is in
  `$SKYFRAME_PLUGINS_STATE` (default `~/.skyframe/plugins_state.json`).
  Every plugin is disabled by default.
* **HTTP.**
  * `GET /api/plugins` → `{plugins: [Plugin], dirs, state_path}`.
  * `POST /api/plugins/rescan` → the same listing.
  * `POST /api/plugins/<id>/enable {enabled: bool}` → `Plugin` (404 for
    an unknown id, 400 for an invalid plugin).
  * `GET /api/plugins/<id>/files/<path>` → a web asset from inside the
    folder only (404 otherwise).
  * `POST /api/plugins/<id>/call/<name>` → the endpoint's JSON (404 unless
    the plugin is enabled and declares it; 400 when it raises).
* **`Plugin` shape.** `{id, name, version, description, author, dir,
  enabled, loaded, error, invalid, js, css, hooks, endpoints, tables:
  ["plugin.<id>.<key>"], menus, permissions, log}`.
* **Analysis hooks.**
  * `pre_analysis(model, ctx)` runs before the engine is built. The
    warnings it returns are re-issued through `warnings.warn`, so they
    reach the run log.
  * `post_analysis(model, results, ctx)` may return a JSON-safe dict,
    which is stored at `results["plugins"][id]`. A hook that raises gives
    `{"error": msg}`.
  * The `plugins` key exists only when an enabled plugin returned data.
* **Tables.** Registered with `core.tables.register_table` as
  `plugin.<id>.<key>`, group `"Plugins"`, served by the ordinary `POST
  /api/tables/<key>`. They are unregistered on disable, and a new
  `PluginManager` starts from no plugin tables.
* **UI (`js/plugins.js`, `js/mock_plugins.js`).**
  * Options › Plugins… (`opt-plg-manager`) and Tools › Plugins ▸ Plugin
    Manager… (`tool-plg-manager`) open dialog `#plManager`: list
    `#plList`, `#plEnable`, `#plDisable`, `#plRescan`. Changes apply on
    OK.
  * Help › Scripting API (REST / OAPI)… (`help-plg-apiref`) opens
    `#plApiRef`, the endpoint list from the spec.
  * Enabled plugins' JS modules are imported at start-up and given
    `activate(api)`. The menu items they add are removed on disable.
* **Test hook.** `__sky.plugins = {state, refresh, openManager,
  openApiRef, setEnabled, showTable, active}`.
* **Example.** `plugins/example-story-summary` adds menu
  `tool-plg-story-summary`, table `plugin.example-story-summary.story_summary`
  (`story, elevation, columns, beams, braces, max_drift`), endpoint
  `summary` → `{model, stories: [{story, elevation, columns, beams,
  braces}]}`, and post-analysis output `{stories, max_drift, governing}`.

### Interop formats (`skyframe/api/interop_api.py`; docs/INTEROP.md)

* **`GET /api/interop/formats`** → `{formats: [{fmt, label, ext, export,
  import, mime}]}`. The formats are `s2k`, `f2k`, `sdnf`, `cis2`
  (import only), `idea` (export only) and `ifc_revit` (export only).
* **`POST /api/interop/export/<fmt>`.**
  * Body: `{model?, units?: [F, L] | "m|mm|in|ft", story?,
    loads_from_above? = true, load_effects? = true, results?, include?,
    soft_errors?}`.
  * Response: `{ok: true, filename, mime, content | content_b64 (idea
    zip), report}`.
  * `f2k` and `idea` run the analysis when no `results` are given.
* **`POST /api/interop/import/<fmt>`.**
  * Body: `{text | content_b64, units?, sections_from_current? = true,
    dry_run?, soft_errors?}`.
  * Response: `{ok: true, model, warnings, report: {format, read}}`.
  * The imported model replaces the server model unless `dry_run`.
  * A file with no frames or areas is a 400.
* **Reports.**
  * `s2k`: `{format, units, written, tables: {name: rows}, skipped}`.
  * `f2k`: `{format, story, elevation, units, written: {points, slabs,
    beams, columns, load_patterns, column_reactions, load_cases,
    load_combinations}, reaction_totals: {case: ΣFgrav}, tables,
    skipped}`.
  * `sdnf`: `{format, version: "3.0", units, written, skipped}`.
  * `idea`: `{format, schema, written: {points, members, cross_sections,
    materials, connection_points, load_cases, combinations,
    internal_force_results}, skipped, files}`.
  * `ifc_revit`: the IFC report plus `written.revit_types` /
    `revit_psets`.
* **`export_ifc(model, include=None, revit=False)`.**
  * `revit=True` adds Body / Axis sub-contexts, storeys placed at their
    elevation with `LongName`, Ifc{Column,Beam,Member}Type with
    Revit-recognised names plus IfcRelDefinesByType, Pset_*Common
    (Reference / LoadBearing / IsExternal), and Revit material names with
    a capitalised Category.
  * `read_ifc` reads the file back with identical geometry.
* **UI (`js/interop.js`, `js/mock_interop.js`).**
  * Import acts: `file-iop-imp-s2k|f2k|sdnf|cis2`. Export acts:
    `file-iop-exp-s2k|f2k|sdnf|idea|ifc_revit`.
  * Dialogs: `#ioExport` (`#ioUnits` defaults to the display units,
    `#ioStory`, `#ioAbove`, `#ioLen`, `#ioEffects`, `#ioInc_*`) and
    `#ioImport` (`#ioFile`, `#ioImpUnits`, `#ioFromCur`).
  * Downloads and reports reuse `__sky.exchange`.
  * Test hook: `__sky.interop = {EXPORT, IMPORT, openExport, openImport,
    exportNow, importNow, state}`.
  * Panels: File › Import 9 and File › Export 11 entries.

### Validation

* `tests/test_api_openapi.py`:
  * official and structural spec validity;
  * every route covered;
  * core shapes;
  * the v1 alias.
* `tests/test_oapi_facade.py`: see above.
* `tests/test_plugins.py`:
  * disabled = no-op;
  * enable → JS served, traversal refused, table added (closed-form
    counts 6 columns / 7 beams per story, drifts equal to the analysis);
  * hooks and endpoint work;
  * state persists;
  * failures are contained;
  * manifest validation.
* `tests/test_interop_csi.py`:
  * s2k round trips (identical static results and periods; a rich model
    exact; kip-in to 1e-12);
  * f2k reactions closed forms (ΣFgrav = 25·ΣL of the beams above, ΣFx =
    story forces above);
  * f2k round trip;
  * HTTP.
* `tests/test_interop_steel.py`:
  * SDNF round trip (exact; mm; foreign packets; placeholders);
  * CIS/2 analysis and design models (AISC lookup, parametric I area
    closed form, mm units);
  * IDEA IOM well-formed, with the expected counts: 6 points, 7
    members / elements / segments, 2 sections, 5 connection points, 1
    load case, 1 combination, 77 results;
  * base forces in the written LCS balance 240 kN;
  * HTTP.
* `tests/test_ifc_revit.py`: default unchanged, Revit entities, exact
  re-import, names.
* Playwright, mock 21/21 and live 23/23:
  * every new dialog opens from its menu;
  * exports leave the model byte-identical;
  * kip-in sets the s2k units;
  * s2k export → import round trip (byte-identical in mock);
  * a bad CIS/2 file gives an inline error;
  * Plugins OK with defaults is a no-op;
  * enabling the example adds its menu item and table (one row per
    story), and disabling removes it;
  * the API reference dialog opens;
  * live analysis succeeds; existing menus still work;
  * every panel has ≤ 14 entries;
  * zero console errors.

## Buckling with shells and links, sparse eigen and BucklingFEM

v1.24 (Wave B). Modules: `engine/buckling_fem.py`, `engine/geom_stiffness.py`,
`engine/lanczos.py`, `engine/sparse_solve.py`, `engine/sparse_modal.py`,
`core/buckling_fem.py`, `core/member_buckling.py`, `core/stability_sparse.py`,
`api/buckling_api.py`; UI `js/bucklingfem.js`, `js/mock_bucklingfem.js`.
Tests `tests/test_buckling_fem.py`. Benchmarks: docs/PERFORMANCE.md
"Large models (v1.24)".

### Buckling case options

```jsonc
"buckling_cases": {"B": {"name": "B", "gravity": {"DEAD": 1.0}, "num_modes": 6,
  "base_case": null,
  "formulation": "fem",        // emitted only when not "frame" (default)
  "include_shells": false,     // emitted only when false (default true)
  "include_links": false}}     // emitted only when false (default true)
```

* `formulation: "frame"` (default) is the v0.10 numpy frame-only solver,
  unchanged and byte-identical (shells / links skipped and end releases
  ignored, each with its legacy warning).
* `formulation: "fem"` solves `K phi = lambda (-Kg) phi` on the engine's
  own elastic model. `K` contains every element the engine builds: frames
  with offsets, per-DOF releases and partial fixity, auto-mesh, panel
  zones; shells; links; springs; rigid and named diaphragms.
  * The reference state is a linear solve of `gravity` with every engine
    load type, area loads included.
  * `Kg` comes from the element responses: frame axial force
    `N = (-localForce[0] + localForce[6]) / 2`, with native moment releases
    condensed; truss string stiffness; shell membrane resultants
    `n11, n22, n12` at the Gauss points (plate geometric stiffness on the
    three translations). Rigid-zone arms, edge ties and links carry no
    `Kg`.
  * The eigenproblem is solved by shift-invert Lanczos with locking and
    subspace refinement. Every step is one solve on ONE MUMPS
    factorization.
  * `base_case` uses the two-load-set form
    `(K + Kg_base) phi = lambda (-Kg) phi`, solved by a K-preconditioned
    Davidson iteration.
  * `include_shells` / `include_links = false` analyse a copy without
    those objects. Supports and loads left on points no remaining element
    uses are dropped, with a warning. Modes are reported on the full
    model's mesh tags.
* Result additions (formulation "fem" only; the frame result dict is
  unchanged):

```jsonc
"buckling": {"B": {"factors": [...], "modes": {"1": {"<tag>": [6]}}, "gravity": {...},
  "warnings": [...], "base_case": null,
  "formulation": "fem", "include_shells": true, "include_links": true,
  "solver": {"system": "Mumps", "equations_estimate": 952, "solves": 49,
             "converged": true, "max_residual": 7.6e-11},
  "geometric_stiffness": {"frame_elements": 0, "shell_elements": 144}}}
```

Validation (`tests/test_buckling_fem.py`):

* Simply supported square plate in uniaxial compression,
  `Ncr = 4 pi^2 D / b^2`: 12x12 mesh +0.79 % (pinned within 2 %); the
  second mode is k = 6.25 (4 %).
* Shell strip column (nu = 0): `pi^2 E b t^3 / 12 L^2`, +0.41 %.
* Link-braced column (pinned base, top spring k): `P = k L` to 1e-6. A
  stiff link gives the Euler load (2e-3).
* Pinned-pinned column through end releases with fixed supports:
  `pi^2 E I / L^2`, +3.3e-5. The frame formulation still reports its
  legacy "releases ignored" result.
* Two-load-set identity `lambda(base = P) = lambda(none) - 1` (1e-7).

### Sparse eigen, MUMPS and the sparse stability check

* `engine/sparse_solve.EngineSolver`: `K^-1 f` on the reused elastic domain
  with `system Mumps` + `Linear -factorOnce`. MUMPS keeps its LU factors,
  while this OpenSees build's UmfPack refactors on every `analyze`.
* Solver policy: `analysis_options.solver = "auto"` uses MUMPS at or above
  `MUMPS_EQ_THRESHOLD = 20000` estimated equations. Below it, nothing
  changes (byte-identical). `FACTOR_ONCE_SYSTEMS` now includes Mumps.
  * A MUMPS factor-once solve of a pattern with ELEMENT loads fails in
    MUMPS' substitution (error -3). `solver_policy.analyze_reused`
    re-factors once and retries, quietly.
  * `threads_info()` reports that the build links libgomp but gains no
    thread speed-up (single-threaded reference BLAS). No multithreaded
    path is enabled.
* `analysis_options.eigen_solver`: `"auto" | "legacy" | "lanczos"`.
  * `"auto"` runs the sparse Lanczos modal solver only above both
    thresholds: a sparse system AND more than `MAX_MASSED` (600) free
    massed DOFs, i.e. where the exact condensation does not apply.
    Smaller models keep their exact legacy path.
  * `"lanczos"` forces it.
  * Shapes are mass-normalised with a deterministic sign, and degenerate
    clusters are put in canonical form. Pinned equal to the legacy eigen
    run to 1e-9 (quick building with RS: 4e-14).
* Stability check `POST /api/check/stability` gains
  `{method?: "auto" | "dense" | "sparse"}`; `stability_diagnostics(...,
  method="auto")`.
  * Models above the dense cap (3000 equations, up to 400 000) get the
    sparse check instead of the "too large" skip.
  * The sparse check adds soft grounded springs `1e-10 x k_ref` to every
    free DOF and runs Lanczos on `K phi = mu S phi`. Mechanisms are modes
    with `theta = 1/(1+mu) > 1/2`.
  * The result carries `"method": "sparse"` and `"sparse": {system,
    solves, converged, spring_ratio, reference_stiffness,
    ground_springs}`. `lowest_eigenvalues` holds `1e-10 x mu`, and the
    condition / diagonal-ratio fields stay null.
  * `"dense"` keeps the legacy skip. The mechanism count and unstable DOFs
    equal the dense check on the test portals.

### BucklingFEM (member shell buckling)

`POST /api/member_buckling` request:

```jsonc
{"member": "B1", "model"?: {...}, "case"?: "DEAD",
 "forces"?: {"P": -100, "M3i": 0, "M3j": 0, "M2i": 0, "M2j": 0, "w2": 0},
 "boundary"?: "fork" | "fixed", "load_position"?: "shear_center" | "top" | "bottom",
 "mesh"?: {"n_long": 60, "n_web": 8, "n_flange": 4}, "num_modes"?: 4,
 "section"?: {"type": "I", "dims": {"d", "bf", "tf", "tw"}}, "length"?: 4.0}
```

Response:

```jsonc
{"member", "section": {"name", "type", "dims"}, "material": {"name", "E", "nu"},
 "length", "boundary", "load_position", "forces",
 "mesh": {"n_long", "n_web", "n_flange", "nodes", "shells", "equations"},
 "section_props": {"A", "xc", "yc", "I33", "I22", "J", "Cw"},
 "factors": [...],
 "modes": [{"mode", "factor", "class", "group", "global_fraction", "half_waves"}],
 "nodes": {"<tag>": [x, y3, y2]}, "quads": [[t1, t2, t3, t4]],
 "shapes": {"1": {"<tag>": [ux, uy, uz]}},
 "references": {"P_euler_minor", "P_euler_major", "Mcr_ltb"?,
                "plates": [{"role", "b", "t", "k", "sigma_cr"}]},
 "solver": {"system", "solves", "converged", "max_residual"}, "warnings": [...]}
```

`POST /api/member_buckling/forces {member, case, model?}` returns
`{forces}`: mean N, end M3 / M2, and `w2` from the mid-span deviation of M3.

* Sections: `I`, `I_mono`, `channel`, `tee`, `rect_tube`. Dimensions come
  from `dims`, the steel-database record (`db_ref` or a library W name), or
  `section`. Any other section returns a 400.
* Forces are internal: `sigma = P/A - M3 (y2 - yc)/I33 + M2 (y3 - xc)/I22`,
  the member_stations convention. Positive M3 compresses +2, and positive
  M2 puts +3 in tension. The stresses are integrated exactly on the
  mid-line segments. Shear and span loads are reacted by the end supports.
* `class`: `local | distortional | lateral-torsional | torsional |
  flexural-minor | flexural-major`. It is a heuristic built from a rigid
  section fit per station and the plate-junction motion.
* Validation:
  * fork I beam under uniform moment vs `Mcr = (pi/L) sqrt(E Iy G J)
    sqrt(1 + pi^2 E Cw / (G J L^2))`: +1.9 % (pinned within 5 %);
  * square tube in compression, walls as SS plates with k = 4: +0.65 %
    (pinned within 3 %);
  * I web local buckling between the SS (k = 4) and clamped (k = 6.97)
    plate values: k = 5.83.
* UI: Analyze › Member Buckling… (`an-member-buckling`, also in the frame
  right-click menu) opens a dialog with a mode table, the closed-form
  references and an animated 3D mode-shape viewer.
* The Buckling case dialog's Advanced group gains the Formulation, Include
  shells and Include links fields. Only touched fields are written.


## E2K native nonlinear records (Wave C)

`export_e2k` now writes pushover / nonlinear static / staged / steady-state
/ PSD cases, hinge properties + assignments + overwrites, nonlinear link
properties and temperature loads as native ETABS records, and `read_e2k`
reads them back (module `skyframe/io/e2k_nonlinear.py`, hooks in
`e2k_write.py` / `e2k_read.py`). Before, they travelled only as `$SF`
comments. Whatever a record cannot carry still falls back to `$SF` (the
exporter diffs the native re-import), so `report["exact"]` is unchanged.
Models without these features export byte-identically.

Record dialect (file units; `"..."` = quoted name):

* **Nonlinear static** (`model.nonlinear_static_cases`):
  `LOADCASE "N" TYPE "Nonlinear Static" INITCOND "<start_from>"|"NONE"
  MODALCASE "MODAL"`, one `LOADPAT "p" SF s` row per load, `NLGEOMTYPE
  "None"|"PDelta"|"LargeDispl"`, `LOADCONTROL "Full"|"Displacement"
  DISPLTYPE "Monitored" [MONITOREDDISPL "Joint"|"Story" STORY "s"]
  [DISPLMAG d] DOF "U1".."R3" [JOINT "pt" "level"]`, `RESULTSSAVED
  "Multiple" MINSAVED n MAXSAVED n` (n = steps), `[TOWER "t"]`, and the
  case hinge plan `HINGEMODE "Column Base"|"All Ends"|"ASCE41" HARDENING h
  [DEFAULTMY m]`, `HINGEMY "uid" MY m`, `HINGEPARAM "k" VALUE v`.
* **Pushover** (`model.pushover_cases`): the same rows plus
  `LOADDIST "Roof Point"|"Pattern"|"Mode"|"Uniform Acceleration"|
  "Triangular" DIR "X"|"Y" TARGETDRIFT r [K k] [LOADMAG f] [TOWER "t"]`;
  the distribution load as `LOADPAT "p" SF 1` / `MODE n SF 1` (0 =
  dominant) / `ACCEL "U1"|"U2" SF 1`; held gravity as an auxiliary
  full-load case `"<name>~GRAV"` named in INITCOND (else INITCOND =
  start_from).
* **Reading ETABS files:** a Nonlinear Static case with LOADDIST / MODE /
  ACCEL rows becomes a pushover case (gravity = the pattern factors of its
  INITCOND nonlinear case), one with LOADCONTROL a nonlinear static case
  (start_from = INITCOND when that is a nonlinear static case; a chain is
  given its most nonlinear NLGEOMTYPE, reported), anything else a static
  LoadCase as before. steps = MINSAVED (default 10 / 100).
* **Staged** (`model.staged_cases`): `TYPE "Nonlinear Staged
  Construction"`; per_story: `AUTOSTAGE "Per Story"` + `LOADPAT "pattern"
  SF 1`; user stages: `STAGE "s" [DURATION d]` then `STAGE "s" OPERATION
  "Add Structure"|"Remove Structure"|"Load Objects" OBJTYPE "Group"
  OBJNAME "g" [AGE a] [LOADTYPE "Load Pattern" LOADNAME "p"] [SF s]`;
  include_live as `FINALLOADPAT "p" SF f`.
* **Frequency** (`frequency_functions`, `steady_state_cases`,
  `psd_cases`): `FUNCTION "f" FUNCTYPE "STEADYSTATE"|"PSD"` + `FUNCTION "f"
  FREQ hz VALUE v`; `TYPE "Steady State"|"Power Spectral Density"`, loads
  `LOADPAT "p"|ACCEL "U1".."U3" SF s FUNC "f" PHASE deg` (ACCEL SF in
  file length/s²), `FREQRANGE f0 FREQEND f1 NUMFREQ n MODALFREQ "Yes"`,
  `ADDFREQ f`, `SOLUTIONMETHOD "Modal"|"Direct" NUMMODES n`, `DAMPTYPE
  "Modal"|"Hysteretic" CONSTDAMP z`, `OUTPUTJOINT "pt" "level"`.
* **Hinges** (`$ FRAME HINGE PROPERTIES`): `HINGE "h" BEHAVIOR
  "Deformation Controlled" DOF "M3"|"M2"|"P"|"V2"|"V3"|"T"|"PMM"|"M2M3"
  TYPE "Moment-Rotation"|"Force-Displacement" HYSTYPE "Kinematic"|..
  BEYONDE "Drops To Zero"|"Is Extrapolated"`; backbone points after A as
  `HINGE "h" "B" d f` .. `"-B" -d -f` (f unitless × MOMENTSFP/FORCESFP;
  d absolute unless ROTATIONSFP/DISPSFP is given, `"Auto"` = yield
  deformation); `IO x LS x CP x -IO ..`; `KELASTIC k`; `PINCHX ..`.
  `DOF "FiberPM2M3" FROMSECTION "Yes"` for PMM_fiber; auto (code-table)
  properties `AUTOHINGETYPE "ASCE41-17" TABLEITEM "Concrete Beams" DOF
  "M3"` + `AUTOPARAM "k" VALUE v`, `GRAVLOADPAT "p" SF f`, `AXIALP P`.
  Assignments `HINGEASSIGN "frame" "story" HINGEPROP "h" [AUTOHINGETYPE ..
  TABLEITEM .. DOF ..] RDISTANCE r`; legacy `auto_m3` = `AUTOHINGETYPE
  "ASCE41-17" DOF "M3" RDISTANCE 0|1` (no TABLEITEM / HINGEPROP);
  `fiber_pmm` = `DOF "FiberPM2M3" RDISTANCE 0|1`; ETABS auto assignments
  without HINGEPROP create property `"Auto <code> <item> <dof>"`.
  Overwrites `HINGEOVERWRITE "frame" "story" [AUTOSUBDIVIDE "Yes"]
  [AUTOSUBDIVIDERELLENGTH r]`.
* **Links**: device links get `LINKPROP "LP_uid" TYPE "<ETABS type>"
  [HYSTYPE ".."]` (damper "DAMPER - EXPONENTIAL", damper_bilinear "DAMPER -
  BILINEAR", gap / hook, isolator "RUBBER ISOLATOR" "Bilinear",
  rubber_isolator_bouc_wen "RUBBER ISOLATOR" "Bouc-Wen", fp_isolator
  "FRICTION ISOLATOR", tc_fp_isolator "T/C FRICTION ISOLATOR", triple_fp
  "TRIPLE PENDULUM ISOLATOR", multilinear "MULTILINEAR ELASTIC",
  multilinear_* "MULTILINEAR PLASTIC" + Kinematic/Takeda/Pivot,
  friction_spring "DAMPER - FRICTION SPRING", plastic_wen / brb "PLASTIC
  (WEN)" (+ "BRB")) and per-DOF parameter rows `LINKPROP "LP" DOF "U1"
  NONLIN "Yes" K k C c EXP a ...` (keyword table `_LINK_KEYS`; damper C
  in force·(s/length)^alpha), multilinear points `DOF "U2" DISPL d FORCE f`
  (negative side signed).
* **Temperature**: `LINELOAD "frame" "story" TYPE "TEMPERATURE" LC "p" T
  dT` + `TYPE "TEMPERATUREGRADIENT" DIR "2"|"3" LC "p" TGRAD g` (°C per
  file length), `AREALOAD .. TYPE "TEMPERATURE" | "TEMPERATUREGRADIENT"
  DIR "3"`, `POINTLOAD "pt" "level" LC "p" TYPE "TEMPERATURE" T dT`.
* **Robustness**: an ETABS item SkyFrame rejects (bad hinge property,
  unsupported table, invalid case) is dropped with a warning (`salvage`);
  the reader never raises on these records. `PDELTA LOAD "p" FACTOR f`
  rows are read as P-Delta load factors.

### DWG

No DWG converter is available on the build platform (no `libredwg-tools`
in Ubuntu 24.04, no pip converter). `skyframe.io.dwg.check_not_dwg` (called
by `import_dxf_layers`) refuses content starting with a DWG signature
`AC10xx` with `DWG_HELP` (save as DXF from the CAD program);
`POST /api/exchange/import/dxf` answers 400 with that message. The DXF
import dialog accepts `.dwg` to show it (mock: `js/mock_e2k_nonlinear.js`).

### Tests

`tests/test_e2k_nonlinear.py` (16): native-only re-import equals the
model exactly in kN-m and within 1e-12 in kip-in / N-mm / kgf-m / lb-ft;
analysis results of a model with pushover, NL static chain, user and
per-story staged, steady-state, PSD, user/auto/legacy hinges, 16 device
links and frame / shell / joint temperatures are identical (0.0 rel)
after the native round trip; dialect records; legacy exports unchanged;
hand-written ETABS file: restrained beam N = E A α ΔT = 480 kN and
M = E I α g = 24 kN·m, damper C_SI = C·F/L^α, hinge scale 66.77 kN·m,
displacement pushover ends at DISPLMAG = 30 mm; warnings not raises;
API round trip; DWG detection + HTTP 400; Tekla/Revit file routes.
Playwright mock 17/17, live 20/20.

## Cut, pier and spandrel forces for every case type, TH shell results and Compare Results (Wave C)

Bulk in `skyframe/engine/cut_results.py` (engine), `skyframe/core/th_output.py`
(TH output request), `skyframe/core/th_shells.py` (TH shell sources),
`skyframe/core/cut_tables.py` (Max / Min table rows),
`skyframe/core/results_diff.py` + `skyframe/api/results_compare_api.py`
(comparison). Shared files carry one-line hooks only: `_modal_static`
(per-mode cut vector), `modal_condensed._unit_response` (condensed RS path),
`run()` (harvest after the RS combos; extend after the spandrels),
`run_time_history` (recorder begin / record / finish),
`nonlinear_static.run_case` (per-step recorder), `TimeHistoryCase.th_output`
+ `to_dict` / `validate` / `from_dict` (model.py), three hooks in
`core/shell_results.py`, one `_CASE_TYPE` entry pair in
`core/shell_tables.py`, one decorate line in `core/tables.compute_table`, one
register line in `api/server.py`.

### Cut forces for every case type

The section-cut (`compute_section_cut`), pier (`_pier_cut_forces`) and
spandrel (`engine/spandrels._cut_resultant`) kernels are linear in the element
forces; they are now evaluated for:

| case type | how | entry |
|---|---|---|
| response spectrum, RS directional combo | per mode on the mode's static solve; the signed per-mode vector rides through the RS machinery as one pseudo member-station entry (uid `__cutx__`), so CQC / SRSS / GMC / DSC / NRC / ABS, rigid response, missing mass, multi-direction SRSS / CQC3 / ABS, eccentricity (`R + abs(R_T)`) and `rs_combos` all apply unchanged; `run()` removes the carrier before the results are built | positive envelope (standard keys) |
| time history (direct integration, incl. named modal case / initial case) | per step from the step's element forces (only the cut members and shell elements are queried), past the gravity state like every TH series | max / min of the signed history |
| nonlinear static | per converged step (span-load records scaled by the step's load factor) | final state |
| staged | accumulated final state | single value |
| max / min combos (legacy envelope; every extended combo that is not single-valued: envelope / abs / srss / range, add with RS or TH members, nested) | CONTRACT "Load combinations" interval rules on the member vectors (`combos_ext.combine_terms`) | max / min |

Results land in the EXISTING blocks with the existing per-entry shape;
max / min entries add a `"min"` sub-dict of the same shape:

```jsonc
"section_cuts": {"THX": {"CUT": {"FX": 12.1, "FY": 0, "FZ": 0, "MX": 0, "MY": 18.2, "MZ": 0,
                                 "min": {"FX": -11.7, ...}, "n_members": 1, "n_shells": 0, "warnings": []}}},
"piers":     {"ENV": {"P1": {"Story1": {"P": 5.98, "V": 48.4, "M": 120.4, "min": {"P": -24.4, "V": -11.9, "M": -29.5}}}}},
"spandrels": {"THX": {"SP1": {"Story1": {"length": 2.0, "n_shells": 1, "n_frames": 0,
                                         "Left": {...}, "Right": {...}, "min": {"Left": {...}, "Right": {...}}}}}}
```

Histories:

* `th_cases[name]["cut_forces"] = {"t": [...], "section_cuts": {cut: {FX: [...], ...}},
  "piers": {label: {story: {P: [...], V: [...], M: [...]}}}, "spandrels": {label: {story:
  {Left: {P: [...], ...}, Right: {...}}}}}` (one value per entry of `t`);
* `nonlinear_static[name].nonlinear.history["cut_forces"]` — the same blocks with
  `"step"` instead of `"t"` (one value per history step, entry 0 = initial state).

Emitted only when the model defines section cuts, pier labels / auto piers or
spandrel labels. **Legacy byte-identical:** static-case and single-valued
combo entries are never recomputed (bit-identical); a model without cuts /
piers / spandrels gives a bit-identical results JSON (verified by JSON hash on
a 3-story frame with RS + TH + envelope combo and on an unlabeled meshed wall
with RS + TH; on a labeled wall + cut model every pre-existing key is
bit-identical). FNA time histories record no cut forces (direct integration
only, like plot functions).

Validation (`tests/test_wavec_cut_results.py`): RS pier, spandrel and cut
values of a single-mode model equal `abs(static) x Sa g m / F_static` to 1e-9
(cut: `abs(FX) = Sa g m`, `abs(MY) = 1.5 Sa g m`); TH pier shears of the two base
piers sum to `-base_FX(t)` (1e-9 relative) and a frame cut below the only
mass equals `base_FX(t)`; envelope / add-with-RS / envelope-with-TH combos
bracket their members exactly; nonlinear static step `i` = `i/n` x final.

### TH shell results (`TimeHistoryCase.th_output`)

```jsonc
"th_output": {"shells": true, "steps": [50, 120], "every": 0, "frames": 0}
```

Only non-default keys are kept (canonical form; `None` / absent = legacy).
`steps`: 1-based snapshot steps (<= 200); `every`: also a snapshot every N
steps; `frames`: number of evenly spaced deformed-shape animation frames
(<= 1000). Validation (`ValueError`): unknown key, steps not integers >= 1,
every < 0, frames outside [0, 1000].

* `shells: true` -> `th_cases[name]["shell_forces"] = {"steps": [...], "t": [...],
  "at": {"<step>": {"<quad>": [8]}}, "max": {"<quad>": [8]}, "min": {"<quad>": [8]}}` —
  the static `shell_forces` resultants (past the gravity state) at the snapshot steps
  and their running max / min over every step.
* `frames: n` -> `th_cases[name]["frames"] = {"steps": [...], "t": [...],
  "node_disp": {"<step>": {"<tag>": [ux, uy, uz, rx, ry, rz]}}}`.

Shell contours / tables: a TH case is now a shell source — `"<case>"` is the
step envelope (bounded: Max / Min, `case_type` `LinDirHist`), `"<case> @ step
<k>"` the signed snapshot (kind `time_history_step`); both appear in
`POST /api/shell_results/sources` and work in `POST /api/shell_results/contour`
and the Shell Forces / Stresses tables; a TH member of a combo enters with its
envelope box. Validation: the snapshot at a step equals a static solve with the
step's nodal displacements imposed (1e-7).

### Tables

Pier Forces, Spandrel Forces and Section Cut Forces gain a `step_type` column
("Step Type", after Case Type) with one `Max` and one `Min` row per max / min
entry, and name the case kind (`LinRespSpec`, `LinDirHist` / `NonDirHist`,
`NonStatic`, `NonStaged`); RS pier `P` is reported as the positive envelope.
Results with neither max / min entries nor these case kinds return the legacy
table unchanged.

### Results comparison (Compare Results)

`POST /api/results/compare` — diff table of two runs. Body `{a, b, case?, quantity?, rtol?, atol?, max_rows?}`;
`a` / `b` are results dicts, saved wrappers `{"results": {...}}`, or `"current"`
(the stored results of the last analysis of the current model). Returns
`{equal, n_compared, n_flagged, worst_rel, rtol, atol, truncated, rows, cases,
quantities}`; 400 on a bad body / tolerance or missing current results.

A value pair is flagged when `abs(a - b) > max(atol, rtol * max(abs(a), abs(b), S))`
(`S` = largest magnitude of the quantity family in run A, as in
`core/results_compare.py`; modal shapes sign / cluster aligned). Rows
(largest relative difference first):
`{path, block, case, quantity, item, a, b, diff, rel, status}` with `status`
`changed` | `type` | `length` | `only_a` | `only_b` (a subtree present in one run
only: one row, `a` / `b` = its count of numbers). `case` filters the name-keyed
blocks (`cases`, `combos`, `rs_cases`, `th_cases`, `section_cuts`, `piers`,
`spandrels`, ...); `quantity` a case-block quantity (`node_disp`, `base`,
`member_stations`, ... — `min.<q>` envelope minima included) or a block name.
Geometry blocks (`nodes`, `members`, `shell_quads`, ...) and status maps are
not compared.

### UI (Wave C)

Entry `js/wavec_results.js` (app.js: one import + one init line). Menus
(`sky.menu.add`, every panel stays <= 14 entries):

| command | act | module |
|---|---|---|
| Display › Force Diagrams › Pier / Spandrel / Link Diagrams (3D)… | `dis-wc-diag3d` | `js/wc_diagrams.js` — piers (one block per story at the pier centroid, V / M in the wall plane, P along the normal), spandrels (Left → Right along the span), elastic links (`k (u_j − u_i)`, static cases / single-valued combos); any case in `results.piers` / `spandrels` incl. RS, TH and max/min combos (Min dashed). Drawn through a one-line `Viewer3D.wcOverlay` hook. |
| Display › Export Animation Video… | `dis-wc-video` | `js/wc_video.js` — deformed shape (cyclic), mode shape or TH response (recorded `frames`, else story displacements spread over each story) of the 3D canvas via `canvas.captureStream(fps)` + `MediaRecorder` (WebM, VP9 / VP8), frame rate and duration options; the WebM Duration element is written into the header; saved through the `<a download>` path (browser and pywebview). |
| Tools › Compare Results… | `tools-wc-compare` | `js/wc_compare.js` — Run A / Run B from the current results, the last 5 runs of the session, or loaded result files (`.json`; "Save Current Results…" writes `{"results": …}`); case / quantity filters, relative tolerance; diff table in display units. |
| Load Case Data — Time History › Output | — | `js/wc_thout.js` (one body line in `js/dlgstd_cases.js`): shell forces on/off, snapshot steps, every N steps, animation frames; only changed keys are written (OK with defaults is byte-identical). |

`js/mock_wavec.js` gives `?mock=1` TH / max-min cut entries and histories,
TH shell snapshots / frames, th_output validation and the results diff;
`js/mock_shellres.js` serves the TH shell sources (three hook lines).
Playwright: mock 29/29, live 13/13 (dialogs open from their menus; TH
output edit round-trips through `POST /api/model`; OK with defaults
byte-identical; pier / spandrel / link diagrams change the canvas (pixel
diff); TH / mode / deformed videos are non-empty WebM blobs of the requested
duration (`video.duration` 1.5 s / 2 s); Compare Results flags a deliberately
changed value; kip-in labels; zero console errors).

## Eigen shift / cutoff / tolerance, modal history and FNA case types, TH initial conditions, FNA links, RS / steady-state modal damping, pushover any DOF, Cm / TL / FEMA 440 DM, shell creep (Wave C dynamics)

Model side `core/wavec_dyn.py` (+ `core/pushover_coeff.py`, `core/fna_devices_ext.py`);
engine `engine/eigen_shift.py`, `engine/wavec_th.py`, `engine/fna_links_ext.py`,
`engine/modal_damping.py`, `engine/shell_creep.py`. Every default reproduces the
previous results byte-identically (nothing new is emitted while unset).

### Eigen parameters

```json
"eigen_options": {"shift": 2.5, "cutoff": 1.0, "tolerance": 1e-9},
"modal_cases": {"MSH": {"type": "eigen", "num_modes": 6, "shift": 4.0}}
```

* `shift` Hz >= 0 (center), `cutoff` Hz >= 0 (radius, 0 = none), `tolerance` in (0, 0.01]
  (default 1e-9). `eigen_options` = the default MODAL case (emitted only when non-empty);
  the same keys on an eigen modal case (not with `stiffness_case`, not Ritz, not with
  `modal_from_case`).
* Method: exact condensation onto the massed DOFs (`A = M^1/2 G_mm M^1/2`), shift-invert
  subspace iteration `S = (I - sigma A)^-1 A`, `sigma = (2 pi shift)^2`, Rayleigh-Ritz,
  stopping when every retained eigenvalue changes by less than `tolerance`. Retained = the
  `num_modes` modes nearest the shift with `|f - shift| <= cutoff`, reported in ascending
  frequency. A shift exactly on an eigenvalue is nudged by 1e-7. Cap: 4000 massed DOFs.
* `modal.eigen` (and `modal_cases[n].eigen`) = `{shift, cutoff, tolerance, sigma, requested,
  found, iterations, converged}`.

### TH case types (`TimeHistoryCase.th_type`, emitted only when not "direct")

* `"direct"` (default) | `"modal"` (Linear Modal History) | `"fna"` (Nonlinear Modal
  History). The case stays in `th_cases` (results in `th_cases`, combinations unchanged).
  `model.case_kinds()` reports `"modal_history"` / `"fna"`. `run()`, Set Load Cases to Run
  and `run_time_history` run the case by its own method, and the results carry
  `th_cases[n].th_type`.
* `modal`: device links are replaced by their FNA linear part, then a device-free modal
  integration runs. `fna`: `run_fna`. Both reject `nonlinear` (hinges) and DI options;
  `modal` also rejects gravity / initial_case. DB table `th_cases` has a `th_type` column.

### TH initial conditions (`initial_case`)

* May name a nonlinear static OR a staged case (validation, dependency plan).
* Direct integration: legacy chains (load control, no hinges) are unchanged. Other NLS
  chains (hinged / displacement control, geometric none) are replayed in the TH domain;
  the hinges are the TH case's own if it defines any, else the predecessor's. Staged: the
  end displacements are imposed (`setNodeDisp`, one zero Linear step, then `F(u_s)` as
  constant loads), so the domain starts at `u_s` in equilibrium. Staged locked-in
  self-equilibrated forces are not represented.
* `th_cases[n].initial_state = {case, kind: "nonlinear_static"|"staged", hinges_from?,
  max_disp_mismatch?}`. FNA also accepts a staged `initial_case` (device `d0` from `u_s`).

### FNA links (amends "Link types: Plastic (Wen), BRB, bilinear damper, T/C friction isolator")

| type | device | rows | basis |
|---|---|---|---|
| `multilinear_takeda` / `multilinear_pivot` (+ C5 `multilinear_degrading` with `points`) | OpenSees `Hysteretic` law transcribed (`pinch_x`/`pinch_y`/`unload_beta`/`points_neg` when present) | 2 shear | `[k1, k1, kv]` |
| `brb` | Menegotto-Pinto, asymmetric kinematic (Steel4 `-asym -kin`) | 1 axial | none |
| `damper_bilinear` | Maxwell + bilinear dashpot | 1 axial | none |
| `tc_fp_isolator` | friction pendulum, zero axial / shear in uplift, hold-down | 3 | `[k0 + N/R, k0 + N/R, kv]` (needs gravity / initial_case) |

`th_cases[n].fna.devices` names `HystereticDevice`, `MenegottoPintoAsym`, `BilinearDamper`
and `TCFrictionPendulum`. Hinged FNA cases still raise.

### Modal damping of RS / steady-state / PSD cases

```json
"modal_damping": {"type": "interpolated_period", "points": [[0.1, 0.03], [1.0, 0.05]],
                  "overrides": {"2": 0.10}, "sa_correction": "ec8", "function_damping": 0.05}
```

* `type`: constant | interpolated_period | interpolated_frequency | mass_stiffness
  (`a0`/`a1`, or `periods` + `ratios`) | composite (`material_damping`, strain energy).
  `overrides` per mode (1-based). `sa_correction` (RS only): none | ec8
  (`eta = sqrt(10/(5+100 zeta)) >= 0.55`, relative to `function_damping`).
* RS: Der Kiureghian variable-damping CQC (and per-mode DSC). Results:
  `rs_cases[n].rs.modal_damping = {type, zetas, sa_factors?, composite?}`.
  Steady state / PSD: `H_i = 1/(w_i^2 - w^2 + 2 i zeta_i w_i w)`. Results:
  `steady_state[n].modal_damping` / `psd[n].modal_damping`.

### Pushover

* `PushoverCase.control_dof` is any of UX UY UZ RX RY RZ. UZ / R* with displacement
  control need `target_disp` (m or rad). A rigid-diaphragm slave drives its master only
  for UX / UY / RZ. `capacity_curve.dof` is the label. SkyFrame has no generalized
  displacements, so that control option is not offered.
* ASCE 41-17: `POST /api/results/performance-point` accepts `TL` (s), and `system` (a
  Table 7-4 key: concrete_moment_frame 0.9, concrete_shear_wall 0.8, concrete_pier_spandrel
  0.8, steel_moment_frame 0.9, steel_cbf 0.9, steel_ebf 0.9, other 1.0; applies for 3+
  stories and Te <= 1 s) or `Cm`. `PushoverCase.performance` accepts `cm_system`, `Cm`,
  `near_field` and `alpha_pdelta` (emitted only when set). Results add `TL` / `system` only
  when given.
* Method `"fema440_dm"` (FEMA 440 displacement modification; not in the default method
  list): `delta_t = C0 C1 C2 Sa Te^2 g / 4 pi^2`, C1 Eq. 5-1, C2 Eq. 5-2,
  `R = Sa / (Vy/W) Cm`, `R_max` Eq. 5-4 with `alpha_e` Eq. 5-5 (post-peak slope; lambda 0.8,
  or 0.2 near field) -> `dynamic_instability`. Keys: `R, Cm, C0, C1, C2, delta_t, alpha_2,
  alpha_e, lambda, R_max, dynamic_instability` plus the bilinearization.

### Shell creep / shrinkage (step staged)

* `StagedCase.time_dependent.shell_creep: true` (method `step` only). The default (off)
  keeps the legacy ageing-only shells. The element-average mechanical membrane strain and
  curvature come from the nodal displacements (boundary integral). Bazant superposition
  uses the shell law's `J(t, tau)` and shrinkage. The imposed strains enter as the exact
  work-equivalent nodal loads of a constant strain (membrane `(L/2) N0 n`, bending moment
  `h2 e1 - h1 e2` with `h = (L/2) M0 n`), and the shell modulus is `1/J` as for frames.
  Shrinkage is uniform per region; creep is per element.

### UI

* `js/wavec_dyn.js` (+ `js/mock_wavec_dyn.js`): Analyze › Advanced › **Eigen Parameters…**
  (`an-wc-eigen`) and **Modal Damping (RS / SS)…** (`an-wc-mdamp`); Define › Load Cases ›
  **Time History Solution Type…** (`def-wc-thtype`). Hooks: `app.js` (1 import, 1 init),
  `casedlg.js` (pushover DOF options), `pomethods.js` / `mock_pomethods.js` (Cm system,
  near field, FEMA 440 DM method), `timedep.js` (shell creep checkbox).

### Validation (`tests/test_wavec_dynamics.py`)

* 12-mass shear chain against the hand eigenvalues
  `lambda_j = 2k/m (1 - cos((2j-1) pi / (2N+1)))`: the shift returns the 3 nearest modes
  (rel 1e-8); the cutoff keeps exactly 1.
* Linear modal history = direct integration with 9 modes, within 1e-6 of the peak
  (measured 4e-16; the last two samples are excluded because of the end-of-record
  convention).
* NL TH from staged: domain displacement mismatch <= 1e-9 u_max (measured 1e-18), and a zero
  record stays at u_s. A hinged load-control NLS predecessor runs.
* The Takeda / Pivot law equals OpenSees `Hysteretic` on random cyclic paths to 1e-9; FNA vs
  DI on 4 bearings is within 2 % (measured < 1e-5). The BRB law equals Steel4 on full cycles
  to 1e-12 (small noisy reversals differ by up to ~5 % because of Steel4 branch memory); FNA
  vs DI 0.35 %. Bilinear damper 6e-5. T/C isolator FNA 0.8 % vs DI; the device carries 0
  axial / shear in uplift, plus the hold-down.
* RS with interpolated + override damping equals independent modal statics + Der Kiureghian
  CQC (1e-6), with the EC8 eta correction. Composite ratios equal the energy-share hand sums.
  Steady-state receptance with per-mode zetas matches to 1e-6.
* Pushover RZ control: final base shear = theta / (rz per kN) (linear, 1e-6).
* ASCE 41 Cm (Table 7-4) and the TL branch `SD1 TL / T^2`. FEMA 440 C1 = 1 + 2/15 and
  C2 = 1.02 at R = 3, Te = 0.5 s, site D; R_max Eq. 5-4 by hand.
* Shell creep: cantilever slab tip deflection = elastic x (1 + phi) within 2 %. Free slab
  shrinkage = eps_sh L and eps_sh B (1e-6), with zero reactions.
* Legacy hashes (3-story building run / FNA / pushover performance / ASCE 41 / step-staged
  slab) are identical. Playwright on port 8726: mock + live 32/32.

## Wave C: distributed frame moments, frame piers, floor mesh "none", per-area edge constraints, beam LLR, static eccentricity overrides, diaphragm Fpx

All new keys are written ONLY when set, so a legacy model's JSON and every
legacy result are byte-identical. This was checked by hashing the model, the
results and the LLR / pier tables of four representative models before and
after the change.

### Distributed frame moments, local 1 / 3 frame loads (`core/frame_loads_ext.py`, `engine/frame_dist_loads.py`)

* `MemberLoad.moment_dist: "" | "uniform" | "trapezoid"` on `kind: "moment"`
  (`""` = the legacy concentrated couple).  Distributed: `w` (and `w2`) in kN·m/m
  over the fraction span `[a, b]`; `direction` in
  `loads_ext.MEMBER_MOMENT_DIRECTIONS` (local_1/2/3 = local_x/y/z, or global_x/y/z;
  right-hand rule).  Validation: `0 <= a <= b <= 1`; not projected;
  `moment_dist` only on kind moment.
* `MEMBER_LOAD_DIRECTIONS` gains `"local_1"` (axial, i → j) and `"local_3"`
  (`"local_y"` stays local 2) for udl, trapezoid and point loads.
* Engine: each segment gets the span record
  `("mtrap", (mx1,my1,mz1), (mx2,my2,mz2), xa, xb)` and goes through the
  exact fixed-end path.
  * Consistent load = ∫ m(x) × the section-rotation field of the exact
    homogeneous solution (Hermite, or the exact Timoshenko `mu` functions;
    twist is linear).  3-point Gauss is exact here because the integrand is
    cubic.
  * Release condensation (`core/releases.py`) and auto-mesh segments apply
    unchanged.
  * Stations: the couple resultant ∫ m adds to T / M2 / M3; V and N are
    unchanged.
  * Deflections: ∫ m0 (ξ − s)²/2.  The Timoshenko shear term uses the
    force-only moment, because a couple does not change Q.
* Curved members split distributed moments per chord.  Nonlinear-static
  stepping scales `mtrap` records.  The reaction balance and the applied-load
  totals count local 1 / 3 loads.
* DB edit: `frame_moment_loads` (Frame Loads - Moment) and
  `frame_distributed_moments` (Frame Loads - Distributed Moment, quantity
  `line_moment` kN·m/m).
* Pinned in `tests/test_wavec_frame_loads.py`:
  * uniform torque t on a cantilever: T(x) = t(L − x) and tip twist
    tL²/(2GJ), also when auto-meshed;
  * fixed–fixed beam, uniform m about local 3: V = m, end moments 0;
  * cantilever: |M3| = m(L − x), tip θ = mL²/(2EI), δ = mL³/(3EI);
  * simply supported beam, trapezoidal m: |M3| = m0x/2 − m0x²/(2L);
  * propped (Mj or Mi released): M3 ≡ 0, prop reaction = m;
  * Timoshenko fixed–fixed: end moments mLφ/(2(1+φ));
  * axial local-1 load: N(x) = w(L − x), u = wL²/(2EA);
  * local-3 udl: tip deflection wL⁴/(8EI22).

### Frame pier labels (`core/frame_piers.py`, `engine/frame_piers.py`)

* `FrameMember.pier` applies to columns and inclined frames only.
  Horizontal beams are refused because they are spandrels.
  `BuildingModel.pier_labels` holds the defined labels.  Frames and walls
  sharing a label form one pier per story.
* `results["piers"][case][label][story] = {P, V, M}`, with the same
  conventions as walls:
  * each labelled frame crossing the story-bottom elevation is cut there
    from its station forces;
  * P = ΣF_z (compression positive), V = −ΣF·ĥ,
    M = Σ(s − s̄)F_z + M·(ĥ × ẑ);
  * ĥ and s̄ come from the label's first wall cut on that story when the
    pier has walls.  Otherwise they come from the plan line through the
    columns (pointing toward +X or +Y) and the area-weighted column
    centroid.  A single column uses its local-2 axis (V = V2, M = M3).
  * Computed for static cases and additive combos; live-load reduction
    picks up frame piers.
* DB edit: `pier_assignments` (Pier Label Assignments, columns and walls).
* Pinned:
  * two-column portal: V = F, P = ΣN, M = Fh;
  * wall plus boundary column: V = F, M = Fh about the wall centroid;
  * multistory moments and combos.

### Floor auto mesh options: no auto mesh and DB columns (`core/shellopts.py`, `core/wavec_tables.py`)

* `floor_mesh.mode` gains `"none"` (ETABS No Auto Mesh).
  * A 4-corner slab becomes ONE element.
  * A polygon slab gets the coarsest conforming mesh: lines only through its
    vertices and the in-plane frame, link, support and load points.
  * `"none"` cannot be combined with `max_size`, `mesh_divisions`, or
    openings on 4-corner slabs.  The mesh study leaves these slabs alone.
* `shell_objects` gains these columns:
  * Floor Mesh (blank = no options);
  * Floor Max Size and Mesh at Beams / Walls / Grids, stored only while a
    mode is set;
  * Edge Constraint (model | yes | no).

### Per-object auto edge constraints (`core/edge_constraints_ext.py`)

* `ShellRegion.edge_constraint: null | true | false` (null = the model
  switch).
* A hanging-node chain is built when the area that owns the edge is
  effectively on, or when any area owning a hanging node is explicitly on.
* With no override this is exactly the model-wide v0.22 zipper.
* Pinned against the v0.22 tip deflections: 1.218739e-4 tied, 1.526269e-4
  untied.

### Live-load reduction for beams (`core/llr_beams.py`)

* `llr_settings.apply_to` may include `"beams"`; the default is unchanged.
  Optional `beam_params {KLL}` (default 2) and `beam_areas {uid: m²}`.
* Tributary area At = L × (half the spacing to the nearest overlapping
  parallel beam on each side, or the full distance to the floor bounding box
  where there is none); n = 1.
* Factors:
  * ASCE 7: 0.25 + 4.57/√(KLL At), limited to [0.5, 1];
  * NBCC: as for columns;
  * EC1: αA;
  * IS 875 §3.2.2: 5 % per 50 m² (at most 25 %);
  * user curve.
* Beams appear in the factor and reduced-force tables (every station) and in
  `apply_to_results`.

### Static seismic eccentricity overrides (`core/static_ecc.py`)

* `LoadPattern.ecc_overrides {story | "story/diaphragm" | "story/(free)":
  ratio | {"length": e}}`.
  * A ratio in [0, 0.5] replaces |ecc|: Mz = F·r·B⊥.
  * A length gives Mz = F·e.
  * The sign comes from the pattern's ecc / ecc_y.
  * Overrides are active only with accidental torsion on.
* Honoured on the rigid-master, semi-rigid slab and named-diaphragm paths.
* DB edit: `load_pattern_ecc_overrides`.

### Diaphragm design forces (ASCE 7 12.10) (`core/fpx.py`)

* §12.10.1.1: Fpx = ΣF_i/Σw_i·w_px, limited to [0.2, 0.4]·SDS·Ie·w_px.
* §12.10.3 alternative: Cpx/Rs·w_px ≥ 0.2 SDS Ie w_px, with Cp0, Cpi, Cpn,
  Γm1, Γm2 and Cs2.  These coefficients were implemented from memory of the
  standard and need to be checked against it.
* `BuildingModel.fpx_settings {SDS, Ie, patterns?, method?, weights?, wpx?,
  Rs?, Cs?, Omega0?, zs?, SD1?}`.
* Results table `diaphragm_design_forces`.
* `generate_patterns` writes one story-force pattern `<src>_Fpx_<story>` per
  level and copies the source's accidental-torsion settings.

### Wave C endpoints (`api/wavec_api.py`)

* ``POST /api/fpx`` body ``{pattern?, SDS?, Ie?, method?, weights?, wpx?, model?}`` -> ``{"reports": [...]}`` (read only).
* ``POST /api/pattern/fpx`` body ``{pattern, SDS, Ie?, prefix?, copy_ecc?}`` -> ``{"model", "patterns", "report"}`` (writes the current model).
* ``POST /api/llr/beams`` body ``{settings?, model?}`` -> ``{"code", "beams": {uid: {At, A1, n, R, L, w_left, w_right}}}``.
* ``POST /api/pattern/ecc-torques`` body ``{pattern, model?}`` -> ``{"rows": [{story, fx, fy, mz_x, mz_y, override}]}``.

### Wave C UI (`js/wavec.js`, `js/mock_wavec.js`)

* New acts:
  * `def-wc-piers`: Define › Groups, Cuts & Labels › Pier Labels…
  * `def-wc-ecc`: Define › Auto Lateral Loads › Eccentricity Overrides…
  * `def-wc-fpx`: Define › Auto Lateral Loads › Diaphragm Design Forces (Fpx)…
  * `asn-wc-fpier`: Assign › Frame › Pier Label…
  * `asn-wc-spier`: Assign › Shell › Pier Label…
  * `asn-wc-floormesh`: Assign › Shell › Floor Auto Mesh Options…
  * `asn-wc-edge`: Assign › Shell › Auto Edge Constraints…
  * `asn-wc-fmoment`: Assign › Frame Loads › Distributed Moments…
* Live Load Reduction Factors (`des-lc-llr`) gains Beams and a beam KLL.
  The Distributed frame load dialog gains Local 1 / Local 3.
* Test hook: `__sky.waveC = {openPierLabels, …}`.
* Mock support:
  * Fpx, beam LLR and eccentricity-torque ports;
  * frame pier forces in mock results;
  * POST /api/model checks;
  * the new DB tables and columns;
  * the mock Diaphragm Design Forces table.


## Wave C links and hinges (link types, effective K/C, P-M hinges, Caltrans / TBI)

Modules: `core/links_c.py` (keys, validation, local axes, laws),
`engine/links_c.py` (OpenSees builders, one hook in the `_build` link
loop), `core/fna_devices_c.py` + `engine/fna_c.py` (FNA devices, hooks in
`engine/fna.py`), `engine/pmm_update.py` (P-M capacity updates),
`design/hinge_tables_c.py` (Caltrans / TBI, registered into
`design/hinge_tables.py`).  Every default is the legacy path: a model that
uses none of the keys below gives byte-identical results.

### Link local axes
`U1` = axis i -> j (`+Z` at zero length); vertical / zero-length link:
`U2 = +X`, `U3 = U1 x U2`; any other link: `U2` in the vertical plane
through the axis pointing up, `U3 = U1 x U2`.  `R1..R3` about `U1..U3`.

### New `LinkMember.link_type` values (`params`)
| type | params | engine |
|---|---|---|
| `linear_coupled` | `K`: 6x6 nested list (local U1..R3), symmetric PSD | exact parallel springs (line + torsional `elasticBeamColumn`s placed by `-jntOffset`) |
| `multilinear_elastic` | `curves`: `{"U1".."R3": [[d, F], ...]}`, `k_lin?`: 6-list | `ElasticMultiLinear` per local DOF |
| `multilinear_degrading` | `points`, `points_neg?`, `damage1?=0`, `damage2?=0`, `unload_beta?=0.5`, `kv?` | `Hysteretic` |
| `multilinear_concrete` | `points`, `points_neg?`, `pinch_x?=0.8`, `pinch_y?=0.2`, `damage1?`, `damage2?`, `unload_beta?=0`, `kv?` | `Hysteretic` |
| `multilinear_isotropic` | `points` (exactly 2), `kv?` | `Steel01` isotropic |
| `multilinear_brb` | `points` (2), `points_neg?` (2, same yield point), `R0?=20`, `iso_b?=0`, `iso_rho?=1`, `kv?` | `Steel4` asym kinematic (+ `-iso`) |
| `hdr_isolator` | `Gr Kbulk D1 D2 ts tr n a1 a2 a3 b1 b2 b3 c1 c2 c3 c4`, `kc? phi_m? ac?` | `HDR` (Grant model) |

* `linear_coupled` acts on `d = [u_j(carried to i) - u_i; th_j - th_i]`
  (moment equilibrium for a finite length); reactions equal `K_g d`
  exactly, `K_g = T^T K T`.  Rejected: non-symmetric, non-PSD, all-zero,
  and a non-zero trace of the coupling block `K[0:3, 3:6]` (screw /
  axial-torsion coupling, not realisable with OpenSees elements).
* plastic options: a link with a horizontal component acts axially (one
  material on U1); vertical / zero-length = both shears + elastic `kv`.
  Peak-oriented rule (Takeda / pivot / degrading / concrete) = OpenSees
  `Hysteretic`: unload at `k0 (u_max/d1)^-beta`, reload toward the
  opposite peak through the pinch point `(u0 + pinch_x (d* - u0),
  pinch_y F_t)`, `d* = u_t - (1 - pinch_y) F_t / k_u`.
* isotropic: `Steel01(fy, k0, b, a1, 1, a1, 1)` with `a1 = 2b (d2/d1 - 1)
  / (d2 / (2 d1))^0.8`: a reversal from the backbone end `(d2, F2)` yields
  at exactly `-F2` (ETABS isotropic); other amplitudes follow Steel01's
  0.8-power law.
* HDR elastic component `F = a1 u + a2 u^3 + a3 u^5` (b = c = 0).

### New optional keys on existing types
* `fp_isolator` / `friction_spring` / `tc_fp_isolator`: `mu_fast` + `rate`
  -> `mu(v) = mu_fast - (mu_fast - mu) exp(-rate |v|)` (OpenSees
  `VelDependent`); plus `n_slow` / `n_fast` (`mu_max_fact?=2`) ->
  `VelNormalFrcDep` (`mu_slow = mu N^(n_slow-1)`, `mu_fast = mu_fast
  N^(n_fast-1)`).  A per-surface key (`mu3`, `mu_t`) keeps the same
  fast/slow ratio.
* `tc_fp_isolator`: `mu3`, `R3` (U3 direction; `mu` / `R` act along U2;
  uncoupled per direction, FNA only: direct integration raises
  `NotImplementedError`), `mu_t` (+ `R_t`, default `R`): friction in
  tension `mu_t N_t + N_t u / R_t` with `N_t = kt (d - gap_t)` (needs
  `kt > 0`); built as a second `singleFPBearing` whose axial material is
  `-kt` past the gap (read as compression) plus a `2 kt` hook.
* every non-elastic type: `k_eff` (6-list, local, >= 0, one > 0) and
  `c_eff` (6-list, needs `k_eff`).

### Effective (linear) stiffness and damping
A link with `k_eff` is replaced in every LINEAR build (`_build` without
`hinge_case`: linear static, modal / Ritz, RS, linear time history,
buckling, steady state / PSD) by a linear link with `Elastic(k_eff,
c_eff)` per local DOF (`zeroLength` / `twoNodeLink` with `-orient`);
NONLINEAR builds (pushover, nonlinear static, nonlinear TH) keep the
nonlinear property; FNA keeps `k_eff` as the linear part of its basis.
`asm.link_effective[uid] = {ele, k_eff, c_eff, dirs}`.

### FNA devices
`linear_coupled` (basis), `multilinear_elastic` (translational curves),
Takeda / pivot / degrading / concrete (`PeakOriented`, identical to
`Hysteretic` without damage; damage1/2 > 0 raise), `brb` and
`multilinear_brb` (sharp bilinear asymmetric kinematic), `damper_bilinear`
(Maxwell + bilinear dashpot), friction bearings with `mu(v, N)`, and the
T/C isolator (per direction, tension friction).  `hdr_isolator` and
`multilinear_isotropic` raise `NotImplementedError` (use direct
integration).

### Hinges
* PMM `axial.source = "analysis"`: P starts at the gravity value and is
  updated after EVERY committed step from the hinge's own axial force;
  `drop_strength` of PMM / M2M3 may now be `"drops"` (C -> D drop, D -> E,
  residual `1e-3` past E).  Method (`engine/pmm_update.py`): the
  Elliptical section's yield moments are OpenSees parameters (`Fy1`,
  `Fy2`); after step k they are set to `r(dp_k) * M_y(P_k)` (surface at
  `P_k`, `r` = `core.interaction_hinges.capacity_ratio` of the running
  max resultant plastic deformation), so the capacity lags P by one
  step.  Hinge detail adds `capacity_update`, `axial_update` and per-step
  `P_step`, `capacity_M2`, `capacity_M3`, `capacity_ratio` (entry k = the
  capacity used by step k+1).
* auto hinge `auto.axial_update: true` (PMM only; written only when true):
  the auto P-M2-M3 hinge becomes ONE coupled interaction hinge whose
  surface is the table generator's yield moment sampled at 81 axial
  loads (0 / 90 degree curves), backbone ratios and absolute acceptance
  from the M3 spec at the gravity P, `axial.source = "analysis"`,
  `drop_strength = "drops"`.
* codes `"Caltrans"` (`rc_column`: M3, M2, PMM) and `"TBI"` (`rc_beam`,
  `rc_column`, `rc_coupling_beam`), formulas in
  `design/hinge_tables_c.py`; every table `verified: false`
  (`preliminary`), see REVIEW_FLAGS.

### Tests
`tests/test_links_wavec.py`, `tests/test_hinges_wavec.py`.
