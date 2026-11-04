"""`import runner.importers.base`: runner_support/importers/base.py (runner/__init__.py)."""
import sys

from runner_support.importers import base as _real

sys.modules[__name__] = _real
