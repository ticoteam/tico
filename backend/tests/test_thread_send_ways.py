"""Steer, queue or defer for a person's message in a bot's Home or a DM with it (backend/thread_api.py `delivery`,
backend/delivery.py): a post that names no way goes exactly as before, into the bot's running turn; queue waits for that
turn; defer starts no run until its deadline; a deadline takes the right to message the bot. Fictional people only."""

import pytest

from backend import delivery, task_privacy
from backend.store import H
from backend.tests.test_api import api, claim, headers, post  # noqa: F401  (the fixture and its helpers)
from backend.tests.test_delivery import fired, job_of, rows, setup, tick
from backend.tests.test_thread_api import set_bot_access


def dm(api, text, token="ana-test", **way):
    made = post(api, "threads/dm", {"members": ["bot:ops"], "body": text, **way}, token=token)
    return made["id"], made["message"]


def say(api, thread_id, text, token="ana-test", expected=200, **way):
    return post(api, f"threads/{thread_id}/messages", {"text": text, **way}, token=token, expected=expected)


def home(api):
    r = api.get("/api/v2/threads/home/bot:ops", headers=headers())
    assert r.status_code == 200, r.text
    return r.json()["id"]


def carrier_of(api, message_id):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT carrier_id, mode FROM message_deliveries WHERE message_id=? AND carrier_id IS NOT NULL",
                        (message_id,)).fetchone()
        assert row, "no carrier"
        refs = H.message(c, row["carrier_id"])["refs"]
    return row["carrier_id"], row["mode"], refs


def started(api, machine):
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "dm"}, machine["token"])
    return attempt


def cara_dm(api, machine=None):
    """Cara (a plain member; Ben is an admin) opens a DM with Ops, its run starts when `machine` is given, then she keeps
    reading Ops but may no longer message it (Write)."""
    set_bot_access(api, "ops", {"people": ["cara"]}, {"people": ["cara"]}, {"people": ["cara"]})
    tid, _ = dm(api, "Hello.", token="cara-test")
    if machine:
        started(api, machine)
    set_bot_access(api, "ops", {"people": ["cara"]}, {"people": ["cara"]}, {"people": ["ben"]})
    return tid


def test_a_dm_post_with_no_way_steers_as_before_and_queue_waits_for_the_turn(api):
    machine = setup(api)
    tid, first = dm(api, "Morning numbers?")
    assert "sent_as" not in first and job_of(api, first["id"])
    attempt = started(api, machine)
    plain = say(api, tid, "Skip the charts.")
    queued = say(api, tid, "And the refunds.", delivery="queue")
    steered = say(api, tid, "Use the new copy.", delivery="steer")
    assert "sent_as" not in plain and plain["refs"].get("delivery") is None and "send_way" not in plain["refs"]
    assert queued["sent_as"] == {"delivery": "queue", "until": None}
    assert steered["sent_as"] == {"delivery": "steer", "until": None} and steered["refs"].get("delivery") is None
    taken = [m["id"] for m in post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])["messages"]]
    assert plain["id"] in taken and steered["id"] in taken and queued["id"] not in taken
    assert job_of(api, queued["id"])[1] == "queued"


def test_a_dm_defer_starts_no_run_until_its_deadline_and_with_none_never(api):
    setup(api)
    tid, never = dm(api, "Some day: the yearly view.", delivery="defer")
    assert never["sent_as"] == {"delivery": "defer", "until": None} and job_of(api, never["id"]) is None
    [row] = rows(api, item=never["id"])
    assert row["kind"] == "message" and row["due_at"] is None and row["conversation_id"] == tid
    soon = say(api, tid, "Billing first.", delivery="defer", defer_for="30m")
    assert soon["sent_as"]["delivery"] == "defer" and soon["sent_as"]["until"] and job_of(api, soon["id"]) is None
    tick(api, 20)
    assert fired(api) == []
    tick(api, 31)
    assert len(fired(api)) == 1 and job_of(api, soon["id"])


def test_a_homes_carrier_takes_the_way_and_a_plain_post_is_unchanged(api):
    setup(api)
    tid = home(api)
    plain = say(api, tid, "Plain one.")
    carrier, mode, refs = carrier_of(api, plain["id"])
    assert "sent_as" not in plain and mode == "queue" and "delivery" not in refs and not refs.get("quiet")
    assert job_of(api, carrier)[1] == "queued"

    queued = say(api, tid, "After this turn.", delivery="queue")
    carrier, _, refs = carrier_of(api, queued["id"])
    assert refs["delivery"] == "queue" and job_of(api, carrier)[1] == "queued"

    deferred = say(api, tid, "Next time you look.", delivery="defer", defer_for="1h")
    carrier, mode, refs = carrier_of(api, deferred["id"])
    assert mode == "defer" and refs["quiet"] is True and job_of(api, carrier) is None
    [row] = rows(api, item=carrier)
    assert deferred["sent_as"] == {"delivery": "defer", "until": row["due_at"]} and row["conversation_id"] == tid
    tick(api, 61)
    assert job_of(api, carrier)


def test_a_deadline_or_a_wake_takes_the_right_to_message_the_bot(api):
    # Outside a DM with the bot (its Home), a reader without Write wakes nothing.
    setup(api)
    cara_dm(api)
    r = api.get("/api/v2/threads/home/bot:ops", headers=headers("cara-test"))
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    for way in ({"delivery": "queue"}, {"delivery": "steer"}, {"delivery": "defer", "defer_for": "1h"}):
        r = api.post(f"/api/v2/threads/{tid}/messages", json={"text": "Mine first.", **way}, headers=headers("cara-test"))
        assert r.status_code == 403, r.text
    assert not rows(api, conversation_id=tid)


