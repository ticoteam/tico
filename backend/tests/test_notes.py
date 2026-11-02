"""Quiet notes: something for a bot's next run to know, asking nothing.

Ben, 2026-09-24: each monitor leaves the Product Manager a note after its check, the notes
stack up without waking it, and its daily report, which runs after every monitor has checked,
reads them all in one prompt, each with the time it was sent.
"""

from backend import hubdb as H
from backend.tests.test_api import api, assign, get, post, ready, runner  # noqa: F401


def claim(api, r, next_run=True):
    return post(api, "jobs/claim", {"next_run": next_run}, token=r["token"])["attempt"]


def setup(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    return r


def note(api, text="Cancellations are flat; nothing for Ben today.", to="ops", token="ana-test"):
    return post(api, "notes", {"to": to, "text": text}, token=token)["note"]


def jobs(api, bot="ops"):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs WHERE bot=?", (bot,)).fetchone()[0]


def listed(api, slug="ops"):
    return next(row for row in get(api, "bots") if row["slug"] == slug)


def test_a_note_wakes_nobody_and_waits(api):
    r = setup(api)
    n = note(api)
    assert n["waiting"] is True and n["carried"] is False
    assert n["to"] == "bot:ops" and n["from"] == "human:ana"
    assert jobs(api) == 0
    assert claim(api, r) is None
    assert listed(api)["notes"] == 1
    assert listed(api)["queued"] == 0


def test_a_failed_run_gives_them_back_and_a_finished_one_does_not(api):
    r = setup(api)
    n = note(api)
    post(api, "chat/ops", {"text": "First"})
    first = claim(api, r)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t1"}, token=r["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "failed", "text": "boom", "last_seq": 0}, token=r["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='cancelled' WHERE state='uncertain'")
        c.execute("UPDATE run_backoff SET wait_until=NULL")        # the fast failure's wait is over
    post(api, "chat/ops", {"text": "Second"})
    second = claim(api, r)
    assert [x["id"] for x in second["notes"]] == [n["id"]]
    post(api, f"attempts/{second['id']}/started", {"thread_id": "t2"}, token=r["token"])
    post(api, f"attempts/{second['id']}/complete", {"outcome": "completed", "text": "ok", "last_seq": 0}, token=r["token"])
    post(api, "chat/ops", {"text": "Third"})
    assert claim(api, r)["notes"] == []


def test_a_bot_leaves_another_bot_a_note_and_sees_only_its_own(api):
    # The monitors' case: a bot's run leaves the Product Manager a note.
    r = setup(api)
    post(api, "chat/ops", {"text": "Run your check"})
    attempt = claim(api, r)
    left = note(api, "Ops check: all green.", to="cpo", token=attempt["token"])
    assert left["from"] == "bot:ops" and left["to"] == "bot:cpo"
    note(api, "Something only ana sent to coo.", to="coo")
    mine = get(api, "notes", token=attempt["token"])["notes"]
    assert [n["text"] for n in mine] == ["Ops check: all green."]


def test_a_bot_that_only_runs_tasks_gets_its_notes_once(api):
    # Every turn of a task-keyed bot on a lanes runner is in a task lane, so no main-lane turn ever comes.
    from backend.tests.test_task_lanes import new_task, task_bot
    r = task_bot(api, lanes=True)
    n = note(api)
    new_task(api, "first")
    new_task(api, "second")
    first, second = claim(api, r), claim(api, r)
    assert first["lane"].startswith("task:") and second["lane"].startswith("task:")
    assert [x["id"] for x in first["notes"]] == [n["id"]]
    assert second["notes"] == []


