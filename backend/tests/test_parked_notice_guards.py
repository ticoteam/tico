"""Notice turns retain task privacy, operator scope and the task's independent waits."""
import pytest

from backend import auto_tasks, hubdb as H, rules as R, task_privacy
from backend.tests.test_api import api, claim, get, post, runner  # noqa: F401
from backend.tests.test_parked_urgent_news import machine, parked, still_blocked
from backend.tests.test_urgent_news import sent_job
from backend.tests.test_task_lanes import task_bot


def scope(api, task):
    return post(api, 'bots/cpo/control', {'action': 'scope', 'task_id': task,
                                         'expected_revision': get(api, 'bots/cpo')['revision']})


def test_notice_delivery_keeps_operator_and_task_claim_scope(api):
    r = machine(api)
    stranger = runner(api, operator='ben')
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, incident=True)
        msg, job = sent_job(c, task, 'bot:cpo', 'The incident needs attention', {'news': 'step_entered'})
        assert job and msg['refs'][R.PARKED_NOTICE]
    assert claim(api, stranger, 'cpo') is None
    scope(api, blocker['id'])
    assert claim(api, r, 'cpo') is None
    scope(api, task['id'])
    attempt = claim(api, r, 'cpo')
    assert attempt and attempt['message']['id'] == msg['id'] and attempt['task'] is None
    with api.app.state.store.read() as c:
        still_blocked(c, task, blocker)


@pytest.mark.parametrize('wait', ['approval', 'worktree', 'put_back'])
def test_notice_turn_never_clears_an_independent_wait(api, wait):
    r = machine(api)
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, incident=True)
        if wait == 'approval':
            c.execute("INSERT INTO approvals(id,kind,task_id,requested_by_id,created_at) "
                      "VALUES('approval','send',?,'bot:cpo',?)", (task['id'], H.now()))
        elif wait == 'worktree':
            from backend.worktree_waits import ensure
            ensure(c)
            c.execute("INSERT INTO worktree_waits(id,task_id,owner,repo,generation,state,created_at,updated_at) "
                      "VALUES('worktree',?,'bot:cpo','acme/app',0,'waiting',?,?)", (task['id'], H.now(), H.now()))
        else:
            H._task_event(c, task['id'], 'bot:cpo', 'queue_return', 'doing', 'open', 'Return to queue')
            assert auto_tasks.put_back_held(c, H.task(c, task['id']))
        before = H.task(c, task['id'])
        msg, job = sent_job(c, before, 'bot:cpo', 'The incident needs attention', {'news': 'step_entered'})
        assert job and job['held'] == 0 and msg['refs'][R.PARKED_NOTICE]
    attempt = claim(api, r, 'cpo')
    if wait == 'put_back':
        assert attempt is None
    else:
        assert attempt and attempt['task'] is None
        post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'notice-session'}, token=r['token'])
        post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'completed', 'last_seq': 0,
                                                      'text': 'Read the notice.'}, token=r['token'])
    with api.app.state.store.read() as c:
        still_blocked(c, task, blocker)
        after = H.task(c, task['id'])
        assert all(after[k] == before[k] for k in ('status', 'step_id', 'waiting_on', 'snoozed_until', 'next_run'))
        if wait == 'approval':
            assert c.execute("SELECT decision FROM approvals WHERE id='approval'").fetchone()[0] is None
        elif wait == 'worktree':
            assert c.execute("SELECT state FROM worktree_waits WHERE id='worktree'").fetchone()[0] == 'waiting'
        else:
            assert auto_tasks.put_back_held(c, after)


def test_private_notice_retains_output_fences_without_a_task_attachment(api):
    from backend.tests.test_api import unrelated_task_managers
    r = machine(api)
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, incident=True)
        unrelated_task_managers(c)
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (task['id'],))
        msg, job = sent_job(c, H.task(c, task['id']), 'bot:cpo', 'Private incident news',
                            {'news': 'step_entered'})
        assert job and msg['refs'][R.PARKED_NOTICE]
    attempt = claim(api, r, 'cpo')
    assert attempt and attempt['task'] is None
    with api.app.state.store.read() as c:
        assert task['id'] in task_privacy.attempt_tasks(c, attempt['id'])
    post(api, 'updates', {'body': 'Private derived output'}, attempt['token'], expected=403)
    get(api, 'messages/' + msg['id'], 'cara-test', expected=404)
    with api.app.state.store.read() as c:
        still_blocked(c, task, blocker)


