"""Service keys: another system files, updates, closes and reopens its tasks with one, and reaches nothing else."""

from backend.tests.test_api import api, get, headers, post  # noqa: F401  (the api fixture)


def inbound(api, secret, expected=200, **body):
    r = api.post("/api/v2/inbound/tasks", json=body, headers={"Authorization": "Bearer " + secret})
    assert r.status_code == expected, r.text
    return r.json()


def test_one_task_per_piece_of_work_whatever_order_the_calls_come_in(api):
    secret = post(api, "service-keys", {"label": "Billing backend"})["key"]
    # Done over there before any task existed: nothing is filed.
    assert inbound(api, secret, key="report-40", close=True) == {"task": None, "created": False, "changed": False}
    work = {"key": "report-41", "owner": "ben@acme.example", "title": "Review the weekly report for Acme",
            "body": "The report is ready."}
    first = inbound(api, secret, **work)
    assert set(first["task"]) == {"id"}
    task = get(api, "tasks/" + first["task"]["id"])["task"]
    assert first["created"] and (task["owner"], task["requester"]) == ("human:ben", "keeper")
    assert task["body"] == "The report is ready.\n\n_Filed by Billing backend (service key)._"
    again = inbound(api, secret, **work)
    assert (again["task"]["id"], again["created"], again["changed"]) == (task["id"], False, False)
    # Another piece of work may share the title and the owner: the pair is what makes it one task.
    other = inbound(api, secret, **{**work, "key": "report-42"})
    assert other["created"] and other["task"]["id"] != task["id"]
    changed = inbound(api, secret, key="report-41", body="The report is ready. Two charts moved.", labels=["reports"])
    assert changed["changed"] and changed["task"] == {"id": task["id"]}
    updated = get(api, "tasks/" + task["id"])["task"]
    assert updated["labels"] == ["reports"]
    assert updated["body"].startswith("The report is ready. Two charts moved.")
    closed = inbound(api, secret, key="report-41", close=True, note="Approved in billing.")
    assert closed["changed"] and get(api, "tasks/" + task["id"])["task"]["status"] == "closed"
    assert not inbound(api, secret, key="report-41", close=True)["changed"]
    reopened = inbound(api, secret, key="report-41", owner="human:cara")
    assert reopened["changed"] and reopened["task"] == {"id": task["id"]}
    updated = get(api, "tasks/" + task["id"])["task"]
    assert (updated["status"], updated["owner"]) == ("open", "human:cara")
    inbound(api, secret, 422, key="report-43", owner="nobody@acme.example", title="Review the report", body="x")
    with api.app.state.store.read() as c:
        used = c.execute("SELECT COUNT(*) FROM events WHERE action='service_key.use'").fetchone()[0]
    assert used == 8                # every call that was answered, including the ones that changed nothing


def test_a_service_key_reaches_nothing_else_and_a_revoked_one_nothing_at_all(api):
    post(api, "service-keys", {"label": "Mine"}, token="cara-test", expected=403)       # a member
    made = post(api, "service-keys", {"label": "Billing backend"}, token="ben-test")     # an admin
    listed = get(api, "service-keys")["keys"]
    assert [k["id"] for k in listed] == [made["id"]] and made["key"] not in str(listed)
    sk = {"Authorization": "Bearer " + made["key"]}
    for method, path in (("get", "/api/v2/tasks"), ("post", "/api/v2/sql")):
        r = api.request(method.upper(), path, headers=sk, json={} if method == "post" else None)
        assert r.status_code == 403, path
    # A person's session or personal token is not a service key, and SQL never shows a key's hash.
    token = post(api, "me/tokens", {"label": "script"})["token"]
    for person in (headers(), headers(token)):
        assert api.post("/api/v2/inbound/tasks", json={"key": "x", "close": True}, headers=person).status_code == 403
    assert api.post("/api/v2/sql", json={"sql": "SELECT key_hash FROM service_keys"}, headers=headers()).status_code == 422
    post(api, "service-keys/" + made["id"] + "/revoke", {})
    assert api.post("/api/v2/inbound/tasks", json={"key": "x", "close": True}, headers=sk).status_code == 401


