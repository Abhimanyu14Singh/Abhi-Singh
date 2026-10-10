"""Verification family 3 -- DYNAMICS (modal, response spectrum, linear time
history, Rayleigh damping, rigid-diaphragm torsion).

Modelled on the CSI *SAP2000 / ETABS Software Verification* dynamic
examples (Bathe-Wilson eigenvalue problem, shear-building modal and
response-spectrum examples, linear direct-integration time history,
rigid-diaphragm torsional modes).  References are the published values or
the closed forms of Chopra, *Dynamics of Structures* (5th ed.):

* modal quantities of a condensed shear building are EXACT (no
  discretisation): 1e-8 relative;
* Newmark constant-average acceleration has the period-elongation error
  (pi^2/12)(dt/T)^2 (Chopra Sec. 5.5) -- the TH tolerances are derived
  from it for the chosen dt.
"""

import math

import numpy as np
import pytest

from skyframe.core.model import G_ACCEL, NodalMass

from _vhelp import (FIXED, chain, check, engine, new_model, planar_xz,
                    section, support, tag_at)

FAM = "Dynamics"


# --------------------------------------------------------------------------- #
# D1  Bathe & Wilson eigenvalue problem
# --------------------------------------------------------------------------- #
def test_d01_bathe_wilson_eigenvalues():
    """SAP2000 Verification Example 1-001 / ETABS Example 1 "Bathe and
    Wilson eigenvalue problem": 10-bay, 9-story plane frame, bays 20 ft,
    stories 10 ft, E = 432 000 k/ft^2, A = 3 ft^2, I = 1 ft^4, mass 3
    k-s^2/ft^2 per unit length lumped at the joints (both translations);
    bending + axial deformation only.  Published eigenvalues (Bathe &
    Wilson 1972, ASCE J. Eng. Mech. Div. 98(EM6), plane-frame table):
    lambda = omega^2 = 0.589541, 5.52695, 16.5878."""
    m = new_model("D1", 432000.0, heights=[10.0] * 9)
    section(m, "S", 3.0, 1.0)
    planar_xz(m)
    mass = {}

    def addm(p, v):
        mass[p] = mass.get(p, 0.0) + v
    for j in range(9):
        for i in range(11):
            p, q = (20.0 * i, 0, 10.0 * j), (20.0 * i, 0, 10.0 * (j + 1))
            m.add_member("column", "S", p, q, story=f"Story{j + 1}",
                         uid=f"C{i}_{j}")
            addm(p, 15.0)
            addm(q, 15.0)
    for j in range(1, 10):
        for i in range(10):
            p, q = (20.0 * i, 0, 10.0 * j), (20.0 * (i + 1), 0, 10.0 * j)
            m.add_member("beam", "S", p, q, story=f"Story{j}",
                         uid=f"B{i}_{j}")
            addm(p, 30.0)
            addm(q, 30.0)
    for i in range(11):
        support(m, (20.0 * i, 0, 0))
    for p, v in mass.items():
        if p[2] > 0:
            m.nodal_masses.append(NodalMass(p, mx=v, mz=v))
    m.num_modes = 3
    md = engine(m).run_modal(3)
    lam = [(2 * math.pi / T) ** 2 for T in md.periods]
    S = "Bathe & Wilson (1972); SAP2000 Ex. 1-001 / ETABS Ex. 1"
    for k, ref in enumerate((0.589541, 5.52695, 16.5878)):
        # published to 6 significant figures -> 1e-5 relative
        check("Bathe-Wilson 10-bay 9-story frame eigenvalues", S,
              f"lambda_{k + 1} = omega^2", ref, lam[k], 1e-5, family=FAM)


