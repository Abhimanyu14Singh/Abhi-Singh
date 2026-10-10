"""Verification family 2 -- SHELLS / PLATES / WALLS.

Modelled on the CSI *SAP2000 Software Verification* shell examples (plate
bending of square plates, the MacNeal-Harder / Belytschko "obstacle
course": Scordelis-Lo roof, pinched cylinder, pinched hemisphere, Morley
skew plate) and the *ETABS* wall examples (cantilever shear-wall drift,
wall with openings).

SkyFrame meshes shells with OpenSees ``ShellMITC4`` (flat 4-node
Mindlin-Reissner + drilling DOF).  Shell results are MESH-DEPENDENT by
nature, so every problem here is a CONVERGENCE STUDY: the test asserts
monotone convergence toward the reference AND the finest-mesh error
against a tolerance taken from the observed convergence rate (never
widened to hide a deviation).  Curved shells are built from planar
single-element regions (``_vhelp.grid_shell``) so the discretisation is
exactly the classic n x n benchmark mesh.
"""

import math

import numpy as np
import pytest

from skyframe.core.model import AreaLoad, Opening, ShellRegion

from _q8 import solve_wall
from _vhelp import (FIXED, check, disp, grid_shell, load, new_model,
                    planar_xz, run_case, shell_section, support)

FAM = "Shells"


def _merge(r, s):
    return [max(a, b) for a, b in zip(r, s)]


def _quad_centroids(eng):
    return np.array([np.mean([eng._asm.node_coords[t] for t in q["nodes"]],
                             axis=0) for q in eng._asm.shell_quads])


# --------------------------------------------------------------------------- #
# S1/S2  square plates under uniform load (Timoshenko)
# --------------------------------------------------------------------------- #
def _square_plate(n, clamped, a=1.0, t=0.01, E=2.0e8, nu=0.3, q=1.0):
    m = new_model("PL", E, nu=nu, heights=[1.0])
    shell_section(m, "SH", t)
    m.shells.append(ShellRegion("S1", "slab", "shell", "SH",
                                [(0, 0, 0), (a, 0, 0), (a, a, 0), (0, a, 0)],
                                mesh_size=a / n))
    for k in range(n + 1):
        s = a * k / n
        # HARD simple support: w = 0 and the rotation about the edge NORMAL
        # (tangential slope) = 0 -- the Kirchhoff SS condition
        for p, r in (((s, 0, 0), (0, 0, 1, 0, 1, 0)),
                     ((s, a, 0), (0, 0, 1, 0, 1, 0)),
                     ((0, s, 0), (0, 0, 1, 1, 0, 0)),
                     ((a, s, 0), (0, 0, 1, 1, 0, 0))):
            support(m, p, FIXED if clamped else r)
    if not clamped:
        support(m, (0, 0, 0), (1, 1, 1, 0, 0, 1))
        support(m, (a, 0, 0), (0, 1, 1, 0, 0, 0))
    m.pattern("Q").area_loads.append(AreaLoad("S1", q))
    m.add_case("Q", {"Q": 1.0})
    eng, r = run_case(m, "Q")
    D = E * t ** 3 / (12 * (1 - nu ** 2))
    w = -disp(eng, r, (a / 2, a / 2, 0), 2) * D / (q * a ** 4)
    cen = _quad_centroids(eng)
    near = np.argsort(np.linalg.norm(cen[:, :2] - [a / 2, a / 2], axis=1))[:4]
    Mc = abs(np.mean([r.shell_forces[i][3] for i in near])) / (q * a * a)
    # edge moment at (0, a/2): quadratic extrapolation from the centroids
    # of the first three element rows (x = h/2, 3h/2, 5h/2) to x = 0
    h = a / n
    vals = []
    for xc in (h / 2, 1.5 * h, 2.5 * h):
        d = np.linalg.norm(cen[:, :2] - [xc, a / 2], axis=1)
        idx = np.argsort(d)[:2]
        vals.append(np.mean([r.shell_forces[i][3] for i in idx]))
    Me = abs(15 * vals[0] - 10 * vals[1] + 3 * vals[2]) / 8 / (q * a * a)
    return w, Mc, Me


