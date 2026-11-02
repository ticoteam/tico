"""The repository webhook keeps a task's pull request links current; it never moves the task."""

import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from urllib.parse import quote

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
                c.execute("INSERT INTO bot_config(bot,config,team,owner_id) VALUES(?,?,?,?)",
                          (slug, encode(config), "product" if slug == "cpo" else "marketing", "ana"))
            c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),))
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


def test_a_pull_request_never_moves_its_task_through_merge_and_deploy(api):
    """A PR on a task is information: opened, merged and deployed, the task keeps its status and step."""
    typ = post(api, "task-types", {"name": "Product", "steps": [
        {"name": "Build", "status": "doing"}, {"name": "QA", "status": "review"}, {"name": "Shipped", "status": "done"}]})["type"]
    plain = post(api, "tasks", {"owner": "cpo", "title": "Ship the pricing page", "body": "x", "links": [PR]})
    custom = post(api, "tasks", {"owner": "cpo", "title": "Ship the pricing copy", "body": "x", "links": [PR],
                                 "type": typ["id"], "step": "Build"})
    with api.app.state.store.transaction() as c:   # a legacy product-lane row used to move; nothing does now
        c.execute("UPDATE tasks SET lane='product' WHERE id=?", (plain["id"],))
    before = {t["id"]: (t["status"], t["step_id"]) for t in (plain, custom)}
    def unmoved():
        for tid, (status, step) in before.items():
            task = get(api, "tasks/" + tid)["task"]
            assert (task["status"], task["step_id"]) == (status, step)
        return [get(api, "tasks/" + tid)["task"]["links"][0] for tid in before]

    opened = hook(api, "pull_request", pr_event("opened"))
    assert "moved" not in opened and all(l["state"] == "open" for l in unmoved())
    hook(api, "pull_request", pr_event("closed", merged=True, merge_commit_sha="abc123", merged_at="2026-09-18T20:00:00Z"))
    assert all(l["state"] == "merged" and l["pr_sha"] == "abc123" for l in unmoved())

    # main moves on: the merge commit, then a later commit that the next release is built from
    pushed = hook(api, "push", {"ref": "refs/heads/main", "repository": {"full_name": "ticoteam/tico", "default_branch": "main"},
                                "commits": [{"id": "abc123"}, {"id": "def456"}], "head_commit": {"id": "def456"}})
    assert pushed["commits"] == 2 and pushed["shipped"] == []
    api.app.state.store.settings.release_commit = "def456"
    with api.app.state.store.transaction() as c:
        assert sorted(G.ship_deployed(c, api.app.state.store.settings)) == sorted(before)
        assert c.execute("SELECT count(*) FROM events WHERE action='github.shipped'").fetchone()[0] == 2
    assert all(l["state"] == "shipped" for l in unmoved())
    # a tag push or a published release moves nothing; a release only wakes tasks waiting for it (test_milestone_waits)
    hook(api, "push", {"ref": "refs/tags/v1.2.3", "repository": {"full_name": "ticoteam/tico"}, "after": "d" * 40})
    assert hook(api, "release", {"action": "published", "repository": {"full_name": "ticoteam/tico"},
                                 "release": {"tag_name": "v1.2.3", "target_commitish": "d" * 40}})["woken"] == []
    unmoved()


def test_many_prs_roll_up_worst_state(api):
    second = PR.replace('/412', '/413')
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship both pieces', 'body': 'x', 'links': [PR, second]})
    hook(api, 'pull_request', pr_event('opened', draft=True, head={'sha': 'head1'}))
    draft = get(api, 'tasks/' + task['id'])['task']
    assert draft['links'][0]['state'] == 'draft' and draft['pr_state'] == 'open'
    hook(api, 'pull_request', pr_event('ready_for_review', head={'sha': 'head1'}))
    hook(api, 'pull_request', pr_event('closed', merged=True, merge_commit_sha='abc123'))
    detail = get(api, 'tasks/' + task['id'])['task']
    assert detail['pr_state'] == 'open'
    assert detail['links'][0]['repo'] == 'ticoteam/tico' and detail['links'][0]['number'] == 412
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
    assert get(api, 'tasks/' + task['id'])['task']['status'] == task['status']
    response = api.delete('/api/v2/tasks/' + task['id'] + '/links/' + links[1]['id'], headers=headers())
    assert response.status_code == 200 and len(response.json()['links']) == 1
    assert get(api, 'tasks/' + task['id'])['task']['status'] == task['status']


def test_webhook_burst_is_durable_and_sends_one_specific_wake(api):
    from backend import repositories as R
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Repair checks', 'body': 'x', 'links': [PR]})
    with api.app.state.store.transaction() as c:
        before = c.execute('SELECT count(*) FROM messages').fetchone()[0]
    for n in (1, 2):
        hook(api, 'pull_request_review_comment', {'action': 'created', 'pull_request': {'html_url': PR},
                                                  'comment': {'id': n, 'user': {'login': 'sam'}}})
    with api.app.state.store.transaction() as c:
        key = 'github-task-wake:' + task['id']
        burst = R.metadata(c, key)
        assert len(burst['items']) == 2
        # Performance owns the delay at enqueue time; GitHub adds no second debounce.
        assert G.flush_wakes(c) == [task['id']]
        assert G.flush_wakes(c) == []
        assert c.execute('SELECT count(*) FROM messages').fetchone()[0] == before + 1
        message = c.execute('SELECT body FROM messages ORDER BY created_at DESC LIMIT 1').fetchone()[0]
        assert '2 review comments' in message and 'tico#412' in message
        job = c.execute("SELECT held,wake_preset FROM jobs WHERE bot_id='cpo' ORDER BY rowid DESC LIMIT 1").fetchone()
        assert job['held'] and job['wake_preset'] == 'performance'


def test_each_merged_pr_ships_once_the_release_contains_it(api):
    second = PR.replace('/412', '/413')
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship several changes', 'body': 'x', 'links': [PR, second]})
    hook(api, 'pull_request', pr_event('closed', merged=True, merge_commit_sha='first'))
    hook(api, 'pull_request', pr_event('closed', html_url=second, merged=True, merge_commit_sha='second'))
    api.app.state.store.settings.release_commit = 'first'
    with api.app.state.store.transaction() as c:
        assert G.ship_deployed(c, api.app.state.store.settings) == [task['id']]
        assert [l['state'] for l in H.task_links(c, task['id'])] == ['shipped', 'merged']
        G.push(c, {'ref': 'refs/heads/main', 'repository': {'full_name': 'ticoteam/tico'},
                   'commits': [{'id': 'first'}, {'id': 'second'}]})
        api.app.state.store.settings.release_commit = 'second'
        assert G.ship_deployed(c, api.app.state.store.settings) == [task['id']]
        assert all(l['state'] == 'shipped' for l in H.task_links(c, task['id']))
        assert H.task(c, task['id'])['status'] == task['status']


