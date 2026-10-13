"""CSI ETABS Verification, Analysis Example 15 -- Wall object behaviour,
static lateral loads (Rev. 2): six wall configurations, 20 models, 30
compared displacements.

Common inputs (published, kip-in-s): E = 3000 ksi, nu = 0.2, story height
120 in, fixed base, rigid diaphragm at every floor, one 100 k static
lateral load at the top.  The independent reference is CSI's SAP2000
REFINED-mesh model (Tables 15-1 .. 15-6, "SAP2000" column); the ETABS
column used one or two coarse wall panels per story and is shown for
comparison only (CSI: "only the finest mesh" must meet the limits).

SkyFrame models the walls as meshed shells with a uniform element size
``H_FINE`` (10 in); planar rows report the coarser ``H_COARSE`` (20 in)
value in the note column as the mesh check.  Rigid diaphragm per floor
(SkyFrame rigid diaphragm = the SAP2000 "equal lateral displacement"
constraint for planar walls / rigid floor for 3D walls).

[derived] Element formulation of the reference.  CSI only says "refined
meshes of the membrane/shell element".  Planar walls (15a, b, c, e) are
loaded in plane, so SkyFrame uses the ``membrane`` shell type
(ASDShellQ4: drilling-enhanced membrane, free of the in-plane shear
locking of bilinear quads).  For the two 3D walls the published values
identify the formulation: the C wall (15d) is reproduced only WITHOUT
plate bending (membrane: 0.8942 in / 0.01910 rad vs SAP2000 0.8936 /
0.0191; full shells are 21-27 % stiffer because the 6 in plate bending
resists the warping torsion at the folds), the E wall (15f) only WITH it
(full ShellMITC4: 0.3658 in / 0.00391 rad vs 0.3655 / 0.0039; membrane is
5-10 % softer).  Each 3D row is therefore computed with the matching
formulation and the OTHER formulation's value is printed in its note, so
the effect of this choice is visible on every row.
[derived] 15b: the re-entrant corners where the wall sits on the column
legs are stress singularities, so the displacement keeps growing with
refinement (membrane, story 3: 20 in 0.0676, 10 in 0.0701, 5 in 0.0712
in).  SAP2000's published mesh (Fig. 15-9) has 20 in elements (14 across
the 280 in wall, 2 per leg) and SkyFrame at that mesh reproduces it to ~1
%; the 15b rows are therefore run at the REFERENCE mesh (20 in) and the
note gives the 10 in value -- an unconverged reference, not a solver
difference (listed under known differences).
[derived] 15c: the SAP2000 "equal lateral displacement" constraint acts
on the PIER floor nodes; constraining the spandrel-band nodes too makes
the top fibre of each spandrel axially rigid and stiffens the 240 in
coupled wall by ~35 % (0.0954 vs 0.1427 in with MITC4 at 20 in).  The
SkyFrame model therefore assigns only the pier floor joints to a named
rigid diaphragm (ETABS Assign > Joint > Diaphragm).

* 15a planar wall, length 120 / 360 / 720 in, 1 / 3 / 6 stories, t = 12;
  load 100 k +X at the top-left corner (C1, roof).
* 15b wall on columns, 3 stories: C1/C2 at 240 in centres; story 1 two
  40" (X) x 20" (Y) columns, stories 2-3 a 280" (= 40 + 200 + 40) wall,
  12" thick (the 40" x 12" end piers have the panel thickness).
  [assumed] Columns modelled as 20"-thick shell legs 40" wide (as the
  SAP2000 refined mesh, Fig. 15-9, shows them), 100 k +X at the 3rd
  level top-left corner.
* 15c wall-spandrel: left pier 240 in (C1-C2), opening 60 / 240 in
  (C2-C3), right pier 120 in (C3-C4), 3 / 6 stories, t = 12; spandrels 40
  in deep at the top of every story.  [assumed] spandrels modelled as
  shell bands (as in the SAP2000 refined mesh, Fig. 15-8); ETABS used
  beams.  [assumed] Load not drawn in Fig. 15-3: 100 k +X at the
  top-left corner (roof), by analogy with 15a.
* 15d C-shaped wall, t = 6, 3 / 6 stories; plan (in) C1 (-140, -120), C2
  (-140, -80), C3 (-60, 0), C4 (60, 0), C5 (140, -80), C6 (140, -120);
  100 k in X and in Y at the origin (mid-web, roof), separately.
* 15e thickened-edge wall, C1-C2 = 210 in; 30" x 18" end piers centred on
  C1/C2, 8" web; overall 240 in; 3 / 6 stories.  [assumed] Load arrow
  unlabelled: 100 k +X at the top-left corner.  Piers as 18"-thick shell
  strips.
* 15f E-shaped wall, t = 6, 3 / 6 stories; flange C1 (-120, 0) - C3
  (120, 0), webs to y = -120 at x = -120, 0, 120; 100 k in X and in Y at
  C2 (origin, roof), separately.

Displacement directions: X / Y translation and Z rotation (rad) of the
load point.  The CSI tables print magnitudes; SkyFrame magnitudes are
compared.
"""

