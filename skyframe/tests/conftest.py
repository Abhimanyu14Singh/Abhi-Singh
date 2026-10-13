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

# Model History (core/model_history.py): automatic checkpoints taken by the
# server hooks (save / before analysis) go to a throw-away folder in tests
# instead of ~/.skyframe/models/.history.
import os as _os  # noqa: E402
import tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("SKYFRAME_HISTORY_DIR",
                       _tempfile.mkdtemp(prefix="skyframe-history-"))
