"""`import runner.op`: runner_support/op.py (runner/__init__.py)."""
import sys

from runner_support import op as _real

sys.modules[__name__] = _real
