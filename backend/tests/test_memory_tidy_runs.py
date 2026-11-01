"""Admission fixtures only: disposable team, no provider calls or real repositories."""
import json
from datetime import datetime, timezone

import pytest

from backend import memory_tidy_runs as runs
from backend.store import H, Problem
from backend.tests.test_api import api, put  # noqa: F401
from backend.tests.test_memory_tidy_controls import enable, entry

AT = datetime(2026, 11, 2, 11, 30, tzinfo=timezone.utc)  # 03:30 Pacific, after DST


def setup(api):
    enable(api)
    put(api, 'system-routines/memory_tidy', {'on': True, 'pilots': [entry()]})
    return api.app.state.store


def commit(c, subject='ordinary', files=None, attempt='', shared=True, committed='2026-11-02T10:00:00Z', bot='ops'):
    c.execute('INSERT INTO bot_memory_updates(bot,sha,subject,author,committed,files_json,attempt_id,shared,runner_id,reported) '
              'VALUES(?,?,?,?,?,?,?,?,?,?)', (bot, H.new_id(), subject, 'ops', committed,
              json.dumps(files if files is not None else ['memory/learnings.md']), attempt, int(shared), 'fixture', committed))


def last(c, status='discarded', cost=0):
    c.execute('INSERT INTO memory_tidy_passes(id,night,bot,model,started,deadline,status,baseline,cost_usd) '
              'VALUES(?,?,?,?,?,?,?,?,?)', ('past', '2026-11-01', 'ops', entry()['model'],
              '2026-11-01T11:30:00Z', '2026-11-01T13:00:00Z', status, '2026-11-01T11:30:00Z', cost))


def reasons(c):
    return [r[0] for r in c.execute("SELECT reason FROM memory_tidy_passes WHERE id!='past'")]


def test_no_change_is_free_and_logs_without_creating_any_task(api, monkeypatch):
    store = setup(api)
    monkeypatch.setattr(runs.check_models, 'choose', lambda *a, **kw: pytest.fail('model path reached'))
    with store.transaction() as c:
        last(c)
        commit(c, 'Dreamed 2026-11-01')
        commit(c, files=['knowledge/fact.md'])
        commit(c, shared=False)
        before = c.execute('SELECT count(*) FROM tasks').fetchone()[0]
        assert len(runs.admit(c, store.settings, AT)) == 1
        assert reasons(c) == ['skipped: no change']
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == before
        assert 'skipped: no change' in c.execute("SELECT result FROM system_runs WHERE job='memory_tidy'").fetchone()[0]
        assert runs.admit(c, store.settings, AT) == []


@pytest.mark.parametrize('reason', ['skipped: model floor', 'skipped: spend limit'])
def test_ordered_skips_leave_no_partial_parent_or_check(api, monkeypatch, reason):
    store = setup(api)
    if reason.endswith('spend limit'):
        monkeypatch.setattr(runs.usage_limits, 'blocked', lambda *a: 'daily')
    with store.transaction() as c:
        commit(c)
        if reason.endswith('model floor'):
            c.execute('UPDATE registry_metadata SET value_json=? WHERE key=?',
                      (json.dumps([entry(model='claude:claude-haiku-5-5')]), runs.controls.PILOTS_KEY))
        before = c.execute('SELECT count(*) FROM tasks').fetchone()[0]
        runs.admit(c, store.settings, AT)
        assert reasons(c) == [reason]
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == before


def test_off_and_nonpilot_never_enter_model_path(api, monkeypatch):
    store = setup(api)
    monkeypatch.setattr(runs.check_models, 'choose', lambda *a, **kw: pytest.fail('model path reached'))
    with store.transaction() as c:
        commit(c)
    put(api, 'system-routines/memory_tidy', {'on': False})
    with store.transaction() as c:
        assert runs.admit(c, store.settings, AT) == []
    put(api, 'system-routines/memory_tidy', {'on': True, 'pilots': []})
    with store.transaction() as c:
        assert runs.admit(c, store.settings, AT) == []


