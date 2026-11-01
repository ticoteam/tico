"""A bot back from a quarantine is told the refusal that stopped it, and its manager bot can write to it meanwhile."""

import json

import pytest

from backend import hubdb as H
from backend.tests.test_tasks_board import api, bot_token, headers, post  # noqa: F401 (fixture)


def notes_for(api, slug):
    with api.app.state.store.read() as c:
        return H.notes_waiting(c, slug)


def test_a_lifted_quarantine_leaves_the_bot_a_note_with_the_refusal_that_stopped_it(api):
    token = bot_token(api, "cpo")
    item = post(api, "tasks", {"owner_id": "cpo", "title": "Research exports", "body": "x"})
    for _ in range(H.QUARANTINE_AT + 1):
        r = api.post("/api/v2/tasks/" + item["id"], json={"version": item["version"], "number": 993}, headers=headers(token))
        assert r.status_code == 403, r.text
    with api.app.state.store.read() as c:
        assert H.bot(c, "cpo")["state"] == "quarantined"
    assert notes_for(api, "cpo") == [], "nothing until it is back"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE events SET created_at=? WHERE action='quarantine'", (H.shift(H.now(), seconds=-3700),))
        assert H.lift_cooled_quarantines(c) == ["cpo"]
    [note] = notes_for(api, "cpo")
    assert note["sender_id"] == H.KEEPER
    assert "number on a task is changed by a person" in note["body"] and "(identity)" in note["body"]
    assert note["body"].endswith(H.QUARANTINE_LIFTED)
    # A person clearing it leaves the same note; so does an escape, naming what the check stopped.
    with api.app.state.store.transaction() as c:
        H.quarantine(c, "cpo", "escape: a secrets path", context={"what": "task note", "found": "secrets/mail.env"})
        H.status_set(c, "human:ana", "cpo", state="active", reason="reviewed")
    note = notes_for(api, "cpo")[-1]
    assert "the safety check stopped a task note naming secrets/mail.env" in note["body"]
    assert note["body"].endswith(H.QUARANTINE_LIFTED)


def test_a_quarantined_bots_manager_bot_writes_to_it_and_it_waits_but_no_other_bot_does(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='ops' WHERE bot='cpo'")
        task = H.task_create(c, "human:ana", "Research imports", "x", "bot:cpo", lint=False)
        H.quarantine(c, "cpo", "10 repeated refusals today", refusal={"rule": "label", "detail": "x"})
    with api.app.state.store.transaction() as c:
        said = H.say(c, "bot:ops", "bot:cpo", "Stop adding the roadmap label; Ben will.", refs={"needs_action": True})
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (said["id"],)).fetchone()[0] == "queued"
        H.note_create(c, "bot:ops", "bot:cpo", "The roadmap label needs an approval.")
        c.execute("UPDATE tasks SET requester_id='bot:ops' WHERE id=?", (task["id"],))
        comment = H.task_comment(c, "bot:ops", task["id"], "Leave the label to a person.")
        assert comment["recipient_id"] == "bot:cpo"
    # Any other bot's message waits too (a refused one quarantined the sender in turn), but its note does not.
    with api.app.state.store.transaction() as c:
        said = H.say(c, "bot:cmo", "bot:cpo", "Are you stuck?", refs={"needs_action": True})
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (said["id"],)).fetchone()[0] == "queued"
        # One that can wait (Dev 149) is kept the same way, for the bot's next run once it is back.
        said = H.say(c, "bot:cmo", "bot:cpo", "FYI: the export finished.")
        assert c.execute("SELECT count(*) FROM notes WHERE message_id=?", (said["id"],)).fetchone()[0] == 1
    with pytest.raises(H.Refused) as got:
        with api.app.state.store.transaction() as c:
            H.note_create(c, "bot:cmo", "bot:cpo", "Are you stuck?")
    assert got.value.rule == "target_inactive"
    with api.app.state.store.transaction() as c:
        assert H.say(c, "human:ben", "bot:cpo", "Are you stuck?"), "a person's message waits, as before"
        assert H.note_create(c, "human:ben", "bot:cpo", "Back soon?")
    # A bot a person paused stays theirs: its manager bot's note is refused, and its message waits for the person
    # to resume it.
    with api.app.state.store.transaction() as c:
        H.status_set(c, "human:ana", "cpo", state="active", reason="reviewed")
        H.status_set(c, "human:ana", "cpo", state="paused", reason="holding it")
    with pytest.raises(H.Refused) as got:
        with api.app.state.store.transaction() as c:
            H.note_create(c, "bot:ops", "bot:cpo", "Start the export work.")
    assert got.value.rule == "target_inactive"
    with api.app.state.store.transaction() as c:
        said = H.say(c, "bot:ops", "bot:cpo", "Start the export work.", refs={"needs_action": True})
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (said["id"],)).fetchone()[0] == "queued"


def test_a_manager_declared_only_in_the_bots_definition_counts_too(api):
    import json
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='cpo'").fetchone()[0])
        c.execute("UPDATE bot_config SET reports_to=NULL, config=? WHERE bot='cpo'",
                  (json.dumps({**config, "reports_to": "ops"}),))
        H.quarantine(c, "cpo", "10 repeated refusals today")
    with api.app.state.store.transaction() as c:
        assert H.note_create(c, "bot:ops", "bot:cpo", "The roadmap label needs an approval.")


def test_quarantine_batch_and_single_read_pick_the_same_newest_event_on_a_tied_time(api):
    """Two quarantine events with the same `ts`: the batch and the single-row read both take the one inserted last."""
    from backend.read_cache import one
    store = api.app.state.store
    with store.transaction() as c:
        for event_id, reason in (("older", "first"), ("newer", "second")):
            c.execute("INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(?,?,?,?,?,?)",
                      (event_id, "2026-11-01T10:00:00Z", "owner", "quarantine", "bot:tied",
                       json.dumps({"reason": reason})))
    with store.read_transaction() as c:
        H.prime_quarantines(c, ["tied"])
        batched = json.loads(one(c, H.QUARANTINE_DETAIL_SQL, ("bot:tied",))["detail"])["reason"]
        single = json.loads(c.execute(H.QUARANTINE_DETAIL_SQL, ("bot:tied",)).fetchone()["detail"])["reason"]
    assert batched == single == "second"
