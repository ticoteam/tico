"""Comments route like posts (threads/tasks spec step 4): a task comment reaches whom it addresses, by its way, and
nobody else: a task's comments are a log, so a plain comment reaches nobody, and nothing is guessed from roles. Steven's case: a person's comment on a
ticket a monitor bot filed does not wake the filer. Fictional people only: ana (owner), ben and cara."""

import pytest

from backend import threads
from backend.store import H
from backend.tests.helpers.api import get, headers, post  # noqa: F401  (the fixture and its helpers)


@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.helpers.api import unrelated_task_managers

    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def delivery(api, message_id, recipient):
    found = rows(api, "SELECT * FROM message_deliveries WHERE message_id=? AND recipient=?", message_id, recipient)
    return found[0] if found else None


def bot_jobs(api, bot):
    return rows(api, "SELECT id, message_id, state FROM jobs WHERE bot_id=? AND state='queued'", bot)


def follows(api, tid, actor):
    with api.app.state.store.read() as c:
        thread = H.task(c, tid)["conversation_id"]
        found = c.execute(
            "SELECT following, notify, reason FROM thread_followers WHERE thread_id=? AND actor=?", (thread, actor)
        ).fetchone()
    return tuple(found) if found else None


def comment(api, tid, text, token="ana-test", expected=200, **more):
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": text, **more}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def filed_by_a_bot(api, owner="human:ben"):
    with api.app.state.store.transaction() as c:
        row = H.task_create(
            c, "bot:cpo", "Reservation 42 is double booked", "Two stays overlap on May 3.", owner, lint=False
        )
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    return row


def test_a_comment_on_a_ticket_a_bot_filed_does_not_wake_the_filer(api):
    row = filed_by_a_bot(api)
    sent = comment(api, row["id"], "Looked into it: the second stay was cancelled.", token="ben-test")["comment"]
    assert delivery(api, sent["id"], "bot:cpo") is None and bot_jobs(api, "cpo") == []
    assert follows(api, row["id"], "bot:cpo") is None


def test_an_at_mention_wakes_and_a_later_plain_comment_reaches_nobody(api):
    row = filed_by_a_bot(api)
    sent = comment(api, row["id"], "@cpo can you recheck the calendar?", token="ben-test")["comment"]
    assert [j["message_id"] for j in bot_jobs(api, "cpo")] == [sent["id"]]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    later = comment(api, row["id"], "Fixed on the channel side too.", token="ben-test")
    assert later["comment"]["recipient_id"] is None and bot_jobs(api, "cpo") == []
    assert rows(api, "SELECT 1 FROM message_deliveries WHERE message_id=?", later["comment"]["id"]) == []


