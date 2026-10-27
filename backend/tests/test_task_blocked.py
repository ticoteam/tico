"""Blocked work has one state across gates, dependencies, clocks and the board."""
import json

import pytest

from backend import hubdb as H
from backend.tests.test_review_gate import api, ticket, move, HEAD, get
from backend.tests.test_github import post


def test_blocked_step_survives_restart_and_is_before_work(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        typ = H.type_get(c, task['type_id'])
        assert [s['name'] for s in typ['steps']][:2] == ['Blocked', 'In Progress']
        H.migrate(c)
        assert H.type_get(c, typ['id'])['steps'] == typ['steps']


def test_restart_stamps_unstamped_builds_and_writes_nothing_once_done(api):
    """Every start runs this in a transaction that has already read; a write there fails at once if another
    connection committed meanwhile, so a start with nothing to stamp must not write."""
    import sqlite3
    from backend import task_blocked
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='doing',building_started_at=NULL,building_seconds=0 WHERE id=?", (task['id'],))
        task_blocked.ensure_steps(c)
        assert H.task(c, task['id'])['building_started_at']
    path = api.app.state.store.settings.db_path
    reader = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    reader.row_factory = sqlite3.Row
    other = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    try:
        reader.execute('SAVEPOINT start')
        reader.execute('SELECT count(*) FROM tasks').fetchone()
        other.execute("UPDATE tasks SET title=title||'' WHERE id=?", (task['id'],))
        task_blocked.ensure_steps(reader)
        reader.execute('RELEASE start')
    finally:
        reader.close()
        other.close()


def test_owner_block_keeps_reason_time_and_resume_step(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, task['owner'], task['id'], step='Blocked', note='Waiting for the test machine')
        blocked = H.task(c, task['id'])
        assert blocked['status'] == 'waiting'
        assert blocked['blocked_reason'] == 'Waiting for the test machine'
        assert blocked['blocked_since']
        H.task_update(c, task['owner'], task['id'], note='Still waiting')
        assert H.task(c, task['id'])['blocked_since'] == blocked['blocked_since']
        H.task_update(c, task['owner'], task['id'], status='doing')
        resumed = H.task(c, task['id'])
        assert resumed['step_id'] == task['step_id']
        assert resumed['blocked_since'] is None


def test_gate_refusal_commits_block_without_advancing_review(api):
    task = ticket(api)
    assert move(api, task).status_code == 422
    current = get(api, 'tasks/' + task['id'])['task']
    assert current['step']['name'] == 'Blocked'
    assert current['status'] == 'waiting'
    assert 'receipt' in current['blocked_reason']
    # The screen keeps a gate hold's own line instead of a bot wait's (ui/app/tasks.js taskBotWait).
    assert current['blocked_kind'] == 'gate'
    assert current['version'] > task['version']
    with api.app.state.store.transaction() as c:
        from backend import task_blocked
        H.task_update(c, task['owner'], task['id'], note=f'Tests: head {HEAD}: 9 passed, 0 failed')
        task_blocked.reconcile(c, task['id'])
        # Tico still retries the refused move (backend/move_retry.py), so the hold is left for it to make.
        assert H.task(c, task['id'])['blocked_kind'] == 'gate'
    from backend import move_retry
    assert move_retry.tick(api.app.state.github_app, force=True) == [task['id']]
    assert get(api, 'tasks/' + task['id'])['task']['step']['name'] == 'Review 1'


def test_two_dependencies_block_until_both_clear(api):
    task = ticket(api)
    children = [post(api, 'tasks', {'owner': 'cmo', 'title': f'Prepare resource {i}', 'body': 'Prepare it.'}) for i in range(2)]
    from backend import task_relations as TR
    with api.app.state.store.transaction() as c:
        for child in children:
            TR.add_blocker(c, H.KEEPER, task['id'], child['id'])
        assert H.task(c, task['id'])['status'] == 'waiting'
        moved = H.task_update(c, task['owner'], task['id'], step='In Progress')
        assert 'remains Blocked' in moved['warnings'][0]
        assert H.task(c, task['id'])['status'] == 'waiting', 'an explicit move cannot bypass a live dependency'
        TR.remove_blocker(c, H.KEEPER, task['id'], children[0]['id'])
        assert H.task(c, task['id'])['status'] == 'waiting'
        H.task_update(c, H.KEEPER, children[1]['id'], status='done')
        assert H.task(c, task['id'])['step_id'] == task['step_id']


def test_scheduler_discovers_and_clears_a_person_child_wait(api):
    from datetime import datetime, timezone, timedelta
    from backend.scheduler import Scheduler
    task = ticket(api)
    store = api.app.state.store
    with store.transaction() as c:
        child = H.task_create(c, 'human:ana', 'Choose a machine', 'Choose one.', 'human:ana', parent_id=task['id'])
    scheduler = Scheduler(store, api.app.state.execution)
    at = datetime.now(timezone.utc)
    scheduler.tick(at)
    with store.transaction() as c:
        blocked = H.task(c, task['id'])
        assert blocked['status'] == 'waiting' and blocked['blocked_kind'] == 'dependency'
        H.task_update(c, 'human:ana', child['id'], status='done')
    scheduler.tick(at + timedelta(minutes=1))
    with store.read() as c:
        assert H.task(c, task['id'])['step_id'] == task['step_id']
        assert H.task(c, task['id'])['blocked_since'] is None


def test_scheduler_clears_receipt_gate_without_a_stall_candidate(api):
    from datetime import datetime, timezone
    from backend.scheduler import Scheduler
    task = ticket(api)
    assert move(api, task).status_code == 422
    store = api.app.state.store
    with store.transaction() as c:
        H.task_update(c, task['owner'], task['id'], note=f'Tests: head {HEAD}: 9 passed, 0 failed')
        assert task['id'] not in [r['id'] for r in H.stalled_tasks(c)]
    Scheduler(store, api.app.state.execution).tick(datetime.now(timezone.utc))
    from backend import move_retry
    assert move_retry.tick(api.app.state.github_app, force=True) == [task['id']]
    assert get(api, 'tasks/' + task['id'])['task']['step']['name'] == 'Review 1'
    with store.read() as c:
        assert H.task(c, task['id'])['blocked_since'] is None


def test_scheduler_clears_ci_hold_after_real_green_webhook(api, monkeypatch):
    from datetime import datetime, timezone
    from backend import review_gate
    from backend.scheduler import Scheduler
    from backend.tests.test_github import _suite
    task = ticket(api, f'Tests: head {HEAD}: 9 passed, 0 failed')
    store = api.app.state.store
    with store.transaction() as c:
        row = H.task(c, task['id'])
        pr = review_gate.links(c, row)[0]
        with pytest.raises(H.Refused) as refused:
            review_gate.check(c, row['owner'], row, {pr['id']: {
                'head_sha': HEAD, 'checks': {'github-actions': 'failing'}, 'at': H.now()}})
        review_gate.notify(c, row['owner'], refused.value.review_gate)
        observed = H.shift(H.now(), seconds=1)
    monkeypatch.setattr(H, 'now', lambda: observed)
    _suite(api, 'github-actions', 'completed', 'success')
    Scheduler(store, api.app.state.execution).tick(datetime.now(timezone.utc))
    with store.read() as c:
        assert H.task(c, task['id'])['blocked_since'] is None
        assert H.task(c, task['id'])['step_id'] == task['step_id']


def test_note_only_wait_reopens_but_explicit_owner_hold_stays(api):
    from backend import task_blocked
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, task['owner'], task['id'], status='waiting', note='Waiting on a vendor reply')
        row = H.task(c, task['id'])
        assert row['blocked_kind'] == 'stranded'
        # Startup migration must preserve recovery for legacy note-only waits too.
        c.execute('UPDATE tasks SET blocked_since=NULL,blocked_kind=NULL WHERE id=?', (task['id'],))
        task_blocked.backfill(c)
        assert H.task(c, task['id'])['blocked_kind'] == 'stranded'
        assert H.sweep_stranded(c, H.shift(H.now(), hours=3)) == [(task['id'], 'open')]
        H.task_update(c, task['owner'], task['id'], step='Blocked', note='Waiting for a machine')
        assert H.sweep_stranded(c, H.shift(H.now(), hours=3)) == []
        assert H.task(c, task['id'])['blocked_kind'] == 'manual'


