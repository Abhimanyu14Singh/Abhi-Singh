"""
Full test suite — run from etabs_dashboard/ directory:
    python run_tests.py
"""
import sys, types, traceback

# Stub Windows-only modules
for name in ("comtypes", "comtypes.client"):
    sys.modules[name] = types.ModuleType(name)

import app as application  # boots Dash, registers all 47 callbacks

# ── Mock ETABS data ───────────────────────────────────────────────────────────
STORIES = [{"name": f"Story{i}", "elevation": i*3.0, "height": 3.0}
           for i in range(1, 6)]
JOINTS  = [{"name": str(i), "x": (i%5)*5.0, "y": (i//5)*5.0, "z": (i//25+1)*3.0}
           for i in range(125)]
FRAMES  = [{"name": str(i), "point_i": str(i), "point_j": str(i+1), "section": "W14x90",
             "xi":(i%5)*5.0,"yi":0.0,"zi":3.0+(i//10)*3,
             "xj":((i+1)%5)*5.0,"yj":0.0,"zj":3.0+(i//10)*3}
           for i in range(60)]
LOAD_CASES   = ["DEAD","LIVE","EX","EY","WX","1.2D+1.6L"]
LOAD_COMBOS  = ["1.2D+1.6L"]
LOAD_PATTERNS = [
    {"name":"DEAD","type_code":1,"type_name":"Dead","self_wt":1.0},
    {"name":"LIVE","type_code":3,"type_name":"Live","self_wt":0.0},
    {"name":"EX",  "type_code":7,"type_name":"Seismic","self_wt":0.0},
    {"name":"EY",  "type_code":7,"type_name":"Seismic","self_wt":0.0},
    {"name":"WX",  "type_code":6,"type_name":"Wind","self_wt":0.0},
]
BASE_RXN = [{"load_case":c,"step_type":"Max","step_num":1,
             "Fx":200*(i+1),"Fy":180*(i+1),"Fz":-9000*(i+1),
             "Mx":6000.0,"My":5000.0,"Mz":400.0}
            for i,c in enumerate(LOAD_CASES)]
STORY_DRIFTS = [
    {"story":s["name"],"load_case":c,"step_type":"Max","step_num":1,
     "direction":d,"drift":0.004*(1+STORIES.index(s)*0.15)*(1+i*0.2),
     "label":"J1","x":0.0,"y":0.0,"z":s["elevation"]}
    for s in STORIES for i,c in enumerate(LOAD_CASES[:4]) for d in ["X","Y"]
]
STORY_FORCES = [
    {"story":s["name"],"load_case":c,"step_type":"Max","step_num":1,"location":"Bottom",
     "Px":0.0,"Py":0.0,"Vx":600*(STORIES.index(s)+1),"Vy":550*(STORIES.index(s)+1),
     "T":120.0,"Mx":2500*(STORIES.index(s)+1),"My":2200*(STORIES.index(s)+1)}
    for s in STORIES for c in LOAD_CASES[:4]
]
MODAL_PERIODS = [{"mode":i+1,"load_case":"MODAL","period":1.4/(i+1),
                  "frequency":(i+1)/1.4,"circ_freq":(i+1)*4.5,"eigenvalue":(i+1)*20.0}
                 for i in range(15)]
MODAL_RATIOS  = [{"mode":i+1,"Ux":max(0,0.40-i*0.03),"Uy":max(0,0.32-i*0.02),
                  "Uz":0.0,"Rx":0.01,"Ry":0.01,"Rz":max(0,0.12-i*0.008),
                  "sum_Ux":min(1.0,sum(max(0,0.40-j*0.03) for j in range(i+1))),
                  "sum_Uy":min(1.0,sum(max(0,0.32-j*0.02) for j in range(i+1))),
                  "sum_Rx":0.0,"sum_Ry":0.0}
                 for i in range(15)]
JOINT_DISP = [{"joint":str(j),"load_case":c,"step_type":"Max",
               "U1":0.05*j*0.01,"U2":0.04*j*0.01,"U3":-0.002,
               "R1":0.0001,"R2":0.0001,"R3":0.0}
              for j in range(30) for c in LOAD_CASES[:3]]

MOCK = {
    "status": "ok",
    "model_info": {
        "filename":"TestBuilding.edb","units":"kN, m, °C","units_code":6,
        "num_stories":5,"num_joints":125,"num_frames":60,"num_shells":20,
        "num_load_cases":6,"num_load_combos":1,
    },
    "stories": STORIES, "joints": JOINTS, "frames": FRAMES, "shells": [],
    "load_cases": LOAD_CASES, "load_combos": LOAD_COMBOS,
    "load_patterns": LOAD_PATTERNS,
    "results": {
        "base_reactions": BASE_RXN, "story_drifts": STORY_DRIFTS,
        "story_forces": STORY_FORCES, "modal_periods": MODAL_PERIODS,
        "modal_mass_ratios": MODAL_RATIOS, "joint_displacements": JOINT_DISP,
    },
}

# ── Test runner ───────────────────────────────────────────────────────────────
passed = failed = 0
failures = []

def test(name, fn):
    global passed, failed
    try:
        r = fn()
        assert r is not None, "returned None"
        passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        failed += 1
        failures.append((name, traceback.format_exc()))
        print(f"  FAIL  {name}: {e}")

# ── Structural pages ──────────────────────────────────────────────────────────
from callbacks.navigation import render_page, update_sidebar_summary
from callbacks.charts.overview_cb import (
    update_kpis, update_model_table, update_story_table,
    update_lc_tables, update_pattern_table, update_composition_chart)
from callbacks.charts.plan_view_cb import populate_story_dropdown, update_plan_view
from callbacks.charts.story_drifts_cb import populate_cases, update_drift_charts
from callbacks.charts.story_forces_cb import populate_sf_cases, update_sf
from callbacks.charts.base_reactions_cb import populate_br_cases, update_br
from callbacks.charts.frame_forces_cb import (
    populate_ff_frames, populate_ff_cases, update_ff_charts)
from callbacks.charts.modal_cb import update_modal
from callbacks.charts.displacements_cb import populate_disp_cases, update_disp
from callbacks.charts.load_patterns_cb import update_load_patterns
from callbacks.charts.torsion_cb import populate_torsion_cases, update_torsion

print("\n══ Structural Callback Tests ════════════════════════════════")
ALL_PATHS = ["/","/overview","/plan-view","/story-drifts","/story-forces",
             "/base-reactions","/frame-forces","/modal","/displacements",
             "/load-patterns","/torsion",
             "/statistics","/heatmaps","/code-checks",
             "/outliers","/correlation","/scorecard"]
for p in ALL_PATHS:
    test(f"page render {p}", lambda p=p: render_page(p))

test("sidebar (attached)",   lambda: update_sidebar_summary(MOCK))
test("sidebar (no data)",    lambda: update_sidebar_summary(None))
test("overview KPIs",        lambda: update_kpis(MOCK))
test("overview model table", lambda: update_model_table(MOCK))
test("overview story table", lambda: update_story_table(MOCK))
test("overview LC tables",   lambda: update_lc_tables(MOCK))
test("overview patterns",    lambda: update_pattern_table(MOCK))
test("overview composition", lambda: update_composition_chart(MOCK, "light"))
test("plan populate",        lambda: populate_story_dropdown(MOCK))
test("plan view Story1",     lambda: update_plan_view("Story1",["frames","joints"], MOCK, "light"))
test("drift populate",       lambda: populate_cases(MOCK))
test("drift charts both",    lambda: update_drift_charts(MOCK,["EX","EY"],"Both",0.025,"light"))
test("drift charts X dark",  lambda: update_drift_charts(MOCK,["EX"],"X",0.020,"dark"))
test("sf populate",          lambda: populate_sf_cases(MOCK))
test("sf charts Vx",         lambda: update_sf(MOCK, ["EX","EY"], "Vx", "light"))
test("br populate",          lambda: populate_br_cases(MOCK))
test("br charts forces",     lambda: update_br(MOCK, LOAD_CASES, ["Fx","Fy","Fz"], "light"))
test("br charts moments",    lambda: update_br(MOCK, LOAD_CASES, ["Mx","My","Mz"], "dark"))
test("ff populate frames",   lambda: populate_ff_frames(MOCK, None))
test("ff populate cases",    lambda: populate_ff_cases(MOCK))
test("ff charts no forces",  lambda: update_ff_charts(None, "EX", "light"))
test("ff charts with data",  lambda: update_ff_charts(
    [{"frame":"1","load_case":"EX","step_type":"Max","station":s*0.5,
      "P":-100.0,"V2":20.0,"V3":5.0,"T":1.0,"M2":10.0,"M3":80.0}
     for s in range(10)], "EX", "light"))
test("modal charts",         lambda: update_modal(MOCK, "light"))
test("modal charts dark",    lambda: update_modal(MOCK, "dark"))
test("disp populate",        lambda: populate_disp_cases(MOCK))
test("disp charts U1",       lambda: update_disp(MOCK, "EX", "U1", "light"))
test("disp charts U3",       lambda: update_disp(MOCK, "DEAD", "U3", "dark"))
test("load patterns light",  lambda: update_load_patterns(MOCK, "Fx", "light"))
test("load patterns dark",   lambda: update_load_patterns(MOCK, "Fy", "dark"))
test("torsion populate",     lambda: populate_torsion_cases(MOCK))
test("torsion light",        lambda: update_torsion(MOCK, "EX", "light"))
test("torsion dark",         lambda: update_torsion(MOCK, "EY", "dark"))
test("torsion no data",      lambda: update_torsion(None, None, "light"))

# ── Analytics ─────────────────────────────────────────────────────────────────
from callbacks.charts.statistics_cb  import update_stats,         populate_stats_cases
from callbacks.charts.heatmaps_cb    import update_heatmaps
from callbacks.charts.code_checks_cb import update_code_checks,   populate_cc_cases
from callbacks.charts.outliers_cb    import update_outliers,       populate_out_cases
from callbacks.charts.correlation_cb import update_correlation
from callbacks.charts.scorecard_cb   import update_scorecard

print("\n══ Analytics Callback Tests ════════════════════════════════")
test("stats populate cases",          lambda: populate_stats_cases(MOCK))
test("stats drift light",             lambda: update_stats(MOCK,"drift",[],"light"))
test("stats drift dark filtered",     lambda: update_stats(MOCK,"drift",["EX"],"dark"))
test("stats Vx",                      lambda: update_stats(MOCK,"Vx",[],"light"))
test("stats My dark",                 lambda: update_stats(MOCK,"Mx",[],"dark"))
test("stats no data",                 lambda: update_stats(None,"drift",[],"light"))

test("heatmap drift_X raw light",     lambda: update_heatmaps(MOCK,"drift_X","YlOrRd","raw","light"))
test("heatmap drift_Y pct dark",      lambda: update_heatmaps(MOCK,"drift_Y","Viridis","pct","dark"))
test("heatmap Vx zscore",             lambda: update_heatmaps(MOCK,"Vx","Plasma","zscore","light"))
test("heatmap Mx raw",                lambda: update_heatmaps(MOCK,"Mx","Blues","raw","light"))
test("heatmap no data",               lambda: update_heatmaps(None,"drift_X","YlOrRd","raw","light"))

test("cc populate cases",             lambda: populate_cc_cases(MOCK))
test("cc steel_mrf metres",           lambda: update_code_checks(MOCK,"steel_mrf","m","DEAD","EX","light"))
test("cc conc_mrf feet dark",         lambda: update_code_checks(MOCK,"conc_mrf","ft","DEAD","EY","dark"))
test("cc other no cases",             lambda: update_code_checks(MOCK,"other","m",None,None,"light"))
test("cc no data",                    lambda: update_code_checks(None,"other","m",None,None,"light"))

test("outliers populate cases",       lambda: populate_out_cases(MOCK))
test("outliers drift 2σ light",       lambda: update_outliers(MOCK,"drift",2.0,[],"light"))
test("outliers Vx 1.5σ dark",         lambda: update_outliers(MOCK,"Vx",1.5,[],"dark"))
test("outliers Fx reactions",         lambda: update_outliers(MOCK,"Fx",2.0,[],"light"))
test("outliers Vy 3σ",                lambda: update_outliers(MOCK,"Vy",3.0,[],"light"))
test("outliers no data",              lambda: update_outliers(None,"drift",2.0,[],"light"))

test("correlation full metrics light",lambda: update_correlation(
    MOCK,["max_drift_X","max_drift_Y","max_Vx","max_Vy","base_Fx","base_Fy"],
    "max_drift_X","light"))
test("correlation fewer metrics dark",lambda: update_correlation(
    MOCK,["max_drift_X","max_Vx"],"max_drift_X","dark"))
test("correlation no data",           lambda: update_correlation(None,[],"max_drift_X","light"))

test("scorecard 2.5% other m light",  lambda: update_scorecard(MOCK,0.025,"other","m","light"))
test("scorecard 2.0% steel m dark",   lambda: update_scorecard(MOCK,0.020,"steel_mrf","m","dark"))
test("scorecard 0.4% IS 1893",        lambda: update_scorecard(MOCK,0.004,"conc_mrf","m","light"))
test("scorecard ft units",            lambda: update_scorecard(MOCK,0.025,"other","ft","light"))
test("scorecard no data",             lambda: update_scorecard(None,0.025,"other","m","light"))

# ── Summary ───────────────────────────────────────────────────────────────────
total = passed + failed
print(f"\n══ RESULTS: {passed}/{total} passed, {failed} failed ══")
if failures:
    print("\n── Failure Details ──")
    for name, tb in failures:
        print(f"\n❌ [{name}]\n{tb}")
sys.exit(0 if failed == 0 else 1)
