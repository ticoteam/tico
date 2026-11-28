"""`python -m runner ...`: runner_support/__main__.py (runner/__init__.py).

TICO_RUNNER_SHIM tells runner_support this start came through the old package; it hands that on to the Rust runner as
TICO_RUNNER_ENTRY=runner, which reports it so Computers can show which computers still start this way."""

import os
import runpy

os.environ["TICO_RUNNER_SHIM"] = "1"
runpy.run_module("runner_support", run_name="__main__", alter_sys=True)