from __future__ import annotations

import math
from typing import Dict, List

from skyframe.core.model import ShellRegion, ShellSection

from ._core import (CaseResult, FIXED, disp, engine, load, new_model,
                    support, timed)
from ._svg import Sketch

ID = "EX15"
TITLE = "Shear walls: 6 configurations, 20 models"
TIER = "1"

H_FINE = 10.0
H_COARSE = 20.0
P = 100.0
E, NU = 3000.0, 0.2

# Table 15-1 (15a): (stories, length, ETABS, SAP2000)
T15A = [(6, 120, "2.3921", "2.4287"), (6, 360, "0.0986", "0.1031"),
        (6, 720, "0.0172", "0.0186"), (3, 120, "0.3071", "0.3205"),
        (3, 360, "0.0170", "0.0187"), (3, 720, "0.0046", "0.0052"),
        (1, 120, "0.0145", "0.0185"), (1, 360, "0.0025", "0.0029"),
        (1, 720, "0.0011", "0.0013")]
T15B = [(3, "0.0691", "0.0671"), (2, "0.0524", "0.0530"),
        (1, "0.0390", "0.0412")]
T15C = [(6, 60, "0.0844", "0.0869"), (6, 240, "0.1456", "0.1505"),
        (3, 60, "0.0188", "0.0200"), (3, 240, "0.0313", "0.0332")]
# 15d / 15f: (stories, load dir, disp dir, ETABS, SAP2000)
T15D = [(6, "X", "X", "0.8637", "0.8936"), (6, "X", "Rz", "0.0185", "0.0191"),
        (6, "Y", "Y", "1.1447", "1.1882"), (3, "X", "X", "0.1249", "0.1337"),
        (3, "X", "Rz", "0.0024", "0.0025"), (3, "Y", "Y", "0.1623", "0.1733")]
T15E = [(6, "0.2822", "0.2899"), (3, "0.0464", "0.0480")]
T15F = [(6, "X", "X", "0.3707", "0.3655"), (6, "X", "Rz", "0.0042", "0.0039"),
        (6, "Y", "Y", "0.7295", "0.7490"), (3, "X", "X", "0.0602", "0.0628"),
        (3, "X", "Rz", "0.0005", "0.0005"), (3, "Y", "Y", "0.0993", "0.1058")]


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _model(name: str, n: int, planar: bool):
    m = new_model(name, E, nu=NU, heights=[120.0] * n, diaphragm=True)
    if planar:
        m.active_dof = ["UX", "UZ", "RY"]
    return m


#: wall element formulation per configuration (see the module docstring):
#: "membrane" = ASDShellQ4 membrane only, "shell" = full ShellMITC4
WALL_TYPE = {"a": "membrane", "b": "membrane", "c": "membrane",
             "d": "membrane", "e": "membrane", "f": "shell"}
OTHER = {"membrane": "shell", "shell": "membrane"}
H_15B = 20.0          # 15b runs at the reference (SAP2000 Fig. 15-9) mesh


def _ssec(m, t: float) -> str:
    name = f"W{t:g}"
    if name not in m.shell_sections:
        m.add_shell_section(ShellSection(name, "MAT", t))
    return name


def set_wall_type(m, stype: str) -> None:
    for sec in m.shell_sections.values():
        sec.shell_type = stype


