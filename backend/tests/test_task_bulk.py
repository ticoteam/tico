"""A bulk change by filter (POST /api/v2/task-bulk): it selects with the task list's own filters, changes each task as
its single update would (permissions, refusals and events alike), is capped, and a dry run writes nothing."""

from backend import hubdb as H
from backend.tests.test_tasks_board import api, bot_token, get, pipeline, post  # noqa: F401


def bulk(api, filter, action, token="ana-test", expected=200, **change):
    return post(api, "task-bulk", {"filter": filter, "action": action, **change}, token=token, expected=expected)


def task(api, title, token="ana-test", **fields):
    return post(api, "tasks", {"owner_id": "cmo", "title": title, "body": "Please.", **fields}, token=token)


def label(api, t, *keys):
    return post(api, "tasks/" + t["id"], {"version": t["version"], "labels": list(keys)})


def events(api, field):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT task_id, actor, new FROM task_events WHERE field=?", (field,))]


def test_the_filter_selects_exactly_what_the_task_list_shows(api):
    typ = pipeline(api)
    launch = [label(api, task(api, f"Draft launch post {i}", type=typ["id"]), "launch") for i in range(3)]
    other_step = label(api, task(api, "Review the launch copy", type=typ["id"], step="Copy review"), "launch")
    untagged = task(api, "Draft the footer", type=typ["id"])
    general = label(api, task(api, "Draft the general note"), "launch")
    elsewhere = label(api, task(api, "Draft the partner post", owner="ops", type=typ["id"]), "launch")
    chosen = {"type": typ["id"], "step": "Draft", "label": "launch", "owner_id": "cmo"}
    listed = get(api, f"tasks?type={typ['id']}&step=Draft&label=launch&owner=cmo")["tasks"]
    assert {t["id"] for t in listed} == {t["id"] for t in launch}

    out = bulk(api, chosen, "step", step="Legal review")
    assert (out["matched"], out["changed"], out["unchanged"], out["skipped"]) == (3, 3, 0, 0)
    assert {t["id"] for t in out["tasks"]["changed"]} == {t["id"] for t in launch}
    for t in launch:
        assert get(api, "tasks/" + t["id"])["task"]["step"]["name"] == "Legal review"
    for t in (other_step, untagged, general, elsewhere):
        assert get(api, "tasks/" + t["id"])["task"]["step_id"] == t["step_id"]
    # Each task's move is its own event, made by the caller.
    legal = next(s["id"] for s in typ["steps"] if s["name"] == "Legal review")
    moved = [e for e in events(api, "step") if e["new"] == legal]
    assert len(moved) == 3 and {e["actor"] for e in moved} == {"human:ana"}

    # Run again, nothing is left to move; a filter that names nothing, or a person who is nobody, never widens.
    again = bulk(api, {**chosen, "step": "Legal review"}, "step", step="Legal review")
    assert (again["changed"], again["unchanged"]) == (0, 3)
    assert bulk(api, {}, "close", expected=422)["error"]["code"] == "filter"
    assert bulk(api, {"lane": "company"}, "close", expected=422)["error"]["code"] == "filter"
    assert bulk(api, {"owner_id": "nobody-here"}, "close", expected=422)["error"]["code"] == "owner"
    assert get(api, "tasks/" + general["id"])["task"]["status"] == "open"


def test_tags_close_and_reopen_by_filter(api):
    tasks = [label(api, task(api, f"Draft the brief {i}"), "brief") for i in range(2)]
    out = bulk(api, {"label": "brief"}, "tags", add=["Q4"], remove=["brief"])
    assert out["changed"] == 2
    from backend.tests.test_openapi_v2 import conforms
    assert conforms(out, {"$ref": "#/components/schemas/TaskBulkResult"}, get(api, "openapi.json")) is None
    assert {tuple(get(api, "tasks/" + t["id"])["task"]["labels"]) for t in tasks} == {("q4",)}
    assert bulk(api, {"label": "q4"}, "close", note="Not needed")["changed"] == 2
    assert {get(api, "tasks/" + t["id"])["task"]["status"] for t in tasks} == {"closed"}
    # A close has its way back: the same filter on closed tasks opens them again.
    assert bulk(api, {"label": "q4", "status": "closed"}, "status", status="open")["changed"] == 2
    assert {get(api, "tasks/" + t["id"])["task"]["status"] for t in tasks} == {"open"}


