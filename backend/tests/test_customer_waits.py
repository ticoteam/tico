from datetime import datetime, timezone

import pytest

from backend import never_stuck, outside_intake, stage_rules, task_wakes
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import post

OWNER = "bot:builder"
CUSTOMER = "ext:email:customer"
CONNECTION = "sales"


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(datetime(2030, 1, 7, tzinfo=timezone.utc), tick=False)
    with company.transaction() as conn:
        X.bot(conn, "builder")
        yield conn


def _thread(c, parent=None, participants=(X.ANA, CUSTOMER), connection=CONNECTION, task_id=None):
    tid = H.new_id()
    root, path = (
        c.execute("SELECT root_id,path FROM conversations WHERE id=?", (parent,)).fetchone() if parent else (tid, "/")
    )
    c.execute(
        "INSERT INTO conversations(id,kind,participants,parent_id,root_id,path,connection_id,task_id) "
        "VALUES(?,'chat',?,?,?,?,?,?)",
        (tid, H._dump(participants), parent, root, path + tid + "/", "" if parent else connection, task_id),
    )
    return tid


def _message(c, thread, sender, *, notify=False, connection=CONNECTION, provider_id=None, direction="outbound"):
    mid = H.new_id()
    c.execute(
        "INSERT INTO messages(id,conversation_id,sender_id,kind,body,refs,created_at,"
        "channel,direction,connection_id,source_message_id) VALUES(?,?,?,'say','Hello','{}',?,'email',?,?,?)",
        (mid, thread, sender, H.now(), direction, connection, provider_id or mid),
    )
    message = H.message(c, mid)
    if notify:
        E.customer_replied(c, message, H.conversation(c, thread))
    return message


def _reply(c, thread, sender=CUSTOMER, **kwargs):
    return _message(c, thread, sender, direction="inbound", **kwargs)


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

    room = _thread(c, thread) if subthread else thread
    reply = _reply(c, room, notify=True)
    E.customer_replied(c, reply, H.conversation(c, room))

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
    earlier = _reply(c, thread)
    parked = _park(c, task, thread, _message(c, thread, OWNER))

    room = H.conversation(c, thread)
    E.customer_replied(c, earlier, room)
    _reply(c, thread, "ext:email:other", notify=True)
    _reply(c, _thread(c, root), notify=True)
    _reply(c, thread, connection="support", notify=True)
    _reply(c, thread, connection="", notify=True)
    internal = _message(c, thread, CUSTOMER, direction=None, notify=True)
    E.customer_replied(c, {key: value for key, value in internal.items() if key != "direction"}, room)

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
        _reply(c, thread, notify=True)

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
    wait["until"] = H.shift(H.now(), days=1)
    with store.transaction() as conn:
        _message(conn, reply_thread, CUSTOMER, direction=None)
        _reply(conn, reply_thread, connection="support")
        _reply(conn, reply_thread, connection="")
        deleted = _reply(conn, reply_thread)
        conn.execute("UPDATE messages SET deleted_at=? WHERE id=?", (H.now(), deleted["id"]))
        removed_thread = _thread(conn, thread)
        _reply(conn, removed_thread)
        conn.execute("UPDATE conversations SET deleted_at=? WHERE id=?", (H.now(), removed_thread))
    assert post(api, path, wait)["snooze"]["waiting_for_reply"]
    with store.transaction() as conn:
        _reply(conn, reply_thread, notify=True)
    post(api, path, wait, token="cara-test", expected=404)
    response = post(api, path, wait, expected=409)
    assert response["error"] == "task_customer_replied"
    with store.read() as conn:
        assert E.task(conn, task["id"])["snooze"] is None
        assert task_wakes.queued(conn, task["id"], OWNER)


