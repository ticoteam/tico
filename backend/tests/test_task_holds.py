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
    return [m for m in H._rows(c.execute("SELECT body, refs FROM messages WHERE to_actor=? ORDER BY created_at", (to,)))
            if (H._json(m["refs"], {}) or {}).get("news") == news]


def withdrawn_label(c, ask_id):
    return (H._json(H.message(c, ask_id)["refs"], {}) or {}).get("withdrawn")


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
        tid = bot_task(c, "Staff Hosting timing")
        for _ in range(3):
            question = ask(c, tid, "When does Staff Hosting ship?")
            wait(c, tid)
            c.execute("UPDATE messages SET superseded_at=? WHERE id=?", (H.now(), question["id"]))
            task_holds.withdrawn(c, tid, [question["id"]], "no answer in two business days")
        told = notices(c, "bot:ops")
        assert len(told) == 3
        assert "third question from you" in told[2]["body"] and "third" not in told[1]["body"]


def test_a_private_child_is_never_named_in_its_parent_s_reason(api):
    """The reason is shown to everyone who reads the parent; the private child's title stays off it."""
    with api.app.state.store.transaction() as c:
        mine = bot_task(c, "Plan the audit", owner="human:ana", actor="human:ana")
        secret = H.task_create(c, "human:ana", "Vendor breach review", "x", "bot:ops", parent_id=mine,
                               private=True, lint=False)["id"]
        assert wait(c, mine, owner="human:ana")["blocked_reason"] == "Waiting on a private task"
        H.task_update(c, "bot:ops", secret, status="done", note="Reviewed")
        assert H.task(c, mine)["blocked_reason"] == "Nothing left to wait on: A private task is done"


def test_removing_the_last_blocker_reopens_a_bot_task_and_words_a_person_s(api):
    from backend import task_relations as TR
    with api.app.state.store.transaction() as c:
        for owner, actor, what in (("bot:ops", "human:ben", "form"), ("human:ana", "human:ana", "menu")):
            tid = bot_task(c, f"Ship the {what}", owner=owner, actor=actor)
            blocker = bot_task(c, f"Pick the {what} vendor", owner="bot:cpo")
            TR.add_blocker(c, H.KEEPER, tid, blocker)
            row = wait(c, tid, owner=owner)
            assert row["status"] == "waiting" and row["blocked_reason"] == f"Waiting on cpo: Pick the {what} vendor"
            TR.remove_blocker(c, H.KEEPER, tid, blocker)
            row = H.task(c, tid)
            if owner == "bot:ops":
                assert row["status"] == "open"
                assert row["note"] == "Pick the form vendor no longer blocks it; nothing left to wait on"
            else:
                assert row["status"] == "waiting"
                assert row["blocked_reason"] == "Nothing left to wait on: Pick the menu vendor no longer blocks it"


def test_moving_a_child_in_or_out_recomputes_both_parents(api):
    from backend import task_relations as TR
    with api.app.state.store.transaction() as c:
        mine = bot_task(c, "Plan the party", owner="human:ana", actor="human:ana")
        child = bot_task(c, "Book the band", parent=mine, actor="human:ana")
        assert wait(c, mine, owner="human:ana")["blocked_reason"] == "Waiting on ops: Book the band"
        TR.set_parent(c, "human:ana", child, None)
        assert H.task(c, mine)["blocked_reason"] == "Nothing left to wait on: Book the band moved away"

        bots = bot_task(c, "Plan the dinner", actor="human:ana")
        assert wait(c, bots)["blocked_reason"] == task_holds.NOTHING
        TR.set_parent(c, "human:ana", child, bots)
        row = H.task(c, bots)
        assert row["status"] == "waiting" and row["blocked_reason"] == "Waiting on ops: Book the band"
        TR.set_parent(c, "human:ana", child, None)
        row = H.task(c, bots)
        assert row["status"] == "open" and row["note"] == "Book the band moved away; nothing left to wait on"

        # A subtask filed under a waiting parent is named at once.
        wait(c, mine, owner="human:ana")
        bot_task(c, "Order the cake", owner="bot:cpo", parent=mine, actor="human:ana")
        assert H.task(c, mine)["blocked_reason"] == "Waiting on cpo: Order the cake"