def test_task_link_upgrade_preserves_legacy_pr_rows(api):
    from backend.store import Store
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep the existing PR', 'body': 'x', 'links': [PR]})
    store = api.app.state.store
    with store.transaction() as c:
        c.execute('CREATE TABLE legacy_task_links(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), '
                  'kind TEXT NOT NULL, url TEXT NOT NULL, title TEXT, state TEXT, added_by TEXT, created_at TEXT NOT NULL, '
                  'pr_sha TEXT, pr_merged_at TEXT)')
        c.execute('INSERT INTO legacy_task_links SELECT id,task_id,kind,url,title,state,added_by,created_at,pr_sha,pr_merged_at FROM task_links')
        c.execute('DROP TABLE task_links')
        c.execute('ALTER TABLE legacy_task_links RENAME TO task_links')
        c.execute('PRAGMA user_version=17')
        c.execute('DELETE FROM cloud_migrations WHERE version=51')
    # Version 17 is below the bridge, so this boot is the legacy import path (adopt_legacy), as test_upgrade does.
    Store(store.settings).initialize(seed_market=False, adopt_legacy=True)
    with store.read() as c:
        link = H.task_links(c, task['id'])[0]
        assert link['url'] == PR and link['state'] == 'open' and link['title'] == 'tico#412'
        assert link['repo'] == 'ticoteam/tico' and link['number'] == 412
        assert link['path'] is None and link['computer_id'] is None and link['checks'] is None
        assert c.execute('PRAGMA user_version').fetchone()[0] == len(H.MIGRATIONS)
        assert c.execute('SELECT 1 FROM cloud_migrations WHERE version=51').fetchone()




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
        assert 'head_login' not in H._json(H.task_links(c, task['id'])[0]['detail'], {})
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
    for n, login in enumerate(['sam', 'reviewer[bot]']):
        hook(api, 'pull_request_review', {'action': 'submitted', 'pull_request': payload['pull_request'],
            'review': {'id': n + 10, 'state': 'changes_requested', 'user': {'login': login}}})
        with api.app.state.store.transaction() as c:
            burst = metadata(c, 'github-task-wake:' + task['id'])
            # A person's change request wakes; an App's is a note for the next run.
            assert 'changes requested' in (burst.get('items') or burst['notes'])[0] and bool(burst.get('items')) == (login == 'sam')
            c.execute("DELETE FROM registry_metadata WHERE key=?", ('github-task-wake:' + task['id'],))
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created_at,created_by) "
                  "VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,?)", (H.now(), 'human:ana'))
    hook(api, 'pull_request_review_comment', {'action': 'created', 'pull_request': {'html_url': PR},
        'comment': {'id': 44, 'user': {'login': 'token-login'}, 'performed_via_github_app': {'slug': 'example-app'}}})
    with api.app.state.store.read() as c:
        assert not metadata(c, 'github-task-wake:' + task['id'])


def test_a_pr_outside_the_app_org_is_a_plain_link(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship tracked work', 'body': 'x', 'links': [PR]})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created_at,created_by) "
                  "VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,?)", (H.now(), 'human:ana'))
    links = post(api, 'tasks/' + task['id'] + '/links', {'url': 'https://github.com/outside/service/pull/2'})['links']
    assert links[-1]['kind'] == 'url'


def test_background_refresh_keeps_newer_webhook_state(api, monkeypatch):
    from types import SimpleNamespace
    import threading
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep newest PR facts', 'body': 'x', 'links': [PR]})
    service = api.app.state.github_app
    with service.store.transaction() as c:
        c.execute("INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at) VALUES(?,1,1,?,?)",
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
        assert 'conflict_head' not in H._json(H.task_links(c, task['id'])[0]['detail'], {})
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
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (tasks[1]["id"],))
        c.execute("UPDATE tasks SET status=? WHERE id=?", (finished_status, tasks[2]["id"]))
    wake = H._wake
    def broken(c, task, *args, **kwargs):
        if task["id"] == tasks[0]["id"]:
            H.event(c, H.KEEPER, "wake.partial", task["id"])
            raise RuntimeError("bad conversation")
        return wake(c, task, *args, **kwargs)
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
        assert H.task(c, own["id"])["status"] == "ready"
        assert H.task_links(c, other["id"])[0]["state"] == "merged"


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
    # Two people never share a login; one a roster already shares (written before the check) names nobody.
    with api.app.state.store.transaction() as c:
        people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        people["people"].append({"id": "bo", "email": "bo@acme.example"})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(people),))
        H.sync_registry(c, {}, people)
    post(api, "people/bo", {"github": "ANA-dev"}, expected=409)
    with api.app.state.store.transaction() as c:
        people["people"][-1]["github"] = "ana-dev"
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(people),))
    hook(api, "pull_request", asked)
    assert roles() == {"reviewer": ["human:ana"]}



def _burst(api, task_id):
    """What a task's PR burst will send: grouped items, then each head's finished checks."""
    from backend import repositories as R
    with api.app.state.store.transaction() as c:
        burst = R.metadata(c, 'github-task-wake:' + task_id)
        return burst.get('items', []) + [d['item'] for d in burst.get('done', [])]


def _app(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created_at,created_by) "
                  "VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,?)", (H.now(), 'human:ana'))


def _arm(api, task_id, armed=True, url=PR):
    return post(api, 'tasks/' + task_id + '/links', {'url': url, 'wake_once': armed})['links']


def _suite(api, slug, status, conclusion=None, sha='a' * 40, id=None):
    hook(api, 'check_suite', {'action': 'completed' if status == 'completed' else 'requested',
                              'repository': {'full_name': 'ticoteam/tico'}, 'check_suite': {
        'id': id, 'status': status, 'conclusion': conclusion, 'head_sha': sha, 'app': {'slug': slug},
        'pull_requests': [{'number': 412}]}})


