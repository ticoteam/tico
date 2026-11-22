from backend import files, relationship_runs as runs
from backend.entities import store as entities
from backend.store import H
from backend.tests.helpers.api import assign, claim, headers, post, runner
from clients.sessions import entity_lane


def test_relationship_worker_claims_independent_lanes_and_reads_only_its_relationship(api):
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        # The relationship core change provides this column on the branch that this one will follow.
        if "entity_id" not in {row["name"] for row in c.execute("PRAGMA table_info('tasks')")}:
            c.execute("ALTER TABLE tasks ADD COLUMN entity_id TEXT")
        owner = auth.owner_identity(c)
        scopes = [
            entities.upsert(c, owner.actor, "organization", name=name, match={})["id"] for name in ("North", "South")
        ]
        for index, entity in enumerate(scopes):
            runs.enqueue(c, auth, owner, entity, f"Summarize relationship {index}")
        runs.enqueue(c, auth, owner, scopes[0], "Next turn on the first relationship")

    tasks = [
        post(api, "tasks", {"title": name, "body": "Update requested.", "owner": "human:ana", "private": False})["id"]
        for name in ("North task", "South task")
    ]
    pages = [files.new_id(), files.new_id()]
    with store.transaction() as c:
        for entity, tid, fid in zip(scopes, tasks, pages, strict=True):
            c.execute("UPDATE tasks SET entity_id=? WHERE id=?", (entity, tid))
            c.execute(
                "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,first_activity_at,last_activity_at) "
                "VALUES(?,'ops','bot',?,'Memory','page','text/html','',?,?)",
                (fid, fid, H.now(), H.now()),
            )
            c.execute("INSERT INTO pages(file_id,created_at,updated_at) VALUES(?,?,?)", (fid, H.now(), H.now()))
            c.execute(
                "INSERT INTO page_links(id,page_id,target,role,created_by,created_at) VALUES(?,?,?,'memory',?,?)",
                (H.new_id(), fid, entity_lane(entity), owner.actor, H.now()),
            )
    assert api.get(f"/api/v2/bots/{runs.BOT}", headers=headers("cara-test")).status_code == 404
    machine = runner(api)
    assign(api, machine, runs.BOT)
    ready = {
        "schema_version": 1,
        "isolated": True,
        "bots": {runs.BOT: {"ready": True, "repository_present": True, "configuration_valid": True}},
        "runtimes": {"pi": {"installed": True, "authenticated": "ready"}},
    }

    def heartbeat(relationship_lanes):
        return post(
            api,
            "runners/heartbeat",
            {
                "version": "test",
                "platform": "test",
                "readiness": {**ready, **({"relationship_lanes": True} if relationship_lanes else {})},
                "capabilities": ["task_lanes_v2"],
            },
            token=machine["token"],
        )

    heartbeat(False)
    assert claim(api, machine, runs.BOT) is None
    heartbeat(True)
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
        assert api.get(f"/api/v2/tasks/{tasks[1 - own]}", headers=token).status_code == 404
        assert api.get(f"/api/v2/pages/{pages[own]}", headers=token).status_code == 200
        assert api.get(f"/api/v2/pages/{pages[1 - own]}", headers=token).status_code == 404
        assert api.get(f"/api/v2/threads/{attempts[1 - index]['conversation']['id']}", headers=token).status_code == 404
        assert api.post(f"/api/v2/tasks/{tasks[own]}", json={"title": "Overwrite"}, headers=token).status_code == 403
        for path in ("tasks", "threads/search", "files/search", "credential-runtime"):
            assert api.get(f"/api/v2/{path}", headers=token).status_code == 403