@pytest.mark.parametrize('kind', ['gate', 'worktree'])
def test_stranded_sweep_leaves_gate_and_worktree_holds_to_reconciliation(api, kind):
    """Only reconciliation knows when a gate or worktree hold clears (fresh CI, no unlinked pushes): the stranded
    sweep must neither reopen one nor overwrite its note with "nothing is outstanding"."""
    from backend import task_blocked
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        task_blocked.hold(c, task['id'], 'Review refused: CI failing', kind)
        held = H.task(c, task['id'])
        assert held['status'] == 'waiting' and held['blocked_kind'] == kind
        assert H.sweep_stranded(c, H.shift(H.now(), hours=H.STRANDED_GRACE_HOURS + 1)) == []
        after = H.task(c, task['id'])
        assert (after['status'], after['note'], after['blocked_kind'], after['blocked_since']) == (
            'waiting', held['note'], kind, held['blocked_since'])
    assert get(api, 'tasks/' + task['id'])['task']['blocked_kind'] == kind


def test_stranded_sweep_preserves_general_worktree_hold(api):
    """Unlinked pushes stay outstanding on General even without a remembered move."""
    from backend import move_retry, review_gate
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, 'human:ana', 'Prepare the offsite plan', 'Write the plan.', 'bot:cpo')
        assert task['type_id'] == H.GENERAL_TYPE
        assert not review_gate.engineering(c, task)
        # Worktree holds can remain after a ticket moves onto another board.
        H.task_update(c, task['owner'], task['id'], status='waiting', note='Waiting for unlinked pushes')
        c.execute("UPDATE tasks SET blocked_kind='worktree',blocked_reason=? WHERE id=?",
                  ('Waiting for unlinked pushes', task['id']))
        held = H.task(c, task['id'])
        assert held['blocked_since']
        assert move_retry.remembered(c, held) is None
        assert H.sweep_stranded(c, H.shift(H.now(), hours=H.STRANDED_GRACE_HOURS + 1)) == []
        after = H.task(c, task['id'])
        for field in ('status', 'note', 'blocked_kind', 'blocked_reason', 'blocked_since', 'version'):
            assert after[field] == held[field], field


