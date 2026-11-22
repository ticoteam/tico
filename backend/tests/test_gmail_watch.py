"""The server hears new mail from Gmail itself (backend/gmail_watch.py): each message once, nothing of it kept."""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from backend import automations, drains, gmail_watch, google_workspace, outside_intake
from backend import mailbox_access as MA
from backend.entities import store as ES
from backend.store import H
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.fake_gmail import fake_gmail  # noqa: F401

BOX = "ana@acme.example"
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
NOOP = {
    "id": "sort",
    "type": "filter",
    "rules": [
        {"when": [{"field": "message.subject", "op": "contains", "value": "zz-never"}], "then": [{"do": "archive"}]}
    ],
}


def epoch(at):
    return int(at.timestamp())


@pytest.fixture
def watched(api, fake_gmail, monkeypatch):
    """Ana's mailbox, covered by the company's key, and one message automation on it."""
    monkeypatch.setattr(google_workspace, "authority", lambda c, mailbox: "workspace")
    with api.app.state.store.transaction() as c:
        MA.ensure_mailbox(c, BOX, person_id="ana", created_by="human:ana")
    aid = post(
        api,
        "automations",
        {"name": "Mail", "owner": "person:ana", "trigger": {"type": "message", "inbox": BOX}, "steps": [NOOP]},
    )["id"]
    return api, fake_gmail, aid


def check(api, at):
    """One pass of the watch at `at` (each test steps on past INTERVAL, so the mailbox is due)."""
    gmail_watch._TRIED.clear()
    with time_machine.travel(at, tick=False):
        return gmail_watch.drain(api.app.state.store)


def heard(api, aid):
    rows = get(api, f"automations/{aid}/runs")["runs"]
    refs = [r["trigger_ref"] for r in rows]
    assert len(refs) == len(set(refs))  # never twice
    return sorted(ref.rsplit(":", 1)[-1] for ref in refs)


def box_row(api):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM mailboxes WHERE address=?", (BOX,)).fetchone())


def test_new_mail_is_heard_once_and_old_mail_before_the_watch_never(watched):
    api, gmail, aid = watched
    gmail.add(BOX, "recent", epoch=epoch(T0 - timedelta(hours=2)))
    gmail.add(BOX, "ancient", epoch=epoch(T0 - timedelta(days=3)))
    gmail.add(BOX, "draft", epoch=epoch(T0 - timedelta(hours=1)), labels=("DRAFT",))
    assert check(api, T0) == 1  # the first check: the last day's mail, drafts not heard
    assert heard(api, aid) == ["recent"]
    first = box_row(api)
    assert first["watching_since_at"] == "2026-11-15T12:00:00.000000Z" and first["check_error"] is None
    assert first["history_id"] == str(gmail.history_id(BOX))
    assert check(api, T0 + timedelta(minutes=2)) == 0  # a repeat pass hears nothing new
    gmail.add(BOX, "new", epoch=epoch(T0 + timedelta(minutes=3)))
    assert check(api, T0 + timedelta(minutes=4)) == 1
    assert heard(api, aid) == ["new", "recent"]
    assert box_row(api)["watching_since_at"] == first["watching_since_at"]  # the watch never moves


def test_an_expired_history_id_lists_again_without_hearing_anything_twice(watched):
    api, gmail, aid = watched
    gmail.add(BOX, "one", epoch=epoch(T0 - timedelta(minutes=30)))
    check(api, T0)
    gmail.add(BOX, "two", epoch=epoch(T0 + timedelta(minutes=1)))
    gmail.expired.add(BOX)  # Gmail no longer has the saved history id: the last hour is listed again
    assert check(api, T0 + timedelta(minutes=3)) == 2
    assert heard(api, aid) == ["one", "two"]


