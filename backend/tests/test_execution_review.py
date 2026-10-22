"""Operator review preserves evidence and fences stale or unauthorized decisions."""
import json

import pytest
from backend.tests.test_api import api, assign, runner, setup_attempt, post, get, expire, ready, claim, headers


def interrupted(api):
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id':'review-test'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events':[{'seq':1,'kind':'message','payload':{'text':'Saved report; email already sent.'}}]}, machine['token'])
    expire(api, attempt['id'])
    assert claim(api, machine) is None
    return machine, message, attempt


@pytest.mark.parametrize("acknowledge_start", [False, True])
def test_pre_start_retry_limit_fails_unstarted_jobs_and_preserves_running_work(api, monkeypatch, acknowledge_start):
    from backend import execution

    monkeypatch.setattr(execution, "PRE_START_TRIES", 2)
    machine, message, first = setup_attempt(api)
    get(api, "credential-runtime", first["token"])
    expire(api, first["id"])
    ready(api, machine, ["ops"])
    second = post(api, "jobs/claim", {}, token=machine["token"])["attempt"]
    assert second["job_id"] == first["job_id"]
    get(api, "credential-runtime", second["token"])
    if acknowledge_start:
        post(api, f"attempts/{second['id']}/started", {"thread_id": "started-work"}, token=machine["token"])
    expire(api, second["id"])
    ready(api, machine, ["ops"])
    assert post(api, "jobs/claim", {}, token=machine["token"])["attempt"] is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (first["job_id"],)).fetchone()[0] == (
            "uncertain" if acknowledge_start else "failed")
        assert c.execute("SELECT state FROM attempts WHERE id=?", (second["id"],)).fetchone()[0] == (
            "expired" if acknowledge_start else "failed")
    messages = get(api, f"conversations/{message['conversation_id']}/messages")
    replies = [m["body"] for m in messages if m["from_actor"] == "bot:ops"]
    assert replies == ([] if acknowledge_start else ["Your ops couldn't start on Test Mac; check that Computer"])


def test_review_is_private_and_exposes_saved_output_without_credentials(api):
    machine, message, attempt = interrupted(api)
    get(api, 'bots/ops/execution-review', token='ben-test', expected=403)
    get(api, 'bots/ops/execution-review', token=machine['token'], expected=403)
    result = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert result['id'] == attempt['job_id']
    assert result['output'][0]['text'] == 'Saved report; email already sent.'
    assert result['reason'].startswith('The run stopped before it returned an answer.')
    assert result['stopped'] == 'unknown' and result['acted'] == []             # the card leads with "don't retry"
    assert 'token_hash' not in result['attempt'] and 'token' not in result['attempt']


def limited_turn(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {'thread_id':'limited'}, machine['token'])
    return post(api, f"attempts/{attempt['id']}/complete",
                {'outcome':'failed','last_seq':0,'limited':True,'text':"You've hit your usage limit."}, machine['token'])


def test_subscription_quota_holds_until_the_provider_reset_and_try_now_can_lift_it(api, monkeypatch):
    from backend.execution import cooling_off
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    now = H.now()
    reset = H.shift(now, days=4)[:19] + "Z"
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "quota"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/events", {"events": [{"seq": 1, "kind": "error", "payload": {
        "error": f"Subscription quota exhausted. Your usage window resets at {reset}. (rate_limit_error)",
        "limit": True}}]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {
        "outcome": "failed", "last_seq": 1, "limited": True, "text": ""}, machine["token"])
    monkeypatch.setattr(H, "now", lambda: H.shift(now, hours=3))
    ready(api, machine, ["ops"])
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert cooling_off(c, "ops")
        assert reset[:10] in H.status(c, "ops")["focus"]
    monkeypatch.setattr(H, "now", lambda: H.shift(reset, seconds=2))
    with api.app.state.store.read() as c:
        assert not cooling_off(c, "ops")
    monkeypatch.setattr(H, "now", lambda: H.shift(now, hours=3))
    ready(api, machine, ["ops"])
    post(api, "bots/ops/limit/retry", {})
    with api.app.state.store.read() as c:
        assert not cooling_off(c, "ops")


def test_a_reported_reset_holds_the_bot_until_then_and_the_tick_releases_it(api, monkeypatch):
    from backend.execution import cooling_off
    from backend.scheduler import Scheduler
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    assert get(api, "runners/desired", machine["token"])["features"]["limit_resets_at"] is True
    now = H.now()
    # Sooner than the default wait: the provider's reset wins either way.
    reset = H.shift(now, minutes=12)[:19] + "+00:00"
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "reset"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": 0, "limited": True,
         "limit_resets_at": reset, "text": ""}, machine["token"])
    with api.app.state.store.read() as c:
        status = H.status(c, "ops")
        assert status["state"] == "limited" and f"resumes at {reset[11:16]} UTC" in status["focus"]
    monkeypatch.setattr(H, "now", lambda: H.shift(reset, seconds=-30))
    ready(api, machine, ["ops"])
    assert claim(api, machine) is None
    scheduler = Scheduler(api.app.state.store, api.app.state.execution)
    scheduler.tick()
    with api.app.state.store.read() as c:
        assert cooling_off(c, "ops") and H.status(c, "ops")["state"] == "limited"
        seq = c.execute("SELECT coalesce(max(seq),0) FROM changes").fetchone()[0]
    monkeypatch.setattr(H, "now", lambda: H.shift(reset, seconds=2))
    scheduler.tick()
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["state"] == "idle" and not cooling_off(c, "ops")
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "queued"
        # The computer is told to ask for work now, not at its next backup poll.
        assert c.execute("SELECT 1 FROM changes WHERE seq>? AND topic='runner' AND kind='work' AND bot='ops'",
                         (seq,)).fetchone()


@pytest.mark.parametrize("reset", ["days9", "past", "naive"])
def test_an_unbelievable_reported_reset_keeps_the_default_wait(api, reset):
    from backend.execution import LIMIT_COOLDOWN, limit_cooldown
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    now = H.now()
    at = {"days9": H.shift(now, days=9), "past": H.shift(now, minutes=-10), "naive": H.shift(now, hours=1)[:19]}[reset]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "odd-reset"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": 0, "limited": True,
         "limit_resets_at": at, "text": ""}, machine["token"])
    with api.app.state.store.read() as c:
        assert limit_cooldown(c, "ops") == LIMIT_COOLDOWN


