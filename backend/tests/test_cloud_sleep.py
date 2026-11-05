"""A cloud machine asleep on purpose explains its queue without hiding a wake that failed to happen."""

import sqlite3
import time

import pytest

from backend import (check_models, cloud_machines as CM, execution, health, providers, start_places,
                     thread_bot_status, usage_limits, views)
from backend.store import H, encode
from backend.tests.test_api import api, assign, claim, get, ready, runner  # noqa: F401
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
    task = waiting_task(cloud)
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
    with cloud.store.transaction() as c:
        c.execute('UPDATE tasks SET waiting_on=NULL WHERE id=?', (task['id'],))
    assert cloud.tick(10) == ['start']
    body = get(cloud.api, 'health')
    queue = next(row for row in body['checks'] if row['id'] == 'queue')
    assert queue['status'] == 'bad' and 'Cloud machine: Starting' in queue['summary']


def test_health_keeps_a_resting_bots_reason_when_another_bot_is_stuck(cloud):
    asleep_bot(cloud)
    waiting_task(cloud)
    work(cloud, 'cpo')
    with cloud.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE id='cpo'")
    body = get(cloud.api, 'health')
    queue = next(row for row in body['checks'] if row['id'] == 'queue')
    assert {row['bot']: row['reason'] for row in queue['items']} == {'ops': 'task_wait', 'cpo': 'paused'}
    assert [row['bot'] for row in body['stuck']] == ['cpo']


def test_an_old_task_wait_does_not_hide_a_sleeping_bots_signed_out_check(cloud):
    computer = asleep_bot(cloud)
    parent = waiting_task(cloud)
    with cloud.store.transaction() as c:
        check = H.task_create(c, 'bot:ops', 'Check the change', '', 'bot:ops',
                              parent_id=parent['id'], private=False)
        check_models.record(c, 'bot:ops', check['id'], {
            'runtime': 'grok', 'model': providers.recommended('xai')['id'],
            'harness': 'grok', 'effort': '', 'role': 'check'})
        c.execute('UPDATE runner_states SET readiness=? WHERE runner_id=?', (encode({
            'schema_version': 1, 'bots': {'ops': {'ready': True}},
            'runtimes': {'grok': {'installed': True, 'authenticated': 'missing'}}}), computer['runner_id']))
        c.execute("UPDATE jobs SET created_at=? WHERE state='queued' AND message_id IN "
                  "(SELECT id FROM messages WHERE conversation_id=?)",
                  (H.shift(H.now(), hours=-1), check['conversation_id']))
    with cloud.store.read() as c:
        args = (c, cloud.store.settings, computer['runner_id'], 'ops')
        assert execution.bot_claimable(*args, ignore_refusals=CM.STALE)[0] == 'task_wait'
        assert {why for _, why in execution.queued_reasons(*args, ignore_refusals=CM.STALE)} == {
            'task_wait', 'check_model'}
    body = get(cloud.api, 'health')
    queue = next(row for row in body['checks'] if row['id'] == 'queue')
    assert queue['status'] == 'bad'
    assert [(row['bot'], row['reason']) for row in queue['items']] == [('ops', 'check_model')]
    assert [(row['bot'], row['reason']) for row in body['stuck']] == [('ops', 'check_model')]
    assert {group['reason']: group['queued'] for group in body['stuck'][0]['queue_reasons']} == {
        'task_wait': 1, 'check_model': 1}
    assert queue['items'][0]['fixes'] == [health._fix(*health.BOT_SETTINGS)]


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


def test_ignoring_last_boot_readiness_keeps_a_sleeping_bots_spending_hold(cloud):
    computer = asleep_bot(cloud)
    work(cloud)
    with cloud.store.transaction() as c:
        c.execute("INSERT INTO attempts(id,bot_id,state,created_at,settled_at,exit,est_cost_usd) "
                  "VALUES('spent-run','ops','completed',?,?,'completed',5)", (H.now(), H.now()))
        usage_limits.save_own(c, 'ops', {'daily_usd': 1}, None)
        c.execute("UPDATE runner_states SET readiness='{}' WHERE runner_id=?", (computer['runner_id'],))
        c.execute("UPDATE jobs SET created_at=? WHERE bot_id='ops' AND state='queued'",
                  (H.shift(H.now(), minutes=-health.QUEUE_MINUTES - 1),))
    with cloud.store.read() as c:
        args = (c, cloud.store.settings, computer['runner_id'], 'ops')
        assert execution.bot_claimable(*args)[0] == 'readiness_unknown'
        assert execution.bot_claimable(*args, ignore_refusals=CM.STALE)[0] == 'limit'
        assert {why for _, why in execution.queued_reasons(*args, ignore_refusals=CM.STALE)} == {'limit'}
    body = get(cloud.api, 'health')
    queue = next(row for row in body['checks'] if row['id'] == 'queue')
    assert queue['items'][0]['reason'] == 'limit'
    assert 'Over its spending limit' in queue['summary']
    assert body['stuck'][0]['reason'] == 'limit'


