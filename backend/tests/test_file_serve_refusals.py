"""GET /api/v2/files/{fid}/versions/{n} answers one 404 for seven different refusals (Files.serve). Each now logs its
check, so an intermittent "File not found" can be explained afterwards; the answer to the caller stays the same.

A page is also read at once after a bot publishes it from inside a live run that carries a requester's input, while
the run is running, after it ends with an input handed back, and after the next run takes that input over: the run's
tasks and inputs move in exactly that window, and a fence over them is the likeliest cause of a page that reads as
missing just after publishing.
"""

import logging

import pytest

from backend import postal_mail
from backend import task_privacy as privacy
from backend import files as F
from backend.enqueue import enqueue_stored
from backend.store import H, encode
from backend.tests.helpers.api import assign, claim, headers, post, ready, restrict, runner, setup_attempt
from backend.tests.helpers.counsel import store_file
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.pages import page

OWNER, REQUESTER = "ana-test", "cara-test"


def version(api, fid, number, token=OWNER):
    return api.get(f"/api/v2/files/{fid}/versions/{number}", headers=headers(token))


def refusals(caplog):
    return [r.file_refusal for r in caplog.records if r.name == "tico.files" and hasattr(r, "file_refusal")]


def refused(api, caplog, fid, number, token, category, message="File not found"):
    """The read answers the unchanged 404, and exactly one log line names `category`."""
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tico.files"):
        answer = version(api, fid, number, token)
    assert answer.status_code == 404, answer.text
    assert message in answer.text
    logged = refusals(caplog)
    assert [line["category"] for line in logged] == [category], logged
    return logged[0]


def published(api, attempt, name="weekly.html", **data):
    made = page(api, attempt, name=name, **data)
    assert made.status_code == 200, made.text
    return made.json()["page"]["file_id"]


def test_each_refusal_logs_its_check_and_answers_the_same_404(api, caplog, monkeypatch):
    machine, _, ops = setup_attempt(api)
    fid = published(api, ops)
    assert version(api, fid, 1).status_code == 200

    line = refused(api, caplog, "file-that-was-never-made", 1, OWNER, "no_row")
    assert line == {"category": "no_row", "file": "file-that-was-never-made", "version": 1} | {
        "actor": "human:ana",
        "role": "owner",
    }

    refused(api, caplog, fid, 99, OWNER, "no_version", "This file has no stored copy to open")

    # A page whose run Cara has no part in: its blob is fenced by the page's own visibility, and the line names the
    # run that made it and whether that run is still live.
    line = refused(api, caplog, fid, 1, REQUESTER, "blob_fence")
    assert (line["actor"], line["role"], line["attempt"]) == ("human:cara", "human", ops["id"])
    assert (line["attempt_state"], line["attempt_running"]) == ("leased", True)
    # Cara may read the run itself, so the line also says the run is not what fenced her: the page's scope did.
    assert line["attempt_readable"] is True
    # Once the run has ended, the same refusal says so: a live-run refusal and a later one read apart.
    post(api, f"attempts/{ops['id']}/started", {"thread_id": "t"}, machine["token"])
    post(
        api,
        f"attempts/{ops['id']}/complete",
        {"outcome": "completed", "text": "Published.", "last_seq": 0},
        machine["token"],
    )
    line = refused(api, caplog, fid, 1, REQUESTER, "blob_fence")
    assert (line["attempt"], line["attempt_state"], line["attempt_running"]) == (ops["id"], "completed", False)

    # Not a page: the file's own visibility refuses it (a bot whose files only Ana may read).
    upload, number = store_file(api, "A private synthetic note.", title="Private note", bot="finance")
    with api.app.state.store.transaction() as c:
        restrict(c, "finance", people=["ana"])
    line = refused(api, caplog, upload, number, REQUESTER, "file_fence")
    assert "attempt" not in line  # a person's upload was made by no run

    # The page's audience narrows it to Ben.
    edit(api, fid, {"people": ["ben"]}, token=OWNER)
    refused(api, caplog, fid, 1, REQUESTER, "audience")
    edit(api, fid, {"everyone": True}, token=OWNER)

    # A postal scan's own grant refuses it (postal tests cover who holds one; here only the log line).
    monkeypatch.setattr(postal_mail, "scan_access", lambda *a, **kw: False)
    refused(api, caplog, upload, number, OWNER, "scan")
    monkeypatch.undo()

    # The Rules Owner's read names another version (counsel tests cover the read itself).
    monkeypatch.setattr(F, "asked_to_read", lambda c, who, fid, version=None, **kw: version is None)
    refused(api, caplog, upload, number, OWNER, "rules_read")
    monkeypatch.undo()

    # No line is written for a read that succeeds, and every category is one Files.serve can log.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tico.files"):
        assert version(api, upload, number).status_code == 200
    assert refusals(caplog) == []
    assert set(F.REFUSALS) == {"no_row", "audience", "blob_fence", "scan", "file_fence", "rules_read", "no_version"}


