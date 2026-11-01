"""A snoozed task is off the lists and quiet until its date, then back with one wake (backend/task_snooze.py)."""
import json
from datetime import timedelta

from backend import still_wanted
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401


def ahead(days):
    """A stored timestamp `days` from now."""
    return H.shift(H.now(), days=days)


def listed(api, token="ana-test", query=""):
    return {t["id"] for t in get(api, "tasks?status=open,doing,waiting" + query, token)["tasks"]}


def notices(c, task_id, words):
    return c.execute("SELECT count(*) FROM messages WHERE to_actor='bot:ops' AND json_extract(refs,'$.task')=? "
                     "AND body LIKE ?", (task_id, words + "%")).fetchone()[0]


def jobs(c):
    return c.execute("SELECT count(*) FROM jobs WHERE bot='ops' AND state='queued'").fetchone()[0]


def test_a_snoozed_task_leaves_lists_and_needs_you_for_the_people_on_it_only(api):
    mine = post(api, "tasks", {"owner": "ana", "title": "Renew the domain", "body": "x"})
    bots = post(api, "tasks", {"owner": "ops", "title": "Tidy the runbooks", "body": "x"})
    # Someone neither on the task nor a mover may not set it aside; a role on it is enough.
    post(api, f"tasks/{bots['id']}/snooze", {"until": ahead(30)}, token="ben-test", expected=403)
    post(api, f"tasks/{bots['id']}", {"version": bots["version"], "roles": {"reviewer": ["ben"]}})
    day = ahead(30)[:10]
    snoozed = post(api, f"tasks/{bots['id']}/snooze", {"until": day}, token="ben-test")
    # A bare date is the start of that day in the team's zone.
    assert snoozed["snoozed_until"][:10] == day and snoozed["snoozed_until"][11:13] in ("07", "08")
    assert snoozed["snoozed_by"] == "human:ben"
    post(api, f"tasks/{mine['id']}/snooze", {"until": ahead(-1)}, expected=422)      # the past
    post(api, f"tasks/{mine['id']}/snooze", {"until": ahead(3)})
    assert {mine["id"], bots["id"]}.isdisjoint(listed(api))
    assert {mine["id"], bots["id"]} <= listed(api, query="&snoozed=only") <= listed(api, query="&snoozed=all")
    assert get(api, "tasks/" + bots["id"])["task"]["snoozed_until"]                     # by id, still there
    assert mine["id"] not in {i.get("id") for i in get(api, "needs-you")["items"]}
    # Finishing it ends the snooze: there is nothing left to wait for.
    done = post(api, f"tasks/{mine['id']}", {"version": get(api, "tasks/" + mine["id"])["task"]["version"],
                                              "status": "done"})
    assert done["snoozed_until"] is None


def test_nothing_automatic_wakes_a_snoozed_task_but_a_person_still_reaches_it(api):
    store = api.app.state.store
    with store.transaction() as c:
        row = H.task_create(c, H.human_actor("ana"), "Plan the offsite", "Next quarter.", "bot:ops")
        H.task_update(c, "bot:ops", row["id"], status="doing")
        c.execute("UPDATE jobs SET state='completed' WHERE bot='ops'")
        H.TS.snooze(c, "human:ana", row["id"], ahead(30))
        assert notices(c, row["id"], "Snoozed until") == 1 and jobs(c) == 0             # told, not run
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), days=-9), row["id"]))
        assert row["id"] not in [t["id"] for t in still_wanted.idle_tasks(c)]
        assert H.wake_stalled(c)["woke"] == []
        H.task_update(c, H.human_actor("ana"), row["id"], status="open")
        assert notices(c, row["id"], "Open:") == 1 and jobs(c) == 0, "news about it waits for the bot's next run"
        assert not H.comment_wakes(c, "bot:finance", H.task(c, row["id"]), "Any news?")
        H.task_comment(c, H.human_actor("ana"), row["id"], "Here is the venue list.")
        assert jobs(c) == 1