def test_the_note_a_lifted_quarantine_leaves_reaches_the_next_run_ahead_of_a_backlog(api):
    from backend import hubdb as H
    r = setup(api)
    with api.app.state.store.transaction() as c:
        H.quarantine(c, "ops", "10 repeated refusals today", refusal={"rule": "identity", "detail": "lane is a person's"})
        for i in range(50):
            c.execute("INSERT INTO notes(id,from_actor,to_actor,body,created) VALUES(?,?,?,?,?)",
                      (H.new_id(), "human:ana", "bot:ops", f"Older note {i}", H.shift(H.now(), seconds=-600 + i)))
        H.status_set(c, "human:ana", "ops", state="active", reason="reviewed")
    post(api, "chat/ops", {"text": "Back to the exports"})
    first = claim(api, r)["notes"]
    assert len(first) == 50 and first[0]["from"] == H.KEEPER
    assert "lane is a person's (identity)" in first[0]["text"] and first[0]["text"].endswith(H.QUARANTINE_LIFTED)


def settled(api, nid):
    with api.app.state.store.read() as c:
        return c.execute("SELECT settled_at FROM notes WHERE id=?", (nid,)).fetchone()[0]


def test_a_completed_run_settles_its_notes_and_a_failed_one_does_not(api):
    r = setup(api)
    n = note(api)
    post(api, "chat/ops", {"text": "First"})
    first = claim(api, r)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t1"}, token=r["token"])
    assert settled(api, n["id"]) is None                      # carried by a run still going
    post(api, f"attempts/{first['id']}/complete", {"outcome": "failed", "text": "boom", "last_seq": 0}, token=r["token"])
    assert settled(api, n["id"]) is None                      # the failed run gave it back, so it waits again
    with api.app.state.store.read() as c:
        assert [x["id"] for x in H.notes_waiting(c, "ops")] == [n["id"]]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='cancelled' WHERE state='uncertain'")
        c.execute("UPDATE run_backoff SET wait_until=NULL")
    post(api, "chat/ops", {"text": "Second"})
    second = claim(api, r)
    post(api, f"attempts/{second['id']}/started", {"thread_id": "t2"}, token=r["token"])
    post(api, f"attempts/{second['id']}/complete", {"outcome": "completed", "text": "ok", "last_seq": 0}, token=r["token"])
    assert settled(api, n["id"])
    with api.app.state.store.read() as c:
        assert H.notes_waiting(c, "ops") == [] and H.notes_waiting_counts(c, ["ops"]) == {"ops": 0}
    shown = next(x for x in get(api, "notes")["notes"] if x["id"] == n["id"])
    assert shown["waiting"] is False and shown["carried"] is True and shown["carried_by"] == second["id"]


def test_an_unsettled_note_with_a_completed_carrier_is_still_read_and_the_catch_up_settles_it(api):
    r = setup(api)
    n = note(api)
    post(api, "chat/ops", {"text": "First"})
    first = claim(api, r)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t1"}, token=r["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "ok", "last_seq": 0}, token=r["token"])
    store = api.app.state.store
    with store.transaction() as c:                            # a path that missed it, or a database from before the column
        c.execute("UPDATE notes SET settled_at=NULL WHERE id=?", (n["id"],))
    with store.read() as c:
        assert H.notes_waiting(c, "ops") == [] and not H.note_waiting(c, H.note(c, n["id"]))
    with store.transaction() as c:
        assert H.notes_settle_catch_up(c, 500) == 1 and H.notes_settle_catch_up(c, 500) == 0
    assert settled(api, n["id"])
    store.initialize()                                        # a repeated start leaves it settled
    assert settled(api, n["id"])


def test_the_migration_settles_old_notes_of_completed_runs_only(api):
    r = setup(api)
    done, failed = note(api, "one"), None
    post(api, "chat/ops", {"text": "First"})
    first = claim(api, r)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t1"}, token=r["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "ok", "last_seq": 0}, token=r["token"])
    failed = note(api, "two")
    post(api, "chat/ops", {"text": "Second"})
    second = claim(api, r)
    post(api, f"attempts/{second['id']}/started", {"thread_id": "t2"}, token=r["token"])
    post(api, f"attempts/{second['id']}/complete", {"outcome": "failed", "text": "boom", "last_seq": 0}, token=r["token"])
    waiting = note(api, "three")
    store = api.app.state.store
    with store.transaction() as c:                            # a database from before the column
        c.execute("DROP INDEX notes_waiting")
        c.execute("DROP INDEX notes_unsettled_carried")
        c.execute("ALTER TABLE notes DROP COLUMN settled_at")
    store.initialize()
    assert settled(api, done["id"]) and settled(api, failed["id"]) is None and settled(api, waiting["id"]) is None
    with store.read() as c:
        assert {x["id"] for x in H.notes_waiting(c, "ops")} == {failed["id"], waiting["id"]}