def _rect_xz(m, uid, x0, x1, z0, z1, t, h, y=0.0):
    m.shells.append(ShellRegion(uid, "wall", "shell", _ssec(m, t),
                                [(x0, y, z0), (x1, y, z0), (x1, y, z1),
                                 (x0, y, z1)], mesh_size=h))


def _fix_base(m, eng=None):
    """Fully fix every shell node at z = 0 (done after meshing)."""
    from skyframe.core.mesh import mesh_model
    pts = mesh_model(m).points
    for p in pts:
        if abs(p[2]) < 1e-9:
            support(m, p, FIXED)


def _run(m, case="P"):
    m.add_case(case, {case: 1.0})
    _fix_base(m)
    eng = engine(m)
    return eng, eng.run_static(case)


def model_15a(n: int, L: float, h: float):
    m = _model("15a", n, True)
    _rect_xz(m, "W", 0.0, L, 0.0, 120.0 * n, 12.0, h)
    load(m, "P", (0.0, 0.0, 120.0 * n), fx=P)
    return m, (0.0, 0.0, 120.0 * n)


def model_15b(h: float):
    m = _model("15b", 3, True)
    _rect_xz(m, "COL1", -20.0, 20.0, 0.0, 120.0, 20.0, h)
    _rect_xz(m, "COL2", 220.0, 260.0, 0.0, 120.0, 20.0, h)
    _rect_xz(m, "W", -20.0, 260.0, 120.0, 360.0, 12.0, h)
    load(m, "P", (-20.0, 0.0, 360.0), fx=P)
    return m, (-20.0, 0.0, 360.0)


def model_15c(n: int, Lb: float, h: float):
    m = _model("15c", n, True)
    x2, x3 = 240.0, 240.0 + Lb
    x4 = x3 + 120.0
    _rect_xz(m, "PL", 0.0, x2, 0.0, 120.0 * n, 12.0, h)
    _rect_xz(m, "PR", x3, x4, 0.0, 120.0 * n, 12.0, h)
    for k in range(1, n + 1):
        z = 120.0 * k
        _rect_xz(m, f"SP{k}", x2, x3, z - 40.0, z, 12.0, h)
    load(m, "P", (0.0, 0.0, 120.0 * n), fx=P)
    # equal lateral displacement of the PIER floor nodes only (the
    # spandrel bands are not constrained -- see the module docstring)
    m.diaphragms = {"FLOOR": {"type": "rigid"}}
    for k in range(1, n + 1):
        for x0, x1 in ((0.0, x2), (x3, x4)):
            nseg = round((x1 - x0) / h)
            for i in range(nseg + 1):
                m.joint_diaphragms.append(
                    {"point": [x0 + (x1 - x0) * i / nseg, 0.0, 120.0 * k],
                     "diaphragm": "FLOOR"})
    return m, (0.0, 0.0, 120.0 * n)


def model_15e(n: int, h: float):
    m = _model("15e", n, True)
    H = 120.0 * n
    _rect_xz(m, "P1", -15.0, 15.0, 0.0, H, 18.0, h)
    _rect_xz(m, "WEB", 15.0, 195.0, 0.0, H, 8.0, h)
    _rect_xz(m, "P2", 195.0, 225.0, 0.0, H, 18.0, h)
    load(m, "P", (-15.0, 0.0, H), fx=P)
    return m, (-15.0, 0.0, H)


C_PLAN = [(-140.0, -120.0), (-140.0, -80.0), (-60.0, 0.0), (60.0, 0.0),
          (140.0, -80.0), (140.0, -120.0)]
E_SEGS = [((-120.0, 0.0), (0.0, 0.0)), ((0.0, 0.0), (120.0, 0.0)),
          ((-120.0, 0.0), (-120.0, -120.0)), ((0.0, 0.0), (0.0, -120.0)),
          ((120.0, 0.0), (120.0, -120.0))]


def _seg_h(a, b, h):
    """Mesh size that divides segment a-b into whole elements ~h."""
    L = math.dist(a, b)
    return L / max(1, round(L / h))