@pytest.mark.parametrize('with_dependency', [False, True])
def test_worktree_hold_moved_to_general_resumes_once_after_linking_pushes(api, with_dependency):
    from backend import review_gate, task_blocked, task_relations as TR
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        branch = f"tico/{task['id'][:8]}-offsite-plan"
        tree = H.new_id()
        c.execute("INSERT INTO task_links(id,task_id,kind,url,state,added_by,created,repo,branch,detail_json) "
                  "VALUES(?,?,'worktree',?,'present',?,?,'Acme/product',?,?)",
                  (tree, task['id'], 'worktree:' + tree, task['owner'], H.now(), branch,
                   json.dumps({'pushed': 'd' * 40, 'expected_head': 'b' * 40})))
        assert H.unlinked_pushes(c, task['id'])
        task_blocked.hold(c, task['id'], 'Waiting for unlinked pushes', 'worktree')
        held = H.task(c, task['id'])
        if with_dependency:
            dependency = H.task_create(c, 'human:ana', 'Choose the venue', 'Choose one.', 'human:ana')
            TR.add_blocker(c, 'human:ana', task['id'], dependency['id'])
        H.task_update(c, 'human:ana', task['id'], type=H.GENERAL_TYPE)
        moved = H.task(c, task['id'])
        assert moved['status'] == 'waiting' and moved['blocked_kind'] == 'worktree'
        assert moved['blocked_from_step'] == held['blocked_from_step']
        assert not review_gate.engineering(c, moved)
        assert H.sweep_stranded(c, H.shift(H.now(), hours=H.STRANDED_GRACE_HOURS + 1)) == []
        task_blocked.sweep(c, H.now())
        assert H.task(c, task['id'])['version'] == moved['version']
        assert H.task(c, task['id'])['note'] == moved['note']
        link = H.task_link(c, 'human:ana', task['id'], 'https://github.com/Acme/product/pull/88')
        assert link['kind'] == 'pr'
        assert H.unlinked_pushes(c, task['id']) == []
        if with_dependency:
            task_blocked.sweep(c, H.now())
            assert H.task(c, task['id'])['status'] == 'waiting'
            assert H.task(c, task['id'])['version'] == moved['version']
            # Isolate the scheduled recovery from the dependency-completion path's own wake.
            c.execute("UPDATE tasks SET status='done' WHERE id=?", (dependency['id'],))
        candidates = task_blocked.candidates(c, H.now())
        assert task['id'] in candidates
        task_blocked.sweep(c, H.now(), candidates)
        resumed = H.task(c, task['id'])
        assert resumed['status'] == held['blocked_from_status'] == 'doing'
        assert resumed['step_id'] != held['blocked_from_step']
        if resumed['step_id']:
            assert H._one(c, 'SELECT type_id FROM task_steps WHERE id=?',
                          (resumed['step_id'],))['type_id'] == H.GENERAL_TYPE
        for field in ('blocked_since', 'blocked_kind', 'blocked_reason', 'blocked_from_step', 'blocked_from_status'):
            assert resumed[field] is None, field
        assert resumed['version'] > moved['version']
        task_blocked.sweep(c, H.now())
        assert H.task(c, task['id'])['version'] == resumed['version']
        wakes = H._rows(c.execute("SELECT * FROM messages WHERE to_actor=? "
                                  "AND json_extract(refs_json,'$.task')=? AND body LIKE 'Worktree hold cleared:%'",
                                  (task['owner'], task['id'])))
        assert len(wakes) == 1
        assert json.loads(wakes[0]['refs_json'])['news'] == 'assignment'


