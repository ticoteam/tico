from datetime import datetime, timezone

import pytest

from backend.tests.test_api import api, post  # noqa: F401
from backend.scheduler import Scheduler
from backend.store import H


def test_schedule_survives_restart_without_duplicate_work(api):
    finished_status = 'done'
    at = datetime(2026, 9, 10, 17, 0, tzinfo=timezone.utc)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO schedules(id,bot,cron,title,playbook,last_fired,next_due) "
                  "VALUES('morning','coo','0 9 * * *','Review daily work','Review work',NULL,?)",
                  ('2026-09-09T16:00:00.000000Z',))
    first = Scheduler(store, api.app.state.execution).tick(at)
    second = Scheduler(store, api.app.state.execution).tick(at)
    assert len(first["fired"]) == 1
    assert not second["fired"]
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM schedule_occurrences").fetchone()[0] == 1
        # The day's update request (backend/updates.py) is a job of its own, not the routine's.
        assert c.execute("SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "WHERE json_extract(m.refs_json,'$.update_request') IS NULL").fetchone()[0] == 1
        assert c.execute("SELECT next_due FROM schedules").fetchone()[0] == '2026-09-11T16:00:00.000000Z'
    # A finished occurrence keeps its status; the next occurrence gets its own task and is deduplicated after restart.
    with store.transaction() as c:
        previous = H.task_update(c, 'bot:coo', first['fired'][0], status=finished_status, note='Daily work reviewed.')
    next_at = datetime(2026, 9, 11, 17, tzinfo=timezone.utc)
    next_run = Scheduler(store, api.app.state.execution).tick(next_at)
    assert len(next_run['fired']) == 1 and next_run['fired'][0] != previous['id']
    assert not Scheduler(store, api.app.state.execution).tick(next_at)['fired']
    with store.read() as c:
        assert H.task(c, previous['id']) == previous
        assert c.execute('SELECT count(*) FROM schedule_occurrences').fetchone()[0] == 2


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
    # Another writer holds the lock for a second; an idle claim answers at once anyway.
    with store.read() as holder:
        holder.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        assert post(api, "jobs/claim", {"next_run": True}, token=runners[0]["token"]) == {"attempt": None}
        assert time.monotonic() - started < 1
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
        assert c.execute("SELECT last_success FROM service_health WHERE service='scheduler'").fetchone()[0]


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
    def select_again(*args):
        selections.append(1)
        return candidate(*args)
    def reassign_after_read(*args):
        result = idle(*args)
        selected.extend(args[-1])
        with store.transaction() as c:
            H.task_update(c, 'human:ana', task['id'], owner='bot:finance')
        return result
    monkeypatch.setattr(execution, "_idle_claim", reassign_after_read)
    monkeypatch.setattr(execution, "candidate", select_again)
    assert post(api, "jobs/claim", {}, token=machine["token"])["attempt"] is None
    assert selected and selected[0]['bot'] == 'ops'
    assert selections == [1, 1]
    with store.read() as c:
        assert H.task(c, task['id'])['private']
        assert c.execute("SELECT count(*) FROM attempts WHERE bot='ops'").fetchone()[0] == 1
        assert not c.execute("SELECT 1 FROM attempts WHERE bot='ops' AND state IN ('leased','running')").fetchone()


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
            c.execute("INSERT INTO tasks(id,title,body,requester,owner,status,created,updated,private) "
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
            c.execute("UPDATE jobs SET created=?,state=? WHERE message_id=?", ('2026-09-01T00:00:00Z' if i % 5 == 0 else '2026-09-02T00:00:00Z', 'uncertain' if i % 17 == 0 else 'queued', m['id']))
        c.execute("UPDATE bot_config SET onboarding_state='needs_setup' WHERE bot='finance'")
        readiness = readiness_document(c.execute('SELECT readiness_json FROM runners WHERE id=?', (r['runner_id'],)).fetchone()[0])
        # The old candidate's Python predicate, with readiness and onboarding gates.
        def old_claimable(job):
            check = bot_readiness(readiness, job['bot'])
            if check.get('ready') is not True:
                return False
            msg = H.message(c, job['message_id'])
            conv = H.conversation(c, msg['conversation_id'])
            parked = c.execute('SELECT onboarding_state FROM bot_config WHERE bot=?', (job['bot'],)).fetchone()[0]
            if not msg['from_actor'].startswith('human:') and parked in PARKED:
                return False
            task = H.message_task_id(msg, conv)
            if not (msg['from_actor'].startswith('human:') and conv['kind'] == 'chat' and task is None):
                for held in c.execute("SELECT message_id FROM jobs WHERE bot=? AND state='uncertain'", (job['bot'],)):
                    other = H.message(c, held[0])
                    other_conv = H.conversation(c, other['conversation_id'])
                    if (task and H.message_task_id(other, other_conv) == task) or (not task and conv['kind'] == 'chat' and other['conversation_id'] == msg['conversation_id']):
                        return False
            return True
        # Compare the entire order, including tied and NULL timestamps, not just the first row.
        for _ in range(200):
            rows = c.execute("SELECT j.* FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued' ORDER BY CASE WHEN m.from_actor LIKE 'human:%' THEN 0 ELSE 1 END,j.created,j.id").fetchall()
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
