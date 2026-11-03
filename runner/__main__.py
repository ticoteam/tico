"""`python -m runner ...`: runner_support/__main__.py (runner/__init__.py)."""
import runpy

runpy.run_module("runner_support", run_name="__main__", alter_sys=True)
