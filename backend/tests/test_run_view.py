"""A task's run panel (GET /api/v2/tasks/{id}/run-view): each attempt with what followed it, the subagents it started,
the background shells and monitors still running, and the checkpoint. Nothing from a call's arguments reaches it."""
import json

from backend.store import H
from backend.tests.test_shared_logins import queued_login  # noqa: F401
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
    [run] = view["runs"]
    assert (run["id"], run["state"], run["try"], run["tries"], run["next"]) == (aid, "running", 1, 1, None)
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
    [shell, _, moved] = view["runs"][0]["background"]
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
    assert get(api, f"tasks/{tid}/run-view", token="cara-test")["runs"] == []
    assert [r["id"] for r in get(api, f"tasks/{tid}/run-view")["runs"]] == [aid]


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
    assert view["runs"][0]["next"]["kind"] == "waiting"
    assert view["runs"][0]["reason"] == "Computer lost or heartbeat stopped (attempt lease expired)"
    aged(api, first["id"], grace=True)
    assert [d["state"] for d in sweep(api)] == ["queued"]
    ready(api, machine, ["ops"])
    second = claim(api, machine)
    assert second["job_id"] == first["job_id"]
    later, earlier = get(api, f"tasks/{tid}/run-view")["runs"]
    with api.app.state.store.read() as c:
        from backend.run_view import RECOVERY_SQL
        plan = " ".join(r[-1] for r in c.execute(f"EXPLAIN QUERY PLAN SELECT created_at FROM events WHERE {RECOVERY_SQL} "
                                                 "AND target IN (?,?)", ("j", "a")))
    assert "events_action_target" in plan, plan
    assert (earlier["id"], earlier["try"], earlier["tries"], earlier["state"]) == (first["id"], 1, 2, "expired")
    assert earlier["next"]["kind"] == "resumed" and earlier["next"]["by"] == H.KEEPER
    assert earlier["next"]["note"].startswith("Resumed automatically: the interrupted run only read (Read)")
    assert "Archived one bot" not in json.dumps(earlier), "the hub's own words, never what the stopped run saved"
    # A resume recorded before the hub kept a line for people still shows only its first paragraph.
    from backend.run_view import decision_note
    assert decision_note(H.KEEPER, {"note": "Check first.\n\nWhat the stopped run saved:\nArchived"}) == "Check first."
    assert (later["id"], later["try"], later["tries"], later["next"]) == (second["id"], 2, 2, None)


def test_a_persons_retry_and_dismissal_name_them(api):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, first = task_attempt(api, machine, "Rename the files")
    expire(api, first["id"])
    assert claim(api, machine) is None
    post(api, f"jobs/{first['job_id']}/retry", {"acknowledge_uncertain_effects": True})
    queued = get(api, f"tasks/{tid}/run-view")["runs"][0]["next"]
    assert (queued["kind"], queued["by"], queued["note"]) == ("queued", "human:ana", "Queued to run again by hand.")
    second = claim(api, machine)
    post(api, f"attempts/{second['id']}/started", {"thread_id": "again"}, machine["token"])
    expire(api, second["id"])
    assert claim(api, machine) is None
    post(api, f"jobs/{first['job_id']}/reconcile", {"run_id": second["id"], "decision": "dismiss",
                                                    "note": "Renamed by hand; nothing left to do.",
                                                    "acknowledge_uncertain_effects": True})
    later, earlier = get(api, f"tasks/{tid}/run-view")["runs"]
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
    assert {r["id"] for r in get(api, f"tasks/{tid}/run-view")["runs"]} == {shared["id"], direct["id"]}
    monkeypatch.setattr("backend.run_view.ATTEMPT_LIMIT", 1)
    seen = get(api, f"tasks/{tid}/run-view", token="cara-test")
    assert [r["id"] for r in seen["runs"]] == [shared["id"]] and "payroll" not in json.dumps(seen)
    access = get(api, "bots/ops/access")
    put(api, "bots/ops/access", {"see": access["see"], "read": {"people": ["ana"]}, "write": access["write"],
                                 "revision": access["revision"]})
    # The task moved to a person: Cara still reads it, and still none of the bot's runs on it.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='human:ana', requester_id='human:ana' WHERE id=?", (tid,))
    assert get(api, f"tasks/{tid}/run-view", token="cara-test")["runs"] == []
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
    assert (live["state"], live["run_id"], live["bot_id"]) == ("running", aid, "ops")
    assert live["parts"][-1] == {**live["parts"][-1], "kind": "tool", "text": "Ran hub task create"}
    events(api, machine, aid, [("message", {"text": "Counted: 42 words.", "final": True})], first=3)
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": "Counted: 42 words.", "last_seq": 3},
         machine["token"])
    page = get(api, f"tasks/{tid}")
    reply = next(m for m in page["comments"] if m["sender_id"] == "bot:ops" and "42 words" in m["body"])
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


