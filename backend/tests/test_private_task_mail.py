"""A bot's run on a private task drafts and queues mail from a mailbox it may write, as any run does: the mailbox's
own level decides, and the send waits on the inbox's send gate, whose OK is a private task
of its approver alone. Synthetic fixtures only: ana's inbox, the bot ops, fictional people ben (the private task's
requester) and cara (the named approver)."""

import pytest

from backend import email_sending as S, task_asks
from backend.store import H
from backend.tests.helpers.api import give_ok, headers, post
from backend.tests.helpers.email_sending import BOX, CONTACT, dispatch, inbox, policy  # noqa: F401
from backend.tests.helpers.fake_gmail import fake_gmail  # noqa: F401
from backend.tests.helpers.threads_private_execution import private_run, private_tasks_have_an_unrelated_manager  # noqa: F401

TITLE = "Sylvan Reed: written reply"


@pytest.fixture(autouse=True)
def classified(monkeypatch):
    """The decision model reads each email as neither legal nor money (no model runs in tests)."""
    monkeypatch.setattr(
        S,
        "classify",
        lambda app, store, snap, review=None: {"legal_or_money": False, "review": None, "status": "classified"},
    )


def test_a_private_run_drafts_and_queues_a_send_and_the_ok_goes_only_to_its_approver(inbox):
    policy(inbox, who_can_ok="named_people", approvers=["cara"], undo_seconds=0, pacing_seconds=0)
    row, attempt = private_run(inbox)
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET title=? WHERE id=?", (TITLE, row["id"]))
    token = attempt["token"]

    body = {"mailbox": BOX, "reply_to": "thread", "body": "Thanks for the update. Forwarding it on.\n\nOps"}
    made = post(inbox, "mail/drafts", body, token)
    assert made["created"] and made["to"] == [CONTACT], made
    linted = post(inbox, "mail/lint", body, token)
    assert "errors" in linted, linted

    queued = post(inbox, "email/sends", {"mailbox": BOX, "draft_id": made["draft"]}, token)["send"]
    assert queued["state"] == "waiting_approval" and queued["approval_id"], queued
    assert queued["actor"] == "bot:ops", queued

    with inbox.app.state.store.read() as c:
        ok_task = H.task(c, task_asks.task_of(c, queued["approval_id"]))
        assert H.task_private(c, ok_task) and TITLE not in ok_task["title"], dict(ok_task)
        assert H.task_private(c, H.task(c, row["id"])), "the private task stays private"
        asked = task_asks.grant(c, queued["approval_id"])
        assert asked["asked_of_id"] == "human:cara", dict(asked)

    # The OK is the approver's alone: ben (the private task's requester) and ana (the inbox's person, no approver)
    # neither see its task nor open it.
    for outsider in ("ben-test", "ana-test"):
        seen = inbox.get(f"/api/v2/tasks/{ok_task['id']}", headers=headers(outsider))
        assert seen.status_code in (403, 404), (outsider, seen.text)
        opened = inbox.get(f"/api/v2/oks/{queued['approval_id']}", headers=headers(outsider))
        assert opened.status_code == 404, (outsider, opened.text)
    # Cara opens the frozen email and OKs it; it goes out once.
    assert inbox.get("/api/v2/email/sends/" + queued["id"], headers=headers("cara-test")).status_code == 200
    give_ok(inbox, queued["approval_id"], token="cara-test")
    assert len(dispatch(inbox) + dispatch(inbox)) == 1  # the first pass lets it go, a later one sends it
    with inbox.app.state.store.read() as c:
        assert c.execute("SELECT state FROM email_sends WHERE id=?", (queued["id"],)).fetchone()[0] == "sent"
        assert task_asks.grant(c, queued["approval_id"])["ok_by_id"] == "human:cara"


def test_a_private_run_is_judged_by_the_route_alone_as_any_run_is(inbox):
    """The company sending policy is an admin's (acting as themselves): a run is refused it by the route, not because
    its task is private."""
    _, attempt = private_run(inbox)
    refused = inbox.put("/api/v2/email/sending-policy", json={"mode": "never"}, headers=headers(attempt["token"]))
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] != "privacy", refused.text
