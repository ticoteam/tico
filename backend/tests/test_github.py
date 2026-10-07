"""The repository webhook moves a product-lane task with its pull request, and a release ships it."""

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

SECRET = "hook-secret"
PR = "https://github.com/ticoteam/tico/pull/412"


@pytest.fixture
def api(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "hub.db", github_webhook_secret=SECRET,
                              release_commit="", release_repo="ticoteam/tico", test_identities={
        "ana-test": Identity("human:ana", "owner", "ana@acme.example")}))
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ("cpo", "cmo")}
            H.sync_registry(c, bots, {"people": [{"id": "ana", "email": "ana@acme.example", "team": "leadership"}]})
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({"people": [
                {"id": "ana", "email": "ana@acme.example", "team": "leadership", "primary_for": ["*"]}]}),))
            for slug, config in bots.items():
                c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES(?,?,?,?)",
                          (slug, encode(config), "product" if slug == "cpo" else "marketing", "ana"))
            c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),))
        yield client


def headers():
    return {"Authorization": "Bearer ana-test", "Idempotency-Key": str(uuid.uuid4())}


def post(api, path, body, expected=200):
    r = api.post("/api/v2/" + path, json=body, headers=headers())
    assert r.status_code == expected, r.text
    data = r.json()
    return data["task"] if isinstance(data, dict) and set(data) == {"task"} else data


def get(api, path):
    r = api.get("/api/v2/" + path, headers=headers())
    assert r.status_code == 200, r.text
    return r.json()


def hook(api, event, payload, secret=SECRET, expected=200):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = api.post(G.PATH, content=body, headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": event,
                                               "Content-Type": "application/json"})
    assert r.status_code == expected, r.text
    return r.json() if r.content else {}


def pr_event(action, **over):
    pr = {"html_url": PR, "draft": False, "merged": False, "merge_commit_sha": None, "merged_at": None, **over}
    return {"action": action, "pull_request": pr, "repository": {"full_name": "ticoteam/tico"}}


def test_a_bad_signature_is_refused_and_an_unset_secret_hides_the_path(api, tmp_path):
    hook(api, "ping", {"zen": "x"}, secret="wrong", expected=403)
    assert hook(api, "ping", {"zen": "x"}) == {"ok": True}
    bare = create_app(Settings(db_path=tmp_path / "other.db"))
    with TestClient(bare) as client:
        r = client.post(G.PATH, content=b"{}", headers={"X-Hub-Signature-256": "sha256=00", "X-GitHub-Event": "ping"})
        assert r.status_code == 404


def test_the_pull_request_moves_the_task_through_review_ready_and_shipped(api):
    task = post(api, "tasks", {"owner": "cpo", "title": "Ship the pricing page", "body": "x", "links": [PR]})
    assert task["lane"] == "company" and task["status"] == "open"
    # The product lane is retired (new tasks are company work); a product row from before still moves.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET lane='product' WHERE id=?", (task["id"],))

    opened = hook(api, "pull_request", pr_event("opened"))
    assert opened["moved"] == [[task["id"], "review"]]
    assert get(api, "tasks/" + task["id"])["task"]["status"] == "review"

    merged = hook(api, "pull_request", pr_event("closed", merged=True, merge_commit_sha="abc123", merged_at="2026-09-18T20:00:00Z"))
    assert merged["moved"] == [[task["id"], "ready"]]
    detail = get(api, "tasks/" + task["id"])
    assert detail["task"]["status"] == "ready"
    assert detail["task"]["links"][0]["state"] == "merged" and detail["task"]["links"][0]["pr_sha"] == "abc123"

    # main moves on: the merge commit, then a later commit that the next release is built from
    pushed = hook(api, "push", {"ref": "refs/heads/main", "repository": {"full_name": "ticoteam/tico", "default_branch": "main"},
                                "commits": [{"id": "abc123"}, {"id": "def456"}], "head_commit": {"id": "def456"}})
    assert pushed["commits"] == 2 and pushed["shipped"] == []
    api.app.state.store.settings.release_commit = "def456"
    with api.app.state.store.transaction() as c:
        assert G.ship_deployed(c, api.app.state.store.settings) == [task["id"]]
    after = get(api, "tasks/" + task["id"])
    assert after["task"]["status"] == "done" and "Shipped in release def456" in after["task"]["note"]
    assert after["task"]["links"][0]["state"] == "shipped"