def test_the_working_line_follows_whichever_bot_a_persons_message_went_to(api):
    """The task's "Working" line is the owning bot's run, or the run for a person's message in the task's room,
    whichever bot that went to: on a person's task the bot that asked it answers, and the page shows it working.
    Another bot's run for a bot's message is not the line. `comment_to` says before sending who will get a comment."""
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, attempt = task_attempt(api, machine)
    assert get(api, f"tasks/{tid}")["execution"]["run_id"] == attempt["id"]
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Counted.", "last_seq": 0},
         machine["token"])
    with api.app.state.store.transaction() as c:
        cid = H.task(c, tid)["conversation_id"]
        c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
                  "VALUES('msg-coo',?,'bot:ops','bot:coo','chat','Over to you','{}',?)", (cid, H.now()))
        assert c.execute("SELECT bot_id FROM jobs WHERE message_id='msg-coo'").fetchone()["bot_id"] == "coo"
    assert get(api, f"tasks/{tid}")["execution"]["run_id"] == attempt["id"], "a bot's message to a bot is not it"
    # A person owns the task and the bot that asked it answers them. A plain comment there is deferred a day
    # (backend/delivery.py); one that names the bot wakes it.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='human:ana', requester_id='bot:ops' WHERE id=?", (tid,))
    said = post(api, f"tasks/{tid}/comments", {"text": "@ops which app is still open?"})
    assert said["woke"] and said["comment"]["recipient_id"] == "bot:ops"
    queued = get(api, f"tasks/{tid}")["execution"] or {}
    assert (queued["message_id"], queued["bot_id"], queued["state"], queued["from_person"]) == (
        said["comment"]["id"], "ops", "queued", True)
    turn = claim(api, machine)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "answer"}, machine["token"])
    working = get(api, f"tasks/{tid}")["execution"]
    assert (working["run_id"], working["state"], working["bot_id"]) == (turn["id"], "running", "ops")
    # The next plain comment waits for the bot; the box offers @ops to wake it.
    assert get(api, f"tasks/{tid}")["comment_to"] == {"bot": "ops", "wakes": False, "mention": ["ops"]}
    # Someone who neither owns, asked nor may move it is saved for the bot's next run, and no @mention changes that:
    # the composer offers none, and one sent anyway wakes nobody.
    assert get(api, f"tasks/{tid}", token="cara-test")["comment_to"] == {"bot": "ops", "wakes": False, "mention": []}
    assert post(api, f"tasks/{tid}/comments", {"text": "@ops is it billing?"}, token="cara-test")["woke"] is False
    # A mover who does not own it is saved too, unless they @mention the bot that asked: the composer offers it and
    # it wakes the bot.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='human:ben' WHERE id=?", (tid,))
    to = get(api, f"tasks/{tid}")["comment_to"]
    assert (to["bot_id"], to["wakes"], "ops" in to["mention"]) == ("ops", False, True)
    assert post(api, f"tasks/{tid}/comments", {"text": "Noted."})["woke"] is False
    assert post(api, f"tasks/{tid}/comments", {"text": "@ops is it billing?"})["woke"] is True
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET requester_id='human:ben' WHERE id=?", (tid,))
    assert get(api, f"tasks/{tid}")["comment_to"] == {"bot": None, "wakes": False, "mention": []}, "no bot gets it"


