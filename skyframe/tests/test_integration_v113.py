"""Cross-feature integration checks for the v1.13 wave.

Features built in parallel are pinned here where they interact:
Set Load Cases to Run x steady-state / PSD load cases.
"""

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.model import MODAL_CASE  # noqa: E402
from test_frequency import _model_with_cases  # noqa: E402


def test_frequency_cases_are_listed_case_kinds():
    kinds = _model_with_cases().case_kinds()
    assert kinds["SS"] == "steady_state"
    assert kinds["PSD"] == "psd"


def test_frequency_cases_run_by_default():
    res = OpenSeesEngine(_model_with_cases()).run().to_dict()
    assert set(res["steady_state"]) == {"SS"}
    assert set(res["psd"]) == {"PSD"}
    assert res["case_status"]["SS"] == "finished"
    assert res["case_status"]["PSD"] == "finished"


def test_not_run_frequency_cases_are_skipped():
    mdl = _model_with_cases()
    mdl.cases_not_run = ["SS", "PSD"]
    mdl.validate()                      # names are accepted, not "unknown"
    res = OpenSeesEngine(mdl).run().to_dict()
    assert "steady_state" not in res and "psd" not in res
    assert res["case_status"]["SS"] == "not_run"
    assert res["case_status"]["PSD"] == "not_run"


def test_frequency_case_runs_modal_as_dependency():
    mdl = _model_with_cases()
    mdl.cases_not_run = [MODAL_CASE, "SS"]
    res = OpenSeesEngine(mdl).run().to_dict()
    assert res["case_status"]["PSD"] == "finished"
    assert res["case_status"][MODAL_CASE] == "run_as_dependency"
    assert res["case_status"]["SS"] == "not_run"


def test_skipping_one_frequency_case_leaves_the_other_identical():
    full = OpenSeesEngine(_model_with_cases()).run().to_dict()
    mdl = _model_with_cases()
    mdl.cases_not_run = ["SS"]
    part = OpenSeesEngine(mdl).run().to_dict()
    assert part["psd"]["PSD"]["rms"] == full["psd"]["PSD"]["rms"]