def test_intake_commits_before_the_cursor_and_replay_returns_the_same_run(watched, monkeypatch):
    api, gmail, _ = watched
    store = api.app.state.store
    gmail.add(BOX, "first", thread="support", epoch=epoch(T0), labels=("INBOX", "Tico Intake"))
    box = box_row(api)
    original = automations.queue_intake

    def fail(*args):
        raise RuntimeError("intake write interrupted")

    monkeypatch.setattr(automations, "queue_intake", fail)
    with pytest.raises(RuntimeError, match="intake write interrupted"):
        check(api, T0)
    assert box_row(api)["history_id"] == box["history_id"]
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE source_message_id='first'").fetchone()

    monkeypatch.setattr(automations, "queue_intake", original)
    assert check(api, T0) == 1
    assert box_row(api)["history_id"] == str(gmail.history_id(BOX))
    with store.transaction() as c:
        run = c.execute("SELECT id,status FROM automation_runs WHERE trigger_ref LIKE 'intake:%'").fetchone()
        assert run["status"] == "queued"
        same = outside_intake.save_message(
            c,
            channel="gmail",
            connection_id=box["id"],
            owner_id="ana",
            source_thread_id="support",
            source_message_id="first",
            sender="person@customer.example",
            title="Replay",
            body="Replay",
        )
        assert same == run["id"]
        assert c.execute("SELECT count(*) FROM messages WHERE source_message_id='first'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM automation_runs WHERE trigger_ref LIKE 'intake:%'").fetchone()[0] == 1


def test_intake_arrivals_survive_a_full_hold_and_a_stale_claim(watched):
    api, gmail, _ = watched
    store = api.app.state.store
    gmail.add(BOX, "one", thread="support", epoch=epoch(T0), labels=("INBOX", "Tico Intake"))
    check(api, T0)
    with store.transaction() as c:
        first = c.execute("SELECT * FROM automation_runs WHERE trigger_ref LIKE 'intake:%'").fetchone()
        aid = first["automation_id"]
        automations._hold(c, automations._row(c, aid), "flood")
        c.execute("UPDATE holds SET details=? WHERE holdable_id=?", (json.dumps({"arrivals": 500}), aid))
    gmail.add(BOX, "two", thread="support", epoch=epoch(T0 + timedelta(minutes=2)), labels=("INBOX", "Tico Intake"))
    check(api, T0 + timedelta(minutes=3))
    with store.transaction() as c:
        assert [row[0] for row in c.execute("SELECT status FROM automation_runs WHERE automation_id=?", (aid,))] == [
            "held",
            "held",
        ]
        assert automations._unhold(c, aid, "human:ana", "release") == 2
        c.execute(
            "UPDATE automation_runs SET status='running',claimed_at=?,claim_token='expired' WHERE id=?",
            (first["started_at"], first["id"]),
        )
    later = T0 + timedelta(minutes=automations.STALE_MINUTES + 1)
    with time_machine.travel(later, tick=False):
        automations.tick(store, later, drain=False)
        assert not store.automations.go(automations.RunClaim(first["id"], "expired"))
        store.automations.drain()
    with store.read() as c:
        assert [row[0] for row in c.execute("SELECT status FROM automation_runs WHERE automation_id=?", (aid,))] == [
            "ok",
            "ok",
        ]
        assert c.execute("SELECT count(*) FROM tasks WHERE occurrence_step='triage'").fetchone()[0] == 1


def test_unknown_sender_opens_one_private_due_task_and_ingest_does_not_fan_out(watched):
    api, gmail, _ = watched
    store = api.app.state.store
    with store.transaction() as c:
        possible = ES.upsert(c, "human:ana", "person", match={"email": "person@customer.example"})["id"]
    gmail.add(BOX, "one", thread="support", epoch=epoch(T0), labels=("INBOX", "Tico Intake"))
    check(api, T0)
    with store.read() as c:
        thread = c.execute("SELECT * FROM conversations WHERE audience='entity'").fetchone()
        assert thread["entity_id"] != possible  # From is only a suggestion
        assert "ext:gmail:person@customer.example" in json.loads(thread["participants"])
    root_id = thread["id"]
    followed = api.put(f"/api/v2/threads/{root_id}/follow", json={"notify": "live"}, headers=headers())
    assert followed.status_code == 200, followed.text

    gmail.add(
        BOX,
        "two",
        thread="support",
        body="@ana @ops",
        epoch=epoch(T0 + timedelta(minutes=2)),
        labels=("INBOX", "Tico Intake"),
    )
    check(api, T0 + timedelta(minutes=3))
    with time_machine.travel(T0 + timedelta(minutes=4), tick=False):
        store.automations.drain()
    with store.read() as c:
        (task,) = c.execute("SELECT * FROM tasks WHERE occurrence_step='triage'")
        assert (task["owner_id"], task["assignee_id"], task["private"]) == ("human:ana", "human:ana", 1)
        assert task["entity_id"] == thread["entity_id"]
        assert H.parse_ts(task["due_at"]) == T0 + timedelta(days=1)
        assert c.execute("SELECT 1 FROM messages WHERE body LIKE ?", (f"%Possible contact {possible}.",)).fetchone()
        two = c.execute("SELECT * FROM messages WHERE source_message_id='two'").fetchone()
        assert (two["conversation_id"], two["direction"], two["channel"]) == (root_id, "inbound", "gmail")
        for table in ("message_mentions", "message_deliveries", "jobs"):
            assert not c.execute(f"SELECT 1 FROM {table} WHERE message_id=?", (two["id"],)).fetchone()


