"""Urgent notice delivery must not release the work that a dependency holds."""
import json

import pytest

from backend import auto_tasks, hubdb as H, rules as R, wake_rules
from backend.tests.test_api import api, assign, claim, post, ready, runner  # noqa: F401
from backend.tests.test_profiles import put
from backend.tests.test_urgent_news import sent_job
from backend.tests.test_wake_rules import as_cause


def parked(c, profile='lead', party='owned', incident=False):
    put(c, 'cpo', profile)
    owner = 'bot:cpo' if party == 'owned' else 'bot:ops'
    requester = 'bot:cpo' if party == 'requested' else 'bot:ops'
    blocker = H.task_create(c, 'bot:ops', 'Prepare the machine', 'Synthetic prerequisite', 'bot:ops')
    task = H.task_create(c, requester, 'Restore the service', 'Synthetic held work', owner,
                         labels=['incident'] if incident else [])
    H.TR.add_blocker(c, 'human:ana', task['id'], blocker['id'])
    c.execute('DELETE FROM jobs')
    return H.task(c, task['id']), blocker


def still_blocked(c, task, blocker):
    assert H.live_blocker(c, task['id']) == blocker['id']
    assert H.task(c, blocker['id'])['status'] == 'open'


def machine(api):
    r = runner(api)
    assign(api, r, 'cpo')
    ready(api, r, ['cpo'])
    return r


@pytest.mark.parametrize('profile', ['lead', 'doer', 'custom'])
@pytest.mark.parametrize('party', ['owned', 'requested'])
def test_parked_party_blocker_delivers_a_usable_notice_job(api, profile, party):
    r = machine(api)
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, profile, party)
        msg, job = sent_job(c, task, 'bot:cpo', 'A blocker needs attention', {'news': 'task_blocked'})
        assert R.urgent_rule(c, H.KEEPER, 'bot:cpo', {'task': task['id']}, None, 'task_blocked') == 'task_blocker'
        # A row or built-in that already wakes keeps its own name.
        expected = ('profile_lead_task_blocked' if profile == 'lead' else 'task_blocked') \
            if party == 'requested' else 'task_blocker'
        assert msg['refs']['rule'] == expected
        assert job and job['state'] == 'queued' and job['held'] == 0
        still_blocked(c, task, blocker)
    attempt = claim(api, r, 'cpo')
    assert attempt is not None, 'A queued urgent notice must be usable while its work remains blocked'
    assert attempt['message']['id'] == msg['id']
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'notice-session'}, token=r['token'])
    with api.app.state.store.read() as c:
        assert H.message(c, msg['id'])['delivered_at']
        still_blocked(c, task, blocker)


@pytest.mark.parametrize('profile', ['lead', 'doer', 'custom'])
def test_unrelated_blocker_keeps_its_existing_audience(api, profile):
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, profile, party='unrelated')
        msg, job = sent_job(c, task, 'bot:cpo', 'Another task is blocked', {'news': 'task_blocked'})
        if profile == 'lead':
            assert msg['refs']['rule'] == 'task_blocker' and job and job['held'] == 0
        else:
            assert job is None and msg['refs']['quiet']
        still_blocked(c, task, blocker)


@pytest.mark.parametrize('profile', ['lead', 'doer', 'custom'])
def test_parked_incident_step_notice_wakes_without_moving_the_task(api, profile):
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, profile, incident=True)
        # This is a notice payload, not a successful transition through an open dependency.
        msg, job = sent_job(c, task, 'bot:cpo', 'Step moved: On Deck -> In Progress',
                            {'news': 'step_entered'})
        assert msg['refs']['rule'] == 'incident_news'
        assert job and job['held'] == 0 and job['state'] == 'queued'
        assert H.task(c, task['id'])['status'] == task['status']
        still_blocked(c, task, blocker)


def test_nonurgent_parked_step_stays_a_note(api):
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c)
        msg, job = sent_job(c, task, 'bot:cpo', 'Step moved: On Deck -> In Progress', {'news': 'step_entered'})
        assert job is None and msg['refs']['held_by'] == 'parked'
        still_blocked(c, task, blocker)


