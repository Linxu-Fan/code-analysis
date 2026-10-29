import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) == 1:
        from codescope.gui.app import main
    else:
        from codescope.cli import main
    sys.exit(main())
