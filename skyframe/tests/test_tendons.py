"""Post-tensioning tendons modelled as equivalent loads + the hyperstatic
case (CONTRACT "Post-tensioning tendons (as loads) and hyperstatic case").

Every expected value is derived in-test from hand mechanics:
  * parabolic drape e over chord L, force P: uplift w = 8 P e / L^2;
    simply supported (determinate) beam: M(x) = -P e(x), secondary = 0;
  * two equal continuous spans, tendon at the centroid over the supports
    with sag e per span: interior total M = w L^2 / 8 = P e, primary 0,
    secondary M2(x) = P e x / L, secondary reactions Pe/L, -2Pe/L, Pe/L;
    linear transformation (interior eccentricity changed by delta) leaves
    the total moments unchanged and moves P*delta from secondary to primary;
  * friction P(x) = P0 exp(-(mu alpha + k x));
  * anchor set: 2 int_0^l (P_f - P_f(l)) dx = delta E A.

Sign convention (engine stations): M3 positive sagging (local y up for a
horizontal beam along +X), N positive in tension.
"""

import copy
import hashlib
import json
import math

import pytest

pytest.importorskip("openseespy")

from skyframe.core.model import (BuildingModel, FrameSection,  # noqa: E402
                                 LoadPattern, Material, PointSupport,
                                 ShellSection)
from skyframe.core import tendons as T  # noqa: E402
from skyframe.engine.opensees_engine import OpenSeesEngine  # noqa: E402

E_C = 30e6
P0 = 1000.0           # jacking force = 1e6 kPa * 0.001 m^2
PIN = (1, 1, 1, 1, 0, 0)
ROLL = (0, 1, 1, 0, 0, 0)
ROLL_Z = (0, 0, 1, 0, 0, 0)


def _model(spans=(10.0,), supports=None):
    m = BuildingModel(name="pt")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=E_C, nu=0.2))
    m.set_stories([1.0])
    m.add_section(FrameSection.rectangular("B", "C", 0.4, 0.6))
    x = 0.0
    for k, L in enumerate(spans):
        m.add_member("beam", "B", (x, 0, 0), (x + L, 0, 0), story="Story1",
                     uid=f"B{k + 1}")
        x += L
    xs = [0.0]
    for L in spans:
        xs.append(xs[-1] + L)
    if supports is None:
        supports = [PIN] + [ROLL] * len(spans)
    for xx, rs in zip(xs, supports):
        m.supports.append(PointSupport((xx, 0, 0), rs))
    m.patterns["PT"] = LoadPattern("PT")
    m.add_case("PT", {"PT": 1.0})
    return m


def _tendon(**kw):
    d = {"uid": "T1", "area": 0.001, "jacking_stress": 1.0e6,
         "pattern": "PT"}
    d.update(kw)
    return d


def _run(m):
    m.validate()
    return OpenSeesEngine(m).run().to_dict()


def _react(r, case, x, comp=2):
    nodes = r["nodes"]
    for t, v in r["cases"][case]["reactions"].items():
        if abs(nodes[t][0] - x) < 1e-9 and abs(nodes[t][1]) < 1e-9:
            return v[comp]
    raise KeyError(x)


# --------------------------------------------------------------------------- #
# 1. simply supported beam, parabolic drape (determinate)
# --------------------------------------------------------------------------- #
L1, E1 = 10.0, 0.2


def _ss():
    m = _model()
    m.tendons.append(_tendon(profile={"start": [0, 0, 0], "end": [L1, 0, 0],
                                      "sag": E1}, host="B1"))
    m.hyperstatic_cases["HYP"] = {"case": "PT"}
    return m


def test_ss_uplift_equals_8Pe_over_L2():
    m = _ss()
    r = _run(m)
    w = 8.0 * P0 * E1 / L1 ** 2
    assert r["tendons"]["T1"]["segment_uplift"][0] == pytest.approx(w,
                                                                    rel=1e-12)
    # the distributed loads sum to w*L upward
    up = sum(ln["force"][2] for ln in
             r["tendons"]["T1"]["equivalent_loads"]["lines"])
    assert up == pytest.approx(w * L1, rel=1e-12)
    # anchor forces: horizontal P, vertical P * slope (4e/L) downward
    pts = r["tendons"]["T1"]["equivalent_loads"]["points"]
    assert pts[0]["force"] == pytest.approx([P0, 0.0, -4 * E1 / L1 * P0])
    assert pts[-1]["force"] == pytest.approx([-P0, 0.0, -4 * E1 / L1 * P0])


