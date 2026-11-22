from datetime import datetime, timezone

import pytest

from backend import gmail_live, mailbox_access, never_stuck, outside_intake, stage_rules, task_wakes
from backend import task_engine as E
from backend.item_sources import github
from backend.store import H, digest, encode
from backend.task_rule_gate import TaskRules
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import post
from backend.tests.helpers.automation_api import as_bot
from backend.tests.helpers.email_sending import BOX, CONTACT, autonomous, dispatch, inbox, queue  # noqa: F401
from backend.tests.helpers.fake_gmail import fake_gmail  # noqa: F401

OWNER = "bot:builder"
CUSTOMER = "ext:email:customer"
CONNECTION = "sales"


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(datetime(2030, 1, 7, tzinfo=timezone.utc), tick=False)
    with company.transaction() as conn:
        X.bot(conn, "builder")
        yield conn


def _thread(c, parent=None, participants=(X.ANA, CUSTOMER), connection=CONNECTION):
    tid = H.new_id()
    root, path = (
        c.execute("SELECT root_id,path FROM conversations WHERE id=?", (parent,)).fetchone() if parent else (tid, "/")
    )
    c.execute(
        "INSERT INTO conversations(id,kind,participants,parent_id,root_id,path,connection_id) "
        "VALUES(?,'chat',?,?,?,?,?)",
        (tid, H._dump(participants), parent, root, path + tid + "/", "" if parent else connection),
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


def test_reply_wakes_the_turn_once_without_spending_a_try(c):
    task = X.new_task(c, OWNER, "Check with customer")
    tid = task["id"]
    thread = _thread(c)
    sent = _message(c, thread, OWNER)
    c.execute("UPDATE visits SET tries=3,level='manager' WHERE id=?", (task["visit"]["id"],))
    parked = _park(c, task, thread, sent)
    assert not task_wakes.queued(c, tid, OWNER)

    reply = _reply(c, thread, notify=True)
    E.customer_replied(c, reply, H.conversation(c, thread))

    fresh = E.task(c, tid)
    assert fresh["snooze"] is None
    assert (fresh["visit"]["tries"], fresh["visit"]["level"]) == (0, "owner")
    assert task_wakes.queued(c, tid, OWNER)
    wakes = c.execute("SELECT body FROM messages WHERE wake_key LIKE ?", (f"{tid}:%:reply:%",)).fetchall()
    assert len(wakes) == 1 and "A customer replied" in wakes[0][0]
    assert c.execute("SELECT ended_at FROM snoozes WHERE id=?", (parked["snooze"]["id"],)).fetchone()[0]


def test_wrong_sender_other_thread_and_earlier_reply_leave_the_wait(c):
    task = X.new_task(c, OWNER, "Check with customer")
    thread = _thread(c)
    earlier = _reply(c, thread)
    parked = _park(c, task, thread, _message(c, thread, OWNER))

    room = H.conversation(c, thread)
    E.customer_replied(c, earlier, room)
    _reply(c, thread, "ext:email:other", notify=True)
    _reply(c, _thread(c), notify=True)
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
    path = f"tasks/{task['id']}/snooze"
    wait = {"wait_thread_id": thread, "wait_sender": CUSTOMER, "wait_after_seq": sent["seq"]}
    post(api, path, wait, expected=422)
    wait["until"] = H.shift(H.now(), days=1)
    with store.transaction() as conn:
        _message(conn, thread, CUSTOMER, direction=None)
        _reply(conn, thread, connection="support")
        _reply(conn, thread, connection="")
        deleted = _reply(conn, thread)
        conn.execute("UPDATE messages SET deleted_at=? WHERE id=?", (H.now(), deleted["id"]))
    assert post(api, path, wait)["snooze"]["waiting_for_reply"]
    with store.transaction() as conn:
        _reply(conn, thread, notify=True)
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
        secret = _message(conn, thread, OWNER)
        conn.execute("UPDATE messages SET refs=? WHERE id=?", (H._dump({"task": hidden["id"]}), secret["id"]))
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


def test_customer_wait_refuses_a_subthread(api):
    with api.app.state.store.transaction() as conn:
        task = X.new_task(conn, X.ANA, "Follow up")
        root = _thread(conn)
        child = _thread(conn, root)
        sent = _message(conn, child, X.ANA)
    refused = post(
        api,
        f"tasks/{task['id']}/snooze",
        {
            "wait_thread_id": child,
            "wait_sender": CUSTOMER,
            "wait_after_seq": sent["seq"],
            "until": H.shift(H.now(), days=1),
        },
        expected=422,
    )
    assert refused["error"] == "task_customer_wait_thread"


def _arrive_gmail(conn, box, source_id, timestamp_ms):
    return outside_intake.save_message(
        conn,
        channel="gmail",
        connection_id=box["id"],
        owner_id=box["person_id"],
        source_thread_id="thread",
        source_message_id=source_id,
        sender=CONTACT,
        title="Question",
        body="Reply",
        provider_timestamp_ms=timestamp_ms,
    )


def _gmail_wait(thread, provider_id):
    return {
        "wait_thread_id": thread,
        "wait_sender": f"ext:gmail:{CONTACT}",
        "wait_provider_reply_id": provider_id,
        "until": H.shift(H.now(), days=1),
    }


def test_real_gmail_send_anchors_a_wait_that_intake_wakes(inbox, fake_gmail):
    autonomous(inbox)
    store = inbox.app.state.store
    sent_ms = 1_783_368_401_125

    with store.transaction() as conn:
        box = mailbox_access.row_by_address(conn, BOX)
        other_box = mailbox_access.ensure_mailbox(conn, "cara@acme.example", person_id="cara", created_by="human:cara")
        assert _arrive_gmail(conn, box, "inbound", sent_ms - 1000)
        assert _arrive_gmail(conn, other_box, "inbound", sent_ms - 1000)
        thread = conn.execute("SELECT id FROM conversations WHERE connection_id=?", (box["id"],)).fetchone()[0]
        X.bot(conn, "builder")
        task = X.new_task(conn, X.ANA, "Follow up", assignee=OWNER)

    fake_gmail.send_answer = (200, {"id": "sent-reply", "threadId": "thread"})
    confirmed = fake_gmail.add(BOX, "sent-reply", thread="thread", sender=BOX, to=[CONTACT], labels=("SENT",))
    confirmed["internalDate"] = str(sent_ms)
    queued = queue(inbox, reply_to_id="inbound", thread_id="thread")
    [sent_mime] = dispatch(inbox)
    assert b"Thanks for the update." in sent_mime
    assert queue(inbox, reply_to_id="inbound", thread_id="thread")["id"] == queued["id"]
    assert dispatch(inbox) == []
    with store.read() as conn:
        [sent] = conn.execute("SELECT * FROM messages WHERE direction='outbound'").fetchall()
        assert (sent["conversation_id"], sent["connection_id"], sent["sender_id"]) == (thread, box["id"], X.ANA)
        assert (sent["channel"], sent["source_message_id"], sent["body"]) == (
            "gmail",
            "sent-reply",
            "Sent an email in Gmail.",
        )
        assert H._json(sent["refs"], {})["provider_timestamp_ms"] == sent_ms
        assert tuple(conn.execute("SELECT state,raw FROM email_sends WHERE id=?", (queued["id"],)).fetchone()) == (
            "sent",
            "",
        )

    with store.transaction() as conn:
        assert _arrive_gmail(conn, box, "older-before-wait", sent_ms - 1)
    parked = post(inbox, f"tasks/{task['id']}/snooze", _gmail_wait(thread, sent["source_message_id"]))
    assert parked["snooze"]["waiting_for_reply"]
    with store.transaction() as conn:
        assert _arrive_gmail(conn, other_box, "second", sent_ms + 1)
        assert _arrive_gmail(conn, box, "older-after-wait", sent_ms - 1)
        assert _arrive_gmail(conn, box, "same-millisecond", sent_ms)
        assert E.task(conn, task["id"])["snooze"]
        conn.execute("SAVEPOINT reply")
        assert _arrive_gmail(conn, box, "second", sent_ms + 1)
        assert E.task(conn, task["id"])["snooze"] is None
        assert task_wakes.queued(conn, task["id"], OWNER)
        conn.execute("ROLLBACK TO reply")
        conn.execute("RELEASE reply")
        assert not conn.execute(
            "SELECT 1 FROM messages WHERE connection_id=? AND source_message_id='second'", (box["id"],)
        ).fetchone()
        assert E.task(conn, task["id"])["snooze"]
        assert not task_wakes.queued(conn, task["id"], OWNER)
        run = _arrive_gmail(conn, box, "second", sent_ms + 1)
        assert run and _arrive_gmail(conn, box, "second", sent_ms + 1) == run
    with store.read() as conn:
        assert conn.execute(
            "SELECT 1 FROM messages WHERE connection_id=? AND source_message_id='second'", (box["id"],)
        ).fetchone()
        assert E.task(conn, task["id"])["snooze"] is None
        assert task_wakes.queued(conn, task["id"], OWNER)


def test_gmail_reply_saved_before_outbound_receipt_refuses_wait(inbox, fake_gmail, monkeypatch):
    autonomous(inbox)
    store = inbox.app.state.store
    sent_ms = 1_783_368_401_125
    with store.transaction() as conn:
        box = mailbox_access.row_by_address(conn, BOX)
        assert _arrive_gmail(conn, box, "inbound", sent_ms - 1000)
        task = X.new_task(conn, X.ANA, "Follow up")
        thread = outside_intake.conversation_id("gmail", box["id"], "thread")
    queue(inbox, reply_to_id="inbound", thread_id="thread")

    fake_gmail.send_answer = (200, {"id": "sent-reply", "threadId": "thread", "internalDate": str(sent_ms)})

    def confirmed_send(*args, **kwargs):
        confirmed = fake_gmail.send(*args, **kwargs)
        # The watcher commits the reply before the send worker stores its receipt.
        with store.transaction() as conn:
            assert _arrive_gmail(conn, box, "early-intake", sent_ms + 1)
        return confirmed

    monkeypatch.setattr(gmail_live, "send_call", confirmed_send)
    assert len(dispatch(inbox)) == 1

    refused = post(inbox, f"tasks/{task['id']}/snooze", _gmail_wait(thread, "sent-reply"), expected=409)
    assert refused["error"] == "task_customer_replied"
    with store.read() as conn:
        assert E.task(conn, task["id"])["snooze"] is None


def test_reply_without_a_matching_wait_only_probes_the_wait_index(c):
    room = _thread(c)
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


def test_bot_wait_defaults_its_reason_and_only_people_can_end_it_early(api, time_machine):
    time_machine.move_to(datetime(2030, 1, 7, tzinfo=timezone.utc), tick=False)
    store = api.app.state.store
    with store.transaction() as conn:
        X.bot(conn, "builder")
        task = X.new_task(conn, X.ANA, "Follow up", assignee=OWNER)
        thread = _thread(conn, participants=(X.ANA, OWNER, CUSTOMER))
        sent = _message(conn, thread, OWNER)
    bot_token = as_bot(api, "builder")
    path = f"tasks/{task['id']}"
    wait = {"wait_thread_id": thread, "wait_sender": CUSTOMER, "wait_after_seq": sent["seq"]}
    later = {**wait, "until": H.shift(H.now(), days=31)}
    denied = post(api, path + "/snooze", {**later, "version": task["version"]}, token=bot_token, expected=422)
    assert denied["error"] == "task_customer_wait_date"
    within_limit = {**wait, "until": H.shift(H.now(), days=30), "version": task["version"]}
    parked = post(api, path + "/snooze", within_limit, token=bot_token)
    assert parked["snooze"]["why"] == "Waiting for a customer reply"

    denied = post(api, path + "/unsnooze", {}, token=bot_token, expected=409)
    assert denied["error"] == "task_customer_wait"
    with store.transaction() as conn:
        assert E.unsnooze(conn, task["id"], X.ANA, consequence="answer")["snooze"]
    assert post(api, path + "/unsnooze", {})["snooze"] is None
    assert post(api, path + "/snooze", later)["snooze"]["waiting_for_reply"]


def test_ordinary_snooze_replays_a_receipt_from_before_wait_fields(api):
    store = api.app.state.store
    with store.transaction() as conn:
        task = X.new_task(conn, X.ANA, "Follow up")
    path = f"tasks/{task['id']}/snooze"
    body = {"until": H.shift(H.now(), days=1), "why": "Check tomorrow"}
    first = post(api, path, body, key="old-snooze")
    with store.transaction() as conn:
        conn.execute(
            "UPDATE idempotency SET request_hash=? WHERE key='old-snooze'",
            (digest(encode({**body, "version": None})),),
        )
    assert post(api, path, {**body, "wait_thread_id": None}, key="old-snooze") == first


def test_snooze_resolves_the_anchor_once_when_rules_let_it_through(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as conn:
        task = X.new_task(conn, X.ANA, "Follow up")
        thread = _thread(conn)
        sent = _message(conn, thread, X.ANA)

    def verdict(rules, who, tid, operation, *args, **kwargs):
        with store.read() as conn:
            recipients = rules.audience(conn, who, E.task(conn, tid))
        return {
            "outcome": "no_rules",
            "author": who.actor,
            "operation": operation,
            "recipients": sorted(set(recipients) - {who.actor}),
        }

    looked_up = []
    resolve = E.customer_wait_anchor

    def anchor(conn, room, sender, seq, provider_id):
        looked_up.append(seq)
        return resolve(conn, room, sender, seq, provider_id)

    monkeypatch.setattr(TaskRules, "check", verdict)
    monkeypatch.setattr(E, "customer_wait_anchor", anchor)
    wait = {"wait_thread_id": thread, "wait_sender": CUSTOMER, "wait_after_seq": sent["seq"]}
    assert post(api, f"tasks/{task['id']}/snooze", {**wait, "until": H.shift(H.now(), days=1)})["snooze"]
    assert looked_up == [sent["seq"]]


def test_github_news_skips_customer_waits_before_looking_up_prs_or_turns(c, monkeypatch):
    task = X.new_task(c, OWNER, "Follow up")
    tid = task["id"]
    thread = _thread(c)
    E.snooze(
        c,
        tid,
        OWNER,
        "Waiting for a reply on acme/service#42",
        H.shift(H.now(), days=1),
        task["version"],
        customer_wait=(thread, CUSTOMER, _message(c, thread, OWNER)["seq"]),
    )
    c.execute(
        "INSERT INTO task_links(id,task_id,kind,url,state,repo,number,created_at) "
        "VALUES(?,?,'pr','https://github.com/acme/service/pull/5','open','acme/service',5,?)",
        (H.new_id(), tid, H.now()),
    )

    def unexpected(*args):
        pytest.fail("GitHub news looked up a customer wait's PR or turn")

    monkeypatch.setattr(github, "queues_now", unexpected)
    monkeypatch.setattr(github, "prs_of", unexpected)
    monkeypatch.setattr(E, "_turn_of", unexpected)
    assert not github.snooze_news(c, tid, "checks:acme/service#5", "Checks passed")
    assert not github.base_moved(c, "acme/service", "main", "a" * 40, default_branch="main")
    assert not github.named_pr_done(c, "acme/service", 42, "merged")
    assert E.task(c, tid)["snooze"]["wait_thread_id"] == thread
