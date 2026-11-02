"""Review entry uses current CI evidence without losing real same-head failures."""
import json
from types import SimpleNamespace

import pytest

from backend import hubdb as H, test_receipts, review_gate
from backend.tests.test_github import api, headers, post, PR
from backend.tests.test_review_gate import HEAD, OLD, ticket, move


def github(api, monkeypatch, checks=(), statuses=()):
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *a: ('synthetic', 'later'))
    calls = []
    def fetch(method, path, **kwargs):
        calls.append((path, kwargs.get('params')))
        data = ({'head': {'sha': HEAD}} if '/pulls/' in path else
                {'check_runs': list(checks)} if path.endswith('check-runs') else list(statuses))
        return SimpleNamespace(status_code=200, json=lambda: data)
    monkeypatch.setattr(service, '_call', fetch)
    return calls


@pytest.mark.parametrize('step', ['Review 1', 'Review 2'])
@pytest.mark.parametrize('receipt', [None, f'Tests: head {HEAD}: 2 failed',
                                  f'Tests: head {OLD}: 9 passed'])
def test_fresh_all_green_opens_review_without_text_and_overrides_text_failure(api, monkeypatch, step, receipt):
    task = ticket(api, receipt)
    github(api, monkeypatch, [{'id': 1, 'name': 'python', 'conclusion': 'success'},
                              {'id': 2, 'name': 'browser', 'conclusion': 'success'}])
    response = move(api, task, step)
    assert response.status_code == 200, response.text
    child = api.post('/api/v2/tasks', headers=headers(), json={'owner': 'cmo', 'title': step + ': PR #412',
        'body': 'Check current code.', 'relations': [{'task': task['id'], 'kind': 'parent'}]})
    assert child.status_code == 200, child.text


@pytest.mark.parametrize('checks,statuses', [([], []),
    ([{'id': 1, 'name': 'python', 'conclusion': None}], []),
    ([{'id': 1, 'name': 'python', 'conclusion': 'skipped'}], []),
    ([{'id': 1, 'name': 'python', 'conclusion': 'neutral'}], [])])
def test_absent_pending_or_only_skipped_checks_still_need_passing_receipt(api, monkeypatch, checks, statuses):
    task = ticket(api)
    github(api, monkeypatch, checks, statuses)
    response = move(api, task)
    assert response.status_code == 422 and 'passing test receipt' in response.text, response.text
    assert 'manager' in response.text and 'person' in response.text
    with api.app.state.store.transaction() as c:
        H.task_comment(c, task['owner'], task['id'], f'Tests: head {HEAD}: 9 passed, 0 failed', wake=False)
    assert move(api, task).status_code == 200


def test_cached_webhook_green_does_not_supply_the_receipt(api):
    # Without a fresh GitHub read the gate has only the webhook-built cache, where a skipped check reads as passing.
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.checks',json(?)) WHERE task_id=? AND kind='pr'",
                  (json.dumps({'python': 'passing'}), task['id']))
    response = move(api, task)
    assert response.status_code == 422 and 'passing test receipt' in response.text, response.text


@pytest.mark.parametrize('source', ['checks', 'statuses'])
def test_github_failed_attempt_still_blocks_after_green_rerun(api, monkeypatch, source):
    task = ticket(api, f'Tests: head {HEAD}: 9 passed')
    calls = github(api, monkeypatch,
        checks=[{'id': 2, 'name': 'python', 'conclusion': 'success'},
                {'id': 1, 'name': 'python', 'conclusion': 'failure'}] if source == 'checks' else [],
        statuses=[{'context': 'python', 'state': 'success'},
                  {'context': 'python', 'state': 'failure'}] if source == 'statuses' else [])
    response = move(api, task)
    assert response.status_code == 422, response.text
    assert 'same head' in response.text and 'rerun' in response.text and 'new head' in response.text
    assert all(params.get('filter') == 'all' for path, params in calls if path.endswith('check-runs'))


@pytest.mark.parametrize('pending', [False, True])
def test_success_with_skipped_companion_counts_but_pending_does_not(api, monkeypatch, pending):
    task = ticket(api)
    github(api, monkeypatch, [{'id': 1, 'name': 'python', 'conclusion': 'success'},
                             {'id': 2, 'name': 'optional', 'conclusion': None if pending else 'skipped'}])
    response = move(api, task)
    assert response.status_code == (422 if pending else 200), response.text


