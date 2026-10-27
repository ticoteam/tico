"""A waiting task's Blocked reason names what really holds it, and follows every change to its holds; a question to a
person lives and dies with its own task, and its asker hears when it is withdrawn (backend/task_holds.py, Dev 627)."""
from backend.tests.test_api import api, post  # noqa: F401
from backend.store import H
from backend import approval_expiry, still_needed, task_holds


def bot_task(c, title, owner="bot:ops", parent=None, actor="human:ben"):
    return H.task_create(c, actor, title, "x", owner, parent_id=parent)["id"]


def ask(c, tid, text, asker="bot:ops", to="human:ana"):
    return H.say(c, asker, to, text, kind="ask", conversation_id=H.task(c, tid)["conversation_id"], refs={"task": tid})


def wait(c, tid, owner="bot:ops", note=None):
    H.task_update(c, owner, tid, status="waiting", note=note)
    return H.task(c, tid)


def notices(c, to, news="ask_withdrawn"):
    return [m for m in H._rows(c.execute("SELECT body, refs_json FROM messages WHERE to_actor=? ORDER BY created", (to,)))
            if (H._json(m["refs_json"], {}) or {}).get("news") == news]


def withdrawn_label(c, ask_id):
    return (H._json(H.message(c, ask_id)["refs_json"], {}) or {}).get("withdrawn")


def test_a_question_on_an_open_subtask_outlives_its_parent_ending(api):
    """Criterion 2: the bot asks on a subtask, then marks the parent done; the question stays."""
    with api.app.state.store.transaction() as c:
        parent = bot_task(c, "Spec Settings search")
        child = bot_task(c, "Ask Ana about search scope", parent=parent)
        question = ask(c, child, "Search titles only, or bodies too?")
        wait(c, child)
        H.task_update(c, "bot:ops", parent, status="done", note="Spec written")
        assert not H.message(c, question["id"])["superseded_at"]
        row = H.task(c, child)
        assert row["status"] == "waiting"
        ana = task_holds.name(c, "human:ana")
        assert row["blocked_reason"] == f"Waiting on {ana}: Search titles only, or bodies too?"
        assert approval_expiry.sweep(c)["asks"] == []
        assert not H.message(c, question["id"])["superseded_at"]
        assert child in [t["id"] for t in H.tasks_asked_of(c, "human:ana")]


def test_ending_the_question_s_own_task_tells_the_asker_whether_the_person_saw_it(api):
    """Criterion 3: someone other than the asker ends the task; one notice, labelled before/after the person saw it."""
    with api.app.state.store.transaction() as c:
        ana = task_holds.name(c, "human:ana")
        unseen = bot_task(c, "Pick the hosting page date")
        first = ask(c, unseen, "Ship Staff Hosting on Friday?")
        wait(c, unseen)
        H.task_close(c, "human:ben", unseen, "Board cleanup")
        assert H.message(c, first["id"])["superseded_at"]
        assert withdrawn_label(c, first["id"]) == f"Withdrawn before {ana} saw it: its task was closed"
        told = notices(c, "bot:ops")
        assert len(told) == 1 and "Ship Staff Hosting on Friday?" in told[0]["body"]
        assert f"Withdrawn before {ana} saw it: its task was closed" in told[0]["body"]

        seen = bot_task(c, "Pick the launch date")
        second = ask(c, seen, "Launch on Monday?")
        H.mark_read(c, "human:ana", second["id"])
        H.task_close(c, "human:ben", seen, "Not needed")
        assert withdrawn_label(c, second["id"]) == f"Withdrawn after {ana} saw it: its task was closed"
        assert len(notices(c, "bot:ops")) == 2

        # The asker ending its own task needs no notice of what it did.
        own = bot_task(c, "Pick a venue")
        third = ask(c, own, "Indoor or outdoor?")
        H.task_update(c, "bot:ops", own, status="done", note="Picked indoor")
        assert H.message(c, third["id"])["superseded_at"]
        assert len(notices(c, "bot:ops")) == 2