@pytest.mark.parametrize('lanes', [False, True])
def test_person_message_during_notice_turn_gets_its_own_reply(api, lanes):
    r = task_bot(api, lanes=lanes, others=('cpo',))
    room = post(api, 'chat/cpo', {'text': 'Open the bot room'})['conversation_id']
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, incident=True)
        msg = H._write_message(c, H.KEEPER, 'bot:cpo', 'Incident needs attention', H.conversation(c, room),
                               'notice', {'task': task['id'], 'news': 'step_entered'}, None, None)
        assert msg['refs'][R.PARKED_NOTICE]
    notice = claim(api, r, 'cpo')
    assert notice and notice['task'] is None
    post(api, f"attempts/{notice['id']}/started", {'thread_id': 'notice'}, r['token'])
    person = post(api, 'chat/cpo', {'text': 'Please answer my separate question'})
    incoming = post(api, f"attempts/{notice['id']}/inputs", {}, r['token'])
    assert person['id'] not in [m['id'] for m in incoming['messages']]
    post(api, f"attempts/{notice['id']}/complete", {'outcome': 'completed', 'last_seq': 0,
                                                 'text': 'Read the incident.'}, r['token'])
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE message_id=?', (person['id'],)).fetchone()[0] == 'queued'
        assert not c.execute('SELECT 1 FROM attempt_inputs WHERE message_id=?', (person['id'],)).fetchone()
        still_blocked(c, task, blocker)
    answer = claim(api, r, 'cpo')
    assert answer and answer['message']['id'] == person['id']
    post(api, f"attempts/{answer['id']}/started", {'thread_id': 'person'}, r['token'])
    post(api, f"attempts/{answer['id']}/complete", {'outcome': 'completed', 'last_seq': 0,
                                                 'text': 'Here is your answer.'}, r['token'])
    with api.app.state.store.read() as c:
        reply = c.execute("SELECT body FROM messages WHERE in_reply_to=? AND sender_id='bot:cpo'",
                          (person['id'],)).fetchone()
        assert reply and reply[0] == 'Here is your answer.'


def test_lost_notice_is_dismissed_when_its_referenced_task_closes(api):
    from backend.execution import STALL_MAX
    r = machine(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked(c, incident=True)
        msg, _ = sent_job(c, task, 'bot:cpo', 'Incident needs attention', {'news': 'step_entered'})
    notice = claim(api, r, 'cpo')
    assert notice and notice['task'] is None
    post(api, f"attempts/{notice['id']}/started", {'thread_id': 'lost-notice'}, r['token'])
    post(api, f"attempts/{notice['id']}/complete", {'outcome': 'interrupted', 'last_seq': 0}, r['token'])
    post(api, 'tasks/' + task['id'], {'version': get(api, 'tasks/' + task['id'])['task']['version'],
                                    'close': True, 'quiet': True})
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempts SET finished_at=? WHERE id=?',
                  (H.shift(H.now(), seconds=-STALL_MAX - 1), notice['id']))
        assert c.execute('SELECT state FROM jobs WHERE id=?', (notice['job_id'],)).fetchone()[0] == 'uncertain'
        result = api.app.state.execution.auto_reconcile(c)
        assert [d['state'] for d in result] == ['canceled']
        assert H.task(c, task['id'])['status'] == 'closed'
        assert c.execute('SELECT count(*) FROM attempts WHERE job_id=?', (notice['job_id'],)).fetchone()[0] == 1


@pytest.mark.parametrize('refs,party,urgent', [
    ({'wake': 'review_request'}, 'unrelated', 'direct_review_request'),
    ({'stale_check': 'notice-action'}, 'owned', 'task_action'),
])
def test_existing_review_and_action_urgency_gets_a_notice_turn(api, refs, party, urgent):
    r = machine(api)
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, party=party)
        assert R.urgent_rule(c, H.KEEPER, 'bot:cpo', {'task': task['id'], **refs}, None,
                             R.event_of(H.KEEPER, 'notice', refs, 'Action needed')) == urgent
        msg, job = sent_job(c, task, 'bot:cpo', 'Action needed', refs)
        assert job and job['held'] == 0 and msg['refs'][R.PARKED_NOTICE]
        deciding_rule = msg['refs']['rule']
    notice = claim(api, r, 'cpo')
    assert notice and notice['task'] is None and notice['message']['refs']['rule'] == deciding_rule
    post(api, f"attempts/{notice['id']}/started", {'thread_id': 'review-notice'}, r['token'])
    post(api, f"attempts/{notice['id']}/complete", {'outcome': 'completed', 'last_seq': 0,
                                                 'text': 'Read the request.'}, r['token'])
    with api.app.state.store.read() as c:
        still_blocked(c, task, blocker)
        assert H.task(c, task['id'])['status'] == task['status']
        assert H.message(c, msg['id'])['delivered_at']


def test_untrusted_notice_marker_is_removed_for_a_human_recipient(api):
    with api.app.state.store.transaction() as c:
        task, _ = parked(c)
        msg = H._write_message(c, H.KEEPER, 'human:ana', 'Synthetic news',
                               H.conversation(c, task['conversation_id']), 'notice',
                               {'task': task['id'], R.PARKED_NOTICE: True}, None, None)
        assert R.PARKED_NOTICE not in msg['refs']
