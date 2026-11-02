"""Rollback (e2e/README.md "rollback"): a backup taken before an upgrade is served by the old server.

With `--old-server-ref REF` (TICO_E2E_OLD_SERVER_REF) the old server is the checkout at REF, extracted with `git archive`
and run as its own process (e2e/rollback_server.py). It populates a new database with the scenario, takes SQLite's
backup-API copy (the pre-upgrade backup), and the old server boots a restored copy of that backup in a second process
and must answer the scenario exactly as it did before the backup. Without a ref, the same check runs with this
checkout's server: a copy booted by the code under test, which proves the copy but not a rollback.

A ref that is not in this checkout fails the test; it is never a skip, because a skipped rollback is not a rollback."""
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from backend.tests.test_api import api  # noqa: F401  (the fixture)
from e2e import scenario
from e2e.normalize import normalize
from e2e.server import serve, settings_for, snapshot

ROOT = Path(__file__).resolve().parents[2]
REF_ENV = "TICO_E2E_OLD_SERVER_REF"
pytestmark = pytest.mark.slow


def same_checkout(api, tmp_path):  # noqa: F811
    ids = scenario.populate(api)
    source = api.app.state.store.settings.db_path
    scenario.populate_direct(source)
    names = {k: v for k, v in ids.items() if not k.startswith("_")}
    # The fixture seeds rows after its own boot: one boot settles them, and the expected answers come from that state.
    raw = tmp_path / "raw" / "hub.sqlite"
    snapshot(source, raw)
    with serve(settings_for(tmp_path / "settle", raw)) as client:
        before = {name: normalize(entry, names) for name, entry in scenario.capture(client, ids).items()}
    copy = tmp_path / "rollback" / "hub.sqlite"
    snapshot(raw, copy)
    with serve(settings_for(tmp_path / "rollback-settings", copy)) as client:
        assert client.get("/healthz").status_code == 200
        after = {name: normalize(entry, names) for name, entry in scenario.capture(client, ids).items()}
    assert after == before


def checkout(ref, into):
    """The tree at `ref`, extracted from git's archive: this checkout's working copy is not touched."""
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                            capture_output=True, text=True)
    if commit.returncode != 0:
        pytest.fail(f"{REF_ENV}={ref} is not a commit in this checkout; fetch it first (git fetch origin {ref})")
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", ref], capture_output=True, check=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(into, filter="data")


def old_server(tree, args):
    """One step of the rollback in a process of its own: the checkout's backend, this tree's e2e package."""
    result = subprocess.run([sys.executable, "-m", "e2e.rollback_server", *args], cwd=tree,
                            env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True, timeout=900)
    assert result.returncode == 0, "old server step %s failed:\n%s" % (args[0], result.stderr[-4000:])


def old_ref_rollback(ref, tmp_path):
    tree = tmp_path / "checkout"
    tree.mkdir()
    checkout(ref, tree)
    work = tmp_path / "before-upgrade"
    ids_path = tmp_path / "ids.json"
    old_server(tree, ["populate", str(work), "--out", str(ids_path)])
    before = json.loads(ids_path.read_text())["answers"]
    # The restore: a copy of the pre-upgrade backup, booted by the old server in a process that never saw the scenario.
    restored = tmp_path / "restore" / "hub.sqlite"
    snapshot(work / "backup.sqlite", restored)
    after_path = tmp_path / "after.json"
    old_server(tree, ["serve", str(restored), "--ids", str(ids_path), "--out", str(after_path)])
    after = json.loads(after_path.read_text())["answers"]
    assert len(before["tasks"]["body"]["tasks"]) >= 2, "the scenario's tasks are not in the backup"
    assert after == before


def test_a_backup_taken_before_an_upgrade_is_served_by_the_old_server(tmp_path, request):
    ref = os.environ.get(REF_ENV)
    if ref:
        old_ref_rollback(ref, tmp_path)
    else:
        same_checkout(request.getfixturevalue("api"), tmp_path)
