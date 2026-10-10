"""Verification family 4 -- NONLINEAR (pushover vs plastic collapse,
tension-only braces, gap / hook links, base isolator).

Modelled on the CSI *SAP2000 Software Verification* nonlinear examples
(nonlinear static pushover with plastic hinges, tension/compression-only
frames, gap and hook link elements, base-isolator link elements).
References are classical plastic-analysis / statics / link-law closed
forms; tolerances reflect the documented idealisations (stiff-hinge
spring factor n = 10, tension-only residual stiffness 1e-6 E).
"""

import math

import numpy as np
import pytest

from skyframe.core.model import NodalMass

from _vhelp import (FIXED, PINNED, check, disp, engine, load, new_model,
                    planar_xz, run_case, section, support, tag_at)

FAM = "Nonlinear"


# --------------------------------------------------------------------------- #
# N1  pushover of an elastic-perfectly-plastic portal vs plastic collapse
# --------------------------------------------------------------------------- #
def _epp_portal(V, hardening=0.0):
    h, L, E, I, Mp = 4.0, 6.0, 2.0e8, 2.0e-4, 200.0
    m = new_model("N1", E, heights=[h])
    section(m, "S", 1e-2, I)
    planar_xz(m)
    m.add_member("column", "S", (0, 0, 0), (0, 0, h), story="Story1",
                 uid="C1")
    m.add_member("column", "S", (L, 0, 0), (L, 0, h), story="Story1",
                 uid="C2")
    m.add_member("beam", "S", (0, 0, h), (L / 2, 0, h), story="Story1",
                 uid="B1")
    m.add_member("beam", "S", (L / 2, 0, h), (L, 0, h), story="Story1",
                 uid="B2")
    support(m, (0, 0, 0))
    support(m, (L, 0, 0))
    grav = {}
    if V:
        load(m, "V", (L / 2, 0, h), fz=-V)
        grav = {"V": 1.0}
    m.add_pushover_case("PO", "X", gravity=grav, target_drift=0.03,
                        steps=150, hinges="all_ends", default_My=Mp,
                        hardening=hardening)
    return engine(m).run_pushover("PO"), h, L, Mp


def test_n01_pushover_epp_portal_sway_mechanism():
    """Fixed-base portal, all member ends hinged with the same Mp, elastic-
    perfectly-plastic (hardening 0), lateral push only (SAP2000 Verification
    nonlinear-static "pushover of a portal frame" class).  Plastic collapse
    by the kinematic theorem (Neal, *The Plastic Methods of Structural
    Analysis*, Ch. 2; Horne): sway mechanism H_c = 4 Mp / h.

    REGRESSION (fixed in this suite): with hardening = 0 the hinge Steel01
    tangent was exactly zero, the joint where a yielded column hinge meets
    a yielded beam hinge had no rotational stiffness, K went singular and
    the push stopped at step 44 with V = 198.39 kN (the curve never
    reached the plateau)."""
    po, h, L, Mp = _epp_portal(0.0)
    assert not po.warnings, po.warnings
    assert len(po.base_shear) == 150
    check("EPP portal pushover: sway mechanism",
          "Neal, Plastic Methods Ch. 2 (kinematic theorem); SAP2000 "
          "pushover class", "collapse base shear 4Mp/h", 4 * Mp / h,
          max(po.base_shear), 1e-6, family=FAM)


def test_n02_pushover_epp_portal_combined_mechanism():
    """Same portal with a held mid-span gravity load V = 6 Mp/L (applied
    first, then the lateral push).  Combined mechanism (hinges at both
    column bases, under the load and at the leeward beam end):
    H h + V L/2 = 6 Mp -> H_c = 3 Mp / h (governs over the sway 4 Mp/h; the
    beam mechanism needs V = 8 Mp/L).

    REGRESSION: before the fix the push stopped at step 16 with
    V = 110.13 kN, 27% below the collapse load (the two yielded hinges at
    the mid-span node left its rotation without stiffness)."""
    po, h, L, Mp = _epp_portal(6 * 200.0 / 6.0)
    assert not po.warnings, po.warnings
    check("EPP portal pushover: combined mechanism",
          "Neal, Plastic Methods Ch. 2 (combined mechanism)",
          "collapse base shear 3Mp/h", 3 * Mp / h, max(po.base_shear), 1e-6,
          family=FAM)


