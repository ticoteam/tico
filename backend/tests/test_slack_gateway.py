"""The Slack gateway (`backend/slack_gateway.py`, `docs/slack-gateway.md`).

Slack and the judge are fakes; nothing here touches the network. The events, threads, posts,
conversations, messages and jobs go through the real store and write layer, because the
guardrails are the point.
"""

import json
import logging
import sqlite3
import threading
from importlib import import_module
from types import SimpleNamespace

import pytest

from backend import outbound_actions as OA, outbound_slack as OS
from backend import hubdb as H
from backend import slack_gateway as G
from backend import slack_requests as SR
from backend.config import Settings
from backend.store import Store, encode
from clients import judge as J
from backend.tests.helpers.slack_gateway import (
    APP,
    BOT_USER,
    CHANNELS,
    COUNTER,
    CappedJudge,
    Clock,
    DM,
    FakeSlack,
    MARKETING,
    QUIET,
    RELEASE,
    TEAM,
    add_bots,
    answers,
    channel_message,
    envelope,
    gateway,
    hub,
    readers,
    rows,
    send,
)


def events(hub):
    return rows(hub, "SELECT * FROM slack_events ORDER BY ts")


def jobs(hub):
    return rows(hub, "SELECT bot_id,state FROM jobs ORDER BY created_at")


# ----------------------------------------------------------------------------- start-up
def test_startup_refuses_another_workspace_or_app_and_reports_missing_scopes(hub):
    with pytest.raises(RuntimeError, match="workspace"):
        G.Gateway(hub, FakeSlack(team="T0OTHER")).verify_app()
    with pytest.raises(RuntimeError, match="app"):
        G.Gateway(hub, FakeSlack(app="A0OTHER")).verify_app()
    with pytest.raises(RuntimeError, match="SLACK_TEAM_ID"):
        G.Gateway(Store(Settings(db_path=hub.settings.db_path)), FakeSlack()).verify_app()
    limited = G.Gateway(
        hub,
        FakeSlack(
            scopes=("chat:write", "im:history", "channels:history", "groups:history", "users:read", "users:read.email")
        ),
    )
    verified = limited.verify_app()
    assert verified == {
        "team_id": TEAM,
        "app_id": APP,
        "bot_user_id": BOT_USER,
        "missing_scopes": ["app_mentions:read", "chat:write.customize", "files:read", "links:read", "links:write"],
    }
    assert limited.customize is False


# ----------------------------------------------------------------------------- verify and deny
def test_only_verified_admitted_roster_humans_in_internal_channels_get_through(gateway, hub):
    slack = gateway.slack
    # Cheap filters before anything is persisted: not our workspace, a bot, an edit, a group DM.
    assert gateway.receive(envelope("hi", team="T0OTHER")) == "foreign"
    assert gateway.receive(envelope("hi", bot_id="B0X")) == "bot_or_subtype"
    assert gateway.receive(envelope("hi", subtype="message_changed")) == "bot_or_subtype"
    assert gateway.receive(envelope("hi", user=BOT_USER)) == "bot_or_subtype"
    assert (
        gateway.receive(
            {
                **envelope("hi", kind="message"),
                "event": {**envelope("hi", kind="message")["event"], "channel_type": "mpim"},
            }
        )
        == "not_dm"
    )
    # A plain channel message is stored for the channel's readers, never routed (see the channel tests).
    assert (
        gateway.receive(
            {
                **envelope("hi", kind="message"),
                "event": {**envelope("hi", kind="message")["event"], "channel_type": "channel"},
            }
        )
        == "stored"
    )
    assert [e["state"] for e in events(hub)] == ["stored"] and slack.calls == []
    with hub.transaction() as c:
        c.execute("DELETE FROM slack_events")
    # Persisted, then denied with the reason, and nobody is replied to.
    denied = {
        "guest": send(gateway, "@Tico hello", user="U5"),
        "deactivated": send(gateway, "@Tico hello", user="U6"),
        "bot user": send(gateway, "@Tico hello", user="U7"),
        "not admitted": send(gateway, "@Tico hello", user="U3"),
        "not on roster": send(gateway, "@Tico hello", user="U4"),
        "unknown user": send(gateway, "@Tico hello", user="U9"),
        "slack connect": send(gateway, "@Tico hello", channel="C0EXT"),
        "other workspace": send(gateway, "@Tico hello", user="U8"),
        "other workspace, by DM": send(gateway, "hello", user="U8", channel=DM, kind="message"),
    }
    reasons = {name: result["reason"] for name, result in denied.items()}
    assert reasons == {
        "guest": "sender is a guest",
        "deactivated": "sender is deactivated",
        "bot user": "sender is a bot",
        "not admitted": "sender is not admitted by hub-access",
        "not on roster": "sender is not on the roster",
        "unknown user": "users.info: user_not_found",
        "slack connect": "channel is shared outside the workspace",
        "other workspace": "sender is outside the workspace",
        "other workspace, by DM": "sender is outside the workspace",
    }
    assert not any(call == ("conversations.info", DM) for call in slack.calls), "a DM is never looked up as a channel"
    assert {e["state"] for e in events(hub)} == {"denied"}
    assert slack.posts == [] and jobs(hub) == []
    assert rows(hub, "SELECT count(*) AS n FROM conversations")[0]["n"] == 0
    # A verified person goes through: a fresh users.info each time, never a cache.
    before = len([call for call in slack.calls if call[0] == "users.info"])
    result = send(gateway, "<@U0BUS6WA4SY> can Legal check this clause?", answers(legal=0.9))
    assert result["state"] == "routed"
    assert len([call for call in slack.calls if call[0] == "users.info"]) == before + 1
    event = events(hub)[-1]
    assert event["actor"] == "human:ana" and event["text"] == "@Tico can Legal check this clause?"


def test_an_event_is_persisted_once_however_often_slack_delivers_it(gateway, hub):
    payload = envelope("<@U0BUS6WA4SY> ping", event_id="Ev1")
    assert gateway.receive(payload) == "queued"
    assert gateway.receive(payload) == "duplicate"
    # The same message under another event id (a DM that also mentions Tico arrives twice).
    twin = {**payload, "event_id": "Ev2"}
    assert gateway.receive(twin) == "duplicate"
    gateway.engine.answers.append(answers(legal=0.9))
    gateway.tick()
    assert [e["event_id"] for e in events(hub)] == ["Ev1"]
    assert len(jobs(hub)) == 1
    # A retry after routing changes nothing either.
    assert gateway.receive(payload) == "duplicate"
    assert gateway.tick()["events"] == []


def test_a_stored_message_and_its_edit_name_the_people_they_mention_asking_slack_once_an_hour(gateway, hub):
    """The app shows the stored text, so each `<@U…>` is stored as the person's name; a user Slack cannot answer for
    keeps the id. The lookups share the bridge's users.info cache, so a busy channel costs no call per message."""
    slack = gateway.slack
    said = f"<@U2> and <@U9> please check, <@{BOT_USER}> too"
    assert gateway.receive(channel_message(said, ts="1690000500.000100")) == "stored"
    assert events(hub)[-1]["text"] == "@Ben and @U9 please check, @Tico too"
    assert gateway.receive(channel_message("<@U2> again", ts="1690000501.000100")) == "stored"
    assert events(hub)[-1]["text"] == "@Ben again"
    edit = channel_message("", ts="1690000502.000100", subtype="message_changed")
    edit["event"]["message"] = {"ts": "1690000500.000100", "text": "<@U2> please check", "user": "U1"}
    assert gateway.receive(edit) == "edited"
    assert rows(hub, "SELECT text FROM slack_events WHERE ts='1690000500.000100'")[0]["text"] == "@Ben please check"
    asked = [call[1] for call in slack.calls if call[0] == "users.info"]
    assert asked.count("U2") == 1 and BOT_USER not in asked
    gateway.clock.advance(3601)
    assert gateway.receive(channel_message("<@U2> later", ts="1690000503.000100")) == "stored"
    assert [call[1] for call in slack.calls if call[0] == "users.info"].count("U2") == 2


# ----------------------------------------------------------------------------- routing
# ----------------------------------------------------------------------------- egress
def _thread_of_reply(hub, message_id):
    """The Slack thread whose conversation holds this message: in a shared hub, the thread that check routed."""
    [thread] = rows(
        hub,
        "SELECT t.* FROM slack_threads t JOIN messages m ON m.conversation_id = t.conversation_id WHERE m.id=?",
        message_id,
    )
    return thread


def routed_reply(gateway, hub, text="Done."):
    before = {r["conversation_id"] for r in rows(hub, "SELECT conversation_id FROM slack_threads")}
    send(gateway, "<@U0BUS6WA4SY> Legal?", answers(legal=0.9))
    # The thread this message routed (created_at does not tell threads apart: the clock may not move between them).
    [thread] = [t for t in rows(hub, "SELECT * FROM slack_threads") if t["conversation_id"] not in before]
    with hub.transaction() as c:
        message = rows(hub, "SELECT id FROM messages WHERE conversation_id=?", thread["conversation_id"])[0]
        reply = H.say(
            c, "bot:legal", "human:ana", text, conversation_id=thread["conversation_id"], in_reply_to=message["id"]
        )
    return reply["id"]