def model_3d(kind: str, n: int, ldir: str, h: float):
    m = _model(f"15{kind}", n, False)
    H = 120.0 * n
    if kind == "d":
        segs = list(zip(C_PLAN, C_PLAN[1:]))
    else:
        segs = E_SEGS
    for i, (a, b) in enumerate(segs):
        # one region per story keeps the story-line nodes on every panel;
        # the in-plane division follows the segment length
        hs = _seg_h(a, b, h)
        for k in range(n):
            m.shells.append(ShellRegion(
                f"S{i}_{k}", "wall", "shell", _ssec(m, 6.0),
                [(a[0], a[1], 120.0 * k), (b[0], b[1], 120.0 * k),
                 (b[0], b[1], 120.0 * (k + 1)),
                 (a[0], a[1], 120.0 * (k + 1))],
                mesh_size=hs))
            m.shells[-1].mesh_divisions = {
                "n1": max(1, round(math.dist(a, b) / h)),
                "n2": max(1, round(120.0 / h))}
    load(m, "P", (0.0, 0.0, H), **({"fx": P} if ldir == "X" else {"fy": P}))
    return m, (0.0, 0.0, H)


# --------------------------------------------------------------------------- #
def top_value(kind: str, build, dof: int, h: float,
              stype: str = "") -> float:
    """|displacement| (dof 0/1) or |diaphragm rotation| (dof 5) at the load
    point of ``build(h)`` with wall formulation ``stype``."""
    m, pt = build(h)
    set_wall_type(m, stype or WALL_TYPE[kind])
    eng, r = _run(m)
    if dof == 5:
        st = m.stories[-1].name
        return abs(float(r.node_disp[eng._asm.masters[st]][5]))
    return abs(disp(eng, r, pt, dof))


