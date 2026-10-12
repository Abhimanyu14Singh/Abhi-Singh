"""Build Rhino .3dm test files with rhino3dm (tests of the .3dm import).

``build_frame_3dm(path, ...)`` writes a small structure in MILLIMETRES:

* layer ``Structure::Columns`` (red)  -- 4 vertical lines 0 -> H
* layer ``Structure::Beams``  (blue)  -- 3 straight edge beams at H + one
  arc beam (the 4th edge, bulging out) + a NURBS beam along the diagonal
  (mid-span sag)
* layer ``Structure::Slabs``  (grey)  -- planar slab at H with a 1 x 1 m
  opening (a trimmed planar face: outer + inner loop)
* layer ``Structure::Roof``   (green) -- a mesh roof (warped 3 x 3 grid)
  above the slab, supported by four short posts on ``Structure::Columns``
* layer ``Supports``          (black) -- 4 points at the column bases
* UserText: beam ``B0`` carries ``sf_section = W18X50``; post ``P0`` has
  ``sf_role = brace`` (an override of the layer default)

Returns ``{name: guid}`` of the objects.  ``omit`` drops objects by name,
``dz`` lifts every beam / slab / roof / post (a geometry change), ``dx``
shifts the whole structure in x.
"""

from __future__ import annotations

from typing import Dict, Iterable

L = 6000.0          # mm
H = 3000.0          # mm
ROOF = 1500.0       # mm above the slab


_VAULT_SRC = r"""
import json, os, sys
import rhino3dm as r
x0, z0, with_mesh = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3] == "1"
ns = r.NurbsSurface.Create(3, False, 3, 3, 3, 3)
for u in range(3):
    for v in range(3):
        ns.Points[u, v] = r.Point4d(x0 + u * 1500.0, v * 1500.0,
                                    z0 + (600.0 if u == 1 else 0.0), 1)
for k in range(4):
    ns.KnotsU[k] = 0 if k < 2 else 1
    ns.KnotsV[k] = 0 if k < 2 else 1
bs = r.Brep.CreateFromSurface(ns)
if with_mesh:
    mm = r.Mesh()
    n = 4
    for i in range(n + 1):
        for j in range(n + 1):
            q = ns.PointAt(i / n, j / n)
            mm.Vertices.Add(q.X, q.Y, q.Z)
    for i in range(n):
        for j in range(n):
            k = i * (n + 1) + j
            mm.Faces.AddFace(k, k + n + 1, k + n + 2, k + 1)
    bs.Faces[0].SetMesh(mm, r.MeshType.Render)
sys.stdout.write(json.dumps(bs.Encode()))
sys.stdout.flush()
os._exit(0)
"""


def vault_encoded(x0: float, z0: float, render_mesh: bool = True) -> dict:
    """Encoded brep of a vault face (+ a stored render mesh).

    rhino3dm 8's ``BrepFace.SetMesh`` takes ownership of the mesh it is
    given, so the mesh is freed twice when Python collects both objects
    (a segfault).  The face is therefore built in a child process that
    leaves with ``os._exit`` right after encoding it; the caller decodes
    it (``CommonObject.Decode`` keeps the stored render mesh)."""
    import json
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-c", _VAULT_SRC, str(x0), str(z0),
         "1" if render_mesh else "0"],
        check=True, capture_output=True, text=True, timeout=120)
    return json.loads(out.stdout)