@pytest.mark.parametrize('total', [50, 51])
def test_stop_precedes_every_other_skip_and_sends_one_product_report(api, monkeypatch, total):
    store = setup(api)
    with store.transaction() as c:
        H.sync_registry(c, {'product-lead': {'name': 'Product', 'status': 'active', 'runtime': 'fake'}}, {})
        last(c, cost=total)
        # No memory change: the spend stop must still win.
        runs.admit(c, store.settings, AT)
        assert reasons(c) == ['skipped: spend stop']
        assert c.execute("SELECT count(*) FROM tasks WHERE owner='bot:product-lead'").fetchone()[0] == 1
        runs.admit(c, store.settings, AT)
        assert c.execute("SELECT count(*) FROM tasks WHERE owner='bot:product-lead'").fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM task_check_models').fetchone()[0] == 0


def test_runtime_guard_unavailable_rolls_back_all_partial_work(api, monkeypatch):
    store = setup(api)
    monkeypatch.delattr(runs.check_models, 'file_pass', raising=False)
    with store.transaction() as c:
        commit(c)
    with pytest.raises(Problem, match='not available'):
        with store.transaction() as c:
            runs.admit(c, store.settings, AT)
    with store.read() as c:
        assert c.execute('SELECT count(*) FROM memory_tidy_passes').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM task_check_models').fetchone()[0] == 0


def test_terminal_model_unavailability_is_free_and_has_no_substitute(api, monkeypatch):
    store = setup(api)
    monkeypatch.setattr(runs.check_models, 'file_pass', lambda *a: pytest.fail('must not file'), raising=False)
    def unavailable(*a, **kw):
        assert a[3] == entry()['model'] and kw == {'memory_pass': True}
        raise runs.check_models.CheckModelError('runner_not_ready', 'signed out')
    monkeypatch.setattr(runs.check_models, 'choose', unavailable)
    with store.transaction() as c:
        commit(c)
        runs.admit(c, store.settings, AT)
        assert reasons(c) == ['skipped: model unavailable']
        assert c.execute('SELECT count(*) FROM task_check_models').fetchone()[0] == 0


def test_deadline_quietly_closes_child_and_parent_even_when_switch_is_off(api):
    store = setup(api)
    with store.transaction() as c:
        parent = H.task_create(c, H.KEEPER, 'Synthetic nightly container', '', 'bot:ops',
                               private=True, quiet_notice=True, lint=False)
        child = H.task_create(c, H.KEEPER, 'Synthetic pass check', '', 'bot:ops', parent_id=parent['id'],
                              private=True, quiet_notice=True, lint=False)
        last(c, status='filed')
        c.execute('UPDATE memory_tidy_passes SET parent_task_id=?,check_task_id=?', (parent['id'], child['id']))
        before = c.execute('SELECT count(*) FROM jobs').fetchone()[0]
        runs.sweep(c, AT)
        assert [H.task(c, t)['status'] for t in (parent['id'], child['id'])] == ['closed', 'closed']
        assert c.execute('SELECT count(*) FROM jobs').fetchone()[0] == before
        assert c.execute('SELECT reason FROM memory_tidy_passes').fetchone()[0] == 'skipped: not finished'


def test_subscription_estimates_count_and_zero_reports_never_erase_cost(api):
    store = setup(api)
    with store.transaction() as c:
        last(c)
        c.execute("UPDATE memory_tidy_passes SET check_task_id='synthetic-check'")
        c.execute("INSERT INTO turns(id,bot,task_id,started,model,input_tokens,cached_tokens,output_tokens,"
                  "est_cost_usd,billing) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  ('synthetic-turn', 'ops', 'synthetic-check', '2026-11-01T11:31:00Z',
                   'gpt-6.1-sol', 1000000, 0, 100000, 0, 'subscription'))
        assert runs.refresh_costs(c) == pytest.approx(3)
        c.execute("UPDATE turns SET input_tokens=0,output_tokens=0")
        assert runs.refresh_costs(c) == pytest.approx(3)


def test_free_skip_excludes_pass_turn_commits_with_ordinary_subjects(api):
    store = setup(api)
    with store.transaction() as c:
        last(c)
        c.execute("UPDATE memory_tidy_passes SET check_task_id='synthetic-check'")
        c.execute("INSERT INTO turns(id,bot,task_id) VALUES('synthetic-turn','ops','synthetic-check')")
        commit(c, subject='ordinary looking pass commit', attempt='synthetic-turn')
        assert not runs.changed(c, 'ops', '2026-11-01T11:30:00Z')
        commit(c)
        assert runs.changed(c, 'ops', '2026-11-01T11:30:00Z')