def test_still_needed_returns_the_question_and_reopens_its_bot_task_without_a_second_notice(api):
    with api.app.state.store.transaction() as c:
        ana = task_holds.name(c, "human:ana")
        tid = bot_task(c, "Order the mail key")
        question = ask(c, tid, "Which mailbox gets the key?")
        wait(c, tid)
        still_needed.send_back(c, "bot:ops", [(H.message(c, question["id"]), H.task(c, tid))])
        assert withdrawn_label(c, question["id"]).startswith(f"Withdrawn before {ana} saw it: no answer in two")
        row = H.task(c, tid)
        assert row["status"] == "open" and "nothing left to wait on" in row["note"]
        assert notices(c, "bot:ops") == []      # still needed? sent its own one notice


def test_the_last_child_finishing_reopens_a_bot_task_and_one_of_two_keeps_it_waiting_on_the_other(api):
    """Criteria 4 and 5."""
    with api.app.state.store.transaction() as c:
        tid = bot_task(c, "Release the pricing page")
        first = bot_task(c, "Write the copy", owner="bot:cpo", parent=tid, actor="bot:ops")
        second = bot_task(c, "Draw the chart", owner="bot:finance", parent=tid, actor="bot:ops")
        assert wait(c, tid)["blocked_reason"] == "Waiting on cpo: Write the copy (and 1 more)"
        H.task_update(c, "bot:cpo", first, status="done", note="Copy written")
        row = H.task(c, tid)
        assert row["status"] == "waiting" and row["blocked_reason"] == "Waiting on finance: Draw the chart"
        H.task_update(c, "bot:finance", second, status="done", note="Chart drawn")
        row = H.task(c, tid)
        assert row["status"] == "open" and row["blocked_reason"] is None
        assert row["note"] == "Draw the chart is done; nothing left to wait on"


def test_an_answer_or_a_decided_approval_clears_its_hold(api):
    """Criteria 6 and 7."""
    with api.app.state.store.transaction() as c:
        ana = task_holds.name(c, "human:ana")
        tid = bot_task(c, "Choose a vendor")
        question = ask(c, tid, "Vendor A or B?")
        approval = H.approval_request(c, "bot:ops", "spend", {"amount": 120, "account": "card", "what": "Vendor A"}, task_id=tid)
        row = wait(c, tid)
        assert row["blocked_reason"] == f"Waiting on {ana}: Vendor A or B? (and 1 more)"
        H.answer(c, "human:ana", question["id"], "A")
        row = H.task(c, tid)
        assert row["status"] == "waiting" and not row["blocked_reason"].startswith(f"Waiting on {ana}: Vendor")
        assert "spend approval" in row["blocked_reason"]
        H.approval_decide(c, "human:ana", approval["id"], "approved")
        row = H.task(c, tid)
        assert row["status"] == "open" and row["note"] == "Your spend approval was approved; nothing left to wait on"


def test_a_note_only_update_keeps_the_tracked_reason_and_never_names_an_untracked_person(api):
    """Criteria 8 and 9."""
    with api.app.state.store.transaction() as c:
        ana = task_holds.name(c, "human:ana")
        held = bot_task(c, "Confirm the budget")
        ask(c, held, "Is 5k fine?")
        wait(c, held)
        H.task_update(c, "bot:ops", held, note="waiting on Engineering")
        row = H.task(c, held)
        assert row["blocked_reason"] == f"Waiting on {ana}: Is 5k fine?" and row["note"] == "waiting on Engineering"

        loose = bot_task(c, "Wait for the deploy")
        row = wait(c, loose, note="Waiting on Chris Hawkins to deploy")
        assert row["blocked_reason"] == task_holds.NOTHING
        H.task_update(c, "bot:ops", loose, note="Still waiting on Chris Hawkins")
        assert H.task(c, loose)["blocked_reason"] == task_holds.NOTHING
        # The stranded clock runs from when the wait on nothing began, not from the note.
        old = H.shift(H.now(), hours=-3)
        c.execute("UPDATE tasks SET blocked_since=? WHERE id=?", (old, loose))
        assert (loose, "open") in H.sweep_stranded(c)
        assert held not in [t for t, _ in H.sweep_stranded(c, H.shift(H.now(), hours=3))]