def test_many_prs_wait_for_every_link_and_roll_up_worst_state(api):
    second = PR.replace('/412', '/413')
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship both pieces', 'body': 'x', 'links': [PR, second]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET lane='product' WHERE id=?", (task['id'],))
    hook(api, 'pull_request', pr_event('opened', draft=True, head={'sha': 'head1'}))
    draft = get(api, 'tasks/' + task['id'])['task']
    assert draft['status'] == 'review' and draft['links'][0]['state'] == 'draft' and draft['pr_state'] == 'open'
    hook(api, 'pull_request', pr_event('ready_for_review', head={'sha': 'head1'}))
    hook(api, 'pull_request', pr_event('closed', merged=True, merge_commit_sha='abc123'))
    detail = get(api, 'tasks/' + task['id'])['task']
    assert detail['status'] == 'review' and detail['pr_state'] == 'open'
    assert detail['links'][0]['repo'] == 'ticoteam/tico' and detail['links'][0]['number'] == 412
    hook(api, 'pull_request', pr_event('converted_to_draft', html_url=second, draft=True))
    assert get(api, 'tasks/' + task['id'])['task']['status'] == 'doing'
    hook(api, 'pull_request', pr_event('synchronize', html_url=second, mergeable=False, number=413))
    assert get(api, 'tasks/' + task['id'])['task']['pr_state'] == 'conflict'
    hook(api, 'check_run', {'repository': {'full_name': 'ticoteam/tico'}, 'check_run': {
        'name': 'Unit tests', 'conclusion': 'failure', 'pull_requests': [{'number': 413}]}})
    assert get(api, 'tasks/' + task['id'])['task']['pr_state'] == 'failing'
    hook(api, 'pull_request_review', {'action': 'submitted', 'pull_request': {'html_url': second},
        'review': {'state': 'changes_requested'}})
    hook(api, 'pull_request_review_comment', {'action': 'created', 'pull_request': {'html_url': second}})
    links = get(api, 'tasks/' + task['id'] + '/links')['links']
    assert links[1]['review_state'] == 'changes_requested' and links[1]['pending_comments'] == 1
    hook(api, 'pull_request', pr_event('closed', html_url=second))
    assert get(api, 'tasks/' + task['id'])['task']['status'] == 'ready'
    response = api.delete('/api/v2/tasks/' + task['id'] + '/links/' + links[1]['id'], headers=headers())
    assert response.status_code == 200 and len(response.json()['links']) == 1


def test_webhook_burst_is_durable_and_sends_one_specific_wake(api):
    from backend import repositories as R
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Repair checks', 'body': 'x', 'links': [PR]})
    with api.app.state.store.transaction() as c:
        before = c.execute('SELECT count(*) FROM messages').fetchone()[0]
    for name in ('Unit tests', 'Lint'):
        hook(api, 'check_run', {'repository': {'full_name': 'ticoteam/tico'}, 'check_run': {
            'name': name, 'conclusion': 'failure', 'pull_requests': [{'number': 412}]}})
    with api.app.state.store.transaction() as c:
        assert G.flush_wakes(c) == []
        assert c.execute('SELECT count(*) FROM messages').fetchone()[0] == before
        key = 'github-task-wake:' + task['id']
        burst = R.metadata(c, key)
        assert len(burst['items']) == 2
        burst['due'] = H.shift(H.now(), seconds=-1)
        R.save_metadata(c, key, burst)
        assert G.flush_wakes(c) == [task['id']]
        assert G.flush_wakes(c) == []
        assert c.execute('SELECT count(*) FROM messages').fetchone()[0] == before + 1
        message = c.execute('SELECT body FROM messages ORDER BY created DESC LIMIT 1').fetchone()[0]
        assert 'Unit tests' in message and 'Lint' in message and 'tico#412' in message


