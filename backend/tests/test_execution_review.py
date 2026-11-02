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


def test_a_sparse_failure_with_recorded_writes_is_held_not_retried(api):
    """A host that streams almost nothing can still have written to the hub, reported usage and left text.
    Treating that as no-effect would retry work that may already have pushed, sent or paid."""
    machine, message, attempt = setup_attempt(api, bot='finance')
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'sparse-positive'}, machine['token'])
    post(api, 'messages', {'to': 'ana', 'text': 'Synthetic effect already delivered'}, attempt['token'])
    with api.app.state.store.read() as c:
        assert c.execute('SELECT writes FROM turns WHERE id=?', (attempt['id'],)).fetchone()[0] > 0
    post(api, f"attempts/{attempt['id']}/complete",
         {'outcome': 'failed', 'last_seq': 0, 'text': 'Synthetic partial output',
          'usage': {'output_tokens': 100_000, 'model': 'gpt-6-sol', 'runtime': 'codex'}}, machine['token'])
    with api.app.state.store.read() as c:
        assert job_state(api, attempt['job_id']) == 'uncertain'
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.no_effect_retry' AND target=?",
                         (attempt['id'],)).fetchone()[0] == 0
    assert get(api, 'bots/finance/execution-review')['jobs'][0]['id'] == attempt['job_id']


@pytest.mark.parametrize('evidence', ['text', 'pushes', 'tokens_out', 'segment_output', 'consumed_approval'])
def test_each_kind_of_sparse_evidence_alone_holds_the_run(api, evidence):
    """Each piece of positive evidence on its own makes a blind retry unsafe: partial text, a git push, output tokens
    (top-level or in a fallback segment), or an approval the bot consumed during the run."""
    from backend.store import H
    machine, message, attempt = setup_attempt(api, bot='finance')
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'sparse-' + evidence}, machine['token'])
    body = {'outcome': 'failed', 'last_seq': 0, 'text': ''}
    if evidence == 'text':
        body['text'] = 'Synthetic partial output'
    elif evidence == 'pushes':
        body['pushes'] = [{'path': '/synthetic/worktree', 'count': 1}]
    elif evidence == 'tokens_out':
        body['tokens_out'] = 40
    elif evidence == 'segment_output':
        body['usage'] = {'input_tokens': 9_000, 'model': 'gpt-6-sol', 'runtime': 'codex',
                         'segments': [{'input_tokens': 9_000, 'model': 'gpt-6-sol'},
                                      {'output_tokens': 40, 'model': 'muse-spark-1.3'}]}
    else:
        with api.app.state.store.transaction() as c:
            c.execute('INSERT INTO approvals(id,kind,message_id,requested_by,created,consumed_at) VALUES(?,?,?,?,?,?)',
                      (H.new_id(), 'spend', attempt['message']['id'], 'bot:finance', H.now(), H.shift(H.now(), seconds=1)))
    post(api, f"attempts/{attempt['id']}/complete", body, machine['token'])
    assert job_state(api, attempt['job_id']) == 'uncertain'


def test_a_sparse_failure_billed_only_for_its_prompt_still_goes_back_in_the_queue(api):
    """A provider bills the prompt and then errors: input tokens alone are not evidence the turn did anything."""
    machine, message, attempt = setup_attempt(api, bot='finance')
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'sparse-billed'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete",
         {'outcome': 'failed', 'last_seq': 0, 'text': '', 'tokens_in': 9_000,
          'usage': {'input_tokens': 9_000, 'cached_tokens': 2_000, 'model': 'gpt-6-sol', 'runtime': 'codex'}},
         machine['token'])
    assert job_state(api, attempt['job_id']) == 'queued'


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


def restarted_mid_turn(api, calls, *, reported=True, hop=False, restart=True):
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
             'text': 'Runner restarted during execution' if restart else 'Execution interrupted'}, machine['token'])
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


def hub_update(api, shift=0):
    """The events Tico's own update writes (app.py, hosted_updates.py), at now; `shift` moves both that many seconds
    into the past, as an update that finished before the run began."""
    from backend.store import H
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, 'system.update.started', '0.4.17', {'from': '0.4.16', 'to': '0.4.17'})
        H.event(c, H.KEEPER, 'system.update.finished', '0.4.17',
                {'outcome': 'succeeded', 'from': '0.4.16', 'to': '0.4.17', 'message': ''})
        if shift:
            for row in c.execute("SELECT id,ts FROM events WHERE action LIKE 'system.update.%'").fetchall():
                c.execute("UPDATE events SET ts=? WHERE id=?", (H.shift(row[1], seconds=shift), row[0]))


def lapsed_shell_run(api, summary='ls notes', update=None):
    """A run that ran one shell command, then its lease lapsed. `update` is None for no update during the run, or
    the seconds hub_update moves its events (0 for an update during the run)."""
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'updated'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}},
        {'seq': 2, 'kind': 'tool', 'payload': {'tool': 'shell', 'status': 'started', 'item_id': 'sh1',
                                               'summary': summary}}]},
        machine['token'])
    if update is not None:
        hub_update(api, shift=update)
    expire(api, attempt['id'])
    assert claim(api, machine) is None
    assert job_state(api, attempt['job_id']) == 'uncertain'
    return machine, message, attempt


def run_line(api):
    """The run's own line, as Needs you lists it for its operator."""
    from backend import execution
    store = api.app.state.store
    with store.read_transaction() as c:
        [line], _ = execution.stopped_rows(c, store.settings.test_identities['ana-test'], api.app.state.auth, 'ops')
    return line


def no_one_asked(api):
    """Nothing about the run reaches a person or BotOps: no Needs you or Settings line, no review task."""
    from backend.store import H
    assert not [i for i in get(api, 'operations')['issues'] if i['kind'] == 'uncertain_work']
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE title IN (?,?)", (
            H.HELD_REVIEW_TITLE.format(bot='ops'), "Find why ops's runs keep stopping")).fetchone()


def resumed_body(api, machine):
    ready(api, machine, ['ops'])
    attempt = claim(api, machine)
    assert attempt['recovery']['resume_session'] is True       # the same computer continues the provider session
    return attempt['message']['body']


@pytest.mark.parametrize('update', [0, -300])
def test_a_lapse_in_tico_s_update_window_resumes_on_the_next_sweep_and_asks_nobody(api, update):
    """An update must not wait for runs, so a run it cut resumes by itself, a shell command and all, with the
    restart's note. The updater restarts the runners after it reports its finish, so a run that began minutes after
    the update finished counts too, flagged or not."""
    machine, message, attempt = lapsed_shell_run(api, update=update)
    assert [d['state'] for d in sweep(api)] == ['queued']
    no_one_asked(api)
    body = resumed_body(api, machine)
    assert "by Tico's update" in body and 'Check what already happened first' in body
    assert 'No consumed approval or named irreversible outside action was recorded.' in body


def test_a_restart_the_computer_reports_during_tico_s_update_is_retried_by_the_sweep(api):
    """The computer's own restart report is the proof: with it, a run that used only a shell resumes at once."""
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'updated'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}},
        {'seq': 2, 'kind': 'tool', 'payload': {'tool': 'shell', 'status': 'started', 'item_id': 'sh1',
                                               'summary': 'ls notes'}}]}, machine['token'])
    hub_update(api)
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': 2,
         'text': 'Runner restarted during execution'}, machine['token'])
    [review] = get(api, 'bots/ops/execution-review')['jobs']
    assert review['stopped'] == 'restarted'
    assert "The computer reported a restart during Tico's update" in review['reason']
    assert [d['state'] for d in sweep(api)] == ['queued']


def owner_update(api, monkeypatch):
    """The owner's Update now (app.py), its updater answering that the update started."""
    import httpx
    from backend import releases
    monkeypatch.setattr(releases, 'TRANSPORT', httpx.MockTransport(lambda request: httpx.Response(
        200, json={'state': 'pulling', 'from': '0.4.16', 'to': '0.4.17', 'message': ''})))
    monkeypatch.setenv('TICO_UPDATER_URL', 'http://updater:9000')
    monkeypatch.setenv('TICO_VERSION', '0.4.16')
    post(api, 'system/update', {'version': 'v0.4.17'})


def live_shell_run(api, summary):
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'cut'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}},
        {'seq': 2, 'kind': 'tool', 'payload': {'tool': 'shell', 'status': 'started', 'item_id': 'sh1',
                                               'summary': summary}}]}, machine['token'])
    return machine, message, attempt


def lapse(api, machine, attempt):
    expire(api, attempt['id'])
    assert claim(api, machine) is None and job_state(api, attempt['job_id']) == 'uncertain'


