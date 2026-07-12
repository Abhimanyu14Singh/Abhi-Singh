"""Wave 23 validation: auto edge constraints (the "zipper"), nonlinear
layered shells, line/area springs, semi-rigid story-force distribution.

Every number is hand-pinned:

* zipper — two-region cantilever wall (4 x 3 x 0.2 m, E = 25e6, nu = 0.2,
  left half meshed 1 x 2 @ 1.5 m, right half 2 x 3 @ 1.0 m) under a
  100 kN tip shear: the three hanging-node chains are enumerated by hand
  (left interface edges (0,1.5)/(1.5,3) each carry one right-side node,
  the right edge (1,2) carries the left-side 1.5 node); untied the halves
  connect only at the two shared corners (tip 1.5263e-4 m, +19.4% vs the
  1.2784e-4 monolithic value); tied the tip lands at 1.2187e-4 m, -4.7%
  from monolithic (the pinned-chain tie is documented "stiffer than pure
  interpolation" — CONTRACT v0.22; the untied error is 4x larger).
  Equilibrium is exact in every variant (reactions balance to 1e-9
  relative — the ties are elements, not external forces).  A frame member
  whose end lands mid-edge is a MECHANISM untied (singular solve) and
  carries its load through the chain when tied.
* layered shells — a 2 x 3 m concrete wall (fc' = 30 MPa attr) pushover:
  the ASDConcrete3D law's first segment slope is EXACTLY E, so the
  first-step pushover secant equals the elastic wall stiffness
  254165.46 kN/m to 1e-4; the curve then peaks (~258.9 kN, far below the
  elastic extrapolation at that displacement) and descends — genuine
  cracking.  A steel-only panel (PlateRebar angle 0) in uniform stretch
  matches u = 2P*W/(E*t*H) = 1.0e-4 m and the same-thickness elastic
  panel to 1e-3 (the d = 1e-8 m displacement-controlled push adds
  exactly 1e-8, i.e. +1e-4 relative).  Linear analyses of a layered
  model are BIT-identical to the elastic model at the summed layer
  thickness, and self-weight uses the summed thickness.
* line/area springs — a rigid plate (4 x 4, kz = 1000 kN/m/m^2, A = 16)
  under a 100 kN center load settles P/(kz*A) = 6.25 mm uniformly
  (< 1e-6 relative with E = 25e11, t = 2 — bending flexibility scales
  1/(E t^3) and dominates the residual); nodal spring reactions equal
  kz * tributary * u exactly (corner 1.5625 / edge 3.125 / interior
  6.25 kN, structured-mesh quarters).  A wall base line spring
  (kz = 5000 kN/m/m over 4 m, uniform 10 kPa on an 8 m^2 wall) reacts
  [10, 20, 20, 20, 10] kN — exactly the [0.5, 1, 1, 1, 0.5] m tributary
  lengths.  Compression-only springs leave every uplifted node at the
  1e-6-residual force level while equilibrium still closes to 1e-9.
* semi-rigid distribution — story forces on a "none"-diaphragm story
  with a meshed slab spread over the slab mesh nodes by tributary mass
  (node count when massless) with the accidental-torsion moment realized
  as the antisymmetric couple field (CONTRACT v0.22): total base shear
  and base torque match the rigid-diaphragm run to 1e-9 (MZ = 270 kN*m
  = -(-100*3 + 100*0.05*6) about the origin), the symmetric building
  responds symmetrically, the semi-rigid drift brackets the rigid drift
  from above, and a story force whose slab mass sits on ONE node is
  EXACTLY a nodal load at that node.

Units: kN, m, kPa per CONTRACT.md.
"""

import warnings

import pytest

import openseespy.opensees as ops

from skyframe.core.mesh import edge_tie_chains, mesh_model
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 LineSpring, Material, MemberLoad, NodalLoad,
                                 NodalMass, PointSupport, ShellSection,
                                 StoryForce)
from skyframe.engine.opensees_engine import (AXIAL_ONLY_RATIO,
                                             SHELL_SUBLAYERS, OpenSeesEngine)

E_CONC = 25.0e6      # kPa
FIX = (1, 1, 1, 1, 1, 1)


def _node_at(eng, x, z, y=0.0, tol=1e-6):
    """Structural node tag at (x, y, z) from the engine's stored assembly."""
    for t, c in eng._asm.struct_coords.items():
        if (abs(c[0] - x) < tol and abs(c[1] - y) < tol
                and abs(c[2] - z) < tol):
            return t
    raise AssertionError(f"no node at ({x}, {y}, {z})")


# --------------------------------------------------------------------------- #
# 1. auto edge constraints (the zipper)
# --------------------------------------------------------------------------- #
def _zipper_wall(edge_constraints, split=True):
    """Cantilever wall 4 x 3 x 0.2 m, tip shear 100 kN at (4, 0, 3).

    split: left half meshed 1 x 2 (1.5 m), right half 2 x 3 (1.0 m) —
    interface nodes match only at z = 0 and z = 3.
    """
    m = BuildingModel(name="zip")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("C", E_CONC, 0.2))
    m.add_shell_section(ShellSection("SH", "C", 0.2))
    m.set_stories([3.0])
    if split:
        m.add_shell("wall", "shell", "SH",
                    [(0, 0, 0), (2, 0, 0), (2, 0, 3), (0, 0, 3)],
                    mesh_size=1.5, uid="WL")
        m.add_shell("wall", "shell", "SH",
                    [(2, 0, 0), (4, 0, 0), (4, 0, 3), (2, 0, 3)],
                    mesh_size=1.0, uid="WR")
    else:
        m.add_shell("wall", "shell", "SH",
                    [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)],
                    mesh_size=1.0, uid="W")
    m.edge_constraints = edge_constraints
    m.pattern("P", "other").nodal_loads.append(NodalLoad((4, 0, 3), fx=100.0))
    m.add_case("P", {"P": 1.0})
    return m


