"""Speed group: the SP1 benchmark at 10 and 40 stories (kip, ft).

Both come from SP1's generator (``cases_nonlin.rc_frame``): the same 4 x 3 bays of 25 ft, 13 ft stories,
8 in thin-shell slabs drawn as nm x nm separate area objects per bay in both programs (ETABS does not mesh
them further), beams split at every slab node, fixed column bases, the same load patterns, 12 modes and a
P-Delta case.  The runner times them like SP1 (``benchmark`` in run_etabs.py, ``speed_skyframe.py`` for
SkyFrame on the same PC).

    SP2  10 stories, slab mesh 2.5 ft (nm 10): 12,000 shells, 3,300 frames, ~76,000 equations
    SP3  40 stories, slab mesh 5 ft   (nm 5):  12,000 shells, 7,000 frames, ~81,000 equations

SP3 uses the coarser 5 ft mesh: at SP1's 2.5 ft it would be 48,000 shells, 13,200 frames and ~305,000
equations.  SkyFrame solves that (measured on 4 cores without PARDISO: 77 s linear, 144 s P-Delta, 2.9 GB),
but ETABS would be built through ~250,000 COM calls and analysed three times (two timed parts, then the
results run), and its outputs would be too big to push: ~65 MB of joint results in SP3.etabs.json, plus
analysis files over GitHub's 100 MB limit in the pushed SkyFrameVerification folder.

Both are marked "slow": the pytest suite skips running them unless SKYFRAME_SLOW=1.
"""

from __future__ import annotations

from cases_nonlin import rc_frame


def SP2() -> dict:
    """A 10-story RC frame, 4 x 3 bays of 25 ft, shell slabs meshed at 2.5 ft in both programs."""
    return dict(rc_frame(
        "SP2", "Speed benchmark: 10-story RC frame",
        ("SP1 at 10 stories: 4 x 3 bays of 25 ft, 8 in slabs as thin shells meshed at 2.5 ft (12,000 shell "
         "elements, 3,300 frame elements, beams split at every slab node, about 76,000 equations). Lateral "
         "load 20 kips x level number. Timed twice: linear (self-weight, superimposed load, lateral load and "
         "12 modes) and nonlinear (P-Delta static under all three together). ETABS uses its advanced solver "
         "on all cores."),
        ns=10, nm=10, Fk=20.0), slow=True)


def SP3() -> dict:
    """A 40-story RC frame, 4 x 3 bays of 25 ft, shell slabs meshed at 5 ft in both programs."""
    return dict(rc_frame(
        "SP3", "Speed benchmark: 40-story RC frame",
        ("SP1 at 40 stories with 3 ft square columns: 4 x 3 bays of 25 ft, 8 in slabs as thin shells meshed "
         "at 5 ft (12,000 shell elements, 7,000 frame elements, beams split at every slab node, about 81,000 "
         "equations). Lateral load 1.5 kips x level number. Timed twice: linear (self-weight, superimposed "
         "load, lateral load and 12 modes) and nonlinear (P-Delta static under all three together). ETABS "
         "uses its advanced solver on all cores. The slab mesh is coarser than SP1's 2.5 ft (identical in "
         "both programs) so the model and its results stay a practical size."),
        ns=40, nm=5, Fk=1.5, col=3.0), slow=True)


CASES = {"SP2": SP2, "SP3": SP3}