def build_frame_3dm(path: str, *, omit: Iterable[str] = (), dz: float = 0.0,
                    render_mesh: bool = True,
                    units: str = "Millimeters", dx: float = 0.0) -> Dict[str, str]:
    import rhino3dm as r
    omit = set(omit)
    f = r.File3dm()
    f.Settings.ModelUnitSystem = getattr(r.UnitSystem, units)
    f.Settings.ModelAbsoluteTolerance = 0.01
    top = r.Layer()
    top.Name, top.Color = "Structure", (90, 90, 90, 255)
    i_top = f.Layers.Add(top)
    top_id = f.Layers.FindIndex(i_top).Id

    def child(name, color):
        ly = r.Layer()
        ly.Name, ly.Color, ly.ParentLayerId = name, color, top_id
        return f.Layers.Add(ly)
    i_col = child("Columns", (220, 30, 30, 255))
    i_beam = child("Beams", (30, 60, 220, 255))
    i_slab = child("Slabs", (160, 160, 160, 255))
    i_roof = child("Roof", (30, 160, 60, 255))
    sup = r.Layer()
    sup.Name, sup.Color = "Supports", (0, 0, 0, 255)
    i_sup = f.Layers.Add(sup)
    ids: Dict[str, str] = {}

    def attr(layer, **user):
        a = r.ObjectAttributes()
        a.LayerIndex = layer
        for k, v in user.items():
            a.SetUserString(k, v)
        return a

    def P(x, y, z):
        return r.Point3d(x + dx, y, z)
    z1 = H + dz
    corners = [(0, 0), (L, 0), (L, L), (0, L)]
    for k, (x, y) in enumerate(corners):
        if f"C{k}" not in omit:
            ids[f"C{k}"] = str(f.Objects.AddLine(P(x, y, 0), P(x, y, z1),
                                                 attr(i_col)))
        if f"S{k}" not in omit:
            ids[f"S{k}"] = str(f.Objects.AddPoint(P(x, y, 0), attr(i_sup)))
    for k in range(3):
        a, b = corners[k], corners[k + 1]
        if f"B{k}" in omit:
            continue
        user = {"sf_section": "W18X50"} if k == 0 else {}
        ids[f"B{k}"] = str(f.Objects.AddLine(P(a[0], a[1], z1),
                                             P(b[0], b[1], z1),
                                             attr(i_beam, **user)))
    if "ARC" not in omit:              # edge (0,L) -> (0,0), bulging to -x
        arc = r.Arc(P(0, L, z1), P(-1000, L / 2, z1), P(0, 0, z1))
        ids["ARC"] = str(f.Objects.AddArc(arc, attr(i_beam)))
    if "NURBS" not in omit:            # diagonal beam with a sag in z
        pts = [P(0, 0, z1), P(L / 3, L / 3, z1 - 300), P(2 * L / 3,
                                                         2 * L / 3, z1 - 300),
               P(L, L, z1)]
        nc = r.NurbsCurve.Create(False, 3, pts)
        ids["NURBS"] = str(f.Objects.AddCurve(nc, attr(i_beam)))
    if "SLAB" not in omit:
        # rhino3dm cannot add an inner loop to a trimmed plane directly:
        # take the bottom cap (outer + inner loop) of an extruded profile
        # with a hole, built in the world XY plane, then lift it to z1
        rect = r.Polyline([P(0, 0, 0), P(L, 0, 0), P(L, L, 0), P(0, L, 0),
                           P(0, 0, 0)]).ToPolylineCurve()
        hole = r.Polyline([P(2000, 2000, 0), P(3000, 2000, 0),
                           P(3000, 3000, 0), P(2000, 3000, 0),
                           P(2000, 2000, 0)]).ToPolylineCurve()
        ex = r.Extrusion.Create(rect, 200, True)
        if not ex.AddInnerProfile(hole):
            raise RuntimeError("rhino3dm: AddInnerProfile failed")
        b = ex.ToBrep(False)
        face = next(k for k in range(len(b.Faces))
                    if len(b.Faces[k].Loops) == 2)
        slab = b.Faces[face].DuplicateFace(False)
        slab.Translate(r.Vector3d(0, 0, z1))
        ids["SLAB"] = str(f.Objects.AddBrep(slab, attr(i_slab)))
    z2 = z1 + ROOF
    if "ROOF" not in omit:             # warped mesh: centre raised 400 mm
        m = r.Mesh()
        n = 3
        for i in range(n + 1):
            for j in range(n + 1):
                x, y = L * i / n, L * j / n
                bump = 400.0 if (0 < i < n and 0 < j < n) else 0.0
                m.Vertices.Add(x + dx, y, z2 + bump)
        for i in range(n):
            for j in range(n):
                k = i * (n + 1) + j
                m.Faces.AddFace(k, k + n + 1, k + n + 2, k + 1)
        ids["ROOF"] = str(f.Objects.AddMesh(m, attr(i_roof)))
    for k, (x, y) in enumerate(corners):
        if f"P{k}" in omit:
            continue
        user = {"sf_role": "brace"} if k == 0 else {}
        ids[f"P{k}"] = str(f.Objects.AddLine(P(x, y, z1), P(x, y, z2),
                                             attr(i_col, **user)))
    if "VAULT" not in omit:            # non-planar NURBS face
        bs = r.CommonObject.Decode(vault_encoded(L + 1000, z1, render_mesh))
        ids["VAULT"] = str(f.Objects.AddBrep(bs, attr(i_roof)))
    if not f.Write(str(path), 8):
        raise RuntimeError(f"could not write {path}")
    return ids


if __name__ == "__main__":          # python tests/_rhino_build.py out.3dm
    import sys
    kw = {}
    if len(sys.argv) > 2:
        kw["dz"] = float(sys.argv[2])
    print(build_frame_3dm(sys.argv[1], **kw))
