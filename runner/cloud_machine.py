"""`python -m runner.cloud_machine` and `import runner.cloud_machine`: runner_support/cloud_machine.py (runner/__init__.py)."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("runner_support.cloud_machine", run_name="__main__", alter_sys=True)
else:
    from runner_support import cloud_machine as _real

    sys.modules[__name__] = _real