def test_n03_pushover_hardening_slope():
    """Bilinear hinge with hardening h: the stiff-hinge series idealisation
    makes the member-end post-yield/elastic stiffness ratio exactly h
    (CONTRACT v0.5).  On a fixed-base cantilever column with a base hinge
    the hand series formulas are exact:
    K_el = 1 / (L^3/3EI + L^2/k_theta), K_pl = 1 / (L^3/3EI +
    L^2/(b k_theta)), k_theta = 10 * 6EI/L, b = h/(n+1-hn)."""
    L, E, I, Mp, hrd = 3.0, 2.0e8, 1.0e-4, 150.0, 0.05
    m = new_model("N3", E, heights=[L])
    section(m, "S", 1e-2, I)
    planar_xz(m)
    m.add_member("column", "S", (0, 0, 0), (0, 0, L), story="Story1",
                 uid="C1")
    support(m, (0, 0, 0))
    m.add_pushover_case("PO", "X", target_drift=0.05, steps=100,
                        hinges="column_base", default_My=Mp,
                        hardening=hrd)
    po = engine(m).run_pushover("PO")
    u = np.array(po.roof_disp)
    V = np.array(po.base_shear)
    n = 10.0
    kth = n * 6 * E * I / L
    b = hrd / (n + 1 - hrd * n)
    f = L ** 3 / (3 * E * I)
    K_el = 1 / (f + L * L / kth)
    K_pl = 1 / (f + L * L / (b * kth))
    S = "Stiff-hinge series idealisation (CONTRACT v0.5), hand series"
    check("Bilinear-hinge cantilever pushover", S, "elastic slope",
          K_el, V[0] / u[0], 1e-9, family=FAM)
    check("Bilinear-hinge cantilever pushover", S, "post-yield slope",
          K_pl, (V[-1] - V[-2]) / (u[-1] - u[-2]), 1e-6, family=FAM)
    # yield point = intersection of the elastic and post-yield branches
    u_y = (V[-1] - K_pl * u[-1]) / (K_el - K_pl)
    check("Bilinear-hinge cantilever pushover", S, "yield base shear Mp/L",
          Mp / L, K_el * u_y, 1e-6, family=FAM)


def _hinged_sdof_newmark(acc, dt, m, kc, kh, Fy, a0, a1):
    """Independent integrator for the base-hinged cantilever with a top
    mass, written as the exact 2-DOF lateral analogue: top displacement u
    (mass m) and the hinge's lateral coordinate x = theta_base * L
    (massless).  The elastic column is a Kelvin element kc = 3EI/L^3 with
    stiffness-proportional damping a1*kc (Rayleigh on the elastic
    element), the hinge an UNDAMPED elastic-perfectly-plastic spring
    kh = k_theta/L^2, Fy = My/L (OpenSees zeroLength springs carry no
    Rayleigh term -- SkyFrame's documented convention, the Zareian & Medina
    (2010) practice), plus mass-proportional a0*m.  Newmark constant
    average acceleration on both DOFs with Newton iterations (Chopra
    Sec. 5.7).  Sample k of ``acc`` acts at t = k dt; entry k of the result
    is u at t = (k+1) dt."""
    g_, b_ = 0.5, 0.25
    M = np.diag([m, 0.0])
    C = np.array([[a0 * m + a1 * kc, -a1 * kc], [-a1 * kc, a1 * kc]])
    d = np.zeros(2)
    v = np.zeros(2)
    a = np.zeros(2)
    xp = 0.0                                   # hinge plastic offset

    def hinge(x):
        f = kh * (x - xp)
        if abs(f) > Fy:
            return math.copysign(Fy, f), 0.0
        return f, kh
    out = []
    for s in range(len(acc) - 1):
        p = np.array([-m * acc[s + 1], 0.0])
        d1 = d.copy()
        for _ in range(100):
            a1_ = (d1 - d - dt * v - dt * dt * (0.5 - b_) * a) / (b_ * dt * dt)
            v1 = v + dt * ((1 - g_) * a + g_ * a1_)
            fh, kht = hinge(d1[1])
            fc = kc * (d1[0] - d1[1])
            fint = np.array([fc, -fc + fh])
            R = M @ a1_ + C @ v1 + fint - p
            if np.max(np.abs(R)) < 1e-10:
                break
            Kt = np.array([[kc, -kc], [-kc, kc + kht]])
            d1 = d1 - np.linalg.solve(M / (b_ * dt * dt)
                                      + C * g_ / (b_ * dt) + Kt, R)
        a_new = (d1 - d - dt * v - dt * dt * (0.5 - b_) * a) / (b_ * dt * dt)
        v = v + dt * ((1 - g_) * a + g_ * a_new)
        a = a_new
        f = kh * (d1[1] - xp)
        if abs(f) > Fy:
            xp = d1[1] - math.copysign(Fy, f) / kh
        d = d1
        out.append(d[0])
    return np.array(out)


