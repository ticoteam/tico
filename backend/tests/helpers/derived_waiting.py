"""Helpers that other tests share, moved out of test_derived_waiting.py so they import no test file."""

import importlib.util
from pathlib import Path


def _migration(name):
    path = Path(__file__).resolve().parents[2] / "migrations" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
