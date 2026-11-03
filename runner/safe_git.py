"""`import runner.safe_git`: runner_support/safe_git.py (runner/__init__.py)."""
import sys

from runner_support import safe_git as _real

sys.modules[__name__] = _real