@pytest.mark.parametrize("regime", ["epp", "elastic"])
def test_n03b_nonlinear_th_epp_sdof(regime):
    """Nonlinear direct-integration TH of an elastic-perfectly-plastic
    base-hinged cantilever (hinge My, hardening 0, mass at the top) under a
    half-sine ground pulse (SAP2000 Verification nonlinear-TH class), vs
    an independent Newmark integrator of the exact 2-DOF lateral analogue
    (Chopra Sec. 5.7).  Rayleigh a0/a1 are fitted to the INITIAL hinge-free
    mode w0 = sqrt(3EI/(m L^3)) (documented SkyFrame convention).

    Documented (not changed) finding: the zeroLength hinge springs carry no
    Rayleigh term, so the stiffness-proportional damping of a hinged model
    acts on the elastic members only and the delivered damping is slightly
    below the case's zeta (here the classical-Rayleigh SDOF would give a
    0.78% smaller end-of-record displacement).  Damping the springs with
    the committed tangent was tried: it reproduces classical Rayleigh
    exactly but breaks the TH energy tracker's exact balance at yield
    events, so the documented practice was kept."""
    L, E, I, mass, zeta = 3.0, 2.0e8, 1.0e-4, 20.0, 0.02
    My = 60.0 if regime == "epp" else 1.0e9       # elastic: never yields
    m = new_model("N3b", E, heights=[L])
    section(m, "S", 1e-2, I)
    planar_xz(m)
    m.add_member("column", "S", (0, 0, 0), (0, 0, L), story="Story1",
                 uid="C1")
    support(m, (0, 0, 0))
    m.nodal_masses.append(NodalMass((0, 0, L), mx=mass))
    m.num_modes = 1
    dt = 0.002
    t = np.arange(0, 2.0, dt)
    acc = np.where(t < 0.6, 6.0 * np.sin(math.pi * t / 0.6), 0.0)
    m.add_th_case("TH", "X", list(acc), dt, damping=zeta, nonlinear=True,
                  hinges="column_base", default_My=My, hardening=0.0)
    th = engine(m).run_time_history("TH")
    u = np.array(th.story_ux["Story1"])
    kc = 3 * E * I / L ** 3
    kh = 10 * 6 * E * I / L / L ** 2
    w0 = math.sqrt(kc / mass)
    ref = _hinged_sdof_newmark(list(acc) + [0.0], dt, mass, kc, kh, My / L,
                               zeta * w0, zeta / w0)
    S = "Chopra Sec. 5.7 Newmark (independent 2-DOF numpy integrator)"
    Pn = ("EPP hinged SDOF nonlinear TH" if regime == "epp"
          else "Hinged SDOF nonlinear TH below yield")
    # same integrator and damping law: only the Newton tolerance and the
    # Steel01 yield-corner treatment differ
    tol = 1e-4 if regime == "epp" else 1e-8
    check(Pn, S, "peak displacement", np.max(np.abs(ref)),
          np.max(np.abs(u)), tol, family=FAM)
    check(Pn, S, "displacement at end of record", ref[-1], u[-1], tol,
          family=FAM)


