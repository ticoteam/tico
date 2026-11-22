import json

from backend import model_subscriptions, relationship_runs as runs, threads
from backend.auth import Identity
from backend.entities import store as entities
from backend.store import H
from backend.tests.helpers.api import assign, claim, headers, post, runner
from clients.sessions import entity_lane


def worker(api):
    machine = runner(api)
    assign(api, machine, runs.BOT)
    return machine


def heartbeat(api, machine, *, relationship_lanes=True, authenticated="ready"):
    return post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "mac",
            "readiness": {
                "schema_version": 1,
                "isolated": False,
                **({"relationship_lanes": True} if relationship_lanes else {}),
                "bots": {
                    runs.BOT: {
                        "ready": True,
                        "repository_present": False,
                        "configuration_valid": True,
                        "runtime": "claude",
                        "sign_in": "ready",
                    }
                },
                "runtimes": {"claude": {"installed": True, "authenticated": authenticated}},
            },
            "capabilities": ["task_lanes_v2"],
        },
        token=machine["token"],
    )


def memory(api, entity, name):
    fid = post(api, "pages", {"name": name, "text": "<p>Relationship notes</p>"})["page"]["file_id"]
    post(api, f"pages/{fid}/links", {"target": entity_lane(entity), "role": "memory"})
    return fid


def test_relationship_worker_claims_independent_lanes_and_reads_only_its_relationship(api, monkeypatch):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        owner = auth.owner_identity(c)
        scopes = [
            entities.upsert(c, owner.actor, "organization", name=name, match={})["id"] for name in ("North", "South")
        ]
        for index, entity in enumerate(scopes):
            runs.enqueue(c, auth, owner, entity, f"Summarize relationship {index}")
        runs.enqueue(c, auth, owner, scopes[0], "Next turn on the first relationship")

    tasks = [
        post(
            api,
            "tasks",
            {"title": name, "body": "Update requested.", "owner": "human:ana", "private": False, "entity": entity},
        )["id"]
        for name, entity in zip(("North task", "South task"), scopes, strict=True)
    ]
    pages = [memory(api, entity, f"memory-{i}.html") for i, entity in enumerate(scopes)]
    assert api.get(f"/api/v2/bots/{runs.BOT}", headers=headers("cara-test")).status_code == 404
    machine = worker(api)
    heartbeat(api, machine, relationship_lanes=False)
    assert claim(api, machine, runs.BOT) is None
    # The runner last reported the bot ready via a company subscription, which this worker never receives.
    monkeypatch.setattr(model_subscriptions, "covered_runtimes", lambda *_: {"claude": "claude"})
    heartbeat(api, machine, authenticated="missing")
    assert claim(api, machine, runs.BOT) is None
    heartbeat(api, machine)
    attempts = [claim(api, machine, runs.BOT), claim(api, machine, runs.BOT)]
    assert all(attempts)
    assert {a["lane"] for a in attempts} == {entity_lane(entity) for entity in scopes}
    assert claim(api, machine, runs.BOT) is None

    for index, attempt in enumerate(attempts):
        own = [entity_lane(e) for e in scopes].index(attempt["lane"])
        token = headers(attempt["token"])
        assert api.get(f"/api/v2/tasks/{tasks[own]}", headers=token).status_code == 200
        thread = attempt["conversation"]["id"]
        timeline = api.get(f"/api/v2/threads/{thread}/timeline", headers=token)
        assert timeline.status_code == 200
        assert attempt["message"]["body"].startswith(f"Summarize relationship {own}")
        assert [msg["id"] for msg in attempt["history"]] == [attempt["message"]["id"]]
        assert api.get(f"/api/v2/tasks/{tasks[1 - own]}", headers=token).status_code == 404
        assert api.get(f"/api/v2/pages/{pages[own]}", headers=token).status_code == 200
        assert api.get(f"/api/v2/pages/{pages[1 - own]}", headers=token).status_code == 404
        assert api.get(f"/api/v2/threads/{attempts[1 - index]['conversation']['id']}", headers=token).status_code == 404
        assert api.post(f"/api/v2/tasks/{tasks[own]}", json={"title": "Overwrite"}, headers=token).status_code == 403
        post(api, f"attempts/{attempt['id']}/renew", {}, token=attempt["token"], expected=403)
        for path in ("tasks", "threads/search", "files/search", "credential-runtime"):
            assert api.get(f"/api/v2/{path}", headers=token).status_code == 403


