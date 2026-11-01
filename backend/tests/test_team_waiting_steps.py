"""A team's own waiting step holds the tickets a person parks there, and an upgrade never sends a ticket to a stale step."""
import sqlite3

import pytest

from backend import hubdb as H, task_blocked, task_relations as TR
from backend.tests.test_review_gate import api, ticket, move, get
from backend.tests.test_github import post


def _park_step(api, task):
    with api.app.state.store.transaction() as c:
        typ = H.type_get(c, task['type_id'])
        H.type_update(c, H.KEEPER, typ['id'], steps=[*typ['steps'], {'name': 'Waiting On Dependency', 'status': 'waiting'}])
        return next(s['id'] for s in H.type_get(c, typ['id'])['steps'] if s['name'] == 'Waiting On Dependency')


def test_a_ticket_parked_by_hand_in_a_teams_waiting_step_stays_and_holds_never_land_there(api):
    parked = ticket(api)
    held = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Improve alerts', 'body': 'Build alerts.',
                               'type': parked['type_id'], 'step': 'In Progress'})
    step = _park_step(api, parked)
    assert move(api, parked, 'Waiting On Dependency').status_code == 200
    with api.app.state.store.transaction() as c:
        row = H.task(c, parked['id'])
        assert (row['step_id'], row['status'], row['blocked_kind']) == (step, 'waiting', 'manual')
        # Neither the hourly sweep nor a restart moves it out: a person parked it.
        H.sweep_stranded(c, H.shift(H.now(), hours=H.STRANDED_GRACE_HOURS + 1))
        H.migrate(c)
        assert H.task(c, parked['id'])['step_id'] == step
        assert any(s['id'] == step for s in H.type_get(c, parked['type_id'])['steps'])
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


def _without_step(api, task, step):
    with api.app.state.store.transaction() as c:
        typ = H.type_get(c, task['type_id'])
        H.type_update(c, H.KEEPER, typ['id'], steps=[s for s in typ['steps'] if s['id'] != step])


def test_deleting_a_waiting_step_returns_its_parked_ticket_to_the_step_it_left(api):
    parked = ticket(api)
    step = _park_step(api, parked)
    assert move(api, parked, 'Waiting On Dependency').status_code == 200
    _without_step(api, parked, step)
    with api.app.state.store.transaction() as c:
        row = H.task(c, parked['id'])
        assert (row['step_id'], row['status'], row['blocked_since']) == (parked['step_id'], 'doing', None)


def test_deleting_a_waiting_step_sends_a_ticket_whose_return_step_is_gone_to_on_deck(api):
    parked = ticket(api)
    step = _park_step(api, parked)
    with api.app.state.store.transaction() as c:
        typ = H.type_get(c, parked['type_id'])
        H.type_update(c, H.KEEPER, typ['id'], steps=[{'name': 'On Deck', 'status': 'open'}, *typ['steps']])
        deck = next(s['id'] for s in H.type_get(c, typ['id'])['steps'] if s['name'] == 'On Deck')
    assert move(api, parked, 'Waiting On Dependency').status_code == 200
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET blocked_from_step='a step long gone' WHERE id=?", (parked['id'],))
    _without_step(api, parked, step)
    with api.app.state.store.transaction() as c:
        row = H.task(c, parked['id'])
        assert (row['step_id'], row['status'], row['blocked_since']) == (deck, 'open', None)


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
    events = c.execute('SELECT count(*) FROM task_events').fetchone()[0]
    H.migrate(c)
    assert c.execute('SELECT count(*) FROM task_events').fetchone()[0] == events
    for ident in ('retired', 'chosen'):
        c.execute("UPDATE tasks SET blocked_kind='dependency' WHERE id=?", (ident,))
        task_blocked.reconcile(c, ident)
    assert H.task(c, 'retired')['step_id'] == 'deck'
    assert H.task(c, 'chosen')['step_id'] == 'staging'
    c.close()


def _held_on_a_blocker(api, task):
    """The ticket is in Blocked, held on a blocker another bot owns: the hold the hourly sweep takes."""
    blocker = post(api, 'tasks', {'owner_id': 'cmo', 'title': 'Ship the dependency', 'body': 'Build it.'})
    with api.app.state.store.transaction() as c:
        TR.relate(c, H.KEEPER, task['id'], blocker['id'], 'blocked_by')
        task_blocked.sweep(c, H.now())
        row = H.task(c, task['id'])
        assert (row['blocked_kind'], row['status']) == ('dependency', 'waiting')
    return blocker


def test_a_person_parking_a_held_ticket_in_a_teams_waiting_step_makes_the_hold_manual(api):
    held = ticket(api)
    step = _park_step(api, held)
    blocker = _held_on_a_blocker(api, held)
    # A board drag into the team's column sends no note.
    assert move(api, held, 'Waiting On Dependency').status_code == 200
    with api.app.state.store.transaction() as c:
        assert H.task(c, held['id'])['blocked_kind'] == 'manual'
        # The dependency is done: the ticket stays in the step a person parked it in, not at the step it was held from.
        H.task_update(c, H.KEEPER, blocker['id'], status='done')
        assert (H.task(c, held['id'])['step_id'], H.task(c, held['id'])['status']) == (step, 'waiting')