def test_deleting_the_only_child_clears_the_parent_s_hold(api):
    from backend.task_delete import delete_tasks
    with api.app.state.store.transaction() as c:
        mine = bot_task(c, "Plan the trip", owner="human:ana", actor="human:ana")
        # A person's subtask: a bot's would carry its queued wake, and a task carrying work is not deleted.
        child = bot_task(c, "Book the train", owner="human:ben", parent=mine, actor="human:ana")
        wait(c, mine, owner="human:ana")
        report = delete_tasks(c, [child], apply=True, actor="human:ana")
        assert report["applied"], report["refused"]
        assert H.task(c, mine)["blocked_reason"] == "Nothing left to wait on: Book the train was deleted"


def test_a_note_only_edit_keeps_a_recorded_person_wait(api):
    """Criterion 8 on the recorded wait itself, not only the question: a note alone never erases `waiting_on`."""
    with api.app.state.store.transaction() as c:
        ana = task_holds.name(c, "human:ana")
        # Recorded with its question, as a bot's wait on a person is today.
        asked = bot_task(c, "Pick the venue")
        ask(c, asked, "Hall A or Hall B?")
        H.task_update(c, "bot:ops", asked, status="waiting", waiting_on="human:ana", note="Ana, Hall A or Hall B?")
        assert H.task(c, asked)["waiting_on"] == "human:ana"
        H.task_update(c, "bot:ops", asked, note="waiting on Engineering")
        row = H.task(c, asked)
        assert row["waiting_on"] == "human:ana" and row["note"] == "waiting on Engineering"
        assert row["blocked_reason"] == f"Waiting on {ana}: Hall A or Hall B?"
        # Recorded with no question behind it (a wait from before questions were filed for it): only the field holds.
        bare = bot_task(c, "Sign the lease")
        wait(c, bare)
        c.execute("UPDATE tasks SET waiting_on='human:ana' WHERE id=?", (bare,))
        task_holds.refresh(c, bare)
        assert H.task(c, bare)["blocked_reason"] == f"Waiting on {ana}" and not H.open_task_asks(c, H.task(c, bare))
        H.task_update(c, "bot:ops", bare, note="waiting on Engineering")
        row = H.task(c, bare)
        assert row["waiting_on"] == "human:ana" and row["status"] == "waiting"
        assert row["blocked_reason"] == f"Waiting on {ana}" and row["note"] == "waiting on Engineering"
        assert bare in [t["id"] for t in H.needs_you(c, "human:ana")["waiting"]]


def test_a_child_reopened_or_renamed_is_named_again_by_its_waiting_parent(api):
    with api.app.state.store.transaction() as c:
        mine = bot_task(c, "Plan the launch", owner="human:ana", actor="human:ana")
        child = bot_task(c, "Write the post", parent=mine, actor="human:ana")
        wait(c, mine, owner="human:ana")
        H.task_update(c, "bot:ops", child, status="done", note="Written")
        assert H.task(c, mine)["blocked_reason"] == "Nothing left to wait on: Write the post is done"
        H.task_update(c, "bot:ops", child, status="open", note="Needs a second draft")
        assert H.task(c, mine)["blocked_reason"] == "Waiting on ops: Write the post"
        H.task_update(c, "bot:ops", child, title="Write the launch post")
        assert H.task(c, mine)["blocked_reason"] == "Waiting on ops: Write the launch post"


def test_a_question_from_a_private_source_is_not_quoted_in_a_company_task_s_reason(api):
    """The reason is shown to every reader of the task; a question tied to it from a stricter source is only 'a
    question' there. One asked in the task's own room is still quoted."""
    with api.app.state.store.transaction() as c:
        ana = task_holds.name(c, "human:ana")
        tid = bot_task(c, "Renew the vendor contract")
        secret = H.task_create(c, "human:ana", "Vendor breach review", "x", "bot:ops", private=True, lint=False)
        room = H.task(c, tid)["conversation_id"]
        # Asked in the private room, tied to the company task.
        H.say(c, "bot:ops", "human:ana", "Was the breach in the March export?", kind="ask",
              conversation_id=secret["conversation_id"], refs={"task": tid})
        assert wait(c, tid)["blocked_reason"] == f"Waiting on {ana}: a question"
        # Asked in the company task's room while it waits, but built on a private message.
        source = H.say(c, "bot:ops", "human:ana", "Breach notes: the March export leaked.",
                       conversation_id=secret["conversation_id"], refs={"task": secret["id"]})
        H.say(c, "bot:ops", "human:ana", "Should the breach go in the renewal terms?", kind="ask",
              conversation_id=room, refs={"task": tid, "inputs": [source["id"]]})
        assert "breach" not in H.task(c, tid)["blocked_reason"].lower()
    text = api.get("/api/v2/tasks/" + tid, headers={"Authorization": "Bearer ben-test"}).text
    assert "Renew the vendor contract" in text and "breach" not in text.lower()
    with api.app.state.store.transaction() as c:
        plain = bot_task(c, "Renew the cleaning contract")
        ask(c, plain, "Keep the same cleaner?")
        assert wait(c, plain)["blocked_reason"] == f"Waiting on {ana}: Keep the same cleaner?"


