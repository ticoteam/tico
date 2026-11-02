"""A refused review move is retried by Tico once what refused it clears (Dev 534 C, criterion 8)."""
import json

import pytest

from backend import hubdb as H, move_retry
from backend.tests.test_github import api, get, headers  # noqa: F401
from backend.tests.test_review_gate import ticket, move, HEAD, BASE
from backend.tests.test_api import hold

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
    assert tick(api) == [] and step(api, task) == 'In Progress'
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
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.checks',json(?)) WHERE task_id=? AND kind='pr'",
                  (json.dumps({'python': 'failing'}), task['id']))
    refused = move(api, task)
    assert refused.status_code == 422 and 'Tico retries this move itself when the checks' in refused.text
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.checks',json(?)) WHERE task_id=? AND kind='pr'",
                  (json.dumps({'python': 'passing'}), task['id']))
    assert tick(api) == [task['id']] and step(api, task) == 'Review 1'


def test_a_retry_still_refused_waits_for_the_next_change_and_stops_when_the_ticket_moves(api):
    task = ticket(api)
    assert move(api, task).status_code == 422
    with api.app.state.store.transaction() as c:   # a change, but not a receipt
        c.execute('UPDATE tasks SET note=? WHERE id=?', ('Still building the fix.', task['id']))
    assert tick(api) == [] and step(api, task) == 'In Progress'
    tries = retries(api, task)
    assert len(tries) == 2 and tries[-1]['tries'] == 1 and tries[-1]['state'] == 'waiting'
    assert tick(api) == [] and len(retries(api, task)) == 2      # same inputs: not tried again
    # The builder moves it elsewhere: the remembered move is dropped even when a receipt then arrives.
    fresh = get(api, 'tasks/' + task['id'])['task']
    with api.app.state.store.transaction() as c:
        H.task_update(c, H.KEEPER, task['id'], step='Done')
        c.execute('UPDATE tasks SET note=? WHERE id=?', (RECEIPT, task['id']))
    assert fresh['step']['name'] == 'In Progress'
    assert tick(api) == [] and step(api, task) == 'Done'


def test_hint_names_what_clears_each_refusal():
    assert 'checks on the current head pass' in move_retry.hint('ci', 'CI failing at head x')
    assert 'GitHub can be read again' in move_retry.hint('ci', 'cannot verify the current head and CI for x')
    assert move_retry.hint('other') == ''


def unreadable(api, monkeypatch, mode, response=None):
    """GitHub cannot be read at a review move through the real snapshot and refusal path. `mode` is what the
    read reports: 'temporary' (the token mint times out), 'permission' (the mint is refused for access), or a
    read failure with no cause ('read-422', 'read-boom'). Only the HTTP boundary is stubbed; snapshot,
    problem, reject and remember all run production code. Returns a switch that makes GitHub readable again."""
    from types import SimpleNamespace
    from backend.store import Problem
    state = {'down': True}
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('devops-engineer','devops-engineer','DevOps','active')")
        c.execute("INSERT INTO bot_config(bot,config,operator) VALUES('devops-engineer','{\"runtime\":\"fake\"}','ana')")
    service = api.app.state.github_app
    # A configured app row: truthy for the gate's snapshot, mapping-shaped for link refreshes.
    monkeypatch.setattr(service, 'row', lambda c=None: {"org": "ticoteam"})
    real_mint = service.mint
    if mode == 'mint-403':
        monkeypatch.setattr(service, 'installation', lambda: 77)
        monkeypatch.setattr(service, '_jwt', lambda: 'synthetic-jwt')

    def mint(repos, scopes):
        if state['down'] and mode == 'temporary':
            raise TimeoutError('synthetic GitHub timeout')
        if state['down'] and mode == 'permission':
            raise Problem('github_app_permissions', 'synthetic permission refusal', 409)
        if state['down'] and mode == 'mint-403':
            return real_mint(repos, scopes)
        return ('synthetic-token', '2030-01-01T00:00:00Z')

    def call(method, path, **kw):
        if state['down'] and (mode == 'mint-403' and path.endswith('/access_tokens')
                              or mode == 'read-403' and path.startswith('/repos/')):
            return response
        if state['down'] and mode == 'read-422':
            return SimpleNamespace(status_code=422, json=lambda: {})
        if state['down'] and mode == 'read-boom':
            raise RuntimeError('synthetic transport failure')
        if 'check-runs' in path:
            payload = {'check_runs': []}
        elif 'statuses' in path:
            payload = []
        else:
            payload = {'head': {'sha': HEAD}}
        return SimpleNamespace(status_code=200, json=lambda: payload)

    monkeypatch.setattr(service, 'mint', mint)
    monkeypatch.setattr(service, '_call', call)
    return lambda: state.update(down=False)


