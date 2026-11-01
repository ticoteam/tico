import json

import pytest

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
    update = post(api, "messages", {"to": "ops", "kind": "say", "text": "Separate FYI", "needs_action": True}, finance["token"])

    assert post(api, f"attempts/{active['id']}/inputs", {}, r["token"])["messages"] == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (update["id"],)).fetchone()[0] == "queued"


def test_botops_continuation_keeps_the_requester_and_quiet_progress_stays_on_the_task(api, botops):
    from backend.tests.test_botops_parity import act
    active = turn(api, botops, person="ana-test", text="Review ops and keep working until done")
    with api.app.state.store.read() as c:
        origin = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (active["id"],)).fetchone()[0]
    task = post(api, "tasks", {"owner_id": "botops", "title": "Review ops", "body": "Finish the requested review",
                              "request_id": origin}, active["token"])
    assert task["requester_id"] == "human:ana" and task["request_id"] == origin
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
    assert claim(api, r) is None                    # a run that failed at once makes the bot pause (backend/run_backoff.py)
    post(api, "bots/ops/release", {})
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
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
    with api.app.state.store.transaction() as c:
        packet = H.task_create(c, "bot:finance", "Packet", "Figures.", "bot:ops", private=False, lint=False)
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
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


def test_a_deferred_input_moves_to_the_next_run_after_the_history_sweep_indexed_the_first(api):
    """The move's event and the input's own row both mark what read the first run as changed, in one write; once the
    sweep has indexed that run, the second run must still get the input instead of an error."""
    from backend import read_deps
    from backend.store import H
    from backend.tests.test_api import unrelated_task_managers
    r = two_bots(api)
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
    with api.app.state.store.transaction() as c:
        packet = H.task_create(c, "bot:finance", "Packet", "Figures.", "bot:ops", private=False, lint=False)
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
    post(api, "chat/ops", {"text": "Check the public numbers"})
    first = claim(api, r, "ops")
    post(api, f"attempts/{first['id']}/started", {"thread_id": "thread"}, r["token"])
    ask = ask_ops(api, r, packet["id"], "Are the packet figures final?")
    assert [m["id"] for m in post(api, f"attempts/{first['id']}/inputs", {}, r["token"])["messages"]] == [ask["id"]]
    post(api, f"attempts/{first['id']}/inputs/{ask['id']}/ack", {}, r["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "", "last_seq": 0}, r["token"])
    with api.app.state.store.read() as conn:
        read_deps.sweep(conn)
    post(api, "chat/ops", {"text": "One more check"})
    second = claim(api, r, "ops")
    post(api, f"attempts/{second['id']}/started", {"thread_id": "thread-2"}, r["token"])
    assert [m["id"] for m in post(api, f"attempts/{second['id']}/inputs", {}, r["token"])["messages"]] == [ask["id"]]


def test_private_task_input_is_not_folded_into_another_tasks_run_and_runs_on_its_own(api):
    from backend.store import H
    r = two_bots(api)
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "bot:finance", "Private packet", "Confidential figures.", "bot:ops",
                                private=True, lint=False)
        public = H.task_create(c, "human:ana", "Public review", "Review the public numbers.", "bot:ops",
                               private=False, lint=False)
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued' AND message_id NOT IN "
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


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_an_input_the_runtime_never_got_goes_back_to_the_queue_whatever_the_outcome(api, outcome):
    # The runner polled it, but the turn ended before the steer (then a goal control settled the run
    # "completed", or the turn failed): settled with the run it would read as answered, or uncertain.
    r, _, first = setup_attempt(api)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "thread"}, r["token"])
    lost = post(api, "chat/ops", {"text": "Also include the weekly numbers"})
    applied = post(api, "chat/ops", {"text": "And the monthly ones"})
    assert {m["id"] for m in post(api, f"attempts/{first['id']}/inputs", {}, r["token"])["messages"]} == \
        {lost["id"], applied["id"]}
    post(api, f"attempts/{first['id']}/inputs/{applied['id']}/ack", {}, r["token"])
    # The bot spoke, so a failure is a person's review ("uncertain"), not a turn that never ran.
    post(api, f"attempts/{first['id']}/events", {"events": [{"seq": 1, "kind": "message",
                                                              "payload": {"text": "Working on it"}}]}, r["token"])
    # Only this run's own unacknowledged inputs come back: the acknowledged one reached the runtime.
    post(api, f"attempts/{first['id']}/complete", {"outcome": outcome, "text": "Here is the summary.", "last_seq": 1,
         "undelivered": [lost["id"], applied["id"], "msg-of-someone-else"]}, r["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state,attempt_id FROM jobs WHERE message_id=?", (lost["id"],)).fetchone()[:] == \
            ("queued", None)
        # The acknowledged one is settled with the run as before and stays this run's input.
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (applied["id"],)).fetchone()[0] == \
            ("completed" if outcome == "completed" else "uncertain")
        assert c.execute("SELECT attempt_id FROM attempt_inputs WHERE message_id=?", (applied["id"],)).fetchone()[0] == \
            first["id"]
        assert not c.execute("SELECT 1 FROM attempt_inputs WHERE message_id=?", (lost["id"],)).fetchone()
        if outcome == "completed":
            answers = c.execute("SELECT json_extract(refs,'$.answers') FROM messages WHERE sender_id='bot:ops' "
                                "AND body='Here is the summary.'").fetchone()[0]
            assert lost["id"] not in answers and applied["id"] in answers
    if outcome == "completed":
        second = claim(api, r)
        with api.app.state.store.read() as c:
            assert c.execute("SELECT message_id FROM jobs WHERE id=?", (second["job_id"],)).fetchone()[0] == lost["id"]


