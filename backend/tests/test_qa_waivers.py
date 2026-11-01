"""A person waives a Dev ticket's In QA step for one exact head (backend/qa_waivers.py): recorded as a waiver with its
actor, reason, head, step and time, never as a QA verdict; CI and both reviews on that head stay required."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from backend import hubdb as H, qa_waivers, review_gate
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import encode
from backend.tests.test_github import SECRET, PR, hook, pr_event

HEAD, NEW = 'a' * 40, 'b' * 40
STEPS = [{'name': n, 'status': s} for n, s in (('In Progress', 'doing'), ('Review 1', 'review'), ('Review 1 Fixes', 'doing'),
         ('Review 2', 'review'), ('Review 2 Fixes', 'doing'), ('In QA', 'review'), ('QA Fixes', 'doing'),
         ('Pending Release', 'ready'), ('Done', 'done'))]


class GitHub:
    """What a fresh GitHub read of the pull request returns (review_gate.snapshot), as the route takes it: the head
    and every check attempt, skips marked as skipped. `connected=False` is a company without the GitHub App."""
    def __init__(self):
        self.head, self.checks, self.connected, self.reads = HEAD, {}, True, 0

    def snapshot(self, service, prs):
        self.reads += 1
        if not self.connected:
            return None
        return {pr['id']: {'head_sha': self.head, 'checks': dict(self.checks), 'at': H.now()} for pr in prs}


@pytest.fixture
def api(tmp_path, monkeypatch):
    github = GitHub()
    monkeypatch.setattr(review_gate, 'snapshot', lambda service, prs: github.snapshot(service, prs))
    app = create_app(Settings(db_path=tmp_path / 'hub.db', github_webhook_secret=SECRET, release_commit='',
                              release_repo='ticoteam/tico', test_identities={
        'ana-test': Identity('human:ana', 'owner', 'ana@acme.example'),
        'ben-test': Identity('human:ben', 'human', 'ben@acme.example'),
        'cara-test': Identity('human:cara', 'human', 'cara@acme.example'),
        'bot-test': Identity('bot:cpo', 'bot', agent='hermes'),
        # What a bot's delegation (BotOps, or a confirmed Assistant card) makes of a call for Ana: her actor, a bot's
        # call. The lending bot signs in with an agent credential, which the fixture registers for cpo.
        'botops-test': Identity('human:ana', 'owner', 'ana@acme.example', via='botops', confirmed=True,
                                task_actor='bot:cpo', agent='hermes'),
        'assistant-test': Identity('human:ana', 'owner', 'ana@acme.example', via='assistant', confirmed=True),
        'token-test': Identity('human:ana', 'owner', 'ana@acme.example', via_token=True, token_label='laptop')}))
    with TestClient(app) as client:
        client.github = github
        with app.state.store.transaction() as c:
            bots = {slug: {'name': slug, 'runtime': 'fake', 'status': 'active'} for slug in ('cpo', 'cmo')}
            people = [{'id': 'ana', 'email': 'ana@acme.example', 'team': 'leadership', 'primary_for': ['*']},
                      {'id': 'ben', 'email': 'ben@acme.example', 'team': 'support'},
                      {'id': 'cara', 'email': 'cara@acme.example', 'team': 'support'}]
            H.sync_registry(c, bots, {'people': people})
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({'people': people}),))
            for slug, config in bots.items():
                c.execute('INSERT INTO bot_config(bot,config,team,operator) VALUES(?,?,?,?)',
                          (slug, encode(config), 'product', 'ana'))
            c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({'completed': '2026-01-01T00:00:00Z'}),))
            H.type_create(c, H.KEEPER, 'Dev tickets', STEPS)
            # The bot signs in as an agent does (bot-test), so a refusal is the waiver's and not the credential's.
            c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('cpo','hermes','x',?,'human:ana')",
                      (H.now(),))
        yield client


def call(api, method, path, token='ana-test', body=None):
    return api.request(method, '/api/v2/' + path, json=body,
                       headers={'Authorization': 'Bearer ' + token, 'Idempotency-Key': str(uuid.uuid4())})


def child(api, ticket, title, status='done', verdict=None, head=None):
    """A review or QA subtask with a recorded verdict, written directly: the review gate is not under test here."""
    r = call(api, 'POST', 'tasks', body={'owner': 'cmo', 'title': 'Check it', 'body': 'Check the change.',
                                        'relations': [{'task': ticket['id'], 'kind': 'parent'}]})
    assert r.status_code == 200, r.text
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET title=?, status=?, verdict=?, verdict_head=? WHERE id=?',
                  (title, status, verdict, head, r.json()['task']['id']))
    return r.json()['task']


def green(api, checks=None):
    """GitHub reports the checks at the current head: a pass, and a skip that is not one."""
    api.github.checks = checks or {'check:python': 'passing', 'check:browser': 'skipped'}


def ticket(api, owner='cpo', step='In QA', checks=None, reviews=(('approve', HEAD), ('approve', HEAD)),
           title='Fix the checkout total'):
    with api.app.state.store.read() as c:
        typ = H.type_get(c, 'Dev tickets')
    r = call(api, 'POST', 'tasks', body={'owner': owner, 'title': title, 'body': 'Fix it.',
                                        'type': typ['id'], 'step': step, 'links': [PR]})
    assert r.status_code == 200, r.text
    row = r.json()['task']
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    green(api, checks)
    for name, (verdict, head) in zip(('Review 1: PR #412', 'Review 2: PR #412'), reviews):
        if verdict:
            child(api, row, name, verdict=verdict, head=head)
    child(api, row, 'QA: PR #412', status='doing')
    return row


def waive(api, row, token='ana-test', head=HEAD, reason='Urgent fix, QA bot down', **more):
    return call(api, 'POST', f"tasks/{row['id']}/qa-waiver", token, {'head': head, 'reason': reason, **more})


def shown(api, row):
    return call(api, 'GET', 'tasks/' + row['id']).json()


@pytest.mark.parametrize('owner,token,actor', [('human:ben', 'ben-test', 'human:ben'),   # the ticket's owner
                                               ('cpo', 'ana-test', 'human:ana'),           # a manager
                                               ('human:ben', 'ana-test', 'human:ana')])    # a manager, a person's ticket
def test_owner_or_manager_waives_the_current_qa_step_on_the_exact_head(api, owner, token, actor):
    row = ticket(api, owner=owner)
    r = waive(api, row, token)
    assert r.status_code == 200, r.text
    waiver = r.json()['waiver']
    assert {k: waiver[k] for k in ('actor', 'reason', 'head', 'step', 'pr', 'current')} == {
        'actor': actor, 'reason': 'Urgent fix, QA bot down', 'head': HEAD, 'step': 'In QA', 'pr': PR, 'current': True}
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
        told = c.execute("SELECT to_actor, body FROM messages WHERE json_extract(refs,'$.qa_waiver')=?", (HEAD,)).fetchall()
    # The owner hears once, unless the owner waived it.
    assert [(m[0], 'not a QA pass' in m[1]) for m in told] == ([] if owner == actor else [(detail['task']['owner'], True)])


@pytest.mark.parametrize('owner,token,why', [('human:ben', 'cara-test', 'owner or a manager'),
                                             ('cpo', 'ben-test', 'owner or a manager'),
                                             ('cpo', 'bot-test', 'Only a person, signed in as themselves')])
def test_a_non_owner_non_manager_or_a_bot_is_refused(api, owner, token, why):
    row = ticket(api, owner=owner)
    r = waive(api, row, token)
    assert r.status_code == 403 and why in r.text, r.text
    assert shown(api, row)['task']['qa_waivers'] == []


@pytest.mark.parametrize('step,asked,why', [('Review 2', None, 'not In QA'), ('Pending Release', None, 'not In QA'),
                                            ('In QA', 'Review 1', 'Review 1 is mandatory'),
                                            ('In QA', 'Review 2', 'Review 2 is mandatory'),
                                            ('In QA', 'CI', 'CI is mandatory'),
                                            ('In QA', 'Release approval', 'Release approval is mandatory'),
                                            ('In QA', 'Post Release QA', 'Only the In QA step')])
def test_only_the_current_in_qa_step_is_waivable(api, step, asked, why):
    row = ticket(api, step=step)
    r = waive(api, row, step=asked) if asked else waive(api, row)
    assert r.status_code == 422 and why in r.text, r.text
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


@pytest.mark.parametrize('checks,why', [({'check:python': 'failing', 'check:browser': 'passing'}, 'CI failing on'),
                                        ({'check:python': 'passing', 'check:browser': 'pending'}, 'CI still running'),
                                        ({'check:python': 'skipped'}, 'no passing hosted check'),
                                        # A re-run passed, but the read keeps the failed attempt (filter=all).
                                        ({'check:python (1)': 'failing', 'check:python (2)': 'passing'}, 'CI failing on')])
def test_checks_not_green_are_refused_and_named(api, checks, why):
    row = ticket(api, checks=checks)
    r = waive(api, row)
    assert r.status_code == 422 and why in r.text and 'Hosted checks stay mandatory' in r.text, r.text


@pytest.mark.parametrize('reviews,why', [((('approve', HEAD), (None, None)), 'Review 2 has no recorded verdict'),
                                         (((None, None), ('approve', HEAD)), 'Review 1 has no recorded verdict'),
                                         ((('approve', HEAD), ('changes', HEAD)), 'Review 2 asked for changes'),
                                         ((('approve', HEAD), ('approve', NEW)), 'Review 2 approved bbbbbbbbbbbb'),
                                         ((('changes', HEAD), ('approve', HEAD)), 'Review 1 asked for changes'),
                                         # Review 2's approval of the head never stands in for Review 1's.
                                         ((('changes', NEW), ('approve', HEAD)), 'Review 1 asked for changes'),
                                         ((('approve', NEW), ('approve', HEAD)), 'Review 1 approved bbbbbbbbbbbb')])
def test_required_reviews_not_approving_the_head_are_refused(api, reviews, why):
    row = ticket(api, reviews=reviews)
    r = waive(api, row)
    assert r.status_code == 422 and why in r.text and 'Code reviews stay mandatory' in r.text, r.text


def test_review_1_re_asked_and_approving_the_fixed_head_allows_it(api):
    """Review 1 asked for changes on an earlier head; its re-ask approved the fixed head, and so did Review 2."""
    row = ticket(api, reviews=(('changes', NEW), ('approve', HEAD)))
    child(api, row, 'Review 1: PR #412 delta', verdict='approve', head=HEAD)
    assert waive(api, row).status_code == 200


def test_an_open_review_is_refused_and_a_second_waiver_for_the_head_too(api):
    row = ticket(api)
    child(api, row, 'Review 2: PR #412 delta', status='doing')
    r = waive(api, row)
    assert r.status_code == 422 and 'Review 2 is still open' in r.text, r.text
    row = ticket(api, title='Fix the basket total')
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM task_links WHERE task_id<>?", (row['id'],))
    assert waive(api, row).status_code == 200
    r = waive(api, row, reason='Again')
    assert r.status_code == 422 and 'already waived' in r.text, r.text


def test_a_fresh_github_read_is_what_counts(api):
    """With the GitHub App configured, the route's fresh read stands in for the webhook-built link."""
    row = ticket(api)
    with api.app.state.store.transaction() as c:
        link = H.task_links(c, row['id'])[0]
        fresh = {link['id']: {'head_sha': HEAD, 'checks': {'check:python': 'pending'}, 'at': H.now()}}
        with pytest.raises(H.Refused, match='CI still running'):
            qa_waivers.waive(c, 'human:ana', row['id'], HEAD, 'Urgent', snapshots=fresh, manager=True)
        moved = {link['id']: {'head_sha': NEW, 'checks': {'check:python': 'passing'}, 'at': H.now()}}
        with pytest.raises(H.Refused, match='not the current head'):
            qa_waivers.waive(c, 'human:ana', row['id'], HEAD, 'Urgent', snapshots=moved, manager=True)
        failed = {link['id']: {'error': True, 'why': 'GitHub read failed: ConnectError'}}
        with pytest.raises(H.Refused, match='Cannot read the current head'):
            qa_waivers.waive(c, 'human:ana', row['id'], HEAD, 'Urgent', snapshots=failed, manager=True)


