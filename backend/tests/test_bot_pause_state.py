"""Pause banner projection over disposable synthetic fixtures only."""
import pytest

from backend import company_billing
from backend.store import H
from backend.tests.test_api import api, get, post, restrict  # noqa: F401
from backend.tests.test_openapi_v2 import headers


@pytest.fixture(autouse=True)
def fresh_pause_cache(monkeypatch):
    monkeypatch.setattr(company_billing, '_pause', {})
    monkeypatch.setattr(company_billing, 'CLOCK', lambda: 1000.0)


def read(api, token='ana-test'):
    return api.get('/api/v2/bots/ops/pause-state', headers=headers(token))


def test_pause_record_and_legacy_fallback(api, monkeypatch):
    monkeypatch.setattr(company_billing, 'paused', lambda *args: False)
    before = read(api).json()
    assert before['state'] == 'active' and before['can_control'] is True
    post(api, 'bots/ops/control', {'action': 'pause', 'expected_revision': before['revision']})
    paused = read(api).json()
    assert paused['state'] == 'paused'
    assert paused['paused_by']['name']
    assert paused['paused_by']['at'].endswith('Z')
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM events WHERE target='ops' AND action='bot.pause'")
    assert read(api).json()['paused_by'] is None


def test_only_readable_open_tasks_count_and_no_new_control_permission(api, monkeypatch):
    monkeypatch.setattr(company_billing, 'paused', lambda *args: False)
    post(api, 'tasks', {'owner_id': 'ops', 'title': 'Check visible work', 'body': 'Review the design.'})
    post(api, 'tasks', {'owner_id': 'ops', 'title': 'Check private work', 'body': 'Review the design.', 'private': True})
    mine = read(api, 'cara-test').json()
    assert mine['can_control'] is False and mine['open_tasks'] == 1
    post(api, 'bots/ops/control', {'action': 'pause', 'expected_revision': mine['revision']}, 'cara-test', expected=403)
    with api.app.state.store.transaction() as c:
        restrict(c, 'ops', see={'people': ['ana']}, read={'people': ['ana']}, write={'people': ['ana']})
    assert read(api, 'cara-test').status_code == 404


def test_billing_projection_excludes_money_and_record_only_is_not_a_pause(api, monkeypatch):
    billing = {'state': 'paused', 'record_only': False, 'balance_micros': -1000000, 'credits': ['private']}
    monkeypatch.setattr(company_billing, 'read', lambda *args: billing)
    value = read(api, 'cara-test').json()
    assert value['company_paused'] is True
    assert 'balance_micros' not in value and 'credits' not in value
    billing['record_only'] = True
    monkeypatch.setattr(company_billing, 'CLOCK', lambda: 1000.0 + company_billing.PAUSE_TTL + 1)
    assert read(api).json()['company_paused'] is False


def test_pause_bit_is_read_from_hq_once_per_minute(api, monkeypatch):
    calls = []
    def hq(*args):
        calls.append(args)
        return {'state': 'paused', 'record_only': False}
    monkeypatch.setattr(company_billing, 'read', hq)
    now = [1000.0]
    monkeypatch.setattr(company_billing, 'CLOCK', lambda: now[0])
    assert read(api, 'cara-test').json()['company_paused'] is True
    assert read(api, 'cara-test').json()['company_paused'] is True
    assert len(calls) == 1
    now[0] += company_billing.PAUSE_TTL - 1
    assert read(api).json()['company_paused'] is True
    assert len(calls) == 1
    now[0] += 2
    assert read(api).json()['company_paused'] is True
    assert len(calls) == 2


def test_paused_cache_is_per_gateway_key_and_read_stays_uncached(monkeypatch):
    calls = []
    def hq(key, base, *rest):
        calls.append((key, base))
        return {'state': 'active'}
    monkeypatch.setattr(company_billing, 'read', hq)
    assert company_billing.paused('k1', 'https://gw.example') is False
    assert company_billing.paused('k1', 'https://gw.example') is False
    assert company_billing.paused('k2', 'https://gw.example') is False
    assert calls == [('k1', 'https://gw.example'), ('k2', 'https://gw.example')]
