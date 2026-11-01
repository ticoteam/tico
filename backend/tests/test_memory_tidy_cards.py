"""Synthetic cards only: no real memory repositories, model calls or team databases."""
import json
import sqlite3
from dataclasses import replace

import pytest

from backend import approval_expiry, memory_tidy_cards as C
from backend.store import H, Problem
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_memory_tidy import snapshot, proposal, run
from backend.tests.test_tasks_board import bot_token


@pytest.fixture
def db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(C.SCHEMA)
    yield c
    c.close()


def offer(c, **kw):
    snap = snapshot()
    return C.file_card(c, **{'bot': 'ops', 'reviewer': 'human:ana', 'pass_id': H.new_id(),
                            'memory_commit': 'a' * 40, 'model': 'codex:gpt-6.1-sol', 'cost_usd': .125,
                            'result': run(snap, proposal(snap)), **kw})


def test_one_open_card_retry_and_new_night_replaces_without_applying(db, monkeypatch):
    monkeypatch.setattr(H, 'now', lambda: '2026-11-02T18:00:00.000000Z')
    first = offer(db, pass_id='night1')
    assert offer(db, pass_id='night1') == first
    assert len(C.cards_for(db, 'human:ana')) == 1
    monkeypatch.setattr(H, 'now', lambda: '2026-11-03T18:00:00.000000Z')
    second = offer(db, pass_id='night2')
    assert C.cards_for(db, 'human:ana') == [{k: v for k, v in second.items() if k != 'diff'}]
    old = dict(db.execute('SELECT * FROM memory_tidy_cards WHERE id=?', (first['id'],)).fetchone())
    assert (old['status'], old['reason']) == ('withdrawn', 'replaced')
    # A delayed retry never reopens the old card or withdraws the new one.
    assert offer(db, pass_id='night1')['status'] == 'withdrawn'
    assert len(C.cards_for(db, 'human:ana')) == 1
    with pytest.raises(Problem):
        offer(db, started_at='2026-11-02T17:00:00Z')
    # These are the only tables: filing has no repository/memory/job storage to mutate.
    assert [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ['memory_tidy_cards']


def test_two_business_days_excludes_weekend_and_expires_at_boundary(db, monkeypatch):
    monkeypatch.setattr(H, 'now', lambda: '2026-11-06T18:00:00.000000Z')  # Friday 10 Pacific
    card = offer(db)
    assert card['expires'] == '2026-11-10T18:00:00.000000Z'  # Tuesday 10 Pacific
    before = '2026-11-10T17:59:59.999999Z'
    assert len(C.cards_for(db, 'human:ana', before)) == 1
    assert C.sweep(db, before) == 0
    assert C.cards_for(db, 'human:ana', card['expires']) == []
    assert C.sweep(db, card['expires']) == 1
    assert db.execute('SELECT status,reason FROM memory_tidy_cards').fetchone()[:] == ('withdrawn', 'expired')


@pytest.mark.parametrize('status', ['flagged', 'no_change'])
def test_nonoffers_leave_existing_card_untouched(db, monkeypatch, status):
    monkeypatch.setattr(H, 'now', lambda: '2026-11-02T18:00:00.000000Z')
    original = offer(db)
    result = replace(run(snapshot(), proposal(snapshot())), status=status)
    assert offer(db, result=result) is None
    assert C.cards_for(db, 'human:ana') == [{k: v for k, v in original.items() if k != 'diff'}]


@pytest.mark.parametrize('kw', [{'cost_usd': float('nan')}, {'cost_usd': -1}, {'model': 'claude:haiku'},
                               {'memory_commit': 'unknown'}])
def test_invalid_metadata_cannot_offer(db, kw):
    with pytest.raises(Problem):
        offer(db, **kw)
    assert db.execute('SELECT count(*) FROM memory_tidy_cards').fetchone()[0] == 0


def test_named_reviewer_sees_card_after_both_private_tasks_close_and_sweep(api):
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, 'human:ana', 'Synthetic tidy pass', 'Synthetic fixture', 'bot:ops',
                               private=True, lint=False)
        child = H.task_create(c, 'bot:ops', 'Synthetic tidy check', 'Synthetic fixture', 'bot:ops',
                              private=True, parent_id=parent['id'], lint=False)
        before = {t: c.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in ('jobs', 'messages', 'approvals')}
        card = offer(c, reviewer='bot:cpo', parent_task_id=parent['id'], check_task_id=child['id'])
        after = {t: c.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in before}
        assert after == before  # no wake or task-bound approval/ask on filing
        H.task_close(c, 'bot:ops', child['id'], quiet=True)
        H.task_close(c, 'human:ana', parent['id'], quiet=True)
        approval_expiry.sweep(c)
    token = bot_token(api, 'cpo')
    cards = get(api, 'memory-tidy/cards', token)['cards']
    assert len(cards) == 1
    assert {k: cards[0][k] for k in card if k != 'diff'} == {k: v for k, v in card.items() if k != 'diff'}
    assert 'diff' not in cards[0]  # large diffs are fetched only when opening a card
    detail = get(api, 'memory-tidy/cards/' + card['id'], token)['card']
    assert {k: detail[k] for k in card} == card
    assert len(card['diary'].splitlines()) <= 3
    assert card['diff'] and card['memory_commit'] == 'a' * 40
    assert card['model'] == 'codex:gpt-6.1-sol' and card['cost_usd'] == .125
    # Being the owner/manager grants no access to another reviewer's card.
    assert get(api, 'memory-tidy/cards')['cards'] == []
    get(api, 'memory-tidy/cards/' + card['id'], expected=404)
    post(api, 'memory-tidy/cards/' + card['id'] + '/decide', {'decision': 'discard'}, expected=404)


