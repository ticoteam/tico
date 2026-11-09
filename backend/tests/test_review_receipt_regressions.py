"""Review entry uses current CI evidence without losing real same-head failures."""

import json
from types import SimpleNamespace

import pytest

from backend import hubdb as H, test_receipts, review_gate
from backend.tests.helpers.github import api, get, headers, hook, post, pr_event, PR  # noqa: F401
from backend.tests.helpers.review_gate import HEAD, OLD, ticket, move  # noqa: F401
from backend import bot_settings as BS


def github(api, monkeypatch, checks=(), statuses=()):
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *a: ('synthetic', 'later'))
    calls = []

    def fetch(method, path, **kwargs):
        calls.append((path, kwargs.get('params')))
        data = (
            {'head': {'sha': HEAD}}
            if '/pulls/' in path
            else {'check_runs': list(checks)}
            if path.endswith('check-runs')
            else list(statuses)
        )
        return SimpleNamespace(status_code=200, json=lambda: data)

    monkeypatch.setattr(service, '_call', fetch)
    return calls


def _row_ticket(api, receipt=None, **fields):
    """`ticket` for one row of a folded table: the first row makes the board and its ticket; each later row files one
    more ticket of the same board under its own title and leaves it the only task holding the PR link."""
    with api.app.state.store.read() as c:
        typ = H.type_get(c, 'Dev tickets')
        n = c.execute('SELECT count(*) FROM tasks').fetchone()[0]
    if not typ:
        return ticket(api, receipt, **fields)
    task = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': f'Improve messages, fold row {n}',
            'body': 'Build messages.',
            'type': typ['id'],
            'step': 'Build',
            'links': [PR],
            **fields,
        },
    )
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    with api.app.state.store.transaction() as c:
        c.execute('DELETE FROM task_links WHERE task_id<>?', (task['id'],))
        if receipt:
            c.execute('UPDATE tasks SET note=? WHERE id=?', (receipt, task['id']))
    return get(api, 'tasks/' + task['id'])['task']


FRESH_ALL_GREEN_ROWS = [
    ('Review 1', None),
    ('Review 1', f'Tests: head {HEAD}: 2 failed'),
    ('Review 1', f'Tests: head {OLD}: 9 passed'),
    ('Review 2', None),
]


def _fresh_all_green_opens_review_without_text_and_overrides_text_failure(api, monkeypatch, step, receipt):
    task = _row_ticket(api, receipt)
    github(
        api,
        monkeypatch,
        [{'id': 1, 'name': 'python', 'conclusion': 'success'}, {'id': 2, 'name': 'browser', 'conclusion': 'success'}],
    )
    response = move(api, task, step)
    assert response.status_code == 200, response.text
    child = api.post(
        '/api/v2/tasks',
        headers=headers(),
        json={
            'owner': 'cmo',
            'title': step + ': PR #412',
            'body': 'Check current code.',
            'relations': [{'task': task['id'], 'kind': 'parent'}],
        },
    )
    assert child.status_code == 200, child.text


ABSENT_PENDING_OR_SKIPPED_ROWS = [
    ([], []),
    ([{'id': 1, 'name': 'python', 'conclusion': None}], []),
    ([{'id': 1, 'name': 'python', 'conclusion': 'skipped'}], []),
]


def _absent_pending_or_only_skipped_checks_still_need_passing_receipt(api, monkeypatch, checks, statuses):
    task = _row_ticket(api)
    github(api, monkeypatch, checks, statuses)
    response = move(api, task)
    assert response.status_code == 422 and 'passing test receipt' in response.text, response.text
    assert 'manager' in response.text and 'person' in response.text
    with api.app.state.store.transaction() as c:
        H.task_comment(c, task['owner_id'], task['id'], f'Tests: head {HEAD}: 9 passed, 0 failed', wake=False)
    assert move(api, task).status_code == 200


def _cached_webhook_green_does_not_supply_the_receipt(api):
    # Without a fresh GitHub read the gate has only the webhook-built cache, where a neutral check reads as passing and a re-run replaces a failure.
    task = _row_ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE task_links SET detail=json_set(detail,'$.checks',json(?)) WHERE task_id=? AND kind='pr'",
            (json.dumps({'python': 'passing'}), task['id']),
        )
    response = move(api, task)
    assert response.status_code == 422 and 'passing test receipt' in response.text, response.text


