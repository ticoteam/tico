"""The server hears new mail from Gmail itself (backend/gmail_watch.py): each message once, nothing of it kept."""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from backend import automations, drains, gmail_live, gmail_watch, google_workspace, outside_intake, threads
from backend import mailbox_access as MA
from backend.entities import store as ES
from backend.store import H
from backend.tests.helpers.api import get, headers, post, setup_attempt  # noqa: F401
from backend.tests.helpers.execution_review import interrupted
from backend.tests.helpers.fake_gmail import fake_gmail  # noqa: F401

BOX = "ana@acme.example"
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
DMARC_PASS = {"Authentication-Results": "mx.google.com; dmarc=pass (p=reject) header.from=customer.example"}
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


@pytest.mark.parametrize("watch_path", ["first_check", "history", "expired_history"])
def test_bad_watch_timestamp_starts_a_new_window_for_catch_up_and_history(watched, watch_path):
    api, gmail, aid = watched
    if watch_path != "first_check":
        assert check(api, T0) == 0
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE mailboxes SET watching_since_at='invalid' WHERE address=?", (BOX,))
    if watch_path == "expired_history":
        gmail.expired.add(BOX)
    later = T0 + timedelta(minutes=3)
    gmail.add(BOX, "old", epoch=epoch(T0 + timedelta(minutes=1)))
    gmail.add(BOX, "ordinary", epoch=epoch(later))
    check(api, later)
    assert box_row(api)["history_id"] == str(gmail.history_id(BOX))
    assert H.parse_ts(box_row(api)["watching_since_at"]) == later
    assert heard(api, aid) == ["ordinary"]
    gmail.expired.discard(BOX)
    gmail.add(BOX, "late-history-old", epoch=epoch(later - timedelta(seconds=1)))
    gmail.add(BOX, "late-history-new", epoch=epoch(later + timedelta(minutes=1)))
    assert check(api, later + timedelta(minutes=2)) == 2
    assert heard(api, aid) == ["late-history-new", "ordinary"]


def test_a_message_in_the_same_second_as_a_repaired_watch_still_counts(watched):
    api, gmail, aid = watched
    assert check(api, T0) == 0
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE mailboxes SET watching_since_at='invalid' WHERE address=?", (BOX,))
    repaired_at = T0 + timedelta(minutes=3, milliseconds=400)  # Gmail reports this second as a whole number
    gmail.add(BOX, "same-second", epoch=epoch(repaired_at.replace(microsecond=0)))
    check(api, repaired_at)
    assert heard(api, aid) == ["same-second"]


def test_intake_commits_before_the_cursor_and_replay_returns_the_same_run(watched, monkeypatch):
    api, gmail, _ = watched
    store = api.app.state.store
    with store.transaction() as c:
        ES.upsert(c, "human:ana", "person", match={"email": "person@customer.example"})
    raw = gmail.add(
        BOX, "first", thread="support", epoch=epoch(T0), labels=("INBOX", "Tico Intake"), headers=DMARC_PASS
    )
    sent_ms = epoch(T0) * 1000 + 125
    raw["internalDate"] = str(sent_ms)
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
        [saved] = c.execute("SELECT refs FROM messages WHERE source_message_id='first'").fetchall()
        assert json.loads(saved["refs"])["provider_timestamp_ms"] == sent_ms
        assert c.execute("SELECT count(*) FROM automation_runs WHERE trigger_ref LIKE 'intake:%'").fetchone()[0] == 1


def test_intake_does_not_use_the_date_header_as_provider_time(watched, monkeypatch):
    api, gmail, _ = watched
    check(api, T0)
    gmail.add(
        BOX,
        "header-only",
        thread="support",
        epoch=epoch(T0),
        labels=("INBOX", "Tico Intake"),
        headers={"Date": "Mon, 16 Nov 2026 12:00:00 +0000"},
    )

    def without_provider_time(url, **kwargs):
        status, found = gmail(url, **kwargs)
        if url.partition("?")[0].endswith("/messages/header-only"):
            found.pop("internalDate", None)
        return status, found

    monkeypatch.setattr(gmail_live, "gmail_call", without_provider_time)

    assert check(api, T0 + timedelta(minutes=2)) == 1
    with api.app.state.store.read() as c:
        [saved] = c.execute("SELECT refs FROM messages WHERE source_message_id='header-only'").fetchall()
        assert "provider_timestamp_ms" not in json.loads(saved["refs"])


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
        assert c.execute("SELECT count(*) FROM tasks WHERE occurrence_step='triage'").fetchone()[0] == 2


