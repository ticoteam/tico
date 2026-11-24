"""A task's comments are a log, and each agent on it has its own session view (docs/tasks.md, Comments and sessions).

A plain comment wakes and notifies nobody; an @mention or a person's Reply reaches whom it names, an agent in its own
session; a message `to` an agent is that agent's session message. Every message an agent's session holds is stamped
(`messages.session_agent_id`) in the task's one thread: the session view, the comments view and each agent's replay
read by it. Fictional people only: ana (the owner, who files and directs), ben (a teammate who may not direct)."""

import pytest

from backend import task_privacy
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, headers, post, ready, runner  # noqa: F401


@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.helpers.api import unrelated_task_managers

    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def jobs(api, bot):
    return [r["message_id"] for r in rows(api, "SELECT message_id FROM jobs WHERE bot_id=? AND state='queued'", bot)]


def settle(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")


def ticket(api, owner="ops", **more):
    row = post(api, "tasks", {"owner": owner, "title": "Close the month", "body": "Reconcile and close May.", **more})
    settle(api)
    return row


def comment(api, tid, text, token="ana-test", expected=200, **more):
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": text, **more}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def agent_says(api, bot, tid, text, **more):
    with api.app.state.store.transaction() as c:
        return H.task_comment(c, bot, tid, text, **more)


def view(api, tid, token="ana-test", **params):
    r = api.get(f"/api/v2/tasks/{tid}/comments", params=params, headers=headers(token))
    assert r.status_code == 200, r.text
    return [m["id"] for m in r.json()["comments"]]


def sessions(api, tid):
    return {s["agent"]: s["last_seq"] for s in get(api, "tasks/" + tid)["sessions"]}


def test_a_plain_comment_on_a_held_ticket_starts_no_run_and_notifies_nobody(api):
    row = ticket(api)
    for way in (None, "steer"):
        sent = comment(api, row["id"], "The bank feed is back.", **({"delivery": way} if way else {}))
        assert (sent["woke"], sent["delivered"]) == (False, False), "the answer says nobody was reached"
        assert sent["comment"]["recipient_id"] is None and jobs(api, "ops") == []
        assert rows(api, "SELECT 1 FROM message_deliveries WHERE message_id=?", sent["comment"]["id"]) == []
    assert get(api, "tasks/" + row["id"])["comment_to"] == {"bot": None, "wakes": False, "mention": []}


def test_a_persons_reply_wakes_the_agent_author_in_its_session(api):
    row = ticket(api)
    said = agent_says(api, "bot:ops", row["id"], "Built it; the PR is up.")
    thread = row["conversation_id"]
    reach = api.get(f"/api/v2/threads/{thread}/reach", params={"in_reply_to": said["id"]}, headers=headers())
    assert [(r["actor"], r["reason"]) for r in reach.json()["reaches"]] == [("bot:ops", "mention")], reach.text
    # Only `in_reply_to`, as the CLI and the API send it: no @ in the text.
    reply = comment(api, row["id"], "Please add a test for the refund path.", in_reply_to=said["id"])["comment"]
    assert reply["in_reply_to"] == said["id"] and reply["recipient_id"] == "bot:ops"
    assert jobs(api, "ops") == [reply["id"]], "the agent author is woken"
    assert reply["body"] == "Please add a test for the refund path.", "nothing is added to the text"
    assert reply["id"] in view(api, row["id"], session="bot:ops"), "the Reply is in its session view"
    assert set(view(api, row["id"], comments="true")) >= {said["id"], reply["id"]}, "and under the comment"
    comment(api, row["id"], "Thanks.", in_reply_to="not-a-message", expected=422)


def test_a_reply_to_an_agent_that_cannot_take_a_turn_reaches_nobody(api):
    row = ticket(api)
    said = agent_says(api, "bot:finance", row["id"], "Totals checked.")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE id=(SELECT id FROM bots WHERE slug='finance')")
    reach = api.get(
        f"/api/v2/threads/{row['conversation_id']}/reach", params={"in_reply_to": said["id"]}, headers=headers()
    )
    assert reach.json()["reaches"] == [], reach.text
    sent = comment(api, row["id"], "Which totals?", in_reply_to=said["id"])
    assert sent["woke"] is False and jobs(api, "finance") == []


def test_an_at_mention_runs_the_agent_and_its_answer_nests_under_the_comment(api):
    row = ticket(api)
    asked = comment(api, row["id"], "@finance can you check the May totals?")["comment"]
    assert jobs(api, "finance") == [asked["id"]]
    assert set(sessions(api, row["id"])) == {"bot:ops", "bot:finance"}, "the woken agent shows before it replies"
    machine = runner(api)
    assign(api, machine, "finance")
    ready(api, machine, ["finance"])
    turn = claim(api, machine, "finance")
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"][:8]}, machine["token"])
    post(
        api,
        f"attempts/{turn['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Totals match the bank."},
        machine["token"],
    )
    answers = rows(
        api, "SELECT id, in_reply_to, session_agent_id FROM messages WHERE sender_id='bot:finance' AND kind='say'"
    )
    assert answers == [{"id": answers[0]["id"], "in_reply_to": asked["id"], "session_agent_id": "bot:finance"}], (
        "it answered once, under the comment, in its own session"
    )
    answer = answers[0]["id"]
    assert {asked["id"], answer} <= set(view(api, row["id"], comments="true")), "the answer shows under the comment"
    assert {asked["id"], answer} <= set(view(api, row["id"], session="bot:finance"))
    assert not {asked["id"], answer} & set(view(api, row["id"], session="bot:ops")), "not in another agent's session"
    assert sessions(api, row["id"])["bot:finance"] is not None


