"""A task's run panel (GET /api/v2/tasks/{id}/run-view): each attempt with what followed it, the subagents it started,
the background shells and monitors still running, and the checkpoint. Nothing from a call's arguments reaches it."""
import json

from backend.store import H
from backend.tests.test_api import api, assign, claim, expire, get, post, put, ready, runner  # noqa: F401
from backend.tests.test_execution_review import aged, sweep


def task_attempt(api, machine, title="Count the words"):
    task = post(api, "tasks", {"owner": "ops", "title": title, "body": "Count them."})
    attempt = claim(api, machine)
    assert attempt["task"]["id"] == task["id"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": title}, machine["token"])
    return task["id"], attempt


def events(api, machine, aid, rows, first=1):
    post(api, f"attempts/{aid}/events", {"events": [{"seq": first + i, "kind": k, "payload": p}
                                                    for i, (k, p) in enumerate(rows)]}, machine["token"])


def test_a_run_shows_its_subagent_and_the_background_command_it_left_running(api):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, attempt = task_attempt(api, machine)
    aid, secret = attempt["id"], "hunter2-token"
    agent = {"id": "a1", "parent_id": None, "type": "general-purpose", "model": "claude-haiku-5-5",
             "description": f"count {secret}"}
    events(api, machine, aid, [
        ("status", {"state": "active", "tool_events": True}),
        ("tool", {"tool": "Bash", "status": "started", "item_id": "s1", "summary": "sleep", "parent_id": None,
                  "background": True}),
        ("tool", {"tool": "Monitor", "status": "started", "item_id": "m1", "summary": "tail", "parent_id": None,
                  "background": True}),
        ("tool", {"tool": "Monitor", "status": "completed", "item_id": "m1", "parent_id": None, "background": True}),
        ("tool", {"tool": "Agent", "status": "started", "item_id": "a1", "summary": "", "parent_id": None,
                  "subagent": {**agent, "status": "started"}}),
        ("tool", {"tool": "Agent", "status": "backgrounded", "item_id": "a1", "parent_id": None, "background": True}),
        ("tool", {"tool": "Bash", "status": "started", "item_id": "b2", "summary": "wc", "parent_id": "a1"}),
        ("tool", {"tool": "Bash", "status": "completed", "item_id": "b2", "parent_id": "a1"}),
        ("tool", {"tool": "Agent", "status": "completed", "item_id": "a1", "parent_id": None, "background": True,
                  "subagent": {**agent, "status": "completed", "tool_uses": 1, "duration_ms": 2201}}),
        # A shell Claude moved to the background while it ran: its start did not say so.
        ("tool", {"tool": "Bash", "status": "started", "item_id": "s2", "summary": "npm", "parent_id": None}),
        ("tool", {"tool": "Bash", "status": "backgrounded", "item_id": "s2", "parent_id": None, "background": True})])
    view = get(api, f"tasks/{tid}/run-view")
    assert secret not in json.dumps(view)
    [run] = view["attempts"]
    assert (run["run"], run["state"], run["try"], run["tries"], run["next"]) == (aid, "running", 1, 1, None)
    assert [(s["tool"], s["summary"], s["running"]) for s in run["background"]] == [
        ("Bash", "sleep", True), ("Monitor", "tail", False), ("Bash", "", True)]
    [sub] = run["subagents"]
    assert (sub["type"], sub["model"], sub["status"], sub["tool_uses"], sub["duration_ms"], sub["background"]) == (
        "general-purpose", "claude-haiku-5-5", "completed", 1, 2201, True)
    # The steps view keeps one line for the subagent that moved to the background.
    steps = get(api, f"turns/{aid}/steps")["steps"]
    assert [(s["kind"], s.get("nested", False)) for s in steps] == [
        ("tool", False), ("tool", False), ("subagent", False), ("tool", True), ("tool", False)]

    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "last_seq": 11, "text": "Counted: 1 word."},
         machine["token"])
    view = get(api, f"tasks/{tid}/run-view")
    [shell, _, moved] = view["attempts"][0]["background"]
    assert (shell["running"], shell["status"]) == (False, "no end reported") == (moved["running"], moved["status"])
    assert view["checkpoint"]["text"] == "Counted: 1 word." and view["checkpoint"]["by"] == "bot:ops"
    # A long list keeps a call still running, however early it started.
    from backend.run_view import NODE_LIMIT, capped
    kept = capped([{"id": n, "running": n == 0} for n in range(NODE_LIMIT + 10)])
    assert len(kept) == NODE_LIMIT and kept[0]["id"] == 0 and kept[-1]["id"] == NODE_LIMIT + 9

    # A run that also carried a private task is left out for whoever cannot read that task.
    hidden = post(api, "tasks", {"owner": "ops", "title": "Private", "body": "Only Ana.", "private": True})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (aid, hidden["id"]))
    assert get(api, f"tasks/{tid}/run-view", token="cara-test")["attempts"] == []
    assert [r["run"] for r in get(api, f"tasks/{tid}/run-view")["attempts"]] == [aid]


