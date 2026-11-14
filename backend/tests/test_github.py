"""The repository webhook keeps a task's pull request links current; it never moves the task."""

import hashlib
import json
import uuid
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend import github as G
from backend import hubdb as H
from backend.app import create_app
from backend.config import Settings
from backend.store import encode
from backend.tests.helpers.github import (
    PR,
    _app,
    _branch_pr,
    _burst,
    _prs,
    _reconciler,
    _status,
    _suite,
    api,
    get,
    headers,
    hook,
    post,
    pr_event,
)


def merged(title, url):
    """The one wake a merge gives the bot whose open task has the pull request (github._merge_wakes)."""
    return f"{title} merged: {url}. Carry this task on: say it is done, or close it."


def test_a_bad_signature_is_refused_and_an_unset_secret_hides_the_path(api, tmp_path):
    hook(api, "ping", {"zen": "x"}, secret="wrong", expected=403)
    assert hook(api, "ping", {"zen": "x"}) == {"ok": True}
    bare = create_app(Settings(db_path=tmp_path / "other.db"))
    with TestClient(bare) as client:
        r = client.post(G.PATH, content=b"{}", headers={"X-Hub-Signature-256": "sha256=00", "X-GitHub-Event": "ping"})
        assert r.status_code == 404


def test_a_pull_request_never_moves_its_task_through_merge_and_deploy(api):
    """A PR on a task is information: opened, merged and deployed, the task keeps its status and stage."""
    plain = post(api, "tasks", {"owner": "cpo", "title": "Ship the pricing page", "body": "x", "links": [PR]})
    custom = post(api, "tasks", {"owner": "cpo", "title": "Ship the pricing copy", "body": "x", "links": [PR]})
    with api.app.state.store.transaction() as c:  # a legacy product-lane row used to move; nothing does now
        c.execute("UPDATE tasks SET lane='product' WHERE id=?", (plain["id"],))
    before = {t["id"]: (t["status"], t["stage"]["id"]) for t in (plain, custom)}

    def unmoved():
        for tid, (status, stage) in before.items():
            task = get(api, "tasks/" + tid)["task"]
            assert (task["status"], task["stage"]["id"]) == (status, stage)
        return [get(api, "tasks/" + tid)["task"]["links"][0] for tid in before]

    opened = hook(api, "pull_request", pr_event("opened"))
    assert "moved" not in opened and all(l["state"] == "open" for l in unmoved())
    hook(
        api,
        "pull_request",
        pr_event("closed", merged=True, merge_commit_sha="abc123", merged_at="2026-09-18T20:00:00Z"),
    )
    assert all(l["state"] == "merged" and l["pr_sha"] == "abc123" for l in unmoved())

    # main moves on: the merge commit, then a later commit that the next release is built from
    pushed = hook(
        api,
        "push",
        {
            "ref": "refs/heads/main",
            "repository": {"full_name": "ticoteam/tico", "default_branch": "main"},
            "commits": [{"id": "abc123"}, {"id": "def456"}],
            "head_commit": {"id": "def456"},
        },
    )
    assert pushed["commits"] == 2 and pushed["shipped"] == []
    api.app.state.store.settings.release_commit = "def456"
    with api.app.state.store.transaction() as c:
        assert sorted(G.ship_deployed(c, api.app.state.store.settings)) == sorted(before)
        assert c.execute("SELECT count(*) FROM events WHERE action='github.shipped'").fetchone()[0] == 2
    assert all(l["state"] == "shipped" for l in unmoved())
    # a tag push or a published release moves nothing; a release only wakes tasks waiting for it (test_milestone_waits)
    hook(api, "push", {"ref": "refs/tags/v1.2.3", "repository": {"full_name": "ticoteam/tico"}, "after": "d" * 40})
    assert (
        hook(
            api,
            "release",
            {
                "action": "published",
                "repository": {"full_name": "ticoteam/tico"},
                "release": {"tag_name": "v1.2.3", "target_commitish": "d" * 40},
            },
        )["woken"]
        == []
    )
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
    hook(
        api,
        'check_run',
        {
            'repository': {'full_name': 'ticoteam/tico'},
            'check_run': {'name': 'Unit tests', 'conclusion': 'failure', 'pull_requests': [{'number': 413}]},
        },
    )
    assert get(api, 'tasks/' + task['id'])['task']['pr_state'] == 'failing'
    hook(
        api,
        'pull_request_review',
        {'action': 'submitted', 'pull_request': {'html_url': second}, 'review': {'state': 'changes_requested'}},
    )
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
        hook(
            api,
            'pull_request_review_comment',
            {'action': 'created', 'pull_request': {'html_url': PR}, 'comment': {'id': n, 'user': {'login': 'sam'}}},
        )
    with api.app.state.store.transaction() as c:
        key = 'github-task-wake:' + task['id']
        burst = R.metadata(c, key)
        assert len(burst['items']) == 2
        # The notice wait owns the delay at enqueue time; GitHub adds no second debounce.
        assert G.flush_wakes(c) == [task['id']]
        assert G.flush_wakes(c) == []
        assert c.execute('SELECT count(*) FROM messages').fetchone()[0] == before + 1
        message = c.execute('SELECT body FROM messages ORDER BY created_at DESC LIMIT 1').fetchone()[0]
        assert '2 review comments' in message and 'tico#412' in message
        job = c.execute("SELECT held,wake_preset FROM jobs WHERE bot_id='cpo' ORDER BY rowid DESC LIMIT 1").fetchone()
        assert job['held'] and job['wake_preset'] is None  # held by the notice wait; no preset is recorded


def test_each_merged_pr_ships_once_the_release_contains_it(api):
    second = PR.replace('/412', '/413')
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship several changes', 'body': 'x', 'links': [PR, second]})
    hook(api, 'pull_request', pr_event('closed', merged=True, merge_commit_sha='first'))
    hook(api, 'pull_request', pr_event('closed', html_url=second, merged=True, merge_commit_sha='second'))
    api.app.state.store.settings.release_commit = 'first'
    with api.app.state.store.transaction() as c:
        assert G.ship_deployed(c, api.app.state.store.settings) == [task['id']]
        assert [l['state'] for l in H.task_links(c, task['id'])] == ['shipped', 'merged']
        G.push(
            c,
            {
                'ref': 'refs/heads/main',
                'repository': {'full_name': 'ticoteam/tico'},
                'commits': [{'id': 'first'}, {'id': 'second'}],
            },
        )
        api.app.state.store.settings.release_commit = 'second'
        assert G.ship_deployed(c, api.app.state.store.settings) == [task['id']]
        assert all(l['state'] == 'shipped' for l in H.task_links(c, task['id']))
        assert H.task(c, task['id'])['status'] == task['status']


def test_reviewer_who_pushes_is_not_the_bot_and_changes_requests_always_wake(api):
    from backend.repositories import metadata

    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Handle reviewer feedback', 'body': 'x', 'links': [PR]})
    payload = pr_event('synchronize', head={'sha': 'cur'}, user={'login': 'engineer[bot]'})
    payload['sender'] = {'login': 'engineer[bot]'}
    hook(api, 'pull_request', payload)
    # Author identity remains available even when a comment payload omits the PR author.
    hook(
        api,
        'pull_request_review_comment',
        {
            'action': 'created',
            'pull_request': {'html_url': PR},
            'comment': {'id': 1, 'user': {'login': 'engineer[bot]'}},
        },
    )
    with api.app.state.store.read() as c:
        assert not metadata(c, 'github-task-wake:' + task['id'])
    payload['pull_request']['head']['sha'] = 'next'
    payload['sender'] = {'login': 'sam'}
    hook(api, 'pull_request', payload)
    with api.app.state.store.read() as c:
        assert 'head_login' not in H._json(H.task_links(c, task['id'])[0]['detail'], {})
    hook(
        api,
        'pull_request_review_comment',
        {'action': 'created', 'pull_request': {'html_url': PR}, 'comment': {'id': 2, 'user': {'login': 'sam'}}},
    )
    with api.app.state.store.transaction() as c:
        assert metadata(c, 'github-task-wake:' + task['id'])['items']
        c.execute("DELETE FROM registry_metadata WHERE key=?", ('github-task-wake:' + task['id'],))
    hook(
        api,
        'pull_request_review',
        {
            'action': 'submitted',
            'pull_request': payload['pull_request'],
            'review': {'id': 9, 'state': 'commented', 'user': {'login': 'sam'}},
        },
    )
    with api.app.state.store.transaction() as c:
        assert 'Review commented' in metadata(c, 'github-task-wake:' + task['id'])['items'][0]
        c.execute("DELETE FROM registry_metadata WHERE key=?", ('github-task-wake:' + task['id'],))
    for n, login in enumerate(['sam', 'reviewer[bot]']):
        hook(
            api,
            'pull_request_review',
            {
                'action': 'submitted',
                'pull_request': payload['pull_request'],
                'review': {'id': n + 10, 'state': 'changes_requested', 'user': {'login': login}},
            },
        )
        with api.app.state.store.transaction() as c:
            burst = metadata(c, 'github-task-wake:' + task['id'])
            # A person's change request wakes; an App's is a note for the next run.
            assert 'changes requested' in (burst.get('items') or burst['notes'])[0] and bool(burst.get('items')) == (
                login == 'sam'
            )
            c.execute("DELETE FROM registry_metadata WHERE key=?", ('github-task-wake:' + task['id'],))
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created_at,created_by) "
            "VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,?)",
            (H.now(), 'human:ana'),
        )
    hook(
        api,
        'pull_request_review_comment',
        {
            'action': 'created',
            'pull_request': {'html_url': PR},
            'comment': {
                'id': 44,
                'user': {'login': 'token-login'},
                'performed_via_github_app': {'slug': 'example-app'},
            },
        },
    )
    with api.app.state.store.read() as c:
        assert not metadata(c, 'github-task-wake:' + task['id'])


