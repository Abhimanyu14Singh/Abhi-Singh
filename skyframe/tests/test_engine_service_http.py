"""The local engine service: version, browser checks, a solve job end to end, cancel."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from skyframe.engine_service.service import serve
from skyframe.engine_service.supa import CASES

SUPA = "https://supa.example.com"


@pytest.fixture(scope="module")
def base():
    httpd = serve(0, [SUPA])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def call(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, dict(r.headers), (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, dict(e.headers), (json.loads(raw) if raw else None)


def cantilever(P=10.0):
    E = 29000 * 144
    m = dict(xyz=[0, 0, 0, 0, 0, 12], fixed=[True, False],
             els=[dict(i=0, j=1, E=E, G=11200 * 144, A=0.1, Iy=0.01, Iz=0.01, J=0.001, yh=[1, 0, 0],
                       ri=False, rj=False, src="col", kind="frame", q={})],
             dia=[], nodeLoads={c: [0.0] * 6 for c in CASES}, diaLoads={c: [] for c in CASES}, notes=[])
    m["nodeLoads"]["Wx"][3] = P
    return m


def test_version(base):
    code, _, v = call(base + "/engine/version")
    assert code == 200 and v["api"] and "supa" in v["jobs"]


def test_private_network_preflight_for_an_allowed_page(base):
    code, h, _ = call(base + "/engine/supa/solve", "OPTIONS",
                      headers={"Origin": SUPA, "Access-Control-Request-Method": "POST",
                               "Access-Control-Request-Private-Network": "true"})
    assert code == 204
    assert h["Access-Control-Allow-Origin"] == SUPA
    assert h["Access-Control-Allow-Private-Network"] == "true"


def test_other_websites_are_refused(base):
    code, _, b = call(base + "/engine/version", headers={"Origin": "https://evil.example"})
    assert code == 403 and "not allowed" in b["error"]
    code, _, _ = call(base + "/engine/supa/solve", "OPTIONS", headers={"Origin": "https://evil.example"})
    assert code == 403


def test_bad_model_is_refused_before_queueing(base):
    code, _, b = call(base + "/engine/supa/solve", "POST", {"xyz": [0, 0]})
    assert code == 400 and "FemModel has no" in b["error"]


def test_solve_job_end_to_end(base):
    code, h, j = call(base + "/engine/supa/solve", "POST", cantilever(), headers={"Origin": SUPA})
    assert code == 202 and h["Access-Control-Allow-Origin"] == SUPA
    jid, after, steps = j["job"], 0, []
    t0 = time.time()
    while time.time() - t0 < 120:
        _, _, s = call(f"{base}/engine/jobs/{jid}?after={after}&wait=10")
        steps += [x["step"] for x in s["steps"]]
        after = s["next"]
        if s["state"] in ("done", "failed", "cancelled"):
            break
    assert s["state"] == "done", s
    assert "Factoring" in steps
    code, _, r = call(f"{base}/engine/jobs/{jid}/result")
    assert code == 200
    assert abs(r["pieces"]["col"]["Wx"]["M"] - 120) < 0.12


def test_cancel(base):
    _, _, j = call(base + "/engine/supa/solve", "POST", cantilever())
    code, _, b = call(f"{base}/engine/jobs/{j['job']}/cancel", "POST", {})
    assert code == 200 and b["ok"]
    t0 = time.time()
    while time.time() - t0 < 60:
        _, _, s = call(f"{base}/engine/jobs/{j['job']}?wait=5")
        if s["state"] in ("done", "cancelled", "failed"):
            break
    assert s["state"] in ("cancelled", "done")      # a very fast solve may finish first
    code, _, _ = call(f"{base}/engine/jobs/nope/result")
    assert code == 404