def test_a_slack_reply_answers_no_ask_and_the_bot_hears_it_once(gateway, hub):
    """A ❓ is answered on its task's chat: Ana's Slack follow-up to the bot's question reaches the bot once, as her
    message, and the ❓ stays open in her Needs you."""
    from backend import question_marks, waiting_on

    send(gateway, "<@U0BUS6WA4SY> Legal?", answers(legal=0.9))
    thread = rows(hub, "SELECT * FROM slack_threads")[0]
    event = {"channel": thread["channel"], "thread_ts": thread["thread_ts"]}
    with hub.transaction() as c:
        review = H.task_create(c, "human:ana", "Review the contract", "Read it.", "bot:legal", lint=False)
        asked = question_marks.ask(c, "bot:legal", review["id"], "human:ana", "Which state's law applies?")
        H.say(c, "bot:legal", "human:ana", "Which state's law applies?", conversation_id=thread["conversation_id"])
        mark = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
        G.say_to_bot(c, "human:ana", "legal", event, "Delaware.", {}, H.now())
    with hub.read() as c:
        assert [
            r[0] for r in c.execute("SELECT body FROM messages WHERE recipient_id='bot:legal' AND rowid>?", (mark,))
        ] == ["Delaware."]
        woke = c.execute(
            "SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE m.recipient_id='bot:legal' AND m.rowid>?",
            (mark,),
        ).fetchone()[0]
        assert woke == 1, woke
        asks = [n["id"] for n in waiting_on.needs(c, "human:ana") if n["kind"] == "question"]
        assert asks == [question_marks.wait_id(asked)]


class BothJudges:
    """The org rule check's judge for its own `rule-check` calls, and the decision engine for the rest."""

    def __init__(self, rules, engine):
        self.rules, self.engine = rules, engine

    def __call__(self, state, questions, label=None):
        if str(label or "").startswith("rule-check"):
            return self.rules(state, questions, label)
        return self.engine(state, questions, label)


def test_a_direct_message_the_org_rules_hold_reaches_no_bot_until_the_hold_is_sent(gateway, hub):
    """An org rule the message breaks holds its copy before any bot has it: nothing is written to a bot's conversation,
    and the hold waits for its author."""
    from backend import org_rules as R
    from backend.auth import Auth, Identity
    from backend.tests.helpers.org_rule_check import COLOR, Judge

    owner = Identity("human:ben", "owner", email="ben@acme.example")
    with hub.transaction() as c:
        R.add_rule(
            c,
            Auth(hub),
            owner,
            {"kind": "internal", "text": COLOR, "who": {"everyone": True}, "with": {}, "if_broken": "block"},
        )
    gateway._judge = BothJudges(Judge(hub), gateway.engine)
    send(gateway, "The favorite color is teal", judge_answer=answers(legal=0.9), channel=DM, kind="message")
    assert rows(hub, "SELECT id FROM messages WHERE sender_id='human:ana'") == []
    assert len(rows(hub, "SELECT id FROM org_rule_holds")) == 1


@pytest.mark.parametrize("held", ["paused", "quarantined"])
def test_a_persons_new_thread_to_a_bot_held_since_the_roster_was_read_is_kept_and_said_so(gateway, hub, held):
    """Legal is paused or quarantined while the decision model reads the roster: the new thread's message is kept for
    Legal, the person is told in Slack that it waits, and it is one queued job that runs when Legal is back."""

    def hold(state):
        with hub.transaction() as c:
            if held == "quarantined":
                H.quarantine(c, "legal", "10 repeated refusals today")
            else:
                c.execute("UPDATE bots SET state='paused' WHERE slug='legal'")
        return answers(legal=0.9)

    result = send(gateway, "please review the vendor terms", judge_answer=hold, channel=DM, kind="message")
    assert result["state"] == "routed", result
    (kept,) = result["decision"]["delivered"]
    assert kept["bot"] == "legal" and kept["opened"] and result["decision"]["dropped"] == []
    assert "Your message is kept for when it is back" in kept["held"]
    assert ("until about" if held == "quarantined" else "until a person resumes it") in kept["held"]
    assert [p["text"] for p in gateway.slack.posts] == [kept["held"]] and gateway.slack.posts[0]["channel"] == DM
    message = rows(hub, "SELECT * FROM messages WHERE id=?", kept["message_id"])[0]
    assert (message["sender_id"], message["recipient_id"], message["body"]) == (
        "human:ana",
        "bot:legal",
        "please review the vendor terms",
    )
    assert rows(hub, "SELECT bot_id,state FROM jobs WHERE message_id=?", kept["message_id"]) == [
        {"bot_id": "legal", "state": "queued"}
    ]
    # Nothing else woke up for it, and it is not sent again: the assistant got nothing, one job waits for Legal.
    assert jobs(hub) == [{"bot_id": "legal", "state": "queued"}]
    with hub.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug='legal'")
    assert gateway.tick()["events"] == [] and jobs(hub) == [{"bot_id": "legal", "state": "queued"}]


def test_a_new_thread_to_a_bot_that_is_gone_is_still_dropped_and_the_assistant_asks(gateway, hub):
    def archive(state):
        with hub.transaction() as c:
            c.execute("UPDATE bots SET state='archived' WHERE slug='legal'")
        return answers(legal=0.9)

    result = send(gateway, "please review the vendor terms", judge_answer=archive, channel=DM, kind="message")
    assert [d["bot"] for d in result["decision"]["dropped"]] == ["legal"]
    assert [d["bot"] for d in result["decision"]["delivered"]] == ["assistant"] and gateway.slack.posts == []


def test_a_post_is_attempted_once_and_a_crash_between_send_and_record_is_uncertain(gateway, hub):
    slack, clock = gateway.slack, gateway.clock
    mid = routed_reply(gateway, hub)
    slack.failures.append(G.SlackUnreachable("Slack chat.postMessage: TimeoutError"))
    gateway.tick()
    with hub.read() as c:
        post = OA.get(c, mid)
        assert post["state"] == "unknown" and post["attempts"] == 1 and post["provider_attempted_at"]
        assert post["audience"] == "internal"
    gateway.tick()
    assert not slack.posts
    with hub.transaction() as c:
        assert OA.get(c, mid)["attempts"] == 1
        limited = H.say(
            c,
            "bot:legal",
            "human:ana",
            "Another reply.",
            conversation_id=rows(hub, "SELECT conversation_id FROM slack_threads")[0]["conversation_id"],
        )["id"]
    slack.failures.append(G.SlackError("chat.postMessage", "ratelimited", retry_after=5))
    gateway.tick()
    with hub.read() as c:
        post = OA.get(c, limited)
        assert post["state"] == "queued" and post["last_error"] == "ratelimited" and not post["provider_attempted_at"]
        assert post["attempts"] == 1 and post["next_attempt_at"] == H.shift(clock(), seconds=5)
    gateway.tick()
    assert not slack.posts, "not before Retry-After"
    clock.advance(5)
    gateway.tick()
    with hub.read() as c:
        post = OA.get(c, limited)
        assert post["state"] == "done" and post["attempts"] == 2 and post["payload"] == ""
        assert json.loads(post["receipt"]) == {"ts": "1700000001.000100"}
    with hub.transaction() as c:
        failed = H.say(
            c,
            "bot:legal",
            "human:ana",
            "One more thing.",
            conversation_id=rows(hub, "SELECT conversation_id FROM slack_threads")[0]["conversation_id"],
        )["id"]
    slack.failures.append(G.SlackError("chat.postMessage", "channel_not_found"))
    gateway.tick()
    with hub.read() as c:
        assert OA.get(c, failed)["state"] == "failed"
    gateway.tick()
    assert len(slack.posts) == 1


def copy_legacy_post(gateway, hub, state, *, migrated=True):
    mid = routed_reply(gateway, hub)
    thread = _thread_of_reply(hub, mid)
    migration = import_module("backend.migrations.20261122144410_slack_outbound")
    with hub.transaction() as c:
        post = c.execute(
            "INSERT INTO slack_posts(message_id,channel,thread_ts,bot,text,state,attempts,created_at,updated_at) "
            "VALUES(?,?,?,'legal','Frozen legacy reply',?,?,?,?) RETURNING *",
            (
                mid,
                thread["channel"],
                thread["thread_ts"],
                state,
                int(state == "sending"),
                gateway.clock(),
                gateway.clock(),
            ),
        ).fetchone()
        if migrated:
            migration._copy(c, dict(post), H.shift(gateway.clock(), minutes=2))
    return mid


def claim_legacy_post(c, mid, at):
    # The old gateway checks only these states when its Slack preflight returns.
    return c.execute(
        "UPDATE slack_posts SET state='sending',attempts=attempts+1,updated_at=? "
        "WHERE message_id=? AND state IN ('ready','rate_limited')",
        (at, mid),
    ).rowcount


@pytest.mark.parametrize("prior", ["ready", "sending"])
def test_tick_adopts_an_old_gateway_post_sent_during_cutover(gateway, hub, prior):
    mid = copy_legacy_post(gateway, hub, prior)
    expected = "queued" if prior == "ready" else "unknown"
    assert gateway.tick()["posts"] == []
    with hub.read() as c:
        assert OA.get(c, mid)["state"] == expected

    gateway.clock.advance(60)
    ts = "1700000050.000100"
    with hub.transaction() as c:
        c.execute(
            "UPDATE slack_posts SET state='sent',slack_ts=?,attempts=1,updated_at=? WHERE message_id=?",
            (ts, gateway.clock(), mid),
        )
        H.event(c, "bot:legal", "slack.post", mid, {"state": "sent"})
    assert gateway.tick()["posts"] == []
    with hub.read() as c:
        post = OA.get(c, mid)
        assert post["state"] == "done" and post["attempts"] == 1 and not post["provider_attempted_at"]
        assert not post["payload"] and json.loads(post["receipt"]) == {"ts": ts}
        assert json.loads(post["metadata"])["_tico"]["sent_text"] == "Frozen legacy reply"
        assert not OA.asked(post).get("slack_legacy")
        assert (
            c.execute("SELECT count(*) FROM events WHERE action='slack.post' AND target=?", (mid,)).fetchone()[0] == 1
        )
    gateway.clock.advance(61)
    assert gateway.tick()["posts"] == [] and not gateway.slack.posts