def test_a_pr_outside_the_app_org_is_a_plain_link(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship tracked work', 'body': 'x', 'links': [PR]})
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created_at,created_by) "
            "VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,?)",
            (H.now(), 'human:ana'),
        )
    links = post(api, 'tasks/' + task['id'] + '/links', {'url': 'https://github.com/outside/service/pull/2'})['links']
    assert links[-1]['kind'] == 'url'


def test_background_refresh_keeps_newer_webhook_state(api, monkeypatch):
    from types import SimpleNamespace
    import threading

    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Keep newest PR facts', 'body': 'x', 'links': [PR]})
    service = api.app.state.github_app
    with service.store.transaction() as c:
        c.execute(
            "INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at) VALUES(?,1,1,?,?)",
            ('ticoteam/tico', 'human:ana', H.now()),
        )
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


def test_grouped_wakes_retry_bad_rows_and_send_open_tasks(api, monkeypatch):
    from backend.repositories import save_metadata

    tasks = [
        post(api, "tasks", {"owner": "cpo", "title": title, "body": "Review work"})
        for title in ("Broken notice", "Open task", "Finished task")
    ]
    store = api.app.state.store
    with store.transaction() as c:
        for task in tasks:
            save_metadata(
                c, "github-task-wake:" + task["id"], {"due": H.shift(H.now(), seconds=-1), "items": ["Checks failed"]}
            )
        H.task_done(c, "human:ana", tasks[2]["id"], "Finished.")
    from backend import task_engine as E

    tell = E.tell_owner

    def broken(c, task_id, *args, **kwargs):
        if task_id == tasks[0]["id"]:
            H.event(c, H.KEEPER, "wake.partial", task_id)
            raise RuntimeError("bad conversation")
        return tell(c, task_id, *args, **kwargs)

    monkeypatch.setattr(E, "tell_owner", broken)
    with store.transaction() as c:
        assert G.flush_wakes(c) == [tasks[1]["id"]]
    with store.read() as c:
        assert [r[0] for r in c.execute("SELECT key FROM registry_metadata WHERE key LIKE 'github-task-wake:%'")] == [
            "github-task-wake:" + tasks[0]["id"]
        ]
        assert not c.execute("SELECT 1 FROM events WHERE action='wake.partial'").fetchone()
    monkeypatch.setattr(E, "tell_owner", tell)
    with store.transaction() as c:
        assert G.flush_wakes(c) == [tasks[0]["id"]]


def test_a_review_request_puts_the_person_in_the_configured_role(api):
    assert post(api, "people/ana", {"github": "@Ana-Dev"})["github"] == "ana-dev"
    task = post(api, "tasks", {"owner": "cpo", "title": "Ship the pricing page", "body": "x", "links": [PR]})
    asked = {**pr_event("review_requested"), "requested_reviewer": {"login": "Ana-Dev"}}
    roles = lambda: get(api, "tasks/" + task["id"])["task"].get("roles") or {}

    hook(api, "pull_request", asked)  # no role configured: roles stay generic and untouched
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


def _arm(api, task_id, armed=True, url=PR):
    return post(api, 'tasks/' + task_id + '/links', {'url': url, 'wake_once': armed})['links']


def test_wake_once_fires_once_when_every_suite_finishes_and_never_moves_the_task(api):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on CI', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _suite(api, 'github-actions', 'completed', 'success')
    # Unarmed, the head's one result is queued all the same; backend/rules.py decides whether it wakes or is a note.
    assert _burst(api, task['id']) == [f"Checks finished on tico#412: passed {PR}/checks at {'a' * 12}"]
    links = _arm(api, task['id'])
    assert json.loads(links[0]['detail'])['wake_once']['by'] == 'human:ana'
    assert _arm(api, task['id']) == links  # arming twice changes nothing
    # A single run, or one suite while another still runs, is not the end of the checks.
    hook(
        api,
        'check_run',
        {
            'repository': {'full_name': 'ticoteam/tico'},
            'check_run': {
                'name': 'Unit tests',
                'conclusion': 'success',
                'head_sha': 'a' * 40,
                'pull_requests': [{'number': 412}],
            },
        },
    )
    _suite(api, 'github-actions', 'in_progress')
    _suite(api, 'vercel', 'completed', 'success')
    hook(
        api,
        'check_run',
        {
            'repository': {'full_name': 'ticoteam/tico'},
            'check_run': {
                'name': 'Deploy preview',
                'status': 'in_progress',
                'conclusion': None,
                'head_sha': 'a' * 40,
                'pull_requests': [{'number': 412}],
            },
        },
    )
    _suite(api, 'github-actions', 'completed', 'failure')  # a failing check alone says nothing: one is still pending
    assert len(_burst(api, task['id'])) == 1 and 'passed' in _burst(api, task['id'])[0]
    # The last pending run finishing after the suites have is the end of the checks.
    hook(
        api,
        'check_run',
        {
            'repository': {'full_name': 'ticoteam/tico'},
            'check_run': {
                'name': 'Deploy preview',
                'status': 'completed',
                'conclusion': 'success',
                'head_sha': 'a' * 40,
                'pull_requests': [{'number': 412}],
            },
        },
    )
    _suite(api, 'github-actions', 'completed', 'failure')  # a redelivery
    # The result that was still waiting to be sent is replaced, so one head gives one wake.
    assert _burst(api, task['id']) == [
        f"Checks finished on tico#412: failed (github-actions) {PR}/checks at {'a' * 12}"
    ]
    detail = json.loads(get(api, 'tasks/' + task['id'] + '/links')['links'][0]['detail'])
    assert 'wake_once' not in detail and detail['wake_once_fired']['item'].startswith('Checks finished')
    assert get(api, 'tasks/' + task['id'])['task']['status'] == task['status']


def test_wake_once_on_a_commit_status_that_finishes_last_names_it_when_it_fails(api):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on CI', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _arm(api, task['id'])
    _status(api, 'ci/legacy', 'pending')
    _suite(api, 'github-actions', 'completed', 'success')  # a status is still pending: not finished
    _status(api, 'ci/other', 'success')  # never seen pending: one of many, not the last
    assert _burst(api, task['id']) == []
    _status(api, 'ci/legacy', 'failure')
    _status(api, 'ci/legacy', 'failure')  # a redelivery
    assert _burst(api, task['id']) == [f"Checks finished on tico#412: failed (ci/legacy) {PR}/checks at {'a' * 12}"]
    detail = json.loads(get(api, 'tasks/' + task['id'] + '/links')['links'][0]['detail'])
    assert 'wake_once' not in detail


def _run(api, name, suite, status, conclusion=None, sha='a' * 40, suite_status='in_progress'):
    hook(
        api,
        'check_run',
        {
            'repository': {'full_name': 'ticoteam/tico'},
            'check_run': {
                'name': name,
                'status': status,
                'conclusion': conclusion,
                'head_sha': sha,
                'app': {'slug': 'github-actions'},
                'check_suite': {'id': suite, 'status': suite_status},
                'pull_requests': [{'number': 412}],
            },
        },
    )


