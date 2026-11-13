"""A held email goes out only on an OK bound to its exact frozen copy (backend/email_sending.py, backend/task_asks.py),
on a store with no app: asked of someone who may approve it, given from the preview by their own click, still theirs
to give at the send, used once. A push past, a send back and a redraft send nothing."""

import json

import pytest

from backend import email_sending as S
from backend import mailbox_access as MA
from backend import task_asks as K
from backend.auth import Auth, Identity
from backend.store import H
from backend.tests.helpers.email_sending import BOX, incoming, mime

ANA, BEN = "human:ana", "human:ben"


@pytest.fixture
def store(company):
    with company.transaction() as c:
        row = MA.ensure_mailbox(c, BOX, person_id="ana", created_by=ANA)
        MA.grant(c, ANA, row["id"], "bot", "ops", "read_write")
        incoming(c)
        S.update_policy(c, Identity(ANA, "owner"), "company", S.PolicyUpdate(undo_seconds=0, pacing_seconds=0))
    company.auth = Auth(company)
    return company


def _queue(store, body="Thanks for the update."):
    with store.transaction() as c:
        return S.enqueue(
            c,
            store.auth,
            "bot:ops",
            S.Enqueue(mailbox=BOX, raw=mime(body)),
            {"legal_or_money": False, "status": "classified", "review": True},
        )


def _row(c, sid):
    return c.execute("SELECT * FROM email_sends WHERE id=?", (sid,)).fetchone()


def _pass(store):
    """One pass of the queue, as a computer's claim makes it: reconcile, then lease what is due."""
    with store.transaction() as c:
        return S._lease(c, store.auth, BOX, "r1")


def _ok(store, queued, person=ANA, own=True):
    """The person opens the email's preview and clicks OK on it, as the routes do."""
    who = Identity(person, "human") if own else Identity(person, "human", via="assistant", confirmed=True)
    with store.transaction() as c:
        row = _row(c, queued["id"])
        shown = S.shown_to(c, who, row)
        K.give_ok(c, person, row["approval_id"], shown, own_click=K.own_click(who), ctx=K.Context(auth=store.auth))


def test_a_held_email_asks_for_an_ok_on_a_task_of_its_own(store):
    queued = _queue(store)
    assert queued["state"] == "waiting_approval" and "ask_first" in queued["decision"]["reasons"]
    with store.read() as c:
        rid = queued["approval_id"]
        grant = K.grant(c, rid)
        assert (grant["subject"], grant["asked_by_id"]) == ("email_send:" + queued["id"], "bot:ops")
        task = H.task(c, K.task_of(c, rid))
        # Private to its parties, titled for the act only: the email's subject line stays in its preview.
        assert (task["owner_id"], task["title"], bool(task["private"])) == ("bot:ops", "Send an email", True)
        ask = H.task_view(c, task["id"], ANA)["to_leave"][-1]
        assert (ask["kind"], ask["who"], ask["shows"]) == ("ok", ANA, ["email:" + queued["id"]])
    assert _pass(store) is None
    with store.read() as c:
        assert _row(c, queued["id"])["state"] == "waiting_approval"


def test_the_ok_from_the_preview_sends_it_once_and_finishes_its_task(store):
    queued = _queue(store)
    _ok(store, queued)
    assert _pass(store) is None  # this pass sees the OK: the email is due after its undo window
    leased = _pass(store)
    assert leased["id"] == queued["id"]
    with store.transaction() as c:
        row = _row(c, queued["id"])
        found = S.decision(c, store.auth, row, dispatch=True)
        assert found["approved"]
        S._permit(c, row, found)
        assert K.standing(c, queued["approval_id"]) == K.USED
        task_id = K.task_of(c, queued["approval_id"])
        assert H.task(c, task_id)["status"] == "open"  # claimed: done only once the provider took it
        S._finish(c, row, "sent", "msg-1")
        assert H.task(c, task_id)["status"] == "done"
        with pytest.raises(H.Refused, match="consumed"):
            K.use(c, queued["approval_id"], "email_send:" + queued["id"], S.content(row), "bot:ops")


def test_an_assistant_or_a_token_is_not_the_persons_ok(store):
    queued = _queue(store)
    with pytest.raises(H.Refused, match="identity"):
        _ok(store, queued, own=False)
    with store.read() as c:
        assert K.standing(c, queued["approval_id"]) == K.WAITING


def test_someone_who_may_not_approve_this_inbox_cannot_ok_it(store):
    queued = _queue(store)
    with store.transaction() as c:
        task_id = K.task_of(c, queued["approval_id"])
        # The ask's task is private: it goes to Ben only once someone who sees it lets Ben see it too.
        with pytest.raises(H.Refused, match="Ben can't see this private task"):
            H.task_give(c, ANA, task_id, queued["approval_id"], BEN)
    with store.transaction() as c:
        H.set_task_readers(c, "bot:ops", task_id, add=[BEN])
        H.task_give(c, ANA, task_id, queued["approval_id"], BEN)
    with pytest.raises(Exception, match="approval rule"):
        _ok(store, queued, person=BEN)


def test_an_ok_whose_giver_may_no_longer_approve_does_not_send_and_is_asked_again(store):
    queued = _queue(store)
    _ok(store, queued)
    with store.transaction() as c:
        # Only Ben approves this company's sends now.
        rule = S.PolicyUpdate(who_can_ok="named_people", approvers=["ben"])
        S.update_policy(c, Identity(ANA, "owner"), "company", rule)
        assert not S.approved(c, store.auth, _row(c, queued["id"]), S.effective(c, BOX, "ops"))
    assert _pass(store) is None
    with store.read() as c:
        row = _row(c, queued["id"])
        assert row["state"] == "waiting_approval" and row["approval_id"] != queued["approval_id"]
        assert K.standing(c, queued["approval_id"]) == K.WITHDRAWN
        assert H.task_view(c, K.task_of(c, row["approval_id"]), BEN)["to_leave"][-1]["who"] == BEN


def test_a_push_past_its_task_sends_nothing(store):
    queued = _queue(store)
    with store.transaction() as c:
        task_id = K.task_of(c, queued["approval_id"])
        version = H.task(c, task_id)["version"]
        H.task_move(c, "bot:ops", task_id, H.TASK_NEXT, "Ana is away and it is urgent.", version)
    assert _pass(store) is None
    with store.read() as c:
        row = _row(c, queued["id"])
        assert row["state"] == "blocked" and json.loads(row["decision"])["reasons"] == ["approval_withdrawn"]


def test_a_send_back_blocks_it_and_closes_its_task(store):
    queued = _queue(store)
    with store.transaction() as c:
        task_id = K.task_of(c, queued["approval_id"])
        H.task_answer(c, ANA, task_id, queued["approval_id"], "no", "Say the amount.")
    assert _pass(store) is None
    with store.read() as c:
        row = _row(c, queued["id"])
        assert row["state"] == "blocked" and json.loads(row["decision"])["reasons"] == ["approval_declined"]
        assert H.task(c, task_id)["status"] == "closed"


def test_an_ok_of_one_email_lets_no_other_email_go(store):
    first = _queue(store)
    _ok(store, first)
    second = _queue(store, body="A different reply.")
    with store.transaction() as c:
        c.execute("UPDATE email_sends SET approval_id=? WHERE id=?", (first["approval_id"], second["id"]))
        assert not S.approved(c, store.auth, _row(c, second["id"]), S.effective(c, BOX, "ops"))