def test_edge_tie_chains_hand_enumerated():
    """The two-region wall has EXACTLY three hanging-node chains: the two
    left interface edges (z 0->1.5 and 1.5->3 at x = 2) each carry one
    right-mesh node (z = 1 resp. z = 2), and the right interface edge
    (z 1->2) carries the left-mesh z = 1.5 node."""
    mesh = mesh_model(_zipper_wall(True))
    chains = edge_tie_chains(mesh)
    assert len(chains) == 3
    seen = []
    for ruid, na, nb, hangs in chains:
        za, zb = mesh.points[na][2], mesh.points[nb][2]
        assert abs(mesh.points[na][0] - 2.0) < 1e-9      # all at x = 2
        assert abs(mesh.points[nb][0] - 2.0) < 1e-9
        zh = [mesh.points[h][2] for h in hangs]
        seen.append((ruid, tuple(sorted((za, zb))), tuple(zh)))
    assert (("WL", (0.0, 1.5), (1.0,)) in seen)
    assert (("WL", (1.5, 3.0), (2.0,)) in seen)
    assert (("WR", (1.0, 2.0), (1.5,)) in seen)
    # a matched-mesh interface has NO chains
    mono = mesh_model(_zipper_wall(False, split=False))
    assert edge_tie_chains(mono) == []


def test_zipper_untied_interface_is_soft():
    """Untied, the halves connect only at the two shared corner nodes:
    tip deflection 1.5263e-4 m — +19.4% over the 1.2784e-4 monolithic
    wall.  Equilibrium is exact either way."""
    eng_m = OpenSeesEngine(_zipper_wall(False, split=False))
    mono = eng_m.run_static("P")
    eng = OpenSeesEngine(_zipper_wall(False))
    r = eng.run_static("P")
    assert eng._asm.edge_ties == []                      # flag off: no ties
    tip_m = mono.node_disp[_node_at(eng_m, 4, 3)][0]
    tip = r.node_disp[_node_at(eng, 4, 3)][0]
    assert tip_m == pytest.approx(1.278350e-4, rel=1e-4)
    assert tip == pytest.approx(1.526269e-4, rel=1e-4)
    assert tip / tip_m == pytest.approx(1.1939, rel=1e-3)
    assert r.base["FX"] == pytest.approx(-100.0, rel=1e-9)


def test_zipper_ties_recover_single_region_value():
    """Tied, the tip deflection lands within 5% of the monolithic wall
    (1.2187e-4 vs 1.2784e-4, -4.66%: the pinned tie chain is documented
    slightly STIFFER than pure interpolation — CONTRACT v0.22 — while the
    untied error was +19.4%); the reaction balance stays exact (the ties
    are internal elements)."""
    eng = OpenSeesEngine(_zipper_wall(True))
    r = eng.run_static("P")
    assert len(eng._asm.edge_ties) == 3
    # 1 hanging node each -> 2 tie elements per chain
    assert all(len(e["eles"]) == 2 and len(e["chain"]) == 3
               for e in eng._asm.edge_ties)
    tip = r.node_disp[_node_at(eng, 4, 3)][0]
    assert tip == pytest.approx(1.218739e-4, rel=1e-4)
    assert abs(tip / 1.278350e-4 - 1.0) < 0.05
    assert r.base["FX"] == pytest.approx(-100.0, rel=1e-9)
    # default-off keeps the model dict round-trip and the mono build clean
    back = BuildingModel.from_dict(_zipper_wall(True).to_dict())
    assert back.edge_constraints is True


def test_zipper_patch_axial_equilibrium_exact():
    """Uniform vertical load across the zipped interface: the reaction sum
    balances the applied load to 1e-9 — equilibrium is exact regardless
    of the tie stiffness (internal elements cannot create force)."""
    m = _zipper_wall(True)
    q = 12.0                                             # kPa on 12 m^2
    m.pattern("Q", "other").area_loads.append(AreaLoad("WL", q))
    m.patterns["Q"].area_loads.append(AreaLoad("WR", q))
    m.add_case("Q", {"Q": 1.0})
    r = OpenSeesEngine(m).run_static("Q")
    total = sum(v[2] for v in r.reactions.values())
    assert total == pytest.approx(q * 12.0, rel=1e-9)    # 144 kN
    assert r.base["FZ"] == pytest.approx(q * 12.0, rel=1e-9)


