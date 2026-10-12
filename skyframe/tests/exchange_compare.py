"""Result comparison helpers for the model-exchange round-trip tests."""

import math


def _flatten(obj, path, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, path + (str(k),), out)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _flatten(v, path + (i,), out)
    elif isinstance(obj, bool) or obj is None or isinstance(obj, str):
        out[path] = obj
    elif isinstance(obj, (int, float)):
        out[path] = float(obj)


def max_rel_diff(a: dict, b: dict, depth: int = 2):
    """(max relative difference, offending path, missing keys): each leaf
    difference is divided by the max |value| of its ``depth``-key group."""
    fa, fb = {}, {}
    _flatten(a, (), fa)
    _flatten(b, (), fb)
    missing = sorted(set(fa) ^ set(fb), key=str)[:5]
    scale = {}
    for p, v in fa.items():
        if isinstance(v, float) and math.isfinite(v):
            g = p[:depth]
            scale[g] = max(scale.get(g, 0.0), abs(v))
    worst, where = 0.0, None
    for p, v in fa.items():
        w = fb.get(p)
        if isinstance(v, float) and isinstance(w, float):
            if not (math.isfinite(v) and math.isfinite(w)):
                if not (v == w or (math.isnan(v) and math.isnan(w))):
                    return float("inf"), p, missing
                continue
            s = scale.get(p[:depth], 0.0) or 1.0
            d = abs(v - w) / s
            if d > worst:
                worst, where = d, p
        elif v != w and not (isinstance(v, float) or isinstance(w, float)):
            return float("inf"), p, missing
    return worst, where, missing