def test_the_sessions_list_names_an_agent_by_its_mentions_not_deleted_and_by_a_run_alone(api):
    row = ticket(api)
    asked = comment(api, row["id"], "@finance can you check the May totals?")["comment"]
    assert sessions(api, row["id"])["bot:finance"] == asked["seq"], "the newest message its view shows"
    post(api, f"tasks/{row['id']}/comments/{asked['id']}/delete", {})
    assert "bot:finance" not in sessions(api, row["id"]), "a taken-back mention leaves no session"
    with api.app.state.store.transaction() as c:  # a run on the task, nothing written yet
        c.execute(
            "INSERT INTO attempts (id, bot_id, state, created_at, task_id) VALUES (?, 'cpo', 'running', ?, ?)",
            (H.new_id(), H.now(), row["id"]),
        )
    assert sessions(api, row["id"])["bot:cpo"] is None


def test_the_sessions_list_names_an_agent_with_a_waiting_job_and_dates_it_by_what_its_view_shows(api):
    row = ticket(api)
    [mark] = asks(api, row["id"], "bot:finance")
    answer = comment(api, row["id"], "Use the Chase feed.", ask=mark)["comment"]
    assert jobs(api, "finance") == [answer["id"]]
    assert "bot:finance" in sessions(api, row["id"]), "the asker the pinned answer woke, before its run is claimed"
    # An agent's reply to another agent reaches it at its next turn (reason reply): not in that agent's session view,
    # so it dates nothing there.
    with api.app.state.store.transaction() as c:
        theirs = H.task_comment(c, "bot:cpo", row["id"], "Starting on the refunds.")
        c.execute("UPDATE messages SET session_agent_id='bot:cpo' WHERE id=?", (theirs["id"],))  # as its run's would be
        reply = H.task_comment(c, "bot:ops", row["id"], "Thanks, noted.", in_reply_to=theirs["id"])
    assert rows(
        api, "SELECT reason FROM message_deliveries WHERE message_id=? AND recipient='bot:cpo'", reply["id"]
    ) == [{"reason": "reply"}]
    assert reply["id"] not in view(api, row["id"], session="bot:cpo")
    assert sessions(api, row["id"])["bot:cpo"] == theirs["seq"]


