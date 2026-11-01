"""Recovery waits have an owner and only established empty fallback results wait for a reset."""
import json

import pytest
from backend import login_holds
from backend.store import H
from backend.scheduler import Scheduler
from backend.tests.test_api import api, post, get, claim, ready, setup_attempt
from backend.tests.test_execution_review import (add_recovery_reviewer, restarted_mid_turn, rival, sweep, job_state)
from backend.tests.test_login_holds import at, signed_in, two_bots, limit, events, health_detail


@pytest.mark.parametrize('identity', ['task', 'lane', 'main'])
def test_overlap_wait_is_visible_and_reviewed_within_five_minutes_of_freeing(api, monkeypatch, identity):
    add_recovery_reviewer(api)
    machine, message, attempt = restarted_mid_turn(api, [('Bash', None)], restart=False)
    other = rival(api, machine, attempt)
    with api.app.state.store.transaction() as c:
        if identity in ('lane', 'main'):
            lane = 'main' if identity == 'main' else 'task:shared'
            c.execute("UPDATE attempts SET lane=? WHERE id IN (?,?)", (lane, attempt['id'], other['id']))
        else:
            task = H.task_create(c, 'human:ana', 'Shared work', 'Finish this.', 'bot:ops')
            c.execute('UPDATE turns SET task_id=? WHERE id IN (?,?)', (task['id'], attempt['id'], other['id']))
            c.execute('UPDATE messages SET refs=? WHERE id=?', (json.dumps({'task': task['id']}), message['id']))
    assert sweep(api) == []
    with api.app.state.store.read() as c:
        mark = c.execute("SELECT detail FROM events WHERE action='job.recovery_wait' AND target=?", (attempt['id'],)).fetchone()
        assert json.loads(mark[0])['blocking_attempt'] == other['id']
        assert json.loads(mark[0])['owner'] == H.KEEPER
    assert 'Tico owns recovery' in get(api, 'bots/ops/execution-review')['jobs'][0]['reason']
    assert sweep(api) == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='job.recovery_wait' AND target=?", (attempt['id'],)).fetchone()[0] == 1
    freed = H.now()
    post(api, f"attempts/{other['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Finished.'}, machine['token'])
    monkeypatch.setattr(H, 'now', lambda: H.shift(freed, seconds=30))
    Scheduler(api.app.state.store, api.app.state.execution).tick()
    assert job_state(api, attempt['job_id']) == 'uncertain'
    with api.app.state.store.read() as c:
        held = json.loads(c.execute("SELECT detail FROM events WHERE action='job.held' AND target=?", (attempt['id'],)).fetchone()[0])
        assert held['owner'] == 'bot:botops' and held['reason']
        assert c.execute("SELECT 1 FROM tasks WHERE owner='bot:botops' AND status='open'").fetchone()
    assert 'BotOps owns recovery' in get(api, 'bots/ops/execution-review')['jobs'][0]['reason']


@pytest.mark.parametrize('ready_fallback', [False, True])
def test_known_reset_keeps_new_person_messages_queued_or_uses_ready_fallback(api, monkeypatch, ready_fallback):
    machine = two_bots(api, monkeypatch)
    first_message = post(api, 'chat/ops', {'text': 'First request'})
    first = claim(api, machine, bot='ops')
    limit(api, machine, first, errors=('Usage limit',), limit_resets_at='2026-10-19T02:40:00Z')
    at(monkeypatch, '2026-10-19T02:20:00.000000Z', api)
    signed_in(api, machine, ['ops', 'finance'], ['codex'] if ready_fallback else [])
    post(api, 'chat/ops', {'text': 'A new request does not reset a known provider hold'})
    if ready_fallback:
        fallback = claim(api, machine, bot='ops')
        assert fallback and fallback['config']['primary_held']
        return
    assert claim(api, machine, bot='ops') is None
    assert claim(api, machine, bot='ops') is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE bot='ops'").fetchone()[0] == 1
    at(monkeypatch, '2026-10-19T02:40:00.000000Z', api)
    signed_in(api, machine, ['ops', 'finance'], [])
    assert claim(api, machine, bot='ops')


@pytest.mark.parametrize('evidence', ['empty', 'output', 'tool', 'unknown', 'approval', 'tokens', 'missing_terminal', 'unknown_tokens', 'yielded_output', 'usage'])
def test_interrupted_fallback_waits_for_reset_only_with_positive_empty_evidence(api, monkeypatch, evidence):
    machine = two_bots(api, monkeypatch)
    post(api, 'chat/ops', {'text': 'Work to finish'})
    first = claim(api, machine, bot='ops')
    limit(api, machine, first, errors=('Usage limit',), limit_resets_at='2026-10-19T02:40:00Z')
    at(monkeypatch, '2026-10-19T02:20:00.000000Z', api)
    signed_in(api, machine, ['ops', 'finance'], ['codex'])
    fallback = claim(api, machine, bot='ops')
    assert fallback['config']['primary_held']
    post(api, f"attempts/{fallback['id']}/started", {'thread_id': 'fallback'}, machine['token'])
    rows = [('status', {'tool_events': evidence != 'unknown'})]
    if evidence in ('output', 'yielded_output'):
        rows.append(('delta', {'text': 'Partial work'}))
    if evidence == 'tool':
        rows.append(('tool', {'tool': 'mcp__hub__hub_webhook_send', 'status': 'started'}))
    if evidence == 'tokens':
        rows.append(('tokens', {'output': 4}))
    if evidence == 'unknown_tokens':
        rows.append(('tokens', {'input': 5}))
    if evidence not in ('unknown', 'missing_terminal'):
        rows.append(('diagnostic', {'interruption_complete': True}))
    if evidence == 'approval':
        with api.app.state.store.transaction() as c:
            aid = H.new_id()
            c.execute('INSERT INTO approvals(id,kind,message_id,requested_by,created_at,consumed_at) VALUES(?,?,?,?,?,?)',
                      (aid, 'spend', fallback['message']['id'], 'bot:ops', H.now(), H.now()))
            H.event(c, 'bot:ops', 'approval.consume', aid, {'kind': 'spend', 'attempt_id': fallback['id']})
    post(api, f"attempts/{fallback['id']}/events", {'events': [
        {'seq': n, 'kind': kind, 'payload': payload} for n, (kind, payload) in enumerate(rows, 1)]}, machine['token'])
    post(api, f"attempts/{fallback['id']}/complete", {'outcome': 'interrupted', 'last_seq': len(rows),
         'primary_held': True, 'fallback': 'codex', 'text': '',
         'yielded': evidence == 'yielded_output',
         **({'tokens_out': 3} if evidence == 'usage' else {})}, machine['token'])
    expected = 'queued' if evidence == 'empty' else 'uncertain'
    assert job_state(api, fallback['job_id']) == expected
    if evidence != 'empty':
        # Complete telemetry without effects is not enough if it contains output or lacks a terminal receipt.
        sweep(api)
        assert job_state(api, fallback['job_id']) == 'uncertain'
        return
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    assert claim(api, machine, bot='ops') is None
    with api.app.state.store.read() as c:
        detail = json.loads(c.execute("SELECT detail FROM events WHERE action='job.reset_wait' AND target=?", (fallback['job_id'],)).fetchone()[0])
        assert detail['until'].startswith('2026-10-19T02:40:00')
    at(monkeypatch, '2026-10-19T02:40:00.000000Z', api)
    Scheduler(api.app.state.store, api.app.state.execution).tick()
    signed_in(api, machine, ['ops', 'finance'], ['codex'])
    resumed = claim(api, machine, bot='ops')
    assert resumed and not resumed['config'].get('primary_held')


def test_shell_run_ignores_another_runs_task_merge(api):
    machine, message, attempt = restarted_mid_turn(api, [('Bash', None)])
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, 'human:ana', 'Shared task', 'Finish.', 'bot:ops')
        c.execute('UPDATE turns SET task_id=? WHERE id=?', (task['id'], attempt['id']))
        c.execute('INSERT INTO task_links(id,task_id,kind,url,created_at,pr_merged_at) VALUES(?,?,?,?,?,?)',
                  (H.new_id(), task['id'], 'pr', 'https://example.com/acme/app/pull/9', H.now(), H.now()))
    assert [d['state'] for d in sweep(api)] == ['queued']


