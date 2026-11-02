"""A held run always has someone on it, shows on Health after two hours, and its task's queued work joins its resumed
turn (Dev 543: on Oct 23 the stall check closed BotOps's review of two held DevOps runs and nobody looked for 2.5 days,
while Review 1 verdicts queued behind held runs on their tasks)."""
import pytest

from backend.store import H
from backend import task_privacy as privacy
from backend.auth import Identity
from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401 (api: the fixture)
from backend.tests.test_execution_review import aged, interrupted, job_state, restarted_mid_turn, rival, stop_partway, sweep

TITLE = "Review ops's stopped runs held for a check"


def botops(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(id, slug, display_name, state) VALUES(?, ?, 'BotOps', 'active')",
                  (H.FLEET_MAINTAINER, H.FLEET_MAINTAINER))


def held(api):
    """One run of ops that merged a pull request from a shell and was cut off by a restart: held for BotOps, with its
    review task filed. A shell alone resumes after a restart; a named outside action is what holds it."""
    botops(api)
    machine, message, attempt = restarted_mid_turn(api, [('Read', None), ('Bash', None)])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempt_events SET payload=json_set(payload,'$.summary','gh pr merge 42 --squash') "
                  "WHERE attempt_id=? AND kind='tool' AND json_extract(payload,'$.tool')='Bash' "
                  "AND json_extract(payload,'$.status')='started'", (attempt['id'],))
    aged(api, attempt['id'])
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    return machine, message, attempt


def reviews(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,status,body,created_at FROM tasks WHERE title=? ORDER BY rowid",
                                           (TITLE,))]


def backdate(api, task_id, minutes):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (H.shift(H.now(), minutes=-minutes), task_id))


@pytest.mark.parametrize('how', ['closed', 'done'])
def test_a_held_run_whose_review_task_was_closed_reopens_it_on_the_next_sweep(api, how):
    """Acceptance 8. The hold used to be raised once per run: a closed review left the run with nobody on it."""
    machine, message, attempt = held(api)
    [first] = reviews(api)
    assert first['status'] == 'open' and 'merge pull request' in first['body']
    assert sweep(api) == [] and len(reviews(api)) == 1             # one open review per bot, not one per sweep
    backdate(api, first['id'], 60)
    with api.app.state.store.transaction() as c:
        if how == 'closed':
            H.task_close(c, H.KEEPER, first['id'], 'Closed by the stall check: BotOps cannot diagnose itself.',
                         quiet=True)
        else:
            H.task_update(c, H.bot_actor(H.FLEET_MAINTAINER), first['id'], status='done', note='Looked.')
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    [second] = reviews(api)
    assert second['id'] == first['id'] and second['status'] == 'open'
    assert 'merge pull request' in second['body']
    assert message['body'] not in second['body']                # the request's words stay in their own chat
    # Decided: nothing is held, so a closed review stays closed.
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'run_id': attempt['id'], 'decision': 'dismiss',
         'note': 'The report was already sent; nothing to repeat.', 'acknowledge_uncertain_effects': True})
    with api.app.state.store.transaction() as c:
        H.task_close(c, H.KEEPER, second['id'], 'Settled.', quiet=True)
    backdate(api, second['id'], 60)
    sweep(api)
    assert [r['status'] for r in reviews(api)] == ['closed']


def test_a_review_closed_soon_after_it_was_filed_reopens_on_the_next_sweep(api):
    machine, message, attempt = held(api)
    [first] = reviews(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, H.bot_actor(H.FLEET_MAINTAINER), first['id'], status='done', note='Looked.')
    sweep(api)
    assert [(r['id'], r['status']) for r in reviews(api)] == [(first['id'], 'open')]


def held_again(api, machine):
    """A second run of ops, on a task, stops partway after a shell call and is held."""
    post(api, 'tasks', {'owner': 'ops', 'title': 'Ship the export', 'body': 'Build it and open the PR.'})
    ready(api, machine, ['ops'])
    attempt = claim(api, machine)
    stop_partway(api, machine, attempt, 'held-again')
    aged(api, attempt['id'])
    sweep(api)
    assert job_state(api, attempt['job_id']) == 'uncertain'
    return attempt


