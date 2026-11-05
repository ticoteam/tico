"""`import runner.credential_socket`: runner_support/credential_socket.py (runner/__init__.py)."""
import sys

from runner_support import credential_socket as _real

sys.modules[__name__] = _real