def test_a_comment_in_the_room_a_task_moved_to_steers_the_turn_still_running_from_the_old_chat(api):
    from backend.store import H
    r = runner(api)
    assign(api, r, "ops")
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": ["task_lanes_v2"],
                                    "readiness": {"ops": True}}, token=r["token"])
    room = post(api, "chat/ops", {"text": "Hello"})["conversation_id"]
    with api.app.state.store.transaction() as c:      # filed into the chat before task rooms
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")
        task = H.task_create(c, "human:ana", "Recheck the cash warning", "Today.", "bot:ops", conversation_id=room, own_room=False)
    active = claim(api, r)
    post(api, f"attempts/{active['id']}/started", {"thread_id": "thread"}, r["token"])
    with api.app.state.store.transaction() as c:
        moved = H.task(c, task["id"])
        assert moved["conversation_id"] != room
        comment = H.task_comment(c, "human:ana", task["id"], "Use the new numbers.")
    assert [m["id"] for m in post(api, f"attempts/{active['id']}/inputs", {}, r["token"])["messages"]] == [comment["id"]]


def test_a_task_botops_files_from_a_persons_chat_lends_their_rights_to_its_own_run(api, botops):
    """HotMic, Oct 15: BotOps filed "Build a bot" citing `turn`; the task stored the literal and its own run, which starts
    from the task notice, was refused bot registration."""
    from backend.tests.test_member_bots import register
    active = turn(api, botops, person="ana-test", text="Add a Software Engineer bot in engineering")
    task = post(api, "tasks", {"owner_id": "botops", "title": "Build a bot: Software Engineer", "body": "Build it.",
                               "request_id": "turn"}, active["token"])
    with api.app.state.store.read() as c:
        origin = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (active["id"],)).fetchone()[0]
        assert c.execute("SELECT request_id FROM tasks WHERE id=?", (task["id"],)).fetchone()[0] == origin
    finish(api, botops, active)
    made = register(api, claim(api, botops, "botops"), "software-engineer")
    assert made.status_code == 200, made.text