def test_wake_once_fires_once_when_every_suite_finishes_and_never_moves_the_task(api):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on CI', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _suite(api, 'github-actions', 'completed', 'success')
    # Unarmed, the head's one result is queued all the same; backend/rules.py decides whether it wakes or is a note.
    assert _burst(api, task['id']) == [f"Checks finished on tico#412: passed {PR}/checks at {'a' * 12}"]
    links = _arm(api, task['id'])
    assert json.loads(links[0]['detail'])['wake_once']['by'] == 'human:ana'
    assert _arm(api, task['id']) == links                     # arming twice changes nothing
    # A single run, or one suite while another still runs, is not the end of the checks.
    hook(api, 'check_run', {'repository': {'full_name': 'ticoteam/tico'}, 'check_run': {
        'name': 'Unit tests', 'conclusion': 'success', 'head_sha': 'a' * 40, 'pull_requests': [{'number': 412}]}})
    _suite(api, 'github-actions', 'in_progress')
    _suite(api, 'vercel', 'completed', 'success')
    hook(api, 'check_run', {'repository': {'full_name': 'ticoteam/tico'}, 'check_run': {
        'name': 'Deploy preview', 'status': 'in_progress', 'conclusion': None, 'head_sha': 'a' * 40,
        'pull_requests': [{'number': 412}]}})
    _suite(api, 'github-actions', 'completed', 'failure')    # a failing check alone says nothing: one is still pending
    assert len(_burst(api, task['id'])) == 1 and 'passed' in _burst(api, task['id'])[0]
    # The last pending run finishing after the suites have is the end of the checks.
    hook(api, 'check_run', {'repository': {'full_name': 'ticoteam/tico'}, 'check_run': {
        'name': 'Deploy preview', 'status': 'completed', 'conclusion': 'success', 'head_sha': 'a' * 40,
        'pull_requests': [{'number': 412}]}})
    _suite(api, 'github-actions', 'completed', 'failure')     # a redelivery
    # The result that was still waiting to be sent is replaced, so one head gives one wake.
    assert _burst(api, task['id']) == [f"Checks finished on tico#412: failed (github-actions) {PR}/checks at {'a' * 12}"]
    detail = json.loads(get(api, 'tasks/' + task['id'] + '/links')['links'][0]['detail'])
    assert 'wake_once' not in detail and detail['wake_once_fired']['item'].startswith('Checks finished')
    assert get(api, 'tasks/' + task['id'])['task']['status'] == task['status']


def _status(api, context, state, sha='a' * 40):
    hook(api, 'status', {'repository': {'full_name': 'ticoteam/tico'}, 'sha': sha, 'context': context, 'state': state})


def test_wake_once_on_a_commit_status_that_finishes_last_names_it_when_it_fails(api):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on CI', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _arm(api, task['id'])
    _status(api, 'ci/legacy', 'pending')
    _suite(api, 'github-actions', 'completed', 'success')     # a status is still pending: not finished
    _status(api, 'ci/other', 'success')                       # never seen pending: one of many, not the last
    assert _burst(api, task['id']) == []
    _status(api, 'ci/legacy', 'failure')
    _status(api, 'ci/legacy', 'failure')                      # a redelivery
    assert _burst(api, task['id']) == [f"Checks finished on tico#412: failed (ci/legacy) {PR}/checks at {'a' * 12}"]
    detail = json.loads(get(api, 'tasks/' + task['id'] + '/links')['links'][0]['detail'])
    assert 'wake_once' not in detail


def test_wake_once_holds_while_a_finished_runs_own_suite_still_runs(api):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on CI', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _arm(api, task['id'])
    def run(name, status, conclusion=None, suite='in_progress', id=2):
        hook(api, 'check_run', {'repository': {'full_name': 'ticoteam/tico'}, 'check_run': {
            'name': name, 'status': status, 'conclusion': conclusion, 'head_sha': 'a' * 40, 'app': {'slug': 'github-actions'},
            'check_suite': {'id': id, 'status': suite, 'app': {'slug': 'github-actions'}}, 'pull_requests': [{'number': 412}]}})
    run('build', 'in_progress')
    _suite(api, 'vercel', 'completed', 'success')
    _suite(api, 'github-actions', 'completed', 'failure', id=1)   # another workflow, its own suite
    run('build', 'completed', 'success')                      # its workflow has more jobs to come
    run('deploy', 'completed', 'failure')
    assert _burst(api, task['id']) == []                      # no single check is news
    _suite(api, 'github-actions', 'completed', 'failure', id=2)
    assert _burst(api, task['id']) == [
        f"Checks finished on tico#412: failed (github-actions, deploy) {PR}/checks at {'a' * 12}"]
    run('deploy', 'completed', 'failure')                     # delivered late: does not reopen its suite
    detail = json.loads(get(api, 'tasks/' + task['id'] + '/links')['links'][0]['detail'])
    assert detail['checks']['suite:github-actions#2'] == 'failing'
    # A re-run on the same commit keeps its suite id; its runs reopen the suite until it completes again.
    _arm(api, task['id'])
    before = _burst(api, task['id'])
    run('build', 'in_progress')
    run('deploy', 'queued')
    run('build', 'completed', 'success')
    run('deploy', 'completed', 'success')
    assert _burst(api, task['id']) == before
    _suite(api, 'github-actions', 'completed', 'success', id=2)
    assert _burst(api, task['id']) == [
        f"Checks finished on tico#412: failed (github-actions) {PR}/checks at {'a' * 12}"]


def test_wake_once_on_a_review_a_ship_or_a_merge_that_cannot_ship(api):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on review', 'body': 'x', 'links': [PR]})
    _arm(api, task['id'])
    review = {'action': 'submitted', 'pull_request': {'html_url': PR}, 'repository': {'full_name': 'ticoteam/tico'},
              'review': {'id': 7, 'state': 'approved', 'user': {'login': 'ana'},
                         'html_url': PR + '#pullrequestreview-7'}}
    hook(api, 'pull_request_review', review)
    hook(api, 'pull_request_review', {**review, 'review': {**review['review'], 'id': 8}})   # disarmed: quiet
    assert _burst(api, task['id']) == [f'Review approved on tico#412 by ana {PR}#pullrequestreview-7']
    # Armed again: a merge in the release repository waits for the release that ships it.
    assert 'wake_once' not in json.loads(_arm(api, task['id'], armed=False)[0]['detail'])
    shipping = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on the release', 'body': 'x', 'links': [PR]})
    late = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on the release too', 'body': 'x', 'links': [PR]})
    _arm(api, shipping['id'])
    hook(api, 'pull_request', pr_event('closed', merged=True, merge_commit_sha='b' * 40))
    assert _burst(api, shipping['id']) == []
    # Merged in the release repository and waiting to ship: arming it now is still allowed.
    assert json.loads(_arm(api, late['id'])[0]['detail'])['wake_once']['by'] == 'human:ana'
    api.app.state.store.settings.release_commit = 'b' * 40
    with api.app.state.store.transaction() as c:
        assert shipping['id'] in G.ship_deployed(c, api.app.state.store.settings)
        assert G.ship_deployed(c, api.app.state.store.settings) == []
        assert H.task(c, shipping['id'])['status'] == shipping['status']
    assert _burst(api, shipping['id']) == [f"Shipped: tico#412 is in the running release {'b' * 12} {PR}"]
    assert _burst(api, late['id']) == _burst(api, shipping['id'])
    # A merge anywhere else never ships, so it is the last result.
    other = 'https://github.com/ticoteam/site/pull/9'
    elsewhere = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on the site', 'body': 'x', 'links': [other]})
    _arm(api, elsewhere['id'], url=other)
    hook(api, 'pull_request', pr_event('closed', html_url=other, merged=True, merge_commit_sha='c' * 40))
    assert _burst(api, elsewhere['id']) == [
        f"Merged: site#9; it is not in the repository Tico releases, so no shipped wake follows {other}"]
    # Arming it again has nothing left to wake on, and neither has a shipped PR.
    r = api.post('/api/v2/tasks/' + elsewhere['id'] + '/links', json={'url': other, 'wake_once': True}, headers=headers())
    assert r.status_code >= 400 and 'no result is left' in r.text
    r = api.post('/api/v2/tasks/' + shipping['id'] + '/links', json={'url': PR, 'wake_once': True}, headers=headers())
    assert r.status_code >= 400 and 'shipped' in r.text


