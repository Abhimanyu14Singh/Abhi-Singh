"""Example SkyFrame plugin (docs/PLUGINS.md): per-story member counts and
drifts.

* ``pre_analysis``  -- warns when the model has no static load case;
* ``post_analysis`` -- adds ``results["plugins"]["example-story-summary"]
  = {"stories", "max_drift", "governing"}``;
* endpoint ``summary`` -- per-story member counts of the current model;
* table ``story_summary`` -- one row per story: elevation, member counts
  and the largest |drift ratio| over the static cases.

Only plain Python and the documented model / results shapes are used.
"""


def _counts(model):
    rows = []
    for s in model.stories:
        mem = [m for m in model.members if m.story == s.name]
        rows.append({"story": s.name, "elevation": s.elevation,
                     "columns": sum(m.kind == "column" for m in mem),
                     "beams": sum(m.kind == "beam" for m in mem),
                     "braces": sum(m.kind == "brace" for m in mem)})
    return rows


def _drift(results, story):
    best = 0.0
    for case in (results.get("cases") or {}).values():
        row = (case.get("story") or {}).get(story) or {}
        best = max(best, abs(row.get("drift_x", 0.0)),
                   abs(row.get("drift_y", 0.0)))
    return best


def pre_analysis(model, ctx):
    if not model.cases:
        return ["the model has no static load case"]
    ctx["log"](f"pre_analysis: {len(model.members)} frames")
    return []


def post_analysis(model, results, ctx):
    drifts = {s.name: _drift(results, s.name) for s in model.stories}
    gov = max(drifts, key=drifts.get) if drifts else None
    return {"stories": len(model.stories),
            "max_drift": drifts[gov] if gov else 0.0, "governing": gov}


def _summary(model, body, ctx):
    return {"model": model.name, "stories": _counts(model)}


def _table(results, model):
    rows = _counts(model)
    for r in rows:
        r["max_drift"] = _drift(results, r["story"])
    return rows


endpoints = {"summary": _summary}
tables = {"story_summary": _table}
