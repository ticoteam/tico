"""`python -m runner.systemd_units` and `import runner.systemd_units`: runner_support/systemd_units.py (runner/__init__.py)."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("runner_support.systemd_units", run_name="__main__", alter_sys=True)
else:
    from runner_support import systemd_units as _real

    sys.modules[__name__] = _real
