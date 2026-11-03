"""`import runner.close_calls`: runner_support/close_calls.py (runner/__init__.py)."""
import sys

from runner_support import close_calls as _real

sys.modules[__name__] = _real
