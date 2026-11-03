"""`import runner.redact`: runner_support/redact.py (runner/__init__.py)."""
import sys

from runner_support import redact as _real

sys.modules[__name__] = _real