def _github_failed_attempt_still_blocks_after_green_rerun(api, monkeypatch, source):
    task = _row_ticket(api, f'Tests: head {HEAD}: 9 passed')
    calls = github(
        api,
        monkeypatch,
        checks=[
            {'id': 2, 'name': 'python', 'conclusion': 'success'},
            {'id': 1, 'name': 'python', 'conclusion': 'failure'},
        ]
        if source == 'checks'
        else [],
        statuses=[{'context': 'python', 'state': 'success'}, {'context': 'python', 'state': 'failure'}]
        if source == 'statuses'
        else [],
    )
    response = move(api, task)
    assert response.status_code == 422, response.text
    assert 'same head' in response.text and 'rerun' in response.text and 'new head' in response.text
    assert all(params.get('filter') == 'all' for path, params in calls if path.endswith('check-runs'))


def _success_with_skipped_companion_counts_but_pending_does_not(api, monkeypatch, pending):
    task = _row_ticket(api)
    github(
        api,
        monkeypatch,
        [
            {'id': 1, 'name': 'python', 'conclusion': 'success'},
            {'id': 2, 'name': 'optional', 'conclusion': None if pending else 'skipped'},
        ],
    )
    response = move(api, task)
    assert response.status_code == (422 if pending else 200), response.text


def _successful_commit_status_can_supply_the_receipt(api, monkeypatch):
    task = _row_ticket(api)
    github(api, monkeypatch, statuses=[{'context': 'tests', 'state': 'success'}])
    assert move(api, task).status_code == 200


def _one_green_pr_does_not_supply_another_prs_receipt(api, monkeypatch):
    task = _row_ticket(api)
    post(api, 'tasks/' + task['id'] + '/links', {'url': PR.replace('/412', '/413')})
    github(api, monkeypatch, [{'id': 1, 'name': 'python', 'conclusion': 'success'}])
    service = api.app.state.github_app
    fetch = service._call
    monkeypatch.setattr(
        service,
        '_call',
        lambda method, path, **kw: (
            SimpleNamespace(status_code=200, json=lambda: {'head': {'sha': OLD}})
            if '/pulls/413' in path
            else SimpleNamespace(status_code=200, json=lambda: {'check_runs': []})
            if OLD in path and path.endswith('check-runs')
            else fetch(method, path, **kw)
        ),
    )
    response = move(api, task)
    assert response.status_code == 422 and OLD in response.text, response.text


def test_a_fresh_github_read_decides_when_ci_stands_in_for_the_receipt(api, subtests):
    """Every fresh-read row in one hub: each row files its own ticket (the only one holding the PR link) and patches
    GitHub inside its own MonkeyPatch context, so the unconfigured-cache row reads the webhook cache as on a fresh hub."""
    for step, receipt in FRESH_ALL_GREEN_ROWS:
        with subtests.test('fresh_all_green', step=step, receipt=receipt), pytest.MonkeyPatch.context() as mp:
            _fresh_all_green_opens_review_without_text_and_overrides_text_failure(api, mp, step, receipt)
    for checks, statuses in ABSENT_PENDING_OR_SKIPPED_ROWS:
        with subtests.test('absent_pending_or_skipped', checks=checks), pytest.MonkeyPatch.context() as mp:
            _absent_pending_or_only_skipped_checks_still_need_passing_receipt(api, mp, checks, statuses)
    with subtests.test('cached_webhook_green'):
        _cached_webhook_green_does_not_supply_the_receipt(api)
    for source in ('checks', 'statuses'):
        with subtests.test('failed_attempt_after_green_rerun', source=source), pytest.MonkeyPatch.context() as mp:
            _github_failed_attempt_still_blocks_after_green_rerun(api, mp, source)
    for pending in (False, True):
        with subtests.test('skipped_or_pending_companion', pending=pending), pytest.MonkeyPatch.context() as mp:
            _success_with_skipped_companion_counts_but_pending_does_not(api, mp, pending)
    with subtests.test('commit_status_receipt'), pytest.MonkeyPatch.context() as mp:
        _successful_commit_status_can_supply_the_receipt(api, mp)
    with subtests.test('one_green_pr_per_pr'), pytest.MonkeyPatch.context() as mp:
        _one_green_pr_does_not_supply_another_prs_receipt(api, mp)