def test_review_fix_steps_accrue_active_building_time(api, monkeypatch):
    from backend import task_blocked
    task = ticket(api)
    at = '2026-10-26T01:00:00.000000Z'
    monkeypatch.setattr(H, 'now', lambda: at)
    with api.app.state.store.transaction() as c:
        fixes = c.execute("SELECT id FROM task_steps WHERE type_id=? AND name='Review 1 Fixes'", (task['type_id'],)).fetchone()[0]
        c.execute('UPDATE tasks SET building_started_at=NULL,building_seconds=0 WHERE id=?', (task['id'],))
        H.task_update(c, task['owner'], task['id'], step=fixes)
        at = '2026-10-26T02:00:00.000000Z'
        H.task_update(c, task['owner'], task['id'], step='Blocked', note='Waiting for a machine')
        at = '2026-10-26T07:00:00.000000Z'
        assert task_blocked.clocks(H.task(c, task['id']), at)['building_seconds'] == 3600


def test_blocked_time_pauses_build_clock_and_age_alert_is_once(api, monkeypatch):
    task = ticket(api)
    from backend import task_blocked
    at = '2026-10-26T01:00:00.000000Z'
    monkeypatch.setattr(H, 'now', lambda: at)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET building_started_at=? WHERE id=?', ('2026-10-26T00:00:00.000000Z', task['id']))
        H.task_update(c, task['owner'], task['id'], step='Blocked', note='Waiting for a machine')
        at = '2026-10-26T06:00:00.000000Z'
        blocked = H.task(c, task['id'])
        assert task_blocked.clocks(blocked, at)['building_seconds'] == 3600
        assert task_blocked.clocks(blocked, at)['blocked_seconds'] == 18000
        # A type manager gets one alert for this blocked episode.
        c.execute('UPDATE task_types SET manager=? WHERE id=?', ('cmo', task['type_id']))
        task_blocked.sweep(c, at)
        task_blocked.sweep(c, at)
        assert c.execute("SELECT count(*) FROM events WHERE action='task.blocked_age' AND target=?", (task['id'],)).fetchone()[0] == 1
        H.task_update(c, task['owner'], task['id'], status='doing')
        at = '2026-10-26T07:00:00.000000Z'
        assert task_blocked.clocks(H.task(c, task['id']), at)['building_seconds'] == 7200


