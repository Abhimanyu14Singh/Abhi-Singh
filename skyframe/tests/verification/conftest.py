"""Verification-suite pytest hooks.

When the environment variable ``SKYFRAME_VERIF_JSON`` names a file, every
comparison recorded by ``_vhelp.check`` during the session is written there
as JSON at session end (``make_verification_md.py`` sets it and renders
``docs/VERIFICATION.md``).  Ordinary test runs write nothing.
"""

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def pytest_sessionfinish(session, exitstatus):
    out = os.environ.get("SKYFRAME_VERIF_JSON")
    if not out:
        return
    import _vhelp
    Path(out).write_text(json.dumps(_vhelp.RECORDS, indent=1))