def test_trusted_result_files_card_before_closure_without_parent_wake(api, monkeypatch):
    from backend.tests.test_memory_tidy import snapshot, proposal
    store = setup(api)
    monkeypatch.setattr(H, 'now', lambda: '2026-11-01T11:45:00.000000Z')
    with store.transaction() as c:
        parent = H.task_create(c, H.KEEPER, 'Synthetic private container', '', 'bot:ops',
                               private=True, quiet_notice=True, lint=False)
        child = H.task_create(c, H.KEEPER, 'Synthetic private child', '', 'bot:ops',
                              parent_id=parent['id'], private=True, quiet_notice=True, lint=False)
        last(c, status='filed')
        c.execute('UPDATE memory_tidy_passes SET parent_task_id=?,check_task_id=?', (parent['id'], child['id']))
        before = c.execute('SELECT count(*) FROM jobs').fetchone()[0]
        snap = snapshot()
        card = runs.deliver(c, 'past', snapshot=snap, raw=json.dumps(proposal(snap)),
            checkout_before={'manifest': 'same'}, checkout_after={'manifest': 'same'},
            conflict_check=lambda agent, rules: [], memory_commit='a' * 40, reviewer='human:ana')
        assert card and card['status'] == 'open'
        assert H.task(c, child['id'])['status'] == H.task(c, parent['id'])['status'] == 'closed'
        assert c.execute('SELECT count(*) FROM jobs').fetchone()[0] == before
        assert runs.deliver(c, 'past', snapshot={}, raw='', checkout_before={}, checkout_after={},
            conflict_check=None, memory_commit='a' * 40, reviewer='human:ana') is None
        c.execute("UPDATE memory_tidy_cards SET status='discarded'")
        runs.sync_decisions(c)
        assert c.execute("SELECT status FROM memory_tidy_passes").fetchone()[0] == 'discarded'


def test_cancelled_child_cannot_leave_partial_card(api, monkeypatch):
    store = setup(api)
    monkeypatch.setattr(H, 'now', lambda: '2026-11-01T11:45:00.000000Z')
    with store.transaction() as c:
        last(c, status='filed')
        assert runs.deliver(c, 'past', snapshot={}, raw='', checkout_before={}, checkout_after={},
            conflict_check=None, memory_commit='a' * 40, reviewer='human:ana') is None
        assert c.execute('SELECT count(*) FROM memory_tidy_cards').fetchone()[0] == 0
        assert c.execute('SELECT reason FROM memory_tidy_passes').fetchone()[0] == 'skipped: model unavailable'


def test_global_slot_survives_deadline_until_usage_is_reconciled(api):
    store = setup(api)
    with store.transaction() as c:
        last(c, status='filed', cost=49)
        child = H.task_create(c, H.KEEPER, 'Synthetic overdue check', '', 'bot:ops',
                              private=True, quiet_notice=True, lint=False)
        c.execute('UPDATE memory_tidy_passes SET check_task_id=?', (child['id'],))
        c.execute("INSERT INTO turns(id,bot,task_id,started,model) VALUES(?,?,?,?,?)",
                  ('pending-turn', 'ops', child['id'], '2026-11-01T12:59:00Z', 'gpt-6.1-sol'))
        # The deadline closes the task but its running model is not yet settled.
        runs.sweep(c, AT)
        assert runs.unsettled(c)
        c.execute("UPDATE turns SET finished=?,usage_reason='no_result'", (runs.stamp(AT),))
        assert runs.unsettled(c), 'an expired lease with missing usage is not a free pass'
        c.execute("UPDATE turns SET input_tokens=1000000,output_tokens=100000,usage_reason=NULL")
        assert not runs.unsettled(c)
        # The historical estimate never decreases; reconcile a cost past the stop.
        c.execute("UPDATE memory_tidy_passes SET cost_usd=51")
        H.sync_registry(c, {'product-lead': {'name': 'Product', 'status': 'active', 'runtime': 'fake'}}, {})
        commit(c)
        runs.admit(c, store.settings, AT)
        assert reasons(c) == ['skipped: spend stop']
        report = c.execute("SELECT body FROM tasks WHERE owner='bot:product-lead'").fetchone()[0]
        assert '$51.00' in report and '$1.00' in report