def test_shipping_waits_for_all_merged_prs_in_the_release(api):
    second = PR.replace('/412', '/413')
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship several changes', 'body': 'x', 'links': [PR, second]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET lane='product' WHERE id=?", (task['id'],))
    hook(api, 'pull_request', pr_event('closed', merged=True, merge_commit_sha='first'))
    hook(api, 'pull_request', pr_event('closed', html_url=second, merged=True, merge_commit_sha='second'))
    api.app.state.store.settings.release_commit = 'first'
    with api.app.state.store.transaction() as c:
        assert G.ship_deployed(c, api.app.state.store.settings) == []
        G.push(c, {'ref': 'refs/heads/main', 'repository': {'full_name': 'ticoteam/tico'},
                   'commits': [{'id': 'first'}, {'id': 'second'}]})
        api.app.state.store.settings.release_commit = 'second'
        assert G.ship_deployed(c, api.app.state.store.settings) == [task['id']]
        assert all(l['state'] == 'shipped' for l in H.task_links(c, task['id']))


def test_task_link_upgrade_preserves_legacy_pr_rows(api):
    from backend.store import Store
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep the existing PR', 'body': 'x', 'links': [PR]})
    store = api.app.state.store
    with store.transaction() as c:
        c.execute('CREATE TABLE legacy_task_links(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), '
                  'kind TEXT NOT NULL, url TEXT NOT NULL, title TEXT, state TEXT, added_by TEXT, created TEXT NOT NULL, '
                  'pr_sha TEXT, pr_merged_at TEXT)')
        c.execute('INSERT INTO legacy_task_links SELECT id,task_id,kind,url,title,state,added_by,created,pr_sha,pr_merged_at FROM task_links')
        c.execute('DROP TABLE task_links')
        c.execute('ALTER TABLE legacy_task_links RENAME TO task_links')
        c.execute('PRAGMA user_version=17')
        c.execute('DELETE FROM cloud_migrations WHERE version=51')
    Store(store.settings).initialize(seed_market=False)
    with store.read() as c:
        link = H.task_links(c, task['id'])[0]
        assert link['url'] == PR and link['state'] == 'open' and link['title'] == 'tico#412'
        assert link['repo'] == 'ticoteam/tico' and link['number'] == 412
        assert link['path'] is None and link['computer_id'] is None and link['checks'] is None
        assert c.execute('PRAGMA user_version').fetchone()[0] == len(H.MIGRATIONS)
        assert c.execute('SELECT 1 FROM cloud_migrations WHERE version=51').fetchone()


def test_abandoned_pr_returns_to_doing_and_manual_moves_survive_events(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Handle abandoned work', 'body': 'x', 'links': [PR]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET lane='product',status='review' WHERE id=?", (task['id'],))
    hook(api, 'pull_request', pr_event('closed'))
    task = get(api, 'tasks/' + task['id'])['task']
    assert task['status'] == 'doing'
    # Human Ready and Done stay available even with an untracked open PR.
    post(api, 'tasks/' + task['id'] + '/links', {'url': PR.replace('/412', '/413')})
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'status': 'ready'})
    post(api, 'tasks/' + task['id'] + '/links', {'url': PR.replace('/412', '/414')})
    assert get(api, 'tasks/' + task['id'])['task']['status'] == 'ready'
    hook(api, 'pull_request', pr_event('reopened'))
    assert get(api, 'tasks/' + task['id'])['task']['status'] == 'ready'
    task = post(api, 'tasks/' + task['id'], {'version': task['version'], 'status': 'waiting'})
    hook(api, 'pull_request', pr_event('synchronize'))
    hook(api, 'pull_request', pr_event('closed', merged=True))
    task = get(api, 'tasks/' + task['id'])['task']
    assert task['status'] == 'waiting'
    post(api, 'tasks/' + task['id'], {'version': task['version'], 'status': 'done'})


