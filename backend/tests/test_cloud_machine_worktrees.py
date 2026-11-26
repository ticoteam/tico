"""A cloud machine made again on a new disk takes its open task checkouts with it (task #3448).

Before: every checkout stayed on the old, revoked computer, Health listed each as "Left on a retired computer", and each
owner bot had to run `tico task worktree add` by hand."""

import json
import uuid

from backend import cloud_machines as CM, maintenance_health, worktrees as W
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import post, runner
from backend.tests.helpers.cloud_machines import cloud, idle, shared_enrolled, work  # noqa: F401
from backend.tests.helpers.task_lanes import new_task
from backend.tests.helpers.worktrees import end

READY = {'schema_version': 1, 'bots': {}, 'worktrees': True, 'worktree_waits': True, 'worktree_recovery': True}
PUSHED, HEAD = 'a' * 40, 'c' * 40


def checkout(store, task, bot, computer):
    """A ready checkout of `task` by `bot` on `computer` whose last report had 2 commits not on GitHub."""
    link = uuid.uuid4().hex
    detail = {
        'owner': 'bot:' + bot,
        'checkout_state': 'ready',
        'setup_pending': False,
        'expected_head': PUSHED,
        'checkout_target': 'refs/heads/tico/x',
        'expected_base': 'b' * 64,
        'last_commit': HEAD,
        'pushed': PUSHED,
        'ahead': 2,
        'dirty_files': 0,
    }
    with store.transaction() as c:
        c.execute(
            "INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,branch,computer_id,path,"
            "updated_at,detail) VALUES(?,?,'worktree',?,'Acme/product','present',?,?,'Acme/product',?,?,?,?,?)",
            (
                link,
                task,
                'worktree:' + link,
                'bot:' + bot,
                H.now(),
                f'tico/{task[:8]}-x',
                computer,
                f'tasks/{task[:8]}/acme__product',
                H.now(),
                json.dumps(detail),
            ),
        )
    return link


def make_again(cloud, key=CM.SHARED):
    """HQ lost the machine: its next start is refused, Tico makes it again, and the new computer enrolls."""
    idle(cloud)
    cloud.tick(400)  # idle: it stops
    work(cloud, 'ops', text='More')
    machine = cloud.row(key)['machine_id']
    cloud.hq.fail['start'] = [CM.Refused('not_found', 404)]
    cloud.hq.machines[machine]['state'] = 'deleted'
    cloud.hq.calls.clear()
    cloud.tick(10)
    assert cloud.tick(10)[-1] == 'create'
    code = cloud.hq.calls[-1][3]
    return post(cloud.api, 'runners/enroll', {'code': code, 'label': 'boat-vm', 'platform': 'linux'})['runner_id']


def link(store, link_id):
    with store.read() as c:
        row = dict(c.execute('SELECT * FROM task_links WHERE id=?', (link_id,)).fetchone())
    return row, json.loads(row['detail'])


def retired_findings(store):
    with store.read() as c:
        return [
            f
            for f in maintenance_health.findings(c, store.settings)
            if f['check_id'] == 'worktree_error' and W.RETIRED in f['detail']
        ]


def test_a_shared_machine_made_again_moves_each_open_checkout_to_its_new_computer(cloud):
    old = shared_enrolled(cloud, 'ops', 'finance')
    ops_task, finance_task = new_task(cloud.api, 'Ops work')['id'], new_task(cloud.api, 'Finance work', 'finance')['id']
    ops_link = checkout(cloud.store, ops_task, 'ops', old)
    finance_link = checkout(cloud.store, finance_task, 'finance', old)

    new = make_again(cloud)

    assert new != old
    for link_id in (ops_link, finance_link):
        row, detail = link(cloud.store, link_id)
        assert row['computer_id'] == new and row['state'] == 'pending'
        assert detail['checkout_state'] == 'queued' and detail['setup_pending'] is True
        # Restored from what was published, newest first (the runner also reads the checkout's wip/ copy).
        assert detail['recovery']['branch'] == row['branch'] and detail['recovery']['published_head'] == PUSHED
        # What the old computer last said stays as history.
        assert detail['retired_worktree']['computer_id'] == old
        assert detail['retired_worktree']['last_commit'] == HEAD and detail['retired_worktree']['ahead'] == 2
        assert not any(detail.get(k) for k in ('last_commit', 'pushed', 'ahead', *W.IDENTITY))
    assert retired_findings(cloud.store) == []
    # The new computer is asked to rebuild them as soon as it says it can, without either bot asking.
    with cloud.store.transaction() as c:
        c.execute('UPDATE runner_states SET readiness=? WHERE runner_id=?', (json.dumps(READY), new))
        actions = W.heartbeat(c, Identity('runner:' + new, 'runner', runner_id=new), [], True)
    assert sorted((a['link_id'], a['action']) for a in actions) == sorted(
        [(ops_link, 'prepare'), (finance_link, 'prepare')]
    )


def test_a_finished_tasks_checkout_is_closed_with_its_history_not_restored(cloud):
    old = shared_enrolled(cloud, 'ops')
    done, closed = new_task(cloud.api, 'Done work')['id'], new_task(cloud.api, 'Closed work')['id']
    links = {checkout(cloud.store, done, 'ops', old): 'done', checkout(cloud.store, closed, 'ops', old): 'closed'}
    with cloud.store.transaction() as c:
        end(c, done, 'done')
        end(c, closed, 'closed')

    make_again(cloud)

    for link_id in links:
        row, detail = link(cloud.store, link_id)
        assert row['state'] == 'removed' and row['computer_id'] == old
        assert 'recovery' not in detail
        assert detail['retired_worktree']['last_commit'] == HEAD and detail['retired_worktree']['ahead'] == 2
        assert detail['release_reason'] == 'task finished'
    assert retired_findings(cloud.store) == []


def test_a_checkout_on_another_computer_stays_where_it_is(cloud):
    """Only the replaced computer's checkouts move: one a bot keeps on a person's computer is not touched."""
    old = shared_enrolled(cloud, 'ops')
    elsewhere = runner(cloud.api, label='Office Mac')['runner_id']
    task = new_task(cloud.api, 'Ops work')['id']
    kept = checkout(cloud.store, task, 'ops', elsewhere)
    moved = checkout(cloud.store, new_task(cloud.api, 'More ops work')['id'], 'ops', old)

    new = make_again(cloud)

    assert link(cloud.store, kept)[0]['computer_id'] == elsewhere and link(cloud.store, kept)[0]['state'] == 'present'
    assert link(cloud.store, moved)[0]['computer_id'] == new


def test_a_computer_a_person_replaces_keeps_todays_behaviour(cloud):
    """A bot's own computer (not a cloud machine) that a person removes and enrolls again: its checkouts stay on the
    old registration for the bot to bring back, as before."""
    first = runner(cloud.api, label='Office Mac')['runner_id']
    task = new_task(cloud.api, 'Ops work')['id']
    link_id = checkout(cloud.store, task, 'ops', first)
    with cloud.store.transaction() as c:
        c.execute('UPDATE runners SET revoked_at=? WHERE id=?', (H.now(), first))
    runner(cloud.api, label='Office Mac')
    row, detail = link(cloud.store, link_id)
    assert row['computer_id'] == first and row['state'] == 'present' and 'recovery' not in detail
    assert len(retired_findings(cloud.store)) == 1
