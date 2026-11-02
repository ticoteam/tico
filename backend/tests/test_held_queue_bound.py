"""Held queue admission uses only disposable synthetic API databases, never a live queue."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from backend import hubdb as H
from backend.store import Store
from backend.tests.test_api import api, get, post, restrict, runner, assign, ready, claim, setup_attempt  # noqa: F401
from backend.tests.test_member_bots import finish


@pytest.fixture
def queue(api, monkeypatch):
    for name in ('HELD_QUEUE_ITEMS', 'HELD_QUEUE_BYTES', 'HELD_QUEUE_SENDER_ITEMS', 'HELD_QUEUE_SENDER_BYTES'):
        monkeypatch.setattr(H, name, 100000)
    tokens = {slug: setup_attempt(api, slug, 'ben' if slug == 'cpo' else 'ana')[2]['token']
              for slug in ('ops', 'cpo', 'doc-updater')}
    tasks = [post(api, 'tasks', {'owner': 'finance', 'title': 'Check fixture invoices', 'body': 'Synthetic work'},
                  token=tokens[sender]) for sender in ('ops', 'cpo')]
    return api, tokens, tasks


def hold(api, state='paused'):
    with api.app.state.store.transaction() as c:
        if state == 'quarantined':
            H.quarantine(c, 'finance', 'safety check fixture')
        else:
            H.status_set(c, 'human:ana', 'finance', state='paused', reason='fixture hold')


def send(queue, sender='ops', text='A fixture message', expected=200, **fields):
    api, tokens, _ = queue
    return post(api, 'messages', {'to': 'finance', 'text': text, 'needs_action': True, **fields},
                token=tokens[sender], expected=expected)


def pending(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM messages WHERE recipient_id='bot:finance' "
                    "AND sender_id LIKE 'bot:%' AND delivered_at IS NULL AND deleted_at IS NULL ORDER BY created_at,id")]


def retained(rows):
    return sum(len(r['body'].encode('utf-8')) + len(r['refs'].encode('utf-8')) for r in rows)


def domain(api):
    with api.app.state.store.read() as c:
        tables = ('messages', 'jobs', 'notes', 'conversations', 'tasks', 'task_events', 'task_delegations')
        return {t: [tuple(r) for r in c.execute('SELECT * FROM ' + t + ' ORDER BY rowid')] for t in tables} | {
            'events': [tuple(r) for r in c.execute("SELECT * FROM events WHERE action<>'refused' ORDER BY rowid")]}


@pytest.mark.parametrize('state', ['paused', 'quarantined'])
def test_total_items_across_senders_rooms_and_comments_rolls_back(queue, monkeypatch, state):
    api, tokens, tasks = queue
    hold(api, state)
    monkeypatch.setattr(H, 'HELD_QUEUE_ITEMS', 4)
    send(queue)
    send(queue, 'cpo', conversation_id=tasks[1]['conversation_id'])
    post(api, f"tasks/{tasks[0]['id']}/comments", {'text': 'Fixture finding', 'needs_action': True}, token=tokens['ops'])
    send(queue, 'doc-updater', kind='notice')
    assert len(pending(api)) == 4
    before = domain(api)
    result = post(api, f"tasks/{tasks[1]['id']}/comments", {'text': 'No room', 'needs_action': True},
                  token=tokens['cpo'], expected=422)
    assert result['error']['code'] == 'queue_full'
    assert state in result['error']['detail'] and 'recipient item limit 4' in result['error']['detail']
    assert 'resumes' in result['error']['detail']
    assert domain(api) == before
    assert len({r['conversation_id'] for r in pending(api)}) == 4


@pytest.mark.parametrize('setting,label', [('HELD_QUEUE_BYTES', 'recipient byte'), ('HELD_QUEUE_SENDER_BYTES', 'your byte')])
def test_byte_boundary_uses_final_utf8_body_and_refs(queue, monkeypatch, setting, label):
    api, _, _ = queue
    hold(api)
    body = '界🙂'
    first = send(queue, text=body, kind='notice')
    size = retained(pending(api))
    assert size > len(body) + len(first['refs'])
    monkeypatch.setattr(H, setting, size * 2)
    send(queue, text='🙂界', kind='notice')  # same bytes; an identical repeat is a duplicate wake, not queued
    assert retained(pending(api)) == size * 2
    before = domain(api)
    result = send(queue, text='x', kind='notice', expected=422)
    assert result['error']['code'] == 'queue_full' and label in result['error']['detail']
    assert domain(api) == before
    if setting == 'HELD_QUEUE_SENDER_BYTES':
        send(queue, 'cpo', text=body, kind='notice')


def test_sender_items_leave_room_for_other_senders_and_count_bot_notices(queue, monkeypatch):
    api, _, _ = queue
    hold(api)
    monkeypatch.setattr(H, 'HELD_QUEUE_SENDER_ITEMS', 1)
    send(queue, kind='notice')
    before = domain(api)
    result = send(queue, kind='notice', expected=422)
    assert 'your item limit 1' in result['error']['detail']
    assert domain(api) == before
    send(queue, 'cpo', kind='notice')
    assert len(pending(api)) == 2


@pytest.mark.parametrize('setting', ['HELD_QUEUE_ITEMS', 'HELD_QUEUE_BYTES', 'HELD_QUEUE_SENDER_ITEMS', 'HELD_QUEUE_SENDER_BYTES'])
def test_concurrent_admission_cannot_overshoot_on_independent_connections(queue, monkeypatch, setting):
    api, _, _ = queue
    hold(api)
    # Measure this exact stored notice; every candidate below has the same byte size.
    send(queue, text='界🙂9', kind='notice')
    charge = retained(pending(api))
    monkeypatch.setattr(H, setting, 3 if setting.endswith('ITEMS') else 3 * charge)
    barrier = Barrier(8)
    def submit(i):
        barrier.wait(timeout=10)
        sender = 'ops' if 'SENDER' in setting else ('ops', 'cpo', 'doc-updater')[i % 3]
        # Separate Stores have independent writer gates and connection pools: SQLite must serialize them.
        with Store(api.app.state.store.settings).transaction() as c:
            try:
                return H.say(c, 'bot:' + sender, 'bot:finance', f'界🙂{i}', kind='notice', refs={'needs_action': True})['id']
            except H.Refused as exc:
                assert exc.rule == 'queue_full'
                raise
    def admitted(i):
        try:
            return submit(i)
        except H.Refused:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(admitted, range(8)))
    assert sum(r is not None for r in results) == 2
    assert len(pending(api)) == 3 and retained(pending(api)) == 3 * charge
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE message_id IN (?,?,?)",
                         [r['id'] for r in pending(api)]).fetchone()[0] == 3


def test_queue_full_never_counts_toward_repeat_review_or_daily_backstop(queue, monkeypatch):
    api, _, _ = queue
    hold(api, 'quarantined')
    monkeypatch.setattr(H, 'HELD_QUEUE_ITEMS', 0)
    monkeypatch.setattr(H, 'CAP_PER_HOUR', 0)  # a full held queue must win over loop-cap misconduct
    for _ in range(H.QUARANTINE_TOTAL_AT + 2):
        assert send(queue, expected=422)['error']['code'] == 'queue_full'
    # Force a different refusal after the queue-full diagnostics to exercise the cumulative backstop too.
    post(api, 'messages', {'to': 'ops', 'text': 'Self fixture'}, token=queue[1]['ops'], expected=422)
    with api.app.state.store.read() as c:
        assert H.bot(c, 'ops')['state'] == 'active'
        assert H.bot(c, 'finance')['state'] == 'quarantined'
        assert H.lift_cooled_quarantines(c) == []
        assert not c.execute("SELECT 1 FROM tasks WHERE title LIKE 'Review ops%' ").fetchone()


@pytest.mark.parametrize('state', ['paused', 'quarantined'])
def test_full_queue_keeps_work_and_human_system_and_recovery_controls_work(queue, monkeypatch, state):
    api, _, _ = queue
    machine = runner(api, label='Fixture finance machine')
    assign(api, machine, 'finance')
    ready(api, machine, ['finance'])
    while work := claim(api, machine, 'finance'):
        finish(api, machine, work)
    hold(api, state)
    monkeypatch.setattr(H, 'HELD_QUEUE_ITEMS', 2)
    accepted = [send(queue)['id'], send(queue, 'cpo')['id']]
    assert claim(api, machine, 'finance') is None
    # Lowering limits preserves all accepted work, even when already above the new bound.
    monkeypatch.setattr(H, 'HELD_QUEUE_ITEMS', 1)
    before = pending(api)
    assert send(queue, expected=422)['error']['code'] == 'queue_full'
    assert pending(api) == before
    post(api, 'messages', {'to': 'finance', 'text': 'Person recovery instructions'})
    with api.app.state.store.transaction() as c:
        H.say(c, H.KEEPER, 'bot:finance', 'Fixture system notice', kind='notice')
    if state == 'quarantined':
        post(api, 'bots/finance/quarantine/clear', {})
    else:
        with api.app.state.store.read() as c:
            revision = c.execute("SELECT revision FROM bot_config WHERE bot='finance'").fetchone()[0]
        post(api, 'bots/finance/control', {'action': 'resume', 'expected_revision': revision})
    ran = []
    while work := claim(api, machine, 'finance'):
        assert claim(api, machine, 'finance') is None, 'existing one-active-attempt limit still applies'
        with api.app.state.store.read() as c:
            ran += [r['message_id'] for r in c.execute('SELECT j.message_id FROM jobs j JOIN messages m ON m.id=j.message_id '
                                                      'WHERE j.attempt_id=? ORDER BY m.created_at,m.id', (work['id'],))
                    if r['message_id'] in accepted]
        finish(api, machine, work)
    assert ran == accepted
    assert claim(api, machine, 'finance') is None
    send(queue, 'doc-updater')  # active queue remains unchanged even above the held limit


@pytest.mark.parametrize('state', ['active', 'paused', 'quarantined'])
@pytest.mark.parametrize('denial', ['write', 'contact'])
def test_permission_denial_precedes_capacity(queue, monkeypatch, state, denial):
    api, tokens, _ = queue
    monkeypatch.setattr(H, 'HELD_QUEUE_ITEMS', 0)
    if state != 'active':
        hold(api, state)
    with api.app.state.store.transaction() as c:
        if denial == 'write':
            restrict(c, 'finance', see={'bots': ['doc-updater'], 'people': ['ana']},
                     read={'bots': ['doc-updater'], 'people': ['ana']}, write={'people': ['ana']})
        else:
            c.execute("UPDATE bot_config SET config=json_set(config,'$.bot_contact','replies') WHERE bot='finance'")
    result = send(queue, 'doc-updater', expected=403)
    assert result['error']['code'] == ('forbidden' if denial == 'write' else 'bot_contact')
    assert 'queue' not in result['error']['detail'].lower()
    assert pending(api) == []


def test_author_cannot_grow_a_pending_comment_past_byte_bound(queue, monkeypatch):
    api, tokens, tasks = queue
    hold(api)
    from backend import bot_to_bot
    # A comment that wakes (wake_rules.always_wakes, or a rule that wakes on comments) keeps plain references, so its author can still edit it.
    monkeypatch.setattr(bot_to_bot, 'can_wait', lambda *a, **k: False)
    from backend import rules
    monkeypatch.setattr(rules, '_rule_for_event', lambda event: (rules.WAKE, event))   # as a wake rule would
    with api.app.state.store.transaction() as c:
        comment = H.task_comment(c, 'bot:ops', tasks[0]['id'], 'Small')
    monkeypatch.setattr(H, 'HELD_QUEUE_BYTES', retained(pending(api)))
    before = domain(api)
    result = post(api, f"tasks/{tasks[0]['id']}/comments/{comment['id']}", {'text': 'Much larger 🙂'},
                  token=tokens['ops'], expected=422)
    assert result['error']['code'] == 'queue_full'
    assert domain(api) == before


def test_total_bytes_combine_senders_task_rooms_comments_and_notices(queue, monkeypatch):
    api, tokens, tasks = queue
    hold(api)
    send(queue, text='界🙂')
    first_size = retained(pending(api))
    send(queue, 'cpo', text='界🙂', conversation_id=tasks[1]['conversation_id'])
    post(api, f"tasks/{tasks[0]['id']}/comments", {'text': '界🙂', 'needs_action': True}, token=tokens['ops'])
    monkeypatch.setattr(H, 'HELD_QUEUE_BYTES', retained(pending(api)) + first_size)
    send(queue, 'doc-updater', text='界🙂', kind='notice')
    assert retained(pending(api)) == H.HELD_QUEUE_BYTES
    before = domain(api)
    assert send(queue, 'cpo', text='x', expected=422)['error']['code'] == 'queue_full'
    assert domain(api) == before


@pytest.mark.parametrize('final_state', ['active', 'paused'])
def test_recipient_state_is_read_after_acquiring_the_write_transaction(queue, monkeypatch, final_state):
    api, _, _ = queue
    hold(api)
    monkeypatch.setattr(H, 'HELD_QUEUE_ITEMS', 0)
    entered = Event()
    def submit():
        entered.set()
        try:
            with Store(api.app.state.store.settings).transaction() as c:
                return H.say(c, 'bot:ops', 'bot:finance', 'State-race fixture', refs={'needs_action': True})['id']
        except H.Refused as exc:
            return exc.rule
    with ThreadPoolExecutor(max_workers=1) as pool:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE bots SET state=? WHERE slug='finance'", ('paused' if final_state == 'active' else 'active',))
            future = pool.submit(submit)
            assert entered.wait(timeout=10)
            c.execute("UPDATE bots SET state=? WHERE slug='finance'", (final_state,))
        result = future.result(timeout=10)
    assert (result == 'queue_full') == (final_state == 'paused')
    assert len(pending(api)) == (1 if final_state == 'active' else 0)


def test_final_chat_goal_metadata_is_charged_and_rejection_rolls_back(queue, monkeypatch):
    api, _, tasks = queue
    room = {'id': tasks[0]['conversation_id']}
    hold(api)
    from backend import chat_goals
    original = chat_goals.current
    monkeypatch.setattr(chat_goals, 'current', lambda c, cid: {'id': 'fixture-goal', 'updated_at': 'fixture-time', 'status': 'active'}
                        if cid == room['id'] else original(c, cid))
    sent = send(queue, conversation_id=room['id'])
    assert sent['refs']['goal_context']['id'] == 'fixture-goal'
    size = retained(pending(api))
    # Enough for an unadorned message, but one byte short of the final stored goal metadata.
    monkeypatch.setattr(H, 'HELD_QUEUE_BYTES', 2 * size - 1)
    before = domain(api)
    assert send(queue, conversation_id=room['id'], expected=422)['error']['code'] == 'queue_full'
    assert domain(api) == before


def test_full_queue_withholds_only_the_automatic_answer_and_the_run_completes(api, monkeypatch):
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    with api.app.state.store.transaction() as c:
        asked = H.say(c, 'bot:finance', 'bot:ops', 'Fixture question', kind='ask')
    work = claim(api, machine, 'ops')
    post(api, f"attempts/{work['id']}/started", {'thread_id': 'fixture-thread'}, machine['token'])
    hold(api)
    monkeypatch.setattr(H, 'HELD_QUEUE_BYTES', 32)  # short answer fits; its final run/answers references do not
    done = {'outcome': 'completed', 'text': 'ok', 'last_seq': 0}
    key = f"complete:{work['id']}"
    post(api, f"attempts/{work['id']}/complete", done, machine['token'], key=key)
    with api.app.state.store.read() as c:
        attempt = c.execute('SELECT state, final_text FROM attempts WHERE id=?', (work['id'],)).fetchone()
        assert (attempt['state'], attempt['final_text']) == ('completed', 'ok')
        assert c.execute("SELECT state FROM jobs WHERE attempt_id=?", (work['id'],)).fetchone()[0] == 'completed'
        assert c.execute('SELECT count(*) FROM messages WHERE in_reply_to=?', (asked['id'],)).fetchone()[0] == 0
        withheld = c.execute("SELECT detail FROM events WHERE action='reply.withheld' AND target=?",
                             (work['id'],)).fetchone()
        assert withheld and '"queue_full"' in withheld[0]
        assert not c.execute("SELECT 1 FROM refusals WHERE actor='bot:ops' AND rule='queue_full'").fetchone()
    assert pending(api) == []
    # The runner's retry under the same key replays the success, and posts nothing more after the bot resumes.
    with api.app.state.store.transaction() as c:
        H.status_set(c, 'human:ana', 'finance', state='active', reason='Fixture resume')
    post(api, f"attempts/{work['id']}/complete", done, machine['token'], key=key)
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM messages WHERE in_reply_to=?', (asked['id'],)).fetchone()[0] == 0


def test_queue_full_does_not_trigger_escalation_from_prior_refusals(queue, monkeypatch):
    api, _, _ = queue
    hold(api)
    monkeypatch.setattr(H, 'HELD_QUEUE_ITEMS', 0)
    with api.app.state.store.transaction() as c:
        for _ in range(H.QUARANTINE_AT + 1):
            c.execute('INSERT INTO refusals VALUES(?,?,?,?,?,?)',
                      (H.new_id(), H.now(), 'bot:ops', 'identity', H._dump({'detail': 'Synthetic prior refusal'}), 'normal'))
    assert send(queue, expected=422)['error']['code'] == 'queue_full'
    with api.app.state.store.read() as c:
        assert H.bot(c, 'ops')['state'] == 'active'
        assert not c.execute("SELECT 1 FROM tasks WHERE title LIKE 'Review ops%'").fetchone()


def test_messages_resume_never_drains_are_not_charged_and_goal_events_still_post(api, monkeypatch):
    from backend.tests.test_chat_goals import setup, action, goal_event
    machine, cid = setup(api)
    action(api, cid, objective='Produce the Acme summary')
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'fixture-thread'}, machine['token'])
    with api.app.state.store.transaction() as c:
        H.quarantine(c, 'ops', 'Fixture hold mid-turn')
    monkeypatch.setattr(H, 'HELD_QUEUE_ITEMS', 1)
    with api.app.state.store.transaction() as c:
        # A quiet comment (wake=False) gets neither a job nor a carry-note: resume never delivers it.
        H._write_message(c, 'bot:finance', 'bot:ops', 'Fixture quiet note', H.conversation(c, cid), 'say',
                         {'comment': True, 'quiet': True}, None, None)
    # The in-flight turn's goal ends with a notice from the bot to itself: never charged, never refused.
    goal_event(api, machine, attempt, 'met', 'The condition holds.', started=False)
    assert get(api, f'conversations/{cid}/goal')['goal']['status'] == 'met'
    with api.app.state.store.transaction() as c:
        H.say(c, 'bot:finance', 'bot:ops', 'Fixture delivered one', refs={'needs_action': True})
    with pytest.raises(H.Refused) as refused, api.app.state.store.transaction() as c:
        H.say(c, 'bot:finance', 'bot:ops', 'Fixture delivered two', refs={'needs_action': True})
    assert refused.value.rule == 'queue_full'


def test_goal_command_is_charged_with_its_stored_objective(queue, monkeypatch):
    api, tokens, tasks = queue
    from backend import chat_goals
    monkeypatch.setattr(chat_goals, 'capabilities', lambda c, auth, conv: ('finance', True, []))
    hold(api)
    objective = 'Reconcile the Acme fixture invoices. ' * 80
    # The "/goal <objective>" body and its references fit; the objective stored again in goal_objective does not.
    monkeypatch.setattr(H, 'HELD_QUEUE_BYTES', len(objective) + 1000)
    before = domain(api)
    result = post(api, f"conversations/{tasks[0]['conversation_id']}/goal", {'action': 'set', 'objective': objective},
                  token=tokens['ops'], expected=422)
    assert result['error']['code'] == 'queue_full'
    assert domain(api) == before


@pytest.mark.parametrize('state', ['paused', 'quarantined'])
def test_external_harness_inbox_without_jobs_or_notes_fills_the_cap(queue, monkeypatch, state):
    # An external harness with no delivery computer gets no job, and a waking message no carry-note: its inbox is
    # what it reads on return, so every waking message (a needs_action notice too) is charged.
    api, _, _ = queue
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.harness','hermes') WHERE bot='finance'")
    hold(api, state)
    monkeypatch.setattr(H, 'HELD_QUEUE_ITEMS', 3)
    send(queue)
    send(queue, sender='cpo')
    with api.app.state.store.transaction() as c:
        H.say(c, 'bot:ops', 'bot:finance', 'Fixture notice', kind='notice', refs={'needs_action': True})
    held = [r['id'] for r in pending(api)]
    assert len(held) == 3
    with api.app.state.store.read() as c:
        for table in ('jobs', 'notes'):
            assert not c.execute(f"SELECT 1 FROM {table} WHERE message_id IN (?,?,?)", held).fetchone()
    before = domain(api)
    assert send(queue, sender='cpo', expected=422)['error']['code'] == 'queue_full'
    assert domain(api) == before