# --------------------------------------------------------------------------- #
# shear-building helpers
# --------------------------------------------------------------------------- #
def _shear_building(ks, ms, h=3.0, E=2.0e8):
    """Condensed shear building: one column line, story stiffness k_i
    exactly (rotations restrained at every floor -> k = 12EI/h^3), lumped
    floor masses m_i.  Returns (model, floor elevations)."""
    n = len(ks)
    m = new_model("SB", E, heights=[h] * n)
    planar_xz(m)
    m.num_modes = n
    z = 0.0
    zs = []
    for i, k in enumerate(ks):
        I = k * h ** 3 / (12.0 * E)
        section(m, f"S{i}", 10.0, I)
        m.add_member("column", f"S{i}", (0, 0, z), (0, 0, z + h),
                     story=f"Story{i + 1}", uid=f"C{i + 1}")
        z += h
        zs.append(z)
        support(m, (0, 0, z), (0, 0, 0, 0, 1, 0))   # rigid floor beam
        m.nodal_masses.append(NodalMass((0, 0, z), mx=ms[i]))
    support(m, (0, 0, 0))
    return m, zs


def _shear_KM(ks, ms):
    n = len(ks)
    K = np.zeros((n, n))
    for i, k in enumerate(ks):
        K[i, i] += k
        if i + 1 < n:
            K[i, i] += ks[i + 1]
            K[i, i + 1] = K[i + 1, i] = -ks[i + 1]
    return K, np.diag(ms)


# --------------------------------------------------------------------------- #
# D2  uniform shear building: closed-form periods
# --------------------------------------------------------------------------- #
def test_d02_uniform_shear_building_closed_form_periods():
    """Uniform n-story shear building (equal k, m): closed form
    omega_r = 2 sqrt(k/m) sin[(2r-1) pi / (2(2n+1))] (Humar, *Dynamics
    of Structures*, uniform shear-beam building; ETABS "shear building
    modal" verification class).  Exact for the condensed model -> 1e-8."""
    n, k, mm = 5, 4.0e4, 50.0
    m, _ = _shear_building([k] * n, [mm] * n)
    md = engine(m).run_modal(n)
    S = "Closed form (Humar Sec. 11.4); ETABS shear-building modal class"
    for r in range(1, n + 1):
        w = 2 * math.sqrt(k / mm) * math.sin((2 * r - 1) * math.pi
                                             / (2 * (2 * n + 1)))
        check("Uniform 5-story shear building periods", S,
              f"T_{r}", 2 * math.pi / w, md.periods[r - 1], 1e-8,
              family=FAM)


# --------------------------------------------------------------------------- #
# D3  response spectrum SRSS / CQC
# --------------------------------------------------------------------------- #
def _spectrum(T):
    """Design-type spectrum used below (Sa in g)."""
    return 1.0 if T <= 0.5 else 0.5 / T


def _hand_rsa(ks, ms, zs, zeta, method):
    """Chopra Sec. 13.2 hand RSA: modal static responses
    r_n = Gamma_n D_n (...) combined by SRSS or CQC (Der Kiureghian 1981
    correlation, equal damping)."""
    K, M = _shear_KM(ks, ms)
    w2, phi = np.linalg.eig(np.linalg.solve(M, K))
    o = np.argsort(w2)
    w = np.sqrt(w2[o].real)
    phi = phi[:, o].real
    one = np.ones(len(ks))
    roof, base, drift1 = [], [], []
    for i in range(len(ks)):
        f = phi[:, i]
        Mn = f @ M @ f
        G = (f @ M @ one) / Mn
        A = _spectrum(2 * math.pi / w[i]) * G_ACCEL
        D = A / w[i] ** 2
        u = G * f * D
        roof.append(u[-1])
        drift1.append(u[0])
        base.append(G ** 2 * Mn * A)       # Vb_n = M*_n A_n (signed by G^2)
    n = len(ks)
    if method == "SRSS":
        rho = np.eye(n)
    else:
        rho = np.empty((n, n))
        for i in range(n):
            for j in range(n):
                b = w[j] / w[i]
                rho[i, j] = (8 * zeta ** 2 * (1 + b) * b ** 1.5
                             / ((1 - b * b) ** 2
                                + 4 * zeta ** 2 * b * (1 + b) ** 2))
    comb = lambda r: math.sqrt(np.array(r) @ rho @ np.array(r))
    return comb(roof), comb(base), 2 * math.pi / w


