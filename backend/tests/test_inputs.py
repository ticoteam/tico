from backend.tests.test_api import api, assign, claim, get, post, ready, runner, setup_attempt, expire
from backend.tests.test_member_bots import botops, turn, finish


def test_interrupted_steered_question_is_not_silently_completed(api):
    r, _, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "thread"}, r["token"])
    # A second bot asks while COO is active.
    assign(api, r, "finance")
    ready(api, r, ["ops", "finance"])
    post(api, "chat/finance", {"text": "Ask COO"})
    finance = claim(api, r)
    q = post(api, "messages", {"to": "ops", "kind": "ask", "text": "Any update?", "wait_s": 60}, finance["token"])
    post(api, f"attempts/{a['id']}/inputs", {}, r["token"])
    expire(api, a["id"])
    with api.app.state.store.transaction() as c:
        api.app.state.execution.expire(c)
        state = c.execute("SELECT state FROM jobs WHERE message_id=?", (q["id"],)).fetchone()[0]
    assert state == "uncertain"


def test_plain_message_from_another_conversation_does_not_interrupt_running_turn(api):
    r, _, active = setup_attempt(api)
    post(api, f"attempts/{active['id']}/started", {"thread_id": "thread"}, r["token"])
    assign(api, r, "finance")
    ready(api, r, ["ops", "finance"])
    post(api, "chat/finance", {"text": "Send COO a separate update"})
    finance = claim(api, r)
    update = post(api, "messages", {"to": "ops", "kind": "say", "text": "Separate FYI"}, finance["token"])

    assert post(api, f"attempts/{active['id']}/inputs", {}, r["token"])["messages"] == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (update["id"],)).fetchone()[0] == "queued"


def test_botops_queues_unrelated_requests_and_task_notices_but_accepts_an_explicit_reply(api, botops):
    active = turn(api, botops, person="ana-test", text="Review ops")
    post(api, f"attempts/{active['id']}/started", {"thread_id": "qa-thread"}, botops["token"])
    with api.app.state.store.read() as c:
        origin = dict(c.execute("SELECT m.* FROM messages m JOIN jobs j ON j.message_id=m.id JOIN attempts a ON a.job_id=j.id "
                                "WHERE a.id=?", (active["id"],)).fetchone())
    separate = post(api, "chat/botops", {"text": "Review finance independently"})
    task = post(api, "tasks", {"owner": "botops", "title": "Review another bot", "body": "Review finance independently."})
    assert post(api, f"attempts/{active['id']}/inputs", {}, botops["token"])["messages"] == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (separate["id"],)).fetchone()[0] == "queued"
    from backend.tests.test_mcp import call as mcp
    err, correction = mcp(api, "hub_message_send", {"to": "botops", "text": "For the ops review, include its Routines",
                           "conversation_id": origin["conversation_id"], "in_reply_to": origin["id"]})
    assert not err, correction
    inputs = post(api, f"attempts/{active['id']}/inputs", {}, botops["token"])["messages"]
    assert [m["id"] for m in inputs] == [correction["id"]]


def test_generated_tool_task_accepts_its_requesters_steering_and_withdrawal(api, botops):
    from backend.tests.test_mcp import call as mcp
    made = post(api, "bots/ops/tools", {"service": "qa-service", "can": ["read"]})
    task = get(api, "tasks/" + made["task_id"])["task"]
    active = claim(api, botops, "botops")
    post(api, f"attempts/{active['id']}/started", {"thread_id": "qa-task"}, botops["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT task_id FROM bot_status WHERE bot='botops'").fetchone()[0] == task["id"]
    err, correction = mcp(api, "hub_message_send", {"to": "botops", "text": "Use the fixture only", "steer": True})
    assert not err
    inputs = post(api, f"attempts/{active['id']}/inputs", {}, botops["token"])["messages"]
    assert [m["id"] for m in inputs] == [correction["id"]]
    post(api, f"attempts/{active['id']}/inputs/{correction['id']}/ack", {}, botops["token"])
    post(api, f"bots/ops/tools/{made['tool']['id']}/delete", {})
    inputs = post(api, f"attempts/{active['id']}/inputs", {}, botops["token"])["messages"]
    assert len(inputs) == 1 and "withdrawn" in inputs[0]["body"]


def test_new_chat_clears_previous_task_from_running_status(api, botops):
    task = post(api, "tasks", {"owner": "botops", "title": "QA previous task", "body": "Check a fixture"})
    worker = claim(api, botops, "botops")
    finish(api, botops, worker)
    post(api, "tasks/" + task["id"], {"version": task["version"], "close": True, "quiet": True})
    active = turn(api, botops, person="ana-test", text="Review current status")
    post(api, f"attempts/{active['id']}/started", {"thread_id": "qa-chat"}, botops["token"])
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT task_id FROM bot_status WHERE bot='botops'").fetchone()[0]


def test_botops_continuation_keeps_the_requester_and_quiet_progress_stays_on_the_task(api, botops):
    from backend.tests.test_botops_parity import act
    active = turn(api, botops, person="ana-test", text="Review ops and keep working until done")
    with api.app.state.store.read() as c:
        origin = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (active["id"],)).fetchone()[0]
    task = post(api, "tasks", {"owner": "botops", "title": "Review ops", "body": "Finish the requested review",
                              "request_id": origin}, active["token"])
    assert task["requester"] == "human:ana" and task["request_id"] == origin
    post(api, "tasks/" + task["id"], {"version": task["version"], "note": "QA detailed progress", "quiet": True}, active["token"])
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE body='QA detailed progress'").fetchone()
        assert c.execute("SELECT note FROM tasks WHERE id=?", (task["id"],)).fetchone()[0] == "QA detailed progress"
    finish(api, botops, active)
    continuation = claim(api, botops, "botops")
    changed = act(api, continuation, "GET", "bots/ops/access")
    assert changed.status_code == 200, changed.text


