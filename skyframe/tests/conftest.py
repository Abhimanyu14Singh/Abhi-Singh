"""Pytest configuration for the SkyFrame validation-test silo.

Puts the skyframe package root (the directory that CONTAINS the ``skyframe``
package) on sys.path so ``import skyframe`` works when pytest is run from the
repo checkout (e.g. ``cd skyframe && python -m pytest tests/``).
"""

import sys
from pathlib import Path

# .../skyframe/tests/conftest.py -> parents[1] == .../skyframe (package root)
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
