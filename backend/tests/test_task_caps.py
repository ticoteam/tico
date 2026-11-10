"""Task caps (redesign/CONTRACT.md §1, backend/task_caps.py): how many tasks a teammate may send a manager a day.

The org here: ops reports to coo, coo to Ana (the owner). A cap on (ops, Ana) limits the tasks ops creates for Ana."""

from backend import task_caps
from backend.store import H
from backend.tests.helpers.api import get, headers, post  # noqa: F401  (the api fixture)
from backend.tests.helpers.migrations import restore_config, forget_from
from backend.tests.helpers.task_caps import as_bot


def report(api, bot, boss):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to=? WHERE bot=?", (boss, bot))


def org(api):
    """ops under coo under Ana, and ops signing in as itself (`ops-test`)."""
    report(api, "coo", "human:ana")
    report(api, "ops", "coo")  # a bot names its boss by slug; a person as human:<id>
    as_bot(api, "ops", "ops-test")


def put_cap(api, body, token="ana-test", expected=200):
    r = api.put("/api/v2/task-caps", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def task(api, token, title, owner, expected=200):
    """A task from `token`'s teammate to `owner`. Returns the reply's JSON, for a refusal too."""
    r = api.post(
        "/api/v2/tasks", json={"title": title, "body": "Scenario task.", "owner": owner}, headers=headers(token)
    )
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
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": None})  # null removes the cap
    task(api, "ops-test", "Task 3", owner="ana")


def test_a_zero_cap_blocks_the_pair_and_names_the_manager_to_go_through_while_own_work_passes(api):
    org(api)
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": 0})
    refused = task(api, "ops-test", "Straight to Ana", owner="ana", expected=409)
    assert refused["cap"] == 0 and refused["via"] == "bot:coo"
    task(api, "ops-test", "Ops's own work", owner="ops")  # creator is the owner


def test_closing_a_task_before_the_receiver_acts_frees_its_slot(api):
    org(api)
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": 1})
    first = task(api, "ops-test", "First", owner="ana")
    task(api, "ops-test", "Second", owner="ana", expected=409)
    closing = api.get(f"/api/v2/tasks/{first['id']}", headers=headers("ops-test")).json()
    version = (closing.get("task") or closing)["version"]
    r = api.post(
        f"/api/v2/tasks/{first['id']}", json={"version": version, "status": "closed"}, headers=headers("ops-test")
    )
    assert r.status_code == 200, r.text
    assert rows(api)[("bot:ops", "person:ana")]["count"] == 0
    task(api, "ops-test", "Second", owner="ana")


def _ok(api, rid, token="ana-test", expected=200):
    """The person opens the proposal's preview in Tico and clicks OK on it (backend/task_asks.py)."""
    from backend import task_asks

    shown = get(api, f"oks/{rid}", token=token)["shown_hash"] if expected == 200 else "unseen"
    with api.app.state.store.read() as c:
        task = task_asks.task_of(c, rid)
    return post(api, f"tasks/{task}/asks/{rid}/ok", {"shown_hash": shown}, token=token, expected=expected)


def test_a_bot_asks_for_its_cap_as_an_ok_a_person_gives_and_the_hub_applies_it(api):
    org(api)
    asked = api.put(
        "/api/v2/task-caps",
        json={"sender": "bot:ops", "receiver": "person:ana", "per_day": 1},
        headers=headers("ops-test"),
    )
    assert asked.status_code == 200 and asked.json()["status"] == "pending"
    aid = asked.json()["approval"]
    assert ("bot:ops", "person:ana") not in rows(api)  # nothing applied yet
    assert [p["id"] for p in get(api, "task-caps")["proposals"]] == [aid]
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": 5}, token="cara-test", expected=403)
    _ok(api, aid, token="cara-test", expected=409)  # asked of the top of the org, not of her
    _ok(api, aid)
    assert rows(api)[("bot:ops", "person:ana")]["per_day"] == 1
    assert get(api, "task-caps")["proposals"] == []
    audited = api.get("/api/v2/audit", params={"object": "task_cap:bot:ops>person:ana"}, headers=headers()).json()[
        "rows"
    ]
    applied = [row for row in audited if row["via"] == f"approval:{aid}"]
    assert len(applied) == 1 and applied[0]["before"] is None
    assert applied[0]["after"] == {"sender": "bot:ops", "receiver": "person:ana", "per_day": 1}
    task(api, "ops-test", "Only one", owner="ana")
    task(api, "ops-test", "Over", owner="ana", expected=409)


def test_a_cap_names_a_manager_above_the_sender_and_a_stranger_is_not_found(api):
    org(api)
    put_cap(api, {"sender": "bot:ops", "receiver": "bot:ops", "per_day": 1}, expected=422)  # no cap on one's own work
    put_cap(api, {"sender": "bot:ops", "receiver": "bot:cpo", "per_day": 1}, expected=422)  # a peer, not a manager
    put_cap(api, {"sender": "bot:ops", "receiver": "person:nobody", "per_day": 1}, expected=404)


def test_a_hand_over_adds_a_send_and_never_takes_the_first_one_out_of_the_count(api):
    org(api)
    put_cap(api, {"sender": "bot:ops", "receiver": "person:ana", "per_day": 1})
    first = task(api, "ops-test", "First", owner="ana")
    shown = api.get(f"/api/v2/tasks/{first['id']}", headers=headers()).json()
    r = api.post(
        f"/api/v2/tasks/{first['id']}",
        json={"version": (shown.get("task") or shown)["version"], "owner": "coo"},
        headers=headers(),
    )  # Ana hands it to coo
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        senders = [
            row[0]
            for row in c.execute("SELECT sender FROM task_cap_log WHERE task_id=? ORDER BY sent_at", (first["id"],))
        ]
    assert senders == ["bot:ops", "human:ana"]  # both sends kept
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
    task(api, "ops-test", "Twice", owner="ana", expected=409)  # a new key is a new send


def test_the_upgrade_drops_the_retired_send_table_and_the_log_keeps_counting(api):
    """An install from before the drop still has task_cap_sends. Migration 0004 removes it at the next start, and a send
    the cap counts (task_cap_log) still counts after it."""
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("CREATE TABLE task_cap_sends(task_id TEXT PRIMARY KEY, sender TEXT, receiver TEXT, sent_at TEXT)")
        c.execute(
            "INSERT INTO task_cap_sends(task_id, sender, receiver, sent_at) VALUES('t0','bot:ops','human:ana',?)",
            (H.now(),),
        )
        # As a file from before 0004 has it: that migration and every later one not run yet. The reruns read
        # bot_config.config (0061), which 0070 dropped: the column goes back first, as on a file from before 0070.
        restore_config(c)
        forget_from(c, "0004")
    store.initialize(seed_market=False)
    with store.transaction() as c:
        tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "task_cap_sends" not in tables and "task_cap_log" in tables
        assert c.execute("SELECT 1 FROM schema_migrations WHERE version='0004_drop_task_cap_sends'").fetchone()
        task_caps.record(c, "t1", "bot:ops", "human:ana")
        assert task_caps.tally(c, "bot:ops", "human:ana") == (1, 0)