def test_an_api_key_quota_refusal_holds_the_bot_and_says_billing_not_subscription(api):
    from backend.execution import cooling_off
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "billing"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/events", {"events": [{"seq": 1, "kind": "error", "payload": {
        "error": "Quota exceeded. Check your plan and billing details.", "limit": True}}]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {
        "outcome": "failed", "last_seq": 1, "limited": True, "text": ""}, machine["token"])
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
        status = H.status(c, 'ops')
        assert status['state'] == 'limited' and 'billing' in status['focus'] and cooling_off(c, 'ops')
    assert claim(api, machine) is None
    issue = next(i for i in api.get('/api/status', headers=headers()).json()['health_issues'] if i['kind'] == 'bot' and i['bot'] == 'ops')
    assert 'billing' in issue['detail'] and 'subscription' not in issue['detail']


def age_limit(api, seconds):
    """Move the bot's limit and every login hold `seconds` into the past, as if that much time went by."""
    import json as _json
    from backend.store import H
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_status SET since=? WHERE bot='ops'", (H.shift(H.status(c, 'ops')['since'], seconds=-seconds),))
        for table, column in (("attempts", "finished"), ("messages", "created")):
            for rowid, at in c.execute(f"SELECT rowid,{column} FROM {table} WHERE {column} IS NOT NULL").fetchall():
                c.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (H.shift(at, seconds=-seconds), rowid))
        for key, value in c.execute("SELECT key,value_json FROM registry_metadata WHERE key LIKE 'login-hold:%'").fetchall():
            hold = _json.loads(value)
            hold.update(since=H.shift(hold['since'], seconds=-seconds), until=H.shift(hold['until'], seconds=-seconds))
            c.execute("UPDATE registry_metadata SET value_json=? WHERE key=?", (_json.dumps(hold), key))


def test_usage_limited_turn_requeues_and_cools_off_longer_after_a_third_strike(api):
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    assert limited_turn(api, machine, attempt)['outcome'] == 'failed'
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
        status = H.status(c, 'ops')
        assert status['state'] == 'limited' and status['focus'].startswith('Primary fake plan usage limit at ')
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.limited' AND target=?", (attempt['id'],)).fetchone()[0] == 1
    issues = api.get('/api/status', headers=headers()).json()['health_issues']
    issue = next(i for i in issues if i['kind'] == 'bot' and i['bot'] == 'ops')
    assert issue['detail'].startswith('Primary fake plan usage limit; resumes at ')
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    # Inside the hold nothing is handed out; after it the same job comes back. Each limit in a row on the login
    # holds longer: 30, then 60, then 120 minutes (backend/login_holds.py BACKOFF).
    assert claim(api, machine) is None
    age_limit(api, 1801)
    second = claim(api, machine)
    assert second and second['job_id'] == attempt['job_id'] and second['id'] != attempt['id']
    limited_turn(api, machine, second)
    with api.app.state.store.transaction() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
        # The second limit restarted the hold even though the state did not change.
        assert H.status(c, 'ops')['since'] > H.shift(H.now(), seconds=-5)
    age_limit(api, 1801)
    assert claim(api, machine) is None                      # the second limit in a row holds an hour
    age_limit(api, 1800)
    third = claim(api, machine)
    assert third and third['job_id'] == attempt['job_id']
    limited_turn(api, machine, third)
    # A third limit in a row never becomes uncertain: the job stays queued behind a longer
    # hold, and Needs attention says when it retries instead of asking for a review.
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
        status = H.status(c, 'ops')
        assert status['state'] == 'limited' and 'third in a row' in status['focus']
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    issue = next(i for i in api.get('/api/status', headers=headers()).json()['health_issues'] if i['kind'] == 'bot' and i['bot'] == 'ops')
    assert issue['detail'].startswith('Primary fake plan usage limit, three times in a row; queued work waits until ')
    assert claim(api, machine) is None
    age_limit(api, 3601)
    assert claim(api, machine) is None                      # an hour is not enough this time
    age_limit(api, 3600)
    fourth = claim(api, machine)
    assert fourth and fourth['job_id'] == attempt['job_id']
    # A turn that ran on the API-key fallback completes normally and is counted.
    post(api, f"attempts/{fourth['id']}/started", {'thread_id': 'fallback'}, machine['token'])
    post(api, f"attempts/{fourth['id']}/complete",
         {'outcome': 'completed', 'last_seq': 0, 'fallback': 'gemini-api', 'text': 'Answered on the API key.'}, machine['token'])
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'completed'
        assert H.status(c, 'ops')['state'] == 'idle'
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.fallback' AND target=?", (fourth['id'],)).fetchone()[0] == 1
    run = next(r for r in api.get('/api/runs', headers=headers()).json() if r['run'] == fourth['id'])
    assert run['fallback'] == 'gemini-api' and run['exit'] == 0
    assert 'Fallback: gemini-api' in api.get(f"/api/runs/{fourth['id']}/log", headers=headers()).text


def test_a_result_that_arrives_after_the_lease_lapsed_settles_the_job_instead_of_a_review(api):
    """The turn never stopped on the Mac; only the lease did. The finished result is the answer
    to the question the review would have asked, so it settles the job and unblocks the bot."""
    from backend.store import H
    machine, message, attempt = interrupted(api)
    aged(api, attempt['id'])                             # past the window `restore` works in
    assert get(api, 'bots/ops/execution-review')['jobs'][0]['id'] == attempt['job_id']
    done = post(api, f"attempts/{attempt['id']}/complete",
                {'outcome': 'completed', 'last_seq': 1, 'text': 'Opened the three pull requests.'},
                machine['token'])
    assert done['outcome'] == 'completed' and not done.get('filed')
    assert done['message']['body'] == 'Opened the three pull requests.'
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM attempts WHERE id=?", (attempt['id'],)).fetchone()[0] == 'completed'
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt['job_id'],)).fetchone()[0] == 'completed'
        turn = c.execute("SELECT exit,summary FROM turns WHERE id=?", (attempt['id'],)).fetchone()
        assert turn['exit'] == 'completed' and turn['summary'] == 'Opened the three pull requests.'
        assert H.status(c, 'ops')['state'] == 'idle'
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    # The bot is free again: the uncertain job is gone, so it can claim its next piece of work.
    ready(api, machine, ['ops'])
    post(api, 'chat/ops', {'text': 'Next thing please.'})
    assert claim(api, machine)


def test_a_late_result_never_overrides_the_attempt_that_took_the_work_over(api):
    """The sweep resumed this run and a second attempt is live. The first attempt's result is
    filed against itself and leaves the new one alone."""
    machine, message, attempt = interrupted(api)
    tool_reporting(api)
    aged(api, attempt['id'], grace=True)
    assert [d['state'] for d in sweep(api)] == ['queued']
    ready(api, machine, ['ops'])
    second = claim(api, machine)
    assert second and second['id'] != attempt['id']
    filed = post(api, f"attempts/{attempt['id']}/complete",
                 {'outcome': 'completed', 'last_seq': 1, 'text': 'The first run finished after all.'},
                 machine['token'])
    assert filed['filed'] is True
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM attempts WHERE id=?", (second['id'],)).fetchone()[0] == 'leased'
        assert c.execute("SELECT attempt_id FROM jobs WHERE id=?", (attempt['job_id'],)).fetchone()[0] == second['id']


