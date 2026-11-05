"""Fixes sources keep green CI from skipping feedback at the judged head."""
import json

import pytest

from backend import hubdb as H, step_flow as F, verdict_moves as V
from backend.tests.test_github import PR, _suite, api, hook, pr_event  # noqa: F401
from backend.tests.test_verdict_moves import HEAD, NEW, builder_jobs, check, step, verdict


@pytest.mark.parametrize('reviews,source', [(1, 'Review 1'), (2, 'Review 1'), (2, 'Review 2')])
@pytest.mark.parametrize('return_kind', ['failed_check', 'verdict_move', 'manual_approval', 'manual_before_verdict',
                                       'manual_after_old_failure'])
def test_every_return_to_fixes_keeps_the_source_for_a_green_merge(api, reviews, source, return_kind):
    store = api.app.state.store
    with store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Merge after fixes', [
            {'name': 'Build', 'status': 'doing', 'flow': {'after_green': 'merge'}},
            *[{'name': f'Review {n}', 'status': 'review',
               'flow': {'check': 'review', 'on_pass': 'Build', 'on_fail': 'Build'}}
              for n in range(1, reviews + 1)],
        ])
        task = H.task_create(c, 'human:ana', 'Build messages', '', 'bot:cpo', type=typ['id'],
                             step='Review 1' if return_kind == 'manual_after_old_failure' else source, lint=False)
        link = H.task_link(c, 'human:ana', task['id'], PR)
        c.execute("UPDATE task_links SET state='open',checks='passing',mergeable='clean',detail=? WHERE id=?",
                  (json.dumps({'head_sha': HEAD}), link['id']))
    judged = HEAD
    if return_kind == 'manual_after_old_failure':
        verdict(api, check(api, task), 'changes')
        hook(api, 'pull_request', pr_event('synchronize', head={'sha': NEW}))
        with store.transaction() as c:
            H.task_update(c, 'bot:cpo', task['id'], step=source)
        judged = NEW
    review = check(api, task, title=f'{source}: PR #412')
    if return_kind == 'verdict_move':
        verdict(api, review, 'approve')
    else:
        with store.transaction() as c:
            if return_kind != 'manual_before_verdict':
                H.task_update(c, review['owner_id'], review['id'], status='done', note='Reviewed.',
                              verdict='changes' if return_kind == 'failed_check' else 'approve', verdict_head=judged)
            H.task_update(c, 'bot:cpo', task['id'], step='Build')
            if return_kind == 'manual_after_old_failure':
                attempt = c.execute('SELECT id FROM attempts WHERE task_id=?', (review['id'],)).fetchone()[0]
                assert V.after_capture(c, review['id'], judged, attempt) is None

    with store.read_transaction() as c:
        current = H.task(c, task['id'])
        fixes = F.step(c, current['step_id'])
        assert fixes['name'] == 'Build'
        assert F.fixes_for(c, fixes, current)['name'] == source
        moved = c.execute("SELECT 1 FROM history_events WHERE subject_id=? AND kind='verdict_move'",
                          (task['id'],)).fetchone()
        assert bool(moved) == (return_kind in ('verdict_move', 'manual_after_old_failure'))

    if return_kind == 'manual_before_verdict':
        # The check finishes after the builder returns; its verdict still belongs to the source step.
        verdict(api, review, 'approve')
    with store.transaction() as c:
        current = H.task(c, task['id'])
        assert V._judged_by(c, current, F.step(c, current['step_id'])) == judged
        c.execute('DELETE FROM jobs')
    _suite(api, 'github-actions', 'completed', 'success', sha=judged)
    assert not [m for m in builder_jobs(api) if m['refs'].get(V.HANDOFF) == 'merge']
    pushed = 'e' * 40 if judged == NEW else NEW
    hook(api, 'pull_request', pr_event('synchronize', head={'sha': pushed}))
    _suite(api, 'github-actions', 'completed', 'success', sha=pushed)
    handoffs = [m for m in builder_jobs(api) if m['refs'].get(V.HANDOFF) == 'merge']
    assert len(handoffs) == 1
    assert handoffs[0]['refs']['pr_head'] == pushed
    assert step(api, task) == 'Build'


def test_an_approval_return_uses_a_step_whose_failure_goes_elsewhere(api):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Split feedback', [
            {'name': 'Build', 'status': 'doing', 'flow': {'after_green': 'merge'}},
            {'name': 'Changes', 'status': 'doing'},
            {'name': 'Review', 'status': 'review',
             'flow': {'check': 'review', 'on_pass': 'Build', 'on_fail': 'Changes'}},
        ])
        task = H.task_create(c, 'human:ana', 'Build messages', '', 'bot:cpo', type=typ['id'], step='Review', lint=False)
        link = H.task_link(c, 'human:ana', task['id'], PR)
        c.execute("UPDATE task_links SET state='open',checks='passing',mergeable='clean',detail=? WHERE id=?",
                  (json.dumps({'head_sha': HEAD}), link['id']))
    verdict(api, check(api, task, title='Review: PR #412'), 'approve')
    assert step(api, task) == 'Build'
    hook(api, 'pull_request', pr_event('synchronize', head={'sha': NEW}))
    _suite(api, 'github-actions', 'completed', 'success', sha=NEW)
    assert [m['refs']['pr_head'] for m in builder_jobs(api) if m['refs'].get(V.HANDOFF) == 'merge'] == [NEW]