@pytest.mark.parametrize(
    ("legacy_state", "state"), [("sent", "done"), ("uncertain", "unknown"), ("sending", "unknown")]
)
def test_permit_reads_the_legacy_post_after_the_tick_started(gateway, hub, monkeypatch, legacy_state, state):
    mid = copy_legacy_post(gateway, hub, "ready")
    evidence = gateway.post_evidence
    ts = "1700000050.000100" if legacy_state == "sent" else None

    def finish_old_post(channel):
        with hub.transaction() as c:
            assert claim_legacy_post(c, mid, gateway.clock()) == 1
            if legacy_state != "sending":
                c.execute(
                    "UPDATE slack_posts SET state=?,slack_ts=?,updated_at=? WHERE message_id=?",
                    (legacy_state, ts, gateway.clock(), mid),
                )
        return evidence(channel)

    monkeypatch.setattr(gateway, "post_evidence", finish_old_post)
    gateway.clock.advance(119)
    assert gateway.tick()["posts"] == []
    gateway.clock.advance(1)
    assert gateway.tick()["posts"] == [{"message_id": mid, "state": "sent" if ts else "uncertain", "slack_ts": ts}]
    with hub.read() as c:
        post = OA.get(c, mid)
        assert post["state"] == state and post["attempts"] == 1 and not post["provider_attempted_at"]
    gateway.clock.advance(OA.STALE + 1)
    assert gateway.tick()["posts"] == [] and not gateway.slack.posts


def test_the_new_permit_fences_the_old_gateway_claim(gateway, hub, monkeypatch):
    mid = copy_legacy_post(gateway, hub, "ready")
    post_message = gateway.slack.post_message

    def post_with_old_gateway_waiting(*args, **kwargs):
        with hub.transaction() as c:
            assert OA.get(c, mid)["provider_attempted_at"]
            assert claim_legacy_post(c, mid, gateway.clock()) == 0
            old = c.execute("SELECT state,error FROM slack_posts WHERE message_id=?", (mid,)).fetchone()
            assert tuple(old) == ("canceled", "migrated")
        return post_message(*args, **kwargs)

    monkeypatch.setattr(gateway.slack, "post_message", post_with_old_gateway_waiting)
    gateway.clock.advance(120)
    assert gateway.tick()["posts"] == [{"message_id": mid, "state": "sent", "slack_ts": "1700000001.000100"}]
    assert gateway.tick()["posts"] == [] and len(gateway.slack.posts) == 1


def test_discovers_posts_written_by_an_old_gateway_after_cutover(gateway, hub):
    assert gateway.deliver() == []
    mid = copy_legacy_post(gateway, hub, "ready", migrated=False)
    assert gateway.originate() == gateway.mirror() == 0
    assert gateway.deliver() == []
    thread = _thread_of_reply(hub, mid)
    with hub.transaction() as c:
        row = OA.get(c, mid)
        assert row["state"] == "queued" and row["destination"] == thread["channel"]
        assert claim_legacy_post(c, mid, gateway.clock()) == 1
        ts = "1700000050.000100"
        c.execute("UPDATE slack_posts SET state='sent',slack_ts=? WHERE message_id=?", (ts, mid))
    gateway.clock.advance(121)
    assert gateway.deliver() == [] and not gateway.slack.posts
    with hub.read() as c:
        row = OA.get(c, mid)
        assert row["state"] == "done" and json.loads(row["receipt"]) == {"ts": ts}
        lines = gateway.exchanges(c, thread["channel"], thread["thread_ts"])
        assert next(line["text"] for line in lines if line["ts"] == ts) == "Frozen legacy reply"


@pytest.mark.parametrize("prior", ["ready", "sending"])
def test_cutover_keeps_a_later_retry_after_from_the_old_gateway(gateway, hub, prior):
    mid = copy_legacy_post(gateway, hub, prior)
    if prior == "sending":
        with hub.read() as c:
            assert OA.get(c, mid)["state"] == "unknown"
    retry_at = H.shift(gateway.clock(), seconds=180)
    gateway.clock.advance(60)
    with hub.transaction() as c:
        c.execute(
            "UPDATE slack_posts SET state='rate_limited',attempts=1,next_attempt_at=?,updated_at=? WHERE message_id=?",
            (retry_at, gateway.clock(), mid),
        )
    assert gateway.tick()["posts"] == []
    with hub.read() as c:
        saved = OA.get(c, mid)
        assert saved["state"] == "queued" and saved["next_attempt_at"] == retry_at
        assert saved["provider_attempted_at"] is None
    gateway.clock.advance(119)
    assert gateway.tick()["posts"] == [] and not gateway.slack.posts
    gateway.clock.advance(1)
    assert gateway.tick()["posts"] == [{"message_id": mid, "state": "sent", "slack_ts": "1700000001.000100"}]
    with hub.read() as c:
        assert OA.get(c, mid)["attempts"] == 2


@pytest.mark.parametrize("legacy_state", ["ready", "retry", "rate_limited", "sent"])
def test_redaction_scrubs_legacy_only_posts_and_preserves_receipts(gateway, hub, legacy_state):
    mid = copy_legacy_post(gateway, hub, legacy_state, migrated=False)
    ts = "1700000050.000100" if legacy_state == "sent" else None
    with hub.transaction() as c:
        c.execute("UPDATE slack_posts SET slack_ts=? WHERE message_id=?", (ts, mid))
        OS.withdraw(c, mid)
        assert claim_legacy_post(c, mid, gateway.clock()) == 0
        old = c.execute("SELECT state,text,slack_ts FROM slack_posts WHERE message_id=?", (mid,)).fetchone()
        assert tuple(old) == ("sent" if ts else "canceled", "", ts)
    assert gateway.deliver() == [] and not gateway.slack.posts
    with hub.read() as c:
        saved = OA.get(c, mid)
        assert saved["state"] == ("done" if ts else "canceled")
        assert json.loads(saved["receipt"]).get("ts") == ts
        assert "Frozen legacy reply" not in saved["metadata"] and not saved["payload"]


@pytest.mark.filterwarnings("ignore:.*backend.slack_gateway.*found in sys.modules:RuntimeWarning")
def test_module_entrypoint_requeues_a_slack_429(gateway, hub, monkeypatch):
    import runpy
    import sys

    import httpx

    from backend import outbound_http

    monkeypatch.setattr(sys, "argv", ["slack_gateway"])
    monkeypatch.setattr(Settings, "from_env", lambda: SimpleNamespace(slack_gateway_enabled=False))
    exited = []
    monkeypatch.setattr(sys, "exit", exited.append)
    production = runpy.run_module("backend.slack_gateway", run_name="__main__")
    assert exited == [0]
    mid = routed_reply(gateway, hub)
    gateway.mirror()
    real_client = production["SlackAPI"]("synthetic", "synthetic")
    monkeypatch.setattr(gateway.slack, "post_message", real_client.post_message)
    monkeypatch.setattr(
        outbound_http, "send", lambda *_a, **_kw: outbound_http.Reply(429, httpx.Headers({"Retry-After": "17"}), b"")
    )
    assert gateway.deliver() == [{"message_id": mid, "state": "rate_limited", "slack_ts": None}]
    with hub.read() as c:
        row = OA.get(c, mid)
        assert row["state"] == "queued" and row["attempts"] == 1 and row["provider_attempted_at"] is None
        assert row["next_attempt_at"] == H.shift(gateway.clock(), seconds=17)


@pytest.mark.parametrize("code", ["internal_error", "fatal_error", "unknown_error"])
def test_slack_errors_that_may_have_posted_stay_unknown(gateway, hub, code):
    mid = routed_reply(gateway, hub)
    gateway.mirror()
    gateway.slack.failures.append(G.SlackError("chat.postMessage", code))
    assert gateway.deliver() == [{"message_id": mid, "state": "uncertain", "slack_ts": None}]
    gateway.clock.advance(OA.STALE + 1)
    assert gateway.deliver() == []
    with hub.read() as c:
        row = OA.get(c, mid)
        assert row["state"] == "unknown" and row["provider_attempted_at"] and row["attempts"] == 1


def test_routing_context_and_sent_history_keep_the_sent_text_until_redaction(gateway, hub):
    mid = routed_reply(gateway, hub, "What Slack saw.")
    gateway.mirror()
    [sent] = gateway.deliver()
    [thread] = rows(hub, "SELECT * FROM slack_threads")
    with hub.transaction() as c:
        c.execute("UPDATE messages SET body=? WHERE id=?", ("A later local edit", mid))
        row = c.execute(f"SELECT text FROM ({OS.POSTS}) WHERE message_id=?", (mid,)).fetchone()
        assert row["text"] == "What Slack saw."
        lines = gateway.exchanges(c, thread["channel"], thread["thread_ts"])
        assert next(line["text"] for line in lines if line["ts"] == sent["slack_ts"]) == "What Slack saw."
        OS.withdraw(c, mid)
        assert "What Slack saw." not in OA.get(c, mid)["metadata"]
        assert c.execute(f"SELECT text FROM ({OS.POSTS}) WHERE message_id=?", (mid,)).fetchone()["text"] == ""


