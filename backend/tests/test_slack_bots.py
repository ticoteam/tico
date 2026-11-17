"""Bots use Slack through the server (backend/slack_bots.py) and the gateway (backend/slack_requests.py), never with a
token of their own: every gate on the server, history and files answered by the gateway's request thread, posts in the
gateway's outbox, DMs as the bot's message to a person, and a clear 503 when the gateway is not running.

Ops is the bot (its computer reports a `service: slack` entry); Ana owns the company and has a Slack id.
"""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import anyio
import pytest

from backend import hubdb as H
from backend import org_rule_check as OR
from backend import screening
from backend import slack_bots as SB
from backend import slack_requests as SR
from backend.tests.helpers.api import get, headers, post, setup_attempt  # noqa: F401  (fixture)
from backend.tests.helpers.bot_tools import configure, report
from backend.tests.helpers.slack_gateway import (  # noqa: F401  (fixture)
    CHANNELS,
    MARKETING,
    envelope,
    gateway,
    hub,
    rows,
)

GENERAL, PARTNERS, QUIET_ID = "C0000000001", "C0EXT", "C0000000003"
SLACK = {"service": "slack", "can": ["read", "post"], "scope": {"channels": ["#general"], "dms": "yes"}}


class FakeAPI:
    """What the gateway's request thread asks Slack: channels, a page of history, replies, a file and its bytes."""

    def __init__(self):
        self.channels = {
            GENERAL: {"id": GENERAL, "name": "general"},
            PARTNERS: {"id": PARTNERS, "name": "partners", "is_ext_shared": True},
            QUIET_ID: {"id": QUIET_ID, "name": "quiet"},
        }
        self.pages = {
            GENERAL: [
                [
                    {"ts": "1700000002.000100", "user": "U1", "text": "Launch is <@U1>'s call", "reply_count": 1},
                    {"ts": "1700000003.000100", "subtype": "channel_join", "user": "U9", "text": "joined"},
                ],
                [
                    {
                        "ts": "1700000001.000100",
                        "bot_id": "B1",
                        "username": "deploys",
                        "text": "",
                        "attachments": [{"fallback": "build 41 green"}],
                        "files": [{"id": "F0FILE0001", "name": "notes.txt", "mimetype": "text/plain"}],
                    }
                ],
            ]
        }
        self.threads = {(GENERAL, "1700000002.000100"): [{"ts": "1700000004.000100", "user": "U2", "text": "Agreed"}]}
        self.files = {
            "F0FILE0001": {
                "id": "F0FILE0001",
                "name": "notes.txt",
                "mimetype": "text/plain",
                "size": 11,
                "channels": [GENERAL],
                "url_private_download": "https://files.slack.com/notes.txt",
            },
            "F0DMFILE01": {
                "id": "F0DMFILE01",
                "name": "dm.pdf",
                "mimetype": "application/pdf",
                "ims": ["D0AAAAAAA"],
                "url_private": "https://files.slack.com/dm.pdf",
            },
            "F0QUIET001": {"id": "F0QUIET001", "channels": [QUIET_ID], "url_private": "https://files.slack.com/q"},
            "F0ELSEWHR1": {"id": "F0ELSEWHR1", "channels": [GENERAL], "url_private": "https://example.com/x"},
            "F0SIGNIN01": {
                "id": "F0SIGNIN01",
                "channels": [GENERAL],
                "mimetype": "application/pdf",
                "url_private": "https://files.slack.com/signin",
            },
            "F0HUGE0001": {
                "id": "F0HUGE0001",
                "channels": [GENERAL],
                "size": SR.MAX_FILE_BYTES + 1,
                "url_private": "https://files.slack.com/huge",
            },
            "F0GONE0001": {"id": "F0GONE0001", "channels": [GENERAL], "url_private": "https://files.slack.com/gone"},
        }
        self.calls = []

    def conversations_list(self):
        return list(self.channels.values())

    def conversations_info(self, channel):
        self.calls.append(("conversations.info", channel))
        if channel not in self.channels:
            raise RuntimeError("channel_not_found")
        return self.channels[channel]

    def history_range(self, channel, oldest, latest, cursor=""):
        self.calls.append(("history", channel, oldest, latest, cursor))
        pages = self.pages.get(channel, [[]])
        n = int(cursor or 0)
        return pages[n], str(n + 1) if n + 1 < len(pages) else ""

    def replies(self, channel, ts):
        return self.threads.get((channel, ts), [])

    def files_info(self, fid):
        return self.files[fid]

    def download(self, url, limit):
        self.calls.append(("download", url))
        if url.endswith("/signin"):
            return 200, "text/html; charset=utf-8", b"<html>sign in</html>"
        if url.endswith("/gone"):
            return 404, "", b""
        return 200, "text/plain", b"hello world" if url.endswith(".txt") else b"%PDF-1"


