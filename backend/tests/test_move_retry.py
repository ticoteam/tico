"""A refused review move is retried by Tico once what refused it clears (Dev 534 C, criterion 8)."""
import json

import pytest

from backend import hubdb as H, move_retry
from backend.tests.test_github import api, get, headers  # noqa: F401
from backend.tests.test_review_gate import ticket, move, HEAD, BASE

RECEIPT = f'Tests: base {BASE} head {HEAD}: 9 passed, 0 failed'


def managed(api, task):
    """The type is managed by cmo, as Dev tickets are by Head of Engineering."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET manager='cmo' WHERE id=?", (task['type_id'],))


def step(api, task):
    return get(api, 'tasks/' + task['id'])['task']['step']['name']


def retries(api, task):
    with api.app.state.store.read() as c:
        return [json.loads(r[0]) for r in c.execute(
            "SELECT new FROM task_events WHERE task_id=? AND field='move_retry' ORDER BY ts, rowid", (task['id'],))]


def tick(api):
    return move_retry.tick(api.app.state.github_app, force=True)


def owned_by(api, slug):
    with api.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT title FROM tasks WHERE owner=?", (H.bot_actor(slug),))]


def test_refused_for_missing_receipt_moves_itself_when_the_receipt_is_posted(api):
    task = ticket(api)
    managed(api, task)
    refused = move(api, task)
    assert refused.status_code == 422
    assert 'Tico retries this move itself when a passing test receipt' in refused.text
    assert [r['step'] for r in retries(api, task)] == ['Review 1']
    # Nothing changed: no retry, no new refusal.
    assert tick(api) == [] and step(api, task) == 'Blocked'
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET note=? WHERE id=?', (RECEIPT, task['id']))
    assert tick(api) == [task['id']]
    assert step(api, task) == 'Review 1'
    with api.app.state.store.read() as c:
        mover = c.execute("SELECT actor FROM task_events WHERE task_id=? AND field='step' ORDER BY ts DESC, rowid DESC",
                          (task['id'],)).fetchone()[0]
    assert mover == H.KEEPER
    assert owned_by(api, 'cmo') == []       # the type's manager heard nothing
    assert tick(api) == []                  # done once


def test_refused_for_failing_checks_moves_when_the_checks_pass(api):
    task = ticket(api, RECEIPT)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail_json=json_set(detail_json,'$.checks',json(?)) WHERE task_id=? AND kind='pr'",
                  (json.dumps({'python': 'failing'}), task['id']))
    refused = move(api, task)
    assert refused.status_code == 422 and 'Tico retries this move itself when the checks' in refused.text
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail_json=json_set(detail_json,'$.checks',json(?)) WHERE task_id=? AND kind='pr'",
                  (json.dumps({'python': 'passing'}), task['id']))
    assert tick(api) == [task['id']] and step(api, task) == 'Review 1'


def test_a_retry_still_refused_waits_for_the_next_change_and_stops_when_the_ticket_moves(api):
    task = ticket(api)
    assert move(api, task).status_code == 422
    with api.app.state.store.transaction() as c:   # a change, but not a receipt
        c.execute('UPDATE tasks SET note=? WHERE id=?', ('Still building the fix.', task['id']))
    assert tick(api) == [] and step(api, task) == 'Blocked'
    tries = retries(api, task)
    assert len(tries) == 2 and tries[-1]['tries'] == 1 and tries[-1]['state'] == 'waiting'
    assert tick(api) == [] and len(retries(api, task)) == 2      # same inputs: not tried again
    # The builder moves it elsewhere: the remembered move is dropped even when a receipt then arrives.
    fresh = get(api, 'tasks/' + task['id'])['task']
    with api.app.state.store.transaction() as c:
        # A person: a bot's move to Done needs every review slot approved (review_gate.passed).
        H.task_update(c, 'human:ana', task['id'], step='Done')
        c.execute('UPDATE tasks SET note=? WHERE id=?', (RECEIPT, task['id']))
    assert fresh['step']['name'] == 'Blocked'
    assert tick(api) == [] and step(api, task) == 'Done'


def test_hint_names_what_clears_each_refusal():
    assert 'checks on the current head pass' in move_retry.hint('ci', 'CI failing at head x')
    assert 'GitHub can be read again' in move_retry.hint('ci', 'cannot verify the current head and CI for x')
    assert move_retry.hint('other') == ''


TEMPORARY = {'problem_code': 'github_unreachable', 'temporary': True, 'devops': False}
PERMANENT = {'problem_code': 'github_app_permissions', 'temporary': False, 'devops': True}
UNEXPECTED = {'problem_code': 'github_read', 'temporary': False, 'devops': False}


def unreadable(api, monkeypatch, cause=None):
    """GitHub cannot be read at a review move; `cause` is what the gate's read reported about why. Returns a
    switch that makes GitHub readable again."""
    from datetime import datetime, timezone
    from backend import review_gate
    state = {'down': True}
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug, display_name, runtime, state) VALUES('devops-engineer','DevOps','fake','active')")

    def snapshot(service, prs):
        if state['down']:
            return {pr['id']: {'error': True, 'why': 'GitHub did not answer'} for pr in prs}
        return {pr['id']: {'head_sha': HEAD, 'checks': {'check:python': 'passing'},
                           'at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'} for pr in prs}
    real = review_gate.reject

    def reject(c, actor, row, kind, head, pr, text, *rest, **options):
        try:
            real(c, actor, row, kind, head, pr, text, *rest, **options)
        except H.Refused as exc:
            exc.review_gate.update(cause or {})
            raise
    monkeypatch.setattr(review_gate, 'snapshot', snapshot)
    monkeypatch.setattr(review_gate, 'reject', reject)
    return lambda: state.update(down=False)


def later(minutes):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def devops_tasks(api):
    return owned_by(api, 'devops-engineer')


def test_temporary_github_failure_retries_on_the_timer_and_never_asks_devops(api, monkeypatch):
    task = ticket(api, RECEIPT)
    back = unreadable(api, monkeypatch, TEMPORARY)
    assert move(api, task).status_code == 422
    assert move_retry.tick(api.app.state.github_app, at=later(1), force=True) == []   # inside the 5 minutes
    for minutes in (6, 12):
        assert move_retry.tick(api.app.state.github_app, at=later(minutes), force=True) == []
    assert step(api, task) == 'Blocked' and devops_tasks(api) == []
    assert retries(api, task)[-1]['problem_code'] == 'github_unreachable'
    back()
    assert move_retry.tick(api.app.state.github_app, at=later(18), force=True) == [task['id']]
    assert step(api, task) == 'Review 1' and devops_tasks(api) == []


def test_permission_failure_asks_devops_once_and_moves_when_github_reads_again(api, monkeypatch):
    task = ticket(api, RECEIPT)
    back = unreadable(api, monkeypatch, PERMANENT)
    assert move(api, task).status_code == 422
    assert devops_tasks(api) == ['GitHub cannot be read for review moves']    # on the first refusal
    fresh = get(api, 'tasks/' + task['id'])['task']
    assert move(api, fresh).status_code == 422                                # the builder tries again
    assert move_retry.tick(api.app.state.github_app, at=later(6), force=True) == []
    assert len(devops_tasks(api)) == 1                                        # the same ask, reused
    back()
    assert move_retry.tick(api.app.state.github_app, at=later(12), force=True) == [task['id']]
    assert step(api, task) == 'Review 1'


@pytest.mark.parametrize('cause', [None, UNEXPECTED], ids=['no-cause', 'unexpected-cause'])
def test_unreadable_without_a_temporary_cause_asks_devops_only_when_it_repeats(api, monkeypatch, cause):
    task = ticket(api, RECEIPT)
    unreadable(api, monkeypatch, cause)
    assert move(api, task).status_code == 422
    assert devops_tasks(api) == []
    assert move_retry.tick(api.app.state.github_app, at=later(6), force=True) == []
    assert devops_tasks(api) == ['GitHub cannot be read for review moves']


def test_one_tickets_failing_retry_does_not_hold_back_the_others(api, monkeypatch):
    rows = [({'id': 'broken'}, {}), ({'id': 'fine'}, {})]
    monkeypatch.setattr(move_retry, 'pending', lambda c, at: rows)

    def retry(store, service, row, detail):
        if row['id'] == 'broken':
            raise RuntimeError('synthetic failure for one ticket')
        return True

    monkeypatch.setattr(move_retry, 'retry', retry)
    assert tick(api) == ['fine']


def test_retries_refused_again_expire_three_days_after_the_first_refusal(api):
    from datetime import datetime, timedelta, timezone
    task = ticket(api)
    assert move(api, task).status_code == 422
    first = (datetime.now(timezone.utc) - timedelta(days=2, hours=12)).strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
    with api.app.state.store.transaction() as c:    # the first refusal was two and a half days ago
        c.execute('UPDATE task_events SET ts=? WHERE task_id=?', (first, task['id']))
        c.execute("UPDATE task_events SET new=json_set(new,'$.at',?,'$.since',?) "
                  "WHERE task_id=? AND field='move_retry'", (first, first, task['id']))
        c.execute('UPDATE tasks SET note=? WHERE id=?', ('Still building the fix.', task['id']))
    assert tick(api) == []                          # retried now and refused again
    assert retries(api, task)[-1]['since'] == first
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET note=? WHERE id=?', (RECEIPT, task['id']))
    assert move_retry.tick(api.app.state.github_app, at=later(13 * 60), force=True) == []
    assert step(api, task) == 'Blocked'


def test_a_private_tickets_blocker_is_private_to_devops_and_does_not_name_it(api, monkeypatch):
    task = ticket(api, RECEIPT)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET private=1 WHERE id=?', (task['id'],))
    unreadable(api, monkeypatch, PERMANENT)
    assert move(api, task).status_code == 422
    with api.app.state.store.read() as c:
        blocker = dict(c.execute("SELECT * FROM tasks WHERE owner=?", (H.bot_actor('devops-engineer'),)).fetchone())
        audit = [tuple(r) for r in c.execute("SELECT target, detail_json FROM events WHERE action='blocker.routed'")]
        assert blocker['private']
        assert H.task_private_readable(c, H.bot_actor('devops-engineer'), blocker)
        assert not H.task_private_readable(c, 'human:ana', blocker)     # not on the private task or the blocker
    assert 'a private task' in blocker['body']
    assert task['id'] not in blocker['body'] and task['title'] not in blocker['body']
    for target, detail in audit:                       # the company-readable audit row carries no PR, repo or task
        assert 'github.com' not in target and '/pull/' not in target and task['id'] not in detail
    seen = api.get('/api/v2/tasks/' + blocker['id'], headers=headers())     # ana, a person not on it
    assert seen.status_code in (403, 404) and 'github.com' not in seen.text and 'review moves' not in seen.text


def test_a_private_tickets_receipt_comment_moves_it(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET private=1 WHERE id=?', (task['id'],))
    assert move(api, task).status_code == 422
    with api.app.state.store.transaction() as c:    # the builder's receipt, as a comment rather than the note
        H._write_message(c, task['owner'], task['requester'], RECEIPT, H.conversation(c, task['conversation_id']), 'say',
                         {'task': task['id'], 'comment': True}, None, None)
    assert tick(api) == [task['id']]
    assert step(api, task) == 'Review 1'


def test_a_retry_does_not_pass_on_evidence_the_mover_cannot_read(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:      # a private review child only the owner reads, holding a receipt
        # Ana heads every bot that reports to no one, so cpo reports to another root person she does not manage.
        roster = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster['people'].append({'id': 'zed', 'email': 'zed@acme.example'})
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (json.dumps(roster),))
        c.execute("UPDATE bot_config SET reports_to='human:zed' WHERE bot='cpo'")
        child = H.task_create(c, H.bot_actor('cpo'), 'Evidence', 'x', H.bot_actor('cpo'), parent_id=task['id'])
        c.execute("UPDATE tasks SET private=1, title='Review 1' WHERE id=?", (child['id'],))
        H._write_message(c, H.bot_actor('cpo'), H.bot_actor('cpo'), RECEIPT, H.conversation(c, child['conversation_id']),
                         'say', {'task': child['id'], 'comment': True}, None, None)
    assert move(api, task).status_code == 422           # ana cannot read it
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET note='unrelated' WHERE id=?", (task['id'],))
    assert tick(api) == [] and step(api, task) == 'Blocked'
    assert retries(api, task)[-1]['by'] == 'human:ana'  # a refused retry keeps the mover it reads as


def test_a_refused_move_is_retried_as_the_actor_it_was_made_as_not_the_caller(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:      # BotOps calls as itself, moving for ana
        with pytest.raises(H.Refused) as refused:
            H.task_update(c, 'human:ana', task['id'], step='Review 1', review_prs={})
        move_retry.remember(c, H.bot_actor('botops'), refused.value.review_gate)
    assert retries(api, task)[-1]['by'] == 'human:ana'


def test_a_refused_move_held_blocked_is_retried_even_when_the_blocked_sweep_runs_first(api):
    """A refused move holds the ticket on Blocked (backend/task_blocked.py). When what refused it clears, the move is
    made, not undone: the blocked sweep must not send it back to its earlier step and drop the remembered move."""
    from backend import task_blocked
    task = ticket(api)
    assert move(api, task).status_code == 422
    assert step(api, task) == 'Blocked'
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET note=? WHERE id=?', (RECEIPT, task['id']))
        task_blocked.sweep(c, H.now())
    assert step(api, task) == 'Blocked'
    assert tick(api) == [task['id']] and step(api, task) == 'Review 1'
    with api.app.state.store.read() as c:
        assert H.task(c, task['id'])['blocked_since'] is None


def test_a_gate_hold_with_no_live_retry_goes_back_to_its_earlier_step(api):
    """Once Tico no longer retries the move (it expired), the blocked sweep clears the gate hold as before."""
    from datetime import datetime, timezone
    from backend import task_blocked
    task = ticket(api)
    assert move(api, task).status_code == 422
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_events SET new=json_set(new,'$.since','2020-01-01T00:00:00.000000Z') "
                  "WHERE task_id=? AND field='move_retry'", (task['id'],))
        c.execute('UPDATE tasks SET note=? WHERE id=?', (RECEIPT, task['id']))
        task_blocked.sweep(c, datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z')
    assert step(api, task) == 'In Progress'


def test_a_retry_that_another_wait_keeps_on_blocked_claims_no_move_and_waits_for_a_change(api):
    """A gate hold with another wait beside it (here a person's child task) stays Blocked when Tico retries the move:
    that is no move, so Tico says nothing and does not try again until something changes."""
    task = ticket(api)
    assert move(api, task).status_code == 422
    with api.app.state.store.transaction() as c:
        child = H.task_create(c, 'human:ana', 'Choose a machine', 'Choose one.', 'human:ana', parent_id=task['id'])
        c.execute('UPDATE tasks SET note=? WHERE id=?', (RECEIPT, task['id']))

    def claimed():
        with api.app.state.store.read() as c:
            return c.execute("SELECT count(*) FROM messages WHERE body LIKE 'Tico moved this ticket%'").fetchone()[0]
    for _ in range(3):
        assert tick(api) == []
    assert step(api, task) == 'Blocked' and claimed() == 0
    assert len(retries(api, task)) == 2 and retries(api, task)[-1]['state'] == 'waiting'
    # The other wait ends: that is a change, and the remembered move is made.
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'human:ana', child['id'], status='done')
    assert tick(api) == [task['id']] and step(api, task) == 'Review 1' and claimed() == 1