@pytest.mark.parametrize(
    'text,status',
    [
        (f'Tests at head {HEAD}: 9 passed after fixing the earlier failure', 'passed'),
        (f'Tests at head {HEAD}: 9 passed; fixed the failure at head {OLD}', 'passed'),
        (f'Tests at head {HEAD}: 9 passed; the earlier error is now repaired', 'passed'),
        (f'Tests at head {HEAD}: 9 passed; 2 failures now fixed', 'passed'),
        (f'Tests at head {HEAD}: 9 passed after fixing 2 earlier failures', 'passed'),
        (f'Tests at head {HEAD}: 9 passed; tests at head {OLD}: 2 failed', 'passed'),
        (f'Tests at head {HEAD}: 9 passed, failure at head {OLD}', 'passed'),
        (f'Tests: base {OLD}, head {HEAD}: 9 passed', 'passed'),
        (f'Tests at head {HEAD}: 9 passed after tests at head {OLD}: 2 failed', 'passed'),
        (f'Tests at head {HEAD}: 2 failed after fixing the earlier failure', 'failed'),
        (f'Tests at head {HEAD}: 9 passed, 1 failed; fixed an earlier failure', 'failed'),
        (f'Tests at head {HEAD}: 9 passed; 1 failed', 'failed'),
        (f'Tests completed and head {HEAD}: 9 passed', 'passed'),
        (f'Tests at head {OLD}: 2 failed. Tests at head {HEAD}: 9 passed', 'failed'),
    ],
)
def test_historical_failure_phrases_are_not_current_run_results(text, status):
    receipt = test_receipts.recognise(text)
    assert receipt['status'] == status
    if receipt['head'] == OLD:
        assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] != 'failed'


def test_two_heads_in_one_line_keep_their_own_outcomes():
    text = f'Tests at head {HEAD}: 9 passed; tests at head {OLD}: 2 failed'
    assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] == 'passed'
    assert test_receipts.at_head({'id': 't', 'note': text}, OLD)[0] == 'failed'


@pytest.mark.parametrize(
    'result,status',
    [
        ('9 passed; 2 failures were fixed', 'passed'),
        ('9 passed; 2 failures were repaired', 'passed'),
        ('9 passed; 2 failing tests were fixed', 'passed'),
        ('9 passed; 2 errors are repaired', 'passed'),
        ('9 passed; 2 failing tests have been resolved', 'passed'),
        ('9 passed; 2 failed tests had already been corrected', 'passed'),
        ('9 passed; 2 failing tests were fixed; 1 failed', 'failed'),
        ('9 passed; 2 failures were not fixed', 'failed'),
        ('9 passed; the earlier failure is recurring', 'failed'),
        ('9 passed; the previous error is still present', 'failed'),
        ('9 passed; 2 failures were fixed; the earlier failure is recurring', 'failed'),
    ],
)
def test_qa_repair_phrases_preserve_unresolved_and_recurring_failures(result, status):
    note = f'Tests at head {HEAD}: {result}'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


@pytest.mark.parametrize(
    'result,status',
    [
        ('9 passed; 2 failures were fixed. Re-run: 2 failed', 'failed'),
        ('9 passed; 2 failures were fixed. Rerun: 2 failing', 'failed'),
        ('9 passed; 2 failures were fixed. Latest run: 2 failed, 9 passed', 'failed'),
        ('9 passed. Re-run: 2 failed', 'failed'),
        ('9 passed. The latest result: 2 failed', 'failed'),
        ('9 passed; 2 failures were fixed. Re-run: 9 passed, 0 failed', 'passed'),
        ('9 passed. Earlier run: 2 failures were fixed', 'passed'),
        ('9 passed; the earlier failure has recurred', 'failed'),
        ('9 passed; the earlier failure reoccurred', 'failed'),
        ('9 passed; the earlier failure has returned', 'failed'),
        ('9 passed; the earlier failure keeps happening', 'failed'),
        ('9 passed; the earlier failure is persistent', 'failed'),
        ('9 passed; the earlier failure fixed but still present', 'failed'),
        ('9 passed; the earlier failure was fixed but is still present', 'failed'),
        ('9 passed; the previous errors have been repaired but still persistent', 'failed'),
        ('9 passed; the earlier failure was fixed but is no longer present', 'passed'),
        ('9 passed; the earlier failure has not recurred', 'passed'),
        ('9 passed; the earlier failure is no longer persistent', 'passed'),
        (f'9 passed. Latest run: 2 failed at head {OLD}', 'passed'),
        ('9 passed; 2 failures were fixed. Re-run failed: 2', 'failed'),
        ('9 passed; 2 failures were fixed but they are back', 'failed'),
        ('9 passed; 2 failures were fixed, but it is still present', 'failed'),
        ('9 passed. The earlier failure has recurred.', 'failed'),
        ('9 passed; 2 failures were fixed. Re-run: 11 passed', 'passed'),
    ],
)
def test_qa_rerun_counts_and_recurrence_stay_with_their_head(result, status):
    note = f'Tests at head {HEAD}: {result}'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