def test_age_alert_skips_a_waiting_task_on_another_board(api, monkeypatch):
    """The four-hour alert belongs to engineering holds: an ordinary task on another board that waits on its
    own subtask gets no Blocked alert to its type manager."""
    from backend import review_gate, task_blocked
    at = '2026-10-26T01:00:00.000000Z'
    monkeypatch.setattr(H, 'now', lambda: at)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, 'human:ana', 'Plan the offsite', 'Book a venue.', 'bot:cpo')
        assert not review_gate.engineering(c, task)
        c.execute('UPDATE task_types SET manager=? WHERE id=?', ('cmo', task['type_id']))
        H.task_create(c, 'bot:cpo', 'Choose a venue', 'Choose one.', 'human:ana', parent_id=task['id'])
        H.task_update(c, 'bot:cpo', task['id'], status='waiting', note='Waiting on Ana to choose a venue')
        held = H.task(c, task['id'])
        assert held['blocked_since'] and held['blocked_kind'] == 'dependency'
        at = '2026-10-26T06:00:00.000000Z'
        assert task['id'] not in task_blocked.candidates(c, at)
        task_blocked.sweep(c, at)
        assert H.task(c, task['id'])['blocked_since'] == held['blocked_since']
        assert c.execute("SELECT count(*) FROM events WHERE action='task.blocked_age' AND target=?", (task['id'],)).fetchone()[0] == 0


def test_owner_blocked_move_is_a_manual_hold_that_never_reopens(api):
    """The write's hint must match what happens: the owner's own Blocked move is a manual hold, which the stranded
    sweep leaves alone, so it gets no "goes back to open" warning."""
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        parked = H.task_update(c, task['owner'], task['id'], step='Blocked', note='Waiting for a machine')
        assert H.task(c, task['id'])['blocked_kind'] == 'manual'
        assert not any('goes back to open' in w for w in parked.get('warnings') or [])
        H.sweep_stranded(c, at=H.shift(H.now(), hours=48))
        assert H.task(c, task['id'])['status'] == 'waiting'


def test_fresh_ci_refusal_cannot_be_cleared_by_older_cached_green(api):
    from backend import review_gate, task_blocked
    task = ticket(api, f'Tests: head {HEAD}: 9 passed, 0 failed')
    with api.app.state.store.transaction() as c:
        row = H.task(c, task['id'])
        pr = review_gate.links(c, row)[0]
        with pytest.raises(H.Refused) as refused:
            review_gate.check(c, row['owner'], row, {pr['id']: {
                'head_sha': HEAD, 'checks': {'python': 'failing'}, 'at': H.now()}})
        review_gate.notify(c, row['owner'], refused.value.review_gate)
        event_count = c.execute('SELECT count(*) FROM events').fetchone()[0]
        task_blocked.reconcile(c, task['id'])
        assert H.task(c, task['id'])['blocked_since']
        assert c.execute('SELECT count(*) FROM events').fetchone()[0] == event_count, 'probing never records another refusal'
        c.execute('UPDATE task_links SET updated=?,detail_json=? WHERE id=?', (
            H.shift(H.now(), seconds=1), json.dumps({'head_sha': HEAD, 'checks': {'python': 'passing'}}), pr['id']))
        task_blocked.reconcile(c, task['id'])
        assert H.task(c, task['id'])['step_id'] == task['step_id']