def test_s01_simply_supported_square_plate():
    """Simply supported square plate, uniform load q, nu = 0.3, a/t = 100
    (SAP2000 Verification "plate bending" examples; Timoshenko &
    Woinowsky-Krieger, *Theory of Plates and Shells*, Table 8:
    w = 0.00406 q a^4/D, M_x = 0.0479 q a^2; exact series values 0.00406235
    / 0.0478864).  Hard SS; 8/16/32 meshes."""
    res = {n: _square_plate(n, False) for n in (8, 16, 32)}
    w_ref, M_ref = 0.00406235, 0.0478864
    assert res[8][0] < res[16][0] < res[32][0]       # monotone from below
    S = "Timoshenko & Woinowsky-Krieger Table 8 (Navier series)"
    note = "8/16/32: " + "/".join(f"{res[n][0]:.6f}" for n in (8, 16, 32))
    check("SS square plate, UDL (32x32)", S, "centre deflection wD/qa^4",
          w_ref, res[32][0], 2e-3, family=FAM, note=note)
    check("SS square plate, UDL (32x32)", S,
          "centre moment Mx/qa^2 (4 centre quads)", M_ref, res[32][1], 5e-3,
          family=FAM, note="quad centroids sit h/2 off centre")


def test_s02_clamped_square_plate():
    """Clamped square plate, uniform load (Timoshenko & Woinowsky-Krieger
    Table 35: w = 0.00126 q a^4/D, M_centre = 0.0231 q a^2, M_edge =
    -0.0513 q a^2; accurate series values from Taylor & Govindjee (2004)
    "Solution of clamped rectangular plate problems", Commun. Numer. Meth.
    Eng. 20: 0.00126532, 0.0229051, 0.0513338).  Mindlin MITC4 at a/t = 100
    adds ~0.08% shear deflection (expected, small)."""
    res = {n: _square_plate(n, True) for n in (8, 16, 32)}
    w_ref, Mc_ref, Me_ref = 0.00126532, 0.0229051, 0.0513338
    assert res[8][0] < res[16][0] < res[32][0]       # monotone from below
    S = ("Timoshenko & Woinowsky-Krieger Table 35; Taylor & Govindjee "
         "(2004)")
    note = "8/16/32: " + "/".join(f"{res[n][0]:.7f}" for n in (8, 16, 32))
    check("Clamped square plate, UDL (32x32)", S,
          "centre deflection wD/qa^4", w_ref, res[32][0], 2e-3, family=FAM,
          note=note)
    check("Clamped square plate, UDL (32x32)", S, "centre moment / qa^2",
          Mc_ref, res[32][1], 5e-3, family=FAM)
    check("Clamped square plate, UDL (32x32)", S,
          "edge moment / qa^2 (extrapolated to edge)", Me_ref, res[32][2],
          1e-2, family=FAM,
          note="8/16/32: " + "/".join(f"{res[n][2]:.4f}" for n in
                                      (8, 16, 32)))


# --------------------------------------------------------------------------- #
# S3  Scordelis-Lo roof
# --------------------------------------------------------------------------- #
def _scordelis_lo(n):
    R, L2, t, E, nu, q = 25.0, 25.0, 0.25, 4.32e8, 0.0, 90.0
    ang = math.radians(40.0)
    m = new_model("SL", E, nu=nu, heights=[100.0])
    shell_section(m, "SH", t)
    pts = grid_shell(m, "SH", lambda i, j: (L2 * i / n,
                                             R * math.sin(ang * j / n),
                                             R * math.cos(ang * j / n)), n, n)
    for (i, j), p in pts.items():
        r = [0] * 6
        if i == 0:                      # symmetry plane x = 0 (mid-span)
            r = _merge(r, (1, 0, 0, 0, 1, 1))
        if j == 0:                      # symmetry plane y = 0 (crown)
            r = _merge(r, (0, 1, 0, 1, 0, 1))
        if i == n:                      # rigid end diaphragm: uy = uz = 0
            r = _merge(r, (0, 1, 1, 0, 0, 0))
        if any(r):
            support(m, p, r)
    pat = m.pattern("G")
    for sh in m.shells:
        pat.area_loads.append(AreaLoad(sh.uid, q))     # 90 psf of surface
    m.add_case("G", {"G": 1.0})
    eng, r = run_case(m, "G")
    return -disp(eng, r, pts[(0, n)], 2)