# --------------------------------------------------------------------------- #
# N4  tension-only X-braces
# --------------------------------------------------------------------------- #
def test_n04_tension_only_x_brace():
    """Pin-jointed X-braced bay with TENSION-ONLY diagonals (SAP2000
    Verification "frame - tension/compression limits" class): lateral H at
    the top-left joint.  The compression diagonal goes slack, the structure
    becomes the statically determinate truss: tension brace
    T = H L_d / L, leeward column compression H h / L, and the top drift by
    the unit-load method delta = H [L/EA_b + (L_d/L)^2 L_d/EA_d +
    (h/L)^2 h/EA_c]."""
    L, h, E = 6.0, 4.0, 2.0e8
    Ab, Ad, Ac, I = 4e-3, 1.5e-3, 6e-3, 1e-5
    H = 100.0
    m = new_model("N4", E, heights=[h])
    section(m, "BM", Ab, I)
    section(m, "BR", Ad, I)
    section(m, "CO", Ac, I)
    planar_xz(m)
    m.add_member("column", "CO", (0, 0, 0), (0, 0, h), story="Story1",
                 uid="C1")
    m.add_member("column", "CO", (L, 0, 0), (L, 0, h), story="Story1",
                 uid="C2")
    m.add_member("beam", "BM", (0, 0, h), (L, 0, h), story="Story1",
                 uid="B1", releases="Mi,Mj")
    m.add_member("brace", "BR", (0, 0, 0), (L, 0, h), story="Story1",
                 uid="D1", axial_limit="tension")
    m.add_member("brace", "BR", (L, 0, 0), (0, 0, h), story="Story1",
                 uid="D2", axial_limit="tension")
    support(m, (0, 0, 0), PINNED)
    support(m, (L, 0, 0), PINNED)
    load(m, "H", (0, 0, h), fx=H)
    m.add_case("H", {"H": 1.0})
    eng, r = run_case(m, "H")
    Ld = math.hypot(L, h)
    S = "Statics + unit-load method; SAP2000 tension-only frame class"
    Pn = "Tension-only X-brace"
    # member_forces[6] = +N (tension positive) for axial-only members
    check(Pn, S, "tension brace force H Ld/L", H * Ld / L,
          r.member_forces["D1"][6], 1e-5, family=FAM,
          note="slack brace keeps 1e-6 E (CONTRACT v0.12)")
    check(Pn, S, "slack brace force (< 1e-4 H)", 0.0,
          r.member_forces["D2"][6], 0.0, family=FAM, abs_floor=1e-4 * H)
    delta = H * (L / (E * Ab) + (Ld / L) ** 2 * Ld / (E * Ad)
                 + (h / L) ** 2 * h / (E * Ac))
    check(Pn, S, "top drift (unit-load method)", delta,
          disp(eng, r, (0, 0, h), 0), 1e-4, family=FAM,
          note="residual compression modulus 1e-6 E")