@pytest.mark.parametrize("method", ["SRSS", "CQC"])
def test_d03_response_spectrum_three_story(method):
    """3-story shear building response spectrum (ETABS Verification
    "response spectrum" example class; hand reference Chopra Sec. 13.2:
    modal static responses, SRSS and CQC with the Der Kiureghian (1981)
    correlation coefficient, zeta = 5%).  Stiffness/mass chosen so modes 2
    and 3 are fairly close (CQC != SRSS) and the periods (0.87/0.35/0.25 s)
    use both the plateau and the descending branch of the spectrum."""
    ks = [3.0e4, 2.4e4, 1.8e4]
    ms = [120.0, 110.0, 90.0]
    m, zs = _shear_building(ks, ms)
    m.add_rs_case("RS", "X", [[0.0, 1.0], [0.5, 1.0]]
                  + [[T, 0.5 / T] for T in np.linspace(0.55, 4.0, 400)],
                  combo_method=method, damping=0.05)
    eng = engine(m)
    rs = eng.run_response_spectrum("RS")
    roof_ref, base_ref, T = _hand_rsa(ks, ms, zs, 0.05, method)
    roof = abs(rs.node_disp[tag_at(eng, (0, 0, zs[-1]))][0])
    S = "Chopra Sec. 13.2 hand RSA; ETABS response-spectrum class"
    # spectrum is tabulated at 400 points between 0.55 and 4 s and
    # linearly interpolated -> interpolation error of 0.5/T < 1e-5
    tol = 5e-5
    check(f"3-story shear building RSA ({method})", S, "roof displacement",
          roof_ref, roof, tol, family=FAM,
          note="T = " + ", ".join(f"{t:.3f}" for t in T) + " s")
    check(f"3-story shear building RSA ({method})", S, "base shear",
          base_ref, abs(rs.base["FX"]), tol, family=FAM)
    if method == "SRSS":
        K, M = _shear_KM(ks, ms)
        w2, phi = np.linalg.eig(np.linalg.solve(M, K))
        o = np.argsort(w2)
        w = np.sqrt(w2[o].real)
        phi = phi[:, o].real
        one = np.ones(3)
        # story shears / drift ratios: per-mode, then SRSS (ETABS-style
        # per-mode drifts, Chopra Sec. 13.2.3 modal static responses)
        V = np.zeros((3, 3))
        dr = np.zeros((3, 3))
        for i in range(3):
            f = phi[:, i]
            G = (f @ M @ one) / (f @ M @ f)
            A = _spectrum(2 * math.pi / w[i]) * G_ACCEL
            V[i] = np.cumsum((G * (M @ f) * A)[::-1])[::-1]
            u = G * f * A / w[i] ** 2
            dr[i] = np.diff(np.concatenate([[0.0], u])) / 3.0
        Vs = np.sqrt((V ** 2).sum(0))
        Ds = np.sqrt((dr ** 2).sum(0))
        for k in (1, 3):
            st = rs.story[f"Story{k}"]
            check("3-story shear building RSA (SRSS)", S,
                  f"story {k} shear", Vs[k - 1], st["shear_x"], tol,
                  family=FAM)
            check("3-story shear building RSA (SRSS)", S,
                  f"story {k} drift ratio", Ds[k - 1], st["drift_x"], tol,
                  family=FAM)
        # effective modal mass ratios M*_n / sum(m) (Chopra Sec. 13.2.5)
        part = eng.run_modal().participation
        for i in range(3):
            f = phi[:, i]
            ratio = (f @ M @ one) ** 2 / (f @ M @ f) / sum(ms)
            check("3-story shear building modal mass ratios",
                  "Chopra Sec. 13.2.5 effective modal mass",
                  f"UX mass ratio mode {i + 1}", ratio, part[i]["ux"], 1e-8,
                  family=FAM)