@pytest.mark.parametrize('runs', [[('Unit tests', 'skipped')],                          # a skip-only head
                                  [('Unit tests', 'failure'), ('Unit tests', 'success')]])  # a failure hidden by a re-run
def test_without_the_github_app_check_webhooks_alone_never_allow_a_waiver(api, runs):
    """The webhook-built link reads a skip as passing and lets a re-run's pass replace a failure, so with no fresh
    GitHub read to tell them apart, a waiver is refused rather than trusting it."""
    api.github.connected = False
    row = ticket(api)
    for name, conclusion in runs:
        hook(api, 'check_run', {'repository': {'full_name': 'ticoteam/tico'}, 'check_run': {
            'name': name, 'status': 'completed', 'conclusion': conclusion, 'head_sha': HEAD,
            'pull_requests': [{'number': 412}]}})
    assert shown(api, row)['task']['links'][0]['checks'] == 'passing'   # what the cache would have trusted
    r = waive(api, row)
    assert r.status_code == 422 and 'GitHub App is not connected' in r.text, r.text
    assert shown(api, row)['task']['qa_waivers'] == []


def test_every_open_pull_request_on_the_ticket_needs_green_checks(api):
    row = ticket(api)
    second = PR.rsplit('/', 1)[0] + '/413'
    assert call(api, 'POST', f"tasks/{row['id']}/links", body={'url': second}).status_code == 200
    with api.app.state.store.read() as c:
        other = next(l for l in H.task_links(c, row['id']) if l['url'] == second)
    real = api.github.snapshot
    api.github.snapshot = lambda service, prs: {**real(service, prs), other['id']: {
        'head_sha': NEW, 'checks': {'check:python': 'failing'}, 'at': H.now()}}
    r = waive(api, row)
    assert r.status_code == 422 and 'CI failing on ' + second in r.text, r.text


