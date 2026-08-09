"""
check_import_paths.py — run this once. Add nothing, change nothing else.

Prints the EXACT file path Python is actually importing core.signals and
core.features from. Run this from the SAME PyCharm run configuration you
use for run_backtest.py, then again with the one you use for
run_performance.py, and compare the two printed paths.

If they point to two DIFFERENT files on disk, that's the bug: you have a
leftover duplicate `core/` folder somewhere, and different scripts are
picking up different copies.
"""

import core.signals
import core.features
import core.data
import os

print("=" * 70)
print("core.signals  imported from:", os.path.abspath(core.signals.__file__))
print("core.features imported from:", os.path.abspath(core.features.__file__))
print("core.data     imported from:", os.path.abspath(core.data.__file__))
print("Current working directory: ", os.getcwd())
print("=" * 70)