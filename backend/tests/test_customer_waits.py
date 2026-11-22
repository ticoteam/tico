from datetime import datetime, timezone

import pytest

from backend import never_stuck, stage_rules, task_wakes
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import post

OWNER = "bot:builder"
CUSTOMER = "ext:email:customer"


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(datetime(2030, 1, 7, tzinfo=timezone.utc), tick=False)
    with company.transaction() as conn:
        X.bot(conn, "builder")
        yield conn


def _thread(c, parent=None, participants=(X.ANA, CUSTOMER)):
    tid = H.new_id()
    root, path = (
        c.execute("SELECT root_id,path FROM conversations WHERE id=?", (parent,)).fetchone() if parent else (tid, "/")
    )
    c.execute(
        "INSERT INTO conversations(id,kind,participants,parent_id,root_id,path) VALUES(?,'chat',?,?,?,?)",
        (tid, H._dump(participants), parent, root, path + tid + "/"),
    )
    return tid


def _message(c, thread, sender, *, inbound=False, connection=None, provider_id=None):
    mid = H.new_id()
    columns = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
    provenance = {
        "channel": "email",
        "direction": "inbound" if sender.startswith("ext:") else "outbound",
        "connection_id": connection if connection is not None else "",
        "source_message_id": provider_id or mid,
    }
    extra = {name: value for name, value in provenance.items() if name in columns}
    c.execute(
        "INSERT INTO messages(id,conversation_id,sender_id,kind,body,refs,created_at"
        + "".join(f",{name}" for name in extra)
        + ") VALUES(?,?,?,'say','Hello','{}',?"
        + ",?" * len(extra)
        + ")",
        (mid, thread, sender, H.now(), *extra.values()),
    )
    if inbound:
        E.customer_replied(c, mid)
    return H.message(c, mid)


def _park(c, task, thread, sent):
    return E.snooze(
        c,
        task["id"],
        OWNER,
        "Waiting for their reply",
        H.shift(H.now(), days=5),
        task["version"],
        customer_wait=(thread, CUSTOMER, sent["seq"]),
    )


@pytest.mark.parametrize("subthread", [False, True], ids=["same-thread", "subthread"])
def test_reply_wakes_the_turn_once_without_spending_a_try(c, subthread):
    task = X.new_task(c, OWNER, "Check with customer")
    tid = task["id"]
    thread = _thread(c)
    sent = _message(c, thread, OWNER)
    c.execute("UPDATE visits SET tries=3,level='manager' WHERE id=?", (task["visit"]["id"],))
    parked = _park(c, task, thread, sent)
    assert not task_wakes.queued(c, tid, OWNER)

    reply = _message(c, _thread(c, thread) if subthread else thread, CUSTOMER, inbound=True)
    E.customer_replied(c, reply["id"])

    fresh = E.task(c, tid)
    assert fresh["snooze"] is None
    assert (fresh["visit"]["tries"], fresh["visit"]["level"]) == (0, "owner")
    assert task_wakes.queued(c, tid, OWNER)
    wakes = c.execute("SELECT body FROM messages WHERE wake_key LIKE ?", (f"{tid}:%:reply:%",)).fetchall()
    assert len(wakes) == 1 and "A customer replied" in wakes[0][0]
    assert c.execute("SELECT ended_at FROM snoozes WHERE id=?", (parked["snooze"]["id"],)).fetchone()[0]


def test_wrong_sender_sibling_thread_and_earlier_reply_leave_the_wait(c):
    task = X.new_task(c, OWNER, "Check with customer")
    root = _thread(c)
    thread = _thread(c, root)
    earlier = _message(c, thread, CUSTOMER)
    parked = _park(c, task, thread, _message(c, thread, OWNER))

    E.customer_replied(c, earlier["id"])
    _message(c, thread, "ext:email:other", inbound=True)
    _message(c, _thread(c, root), CUSTOMER, inbound=True)

    assert E.task(c, task["id"])["snooze"]["id"] == parked["snooze"]["id"]
    assert not task_wakes.queued(c, task["id"], OWNER)


def test_filed_task_ending_and_met_stage_leave_the_wait_until_fallback(c, time_machine):
    X.pipeline(c, "service", "Service", X.GENERAL + ({"name": "Next", "owner": "assignee"},))
    parent = X.new_task(c, OWNER, "Check with customer", pipeline_id="service")
    child = X.new_task(c, OWNER, "Look up the order", parent=parent["id"])
    thread = _thread(c)
    parked = _park(c, E.task(c, parent["id"]), thread, _message(c, thread, OWNER))

    E.move(c, child["id"], E.CLOSED, OWNER, "Order found", child["version"])
    assert E.task(c, parent["id"])["snooze"]["id"] == parked["snooze"]["id"]
    X.say_done(c, E.task(c, parent["id"]), by=OWNER)
    never_stuck.check(c)
    assert E.task(c, parent["id"])["snooze"]["id"] == parked["snooze"]["id"]
    assert E.task(c, parent["id"])["stage_id"] == parent["stage_id"]

    time_machine.shift(5 * 24 * 60 * 60 + 1)
    assert never_stuck.check(c)["snoozes_ended"] == 1
    assert E.task(c, parent["id"])["snooze"] is None