def test_inbound_and_shutdown_ring_the_gateway_and_a_wait_drains_the_burst(gateway, monkeypatch):
    from collections import deque

    datagrams = deque()
    rings = []

    def ring(path):
        rings.append(path)
        datagrams.append(b"1")

    def recv(_):
        if not datagrams:
            raise BlockingIOError
        return datagrams.popleft()

    monkeypatch.setattr(OS, "ring", ring)
    bell = SimpleNamespace(recv=recv, settimeout=lambda _: None, setblocking=lambda _: None)
    assert gateway.receive(envelope("<@U0BUS6WA4SY> Legal?")) == "queued"
    assert rings == [gateway.settings.db_path]
    for _ in range(3):
        gateway.notify()
    gateway.wait(bell)
    assert not datagrams
    gateway.request_stop()
    assert gateway.stop.is_set() and datagrams.pop() == b"1"


def test_a_bell_socket_error_does_not_stop_the_gateway(gateway):
    def broken(_):
        gateway.wake.set()
        raise OSError("bell failed")

    gateway.wait(SimpleNamespace(recv=broken, settimeout=lambda _: None))
    assert not gateway.wake.is_set()


def test_gateway_recovery_cannot_reclaim_other_channels_or_send_under_a_lost_claim(gateway, hub, monkeypatch):
    mid = routed_reply(gateway, hub)
    gateway.mirror()
    now = gateway.clock()
    with hub.transaction() as c:
        c.execute(
            "INSERT INTO outbound_actions(id,channel,audience,operation,destination,occurrence_key,state,claimed_at,"
            "claim_token,send_after,next_attempt_at,created_at,updated_at) "
            "VALUES('jira-claim','jira','external','comment','ACME-3','jira-comment','attempting',?,'token',?,?,?,?)",
            (now, now, now, now, now),
        )
    assert OA.claim(hub, now) is None
    claimed = OA.claim(hub, now, slack=True)
    assert claimed.id == mid
    slack_rings, worker_rings = [], []
    monkeypatch.setattr(OS, "ring", slack_rings.append)
    monkeypatch.setattr(OA, "ring", lambda: worker_rings.append(True))
    gateway.clock.advance(OA.STALE + 1)
    assert gateway.recover() == 1
    assert slack_rings == [hub.settings.db_path] and not worker_rings
    with hub.read() as c:
        assert OA.get(c, "jira-claim")["claim_token"] == "token"
    assert OA.attempt(hub, claimed, gateway=gateway, evidence=(None, None)) == "lost"
    assert not gateway.slack.posts
    gateway.deliver()
    assert len(gateway.slack.posts) == 1


# ----------------------------------------------------------------------------- the review's cases
def test_a_bot_reply_is_escaped_so_it_cannot_page_the_channel_or_lose_prose(gateway, hub):
    routed_reply(gateway, hub, "<!channel> R&D says x < y and y > z")
    gateway.tick()
    assert gateway.slack.posts[0]["text"] == "&lt;!channel&gt; R&amp;D says x &lt; y and y &gt; z\n\n(sent from Legal)"
    with hub.transaction() as c:
        H.say(
            c,
            "bot:legal",
            "human:ana",
            "<!everyone> <!here>",
            conversation_id=rows(hub, "SELECT conversation_id FROM slack_threads")[0]["conversation_id"],
        )
    gateway.tick()
    assert gateway.slack.posts[1]["text"] == "&lt;!everyone&gt; &lt;!here&gt;\n\n(sent from Legal)"


def thread_of(hub, channel):
    return rows(hub, "SELECT thread_id FROM slack_thread_links WHERE channel_id=?", channel)[0]["thread_id"]


def test_a_read_channel_comes_into_its_read_only_thread_and_its_active_readers_wake_once_an_hour(readers):
    from backend import thread_bot_digest

    gw, hub, slack = readers, readers.store, readers.slack
    marketing, release = thread_of(hub, MARKETING), thread_of(hub, RELEASE)
    assert {
        (r["actor"], r["notify"])
        for r in rows(hub, "SELECT actor,notify FROM thread_followers WHERE thread_id=? AND following=1", marketing)
    } == {("bot:cmo", "digest"), ("bot:seo", "digest"), ("bot:finance", "digest")}
    assert gw.receive(channel_message("Launch copy is ready for review")) == "stored"
    assert gw.receive(channel_message("Deploy 4.2 went out", channel=RELEASE, user="U2")) == "stored"
    assert gw.receive(channel_message("Nobody reads this", channel=QUIET)) == "stored"
    assert [e["state"] for e in events(hub)] == ["stored"] * 3 and jobs(hub) == [], "stored is not routed"
    said = rows(
        hub, "SELECT conversation_id,sender_id,body FROM messages WHERE conversation_id IN (?,?)", marketing, release
    )
    assert sorted((m["conversation_id"], m["sender_id"], m["body"]) for m in said) == sorted(
        [(marketing, "human:ana", "Launch copy is ready for review"), (release, "human:ben", "Deploy 4.2 went out")]
    )
    gw.tick()
    assert slack.posts == [] and slack.ephemeral == [], "nothing goes back to a read channel"
    assert thread_bot_digest.sweep(hub) == 2 + 1, "the CMO, SEO and CTO wake; the paused Finance bot waits"
    woken = rows(
        hub, "SELECT recipient_id,body FROM messages WHERE sender_id='keeper' AND kind='notice' ORDER BY recipient_id"
    )
    assert [w["recipient_id"] for w in woken] == ["bot:cmo", "bot:cto", "bot:seo"]
    assert f"- {marketing}: 1" in woken[0]["body"] and "Launch copy" not in woken[0]["body"]
    assert sorted(j["bot_id"] for j in jobs(hub)) == ["cmo", "cto", "seo"], "each reader gets one turn"
    assert thread_bot_digest.sweep(hub) == 0, "nothing new, nobody woken"


def test_a_shared_dm_with_56_active_bots_is_asked_in_2_calls_and_routed(gateway, hub):
    """56 bots and the three fixed questions are 59, past the Gateway's 40 a call: 2 calls, every question once."""
    add_bots(hub, 56 - 6)  # the fixture already has six active bots
    with hub.read() as c:
        assert len(gateway.fleet(c)) == 56
    gateway._judge = engine = CappedJudge({"asks": 0.9, "bot:bot-49": 0.95, "bot:legal": 0.7})
    result = send(gateway, "please draft the vendor terms", channel=DM, kind="message")
    assert result["state"] == "routed", result
    assert [r["bot"] for r in result["decision"]["recipients"]] == ["bot-49", "legal"]
    assert len(engine.calls) == 2 and all(len(call) <= 40 for call in engine.calls)
    asked = [q for call in engine.calls for q in call]
    assert len(asked) == len(set(asked)) == 59, "every question once"
    assert len(result["decision"]["scores"]) == 56
    assert events(hub)[0]["state"] == "routed"
    with hub.read() as c:
        audit = json.loads(c.execute("SELECT detail FROM events WHERE action='judge.call'").fetchone()[0])
    assert audit["questions"] == 59 and audit["calls"] == 2


def test_a_fleet_too_big_for_one_call_is_asked_in_the_fewest_calls_and_no_question_twice(gateway, hub, monkeypatch):
    add_bots(hub, 56 - 6)
    gateway._judge = engine = CappedJudge({"asks": 0.9, "bot:bot-49": 0.95, "bot:legal": 0.7})
    with hub.read() as c:
        fleet = gateway.fleet(c)
    questions = gateway.questions(fleet)
    longest = max(J.chars({qid: q}) for qid, q in questions.items())
    total = sum(J.chars({qid: q}) for qid, q in questions.items())
    real = J.split

    def split(state, questions):  # the message and about half the questions fit in one call
        monkeypatch.setattr(J, "MAX_CALL_CHARS", J.chars(state) + total // 2 + longest)
        return real(state, questions)

    monkeypatch.setattr(J, "split", split)
    result = send(gateway, "please draft the vendor terms", channel=DM, kind="message")
    assert result["state"] == "routed" and [r["bot"] for r in result["decision"]["recipients"]] == ["bot-49", "legal"]
    asked = [q for call in engine.calls for q in call]
    assert len(engine.calls) == 2 and sorted(asked) == sorted(questions), "each question once, in two calls"
    with hub.read() as c:
        audit = json.loads(c.execute("SELECT detail FROM events WHERE action='judge.call'").fetchone()[0])
    assert audit["calls"] == 2


def test_a_persons_message_the_model_reads_as_asking_for_nothing_still_reaches_the_assistant(gateway, hub):
    """Decide picks who gets a person's message, never whether anyone does (the Decide on Jev spec)."""
    result = send(gateway, "thanks, that's all", judge_answer=answers(asks=0.1, legal=0.9), channel=DM, kind="message")
    assert result["state"] == "routed" and [r["bot"] for r in result["decision"]["recipients"]] == ["assistant"]
    assert "asks for nothing" in result["decision"]["reason"] and [j["bot_id"] for j in jobs(hub)] == ["assistant"]


def test_a_decision_outage_retries_for_ten_minutes_and_then_fails_instead_of_looping(gateway, hub):
    gateway._judge = CappedJudge(
        {}, fail=J.JudgeError("unavailable", "the decision model answered 503", 503, retryable=True)
    )
    assert send(gateway, "hello there", channel=DM, kind="message")["state"] == "received"
    assert gateway.slack.posts == [], "a short outage tells nobody"
    gateway.clock.advance(G.RETRY_SECONDS)
    assert [e["state"] for e in gateway.tick()["events"]] == ["received"], "retried after a minute"
    gateway.clock.advance(G.ROUTE_GIVE_UP_SECONDS)
    final = gateway.tick()["events"][-1]
    assert final["state"] == "failed" and "still failing after 10 minutes" in final["reason"]
    assert events(hub)[0]["state"] == "failed" and len(gateway.slack.posts) == 1
    calls = len(gateway._judge.calls)
    gateway.clock.advance(3600)
    assert gateway.tick()["events"] == [] and len(gateway._judge.calls) == calls


def link_task_requester(hub, slack_id='U1', enabled=True):
    hub.settings.slack_gateway_enabled = enabled
    hub.settings.public_url = 'https://tico.example.com'
    with hub.transaction() as c:
        c.execute('UPDATE humans SET slack_id=? WHERE id=?', (slack_id, 'ana'))


def test_a_muted_bot_stays_in_tico_unless_the_person_is_talking_to_it_in_slack(gateway, hub):
    link_task_requester(hub)
    with hub.transaction() as c:
        raw = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        raw["people"] = [{**p, "slack_muted_bots": ["legal"]} if p["id"] == "ana" else p for p in raw["people"]]
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(raw),))
    # Ana asked Legal from Slack, so everything Legal says in that conversation still goes back there.
    answer = routed_reply(gateway, hub)
    thread = rows(hub, "SELECT * FROM slack_threads")[0]
    with hub.transaction() as c:
        later = H.say(c, "bot:legal", "human:ana", "Run report.", conversation_id=thread["conversation_id"])["id"]
        other = H.say(c, "bot:cmo", "human:ana", "Done with the launch plan.")["id"]
    gateway.originate()
    gateway.mirror()
    with hub.transaction() as c:
        # Days later, Legal's unprompted digest lands in the same chat; Ana is no longer talking there.
        c.execute(
            "UPDATE messages SET created_at=? WHERE conversation_id=? AND sender_id='human:ana'",
            (H.shift(H.now(), days=-2), thread["conversation_id"]),
        )
        muted = H.say(
            c, "bot:legal", "human:ana", "Weekly contract digest.", conversation_id=thread["conversation_id"]
        )["id"]
    gateway.originate()
    gateway.mirror()
    posts = {r["message_id"]: r["state"] for r in rows(hub, f"SELECT * FROM ({OS.POSTS})")}
    assert posts == {answer: "queued", later: "queued", muted: "canceled", other: "queued"}


