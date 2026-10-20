"""Supa drop-in on the SkyFrame engine: the kit's own closed-form checks
(SP Files/supa-fem-kit/tools/check.ts) plus the stability report."""

import math

import pytest

from skyframe.engine_service.supa import CASES, ModelError, solve_fem
from skyframe.engine_service.stability import check

E, G = 29000 * 144, 11200 * 144
SEC = dict(E=E, G=G, A=0.1, Iy=0.01, Iz=0.01, J=0.001)


def loads(n):
    return {c: [0.0] * (3 * n) for c in CASES}


def near(got, want, tol=1e-3):
    assert abs(got - want) <= tol * abs(want), (got, want)


def el(i, j, yh, src="x", ri=False, rj=False, q=None, kind="frame"):
    return dict(i=i, j=j, **SEC, yh=yh, ri=ri, rj=rj, src=src, kind=kind, q=q or {})


def test_cantilever():
    L, P = 12, 10
    m = dict(xyz=[0, 0, 0, 0, 0, L], fixed=[True, False], els=[el(0, 1, [1, 0, 0], "col")],
             dia=[], nodeLoads=loads(2), diaLoads=loads(0), notes=[])
    m["nodeLoads"]["Wx"][3] = P
    r = solve_fem(m)
    near(r["nodeU"]["Wx"][3], P * L**3 / (3 * E * SEC["Iz"]))
    near(r["pieces"]["col"]["Wx"]["M"], P * L)
    near(r["pieces"]["col"]["Wx"]["V"], P)
    near(-r["balance"]["Wx"]["reactions"][0], P)


def test_simple_beam_in_two_halves():
    L, w = 30, 2
    m = dict(xyz=[0, 0, 0, L / 2, 0, 0, L, 0, 0], fixed=[True, False, True],
             els=[el(0, 1, [0, 1, 0], "beam", ri=True, q={"D": [0, 0, -w]}),
                  el(1, 2, [0, 1, 0], "beam", rj=True, q={"D": [0, 0, -w]})],
             dia=[], nodeLoads=loads(3), diaLoads=loads(0), notes=[])
    r = solve_fem(m)
    near(-r["nodeU"]["D"][5], 5 * w * L**4 / (384 * E * SEC["Iy"]))
    near(r["pieces"]["beam"]["D"]["M"], w * L * L / 8)
    near(r["pieces"]["beam"]["D"]["V"], w * L / 2)
    near(r["balance"]["D"]["reactions"][2], w * L)


def test_two_columns_on_a_rigid_diaphragm():
    L, P, s = 12, 10, 20
    m = dict(xyz=[0, 0, 0, 0, 0, L, s, 0, 0, s, 0, L], fixed=[True, False, True, False],
             els=[el(0, 1, [1, 0, 0], "c1"), el(2, 3, [1, 0, 0], "c2")],
             dia=[dict(id="roof", x=s / 2, y=0, z=L, nodes=[1, 3])],
             nodeLoads=loads(4), diaLoads=loads(1), notes=[])
    m["diaLoads"]["Wx"][0] = P
    m["diaLoads"]["Wy"][1] = P
    m["diaLoads"]["Wy"][2] = P * 5
    r = solve_fem(m)
    k = 3 * E * SEC["Iz"] / L**3
    near(r["dia"][0]["u"]["Wx"][0], P / 2 / k)
    near(r["pieces"]["c1"]["Wx"]["V"], P / 2)
    turn = P * 5 / (2 * k * (s / 2) ** 2 + 2 * G * SEC["J"] / L)
    near(r["dia"][0]["u"]["Wy"][1], P / 2 / k)
    near(r["nodeU"]["Wy"][3 * 3 + 1], P / 2 / k + (s / 2) * turn)


def test_tension_positive_and_wall_strips_add_side_by_side():
    # two strips of one wall under a lateral load at their shared head: shears add
    L, P = 10, 20
    m = dict(xyz=[0, 0, 0, 0, 0, L, 5, 0, 0, 5, 0, L], fixed=[True, False, True, False],
             els=[el(0, 1, [1, 0, 0], "W1", kind="wall"), el(2, 3, [1, 0, 0], "W1", kind="wall")],
             dia=[dict(id="r", x=2.5, y=0, z=L, nodes=[1, 3])],
             nodeLoads=loads(4), diaLoads=loads(1), notes=[])
    m["diaLoads"]["Wx"][0] = P
    m["nodeLoads"]["D"][5] = 3.0        # pull up on node 1 -> tension in strip 1
    r = solve_fem(m)
    near(r["pieces"]["W1"]["Wx"]["V"], P)
    assert r["pieces"]["W1"]["D"]["N"] > 0


