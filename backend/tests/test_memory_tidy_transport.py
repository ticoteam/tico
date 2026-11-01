"""Runner custody fixtures; disposable state and synthetic text only."""
import json

import pytest

from backend import memory_tidy_transport as transport
from backend.store import H, Problem
from backend.tests.test_api import api, post  # noqa: F401
from backend.tests.test_memory_tidy import snapshot, proposal
from backend.tests.test_memory_tidy_runs import last, setup


def pinned(c):
    last(c, status='filed')
    row = c.execute('SELECT * FROM memory_tidy_passes').fetchone()
    body = transport.Prepare(memory_commit='a' * 40, snapshot={k: v for k, v in snapshot().items() if k == 'AGENT.md' or k.startswith(('memory/', 'knowledge/')) and k.endswith('.md')}, manifest='b' * 64)
    return row, body


def test_packet_is_pinned_and_editing_model_cannot_select_verification_rules(api):
    store = setup(api)
    with store.transaction() as c:
        row, body = pinned(c)
        packet = transport.prepare(c, row, 'attempt', body)
        assert packet['entries'] and 'agent' not in packet
        again = transport.prepare(c, row, 'attempt', body)
        assert again == packet
        with pytest.raises(Problem, match='already pinned'):
            transport.prepare(c, row, 'other-attempt', body)
        verify = transport.proposal(c, 'attempt', json.dumps(proposal(snapshot())))
        assert verify['agent'] == snapshot()['AGENT.md'] and verify['rules']
        assert any('General rule' in r for r in verify['rules'])
        transport.verification(c, 'attempt', transport.Verification(text='{"conflicts":[]}', manifest='b' * 64))
        saved = c.execute('SELECT * FROM memory_tidy_packets').fetchone()
        assert saved['conflicts'] == 0 and saved['stage'] == 'verified'
        with pytest.raises(Problem):
            transport.proposal(c, 'attempt', '{}')
        with pytest.raises(Problem):
            transport.verification(c, 'attempt', transport.Verification(text='{"conflicts":[]}', manifest='c' * 64))


def test_untrusted_actor_cannot_supply_runner_snapshot_or_verdict(api):
    for suffix, body in [('prepare', {'memory_commit': 'a' * 40, 'snapshot': {}, 'manifest': 'b' * 64}),
                         ('proposal', {'text': '{}'}),
                         ('verification', {'text': '{"conflicts":[]}', 'manifest': 'b' * 64})]:
        post(api, 'attempts/synthetic/memory-tidy/' + suffix, body, expected=403)


def test_unsafe_output_never_persists_as_a_proposal_or_verification_input(api):
    store = setup(api)
    with store.transaction() as c:
        row, body = pinned(c)
        transport.prepare(c, row, 'attempt', body)
        obj = proposal(snapshot())
        secret = 'sk-' + 'Z' * 35
        obj['diary'] = secret
        assert transport.proposal(c, 'attempt', json.dumps(obj)) is None
        saved = dict(c.execute('SELECT * FROM memory_tidy_packets').fetchone())
        assert saved['proposal'] is None and secret not in json.dumps(saved)
        assert '19:' in saved['flags_json']


def test_snapshot_cannot_include_transcripts_state_or_paths_outside_memory(api):
    store = setup(api)
    with store.transaction() as c:
        row, body = pinned(c)
        for path in ('state.md', 'memory/../outside.md', '/memory/private.md', 'memory/transcript.txt'):
            with pytest.raises(Problem, match='out-of-scope'):
                transport.prepare(c, row, 'attempt', body.model_copy(update={'snapshot': {path: 'untrusted'}}))
        assert c.execute('SELECT count(*) FROM memory_tidy_packets').fetchone()[0] == 0


@pytest.mark.parametrize('mutated', [False, True])
def test_runner_completion_retains_attachment_and_closes_without_waking_parent(api, monkeypatch, mutated):
    from backend.tests.test_memory_pass import pass_filed
    from backend.tests.test_api import claim
    from backend.tests.test_check_models import finish
    machine, parent, child = pass_filed(api)
    check = claim(api, machine)
    store = api.app.state.store
    monkeypatch.setattr(H, 'now', lambda: '2026-11-01T11:45:00.000000Z')
    with store.transaction() as c:
        last(c, status='filed')
        c.execute('UPDATE memory_tidy_passes SET parent_task_id=?,check_task_id=?', (parent['id'], child['id']))
        row = c.execute('SELECT * FROM memory_tidy_passes').fetchone()
        snap = {k: v for k, v in snapshot().items() if k == 'AGENT.md' or k.startswith(('memory/', 'knowledge/')) and k.endswith('.md')}
        transport.prepare(c, row, check['id'], transport.Prepare(memory_commit='a' * 40, snapshot=snap, manifest='b' * 64))
        transport.proposal(c, check['id'], json.dumps(proposal(snap)))
        transport.verification(c, check['id'], transport.Verification(text='{"conflicts":[]}', manifest=('c' if mutated else 'b') * 64))
        before = c.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0]
    finish(api, machine, check, {'runtime': 'codex', 'model': 'gpt-6.1-sol'})
    with store.read() as c:
        assert H.task(c, child['id'])['status'] == H.task(c, parent['id'])['status'] == 'closed'
        assert c.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0] == before
        cards = c.execute('SELECT * FROM memory_tidy_cards').fetchall()
        assets = c.execute('SELECT b.* FROM blobs b JOIN task_assets a ON a.blob_id=b.id WHERE a.task_id=?', (child['id'],)).fetchall()
        assert len(cards) == len(assets) == (0 if mutated else 1)
        assert c.execute('SELECT cost_usd FROM memory_tidy_passes').fetchone()[0] > 0
        if not mutated:
            assert cards[0]['reviewer'] == 'bot:product-lead'
            assert json.loads(api.app.state.blobs.get(assets[0]['digest']))['edits'][0]['number'] == 1
            # The original authorized requester can still download after quiet closure.
            response = api.get('/api/v2/files/' + assets[0]['id'], headers={'Authorization': 'Bearer ana-test'})
            assert response.status_code == 200 and response.content