def test_a_run_cancelled_by_its_concurrency_group_is_not_a_failed_check(api):
    """Nov 12 stress run, I38: GitHub cancelled a duplicate workflow run (its concurrency group) and the owner was told
    the checks failed, so it pushed an empty commit. A cancelled suite superseded by a newer run of the same workflow
    (a newer suite of its App with a check run of the same name) is left out; a cancel with nothing after it says so
    plainly. A failed or timed-out check is still a failure."""
    assert [G._check_state(r) for r in ('cancelled', 'stale', 'failure', 'timed_out')] == [
        'cancelled',
        'cancelled',
        'failing',
        'failing',
    ]
    two = {'suite:github-actions#1': 'cancelled', 'suite:github-actions#2': 'passing'}
    same = {'suite:github-actions#1': ['test'], 'suite:github-actions#2': ['test']}
    assert G._overall(two, same) == 'passing'
    assert G._overall({**two, 'suite:github-actions#2': 'pending'}, same) == 'pending'
    # Another workflow of the same App, or a suite whose runs were never seen, supersedes nothing.
    assert G._overall(two, {'suite:github-actions#1': ['test'], 'suite:github-actions#2': ['lint']}) == 'cancelled'
    assert G._overall(two) == 'cancelled'
    assert G._overall({'suite:github-actions#1': 'cancelled', 'lint': 'failing'}) == 'failing'
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Duplicate CI', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _suite(api, 'github-actions', 'queued', id=2)  # the newer run starts and cancels the older one
    _run(api, 'test', 1, 'completed', 'cancelled', suite_status='completed')
    _suite(api, 'github-actions', 'completed', 'cancelled', id=1)
    _run(api, 'test', 2, 'in_progress')
    _run(api, 'test', 2, 'completed', 'success')
    assert _burst(api, task['id']) == []
    _suite(api, 'github-actions', 'completed', 'success', id=2)
    assert _burst(api, task['id']) == [f"Checks finished on tico#412: passed {PR}/checks at {'a' * 12}"]
    # A new commit: workflow CI is cancelled and never re-run, workflow Lint (a newer suite of the same App) passes.
    hook(api, 'pull_request', pr_event('synchronize', head={'sha': 'b' * 40}))
    b = 'b' * 40
    _suite(api, 'github-actions', 'queued', sha=b, id=4)
    _run(api, 'test', 3, 'completed', 'cancelled', sha=b, suite_status='completed')
    _suite(api, 'github-actions', 'completed', 'cancelled', sha=b, id=3)
    _run(api, 'lint', 4, 'completed', 'success', sha=b)
    _suite(api, 'github-actions', 'completed', 'success', sha=b, id=4)
    assert _burst(api, task['id'])[-1] == (
        f"Checks finished on tico#412: cancelled, not failed (test, github-actions); re-run them, there is no code to "
        f"fix "
        f"{PR}/checks at {'b' * 12}"
    )
    link = get(api, 'tasks/' + task['id'] + '/links')['links'][0]
    assert link['checks'] == 'cancelled'


def test_a_fresh_read_ties_a_cancelled_suite_to_its_workflow_by_its_runs(api):
    """The background re-read (`read_checks`, `apply_checks`) decides as the webhooks do: a cancelled suite is left
    out only when a newer suite has a run of the same name."""

    def read(lint_name):
        answers = {
            '/check-suites': {
                'check_suites': [
                    {'id': 1, 'status': 'completed', 'conclusion': 'cancelled', 'app': {'slug': 'github-actions'}},
                    {'id': 2, 'status': 'completed', 'conclusion': 'success', 'app': {'slug': 'github-actions'}},
                ]
            },
            '/check-runs': {
                'check_runs': [
                    {
                        'name': 'test',
                        'status': 'completed',
                        'conclusion': 'cancelled',
                        'check_suite': {'id': 1},
                        'app': {'slug': 'github-actions'},
                    },
                    {
                        'name': lint_name,
                        'status': 'completed',
                        'conclusion': 'success',
                        'check_suite': {'id': 2},
                        'app': {'slug': 'github-actions'},
                    },
                ]
            },
            '/status': {'statuses': []},
        }

        def call(method, path, headers=None, params=None):
            body = next(v for k, v in answers.items() if path.endswith(k))
            return SimpleNamespace(status_code=200, json=lambda: body)

        return G.read_checks(SimpleNamespace(_call=call), {}, 'ticoteam/tico', 'a' * 40)

    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Re-read CI', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    link_id = get(api, 'tasks/' + task['id'] + '/links')['links'][0]['id']
    with api.app.state.store.transaction() as c:
        assert G.apply_checks(c, link_id, 'a' * 40, read('lint')) == 'cancelled'  # another workflow: CI never ran
        assert G.apply_checks(c, link_id, 'a' * 40, read('test')) == 'passing'  # CI re-run in a newer suite


def _merge_ok(api, conclusion, suite=9, sha='a' * 40):
    """Tico's own `tico/merge-ok` run, posted completed by the company's App (`AppGitHub.post_check`)."""
    hook(
        api,
        'check_run',
        {
            'repository': {'full_name': 'ticoteam/tico'},
            'check_run': {
                'name': 'tico/merge-ok',
                'status': 'completed',
                'conclusion': conclusion,
                'head_sha': sha,
                'app': {'slug': 'example-app'},
                'check_suite': {'id': suite, 'status': 'completed'},
                'pull_requests': [{'number': 412}],
            },
        },
    )


def test_ticos_own_merge_gate_is_not_a_ci_check(api):
    """Nov 13: a task listed its PR as checks failing when the only red check was `tico/merge-ok` ("Not queued by
    Tico"), Tico's own gate, red because the task had not reached Merge. Bots read it as broken CI and re-ran it. The
    gate and its App's suite are left out of a head's checks state and of what a finished head names; a real failure
    beside it still fails, and a head with nothing but the gate is not green."""
    gate = {'suite:example-app#9': ['tico/merge-ok']}
    ci = {'python': 'passing', 'suite:github-actions#1': 'passing'}
    assert G._overall({**ci, 'tico/merge-ok': 'failing', 'suite:example-app#9': 'failing'}, gate) == 'passing'
    assert G._overall({**ci, 'tico/merge-ok': 'failing'}) == 'passing'
    assert G._overall({**ci, 'python': 'failing', 'tico/merge-ok': 'passing'}, gate) == 'failing'
    assert G._overall({'tico/merge-ok': 'passing', 'suite:example-app#9': 'passing'}, gate) == 'pending'
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Gate is not CI', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _run(api, 'python', 1, 'completed', 'success', suite_status='completed')
    _suite(api, 'github-actions', 'completed', 'success', id=1)
    assert _burst(api, task['id']) == [f"Checks finished on tico#412: passed {PR}/checks at {'a' * 12}"]
    # The company App's suite can report before its run: it is still the gate's.
    _suite(api, 'example-app', 'completed', 'failure', id=9)
    _merge_ok(api, 'failure')
    link = get(api, 'tasks/' + task['id'] + '/links')['links'][0]
    assert link['checks'] == 'passing'
    assert all('failed' not in item for item in _burst(api, task['id']))
    # A fresh read from GitHub decides the same.
    answers = {
        '/check-suites': {
            'check_suites': [
                {'id': 1, 'status': 'completed', 'conclusion': 'success', 'app': {'slug': 'github-actions'}},
                {'id': 9, 'status': 'completed', 'conclusion': 'failure', 'app': {'slug': 'example-app'}},
            ]
        },
        '/check-runs': {
            'check_runs': [
                {
                    'name': 'python',
                    'status': 'completed',
                    'conclusion': 'success',
                    'check_suite': {'id': 1},
                    'app': {'slug': 'github-actions'},
                },
                {
                    'name': 'tico/merge-ok',
                    'status': 'completed',
                    'conclusion': 'failure',
                    'check_suite': {'id': 9},
                    'app': {'slug': 'example-app'},
                },
            ]
        },
        '/status': {'statuses': []},
    }

    def call(method, path, headers=None, params=None):
        body = next(v for k, v in answers.items() if path.endswith(k))
        return SimpleNamespace(status_code=200, json=lambda: body)

    read = G.read_checks(SimpleNamespace(_call=call), {}, 'ticoteam/tico', 'a' * 40)
    with api.app.state.store.transaction() as c:
        assert G.apply_checks(c, link['id'], 'a' * 40, read) == 'passing'


def _detail(link):
    return link['detail'] if isinstance(link['detail'], dict) else json.loads(link['detail'] or '{}')


def test_the_merge_gate_is_kept_on_the_link_apart_from_the_checks(api):
    """#426: the task's PR line shows Tico's gate as its own grey chip, so the link keeps where the gate stands:
    waiting while it is red (not queued by Tico), signed off once the Merge stage passed it, none before it ran."""
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Gate on the link', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _run(api, 'python', 1, 'completed', 'success', suite_status='completed')
    link = get(api, 'tasks/' + task['id'] + '/links')['links'][0]
    assert _detail(link)['merge_gate'] == 'none'
    _merge_ok(api, 'failure')
    link = get(api, 'tasks/' + task['id'] + '/links')['links'][0]
    assert (link['checks'], _detail(link)['merge_gate']) == ('passing', 'waiting')
    _merge_ok(api, 'success', suite=10)
    assert _detail(get(api, 'tasks/' + task['id'] + '/links')['links'][0])['merge_gate'] == 'signed_off'
    # A fresh read from GitHub keeps it too, even when the checks state itself does not change.
    with api.app.state.store.transaction() as c:
        state = G.apply_checks(c, link['id'], 'a' * 40, {'python': 'passing', 'tico/merge-ok': 'failing'})
    link = get(api, 'tasks/' + task['id'] + '/links')['links'][0]
    assert (state, _detail(link)['merge_gate']) == ('passing', 'waiting')