def test_ss_midspan_moment_is_minus_Pe_and_parabolic():
    r = _run(_ss())
    st = r["cases"]["PT"]["member_stations"]["B1"]
    for x, M in zip(st["x"], st["M3"]):
        ex = 4 * E1 * x * (L1 - x) / L1 ** 2
        assert M == pytest.approx(-P0 * ex, abs=1e-8)
    i = st["x"].index(5.0)
    assert st["M3"][i] == pytest.approx(-P0 * E1, rel=1e-12)
    assert all(n == pytest.approx(-P0) for n in st["N"])  # compression P
    # shear = -P * tendon slope
    for x, V in zip(st["x"], st["V2"]):
        assert V == pytest.approx(P0 * 4 * E1 * (L1 - 2 * x) / L1 ** 2,
                                  abs=1e-8)


def test_ss_zero_secondary_and_reactions():
    r = _run(_ss())
    h = r["hyperstatic"]["HYP"]
    assert h["case"] == "PT" and h["tendon_patterns"] == {"PT": 1.0}
    b = h["members"]["B1"]
    assert b["hosts_tendon"] is True
    for k in ("N", "V2", "V3", "T", "M2", "M3"):
        assert max(abs(v) for v in b["secondary"][k]) < 1e-8
    assert b["primary"]["M3"][5] == pytest.approx(-P0 * E1, rel=1e-12)
    for v in h["reactions"].values():
        assert max(abs(c) for c in v) < 1e-8


# --------------------------------------------------------------------------- #
# 2. two-span continuous beam
# --------------------------------------------------------------------------- #
L2, E2 = 10.0, 0.2


def _two_span(delta=0.0, n_sub=16):
    m = _model(spans=(L2, L2))
    m.tendons.append(_tendon(points=[[0, 0, 0], [L2, 0, delta],
                                     [2 * L2, 0, 0]],
                             sags=[E2, E2], host=["B1", "B2"],
                             n_sub=n_sub))
    m.hyperstatic_cases["HYP"] = {"case": "PT"}
    return m


def test_two_span_total_and_secondary_moments_vs_hand():
    r = _run(_two_span())
    w = 8 * P0 * E2 / L2 ** 2
    h = r["hyperstatic"]["HYP"]["members"]
    st = h["B1"]
    for j, x in enumerate(st["x"]):
        # continuous beam under uniform upward w: M = -3wLx/8 + w x^2/2
        tot = -3 * w * L2 * x / 8 + w * x * x / 2
        assert st["total"]["M3"][j] == pytest.approx(tot, abs=1e-7)
        assert st["primary"]["M3"][j] == pytest.approx(
            -P0 * 4 * E2 * x * (L2 - x) / L2 ** 2, abs=1e-9)
        assert st["secondary"]["M3"][j] == pytest.approx(P0 * E2 * x / L2,
                                                         abs=1e-7)
    assert st["secondary"]["M3"][-1] == pytest.approx(P0 * E2, rel=1e-9)
    # span 2 mirrors span 1
    s2 = h["B2"]
    assert s2["secondary"]["M3"][0] == pytest.approx(P0 * E2, rel=1e-9)
    assert s2["secondary"]["M3"][-1] == pytest.approx(0.0, abs=1e-7)
    # secondary shear constant = Pe/L
    assert all(v == pytest.approx(-P0 * E2 / L2, abs=1e-7) or
               v == pytest.approx(P0 * E2 / L2, abs=1e-7)
               for v in st["secondary"]["V2"])


def test_two_span_secondary_reactions_vs_hand():
    r = _run(_two_span())
    R = P0 * E2 / L2
    assert _react(r, "PT", 0.0) == pytest.approx(R, rel=1e-9)
    assert _react(r, "PT", L2) == pytest.approx(-2 * R, rel=1e-9)
    assert _react(r, "PT", 2 * L2) == pytest.approx(R, rel=1e-9)
    hr = r["hyperstatic"]["HYP"]["reactions"]
    assert hr == r["cases"]["PT"]["reactions"]
    assert sum(v[2] for v in hr.values()) == pytest.approx(0.0, abs=1e-9)


