"""`import runner.importers.service`: runner_support/importers/service.py (runner/__init__.py)."""
import sys

from runner_support.importers import service as _real

sys.modules[__name__] = _real
