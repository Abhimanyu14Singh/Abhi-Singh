"""Per-area auto edge constraints (ETABS Assign > Shell > Auto Edge
Constraints) -- CONTRACT "Per-object auto edge constraints".

``ShellRegion.edge_constraint``: ``None`` (default) follows the model-wide
``BuildingModel.edge_constraints`` switch; ``True`` / ``False`` override it
for that area.  The engine's zipper (``core.mesh.edge_tie_chains``) is
unchanged; each hanging-node chain is kept when

* the area that OWNS the element edge has an effective ``True`` (own
  override, else the model switch) -- the area ties the mismatched nodes
  and mid-edge frame ends landing on its edges; OR
* any area owning one of the HANGING nodes carries an explicit ``True``
  -- an area assigned edge constraints also zips its own nodes onto a
  coarser neighbour's edges.

With no per-area override this is exactly the model-wide switch (every
chain when it is on, none when it is off), so legacy models are
byte-identical.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple


def any_override(model) -> bool:
    return any(getattr(r, "edge_constraint", None) is not None
               for r in model.shells)


def active(model) -> bool:
    """True when the engine must build the zipper at all."""
    if getattr(model, "edge_constraints", False):
        return True
    return any(getattr(r, "edge_constraint", None) is True
               for r in model.shells)


def _effective(model, region) -> bool:
    ec = getattr(region, "edge_constraint", None)
    return bool(getattr(model, "edge_constraints", False)) if ec is None \
        else bool(ec)


def filter_chains(model, mesh, chains: List[Tuple[str, int, int, List[int]]]
                  ) -> List[Tuple[str, int, int, List[int]]]:
    """The subset of ``edge_tie_chains(mesh)`` to realise (see the module
    doc).  Returns ``chains`` itself when no area overrides the switch."""
    if not any_override(model):
        return chains
    regions = {r.uid: r for r in model.shells}
    explicit_on: Set[str] = {r.uid for r in model.shells
                             if getattr(r, "edge_constraint", None) is True}
    node_regions: Dict[int, Set[str]] = {}
    if explicit_on:
        for q in mesh.quads:
            if q.region in explicit_on:
                for n in q.nodes:
                    node_regions.setdefault(n, set()).add(q.region)
    out = []
    for ch in chains:
        ruid, _a, _b, hangs = ch
        reg = regions.get(ruid)
        if reg is not None and _effective(model, reg):
            out.append(ch)
            continue
        if any(node_regions.get(h) for h in hangs):
            out.append(ch)
    return out


def summary(model) -> Dict[str, object]:
    """``{"model": bool, "on": [uids], "off": [uids]}`` (UI / report)."""
    return {"model": bool(getattr(model, "edge_constraints", False)),
            "on": [r.uid for r in model.shells
                   if getattr(r, "edge_constraint", None) is True],
            "off": [r.uid for r in model.shells
                    if getattr(r, "edge_constraint", None) is False]}


def validate(model) -> None:
    for r in model.shells:
        ec = getattr(r, "edge_constraint", None)
        if ec is not None and not isinstance(ec, bool):
            raise ValueError(f"Shell {r.uid}: edge_constraint must be "
                             f"true, false or null (got {ec!r})")
        if ec is not None and r.behavior != "shell":
            raise ValueError(f"Shell {r.uid}: edge constraints need shell "
                             "behavior (membrane regions are not meshed)")


# --------------------------------------------------------------------------- #
# DB edit: an "Edge Constraint" column on the Shell Objects table
# --------------------------------------------------------------------------- #
EC_CHOICES = ("model", "yes", "no")


def col_get(o, d):
    ec = o.get("edge_constraint")
    return "model" if ec is None else ("yes" if ec else "no")


def col_put(o, v, d):
    if v in (None, "", "model"):
        o.pop("edge_constraint", None)
    else:
        o["edge_constraint"] = v in ("yes", True, "true", "True")