def test_person_dependency_resumes_the_previous_review_step(api):
    from backend import task_blocked, task_relations as TR
    task = ticket(api, f'Tests: head {HEAD}: 9 passed, 0 failed')
    assert move(api, task, 'Review 2').status_code == 200
    with api.app.state.store.transaction() as c:
        review_step = H.task(c, task['id'])['step_id']
        answer = H.task_create(c, 'human:ana', 'Choose a test machine', 'Choose a machine.', 'human:ana', parent_id=task['id'])
        TR.add_blocker(c, H.KEEPER, task['id'], answer['id'])
        blocked = H.task(c, task['id'])
        assert blocked['status'] == 'waiting' and blocked['blocked_from_step'] == review_step
        from backend import auto_tasks
        assert auto_tasks.review(c, answer), 'a review child keeps its cap exemption while its parent is Blocked'
        H.task_update(c, 'human:ana', answer['id'], status='done')
        task_blocked.reconcile(c, task['id'])
        assert H.task(c, task['id'])['step_id'] == review_step
        assert H.task(c, task['id'])['status'] == 'review'


def test_undecided_approval_blocks_and_decision_resumes_the_previous_step(api):
    from backend import task_blocked
    task = ticket(api, f'Tests: head {HEAD}: 9 passed, 0 failed')
    assert move(api, task, 'Review 2').status_code == 200
    with api.app.state.store.transaction() as c:
        review_step = H.task(c, task['id'])['step_id']
        ask = H.approval_request(c, task['owner'], 'merge', {'repo': 'acme/app', 'pr': 12}, task_id=task['id'])
        task_blocked.sweep(c, H.now())
        blocked = H.task(c, task['id'])
        assert blocked['status'] == 'waiting' and blocked['blocked_kind'] == 'dependency'
        assert blocked['blocked_reason'] == 'Waiting on Ana: a merge approval' and blocked['blocked_from_step'] == review_step
        task_blocked.sweep(c, H.now())
        assert H.task(c, task['id'])['status'] == 'waiting', 'an undecided approval keeps holding'
        H.approval_decide(c, 'human:ana', ask['id'], 'approved')
        task_blocked.sweep(c, H.now())
        resumed = H.task(c, task['id'])
        assert resumed['step_id'] == review_step and resumed['status'] == 'review'
        assert resumed['blocked_since'] is None


def test_explicit_milestone_wait_blocks_and_resumes_without_a_task_completion(api):
    from backend import milestone_waits as MW
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        MW.add(c, task['owner'], H.task(c, task['id']), 'release:acme/app@v1.2.3')
        blocked = H.task(c, task['id'])
        assert blocked['status'] == 'waiting' and blocked['blocked_since']
        MW.happened(c, 'release_published', 'acme/app@v1.2.3')
        assert H.task(c, task['id'])['step_id'] == task['step_id']
        assert H.task(c, task['id'])['blocked_since'] is None


def test_sweep_discovers_person_wait_and_keeps_private_age_alert_private(api):
    from backend import task_blocked
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        answer = H.task_create(c, 'human:ana', 'Choose a machine', 'Choose one.', 'human:ana', parent_id=task['id'])
        c.execute('UPDATE tasks SET private=1 WHERE id=?', (task['id'],))
        c.execute('UPDATE task_types SET manager=? WHERE id=?', ('cmo', task['type_id']))
        task_blocked.sweep(c, H.now(), task_ids=[])
        assert H.task(c, task['id'])['status'] == 'doing', 'a scoped sweep cannot mutate unrelated work'
        task_blocked.sweep(c, H.now())
        row = H.task(c, task['id'])
        assert row['status'] == 'waiting' and row['blocked_kind'] == 'dependency'
        task_blocked.sweep(c, H.shift(row['blocked_since'], hours=5))
        assert not c.execute("SELECT 1 FROM events WHERE action='task.blocked_age' AND target=?", (task['id'],)).fetchone()
        H.task_update(c, 'human:ana', answer['id'], status='done')
        task_blocked.sweep(c, H.now())
        assert H.task(c, task['id'])['step_id'] == task['step_id']