def test_a_late_result_still_has_to_come_from_the_runner_that_owns_the_attempt(api):
    from backend.tests.test_api import runner
    machine, message, attempt = interrupted(api)
    aged(api, attempt['id'])
    intruder = runner(api, 'ana')
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'completed', 'last_seq': 1, 'text': 'Not mine.'},
         intruder['token'], expected=403)
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Events missing.'},
         machine['token'], expected=409)
    assert get(api, 'bots/ops/execution-review')['jobs'][0]['id'] == attempt['job_id']


def aged(api, attempt_id, grace=False):
    """Push the interrupted attempt past STALL_MAX, so the runner's own restore no longer applies; with `grace`, also
    past RESULT_GRACE, after which the sweep stops waiting for a lapsed run's own result and may resume it."""
    from backend.execution import RESULT_GRACE, STALL_MAX
    from backend.store import H
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempts SET finished=? WHERE id=?',
                  (H.shift(H.now(), seconds=-(RESULT_GRACE if grace else STALL_MAX) - 1), attempt_id))


def tool_reporting(api, bot='ops'):
    """The sweep only reads "no tool call" as evidence on a host that reports its tool calls."""
    import json
    from backend.store import encode
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()[0])
        config['runtime'] = 'gemini'
        c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (encode(config), bot))


def sweep(api):
    with api.app.state.store.transaction() as c:
        return api.app.state.execution.auto_reconcile(c)


def job_state(api, job_id):
    with api.app.state.store.read() as c:
        return c.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()[0]


def fails_at_once(api, machine, attempt, events=()):
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'grok-internal'}, machine['token'])
    events = [{'seq': 1, 'kind': 'error', 'payload': {'error': 'Internal error'}}, *events]
    post(api, f"attempts/{attempt['id']}/events", {'events': events}, machine['token'])
    return post(api, f"attempts/{attempt['id']}/complete",
                {'outcome': 'failed', 'last_seq': len(events), 'text': ''}, machine['token'])


def test_a_turn_that_failed_before_doing_anything_goes_back_in_the_queue(api):
    """2026-09-22: Grok answered every turn of seven bots with "Internal error" at the first
    event; the 14 jobs left uncertain stopped those bots until a person cleared each one."""
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    fails_at_once(api, machine, attempt)
    with api.app.state.store.read() as c:
        assert job_state(api, attempt['job_id']) == 'queued'
        assert H.status(c, 'ops')['state'] == 'idle'
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.no_effect_retry'").fetchone()[0] == 1
    assert get(api, 'bots/ops/execution-review')['jobs'] == []


def test_a_stopped_run_that_used_tools_is_held_until_reviewed(api):
    """A run that may have acted is not run again on its own: it waits, named, for its operator or BotOps.
    Their resume runs it once more; a second stop is dismissed and BotOps finds out why."""
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'stopped'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'name': 'shell'}},
        {'seq': 2, 'kind': 'message', 'payload': {'text': 'Archived both bots; telling Priya next.'}}]},
        machine['token'])
    expire(api, attempt['id'])
    assert claim(api, machine) is None
    aged(api, attempt['id'])
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert review['output'][-1]['text'] == 'Archived both bots; telling Priya next.'
    assert [t['tool'] for t in review['tools']] == ['shell'] and 'shell' in review['reason']
    assert review['stopped'] == 'changed' and review['acted'] == ['shell']      # named on the card's one line
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'attempt_id': attempt['id'], 'decision': 'resume',
         'note': 'Nothing was archived yet; run it again.', 'acknowledge_uncertain_effects': True})
    ready(api, machine, ['ops'])
    again = claim(api, machine)
    assert 'Nothing was archived yet; run it again.' in again['message']['body']
    with api.app.state.store.transaction() as c:
        if not H.bot(c, H.FLEET_MAINTAINER):
            c.execute("INSERT INTO bots(slug, display_name, state) VALUES(?, 'BotOps', 'active')", (H.FLEET_MAINTAINER,))
    post(api, f"attempts/{again['id']}/started", {'thread_id': 'stopped-again'}, machine['token'])
    post(api, f"attempts/{again['id']}/events", {'events': [{'seq': 1, 'kind': 'tool', 'payload': {'name': 'shell'}}]},
         machine['token'])
    expire(api, again['id'])
    assert claim(api, machine) is None
    aged(api, again['id'])
    assert [d['state'] for d in sweep(api)] == ['cancelled']
    with api.app.state.store.read() as c:
        task = c.execute("SELECT owner,body FROM tasks WHERE title=?", ("Find why ops's runs keep stopping",)).fetchone()
    assert task and task['owner'] == H.bot_actor(H.FLEET_MAINTAINER) and message['body'] not in task['body']
    assert get(api, 'bots/ops/execution-review')['jobs'] == []


@pytest.mark.parametrize('declared, hopped', [(True, False), (False, False), (True, True)])
def test_a_run_that_says_it_reports_tool_calls_resumes_plainly_when_it_called_none(api, declared, hopped):
    """Claude and Codex runners that report each tool call say so on the turn's `status` event; a run like
    that with no tool event provably called none. The same silence from a runner that does not say so
    (an older one), or after the turn fell back to a host that does not, proves nothing, so that run is
    told to check what is already done."""
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'declared'}, machine['token'])
    status = {'state': 'active', **({'tool_events': True} if declared else {})}
    events = [{'seq': 1, 'kind': 'status', 'payload': status},
              {'seq': 2, 'kind': 'message', 'payload': {'text': 'Reading the brief.'}}]
    if hopped:
        events += [{'seq': 3, 'kind': 'diagnostic', 'payload': {'text': 'claude hit its usage limit; running this turn on gemini'}},
                   {'seq': 4, 'kind': 'status', 'payload': {'tool_events': False}},
                   {'seq': 5, 'kind': 'status', 'payload': {'state': 'active'}}]
    post(api, f"attempts/{attempt['id']}/events", {'events': events}, machine['token'])
    expire(api, attempt['id'])
    assert claim(api, machine) is None
    aged(api, attempt['id'], grace=True)
    if not declared or hopped:
        assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
        return
    assert [d['state'] for d in sweep(api)] == ['queued']
    ready(api, machine, ['ops'])
    assert 'recorded no tool call' in claim(api, machine)['message']['body']


