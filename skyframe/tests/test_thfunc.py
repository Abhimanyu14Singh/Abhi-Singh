"""Time-history function input: PEER AT2 / time-value / equal-interval
import with header skip + units/scale, the periodic and ramp generators,
the exact piecewise-linear response spectrum, frequency-domain spectral
matching (+-10% over the period range) and the HTTP endpoints.

Every number is pinned by hand: the AT2 sample below is embedded verbatim
(classic NGA header, 5 values per line), generator samples are evaluated
in closed form, the spectrum is cross-checked against an independent
fine-step RK4 integration, and the matched spectrum is re-measured on a
grid DIFFERENT from the one the matcher iterated on.
"""

import math

import numpy as np
import pytest

from skyframe.core import thfunc as tf
from skyframe.core.model import BuildingModel, TimeHistoryFunction

G = 9.80665

AT2_SAMPLE = """PEER NGA STRONG MOTION DATABASE RECORD
SKYFRAME TEST 01/01/26, SYNTHETIC STATION, 090
ACCELERATION TIME SERIES IN UNITS OF G
NPTS=   12, DT=   .0100 SEC
  .1000000E-02  .2000000E-02 -.3000000E-02  .4000000E-02  .5000000E-02
 -.6000000E-02  .7000000E-02  .8000000E-02 -.9000000E-02  .1000000E-01
  .1100000E-01 -.1200000E-01  .9999000E+00
"""

AT2_WEST2 = """PEER NGA-West2 Database
Synthetic, 01/01/2026, Station, 000
Acceleration in g
     4    0.0050    NPTS, DT
 0.10 -0.20 0.30 -0.40
"""


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def test_at2_embedded_sample_parses_exactly():
    rec = tf.parse_at2(AT2_SAMPLE)
    assert rec["npts"] == 12 and rec["dt"] == pytest.approx(0.01)
    assert rec["units"] == "g"
    # only NPTS values are taken (the 13th number is trailing junk)
    assert len(rec["values"]) == 12
    assert rec["values"][2] == pytest.approx(-0.003)
    assert rec["values"][-1] == pytest.approx(-0.012)
    out = tf.import_function(AT2_SAMPLE, "at2")
    assert out["dt"] == pytest.approx(0.01)
    assert out["values"][0] == pytest.approx(0.001 * G, rel=1e-12)
    assert out["values"][11] == pytest.approx(-0.012 * G, rel=1e-12)
    assert out["source"]["kind"] == "file"
    assert out["source"]["format"] == "at2" and out["source"]["npts"] == 12
    # scale factor and a units override multiply exactly
    o2 = tf.import_function(AT2_SAMPLE, "at2", scale=2.5, units="m/s2")
    assert o2["values"][1] == pytest.approx(2.5 * 0.002, rel=1e-12)


def test_at2_west2_header_and_errors():
    out = tf.import_function(AT2_WEST2, "at2")
    assert out["dt"] == pytest.approx(0.005)
    assert out["values"] == pytest.approx([0.1 * G, -0.2 * G, 0.3 * G,
                                           -0.4 * G])
    short = AT2_SAMPLE.replace("NPTS=   12", "NPTS=   40")
    with pytest.raises(ValueError, match="only 13 values"):
        tf.parse_at2(short)
    with pytest.raises(ValueError, match="NPTS"):
        tf.parse_at2("a\nb\nc\nno header here\n1 2 3")
    with pytest.raises(ValueError, match="format"):
        tf.import_function(AT2_SAMPLE, "xyz")


def test_time_value_pairs_uniform_resampled_and_prepended():
    txt = "time accel header\n0.0 1.0\n0.1 2.0\n0.2 -1.0\n0.3 0.5\n"
    out = tf.import_function(txt, "time_value", header_lines=1,
                             units="m/s2")
    assert out["dt"] == pytest.approx(0.1)
    assert out["values"] == pytest.approx([1.0, 2.0, -1.0, 0.5])
    assert not out["warnings"]
    # non-uniform -> resampled at the smallest spacing by linear interp
    txt = "0.0 0.0\n0.1 1.0\n0.3 -1.0\n"
    out = tf.import_function(txt, "time_value", units="m/s2")
    assert out["dt"] == pytest.approx(0.1)
    assert out["values"] == pytest.approx([0.0, 1.0, 0.0, -1.0])
    assert any("resampled" in w for w in out["warnings"])
    # explicit dt + a first time > 0 -> (0, 0) prepended
    out = tf.import_function("0.2 1.0\n0.4 3.0\n", "time_value",
                             dt=0.1, units="cm/s2")
    assert out["values"] == pytest.approx([0.0, 0.005, 0.01, 0.02, 0.03])
    assert any("prepended" in w for w in out["warnings"])
    with pytest.raises(ValueError, match="odd count"):
        tf.import_function("0 1 2", "time_value")
    with pytest.raises(ValueError, match="increase"):
        tf.import_function("0 1\n0 2\n", "time_value")