def test_a_qa_failure_at_the_head_is_not_waivable(api):
    """A waiver stands in for a QA result that is missing, not for one that failed."""
    row = ticket(api)
    failed = child(api, row, 'QA: PR #412 retry', verdict='fail', head=HEAD[:12])
    r = waive(api, row)
    assert r.status_code == 422 and 'QA recorded fail at aaaaaaaaaaaa' in r.text and failed['id'] in r.text, r.text
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET verdict_head=? WHERE id=?', (None, failed['id']))  # a fail naming no head
    r = waive(api, row)
    assert r.status_code == 422 and 'QA recorded fail at no named head' in r.text, r.text
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET verdict_head=? WHERE id=?', (NEW, failed['id']))   # it failed an earlier head
    assert waive(api, row).status_code == 200


@pytest.mark.parametrize('pushed', ['the waived PR', 'another open PR'])
def test_a_push_recorded_while_github_is_read_refuses_the_stale_green_read(api, pushed):
    """The route reads GitHub outside the write lock. A push whose webhook lands before the waiver is written makes
    that read stale: refused with nothing recorded and nobody woken, as for a review move."""
    row = ticket(api)
    second = PR.rsplit('/', 1)[0] + '/413'
    assert call(api, 'POST', f"tasks/{row['id']}/links", body={'url': second}).status_code == 200
    hook(api, 'pull_request', pr_event('opened', html_url=second, number=413, head={'sha': HEAD}))
    url = PR if pushed == 'the waived PR' else second
    real = api.github.snapshot
    def read_then_push(service, prs):
        green = real(service, prs)
        hook(api, 'pull_request', pr_event('synchronize', html_url=url, number=int(url.rsplit('/', 1)[1]),
                                           head={'sha': NEW}))
        return green
    api.github.snapshot = read_then_push
    r = waive(api, row)
    assert r.status_code == 422 and url + ' changed while GitHub was read' in r.text, r.text
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='qa_waiver'", (row['id'],)).fetchall()
        assert not c.execute("SELECT 1 FROM messages WHERE body LIKE '%waived QA%'").fetchall()