def test_two_span_linear_transformation():
    """Raising the interior-support eccentricity by delta (linear
    transformation, sags kept) leaves the total moments unchanged (to the
    small-slope order (delta/L)^2) and shifts P*delta from the secondary to
    the primary moment at the interior support."""
    delta = 0.1
    r0 = _run(_two_span())
    r1 = _run(_two_span(delta))
    h0 = r0["hyperstatic"]["HYP"]["members"]["B1"]
    h1 = r1["hyperstatic"]["HYP"]["members"]["B1"]
    tol = 2 * (delta / L2) ** 2 * P0 * E2 + 1e-6
    for a, b in zip(h0["total"]["M3"], h1["total"]["M3"]):
        assert b == pytest.approx(a, abs=tol)
    assert h1["primary"]["M3"][-1] == pytest.approx(P0 * delta, rel=1e-3)
    assert h1["secondary"]["M3"][-1] == pytest.approx(P0 * (E2 - delta),
                                                      rel=2e-3)


# --------------------------------------------------------------------------- #
# 3. losses
# --------------------------------------------------------------------------- #
def test_friction_quarter_points_parabola():
    mu, k = 0.2, 0.002
    m = _model()
    m.tendons.append(_tendon(profile={"start": [0, 0, 0], "end": [L1, 0, 0],
                                      "sag": E1}, host="B1",
                             losses={"friction_mu": mu, "wobble_k": k}))
    m.validate()
    rep = T.tendon_report(m, m.tendons[0])
    st = rep["stations"]
    a = 4 * E1 / L1 ** 2                       # e(x) = a x (L - x)

    def arc(x):                                # exact parabola arc length
        def F(t):
            g = a * (L1 - 2 * t)
            return -(g * math.sqrt(1 + g * g) + math.asinh(g)) / (4 * a)
        return F(x) - F(0.0)

    for q in (0.25, 0.5, 0.75, 1.0):
        x = q * L1
        alpha = math.atan(a * L1) - math.atan(a * (L1 - 2 * x))
        P = P0 * math.exp(-(mu * alpha + k * arc(x)))
        j = min(range(len(st["points"])),
                key=lambda i: abs(st["points"][i][0] - x))
        assert st["s"][j] == pytest.approx(arc(x), rel=1e-9)
        assert st["alpha"][j] == pytest.approx(alpha, rel=1e-12)
        assert st["P"][j] == pytest.approx(P, rel=1e-9)


def test_friction_polyline_kink_and_wobble():
    """Harped (polyline) tendon: straight legs (alpha = 0) then a kink of
    2*theta at midspan."""
    mu, k, e = 0.25, 0.003, 0.3
    m = _model()
    m.tendons.append(_tendon(points=[[0, 0, 0], [L1 / 2, 0, -e],
                                     [L1, 0, 0]], host="B1",
                             losses={"friction_mu": mu, "wobble_k": k}))
    m.validate()
    prof = T.force_profile(m, m.tendons[0])
    th = math.atan(e / (L1 / 2))
    leg = math.hypot(L1 / 2, e)
    # quarter point (first leg, mid) and 3/4 point (second leg, mid)
    s_b, P_b = prof["s_bound"], prof["P_bound"]
    j1 = min(range(len(s_b)), key=lambda i: abs(s_b[i] - leg / 2))
    j3 = min(range(len(s_b)), key=lambda i: abs(s_b[i] - 1.5 * leg))
    assert P_b[j1] == pytest.approx(P0 * math.exp(-k * leg / 2), rel=1e-12)
    assert P_b[j3] == pytest.approx(
        P0 * math.exp(-(mu * 2 * th + k * 1.5 * leg)), rel=1e-12)
    assert prof["alpha_bound"][-1] == pytest.approx(2 * th, rel=1e-12)
    # kink force at the harp point = 2 P sin(theta) upward (no losses)
    m.tendons[0]["losses"] = {}
    eq = T.equivalent_loads(m, T.normalize_tendon(m.tendons[0]))
    harp = [p for p in eq["points"] if abs(p["point"][0] - L1 / 2) < 1e-9]
    F = [sum(h["force"][i] for h in harp) for i in range(3)]
    # chord component of T is P on each straight leg (exact tendon force)
    assert F[2] == pytest.approx(2 * P0 * math.sin(th), rel=1e-12)
    assert F[0] == pytest.approx(0.0, abs=1e-9)


