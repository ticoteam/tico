"""The Bookkeeper's Books check and Watch are private tasks, and its Finance actions made from them are admitted
(bookkeeper.private_finance_run, task_privacy.guard_write). Synthetic fixtures only: Acme's Mercury feed, fictional
people ben (no Finance access) and ana (the owner).

Every other private run keeps its refusal: a run carrying anything besides the Books check or Watch, a task of the
Bookkeeper's that a person filed, and every other write path of Finance. A refused write says so in one plain sentence;
the row stays in the queue for the next check, and nothing asks a person to reissue, publish or make it visible."""

import pytest

from backend import bookkeeper as BK
from backend.store import H
from backend.tests.test_api import api, headers  # noqa: F401
from backend.tests.test_finance_bookkeeper_turns import _library_file, ask, chart, hire, mercury
from backend.tests.test_finance_fixtures import OWNER
from backend.tests.test_mcp import call

PRIVACY = "Private task work cannot publish into company-wide resources"


def private_run(api, *, carried=("books",), extra=None, feed=True):
    """The Bookkeeper's run after its hire, carrying the system's own private work: a Books check (`deliver`) and/or a
    Watch task, plus `extra` tasks named by (title, requester, owner). Returns (token, task ids by kind, attempt id)."""
    token = hire(api)
    made = {}
    with api.app.state.store.transaction() as c:
        (attempt,) = [r[0] for r in c.execute("SELECT id FROM attempts WHERE bot_id=?", (BK.SLUG,)).fetchall()]
        if "books" in carried:
            made["books"] = BK.deliver(c, "Books check: 1 item.", 1)[0]
        if "watch" in carried:
            made["watch"] = BK.deliver(c, "Watch: 1 finding.", 1, name=BK.WATCH_TITLE, event=BK.WATCH_EVENT)[0]
        for i, (title, requester, owner) in enumerate(extra or ()):
            made[f"extra{i}"] = H.task_create(c, requester, title, "Private work.", owner, private=True, lint=False)[
                "id"
            ]
        for tid in made.values():
            c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (attempt, tid))
    if feed:
        mercury(api)
    return token, made, attempt


def post_finance(api, token, kind, payload, key):
    return api.post(
        "/api/v2/finance/actions",
        json={"kind": kind, "payload": payload},
        headers=headers(token, key=key),
    )


def deposit_feed_id(api, token):
    err, queue = call(api, "hub_finance_inbox", {"kind": "uncategorized"}, token=token)
    assert not err, queue
    return next(r for r in queue["items"] if r["label"].startswith("Acme Customer"))["feed_id"]


# ----------------------------------------------------------------------------- the writes the Books check makes
def test_a_books_check_run_categorizes_a_row_and_records_a_receipt_match(api):
    token, made, _ = private_run(api)
    feed_id = deposit_feed_id(api, token)
    err, posted = ask(
        api, token, "categorize", {"feed_id": feed_id, "account_id": chart(api, "4000")}, f"cat:{feed_id}"
    )
    assert not err and posted["status"] == "committed", posted
    err, left = call(api, "hub_finance_inbox", {"kind": "uncategorized"}, token=token)
    assert not err and feed_id not in {r["feed_id"] for r in left["items"]}, left

    err, found = call(api, "hub_finance_search", {"kind": "feed", "q": "Mercury"}, token=token)
    fee = next(r for r in found["results"] if r["provider_kind"] == "wireFee")
    _library_file(api, "file-receipt", "bookkeeper")
    err, matched = ask(
        api,
        token,
        "attach",
        {"subject_kind": "feed", "subject_id": fee["id"], "file_ids": ["file-receipt"]},
        f"attach:{fee['id']}",
    )
    assert not err and matched["status"] == "committed", matched
    assert len(matched["result"]["attachment_ids"]) == 1, matched


def test_a_watch_run_writes_finance_and_the_action_is_the_bookkeepers(api):
    token, made, _ = private_run(api, carried=("watch",))
    feed_id = deposit_feed_id(api, token)
    err, posted = ask(
        api, token, "categorize", {"feed_id": feed_id, "account_id": chart(api, "4000")}, f"cat:{feed_id}"
    )
    assert not err and posted["status"] == "committed", posted
    with api.app.state.store.read() as c:
        actor = c.execute("SELECT actor FROM fin_actions WHERE id=?", (posted["action_id"],)).fetchone()[0]
    assert actor == BK.ACTOR, actor


@pytest.mark.parametrize("carried", [("books",), ("watch",), ("books", "watch")])
def test_a_finance_write_from_its_own_private_work_leaves_the_task_private(api, carried):
    token, made, _ = private_run(api, carried=carried)
    feed_id = deposit_feed_id(api, token)
    with api.app.state.store.read() as c:
        before = {tid: (H.task(c, tid)["private"], H.task(c, tid)["visibility"]) for tid in made.values()}
    err, posted = ask(
        api, token, "categorize", {"feed_id": feed_id, "account_id": chart(api, "4000")}, f"cat:{feed_id}"
    )
    assert not err and posted["status"] == "committed", posted
    with api.app.state.store.read() as c:
        after = {tid: (H.task(c, tid)["private"], H.task(c, tid)["visibility"]) for tid in made.values()}
        assert all(H.task_private(c, H.task(c, tid)) for tid in made.values())
    assert after == before