def test_private_work_is_neither_returned_nor_changed_by_its_service_key(api):
    secret = post(api, "service-keys", {"label": "Reports"})["key"]
    task = inbound(api, secret, key="report", owner="human:ben", title="Review the report", body="Please review.")["task"]
    with api.app.state.store.transaction() as c:
        # Privacy belongs to the separate task gate; the service boundary tolerates older schemas.
        if "private" not in {r[1] for r in c.execute("PRAGMA table_info(tasks)")}:
            c.execute("ALTER TABLE tasks ADD COLUMN private INTEGER NOT NULL DEFAULT 0")
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (task["id"],))
        before = dict(c.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
    for body in ({}, {"body": "Overwrite", "owner": "human:cara"}, {"close": True}, {"step": "open"}):
        refused = inbound(api, secret, 403, key="report", **body)
        assert "Please review" not in str(refused)
    with api.app.state.store.read() as c:
        assert dict(c.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()) == before


def test_mint_shows_the_secret_once_without_persisting_it_in_the_retry_cache(api):
    same_headers = headers()
    made = api.post("/api/v2/service-keys", json={"label": "Reports"}, headers=same_headers).json()
    retry = api.post("/api/v2/service-keys", json={"label": "Reports"}, headers=same_headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["id"] == made["id"] and "key" not in retry.json()
    with api.app.state.store.read() as c:
        for table in ("service_keys", "idempotency", "events"):
            assert made["key"] not in str([tuple(r) for r in c.execute("SELECT * FROM " + table)])


def test_an_update_key_checks_starts_and_follows_an_update_and_reaches_nothing_else(api, monkeypatch):
    import httpx
    from backend import releases
    calls = []

    def network(request):
        calls.append((request.method, request.url.path))
        if request.url.host == "updater":
            return httpx.Response(200, json={"state": "pulling", "from": "0.1.0", "to": "0.2.0", "message": ""})
        return httpx.Response(200, json={"tag_name": "v0.2.0", "html_url": "", "published_at": "", "name": "v0.2.0"})
    monkeypatch.setattr(releases, "CHECKER", releases.Checker())
    monkeypatch.setattr(releases, "TRANSPORT", httpx.MockTransport(network))
    monkeypatch.setenv("TICO_UPDATE_CHECK", "on")
    monkeypatch.setenv("TICO_RELEASES_URL", "https://releases.acme.example/latest")
    monkeypatch.setenv("TICO_UPDATER_URL", "http://updater:9000")
    monkeypatch.setenv("TICO_VERSION", "0.1.0")
    # Only the owner makes one: only the owner updates the install.
    post(api, "service-keys", {"label": "Release bot", "scope": "update"}, token="ben-test", expected=403)
    made = post(api, "service-keys", {"label": "Release bot", "scope": "update"})
    assert made["scope"] == "update"
    assert {k["id"]: k["scope"] for k in get(api, "service-keys")["keys"]} == {made["id"]: "update"}
    sk = {"Authorization": "Bearer " + made["key"]}
    assert api.post("/api/v2/system/update/check", headers=sk).json()["latest"] == "0.2.0"
    started = api.post("/api/v2/system/update", json={"version": "v0.2.0"}, headers=sk)
    assert started.status_code == 200 and started.json()["state"] == "pulling"
    status = api.get("/api/v2/system/update", headers=sk).json()
    assert status["to"] == "0.2.0" and status["running"] == "0.1.0" and isinstance(status["computers"], dict)
    assert ("POST", "/update") in calls
    with api.app.state.store.read() as c:
        started_by = c.execute("SELECT actor FROM events WHERE action='system.update.started'").fetchone()[0]
    assert started_by == "service:" + made["id"]
    assert api.get("/healthz").status_code == 200
    for method, path in (("get", "/api/v2/tasks"), ("post", "/api/v2/inbound/tasks"), ("get", "/api/v2/credentials")):
        r = api.request(method.upper(), path, headers=sk, json={} if method != "get" else None)
        assert r.status_code == 403, path
    # A tasks key does not reach the updater.
    tasks_key = {"Authorization": "Bearer " + post(api, "service-keys", {"label": "Billing"})["key"]}
    for method, path in (("post", "/api/v2/system/update"),):
        assert api.request(method.upper(), path, headers=tasks_key, json={"version": "0.2.0"}).status_code == 403
    post(api, "service-keys/" + made["id"] + "/revoke", {})
    assert api.get("/api/v2/system/update", headers=sk).status_code == 401