def test_a_person_s_task_stays_put_and_an_owner_s_own_hold_is_kept(api):
    """Criteria 10 and 11."""
    with api.app.state.store.transaction() as c:
        mine = bot_task(c, "Plan the offsite", owner="human:ana", actor="human:ana")
        child = bot_task(c, "Book the venue", parent=mine, actor="human:ana")
        wait(c, mine, owner="human:ana")
        H.task_update(c, "bot:ops", child, status="done", note="Booked")
        row = H.task(c, mine)
        assert row["status"] == "waiting"
        assert row["blocked_reason"] == "Nothing left to wait on: Book the venue is done"
        assert row["holds_cleared_at"]

        manual = bot_task(c, "Hold the newsletter")
        sub = bot_task(c, "Proofread it", owner="bot:cpo", parent=manual, actor="bot:ops")
        wait(c, manual)
        c.execute("UPDATE tasks SET blocked_kind='manual', blocked_reason='Held until the legal review' WHERE id=?",
                  (manual,))
        H.task_update(c, "bot:cpo", sub, status="done", note="Proofread")
        row = H.task(c, manual)
        assert row["status"] == "waiting" and row["blocked_reason"] == "Held until the legal review"


def test_backfill_puts_every_waiting_task_right_once_with_one_wake_per_bot(api):
    """Criterion 12."""
    with api.app.state.store.transaction() as c:
        ana = task_holds.name(c, "human:ana")
        stale = [bot_task(c, f"Stale wait {n}") for n in range(3)]
        for tid in stale:
            wait(c, tid)
        c.execute(f"UPDATE tasks SET blocked_reason='Waiting on Chris Hawkins', blocked_kind='dependency' "
                  f"WHERE id IN ({','.join('?' * len(stale))})", stale)
        held = bot_task(c, "Really waiting")
        ask(c, held, "Which plan?")
        wait(c, held)
        c.execute("UPDATE tasks SET blocked_reason='Waiting on Chris Hawkins' WHERE id=?", (held,))
        person = bot_task(c, "Ana's own wait", owner="human:ana", actor="human:ana")
        wait(c, person, owner="human:ana")
        c.execute("UPDATE tasks SET blocked_reason='Waiting on Chris Hawkins' WHERE id=?", (person,))
        before = len(notices(c, "bot:ops", news="assignment"))
        result = task_holds.backfill(c)
        assert result == {"fixed": 2, "opened": 3, "woken": 1}
        assert all(H.task(c, tid)["status"] == "open" for tid in stale)
        assert H.task(c, held)["blocked_reason"] == f"Waiting on {ana}: Which plan?"
        assert H.task(c, person)["blocked_reason"] == task_holds.NOTHING
        assert len(notices(c, "bot:ops", news="assignment")) == before + 1
        assert task_holds.backfill(c) is None
        waiting = H._rows(c.execute("SELECT blocked_reason FROM tasks WHERE status='waiting'"))
        assert not [r for r in waiting if "Chris Hawkins" in (r["blocked_reason"] or "")]


def test_the_third_withdrawal_of_the_same_question_in_a_day_says_so(api):
    """Criterion 13."""
    with api.app.state.store.transaction() as c:
        for n in range(3):
            tid = bot_task(c, "Staff Hosting timing") if n == 0 else tid
            question = ask(c, tid, "When does Staff Hosting ship?")
            wait(c, tid)
            c.execute("UPDATE messages SET superseded_at=? WHERE id=?", (H.now(), question["id"]))
            task_holds.withdrawn(c, tid, [question["id"]], "no answer in two business days")
        told = notices(c, "bot:ops")
        assert len(told) == 3
        assert "third question from you" in told[2]["body"] and "third" not in told[1]["body"]