def test_a_run_live_as_the_owner_updates_is_flagged_and_resumes_naming_its_outside_action(api, monkeypatch):
    """The cut is recorded as the update starts, not guessed afterwards. A run that recorded a merge still resumes,
    held for nobody: its note names the merge to verify before doing it again."""
    machine, message, attempt = live_shell_run(api, 'gh pr merge 12')
    owner_update(api, monkeypatch)
    with api.app.state.store.read() as c:
        [flag] = c.execute("SELECT detail_json FROM events WHERE action='attempt.update_cut' AND target=?",
                           (attempt['id'],)).fetchall()
    assert json.loads(flag[0])['cause'] == 'update' and json.loads(flag[0])['update'] == '0.4.17'
    lapse(api, machine, attempt)
    assert [d['state'] for d in sweep(api)] == ['queued']
    no_one_asked(api)
    body = resumed_body(api, machine)
    assert "by Tico's update" in body and 'merge pull request' in body
    assert 'verify whether that already took effect before doing it again' in body


def test_a_flagged_run_that_lapses_long_after_the_server_came_back_keeps_the_usual_rule(api):
    """The flag says the run was live at the cut, not that every later lapse is the cut's: one that carried on and
    lapsed an hour later is held like any other lapse after a shell command."""
    from backend.execution import flag_cut
    from backend.store import H
    machine, message, attempt = live_shell_run(api, 'ls notes')
    with api.app.state.store.transaction() as c:
        flag_cut(c, 'restart')
        c.execute("UPDATE events SET ts=? WHERE action='attempt.update_cut'", (H.shift(H.now(), seconds=-3600),))
    lapse(api, machine, attempt)
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'


def test_a_request_cut_by_updates_resumes_three_times_then_ends_quietly(api):
    from backend.execution import UPDATE_RESUMES, flag_cut
    machine, message, attempt = live_shell_run(api, 'ls notes')
    for n in range(UPDATE_RESUMES + 1):
        if n:
            ready(api, machine, ['ops'])
            attempt = claim(api, machine)
            post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'cut'}, machine['token'])
        with api.app.state.store.transaction() as c:
            flag_cut(c, 'update', '0.4.17')
        lapse(api, machine, attempt)
        [decision] = sweep(api)
        assert decision['state'] == ('queued' if n < UPDATE_RESUMES else 'cancelled')
    no_one_asked(api)
    with api.app.state.store.read() as c:
        note = c.execute("SELECT note FROM job_recovery WHERE job_id=?", (attempt['job_id'],)).fetchone()[0]
    assert note.startswith('Ended quietly')


@pytest.mark.parametrize('stale', [False, True])
def test_a_run_held_before_this_release_for_an_update_settles_on_the_next_sweep(api, stale):
    """Runs held before Tico resumed update cuts, already handed to BotOps, settle with no choice: a recent one
    resumes, one stopped more than a day ago is dismissed as stale work."""
    from backend.store import H
    hours = 25 if stale else 1
    machine, message, attempt = lapsed_shell_run(api, update=-hours * 3600)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET finished=? WHERE id=?", (H.shift(H.now(), seconds=-hours * 3600 + 60), attempt['id']))
        H.event(c, H.KEEPER, 'job.held', attempt['id'], {'job_id': attempt['job_id'], 'tools': ['shell'],
                                                         'reason': 'Review required before repeating possible effects.'})
    assert [d['state'] for d in sweep(api)] == ['cancelled' if stale else 'queued']
    no_one_asked(api)


def cut_behind_live_work(api):
    """An update cut whose lane is busy with live work ('live-one'): the sweep saves Tico's choice for it."""
    machine, message, attempt = lapsed_shell_run(api, update=0)
    with api.app.state.store.transaction() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(attempts)")]
        picked = ",".join({'id': "'live-one'", 'token_hash': "'live-hash'", 'state': "'running'"}.get(k, k) for k in cols)
        c.execute(f"INSERT INTO attempts ({','.join(cols)}) SELECT {picked} FROM attempts WHERE id=?", (attempt['id'],))
    [decision] = sweep(api)
    assert decision == {'job_id': attempt['job_id'], 'state': 'uncertain', 'deferred': True}
    return machine, message, attempt


def test_an_update_cut_behind_live_work_on_its_lane_is_saved_for_it_and_shown_to_nobody(api):
    """A run waits for live work in its lane before it resumes; meanwhile Tico's own choice is saved, so no Needs you
    line offers one, and the sweep applies it once the lane frees."""
    from backend import execution
    machine, message, attempt = cut_behind_live_work(api)
    store = api.app.state.store
    with store.read_transaction() as c:
        assert execution.stopped_rows(c, store.settings.test_identities['ana-test'], api.app.state.auth, 'ops') == ([], 0)
    no_one_asked(api)
    with store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id='live-one'")
    assert [d['state'] for d in sweep(api)] == ['queued']
    assert "by Tico's update" in resumed_body(api, machine)


@pytest.mark.parametrize('since', ['stop', 'stale'])
def test_tico_s_saved_choice_for_an_update_cut_is_judged_again_when_its_lane_frees(api, since):
    """The saved choice is Tico's, not a person's: a Stop pressed on the lapsed run meanwhile, or a day's wait for the
    lane, dismisses it quietly when the lane frees instead of resuming stale or stopped work."""
    from backend.store import H
    machine, message, attempt = cut_behind_live_work(api)
    if since == 'stop':
        post(api, 'bots/ops/stop', {'attempt_id': attempt['id']})
    with api.app.state.store.transaction() as c:
        if since == 'stale':
            for table, col, where in (('events', 'ts', "action LIKE 'system.update.%'"), ('attempts', 'finished', 'id=?')):
                args = (attempt['id'],) if '?' in where else ()
                for rid, ts in c.execute(f"SELECT rowid,{col} FROM {table} WHERE {where}", args).fetchall():
                    c.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?", (H.shift(ts, seconds=-25 * 3600), rid))
        c.execute("UPDATE attempts SET state='completed' WHERE id='live-one'")
    assert [d['state'] for d in sweep(api)] == ['cancelled']
    with api.app.state.store.read() as c:
        note = c.execute("SELECT note FROM job_recovery WHERE job_id=?", (attempt['job_id'],)).fetchone()[0]
    assert note.startswith('Stopped by' if since == 'stop' else 'Dismissed quietly')
    no_one_asked(api)


def test_a_lease_claimed_before_a_restart_that_started_after_it_lapses_as_usual(api):
    """The server's start flags the leases its last process held, but a run that only began after the restart ran on
    the new server: its lapse is an ordinary one, held for review like any run that acted, never resumed as a cut."""
    from backend.execution import flag_cut
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    with api.app.state.store.transaction() as c:
        assert flag_cut(c, 'restart') == 1
        c.execute("UPDATE events SET ts=? WHERE action='attempt.update_cut' AND target=?",
                  (H.shift(H.now(), seconds=-60), attempt['id']))
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'after'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}},
        {'seq': 2, 'kind': 'tool', 'payload': {'tool': 'shell', 'status': 'started', 'item_id': 'sh1',
                                               'summary': 'gh pr merge 12'}}]}, machine['token'])
    lapse(api, machine, attempt)
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    [review] = get(api, 'bots/ops/execution-review')['jobs']
    assert review['stopped'] == 'changed' and 'restart' not in review['reason']


@pytest.mark.parametrize('update', [None, -3600])
def test_a_lapse_with_no_hub_update_during_the_run_stays_held(api, update):
    """A lapsed lease is also what a computer that went offline looks like, so with no update during the run it is
    held like any other run that acted (an update an hour before the run began is no cause for it either)."""
    machine, message, attempt = lapsed_shell_run(api, update=update)
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    [review] = get(api, 'bots/ops/execution-review')['jobs']
    assert review['stopped'] == 'changed' and "Tico's update" not in review['reason']


@pytest.mark.parametrize('writer', [('Bash', None), ('Write', 'call-1'), ('mcp__hub__hub_task_update', None)])
def test_a_run_that_used_a_tool_that_writes_waits_and_its_review_lists_the_calls(api, writer):
    """Any call outside the reads, a subagent's included, holds the run for review; the card names every call."""
    machine, message, attempt = restarted_mid_turn(api, [('Read', None), ('Agent', None), writer], restart=False)
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
    machine, message, attempt = restarted_mid_turn(api, [('Read', None)], hop=True, restart=False)
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert review['stopped'] == 'unknown' and [t['tool'] for t in review['tools']] == ['Read']


