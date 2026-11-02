"""Task-rule permission and reversible-close boundaries; isolated synthetic team fixtures."""
import json
from datetime import datetime, timedelta, timezone

from backend import task_rules as R
from backend.store import H
from backend.tests.test_api import api, get, post, put, setup_attempt  # noqa: F401

AT = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def department(api):
    with api.app.state.store.transaction() as c:
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster['org_groups'] = {'engineering': {'name': 'Engineering'}, 'web': {'name': 'Web', 'parent': 'engineering'}, 'marketing': {'name': 'Marketing'}}
        next(p for p in roster['people'] if p['id'] == 'cara')['team'] = 'web'
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))


def done(api, monkeypatch, title='Prepare export', hours=25):
    old = R.SR._stamp(AT - timedelta(hours=hours))
    with monkeypatch.context() as m:
        m.setattr(H, 'now', lambda: old)
        with api.app.state.store.transaction() as c:
            task = H.task_create(c, 'human:ana', title, 'Synthetic fixture', 'human:ana', deduplicate=False)
            H.task_update(c, 'human:ana', task['id'], status='done')
    return task


def run(api, at=AT):
    failures = []
    R.tick(api.app.state.store, at, failures)
    return failures


def task(api, key):
    with api.app.state.store.read() as c:
        return dict(c.execute('SELECT * FROM tasks WHERE id=?', (key,)).fetchone())


def test_department_membership_defaults_and_server_permissions(api):
    department(api)
    data = get(api, 'system-routines', token='cara-test')
    seed = next(j for j in data['jobs'] if j['key'] == R.DEFAULT)
    assert seed['on'] and seed['kind'] == 'task_rule' and seed['can_change']
    assert seed['triggers'] == [{'key': 'done_untouched', 'hours': 24}]
    assert seed['actions'] == ['close'] and seed['department']['id'] == 'engineering'
    created = post(api, 'task-rules', {'name': 'Close reviewed exports'}, token='cara-test')['job']
    assert created['department']['id'] == 'engineering'
    assert put(api, 'task-rules/' + created['key'], {'on': False}, token='cara-test')['job']['on'] is False
    marketing = post(api, 'task-rules', {'department': 'marketing'})['job']
    put(api, 'task-rules/' + marketing['key'], {'on': False}, token='cara-test', expected=403)
    put(api, 'task-rules/' + created['key'], {'department': 'marketing'}, token='cara-test', expected=403)
    with api.app.state.store.read() as c:
        before = R.get(c, marketing['key'])
    put(api, 'task-rules/' + marketing['key'], {
        'department': 'engineering', 'name': 'Taken rule', 'on': False,
        'triggers': [{'key': 'done_untouched', 'hours': 48}], 'actions': ['close'],
    }, token='cara-test', expected=403)
    with api.app.state.store.read() as c:
        assert R.get(c, marketing['key']) == before
    assert put(api, 'task-rules/' + marketing['key'], {'on': False}, token='ben-test')['job']['on'] is False
    post(api, 'task-rules', {'department': 'Web'}, expected=422)
    post(api, 'task-rules', {'department': 'engineering', 'actions': ['delete']}, expected=422)
    put(api, 'task-rules/' + created['key'], {'triggers': [{'key': 'done_untouched', 'hours': 0}]}, expected=422)
    put(api, 'system-routines/health_scan', {'on': True}, token='cara-test', expected=403)


def test_full_wait_one_history_note_and_reopen_undo(api, monkeypatch):
    old = done(api, monkeypatch)
    young = done(api, monkeypatch, 'Recent export', hours=23)
    edge = done(api, monkeypatch, 'Exactly one day', hours=24)
    assert not run(api)
    assert task(api, old['id'])['status'] == task(api, edge['id'])['status'] == 'closed'
    assert task(api, young['id'])['status'] == 'done'
    with api.app.state.store.read() as c:
        notes = c.execute('SELECT note FROM task_events WHERE task_id=?', (old['id'],)).fetchall()
        assert [r[0] for r in notes].count("Closed by rule 'Close 24h after done'") == 1
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'human:ana', old['id'], status='open')
    assert task(api, old['id'])['closed_at'] is None
    assert not run(api, AT + timedelta(minutes=16))
    assert task(api, old['id'])['status'] == 'open'


