"""Wave C (C9): results CSV folder + the C9 HTTP endpoints
(moment-curvature, shell stiffness preview) and the DB-edit columns.
CONTRACT "Anisotropic shells, material angle, Section Designer
moment-curvature, layered shells with any material, results CSV folder".

* every Show-Tables table becomes one CSV whose row count equals the
  table's and whose cells equal the SQLite export cell for cell (same
  converted values);
* the HTTP moment-curvature result equals the library call; the
  stiffness preview equals h Qbar / h^3/12 Qbar.
"""

from __future__ import annotations

import base64
import csv
import io
import sqlite3
import warnings
import zipfile

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")

from skyframe.core import aniso, ortho, tables                    # noqa
from skyframe.core.model import (BuildingModel, Material,         # noqa
                                 NodalLoad, ShellRegion, ShellSection)
from skyframe.io.results_csv import (csv_files,                   # noqa
                                     export_results_csv_folder,
                                     export_results_csv_zip)
from skyframe.io.results_export import export_results_sqlite      # noqa

import test_results_export as RE                                   # noqa
import test_wave_c9_mc as MC                                       # noqa


@pytest.fixture(scope="module")
def solved():
    m = RE._coupled_wall(frame_beam=True)
    _e, _r, d, ctx = RE._solve(m)
    return m, d, ctx


def _read(text):
    return list(csv.reader(io.StringIO(text)))


def test_csv_folder_every_table_matches_sqlite(solved, tmp_path):
    m, d, ctx = solved
    rep = export_results_csv_folder(m, d, str(tmp_path / "out"), ctx)
    files = {p.name: p.read_text(encoding="utf-8")
             for p in (tmp_path / "out").iterdir()}
    idx = _read(files["_index.csv"])
    assert idx[0] == ["TableKey", "TableName", "TableGroup", "NumRows",
                      "File"]
    assert {r[0] for r in idx[1:]} == set(tables.TABLES)
    assert "README.txt" in files and "_units.csv" in files
    meta = dict(_read(files["_meta.csv"])[1:])
    assert meta["Units"] == "kN-m" and meta["ProgramName"] == "SkyFrame"
    data, _ = export_results_sqlite(m, d, ctx)
    p = tmp_path / "r.sqlite"
    p.write_bytes(data)
    con = sqlite3.connect(str(p))
    for key, title, _g, n, fn in idx[1:]:
        rows = _read(files[fn])
        assert len(rows) - 1 == int(n) == rep["tables"][key]
        name = con.execute('SELECT TableName FROM "Table Index" WHERE '
                           'TableKey = ?', (key,)).fetchone()[0]
        db = con.execute(f'SELECT * FROM "{name}"').fetchall()
        assert len(db) == len(rows) - 1
        for a, b in zip(rows[1:], db):
            for x, y in zip(a, b):
                if y is None:
                    assert x == ""
                elif isinstance(y, float):
                    assert float(x) == y            # full precision (repr)
                elif isinstance(y, int) and x in ("TRUE", "FALSE"):
                    assert (x == "TRUE") == bool(y)
                else:
                    assert x == str(y)
    con.close()
    # the units file names a unit for every M3 column
    units = _read(files["_units.csv"])
    assert ["Element Forces - Frames.csv", "M3", "moment", "kN*m"] in units


def test_csv_zip_deterministic_and_units(solved):
    m, d, ctx = solved
    z1, rep = export_results_csv_zip(m, d, ctx, keys=["spandrel_forces"],
                                     units="kip-ft")
    z2, _ = export_results_csv_zip(m, d, ctx, keys=["spandrel_forces"],
                                   units="kip-ft")
    assert z1 == z2
    zf = zipfile.ZipFile(io.BytesIO(z1))
    assert zf.testzip() is None
    names = zf.namelist()
    assert names[0].startswith(rep["folder"] + "/")
    sp = _read(zf.read(f"{rep['folder']}/Spandrel Forces.csv")
               .decode("utf-8"))
    assert "V2 [kip]" in sp[0]
    v = tables.compute_table("spandrel_forces", d, m, ctx)["rows"][0]["V2"]
    assert float(sp[1][sp[0].index("V2 [kip]")]) == pytest.approx(
        v / 4.4482216152605, rel=1e-12)
    files, _ = csv_files(m, d, ctx, keys=["spandrel_forces"])
    assert set(files) == {"Spandrel Forces.csv", "_index.csv",
                          "_units.csv", "_meta.csv", "README.txt"}


