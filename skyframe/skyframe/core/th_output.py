"""Time-history output requests: shell forces / stresses (Wave C).

``TimeHistoryCase.th_output`` (``None`` = legacy: nothing extra recorded,
key omitted from the model JSON)::

    {"shells": true,          # record shell resultants every step
     "steps": [50, 120],      # full snapshots at these 1-based steps
     "every": 0,              # + a snapshot every N steps (0 = none)
     "frames": 0}             # deformed-shape animation frames (0 = none)

With ``shells`` on, the direct-integration engine records the
Gauss-averaged shell resultants (the static ``shell_forces`` shape) at
every step: their running max / min (the envelope) and full snapshots at
the requested steps go to ``results.th_cases[name]["shell_forces"]``.
Shell contours (core/shell_results.py) read a snapshot (``step``) or the
envelope; the shell tables list them as Max / Min / step rows.

``frames`` > 0 records the full nodal displacement field (6 dof per node,
past the gravity state) at ``frames`` evenly spaced steps (the last step
always included) into ``results.th_cases[name]["frames"] = {"steps":
[...], "t": [...], "node_disp": {"<step>": {"<tag>": [6]}}}`` — the
deformed-shape animation / video export of the TH response.

Cut forces (section cuts, piers, spandrels) need no request: they are
recorded for every TH case of a model that defines them
(engine/cut_results.py).
"""

from __future__ import annotations

from typing import Optional

KEYS = ("shells", "steps", "every", "frames")
MAX_SNAPSHOTS = 200
MAX_FRAMES = 1000
DEFAULTS = {"shells": False, "steps": [], "every": 0, "frames": 0}


def normalize(req) -> Optional[dict]:
    """Canonical ``th_output`` (None stays None); ValueError on bad shape."""
    if req is None:
        return None
    if not isinstance(req, dict):
        raise ValueError("th_output must be an object")
    bad = [k for k in req if k not in KEYS]
    if bad:
        raise ValueError(f"th_output: unknown key(s) {sorted(bad)}; "
                         f"allowed {list(KEYS)}")
    out: dict = {"shells": bool(req.get("shells", False))}
    steps = req.get("steps") or []
    if not isinstance(steps, (list, tuple)):
        raise ValueError("th_output.steps must be a list of step numbers")
    clean = []
    for s in steps:
        if isinstance(s, bool) or not isinstance(s, (int, float)) \
                or float(s) != int(s) or int(s) < 1:
            raise ValueError("th_output.steps entries must be integers >= 1")
        clean.append(int(s))
    out["steps"] = sorted(set(clean))
    ev = req.get("every", 0) or 0
    if isinstance(ev, bool) or not isinstance(ev, (int, float)) \
            or float(ev) != int(ev) or int(ev) < 0:
        raise ValueError("th_output.every must be an integer >= 0")
    out["every"] = int(ev)
    fr = req.get("frames", 0) or 0
    if isinstance(fr, bool) or not isinstance(fr, (int, float)) \
            or float(fr) != int(fr) or not 0 <= int(fr) <= MAX_FRAMES:
        raise ValueError(f"th_output.frames must be an integer in "
                         f"[0, {MAX_FRAMES}]")
    out["frames"] = int(fr)
    if len(out["steps"]) > MAX_SNAPSHOTS:
        raise ValueError(f"th_output.steps: at most {MAX_SNAPSHOTS} "
                         "snapshot steps")
    # canonical form keeps only the non-default keys (a UI writing the
    # minimal request round-trips byte-identically)
    return {k: v for k, v in out.items() if v != DEFAULTS[k]}


def _full(req) -> dict:
    return dict(DEFAULTS, **(normalize(req) or {}))


def validate(model) -> None:
    """Every TH case's ``th_output`` (called from BuildingModel.validate)."""
    for th in (getattr(model, "th_cases", None) or {}).values():
        req = getattr(th, "th_output", None)
        if req is None:
            continue
        try:
            norm = normalize(req)
        except ValueError as exc:
            raise ValueError(f"TH case {th.name}: {exc}") from None
        th.th_output = norm


def frame_steps(th, n: int) -> list:
    """1-based steps of the animation frames of a run with ``n`` steps."""
    req = getattr(th, "th_output", None)
    nf = _full(req)["frames"] if req is not None else 0
    if nf <= 0 or n <= 0:
        return []
    nf = min(nf, n)
    return sorted({max(1, round(n * (k + 1) / nf)) for k in range(nf)})


def shell_request(th) -> Optional[dict]:
    """The normalized request when shell recording is on, else None."""
    req = getattr(th, "th_output", None)
    if req is None:
        return None
    norm = _full(req)
    return norm if norm["shells"] else None


def model_from_dict(mdl, d: dict) -> None:
    """Read ``th_cases[*].th_output`` from a model JSON dict."""
    for name, td in (d.get("th_cases") or {}).items():
        if name in mdl.th_cases and isinstance(td, dict) \
                and td.get("th_output") is not None:
            mdl.th_cases[name].th_output = normalize(td["th_output"])