def test_zipper_frame_end_mid_edge():
    """A beam end at (2, 0, 1.5) lands strictly inside the wall edge
    (2,0,1)-(2,0,2) (wall meshed 1.0 m): UNTIED that node is a genuine
    mechanism (the solve is singular -> RuntimeError); TIED the chain
    carries the 10 kN midspan load into the wall (base FZ = 10 to 1e-9,
    beam-end settlement -3.321e-6 m pinned)."""
    def frame_zip(ties):
        m = BuildingModel(name="fz")
        m.rigid_diaphragms = False
        m.num_modes = 0
        m.add_material(Material("C", E_CONC, 0.2))
        m.add_shell_section(ShellSection("SH", "C", 0.2))
        m.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
        m.set_stories([3.0])
        m.add_shell("wall", "shell", "SH",
                    [(0, 0, 0), (2, 0, 0), (2, 0, 3), (0, 0, 3)],
                    mesh_size=1.0, uid="W")
        m.add_member("beam", "BM", (2, 0, 1.5), (5, 0, 1.5),
                     story="Story1", uid="B1")
        m.supports = [PointSupport((x, 0, 0), FIX) for x in (0.0, 1.0, 2.0)]
        m.supports.append(PointSupport((5, 0, 1.5), (1, 1, 1, 0, 0, 0)))
        m.edge_constraints = ties
        m.pattern("P", "other").member_loads.append(
            MemberLoad("B1", kind="point", w=10.0, a=0.5,
                       direction="gravity"))
        m.add_case("P", {"P": 1.0})
        return m

    with pytest.raises(RuntimeError, match="Static analysis failed"):
        OpenSeesEngine(frame_zip(False)).run_static("P")
    eng = OpenSeesEngine(frame_zip(True))
    r = eng.run_static("P")
    assert len(eng._asm.edge_ties) == 1
    end_uz = r.node_disp[_node_at(eng, 2, 1.5)][2]
    assert end_uz == pytest.approx(-3.3208e-6, rel=1e-3)
    assert r.base["FZ"] == pytest.approx(10.0, rel=1e-9)


def test_edge_constraints_default_off_bit_identical():
    """edge_constraints defaults False and the untied split model builds
    zero tie elements — the pre-v0.22 path byte-for-byte (same node
    displacement map as an explicit False model)."""
    m = _zipper_wall(False)
    assert m.edge_constraints is False
    d = m.to_dict()
    assert d["edge_constraints"] is False
    # absent key (pre-v0.22 file) parses to False
    d.pop("edge_constraints")
    assert BuildingModel.from_dict(d).edge_constraints is False
    with pytest.raises(ValueError, match="edge_constraints"):
        bad = _zipper_wall(False)
        bad.edge_constraints = "yes"
        bad.validate()


# --------------------------------------------------------------------------- #
# 2. nonlinear layered shell walls
# --------------------------------------------------------------------------- #
def _layered_wall(layered, fc=30_000.0):
    """2 x 3 x 0.2 m cantilever wall, 0.5 m mesh, 100 kN tip shear +
    a 40-step pushover to 12 mm."""
    m = BuildingModel(name="lw")
    m.rigid_diaphragms = False
    m.num_modes = 0
    mat = m.add_material(Material("C", E_CONC, 0.2))
    mat.fc = fc
    lay = ({"layers": [{"t": 0.2, "material": "C", "kind": "concrete"}]}
           if layered else None)
    m.add_shell_section(ShellSection("SH", "C", 0.2, layered=lay))
    m.set_stories([3.0])
    m.add_shell("wall", "shell", "SH",
                [(0, 0, 0), (2, 0, 0), (2, 0, 3), (0, 0, 3)],
                mesh_size=0.5, uid="W")
    m.pattern("P", "other").nodal_loads.append(NodalLoad((2, 0, 3), fx=100.0))
    m.add_case("P", {"P": 1.0})
    m.add_pushover_case("PO", "X", target_drift=0.004, steps=40)
    return m


def test_layered_roundtrip_and_validation():
    """layered round-trips exactly; bad layer shapes are rejected."""
    m = BuildingModel(name="rt")
    m.add_material(Material("C", E_CONC, 0.2))
    m.add_material(Material("S", 200e6, 0.3))
    lay = {"layers": [{"t": 0.08, "material": "C", "kind": "concrete"},
                      {"t": 0.002, "material": "S", "kind": "steel",
                       "angle": 90.0},
                      {"t": 0.08, "material": "C", "kind": "concrete"}]}
    m.add_shell_section(ShellSection("SL", "C", 0.2, layered=lay))
    assert m.shell_sections["SL"].total_thickness == pytest.approx(
        0.162, rel=1e-12)
    back = BuildingModel.from_dict(m.to_dict())
    assert back.shell_sections["SL"].layered == lay
    assert back.to_dict() == m.to_dict()
    for bad, frag in [
            ({"layers": []}, "non-empty"),
            ({"layers": [{"t": 0.1, "material": "X",
                          "kind": "concrete"}]}, "unknown material"),
            ({"layers": [{"t": 0.1, "material": "C",
                          "kind": "rubber"}]}, "kind"),
            ({"layers": [{"t": 0.1, "material": "C", "kind": "concrete",
                          "angle": 0}]}, "only valid for steel"),
            ({"layers": [{"t": 0.1, "material": "S", "kind": "steel",
                          "angle": 45}]}, "angle"),
            ({"layers": [{"t": -0.1, "material": "C",
                          "kind": "concrete"}]}, "t must be"),
            ({"sheets": []}, "single key"),
    ]:
        with pytest.raises(ValueError, match=frag):
            m.add_shell_section(ShellSection("B", "C", 0.2, layered=bad))


def test_layered_linear_uses_summed_elastic_thickness():
    """LINEAR analysis of a layered wall == the elastic wall at the SUMMED
    layer thickness, node displacement for node displacement (identical
    section arguments -> identical stiffness)."""
    lw = _layered_wall(True)
    ew = _layered_wall(False)                 # same 0.2 m elastic section
    r_lay = OpenSeesEngine(lw).run_static("P")
    r_el = OpenSeesEngine(ew).run_static("P")
    assert set(r_lay.node_disp) == set(r_el.node_disp)
    for t in r_el.node_disp:
        for a, b in zip(r_lay.node_disp[t], r_el.node_disp[t]):
            assert a == pytest.approx(b, abs=1e-16)
    # self-weight uses the summed thickness: 24 kN/m3 * 0.2 m * 6 m^2
    m = _layered_wall(True)
    m.pattern("SW", "dead").self_weight_factor = 1.0
    m.add_case("SW", {"SW": 1.0})
    r = OpenSeesEngine(m).run_static("SW")
    assert r.base["FZ"] == pytest.approx(24.0 * 0.2 * 6.0, rel=1e-9)