@pytest.mark.parametrize("stop_hours", [0, outside_intake.DEADLINE_HOURS + 1])
def test_stopped_inbox_keeps_saved_arrivals_and_hears_new_ones_after_reactivation(watched, monkeypatch, stop_hours):
    api, gmail, _ = watched
    store = api.app.state.store
    monkeypatch.setattr(google_workspace, "covers", lambda c, mailbox: True)
    labels = ("INBOX", "Tico Intake")
    gmail.add(BOX, "saved", epoch=epoch(T0), labels=labels)
    assert check(api, T0) == 1
    box = box_row(api)
    assert box["history_id"] == str(gmail.history_id(BOX))
    with store.transaction() as c:
        MA.stop(c, "human:ana", box["id"])
    later = T0 + timedelta(minutes=3)
    gmail.add(BOX, "while-stopped", epoch=epoch(later), labels=labels)
    with time_machine.travel(later, tick=False):
        assert store.automations.drain() == 1
        assert check(api, later) == 0
        assert box_row(api)["history_id"] == box["history_id"]
        with store.read() as c:
            run = c.execute("SELECT * FROM automation_runs WHERE trigger_ref LIKE 'intake:%'").fetchone()
            assert run["status"] == "held"
            assert json.loads(run["saved_state"])["payload"]["source_message_id"] == "saved"
            assert not c.execute("SELECT 1 FROM tasks WHERE occurrence_step='triage'").fetchone()
    resume_at = later + timedelta(hours=stop_hours)
    with time_machine.travel(resume_at, tick=False):
        automations.tick(store, resume_at, drain=False)
        assert store.automations.drain() == 0
        assert post(api, "mailboxes", {"address": BOX})["mailbox"]["id"] == box["id"]
        automations.tick(store, resume_at, drain=False)
        assert check(api, resume_at) == 1
        assert store.automations.drain() == 2
        assert box_row(api)["history_id"] == str(gmail.history_id(BOX))
    with store.read() as c:
        assert c.execute("SELECT status FROM automation_runs WHERE id=?", (run["id"],)).fetchone()[0] == "ok"
        tasks = c.execute("SELECT id,owner_id FROM tasks WHERE occurrence_step='triage'").fetchall()
        assert len(tasks) == 2 and all(task["owner_id"] == "human:ana" for task in tasks)
        text = "\n".join(H.task_words(c, task["id"]) for task in tasks)
        assert "/#all/saved)" in text and "/#all/while-stopped)" in text


