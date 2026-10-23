"""ETABS side of the dynamics extension group (cases_x_dyn.py, H1-H4).  Runs on the ETABS machine
(Python + comtypes); ``run_etabs.py`` downloads it next to itself and passes itself in as ``R``.

ETABS 23's API sets almost nothing on the cases these models need (DirHistNonlinear and
ModHistNonlinear expose no setters on the user's machine; ResponseSpectrum has no SetDirComb), so each
builder uses the API only where ``hasattr`` finds every method it needs, and otherwise adds the case as
new rows of its "Load Case Definitions - ..." database table (the way the N1 buckling case was made).
Every value set is read back.  Where neither works the run stops with the methods and the table's
fields, so the next run can be fixed.

    dynTH      nonlinear direct integration (H1) or FNA (H2), load pattern x time function
    dampers    linear viscous damper links (PropLink.SetDamper, LinkObj.AddByCoord)
    ritzCases  Ritz modal cases (H3)
    rsMulti    spectrum cases with two loads and a directional combination (H4)
"""

from __future__ import annotations

import math


# ── database tables ─────────────────────────────────────────────────────────
def _norm(s) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def _col(low: list, cands: str):
    """Index of the field matching one of ``cands`` ("a|b|c"): exact first, then contained."""
    cs = [_norm(c) for c in cands.split("|")]
    for c in cs:
        for i, f in enumerate(low):
            if f == c:
                return i
    for c in cs:
        if len(c) >= 4:
            for i, f in enumerate(low):
                if c in f:
                    return i
    return None


def _table(sm, R, *alternatives) -> str:
    """The first table key containing every word of one of the alternatives (tuples of words)."""
    keys = R.table_keys(sm)
    for words in alternatives:
        for k in keys:
            if all(w.lower() in k.lower() for w in words):
                return k
    return ""


def _no_table(sm, R, what: str) -> None:
    keys = [k for k in R.table_keys(sm) if "case" in k.lower()]
    raise R.Stop(f"{what}: no load-case table found; load-case tables offered: {', '.join(keys)[:1500]}")


def _read(sm, R, key: str):
    got = sm.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])
    R.ok(got, f"open table {key}")
    return got[0], list(got[1]), int(got[2]), list(got[3])


def _rows_of(sm, R, key: str, name: str) -> list:
    """The rows of ``key`` whose first field is ``name``, as {field: value}."""
    _, fields, n, data = _read(sm, R, key)
    nf = len(fields)
    return [dict(zip(fields, data[k * nf:(k + 1) * nf])) for k in range(n)
            if str(data[k * nf]).lower() == name.lower()]


def _apply(sm, R, key: str) -> None:
    r = sm.DatabaseTables.ApplyEditedTables(True, 0, 0, 0, 0, "")
    R.ok(r, f"apply table {key}")
    if r[0] or r[1]:                                          # fatal errors, errors
        try:
            sm.DatabaseTables.CancelTableEditing()
        except Exception:
            pass
        raise R.Stop(f"table {key}: {r[0]} fatal errors, {r[1]} errors: {str(r[4])[:600]}")
    if str(r[4]).strip():
        R.say(f"    ETABS on table {key}: {str(r[4]).strip()[:300]}")


def _add_rows(sm, R, key: str, rows: list, required: tuple) -> dict:
    """Append ``rows`` ({candidate fields "a|b": value}, all rows with the same keys) to table ``key``.
    Candidates in ``required`` must match a field; the others are set when the table has them.
    Returns {candidates: field name} of what was set."""
    version, fields, n, data = _read(sm, R, key)
    low = [_norm(f) for f in fields]
    cols, missing = {}, []
    for cands in rows[0]:
        i = _col(low, cands)
        if i is None:
            if cands in required:
                missing.append(cands)
        else:
            cols[cands] = i
    if missing:
        raise R.Stop(f"table {key}: no field like {missing} (fields: {', '.join(fields)})")
    for row in rows:
        rec = [""] * len(fields)
        for cands, i in cols.items():
            rec[i] = str(row[cands])
        data += rec
    R.ok(sm.DatabaseTables.SetTableForEditingArray(key, version, fields, n + len(rows), data), f"edit table {key}")
    _apply(sm, R, key)
    return {c: fields[i] for c, i in cols.items()}