def test_equal_interval_header_skip_units_scale():
    txt = "my record\nskip me 99\n1 2 3\n4 5\n"
    out = tf.import_function(txt, "equal", dt=0.02, header_lines=2,
                             units="cm/s2", scale=10.0)
    assert out["dt"] == 0.02
    assert out["values"] == pytest.approx([0.1, 0.2, 0.3, 0.4, 0.5])
    with pytest.raises(ValueError, match="dt"):
        tf.import_function(txt, "equal", header_lines=2)
    with pytest.raises(ValueError, match="units"):
        tf.import_function(txt, "equal", dt=0.1, units="furlongs")


# --------------------------------------------------------------------------- #
# generators
# --------------------------------------------------------------------------- #
def test_generators_closed_form():
    s = tf.generate_function("sine", period=0.8, steps_per_cycle=8,
                             cycles=2, amplitude=3.0)
    assert s["dt"] == pytest.approx(0.1)
    assert len(s["values"]) == 17
    for k, v in enumerate(s["values"]):
        assert v == pytest.approx(3.0 * math.sin(2 * math.pi * k / 8),
                                  abs=1e-12)
    c = tf.generate_function("cosine", period=1.0, steps_per_cycle=4,
                             amplitude=2.0)
    assert c["values"] == pytest.approx([2.0, 0.0, -2.0, 0.0, 2.0],
                                        abs=1e-12)
    r = tf.generate_function("ramp", ramp_time=0.5, amplitude=4.0,
                             max_time=1.0, dt=0.25)
    assert r["values"] == pytest.approx([0.0, 2.0, 4.0, 4.0, 4.0])
    t = tf.generate_function("triangular", period=1.0, steps_per_cycle=8,
                             amplitude=1.0)
    assert t["values"] == pytest.approx([0, .5, 1, .5, 0, -.5, -1, -.5, 0])
    st = tf.generate_function("sawtooth", period=1.0, ramp_time=0.125,
                              steps_per_cycle=8, amplitude=1.0)
    assert st["values"] == pytest.approx([0, 1, 1, 1, 0, -1, -1, -1, 0])
    with pytest.raises(ValueError, match="period/4"):
        tf.generate_function("sawtooth", period=1.0, ramp_time=0.3)
    u = tf.generate_function("user_periodic", values=[0, 1, 0], dt=0.1,
                             cycles=3, amplitude=2.0)
    assert u["values"] == pytest.approx([0, 2, 0, 2, 0, 2, 0])
    assert u["source"]["kind"] == "user_periodic"
    with pytest.raises(ValueError, match="generator"):
        tf.generate_function("square")


# --------------------------------------------------------------------------- #
# response spectrum + spectral matching
# --------------------------------------------------------------------------- #
def _synthetic(seed=7, dt=0.01, n=2000):
    rng = np.random.default_rng(seed)
    t = np.arange(n) * dt
    env = np.where(t < 2, (t / 2) ** 2,
                   np.where(t < 10, 1.0, np.exp(-0.4 * (t - 10))))
    return (rng.standard_normal(n) * env).tolist(), dt


def _rk4_psa(a, dt, T, z=0.05, sub=40, every=1):
    """Independent fine-step RK4 of the SDOF under the linearly
    interpolated record (a different integrator from the one under
    test)."""
    w = 2 * math.pi / T
    t = np.arange(len(a)) * dt
    h = dt / sub
    af = np.interp(np.arange((len(a) - 1) * sub + 1) * h, t, a)
    u = v = um = 0.0

    def f(uu, vv, aa):
        return vv, -aa - 2 * z * w * vv - w * w * uu
    for k in range(len(af) - 1):
        a0, a1 = af[k], af[k + 1]
        am = 0.5 * (a0 + a1)
        k1 = f(u, v, a0)
        k2 = f(u + h / 2 * k1[0], v + h / 2 * k1[1], am)
        k3 = f(u + h / 2 * k2[0], v + h / 2 * k2[1], am)
        k4 = f(u + h * k3[0], v + h * k3[1], a1)
        u += h / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        v += h / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        if (k + 1) % every == 0:
            um = max(um, abs(u))
    return w * w * um


