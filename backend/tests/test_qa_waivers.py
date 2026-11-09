"""A person waives a Dev ticket's In QA step for one exact head (backend/qa_waivers.py): recorded as a waiver with its
actor, reason, head, step and time, never as a QA verdict; CI and both reviews on that head stay required."""

import json
import uuid

import pytest

from backend import hubdb as H, qa_waivers
from backend.tests.helpers.github import PR, hook, pr_event
from backend import bot_settings as BS
from backend.tests.helpers.qa_waivers import GitHub, HEAD, NEW, api, call, child, green, waive


def ticket(
    api,
    owner='cpo',
    step='In QA',
    checks=None,
    reviews=(('approve', HEAD), ('approve', HEAD)),
    title='Fix the checkout total',
):
    with api.app.state.store.read() as c:
        typ = H.type_get(c, 'Dev tickets')
    r = call(
        api,
        'POST',
        'tasks',
        body={'owner': owner, 'title': title, 'body': 'Fix it.', 'type': typ['id'], 'step': step, 'links': [PR]},
    )
    assert r.status_code == 200, r.text
    row = r.json()['task']
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    green(api, checks)
    for name, (verdict, head) in zip(('Review 1: PR #412', 'Review 2: PR #412'), reviews):
        if verdict:
            child(api, row, name, verdict=verdict, head=head)
    child(api, row, 'QA: PR #412', status='doing')
    return row


def fresh(api, n, **more):
    """One more ticket for a table of rows in one test: titled apart, and the only task holding the pull request link."""
    row = ticket(api, title=f'Fix the checkout total {n}', **more)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM task_links WHERE task_id<>?", (row['id'],))
    return row


def shown(api, row):
    return call(api, 'GET', 'tasks/' + row['id']).json()


def test_owner_or_manager_waives_the_current_qa_step_on_the_exact_head(api):
    for n, case in enumerate(
        [
            ('human:ben', 'ben-test', 'human:ben'),  # the ticket's owner
            ('cpo', 'ana-test', 'human:ana'),  # a manager
            ('human:ben', 'ana-test', 'human:ana'),
        ]
    ):  # a manager, a person's ticket
        _owner_or_manager_waives(api, n, *case)


def _owner_or_manager_waives(api, n, owner, token, actor):
    row = fresh(api, n, owner=owner)
    with api.app.state.store.read() as c:
        seen = c.execute("SELECT coalesce(max(rowid),0) FROM messages").fetchone()[0]
    r = waive(api, row, token)
    assert r.status_code == 200, r.text
    waiver = r.json()['waiver']
    assert {k: waiver[k] for k in ('actor', 'reason', 'head', 'step', 'pr', 'current')} == {
        'actor': actor,
        'reason': 'Urgent fix, QA bot down',
        'head': HEAD,
        'step': 'In QA',
        'pr': PR,
        'current': True,
    }
    assert waiver['at']
    detail = shown(api, row)
    # Reads name the actor for people (`actor_name`, added to any record with an actor); the record is the same.
    plain = lambda w: {k: v for k, v in w.items() if k != 'actor_name'}
    assert [plain(w) for w in detail['task']['qa_waivers']] == [plain(waiver)]
    event = next(e for e in detail['events'] if e['field'] == 'qa_waiver')
    assert event['actor'] == actor and event['note'] == 'Urgent fix, QA bot down'
    assert json.loads(event['new']) == {'head': HEAD, 'step': 'In QA', 'pr': PR}
    # A waiver is not a verdict: nothing on the ticket or its QA subtask reads as a QA pass, and the ticket stays put.
    assert not any(e['field'] == 'verdict' for e in detail['events'])
    assert detail['task']['verdict'] is None and detail['task']['step']['name'] == 'In QA'
    qa = next(c for c in detail['children'] if c['title'].startswith('QA'))
    with api.app.state.store.read() as c:
        qa = H.task(c, qa['id'])
    assert qa['verdict'] is None and qa['verdict_head'] is None and qa['status'] == 'doing'
    with api.app.state.store.read() as c:
        told = c.execute(
            "SELECT recipient_id, body FROM messages WHERE json_extract(refs,'$.qa_waiver')=? AND rowid>?", (HEAD, seen)
        ).fetchall()
    # The owner hears once, unless the owner waived it.
    assert [(m[0], 'not a QA pass' in m[1]) for m in told] == (
        [] if owner == actor else [(detail['task']['owner_id'], True)]
    )


def test_a_non_owner_non_manager_or_a_bot_is_refused(api):
    for n, (owner, token, why) in enumerate(
        [
            ('human:ben', 'cara-test', 'owner or a manager'),
            ('cpo', 'ben-test', 'owner or a manager'),
            ('cpo', 'bot-test', 'Only a person, signed in as themselves'),
        ]
    ):
        row = fresh(api, n, owner=owner)
        r = waive(api, row, token)
        assert r.status_code == 403 and why in r.text, (token, r.text)
        assert shown(api, row)['task']['qa_waivers'] == []