@pytest.mark.parametrize('reviews,feedback,return_kind,pushed,source', [
    (1, [('Review 1', HEAD)], 'verdict', False, 'Review 1'),
    (1, [('Review 1', HEAD)], 'manual', False, 'Review 1'),
    (2, [('Review 1', HEAD)], 'verdict', False, 'Review 1'),
    (2, [('Review 2', HEAD)], 'verdict', False, 'Review 2'),
    (2, [('Review 1', HEAD)], 'manual', False, 'Review 1'),
    (2, [('Review 2', HEAD)], 'manual', False, 'Review 2'),
    (2, [('Review 1', HEAD), ('Review 2', HEAD)], 'manual', False, 'Review 2'),
    (2, [('Review 2', HEAD), ('Review 1', HEAD)], 'manual', False, 'Review 1'),
    (2, [('Review 1', HEAD), ('Review 2', HEAD)], 'manual_after_verdict', False, 'Review 2'),
    (2, [('Review 1', HEAD), ('Review 2', NEW)], 'manual', False, 'Review 1'),
    (1, [('Review 1', HEAD), ('Review 1', NEW)], 'manual', False, 'Review 1'),
    (1, [('Review 1', HEAD)], 'manual', True, 'Review 1'),
    (2, [('Review 2', HEAD)], 'verdict', True, 'Review 2'),
    (2, [('Review 1', HEAD), ('Review 2', HEAD)], 'manual', True, 'Review 2'),
])
def test_fixes_source_blocks_the_failed_head_until_a_new_commit(api, reviews, feedback, return_kind, pushed, source):
    store = api.app.state.store
    with store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, 'Feedback flow', [
            {'name': 'Build', 'status': 'doing', 'flow': {'after_green': 'QA'}},
            *[{'name': f'Review {n}', 'status': 'review',
               'flow': {'check': 'review', 'on_pass': 'Build', 'on_fail': 'Build'}}
              for n in range(1, reviews + 1)],
            {'name': 'QA', 'status': 'review', 'flow': {'check': 'qa', 'on_pass': 'merge'}},
        ])
        task = H.task_create(c, 'human:ana', 'Build messages', '', 'bot:cpo', type=typ['id'], step='Build', lint=False)
        link = H.task_link(c, 'human:ana', task['id'], PR)
        c.execute("UPDATE task_links SET state='open',checks='passing',mergeable='clean',detail=? WHERE id=?",
                  (json.dumps({'head_sha': HEAD}), link['id']))

    for n, (name, head) in enumerate(feedback):
        with store.transaction() as c:
            H.task_update(c, 'bot:cpo', task['id'], step=name)
        review = check(api, task, title=f'{name}: PR #412')
        if return_kind == 'verdict' or (return_kind == 'manual_after_verdict' and n == 0):
            verdict(api, review, 'changes', head=head)
        else:
            with store.transaction() as c:
                H.task_update(c, review['owner_id'], review['id'], status='done', note='Reviewed.',
                              verdict='changes', verdict_head=head)
                # The builder returns before capture, so this entry has no verdict_move event.
                H.task_update(c, 'bot:cpo', task['id'], step='Build')
                attempt = c.execute('SELECT id FROM attempts WHERE task_id=?', (review['id'],)).fetchone()[0]
                assert V.after_capture(c, review['id'], head, attempt) is None
    with store.read_transaction() as c:
        current = H.task(c, task['id'])
        fixes = F.step(c, current['step_id'])
        assert fixes['name'] == 'Build'
        assert F.fixes_for(c, fixes, current)['name'] == source
        assert V.verdict_route(c, current, HEAD)['verdict'] == 'changes'
        assert V._judged_by(c, current, fixes) == HEAD
        if return_kind == 'manual':
            assert not c.execute("SELECT 1 FROM history_events WHERE subject_id=? AND kind='verdict_move'",
                                 (task['id'],)).fetchone()

    # Exercise the webhook -> checks_done ticket flow as well as direct source selection.
    _suite(api, 'github-actions', 'completed', 'success', sha=HEAD)
    assert step(api, task) == 'Build'
    if pushed:
        hook(api, 'pull_request', pr_event('synchronize', head={'sha': NEW}))
        _suite(api, 'github-actions', 'completed', 'success', sha=NEW)
        assert step(api, task) == 'QA'
