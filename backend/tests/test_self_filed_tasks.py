"""A bot that files a task for itself in one of its turns, and a turn Tico woke that changed nothing (Dev 359).

The task's own turn starts once the filing turn has ended, never beside it: a task the filing turn finished by then
starts no turn at all. A turn Tico woke on a task already finished that wrote nothing posts no reply to the person;
its text stays on the run."""
import pytest

from backend import wake_presets as W
from backend.store import H
from backend.tests.test_api import api, claim, get, post  # noqa: F401  (api is a fixture)
from backend.tests.test_run_triggers import finish, machine, start
from backend.tests.test_task_lanes import new_task, task_bot


def busy(api, machine):
    """ops's turn on a task a person filed, started."""
    new_task(api, "plan the week")
    active = claim(api, machine)
    post(api, f"attempts/{active['id']}/started", {"thread_id": active["lane"]}, machine["token"])
    return active


def done(api, task_id, token):
    version = get(api, "tasks/" + task_id, token)["task"]["version"]
    post(api, "tasks/" + task_id, {"version": version, "status": "done"}, token)


def complete(api, machine, attempt, text="Done."):
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": text},
         machine["token"])


def test_a_task_a_bot_files_for_itself_starts_once_the_filing_turn_ends(api):
    machine = task_bot(api, lanes=True)
    active = busy(api, machine)
    mine = post(api, "tasks", {"owner": "ops", "title": "Tidy the roadmap", "body": "Close stale items."},
                token=active["token"])
    assert claim(api, machine) is None
    # A task a person files still starts beside the turn.
    other = new_task(api, "release notes")
    assert claim(api, machine)["lane"] == "task:" + other["id"]
    complete(api, machine, active)
    after = claim(api, machine)
    assert (after["lane"], H.message_task_id(after["message"])) == ("task:" + mine["id"], mine["id"])


@pytest.mark.parametrize("child", [False, True], ids=["release-pass", "release-child"])
def test_self_filed_release_qa_keeps_the_filing_turn_hold(api, child):
    machine = task_bot(api, lanes=True)
    active = busy(api, machine)
    fields = {"title": "Post Release QA pass: v1.2.3"}
    if child:
        typ = post(api, "task-types", {"name": "Release flow", "steps": [
            {"name": "Post Release QA", "status": "review", "flow": {"check": "release_qa"}}]})["type"]
        parent = post(api, "tasks", {"owner": "finance", "title": "Released work", "body": "Verify it.",
                                     "type": typ["id"], "step": "Post Release QA"}, token=active["token"])
        fields = {"title": "Verify shipped behavior", "relations": [{"kind": "parent", "task": parent["id"]}]}
    mine = post(api, "tasks", {"owner": "ops", "body": "Check the release.", **fields}, token=active["token"])
    with api.app.state.store.read() as c:
        notice = c.execute("SELECT refs FROM messages WHERE recipient_id='bot:ops' "
                           "AND json_extract(refs,'$.task')=? ORDER BY rowid DESC", (mine["id"],)).fetchone()
        refs = H._json(notice[0], {})
        assert refs["wake"] == "review_request" and refs["filed_in"] == active["id"]
    assert claim(api, machine) is None
    complete(api, machine, active)
    after = claim(api, machine)
    assert H.message_task_id(after["message"]) == mine["id"]


def test_a_task_the_filing_turn_finished_starts_no_turn(api):
    machine = task_bot(api, lanes=True)
    active = busy(api, machine)
    order = post(api, "tasks", {"owner": "ops", "title": "Spec to top of roadmap when ready",
                                "body": "Standing order: move it when routines land."}, token=active["token"])
    # Claims come between the filing and the finishing: none starts the task beside the turn that filed it.
    assert claim(api, machine) is None
    done(api, order["id"], active["token"])
    complete(api, machine, active)
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM turns WHERE task_id=?", (order["id"],)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM events WHERE action='job.suppress' AND json_extract(detail,"
                         "'$.task_id')=?", (order["id"],)).fetchone()[0] == 1


def replies(api, task):
    with api.app.state.store.read() as c:
        return [b for (b,) in c.execute("SELECT body FROM messages WHERE sender_id='bot:ops' AND recipient_id LIKE "
                                        "'human:%' AND conversation_id=? ORDER BY rowid", (task["conversation_id"],))]


def recheck_after_claim(api, r, task, body="Still held?", refs=None):
    """A task finishes after the automatic notice is claimed but before the process starts. Queued notices on
    already finished tasks are now suppressed for every kind; this race still needs the no-change reply guard."""
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", task["id"], status="review", mover=True)
        # An update request is an event that wakes; a plain task notice would wait as a note.
        H._wake(c, H.task(c, task["id"]), "bot:ops", body, {"wake": "update", **(refs or {})})
    again = claim(api, r)
    assert again is not None, "the task was open when its automatic wake was claimed"
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", task["id"], status="done", mover=True)
    start(api, r, again)
    return again


def test_a_tico_wake_on_a_finished_task_that_changed_nothing_posts_no_reply(api):
    r, task, again = finished_and_woken(api, first_text="Held: it moves when routines land.")
    assert replies(api, task) == ["Held: it moves when routines land."]
    finish(api, r, again, "Nothing changed: still holding.")
    assert replies(api, task) == ["Held: it moves when routines land."]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT final_text FROM attempts WHERE id=?", (again["id"],)).fetchone()[0] \
            == "Nothing changed: still holding."
        assert c.execute("SELECT count(*) FROM events WHERE action='reply.withheld' AND target=?",
                         (again["id"],)).fetchone()[0] == 1
    # A turn that wrote something still reports it.
    third = recheck_after_claim(api, r, task, "Routines landed")
    post(api, f"tasks/{task['id']}/comments", {"text": "Moved to Approved."}, third["token"])
    finish(api, r, third, "Moved it to Approved.")
    assert replies(api, task)[-1] == "Moved it to Approved."


