"""A waiting task's Blocked reason names what really holds it, and follows every change to its holds; a question to a
person is a question task that lives and dies with the task it was asked under (backend/task_holds.py, Dev 627)."""

from backend.tests.test_api import api, post  # noqa: F401
from backend.store import H
from backend import question_tasks as QT, still_needed, task_holds
from backend.tests.hold_rows import set_holds


def bot_task(c, title, owner="bot:ops", parent=None, actor="human:ben"):
    return H.task_create(c, actor, title, "x", owner, parent_id=parent)["id"]


def ask(c, tid, text, asker="bot:ops", to="human:ana"):
    """`asker` asks `to` a question task under the task."""
    return QT.ask_plain(c, asker, to, text, parent=H.task(c, tid))


def wait(c, tid, owner="bot:ops", note=None):
    H.task_update(c, owner, tid, status="waiting", note=note)
    return H.task(c, tid)


def notices(c, to, news):
    return [
        m
        for m in H._rows(c.execute("SELECT body, refs FROM messages WHERE recipient_id=? ORDER BY created_at", (to,)))
        if (H._json(m["refs"], {}) or {}).get("news") == news
    ]


def test_finishing_parent_closes_its_subtask_and_its_question(api):
    """A subtask closes with its parent, clearing the question and its hold."""
    with api.app.state.store.transaction() as c:
        parent = bot_task(c, "Spec Settings search")
        child = bot_task(c, "Ask Ana about search scope", parent=parent, actor="bot:ops")
        question = ask(c, child, "Search titles only, or bodies too?")
        wait(c, child)
        H.task_update(c, "bot:ops", parent, status="done", note="Spec written")
        assert not QT.is_open(H.task(c, question["id"]))
        row = H.task(c, child)
        assert row["status"] == "closed" and not row["blocked_reason"]
        assert QT.waiting_on(c, row) is None


def test_still_needed_returns_the_question_and_reopens_its_bot_task_without_a_second_notice(api):
    with api.app.state.store.transaction() as c:
        tid = bot_task(c, "Order the mail key")
        question = ask(c, tid, "Which mailbox gets the key?")
        wait(c, tid)
        still_needed.send_back(c, "bot:ops", [question])
        assert H.task(c, question["id"])["status"] == "closed"
        row = H.task(c, tid)
        assert row["status"] == "open" and "nothing left to wait on" in row["note"]


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
        approval = H.approval_request(
            c, "bot:ops", "spend", {"amount": 120, "account": "card", "what": "Vendor A"}, task_id=tid
        )
        row = wait(c, tid)
        assert row["blocked_reason"] == f"Waiting on {ana}: Vendor A or B? (and 1 more)"
        H.task_update(c, "human:ana", question["id"], status="done", note="A")
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
        set_holds(c, manual, blocked_kind='manual', blocked_reason='Held until the legal review')
        H.task_update(c, "bot:cpo", sub, status="done", note="Proofread")
        row = H.task(c, manual)
        assert row["status"] == "waiting" and row["blocked_reason"] == "Held until the legal review"


def test_a_private_child_is_never_named_in_its_parent_s_reason(api):
    """The reason is shown to everyone who reads the parent; the private child's title stays off it."""
    with api.app.state.store.transaction() as c:
        mine = bot_task(c, "Plan the audit", owner="human:ana", actor="human:ana")
        secret = H.task_create(
            c, "human:ana", "Vendor breach review", "x", "bot:ops", parent_id=mine, private=True, lint=False
        )["id"]
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
    """Criterion 8 on the recorded wait itself, not only the question: a note alone never ends a wait on a person."""
    with api.app.state.store.transaction() as c:
        ana = task_holds.name(c, "human:ana")
        # Their open question task holds it (derived): a note edit leaves it, and who it waits on, as they were.
        card = bot_task(c, "Sign the lease")
        H.task_update(
            c,
            "bot:ops",
            card,
            status="waiting",
            waiting_on="human:ana",
            note="Sign the lease today? I'll send it to the landlord after.",
        )
        H.task_update(c, "bot:ops", card, note="waiting on Engineering")
        row = H.task(c, card)
        assert row["status"] == "waiting" and row["note"] == "waiting on Engineering"
        assert row["blocked_reason"].startswith(f"Waiting on {ana}")
        assert QT.waiting_on(c, row) == "human:ana"


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


def test_an_answer_ends_the_wait_but_an_unmatched_note_keeps_it(api):
    """Answering the question task clears the wait; a note on the waiting task preserves it."""

    def asked(c, title):
        tid = bot_task(c, title)
        card = ask(c, tid, "Which month should the report use?")
        wait(c, tid)
        assert H.task(c, tid)["blocked_reason"].startswith(f"Waiting on {task_holds.name(c, 'human:ana')}")
        return tid, card["id"]

    with api.app.state.store.transaction() as c:
        direct, direct_q = asked(c, "Report month (direct)")
        commented, commented_q = asked(c, "Report month (comment)")
        picked, picked_q = asked(c, "Report month (route)")
        H.task_update(c, "human:ana", direct_q, status="done", note="July")
    post(api, f"tasks/{commented}/comments", {"text": "July, please."})
    post(api, f"tasks/{picked_q}/answers", {"target": {"task": picked_q}, "answers": {}, "other": "July"})
    with api.app.state.store.read() as c:
        ana = task_holds.name(c, "human:ana")
        note_row = H.task(c, commented)
        assert QT.is_open(H.task(c, commented_q))
        assert note_row["status"] == "waiting"
        for tid, card in ((direct, direct_q), (picked, picked_q)):
            row = H.task(c, tid)
            assert not QT.is_open(H.task(c, card)), tid
            assert row["status"] == "open", (tid, row["status"], row["blocked_reason"])
            assert row["note"].startswith(f"{ana} answered"), row["note"]
            assert tid not in [t["id"] for t in H.needs_you(c, "human:ana")["waiting"]]


