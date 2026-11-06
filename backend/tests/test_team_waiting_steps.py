"""A team's own waiting step holds the tickets a person parks there, and an upgrade never sends a ticket to a stale step."""
import sqlite3

import pytest

from backend import hubdb as H, task_blocked, task_relations as TR
from backend.tests.test_review_gate import api, ticket, move, get
from backend.tests.test_github import post
from backend.tests.hold_rows import set_holds


def _park_step(api, task):
    with api.app.state.store.transaction() as c:
        typ = H.type_get(c, task['type_id'])
        H.type_update(c, H.KEEPER, typ['id'], steps=[*typ['steps'], {'name': 'Waiting On Dependency', 'status': 'waiting'}])
        return next(s['id'] for s in H.type_get(c, typ['id'])['steps'] if s['name'] == 'Waiting On Dependency')


def _without_step(api, task, step):
    with api.app.state.store.transaction() as c:
        typ = H.type_get(c, task['type_id'])
        H.type_update(c, H.KEEPER, typ['id'], steps=[s for s in typ['steps'] if s['id'] != step])


def _dev(tmp_path):
    c = sqlite3.connect(tmp_path / 'hub.db', isolation_level=None)
    c.row_factory = sqlite3.Row
    H.migrate(c)
    c.execute("INSERT INTO task_types(id,name,created_at,updated_at) VALUES('dev','Dev tickets','x','x')")
    for position, (ident, name, status) in enumerate([
            ('deck', 'On Deck', 'open'), ('progress', 'In Progress', 'doing'),
            ('staging', 'Pending Push To Staging', 'doing'), ('closed', 'Closed', 'closed')]):
        c.execute('INSERT INTO task_steps(id,type_id,name,position,status) VALUES(?,?,?,?,?)',
                  (ident, 'dev', name, position, status))
    for ident in ('retired', 'chosen'):
        # Held in the working step they are in: a hold moves no ticket, so only its status says it waits.
        c.execute("INSERT INTO tasks(id,title,status,body,note,type_id,step_id) VALUES(?,?,'waiting','x','','dev','staging')",
                  (ident, 'A ticket'))
        H._task_event(c, ident, 'human:ana', 'step', 'progress', 'staging')
    # Retiring a waiting step put this one back in the step its history last named, weeks old.
    H._task_event(c, 'retired', H.KEEPER, 'step', 'old-waiting', 'staging', H.RETIRED_WAITING)
    return c


def test_a_return_step_an_upgrade_made_resumes_at_on_deck_and_a_chosen_one_is_kept(tmp_path):
    c = _dev(tmp_path)
    H.migrate(c)
    retired, chosen = H.task(c, 'retired'), H.task(c, 'chosen')
    assert retired['step_id'] == chosen['step_id'] == 'staging'
    assert (retired['blocked_from_step'], retired['blocked_from_status']) == (None, 'open')
    assert chosen['blocked_from_step'] == 'staging'
    events = c.execute("SELECT count(*) FROM history_events WHERE subject_type='task'").fetchone()[0]
    H.migrate(c)
    assert c.execute("SELECT count(*) FROM history_events WHERE subject_type='task'").fetchone()[0] == events
    for ident in ('retired', 'chosen'):
        set_holds(c, ident, blocked_kind='dependency')
        task_blocked.reconcile(c, ident)
    assert H.task(c, 'retired')['step_id'] == 'deck'
    assert H.task(c, 'chosen')['step_id'] == 'staging'
    c.close()


def _held_on_a_blocker(api, task):
    """The ticket is in Blocked, held on a blocker another bot owns: the hold the hourly sweep takes."""
    blocker = post(api, 'tasks', {'owner': 'cmo', 'title': 'Ship the dependency', 'body': 'Build it.'})
    with api.app.state.store.transaction() as c:
        TR.relate(c, H.KEEPER, task['id'], blocker['id'], 'blocked_by')
        task_blocked.sweep(c, H.now())
        row = H.task(c, task['id'])
        assert (row['blocked_kind'], row['status']) == ('dependency', 'waiting')
    return blocker