def later(minutes):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def devops_tasks(api):
    return owned_by(api, 'devops-engineer')


def owner_woken(api, task):
    """Per gate notice to the ticket's owner, whether it queued a turn for them."""
    with api.app.state.store.read() as c:
        return [bool(r[0]) for r in c.execute(
            "SELECT (SELECT count(*) FROM jobs j WHERE j.message_id=m.id) FROM messages m WHERE m.to_actor=? "
            "AND json_extract(m.refs,'$.task')=? AND json_extract(m.refs,'$.review_gate') IS NOT NULL",
            (task['owner'], task['id']))]


def test_temporary_github_failure_retries_on_the_timer_and_never_asks_devops(api, monkeypatch):
    task = ticket(api, RECEIPT)
    back = unreadable(api, monkeypatch, 'temporary')
    assert move(api, task).status_code == 422
    assert move_retry.tick(api.app.state.github_app, at=later(1), force=True) == []   # inside the 5 minutes
    for minutes in (6, 12):
        assert move_retry.tick(api.app.state.github_app, at=later(minutes), force=True) == []
    assert step(api, task) == task['step']['name'] and devops_tasks(api) == []
    assert owner_woken(api, task) == [False]        # told on the ticket, not woken: Tico retries, nobody acts
    remembered = retries(api, task)[-1]
    assert remembered['temporary'] is True and remembered['devops'] is False
    back()
    assert move_retry.tick(api.app.state.github_app, at=later(18), force=True) == [task['id']]
    assert step(api, task) == 'Review 1' and devops_tasks(api) == []


def test_permission_failure_keeps_its_step_without_escalation_and_moves_when_github_reads_again(api, monkeypatch):
    task = ticket(api, RECEIPT)
    back = unreadable(api, monkeypatch, 'permission')
    assert move(api, task).status_code == 422
    assert devops_tasks(api) == []    # on the first refusal
    assert owner_woken(api, task) == [False]
    assert retries(api, task)[0]['devops'] is True
    fresh = get(api, 'tasks/' + task['id'])['task']
    assert move(api, fresh).status_code == 422                                # the builder tries again
    assert move_retry.tick(api.app.state.github_app, at=later(6), force=True) == []
    assert len(devops_tasks(api)) == 0                                        # the same ask, reused
    back()
    assert move_retry.tick(api.app.state.github_app, at=later(12), force=True) == [task['id']]
    assert step(api, task) == 'Review 1'


@pytest.mark.parametrize('mode', ['mint-403', 'read-403'])
@pytest.mark.parametrize('response_headers,message,temporary', [
    pytest.param({'X-RateLimit-Remaining': '0'}, 'Permission unavailable', True, id='primary-limit'),
    pytest.param({'Retry-After': '60'}, 'Try later', True, id='secondary-retry-after'),
    pytest.param({}, 'API RATE LIMIT exceeded', True, id='secondary-rate-message'),
    pytest.param({}, 'ABUSE detection triggered', True, id='secondary-abuse-message'),
    pytest.param({}, 'Resource not accessible', False, id='plain-forbidden'),
])
def test_github_403_rate_limits_retry_quietly_but_plain_403_needs_access(
        api, monkeypatch, mode, response_headers, message, temporary):
    import httpx
    task = ticket(api, RECEIPT)
    response = httpx.Response(403, headers=response_headers, json={'message': 'private-remote-detail: ' + message})
    back = unreadable(api, monkeypatch, mode, response)
    refused = move(api, task)
    assert refused.status_code == 422, refused.text
    assert 'private-remote-detail' not in refused.text
    remembered = retries(api, task)[-1]
    assert remembered['temporary'] is temporary
    assert remembered['devops'] is not temporary
    assert owner_woken(api, task) == [False]
    assert len(devops_tasks(api)) == 0
    assert move_retry.tick(api.app.state.github_app, at=later(1), force=True) == []
    assert len(retries(api, task)) == 1
    for minutes in (6, 12):
        assert move_retry.tick(api.app.state.github_app, at=later(minutes), force=True) == []
    assert len(retries(api, task)) == 3
    assert len(devops_tasks(api)) == 0
    assert owner_woken(api, task) == [False]
    back()
    assert move_retry.tick(api.app.state.github_app, at=later(18), force=True) == [task['id']]
    assert step(api, task) == 'Review 1'


