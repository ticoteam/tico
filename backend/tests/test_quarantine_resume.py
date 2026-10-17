"""A bot back from a quarantine is told the refusal that stopped it, and its manager bot can write to it meanwhile."""

import pytest

from backend import hubdb as H
from backend.tests.test_tasks_board import api, bot_token, headers, post  # noqa: F401 (fixture)


def notes_for(api, slug):
    with api.app.state.store.read() as c:
        return H.notes_waiting(c, slug)


def test_a_lifted_quarantine_leaves_the_bot_a_note_with_the_refusal_that_stopped_it(api):
    token = bot_token(api, "cpo")
    item = post(api, "tasks", {"owner": "cpo", "title": "Research exports", "body": "x"})
    for _ in range(H.QUARANTINE_AT + 1):
        r = api.post("/api/v2/tasks/" + item["id"], json={"version": item["version"], "number": 993}, headers=headers(token))
        assert r.status_code == 403, r.text
    with api.app.state.store.read() as c:
        assert H.bot(c, "cpo")["state"] == "quarantined"
    assert notes_for(api, "cpo") == [], "nothing until it is back"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE events SET ts=? WHERE action='quarantine'", (H.shift(H.now(), seconds=-3700),))
        assert H.lift_cooled_quarantines(c) == ["cpo"]
    [note] = notes_for(api, "cpo")
    assert note["from_actor"] == H.KEEPER
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
        said = H.say(c, "bot:ops", "bot:cpo", "Stop adding the roadmap label; Ben will.")
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (said["id"],)).fetchone()[0] == "queued"
        H.note_create(c, "bot:ops", "bot:cpo", "The roadmap label needs an approval.")
        c.execute("UPDATE tasks SET requester='bot:ops' WHERE id=?", (task["id"],))
        comment = H.task_comment(c, "bot:ops", task["id"], "Leave the label to a person.")
        assert comment["to_actor"] == "bot:cpo"
    for write in (lambda c: H.say(c, "bot:cmo", "bot:cpo", "Are you stuck?"),
                  lambda c: H.note_create(c, "bot:cmo", "bot:cpo", "Are you stuck?")):
        with pytest.raises(H.Refused) as got:
            with api.app.state.store.transaction() as c:
                write(c)
        assert got.value.rule == "reach"
    with api.app.state.store.transaction() as c:
        assert H.say(c, "human:ben", "bot:cpo", "Are you stuck?"), "a person's message waits, as before"
        assert H.note_create(c, "human:ben", "bot:cpo", "Back soon?")
