"""The real Rust runner, `tico-runner`, against a live server with synthetic identities (ana, Acme). It enrolls with a
one-time code, the server holds the registration it wrote, and the code cannot be used twice. Turns (claims, replies,
the threads wake and loop rules) join this file once the threads core lands.

Build the binary first (`cd runner-rs && cargo build --release`) or point TICO_RUNNER_BIN at one, then run
`python3 -m pytest -m slow -n 0 backend/tests/test_threads_e2e_runner.py`. The file is slow, so the default run skips it."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from backend.tests.live import live  # noqa: F401  (a listening server; `live` is its origin)
from backend.tests.test_api import api, post  # noqa: F401  (the api fixture)

pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[2]
LABEL = "Acme test computer"


def runner_binary():
    binary = Path(os.environ.get("TICO_RUNNER_BIN") or ROOT / "runner-rs" / "target" / "release" / "tico-runner")
    if not binary.exists():
        pytest.skip("no tico-runner binary: run `cargo build --release` in runner-rs, or set TICO_RUNNER_BIN")
    return binary


def run(binary, home, *args):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home)}
    return subprocess.run([str(binary), *args], env=env, capture_output=True, text=True, timeout=120)


def enroll_args(url, code_file, projects, config):
    return ["--config", str(config), "enroll", "--url", url, "--code-file", str(code_file),
            "--label", LABEL, "--projects", str(projects)]


def test_a_real_runner_enrolls_with_a_one_time_code_and_the_server_holds_its_registration(api, live, tmp_path):
    binary = runner_binary()
    home, projects = tmp_path / "home", tmp_path / "projects"
    home.mkdir(), projects.mkdir()
    code_file = tmp_path / "code.json"
    code_file.write_text(json.dumps({"code": post(api, "enrollments", {"operator": "ana"})["code"]}))
    config = tmp_path / "runner" / "runner.json"

    done = run(binary, home, *enroll_args(live, code_file, projects, config))
    assert done.returncode == 0, done.stderr[-400:]
    registration = json.loads(config.read_text())
    assert registration["url"] == live and registration["runner_id"]
    with api.app.state.store.read() as c:
        row = c.execute("SELECT label, operator FROM runners WHERE id=?", (registration["runner_id"],)).fetchone()
    assert row is not None and row["label"] == LABEL and row["operator"] == "ana"

    # A one-time code enrolls once: the second attempt is refused and writes no registration.
    second = tmp_path / "second" / "runner.json"
    again = run(binary, home, *enroll_args(live, code_file, projects, second))
    assert again.returncode != 0 and not second.exists()
    assert registration["token"] not in again.stdout + again.stderr