def test_a_runs_snapshot_leaves_out_a_step_the_stream_would_withhold(api):
    """The run a task's page (or an agent's session view) shows while it works passes each step through the stream's
    own check (task_privacy.content_readable, as events._step_shown): a step naming a private task the reader cannot
    read is in neither."""
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile May."})
    secret = post(api, "tasks", {"owner": "human:ana", "title": "Payroll", "body": "Ana only.", "private": True})
    turn = claim(api, machine, "ops")
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t1"}, machine["token"])
    steps = [
        {"seq": 1, "kind": "delta", "payload": {"text": "Reconciling May. "}},
        {"seq": 2, "kind": "delta", "payload": {"text": "Checking the payroll task too.", "task": secret["id"]}},
    ]
    post(api, f"attempts/{turn['id']}/events", {"events": steps}, machine["token"])
    ben = Identity("human:ben", "human")
    with api.app.state.store.read() as c:
        stored = [
            dict(e) for e in c.execute("SELECT * FROM attempt_events WHERE attempt_id=? ORDER BY seq", (turn["id"],))
        ]
        shown = [task_privacy.content_readable(c, task_privacy.actor(ben), e) for e in stored]
    assert shown == [True, False], "the stream sends Ben the first step only"
    for path in (f"tasks/{row['id']}", f"tasks/{row['id']}/execution?agent=ops"):
        seen = get(api, path, "ben-test")["execution"]
        assert seen and "Reconciling May." in seen["text"] and "payroll" not in str(seen), path
    assert "payroll" in get(api, f"tasks/{row['id']}")["execution"]["text"], "Ana reads both"


def test_two_agents_each_have_their_own_session_and_replay_skips_the_other(api):
    row = ticket(api)
    to_ops = comment(api, row["id"], "Ship the fix once the checks pass.", to="ops")["comment"]
    to_finance = comment(api, row["id"], "Check the refund totals.", to="bot:finance")["comment"]
    plain = comment(api, row["id"], "For the record: May closes Friday.")["comment"]
    stamps = {
        r["id"]: r["session_agent_id"]
        for r in rows(api, "SELECT id, session_agent_id FROM messages WHERE conversation_id=?", row["conversation_id"])
    }
    assert (stamps[to_ops["id"]], stamps[to_finance["id"]], stamps[plain["id"]]) == ("bot:ops", "bot:finance", None)
    assert jobs(api, "ops") == [to_ops["id"]] and jobs(api, "finance") == [to_finance["id"]], "each is woken"
    mine = set(view(api, row["id"], session="ops"))
    assert to_ops["id"] in mine and to_finance["id"] not in mine and plain["id"] not in mine
    theirs = set(view(api, row["id"], session="bot:finance"))
    assert to_finance["id"] in theirs and to_ops["id"] not in theirs
    logged = set(view(api, row["id"], comments="true"))
    assert plain["id"] in logged and not {to_ops["id"], to_finance["id"]} & logged
    timeline = api.get(
        f"/api/v2/threads/{row['conversation_id']}/timeline", params={"session": "bot:ops"}, headers=headers()
    ).json()["messages"]
    assert to_ops["id"] in {m["id"] for m in timeline} and to_finance["id"] not in {m["id"] for m in timeline}
    assert {s: True for s in sessions(api, row["id"])} == {"bot:ops": True, "bot:finance": True}
    with api.app.state.store.read() as c:
        replay = task_privacy.page(c, Identity("bot:ops", "bot"), row["conversation_id"], limit=100, session="bot:ops")[
            "messages"
        ]
    seen = {m["id"] for m in replay}
    assert to_ops["id"] in seen and plain["id"] in seen and to_finance["id"] not in seen


def test_a_carrier_is_in_the_session_of_the_agent_it_was_written_for(api):
    """Ana writes in Ops's session and mentions Finance: Tico's carrier that wakes Finance copies her words, so it is
    Finance's session message, never CPO's (who is on the ticket too, but was not mentioned)."""
    row = ticket(api)
    said = comment(api, row["id"], "@finance please check the refund totals first.", to="ops")["comment"]
    [carrier] = rows(
        api,
        "SELECT id, session_agent_id FROM messages WHERE json_extract(refs,'$.carries')=? AND recipient_id='bot:finance'",
        said["id"],
    )
    assert carrier["session_agent_id"] == "bot:finance"

    def replay(bot):
        with api.app.state.store.read() as c:
            page = task_privacy.page(c, Identity(bot, "bot"), row["conversation_id"], limit=100, session=bot)
        return {m["id"] for m in page["messages"]}

    assert carrier["id"] not in replay("bot:cpo") and said["id"] not in replay("bot:cpo")
    assert carrier["id"] in replay("bot:finance") and said["id"] in replay("bot:finance")