@pytest.mark.parametrize('mode', ['read-422', 'read-boom'], ids=['answered-422', 'transport-boom'])
def test_unreadable_without_a_temporary_cause_stays_quiet_when_it_repeats(api, monkeypatch, mode):
    task = ticket(api, RECEIPT)
    unreadable(api, monkeypatch, mode)
    assert move(api, task).status_code == 422
    assert devops_tasks(api) == []
    assert move_retry.tick(api.app.state.github_app, at=later(6), force=True) == []
    assert devops_tasks(api) == []


@pytest.mark.parametrize('record', ['unresolved', 'no-head'])
def test_a_pr_link_tico_cannot_resolve_is_never_routed_as_a_github_outage(api, monkeypatch, record):
    """An unresolved link or a link with no recorded head is Tico's own record: relinking clears it, so it never
    asks DevOps, however long it stays refused."""
    task = ticket(api, RECEIPT)
    unreadable(api, monkeypatch, 'read-422')
    if record == 'unresolved':
        sql = "UPDATE task_links SET number=NULL WHERE task_id=? AND kind='pr'"
    else:
        # No GitHub App configured: the gate reads the cached link, which has no head yet.
        monkeypatch.setattr(api.app.state.github_app, 'row', lambda c=None: None)
        sql = "UPDATE task_links SET detail=json_remove(detail,'$.head_sha') WHERE task_id=? AND kind='pr'"
    with api.app.state.store.transaction() as c:
        c.execute(sql, (task['id'],))
    refused = move(api, task)
    assert refused.status_code == 422 and 'cannot verify the current head and CI' in refused.text, refused.text
    assert retries(api, task)[0]['local'] is True
    assert move_retry.tick(api.app.state.github_app, at=later(6), force=True) == []
    assert move_retry.tick(api.app.state.github_app, at=later(12), force=True) == []
    assert len(retries(api, task)) == 3 and devops_tasks(api) == []


def test_the_install_owners_receipt_counts_on_ticos_retry(api):
    """Tico's retry counts the install owner's receipt as a person's move does, owner set only in the environment."""
    task = ticket(api)
    assert move(api, task).status_code == 422
    api.app.state.store.settings.owner_email = 'editor@acme.example'
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='owner'")
        c.execute("INSERT INTO humans(id,name,email,teams) VALUES('editor','Editor','editor@acme.example','[]')")
        H.task_comment(c, 'human:editor', task['id'], RECEIPT, wake=False)
    assert tick(api) == [task['id']] and step(api, task) == 'Review 1'


def test_a_retry_that_raises_waits_five_minutes_before_reading_github_again(api, monkeypatch):
    """A retry that raises instead of being refused must not re-read GitHub on every pass: it waits like a
    refused outage retry. (`snapshot` raising here simulates the crash; the cause path is covered above.)"""
    from backend import review_gate
    task = ticket(api, RECEIPT)
    unreadable(api, monkeypatch, 'temporary')
    assert move(api, task).status_code == 422
    calls = []

    def snapshot(service, prs):
        calls.append(1)
        raise RuntimeError('synthetic snapshot crash')

    monkeypatch.setattr(review_gate, 'snapshot', snapshot)
    assert move_retry.tick(api.app.state.github_app, at=later(6), force=True) == []
    assert len(calls) == 1 and step(api, task) == task['step']['name']
    assert tick(api) == [] and len(calls) == 1     # still inside the five minutes: no re-read
    assert devops_tasks(api) == []