@pytest.mark.parametrize('token', ['botops-test', 'assistant-test'])
def test_a_bot_acting_for_the_person_is_refused_and_records_nothing(api, token):
    """BotOps or the Assistant calling for a manager carries her actor and passes every other gate; the waiver is
    still a bot's call. Her own personal API token (the CLI, an MCP client she runs) still waives."""
    row = ticket(api)
    r = waive(api, row, token)
    assert r.status_code == 403 and 'Only a person, signed in as themselves' in r.text, r.text
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='qa_waiver'", (row['id'],)).fetchall()
        assert not c.execute("SELECT 1 FROM messages WHERE body LIKE '%waived QA%'").fetchall()
    assert waive(api, row, 'token-test').status_code == 200


def test_botops_in_the_owners_own_chat_cannot_waive_a_ticket_every_gate_allows(api):
    """A real BotOps run started by the company owner in her own BotOps chat, citing that turn: the middleware lends
    it her actor and role, which alone pass the owner/manager test. The ticket is at In QA with every gate green, so
    only the caller's provenance refuses it: before GitHub is read, recording no waiver and waking nobody. The same
    request from Ana herself then goes through."""
    from backend.tests.test_api import assign, claim, post, ready, runner
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        c.execute("INSERT INTO bot_config(bot,config,operator) VALUES('botops',?, 'ana')",
                  (encode({'name': 'botops', 'runtime': 'fake', 'status': 'active'}),))
    machine = runner(api)
    assign(api, machine, 'botops')
    ready(api, machine, ['botops'])
    row = ticket(api, owner='cpo')
    post(api, 'chat/botops', {'text': 'Waive QA on the checkout fix, the QA bot is down'}, token='ana-test')
    attempt = claim(api, machine, 'botops')
    reads = api.github.reads
    r = api.post(f"/api/v2/tasks/{row['id']}/qa-waiver", json={'head': HEAD, 'reason': 'Urgent fix, QA bot down'},
                 headers={'Authorization': 'Bearer ' + attempt['token'], 'Idempotency-Key': str(uuid.uuid4()),
                          'X-Tico-On-Behalf-Of': 'turn'})
    assert r.status_code == 403 and 'Only a person, signed in as themselves' in r.text, r.text
    assert api.github.reads == reads
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='qa_waiver'", (row['id'],)).fetchall()
        assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs,'$.qa_waiver') IS NOT NULL").fetchall()
    assert shown(api, row)['task']['qa_waivers'] == []
    direct = waive(api, row, 'ana-test')
    assert direct.status_code == 200 and direct.json()['waiver']['actor'] == 'human:ana', direct.text
