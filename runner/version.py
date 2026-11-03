"""`import runner.version`: runner_support/version.py (runner/__init__.py)."""
import sys

from runner_support import version as _real

sys.modules[__name__] = _real
