"""Fixes sources keep green CI from skipping feedback at the judged head."""
import json

import pytest

from backend import hubdb as H, step_flow as F, verdict_moves as V
from backend.tests.test_github import PR, _suite, api, hook, pr_event  # noqa: F401
from backend.tests.test_verdict_moves import HEAD, NEW, check, step, verdict


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
        with store.transaction() as c:
            # Verdict recency must not depend on request ids or clock resolution.
            c.execute('UPDATE tasks SET updated_at=? WHERE id=?', (f'2026-01-01T00:00:0{n}Z', review['id']))

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
