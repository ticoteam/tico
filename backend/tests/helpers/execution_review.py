"""Helpers that other tests share, moved out of test_execution_review.py so they import no test file."""

import json
from backend.tests.helpers.api import assign, runner, setup_attempt, post, get, expire, ready, claim


def interrupted(api):
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'review-test'}, machine['token'])
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {'events': [{'seq': 1, 'kind': 'message', 'payload': {'text': 'Saved report; email already sent.'}}]},
        machine['token'],
    )
    expire(api, attempt['id'])
    assert claim(api, machine) is None
    return machine, message, attempt


def aged(api, attempt_id, grace=False):
    """Push the interrupted attempt past STALL_MAX, so the runner's own restore no longer applies; with `grace`, also
    past RESULT_GRACE, after which the sweep stops waiting for a lapsed run's own result and may resume it."""
    from backend.execution import RESULT_GRACE, STALL_MAX
    from backend.store import H

    with api.app.state.store.transaction() as c:
        c.execute(
            'UPDATE attempts SET finished_at=? WHERE id=?',
            (H.shift(H.now(), seconds=-(RESULT_GRACE if grace else STALL_MAX) - 1), attempt_id),
        )


def sweep(api):
    with api.app.state.store.transaction() as c:
        return api.app.state.execution.auto_reconcile(c)


def job_state(api, job_id):
    with api.app.state.store.read() as c:
        return c.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()[0]


def restarted_mid_turn(api, calls, *, reported=True, hop=False, restart=True, stopped=False, reply=None):
    """A Claude-style run that says it reports its tool calls, made `calls` ((name, parent) pairs) and was cut off:
    by a runner restart that reported it interrupted, or by a lapsed lease. `hop`: it then fell back to a host that
    does not report its calls (`tool_events: false`, runner-rs execute.rs). `stopped`: the computer's clean stop
    (its saved "Runner stopped during execution" diagnostic, with an empty reply) instead of a restart."""
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'restarted'}, machine['token'])
    events = [{'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}}]
    for n, (name, parent) in enumerate(calls):
        for status in ('started', 'completed'):
            events.append(
                {
                    'seq': len(events) + 1,
                    'kind': 'tool',
                    'payload': {
                        'tool': name,
                        'status': status,
                        'item_id': f'call-{n}',
                        'parent_id': parent,
                        **({'summary': f'notes/{n}.md'} if status == 'started' else {}),
                    },
                }
            )
    if hop:
        events.append({'seq': len(events) + 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': False}})
    events.append({'seq': len(events) + 1, 'kind': 'message', 'payload': {'text': 'Reading the notes.'}})
    if stopped:
        # As the runner saves a clean stop (runner-rs drive.rs): a diagnostic naming it, and no reply in its completion.
        events.append(
            {'seq': len(events) + 1, 'kind': 'diagnostic', 'payload': {'end_reason': 'Runner stopped during execution'}}
        )
    post(api, f"attempts/{attempt['id']}/events", {'events': events}, machine['token'])
    if reported:
        text = (
            reply
            if reply is not None
            else ''
            if stopped
            else 'Runner restarted during execution'
            if restart
            else 'Execution interrupted'
        )
        post(
            api,
            f"attempts/{attempt['id']}/complete",
            {'outcome': 'interrupted', 'last_seq': len(events), 'text': text},
            machine['token'],
        )
    else:
        expire(api, attempt['id'])
        assert claim(api, machine) is None
    assert job_state(api, attempt['job_id']) == 'uncertain'
    return machine, message, attempt


def finished_then_lapsed(
    api, machine=None, bot='ops', text='Please summarize the current work.', task=False, stopped=False
):
    """A run that read, wrote its answer and went idle; then its lease lapsed before the result reached the hub.
    With `task`, a person filed the request as a task in its own room, and the run starts from the hub's notice."""
    from backend.tests.helpers.api import runner

    if machine is None:
        machine = runner(api, 'ana')
        assign(api, machine, bot)
    ready(api, machine, [bot])
    if task:
        from backend.store import H

        with api.app.state.store.transaction() as c:  # filed outside any chat, as a bot files a ticket
            filed = H.task_create(c, 'human:ana', 'Summarize the work', text, 'bot:' + bot)
            assert not H.task_from_chat(c, filed['id'])
        with api.app.state.store.read() as c:
            message = dict(
                c.execute(
                    "SELECT m.id,m.conversation_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                    "WHERE j.state='queued' AND j.bot_id=?",
                    (bot,),
                ).fetchone()
            )
        message['task'] = filed
    else:
        message = post(api, 'chat/' + bot, {'text': text})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'stalled-' + attempt['id']}, machine['token'])
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {
            'events': [
                {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}},
                {'seq': 2, 'kind': 'message', 'payload': {'text': 'Done: the summary is in the room.', 'final': True}},
                {'seq': 3, 'kind': 'status', 'payload': {'state': 'idle'}},
            ]
        },
        machine['token'],
    )
    if stopped:
        post(api, f'bots/{bot}/stop', {'run_id': attempt['id']})  # never carried by a renewal
    expire(api, attempt['id'])
    assert claim(api, machine) is None
    assert job_state(api, attempt['job_id']) == ('canceled' if stopped else 'uncertain')
    return machine, message, attempt