def test_a_session_message_is_refused_to_whoever_may_not_direct_and_for_an_agent_off_the_ticket(api):
    row = ticket(api)
    comment(api, row["id"], "Ship it.", token="ben-test", to="ops", expected=403)
    comment(api, row["id"], "Ship it.", to="ops", public=True, expected=422)
    said = agent_says(api, "bot:ops", row["id"], "Ready to ship.")
    comment(api, row["id"], "Ship it.", to="ops", in_reply_to=said["id"], expected=422)
    comment(api, row["id"], "Ship it.", to="nobody-by-that-name", expected=422)
    with api.app.state.store.transaction() as c:
        hidden = H.task_create(c, "human:ana", "Payroll review", "Confidential.", "human:ana", private=True, lint=False)
    comment(api, hidden["id"], "Check the payroll totals.", to="finance", expected=422)
    assert jobs(api, "finance") == []
    assert get(api, "tasks/" + row["id"], token="ben-test")["can_direct"] is False
    assert get(api, "tasks/" + row["id"])["can_direct"] is True


def test_the_thread_routes_refuse_to_on_a_tasks_thread(api):
    """`to` writes to an agent's session only through the task's own route, with its checks: the generic thread post and
    upload refuse it on a task's thread, and nothing is written or woken."""
    row = ticket(api)
    cid = row["conversation_id"]
    r = api.post(
        f"/api/v2/threads/{cid}/messages", json={"text": "Ship it.", "to": "bot:ops"}, headers=headers("ben-test")
    )
    assert r.status_code == 422 and "task_to" in r.text, r.text
    r = api.post(
        f"/api/v2/uploads/threads/{cid}/messages",
        data={"text": "Ship it.", "to": "bot:ops"},
        files={"files": ("notes.txt", b"ship notes", "text/plain")},
        headers=headers("ben-test"),
    )
    assert r.status_code == 422 and "task_to" in r.text, r.text
    assert rows(api, "SELECT 1 FROM messages WHERE body='Ship it.'") == [] and jobs(api, "ops") == []


def test_two_agents_answering_each_other_wake_each_other_at_most_once(api):
    row = ticket(api)
    plain = agent_says(api, "bot:ops", row["id"], "Reconciled the bank feed.")
    answered = agent_says(api, "bot:finance", row["id"], "Looks right to me.", in_reply_to=plain["id"])
    held = rows(
        api,
        "SELECT mode, carrier_id FROM message_deliveries WHERE message_id=? AND recipient='bot:ops'",
        answered["id"],
    )
    assert held == [{"mode": "defer", "carrier_id": None}] and jobs(api, "ops") == [], "a plain answer waits"
    asked = agent_says(api, "bot:ops", row["id"], "Can you confirm the totals?", extra_refs={"needs_action": True})
    settle(api)
    agent_says(api, "bot:finance", row["id"], "Confirmed.", in_reply_to=asked["id"])
    assert len(jobs(api, "ops")) == 1, "an answer to what it asked wakes it"


def asks(api, tid, *bots, person="human:ana"):
    from backend import question_marks

    with api.app.state.store.transaction() as c:
        marks = [question_marks.ask(c, bot, tid, person, f"{bot}: which bank feed should I use?")["id"] for bot in bots]
    settle(api)
    return marks


def open_asks(api, tid):
    return {
        r["bot_id"] for r in rows(api, "SELECT bot_id FROM question_marks WHERE task_id=? AND cleared_at IS NULL", tid)
    }


def test_the_pinned_answer_settles_its_ask_and_wakes_its_asker(api):
    row = ticket(api)
    mark, _ = asks(api, row["id"], "bot:ops", "bot:finance")
    sent = comment(api, row["id"], "Use the Chase feed.", ask=mark)["comment"]
    assert open_asks(api, row["id"]) == {"bot:finance"}, "exactly that ask is settled"
    assert jobs(api, "ops") == [sent["id"]] and jobs(api, "finance") == []


def test_a_plain_comment_that_clears_two_agents_asks_reaches_both(api):
    row = ticket(api)
    asks(api, row["id"], "bot:ops", "bot:finance")
    sent = comment(api, row["id"], "Use the Chase feed for both.")["comment"]
    assert open_asks(api, row["id"]) == set()
    assert len(jobs(api, "ops")) == 1 and len(jobs(api, "finance")) == 1, "each asker hears the answer"
    reached = rows(
        api, "SELECT recipient FROM message_deliveries WHERE message_id=? AND recipient LIKE 'bot:%'", sent["id"]
    )
    assert {r["recipient"] for r in reached} == {"bot:ops", "bot:finance"}