def test_an_answer_on_an_open_task_still_reaches_the_person(api):
    """A turn whose reply is its whole work (a question filed as a task) writes nothing and still answers."""
    r = machine(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Which bank feed is late?", "body": "Just tell me."})
    attempt = claim(api, r)
    start(api, r, attempt)
    finish(api, r, attempt, "The Monzo feed, by two days.")
    assert replies(api, task) == ["The Monzo feed, by two days."]


def finished_and_woken(api, refs=None, first_text="Held."):
    """A completed task and a later recheck claimed before its finishing transition: (runner, task, turn)."""
    r = machine(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Hold the spec", "body": "Hold it until routines land."})
    first = claim(api, r)
    start(api, r, first)
    done(api, task["id"], first["token"])
    finish(api, r, first, first_text)
    again = recheck_after_claim(api, r, task, refs=refs)
    return r, task, again


def test_a_persons_message_folded_into_a_recheck_still_gets_its_answer(api):
    r, task, again = finished_and_woken(api)
    person = post(api, f"tasks/{task['id']}/comments", {"text": "Is the spec still on hold?"})["comment"]
    assert person["id"] in [m["id"] for m in post(api, f"attempts/{again['id']}/inputs", {}, r["token"])["messages"]]
    finish(api, r, again, "Yes, until routines land.")
    assert replies(api, task)[-1] == "Yes, until routines land."


def test_a_decided_approval_or_a_reported_push_still_reports(api):
    r, task, again = finished_and_woken(api, {"approval": "a1"})
    finish(api, r, again, "Approved and sent.")
    assert replies(api, task)[-1] == "Approved and sent."
    third = recheck_after_claim(api, r, task)
    post(api, f"attempts/{third['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Pushed the fix.",
                                                   "pushes": [{"path": "tasks/x/app", "count": 1}]}, r["token"])
    assert replies(api, task)[-1] == "Pushed the fix."


def test_a_task_a_bot_files_for_another_bot_starts_at_once(api):
    machine = task_bot(api, lanes=True, others=("finance",))
    active = busy(api, machine)
    theirs = post(api, "tasks", {"owner": "finance", "title": "Pay the invoice", "body": "Due today."},
                  token=active["token"])
    after = claim(api, machine)
    assert (after["bot"], H.message_task_id(after["message"])) == ("finance", theirs["id"])


def test_the_hold_ends_after_a_few_minutes_even_while_the_filing_turn_runs(api):
    from backend import execution
    machine = task_bot(api, lanes=True)
    active = busy(api, machine)
    mine = post(api, "tasks", {"owner": "ops", "title": "Build the docs", "body": "In parallel."},
                token=active["token"])
    assert claim(api, machine) is None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created_at=? WHERE recipient_id='bot:ops' AND body LIKE 'New task from %Build the docs'",
                  (H.shift(H.now(), seconds=-execution.FILER_HOLD_S - 1),))
    assert H.message_task_id(claim(api, machine)["message"]) == mine["id"]


def test_a_task_the_filing_turn_declined_starts_no_turn(api):
    machine = task_bot(api, lanes=True)
    active = busy(api, machine)
    order = post(api, "tasks", {"owner": "ops", "title": "Maybe tidy", "body": "Only if needed."},
                 token=active["token"])
    assert claim(api, machine) is None
    version = get(api, "tasks/" + order["id"], active["token"])["task"]["version"]
    post(api, "tasks/" + order["id"], {"version": version, "status": "declined"}, active["token"])
    complete(api, machine, active)
    assert claim(api, machine) is None


def test_a_person_who_finishes_the_task_with_a_question_gets_the_answer(api):
    """The bot learns of a person's own act (done, with a note) only from Tico's notice: the notice keeps who acted,
    so the bot's answer is posted although its turn wrote nothing."""
    r = machine(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Send the contract", "body": "For signature."})
    first = claim(api, r)
    start(api, r, first)
    version = get(api, "tasks/" + task["id"], first["token"])["task"]["version"]
    post(api, "tasks/" + task["id"], {"version": version, "status": "review"}, first["token"])
    finish(api, r, first, "Sent for signature.")
    version = get(api, "tasks/" + task["id"])["task"]["version"]
    post(api, "tasks/" + task["id"], {"version": version, "status": "done", "note": "Signed. Which draft went out?"})
    again = claim(api, r)
    assert again["message"]["sender_id"] == "keeper"
    start(api, r, again)
    finish(api, r, again, "Draft 3, the one with the new rates.")
    assert replies(api, task)[-1] == "Draft 3, the one with the new rates."


def test_a_task_the_filing_turn_left_waiting_starts_no_turn(api):
    """A standing order parked by the turn that filed it is woken by what it waits for, not by its first notice."""
    machine = task_bot(api, lanes=True)
    active = busy(api, machine)
    order = post(api, "tasks", {"owner": "ops", "title": "Spec to top when routines land", "body": "Standing order."},
                 token=active["token"])
    version = get(api, "tasks/" + order["id"], active["token"])["task"]["version"]
    post(api, "tasks/" + order["id"], {"version": version, "status": "waiting",
                                        "note": "Waiting on routines Part 2 to land."}, active["token"])
    assert claim(api, machine) is None
    complete(api, machine, active)
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM turns WHERE task_id=?", (order["id"],)).fetchone()[0] == 0