def test_pinned_joint_rotation_is_held_not_a_mechanism():
    # a portal whose beam is pinned at both ends onto a column top joint where
    # nothing else resists rotation about x: reported as a pinned joint only
    m = dict(xyz=[0, 0, 0, 0, 0, 10, 10, 0, 10, 10, 0, 0], fixed=[True, False, False, True],
             els=[el(0, 1, [1, 0, 0], "c1"), el(1, 2, [0, 1, 0], "b", ri=True, rj=True,
                                                   q={"D": [0, 0, -1]}), el(3, 2, [1, 0, 0], "c2")],
             dia=[], nodeLoads=loads(4), diaLoads=loads(0), notes=[])
    r = solve_fem(m)
    near(r["pieces"]["b"]["D"]["M"], 1 * 10 * 10 / 8)
    assert r["loose"] == 0
    a, q = r["balance"]["D"]["applied"], r["balance"]["D"]["reactions"]
    assert abs(a[2] + q[2]) < 1e-6 * abs(a[2])


def test_floating_member_is_reported_and_held():
    m = dict(xyz=[0, 0, 0, 0, 0, 10, 5, 5, 5, 6, 5, 5], fixed=[True, False, False, False],
             els=[el(0, 1, [1, 0, 0], "col"), el(2, 3, [0, 1, 0], "orphan", q={"D": [0, 0, -1]})],
             dia=[], nodeLoads=loads(4), diaLoads=loads(0), notes=[])
    st = check(m["xyz"], m["fixed"], m["els"], m["dia"])
    assert st.floating_groups == [[2, 3]]
    r = solve_fem(m)
    assert set(r["looseNodes"]) >= {2, 3}
    assert any("Not connected to the ground" in n for n in r["notes"])
    # its 1 kip has no path to the ground, and the balance says so
    assert abs(r["balance"]["D"]["applied"][2] + r["balance"]["D"]["reactions"][2]) == pytest.approx(1.0, rel=1e-6)


def test_collinear_pin_chain_is_a_mechanism():
    # a node reached only by two pin-ended members along one line: free across it
    m = dict(xyz=[0, 0, 5, 5, 0, 5, 10, 0, 5], fixed=[True, False, True],
             els=[el(0, 1, [0, 1, 0], "a", ri=True, rj=True), el(1, 2, [0, 1, 0], "b", ri=True, rj=True)],
             dia=[], nodeLoads=loads(3), diaLoads=loads(0), notes=[])
    st = check(m["xyz"], m["fixed"], m["els"], m["dia"])
    assert set(st.free_translations[1]) == {"uy", "uz"}
    assert st.srcs_at[1] == ["a", "b"]
    r = solve_fem(m)
    assert 1 in r["looseNodes"]


def test_bad_model_is_refused_with_a_reason():
    with pytest.raises(ModelError, match="bad nodes"):
        solve_fem(dict(xyz=[0, 0, 0], fixed=[True], els=[el(0, 5, [1, 0, 0])], dia=[],
                       nodeLoads=loads(1), diaLoads=loads(0)))


def test_desktop_engine_solve_switch(tmp_path):
    import json
    import shutil
    from pathlib import Path

    from skyframe.desktop import main
    src = Path(__file__).resolve().parents[1] / "skyframe" / "engine_service" / "schema" / "examples" / "portal.supa_model.json"
    f = tmp_path / "portal.model.json"
    shutil.copy(src, f)
    assert main(["--engine-solve", str(f)]) == 0
    r = json.loads((tmp_path / "portal.out.json").read_text())
    near(r["pieces"]["b"]["D"]["M"], 1.0 * 20 * 20 / 8)
    near(r["dia"][0]["u"]["Wx"][0], 10 / 2 / (3 * E * SEC["Iz"] / 12**3))