@pytest.mark.parametrize('control', ['quiet', 'ignore', 'own_write', 'snooze', 'duplicate'])
def test_parked_incident_keeps_silence_and_duplicate_controls(api, control):
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, incident=True)
        refs = {'news': 'step_entered', **({'quiet': True} if control == 'quiet' else {})}
        if control == 'ignore':
            c.execute("INSERT INTO rules(id,scope_kind,event,then_outcome,created_by,created,updated) "
                      "VALUES('ignore-all','team','step_entered','ignore','human:ana',?,?)", (H.now(), H.now()))
        if control == 'snooze':
            H.TS.snooze(c, 'human:ana', task['id'], H.shift(H.now(), days=1))
            c.execute('DELETE FROM jobs')
            task = H.task(c, task['id'])
        if control == 'duplicate':
            _, first = sent_job(c, task, 'bot:cpo', 'Step moved: On Deck -> In Progress', refs)
            assert first and first['state'] == 'queued' and first['held'] == 0
        with as_cause('bot:cpo' if control == 'own_write' else 'bot:ops'):
            msg, job = sent_job(c, task, 'bot:cpo', 'Step moved: On Deck -> In Progress', refs)
        if control == 'duplicate':
            assert job and job['state'] == 'cancelled' and job['wake_class'] == 'duplicate'
            assert c.execute("SELECT count(*) FROM jobs WHERE bot='cpo' AND state='queued'").fetchone()[0] == 1
        else:
            assert job is None and msg['refs']['quiet']
        still_blocked(c, task, blocker)


def test_notice_does_not_release_the_task_start_gate(api):
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, incident=True)
        _, job = sent_job(c, task, 'bot:cpo', 'Step moved: On Deck -> In Progress', {'news': 'step_entered'})
        assert job and job['held'] == 0
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='cpo'").fetchone()[0])
        assert auto_tasks.refusal(c, H.task(c, task['id']), config, woken=True) == 'task_wait'
        assert wake_rules.parked(c, H.task(c, task['id'])) == 'blocked_by'
        still_blocked(c, task, blocker)


def test_real_step_notice_writer_keeps_the_hold(api):
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, incident=True)
        R.step_entered(c, H.KEEPER, task)
        found = c.execute("SELECT j.held,m.refs_json FROM jobs j JOIN messages m ON m.id=j.message_id "
                          "WHERE j.bot='cpo'").fetchone()
        assert found and found['held'] == 0
        assert json.loads(found['refs_json'])['rule'] == 'incident_news'
        still_blocked(c, task, blocker)


def test_urgent_notice_keeps_real_dependency_move_refusals(api):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Dev tickets', [
            {'name': 'On Deck', 'status': 'open'}, {'name': 'In Progress', 'status': 'doing'},
            {'name': 'Review 1', 'status': 'review'}, {'name': 'Done', 'status': 'done'}])
        task = H.task_create(c, 'bot:ops', 'Restore the service', 'Synthetic held work', 'bot:cpo',
                             type=typ['id'], step='On Deck', labels=['incident'])
        blocker = H.task_create(c, 'bot:ops', 'Prepare the machine', 'Synthetic prerequisite', 'bot:ops')
        H.TR.add_blocker(c, 'human:ana', task['id'], blocker['id'])
        c.execute('DELETE FROM jobs')
        _, job = sent_job(c, H.task(c, task['id']), 'bot:cpo', 'The incident needs attention',
                          {'news': 'step_entered'})
        assert job and job['held'] == 0
        for fields in ({'status': 'doing'}, {'step': 'In Progress'}, {'step': 'Review 1'}):
            moved = H.task_update(c, 'bot:cpo', task['id'], **fields)
            assert H.task(c, task['id'])['status'] == 'waiting'
            assert any('remains Blocked' in warning for warning in moved.get('warnings', []))
            still_blocked(c, task, blocker)