def test_a_pr_linked_again_in_another_case_is_the_same_link(api):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on CI', 'body': 'x', 'links': [PR]})
    for url in ('https://github.com/TicoTeam/Tico/pull/412', PR + '/'):
        links = _arm(api, task['id'], url=url)
        assert [link['url'] for link in links] == [PR]
        assert json.loads(links[0]['detail'])['wake_once']['by'] == 'human:ana'
        links = _arm(api, task['id'], armed=False, url=url)
        assert len(links) == 1 and 'wake_once' not in json.loads(links[0]['detail'])
    mixed = 'https://github.com/TICOTEAM/TICO/pull/412'
    other = post(api, 'tasks', {'owner': 'cpo', 'title': 'Another task on it', 'body': 'x', 'links': [mixed]})
    assert [link['url'] for link in _arm(api, other['id'])] == [mixed]     # stored mixed case, linked lowercase
    # A duplicate left by an older install is still disarmed through its own URL.
    with api.app.state.store.transaction() as c:
        H.task_link(c, 'human:ana', task['id'], PR, wake_once=True)
        c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,number,detail) "
                  "VALUES('dup',?,'pr',?,'tico#412','open','human:ana',?,'TicoTeam/Tico',412,?)",
                  (task['id'], 'https://github.com/TicoTeam/Tico/pull/412', H.now(), json.dumps({'wake_once': {'by': 'human:ana'}})))
    links = {link['id']: link for link in _arm(api, task['id'], armed=False, url='https://github.com/TicoTeam/Tico/pull/412')}
    assert 'wake_once' not in json.loads(links['dup']['detail'])
    assert 'wake_once' in json.loads(next(v for k, v in links.items() if k != 'dup')['detail'])


def test_wake_once_needs_the_app_and_waits_only_on_a_pr_github_reports(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on GitHub', 'body': 'x', 'links': [PR]})
    post(api, 'tasks/' + task['id'] + '/links', {'url': PR, 'wake_once': True}, expected=422)   # no App
    _app(api)
    post(api, 'tasks/' + task['id'] + '/links', {'url': 'https://example.com/deploy', 'wake_once': True}, expected=422)
    _arm(api, task['id'])
    with api.app.state.store.transaction() as c:
        assert H.waiting_for(c, H.task(c, task['id'])) is None      # nothing from GitHub on it yet
    hook(api, 'pull_request', pr_event('opened'))
    with api.app.state.store.transaction() as c:
        assert H.waiting_for(c, H.task(c, task['id'])) == 'GitHub on tico#412'
        H.task_update(c, 'bot:cpo', task['id'], status='waiting', note='Waiting on CI for tico#412')
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), hours=-3), task['id']))
        assert H.sweep_stranded(c) == []
    hook(api, 'pull_request', pr_event('closed'))             # a closed PR has nothing left to wait for
    with api.app.state.store.transaction() as c:
        assert H.waiting_for(c, H.task(c, task['id'])) is None


class FakeHistory:
    """A small repository GitHub answers for: each commit is its parents, its whole tree and the renames it made."""

    def __init__(self):
        self.commits, self.blobs, self.calls = {}, {}, []

    def commit(self, name, parents, tree, renames=None):
        sha = hashlib.sha1(name.encode()).hexdigest()
        for text in tree.values():
            self.blobs[hashlib.sha1(text.encode()).hexdigest()] = text
        self.commits[sha] = (list(parents), dict(tree), dict(renames or {}))
        return sha

    def tree(self, sha, **changes):
        """A commit's tree with paths changed (a value) or deleted (None); `__` in a name stands for `/`."""
        tree = dict(self.commits[sha][1])
        for path, text in changes.items():
            path = path.replace("__", "/").replace("_dot_", ".")
            tree.pop(path) if text is None else tree.__setitem__(path, text)
        return tree

    def ancestors(self, sha):
        seen, todo = set(), [sha]
        while todo:
            if (one := todo.pop()) not in seen:
                seen.add(one)
                todo += self.commits[one][0]
        return seen

    def files(self, old, new, renames=()):
        import difflib
        def entry(before, after, path, **extra):
            patch = "\n".join(list(difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(),
                                                         lineterm=""))[2:])
            return {"filename": path, "patch": patch, **extra,
                    "sha": hashlib.sha1((after if after is not None else before).encode()).hexdigest()}
        out = [entry(old[was], new[path], path, status="renamed", previous_filename=was)
               for path, was in dict(renames).items()]
        for path in sorted(set(old) | set(new)):
            if old.get(path) == new.get(path) or path in dict(renames) or path in dict(renames).values():
                continue
            status = "added" if path not in old else "removed" if path not in new else "modified"
            out.append(entry(old.get(path), new.get(path), path, status=status))
        return out

    def call(self, method, path, params=None, headers=None):
        import base64
        from urllib.parse import unquote
        assert method == "GET" and headers == {"Authorization": "Bearer test-token"}
        self.calls.append(path)
        rest = path.removeprefix("/repos/ticoteam/tico/")
        reply = lambda body, status=200: SimpleNamespace(status_code=status, json=lambda: body)
        kind, _, arg = rest.partition("/")
        if kind == "compare":
            base, head = arg.split("...")
            ahead = base in self.ancestors(head)
            return reply({"status": "identical" if base == head else "ahead" if ahead else "diverged",
                          "base_commit": {"sha": base}, "merge_base_commit": {"sha": base if ahead else None},
                          "files": self.files(self.commits[base][1], self.commits[head][1])})
        if kind == "commits":
            parents, tree, renames = self.commits[arg]
            return reply({"parents": [{"sha": p} for p in parents],
                          "files": self.files(self.commits[parents[0]][1] if parents else {}, tree, renames)})
        if kind == "contents":
            text = self.commits[params["ref"]][1].get(unquote(arg))
            return reply({"sha": hashlib.sha1(text.encode()).hexdigest()} if text is not None else {},
                         200 if text is not None else 404)
        if kind == "git" and arg.startswith("blobs/"):
            return reply({"content": base64.b64encode(self.blobs[arg[6:]].encode()).decode(), "encoding": "base64"})
        raise AssertionError(path)


def _refresh_release(api, monkeypatch, history, release):
    import threading
    service = api.app.state.github_app
    history.calls = []
    monkeypatch.setattr(service, '_call', history.call)
    monkeypatch.setattr(service, 'mint', lambda *args, **kw: ('test-token', 'later'))
    api.app.state.store.settings.release_commit = release
    service.deploy_refresh_next = 0
    G.refresh_deployed_tasks(service)
    for worker in threading.enumerate():
        if worker.name == 'tico-deploy-refresh':
            worker.join(5)
    assert not service.deploy_refresh_running


