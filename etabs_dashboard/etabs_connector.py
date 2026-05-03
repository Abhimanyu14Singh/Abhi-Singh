"""
ETABS v23 API Connector
Connects to a running ETABS instance and extracts all structural data.
"""
import sys
import os
import traceback
import pandas as pd
import numpy as np

ETABS_PATH = r"C:\Program Files\Computers and Structures\ETABS 23"

UNITS_MAP = {
    1: "lb, in, °F",  2: "lb, ft, °F",  3: "kip, in, °F", 4: "kip, ft, °F",
    5: "kN, mm, °C",  6: "kN, m, °C",   7: "kgf, mm, °C", 8: "kgf, m, °C",
    9: "N, mm, °C",  10: "N, m, °C",   11: "Tonf, mm, °C",12: "Tonf, m, °C",
   13: "kN, cm, °C", 14: "kgf, cm, °C",15: "N, cm, °C",  16: "Tonf, cm, °C",
}

LOAD_TYPE_MAP = {
    1:"Dead", 2:"Super Dead", 3:"Live", 4:"Roof Live", 5:"Snow",
    6:"Wind", 7:"Seismic", 8:"Wind on Live", 9:"UBC97 Seismic",
   10:"Other", 11:"Move", 12:"Temperature", 13:"RoofDrain",
   14:"Notional", 15:"PatternLive", 16:"Wave", 17:"Braking",
   18:"Prestress", 19:"Hyperstatic",
}

_sap_model = None


def get_etabs_model():
    """Attach to a running ETABS v23 instance. Returns (SapModel, error_msg)."""
    global _sap_model
    try:
        if ETABS_PATH not in sys.path:
            sys.path.insert(0, ETABS_PATH)
        import ETABSv1
        helper = ETABSv1.cHelper(ETABSv1.Helper())
        myETABS = helper.GetObject("CSI.ETABS.API.ETABSObject")
        _sap_model = myETABS.SapModel
        return _sap_model, None
    except Exception:
        pass

    # Fallback: comtypes
    try:
        import comtypes.client
        helper = comtypes.client.CreateObject("ETABSv1.Helper")
        import comtypes.gen.ETABSv1 as ETABSv1
        helper = helper.QueryInterface(ETABSv1.cHelper)
        myETABS = helper.GetObject("CSI.ETABS.API.ETABSObject")
        _sap_model = myETABS.SapModel
        return _sap_model, None
    except Exception as e:
        return None, f"Could not connect to ETABS: {str(e)}\n\nMake sure ETABS v23 is open with a model loaded."


def _safe(call, default=None):
    """Execute an ETABS API call and return result or default on failure."""
    try:
        result = call()
        if isinstance(result, tuple) and result[0] != 0:
            return default
        return result
    except Exception:
        return default


def extract_all_data(SapModel):
    """
    Extract all structural data from ETABS model.
    Returns a dict suitable for JSON / dcc.Store.
    """
    data = {}
    try:
        data["model_info"]     = _extract_model_info(SapModel)
        data["stories"]        = _extract_stories(SapModel)
        data["joints"]         = _extract_joints(SapModel)
        data["frames"]         = _extract_frames(SapModel)
        data["shells"]         = _extract_shells(SapModel)
        data["load_cases"]     = _extract_load_cases(SapModel)
        data["load_patterns"]  = _extract_load_patterns(SapModel)
        data["load_combos"]    = _extract_load_combos(SapModel)
        data["results"]        = _extract_all_results(SapModel)
        data["status"]         = "ok"
    except Exception as e:
        data["status"] = f"error: {traceback.format_exc()}"
    return data


# ─── Model Info ──────────────────────────────────────────────────────────────