@pytest.mark.parametrize(
    'result,status',
    [
        ('9 passed. The 2 failures seen at the prior head have been fixed.', 'passed'),
        ('9 passed. 2 earlier failures fixed.', 'passed'),
        (f'9 passed. Re-run at head {HEAD}: 2 failed.', 'failed'),
        ('9 passed. 2 failures were fixed. Re-run failed: 2', 'failed'),
        ('9 passed. 2 failures fixed but they are back', 'failed'),
        ('9 passed. The failure was fixed, but it is still present', 'failed'),
        ('9 passed. Re-run: 2 failed', 'failed'),
        ('7 passed, 2 failed', 'failed'),
        # Only where-it-was-seen words may sit between a failure and its repair.
        ('9 passed. 2 failed tests need fixing', 'failed'),
        ('9 passed. 2 failures found on the last run are not fixed', 'failed'),
        ('9 passed. The 2 failures seen at the prior head have been fixed but are back.', 'failed'),
        (f'9 passed. Re-run at head {OLD}: 2 failed.', 'passed'),
        ('9 passed. 2 prior failures', 'failed'),
        # A repair word that only describes where the failure happened repairs nothing.
        ('9 passed, 2 failed on the commit fixing the parser.', 'failed'),
        ('9 passed, 2 errors from the resolved merge conflict.', 'failed'),
        ('9 passed, 2 failed in the repaired fixture.', 'failed'),
    ],
)
def test_repaired_prior_failures_and_headed_rerun_failures_after_1546(result, status):
    note = f'Tests at head {HEAD}: {result}'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


@pytest.mark.parametrize(
    'note,status',
    [
        # A repaired failure count is not a test word: these were no receipt before #1588.
        (f'Lint at head {HEAD}: passed after 2 errors fixed.', 'missing'),
        (f'Deploy at head {HEAD}: success; 2 prior errors resolved.', 'missing'),
        (f'Re-run at head {HEAD}: 9 passed; 2 earlier failures fixed.', 'missing'),
        # Dev 517 QA: failures counted in words, or on a continuation, still block.
        (f'Tests at head {HEAD}: 9 passed. Two failures remain.', 'failed'),
        (f'Tests at head {HEAD}: 9 passed; 2 failures were fixed. Two failures remain.', 'failed'),
        (f'Tests at head {HEAD}: 9 passed. The failures remain.', 'failed'),
        (f'Tests at head {HEAD}: 9 passed. Three tests failed.', 'failed'),
        (f'Tests at head {HEAD}: 9 passed. Failing tests: 2', 'failed'),
        (f'Tests at head {HEAD}: 9 passed.\nfailing tests: 2', 'failed'),
        (f'Tests at head {HEAD}: 9 passed. Lint: 1 failure at prior head, now fixed.', 'passed'),
        (f'Tests at head {HEAD}: 9 passed. No failures remain.', 'passed'),
        (f'Tests at head {HEAD}: 9 passed.\nBase at {OLD}: 2 failed', 'passed'),
        # A test word inside a repair phrase is no test context, on the next line or the same one.
        (f'Lint at head {HEAD}: passed.\n2 failing tests were fixed.', 'missing'),
        (f'Deploy at head {HEAD}: success.\n2 failing tests were fixed.', 'missing'),
        (f'Lint at head {HEAD}: passed. 2 failing tests were fixed.', 'missing'),
        (f'Lint at head {HEAD}: passed after fixing 2 test failures.', 'missing'),
        (f'Tests at head {HEAD}: 9 passed.\n2 failing tests were fixed.', 'passed'),
    ],
)
def test_repaired_counts_give_no_test_context_and_word_counts_block(note, status):
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


def test_headed_failure_count_needs_no_test_word():
    note = f'Re-run at head {HEAD}: 2 failed.'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == 'failed'
    # A pass still needs a test word.
    assert test_receipts.at_head({'id': 't', 'note': f'Re-run at head {HEAD}: 9 passed.'}, HEAD)[0] == 'missing'
    # Another head's failure count gives this head's pass no test context:
    # only the failure at the other head is read.
    note = f'Re-run at head {HEAD}: 9 passed, 2 failed at head {OLD}.'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == 'stale'