def test_a_release_that_reverts_a_merged_pr_does_not_ship_it(api, monkeypatch):
    """Dev 127: #818 merged, the release branch reverted it after QA failed, and the release was tagged on that revert.
    Ancestry says the merge is in the release; its change is not, so no Shipped notice and the ticket stays put."""
    _app(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at) VALUES(?,1,1,?,?)",
                  ('ticoteam/tico', 'human:ana', H.now()))
    typ = post(api, 'task-types', {'name': 'Dev', 'steps': [
        {'name': 'Pending Release', 'status': 'review'}, {'name': 'Post Release QA', 'status': 'review'}]})['type']
    tasks = {}
    for n in range(412, 418):
        url = PR.replace('/412', f'/{n}')
        tasks[n] = post(api, 'tasks', {'owner': 'cpo', 'title': f'Change {n}', 'body': 'x', 'links': [url],
                                       'type': typ['id'], 'step': 'Pending Release'})
        _arm(api, tasks[n]['id'], url=url)

    code = "export function refresh() {\n  poll(120000);\n}\n"
    live = "export function refresh() {\n  poll(120000);\n  onStatusEvent(event => moveDesk(event.bot));\n}\n"
    audit = "def a():\n    audit_event()\n    return 1\n\n\ndef b():\n    return 2\n"
    h = FakeHistory()
    base = h.commit('base', [], {'ui/app/refresh.js': code, 'CHANGELOG.md': '## [Unreleased]\n',
                                 'ui/app/pricing.js': 'price = 10\n', 'app/flag.py': 'x=1\n',
                                 'app/loop.py': 'for item in items:\n    work(item)\ndone()\n',
                                 'app/audit.py': audit, 'app/old.py': 'def price():\n    return 10\n'})
    merges = {}
    def merge(n, parent, **changes):
        merges[n] = h.commit(f'#{n}', [parent], h.tree(parent, **changes), changes.pop('renames', None))
        hook(api, 'pull_request', pr_event('closed', html_url=PR.replace('/412', f'/{n}'), merged=True,
                                           merge_commit_sha=merges[n]))
        return merges[n]
    # 412 the live desks (product, test, notes); 413 kept; 414 a two-character edit; 415 indentation only;
    # 416 adds a line that already exists elsewhere in the file; 417 renames a file and edits it.
    last = merge(412, base, ui__app__refresh_dot_js=live, CHANGELOG_dot_md='## [Unreleased]\n- Desks move live\n',
                 ui__tests__overview_live_dot_cjs='assert desk moved at once\n')
    last = merge(413, last, ui__app__pricing_dot_js='price = 12 per seat\n')
    last = merge(414, last, app__flag_dot_py='x=2\n')
    last = merge(415, last, app__loop_dot_py='for item in items:\n    work(item)\n    done()\n')
    last = merge(416, last, app__audit_dot_py=audit.replace('    return 2', '    audit_event()\n    return 2'))
    merges[417] = last = h.commit('#417', [last], h.tree(last, app__old_dot_py=None,
                                  app__new_dot_py='def price():\n    return 20\n'), {'app/new.py': 'app/old.py'})
    hook(api, 'pull_request', pr_event('closed', html_url=PR.replace('/412', '/417'), merged=True, merge_commit_sha=last))
    # The cut moves the notes and drops 416's pre-existing call, keeping the one 416 added.
    cut = h.commit('cut', [last], h.tree(last, CHANGELOG_dot_md='## [0.3.55]\n- Desks move live\n',
                                         app__audit_dot_py=audit.replace('    audit_event()\n    return 1', '    return 1')
                                         .replace('    return 2', '    audit_event()\n    return 2')))
    revert = h.commit('revert', [cut], h.tree(cut, ui__app__refresh_dot_js=code, CHANGELOG_dot_md='## [0.3.55]\n',
                                              ui__tests__overview_live_dot_cjs=None, app__flag_dot_py='x=1\n',
                                              app__loop_dot_py='for item in items:\n    work(item)\ndone()\n',
                                              app__new_dot_py=None, app__old_dot_py='def price():\n    return 10\n'))
    # main's push records put every merge before the release: push order alone would ship them all
    hook(api, 'push', {'ref': 'refs/heads/main', 'repository': {'full_name': 'ticoteam/tico', 'default_branch': 'main'},
                       'commits': [{'id': sha} for sha in [*merges.values(), cut, revert]]})
    api.app.state.store.settings.release_commit = revert
    with api.app.state.store.transaction() as c:
        assert G.ship_deployed(c, api.app.state.store.settings) == []

    _refresh_release(api, monkeypatch, h, revert)
    shipped = {413, 416}
    with api.app.state.store.read() as c:
        for n, task in tasks.items():
            assert [l['state'] for l in H.task_links(c, task['id'])] == ['shipped' if n in shipped else 'merged'], n
        assert sorted(r['target'] for r in c.execute("SELECT target FROM events WHERE action='github.shipped'")) == \
            sorted(tasks[n]['id'] for n in shipped)
    for n, task in tasks.items():
        url = PR.replace('/412', f'/{n}')
        assert _burst(api, task['id']) == ([f"Shipped: tico#{n} is in the running release {revert[:12]} {url}"]
                                           if n in shipped else []), n
        assert get(api, 'tasks/' + task['id'])['task']['step_id'] == task['step_id']
    # A revert is fixed history: the next pass asks GitHub nothing more about it.
    _refresh_release(api, monkeypatch, h, revert)
    assert h.calls == []

    # Re-applied in a later release, the same pull request ships then.
    again = h.commit('reapply #412', [revert], h.tree(revert, ui__app__refresh_dot_js=live))
    _refresh_release(api, monkeypatch, h, again)
    assert _burst(api, tasks[412]['id']) == [f"Shipped: tico#412 is in the running release {again[:12]} {PR}"]


def test_change_kept_reads_a_merge_commit_against_main_and_ignores_cut_tests():
    h = FakeHistory()
    get = lambda path, **params: G._read(SimpleNamespace(_call=h.call), 'test-token', path, params)
    code = "def price():\n    return 10\n"
    base = h.commit('base', [], {'app/price.py': code, 'docs/a.md': 'Plain words here\n'})
    branch = h.commit('branch', [base], {'app/price.py': code + "    # per seat from now on\n",
                                         'app/tests/test_price.py': 'assert price() == 10\n', 'docs/a.md': 'Plain words here\n'})
    side = h.commit('side', [base], {**h.commits[base][1], 'docs/a.md': 'Other words entirely\n'})
    merge = h.commit('merge', [side, branch], {**h.commits[branch][1], 'docs/a.md': 'Other words entirely\n'})
    # A release that cuts the pull request's tests still ships its product change.
    cut = h.commit('cut tests', [merge], {k: v for k, v in h.commits[merge][1].items() if 'tests/' not in k})
    assert G.change_kept(get, 'ticoteam/tico', merge, cut) is True
    # Reverting the merge commit (against main, its first parent) undoes it; the other side's change stays.
    revert = h.commit('revert', [cut], {**h.commits[cut][1], 'app/price.py': code})
    assert G.change_kept(get, 'ticoteam/tico', merge, revert) is False


