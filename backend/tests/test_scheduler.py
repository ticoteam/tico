from datetime import datetime, timezone

import pytest

from backend import routines
from backend.tests.test_api import api, post  # noqa: F401
from backend.scheduler import Scheduler
from backend.store import H


def test_schedule_survives_restart_without_duplicate_work(api):
    """A routine (its schedule automation) fires each occurrence once, across a restart of the scheduler."""
    finished_status = 'done'
    at = datetime(2026, 9, 10, 17, 0, tzinfo=timezone.utc)
    store = api.app.state.store
    with store.transaction() as c:
        aid = routines.create(c, H.KEEPER, 'coo', {'title': 'Review daily work', 'cron': '0 9 * * *',
                                                   'timezone': 'America/Los_Angeles', 'text': 'Review work'},
                              key='morning')['automation']
        c.execute("UPDATE automations SET next_due_at=? WHERE id=?", ('2026-09-09T16:00:00.000000Z', aid))

    def tasks(c):
        return [r[0] for r in c.execute("SELECT id FROM tasks WHERE owner_id='bot:coo' AND title='Review daily work' "
                                        "ORDER BY created_at, rowid")]
    Scheduler(store, api.app.state.execution).tick(at)
    Scheduler(store, api.app.state.execution).tick(at)
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0] == 1
        fired = tasks(c)
        assert len(fired) == 1
        # The day's update request (backend/updates.py) is a job of its own, not the routine's.
        assert c.execute("SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "WHERE json_extract(m.refs,'$.update_request') IS NULL").fetchone()[0] == 1
        assert c.execute("SELECT next_due_at FROM automations WHERE id=?", (aid,)).fetchone()[0] == '2026-09-11T16:00:00.000000Z'
    # A finished occurrence keeps its status; the next occurrence gets its own task and is deduplicated after restart.
    with store.transaction() as c:
        previous = H.task_update(c, 'bot:coo', fired[0], status=finished_status, note='Daily work reviewed.')
    next_at = datetime(2026, 9, 11, 17, tzinfo=timezone.utc)
    Scheduler(store, api.app.state.execution).tick(next_at)
    Scheduler(store, api.app.state.execution).tick(next_at)
    with store.read() as c:
        assert H.task(c, previous['id']) == previous
        assert c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0] == 2
        again = tasks(c)
        assert len(again) == 2 and again[1] != previous['id']


def test_due_reminder_deduplicates_across_scheduler_restart(api):
    task = post(api, "tasks", {"owner": "coo", "title": "Review deadline", "body": "Review pending work", "due": "2026-09-10T15:00:00Z"})
    store = api.app.state.store
    for _ in range(2):
        Scheduler(store, api.app.state.execution).tick(datetime(2026, 9, 10, 17, tzinfo=timezone.utc))
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM task_reminders").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM messages WHERE conversation_id=?", (task["conversation_id"],)).fetchone()[0] == 2


@pytest.mark.slow
def test_idle_claims_do_not_take_the_write_lock_and_never_starve_leases(api):
    import threading, time
    from backend.tests.test_api import assign, headers, ready, runner
    runners = []
    for bot in ("ops", "finance", "cpo"):
        r = runner(api, label=bot)
        assign(api, r, bot)
        ready(api, r, [bot])
        runners.append(r)
        post(api, "jobs/claim", {"next_run": True}, token=r["token"])      # first contact stamps last_seen
    store = api.app.state.store
    # Another writer holds the lock; an idle claim answers anyway. A claim that wrote would wait out the 30 s busy
    # timeout, so the bound sits far from it and from a loaded machine's ordinary slowness.
    with store.read() as holder:
        holder.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        assert post(api, "jobs/claim", {"next_run": True}, token=runners[0]["token"]) == {"attempt": None}
        assert time.monotonic() - started < 10
        assert post(api, "jobs/claim", {"busy_bots": ["unknown-bot"]}, token=runners[0]["token"]) == {"attempt": None}
        holder.execute("ROLLBACK")
    post(api, "chat/ops", {"text": "Anything today?"})
    statuses, got = [], []
    def loop(r):
        for _ in range(15):
            res = api.post("/api/v2/jobs/claim", json={"next_run": True}, headers=headers(r["token"]))
            statuses.append(res.status_code)
            got.append(res.json().get("attempt"))
    threads = [threading.Thread(target=loop, args=(r,)) for r in runners]
    for t in threads:
        t.start()
    for _ in range(3):
        Scheduler(store, api.app.state.execution).tick()
    for t in threads:
        t.join()
    assert set(statuses) == {200}, "no storage_unavailable"
    assert len([a for a in got if a]) == 1
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE state='expired'").fetchone()[0] == 0