def test_dev505_original_note_does_not_claim_a_current_failure():
    # Exact opening sentence of the refused 03:37Z note, not the rewritten receipt.
    head = 'e06bfdf7ec6bdbd1772fb16102fee40715536e92'
    note = (
        'All lifecycle fixes remain built; a hosted-test fixture failure is now repaired and pushed '
        f'in PR #1542 at {head}. Review 1 remains blocked by the GitHub App sign-in refusal '
        '(original operation 46146711-bf35-46bd-8ff2-32dd8d8ab1bb); no retry/relink/bypass.'
    )
    assert test_receipts.at_head({'id': 't', 'note': note}, head)[0] != 'failed'


def _manager_chain_and_editing_person_can_supply_no_ci_receipt(api):
    task = _row_ticket(api)
    for n, author in enumerate(['manager', 'grandmanager', 'person', 'outsider']):
        _author_supplies_receipt(api, task if n == 0 else _row_ticket(api), author)


def _author_supplies_receipt(api, task, author):
    with api.app.state.store.transaction() as c:
        for slug, parent in [('cpo', 'cmo'), ('cmo', 'boss')]:
            c.execute('UPDATE bot_config SET reports_to=? WHERE bot=?', (parent, slug))
        if not c.execute("SELECT 1 FROM bots WHERE id='boss'").fetchone():
            c.execute("INSERT INTO bots(id,slug,state) VALUES('boss','boss','active')")
            c.execute("INSERT INTO bot_config(bot,owner_id) VALUES('boss','ana')")
            BS.write(c, 'boss', {})
        if author == 'outsider':
            c.execute("UPDATE bot_config SET reports_to=NULL WHERE bot='cpo'")
        actor = {'manager': 'bot:cmo', 'grandmanager': 'bot:boss', 'person': 'human:ana', 'outsider': 'bot:cmo'}[author]
        H.task_comment(c, actor, task['id'], f'Tests: head {HEAD}: 9 passed, 0 failed', wake=False)
    response = move(api, task)
    assert response.status_code == (422 if author == 'outsider' else 200), (author, response.text)


def _written_failure_is_not_cleared_by_a_managers_same_head_rerun(api):
    task = _row_ticket(api, f'Tests: head {HEAD}: 2 failed')
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cmo' WHERE bot='cpo'")
        H.task_comment(c, 'bot:cmo', task['id'], f'Tests: head {HEAD}: 9 passed, 0 failed', wake=False)
    response = move(api, task)
    assert response.status_code == 422 and 'failed test receipt' in response.text


def _human_without_task_edit_rights_is_not_a_receipt_author(api):
    task = _row_ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email,teams) VALUES('guest','Guest','guest@example.test','[]')")
        conv = H.conversation(c, task['conversation_id'])
        H._write_message(
            c,
            'human:guest',
            task['owner_id'],
            f'Tests: head {HEAD}: 9 passed',
            conv,
            'say',
            {'task': task['id'], 'quiet': True},
            None,
            None,
        )
    assert move(api, task).status_code == 422


def _nonparty_person_with_task_edit_rights_counts(api):
    task = _row_ticket(api)
    # The owner row is replaced last, so the first two persons count by their team and their delegation alone.
    for n, permission in enumerate(['mover', 'delegate', 'owner']):
        _nonparty_counts(api, task if n == 0 else _row_ticket(api), permission)


def _nonparty_counts(api, task, permission):
    who = 'editor-' + permission
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO humans(id,name,email,teams) VALUES(?,'Editor',?,?)",
            (who, who + '@example.test', json.dumps(['leadership'] if permission == 'mover' else [])),
        )
        if permission == 'owner':
            c.execute(
                "INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)",
                (json.dumps({'email': who + '@example.test'}),),
            )
        elif permission == 'delegate':
            c.execute(
                'INSERT INTO task_delegations(task_id,delegate,requested_by,expires_at) VALUES(?,?,?,?)',
                (task['id'], 'human:' + who, task['requester_id'], '2099-01-01T00:00:00Z'),
            )
        H.task_comment(c, 'human:' + who, task['id'], f'Tests: head {HEAD}: 9 passed', wake=False)
    response = move(api, task)
    assert response.status_code == 200, (permission, response.text)


