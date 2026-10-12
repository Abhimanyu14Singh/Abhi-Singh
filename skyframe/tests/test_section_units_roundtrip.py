"""D3 audit: section editors follow the display units — round trip.

The Section Designer, Built-up editor and parametric section dialogs read
every typed number through ``js/units.js`` (``parse(kind, text)``) into the
SI model store.  The SAME section typed in kip-in and in kN-mm must store
identical properties.  The display -> SI conversion is evaluated by the real
``units.js`` under node (when available); the stored properties come from the
backend endpoints the dialogs call (designer upsert, built-up properties,
parametric shape properties).
"""
import json
import os
import shutil
import subprocess

import pytest

from skyframe.core import shape_props
from skyframe.core.model import BuildingModel, Material

pytestmark = pytest.mark.filterwarnings("ignore")

WEB_JS = os.path.join(os.path.dirname(__file__), "..", "skyframe", "api",
                      "web", "js", "units.js")
NODE = shutil.which("node") or ("/opt/node22/bin/node"
                                if os.path.exists("/opt/node22/bin/node")
                                else None)
IN, MM = 0.0254, 0.001

# the same geometry typed in each unit set (display numbers)
TYPED = {
    "kip-in": {"rect": [[-5, -10], [5, -10], [5, 10], [-5, 10]],
               "hole": [[-2, -4], [2, -4], [2, 4], [-2, 4]],
               "bar": [0, 8, 0.79],                     # y, z (in), area in^2
               "plate": [10, 1], "I": {"d": 16, "bf": 8, "tf": 0.5,
                                       "tw": 0.3}},
    "kN-mm": {"rect": [[-127, -254], [127, -254], [127, 254], [-127, 254]],
              "hole": [[-50.8, -101.6], [50.8, -101.6], [50.8, 101.6],
                       [-50.8, 101.6]],
              "bar": [0, 203.2, 509.6764],
              "plate": [254, 25.4], "I": {"d": 406.4, "bf": 203.2,
                                          "tf": 12.7, "tw": 7.62}},
}


def _to_si_python(sset):
    """Reference conversion with the exact factors (no node)."""
    L = IN if sset == "kip-in" else MM
    t = TYPED[sset]
    return {"rect": [[y * L, z * L] for y, z in t["rect"]],
            "hole": [[y * L, z * L] for y, z in t["hole"]],
            "bar": [t["bar"][0] * L, t["bar"][1] * L, t["bar"][2] * L * L],
            "plate": [v * L for v in t["plate"]],
            "I": {k: v * L for k, v in t["I"].items()}}


def _to_si_units_js(tmp_path):
    """display -> SI through the frontend's own units.js (parse)."""
    script = tmp_path / "conv.mjs"
    url = "file://" + os.path.abspath(WEB_JS)
    script.write_text(
        f"import U from {json.dumps(url)};\n"
        f"const T = {json.dumps(TYPED)};\n"
        "const out = {};\n"
        "for (const [set, t] of Object.entries(T)) {\n"
        "  U.setUnits(set, {silent: true});\n"
        "  const d = v => U.parse('dim', String(v));\n"
        "  out[set] = {\n"
        "    rect: t.rect.map(p => p.map(d)), hole: t.hole.map(p => p.map(d)),\n"
        "    bar: [d(t.bar[0]), d(t.bar[1]), U.parse('area', String(t.bar[2]))],\n"
        "    plate: t.plate.map(d),\n"
        "    I: Object.fromEntries(Object.entries(t.I).map(([k, v]) => [k, d(v)])),\n"
        "    labels: [U.label('dim'), U.label('area'), U.label('inertia')],\n"
        "  };\n"
        "}\n"
        "console.log(JSON.stringify(out));\n")
    res = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                         timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@pytest.fixture()
def si(tmp_path):
    if NODE is None:
        return {s: _to_si_python(s) for s in TYPED}
    out = _to_si_units_js(tmp_path)
    assert out["kip-in"]["labels"] == ["in", "in²", "in⁴"]
    assert out["kN-mm"]["labels"] == ["mm", "mm²", "mm⁴"]
    for s in TYPED:                        # units.js == exact factors
        ref = _to_si_python(s)
        assert out[s]["plate"] == pytest.approx(ref["plate"], rel=1e-15)
    return out


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app, _state
    app = create_app()
    app.testing = True
    with app.test_client() as c:
        yield c, _state


def _close(a, b, rel=1e-12):
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-300)


def test_designer_section_kip_in_equals_kn_mm(client, si):
    c, state = client
    m = BuildingModel("u")
    m.add_material(Material("conc", 25e6, 0.2, 24.0))
    m.add_material(Material("bar", 200e6, 0.3, 77.0))
    state["model"] = m
    props = {}
    for sset in TYPED:
        g = si[sset]
        sec = {"name": "D_" + sset.replace("-", ""), "material": "conc",
               "polygons": [{"vertices": g["rect"]},
                            {"vertices": g["hole"], "hole": True}],
               "rebar": [{"y": g["bar"][0], "z": g["bar"][1],
                          "area": g["bar"][2], "material": "bar"}]}
        r = c.post("/api/sections/designer",
                   json={"action": "upsert", "section": sec})
        assert r.status_code == 200, r.get_json()
        props[sset] = r.get_json()["model"]["sections"][sec["name"]]
    a, b = props["kip-in"], props["kN-mm"]
    for k in ("A", "I33", "I22", "J"):
        assert _close(a[k], b[k]), (k, a[k], b[k])
    # hand: 10x20 in minus the 4x8 in hole + the bar transformed with
    # (n - 1), n = Es / Ec = 200e6 / 25e6 = 8
    assert a["A"] == pytest.approx((200 - 32 + 7 * 0.79) * IN ** 2,
                                   rel=1e-12)


def test_builtup_plate_kip_in_equals_kn_mm(client, si):
    c, _ = client
    mat = next(iter(c.get("/api/model").get_json()["materials"]))
    props = {}
    for sset in TYPED:
        b, t = si[sset]["plate"]
        sec = {"name": "BU", "material": mat, "components": [
            {"kind": "plate", "b": b, "t": t, "x": 0.0, "y": 0.0,
             "angle": 0.0}]}
        r = c.post("/api/sections/builtup",
                   json={"action": "properties", "section": sec})
        assert r.status_code == 200, r.get_json()
        props[sset] = r.get_json()["properties"]
    a, b = props["kip-in"], props["kN-mm"]
    for k in ("A", "I33", "I22", "J", "S33", "Z33", "r33"):
        assert _close(a[k], b[k]), (k, a[k], b[k])
    assert a["A"] == pytest.approx(10 * IN ** 2, rel=1e-12)


def test_parametric_I_kip_in_equals_kn_mm(si):
    a = shape_props.shape_properties("I", si["kip-in"]["I"])
    b = shape_props.shape_properties("I", si["kN-mm"]["I"])
    for k in ("A", "I33", "I22", "J", "Cw", "S33", "Z33", "As2", "As3"):
        assert _close(a[k], b[k]), (k, a[k], b[k])
    # hand A = 2 bf tf + (d - 2 tf) tw  (in^2)
    assert a["A"] == pytest.approx((2 * 8 * 0.5 + 15 * 0.3) * IN ** 2,
                                   rel=1e-12)