def test_response_spectrum_exact_vs_independent_rk4():
    a, dt = _synthetic(n=600)
    # >= 20 samples per period: peak read at the record steps, exact
    Ts = [0.2, 0.5, 2.0]
    S = tf.response_spectrum(a, dt, Ts, 0.05)
    for T, s in zip(Ts, S):
        assert s == pytest.approx(_rk4_psa(a, dt, T, every=40), rel=1e-6)
    # short periods are sub-stepped: within the 1 - cos(pi/20) = 1.2%
    # peak-sampling bound of the continuous (fine RK4) maximum
    for T in (0.03, 0.06):
        s = tf.response_spectrum(a, dt, [T], 0.05)[0]
        ref = _rk4_psa(a, dt, T)
        assert ref * (1 - 0.0125) <= s <= ref * (1 + 1e-6)


def test_spectral_matching_within_10_percent():
    a, dt = _synthetic()
    target = [[0.0, 0.4], [0.1, 1.0], [0.5, 1.0], [1.0, 0.6], [2.0, 0.3],
              [4.0, 0.15]]                                    # Sa in g
    res = tf.match_spectrum(a, dt, target, t_min=0.05, t_max=3.0)
    assert res["source"]["method"] == "frequency_domain"
    assert res["max_misfit"] <= 0.10
    # the seed really was far from the target
    orig = np.array(res["original"]) / np.array(res["target"])
    assert np.max(np.abs(orig - 1.0)) > 0.3
    # independent check on a DIFFERENT, denser period grid
    Tchk = np.exp(np.linspace(math.log(0.05), math.log(3.0), 157))
    S = tf.response_spectrum(res["values"], dt, Tchk, 0.05)
    tgt = np.interp(Tchk, [p[0] for p in target],
                    [p[1] for p in target]) * G
    assert np.max(np.abs(S / tgt - 1.0)) <= 0.10
    assert len(res["values"]) == len(a) and res["dt"] == dt
    with pytest.raises(ValueError, match="2 dt"):
        tf.match_spectrum(a, dt, target, t_min=0.01)


# --------------------------------------------------------------------------- #
# model field + API
# --------------------------------------------------------------------------- #
def test_th_function_source_roundtrip_and_legacy_shape():
    mdl = BuildingModel(name="f")
    mdl.th_functions["A"] = TimeHistoryFunction("A", [0.0, 1.0], 0.01)
    assert "source" not in mdl.to_dict()["th_functions"]["A"]
    src = tf.import_function(AT2_SAMPLE, "at2")
    mdl.th_functions["B"] = TimeHistoryFunction("B", src["values"],
                                                src["dt"], src["source"])
    d = mdl.to_dict()
    m2 = BuildingModel.from_dict(d)
    assert m2.th_functions["B"].source == src["source"]
    assert m2.to_dict()["th_functions"] == d["th_functions"]
    d["th_functions"]["B"]["source"] = {"kind": "magic"}
    with pytest.raises(ValueError, match="source.kind"):
        BuildingModel.from_dict(d)


def test_api_th_function_endpoints(api_client):
    r = api_client.post("/api/th_function/import",
                        json={"text": AT2_SAMPLE, "format": "at2"})
    assert r.status_code == 200
    assert r.get_json()["values"][0] == pytest.approx(0.001 * G)
    assert api_client.post("/api/th_function/import",
                           json={"text": "", "format": "at2"}
                           ).status_code == 400
    r = api_client.post("/api/th_function/generate",
                        json={"kind": "sine", "period": 1.0,
                              "steps_per_cycle": 4})
    assert r.status_code == 200
    assert r.get_json()["values"] == pytest.approx([0, 1, 0, -1, 0],
                                                   abs=1e-12)
    assert api_client.post("/api/th_function/generate",
                           json={"kind": "nope"}).status_code == 400
    a, dt = _synthetic(n=800)
    r = api_client.post("/api/th_function/match", json={
        "values": a, "dt": dt, "t_min": 0.1, "t_max": 2.0,
        "target": [[0.1, 0.8], [0.5, 0.8], [2.0, 0.2]]})
    assert r.status_code == 200
    body = r.get_json()
    assert body["max_misfit"] <= 0.10 and len(body["values"]) == 800
    assert api_client.post("/api/th_function/match",
                           json={"values": a}).status_code == 400