def test_links_left_failing_only_for_the_gate_are_recomputed_once_and_wake_nobody(api):
    """Nov 13 on rc.30: three open PRs still read "failing" after the gate was left out of the state: every real check
    passed, only `tico/merge-ok` and the company App's suite were red, and a failed link with no pending check is never
    read again. One pass rebuilds each open link's state from its stored copy, the App's suite counted as the gate's
    even with no run names stored; a real failure stays failing; nothing is woken, and it runs once."""
    _app(api)
    gate_only = post(api, 'tasks', {'owner': 'cpo', 'title': 'Gate only', 'body': 'x', 'links': [PR]})
    real = post(
        api, 'tasks', {'owner': 'cpo', 'title': 'Real failure', 'body': 'x', 'links': [PR.replace('412', '413')]}
    )
    stored = {
        gate_only['id']: {
            'python': 'passing',
            'suite:github-actions#1': 'passing',
            'tico/merge-ok': 'failing',
            'suite:example-app#9': 'failing',
        },
        real['id']: {
            'python': 'failing',
            'suite:github-actions#1': 'failing',
            'tico/merge-ok': 'failing',
            'suite:example-app#9': 'failing',
        },
    }
    with api.app.state.store.transaction() as c:
        for task_id, checks in stored.items():
            c.execute(
                "UPDATE task_links SET state='open',checks='failing',detail=? WHERE task_id=? AND kind='pr'",
                (json.dumps({'head_sha': 'a' * 40, 'checks': checks}), task_id),
            )
    # The GitHub heartbeat step is what runs it on a live install.
    from backend import heartbeat as HB

    HB.github_sync(SimpleNamespace(app=api.app, store=api.app.state.store))
    with api.app.state.store.transaction() as c:
        assert G.recompute_stored_checks(c) is None
    gate_link = get(api, 'tasks/' + gate_only['id'] + '/links')['links'][0]
    real_link = get(api, 'tasks/' + real['id'] + '/links')['links'][0]
    assert (gate_link['checks'], _detail(gate_link)['merge_gate']) == ('passing', 'waiting')
    assert (real_link['checks'], _detail(real_link)['merge_gate']) == ('failing', 'waiting')
    assert _burst(api, gate_only['id']) == [] and _burst(api, real['id']) == []


def test_a_queued_gate_suite_does_not_hold_a_green_head_unfinished(api):
    """GitHub queues a suite of the company App on every head, and it never runs unless the Merge stage posts the
    gate. Left out of the state, it must be left out of "still going" too, or a green head reads passing but never
    finishes: no "Checks finished", and nothing waiting on the checks is woken."""
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Gate queued', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _suite(api, 'example-app', 'queued', id=9)
    _suite(api, 'github-actions', 'queued', id=1)
    _run(api, 'python', 1, 'in_progress')
    _run(api, 'python', 1, 'completed', 'success', suite_status='completed')
    _suite(api, 'github-actions', 'completed', 'success', id=1)
    link = get(api, 'tasks/' + task['id'] + '/links')['links'][0]
    assert link['checks'] == 'passing'
    assert _burst(api, task['id']) == [f"Checks finished on tico#412: passed {PR}/checks at {'a' * 12}"]


def test_an_all_skipped_head_reads_pending_never_green(api):
    """A draft pull request's run skips every job; nothing on main requires a check, so a head whose every check was
    skipped has not been checked. It reads pending and finishes nothing; one real pass beside the skips is green."""
    assert G._overall({'suite:github-actions#1': 'skipped', 'plan': 'skipped'}) == 'pending'
    assert G._overall({'plan': 'skipped', 'static': 'passing'}) == 'passing'
    assert G._overall({'plan': 'skipped', 'static': 'failing'}) == 'failing'
    assert G._check_state('skipped') == 'skipped' and G._check_state('neutral') == 'passing'
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Draft CI', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    hook(
        api,
        'check_run',
        {
            'repository': {'full_name': 'ticoteam/tico'},
            'check_run': {
                'name': 'plan',
                'status': 'in_progress',
                'conclusion': None,
                'head_sha': 'a' * 40,
                'pull_requests': [{'number': 412}],
            },
        },
    )
    hook(
        api,
        'check_run',
        {
            'repository': {'full_name': 'ticoteam/tico'},
            'check_run': {
                'name': 'plan',
                'status': 'completed',
                'conclusion': 'skipped',
                'head_sha': 'a' * 40,
                'pull_requests': [{'number': 412}],
            },
        },
    )
    _suite(api, 'github-actions', 'completed', 'skipped', id=1)
    link = get(api, 'tasks/' + task['id'] + '/links')['links'][0]
    assert link['checks'] == 'pending' and _burst(api, task['id']) == []
    assert json.loads(link['detail'])['checks'] == {'plan': 'skipped', 'suite:github-actions#1': 'skipped'}
    hook(
        api,
        'check_run',
        {
            'repository': {'full_name': 'ticoteam/tico'},
            'check_run': {
                'name': 'python',
                'status': 'completed',
                'conclusion': 'success',
                'head_sha': 'a' * 40,
                'pull_requests': [{'number': 412}],
            },
        },
    )
    assert get(api, 'tasks/' + task['id'] + '/links')['links'][0]['checks'] == 'passing'


def test_wake_once_holds_while_a_finished_runs_own_suite_still_runs(api):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on CI', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _arm(api, task['id'])

    def run(name, status, conclusion=None, suite='in_progress', id=2):
        hook(
            api,
            'check_run',
            {
                'repository': {'full_name': 'ticoteam/tico'},
                'check_run': {
                    'name': name,
                    'status': status,
                    'conclusion': conclusion,
                    'head_sha': 'a' * 40,
                    'app': {'slug': 'github-actions'},
                    'check_suite': {'id': id, 'status': suite, 'app': {'slug': 'github-actions'}},
                    'pull_requests': [{'number': 412}],
                },
            },
        )

    run('build', 'in_progress')
    _suite(api, 'vercel', 'completed', 'success')
    _suite(api, 'github-actions', 'completed', 'failure', id=1)  # another workflow, its own suite
    run('build', 'completed', 'success')  # its workflow has more jobs to come
    run('deploy', 'completed', 'failure')
    assert _burst(api, task['id']) == []  # no single check is news
    _suite(api, 'github-actions', 'completed', 'failure', id=2)
    assert _burst(api, task['id']) == [
        f"Checks finished on tico#412: failed (github-actions, deploy) {PR}/checks at {'a' * 12}"
    ]
    run('deploy', 'completed', 'failure')  # delivered late: does not reopen its suite
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
        f"Checks finished on tico#412: failed (github-actions) {PR}/checks at {'a' * 12}"
    ]