def test_a_parked_ticket_stays_a_held_one_goes_manual_and_deleting_the_step_returns_it(api):
    parked = ticket(api)
    typ_id = parked['type_id']

    def another(title, step='In Progress'):
        return post(api, 'tasks', {'owner': 'cpo', 'title': title, 'body': 'Build it.', 'type': typ_id, 'step': step})

    held = another('Improve alerts')
    step = _park_step(api, parked)
    # A ticket parked by hand in a team's waiting step stays, and holds never land there.
    assert move(api, parked, 'Waiting On Dependency').status_code == 200
    with api.app.state.store.transaction() as c:
        row = H.task(c, parked['id'])
        assert (row['step_id'], row['status'], row['blocked_kind']) == (step, 'waiting', 'manual')
        # A restart does not move it out: a person parked it.
        H.migrate(c)
        assert H.task(c, parked['id'])['step_id'] == step
        assert any(s['id'] == step for s in H.type_get(c, typ_id)['steps'])
        # A hold Tico finds keeps the ticket in its step, and never moves it into the team's.
        task_blocked.hold(c, held['id'], 'Waiting on a dependency')
        assert (H.task(c, held['id'])['step_id'], H.task(c, held['id'])['status']) == (held['step_id'], 'waiting')
        # A status write on the parked ticket keeps it in its step.
        H._set_status(c, H.KEEPER, H.task(c, parked['id']), 'waiting', note='Still waiting')
        assert H.task(c, parked['id'])['step_id'] == step
    assert move(api, get(api, 'tasks/' + parked['id'])['task'], 'In Progress').status_code == 200
    with api.app.state.store.transaction() as c:
        row = H.task(c, parked['id'])
        assert (row['status'], row['blocked_since']) == ('doing', None)

    # A person parking a held ticket in a team's waiting step makes the hold manual.
    heldback = another('Held on a blocker')
    blocker = _held_on_a_blocker(api, heldback)
    assert move(api, heldback, 'Waiting On Dependency').status_code == 200   # a board drag sends no note
    with api.app.state.store.transaction() as c:
        assert H.task(c, heldback['id'])['blocked_kind'] == 'manual'
        # The dependency is done: the ticket stays in the step a person parked it in, not at the step it was held from.
        H.task_update(c, H.KEEPER, blocker['id'], status='done')
        assert (H.task(c, heldback['id'])['step_id'], H.task(c, heldback['id'])['status']) == (step, 'waiting')

    # A person parks a bot's own ticket without a note.
    own = another('Its own ticket')
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET requester_id=owner_id WHERE id=?', (own['id'],))    # the bot asked for it itself
        # The bot's own move still says why: nobody else will answer a ticket it parks for itself.
        with pytest.raises(H.Refused) as refused:
            H.task_update(c, 'bot:cpo', own['id'], step='Waiting On Dependency')
        assert refused.value.rule == 'lint'
    moved = move(api, own, 'Waiting On Dependency')
    assert moved.status_code == 200, moved.text
    with api.app.state.store.transaction() as c:
        row = H.task(c, own['id'])
        assert (row['step_id'], row['status'], row['blocked_kind']) == (step, 'waiting', 'manual')

    # Deleting the waiting step returns a parked ticket to the step it left, or to On Deck when that is gone too.
    back = another('Parked to return')
    gone = another('Return step gone')
    with api.app.state.store.transaction() as c:
        typ = H.type_get(c, typ_id)
        H.type_update(c, H.KEEPER, typ_id, steps=[{'name': 'On Deck', 'status': 'open'}, *typ['steps']])
        deck = next(s['id'] for s in H.type_get(c, typ_id)['steps'] if s['name'] == 'On Deck')
    assert move(api, back, 'Waiting On Dependency').status_code == 200
    assert move(api, gone, 'Waiting On Dependency').status_code == 200
    with api.app.state.store.transaction() as c:
        set_holds(c, gone['id'], blocked_from_step='a step long gone')
    _without_step(api, parked, step)
    with api.app.state.store.transaction() as c:
        row = H.task(c, back['id'])
        assert (row['step_id'], row['status'], row['blocked_since']) == (back['step_id'], 'doing', None)
        row = H.task(c, gone['id'])
        assert (row['step_id'], row['status'], row['blocked_since']) == (deck, 'open', None)


def test_an_upgrade_or_a_person_parks_a_bots_ticket_in_a_teams_waiting_or_blocked_step_on_a_content_board(api):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Content', [{'name': 'Drafting', 'status': 'doing'},
                                                     {'name': 'Waiting On Review', 'status': 'waiting'},
                                                     {'name': 'Blocked', 'status': 'waiting'}])

    def draft(title):
        return post(api, 'tasks', {'owner': 'cpo', 'title': title, 'body': 'Write it.',
                                   'type': typ['id'], 'step': 'Drafting'})
    review, upgraded, parked = draft('Draft the guide'), draft('Draft the other guide'), draft('Draft the third guide')
    blocker = post(api, 'tasks', {'owner': 'cmo', 'title': 'Ship the dependency', 'body': 'Build it.'})
    with api.app.state.store.transaction() as c:
        TR.relate(c, H.KEEPER, parked['id'], blocker['id'], 'blocked_by')
        ids = {s['name']: s['id'] for s in H.type_get(c, typ['id'])['steps']}
    # The blocker is open while the person parks the ticket: only the manual hold keeps it there once it finishes.
    assert move(api, parked, 'Blocked').status_code == 200
    with api.app.state.store.transaction() as c:
        row = H.task(c, parked['id'])
        assert (row['step_id'], row['status'], row['blocked_kind']) == (ids['Blocked'], 'waiting', 'manual')
        H.task_update(c, H.KEEPER, blocker['id'], status='done')
        assert (H.task(c, parked['id'])['step_id'], H.task(c, parked['id'])['status']) == (ids['Blocked'], 'waiting')
        # An upgrade parks a bot's ticket already in a team's waiting or blocked step: parked before the upgrade, with
        # no hold recorded.
        c.execute("UPDATE tasks SET status='waiting', step_id=? WHERE id=?", (ids['Waiting On Review'], review['id']))
        c.execute("UPDATE tasks SET status='waiting', step_id=? WHERE id=?", (ids['Blocked'], upgraded['id']))
        H.migrate(c)
        for task, step in ((review, ids['Waiting On Review']), (upgraded, ids['Blocked'])):
            assert H.task(c, task['id'])['blocked_kind'] == 'manual'
            assert (H.task(c, task['id'])['step_id'], H.task(c, task['id'])['status']) == (step, 'waiting')