def cases():
    """[(kind, group, quantity, location, ETABS, SAP2000, build, dof)]."""
    out = []
    for n, L, et, ref in T15A:
        out.append(("a", "15a planar wall", f"Top displacement, {n}-story",
                    f"L = {L} in", et, ref,
                    lambda h, n=n, L=L: model_15a(n, L, h), 0))
    for k, et, ref in T15B:
        def b15(h, k=k):
            m, _ = model_15b(h)
            return m, (100.0, 0.0, 120.0 * k)
        out.append(("b", "15b wall on columns", f"Displacement, story {k}",
                    "", et, ref, b15, 0))
    for n, Lb, et, ref in T15C:
        out.append(("c", "15c wall-spandrel", f"Top displacement, {n}-story",
                    f"opening {Lb} in", et, ref,
                    lambda h, n=n, Lb=Lb: model_15c(n, Lb, h), 0))
    def three_d(kind, tab, grp):
        for n, ld, dd, et, ref in tab:
            q = ("Z-rotation" if dd == "Rz" else dd + "-displacement")
            out.append((kind, grp, f"{q}, {n}-story", f"load {ld}", et, ref,
                        lambda h, kind=kind, n=n, ld=ld:
                        model_3d(kind, n, ld, h),
                        {"X": 0, "Y": 1, "Rz": 5}[dd]))
    three_d("d", T15D, "15d C-shaped wall")
    for n, et, ref in T15E:
        out.append(("e", "15e thickened edges", f"Top displacement, "
                    f"{n}-story", "", et, ref,
                    lambda h, n=n: model_15e(n, h), 0))
    three_d("f", T15F, "15f E-shaped wall")
    return out


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER,
        source="CSI SAP2000 refined membrane/shell meshes (CSI 2002), as "
               "published in CSI ETABS Verification Example 15, Tables "
               "15-1 .. 15-6",
        tests="Shell walls (planar, on columns, coupled, C, thickened "
              "edges, E), rigid diaphragms, static lateral load")
    res.inputs = [("Units", "kip, in, s"), ("E / nu", "3000 ksi / 0.2"),
                  ("Story height", "120 in"), ("Load", "100 k at the top"),
                  ("Walls", "t = 12 in (a, b, c), 6 in (d, f), 8 / 18 in "
                            "(e)")]
    res.assumptions = [
        "Reference = CSI's SAP2000 refined-mesh column (CSI rule: only the "
        "finest mesh must meet the limits); the ETABS coarse-panel column "
        "is shown for comparison.",
        f"SkyFrame mesh: uniform {H_FINE:g} in elements; planar rows show "
        f"the {H_COARSE:g} in value in the note as a mesh check.",
        "[derived] Planar walls: membrane shell type (ASDShellQ4). 3D "
        "walls: the published values identify the reference formulation "
        "(C wall = membrane only, E wall = full shell); each 3D row's "
        "note gives the other formulation's value.",
        f"[derived] 15b at the reference mesh ({H_15B:g} in, SAP2000 Fig. "
        "15-9): the re-entrant column/wall corners are singular, so the "
        "value grows with refinement; the 10 in value is in the note.",
        "[derived] 15c: equal lateral displacement on the pier floor "
        "joints only (named diaphragm); constraining the spandrel nodes "
        "makes the spandrel top chord axially rigid.",
        "[assumed] 15c and 15e load magnitude 100 k (arrow unlabelled), "
        "+X at the top-left corner; 15c spandrels as 40 in shell bands; "
        "15b first-story columns as 20 in thick shell legs; 15e piers as "
        "18 in shell strips.",
        "Rigid diaphragm at every floor (= SAP2000 equal-displacement "
        "constraint / rigid floor members).",
        "Magnitudes compared (CSI tables print magnitudes); Z-rotation in "
        "rad.",
    ]
    res.differences = [
        "Example 15: ETABS's one/two-panel wall meshes under-predict the "
        "SAP2000 refined values by up to 21.6 % (1-story 120 in wall); "
        "CSI does not hold coarse meshes to the limits.  SkyFrame is run "
        "with a refined mesh, so it is compared with the refined "
        "reference."]
    res.mesh = (f"uniform {H_FINE:g} in quads (15b: {H_15B:g} in = the "
                f"reference mesh); planar: membrane ASDShellQ4; 3D: "
                f"formulation matched to the reference")
    alt: Dict[str, List[str]] = {}
    for kind, grp, qty, loc, et, ref, build, dof in cases():
        h = H_15B if kind == "b" else H_FINE
        val = top_value(kind, build, dof, h)
        if kind in ("d", "f"):
            o = OTHER[WALL_TYPE[kind]]
            ov = top_value(kind, build, dof, h, o)
            r0 = float(ref)
            note = (f"{o}: {ov:.5g} ({100 * (ov / r0 - 1):+.1f} %)")
            alt.setdefault(kind, []).append(f"{qty} {loc}: {note}")
        elif kind == "b":
            ov = top_value(kind, build, dof, H_FINE)
            note = (f"{H_FINE:g} in mesh: {ov:.5g} "
                    f"({100 * (ov / float(ref) - 1):+.1f} %)")
        else:
            ov = top_value(kind, build, dof, H_COARSE)
            note = f"{H_COARSE:g} in mesh: {ov:.5g}"
        res.add(qty, loc, "rad" if "rotation" in qty else "in", ref,
                "SAP2000 refined mesh", et, val, group=grp, note=note)
    res.differences.append(
        "Example 15d/15f: the reference element formulation is not "
        "stated; the C-wall values are reproduced only by membrane walls "
        "and the E-wall values only by full shells.  With the other "
        "formulation SkyFrame differs by: "
        + "; ".join(alt.get("d", [])[:3]) + " | "
        + "; ".join(alt.get("f", [])[:3]) + ".")
    res.differences.append(
        "Example 15b: re-entrant column/wall corners make the reference "
        "mesh-dependent; SkyFrame at the published SAP2000 mesh (20 in) "
        "matches it, finer meshes converge ~5-7 % softer (see the row "
        "notes).")

    sk = Sketch(420, 240)
    # 15d and 15f plans side by side
    for a, b in zip(C_PLAN, C_PLAN[1:]):
        sk.line(a, b, "w")
    for a, b in E_SEGS:
        sk.line((a[0] + 400, a[1]), (b[0] + 400, b[1]), "w")
    sk.dot((0, 0), "cm").dot((400, 0), "cm")
    sk.text((0, -150), "15d C-shape").text((400, -150), "15f E-shape")
    sk.text((0, 14), "load point").text((400, 14), "load point")
    res.svg = sk.render("Example 15d / 15f plans")
    return res
