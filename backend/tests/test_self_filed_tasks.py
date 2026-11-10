"""A bot that files a task for itself in one of its turns, and a turn Tico woke on a task finished meanwhile (Dev 359).

The task's own turn starts once the filing turn has ended, never beside it: a task the filing turn finished by then
starts no turn at all. A turn Tico woke on a task that was finished after the claim never starts (task_wakes.may_start),
so it posts nothing to the person."""

from backend import task_engine as E
from backend.store import H
from backend.tests.helpers.api import claim, get, post  # noqa: F401  (api is a fixture)
from backend.tests.helpers.run_triggers import finish, machine, start
from backend.tests.helpers.task_lanes import new_task, task_bot


def busy(api, machine):
    """ops's turn on a task a person filed, started."""
    new_task(api, "plan the week")
    active = claim(api, machine)
    post(api, f"attempts/{active['id']}/started", {"thread_id": active["lane"]}, machine["token"])
    return active


def done(api, task_id, token):
    """`token` says the stage's work is finished: in General's one stage, the task is Done."""
    version = get(api, "tasks/" + task_id, token)["task"]["version"]
    post(api, f"tasks/{task_id}/done", {"version": version}, token)


def complete(api, machine, attempt, text="Done."):
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": text},
        machine["token"],
    )


def test_a_task_a_bot_files_for_itself_starts_once_the_filing_turn_ends(api):
    machine = task_bot(api, lanes=True)
    active = busy(api, machine)
    mine = post(
        api, "tasks", {"owner": "ops", "title": "Tidy the roadmap", "body": "Close stale items."}, token=active["token"]
    )
    assert claim(api, machine) is None
    # A task a person files still starts beside the turn.
    other = new_task(api, "release notes")
    assert claim(api, machine)["lane"] == "task:" + other["id"]
    complete(api, machine, active)
    after = claim(api, machine)
    assert (after["lane"], H.message_task_id(after["message"])) == ("task:" + mine["id"], mine["id"])


def test_a_task_the_filing_turn_finished_starts_no_turn(api):
    machine = task_bot(api, lanes=True)
    active = busy(api, machine)
    order = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": "Spec to top of roadmap when ready",
            "body": "Standing order: move it when routines land.",
        },
        token=active["token"],
    )
    # Claims come between the filing and the finishing: none starts the task beside the turn that filed it.
    assert claim(api, machine) is None
    done(api, order["id"], active["token"])
    complete(api, machine, active)
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE task_id=?", (order["id"],)).fetchone()[0] == 0
        # Its wake's run was held at the claim and its job canceled (task_wakes.may_start).
        states = c.execute(
            "SELECT j.state FROM jobs j JOIN messages m ON m.id=j.message_id WHERE m.wake_key LIKE ?",
            (order["id"] + ":%",),
        ).fetchall()
        assert [s[0] for s in states] == ["canceled"]


def replies(api, task):
    with api.app.state.store.read() as c:
        return [
            b
            for (b,) in c.execute(
                "SELECT body FROM messages WHERE sender_id='bot:ops' AND recipient_id LIKE "
                "'human:%' AND conversation_id=? ORDER BY rowid",
                (task["conversation_id"],),
            )
        ]


def recheck_after_claim(api, r, task, body="Still held?"):
    """A task finishes after Tico's automatic wake is claimed but before the process starts: the claimed turn."""
    with api.app.state.store.transaction() as c:
        E.move(c, task["id"], E.REOPEN, "human:ana", "Look again")
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")  # the reopen's own turn is not this one
        # An update request is an event that wakes; a plain task notice would wait as a note.
        H._wake(c, H.task(c, task["id"]), "bot:ops", body, {"wake": "update"})
    again = claim(api, r)
    assert again is not None, "the task was open when its automatic wake was claimed"
    with api.app.state.store.transaction() as c:
        E.move(c, task["id"], E.DONE, "bot:ops", "Finished", seen_version=E.task(c, task["id"])["version"])
    return again