def test_a_refusal_log_line_holds_no_page_text_or_token(api, caplog):
    _, _, ops = setup_attempt(api)
    fid = published(api, ops)
    line = refused(api, caplog, fid, 1, REQUESTER, "blob_fence")
    text = encode(line) + "".join(r.getMessage() for r in caplog.records)
    assert "Acme weekly review" not in text and "42 deals" not in text
    assert ops["token"] not in text and REQUESTER not in text


def input_from_requester(api, cid, mid):
    """A message from Cara, the task's requester, to the ops bot on the task's conversation, with its job queued."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs) "
            "VALUES(?,?,'human:cara','bot:ops','say',?,?,?)",
            (mid, cid, "Synthetic follow-up " + mid, H.now(), encode({})),
        )
        enqueue_stored(c, mid)


@pytest.mark.parametrize("reader", [OWNER, REQUESTER])
def test_a_page_published_inside_a_live_run_reads_at_once_during_and_after_the_run(api, caplog, reader):
    r = runner(api, "ana")
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    work = post(api, "tasks", {"owner": "ops", "title": "Release", "body": "Ship it."}, token=REQUESTER)
    first = claim(api, r)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t"}, r["token"])
    with api.app.state.store.read() as c:
        cid = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (work["id"],)).fetchone()[0]
    # The live run takes two inputs from the requester: it reads one, and the other is still unread when it ends.
    inputs = f"attempts/{first['id']}/inputs"
    input_from_requester(api, cid, "read-input")
    assert [m["id"] for m in post(api, inputs, {}, r["token"])["messages"]] == ["read-input"]
    post(api, f"{inputs}/read-input/ack", {}, r["token"])
    input_from_requester(api, cid, "unread-input")
    assert [m["id"] for m in post(api, inputs, {}, r["token"])["messages"]] == ["unread-input"]

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tico.files"):
        fid = published(api, first)
        # Read at once, while the run that published it is still running.
        assert version(api, fid, 1, reader).status_code == 200, refusals(caplog)
        # The bot reads its own page input while its run is live.
        assert version(api, fid, 1, first["token"]).status_code == 200, refusals(caplog)
        # A second version from the same live run reads too.
        assert (
            published(api, first, data=b"<html><body><h1>Acme weekly review</h1><p>Revised.</p></body></html>") == fid
        )
        assert version(api, fid, 2, reader).status_code == 200, refusals(caplog)
        assert version(api, fid, 2, first["token"]).status_code == 200, refusals(caplog)

        # The run ends and hands its unread input back to the queue.
        post(
            api,
            f"attempts/{first['id']}/complete",
            {"outcome": "completed", "text": "Published.", "last_seq": 0, "undelivered": ["unread-input"]},
            r["token"],
        )
        for number in (1, 2):
            assert version(api, fid, number, reader).status_code == 200, refusals(caplog)

        # The next run takes the handed-back input over as its own job.
        second = claim(api, r)
        post(api, f"attempts/{second['id']}/started", {"thread_id": "t2"}, r["token"])
        with api.app.state.store.read() as c:
            job = c.execute("SELECT job_id FROM attempts WHERE id=?", (second["id"],)).fetchone()[0]
            assert c.execute("SELECT message_id FROM jobs WHERE id=?", (job,)).fetchone()[0] == "unread-input"
        for number in (1, 2):
            assert version(api, fid, number, reader).status_code == 200, refusals(caplog)

        # The input the first run read moves to the next run, as execution's input poll moves one: the first run
        # keeps its tasks through the attempt.input.moved event.
        with api.app.state.store.transaction() as c:
            privacy.record_moved_input(c, first["id"], second["id"], H.message(c, "read-input"))
            c.execute("UPDATE attempt_inputs SET attempt_id=? WHERE message_id='read-input'", (second["id"],))
        for number in (1, 2):
            assert version(api, fid, number, reader).status_code == 200, refusals(caplog)
            # A bot reads a page input as its run (bot role with a task actor), not as a person: the publishing
            # bot's next run reads both versions too.
            assert version(api, fid, number, second["token"]).status_code == 200, refusals(caplog)
    assert refusals(caplog) == []
