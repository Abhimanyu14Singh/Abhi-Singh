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
except Exception:
    pass  # built without the "fast" extra: the app runs on OpenSees only

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