# --------------------------------------------------------------------------- #
# N5  gap and hook links
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["gap", "hook"])
def test_n05_gap_and_hook_links(kind):
    """Cantilever tip restrained by a GAP (compression-only, opening g) or
    HOOK (tension-only, slack s) link to ground (SAP2000 Verification
    "link - gap / hook" examples).  Free tip deflection d0 = P L^3/3EI;
    once d0 exceeds the opening the link force is
    F = (d0 - g) / (L^3/3EI + 1/k) and the tip moves g + F/k.  A load that
    does not close the opening leaves F = 0."""
    L, E, I, k, g = 4.0, 2.0e8, 5e-5, 5.0e3, 0.004
    f = L ** 3 / (3 * E * I)
    S = "Two-spring compatibility; SAP2000 gap/hook link examples"
    for P in (0.5 * g / f, 3.0 * g / f):
        m = new_model("N5", E, heights=[1.0])
        section(m, "S", 1e-2, I)
        planar_xz(m)
        m.add_member("beam", "S", (0, 0, 0), (L, 0, 0), story="Story1",
                     uid="B1")
        support(m, (0, 0, 0))
        # gap: link below the tip (tip moving down CLOSES it);
        # hook: link above the tip (tip moving down OPENS it)
        zg = -1.0 if kind == "gap" else 1.0
        prm = {"k": k, "gap": g} if kind == "gap" else {"k": k, "slack": g}
        m.add_link((L, 0, zg), (L, 0, 0), link_type=kind, params=prm,
                   uid="LK")
        load(m, "P", (L, 0, 0), fz=-P)
        m.add_case("P", {"P": 1.0})
        eng, r = run_case(m, "P")
        d0 = P * f
        F = max(0.0, (d0 - g) / (f + 1 / k))
        anchor = r.reactions[tag_at(eng, (L, 0, zg))][2]
        if F == 0.0:
            check(f"{kind} link, open", S, "link force (open)", 0.0,
                  anchor, 0.0, family=FAM, abs_floor=1e-6 * P)
            check(f"{kind} link, open", S, "tip deflection P L^3/3EI", -d0,
                  disp(eng, r, (L, 0, 0), 2), 1e-6, family=FAM)
        else:
            check(f"{kind} link, engaged", S, "link force", F, abs(anchor),
                  1e-6, family=FAM)
            check(f"{kind} link, engaged", S, "tip deflection g + F/k",
                  -(g + F / k), disp(eng, r, (L, 0, 0), 2), 1e-6,
                  family=FAM)


# --------------------------------------------------------------------------- #
# N6  bilinear base isolator
# --------------------------------------------------------------------------- #
def test_n06_bilinear_isolator_static_and_period():
    """Rigid block on four bilinear isolators (SAP2000 Verification
    "isolator link" examples; Naeim & Kelly, *Design of Seismic Isolated
    Structures*, Ch. 2 bilinear model): static lateral force per bearing
    F > Fy gives u = Fy/k1 + (F - Fy)/k2; elastic period
    T = 2 pi sqrt(m / (4 k1))."""
    h, B = 0.3, 4.0
    k1, k2, Fy, mass = 2000.0, 200.0, 20.0, 80.0
    S = "Naeim & Kelly Ch. 2 bilinear isolator; SAP2000 isolator class"
    for F in (10.0, 30.0):
        m = new_model("N6", 2e11, heights=[h])
        from skyframe.core.model import FrameSection
        m.add_section(FrameSection.rectangular("BM", "MAT", 0.5, 0.5))
        pts = [(0, 0), (B, 0), (B, B), (0, B)]
        for i in range(4):
            (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % 4]
            m.add_member("beam", "BM", (x1, y1, h), (x2, y2, h),
                         story="Story1", uid=f"B{i + 1}")
        for x, y in pts:
            m.add_link((x, y, 0.0), (x, y, h), link_type="isolator",
                       params={"k1": k1, "k2": k2, "Fy": Fy})
            m.nodal_masses.append(NodalMass((x, y, h), mx=mass / 4,
                                            my=mass / 4))
            load(m, "H", (x, y, h), fx=F)
        m.add_case("H", {"H": 1.0})
        m.num_modes = 3
        eng, r = run_case(m, "H")
        u = disp(eng, r, (0, 0, h), 0)
        ref = F / k1 if F <= Fy else Fy / k1 + (F - Fy) / k2
        check("Bilinear isolator, static push", S,
              f"lateral displacement at F = {F:g} kN/bearing", ref, u, 1e-4,
              family=FAM, note="finite kv rocking -> 1e-4")
    T = engine(m).run_modal(3).periods[0]
    check("Bilinear isolator, elastic period", S, "T = 2 pi sqrt(m/4k1)",
          2 * math.pi * math.sqrt(mass / (4 * k1)), T, 1e-4, family=FAM)