@pytest.mark.parametrize('this_attempt', [False, True])
def test_approval_consumer_attempt_overrides_request_message_association(api, this_attempt):
    machine, message, attempt = restarted_mid_turn(api, [('Bash', None)])
    with api.app.state.store.transaction() as c:
        approval = H.new_id()
        c.execute('INSERT INTO approvals(id,kind,message_id,requested_by,created_at,consumed_at) VALUES(?,?,?,?,?,?)',
                  (approval, 'spend', 'prior-request' if this_attempt else message['id'], 'bot:ops', H.now(), H.now()))
        token = H.RUN.set(attempt['id'] if this_attempt else 'different-attempt')
        try:
            H.event(c, 'bot:ops', 'approval.consume', approval, {'kind': 'spend'})
        finally:
            H.RUN.reset(token)
    result = sweep(api)
    assert job_state(api, attempt['job_id']) == ('uncertain' if this_attempt else 'queued')


@pytest.mark.parametrize('named_fallback,release', [(False, 'reset'), (True, 'reset'), (True, 'try-now')])
def test_no_fallback_ready_for_claim_persists_one_hold_until_named_reset(api, monkeypatch, named_fallback, release):
    """Oct 26 Lead regression: zero writes, primary held to 11:50, fallback unavailable for this claim."""
    from backend import wake_presets

    machine = two_bots(api, monkeypatch)
    at(monkeypatch, '2026-10-26T10:53:00.000000Z', api)
    if named_fallback:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE bot_config SET config=json_set(config,'$.fallback',json(?)) WHERE bot='ops'",
                      (json.dumps({'harness': 'codex', 'model': 'gpt-5.5'}),))
    for n in range(2):
        post(api, 'chat/ops', {'text': f'Please finish request {n}'})
    first = claim(api, machine, bot='ops')
    limit(api, machine, first, errors=("Model error: You've hit your session limit · resets 11:50am (UTC)",))
    at(monkeypatch, '2026-10-26T10:54:00.000000Z', api)
    signed_in(api, machine, ['ops', 'finance'], ['codex'])
    fallback = claim(api, machine, bot='ops')
    assert fallback['config']['primary_held'].startswith('2026-10-26T11:50:00')
    # Readiness was good globally; this claim cannot use it. No /started, tool, write or model output.
    seq = events(api, machine, fallback, [('error', 'fake login held (usage limit) until 11:50 UTC, '
                                          'and no fallback is ready on this computer')])
    post(api, f"attempts/{fallback['id']}/complete", {'outcome': 'failed', 'last_seq': seq,
         'limited': True, 'primary_held': True, 'text': ''}, machine['token'])
    with api.app.state.store.read() as c:
        job = c.execute('SELECT state,held,hold_until FROM jobs WHERE id=?', (fallback['job_id'],)).fetchone()
        assert tuple(job) == ('queued', 1, '2026-10-26T11:50:00.000000Z')
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key LIKE 'login-hold:%'").fetchone()[0] == 1
    assert '11:50' in health_detail(api, 'ops')
    for when in ('10:55', '11:07', '11:49'):
        at(monkeypatch, f'2026-10-26T{when}:00.000000Z', api)
        # A changed profile/wake preset must not replace a quota reset with its news-batch delay.
        with api.app.state.store.transaction() as c:
            wake_presets.replan(c, 'ops')
        Scheduler(api.app.state.store, api.app.state.execution).tick()
        signed_in(api, machine, ['ops', 'finance'], ['codex'])
        assert claim(api, machine, bot='ops') is None
        with api.app.state.store.read() as c:
            assert c.execute("SELECT count(*) FROM attempts WHERE bot='ops'").fetchone()[0] == 2
            assert tuple(c.execute('SELECT held,hold_until FROM jobs WHERE id=?',
                                   (fallback['job_id'],)).fetchone()) == (1, '2026-10-26T11:50:00.000000Z')
    if release == 'try-now':
        post(api, 'bots/ops/limit/retry', {})
    else:
        at(monkeypatch, '2026-10-26T11:50:01.000000Z', api)
    Scheduler(api.app.state.store, api.app.state.execution).tick()
    signed_in(api, machine, ['ops', 'finance'], ['codex'])
    resumed = claim(api, machine, bot='ops')
    assert resumed and resumed['job_id'] == fallback['job_id']
    assert not resumed['config'].get('primary_held')
    assert claim(api, machine, bot='ops') is None  # one probe, never a burst at reset


