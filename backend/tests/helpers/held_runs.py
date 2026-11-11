"""Helpers that other tests share, moved out of test_held_runs.py so they import no test file."""

from backend.store import H
from backend.tests.helpers.api import assign, claim, post, ready, runner
from backend.tests.helpers.execution_review import aged, job_state, restarted_mid_turn, stop_partway, sweep


def botops(api):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO bots(id, slug, display_name, state) VALUES(?, ?, 'BotOps', 'active')",
            (H.FLEET_MAINTAINER, H.FLEET_MAINTAINER),
        )


def held(api):
    """One run of ops that merged a pull request from a shell and was cut off by a restart: held for BotOps, with its
    review task filed. A shell alone resumes after a restart; a named outside action is what holds it."""
    botops(api)
    machine, message, attempt = restarted_mid_turn(api, [('Read', None), ('Bash', None)])
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE attempt_events SET payload=json_set(payload,'$.summary','gh pr merge 42 --squash') "
            "WHERE attempt_id=? AND kind='tool' AND json_extract(payload,'$.tool')='Bash' "
            "AND json_extract(payload,'$.status')='started'",
            (attempt['id'],),
        )
    aged(api, attempt['id'])
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    return machine, message, attempt


def task_held(api, private=False):
    """ops's run on a task stops partway after a shell call; a verdict queues behind it on that task, and a notice on
    another task of ops queues too."""
    machine = runner(api, 'ana')
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    task = post(
        api,
        'tasks',
        {'owner': 'ops', 'title': 'Ship the export', 'body': 'Build it and open the PR.', 'private': private},
    )
    task_id = task.get('task', task).get('id') or task['refs']['task']
    attempt = claim(api, machine)
    stop_partway(api, machine, attempt, 'held')
    other = post(api, 'tasks', {'owner': 'ops', 'title': 'Weekly digest', 'body': 'Send it.'})
    other_id = other.get('task', other).get('id') or other['refs']['task']
    # Tico hands a review sub-task's verdict back to its parent task as its own notice: a sub-task's result wakes the
    # parent's owner (rules.AWAITED_EVENTS), where a plain notice from Tico is only a note.
    with api.app.state.store.transaction() as c:
        H.task_comment(
            c, H.KEEPER, task_id, 'VERDICT: changes PR #12 head ' + 'a' * 40, extra_refs={'subtask': 'review-1'}
        )
        H.task_comment(c, H.KEEPER, other_id, 'The digest numbers are in.', extra_refs={'subtask': 'digest-1'})
    return machine, attempt, task_id, other_id
