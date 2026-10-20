"""SkyFrame engine service: the analysis engine as a self-contained part.

Hosts (Supa in a browser, SomOptLite on the desktop, SkyFrame's own UI)
reach the OpenSees engine through this package and nothing else:

* :mod:`.supa` -- drop-in for Supa's ``solveFem``: Supa's element-level
  ``FemModel`` (kip, ft) in, ``FemResult`` out, solved by OpenSees.
* :mod:`.stability` -- the pre-solve check: DOFs no element resists,
  parts of the model not connected to the ground.  It reports; it never
  hides a mechanism behind springs.
* :mod:`.service` -- a local HTTP service on 127.0.0.1 (version, solve,
  progress, cancel, result) with the CORS / private-network answers a
  browser page needs to call it.
* ``python -m skyframe.engine_service`` -- ``serve``, ``solve``, ``version``.

The formats are versioned JSON Schemas in ``schema/``; see
``docs/ENGINE_SERVICE.md``.
"""

ENGINE_API_VERSION = "0.1.0"