def test_routing_counts_against_the_senders_daily_cap_and_over_it_the_assistant_gets_the_message(gateway, hub):
    hub.settings.decisions_daily_calls_human = 1
    first = send(
        gateway,
        "please review the vendor terms",
        judge_answer=answers(legal=0.9),
        channel=DM,
        kind="message",
        user="U2",
    )
    assert first["state"] == "routed"
    audit = rows(hub, "SELECT actor,target,detail FROM events WHERE action='judge.call'")
    assert [(r["actor"], r["target"]) for r in audit] == [("human:ben", G.JUDGE_LABEL)]
    assert json.loads(audit[0]["detail"])["via"] == "slack" and "vendor" not in audit[0]["detail"]
    kept = json.loads(audit[0]["detail"])["answers"]
    assert kept["bot:legal"] == {"type": "noul", "value": 0.9, "confidence": 0.8}
    assert kept["asks"] == {"type": "noul", "value": 0.9, "confidence": 0.8}
    from backend.decide_readout import readout

    with hub.read() as c:
        out = readout(c, hub.settings)
    (row,) = [r for r in out["sets"] if r["set"] == "slack-route"]
    assert row["answers"]["asks"] == {"0.7 and over": 1} and row["answers"]["(other questions)"]["0.7 and over"] >= 1
    assert "not kept" not in out["text"]
    calls = len(gateway.engine.calls)
    second = send(gateway, "and the NDA too", channel=DM, kind="message", user="U2")
    assert len(gateway.engine.calls) == calls, "over the cap the decision model is not asked"
    assert second["state"] == "routed" and second["decision"]["routed_by"] == "budget"
    assert [r["bot"] for r in second["decision"]["recipients"]] == ["assistant"]


def test_a_routing_retry_is_one_short_http_attempt_and_one_audited_call(hub, monkeypatch):
    from backend import judge as B

    attempts = []

    def opener(request, timeout=None):
        attempts.append(timeout)
        raise TimeoutError()

    monkeypatch.setattr("clients.judge.urlopen", opener)
    hub.settings.typesafe_api_key = "synthetic"
    gw = G.Gateway(hub, FakeSlack(), clock=Clock())
    gw.verify_app()
    assert gw.receive(envelope("hello there", channel=DM, kind="message")) == "queued"
    assert gw.tick()["events"][-1]["state"] == "received"
    assert attempts == [B.JEV_TIMEOUT], "the gateway's own minute-by-minute retry is the only retry"
    gw.clock.advance(G.RETRY_SECONDS)
    gw.tick()
    gw.clock.advance(G.ROUTE_GIVE_UP_SECONDS)
    assert gw.tick()["events"][-1]["state"] == "failed"
    assert len(attempts) == 3
    assert rows(hub, "SELECT count(*) AS n FROM events WHERE action='judge.call'")[0]["n"] == 1


def test_a_shared_screenshot_reaches_the_bot_as_a_file_line_it_can_open():
    shot = {"id": "F0SHOT1", "name": "checkout-error.png", "mimetype": "image/png"}
    payload = {
        "event_id": "Ev1",
        "team_id": "T1",
        "api_app_id": "A1",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "ts": "1.1",
            "text": "checkout breaks, see screenshot",
            "files": [shot],
        },
    }
    event, why = G.event_from(payload, "UBOT")
    assert why is None
    assert event["text"] == "checkout breaks, see screenshot\n[file F0SHOT1] checkout-error.png"
    payload["event"]["text"] = ""
    assert G.event_from(payload, "UBOT")[0]["text"] == "[file F0SHOT1] checkout-error.png"
    assert G.message_text({"text": "", "files": [shot]}) == "[file F0SHOT1] checkout-error.png"
    assert G.message_text({"text": "", "files": [{"name": "no-id.png"}]}) == "(a file with no text)"
    assert "files:read" in G.NEEDED_SCOPES
    assert (
        G.message_text({"text": "", "files": [{"id": "F1", "name": "a.png\n[file F2] b.pdf"}]})
        == "[file F1] a.png [file F2] b.pdf"
    )


# ----------------------------------------------------------------------------- visuals: one line, never the visual (Dev 248)
def visual(kind="image", n=1):
    return {"file_id": f"f{n}", "version": 1, "title": f"Chart {n}", "kind": kind, "name": f"chart{n}.png"}


_VISUAL_ROWS = [
    ([visual()], "1 visual - view in Tico"),
    ([visual(k, i) for i, k in enumerate(["image", "svg", "page", "video", "audio"])], "5 visuals - view in Tico"),
    # A plain file (a PDF, an oversize picture kept as a chip) is not a visual; nor is an older attachment with no kind.
    (
        [visual(), {"file_id": "f9", "version": 1, "kind": "file", "name": "terms.pdf"}, {"id": "legacy"}],
        "1 visual - view in Tico",
    ),
]


def _a_reply_with_visuals_reaches_slack_as_its_text_plus_one_line_to_the_message(gateway, hub, attachments, line):
    hub.settings.public_url = "https://tico.example.com"
    first = routed_reply(gateway, hub)
    thread = _thread_of_reply(hub, first)
    with hub.transaction() as c:
        reply = H.say(
            c,
            "bot:legal",
            "human:ana",
            "Revenue doubled <fast>.",
            conversation_id=thread["conversation_id"],
            in_reply_to=first,
            refs={"attachments": attachments},
        )["id"]
    gateway.tick()
    text = gateway.slack.posts[-1]["text"]
    # The whole text, then the line linking to the message in that bot's chat room (no task here), then the footer. Slack
    # wants `&` escaped inside a link too, and opens it as `&`.
    assert text == (
        "Revenue doubled &lt;fast&gt;.\n\n"
        f"<https://tico.example.com/?bot=legal&amp;conversation={thread['conversation_id']}&amp;message={reply}|{line}>"
        "\n\n(sent from Legal)"
    )
    assert "chart" not in text.lower()


def _a_reply_with_no_visual_or_only_files_has_no_line(gateway, hub):
    mark = len(gateway.slack.posts)
    first = routed_reply(gateway, hub)
    thread = _thread_of_reply(hub, first)
    with hub.transaction() as c:
        H.say(
            c,
            "bot:legal",
            "human:ana",
            "Terms attached.",
            conversation_id=thread["conversation_id"],
            in_reply_to=first,
            refs={"attachments": [{"file_id": "f9", "version": 1, "kind": "file", "name": "terms.pdf"}]},
        )
    gateway.tick()
    assert [p["text"] for p in gateway.slack.posts[mark:]] == [
        "Done.\n\n(sent from Legal)",
        "Terms attached.\n\n(sent from Legal)",
    ]


def _a_long_reply_with_a_visual_keeps_the_line_after_the_cut(gateway, hub):
    first = routed_reply(gateway, hub)
    thread = _thread_of_reply(hub, first)
    with hub.transaction() as c:
        H.say(
            c,
            "bot:legal",
            "human:ana",
            "x" * (G.POST_CHARS + 50),
            conversation_id=thread["conversation_id"],
            in_reply_to=first,
            refs={"attachments": [visual()]},
        )
    gateway.tick()
    text = gateway.slack.posts[-1]["text"]
    assert text.endswith("|1 visual - view in Tico>\n\n(sent from Legal)") and "x …\n\n<" in text