def test_wake_once_on_a_review_a_ship_or_a_merge_that_cannot_ship(api):
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on review', 'body': 'x', 'links': [PR]})
    _arm(api, task['id'])
    review = {
        'action': 'submitted',
        'pull_request': {'html_url': PR},
        'repository': {'full_name': 'ticoteam/tico'},
        'review': {'id': 7, 'state': 'approved', 'user': {'login': 'ana'}, 'html_url': PR + '#pullrequestreview-7'},
    }
    hook(api, 'pull_request_review', review)
    hook(api, 'pull_request_review', {**review, 'review': {**review['review'], 'id': 8}})  # disarmed: quiet
    assert _burst(api, task['id']) == [f'Review approved on tico#412 by ana {PR}#pullrequestreview-7']
    # Armed again: a merge in the release repository waits for the release that ships it.
    assert 'wake_once' not in json.loads(_arm(api, task['id'], armed=False)[0]['detail'])
    shipping = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on the release', 'body': 'x', 'links': [PR]})
    late = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on the release too', 'body': 'x', 'links': [PR]})
    _arm(api, shipping['id'])
    hook(api, 'pull_request', pr_event('closed', merged=True, merge_commit_sha='b' * 40))
    assert _burst(api, shipping['id']) == [merged('tico#412', PR)]  # the merge's own wake; the ship waits
    # Merged in the release repository and waiting to ship: arming it now is still allowed.
    assert json.loads(_arm(api, late['id'])[0]['detail'])['wake_once']['by'] == 'human:ana'
    api.app.state.store.settings.release_commit = 'b' * 40
    with api.app.state.store.transaction() as c:
        assert shipping['id'] in G.ship_deployed(c, api.app.state.store.settings)
        assert G.ship_deployed(c, api.app.state.store.settings) == []
        assert H.task(c, shipping['id'])['status'] == shipping['status']
    assert _burst(api, shipping['id']) == [
        merged('tico#412', PR),
        f"Shipped: tico#412 is in the running release {'b' * 12} {PR}",
    ]
    assert _burst(api, late['id']) == _burst(api, shipping['id'])
    # A merge anywhere else never ships, so it is the last result.
    other = 'https://github.com/ticoteam/site/pull/9'
    elsewhere = post(api, 'tasks', {'owner': 'cpo', 'title': 'Wait on the site', 'body': 'x', 'links': [other]})
    _arm(api, elsewhere['id'], url=other)
    hook(api, 'pull_request', pr_event('closed', html_url=other, merged=True, merge_commit_sha='c' * 40))
    # One wake per merge per task: the merge's own, saying no shipped wake follows.
    assert _burst(api, elsewhere['id']) == [
        merged('site#9', other) + " It is not in the repository Tico releases, so no shipped wake follows."
    ]
    # Arming it again has nothing left to wake on, and neither has a shipped PR.
    r = api.post(
        '/api/v2/tasks/' + elsewhere['id'] + '/links', json={'url': other, 'wake_once': True}, headers=headers()
    )
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
    assert [link['url'] for link in _arm(api, other['id'])] == [mixed]  # stored mixed case, linked lowercase
    # A duplicate left by an older install is still disarmed through its own URL.
    with api.app.state.store.transaction() as c:
        H.task_link(c, 'human:ana', task['id'], PR, wake_once=True)
        c.execute(
            "INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,number,detail) "
            "VALUES('dup',?,'pr',?,'tico#412','open','human:ana',?,'TicoTeam/Tico',412,?)",
            (
                task['id'],
                'https://github.com/TicoTeam/Tico/pull/412',
                H.now(),
                json.dumps({'wake_once': {'by': 'human:ana'}}),
            ),
        )
    links = {
        link['id']: link for link in _arm(api, task['id'], armed=False, url='https://github.com/TicoTeam/Tico/pull/412')
    }
    assert 'wake_once' not in json.loads(links['dup']['detail'])
    assert 'wake_once' in json.loads(next(v for k, v in links.items() if k != 'dup')['detail'])


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
            patch = "\n".join(
                list(difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(), lineterm=""))[2:]
            )
            return {
                "filename": path,
                "patch": patch,
                **extra,
                "sha": hashlib.sha1((after if after is not None else before).encode()).hexdigest(),
            }

        out = [
            entry(old[was], new[path], path, status="renamed", previous_filename=was)
            for path, was in dict(renames).items()
        ]
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
            return reply(
                {
                    "status": "identical" if base == head else "ahead" if ahead else "diverged",
                    "base_commit": {"sha": base},
                    "merge_base_commit": {"sha": base if ahead else None},
                    "files": self.files(self.commits[base][1], self.commits[head][1]),
                }
            )
        if kind == "commits":
            parents, tree, renames = self.commits[arg]
            return reply(
                {
                    "parents": [{"sha": p} for p in parents],
                    "files": self.files(self.commits[parents[0]][1] if parents else {}, tree, renames),
                }
            )
        if kind == "contents":
            text = self.commits[params["ref"]][1].get(unquote(arg))
            return reply(
                {"sha": hashlib.sha1(text.encode()).hexdigest()} if text is not None else {},
                200 if text is not None else 404,
            )
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
        c.execute(
            "INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at) VALUES(?,1,1,?,?)",
            ('ticoteam/tico', 'human:ana', H.now()),
        )
    tasks = {}
    for n in range(412, 418):
        url = PR.replace('/412', f'/{n}')
        tasks[n] = post(
            api,
            'tasks',
            {
                'owner': 'cpo',
                'title': f'Change {n}',
                'body': 'x',
                'links': [url],
            },
        )
        _arm(api, tasks[n]['id'], url=url)

    code = "export function refresh() {\n  poll(120000);\n}\n"
    live = "export function refresh() {\n  poll(120000);\n  onStatusEvent(event => moveDesk(event.bot));\n}\n"
    audit = "def a():\n    audit_event()\n    return 1\n\n\ndef b():\n    return 2\n"
    h = FakeHistory()
    base = h.commit(
        'base',
        [],
        {
            'ui/app/refresh.ts': code,
            'ui/app/pricing.js': 'price = 10\n',
            'app/flag.py': 'x=1\n',
            'app/loop.py': 'for item in items:\n    work(item)\ndone()\n',
            'app/audit.py': audit,
            'app/old.py': 'def price():\n    return 10\n',
        },
    )
    merges = {}

    def merge(n, parent, **changes):
        merges[n] = h.commit(f'#{n}', [parent], h.tree(parent, **changes), changes.pop('renames', None))
        hook(
            api,
            'pull_request',
            pr_event('closed', html_url=PR.replace('/412', f'/{n}'), merged=True, merge_commit_sha=merges[n]),
        )
        return merges[n]

    # 412 the live desks (product and test); 413 kept; 414 a two-character edit; 415 indentation only;
    # 416 adds a line that already exists elsewhere in the file; 417 renames a file and edits it.
    last = merge(
        412,
        base,
        ui__app__refresh_dot_js=live,
        ui__tests__overview_live_dot_cjs='assert desk moved at once\n',
    )
    last = merge(413, last, ui__app__pricing_dot_js='price = 12 per seat\n')
    last = merge(414, last, app__flag_dot_py='x=2\n')
    last = merge(415, last, app__loop_dot_py='for item in items:\n    work(item)\n    done()\n')
    last = merge(416, last, app__audit_dot_py=audit.replace('    return 2', '    audit_event()\n    return 2'))
    merges[417] = last = h.commit(
        '#417',
        [last],
        h.tree(last, app__old_dot_py=None, app__new_dot_py='def price():\n    return 20\n'),
        {'app/new.py': 'app/old.py'},
    )
    hook(
        api, 'pull_request', pr_event('closed', html_url=PR.replace('/412', '/417'), merged=True, merge_commit_sha=last)
    )
    # The cut drops 416's pre-existing call, keeping the one 416 added.
    cut = h.commit(
        'cut',
        [last],
        h.tree(
            last,
            app__audit_dot_py=audit.replace('    audit_event()\n    return 1', '    return 1').replace(
                '    return 2', '    audit_event()\n    return 2'
            ),
        ),
    )
    revert = h.commit(
        'revert',
        [cut],
        h.tree(
            cut,
            ui__app__refresh_dot_js=code,
            ui__tests__overview_live_dot_cjs=None,
            app__flag_dot_py='x=1\n',
            app__loop_dot_py='for item in items:\n    work(item)\ndone()\n',
            app__new_dot_py=None,
            app__old_dot_py='def price():\n    return 10\n',
        ),
    )
    # main's push records put every merge before the release: push order alone would ship them all
    hook(
        api,
        'push',
        {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'ticoteam/tico', 'default_branch': 'main'},
            'commits': [{'id': sha} for sha in [*merges.values(), cut, revert]],
        },
    )
    api.app.state.store.settings.release_commit = revert
    with api.app.state.store.transaction() as c:
        assert G.ship_deployed(c, api.app.state.store.settings) == []

    _refresh_release(api, monkeypatch, h, revert)
    shipped = {413, 416}
    with api.app.state.store.read() as c:
        for n, task in tasks.items():
            assert [l['state'] for l in H.task_links(c, task['id'])] == ['shipped' if n in shipped else 'merged'], n
        assert sorted(
            r['target'] for r in c.execute("SELECT target FROM events WHERE action='github.shipped'")
        ) == sorted(tasks[n]['id'] for n in shipped)
    for n, task in tasks.items():
        url = PR.replace('/412', f'/{n}')
        assert _burst(api, task['id']) == [merged(f'tico#{n}', url)] + (
            [f"Shipped: tico#{n} is in the running release {revert[:12]} {url}"] if n in shipped else []
        ), n
        assert get(api, 'tasks/' + task['id'])['task']['stage']['id'] == task['stage']['id']
    # A revert is fixed history: the next pass asks GitHub nothing more about it.
    _refresh_release(api, monkeypatch, h, revert)
    assert h.calls == []

    # Re-applied in a later release, the same pull request ships then.
    again = h.commit('reapply #412', [revert], h.tree(revert, ui__app__refresh_dot_js=live))
    _refresh_release(api, monkeypatch, h, again)
    assert _burst(api, tasks[412]['id']) == [
        merged('tico#412', PR),
        f"Shipped: tico#412 is in the running release {again[:12]} {PR}",
    ]