def test_reconnecting_as_shared_keeps_the_saved_arrival_until_a_person_owns_the_inbox(api, monkeypatch):
    store = api.app.state.store
    address = "support@acme.example"
    monkeypatch.setattr(google_workspace, "covers", lambda c, mailbox: True)
    with time_machine.travel(T0, tick=False):
        with store.transaction() as c:
            box = MA.ensure_mailbox(c, address, person_id="ana", created_by="human:ana")
            aid = automations.INTAKE_AUTOMATION + box["id"]
            rid = automations.queue_intake(c, box["id"], "ana", "intake:saved", {"source_message_id": "saved"})
            MA.stop(c, "human:ana", box["id"])
        result = post(api, "mailboxes", {"address": address, "shared": True})["mailbox"]
        assert (result["id"], result["kind"]) == (box["id"], "shared")
        store.automations.drain()
        assert get(api, f"automations/{aid}/runs")["runs"][0]["status"] == "held"

    overdue = T0 + timedelta(hours=outside_intake.DEADLINE_HOURS + 1)
    with time_machine.travel(overdue, tick=False):
        automations.tick(store, overdue, drain=False)
        assert store.automations.drain() == 0
        with store.transaction() as c:
            run = c.execute("SELECT * FROM automation_runs WHERE id=?", (rid,)).fetchone()
            assert run["status"] == "held" and not run["finished_at"]
            assert json.loads(run["saved_state"])["payload"]["source_message_id"] == "saved"
            assert not c.execute("SELECT 1 FROM tasks WHERE occurrence_step='triage'").fetchone()
            MA.stop(c, "human:ana", box["id"])
        result = post(api, "mailboxes", {"address": address})["mailbox"]
        assert (result["id"], result["kind"]) == (box["id"], "personal")
        automations.tick(store, overdue, drain=False)
        assert store.automations.drain() == 0  # awaiting its person
        with store.transaction() as c:
            MA.link_person(c, "ana", address)
        automations.tick(store, overdue, drain=False)
        assert store.automations.drain() == 1

    with store.read() as c:
        assert c.execute("SELECT status FROM automation_runs WHERE id=?", (rid,)).fetchone()[0] == "ok"
        (task,) = c.execute("SELECT id,owner_id FROM tasks WHERE occurrence_step='triage'").fetchall()
        assert task["owner_id"] == "human:ana"
        assert "/#all/saved)" in H.task_words(c, task["id"])


@pytest.mark.parametrize("action, status", [("release", "ok"), ("drop", "skipped")])
def test_only_inbox_owner_ends_intake_hold_and_visibility_follows_mailbox(api, action, status):
    store = api.app.state.store
    with store.transaction() as c:
        box = MA.ensure_mailbox(c, "ben@acme.example", person_id="ben", created_by="human:ben")
        rid = automations.queue_intake(c, box["id"], "ben", "intake:held", {"source_message_id": "held"})
        aid = automations.INTAKE_AUTOMATION + box["id"]
        automations._hold(c, automations._row(c, aid), "flood")

    card = post(api, "cards", {"template": "automation_runs", "settings": {"team": ""}})["card"]["id"]

    def card_data():
        return get(api, f"cards?ids={card}")["cards"][0]["data"]

    def listed(token):
        rows = get(api, "automations", token=token)["automations"]
        return aid in {row["id"] for row in rows}

    assert not listed("ana-test")
    assert listed("ben-test")
    assert card_data() == {"total": 0, "failed": 0, "running": 0, "runs": []}
    assert get(api, f"automations/{aid}", token="ben-test")["can_end_hold"]
    get(api, f"automations/{aid}/runs", expected=404)
    assert get(api, f"automations/runs?ids={aid}")["runs"] == []
    post(api, f"automations/{aid}/held", {"action": action}, expected=404)
    with store.transaction() as c:
        grant = MA.grant(c, "human:ben", box["id"], "person", "ana", "read")

    assert listed("ana-test")
    assert not get(api, f"automations/{aid}")["can_end_hold"]
    data = card_data()
    assert data["total"] == 1 and data["runs"][0]["id"] == rid
    assert get(api, f"automations/{aid}/runs")["runs"][0]["id"] == rid
    assert get(api, f"automations/runs?ids={aid}")["runs"][0]["id"] == rid
    post(api, f"automations/{aid}/held", {"action": action}, expected=403)
    result = post(api, f"automations/{aid}/held", {"action": action}, token="ben-test")
    assert result["moved"] == 1 and result["readonly"] and not result["can_edit"] and result["can_end_hold"]
    store.automations.drain()
    assert get(api, f"automations/{aid}/runs", token="ben-test")["runs"][0]["status"] == status
    audit = f"audit?object=automation:{aid}&limit=1"
    assert get(api, audit)["rows"][0]["after"][action] == 1
    query = {"sql": "SELECT detail FROM events WHERE target=? AND action LIKE 'automation.hold_%'", "params": [aid]}
    assert len(post(api, "sql", query)["rows"]) == 1
    with store.transaction() as c:
        MA.revoke(c, "human:ben", box["id"], grant["id"])
    assert get(api, audit) == {"rows": [], "next": None}
    assert post(api, "sql", query)["rows"] == []
    assert get(api, audit, token="ben-test")["rows"][0]["after"][action] == 1


