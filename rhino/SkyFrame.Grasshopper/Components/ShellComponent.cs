using System;
using System.Collections.Generic;
using System.Linq;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Types;
using Rhino.Geometry;
using SkyFrame.Exchange.Model;
using SkyFrame.Grasshopper.Goo;

namespace SkyFrame.Grasshopper.Components
{
    public sealed class ShellComponent : SfComponent
    {
        public ShellComponent()
            : base("SF Shell", "SFShell", "Walls / slabs / shells from breps, surfaces and meshes. Planar faces → polygons (with openings); freeform faces → NURBS + quad-dominant mesh; meshes pass through.", SubObjects) { }

        public override Guid ComponentGuid => new Guid("c5e7a2d4-3f6b-4e8a-8cbd-2f3a4b5c6d73");
        protected override string IconName => "Shell";

        protected override void RegisterInputParams(GH_InputParamManager p)
        {
            p.AddGeometryParameter("Geometry", "G", "Brep, surface, extrusion or mesh (one SF object per brep face)", GH_ParamAccess.item);
            p.AddTextParameter("Role", "R", "auto | wall | slab | shell", GH_ParamAccess.item, "auto");
            p.AddTextParameter("Shell Section", "S", "Shell section name", GH_ParamAccess.item);
            p.AddNumberParameter("Thickness", "t", "Thickness (document units)", GH_ParamAccess.item);
            p.AddNumberParameter("Mesh Size", "MS", "Target element size (document units) for freeform faces and SkyFrame meshing; 0 = automatic", GH_ParamAccess.item, 0.0);
            p.AddTextParameter("Material", "M", "Material name", GH_ParamAccess.item);
            p.AddTextParameter("Story", "St", "Story name (optional)", GH_ParamAccess.item);
            for (int i = 2; i < 7; i++) p[i].Optional = true;
        }

        protected override void RegisterOutputParams(GH_OutputParamManager p)
        {
            p.AddGenericParameter("Objects", "O", "SkyFrame objects, one per face (wire into SF Send)", GH_ParamAccess.list);
            p.AddMeshParameter("Mesh", "Mx", "The analysis mesh sent for freeform faces / meshes (for checking)", GH_ParamAccess.list);
        }

        protected override void SolveInstance(IGH_DataAccess da)
        {
            IGH_GeometricGoo goo = null;
            if (!da.GetData(0, ref goo) || goo == null) return;
            GeometryBase g = GH_Convert.ToGeometryBase(goo);
            if (g == null) { AddRuntimeMessage(GH_RuntimeMessageLevel.Error, $"unsupported input {goo.TypeName}"); return; }
            if (g is Curve || g is Rhino.Geometry.Point) { AddRuntimeMessage(GH_RuntimeMessageLevel.Error, "use SF Frame for curves and SF Support / SF Joint for points"); return; }

            try
            {
                var meshSize = Number(da, 4) ?? 0.0;
                var cs = ConvertSettings.FromDocument(Math.Max(0, meshSize));
                var warnings = new List<string>();
                var geoms = RhinoConvert.FromSurfaceLike(g, cs, warnings);
                foreach (var w in warnings) AddRuntimeMessage(GH_RuntimeMessageLevel.Remark, w);

                var role = Roles.Normalize(Text(da, 1, "auto"), Roles.ShellRoles);
                var thickness = Number(da, 3);
                var objs = new List<SfObjectGoo>();
                var meshes = new List<Mesh>();
                foreach (var geom in geoms)
                {
                    var obj = new ExchangeObject
                    {
                        Geom = geom,
                        Role = role,
                        Props = new ObjectProps
                        {
                            ShellSection = NullIfEmpty(Text(da, 2)),
                            Thickness = thickness.HasValue ? thickness * cs.Scale : null,
                            MeshSize = meshSize > 0 ? meshSize * cs.Scale : (double?)null,
                            Material = NullIfEmpty(Text(da, 5)),
                            Story = NullIfEmpty(Text(da, 6)),
                        },
                    };
                    objs.Add(new SfObjectGoo(obj, 1.0 / cs.Scale));
                    if (geom is MeshGeometry mg) meshes.Add(RhinoConvert.ToRhinoMesh(mg, 1.0 / cs.Scale));
                    else if (geom is NurbsSurfaceGeometry ns) meshes.Add(RhinoConvert.ToRhinoMesh(ns.Mesh, 1.0 / cs.Scale));
                }
                var tris = geoms.OfType<NurbsSurfaceGeometry>().Sum(x => x.Mesh.Faces.Count - x.Mesh.QuadCount);
                if (tris > 0) AddRuntimeMessage(GH_RuntimeMessageLevel.Remark, $"{tris} triangle(s) in freeform meshes (trim edges)");
                da.SetDataList(0, objs);
                da.SetDataList(1, meshes);
            }
            catch (ArgumentException ex)
            {
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error, ex.Message);
            }
        }
    }
}