def _hand_anchor_set(P, k, dEA, L):
    def f(l):                                  # increasing in l
        return 2 * P * ((1 - math.exp(-k * l)) / k
                        - l * math.exp(-k * l)) - dEA
    lo, hi = 1e-9, L
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return 0.5 * (lo + hi)


def test_anchor_set_length_vs_hand():
    k, dset, Ls = 0.0066, 0.006, 30.0
    m = _model(spans=(Ls,))
    m.tendons.append(_tendon(points=[[0, 0, 0], [Ls, 0, 0]], host="B1",
                             losses={"wobble_k": k, "anchor_set": dset},
                             n_sub=40))
    m.validate()
    Ep = T.tendon_material(m, m.tendons[0]).E
    assert Ep == 196_501_000.0                 # A416Gr270 (default library)
    prof = T.force_profile(m, m.tendons[0])
    l_hand = _hand_anchor_set(P0, k, dset * Ep * 0.001, Ls)
    assert prof["anchor_set_length"]["start"] == pytest.approx(l_hand,
                                                               rel=1e-4)
    assert prof["anchor_set_length"]["end"] == 0.0
    # force at the anchor = 2 P(l) - P0; beyond l: friction curve
    assert prof["P_bound"][0] == pytest.approx(
        2 * P0 * math.exp(-k * l_hand) - P0, rel=1e-4)
    assert prof["P_bound"][-1] == pytest.approx(P0 * math.exp(-k * Ls),
                                                rel=1e-12)
    # linear-friction approximation sqrt(delta E A / p) agrees to ~3 %
    p = P0 * k
    assert l_hand == pytest.approx(math.sqrt(dset * Ep * 0.001 / p),
                                   rel=0.05)


def test_anchor_set_longer_than_tendon_and_both_ends():
    k, Ls = 0.0066, 8.0
    m = _model(spans=(Ls,))
    m.tendons.append(_tendon(points=[[0, 0, 0], [Ls, 0, 0]], host="B1",
                             losses={"wobble_k": k, "anchor_set": 0.006},
                             n_sub=40))
    m.validate()
    prof = T.force_profile(m, m.tendons[0])
    assert prof["anchor_set_length"]["start"] == pytest.approx(Ls)
    # lost area = delta E A over the whole length
    s, P = prof["s_bound"], prof["P_bound"]
    lost = sum(0.5 * ((P0 * math.exp(-k * s[i]) - P[i])
                      + (P0 * math.exp(-k * s[i + 1]) - P[i + 1]))
               * (s[i + 1] - s[i]) for i in range(len(s) - 1))
    Ep = T.tendon_material(m, m.tendons[0]).E
    assert lost == pytest.approx(0.006 * Ep * 0.001, rel=1e-4)
    # both ends: symmetric profile, max of the two one-end profiles
    m.tendons[0]["jacking_end"] = "both"
    m.tendons[0]["losses"] = {"wobble_k": k}
    prof = T.force_profile(m, T.normalize_tendon(m.tendons[0]))
    P = prof["P_bound"]
    assert P[0] == pytest.approx(P0) and P[-1] == pytest.approx(P0)
    assert min(P) == pytest.approx(P0 * math.exp(-k * Ls / 2), rel=1e-12)
    for a, b in zip(P, P[::-1]):
        assert a == pytest.approx(b, rel=1e-12)


def test_long_term_fraction_and_end_jacking():
    m = _model()
    m.tendons.append(_tendon(points=[[0, 0, 0], [L1, 0, 0]], host="B1",
                             jacking_end="end",
                             losses={"wobble_k": 0.01,
                                     "long_term_fraction": 0.15}))
    m.validate()
    prof = T.force_profile(m, m.tendons[0])
    assert prof["P_bound"][-1] == pytest.approx(0.85 * P0, rel=1e-12)
    assert prof["P_bound"][0] == pytest.approx(
        0.85 * P0 * math.exp(-0.01 * L1), rel=1e-12)