def test_missing_fallback_setup_is_not_a_capacity_or_quota_error():
    from backend import run_backoff
    from datetime import datetime, timezone

    error = 'No fallback configured on this computer; finish fallback setup before retrying.'
    assert not run_backoff.is_limit(error)
    assert not login_holds.capacity(error)
    assert login_holds.reset_from_text(error, datetime(2026, 10, 26, 11, tzinfo=timezone.utc)) is None


def test_fallbacks_own_named_reset_holds_only_that_login_and_is_visible(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    at(monkeypatch, '2026-10-26T10:53:00.000000Z', api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.fallback',json(?)) WHERE bot='ops'",
                  (json.dumps({'harness': 'codex', 'model': 'gpt-5.5'}),))
    post(api, 'chat/ops', {'text': 'Finish the queued work'})
    first = claim(api, machine, bot='ops')
    limit(api, machine, first, errors=('Usage limit',), limit_resets_at='2026-10-26T12:50:00Z')
    at(monkeypatch, '2026-10-26T10:54:00.000000Z', api)
    signed_in(api, machine, ['ops', 'finance'], ['codex'])
    fallback = claim(api, machine, bot='ops')
    assert fallback['config']['primary_held'].startswith('2026-10-26T12:50:00')
    limit(api, machine, fallback,
          errors=("Model error: You've hit your session limit · resets 11:50am (UTC)",),
          fallback='codex', primary_held=True)
    assert job_state(api, fallback['job_id']) == 'queued'
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    assert '11:50' in health_detail(api, 'ops')
    with api.app.state.store.read() as c:
        assert login_holds.get(c, machine['runner_id'], '', 'codex')['until'].startswith('2026-10-26T11:50:00')
        assert login_holds.get(c, machine['runner_id'], '', 'fake')['until'].startswith('2026-10-26T12:50:00')
    at(monkeypatch, '2026-10-26T11:49:00.000000Z', api)
    signed_in(api, machine, ['ops', 'finance'], ['codex'])
    assert claim(api, machine, bot='ops') is None
    at(monkeypatch, '2026-10-26T11:50:01.000000Z', api)
    signed_in(api, machine, ['ops', 'finance'], ['codex'])
    resumed = claim(api, machine, bot='ops')
    assert resumed and resumed['job_id'] == fallback['job_id']
    assert resumed['config']['primary_held'].startswith('2026-10-26T12:50:00')


OBSERVED_LIMIT = "Model error: You've hit your session limit · resets 11:50am (UTC)"


@pytest.mark.parametrize('writes', [False, True])
@pytest.mark.parametrize('notice,named', [
    (OBSERVED_LIMIT, True),
    ("Model error: You've hit your session limit · resets soon", False),
])
def test_fallback_limit_reason_on_job_health_and_review(api, monkeypatch, writes, notice, named):
    machine = two_bots(api, monkeypatch)
    at(monkeypatch, '2026-10-26T10:53:00.000000Z', api)
    post(api, 'chat/ops', {'text': 'Finish the queued work'})
    first = claim(api, machine, bot='ops')
    limit(api, machine, first, errors=('Usage limit',), limit_resets_at='2026-10-26T12:50:00Z')
    at(monkeypatch, '2026-10-26T10:54:00.000000Z', api)
    signed_in(api, machine, ['ops', 'finance'], ['codex'])
    fallback = claim(api, machine, bot='ops')
    post(api, f"attempts/{fallback['id']}/started", {'thread_id': 'fallback'}, machine['token'])
    rows = [('status', {'tool_events': True})]
    if writes:
        rows.append(('tool', {'tool': 'mcp__hub__hub_webhook_send', 'status': 'started'}))
    rows += [('error', {'error': notice, 'limit': True}),
             ('diagnostic', {'interruption_complete': True})]
    post(api, f"attempts/{fallback['id']}/events", {'events': [
        {'seq': n, 'kind': kind, 'payload': payload} for n, (kind, payload) in enumerate(rows, 1)]}, machine['token'])
    post(api, f"attempts/{fallback['id']}/complete", {
        'outcome': 'interrupted' if writes else 'failed', 'last_seq': len(rows),
        'limited': True, 'primary_held': True, 'fallback': 'codex', 'text': ''}, machine['token'])
    assert job_state(api, fallback['job_id']) == ('uncertain' if writes else 'queued')
    review = get(api, 'bots/ops/execution-review')['jobs']
    if writes:
        assert len(review) == 1
        reason = review[0]['reason']
        assert (notice in reason) == named
        assert ('reset ~11:50 UTC' in reason) == named
    else:
        assert review == []
        from backend import views
        with api.app.state.store.read() as c:
            job = views.conversation_execution(c, first['message']['conversation_id'], settings=api.app.state.store.settings)
        assert job['state'] == 'queued'
        assert ('resumes ~11:50 UTC' in job['label']) == named
        assert ('resumes ~11:50 UTC' in health_detail(api, 'ops')) == named
        if named:
            assert claim(api, machine, bot='ops') is None
            # Try now must clear the visible wait as well as release the login hold.
            post(api, 'bots/ops/limit/retry', {})
            with api.app.state.store.read() as c:
                job = views.conversation_execution(c, first['message']['conversation_id'],
                                                   settings=api.app.state.store.settings)
            assert 'resumes ~11:50 UTC' not in job['label']


@pytest.mark.parametrize('limited', [False, True])
def test_fallback_limit_reason_never_attributes_reply_text_to_provider(api, monkeypatch, limited):
    machine = two_bots(api, monkeypatch)
    at(monkeypatch, '2026-10-26T10:53:00.000000Z', api)
    post(api, 'chat/ops', {'text': 'Finish the queued work'})
    first = claim(api, machine, bot='ops')
    limit(api, machine, first, errors=('Usage limit',), limit_resets_at='2026-10-26T12:50:00Z')
    at(monkeypatch, '2026-10-26T10:54:00.000000Z', api)
    signed_in(api, machine, ['ops', 'finance'], ['codex'])
    fallback = claim(api, machine, bot='ops')
    post(api, f"attempts/{fallback['id']}/started", {'thread_id': 'fallback'}, machine['token'])
    rows = [('status', {'tool_events': True}),
            ('tool', {'tool': 'mcp__hub__hub_webhook_send', 'status': 'started'}),
            ('diagnostic', {'interruption_complete': True})]
    post(api, f"attempts/{fallback['id']}/events", {'events': [
        {'seq': n, 'kind': kind, 'payload': payload} for n, (kind, payload) in enumerate(rows, 1)]}, machine['token'])
    post(api, f"attempts/{fallback['id']}/complete", {
        'outcome': 'interrupted', 'last_seq': len(rows), 'limited': limited,
        'primary_held': True, 'fallback': 'codex', 'text': OBSERVED_LIMIT}, machine['token'])
    assert job_state(api, fallback['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs']
    assert len(review) == 1
    assert 'Provider limit:' not in review[0]['reason']
    assert 'Quota reset ~11:50 UTC' not in review[0]['reason']
    from backend import views
    with api.app.state.store.read() as c:
        job = views.conversation_execution(c, first['message']['conversation_id'], settings=api.app.state.store.settings)
    assert 'resumes ~11:50 UTC' not in job['label']
