"""Quarantine stops preserve their cause and requeue work until the bot is active."""
from backend import run_diagnostics
from backend.store import H
from backend.tests.test_api import api, claim, expire, get, post, ready, setup_attempt  # noqa: F401 (api: the fixture)
from backend.tests.test_execution_review import aged, job_state, sweep

LOST = "Computer lost or heartbeat stopped"


def working(api, *, start=True):
    """ops mid-turn: started, and it has used a tool (the kind of run the sweep holds when it is cut off)."""
    machine, message, attempt = setup_attempt(api)
    if start:
        post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'quarantined'}, machine['token'])
        post(api, f"attempts/{attempt['id']}/events", {'events': [
            {'seq': 1, 'kind': 'tool', 'payload': {'name': 'shell'}},
            {'seq': 2, 'kind': 'message', 'payload': {'text': 'Opening the pull request.'}}]}, machine['token'])
    return machine, message, attempt


def quarantine(api, reason="10 refusals today", context=None):
    with api.app.state.store.transaction() as c:
        H.quarantine(c, 'ops', reason, context=context)
        return H.quarantine_resumes(c, 'ops')['resumes_at']


def end_reason(api, attempt):
    with api.app.state.store.read() as c:
        return run_diagnostics.metadata(c, attempt['id'])['end_reason']


def test_a_quarantine_stops_the_running_turn_with_its_reason_and_the_job_runs_again_when_it_lifts(api, time_machine):
    # Keep the one-hour cooldown on the same UTC date as the expected stop reason.
    time_machine.move_to("2026-10-26T12:00:00Z", tick=False)
    machine, message, attempt = working(api)
    resumes = quarantine(api)
    line = f"Quarantined: 10 refusals today, resumes about {resumes[11:16]} UTC"
    # The renewal is answered, not refused: a Stop carrying the quarantine's reason.
    renewed = post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'])
    assert renewed['stop'] is True and renewed['stop_reason'] == line
    # The turn's own calls keep working until it hands in, so nothing reads as a lost lease.
    post(api, f"attempts/{attempt['id']}/events", {'events': [
        {'seq': 3, 'kind': 'message', 'payload': {'text': 'Stopping here.'}}]}, machine['token'])
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': 3}, machine['token'])
    assert end_reason(api, attempt) == line and LOST not in end_reason(api, attempt)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM attempts WHERE id=?", (attempt['id'],)).fetchone()[0] == 'interrupted'
        assert H.bot(c, 'ops')['state'] == 'quarantined'
    # Back in the queue, never uncertain, and nothing held for review.
    assert job_state(api, attempt['job_id']) == 'queued'
    assert get(api, 'bots/ops/execution-review')['jobs'] == []
    notice = get(api, f"conversations/{message['conversation_id']}/messages")[-1]
    assert notice['kind'] == 'notice' and notice['body'].startswith(line)
    assert 'goes back in the queue and runs again when the quarantine lifts' in notice['body']
    # Not while quarantined: the claim only hands work to an active bot.
    ready(api, machine, ['ops'])
    assert claim(api, machine) is None
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, 'ops', state='active', reason='Cooldown over: the refusal count starts again.')
    ready(api, machine, ['ops'])
    assert claim(api, machine)['job_id'] == attempt['job_id']


def test_a_quarantine_only_a_person_lifts_says_so_and_names_no_refused_words(api):
    machine, message, attempt = working(api)
    quarantine(api, "escape: the task note reaches outside the hub: ghp_synthetic",
               context={"what": "task note", "found": "a GitHub token", "preview": "ghp_[masked]", "task": None})
    renewed = post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'])
    assert renewed['stop_reason'] == "Quarantined: the safety check stopped a task note, until a person clears it"
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': 2}, machine['token'])
    assert end_reason(api, attempt) == renewed['stop_reason'] and 'ghp_' not in end_reason(api, attempt)
    assert job_state(api, attempt['job_id']) == 'queued'


def test_an_answer_finished_as_the_quarantine_began_is_kept_on_the_run_and_its_job_runs_again(api):
    machine, message, attempt = working(api)
    quarantine(api)
    assert post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'])['stop'] is True
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'completed', 'last_seq': 2,
         'text': 'Opened the pull request.'}, machine['token'])
    with api.app.state.store.read() as c:
        row = c.execute("SELECT state,final_text FROM attempts WHERE id=?", (attempt['id'],)).fetchone()
    assert tuple(row) == ('interrupted', 'Opened the pull request.')
    assert job_state(api, attempt['job_id']) == 'queued'
    assert end_reason(api, attempt).startswith('Quarantined: ')
    assert not [m for m in get(api, f"conversations/{message['conversation_id']}/messages") if m['from_actor'] == 'bot:ops']