def test_a_persons_follow_up_in_their_botops_chat_joins_the_running_reply(api, botops):
    active = turn(api, botops, person="ana-test", text="Add a Software Engineer bot")
    post(api, f"attempts/{active['id']}/started", {"thread_id": "thread"}, botops["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.harness','claude') WHERE bot='botops'")
    follow = post(api, "chat/botops", {"text": "Use Claude Opus 5.5, medium effort"}, token="ana-test")
    snapshot = get(api, f"conversations/{follow['conversation_id']}/snapshot", "ana-test")
    assert [m["id"] for m in post(api, f"attempts/{active['id']}/inputs", {}, botops["token"])["messages"]] == [follow["id"]]
    # Until the run takes it, it says so, never "waiting for a free turn" on a computer with turns free.
    assert snapshot["execution"]["label"] == "Saved — steering this turn"


def test_a_message_says_whether_it_steers_the_running_turn_and_stop_and_redirect_starts_the_next_with_it(api):
    from backend.store import H
    r, origin, active = setup_attempt(api)
    post(api, f"attempts/{active['id']}/started", {"thread_id": "thread"}, r["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.harness','gemini','$.runtime','gemini') WHERE bot='ops'")
    earlier = post(api, "chat/ops", {"text": "And the March numbers"})
    redirect = post(api, "chat/ops", {"text": "Stop that; do the April numbers instead"})
    page = get(api, f"conversations/{origin['conversation_id']}/snapshot")
    delivery = {m["id"]: m.get("delivery") for m in page["messages"]}
    assert delivery[redirect["id"]] == {"run_id": active["id"], "mode": "next_turn"}
    assert page["execution"]["label"] == "Saved — queued for the next turn"
    # Only the sender's own queued message redirects.
    post(api, "bots/ops/stop", {"run_id": active["id"], "redirect": redirect["id"]}, token="ben-test", expected=404)
    stopped = post(api, "bots/ops/stop", {"run_id": active["id"], "redirect": redirect["id"]})
    assert stopped == {"run_id": active["id"], "stopping": True, "redirect": redirect["id"]}
    page = get(api, f"conversations/{origin['conversation_id']}/snapshot")
    assert {m["id"]: m.get("delivery") for m in page["messages"]}[redirect["id"]]["mode"] == "redirect"
    # A steering host never folds the redirect into the turn it replaces.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.harness','claude','$.runtime','claude') WHERE bot='ops'")
    assert [m["id"] for m in post(api, f"attempts/{active['id']}/inputs", {}, r["token"])["messages"]] == [earlier["id"]]
    post(api, f"attempts/{active['id']}/complete", {"outcome": "interrupted", "text": "", "last_seq": 0}, r["token"])
    nxt = claim(api, r)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT message_id FROM jobs WHERE id=?", (nxt["job_id"],)).fetchone()[0] == redirect["id"]
        assert H.message(c, redirect["id"])["conversation_id"] == origin["conversation_id"]


def test_a_requester_bot_steers_its_delegated_tasks_running_turn(api):
    from backend.store import H
    r = two_bots(api)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:finance", "Close the books", "March.", "bot:ops", private=False, lint=False)
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued' AND bot_id<>'ops'")
    active = claim(api, r, "ops")
    post(api, f"attempts/{active['id']}/started", {"thread_id": "thread"}, r["token"])
    post(api, "chat/finance", {"text": "Tell ops to use the new ledger"})
    finance = claim(api, r, "finance")
    steer = post(api, "messages", {"to": "ops", "kind": "steer", "text": "Use the new ledger",
                                   "refs": {"task": task["id"]}}, finance["token"])
    assert [m["id"] for m in post(api, f"attempts/{active['id']}/inputs", {}, r["token"])["messages"]] == [steer["id"]]
    # Only the task's requester steers it this way.
    with api.app.state.store.transaction() as c:
        other = H.task_create(c, "human:ana", "Pay the rent", "April.", "bot:ops", private=False, lint=False)
    post(api, "messages", {"to": "ops", "kind": "steer", "text": "Skip it", "refs": {"task": other["id"]}},
         finance["token"], expected=422)


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_a_comment_taken_back_while_the_turn_read_it_does_not_break_the_turns_finish(api, outcome):
    # Dev 194: a comment the run had taken in was deleted before it finished. The input loop read it through
    # H.message, which leaves deleted rows out, so every retry of the finish raised a TypeError (a 500 each).
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner_id": "ops", "title": "Review the report", "body": "Please review."})
    first = claim(api, r, "ops")
    post(api, f"attempts/{first['id']}/started", {"thread_id": "thread"}, r["token"])
    path = f"tasks/{task['id']}/comments"
    kept = post(api, path, {"text": "Also check the totals"})["comment"]
    gone = post(api, path, {"text": "And the appendix"})["comment"]
    assert {m["id"] for m in post(api, f"attempts/{first['id']}/inputs", {}, r["token"])["messages"]} == \
        {kept["id"], gone["id"]}
    for m in (kept, gone):
        post(api, f"attempts/{first['id']}/inputs/{m['id']}/ack", {}, r["token"])
    post(api, f"attempts/{first['id']}/events", {"events": [{"seq": 1, "kind": "message",
                                                              "payload": {"text": "Working on it"}}]}, r["token"])
    post(api, f"{path}/{gone['id']}/delete", {})
    finished = post(api, f"attempts/{first['id']}/complete", {"outcome": outcome, "text": "Reviewed.", "last_seq": 1},
                    r["token"])
    assert finished["outcome"] == outcome
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM attempts WHERE id=?", (first["id"],)).fetchone()[0] == outcome
        assert c.execute("SELECT exit FROM turns WHERE id=?", (first["id"],)).fetchone()[0] == outcome
        # The comment still there settles with the run as before; the one taken back is never held or retried.
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (kept["id"],)).fetchone()[0] == \
            ("completed" if outcome == "completed" else "uncertain")
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (gone["id"],)).fetchone()[0] == \
            ("completed" if outcome == "completed" else "canceled")


