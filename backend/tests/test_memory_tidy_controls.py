"""Synthetic settings/admission tests; no model calls, live data or bot repositories."""
import json

import pytest

from backend import memory_tidy_controls as tidy, providers
from backend.tests.test_api import api, get, post, put  # noqa: F401

PATH = "system-routines/memory_tidy"


def enable(api):
    with api.app.state.store.transaction() as c:
        record = providers.load(c, api.app.state.store.settings)
        record['enabled'] = ['openai', 'anthropic']
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (providers.KEY, json.dumps(record)))


def entry(model='codex:gpt-6.1-sol', bot='ops', effort='low'):
    return dict(bot=bot, model=model, effort=effort)


def job(api, token='ana-test'):
    return next(j for j in get(api, 'system-routines', token=token)['jobs'] if j['key'] == 'memory_tidy')


def test_default_off_empty_and_no_admission_or_manual_run(api):
    enable(api)
    j = job(api)
    assert j['on'] is False and j['pilots'] == [] and j['next'] is None
    assert j['schedule'] == 'Nightly at 03:30'
    post(api, PATH + '/run', {}, expected=409)
    put(api, PATH, {'pilots': [entry()]})
    with api.app.state.store.read() as c:
        assert tidy.eligible(c, api.app.state.store.settings, 'ops') is None


def test_only_listed_bot_on_named_model_and_off_stops_all(api):
    enable(api)
    put(api, PATH, {'on': True, 'pilots': [entry()], 'at': '04:00'}, token='ben-test')
    with api.app.state.store.read() as c:
        assert tidy.eligible(c, api.app.state.store.settings, 'ops') == entry()
        assert tidy.eligible(c, api.app.state.store.settings, 'coo') is None
        assert tidy.eligible(c, api.app.state.store.settings, 'op') is None
    assert job(api)['at'] == '04:00'
    put(api, PATH, {'on': False})
    with api.app.state.store.read() as c:
        assert tidy.eligible(c, api.app.state.store.settings, 'ops') is None


@pytest.mark.parametrize('bad', [entry(model='claude:claude-haiku-5-5'), entry(model='unknown'),
    entry(bot='missing'), entry(effort='unsupported')])
def test_refused_list_is_atomic_including_switch_and_time(api, bad):
    enable(api)
    before = job(api)
    put(api, PATH, {'on': True, 'at': '04:00', 'pilots': [bad]}, expected=422)
    after = job(api)
    assert (after['on'], after['at'], after['pilots']) == (before['on'], before['at'], [])


def test_disabled_models_not_offered_or_saved_and_rechecked_at_admission(api):
    enable(api)
    put(api, PATH, {'on': True, 'pilots': [entry()]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key=?", ('{"enabled":["anthropic"]}', providers.KEY))
    assert 'codex:gpt-6.1-sol' not in {r['model'] for r in job(api)['pilot_models']}
    put(api, PATH, {'pilots': [entry()]}, expected=422)
    with api.app.state.store.read() as c:
        assert tidy.eligible(c, api.app.state.store.settings, 'ops') is None


def test_no_haiku_in_options_duplicate_refused_empty_clears(api):
    enable(api)
    assert all('haiku' not in (r['model'] + r['label']).lower() for r in job(api)['pilot_models'])
    put(api, PATH, {'pilots': [entry(), entry()]}, expected=422)
    put(api, PATH, {'pilots': [entry()]})
    assert put(api, PATH, {'pilots': []})['job']['pilots'] == []
    put(api, 'system-routines/learnings', {'pilots': []}, expected=422)


def test_members_cannot_change_or_run_or_read_private_pilot_list(api):
    enable(api)
    assert 'pilots' not in job(api, 'cara-test')
    for body in ({'on': True}, {'at': '04:00'}, {'pilots': [entry()]}):
        put(api, PATH, body, token='cara-test', expected=403)
    post(api, PATH + '/run', {}, token='cara-test', expected=403)


def test_confirmed_pilot_defaults_use_exact_slugs_low_effort_and_preserve_everyday_models(api):
    enable(api)
    store = api.app.state.store
    with store.transaction() as c:
        for p in tidy.DEFAULT_PILOTS:
            c.execute("INSERT INTO bots(id,slug,model,effort) VALUES(?,?,?,?)", ('id-' + p['bot'], p['bot'], 'claude-haiku-5-5', 'high'))
            c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES(?,?,'ana')", ('id-' + p['bot'], json.dumps(
                {'model': 'claude-haiku-5-5', 'task_model': {'model': 'gpt-6-luna'}})))
        before = [tuple(r) for r in c.execute("SELECT bot,config FROM bot_config ORDER BY bot")]
    by_id = [{**p, 'bot': 'id-' + p['bot'], 'slug': p['bot']} for p in tidy.DEFAULT_PILOTS]
    assert job(api)['pilots'] == by_id
    assert all(p['slug'] != 'pr-reviewer-sol' for p in job(api)['pilots'])
    assert put(api, PATH, {'pilots': tidy.DEFAULT_PILOTS})['job']['pilots'] == by_id
    with store.read() as c:
        assert [tuple(r) for r in c.execute("SELECT bot,config FROM bot_config ORDER BY bot")] == before
        assert c.execute("SELECT model,effort FROM bots WHERE slug='botops'").fetchone()[:] == ('claude-haiku-5-5', 'high')
    put(api, PATH, {'pilots': []})
    assert job(api)['pilots'] == []


def test_run_now_fails_closed_until_execution_is_installed(api):
    enable(api)
    put(api, PATH, {'on': True, 'pilots': [entry()]})
    post(api, PATH + '/run', {}, expected=409)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='memory_tidy'").fetchone()[0] == 0


def test_failed_schedule_save_rolls_back_the_pilot_edit(api):
    enable(api)
    put(api, PATH, {'pilots': [entry()], 'at': '25:00'}, expected=422)
    assert job(api)['pilots'] == []


def test_nightly_and_run_now_share_seam_and_off_never_calls_it(api, monkeypatch):
    from datetime import datetime, timezone

    enable(api)
    calls = []
    monkeypatch.setattr(tidy, 'run_now', lambda store, auth, actor: calls.append(actor) or True)
    at = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    store = api.app.state.store
    assert tidy.nightly(store, None, at) is None
    assert calls == []
    post(api, PATH + '/run', {}, expected=409)
    assert calls == []
    put(api, PATH, {'on': True})
    assert tidy.nightly(store, None, at) is True
    post(api, PATH + '/run', {})
    assert len(calls) == 2


def test_corrupt_saved_haiku_is_also_refused_at_runtime(api):
    enable(api)
    put(api, PATH, {'on': True})
    with api.app.state.store.transaction() as c:
        c.execute('INSERT OR REPLACE INTO registry_metadata VALUES(?,?)',
                  (tidy.PILOTS_KEY, json.dumps([entry(model='claude:claude-haiku-5-5')])))
        assert tidy.eligible(c, api.app.state.store.settings, 'ops') is None