def test_completed_tasks_stay_done_after_scheduler_ticks_and_restarts(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        parent = H.task_create(c, 'bot:ops', 'Accept existing work', 'x', 'bot:cpo', lint=False)
        child = H.task_create(c, 'bot:ops', 'Finish child work', 'x', 'bot:cpo', parent_id=parent['id'], lint=False)
        tasks = [parent, H.task_create(c, 'bot:ops', 'Review own delivery', 'x', 'bot:ops', lint=False),
                 H.task_create(c, 'human:ana', 'Review requested delivery', 'x', 'bot:cpo', lint=False),
                 H.task_create(c, 'human:ana', 'Review own receipt', 'x', 'human:ana', lint=False)]
        for task in tasks:
            c.execute("UPDATE tasks SET status='done',done_at='2026-01-01T00:00:00Z' WHERE id=?", (task['id'],))
        old_closed = H.task_create(c, 'bot:ops', 'Cancel old work', 'x', 'bot:cpo', lint=False)
        H.task_close(c, 'bot:ops', old_closed['id'])
        previous = {t['id']: H.task(c, t['id']) for t in [*tasks, old_closed]}
    closes = []
    def close(c, actor, task_id, *args, **kw):
        closes.append(task_id)
        raise AssertionError('Completed work must not close automatically')
    monkeypatch.setattr(H, 'task_close', close)
    for day in (10, 11):
        result = Scheduler(store, api.app.state.execution).tick(datetime(2026, 9, day, 17, tzinfo=timezone.utc))
        assert not result['failures']
    assert not closes
    with store.read() as c:
        assert {tid: H.task(c, tid) for tid in previous} == previous
        assert H.task(c, child['id'])['status'] == 'open'
        assert c.execute("SELECT last_succeeded_at FROM service_health WHERE service='scheduler'").fetchone()[0]


def test_claim_reselects_when_private_task_is_reassigned_after_the_read(api, monkeypatch):
    from backend.tests.test_api import assign, claim, ready, runner
    from backend.tests.test_member_bots import finish
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Review private work", "body": "x", "private": True})
    finish(api, machine, claim(api, machine, "ops"))
    post(api, 'tasks/' + task['id'] + '/comments', {'text': 'Continue this private request.'})
    store, execution = api.app.state.store, api.app.state.execution
    idle = execution._idle_claim
    selected = []
    candidate, selections = execution.candidate, []
    def select_again(*args, **kwargs):
        selections.append(1)
        return candidate(*args, **kwargs)
    def reassign_after_read(c, who, body, picked, failed):
        result = idle(c, who, body, picked, failed)
        selected.extend(picked)
        with store.transaction() as c:
            H.task_update(c, 'human:ana', task['id'], owner='bot:finance')
        return result
    monkeypatch.setattr(execution, "_idle_claim", reassign_after_read)
    monkeypatch.setattr(execution, "candidate", select_again)
    assert post(api, "jobs/claim", {}, token=machine["token"])["attempt"] is None
    assert selected and selected[0]['bot_id'] == 'ops'
    # The read's pick, its recheck under the writer, then one more pick that passes over the reassigned job.
    assert selections == [1, 1, 1]
    with store.read() as c:
        assert H.task(c, task['id'])['private']
        assert c.execute("SELECT count(*) FROM attempts WHERE bot_id='ops'").fetchone()[0] == 1
        assert not c.execute("SELECT 1 FROM attempts WHERE bot_id='ops' AND state IN ('leased','running')").fetchone()


def test_database_failure_keeps_original_error_and_stops_work(tmp_path):
    import sqlite3
    from backend.batch_work import isolated
    c = sqlite3.connect(tmp_path / 'full.db', isolation_level=None)
    c.execute('CREATE TABLE data(x)')
    pages = c.execute('PRAGMA page_count').fetchone()[0]
    c.execute(f'PRAGMA max_page_count={pages + 2}')
    c.execute('BEGIN IMMEDIATE')
    with pytest.raises(sqlite3.DatabaseError, match='database or disk is full'):
        with isolated(c, 'disk', 'one'):
            c.execute('INSERT INTO data VALUES(?)', ('x' * 200000,))
    assert not c.in_transaction
    c.execute('BEGIN IMMEDIATE')
    with pytest.raises(sqlite3.DatabaseError, match='malformed'):
        with isolated(c, 'disk', 'two'):
            c.execute('INSERT INTO data VALUES(?)', ('written before corruption',))
            exc = sqlite3.DatabaseError('database disk image is malformed')
            exc.sqlite_errorcode = sqlite3.SQLITE_CORRUPT
            raise exc
    c.rollback()
    c.close()


@pytest.mark.slow
def test_claim_query_matches_old_python_oracle_on_200_rows(api):
    import itertools
    from backend.auth import Identity
    from backend.models import Claim
    from backend.execution import bot_readiness, readiness_document
    from backend.statuses import PARKED
    from backend.tests.test_api import assign, ready, runner
    r = runner(api)
    for bot in ('ops', 'finance', 'cpo'):
        assign(api, r, bot)
    ready(api, r, ['ops', 'finance'])
    store, execution = api.app.state.store, api.app.state.execution
    who = Identity('runner:' + r['runner_id'], 'runner', runner_id=r['runner_id'])
    with store.transaction() as c:
        # The ordering oracle predates provenance checks. Give its task references real,
        # ordinary sources; missing sources are deliberately unclaimable now.
        for ident in ('held', 'other'):
            c.execute("INSERT INTO tasks(id,title,body,requester_id,owner_id,status,created_at,updated_at,private) "
                      "VALUES(?,?,'x','human:ana','bot:ops','open',?,?,0)", (ident, ident, H.now(), H.now()))
        rooms = {}
        for bot, kind in itertools.product(('ops', 'finance', 'cpo'), ('chat', 'task')):
            rooms[bot, kind] = H.open_conversation(c, 'human:ana', ['bot:' + bot], kind=kind)['id']
        refs = ['held', [' ', None, '\u00a0held\u3000'], 42, ' \t\n', '', None, ['other']]
        for i in range(200):
            bot = ('ops', 'finance', 'cpo')[i % 3]
            kind = ('chat', 'task')[(i // 3) % 2]
            sender = ('human:ana', H.KEEPER)[(i // 6) % 2]
            m = H.say(c, sender, 'bot:' + bot, f'Generated request {i}', conversation_id=rooms[bot, kind],
                      refs={'task': refs[(i // 12) % len(refs)]})
            c.execute("UPDATE jobs SET created_at=?,state=? WHERE message_id=?", ('2026-09-01T00:00:00Z' if i % 5 == 0 else '2026-09-02T00:00:00Z', 'uncertain' if i % 17 == 0 else 'queued', m['id']))
        c.execute("UPDATE bot_config SET onboarding_state='needs_setup' WHERE bot='finance'")
        readiness = readiness_document(c.execute('SELECT readiness FROM runners WHERE id=?', (r['runner_id'],)).fetchone()[0])
        # The old candidate's Python predicate, with readiness and onboarding gates.
        def old_claimable(job):
            check = bot_readiness(readiness, job['bot_id'])
            if check.get('ready') is not True:
                return False
            msg = H.message(c, job['message_id'])
            conv = H.conversation(c, msg['conversation_id'])
            parked = c.execute('SELECT onboarding_state FROM bot_config WHERE bot=?', (job['bot_id'],)).fetchone()[0]
            if not msg['sender_id'].startswith('human:') and parked in PARKED:
                return False
            task = H.message_task_id(msg, conv)
            if not (msg['sender_id'].startswith('human:') and conv['kind'] == 'chat' and task is None):
                for held in c.execute("SELECT message_id FROM jobs WHERE bot_id=? AND state='uncertain'", (job['bot_id'],)):
                    other = H.message(c, held[0])
                    other_conv = H.conversation(c, other['conversation_id'])
                    if (task and H.message_task_id(other, other_conv) == task) or (not task and conv['kind'] == 'chat' and other['conversation_id'] == msg['conversation_id']):
                        return False
            return True
        # Compare the entire order, including tied and NULL timestamps, not just the first row.
        for _ in range(200):
            rows = c.execute("SELECT j.* FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued' ORDER BY CASE WHEN m.sender_id LIKE 'human:%' THEN 0 ELSE 1 END,j.created_at,j.id").fetchall()
            expected = next((j for j in rows if old_claimable(j)), None)
            actual = execution.candidate(c, who, Claim(), execution.runner(c, who))
            assert (actual['id'] if actual else None) == (expected['id'] if expected else None)
            if actual is None:
                break
            c.execute("UPDATE jobs SET state='completed' WHERE id=?", (actual['id'],))


def test_row_isolation_keeps_standalone_task_databases_working(tmp_path):
    import sqlite3
    from backend.batch_work import isolated
    c = sqlite3.connect(tmp_path / 'tasks.db', isolation_level=None)
    c.execute('CREATE TABLE data(x)')
    c.execute('BEGIN IMMEDIATE')
    with isolated(c, 'row', 'bad'):
        c.execute('INSERT INTO data VALUES(1)')
        raise ValueError('Bad row')
    with isolated(c, 'row', 'good'):
        c.execute('INSERT INTO data VALUES(2)')
    c.commit()
    assert c.execute('SELECT x FROM data').fetchall() == [(2,)]
    c.close()


def test_a_duplicate_refusal_is_done_not_a_health_error(tmp_path):
    import sqlite3
    from backend.batch_work import isolated
    c = sqlite3.connect(tmp_path / 'dup.db', isolation_level=None)
    c.execute("CREATE TABLE service_health(service TEXT PRIMARY KEY, last_succeeded_at TEXT, last_error TEXT, "
              "detail TEXT NOT NULL DEFAULT '{}')")
    c.execute("INSERT INTO service_health VALUES('background:wake_stalled:t1',NULL,'wake_stalled: duplicate: x','{}')")
    c.execute('BEGIN IMMEDIATE')
    failures = []
    with isolated(c, 'wake_stalled', 't1', failures):
        raise H.Refused('duplicate', 'x already asks botops for this')
    c.commit()
    assert failures == [] and c.execute('SELECT * FROM service_health').fetchall() == []
    c.close()


def test_rows_from_retired_health_mechanisms_are_removed_on_start(tmp_path, monkeypatch):
    import sqlite3
    from backend.store import retire_health_rows
    c = sqlite3.connect(tmp_path / 'h.db')
    c.execute("CREATE TABLE service_health(service TEXT PRIMARY KEY, last_succeeded_at TEXT, last_error TEXT, "
              "detail TEXT NOT NULL DEFAULT '{}')")
    for service in ('backup', 'backup-daily', 'backup-monthly', 'restore-check', 'processing:r1', 'scheduler'):
        c.execute("INSERT INTO service_health VALUES(?,'2026-09-30',NULL,'{}')", (service,))
    monkeypatch.delenv('TICO_BACKUP_MODE', raising=False)
    retire_health_rows(c)                      # no Litestream here: the snapshot timers may still run
    assert {r[0] for r in c.execute('SELECT service FROM service_health')} == {
        'backup', 'backup-daily', 'backup-monthly', 'restore-check', 'scheduler'}
    monkeypatch.setenv('TICO_BACKUP_MODE', 'remote')
    retire_health_rows(c)
    assert [r[0] for r in c.execute('SELECT service FROM service_health')] == ['scheduler']
    c.close()
