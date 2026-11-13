from datetime import datetime, timezone
import sqlite3
from unittest.mock import Mock

import pytest

from backend import routines
from backend.tests.helpers.api import post  # noqa: F401
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.helpers.scheduler import _wake_by_hand


def test_failed_computer_retirement_records_failure_and_runs_the_remaining_cleanup(api, monkeypatch, caplog):
    from backend import computers, screening, support_triage

    retirement = Mock(side_effect=sqlite3.OperationalError("writer lock timeout"))
    monkeypatch.setattr(computers, "sweep", retirement)
    following = []
    for module in (support_triage, screening):  # the deadline sweeps after retirement (pruning is Clean up's)
        sweep = Mock(wraps=module.sweep)
        monkeypatch.setattr(module, "sweep", sweep)
        following.append(sweep)
    result = Scheduler(api.app.state.store, api.app.state.execution).tick()
    assert result["failures"] == [{"computer_retirement": "OperationalError"}]
    retirement.assert_called_once()
    for sweep in following:
        sweep.assert_called_once()
    assert any(record.name == "tico.scheduler" and record.exc_info for record in caplog.records)


def _scheduler_health(store):
    import json

    with store.read() as c:
        row = c.execute("SELECT last_error, detail FROM service_health WHERE service='scheduler'").fetchone()
    return row[0], json.loads(row[1])["failures"]


def test_a_failure_late_in_the_tick_reaches_the_schedulers_health_row(api, monkeypatch):
    """The health row is written at the end of the pass: a job that fails after the 10-second writes is on it."""
    from backend import thread_rank

    monkeypatch.setattr(thread_rank, "tick", Mock(side_effect=RuntimeError("down")))
    store = api.app.state.store
    result = Scheduler(store, api.app.state.execution).tick()
    assert {"thread_rank": "RuntimeError"} in result["failures"]
    last_error, failures = _scheduler_health(store)
    assert last_error == "schedule errors"
    assert {"thread_rank": "RuntimeError"} in failures


def test_a_tick_that_raises_still_writes_its_health_row(api, monkeypatch):
    from backend import maintenance

    monkeypatch.setattr(maintenance, "tick", Mock(side_effect=RuntimeError("down")))
    store = api.app.state.store
    with pytest.raises(RuntimeError):
        Scheduler(store, api.app.state.execution).tick()
    last_error, failures = _scheduler_health(store)
    assert last_error == "schedule errors"
    assert {"scheduler": "RuntimeError"} in failures


def test_the_finance_sweep_commits_its_money_actions_durably(api, monkeypatch):
    """The hourly Finance sweep ends actions whose OK was given, as the OK route does: its commit reaches the disk
    before the pass goes on (store.transaction(durable=True))."""
    from contextlib import contextmanager

    from backend.finance import actions as finance

    store = api.app.state.store
    real = store.transaction
    # Keyed by connection: the tick's Finance workers open transactions on their own threads meanwhile.
    durable_of, seen = {}, []

    def sweep(c, at=None, most=200):
        seen.append(durable_of.get(id(c)))
        return []

    @contextmanager
    def transaction(durable=False):
        with real(durable) as c:
            durable_of[id(c)] = durable
            yield c

    monkeypatch.setattr(finance, "ok_sweep", sweep)
    monkeypatch.setattr(store, "transaction", transaction)
    Scheduler(store, api.app.state.execution).tick()
    assert seen == [True], f"the sweep ran once, in a durable transaction: {seen}"


def test_saved_nightly_learnings_never_runs_beside_maintenance(api, monkeypatch):
    import json
    from backend import learnings, maintenance

    store = api.app.state.store
    started = []
    monkeypatch.setattr(learnings, "nightly", lambda *args: started.append(args), raising=False)
    with store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata VALUES('system_routines',?) ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value",
            (json.dumps({"learnings": {"on": True, "preset": "daily", "at": "03:00"}}),),
        )
        maintenance.save_settings(c, "human:ana", paused=True)
    scheduler = Scheduler(store, api.app.state.execution)
    for day in (3, 4):
        scheduler.tick(datetime(2026, 11, day, 4, tzinfo=maintenance.ZONE))
    assert not started
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM learning_runs").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='learnings'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM maintenance_runs").fetchone()[0] == 0