def task_reply(hub, private, attachments, title="Q3 numbers"):
    """Legal, owner of a task Ana asked for, answers her in the task's conversation."""
    with hub.transaction() as c:
        made = H.task_create(c, "human:ana", title, "Chart the quarter.", "bot:legal", lint=False, private=private)
        cid = H.task(c, made["id"])["conversation_id"]
        reply = H.say(
            c, "bot:legal", "human:ana", "Here is the quarter.", conversation_id=cid, refs={"attachments": attachments}
        )["id"]
    return made["id"], reply


def _a_task_reply_with_a_visual_links_to_that_message_in_the_task(gateway, hub):
    link_task_requester(hub)
    task, reply = task_reply(hub, False, [visual(), visual("svg", 2)])
    mark = len(gateway.slack.posts)
    gateway.originate()
    gateway.mirror()
    gateway.deliver()
    [post] = gateway.slack.posts[mark:]
    assert post["text"] == (
        "Here is the quarter.\n\n"
        f"<https://tico.example.com/?task={task}&amp;message={reply}|2 visuals - view in Tico>"
        f"\n\n(sent from Legal on <https://tico.example.com/#/task/{task}|#1 Q3 numbers>)"
    )


def _a_task_reply_names_and_links_its_task(gateway, hub):
    """A DM about a task makes sense on its own: the footer names the task (number first when it has one) and links to it."""
    link_task_requester(hub)
    task, _ = task_reply(hub, False, [], title="Q3 costs")
    with hub.transaction() as c:
        c.execute("UPDATE tasks SET number=19332, title=? WHERE id=?", ("Voice <tokens> & keys", task))
    mark = len(gateway.slack.posts)
    gateway.originate()
    gateway.mirror()
    gateway.deliver()
    [post] = gateway.slack.posts[mark:]
    assert post["text"] == (
        "Here is the quarter.\n\n"
        f"(sent from Legal on <https://tico.example.com/#/task/{task}|#19332 Voice &lt;tokens&gt; &amp; keys>)"
    )


def test_a_bot_post_is_shaped_from_its_message_its_task_and_its_visuals(gateway, hub):
    """`rendered` reads the reply's own room for the task it is about: the visual line links to that message in the
    task, and the footer names the task."""
    task, reply = task_reply(hub, False, [visual()], title="Q4 numbers")
    with hub.read() as c:
        shaped, _ = gateway.rendered(c, {"bot": "legal", "text": "Here is the quarter."}, H.message(c, reply))
    base = gateway.settings.public_url
    assert shaped["username"] == "Legal"
    assert shaped["text"] == (
        "Here is the quarter.\n\n"
        f"<{base}/?task={task}&amp;message={reply}|1 visual - view in Tico>"
        f"\n\n(sent from Legal on <{base}/#/task/{task}|#1 Q4 numbers>)"
    )


def test_a_private_task_reply_with_a_visual_never_reaches_slack(gateway, hub):
    """The link would only open for the task's readers, but nothing of a private task goes out in the first place:
    not the text, not the line, not its ids."""
    link_task_requester(hub)
    task, reply = task_reply(hub, True, [visual()])
    gateway.originate()
    gateway.mirror()
    gateway.deliver()
    assert gateway.slack.posts == []
    assert all(r["state"] == "canceled" for r in rows(hub, f"SELECT * FROM ({OS.POSTS}) WHERE message_id=?", reply))


def test_a_slack_thread_reply_links_to_its_own_room_not_the_bots_usual_chat(gateway, hub):
    """Each Slack thread is a chat room of its own beside the person's usual chat with the bot: the link names the
    reply's room, and only the people in that room find it among their chats with the bot."""
    with hub.transaction() as c:
        usual = H.say(c, "human:ana", "bot:legal", "Hi Legal.")["conversation_id"]
    first = routed_reply(gateway, hub)
    send(gateway, "<@U0BUS6WA4SY> Legal, another thing?", answers(legal=0.9))
    threads = [r["conversation_id"] for r in rows(hub, "SELECT * FROM slack_threads ORDER BY created_at")]
    assert len(set(threads + [usual])) == 3
    with hub.transaction() as c:
        reply = H.say(
            c,
            "bot:legal",
            "human:ana",
            "The chart.",
            conversation_id=threads[0],
            in_reply_to=first,
            refs={"attachments": [visual()]},
        )["id"]
    gateway.tick()
    text = [p["text"] for p in gateway.slack.posts if p["text"].startswith("The chart.")][0]
    assert f"conversation={threads[0]}&amp;message={reply}|1 visual - view in Tico>" in text
    with hub.read() as c:
        assert threads[0] in {r["id"] for r in H.chats_with(c, "human:ana", "bot:legal")}
        assert threads[0] not in {r["id"] for r in H.chats_with(c, "human:ben", "bot:legal")}


def test_a_stored_channel_message_reaches_a_message_automation_once_and_only_after_it_was_made(readers, monkeypatch):
    """The stored copy of a read channel's message is what a message automation on that channel hears: offered once,
    only while the redesign is on, and never the backlog from before the automation existed. The row stays `stored`,
    so the edit path and an @-mention that promotes it are unchanged."""
    from backend import automations

    gw, hub = readers, readers.store
    heard = []
    monkeypatch.setattr(
        automations,
        "on_slack",
        lambda c, event, person, channel, settings=None: (
            heard.append((event["text"], person["id"], channel["name"])) or ["run_1"]
        ),
    )
    with hub.transaction() as c:
        c.execute("INSERT INTO registry_metadata(key,value) VALUES('redesign',?)", ('{"enabled": true}',))
    assert gw.receive(channel_message("Before the automation")) == "stored"
    gw.clock.advance(60)
    with hub.transaction() as c:
        c.execute(
            "INSERT INTO automations(id,name,owner_id,group_id,trigger_type,enabled,builtin,flow,version,created_at,"
            "created_by_id,updated_at) VALUES('a1','Marketing','ana',NULL,'message',1,0,?,1,?,'human:ana',?)",
            ('{"trigger": {"type": "message", "inbox": "slack:#marketing"}}', gw.clock(), gw.clock()),
        )
    gw.clock.advance(60)
    assert gw.receive(channel_message("After the automation")) == "stored"
    out = gw.tick()["events"]
    after = rows(hub, "SELECT event_id FROM slack_events WHERE text=?", "After the automation")[0]["event_id"]
    assert out == [{"event_id": after, "state": "stored", "reason": "automation"}]
    assert heard == [("After the automation", "ana", "#marketing")]
    gw.clock.advance(60)
    assert gw.tick()["events"] == [] and len(heard) == 1, "offered once, never again"
    states = rows(hub, "SELECT text,state,processed_at FROM slack_events ORDER BY received_at")
    assert [s["state"] for s in states] == ["stored", "stored"], "the row stays as it was"
    assert states[0]["processed_at"] is None, "the backlog is not offered"
    assert states[1]["processed_at"] is not None


def test_a_two_way_linked_channel_is_left_out_of_the_automations_and_a_read_only_one_is_not(readers, monkeypatch):
    """A channel a two-way link carries is heard in its thread, so the message automations leave it out, including the
    rows stored before the link. A read-only link only copies the channel, so its automations still hear it."""
    from backend import automations

    gw, hub = readers, readers.store
    heard = []
    monkeypatch.setattr(
        automations,
        "on_slack",
        lambda c, event, person, channel, settings=None: heard.append(event["text"]) or ["run_1"],
    )
    with hub.transaction() as c:
        c.execute("INSERT INTO registry_metadata(key,value) VALUES('redesign',?)", ('{"enabled": true}',))
        c.execute(
            "INSERT INTO automations(id,name,owner_id,group_id,trigger_type,enabled,builtin,flow,version,created_at,"
            "created_by_id,updated_at) VALUES('a1','Marketing','ana',NULL,'message',1,0,?,1,?,'human:ana',?)",
            ('{"trigger": {"type": "message", "inbox": "slack:#marketing"}}', gw.clock(), gw.clock()),
        )
    gw.clock.advance(60)
    assert gw.receive(channel_message("Read-only copy")) == "stored"
    gw.offer()
    assert heard == ["Read-only copy"], "a read-only link's channel is offered"
    gw.clock.advance(60)
    assert gw.receive(channel_message("Stored before the two-way link")) == "stored"
    with hub.transaction() as c:
        c.execute("UPDATE slack_thread_links SET read_only=0 WHERE channel_id=?", (MARKETING,))
    gw.offer()
    assert heard == ["Read-only copy"], "the stored row of a two-way linked channel is not offered"


# ----------------------------------------------------------------------------- membership revoked after queueing
def test_a_queued_slack_copy_to_someone_removed_from_the_thread_is_not_sent(gateway, hub):
    """A bot message in a members thread is copied to the addressee's Slack DM. If that person is removed from the
    thread after the copy is queued (here, while it waits out a rate limit), the copy must not go out: Slack would
    show them a thread they can no longer open."""
    from backend import threads

    slack, clock = gateway.slack, gateway.clock

    def queue(mid):
        OS.queue(c, H.message(c, mid), "legal", "D0U2", "", "copy", recipient="human:ben", now=clock())

    with hub.transaction() as c:
        tid = threads.create(c, "human:ana", title="Pricing", audience="members", members=["human:ben"])["id"]
        before = threads.post(c, "bot:legal", tid, "Ben, the first draft.", kind="say", to_actor="human:ben")["id"]
        queue(before)
    # Control: Ben is still a member, so his copy goes out.
    gateway.deliver()
    assert len(slack.posts) == 1
    with hub.transaction() as c:
        after = threads.post(c, "bot:legal", tid, "Ben, the second draft.", kind="say", to_actor="human:ben")["id"]
        queue(after)
    slack.failures.append(G.SlackError("chat.postMessage", "ratelimited", retry_after=1))
    gateway.deliver()
    assert rows(hub, f"SELECT state FROM ({OS.POSTS}) WHERE message_id=?", after)[0]["state"] == "queued"
    with hub.transaction() as c:
        threads.remove_member(c, "human:ana", tid, "human:ben")
    clock.advance(5)
    gateway.deliver()
    assert len(slack.posts) == 1, "no copy reaches a Slack user who left the thread"
    assert rows(hub, f"SELECT state FROM ({OS.POSTS}) WHERE message_id=?", after)[0]["state"] == "canceled"


