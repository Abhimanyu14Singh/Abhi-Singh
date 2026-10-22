"""Can Python drive ETABS 23 on this machine?  Run once, before any test.

    python probe_etabs.py                    (or: py -3.11 probe_etabs.py)
    python probe_etabs.py --etabs "D:\\Apps\\ETABS 23\\ETABS.exe"

It tries two ways of reaching the ETABS API, opens ETABS (visible), makes a
blank model in kip-ft, reads the version, and closes ETABS without saving.
Nothing is written anywhere.  It prints one line per step and, at the end,
which way works.  Paste the whole output back.
"""

import argparse
import os
import platform
import sys
import traceback

DEFAULT_EXE = r"C:\Program Files\Computers and Structures\ETABS 23\ETABS.exe"
KIP_FT_F = 4          # eUnits.kip_ft_F


def say(msg):
    print(msg, flush=True)


def try_comtypes(exe):
    """CSI's documented Python route: comtypes + the registered ETABSv1 type library."""
    import comtypes.client
    helper = comtypes.client.CreateObject("ETABSv1.Helper")
    say("  comtypes: ETABSv1.Helper created")
    import comtypes.gen.ETABSv1 as E          # generated from the registered type library
    helper = helper.QueryInterface(E.cHelper)
    return helper.CreateObject(exe)


def try_pythonnet(exe):
    """.NET route: load ETABSv1.dll directly with pythonnet (no COM registration needed)."""
    import clr                                    # pythonnet
    dll = os.path.join(os.path.dirname(exe), "ETABSv1.dll")
    clr.AddReference(dll)
    import ETABSv1                                # noqa: E402
    helper = ETABSv1.cHelper(ETABSv1.Helper())
    say("  pythonnet: ETABSv1.dll loaded")
    return helper.CreateObject(exe)


def exercise(etabs, how):
    ret = etabs.ApplicationStart()
    say(f"  {how}: ETABS started (return {ret})")
    model = etabs.SapModel
    r1 = model.InitializeNewModel(KIP_FT_F)
    r2 = model.File.NewBlank()
    say(f"  {how}: blank model in kip-ft (returns {r1}, {r2})")
    v = model.GetVersion()
    say(f"  {how}: ETABS version {v}")
    try:
        etabs.ApplicationExit(False)
        say(f"  {how}: ETABS closed")
    except Exception as exc:  # noqa: BLE001
        say(f"  {how}: could not close ETABS ({exc}); close it by hand")
    return r1 == 0 and r2 == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--etabs", default=DEFAULT_EXE, help="full path to ETABS.exe")
    a = ap.parse_args()
    say(f"Python {platform.python_version()} ({platform.architecture()[0]}) on {platform.platform()}")
    say(f"ETABS.exe {'found' if os.path.exists(a.etabs) else 'NOT FOUND'} at {a.etabs}")
    if not os.path.exists(a.etabs):
        say("Give the right path with --etabs \"...\\ETABS.exe\"")
        return 2
    works = []
    for how, fn in (("comtypes", try_comtypes), ("pythonnet", try_pythonnet)):
        say(f"Trying {how} ...")
        try:
            etabs = fn(a.etabs)
            if exercise(etabs, how):
                works.append(how)
                break                          # one working route is enough
        except ModuleNotFoundError as exc:
            say(f"  {how}: not installed ({exc.name})")
        except Exception as exc:  # noqa: BLE001
            say(f"  {how}: failed: {exc.__class__.__name__}: {exc}")
            say("    " + traceback.format_exc().strip().splitlines()[-1])
    say("")
    if works:
        say(f"RESULT: ETABS API works through {works[0]}.")
        return 0
    say("RESULT: no route worked yet. Paste this output back.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