def test_entity_and_inbox_gate_threads_subthreads_and_history_before_paging(watched):
    api, _, _ = watched
    store = api.app.state.store
    mailbox = box_row(api)["id"]
    with store.transaction() as c:
        entity = ES.upsert(c, "human:ana", "person", match={}, name="Acme customer")["id"]
        c.execute(
            "INSERT INTO entity_external_refs(id,entity_id,connection_id,system,external_type,external_id) "
            "VALUES('sender-ref',?,?,'gmail','sender','verified')",
            (entity, mailbox),
        )
        outside_intake.save_message(
            c,
            channel="gmail",
            connection_id=mailbox,
            owner_id="ana",
            source_thread_id="support",
            source_message_id="verified-delivery",
            sender="person@customer.example",
            verified_id="verified",
            title="Private subject",
            body="Private body",
        )
        root = c.execute("SELECT id FROM conversations WHERE entity_id=?", (entity,)).fetchone()[0]
        parent = c.execute("SELECT id FROM messages WHERE conversation_id=?", (root,)).fetchone()[0]
        c.execute(
            "INSERT INTO entity_activities (id,entity_id,kind,occurred_at,summary) "
            "VALUES('visible-activity',?,'booking','2026-01-01','Visit')",
            (entity,),
        )
    reply = post(api, f"threads/{root}/messages", {"text": "Internal note", "parent_message_id": parent})
    child = reply["thread_id"]
    assert child != root
    for id_ in (root, child):
        assert api.get(f"/api/v2/threads/{id_}/timeline", headers=headers("ben-test")).status_code == 404
        assert api.get(f"/api/v2/threads/{id_}/timeline", headers=headers()).status_code == 200

    url = "/api/v2/entities/activity"
    params = {"ref": entity, "limit": 1}
    response = api.get(url, params=params, headers=headers("ben-test"))
    assert response.status_code == 200, response.text
    answer = response.json()
    assert [row["id"] for row in answer["rows"]] == ["visible-activity"]
    assert answer["next"] is None
    answer = api.get(url, params=params, headers=headers()).json()
    assert [row["id"] for row in answer["rows"]] == [root]
    page = api.get(url, params={**params, "after": answer["next"]}, headers=headers()).json()
    assert [row["id"] for row in page["rows"]] == ["visible-activity"]
    for token, expected in (("ana-test", 1), ("ben-test", 0)):
        result = post(api, "sql", {"sql": f"SELECT body FROM messages WHERE id='{parent}'"}, token)
        assert len(result["rows"]) == expected
    with store.transaction() as c:
        grant = MA.grant(c, "human:ana", mailbox, "person", "ben", "read")
    assert api.get(f"/api/v2/threads/{child}/timeline", headers=headers("ben-test")).status_code == 200
    with store.transaction() as c:
        ES.add_source(c, "human:ana", entity, "note_me", "human:ana", visibility="restricted")
    assert api.get(f"/api/v2/threads/{child}/timeline", headers=headers("ben-test")).status_code == 404
    assert api.get(url, params=params, headers=headers("ben-test")).status_code == 404
    with store.transaction() as c:
        ES.add_source(c, "human:ana", entity, "hub", "human:ana", visibility="company")
    assert api.get(f"/api/v2/threads/{child}/timeline", headers=headers("ben-test")).status_code == 200
    with store.transaction() as c:
        MA.revoke(c, "human:ana", mailbox, grant["id"])
    assert api.get(f"/api/v2/threads/{child}/timeline", headers=headers("ben-test")).status_code == 404


