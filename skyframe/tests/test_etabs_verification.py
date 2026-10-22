"""ETABS verification suite (verification/etabs): every case's SkyFrame
results against its hand calculations, and against the stored ETABS 23
results once they exist (results/etabs-23/<id>.etabs.json)."""

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "verification" / "etabs"
sys.path.insert(0, str(HERE))
import cases as C          # noqa: E402
import compare as CMP      # noqa: E402
import skyframe_side as S  # noqa: E402

# Cases marked "slow" in their definition (the SP2/SP3 speed benchmarks, the R1 real projects) take
# minutes to run: SkyFrame runs them only with SKYFRAME_SLOW=1.  Without it, their definitions are
# still checked (test_slow_definitions_build below).
SLOW = {cid for cid in C.CASES if C.get(cid).get("slow")}
RUN_SLOW = os.environ.get("SKYFRAME_SLOW") == "1"


@pytest.mark.parametrize("cid", sorted(C.CASES))
def test_case_against_hand_and_etabs(cid):
    if cid in SLOW and not RUN_SLOW:
        pytest.skip("slow case: set SKYFRAME_SLOW=1 to run it")
    case = C.get(cid)
    stored = json.loads((HERE / "cases" / f"{cid}.json").read_text())
    assert stored == json.loads(json.dumps(case)), "cases/*.json is stale: run python cases.py"
    sky = S.run(case)
    ef = CMP.ETABS_DIR / f"{cid}.etabs.json"
    etabs = json.loads(ef.read_text()) if ef.exists() else None
    rows = CMP.compare(case, sky, etabs)
    bad = [r for r in rows if r["sky_vs_hand"] not in ("exact", "acceptable", "no hand value", "known difference")
           or r["sky_vs_etabs"] == "fails"]
    assert not bad, bad


@pytest.mark.parametrize("cid", sorted(SLOW))
def test_slow_definitions_build(cid):
    """The light check for slow cases: the stored JSON is current and the definition is consistent
    (a SkyFrame model builds from it for a benchmark; the extension has a run hook otherwise)."""
    case = C.get(cid)
    stored = json.loads((HERE / "cases" / f"{cid}.json").read_text())
    assert stored == json.loads(json.dumps(case)), "cases/*.json is stale: run python cases.py"
    if case.get("benchmark"):
        m = S.build(case, only=set(case["benchmark"]["linear"]))
        assert len(m.members) == len(case["frames"])
    else:
        assert hasattr(S._ext(case), "run")