@pytest.fixture
def slack(api, monkeypatch):
    """Ops with a Slack entry on its computer, #general listed with Ops as a reader, #quiet with posting off, the
    gateway connected and its request thread answering from FakeAPI."""
    monkeypatch.setattr(SR, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(SR, "SERVE_SECONDS", 0.01)
    configure(api)
    machine, _, attempt = setup_attempt(api, "ops")
    assert report(api, machine, "ops", [SLACK]).status_code == 200
    post(api, "slack/channels", {"channel": GENERAL, "name": "#general", "readers": ["ops"]}, "ana-test")
    post(api, "slack/channels", {"channel": "#quiet", "post": False}, "ana-test")
    post(api, "slack/channels", {"channel": "#later"}, "ana-test")  # by name: no id yet
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "INSERT INTO service_health(service,detail) VALUES('slack:gateway',?)",
            (json.dumps({"state": "connected"}),),
        )
        c.execute("UPDATE humans SET slack_id='U1' WHERE id='ana'")
        c.execute(
            "INSERT INTO registry_metadata(key,value) VALUES('slack_workspace_url',?)",
            (json.dumps("https://acme.slack.com/"),),
        )
    fake = FakeAPI()
    gw = SimpleNamespace(
        store=store,
        settings=store.settings,
        slack=fake,
        team_url="https://acme.slack.com/",
        display_name=lambda event: {"U1": "Ana Rivera", "U2": "Ben"}.get(event["user_id"], event["user_id"]),
        stop=threading.Event(),
    )
    thread = threading.Thread(target=SR.serve_forever, args=(gw,), daemon=True)
    thread.start()
    ns = SimpleNamespace(api=api, token=attempt["token"], machine=machine, fake=fake, gw=gw, store=store, thread=thread)
    yield ns
    gw.stop.set()
    ns.thread.join(5)


class Answer:
    """A response whose .json() is the error itself when it is a refusal ({"error": {code, detail}})."""

    def __init__(self, r):
        self.r, self.content, self.headers = r, r.content, r.headers

    def json(self):
        data = self.r.json()
        return data["error"] if self.r.status_code >= 400 else data


def call(s, method, path, expected=200, **kw):
    r = s.api.request(method, "/api/v2/" + path, headers=headers(s.token), **kw)
    assert r.status_code == expected, r.text
    return Answer(r)


def entry(s, **changes):
    assert report(s.api, s.machine, "ops", [{**SLACK, **changes}]).status_code == 200


# ----------------------------------------------------------------------------- reading
def test_history_comes_from_the_gateway_shaped_with_threads_and_names(slack):
    got = call(
        slack, "GET", "slack/history", params={"channel": "#general", "oldest": 1700000000, "threads": "true"}
    ).json()
    assert got["ok"] and (got["id"], got["name"]) == (GENERAL, "general") and "request_id" not in got
    first, second = got["messages"]
    assert [first["ts"], second["ts"]] == ["1700000001.000100", "1700000002.000100"], "oldest first, joins dropped"
    assert first["author"] == "deploys" and "[attachment] build 41 green" in first["text"]
    assert "F0FILE0001" in first["text"]
    assert second["author"] == "Ana Rivera" and "@Ana Rivera" in second["text"]
    assert second["permalink"] == "https://acme.slack.com/archives/C0000000001/p1700000002000100"
    assert [(r["author"], r["text"]) for r in second["replies"]] == [("Ben", "Agreed")]
    with slack.store.read() as c:
        assert c.execute("SELECT count(*) FROM slack_requests").fetchone()[0] == 0, "the API deletes what it read"
    # By id works too, and without threads no replies are fetched.
    plain = call(slack, "GET", "slack/history", params={"channel": GENERAL, "oldest": 0}).json()
    assert all(m["replies"] == [] for m in plain["messages"])