def test_a_turn_still_waiting_to_start_is_stopped_the_same_way(api):
    machine, message, attempt = working(api, start=False)
    quarantine(api)
    assert post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'])['stop'] is True
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': 0}, machine['token'])
    assert job_state(api, attempt['job_id']) == 'queued'
    assert end_reason(api, attempt).startswith('Quarantined: 10 refusals today, resumes about ')


def test_a_stopped_turn_whose_computer_never_reported_back_goes_back_in_the_queue_not_to_review(api):
    machine, message, attempt = working(api)
    quarantine(api)
    post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'])     # it heard, then the computer went away
    expire(api, attempt['id'])
    assert claim(api, machine) is None                                      # the sweep lets the lease go
    assert job_state(api, attempt['job_id']) == 'queued'
    assert end_reason(api, attempt).startswith('Quarantined: 10 refusals today') and LOST not in end_reason(api, attempt)
    aged(api, attempt['id'])
    assert sweep(api) == [] and job_state(api, attempt['job_id']) == 'queued'
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE title=?",
                             (H.HELD_REVIEW_TITLE.format(bot='ops'),)).fetchone()
    notice = get(api, f"conversations/{message['conversation_id']}/messages")[-1]
    assert notice['body'].startswith('Quarantined: 10 refusals today')


def test_a_real_lease_loss_with_no_quarantine_still_reads_as_a_lost_computer(api):
    """Guard: only a quarantine's Stop changes the account. A lease the server really let go, on an active bot, is
    refused as before, ends as a lost computer and is held for review."""
    machine, message, attempt = working(api)
    expire(api, attempt['id'])
    post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'], expected=409)
    assert claim(api, machine) is None
    assert LOST in end_reason(api, attempt)
    assert job_state(api, attempt['job_id']) == 'uncertain'


def test_a_quarantine_after_the_lease_already_lapsed_is_still_a_lost_computer(api):
    machine, message, attempt = working(api)
    expire(api, attempt['id'])
    quarantine(api)
    refused = post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'], expected=409)
    assert refused['error']['code'] == 'stale_lease'
    with api.app.state.store.read() as c:
        assert c.execute("SELECT stop_requested FROM attempts WHERE id=?", (attempt['id'],)).fetchone()[0] is None


def test_a_person_stop_is_unchanged(api):
    machine, message, attempt = working(api)
    post(api, 'bots/ops/stop', {'attempt_id': attempt['id']})
    renewed = post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'])
    assert renewed['stop'] is True and 'stop_reason' not in renewed
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted', 'last_seq': 2}, machine['token'])
    assert job_state(api, attempt['job_id']) == 'cancelled'
    assert end_reason(api, attempt).startswith('Cancelled by ')


def test_all_running_turns_stop_and_folded_inputs_return_to_the_queue(api):
    from backend.tests.test_task_lanes import new_task, task_bot
    machine = task_bot(api, lanes=True)
    task = new_task(api, 'Check the files')
    first = claim(api, machine)
    post(api, f"attempts/{first['id']}/started", {'thread_id': 'files'}, machine['token'])
    post(api, f"attempts/{first['id']}/events", {'events': [
        {'seq': 1, 'kind': 'tool', 'payload': {'name': 'shell'}}]}, machine['token'])
    folded = post(api, f"tasks/{task['id']}/comments", {'text': 'Include the archive.'})['comment']
    inputs = post(api, f"attempts/{first['id']}/inputs", {}, machine['token'])['messages']
    assert [m['id'] for m in inputs] == [folded['id']]
    post(api, f"attempts/{first['id']}/inputs/{folded['id']}/ack", {}, machine['token'])
    attempts = [first]
    for title in ['Check the export', 'Check the archive']:
        post(api, 'tasks', {'owner': 'ops', 'title': title, 'body': 'Check the saved files.'})
        attempt = claim(api, machine)
        assert attempt is not None
        post(api, f"attempts/{attempt['id']}/started", {'thread_id': title}, machine['token'])
        attempts.append(attempt)
    quarantine(api)
    for attempt in attempts:
        response = post(api, f"attempts/{attempt['id']}/renew", {}, machine['token'])
        assert response['stop'] and response['stop_reason'].startswith('Quarantined: ')
        post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'interrupted',
             'last_seq': 1 if attempt == first else 0}, machine['token'])
        assert job_state(api, attempt['job_id']) == 'queued'
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (folded['id'],)).fetchone()[0] == 'queued'
        assert not c.execute("SELECT 1 FROM attempts WHERE state IN ('running','leased','expired')").fetchone()
        assert not c.execute("SELECT 1 FROM jobs WHERE state='uncertain'").fetchone()
