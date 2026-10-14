# PyInstaller spec for the SkyFrame desktop app.
# Build (from the skyframe/ project root):
#   pyinstaller --noconfirm packaging/skyframe.spec
import sys

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

# the skyframe package itself (includes api/web assets via package data)
for pkg in ("skyframe",):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

# openseespy ships per-platform binary packages
_platform_pkgs = {
    "win32": "openseespywin",
    "linux": "openseespylinux",
    "darwin": "openseespymac",
}
ops_pkg = _platform_pkgs.get(sys.platform)
if ops_pkg:
    d, b, h = collect_all(ops_pkg)
    datas += d; binaries += b; hiddenimports += h
hiddenimports += ["openseespy", "openseespy.opensees"]

# pywebview's platform backends are imported dynamically
try:
    d, b, h = collect_all("webview")
    datas += d; binaries += b; hiddenimports += h
except Exception:
    pass  # server-only build without pywebview installed

# Rhino .3dm import (optional: without it the app reports how to install it)
try:
    d, b, h = collect_all("rhino3dm")
    datas += d; binaries += b; hiddenimports += h
except Exception:
    pass

# E2: Intel MKL PARDISO (optional "fast" extra).  pypardiso is pure Python;
# the MKL runtime ships as data files of the mkl / intel-openmp / tbb wheels
# (<prefix>/Library/bin/*.dll on Windows, <prefix>/lib/lib*.so* on Linux),
# which collect_all cannot see -> add them as binaries at the bundle root,
# where engine/pardiso_solver.find_mkl_rt looks (sys._MEIPASS).
try:
    import glob as _glob
    import os as _os
    import site as _site
    d, b, h = collect_all("pypardiso")
    datas += d; binaries += b; hiddenimports += h
    hiddenimports += ["scipy.sparse", "scipy.sparse.linalg",
                      "scipy.sparse.linalg._eigen.arpack"]
    _pats = (["mkl_*.dll", "libiomp5md.dll", "tbb*.dll", "tcm*.dll",
              "umf*.dll"] if sys.platform == "win32" else
             ["libmkl_*.so*", "libiomp5.so", "libtbb*.so*", "libtcm*.so*",
              "libumf*.so*"])
    _roots = {sys.prefix, sys.base_prefix}
    for _sp in _site.getsitepackages():
        _roots.add(_os.path.dirname(_os.path.dirname(_sp)))
    _seen = set()
    for _r in _roots:
        for _sub in ("Library/bin", "lib", "bin", ""):
            for _pat in _pats:
                for _f in _glob.glob(_os.path.join(_r, _sub, _pat)):
                    if _os.path.basename(_f) not in _seen:
                        _seen.add(_os.path.basename(_f))
                        binaries.append((_f, "."))
    # Intel Simplified Software License text must ship with the MKL runtime
    # (redistribution condition) -> licenses/<dist>/LICENSE.txt in the bundle.
    for _sp in _site.getsitepackages():
        for _lic in _glob.glob(_os.path.join(_sp, "*.dist-info", "LICENSE*")):
            _dist = _os.path.basename(_os.path.dirname(_lic)).split("-")[0].lower()
            if _dist in ("mkl", "onemkl_license", "intel_openmp", "tbb",
                         "pypardiso"):
                datas.append((_lic, _os.path.join("licenses", _dist)))
except Exception:
    pass  # built without the "fast" extra: the app runs on OpenSees only

# OpenSeesPy licence (free for research/education/internal use; commercial
# redistribution needs a licence - docs/REVIEW_FLAGS.md) ships with the bundle.
try:
    import glob as _glob2
    import os as _os2
    import site as _site2
    for _sp in _site2.getsitepackages():
        for _lic in _glob2.glob(_os2.path.join(_sp, "openseespy*", "LICENSE*")):
            datas.append((_lic, _os2.path.join("licenses", _os2.path.basename(_os2.path.dirname(_lic)))))
except Exception:
    pass

a = Analysis(
    ["launch.py"],
    pathex=[".."],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide2", "PySide6"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="SkyFrame",
    console=False,
    icon=None,
)
# console-subsystem twin for headless/CI use (--server-only, --smoke):
# valid stdio and no GUI subsystem quirks on Windows
exe_cli = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="SkyFrameCLI",
    console=True,
    icon=None,
)
coll = COLLECT(
    exe,
    exe_cli,
    a.binaries,
    a.datas,
    name="SkyFrame",
)

# .NET Framework config next to each exe: pythonnet (pywebview's native
# window) loads Python.Runtime.dll with Assembly.LoadFrom; files extracted from
# a downloaded ZIP carry the internet-zone mark, and without this switch .NET
# refuses them ("Failed to resolve Python.Runtime.Loader.Initialize").
if sys.platform == "win32":
    import os as _os3
    _cfg = ('<?xml version="1.0" encoding="utf-8"?>\n<configuration>\n'
            '  <runtime>\n    <loadFromRemoteSources enabled="true"/>\n'
            '  </runtime>\n</configuration>\n')
    for _exe in ("SkyFrame", "SkyFrameCLI"):
        with open(_os3.path.join(DISTPATH, "SkyFrame", _exe + ".exe.config"),
                  "w", encoding="utf-8") as _fh:
            _fh.write(_cfg)