# --------------------------------------------------------------------------- #
# 4. equilibrium
# --------------------------------------------------------------------------- #
def _general_tendon(m, host):
    m.tendons.append(_tendon(
        points=[[0, 0.05, 0.0], [4.0, -0.08, -0.15], [7.0, 0.1, 0.1],
                [L1, 0.02, 0.05]],
        sags=[0.12, -0.05, 0.08], host=host, jacking_end="both",
        losses={"friction_mu": 0.2, "wobble_k": 0.004, "anchor_set": 0.006,
                "long_term_fraction": 0.1}, n_sub=12))


def test_equivalent_loads_self_equilibrated():
    m = _model()
    _general_tendon(m, "B1")
    m.validate()
    eq = T.equivalent_loads(m, m.tendons[0])
    F, M = T.resultant(eq)
    assert max(abs(c) for c in F) < 1e-9 * P0
    assert max(abs(c) for c in M) < 1e-9 * P0 * L1
    F2, M2 = T.resultant(eq, about=(3.0, -2.0, 7.0))
    assert max(abs(c) for c in M2) < 1e-9 * P0 * L1
    # the member-load form is self-equilibrated too
    F3 = [0.0, 0.0, 0.0]
    M3 = [0.0, 0.0, 0.0]
    idx = {"global_x": 0, "global_y": 1, "global_z": 2}
    for uid, kind, w, a, b, dn in T.frame_member_loads(m, m.tendons[0], eq):
        i = idx[dn]
        if kind == "moment":
            M3[i] += w
            continue
        tot = w if kind == "point" else w * (b - a) * L1
        x = 0.5 * (a + b) * L1
        f = [0.0, 0.0, 0.0]
        f[i] = tot
        r = (x, 0.0, 0.0)
        F3[i] += tot
        mm = T._cross(r, f)
        for j in range(3):
            M3[j] += mm[j]
    assert max(abs(c) for c in F3) < 1e-9 * P0
    assert max(abs(c) for c in M3) < 1e-8 * P0 * L1


def test_free_body_determinate_3d_beam_zero_reactions_zero_secondary():
    """Statically determinate 3D support set (pin + torsion at A, roller at
    B): a 3D tendon with lateral eccentricity, kinks, friction, anchor set,
    both-end jacking must give zero reactions and zero secondary forces at
    every station (the load set is self-equilibrated and primary = total)."""
    m = _model(supports=[PIN, ROLL])
    _general_tendon(m, "B1")
    m.hyperstatic_cases["HYP"] = {"case": "PT"}
    r = _run(m)
    for v in r["cases"]["PT"]["reactions"].values():
        assert max(abs(c) for c in v) < 1e-7
    sec = r["hyperstatic"]["HYP"]["members"]["B1"]["secondary"]
    prim = r["hyperstatic"]["HYP"]["members"]["B1"]["primary"]
    # exact up to the lumping of the (small) distributed couples of
    # eccentric curvature loads at sub-piece mid-points (documented)
    for k_ in ("N", "V2", "V3", "M2", "M3"):
        assert max(abs(v) for v in sec[k_]) < 1e-6 * P0 * L1, k_
    # torsion: that lumping is the whole distributed torque -> O(1/n_sub)
    tmax = max(abs(v) for v in prim["T"])
    assert tmax > 1.0
    assert max(abs(v) for v in sec["T"]) < 0.05 * tmax
    assert max(abs(v) for v in prim["M2"]) > 1.0     # lateral eccentricity


def test_multi_member_host_split_at_node():
    """A tendon over two collinear members (one span, mid node free): the
    equivalent loads are split at the node and the determinate result is
    unchanged."""
    m = _model(spans=(L1 / 2, L1 / 2), supports=[PIN, None, ROLL])
    m.supports = [s for s in m.supports if s.restraints is not None]
    m.tendons.append(_tendon(profile={"start": [0, 0, 0], "end": [L1, 0, 0],
                                      "sag": E1}, host=["B1", "B2"],
                             n_sub=7))
    m.hyperstatic_cases["HYP"] = {"case": "PT"}
    r = _run(m)
    pieces = T.sub_pieces(m, m.tendons[0])
    assert {p["host"] for p in pieces} == {"B1", "B2"}
    st2 = r["cases"]["PT"]["member_stations"]["B2"]
    assert st2["M3"][0] == pytest.approx(-P0 * E1, rel=1e-9)
    for k_, vals in r["hyperstatic"]["HYP"]["members"]["B2"][
            "secondary"].items():
        assert max(abs(v) for v in vals) < 1e-7