def test_a_person_without_finance_access_does_not_see_the_books_check_or_watch(api):
    token, made, _ = private_run(api, carried=("books", "watch"))
    for who in ("ben-test", "cara-test"):
        listed = api.get("/api/v2/tasks", headers=headers(who))
        assert listed.status_code == 200, listed.text
        for title in (BK.TITLE, BK.WATCH_TITLE):
            assert f"{title} (" not in listed.text, (who, title)
        for tid in made.values():
            assert tid not in listed.text, (who, tid)
        searched = api.get("/api/v2/tasks", params={"q": "Books check"}, headers=headers(who))
        assert searched.status_code == 200 and "Books check (" not in searched.text, (who, searched.text)
        assert all(tid not in searched.text for tid in made.values()), who


def _asks_and_tasks(api):
    """What a refusal must leave alone: the task rows, the approvals a person decides, and the messages a person reads."""
    with api.app.state.store.read() as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("tasks", "approvals", "messages")}


def test_a_refusal_for_another_reason_is_one_plain_sentence_and_the_row_stays(api):
    token, made, _ = private_run(api, carried=("books", "watch"))
    feed_id = deposit_feed_id(api, token)
    before = _asks_and_tasks(api)
    refused = post_finance(
        api, token, "categorize", {"feed_id": feed_id, "account_id": "acc-no-such-account"}, "cat:bad-account"
    )
    assert refused.status_code == 200, refused.text
    out = refused.json()
    assert out["status"] == "failed" and out.get("reason"), out
    assert PRIVACY not in str(out)
    reason = out["reason"]
    assert isinstance(reason, str) and "\n" not in reason and len(reason) <= 200, reason
    err, left = call(api, "hub_finance_inbox", {"kind": "uncategorized"}, token=token)
    assert not err and feed_id in {r["feed_id"] for r in left["items"]}, left
    assert _asks_and_tasks(api) == before, "a refusal files no task, asks for no approval and sends no message"
    with api.app.state.store.read() as c:
        for tid in made.values():
            row = H.task(c, tid)
            assert row["status"] not in ("done", "closed", "declined"), dict(row)
            assert H.task_private(c, row), dict(row)


def test_a_month_close_from_its_own_private_run_waits_for_the_owner(api):
    token, made, _ = private_run(
        api, feed=False
    )  # as in the close turn: a close is asked for, nothing else is in the books
    err, asked = ask(api, token, "close_period", {"month": "2026-10"}, "close:2026-10")
    assert not err and asked["status"] == "needs_approval", asked
    assert asked["approver"] == OWNER, asked
    decided = api.post(
        f"/api/v2/approvals/{asked['approval_id']}",
        json={"decision": "approved"},
        headers=headers(token),
    )
    assert decided.status_code == 403, decided.text  # a bot never decides an approval
    with api.app.state.store.read() as c:
        row = c.execute("SELECT status, approval_id FROM fin_actions WHERE id=?", (asked["action_id"],)).fetchone()
    assert tuple(row) == ("needs_approval", asked["approval_id"]), tuple(row)


def test_other_finance_writes_from_the_books_check_stay_refused(api):
    token, made, _ = private_run(api)
    refused = api.put(
        "/api/v2/finance/bills-address",
        json={"address": "bills@acme.example"},
        headers=headers(token),
    )
    assert refused.status_code == 403 and PRIVACY in refused.text, refused.text


@pytest.mark.parametrize(
    "extra",
    [
        [("Ben's private note", "human:ben", "human:ana")],  # a second private task the run carries
        [("Books check (9)", "human:ben", BK.ACTOR)],  # named like one, but a person filed it
    ],
)
def test_a_run_carrying_other_private_work_keeps_the_refusal(api, extra):
    token, made, _ = private_run(api, extra=extra)
    feed_id = deposit_feed_id(api, token)
    refused = post_finance(api, token, "categorize", {"feed_id": feed_id, "account_id": chart(api, "4000")}, "cat:x")
    assert refused.status_code == 403 and PRIVACY in refused.text, refused.text


def test_an_open_books_check_takes_the_next_list_and_no_new_task_is_filed(api):
    token, made, _ = private_run(api)
    store = api.app.state.store
    with store.transaction() as c:
        again = BK.deliver(c, "Books check: 2 items.", 2)
    assert again == (made["books"], "updated"), again
    with store.read() as c:
        open_books = c.execute(
            "SELECT COUNT(*) FROM tasks WHERE owner_id=? AND title LIKE 'Books check (%' AND status NOT IN ('done','closed','declined')",
            (BK.ACTOR,),
        ).fetchone()[0]
    assert open_books == 1