def test_blank_filters_never_widen_to_every_task(api):
    t = task(api, "Draft the unrelated note")
    for field in ("label", "updated_since"):
        for blank in ("", " \t "):
            out = bulk(api, {field: blank}, "close", expected=422)
            assert out["error"]["code"] == "filter"
    now = get(api, "tasks/" + t["id"])["task"]
    assert (now["status"], now["version"]) == ("open", t["version"])
    assert bulk(api, {"ids": [t["id"]], "label": " \t "}, "close")["changed"] == 1


def test_bulk_replay_rechecks_revoked_bot_read_access(api):
    t = task(api, "Draft the confidential brief", private=False)
    body = {"filter": {"ids": [t["id"]]}, "action": "close"}
    headers = {"Authorization": "Bearer priya-test", "Idempotency-Key": "bulk-replay"}
    original = api.post("/api/v2/task-bulk", json=body, headers=headers)
    assert original.status_code == 200, original.text
    assert original.json()["tasks"]["skipped"][0]["id"] == t["id"]
    assert api.post("/api/v2/task-bulk", json=body, headers=headers).json() == original.json()

    access = get(api, "bots/cmo/access")
    revoked = api.put("/api/v2/bots/cmo/access", json={
        "revision": access["revision"], "see": {"everyone": True},
        "read": {"people": ["ana"]}, "write": {"everyone": True}},
        headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "revoke-read"})
    assert revoked.status_code == 200, revoked.text
    get(api, "tasks/" + t["id"], token="priya-test", expected=403)
    replay = api.post("/api/v2/task-bulk", json=body, headers=headers)
    assert replay.status_code == 403, replay.text
    assert t["title"] not in replay.text