def test_successful_commit_status_can_supply_the_receipt(api, monkeypatch):
    task = ticket(api)
    github(api, monkeypatch, statuses=[{'context': 'tests', 'state': 'success'}])
    assert move(api, task).status_code == 200


def test_one_green_pr_does_not_supply_another_prs_receipt(api, monkeypatch):
    task = ticket(api)
    post(api, 'tasks/' + task['id'] + '/links', {'url': PR.replace('/412', '/413')})
    github(api, monkeypatch, [{'id': 1, 'name': 'python', 'conclusion': 'success'}])
    service = api.app.state.github_app
    fetch = service._call
    monkeypatch.setattr(service, '_call', lambda method, path, **kw:
        SimpleNamespace(status_code=200, json=lambda: {'head': {'sha': OLD}}) if '/pulls/413' in path else
        SimpleNamespace(status_code=200, json=lambda: {'check_runs': []}) if OLD in path and path.endswith('check-runs') else
        fetch(method, path, **kw))
    response = move(api, task)
    assert response.status_code == 422 and OLD in response.text, response.text


@pytest.mark.parametrize('text,status', [
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
])
def test_historical_failure_phrases_are_not_current_run_results(text, status):
    receipt = test_receipts.recognise(text)
    assert receipt['status'] == status
    if receipt['head'] == OLD:
        assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] != 'failed'


def test_two_heads_in_one_line_keep_their_own_outcomes():
    text = f'Tests at head {HEAD}: 9 passed; tests at head {OLD}: 2 failed'
    assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] == 'passed'
    assert test_receipts.at_head({'id': 't', 'note': text}, OLD)[0] == 'failed'


@pytest.mark.parametrize('result,status', [
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
])
def test_qa_repair_phrases_preserve_unresolved_and_recurring_failures(result, status):
    note = f'Tests at head {HEAD}: {result}'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


@pytest.mark.parametrize('result,status', [
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
])
def test_qa_rerun_counts_and_recurrence_stay_with_their_head(result, status):
    note = f'Tests at head {HEAD}: {result}'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


@pytest.mark.parametrize('result,status', [
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
])
def test_repaired_prior_failures_and_headed_rerun_failures_after_1546(result, status):
    note = f'Tests at head {HEAD}: {result}'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


@pytest.mark.parametrize('note,status', [
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
])
def test_repaired_counts_give_no_test_context_and_word_counts_block(note, status):
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == status


@pytest.mark.parametrize('first', ['Lint', 'Deploy'])
def test_no_ci_review_refuses_lint_pass_followed_by_repaired_test_line(api, first):
    task = ticket(api, f'{first} at head {HEAD}: passed.\n2 failing tests were fixed.')
    response = move(api, task)
    assert response.status_code == 422 and 'passing test receipt' in response.text, response.text


def test_headed_failure_count_needs_no_test_word():
    note = f'Re-run at head {HEAD}: 2 failed.'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == 'failed'
    # A pass still needs a test word.
    assert test_receipts.at_head({'id': 't', 'note': f'Re-run at head {HEAD}: 9 passed.'}, HEAD)[0] == 'missing'
    # Another head's failure count gives this head's pass no test context:
    # only the failure at the other head is read.
    note = f'Re-run at head {HEAD}: 9 passed, 2 failed at head {OLD}.'
    assert test_receipts.at_head({'id': 't', 'note': note}, HEAD)[0] == 'stale'


@pytest.mark.parametrize('tail', ['Re-run: 2 failed', 'Rerun: 2 failing', 'Latest run: 2 failed, 9 passed', 'Re-run failed: 2'])
def test_no_ci_review_refuses_failed_rerun_after_repaired_count(api, tail):
    task = ticket(api, f'Tests at head {HEAD}: 9 passed; 2 failures were fixed. {tail}')
    response = move(api, task)
    assert response.status_code == 422 and 'failed test receipt' in response.text, response.text


