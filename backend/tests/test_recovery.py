import json

from backend.auth import Auth
from backend.backup import restore_snapshot, snapshot
from backend.config import Settings
from backend.store import H, Problem, Store, encode
from backend.tests.test_api import api, post, setup_attempt

import pytest


def test_store_replaces_legacy_queue_trigger_and_drains_queued_self_messages(tmp_path):
    settings = Settings(db_path=tmp_path / "old-queue.sqlite")
    store = Store(settings)
    store.initialize()
    with store.transaction() as c:
        H.sync_registry(c, {"coo": {"name": "coo", "status": "active"}},
                        {"people": []})
        c.execute("DELETE FROM cloud_migrations WHERE version=8")
        c.execute("DROP TRIGGER queue_bot_message")
        c.execute("CREATE TRIGGER queue_bot_message AFTER INSERT ON messages "
                  "WHEN NEW.to_actor LIKE 'bot:%' BEGIN "
                  "INSERT INTO jobs(id,message_id,bot,created) "
                  "VALUES(NEW.id,NEW.id,substr(NEW.to_actor,5),NEW.created); END")
        conversation = H.open_conversation(c, "bot:coo", ["bot:coo"], kind="chat")
        self_message = H._write_message(c, "bot:coo", "bot:coo", "legacy loop",
                                        conversation, "say", {}, None, None)
        assert c.execute("SELECT state FROM jobs WHERE message_id=?",
                         (self_message["id"],)).fetchone()[0] == "queued"
    store.initialize()
    with store.transaction() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?",
                         (self_message["id"],)).fetchone()[0] == "completed"
        H._write_message(c, "bot:coo", "bot:coo", "ignored by trigger",
                         conversation, "say", {}, None, None)
        assert c.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        assert c.execute("SELECT 1 FROM cloud_migrations WHERE version=8").fetchone()


def test_restore_fences_credentials_and_preserves_uncertain_effects(api, tmp_path):
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "pre-disaster"}, r["token"])
    backup = tmp_path / "backup.sqlite"
    restored = tmp_path / "restored.sqlite"
    snapshot(api.app.state.store.settings.db_path, backup)
    report = restore_snapshot(backup, restored)
    assert report["reenrollment_required"] and report["fenced_attempts"] == 1
    assert report["restored"]["integrity"] == ["ok"]
    store = Store(Settings(db_path=restored))
    auth = Auth(store)
    with pytest.raises(Problem):
        auth.authenticate({"authorization": "Bearer " + r["token"]})
    with pytest.raises(Problem):
        auth.authenticate({"authorization": "Bearer " + a["token"]})
    with store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (a["job_id"],)).fetchone()[0] == "uncertain"
        assert c.execute("SELECT generation FROM assignments WHERE bot='ops'").fetchone()[0] == 2


def test_restart_gives_lapsed_leases_one_more_period_instead_of_expiring_them(api):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.tests.test_api import claim, expire
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "through-deploy"}, r["token"])
    expire(api, a["id"])
    # A deploy restarts the API against the same database while the runner keeps working.
    with TestClient(create_app(api.app.state.store.settings)) as restarted:
        assert claim(restarted, r) is None
        with restarted.app.state.store.read() as c:
            attempt = c.execute("SELECT state,lease_until FROM attempts WHERE id=?", (a["id"],)).fetchone()
            assert attempt["state"] == "running" and attempt["lease_until"] > H.now()
            assert c.execute("SELECT state FROM jobs WHERE id=?", (a["job_id"],)).fetchone()[0] == "running"
            assert c.execute("SELECT count(*) FROM events WHERE action='attempt.lease-grace' AND target=?",
                             (a["id"],)).fetchone()[0] == 1
        result = post(restarted, f"attempts/{a['id']}/complete", {"outcome": "completed", "last_seq": 0,
                      "text": "Finished while the API was away."}, r["token"])
        assert result["outcome"] == "completed"
        # A lease that lapses again with nobody renewing it expires exactly as before.
        r2, _, b = setup_attempt(restarted, bot="cpo", operator="ben")
        post(restarted, f"attempts/{b['id']}/started", {"thread_id": "gone"}, r2["token"])
        expire(restarted, b["id"])
        assert claim(restarted, r2) is None
        with restarted.app.state.store.read() as c:
            assert c.execute("SELECT state FROM attempts WHERE id=?", (b["id"],)).fetchone()[0] == "expired"