@pytest.mark.parametrize('readiness', [None, 'readiness_unknown', 'not_ready'])
def test_a_young_claimable_job_is_not_aged_by_an_old_task_wait(cloud, readiness):
    computer = asleep_bot(cloud)
    waiting_task(cloud)
    message = work(cloud)
    if readiness:
        document = ('{}' if readiness == 'readiness_unknown' else
                    '{"schema_version":1,"bots":{"ops":{"ready":false}}}')
        with cloud.store.transaction() as c:
            c.execute('UPDATE runner_states SET readiness=? WHERE runner_id=?', (document, computer['runner_id']))
        with cloud.store.read() as c:
            # The display looks past last-boot readiness; the real claim still refuses it.
            assert execution.bot_claimable(c, cloud.store.settings, computer['runner_id'], 'ops')[0] == readiness
    body = get(cloud.api, 'health')
    assert next(row for row in body['checks'] if row['id'] == 'queue')['status'] == 'ok'
    assert not body['stuck']
    row = next(row for row in get(cloud.api, 'computers')['computers'] if row['id'] == computer['runner_id'])
    assert any('person' in w['reason'] and w['count'] == 1 for w in row['turn_counts']['waits'])
    with cloud.store.transaction() as c:
        c.execute('UPDATE jobs SET created_at=? WHERE message_id=?',
                  (H.shift(H.now(), minutes=-health.QUEUE_MINUTES - 1), message['id']))
    body = get(cloud.api, 'health')
    queue = next(row for row in body['checks'] if row['id'] == 'queue')
    assert queue['status'] == 'bad' and 'Not started after 15 min' in queue['summary']
    assert body['stuck'][0]['reason'] == 'cloud_machine'


@pytest.mark.parametrize('check', ['task', 'login'])
def test_a_failing_sleeping_job_diagnostic_does_not_break_computer_listings(cloud, monkeypatch, check):
    computer = asleep_bot(cloud)
    work(cloud)
    work(cloud, text='Please check the next item too.')
    other = runner(cloud.api)
    assign(cloud.api, other, 'cpo')
    ready(cloud.api, other, ['cpo'])
    work(cloud, 'cpo')
    with cloud.store.read() as c:
        jobs = list(c.execute(execution.queued_jobs_sql(), ('ops',)))
    assert len(jobs) == 2
    bad = jobs[-1]['id']
    job_check, login_check = execution.job_refusal, execution.login_refusal
    checked = []

    def fail(job_id):
        checked.append(job_id)
        if job_id == bad:
            raise RuntimeError('Cannot check this job')

    def task_check(c, job, *args, **kwargs):
        fail(job['id'])
        return job_check(c, job, *args, **kwargs)

    def per_job_login(c, settings, runner_id, bot, config, job_id=None, **kwargs):
        if job_id:
            fail(job_id)
        return login_check(c, settings, runner_id, bot, config, job_id, **kwargs)

    monkeypatch.setattr(execution, 'job_refusal' if check == 'task' else 'login_refusal',
                        task_check if check == 'task' else per_job_login)
    for path, key in [('computers', 'computers'), ('operations', 'machines')]:
        checked.clear()
        rows = {row['id']: row for row in get(cloud.api, path)[key]}
        counts = rows[computer['runner_id']]['turn_counts']
        assert counts['waiting'] == 2 and counts['queued'] == 0
        assert sum(w['count'] for w in counts['waits']) == 2
        assert checked.index(jobs[0]['id']) < checked.index(bad)
        assert rows[other['runner_id']]['turn_counts']['queued'] == 1


def test_a_storage_failure_in_sleeping_queue_diagnostics_still_fails_the_read(cloud, monkeypatch):
    computer = asleep_bot(cloud)
    work(cloud)

    def full_disk(*args, **kwargs):
        error = sqlite3.OperationalError('database or disk is full')
        error.sqlite_errorcode = sqlite3.SQLITE_FULL
        raise error

    monkeypatch.setattr(execution, 'job_refusal', full_disk)
    with cloud.store.read() as c, pytest.raises(sqlite3.OperationalError, match='disk is full'):
        start_places.summary(c, computer['runner_id'], cloud.store.settings)


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


def test_recent_contact_does_not_keep_a_memory_warning_after_a_planned_stop(cloud):
    computer = asleep_bot(cloud)
    rid = computer['runner_id']
    memory(cloud.api, computer, .86)
    with cloud.store.transaction() as c:
        c.execute('UPDATE runner_states SET last_seen_at=? WHERE runner_id=?', (H.now(), rid))
    body = get(cloud.api, 'health')
    assert next(row for row in body['computers'] if row['id'] == rid)['online']
    assert not any(row['id'] == 'runner_load:' + rid for row in body['checks'])
    with cloud.store.transaction() as c:
        CM.save(c, 'ops', state='running')
    load = next(row for row in get(cloud.api, 'health')['checks'] if row['id'] == 'runner_load:' + rid)
    assert load['status'] == 'warn' and 'memory' in load['summary']


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
