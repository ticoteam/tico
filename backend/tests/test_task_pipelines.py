"""Pipeline migrations preserve every legacy status and custom task assignment."""

import sqlite3

from backend import hubdb as H


def test_general_backfill_is_idempotent_and_does_not_reassign_custom_tasks(tmp_path):
    path = tmp_path / 'legacy.db'
    c = sqlite3.connect(path, isolation_level=None)
    c.row_factory = sqlite3.Row
    for script in H.MIGRATIONS[:13]:
        H._apply(c, script)
    c.execute('PRAGMA user_version=13')
    for status in H.TASK_STATUSES:
        c.execute('INSERT INTO tasks(id,title,status,body,note) VALUES(?,?,?,?,?)',
                  (status, 'Keep this task', status, 'Keep these details', 'Keep this note'))
    H._apply(c, "ALTER TABLE tasks ADD COLUMN labels TEXT NOT NULL DEFAULT '[]';")
    c.execute('UPDATE tasks SET labels=?', ('["release","bug"]',))
    H.migrate(c)
    assert c.execute('PRAGMA user_version').fetchone()[0] == len(H.MIGRATIONS)
    for row in c.execute('SELECT * FROM tasks'):
        assert row['type_id'] == H.GENERAL_TYPE
        # No step is waiting or declined: a waiting task starts at Open, a declined one goes to Closed.
        assert row['step_id'] == 'general-' + {'waiting': 'open', 'declined': 'closed'}.get(row['status'], row['status'])
        assert row['status'] == row['id']
        assert row['body'] == 'Keep these details' and row['note'] == 'Keep this note'
        assert H.task_labels(H.task(c, row['id'])) == ['release', 'bug']
    # Every task already in a step keeps the order it was filed in, once.
    ranks = dict(c.execute('SELECT id, step_rank FROM tasks ORDER BY rowid').fetchall())
    assert list(ranks.values()) == sorted(ranks.values()) and None not in ranks.values()
    custom = H.type_create(c, H.KEEPER, 'Marketing', [{'name': 'Draft', 'status': 'open'}])
    c.execute('UPDATE tasks SET type_id=?,step_id=? WHERE id=?', (custom['id'], custom['steps'][0]['id'], 'open'))
    H._set_task_tags(c, H.KEEPER, 'open', ['bug'])
    H._apply(c, H.PIPELINES_SCHEMA)
    H._apply(c, H.NUMBERS_SCHEMA)
    H.migrate(c)
    assert dict(c.execute('SELECT id, step_rank FROM tasks').fetchall()) == ranks
    assert H.task(c, 'open')['type_id'] == custom['id']
    assert H.task_labels(H.task(c, 'open')) == ['bug']
    assert H.task(c, 'open')['labels'] == '["release","bug"]'
    assert c.execute('SELECT count(*) FROM task_steps WHERE type_id=?', (H.GENERAL_TYPE,)).fetchone()[0] == 6
    assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    c.close()


def _hub(tmp_path):
    c = sqlite3.connect(tmp_path / 'hub.db', isolation_level=None)
    c.row_factory = sqlite3.Row
    H.migrate(c)
    return c


