"""Run-budget recovery uses the restart path and keeps irreversible effects and repeated cuts held."""
import pytest

from backend.store import H
from backend.tests.test_api import api, claim, get, post
from backend.tests.test_task_lanes import task_bot, new_task
from backend.tests.test_execution_review import add_recovery_reviewer, job_state, sweep


def running(api):
    machine = task_bot(api, lanes=True)
    add_recovery_reviewer(api)
    task = new_task(api, 'Finish the saved branch')
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'saved-provider-thread'}, machine['token'])
    return machine, task, attempt


def cut(api, machine, attempt, *, minutes='60', command='git push origin HEAD', approval=False,
        end_reason=None, tool_payload=None, silent=False, **flags):
    if approval:
        with api.app.state.store.transaction() as c:
            message = c.execute('SELECT message_id FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0]
            c.execute('INSERT INTO approvals(id,kind,message_id,requested_by_id,created_at,consumed_at) VALUES(?,?,?,?,?,?)',
                      ('used-approval', 'publish', message, 'bot:ops', H.now(), H.now()))
    events = [
        ({'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': False}} if silent else
         {'seq': 1, 'kind': 'tool', 'payload': tool_payload or {'tool': 'exec_command', 'status': 'started',
                                                            'item_id': 'shell', 'summary': command}}),
        {'seq': 2, 'kind': 'message', 'payload': {'text': 'Branch saved and pushed; review is next.'}},
        {'seq': 3, 'kind': 'diagnostic', 'payload': {
            'end_reason': end_reason or f'Timeout: max_run_minutes ({minutes} minutes)'}},
    ]
    post(api, f"attempts/{attempt['id']}/events", {'events': events}, machine['token'])
    return post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': 3,
                'text': 'Branch saved and pushed; review is next.', **flags}, machine['token'])


def next_attempt(api, machine, first):
    resumed = claim(api, machine)
    assert resumed['job_id'] == first['job_id']
    post(api, f"attempts/{resumed['id']}/started", {'thread_id': 'saved-provider-thread'}, machine['token'])
    return resumed