@pytest.mark.parametrize('title', ['Review 1: PR #412', 'QA: PR #412', 'Security: PR #412'])
def test_pipeline_child_keeps_review_and_allows_fix_move(api, title):
    from datetime import datetime, timezone
    from backend.scheduler import Scheduler
    from backend import task_blocked, task_relations as TR
    task = ticket(api, f'Tests: head {HEAD}: 9 passed, 0 failed')
    assert move(api, task).status_code == 200
    store = api.app.state.store
    with store.transaction() as c:
        child = H.task_create(c, task['owner'], title, 'Check the current candidate.', 'bot:cmo', parent_id=task['id'])
        fixes = c.execute("SELECT id FROM task_steps WHERE type_id=? AND name='Review 1 Fixes'", (task['type_id'],)).fetchone()[0]
        review_step = H.task(c, task['id'])['step_id']
    Scheduler(store, api.app.state.execution).tick(datetime.now(timezone.utc))
    with store.transaction() as c:
        row = H.task(c, task['id'])
        assert row['step_id'] == review_step and row['status'] == 'review'
        # Repair a hold persisted before this upgrade, even while its review remains open.
        task_blocked.hold(c, task['id'], 'Waiting on an open child task')
        task_blocked.reconcile(c, task['id'])
        assert H.task(c, task['id'])['step_id'] == review_step
        H.task_update(c, task['owner'], task['id'], step=fixes)
        assert H.task(c, task['id'])['step_id'] == fixes
        person = H.task_create(c, 'human:ana', 'Security: confirm the vendor contract', 'Choose one.', 'human:ana', parent_id=task['id'])
        task_blocked.sweep(c, H.now(), task_ids=[task['id']])
        assert H.task(c, task['id'])['status'] == 'waiting', 'a check child must not hide another child wait'
        H.task_update(c, 'human:ana', person['id'], status='done')
        # An explicit dependency on that same child remains a real hold.
        TR.add_blocker(c, H.KEEPER, task['id'], child['id'])
        assert H.task(c, task['id'])['status'] == 'waiting'


def test_person_wait_does_not_replace_owner_manual_hold(api):
    from backend import task_blocked
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, task['owner'], task['id'], step='Blocked', note='Waiting for a machine')
        H.task_comment(c, task['owner'], task['id'], 'Which machine?', ask={'questions': [
            {'id': 'machine', 'header': '', 'question': 'Which machine?',
             'options': [{'label': 'A'}, {'label': 'B'}], 'multi': False, 'other': True}], 'who': 'human:ana'})
        H.task_update(c, task['owner'], task['id'], status='waiting', waiting_on='human:ana')
        assert H.task(c, task['id'])['blocked_kind'] == 'manual'
        H.supersede_asks(c, task['id'])
        H.task_update(c, task['owner'], task['id'], waiting_on='')
        task_blocked.reconcile(c, task['id'])
        assert H.task(c, task['id'])['blocked_kind'] == 'manual'
        assert H.task(c, task['id'])['blocked_reason'] == 'Waiting for a machine'


def test_scheduler_does_not_write_check_an_unheld_engineering_task(api, monkeypatch):
    from datetime import datetime, timezone
    from backend.scheduler import Scheduler
    from backend import task_blocked
    task = ticket(api)
    called = []
    original = task_blocked.sweep
    def record(c, at, task_ids=None):
        called.extend(task_ids or [])
        return original(c, at, task_ids)
    monkeypatch.setattr(task_blocked, 'sweep', record)
    Scheduler(api.app.state.store, api.app.state.execution).tick(datetime.now(timezone.utc))
    assert task['id'] not in called


def test_an_old_blocker_does_not_put_a_merged_ticket_back_on_hold(api):
    """A step whose flow says can_hold false (Pending Release on a Dev board) is never held: a merged change is
    reverted or fixed forward, not Blocked."""
    from backend import step_flow, task_relations as TR
    task = ticket(api)
    old = post(api, 'tasks', {'owner': 'cmo', 'title': 'Restore an old check', 'body': 'Old.'})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_steps SET flow_json=? WHERE id=?", (json.dumps({"can_hold": False}), task['step_id']))
        TR.add_blocker(c, H.KEEPER, task['id'], old['id'])
        row = H.task(c, task['id'])
        assert (row['step_id'], row['blocked_since']) == (task['step_id'], None)
        assert step_flow.can_hold(c, H.type_get(c, task['type_id'])['steps'][0]['id']), "other steps still hold"