def test_every_cloud_migration_number_is_used_once_and_in_order():
    """Two branches picked the same migration number twice on 2026-09-24 (#493): the second block
    then never runs on a database that already recorded the number. With no CI, this is the check
    that fails before such a merge ships."""
    import re
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "store.py").read_text()
    blocks = re.split(r'(?=if not c\.execute\("SELECT 1 FROM cloud_migrations WHERE version=)', source)[1:]
    pairs = [(int(re.match(r'if not c\.execute\("SELECT 1 FROM cloud_migrations WHERE version=(\d+)', b).group(1)),
              [int(v) for v in re.findall(r"INSERT INTO cloud_migrations VALUES\((\d+)", b)]) for b in blocks]
    checked = [v for v, _ in pairs]
    assert len(checked) == len(set(checked)), f"a migration number is used twice: {sorted(checked)}"
    assert all(inserted == [v] for v, inserted in pairs), "each block records exactly its own number"
    # Numbers may skip: some company-specific migrations were removed.
    assert checked == sorted(checked), "migration blocks run in number order"


@pytest.mark.parametrize("sweep", ["claim", "scheduler"])
def test_stall_grace_survives_bounded_expiry_and_contact_between_chunks(api, monkeypatch, sweep):
    from backend.scheduler import Scheduler
    from backend.tests.test_api import claim, headers
    from backend.tests.test_attempt_contention import fleet

    machine = fleet(api, attempts=10, trees=0)
    store, execution = api.app.state.store, api.app.state.execution
    now = H.now()
    clock = [now]
    monkeypatch.setattr(H, "now", lambda: clock[0])
    with store.transaction() as c:
        c.execute("UPDATE attempts SET state=CASE WHEN id IN ('attempt-0','attempt-2','attempt-4',"
                  "'attempt-6','attempt-8') THEN 'leased' ELSE 'running' END,lease_until=?",
                  (H.shift(now, seconds=-1),))
        c.execute("UPDATE jobs SET state=(SELECT a.state FROM attempts a WHERE a.id=jobs.attempt_id) "
                  "WHERE attempt_id IS NOT NULL")
    execution.served_at = H.shift(now, seconds=-store.settings.lease_seconds)
    if sweep == "claim":
        # Each claim maintains at most eight leases and records renewed runner contact.
        assert claim(api, machine) is None
        with store.read() as c:
            assert c.execute("SELECT count(*) FROM events WHERE action='attempt.lease-grace'").fetchone()[0] == 8
        assert claim(api, machine) is None
    else:
        original = execution.expire
        from contextlib import contextmanager
        chunks = []
        transaction = store.transaction
        contact_due = [False]

        def expired(c, **kwargs):
            original(c, **kwargs)
            if "attempt_ids" in kwargs:
                chunks.append(kwargs["attempt_ids"])
                if len(chunks) == 1:
                    contact_due[0] = True

        @contextmanager
        def transaction_then_contact():
            with transaction() as c:
                yield c
            if contact_due[0]:
                contact_due[0] = False
                # A real read-only renew updates contact after the first chunk commits.
                assert api.post('/api/v2/attempts/attempt-0/renew', json={},
                                headers=headers(machine['token'])).status_code == 200

        monkeypatch.setattr(store, "transaction", transaction_then_contact)
        monkeypatch.setattr(execution, "expire", expired)
        assert not Scheduler(store, execution).tick()["failures"]
        assert len(chunks) == 10
        monkeypatch.setattr(execution, "expire", original)
    until = H.shift(now, seconds=store.settings.lease_seconds)
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE state IN ('leased','running') "
                         "AND lease_until=?", (until,)).fetchone()[0] == 10
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.lease-grace'").fetchone()[0] == 10
        assert not c.execute("SELECT 1 FROM events WHERE action='attempt.expired'").fetchone()
    # Contact continues, but these runners really stop renewing. Every original lease must expire.
    for seconds in (store.settings.lease_seconds // 3, 2 * store.settings.lease_seconds // 3,
                    store.settings.lease_seconds):
        clock[0] = H.shift(now, seconds=seconds)
        execution.record_contact(clock[0])
    clock[0] = H.shift(until, seconds=1)
    with store.transaction() as c:
        execution.expire(c)
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE state='expired'").fetchone()[0] == 10
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.lease-grace'").fetchone()[0] == 10