def test_first_timeout_waiting_for_live_session_has_timeout_card_then_resumes(api):
    """A session fence delays recovery without changing the cause to a computer restart."""
    machine, task, first = running(api)
    new_task(api, 'Finish other session work')
    other = claim(api, machine)
    post(api, f"attempts/{other['id']}/started", {'thread_id': 'other-session-work'}, machine['token'])
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE turns SET task_id=? WHERE id=?', (task['id'], other['id']))
    cut(api, machine, first)
    assert job_state(api, first['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert review['stopped'] == 'timeout'
    with api.app.state.store.read() as c:
        assert not c.execute('SELECT 1 FROM job_recovery WHERE job_id=?', (first['job_id'],)).fetchone()
    assert sweep(api) == []
    post(api, f"attempts/{other['id']}/complete", {'outcome': 'completed', 'last_seq': 0,
                                                  'text': 'Session work finished.'}, machine['token'])
    assert [d['state'] for d in sweep(api)] == ['queued']
    assert job_state(api, first['job_id']) == 'queued'


def test_pushed_branch_timeout_requeues_same_job_lane_and_carries_task_handoff(api):
    """A pushed PR head and a local shell edit need no operator recovery, as in the interrupted builder case."""
    machine, task, first = running(api)
    handoff = 'Committed and pushed the PR head. Next: review the saved change.'
    post(api, f"tasks/{task['id']}", {'note': handoff, 'version': get(api, f"tasks/{task['id']}", first['token'])['task']['version']}, first['token'])
    result = cut(api, machine, first)
    assert result['outcome'] == 'interrupted'  # the timeout's own outcome is unchanged
    assert job_state(api, first['job_id']) == 'queued'
    with api.app.state.store.read() as c:
        assert c.execute('SELECT final_text FROM attempts WHERE id=?', (first['id'],)).fetchone()[0].startswith('Branch saved')
        assert c.execute('SELECT note FROM tasks WHERE id=?', (task['id'],)).fetchone()[0] == handoff
        assert c.execute('SELECT end_reason FROM attempt_diagnostics WHERE attempt_id=?', (first['id'],)).fetchone()[0] == 'Timeout: max_run_minutes (60 minutes)'
        lines = [r[0] for r in c.execute('SELECT body FROM messages WHERE conversation_id=?', (task['conversation_id'],))]
        assert lines.count('Reached its 60-minute run limit; continuing') == 1
        assert not c.execute("SELECT 1 FROM tasks WHERE owner_id='bot:botops'").fetchone()
        before = dict(c.execute('SELECT * FROM job_recovery WHERE job_id=?', (first['job_id'],)).fetchone())
    assert sweep(api) == []
    resumed = claim(api, machine)
    assert resumed['job_id'] == first['job_id'] and resumed['lane'] == first['lane'] == 'task:' + task['id']
    assert resumed['task'].get('session_task_id', resumed['task']['id']) == first['task'].get('session_task_id', first['task']['id']) == task['id']
    assert resumed['recovery'] == {'resume_session': True}
    assert handoff in resumed['message']['body'] and 'Check what already happened first' in resumed['message']['body']
    with api.app.state.store.read() as c:
        assert dict(c.execute('SELECT * FROM job_recovery WHERE job_id=?', (first['job_id'],)).fetchone()) == before
        assert c.execute('SELECT thread_id FROM bot_sessions WHERE bot=?', ('ops',)).fetchone()[0] == 'saved-provider-thread'


@pytest.mark.parametrize(('command', 'label', 'approval'), [
    ('gh pr merge 42 --squash --body private-marker', 'merge pull request', False),
    ('npm publish --token private-marker', 'publish package', False),
    ('hub email send recipient private-marker', 'send email', False),
    ('stripe payment_intents create --amount 100', 'payment', False),
    ('git push origin HEAD', 'consumed approval (publish)', True),
])
def test_timeout_holds_named_irreversible_action_or_consumed_approval(api, command, label, approval):
    machine, _, first = running(api)
    cut(api, machine, first, command=command, approval=approval)
    assert job_state(api, first['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert label in review['reason'] and review['stopped'] == 'changed'
    assert 'private-marker' not in review['reason']
    with api.app.state.store.read() as c:
        tasks = c.execute("SELECT body FROM tasks WHERE owner_id='bot:botops' AND status='open'").fetchall()
        assert len(tasks) == 1 and label in tasks[0]['body'] and 'private-marker' not in tasks[0]['body']
    assert sweep(api) == [] and job_state(api, first['job_id']) == 'uncertain'


def test_second_consecutive_cut_stays_held_with_open_review_and_no_saved_decision(api):
    machine, _, first = running(api)
    cut(api, machine, first)
    second = next_attempt(api, machine, first)
    cut(api, machine, second)
    assert job_state(api, first['job_id']) == 'uncertain'
    assert claim(api, machine) is None
    why = 'cut by the run time limit twice in a row'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert why in review['reason'] and review['stopped'] == 'twice'
    with api.app.state.store.read() as c:
        body = c.execute("SELECT body FROM tasks WHERE owner_id='bot:botops' AND status='open'").fetchone()[0]
        assert why in body and 'recorded exec_command' not in body
        assert not c.execute('SELECT 1 FROM job_recovery WHERE job_id=? AND attempt_id=?',
                             (first['job_id'], second['id'])).fetchone()
    assert sweep(api) == [] and job_state(api, first['job_id']) == 'uncertain'


def test_completed_attempt_breaks_the_consecutive_cut_streak(api):
    machine, _, first = running(api)
    cut(api, machine, first)
    second = next_attempt(api, machine, first)
    post(api, f"attempts/{second['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Done.'}, machine['token'])
    # A later delivery of the same job has a completed attempt between its two time-limit cuts.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='queued' WHERE id=?", (first['job_id'],))
    third = next_attempt(api, machine, first)
    cut(api, machine, third)
    assert job_state(api, first['job_id']) == 'queued'
    assert get(api, 'bots/ops/execution-review')['jobs'] == []


def test_fractional_limit_uses_reported_budget_without_touching_task_note(api):
    machine, task, first = running(api)
    cut(api, machine, first, minutes='6.5')
    resumed = claim(api, machine)
    assert resumed['job_id'] == first['job_id']
    assert 'Reached its 6.5-minute run limit; continuing' in resumed['message']['body']
    with api.app.state.store.read() as c:
        assert not c.execute('SELECT note FROM tasks WHERE id=?', (task['id'],)).fetchone()[0]


@pytest.mark.parametrize('reason', [
    'Timeout: Gateway credential lifetime (24 hours)',
    'Computer lost or heartbeat stopped (attempt lease expired)',
])
def test_other_interruptions_do_not_get_run_budget_recovery(api, reason):
    machine, _, first = running(api)
    cut(api, machine, first, end_reason=reason)
    assert job_state(api, first['job_id']) == 'uncertain'
    assert claim(api, machine) is None


def test_person_stop_overrides_a_time_limit_diagnostic(api):
    machine, _, first = running(api)
    post(api, 'bots/ops/stop', {'run_id': first['id']})
    cut(api, machine, first)
    assert job_state(api, first['job_id']) == 'canceled'


def test_unknown_fallback_effects_stay_held_even_with_a_time_limit(api):
    machine, _, first = running(api)
    cut(api, machine, first, primary_held=True, fallback='gemini')
    assert job_state(api, first['job_id']) == 'uncertain'
    assert get(api, 'bots/ops/execution-review')['jobs'][0]['stopped'] == 'unknown'


@pytest.mark.parametrize('flags', [{'yielded': True}, {'limited': True}])
def test_retry_flags_cannot_bypass_irreversible_action_check(api, flags):
    machine, _, first = running(api)
    cut(api, machine, first, command='gh pr merge 42', **flags)
    assert job_state(api, first['job_id']) == 'uncertain'
    assert 'merge pull request' in get(api, 'bots/ops/execution-review')['jobs'][0]['reason']


def test_silent_harness_timeout_continues_once_under_explicit_restart_policy(api):
    machine, _, first = running(api)
    cut(api, machine, first, silent=True)
    assert job_state(api, first['job_id']) == 'queued'
    resumed = next_attempt(api, machine, first)
    assert resumed['recovery'] == {'resume_session': True}
    assert 'Check what already happened first' in resumed['message']['body']
    cut(api, machine, resumed, silent=True)
    assert job_state(api, first['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert review['stopped'] == 'twice'


@pytest.mark.parametrize(('payload', 'label'), [
    ({'tool': 'mcp__acme__merge_pull_request', 'status': 'started', 'summary': 'private-marker'},
     'merge pull request'),
    ({'tool': 'exec_command', 'status': 'started', 'summary': 'private-marker',
      'outside_actions': ['publish package']}, 'publish package'),
])
def test_second_cut_names_outside_effects_from_tool_or_classified_event(api, payload, label):
    machine, _, first = running(api)
    cut(api, machine, first)
    second = next_attempt(api, machine, first)
    cut(api, machine, second, tool_payload=payload)
    assert job_state(api, first['job_id']) == 'uncertain'
    assert claim(api, machine) is None
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert review['stopped'] == 'changed' and label in review['reason']
    with api.app.state.store.read() as c:
        body = c.execute("SELECT body FROM tasks WHERE owner_id='bot:botops' AND status='open'").fetchone()[0]
        assert 'cut by the run time limit twice in a row' in body and f'recorded {label}' in body
        assert 'private-marker' not in body + review['reason']
        assert not c.execute('SELECT 1 FROM job_recovery WHERE job_id=? AND attempt_id=?',
                             (first['job_id'], second['id'])).fetchone()
