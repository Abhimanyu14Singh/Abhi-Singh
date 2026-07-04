#!/usr/bin/env bash
# Build the SkyFrame standalone desktop app locally (no cloud CI needed).
#
#   ./packaging/build.sh
#
# Produces packaging/dist/SkyFrame/  (run ./SkyFrame, or ./SkyFrameCLI --server-only)
# and a compressed SkyFrame-<os>.tar.gz next to it.
set -euo pipefail
cd "$(dirname "$0")/.."          # skyframe project root

echo "==> Installing SkyFrame + packaging deps"
pip install -q -e '.[desktop,dev]'

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