def test_history_is_refused_outside_the_bots_channels_and_for_slack_connect(slack):
    refused = call(slack, "GET", "slack/history", 403, params={"channel": "#quiet", "oldest": 0}).json()
    assert refused["code"] == "slack_scope" and "#general" in refused["detail"]
    call(slack, "GET", "slack/history", 422, params={"channel": "#general", "oldest": 10, "latest": 5})
    # Declared, but shared outside the workspace: only Slack can tell, so the gateway refuses it.
    entry(slack, scope={"channels": ["#general", "#partners", "#nowhere"]})
    outside = call(slack, "GET", "slack/history", 403, params={"channel": "#partners", "oldest": 0}).json()
    assert outside["code"] == "slack_connect"
    missing = call(slack, "GET", "slack/history", 404, params={"channel": "#nowhere", "oldest": 0}).json()
    assert missing["code"] == "not_found" and "invite @Tico" in missing["detail"]


def test_a_bot_without_read_or_without_slack_is_refused_and_a_person_cannot_use_the_routes(slack):
    entry(slack, can=["post"])
    assert call(slack, "GET", "slack/history", 403, params={"channel": "#general", "oldest": 0}).json()["code"] == (
        "slack_access"
    )
    assert report(slack.api, slack.machine, "ops", []).status_code == 200
    refused = call(slack, "GET", "slack/history", 403, params={"channel": "#general", "oldest": 0}).json()
    assert refused["code"] == "slack_access" and "service: slack" in refused["detail"]
    r = slack.api.get("/api/v2/slack/history", params={"channel": "#general", "oldest": 0}, headers=headers())
    assert r.status_code == 403 and "for bots" in r.json()["error"]["detail"]


def test_screening_warns_or_holds_what_a_bot_reads(slack, monkeypatch):
    verdicts = iter([{"action": "warn", "score": 0.7, "kinds": ["override"]}, {"action": "hold"}])
    monkeypatch.setattr(screening, "screen", lambda *a, **k: next(verdicts))
    monkeypatch.setattr(screening, "warning_line", lambda v: "Tico screening: careful")
    warned = call(slack, "GET", "slack/history", params={"channel": "#general", "oldest": 0}).json()
    assert warned["warning"] == "Tico screening: careful"
    held = call(slack, "GET", "slack/history", 403, params={"channel": "#general", "oldest": 0}).json()
    assert held["code"] == "screening_held"


def test_a_file_is_downloaded_by_the_gateway_where_the_bot_may_read_it(slack):
    r = call(slack, "GET", "slack/files/F0FILE0001")
    assert r.content == b"hello world" and r.headers["X-Slack-File-Name"] == "notes.txt"
    assert r.headers["X-Slack-File-Type"] == "text/plain" and "X-Tico-Screening" not in r.headers
    assert list(SR.files_dir(slack.store.settings).iterdir()) == [], "the bytes are deleted once streamed"
    assert call(slack, "GET", "slack/files/F0DMFILE01").content == b"%PDF-1", "a DM's file, with DMs on"
    for fid, status, code in (
        ("F0QUIET001", 403, "slack_scope"),
        ("F0ELSEWHR1", 403, "not_slack_file"),
        ("F0SIGNIN01", 502, "files_scope"),
        ("F0HUGE0001", 413, "too_large"),
        ("F0GONE0001", 502, "slack_failed"),
    ):
        assert call(slack, "GET", "slack/files/" + fid, status).json()["code"] == code, fid
    call(slack, "GET", "slack/files/not-a-file", 422)
    entry(slack, scope={"channels": ["#general"], "dms": "no"})
    assert call(slack, "GET", "slack/files/F0DMFILE01", 403).json()["code"] == "slack_scope"


# ----------------------------------------------------------------------------- posting
def test_a_post_goes_into_the_outbox_and_the_bots_room(slack):
    made = call(slack, "POST", "slack/posts", json={"channel": "#general", "text": "Release is out"}).json()
    mid = made["message_id"]
    assert (made["state"], made["channel"], made["name"]) == ("ready", GENERAL, "general")
    with slack.store.read() as c:
        row = c.execute("SELECT * FROM slack_posts WHERE message_id=?", (mid,)).fetchone()
        message = H.message(c, mid)
    assert (row["channel"], row["bot"], row["text"], row["state"]) == (GENERAL, "ops", "Release is out", "ready")
    assert message["sender_id"] == "bot:ops" and message["refs"]["slack_post"]["channel"] == GENERAL
    assert call(slack, "GET", "slack/posts/" + mid).json()["state"] == "ready"
    with slack.store.transaction() as c:
        c.execute("UPDATE slack_posts SET state='sent',slack_ts='1700000009.000100' WHERE message_id=?", (mid,))
    sent = call(slack, "GET", "slack/posts/" + mid).json()
    assert sent["permalink"] == "https://acme.slack.com/archives/C0000000001/p1700000009000100"
    call(slack, "GET", "slack/posts/nope", 404)
    r = slack.api.get("/api/v2/slack/posts/" + mid, headers=headers())
    assert r.status_code == 403
    threaded = call(
        slack, "POST", "slack/posts", json={"channel": GENERAL, "text": "In thread", "thread_ts": "1700000002.000100"}
    ).json()
    with slack.store.read() as c:
        assert c.execute("SELECT thread_ts FROM slack_posts WHERE message_id=?", (threaded["message_id"],)).fetchone()[
            0
        ]


