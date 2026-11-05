"""The `runner` package forwards what installed units, older binaries, older update processes and saved cloud images
still start or import (runner/__init__.py)."""
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUPPORT = ROOT / "runner_support"


def run(*args, stdin=None, env=None):
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=60, input=stdin,
                          env=env)


def support_modules():
    """Every module of runner_support/ outside its tests, dotted, after `runner_support.`."""
    for path in sorted(SUPPORT.rglob("*.py")):
        rel = path.relative_to(SUPPORT).with_suffix("")
        if "tests" in rel.parts or rel.parts in (("__init__",), ("__main__",)):
            continue
        yield ".".join(rel.parts[:-1] if rel.name == "__init__" else rel.parts)


@pytest.mark.parametrize("name", list(support_modules()))
def test_every_runner_module_path_is_the_runner_support_module(name):
    # A process of an earlier release keeps its old `runner` package loaded; its lazy imports find these files.
    rel = Path(*name.split("."))
    assert (ROOT / "runner" / rel.with_suffix(".py")).is_file() or (ROOT / "runner" / rel / "__init__.py").is_file()
    real = importlib.import_module("runner_support." + name)
    old = importlib.import_module("runner." + name)
    if name == "importers":   # a package of its own, so its modules load the forwarders beside it
        assert old.__path__ == [str(ROOT / "runner" / "importers")]
        assert old.importer_class is real.importer_class and old.REGISTRY is real.REGISTRY
    else:
        assert old is real


def test_lazy_relative_imports_of_an_old_process_reach_runner_support():
    # Old runner/connectors.py runs `from .freshness import CodeWatch`, old runner/__main__.py
    # `from .importers.service import doctor`, old backend/granola_mcp.py `from runner.importers.base import email_of`.
    import runner_support.freshness
    import runner_support.importers.base
    import runner_support.importers.service

    assert importlib.import_module(".freshness", "runner").CodeWatch is runner_support.freshness.CodeWatch
    assert importlib.import_module(".importers.service", "runner").doctor is runner_support.importers.service.doctor
    from runner.importers.base import email_of
    assert email_of is runner_support.importers.base.email_of


def test_python_dash_m_runner_still_parses_unit_commands():
    said = run("-m", "runner", "--help")
    assert said.returncode == 0, said.stderr
    for command in ("run", "connectors", "close-calls", "importers", "catalog"):
        assert command in said.stdout


def test_python_dash_m_runner_engine_keeps_its_exit_codes_and_answers():
    said = run("-m", "runner.engine", "bogus")
    assert said.returncode == 2
    assert "prepare|path" in said.stderr
    # An earlier release's update process prepares the new tree with `runner.engine`: it answers as runner_support's.
    env = {**os.environ, "TICO_RUNNER_BIN": str(ROOT / "no-such-tico-runner")}
    old, new = run("-m", "runner.engine", "path", env=env), run("-m", "runner_support.engine", "path", env=env)
    assert (old.returncode, old.stdout, old.stderr) == (new.returncode, new.stdout, new.stderr)
    # `path` finds any current runner (the checkout's own build or download counts), so this checkout may have one:
    # TICO_RUNNER_BIN alone does not make it absent. Either way the answer is the one runner_support gives.
    if old.returncode == 1:
        assert "no Rust runner for this checkout" in old.stderr
    else:
        assert old.returncode == 0 and Path(old.stdout.strip()).is_file(), old.stderr


@pytest.mark.parametrize("module", ["release_update", "release_branch", "cloud_machine", "systemd_units", "git_credentials", "checkout_links"])
def test_python_dash_m_runner_entry_points_run_runner_support(module):
    old, new = run("-m", "runner." + module, "--help"), run("-m", "runner_support." + module, "--help")
    assert old.returncode == new.returncode == 0, old.stderr
    assert old.stdout == new.stdout


def test_python_dash_m_runner_memory_tidy_keep_answers_as_runner_support(tmp_path):
    answers = [run("-m", name, str(tmp_path / "no-checkout"), str(tmp_path / f"journal-{i}"), stdin="{}")
               for i, name in enumerate(("runner.memory_tidy_keep", "runner_support.memory_tidy_keep"))]
    assert answers[0].returncode == answers[1].returncode == 0, answers[0].stderr
    assert answers[0].stdout == answers[1].stdout and '"status"' in answers[0].stdout


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_shell_init_by_its_old_path_still_sets_the_turn_path(shell):
    # A macOS turn an earlier runner binary started has BASH_ENV/ZDOTDIR at runner/shell_init.
    if not shutil.which(shell):
        pytest.skip(f"no {shell} here")
    startup = ROOT / "runner" / "shell_init"
    env = {"HOME": "/nonexistent", "PATH": os.defpath, "TICO_TURN_PATH": "/acme/turn-bin:" + os.defpath}
    env.update({"BASH_ENV": str(startup / "bash-env")} if shell == "bash" else {"ZDOTDIR": str(startup)})
    said = subprocess.run([shell, "-c", 'printf %s "$PATH"'], env=env, capture_output=True, text=True, timeout=30)
    assert said.returncode == 0 and not said.stderr, said.stderr
    assert said.stdout.startswith("/acme/turn-bin:")
    assert {p.name for p in (SUPPORT / "shell_init").iterdir()} == {p.name for p in startup.iterdir()}