def naming(api, review_id, job_id):
    """The review's comments that name `job_id`."""
    with api.app.state.store.read() as c:
        return [m for m in H.task_comments(c, review_id) if job_id in str(m.get('body') or '')]


def test_a_run_held_while_a_review_is_open_is_named_on_that_review_once(api):
    """Later held runs remain visible on the existing review without repeated comments."""
    machine, message, first = held(api)
    [review] = reviews(api)
    assert first['job_id'] in review['body']
    second = held_again(api, machine)
    assert len(reviews(api)) == 1                                   # still the one open review
    [comment] = naming(api, review['id'], second['job_id'])
    assert comment['sender_id'] == H.KEEPER and 'used Bash' in comment['body']
    assert first['job_id'] not in comment['body']                   # the first run is already in the body
    sweep(api)
    sweep(api)
    assert len(naming(api, review['id'], second['job_id'])) == 1    # named once, not on every sweep
    assert naming(api, review['id'], first['job_id']) == []


def test_a_review_closed_while_its_runs_are_held_is_opened_again_naming_each_of_them(api):
    machine, message, first = held(api)
    second = held_again(api, machine)
    [review] = reviews(api)
    with api.app.state.store.transaction() as c:
        H.task_close(c, H.KEEPER, review['id'], 'Closed by the stall check: BotOps cannot diagnose itself.', quiet=True)
    backdate(api, review['id'], 60)
    sweep(api)
    [again] = [r for r in reviews(api) if r['status'] == 'open']
    assert again['id'] == review['id']
    named = again['body'] + ' '.join(m['body'] for m in naming(api, again['id'], first['job_id'])
                                     + naming(api, again['id'], second['job_id']))
    assert first['job_id'] in named and second['job_id'] in named


def capped(c, task):
    """Past the day's stall wakes, as test_stall does it: the next check escalates."""
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), minutes=-120), task['id']))
    for _ in range(H.STALL_WAKES_PER_DAY):
        H.event(c, H.KEEPER, 'task.stall_wake', task['id'], {})
    c.execute("UPDATE events SET created_at=? WHERE action='task.stall_wake' AND target=?",
              (H.shift(H.now(), hours=-1), task['id']))
    c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")


def test_the_stall_check_does_not_close_a_review_while_its_runs_are_still_held(api):
    machine, message, attempt = held(api)
    [review] = reviews(api)
    with api.app.state.store.transaction() as c:
        capped(c, H.task(c, review['id']))
        assert review['id'] in H.wake_stalled(c)['escalated']
        assert H.task(c, review['id'])['status'] == 'open'
        kept = c.execute("SELECT json_extract(detail,'$.kept_open') FROM events "
                         "WHERE action='task.stall_escalated' AND target=?", (review['id'],)).fetchall()
        assert [k[0] for k in kept] == ['runs still held']
    # Its positive twin: once the run is decided, the review is finished at save time, and the stall check leaves
    # it be: nothing is held, so nothing keeps it open.
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'run_id': attempt['id'], 'decision': 'dismiss',
         'note': 'The report was already sent; nothing to repeat.', 'acknowledge_uncertain_effects': True})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM events WHERE action='task.stall_escalated' AND target=?", (review['id'],))
        assert H.task(c, review['id'])['status'] == 'done'
        assert review['id'] not in H.wake_stalled(c)['escalated']
        assert H.task(c, review['id'])['status'] == 'done'


