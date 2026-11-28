"""`python -m runner.cloud_machine` and `import runner.cloud_machine`: runner_support/cloud_machine.py (runner/__init__.py)."""

import os
import runpy
import sys

if __name__ == "__main__":
    # The boot records that it came through the old package (runner_support/cloud_machine.py boot, ENTRY_FILE).
    os.environ["TICO_RUNNER_SHIM"] = "1"
    runpy.run_module("runner_support.cloud_machine", run_name="__main__", alter_sys=True)
else:
    from runner_support import cloud_machine as _real

    sys.modules[__name__] = _real