def test_post_gates_list_posting_off_unknown_id_length_and_post_verb(slack):
    def refused(body, status):
        return call(slack, "POST", "slack/posts", status, json=body).json()

    assert refused({"channel": "#random", "text": "hi"}, 403)["code"] == "slack_channel"
    assert "off" in refused({"channel": "#quiet", "text": "hi"}, 403)["detail"]
    assert refused({"channel": "#later", "text": "hi"}, 409)["code"] == "slack_channel"
    assert refused({"channel": "#general", "text": "   "}, 422)["code"] == "slack_text"
    assert "4000" in refused({"channel": "#general", "text": "x" * 4001}, 422)["detail"]
    refused({"channel": "#general", "text": "hi", "thread_ts": "soon"}, 422)
    entry(slack, can=["read"])
    assert refused({"channel": "#general", "text": "hi"}, 403)["code"] == "slack_access"


def test_an_onboarding_bot_sends_nothing(slack, monkeypatch):
    from backend import lifecycle

    monkeypatch.setattr(lifecycle, "is_onboarding", lambda c, bot: True)
    assert call(slack, "POST", "slack/posts", 403, json={"channel": "#general", "text": "hi"}).json()["code"] == (
        "onboarding"
    )
    assert call(slack, "POST", "slack/dms", 403, json={"to": "ana@acme.example", "text": "hi"}).json()["code"] == (
        "onboarding"
    )


def test_the_gateway_being_off_answers_503_saying_what_to_start(slack, monkeypatch):
    with slack.store.transaction() as c:
        c.execute("UPDATE service_health SET detail=? WHERE service='slack:gateway'", (json.dumps({"state": "off"}),))
    for method, path, kw in (
        ("GET", "slack/history", {"params": {"channel": "#general", "oldest": 0}}),
        ("POST", "slack/posts", {"json": {"channel": "#general", "text": "hi"}}),
        ("POST", "slack/dms", {"json": {"to": "ana@acme.example", "text": "hi"}}),
        ("GET", "slack/files/F0FILE0001", {}),
    ):
        answer = call(slack, method, path, 503, **kw).json()
        assert answer["code"] == "slack_gateway_off" and "TICO_SLACK_GATEWAY_ENABLED" in answer["detail"], path
    # Marked connected but not answering: nobody claims the request, so the same 503, and the row is gone.
    with slack.store.transaction() as c:
        c.execute(
            "UPDATE service_health SET detail=? WHERE service='slack:gateway'", (json.dumps({"state": "connected"}),)
        )
    slack.gw.stop.set()
    time.sleep(0.05)
    monkeypatch.setattr(SR, "CLAIM_SECONDS", 0.05)
    assert call(slack, "GET", "slack/history", 503, params={"channel": "#general", "oldest": 0}).json()["code"] == (
        "slack_gateway_off"
    )
    with slack.store.read() as c:
        assert c.execute("SELECT count(*) FROM slack_requests").fetchone()[0] == 0


# ----------------------------------------------------------------------------- waiting holds no thread
def pause_gateway(s):
    """The gateway's request thread stops answering: what is asked stays pending (and is not 503 for a while)."""
    s.gw.stop.set()
    s.thread.join(5)


def resume_gateway(s):
    s.gw.stop = threading.Event()
    s.thread = threading.Thread(target=SR.serve_forever, args=(s.gw,), daemon=True)
    s.thread.start()


def pending(s):
    with s.store.read() as c:
        return c.execute("SELECT count(*) FROM slack_requests WHERE state='pending'").fetchone()[0]


def until(check, seconds=5.0):
    stop = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < stop, "timed out"
        time.sleep(0.01)