@pytest.mark.parametrize("way", ["defer", "queue"])
def test_a_plain_comment_that_clears_asks_reaches_each_asker_by_its_way(api, way):
    row = ticket(api)
    asks(api, row["id"], "bot:ops", "bot:finance")
    more = {"delivery": way, **({"defer_for": "2h"} if way == "defer" else {})}
    sent = comment(api, row["id"], "Use the Chase feed for both.", **more)["comment"]
    reached = rows(
        api,
        "SELECT recipient, mode FROM message_deliveries WHERE message_id=? AND recipient LIKE 'bot:%' ORDER BY recipient",
        sent["id"],
    )
    assert reached == [{"recipient": "bot:finance", "mode": way}, {"recipient": "bot:ops", "mode": way}]


def test_a_message_in_one_agents_session_clears_only_that_agents_ask(api):
    row = ticket(api)
    asks(api, row["id"], "bot:ops", "bot:finance")
    sent = comment(api, row["id"], "Use the Chase feed.", to="finance")["comment"]
    assert open_asks(api, row["id"]) == {"bot:ops"}
    assert jobs(api, "finance") == [sent["id"]] and jobs(api, "ops") == []


def test_a_done_note_is_one_comment_by_the_agent_whose_mentions_reach_nobody(api):
    from backend import task_calls
    from backend import task_engine as E

    row = ticket(api)
    with api.app.state.store.transaction() as c:
        for _ in range(2):  # a retry
            H.task_done(
                c, "bot:ops", row["id"], "Closed May; @ben please file the report.", H.task(c, row["id"])["version"]
            )
    notes = rows(api, "SELECT id, sender_id, session_agent_id, wake_key FROM messages WHERE body LIKE 'Closed May;%'")
    assert len(notes) == 1 and notes[0]["sender_id"] == "bot:ops" and notes[0]["session_agent_id"] is None
    assert notes[0]["id"] in view(api, row["id"], comments="true")
    assert rows(api, "SELECT 1 FROM message_deliveries WHERE message_id=?", notes[0]["id"]) == []
    # Done again on one visit (after a send back): the same comment, updated in place.
    again = ticket(api)
    with api.app.state.store.transaction() as c:
        visit = E.task(c, again["id"])["visit"]["id"]
        task_calls.done_note(c, "bot:ops", again["id"], "First pass done.", visit)
        task_calls.done_note(c, "bot:ops", again["id"], "Second pass done, with the fix.", visit)
    said = rows(api, "SELECT body, edited_at FROM messages WHERE body LIKE '%pass done%'")
    assert len(said) == 1 and said[0]["body"] == "Second pass done, with the fix." and said[0]["edited_at"]
    # Deleted by its author, it stays deleted: done again on that visit posts nothing and raises nothing.
    with api.app.state.store.transaction() as c:
        mine = c.execute("SELECT id FROM messages WHERE body LIKE 'Second pass done%'").fetchone()[0]
        H.task_comment_delete(c, "bot:ops", again["id"], mine)
        task_calls.done_note(c, "bot:ops", again["id"], "Third pass done.", visit)
    assert rows(api, "SELECT 1 FROM messages WHERE body LIKE 'Third pass%'") == []


def told(api, person):
    return rows(
        api,
        "SELECT m.body FROM message_deliveries d JOIN messages m ON m.id=d.message_id "
        "WHERE d.recipient=? AND json_extract(m.refs,'$.told') IS NOT NULL ORDER BY m.rowid",
        person,
    )