def test_a_read_only_run_that_stops_again_after_its_resume_is_dismissed_at_once(api):
    machine, message, attempt = restarted_mid_turn(api, [('Read', None)])
    assert [d['state'] for d in sweep(api)] == ['queued']
    ready(api, machine, ['ops'])
    again = claim(api, machine)
    post(api, f"attempts/{again['id']}/started", {'thread_id': 'restarted'}, machine['token'])
    post(api, f"attempts/{again['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}}]}, machine['token'])
    post(api, f"attempts/{again['id']}/complete", {'outcome': 'interrupted', 'last_seq': 1,
         'text': 'Runner restarted during execution'}, machine['token'])
    assert get(api, 'bots/ops/execution-review')['jobs'][0]['stopped'] == 'twice'
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
        return ' '.join(i['detail'] + ' ' + ' '.join(str(r['title']) for r in i.get('runs', []))
                        for i in get(api, 'operations', token=token)['issues'] if i['kind'] == 'uncertain_work')
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
    assert [r['title'] for r in stopped_issue(api, 'ben-test')['runs']] == [None]     # still a line, unnamed
    # Ben is not ops' operator: no buttons, and no handles to decide with; Ana, who operates it, gets both.
    ben = stopped_issue(api, 'ben-test')
    assert ben['can_review'] is False and all('jobs' not in r for r in ben['runs'])
    ana = stopped_issue(api, 'ana-test')
    assert ana['can_review'] is True and [len(r['jobs']) for r in ana['runs']] == [1]
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


def test_a_model_at_capacity_after_a_tool_is_saved_for_review_not_replayed(api, monkeypatch):
    # An older runner still reports the refusal as a limit; the run's own events say it acted, and they decide.
    from backend.store import H
    monkeypatch.setattr(H, 'now', lambda: '2026-10-23T05:00:00.000000Z')
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'capacity-acted'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'name': 'send_email', 'text': 'Sent'}},
        {'seq': 2, 'kind': 'error', 'payload': {'error': 'Selected model is at capacity. Please try a different model.',
                                                'limit': True}}]}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'failed', 'last_seq': 2, 'limited': True, 'text': ''},
         machine['token'])
    monkeypatch.setattr(H, 'now', lambda: '2026-10-23T05:02:01.000000Z')
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen=?,awake_since=NULL", (H.now(),))
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'uncertain'
    ready(api, machine, ['ops'])
    assert claim(api, machine) is None


def later():
    """Times for a test's turns from just after its message was written, so the message never reads as a person's
    after a limit (person_wrote_since), and within its first claim's start reservation."""
    from backend.store import H
    base = H.shift(H.now(), seconds=5)
    return lambda seconds: H.shift(base, seconds=seconds)


def capacity_turn(api, monkeypatch, machine, attempt, at, acted=True, continues=True, thread='capacity-thread'):
    from backend.store import H
    monkeypatch.setattr(H, 'now', lambda: at)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': thread}, machine['token'])
    events = [{'seq': 1, 'kind': 'tool', 'payload': {'name': 'send_email', 'text': 'Sent'}}] if acted else []
    events.append({'seq': len(events) + 1, 'kind': 'error',
                   'payload': {'error': 'Selected model is at capacity. Please try a different model.', 'limit': True}})
    post(api, f"attempts/{attempt['id']}/events", {'events': events}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'failed', 'last_seq': len(events), 'text': '',
                                                     'limited': not acted, 'continues': continues}, machine['token'])


def claim_at(api, monkeypatch, machine, at, continues=True):
    """A claim at `at` by a runner that does (`continues`) or does not advertise capacity continuation."""
    from backend.store import H
    monkeypatch.setattr(H, 'now', lambda: at)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen=?,awake_since=NULL", (H.now(),))
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {'ops': True},
                                    "capabilities": ['capacity_continue_v1'] if continues else []}, token=machine['token'])
    return claim(api, machine)


@pytest.mark.parametrize('continuation_acted', [False, True])
def test_a_model_at_capacity_after_the_bot_acted_continues_its_thread_once(api, monkeypatch, continuation_acted):
    from backend import execution
    machine, message, attempt = setup_attempt(api)
    at = later()
    capacity_turn(api, monkeypatch, machine, attempt, at(0))
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
    assert get(api, 'bots/ops/execution-review')['jobs'] == []                  # not held for a person
    assert claim_at(api, monkeypatch, machine, at(60)) is None   # the capacity hold
    following = claim_at(api, monkeypatch, machine, at(121))
    assert following['job_id'] == attempt['job_id'] and following['continue_thread'] == 'capacity-thread'
    assert following['message']['body'].endswith(execution.CONTINUE_NOTE)
    # A second capacity refusal exhausts the one continuation, whether or not it ran another tool.
    capacity_turn(api, monkeypatch, machine, following, at(180), acted=continuation_acted,
                  continues=continuation_acted)
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'uncertain'
        assert execution.H.status(c, 'ops')['state'] == 'crashed'
    assert [j['attempt_id'] for j in get(api, 'bots/ops/execution-review')['jobs']] == [following['id']]
    assert claim_at(api, monkeypatch, machine, at(600)) is None


def test_a_capacity_notice_alone_is_no_work_to_continue(api, monkeypatch):
    # The runner reads Claude's own "overloaded" message as work and sends `continues`; the server's stricter proof wins.
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    at = later()
    monkeypatch.setattr(H, 'now', lambda: at(0))
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'capacity-thread'}, machine['token'])
    notice = 'Selected model is at capacity. Please try a different model.'
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}},
        {'seq': 2, 'kind': 'message', 'payload': {'text': notice, 'final': False}},
        {'seq': 3, 'kind': 'error', 'payload': {'error': notice, 'limit': True}},
    ]}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'failed', 'last_seq': 3, 'text': notice,
                                                     'continues': True}, machine['token'])
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
    assert claim_at(api, monkeypatch, machine, at(60)) is None                  # the capacity hold still applies
    following = claim_at(api, monkeypatch, machine, at(121))
    assert following['job_id'] == attempt['job_id'] and not following.get('continue_thread')


def test_a_capacity_continuation_with_a_missing_session_is_held_without_a_silent_retry(api, monkeypatch):
    machine, message, attempt = setup_attempt(api)
    at = later()
    capacity_turn(api, monkeypatch, machine, attempt, at(0))
    following = claim_at(api, monkeypatch, machine, at(121))
    post(api, f"attempts/{following['id']}/started", {'thread_id': 'capacity-thread'}, machine['token'])
    post(api, f"attempts/{following['id']}/events", {'events': [
        {'seq': 1, 'kind': 'error', 'payload': {'error': 'No conversation found with session ID: capacity-thread'}}
    ]}, machine['token'])
    post(api, f"attempts/{following['id']}/complete", {'outcome': 'failed', 'last_seq': 1, 'text': ''}, machine['token'])
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'uncertain'
    assert [j['attempt_id'] for j in get(api, 'bots/ops/execution-review')['jobs']] == [following['id']]
    assert claim_at(api, monkeypatch, machine, at(600)) is None


def test_a_runner_that_cannot_continue_a_thread_is_never_handed_the_continuation(api, monkeypatch):
    machine, message, attempt = setup_attempt(api)
    at = later()
    capacity_turn(api, monkeypatch, machine, attempt, at(0))
    # A runner from before capacity continuation would ignore `continue_thread` and start the job on a fresh thread.
    assert claim_at(api, monkeypatch, machine, at(121), continues=False) is None
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
    following = claim_at(api, monkeypatch, machine, at(130))
    assert following['job_id'] == attempt['job_id'] and following['continue_thread'] == 'capacity-thread'


def test_a_capacity_stop_without_a_kept_thread_is_held_and_a_continuation_never_moves_to_a_fallback(api, monkeypatch):
    from backend import execution
    machine, message, attempt = setup_attempt(api)
    at = later()
    capacity_turn(api, monkeypatch, machine, attempt, at(0), continues=False)
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'uncertain'
        assert execution.capacity_continuation(c, attempt['job_id']) is None
    # A job that continues a thread waits out its own login's hold even where a fallback is ready.
    monkeypatch.setattr(execution, 'primary_hold', lambda *a, **k: 'held')
    monkeypatch.setattr(execution, 'fallback_choice', lambda *a, **k: ('', 'claude'))
    with api.app.state.store.transaction() as c:
        assert execution.login_refusal(c, None, machine['runner_id'], 'ops', {}, attempt['job_id']) is None
        c.execute("UPDATE attempts SET thread_id='kept',result_json=json_set(result_json,'$.capacity_continue',json('true')) "
                  "WHERE id=?", (attempt['id'],))
        assert execution.capacity_continuation(c, attempt['job_id']) == 'kept'
        assert execution.login_refusal(c, None, machine['runner_id'], 'ops', {}, attempt['job_id']) == 'held'


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


def test_a_claim_expires_lapsed_leases_in_its_own_write_and_rechecks_one_job_under_the_lock(api, monkeypatch):
    # TK-INSRN53V (102 bots): expiry inside the claim's write, then the full selection it forced there, held the lock
    # 7-26 s. The route expires first in a short write; the read after it sees the requeued job; the claim's write
    # rechecks that one job and hands it out on this same claim.
    from backend.execution import Execution
    machine, message, lapsed = setup_attempt(api)
    expire(api, lapsed['id'])
    ready(api, machine, ['ops'])
    seen, inside = [], [False]
    original_claim, original_expire, original_candidate = Execution.claim, Execution.expire, Execution.candidate

    def claim_(self, c, *a, **k):
        inside[0] = True
        try:
            return original_claim(self, c, *a, **k)
        finally:
            inside[0] = False

    def expire_(self, c, **k):
        seen.append(('expire', inside[0], c.write_transaction))
        return original_expire(self, c, **k)

    def candidate_(self, c, *a, **k):
        seen.append(('candidate', inside[0], k.get('job_id')))
        return original_candidate(self, c, *a, **k)
    monkeypatch.setattr(Execution, 'claim', claim_)
    monkeypatch.setattr(Execution, 'expire', expire_)
    monkeypatch.setattr(Execution, 'candidate', candidate_)
    attempt = claim(api, machine)
    assert attempt and attempt['job_id'] == lapsed['job_id']
    assert ('expire', False, True) in seen                       # its own write, before the claim's
    assert not [s for s in seen if s[0] == 'expire' and s[1]]    # never under the claim's lock
    assert [s for s in seen if s[0] == 'candidate' and s[1]] == [('candidate', True, lapsed['job_id'])]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM attempts WHERE id=?", (lapsed['id'],)).fetchone()[0] == 'expired'


