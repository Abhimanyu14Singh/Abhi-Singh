# CSI, steel and Revit interoperability

These formats sit next to E2K, IFC, DXF and Excel (see CONTRACT "Model
exchange").

* **Menus.** File › Import ▸ and File › Export ▸ (`js/interop.js`).
* **HTTP.** `POST /api/interop/export|import/<fmt>`.
* **Python.** `skyframe.io.s2k`, `f2k`, `sdnf`, `cis2`, `idea_iom` and
  `ifc_write(..., revit=True)`.

| Format | Export | Import | Module |
|---|---|---|---|
| SAP2000 `.s2k` (CSI database tables) | ✓ | ✓ | `io/s2k.py` |
| SAFE floor `.f2k` | ✓ | ✓ (one-story model) | `io/f2k.py` |
| SDNF v3.0 | ✓ | ✓ | `io/sdnf.py` |
| CIS/2 LPM6 (STEP) | – | ✓ | `io/cis2.py` |
| IDEA StatiCa IOM XML | ✓ (zip of model + results) | – | `io/idea_iom.py` |
| IFC4 for Revit | ✓ | ✓ (ordinary IFC reader) | `io/ifc_write.py` + `io/ifc_revit.py` |

`.s2k` and `.f2k` share `io/csi_tables.py`. A file is a list of `TABLE:
"NAME"` blocks of `Field=Value` records; a ` _` at the end of a line
continues the record. This is the same CSI family as ETABS `.e2k`, and the
writers reuse the e2k unit system (`exchange_common.Units`) and its section
classification. CSiBridge `.b2k` uses the same table format. SkyFrame has
no bridge objects, so `.s2k` is also the route into CSiBridge.

## SAP2000 `.s2k`

Units follow `CurrUnits`, for example `"KN, m, C"` or `"Kip, in, F"`.

* **Properties.**
  * Materials use tables 01, 02, 03A and 03B.
  * Frame sections use the shape plus `t3 t2 tf tw t2b tfb`, the exact
    properties and the eight modifiers. Database shapes add `FromFile` /
    `DBSectName`.
  * Area sections use their type.
* **Geometry.** Joints, frame and area connectivity, and assignments:
  local axes, releases with partial fixity, length offsets and auto mesh.
* **Supports and mass.** Restraints, uncoupled springs and added masses.
* **Rigid story diaphragms.** Each becomes a diaphragm constraint named
  after its story. Story forces and explicit story masses sit on a special
  joint `~D<story>` at the centroid of the story's joints.
* **Loads.** Load patterns with their self-weight multiplier, joint
  forces, distributed / point / moment frame loads (projected directions
  included) and uniform area loads.
* **Mass, cases and combos.** The mass source, linear static and modal
  cases, combinations of every type, and groups.
* **Extensions.** Two extension tables carry what SAP2000 cannot:
  * `SKYFRAME 01 - STORIES` holds the story names and heights;
  * `SKYFRAME 02 - SHELL TYPES` holds SkyFrame's legacy `shell` element.

  SAP2000 reports both as unrecognised and continues.
* **Round trip.**
  * The quick building gives identical static results and periods.
  * A model with an AISC shape, parametric I and channel, releases,
    partial fixity, rigid offsets, a meshed slab and a membrane deck gives
    an identical model and identical static results.
  * In kip-in, the model is equal to 1e-12.
* **Not written.** These are listed in `report.skipped`: openings, links,
  line and area springs, hinges, insertion points, joint offsets,
  RS / TH / pushover / staged / buckling cases, thermal loads, ground
  displacements and accidental torsion.

## SAFE floor `.f2k`

`export_f2k(model, story, results=…)` writes one floor. Floor points sit at
`Z = 0`.

* **Objects.**
  * The story's slabs.
  * Its beams.
  * The columns below the floor, with a fixed far end (pinned for pinned
    bases).
* **Floor loads.**
  * Surface loads.
  * Distributed and point loads on the floor beams.
  * Joint loads at floor points.
* **Column reactions from above** (needs results; the server runs the
  analysis when none are given).
  * For every column standing on the floor, the export writes the force
    the column exerts on its floor point: minus its end force, rotated to
    global axes. These go in as `Fx Fy Fgrav Mx My Mz` point loads, one
    per static case.
  * They go into the pattern of the same name, or into `"<case> (above)"`
    when the case name is not a pattern.
  * Check: with the quick building, the DEAD reactions on Story1 equal
    25 kN/m × the total length of the beams above, and the EQX shears
    equal the story forces above.
* **Cases and combos.** Load cases and combinations are written too.
* **Reading back.** `read_f2k` rebuilds a one-story model and returns the
  same slab corners, column positions and load totals.

## SDNF v3.0 (documented subset)

```
Packet 00
"<company>" "<structure>" "<code>" "<date>" "<time>" <rev> "<length unit>" "<force unit>"
Packet 10
<member count>
<id> "<type>" "<piece mark>" "<section>" "<grade>" <rotation> <mirror x> <mirror y> <cardinal point>
<x1> <y1> <z1> <x2> <y2> <z2>
<dx1> <dy1> <dz1> <dx2> <dy2> <dz2>
"<U1..R3 release start, 1 = free>" "<release end>"
```

* **Field values.**
  * `type` is one of Column, Beam, Vertical Brace, Horizontal Brace.
  * `section` is the database name for database sections; `grade` is the
    material name.
  * Units are Meters, Millimeters, Inches or Feet.
* **Reading.**
  * The reader skips other packets with a warning.
  * A section is resolved, in order, from the current model, the steel
    databases (aisc, eurocode, bs, as_nzs, is808), or a 0.3 m placeholder
    with a warning.
* **Round trip.** End points, section, rotation, releases and cardinal
  point come back exact.
* **Caveat.** The record layout follows the SDNF 3.0 packet structure. No
  third-party SDNF files were available to validate against, so check a
  file from your detailing package once before production use.

## CIS/2 (LPM6) reader

The reader takes the attribute positions tolerantly (the first attribute of
the expected kind), so the optional LPM6 item-number and description slots
may be present or absent.

* **Analysis model.** `NODE` + `CARTESIAN_POINT` (with optional
  `BOUNDARY_CONDITION_LOGICAL` restraints, six .T./.F. flags),
  `ELEMENT_CURVE_SIMPLE` (section and material references) and
  `ELEMENT_NODE_CONNECTIVITY` (connectivity number 1/2 = end i/j).
* **Design model.** Used when there are no analysis elements:
  `LOCATED_PART` → `AXIS2_PLACEMENT_3D` (origin, axis) plus
  `PART_PRISMATIC_SIMPLE` (section, length).
* **Sections.** `SECTION_PROFILE_I_TYPE / _CHANNEL / _ANGLE / _T_TYPE /
  _RECTANGLE / _RECTANGLE_HOLLOW / _CIRCLE / _CIRCLE_HOLLOW`.
  * A name found in a steel database or in the current model wins.
  * Otherwise the numeric dimensions are read in attribute order:
    * I, channel, tee: depth, width, web, flange;
    * angle: depth, width, thickness;
    * rectangle: depth, width, wall;
    * circle: diameter, wall.
* **Units.** `SI_UNIT(.MILLI.,.METRE.)` means mm, and inch / foot
  conversion-based units are recognised. Otherwise the file is read in
  metres. The `units` option overrides this.
* **Not read.** Loads, connections and the full LPM6 schema.

## IDEA StatiCa (IOM)

`export_idea_zip` writes `<name>.xml` (OpenModel) and `<name>.results.xml`
(OpenModelResult) in SI base units.

* **Geometry.** Point3D, LineSegment3D with the member LCS, PolyLine3D and
  Segment3D.
* **Materials.** MatSteelEc3 or MatConcreteEc2.
* **Cross-sections.**
  * Rolled\* types with `UniqueName` for database shapes.
  * `Iw` for a parametric I.
  * `Rect` otherwise.
  * Every section also carries the exact `A Iy Iz It`.
* **Members and joints.** Element1D and Member1D, plus a ConnectionPoint
  for every joint where two or more members meet.
* **Loads.** LoadGroupEC, a LoadCase per static case, and a CombiInputEC
  per additive combination.
* **Load effects.** Internal forces at every output station, per static
  case, in the IDEA LCS (x = local 1, y = −local 3, z = local 2): `N = P,
  Qy = −V3, Qz = V2, Mx = T, My = −M3, Mz = M2`. Forces are in N and
  moments in N·m.
* **Check.** On a two-story steel bay, the base forces rotated with the
  written LCS balance the applied 240 kN exactly.

## Revit link

ETABS's practical route to Revit is IFC. File › Export › IFC for Revit
(`export_ifc(model, revit=True)`) writes everything the ordinary IFC export
writes (the default output is unchanged), plus:

* **Reference View contexts.** `Body` and `Axis` geometric representation
  sub-contexts, with the `ReferenceView_V1.2, StructuralAnalysisView`
  header.
* **Levels.**
  * Each `IfcBuildingStorey` is placed at its elevation, which is where
    Revit reads the level height.
  * Each storey also gets `LongName` = the story name.
* **Family types.**
  * One `IfcColumnType` / `IfcBeamType` / `IfcMemberType` per section,
    named the way Revit's structural families spell them: `W14X90`,
    `HSS6X6X1/2`, `IPE300`, `300 x 600mm`, `450mm`.
  * Linked to the members with `IfcRelDefinesByType`.
* **Property sets.** `Pset_ColumnCommon`, `Pset_BeamCommon`,
  `Pset_MemberCommon`, `Pset_WallCommon` and `Pset_SlabCommon`, with
  `Reference`, `LoadBearing = TRUE` and `IsExternal = FALSE`.
* **Materials.** Revit-style names (`Steel ASTM A992`, `Steel ASTM A36`,
  `Concrete, Cast-in-Place - 30 MPa (C30)`, otherwise `<Category> -
  <name>`) with a capitalised IFC Category (Steel / Concrete / …), which
  Revit maps to the structural material class.

### Revit import workflow

1. In SkyFrame, use File › Export › IFC for Revit…. Keep "Physical
   members" ticked. Add "Structural analysis view" when the Revit model
   will also feed an analysis tool.
2. In Revit, use Insert › Link IFC (keeps the model coordinated) or File ›
   Open › IFC (makes editable elements). Before that, set File › Options ›
   File Locations › IFC Options so the "Import IFC class" mapping leaves
   IfcColumn / IfcBeam / IfcMember on Structural Columns / Structural
   Framing.
3. Levels come from the storeys. Use Manage › Project Units in metres or
   millimetres; the IFC is in metres.
4. Family types come from the IfcColumnType / IfcBeamType names. To swap a
   type to a loaded native family, select by type and use Type Selector,
   or run the IFC "Map family types" tool.
5. Analysis members and supports come in as IfcStructural\* entities, which
   Revit shows in the analytical view when it is opened, not linked.

A native Revit add-in (the CSiXRevit equivalent) is a separate product. It
needs the Revit API and a Windows Revit installation, so it cannot be built
or tested here and is out of scope.