@pytest.fixture()
def client():
    from skyframe.api import server
    app = server.create_app()
    app.config["TESTING"] = True
    saved = server._state["model"]
    yield app.test_client(), server
    server._state["model"] = saved
    server._tables_store.clear()


def test_api_csv_export(client):
    c, _srv = client
    m = RE._coupled_wall(frame_beam=True)
    assert c.post("/api/model", json=m.to_dict()).status_code == 200
    assert c.post("/api/analyze").status_code == 200
    r = c.post("/api/tables/export/csv", json={"tables": ["story_drifts"]})
    j = r.get_json()
    assert r.status_code == 200 and j["mime"] == "application/zip"
    assert j["filename"].endswith("_results.zip")
    zf = zipfile.ZipFile(io.BytesIO(base64.b64decode(j["content_b64"])))
    assert any(n.endswith("/Story Drifts.csv") for n in zf.namelist())


def test_api_moment_curvature_and_stiffness(client):
    c, srv = client
    m = BuildingModel(name="mc")
    for mt in MC._mats().values():
        m.add_material(mt)
    ds = MC._rect()
    from skyframe.core import moment_curvature as mc
    body = {"section": ds.to_dict(), "P": 0.0, "concrete": "simple",
            "steel": "epp", "params": {"eps_c0": MC.E0, "eps_cu": MC.ECU}}
    assert c.post("/api/model", json=m.to_dict()).status_code == 200
    r = c.post("/api/sections/designer/mc", json=body)
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    ref = mc.moment_curvature(ds, srv._state["model"].materials,
                              concrete="simple", steel="epp",
                              params={"eps_c0": MC.E0, "eps_cu": MC.ECU})
    assert j["ultimate"]["M"] == pytest.approx(ref["ultimate"]["M"],
                                               rel=1e-12)
    assert len(j["rows"]) == len(j["points"])
    assert c.post("/api/sections/designer/mc",
                  json={"name": "nope"}).status_code == 400
    assert c.post("/api/sections/designer/mc",
                  json=dict(body, concrete="x")).status_code == 400
    # stiffness preview of a rotated orthotropic section
    tim = Material("TIM", E=12e6, nu=0.3, symmetry="orthotropic",
                   ortho={"E2": 4e6, "G12": 7e5, "G13": 7e5, "G23": 5e5})
    r = c.post("/api/shell_sections/stiffness", json={
        "section": {"material": "TIM", "thickness": 0.2,
                    "material_angle": 30.0},
        "materials": {"TIM": tim.to_dict()}})
    j = r.get_json()
    Qb = aniso.rotate_q(ortho.q_matrix(ortho.constants(tim)), 30.0)
    assert np.allclose(j["A"], 0.2 * Qb) and j["angle"] == 30.0
    assert np.allclose(j["D"], 0.2 ** 3 / 12 * Qb)


def test_db_edit_columns_roundtrip():
    from skyframe.core import modeltables as MT
    m = BuildingModel(name="db")
    m.add_material(Material("TIM", E=12e6, nu=0.3, symmetry="orthotropic",
                            ortho={"E2": 4e6, "G12": 7e5, "G13": 7e5,
                                   "G23": 5e5}))
    m.add_shell_section(ShellSection("S", "TIM", 0.2))
    d0 = m.to_dict()
    cols = {c["key"] for c in MT.get_table(d0, "materials")["columns"]}
    assert {"mat_angle", "aniso_Q16", "aniso_G23"} <= cols
    # identity: rows -> apply leaves the model untouched
    rows = {k: MT.get_table(d0, k)["rows"] for k in ("materials",
                                                     "shell_sections")}
    m1, errs, _ = MT.apply_tables(d0, rows)
    assert errs == [] and m1.to_dict() == d0
    # material angle on the shell section and on the material
    ss = MT.get_table(d0, "shell_sections")["rows"]
    ss[0]["material_angle"] = 30.0
    mt = MT.get_table(d0, "materials")["rows"]
    tim = next(r for r in mt if r["name"] == "TIM")
    tim["mat_angle"] = 15.0
    m2, errs, _ = MT.apply_tables(d0, {"shell_sections": ss,
                                       "materials": mt})
    assert errs == [], errs
    d2 = m2.to_dict()
    assert d2["shell_sections"]["S"]["material_angle"] == 30.0
    assert d2["materials"]["TIM"]["material_angle"] == 15.0
    ss = MT.get_table(d2, "shell_sections")["rows"]
    ss[0]["material_angle"] = None
    m3, errs, _ = MT.apply_tables(d2, {"shell_sections": ss})
    assert errs == [] and "material_angle" not in \
        m3.to_dict()["shell_sections"]["S"]
