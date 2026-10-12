"""Element / wall / nonlinear-step analysis-results tables (ETABS Display >
Show Tables), registered into :mod:`skyframe.core.tables` through its
extension registry (:func:`skyframe.core.tables.register_table`).

Pure post-processing of a solved results dict (no re-solve):

=============================  ==============================================
key                            ETABS table
=============================  ==============================================
element_forces_frames          Element Forces - Frames (every output station)
element_forces_links           Element Forces - Links (elastic links)
pier_forces                    Pier Forces
spandrel_forces                Spandrel Forces
section_cut_forces             Section Cut Forces - Analysis
frame_hinge_states             Frame Hinge States (pushover / NL static / NLTH)
tendon_forces                  Tendon Forces (P after losses per station)
pushover_curve                 Pushover Curve (with hinge-state counts)
nonlinear_static_steps         Nonlinear Static Steps
th_base_reactions_steps        Base Reactions - Time History Steps
th_story_displacements_steps   Story Displacements - Time History Steps
=============================  ==============================================

Sign conventions follow ETABS: frame / pier / spandrel ``P`` is
TENSION-positive (the engine's pier ``P`` is compression-positive, so the
pier table reports ``-P``); frame station forces are the engine's
``member_stations`` values (``N`` tension-positive) unchanged.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from . import tables as _T

_col = _T._col
_G = "Analysis Results"
G_FRAME = f"{_G} > Element Output > Frame Output"
G_LINK = f"{_G} > Element Output > Link Output"
G_WALL = f"{_G} > Element Output > Wall Output"
G_TENDON = f"{_G} > Element Output > Tendon Output"
G_CUT = f"{_G} > Structure Output > Other Output Items"
G_NL = f"{_G} > Structure Output > Nonlinear Output"
G_TH = f"{_G} > Structure Output > Time History Output"

FORCE6 = [_col("P", "P", "force"), _col("V2", "V2", "force"),
          _col("V3", "V3", "force"), _col("T", "T", "moment"),
          _col("M2", "M2", "moment"), _col("M3", "M3", "moment")]


def _case_type(c, name: str) -> str:
    if name in c.R.get("combos", {}):
        return "Combination"
    lc = c.model.cases.get(name)
    if lc is not None:
        return "LinStatic" if lc.effective_geometric == "linear" \
            else "NonStatic"
    return "LinStatic"


# --------------------------------------------------------------------------- #
# Element Forces - Frames
# --------------------------------------------------------------------------- #
FRAME_FORCE_COLS = [
    _col("story", "Story", "text"), _col("frame", "Frame", "text"),
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("station", "Station", "length")] + FORCE6


def _t_frame_forces(c) -> List[dict]:
    """One row per member output station (``member_stations``)."""
    story = {m["uid"]: m.get("story", "") for m in c.R.get("members", [])}
    order = [m.uid for m in c.model.members]
    rows = []
    for name, ctype, cd in c.sources():
        sts = cd.get("member_stations", {})
        for uid in order + [u for u in sts if u not in set(order)]:
            st = sts.get(uid)
            if not st or not st.get("x"):
                continue
            for i, x in enumerate(st["x"]):
                rows.append({
                    "story": story.get(uid, ""), "frame": uid, "case": name,
                    "case_type": ctype, "station": x,
                    "P": st["N"][i], "V2": st["V2"][i], "V3": st["V3"][i],
                    "T": st["T"][i], "M2": st["M2"][i], "M3": st["M3"][i]})
    return rows


# --------------------------------------------------------------------------- #
# Element Forces - Links (elastic links: F = k (u_j - u_i), global axes)
# --------------------------------------------------------------------------- #
LINK_FORCE_COLS = [
    _col("story", "Story", "text"), _col("link", "Link", "text"),
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("location", "Location", "text")] + FORCE6


def link_context(engine) -> dict:
    """``link_nodes``: uid -> [tag_i, tag_j] exactly as the engine resolved
    them (``OpenSeesEngine._find_node``)."""
    asm = getattr(engine, "_asm", None)
    if asm is None or not engine.model.links:
        return {}
    out = {}
    for lk in engine.model.links:
        try:
            out[lk.uid] = [int(engine._find_node(asm, lk.pi)),
                           int(engine._find_node(asm, lk.pj))]
        except Exception:                             # pragma: no cover
            continue
    return {"link_nodes": out}


def _link_tags(c, lk) -> Optional[Tuple[int, int]]:
    ln = (c.context.get("link_nodes") or {}).get(lk.uid)
    if ln:
        return int(ln[0]), int(ln[1])
    def find(p):            # first match in engine node order (_find_node)
        for t, xyz in c.nodes.items():
            if t in c.master_tags:
                continue
            if all(abs(xyz[k] - float(p[k])) < 1e-6 for k in range(3)):
                return t
        return None
    ti, tj = find(lk.pi), find(lk.pj)
    if ti is None or tj is None:
        return None
    return ti, tj


def _t_link_forces(c) -> List[dict]:
    rows = []
    links = list(getattr(c.model, "links", []) or [])
    if not links:
        return rows
    skipped = [lk.uid for lk in links
               if getattr(lk, "link_type", "elastic") != "elastic"]
    if skipped:
        c.warnings.append(
            "device links (" + ", ".join(skipped) + ") are nonlinear / "
            "velocity-dependent: their forces are reported by the time-"
            "history plot functions, not by this static table")
    for name, ctype, cd in c.sources():
        nd = cd.get("node_disp", {})
        for lk in links:
            if getattr(lk, "link_type", "elastic") != "elastic":
                continue
            tags = _link_tags(c, lk)
            if tags is None:
                continue
            di = nd.get(str(tags[0]), [0.0] * 6)
            dj = nd.get(str(tags[1]), [0.0] * 6)
            f = [float(lk.stiffness[k]) * (dj[k] - di[k]) for k in range(6)]
            z = 0.5 * (lk.pi[2] + lk.pj[2])
            st = c.story_at(z) or c.story_at(lk.pi[2])
            for loc in ("I-End", "J-End"):
                rows.append({"story": st, "link": lk.uid, "case": name,
                             "case_type": ctype, "location": loc,
                             "P": f[0], "V2": f[1], "V3": f[2], "T": f[3],
                             "M2": f[4], "M3": f[5]})
    return rows


# --------------------------------------------------------------------------- #
# Pier / Spandrel Forces
# --------------------------------------------------------------------------- #
PIER_COLS = [
    _col("story", "Story", "text"), _col("pier", "Pier", "text"),
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("location", "Location", "text"),
    _col("P", "P", "force"), _col("V2", "V2", "force"),
    _col("M3", "M3", "moment")]


def _story_rank(c) -> Dict[str, int]:
    return {s.name: i for i, s in enumerate(c.stories)}


def _filtered(c, block: dict):
    for name, by in block.items():
        if c.case_filter is not None and name not in c.case_filter:
            continue
        yield name, by


def _t_piers(c) -> List[dict]:
    rank = _story_rank(c)
    rows = []
    for name, by_pier in _filtered(c, c.R.get("piers", {}) or {}):
        ct = _case_type(c, name)
        for pier in sorted(by_pier):
            for story, f in sorted(by_pier[pier].items(),
                                   key=lambda kv: -rank.get(kv[0], -1)):
                rows.append({"story": story, "pier": pier, "case": name,
                             "case_type": ct, "location": "Bottom",
                             "P": -float(f["P"]), "V2": float(f["V"]),
                             "M3": float(f["M"])})
    return rows


SPANDREL_COLS = [
    _col("story", "Story", "text"), _col("spandrel", "Spandrel", "text"),
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("location", "Location", "text")] + FORCE6 + [
    _col("length", "Length", "length")]


def _t_spandrels(c) -> List[dict]:
    rank = _story_rank(c)
    rows = []
    for name, by_lab in _filtered(c, c.R.get("spandrels", {}) or {}):
        ct = _case_type(c, name)
        for lab in sorted(by_lab):
            for story, e in sorted(by_lab[lab].items(),
                                   key=lambda kv: -rank.get(kv[0], -1)):
                for loc in ("Left", "Right"):
                    f = e.get(loc)
                    if not f:
                        continue
                    rows.append({"story": story, "spandrel": lab,
                                 "case": name, "case_type": ct,
                                 "location": loc,
                                 **{k: float(f[k]) for k in
                                    ("P", "V2", "V3", "T", "M2", "M3")},
                                 "length": float(e.get("length", 0.0))})
    return rows


# --------------------------------------------------------------------------- #
# Section Cut Forces - Analysis
# --------------------------------------------------------------------------- #
CUT_COLS = [
    _col("cut", "SectionCut", "text"),
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("F1", "F1", "force"), _col("F2", "F2", "force"),
    _col("F3", "F3", "force"), _col("M1", "M1", "moment"),
    _col("M2", "M2", "moment"), _col("M3", "M3", "moment"),
    _col("n_members", "Frames", "id"), _col("n_shells", "Shells Excl.", "id")]


def _t_cuts(c) -> List[dict]:
    rows = []
    order = [ct.name for ct in getattr(c.model, "section_cuts", [])]
    for name, cuts in _filtered(c, c.R.get("section_cuts", {}) or {}):
        ct = _case_type(c, name)
        for cut in order + [k for k in cuts if k not in set(order)]:
            v = cuts.get(cut)
            if v is None:
                continue
            for w in v.get("warnings", []):
                c.warnings.append(f"{cut}: {w}")
            rows.append({"cut": cut, "case": name, "case_type": ct,
                         "F1": v["FX"], "F2": v["FY"], "F3": v["FZ"],
                         "M1": v["MX"], "M2": v["MY"], "M3": v["MZ"],
                         "n_members": int(v.get("n_members", 0)),
                         "n_shells": int(v.get("n_shells", 0))})
    return rows


# --------------------------------------------------------------------------- #
# Frame Hinge States
# --------------------------------------------------------------------------- #
HINGE_COLS = [
    _col("story", "Story", "text"), _col("frame", "Frame", "text"),
    _col("case", "Output Case", "text"), _col("case_type", "Case Type", "text"),
    _col("step", "Step", "id"), _col("hinge", "Hinge ID", "text"),
    _col("rel_dist", "Rel Dist", "ratio"), _col("dof", "Hinge DOF", "text"),
    _col("force", "Force/Moment", "moment"),
    _col("deform", "Deformation", "angle"),
    _col("plastic", "Plastic Deformation", "angle"),
    _col("state", "Hinge State", "text"),
    _col("status", "Hinge Status", "text")]

_USER_STATE = {      # engine hinge_state -> (ETABS state, ETABS status)
    "A-B": ("A to B", "<=IO"), "B-IO": ("B to C", "<=IO"),
    "IO-LS": ("B to C", "IO to LS"), "LS-CP": ("B to C", "LS to CP"),
    ">CP": ("B to C", ">CP"), "C-D": ("C to D", ">CP"),
    "D-E": ("D to E", ">CP"), ">E": (">E", ">CP")}
_LEGACY_STATUS = {"elastic": "<=IO", "IO": "<=IO", "LS": "IO to LS",
                  "CP": "LS to CP", "collapse": ">CP"}


def _elastic_k(h: dict) -> float:
    """Spring elastic stiffness of an auto hinge: |M| / |rot| at the
    largest-rotation step still in the elastic state (exact on the linear
    branch; 0 when no elastic step moved)."""
    best, ke = 0.0, 0.0
    for rot, mom, st in zip(h.get("rot") or [], h.get("moment") or [],
                            h.get("state") or []):
        if st == "elastic" and abs(rot) > best and abs(rot) > 1e-15:
            best, ke = abs(rot), abs(mom) / abs(rot)
    return ke


def hinge_state(h: dict, k: int) -> Tuple[str, str, float]:
    """(ETABS hinge state, status, plastic deformation) of hinge entry
    ``h`` at step ``k``.

    User hinges carry the engine's own ``hinge_state`` / ``rot_plastic``.
    Auto (ASCE 41) hinges carry ``state`` (elastic | IO | LS | CP |
    collapse); their plastic rotation is ``|rot| - My/k_e`` (``k_e`` = the
    spring's elastic stiffness, as in design.hinges.hinge_state; fiber
    hinges report ``rot_plastic`` directly) and the backbone
    segment follows from the plastic rotations a (point C) and b (point E):
    ``p <= 0`` A to B, ``p <= a`` B to C, ``p <= b`` D to E (the C-D drop is
    vertical), else >E.
    """
    rot = h.get("rot") or []
    r = float(rot[k]) if k < len(rot) else 0.0
    hs = h.get("hinge_state")
    if hs and k < len(hs):
        st, status = _USER_STATE.get(hs[k], (hs[k], ""))
        rp = h.get("rot_plastic") or []
        return st, status, float(rp[k]) if k < len(rp) else 0.0
    legacy = (h.get("state") or [])
    lg = legacy[k] if k < len(legacy) else "elastic"
    rp = h.get("rot_plastic")
    if rp and k < len(rp):                       # fiber hinges
        p = float(rp[k])
    else:
        ke = _elastic_k(h)
        p = (max(0.0, abs(r) - float(h.get("My", 0.0)) / ke)
             if ke > 0.0 else 0.0)
    if lg == "elastic":
        p = 0.0
    a, b = h.get("a"), h.get("b")
    if lg == "elastic" or p <= 1e-12:
        st = "A to B"
    elif a is None or p <= float(a):
        st = "B to C"
    elif b is None or p <= float(b):
        st = "D to E"
    else:
        st = ">E"
    return st, _LEGACY_STATUS.get(lg, lg), p


def _nl_keep(c, name: str, names: Iterable[str]) -> bool:
    """Case filter for the nonlinear / time-history step tables: the
    Show Tables filter lists static cases + additive combos only, so it
    applies here only when it names one of these cases (else all rows)."""
    if c.case_filter is None or not (set(names) & c.case_filter):
        return True
    return name in c.case_filter


def _hinge_sources(c) -> List[Tuple[str, str, List[dict]]]:
    out = []
    for name, po in (c.R.get("pushover", {}) or {}).items():
        out.append((name, "NonStatic", po.get("hinges") or []))
    for name, ns in (c.R.get("nonlinear_static", {}) or {}).items():
        nl = ns.get("nonlinear") or {}
        out.append((name, "NonStatic", nl.get("hinges") or []))
    for name, th in (c.R.get("th_cases", {}) or {}).items():
        out.append((name, "NonModHist", th.get("hinges") or []))
    names = [n for n, _t, _h in out]
    return [(n, t, h) for n, t, h in out if h and _nl_keep(c, n, names)]


def _rel_dist(h: dict) -> float:
    if h.get("relative_distance") is not None:
        return float(h["relative_distance"])
    e = str(h.get("end", ""))
    if e == "i":
        return 0.0
    if e == "j":
        return 1.0
    try:
        return float(e)
    except ValueError:
        return 0.0


def _t_hinges(c) -> List[dict]:
    story = {m["uid"]: m.get("story", "") for m in c.R.get("members", [])}
    rows = []
    for name, ctype, hinges in _hinge_sources(c):
        n = max((len(h.get("rot") or []) for h in hinges), default=0)
        for k in range(n):
            for h in hinges:
                if k >= len(h.get("rot") or []):
                    continue
                st, status, p = hinge_state(h, k)
                mom = h.get("moment") or []
                rows.append({
                    "story": story.get(h.get("uid"), ""),
                    "frame": h.get("uid", ""), "case": name,
                    "case_type": ctype, "step": k + 1,
                    "hinge": f"{h.get('uid', '')}-{h.get('end', '')}",
                    "rel_dist": _rel_dist(h), "dof": h.get("type", "M3"),
                    "force": float(mom[k]) if k < len(mom) else 0.0,
                    "deform": float(h["rot"][k]), "plastic": p,
                    "state": st, "status": status})
    return rows


# --------------------------------------------------------------------------- #
# Tendon Forces
# --------------------------------------------------------------------------- #
TENDON_COLS = [
    _col("tendon", "Tendon", "text"), _col("pattern", "Load Pattern", "text"),
    _col("station", "Station", "length"), _col("alpha", "Angle Change", "angle"),
    _col("P", "P", "force"), _col("loss", "Loss", "percent"),
    _col("x", "X", "length"), _col("y", "Y", "length"),
    _col("z", "Z", "length")]


def _t_tendons(c) -> List[dict]:
    rows = []
    for uid, td in (c.R.get("tendons", {}) or {}).items():
        st = td.get("stations") or {}
        P0 = float(td.get("P0") or 0.0)
        pts = st.get("points") or []
        for i, s in enumerate(st.get("s") or []):
            P = float(st["P"][i])
            p = pts[i] if i < len(pts) else [None, None, None]
            rows.append({"tendon": uid, "pattern": td.get("pattern", ""),
                         "station": float(s),
                         "alpha": float((st.get("alpha") or [0.0] * (i + 1))[i]),
                         "P": P,
                         "loss": 100.0 * (1.0 - P / P0) if P0 else 0.0,
                         "x": p[0], "y": p[1], "z": p[2]})
    return rows


# --------------------------------------------------------------------------- #
# Pushover curve / nonlinear static steps
# --------------------------------------------------------------------------- #
STATUS_BINS = ("<=IO", "IO to LS", "LS to CP", ">CP")
PUSH_COLS = [
    _col("case", "Output Case", "text"), _col("step", "Step", "id"),
    _col("disp", "Monitored Displ", "length"),
    _col("base_force", "Base Force", "force"),
    _col("drift", "Roof Drift", "ratio"),
    _col("AtoB", "A-B", "id"), _col("BtoC", "B-C", "id"),
    _col("CtoD", "C-D", "id"), _col("DtoE", "D-E", "id"),
    _col("beyondE", ">E", "id"),
    _col("AtoIO", "A-IO", "id"), _col("IOtoLS", "IO-LS", "id"),
    _col("LStoCP", "LS-CP", "id"), _col("beyondCP", ">CP", "id"),
    _col("total", "Total", "id")]
_STATE_KEY = {"A to B": "AtoB", "B to C": "BtoC", "C to D": "CtoD",
              "D to E": "DtoE", ">E": "beyondE"}
_STATUS_KEY = {"<=IO": "AtoIO", "IO to LS": "IOtoLS", "LS to CP": "LStoCP",
               ">CP": "beyondCP"}


def _t_pushover(c) -> List[dict]:
    rows = []
    po_all = c.R.get("pushover", {}) or {}
    for name, po in po_all.items():
        if not _nl_keep(c, name, po_all):
            continue
        hinges = po.get("hinges") or []
        disp, V = po.get("roof_disp") or [], po.get("base_shear") or []
        drift = po.get("roof_drift") or []
        for k in range(len(disp)):
            r = {"case": name, "step": k + 1, "disp": disp[k],
                 "base_force": V[k] if k < len(V) else None,
                 "drift": drift[k] if k < len(drift) else None}
            for key in list(_STATE_KEY.values()) + list(_STATUS_KEY.values()):
                r[key] = 0
            for h in hinges:
                if k >= len(h.get("rot") or []):
                    continue
                st, status, _p = hinge_state(h, k)
                if st in _STATE_KEY:
                    r[_STATE_KEY[st]] += 1
                if status in _STATUS_KEY:
                    r[_STATUS_KEY[status]] += 1
            r["total"] = sum(r[v] for v in _STATE_KEY.values())
            rows.append(r)
    return rows


NLS_COLS = [
    _col("case", "Output Case", "text"), _col("step", "Step", "id"),
    _col("load_factor", "Load Factor", "factor"),
    _col("disp", "Monitored Displ", "length"),
    _col("FX", "FX", "force"), _col("FY", "FY", "force"),
    _col("FZ", "FZ", "force"), _col("MX", "MX", "moment"),
    _col("MY", "MY", "moment"), _col("MZ", "MZ", "moment")]


def _t_nls(c) -> List[dict]:
    rows = []
    ns_all = c.R.get("nonlinear_static", {}) or {}
    for name, ns in ns_all.items():
        if not _nl_keep(c, name, ns_all):
            continue
        hist = (ns.get("nonlinear") or {}).get("history") or {}
        steps = hist.get("step") or []
        base = hist.get("base") or {}
        for i, s in enumerate(steps):
            def g(key, arr=None):
                a = arr if arr is not None else hist.get(key) or []
                return a[i] if i < len(a) else None
            rows.append({"case": name, "step": int(s),
                         "load_factor": g("load_factor"), "disp": g("disp"),
                         **{k: g(k, base.get(k) or []) for k in
                            ("FX", "FY", "FZ", "MX", "MY", "MZ")}})
    return rows


# --------------------------------------------------------------------------- #
# Time history steps
# --------------------------------------------------------------------------- #
TH_BASE_COLS = [
    _col("case", "Output Case", "text"), _col("step", "Step", "id"),
    _col("time", "Time", "time"), _col("FX", "FX", "force"),
    _col("FY", "FY", "force")]


def _th_items(c):
    th_all = c.R.get("th_cases", {}) or {}
    for name, th in th_all.items():
        if not _nl_keep(c, name, th_all):
            continue
        if th.get("t"):
            yield name, th


def _t_th_base(c) -> List[dict]:
    rows = []
    for name, th in _th_items(c):
        fx, fy = th.get("base_FX") or [], th.get("base_FY") or []
        for i, t in enumerate(th["t"]):
            rows.append({"case": name, "step": i + 1, "time": t,
                         "FX": fx[i] if i < len(fx) else None,
                         "FY": fy[i] if i < len(fy) else None})
    return rows


TH_STORY_COLS = [
    _col("case", "Output Case", "text"), _col("step", "Step", "id"),
    _col("time", "Time", "time"), _col("story", "Story", "text"),
    _col("UX", "UX", "length"), _col("UY", "UY", "length")]


def _t_th_story(c) -> List[dict]:
    rows = []
    order = [s.name for s in reversed(c.stories)]
    for name, th in _th_items(c):
        ux, uy = th.get("story_ux") or {}, th.get("story_uy") or {}
        names = [s for s in order if s in ux or s in uy]
        for i, t in enumerate(th["t"]):
            for s in names:
                a, b = ux.get(s) or [], uy.get(s) or []
                rows.append({"case": name, "step": i + 1, "time": t,
                             "story": s,
                             "UX": a[i] if i < len(a) else None,
                             "UY": b[i] if i < len(b) else None})
    return rows


# --------------------------------------------------------------------------- #
# registration (ETABS tree order, after the existing catalogue)
# --------------------------------------------------------------------------- #
TABLE_DEFS = [
    ("element_forces_frames", "Element Forces - Frames", G_FRAME,
     FRAME_FORCE_COLS, _t_frame_forces),
    ("frame_hinge_states", "Frame Hinge States", G_FRAME, HINGE_COLS,
     _t_hinges),
    ("element_forces_links", "Element Forces - Links", G_LINK,
     LINK_FORCE_COLS, _t_link_forces),
    ("pier_forces", "Pier Forces", G_WALL, PIER_COLS, _t_piers),
    ("spandrel_forces", "Spandrel Forces", G_WALL, SPANDREL_COLS,
     _t_spandrels),
    ("tendon_forces", "Tendon Forces", G_TENDON, TENDON_COLS, _t_tendons),
    ("section_cut_forces", "Section Cut Forces - Analysis", G_CUT, CUT_COLS,
     _t_cuts),
    ("pushover_curve", "Pushover Curve", G_NL, PUSH_COLS, _t_pushover),
    ("nonlinear_static_steps", "Nonlinear Static Steps", G_NL, NLS_COLS,
     _t_nls),
    ("th_base_reactions_steps", "Base Reactions - Time History Steps", G_TH,
     TH_BASE_COLS, _t_th_base),
    ("th_story_displacements_steps", "Story Displacements - Time History "
     "Steps", G_TH, TH_STORY_COLS, _t_th_story),
]


def register(keys: Optional[Iterable[str]] = None) -> None:
    for key, title, group, cols, fn in TABLE_DEFS:
        if keys is None or key in keys:
            _T.register_table(key, title, group, cols, fn)
    _T.register_context(link_context)