def test_an_opt_in_login_queue_names_the_holder_only_to_who_may_see_it(api, queued_login):
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
    [run] = get(api, f"tasks/{tid}/run-view")["runs"]
    assert (run["state"], run["waiting"], run["moved"]) == ("running", line, None)
    [run] = get(api, f"tasks/{tid}/run-view", "ben-test")["runs"]
    assert run["waiting"] == "Waiting for this computer's ChatGPT login to be free (busy with another bot)"
    # A runner that knows the queue names the holder's run, since when, and the place. The holder's task is looked up
    # from its run, only for a reader who may read that run, and is never stored with the wait: every event goes out
    # raw to whoever reads the waiting run (the changes feed, the run's events), so neither the task an old runner
    # sends nor its worded line is kept. A holder that is no live run is flagged for Health.
    from backend import login_waits
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET title='Close the books', number=12 WHERE id=?", (tid,))
    held = "#12 Close the books"
    worded = f"Waiting for this computer's ChatGPT login (held by finance on {held} since 19:06Z; position 2)"
    place = {"waiting": worded, "login": "this computer's ChatGPT login", "holder": "finance", "holder_attempt": aid,
             "holder_task": held, "since": 1_760_900_760, "position": 2}
    events(api, machine, aid, [("status", place)], first=2)
    [run] = get(api, f"tasks/{tid}/run-view")["runs"]
    assert run["waiting"] == worded
    [run] = get(api, f"tasks/{tid}/run-view", "ben-test")["runs"]
    assert run["waiting"] == "Waiting for this computer's ChatGPT login (held by another bot since 19:06Z; position 2)"
    with api.app.state.store.read() as c:
        kept = " ".join(r[0] for r in c.execute("SELECT payload FROM attempt_events WHERE attempt_id=?", (aid,)))
    assert "Close the books" not in kept and "held by" not in kept
    # Held by another turn of the same bot: a reader who may see that run still learns its task.
    events(api, machine, aid, [("status", {**place, "holder": "ops"})], first=3)
    [run] = get(api, f"tasks/{tid}/run-view")["runs"]
    assert run["waiting"] == worded.replace("held by finance", "held by another turn of this bot")
    events(api, machine, aid, [("status", place)], first=4)
    with api.app.state.store.read() as c:
        assert not login_waits.unknown_holder(c, aid)
    # The bot's status says it waits and on whom; Health counts it as busy, not stuck.
    [status] = [s for s in get(api, "status")["bots"] if s and s["bot"] == "ops"]
    assert status["focus"] == status["waiting"] == worded
    assert not [r for r in get(api, "health")["stuck"] if r["bot"] == "ops"]
    events(api, machine, aid, [("status", {**place, "holder_attempt": "gone"})], first=5)
    [row] = [r for r in get(api, "health")["stuck"] if r["bot"] == "ops"]
    assert row["reason"] == "login_holder_unknown"
    # Once the login is its own the wait is over; a turn that ran on its fallback instead says where and why.
    events(api, machine, aid, [("status", {"waiting": ""}),
                               ("diagnostic", {"text": "ChatGPT login busy with finance; running this turn on claude",
                                               "fallback": "claude",
                                               "busy": {"login": "this computer's ChatGPT login", "holder": "finance"}})],
           first=6)
    [run] = get(api, f"tasks/{tid}/run-view")["runs"]
    assert (run["waiting"], run["moved"]) == (None, "ChatGPT login busy with finance; ran this turn on claude")
    [run] = get(api, f"tasks/{tid}/run-view", "ben-test")["runs"]
    assert run["moved"] == "ChatGPT login busy with another bot; ran this turn on claude"


def test_an_opt_in_login_queue_shows_the_wait_as_the_chat_label_not_a_failure(api, queued_login):
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


