"""SkyFrame benchmark pack: CSI ETABS verification examples re-modelled from
the published data, plus closed-form gap cases.

Every case module BUILDS a SkyFrame :class:`~skyframe.core.model.
BuildingModel` from the published inputs (no CSI file is read or shipped),
runs the real engine and returns a :class:`~skyframe.benchmarks._core.
CaseResult` whose rows compare SkyFrame with the independent reference and
with CSI's ETABS value, rated with CSI's acceptance criteria.

Run ``python -m skyframe.benchmarks [--case ID] [--report out_dir]``.
"""

from __future__ import annotations

import importlib
import traceback
from typing import Dict, List, Optional

from ._core import CaseResult

#: case id -> module (import path relative to this package), report order
CASE_MODULES: Dict[str, str] = {
    "EX01": "ex01",
    "EX02": "ex02",
    "EX03": "ex03",
    "EX04": "ex04",
    "EX05": "ex05",
    "EX06": "ex06",
    "EX07": "ex07",
    "EX08": "ex08",
    "EX15": "ex15",
    "EX09": "blocked",
    "EX10": "blocked",
    "EX11": "blocked",
    "EX12": "blocked",
    "EX13": "blocked",
    "EX14": "blocked",
    "GAP-PDELTA": "gap_pdelta",
    "GAP-BUCKLING": "gap_buckling",
    "GAP-PLATE": "gap_plate",
    "GAP-PATCH": "gap_plate",
    "GAP-DIAPHRAGM": "gap_diaphragm",
    "GAP-FPX": "gap_fpx",
}

TIER1 = ("EX01", "EX04", "EX06", "EX08", "EX15")
GAP = ("GAP-PDELTA", "GAP-BUCKLING", "GAP-PLATE", "GAP-PATCH",
       "GAP-DIAPHRAGM", "GAP-FPX")


def case_ids() -> List[str]:
    return list(CASE_MODULES)


def run_case(case_id: str) -> CaseResult:
    """Run one case by id (an exception becomes an ``error`` result)."""
    mod = importlib.import_module(f".{CASE_MODULES[case_id]}", __name__)
    try:
        if hasattr(mod, "run_id"):
            return mod.run_id(case_id)
        return mod.run()
    except Exception as exc:                     # pragma: no cover - report
        return CaseResult(case_id, getattr(mod, "TITLE", case_id),
                          getattr(mod, "TIER", "?"), source="",
                          status="error",
                          error=f"{exc!r}\n{traceback.format_exc()}")


def run_all(ids: Optional[List[str]] = None) -> List[CaseResult]:
    return [run_case(i) for i in (ids or case_ids())]