def test_defer_never_queues_the_mentioned_and_reaches_nobody_else(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    sent = comment(api, row["id"], "@assistant fyi, no rush", delivery="defer", defer_for="never")["comment"]
    assert sent["recipient_id"] == "bot:assistant" and len(bot_jobs(api, "assistant")) == 1, (
        "the @mention is reached, queued"
    )
    assert bot_jobs(api, "ops") == [] and delivery(api, sent["id"], "bot:ops") is None, "the holder is not reached"


def test_people_get_only_what_names_them(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    plain = comment(api, row["id"], "Status: waiting on the bank.")["comment"]
    assert delivery(api, plain["id"], "human:ben") is None and delivery(api, plain["id"], "human:cara") is None
    named = comment(api, row["id"], "@ben and @cara, can you confirm the totals?")["comment"]
    assert delivery(api, named["id"], "human:ben")["reason"] == "mention"
    assert delivery(api, named["id"], "human:cara")["reason"] == "mention"


def test_a_bots_at_mention_of_a_bot_wakes_it_and_its_plain_comment_reaches_nobody(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
        plain = H.task_comment(c, "bot:ops", row["id"], "Reconciled the bank feed.")
    assert rows(api, "SELECT 1 FROM message_deliveries WHERE message_id=?", plain["id"]) == []
    with api.app.state.store.transaction() as c:
        named = H.task_comment(c, "bot:ops", row["id"], "@assistant can you sign off on the close?")
    jobs = bot_jobs(api, "assistant")
    assert len(jobs) == 1 and jobs[0]["message_id"] in (
        named["id"],
        (delivery(api, named["id"], "bot:assistant") or {}).get("carrier_id"),
    ), "an @mention wakes by the way"


def test_the_loop_guard_caps_bots_at_mentioning_each_other(api, monkeypatch):
    monkeypatch.setattr(threads, "LOOP_GUARD_PER_HOUR", 2)
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
        for n in range(4):
            H.task_comment(c, "bot:ops", row["id"], f"@assistant round {n}, over to you.")
    root = "(SELECT conversation_id FROM tasks WHERE id=?)"
    capped = rows(api, f"SELECT 1 FROM message_deliveries WHERE root_id={root} AND state='capped'", row["id"])
    woke = rows(
        api,
        f"SELECT 1 FROM message_deliveries WHERE root_id={root} AND cause='bot' AND recipient='bot:assistant' "
        "AND mode IN ('steer','queue') AND state<>'capped'",
        row["id"],
    )
    assert capped and len(woke) <= 2


def test_a_bots_mention_on_a_private_task_adds_a_reader_only_within_its_reach(api):
    with api.app.state.store.transaction() as c:
        row = H.task_create(
            c, "human:ana", "Payroll review", "Confidential: payroll.", "bot:ops", private=True, lint=False
        )
    r = api.put("/api/v2/reach/settings/bot:ops", json={"mode": "closed"}, headers=headers())
    assert r.status_code == 200, r.text
    with api.app.state.store.transaction() as c:
        said = H.task_comment(c, "bot:ops", row["id"], "@ben can you check the totals?")
        assert "human:ben" not in H.task_readers(H.task(c, row["id"]))
        assert (
            c.execute(
                "SELECT in_audience FROM message_mentions WHERE message_id=? AND actor='human:ben'", (said["id"],)
            ).fetchone()[0]
            == 0
        )
    assert delivery(api, said["id"], "human:ben") is None
    # A person who reads the task widens it with an @mention, and the mentioned reader follows it.
    sent = comment(api, row["id"], "@ben please check the totals.")["comment"]
    with api.app.state.store.read() as c:
        assert "human:ben" in H.task_readers(H.task(c, row["id"]))
    assert delivery(api, sent["id"], "human:ben")["reason"] == "mention"


def test_the_reach_preview_answers_who_a_message_would_reach(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    thread = get(api, "tasks/" + row["id"])["task"]["conversation_id"]

    def preview(**params):
        r = api.get(f"/api/v2/threads/{thread}/reach", params=params, headers=headers())
        assert r.status_code == 200, r.text
        return r.json()

    plain = preview(text="Status update")
    assert plain["way"] == "steer" and plain["reaches"] == [], "a plain comment reaches nobody"
    named = preview(text="@assistant and @ben, see this", delivery="defer", defer_for="never")
    assert {x["actor"]: (x["reason"], x["mode"]) for x in named["reaches"]} == {
        "bot:assistant": ("mention", "queue"),
        "human:ben": ("mention", "queue"),
    }
    r = api.get(f"/api/v2/threads/{thread}/reach", params={"delivery": "steer", "defer_for": "4h"}, headers=headers())
    assert r.status_code == 422, r.text
    assert rows(api, "SELECT 1 FROM messages WHERE conversation_id=? AND body LIKE '%see this%'", thread) == []


def test_a_readers_mention_reaches_the_bot_but_delegates_nothing(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
        c.execute("DELETE FROM task_delegations")
    sent = comment(api, row["id"], "@assistant can you check the May totals?", token="ben-test")["comment"]
    assert sent["recipient_id"] == "bot:assistant" and len(bot_jobs(api, "assistant")) == 1, (
        "the @mention is reached, queued"
    )
    assert rows(api, "SELECT delegate FROM task_delegations WHERE task_id=?", row["id"]) == [], (
        "only someone who may direct the task hands its bots party rights"
    )
    comment(api, row["id"], "@finance please check them too.")
    assert "bot:finance" in {
        r["delegate"] for r in rows(api, "SELECT delegate FROM task_delegations WHERE task_id=?", row["id"])
    }


def test_a_defer_on_a_task_filed_into_a_shared_chat_keeps_its_deadline(api):
    from backend.tests.threads_e2e import create_thread

    room = create_thread(api, title="Billing", members=["bot:ops"])["id"]
    with api.app.state.store.transaction() as c:  # filed into the chat, with no task room of its own
        legacy = H.task_create(
            c, "human:ana", "Plan the billing notes", "For the team.", "bot:ops", conversation_id=room, own_room=False
        )
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
    assert legacy["conversation_id"] == room
    sent = comment(api, legacy["id"], "Whenever you get to it today.", delivery="defer", defer_for="4h")
    assert sent["comment"]["recipient_id"] == "bot:ops" and bot_jobs(api, "ops") == []
    due = rows(api, "SELECT due_at FROM deferrals WHERE item=?", sent["comment"]["id"])
    assert due and due[0]["due_at"], "the owner takes the deferral, with its deadline"
    assert sent["sent_as"] == {"delivery": "defer", "until": due[0]["due_at"]}


def test_a_reply_reaches_its_author_only_while_they_read_the_thread_and_asks_need_them(api):
    from backend.open_mentions import rows as open_mentions
    from backend.tests.threads_e2e import call, create_thread, ok, remove_member, say

    t = create_thread(api, title="Venue", members=["human:ben", "human:cara"])
    said = say(api, "cara-test", t["id"], "Friday or Saturday?")
    said = said.get("message", said)
    reply = ok(
        call(api, "POST", f"threads/{t['id']}/messages", "ana-test", {"text": "Friday.", "in_reply_to": said["id"]})
    )
    reply = reply.get("message", reply)
    assert delivery(api, reply["id"], "human:cara")["reason"] == "reply"
    with api.app.state.store.read() as c:
        assert reply["id"] in [m["id"] for m in open_mentions(c, "human:cara")], "Needs you lists a reply to the person"
    ok(remove_member(api, "ana-test", t["id"], "human:cara"))
    again = ok(
        call(
            api,
            "POST",
            f"threads/{t['id']}/messages",
            "ana-test",
            {"text": "Actually, Saturday.", "in_reply_to": said["id"]},
        )
    )
    again = again.get("message", again)
    assert delivery(api, again["id"], "human:cara") is None, "a past reader is reached no more"


def test_joining_a_tasks_thread_follows_nobody(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile and close May."})
    assert follows(api, row["id"], "human:cara") is None
    with api.app.state.store.transaction() as c:
        threads._joined(c, row["conversation_id"], "human:cara")
    assert follows(api, row["id"], "human:cara")[:3] == (0, "live", "joined")