@pytest.mark.parametrize(
    'tail',
    [
        f'2 failed at head {HEAD}',
        f'head {HEAD}: 2 failed',
        '2 earlier failed',
        '12 previous test failures',
        '2 failed.',
        '2 failed. 1 error',
    ],
)
def test_failure_counts_keep_test_context_across_clauses_and_sentences(tail):
    separator = '. ' if tail.startswith('2 failed.') else '; '
    text = f'Tests at head {HEAD}: 9 passed{separator}{tail}'
    assert test_receipts.recognise(text)['status'] == 'failed'
    assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] == 'failed'


def test_head_bound_failure_inherits_test_context_without_poisoning_other_head():
    text = f'Tests at head {HEAD}: 9 passed; 2 failed at head {OLD}'
    assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] == 'passed'
    assert test_receipts.at_head({'id': 't', 'note': text}, OLD)[0] == 'failed'


def _null_email_receipt_author_fails_closed_without_crashing(api):
    task = _row_ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email,teams) VALUES('guest','Guest',NULL,'[]')")
        c.execute(
            "INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)", (json.dumps({'email': 'owner@acme.example'}),)
        )
        conv = H.conversation(c, task['conversation_id'])
        H._write_message(
            c,
            'human:guest',
            task['owner_id'],
            f'Tests: head {HEAD}: 9 passed',
            conv,
            'say',
            {'task': task['id'], 'quiet': True},
            None,
            None,
        )
    response = move(api, task)
    assert response.status_code == 422 and 'missing test receipt' in response.text, response.text


def _environment_owner_receipt_counts_without_stored_owner(api, handoff):
    task = _row_ticket(api)
    api.app.state.store.settings.owner_email = 'editor@acme.example'
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='owner'")
        c.execute("INSERT INTO humans(id,name,email,teams) VALUES('editor','Editor','editor@acme.example','[]')")
        H.task_comment(c, 'human:editor', task['id'], f'Tests: head {HEAD}: 9 passed', wake=False)
    response = (
        move(api, task)
        if handoff == 'move'
        else api.post(
            '/api/v2/tasks',
            headers=headers(),
            json={
                'owner': 'cmo',
                'title': 'Review 1: PR #412',
                'body': 'Check current code.',
                'relations': [{'task': task['id'], 'kind': 'parent'}],
            },
        )
    )
    assert response.status_code == 200, response.text


def _removed_manager_receipt_does_not_count(api):
    task = _row_ticket(api)
    for n, removal in enumerate(['deleted_at', 'hidden_at', 'superseded_at']):
        _removed_receipt(api, task if n == 0 else _row_ticket(api), removal)


def _removed_receipt(api, task, removal):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cmo' WHERE bot='cpo'")
        conv = H.conversation(c, task['conversation_id'])
        msg = H._write_message(
            c,
            'bot:cmo',
            task['owner_id'],
            f'Tests: head {HEAD}: 9 passed',
            conv,
            'say',
            {'task': task['id'], 'quiet': True},
            None,
            None,
        )
        c.execute(f'UPDATE messages SET {removal}=? WHERE id=?', (H.now(), msg['id']))
        facts = review_gate.evidence(c, H.task(c, task['id']), 'human:ana')
        assert test_receipts.at_head(facts, HEAD)[0] == 'missing'
        c.execute(f'UPDATE messages SET {removal}=NULL WHERE id=?', (msg['id'],))
        facts = review_gate.evidence(c, H.task(c, task['id']), 'human:ana')
        assert test_receipts.at_head(facts, HEAD)[0] == 'passed'


def _manager_receipt_in_private_child_still_requires_mover_visibility(api):
    task = _row_ticket(api, private=False)
    with api.app.state.store.transaction() as c:
        # Ana heads every bot that reports to no one, so cmo reports to another root person she does not manage.
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster['people'].append({'id': 'zed', 'email': 'zed@acme.example'})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
        c.execute("UPDATE bot_config SET reports_to='human:zed' WHERE bot='cmo'")
        c.execute("UPDATE bot_config SET reports_to='cmo' WHERE bot='cpo'")
        child = H.task_create(c, 'human:ana', 'Evidence', 'x', 'cmo', parent_id=task['id'], next_run=True)
        c.execute(
            "UPDATE tasks SET title='Review 1: previous', private=1, requester_id=? WHERE id=?",
            (task['owner_id'], child['id']),
        )
        conv = H.conversation(c, child['conversation_id'])
        H._write_message(
            c,
            'bot:cmo',
            child['owner_id'],
            f'Tests: head {HEAD}: 9 passed',
            conv,
            'say',
            {'task': child['id'], 'quiet': True},
            None,
            None,
        )
        assert H.task_private_readable(c, task['owner_id'], H.task(c, child['id']))
        assert not H.task_private_readable(c, 'human:ana', H.task(c, child['id']))
    response = move(api, task)
    assert response.status_code == 422 and 'missing test receipt' in response.text, response.text
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET private=0 WHERE id=?', (child['id'],))
    assert move(api, task).status_code == 200