def test_intake_failed_step_rolls_back_and_drain_hands_it_to_the_owner(watched, monkeypatch):
    api, gmail, _ = watched
    store = api.app.state.store
    for mid in ("one", "two"):
        gmail.add(BOX, mid, epoch=epoch(T0), labels=("INBOX", "Tico Intake"))
    check(api, T0)
    process = outside_intake.process

    def fail_first(c, connection_id, ref, payload, started_at):
        status = process(c, connection_id, ref, payload, started_at)
        if payload["source_message_id"] == "one":
            c.execute("UPDATE tasks SET title='Uncommitted title' WHERE occurrence_step='triage'")
            raise RuntimeError("interrupted")
        return status

    monkeypatch.setattr(outside_intake, "process", fail_first)
    with time_machine.travel(T0, tick=False):
        assert store.automations.drain() == 2
    with store.read() as c:
        assert [r[0] for r in c.execute("SELECT status FROM automation_runs WHERE trigger_ref LIKE 'intake:%'")] == [
            "ok",
            "ok",
        ]
        tasks = list(c.execute("SELECT id,title,owner_id FROM tasks WHERE occurrence_step='triage'"))
        assert len(tasks) == 2 and all(r["title"] == "Triage unverified sender" for r in tasks)
        assert all(r["owner_id"] == "human:ana" for r in tasks)
        assert sum("Automatic processing could not finish" in H.task_words(c, r["id"]) for r in tasks) == 1


def test_intake_stale_claim_and_failed_handoff_requeue_the_same_run_once(watched, monkeypatch):
    api, gmail, _ = watched
    store = api.app.state.store
    gmail.add(BOX, "stale", epoch=epoch(T0), labels=("INBOX", "Tico Intake"))
    check(api, T0)
    with store.transaction() as c:
        run = c.execute("SELECT * FROM automation_runs WHERE trigger_ref LIKE 'intake:%'").fetchone()
        state = json.loads(run["saved_state"])
        state["resumes"] = automations.MAX_RESUMES
        c.execute(
            "UPDATE automation_runs SET status='running',claimed_at=?,claim_token='expired',saved_state=? WHERE id=?",
            (run["started_at"], json.dumps(state), run["id"]),
        )

    def unavailable(*args, **kwargs):
        raise RuntimeError("task creation unavailable")

    later = T0 + timedelta(minutes=automations.STALE_MINUTES + 1)
    with time_machine.travel(later, tick=False):
        automations.tick(store, later, drain=False)
        with monkeypatch.context() as patch:
            patch.setattr(outside_intake, "triage", unavailable)
            assert store.automations.drain() == 1
            with store.transaction() as c:
                assert (
                    c.execute("SELECT status FROM automation_runs WHERE id=?", (run["id"],)).fetchone()[0] == "failed"
                )
                connection = run["automation_id"].removeprefix(automations.INTAKE_AUTOMATION)
                assert automations.replay_intake(c, connection, run["trigger_ref"]) == run["id"]
                assert (
                    c.execute("SELECT status FROM automation_runs WHERE id=?", (run["id"],)).fetchone()[0] == "queued"
                )
            assert store.automations.drain() == 1
        with store.transaction() as c:
            assert automations.replay_intake(c, connection, run["trigger_ref"]) == run["id"]
            assert c.execute("SELECT status FROM automation_runs WHERE id=?", (run["id"],)).fetchone()[0] == "failed"
    retry_at = later + timedelta(hours=1)
    with time_machine.travel(retry_at, tick=False):
        automations.tick(store, retry_at, drain=False)
        assert store.automations.drain() == 1
    with store.read() as c:
        assert c.execute("SELECT status FROM automation_runs WHERE id=?", (run["id"],)).fetchone()[0] == "ok"
        assert c.execute("SELECT count(*) FROM tasks WHERE occurrence_step='triage'").fetchone()[0] == 1


