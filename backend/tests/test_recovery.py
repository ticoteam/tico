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
                  "WHEN NEW.recipient_id LIKE 'bot:%' BEGIN "
                  "INSERT INTO jobs(id,message_id,bot_id,created_at) "
                  "VALUES(NEW.id,NEW.id,substr(NEW.recipient_id,5),NEW.created_at); END")
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


def test_a_run_the_last_process_held_is_flagged_at_start_and_resumes_if_it_lapses(api):
    """A plain restart or crash writes no update event: the start flags the runs the last process held, and one whose
    computer does not carry on resumes on the next sweep, a shell command and all, with nobody asked."""
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.tests.test_api import claim, expire, ready
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "cut-by-restart"}, r["token"])
    post(api, f"attempts/{a['id']}/events", {"events": [
        {"seq": 1, "kind": "status", "payload": {"state": "active", "tool_events": True}},
        {"seq": 2, "kind": "tool", "payload": {"tool": "shell", "status": "started", "item_id": "sh1",
                                               "summary": "ls notes"}}]}, r["token"])
    with TestClient(create_app(api.app.state.store.settings)) as restarted:
        store = restarted.app.state.store
        with store.read() as c:
            [flag] = c.execute("SELECT detail FROM events WHERE action='attempt.update_cut' AND target=?",
                               (a["id"],)).fetchall()
        assert json.loads(flag[0])["cause"] == "restart"
        expire(restarted, a["id"])
        assert claim(restarted, r) is None
        with store.transaction() as c:
            assert [d["state"] for d in restarted.app.state.execution.auto_reconcile(c)] == ["queued"]
            assert not c.execute("SELECT 1 FROM tasks WHERE title LIKE 'Review %'").fetchone()
        ready(restarted, r, ["ops"])
        again = claim(restarted, r)
        assert again["job_id"] == a["job_id"] and again["recovery"]["resume_session"] is True
        assert "by a Tico restart" in again["message"]["body"]


def test_a_server_that_boots_again_while_a_run_is_live_flags_that_run_once(api):
    """Each start flags the runs its last process held, but a run already flagged by a start gets no second event: after
    the second boot it still has one restart flag. An update's flag is its own cause, so it is still written."""
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.execution import flag_cut
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "boot-twice"}, r["token"])

    def flags():
        with api.app.state.store.read() as c:
            return c.execute("SELECT count(*) FROM events WHERE action='attempt.update_cut' AND target=?",
                             (a["id"],)).fetchone()[0]
    for _ in range(2):
        with TestClient(create_app(api.app.state.store.settings)):
            assert flags() == 1
    with api.app.state.store.transaction() as c:
        assert flag_cut(c, "update", "0.4.18") == 1
    assert flags() == 2


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
    # Brief contact must not enroll already-graced attempts in another stalled cohort.
    # Stop contact a third of a lease in: the first deadline is itself a server stall.
    clock[0] = H.shift(now, seconds=store.settings.lease_seconds // 3)
    execution.record_contact(clock[0])
    clock[0] = H.shift(until, seconds=1)
    with store.transaction() as c:
        execution.expire(c)
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE state='expired'").fetchone()[0] == 10
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.lease-grace'").fetchone()[0] == 10


@pytest.mark.parametrize("sweep", ["bounded", "scheduler"])
def test_hard_start_deadlines_survive_chunked_stall_recovery(api, monkeypatch, sweep):
    from backend.scheduler import Scheduler
    from backend.tests.test_attempt_contention import fleet

    fleet(api, attempts=12, trees=0)
    store, execution = api.app.state.store, api.app.state.execution
    now = H.now()
    monkeypatch.setattr(H, "now", lambda: now)
    with store.transaction() as c:
        for i in range(10):
            aid = f"attempt-{i}"
            c.execute("UPDATE attempts SET state='leased',lease_until=? WHERE id=?",
                      (H.shift(now, seconds=-1 if i < 5 else 600), aid))
            c.execute("UPDATE jobs SET state='leased' WHERE attempt_id=?", (aid,))
            c.execute("INSERT INTO start_places(attempt_id,start_until,managed) VALUES(?,?,1)",
                      (aid, H.shift(now, seconds=-1)))
        for i in (10, 11):
            c.execute("UPDATE attempts SET lease_until=? WHERE id=?",
                      (H.shift(now, seconds=-1), f"attempt-{i}"))
    execution.served_at = H.shift(now, seconds=-store.settings.lease_seconds)
    if sweep == "scheduler":
        assert not Scheduler(store, execution).tick()["failures"]
    else:
        for _ in range(2):
            with store.transaction() as c:
                execution.expire(c, limit=8)
            execution.record_contact(now)
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE state='expired'").fetchone()[0] == 10
        assert c.execute("SELECT count(*) FROM attempts WHERE state='running' AND lease_until=?",
                         (H.shift(now, seconds=store.settings.lease_seconds),)).fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.lease-grace'").fetchone()[0] == 2