def test_change_kept_reads_a_merge_commit_against_main_and_ignores_cut_tests():
    h = FakeHistory()
    get = lambda path, **params: G._read(SimpleNamespace(_call=h.call), 'test-token', path, params)
    code = "def price():\n    return 10\n"
    base = h.commit('base', [], {'app/price.py': code, 'docs/a.md': 'Plain words here\n'})
    branch = h.commit(
        'branch',
        [base],
        {
            'app/price.py': code + "    # per seat from now on\n",
            'app/tests/test_price.py': 'assert price() == 10\n',
            'docs/a.md': 'Plain words here\n',
        },
    )
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
        c.execute(
            "INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,branch,path,updated_at,"
            "detail) VALUES(?,?,'worktree',?,?,?,'bot:cpo',?,?,?,?,?,?)",
            (
                link,
                task_id,
                'worktree:' + link,
                repo,
                state,
                H.now(),
                repo,
                branch,
                f'tasks/{task_id[:8]}/{repo.replace("/", "__")}',
                H.now(),
                json.dumps({'owner': 'bot:cpo', **detail}),
            ),
        )
    return link


def _worktree_detail(api, link):
    with api.app.state.store.read() as c:
        return json.loads(c.execute("SELECT detail FROM task_links WHERE id=?", (link,)).fetchone()[0])


def test_opened_non_engineering_pr_does_not_need_creation_qa(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Planning notes', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    assert not _prs(api, task['id'])[412]['detail'].get('qa_default_pending')


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
    hook(
        api,
        'pull_request',
        {
            'action': 'closed',
            'pull_request': _branch_pr(
                16, branch, 'ticoteam/tico-services', state='closed', merged=True, merge_commit_sha='c' * 40
            ),
        },
    )
    prs = {(l['repo'], l['number']): l for l in _prs(api, task['id']).values()}
    assert set(prs) == {('ticoteam/tico', 984), ('ticoteam/tico-services', 16)}
    assert prs[('ticoteam/tico', 984)]['state'] == 'open' and prs[('ticoteam/tico-services', 16)]['state'] == 'merged'
    assert all(l['added_by'] == 'keeper' and l['branch'] == branch for l in prs.values())
    assert _prs(api, other['id']) == {}
    # Registered once: later events on the same PR update that link, and registering wakes nobody.
    hook(api, 'pull_request', {'action': 'synchronize', 'pull_request': _branch_pr(984, branch)})
    assert len(_prs(api, task['id'])) == 2
    assert _burst(api, task['id']) == [merged('tico-services#16', 'https://github.com/ticoteam/tico-services/pull/16')]


def test_reconcile_heals_stale_and_unregistered_prs_on_a_closed_task_nobody_reads(api, monkeypatch):
    _app(api)
    task = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Org chart order',
            'body': 'x',
            'links': ['https://github.com/ticoteam/tico/pull/746'],
        },
    )
    branch = f"tico/{task['id'][:8]}-org-chart-order"
    _worktree(api, task['id'], 'ticoteam/tico', branch)
    _worktree(api, task['id'], 'ticoteam/tico', 'tico/' + task['id'][:8] + '-fix-forward')
    pushed = 'tico/' + task['id'][:8] + '-first-push'
    _worktree(api, task['id'], 'ticoteam/tico', pushed)
    # The first push asks for a draft PR; the push itself (and the checks it starts) is never held back.
    assert (
        hook(
            api,
            'push',
            {
                'ref': 'refs/heads/' + pushed,
                'repository': {'full_name': 'ticoteam/tico'},
                'after': 'f' * 40,
                'head_commit': {'id': 'f' * 40},
            },
        )['commits']
        == 0
    )
    calls = []

    def fetch(method, path, headers=None, params=None, json=None):
        calls.append((method, path))
        if method == 'POST':
            assert json['head'] == pushed and json['draft'] is True and json['title'] == 'Org chart order'
            return SimpleNamespace(status_code=201, json=lambda: _branch_pr(991, pushed, draft=True))
        if path.endswith('/pulls/746'):
            return SimpleNamespace(
                status_code=200,
                json=lambda: _branch_pr(746, branch, state='closed', merged=True, merge_commit_sha='d' * 40),
            )
        if '/compare/' in path:
            # Only the pushed branch has commits of its own; a branch nobody pushed is not on GitHub.
            return (
                SimpleNamespace(status_code=200, json=lambda: {'ahead_by': 1})
                if path.endswith(quote(pushed, safe=''))
                else SimpleNamespace(status_code=404, json=lambda: {})
            )
        if params and params['head'] == 'ticoteam:tico/' + task['id'][:8] + '-fix-forward':
            return SimpleNamespace(status_code=200, json=lambda: [_branch_pr(990, params['head'].split(':')[1])])
        return SimpleNamespace(status_code=200, json=lambda: [])

    service, run = _reconciler(api, monkeypatch, fetch)
    with service.store.transaction() as c:
        H.task_close(c, "human:ana", task['id'], "Not needed.")
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
    busy = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Busy',
            'body': 'x',
            'links': [f'https://github.com/ticoteam/tico/pull/{n}' for n in range(100, 120)],
        },
    )
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
            return (
                SimpleNamespace(status_code=200, json=lambda: {'ahead_by': 2})
                if path.endswith(quote(branch, safe=''))
                else SimpleNamespace(status_code=404, json=lambda: {})
            )
        if '/pulls/' in path:
            return SimpleNamespace(status_code=200, json=lambda: _branch_pr(int(path.rsplit('/', 1)[1]), 'feature'))
        return SimpleNamespace(status_code=200, json=lambda: [])

    service, run = _reconciler(api, monkeypatch, fetch)
    with service.store.transaction() as c:
        c.execute(
            "UPDATE task_links SET updated_at=? WHERE task_id=? AND kind='pr'",
            ('2026-01-01T00:00:00.000000Z', busy['id']),
        )
    run()
    assert _prs(api, missed['id'])[200]['state'] == 'draft' and _prs(api, idle['id']) == {}
    assert [c for c in calls if c[0] == 'POST'] == [('POST', '/repos/ticoteam/tico/pulls')]
    read = [int(p.rsplit('/', 1)[1]) for m, p in calls if '/pulls/' in p]
    assert len(read) == 17 and _worktree_detail(api, tree)['remote']['ahead'] == 2
    cache = service.worktree_reconcile_cache
    schedule = {
        'pr' if key[0] == 'pr' else key[2].split('-', 1)[1]: round(e['next'] - e['last']) for key, e in cache.items()
    }
    assert schedule == {'pr': 180, 'busy': 900, 'never-pushed': 900, 'missed-push': 180}
    # The next pass reads the three PRs the first one had no room for and the new draft, nothing it read already.
    calls.clear()
    service.worktree_reconcile_next = 0
    run()
    assert sorted(int(p.rsplit('/', 1)[1]) for m, p in calls if '/pulls/' in p) == sorted(
        set(range(100, 120)) - set(read)
    ) + [200]


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
            G.pull_request(
                c,
                {
                    'action': 'closed',
                    'pull_request': _branch_pr(
                        7,
                        branch,
                        state='closed',
                        merged=True,
                        merge_commit_sha='c' * 40,
                        head={'ref': branch, 'sha': 'n' * 40, 'repo': {'full_name': 'ticoteam/tico'}},
                    ),
                },
            )
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
    hook(
        api,
        'push',
        {
            'ref': 'refs/heads/' + branch,
            'repository': {'full_name': 'ticoteam/tico'},
            'after': 'f' * 40,
            'head_commit': {'id': 'f' * 40},
        },
    )
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


