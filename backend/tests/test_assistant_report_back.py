"""A task the person handed off through their Assistant reports back once in their Assistant chat when it ends
(backend/task_effects.py `_report_back`): one message per terminal event, none when they ended it themselves, none for a
call's follow-up, their own Assistant chat when the turn's room is gone, and only the title for a task they can no
longer read."""

import json

from backend import task_effects
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
            "SELECT sender_id, recipient_id, conversation_id, body, refs, wake_key FROM messages "
            "WHERE json_extract(refs,'$.report_back')=1 ORDER BY created_at, rowid"
        ).fetchall()
    return [{**dict(r), "refs": json.loads(r["refs"])} for r in rows]


def test_a_handoff_that_ends_reports_back_once_per_end_in_the_turns_room(api):
    [made], origin = handoff(api)
    end(api, made["id"], E.DONE, why="Posted the draft in the launch doc.")
    [first] = reports(api)
    assert first["sender_id"] == "bot:assistant" and first["recipient_id"] == "human:ben"
    assert first["conversation_id"] == origin
    assert first["body"] == (
        f"Back on Draft the launch post: Posted the draft in the launch doc.\n\n[Open task](#/task/{made['id']})"
    )
    assert first["refs"]["task"] == made["id"]
    # The same terminal event again (a retried effect) writes nothing more: its key is already there.
    with api.app.state.store.transaction() as c:
        revision = first["wake_key"].rsplit(":", 1)[1]
        task_effects._report_back(c, E.task(c, made["id"]), "bot:ops", "Again", revision)
    assert len(reports(api)) == 1
    # Reopened and ended again: a second report.
    end(api, made["id"], E.REOPEN, by="human:ana", why="One more pass")
    end(api, made["id"], E.CLOSED, by="human:ana", why="Not needed after all")
    later = reports(api)
    assert len(later) == 2 and later[1]["wake_key"] != later[0]["wake_key"]
    assert later[1]["body"].startswith("Back on Draft the launch post: Not needed after all")


def test_the_owners_done_note_stands_in_and_no_note_says_it_ended_without_a_result(api):
    (made, other), _ = handoff(api, "Draft the launch post", "Book the venue")
    end(api, made["id"], E.DONE, notes="Draft is in the doc.")
    end(api, other["id"], E.DONE)
    bodies = [r["body"].split("\n\n")[0] for r in reports(api)]
    assert bodies == ["Back on Draft the launch post: Draft is in the doc.", "Book the venue ended without a result"]


def test_no_report_when_the_person_closed_it_themselves_or_for_a_call_follow_up(api):
    (made, followed), _ = handoff(api, "Draft the launch post", "Find the ship date")
    end(api, made["id"], E.CLOSED, by="human:ben", why="I did it myself")
    with api.app.state.store.transaction() as c:
        c.execute("PRAGMA defer_foreign_keys=ON")
        c.execute(
            "INSERT INTO call_follow_ups(task_id,call_id,bot_id) VALUES(?,?,?)", (followed["id"], "call-1", "ops")
        )
        row = E.task(c, followed["id"])
        X.say_done(c, row)
        E.move(c, followed["id"], E.DONE, "bot:ops", "June 3", seen_version=row["version"])
        c.execute("DELETE FROM call_follow_ups WHERE task_id=?", (followed["id"],))
    assert reports(api) == []


def test_a_gone_turn_room_reports_in_the_persons_own_assistant_chat(api):
    [made], origin = handoff(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), origin))
    end(api, made["id"], E.DONE, why="Done.")
    [report] = reports(api)
    assert report["conversation_id"] != origin and report["wake_key"].startswith(f"report_back:{made['id']}:")
    with api.app.state.store.read() as c:
        room = c.execute(
            "SELECT scope, owner_id, room_key, closed_at FROM conversations WHERE id=?", (report["conversation_id"],)
        ).fetchone()
    assert dict(room) == {"scope": "personal", "owner_id": "human:ben", "room_key": "assistant", "closed_at": None}


def test_a_task_the_person_can_no_longer_read_is_reported_by_its_title_alone(api):
    [made], _ = handoff(api, "Plan the offsite")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET requester_id='human:cara', private=1, readers=NULL WHERE id=?", (made["id"],))
    end(api, made["id"], E.DONE, why="Secret venue picked.")
    [report] = reports(api)
    assert report["body"] == "Plan the offsite has ended" and "task" not in report["refs"]
