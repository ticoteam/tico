"""The Bookkeeper's Books check and Watch are private tasks, and its Finance actions made from them are admitted
(bookkeeper.private_finance_run, task_privacy.guard_write). Synthetic fixtures only: Acme's Mercury feed, fictional
people ben (no Finance access) and ana (the owner).

Every other private run keeps its refusal: a run carrying anything besides the Books check or Watch, a task of the
Bookkeeper's that a person filed, and every other write path of Finance. A refused write says so in one plain sentence;
the row stays in the queue for the next check, and nothing asks a person to reissue, publish or make it visible."""

import pytest

from backend import bookkeeper as BK
from backend.store import H
from backend.tests.helpers.api import headers  # noqa: F401
from backend.tests.helpers.finance_bookkeeper_turns import _library_file, ask, chart, hire, mercury  # noqa: F401
from backend.tests.helpers.finance_fixtures import OWNER  # noqa: F401
from backend.tests.helpers.mcp import call  # noqa: F401

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
        for tid in made.values():  # the run carries each task, as the engine records it
            H.event(c, "system", "task.next-run.carried", attempt, {"task_id": tid})
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
    err, queue = call(api, "tico_finance_inbox", {"kind": "uncategorized"}, token=token)
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
    err, left = call(api, "tico_finance_inbox", {"kind": "uncategorized"}, token=token)
    assert not err and feed_id not in {r["feed_id"] for r in left["items"]}, left

    err, found = call(api, "tico_finance_search", {"kind": "feed", "q": "Mercury"}, token=token)
    fee = next(r for r in found["results"] if r["provider_kind"] == "wireFee")
    _library_file(api, "file-receipt", "finance-owner")
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
    """What a refusal must leave alone: the task rows, the asks a person answers, and the messages a person reads."""
    with api.app.state.store.read() as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("tasks", "requirements", "messages")}


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
    err, left = call(api, "tico_finance_inbox", {"kind": "uncategorized"}, token=token)
    assert not err and feed_id in {r["feed_id"] for r in left["items"]}, left
    assert _asks_and_tasks(api) == before, "a refusal files no task, asks for no approval and sends no message"
    with api.app.state.store.read() as c:
        for tid in made.values():
            row = H.task(c, tid)
            assert row["status"] == "open", dict(row)
            assert H.task_private(c, row), dict(row)


def test_the_refusal_is_noted_on_the_task_in_one_sentence_and_nobody_is_asked(api):
    """The Bookkeeper's follow-up after a refused write, scripted in the order AGENTS.md's Each Books check gives it: the
    row stays, one plain sentence goes on the task, and the run goes on. The steps are scripted, not a live model, so
    this pins what the product allows and what those steps leave behind; a model's wording is not tested here."""
    token, made, _ = private_run(api, carried=("books",))
    feed_id = deposit_feed_id(api, token)
    before = _asks_and_tasks(api)
    refused = post_finance(
        api, token, "categorize", {"feed_id": feed_id, "account_id": "acc-no-such-account"}, "cat:bad-account"
    )
    reason = refused.json()["reason"]
    sentence = f"The categorize was refused ({reason}), so the row stays for the next check."
    err, noted = call(api, "tico_task_comment", {"id": made["books"], "text": sentence}, token=token)
    assert not err, noted

    err, shown = call(api, "tico_task_show", {"id": made["books"]}, token=token)
    assert not err, shown
    notes = [c["body"] for c in shown["comments"] if c["sender_id"] == BK.ACTOR]
    assert notes == [sentence], notes
    assert "\n" not in sentence and sentence.count(".") == 1 and sentence.endswith("."), sentence
    assert not any(word in sentence.lower() for word in ("reissue", "publish", "resubmit")), sentence

    # Nothing asks a person to reissue or publish it: the bot may not make its task company-visible.
    err, widened = call(
        api,
        "tico_task_update",
        {"id": made["books"], "private": False, "version": shown["task"]["version"]},
        token=token,
    )
    assert err and widened.get("error") == "privacy", widened
    with api.app.state.store.read() as c:
        assert H.task_private(c, H.task(c, made["books"])), "the Books check stays private"
        assert H.task(c, made["books"])["status"] == "open"
    after = _asks_and_tasks(api)
    assert after["tasks"] == before["tasks"] and after["requirements"] == before["requirements"], (before, after)
    assert after["messages"] == before["messages"] + 1, "only the note itself is written: no message to anyone"
    err, left = call(api, "tico_finance_inbox", {"kind": "uncategorized"}, token=token)
    assert not err and feed_id in {r["feed_id"] for r in left["items"]}, left