def test_s03_scordelis_lo_roof():
    """Scordelis-Lo barrel vault (MacNeal & Harder 1985, "A proposed
    standard set of problems to test finite element accuracy", Finite
    Elem. Anal. Des. 1; SAP2000 Verification shell example): R = 25 ft,
    L = 50 ft, 40 deg, t = 0.25 ft, E = 4.32e8 psf, nu = 0, self weight
    90 psf, rigid end diaphragms.  Reference vertical displacement at the
    mid-span free edge 0.3024 ft (Belytschko et al. 1985; MacNeal-Harder
    quote 0.3086 from deep-shell theory and note elements converge to
    ~0.302).  Quarter model, n x n flat MITC4 facets."""
    res = {n: _scordelis_lo(n) for n in (8, 16, 32)}
    ref = 0.3024
    assert res[8] < res[16] < res[32] < ref * 1.002
    check("Scordelis-Lo roof (32x32 quarter)",
          "MacNeal & Harder (1985); Belytschko et al. (1985)",
          "free-edge mid-span vertical displacement [ft]", ref, res[32],
          1e-2, family=FAM,
          note="8/16/32: " + "/".join(f"{res[n]:.4f}" for n in (8, 16, 32))
          + "; 48x48 = 0.3011 (h^1.5 convergence)")


# --------------------------------------------------------------------------- #
# S4  pinched cylinder with end diaphragms
# --------------------------------------------------------------------------- #
def _pinched_cylinder(n):
    R, L2, t, E, nu, P = 300.0, 300.0, 3.0, 3.0e6, 0.3, 1.0
    m = new_model("PC", E, nu=nu, heights=[1000.0])
    shell_section(m, "SH", t)
    pts = grid_shell(m, "SH", lambda i, j: (
        L2 * i / n, R * math.cos(math.pi / 2 * j / n),
        R * math.sin(math.pi / 2 * j / n)), n, n)
    for (i, j), p in pts.items():
        r = [0] * 6
        if i == 0:                      # mid-length symmetry plane
            r = _merge(r, (1, 0, 0, 0, 1, 1))
        if j == 0:                      # plane z = 0
            r = _merge(r, (0, 0, 1, 1, 1, 0))
        if j == n:                      # plane y = 0
            r = _merge(r, (0, 1, 0, 1, 0, 1))
        if i == n:                      # rigid diaphragm
            r = _merge(r, (0, 1, 1, 0, 0, 0))
        if any(r):
            support(m, p, r)
    load(m, "P", pts[(0, n)], fz=-P / 4)        # octant carries P/4
    m.add_case("P", {"P": 1.0})
    eng, r = run_case(m, "P")
    return -disp(eng, r, pts[(0, n)], 2)


def test_s04_pinched_cylinder():
    """Pinched cylinder with rigid end diaphragms (MacNeal & Harder 1985;
    Belytschko obstacle course; SAP2000 Verification shell example):
    R = 300, L = 600, t = 3, E = 3e6, nu = 0.3, P = 1.  Reference radial
    displacement under the load 1.8248e-5.  Octant model."""
    res = {n: _pinched_cylinder(n) for n in (8, 16, 32)}
    ref = 1.8248e-5
    assert res[8] < res[16] < res[32] < ref
    check("Pinched cylinder (32x32 octant)",
          "MacNeal & Harder (1985); Belytschko et al. (1985)",
          "displacement under load", ref, res[32], 1.5e-2, family=FAM,
          note="8/16/32 ratio: " + "/".join(f"{res[n] / ref:.3f}"
                                            for n in (8, 16, 32)))