def test_schedule_survives_restart_without_duplicate_work(api):
    """A routine (its schedule automation) fires each occurrence once, across a restart of the scheduler."""
    finished_status = 'done'
    at = datetime(2026, 9, 10, 17, 0, tzinfo=timezone.utc)
    store = api.app.state.store
    with store.transaction() as c:
        aid = routines.create(
            c,
            H.KEEPER,
            'assistant',
            {
                'title': 'Review daily work',
                'cron': '0 9 * * *',
                'timezone': 'America/Los_Angeles',
                'text': 'Review work',
            },
            key='morning',
        )['automation']
        c.execute("UPDATE automations SET next_due_at=? WHERE id=?", ('2026-09-09T16:00:00.000000Z', aid))

    def tasks(c):
        return [
            r[0]
            for r in c.execute(
                "SELECT id FROM tasks WHERE owner_id='bot:assistant' AND title='Review daily work' ORDER BY created_at, rowid"
            )
        ]

    Scheduler(store, api.app.state.execution).tick(at)
    Scheduler(store, api.app.state.execution).tick(at)
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0] == 1
        fired = tasks(c)
        assert len(fired) == 1
        # The day's update request (backend/updates.py) is a job of its own, not the routine's.
        assert (
            c.execute(
                "SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id "
                "WHERE json_extract(m.refs,'$.update_request') IS NULL"
            ).fetchone()[0]
            == 1
        )
        assert (
            c.execute("SELECT next_due_at FROM automations WHERE id=?", (aid,)).fetchone()[0]
            == '2026-09-11T16:00:00.000000Z'
        )
    # A finished occurrence keeps its status; the next occurrence gets its own task and is deduplicated after restart.
    with store.transaction() as c:
        previous = H.task_update(
            c,
            'bot:assistant',
            fired[0],
            status=finished_status,
            note='Daily work reviewed.',
            seen_version=H.task(c, fired[0])['version'],
        )
    next_at = datetime(2026, 9, 11, 17, tzinfo=timezone.utc)
    Scheduler(store, api.app.state.execution).tick(next_at)
    Scheduler(store, api.app.state.execution).tick(next_at)
    with store.read() as c:
        assert H.task(c, previous['id']) == previous
        assert c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0] == 2
        again = tasks(c)
        assert len(again) == 2 and again[1] != previous['id']