# --------------------------------------------------------------------------- #
# D4  SDOF linear time history vs Duhamel / closed form
# --------------------------------------------------------------------------- #
def _sdof_harmonic(t, w, zeta, A, W):
    """Exact response (Chopra Sec. 3.2) of u'' + 2 zeta w u' + w^2 u =
    -A sin(W t), at rest at t = 0 (equivalently the Duhamel integral)."""
    b = W / w
    st = -A / w ** 2
    den = (1 - b * b) ** 2 + (2 * zeta * b) ** 2
    C = st * (1 - b * b) / den
    D = st * (-2 * zeta * b) / den
    wd = w * math.sqrt(1 - zeta ** 2)
    A1 = -D
    B1 = (zeta * w * A1 - W * C) / wd
    return (np.exp(-zeta * w * t) * (A1 * np.cos(wd * t) + B1 * np.sin(wd * t))
            + C * np.sin(W * t) + D * np.cos(W * t))


def test_d04_sdof_time_history_vs_closed_form():
    """Linear direct-integration TH of an SDOF cantilever (SAP2000
    Verification "linear time history of a SDOF" class) under harmonic
    ground acceleration, 5% damping, 4 s; reference = exact closed form /
    Duhamel integral (Chopra Sec. 3.2 & 4.2).  dt = T/100 -> Newmark
    period elongation (pi^2/12)(dt/T)^2 = 0.008% (Chopra Sec. 5.5);
    asserted: peak within 0.2%, whole-history max error < 1% of peak."""
    h, E, mass = 3.0, 2.0e8, 20.0
    I = 2.0e-4
    k = 3 * E * I / h ** 3
    w = math.sqrt(k / mass)
    T = 2 * math.pi / w
    zeta, A, W = 0.05, 2.0, 0.8 * w
    dt = T / 100.0
    nstep = int(4.0 / dt)
    t = np.arange(nstep + 1) * dt
    acc = A * np.sin(W * t)
    m = new_model("D4", E, heights=[h])
    section(m, "S", 1e-2, I)
    planar_xz(m)
    m.add_member("column", "S", (0, 0, 0), (0, 0, h), story="Story1",
                 uid="C1")
    support(m, (0, 0, 0))
    m.nodal_masses.append(NodalMass((0, 0, h), mx=mass))
    m.num_modes = 1
    m.add_th_case("TH", "X", list(acc), dt, damping=zeta)
    th = engine(m).run_time_history("TH")
    u = np.array(th.story_ux["Story1"])
    tt = np.array(th.t)
    ref = _sdof_harmonic(tt, w, zeta, A, W)
    S = "Closed form / Duhamel integral (Chopra Sec. 3.2, 4.2)"
    check("SDOF linear TH, harmonic ground motion", S, "peak |u|",
          np.max(np.abs(ref)), np.max(np.abs(u)), 2e-3, family=FAM,
          note=f"T = {T:.4f} s, dt = T/100")
    hist = np.max(np.abs(u - ref)) / np.max(np.abs(ref))
    check("SDOF linear TH, harmonic ground motion", S,
          "max history error / peak", 0.0, hist, 0.0, family=FAM,
          abs_floor=1e-2)


