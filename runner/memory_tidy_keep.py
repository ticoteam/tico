"""`python -m runner.memory_tidy_keep` and `import runner.memory_tidy_keep`: runner_support/memory_tidy_keep.py (runner/__init__.py)."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("runner_support.memory_tidy_keep", run_name="__main__", alter_sys=True)
else:
    from runner_support import memory_tidy_keep as _real

    sys.modules[__name__] = _real
