"""ETABS verification suite (verification/etabs): every case's SkyFrame
results against its hand calculations, and against the stored ETABS 23
results once they exist (results/etabs-23/<id>.etabs.json)."""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "verification" / "etabs"
sys.path.insert(0, str(HERE))
import cases as C          # noqa: E402
import compare as CMP      # noqa: E402
import skyframe_side as S  # noqa: E402


@pytest.mark.parametrize("cid", sorted(C.CASES))
def test_case_against_hand_and_etabs(cid):
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