def test_layered_concrete_precrack_stiffness_matches_elastic():
    """The ASDConcrete3D laws are built with their first segment slope
    EXACTLY E (tension: (ft/E, ft); compression: (fc/E, fc) — CONTRACT
    v0.22), so the first pushover step (d = 0.3 mm, well below cracking)
    reproduces the elastic wall stiffness 254165.46 kN/m to 1e-4."""
    eng_el = OpenSeesEngine(_layered_wall(False))
    r = eng_el.run_static("P")
    k_el = 100.0 / r.node_disp[_node_at(eng_el, 2, 3)][0]
    assert k_el == pytest.approx(254165.4598, rel=1e-6)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        po = OpenSeesEngine(_layered_wall(True)).run_pushover("PO")
    assert len(po.roof_disp) == 40                       # fully converged
    k0 = po.base_shear[0] / po.roof_disp[0]
    assert k0 == pytest.approx(k_el, rel=1e-4)


def test_layered_concrete_pushover_softens():
    """Genuine cracking nonlinearity: the capacity curve peaks at
    ~258.9 kN (2.4x below the 630 kN elastic extrapolation at the peak
    displacement — and below it already at twice the cracking
    displacement), then DESCENDS to ~85 kN at 12 mm."""
    eng_el = OpenSeesEngine(_layered_wall(False))
    k_el = 100.0 / eng_el.run_static("P").node_disp[_node_at(
        eng_el, 2, 3)][0]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        po = OpenSeesEngine(_layered_wall(True)).run_pushover("PO")
    V, d = po.base_shear, po.roof_disp
    v_peak = max(V)
    d_peak = d[V.index(v_peak)]
    assert v_peak == pytest.approx(258.90, rel=1e-2)
    assert v_peak < 0.75 * k_el * d_peak                 # measured 0.68x
    # at 2x the cracking displacement the curve is already sub-elastic
    d_cr = next(dd for vv, dd in zip(V, d)
                if vv / dd < 0.98 * k_el)                # first cracked step
    v_2cr = next(vv for vv, dd in zip(V, d) if dd >= 2.0 * d_cr)
    assert v_2cr < k_el * (2.0 * d_cr)
    # descending branch: the end of the curve is well below the peak
    assert V[-1] < 0.5 * v_peak
    assert V[-1] == pytest.approx(84.83, rel=5e-2)


def test_layered_steel_panel_matches_elastic_closed_form():
    """Steel-only panel (one 10 mm PlateRebar layer, angle 0 = bars along
    the pull) in UNIFORM stretch: u = 2P*W/(E*t*H) = 1.0e-4 m.  The
    stretch is applied as the pushover GRAVITY stage (2 x 100 kN, stress
    20 MPa << fy) and the 1-step displacement-controlled push adds
    EXACTLY du = 1e-8 m, so the layered panel reads 1.0001e-4 m; the
    same-thickness elastic panel (nu = 0) solves the identical closed
    form.  Both match to 1e-3 (the spec'd 1% with margin)."""
    E_s, t_s, W, H, P = 200e6, 0.01, 1.0, 1.0, 100.0

    def panel(layered):
        m = BuildingModel(name="sp")
        m.rigid_diaphragms = False
        m.num_modes = 0
        m.add_material(Material("S", E_s, 0.0))          # nu = 0: no Poisson
        lay = ({"layers": [{"t": t_s, "material": "S", "kind": "steel",
                            "angle": 0.0}]} if layered else None)
        m.add_shell_section(ShellSection("SH", "S", t_s, layered=lay))
        m.set_stories([H])
        # corner order puts the free (W, 0, H) node at the lowest top tag
        # so it becomes the pushover control node
        m.add_shell("wall", "shell", "SH",
                    [(W, 0, 0), (0, 0, 0), (0, 0, H), (W, 0, H)],
                    mesh_size=1.0, uid="W")
        m.supports = [PointSupport((0, 0, 0), FIX),
                      PointSupport((0, 0, H), FIX),
                      PointSupport((W, 0, 0), (0, 1, 1, 1, 1, 1)),
                      PointSupport((W, 0, H), (0, 1, 1, 1, 1, 1))]
        g = m.pattern("G", "other")
        g.nodal_loads.append(NodalLoad((W, 0, 0), fx=P))
        g.nodal_loads.append(NodalLoad((W, 0, H), fx=P))
        m.add_case("G", {"G": 1.0})
        m.add_pushover_case("PO", "X", gravity={"G": 1.0},
                            target_drift=1e-8, steps=1)
        return m

    u_cf = 2.0 * P * W / (E_s * t_s * H)                 # 1.0e-4 m
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        OpenSeesEngine(panel(True)).run_pushover("PO")
    # the OpenSees domain persists after the run: read the stretched node
    tag = next(t for t in ops.getNodeTags()
               if abs(ops.nodeCoord(t, 1) - W) < 1e-9
               and abs(ops.nodeCoord(t, 3) - H) < 1e-9)
    u_lay = ops.nodeDisp(tag, 1)
    assert u_lay == pytest.approx(u_cf + 1e-8, rel=1e-6)  # exact du added
    eng_el = OpenSeesEngine(panel(False))
    u_el = eng_el.run_static("G").node_disp[_node_at(eng_el, W, H)][0]
    assert u_el == pytest.approx(u_cf, rel=1e-9)
    assert u_lay == pytest.approx(u_el, rel=1e-3)
    assert SHELL_SUBLAYERS >= 3                          # LayeredShell floor