def test_one_filed_check_holds_the_next_nights_admission(api, monkeypatch):
    store = setup(api)
    with store.transaction() as c:
        commit(c)
        last(c, status='filed')
        monkeypatch.setattr(runs.check_models, 'choose', lambda *a, **kw: pytest.fail('second pass admitted'))
        # Retain a live slot across a new nightly date; night uniqueness is insufficient.
        c.execute("UPDATE memory_tidy_passes SET deadline='2026-11-03T13:00:00.000000Z'")
        assert runs.admit(c, store.settings, AT) == []
        assert c.execute('SELECT count(*) FROM memory_tidy_passes').fetchone()[0] == 1


def test_claim_rechecks_changed_model_without_fallback_or_parent_wake(api):
    store = setup(api)
    with store.transaction() as c:
        parent = H.task_create(c, H.KEEPER, 'Synthetic container', '', 'bot:ops',
                               private=True, quiet_notice=True, lint=False)
        child = H.task_create(c, H.KEEPER, 'Synthetic child', '', 'bot:ops',
                              parent_id=parent['id'], private=True, quiet_notice=True, lint=False)
        last(c, status='filed')
        c.execute("UPDATE memory_tidy_passes SET parent_task_id=?,check_task_id=?,deadline=?",
                  (parent['id'], child['id'], '2026-11-03T13:00:00.000000Z'))
        before = c.execute('SELECT count(*) FROM jobs').fetchone()[0]
        # A model change after filing cannot run the old model or its substitute.
        c.execute('UPDATE registry_metadata SET value_json=? WHERE key=?',
                  (json.dumps([entry(model='claude:claude-sonnet-5-5')]), runs.controls.PILOTS_KEY))
        assert not runs.before_start(c, store.settings, child['id'], AT)
        assert c.execute('SELECT reason FROM memory_tidy_passes').fetchone()[0] == 'skipped: model unavailable'
        assert c.execute('SELECT count(*) FROM jobs').fetchone()[0] == before
        assert H.task(c, child['id'])['status'] == H.task(c, parent['id'])['status'] == 'closed'


def test_two_eligible_bots_serialize_and_next_admission_uses_first_cost(api, monkeypatch):
    store = setup(api)
    put(api, 'system-routines/memory_tidy', {'pilots': [entry(), entry(bot='coo')]})
    filed = []
    monkeypatch.setattr(runs.check_models, 'choose', lambda *a, **kw: {'harness': 'codex'})
    monkeypatch.setattr(runs, 'packet_runner', lambda *a: True)
    monkeypatch.setattr(runs.check_models, 'file_pass', lambda c, settings, task, model: filed.append(task))
    with store.transaction() as c:
        H.sync_registry(c, {'product-lead': {'name': 'Product', 'status': 'active', 'runtime': 'fake'}}, {})
        commit(c)
        commit(c, bot='coo')
        last(c, cost=49)
        first = runs.admit(c, store.settings, AT)
        assert len(first) == len(filed) == 1
        assert runs.admit(c, store.settings, AT) == []
        assert len(filed) == 1
        # Completion and reconciled subscription usage release the global slot.
        c.execute("INSERT INTO turns(id,bot,task_id,started,finished,model,input_tokens,output_tokens,billing) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", ('turn-first', 'ops', filed[0], runs.stamp(AT), runs.stamp(AT),
                                               'gpt-6.1-sol', 1000000, 100000, 'subscription'))
        c.execute("UPDATE memory_tidy_passes SET status='no_change' WHERE id=?", (first[0],))
        second = runs.admit(c, store.settings, AT)
        assert len(second) == 1 and len(filed) == 1
        assert c.execute('SELECT reason FROM memory_tidy_passes WHERE id=?', (second[0],)).fetchone()[0] == 'skipped: spend stop'
        assert runs.refresh_costs(c) == pytest.approx(52)
        assert c.execute("SELECT count(*) FROM tasks WHERE owner='bot:product-lead'").fetchone()[0] == 1