def test_a_held_private_run_has_an_open_operator_review_when_botops_cannot_read_it(api):
    """Criterion 12. Access failure names the boundary and goes to the authorized operator, not a guessed retry."""
    machine, attempt, task_id, other_id = task_held(api, private=True)
    with api.app.state.store.read() as c:
        botops_identity = privacy.actor(Identity('bot:' + H.FLEET_MAINTAINER, 'bot'))
        message = H.message(c, attempt['message']['id'])
        assert not privacy.message_readable(c, botops_identity, message)
        operator = c.execute("SELECT owner_id FROM bot_config WHERE bot='ops'").fetchone()[0]
        display_name = H.bot(c, 'ops')['display_name']
        title = H.HELD_OPERATOR_REVIEW_TITLE.format(display_name=display_name)
    with api.app.state.store.transaction() as c:
        existing = H.task_create(c, H.KEEPER, title, 'Please review the unresolved stopped runs.',
                                 'human:' + operator, private=True, lint=False)
    aged(api, attempt['id'])
    assert sweep(api) == []
    with api.app.state.store.read() as c:
        reviews = [dict(r) for r in c.execute("SELECT id,status,body FROM tasks WHERE title=? AND owner_id=?",
                                              (title, 'human:' + operator))]
        botops_reviews = c.execute("SELECT count(*) FROM tasks WHERE title=? AND status IN "
                                   "('open','doing','waiting','review','ready')",
                                   (TITLE,)).fetchone()[0]
    assert len(reviews) == 1 and reviews[0]['id'] == existing['id'] and reviews[0]['status'] == 'open'
    assert "only the bot's owner or operator" in reviews[0]['body']
    assert 'BotOps has made no recovery decision' in reviews[0]['body']
    assert botops_reviews == 0
    assert 'Bot: ops.' in reviews[0]['body']
    with api.app.state.store.transaction() as c:
        H._settle_undiagnosed(c, H.task(c, existing['id']), 'BotOps cannot diagnose this run')
        assert H.task(c, existing['id'])['status'] == 'open'
        # Renamed after the review was filed, with another bot now under its old name: the title no longer leads to
        # ops, and the review still stays open while its run is held.
        c.execute("UPDATE bots SET display_name='Ops (renamed)' WHERE slug='ops'")
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('decoy', 'decoy', ?, 'active')", (display_name,))
        H._settle_undiagnosed(c, H.task(c, existing['id']), 'BotOps cannot diagnose this run')
        assert H.task(c, existing['id'])['status'] == 'open'
        c.execute("DELETE FROM bots WHERE slug='decoy'")
        c.execute("UPDATE bots SET display_name=? WHERE slug='ops'", (display_name,))
    # A safe decision ends the held run, so the same stall check can close the review as usual.
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'run_id': attempt['id'], 'decision': 'dismiss',
         'note': 'The operator checked the request and it should not be repeated.',
         'acknowledge_uncertain_effects': True})
    with api.app.state.store.transaction() as c:
        H._settle_undiagnosed(c, H.task(c, existing['id']), 'BotOps cannot diagnose this run')
        assert H.task(c, existing['id'])['status'] == 'closed'
    # The reused operator request closes only after a safe decision; no second task appears after settlement.
    assert sweep(api) == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title=? AND owner_id=?",
                         (title, 'human:' + operator)).fetchone()[0] == 1


def stopped_two_hours(api, attempt_id, minutes=121):
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempts SET finished_at=? WHERE id=?', (H.shift(H.now(), minutes=-minutes), attempt_id))


def uncertain_issue(api, token='ana-test'):
    return next(i for i in get(api, 'operations', token=token)['issues'] if i['kind'] == 'uncertain_work')