def test_an_idle_claim_with_nothing_lapsed_takes_no_write(api, monkeypatch):
    from backend.tests.test_api import runner
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    claim(api, machine)                                          # the contact stamp is due once
    store = api.app.state.store
    writes = []
    original = store.write
    monkeypatch.setattr(store, 'write', lambda *a, **k: writes.append(a) or original(*a, **k))
    assert claim(api, machine) is None and writes == []


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
    assert claim(api, machine) is None
    assert job_state(api, attempt['job_id']) == ('cancelled' if stopped else 'uncertain')
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
    post(api, 'tasks/' + task['id'], {'version': attempt['task']['version'], 'close': True, 'quiet': True})
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
    """Dev 242 R1: a person pressed Stop while the computer was asleep, before the hub marked the lease lapsed. No
    renewal carried it, the lease lapsed, the model finished anyway and the woken runner handed the answer in as
    completed. It is filed on its run, not posted, and the job ends stopped (cancelled), not completed, whether it was
    still uncertain or the person had dismissed it first."""
    from backend.tests.test_api import runner
    machine = runner(api, 'ana')
    for bot, dismissed in (('ops', False), ('finance', True)):
        assign(api, machine, bot)
        machine, message, attempt = finished_then_lapsed(api, machine, bot, stopped=not dismissed)
        aged(api, attempt['id'])
        if dismissed:
            post(api, f'bots/{bot}/stop', {'attempt_id': attempt['id']})
            post(api, f"jobs/{attempt['job_id']}/reconcile", {'attempt_id': attempt['id'], 'decision': 'dismiss',
                 'note': 'Stopped; nothing left to send.', 'acknowledge_uncertain_effects': True})
        filed = hand_in(api, machine, attempt)
        assert filed['filed'] is True and filed['message'] is None
        assert settled(api, attempt) == ('expired', 'cancelled')
        assert bot_replies(api, message, bot) == []


def stopped_notices(api, message, attempt):
    with api.app.state.store.read() as c:
        events = c.execute("SELECT count(*) FROM events WHERE action='attempt.stopped' AND target=?",
                           (attempt['id'],)).fetchone()[0]
    notices = [m['body'] for m in get(api, f"conversations/{message['conversation_id']}/messages")
               if m['kind'] == 'notice' and m['body'].startswith('Stopped by ')]
    return events, notices


def test_a_stop_pressed_after_the_lease_lapsed_is_recorded_and_the_late_answer_is_filed(api):
    """Dev 242 R2: the hub had already marked the sleeping turn's lease lapsed when the person pressed Stop on it. The
    Stop used to be refused ("not running") and the answer the model went on to finish was posted. Now it is recorded
    on the lapsed turn it names, and the late answer is filed on its run; the job ends as a stopped one does."""
    from backend.store import H
    machine, message, attempt = finished_then_lapsed(api)
    stopping = post(api, 'bots/ops/stop', {'attempt_id': attempt['id']})
    assert stopping == {'attempt_id': attempt['id'], 'stopping': True}
    # Nothing the runner reports brings the stopped turn back, within STALL_MAX or not.
    post(api, f"attempts/{attempt['id']}/inputs", {}, machine['token'], expected=409)
    filed = hand_in(api, machine, attempt)
    assert filed['filed'] is True and filed['message'] is None
    assert settled(api, attempt) == ('expired', 'cancelled')
    assert bot_replies(api, message) == []
    assert stopped_notices(api, message, attempt) == (1, ['Stopped by Ana'])
    with api.app.state.store.read() as c:
        assert H.status(c, 'ops')['state'] == 'idle'
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    # Never run again: not by the sweep past RESULT_GRACE, not by the next claim.
    aged(api, attempt['id'], grace=True)
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'cancelled'
    ready(api, machine, ['ops'])
    assert claim(api, machine) is None


def test_a_stop_after_the_lease_lapsed_takes_the_turns_id_and_a_job_still_on_it(api):
    """Only a lapsed turn named by its id takes a Stop, while its job is still on it, uncertain or let go. A Stop
    without an id never reaches an older lapsed run, one held for review included; a job queued to run again, or
    taken by a later run, is not the lapsed turn's to stop."""
    machine, message, held = interrupted(api)                          # acted on a host that can't say what it did
    aged(api, held['id'])
    assert sweep(api) == [] and job_state(api, held['job_id']) == 'uncertain'
    post(api, 'bots/ops/stop', {}, expected=409)
    post(api, 'bots/ops/stop', {'attempt_id': held['id']}, expected=409)  # left to its review
    assert sweep(api) == [] and job_state(api, held['job_id']) == 'uncertain'
    post(api, f"jobs/{held['job_id']}/reconcile", {'attempt_id': held['id'], 'decision': 'dismiss',
         'note': 'Checked what it did; nothing left to send here.', 'acknowledge_uncertain_effects': True})
    post(api, 'bots/ops/stop', {'attempt_id': held['id']})             # reviewed and let go: a Stop files its answer
    machine, message, attempt = finished_then_lapsed(api, machine)
    post(api, 'bots/ops/stop', {'attempt_id': attempt['id']}, token='cara-test', expected=403)
    aged(api, attempt['id'])
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'attempt_id': attempt['id'], 'decision': 'dismiss',
         'note': 'Looks stuck; nothing left to send here.', 'acknowledge_uncertain_effects': True})
    post(api, 'bots/ops/stop', {'attempt_id': attempt['id']})          # let go: its late answer is filed, not posted
    assert hand_in(api, machine, attempt)['filed'] is True and bot_replies(api, message) == []
    assert settled(api, attempt) == ('expired', 'cancelled')
    tool_reporting(api)
    machine, message, first = finished_then_lapsed(api, machine, text='Another summary.')
    aged(api, first['id'], grace=True)
    assert [d['state'] for d in sweep(api)] == ['queued']
    post(api, 'bots/ops/stop', {'attempt_id': first['id']}, expected=409)  # queued to run again
    ready(api, machine, ['ops'])
    assert claim(api, machine)['job_id'] == first['job_id']
    post(api, 'bots/ops/stop', {'attempt_id': first['id']}, expected=409)  # a later run took it


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


@pytest.mark.parametrize('shape', ['transferred', 'resumed', 'rival'])
def test_a_late_answer_filed_for_a_stop_leaves_a_job_this_run_no_longer_speaks_for(api, shape):
    """The Stop ends the job only while this run still speaks for it: still uncertain on this attempt, on the computer
    the bot is assigned to, with no other run live in its lane. A job moved to another computer, queued by a decision,
    or behind a live run is left as it was."""
    machine, message, attempt = finished_then_lapsed(api)
    post(api, 'bots/ops/stop', {'attempt_id': attempt['id']})
    if shape == 'transferred':
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE assignments SET generation=generation+1 WHERE bot='ops'")
    elif shape == 'resumed':
        aged(api, attempt['id'])
        post(api, f"jobs/{attempt['job_id']}/reconcile", {'attempt_id': attempt['id'], 'decision': 'resume',
             'note': 'The run only read; run it again.', 'acknowledge_uncertain_effects': True})
    else:
        rival(api, machine, attempt)
    before = job_state(api, attempt['job_id'])
    assert before == ('queued' if shape == 'resumed' else 'uncertain')
    filed = hand_in(api, machine, attempt)
    assert filed['filed'] is True and bot_replies(api, message) == []
    assert settled(api, attempt) == ('expired', before)
    assert stopped_notices(api, message, attempt) == (0, [])