def test_at_its_date_a_snoozed_task_is_back_and_its_bot_is_woken_once(api):
    store = api.app.state.store
    with store.transaction() as c:
        row = H.task_create(c, H.human_actor("ana"), "Review the contract", "When it renews.", "bot:ops",
                            due=ahead(1))
        c.execute("UPDATE jobs SET state='completed' WHERE bot='ops'")
        H.TS.snooze(c, "human:ana", row["id"], ahead(2))
    assert row["id"] not in listed(api)
    at = H.parse_ts(ahead(2)) + timedelta(seconds=1)
    for later in (at, at + timedelta(minutes=1)):
        Scheduler(store, api.app.state.execution).tick(later)
    assert row["id"] in listed(api)
    with store.read() as c:
        assert H.task(c, row["id"])["snoozed_until"] is None
        assert notices(c, row["id"], "Snooze over") == 1 and jobs(c) == 1
        assert notices(c, row["id"], "Due:") == 0, "the due date that passed in the snooze rides on that wake"


def test_unsnoozing_early_brings_it_back_now_and_wakes_its_bot(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Update the price page", "body": "x"})
    post(api, f"tasks/{task['id']}/snooze", {"until": ahead(30)})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot='ops'")
    back = post(api, f"tasks/{task['id']}/unsnooze", {})
    assert back["snoozed_until"] is None and task["id"] in listed(api)
    with api.app.state.store.read() as c:
        assert notices(c, task["id"], "Unsnoozed") == 1 and jobs(c) == 1


def test_the_requester_is_told_and_review_hands_it_back_awake(api):
    store = api.app.state.store
    with store.transaction() as c:
        row = H.task_create(c, "bot:finance", "Reconcile the card", "Month end.", "bot:ops")
        H.task_update(c, "bot:ops", row["id"], status="doing")
        c.execute("UPDATE jobs SET state='completed'")
        H.TS.snooze(c, "bot:ops", row["id"], ahead(30))
        told = c.execute("SELECT refs FROM messages WHERE to_actor='bot:finance' AND body LIKE 'Snoozed until%'"
                         " AND json_extract(refs,'$.task')=?", (row["id"],)).fetchall()
        assert len(told) == 1 and c.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0] == 0
        # Only its owner's news waits: a notice to the requester bot about it wakes that bot.
        H._wake(c, H.task(c, row["id"]), "bot:finance", "Question for you: which card?", {"wake": "update"})
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='finance' AND state='queued'").fetchone()[0] == 1
        c.execute("UPDATE jobs SET state='completed'")
        # Handed back for review, the requester bot is woken now and the snooze is over.
        H.task_update(c, "bot:ops", row["id"], status="review")
        assert H.task(c, row["id"])["snoozed_until"] is None
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='finance' AND state='queued'").fetchone()[0] == 1


def snooze_wakes(c, task_id, prefix):
    return c.execute("SELECT body, refs FROM messages WHERE to_actor='bot:ops' AND body LIKE ? "
                     "AND json_extract(refs,'$.task')=?", (prefix + "%", task_id)).fetchall()


def test_a_bot_snoozes_its_own_task_with_a_note_and_the_note_comes_back_when_it_ends(api):
    store = api.app.state.store
    with store.transaction() as c:
        row = H.task_create(c, H.human_actor("ana"), "Chase the lease signature", "Waiting on the landlord.", "bot:ops")
        H.task_update(c, "bot:ops", row["id"], status="doing")
        c.execute("UPDATE jobs SET state='completed' WHERE bot='ops'")
        # The owner bot sets its own task aside: the note says what to check when it wakes.
        H.TS.snooze(c, "bot:ops", row["id"], ahead(2), note="Check whether the landlord replied by mail")
        assert H.task(c, row["id"])["snoozed_note"] == "Check whether the landlord replied by mail"
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='ops' AND state='queued'").fetchone()[0] == 0
    at = H.parse_ts(ahead(2)) + timedelta(seconds=1)
    Scheduler(store, api.app.state.execution).tick(at)
    with store.read() as c:
        woken = snooze_wakes(c, row["id"], "Snooze over")
        assert len(woken) == 1
        body, refs = woken[0][0], json.loads(woken[0][1])
        assert body.endswith("\nYour note: Check whether the landlord replied by mail")
        assert refs["note"] == "Check whether the landlord replied by mail" and refs["wake"] == "snooze"
        assert H.task(c, row["id"])["snoozed_note"] is None