def test_codex_ignores_old_login_wait_events_in_tasks_chat_status_and_health(api):
    from backend import login_waits
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, attempt = task_attempt(api, machine)
    aid = attempt["id"]
    # An old runner's final wait must not survive as a current label after the queue is disabled.
    events(api, machine, aid, [("status", {
        "waiting": 'Waiting for ChatGPT login "work" to be free', "login": 'ChatGPT login "work"',
        "holder": "finance", "holder_attempt": "gone", "position": 1})])
    with api.app.state.store.read() as c:
        assert login_waits.wait(c, aid) is None
        assert not login_waits.unknown_holder(c, aid)
    [run] = get(api, f"tasks/{tid}/run-view")["runs"]
    assert run["waiting"] is None and run["state"] == "running"
    execution = get(api, f"tasks/{tid}")["execution"]
    assert "waiting" not in execution and "Waiting for" not in execution["label"]
    conversation = attempt["conversation"]["id"]
    execution = get(api, f"conversations/{conversation}/snapshot")["execution"]
    assert "waiting" not in execution and "Waiting for" not in execution["label"]
    [status] = [s for s in get(api, "status")["bots"] if s and s["bot"] == "ops"]
    assert not status.get("waiting") and "Waiting for" not in status["focus"]
    assert not [row for row in get(api, "health")["stuck"]
                if row["bot"] == "ops" and row.get("reason") == "login_holder_unknown"]


DEV_563_NOTE = ("Run finished its work before the 60-min limit: PR 1596 pushed at 8cf2a8e, hosted checks passed, "
                "Review 1 requested. Nothing to redo.")


def botops_decides(api, job_id, attempt_id, decision, note, at):
    """BotOps' own review of a held run (execution.reconcile_job, as `bot:botops`), saved at `at`."""
    from types import SimpleNamespace
    from backend.auth import Identity
    with api.app.state.store.transaction() as c:
        if not H.bot(c, H.FLEET_MAINTAINER):
            c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES(?, ?, 'BotOps', 'active')", (H.FLEET_MAINTAINER, H.FLEET_MAINTAINER))
        api.app.state.execution.reconcile_job(c, Identity("bot:" + H.FLEET_MAINTAINER, "bot"), job_id, SimpleNamespace(
            run_id=attempt_id, decision=decision, note=note, acknowledge_uncertain_effects=True))
        c.execute("UPDATE job_recovery SET created_at=? WHERE job_id=?", (at, job_id))


def held_run(api, title):
    """A task's run cut at its time limit and held for review (the Dev 563 shape): the job stays uncertain."""
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid, attempt = task_attempt(api, machine, title)
    expire(api, attempt["id"])
    assert claim(api, machine) is None
    return machine, tid, attempt


def test_a_held_runs_decision_shows_beside_its_state_with_who_when_and_the_note(api):
    _, tid, attempt = held_run(api, "Build the QA waiver")
    [run] = get(api, f"tasks/{tid}/run-view")["runs"]
    # No recovery decision yet: no decision label.
    assert (run["job_state"], run["decision"]) == ("uncertain", None)

    botops_decides(api, attempt["job_id"], attempt["id"], "dismiss", DEV_563_NOTE, "2026-10-26T09:50:45.103088Z")
    [run] = get(api, f"tasks/{tid}/run-view")["runs"]
    assert run["job_state"] == "canceled"
    assert run["decision"] == {"decision": "dismiss", "run_id": attempt["id"], "current": True,
                               "state": "canceled", "by": "bot:botops", "by_kind": "botops",
                               "at": "2026-10-26T09:50:45.103088Z", "note": DEV_563_NOTE}
    from backend.run_view import decision_label
    assert decision_label(run["decision"], lambda a: "BotOps") == (
        "Cancelled: dismissed by BotOps at 09:50Z, " + DEV_563_NOTE)

    # The label is read from the decision record each time, never copied onto the job: a later decision shows.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE job_recovery SET decision='resume', actor='human:ana', note=?, created_at=? WHERE job_id=?",
                  ("Ana wants it run again after all.", "2026-10-26T10:02:00Z", attempt["job_id"]))
    [run] = get(api, f"tasks/{tid}/run-view")["runs"]
    assert (run["decision"]["decision"], run["decision"]["by"], run["decision"]["by_kind"], run["decision"]["note"]) == (
        "resume", "human:ana", "person", "Ana wants it run again after all.")