def test_a_held_run_left_two_hours_without_a_decision_is_on_health_and_its_operators_needs_you(api):
    """Acceptance 9."""
    machine, message, attempt = held(api)
    stopped_two_hours(api, attempt['id'], minutes=119)
    early = uncertain_issue(api)
    assert 'saved 1 stopped run for later' in early['title'] and early['needs_person'] is False
    assert not [ch for ch in get(api, 'health')['checks'] if ch['id'] == 'stopped_runs']
    stopped_two_hours(api, attempt['id'])
    issue = uncertain_issue(api)                                   # Ana operates ops: her Needs you shows it
    assert issue['title'] == 'ops has 1 stopped run waiting 2 h'
    assert issue['needs_person'] is True and issue['action'] == 'review' and issue['severity'] == 'error'
    assert issue['detail'].startswith('Nobody has decided it yet.')
    [check] = [ch for ch in get(api, 'health')['checks'] if ch['id'] == 'stopped_runs']
    assert [item['text'] for item in check['items']] == ['ops: 1 stopped run waiting 2 h']
    assert check['items'][0]['bot'] == 'ops' and check['status'] == 'warn'
    # Ben reads ops but does not operate it: Health and Settings name it; his Needs you does not interrupt him.
    ben = uncertain_issue(api, 'ben-test')
    assert ben['title'] == 'ops has 1 stopped run waiting 2 h' and ben['needs_person'] is False
    assert [ch for ch in get(api, 'health', token='ben-test')['checks'] if ch['id'] == 'stopped_runs']
    # A saved decision is a review: it is no longer "nobody decided".
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'run_id': attempt['id'], 'decision': 'dismiss',
         'note': 'The report was already sent; nothing to repeat.', 'acknowledge_uncertain_effects': True})
    assert not [i for i in get(api, 'operations')['issues'] if i['kind'] == 'uncertain_work']
    assert not [ch for ch in get(api, 'health')['checks'] if ch['id'] == 'stopped_runs']


def test_a_saved_choice_waiting_for_a_live_lane_is_not_counted_as_unreviewed(api):
    """A deferred resume/dismiss is already reviewed even while its job remains uncertain (Dev 545 boundary)."""
    machine, message, attempt = interrupted(api)
    other = rival(api, machine, attempt)
    result = post(api, f"jobs/{attempt['job_id']}/reconcile", {'run_id': attempt['id'], 'decision': 'resume',
                  'note': 'Reviewed; continue after the other run ends.', 'acknowledge_uncertain_effects': True})
    assert result == {'job_id': attempt['job_id'], 'state': 'uncertain', 'deferred': True}
    assert job_state(api, attempt['job_id']) == 'uncertain'
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempts SET finished_at=? WHERE id=?',
                  (H.shift(H.now(), minutes=-121), attempt['id']))
    issue = uncertain_issue(api)
    assert 'waiting 2 h' not in issue['title'] and issue['needs_person'] is False
    assert 'saved 1 stopped run for later' in issue['title']
    assert not [ch for ch in get(api, 'health')['checks'] if ch['id'] == 'stopped_runs']


def test_a_saved_task_choice_with_queued_work_waits_for_the_live_lane_without_asking_again(api):
    """A saved choice is reviewed even when same-task work remains fenced behind its busy lane (Review 1)."""
    from backend.tests.test_execution_review import rival

    machine, attempt, task_id, _ = task_held(api)
    rival(api, machine, attempt)
    result = post(api, f"jobs/{attempt['job_id']}/reconcile", {'run_id': attempt['id'], 'decision': 'resume',
                  'note': 'Reviewed; continue after the other run ends.', 'acknowledge_uncertain_effects': True})
    assert result == {'job_id': attempt['job_id'], 'state': 'uncertain', 'deferred': True}
    assert len([job for job in queued_on(api, task_id) if job['id'] != attempt['job_id']]) == 1
    issue = uncertain_issue(api)
    assert issue['needs_person'] is False
    assert 'saved 1 stopped run for later' in issue['title']
    assert 'Choose Try again or Don\'t retry' not in issue['detail']
    assert not issue.get('held_back')


def test_a_queued_reset_deferral_does_not_create_a_held_review(api):
    """A known-empty retry deferred to the primary reset is queued work, not an unresolved held attempt (Dev 545)."""
    machine, message, attempt = restarted_mid_turn(api, [('Bash', None)])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='queued' WHERE id=?", (attempt['job_id'],))
    assert sweep(api) == []
    assert job_state(api, attempt['job_id']) == 'queued'
    assert reviews(api) == []
    assert not [ch for ch in get(api, 'health')['checks'] if ch['id'] == 'stopped_runs']


