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


def two_bots(api):
    r = runner(api)
    for bot in ("ops", "finance"):
        assign(api, r, bot)
    ready(api, r, ["ops", "finance"])
    return r


def ask_ops(api, r, task_id, text):
    post(api, "chat/finance", {"text": "Ask ops about the packet"})
    finance = claim(api, r, "finance")
    return post(api, "messages", {"to": "ops", "kind": "ask", "text": text, "wait_s": 60,
                                  "refs": {"task": task_id}}, finance["token"])


def test_deferred_input_moves_to_the_next_run_and_the_first_run_keeps_its_task(api):
    from backend import task_privacy
    from backend.store import H
    from backend.tests.test_task_privacy_reads import sql
    r = two_bots(api)
    with api.app.state.store.transaction() as c:
        packet = H.task_create(c, "bot:finance", "Packet", "Figures.", "bot:ops", private=False, lint=False)
        c.execute("UPDATE jobs SET state='cancelled' WHERE state='queued'")
    post(api, "chat/ops", {"text": "Check the public numbers"})
    first = claim(api, r, "ops")
    post(api, f"attempts/{first['id']}/started", {"thread_id": "thread"}, r["token"])
    ask = ask_ops(api, r, packet["id"], "Are the packet figures final?")
    assert [m["id"] for m in post(api, f"attempts/{first['id']}/inputs", {}, r["token"])["messages"]] == [ask["id"]]
    post(api, f"attempts/{first['id']}/inputs/{ask['id']}/ack", {}, r["token"])
    # The run ends without answering or touching the packet task, so its question waits for a later run.
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "", "last_seq": 0}, r["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state,attempt_id FROM jobs WHERE message_id=?", (ask["id"],)).fetchone()[:] == ("queued", None)
    post(api, "chat/ops", {"text": "One more check"})
    second = claim(api, r, "ops")
    post(api, f"attempts/{second['id']}/started", {"thread_id": "thread-2"}, r["token"])
    assert [m["id"] for m in post(api, f"attempts/{second['id']}/inputs", {}, r["token"])["messages"]] == [ask["id"]]
    post(api, f"attempts/{second['id']}/inputs/{ask['id']}/ack", {}, r["token"])
    assert post(api, f"attempts/{second['id']}/inputs", {}, r["token"])["messages"] == []
    get(api, f"turns/{first['id']}/steps")
    # The first run read the packet question. If the packet later turns private, that run must
    # close to people outside it even though the input row now belongs to the second run.
    with api.app.state.store.transaction() as c:
        assert c.execute("SELECT attempt_id FROM attempt_inputs WHERE message_id=?", (ask["id"],)).fetchone()[0] == second["id"]
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (packet["id"],))
    get(api, f"turns/{first['id']}/steps", expected=404)
    assert not sql(api, f"SELECT id FROM attempts WHERE id='{first['id']}'")
    with api.app.state.store.read() as c:
        assert packet["id"] in task_privacy.attempt_tasks(c, first["id"])
        assert task_privacy.attempt_readable(c, "bot:ops", first["id"])
        event = c.execute("SELECT * FROM events WHERE action='attempt.input.moved' AND target=?", (first["id"],)).fetchone()
        assert not task_privacy.event_readable(c, "human:ana", event)


def test_private_task_input_is_not_folded_into_another_tasks_run_and_runs_on_its_own(api):
    from backend.store import H
    r = two_bots(api)
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "bot:finance", "Private packet", "Confidential figures.", "bot:ops",
                                private=True, lint=False)
        public = H.task_create(c, "human:ana", "Public review", "Review the public numbers.", "bot:ops",
                               private=False, lint=False)
        c.execute("UPDATE jobs SET state='cancelled' WHERE state='queued' AND message_id NOT IN "
                  "(SELECT m.id FROM messages m JOIN conversations cv ON cv.id=m.conversation_id WHERE cv.task_id=?)",
                  (public["id"],))
    first = claim(api, r, "ops")
    post(api, f"attempts/{first['id']}/started", {"thread_id": "thread"}, r["token"])
    with api.app.state.store.read() as c:
        origin = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (first["job_id"],)).fetchone()[0])
        assert H.message_task_id(origin, H.conversation(c, origin["conversation_id"])) == public["id"]
    ask = ask_ops(api, r, private["id"], "Are the private figures final?")
    assert post(api, f"attempts/{first['id']}/inputs", {}, r["token"])["messages"] == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (ask["id"],)).fetchone()[0] == "queued"
        assert not c.execute("SELECT 1 FROM attempt_inputs WHERE message_id=?", (ask["id"],)).fetchone()
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "Reviewed", "last_seq": 0}, r["token"])
    get(api, f"turns/{first['id']}/steps")        # the person who started the public run keeps it
    second = claim(api, r, "ops")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT message_id FROM jobs WHERE id=?", (second["job_id"],)).fetchone()[0] == ask["id"]