@pytest.mark.parametrize('tico_first', [False, True])
def test_a_bot_pr_racing_tico_s_draft_settles_on_the_earliest_and_the_other_closes_quietly(
    api, monkeypatch, tico_first
):
    """GitHub accepted a bot's `gh pr create` and Tico's draft on one head in the same second. Tico reads the branch
    again after its draft, keeps the earliest, closes the later one with a note, and the task keeps one link; the
    later one's late webhooks never link it again and its close wakes nobody. Synthetic: no real GitHub call."""
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Race', 'body': 'x'})
    branch = f"tico/{task['id'][:8]}-race"
    _worktree(api, task['id'], 'ticoteam/tico', branch)
    hook(
        api,
        'push',
        {
            'ref': 'refs/heads/' + branch,
            'repository': {'full_name': 'ticoteam/tico'},
            'after': 'f' * 40,
            'head_commit': {'id': 'f' * 40},
        },
    )
    mine, bots = (3399, 3400) if tico_first else (3400, 3399)
    later = max(mine, bots)
    calls = []

    def fetch(method, path, headers=None, params=None, json=None):
        calls.append((method, path, json))
        if method == 'POST' and path.endswith('/pulls'):
            # Both webhooks land while Tico's request is still out.
            for n in (bots, mine):
                hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(n, branch, draft=True)})
            return SimpleNamespace(status_code=201, json=lambda: _branch_pr(mine, branch, draft=True))
        if method in ('POST', 'PATCH'):
            return SimpleNamespace(status_code=200 if method == 'PATCH' else 201, json=lambda: {})
        if '/compare/' in path:
            return SimpleNamespace(status_code=200, json=lambda: {'ahead_by': 1})
        if params and params.get('state') == 'open':
            return SimpleNamespace(status_code=200, json=lambda: [_branch_pr(n, branch) for n in (3400, 3399)])
        return SimpleNamespace(status_code=200, json=lambda: [])

    _, run = _reconciler(api, monkeypatch, fetch)
    run()
    assert list(_prs(api, task['id'])) == [3399]
    closes = [(m, p, j) for m, p, j in calls if m == 'PATCH' or '/issues/' in p]
    assert [(m, p) for m, p, _ in closes] == [
        ('POST', f'/repos/ticoteam/tico/issues/{later}/comments'),
        ('PATCH', f'/repos/ticoteam/tico/pulls/{later}'),
    ]
    assert '#3399' in closes[0][2]['body'] and closes[1][2] == {'state': 'closed'}
    # The later one's late webhooks: the open one never links again, and its close is no news to the task.
    hook(api, 'pull_request', {'action': 'synchronize', 'pull_request': _branch_pr(later, branch)})
    hook(api, 'pull_request', {'action': 'closed', 'pull_request': _branch_pr(later, branch, state='closed')})
    assert list(_prs(api, task['id'])) == [3399] and _burst(api, task['id']) == []


@pytest.mark.parametrize('own_work', [False, True])
def test_a_worktree_branch_pushed_at_the_tasks_open_pr_head_gets_no_draft(api, monkeypatch, own_work):
    """The Release Owner rebased the task's PR (on a branch of the Builder's own name) from its own worktree: the PR's
    branch and the worktree's branch were pushed at one commit, and the worktree branch got a second, draft PR. A
    branch whose work is all in an open PR of the same task gets none; one with work of its own still does."""
    _app(api)
    pr = 'https://github.com/ticoteam/tico/pull/3391'
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Phone buttons', 'body': 'x', 'links': [pr]})
    theirs, canonical = f"tico/{task['id'][:8]}-prop-add", f"tico/{task['id'][:8]}-phone-buttons"
    if own_work:  # a PR whose head no webhook has told yet is read
        assert not _prs(api, task['id'])[3391]['detail'].get('head_sha')
    else:
        hook(api, 'pull_request', {'action': 'synchronize', 'pull_request': _branch_pr(3391, theirs)})
    _worktree(api, task['id'], 'ticoteam/tico', canonical)
    hook(
        api,
        'push',
        {
            'ref': 'refs/heads/' + canonical,
            'repository': {'full_name': 'ticoteam/tico'},
            'after': 'b' * 40,
            'head_commit': {'id': 'b' * 40},
        },
    )
    calls = []

    def fetch(method, path, headers=None, params=None, json=None):
        calls.append((method, path))
        if method == 'POST':
            return SimpleNamespace(status_code=201, json=lambda: _branch_pr(3421, canonical, draft=True))
        if path.endswith('/pulls/3391'):
            return SimpleNamespace(status_code=200, json=lambda: _branch_pr(3391, theirs))
        if '/compare/' in path:
            beyond_pr = path.split('/compare/')[1].startswith('b' * 40)
            return SimpleNamespace(status_code=200, json=lambda: {'ahead_by': int(own_work or not beyond_pr)})
        return SimpleNamespace(status_code=200, json=lambda: [])

    _, run = _reconciler(api, monkeypatch, fetch)
    run()
    posts = [c for c in calls if c[0] == 'POST']
    assert ('GET', f"/repos/ticoteam/tico/compare/{'b' * 40}...{quote(canonical, safe='')}") in calls
    if own_work:
        assert posts == [('POST', '/repos/ticoteam/tico/pulls')] and sorted(_prs(api, task['id'])) == [3391, 3421]
    else:
        assert posts == [] and list(_prs(api, task['id'])) == [3391]


def test_two_live_prs_on_one_task_branch_settle_on_the_earliest_whichever_webhook_comes_first(api):
    """Webhooks alone, with no draft of Tico's: the later live PR on a task branch is unlinked when the earlier one
    arrives, a later one never joins, and a closed PR on another branch or a lone one still links as before."""
    _app(api)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Twice', 'body': 'x'})
    branch = f"tico/{task['id'][:8]}-twice"
    _worktree(api, task['id'], 'ticoteam/tico', branch)
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(21, branch)})
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(20, branch)})
    assert list(_prs(api, task['id'])) == [20]
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(22, branch)})
    hook(api, 'pull_request', {'action': 'reopened', 'pull_request': _branch_pr(21, branch)})
    assert list(_prs(api, task['id'])) == [20]
    # Closed and replaced on purpose: the closed link stays, the replacement links, and nothing is unlinked.
    hook(api, 'pull_request', {'action': 'closed', 'pull_request': _branch_pr(20, branch, state='closed')})
    hook(api, 'pull_request', {'action': 'opened', 'pull_request': _branch_pr(30, branch)})
    prs = _prs(api, task['id'])
    assert {n: l['state'] for n, l in prs.items()} == {20: 'closed', 30: 'open'}