# --------------------------------------------------------------------------- #
# D5  Rayleigh damping on a 3-DOF system
# --------------------------------------------------------------------------- #
def test_d05_rayleigh_damping_mdof_time_history():
    """Rayleigh damping (Chopra Sec. 11.4.1): SkyFrame fits C = a0 M + a1 K
    to zeta at modes 1 and 3; mode 2 then carries zeta_2 = a0/(2 w2) +
    a1 w2/2 < zeta.  Reference: exact modal superposition of the 3-DOF
    shear building, each mode integrated in closed form (Chopra Sec. 3.2)
    with ITS Rayleigh damping ratio, harmonic ground motion near mode 2
    resonance (so the mode-2 damping value controls the answer)."""
    ks = [3.0e4, 2.4e4, 1.8e4]
    ms = [120.0, 110.0, 90.0]
    zeta = 0.05
    m, zs = _shear_building(ks, ms)
    K, M = _shear_KM(ks, ms)
    w2, phi = np.linalg.eig(np.linalg.solve(M, K))
    o = np.argsort(w2)
    w = np.sqrt(w2[o].real)
    phi = phi[:, o].real
    a0 = 2 * zeta * w[0] * w[2] / (w[0] + w[2])
    a1 = 2 * zeta / (w[0] + w[2])
    zetas = a0 / (2 * w) + a1 * w / 2
    A, W = 1.0, 0.97 * w[1]
    T3 = 2 * math.pi / w[2]
    dt = T3 / 60.0
    nstep = int(6.0 / dt)
    t = np.arange(nstep + 1) * dt
    m.add_th_case("TH", "X", list(A * np.sin(W * t)), dt, damping=zeta)
    th = engine(m).run_time_history("TH")
    tt = np.array(th.t)
    roof = np.array(th.story_ux[f"Story{len(ks)}"])
    one = np.ones(3)
    ref = np.zeros_like(tt)
    for i in range(3):
        f = phi[:, i]
        G = (f @ M @ one) / (f @ M @ f)
        ref += G * f[-1] * _sdof_harmonic(tt, w[i], zetas[i], A, W)
    S = "Chopra Sec. 11.4.1 (Rayleigh) + exact modal superposition"
    check("3-DOF Rayleigh-damped TH near mode-2 resonance", S,
          "peak roof displacement", np.max(np.abs(ref)),
          np.max(np.abs(roof)), 3e-3, family=FAM,
          note=f"Rayleigh zeta_1..3 = {zetas[0]:.4f}/{zetas[1]:.4f}/"
               f"{zetas[2]:.4f}")


# --------------------------------------------------------------------------- #
# D6  rigid diaphragm: coupled lateral-torsional modes
# --------------------------------------------------------------------------- #
def test_d06_rigid_diaphragm_torsional_modes():
    """One-story, rigid-diaphragm building with an unsymmetric column layout
    (ETABS Verification "rigid diaphragm / torsional" class; reference
    Chopra Sec. 9.5 one-story unsymmetric-plan system): cantilever columns
    (k = 3EI/h^3 each way, torsion GJ/h), uniformly distributed mass m over
    an a x b plan -> J_m = m (a^2+b^2)/12 about the plan centre.  Hand
    3x3 eigenproblem in (ux, uy, theta) about the plan centre."""
    a, b, h, E, nu, mass = 8.0, 6.0, 3.5, 2.5e7, 0.2, 80.0
    m = new_model("D6", E, nu=nu, heights=[h])
    m.rigid_diaphragms = True
    cols = [((0, 0), 0.40), ((a, 0), 0.40), ((0, b), 0.55), ((a, b), 0.55),
            ((a / 2, 0), 0.30)]
    from skyframe.core.model import FrameSection
    G = E / (2 * (1 + nu))
    K = np.zeros((3, 3))
    xc, yc = a / 2, b / 2
    for i, ((x, y), s) in enumerate(cols):
        sec = m.add_section(FrameSection.rectangular(f"S{i}", "MAT", s, s))
        m.add_member("column", f"S{i}", (x, y, 0), (x, y, h),
                     story="Story1", uid=f"C{i}")
        support(m, (x, y, 0))
        kx = 3 * E * sec.I22 / h ** 3       # square: I22 = I33
        ky = 3 * E * sec.I33 / h ** 3
        dx, dy = x - xc, y - yc
        K += np.array([[kx, 0, -kx * dy],
                       [0, ky, ky * dx],
                       [-kx * dy, ky * dx,
                        kx * dy * dy + ky * dx * dx + G * sec.J / h]])
    m.story_masses["Story1"] = mass
    m.num_modes = 3
    Mm = np.diag([mass, mass, mass * (a * a + b * b) / 12.0])
    w2 = np.sort(np.linalg.eigvals(np.linalg.solve(Mm, K)).real)
    Tref = 2 * math.pi / np.sqrt(w2)
    md = engine(m).run_modal(3)
    S = "Chopra Sec. 9.5 one-story unsymmetric plan (hand 3x3 eigen)"
    for i in range(3):
        check("Rigid-diaphragm one-story unsymmetric plan", S,
              f"T_{i + 1} (coupled lateral-torsional)", Tref[i],
              md.periods[i], 1e-8, family=FAM)
