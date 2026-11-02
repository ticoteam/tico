"""Selected-job revalidation stays bounded without trusting the earlier read."""
import json

import pytest

from backend import execution
from backend.auth import Identity
from backend.models import Claim
from backend.store import H, encode
from backend.tests.test_api import api, assign, claim, post, ready, runner  # noqa: F401


def queued_fleet(api, machine, start, stop):
    store = api.app.state.store
    with store.transaction() as c:
        for i in range(start, stop):
            bot = f"queued-{i}"
            c.execute("INSERT INTO bots(id,slug,runtime,state,created_at) VALUES(?,?,'fake','active',?)", (bot, bot, H.now()))
            c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES(?,?,'ana')",
                      (bot, encode({'name': bot, 'runtime': 'fake'})))
            c.execute("INSERT INTO assignments VALUES(?,?,1,?,'human:ana')", (bot, machine['runner_id'], H.now()))
            H.say(c, 'human:ana', 'bot:' + bot, 'Synthetic unrelated work')
    ready(api, machine, ['ops', *(f'queued-{i}' for i in range(stop))])


def test_selected_claim_classifies_one_bot_as_unrelated_queue_grows(api, monkeypatch):
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    message = post(api, 'chat/ops', {'text': 'Selected synthetic work'})
    store, engine = api.app.state.store, api.app.state.execution
    who = Identity('runner:' + machine['runner_id'], 'runner', runner_id=machine['runner_id'])
    checked, original = [], execution.claim_refusal

    def counted(c, settings, runner, bot, *args, **kwargs):
        checked.append(bot)
        return original(c, settings, runner, bot, *args, **kwargs)

    monkeypatch.setattr(execution, 'claim_refusal', counted)
    counts = []
    for start, stop in ((0, 3), (3, 30)):
        queued_fleet(api, machine, start, stop)
        checked.clear()
        with store.transaction() as c:
            selected = engine.candidate(c, who, Claim(), engine.runner(c, who), job_id=message['id'])
        assert selected['id'] == message['id']
        counts.append(len(checked))
    assert counts == [1, 1]
    assert checked == ['ops']
    # The public claim's read selection still sees the full queue, but its writer
    # recheck classifies only the selected bot and leases the same oldest job.
    rechecks, candidate = [], engine.candidate

    def observe(*args, **kwargs):
        before = len(checked)
        result = candidate(*args, **kwargs)
        if kwargs.get('job_id') is not None:
            rechecks.append(checked[before:])
        return result

    monkeypatch.setattr(engine, 'candidate', observe)
    attempt = claim(api, machine)
    assert attempt['message']['id'] == message['id']
    assert rechecks == [['ops']]


@pytest.mark.parametrize('change', ['held', 'assignment', 'readiness', 'lane'])
def test_selected_claim_rechecks_changes_after_read_and_takes_the_next_job(api, monkeypatch, change):
    machine = runner(api)
    for bot in ('ops', 'finance'):
        assign(api, machine, bot)
    ready(api, machine, ['ops', 'finance'])
    first = post(api, 'chat/ops', {'text': 'Selected before the change'})
    post(api, 'chat/finance', {'text': 'Must wait for the next selection'})
    store, engine = api.app.state.store, api.app.state.execution
    candidate, selected = engine.candidate, []

    def changes_after_read(c, who, body, target, **kwargs):
        row = candidate(c, who, body, target, **kwargs)
        # The change happens once, between the read and the claim's write (the read's own pick, not the claim's).
        if kwargs.get('job_id') is None and row is not None and not selected:
            selected.append(row['id'])
            with store.transaction() as writer:
                if change == 'held':
                    writer.execute('UPDATE jobs SET held=1 WHERE id=?', (row['id'],))
                elif change == 'assignment':
                    writer.execute("DELETE FROM assignments WHERE bot='ops'")
                elif change == 'readiness':
                    readiness = json.loads(target['readiness'])
                    readiness['bots']['ops']['ready'] = False
                    writer.execute('UPDATE runners SET readiness=? WHERE id=?',
                                   (encode(readiness), machine['runner_id']))
                else:
                    # A currently active turn fences the bot even when it was
                    # absent from the earlier read's global active-attempt set.
                    writer.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,lane) "
                                   "VALUES('other-turn',?,'ops',?,1,'synthetic','running',?,?,'main')",
                                   (row['id'], machine['runner_id'], H.shift(H.now(), minutes=2), H.now()))
        return row

    monkeypatch.setattr(engine, 'candidate', changes_after_read)
    # The changed selection is not handed out; the claim drops it and takes the next eligible job in the same call.
    turn = claim(api, machine)
    assert selected == [first['id']]
    assert turn is not None and turn['bot'] == 'finance', turn
    with store.read() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (first['id'],)).fetchone()[0] == 'queued'
