"""Model-wide Rayleigh damping options for springs, links and hinges.

``BuildingModel.damping_options`` is a plain dict::

    {"rayleigh_on_links": "none" | "elastic_only" | "all",
     "rayleigh_stiffness_basis": "committed" | "current" | "initial"}

It decides whether the zeroLength / twoNodeLink elements the engine
creates (plastic-hinge springs, grounded point springs, link elements,
panel-zone springs) take part in the stiffness-proportional Rayleigh
damping of direct-integration time-history cases.  OpenSees ``zeroLength``
and ``twoNodeLink`` carry NO Rayleigh term unless built with
``-doRayleigh``; SkyFrame historically never set it, so

* a nonlinear-TH hinged model was damped by its elastic members only
  (an EPP SDOF ran +0.78 % against the classically damped SDOF), and
* a linear TH of a model on grounded springs / elastic links was
  under-damped relative to the case's Rayleigh ratio (the spring part of
  the stiffness carried no ``a1 K`` term although ``a0/a1`` are fitted to
  the modes OF the sprung model).

``"none"`` (default) is that exact pre-existing behaviour (bit-identical
results); ``to_dict`` emits the key ONLY when the options differ from the
defaults, so default models serialize byte-identically.

Per element kind (engine: :mod:`skyframe.engine.link_rayleigh`):

=====================================  =========  ============  =====
element                                ``none``   elastic_only  all
=====================================  =========  ============  =====
grounded linear point / line / area    --         yes           yes
springs, named LINEAR springs
elastic link elements                  --         yes           yes
panel-zone springs (elastic)           --         yes           yes
hinge springs (pushover / ASCE 41 /    --         --            yes
user hinges) in nonlinear TH
named NONLINEAR springs (c/t-only,     --         --            yes
gap, multilinear), compression-only
vertical springs
material links: isolator (bilinear),   --         --            yes
multilinear, takeda / pivot, gap, hook
viscous dampers                        --         --            --
friction / rubber bearing elements     --         --            --
(singleFP, triple FP, flat slider,
Bouc-Wen)
stiff hinge-tie / dummy springs        --         --            --
=====================================  =========  ============  =====

Never damped: a viscous damper IS a dashpot (Rayleigh on its Maxwell
spring would double-count damping); the dedicated bearing elements carry
their own hysteretic dissipation and (friction) a near-rigid initial
stiffness whose proportional damping is the very artefact CSI added the
"effective stiffness" link damping basis to avoid (OpenSees
TripleFrictionPendulum has no Rayleigh switch at all); hinge ties and the
zero-free-DOF dummy stand in for constraints.

``rayleigh_stiffness_basis`` = the stiffness the participating elements'
``a1 K`` uses (the CSI link "stiffness used for stiffness-proportional
damping" option): ``committed`` (default; last converged tangent —
OpenSees ``betaKcomm``, the engine's nonlinear-TH choice), ``current``
(trial tangent, ``betaK``) or ``initial`` (``betaKinit``, the CSI
default — puts ``a1 k_theta theta_dot`` on the stiff hinge springs after
yield).  For linear-elastic elements the three are identical.

Recommended: ``{"rayleigh_on_links": "all", "rayleigh_stiffness_basis":
"committed"}`` — reproduces classical Rayleigh damping (the whole
assembled stiffness, springs included, is damped at the case's ratio)
without post-yield damping artefacts.
"""

from __future__ import annotations

from typing import Dict

RAYLEIGH_ON_LINKS = ("none", "elastic_only", "all")
RAYLEIGH_STIFFNESS_BASES = ("committed", "current", "initial")
DAMPING_OPTION_KEYS = ("rayleigh_on_links", "rayleigh_stiffness_basis")
RECOMMENDED_DAMPING_OPTIONS = {"rayleigh_on_links": "all",
                               "rayleigh_stiffness_basis": "committed"}


def damping_defaults() -> dict:
    """A fresh copy of the default options (links undamped = legacy)."""
    return {"rayleigh_on_links": "none",
            "rayleigh_stiffness_basis": "committed"}


def normalized(opts) -> dict:
    """Defaults overlaid with ``opts`` (no validation)."""
    out = damping_defaults()
    if isinstance(opts, dict):
        out.update(opts)
    return out


def effective(model) -> Dict[str, str]:
    """The active options; anything malformed -> defaults."""
    o = normalized(getattr(model, "damping_options", None))
    if o.get("rayleigh_on_links") not in RAYLEIGH_ON_LINKS:
        o["rayleigh_on_links"] = "none"
    if o.get("rayleigh_stiffness_basis") not in RAYLEIGH_STIFFNESS_BASES:
        o["rayleigh_stiffness_basis"] = "committed"
    return {k: o[k] for k in DAMPING_OPTION_KEYS}


def validate_damping_options(model) -> None:
    """Raise ``ValueError`` on malformed ``model.damping_options``."""
    opts = getattr(model, "damping_options", None)
    if opts is None:
        return
    if not isinstance(opts, dict):
        raise ValueError("damping_options must be an object")
    bad = sorted(set(opts) - set(DAMPING_OPTION_KEYS))
    if bad:
        raise ValueError(f"damping_options: unknown keys {bad} "
                         f"(allowed: {list(DAMPING_OPTION_KEYS)})")
    o = normalized(opts)
    if o["rayleigh_on_links"] not in RAYLEIGH_ON_LINKS:
        raise ValueError(
            f"damping_options.rayleigh_on_links must be one of "
            f"{RAYLEIGH_ON_LINKS}, got {o['rayleigh_on_links']!r}")
    if o["rayleigh_stiffness_basis"] not in RAYLEIGH_STIFFNESS_BASES:
        raise ValueError(
            f"damping_options.rayleigh_stiffness_basis must be one of "
            f"{RAYLEIGH_STIFFNESS_BASES}, got "
            f"{o['rayleigh_stiffness_basis']!r}")


def is_default(model) -> bool:
    opts = getattr(model, "damping_options", None)
    return opts is None or normalized(opts) == damping_defaults()


def damping_to_dict(model) -> dict:
    """``{"damping_options": {...}}`` when not the defaults, else ``{}``."""
    if is_default(model):
        return {}
    return {"damping_options": {k: normalized(model.damping_options)[k]
                                for k in DAMPING_OPTION_KEYS}}


def damping_from_dict(model, d: dict) -> None:
    """Read ``damping_options`` (absent -> defaults)."""
    v = d.get("damping_options")
    if v is None:
        model.damping_options = damping_defaults()
        return
    if not isinstance(v, dict):
        raise ValueError("damping_options must be an object")
    model.damping_options = dict(v)