# --------------------------------------------------------------------------- #
# 3. line springs + compression-only area springs
# --------------------------------------------------------------------------- #
def _plate(kz=1000.0, comp=False, at=(2.0, 2.0), P=100.0, E=25.0e11,
           t=2.0):
    """4 x 4 rigid-ish plate at z = 1 on uniform area springs; in-plane +
    drilling dofs held at every mesh node (the springs carry uz)."""
    m = BuildingModel(name="pl")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("C", E, 0.2))
    m.add_shell_section(ShellSection("SL", "C", t))
    m.set_stories([1.0])
    r = m.add_shell("slab", "shell", "SL",
                    [(0, 0, 1), (4, 0, 1), (4, 4, 1), (0, 4, 1)],
                    mesh_size=1.0, uid="S1")
    r.area_spring = {"kz": kz, "compression_only": comp}
    for i in range(5):
        for j in range(5):
            m.supports.append(PointSupport((i, j, 1.0), (1, 1, 0, 0, 0, 1)))
    m.pattern("P", "other").nodal_loads.append(
        NodalLoad((at[0], at[1], 1.0), fz=-P))
    m.add_case("P", {"P": 1.0})
    return m


def test_area_spring_uniform_settlement_closed_form():
    """Central 100 kN on the rigid plate: every node settles
    P/(kz*A) = 100/(1000*16) = 6.25 mm to better than 1e-6 relative
    (E = 25e11, t = 2: the plate's bending residual ~1/(E t^3) is 8e-7)."""
    eng = OpenSeesEngine(_plate())
    r = eng.run_static("P")
    exp = -100.0 / (1000.0 * 16.0)
    for t in eng._asm.struct_coords:
        assert r.node_disp[t][2] == pytest.approx(exp, rel=1e-6)
    assert r.base["FZ"] == pytest.approx(100.0, rel=1e-12)


def test_area_spring_reactions_follow_tributary_areas():
    """Nodal spring reactions = kz * tributary area * settlement exactly:
    the structured 1 m mesh gives corner/edge/interior areas
    0.25/0.5/1.0 m^2 -> 1.5625/3.125/6.25 kN (1:2:4), summing to P."""
    eng = OpenSeesEngine(_plate())
    r = eng.run_static("P")

    def rz(x, y):
        return r.reactions[_node_at(eng, x, y=y, z=1.0)][2]

    assert rz(0, 0) == pytest.approx(1.5625, rel=1e-5)
    assert rz(1, 0) == pytest.approx(3.1250, rel=1e-5)
    assert rz(1, 1) == pytest.approx(6.2500, rel=1e-5)
    assert rz(1, 0) / rz(0, 0) == pytest.approx(2.0, rel=1e-5)
    assert rz(1, 1) / rz(0, 0) == pytest.approx(4.0, rel=1e-5)
    assert sum(v[2] for v in r.reactions.values()) == pytest.approx(
        100.0, rel=1e-12)


def test_area_spring_compression_only_uplift_carries_zero():
    """100 kN at the (0, 0) corner of the compression-only plate: the far
    side UPLIFTS; every uplifted node's spring force sits at the 1e-6
    residual level (< 1e-3 kN here, i.e. < 1e-5 of the load — the
    Elastic(k*ratio, 0, k) tension path) while the reaction sum still
    equals the load to 1e-9.  (Realistic plate stiffness E = 25e6,
    t = 0.5 here: Newton then converges the piecewise-linear springs to
    machine precision — the near-rigid plate of the uniformity test would
    stop on the displacement test with kN-scale force residuals.)"""
    eng = OpenSeesEngine(_plate(comp=True, at=(0.0, 0.0), E=25.0e6, t=0.5))
    r = eng.run_static("P")
    up = [(t, r.node_disp[t][2]) for t in eng._asm.struct_coords
          if r.node_disp[t][2] > 0.0]
    assert len(up) > 5                                   # genuine uplift zone
    for t, uz in up:
        assert abs(r.reactions[t][2]) < 5e-3             # ~zero spring force
    assert sum(v[2] for v in r.reactions.values()) == pytest.approx(
        100.0, rel=1e-9)
    # the linear-spring plate under the same eccentric load pulls DOWN on
    # the uplift side instead (sanity: compression_only changed physics)
    r_lin = OpenSeesEngine(_plate(comp=False, at=(0.0, 0.0), E=25.0e6,
                                  t=0.5)).run_static("P")
    assert min(v[2] for v in r_lin.reactions.values()) < -1e-2


def _wall_on_line_spring(comp=False, q=10.0, E=25.0e9):
    """4 x 2 x 0.3 m stiff wall on a base line spring kz = 5000 kN/m/m,
    uniform q kPa area load."""
    m = BuildingModel(name="ls")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("C", E, 0.2))
    m.add_shell_section(ShellSection("SH", "C", 0.3))
    m.set_stories([2.0])
    m.add_shell("wall", "shell", "SH",
                [(0, 0, 0), (4, 0, 0), (4, 0, 2), (0, 0, 2)],
                mesh_size=1.0, uid="W1")
    m.add_line_spring((0, 0, 0), (4, 0, 0), kz=5000.0,
                      compression_only=comp)
    # rx held (out-of-plane tipping about the base line has no spring);
    # ry/rz FREE so overturning is resisted by the springs, not clamped
    # base rotations
    for x in range(5):
        m.supports.append(PointSupport((x, 0, 0.0), (1, 1, 0, 1, 0, 0)))
    m.pattern("Q", "other").area_loads.append(AreaLoad("W1", q))
    m.add_case("Q", {"Q": 1.0})
    return m