# --------------------------------------------------------------------------- #
# S5  pinched hemisphere with 18 deg hole
# --------------------------------------------------------------------------- #
def _pinched_hemisphere(n):
    R, t, E, nu, F = 10.0, 0.04, 6.825e7, 0.3, 1.0
    m = new_model("PH", E, nu=nu, heights=[100.0])
    shell_section(m, "SH", t)
    top = math.radians(72.0)

    def xyz(i, j):
        th, ph = math.pi / 2 * i / n, top * j / n
        return (R * math.cos(ph) * math.cos(th),
                R * math.cos(ph) * math.sin(th), R * math.sin(ph))
    pts = grid_shell(m, "SH", xyz, n, n)
    for (i, j), p in pts.items():
        r = [0] * 6
        if i == 0:                      # plane y = 0
            r = _merge(r, (0, 1, 0, 1, 0, 1))
        if i == n:                      # plane x = 0
            r = _merge(r, (1, 0, 0, 0, 1, 1))
        if any(r):
            support(m, p, r)
    support(m, pts[(0, n)], (0, 0, 1, 0, 0, 0))     # rigid-body z only
    load(m, "P", pts[(0, 0)], fx=F)
    load(m, "P", pts[(n, 0)], fy=-F)
    m.add_case("P", {"P": 1.0})
    eng, r = run_case(m, "P")
    return disp(eng, r, pts[(0, 0)], 0)


def test_s05_pinched_hemisphere():
    """Pinched hemisphere with an 18 deg hole (MacNeal & Harder 1985;
    SAP2000 Verification shell example): R = 10, t = 0.04, E = 6.825e7,
    nu = 0.3, alternating radial loads F = 2 (1 on the quarter model).
    Reference radial displacement 0.094 (MacNeal-Harder; later converged
    studies give 0.0924)."""
    res = {n: _pinched_hemisphere(n) for n in (8, 16, 32)}
    ref = 0.094
    assert res[8] < res[16] < res[32]
    check("Pinched hemisphere (32x32 quarter)",
          "MacNeal & Harder (1985)", "radial displacement under load", ref,
          res[32], 1.5e-2, family=FAM,
          note="8/16/32: " + "/".join(f"{res[n]:.4f}" for n in (8, 16, 32))
          + "; vs 0.0924: +0.7%")


# --------------------------------------------------------------------------- #
# S6  Morley 30 deg skew plate
# --------------------------------------------------------------------------- #
def _morley(n):
    a, t, nu, q = 1.0, 0.01, 0.3, 1.0
    E = 12 * (1 - nu ** 2) / t ** 3          # D = 1
    sk = math.radians(30.0)
    m = new_model("MS", E, nu=nu, heights=[100.0])
    shell_section(m, "SH", t)
    c = [(0, 0, 0), (a, 0, 0), (a + a * math.cos(sk), a * math.sin(sk), 0),
         (a * math.cos(sk), a * math.sin(sk), 0)]
    m.shells.append(ShellRegion("S1", "slab", "shell", "SH", c,
                                mesh_size=a / n))

    def P(u, v):
        return tuple(c[0][k] + u * (c[1][k] - c[0][k])
                     + v * (c[3][k] - c[0][k]) for k in range(3))
    for k in range(n + 1):
        s = k / n
        for p in (P(s, 0), P(s, 1), P(0, s), P(1, s)):
            support(m, p, (0, 0, 1, 0, 0, 0))     # soft SS (w = 0)
    support(m, c[0], (1, 1, 1, 0, 0, 1))
    support(m, c[1], (0, 1, 1, 0, 0, 0))
    m.pattern("Q").area_loads.append(AreaLoad("S1", q))
    m.add_case("Q", {"Q": 1.0})
    eng, r = run_case(m, "Q")
    return -disp(eng, r, P(0.5, 0.5), 2)