def test_intake_verifies_exact_sender_and_keeps_unverified_mail_in_owner_triage(watched):
    api, gmail, ordinary = watched
    store = api.app.state.store
    with store.transaction() as c:
        possible = ES.upsert(c, "human:ana", "person", match={"email": "person@customer.example"})["id"]
    gmail.add(BOX, "one", thread="support", epoch=epoch(T0), labels=("INBOX", "Tico Intake"))
    check(api, T0)
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM conversations WHERE audience='entity'").fetchone()

    gmail.add(
        BOX,
        "two",
        thread="support",
        body="@ana @ops",
        epoch=epoch(T0 + timedelta(minutes=2)),
        labels=("INBOX", "Tico Intake"),
        headers={
            "Authentication-Results": "mx.google.com; dkim=pass header.i=@customer.example header.b=XyZa",
            "DKIM-Signature": "v=1; d=customer.example; b=XyZabcdef",
        },
    )
    check(api, T0 + timedelta(minutes=3))
    with time_machine.travel(T0 + timedelta(minutes=4), tick=False):
        assert heard(api, ordinary) == []
        store.automations.drain()
    with store.read() as c:
        thread = c.execute("SELECT * FROM conversations WHERE audience='entity'").fetchone()
        assert thread["entity_id"] == possible
        assert "ext:gmail:person@customer.example" in json.loads(thread["participants"])
        (task,) = c.execute("SELECT * FROM tasks WHERE occurrence_step='triage'")
        assert (task["owner_id"], task["assignee_id"], task["private"]) == ("human:ana", "human:ana", 1)
        assert not task["entity_id"]
        assert "unverified sender" in task["title"]
        assert H.parse_ts(task["due_at"]) == T0 + timedelta(days=1)
        assert not c.execute("SELECT 1 FROM messages WHERE source_message_id='one'").fetchone()
        two = c.execute("SELECT * FROM messages WHERE source_message_id='two'").fetchone()
        assert (two["conversation_id"], two["direction"], two["channel"]) == (thread["id"], "inbound", "gmail")
        for table in ("message_mentions", "message_deliveries", "jobs"):
            assert not c.execute(f"SELECT 1 FROM {table} WHERE message_id=?", (two["id"],)).fetchone()


def test_intake_ignores_machines_and_does_not_suggest_public_mail_domains(watched):
    api, gmail, ordinary = watched
    store = api.app.state.store
    with store.transaction() as c:
        ES.upsert(c, "human:ana", "organization", match={"domain": "gmail.com"})
    for mid, sender, facts in (
        ("auto", "person@customer.example", {"Auto-Submitted": "auto-replied"}),
        ("list", "person@customer.example", {"List-Id": "support.customer.example"}),
        ("bulk", "person@customer.example", {"List-Unsubscribe": "<mailto:list@customer.example>"}),
        ("bounce", "mailer-daemon@customer.example", {}),
        ("null-path", "person@customer.example", {"Return-Path": "<>"}),
        ("own", BOX, {}),
        ("free", "someone@gmail.com", {"Authentication-Results": "mx.google.com; dmarc=pass header.from=gmail.com"}),
    ):
        gmail.add(
            BOX,
            mid,
            sender=sender,
            cc=("outside@customer.example",),
            epoch=epoch(T0),
            labels=("INBOX", "Tico Intake"),
            headers=facts,
        )
    check(api, T0)
    with time_machine.travel(T0, tick=False):
        assert heard(api, ordinary) == []
        store.automations.drain()
    with store.read() as c:
        (run,) = c.execute("SELECT id FROM automation_runs WHERE trigger_ref LIKE 'intake:%'")
        assert run["id"]
        (task,) = c.execute("SELECT * FROM tasks WHERE occurrence_step='triage'")
        assert task["title"] == "Triage outside conversation"
        text = H.task_words(c, task["id"])
        assert "/#all/free" in text
        assert "Possible contact" not in text
        assert not ES.matching(c, "organization", {"domain": "customer.example"})