def test_gmail_down_leaves_the_history_id_and_says_why_then_catches_up(watched):
    api, gmail, aid = watched
    check(api, T0)
    before = box_row(api)
    gmail.add(BOX, "during", epoch=epoch(T0 + timedelta(minutes=1)))
    gmail.down.add(BOX)
    assert check(api, T0 + timedelta(minutes=3)) == 0
    after = box_row(api)
    assert (after["history_id"], after["checked_at"]) == (before["history_id"], before["checked_at"])
    assert "Gmail did not answer" in after["check_error"]
    listed = {m["address"]: m for m in get(api, "mailboxes")["mailboxes"]}
    assert (listed[BOX]["sync"], listed[BOX]["sync_error"]) == ("failed", after["check_error"])
    gmail.down.clear()
    assert check(api, T0 + timedelta(minutes=6)) == 1
    assert heard(api, aid) == ["during"] and box_row(api)["check_error"] is None


def test_a_refused_mailbox_goes_to_reconnects_as_a_sign_in_to_fix(watched):
    api, gmail, _ = watched
    gmail.refused.add(BOX)
    check(api, T0)
    listed = {m["address"]: m for m in get(api, "mailboxes")["mailboxes"]}
    assert listed[BOX]["sync"] == "signin"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT service FROM google_signin_refusals WHERE account=?", (BOX,)).fetchone()[0] == "mail"
    gmail.refused.clear()
    check(api, T0 + timedelta(minutes=3))
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM google_signin_refusals WHERE account=?", (BOX,)).fetchone()


def test_bounces_known_sender_and_the_directory_are_fed_and_no_row_keeps_a_body(watched):
    api, gmail, aid = watched
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "INSERT INTO email_sends(id,mailbox,actor,bot,via,content_sha256,idempotency_key,recipients,attachments,"
            "subject,body_sha256,raw,draft_id,thread_id,reply_to_id,approval_id,classification,decision,state,"
            "created_at,send_after) VALUES('s1',?,'human:ana','','','h','k1','[\"gone@vendor.example\"]','[]','Hi',"
            "'b','','','','','','{}','{}','sent',?,?)",
            (BOX, H.now(), H.now()),
        )
    gmail.add(BOX, "wrote-before", sender=BOX, to=["jane@client.example"], labels=("SENT",), epoch=epoch(T0) - 7200)
    gmail.add(
        BOX,
        "jane",
        sender="jane@client.example",
        sender_header="Jane Ortiz <jane@client.example>",
        body="SECRET CONTRACT TERMS",
        snippet="A short preview",
        epoch=epoch(T0) - 60,
    )
    gmail.add(
        BOX,
        "bounce",
        sender="mailer-daemon@vendor.example",
        body="Final-Recipient: rfc822; gone@vendor.example",
        epoch=epoch(T0) - 30,
    )
    check(api, T0)
    with store.read() as c:
        assert c.execute("SELECT address FROM email_suppressed_recipients").fetchone()[0] == "gone@vendor.example"
        assert ES.matching(c, "person", {"email": "jane@client.example"})
        jane = c.execute(
            "SELECT saved_state FROM automation_runs WHERE trigger_ref=?", ("mail:" + BOX + ":jane",)
        ).fetchone()
        message = json.loads(jane["saved_state"])["payload"]["message"]
        assert message["known_sender"] is True and message["body"] == "SECRET CONTRACT TERMS"  # while it runs
    store.automations.drain()
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM automation_runs WHERE finished_at IS NULL").fetchone()[0] == 0
        for row in c.execute("SELECT saved_state FROM automation_runs"):
            assert "SECRET CONTRACT TERMS" not in (row[0] or "")
    assert sorted(heard(api, aid)) == ["bounce", "jane", "wrote-before"]


