"""`import runner.regular_file`: runner_support/regular_file.py (runner/__init__.py)."""
import sys

from runner_support import regular_file as _real

sys.modules[__name__] = _real