def test_a_dry_run_counts_the_real_outcome_and_changes_nothing(api):
    mine = [label(api, task(api, f"Call the lead {i}", owner="priya"), "calls") for i in range(2)]
    theirs = label(api, task(api, "Draft the call script"), "calls")
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM task_events").fetchone()[0]
        refusals = c.execute("SELECT count(*) FROM refusals").fetchone()[0]
    out = bulk(api, {"label": "calls"}, "status", token="priya-test", status="done", dry_run=True)
    assert out["dry_run"] is True and (out["changed"], out["skipped"]) == (2, 1)
    assert [t["id"] for t in out["tasks"]["skipped"]] == [theirs["id"]]
    for t in mine + [theirs]:
        now = get(api, "tasks/" + t["id"])["task"]
        assert (now["status"], now["version"]) == ("open", t["version"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM task_events").fetchone()[0] == before
        assert c.execute("SELECT count(*) FROM refusals").fetchone()[0] == refusals


def test_nobody_bulk_changes_a_task_they_could_not_change_one_by_one(api):
    mine = label(api, task(api, "Call the lead", owner="priya"), "calls")
    theirs = label(api, task(api, "Draft the call script"), "calls")
    # Priya moves no one else's task one at a time, so not in bulk either; her own still changes.
    post(api, "tasks/" + theirs["id"], {"version": theirs["version"], "status": "done"}, token="priya-test", expected=403)
    out = bulk(api, {"label": "calls"}, "status", token="priya-test", status="done")
    assert (out["changed"], out["skipped"]) == (1, 1)
    assert out["tasks"]["skipped"][0]["id"] == theirs["id"] and out["tasks"]["skipped"][0]["code"] == "identity"
    assert get(api, "tasks/" + mine["id"])["task"]["status"] == "done"
    assert get(api, "tasks/" + theirs["id"])["task"]["status"] == "open"

    # A bot is held to its own rights too: it closes nothing it did not ask for, and each refusal is on record.
    token = bot_token(api, "ops")
    owned = task(api, "Ship the screen", owner="ops")
    out = bulk(api, {"owner_id": "ops"}, "close", token=token)
    assert out["changed"] == 0 and owned["id"] in {t["id"] for t in out["tasks"]["skipped"]}
    assert get(api, "tasks/" + owned["id"])["task"]["status"] == "open"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM refusals WHERE actor='bot:ops'").fetchone()[0] >= 1


def test_more_tasks_than_the_cap_are_refused_whole(api):
    with api.app.state.store.transaction() as c:
        for i in range(201):
            H.task_create(c, "human:ana", f"Review the account {i}", "Please.", "bot:cmo", lint=False, deduplicate=False)
    out = bulk(api, {"owner_id": "cmo"}, "status", status="doing", expected=422)
    assert out["error"]["code"] == "too_many" and "200" in out["error"]["detail"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE status='doing'").fetchone()[0] == 0


def test_the_cli_and_the_mcp_tool_send_the_bulk_change_the_route_takes(api):
    from clients import hubcli, remotecli
    from backend.tests.test_mcp import call

    sent = []

    class Client:
        def get(self, path, **query):
            return {"actor": "human:ana"}

        def post(self, path, body, key=None):
            sent.append((path, body))
            return {}

    args = hubcli.parser().parse_args(["task", "bulk", "step", "Legal review", "--where-type", "Marketing",
                                       "--where-step", "Draft", "--where-owner", "me", "--where-status", "open",
                                       "--where-status", "doing", "--dry-run"])
    remotecli.via_tool(Client(), args)
    assert sent == [("task-bulk", {"filter": {"type": "Marketing", "step": "Draft", "owner_id": "human:ana",
                                              "status": "open,doing"},
                                   "action": "step", "step": "Legal review", "dry_run": True})]

    tasks = [label(api, task(api, f"Draft the brief {i}"), "brief") for i in range(2)]
    err, out = call(api, "hub_task_bulk", {"filter": {"label": "brief", "owner_id": "cmo"}, "action": "close"})
    assert not err and out["changed"] == 2
    assert {get(api, "tasks/" + t["id"])["task"]["status"] for t in tasks} == {"closed"}


def test_one_bulk_call_counts_once_toward_a_bots_quarantine(api):
    # Rule 8 quarantines a bot at QUARANTINE_AT repeated refusals in a day. A bot's honest bulk close over 50 tasks it
    # may not close is one refused try: each task's refusal stays on record, and the bot stays active.
    token = bot_token(api, "ops")
    with api.app.state.store.transaction() as c:
        owned = [H.task_create(c, "human:ana", f"Review the account {i}", "Please.", "bot:ops", lint=False,
                               deduplicate=False) for i in range(50)]

    def refusals():
        with api.app.state.store.read() as c:
            return c.execute("SELECT count(*) FROM refusals WHERE actor='bot:ops'").fetchone()[0]

    def state():
        with api.app.state.store.read() as c:
            return H.bot(c, "ops")["state"]

    # A dry run still adds none.
    out = bulk(api, {"owner_id": "ops"}, "close", token=token, dry_run=True)
    assert (out["changed"], out["skipped"]) == (0, 50) and refusals() == 0
    out = bulk(api, {"owner_id": "ops"}, "close", token=token)
    assert (out["changed"], out["skipped"]) == (0, 50)
    assert refusals() == 50 and state() == "active"

    # It counts as the same refused close made one by one: nine more single tries are its nine repeats, and the
    # tenth quarantines the bot, as ten single tries in a row would have.
    for i in range(H.QUARANTINE_AT):
        assert state() == "active", i
        row = get(api, "tasks/" + owned[0]["id"])["task"]
        post(api, "tasks/" + row["id"], {"version": row["version"], "close": True}, token=token, expected=403)
    assert state() == "quarantined"
    # What the bot is told when it is back quotes the refused words, never the stored refusal or its call.
    with api.app.state.store.read() as c:
        stopped = next(e["detail"]["refusal"] for e in H.events(c, actor=H.KEEPER) if e["action"] == "quarantine")
        words = H.refusals_for(c, actor="bot:ops")[-1]["detail"]
    assert stopped == {"rule": "close", "detail": words}