def test_snooze_requires_outside_provenance_and_hides_unreadable_anchors(api):
    store = api.app.state.store
    with store.transaction() as conn:
        who = "human:cara"
        task = X.new_task(conn, who, "Follow up")
        hidden = H.task_create(conn, X.ANA, "Private work", "Look into it", X.ANA, private=True, lint=False)
        thread = _thread(conn, participants=(who, CUSTOMER))
        internal = _thread(conn, participants=(who, CUSTOMER), connection="")
        secret_room = _thread(conn, thread, task_id=hidden["id"])
        secret = _message(conn, secret_room, OWNER)
        unrelated = _message(conn, _thread(conn), OWNER)
        internal_post = _message(conn, thread, who, direction=None)
        other_inbox = _message(conn, thread, OWNER, connection="support")
        inbound = _reply(conn, thread)
        sent = _message(conn, thread, OWNER, provider_id="provider-42")
    path = f"tasks/{task['id']}/snooze"
    wait = {"wait_thread_id": thread, "wait_sender": CUSTOMER, "until": H.shift(H.now(), days=1)}
    for thread_id, sender in ((internal, CUSTOMER), (thread, "ext:email:unknown"), (thread, who)):
        post(
            api,
            path,
            {**wait, "wait_thread_id": thread_id, "wait_sender": sender, "wait_after_seq": sent["seq"]},
            token="cara-test",
            expected=422,
        )
    missing = post(api, path, {**wait, "wait_after_seq": 2**63 - 1}, token="cara-test", expected=404)
    for anchor in (secret, unrelated, internal_post, other_inbox, inbound):
        assert post(api, path, {**wait, "wait_after_seq": anchor["seq"]}, token="cara-test", expected=404) == missing
    assert post(api, path, {**wait, "wait_provider_reply_id": "provider-42"}, token="cara-test")["snooze"][
        "waiting_for_reply"
    ]


def test_intake_commits_the_reply_and_wake_together(api):
    store = api.app.state.store

    def arrive(conn, source_id):
        return outside_intake.save_message(
            conn,
            channel="email",
            connection_id=CONNECTION,
            owner_id="ana",
            source_thread_id="outside-thread",
            source_message_id=source_id,
            sender="customer",
            title="Question",
            body="Reply",
        )

    with store.transaction() as conn:
        assert arrive(conn, "first")
        thread = conn.execute("SELECT id FROM conversations WHERE connection_id=?", (CONNECTION,)).fetchone()[0]
        X.bot(conn, "builder")
        task = X.new_task(conn, OWNER, "Follow up")
        _park(conn, task, thread, _message(conn, thread, OWNER))
    with store.transaction() as conn:
        conn.execute("SAVEPOINT reply")
        assert arrive(conn, "second")
        assert E.task(conn, task["id"])["snooze"] is None
        assert task_wakes.queued(conn, task["id"], OWNER)
        conn.execute("ROLLBACK TO reply")
        conn.execute("RELEASE reply")
        assert not conn.execute("SELECT 1 FROM messages WHERE source_message_id='second'").fetchone()
        assert E.task(conn, task["id"])["snooze"]
        assert not task_wakes.queued(conn, task["id"], OWNER)
        run = arrive(conn, "second")
        assert run and arrive(conn, "second") == run
    with store.read() as conn:
        assert conn.execute("SELECT 1 FROM messages WHERE source_message_id='second'").fetchone()
        assert E.task(conn, task["id"])["snooze"] is None
        assert task_wakes.queued(conn, task["id"], OWNER)


def test_reply_without_a_matching_wait_only_probes_the_wait_index(c):
    room = _thread(c, _thread(c))
    reply = _reply(c, room)
    thread = H.conversation(c, room)
    reads = []
    c.set_trace_callback(reads.append)
    try:
        E.customer_replied(c, reply, thread)
    finally:
        c.set_trace_callback(None)
    assert len(reads) == 1 and "FROM snoozes" in reads[0]
    plan = c.execute("EXPLAIN QUERY PLAN " + reads[0]).fetchall()
    assert any("snoozes_wait_thread" in row[3] for row in plan)