def test_a_copy_to_ben_never_reuses_another_members_slack_dm(gateway, hub):
    """A members thread whose Slack mapping is Cara's DM: a bot's message to Ben must not be copied into Cara's DM,
    since the copy would reach Cara with Ben's message. A shared channel mapping of the same thread still gets it."""
    from backend import threads

    with hub.transaction() as c:
        tid = threads.create(c, "human:ana", title="Pricing", audience="members", members=["human:ben", "human:cara"])[
            "id"
        ]
        c.execute(
            "INSERT INTO slack_events(event_id,team_id,channel,channel_kind,thread_ts,reply_ts,ts,user_id,event_type,"
            "text,received_at,state,actor) VALUES('ev-cara',?,'D0CARA','im','','','1','U5','message','hi',?,'routed',"
            "'human:cara')",
            (TEAM, "2000-01-01T00:00:00Z"),
        )
        c.execute(
            "INSERT INTO slack_threads(channel,thread_ts,bot,conversation_id,created_at,last_routed_at,recipient_actor) "
            "VALUES('D0CARA','','legal',?,'2000-01-01T00:00:00Z',NULL,'human:cara')",
            (tid,),
        )
        dm_copy = threads.post(c, "bot:legal", tid, "Ben, the draft.", kind="say", to_actor="human:ben")["id"]
    gateway.mirror()
    gateway.deliver()
    assert not any(post["channel"] == "D0CARA" for post in gateway.slack.posts)
    assert not rows(hub, f"SELECT 1 FROM ({OS.POSTS}) WHERE message_id=?", dm_copy)  # queued for no one else's DM

    with hub.transaction() as c:
        channel_tid = threads.create(c, "human:ana", title="Shared", audience="members", members=["human:ben"])["id"]
        c.execute(
            "INSERT INTO slack_threads(channel,thread_ts,bot,conversation_id,created_at,last_routed_at) "
            "VALUES(?,'','legal',?,'2000-01-01T00:00:00Z',NULL)",
            (MARKETING, channel_tid),
        )
        shared = threads.post(c, "bot:legal", channel_tid, "Team, the draft.", kind="say", to_actor="human:ben")["id"]
    gateway.mirror()
    gateway.deliver()
    assert rows(hub, f"SELECT channel FROM ({OS.POSTS}) WHERE message_id=?", shared)[0]["channel"] == MARKETING
    assert any(post["channel"] == MARKETING for post in gateway.slack.posts)


def test_a_copy_to_a_member_of_a_native_dm_with_no_inbound_yet_is_delivered_and_a_non_member_gets_nothing(gateway, hub):
    """A native DM keeps its other people in thread membership. A member with no inbound message yet gets the copy in
    their own DM, which originate opens for them; a removed person gets nothing."""
    from backend import threads

    with hub.transaction() as c:
        c.execute("UPDATE humans SET slack_id='U2' WHERE id='ben'")
        tid = threads.create(c, "human:ana", title="Pricing", audience="members", members=["human:ben", "human:cara"])[
            "id"
        ]
        to_ben = threads.post(c, "bot:legal", tid, "Ben, the draft.", kind="say", to_actor="human:ben")["id"]
        to_cara = threads.post(c, "bot:legal", tid, "Cara, the draft.", kind="say", to_actor="human:cara")["id"]
        threads.remove_member(c, "human:ana", tid, "human:cara")
    gateway.originate()
    gateway.mirror()
    gateway.deliver()
    assert rows(hub, f"SELECT state FROM ({OS.POSTS}) WHERE message_id=?", to_ben)[0]["state"] == "done"
    assert any(post["channel"] == "D0U2" and "Ben, the draft." in post["text"] for post in gateway.slack.posts)
    assert not rows(hub, f"SELECT 1 FROM ({OS.POSTS}) WHERE message_id=?", to_cara)
    assert not any("Cara, the draft." in post["text"] for post in gateway.slack.posts)


def test_a_bot_message_to_ben_never_goes_through_caras_dm_after_she_leaves_the_thread(gateway, hub):
    """originate opens Cara's DM for a members thread; Cara leaves; the next bot message is to Ben. It must reach Ben
    through his own DM, and Cara's DM must carry nothing for him: a DM mapping carries copies only to its stored person."""
    from backend import threads

    with hub.transaction() as c:
        c.execute("UPDATE humans SET slack_id='UC' WHERE id='cara'")
        c.execute("UPDATE humans SET slack_id='U2' WHERE id='ben'")
        tid = threads.create(c, "human:ana", title="Pricing", audience="members", members=["human:ben", "human:cara"])[
            "id"
        ]
        threads.post(c, "bot:legal", tid, "Cara, the first draft.", kind="say", to_actor="human:cara")
    gateway.originate()
    gateway.deliver()
    assert any(post["channel"] == "D0UC" for post in gateway.slack.posts)
    with hub.transaction() as c:
        threads.remove_member(c, "human:ana", tid, "human:cara")
        ben_msg = threads.post(c, "bot:legal", tid, "Ben, the second draft.", kind="say", to_actor="human:ben")["id"]
    gateway.originate()
    gateway.mirror()
    gateway.deliver()
    ben_posts = [post for post in gateway.slack.posts if "Ben, the second draft." in post["text"]]
    assert [post["channel"] for post in ben_posts] == ["D0U2"]
    assert not any(post["channel"] == "D0UC" and "second draft" in post["text"] for post in gateway.slack.posts)
    assert rows(hub, f"SELECT state FROM ({OS.POSTS}) WHERE message_id=?", ben_msg)[0]["state"] == "done"


def test_a_dm_mapping_from_before_the_column_takes_only_a_person_its_verified_messages_name(hub):
    """A DM mapping made before recipient_actor existed: one verified sender names its person; two senders, or none,
    leave it empty, so its copies fail closed."""
    from backend import threads

    with hub.transaction() as c:
        tid = threads.create(c, "human:ana", title="Old", audience="members", members=["human:ben"])["id"]
        other = threads.create(c, "human:ana", title="Older", audience="members", members=["human:cara"])["id"]
        for channel, conversation, actors in (
            ("D0ONE", tid, ["human:ben"]),
            ("D0TWO", other, ["human:ben", "human:cara"]),
        ):
            c.execute(
                "INSERT INTO slack_threads(channel,thread_ts,bot,conversation_id,created_at,last_routed_at) "
                "VALUES(?,'','legal',?,'2000-01-01T00:00:00Z',NULL)",
                (channel, conversation),
            )
            c.execute("UPDATE slack_threads SET recipient_actor=NULL WHERE channel=?", (channel,))
            for n, actor in enumerate(actors):
                c.execute(
                    "INSERT INTO slack_events(event_id,team_id,channel,channel_kind,thread_ts,reply_ts,ts,user_id,"
                    "event_type,text,received_at,state,actor) VALUES(?,?,?,'im','','',?,'U1','message','hi',"
                    "'2000-01-01T00:00:00Z','routed',?)",
                    (f"{channel}-{n}", TEAM, channel, f"{n + 1}.0", actor),
                )
    hub.initialize()
    found = dict(
        (r["channel"], r["recipient_actor"])
        for r in rows(hub, "SELECT channel,recipient_actor FROM slack_threads WHERE channel IN ('D0ONE','D0TWO')")
    )
    assert found == {"D0ONE": "human:ben", "D0TWO": None}


# ----------------------------------------------------------------------------- link previews
def shared(urls, user="U1", channel=MARKETING, **event_fields):
    COUNTER["n"] += 1
    return {
        "type": "event_callback",
        "team_id": TEAM,
        "api_app_id": APP,
        "event_id": f"Ev{COUNTER['n']:08d}",
        "event": {
            "type": "link_shared",
            "user": user,
            "channel": channel,
            "message_ts": "1690000000.000100",
            "links": [{"url": u, "domain": "tico.example.com"} for u in urls],
            **event_fields,
        },
    }