def test_task_open_refreshes_in_background_with_coalescing_and_backoff(api, monkeypatch):
    from types import SimpleNamespace
    from threading import Event
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Refresh without webhooks', 'body': 'x',
                             'links': [PR, PR.replace('/412', '/413'), PR.replace('ticoteam/tico', 'example/service')]})
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c=None: {'slug': 'test-app', 'org': 'ticoteam'})
    with service.store.transaction() as c:
        c.execute("INSERT INTO repositories(full_name,reachable,enabled,added_by,updated) VALUES(?,1,1,?,?)",
                  ('ticoteam/tico', 'human:ana', H.now()))
    entered, release, finished = Event(), Event(), Event()
    calls = []
    def mint(*args):
        entered.set()
        assert release.wait(3)
        return 'test-token', 'later'
    monkeypatch.setattr(service, 'mint', mint)
    def fetch(method, path, **kw):
        calls.append(path)
        if path.endswith('/413'):
            raise RuntimeError('offline')
        if '/commits/' in path:
            return SimpleNamespace(status_code=200, json=lambda: {'committer': {'login': 'computer-login'}})
        return SimpleNamespace(status_code=200, json=lambda: {'state': 'closed', 'merged': True,
            'merge_commit_sha': 'merged', 'head': {'sha': 'cur'}, 'user': {'login': 'computer-login'}})
    monkeypatch.setattr(service, '_call', fetch)
    real = G.pull_request
    def update(*args, **kwargs):
        result = real(*args, **kwargs)
        finished.set()
        return result
    monkeypatch.setattr(G, 'pull_request', update)
    try:
        links = get(api, 'tasks/' + task['id'])['task']['links']
        assert [l['state'] for l in links] == ['open', 'open', 'open']
        assert entered.wait(1)
        get(api, 'tasks/' + task['id'])
        assert calls == []  # a blocked token mint cannot delay or duplicate task reads
    finally:
        release.set()
    assert finished.wait(2)
    # Join the actual worker so the store is not torn down while it is writing.
    import threading
    for worker in threading.enumerate():
        if worker.name == 'tico-pr-refresh':
            worker.join(2)
    assert not service.pr_refresh_running
    for _ in range(2):
        links = get(api, 'tasks/' + task['id'])['task']['links']
        assert [l['state'] for l in links] == ['merged', 'open', 'open']
    assert len(calls) == 3  # two PRs plus head identity; unreachable repo skipped
    with service.store.read() as c:
        detail = H._json(H.task_links(c, task['id'])[0]['detail_json'], {})
        assert detail['head_login'] == 'computer-login'
    failures = service.pr_refresh_cache[('ticoteam/tico', 413)]
    assert failures['failures'] == 1
    import time
    assert failures['next'] > time.monotonic() + 290
    failures['next'] = 0
    G.refresh_task_prs(service, task['id'])
    for worker in threading.enumerate():
        if worker.name == 'tico-pr-refresh':
            worker.join(2)
    assert service.pr_refresh_cache[('ticoteam/tico', 413)]['failures'] == 2
    assert service.pr_refresh_cache[('ticoteam/tico', 413)]['next'] > time.monotonic() + 590