def test_acknowledged_input_requeued_by_a_finished_run_is_delivered_once_to_the_next_run(api):
    # TIDY v0.3.21: a folded, acknowledged input whose job went back to the queue made every later
    # inputs poll fail with UNIQUE constraint failed: attempt_inputs.message_id (a 500 each retry).
    r, origin, first = setup_attempt(api)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "thread"}, r["token"])
    follow = post(api, "chat/ops", {"text": "Also include the weekly numbers"})
    assert [m["id"] for m in post(api, f"attempts/{first['id']}/inputs", {}, r["token"])["messages"]] == [follow["id"]]
    post(api, f"attempts/{first['id']}/inputs/{follow['id']}/ack", {}, r["token"])
    assert post(api, f"attempts/{first['id']}/inputs", {}, r["token"])["messages"] == []
    # The runtime could not renew its sign-in: nothing ran, so the turn and its input are requeued.
    post(api, f"attempts/{first['id']}/complete", {"outcome": "failed", "text": "sign-in lapsed",
         "last_seq": 0, "retryable": True}, r["token"])
    with api.app.state.store.read() as c:
        assert {s for (s,) in c.execute("SELECT state FROM jobs WHERE message_id IN (?,?)",
                                        (origin["id"], follow["id"]))} == {"queued"}
    second = claim(api, r)
    post(api, f"attempts/{second['id']}/started", {"thread_id": "thread-2"}, r["token"])
    with api.app.state.store.read() as c:
        started_for = c.execute("SELECT message_id FROM jobs WHERE id=?", (second["job_id"],)).fetchone()[0]
    other = follow["id"] if started_for == origin["id"] else origin["id"]
    # The requeued message reaches the new run once, and acknowledging it there ends delivery.
    assert [m["id"] for m in post(api, f"attempts/{second['id']}/inputs", {}, r["token"])["messages"]] == [other]
    assert [m["id"] for m in post(api, f"attempts/{second['id']}/inputs", {}, r["token"])["messages"]] == [other]
    post(api, f"attempts/{second['id']}/inputs/{other}/ack", {}, r["token"])
    assert post(api, f"attempts/{second['id']}/inputs", {}, r["token"])["messages"] == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT attempt_id,acked_at IS NOT NULL FROM attempt_inputs WHERE message_id=?",
                         (other,)).fetchone()[:] == (second["id"], 1)
        assert c.execute("SELECT state,attempt_id FROM jobs WHERE message_id=?", (other,)).fetchone()[:] == ("input", second["id"])


def test_requeued_input_already_acknowledged_by_this_run_is_not_redelivered(api):
    r, _, first = setup_attempt(api)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "thread"}, r["token"])
    follow = post(api, "chat/ops", {"text": "One more thing"})
    post(api, f"attempts/{first['id']}/inputs", {}, r["token"])
    with api.app.state.store.transaction() as c:
        # A job back in the queue while the run that already took its input is still live.
        c.execute("UPDATE jobs SET state='queued' WHERE message_id=?", (follow["id"],))
        c.execute("UPDATE attempt_inputs SET acked_at=? WHERE message_id=?", ("2026-01-01T00:00:00Z", follow["id"]))
    # Same run: already acknowledged there, so not delivered again and no error.
    assert post(api, f"attempts/{first['id']}/inputs", {}, r["token"])["messages"] == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT attempt_id,acked_at FROM attempt_inputs WHERE message_id=?",
                         (follow["id"],)).fetchone()[:] == (first["id"], "2026-01-01T00:00:00Z")