def test_only_the_current_in_qa_step_is_waivable(api):
    for n, (step, asked, why) in enumerate(
        [
            ('Review 2', None, 'not In QA'),
            ('Pending Release', None, 'not In QA'),
            ('In QA', 'Review 1', 'Review 1 is mandatory'),
            ('In QA', 'Review 2', 'Review 2 is mandatory'),
            ('In QA', 'CI', 'CI is mandatory'),
            ('In QA', 'Release approval', 'Release approval is mandatory'),
            ('In QA', 'Post Release QA', 'Only the In QA step'),
        ]
    ):
        row = fresh(api, n, step=step)
        r = waive(api, row, step=asked) if asked else waive(api, row)
        assert r.status_code == 422 and why in r.text, (step, asked, r.text)
        assert shown(api, row)['task']['qa_waivers'] == []


def test_a_different_head_is_refused_and_a_new_push_voids_the_waiver(api):
    row = ticket(api)
    r = waive(api, row, head=NEW)
    assert r.status_code == 422 and 'not the current head' in r.text, r.text
    assert waive(api, row).status_code == 200
    hook(api, 'pull_request', pr_event('synchronize', head={'sha': NEW}))
    api.github.head, api.github.checks = NEW, {'check:python': 'pending'}
    assert [w['current'] for w in shown(api, row)['task']['qa_waivers']] == [False]
    # The push left its checks pending: nothing can be waived until they pass.
    r = waive(api, row, head=NEW)
    assert r.status_code == 422 and 'Hosted checks stay mandatory' in r.text, r.text
    green(api)
    # The old head's waiver does not stand for the new head, and the new head has no review approving it.
    r = waive(api, row, head=NEW)
    assert r.status_code == 422 and 'Review 1 approved aaaaaaaaaaaa, not bbbbbbbbbbbb' in r.text, r.text
    r = waive(api, row)
    assert r.status_code == 422 and 'not the current head' in r.text, r.text


def test_checks_not_green_are_refused_and_named(api):
    for n, (checks, why) in enumerate(
        [
            ({'check:python': 'failing', 'check:browser': 'passing'}, 'CI failing on'),
            ({'check:python': 'passing', 'check:browser': 'pending'}, 'CI still running'),
            ({'check:python': 'skipped'}, 'no passing hosted check'),
            # A re-run passed, but the read keeps the failed attempt (filter=all).
            ({'check:python (1)': 'failing', 'check:python (2)': 'passing'}, 'CI failing on'),
        ]
    ):
        row = fresh(api, n, checks=checks)
        r = waive(api, row)
        assert r.status_code == 422 and why in r.text and 'Hosted checks stay mandatory' in r.text, (checks, r.text)


def test_required_reviews_not_approving_the_head_are_refused(api):
    for n, (reviews, why) in enumerate(
        [
            ((('approve', HEAD), (None, None)), 'Review 2 has no recorded verdict'),
            (((None, None), ('approve', HEAD)), 'Review 1 has no recorded verdict'),
            ((('approve', HEAD), ('changes', HEAD)), 'Review 2 asked for changes'),
            ((('approve', HEAD), ('approve', NEW)), 'Review 2 approved bbbbbbbbbbbb'),
            ((('changes', HEAD), ('approve', HEAD)), 'Review 1 asked for changes'),
            # Review 2's approval of the head never stands in for Review 1's.
            ((('changes', NEW), ('approve', HEAD)), 'Review 1 asked for changes'),
            ((('approve', NEW), ('approve', HEAD)), 'Review 1 approved bbbbbbbbbbbb'),
        ]
    ):
        row = fresh(api, n, reviews=reviews)
        r = waive(api, row)
        assert r.status_code == 422 and why in r.text and 'Code reviews stay mandatory' in r.text, (reviews, r.text)


def test_one_ticket_waivers_follow_reviews_open_checks_every_pr_and_qa_fails(api, subtests):
    """One install, a fresh ticket per check (the only one holding the pull request link): a re-asked Review 1, an open
    review and a second waiver, a fresh GitHub read, every open pull request's checks, and a QA fail at the head."""
    for n, check in enumerate(
        (
            _review_1_re_asked_and_approving_the_fixed_head_allows_it,
            _an_open_review_is_refused_and_a_second_waiver_for_the_head_too,
            _a_fresh_github_read_is_what_counts,
            _every_open_pull_request_on_the_ticket_needs_green_checks,
            _a_qa_failure_at_the_head_is_not_waivable,
        )
    ):
        with subtests.test(check.__name__):
            check(api, 10 * n + 10)