def _verify(sm, R, key: str, name: str, setf: dict, want: dict) -> list:
    """Read the case's rows back and compare the fields in ``want`` ({candidates: value}); returns the
    rows.  A number must match to 1e-9 (relative), a string ignoring case and spaces."""
    rows = _rows_of(sm, R, key, name)
    if not rows:
        raise R.Stop(f"table {key}: case {name} is not there after the edit")
    for row in rows:
        for cands, v in want.items():
            f = setf.get(cands)
            if f is None:
                continue
            got = row.get(f, "")
            try:
                same = abs(float(got) - float(v)) <= 1e-9 * max(1.0, abs(float(v)))
            except (TypeError, ValueError):
                same = _norm(got) == _norm(v)
            if not same:
                raise R.Stop(f"table {key}: case {name} reads back {f} = {got!r}, set {v!r} (row {row})")
    R.say(f"    {name} read back from {key}: " + "; ".join(
        ", ".join(f"{k}={v}" for k, v in r.items() if str(v).strip() and k.upper() != "GUID") for r in rows)[:900])
    return rows


# ── time histories (H1 nonlinear direct integration, H2 FNA) ─────────────────
_STEP_COLS = ("outsteps|numoutsteps|numsteps|numberofsteps|nsteps|outputsteps",
              "stepsize|outstepsize|outputstepsize|timestep|dt")


def _th_rows(th: dict, extra: dict) -> list:
    rows = []
    for ld in th["loads"]:
        row = {"name": th["name"], "loadtype": "Load Pattern", "loadname": ld["pattern"],
               "function|funcname|func|timefunction": ld["function"], "loadsf|scalefactor|sf": float(ld["scale"]),
               "timefactor|timesf": 1.0, "arrivaltime|arrival": 0.0,
               _STEP_COLS[0]: int(th["steps"]), _STEP_COLS[1]: float(th["dt"])}
        row.update(extra)
        rows.append(row)
    return rows


def _api_loads(obj, th: dict):
    n = len(th["loads"])
    return obj.SetLoads(th["name"], n, ["Load"] * n, [ld["pattern"] for ld in th["loads"]],
                        [ld["function"] for ld in th["loads"]], [float(ld["scale"]) for ld in th["loads"]],
                        [1.0] * n, [0.0] * n, ["Global"] * n, [0.0] * n)


def _damp_tables(sm, R) -> dict:
    """Every table whose key contains "damp", with its fields (logged: ETABS 23 keeps case damping in
    tables of its own, e.g. "Load Case Definitions - Damping - Modal")."""
    out = {}
    for k in R.table_keys(sm):
        if "damp" in k.lower():
            try:
                out[k] = _read(sm, R, k)[1]
            except Exception as exc:                  # informative only
                out[k] = [f"(fields not read: {exc})"]
            R.say(f"    damping table {k}: fields {', '.join(out[k])}")
    return out


def _set_case_row(sm, R, key: str, name: str, values: dict, required: tuple):
    """Set ``values`` ({candidate fields "a|b": value}) on case ``name``'s rows of ``key`` (first field =
    case name), adding a row when the case has none.  Returns ({candidates: field}, whether added)."""
    version, fields, n, data = _read(sm, R, key)
    low, nf = [_norm(f) for f in fields], len(fields)
    cols = {c: _col(low, c) for c in values}
    missing = [c for c in required if cols.get(c) is None]
    if missing:
        raise R.Stop(f"table {key}: no field like {missing} (fields: {', '.join(fields)})")
    rows = [k for k in range(n) if str(data[k * nf]).lower() == name.lower()]
    added = not rows
    if added:
        data += [name] + [""] * (nf - 1)
        rows, n = [n], n + 1
    for k in rows:
        for c, i in cols.items():
            if i is not None:
                data[k * nf + i] = str(values[c])
    R.ok(sm.DatabaseTables.SetTableForEditingArray(key, version, fields, n, data), f"edit table {key}")
    _apply(sm, R, key)
    return {c: fields[i] for c, i in cols.items() if i is not None}, added


def _zero_fields(fields: list) -> dict:
    """Damping fields to set to 0 for no viscous damping: proportional coefficients and damping ratios
    (periods / frequencies and type fields are left alone).  Keys are the exact field names."""
    out = {}
    for f in fields:
        n = _norm(f)
        if "coef" in n or "ratio" in n or (n.startswith("damp") and n[-1:].isdigit()):
            out[f] = 0.0
    return out