def test_relationship_reads_use_current_sponsor_grants_and_strip_other_page_refs(api):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        owner = auth.owner_identity(c)
        north, south = [
            entities.upsert(c, owner.actor, "organization", name=name, match={})["id"] for name in ("North", "South")
        ]
        c.execute("UPDATE entity_sources SET visibility='company' WHERE entity_id=?", (north,))
        runs.enqueue(c, auth, auth.identity_for_actor(c, "human:cara"), north, "Summarize")
    task = post(
        api,
        "tasks",
        {
            "title": "Renewal",
            "body": "Review the renewal terms.",
            "owner": "ana",
            "private": True,
            "readers": ["cara"],
            "entity": north,
        },
    )
    tid = task["id"]
    north_page, south_page = memory(api, north, "north.html"), memory(api, south, "south.html")
    with store.transaction() as c:
        # Exercise stored audiences even though new memory links require a company-visible page.
        c.execute("UPDATE pages SET audience=? WHERE file_id=?", (json.dumps({"people": ["cara"]}), north_page))
        room = H.conversation(c, H.task(c, tid)["conversation_id"])
        H._write_message(
            c, owner.actor, "", "Renewal context", room, "comment", {"materials": {"file_id": south_page}}, None, None
        )
    machine = worker(api)
    heartbeat(api, machine)
    attempt = claim(api, machine, runs.BOT)
    token = headers(attempt["token"])
    shown = api.get(f"/api/v2/tasks/{tid}", headers=token)
    assert shown.status_code == 200
    assert "Renewal context" in shown.text
    assert south_page not in shown.text
    for path in (f"pages/{north_page}", f"files/{north_page}", f"files/{north_page}/versions/1"):
        assert api.get(f"/api/v2/{path}", headers=token).status_code == 200
    # Removing grants takes effect for the same attempt even after it has read the objects.
    with store.transaction() as c:
        c.execute("UPDATE tasks SET readers='[]' WHERE id=?", (tid,))
        c.execute("UPDATE pages SET audience=? WHERE file_id=?", (json.dumps({"people": ["ben"]}), north_page))
    for path in (f"tasks/{tid}", f"pages/{north_page}", f"files/{north_page}/versions/1"):
        assert api.get(f"/api/v2/{path}", headers=token).status_code == 404


def test_relationship_source_threads_apply_scope_and_sponsor_in_python_and_sql(api):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        owner = auth.owner_identity(c)
        north, south = [
            entities.upsert(c, owner.actor, "organization", name=name, match={})["id"] for name in ("North", "South")
        ]
    own, other = [
        post(
            api,
            "tasks",
            {
                "title": title,
                "body": "Review the renewal terms.",
                "owner": "ana",
                "private": True,
                "entity": entity,
            },
        )["id"]
        for title, entity in (("North renewal", north), ("South renewal", south))
    ]
    with store.transaction() as c:
        source = threads.create(c, owner, audience="company", title="Source")["id"]
        unrelated = threads.create(c, owner, audience="company", title="Unrelated")["id"]
        message = threads.post(c, owner, source, "In scope", refs={"task": own})["id"]
        threads.post(c, owner, source, "Outside scope", refs={"task": other})
        sub = threads.create(c, owner, parent_message_id=message)["id"]
        c.execute("UPDATE conversations SET task_id=? WHERE id=?", (other, sub))
        # Outside intake supplies these columns. Model its stored thread without depending on its delivery path.
        if not any(row["name"] == "entity_id" for row in c.execute("PRAGMA table_info(conversations)")):
            c.execute("ALTER TABLE conversations ADD COLUMN entity_id TEXT")
        c.execute("UPDATE conversations SET entity_id=?,audience='entity' WHERE id=?", (north, source))
        runs.enqueue(c, auth, owner, north, "Summarize source", thread_id=source)
    machine = worker(api)
    heartbeat(api, machine)
    attempt = claim(api, machine, runs.BOT)
    token = headers(attempt["token"])
    timeline = api.get(f"/api/v2/threads/{source}/timeline", headers=token)
    assert timeline.status_code == 200
    assert "In scope" in timeline.text and "Outside scope" not in timeline.text
    for tid in (sub, unrelated):
        assert api.get(f"/api/v2/threads/{tid}", headers=token).status_code == 404
    with store.read_transaction() as c:
        view = threads.Viewer(auth, Identity(runs.ACTOR, "bot", attempt_id=attempt["id"]))
        expected = {source, attempt["conversation"]["id"]}
        assert threads.readable_ids(c, view, [*expected, sub, unrelated]) == expected
        assert {r["id"] for r in c.execute("SELECT id FROM conversations WHERE " + threads.readable_sql(c, view))} == (
            expected
        )