def test_reviewer_who_pushes_is_not_the_bot_and_changes_requests_always_wake(api):
    from backend.repositories import metadata
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Handle reviewer feedback', 'body': 'x', 'links': [PR]})
    payload = pr_event('synchronize', head={'sha': 'cur'}, user={'login': 'engineer[bot]'})
    payload['sender'] = {'login': 'engineer[bot]'}
    hook(api, 'pull_request', payload)
    # Author identity remains available even when a comment payload omits the PR author.
    hook(api, 'pull_request_review_comment', {'action': 'created', 'pull_request': {'html_url': PR},
        'comment': {'id': 1, 'user': {'login': 'engineer[bot]'}}})
    with api.app.state.store.read() as c:
        assert not metadata(c, 'github-task-wake:' + task['id'])
    payload['pull_request']['head']['sha'] = 'next'
    payload['sender'] = {'login': 'sam'}
    hook(api, 'pull_request', payload)
    with api.app.state.store.read() as c:
        assert 'head_login' not in H._json(H.task_links(c, task['id'])[0]['detail_json'], {})
    hook(api, 'pull_request_review_comment', {'action': 'created', 'pull_request': {'html_url': PR},
        'comment': {'id': 2, 'user': {'login': 'sam'}}})
    with api.app.state.store.transaction() as c:
        assert metadata(c, 'github-task-wake:' + task['id'])['items']
        c.execute("DELETE FROM registry_metadata WHERE key=?", ('github-task-wake:' + task['id'],))
    hook(api, 'pull_request_review', {'action': 'submitted', 'pull_request': payload['pull_request'],
        'review': {'id': 9, 'state': 'commented', 'user': {'login': 'sam'}}})
    with api.app.state.store.transaction() as c:
        assert 'Review commented' in metadata(c, 'github-task-wake:' + task['id'])['items'][0]
        c.execute("DELETE FROM registry_metadata WHERE key=?", ('github-task-wake:' + task['id'],))
    for n, login in enumerate(['sam', 'engineer[bot]']):
        hook(api, 'pull_request_review', {'action': 'submitted', 'pull_request': payload['pull_request'],
            'review': {'id': n + 10, 'state': 'changes_requested', 'user': {'login': login}}})
        with api.app.state.store.transaction() as c:
            assert 'changes requested' in metadata(c, 'github-task-wake:' + task['id'])['items'][0]
            c.execute("DELETE FROM registry_metadata WHERE key=?", ('github-task-wake:' + task['id'],))
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created,created_by) "
                  "VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,?)", (H.now(), 'human:ana'))
    hook(api, 'pull_request_review_comment', {'action': 'created', 'pull_request': {'html_url': PR},
        'comment': {'id': 44, 'user': {'login': 'token-login'}, 'performed_via_github_app': {'slug': 'example-app'}}})
    with api.app.state.store.read() as c:
        assert not metadata(c, 'github-task-wake:' + task['id'])


def test_untracked_pr_does_not_block_ready_and_unlink_recomputes(api):
    second = PR.replace('/412', '/413')
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship tracked work', 'body': 'x',
                             'links': [PR, second, 'https://github.com/example/service/pull/1']})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET lane='product' WHERE id=?", (task['id'],))
    hook(api, 'pull_request', pr_event('opened', html_url=second))
    hook(api, 'pull_request', pr_event('closed', merged=True, merge_commit_sha='abc'))
    detail = get(api, 'tasks/' + task['id'])['task']
    assert detail['status'] == 'review'
    open_link = next(l for l in detail['links'] if l['url'] == second)
    assert api.delete('/api/v2/tasks/' + task['id'] + '/links/' + open_link['id'], headers=headers()).status_code == 200
    assert get(api, 'tasks/' + task['id'])['task']['status'] == 'ready'
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created,created_by) "
                  "VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,?)", (H.now(), 'human:ana'))
    links = post(api, 'tasks/' + task['id'] + '/links', {'url': 'https://github.com/outside/service/pull/2'})['links']
    assert links[-1]['kind'] == 'url'
    # An unreachable, unticked repo with no webhook history cannot hold up automatic Ready.
    other = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship without stale links', 'body': 'x',
                              'links': [PR, PR.replace('ticoteam/tico', 'ticoteam/other')]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET lane='product' WHERE id=?", (other['id'],))
    with api.app.state.store.transaction() as c:
        G.pull_request(c, {'action': 'refresh', 'pull_request': {
            'html_url': PR.replace('ticoteam/tico', 'ticoteam/other'), 'state': 'open'}})
    hook(api, 'pull_request', pr_event('closed', merged=True))
    assert get(api, 'tasks/' + other['id'])['task']['status'] == 'ready'