def test_a_member_of_a_dm_with_the_bot_names_any_way_without_write(api):
    # A plain post in her DM already wakes Ops, so the way her composer remembers does not make it a quiet defer.
    setup(api)
    tid = cara_dm(api)
    queued = say(api, tid, "Mine first.", token="cara-test", delivery="queue")
    assert queued["sent_as"]["delivery"] == "queue" and job_of(api, queued["id"])
    later = say(api, tid, "In an hour.", token="cara-test", delivery="defer", defer_for="1h")
    assert later["sent_as"]["delivery"] == "defer" and [r["due_at"] is not None for r in rows(api, item=later["id"])] == [True]


@pytest.mark.parametrize("body", [
    {"text": "x", "delivery": "steer", "defer_for": "4h"},
    {"text": "x", "delivery": "defer", "defer_for": "0m"},
    {"text": "x", "delivery": "later"},
    {"text": "x", "refs": {"send_way": "defer"}},
    {"text": "x", "refs": {"send_defer_for": "1h"}},
    {"text": "x", "delivery": "queue", "refs": {"delivery": "queue"}},
])
def test_a_thread_delivery_that_cannot_apply_is_refused(api, body):
    setup(api)
    tid, _ = dm(api, "Hello.")
    assert api.post(f"/api/v2/threads/{tid}/messages", json=body, headers=headers()).status_code == 422


def test_a_way_goes_only_where_a_bot_is_across(api):
    made = post(api, "threads/dm", {"members": ["human:ben"], "body": "Budget call at 3"})
    r = api.post(f"/api/v2/threads/{made['id']}/messages", json={"text": "x", "delivery": "queue"}, headers=headers())
    assert r.status_code == 422 and "bot" in r.text, r.text
    r = api.post("/api/v2/threads/dm", json={"members": ["human:ben"], "body": "x", "delivery": "defer"},
                 headers=headers())
    assert r.status_code == 422, r.text


def test_a_legacy_refs_delivery_goes_as_it_did(api):
    """A client's own refs.delivery, with no `delivery`, is kept and read as before: the thread routes' marks are
    their own (send_way), so it neither defers nor is refused."""
    machine = setup(api)
    tid, _ = dm(api, "Morning numbers?")
    attempt = started(api, machine)
    legacy = say(api, tid, "After this turn.", refs={"delivery": "queue"})
    assert "sent_as" not in legacy and legacy["refs"]["delivery"] == "queue" and "send_way" not in legacy["refs"]
    taken = [m["id"] for m in post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])["messages"]]
    assert legacy["id"] not in taken and job_of(api, legacy["id"])[1] == "queued"
    deferred = say(api, tid, "Some day.", refs={"delivery": "defer", "defer_for": "1h"})
    assert "sent_as" not in deferred and job_of(api, deferred["id"]) and not rows(api, item=deferred["id"])


def test_a_dm_members_defer_rides_along_as_a_queue_while_the_bot_runs_there(api):
    machine = setup(api)
    tid = cara_dm(api, machine)
    later = say(api, tid, "When you get to it.", token="cara-test", delivery="defer")
    assert later["sent_as"]["delivery"] == "queue" and job_of(api, later["id"])


def test_a_dm_made_again_rides_along_while_the_bot_runs_there(api):
    machine = setup(api)
    tid, _ = dm(api, "Morning numbers?")
    started(api, machine)
    again, message = dm(api, "And the refunds.", delivery="defer")
    assert again == tid and message["sent_as"] == {"delivery": "queue", "until": None}
    assert job_of(api, message["id"]) and not rows(api, item=message["id"])


def test_a_deferred_carrier_is_checked_again_when_it_is_carried_or_fired(api, monkeypatch):
    """A Home post the bot may not read (a private task it is not on) makes no carrier and nothing deferred; one it
    could read when written but not any more is never written into a turn, nor starts one at its deadline."""
    setup(api)
    tid = home(api)
    task = post(api, "tasks", {"title": "Secret plan", "body": "Do", "owner": "human:ana", "private": True})
    hidden = say(api, tid, "About the secret plan.", refs={"task": task["id"]}, delivery="defer", defer_for="30m")
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM message_deliveries WHERE message_id=? AND carrier_id IS NOT NULL",
                             (hidden["id"],)).fetchone()
    assert hidden["sent_as"] == {"delivery": "defer", "until": None} and not rows(api, conversation_id=tid)

    soon = say(api, tid, "Billing first.", delivery="defer", defer_for="30m")
    later = say(api, tid, "Then the docs.", delivery="defer", defer_for="never")
    carriers = [carrier_of(api, m["id"])[0] for m in (soon, later)]
    assert len(rows(api, conversation_id=tid)) == 2
    real = task_privacy.message_readable
    gone = {soon["id"], later["id"]}
    monkeypatch.setattr(task_privacy, "message_readable", lambda c, a, m, current=False: (
        (m or {}).get("id") not in gone and real(c, a, m, current)))
    tick(api, 31)
    assert fired(api) == [] and all(job_of(api, cid) is None for cid in carriers)
    with api.app.state.store.transaction() as c:
        _, lines, left = delivery.carried(c, "ops", "attempt-x", [tid])
    assert lines == [] and left == 0
    assert {r["how"] for r in rows(api, conversation_id=tid)} == {"moot"}