def set_direct_nonlinear(sm, R, th: dict) -> str:
    """Nonlinear direct-integration history, no viscous damping (mass and stiffness coefficients 0),
    Hilber-Hughes-Taylor alpha 0 (= Newmark average acceleration)."""
    nm, dh = th["name"], sm.LoadCases.DirHistNonlinear
    if all(hasattr(dh, m) for m in ("SetCase", "SetLoads", "SetTimeStep", "SetDampProportional")):
        R.ok(dh.SetCase(nm), f"{nm} case")
        R.ok(_api_loads(dh, th), f"{nm} loads")
        R.ok(dh.SetTimeStep(nm, int(th["steps"]), float(th["dt"])), f"{nm} time steps")
        R.ok(dh.SetDampProportional(nm, 1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), f"{nm} damping 0")
        if hasattr(dh, "SetTimeIntegration"):
            R.ok(dh.SetTimeIntegration(nm, 4, 0.0, 0.25, 0.5, 1.0, 0), f"{nm} HHT alpha 0")
        if hasattr(dh, "GetDampProportional"):
            got = dh.GetDampProportional(nm, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            R.ok(got, f"read back {nm} damping")
            if abs(float(got[1])) > 1e-12 or abs(float(got[2])) > 1e-12:
                raise R.Stop(f"{nm}: damping coefficients read back {list(got[:3])}, set 0")
        return "API (LoadCases.DirHistNonlinear)"
    # ETABS 23: "Load Case Definitions - Time History - Nonlin Direct Integration" ("Nonlin", not "Nonlinear")
    key = _table(sm, R, ("load case", "nonlin", "direct"))
    if not key:
        _no_table(sm, R, f"nonlinear direct-integration case {nm} (DirHistNonlinear offers: {R._methods(dh)})")
    fields = _read(sm, R, key)[1]
    R.say(f"    table {key}: fields {', '.join(fields)}")
    damps = _damp_tables(sm, R)
    zero = _zero_fields(fields)                    # damping in the case's own table, if it is there
    rows = _th_rows(th, dict(zero, **{"hhtalpha|alpha": 0.0}))
    required = ("name", "loadtype", "loadname", "function|funcname|func|timefunction", "loadsf|scalefactor|sf",
                *_STEP_COLS)
    setf = _add_rows(sm, R, key, rows, required)
    _verify(sm, R, key, nm, setf, dict(zero, **{_STEP_COLS[0]: th["steps"], _STEP_COLS[1]: th["dt"]}))
    if zero:
        return f"table {key}, damping fields {list(zero)} = 0"
    # else a damping table of its own: proportional (mass / stiffness) damping, not modal or hysteretic
    dkey = next((k for words in (("proportional",), ("direct",), ("nonlin",))
                 for k in damps if all(w in k.lower() for w in words)), "")
    if not dkey:
        raise R.Stop(f"{nm}: no damping fields in {key} ({', '.join(fields)}) and no proportional damping table; "
                     f"damping tables: {damps}")
    dzero = _zero_fields(damps[dkey])
    if not dzero:
        raise R.Stop(f"{nm}: table {dkey} has no coefficient or ratio field to zero (fields {damps[dkey]})")
    dset, added = _set_case_row(sm, R, dkey, nm, dzero, tuple(dzero))
    _verify(sm, R, dkey, nm, dset, dzero)
    return f"table {key}; damping {list(dzero)} = 0 in {dkey} ({'row added' if added else 'row edited'})"


def set_fna(sm, R, case: dict, th: dict) -> str:
    """Nonlinear modal history (FNA) on the case's modal case, constant modal damping."""
    nm, mh = th["name"], sm.LoadCases.ModHistNonlinear
    zeta = float(th["damping"]["zeta"])
    modal = th.get("modalCase") or case["modal"]["case"]
    if all(hasattr(mh, m) for m in ("SetCase", "SetLoads", "SetModalCase", "SetTimeStep", "SetDampConstant")):
        R.ok(mh.SetCase(nm), f"{nm} case")
        R.ok(mh.SetModalCase(nm, modal), f"{nm} modal case")
        R.ok(_api_loads(mh, th), f"{nm} loads")
        R.ok(mh.SetTimeStep(nm, int(th["steps"]), float(th["dt"])), f"{nm} time steps")
        R.ok(mh.SetDampConstant(nm, zeta), f"{nm} damping")
        if hasattr(mh, "GetDampConstant"):
            got = mh.GetDampConstant(nm, 0.0)
            R.ok(got, f"read back {nm} damping")
            if abs(float(got[0]) - zeta) > 1e-12:
                raise R.Stop(f"{nm}: damping reads back {got[0]}, set {zeta}")
        return "API (LoadCases.ModHistNonlinear)"
    # ETABS 23: "Load Case Definitions - Time History - Nonlinear Modal"; its damping is in
    # "Load Case Definitions - Damping - Modal" (one row per case)
    key = _table(sm, R, ("load case", "fna"), ("load case", "nonlin", "modal"))
    if not key:
        _no_table(sm, R, f"FNA case {nm} (ModHistNonlinear offers: {R._methods(mh)})")
    extra = {"modalcase|modal": modal}
    rows = _th_rows(th, extra)
    required = ("name", "loadtype", "loadname", "function|funcname|func|timefunction", "loadsf|scalefactor|sf",
                *_STEP_COLS, *extra)
    setf = _add_rows(sm, R, key, rows, required)
    _verify(sm, R, key, nm, setf, dict(extra, **{_STEP_COLS[0]: th["steps"], _STEP_COLS[1]: th["dt"]}))
    damps = _damp_tables(sm, R)
    dkey = next((k for k in damps if "modal" in k.lower() and "load case" in k.lower()), "")
    if not dkey:
        raise R.Stop(f"{nm}: no modal damping table; damping tables: {damps}")
    cands = ("constdamp|dampconst|constantdamping|dampconstant|constdampratio|constantdamp|constantratio|"
             "dampratio|constant|ratio")
    dset, added = _set_case_row(sm, R, dkey, nm, {cands: zeta}, (cands,))
    _verify(sm, R, dkey, nm, dset, {cands: zeta})
    return (f"table {key}; constant modal damping {zeta} as {dset[cands]} in {dkey} "
            f"({'row added' if added else 'row edited'})")


# ── links ───────────────────────────────────────────────────────────────────
def set_damper(sm, R, dp: dict) -> str:
    """A linear viscous damper along U1 (i -> j): no linear effective stiffness or damping, nonlinear
    Maxwell spring k in series with dashpot c, exponent 1."""
    name = f"DAMP_{dp['id']}"
    dof, fixed, nonlin = [True] + [False] * 5, [False] * 6, [True] + [False] * 5
    ke, ce = [0.0] * 6, [0.0] * 6
    k, c, ex = [float(dp["k"])] + [0.0] * 5, [float(dp["c"])] + [0.0] * 5, [1.0] * 6
    tried = []
    if not (R._try("PropLink.SetDamper", tried, lambda: sm.PropLink.SetDamper(
                name, dof, fixed, nonlin, ke, ce, k, c, ex, 0.0, 0.0, "", ""))
            or R._try("PropLink.SetDamper (short)", tried, lambda: sm.PropLink.SetDamper(
                name, dof, fixed, nonlin, ke, ce, k, c, ex, 0.0, 0.0))):
        raise R.Stop("damper link property refused: " + "; ".join(tried) + f" | PropLink offers: {R._methods(sm.PropLink)}")
    back = "not read back"
    try:
        got = sm.PropLink.GetDamper(name, [], [], [], [], [], [], [], [], 0.0, 0.0, "", "")
        R.ok(got, f"read back damper {name}")
        gk, gc, gx = float(got[5][0]), float(got[6][0]), float(got[7][0])
        if (abs(gk - dp["k"]) > 1e-9 * dp["k"] or abs(gc - dp["c"]) > 1e-9 * dp["c"] or abs(gx - 1.0) > 1e-12
                or abs(float(got[3][0])) > 1e-12 or not got[2][0]):
            raise R.Stop(f"damper {name} reads back k {gk}, c {gc}, exponent {gx}, Ke {got[3][0]}, "
                         f"nonlinear {got[2][0]}; set k {dp['k']}, c {dp['c']}, 1, 0, True")
        back = f"read back k {gk}, c {gc}, exponent {gx}, Ke {got[3][0]}"
    except R.Stop:
        raise
    except Exception as exc:
        back = f"not read back ({exc})"
    (xi, yi, zi), (xj, yj, zj) = dp["i"], dp["j"]
    r = sm.LinkObj.AddByCoord(xi, yi, zi, xj, yj, zj, "", False, name, dp["id"], "Global")
    R.ok(r, f"damper link {dp['id']}")
    return f"damper c {dp['c']:.6g} kip-s/ft, series k {dp['k']:.6g} k/ft ({back}), link {r[0]}"


# ── Ritz ────────────────────────────────────────────────────────────────────
def set_ritz(sm, R, rc: dict) -> str:
    nm, n, mr = rc["name"], int(rc["modes"]), sm.LoadCases.ModalRitz
    starts = list(rc["starts"])
    k = len(starts)
    if all(hasattr(mr, m) for m in ("SetCase", "SetLoads", "SetNumberModes")):
        R.ok(mr.SetCase(nm), f"Ritz case {nm}")
        R.ok(mr.SetNumberModes(nm, n, n), f"{nm} number of vectors")
        R.ok(mr.SetLoads(nm, k, ["Accel"] * k, starts, [0] * k, [99.0] * k), f"{nm} starting vectors")
        msg = "API (LoadCases.ModalRitz)"
        if hasattr(mr, "GetNumberModes"):
            got = mr.GetNumberModes(nm, 0, 0)
            R.ok(got, f"read back {nm} vectors")
            if int(got[0]) != n:
                raise R.Stop(f"{nm}: number of vectors reads back {list(got[:2])}, set {n}")
            msg += f", {got[0]} vectors (read back)"
        if hasattr(mr, "GetLoads"):
            got = mr.GetLoads(nm, 0, [], [], [], [])
            R.ok(got, f"read back {nm} loads")
            msg += f", loads {list(got[1])} {list(got[2])} (read back)"
        return msg
    key = _table(sm, R, ("load case", "ritz"), ("ritz",))       # ETABS 23: "Modal Case Definitions - Ritz"
    if not key:
        _no_table(sm, R, f"Ritz case {nm} (ModalRitz offers: {R._methods(mr)})")
    tried = []
    u123 = {"UX": "U1", "UY": "U2", "UZ": "U3"}
    # the table's words for an acceleration start are not documented: try the API's, then the GUI's
    for ltype, numbered in (("Acceleration", False), ("Accel", False), ("Acceleration", True)):
        rows = [{"name": nm, "maxmodes|maxnummodes|maxnumber|nummodes": n, "minmodes|minnummodes|minnumber": n,
                 "loadtype": ltype, "loadname": u123.get(s, s) if numbered else s,
                 "maxcycles|ritzmaxcyc|maxcyc": 0, "targetpar|targetmasspar|targetratio": 99.0} for s in starts]
        try:
            setf = _add_rows(sm, R, key, rows, ("name", "maxmodes|maxnummodes|maxnumber|nummodes", "loadtype",
                                                "loadname"))
            _verify(sm, R, key, nm, setf, {"maxmodes|maxnummodes|maxnumber|nummodes": n})
            return f"table {key} (load type {ltype})"
        except R.Stop as exc:
            tried.append(str(exc))
    raise R.Stop(f"Ritz case {nm} refused: " + " || ".join(tried)[:1500])


# ── response spectrum with several loads ────────────────────────────────────
_DIR = {"SRSS": 1, "ABS": 2, "CQC3": 3}           # GetDirComb MyType


def _dir_comb(rsp, nm: str):
    got = rsp.GetDirComb(nm, 0, 0.0)
    if (got[-1] if isinstance(got, (list, tuple)) else got) != 0:
        return None
    return int(got[0]), float(got[1])


def set_rs_multi(sm, R, case: dict, rs: dict) -> str:
    nm, rsp = rs["name"], sm.LoadCases.ResponseSpectrum
    lds = rs["loads"]
    n = len(lds)
    R.ok(rsp.SetCase(nm), f"response spectrum case {nm}")
    R.ok(rsp.SetModalCase(nm, case["modal"]["case"]), f"{nm} modal case")
    R.ok(rsp.SetLoads(nm, n, ["U1" if ld["dir"] == "X" else "U2" for ld in lds], [rs["function"]] * n,
                      [float(ld["scale"]) for ld in lds], ["Global"] * n, [0.0] * n), f"{nm} loads")
    R.ok(rsp.SetEccentricity(nm, 0.0), f"{nm} eccentricity")
    got = rsp.GetLoads(nm, 0, [], [], [], [], [])
    R.ok(got, f"read back {nm} loads")
    if int(got[0]) != n or any(abs(float(a) - float(ld["scale"])) > 1e-9 * abs(float(ld["scale"]))
                               for a, ld in zip(got[3], lds)):
        raise R.Stop(f"{nm}: loads read back {list(got[1])} {list(got[2])} {list(got[3])}, set {lds}")
    out = [f"loads {list(got[1])} x {[round(float(a), 6) for a in got[3]]} (read back)"]
    out.append(R.set_rs_comb_damping(sm, nm, {"combine": rs["combine"], "damping": rs["damping"]}))
    want, sf = _DIR[rs["dirCombo"]], float(rs.get("dirSF", 1.0))
    have = _dir_comb(rsp, nm)
    if have is not None and have[0] == want and (want != 2 or abs(have[1] - sf) < 1e-9):
        out.append(f"directions {rs['dirCombo']} (ETABS default, read back)")
        return ", ".join(out)
    tried = []
    if hasattr(rsp, "SetDirComb"):
        R._try("ResponseSpectrum.SetDirComb", tried, lambda: rsp.SetDirComb(nm, want, sf))
    else:
        key = _table(sm, R, ("load case", "response spectrum"))
        if not key:
            _no_table(sm, R, f"{nm} directional combination")
        names = {"ABS": ("Absolute", "ABS", "Abs"), "SRSS": ("SRSS",), "CQC3": ("CQC3",)}[rs["dirCombo"]]
        for label in names:
            try:
                tried.append(R.edit_table_row(sm, key, nm, {"dircombo|dircomb|dirctype|dircombtype|directionalcombo|"
                                                            "dircombination": label}))
            except R.Stop as exc:
                tried.append(str(exc))
                continue
            if want == 2:
                try:
                    tried.append(R.edit_table_row(sm, key, nm, {"dirsf|abssf|dircombsf|absdirsf|dircombscale|"
                                                                "absscale": sf}))
                except R.Stop as exc:
                    tried.append(f"(scale factor not set: {exc})")
            have = _dir_comb(rsp, nm)
            if have is not None and have[0] == want:
                break
    have = _dir_comb(rsp, nm)
    if have is None or have[0] != want or (want == 2 and abs(have[1] - sf) > 1e-9):
        raise R.Stop(f"{nm}: directional combination reads back {have}, wanted {rs['dirCombo']} ({want}, sf {sf}); "
                     + " | ".join(tried)[:1500] + f" | ResponseSpectrum offers: {R._methods(rsp)}")
    out.append(f"directions {rs['dirCombo']} sf {have[1]} (set, read back: {'; '.join(tried)[:300]})")
    return ", ".join(out)


# ── hooks ───────────────────────────────────────────────────────────────────
def build(sm, case: dict, R, point, names: dict) -> None:
    for dp in case.get("dampers", []):
        R.say(f"  damper {dp['id']}: {set_damper(sm, R, dp)}")
    for th in case.get("dynTH", []):
        if th["method"] == "direct":
            how = set_direct_nonlinear(sm, R, th)
        elif th["method"] == "fna":
            how = set_fna(sm, R, case, th)
        else:
            raise R.Stop(f"dynTH method {th['method']!r} not mapped")
        R.say(f"  time history {th['name']} ({th['method']}, {th['steps']} x {th['dt']} s): {how}")
    for rc in case.get("ritzCases", []):
        R.say(f"  Ritz case {rc['name']} ({rc['modes']} vectors from {rc['starts']}): {set_ritz(sm, R, rc)}")
    for rs in case.get("rsMulti", []):
        R.say(f"  spectrum case {rs['name']}: {set_rs_multi(sm, R, case, rs)}")


def analysis_cases(case: dict) -> list:
    return [rc["name"] for rc in case.get("ritzCases", [])]


def _select(sm, R, cname: str) -> None:
    R.ok(sm.Results.Setup.DeselectAllCasesAndCombosForOutput(), "deselect output")
    R.ok(sm.Results.Setup.SetCaseSelectedForOutput(cname), f"select {cname}")


def _links_by_points(sm, R) -> dict:
    """{(xyz_i, xyz_j): ETABS link name} for every link object."""
    out = {}
    r = sm.LinkObj.GetNameList(0, [])
    R.ok(r, "list links")
    for ln in r[1]:
        p = sm.LinkObj.GetPoints(ln, "", "")
        R.ok(p, f"end points of link {ln}")
        xyz = []
        for pn in p[:2]:
            c = sm.PointObj.GetCoordCartesian(pn, 0, 0, 0, "Global")
            R.ok(c, f"coordinates of point {pn}")
            xyz.append(tuple(round(v, 6) for v in c[:3]))
        out[tuple(xyz)] = ln
    return out


def _joint_at(sm, R, at) -> str:
    r = sm.PointObj.GetNameList(0, [])
    R.ok(r, "list points")
    for pn in r[1]:
        c = sm.PointObj.GetCoordCartesian(pn, 0, 0, 0, "Global")
        R.ok(c, f"coordinates of point {pn}")
        if math.dist(c[:3], at) < 1e-6:
            return pn
    raise R.Stop(f"no joint at {at}")


def results(sm, case: dict, R, names: dict, res: dict) -> None:
    vals = res.setdefault("values", {})
    for opt in ("SetOptionModalHist", "SetOptionDirectHist"):
        try:                       # step by step (envelopes gave only the last step in N4): peaks below
            getattr(sm.Results.Setup, opt)(2)          # are max |value| over every row
        except Exception:
            pass
    links = case.get("links", []) + case.get("dampers", [])
    by_pts = _links_by_points(sm, R) if links else {}
    for th in case.get("dynTH", []):
        cname = th["name"]
        _select(sm, R, cname)
        br = sm.Results.BaseReact(0, [], [], [], [], [], [], [], [], [], 0, 0, 0)
        R.ok(br, f"base reactions {cname}")
        vals[f"{cname}:peakBaseFX"] = max(abs(br[4][k]) for k in range(br[0]))
        for lk in links:
            key = (tuple(round(v, 6) for v in lk["i"]), tuple(round(v, 6) for v in lk["j"]))
            ln = by_pts.get(key) or by_pts.get((key[1], key[0]))
            if ln is None:
                raise R.Stop(f"link {lk['id']} not found among {list(by_pts.values())}")
            lf = sm.Results.LinkForce(ln, 0, 0, [], [], [], [], [], [], [], [], [], [], [], [])
            R.ok(lf, f"link forces {lk['id']} {cname}")
            vals[f"{cname}:peakLinkP:{lk['id']}"] = max(abs(lf[7][k]) for k in range(lf[0]))
            R.say(f"  {cname}: peak axial force in link {lk['id']} ({ln}) {vals[f'{cname}:peakLinkP:' + lk['id']]:.6g}")
        top = next((ms["at"] for ms in case.get("masses", [])), None)
        if top is not None:
            jv = sm.Results.JointVel(_joint_at(sm, R, top), 0, 0, [], [], [], [], [], [], [], [], [], [], [])
            R.ok(jv, f"joint velocities {cname}")
            vals[f"{cname}:peakVX"] = max(abs(jv[6][k]) for k in range(jv[0]))
    for rc in case.get("ritzCases", []):
        _select(sm, R, rc["name"])
        mp = sm.Results.ModalPeriod(0, [], [], [], [], [], [], [])
        R.ok(mp, f"periods {rc['name']}")
        mr = sm.Results.ModalParticipatingMassRatios(0, [], [], [], [], [], [], [], [], [], [], [], [], [], [], [], [])
        R.ok(mr, f"mass ratios {rc['name']}")
        # ETABS lists every modal case's modes whatever is selected: keep this case's rows
        T = [mp[4][k] for k in range(mp[0]) if str(mp[1][k]).lower() == rc["name"].lower()]
        UX = [mr[5][k] for k in range(mr[0]) if str(mr[1][k]).lower() == rc["name"].lower()]
        for k, t in enumerate(T):
            vals[f"{rc['name']}:T{k + 1}"] = t
        for k, u in enumerate(UX):
            vals[f"{rc['name']}:UX{k + 1}"] = u
        R.say(f"  {rc['name']}: periods {T}, UX ratios {UX}")
