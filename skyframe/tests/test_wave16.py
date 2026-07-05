"""Wave 16 backend tests (v0.15):

1. Advanced link types (seismic protection devices): damper / gap / hook /
   isolator — model round-trip + validation, and engine behavior against
   hand-derived closed forms:
     * damper: static solve UNCHANGED by the damper (a Maxwell damper
       carries no static force); free-vibration added damping ratio
       zeta_add = cd / (2 m wn) recovered by log decrement;
     * gap/hook: zero force until the pair closes/opens by the gap/slack,
       then k*(delta - g), matched against an independent two-spring
       numpy solve;
     * isolator: elastic lateral period T = 2 pi sqrt(m / (4 k1)); static
       bilinear response past 4*Fy against the hand bilinear law; nonlinear
       TH peak against an independent numpy bilinear-kinematic Newmark
       integrator (the Wave-7 benchmark approach).
2. Wall piers: per-story P/V/M of labeled walls from EXACT nodal free-body
   cuts — cantilever wall (V = V0, P = P0, M = V0*h), 2-story wall (story
   shear constant, moment growing linearly with depth), exact combo
   superposition, auto_pier_walls, membrane-wall skip warning.
3. API: link_type/params + pier + auto_pier_walls round-trip through
   POST /api/model; 400 on bad link_type / incomplete params.

Every expected number is hand-derived (spring statics, SDOF dynamics,
free-body equilibrium), independent of the module under test.
Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import numpy as np
import pytest

from skyframe.core.mesh import mesh_model
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalLoad, NodalMass, PointSupport,
                                 ShellRegion, ShellSection)
from skyframe.engine.opensees_engine import OpenSeesEngine

E_CONC = 25e6           # kPa


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cantilever_column(name, L=3.0, size=0.3):
    """Fixed-base vertical column; lateral tip stiffness k = 3EI/L^3 exact."""
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.num_modes = 1
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.set_stories([L])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                   story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    return mdl


K_COL = 3.0 * E_CONC * (0.3 ** 4 / 12.0) / 3.0 ** 3      # = 1875 kN/m


def _node_at(asm_coords, pred):
    tags = [t for t, c in asm_coords.items() if pred(c)]
    assert tags, "no node matches predicate"
    return tags[0]


# --------------------------------------------------------------------------- #
# 1. model layer: link_type/params round-trip + validation
# --------------------------------------------------------------------------- #
def test_link_types_roundtrip_and_defaults():
    mdl = _cantilever_column("links")
    mdl.add_link((0, 0, 3), (1, 0, 3), link_type="damper",
                 params={"cd": 25.0, "alpha": 0.7}, uid="D1")
    mdl.add_link((0, 0, 3), (1, 0, 3), link_type="gap",
                 params={"k": 5000.0, "gap": 0.01}, uid="G1")
    mdl.add_link((0, 0, 3), (1, 0, 3), link_type="hook",
                 params={"k": 5000.0, "slack": 0.02}, uid="H1")
    mdl.add_link((0, 0, 0), (0, 0, 0.3), link_type="isolator",
                 params={"k1": 2000.0, "k2": 200.0, "Fy": 20.0}, uid="I1")
    mdl.add_link((0, 0, 3), (0, 0, 3), [1e4, 0, 0, 0, 0, 0], uid="E1")

    back = BuildingModel.from_dict(mdl.to_dict())
    by_uid = {lk.uid: lk for lk in back.links}
    assert by_uid["D1"].link_type == "damper"
    assert by_uid["D1"].params == {"cd": 25.0, "alpha": 0.7}
    assert by_uid["G1"].params["gap"] == 0.01
    assert by_uid["H1"].params["slack"] == 0.02
    assert by_uid["I1"].link_type == "isolator"
    assert by_uid["E1"].link_type == "elastic"
    assert by_uid["E1"].stiffness[0] == 1e4

    # pre-v0.15 file: no link_type/params keys -> elastic
    d = mdl.to_dict()
    for ld in d["links"]:
        ld.pop("link_type")
        ld.pop("params")
    d["links"] = [ld for ld in d["links"] if ld["uid"] == "E1"]
    old = BuildingModel.from_dict(d)
    assert old.links[0].link_type == "elastic"
    assert old.links[0].params == {}


def test_link_validation_errors():
    mdl = _cantilever_column("badlinks")
    with pytest.raises(ValueError, match="link_type"):
        mdl.add_link((0, 0, 3), (1, 0, 3), link_type="bumper",
                     params={"cd": 1.0})
    with pytest.raises(ValueError, match="missing required param 'cd'"):
        mdl.add_link((0, 0, 3), (1, 0, 3), link_type="damper", params={})
    with pytest.raises(ValueError, match="unknown param"):
        mdl.add_link((0, 0, 3), (1, 0, 3), link_type="damper",
                     params={"cd": 1.0, "zeta": 0.1})
    with pytest.raises(ValueError, match="zero length"):
        mdl.add_link((0, 0, 3), (0, 0, 3), link_type="damper",
                     params={"cd": 1.0})
    with pytest.raises(ValueError, match="alpha"):
        mdl.add_link((0, 0, 3), (1, 0, 3), link_type="damper",
                     params={"cd": 1.0, "alpha": 3.0})
    with pytest.raises(ValueError, match="gap must be >= 0"):
        mdl.add_link((0, 0, 3), (1, 0, 3), link_type="gap",
                     params={"k": 100.0, "gap": -0.01})
    with pytest.raises(ValueError, match="k2 must satisfy"):
        mdl.add_link((0, 0, 0), (0, 0, 0.3), link_type="isolator",
                     params={"k1": 100.0, "k2": 200.0, "Fy": 5.0})
    with pytest.raises(ValueError, match="vertical"):
        mdl.add_link((0, 0, 0), (1, 0, 0.3), link_type="isolator",
                     params={"k1": 100.0, "k2": 10.0, "Fy": 5.0})
    # elastic links keep the v0.5 rule: at least one stiffness > 0
    with pytest.raises(ValueError, match="at least one"):
        mdl.add_link((0, 0, 3), (1, 0, 3), [0.0] * 6)


# --------------------------------------------------------------------------- #
# 2. damper
# --------------------------------------------------------------------------- #
def test_damper_contributes_nothing_static():
    """A static case with a damper equals the damper-free case to 1e-9
    (Maxwell damper: no static force; verified stiffness-free too)."""
    def build(with_damper):
        mdl = _cantilever_column("dmp" if with_damper else "nodmp")
        if with_damper:
            mdl.add_link((0, 0, 3.0), (1.5, 0, 3.0), link_type="damper",
                         params={"cd": 40.0})
        mdl.pattern("F", "other").nodal_loads.append(
            NodalLoad((0, 0, 3.0), fx=10.0))
        mdl.add_case("F", {"F": 1.0})
        return OpenSeesEngine(mdl).run_static("F")

    r0, r1 = build(False), build(True)
    for t, v0 in r0.node_disp.items():
        v1 = r1.node_disp[t]
        assert max(abs(a - b) for a, b in zip(v0, v1)) < 1e-9
    # tip displacement itself is the exact cantilever closed form
    tip = max(v[0] for v in r0.node_disp.values())
    assert tip == pytest.approx(10.0 / K_COL, rel=1e-9)
    for k in ("FX", "FY", "FZ", "MX", "MY", "MZ"):
        assert r1.base[k] == pytest.approx(r0.base[k], abs=1e-9)


def test_damper_free_vibration_added_damping():
    """SDOF free decay: zeta_add = cd/(2 m wn) ~ 0.08 via log decrement
    within 5%; response decays monotonically peak to peak, no NaN."""
    m_t = 10.0
    wn = math.sqrt(K_COL / m_t)
    zeta = 0.08
    cd = 2.0 * zeta * m_t * wn
    Tn = 2.0 * math.pi / wn
    dt = Tn / 100.0
    accel = [3.0] * 10 + [0.0] * 3000        # velocity pulse, then free decay

    mdl = _cantilever_column("dampfv")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=m_t))
    mdl.add_link((0, 0, 3.0), (1.5, 0, 3.0), link_type="damper",
                 params={"cd": cd})
    mdl.add_th_case("FV", "X", accel, dt, damping=1e-9)   # Rayleigh ~ 0
    res = OpenSeesEngine(mdl).run_time_history("FV")
    us = res.story_ux["Story1"]
    assert all(math.isfinite(u) for u in us)

    u_max = max(abs(u) for u in us)
    peaks = [us[i] for i in range(30, len(us) - 1)
             if us[i] > us[i - 1] and us[i] > us[i + 1]
             and us[i] > 0.01 * u_max]         # skip tail micro-ripples
    assert len(peaks) >= 7
    # energy sanity: strictly decaying positive peaks
    assert all(a > b for a, b in zip(peaks, peaks[1:]))
    n = 6
    delta = math.log(peaks[0] / peaks[n]) / n
    z_meas = delta / math.sqrt(4.0 * math.pi ** 2 + delta ** 2)
    assert z_meas == pytest.approx(zeta, rel=0.05), (
        f"log-decrement zeta {z_meas:.5f} vs cd/(2 m wn) = {zeta}")
    # decayed nearly to rest after ~30 cycles at 8%
    assert abs(us[-1]) < 0.02 * max(abs(u) for u in us)


# --------------------------------------------------------------------------- #
# 3. gap / hook
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ltype,sgn,key", [("gap", +1.0, "gap"),
                                           ("hook", -1.0, "slack")])
def test_gap_hook_engagement(ltype, sgn, key):
    """Column tip pushed toward (gap) / away from (hook) a device with
    opening g: link force ZERO at delta = g/2, then k*(delta - g) matching
    an independent numpy two-spring solve to 1e-6."""
    g, k_dev = 0.01, 5000.0
    mdl = _cantilever_column(ltype)
    mdl.add_link((0, 0, 3.0), (1.0, 0, 3.0), link_type=ltype,
                 params={"k": k_dev, key: g})
    F1 = sgn * K_COL * (0.5 * g)              # -> delta = g/2, not engaged
    F2 = sgn * 40.0                           # well past engagement
    mdl.pattern("F1", "other").nodal_loads.append(
        NodalLoad((0, 0, 3.0), fx=F1))
    mdl.pattern("F2", "other").nodal_loads.append(
        NodalLoad((0, 0, 3.0), fx=F2))
    mdl.add_case("F1", {"F1": 1.0})
    mdl.add_case("F2", {"F2": 1.0})

    eng = OpenSeesEngine(mdl)
    ra = eng.run_static("F1")
    rb = eng.run_static("F2")
    coords = eng._asm.node_coords
    anchor = _node_at(coords, lambda c: abs(c[0] - 1.0) < 1e-9)
    tip = _node_at(coords, lambda c: abs(c[0]) < 1e-9 and abs(c[2] - 3) < 1e-9)

    # (a) not engaged: tip moves g/2, the grounded anchor carries nothing
    assert ra.node_disp[tip][0] == pytest.approx(sgn * 0.5 * g, rel=1e-9)
    assert abs(ra.reactions[anchor][0]) < 1e-6

    # (b) engaged: independent 2x2 numpy solve of the two-spring system
    #     K_col*u + k_dev*(u - sgn*g) = F2  (device active past the opening)
    u_hand = float(np.linalg.solve(
        np.array([[K_COL + k_dev]]),
        np.array([F2 + sgn * k_dev * g]))[0])
    f_link = k_dev * (abs(u_hand) - g)
    assert f_link > 5.0                       # genuinely engaged
    assert rb.node_disp[tip][0] == pytest.approx(u_hand, rel=1e-6)
    assert abs(rb.reactions[anchor][0]) == pytest.approx(f_link, rel=1e-6)
    # global equilibrium: base FX (column + anchor) balances the load
    assert rb.base["FX"] == pytest.approx(-F2, rel=1e-9)


def test_gap_static_solved_with_newton():
    """A model containing a gap link routes static cases through Newton
    (documented); a pure-damper model keeps the linear path."""
    mdl = _cantilever_column("newton")
    mdl.add_link((0, 0, 3.0), (1.0, 0, 3.0), link_type="gap",
                 params={"k": 100.0, "gap": 0.01})
    eng = OpenSeesEngine(mdl)
    assert eng._nonlinear_static_links_present()
    mdl2 = _cantilever_column("lin")
    mdl2.add_link((0, 0, 3.0), (1.0, 0, 3.0), link_type="damper",
                  params={"cd": 10.0})
    eng2 = OpenSeesEngine(mdl2)
    assert eng2._device_links_present()
    assert not eng2._nonlinear_static_links_present()


# --------------------------------------------------------------------------- #
# 4. isolator
# --------------------------------------------------------------------------- #
K1, K2, FY_ISO = 2000.0, 200.0, 20.0        # per isolator (kN/m, kN)
M_BLK = 50.0                                 # tonne
B_BLK, H_ISO = 4.0, 0.3


def _isolated_block():
    """Rigid-ish block (very stiff beam ring) on 4 isolators; the ground
    ends auto-fix (z_min base fixity) and the block carries the mass."""
    mdl = BuildingModel(name="iso")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 2
    mdl.add_material(Material("STIFF", E=2e11, nu=0.3))
    mdl.add_section(FrameSection.rectangular("BM", "STIFF", 0.5, 0.5))
    mdl.set_stories([H_ISO])
    pts = [(0, 0), (B_BLK, 0), (B_BLK, B_BLK), (0, B_BLK)]
    for i in range(4):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        mdl.add_member("beam", "BM", (x1, y1, H_ISO), (x2, y2, H_ISO),
                       story="Story1", uid=f"B{i + 1}")
    for x, y in pts:
        mdl.add_link((x, y, 0.0), (x, y, H_ISO), link_type="isolator",
                     params={"k1": K1, "k2": K2, "Fy": FY_ISO})
        mdl.nodal_masses.append(NodalMass((x, y, H_ISO),
                                          mx=M_BLK / 4, my=M_BLK / 4))
    return mdl, pts


def test_isolator_elastic_period_and_bilinear_static():
    mdl, pts = _isolated_block()
    F_el = 0.5 * 4.0 * FY_ISO                # half the yield level
    F_pl = 2.0 * 4.0 * FY_ISO                # twice the yield level
    for nm, F in (("EL", F_el), ("PL", F_pl)):
        pat = mdl.pattern(nm, "other")
        for x, y in pts:
            pat.nodal_loads.append(NodalLoad((x, y, H_ISO), fx=F / 4.0))
        mdl.add_case(nm, {nm: 1.0})

    eng = OpenSeesEngine(mdl)
    # (a) elastic-range lateral period T = 2 pi sqrt(m / (4 k1))
    T_hand = 2.0 * math.pi * math.sqrt(M_BLK / (4.0 * K1))
    assert eng.run_modal().periods[0] == pytest.approx(T_hand, rel=1e-3)

    coords = eng._asm.node_coords
    corner = _node_at(coords, lambda c: abs(c[0]) < 1e-9
                      and abs(c[1]) < 1e-9 and abs(c[2] - H_ISO) < 1e-9)
    # (b) elastic static: u = F / (4 k1)  (1e-4: the finite-height
    #     twoNodeLink shear-moment transfer flexes the stiff beam ring by
    #     ~1e-6 relative — the SDOF idealisation is otherwise exact)
    u_el = eng.run_static("EL").node_disp[corner][0]
    assert u_el == pytest.approx(F_el / (4.0 * K1), rel=1e-4)
    # (c) past yield: u = Fy/k1 + (F - 4 Fy) / (4 k2)  (post-yield slope
    #     4*k2; spec tolerance 1%, actual agreement ~1e-6)
    u_pl = eng.run_static("PL").node_disp[corner][0]
    u_hand = FY_ISO / K1 + (F_pl - 4.0 * FY_ISO) / (4.0 * K2)
    assert u_pl == pytest.approx(u_hand, rel=0.01)
    assert u_pl == pytest.approx(u_hand, rel=1e-4)   # secant near-exact


def _numpy_bilinear_newmark(accel, dt, m, k_el, k_pl, Fy):
    """Independent SDOF bilinear-kinematic Newmark (gamma=1/2, beta=1/4),
    zero damping — the Wave-7 elastoplastic benchmark approach."""
    n = len(accel)
    ag = list(accel) + [0.0]
    H = k_el * k_pl / (k_el - k_pl)
    u = v = a = 0.0
    up = alpha = 0.0
    g_, b_ = 0.5, 0.25
    hist = np.zeros(n)
    for kstep in range(n):
        p1 = -m * ag[kstep + 1]
        u1 = u
        for _ in range(100):
            a1_ = (u1 - u - dt * v - dt * dt * (0.5 - b_) * a) / (b_ * dt * dt)
            f_tr = k_el * (u1 - up)
            xi = f_tr - alpha
            if abs(xi) > Fy:
                dg = (abs(xi) - Fy) / (k_el + H)
                f1 = f_tr - k_el * dg * math.copysign(1.0, xi)
                kt = k_el * H / (k_el + H)
            else:
                f1, kt = f_tr, k_el
            R = m * a1_ + f1 - p1
            if abs(R) < 1e-12:
                break
            u1 -= R / (m / (b_ * dt * dt) + kt)
        a_new = (u1 - u - dt * v - dt * dt * (0.5 - b_) * a) / (b_ * dt * dt)
        v = v + dt * ((1.0 - g_) * a + g_ * a_new)
        a = a_new
        f_tr = k_el * (u1 - up)
        xi = f_tr - alpha
        if abs(xi) > Fy:
            dg = (abs(xi) - Fy) / (k_el + H)
            up += dg * math.copysign(1.0, xi)
            alpha += H * dg * math.copysign(1.0, xi)
        u = u1
        hist[kstep] = u
    return hist


def test_isolator_th_matches_numpy_bilinear():
    """Half-sine pulse well past 4*Fy, then free decay: engine peak and
    residual vs the independent numpy bilinear integrator within 2%."""
    dt, td, A = 0.005, 0.5, 3.0
    n_p = int(td / dt)
    accel = ([A * math.sin(math.pi * k * dt / td) for k in range(n_p + 1)]
             + [0.0] * 800)
    mdl, _ = _isolated_block()
    mdl.add_th_case("TH", "X", accel, dt, damping=1e-9)   # hysteretic only
    res = OpenSeesEngine(mdl).run_time_history("TH")
    u_e = np.array(res.story_ux["Story1"])
    u_n = _numpy_bilinear_newmark(accel, dt, M_BLK,
                                  4.0 * K1, 4.0 * K2, 4.0 * FY_ISO)
    pk_e, pk_n = np.max(np.abs(u_e)), np.max(np.abs(u_n))
    u_y = FY_ISO / K1
    assert pk_n > 5.0 * u_y                   # the benchmark really yields
    assert abs(pk_e - pk_n) <= 0.02 * pk_n
    assert abs(u_e[-1] - u_n[-1]) <= 0.02 * pk_n
    assert np.all(np.isfinite(u_e))


# --------------------------------------------------------------------------- #
# 5. wall piers
# --------------------------------------------------------------------------- #
def _wall_model(stories, W=3.0, mesh=0.25, pier="P1", V0=50.0, P0=100.0,
                behavior="shell"):
    """Cantilever shear wall over the given story heights, fixed base
    (auto z_min fixity over the bottom edge), lateral V0 + vertical P0
    applied as tributary-consistent nodal loads on the top edge."""
    H = sum(stories)
    mdl = BuildingModel(name="pier")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.shell_sections["SH"] = ShellSection("SH", "CONC", 0.2)
    mdl.set_stories(list(stories))
    mdl.shells.append(ShellRegion(
        "W1", "wall", behavior, "SH",
        [(0, 0, 0), (W, 0, 0), (W, 0, H), (0, 0, H)],
        mesh_size=mesh, story=mdl.stories[-1].name, pier=pier))
    top = sorted(p for p in mesh_model(mdl).points if abs(p[2] - H) < 1e-9)
    xs = [p[0] for p in top]
    trib = [(xs[min(i + 1, len(xs) - 1)] - xs[max(i - 1, 0)]) / 2.0
            for i in range(len(xs))]
    assert sum(trib) == pytest.approx(W, rel=1e-9)
    pv = mdl.pattern("V", "quake")
    pp = mdl.pattern("P", "dead")
    for p, tw in zip(top, trib):
        pv.nodal_loads.append(NodalLoad(p, fx=V0 * tw / W))
        pp.nodal_loads.append(NodalLoad(p, fz=-P0 * tw / W))
    mdl.add_case("V", {"V": 1.0})
    mdl.add_case("P", {"P": 1.0})
    return mdl


def test_pier_cantilever_wall_base_forces():
    """1-story wall, base pier: V within 2% of V0 (actual: exact nodal
    free body), P within 1% of P0, M within 4% of V0*h — cf. the Wave-5
    wall Nxy section-cut equilibrium precedent, now exact because the cut
    sums element NODAL forces instead of gauss-averaged resultants."""
    V0, P0, H = 50.0, 100.0, 3.0
    mdl = _wall_model([H], V0=V0, P0=P0)
    res = OpenSeesEngine(mdl).run()
    piers = res.piers
    pv = piers["V"]["P1"]["Story1"]
    pp = piers["P"]["P1"]["Story1"]
    assert pv["V"] == pytest.approx(V0, rel=0.02)
    assert pv["V"] == pytest.approx(V0, rel=1e-9)        # exact free body
    assert pp["P"] == pytest.approx(P0, rel=0.01)
    assert pp["P"] == pytest.approx(P0, rel=1e-9)
    assert pv["M"] == pytest.approx(V0 * H, rel=0.04)
    assert pv["M"] == pytest.approx(V0 * H, rel=1e-9)
    # symmetric axial load: no in-plane moment; lateral load: no axial
    assert abs(pp["M"]) < 1e-6 * V0 * H
    assert abs(pv["P"]) < 1e-6 * P0
    # serialised shape
    d = res.to_dict()
    assert d["piers"]["V"]["P1"]["Story1"]["V"] == pytest.approx(pv["V"])


def test_pier_two_story_wall_shear_constant_moment_grows():
    """Single lateral load path: story-2 and story-1 pier V both equal V0;
    M grows linearly with depth: M(story2) = V0*h2 at its bottom, and
    M(story1) = V0*(h1+h2) at the base (spec 5%; actual exact)."""
    h1, h2, V0 = 3.0, 3.0, 50.0
    mdl = _wall_model([h1, h2], V0=V0, P0=80.0)
    piers = OpenSeesEngine(mdl).run().piers
    s1 = piers["V"]["P1"]["Story1"]
    s2 = piers["V"]["P1"]["Story2"]
    assert s2["V"] == pytest.approx(V0, rel=1e-9)
    assert s1["V"] == pytest.approx(V0, rel=1e-9)
    assert s2["M"] == pytest.approx(V0 * h2, rel=1e-9)
    assert s1["M"] == pytest.approx(V0 * (h1 + h2), rel=0.05)
    assert s1["M"] == pytest.approx(V0 * (h1 + h2), rel=1e-9)
    # gravity case: full P0 arrives at every story cut
    pp = piers["P"]["P1"]
    assert pp["Story1"]["P"] == pytest.approx(80.0, rel=1e-9)
    assert pp["Story2"]["P"] == pytest.approx(80.0, rel=1e-9)


def test_pier_combo_superposition_exact():
    mdl = _wall_model([3.0])
    mdl.add_combo("U", {"V": 1.2, "P": 1.6})
    piers = OpenSeesEngine(mdl).run().piers
    for story in ("Story1",):
        for key in ("P", "V", "M"):
            want = (1.2 * piers["V"]["P1"][story][key]
                    + 1.6 * piers["P"]["P1"][story][key])
            got = piers["U"]["P1"][story][key]
            assert got == pytest.approx(want, abs=1e-9 + 1e-9 * abs(want))


def test_auto_pier_walls_labels_with_uid():
    mdl = _wall_model([3.0], pier="")
    res0 = OpenSeesEngine(mdl).run()
    assert res0.piers == {}                    # unlabeled, auto off
    mdl2 = _wall_model([3.0], pier="")
    mdl2.auto_pier_walls = True
    res1 = OpenSeesEngine(mdl2).run()
    assert "W1" in res1.piers["V"]             # auto label = wall uid
    assert res1.piers["V"]["W1"]["Story1"]["V"] == pytest.approx(50.0,
                                                                 rel=1e-9)
    # round-trip of the flag + pier label
    mdl3 = _wall_model([3.0], pier="CORE")
    mdl3.auto_pier_walls = True
    back = BuildingModel.from_dict(mdl3.to_dict())
    assert back.auto_pier_walls is True
    assert back.shells[0].pier == "CORE"


def test_membrane_wall_pier_skipped_with_warning():
    """A labeled membrane wall has no FE: warned and absent from piers.
    (Membrane walls are rejected by validate(); appended directly to
    exercise the engine guard.)"""
    mdl = BuildingModel(name="memwall")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.5))
    mdl.set_stories([3.0])
    for x in (0.0, 3.0):
        mdl.add_member("column", "COL", (x, 0, 0), (x, 0, 3.0),
                       story="Story1", uid=f"C{int(x) + 1}")
        mdl.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.add_member("beam", "BM", (0, 0, 3.0), (3.0, 0, 3.0),
                   story="Story1", uid="TB")
    mdl.shells.append(ShellRegion(
        "WM", "wall", "membrane", "", [(0, 0, 0), (3, 0, 0), (3, 0, 3),
                                       (0, 0, 3)], story="Story1",
        pier="PM"))
    mdl.pattern("F", "other").nodal_loads.append(
        NodalLoad((0, 0, 3.0), fx=10.0))
    mdl.add_case("F", {"F": 1.0})
    with pytest.warns(UserWarning, match="membrane wall skipped"):
        res = OpenSeesEngine(mdl).run()
    assert res.piers == {}


# --------------------------------------------------------------------------- #
# 6. API round-trip
# --------------------------------------------------------------------------- #
@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_links_and_piers_roundtrip(api_client):
    mdl = _wall_model([3.0], pier="CORE")
    mdl.auto_pier_walls = True
    mdl.add_link((0, 0, 3.0), (1.5, 0, 3.0), link_type="damper",
                 params={"cd": 30.0}, uid="D1")
    mdl.add_link((0, 0, 0.0), (0, 0, 0.3), link_type="isolator",
                 params={"k1": 2000.0, "k2": 200.0, "Fy": 20.0}, uid="I1")
    resp = api_client.post("/api/model", json=mdl.to_dict())
    assert resp.status_code == 200
    d = resp.get_json()
    links = {ld["uid"]: ld for ld in d["links"]}
    assert links["D1"]["link_type"] == "damper"
    assert links["D1"]["params"]["cd"] == 30.0
    assert links["I1"]["params"]["Fy"] == 20.0
    assert d["auto_pier_walls"] is True
    assert d["shells"][0]["pier"] == "CORE"


def test_api_bad_link_type_and_params_400(api_client):
    mdl = _cantilever_column("api")
    mdl.add_link((0, 0, 3.0), (1.0, 0, 3.0), link_type="gap",
                 params={"k": 100.0, "gap": 0.01}, uid="G1")
    good = mdl.to_dict()

    bad1 = {**good, "links": [dict(good["links"][0], link_type="spring")]}
    r1 = api_client.post("/api/model", json=bad1)
    assert r1.status_code == 400 and "link_type" in r1.get_json()["error"]

    bad2 = {**good, "links": [dict(good["links"][0], params={"k": 100.0})]}
    r2 = api_client.post("/api/model", json=bad2)
    assert r2.status_code == 400 and "gap" in r2.get_json()["error"]
