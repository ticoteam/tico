"""`python -m runner.git_credentials` and `import runner.git_credentials`: runner_support/git_credentials.py (runner/__init__.py)."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("runner_support.git_credentials", run_name="__main__", alter_sys=True)
else:
    from runner_support import git_credentials as _real

    sys.modules[__name__] = _real
