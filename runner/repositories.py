"""`import runner.repositories`: runner_support/repositories.py (runner/__init__.py)."""
import sys

from runner_support import repositories as _real

sys.modules[__name__] = _real