@pytest.mark.parametrize("carried", [("books",), ("watch",)])
def test_a_private_run_never_ignores_a_row(api, carried):
    """Ignoring a feed row reverses its entry, so a row a payment paid would return to owed: from a private run the
    categorize is refused when it ignores, and the row stays in the queue."""
    token, made, _ = private_run(api, carried=carried)
    feed_id = deposit_feed_id(api, token)
    refused = post_finance(api, token, "categorize", {"feed_id": feed_id, "ignore": True}, f"ignore:{feed_id}")
    assert refused.status_code == 403 and BK.PRIVATE_FINANCE_REFUSAL in refused.text, refused.text
    err, left = call(api, "tico_finance_inbox", {"kind": "uncategorized"}, token=token)
    assert not err and feed_id in {r["feed_id"] for r in left["items"]}, left


def test_a_month_close_from_its_own_private_run_is_refused_and_no_approval_is_asked(api):
    """A close is the month-ready task's work, not the Books check's: from a private run it is refused like the other
    money kinds, so no approval is asked and the Bookkeeper never decides one (the owner approves a close the close turn
    asks for, covered by test_close_turn)."""
    token, made, _ = private_run(api, feed=False)
    with api.app.state.store.read() as c:
        asks = c.execute("SELECT COUNT(*) FROM requirements").fetchone()[0]
    refused = post_finance(api, token, "close_period", {"month": "2026-10"}, "close:2026-10")
    assert refused.status_code == 403 and BK.PRIVATE_FINANCE_REFUSAL in refused.text, refused.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM requirements").fetchone()[0] == asks
        assert c.execute("SELECT COUNT(*) FROM fin_actions WHERE kind='close_period'").fetchone()[0] == 0


def test_the_bills_address_write_from_the_books_check_stays_refused(api):
    """Another Finance route (not the action route) keeps the privacy refusal from a private Books check run."""
    token, made, _ = private_run(api)
    refused = api.put(
        "/api/v2/finance/bills-address",
        json={"address": "bills@acme.example"},
        headers=headers(token),
    )
    assert refused.status_code == 403 and PRIVACY in refused.text, refused.text


MONEY_KINDS = [
    ("bill_approve", {"bill_id": "bill-x"}),  # a bill goes to payables and spends its budget
    ("payment_create", {"bill_id": "bill-x", "amount": 100}),  # a payment out, approval only above a limit
    ("card_create", {"amount": 100}),  # a single-use card for a bot
]


@pytest.mark.parametrize("carried", [("books",), ("watch",), ("books", "watch")])
@pytest.mark.parametrize("kind,payload", MONEY_KINDS)
def test_a_private_run_makes_no_money_action_and_no_row_is_written(api, carried, kind, payload):
    """The exemption admits categorize and attach only: a money kind from the Books check or Watch is refused on the
    action route, with the one plain sentence, and writes no action row."""
    token, made, _ = private_run(api, carried=carried)
    with api.app.state.store.read() as c:
        before = c.execute("SELECT COUNT(*) FROM fin_actions").fetchone()[0]
    refused = post_finance(api, token, kind, payload, f"money:{kind}")
    assert refused.status_code == 403 and BK.PRIVATE_FINANCE_REFUSAL in refused.text, (kind, refused.text)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_actions").fetchone()[0] == before, kind
        for tid in made.values():
            assert H.task_private(c, H.task(c, tid)), tid


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
            "SELECT COUNT(*) FROM tasks WHERE owner_id=? AND title LIKE 'Books check (%' AND status='open'",
            (BK.ACTOR,),
        ).fetchone()[0]
    assert open_books == 1