def restarted_mid_turn(api, calls, *, reported=True, hop=False):
    """A Claude-style run that says it reports its tool calls, made `calls` ((name, parent) pairs) and was cut off:
    by a runner restart that reported it interrupted, or by a lapsed lease. `hop`: it then fell back to a host that
    does not report its calls (`tool_events: false`, runner-rs execute.rs)."""
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'restarted'}, machine['token'])
    events = [{'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}}]
    for n, (name, parent) in enumerate(calls):
        for status in ('started', 'completed'):
            events.append({'seq': len(events) + 1, 'kind': 'tool', 'payload': {
                'tool': name, 'status': status, 'item_id': f'call-{n}', 'parent_id': parent,
                **({'summary': f'notes/{n}.md'} if status == 'started' else {})}})
    if hop:
        events.append({'seq': len(events) + 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': False}})
    events.append({'seq': len(events) + 1, 'kind': 'message', 'payload': {'text': 'Reading the notes.'}})
    post(api, f"attempts/{attempt['id']}/events", {'events': events}, machine['token'])
    if reported:
        post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': len(events),
             'text': 'Runner restarted during execution'}, machine['token'])
    else:
        expire(api, attempt['id'])
        assert claim(api, machine) is None
    assert job_state(api, attempt['job_id']) == 'uncertain'
    return machine, message, attempt


def test_a_run_that_only_read_resumes_at_once_after_its_runner_restarted(api):
    """Reads, including a subagent's, repeat nothing; a restart ended the run, so nothing is left to restore."""
    machine, message, attempt = restarted_mid_turn(api, [('Read', None), ('Agent', None), ('Grep', 'call-1')])
    assert [d['state'] for d in sweep(api)] == ['queued']
    ready(api, machine, ['ops'])
    body = claim(api, machine)['message']['body']
    assert 'only read (Agent, Grep, Read)' in body and 'check what already happened, then continue' in body
    assert 'finish only what is left' not in body


def test_a_run_that_only_read_waits_out_a_lapsed_lease_for_the_runner_to_restore_it(api):
    machine, message, attempt = restarted_mid_turn(api, [('Read', None)], reported=False)
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert review['stopped'] == 'read' and review['acted'] == []              # the card leads with "Try again"
    # Past the runner's restore window its finished result may still come in: no second run yet.
    aged(api, attempt['id'])
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    aged(api, attempt['id'], grace=True)
    assert [d['state'] for d in sweep(api)] == ['queued']
    ready(api, machine, ['ops'])
    assert 'only read (Read)' in claim(api, machine)['message']['body']


@pytest.mark.parametrize('writer', [('Bash', None), ('Write', 'call-1'), ('mcp__hub__hub_task_update', None)])
def test_a_run_that_used_a_tool_that_writes_waits_and_its_review_lists_the_calls(api, writer):
    """Any call outside the reads, a subagent's included, holds the run for review; the card names every call."""
    machine, message, attempt = restarted_mid_turn(api, [('Read', None), ('Agent', None), writer])
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert [(t['tool'], t['nested'], t['status']) for t in review['tools']] == [
        ('Read', False, 'completed'), ('Agent', False, 'completed'), (writer[0], bool(writer[1]), 'completed')]
    assert review['tools'][0]['summary'] == 'notes/0.md'
    assert f'can change things ({writer[0]})' in review['reason']
    assert review['stopped'] == 'changed' and review['acted'] == [writer[0]]
    from backend.store import H
    with api.app.state.store.transaction() as c:
        if not H.bot(c, H.FLEET_MAINTAINER):
            c.execute("INSERT INTO bots(slug, display_name, state) VALUES(?, 'BotOps', 'active')", (H.FLEET_MAINTAINER,))
    aged(api, attempt['id'])
    assert sweep(api) == [] and sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    with api.app.state.store.read() as c:
        handed = c.execute("SELECT owner,body FROM tasks WHERE title=?", ("Review ops's stopped runs held for a check",)).fetchall()
        holds = c.execute("SELECT count(*) FROM events WHERE action='job.held' AND target=?", (attempt['id'],)).fetchone()[0]
    assert [t['owner'] for t in handed] == [H.bot_actor(H.FLEET_MAINTAINER)] and holds == 1
    # A company task: the words of Ana's own chat with ops stay in that chat.
    assert message['body'] not in handed[0]['body'] and attempt['job_id'] in handed[0]['body']


def test_a_run_that_only_read_but_consumed_an_approval_is_held(api):
    from backend.store import H
    machine, message, attempt = restarted_mid_turn(api, [('Read', None)])
    with api.app.state.store.transaction() as c:
        started = c.execute("SELECT started FROM attempts WHERE id=?", (attempt['id'],)).fetchone()[0]
        c.execute("INSERT INTO approvals(id,kind,message_id,requested_by,created,consumed_at) VALUES(?,?,?,?,?,?)",
                  (H.new_id(), 'spend', message['id'], 'bot:ops', started, started))
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]          # the card never says "only read" of it
    assert review['stopped'] == 'changed' and review['acted'] == [] and 'approval' in review['reason']


def test_a_run_that_read_then_hopped_to_a_silent_host_is_held_and_never_called_read_only(api):
    """Its Read was recorded; what the fallback host did was not. The sweep holds it, and the card agrees."""
    machine, message, attempt = restarted_mid_turn(api, [('Read', None)], hop=True)
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert review['stopped'] == 'unknown' and [t['tool'] for t in review['tools']] == ['Read']


def test_a_read_only_run_that_stops_again_after_its_resume_is_not_resumed_early(api):
    machine, message, attempt = restarted_mid_turn(api, [('Read', None)])
    assert [d['state'] for d in sweep(api)] == ['queued']
    ready(api, machine, ['ops'])
    again = claim(api, machine)
    post(api, f"attempts/{again['id']}/started", {'thread_id': 'restarted'}, machine['token'])
    post(api, f"attempts/{again['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}}]}, machine['token'])
    post(api, f"attempts/{again['id']}/complete", {'outcome': 'interrupted', 'last_seq': 1,
         'text': 'Runner restarted during execution'}, machine['token'])
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    assert get(api, 'bots/ops/execution-review')['jobs'][0]['stopped'] == 'twice'    # not "Try again" first
    aged(api, again['id'])
    assert [d['state'] for d in sweep(api)] == ['cancelled']


def test_a_held_run_names_its_task_and_owner_only_to_who_may_read_it(api):
    machine = runner(api, 'ana')
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    post(api, 'tasks', {'owner': 'ops', 'title': 'Archive the old bots', 'body': 'Archive them and tell Priya.', 'private': True})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'held'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'tool': 'Bash', 'status': 'started', 'item_id': 'b1'}}]}, machine['token'])
    expire(api, attempt['id'])
    assert claim(api, machine) is None
    issue = next(i for i in get(api, 'operations')['issues'] if i['kind'] == 'uncertain_work')
    assert 'Held: "Archive the old bots" (owner ' in issue['detail']
    assert get(api, 'bots/ops/execution-review')['jobs'][0]['task']['owner'] == 'bot:ops'
    held = [i for i in get(api, 'operations', token='ben-test')['issues'] if i['kind'] == 'uncertain_work']
    assert all('Archive the old bots' not in i['detail'] for i in held)


