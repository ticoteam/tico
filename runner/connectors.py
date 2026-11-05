"""`import runner.connectors`: runner_support/connectors.py (runner/__init__.py)."""
import sys

from runner_support import connectors as _real

sys.modules[__name__] = _real