def test_a_finished_run_of_any_kind_drops_the_body_it_read(api):
    """The database's own rule: any write that finishes a run (or touches a finished one) takes the body out."""
    store = api.app.state.store
    state = {"payload": {"message": {"subject": "Hi", "body": "Private words", "snippet": "Private"}}, "steps": []}
    with store.transaction() as c:
        for rid, status in (("run_a", "running"), ("run_b", "queued")):
            c.execute(
                "INSERT INTO automation_runs(id,automation_id,version,started_at,status,saved_state) "
                "VALUES(?,'auto',1,?,?,?)",
                (rid, H.now(), status, json.dumps(state)),
            )
        c.execute("UPDATE automation_runs SET status='ok',finished_at=? WHERE id='run_a'", (H.now(),))
        c.execute("UPDATE automation_runs SET saved_state=saved_state WHERE id='run_b'")
        kept = {r[0]: json.loads(r[1]) for r in c.execute("SELECT id,saved_state FROM automation_runs")}
    assert kept["run_a"]["payload"]["message"] == {"subject": "Hi"}  # the body and the snippet cut from it are gone
    assert kept["run_b"]["payload"]["message"]["body"] == "Private words"  # unfinished: the run still reads it


def test_fifty_mailboxes_take_one_pass_within_the_drain_budget(api, fake_gmail, monkeypatch):
    monkeypatch.setattr(google_workspace, "authority", lambda c, mailbox: "workspace")
    with api.app.state.store.transaction() as c:
        for n in range(50):
            MA.ensure_mailbox(c, f"inbox{n}@acme.example", kind="shared", created_by="human:ana")
    for n in range(50):
        fake_gmail.add(f"inbox{n}@acme.example", f"m{n}", epoch=int(time.time()) - 60)
    started = time.monotonic()
    heard_now = gmail_watch.drain(api.app.state.store, budget=drains.Budget(drains.BUDGET))
    assert heard_now == 50 and time.monotonic() - started < drains.BUDGET
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM mailboxes WHERE checked_at IS NULL").fetchone()[0] == 0


def test_no_google_token_reaches_a_response_log_event_or_sql(watched, caplog, monkeypatch):
    from backend import google_tokens

    api, gmail, _ = watched
    secret = "ya29.SECRET-ACCESS-TOKEN"
    monkeypatch.setattr(google_tokens, "token", lambda c, mailbox, service, write=False: secret)
    gmail.add(BOX, "m1", epoch=int(time.time()) - 60)
    gmail_watch.drain(api.app.state.store)
    gmail.down.add(BOX)
    gmail_watch._TRIED.clear()
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE mailboxes SET checked_at=NULL")
    gmail_watch.drain(api.app.state.store)
    answers = [
        api.get(path, headers=headers()).text
        for path in ("/api/v2/mailboxes", "/api/v2/mail/mailboxes", "/api/v2/messaging/bots")
    ]
    with api.app.state.store.read() as c:
        rows = [
            json.dumps([tuple(r) for r in c.execute(f"SELECT * FROM {table}")])
            for table in ("mailboxes", "events", "service_health", "automation_runs")
        ]
    for text in (*answers, *rows, caplog.text):
        assert secret not in text


@pytest.fixture
def ears(monkeypatch):
    """The ids each new message `on_mail` is given, in order (an automation's run guard holds back a burst of runs)."""
    from backend import automations

    got = []
    real = automations.on_mail

    def on_mail(c, mailbox, message, watch=None):
        got.append(message.msg_id)
        return real(c, mailbox, message, watch)

    monkeypatch.setattr(automations, "on_mail", on_mail)
    return got


class Allow:
    """A drain budget that lets `n` messages through, then is spent."""

    def __init__(self, n):
        self.n = n

    def left(self):
        self.n -= 1
        return self.n >= 0


def watch_pass(api, at, budget=None):
    gmail_watch._TRIED.clear()
    with time_machine.travel(at, tick=False):
        return gmail_watch.drain(api.app.state.store, budget=budget)


