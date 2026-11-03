"""Synthetic pilot histories only; no live credentials, repositories or databases."""
import json
import pytest

from backend import memory_tidy_report as R
from backend.store import H, Problem
from backend.tests.test_api import api, get, post, headers  # noqa: F401
from backend.tests.test_tasks_board import bot_token

START = '2026-11-02T00:00:00.000000Z'
NOW = '2026-11-16T00:00:00.000000Z'


def prep(api, monkeypatch):
    monkeypatch.setattr(H, 'now', lambda: NOW)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE registry_metadata SET value=? WHERE key=?', (START, R.LIVE))
        c.execute('INSERT OR REPLACE INTO registry_metadata VALUES(?,?)',
                  ('memory_tidy:pilots', json.dumps([{'bot': 'ops', 'model': 'codex:gpt-6.1-sol', 'effort': 'low'}])))
    return api.app.state.store


def passed(c, ident, status='no_change', cost=.1, when='2026-11-03T00:00:00.000000Z'):
    c.execute('INSERT INTO memory_tidy_passes(id,night,bot,model,started_at,deadline,status,reason,cost_usd) '
              'VALUES(?,?,?,?,?,?,?,?,?)', (ident, ident, 'ops', 'codex:gpt-6.1-sol', when, NOW, status,
              'skipped: no change' if status == 'skipped' else '', cost))