def test_a_turn_whose_own_comment_was_taken_back_still_finishes(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner_id": "ops", "title": "Review the report", "body": "Please review."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
    path = f"tasks/{task['id']}/comments"
    said = post(api, path, {"text": "Please look at the totals"})["comment"]
    first = claim(api, r, "ops")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT message_id FROM jobs WHERE id=?", (first["job_id"],)).fetchone()[0] == said["id"]
    post(api, f"attempts/{first['id']}/started", {"thread_id": "thread"}, r["token"])
    post(api, f"{path}/{said['id']}/delete", {})
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "Totals checked.", "last_seq": 0},
         r["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM attempts WHERE id=?", (first["id"],)).fetchone()[0] == "completed"
        assert c.execute("SELECT state FROM jobs WHERE id=?", (first["job_id"],)).fetchone()[0] == "completed"


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_a_comment_taken_back_before_the_runtime_got_it_is_cancelled_not_requeued(api, outcome):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner_id": "ops", "title": "Review the report", "body": "Please review."})
    first = claim(api, r, "ops")
    post(api, f"attempts/{first['id']}/started", {"thread_id": "thread"}, r["token"])
    path = f"tasks/{task['id']}/comments"
    kept = post(api, path, {"text": "Also check the totals"})["comment"]
    gone = post(api, path, {"text": "And the appendix"})["comment"]
    assert {m["id"] for m in post(api, f"attempts/{first['id']}/inputs", {}, r["token"])["messages"]} == \
        {kept["id"], gone["id"]}
    post(api, f"{path}/{gone['id']}/delete", {})
    post(api, f"attempts/{first['id']}/complete", {"outcome": outcome, "text": "Reviewed.", "last_seq": 0,
         "undelivered": [kept["id"], gone["id"]]}, r["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state,attempt_id FROM jobs WHERE message_id=?", (kept["id"],)).fetchone()[:] == \
            ("queued", None)
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (gone["id"],)).fetchone()[0] == "canceled"
        assert not c.execute("SELECT 1 FROM attempt_inputs WHERE message_id IN (?,?)", (kept["id"], gone["id"])).fetchone()