def _review_1_re_asked_and_approving_the_fixed_head_allows_it(api, n):
    """Review 1 asked for changes on an earlier head; its re-ask approved the fixed head, and so did Review 2."""
    row = fresh(api, n, reviews=(('changes', NEW), ('approve', HEAD)))
    child(api, row, 'Review 1: PR #412 delta', verdict='approve', head=HEAD)
    assert waive(api, row).status_code == 200


def _an_open_review_is_refused_and_a_second_waiver_for_the_head_too(api, n):
    row = fresh(api, n)
    child(api, row, 'Review 2: PR #412 delta', status='doing')
    r = waive(api, row)
    assert r.status_code == 422 and 'Review 2 is still open' in r.text, r.text
    row = fresh(api, n + 1)
    assert waive(api, row).status_code == 200
    r = waive(api, row, reason='Again')
    assert r.status_code == 422 and 'already waived' in r.text, r.text


def _a_fresh_github_read_is_what_counts(api, n):
    """With the GitHub App configured, the route's fresh read stands in for the webhook-built link."""
    row = fresh(api, n)
    with api.app.state.store.transaction() as c:
        link = H.task_links(c, row['id'])[0]
        pending = {link['id']: {'head_sha': HEAD, 'checks': {'check:python': 'pending'}, 'at': H.now()}}
        with pytest.raises(H.Refused, match='CI still running'):
            qa_waivers.waive(c, 'human:ana', row['id'], HEAD, 'Urgent', snapshots=pending, manager=True)
        moved = {link['id']: {'head_sha': NEW, 'checks': {'check:python': 'passing'}, 'at': H.now()}}
        with pytest.raises(H.Refused, match='not the current head'):
            qa_waivers.waive(c, 'human:ana', row['id'], HEAD, 'Urgent', snapshots=moved, manager=True)
        failed = {link['id']: {'error': True, 'why': 'GitHub read failed: ConnectError'}}
        with pytest.raises(H.Refused, match='Cannot read the current head'):
            qa_waivers.waive(c, 'human:ana', row['id'], HEAD, 'Urgent', snapshots=failed, manager=True)


def test_without_the_github_app_check_webhooks_alone_never_allow_a_waiver(api, subtests):
    """The webhook-built link lets a re-run's pass replace a failure (and reads a neutral check as passing), so with no
    fresh GitHub read to tell them apart, a waiver is refused rather than trusting it. One install, a ticket per row."""
    for n, (runs, cached) in enumerate(
        [
            ([('Unit tests', 'skipped')], 'pending'),  # a skip-only head reads pending
            ([('Unit tests', 'failure'), ('Unit tests', 'success')], 'passing'),  # a failure hidden by a re-run
        ]
    ):
        with subtests.test(cached=cached):
            _check_webhooks_alone_never_allow_a_waiver(api, n, runs, cached)


def _check_webhooks_alone_never_allow_a_waiver(api, n, runs, cached):
    api.github.connected = False
    row = fresh(api, n)
    for name, conclusion in runs:
        hook(
            api,
            'check_run',
            {
                'repository': {'full_name': 'ticoteam/tico'},
                'check_run': {
                    'name': name,
                    'status': 'completed',
                    'conclusion': conclusion,
                    'head_sha': HEAD,
                    'pull_requests': [{'number': 412}],
                },
            },
        )
    assert shown(api, row)['task']['links'][0]['checks'] == cached  # what the cache would have trusted
    r = waive(api, row)
    assert r.status_code == 422 and 'GitHub App is not connected' in r.text, r.text
    assert shown(api, row)['task']['qa_waivers'] == []


def _every_open_pull_request_on_the_ticket_needs_green_checks(api, n):
    row = fresh(api, n)
    second = PR.rsplit('/', 1)[0] + '/413'
    assert call(api, 'POST', f"tasks/{row['id']}/links", body={'url': second}).status_code == 200
    with api.app.state.store.read() as c:
        other = next(l for l in H.task_links(c, row['id']) if l['url'] == second)
    real = api.github.snapshot
    api.github.snapshot = lambda service, prs: {
        **real(service, prs),
        other['id']: {'head_sha': NEW, 'checks': {'check:python': 'failing'}, 'at': H.now()},
    }
    try:
        r = waive(api, row)
    finally:
        api.github.snapshot = real
    assert r.status_code == 422 and 'CI failing on ' + second in r.text, r.text


def _a_qa_failure_at_the_head_is_not_waivable(api, n):
    """A waiver stands in for a QA result that is missing, not for one that failed."""
    row = fresh(api, n)
    failed = child(api, row, 'QA: PR #412 retry', verdict='fail', head=HEAD[:12])
    r = waive(api, row)
    assert r.status_code == 422 and 'QA recorded fail at aaaaaaaaaaaa' in r.text and failed['id'] in r.text, r.text
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET verdict_head=? WHERE id=?', (None, failed['id']))  # a fail naming no head
    r = waive(api, row)
    assert r.status_code == 422 and 'QA recorded fail at no named head' in r.text, r.text
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET verdict_head=? WHERE id=?', (NEW, failed['id']))  # it failed an earlier head
    assert waive(api, row).status_code == 200