def test_structured_answers_end_the_wait_but_an_unmatched_note_keeps_it(api):
    """A direct exact label or card pick clears the wait; an unmatched task note preserves it."""
    def asked(c, title):
        tid = bot_task(c, title)
        q = H.task_comment(c, "bot:ops", tid, "Which month should the report use?", wake=False, ask={
            "questions": [{"id": "month", "header": "Month", "question": "Which month should the report use?",
                           "multi": False, "other": True,
                           "options": [{"label": "July", "description": ""}, {"label": "August", "description": ""}]}],
            "who": "human:ana"})
        H.task_update(c, "bot:ops", tid, status="waiting", waiting_on="human:ana", note="Waiting on Ana's pick.")
        assert H.task(c, tid)["blocked_reason"].startswith(f"Waiting on {task_holds.name(c, 'human:ana')}")
        return tid, q["id"]

    with api.app.state.store.transaction() as c:
        direct, direct_q = asked(c, "Report month (direct)")
        commented, _ = asked(c, "Report month (comment)")
        picked, picked_q = asked(c, "Report month (pick)")
    post(api, f"messages/{direct_q}/answer", {"text": "July"})
    post(api, f"tasks/{commented}/comments", {"text": "July, please."})
    post(api, f"tasks/{picked}/answers", {"target": {"comment": picked_q}, "answers": {"month": ["July"]}})
    with api.app.state.store.read() as c:
        ana = task_holds.name(c, "human:ana")
        note_row = H.task(c, commented)
        assert H.open_task_asks(c, note_row)
        assert (note_row["status"], note_row["waiting_on"]) == ("waiting", "human:ana")
        for tid in (direct, picked):
            row = H.task(c, tid)
            assert not H.open_task_asks(c, row), tid
            assert (row["status"], row["waiting_on"]) == ("open", None), (tid, row["status"], row["blocked_reason"])
            assert row["note"] == f"{ana} answered your question; nothing left to wait on"
            assert tid not in [t["id"] for t in H.needs_you(c, "human:ana")["waiting"]]


def test_a_person_s_task_distinguishes_a_structured_answer_from_a_note(api):
    """A structured answer clears the question without moving a person's task; a note leaves it open."""
    def asked(c, title):
        tid = H.task_create(c, "bot:ops", title, "x", "human:ana")["id"]
        q = H.task_comment(c, "bot:ops", tid, "Which month for the review?", wake=False, ask={
            "questions": [{"id": "month", "header": "Month", "question": "Which month for the review?",
                           "multi": False, "other": True,
                           "options": [{"label": "July", "description": ""}, {"label": "August", "description": ""}]}],
            "who": "human:ana"})
        H.task_update(c, "human:ana", tid, status="waiting")
        return tid, q["id"]

    with api.app.state.store.transaction() as c:
        direct, direct_q = asked(c, "Review month (direct)")
        commented, _ = asked(c, "Review month (comment)")
        picked, picked_q = asked(c, "Review month (pick)")
        steps = {tid: H.task(c, tid)["step_id"] for tid in (direct, commented, picked)}
    post(api, f"messages/{direct_q}/answer", {"text": "July"})
    post(api, f"tasks/{commented}/comments", {"text": "July, please."})
    post(api, f"tasks/{picked}/answers", {"target": {"comment": picked_q}, "answers": {"month": ["July"]}})
    with api.app.state.store.read() as c:
        ana, ops = task_holds.name(c, "human:ana"), task_holds.name(c, "bot:ops")
        note_row = H.task(c, commented)
        assert H.open_task_asks(c, note_row)
        assert (note_row["status"], note_row["step_id"]) == ("waiting", steps[commented])
        for tid in (direct, picked):
            row = H.task(c, tid)
            assert (row["status"], row["step_id"]) == ("waiting", steps[tid]), tid
            assert row["blocked_reason"] == f"Nothing left to wait on: {ana} answered {ops}'s question", tid
            assert row["holds_cleared_at"], tid
