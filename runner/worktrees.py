"""`import runner.worktrees`: runner_support/worktrees.py (runner/__init__.py)."""
import sys

from runner_support import worktrees as _real

sys.modules[__name__] = _real