def test_a_stopped_run_whose_computer_never_reported_back_is_dismissed_not_resumed(api):
    """Stop pressed, and the computer never woke: no answer, no interruption, only the lapsed lease. A run that only
    read would be resumed past RESULT_GRACE; a stopped one is let go by the person's choice instead."""
    tool_reporting(api)
    machine, message, attempt = finished_then_lapsed(api)
    post(api, 'bots/ops/stop', {'attempt_id': attempt['id']})
    aged(api, attempt['id'], grace=True)
    assert [d['state'] for d in sweep(api)] == ['cancelled']
    with api.app.state.store.read() as c:
        note = c.execute("SELECT note FROM job_recovery WHERE job_id=?", (attempt['job_id'],)).fetchone()[0]
    assert note == 'Stopped by Ana; not resumed.'
    ready(api, machine, ['ops'])
    assert claim(api, machine) is None
    # Should the answer still come, it is filed on its run, not posted.
    assert hand_in(api, machine, attempt)['filed'] is True and bot_replies(api, message) == []


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
    # A lapsed lease is the computer's, not a bug: the card says so once, and names the run on its own line.
    assert issue['detail'] == ('Its computer stopped answering during this run, not a bug. '
                               '2 queued requests wait until you decide.')
    assert issue['runs'] == [{'title': 'Archive the old bots', 'cause': 'timeout', 'why': 'Computer stopped answering',
                              'routine': True, 'retry': False, 'at': issue['runs'][0]['at'],
                              'jobs': [{'id': attempt['job_id'], 'attempt_id': attempt['id']}], 'decided': None}]
    assert issue['runs'][0]['at'] and issue['more_runs'] == 0 and issue['can_review'] is True
    post(api, f"jobs/{attempt['job_id']}/reconcile", {'attempt_id': attempt['id'], 'decision': 'dismiss',
         'note': 'The archive already ran; do not repeat it.', 'acknowledge_uncertain_effects': True})
    assert not [i for i in get(api, 'operations')['issues'] if i['kind'] == 'uncertain_work']
    assert claim(api, machine)                             # reviewed: the waiting work runs


def test_stopped_runs_in_needs_you_are_one_line_per_run_and_a_restart_says_it_is_not_a_bug(api, monkeypatch):
    """Oct 26 (Chris): a computer restart held Builder (OpenAI)'s runs, and Needs you showed "6 stopped runs" as one
    paragraph of quoted titles. A message folded into a run rides on its line (one choice settles both), the count is
    of runs, and the interruption is named with its time so the person knows it is not a bug."""
    from backend.store import H
    machine = runner(api, 'ana')
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    task = post(api, 'tasks', {'owner': 'ops', 'title': 'Ship the receipts fix', 'body': 'Push it and open the PR.'})
    task_id = task.get('task', task).get('id') or task['refs']['task']
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'drained'}, machine['token'])
    post(api, f"tasks/{task_id}/comments", {'text': 'Also rebase on main.'})
    carried = post(api, f"attempts/{attempt['id']}/inputs", {}, machine['token'])['messages']
    assert len(carried) == 1
    post(api, f"attempts/{attempt['id']}/inputs/{carried[0]['id']}/ack", {}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'tool': 'Bash', 'status': 'started', 'item_id': 'b1'}}]}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': 1,
         'text': 'Runner restarted during execution'}, machine['token'])
    post(api, f"tasks/{task_id}/comments", {'text': 'And tell me when it is up.'})   # waits behind the held run
    ready(api, machine, ['ops'])
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        finished = c.execute("SELECT finished FROM attempts WHERE id=?", (attempt['id'],)).fetchone()[0]
        jobs = [r[0] for r in c.execute("SELECT id FROM jobs WHERE attempt_id=? AND state='uncertain' ORDER BY created",
                                        (attempt['id'],))]
    assert len(jobs) == 2
    issue = stopped_issue(api)
    assert issue['title'].endswith('has 1 stopped run holding up work')
    assert issue['detail'] == (f'Its computer interrupted this run at {H.parse_ts(finished):%H:%M} UTC, usually for a '
                               'restart, not a bug. 1 queued request waits until you decide.')
    [line] = issue['runs']
    assert (line['title'], line['cause'], line['why'], line['routine']) == (
        'Ship the receipts fix', 'interrupted', 'Interrupted on its computer', True)
    assert sorted(j['id'] for j in line['jobs']) == sorted(jobs)
    assert {j['attempt_id'] for j in line['jobs']} == {attempt['id']}
    # A run past the listed ones may have stopped some other way: the shared "not a bug" line needs every run shown.
    from backend import execution
    listed = execution.stopped_rows
    def one_more_run(*args, **kwargs):
        rows, runs = listed(*args, **kwargs)
        return rows, runs + 1
    monkeypatch.setattr(execution, 'stopped_rows', one_more_run)
    issue = stopped_issue(api)
    assert issue['more_runs'] == 1 and issue['detail'] == '1 queued request waits until you decide.'
    monkeypatch.setattr(execution, 'stopped_rows', listed)
    for job in line['jobs']:
        post(api, f"jobs/{job['id']}/reconcile", {'attempt_id': job['attempt_id'], 'decision': 'resume',
             'note': 'Check what already happened, then continue.', 'acknowledge_uncertain_effects': True})
    assert not [i for i in get(api, 'operations')['issues'] if i['kind'] == 'uncertain_work']


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
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'tool': 'Bash', 'status': 'started', 'item_id': 'b1'}}]}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': 1,
         'text': 'Runner restarted during execution'}, machine['token'])
    post(api, f"tasks/{task_id}/comments", {'text': 'And tell me when it is up.'})   # waits behind the held run
    rival(api, machine, attempt)
    issue = stopped_issue(api)
    print("ISSUE", issue)
    [line] = issue["runs"]
    assert len(line['jobs']) == 2 and line['decided'] is None and all('decided' not in j for j in line['jobs'])
    return line['jobs']


def beside_open_work(monkeypatch):
    """Another stopped run that still holds work up keeps the bot's card listing its runs: a run whose every request
    has a saved choice holds nothing up (execution.held_back), so alone it leaves the card."""
    from backend import execution
    monkeypatch.setattr(execution, 'held_back', lambda *args, **kwargs: (1, ['Another task']))


def test_a_folded_request_that_is_unsafe_to_retry_keeps_its_run_off_try_again(api, monkeypatch):
    """One choice settles every request folded into a run, so the run's line is only as safe as its least safe
    request: a folded comment whose message used an approval keeps the run off Try again."""
    from backend import execution
    first, second = busy_folded_run(api)
    safe = {'safe': True, 'starts': 1, 'update': None, 'outside': [], 'consumed': 0, 'fallback_unknown': False}
    monkeypatch.setattr(execution, 'retry_facts', lambda c, job, started, finished: {
        **safe, **({'safe': False, 'consumed': 1} if job['id'] == second['id'] else {})})
    line = run_line(api)
    assert [j['id'] for j in line['jobs']] == [first['id'], second['id']]
    assert line['retry'] is False
    monkeypatch.setattr(execution, 'retry_facts', lambda c, job, started, finished: safe)
    assert run_line(api)['retry'] is True


def test_a_choice_saved_while_the_bot_is_busy_shows_on_its_stopped_run_line(api, monkeypatch):
    """Dev 561: Try again on a Needs you line while the bot's lane is busy saves the choice and leaves the run held, so
    a refreshed card listed it as undecided and a second click was refused (409). Each folded request carries the
    choice saved for it; the line carries it once every request it settles has one, so the card shows the outcome."""
    first, second = busy_folded_run(api)
    body = {'decision': 'resume', 'note': 'Check what already happened, then continue.',
            'acknowledge_uncertain_effects': True}
    assert post(api, f"jobs/{first['id']}/reconcile", {**body, 'attempt_id': first['attempt_id']})['deferred'] is True
    # Partway: the saved request says so, the other is still to decide, so the line still offers its choice.
    issue = stopped_issue(api)
    print("ISSUE", issue)
    [line] = issue["runs"]
    assert [j.get('decided') for j in line['jobs']] == ['resume', None] and line['decided'] is None
    assert post(api, f"jobs/{second['id']}/reconcile", {**body, 'attempt_id': second['attempt_id']})['deferred'] is True
    assert 'runs' not in stopped_issue(api)
    beside_open_work(monkeypatch)
    issue = stopped_issue(api)
    print("ISSUE", issue)
    [line] = issue["runs"]
    assert [j['decided'] for j in line['jobs']] == ['resume', 'resume'] and line['decided'] == 'resume'
    # A reader who may not decide still sees that a choice is saved, without the handles.
    [seen] = stopped_issue(api, 'ben-test')['runs']
    assert seen['decided'] == 'resume' and 'jobs' not in seen


def test_a_run_whose_folded_requests_got_different_saved_choices_is_settled(api, monkeypatch):
    """The Details card decides each folded request on its own, so one can be saved as Try again and the other as
    Don't retry. The line is still settled ("mixed"): without it the card offered a choice that sends nothing."""
    first, second = busy_folded_run(api)
    body = {'note': 'Each request was decided on its own.', 'acknowledge_uncertain_effects': True}
    for job, decision in ((first, 'resume'), (second, 'dismiss')):
        assert post(api, f"jobs/{job['id']}/reconcile",
                    {**body, 'decision': decision, 'attempt_id': job['attempt_id']})['deferred'] is True
    beside_open_work(monkeypatch)
    issue = stopped_issue(api)
    print("ISSUE", issue)
    [line] = issue["runs"]
    assert [j['decided'] for j in line['jobs']] == ['resume', 'dismiss'] and line['decided'] == 'mixed'