def test_a_human_requester_hears_the_end_once_per_completion(api):
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, "human:ben", "Close the month", "Reconcile May.", "bot:ops", lint=False)
        for _ in range(2):  # a retry
            H.task_done(c, "bot:ops", row["id"], "May is closed.", H.task(c, row["id"])["version"])
    assert [r["body"] for r in told(api, "human:ben")] == ["Done: May is closed."]
    with api.app.state.store.transaction() as c:
        H.task_move(c, "human:ana", row["id"], H.TASK_REOPEN, "One more account", mover=True)
        H.task_close(c, "human:ana", row["id"], "Moved to June", mover=True)
    assert [r["body"] for r in told(api, "human:ben")] == ["Done: May is closed.", "Closed: Moved to June"]
    assert told(api, "human:ana") == []
    # A requester who also holds it hears it too, unless they ended it themselves.
    with api.app.state.store.transaction() as c:
        own = H.task_create(c, "human:ben", "File the May report", "For the board.", "human:ben", lint=False)
        H.task_close(c, "human:ana", own["id"], "Not needed this month", mover=True)
    assert told(api, "human:ben")[-1]["body"] == "Closed: Not needed this month"
    # An agent requester gets only its quiet report, as before.
    with api.app.state.store.transaction() as c:
        filed = H.task_create(c, "bot:cpo", "Check the refunds", "For May.", "bot:ops", lint=False)
        H.task_done(c, "bot:ops", filed["id"], "Refunds checked.", H.task(c, filed["id"])["version"])
    assert rows(api, "SELECT 1 FROM messages WHERE recipient_id='bot:cpo' AND wake_key LIKE '%:result'")
    assert rows(api, "SELECT 1 FROM messages WHERE wake_key LIKE ?", f"{filed['id']}:%:told") == []


def test_the_end_line_reaches_only_the_requesters_whoever_the_note_mentions(api):
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, "human:ben", "Close the month", "Reconcile May.", "bot:ops", lint=False)
        H.task_done(c, "bot:ops", row["id"], "May is closed; @ana @cpo please check.", H.task(c, row["id"])["version"])
    [line] = rows(api, "SELECT id FROM messages WHERE wake_key LIKE ?", f"{row['id']}:%:told")
    reached = rows(api, "SELECT recipient FROM message_deliveries WHERE message_id=?", line["id"])
    assert [r["recipient"] for r in reached] == ["human:ben"]


def _turn_for(api, row, text, bot="ops"):
    """A person's message to `bot`'s session on the ticket, and the turn it starts, begun."""
    comment(api, row["id"], text, to=bot)
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    turn = claim(api, machine, bot)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"][:8]}, machine["token"])
    return machine, turn


def _finish(api, machine, turn, text):
    body = {"outcome": "completed", "last_seq": 0, "text": text}
    post(api, f"attempts/{turn['id']}/complete", body, machine["token"], key="completion")
    return rows(api, "SELECT recipient_id FROM messages WHERE sender_id='bot:ops' AND body=?", text)


@pytest.mark.parametrize("mid_turn", ["Uploaded the bank export.", "@ana uploaded the bank export.", "ask"])
def test_a_turn_answers_the_person_who_started_it_unless_its_post_already_reached_someone(api, mid_turn):
    row = ticket(api)
    machine, turn = _turn_for(api, row, "Please reconcile the bank feed.")
    if mid_turn == "ask":
        post(api, f"tasks/{row['id']}/questions", {"to": "ana", "text": "Which month first?"}, turn["token"])
    else:
        agent_says(api, "bot:ops", row["id"], mid_turn)  # a comment with files, say: the turn may post those
    answered = _finish(api, machine, turn, "Reconciled; two items differ.")
    if mid_turn == "Uploaded the bank export.":
        assert answered == [{"recipient_id": "human:ana"}], "a post that reached nobody is no answer"
    else:
        assert answered == [], "its mention or ❓ already reached ana: the turn does not post twice"


def test_the_demo_answers_a_session_message_in_that_agents_session(api):
    """The demo's stand-in answer (demo.tick) is posted as a run's reply is: in its agent's session, never the comments."""
    from backend import demo

    row = ticket(api)
    sent = comment(api, row["id"], "Can retries resume from the last chunk?", to="ops")["comment"]
    assert jobs(api, "ops") == [sent["id"]]
    demo.tick(api.app.state.store)
    answer = rows(
        api, "SELECT id, body, session_agent_id FROM messages WHERE sender_id='bot:ops' AND in_reply_to=?", sent["id"]
    )
    assert [(m["body"], m["session_agent_id"]) for m in answer] == [(demo.BOT_REPLY, "bot:ops")]
    logged, session = set(view(api, row["id"], comments="true")), set(view(api, row["id"], session="ops"))
    assert {sent["id"], answer[0]["id"]} <= session and not {sent["id"], answer[0]["id"]} & logged, "not a comment"