def test_a_retried_run_shows_both_attempts_and_the_decision_between_them(api):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, first = task_attempt(api, machine, "Archive the bots")
    events(api, machine, first["id"], [("status", {"state": "active", "tool_events": True}),
                                       ("tool", {"tool": "Read", "status": "started", "item_id": "r1"}),
                                       ("message", {"text": "Archived one bot; the second is next."})])
    expire(api, first["id"])
    assert claim(api, machine) is None
    view = get(api, f"tasks/{tid}/run-view")
    assert view["attempts"][0]["next"]["kind"] == "waiting"
    assert view["attempts"][0]["reason"] == "Lease expired before completion"
    aged(api, first["id"])
    assert [d["state"] for d in sweep(api)] == ["queued"]
    ready(api, machine, ["ops"])
    second = claim(api, machine)
    assert second["job_id"] == first["job_id"]
    later, earlier = get(api, f"tasks/{tid}/run-view")["attempts"]
    with api.app.state.store.read() as c:
        from backend.run_view import RECOVERY_SQL
        plan = " ".join(r[-1] for r in c.execute(f"EXPLAIN QUERY PLAN SELECT ts FROM events WHERE {RECOVERY_SQL} "
                                                 "AND target IN (?,?)", ("j", "a")))
    assert "events_action_target" in plan, plan
    assert (earlier["run"], earlier["try"], earlier["tries"], earlier["state"]) == (first["id"], 1, 2, "expired")
    assert earlier["next"]["kind"] == "resumed" and earlier["next"]["by"] == H.KEEPER
    assert earlier["next"]["note"].startswith("Resumed automatically: the interrupted run only read (Read)")
    assert "Archived one bot" not in json.dumps(earlier), "the hub's own words, never what the stopped run saved"
    # A resume recorded before the hub kept a line for people still shows only its first paragraph.
    from backend.run_view import decision_note
    assert decision_note(H.KEEPER, {"note": "Check first.\n\nWhat the stopped run saved:\nArchived"}) == "Check first."
    assert (later["run"], later["try"], later["tries"], later["next"]) == (second["id"], 2, 2, None)


def test_a_persons_retry_and_dismissal_name_them(api):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, first = task_attempt(api, machine, "Rename the files")
    expire(api, first["id"])
    assert claim(api, machine) is None
    post(api, f"jobs/{first['job_id']}/retry", {"acknowledge_uncertain_effects": True})
    queued = get(api, f"tasks/{tid}/run-view")["attempts"][0]["next"]
    assert (queued["kind"], queued["by"], queued["note"]) == ("queued", "human:ana", "Queued to run again by hand.")
    second = claim(api, machine)
    post(api, f"attempts/{second['id']}/started", {"thread_id": "again"}, machine["token"])
    expire(api, second["id"])
    assert claim(api, machine) is None
    post(api, f"jobs/{first['job_id']}/reconcile", {"attempt_id": second["id"], "decision": "dismiss",
                                                    "note": "Renamed by hand; nothing left to do.",
                                                    "acknowledge_uncertain_effects": True})
    later, earlier = get(api, f"tasks/{tid}/run-view")["attempts"]
    assert (earlier["next"]["kind"], earlier["next"]["by"], earlier["next"]["note"]) == (
        "retried", "human:ana", "Run again by hand.")
    assert (later["next"]["kind"], later["next"]["by"], later["next"]["note"]) == (
        "dismissed", "human:ana", "Renamed by hand; nothing left to do.")


