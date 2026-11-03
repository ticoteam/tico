"""`python -m runner.engine` and `import runner.engine`: runner_support/engine.py (runner/__init__.py)."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("runner_support.engine", run_name="__main__", alter_sys=True)
else:
    from runner_support import engine as _real

    sys.modules[__name__] = _real
