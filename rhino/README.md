# SkyFrame for Grasshopper (Rhino 8)

A compiled Grasshopper plugin that links Rhino 8 and Grasshopper to the **SkyFrame** building analysis
app. You send geometry from Grasshopper (or whole Rhino layers) to SkyFrame as frames, walls, slabs, shells
and supports. You can preview the change before you apply it, and choose merge or replace. You can run the
analysis and read the results back into Grasshopper data trees, along with a deformed shape.

The plugin talks to the app over the local HTTP API in the shared exchange spec (`skyframe.exchange/1`,
spec §6). The SkyFrame app has to be running on the same machine.

```
rhino/
├── SkyFrame.Exchange/          netstandard2.0: DTOs, JSON, HTTP client, units, stable ids, results (no Rhino)
├── SkyFrame.Grasshopper/       the .gha (net7.0 + net48): thin Grasshopper components
├── SkyFrame.Exchange.Tests/    xunit tests (dotnet test)
├── samples/                    sample payloads (used by the tests AND by tools/send_sample.py)
├── tools/send_sample.py        posts the samples to a running SkyFrame (integration check)
├── tools/make_icons.py         regenerates the 24×24 icons
├── manifest.yml                Yak (Package Manager) manifest
└── build.sh / build.ps1        build + test + package
```

---

## 1. Install

### Option A: Package Manager (.yak)
1. Get `skyframe-<version>-rh8_0-any.yak`. You can download it from the **Build Grasshopper plugin** GitHub
   Actions run (artifact `skyframe-grasshopper`), or build it yourself with `rhino\build.ps1`.
2. Close Grasshopper, then run this in a terminal:
   `"C:\Program Files\Rhino 8\System\Yak.exe" install path\to\skyframe-<version>-rh8_0-any.yak`.
   Once the package is published to the McNeel server, you can instead search for **skyframe** with
   `_PackageManager` in Rhino.
3. Restart Rhino and open Grasshopper. A **SkyFrame** tab appears.

The package contains `net7.0/` (the default Rhino 8 runtime) and `net48/` (Rhino started with
`SetDotNetRuntime → NETFramework`). Rhino loads the build that matches its runtime.

### Option B: drag and drop the .gha
1. Take `dist/net7.0/SkyFrame.Grasshopper.gha`. Use `dist/net48/…` only if your Rhino 8 runs on .NET Framework.
   The `.gha` is self-contained (the exchange library is compiled in), so you only need this one file.
2. On Windows, right-click the file, choose **Properties**, tick **Unblock** and click OK. Files downloaded
   from the internet are blocked otherwise.
3. Drag the `.gha` onto the Grasshopper canvas. Or copy it into *File → Special Folders → Components Folder*
   and restart Rhino.

### Uninstall
`_PackageManager` → SkyFrame → Uninstall. Or delete the `.gha` from the Components folder.

---

## 2. Components (tab **SkyFrame**)

These descriptions stand in for screenshots. Each one describes what you see on the canvas.

### 1 Link

**SF Connect** shows a plug icon (blue and orange dots joined by a grey line).
- Inputs: `Url` (default `http://127.0.0.1:8600`; you can also type just the port the desktop app prints),
  and `Refresh` (connect a Button).
- Outputs: `Status`, `Model` (name of the open SkyFrame model), `Connected`, `Url` (normalised, to wire into
  Send and Results), `Sessions`, and `Has Results`.
- The component bubble reads *connecting…*, then *connected* or *offline*. It calls `GET /api/exchange/status`
  asynchronously, so the canvas never freezes.

**SF Send** shows an orange arrow pointing into a blue block.
- Inputs:
  - `Objects` (tree)
  - `Url`
  - `Session` (optional)
  - `Mode`: `merge` (default) or `replace`
  - `Preview`: a dry run with `/api/exchange/preview`, which leaves the model untouched
  - `Send`: a Button or a Toggle
  - `Auto`: live link, debounced to 750 ms
  - `Run`: run the analysis after applying
  - `Mapping`: name of a mapping profile stored in the model
  - `Layer`: layer name given to Grasshopper objects (default `Grasshopper`)
- Outputs:
  - `Report`: text such as *APPLIED merge + run (14:02:11) / created: 12 members, 4 shells, 6 joints /
    updated: … / deleted: …*
  - `Created`, `Updated`, `Deleted`
  - `Warnings`
  - `Session`
  - `Objects`: the objects as sent, with their final ids
  - `Revision`: goes up by one after each successful apply
  - `Payload`: the JSON that was sent
- The bubble reads *sending…*, *sent*, *live* or *error*.
- Sending runs on a worker thread. When the response arrives, the component re-solves itself and shows the
  report. A Toggle that stays True re-sends only when the payload changes. A failed send is not retried in a
  loop; press Send again to retry.

