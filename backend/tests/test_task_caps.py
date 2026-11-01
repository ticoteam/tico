"""Task caps (redesign/CONTRACT.md §1, backend/task_caps.py): how many tasks a teammate may send a manager a day.

The org here: ops reports to coo, coo to Ana (the owner). A cap on (ops, Ana) limits the tasks ops creates for Ana."""
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (the api fixture)


def report(api, bot, boss):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to=? WHERE bot=?", (boss, bot))


def as_bot(api, slug, token):
    """The bot signs in with its own agent credential, as the hub's agents do: one agents row per bot."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
                  (slug, "test", "x-" + slug, H.now(), "human:ana"))
    api.app.state.store.settings.test_identities[token] = Identity(f"bot:{slug}", "bot", agent="test")


def org(api):
    """ops under coo under Ana, and ops signing in as itself (`ops-test`)."""
    report(api, "coo", "human:ana")
    report(api, "ops", "coo")                 # a bot names its boss by slug; a person as human:<id>
    as_bot(api, "ops", "ops-test")


def put_cap(api, body, token="ana-test", expected=200):
    r = api.put("/api/v2/task-caps", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def task(api, token, title, owner, expected=200):
    """A task from `token`'s teammate to `owner`. Returns the reply's JSON, for a refusal too."""
    r = api.post("/api/v2/tasks", json={"title": title, "body": "Scenario task.", "owner": owner}, headers=headers(token))
    assert r.status_code == expected, r.text
    data = r.json()
    return data.get("task", data)


def rows(api):
    return {(row["sender"], row["receiver"]): row for row in get(api, "task-caps")["caps"]}


def test_a_task_over_the_cap_is_refused_with_the_flat_payload_and_writes_no_row(api):
    org(api)
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": 2})
    task(api, "ops-test", "Task 1", owner="ana")
    task(api, "ops-test", "Task 2", owner="ana")
    refused = task(api, "ops-test", "Task 3", owner="ana", expected=409)
    assert refused["error"] == "task_cap" and refused["cap"] == 2 and refused["count"] == 2
    assert refused["receiver"] == "person:ana" and "via" not in refused
    assert [t["title"] for t in refused["today"]] == ["Task 1", "Task 2"]
    assert all({"id", "title", "status"} <= set(t) for t in refused["today"])
    assert refused["message"].endswith("Nothing was queued.")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title='Task 3'").fetchone()[0] == 0
    row = rows(api)[("bot:ops", "person:ana")]
    assert row["per_day"] == 2 and row["count"] == 2 and row["closed"] == 0
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": None})         # null removes the cap
    task(api, "ops-test", "Task 3", owner="ana")


def test_a_zero_cap_blocks_the_pair_and_names_the_manager_to_go_through_while_own_work_passes(api):
    org(api)
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": 0})
    refused = task(api, "ops-test", "Straight to Ana", owner="ana", expected=409)
    assert refused["cap"] == 0 and refused["via"] == "bot:coo"
    task(api, "ops-test", "Ops's own work", owner="ops")                                   # creator is the owner


def test_closing_a_task_before_the_receiver_acts_frees_its_slot(api):
    org(api)
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": 1})
    first = task(api, "ops-test", "First", owner="ana")
    task(api, "ops-test", "Second", owner="ana", expected=409)
    closing = api.get(f"/api/v2/tasks/{first['id']}", headers=headers("ops-test")).json()
    version = (closing.get("task") or closing)["version"]
    r = api.post(f"/api/v2/tasks/{first['id']}", json={"version": version, "status": "closed"}, headers=headers("ops-test"))
    assert r.status_code == 200, r.text
    assert rows(api)[("bot:ops", "person:ana")]["count"] == 0
    task(api, "ops-test", "Second", owner="ana")


def test_a_bot_asks_for_its_cap_as_an_approval_a_person_decides_and_the_hub_applies_it(api):
    org(api)
    asked = api.put("/api/v2/task-caps", json={"sender": "bot:ops", "receiver": "person:ana", "per_day": 1},
                    headers=headers("ops-test"))
    assert asked.status_code == 200 and asked.json()["status"] == "pending"
    aid = asked.json()["approval"]
    assert ("bot:ops", "person:ana") not in rows(api)                                   # nothing applied yet
    assert [p["id"] for p in get(api, "task-caps")["proposals"]] == [aid]
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": 5}, token="cara-test", expected=403)
    post(api, f"approvals/{aid}", {"decision": "approved"}, token="cara-test", expected=403)   # not hers to decide
    assert post(api, f"approvals/{aid}", {"decision": "approved"})["decision"] == "approved"
    assert rows(api)[("bot:ops", "person:ana")]["per_day"] == 1
    assert get(api, "task-caps")["proposals"] == []
    audited = api.get("/api/v2/audit", params={"object": "task_cap:bot:ops>person:ana"}, headers=headers()).json()["rows"]
    applied = [row for row in audited if row["via"] == f"approval:{aid}"]
    assert len(applied) == 1 and applied[0]["before"] is None
    assert applied[0]["after"] == {"sender": "bot:ops", "receiver": "person:ana", "per_day": 1}
    task(api, "ops-test", "Only one", owner="ana")
    task(api, "ops-test", "Over", owner="ana", expected=409)


def test_a_cap_names_a_manager_above_the_sender_and_a_stranger_is_not_found(api):
    org(api)
    put_cap(api, {"sender": "bot:ops", "receiver": "bot:ops", "per_day": 1}, expected=422)     # no cap on one's own work
    put_cap(api, {"sender": "bot:ops", "receiver": "bot:cpo", "per_day": 1}, expected=422)     # a peer, not a manager
    put_cap(api, {"sender": "bot:ops", "receiver": "person:nobody", "per_day": 1}, expected=404)


def test_a_hand_over_adds_a_send_and_never_takes_the_first_one_out_of_the_count(api):
    org(api)
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": 1})
    first = task(api, "ops-test", "First", owner="ana")
    shown = api.get(f"/api/v2/tasks/{first['id']}", headers=headers()).json()
    r = api.post(f"/api/v2/tasks/{first['id']}", json={"version": (shown.get("task") or shown)["version"], "owner": "coo"},
                 headers=headers())                                                     # Ana hands it to coo
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        senders = [row[0] for row in c.execute("SELECT sender FROM task_cap_log WHERE task_id=? ORDER BY sent_at",
                                                 (first["id"],))]
    assert senders == ["bot:ops", "human:ana"]                                         # both sends kept
    assert rows(api)[("bot:ops", "person:ana")]["count"] == 1
    task(api, "ops-test", "Second", owner="ana", expected=409)


def test_a_retry_with_a_saved_idempotency_key_replays_instead_of_hitting_the_cap(api):
    org(api)
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": 1})
    body = {"title": "Once", "body": "Scenario task.", "owner": "ana"}
    first = api.post("/api/v2/tasks", json=body, headers=headers("ops-test", "retry-1"))
    again = api.post("/api/v2/tasks", json=body, headers=headers("ops-test", "retry-1"))
    assert first.status_code == again.status_code == 200, again.text
    assert again.json() == first.json()
    task(api, "ops-test", "Twice", owner="ana", expected=409)                         # a new key is a new send


def test_the_upgrade_keeps_the_sends_already_counted(tmp_path):
    conn = H.connect(tmp_path / "hub.db")
    try:
        conn.execute("DROP TABLE task_cap_log")
        conn.execute("INSERT INTO task_cap_sends(task_id, sender, receiver, sent_at) VALUES('t1','bot:ops','human:ana',?)",
                     (H.now(),))
        conn.execute(f"PRAGMA user_version={H.MIGRATIONS.index(H.TASK_CAP_LOG_SCHEMA)}")
        H.migrate(conn)
        assert [tuple(r) for r in conn.execute("SELECT task_id, sender, receiver FROM task_cap_log")] == \
            [("t1", "bot:ops", "human:ana")]
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(H.MIGRATIONS)
    finally:
        conn.close()