def test_s06_morley_skew_plate():
    """Morley 30 deg rhombic plate, simply supported, uniform load
    (Morley 1963, *Skew Plates and Structures*; NAFEMS LE / Abaqus
    benchmark 2.3.4 family; SAP2000 Verification skew-plate example):
    centre deflection w = 0.408e-3 q a^4 / D (Kirchhoff series).  The
    obtuse-corner singularity makes every element converge slowly; the
    skew edges cannot take the hard-SS rotation condition through global
    restraints, so the Mindlin soft-SS boundary layer makes the finest
    meshes overshoot slightly (48x48 = 0.4110e-3, +0.7%) -- documented."""
    res = {n: _morley(n) for n in (8, 16, 32)}
    ref = 0.408e-3
    assert res[8] < res[16] < res[32]
    check("Morley 30 deg skew plate (32x32)", "Morley (1963)",
          "centre deflection wD/qa^4", ref, res[32], 1.5e-2, family=FAM,
          note="8/16/32 (x1e3): " + "/".join(f"{1e3 * res[n]:.4f}"
                                            for n in (8, 16, 32)))


# --------------------------------------------------------------------------- #
# S7/S8  ETABS-style walls vs an independent Q8 plane-stress solution
# --------------------------------------------------------------------------- #
WALL_E, WALL_NU, WALL_T, WALL_P = 25.0e6, 0.2, 0.3, 1000.0


def _door_cells(h):
    """Q8 cell mask: a 1.5 m x 2.25 m door centred in each 3 m story."""
    def omit(i, j):
        x0, x1, z0, z1 = i * h, (i + 1) * h, j * h, (j + 1) * h
        return any(x0 >= 2.25 - 1e-9 and x1 <= 3.75 + 1e-9
                   and z0 >= 3 * k - 1e-9 and z1 <= 3 * k + 2.25 + 1e-9
                   for k in range(3))
    return omit


def _sky_wall(Lx, Lz, h, doors=False):
    """SkyFrame wall in the X-Z plane, fixed base, uniform top shear
    traction (consistent Q4 edge weights), drift at the top centre."""
    m = new_model("W", WALL_E, nu=WALL_NU, heights=[3.0] * round(Lz / 3))
    planar_xz(m)
    shell_section(m, "SH", WALL_T)
    ops_ = ([Opening(2.25 / Lx, 3 * k / Lz, 3.75 / Lx, (3 * k + 2.25) / Lz)
             for k in range(3)] if doors else [])
    m.shells.append(ShellRegion("W1", "wall", "shell", "SH",
                                [(0, 0, 0), (Lx, 0, 0), (Lx, 0, Lz),
                                 (0, 0, Lz)], mesh_size=h, openings=ops_))
    nx = round(Lx / h)
    for i in range(nx + 1):
        x = i * h
        if not (doors and 2.25 + 1e-9 < x < 3.75 - 1e-9):
            support(m, (x, 0, 0))
        load(m, "P", (x, 0, Lz),
             fx=WALL_P / Lx * h * (0.5 if i in (0, nx) else 1.0))
    m.add_case("P", {"P": 1.0})
    eng, r = run_case(m, "P")
    return disp(eng, r, (Lx / 2, 0, Lz), 0)


def _q8_wall(Lx, Lz, h, doors=False):
    sol = solve_wall(Lx, Lz, round(Lx / h), round(Lz / h), WALL_T, WALL_E,
                     WALL_NU, WALL_P, _door_cells(h) if doors else None)
    return sol[(round(Lx / 2, 9), round(Lz, 9))][0]


def _richardson(u_vals):
    """Three-mesh (h, h/2, h/4) Richardson extrapolation with the OBSERVED
    order p; returns (u_inf, p)."""
    u1, u2, u3 = u_vals
    p = math.log((u2 - u1) / (u3 - u2)) / math.log(2.0)
    return u3 + (u3 - u2) / (2 ** p - 1), p


