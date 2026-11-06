"""`import runner.git_locks`: runner_support/git_locks.py (runner/__init__.py)."""
import sys

from runner_support import git_locks as _real

sys.modules[__name__] = _real