def test_intake_distrusts_other_domains_and_hands_over_held_arrivals_at_deadline(watched):
    api, gmail, _ = watched
    store = api.app.state.store
    check(api, T0)
    with store.transaction() as c:
        ES.upsert(c, "human:ana", "person", match={"email": "person@customer.example"})
        c.execute("UPDATE mailboxes SET watching_since_at='invalid' WHERE address=?", (BOX,))
    arrival = T0 + timedelta(minutes=3)
    for mid, facts in (
        ("upstream", {"Authentication-Results": "other.example; dmarc=pass header.from=customer.example"}),
        ("wrong-signer", {"Authentication-Results": "mx.google.com; dkim=pass header.d=other.example"}),
        ("duplicate-from", {**DMARC_PASS, "from": "person@other.example"}),
    ):
        gmail.add(BOX, mid, epoch=epoch(arrival), labels=("INBOX", "Tico Intake"), headers=facts)
    assert check(api, arrival) == 3
    with time_machine.travel(arrival, tick=False):
        with store.transaction() as c:
            aid = c.execute("SELECT automation_id FROM automation_runs WHERE trigger_ref LIKE 'intake:%'").fetchone()[0]
            automations._hold(c, automations._row(c, aid), "flood")
        assert store.automations.drain() == 0
    deadline = arrival + timedelta(hours=outside_intake.DEADLINE_HOURS)
    with time_machine.travel(deadline, tick=False):
        automations.tick(store, deadline, drain=False)
        assert store.automations.drain() == 3
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM conversations WHERE audience='entity'").fetchone()
        tasks = c.execute("SELECT title,owner_id FROM tasks WHERE occurrence_step='triage'").fetchall()
        assert len(tasks) == 3 and all(tuple(task) == ("Triage unverified sender", "human:ana") for task in tasks)


