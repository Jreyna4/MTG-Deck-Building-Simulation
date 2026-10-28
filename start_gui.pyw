"""Double-click this file to open the simulator window (Windows).

On Mac or Linux, run  python3 -m mtgsim  in this folder instead.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtgsim.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
