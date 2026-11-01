"""A computer's event stream (backend/events.py `runner_read`, `GET /api/v2/runners/me/events`): work and
changes reach only the computer they concern, as hints with no values; a resume sends only what came
since, a client past the log is reset, and a revoked computer's stream ends."""
import asyncio
import json
import sqlite3

import pytest

from backend import events as E
from backend import hubdb as H
from backend.tests.test_api import api, assign, post, ready, runner  # noqa: F401
from backend.tests.test_credential_sharing import JIRA, jira, local
from backend.tests.test_events import live, topic  # noqa: F401


def told(api, computer, after):
    with api.app.state.store.read() as c:
        return E.runner_read(c, computer["runner_id"], "runner:" + computer["runner_id"], after)


def newest(api):
    with api.app.state.store.read() as c:
        return E.latest(c)


def test_work_and_credentials_reach_only_the_computer_they_concern_without_values(api):
    local(api)
    a, b = runner(api, label="A"), runner(api, label="B")
    assign(api, a, "ops")
    ready(api, a, ["ops"]), ready(api, b, [])
    start = newest(api)
    post(api, "chat/ops", {"text": "Please summarize the current work."})
    sent, cursor, reset, _ = told(api, a, start)
    assert [data["kind"] for _, data in sent] == ["work"] and sent[0][1]["bots"] == ["ops"] and not reset
    assert told(api, b, start)[0] == []
    # A credential and its grant: every computer re-reads the names it may use, and is sent no value.
    row = jira(api)
    post(api, f"credentials/{row['id']}/grants", {"subject": "bot:ops"})
    for computer in (a, b):
        sent = told(api, computer, cursor)[0]
        assert [data["kind"] for _, data in sent] == ["credentials"]
        assert JIRA not in json.dumps(sent) and set(sent[0][1]) == {"seq", "kind", "bots"}


def test_the_migration_dropping_a_bots_subscription_refreshes_only_its_computer_without_values(api):
    a, b = runner(api, label="A"), runner(api, label="B")
    assign(api, a, "ops")
    assign(api, b, "cpo")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO subscription_assignments VALUES(?,?,?,?,?)", ("bot", "ops", "private-first", H.now(), "human:ana"))
    cursor = newest(api)
    # One login per AI tool per computer (#1259): the bot's choice goes, and its computer moves it to its own login.
    with api.app.state.store.transaction() as c:
        H._apply(c, H.ONE_LOGIN_PER_COMPUTER_SCHEMA)
    sent, after, reset, _ = told(api, a, cursor)
    assert [data["kind"] for _, data in sent] == ["assignments", "work"]
    assert not reset and all(data["bots"] == ["ops"] for _, data in sent)
    assert all(set(data) == {"seq", "kind", "bots"} for _, data in sent)
    assert "private-" not in json.dumps(sent)
    assert told(api, b, cursor)[0] == []
    cursor = after
    # Retired group choices remain stored for compatibility, but no longer select a connection.
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO subscription_assignments VALUES(?,?,?,?,?)",
                  ("group", "ops", "private-retired", H.now(), "human:ana"))
    assert told(api, a, cursor)[0] == told(api, b, cursor)[0] == []


def test_resume_sends_only_what_came_since_and_a_client_past_the_log_is_reset(api):
    a = runner(api)
    assign(api, a, "ops")
    post(api, "chat/ops", {"text": "First."})
    cursor = newest(api)
    post(api, "chat/ops", {"text": "Second."})
    sent, _, reset, _ = told(api, a, cursor)
    assert len(sent) == 1 and sent[0][0] > cursor and not reset
    assert E.sweep(api.app.state.store, H.shift(H.now(), hours=E.KEEP_HOURS + 1)) > 0
    post(api, "chat/ops", {"text": "Third."})
    assert told(api, a, 1)[2] and told(api, a, 10**9)[2]


@pytest.mark.slow
def test_the_stream_tells_its_computer_keeps_it_seen_and_ends_once_revoked(api, live):
    a, b = runner(api, label="A"), runner(api, label="B")
    assign(api, a, "ops")
    mine, other = (live(computer["token"], path="/api/v2/runners/me/events") for computer in (a, b))
    for stream in (mine, other):
        assert stream.until(topic("ready"))["data"]["release"] is not None
    post(api, "chat/ops", {"text": "Please summarize the current work."})
    got = mine.until(topic("runner", kind="work"))
    assert got["data"]["bots"] == ["ops"] and int(got["id"]) == got["data"]["seq"]
    other.drain(1.0)
    assert not [event for event in other.seen if event["event"] == "runner"]
    # The stream is contact: the computer stays seen while it sends nothing else.
    mine.until(topic("keepalive"))
    with api.app.state.store.read() as c:
        seen = c.execute("SELECT last_seen_at FROM runners WHERE id=?", (a["runner_id"],)).fetchone()[0]
    assert seen > H.shift(H.now(), seconds=-3)
    post(api, f"runners/{a['runner_id']}/revoke", {})
    mine.until(topic("expired"))


def test_a_quiet_stream_keeps_sending_keepalives_while_the_database_is_busy(api, live):
    # The contact write behind a keepalive waits for the write lock; the keepalive does not.
    computer = runner(api, label="A")
    stream = live(computer["token"], path="/api/v2/runners/me/events")
    stream.until(topic("ready"))
    holder = sqlite3.connect(str(api.app.state.store.settings.db_path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        for _ in range(3):
            stream.until(topic("keepalive"), timeout=5)
    finally:
        holder.rollback()
        holder.close()


def test_keepalives_go_out_while_other_computers_changes_keep_ringing_the_bell(api, live, monkeypatch):
    # Every wait ends with a ring for a change this computer is not sent: only keepalives hold its stream open.
    computer = runner(api, label="A")
    bell = E.runner_bell(api.app.state.store)

    async def rings(generation, timeout):
        await asyncio.sleep(0.05)
        return True

    stream = live(computer["token"], path="/api/v2/runners/me/events")
    stream.until(topic("ready"))
    monkeypatch.setattr(bell, "wait", rings)
    for _ in range(3):
        stream.until(topic("keepalive"), timeout=5)
