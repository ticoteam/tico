"""Helpers that other tests share, moved out of test_github.py so they import no test file."""

import hashlib
import hmac
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from backend import github as G
from backend import hubdb as H
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import encode
from backend import bot_settings as BS


SECRET = "hook-secret"


PR = "https://github.com/ticoteam/tico/pull/412"


@pytest.fixture
def api(tmp_path):
    app = create_app(
        Settings(
            db_path=tmp_path / "hub.db",
            github_webhook_secret=SECRET,
            release_commit="",
            release_repo="ticoteam/tico",
            test_identities={"ana-test": Identity("human:ana", "owner", "ana@acme.example")},
        )
    )
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ("cpo", "cmo")}
            H.sync_registry(c, bots, {"people": [{"id": "ana", "email": "ana@acme.example", "team": "leadership"}]})
            c.execute(
                "INSERT INTO registry_metadata VALUES('people',?)",
                (
                    encode(
                        {
                            "people": [
                                {"id": "ana", "email": "ana@acme.example", "team": "leadership", "primary_for": ["*"]}
                            ]
                        }
                    ),
                ),
            )
            for slug, config in bots.items():
                c.execute(
                    'INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)',
                    (slug, 'product' if slug == 'cpo' else 'marketing', 'ana'),
                )
                BS.write(c, slug, config)
            c.execute(
                "INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),)
            )
        yield client


def headers():
    return {"Authorization": "Bearer ana-test", "Idempotency-Key": str(uuid.uuid4())}


def post(api, path, body, expected=200):
    r = api.post("/api/v2/" + path, json=body, headers=headers())
    assert r.status_code == expected, r.text
    data = r.json()
    return data["task"] if isinstance(data, dict) and "task" in data and set(data) <= {"task", "readback"} else data


def get(api, path):
    r = api.get("/api/v2/" + path, headers=headers())
    assert r.status_code == 200, r.text
    return r.json()


def hook(api, event, payload, secret=SECRET, expected=200):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = api.post(
        G.PATH,
        content=body,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": event, "Content-Type": "application/json"},
    )
    assert r.status_code == expected, r.text
    return r.json() if r.content else {}


def pr_event(action, **over):
    pr = {"html_url": PR, "draft": False, "merged": False, "merge_commit_sha": None, "merged_at": None, **over}
    return {"action": action, "pull_request": pr, "repository": {"full_name": "ticoteam/tico"}}


def _burst(api, task_id):
    """What a task's PR burst will send: grouped items, then each head's finished checks."""
    from backend import repositories as R

    with api.app.state.store.transaction() as c:
        burst = R.metadata(c, 'github-task-wake:' + task_id)
        return burst.get('items', []) + [d['item'] for d in burst.get('done', [])]


def _app(api):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created_at,created_by) "
            "VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,?)",
            (H.now(), 'human:ana'),
        )


def _suite(api, slug, status, conclusion=None, sha='a' * 40, id=None):
    hook(
        api,
        'check_suite',
        {
            'action': 'completed' if status == 'completed' else 'requested',
            'repository': {'full_name': 'ticoteam/tico'},
            'check_suite': {
                'id': id,
                'status': status,
                'conclusion': conclusion,
                'head_sha': sha,
                'app': {'slug': slug},
                'pull_requests': [{'number': 412}],
            },
        },
    )


def _status(api, context, state, sha='a' * 40):
    hook(api, 'status', {'repository': {'full_name': 'ticoteam/tico'}, 'sha': sha, 'context': context, 'state': state})


def _branch_pr(number, branch, repo='ticoteam/tico', head_repo=None, **over):
    return {
        'html_url': f'https://github.com/{repo}/pull/{number}',
        'number': number,
        'state': 'open',
        'draft': False,
        'merged': False,
        'merge_commit_sha': None,
        'merged_at': None,
        'head': {'ref': branch, 'sha': 'b' * 40, 'repo': {'full_name': head_repo or repo}},
        **over,
    }


def _prs(api, task_id):
    """The task's PR links as stored; a task GET would start its own GitHub refresh."""
    with api.app.state.store.read() as c:
        return {
            l['number']: {**l, 'detail': json.loads(l['detail'] or '{}')}
            for l in H._rows(c.execute("SELECT * FROM task_links WHERE task_id=? AND kind='pr'", (task_id,)))
        }


def _reconciler(api, monkeypatch, fetch, mint=None):
    """The background pass against a fake GitHub; `run()` makes one pass and waits for it."""
    import threading

    service = api.app.state.github_app
    with service.store.transaction() as c:
        for repo in ('ticoteam/tico', 'ticoteam/tico-services'):
            c.execute(
                "INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at,default_branch) "
                "VALUES(?,1,1,?,?,'main')",
                (repo, 'human:ana', H.now()),
            )
    monkeypatch.setattr(service, 'row', lambda c=None: {'slug': 'test-app', 'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', mint or (lambda *args, **kw: ('test-token', 'later')))
    monkeypatch.setattr(service, '_call', fetch)

    def run(fresh=False):
        if fresh:
            service.worktree_reconcile_next = 0
            service.__dict__.get('worktree_reconcile_cache', {}).clear()
        G.reconcile_worktree_prs(service)
        for worker in threading.enumerate():
            if worker.name == 'tico-worktree-pr-reconcile':
                worker.join(5)

    return service, run