def test_a_persons_resume_with_a_note_names_them_and_a_run_without_one_has_no_label(api):
    machine, tid, attempt = held_run(api, "Rename the folders")
    post(api, f"jobs/{attempt['job_id']}/reconcile", {"run_id": attempt["id"], "decision": "resume",
                                                      "note": "Nothing was renamed yet; run it again.",
                                                      "acknowledge_uncertain_effects": True})
    [run] = get(api, f"tasks/{tid}/run-view")["runs"]
    assert run["job_state"] == "queued"
    assert (run["decision"]["decision"], run["decision"]["by"], run["decision"]["by_kind"], run["decision"]["note"],
            run["decision"]["state"]) == ("resume", "human:ana", "person", "Nothing was renamed yet; run it again.",
                                          "queued")
    # The try it started has no decision of its own, so it carries no label.
    ready(api, machine, ["ops"])
    again = claim(api, machine)
    post(api, f"attempts/{again['id']}/started", {"thread_id": "again"}, machine["token"])
    later, earlier = get(api, f"tasks/{tid}/run-view")["runs"]
    assert (later["id"], later["decision"]) == (again["id"], None)
    assert earlier["decision"]["by"] == "human:ana"
    # The new try stops too and nobody has decided it: the job is held again, but the earlier resume is history, not
    # a saved choice for this stop. It says only what was decided; the new stop carries no label.
    expire(api, again["id"])
    assert claim(api, machine) is None
    later, earlier = get(api, f"tasks/{tid}/run-view")["runs"]
    assert later["job_state"] == "uncertain" and later["decision"] is None
    assert (earlier["decision"]["current"], earlier["decision"]["state"]) == (False, None)
    from backend.run_view import decided_by, decision_label
    assert decision_label(earlier["decision"], lambda a: "Ana") == (
        "Resumed by Ana at " + earlier["decision"]["at"][11:16] + "Z, Nothing was renamed yet; run it again.")
    # Tico's own auto-reconcile is named as such.
    assert decided_by(H.KEEPER) == "tico"
    assert decision_label({"decision": "resume", "by": H.KEEPER, "by_kind": "tico", "state": "completed",
                           "at": "2026-10-26T07:05:00Z", "note": ""}, str) == (
        "Done: resumed by Tico's auto-reconcile at 07:05Z")


def test_a_deferred_choice_keeps_the_time_it_was_made(api):
    """A choice saved while the lane is busy is applied later by the same `_decide`; the label's time is when the
    person decided, not when it was applied. A new decision takes its own time."""
    _, _, attempt = held_run(api, "Tidy the archive")
    execution = api.app.state.execution
    with api.app.state.store.transaction() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()
        execution._decide(c, row, attempt["id"], "resume", "Run it once the lane is free.", "human:ana", deferred=True)
        c.execute("UPDATE job_recovery SET created_at='2026-10-26T09:00:00Z' WHERE job_id=?", (attempt["job_id"],))
        execution._decide(c, row, attempt["id"], "resume", "Run it once the lane is free.", "human:ana")
        assert c.execute("SELECT created_at FROM job_recovery WHERE job_id=?", (attempt["job_id"],)).fetchone()[0] == (
            "2026-10-26T09:00:00Z")
        execution._decide(c, row, attempt["id"], "dismiss", "Done by hand after all, nothing to run.", "human:ana")
        assert c.execute("SELECT created_at FROM job_recovery WHERE job_id=?", (attempt["job_id"],)).fetchone()[0] != (
            "2026-10-26T09:00:00Z")
