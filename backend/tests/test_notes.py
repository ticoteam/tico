"""Quiet notes: something for a bot's next run to know, asking nothing.

Ben, 2026-09-24: each monitor leaves the Product Manager a note after its check, the notes
stack up without waking it, and its daily report, which runs after every monitor has checked,
reads them all in one prompt, each with the time it was sent.
"""

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
