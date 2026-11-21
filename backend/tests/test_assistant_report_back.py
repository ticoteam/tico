"""A task the person handed off through their Assistant reports back once in their Assistant chat when it ends
(backend/task_effects.py `_report_back`): one system line per terminal event with this cycle's note, none when the person
made the move themselves, a new Assistant chat when theirs is closed or deleted, only the
title for a task they can no longer read, and never at the cost of the move."""

import json

from backend import assistant, task_effects
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import post
from backend.tests.helpers.assistant import assistant_turn, turn_message


def handoff(api, *titles):
    """Ben asks his Assistant; its turn hands each piece of work to ops. Returns the tasks and the turn's room."""
    _, attempt = assistant_turn(api, "ben-test", "Have ops take these")
    made = [
        post(api, "tasks", {"owner": "ops", "title": title, "body": "For Oct 1."}, token=attempt["token"])
        for title in titles or ("Draft the launch post",)
    ]
    assert all(m["owner_id"] == "bot:ops" and m["requester_id"] == "human:ben" for m in made)
    with api.app.state.store.read() as c:
        origin = c.execute("SELECT conversation_id FROM messages WHERE id=?", (turn_message(api, attempt),)).fetchone()
    return made, origin[0]


def end(api, task_id, to, by="bot:ops", why=None, notes=None):
    """Move the task. To Done, its owner says done first (with `notes`, its done summary)."""
    with api.app.state.store.transaction() as c:
        row = E.task(c, task_id)
        if to == E.DONE:
            rid = c.execute(
                "SELECT id FROM requirements WHERE stage_id=? AND kind='owner_done' AND withdrawn_at IS NULL",
                (row["stage_id"],),
            ).fetchone()[0]
            X.answer(c, row, rid, "done", by, notes)
        return E.move(c, task_id, to, by, why, seen_version=row["version"])


def reports(api):
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT id, sender_id, recipient_id, conversation_id, body, refs, wake_key FROM messages "
            "WHERE json_extract(refs,'$.report_back')=1 ORDER BY created_at, rowid"
        ).fetchall()
    return [{**dict(r), "refs": json.loads(r["refs"])} for r in rows]


def test_each_end_reports_once_with_this_cycles_note_as_a_system_line(api):
    [made], origin = handoff(api)
    end(api, made["id"], E.DONE, notes="Draft is in the doc. Thanks @ops")
    [first] = reports(api)
    assert first["sender_id"] == "bot:assistant" and first["recipient_id"] == "human:ben"
    assert first["conversation_id"] == origin
    link = f"[Open task](#/task/{made['id']})"
    assert first["body"] == f"Back on Draft the launch post:\n{link}\n\nDraft is in the doc. Thanks @ops"
    assert first["refs"]["task"] == made["id"] and first["refs"]["no_mentions"] and first["refs"]["quiet"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE message_id=?", (first["id"],)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM message_mentions WHERE message_id=?", (first["id"],)).fetchone()[0] == 0
    # The same terminal event again (a retried effect) writes nothing more: its key is already there.
    with api.app.state.store.transaction() as c:
        revision = first["wake_key"].rsplit(":", 1)[1]
        task_effects._report_back(c, E.task(c, made["id"]), "bot:ops", "Again", revision)
    assert len(reports(api)) == 1
    # Reopened and done again with no words: the first cycle's note is not reused.
    end(api, made["id"], E.REOPEN, by="human:ana", why="One more pass")
    end(api, made["id"], E.DONE)
    # Reopened and closed by someone else: the move's own note.
    end(api, made["id"], E.REOPEN, by="human:ana", why="Again")
    end(api, made["id"], E.CLOSED, by="human:ana", why="Not needed after all")
    bodies = [r["body"] for r in reports(api)]
    assert bodies[1:] == [
        f"Draft the launch post ended without a result\n\n{link}",
        f"Back on Draft the launch post:\n{link}\n\nNot needed after all",
    ]
    assert len({r["wake_key"] for r in reports(api)}) == 3


def test_no_report_when_the_person_closed_it_themselves(api):
    [made], _ = handoff(api)
    end(api, made["id"], E.CLOSED, by="human:ben", why="I did it myself")
    assert reports(api) == []