def _worktree(api, task_id, repo, branch, state='present', **detail):
    with api.app.state.store.transaction() as c:
        link = uuid.uuid4().hex
        c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,branch,path,updated_at,"
                  "detail) VALUES(?,?,'worktree',?,?,?,'bot:cpo',?,?,?,?,?,?)",
                  (link, task_id, 'worktree:' + link, repo, state, H.now(), repo, branch,
                   f'tasks/{task_id[:8]}/{repo.replace("/", "__")}', H.now(), json.dumps({'owner': 'bot:cpo', **detail})))
    return link


def _branch_pr(number, branch, repo='ticoteam/tico', head_repo=None, **over):
    return {'html_url': f'https://github.com/{repo}/pull/{number}', 'number': number, 'state': 'open', 'draft': False,
            'merged': False, 'merge_commit_sha': None, 'merged_at': None,
            'head': {'ref': branch, 'sha': 'b' * 40, 'repo': {'full_name': head_repo or repo}}, **over}


def _prs(api, task_id):
    """The task's PR links as stored; a task GET would start its own GitHub refresh."""
    with api.app.state.store.read() as c:
        return {l['number']: {**l, 'detail': json.loads(l['detail'] or '{}')} for l in H._rows(c.execute(
            "SELECT * FROM task_links WHERE task_id=? AND kind='pr'", (task_id,)))}


def _worktree_detail(api, link):
    with api.app.state.store.read() as c:
        return json.loads(c.execute("SELECT detail FROM task_links WHERE id=?", (link,)).fetchone()[0])


def _reconciler(api, monkeypatch, fetch, mint=None):
    """The background pass against a fake GitHub; `run()` makes one pass and waits for it."""
    import threading
    service = api.app.state.github_app
    with service.store.transaction() as c:
        for repo in ('ticoteam/tico', 'ticoteam/tico-services'):
            c.execute("INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at,default_branch) "
                      "VALUES(?,1,1,?,?,'main')", (repo, 'human:ana', H.now()))
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


def test_opened_non_engineering_pr_does_not_need_creation_qa(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Planning notes', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    assert not _prs(api, task['id'])[412]['detail'].get('qa_default_pending')


def test_stale_non_engineering_qa_marker_settles_without_repeated_github_reads(api, monkeypatch):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Planning notes', 'body': 'x', 'links': [PR]})
    # Existing links can carry the marker written by the old opened-webhook path.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail=? WHERE task_id=? AND kind='pr'",
                  (json.dumps({'qa_default_pending': True, 'kept': 'metadata'}), task['id']))
    calls = []

    def fetch(method, path, **kw):
        calls.append(path)
        assert path == '/repos/ticoteam/tico/pulls/412'
        return SimpleNamespace(status_code=200, json=lambda: _branch_pr(412, 'planning-notes'))

    service, run = _reconciler(api, monkeypatch, fetch)
    run(fresh=True)
    detail = _prs(api, task['id'])[412]['detail']
    assert not detail.get('qa_default_pending')
    assert detail['kept'] == 'metadata'
    assert calls == ['/repos/ticoteam/tico/pulls/412']
    # Ignore both caches: the settled link itself must exclude the next pass.
    run(fresh=True)
    assert calls == ['/repos/ticoteam/tico/pulls/412']
    with service.store.read() as c:
        assert H.task_labels(H.task(c, task['id'])) == []


def test_a_pr_from_a_worktree_branch_registers_itself_on_both_repos_and_never_from_a_fork(api):
    """A bot that opens a PR and never links it still gets the link its worktree's release waits on."""
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Gateway key per install', 'body': 'x'})
    branch = f"tico/{task['id'][:8]}-gateway-key-per-install"
    _worktree(api, task['id'], 'ticoteam/tico', branch)
    _worktree(api, task['id'], 'ticoteam/tico-services', branch)
    _worktree(api, task['id'], 'otherorg/tico', branch)
    other = post(api, 'tasks', {'owner': 'cpo', 'title': 'Unrelated', 'body': 'x'})
    _worktree(api, other['id'], 'ticoteam/tico', f"tico/{other['id'][:8]}-unrelated")

    # Never from a fork, a head repository GitHub does not name, or another organization than the App's.
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(984, branch, head_repo='someone/tico')})
    gone = _branch_pr(984, branch)
    gone['head']['repo'] = None
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': gone})
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(5, branch, 'otherorg/tico')})
    assert _prs(api, task['id']) == {}
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(984, branch)})
    hook(api, 'pull_request', {'action': 'closed', 'pull_request': _branch_pr(
        16, branch, 'ticoteam/tico-services', state='closed', merged=True, merge_commit_sha='c' * 40)})
    prs = {(l['repo'], l['number']): l for l in _prs(api, task['id']).values()}
    assert set(prs) == {('ticoteam/tico', 984), ('ticoteam/tico-services', 16)}
    assert prs[('ticoteam/tico', 984)]['state'] == 'open' and prs[('ticoteam/tico-services', 16)]['state'] == 'merged'
    assert all(l['added_by'] == 'keeper' and l['branch'] == branch for l in prs.values())
    assert _prs(api, other['id']) == {}
    # Registered once: later events on the same PR update that link, and registering wakes nobody.
    hook(api, 'pull_request', {'action': 'synchronize', 'pull_request': _branch_pr(984, branch)})
    assert len(_prs(api, task['id'])) == 2
    assert _burst(api, task['id']) == []


