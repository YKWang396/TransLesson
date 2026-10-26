"""Frozen entry point shared by GUI and pipe-enabled worker executables."""
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    os.environ.setdefault("MATHBRIDGE_NODE", str(Path(sys._MEIPASS) / "runtime" / "node.exe"))

from mathbridge.app import main

raise SystemExit(main())