def test_a_call_follow_up_handed_off_through_the_assistant_reports_back_too(api):
    [made], _ = handoff(api, "Find the ship date")
    with api.app.state.store.transaction() as c:
        c.execute("PRAGMA defer_foreign_keys=ON")
        c.execute("INSERT INTO call_follow_ups(task_id,call_id,bot_id) VALUES(?,?,?)", (made["id"], "call-1", "ops"))
        row = E.task(c, made["id"])
        X.say_done(c, row)
        E.move(c, made["id"], E.DONE, "bot:ops", "June 3", seen_version=row["version"])
        c.execute("DELETE FROM call_follow_ups WHERE task_id=?", (made["id"],))
    [report] = reports(api)
    assert report["body"].endswith("\n\nJune 3")


def test_a_done_again_after_a_send_back_is_the_note(api):
    [made], _ = handoff(api)
    with api.app.state.store.transaction() as c:
        row = E.task(c, made["id"])
        rid = c.execute(
            "SELECT id FROM requirements WHERE stage_id=? AND kind='owner_done' AND withdrawn_at IS NULL",
            (row["stage_id"],),
        ).fetchone()[0]
        # The done again carries the words; the owner's done that follows says none.
        X.answer(c, row, rid, "again", "bot:ops", "Fixed the intro.")
        X.answer(c, row, rid, "done", "bot:ops")
        E.move(c, made["id"], E.DONE, "bot:ops", None, seen_version=row["version"])
    [report] = reports(api)
    assert report["body"].endswith("\n\nFixed the intro.")


def test_a_bad_create_audit_skips_the_report_and_the_move_still_commits(api):
    [made], _ = handoff(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE events SET detail='[1]' WHERE action='task.create' AND target=?", (made["id"],))
    end(api, made["id"], E.DONE, why="Shipped.")
    with api.app.state.store.read() as c:
        assert E.task(c, made["id"])["status"] == "done"
    assert reports(api) == []


def test_the_link_stays_outside_an_open_fence_and_a_long_note(api):
    [made], _ = handoff(api)
    end(api, made["id"], E.DONE, notes="```\n" + "x" * 5000)
    [report] = reports(api)
    head, note = report["body"].split("\n\n", 1)
    assert head == f"Back on Draft the launch post:\n[Open task](#/task/{made['id']})"
    assert note.startswith("```") and len(note) == 4000


def test_a_closed_or_deleted_assistant_room_gives_way_to_a_new_one(api):
    (made, other), origin = handoff(api, "Draft the launch post", "Book the venue")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET deleted_at=? WHERE id=?", (H.now(), origin))
    end(api, made["id"], E.DONE, why="Done.")
    [report] = reports(api)
    with api.app.state.store.read() as c:
        rooms = {
            r["id"]: dict(r)
            for r in c.execute(
                "SELECT id, scope, owner_id, room_key, closed_at, deleted_at FROM conversations WHERE id IN (?,?)",
                (origin, report["conversation_id"]),
            )
        }
    assert report["conversation_id"] != origin and rooms[origin]["closed_at"]
    fresh = rooms[report["conversation_id"]]
    assert (fresh["scope"], fresh["owner_id"], fresh["room_key"], fresh["closed_at"], fresh["deleted_at"]) == (
        "personal",
        "human:ben",
        "assistant",
        None,
        None,
    )
    # A closed room: the next report opens another.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), report["conversation_id"]))
    end(api, other["id"], E.DONE, why="Booked.")
    assert reports(api)[1]["conversation_id"] not in (origin, report["conversation_id"])


def test_a_report_that_fails_never_undoes_the_move(api, monkeypatch):
    [made], _ = handoff(api)

    def paused(*_):
        raise RuntimeError("the Assistant is paused")

    monkeypatch.setattr(assistant, "ensure_room", paused)
    end(api, made["id"], E.DONE, why="Shipped.")
    with api.app.state.store.read() as c:
        assert E.task(c, made["id"])["status"] == "done"
    assert reports(api) == []


def test_a_task_the_person_can_no_longer_read_is_reported_by_its_filed_title_alone(api):
    [made], _ = handoff(api, "Plan the offsite")
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE tasks SET title='Offsite at the lake house', requester_id='human:cara', private=1, readers=NULL "
            "WHERE id=?",
            (made["id"],),
        )
    end(api, made["id"], E.DONE, why="Secret venue picked.")
    [report] = reports(api)
    assert report["body"] == "Plan the offsite has ended" and "task" not in report["refs"]
