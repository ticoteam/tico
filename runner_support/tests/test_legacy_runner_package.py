"""The `runner` package forwards what installed units, older binaries and saved cloud images still start."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*args):
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=60)


def test_old_module_paths_reach_runner_support():
    import runner.cloud_machine
    import runner.engine
    import runner.memory_tidy_keep
    import runner.release_update
    import runner_support.cloud_machine
    import runner_support.engine
    import runner_support.memory_tidy_keep
    import runner_support.release_update

    assert runner.engine.main is runner_support.engine.main
    assert runner.engine.source_id is runner_support.engine.source_id
    assert runner.release_update.main is runner_support.release_update.main
    assert runner.memory_tidy_keep.main is runner_support.memory_tidy_keep.main
    assert runner.cloud_machine.main is runner_support.cloud_machine.main


def test_python_dash_m_runner_still_parses_unit_commands():
    said = run("-m", "runner", "--help")
    assert said.returncode == 0, said.stderr
    for command in ("run", "connectors", "close-calls", "importers", "catalog"):
        assert command in said.stdout


def test_python_dash_m_runner_engine_keeps_its_exit_codes():
    said = run("-m", "runner.engine", "bogus")
    assert said.returncode == 2
    assert "prepare|path" in said.stderr
