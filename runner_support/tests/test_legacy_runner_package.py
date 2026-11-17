"""The three `runner` entry points still in use: installed units run `python -m runner` (runner/__main__.py), saved cloud
machine images run `python -m runner.cloud_machine boot` (runner/cloud_machine.py), and runner/__init__.py is the package."""

import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*args):
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=60)


def test_runner_package_holds_only_the_three_entry_points():
    kept = sorted(p.name for p in (ROOT / "runner").iterdir() if not p.name.startswith((".", "__pycache__")))
    assert kept == ["__init__.py", "__main__.py", "cloud_machine.py"]
    runner = importlib.import_module("runner")
    assert Path(runner.__file__).resolve() == (ROOT / "runner" / "__init__.py").resolve()


def test_python_dash_m_runner_still_parses_unit_commands():
    said = run("-m", "runner", "--help")
    assert said.returncode == 0, said.stderr
    for command in ("run", "connectors", "close-calls", "importers", "catalog"):
        assert command in said.stdout


def test_runner_cloud_machine_is_runner_support_cloud_machine():
    # A saved cloud machine image boots `python -m runner.cloud_machine`: the module is runner_support's.
    assert importlib.import_module("runner.cloud_machine") is importlib.import_module("runner_support.cloud_machine")
    old, new = run("-m", "runner.cloud_machine", "--help"), run("-m", "runner_support.cloud_machine", "--help")
    assert old.returncode == new.returncode == 0, old.stderr
    assert old.stdout == new.stdout