def test_a_push_recorded_while_github_is_read_refuses_the_stale_green_read(api, subtests):
    """The route reads GitHub outside the write lock. A push whose webhook lands before the waiver is written makes
    that read stale: refused with nothing recorded and nobody woken, as for a review move. One install, a ticket per
    row."""
    for n, pushed in enumerate(['the waived PR', 'another open PR']):
        with subtests.test(pushed=pushed):
            _a_push_recorded_while_github_is_read(api, n, pushed)


def _a_push_recorded_while_github_is_read(api, n, pushed):
    row = fresh(api, n)
    second = PR.rsplit('/', 1)[0] + '/413'
    assert call(api, 'POST', f"tasks/{row['id']}/links", body={'url': second}).status_code == 200
    hook(api, 'pull_request', pr_event('opened', html_url=second, number=413, head={'sha': HEAD}))
    url = PR if pushed == 'the waived PR' else second
    real = api.github.snapshot

    def read_then_push(service, prs):
        green = real(service, prs)
        hook(
            api,
            'pull_request',
            pr_event('synchronize', html_url=url, number=int(url.rsplit('/', 1)[1]), head={'sha': NEW}),
        )
        return green

    api.github.snapshot = read_then_push
    try:
        r = waive(api, row)
    finally:
        api.github.snapshot = real
    assert r.status_code == 422 and url + ' changed while GitHub was read' in r.text, r.text
    with api.app.state.store.read() as c:
        assert not c.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='qa_waiver'", (row['id'],)
        ).fetchall()
        assert not c.execute("SELECT 1 FROM messages WHERE body LIKE '%waived QA%'").fetchall()


@pytest.mark.parametrize('token', ['botops-test', 'assistant-test'])
def test_a_bot_acting_for_the_person_is_refused_and_records_nothing(api, token):
    """BotOps or the Assistant calling for a manager carries her actor and passes every other gate; the waiver is
    still a bot's call. Her own personal API token (the CLI, an MCP client she runs) still waives."""
    row = ticket(api)
    r = waive(api, row, token)
    assert r.status_code == 403 and 'Only a person, signed in as themselves' in r.text, r.text
    with api.app.state.store.read() as c:
        assert not c.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='qa_waiver'", (row['id'],)
        ).fetchall()
        assert not c.execute("SELECT 1 FROM messages WHERE body LIKE '%waived QA%'").fetchall()
    assert waive(api, row, 'token-test').status_code == 200


def test_botops_in_the_owners_own_chat_cannot_waive_a_ticket_every_gate_allows(api):
    """A real BotOps run started by the company owner in her own BotOps chat, citing that turn: the middleware lends
    it her actor and role, which alone pass the owner/manager test. The ticket is at In QA with every gate green, so
    only the caller's provenance refuses it: before GitHub is read, recording no waiver and waking nobody. The same
    request from Ana herself then goes through."""
    from backend.tests.helpers.api import assign, claim, post, ready, runner

    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('botops','botops','BotOps','active')")
        c.execute("INSERT INTO bot_config(bot,owner_id) VALUES('botops','ana')")
        BS.write(c, 'botops', {'name': 'botops', 'runtime': 'fake', 'status': 'active'})
    machine = runner(api)
    assign(api, machine, 'botops')
    ready(api, machine, ['botops'])
    row = ticket(api, owner='cpo')
    post(api, 'chat/botops', {'text': 'Waive QA on the checkout fix, the QA bot is down'}, token='ana-test')
    attempt = claim(api, machine, 'botops')
    reads = api.github.reads
    r = api.post(
        f"/api/v2/tasks/{row['id']}/qa-waiver",
        json={'head': HEAD, 'reason': 'Urgent fix, QA bot down'},
        headers={
            'Authorization': 'Bearer ' + attempt['token'],
            'Idempotency-Key': str(uuid.uuid4()),
            'X-Tico-On-Behalf-Of': 'turn',
        },
    )
    assert r.status_code == 403 and 'Only a person, signed in as themselves' in r.text, r.text
    assert api.github.reads == reads
    with api.app.state.store.read() as c:
        assert not c.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='qa_waiver'", (row['id'],)
        ).fetchall()
        assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs,'$.qa_waiver') IS NOT NULL").fetchall()
    assert shown(api, row)['task']['qa_waivers'] == []
    direct = waive(api, row, 'ana-test')
    assert direct.status_code == 200 and direct.json()['waiver']['actor'] == 'human:ana', direct.text
