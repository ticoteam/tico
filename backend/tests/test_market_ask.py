"""Market ask acceptance, isolated fictional graph and stubbed provider transport."""
import io
import json

import pytest

from backend import market as M, providers
from backend.tests.test_api import api, post  # noqa: F401
from clients import judge as J


def seed(api):
    with api.app.state.store.transaction() as c:
        for name in ('Kylon', 'Birch', 'Cedar'):
            ev, _ = M.create_evidence(c, M.SEED_ACTOR, quote=f'{name} offers self-hosting.',
                                      our_read=f'{name} charges $20 monthly.')
            M.create_entity(c, M.SEED_ACTOR, type='company', name=name, summary='A competitor.',
                            evidence_ids=[ev['id']])


def configure(monkeypatch, provider):
    for key in J.LLM_KEYS.values():
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(providers, 'load', lambda *a: {
        'runtime': 'fake', 'model': '', 'enabled': [provider] if provider else []})
    if provider:
        monkeypatch.setenv(J.LLM_KEYS[provider], 'synthetic-test-key')


@pytest.mark.parametrize('provider', ['openai', 'anthropic', 'google', 'openrouter'])
def test_enabled_provider_writes_answer_and_audit(api, monkeypatch, provider):
    seed(api)
    configure(monkeypatch, provider)
    seen = []

    def opener(request, timeout=None):
        seen.append(json.loads(request.data))
        answer = json.dumps({'answer': 'Kylon charges $20 monthly.'})
        payload = {'openai': {'choices': [{'message': {'content': answer}}]},
                   'anthropic': {'content': [{'text': answer}]},
                   'google': {'candidates': [{'content': {'parts': [{'text': answer}]}}]}}
        return io.BytesIO(json.dumps(payload.get(provider, payload['openai'])).encode())

    monkeypatch.setattr('urllib.request.urlopen', opener)
    out = post(api, 'market/ask', {'question': 'What does Kylon charge?'})
    with api.app.state.store.read() as c:
        audit = [dict(r) for r in c.execute("SELECT detail FROM events WHERE action='market.ask'")]
    assert out['answer'] == 'Kylon charges $20 monthly.', audit
    assert out['citations'] and seen
    with api.app.state.store.read() as c:
        detail = json.loads(c.execute("SELECT detail FROM events WHERE action='market.ask'").fetchone()[0])
    model = (providers.recommended(provider) or {})['id']
    if provider == 'openrouter':
        from clients.openrouter import model_id
        model = model_id(model)
    assert detail['model'] == model
    assert 'Kylon' not in json.dumps(detail) and 'synthetic-test-key' not in json.dumps(detail)


def test_any_term_search_keeps_three_companies_and_ranks_before_limit(api, monkeypatch):
    seed(api)
    configure(monkeypatch, 'openai')
    seen = []

    def writer(*args, **kwargs):
        def write(system, user):
            seen.append(user)
            return {'answer': 'Kylon, Birch and Cedar offer self-hosting.'}
        write.model = 'stub-model'
        return write

    monkeypatch.setattr(J, 'llm_writer', writer)
    out = post(api, 'market/ask', {'question': 'Which competitors sell self-hosting?'})
    assert out['answer'] == 'Kylon, Birch and Cedar offer self-hosting.'
    assert sum(c['kind'] == 'evidence' for c in out['citations']) >= 3
    assert all(name in seen[0] for name in ('Kylon', 'Birch', 'Cedar'))
    with api.app.state.store.read() as c:
        assert M.find(c, 'Kylon self-hosting', limit=1)['evidence'][0]['quote'].startswith('Kylon')
        c.execute('DROP TABLE market_fts')
        assert len(M.find(c, 'Which competitors sell self-hosting?')['evidence']) == 3
        assert M.find(c, 'Kylon self-hosting', limit=1)['evidence'][0]['quote'].startswith('Kylon')


def test_no_enabled_key_records_reason_and_returns_rows(api, monkeypatch):
    seed(api)
    configure(monkeypatch, None)
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: pytest.fail('No model should be called'))
    out = post(api, 'market/ask', {'question': 'What does Kylon charge?'})
    assert out['answer'].startswith('Pulled from the market graph.') and out['citations']
    with api.app.state.store.read() as c:
        detail = json.loads(c.execute("SELECT detail FROM events WHERE action='market.ask'").fetchone()[0])
    assert detail['error'] == 'unconfigured'


def test_tico_gateway_writes_answer_and_audit(api, monkeypatch):
    from backend import gateway_connection, models_preset
    seed(api)
    configure(monkeypatch, None)
    monkeypatch.setattr(models_preset, 'spends_gateway', lambda record: True)
    monkeypatch.setattr(gateway_connection, 'connected', lambda *a: True)
    monkeypatch.setattr(gateway_connection, 'url', lambda *a: 'https://gateway.example/v1')
    monkeypatch.setattr(gateway_connection, 'env_key', lambda *a: 'synthetic-gateway-key')
    monkeypatch.setattr(gateway_connection, 'target', lambda *a: (
        'synthetic-gateway-key', 'https://gateway.example/v1', None))
    seen = []

    def gateway_writer(key_of, base, model):
        assert key_of() == 'synthetic-gateway-key'
        def write(system, user):
            seen.append(user)
            return {'answer': 'Kylon charges $20 monthly.'}
        write.model = model
        return write

    monkeypatch.setattr(J, 'gateway_writer', gateway_writer)
    out = post(api, 'market/ask', {'question': 'What does Kylon charge?'})
    assert out['answer'] == 'Kylon charges $20 monthly.' and out['citations'] and seen
    with api.app.state.store.read() as c:
        detail = json.loads(c.execute("SELECT detail FROM events WHERE action='market.ask'").fetchone()[0])
    assert detail['model'].startswith('tico-')


def test_failed_writer_is_audited_without_losing_rows(api, monkeypatch):
    seed(api)
    def writer(system, user):
        raise ValueError('synthetic private provider error')
    writer.model = 'stub-model'
    monkeypatch.setattr('backend.updates.writer_for', lambda *a, **kw: writer)
    out = post(api, 'market/ask', {'question': 'What does Kylon charge?'})
    assert out['answer'].startswith('Pulled from the market graph.') and out['citations']
    with api.app.state.store.read() as c:
        detail = json.loads(c.execute("SELECT detail FROM events WHERE action='market.ask'").fetchone()[0])
    assert detail['model'] == 'stub-model' and detail['error'] == 'ValueError'
    assert 'private provider error' not in json.dumps(detail)


def test_rehearsal_does_not_call_model(api, monkeypatch):
    seed(api)
    api.app.state.store.settings.rehearsal = True
    monkeypatch.setattr(J, 'llm_writer', lambda *a, **kw: pytest.fail('No rehearsal model call'))
    out = post(api, 'market/ask', {'question': 'What does Kylon charge?'})
    assert out['answer'].startswith('Pulled from the market graph.') and out['citations']
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM events WHERE action='market.ask'").fetchone()
