#!/bin/bash
# Truss Lab installer for macOS.
# Copies the app into ~/Applications/Truss Lab and puts a shortcut on the Desktop.
# No admin password needed. Nothing is downloaded.
cd "$(dirname "$0")" || exit 1

echo ""
echo "  Truss Lab installer"
echo "  -------------------"

if [ ! -f "TrussLab.html" ]; then
  echo "  Could not find TrussLab.html next to this installer."
  echo "  Please unzip the whole folder first, then run the installer again."
  read -r -p "  Press Return to close." _
  exit 1
fi

DEST="$HOME/Applications/Truss Lab"
mkdir -p "$DEST"
cp -f "TrussLab.html" "$DEST/TrussLab.html"
echo "  Copied app to: $DEST/TrussLab.html"

if [ -d "$HOME/Desktop" ]; then
  ln -sf "$DEST/TrussLab.html" "$HOME/Desktop/Truss Lab.html"
  echo "  Shortcut added: Desktop/Truss Lab.html"
fi

echo ""
echo "  Done! Opening Truss Lab in your web browser..."
open "$DEST/TrussLab.html"
