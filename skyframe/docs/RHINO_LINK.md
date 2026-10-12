# Rhino and Grasshopper link — user guide

SkyFrame reads structural geometry from **Rhino 8** in two ways:

| Route | What you do | Best for |
|---|---|---|
| **File › Import › Rhino (.3dm)…** | Pick a saved `.3dm` file, map its layers, preview, apply | One-off imports, sharing files |
| **File › Link Rhino File…** | Link a `.3dm` path; after every save in Rhino the status bar says **Rhino: changed — Update** | Iterating on a model drawn in Rhino |
| **Grasshopper plugin** (SkyFrame Connect / Send) | Send geometry straight from a definition to the running app | Parametric studies, free-form surfaces |

All three produce the same `skyframe.exchange/1` payload and go through the same
conversion, so layer conventions, UserText keys and mapping profiles work identically.

Supported geometry: lines, polylines, arcs, NURBS curves, planar and free-form surfaces
(trimmed planar faces with openings), meshes and points. Solids are not structural input:
model the mid-surfaces instead (a closed brep is imported face by face with a warning).

---

## 1. Installing Rhino file support

`.3dm` reading uses the optional [`rhino3dm`](https://pypi.org/project/rhino3dm/) package
(version 8.x, wheels for Windows, macOS and Linux). The desktop installer bundles it. For a
source install:

```
pip install "skyframe[rhino]"        # or: pip install "rhino3dm>=8,<9"
```

Without it, File › Import › Rhino (.3dm)… reports how to install it. Grasshopper sends
JSON, so the GH route works without rhino3dm.

---

## 2. How objects become structure

### Roles

Each Rhino object gets a **role**. The first rule that gives a value wins:

1. **Grasshopper** components set `role` / `props` explicitly.
2. **Rhino UserText** on the object (`sf_role`, `sf_section`, …; see §3).
3. The **mapping profile** rule whose layer pattern matches the object's layer (§4).
4. **Geometry defaults:**

| Geometry | Default role |
|---|---|
| curve, vertical (within 1°) | column |
| curve, horizontal (within 1°) | beam |
| curve, inclined | brace |
| planar surface, horizontal | slab |
| planar surface, vertical | wall |
| other surface / mesh | shell (each face: slab if it tilts ≤ 45°, else wall) |
| point on a layer whose path contains "support", or with a restraint | support (default fixed `111111`) |
| other point | joint (splits the frames passing through it) |

Each property resolves on its own. For example, the role can come from the layer rule while
the section comes from UserText.

Roles: `column, beam, brace, frame` (frame = classify by geometry), `wall, slab, shell`,
`support, joint, link`, and `ignore`, which skips the object (useful for annotation or
reference layers).

### Geometry conversion

| Rhino | SkyFrame |
|---|---|
| line | one frame member |
| polyline | one member per segment (each segment classified on its own) |
| arc (also a NURBS that is an exact arc) | one **curved frame** (chords: one per 10°, or per `mesh_size`) |
| free-form NURBS curve | a chain of straight members along the curve: chord sag ≤ max(tolerance, length/200), or pieces of length ≤ `mesh_size` |
| planar trimmed surface | **polygon shell** with its holes as openings |
| non-planar surface | its render/analysis mesh: one shell per face; warped quads become two triangles |
| mesh | one shell per face (triangles and quads); coincident vertices merged |
| point | support or joint |
| closed planar curve with role slab/wall/shell | polygon shell |

**Units** come from the file (Rhino › Document Properties › Units): mm, cm, m, in, ft, plus
µm, dm, km, yd, mi, mil and µin (converted). Everything is stored in metres. Lengths in
UserText and profiles (`sf_thickness`, `sf_mesh_size`) use the file units.

**Free-form surfaces in a .3dm** need a stored render mesh. Rhino keeps one once a shaded
viewport has displayed the surface, unless the file was saved with *Save small* /
*SaveSmall*. Without a mesh the face is skipped with a warning; save with render meshes, or
send the surface through Grasshopper, which meshes it with your `mesh_size`.

After conversion SkyFrame runs **Model Repair**: merge joints within the file tolerance,
remove zero-length and duplicate frames, snap shell corners to frame ends, and split frames
at joints and at crossings. Frames and shells therefore connect even when the Rhino geometry
is a little off. The repair actions are listed in the report.

### Stories

With the story rule `auto` (default), the elevations of **horizontal beams and slabs**
are clustered (default tolerance 50 mm; set it in the mapping dialog). Each new level
becomes a story named `Story<n>`, unless it matches an existing story. Columns, walls and
braces take the story of their top end. Objects above the highest level get the top story.
Free-form shells (a roof mesh) never create levels. A layer rule or `sf_story` can force a
named story.

---

## 3. UserText keys (Rhino › Properties › Attribute User Text)

| Key | Value | Example |
|---|---|---|
| `sf_role` | role (see §2) | `beam`, `ignore` |
| `sf_section` | frame section: a model section, or any steel database shape (imported automatically) | `W18X50`, `IPE300` |
| `sf_material` | material (a model or library material; a unique prefix like `A992` works) | `A992Fy50` |
| `sf_shell_section` | shell section name; an unknown `SLAB200` / `WALL250` is created with t = 200 / 250 mm | `SLAB200` |
| `sf_thickness` | shell thickness (file units) | `200` |
| `sf_mesh_size` | target mesh / segment size (file units) | `1000` |
| `sf_story` | story name or `auto` | `Level 3` |
| `sf_angle` | frame local-axis rotation (degrees) | `90` |
| `sf_restraint` | support restraints UX UY UZ RX RY RZ | `111000` (pinned) |
| `sf_release_i`, `sf_release_j` | end releases P V2 V3 T M2 M3 | `000011` (moment release) |

Keys are case-insensitive. A bad value is ignored with a warning. Releases: SkyFrame
models the moment release at an end (M2 and M3 together); other release patterns are
approximated, with a warning.

---

## 4. Layer naming and mapping profiles

The **mapping dialog** lists every layer: full path (`Structure::Beams`), colour swatch,
object count. For each layer you can set:

- **Role**
- **Frame section** (type to search the model sections and the steel database)
- **Shell section**
- **Material**
- **Story**

Leave a field on *auto* to let UserText and the geometry decide.

A suggested Rhino layer tree that needs no mapping at all:

```
Structure
  Columns        (vertical lines → columns)
  Beams          (horizontal lines / arcs → beams)
  Braces
  Slabs          (planar surfaces with openings)
  Walls
  Roof           (free-form surfaces / meshes)
Supports         (points → fixed supports)
Annotation       → map to "Ignore"
```

**Profiles** keep a mapping for later. Type a name and click **Save Profile**: the profile
is stored **in the SkyFrame model** (`exchange_mappings`), so it travels with the file.
Pick it next time and click **Load**. Rule patterns are globs on the layer path,
case-insensitive: `Structure::*`, `*Beams*`, `Level?::Slabs`. The first matching rule wins.
Grasshopper can use a profile by name (`mapping: "office"`).

---

## 5. Preview, Apply, Undo

**Preview** shows what will happen to the model, without changing it:

- the number of members, shells and joints to be **created**, **updated**, **deleted** or left **unchanged**;
- new stories;
- warnings;
- one row per Rhino object: role, section, story and status.

**Apply** makes the change as **one undo step**: Edit › Undo removes the whole import.
Tick *Run the analysis after Apply* to solve right away.

### Merge (default) and Replace

Every object remembers where it came from (`source_ref`: session + Rhino object id). On a
re-import or an **Update**, the same object is **updated in place**:

- Its **geometry** follows Rhino.
- Everything assigned **in SkyFrame is kept**: section and material (unless the object
  itself carries `sf_section` / props), loads, releases, groups, hinges, insertion
  points, etc.
- A frame that SkyFrame split, for example at a joint, keeps its pieces; they are moved
  with the frame.
- Point loads and masses at a moved joint move with it.

Objects **deleted in Rhino** are deleted in SkyFrame. This applies only to objects of the
same session (the same file or the same Grasshopper definition), never to objects drawn in
SkyFrame or coming from another file.

**Replace** drops all objects of the session and recreates them from the file. SkyFrame-side
assignments of those objects are lost.

Rhino keeps object ids when you edit and save a file, so merging works across saves.
Copying objects, or rebuilding the file from scratch, creates new ids: those objects are
then new objects in SkyFrame.

---

## 6. The linked workflow

1. **File › Link Rhino File…**, enter the path of the `.3dm` (e.g. `C:\Projects\tower.3dm`),
   then **Link & Import**. Map the layers once (save a profile), then Apply.
2. Keep modelling in Rhino and **save**. SkyFrame checks the file's modification time, and
   the status bar changes from **Rhino: linked ✓** to **Rhino: changed — Update**.
3. Click the indicator. The file is re-read with the same mapping, and the preview shows
   what changed. Apply is one undo step.
4. Optional: *Apply changes automatically* re-imports (merge) on every save without a
   preview, and the UI reloads the model.
5. Click the indicator while linked, then **Unlink** to stop watching the file.

**File › Exchange Sessions…** lists every Rhino file or Grasshopper definition present in
the model, with its object counts and last update. **Remove session objects** deletes one
session's objects in one undo step. The dialog also shows the address for the Grasshopper
**SkyFrame Connect** component; the desktop app prints it at start-up, and the default for
`skyframe --server-only` is `http://127.0.0.1:8600`.

---

## 7. Limits

- No solids (closed breps are imported face by face, with a warning), no SubD, no blocks
  (instances), no point clouds, annotations or hatches. These are skipped and counted in a
  warning. Explode blocks first.
- Curved members are arcs. Other curves become straight segments. Ellipses and splines in
  a polycurve are split per segment.
- A full circle cannot be one curved member. It is imported as segments when it comes as a
  NURBS with samples.
- Frame releases: moment releases only (M2 and M3 together).
- Non-planar polygon faces (beyond 2 × tolerance) are skipped. Send them as meshes.
- Links (`role: link`) are elastic springs; without `props.stiffness` they get 10⁶ kN/m
  (kN·m/rad) on all six DOFs.
- Overlapping objects from two different sessions: the repair keeps the first copy and
  reports the dropped duplicates.
- Story clustering only knows horizontal beams and slabs. Use `sf_story` or a profile story
  rule for anything else.
- The link watches the file's modification time on this computer. A network drive with a
  coarse clock may need a manual **Update**.

---

## CONTRACT

The exact JSON is in **CONTRACT.md › "Rhino / Grasshopper exchange"**. In short:

- `skyframe.exchange/1` payload: `schema`, `source {app, doc, units, tolerance}`,
  `session`, `layers [{path, color}]`, `objects [{id, kind, layer, attrs, role?, props?, geom}]`.
- Optional additions to the shared spec, all ignored when absent:
  - payload `warnings`: reader messages;
  - `props.stiffness` (links);
  - UserText `sf_thickness` and `sf_mesh_size`;
  - role `ignore`;
  - rule keys `thickness`, `mesh_size`, `restraint`;
  - profile `story_tolerance` (m).
- Model fields, emitted only when used:
  - `source_ref {session, id, part}` on members, shells, supports and links;
  - `exchange_mappings {name: {rules, story_tolerance?}}`;
  - `exchange_sessions {session: {app, doc, units, tolerance, last_update, mapping?, joints?}}`.
- HTTP:
  - `GET /api/exchange/status`
  - `POST /api/exchange/preview`
  - `POST /api/exchange/apply`
  - `GET /api/exchange/results`
  - `POST /api/rhino/import`
  - `GET|POST|DELETE /api/rhino/link`

  Plus the helpers:
  - `GET /api/rhino/info`
  - `GET|POST /api/exchange/mappings`
  - `POST /api/exchange/sessions/remove`

  CORS: allowed only for `http(s)://127.0.0.1|localhost|[::1][:port]`.