def test_the_waiting_scan_reads_only_the_unsettled_index(api):
    store = api.app.state.store
    with store.read() as c:
        for sql, args in ((f"SELECT n.* FROM notes n WHERE n.to_actor=? AND {H.NOTE_WAITING_SQL} "
                           "ORDER BY NOT (n.from_actor=? AND n.message_id IS NULL),n.created,n.id LIMIT ?",
                           ("bot:ops", "tico", 50)),
                          (f"SELECT n.to_actor,count(*) FROM notes n WHERE n.to_actor IN (?) AND {H.NOTE_WAITING_SQL} "
                           "GROUP BY n.to_actor", ("bot:ops",))):
            plan = " ".join(str(row[3]) for row in c.execute("EXPLAIN QUERY PLAN " + sql, args))
            assert "notes_waiting" in plan, plan


def test_a_deferred_thread_note_the_bot_lost_access_to_is_cancelled_not_carried(api):
    # A thread message deferred into a bot's notes must not outlive the bot's access: removing the bot from the thread
    # cancels what it would carry, and the bot's next unrelated run reads only the notes it can still read.
    from backend import threads
    from backend.tests.test_threads_delivery import new_thread, say
    r = setup(api)
    store = api.app.state.store
    lost = new_thread(api, members=("human:ben", "bot:ops"), title="Lost")
    kept = new_thread(api, members=("human:ben", "bot:ops"), title="Kept")
    lost_msg = say(api, lost["id"], "Private: the payroll numbers")
    kept_msg = say(api, kept["id"], "Ben's note for ops")
    with store.transaction() as c:
        threads._defer_note(c, H.message(c, lost_msg["id"]), "bot:ops")
        threads._defer_note(c, H.message(c, kept_msg["id"]), "bot:ops")
        ana = api.app.state.auth.identity_for_actor(c, "human:ana")
        threads.remove_member(c, ana, lost["id"], "bot:ops")
    post(api, "chat/ops", {"text": "Unrelated run"})
    run = claim(api, r)
    carried = [n["text"] for n in run["notes"]]
    assert not any("payroll" in text for text in carried), carried
    assert any("Ben's note for ops" in text for text in carried), carried
    with store.read() as c:
        cancelled = c.execute("SELECT cancelled_at FROM notes WHERE message_id=?", (lost_msg["id"],)).fetchone()[0]
    assert cancelled


def test_a_note_whose_source_the_bot_lost_is_not_read_back_through_the_notes_list(api):
    # Before the next claim, /notes must not return a copied body the recipient can no longer read.
    from backend import threads
    from backend.tests.test_threads_delivery import new_thread, say
    r = setup(api)
    store = api.app.state.store
    lost = new_thread(api, members=("human:ben", "bot:ops"), title="Lost")
    lost_msg = say(api, lost["id"], "Private: the payroll numbers")
    with store.transaction() as c:
        threads._defer_note(c, H.message(c, lost_msg["id"]), "bot:ops")
        ana = api.app.state.auth.identity_for_actor(c, "human:ana")
        threads.remove_member(c, ana, lost["id"], "bot:ops")
    post(api, "chat/ops", {"text": "Unrelated turn"})
    turn = claim(api, r, next_run=False)                  # an active turn: its notes are not carried
    visible = get(api, "notes", token=turn["token"])["notes"]
    assert not any("payroll" in (n.get("text") or "") for n in visible), visible