def test_a_held_run_on_a_private_task_is_not_handed_to_botops(api):
    """BotOps cannot read private work: neither hand-off is filed; the operator still reviews it."""
    from backend.store import H
    with api.app.state.store.transaction() as c:
        if not H.bot(c, H.FLEET_MAINTAINER):
            c.execute("INSERT INTO bots(slug, display_name, state) VALUES(?, 'BotOps', 'active')", (H.FLEET_MAINTAINER,))
    machine = runner(api, 'ana')
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    post(api, 'tasks', {'owner': 'ops', 'title': 'Ana only', 'body': 'Private work.', 'private': True})
    attempt = claim(api, machine)
    for run in (attempt, None):
        if run is None:
            post(api, f"jobs/{attempt['job_id']}/reconcile", {'attempt_id': attempt['id'], 'decision': 'resume',
                 'note': 'Nothing happened yet; run it again.', 'acknowledge_uncertain_effects': True})
            ready(api, machine, ['ops'])
            run = claim(api, machine)
        post(api, f"attempts/{run['id']}/started", {'thread_id': 'private'}, machine['token'])
        post(api, f"attempts/{run['id']}/events", {'events': [
            {'seq': 1, 'kind': 'tool', 'payload': {'tool': 'Bash', 'status': 'started', 'item_id': 'b1'}}]}, machine['token'])
        expire(api, run['id'])
        assert claim(api, machine) is None
        aged(api, run['id'])
        sweep(api)
    assert job_state(api, attempt['job_id']) == 'cancelled'    # stopped twice
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE owner=?", (H.bot_actor(H.FLEET_MAINTAINER),)).fetchone()[0] == 0


def test_a_held_company_task_involving_a_bot_the_reader_cannot_read_stays_unnamed(api, monkeypatch):
    """The same fence as reading the task: Ben reads ops but not legal, so legal's request to ops is not his."""
    from backend.auth import Auth
    machine = runner(api, 'ana')
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    task = post(api, 'tasks', {'owner': 'ops', 'title': 'Counsel memo for Acme', 'body': 'Draft it.'})
    task_id = task.get('task', task).get('id') or task['refs']['task']
    attempt = claim(api, machine)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET requester='bot:legal' WHERE title='Counsel memo for Acme'")
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'held'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'tool': 'Bash', 'status': 'started', 'item_id': 'b1'}}]}, machine['token'])
    expire(api, attempt['id'])
    assert claim(api, machine) is None
    post(api, f"tasks/{task_id}/comments", {'text': 'Cite the new clause.'})         # waits behind the stopped run
    def held_for(token):
        return ' '.join(i['detail'] for i in get(api, 'operations', token=token)['issues'] if i['kind'] == 'uncertain_work')
    assert 'Counsel memo for Acme' in held_for('ben-test')          # control: Ben may read it
    assert stopped_issue(api, 'ben-test')['held_tasks'] == ['Counsel memo for Acme']
    real = Auth.bot_accesses
    def without_legal(self, c, who, slugs=None):
        levels = real(self, c, who, slugs)
        if who.actor == 'human:ben':
            levels = {**levels, 'legal': {'see': True, 'read': False, 'write': False}}
        return levels
    monkeypatch.setattr(Auth, 'bot_accesses', without_legal)
    assert 'Counsel memo for Acme' not in held_for('ben-test')
    assert stopped_issue(api, 'ben-test')['held_tasks'] == []
    assert 'Counsel memo for Acme' in held_for('ana-test')


@pytest.mark.parametrize('activity', [None, 'tool'])
def test_rejected_signin_cannot_replay_a_run_that_acted(api, activity):
    import json

    from backend.tests.test_api import runner, assign
    from backend.tests.test_subscriptions import set_runtime

    acted = activity is not None
    machine = runner(api)
    assign(api, machine, 'ops')
    set_runtime(api, 'codex')
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {
        'schema_version': 1, 'runtimes': {'codex': {'installed': True, 'authenticated': 'ready'}},
        'bots': {'ops': {'runtime': 'codex', 'ready': True}}}}, machine['token'])
    post(api, 'chat/ops', {'text': 'Review the request'})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'signin-test'}, machine['token'])
    if acted:
        post(api, f"attempts/{attempt['id']}/events",
             {'events': [{'seq': 1, 'kind': activity, 'payload': {'text': 'Working', 'name': 'send_email'}}]}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete", {
        'outcome': 'failed', 'last_seq': int(acted),
        'auth_rejected': {'runtime': 'codex', 'reason': 'OAuth session expired and could not be refreshed'}
    }, machine['token'])
    with api.app.state.store.read() as c:
        job = c.execute('SELECT state FROM jobs WHERE attempt_id=?', (attempt['id'],)).fetchone()[0]
        assert job == ('uncertain' if acted else 'queued')
        status = c.execute("SELECT state,focus FROM bot_status WHERE bot='ops'").fetchone()
        assert status['state'] == ('crashed' if acted else 'idle')
        assert 'sign-in' in status['focus']
        report = json.loads(c.execute('SELECT readiness_json FROM runners WHERE id=?', (machine['runner_id'],)).fetchone()[0])
        assert report['runtimes']['codex']['authenticated'] == 'rejected'
    assert claim(api, machine) is None


def test_a_person_who_manages_the_bot_stops_its_turn_and_the_next_message_runs(api):
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'held'}, machine['token'])
    assert 'stop' not in post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'])
    post(api, 'bots/ops/stop', {'attempt_id': attempt['id']}, token='cara-test', expected=403)
    post(api, 'bots/ops/stop', {'attempt_id': attempt['id']})
    assert post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'])['stop'] is True
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': 0}, machine['token'])
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'cancelled'
        assert H.status(c, 'ops')['state'] == 'idle'
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    notice = get(api, f"conversations/{message['conversation_id']}/messages")[-1]
    assert notice['body'].startswith('Stopped by ') and notice['kind'] == 'notice'
    # The stopped job is not retried, and the notice starts no run: the person's next message is what runs.
    follow_up = post(api, 'chat/ops', {'text': 'Do this instead.'})
    assert claim(api, machine)['job_id'] == follow_up['id']


