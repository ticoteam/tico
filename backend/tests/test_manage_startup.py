"""`python -m backend.manage` as docker/entrypoint.sh and an operator run it: a real process, not main() in-process."""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

from clients.environments import seed_registry

from backend import manage

REPO = Path(__file__).resolve().parents[2]


def run_manage(*args, registry):
    # Only what the command needs: no inherited service credentials reach the child process.
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "TICO_REGISTRY_DIR": str(registry)}
    result = subprocess.run(
        [sys.executable, "-m", "backend.manage", *map(str, args)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_startup_commands_run_as_a_process_on_a_fresh_volume(tmp_path):
    registry = seed_registry(
        tmp_path,
        "Owner",
        "owner@example.com",
        {"company_name": "Example", "app_name": "Example", "assistant_name": "Assistant"},
    )
    database = tmp_path / "hub.sqlite"
    (tmp_path / "seed-projects").mkdir()
    created = run_manage("initialize", database, "--projects", tmp_path / "seed-projects", registry=registry)
    assert created["bots"] > 0
    assert run_manage("inspect", database, registry=registry)["integrity"] == ["ok"]
    assert "enabled" in json.dumps(run_manage("usage-count", database, "show", registry=registry))
    bot = sorted(b for bots in created["bots_by_operator"].values() for b in bots)[0]
    assert run_manage("set-repo", database, bot, "owner/name", registry=registry)["repo"] == "owner/name"
    assert run_manage("snapshot", database, tmp_path / "copy.sqlite", registry=registry)
