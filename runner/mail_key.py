"""`import runner.mail_key`: runner_support/mail_key.py (runner/__init__.py)."""
import sys

from runner_support import mail_key as _real

sys.modules[__name__] = _real