def test_background_refresh_keeps_newer_webhook_state(api, monkeypatch):
    from types import SimpleNamespace
    import threading
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep newest PR facts', 'body': 'x', 'links': [PR]})
    service = api.app.state.github_app
    with service.store.transaction() as c:
        c.execute("INSERT INTO repositories(full_name,reachable,enabled,added_by,updated) VALUES(?,1,1,?,?)",
                  ('ticoteam/tico', 'human:ana', H.now()))
    monkeypatch.setattr(service, 'row', lambda c=None: {'slug': 'test-app', 'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *args: ('test-token', 'later'))
    def fetch(method, path, **kw):
        with service.store.transaction() as c:
            G.pull_request(c, pr_event('closed', merged=True))
        return SimpleNamespace(status_code=200, json=lambda: {'state': 'open'})
    monkeypatch.setattr(service, '_call', fetch)
    G.refresh_task_prs(service, task['id'])
    for worker in threading.enumerate():
        if worker.name == 'tico-pr-refresh':
            worker.join(2)
    assert not service.pr_refresh_running
    with service.store.read() as c:
        assert H.task_links(c, task['id'])[0]['state'] == 'merged'


def test_ready_waits_for_second_repository_without_events_on_this_task(api, monkeypatch):
    monkeypatch.setattr(G, 'refresh_task_prs', lambda *args: None)
    other = 'https://github.com/ticoteam/web/pull/7'
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created,created_by) "
                  "VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,?)", (H.now(), 'human:ana'))
        c.execute("INSERT INTO repositories(full_name,reachable,enabled,added_by,updated) VALUES(?,?,?,?,?)",
                  ('TicoTeam/Web', 0, 1, 'human:ana', H.now()))
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship two repositories', 'body': 'x', 'links': [PR, other]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET lane='product' WHERE id=?", (task['id'],))
    hook(api, 'pull_request', pr_event('opened'))
    hook(api, 'pull_request', pr_event('closed', merged=True, merge_commit_sha='abc'))
    detail = get(api, 'tasks/' + task['id'])['task']
    assert detail['status'] == 'review'
    assert next(l for l in detail['links'] if l['url'] == other)['state'] == 'open'
    hook(api, 'pull_request', pr_event('closed', html_url=other))
    assert get(api, 'tasks/' + task['id'])['task']['status'] == 'ready'


def test_new_head_clears_stale_conflict_and_deduplicates_per_head(api):
    from backend.repositories import metadata
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Fix merge conflict', 'body': 'x', 'links': [PR]})
    def clear_wake():
        with api.app.state.store.transaction() as c:
            items = metadata(c, 'github-task-wake:' + task['id']).get('items', [])
            c.execute("DELETE FROM registry_metadata WHERE key=?", ('github-task-wake:' + task['id'],))
            return items
    hook(api, 'pull_request', pr_event('synchronize', mergeable=False, head={'sha': 'a'}))
    assert clear_wake()
    hook(api, 'pull_request', pr_event('synchronize', mergeable=None, head={'sha': 'b'}))
    link = get(api, 'tasks/' + task['id'])['task']['links'][0]
    assert link['mergeable'] == 'unknown' and not clear_wake()
    hook(api, 'pull_request', pr_event('synchronize', mergeable=False, head={'sha': 'b'}))
    assert clear_wake()
    hook(api, 'pull_request', pr_event('synchronize', mergeable=None, head={'sha': 'b'}))
    with api.app.state.store.transaction() as c:
        G.pull_request(c, pr_event('refresh', mergeable=False, head={'sha': 'b'}))
    assert not clear_wake()
    with api.app.state.store.transaction() as c:
        G.pull_request(c, pr_event('refresh', mergeable=True, head={'sha': 'b'}))
        assert 'conflict_head' not in H._json(H.task_links(c, task['id'])[0]['detail_json'], {})
    assert get(api, 'tasks/' + task['id'])['task']['pr_state'] == 'open'
    hook(api, 'pull_request', pr_event('synchronize', mergeable=False, head={'sha': 'b'}))
    assert clear_wake()