def settled(api, attempt):
    with api.app.state.store.read() as c:
        return (
            c.execute("SELECT state FROM attempts WHERE id=?", (attempt['id'],)).fetchone()[0],
            c.execute("SELECT state FROM jobs WHERE id=?", (attempt['job_id'],)).fetchone()[0],
        )


def rival(api, machine, attempt, bot='ops'):
    """Another run of the bot live in the lapsed turn's task-session lane; an unrelated run in the main lane no
    longer holds a stopped run back, so the rival shares the session."""
    from backend.store import H

    with api.app.state.store.transaction() as c:
        H.task_create(c, 'human:ana', 'Something else', 'Do something else.', 'bot:' + bot)
    ready(api, machine, [bot])
    other = claim(api, machine)
    assert other and other['id'] != attempt['id']
    post(api, f"attempts/{other['id']}/started", {'thread_id': 'rival'}, machine['token'])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lane='session:rival' WHERE id IN (?,?)", (attempt['id'], other['id']))
    return other


def stopped_issue(api, token='ana-test'):
    return next(i for i in get(api, 'operations', token=token)['issues'] if i['kind'] == 'uncertain_work')


def busy_folded_run(api):
    """A run interrupted with a comment folded into it, a second comment queued behind it and the bot's lane busy:
    one Needs you line settling two requests, where a choice is saved for the lane's next turn (deferred)."""
    machine = runner(api, 'ana')
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    task = post(api, 'tasks', {'owner': 'ops', 'title': 'Ship the receipts fix', 'body': 'Push it and open the PR.'})
    task_id = task.get('task', task).get('id') or task['refs']['task']
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'drained'}, machine['token'])
    post(api, f"tasks/{task_id}/comments", {'text': 'Also rebase on main.'})
    carried = post(api, f"attempts/{attempt['id']}/inputs", {}, machine['token'])['messages']
    post(api, f"attempts/{attempt['id']}/inputs/{carried[0]['id']}/ack", {}, machine['token'])
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {'events': [{'seq': 1, 'kind': 'tool', 'payload': {'tool': 'Bash', 'status': 'started', 'item_id': 'b1'}}]},
        machine['token'],
    )
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {'outcome': 'interrupted', 'last_seq': 1, 'text': 'Runner restarted during execution'},
        machine['token'],
    )
    post(api, f"tasks/{task_id}/comments", {'text': 'And tell me when it is up.'})  # waits behind the held run
    rival(api, machine, attempt)
    issue = stopped_issue(api)
    print("ISSUE", issue)
    [line] = issue["runs"]
    assert len(line['jobs']) == 2 and line['decided'] is None and all('decided' not in j for j in line['jobs'])
    return line['jobs']


def stop_partway(api, machine, attempt, thread):
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': thread}, machine['token'])
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {'events': [{'seq': 1, 'kind': 'tool', 'payload': {'tool': 'Bash', 'status': 'started', 'item_id': 'b1'}}]},
        machine['token'],
    )
    expire(api, attempt['id'])
    assert claim(api, machine) is None


def add_recovery_reviewer(api):
    from backend.store import H

    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES(?, ?, 'BotOps', 'active')",
            (H.FLEET_MAINTAINER, H.FLEET_MAINTAINER),
        )


def runner_shell_cases():
    from pathlib import Path

    return json.loads(
        (Path(__file__).resolve().parents[3] / 'runner-rs/tests/fixtures/recovery-shell-events.json').read_text()
    )