def test_bots_waiting_on_slack_do_not_hold_the_servers_threads(slack, monkeypatch):
    """With every thread pool down to two threads, six bots wait on the gateway at once and an unrelated request is
    still answered at once: a waiting read holds no thread. A sync wait would hold all of them and queue the rest."""
    monkeypatch.setattr(SR, "CLAIM_SECONDS", 30)
    portal = slack.api.portal
    loop = portal.call(asyncio.get_running_loop)
    limiter = portal.call(anyio.to_thread.current_default_thread_limiter)
    tokens, executor, small = limiter.total_tokens, loop._default_executor, ThreadPoolExecutor(2)

    def shrink(n, pool):
        limiter.total_tokens = n
        loop.set_default_executor(pool)

    pause_gateway(slack)
    portal.call(shrink, 2, small)
    try:
        answers = []
        waits = [
            threading.Thread(
                target=lambda: answers.append(
                    slack.api.get(
                        "/api/v2/slack/history",
                        params={"channel": "#general", "oldest": 0},
                        headers=headers(slack.token),
                    ).status_code
                )
            )
            for _ in range(6)
        ]
        for t in waits:
            t.start()
        until(lambda: pending(slack) == 6)
        started = time.monotonic()
        me = slack.api.get("/api/v2/me", headers=headers(slack.token))
        assert me.status_code == 200 and time.monotonic() - started < 1.0, "an unrelated request is not kept waiting"
        assert all(t.is_alive() for t in waits) and pending(slack) == 6, "the six reads are still waiting"
        resume_gateway(slack)
        for t in waits:
            t.join(10)
        assert answers == [200] * 6
    finally:
        portal.call(shrink, tokens, executor)
        small.shutdown(wait=False)


def test_past_the_cap_a_read_is_told_slack_is_busy(slack, monkeypatch):
    monkeypatch.setattr(SR, "CLAIM_SECONDS", 30)
    monkeypatch.setattr(slack.api.app.state, "slack_waits", threading.BoundedSemaphore(1))
    pause_gateway(slack)
    first = []
    waiting = threading.Thread(
        target=lambda: first.append(call(slack, "GET", "slack/files/F0FILE0001").content), daemon=True
    )
    waiting.start()
    until(lambda: pending(slack) == 1)
    for path, kw in (
        ("slack/history", {"params": {"channel": "#general", "oldest": 0}}),
        ("slack/files/F0FILE0001", {}),
    ):
        busy = call(slack, "GET", path, 503, **kw)
        assert busy.json()["code"] == "slack_busy" and busy.json()["retryable"], path
        assert busy.headers["Retry-After"] == str(SB.BUSY_RETRY_SECONDS)
    assert pending(slack) == 1, "nothing more was asked of Slack"
    resume_gateway(slack)
    waiting.join(10)
    assert first == [b"hello world"]
    # The slot is given back: the next read goes through.
    assert call(slack, "GET", "slack/history", params={"channel": "#general", "oldest": 0}).json()["ok"]


# ----------------------------------------------------------------------------- DMs
def test_a_dm_is_the_bots_message_to_a_person_who_may_sign_in(slack):
    sent = call(slack, "POST", "slack/dms", json={"to": "@U1", "text": "Your report is ready"}).json()
    assert sent["to"] == "ana@acme.example" and sent["message_id"]
    with slack.store.read() as c:
        message = H.message(c, sent["message_id"])
    assert message["sender_id"] == "bot:ops" and message["recipient_id"] == "human:ana"
    assert call(slack, "GET", "slack/posts/" + sent["message_id"]).json()["state"] == "queued"
    assert call(slack, "POST", "slack/dms", 403, json={"to": "stranger@example.com", "text": "hi"}).json()["code"] == (
        "slack_recipient"
    )
    no_slack = call(slack, "POST", "slack/dms", 409, json={"to": "ben@acme.example", "text": "hi"}).json()
    assert "Slack account" in no_slack["detail"]


# ----------------------------------------------------------------------------- the org rule check's Send anyway
def test_a_held_post_replays_as_the_bot_while_its_channel_is_still_listed(slack):
    with slack.store.transaction() as c:
        done = SB.replay_post(
            c, None, {"author": "bot:ops"}, {"channel": GENERAL, "thread_ts": "", "text": "Held words"}, None
        )
        assert c.execute("SELECT bot,text FROM slack_posts WHERE message_id=?", (done["message_id"],)).fetchone()[
            :
        ] == (
            "ops",
            "Held words",
        )
    assert "slack_post" in OR.OPERATIONS
    with slack.store.transaction() as c, pytest.raises(Exception, match="off"):
        SB.replay_post(c, None, {"author": "bot:ops"}, {"channel": "#quiet", "text": "x"}, None)