def _extract_model_info(SapModel):
    ret = SapModel.GetModelFilename(False)
    filename = os.path.basename(ret[1]) if isinstance(ret, tuple) else "Unknown"
    units_code = SapModel.GetPresentUnits()
    units_str  = UNITS_MAP.get(units_code, f"Units code {units_code}")

    _, nj, _ = SapModel.PointObj.GetNameList()
    _, nf, _ = SapModel.FrameObj.GetNameList()
    try:
        _, na, _ = SapModel.AreaObj.GetNameList()
    except Exception:
        na = 0
    try:
        _, ns, _ = SapModel.Story.GetNameList()
    except Exception:
        ns = 0
    try:
        _, nlc, _ = SapModel.LoadCases.GetNameList()
    except Exception:
        nlc = 0
    try:
        _, nco, _ = SapModel.LoadCombos.GetNameList()
    except Exception:
        nco = 0

    return {
        "filename":    filename,
        "units":       units_str,
        "units_code":  units_code,
        "num_stories": ns,
        "num_joints":  nj,
        "num_frames":  nf,
        "num_shells":  na,
        "num_load_cases":   nlc,
        "num_load_combos":  nco,
    }


# ─── Geometry ────────────────────────────────────────────────────────────────

def _extract_stories(SapModel):
    try:
        ret, num, names = SapModel.Story.GetNameList()
        stories = []
        for name in names:
            r2, elev   = SapModel.Story.GetElevation(name)
            r3, height = SapModel.Story.GetHeight(name)
            stories.append({
                "name":      name,
                "elevation": round(elev,   4) if r2 == 0 else 0.0,
                "height":    round(height, 4) if r3 == 0 else 0.0,
            })
        return stories
    except Exception:
        return []


def _extract_joints(SapModel):
    try:
        _, _, names = SapModel.PointObj.GetNameList()
        joints = []
        for name in names:
            r, x, y, z, _ = SapModel.PointObj.GetCoordCartesian(name)
            if r == 0:
                joints.append({"name": name,
                                "x": round(x, 4),
                                "y": round(y, 4),
                                "z": round(z, 4)})
        return joints
    except Exception:
        return []


def _extract_frames(SapModel):
    try:
        _, _, names = SapModel.FrameObj.GetNameList()
        # Build joint lookup
        joint_coords = {}
        for j in _extract_joints(SapModel):
            joint_coords[j["name"]] = (j["x"], j["y"], j["z"])

        frames = []
        for name in names:
            r, pi, pj = SapModel.FrameObj.GetPoints(name)
            if r != 0:
                continue
            r2, section, _ = SapModel.FrameObj.GetSection(name)
            section = section if r2 == 0 else ""
            xi, yi, zi = joint_coords.get(pi, (0, 0, 0))
            xj, yj, zj = joint_coords.get(pj, (0, 0, 0))
            frames.append({
                "name":    name,
                "point_i": pi,
                "point_j": pj,
                "section": section,
                "xi": round(xi, 4), "yi": round(yi, 4), "zi": round(zi, 4),
                "xj": round(xj, 4), "yj": round(yj, 4), "zj": round(zj, 4),
            })
        return frames
    except Exception:
        return []


def _extract_shells(SapModel):
    try:
        _, _, names = SapModel.AreaObj.GetNameList()
        joint_coords = {}
        for j in _extract_joints(SapModel):
            joint_coords[j["name"]] = (j["x"], j["y"], j["z"])

        shells = []
        for name in names:
            r, num_pts, pts = SapModel.AreaObj.GetPoints(name)
            if r != 0:
                continue
            coords = [list(joint_coords.get(p, (0, 0, 0))) for p in pts]
            shells.append({"name": name, "points": list(pts), "coords": coords})
        return shells
    except Exception:
        return []


# ─── Load Cases / Patterns / Combos ─────────────────────────────────────────

def _extract_load_cases(SapModel):
    try:
        _, _, names = SapModel.LoadCases.GetNameList()
        return list(names) if names else []
    except Exception:
        return []