def test_a_first_check_hears_every_message_of_the_day_oldest_first_across_passes(watched, ears):
    """More than one Gmail page of mail in the lookback: each pass hears what its budget allows, oldest first, and the
    history id is saved only once the whole day is heard, so nothing older is skipped."""
    api, gmail, aid = watched
    for n in range(600):
        gmail.add(BOX, f"m{n:03d}", epoch=epoch(T0 - timedelta(hours=20)) + n)
    heard_by_pass = [watch_pass(api, T0 + timedelta(minutes=3 * k), Allow(250)) for k in range(2)]
    assert box_row(api)["history_id"] is None and box_row(api)["catch_up"]  # still catching up
    gmail.add(BOX, "during", epoch=epoch(T0 + timedelta(minutes=7)))  # arrives while it catches up
    heard_by_pass.append(watch_pass(api, T0 + timedelta(minutes=9), Allow(250)))
    assert box_row(api)["history_id"] and box_row(api)["catch_up"] is None
    watch_pass(api, T0 + timedelta(minutes=12))
    assert heard_by_pass[:2] == [250, 250]
    assert ears[:600] == [f"m{n:03d}" for n in range(600)]  # oldest first, each once
    assert set(ears) == {f"m{n:03d}" for n in range(600)} | {"during"}


def test_an_expired_history_id_catches_up_past_one_page(watched, ears):
    api, gmail, aid = watched
    check(api, T0)
    for n in range(520):
        gmail.add(BOX, f"x{n:03d}", epoch=epoch(T0) + 30 + n // 10)
    gmail.expired.add(BOX)
    watch_pass(api, T0 + timedelta(minutes=3))
    assert sorted(ears) == [f"x{n:03d}" for n in range(520)]


def test_a_history_record_with_many_messages_is_heard_whole_and_once(watched, ears):
    api, gmail, aid = watched
    check(api, T0)
    gmail.add(BOX, "r000", epoch=epoch(T0) + 10)
    for n in range(1, 101):
        gmail.add(BOX, f"r{n:03d}", epoch=epoch(T0) + 10, same_record=True)
    assert watch_pass(api, T0 + timedelta(minutes=3)) == 101
    assert watch_pass(api, T0 + timedelta(minutes=6)) == 0
    assert sorted(ears) == [f"r{n:03d}" for n in range(101)]


def test_a_spent_budget_stops_between_messages_and_the_next_pass_goes_on(watched):
    api, gmail, aid = watched
    check(api, T0)
    for n in range(5):
        gmail.add(BOX, f"h{n}", epoch=epoch(T0) + 10 + n)
    assert watch_pass(api, T0 + timedelta(minutes=3), Allow(2)) == 2
    assert heard(api, aid) == ["h0", "h1"]
    assert watch_pass(api, T0 + timedelta(minutes=6), Allow(2)) == 2
    assert watch_pass(api, T0 + timedelta(minutes=9)) == 1
    assert heard(api, aid) == [f"h{n}" for n in range(5)]


def test_a_personal_grant_mailbox_is_watched_on_the_server(api, fake_gmail, monkeypatch):
    """A mailbox only its person's own Connect Google grant covers: the server reads its Gmail through that grant (the
    token door) and hears its mail; no computer syncs it."""
    monkeypatch.setattr(google_workspace, "authority", lambda c, mailbox: "personal" if mailbox == BOX else "legacy")
    with api.app.state.store.transaction() as c:
        MA.ensure_mailbox(c, BOX, person_id="ana", created_by="human:ana")
        MA.ensure_mailbox(c, "nobody@elsewhere.example", kind="shared", created_by="human:ana")
    aid = post(
        api,
        "automations",
        {"name": "Mail", "owner": "person:ana", "trigger": {"type": "message", "inbox": BOX}, "steps": [NOOP]},
    )["id"]
    fake_gmail.add(BOX, "mine", epoch=int(time.time()) - 60)
    assert gmail_watch.drain(api.app.state.store) == 1
    assert heard(api, aid) == ["mine"]
    assert not any("elsewhere" in path for path, _, _ in fake_gmail.calls)  # no credential covers it: not watched
