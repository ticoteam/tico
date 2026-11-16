"""A runner's salted device identity survives enrollment and is learned once for legacy registrations."""

from hashlib import sha256

from backend.tests.helpers.api import post, runner  # noqa: F401

DEVICE_ID = sha256(b"acme-device-one").hexdigest()


def test_enrollment_keeps_device_identity_and_repeated_heartbeats_do_not_rewrite_it(api):  # noqa: F811
    machine = runner(api, device_id=DEVICE_ID)
    store = api.app.state.store
    with store.transaction() as c:
        assert c.execute("SELECT device_id FROM runners WHERE id=?", (machine["runner_id"],)).fetchone()[0] == DEVICE_ID
        c.execute("CREATE TABLE runner_identity_writes(n INTEGER)")
        c.execute(
            "CREATE TRIGGER runner_identity_write AFTER UPDATE ON runners "
            "BEGIN INSERT INTO runner_identity_writes VALUES(1); END"
        )
    for _ in range(2):
        post(
            api,
            "runners/heartbeat",
            {"version": "test", "platform": "test", "device_id": DEVICE_ID},
            token=machine["token"],
        )
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM runner_identity_writes").fetchone()[0] == 0


def test_a_legacy_registration_learns_its_device_once_and_missing_reports_keep_it(api):  # noqa: F811
    machine = runner(api)
    store = api.app.state.store
    with store.read() as c:
        assert c.execute("SELECT device_id FROM runners WHERE id=?", (machine["runner_id"],)).fetchone()[0] is None
    post(
        api,
        "runners/heartbeat",
        {"version": "test", "platform": "test", "device_id": DEVICE_ID},
        token=machine["token"],
    )
    for extra in ({}, {"device_id": None}, {"device_id": sha256(b"another-device").hexdigest()}):
        post(api, "runners/heartbeat", {"version": "test", "platform": "test", **extra}, token=machine["token"])
        with store.read() as c:
            assert (
                c.execute("SELECT device_id FROM runners_with_state WHERE id=?", (machine["runner_id"],)).fetchone()[0]
                == DEVICE_ID
            )
