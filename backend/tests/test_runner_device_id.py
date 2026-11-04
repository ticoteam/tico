"""A runner's salted device identity survives enrollment and is learned once for legacy registrations."""

import sqlite3
from hashlib import sha256

from backend.tests.test_api import api, post, runner  # noqa: F401
from backend.tests.test_migrations import boot, fresh, snapshot

DEVICE_ID = sha256(b"acme-device-one").hexdigest()


def test_enrollment_keeps_device_identity_and_repeated_heartbeats_do_not_rewrite_it(api):  # noqa: F811
    machine = runner(api, device_id=DEVICE_ID)
    store = api.app.state.store
    with store.transaction() as c:
        assert c.execute("SELECT device_id FROM runners WHERE id=?", (machine["runner_id"],)).fetchone()[0] == DEVICE_ID
        c.execute("CREATE TABLE runner_identity_writes(n INTEGER)")
        c.execute("CREATE TRIGGER runner_identity_write AFTER UPDATE ON runners "
                  "BEGIN INSERT INTO runner_identity_writes VALUES(1); END")
    for _ in range(2):
        post(api, "runners/heartbeat", {"version": "test", "platform": "test", "device_id": DEVICE_ID}, token=machine["token"])
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM runner_identity_writes").fetchone()[0] == 0


def test_a_legacy_registration_learns_its_device_once_and_missing_reports_keep_it(api):  # noqa: F811
    machine = runner(api)
    store = api.app.state.store
    with store.read() as c:
        assert c.execute("SELECT device_id FROM runners WHERE id=?", (machine["runner_id"],)).fetchone()[0] is None
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "device_id": DEVICE_ID}, token=machine["token"])
    for extra in ({}, {"device_id": None}, {"device_id": sha256(b"another-device").hexdigest()}):
        post(api, "runners/heartbeat", {"version": "test", "platform": "test", **extra}, token=machine["token"])
        with store.read() as c:
            assert c.execute("SELECT device_id FROM runners_with_state WHERE id=?",
                             (machine["runner_id"],)).fetchone()[0] == DEVICE_ID


def test_upgrade_keeps_existing_computers_unknown_and_reboots_without_changes(tmp_path):
    path = fresh(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("DROP INDEX runners_device_id")
        c.execute("ALTER TABLE runners DROP COLUMN device_id")
        c.execute("DELETE FROM schema_migrations WHERE version='0092_runner_device_id'")
        c.execute("INSERT INTO humans(id,name) VALUES('h1','Ana')")
        c.execute("INSERT INTO runners(id,label,owner_id,token_hash,created_at,platform) "
                  "VALUES('r1','Acme Mac','h1','hash-1','2026-01-01T00:00:00Z','darwin')")
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT label,device_id FROM runners_with_state WHERE id='r1'").fetchone() == ("Acme Mac", None)
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    first = snapshot(path)
    boot(path)
    assert snapshot(path) == first