def test_a_run_from_a_conversation_the_reader_cannot_open_stays_out(api, monkeypatch):
    """A person's direct message to the bot that names the task puts that run on the task; only who can read that
    conversation (and the bot) sees it, and it does not push the reader's own runs off the page. Nobody without Read
    on the bot sees its runs. A blocked checkpoint is not shown."""
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, shared = task_attempt(api, machine, "Export the report")
    post(api, f"attempts/{shared['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Exported."},
         machine["token"])
    post(api, "chat/ops", {"text": "Run the payroll export for this task."})
    direct = claim(api, machine)
    post(api, f"attempts/{direct['id']}/started", {"thread_id": "dm"}, machine["token"])
    events(api, machine, direct["id"], [("tool", {"tool": "Bash", "status": "started", "item_id": "p1",
                                                  "summary": "payroll-export.sh", "parent_id": None, "background": True})])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE turns SET task_id=? WHERE id=?", (tid, direct["id"]))
    assert {r["run"] for r in get(api, f"tasks/{tid}/run-view")["attempts"]} == {shared["id"], direct["id"]}
    monkeypatch.setattr("backend.run_view.ATTEMPT_LIMIT", 1)
    seen = get(api, f"tasks/{tid}/run-view", token="cara-test")
    assert [r["run"] for r in seen["attempts"]] == [shared["id"]] and "payroll" not in json.dumps(seen)
    access = get(api, "bots/ops/access")
    put(api, "bots/ops/access", {"see": access["see"], "read": {"people": ["ana"]}, "write": access["write"],
                                 "revision": access["revision"]})
    # The task moved to a person: Cara still reads it, and still none of the bot's runs on it.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner='human:ana', requester='human:ana' WHERE id=?", (tid,))
    assert get(api, f"tasks/{tid}/run-view", token="cara-test")["attempts"] == []
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET checkpoint_blocked=1 WHERE id=?", (tid,))
    assert get(api, f"tasks/{tid}/run-view")["checkpoint"] is None


def test_the_task_page_follows_its_session_live_and_each_reply_keeps_its_steps(api):
    """The task's page reads as its session's chat: while the owner's run works, `execution` says so with its newest
    step; once it replies, the reply is in the thread with the run behind it, and the steps open from it."""
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, attempt = task_attempt(api, machine)
    aid = attempt["id"]
    events(api, machine, aid, [("tool", {"tool": "mcp__hub__task_create", "status": "started", "item_id": "c1"}),
                               ("tool", {"tool": "mcp__hub__task_create", "status": "completed", "item_id": "c1"})])
    live = get(api, f"tasks/{tid}")["execution"]
    assert (live["state"], live["attempt_id"], live["bot"]) == ("running", aid, "ops")
    assert live["parts"][-1] == {**live["parts"][-1], "kind": "tool", "text": "Ran hub task create"}
    events(api, machine, aid, [("message", {"text": "Counted: 42 words.", "final": True})], first=3)
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": "Counted: 42 words.", "last_seq": 3},
         machine["token"])
    page = get(api, f"tasks/{tid}")
    reply = next(m for m in page["comments"] if m["from_actor"] == "bot:ops" and "42 words" in m["body"])
    assert reply["refs"]["turn_id"] == aid and reply["run"]["steps"] >= 1
    assert page["execution"]["state"] == "completed"
    assert get(api, f"turns/{aid}/steps")["steps"], "the reply's steps open from the page"