def test_a_permission_failure_after_a_temporary_one_asks_devops_once(api, monkeypatch):
    """The first refusal was temporary; a later retry is refused for access. That retry asks DevOps, and the
    retries after it reuse the ask."""
    from backend.store import Problem
    task = ticket(api, RECEIPT)
    unreadable(api, monkeypatch, 'temporary')
    assert move(api, task).status_code == 422
    assert devops_tasks(api) == []

    def refused(repos, scopes):
        raise Problem('github_app_permissions', 'synthetic permission refusal', 409)

    monkeypatch.setattr(api.app.state.github_app, 'mint', refused)
    assert move_retry.tick(api.app.state.github_app, at=later(6), force=True) == []
    assert devops_tasks(api) == []
    assert move_retry.tick(api.app.state.github_app, at=later(12), force=True) == []
    assert move_retry.tick(api.app.state.github_app, at=later(18), force=True) == []
    assert len(retries(api, task)) == 4 and devops_tasks(api) == []


def test_a_retry_that_raises_on_a_receipt_refusal_waits_five_minutes_then_moves(api, monkeypatch):
    """A refusal that is not an outage is due when its inputs change; a retry of it that raises must still wait
    before it is tried again, not stay due on every pass."""
    from backend import review_gate
    task = ticket(api)
    assert move(api, task).status_code == 422
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET note=? WHERE id=?', (RECEIPT, task['id']))
    real, calls = review_gate.snapshot, []

    def crash(service, prs):
        calls.append(1)
        raise RuntimeError('synthetic snapshot crash')

    monkeypatch.setattr(review_gate, 'snapshot', crash)
    assert move_retry.tick(api.app.state.github_app, at=later(1), force=True) == [] and len(calls) == 1
    assert move_retry.tick(api.app.state.github_app, at=later(5), force=True) == [] and len(calls) == 1
    monkeypatch.setattr(review_gate, 'snapshot', real)
    assert move_retry.tick(api.app.state.github_app, at=later(7), force=True) == [task['id']]
    assert step(api, task) == 'Review 1'


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
    assert step(api, task) == 'In Progress'


def test_a_private_tickets_outage_does_not_create_a_task_or_wake_a_manager(api, monkeypatch):
    from backend import source_outages
    task = ticket(api, RECEIPT)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET private=1 WHERE id=?', (task['id'],))
    unreadable(api, monkeypatch, 'permission')
    assert move(api, task).status_code == 422
    with api.app.state.store.transaction() as c:
        source_outages.sync(c, api.app.state.store.settings)
        assert not c.execute("SELECT 1 FROM tasks WHERE owner=?", (H.bot_actor('devops-engineer'),)).fetchone()
        assert not c.execute("SELECT 1 FROM events WHERE action='blocker.routed'").fetchone()
        for row in c.execute("SELECT body,refs FROM messages WHERE json_extract(refs,'$.source_outage') IS NOT NULL"):
            assert task['id'] not in row['body'] + row['refs']
            assert task['title'] not in row['body'] + row['refs']
    assert not any(owner_woken(api, task))


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
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster['people'].append({'id': 'zed', 'email': 'zed@acme.example'})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
        c.execute("UPDATE bot_config SET reports_to='human:zed' WHERE bot='cpo'")
        child = H.task_create(c, H.bot_actor('cpo'), 'Evidence', 'x', H.bot_actor('cpo'), parent_id=task['id'])
        c.execute("UPDATE tasks SET private=1, title='Review 1' WHERE id=?", (child['id'],))
        H._write_message(c, H.bot_actor('cpo'), H.bot_actor('cpo'), RECEIPT, H.conversation(c, child['conversation_id']),
                         'say', {'task': child['id'], 'comment': True}, None, None)
    assert move(api, task).status_code == 422           # ana cannot read it
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET note='unrelated' WHERE id=?", (task['id'],))
    assert tick(api) == [] and step(api, task) == 'In Progress'
    assert retries(api, task)[-1]['by'] == 'human:ana'  # a refused retry keeps the mover it reads as