def test_a_note_is_cleared_by_unsnoozing_and_by_finishing_and_refused_when_long(api):
    store = api.app.state.store
    task = post(api, "tasks", {"owner": "ops", "title": "Renew the certificate", "body": "x"})
    post(api, f"tasks/{task['id']}/snooze", {"until": ahead(30), "note": "x" * 501}, expected=422)
    shown = post(api, f"tasks/{task['id']}/snooze", {"until": ahead(30), "note": "  Ask the vendor  "})
    assert shown["snoozed_note"] == "Ask the vendor"
    back = post(api, f"tasks/{task['id']}/unsnooze", {})
    assert back.get("snoozed_note") is None      # a cleared note is left out of the view
    with store.read() as c:
        woken = snooze_wakes(c, task["id"], "Unsnoozed")
        assert len(woken) == 1 and woken[0][0].endswith("\nYour note: Ask the vendor")
    post(api, f"tasks/{task['id']}/snooze", {"until": ahead(30), "note": "Check the vendor"})
    done = post(api, f"tasks/{task['id']}", {"version": get(api, "tasks/" + task["id"])["task"]["version"],
                                              "status": "done"})
    assert done["snoozed_until"] is None and done.get("snoozed_note") is None


def snooze_comments(c, task_id):
    """The notes the keeper left on the task when a snooze ended without a wake."""
    return [r[0] for r in c.execute("SELECT body FROM messages WHERE json_extract(refs,'$.task')=? "
                                    "AND body LIKE 'Snooze over. Note:%' ORDER BY created_at", (task_id,))]


def test_a_snooze_that_changes_bumps_the_version_and_the_same_one_again_does_not(api):
    task = post(api, "tasks", {"owner": "ana", "title": "Sign the lease", "body": "x"})
    day = ahead(20)[:10]
    first = post(api, f"tasks/{task['id']}/snooze", {"until": day, "note": "Ask the landlord"})
    assert first["version"] == task["version"] + 1
    moved = post(api, f"tasks/{task['id']}/snooze", {"until": day, "note": "Ask the landlord by mail"})
    assert moved["version"] == first["version"] + 1 and moved["snoozed_note"] == "Ask the landlord by mail"
    same = post(api, f"tasks/{task['id']}/snooze", {"until": day, "note": "Ask the landlord by mail"})
    assert same["version"] == moved["version"], "the same date and note again change nothing"
    back = post(api, f"tasks/{task['id']}/unsnooze", {})
    assert back["version"] == same["version"] + 1, "ending the snooze is a change too"


def test_a_note_for_a_person_or_a_paused_bot_is_left_on_the_task_when_the_snooze_ends(api):
    store = api.app.state.store
    with store.transaction() as c:
        person = H.task_create(c, H.human_actor("ana"), "Sign the lease", "x", "human:ben")
        paused = H.task_create(c, H.human_actor("ana"), "File the receipts", "x", "bot:ops")
        active = H.task_create(c, H.human_actor("ana"), "Book the venue", "x", "bot:finance")
        for row in (person, paused, active):
            H.TS.snooze(c, "human:ana", row["id"], ahead(2), note="Check the reply")
        c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
        c.execute("UPDATE jobs SET state='completed'")
    at = H.parse_ts(ahead(2)) + timedelta(seconds=1)
    Scheduler(store, api.app.state.execution).tick(at)
    with store.read() as c:
        assert snooze_comments(c, person["id"]) == ["Snooze over. Note: Check the reply"]
        assert snooze_comments(c, paused["id"]) == ["Snooze over. Note: Check the reply"]
        assert snooze_comments(c, active["id"]) == [], "an active bot is woken with the note, not told twice"
        woken = c.execute("SELECT body FROM messages WHERE to_actor='bot:finance' AND body LIKE 'Snooze over%' "
                          "AND json_extract(refs,'$.task')=?", (active["id"],)).fetchall()
        assert len(woken) == 1 and woken[0][0].endswith("\nYour note: Check the reply")
        assert snooze_wakes(c, paused["id"], "Snooze over:") == [], "no wake for a paused bot"
        assert H.task(c, person["id"])["snoozed_note"] is None
