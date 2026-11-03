"""`python -m runner.release_update` and `import runner.release_update`: runner_support/release_update.py (runner/__init__.py)."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("runner_support.release_update", run_name="__main__", alter_sys=True)
else:
    from runner_support import release_update as _real

    sys.modules[__name__] = _real