def test_entity_and_inbox_gate_threads_subthreads_and_history_before_paging(watched):
    api, _, _ = watched
    store = api.app.state.store
    mailbox = box_row(api)["id"]
    with store.transaction() as c:
        entity = ES.upsert(c, "human:ana", "person", match={"email": "person@customer.example"})["id"]
        outside_intake.save_message(
            c,
            channel="gmail",
            connection_id=mailbox,
            owner_id="ana",
            source_thread_id="support",
            source_message_id="verified-delivery",
            sender="person@customer.example",
            verified_id="person@customer.example",
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


def test_bot_standing_grants_cannot_read_outside_threads_or_export_related_runs(watched):
    api, _, _ = watched
    store = api.app.state.store
    _, chat, attempt = setup_attempt(api)
    aid, token = attempt["id"], attempt["token"]
    mailbox = box_row(api)["id"]
    with store.transaction() as c:
        entity = ES.upsert(c, "human:ana", "person", match={"email": "person@customer.example"})["id"]
        root = threads._insert_thread(
            c,
            "ext:gmail:person@customer.example",
            title="Private",
            audience="entity",
            entity_id=entity,
            connection_id=mailbox,
        )["id"]
        MA.grant(c, "human:ana", mailbox, "bot", "ops", "read")
        c.execute(
            "INSERT INTO attempt_captures(attempt_id,bot,provider,frames,bytes,created_at) "
            "VALUES(?,'ops','claude','{}',2,?)",
            (aid, H.now()),
        )
    assert get(api, f"attempts/{aid}/capture")["run"]["id"] == aid
    with store.transaction() as c:
        c.execute("INSERT INTO attempt_conversations VALUES(?,?)", (aid, root))
        from backend.auth import Identity

        viewer = threads.viewer(None, Identity("bot:ops", "bot", attempt_id=aid))
        assert not threads.readable(c, viewer, root)
        assert threads.readable_ids(c, viewer, [root, chat["conversation_id"]]) == {chat["conversation_id"]}
        sql = threads.readable_sql(c, viewer, "t")
        assert c.execute(f"SELECT 1 FROM conversations t WHERE t.id=? AND ({sql})", (root,)).fetchone() is None
    get(api, f"threads/{root}/timeline", token=token, expected=404)
    get(api, f"attempts/{aid}/capture", expected=404)
    post(api, f"threads/{chat['conversation_id']}/members", {"actor": "ext:gmail:unknown@other.example"}, expected=404)


def test_execution_review_redacts_outside_subthread_but_keeps_recovery_controls(api):
    store = api.app.state.store
    _, message, attempt = interrupted(api)
    with store.transaction() as c:
        box = MA.ensure_mailbox(c, "ben@acme.example", person_id="ben", created_by="human:ben")
        entity = ES.upsert(c, "human:ben", "person", match={"email": "person@customer.example"})["id"]
        root = threads._insert_thread(
            c,
            "ext:gmail:person@customer.example",
            title="Private",
            audience="entity",
            entity_id=entity,
            connection_id=box["id"],
        )["id"]
        c.execute(
            "UPDATE conversations SET root_id=?,parent_id=?,participants='[]' WHERE id=?",
            (root, root, message["conversation_id"]),
        )
    result = get(api, "bots/ops/execution-review")["jobs"][0]
    assert result["id"] == attempt["job_id"]
    assert not result["readable"] and result["request"] == "" and result["output"] == result["tools"] == []
    assert result["stopped"] and result["reason"]
    with store.transaction() as c:
        MA.grant(c, "human:ben", box["id"], "person", "ana", "read")
    result = get(api, "bots/ops/execution-review")["jobs"][0]
    assert result["readable"] and result["request"] and result["output"]


def test_history_checks_external_source_and_subthread_root_before_pagination(watched):
    api, _, _ = watched
    store = api.app.state.store
    mailbox = box_row(api)["id"]
    with store.transaction() as c:
        root = ES.upsert(c, "human:ana", "person", match={"email": "person@customer.example"})["id"]
        thread = threads._insert_thread(
            c,
            "ext:gmail:person@customer.example",
            title="Private",
            audience="entity",
            entity_id=root,
            connection_id=mailbox,
        )
        c.execute(
            "INSERT INTO tools(id,name,created_by,created_at,updated_at) "
            "VALUES('source-tool','Outside source','human:ana',?,?)",
            (H.now(), H.now()),
        )
        c.execute(
            "INSERT INTO credentials(id,name,kind,tool_id,created_at,updated_at,updated_by) "
            "VALUES('source-key','Source key','api_key','source-tool',?,?,'human:ana')",
            (H.now(), H.now()),
        )
        source = ES.external_ref(c, "human:ana", root, "close", "activity", "case-1", connection_id="source-tool")
        c.execute(
            "INSERT INTO entity_external_refs(id,entity_id,connection_id,system,external_type,external_id) "
            "VALUES('gmail-ref',?,?,'gmail','message','mail-1')",
            (root, mailbox),
        )
        parent = H._write_message(
            c,
            "ext:gmail:person@customer.example",
            None,
            "Private email",
            thread,
            "say",
            {},
            None,
            None,
            outside=("gmail", mailbox, "mail-1"),
        )
    reply = post(api, f"threads/{thread['id']}/messages", {"text": "Internal note", "parent_message_id": parent["id"]})
    with store.transaction() as c:
        c.executemany(
            "INSERT INTO entity_activities(id,entity_id,kind,occurred_at,subject_type,subject_id,external_ref) "
            "VALUES(?,?,'email',?,?,?,?)",
            (
                ("child", root, "2028-01-02", "thread", reply["thread_id"], "gmail-ref"),
                ("external", root, "2028-01-03", None, None, source["id"]),
                ("visible", root, "2028-01-01", None, None, None),
            ),
        )
    path = f"entities/activity?ref={root}&limit=1"
    assert [row["id"] for row in get(api, path, token="ben-test")["rows"]] == ["visible"]
    first = get(api, path)
    assert [row["id"] for row in first["rows"]] == ["child"]
    assert [row["id"] for row in get(api, f"{path}&after={first['next']}")["rows"]] == ["visible"]
    with store.transaction() as c:
        c.execute(
            "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at,access_level) "
            "VALUES('ben-source','source-key','human:ben','human:ana',?,'read')",
            (H.now(),),
        )
    first = get(api, path, token="ben-test")
    assert [row["id"] for row in first["rows"]] == ["external"]
    assert [row["id"] for row in get(api, f"{path}&after={first['next']}", token="ben-test")["rows"]] == ["visible"]


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