def test_a_refused_move_is_retried_as_the_actor_it_was_made_as_not_the_caller(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:      # BotOps calls as itself, moving for ana
        with pytest.raises(H.Refused) as refused:
            H.task_update(c, 'human:ana', task['id'], step='Review 1', review_prs={})
        move_retry.remember(c, H.bot_actor('botops'), refused.value.review_gate)
    assert retries(api, task)[-1]['by'] == 'human:ana'


def test_a_refused_move_held_is_retried_even_when_the_blocked_sweep_runs_first(api):
    """A refused move holds the ticket where it is (backend/task_blocked.py). When what refused it clears, the move is
    made, not undone: the blocked sweep must not send it back to its earlier step and drop the remembered move."""
    from backend import task_blocked
    task = ticket(api)
    assert move(api, task).status_code == 422
    assert step(api, task) == 'In Progress'
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET note=? WHERE id=?', (RECEIPT, task['id']))
        task_blocked.sweep(c, H.now())
    assert step(api, task) == 'In Progress'
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
    """A gate hold with another wait beside it (here a person's child task) stays held when Tico retries the move:
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
    assert step(api, task) == 'In Progress' and claimed() == 0
    assert len(retries(api, task)) == 2 and retries(api, task)[-1]['state'] == 'waiting'
    # The other wait ends: that is a change, and the remembered move is made.
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'human:ana', child['id'], status='done')
    assert tick(api) == [task['id']] and step(api, task) == 'Review 1' and claimed() == 1


def general(api, task):
    """The ticket moved back to the General board, so a review move is a move onto another board."""
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'human:ana', task['id'], type=H.GENERAL_TYPE, status='doing')
    return get(api, 'tasks/' + task['id'])['task']


def cross_move(api, task, review_step='Review 1'):
    return api.post('/api/v2/tasks/' + task['id'], headers=headers(),
                    json={'version': get(api, 'tasks/' + task['id'])['task']['version'], 'type': 'Dev tickets',
                          'step': review_step})


@pytest.mark.parametrize('review_step', ['Review 1', 'Review 2'])
def test_a_refused_move_onto_another_board_is_retried_onto_that_board(api, review_step):
    """Its step names exist only on the destination board: a retry that names the step alone is refused there."""
    task = general(api, ticket(api))
    assert cross_move(api, task, review_step).status_code == 422
    held = get(api, 'tasks/' + task['id'])['task']
    assert held['type_id'] == H.GENERAL_TYPE and hold(held)['since']
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET note=? WHERE id=?', (RECEIPT, task['id']))
    assert tick(api) == [task['id']]
    moved = get(api, 'tasks/' + task['id'])['task']
    assert moved['type_id'] == task_type(api) and moved['step']['name'] == review_step
    assert not hold(moved)


def task_type(api):
    with api.app.state.store.read() as c:
        return H.type_get(c, 'Dev tickets')['id']


@pytest.mark.parametrize('review_step', ['Review 1', 'Review 2'])
def test_the_stranded_sweep_releases_another_boards_gate_hold_only_once_no_retry_will(api, review_step):
    """Only engineering tickets have reconciliation; on another board the retry is what clears a gate hold, so the
    hourly sweep leaves it while Tico still retries and releases it after, or it would be held forever."""
    from datetime import datetime, timedelta, timezone
    task = general(api, ticket(api))
    assert cross_move(api, task, review_step).status_code == 422
    stamp = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
    with api.app.state.store.transaction() as c:
        assert H.sweep_stranded(c, stamp) == []     # past the sweep's grace, inside the retry's
        c.execute("UPDATE task_events SET new=json_set(new,'$.since','2020-01-01T00:00:00.000000Z') "
                  "WHERE task_id=? AND field='move_retry'", (task['id'],))
        assert H.sweep_stranded(c, stamp) == [(task['id'], 'open')]
    released = get(api, 'tasks/' + task['id'])['task']
    assert released['status'] == 'open' and not hold(released)
