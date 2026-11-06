"""`python -m runner.release_branch` and `import runner.release_branch`: runner_support/release_branch.py (runner/__init__.py)."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("runner_support.release_branch", run_name="__main__", alter_sys=True)
else:
    from runner_support import release_branch as _real

    sys.modules[__name__] = _real
