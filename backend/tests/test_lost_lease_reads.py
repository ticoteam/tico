"""A computer that drops mid-reply: a turn whose shell calls only read and that wrote nothing goes back in line, and a
turn that may have acted is held with one plain notice to the person who asked (a full disk dropped a shared computer;
the Assistant's turn, which had only run `cd repo && hub needs-you show`, sat held for hours and its person heard
nothing)."""
import json

import pytest

from backend.store import H
from backend.tests.test_api import api, claim, expire, post, ready, setup_attempt  # noqa: F401 (api: the fixture)
from backend.tests.test_execution_review import aged, job_state, sweep

CAPS = ['capacity_continue_v1']


def lost_mid_reply(api, shells, *, capacity=True, writes=0):
    """Ana's chat message to ops, whose turn ran `shells` ((summary, outside_actions) pairs, outside_actions None for
    an older runner that sent none), then its computer dropped and its lease lapsed."""
    machine, message, attempt = setup_attempt(api)
    if capacity:
        ready(api, machine, ['ops'], CAPS)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'same-session'}, machine['token'])
    events = [{'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}}]
    for n, (summary, labels) in enumerate(shells):
        events.append({'seq': len(events) + 1, 'kind': 'tool', 'payload': {
            'tool': 'Bash', 'status': 'started', 'item_id': f'sh-{n}', 'summary': summary,
            **({'outside_actions': labels} if labels is not None else {})}})
        events.append({'seq': len(events) + 1, 'kind': 'tool', 'payload': {
            'tool': 'Bash', 'status': 'completed', 'item_id': f'sh-{n}'}})
    post(api, f"attempts/{attempt['id']}/events", {'events': events}, machine['token'])
    store, execution = api.app.state.store, api.app.state.execution
    if writes:
        with store.transaction() as c:
            c.execute("UPDATE attempts SET writes=? WHERE id=?", (writes, attempt['id']))
    expire(api, attempt['id'])
    execution.served_at = None          # no server-stall grace: the computer alone lost contact
    with store.transaction() as c:
        execution.expire(c)
    assert job_state(api, attempt['job_id']) == 'uncertain'
    return machine, message, attempt


def notices(api, attempt):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(
            "SELECT sender_id,body,in_reply_to FROM messages WHERE kind='notice' "
            "AND json_extract(refs,'$.turn_id')=?", (attempt['id'],))]


READS = [('cd', []), ('hub needs-you show', []), ('hub task list', []), ('hub', []), ('grep', [])]


@pytest.mark.parametrize('capacity', [True, False])
def test_a_person_turn_that_only_read_goes_back_in_line_after_its_lease_is_lost(api, capacity):
    machine, message, attempt = lost_mid_reply(api, READS, capacity=capacity)
    with api.app.state.store.read() as c:
        assert H.status(c, 'ops')['state'] == 'idle' and 'Resuming' in H.status(c, 'ops')['focus']
    if not capacity:
        # Without a session it can continue, its lapsed runner's late result may still come in first.
        assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
        aged(api, attempt['id'], grace=True)
    assert [d['state'] for d in sweep(api)] == ['queued']
    ready(api, machine, ['ops'], CAPS)
    again = claim(api, machine)
    assert again['job_id'] == attempt['job_id']
    assert 'check what already happened' in again['message']['body'].lower()
    assert notices(api, attempt) == []          # put back in line: nothing to tell the person


@pytest.mark.parametrize('shells,writes', [
    (READS, 1),                                     # a hub write: it acted
    ([('cd', ['send email'])], 0),                  # a named outside action anywhere in the line
    ([('git', [])], 0),                             # a program that can push
    ([('cd', None)], 0),                            # an older runner that never scanned the line
], ids=['wrote', 'outside-action', 'git', 'unscanned'])
def test_a_person_turn_that_may_have_acted_is_held_and_the_person_is_told_once(api, shells, writes):
    machine, message, attempt = lost_mid_reply(api, shells, writes=writes)
    with api.app.state.store.read() as c:
        assert H.status(c, 'ops')['state'] == 'crashed'
    aged(api, attempt['id'])
    assert sweep(api) == [] and sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    [notice] = notices(api, attempt)
    assert notice['sender_id'] == 'bot:ops' and notice['in_reply_to'] is None     # never read as the answer
    assert 'computer dropped mid-reply' in notice['body'] and 'UTC' in notice['body']
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM events WHERE action='job.held' AND target=?", (attempt['id'],)).fetchone()
        detail = json.loads(c.execute("SELECT detail FROM events WHERE action='attempt.expired' AND target=?",
                                      (attempt['id'],)).fetchone()[0])
    assert detail['job_state'] == 'uncertain'


def fixture_case(command, host):
    """A shell call's start event exactly as the runner sends it (runner-rs tests produce the same fixture)."""
    from backend.tests.test_execution_review import runner_shell_cases
    return {case['command']: case for case in runner_shell_cases()}[command][host]


@pytest.mark.parametrize('command,host,requeued', [
    ('hub task show 42', 'codex', True),
    ('cat note.txt', 'claude', True),
    ('/bin/bash -lc "cd /tmp;gh pr merge 42"', 'claude', False),    # summarized `cd`, but the line merges
    ('hub webhook send 1', 'claude', False),
    ('gh pr view 42', 'claude', False),                              # `gh` alone cannot be told from a merge
])
def test_real_runner_shell_events_decide_a_lost_lease_turn(api, command, host, requeued):
    payload = fixture_case(command, host)
    machine, message, attempt = setup_attempt(api)
    ready(api, machine, ['ops'], CAPS)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'same-session'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}},
        {'seq': 2, 'kind': 'tool', 'payload': payload}]}, machine['token'])
    expire(api, attempt['id'])
    api.app.state.execution.served_at = None
    with api.app.state.store.transaction() as c:
        api.app.state.execution.expire(c)
    aged(api, attempt['id'])
    assert [d['state'] for d in sweep(api)] == (['queued'] if requeued else [])
    assert len(notices(api, attempt)) == (0 if requeued else 1)
