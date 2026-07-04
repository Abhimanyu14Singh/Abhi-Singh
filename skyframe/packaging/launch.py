"""PyInstaller entry point for the SkyFrame desktop app."""

import sys

from skyframe.desktop import main

if __name__ == "__main__":
    sys.exit(main())