def _a_turn_started_by_a_comment(api, bot="ops", r=None):
    if r is None:
        r = runner(api)
        assign(api, r, bot)
        ready(api, r, [bot])
    task = post(api, "tasks", {"owner_id": bot, "title": "Review the report", "body": "Please review."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
    path = f"tasks/{task['id']}/comments"
    said = post(api, path, {"text": "Please look at the totals"})["comment"]
    attempt = claim(api, r, bot)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT message_id FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == said["id"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread"}, r["token"])
    return r, path, said, attempt


@pytest.mark.parametrize("bot", ["ops", "botops"])
def test_a_turn_whose_own_comment_was_taken_back_keeps_polling_and_posts_no_reply_under_it(api, request, bot):
    # #879/#880: the steering poll read the turn's own comment the way a room does, which leaves a deleted one
    # out, so every poll after the author took it back raised a TypeError (a 500) until the turn ended.
    # BotOps reads it a second time for each follow-up it weighs.
    machine = request.getfixturevalue("botops") if bot == "botops" else None
    r, path, said, attempt = _a_turn_started_by_a_comment(api, bot, machine)
    post(api, f"{path}/{said['id']}/delete", {})
    later = post(api, path, {"text": "Also the appendix"})["comment"]
    polled = post(api, f"attempts/{attempt['id']}/inputs", {}, r["token"])["messages"]
    assert said["id"] not in [m["id"] for m in polled]
    for m in polled:
        post(api, f"attempts/{attempt['id']}/inputs/{m['id']}/ack", {}, r["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Totals checked.", "last_seq": 0},
         r["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0] == "completed"
        # #885's shipped settlement for the turn's own job stays as it is.
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "completed"
        # Nothing stands under the comment that was taken back, and its words are not kept on it.
        assert not c.execute("SELECT 1 FROM messages WHERE in_reply_to=?", (said["id"],)).fetchone()
        assert c.execute("SELECT body FROM messages WHERE id=?", (said["id"],)).fetchone()[0] == ""
        if bot == "ops":
            assert [m["id"] for m in polled] == [later["id"]]


def test_a_reply_does_not_list_a_comment_taken_back_mid_turn_among_those_it_answered(api):
    r, path, said, attempt = _a_turn_started_by_a_comment(api)
    kept = post(api, path, {"text": "Also check the totals"})["comment"]
    gone = post(api, path, {"text": "And the appendix"})["comment"]
    assert {m["id"] for m in post(api, f"attempts/{attempt['id']}/inputs", {}, r["token"])["messages"]} == \
        {kept["id"], gone["id"]}
    for m in (kept, gone):
        post(api, f"attempts/{attempt['id']}/inputs/{m['id']}/ack", {}, r["token"])
    post(api, f"{path}/{gone['id']}/delete", {})
    done = post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Reviewed.", "last_seq": 0},
                r["token"])
    with api.app.state.store.read() as c:
        reply = c.execute("SELECT refs FROM messages WHERE in_reply_to=? AND sender_id='bot:ops'",
                          (said["id"],)).fetchone()
        assert reply, done
        assert json.loads(reply[0])["answers"] == [said["id"], kept["id"]]


def test_background_commands_a_finished_turn_kept_wake_the_bot_once(api):
    r, origin, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "thread"}, r["token"])
    post(api, f"attempts/{a['id']}/complete", {"outcome": "completed", "text": "Suite running.", "last_seq": 0}, r["token"])
    done = {"commands": [{"description": "Run the full suite", "exit": 1, "tail": "2 failed, 3788 passed"},
                         {"description": "Watch PR checks", "killed": True, "ci_watch": {"pr": 958}}]}
    assert post(api, f"attempts/{a['id']}/background-done", done, r["token"]) == {"woke": True}
    # The runner retries a report it is not sure landed: the bot is woken once.
    assert post(api, f"attempts/{a['id']}/background-done", done, r["token"]) == {"woke": False}
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT body,refs,conversation_id FROM messages WHERE recipient_id='bot:ops' AND kind='notice'").fetchall()
        assert c.execute("SELECT state FROM jobs WHERE message_id IN (SELECT id FROM messages WHERE recipient_id='bot:ops' "
                         "AND kind='notice')").fetchone()[0] == "queued"
    assert len(rows) == 1 and rows[0]["conversation_id"] == origin["conversation_id"]
    assert json.loads(rows[0]["refs"])["trigger"] == "background_done"
    assert "exit 1" in rows[0]["body"] and "2 failed, 3788 passed" in rows[0]["body"] and "ran over" in rows[0]["body"]


def test_a_wake_restored_after_a_runner_restart_stores_only_what_the_runner_sent(api):
    # The runner's restart payload (afterwatch.rs, `report` without the turn's redactor): the description was stored
    # scrubbed and the output is withheld. The notice is built from that payload alone, so no granted value is stored.
    r, origin, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "thread"}, r["token"])
    post(api, f"attempts/{a['id']}/complete", {"outcome": "completed", "text": "Deploy running.", "last_seq": 0}, r["token"])
    withheld = ("[output withheld: the runner restarted while this command ran, and no longer holds the run's secrets "
                "to redact it]")
    done = {"commands": [{"description": "Deploy with ••••", "exit": 0, "tail": withheld}]}
    assert post(api, f"attempts/{a['id']}/background-done", done, r["token"]) == {"woke": True}
    assert post(api, f"attempts/{a['id']}/background-done", done, r["token"]) == {"woke": False}
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT body FROM messages WHERE recipient_id='bot:ops' AND kind='notice'").fetchall()
        logged = c.execute("SELECT detail FROM events WHERE action='attempt.background-done'").fetchall()
    assert len(rows) == 1 and withheld in rows[0]["body"] and "Deploy with ••••: exit 0" in rows[0]["body"]
    assert "SYNTH_SECRET" not in rows[0]["body"] and all("SYNTH_SECRET" not in e[0] for e in logged)