def test_dev505_original_note_does_not_claim_a_current_failure():
    # Exact opening sentence of the refused 03:37Z note, not the rewritten receipt.
    head = 'e06bfdf7ec6bdbd1772fb16102fee40715536e92'
    note = ('All lifecycle fixes remain built; a hosted-test fixture failure is now repaired and pushed '
            f'in PR #1542 at {head}. Review 1 remains blocked by the GitHub App sign-in refusal '
            '(original operation 46146711-bf35-46bd-8ff2-32dd8d8ab1bb); no retry/relink/bypass.')
    assert test_receipts.at_head({'id': 't', 'note': note}, head)[0] != 'failed'


@pytest.mark.parametrize('author', ['manager', 'grandmanager', 'person', 'outsider'])
def test_manager_chain_and_editing_person_can_supply_no_ci_receipt(api, author):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        for slug, parent in [('cpo', 'cmo'), ('cmo', 'boss')]:
            c.execute('UPDATE bot_config SET reports_to=? WHERE bot=?', (parent, slug))
        c.execute("INSERT INTO bots(slug,state) VALUES('boss','active')")
        c.execute("INSERT INTO bot_config(bot,config,operator) VALUES('boss','{}','ana')")
        if author == 'outsider':
            c.execute("UPDATE bot_config SET reports_to=NULL WHERE bot='cpo'")
        actor = {'manager': 'bot:cmo', 'grandmanager': 'bot:boss', 'person': 'human:ana', 'outsider': 'bot:cmo'}[author]
        H.task_comment(c, actor, task['id'], f'Tests: head {HEAD}: 9 passed, 0 failed', wake=False)
    response = move(api, task)
    assert response.status_code == (422 if author == 'outsider' else 200), response.text


def test_written_failure_is_not_cleared_by_a_managers_same_head_rerun(api):
    task = ticket(api, f'Tests: head {HEAD}: 2 failed')
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cmo' WHERE bot='cpo'")
        H.task_comment(c, 'bot:cmo', task['id'], f'Tests: head {HEAD}: 9 passed, 0 failed', wake=False)
    response = move(api, task)
    assert response.status_code == 422 and 'failed test receipt' in response.text


def test_human_without_task_edit_rights_is_not_a_receipt_author(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email,teams) VALUES('guest','Guest','guest@example.test','[]')")
        conv = H.conversation(c, task['conversation_id'])
        H._write_message(c, 'human:guest', task['owner'], f'Tests: head {HEAD}: 9 passed', conv,
                         'say', {'task': task['id'], 'quiet': True}, None, None)
    assert move(api, task).status_code == 422


@pytest.mark.parametrize('permission', ['owner', 'mover', 'delegate'])
def test_nonparty_person_with_task_edit_rights_counts(api, permission):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email,teams) VALUES('editor','Editor','editor@example.test',?)",
                  (json.dumps(['leadership'] if permission == 'mover' else []),))
        if permission == 'owner':
            c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)",
                      (json.dumps({'email': 'editor@example.test'}),))
        elif permission == 'delegate':
            c.execute('INSERT INTO task_delegations(task_id,delegate,requested_by,expires_at) VALUES(?,?,?,?)',
                      (task['id'], 'human:editor', task['requester'], '2099-01-01T00:00:00Z'))
        H.task_comment(c, 'human:editor', task['id'], f'Tests: head {HEAD}: 9 passed', wake=False)
    response = move(api, task)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('tail', [
    f'2 failed at head {HEAD}',
    f'head {HEAD}: 2 failed',
    '2 earlier failed',
    '12 previous test failures',
    '2 failed.',
    '2 failed. 1 error',
])
def test_failure_counts_keep_test_context_across_clauses_and_sentences(tail):
    separator = '. ' if tail.startswith('2 failed.') else '; '
    text = f'Tests at head {HEAD}: 9 passed{separator}{tail}'
    assert test_receipts.recognise(text)['status'] == 'failed'
    assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] == 'failed'


def test_head_bound_failure_inherits_test_context_without_poisoning_other_head():
    text = f'Tests at head {HEAD}: 9 passed; 2 failed at head {OLD}'
    assert test_receipts.at_head({'id': 't', 'note': text}, HEAD)[0] == 'passed'
    assert test_receipts.at_head({'id': 't', 'note': text}, OLD)[0] == 'failed'