def test_a_person_writing_or_try_now_lifts_a_usage_limit_cooldown_once(api):
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    limited_turn(api, machine, attempt)
    assert claim(api, machine) is None
    # A person's new message gets one try now; a bot's or the keeper's would not.
    post(api, "chat/ops", {"text": "Renewed the plan, try again."})
    retry = claim(api, machine)
    assert retry, "a person's message after the limit lets the bot try once"
    limited_turn(api, machine, retry)
    assert claim(api, machine) is None, "the new limit is later than the message: the cooldown holds"
    # Try now: only a person who manages the bot; it clears every bot limited on the same runtime and computer.
    post(api, "bots/ops/limit/retry", {}, token="cara-test", expected=403)
    assign(api, machine, "coo")
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, "coo", state="limited", focus="fake usage limit")
    assert post(api, "bots/ops/limit/retry", {})["cleared"] == 2
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["state"] == H.status(c, "coo")["state"] == "idle"
        assert c.execute("SELECT count(*) FROM events WHERE action='limit.cleared'").fetchone()[0] == 2
    assert claim(api, machine)
    post(api, "bots/ops/limit/retry", {}, expected=409)


def test_a_claim_that_lapsed_before_its_run_started_does_not_count_as_stopping_partway(api):
    machine, message, lapsed = setup_attempt(api)
    expire(api, lapsed['id'])                     # the computer never started it: back in the queue
    ready(api, machine, ['ops'])
    attempt = claim(api, machine)
    assert attempt and attempt['job_id'] == lapsed['job_id']
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'once'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}}]}, machine['token'])
    expire(api, attempt['id'])
    assert claim(api, machine) is None and job_state(api, attempt['job_id']) == 'uncertain'
    aged(api, attempt['id'], grace=True)
    assert [d['state'] for d in sweep(api)] == ['queued']     # one real stop: resumed, not dismissed


# A finished turn whose runner stalled before handing it in (Dev 242; Oct 17-18, results 15 to 76 minutes after the
# lease lapsed). Each shape: the model finished, the lease lapsed, something decided the job, then the result came.

def finished_then_lapsed(api, machine=None, bot='ops', text='Please summarize the current work.', task=False, stopped=False):
    """A run that read, wrote its answer and went idle; then its lease lapsed before the result reached the hub.
    With `task`, a person filed the request as a task in its own room, and the run starts from the hub's notice."""
    from backend.tests.test_api import runner
    if machine is None:
        machine = runner(api, 'ana')
        assign(api, machine, bot)
    ready(api, machine, [bot])
    if task:
        from backend.store import H
        with api.app.state.store.transaction() as c:      # filed outside any chat, as a bot files a ticket
            filed = H.task_create(c, 'human:ana', 'Summarize the work', text, 'bot:' + bot)
            assert not H.task_from_chat(c, filed['id'])
        with api.app.state.store.read() as c:
            message = dict(c.execute("SELECT m.id,m.conversation_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                                     "WHERE j.state='queued' AND j.bot=?", (bot,)).fetchone())
        message['task'] = filed
    else:
        message = post(api, 'chat/' + bot, {'text': text})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'stalled-' + attempt['id']}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}},
        {'seq': 2, 'kind': 'message', 'payload': {'text': 'Done: the summary is in the room.', 'final': True}},
        {'seq': 3, 'kind': 'status', 'payload': {'state': 'idle'}}]}, machine['token'])
    if stopped:
        post(api, f'bots/{bot}/stop', {'attempt_id': attempt['id']})       # never carried by a renewal
    expire(api, attempt['id'])
    assert claim(api, machine) is None and job_state(api, attempt['job_id']) == 'uncertain'
    return machine, message, attempt


def hand_in(api, machine, attempt, text='Done: the summary is in the room.', outcome='completed'):
    return post(api, f"attempts/{attempt['id']}/complete", {'outcome': outcome, 'last_seq': 3, 'text': text},
                machine['token'])


def settled(api, attempt):
    with api.app.state.store.read() as c:
        return (c.execute("SELECT state FROM attempts WHERE id=?", (attempt['id'],)).fetchone()[0],
                c.execute("SELECT state FROM jobs WHERE id=?", (attempt['job_id'],)).fetchone()[0])


def bot_replies(api, message, bot='ops'):
    return [m['body'] for m in get(api, f"conversations/{message['conversation_id']}/messages")
            if m['from_actor'] == 'bot:' + bot]


def test_a_finished_turn_dismissed_because_its_task_closed_still_ends_completed_and_answers_in_the_task(api):
    """bffaef5b, 4db2021c, 1c166f6f, 059f35ce, 14419cd1: the task was closed while its finished result was stuck on
    the computer, the sweep dismissed the lapsed job, then the result came in. The person sees the answer in the
    task's own room, and the task stays closed."""
    from backend.store import H
    machine, message, attempt = finished_then_lapsed(api, task=True)
    task = message['task']
    assert task['conversation_id'] == message['conversation_id']             # the task's own room, not the chat
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'close': True, 'quiet': True})
    aged(api, attempt['id'])
    assert [d['state'] for d in sweep(api)] == ['cancelled']
    done = hand_in(api, machine, attempt)
    assert done['outcome'] == 'completed' and not done.get('filed')
    assert settled(api, attempt) == ('completed', 'completed')
    assert bot_replies(api, message) == ['Done: the summary is in the room.']
    assert done['message']['to_actor'] == 'human:ana'
    with api.app.state.store.read() as c:
        assert H.task(c, task['id'])['status'] == 'closed'
        assert c.execute("SELECT exit FROM turns WHERE id=?", (attempt['id'],)).fetchone()[0] == 'completed'
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.late-settled' AND target=?",
                         (attempt['id'],)).fetchone()[0] == 1
    assert get(api, 'bots/ops/execution-review')['jobs'] == []


def test_a_finished_turn_whose_job_a_person_dismissed_first_still_ends_completed_and_answers(api):
    """9026151f, 38131cc0: the job was let go before the result came; the answer still reaches the room."""
    machine, message, attempt = finished_then_lapsed(api)
    aged(api, attempt['id'])
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'attempt_id': attempt['id'], 'decision': 'dismiss',
         'note': 'Looks stuck; nothing left to send here.', 'acknowledge_uncertain_effects': True})
    assert job_state(api, attempt['job_id']) == 'cancelled'
    done = hand_in(api, machine, attempt)
    assert not done.get('filed') and done['message']['body'] == 'Done: the summary is in the room.'
    assert settled(api, attempt) == ('completed', 'completed')
    assert bot_replies(api, message) == ['Done: the summary is in the room.']