def test_a_stops_cause_comes_from_what_the_run_recorded_not_from_its_text():
    """A Billing task's answer that says "quota" is not a usage limit; only the run's own fields say why it stopped."""
    from backend.execution import ROUTINE_STOPS, stop_cause
    def attempt(state='failed', stop=None, **result):
        return {'state': state, 'stop_requested': stop, 'result_json': json.dumps(result)}
    assert stop_cause(attempt(text='Billing: the quota page and the usage limit table are done')) == 'partway'
    assert stop_cause(attempt(limited=True)) == 'quota'
    assert stop_cause(attempt(primary_limited=True)) == 'partway'  # fell back to another model, then failed
    assert stop_cause(attempt('interrupted', primary_limited=True)) == 'interrupted'
    assert stop_cause(attempt(limit_source={'kind': 'capacity'})) == 'capacity'
    assert stop_cause(attempt('interrupted')) == 'interrupted'
    assert stop_cause(attempt('expired')) == 'timeout'
    assert stop_cause(attempt('interrupted', stop='human:ana')) == 'stopped'
    assert stop_cause(None) == 'partway'
    assert stop_cause(attempt('expired'), update=True) == 'update'   # the update's lapse, not a lost computer
    assert stop_cause(attempt('interrupted', limited=True), update=True) == 'quota'  # a usage limit still says so
    assert {'stopped', 'partway'}.isdisjoint(ROUTINE_STOPS) and 'update' in ROUTINE_STOPS


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
    # Each count checks its rows' privacy in one batched pass, the one Ben may not read included.
    from backend import execution
    from backend import task_privacy as privacy
    store, calls, real = api.app.state.store, [], privacy.readable_messages

    def counted(c, actor, messages):
        rows = list(messages)
        calls.append(len(rows))
        return real(c, actor, rows)

    seen = {}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(privacy, 'readable_messages', counted)
        for token in ('ana-test', 'ben-test'):
            who = store.settings.test_identities[token]
            calls.clear()
            with store.read_transaction() as c:
                seen[token] = (execution.held_back(c, who, api.app.state.auth, 'ops')[0],
                               execution.left_by_keeper(c, who, 'ops'), execution.unreviewed(c, who, 'ops')[0])
            assert len(calls) == 3 and calls[:2] == [1, 1], calls
    assert seen['ana-test'][:2] == (1, 1) and seen['ben-test'][:2] == (0, 0), seen


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
    assert issue['detail'].endswith("Tico can't safely decide it on its own.")
    assert [r['title'] for r in issue['runs']] == ['A chat request']           # a chat, not an unreadable task
    assert [j['id'] for j in get(api, 'bots/ops/execution-review')['jobs']]


@pytest.mark.parametrize('reported', [True, False])
def test_a_stop_on_a_lapsed_turn_ends_only_its_request_not_a_message_folded_into_it(api, reported):
    """A follow-up folded into the turn is not what the person stopped. Handed back by the late result as never given
    to the runtime, it is queued for the next turn; with no result at all, the sweep lets the request go and judges
    the folded message as before: a turn that only read is resumed for it."""
    tool_reporting(api)
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'folded'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}}]}, machine['token'])
    follow = post(api, 'chat/ops', {'text': 'Also include the weekly numbers'})
    assert [m['id'] for m in post(api, f"attempts/{attempt['id']}/inputs", {}, machine['token'])['messages']] == [follow['id']]
    expire(api, attempt['id'])
    assert claim(api, machine) is None
    post(api, 'bots/ops/stop', {'attempt_id': attempt['id']})
    if reported:
        filed = post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'completed', 'last_seq': 1,
                     'text': 'Done.', 'undelivered': [follow['id']]}, machine['token'])
        assert filed['filed'] is True and job_state(api, attempt['job_id']) == 'cancelled'
        assert job_state(api, follow['id']) == 'queued'
        ready(api, machine, ['ops'])
        assert claim(api, machine)['job_id'] == follow['id']
    else:
        aged(api, attempt['id'], grace=True)
        assert sorted(d['state'] for d in sweep(api)) == ['cancelled', 'queued']
        assert job_state(api, attempt['job_id']) == 'cancelled' and job_state(api, follow['id']) == 'queued'


@pytest.mark.parametrize('decision,expected', [('resume', 'queued'), ('dismiss', 'cancelled')])
def test_review_choice_survives_a_busy_lane_and_applies_once_idle(api, decision, expected):
    """The operator's explicit choice is durable, fences late output and waits only for its own lane."""
    machine, message, attempt = interrupted(api)
    other = rival(api, machine, attempt)
    body = {'attempt_id': attempt['id'], 'decision': decision,
            'note': 'Reviewed outside effects; apply this choice.', 'acknowledge_uncertain_effects': True}
    post(api, f"jobs/{attempt['job_id']}/reconcile", body, token=machine['token'], expected=403)
    post(api, f"jobs/{attempt['job_id']}/reconcile", {**body, 'attempt_id': other['id']}, expected=409)
    path = f"jobs/{attempt['job_id']}/reconcile"
    result = post(api, path, body, key='first-choice')
    assert result == {'job_id': attempt['job_id'], 'state': 'uncertain', 'deferred': True}
    review = next(j for j in get(api, 'bots/ops/execution-review')['jobs'] if j['id'] == attempt['job_id'])
    assert review['pending_decision'] == {'decision': decision}
    with api.app.state.store.read() as c:
        saved = dict(c.execute("SELECT * FROM job_recovery WHERE job_id=?", (attempt['job_id'],)).fetchone())
    assert saved['decision'] == decision and saved['actor'] == 'human:ana'
    conflict = {**body, 'decision': 'dismiss' if decision == 'resume' else 'resume',
                'note': 'A stale dialog submitted the opposite choice.'}
    assert post(api, path, body, key='first-choice') == result
    refused = post(api, path, conflict, key='stale-while-busy', expected=409)
    assert refused['error']['code'] == 'uncertain'
    with api.app.state.store.read() as c:
        assert dict(c.execute("SELECT * FROM job_recovery WHERE job_id=?", (attempt['job_id'],)).fetchone()) == saved
    filed = post(api, f"attempts/{attempt['id']}/complete",
                 {'outcome': 'completed', 'last_seq': 1, 'text': 'A stale final answer.'}, machine['token'])
    assert filed['filed'] is True and bot_replies(api, message) == []
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    post(api, f"attempts/{other['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Other work done.'},
         machine['token'])
    # An idle lane is still held for the accepted choice until the scheduler applies it.
    refused = post(api, path, conflict, key='stale-before-sweep', expected=409)
    assert refused['error']['code'] == 'uncertain'
    with api.app.state.store.read() as c:
        assert dict(c.execute("SELECT * FROM job_recovery WHERE job_id=?", (attempt['job_id'],)).fetchone()) == saved
    assert job_state(api, attempt['job_id']) == 'uncertain'
    assert [d['state'] for d in sweep(api)] == [expected]
    assert job_state(api, attempt['job_id']) == expected
    with api.app.state.store.read() as c:
        applied = dict(c.execute("SELECT * FROM job_recovery WHERE job_id=?", (attempt['job_id'],)).fetchone())
    assert {k: applied[k] for k in ('decision', 'note', 'actor')} == {k: saved[k] for k in ('decision', 'note', 'actor')}
    assert sweep(api) == []
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    post(api, path, body, expected=409)