@pytest.mark.slow
def test_idle_claims_do_not_take_the_write_lock_and_never_starve_leases(api):
    import threading, time
    from backend.tests.helpers.api import assign, headers, ready, runner

    runners = []
    for bot in ("ops", "finance", "cpo"):
        r = runner(api, label=bot)
        assign(api, r, bot)
        ready(api, r, [bot])
        runners.append(r)
        post(api, "jobs/claim", {"next_run": True}, token=r["token"])  # first contact stamps last_seen
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
        tasks = [
            parent,
            H.task_create(c, 'bot:ops', 'Review own delivery', 'x', 'bot:ops', lint=False),
            H.task_create(c, 'human:ana', 'Review requested delivery', 'x', 'bot:cpo', lint=False),
            H.task_create(c, 'human:ana', 'Review own receipt', 'x', 'human:ana', lint=False),
        ]
        for task in tasks:
            H.task_update(c, task['owner_id'], task['id'], status='done', seen_version=H.task(c, task['id'])['version'])
        old_closed = H.task_create(c, 'bot:ops', 'Cancel old work', 'x', 'bot:cpo', lint=False)
        H.task_close(c, 'bot:ops', old_closed['id'], 'Not needed', old_closed['version'])
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
    from backend.tests.helpers.api import assign, claim, ready, runner
    from backend.tests.helpers.member_bots import finish

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
    from backend.tests.helpers.api import assign, ready, runner

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
            c.execute(
                "INSERT INTO tasks(id,title,body,requester_id,owner_id,status,created_at,updated_at,private) "
                "VALUES(?,?,'x','human:ana','bot:ops','open',?,?,0)",
                (ident, ident, H.now(), H.now()),
            )
        rooms = {}
        for bot, kind in itertools.product(('ops', 'finance', 'cpo'), ('chat', 'task')):
            rooms[bot, kind] = H.open_conversation(c, 'human:ana', ['bot:' + bot], kind=kind)['id']
        refs = ['held', [' ', None, '\u00a0held\u3000'], 42, ' \t\n', '', None, ['other']]
        for i in range(200):
            bot = ('ops', 'finance', 'cpo')[i % 3]
            kind = ('chat', 'task')[(i // 3) % 2]
            sender = ('human:ana', H.KEEPER)[(i // 6) % 2]
            m = H.say(
                c,
                sender,
                'bot:' + bot,
                f'Generated request {i}',
                conversation_id=rooms[bot, kind],
                refs={'task': refs[(i // 12) % len(refs)]},
            )
            c.execute(
                "UPDATE jobs SET created_at=?,state=? WHERE message_id=?",
                (
                    '2026-09-01T00:00:00Z' if i % 5 == 0 else '2026-09-02T00:00:00Z',
                    'uncertain' if i % 17 == 0 else 'queued',
                    m['id'],
                ),
            )
        c.execute("UPDATE bot_config SET onboarding_state='needs_setup' WHERE bot='finance'")
        readiness = readiness_document(
            c.execute('SELECT readiness FROM runners_with_state WHERE id=?', (r['runner_id'],)).fetchone()[0]
        )

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
                for held in c.execute(
                    "SELECT message_id FROM jobs WHERE bot_id=? AND state='uncertain'", (job['bot_id'],)
                ):
                    other = H.message(c, held[0])
                    other_conv = H.conversation(c, other['conversation_id'])
                    if (task and H.message_task_id(other, other_conv) == task) or (
                        not task and conv['kind'] == 'chat' and other['conversation_id'] == msg['conversation_id']
                    ):
                        return False
            return True

        # Compare the entire order, including tied and NULL timestamps, not just the first row.
        for _ in range(200):
            rows = c.execute(
                "SELECT j.* FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued' ORDER BY CASE WHEN m.sender_id LIKE 'human:%' THEN 0 ELSE 1 END,j.created_at,j.id"
            ).fetchall()
            expected = next((j for j in rows if old_claimable(j)), None)
            actual = execution.candidate(c, who, Claim(), execution.runner(c, who))
            assert (actual['id'] if actual else None) == (expected['id'] if expected else None)
            if actual is None:
                break
            c.execute("UPDATE jobs SET state='completed' WHERE id=?", (actual['id'],))


def _ops_runner(api):
    from backend.auth import Identity
    from backend.tests.helpers.api import assign, ready, runner

    r = runner(api)
    assign(api, r, 'ops')
    ready(api, r, ['ops'])
    return Identity('runner:' + r['runner_id'], 'runner', runner_id=r['runner_id'])


def _carrier_job(c, message_id):
    """The queued job of the keeper carrier a thread wrote for this message (threads._carrier)."""
    return c.execute(
        "SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE m.sender_id='keeper' "
        "AND json_extract(m.refs,'$.carries')=?",
        (message_id,),
    ).fetchone()[0]


def test_a_persons_home_message_carried_by_a_notice_is_claimed_before_an_older_notice(api):
    from backend import threads
    from backend.models import Claim

    who = _ops_runner(api)
    execution, store = api.app.state.execution, api.app.state.store
    with store.transaction() as c:
        older = H.say(c, H.KEEPER, 'bot:ops', 'Older keeper notice', kind='notice')
        older_job = _wake_by_hand(c, older['id'], 'ops')
        home = threads.home(c, 'bot:ops', create=True)
        mine = threads.post(c, 'human:ana', home['id'], 'Stop the April numbers')
        carrier = _carrier_job(c, mine['id'])
        c.execute("UPDATE jobs SET created_at=? WHERE id=?", ('2026-09-01T00:00:00Z', older_job))
        c.execute("UPDATE jobs SET created_at=? WHERE id=?", ('2026-09-02T00:00:00Z', carrier))
        claimed = execution.candidate(c, who, Claim(), execution.runner(c, who))
        assert claimed['id'] == carrier


def test_a_bots_message_carried_by_a_notice_keeps_its_place_behind_an_older_notice(api):
    from backend.models import Claim

    who = _ops_runner(api)
    execution, store = api.app.state.execution, api.app.state.store
    with store.transaction() as c:
        older = H.say(c, H.KEEPER, 'bot:ops', 'Older keeper notice', kind='notice')
        said = H.say(c, 'bot:finance', 'human:ana', 'Finance has the numbers')
        carrier = H.say(
            c,
            H.KEEPER,
            'bot:ops',
            'bot:finance: Finance has the numbers',
            kind='notice',
            refs={'carries': said['id'], 'from': 'bot:finance', 'wake': 'thread'},
        )
        older_job = _wake_by_hand(c, older['id'], 'ops')
        carrier_job = _wake_by_hand(c, carrier['id'], 'ops')
        c.execute("UPDATE jobs SET created_at=? WHERE id=?", ('2026-09-01T00:00:00Z', older_job))
        c.execute("UPDATE jobs SET created_at=? WHERE id=?", ('2026-09-02T00:00:00Z', carrier_job))
        claimed = execution.candidate(c, who, Claim(), execution.runner(c, who))
        assert claimed['id'] == older_job


def test_a_carried_persons_home_chat_stays_claimable_past_an_interrupted_run_on_the_bot(api):
    from backend import threads
    from backend.execution import queued_jobs_sql

    _ops_runner(api)
    with api.app.state.store.transaction() as c:
        home = threads.home(c, 'bot:ops', create=True)
        earlier = threads.post(c, 'human:ana', home['id'], 'Earlier question')
        c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (_carrier_job(c, earlier['id']),))
        mine = threads.post(c, 'human:ana', home['id'], 'Now this one')
        free = {row['id']: row['free'] for row in c.execute(queued_jobs_sql(), ('ops',))}
        assert free[_carrier_job(c, mine['id'])] == 1


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
    c.execute(
        "CREATE TABLE service_health(service TEXT PRIMARY KEY, last_succeeded_at TEXT, last_error TEXT, "
        "detail TEXT NOT NULL DEFAULT '{}')"
    )
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
    c.execute(
        "CREATE TABLE service_health(service TEXT PRIMARY KEY, last_succeeded_at TEXT, last_error TEXT, "
        "detail TEXT NOT NULL DEFAULT '{}')"
    )
    for service in ('backup', 'backup-daily', 'backup-monthly', 'restore-check', 'processing:r1', 'scheduler'):
        c.execute("INSERT INTO service_health VALUES(?,'2026-09-30',NULL,'{}')", (service,))
    monkeypatch.delenv('TICO_BACKUP_MODE', raising=False)
    retire_health_rows(c)  # no Litestream here: the snapshot timers may still run
    assert {r[0] for r in c.execute('SELECT service FROM service_health')} == {
        'backup',
        'backup-daily',
        'backup-monthly',
        'restore-check',
        'scheduler',
    }
    monkeypatch.setenv('TICO_BACKUP_MODE', 'remote')
    retire_health_rows(c)
    assert [r[0] for r in c.execute('SELECT service FROM service_health')] == ['scheduler']
    c.close()
