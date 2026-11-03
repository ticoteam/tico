"""`import runner.isolation`: runner_support/isolation.py (runner/__init__.py)."""
import sys

from runner_support import isolation as _real

sys.modules[__name__] = _real