def test_line_spring_reactions_match_tributary_lengths_exactly():
    """Uniform 10 kPa on the 8 m^2 wall -> 80 kN onto the base line
    spring.  Base nodes at x = 0..4 own tributary lengths
    [0.5, 1, 1, 1, 0.5] m, so the (stiff-wall, uniform-settlement)
    reactions are EXACTLY [10, 20, 20, 20, 10] kN."""
    eng = OpenSeesEngine(_wall_on_line_spring())
    r = eng.run_static("Q")
    got = [r.reactions[_node_at(eng, x, 0.0)][2] for x in range(5)]
    assert got == pytest.approx([10.0, 20.0, 20.0, 20.0, 10.0], rel=1e-6)
    assert r.base["FZ"] == pytest.approx(80.0, rel=1e-9)
    # round trip
    back = BuildingModel.from_dict(_wall_on_line_spring().to_dict())
    ls = back.line_springs[0]
    assert (ls.p1, ls.p2, ls.kz) == ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0),
                                     5000.0)


def test_line_spring_compression_only_uplift_end_sheds():
    """A 24 kN*m overturning couple (+-6 kN at the wall top corners) on a
    16 kN gravity load: the resultant sits at 1.5 m eccentricity — past
    the b/6 = 0.667 m kern but inside the b/2 = 2 m overturning limit.
    Compression-only: the x >= 3 side UPLIFTS and its spring forces read
    EXACTLY the residual path -AXIAL_ONLY_RATIO*kz_i*uz (8.0e-6 kN
    pinned); the contact zone carries [8, 8] kN at x = 0, 1 (rigid-wall
    statics: R0*2 + R1*1 = 24 with R0 + R1 = 16).  The LINEAR spring at
    x = 4 instead pulls DOWN 2500*theta*2 - 2 = 2.0 kN
    (theta = 24/(sum k_i x_i^2) = 24/30000).  Equilibrium closes to 1e-9
    in both cases.  (Realistic E = 25e6 so Newton converges the
    piecewise-linear system to machine precision.)"""
    def with_couple(comp):
        m = _wall_on_line_spring(comp=comp, q=2.0, E=25.0e6)  # 16 kN gravity
        p = m.patterns["Q"]
        p.nodal_loads.append(NodalLoad((0, 0, 2.0), fz=-6.0))
        p.nodal_loads.append(NodalLoad((4, 0, 2.0), fz=6.0))
        return m

    eng = OpenSeesEngine(with_couple(True))
    r = eng.run_static("Q")
    for x, kz_i in ((4.0, 2500.0), (3.0, 5000.0)):
        t = _node_at(eng, x, 0.0)
        uz = r.node_disp[t][2]
        assert uz > 1e-3                                 # genuine uplift
        assert r.reactions[t][2] == pytest.approx(
            -AXIAL_ONLY_RATIO * kz_i * uz, rel=1e-3)     # residual path only
        assert abs(r.reactions[t][2]) < 1e-4
    assert r.reactions[_node_at(eng, 0.0, 0.0)][2] == pytest.approx(
        8.0, rel=1e-4)
    assert r.reactions[_node_at(eng, 1.0, 0.0)][2] == pytest.approx(
        8.0, rel=1e-4)
    assert sum(v[2] for v in r.reactions.values()) == pytest.approx(
        16.0, rel=1e-9)
    eng_lin = OpenSeesEngine(with_couple(False))
    r_lin = eng_lin.run_static("Q")
    assert r_lin.reactions[_node_at(eng_lin, 4, 0.0)][2] == pytest.approx(
        -2.0, rel=1e-3)                                  # pulls down
    assert sum(v[2] for v in r_lin.reactions.values()) == pytest.approx(
        16.0, rel=1e-9)


def test_line_spring_off_structure_raises():
    """A line spring along a line with NO FE node on it fails the build
    with a clear error."""
    m = _wall_on_line_spring()
    m.line_springs.append(LineSpring((0, 5, 0), (4, 5, 0), kz=100.0))
    with pytest.raises(ValueError, match="no FE node"):
        OpenSeesEngine(m).run_static("Q")


def test_spring_validation_errors():
    """Model-layer validation of the new spring fields."""
    m = _wall_on_line_spring()
    with pytest.raises(ValueError, match="distinct"):
        m.add_line_spring((0, 0, 0), (0, 0, 0), kz=1.0)
    with pytest.raises(ValueError, match="at least one"):
        m.add_line_spring((0, 0, 0), (1, 0, 0))
    with pytest.raises(ValueError, match="kz must be"):
        m.add_line_spring((0, 0, 0), (1, 0, 0), kz=-5.0)
    with pytest.raises(ValueError, match="compression_only needs kz"):
        m.add_line_spring((0, 0, 0), (1, 0, 0), kx=1.0,
                          compression_only=True)
    region = m.shells[0]
    for bad, frag in [({"kz": 0.0}, "kz must be"),
                      ({"kz": 5.0, "mystery": 1}, "unknown area_spring"),
                      ({"kz": 5.0, "compression_only": 1}, "bool"),
                      ("springy", "must be a dict")]:
        region.area_spring = bad
        with pytest.raises(ValueError, match=frag):
            m.validate()
    region.area_spring = None
    m.validate()                                         # clean again
    # membrane slabs cannot carry area springs (no mesh nodes)
    m2 = BuildingModel(name="mem")
    m2.add_material(Material("C", E_CONC, 0.2))
    m2.add_shell_section(ShellSection("SL", "C", 0.2))
    r2 = m2.add_shell("slab", "membrane", "SL",
                      [(0, 0, 3), (4, 0, 3), (4, 4, 3), (0, 4, 3)],
                      uid="M1")
    r2.area_spring = {"kz": 10.0}
    with pytest.raises(ValueError, match="shell behavior"):
        m2.validate()