def test_null_email_receipt_author_fails_closed_without_crashing(api):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email,teams) VALUES('guest','Guest',NULL,'[]')")
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)",
                  (json.dumps({'email': 'owner@acme.example'}),))
        conv = H.conversation(c, task['conversation_id'])
        H._write_message(c, 'human:guest', task['owner'], f'Tests: head {HEAD}: 9 passed', conv,
                         'say', {'task': task['id'], 'quiet': True}, None, None)
    response = move(api, task)
    assert response.status_code == 422 and 'missing test receipt' in response.text, response.text


@pytest.mark.parametrize('handoff', ['move', 'child'])
def test_environment_owner_receipt_counts_without_stored_owner(api, handoff):
    task = ticket(api)
    api.app.state.store.settings.owner_email = 'editor@acme.example'
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='owner'")
        c.execute("INSERT INTO humans(id,name,email,teams) VALUES('editor','Editor','editor@acme.example','[]')")
        H.task_comment(c, 'human:editor', task['id'], f'Tests: head {HEAD}: 9 passed', wake=False)
    response = (move(api, task) if handoff == 'move' else api.post('/api/v2/tasks', headers=headers(), json={
        'owner': 'cmo', 'title': 'Review 1: PR #412', 'body': 'Check current code.',
        'relations': [{'task': task['id'], 'kind': 'parent'}]}))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('removal', ['deleted_at', 'hidden_at', 'superseded_at'])
def test_removed_manager_receipt_does_not_count(api, removal):
    task = ticket(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cmo' WHERE bot='cpo'")
        conv = H.conversation(c, task['conversation_id'])
        msg = H._write_message(c, 'bot:cmo', task['owner'], f'Tests: head {HEAD}: 9 passed', conv,
                               'say', {'task': task['id'], 'quiet': True}, None, None)
        c.execute(f'UPDATE messages SET {removal}=? WHERE id=?', (H.now(), msg['id']))
        facts = review_gate.evidence(c, H.task(c, task['id']), 'human:ana')
        assert test_receipts.at_head(facts, HEAD)[0] == 'missing'
        c.execute(f'UPDATE messages SET {removal}=NULL WHERE id=?', (msg['id'],))
        facts = review_gate.evidence(c, H.task(c, task['id']), 'human:ana')
        assert test_receipts.at_head(facts, HEAD)[0] == 'passed'


def test_manager_receipt_in_private_child_still_requires_mover_visibility(api):
    task = ticket(api, private=False)
    with api.app.state.store.transaction() as c:
        # Ana heads every bot that reports to no one, so cmo reports to another root person she does not manage.
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster['people'].append({'id': 'zed', 'email': 'zed@acme.example'})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
        c.execute("UPDATE bot_config SET reports_to='human:zed' WHERE bot='cmo'")
        c.execute("UPDATE bot_config SET reports_to='cmo' WHERE bot='cpo'")
        child = H.task_create(c, 'human:ana', 'Evidence', 'x', 'cmo', parent_id=task['id'], next_run=True)
        c.execute("UPDATE tasks SET title='Review 1: previous', private=1, requester=? WHERE id=?",
                  (task['owner'], child['id']))
        conv = H.conversation(c, child['conversation_id'])
        H._write_message(c, 'bot:cmo', child['owner'], f'Tests: head {HEAD}: 9 passed', conv,
                         'say', {'task': child['id'], 'quiet': True}, None, None)
        assert H.task_private_readable(c, task['owner'], H.task(c, child['id']))
        assert not H.task_private_readable(c, 'human:ana', H.task(c, child['id']))
    response = move(api, task)
    assert response.status_code == 422 and 'missing test receipt' in response.text, response.text
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET private=0 WHERE id=?', (child['id'],))
    assert move(api, task).status_code == 200


def test_stored_owner_revokes_environment_owners_receipt_authority(api):
    task = ticket(api)
    api.app.state.store.settings.owner_email = 'former@acme.example'
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email,teams) VALUES('former','Former','former@acme.example','[]')")
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)",
                  (json.dumps({'email': 'current@acme.example'}),))
        conv = H.conversation(c, task['conversation_id'])
        H._write_message(c, 'human:former', task['owner'], f'Tests: head {HEAD}: 9 passed', conv,
                         'say', {'task': task['id'], 'quiet': True}, None, None)
    response = move(api, task)
    assert response.status_code == 422 and 'missing test receipt' in response.text, response.text