def task_held(api, private=False):
    """ops's run on a task stops partway after a shell call; a verdict queues behind it on that task, and a notice on
    another task of ops queues too."""
    machine = runner(api, 'ana')
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    task = post(api, 'tasks', {'owner': 'ops', 'title': 'Ship the export', 'body': 'Build it and open the PR.',
                              'private': private})
    task_id = task.get('task', task).get('id') or task['refs']['task']
    attempt = claim(api, machine)
    stop_partway(api, machine, attempt, 'held')
    other = post(api, 'tasks', {'owner': 'ops', 'title': 'Weekly digest', 'body': 'Send it.'})
    other_id = other.get('task', other).get('id') or other['refs']['task']
    # Tico hands a review child's verdict back to its parent task as its own notice.
    with api.app.state.store.transaction() as c:
        H.task_comment(c, H.KEEPER, task_id, 'VERDICT: changes PR #12 head ' + 'a' * 40)
        H.task_comment(c, H.KEEPER, other_id, 'The digest numbers are in.')
    return machine, attempt, task_id, other_id


def task_held_many(api, count):
    machine, attempt, task_id, other_id = task_held(api)
    with api.app.state.store.transaction() as c:
        task = H.task(c, task_id)
        conversation = H.conversation(c, task['conversation_id'])
        for i in range(1, count):
            message = H._write_message(c, H.KEEPER, 'bot:ops', f'Subtask result {i}', conversation, 'say',
                                       {'task': task_id}, None, None)
            c.execute("INSERT OR IGNORE INTO jobs(id,message_id,bot_id,state,created_at) VALUES(?,?,?,'queued',?)",
                      (message['id'], message['id'], 'ops', message['created_at']))
    return machine, attempt, task_id, other_id


def queued_on(api, task_id):
    from backend.execution import queued_task_sql
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(
            "SELECT j.id,j.state,j.attempt_id,m.body FROM jobs j JOIN messages m ON m.id=j.message_id "
            f"JOIN conversations cv ON cv.id=m.conversation_id WHERE j.bot_id='ops' AND {queued_task_sql()}=? "
            "ORDER BY m.rowid", (task_id,))]


@pytest.mark.parametrize('folds', [True, False])
@pytest.mark.parametrize('count', [1, 21])
def test_queued_work_behind_a_held_run_joins_its_resumed_turn(api, monkeypatch, folds, count):
    """Acceptance 10. `folds=False` is the removal control; all same-task jobs join, not just the first 20."""
    from backend import execution
    if not folds:
        monkeypatch.setattr(execution.Execution, 'fold_behind', staticmethod(
            lambda c, row, aid, msg, task_id, followed, lanes, roots, lane: msg))
    machine, attempt, task_id, other_id = task_held_many(api, count)
    assert len([j for j in queued_on(api, task_id) if j['id'] != attempt['job_id']]) == count
    ready(api, machine, ['ops'])
    resumed = post(api, f"jobs/{attempt['job_id']}/reconcile", {'run_id': attempt['id'], 'decision': 'resume',
                   'note': 'Nothing was pushed yet; carry on from the build.', 'acknowledge_uncertain_effects': True})
    assert resumed['state'] == 'queued'
    turn = claim(api, machine)
    assert turn['job_id'] == attempt['job_id']
    body = turn['message']['body']
    behind = [j for j in queued_on(api, task_id) if j['id'] != attempt['job_id']]
    assert len(behind) == count
    if not folds:
        assert 'VERDICT: changes PR #12' not in body and {j['state'] for j in behind} == {'queued'}
        return
    assert 'Queued on this task while this run was held' in body
    assert 'VERDICT: changes PR #12 head ' + 'a' * 40 in body
    assert all(f'Subtask result {i}' in body for i in range(1, count))
    assert {(j['state'], j['attempt_id']) for j in behind} == {('input', turn['id'])}
    # The other task's comment keeps its own turn.
    others = queued_on(api, other_id)
    assert others and {j['state'] for j in others} == {'queued'}
    post(api, f"attempts/{turn['id']}/started", {'thread_id': 'held'}, machine['token'])
    post(api, f"attempts/{turn['id']}/complete", {'outcome': 'completed', 'last_seq': 0,
         'text': 'Took the verdict; fixing the export.'}, machine['token'])
    assert {j['state'] for j in queued_on(api, task_id)} == {'completed'}
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='job.fold_behind' AND target=?",
                         (turn['id'],)).fetchone()[0] == 1