def _extract_load_patterns(SapModel):
    try:
        _, _, names = SapModel.LoadPatterns.GetNameList()
        patterns = []
        for name in names:
            r, ltype, _ = SapModel.LoadPatterns.GetLoadType(name)
            type_name = LOAD_TYPE_MAP.get(ltype, "Other") if r == 0 else "Other"
            r2, sf = SapModel.LoadPatterns.GetSelfWtMultiplier(name)
            patterns.append({
                "name":      name,
                "type_code": ltype if r == 0 else 0,
                "type_name": type_name,
                "self_wt":   round(sf, 3) if r2 == 0 else 0.0,
            })
        return patterns
    except Exception:
        return []


def _extract_load_combos(SapModel):
    try:
        _, _, names = SapModel.LoadCombos.GetNameList()
        return list(names) if names else []
    except Exception:
        return []


# ─── Results ─────────────────────────────────────────────────────────────────

def _select_all_cases(SapModel, cases, combos):
    SapModel.Results.Setup.DeselectAllCasesAndCombosForOutput()
    for c in cases:
        SapModel.Results.Setup.SetCaseSelectedForOutput(c)
    for c in combos:
        SapModel.Results.Setup.SetComboSelectedForOutput(c)


def _extract_all_results(SapModel):
    results = {}
    cases  = _extract_load_cases(SapModel)
    combos = _extract_load_combos(SapModel)
    _select_all_cases(SapModel, cases, combos)

    results["base_reactions"]      = _get_base_reactions(SapModel)
    results["story_drifts"]        = _get_story_drifts(SapModel)
    results["story_forces"]        = _get_story_forces(SapModel)
    results["modal_periods"]       = _get_modal_periods(SapModel)
    results["modal_mass_ratios"]   = _get_modal_mass_ratios(SapModel)
    results["joint_displacements"] = _get_joint_displacements(SapModel)
    return results


def _get_base_reactions(SapModel):
    try:
        r, num, lc, st, sn, Fx, Fy, Fz, Mx, My, Mz, gx, gy, gz = SapModel.Results.BaseReact()
        if r != 0 or num == 0:
            return []
        rows = []
        for i in range(num):
            rows.append({
                "load_case":  lc[i],
                "step_type":  st[i],
                "step_num":   sn[i],
                "Fx": round(Fx[i], 4), "Fy": round(Fy[i], 4), "Fz": round(Fz[i], 4),
                "Mx": round(Mx[i], 4), "My": round(My[i], 4), "Mz": round(Mz[i], 4),
            })
        return rows
    except Exception:
        return []


def _get_story_drifts(SapModel):
    try:
        r, num, story, lc, st, sn, direction, drift, label, x, y, z = SapModel.Results.StoryDrifts()
        if r != 0 or num == 0:
            return []
        rows = []
        for i in range(num):
            rows.append({
                "story":      story[i],
                "load_case":  lc[i],
                "step_type":  st[i],
                "step_num":   sn[i],
                "direction":  direction[i],
                "drift":      round(float(drift[i]), 6),
                "label":      label[i],
                "x":          round(float(x[i]), 4),
                "y":          round(float(y[i]), 4),
                "z":          round(float(z[i]), 4),
            })
        return rows
    except Exception:
        return []


def _get_story_forces(SapModel):
    try:
        r, num, story, lc, st, sn, loc, Px, Py, Vx, Vy, T, Mx, My = SapModel.Results.StoryForces()
        if r != 0 or num == 0:
            return []
        rows = []
        for i in range(num):
            rows.append({
                "story":     story[i],
                "load_case": lc[i],
                "step_type": st[i],
                "step_num":  sn[i],
                "location":  loc[i],
                "Px":  round(float(Px[i]), 4),  "Py":  round(float(Py[i]), 4),
                "Vx":  round(float(Vx[i]), 4),  "Vy":  round(float(Vy[i]), 4),
                "T":   round(float(T[i]),  4),
                "Mx":  round(float(Mx[i]), 4),   "My":  round(float(My[i]), 4),
            })
        return rows
    except Exception:
        return []


