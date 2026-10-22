"""Time-dependent group C1-C3 (kip, ft): creep, shrinkage, and the N6 pushover with frame hinges
assigned through ETABS's database tables.

Extension keys (read by sf_ext_timedep.py and etabs_ext_timedep.py):

    timeDependent   {material, law "ACI 209R-92", rh (percent), vs (volume / surface, ft),
                     cement "I", curing "moist", shrinkStart (days, age when drying starts),
                     creep, shrinkage, aging (bools)}
                    the code model both programs offer; aging off: E stays the 28-day E
    tdStaged        {name, group (every frame and joint), age (days, concrete age when the
                     structure is added), duration (days), pattern (applied when added) | null}
                    one stage: add the structure at that age, load it, hold it for the duration;
                    results are read at the end of the stage
    extPushover     the N6 "pushover" entry, built by the extension (hinges included) so that ETABS
                    can assign the hinges through its tables instead of the stock FrameObj call

ACI 209R-92 (moist cured, type I cement, every correction factor not listed = 1, i.e. standard
slump, fines, air and cement content), V/S in mm:

    creep      phi(t, t0) = (t - t0)^0.6 / (10 + (t - t0)^0.6) phi_u,  phi_u = 2.35 g_la g_rh g_vs
               g_la = 1.25 t0^-0.118, g_rh = 1.27 - 0.67 h, g_vs = 2/3 (1 + 1.13 e^(-0.0213 V/S))
    shrinkage  eps_sh(t) = (t - ts) / (35 + t - ts) eps_shu,  eps_shu = 780e-6 g_rh g_tc g_vs
               g_rh = 1.40 - 1.02 h (0.4 <= h <= 0.8), g_tc = 1.202 - 0.2337 log10(ts),
               g_vs = 1.2 e^(-0.00472 V/S)
"""

from __future__ import annotations

import math

from defs import _COMMON, _b, _col_section, _f, _j, _v

CONC_E = 3600.0 * 144.0            # ksf (3,600 ksi, 28-day modulus)
CONC_NU = 0.2
MM_PER_FT = 304.8


def _conc():
    return [{"name": "CONC", "E": CONC_E, "nu": CONC_NU, "unitWeight": 0.0}]


def _square_col(b=1.0):
    """b x b concrete column (ft); V/S = b/4 (all four faces drying)."""
    A, I = b * b, b ** 4 / 12.0
    return {"name": "CCOL", "material": "CONC", "A": A, "I33": I, "I22": I, "J": 0.1406 * b ** 4,
            "As2": 0.0, "As3": 0.0, "depth": b, "width": b}


def aci209_phi(t_minus_t0, t0, rh, vs_mm):
    g = (1.25 * t0 ** -0.118) * (1.27 - 0.67 * rh / 100.0) * (2.0 / 3.0 * (1.0 + 1.13 * math.exp(-0.0213 * vs_mm)))
    x = t_minus_t0 ** 0.6
    return x / (10.0 + x) * 2.35 * g


def aci209_eps_sh(t_minus_ts, ts, rh, vs_mm):
    h = rh / 100.0
    g_rh = 1.40 - 1.02 * h
    g_tc = 1.202 - 0.2337 * math.log10(ts)
    g_vs = 1.2 * math.exp(-0.00472 * vs_mm)
    return t_minus_ts / (35.0 + t_minus_ts) * 780e-6 * g_rh * g_tc * g_vs


def _td(**over):
    return dict({"material": "CONC", "law": "ACI 209R-92", "rh": 70.0, "vs": 0.25, "cement": "I",
                 "curing": "moist", "shrinkStart": 7.0, "creep": True, "shrinkage": True, "aging": False}, **over)


