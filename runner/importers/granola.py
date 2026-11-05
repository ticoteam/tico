"""`import runner.importers.granola`: runner_support/importers/granola.py (runner/__init__.py)."""
import sys

from runner_support.importers import granola as _real

sys.modules[__name__] = _real
