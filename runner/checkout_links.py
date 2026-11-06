"""`python -m runner.checkout_links` and `import runner.checkout_links`: runner_support/checkout_links.py (runner/__init__.py)."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("runner_support.checkout_links", run_name="__main__", alter_sys=True)
else:
    from runner_support import checkout_links as _real

    sys.modules[__name__] = _real