def test_activity_and_all_holds_protect_done_tasks(api, monkeypatch):
    protected = []
    for label in ('comment', 'edit', 'reopened', 'waiting', 'snoozed', 'pin', 'question', 'approval'):
        t = done(api, monkeypatch, label)
        protected.append(t['id'])
        with monkeypatch.context() as m:
            m.setattr(H, 'now', lambda: R.SR._stamp(AT - timedelta(hours=1)))
            with api.app.state.store.transaction() as c:
                if label == 'comment': H.task_comment(c, 'human:ana', t['id'], 'Needs another look', wake=False)
                elif label == 'edit': H.task_update(c, 'human:ana', t['id'], body='Edited fixture')
                elif label == 'reopened':
                    H.task_update(c, 'human:ana', t['id'], status='open')
                    H.task_update(c, 'human:ana', t['id'], status='done')
                elif label == 'waiting': c.execute("UPDATE tasks SET waiting_on='human:ana' WHERE id=?", (t['id'],))
                elif label == 'snoozed': c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", ((AT + timedelta(days=1)).isoformat(), t['id']))
                elif label == 'pin': c.execute("UPDATE tasks SET labels='[\"pinned\"]' WHERE id=?", (t['id'],))
                elif label == 'question': H.task_comment(c, 'human:ana', t['id'], 'Should this stay open?', wake=False, ask={'to': 'human:ana'})
                else:
                    c.execute("UPDATE tasks SET status='open' WHERE id=?", (t['id'],))
                    H.approval_request(c, 'human:ana', 'send', {'to': 'acme@example.com', 'cc': [], 'subject': 'Fixture', 'body_sha256': 'a'*64, 'mailbox': 'acme@example.com'}, task_id=t['id'])
                    c.execute("UPDATE tasks SET status='done' WHERE id=?", (t['id'],))
        # Holds must protect even when the hold itself has aged beyond the wait.
        if label in ('question', 'approval'):
            with api.app.state.store.transaction() as c:
                old = R.SR._stamp(AT - timedelta(hours=25))
                c.execute('UPDATE tasks SET updated_at=? WHERE id=?', (old, t['id']))
                c.execute('UPDATE messages SET created_at=? WHERE conversation_id=?', (old, t['conversation_id']))
    eligible = done(api, monkeypatch, 'Eligible export')
    assert not run(api)
    assert task(api, eligible['id'])['status'] == 'closed'
    assert all(task(api, key)['status'] == 'done' for key in protected)


def test_cap_off_and_failure_receipt_are_durable(api, monkeypatch):
    for n in range(52): done(api, monkeypatch, f'Export {n}')
    assert not run(api)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE status='closed'").fetchone()[0] == 50
    put(api, 'task-rules/' + R.DEFAULT, {'on': False})
    with api.app.state.store.transaction() as c:
        c.execute("DROP INDEX tasks_rule_done_due")
        c.execute("CREATE INDEX tasks_rule_due ON tasks(status,updated_at,id)")
    api.app.state.store.initialize()  # Upgrade the index without resetting the saved switch.
    with api.app.state.store.read() as c:
        indexes = {r[1] for r in c.execute("PRAGMA index_list(tasks)")}
        assert "tasks_rule_done_due" in indexes and "tasks_rule_due" not in indexes
    assert next(j for j in get(api, 'system-routines')['jobs'] if j['key'] == R.DEFAULT)['on'] is False
    run(api, AT + timedelta(hours=1))
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE status='done'").fetchone()[0] == 2
    put(api, 'task-rules/' + R.DEFAULT, {'on': True})
    original = R.ACTIONS['close'][1]
    def broken(*args):
        original(*args)  # A failure after a close must roll that close back with the batch.
        raise RuntimeError('Private fixture detail')
    monkeypatch.setitem(R.ACTIONS, 'close', ('close it', broken))
    assert run(api, AT + timedelta(hours=2)) == [{'task_rules': 'RuntimeError'}]
    data = get(api, 'system-routines')
    last = next(j for j in data['jobs'] if j['key'] == R.DEFAULT)['last']
    assert last['ok'] is False and last['result'] == 'Failed: unexpected error (RuntimeError)'
    assert 'Private fixture' not in str(data)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE status='done'").fetchone()[0] == 2