def test_a_tico_wake_on_a_task_finished_after_its_claim_never_starts_and_posts_nothing(api):
    r, task, again = finished_and_woken(api, first_text="Held: it moves when routines land.")
    assert replies(api, task) == ["Held: it moves when routines land."]
    refused = post(api, f"attempts/{again['id']}/started", {"thread_id": "fixture-thread"}, r["token"], expected=409)
    assert refused["error"]["code"] == "start_wait"
    assert post(api, f"attempts/{again['id']}/renew", {}, r["token"])["stop"] is True
    post(api, f"attempts/{again['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, r["token"])
    assert replies(api, task) == ["Held: it moves when routines land."]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT started_at FROM attempts WHERE id=?", (again["id"],)).fetchone()[0] is None


def test_an_answer_on_an_open_task_still_reaches_the_person(api):
    """A turn whose reply is its whole work (a question filed as a task) writes nothing and still answers."""
    r = machine(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Which bank feed is late?", "body": "Just tell me."})
    attempt = claim(api, r)
    start(api, r, attempt)
    finish(api, r, attempt, "The Monzo feed, by two days.")
    assert replies(api, task) == ["The Monzo feed, by two days."]


def finished_and_woken(api, first_text="Held."):
    """A completed task and a later recheck claimed before its finishing transition: (runner, task, turn)."""
    r = machine(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Hold the spec", "body": "Hold it until routines land."})
    first = claim(api, r)
    start(api, r, first)
    done(api, task["id"], first["token"])
    finish(api, r, first, first_text)
    again = recheck_after_claim(api, r, task)
    return r, task, again


def test_a_task_a_bot_files_for_another_bot_starts_at_once(api):
    machine = task_bot(api, lanes=True, others=("finance",))
    active = busy(api, machine)
    theirs = post(
        api, "tasks", {"owner": "finance", "title": "Pay the invoice", "body": "Due today."}, token=active["token"]
    )
    after = claim(api, machine)
    assert (after["bot"], H.message_task_id(after["message"])) == ("finance", theirs["id"])


def test_the_hold_ends_after_a_few_minutes_even_while_the_filing_turn_runs(api):
    from backend import execution

    machine = task_bot(api, lanes=True)
    active = busy(api, machine)
    mine = post(
        api, "tasks", {"owner": "ops", "title": "Build the docs", "body": "In parallel."}, token=active["token"]
    )
    assert claim(api, machine) is None
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE messages SET created_at=? WHERE recipient_id='bot:ops' AND wake_key LIKE ?",
            (H.shift(H.now(), seconds=-execution.FILER_HOLD_S - 1), mine["id"] + ":%"),
        )
    assert H.message_task_id(claim(api, machine)["message"]) == mine["id"]


def test_a_task_the_filing_turn_closed_starts_no_turn(api):
    machine = task_bot(api, lanes=True)
    active = busy(api, machine)
    order = post(
        api, "tasks", {"owner": "ops", "title": "Maybe tidy", "body": "Only if needed."}, token=active["token"]
    )
    assert claim(api, machine) is None
    version = get(api, "tasks/" + order["id"], active["token"])["task"]["version"]
    post(
        api,
        f"tasks/{order['id']}/move",
        {"to": "closed", "version": version, "why": "Not needed after all."},
        active["token"],
    )
    complete(api, machine, active)
    assert claim(api, machine) is None


def test_a_task_the_filing_turn_snoozed_starts_no_turn(api):
    """A standing order the turn that filed it snoozed is woken when its snooze ends, not by its first notice."""
    machine = task_bot(api, lanes=True)
    active = busy(api, machine)
    order = post(
        api,
        "tasks",
        {"owner": "ops", "title": "Spec to top when routines land", "body": "Standing order."},
        token=active["token"],
    )
    version = get(api, "tasks/" + order["id"], active["token"])["task"]["version"]
    post(
        api,
        f"tasks/{order['id']}/snooze",
        {"version": version, "until": H.shift(H.now(), days=7), "why": "Waiting on routines Part 2 to land."},
        active["token"],
    )
    assert claim(api, machine) is None
    complete(api, machine, active)
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE task_id=?", (order["id"],)).fetchone()[0] == 0
