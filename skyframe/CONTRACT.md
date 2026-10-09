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