def test_a_person_s_task_distinguishes_an_answer_from_a_note(api):
    """An answer clears the question without moving a person's task; a note leaves it open."""

    def asked(c, title):
        tid = H.task_create(c, "bot:ops", title, "x", "human:ana")["id"]
        card = ask(c, tid, "Which month for the review?")
        H.task_update(c, "human:ana", tid, status="waiting")
        return tid, card["id"]

    with api.app.state.store.transaction() as c:
        direct, direct_q = asked(c, "Review month (direct)")
        commented, commented_q = asked(c, "Review month (comment)")
        picked, picked_q = asked(c, "Review month (route)")
        steps = {tid: H.task(c, tid)["step_id"] for tid in (direct, commented, picked)}
        H.task_update(c, "human:ana", direct_q, status="done", note="July")
    post(api, f"tasks/{commented}/comments", {"text": "July, please."})
    post(api, f"tasks/{picked_q}/answers", {"target": {"task": picked_q}, "answers": {}, "other": "July"})
    with api.app.state.store.read() as c:
        note_row = H.task(c, commented)
        assert QT.is_open(H.task(c, commented_q))
        assert (note_row["status"], note_row["step_id"]) == ("waiting", steps[commented])
        for tid in (direct, picked):
            row = H.task(c, tid)
            assert (row["status"], row["step_id"]) == ("waiting", steps[tid]), tid
            assert row["blocked_reason"].startswith("Nothing left to wait on"), tid
            assert row["holds_cleared_at"], tid


def test_taking_off_a_stale_finished_blocker_releases_nothing(api):
    """A finished blocker held nothing: removing a stale relation must not send a stranded bot task back to work."""
    from backend import task_relations as TR

    with api.app.state.store.transaction() as c:
        tid = bot_task(c, "Ship the form")
        blocker = bot_task(c, "Pick the form vendor", owner="bot:cpo")
        TR.add_blocker(c, H.KEEPER, tid, blocker)
        wait(c, tid)
        assert H.task(c, tid)["status"] == "waiting"
        # A legacy completed blocker can retain its relation after a crash between the status and cleanup writes.
        c.execute("UPDATE tasks SET status='done', done_at=? WHERE id=?", (H.now(), blocker))
        TR.remove_blocker(c, H.KEEPER, tid, blocker)
        row = H.task(c, tid)
        assert (row["status"], row["blocked_reason"]) == ("waiting", "Nothing tracked to wait on")


def test_a_bot_task_waiting_on_nothing_goes_back_to_open_after_the_grace_with_one_wake_per_bot(api):
    """Live: bot tasks parked "Nothing tracked to wait on" sat for a week, since nothing wakes them."""
    with api.app.state.store.transaction() as c:
        loose = [bot_task(c, f"Draft the launch post {n}") for n in range(2)]
        for tid in loose:
            assert wait(c, tid, note="Waiting on the vendor")["blocked_kind"] == "stranded"
        before = len(notices(c, "bot:ops", news="assignment"))
        assert task_holds.unstrand(c, H.shift(H.now(), hours=1))["opened"] == 0
        assert all(H.task(c, tid)["status"] == "waiting" for tid in loose)
        assert task_holds.unstrand(c, H.shift(H.now(), hours=3)) == {"opened": 2, "woken": 1, "told": 0}
        for tid in loose:
            row = H.task(c, tid)
            assert row["status"] == "open" and not row["blocked_since"] and row["note"] == task_holds.REOPENED
        assert len(notices(c, "bot:ops", news="assignment")) == before + 1


def test_a_due_date_ahead_a_hand_hold_and_a_person_s_task_are_left_waiting(api):
    with api.app.state.store.transaction() as c:
        later = H.shift(H.now(), hours=3)
        due = bot_task(c, "Check the vendor reply")
        wait(c, due)
        c.execute("UPDATE tasks SET due_at=? WHERE id=?", (H.shift(H.now(), hours=5), due))
        manual = bot_task(c, "Hold the newsletter")
        wait(c, manual)
        set_holds(c, manual, blocked_kind="manual", blocked_reason="Held until the legal review")
        person = bot_task(c, "Plan the offsite", owner="human:ana", actor="human:ana")
        assert wait(c, person, owner="human:ana")["blocked_kind"] == "stranded"
        assert task_holds.unstrand(c, later)["opened"] == 0
        assert [H.task(c, t)["status"] for t in (due, manual, person)] == ["waiting"] * 3
        # The due date passed: it was something to wait on, and now it is gone.
        assert task_holds.unstrand(c, H.shift(H.now(), hours=6))["opened"] == 1
        assert H.task(c, due)["status"] == "open"
        assert [H.task(c, t)["status"] for t in (manual, person)] == ["waiting"] * 2


def test_a_task_sent_back_three_times_in_a_day_stays_waiting_and_its_manager_bot_hears_once(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")
        tid = bot_task(c, "Sync the price list")
        for hours in (3, 6, 9):
            wait(c, tid)
            assert task_holds.unstrand(c, H.shift(H.now(), hours=hours))["opened"] == 1
        wait(c, tid)
        assert task_holds.unstrand(c, H.shift(H.now(), hours=12)) == {"opened": 0, "woken": 0, "told": 1}
        assert H.task(c, tid)["status"] == "waiting"
        told = notices(c, "bot:cpo", news="stranded_loop")
        assert len(told) == 1 and "Sync the price list" in told[0]["body"]
        assert task_holds.unstrand(c, H.shift(H.now(), hours=13))["told"] == 0
        assert notices(c, "human:ana", news="stranded_loop") == [] == notices(c, "human:ben", news="stranded_loop")
