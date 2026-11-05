"""A cloud machine asleep on purpose explains its queue without hiding a wake that failed to happen."""

import time

import pytest

from backend import cloud_machines as CM, execution, health, start_places, thread_bot_status, views
from backend.store import H
from backend.tests.test_api import api, claim, get, ready  # noqa: F401
from backend.tests.test_cloud_machines import cloud, enrolled, stopped_when_idle, work  # noqa: F401
from backend.tests.test_start_places import memory
from backend.tests.test_thread_halfway import bot_status, new_thread, say


def asleep_bot(cloud):
    computer = enrolled(cloud)
    ready(cloud.api, computer, ['ops'])
    stopped_when_idle(cloud)
    with cloud.store.transaction() as c:
        c.execute('UPDATE runner_states SET last_seen_at=? WHERE runner_id=?',
                  (H.shift(H.now(), hours=-1), computer['runner_id']))
    return computer


def waiting_task(cloud):
    with cloud.store.transaction() as c:
        task = H.task_create(c, 'human:ana', 'Wait for a decision', '', 'bot:ops', private=False)
        c.execute("UPDATE tasks SET waiting_on='human:ana' WHERE id=?", (task['id'],))
        c.execute("UPDATE jobs SET created_at=? WHERE bot_id='ops' AND state='queued'",
                  (H.shift(H.now(), hours=-4),))
    return task


def test_health_names_the_task_wait_without_calling_a_sleeping_bot_stuck(cloud):
    computer = asleep_bot(cloud)
    waiting_task(cloud)
    with cloud.store.read() as c:
        assert execution.bot_claimable(c, cloud.store.settings, computer['runner_id'], 'ops')[0] == 'task_wait'
        assert CM.bot_want(c, cloud.store.settings, 'ops', computer['runner_id'], cloud.at[0])[1] is None
    body = get(cloud.api, 'health')
    queue = next(row for row in body['checks'] if row['id'] == 'queue')
    assert queue['status'] == 'info'
    assert queue['items'][0]['reason'] == 'task_wait'
    assert health.HELD['task_wait'][0] in queue['summary']
    assert 'Starting' not in queue['summary'] and not body['stuck']
    assert cloud.tick(10) == []


@pytest.mark.parametrize('spent', [False, True])
def test_claimable_work_left_asleep_past_the_cutoff_is_still_a_problem(cloud, spent):
    asleep_bot(cloud)
    work(cloud)
    with cloud.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE bot_id='ops' AND state='queued'",
                  (H.shift(H.now(), minutes=-health.QUEUE_MINUTES - 1),))
        if spent:
            job = c.execute("SELECT id FROM jobs WHERE bot_id='ops' AND state='queued'").fetchone()[0]
            CM.save(c, 'ops', wake_key='job:' + job, woke_at=cloud.at[0])
    body = get(cloud.api, 'health')
    queue = next(row for row in body['checks'] if row['id'] == 'queue')
    assert queue['status'] == 'bad' and queue['items'][0]['reason'] == 'cloud_machine'
    assert 'Not started after 15 min' in queue['summary']
    assert body['stuck'][0]['reason'] == 'cloud_machine'


def test_a_young_claimable_job_is_not_aged_by_an_old_task_wait(cloud):
    asleep_bot(cloud)
    waiting_task(cloud)
    work(cloud)
    body = get(cloud.api, 'health')
    assert next(row for row in body['checks'] if row['id'] == 'queue')['status'] == 'ok'
    assert not body['stuck']


def test_resting_computer_splits_job_waits_without_a_stale_sample_label(cloud):
    computer = asleep_bot(cloud)
    memory(cloud.api, computer, .86, at=int(time.time()) // 60 * 60 - 300)
    waiting_task(cloud)
    work(cloud)
    rid = computer['runner_id']
    for path, key in [('computers', 'computers'), ('operations', 'machines')]:
        row = next(row for row in get(cloud.api, path)[key] if row['id'] == rid)
        waits = row['turn_counts']['waits']
        assert sum(w['count'] for w in waits) == 2
        assert any('person' in w['reason'] and w['count'] == 1 for w in waits)
        assert any(w['reason'] == CM.ASLEEP and w['count'] == 1 for w in waits)
        assert start_places.NOT_REPORTING not in row['turn_summary']
    assert not any(row['id'] == 'runner_load:' + rid for row in get(cloud.api, 'health')['checks'])
    with cloud.store.transaction() as c:
        CM.save(c, 'ops', state='running')
    row = next(row for row in get(cloud.api, 'computers')['computers'] if row['id'] == rid)
    assert start_places.NOT_REPORTING in row['turn_summary']


@pytest.mark.parametrize('hold', ['stale_sample', 'memory', 'readiness'])
def test_last_boot_holds_do_not_deadlock_a_stopped_machines_wake(cloud, hold):
    computer = asleep_bot(cloud)
    work(cloud)
    if hold != 'readiness':
        at = int(time.time()) // 60 * 60 - (300 if hold == 'stale_sample' else 0)
        memory(cloud.api, computer, .86, at=at)
    with cloud.store.transaction() as c:
        # A bot's own message exercises admission's load brake too; a person's message skips it.
        c.execute("UPDATE messages SET sender_id='bot:cpo' WHERE id IN "
                  "(SELECT message_id FROM jobs WHERE bot_id='ops' AND state='queued')")
        if hold == 'readiness':
            c.execute("UPDATE runner_states SET readiness='{}' WHERE runner_id=?", (computer['runner_id'],))
    with cloud.store.read() as c:
        why = execution.bot_claimable(c, cloud.store.settings, computer['runner_id'], 'ops')[0]
        if hold == 'readiness':
            assert why in CM.STALE
        else:
            assert why is None
            assert start_places.computer_hold(c, computer['runner_id'])
        found = CM.facts(c, cloud.store.settings, CM.row(c, 'ops'), cloud.at[0])
        assert found['want'].startswith('job:')
        assert CM.decide(CM.row(c, 'ops'), found, cloud.at[0], 300)[0] == 'start'
    if hold == 'memory':
        assert not any(row['id'] == 'runner_load:' + computer['runner_id'] for row in get(cloud.api, 'health')['checks'])
    assert cloud.tick(10) == ['start']
    # A wake does not bypass the checks the running computer still needs to clear.
    assert claim(cloud.api, computer) is None


@pytest.mark.parametrize('recent_contact', [False, True])
def test_resting_chat_labels_do_not_say_starting_or_not_reporting(cloud, recent_contact):
    computer = asleep_bot(cloud)
    memory(cloud.api, computer, .86, at=int(time.time()) // 60 * 60 - 300)
    if recent_contact:
        with cloud.store.transaction() as c:
            c.execute('UPDATE runner_states SET last_seen_at=? WHERE runner_id=?', (H.now(), computer['runner_id']))
    thread = new_thread(cloud.api)
    say(cloud.api, thread['id'], '@ops can you check this?')
    status = bot_status(cloud.api, thread['id'])[0]
    assert status['detail'] != thread_bot_status.STARTING and CM.ASLEEP in status['detail']
    with cloud.store.read() as c:
        label, _ = views.queued_label(c, 'ops', views.machine(c, 'ops'), cloud.store.settings)
        assert CM.ASLEEP in label and start_places.NOT_REPORTING not in label