def test_reconcile_heals_stale_and_unregistered_prs_on_a_declined_task_nobody_reads(api, monkeypatch):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Org chart order', 'body': 'x',
                               'links': ['https://github.com/ticoteam/tico/pull/746']})
    branch = f"tico/{task['id'][:8]}-org-chart-order"
    _worktree(api, task['id'], 'ticoteam/tico', branch)
    _worktree(api, task['id'], 'ticoteam/tico', 'tico/' + task['id'][:8] + '-fix-forward')
    pushed = 'tico/' + task['id'][:8] + '-first-push'
    _worktree(api, task['id'], 'ticoteam/tico', pushed)
    # The first push asks for a draft PR; the push itself (and the checks it starts) is never held back.
    assert hook(api, 'push', {'ref': 'refs/heads/' + pushed, 'repository': {'full_name': 'ticoteam/tico'},
                              'after': 'f' * 40, 'head_commit': {'id': 'f' * 40}})['commits'] == 0
    calls = []
    def fetch(method, path, headers=None, params=None, json=None):
        calls.append((method, path))
        if method == 'POST':
            assert json['head'] == pushed and json['draft'] is True and json['title'] == 'Org chart order'
            return SimpleNamespace(status_code=201, json=lambda: _branch_pr(991, pushed, draft=True))
        if path.endswith('/pulls/746'):
            return SimpleNamespace(status_code=200, json=lambda: _branch_pr(746, branch, state='closed', merged=True,
                                                                             merge_commit_sha='d' * 40))
        if '/compare/' in path:
            # Only the pushed branch has commits of its own; a branch nobody pushed is not on GitHub.
            return (SimpleNamespace(status_code=200, json=lambda: {'ahead_by': 1}) if path.endswith(quote(pushed, safe=''))
                    else SimpleNamespace(status_code=404, json=lambda: {}))
        if params and params['head'] == 'ticoteam:tico/' + task['id'][:8] + '-fix-forward':
            return SimpleNamespace(status_code=200, json=lambda: [_branch_pr(990, params['head'].split(':')[1])])
        return SimpleNamespace(status_code=200, json=lambda: [])
    service, run = _reconciler(api, monkeypatch, fetch)
    with service.store.transaction() as c:
        c.execute("UPDATE tasks SET status='declined' WHERE id=?", (task['id'],))
    run()
    prs = _prs(api, task['id'])
    assert prs[746]['state'] == 'merged' and prs[990]['state'] == 'open' and prs[990]['added_by'] == 'keeper'
    assert prs[991]['state'] == 'draft' and prs[991]['branch'] == pushed
    assert [c for c in calls if c[0] == 'POST'] == [('POST', '/repos/ticoteam/tico/pulls')]
    # Bounded: a second pass inside three minutes reads nothing.
    before = len(calls)
    run()
    assert len(calls) == before and not service.worktree_reconcile_running


def test_reconcile_shares_its_reads_so_a_branch_behind_twenty_open_prs_still_gets_its_draft(api, monkeypatch):
    """Twenty open PRs never take every read: a worktree branch whose push webhook was missed is found on GitHub
    and drafted in the same pass, a never-pushed one gets nothing, and each is asked again on its own schedule."""
    _app(api)
    busy = post(api, 'tasks', {'owner': 'cpo', 'title': 'Busy', 'body': 'x',
                               'links': [f'https://github.com/ticoteam/tico/pull/{n}' for n in range(100, 120)]})
    _worktree(api, busy['id'], 'ticoteam/tico', f"tico/{busy['id'][:8]}-busy")
    missed = post(api, 'tasks', {'owner': 'cpo', 'title': 'Missed push', 'body': 'x'})
    branch = f"tico/{missed['id'][:8]}-missed-push"
    # Only the computer's report says the work is on GitHub: a new head with nothing unpushed.
    tree = _worktree(api, missed['id'], 'ticoteam/tico', branch, expected_head='a' * 40, last_commit='e' * 40, ahead=0)
    idle = post(api, 'tasks', {'owner': 'cpo', 'title': 'Never pushed', 'body': 'x'})
    _worktree(api, idle['id'], 'ticoteam/tico', f"tico/{idle['id'][:8]}-never-pushed")
    calls = []
    def fetch(method, path, headers=None, params=None, json=None):
        calls.append((method, path))
        if method == 'POST':
            assert json['head'] == branch
            return SimpleNamespace(status_code=201, json=lambda: _branch_pr(200, branch, draft=True))
        if '/compare/' in path:
            return (SimpleNamespace(status_code=200, json=lambda: {'ahead_by': 2}) if path.endswith(quote(branch, safe=''))
                    else SimpleNamespace(status_code=404, json=lambda: {}))
        if '/pulls/' in path:
            return SimpleNamespace(status_code=200, json=lambda: _branch_pr(int(path.rsplit('/', 1)[1]), 'feature'))
        return SimpleNamespace(status_code=200, json=lambda: [])
    service, run = _reconciler(api, monkeypatch, fetch)
    with service.store.transaction() as c:
        c.execute("UPDATE task_links SET updated_at=? WHERE task_id=? AND kind='pr'", ('2026-01-01T00:00:00.000000Z', busy['id']))
    run()
    assert _prs(api, missed['id'])[200]['state'] == 'draft' and _prs(api, idle['id']) == {}
    assert [c for c in calls if c[0] == 'POST'] == [('POST', '/repos/ticoteam/tico/pulls')]
    read = [int(p.rsplit('/', 1)[1]) for m, p in calls if '/pulls/' in p]
    assert len(read) == 17 and _worktree_detail(api, tree)['remote']['ahead'] == 2
    cache = service.worktree_reconcile_cache
    schedule = {'pr' if key[0] == 'pr' else key[2].split('-', 1)[1]: round(e['next'] - e['last']) for key, e in cache.items()}
    assert schedule == {'pr': 180, 'busy': 900, 'never-pushed': 900, 'missed-push': 180}
    # The next pass reads the three PRs the first one had no room for and the new draft, nothing it read already.
    calls.clear()
    service.worktree_reconcile_next = 0
    run()
    assert sorted(int(p.rsplit('/', 1)[1]) for m, p in calls if '/pulls/' in p) == sorted(set(range(100, 120)) - set(read)) + [200]


def test_a_webhook_during_branch_discovery_keeps_its_newer_facts(api, monkeypatch):
    """A webhook that registers and merges the PR while the pass is still reading the branch wins over the older
    open state the read brings back."""
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Race', 'body': 'x'})
    branch = f"tico/{task['id'][:8]}-race"
    _worktree(api, task['id'], 'ticoteam/tico', branch)
    other = post(api, 'tasks', {'owner': 'cpo', 'title': 'Same branch', 'body': 'x'})
    _worktree(api, other['id'], 'ticoteam/tico', branch)
    def fetch(method, path, headers=None, params=None, json=None):
        assert method == 'GET' and params['head'] == 'ticoteam:' + branch
        with api.app.state.store.transaction() as c:
            G.pull_request(c, {'action': 'closed', 'pull_request': _branch_pr(
                7, branch, state='closed', merged=True, merge_commit_sha='c' * 40, head={
                    'ref': branch, 'sha': 'n' * 40, 'repo': {'full_name': 'ticoteam/tico'}})})
        return SimpleNamespace(status_code=200, json=lambda: [_branch_pr(7, branch)])
    _, run = _reconciler(api, monkeypatch, fetch)
    run()
    for task_id in (task['id'], other['id']):
        link = _prs(api, task_id)[7]
        assert link['state'] == 'merged' and link['detail']['head_sha'] == 'n' * 40


