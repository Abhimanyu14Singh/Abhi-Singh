#!/bin/sh
# Truss Lab installer for Linux.
# Copies the app into ~/.local/share/trusslab and adds an applications-menu
# entry (plus a Desktop launcher if you have a Desktop folder).
# No sudo needed. Nothing is downloaded.
set -e
cd "$(dirname "$0")"

echo ""
echo "  Truss Lab installer"
echo "  -------------------"

if [ ! -f "TrussLab.html" ]; then
  echo "  Could not find TrussLab.html next to this installer. Unzip the whole folder first."
  exit 1
fi

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/trusslab"
mkdir -p "$DEST" "$DATA/applications"
cp -f TrussLab.html "$DEST/TrussLab.html"

# Simple icon (triangle truss) for the menu entry.
cat > "$DEST/trusslab.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#171d2b"/><path d="M10 48 L32 14 L54 48 Z M32 14 L32 48" fill="none" stroke="#4f9cff" stroke-width="5" stroke-linejoin="round"/></svg>
SVG

DESKTOP_ENTRY="[Desktop Entry]
Type=Application
Name=Truss Lab
Comment=Virtual work truss explorer
Exec=xdg-open \"$DEST/TrussLab.html\"
Icon=$DEST/trusslab.svg
Terminal=false
Categories=Education;Science;Engineering;"

printf '%s\n' "$DESKTOP_ENTRY" > "$DATA/applications/trusslab.desktop"
chmod +x "$DATA/applications/trusslab.desktop"
echo "  Copied app to: $DEST/TrussLab.html"
echo "  Menu entry:    $DATA/applications/trusslab.desktop"

DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESK" ]; then
  printf '%s\n' "$DESKTOP_ENTRY" > "$DESK/trusslab.desktop"
  chmod +x "$DESK/trusslab.desktop"
  echo "  Desktop launcher: $DESK/trusslab.desktop"
fi

echo ""
echo "  Done! Find 'Truss Lab' in your applications menu."
if [ -z "$TRUSSLAB_NO_OPEN" ] && command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$DEST/TrussLab.html" >/dev/null 2>&1 &
fi
