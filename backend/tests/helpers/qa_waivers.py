"""Helpers that other tests share, moved out of test_qa_waivers.py so they import no test file."""

import uuid
import pytest
from fastapi.testclient import TestClient
from backend import hubdb as H, review_gate
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import encode
from backend.tests.helpers.github import SECRET, PR, hook, pr_event
from backend import bot_settings as BS


HEAD, NEW = 'a' * 40, 'b' * 40


STEPS = [
    {'name': n, 'status': s}
    for n, s in (
        ('In Progress', 'doing'),
        ('Review 1', 'review'),
        ('Review 1 Fixes', 'doing'),
        ('Review 2', 'review'),
        ('Review 2 Fixes', 'doing'),
        ('In QA', 'review'),
        ('QA Fixes', 'doing'),
        ('Pending Release', 'ready'),
        ('Done', 'done'),
    )
]


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
    app = create_app(
        Settings(
            db_path=tmp_path / 'hub.db',
            github_webhook_secret=SECRET,
            release_commit='',
            release_repo='ticoteam/tico',
            test_identities={
                'ana-test': Identity('human:ana', 'owner', 'ana@acme.example'),
                'ben-test': Identity('human:ben', 'human', 'ben@acme.example'),
                'cara-test': Identity('human:cara', 'human', 'cara@acme.example'),
                'bot-test': Identity('bot:cpo', 'bot', agent='hermes'),
                # What a bot's delegation (BotOps, or a confirmed Assistant card) makes of a call for Ana: her actor, a bot's
                # call. The lending bot signs in with an agent credential, which the fixture registers for cpo.
                'botops-test': Identity(
                    'human:ana',
                    'owner',
                    'ana@acme.example',
                    via='botops',
                    confirmed=True,
                    task_actor='bot:cpo',
                    agent='hermes',
                ),
                'assistant-test': Identity('human:ana', 'owner', 'ana@acme.example', via='assistant', confirmed=True),
                'token-test': Identity('human:ana', 'owner', 'ana@acme.example', via_token=True, token_label='laptop'),
            },
        )
    )
    with TestClient(app) as client:
        client.github = github
        with app.state.store.transaction() as c:
            bots = {slug: {'name': slug, 'runtime': 'fake', 'status': 'active'} for slug in ('cpo', 'cmo')}
            people = [
                {'id': 'ana', 'email': 'ana@acme.example', 'team': 'leadership', 'primary_for': ['*']},
                {'id': 'ben', 'email': 'ben@acme.example', 'team': 'support'},
                {'id': 'cara', 'email': 'cara@acme.example', 'team': 'support'},
            ]
            H.sync_registry(c, bots, {'people': people})
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({'people': people}),))
            for slug, config in bots.items():
                c.execute('INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)', (slug, 'product', 'ana'))
                BS.write(c, slug, config)
            c.execute(
                "INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({'completed': '2026-01-01T00:00:00Z'}),)
            )
            H.type_create(c, H.KEEPER, 'Dev tickets', STEPS)
            # The bot signs in as an agent does (bot-test), so a refusal is the waiver's and not the credential's.
            c.execute(
                "INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('cpo','hermes','x',?,'human:ana')",
                (H.now(),),
            )
        yield client


def call(api, method, path, token='ana-test', body=None):
    return api.request(
        method,
        '/api/v2/' + path,
        json=body,
        headers={'Authorization': 'Bearer ' + token, 'Idempotency-Key': str(uuid.uuid4())},
    )


def child(api, ticket, title, status='done', verdict=None, head=None):
    """A review or QA subtask with a recorded verdict, written directly: the review gate is not under test here."""
    r = call(
        api,
        'POST',
        'tasks',
        body={
            'owner': 'cmo',
            'title': 'Check it',
            'body': 'Check the change.',
            'relations': [{'task': ticket['id'], 'kind': 'parent'}],
        },
    )
    assert r.status_code == 200, r.text
    with api.app.state.store.transaction() as c:
        c.execute(
            'UPDATE tasks SET title=?, status=?, verdict=?, verdict_head=? WHERE id=?',
            (title, status, verdict, head, r.json()['task']['id']),
        )
    return r.json()['task']


def green(api, checks=None):
    """GitHub reports the checks at the current head: a pass, and a skip that is not one."""
    api.github.checks = checks or {'check:python': 'passing', 'check:browser': 'skipped'}


def waive(api, row, token='ana-test', head=HEAD, reason='Urgent fix, QA bot down', **more):
    return call(api, 'POST', f"tasks/{row['id']}/qa-waiver", token, {'head': head, 'reason': reason, **more})