def test_human_needs_you_and_discard_do_not_change_tasks_or_enqueue_work(api):
    with api.app.state.store.transaction() as c:
        card = offer(c)
        before = {t: c.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in ('jobs', 'messages', 'task_events')}
    assert any(i['id'] == card['id'] and i['kind'] == 'memory_tidy' for i in get(api, 'needs-you')['items'])
    post(api, 'memory-tidy/cards/' + card['id'] + '/decide', {'decision': 'keep'}, expected=409)
    assert get(api, 'memory-tidy/cards')['cards'][0]['status'] == 'open'
    done = post(api, 'memory-tidy/cards/' + card['id'] + '/decide', {'decision': 'discard'})['card']
    assert done['status'] == 'discarded'
    assert not get(api, 'memory-tidy/cards')['cards']
    assert not any(i['id'] == card['id'] for i in get(api, 'needs-you')['items'])
    with api.app.state.store.read() as c:
        assert {t: c.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in before} == before
        assert c.execute("SELECT count(*) FROM changes WHERE topic='needs' AND kind='memory_tidy' "
                         "AND subject_id='human:ana'").fetchone()[0] == 2
        stored = c.execute('SELECT edits_json FROM memory_tidy_cards').fetchone()[0]
        assert json.loads(stored)['edits'][0]['number'] == 1


def test_hourly_scheduler_persists_withdrawal_after_weekend(api, monkeypatch):
    from datetime import datetime
    from backend.scheduler import Scheduler
    monkeypatch.setattr(H, 'now', lambda: '2026-11-06T18:00:00.000000Z')
    with api.app.state.store.transaction() as c:
        card = offer(c)
    at = datetime.fromisoformat(card['expires'].replace('Z', '+00:00'))
    Scheduler(api.app.state.store, api.app.state.execution).tick(at)
    with api.app.state.store.read() as c:
        row = c.execute('SELECT status,reason FROM memory_tidy_cards WHERE id=?', (card['id'],)).fetchone()
        assert row[:] == ('withdrawn', 'expired')


def test_standalone_card_stays_out_of_batch_and_live_brief(api):
    with api.app.state.store.transaction() as c:
        card = offer(c)
        task = H.task_create(c, 'bot:ops', 'Review synthetic notes', 'Ready for review.', 'human:ana', lint=False)
        before = {t: c.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in ('jobs', 'messages')}
    assert any(i['id'] == card['id'] for i in get(api, 'needs-you')['items'])
    brief = get(api, 'live/brief')
    assert sum(g['items'] for g in brief['lineup']) == 1
    batch = post(api, 'batch', {})
    assert batch['total'] == 1
    assert batch['item']['key'] == 'task:' + task['id']
    assert get(api, 'memory-tidy/cards/' + card['id'])['card']['status'] == 'open'
    with api.app.state.store.read() as c:
        assert {t: c.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in before} == before
