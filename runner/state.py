"""`import runner.state`: runner_support/state.py (runner/__init__.py)."""
import sys

from runner_support import state as _real

sys.modules[__name__] = _real
