"""A task the person handed off through their Assistant reports back once in their Assistant chat when it ends
(backend/task_effects.py `_report_back`): one system line per terminal event with this cycle's note, none when the person
made the move themselves, a new Assistant chat when theirs is closed or deleted, only the
title for a task they can no longer read, and never at the cost of the move."""

import json

from backend import answers as A
from backend import auth as auth_module
from backend import assistant, task_effects
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers, post, unrelated_task_managers
from backend.tests.helpers.assistant import assistant_turn, turn_message
from backend.tests.helpers.calls import join, meet, start_call, thread  # noqa: F401 - meet installs the call doubles
from backend.tests.helpers.live_meetings import live  # noqa: F401 - the meet fixture needs the live API


def handoff(api, *titles, pipeline=None, person="ben"):
    """Ben (or `person`) asks their Assistant; its turn hands each piece of work to ops. Returns the tasks and the
    turn's room."""
    _, attempt = assistant_turn(api, person + "-test", "Have ops take these")
    filed = {"owner": "ops", "body": "For Oct 1.", **({"pipeline": pipeline} if pipeline else {})}
    made = [
        post(api, "tasks", {**filed, "title": title}, token=attempt["token"])
        for title in titles or ("Draft the launch post",)
    ]
    assert all(m["owner_id"] == "bot:ops" and m["requester_id"] == "human:" + person for m in made)
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
    assert first["body"] == f"Back on Draft the launch post:\n\n{link}\n\nDraft is in the doc. Thanks @ops"
    assert first["refs"]["task"] == made["id"] and first["refs"]["no_mentions"] and first["refs"]["quiet"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE message_id=?", (first["id"],)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM message_mentions WHERE message_id=?", (first["id"],)).fetchone()[0] == 0
    # The same terminal event again (a retried effect) writes nothing more: its key is already there.
    with api.app.state.store.transaction() as c:
        revision = first["wake_key"].rsplit(":", 1)[1]
        row = E.task(c, made["id"])
        task_effects._report_back(c, row, "bot:ops", "Again", revision)
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
        f"Back on Draft the launch post:\n\n{link}\n\nNot needed after all",
    ]
    assert len({r["wake_key"] for r in reports(api)}) == 3


def test_no_report_when_the_person_closed_it_themselves(api):
    [made], _ = handoff(api)
    end(api, made["id"], E.CLOSED, by="human:ben", why="I did it myself")
    assert reports(api) == []


def test_a_task_filed_for_someone_else_posts_nothing(api):
    [made], _ = handoff(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET requester_id='human:ana' WHERE id=?", (made["id"],))
    end(api, made["id"], E.DONE, why="Shipped.")
    assert reports(api) == []


def test_a_filer_who_has_left_gets_no_report_and_no_room(api):
    [made], origin = handoff(api)
    open_rooms = "SELECT count(*) FROM conversations WHERE owner_id='human:ben' AND closed_at IS NULL"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), origin))
        X.no_sign_in(c, "human:ben")
        before = c.execute(open_rooms).fetchone()[0]
    end(api, made["id"], E.DONE, why="Shipped.")
    assert reports(api) == []
    with api.app.state.store.read() as c:
        assert c.execute(open_rooms).fetchone()[0] == before


def said_done(c, task_id, note):
    """Ops says done through the supported call (task_calls.task_done), on the version it sees."""
    return H.task_done(c, "bot:ops", task_id, note, E.task(c, task_id)["version"])


def review(api, approver):
    """A one-stage pipeline: ops says done, then `approver` OKs. Returns the ok requirement's id."""
    with api.app.state.store.transaction() as c:
        made = X.pipeline(
            c,
            "review",
            "Review",
            stages=[
                {
                    "name": "Review",
                    "owner": "assignee",
                    "requirements": [{"kind": "owner_done"}, {"kind": "ok", "person": approver, "line": "OK it"}],
                }
            ],
        )
    return made["requirements"]["Review"][1]


def test_a_call_follow_up_reports_in_its_call_not_in_the_assistant_chat(meet, monkeypatch):
    with meet.app.state.store.transaction() as c:
        unrelated_task_managers(c, "ops")
    room = thread(meet)
    call = start_call(meet, room, bots=["ops"])
    join(meet, call["id"], "ben-test")
    _, attempt = assistant_turn(meet, "ben-test", "Have ops find the ship date")
    # Filed through the call's own route, as Ben: the private follow-up's receipt is his, the first time and on a retry,
    # and the Assistant reads none. The turn itself (its lease) is what the write lock validates.
    validated = []
    real = auth_module.validate_identity
    monkeypatch.setattr(auth_module, "validate_identity", lambda c, who: (validated.append(who), real(c, who))[1])
    ask = {"ask": "Find the ship date", "bot": "ops"}
    sent = headers(attempt["token"], key="follow-up-once")
    filed, again = (meet.post(f"/api/v2/calls/{call['id']}/follow-ups", json=ask, headers=sent) for _ in range(2))
    assert filed.status_code == 200 and again.json() == filed.json(), (filed.text, again.text)
    ben = [who for who in validated if who.actor == "human:ben"]
    assert ben and all(who.task_actor == "bot:assistant" for who in ben), validated
    task_id = filed.json()["task_id"]
    assert meet.get("/api/v2/tasks/" + task_id, headers=headers(attempt["token"])).status_code == 404
    with meet.app.state.store.read() as c:
        assert not H.task_private_readable(c, "bot:assistant", H.task(c, task_id))
    with meet.app.state.store.transaction() as c:
        said_done(c, task_id, "June 3")
    meet.app.state.calls.sweep()
    assert reports(meet) == []
    with meet.app.state.store.read() as c:
        said = c.execute(
            "SELECT body FROM messages WHERE conversation_id=? AND json_extract(refs,'$.task')=?",
            (room, task_id),
        ).fetchall()
    assert [r["body"].splitlines()[0] for r in said] == ["Back on Find the ship date: June 3"]


