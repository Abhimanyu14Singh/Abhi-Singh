#!/usr/bin/env bash
# Build the SkyFrame standalone desktop app locally (no cloud CI needed).
#
#   ./packaging/build.sh
#
# Produces packaging/dist/SkyFrame/  (run ./SkyFrame, or ./SkyFrameCLI --server-only)
# and a compressed SkyFrame-<os>.tar.gz next to it.
set -euo pipefail
cd "$(dirname "$0")/.."          # skyframe project root

echo "==> Installing SkyFrame + packaging deps (clean, non-editable)"
# A non-editable install is REQUIRED: PyInstaller's collect_all bundles the
# files from site-packages, and an editable install can leave it bundling
# stale staged copies (old web/ assets, old server routes). Force a clean
# copy of the current tree into site-packages before freezing.
rm -rf build skyframe.egg-info
pip uninstall -y skyframe >/dev/null 2>&1 || true
pip install -q '.[desktop,dev]'

echo "==> Sanity: OpenSees imports"
python -c "import openseespy.opensees as ops; print('    opensees OK')"

echo "==> Running validation suite"
python -m pytest tests/ -q

echo "==> Freezing with PyInstaller"
rm -rf packaging/dist packaging/build
pyinstaller --noconfirm \
    --distpath packaging/dist --workpath packaging/build \
    packaging/skyframe.spec

echo "==> Smoke-testing the frozen app"
timeout 120 packaging/dist/SkyFrame/SkyFrameCLI --smoke

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
echo "==> Packaging SkyFrame-${os}.tar.gz"
tar -C packaging/dist -czf "packaging/SkyFrame-${os}.tar.gz" SkyFrame

echo "==> Done: packaging/dist/SkyFrame/  and  packaging/SkyFrame-${os}.tar.gz"
du -sh packaging/dist/SkyFrame "packaging/SkyFrame-${os}.tar.gz"
