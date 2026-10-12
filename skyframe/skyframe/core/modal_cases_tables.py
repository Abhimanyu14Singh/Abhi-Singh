"""Interactive Database Editing tables for named mass sources and modal
cases (:mod:`skyframe.core.modal_cases`), appended to the
:mod:`skyframe.core.modeltables` catalogue by one hook line.

* ``mass_sources`` — Mass Sources (named): name, self-mass mode and the
  five options;
* ``mass_source_patterns`` — Mass Sources (named) - Load Patterns:
  (source, pattern, multiplier);
* ``modal_cases`` — Modal Case Definitions: name, type, modes, mass
  source, stiffness case, Ritz max cycles and the Ritz starting vectors as
  one text cell (``UX; UY; pattern:DEAD; links``).

Renames cascade (mass source -> modal cases; modal case -> RS / TH
``modal_case``); deleting a referenced definition is refused.
"""

from __future__ import annotations

from typing import List


def _starts_text(o, d) -> str:
    out = []
    for s in o.get("ritz_starts") or []:
        if s.get("type") == "accel":
            out.append(str(s.get("dir")))
        elif s.get("type") == "pattern":
            out.append(f"pattern:{s.get('name')}")
        elif s.get("type") == "links":
            out.append("links")
    return "; ".join(out)


def parse_starts(text: str) -> List[dict]:
    """``"UX; UY; pattern:DEAD; links"`` -> Ritz start dicts."""
    out: List[dict] = []
    for tok in str(text or "").replace(",", ";").split(";"):
        tok = tok.strip()
        if not tok:
            continue
        if tok.lower() == "links":
            out.append({"type": "links"})
        elif tok.lower().startswith("pattern:"):
            out.append({"type": "pattern", "name": tok.split(":", 1)[1]
                        .strip()})
        else:
            out.append({"type": "accel", "dir": tok.upper()})
    return out


def _put_starts(o, v, d) -> None:
    if (o.get("type") or "eigen") == "ritz":
        st = parse_starts(v)
        if st:
            o["ritz_starts"] = st
        else:
            o.pop("ritz_starts", None)        # -> the default UX, UY


def cascade_pattern(d, mp) -> None:
    """Pattern renames -> named mass sources and Ritz pattern starts."""
    for src in (d.get("mass_sources") or {}).values():
        pats = src.get("mass_source") or {}
        src["mass_source"] = {mp.get(k, k): v for k, v in pats.items()}
    for mc in (d.get("modal_cases") or {}).values():
        for s in mc.get("ritz_starts") or []:
            if s.get("type") == "pattern" and s.get("name") in mp:
                s["name"] = mp[s["name"]]


def _cascade_mass_source(d, mp) -> None:
    for mc in (d.get("modal_cases") or {}).values():
        if mc.get("mass_source") in mp:
            mc["mass_source"] = mp[mc["mass_source"]]


def _mass_source_refs(d, gone):
    users = [n for n, mc in (d.get("modal_cases") or {}).items()
             if mc.get("mass_source") in gone]
    if users:
        return (f"mass source(s) {sorted(gone)} used by modal case(s) "
                f"{users} — reassign first")
    return None


def _cascade_modal_case(d, mp) -> None:
    for key in ("rs_cases", "th_cases"):
        for c in (d.get(key) or {}).values():
            if c.get("modal_case") in mp:
                c["modal_case"] = mp[c["modal_case"]]


def _modal_case_refs(d, gone):
    users = [n for key in ("rs_cases", "th_cases")
             for n, c in (d.get(key) or {}).items()
             if c.get("modal_case") in gone]
    if users:
        return (f"modal case(s) {sorted(gone)} used by {users} — "
                "reassign first")
    return None


def tables(G_DEF: str, G_CASE: str) -> list:
    from skyframe.core.model import MASS_OPTION_DEFAULTS, MASS_SOURCE_MODES
    from skyframe.core.modal_cases import MODAL_CASE_TYPES
    from skyframe.core import modeltables as mt
    Col, num = mt.Col, "number"

    def opt(k, label):
        return Col(k, label, "bool", path=("options", k),
                   default=MASS_OPTION_DEFAULTS[k])

    def new_ms(d):
        return {"mass_source": {}, "mode": "weight",
                "options": dict(MASS_OPTION_DEFAULTS)}

    T = [
        mt.ObjectTable(
            "mass_sources", "Mass Sources (named)", G_DEF, [
                Col("name", "Mass Source", "text", required=True),
                Col("mode", "Self Mass Mode", "enum",
                    enum=MASS_SOURCE_MODES, default="weight"),
                opt("self_mass", "Element Self Mass"),
                opt("patterns", "Mass from Patterns"),
                opt("include_lateral", "Lateral Mass"),
                opt("include_vertical", "Vertical Mass"),
                opt("lump_at_stories", "Lump at Stories"),
            ], container="mass_sources", keycol="name", name_in_obj=False,
            on_rename=_cascade_mass_source, delete_refs=_mass_source_refs,
            new_obj=new_ms),
        mt.ChildDictTable(
            "mass_source_patterns", "Mass Sources (named) - Load Patterns",
            G_DEF, [
                Col("source", "Mass Source", "enum",
                    enum=mt._names("mass_sources"), required=True),
                Col("pattern", "Load Pattern", "enum",
                    enum=mt._names("patterns"), required=True),
                Col("factor", "Multiplier", num, required=True),
            ], container="mass_sources", field="mass_source",
            parent_col="source", child_col="pattern", value_col="factor"),
        mt.ObjectTable(
            "modal_cases", "Modal Case Definitions", G_CASE, [
                Col("name", "Modal Case", "text", required=True),
                Col("type", "Type", "enum", enum=MODAL_CASE_TYPES,
                    default="eigen"),
                Col("num_modes", "Max Modes / Vectors", "int", default=12),
                Col("mass_source", "Mass Source", "enum",
                    enum=mt._opt_names("mass_sources"), default="",
                    get=lambda o, d: o.get("mass_source") or ""),
                Col("stiffness_case", "Stiffness from NL Case", "enum",
                    enum=mt._opt_names("nonlinear_static_cases"),
                    default="",
                    get=lambda o, d: o.get("stiffness_case") or "",
                    put=lambda o, v, d: o.__setitem__("stiffness_case",
                                                      v or None)),
                Col("max_cycles", "Ritz Max Cycles", "int", default=0,
                    get=lambda o, d: o.get("max_cycles", 0)),
                Col("ritz_starts", "Ritz Starting Vectors", "text",
                    default="", get=_starts_text, put=_put_starts),
            ], container="modal_cases", keycol="name", name_in_obj=False,
            on_rename=_cascade_modal_case, delete_refs=_modal_case_refs,
            new_obj=lambda d: {"type": "eigen", "num_modes": 12,
                               "mass_source": "", "stiffness_case": None}),
    ]
    return T