def test_a_burst_wakes_only_a_bot_whose_turn_it_is_and_never_messages_a_person(api):
    """GitHub news goes through the engine's keyed wake: the bot owner on its turn gets one run; a bot whose task
    waits on a person gets the news as a note and no run; a person who owns the task is told nothing (it is in
    their Needs you)."""
    mine = post(api, 'tasks', {'owner': 'cpo', 'title': 'Repair checks', 'body': 'x', 'links': [PR]})
    asked = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ask first', 'body': 'x', 'links': [PR]})
    post(api, f"tasks/{asked['id']}/asks", {'to': 'ana', 'line': 'Which plan?', 'version': asked['version']})
    theirs = post(api, 'tasks', {'owner': 'ana', 'title': 'Read the PR', 'body': 'x', 'links': [PR]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
        before = c.execute('SELECT max(rowid) FROM messages').fetchone()[0]
    hook(
        api,
        'pull_request_review_comment',
        {'action': 'created', 'pull_request': {'html_url': PR}, 'comment': {'id': 1, 'user': {'login': 'sam'}}},
    )
    with api.app.state.store.transaction() as c:
        assert set(G.flush_wakes(c)) >= {mine['id'], asked['id']}
        rows = c.execute(
            "SELECT m.recipient_id, c.task_id, m.wake_key, json_extract(m.refs,'$.quiet'), "
            "(SELECT j.state FROM jobs j WHERE j.message_id=m.id) FROM messages m "
            'JOIN conversations c ON c.id=m.conversation_id WHERE m.rowid>? ORDER BY m.rowid',
            (before,),
        ).fetchall()
    by_task = {row[1]: row for row in rows}
    assert theirs['id'] not in by_task  # a person gets no message
    assert by_task[mine['id']][0] == 'bot:cpo' and by_task[mine['id']][4] == 'queued'
    assert by_task[mine['id']][2].startswith(f"{mine['id']}:")  # keyed: the engine holds its run to the turn
    assert by_task[asked['id']][3] == 1 and by_task[asked['id']][4] is None  # a note, no run
    assert len(rows) == 2


def _queue(api, task_id, action, reason=None, head='a' * 40, **over):
    """A merge-queue webhook for the task's PR at `head`. GitHub's dequeue `reason` rides on the top-level payload."""
    body = pr_event(action, head={'sha': head}, **over)
    if reason is not None:
        body['reason'] = reason
    return hook(api, 'pull_request', body)


def _sent(api):
    with api.app.state.store.transaction() as c:
        return G.flush_wakes(c)


def _github_pr(api, monkeypatch, **pr):
    """A fake GitHub for the maintenance pass's read of one pull request. `pr` sets what it says; `status` other than 200
    fails the read, and the dict can be changed between passes. Returns the service, the reads asked, and the fake."""
    seen = []
    fake = {'status': 200, 'state': 'open', 'merged': False, 'head': 'a' * 40, **pr}

    def fetch(method, path, headers=None, **kw):
        seen.append((method, path))
        body = {'state': fake['state'], 'merged': fake['merged'], 'head': {'sha': fake['head']}}
        return SimpleNamespace(status_code=fake['status'], json=lambda: body)

    service, _ = _reconciler(api, monkeypatch, fetch)
    return service, seen, fake


@pytest.mark.parametrize('reason', ['CHECKS_FAILED', 'SOME_REASON_GITHUB_ADDED_LATER'])
def test_a_merge_queue_removal_wakes_the_owner_once_when_github_still_has_the_pr_open(api, monkeypatch, reason):
    """The webhook sends nothing for a removal that is not a merge. The maintenance pass reads the pull request, and if it
    is still open at the removed head the owner is woken once, with the reason and the head. A failed read waits for the
    next pass; a redelivery says nothing new; a removal after a re-enqueue is news."""
    service, seen, fake = _github_pr(api, monkeypatch)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship the queued change', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _queue(api, task['id'], 'enqueued')
    with api.app.state.store.transaction() as c:
        queue = H._json(H.task_links(c, task['id'])[0]['detail'], {})['merge_queue']
        assert (queue['state'], queue['head']) == ('queued', 'a' * 40)
    _queue(api, task['id'], 'dequeued', reason)
    item = f"Removed from the merge queue ({reason}) at {'a' * 12}: fix and merge again"
    assert _burst(api, task['id']) == [] and seen == []
    fake['status'] = 500
    G.refresh_queue_removals(service)
    assert seen and _burst(api, task['id']) == []  # a failed read keeps the removal for the next pass
    fake['status'] = 200
    G.refresh_queue_removals(service)
    assert _burst(api, task['id']) == [item] and _sent(api) == [task['id']]
    with api.app.state.store.transaction() as c:
        assert item in c.execute('SELECT body FROM messages ORDER BY rowid DESC LIMIT 1').fetchone()[0]
        pr = H.task_links(c, task['id'])[0]
        queue = H._json(pr['detail'], {})['merge_queue']
        assert queue['state'] == 'dequeued' and 'verify' not in queue
    reads = len(seen)
    G.refresh_queue_removals(service)
    assert len(seen) == reads  # nothing is marked: nothing is read
    _queue(api, task['id'], 'dequeued', reason)  # a redelivery of the same removal
    assert _burst(api, task['id']) == []
    _queue(api, task['id'], 'enqueued')
    _queue(api, task['id'], 'dequeued', reason)  # enqueued again, removed again
    G.refresh_queue_removals(service)
    assert _burst(api, task['id']) == [item]


@pytest.mark.parametrize('reason', ['MERGE', 'ALREADY_MERGED', 'merged'])
def test_a_queue_merge_removal_that_arrives_before_closed_does_not_wake_the_owner(api, monkeypatch, reason):
    """GitHub may send a queue merge's removal before the pull request's own closed event, with merged false and a success
    reason. That is not a removal: nothing is read and nothing is sent, and the closed event is what moves the ticket."""
    service, seen, _ = _github_pr(api, monkeypatch)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship the queued change', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _queue(api, task['id'], 'enqueued')
    _queue(api, task['id'], 'dequeued', reason)
    G.refresh_queue_removals(service)
    assert seen == [] and _burst(api, task['id']) == []
    with api.app.state.store.transaction() as c:
        queue = H._json(H.task_links(c, task['id'])[0]['detail'], {})['merge_queue']
    assert (queue['state'], queue['reason'], 'verify' in queue) == ('dequeued', reason, False)
    # A removal that arrives on a merged pull request is not news either, whatever its reason.
    _queue(api, task['id'], 'enqueued')
    _queue(api, task['id'], 'dequeued', 'CHECKS_FAILED', merged=True)
    G.refresh_queue_removals(service)
    assert seen == [] and _burst(api, task['id']) == [merged('tico#412', PR)]  # the merge's wake, not the removal's


def test_a_removal_for_a_pr_github_reads_as_merged_does_not_wake_the_owner(api, monkeypatch):
    """A removal for a reason that is not a merge, on a pull request that the maintenance read finds merged: the merge is
    news through its closed event, not through the removal. Nothing is sent, and the removal is settled with no second read."""
    service, seen, _ = _github_pr(api, monkeypatch, state='closed', merged=True)
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship the queued change', 'body': 'x', 'links': [PR]})
    hook(api, 'pull_request', pr_event('opened', head={'sha': 'a' * 40}))
    _queue(api, task['id'], 'enqueued')
    _queue(api, task['id'], 'dequeued', 'CI_FAILURE')
    assert _burst(api, task['id']) == []
    G.refresh_queue_removals(service)
    assert seen and _burst(api, task['id']) == []
    with api.app.state.store.transaction() as c:
        queue = H._json(H.task_links(c, task['id'])[0]['detail'], {})['merge_queue']
    assert (queue['state'], queue['reason'], 'verify' in queue) == ('dequeued', 'CI_FAILURE', False)
    reads = len(seen)
    G.refresh_queue_removals(service)
    assert len(seen) == reads


def _done(api, task_id):
    with api.app.state.store.transaction() as c:
        H.task_done(c, "human:ana", task_id, "Done.")


def _merged(api):
    return hook(api, 'pull_request', pr_event('closed', merged=True, merge_commit_sha='c' * 40, merged_at=H.now()))


def _flush(api):
    """Send every PR burst now, as the scheduler does once each is due."""
    from backend import repositories as R

    with api.app.state.store.transaction() as c:
        for row in c.execute("SELECT key FROM registry_metadata WHERE key LIKE 'github-task-wake:%'").fetchall():
            R.save_metadata(c, row['key'], {**R.metadata(c, row['key']), 'due': H.shift(H.now(), seconds=-1)})
        return G.flush_wakes(c)


def test_a_merge_wakes_the_bot_whose_task_has_the_pull_request_once(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship checkout', 'body': 'x', 'links': [PR]})
    _merged(api)
    assert len(_burst(api, task['id'])) == 1 and 'merged' in _burst(api, task['id'])[0]
    assert _flush(api) == [task['id']]
    with api.app.state.store.transaction() as c:
        body = c.execute(
            'SELECT body FROM messages WHERE recipient_id=? ORDER BY rowid DESC LIMIT 1', (task['owner_id'],)
        ).fetchone()[0]
    assert 'Carry this task on' in body
    # GitHub redelivers the merge, and a read refresh finds the same merge: neither wakes the task again.
    _merged(api)
    with api.app.state.store.transaction() as c:
        G.pull_request(
            c, pr_event('refresh', merged=True, merge_commit_sha='c' * 40, merged_at=H.now()), read_refresh=True
        )
    assert _burst(api, task['id']) == []
    assert _flush(api) == []


def test_a_merge_does_not_wake_a_task_a_person_owns(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship checkout', 'body': 'x', 'links': [PR]})
    with api.app.state.store.transaction() as c:
        H.task_reassign(c, "human:ana", task['id'], "human:ana", "I will do it.")
    _merged(api)
    assert _burst(api, task['id']) == []


def test_a_merge_does_not_wake_a_done_task(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship checkout', 'body': 'x', 'links': [PR]})
    _done(api, task['id'])
    _merged(api)
    assert _burst(api, task['id']) == []


def test_a_stale_event_that_reopens_a_merged_pr_does_not_rearm_its_merge_wake(api):
    task = post(api, 'tasks', {'owner': 'cpo', 'title': 'Ship checkout', 'body': 'x', 'links': [PR]})
    _merged(api)
    assert _flush(api) == [task['id']]
    # An older edit, delivered late, still says the PR is open: the link reopens, and the next merge reading is no news.
    hook(api, 'pull_request', pr_event('edited'))
    _merged(api)
    assert _burst(api, task['id']) == []
    assert _flush(api) == []


def test_a_published_release_tells_automations_once_and_names_no_task(api, monkeypatch):
    """`release.published` (event_catalog) fires once per release of the server's release repository: GitHub
    redelivering the webhook, a draft, a prerelease and another repository's release fire nothing."""
    from backend import automations
    from backend import event_catalog as CAT

    assert CAT.canonical("release.published") == "release.published"
    assert CAT.canonical("release_published") == "release.published"
    heard = []
    monkeypatch.setattr(
        automations,
        "on_event",
        lambda c, type_, subject, data, actor, access=None: heard.append((type_, subject, data())),
    )
    repo = "ticoteam/tico"
    api.app.state.store.settings.release_repo = repo

    def publish(tag, full_name=repo, draft=False):
        url = f"https://github.com/{full_name}/releases/tag/{tag}"
        rel = {"tag_name": tag, "draft": draft, "html_url": url}
        return hook(api, "release", {"action": "published", "repository": {"full_name": full_name}, "release": rel})

    publish("v1.4.0")
    publish("v1.4.0")  # GitHub redelivers
    publish("v1.5.0", draft=True)
    publish("v1.5.0-rc.1")
    publish("v9.0.0", full_name="ticoteam/site")
    releases = [h for h in heard if h[0] == "release.published"]
    url = f"https://github.com/{repo}/releases/tag/v1.4.0"
    assert releases == [("release.published", f"{repo}@v1.4.0", {"release": "v1.4.0", "repository": repo, "url": url})]