# --------------------------------------------------------------------------- #
# 5. shell host
# --------------------------------------------------------------------------- #
def test_slab_host_straight_plan_path():
    """One-way slab strip (nu = 0, simply supported lines): tendon along the
    strip centre line with parabolic drape e.  Reactions vanish (self-
    equilibrated loads on a determinate support set) and the midspan
    camber matches the beam result 5 P e L^2 / (48 E I)."""
    L, b, t, e = 10.0, 1.0, 0.25, 0.08
    m = BuildingModel(name="slab")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=E_C, nu=0.0))
    m.set_stories([1.0])
    m.add_shell_section(ShellSection("S", "C", t))
    m.add_shell("slab", "shell", "S", [(0, 0, 0), (L, 0, 0), (L, b, 0),
                                       (0, b, 0)], mesh_size=0.5,
                story="Story1", uid="S1")
    for y in (0.0, 0.5, 1.0):
        m.supports.append(PointSupport((0, y, 0), (1, 1 if y == 0 else 0,
                                                   1, 0, 0, 0)))
        m.supports.append(PointSupport((L, y, 0), (0, 0, 1, 0, 0, 0)))
    m.patterns["PT"] = LoadPattern("PT")
    m.add_case("PT", {"PT": 1.0})
    m.tendons.append(_tendon(profile={"start": [0, 0.5, 0],
                                      "end": [L, 0.5, 0], "sag": e},
                             host="S1", n_sub=20))
    m.hyperstatic_cases["HYP"] = {"case": "PT"}
    r = _run(m)
    tot = [0.0] * 3
    for v in r["cases"]["PT"]["reactions"].values():
        tot = [a + c for a, c in zip(tot, v[:3])]
    assert max(abs(c) for c in tot) < 1e-6
    nodes = r["nodes"]
    mid = [t_ for t_, c in nodes.items()
           if abs(c[0] - L / 2) < 1e-9 and abs(c[1] - 0.5) < 1e-9][0]
    uz = r["cases"]["PT"]["node_disp"][mid][2]
    EI = E_C * b * t ** 3 / 12
    assert uz == pytest.approx(5 * P0 * e * L ** 2 / (48 * EI), rel=0.03)
    assert r["hyperstatic"]["HYP"]["members"] == {}


# --------------------------------------------------------------------------- #
# 6. plumbing: round trip, validation, run control, defaults
# --------------------------------------------------------------------------- #
def test_round_trip_and_results_keys():
    m = _two_span()
    m.validate()
    d = json.loads(json.dumps(m.to_dict()))
    assert d["hyperstatic_cases"] == {"HYP": {"case": "PT"}}
    assert d["tendons"][0]["points"][1] == [L2, 0.0, 0.0]
    m2 = BuildingModel.from_dict(d)
    assert m2.to_dict() == m.to_dict()
    assert m2.case_kinds()["HYP"] == "hyperstatic"
    r = OpenSeesEngine(m2).run().to_dict()
    assert r["case_status"]["HYP"] == "finished"
    assert set(r["hyperstatic"]["HYP"]) == {"case", "tendon_patterns",
                                            "reactions", "base", "members"}
    assert set(r["hyperstatic"]["HYP"]["members"]["B1"]) == {
        "x", "hosts_tendon", "total", "primary", "secondary"}
    json.dumps(r)


def test_hyperstatic_runs_referenced_case_as_dependency():
    m = _two_span()
    m.cases_not_run = ["PT"]
    r = _run(m)
    assert r["case_status"]["PT"] == "run_as_dependency"
    assert "HYP" in r["hyperstatic"]
    m.cases_not_run = ["HYP"]
    r = _run(m)
    assert r["case_status"]["HYP"] == "not_run"
    assert "hyperstatic" not in r