def test_draft_failures_stay_visible_until_the_branch_pr_is_linked(api, monkeypatch):
    """A refused token, no answer, and a draft opened but not linked each leave a reason on the tree, and finding
    the PR by any path clears it. Synthetic: no real GitHub call."""
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Faults', 'body': 'x'})
    branch = f"tico/{task['id'][:8]}-faults"
    tree = _worktree(api, task['id'], 'ticoteam/tico', branch)
    hook(api, 'push', {'ref': 'refs/heads/' + branch, 'repository': {'full_name': 'ticoteam/tico'},
                       'after': 'f' * 40, 'head_commit': {'id': 'f' * 40}})
    fault = {'mint': True, 'post': None, 'listed': []}
    def mint(repos, permissions):
        if permissions.get('pull_requests') == 'write' and fault['mint']:
            raise RuntimeError('permission refused')
        return 'test-token', 'later'
    def fetch(method, path, headers=None, params=None, json=None):
        if method == 'POST':
            if fault['post']:
                raise fault['post']
            return SimpleNamespace(status_code=201, json=lambda: _branch_pr(8, branch, draft=True))
        if '/compare/' in path:
            return SimpleNamespace(status_code=200, json=lambda: {'ahead_by': 1})
        return SimpleNamespace(status_code=200, json=lambda: fault['listed'])
    _, run = _reconciler(api, monkeypatch, fetch, mint)
    run()
    assert _worktree_detail(api, tree)['pr_error'].endswith('failed (no GitHub token for it)')
    fault.update(mint=False, post=ConnectionError('reset'))
    run(fresh=True)
    assert _worktree_detail(api, tree)['pr_error'].endswith('failed (GitHub did not answer)')
    fault['post'] = None
    real = G.pull_request
    monkeypatch.setattr(G, 'pull_request', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('database locked')))
    run(fresh=True)
    assert 'linking it to the task failed' in _worktree_detail(api, tree)['pr_error'] and _prs(api, task['id']) == {}
    monkeypatch.setattr(G, 'pull_request', real)
    fault['listed'] = [_branch_pr(8, branch, draft=True)]
    run(fresh=True)
    assert _prs(api, task['id'])[8]['state'] == 'draft' and 'pr_error' not in _worktree_detail(api, tree)


def test_a_hand_off_refuses_a_pushed_worktree_branch_no_pr_carries_until_it_is_linked(api):
    """Any move to a review step, or to done, with pushed work no pull request carries would orphan the worktree; a
    link, by hand or by the branch's own PR, lets it through. A branch only moved to newer main is no such work."""
    _app(api)
    with api.app.state.store.transaction() as c:
        dev = H.type_create(c, H.KEEPER, 'Dev tickets', [
            {'name': 'In Progress', 'status': 'doing'},
            {'name': 'Review 1', 'status': 'review'}, {'name': 'Review 2', 'status': 'review'},
            {'name': 'Done', 'status': 'done'}])
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Gateway key per install', 'body': 'x'})
    branch = f"tico/{task['id'][:8]}-gateway-key-per-install"
    repos = ('ticoteam/tico', 'ticoteam/tico-services')
    for repo in repos:
        _worktree(api, task['id'], repo, branch, expected_head='a' * 40)

    def move(**change):
        try:
            with api.app.state.store.transaction() as c:
                return H.task_update(c, 'human:ana', task['id'], **change)['status']
        except H.Refused as refused:
            return str(refused)
    assert move(type=dev['id'], step='In Progress') == 'doing'
    # Moved to newer main and nothing more: no commit of the task's own, so nothing to refuse.
    with api.app.state.store.transaction() as c:
        for repo in repos:
            c.execute("INSERT INTO main_pushes(sha,repo,pushed_at) VALUES(?,?,?)", (repo[-1] * 40, repo, H.now()))
            c.execute("UPDATE task_links SET detail=json_set(detail,'$.last_commit',?,'$.ahead',0) "
                      "WHERE task_id=? AND kind='worktree' AND repo=?", (repo[-1] * 40, task['id'], repo))
    assert move(step='Review 1') == 'review'
    # A push counts the moment its webhook lands, before any computer reports it, in both repositories.
    for repo in repos:
        hook(api, 'push', {'ref': 'refs/heads/' + branch, 'repository': {'full_name': repo},
                           'after': 'e' * 40, 'head_commit': {'id': 'e' * 40}})
    refused = move(step='Review 2')
    assert all(f'{repo} branch {branch}' in refused for repo in repos)
    assert move(note='still working') == 'review'
    assert move(status='waiting') == 'waiting'
    assert 'ticoteam/tico branch' in move(step='Review 1')
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(984, branch)})
    still = move(step='Done')
    assert 'ticoteam/tico-services branch' in still and 'ticoteam/tico branch' not in still
    post(api, 'tasks/' + task['id'] + '/links', {'url': 'https://github.com/ticoteam/tico-services/pull/16'})
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(16, branch, repo='ticoteam/tico-services')})
    # Linking is necessary; the review gate also needs a current passing receipt.
    assert move(note='Tests: head ' + 'b' * 40 + ': 9 passed, 0 failed') == 'waiting'
    assert move(step='Review 2') == 'review' and move(step='Done') == 'done'


def test_a_hand_off_waits_for_a_branch_check_when_a_push_left_no_webhook_and_no_report(api, monkeypatch):
    """A push GitHub never announced and no computer reported is on GitHub only: until the background pass has read
    the branch since its last known head, a hand-off is held; the pass drafts the PR (or finds nothing) and frees
    it. A head reported while unpushed is read again once it is reported pushed. Synthetic: no real GitHub call."""
    _app(api)
    pushed = post(api, 'tasks', {'owner': 'cpo', 'title': 'Silent push', 'body': 'x'})
    branch = f"tico/{pushed['id'][:8]}-silent-push"
    _worktree(api, pushed['id'], 'ticoteam/tico', branch, expected_head='a' * 40)
    quiet = post(api, 'tasks', {'owner': 'cpo', 'title': 'Quiet', 'body': 'x'})
    local = _worktree(api, quiet['id'], 'ticoteam/tico', f"tico/{quiet['id'][:8]}-quiet", expected_head='a' * 40,
                      last_commit='e' * 40, ahead=1)
    def fetch(method, path, headers=None, params=None, json=None):
        if method == 'POST':
            return SimpleNamespace(status_code=201, json=lambda: _branch_pr(31, branch, draft=True))
        if '/compare/' in path:
            # Only the silent push's branch is on GitHub; the quiet one has its commit on the computer only.
            return (SimpleNamespace(status_code=200, json=lambda: {'ahead_by': 1}) if path.endswith(quote(branch, safe=''))
                    else SimpleNamespace(status_code=404, json=lambda: {}))
        return SimpleNamespace(status_code=200, json=lambda: [])
    _, run = _reconciler(api, monkeypatch, fetch)

    def review(task):
        try:
            with api.app.state.store.transaction() as c:
                return H.task_update(c, 'human:ana', task['id'], status='review')['status']
        except H.Refused as refused:
            return str(refused)
    assert all('not checked on GitHub' in review(task) for task in (pushed, quiet))
    run()
    assert _prs(api, pushed['id'])[31]['state'] == 'draft' and review(pushed) == 'review'
    assert _worktree_detail(api, local)['remote']['absent'] and review(quiet) == 'review'
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='doing' WHERE id=?", (quiet['id'],))
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.ahead',0) WHERE id=?", (local,))
    assert 'has commits with no pull request linked' in review(quiet)