def test_finished_turns_that_come_back_in_a_burst_after_a_runner_wide_stall_all_settle(api):
    """1c166f6f and 38131cc0 reached the hub in the same second after the whole runner stalled: one job dismissed,
    one still uncertain. Both end completed, each with its own answer."""
    from backend.tests.test_api import runner
    machine = runner(api, 'ana')
    assign(api, machine, 'ops')
    assign(api, machine, 'finance')
    first = finished_then_lapsed(api, machine, 'ops')[2]
    message, second = finished_then_lapsed(api, machine, 'finance')[1:]
    aged(api, first['id'])
    post(api, f"jobs/{first['job_id']}/reconcile", {'attempt_id': first['id'], 'decision': 'dismiss',
         'note': 'Stopped; nothing more to send for it.', 'acknowledge_uncertain_effects': True})
    hand_in(api, machine, first)
    hand_in(api, machine, second, text='Finance summary.')
    assert settled(api, first) == ('completed', 'completed')
    assert settled(api, second) == ('completed', 'completed')
    assert bot_replies(api, message, 'finance') == ['Finance summary.']


def test_a_lapsed_finished_turn_is_not_run_a_second_time_while_its_result_may_still_come(api):
    """8df1be95: the sweep resumed the job 15 minutes after the lease lapsed and the request ran twice; its result
    came seven seconds later. Now the sweep waits RESULT_GRACE, and the late result settles the job."""
    machine, message, attempt = finished_then_lapsed(api)
    aged(api, attempt['id'])
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    ready(api, machine, ['ops'])
    assert claim(api, machine) is None
    done = hand_in(api, machine, attempt)
    assert not done.get('filed') and settled(api, attempt) == ('completed', 'completed')
    ready(api, machine, ['ops'])
    assert claim(api, machine) is None                       # nothing left to run: the model is not called again


def test_a_finished_turn_that_comes_back_after_the_resume_but_before_the_rerun_cancels_the_rerun(api):
    """Past the grace the job is queued to run again; a result that still arrives before a computer takes the rerun
    settles the job, so the model is not called a second time."""
    machine, message, attempt = finished_then_lapsed(api)
    aged(api, attempt['id'], grace=True)
    assert [d['state'] for d in sweep(api)] == ['queued']
    done = hand_in(api, machine, attempt)
    assert not done.get('filed') and settled(api, attempt) == ('completed', 'completed')
    ready(api, machine, ['ops'])
    assert claim(api, machine) is None
    assert bot_replies(api, message) == ['Done: the summary is in the room.']


@pytest.mark.parametrize('decision', ['dismiss', 'resume'])
def test_a_finished_turn_never_settles_a_job_a_later_run_took_even_once_that_run_was_let_go(api, decision):
    """The job is the later run's from the moment it claimed it: when that run lapses too and is dismissed or
    resumed, the first run's late result is only filed. Same computer, so only the job's attempt fences it."""
    machine, message, first = finished_then_lapsed(api)
    aged(api, first['id'], grace=True)
    assert [d['state'] for d in sweep(api)] == ['queued']
    ready(api, machine, ['ops'])
    second = claim(api, machine)
    assert second['job_id'] == first['job_id'] and second['id'] != first['id']
    post(api, f"attempts/{second['id']}/started", {'thread_id': 'second'}, machine['token'])
    expire(api, second['id'])
    assert claim(api, machine) is None and job_state(api, second['job_id']) == 'uncertain'
    aged(api, second['id'])
    post(api, f"jobs/{second['job_id']}/reconcile", {'attempt_id': second['id'], 'decision': decision,
         'note': 'The second run stopped.', 'acknowledge_uncertain_effects': True})
    with api.app.state.store.read() as c:
        before = tuple(c.execute("SELECT state,attempt_id FROM jobs WHERE id=?", (first['job_id'],)).fetchone())
    assert before == ('cancelled' if decision == 'dismiss' else 'queued', second['id'])
    filed = hand_in(api, machine, first, text="The first run's old answer.")
    assert filed['filed'] is True
    with api.app.state.store.read() as c:
        assert tuple(c.execute("SELECT state,attempt_id FROM jobs WHERE id=?", (first['job_id'],)).fetchone()) == before
        assert c.execute("SELECT state FROM attempts WHERE id=?", (first['id'],)).fetchone()[0] == 'expired'
    assert bot_replies(api, message) == []


def test_a_stop_the_runner_never_heard_still_stands_over_the_answer_it_finished_late(api):
    """Dev 242 R1: a person pressed Stop while the computer was asleep. No renewal carried it, the lease lapsed, the
    model finished anyway and the woken runner handed the answer in as completed. It is filed on its run, not posted,
    and the job is not settled completed, whether the job is still uncertain or the sweep has let it go."""
    from backend.tests.test_api import runner
    machine = runner(api, 'ana')
    for bot, dismissed in (('ops', False), ('finance', True)):
        assign(api, machine, bot)
        machine, message, attempt = finished_then_lapsed(api, machine, bot, stopped=True)
        aged(api, attempt['id'])
        if dismissed:
            post(api, f"jobs/{attempt['job_id']}/reconcile", {'attempt_id': attempt['id'], 'decision': 'dismiss',
                 'note': 'Stopped; nothing left to send.', 'acknowledge_uncertain_effects': True})
        before = job_state(api, attempt['job_id'])
        filed = hand_in(api, machine, attempt)
        assert filed['filed'] is True and filed['message'] is None
        assert settled(api, attempt) == ('expired', before)
        assert bot_replies(api, message, bot) == []


def test_a_late_result_that_did_not_finish_after_a_dismissal_is_only_filed(api):
    """Only a finished turn settles a job that was let go: an interrupted one stays on its own run."""
    machine, message, attempt = finished_then_lapsed(api)
    aged(api, attempt['id'])
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'attempt_id': attempt['id'], 'decision': 'dismiss',
         'note': 'Looks stuck; nothing left to send here.', 'acknowledge_uncertain_effects': True})
    filed = hand_in(api, machine, attempt, text='Runner restarted during execution', outcome='interrupted')
    assert filed['filed'] is True and settled(api, attempt) == ('expired', 'cancelled')


def stopped_issue(api, token='ana-test'):
    return next(i for i in get(api, 'operations', token=token)['issues'] if i['kind'] == 'uncertain_work')