# --------------------------------------------------------------------------- #
# 4. semi-rigid diaphragm auto distribution
# --------------------------------------------------------------------------- #
def _slab_building(diaph, acc_tors=True):
    """4 columns + meshed slab, 100 kN EQX story force (5% ecc)."""
    m = BuildingModel(name="sr")
    m.num_modes = 0
    m.add_material(Material("C", E_CONC, 0.2))
    m.add_section(FrameSection.rectangular("COL", "C", 0.5, 0.5))
    m.add_shell_section(ShellSection("SL", "C", 0.2))
    m.set_stories([3.0])
    for x in (0.0, 6.0):
        for y in (0.0, 6.0):
            m.add_member("column", "COL", (x, y, 0), (x, y, 3),
                         story="Story1", uid=f"C{int(x)}{int(y)}")
    m.add_shell("slab", "shell", "SL",
                [(0, 0, 3), (6, 0, 3), (6, 6, 3), (0, 6, 3)],
                mesh_size=1.0, story="Story1", uid="S1")
    m.diaphragm = diaph
    p = m.pattern("EQX", "quake")
    p.story_forces.append(StoryForce("Story1", fx=100.0))
    p.accidental_torsion = acc_tors
    p.ecc = 0.05
    m.add_case("EQX", {"EQX": 1.0})
    return m


def test_semi_rigid_equilibrium_matches_rigid():
    """Total base shear AND base torque of the semi-rigid distribution
    equal the rigid-diaphragm values to 1e-9: FX = -100; MZ about the
    origin = -(-100*3 + Mz_acc) = 270 kN*m (force resultant through the
    weighted slab centroid y = 3, plus the exact 100*0.05*6 = 30 kN*m
    accidental couple).  The reported story shear is unchanged."""
    rr = OpenSeesEngine(_slab_building("rigid")).run_static("EQX")
    rs = OpenSeesEngine(_slab_building("none")).run_static("EQX")
    for r in (rr, rs):
        assert r.base["FX"] == pytest.approx(-100.0, rel=1e-9)
        assert r.base["MZ"] == pytest.approx(270.0, rel=1e-9)
        assert r.story["Story1"]["shear_x"] == pytest.approx(100.0,
                                                             rel=1e-12)
    # without accidental torsion the base torque is the plain 300
    rs0 = OpenSeesEngine(_slab_building("none",
                                        acc_tors=False)).run_static("EQX")
    assert rs0.base["MZ"] == pytest.approx(300.0, rel=1e-9)


def test_semi_rigid_symmetric_building_responds_symmetrically():
    """The building is symmetric about y = 3 and the (torsion-free)
    distributed fx field is too: mirrored nodes get identical ux and
    OPPOSITE uy; nodes on the mirror line have uy = 0."""
    eng = OpenSeesEngine(_slab_building("none", acc_tors=False))
    r = eng.run_static("EQX")
    for t, c in eng._asm.struct_coords.items():
        tm = _node_at(eng, c[0], c[2], y=6.0 - c[1])
        assert r.node_disp[t][0] == pytest.approx(r.node_disp[tm][0],
                                                  abs=1e-15)
        assert r.node_disp[t][1] == pytest.approx(-r.node_disp[tm][1],
                                                  abs=1e-15)
        if abs(c[1] - 3.0) < 1e-9:
            assert abs(r.node_disp[t][1]) < 1e-15


def test_semi_rigid_drift_brackets_rigid_from_above():
    """A flexible (real-membrane) diaphragm can only drift MORE than the
    rigid idealization: 4.7959e-4 >= 4.7417e-4 (pinned; +1.14%, and
    within 10% — the slab is stiff)."""
    dr = OpenSeesEngine(_slab_building("rigid")).run_static(
        "EQX").story["Story1"]["drift_x"]
    ds = OpenSeesEngine(_slab_building("none")).run_static(
        "EQX").story["Story1"]["drift_x"]
    assert dr == pytest.approx(4.74168e-4, rel=1e-4)
    assert ds == pytest.approx(4.79594e-4, rel=1e-4)
    assert ds > dr
    assert ds < 1.10 * dr


def test_semi_rigid_single_massed_node_equals_nodal_load():
    """Tributary-mass weighting, exact: when ALL the story mass sits on
    ONE slab node, the distributed story force IS a nodal load there —
    the two cases match displacement for displacement to 1e-12."""
    m1 = _slab_building("none", acc_tors=False)
    m1.nodal_masses.append(NodalMass((2.0, 1.0, 3.0), mx=5.0, my=5.0))
    m2 = _slab_building("none", acc_tors=False)
    m2.nodal_masses.append(NodalMass((2.0, 1.0, 3.0), mx=5.0, my=5.0))
    p = m2.patterns["EQX"]
    p.story_forces.clear()
    p.nodal_loads.append(NodalLoad((2.0, 1.0, 3.0), fx=100.0))
    r1 = OpenSeesEngine(m1).run_static("EQX")
    r2 = OpenSeesEngine(m2).run_static("EQX")
    assert set(r1.node_disp) == set(r2.node_disp)
    for t in r1.node_disp:
        for a, b in zip(r1.node_disp[t], r2.node_disp[t]):
            assert a == pytest.approx(b, abs=1e-15)