def test_s07_cantilever_shear_wall_drift():
    """Cantilever shear wall drift (ETABS Verification "wall" examples):
    3 m x 9 m (3 stories) x 0.3 m wall, E = 25 GPa, nu = 0.2, fixed base,
    1000 kN top shear.  References: (1) the converged independent Q8
    plane-stress solution (``_q8.py``, Richardson-extrapolated, h^2);
    (2) Timoshenko beam theory PH^3/3EI + PH/(5/6 G A) = 15.552 mm, which
    the 2D solution undercuts by ~0.47% (the fully fixed base also stops
    section warping -- a known beam-theory limitation, hence the looser
    tolerance on that line)."""
    Lx, Lz = 3.0, 9.0
    hs = (0.375, 0.1875, 0.09375)
    sky = [_sky_wall(Lx, Lz, h) for h in hs]
    q8 = [_q8_wall(Lx, Lz, h) for h in (0.75, 0.375, 0.1875)]
    q8_inf = q8[2] + (q8[2] - q8[1]) / 3.0            # p = 2 (smooth)
    assert sky[0] < sky[1] < sky[2] < q8_inf
    I = WALL_T * Lx ** 3 / 12
    G = WALL_E / (2 * (1 + WALL_NU))
    timo = (WALL_P * Lz ** 3 / (3 * WALL_E * I)
            + WALL_P * Lz / (5 / 6 * G * WALL_T * Lx))
    note = ("h=0.375/0.1875/0.094: "
            + "/".join(f"{1e3 * u:.4f}" for u in sky) + " mm")
    check("Cantilever shear wall drift (h = 0.094 m)",
          "Independent numpy Q8 plane stress (converged); ETABS wall class",
          "top drift [m]", q8_inf, sky[2], 2e-3, family=FAM, note=note)
    check("Cantilever shear wall drift (h = 0.094 m)",
          "Timoshenko beam (ETABS hand calc)", "top drift [m]", timo,
          sky[2], 1e-2, family=FAM,
          note=f"2D solution is {100 * (q8_inf / timo - 1):+.2f}% vs beam")


def test_s08_shear_wall_with_openings():
    """Coupled shear wall with a door opening in every story (ETABS
    Verification "wall with openings" class): 6 m x 9 m x 0.3 m, 1.5 x 2.25
    m doors centred in each 3 m story, fixed base, 1000 kN top shear.  The
    re-entrant door corners make BOTH SkyFrame (bilinear MITC4 membrane)
    and the independent Q8 converge slowly (~h^1.1, the corner singularity
    exponent 2*0.544); the references are three-mesh Richardson
    extrapolations with the observed order.  Asserted: the extrapolated
    continuum drifts agree to 0.5%, and the finest SkyFrame mesh is within
    3% (documented convergence study)."""
    Lx, Lz = 6.0, 9.0
    hs = (0.375, 0.1875, 0.09375)
    sky = [_sky_wall(Lx, Lz, h, doors=True) for h in hs]
    q8 = [_q8_wall(Lx, Lz, h, doors=True) for h in hs]
    assert sky[0] < sky[1] < sky[2]
    assert q8[0] < q8[1] < q8[2]
    q8_inf, pq = _richardson(q8)
    sk_inf, ps = _richardson(sky)
    S = ("Independent numpy Q8 plane stress, Richardson-extrapolated; "
         "ETABS wall-with-openings class")
    check("Shear wall with door openings (extrapolated)", S,
          "continuum top drift [m]", q8_inf, sk_inf, 5e-3, family=FAM,
          note=f"observed order SkyFrame p={ps:.2f}, Q8 p={pq:.2f}")
    check("Shear wall with door openings (h = 0.094 m)", S,
          "top drift [m]", q8_inf, sky[2], 3e-2, family=FAM,
          note="h=0.375/0.1875/0.094: " + "/".join(f"{1e3 * u:.4f}"
                                                   for u in sky) + " mm")