def test_grouped_wakes_retry_bad_rows_and_send_blocked_tasks(api, monkeypatch):
    finished_status = "done"
    from backend.repositories import save_metadata
    tasks = [post(api, "tasks", {"owner": "cpo", "title": title, "body": "Review work"})
             for title in ("Broken notice", "Blocked task", "Finished task")]
    store = api.app.state.store
    with store.transaction() as c:
        for task in tasks:
            save_metadata(c, "github-task-wake:" + task["id"], {"due": H.shift(H.now(), seconds=-1), "items": ["Checks failed"]})
        c.execute("UPDATE tasks SET status='blocked' WHERE id=?", (tasks[1]["id"],))
        c.execute("UPDATE tasks SET status=? WHERE id=?", (finished_status, tasks[2]["id"]))
    wake = H._wake
    def broken(c, task, *args):
        if task["id"] == tasks[0]["id"]:
            H.event(c, H.KEEPER, "wake.partial", task["id"])
            raise RuntimeError("bad conversation")
        return wake(c, task, *args)
    monkeypatch.setattr(H, "_wake", broken)
    with store.transaction() as c:
        assert G.flush_wakes(c) == [tasks[1]["id"]]
    with store.read() as c:
        assert [r[0] for r in c.execute("SELECT key FROM registry_metadata WHERE key LIKE 'github-task-wake:%'")] == ["github-task-wake:" + tasks[0]["id"]]
        assert not c.execute("SELECT 1 FROM events WHERE action='wake.partial'").fetchone()
    monkeypatch.setattr(H, "_wake", wake)
    with store.transaction() as c:
        assert G.flush_wakes(c) == [tasks[0]["id"]]


def test_deploy_query_uses_repository_index_and_ignores_other_repos(api):
    own = post(api, "tasks", {"owner": "cpo", "title": "Release work", "body": "Review", "links": [PR]})
    other = post(api, "tasks", {"owner": "cpo", "title": "Other work", "body": "Review",
                                "links": ["https://github.com/example/other/pull/412"]})
    store = api.app.state.store
    store.settings.release_commit = "release-sha"
    store.settings.release_repo = "TicoTeam/Tico"
    with store.transaction() as c:
        c.execute("UPDATE tasks SET status='ready' WHERE id IN (?,?)", (own["id"], other["id"]))
        c.execute("UPDATE task_links SET state='merged',pr_sha='release-sha'")
        plan = c.execute("EXPLAIN QUERY PLAN " + G.DEPLOY_QUERY,
                         ("https://github.com/ticoteam/tico/pull/%",)).fetchall()
        assert any("task_links_repo_url" in r[3] and "url>?" in r[3] for r in plan)
        assert G.ship_deployed(c, store.settings) == [own["id"]]
        assert H.task(c, other["id"])["status"] == "ready"


def test_a_review_request_puts_the_person_in_the_configured_role(api):
    assert post(api, "people/ana", {"github": "@Ana-Dev"})["github"] == "ana-dev"
    task = post(api, "tasks", {"owner": "cpo", "title": "Ship the pricing page", "body": "x", "links": [PR]})
    asked = {**pr_event("review_requested"), "requested_reviewer": {"login": "Ana-Dev"}}
    roles = lambda: get(api, "tasks/" + task["id"])["task"].get("roles") or {}

    hook(api, "pull_request", asked)             # no role configured: roles stay generic and untouched
    assert roles() == {}
    api.app.state.store.settings.github_review_role = "reviewer"
    hook(api, "pull_request", asked)
    assert roles() == {"reviewer": ["human:ana"]}
    hook(api, "pull_request", {**asked, "action": "review_request_removed"})
    assert roles().get("reviewer", []) == []
    # A refresh adds whoever is still asked, and never takes off someone who already reviewed.
    hook(api, "pull_request", pr_event("opened", requested_reviewers=[{"login": "ana-dev"}, {"login": "stranger"}]))
    assert roles() == {"reviewer": ["human:ana"]}
    hook(api, "pull_request", pr_event("synchronize", requested_reviewers=[]))
    assert roles() == {"reviewer": ["human:ana"]}