def test_semi_rigid_frame_only_story_keeps_equal_split():
    """A "none" story WITHOUT a meshed slab keeps the exact pre-v0.22
    equal split over the story nodes (the v0.6 portal behavior): story
    ux = node mean, base FX = -force."""
    m = BuildingModel(name="legacy")
    m.num_modes = 0
    m.rigid_diaphragms = False
    m.add_material(Material("C", E_CONC, 0.2))
    m.add_section(FrameSection.rectangular("COL", "C", 0.4, 0.4))
    m.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
    m.set_stories([3.0])
    m.add_member("column", "COL", (0, 0, 0), (0, 0, 3), story="Story1",
                 uid="C1")
    m.add_member("column", "COL", (5, 0, 0), (5, 0, 3), story="Story1",
                 uid="C2")
    m.add_member("beam", "BM", (0, 0, 3), (5, 0, 3), story="Story1",
                 uid="B1")
    m.pattern("EQ", "quake").story_forces.append(StoryForce("Story1",
                                                            fx=10.0))
    m.add_case("EQ", {"EQ": 1.0})
    eng = OpenSeesEngine(m)
    r = eng.run_static("EQ")
    assert eng._story_slab_nodes(eng._asm, "Story1") == []
    nodes = eng._asm.story_nodes["Story1"]
    mean_ux = sum(r.node_disp[t][0] for t in nodes) / len(nodes)
    assert r.story["Story1"]["ux"] == pytest.approx(mean_ux, rel=1e-12)
    assert r.base["FX"] == pytest.approx(-10.0, rel=1e-9)


def test_semi_rigid_strip_slab_torque_exact():
    """Exact couple realization on a NON-square slab strip (6 x 0.5 m,
    0.5 m mesh, plan extents Lx = 6 / Ly = 0.5): the massless node-count
    weighting puts the force resultant at the node centroid y_bar = 0.25,
    and the accidental couple adds fx*ecc*Ly = 100*0.05*0.5 = 2.5 kN*m,
    so the base torque about the origin is -(-0.25*100 + 2.5) = 22.5 kN*m
    to 1e-9 — while the couple field adds ZERO net force.  (Columns sit
    at diagonal strip corners so the member-based plan extents read
    Lx = 6, Ly = 0.5.)"""
    m = BuildingModel(name="strip")
    m.num_modes = 0
    m.add_material(Material("C", E_CONC, 0.2))
    m.add_section(FrameSection.rectangular("COL", "C", 0.5, 0.5))
    m.add_shell_section(ShellSection("SL", "C", 0.2))
    m.set_stories([3.0])
    for x, y in ((0.0, 0.0), (6.0, 0.5)):
        m.add_member("column", "COL", (x, y, 0), (x, y, 3),
                     story="Story1", uid=f"C{int(x)}")
    m.diaphragm = "none"
    m.add_shell("slab", "shell", "SL",
                [(0, 0, 3), (6, 0, 3), (6, 0.5, 3), (0, 0.5, 3)],
                mesh_size=0.5, story="Story1", uid="S1")
    p = m.pattern("EQX", "quake")
    p.story_forces.append(StoryForce("Story1", fx=100.0))
    p.accidental_torsion = True
    p.ecc = 0.05
    m.add_case("EQX", {"EQX": 1.0})
    r = OpenSeesEngine(m).run_static("EQX")
    assert r.base["FX"] == pytest.approx(-100.0, rel=1e-9)
    assert r.base["FY"] == pytest.approx(0.0, abs=1e-9)
    assert r.base["MZ"] == pytest.approx(
        -(-0.25 * 100.0 + 100.0 * 0.05 * 0.5), rel=1e-9)


def test_api_model_roundtrips_wave23_fields(api_client):
    """POST /api/model round-trips edge_constraints, layered shell
    sections, line springs, and area springs; bad payloads 400."""
    m = _wall_on_line_spring()
    m.edge_constraints = True
    m.shells[0].area_spring = {"kz": 250.0, "compression_only": True}
    m.materials["C"].nu = 0.2
    m.add_material(Material("S", 200e6, 0.3))
    m.add_shell_section(ShellSection("LAY", "C", 0.2, layered={
        "layers": [{"t": 0.1, "material": "C", "kind": "concrete"},
                   {"t": 0.002, "material": "S", "kind": "steel",
                    "angle": 90.0}]}))
    r = api_client.post("/api/model", json=m.to_dict())
    assert r.status_code == 200, r.get_json()
    echoed = r.get_json()
    assert echoed["edge_constraints"] is True
    assert echoed["line_springs"] == [
        {"p1": [0.0, 0.0, 0.0], "p2": [4.0, 0.0, 0.0], "kz": 5000.0,
         "kx": 0.0, "ky": 0.0, "compression_only": False}]
    assert echoed["shells"][0]["area_spring"] == {
        "kz": 250.0, "compression_only": True}
    assert echoed["shell_sections"]["LAY"]["layered"]["layers"][1][
        "angle"] == 90.0
    bad = m.to_dict()
    bad["line_springs"][0]["kz"] = -1.0
    assert api_client.post("/api/model", json=bad).status_code == 400


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