def test_more_than_three_customer_waits_ignore_bot_snooze_rules_and_limit(c):
    stage_rules.add(
        c,
        X.ANA,
        {
            "name": "Short snoozes",
            "event": "bot_snoozes",
            "cond": {"snooze_hours_over": 48},
            "then": "block",
        },
    )
    task = X.new_task(c, OWNER, "Follow up")
    thread = _thread(c)
    for _ in range(5):
        fresh = E.task(c, task["id"])
        assert _park(c, fresh, thread, _message(c, thread, OWNER))["snooze"]
        _message(c, thread, CUSTOMER, inbound=True)

    assert E.task(c, task["id"])["visit"]["bot_snoozes"] == 0
    with pytest.raises(H.Refused, match="Short snoozes"):
        E.snooze(c, task["id"], OWNER, "Check later", H.shift(H.now(), days=5), task["version"])


def test_snooze_refuses_a_reply_that_committed_first_and_requires_a_fallback(api):
    store = api.app.state.store
    with store.transaction() as conn:
        X.bot(conn, "builder")
        task = X.new_task(conn, X.ANA, "Follow up", assignee=OWNER)
        thread = _thread(conn)
        sent = _message(conn, thread, OWNER)
        reply_thread = _thread(conn, thread)
    path = f"tasks/{task['id']}/snooze"
    wait = {"wait_thread_id": thread, "wait_sender": CUSTOMER, "wait_after_seq": sent["seq"]}
    post(api, path, wait, expected=422)
    with store.transaction() as conn:
        _message(conn, reply_thread, CUSTOMER, inbound=True)
    wait["until"] = H.shift(H.now(), days=1)
    post(api, path, wait, token="cara-test", expected=404)
    response = post(api, path, wait, expected=409)
    assert response["error"] == "task_customer_replied"
    with store.read() as conn:
        assert E.task(conn, task["id"])["snooze"] is None
        assert task_wakes.queued(conn, task["id"], OWNER)


def test_provider_anchor_resolves_in_the_threads_inbox_and_wakes_in_the_commit(api):
    store = api.app.state.store
    with store.transaction() as conn:
        # PR9 supplies provider provenance. The original schema can still use sequence anchors.
        for table, fields in (
            ("conversations", {"connection_id": "TEXT NOT NULL DEFAULT ''"}),
            (
                "messages",
                {"connection_id": "TEXT NOT NULL DEFAULT ''", "source_message_id": "TEXT", "direction": "TEXT"},
            ),
        ):
            columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, definition in fields.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        X.bot(conn, "builder")
        task = X.new_task(conn, X.ANA, "Follow up", assignee=OWNER)
        thread = _thread(conn)
        conn.execute("UPDATE conversations SET connection_id='sales' WHERE id=?", (thread,))
        _message(conn, thread, OWNER, connection="sales", provider_id="provider-42")
    path = f"tasks/{task['id']}"
    parked = post(
        api,
        path + "/snooze",
        {
            "wait_thread_id": thread,
            "wait_sender": CUSTOMER,
            "wait_provider_reply_id": "provider-42",
            "until": H.shift(H.now(), days=1),
        },
    )
    assert parked["snooze"]["waiting_for_reply"]
    post(api, path + "/unsnooze", {}, expected=409)

    with store.transaction() as conn:
        _message(conn, thread, CUSTOMER, inbound=True, connection="support")
        assert E.task(conn, task["id"])["snooze"]
        conn.execute("SAVEPOINT reply")
        accepted = _message(conn, thread, CUSTOMER, inbound=True, connection="sales")
        assert E.task(conn, task["id"])["snooze"] is None
        assert task_wakes.queued(conn, task["id"], OWNER)
        conn.execute("ROLLBACK TO reply")
        conn.execute("RELEASE reply")
        assert not H.message(conn, accepted["id"])
        assert E.task(conn, task["id"])["snooze"]["id"]
        assert not task_wakes.queued(conn, task["id"], OWNER)
        accepted = _message(conn, thread, CUSTOMER, inbound=True, connection="sales")
    with store.read() as conn:
        assert H.message(conn, accepted["id"])
        assert E.task(conn, task["id"])["snooze"] is None
        assert task_wakes.queued(conn, task["id"], OWNER)