def test_a_busy_bots_stopped_run_records_dependency_until_task_and_lane_free(api):
    """A busy task or shared lane defers recovery; once free the sweep releases fenced queued work."""
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    stopped = post(api, 'tasks', {'owner': 'ops', 'title': 'Read the notes', 'body': 'Read the notes.'})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'stopped'}, machine['token'])
    events = [{'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}},
              {'seq': 2, 'kind': 'tool', 'payload': {'tool': 'Read', 'status': 'completed', 'item_id': 'call-0'}}]
    post(api, f"attempts/{attempt['id']}/events", {'events': events}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': 2,
         'text': 'Runner restarted during execution'}, machine['token'])
    post(api, f"tasks/{stopped['id']}/comments", {'text': 'Any news?', 'needs_action': True})   # fenced behind it
    busy = post(api, 'tasks', {'owner': 'ops', 'title': 'Something else', 'body': 'Do something else.'})
    other = claim(api, machine)
    post(api, f"attempts/{other['id']}/started", {'thread_id': 'busy'}, machine['token'])

    def counts():
        return get(api, 'pipeline')['queues']['ops'], get(api, 'bots/ops')['queued']

    queue, page = counts()
    assert (queue['uncertain'], queue['dead'], queue['queued']) == (1, 1, 0) and page == 0      # dead is never queued

    # A live run on the same task still makes it wait: that run may be doing the same work.
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE turns SET task_id=? WHERE id=?', (stopped['id'], other['id']))
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'

    # A different task still owns this shared main lane, so record its dependency until it ends.
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE turns SET task_id=? WHERE id=?', (busy['id'], other['id']))
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    post(api, f"attempts/{other['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Done.'}, machine['token'])
    assert [d['state'] for d in sweep(api)] == ['queued']
    queue, page = counts()
    assert (queue['uncertain'], queue['dead'], queue['queued']) == (0, 0, 2) and page == 2


@pytest.mark.parametrize('live_match', [
    'same_job', 'folded_input', 'same_task', 'no_task_conversation',
    'different_task_same_main_lane', 'different_task_shared_session',
    'same_task_without_historical_attempt',
])
def test_auto_reconcile_defers_only_for_the_live_work_identity_that_matches(api, live_match):
    """Each live-work identity is independently tested so only a matching live run delays the decision.

    The historical-attempt case also protects the same task while the uncertain job has no attempt row to join.
    A distinct task sharing a non-main session lane must wait too; a different task on another lane is covered by
    test_a_busy_bots_stopped_run_records_dependency_until_task_and_lane_free. Shared main also waits.
    """
    from backend.execution import STALL_MAX
    from backend.store import H

    tool_reporting(api)
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    target_task = None
    if live_match == 'no_task_conversation':
        target = post(api, 'chat/ops', {'text': 'A stopped request'})
    else:
        target_task = post(api, 'tasks', {'owner': 'ops', 'title': 'Stopped request', 'body': 'Read this.'})
        target = target_task
    target_attempt = claim(api, machine)
    post(api, f"attempts/{target_attempt['id']}/started", {'thread_id': 'stopped'}, machine['token'])
    post(api, f"attempts/{target_attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}}]}, machine['token'])
    post(api, f"attempts/{target_attempt['id']}/complete", {
        'outcome': 'interrupted', 'last_seq': 1, 'text': 'Runner restarted'}, machine['token'])
    with api.app.state.store.read() as c:
        target_message = dict(c.execute('SELECT m.id,m.conversation_id FROM messages m JOIN jobs j '
                                        'ON j.message_id=m.id WHERE j.id=?',
                                        (target_attempt['job_id'],)).fetchone())

    other_task = post(api, 'tasks', {'owner': 'ops', 'title': 'Different task', 'body': 'Continue elsewhere.'})
    live_attempt = claim(api, machine)
    assert live_attempt and live_attempt['job_id'] != target_attempt['job_id']

    with api.app.state.store.transaction() as c:
        if live_match == 'same_job':
            c.execute('UPDATE attempts SET job_id=? WHERE id=?', (target_attempt['job_id'], live_attempt['id']))
        elif live_match == 'folded_input':
            c.execute('INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)',
                      (live_attempt['id'], target_message['id']))
        elif live_match == 'same_task':
            c.execute('UPDATE turns SET task_id=? WHERE id=?', (target_task['id'], live_attempt['id']))
        elif live_match == 'no_task_conversation':
            c.execute('INSERT INTO attempt_conversations(attempt_id,conversation_id) VALUES(?,?)',
                      (live_attempt['id'], target_message['conversation_id']))
        elif live_match == 'different_task_same_main_lane':
            assert target_task['id'] != other_task['id']
            c.execute('UPDATE turns SET task_id=? WHERE id=?', (other_task['id'], live_attempt['id']))
            c.execute("UPDATE attempts SET lane='main' WHERE id IN (?,?)",
                      (target_attempt['id'], live_attempt['id']))
        elif live_match == 'different_task_shared_session':
            assert target_task['id'] != other_task['id']
            c.execute('UPDATE turns SET task_id=? WHERE id=?', (other_task['id'], live_attempt['id']))
            c.execute("UPDATE attempts SET lane='task:shared-root' WHERE id IN (?,?)",
                      (target_attempt['id'], live_attempt['id']))
        elif live_match == 'same_task_without_historical_attempt':
            c.execute('UPDATE jobs SET attempt_id=NULL,created=? WHERE id=?',
                      (H.shift(H.now(), seconds=-STALL_MAX-1), target_attempt['job_id']))
            c.execute('UPDATE turns SET task_id=? WHERE id=?', (target_task['id'], live_attempt['id']))

    assert sweep(api) == [] and job_state(api, target_attempt['job_id']) == 'uncertain'
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='expired',finished=? WHERE id=?", (H.now(), live_attempt['id']))
        c.execute('UPDATE turns SET finished=? WHERE id=?', (H.now(), live_attempt['id']))
    if live_match == 'same_task_without_historical_attempt':
        # With no attempt row, the sweep has no evidence from which to make a recovery decision.
        assert sweep(api) == [] and job_state(api, target_attempt['job_id']) == 'uncertain'
        with api.app.state.store.read() as c:
            assert not c.execute('SELECT 1 FROM job_recovery WHERE job_id=?',
                                 (target_attempt['job_id'],)).fetchone()
    else:
        assert [d['state'] for d in sweep(api)] == ['queued']
        assert job_state(api, target_attempt['job_id']) == 'queued'


def add_recovery_reviewer(api):
    from backend.store import H
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,state) VALUES(?, 'BotOps', 'active')",
                  (H.FLEET_MAINTAINER,))


def test_restart_after_shell_resumes_on_next_scheduler_tick_with_cut_time(api, monkeypatch):
    from backend.scheduler import Scheduler
    from backend.store import H
    add_recovery_reviewer(api)
    machine, message, attempt = restarted_mid_turn(api, [('Bash', None)])
    with api.app.state.store.read() as c:
        cut = c.execute("SELECT finished FROM attempts WHERE id=?", (attempt['id'],)).fetchone()[0]
    monkeypatch.setattr(H, 'now', lambda: H.shift(cut, seconds=30))
    Scheduler(api.app.state.store, api.app.state.execution).tick()
    assert job_state(api, attempt['job_id']) == 'queued'
    ready(api, machine, ['ops'])
    resumed = claim(api, machine)
    assert resumed['job_id'] == attempt['job_id']
    assert resumed['recovery'] == {'resume_session': True}
    assert f"You were interrupted at {cut[11:16]} UTC" in resumed['message']['body']
    assert 'Check what already happened first' in resumed['message']['body']
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner='bot:botops'").fetchone()
        assert not c.execute("SELECT 1 FROM events WHERE action='job.held' AND target=?", (attempt['id'],)).fetchone()


def test_restart_with_named_merge_is_held_immediately_and_review_names_it(api):
    add_recovery_reviewer(api)
    machine, message, attempt = restarted_mid_turn(api, [('Bash', None)])
    with api.app.state.store.transaction() as c:
        # The command is recorded at tool start, so a lost result cannot hide an outside effect.
        c.execute("UPDATE attempt_events SET payload_json=json_set(payload_json,'$.summary',?) "
                  "WHERE attempt_id=? AND kind='tool' AND json_extract(payload_json,'$.status')='started'",
                  ('gh pr merge 42 --squash --body private-marker-42', attempt['id']))
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert 'merge pull request' in review['reason'] and review['stopped'] == 'changed'
    assert 'private-marker-42' not in review['reason'] and '--squash' not in review['reason']
    with api.app.state.store.read() as c:
        tasks = c.execute("SELECT body FROM tasks WHERE owner='bot:botops'").fetchall()
    assert len(tasks) == 1 and 'merge pull request' in tasks[0]['body']
    assert 'private-marker-42' not in tasks[0]['body'] and '--squash' not in tasks[0]['body']