def _get_modal_periods(SapModel):
    try:
        r, num, lc, st, sn, period, freq, cfreq, eigen = SapModel.Results.ModalPeriod()
        if r != 0 or num == 0:
            return []
        rows = []
        for i in range(num):
            rows.append({
                "mode":       i + 1,
                "load_case":  lc[i],
                "period":     round(float(period[i]), 6),
                "frequency":  round(float(freq[i]),   4),
                "circ_freq":  round(float(cfreq[i]),  4),
                "eigenvalue": round(float(eigen[i]),  4),
            })
        return rows
    except Exception:
        return []


def _get_modal_mass_ratios(SapModel):
    try:
        r, num, lc, st, sn, ux, uy, uz, sux, suy, suz, rx, ry, rz, srx, sry, srz = \
            SapModel.Results.ModalParticipatingMassRatios()
        if r != 0 or num == 0:
            return []
        rows = []
        for i in range(num):
            rows.append({
                "mode":   i + 1,
                "Ux": round(float(ux[i]),  4),  "Uy": round(float(uy[i]),  4),  "Uz": round(float(uz[i]),  4),
                "Rx": round(float(rx[i]),  4),  "Ry": round(float(ry[i]),  4),  "Rz": round(float(rz[i]),  4),
                "sum_Ux": round(float(sux[i]), 4), "sum_Uy": round(float(suy[i]), 4),
                "sum_Rx": round(float(srx[i]), 4), "sum_Ry": round(float(sry[i]), 4),
            })
        return rows
    except Exception:
        return []


def _get_joint_displacements(SapModel):
    """Get max joint displacement per story per load case (sampled, not all joints)."""
    try:
        _, _, joint_names = SapModel.PointObj.GetNameList()
        # Sample up to 200 joints to keep data size manageable
        sample = list(joint_names)
        if len(sample) > 200:
            idx = np.linspace(0, len(sample) - 1, 200, dtype=int)
            sample = [sample[i] for i in idx]

        rows = []
        for jname in sample:
            try:
                r, num, obj, elm, lc, st, sn, U1, U2, U3, R1, R2, R3 = \
                    SapModel.Results.JointDispl(jname, 0)
                if r != 0 or num == 0:
                    continue
                for i in range(num):
                    rows.append({
                        "joint":     jname,
                        "load_case": lc[i],
                        "step_type": st[i],
                        "U1": round(float(U1[i]), 6), "U2": round(float(U2[i]), 6),
                        "U3": round(float(U3[i]), 6), "R1": round(float(R1[i]), 6),
                        "R2": round(float(R2[i]), 6), "R3": round(float(R3[i]), 6),
                    })
            except Exception:
                continue
        return rows
    except Exception:
        return []


def get_frame_forces(frame_name):
    """On-demand: get frame forces for a single element. Called from callback."""
    if _sap_model is None:
        return []
    try:
        SapModel = _sap_model
        cases  = _extract_load_cases(SapModel)
        combos = _extract_load_combos(SapModel)
        _select_all_cases(SapModel, cases, combos)
        r, num, obj, elm, lc, st, sn, station, P, V2, V3, T, M2, M3 = \
            SapModel.Results.FrameForce(frame_name, 0)
        if r != 0 or num == 0:
            return []
        rows = []
        for i in range(num):
            rows.append({
                "frame":     frame_name,
                "load_case": lc[i],
                "step_type": st[i],
                "station":   round(float(station[i]), 4),
                "P":  round(float(P[i]),  4),
                "V2": round(float(V2[i]), 4), "V3": round(float(V3[i]), 4),
                "T":  round(float(T[i]),  4),
                "M2": round(float(M2[i]), 4), "M3": round(float(M3[i]), 4),
            })
        return rows
    except Exception:
        return []