def test_a_stopped_run_that_holds_queued_work_needs_a_person_and_says_what_waits(api):
    """Oct 19, launch: five unreviewed stopped runs fenced 14 queued Builder wakes, but each was 'review later' and the
    Review stopped runs button rendered nowhere. A run that holds nothing may still wait for a later look."""
    machine = runner(api, 'ana')
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    task = post(api, 'tasks', {'owner': 'ops', 'title': 'Archive the old bots', 'body': 'Archive them and tell Priya.'})
    task_id = task.get('task', task).get('id') or task['refs']['task']
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'held'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'tool': 'Bash', 'status': 'started', 'item_id': 'b1'}}]}, machine['token'])
    expire(api, attempt['id'])
    assert claim(api, machine) is None
    issue = stopped_issue(api)
    assert issue['needs_person'] is False and 'saved 1 stopped run for later' in issue['title']
    for text in ('Priya says the old bots can go.', 'Also archive the test bot.'):
        post(api, f"tasks/{task_id}/comments", {'text': text})
    ready(api, machine, ['ops'])
    assert claim(api, machine) is None                     # the claim's fence holds both behind the stopped run
    issue = stopped_issue(api)
    assert issue['needs_person'] is True and issue['action'] == 'review' and issue['severity'] == 'error'
    assert issue['title'].endswith('has 1 stopped run holding up work')
    assert issue['held_back'] == 2 and issue['held_tasks'] == ['Archive the old bots']
    assert issue['detail'] == ('"Archive the old bots" (owner ops) stopped partway. 2 queued requests wait on '
                               '"Archive the old bots" until you decide. Choose Try again or Don\'t retry.')
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'attempt_id': attempt['id'], 'decision': 'dismiss',
         'note': 'The archive already ran; do not repeat it.', 'acknowledge_uncertain_effects': True})
    assert not [i for i in get(api, 'operations')['issues'] if i['kind'] == 'uncertain_work']
    assert claim(api, machine)                             # reviewed: the waiting work runs


def stop_partway(api, machine, attempt, thread):
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': thread}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'tool': 'Bash', 'status': 'started', 'item_id': 'b1'}}]}, machine['token'])
    expire(api, attempt['id'])
    assert claim(api, machine) is None


def test_held_work_is_counted_and_named_only_for_who_may_read_it(api):
    """Ben reads one of ops' stopped runs, so his issue is drawn; what the keeper left on Ana's private run, and what
    waits behind it, counts toward neither its numbers, its names nor its urgency."""
    machine = runner(api, 'ana')
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    post(api, 'tasks', {'owner': 'ops', 'title': 'Weekly digest', 'body': 'Send it.'})
    stop_partway(api, machine, claim(api, machine), 'company')
    private = post(api, 'tasks', {'owner': 'ops', 'title': 'Ana only', 'body': 'Private work.', 'private': True})
    private_id = private.get('task', private).get('id') or private['refs']['task']
    # The keeper settles the request Ana's private run was for and leaves the comment carried on that run.
    ready(api, machine, ['ops'])
    first = claim(api, machine)
    stop_partway(api, machine, first, 'twice')
    aged(api, first['id'])
    assert sweep(api) == []
    post(api, f"jobs/{first['job_id']}/reconcile", {'attempt_id': first['id'], 'decision': 'resume',
         'note': 'Nothing happened yet; run it again.', 'acknowledge_uncertain_effects': True})
    ready(api, machine, ['ops'])
    again = claim(api, machine)
    post(api, f"attempts/{again['id']}/started", {'thread_id': 'twice-again'}, machine['token'])
    post(api, f"tasks/{private_id}/comments", {'text': 'Also include the weekly numbers.'})
    carried = post(api, f"attempts/{again['id']}/inputs", {}, machine['token'])['messages']
    assert len(carried) == 1
    post(api, f"attempts/{again['id']}/inputs/{carried[0]['id']}/ack", {}, machine['token'])
    post(api, f"attempts/{again['id']}/events", {'events': [{'seq': 1, 'kind': 'tool', 'payload': {'name': 'shell'}}]},
         machine['token'])
    expire(api, again['id'])
    assert claim(api, machine) is None
    aged(api, again['id'])
    assert [d['state'] for d in sweep(api)] == ['cancelled']
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (carried[0]['id'],)).fetchone()[0] == 'uncertain'
    post(api, f"tasks/{private_id}/comments", {'text': 'One more thing for you.'})     # waits behind it
    ready(api, machine, ['ops'])
    assert claim(api, machine) is None
    ana = stopped_issue(api)
    assert ana['needs_person'] is True and ana['held_back'] == 1 and ana['held_tasks'] == ['Ana only']
    ben = stopped_issue(api, 'ben-test')
    assert 'saved 1 stopped run for later' in ben['title'] and ben['needs_person'] is False
    assert not ben.get('held_back') and 'Ana only' not in ben['detail']


def test_a_run_carried_on_a_stopped_run_the_keeper_settled_is_left_for_a_person(api):
    """The keeper dismissed only the request a stopped run was for ('stopped partway twice'); the message folded into
    the same run stayed held with nobody asked. It is a person's now, whether or not anything waits behind it."""
    from backend.store import H
    machine, message, first = setup_attempt(api)
    post(api, f"attempts/{first['id']}/started", {'thread_id': 'twice'}, machine['token'])
    post(api, f"attempts/{first['id']}/events", {'events': [{'seq': 1, 'kind': 'tool', 'payload': {'name': 'shell'}}]},
         machine['token'])
    expire(api, first['id'])
    assert claim(api, machine) is None
    aged(api, first['id'])
    assert sweep(api) == []
    post(api, f"jobs/{first['job_id']}/reconcile", {'attempt_id': first['id'], 'decision': 'resume',
         'note': 'Nothing was archived yet; run it again.', 'acknowledge_uncertain_effects': True})
    ready(api, machine, ['ops'])
    again = claim(api, machine)
    post(api, f"attempts/{again['id']}/started", {'thread_id': 'twice-again'}, machine['token'])
    follow = post(api, 'chat/ops', {'text': 'Also include the weekly numbers'})
    assert [m['id'] for m in post(api, f"attempts/{again['id']}/inputs", {}, machine['token'])['messages']] == [follow['id']]
    post(api, f"attempts/{again['id']}/inputs/{follow['id']}/ack", {}, machine['token'])
    post(api, f"attempts/{again['id']}/events", {'events': [{'seq': 1, 'kind': 'tool', 'payload': {'name': 'shell'}}]},
         machine['token'])
    expire(api, again['id'])
    assert claim(api, machine) is None
    aged(api, again['id'])
    assert [d['state'] for d in sweep(api)] == ['cancelled']           # the request it was for: stopped twice
    with api.app.state.store.read() as c:
        carried = c.execute("SELECT state FROM jobs WHERE message_id=?", (follow['id'],)).fetchone()[0]
    assert carried == 'uncertain'                                       # the carried one: held, not safe to repeat
    issue = stopped_issue(api)
    assert issue['needs_person'] is True and issue['action'] == 'review' and not issue['held_back']
    assert issue['detail'].endswith(" stopped partway. Tico can't safely decide it on its own. Choose Try again or Don't retry.")
    assert [j['id'] for j in get(api, 'bots/ops/execution-review')['jobs']]
