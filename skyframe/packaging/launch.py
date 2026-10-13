"""PyInstaller entry point for the SkyFrame desktop app."""

import multiprocessing
import sys

if __name__ == "__main__":
    # E1: the spawn solver workers (skyframe/engine/spawn_pool.py) start
    # this same executable; freeze_support() turns such a start into the
    # worker and never reaches the GUI / server below.  Must come first.
    multiprocessing.freeze_support()
    from skyframe.desktop import main
    sys.exit(main())