def test_bot_department_rights_and_aged_holds_do_not_starve_eligible_tasks(api, monkeypatch):
    department(api)
    _, _, attempt = setup_attempt(api)
    token = attempt['token']
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET team='engineering',config=json_set(config,'$.team','engineering') WHERE bot='ops'")
    rule = post(api, 'task-rules', {'name': 'Close exports'}, token=token)['job']
    assert rule['department']['id'] == 'engineering' and rule['can_change']
    put(api, 'task-rules/' + rule['key'], {'on': False}, token=token)
    for n in range(51):
        held = done(api, monkeypatch, f'Pinned export {n}', hours=48)
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE tasks SET labels='[\"pinned\"]' WHERE id=?", (held['id'],))
    eligible = done(api, monkeypatch, 'Eligible after holds')
    assert not run(api)
    assert task(api, eligible['id'])['status'] == 'closed'


def test_history_and_scoped_message_activity_with_old_task_timestamps(api, monkeypatch):
    history = done(api, monkeypatch, 'History only')
    scoped = [done(api, monkeypatch, name) for name in ('New message', 'Edited message', 'Deleted message')]
    unrelated = done(api, monkeypatch, 'Unrelated shared conversation')
    old = R.SR._stamp(AT - timedelta(hours=25))
    recent = R.SR._stamp(AT - timedelta(hours=1))
    shared = H.new_id()
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO conversations(id) VALUES(?)", (shared,))
        for t in [history, *scoped, unrelated]:
            c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (shared, t['id']))
        # A step-only history touch and imported message metadata must protect without tasks.updated.
        c.execute("INSERT INTO task_events(id,task_id,ts,actor,field,old,new,note) "
                  "VALUES(?,?,?,'human:ana','step','before','after','')", (H.new_id(), history['id'], recent))
        for t, field in zip(scoped, ('created_at', 'edited_at', 'deleted_at')):
            times = {'created_at': old, 'edited_at': None, 'deleted_at': None, field: recent}
            c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs,created_at,edited_at,deleted_at) "
                      "VALUES(?,?,'human:ana','human:ana','say','Fixture',?,?,?,?)",
                      (H.new_id(), shared, json.dumps({'task': t['id']}), times['created_at'], times['edited_at'], times['deleted_at']))
        # None of the message fixtures gets a recent history row; all task timestamps stay aged.
        for t in [history, *scoped, unrelated]:
            row = dict(c.execute('SELECT * FROM tasks WHERE id=?', (t['id'],)).fetchone())
            assert all(not row.get(k) or row[k] <= old for k in ('created_at', 'updated_at', 'done_at', 'checkpoint_at'))
        for t in [*scoped, unrelated]:
            assert c.execute('SELECT max(ts) FROM task_events WHERE task_id=?', (t['id'],)).fetchone()[0] <= old
    assert not run(api)
    assert task(api, history['id'])['status'] == 'done'
    assert [task(api, t['id'])['status'] for t in scoped] == ['done'] * 3
    assert task(api, unrelated['id'])['status'] == 'closed'


def test_close_budget_is_shared_by_two_due_rules(api, monkeypatch):
    put(api, 'task-rules/' + R.DEFAULT, {'triggers': [{'key': 'done_untouched', 'hours': 48}]})
    second = post(api, 'task-rules', {'department': 'engineering', 'name': 'Close after one day'})['job']
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_rules SET created_at='2020-01-01T00:00:00.000000Z' WHERE id=?", (R.DEFAULT,))
        c.execute("UPDATE task_rules SET created_at='2020-01-02T00:00:00.000000Z' WHERE id=?", (second['key'],))
    older = [done(api, monkeypatch, f'Older export {n}', hours=49) for n in range(20)]
    younger = [done(api, monkeypatch, f'Younger export {n}', hours=25) for n in range(31)]
    assert not run(api)
    assert all(task(api, t['id'])['status'] == 'closed' for t in older)
    assert sum(task(api, t['id'])['status'] == 'closed' for t in younger) == 30
    with api.app.state.store.read() as c:
        receipts = {r['job']: (r['ok'], r['result']) for r in c.execute('SELECT job,ok,result FROM system_runs')}
        assert receipts[R.DEFAULT] == (1, '20 tasks closed')
        assert receipts[second['key']] == (1, '30 tasks closed')
        notes = c.execute("SELECT note,count(*) FROM task_events WHERE field='status' AND new='closed' GROUP BY note").fetchall()
        assert dict(notes) == {"Closed by rule 'Close 24h after done'": 20, "Closed by rule 'Close after one day'": 30}
