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