def _stored_owner_revokes_environment_owners_receipt_authority(api):
    task = _row_ticket(api)
    api.app.state.store.settings.owner_email = 'former@acme.example'
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email,teams) VALUES('former','Former','former@acme.example','[]')")
        c.execute(
            "INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)",
            (json.dumps({'email': 'current@acme.example'}),),
        )
        conv = H.conversation(c, task['conversation_id'])
        H._write_message(
            c,
            'human:former',
            task['owner_id'],
            f'Tests: head {HEAD}: 9 passed',
            conv,
            'say',
            {'task': task['id'], 'quiet': True},
            None,
            None,
        )
    response = move(api, task)
    assert response.status_code == 422 and 'missing test receipt' in response.text, response.text


def _authority_baseline(api):
    """What the receipt-author checks change: the owner address in settings and the registry, the people roster, who
    each bot reports to, and the humans and bots on file."""
    with api.app.state.store.read() as c:
        return {
            'owner_email': api.app.state.store.settings.owner_email,
            'meta': {
                k: v for k, v in c.execute("SELECT key, value FROM registry_metadata WHERE key IN ('owner','people')")
            },
            'reports_to': list(c.execute('SELECT bot, reports_to FROM bot_config')),
            'humans': {r[0] for r in c.execute('SELECT id FROM humans')},
            'bots': {r[0] for r in c.execute('SELECT id FROM bots')},
        }


def _restore_authority(api, baseline):
    """Put back what a fresh hub had, so each check judges its receipt author as it did alone."""
    api.app.state.store.settings.owner_email = baseline['owner_email']
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key IN ('owner','people')")
        for key, value in baseline['meta'].items():
            c.execute('INSERT INTO registry_metadata VALUES(?,?)', (key, value))
        c.execute('UPDATE bot_config SET reports_to=NULL')
        for bot, reports_to in baseline['reports_to']:
            c.execute('UPDATE bot_config SET reports_to=? WHERE bot=?', (reports_to, bot))
        for (who,) in list(c.execute('SELECT id FROM humans')):
            if who not in baseline['humans']:
                c.execute('DELETE FROM humans WHERE id=?', (who,))
        for (bot,) in list(c.execute('SELECT id FROM bots')):
            if bot not in baseline['bots']:  # the chain check's `boss`: a root bot Ana would otherwise head
                for table, column in (('bot_settings', 'bot_id'), ('bot_config', 'bot'), ('bots', 'id')):
                    c.execute(f'DELETE FROM {table} WHERE {column}=?', (bot,))


def test_who_may_write_a_receipt_without_ci(api, subtests):
    """Every receipt-author row in one hub: each check files its own ticket, and the owner, roster, reporting lines and
    extra humans a check set are put back before the next one."""
    baseline = _authority_baseline(api)
    for check, args in (
        (_manager_chain_and_editing_person_can_supply_no_ci_receipt, ()),
        (_written_failure_is_not_cleared_by_a_managers_same_head_rerun, ()),
        (_human_without_task_edit_rights_is_not_a_receipt_author, ()),
        (_nonparty_person_with_task_edit_rights_counts, ()),
        (_null_email_receipt_author_fails_closed_without_crashing, ()),
        (_environment_owner_receipt_counts_without_stored_owner, ('move',)),
        (_environment_owner_receipt_counts_without_stored_owner, ('child',)),
        (_removed_manager_receipt_does_not_count, ()),
        (_manager_receipt_in_private_child_still_requires_mover_visibility, ()),
        (_stored_owner_revokes_environment_owners_receipt_authority, ()),
    ):
        with subtests.test(check.__name__, args=args):
            try:
                check(api, *args)
            finally:
                _restore_authority(api, baseline)
