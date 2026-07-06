"""SkyFrame — a personal building analysis studio powered by OpenSees.

An ETABS-style structural analysis application:
  * building-centric modelling (grids, stories, frames, rigid diaphragms)
  * research-grade solver: the open-source OpenSees framework (OpenSeesPy)
  * linear static load cases, load combinations and eigenvalue (modal) analysis
  * story drifts, story shears, base reactions, member forces, mode shapes
"""

__version__ = "1.6.0"

from .core.model import (  # noqa: F401
    BuildingModel,
    Material,
    FrameSection,
    Story,
    GridSystem,
    FrameMember,
    LoadPattern,
    LoadCase,
    LoadCombo,
)
from .core.builder import quick_building  # noqa: F401
from .engine.opensees_engine import OpenSeesEngine  # noqa: F401