def test_a_task_still_in_a_chat_room_never_shows_that_chats_run(api):
    """A task filed into a person's chat before task rooms keeps that room. Its page must not show the chat's live
    run to someone who may read the task but not the chat."""
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, attempt = task_attempt(api, machine)
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Counted.", "last_seq": 0},
         machine["token"])
    chat = post(api, "chat/ops", {"text": "My salary is 123k, how much should I save?"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (chat["conversation_id"], tid))
    secret = claim(api, machine)
    post(api, f"attempts/{secret['id']}/started", {"thread_id": "chat"}, machine["token"])
    events(api, machine, secret["id"], [("message", {"text": "With your 123k salary save 2k a month", "final": False})])
    get(api, f"conversations/{chat['conversation_id']}/snapshot", "ben-test", expected=403)
    for token in ("ana-test", "ben-test"):
        assert get(api, f"tasks/{tid}", token)["execution"] is None, "the chat's run is not the task's"


def test_the_working_line_is_only_the_owning_bots_run(api):
    """In the task's own room, only the owning bot's run is the task's "Working" line: another bot's run there is not,
    and a task a person owns has none."""
    from backend.views import conversation_execution
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, attempt = task_attempt(api, machine)
    assert get(api, f"tasks/{tid}")["execution"]["attempt_id"] == attempt["id"]
    with api.app.state.store.read() as c:
        cid = H.task(c, tid)["conversation_id"]
        assert conversation_execution(c, cid, bot="coo") is None, "another bot's runs only"
        assert conversation_execution(c, cid, bot="ops")["attempt_id"] == attempt["id"]
    # Another bot's newer job in the task's room (the requester's bot answering there, say) does not take the line.
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs_json,created) "
                  "VALUES('msg-coo',?,'human:ana','bot:coo','chat','Over to you','{}',?)", (cid, H.now()))
        assert c.execute("SELECT bot FROM jobs WHERE message_id='msg-coo'").fetchone()["bot"] == "coo"
    assert get(api, f"tasks/{tid}")["execution"]["attempt_id"] == attempt["id"], "the owner's run, not the newest"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner='human:ana' WHERE id=?", (tid,))
    assert get(api, f"tasks/{tid}")["execution"] is None, "a person's task has no session to follow"


def test_a_run_waiting_for_its_chatgpt_login_says_so_and_names_the_holder_only_to_who_may_see_it(api):
    from backend.tests.test_api import as_member, restrict
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:      # finance is Ana's alone: Ben may not even learn it is busy
        restrict(c, "finance", people=["ana"])
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, attempt = task_attempt(api, machine)
    aid = attempt["id"]
    line = "Waiting for this computer's ChatGPT login to be free (busy with finance)"
    events(api, machine, aid, [("status", {"waiting": line, "login": "this computer's ChatGPT login", "holder": "finance"})])
    [run] = get(api, f"tasks/{tid}/run-view")["attempts"]
    assert (run["state"], run["waiting"], run["moved"]) == ("running", line, None)
    [run] = get(api, f"tasks/{tid}/run-view", "ben-test")["attempts"]
    assert run["waiting"] == "Waiting for this computer's ChatGPT login to be free (busy with another bot)"
    # Once the login is its own the wait is over; a turn that ran on its fallback instead says where and why.
    events(api, machine, aid, [("status", {"waiting": ""}),
                               ("diagnostic", {"text": "ChatGPT login busy with finance; running this turn on claude",
                                               "fallback": "claude",
                                               "busy": {"login": "this computer's ChatGPT login", "holder": "finance"}})],
           first=2)
    [run] = get(api, f"tasks/{tid}/run-view")["attempts"]
    assert (run["waiting"], run["moved"]) == (None, "ChatGPT login busy with finance; ran this turn on claude")
    [run] = get(api, f"tasks/{tid}/run-view", "ben-test")["attempts"]
    assert run["moved"] == "ChatGPT login busy with another bot; ran this turn on claude"


def test_a_chat_turn_waiting_for_its_login_shows_the_wait_as_its_label_not_a_failure(api):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    msg = post(api, "chat/ops", {"text": "Please count."})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "count"}, machine["token"])
    events(api, machine, attempt["id"], [("status", {"waiting": "Waiting for ChatGPT login \"work\" to be free (busy with ops)",
                                                     "login": "ChatGPT login \"work\"", "holder": "ops"})])
    execution = get(api, f"conversations/{msg['conversation_id']}/snapshot")["execution"]
    line = "Waiting for ChatGPT login \"work\" to be free (busy with another turn of this bot)"
    assert (execution["state"], execution["label"], execution["waiting"]) == ("running", line, line)
    events(api, machine, attempt["id"], [("status", {"waiting": ""})], first=2)
    execution = get(api, f"conversations/{msg['conversation_id']}/snapshot")["execution"]
    assert "waiting" not in execution and execution["label"] != line