def test_a_pasted_task_link_gets_a_preview_only_for_a_verified_reader_and_never_for_a_private_task(gateway, hub):
    hub.settings.public_url = "https://tico.example.com"
    hub.settings.slack_open_label, hub.settings.slack_open_url = "Open in Desk", "desk://task/{id}"
    with hub.transaction() as c:
        team = H.task_create(
            c,
            "human:ana",
            "Fix the login page",
            "## Why\nPeople **cannot** sign in.",
            "human:cara",
            due="2026-11-05",
            lint=False,
            private=False,
        )["id"]
        c.execute("UPDATE tasks SET number=42 WHERE id=?", (team,))
        secret = H.task_create(c, "human:ana", "Salary review", "Private.", "human:ana", lint=False, private=True)["id"]
    slack = gateway.slack
    ticket = "https://tico.example.com/ticket/42/fix-the-login-page"
    assert (
        gateway.receive(
            shared([ticket, f"https://tico.example.com/task/{secret}", f"https://elsewhere.example/task/{team}"])
        )
        == "unfurled"
    )
    [unfurl] = slack.unfurls
    assert list(unfurl) == [ticket]
    section, context, actions = unfurl[ticket]["blocks"]
    assert section["text"]["text"] == f"*<{ticket}|#42 Fix the login page>*\nWhy People cannot sign in."
    # The owner's name and the due date, from the renamed columns (owner_id, due_at): the requester is not shown.
    facts = context["elements"][0]["text"]
    assert "Owner: Cara Melo" in facts and "Due 2026-11-05" in facts and "Ana Rivera" not in facts
    assert (
        actions["elements"][0]["url"] == f"desk://task/{team}"
        and actions["elements"][0]["text"]["text"] == "Open in Desk"
    )
    # A guest, a private task alone, or a link to no task: nothing is shown.
    assert gateway.receive(shared([ticket], user="U5")) == "unfurl_denied"
    assert gateway.receive(shared([f"https://tico.example.com/open/task/{secret}/salary"])) == "unfurl_unreadable"
    assert gateway.receive(shared(["https://tico.example.com/ticket/999"])) == "unfurl_unreadable"
    assert gateway.receive(shared([ticket], channel="C0EXT")) == "unfurl_external"
    # Slack refusing the button's scheme gets the same preview with the open link as text.
    slack.failures.append(G.SlackError("chat.unfurl", "invalid_blocks"))
    assert gateway.receive(shared([f"https://tico.example.com/#/task/{team}"])) == "unfurled"
    blocks = slack.unfurls[-1][f"https://tico.example.com/#/task/{team}"]["blocks"]
    assert len(blocks) == 2 and blocks[1]["elements"][-1]["text"] == f"<desk://task/{team}|Open in Desk>"
    assert len(rows(hub, "SELECT * FROM events WHERE action='slack.unfurl'")) == 2


def test_a_link_gets_no_preview_where_its_channel_cannot_be_checked_or_it_is_still_in_the_composer(gateway, hub):
    hub.settings.public_url = "https://tico.example.com"
    with hub.transaction() as c:
        team = H.task_create(
            c, "human:ana", "Fix the login page", "Body.", "human:cara", due="2026-11-05", lint=False, private=False
        )["id"]
        c.execute("UPDATE tasks SET number=42 WHERE id=?", (team,))
    slack = gateway.slack
    ticket = "https://tico.example.com/ticket/42/fix-the-login-page"
    # A private channel Tico is not in: its info cannot be read, so nothing is shown there.
    assert gateway.receive(shared([ticket], channel="C0PRIVATE")) == "unfurl_unchecked"
    assert slack.unfurls == []
    # A link still in the composer has no channel yet to check it against, whatever its channel field says.
    composer = shared([ticket], channel=MARKETING, unfurl_id="Uf0001", source="composer")
    assert gateway.receive(composer) == "unfurl_composer"
    assert gateway.receive(shared([ticket], channel="COMPOSER")) == "unfurl_composer"
    assert slack.unfurls == []
    # A DM whose info cannot be read still gets its preview: the sender is verified there.
    assert gateway.receive(shared([ticket], channel=DM)) == "unfurled"
    assert len(slack.unfurls) == 1
    assert len(rows(hub, "SELECT * FROM events WHERE action='slack.unfurl'")) == 1


# ----------------------------------------------------------------------------- stories: one gateway and hub each
def test_visual_and_task_replies_share_one_gateway_story(gateway, hub, subtests):
    """The reply path, then task replies: one app, each check reports on its own. A check reads only what it posted
    (from its own mark in the post list), and every task has its own title."""
    for i, (attachments, line) in enumerate(_VISUAL_ROWS):
        with subtests.test(f"row {i}: {line}"):
            _a_reply_with_visuals_reaches_slack_as_its_text_plus_one_line_to_the_message(
                gateway, hub, attachments, line
            )
    for check in (
        _a_reply_with_no_visual_or_only_files_has_no_line,
        _a_long_reply_with_a_visual_keeps_the_line_after_the_cut,
        _a_task_reply_with_a_visual_links_to_that_message_in_the_task,
        _a_task_reply_names_and_links_its_task,
    ):
        with subtests.test(check.__name__):
            check(gateway, hub)


def test_a_request_loop_failure_logs_its_message_scrubbed(monkeypatch, caplog):
    """A failed pass of the requests loop logs the exception's class and message, with a token redacted."""
    gateway = SimpleNamespace(stop=threading.Event())

    def failing_pass(*_args):
        gateway.stop.set()
        raise sqlite3.OperationalError("database is locked (key sk-testkey-000000000000)")

    monkeypatch.setattr(SR, "serve", failing_pass)
    monkeypatch.setattr(SR, "sweep", lambda _gateway: 0)
    with caplog.at_level(logging.ERROR, logger="tico.slack_requests"):
        SR.serve_forever(gateway)
    assert "Slack requests failed: OperationalError: database is locked (key [redacted])" in caplog.text
    assert "sk-testkey" not in caplog.text


# ----------------------------------------------------------------------------- loop failures: the class and a scrubbed message
def locked(*_args, **_kwargs):
    raise sqlite3.OperationalError("database is locked (key sk-testkey-000000000000)")


LOCKED = "OperationalError: database is locked (key [redacted])"


class _SocketClient:
    """Stands in for slack_sdk's SocketModeClient: it keeps the listeners and the acks."""

    def __init__(self, app_token, web_client):
        self.socket_mode_request_listeners, self.acks = [], []

    def connect(self):
        pass

    def send_socket_mode_response(self, response):
        self.acks.append(response.envelope_id)


def test_an_event_the_gateway_cannot_store_is_not_acknowledged_so_slack_sends_it_again(monkeypatch, caplog):
    import slack_sdk.socket_mode
    from slack_sdk.socket_mode.request import SocketModeRequest

    monkeypatch.setattr(slack_sdk.socket_mode, "SocketModeClient", _SocketClient)
    socket = G.SlackAPI("xoxb-test-token", "xapp-test-token").connect(locked)
    socket_request = SocketModeRequest("events_api", "env-1", {"event": {"type": "message"}})
    with caplog.at_level(logging.ERROR, logger="tico.slack"):
        socket.socket_mode_request_listeners[0](socket, socket_request)
    assert f"Event env-1 not persisted: {LOCKED}" in caplog.text
    assert socket.acks == []


def test_an_event_that_fails_is_stored_as_failed_with_its_scrubbed_reason(gateway, hub, monkeypatch, caplog):
    assert gateway.receive(envelope("Please check the launch")) == "queued"
    event_id = rows(hub, "SELECT event_id FROM slack_events")[0]["event_id"]
    monkeypatch.setattr(gateway, "process", locked)
    with caplog.at_level(logging.ERROR, logger="tico.slack"):
        out = gateway.tick()["events"]
    assert f"Event {event_id} failed: {LOCKED}" in caplog.text
    assert {"event_id": event_id, "state": "failed"} in out
    assert rows(hub, "SELECT state,reason FROM slack_events WHERE event_id=?", event_id) == [
        {"state": "failed", "reason": LOCKED}
    ]


def test_a_failed_tick_or_heartbeat_is_logged_scrubbed_and_the_loop_runs_its_next_turn(gateway, monkeypatch, caplog):
    turns = []

    def tick():
        turns.append("tick")
        if len(turns) == 1:
            locked()

    def heartbeat():
        if len(turns) == 2:
            gateway.stop.set()  # the second turn is the last
        gateway.wake.set()  # the loop does not wait out a tick interval
        locked()

    monkeypatch.setattr(SR, "serve_forever", lambda _gateway: None)  # the requests loop has its own test
    monkeypatch.setattr(gateway, "tick", tick)
    gateway.heartbeat = heartbeat
    with caplog.at_level(logging.ERROR, logger="tico.slack"):
        gateway.run()
    assert turns == ["tick", "tick"]
    assert f"Gateway tick failed: {LOCKED}" in caplog.text
    assert f"Gateway heartbeat failed: {LOCKED}" in caplog.text


def test_a_failed_thread_link_pass_is_logged_scrubbed_and_reported_as_an_error(gateway, monkeypatch, caplog):
    monkeypatch.setattr(gateway.bridge, "tick", locked)
    with caplog.at_level(logging.ERROR, logger="tico.slack"):
        out = gateway.tick()
    assert f"Slack thread links failed: {LOCKED}" in caplog.text
    assert out["threads"] == {"error": "OperationalError"}


# ----------------------------------------------------------------------------- start-up on a busy hub
def test_a_locked_database_at_start_up_is_waited_out_not_fatal(monkeypatch, caplog):
    stopping = threading.Event()
    waits = []
    monkeypatch.setattr(stopping, "wait", lambda seconds: waits.append(seconds))
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 4:
            locked()
        return "ok"

    with caplog.at_level(logging.WARNING, logger="tico.slack"):
        assert G._when_free(stopping, "Slack recover", flaky) == "ok"
    assert len(calls) == 4 and waits == [1.0, 2.0, 4.0]
    assert f"Slack recover: {LOCKED}" in caplog.text


def test_start_up_waiting_only_covers_busy_errors_and_stops_when_asked():
    stopping = threading.Event()

    def broken():
        raise sqlite3.OperationalError("no such table: outbound_actions")

    with pytest.raises(sqlite3.OperationalError):
        G._when_free(stopping, "x", broken)
    stopping.set()
    with pytest.raises(sqlite3.OperationalError):
        G._when_free(stopping, "x", locked)