def test_a_done_again_after_a_send_back_is_the_note(api):
    ok = review(api, "human:cara")
    [made], _ = handoff(api, pipeline="review")
    with api.app.state.store.transaction() as c:
        said_done(c, made["id"], "First draft.")
        A.answer(c, made["id"], ok, "human:cara", "no", "Change the headline")
        said_done(c, made["id"], "New headline.")
        assert (
            c.execute("SELECT answer FROM answers WHERE requirement_id=? ORDER BY rowid DESC", (ok,)).fetchone()[0]
            == "again"
        )
        A.answer(c, made["id"], ok, "human:cara", "ok")
    [report] = reports(api)
    assert report["body"].endswith("\n\nNew headline.")


def built_into_review(api):
    """Build then Review (Cara OKs). Ops had said done on Build with no words; its later /done with a note is a plain
    forward move into Review. Returns the task and the Review OK's id."""
    with api.app.state.store.transaction() as c:
        made = X.pipeline(
            c,
            "build",
            "Build",
            stages=[
                {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
                {
                    "name": "Review",
                    "owner": "assignee",
                    "requirements": [{"kind": "ok", "person": "human:cara", "line": "OK it"}],
                },
            ],
        )
    [task], _ = handoff(api, pipeline="build")
    with api.app.state.store.transaction() as c:
        row = E.task(c, task["id"])
        X.answer(c, row, made["requirements"]["Build"][0], "done", "bot:ops")  # said earlier, with no words
        assert said_done(c, task["id"], "Built it.")["stage"]["name"] == "Review"
    return task, made["requirements"]["Review"][0]


def test_a_done_note_into_review_is_quoted_when_the_review_ok_ends_it(api):
    task, ok = built_into_review(api)
    with api.app.state.store.transaction() as c:
        assert A.answer(c, task["id"], ok, "human:cara", "ok")["status"] == "done"
    [report] = reports(api)
    assert report["body"].endswith("\n\nBuilt it.")


def test_a_close_with_no_reason_quotes_this_cycles_done_note(api):
    task, _ = built_into_review(api)
    end(api, task["id"], E.CLOSED, by="human:ana")
    [report] = reports(api)
    assert report["body"].endswith("\n\nBuilt it.")


def test_a_title_never_starts_a_list_or_a_heading():
    assert [task_effects._plain(t) for t in ("1. Plan", "- Plan", "+ Plan", "# Plan", "> Plan", "* Plan")] == [
        "1\\. Plan",
        "\\- Plan",
        "\\+ Plan",
        "\\# Plan",
        "\\> Plan",
        "\\* Plan",
    ]


def test_a_paused_assistant_still_gets_a_room_for_the_report(api):
    [made], origin = handoff(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), origin))
        c.execute("UPDATE bots SET state='paused' WHERE slug='assistant'")
    end(api, made["id"], E.DONE, why="Done.")
    [report] = reports(api)
    assert report["conversation_id"] != origin


def test_a_multiline_title_is_one_plain_line_before_the_link(api):
    [made], _ = handoff(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET title=? WHERE id=?", ("Fix\n```the *fence*", made["id"]))
    end(api, made["id"], E.DONE, why="Fixed.")
    [report] = reports(api)
    assert report["body"] == f"Back on Fix \\`\\`\\`the \\*fence\\*:\n\n[Open task](#/task/{made['id']})\n\nFixed."


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
    head, link, note = report["body"].split("\n\n", 2)
    assert (head, link) == ("Back on Draft the launch post:", f"[Open task](#/task/{made['id']})")
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


def test_a_task_the_person_can_no_longer_read_is_reported_by_its_filed_title_alone(api, monkeypatch):
    # The requester is a party, so only a copied page's rules (counsel.page_copy_readable) can hide it from them.
    from backend import auth

    [made], _ = handoff(api, "Plan the offsite")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET title='Offsite at the lake house' WHERE id=?", (made["id"],))
    monkeypatch.setattr(auth, "actor_reads_task", lambda c, actor, row: actor != "human:ben")
    end(api, made["id"], E.DONE, why="Secret venue picked.")
    [report] = reports(api)
    assert report["body"] == "Plan the offsite has ended" and "task" not in report["refs"]
