"""The three `runner` entry points kept until every installed unit has moved to runner_support: units written before
it run `python -m runner` (runner/__main__.py), cloud machine images saved before it run `python -m runner.cloud_machine
boot` (runner/cloud_machine.py), and runner/__init__.py is the package."""

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


def test_the_retired_connectors_job_waits_idle_until_stopped(monkeypatch, tmp_path, capsys):
    # A launchd job or systemd unit set up before mail moved to the server may still start it: it must not exit and
    # be started again in a loop, and it needs no registration to wait.
    import signal

    from runner_support import __main__ as cli

    parked = []
    monkeypatch.setattr(cli, "park", lambda: parked.append(True))
    cli.main(["--config", str(tmp_path / "missing.json"), "connectors"])
    assert parked == [True]
    blocked, waited = [], []
    monkeypatch.undo()
    cli.park(block=lambda how, signals: blocked.append((how, signals)), wait=waited.append)
    stop = {signal.SIGTERM, signal.SIGINT}
    assert blocked == [(signal.SIG_BLOCK, stop)] and waited == [stop]
    assert "retired" in capsys.readouterr().out


def started_with(monkeypatch, module, *, shim_env=None):
    """The environment `python -m <module> run` hands the Rust runner (engine.exec_binary's execve, stood in for)."""
    import os
    import runpy

    from runner_support import engine, release_update

    monkeypatch.delenv("TICO_RUNNER_ENTRY", raising=False)
    monkeypatch.delenv("TICO_RUNNER_SHIM", raising=False)
    for key, value in (shim_env or {}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(release_update, "move_at_start", lambda: None)
    monkeypatch.setattr(engine, "prepare", lambda root, env: (Path("/stand-in/tico-runner"), ""))
    seen = {}
    real = engine.exec_binary
    monkeypatch.setattr(engine, "exec_binary", lambda argv: real(argv, execv=lambda b, a, env: seen.update(env)))
    monkeypatch.setattr(sys, "argv", [module, "run"])
    monkeypatch.delitem(sys.modules, "runner_support.__main__", raising=False)  # as a fresh `python -m` finds it
    try:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    except SystemExit:
        pass
    assert "TICO_RUNNER_SHIM" not in os.environ  # never handed on: a process the runner starts is not "through runner/"
    return seen


def test_python_dash_m_runner_tells_the_rust_runner_it_started_through_the_old_package(monkeypatch):
    assert started_with(monkeypatch, "runner")["TICO_RUNNER_ENTRY"] == "runner"


def test_python_dash_m_runner_support_tells_the_rust_runner_it_started_through_runner_support(monkeypatch):
    assert started_with(monkeypatch, "runner_support")["TICO_RUNNER_ENTRY"] == "runner_support"


def test_a_start_through_runner_support_from_a_runner_the_old_package_started_says_runner_support(monkeypatch):
    # A runner the old package started passes its word down to what it starts (an updater that restarts it): a start
    # through runner_support from there still says runner_support.
    inherited = started_with(monkeypatch, "runner_support", shim_env={"TICO_RUNNER_ENTRY": "runner"})
    assert inherited["TICO_RUNNER_ENTRY"] == "runner_support"


def test_python_dash_m_runner_cloud_machine_tells_the_boot_it_came_through_the_old_package(monkeypatch):
    import os
    import runpy

    monkeypatch.delenv("TICO_RUNNER_SHIM", raising=False)
    seen = []
    monkeypatch.setattr(runpy, "run_module", lambda name, **kw: seen.append((name, os.environ.get("TICO_RUNNER_SHIM"))))
    runpy.run_path(str(ROOT / "runner" / "cloud_machine.py"), run_name="__main__")
    assert seen == [("runner_support.cloud_machine", "1")]
