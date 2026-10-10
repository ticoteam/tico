"""Operator review preserves evidence and fences stale or unauthorized decisions."""
import pytest
from backend.tests.test_api import api, assign, setup_attempt, post, get, expire, ready, claim, headers


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
    assert 'token_hash' not in result['attempt'] and 'token' not in result['attempt']


def limited_turn(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {'thread_id':'limited'}, machine['token'])
    return post(api, f"attempts/{attempt['id']}/complete",
                {'outcome':'failed','last_seq':0,'limited':True,'text':"You've hit your usage limit."}, machine['token'])


def test_usage_limited_turn_requeues_and_cools_off_longer_after_a_third_strike(api):
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    assert limited_turn(api, machine, attempt)['outcome'] == 'failed'
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
        status = H.status(c, 'ops')
        assert status['state'] == 'limited' and status['focus'].startswith('fake usage limit at ')
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.limited' AND target=?", (attempt['id'],)).fetchone()[0] == 1
    issues = api.get('/api/status', headers=headers()).json()['health_issues']
    issue = next(i for i in issues if i['kind'] == 'bot' and i['bot'] == 'ops')
    assert issue['detail'] == 'fake usage limit; retrying automatically'
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    # Inside the cooldown nothing is handed out; after it the same job comes back.
    assert claim(api, machine) is None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_status SET since=? WHERE bot='ops'", (H.shift(H.now(), seconds=-1801),))
    second = claim(api, machine)
    assert second and second['job_id'] == attempt['job_id'] and second['id'] != attempt['id']
    limited_turn(api, machine, second)
    with api.app.state.store.transaction() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
        # The second limit restarted the 30-minute cooldown even though the state did not change.
        assert H.status(c, 'ops')['since'] > H.shift(H.now(), seconds=-5)
        c.execute("UPDATE bot_status SET since=? WHERE bot='ops'", (H.shift(H.now(), seconds=-1801),))
    third = claim(api, machine)
    assert third and third['job_id'] == attempt['job_id']
    limited_turn(api, machine, third)
    # A third limit in a row never becomes uncertain: the job stays queued behind a longer
    # cooldown, and Needs attention says when it retries instead of asking for a review.
    with api.app.state.store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0] == 'queued'
        status = H.status(c, 'ops')
        assert status['state'] == 'limited' and 'third in a row' in status['focus']
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    issue = next(i for i in api.get('/api/status', headers=headers()).json()['health_issues'] if i['kind'] == 'bot' and i['bot'] == 'ops')
    assert issue['detail'].startswith('fake usage limit three times in a row; queued work waits until ')
    assert claim(api, machine) is None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_status SET since=? WHERE bot='ops'", (H.shift(H.now(), seconds=-1801),))
    assert claim(api, machine) is None                      # 30 minutes is not enough this time
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_status SET since=? WHERE bot='ops'", (H.shift(H.now(), seconds=-7201),))
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
    aged(api, attempt['id'])
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


def aged(api, attempt_id):
    """Push the interrupted attempt past STALL_MAX, so the runner's own restore no longer applies."""
    from backend.execution import STALL_MAX
    from backend.store import H
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE attempts SET finished=? WHERE id=?', (H.shift(H.now(), seconds=-STALL_MAX - 1), attempt_id))


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


def test_a_stopped_run_that_used_tools_resumes_by_itself_with_what_it_saved(api):
    """Ana, 2026-09-25: no person answers "did this run finish an outside action?". The bot gets
    what the run saved, checks what is done, and finishes only the rest."""
    from backend.store import H
    machine, message, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'stopped'}, machine['token'])
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'name': 'shell'}},
        {'seq': 2, 'kind': 'delta', 'payload': {'text': 'Arch'}},
        {'seq': 3, 'kind': 'message', 'payload': {'text': 'Archived both bots; telling Priya next.'}}]},
        machine['token'])
    expire(api, attempt['id'])
    assert claim(api, machine) is None and job_state(api, attempt['job_id']) == 'uncertain'
    aged(api, attempt['id'])
    assert [d['state'] for d in sweep(api)] == ['queued']
    ready(api, machine, ['ops'])
    again = claim(api, machine)
    body = again['message']['body']
    assert 'finish only what is left' in body and 'Archived both bots; telling Priya next.' in body
    assert 'Arch\n' not in body, "whole messages, not streaming fragments"
    # It stops a second time: dismissed, and BotOps (not a person) finds out why.
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
        task = c.execute("SELECT owner FROM tasks WHERE title=?", ("Find why ops's runs keep stopping",)).fetchone()
    assert task and task['owner'] == H.bot_actor(H.FLEET_MAINTAINER)
    assert get(api, 'bots/ops/execution-review')['jobs'] == []


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