def test_a_resumed_turn_takes_an_input_its_held_run_read_after_the_history_sweep_indexed_that_run(api):
    """Moving the input marks what read the held run as changed twice in one write (the move's event, then the input's
    own row). With the sweep's index of that run in place, a claim that fails here fails every time, and no bot on
    the computer starts a turn."""
    from backend import read_deps
    machine, attempt, task_id, _ = task_held(api)
    with api.app.state.store.transaction() as c:
        verdict = c.execute("SELECT m.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='ops' "
                            "AND j.state='queued' AND m.body LIKE 'VERDICT:%'").fetchone()[0]
        c.execute("INSERT INTO attempt_inputs VALUES(?,?,?)", (attempt['id'], verdict, H.now()))
    with api.app.state.store.read() as conn:
        read_deps.sweep(conn)
    ready(api, machine, ['ops'])
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'run_id': attempt['id'], 'decision': 'resume',
         'note': 'Nothing was pushed yet; carry on from the build.', 'acknowledge_uncertain_effects': True})
    turn = claim(api, machine)
    assert 'VERDICT: changes PR #12' in turn['message']['body']
    with api.app.state.store.read() as c:
        assert c.execute("SELECT attempt_id FROM attempt_inputs WHERE message_id=?", (verdict,)).fetchone()[0] == turn['id']


def test_a_folded_input_another_run_already_read_keeps_that_runs_task(api):
    """A message the held run read before it stopped moves to the resumed turn; the held run must still close when
    the task later turns private, as it does when inputs() moves a deferred input."""
    machine, attempt, task_id, _ = task_held(api)
    with api.app.state.store.transaction() as c:
        verdict = c.execute("SELECT m.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='ops' "
                            "AND j.state='queued' AND m.body LIKE 'VERDICT:%'").fetchone()[0]
        c.execute("INSERT INTO attempt_inputs VALUES(?,?,?)", (attempt['id'], verdict, H.now()))
    ready(api, machine, ['ops'])
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'run_id': attempt['id'], 'decision': 'resume',
         'note': 'Nothing was pushed yet; carry on from the build.', 'acknowledge_uncertain_effects': True})
    turn = claim(api, machine)
    assert turn['id'] != attempt['id'] and 'VERDICT: changes PR #12' in turn['message']['body']
    with api.app.state.store.transaction() as c:
        assert c.execute("SELECT attempt_id FROM attempt_inputs WHERE message_id=?", (verdict,)).fetchone()[0] == turn['id']
        moved = c.execute("SELECT count(*) FROM events WHERE action=? AND target=?",
                          (privacy.INPUT_MOVED, attempt['id'])).fetchone()[0]
        assert moved == 1
        assert task_id in privacy.attempt_tasks(c, attempt['id'])


def test_two_requests_folded_into_one_stopped_run_are_one_run_on_needs_you_and_health(api):
    """Two requests that rode on one interrupted run are two jobs but one run: one line with its title once, and one
    count wherever runs are counted (Needs you, the two-hour check on Health)."""
    from backend.tests.test_execution_review import busy_folded_run, stopped_issue
    jobs = busy_folded_run(api)
    [attempt_id] = {job['run_id'] for job in jobs}
    issue = stopped_issue(api)
    assert issue['title'].endswith('has 1 stopped run holding up work')
    [run] = issue['runs']
    assert run['title'] == 'Ship the receipts fix' and len(run['jobs']) == 2
    stopped_two_hours(api, attempt_id)
    [check] = [ch for ch in get(api, 'health')['checks'] if ch['id'] == 'stopped_runs']
    assert [item['text'] for item in check['items']] == ['ops: 1 stopped run waiting 2 h']
    late = stopped_issue(api)                                       # the queued follow-up still waits on it
    assert late['title'] == 'ops has 1 stopped run holding up work'
    assert late['runs'][0]['title'] == 'Ship the receipts fix'