**SF Results** shows bars under an orange deflected arc.
- Inputs: `Objects` (the **Objects output of SF Send**), `Url`, `Session` (Send's `Session`), `Case`,
  `Quantity`, `Scale` (deformed shape, default 50), `Revision` (Send's `Revision`), and `Refresh`.
  - `Quantity` is one of `displacements`, `member_forces`, `reactions`, `periods`, `drifts` or `story`.
  - Because of the `Revision` input, results are fetched again automatically after every apply.
- Outputs:

  | Output | Contents |
  |---|---|
  | `Ids` | `{path;i}` holds the id of each input object. Branches mirror the Send input tree. |
  | `Values` | displacements and reactions: `{path;i;node}` holds `[ux,uy,uz,rx,ry,rz]` or `[FX..MZ]`. member_forces: `{path;i;c}` holds component *c* (`N,V2,V3,T,M2,M3`) at each station. periods: `{0}`. drifts and story: `{row}`, with the story names in `Ids {0}`. |
  | `Labels` | Names of the values. |
  | `Stations` | Station positions along each member. |
  | `Vectors` | Translation or reaction vectors. |
  | `Deformed` | Deformed polylines, meshes and points, scaled by `Scale`. |
  | `Info` | Case, units and the time of the fetch. |

- Units: lengths are converted to the **Rhino document units**. Forces stay in kN, moments in kN·m,
  rotations in rad and periods in s. Drift ratios have no unit.

### 2 Objects

**SF Frame**: input `Curve`, which can be a line, polyline, arc or NURBS curve.
- Other inputs: `Role` (`auto`, `column`, `beam`, `brace` or `frame`), `Section`, `Material`, `Angle` (degrees),
  `Release I` and `Release J`, and `Story`.
- Releases take `000011`, `pinned` or `none`. The order is `[P,V2,V3,T,M2,M3]`.
- How each curve type is sent:
  - Lines become `line`.
  - Polylines become `polyline`; SkyFrame makes one member per segment.
  - Open arcs become `arc` (start, mid, end).
  - Anything else becomes `nurbs` with its control points, weights and knots, plus `samples` taken at the
    document tolerance. When a mesh size is set, it caps the distance between samples.
- `auto` leaves out the `role` field, so SkyFrame classifies the member by orientation, UserText and layer
  mapping.

**SF Shell**: input `Geometry`, which can be a brep, surface, extrusion or mesh.
- Other inputs: `Role` (`auto`, `wall`, `slab` or `shell`), `Shell Section`, `Thickness`, `Mesh Size`,
  `Material` and `Story`.
- **Each brep face becomes one SF object.** How each face is sent:
  - Planar faces, trimmed or not, become a `polygon` with an outer loop and holes. Curved trim edges are
    sampled at tolerance.
  - Freeform faces become a `nurbs_surface` and always include a `mesh`. The mesh comes from
    `Mesh.CreateFromBrep` with `MaximumEdgeLength = Mesh Size` and a square grid, and triangle pairs are then
    merged into quads, so the result is quad-dominant.
  - Meshes pass through as they are.
- The `Mesh` output shows the analysis mesh so you can check it.

**SF Support** (red triangle) and **SF Joint** (orange node): input `Point` and `Restraint`.
- `Restraint` takes `111111`, `111000`, `fixed`, `pinned`, `roller` or `free`. The order is
  `[UX,UY,UZ,RX,RY,RZ]`.
- Support defaults to fixed. A joint has no restraint unless you give one.

**SF Layer Objects** (stacked layers icon): input `Layer`, which takes full paths such as
`Structure::Beams` and allows `*` and `?` wildcards.
- Other inputs: `Sublayers`, `Live`, `Mesh Size` and `Refresh`.
- It references every curve, surface, brep, extrusion, mesh and point on the layer, along with each object's
  **UserText** (`sf_role`, `sf_section`, `sf_release_j`, …).
- Ids are the **Rhino object GUIDs**. A brep with several faces gets `<guid>/<faceIndex>`.
- With `Live` on, the component re-reads the layer about 300 ms after you add, delete, move or edit objects.

### Stable ids and sessions
- Grasshopper geometry gets the id `<SF Send InstanceGuid>/<branch path>/<index>`, for example
  `9c1b…3c4d/0;2/5`. The id depends on the position of the object in the tree wired into Send. If you keep the
  tree structure stable, SkyFrame keeps everything you assigned in the app (loads, sections, hinges) on the
  next merge.
- Referenced Rhino objects keep their GUID for as long as they exist in Rhino.
- The default session is `<definition DocumentID>/<SF Send InstanceGuid>`, so each Send component is its own
  session. Deletes in merge mode only affect the session that sent the objects. Objects drawn directly in
  SkyFrame are never deleted.

---

## 3. Example definition (`portal.gh`, described)

1. **Geometry.** A `Line` from (0,0,0) to (0,0,3.5) for the column. A `Polyline` along the top for the
   beams. A `Surface` 6 × 6 m at z = 3.5 for the slab. A `Point` at each column base.
2. **SF Frame** ×2: one for the column with `Role=column`, `Section=W14X90`. One for the beams with
   `Role=beam`, `Section=W18X35` and `Release J=000011`.
3. **SF Shell**: `Role=slab`, `Shell Section=SLAB200`, `Thickness=0.2`, `Mesh Size=1.0`.
4. **SF Support** on the base points, with the default `fixed`.
5. Merge the four `Objects` outputs into one tree, using **Entwine** so each component gets its own branch
   `{0}`…`{3}`. Wire the tree into **SF Send.Objects**. Add a **Button** on `Send`, a Toggle on `Auto`
   (live link) and a Toggle on `Run`.
6. **SF Connect** → `Url` → SF Send and SF Results.
7. **SF Results**: wire Send's `Objects`, `Session` and `Revision`. Set `Case=DEAD`,
   `Quantity=displacements` and `Scale=100`. Connect `Deformed` to a Custom Preview, and `Values` to a Panel
   or a Gradient.
8. Open SkyFrame. Click **Preview** first and read the report: what would be created and updated, and the
   warnings. Then click **Send**. Move a point in Rhino: with `Auto` on, the model updates within about a
   second. Each apply is one undo step in SkyFrame.

To link a whole Rhino layer, wire **SF Layer Objects** (`Layer=Structure*`, `Sublayers=true`) into Send
instead of, or as well as, the Grasshopper geometry. Put `sf_role` and `sf_section` UserText on the objects,
or use a mapping profile in SkyFrame.

---

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| **SF Connect: "Cannot reach SkyFrame at http://127.0.0.1:8600"** | Check that the app is running. The **desktop app picks a free port** and prints it in its console or title. Type that port into `Url`. `skyframe --server-only` uses **8600**. |
| Connects, but reports "non-JSON response" | Another program is using that port. Check the port. |
| **Firewall prompt or blocked** | The link only uses `127.0.0.1`, so nothing leaves your machine. If corporate security software blocks loopback HTTP, allow `SkyFrame.exe` (or `python.exe`) to listen on localhost. A proxy set in Windows (`HTTP_PROXY`) can also intercept requests; add `127.0.0.1;localhost` to the proxy bypass list. |
| **Model is 1000× too big or small** | The payload carries `source.units` from the **Rhino document units** (*Document Properties → Units*). Model in the units you mean. Rhino units outside m, mm, cm, ft and in (km, yards, …) are exported as metres automatically. Thickness and mesh size are typed in document units. |
| Results look like millimetres but should be metres | SF Results converts lengths to the document units. Forces and moments are always kN and kN·m. |
| Members are not connected to the slab | SkyFrame merges joints within `source.tolerance`, which is the document absolute tolerance. Snap your geometry, or raise the tolerance. |
| Every send recreates objects, so assignments made in SkyFrame are lost | Your tree structure changed, which changes the GH ids, or `Mode=replace` is set. Keep the tree stable: use Entwine with fixed inputs and avoid Flatten before Send. Use `merge`. |
| "Nothing to send" | The `Objects` input is empty or holds geometry that is not an SF object. Wire geometry through SF Frame, SF Shell or SF Support first. |
| Plugin does not load (`.gha` blocked) | Unblock the file (Properties → Unblock). Use the `net48` build if Rhino runs on .NET Framework. |
| Canvas feels slow with Auto on | Sends are debounced to 750 ms and run in the background. A huge layer combined with Auto can still serialise often. Use the Button for big models. |

---

## 5. Build from source

Needs the .NET 8 SDK on Windows, macOS or Linux. Rhino is only needed to *run* the plugin.

```bash
cd rhino
dotnet build -c Release        # SkyFrame.Grasshopper/bin/Release/{net7.0,net48}/SkyFrame.Grasshopper.gha
dotnet test -c Release         # xunit: serialization vs samples, units, ids, results, HTTP client (stub server)
./build.sh                     # build + test + stage dist/ (+ .yak if the yak CLI is available)
pwsh ./build.ps1               # same on Windows; finds Rhino 8's Yak.exe automatically
```

To check the integration against a running SkyFrame:

```bash
python rhino/tools/send_sample.py --url 8600                 # status + preview of the samples
python rhino/tools/send_sample.py --apply --run --results DEAD
```

CI: the **Build Grasshopper plugin** workflow (`.github/workflows/build-grasshopper.yml`) runs only when you
start it manually (`workflow_dispatch`).

### Spec conventions (optional fields the plugin fixes)
- `nurbs.knots` follows the Rhino/openNURBS convention: control-point count + degree − 1 values, the same as
  rhino3dm.
- `nurbs_surface` body: `degree:[du,dv]`, `count:[nu,nv]`, `cv` (nu·nv points, u-major), `weights`,
  `knots_u`, `knots_v`, `closed:[u,v]`, and `mesh:{v,f}`. SkyFrame uses only `mesh`.
- `props.thickness` and `props.mesh_size` are lengths in `source.units`, like the coordinates.
- `role` is omitted for `auto`. `props` is omitted when empty. `attrs` is always present.
- Grasshopper ids write the branch path as `0;2`, without braces. A multi-face Rhino brep gets
  `<guid>/<faceIndex>`.
- Default session: `<GH DocumentID>/<SF Send InstanceGuid>`.