def C1() -> dict:
    """Creep: a concrete column under a sustained axial load for 1000 days (ACI 209R-92)."""
    L, P, b = 12.0, 200.0, 1.0
    sec = _square_col(b)
    A, E = sec["A"], CONC_E
    t0, dur, rh = 28.0, 1000.0, 70.0
    vs_mm = b / 4.0 * MM_PER_FT
    phi = aci209_phi(dur, t0, rh, vs_mm)
    el = P * L / (E * A)
    top = [0.0, 0.0, L]
    td = _td(creep=True, shrinkage=False)
    return {
        "id": "C1", "title": "Creep under sustained load",
        "purpose": ("A 1 ft x 1 ft concrete column (E = 3600 ksi, aging off) loaded with 200 kips at 28 days and "
                    "held for 1000 days in a staged-construction case with time-dependent material: it shortens "
                    "(sigma/E)(1 + phi) L, phi from ACI 209R-92 (RH 70%, V/S 0.25 ft = 76.2 mm, moist cured, "
                    "type I cement, t0 = 28 d, t - t0 = 1000 d; shrinkage off). The same load as a linear static "
                    "case gives the elastic part, so the ratio of the two is 1 + phi."),
        "units": "kip-ft",
        "settings": dict(_COMMON, supports="fixed base",
                         timeDependent="ACI 209R-92 creep only (shrinkage and aging off); one stage: add at "
                                       "28 days, load, hold 1000 days; results at the end of the stage"),
        "materials": _conc(), "sections": [sec],
        "stories": [{"name": "Story1", "height": L}],
        "frames": [{"id": "C1", "i": [0.0, 0.0, 0.0], "j": top, "section": "CCOL", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 0.0], "restraint": [1] * 6}],
        "patterns": [{"name": "G"}],
        "pointLoads": [{"pattern": "G", "at": top, "f": [0, 0, -P, 0, 0, 0]}],
        "ext": "timedep", "extCases": ["STG"],
        "timeDependent": td,
        "tdStaged": {"name": "STG", "age": t0, "duration": dur, "pattern": "G"},
        "checks": [
            _j("G-UZ", "Elastic shortening (linear static)", "G", top, "UZ", -el, "-PL / EA"),
            _j("STG-UZ", "Shortening after 1000 days", "STG", top, "UZ", -el * (1 + phi),
               "-(P/A)/E (1 + phi(t, t0)) L"),
            _f("STG-P", "Column axial force (unchanged by creep)", "STG", "C1", "i", "P", -P, "-P", how="signed"),
            _v("C1-phi", "Creep coefficient phi(1028 d, 28 d) = UZ(STG)/UZ(G) - 1", "phi", phi,
               "(t-t0)^0.6/(10+(t-t0)^0.6) x 2.35 g_la g_rh g_vs"),
        ],
    }


def C2() -> dict:
    """Shrinkage: an unloaded, free-standing concrete column, 1000 days of drying (ACI 209R-92)."""
    L, b = 12.0, 1.0
    sec = _square_col(b)
    ts, dur, rh = 7.0, 1000.0, 70.0
    vs_mm = b / 4.0 * MM_PER_FT
    eps = aci209_eps_sh(dur, ts, rh, vs_mm)
    top = [0.0, 0.0, L]
    td = _td(creep=False, shrinkage=True, shrinkStart=ts)
    return {
        "id": "C2", "title": "Free shrinkage",
        "purpose": ("A 1 ft x 1 ft concrete column fixed at its base only and carrying no load: added at 7 days, "
                    "when drying starts, and left for 1000 days. Nothing restrains it, so no force arises and it "
                    "shortens eps_sh(t) L with eps_sh from ACI 209R-92 (RH 70%, V/S 76.2 mm, moist cured, "
                    "ts = 7 d; creep and aging off)."),
        "units": "kip-ft",
        "settings": dict(_COMMON, supports="fixed base",
                         timeDependent="ACI 209R-92 shrinkage only (creep and aging off); one stage: add at "
                                       "7 days, hold 1000 days; results at the end of the stage"),
        "materials": _conc(), "sections": [sec],
        "stories": [{"name": "Story1", "height": L}],
        "frames": [{"id": "C1", "i": [0.0, 0.0, 0.0], "j": top, "section": "CCOL", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 0.0], "restraint": [1] * 6}],
        "patterns": [],
        "ext": "timedep", "extCases": ["STG"],
        "timeDependent": td,
        "tdStaged": {"name": "STG", "age": ts, "duration": dur, "pattern": None},
        "checks": [
            dict(_j("STG-UZ", "Top shortening after 1000 days of drying", "STG", top, "UZ", -eps * L,
                    "-eps_sh(t) L"), tol=[1e-3, 1e-2]),
            dict(_f("STG-P", "No axial force (free member)", "STG", "C1", "i", "P", 0.0, "0", how="signed"),
                 scale=1.0),
            dict(_v("C2-eps", "Shrinkage strain eps_sh(1007 d) = -UZ / L", "eps_sh", eps,
                    "(t-ts)/(35+t-ts) x 780e-6 g_rh g_tc g_vs"), tol=[1e-3, 1e-2]),
        ],
    }


def C3() -> dict:
    """N6 (pushover, plastic column hinges) with the ETABS hinges assigned through database tables."""
    import cases_nonlin
    c = cases_nonlin.N6()
    po = c.pop("pushover")
    return dict(c, id="C3", title="Pushover, plastic hinges (hinges by table)",
                purpose=c["purpose"] + (" Same model as N6; ETABS 23's API has no FrameObj.SetHingeAssigns, so "
                                        "its extension finds the hinge tables at run time and assigns "
                                        "elastic-perfectly-plastic M3 hinges through them."),
                ext="timedep", extCases=[p["name"] for p in po], extPushover=po)


CASES = {"C1": C1, "C2": C2, "C3": C3}