def card(c, ident, status, target=None):
    c.execute('INSERT INTO memory_tidy_cards(id,bot,reviewer,pass_id,memory_commit,model,cost_usd,diff,diary,edits,'
              'started_at,created_at,expires_at,status,decided_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (ident, 'ops', 'bot:cpo', ident, 'a' * 40, 'codex:gpt-6.1-sol', .1, 'fixture', 'fixture', '{}', START, START, NOW, status, START))
    if target:
        c.execute('INSERT INTO memory_tidy_keeps VALUES(?,?,?,?,?,?,?,?)',
                  ('keep-' + ident, ident, 'fixture', 'bot:cpo', START, 'kept', target, ''))


def test_cohort_counts_withdrawals_skips_late_decisions_and_lifetime_cost(api, monkeypatch):
    store = prep(api, monkeypatch)
    with store.transaction() as c:
        passed(c, 'kept', 'offered', .25)
        card(c, 'kept', 'kept', 'b' * 40)  # ledger sync has not run yet
        passed(c, 'discarded', 'offered', .3)
        card(c, 'discarded', 'discarded')
        passed(c, 'expired', 'offered', .4)
        card(c, 'expired', 'withdrawn')
        passed(c, 'free', 'skipped', 0)
        passed(c, 'empty', 'no_change', .2)
        passed(c, 'older', cost=49, when='2026-10-01T00:00:00.000000Z')
        c.execute('INSERT INTO memory_tidy_report_coverage VALUES(?,?,?)', ('ops', START, NOW))
        report = R.build(c, START, 1, NOW)
    w = report['bots'][0]['weekly']
    assert w['counts']['passes_run'] == 4
    assert {k: w['counts'][k] for k in ('kept', 'discarded', 'withdrawn')} == {'kept': 1, 'discarded': 1, 'withdrawn': 1}
    assert w['skipped_by_reason'] == {'skipped: no change': 1}
    assert w['kept_rate'] == .25 and w['estimated_cost_usd'] == pytest.approx(1.15)
    assert report['estimated_pilot_cost_usd'] == pytest.approx(50.15) and report['spend_stopped']
    assert w['reverted']['value'] == 0 and w['sizes'] is None
    assert len(w['per_pass']) == 5 and report['goal_pass'] is False
    assert 'baseline corrections' in report['bots'][0]['unknown']


def test_explicit_corrections_idempotent_and_self_corrections_refused(api, monkeypatch):
    store = prep(api, monkeypatch)
    body = {'bot': 'ops', 'occurred_at': START, 'evidence': 'https://acme.example/tasks/one#comment-two',
            'reason': 'The deployment date was wrong.'}
    post(api, 'memory-tidy/corrections', body)
    post(api, 'memory-tidy/corrections', body)
    post(api, 'memory-tidy/corrections', {**body, 'reason': 'Changed record'}, expected=409)
    post(api, 'memory-tidy/corrections', body, bot_token(api, 'ops'), expected=403)
    post(api, 'memory-tidy/corrections', {**body, 'evidence': 'not a link'}, expected=422)
    post(api, 'memory-tidy/corrections', {**body, 'evidence': 'https://acme.example/2', 'reason': 'two\nlines'}, expected=422)
    with store.read_transaction() as c:
        report = R.build(c, START, 1, NOW)['bots'][0]
        assert report['two_week']['corrections']['value'] == 1
        assert report['baseline']['corrections']['value'] is None
        assert c.execute('SELECT count(*) FROM memory_tidy_corrections').fetchone()[0] == 1
    response = api.request('DELETE', '/api/v2/memory-tidy/corrections',
                           json={'bot': 'ops', 'evidence': body['evidence']}, headers=headers())
    assert response.status_code == 200 and response.json() == {'recorded': False}
    with store.read_transaction() as c:
        assert R.build(c, START, 1, NOW)['bots'][0]['two_week']['corrections']['value'] == 0


def test_revert_reasons_and_gapped_coverage_remain_unknown(api, monkeypatch):
    store = prep(api, monkeypatch)
    target = 'b' * 40
    with store.transaction() as c:
        passed(c, 'one', 'offered')
        card(c, 'one', 'kept', target)
        reasonless = R.HistoryCommit(sha='c' * 40, committed='2026-11-04T00:00:00Z',
                                    message=f'Revert tidy\n\nThis reverts commit {target}.\n')
        earlier = R.HistoryCommit(sha='a' * 40, committed=START, message='An earlier memory commit')
        R.record_history(c, 'ops', [earlier, reasonless], START)
        R.record_history(c, 'ops', [earlier, reasonless], START)
        w = R.build(c, START, 1, NOW)['bots'][0]['weekly']
        assert w['reverted']['value'] == 1 and w['lost_rule_reverts']['value'] is None
        lost = R.HistoryCommit(sha='d' * 40, committed='2026-11-05T00:00:00Z',
            message=f'Revert tidy\n\nThis reverts commit {target}.\nReason: lost a rule\nLost-rule: Preserve sources\nEvidence: https://acme.example/tasks/one\n')
        R.record_history(c, 'ops', [lost])
        assert c.execute('SELECT count(*) FROM memory_tidy_reverts').fetchone()[0] == 2
        assert R.build(c, START, 1, NOW)['bots'][0]['weekly']['lost_rule_reverts']['recorded'] == 1
        c.execute('DELETE FROM memory_tidy_report_coverage')
        c.executemany('INSERT INTO memory_tidy_report_coverage VALUES(?,?,?)', [
            ('ops', START, '2026-11-04T00:00:00.000000Z'), ('ops', '2026-11-05T00:00:00.000000Z', NOW)])
        assert not R.covered(c, 'ops', START, NOW)
        assert R.build(c, START, 1, NOW)['bots'][0]['weekly']['reverted']['value'] is None


def test_reopened_events_use_historical_owner_and_count_each_reopen(api, monkeypatch):
    store = prep(api, monkeypatch)
    with store.transaction() as c:
        task = H.task_create(c, 'human:ana', 'Synthetic reopen history', 'Fixture', 'bot:ops', lint=False)
        for ident, when, field, old, new in [
            ('early', '2026-10-18T00:00:00Z', 'status', None, 'open'),
            ('r1', '2026-11-03T00:00:00Z', 'status', 'done', 'open'),
            ('r2', '2026-11-04T00:00:00Z', 'status', 'closed', 'open'),
            ('owner', '2026-11-05T00:00:00Z', 'owner', 'bot:ops', 'bot:cpo'),
            ('r3', '2026-11-06T00:00:00Z', 'status', 'closed', 'open')]:
            c.execute('INSERT INTO task_events(id,task_id,created_at,actor,field,old,new,note) VALUES(?,?,?,?,?,?,?,?)',
                      (ident, task['id'], when, 'human:ana', field, old, new, 'fixture'))
        c.execute('UPDATE tasks SET owner_id=? WHERE id=?', ('bot:cpo', task['id']))
        result = R.reopened(c, 'ops', START, NOW)
        assert result['value'] == 2
        assert R.reopened(c, 'ops', '2026-10-01T00:00:00Z', START)['value'] is None


def test_actual_snapshot_sizes_bytes_and_report_audience(api, monkeypatch):
    store = prep(api, monkeypatch)
    before = {'memory/learnings.md': '# Working rules\n- Keep é sources.\n\n## 2026-11-01\nA log.\n', 'knowledge/facts.md': 'é'}
    after = {**before, 'memory/learnings.md': '# Working rules\n- Sources.\n\n## 2026-11-01\nA log.\n'}
    with store.transaction() as c:
        passed(c, 'one', 'offered')
        card(c, 'one', 'kept', 'b' * 40)
        c.execute('INSERT INTO memory_tidy_keep_metrics VALUES(?,?,?,?,?)',
                  ('one', 'a' * 40, 'b' * 40, json.dumps(R.metrics(before)), json.dumps(R.metrics(after))))
    report = get(api, 'memory-tidy/report?pilot_start=' + START)['report']
    size = report['bots'][0]['weekly']['sizes']
    assert size['before']['rule_count'] == 1
    assert size['before']['learnings_bytes'] == len(before['memory/learnings.md'].encode())
    assert size['after']['rule_characters'] < size['before']['rule_characters']
    assert get(api, 'memory-tidy/report?pilot_start=' + START)['markdown'].startswith('# Nightly memory tidy')
    get(api, 'memory-tidy/report?pilot_start=' + START, bot_token(api, 'ops'), expected=403)
    get(api, 'memory-tidy/report?pilot_start=' + START + '&week=3', expected=422)


def test_late_recording_start_does_not_fabricate_zero(api, monkeypatch):
    store = prep(api, monkeypatch)
    with store.transaction() as c:
        c.execute('UPDATE registry_metadata SET value=? WHERE key=?', ('2026-11-03T00:00:00Z', R.LIVE))
        result = R.build(c, START, 1, NOW)['bots'][0]
        assert result['two_week']['corrections']['value'] is None
        assert result['two_week']['lost_rule_reverts']['value'] is None
        assert not result['goal_pass']


def test_completed_week_delivery_is_once_per_recipient_and_off_pilot_without_runs_sends_nothing(api, monkeypatch):
    store = prep(api, monkeypatch)
    monkeypatch.setattr(R, 'REVIEWER', 'bot:cpo')
    with store.transaction() as c:
        assert R.weekly(c, api.app.state.auth, NOW) == []
        passed(c, 'first', when=START)
        tasks = R.weekly(c, api.app.state.auth, NOW)
        assert len(tasks) == 4  # owner + reviewer, both completed pilot weeks
        assert R.weekly(c, api.app.state.auth, NOW) == []
        assert {H.task(c, t)['owner_id'] for t in tasks} == {'human:ana', 'bot:cpo'}
        assert all(H.task(c, t)['private'] for t in tasks)
        assert all('not a pass' in H.task(c, t)['body'] for t in tasks)


def test_goal_requires_complete_evidence_and_every_unchanged_threshold(api, monkeypatch):
    store = prep(api, monkeypatch)
    baseline = '2026-10-18T00:00:00.000000Z'
    sizes = R.metrics({'memory/learnings.md': '# Working rules\n- Keep sources.\n\n## Log\nA note.\n'})
    with store.transaction() as c:
        c.execute('UPDATE registry_metadata SET value=? WHERE key=?', (baseline, R.LIVE))
        c.execute('INSERT INTO memory_tidy_report_coverage VALUES(?,?,?)', ('ops', baseline, NOW))
        task = H.task_create(c, 'human:ana', 'Synthetic historical evidence', 'Fixture', 'bot:ops', lint=False)
        c.execute('INSERT INTO task_events(id,task_id,created_at,actor,field,new) VALUES(?,?,?,?,?,?)',
                  ('old-event', task['id'], baseline, 'human:ana', 'status', 'open'))
        passed(c, 'kept', 'offered')
        card(c, 'kept', 'kept', 'b' * 40)
        c.execute('INSERT INTO memory_tidy_keep_metrics VALUES(?,?,?,?,?)',
                  ('kept', 'a' * 40, 'b' * 40, json.dumps(sizes), json.dumps(sizes)))
        assert R.build(c, START, 2, NOW)['goal_pass']
        c.execute('INSERT INTO memory_tidy_corrections VALUES(?,?,?,?,?,?)',
                  ('ops', 'https://acme.example/task/1', START, 'Wrong date.', 'human:ana', NOW))
        result = R.build(c, START, 2, NOW)
        assert not result['goal_pass'] and result['bots'][0]['thresholds']['corrections_no_higher'] is False
        c.execute('DELETE FROM memory_tidy_corrections')
        passed(c, 'not-kept-1')
        passed(c, 'not-kept-2')
        result = R.build(c, START, 2, NOW)
        assert not result['goal_pass'] and result['bots'][0]['thresholds']['kept_at_least_60_percent'] is False
