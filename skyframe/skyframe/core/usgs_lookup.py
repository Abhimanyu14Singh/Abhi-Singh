"""Optional online USGS seismic design-maps lookup (CONTRACT "Wave C:
regional seismic / wind generators, code spectra, spectrum file import,
USGS lookup and regional material / steel libraries").

Calls the USGS Seismic Design Web Services
(``https://earthquake.usgs.gov/ws/designmaps/<edition>.json``) for a site
latitude / longitude, risk category and site class and returns the design
values (SS, S1, SMS, SM1, SDS, SD1, TL, PGA_M) plus, for ASCE 7-22, the
multi-period design / MCE_R spectra that feed the ``asce7_22_mprs``
spectrum and the ``asce7_22`` auto-lateral generator.

The service is optional: when the network or the service is unreachable
(the usual case inside a locked-down build or office network)
:class:`UsgsUnavailable` is raised with a message that says so and that
the values can still be typed in by hand.  Nothing in the model depends on
this module; it never runs unless called.

Response parsing is defensive: the documented JSON layout is
``{"request": {...}, "response": {"data": {...}, "metadata": {...}}}``
with lower-case keys (``ss``, ``s1``, ``sms``, ``sm1``, ``sds``, ``sd1``,
``tl``, ``pgam``, ``multiPeriodDesignSpectrum: {periods, ordinates}``,
...); spectra given as ``[[T, Sa], ...]`` are accepted too.  The key
names were taken from memory of the service documentation (flagged in
docs/REVIEW_FLAGS.md "Wave C"), so every field is optional.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

BASE_URL = "https://earthquake.usgs.gov/ws/designmaps"
EDITIONS = {"asce7-22": ("A", "B", "BC", "C", "CD", "D", "DE", "E",
                         "Default"),
            "asce7-16": ("A", "B", "C", "D", "E", "Default")}
RISK = ("I", "II", "III", "IV")
VALUE_KEYS = {"SS": "ss", "S1": "s1", "SMS": "sms", "SM1": "sm1",
              "SDS": "sds", "SD1": "sd1", "TL": "tl", "PGA_M": "pgam",
              "Fa": "fa", "Fv": "fv", "SDC": "sdc"}
SPECTRUM_KEYS = {"mprs_design": "multiPeriodDesignSpectrum",
                 "mprs_mcer": "multiPeriodMCErSpectrum",
                 "two_period_design": "twoPeriodDesignSpectrum",
                 "two_period_mcer": "twoPeriodMCErSpectrum"}
MANUAL_HINT = ("enter SS/S1 or the 22 multi-period ordinates by hand "
               "(they are on the USGS / ATC hazard tool printout)")


class UsgsUnavailable(RuntimeError):
    """The USGS web service could not be reached or answered badly."""


def build_url(latitude: float, longitude: float,
              edition: str = "asce7-22", site_class: str = "D",
              risk_category: str = "II", title: str = "SkyFrame") -> str:
    """Validated request URL."""
    for lab, v, lim in (("latitude", latitude, 90.0),
                        ("longitude", longitude, 180.0)):
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) or abs(v) > lim):
            raise ValueError(f"{lab} must be a number in [-{lim:g}, "
                             f"{lim:g}]")
    if edition not in EDITIONS:
        raise ValueError(f"edition must be one of {tuple(EDITIONS)}")
    if site_class not in EDITIONS[edition]:
        raise ValueError(f"site_class must be one of {EDITIONS[edition]} "
                         f"for {edition}")
    if risk_category not in RISK:
        raise ValueError(f"risk_category must be one of {RISK}")
    q = urllib.parse.urlencode({"latitude": float(latitude),
                                "longitude": float(longitude),
                                "riskCategory": risk_category,
                                "siteClass": site_class,
                                "title": str(title or "SkyFrame")[:60]})
    return f"{BASE_URL}/{edition}.json?{q}"


def _spectrum(v) -> Optional[dict]:
    if isinstance(v, dict):
        ts, ss = v.get("periods"), v.get("ordinates")
        if (isinstance(ts, list) and isinstance(ss, list)
                and len(ts) == len(ss) and ts):
            return {"periods": [float(t) for t in ts],
                    "ordinates": [float(s) for s in ss]}
    if isinstance(v, list) and v and all(
            isinstance(p, (list, tuple)) and len(p) == 2 for p in v):
        return {"periods": [float(p[0]) for p in v],
                "ordinates": [float(p[1]) for p in v]}
    return None


def parse_response(payload, edition: str = "asce7-22") -> dict:
    """``{"values": {SS, S1, ...}, "spectra": {...}, "mprs_ordinates"?}``
    from the service JSON (``UsgsUnavailable`` when it holds no data)."""
    if isinstance(payload, (bytes, str)):
        try:
            payload = json.loads(payload)
        except ValueError as exc:
            raise UsgsUnavailable(f"USGS answer is not JSON ({exc}); "
                                  f"{MANUAL_HINT}") from None
    if not isinstance(payload, dict):
        raise UsgsUnavailable(f"USGS answer has no data; {MANUAL_HINT}")
    resp = payload.get("response", payload)
    if isinstance(resp, list):              # some editions wrap in a list
        resp = resp[0] if resp else {}
    data = resp.get("data") if isinstance(resp, dict) else None
    if not isinstance(data, dict):
        msg = payload.get("message") or payload.get("status") or "no data"
        raise UsgsUnavailable(f"USGS answer has no design data ({msg}); "
                              f"{MANUAL_HINT}")
    low = {str(k).lower(): v for k, v in data.items()}
    values = {}
    for out, key in VALUE_KEYS.items():
        v = low.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) \
                and math.isfinite(v):
            values[out] = float(v)
        elif isinstance(v, str) and out == "SDC":
            values[out] = v
    spectra = {}
    for out, key in SPECTRUM_KEYS.items():
        sp = _spectrum(low.get(key.lower()))
        if sp:
            spectra[out] = sp
    if not values and not spectra:
        raise UsgsUnavailable(f"USGS answer holds no design values; "
                              f"{MANUAL_HINT}")
    out = {"edition": edition, "values": values, "spectra": spectra}
    mp = spectra.get("mprs_design")
    if mp:
        out["mprs_pairs"] = [[t, s] for t, s in zip(mp["periods"],
                                                    mp["ordinates"])]
    return out


def lookup(latitude: float, longitude: float, edition: str = "asce7-22",
           site_class: str = "D", risk_category: str = "II",
           title: str = "SkyFrame", timeout: float = 8.0,
           opener: Optional[Callable] = None) -> dict:
    """Query the service.  ``opener(url, timeout)`` -> bytes (default
    :func:`urllib.request.urlopen`) is injectable for tests.  Raises
    ``ValueError`` on bad input and :class:`UsgsUnavailable` when the
    service cannot be used."""
    url = build_url(latitude, longitude, edition, site_class,
                    risk_category, title)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not 0.0 < timeout <= 60.0):
        raise ValueError("timeout must be in (0, 60] s")
    try:
        if opener is None:
            req = urllib.request.Request(
                url, headers={"User-Agent": "SkyFrame (design-maps lookup)"})
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                raw = fh.read()
        else:
            raw = opener(url, timeout)
    except urllib.error.HTTPError as exc:
        raise UsgsUnavailable(f"USGS design-maps service answered HTTP "
                              f"{exc.code}; {MANUAL_HINT}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UsgsUnavailable(f"USGS design-maps service unreachable "
                              f"({reason}); {MANUAL_HINT}") from None
    out = parse_response(raw, edition)
    out.update({"url": url, "latitude": float(latitude),
                "longitude": float(longitude), "site_class": site_class,
                "risk_category": risk_category})
    return out
