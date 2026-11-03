"""`import runner.outage`: runner_support/outage.py (runner/__init__.py)."""
import sys

from runner_support import outage as _real

sys.modules[__name__] = _real