def test_pattern_scale_and_combo_superposition():
    m = _two_span()
    m.add_case("PT2", {"PT": 2.0})
    m.hyperstatic_cases["HYP2"] = {"case": "PT2"}
    m.add_combo("C", {"PT": 0.5})
    r = _run(m)
    a = r["hyperstatic"]["HYP"]["members"]["B1"]["secondary"]["M3"]
    b = r["hyperstatic"]["HYP2"]["members"]["B1"]["secondary"]["M3"]
    for x, y in zip(a, b):
        assert y == pytest.approx(2 * x, abs=1e-7)
    c = r["combos"]["C"]["member_stations"]["B1"]["M3"]
    t = r["cases"]["PT"]["member_stations"]["B1"]["M3"]
    for x, y in zip(t, c):
        assert y == pytest.approx(0.5 * x, abs=1e-9)


@pytest.mark.parametrize("mut,msg", [
    (lambda t: t.update(host="NOPE"), "host"),
    (lambda t: t.update(pattern="NOPE"), "unknown load pattern"),
    (lambda t: t.update(jacking_end="middle"), "jacking_end"),
    (lambda t: t.update(area=0.0), "area"),
    (lambda t: t.update(material="Unobtainium"), "unknown material"),
    (lambda t: t.update(losses={"friction": 0.1}), "unknown losses"),
    (lambda t: t.update(losses={"long_term_fraction": 1.0}), "< 1"),
    (lambda t: t.update(points=[[0, 0, 0], [0, 0, 0], [L2, 0, 0]],
                        sags=[0.0, 0.0]), "zero length"),
    (lambda t: t.update(points=[[0, 0, 0], [30, 0, 0]], sags=[0.0]),
     "does not project"),
])
def test_validation_errors(mut, msg):
    m = _two_span()
    mut(m.tendons[0])
    with pytest.raises(ValueError, match=msg):
        m.validate()


def test_hyperstatic_validation_errors():
    m = _two_span()
    m.hyperstatic_cases["H2"] = {"case": "NOPE"}
    with pytest.raises(ValueError, match="unknown static case"):
        m.validate()
    m = _two_span()
    m.patterns["D"] = LoadPattern("D")
    m.add_case("D", {"D": 1.0})
    m.hyperstatic_cases["H2"] = {"case": "D"}
    with pytest.raises(ValueError, match="no tendon load pattern"):
        m.validate()
    m = _two_span()
    m.hyperstatic_cases["PT"] = {"case": "PT"}
    with pytest.raises(ValueError, match="already used"):
        m.validate()


def test_normalize_profile_forms_equivalent():
    a = T.normalize_tendon(_tendon(
        profile=[{"start": [0, 0, 0], "end": [10, 0, 0], "sag": 0.2},
                 {"start": [10, 0, 0], "end": [20, 0, 0], "sag": 0.1}],
        host="B1"))
    b = T.normalize_tendon(_tendon(points=[[0, 0, 0], [10, 0, 0],
                                           [20, 0, 0]],
                                   sags=[0.2, 0.1], host=["B1"]))
    assert a == b
    assert T.normalize_tendon(a) == a
    with pytest.raises(ValueError, match="contiguous"):
        T.normalize_tendon(_tendon(
            profile=[{"start": [0, 0, 0], "end": [10, 0, 0]},
                     {"start": [11, 0, 0], "end": [20, 0, 0]}], host="B1"))


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()
                          ).hexdigest()


def test_defaults_byte_identical():
    """No tendons: the model dict has no new keys and the results are
    byte-identical to a model whose tendon pattern carries nothing; a
    tendon in a pattern NOT used by a case leaves that case identical."""
    m = _model(spans=(L2, L2))
    m.patterns["D"] = LoadPattern("D")
    from skyframe.core.model import MemberLoad
    m.patterns["D"].member_loads.append(MemberLoad("B1", w=12.0))
    m.add_case("D", {"D": 1.0})
    m.validate()
    d0 = m.to_dict()
    assert "tendons" not in d0 and "hyperstatic_cases" not in d0
    r0 = OpenSeesEngine(m).run().to_dict()
    assert "tendons" not in r0 and "hyperstatic" not in r0
    r0b = OpenSeesEngine(BuildingModel.from_dict(copy.deepcopy(d0))
                         ).run().to_dict()
    assert _digest(r0) == _digest(r0b)
    m.tendons.append(_tendon(points=[[0, 0, 0], [L2, 0, 0], [2 * L2, 0, 0]],
                             sags=[E2, E2], host=["B1", "B2"]))
    r1 = OpenSeesEngine(m).run().to_dict()
    assert _digest(r1["cases"]["D"]) == _digest(r0["cases"]["D"])