def test_wont_do_steps_are_retired_once_and_a_teams_waiting_step_keeps_its_tasks(tmp_path):
    c = _hub(tmp_path)
    c.execute("INSERT INTO task_types(id,name,created_at,updated_at) VALUES('dev','Dev tickets','x','x')")
    for position, (ident, status) in enumerate([('deck', 'open'), ('progress', 'doing'), ('review', 'review'),
                                                ('closed', 'closed'), ('waiting', 'waiting'), ('wont', 'declined')]):
        c.execute('INSERT INTO task_steps(id,type_id,name,position,status) VALUES(?,?,?,?,?)',
                  (ident, 'dev', ident.title(), position, status))
    c.execute("INSERT INTO task_steps(id,type_id,name,position,status) VALUES('general-waiting','general','Waiting',9,'waiting')")
    for ident, typ, step, status in [('was-in-review', 'dev', 'waiting', 'waiting'), ('no-history', 'dev', 'waiting', 'waiting'),
                                     ('dropped', 'dev', 'wont', 'declined'), ('general', 'general', 'general-waiting', 'waiting')]:
        c.execute('INSERT INTO tasks(id,title,status,body,note,type_id,step_id) VALUES(?,?,?,?,?,?,?)',
                  (ident, 'A ticket', status, 'x', '', typ, step))
    H._task_event(c, 'was-in-review', 'human:ana', 'step', 'progress', 'review')
    H._task_event(c, 'was-in-review', 'human:ana', 'step', 'review', 'waiting')
    product = c.execute("SELECT id FROM task_steps WHERE type_id='product-roadmap' AND status='declined'").fetchall()
    H.migrate(c)
    place = dict(c.execute('SELECT id, step_id || ":" || status FROM tasks').fetchall())
    blocked = [s['id'] for s in H.type_get(c, 'dev')['steps'] if s['name'] == 'Blocked']
    # A waiting step is the team's own column: its tickets stay in it, held as if parked there by hand, never Blocked.
    assert place == {'was-in-review': 'waiting:waiting', 'no-history': 'waiting:waiting', 'dropped': 'closed:declined',
                     'general': 'general-waiting:waiting'}
    assert not blocked and not {step.split(':')[0] for step in place.values()} & set(blocked)
    assert {H.task(c, ident)['blocked_kind'] for ident in ('was-in-review', 'no-history')} == {'manual'}
    assert c.execute("SELECT count(*) FROM task_steps WHERE status='waiting' AND id IN ('waiting','general-waiting')").fetchone()[0] == 2
    assert not c.execute("SELECT 1 FROM task_steps s JOIN task_types t ON t.id=s.type_id WHERE s.status='declined' AND t.id<>'product-roadmap'").fetchone()
    # The Product roadmap's Declined is public: it stays.
    assert product and c.execute("SELECT id FROM task_steps WHERE type_id='product-roadmap' AND status='declined'").fetchall() == product
    events = c.execute("SELECT count(*) FROM task_events").fetchone()[0]
    H.migrate(c)
    assert c.execute("SELECT count(*) FROM task_events").fetchone()[0] == events
    assert dict(c.execute('SELECT id, step_id || ":" || status FROM tasks').fetchall()) == place
    assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    c.close()


def test_a_waiting_task_keeps_its_step_and_a_declined_one_closes_and_no_step_may_be_declined(tmp_path):
    c = _hub(tmp_path)
    dev = H.type_create(c, H.KEEPER, 'Dev tickets', [
        {'name': 'On Deck', 'status': 'open'}, {'name': 'Review 1', 'status': 'review'},
        {'name': 'Review 1 Fixes', 'status': 'doing'}, {'name': 'Closed', 'status': 'closed'}])
    steps = {s['name']: s['id'] for s in dev['steps']}
    c.execute("INSERT INTO tasks(id,title,status,body,note,type_id,step_id) VALUES('t','A ticket','review','x','',?,?)",
              (dev['id'], steps['Review 1']))
    state = lambda status: H._task_state(c, H.KEEPER, H.task(c, 't'), status=status)[1:]
    assert state('waiting') == (steps['Review 1'], 'waiting'), 'a held ticket keeps its step, no Blocked column'
    assert state('declined') == (steps['Closed'], 'declined')
    c.execute("UPDATE tasks SET status='waiting',step_id=? WHERE id='t'", (steps['Review 1 Fixes'],))
    assert state('doing') == (steps['Review 1 Fixes'], 'doing'), 'leaving waiting keeps a step of that status'
    c.execute("UPDATE tasks SET status='closed',step_id=? WHERE id='t'", (steps['Closed'],))
    assert state('waiting') == (steps['On Deck'], 'waiting'), 'a finished task brought back waiting starts at the first working step'
    try:
        H.type_update(c, H.KEEPER, dev['id'], steps=[*dev['steps'], {'name': 'Dropped', 'status': 'declined'}])
        raise AssertionError('a declined step was accepted')
    except H.Refused as refused:
        assert "A step can't be declined" in str(refused)
    c.close()
