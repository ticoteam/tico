"""`import runner.freshness`: runner_support/freshness.py (runner/__init__.py)."""
import sys

from runner_support import freshness as _real

sys.modules[__name__] = _real