def test_the_stranded_sweep_leaves_a_ticket_a_person_parked_while_it_was_held(api):
    held = ticket(api)
    step = _park_step(api, held)
    blocker = _held_on_a_blocker(api, held)
    assert move(api, held, 'Waiting On Dependency').status_code == 200
    with api.app.state.store.transaction() as c:
        # The blocker finished without telling the ticket: only the sweep is left to notice.
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (blocker['id'],))
        H.sweep_stranded(c, H.shift(H.now(), hours=H.STRANDED_GRACE_HOURS + 1))
        assert H.task(c, held['id'])['step_id'] == step


def test_an_upgrade_parks_a_bots_ticket_in_a_teams_waiting_step_on_any_board_and_the_sweep_leaves_it(api):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Content', [{'name': 'Drafting', 'status': 'doing'},
                                                     {'name': 'Waiting On Review', 'status': 'waiting'}])
    task = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Draft the guide', 'body': 'Write it.',
                               'type': typ['id'], 'step': 'Drafting'})
    with api.app.state.store.transaction() as c:
        step = next(s['id'] for s in H.type_get(c, typ['id'])['steps'] if s['name'] == 'Waiting On Review')
        # Parked before the upgrade: in the team's waiting step, with no hold recorded.
        c.execute("UPDATE tasks SET status='waiting', step_id=? WHERE id=?", (step, task['id']))
        H.migrate(c)
        assert H.task(c, task['id'])['blocked_kind'] == 'manual'
        H.sweep_stranded(c, H.shift(H.now(), hours=H.STRANDED_GRACE_HOURS + 1))
        assert (H.task(c, task['id'])['step_id'], H.task(c, task['id'])['status']) == (step, 'waiting')


def test_a_person_parks_a_bots_own_ticket_in_a_teams_waiting_step_without_a_note(api):
    parked = ticket(api)
    step = _park_step(api, parked)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET requester_id=owner_id WHERE id=?', (parked['id'],))    # the bot asked for it itself
        # The bot's own move still says why: nobody else will answer a ticket it parks for itself.
        with pytest.raises(H.Refused) as refused:
            H.task_update(c, 'bot:cpo', parked['id'], step='Waiting On Dependency')
        assert refused.value.rule == 'lint'
    moved = move(api, parked, 'Waiting On Dependency')
    assert moved.status_code == 200, moved.text
    with api.app.state.store.transaction() as c:
        row = H.task(c, parked['id'])
        assert (row['step_id'], row['status'], row['blocked_kind']) == (step, 'waiting', 'manual')


def test_a_person_moving_a_bots_ticket_into_a_teams_blocked_step_on_a_content_board_holds_it_by_hand(api):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Content', [{'name': 'Drafting', 'status': 'doing'},
                                                     {'name': 'Blocked', 'status': 'waiting'}])
    task = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Draft the guide', 'body': 'Write it.',
                               'type': typ['id'], 'step': 'Drafting'})
    blocker = post(api, 'tasks', {'owner_id': 'cmo', 'title': 'Ship the dependency', 'body': 'Build it.'})
    with api.app.state.store.transaction() as c:
        TR.relate(c, H.KEEPER, task['id'], blocker['id'], 'blocked_by')
        step = next(s['id'] for s in H.type_get(c, typ['id'])['steps'] if s['name'] == 'Blocked')
    # The blocker is open while the person parks the ticket: only the manual hold keeps it there once it finishes.
    assert move(api, task, 'Blocked').status_code == 200
    with api.app.state.store.transaction() as c:
        row = H.task(c, task['id'])
        assert (row['step_id'], row['status'], row['blocked_kind']) == (step, 'waiting', 'manual')
        H.task_update(c, H.KEEPER, blocker['id'], status='done')
        assert (H.task(c, task['id'])['step_id'], H.task(c, task['id'])['status']) == (step, 'waiting')
        H.sweep_stranded(c, H.shift(H.now(), hours=H.STRANDED_GRACE_HOURS + 1))
        assert (H.task(c, task['id'])['step_id'], H.task(c, task['id'])['status']) == (step, 'waiting')


def test_an_upgrade_holds_a_bots_ticket_already_in_a_teams_blocked_step_on_a_content_board(api):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Content', [{'name': 'Drafting', 'status': 'doing'},
                                                     {'name': 'Blocked', 'status': 'waiting'}])
    task = post(api, 'tasks', {'owner_id': 'cpo', 'title': 'Draft the guide', 'body': 'Write it.',
                               'type': typ['id'], 'step': 'Drafting'})
    with api.app.state.store.transaction() as c:
        step = next(s['id'] for s in H.type_get(c, typ['id'])['steps'] if s['name'] == 'Blocked')
        # Parked before the upgrade: in the team's Blocked step, with no hold recorded.
        c.execute("UPDATE tasks SET status='waiting', step_id=? WHERE id=?", (step, task['id']))
        H.migrate(c)
        assert H.task(c, task['id'])['blocked_kind'] == 'manual'
        H.sweep_stranded(c, H.shift(H.now(), hours=H.STRANDED_GRACE_HOURS + 1))
        assert (H.task(c, task['id'])['step_id'], H.task(c, task['id'])['status']) == (step, 'waiting')