# ----------------------------------------------------------------------------- the gateway's side
def test_the_gateway_cancels_a_bots_post_to_a_slack_connect_channel_and_sends_the_rest(gateway):
    store = gateway.store
    with store.transaction() as c:
        outside = SB.queue_post(c, "cmo", {"id": "C0EXT", "name": "partners"}, "", "To partners")
        inside = SB.queue_post(c, "cmo", {"id": MARKETING, "name": "marketing"}, "", "To marketing")
    gateway.deliver()
    states = {r["message_id"]: r for r in rows(store, "SELECT message_id,state,error FROM slack_posts")}
    assert states[outside["message_id"]]["state"] == "canceled"
    assert "Slack Connect" in states[outside["message_id"]]["error"]
    assert states[inside["message_id"]]["state"] == "sent"
    assert [p["channel"] for p in gateway.slack.posts] == [MARKETING]
    # Slack is asked once per channel, then the answer is kept.
    asked = [call for call in gateway.slack.calls if call == ("conversations.info", MARKETING)]
    assert gateway.outside_workspace({"channel": MARKETING, "message_id": inside["message_id"]}) == ""
    assert [call for call in gateway.slack.calls if call == ("conversations.info", MARKETING)] == asked


def test_the_gateway_skips_a_post_when_slack_cannot_be_asked_and_gives_a_reason_when_it_refuses(gateway, monkeypatch):
    from backend import slack_gateway as G

    store = gateway.store
    with store.transaction() as c:
        queued = SB.queue_post(c, "cmo", {"id": "C0UNKNOWN", "name": "gone"}, "", "Hello")

    def unreachable(channel):
        raise G.SlackUnreachable("down")

    monkeypatch.setattr(gateway.slack, "conversations_info", unreachable)
    gateway.deliver()
    assert rows(store, "SELECT state FROM slack_posts WHERE message_id=?", queued["message_id"])[0]["state"] == "ready"
    monkeypatch.undo()
    gateway.deliver()
    row = rows(store, "SELECT state,error FROM slack_posts WHERE message_id=?", queued["message_id"])[0]
    assert row["state"] == "canceled" and "channel_not_found" in row["error"]
    assert gateway.outside_workspace({"channel": "D0AAAAAAA", "message_id": "x"}) == ""
    assert envelope  # the helper module's names stay imported for its fixtures


def test_the_request_thread_survives_a_bug_and_old_rows_and_files_are_swept(slack, monkeypatch):
    monkeypatch.setitem(SR.HANDLERS, "history", lambda gw, params, rid: 1 / 0)
    answer = call(slack, "GET", "slack/history", 502, params={"channel": "#general", "oldest": 0}).json()
    assert answer["code"] == "slack_failed" and "ZeroDivisionError" in answer["detail"]
    slack.gw.stop.set()
    time.sleep(0.05)
    old = H.shift(H.now(), seconds=-SR.KEEP_SECONDS - 5)
    with slack.store.transaction() as c:
        c.execute(
            "INSERT INTO slack_requests(id,kind,bot,state,created_at,updated_at) VALUES('old','file','ops','done',?,?)",
            (old, old),
        )
    folder = SR.files_dir(slack.store.settings)
    folder.mkdir(exist_ok=True)
    stale = folder / "old"
    stale.write_bytes(b"x")
    past = time.time() - SR.KEEP_SECONDS - 5
    import os

    os.utime(stale, (past, past))
    assert SR.sweep(slack.gw) == 1 and not stale.exists()


def test_check_file_url_and_where_a_file_was_shared():
    assert SR.check_file_url("https://files.slack.com/a") == "https://files.slack.com/a"
    with pytest.raises(SR.Refused):
        SR.check_file_url("http://files.slack.com/a")
    channels, ims = SR.file_conversations(
        {"channels": ["C1"], "groups": ["G1"], "ims": ["D1"], "shares": {"public": {"C2": []}, "private": {"D2": []}}}
    )
    assert channels == ["C1", "C2", "G1"] and ims == ["D1", "D2"]
    assert MARKETING in CHANNELS
