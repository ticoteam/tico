"""`import runner.catalog_job`: runner_support/catalog_job.py (runner/__init__.py)."""
import sys

from runner_support import catalog_job as _real

sys.modules[__name__] = _real