def test_saved_dismiss_finishes_review_before_main_lane_frees_and_preserves_save_time(api, monkeypatch):
    from backend.scheduler import Scheduler
    from backend.store import H
    add_recovery_reviewer(api)
    machine, message, attempt = interrupted(api)
    aged(api, attempt['id'])
    assert sweep(api) == []
    other = rival(api, machine, attempt)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lane='main' WHERE id IN (?,?)", (attempt['id'], other['id']))
    saved_at = H.now()
    result = post(api, f"jobs/{attempt['job_id']}/reconcile", {
        'attempt_id': attempt['id'], 'decision': 'dismiss',
        'note': 'The work landed already. Dismiss this stopped request.', 'acknowledge_uncertain_effects': True})
    assert result['deferred'] is True
    with api.app.state.store.read() as c:
        assert c.execute("SELECT status FROM tasks WHERE owner='bot:botops'").fetchone()[0] == 'done'
        saved = dict(c.execute("SELECT * FROM job_recovery WHERE job_id=?", (attempt['job_id'],)).fetchone())
    assert sweep(api) == []
    monkeypatch.setattr(H, 'now', lambda: H.shift(saved_at, seconds=60))
    post(api, f"attempts/{other['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Finished.'}, machine['token'])
    Scheduler(api.app.state.store, api.app.state.execution).tick()
    assert job_state(api, attempt['job_id']) == 'cancelled'
    with api.app.state.store.read() as c:
        assert dict(c.execute("SELECT * FROM job_recovery WHERE job_id=?", (attempt['job_id'],)).fetchone()) == saved


def test_restart_report_after_lease_expiry_still_resumes_without_waiting_for_result_grace(api):
    machine, message, attempt = restarted_mid_turn(api, [('Bash', None)], reported=False)
    # The computer is back but another run temporarily owns its lane. Its restart report is filed on the
    # expired attempt rather than changing that lease's historical state.
    other = rival(api, machine, attempt)
    with api.app.state.store.read() as c:
        seq = c.execute("SELECT last_seq FROM attempts WHERE id=?", (attempt['id'],)).fetchone()[0]
    filed = post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': seq,
                 'text': 'Runner restarted during execution'}, machine['token'])
    assert filed['filed'] is True
    post(api, f"attempts/{other['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Finished.'}, machine['token'])
    assert [d['state'] for d in sweep(api)] == ['queued']


def test_restart_with_incomplete_tool_reporting_keeps_explicit_restart_policy(api):
    add_recovery_reviewer(api)
    machine, message, attempt = restarted_mid_turn(api, [('Read', None)], hop=True)
    assert [d['state'] for d in sweep(api)] == ['queued']
    ready(api, machine, ['ops'])
    resumed = claim(api, machine)
    assert resumed['recovery'] == {'resume_session': True}
    assert 'Check what already happened first' in resumed['message']['body']
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner='bot:botops'").fetchone()


def test_restart_uses_start_evidence_and_does_not_hold_internal_shell_writes(api):
    add_recovery_reviewer(api)
    machine, message, attempt = restarted_mid_turn(api, [('Bash', None)])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempt_events SET payload_json=json_set(payload_json,'$.summary',?) "
                  "WHERE attempt_id=? AND kind='tool' AND json_extract(payload_json,'$.status')='started'",
                  ('hub task create --title report', attempt['id']))
        c.execute("UPDATE attempt_events SET payload_json=json_set(payload_json,'$.summary',?) "
                  "WHERE attempt_id=? AND kind='tool' AND json_extract(payload_json,'$.status')='completed'",
                  ('The instructions mention npm publish and hub mail send', attempt['id']))
    assert [d['state'] for d in sweep(api)] == ['queued']


@pytest.mark.parametrize('tool,label', [
    ('mcp__hub__hub_release_now', 'publish release'),
    ('mcp__hub__hub_webhook_send', 'send webhook'),
    ('mcp__hub__hub_support_send', 'send support report'),
])
def test_restart_with_hub_publish_or_send_call_is_held_and_names_it(api, tool, label):
    """Hub publish/send tools recorded with their runner prefix hold a restarted run and name the operation."""
    add_recovery_reviewer(api)
    machine, message, attempt = restarted_mid_turn(api, [(tool, None)])
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert label in review['reason'] and review['stopped'] == 'changed'


def runner_shell_cases():
    from pathlib import Path
    return json.loads((Path(__file__).resolve().parents[2] /
                       'runner-rs/tests/fixtures/recovery-shell-events.json').read_text())


@pytest.mark.parametrize('case', runner_shell_cases(), ids=lambda case: case['command'])
@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_restart_shell_actions_use_real_runner_events(api, case, host):
    """Rust host tests produce these exact payloads from provider start items; no full command summary exists."""
    add_recovery_reviewer(api)
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'restarted'}, machine['token'])
    payload = case[host]
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'status', 'payload': {'state': 'active', 'tool_events': True}},
        {'seq': 2, 'kind': 'tool', 'payload': payload}]}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': 2,
         'text': 'Runner restarted during execution'}, machine['token'])
    if payload['outside_actions']:
        assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
        review = get(api, 'bots/ops/execution-review')['jobs'][0]
        assert all(label in review['reason'] for label in payload['outside_actions'])
        with api.app.state.store.read() as c:
            body = c.execute("SELECT body FROM tasks WHERE owner='bot:botops'").fetchone()[0]
        assert all(label in body for label in payload['outside_actions'])
        assert 'synthetic-private-marker' not in body + review['reason']
    else:
        assert [d['state'] for d in sweep(api)] == ['queued']
        with api.app.state.store.read() as c:
            assert not c.execute("SELECT 1 FROM tasks WHERE owner='bot:botops'").fetchone()


@pytest.mark.parametrize('program', ['gh', 'npm', 'docker', 'hub', 'stripe'])
@pytest.mark.parametrize('labels', [None, ['synthetic-private-marker']])
def test_restart_legacy_program_only_event_does_not_hide_possible_action(api, program, labels):
    add_recovery_reviewer(api)
    machine, message, attempt = restarted_mid_turn(api, [('Bash', None)])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempt_events SET payload_json=json_set(payload_json,'$.summary',?) "
                  "WHERE attempt_id=? AND kind='tool' AND json_extract(payload_json,'$.status')='started'",
                  (program, attempt['id']))
        if labels is not None:
            c.execute("UPDATE attempt_events SET payload_json=json_set(payload_json,'$.outside_actions',json(?)) "
                      "WHERE attempt_id=? AND kind='tool' AND json_extract(payload_json,'$.status')='started'",
                      (json.dumps(labels), attempt['id']))
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert 'older computer omitted command details' in review['reason']
    assert 'synthetic-private-marker' not in review['reason']


def test_restart_read_only_run_ignores_another_runs_approval_and_merge(api):
    """An approval another message consumed and a merge another run caused never hold this run."""
    from backend.store import H
    add_recovery_reviewer(api)
    machine, message, attempt = restarted_mid_turn(api, [('Read', None)])
    with api.app.state.store.transaction() as c:
        started = c.execute("SELECT started FROM attempts WHERE id=?", (attempt['id'],)).fetchone()[0]
        # Another lane's approval, consumed while this run was cut off, names another message.
        c.execute("INSERT INTO approvals(id,kind,message_id,requested_by,created,consumed_at) VALUES(?,?,?,?,?,?)",
                  (H.new_id(), 'spend', 'message-elsewhere', 'bot:ops', started, started))
        # Another run's merge on this run's task, inside the report window.
        task = H.task_create(c, 'human:ana', 'Something else', 'Do something else.', 'bot:ops')
        c.execute("INSERT INTO task_links(id,task_id,kind,url,created,pr_merged_at) VALUES(?,?,?,?,?,?)",
                  (H.new_id(), task['id'], 'pr', 'https://example.com/acme/tico/pull/9', started, H.now()))
        # The claim already recorded this run's turn under its attempt id; point it at the task.
        c.execute("UPDATE turns SET task_id=? WHERE id=?", (task['id'], attempt['id']))
    assert [d['state'] for d in sweep(api)] == ['queued']
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner='bot:botops'").fetchone()


def test_restart_run_that_consumed_its_own_approval_is_held(api):
    from backend.store import H
    add_recovery_reviewer(api)
    machine, message, attempt = restarted_mid_turn(api, [('Read', None)])
    with api.app.state.store.transaction() as c:
        started = c.execute("SELECT started FROM attempts WHERE id=?", (attempt['id'],)).fetchone()[0]
        c.execute("INSERT INTO approvals(id,kind,message_id,requested_by,created,consumed_at) VALUES(?,?,?,?,?,?)",
                  (H.new_id(), 'spend', message['id'], 'bot:ops', started, started))
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'uncertain'
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert review['stopped'] == 'changed' and 'approval' in review['reason']


def test_restart_safe_shell_run_card_names_the_restart_not_only_read(api):
    """A restarted shell run with no named outside action may resume, but its card never says it only read."""
    add_recovery_reviewer(api)
    machine, message, attempt = restarted_mid_turn(api, [('Bash', None)])
    review = get(api, 'bots/ops/execution-review')['jobs'][0]
    assert review['stopped'] == 'restarted'
    assert 'only read' not in review['reason']
    assert [d['state'] for d in sweep(api)] == ['queued']


def test_saved_resume_applies_when_main_lane_frees(api, monkeypatch):
    from backend.scheduler import Scheduler
    from backend.store import H
    add_recovery_reviewer(api)
    machine, message, attempt = interrupted(api)
    aged(api, attempt['id'])
    assert sweep(api) == []
    other = rival(api, machine, attempt)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lane='main' WHERE id IN (?,?)", (attempt['id'], other['id']))
    saved_at = H.now()
    result = post(api, f"jobs/{attempt['job_id']}/reconcile", {
        'attempt_id': attempt['id'], 'decision': 'resume',
        'note': 'The work has not landed yet. Run it again when the lane is free.',
        'acknowledge_uncertain_effects': True})
    assert result['deferred'] is True
    assert sweep(api) == []
    monkeypatch.setattr(H, 'now', lambda: H.shift(saved_at, seconds=60))
    post(api, f"attempts/{other['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Finished.'}, machine['token'])
    Scheduler(api.app.state.store, api.app.state.execution).tick()
    assert job_state(api, attempt['job_id']) == 'queued'
    with api.app.state.store.read() as c:
        saved = dict(c.execute("SELECT * FROM job_recovery WHERE job_id=?", (attempt['job_id'],)).fetchone())
    assert saved['decision'] == 'resume'
