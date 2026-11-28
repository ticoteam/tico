"""A person's page sends usage events (POST /api/v2/web-events) that a feature's measure reads later from
flight_usage: an agent's chat window opened (chat_window.opened) and the first message sent from it (chat_window.sent),
each with web or desktop, the agent and the window's own id."""

import json
import sqlite3
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import flight
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import H

PEOPLE = {
    "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
    "ben-test": Identity("human:ben", "human", "ben@acme.example"),
    "qa-test": Identity("bot:qa", "bot", agent="hermes"),
}


@pytest.fixture
def hub(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": "ana@acme.example"}))
    app = create_app(
        Settings(db_path=tmp_path / "hub.db", registry_dir=registry, flight_recorder=True, test_identities=PEOPLE)
    )
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            H.sync_registry(
                c,
                {"qa": {"name": "QA", "status": "active"}},
                {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben")]},
            )
            c.execute(
                "INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('qa','hermes','x',?,'human:ana')",
                (H.now(),),
            )
        yield app, client


def send(client, token, body):
    return client.post("/api/v2/web-events", json=body, headers={"Authorization": "Bearer " + token})


def ev(event="chat_window.opened", surface="web", agent="qa", window="w-0000001", **more):
    return {"event": event, "surface": surface, "agent": agent, "window": window, **more}


def stored(app):
    with flight.tables(app.state.store) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='flight_usage'").fetchone():
            return []
        return [
            (r[0], r[1], json.loads(r[2])) for r in c.execute("SELECT event,actor,detail FROM flight_usage ORDER BY id")
        ]


def metrics(client, token):
    return client.get(
        "/api/v2/system/metrics?section=events,usage", headers={"Authorization": "Bearer " + token}
    ).json()


def test_a_chat_window_is_kept_with_who_where_which_agent_and_window(hub):
    app, client = hub
    assert send(client, "ben-test", ev(window="w-ben-0001")).status_code == 202
    assert send(client, "ana-test", ev(surface="desktop", window="w-ana-0001")).status_code == 202
    assert send(client, "ana-test", ev(surface="desktop", agent="hermes", window="w-ana-0002")).status_code == 202
    assert send(client, "ana-test", ev("chat_window.sent", surface="desktop", window="w-ana-0001")).status_code == 202
    assert stored(app) == [
        ("chat_window.opened", "human:ben", {"surface": "web", "agent": "qa", "window": "w-ben-0001"}),
        ("chat_window.opened", "human:ana", {"surface": "desktop", "agent": "qa", "window": "w-ana-0001"}),
        ("chat_window.opened", "human:ana", {"surface": "desktop", "agent": "hermes", "window": "w-ana-0002"}),
        ("chat_window.sent", "human:ana", {"surface": "desktop", "agent": "qa", "window": "w-ana-0001"}),
    ]
    # The measure: people who opened one, windows opened, and those a message was sent from. Counts only, for a bot too.
    week = {"people": 2, "opened": 3, "web": 1, "desktop": 2, "with_message": 1}
    for token in ("ana-test", "qa-test"):
        out = metrics(client, token)
        assert out["usage"] == {"chat_window": {"7d": week, "28d": week}}
        assert "human:" not in json.dumps(out)


def test_usage_never_reaches_flight_events_which_an_older_release_shows_bots(hub):
    """A rollback that keeps the flight file runs a reader that returns flight_events' details to bots as they are: no
    usage event, and so no person, is ever there. Its table is not one of the seven a schema version drops and remakes."""
    app, client = hub
    assert send(client, "ben-test", ev()).status_code == 202
    rec = app.state.flight
    with flight.tables(app.state.store, write=True) as c:
        flight.write(c, rec.take(int(time.time() // 60 * 60)))
    with flight.tables(app.state.store) as c:
        details = [r[0] for r in c.execute("SELECT detail FROM flight_events")]
    assert not [d for d in details if "human:ben" in d or "chat_window" in d]
    assert "flight_usage" not in flight.TABLES
    with flight.tables(app.state.store, write=True) as c:  # another schema version drops only the seven
        c.execute("PRAGMA user_version=0")
    flight._READY.clear()
    with flight.tables(app.state.store) as c:
        assert c.execute("SELECT count(*) FROM flight_usage").fetchone()[0] == 1


def test_a_reader_without_the_table_reads_no_usage():
    with sqlite3.connect(":memory:") as c:
        assert flight._usage(c, int(time.time())) == {}


def test_usage_older_than_90_days_is_swept(hub):
    app, client = hub
    old = int(time.time()) - flight.LONG_DAYS * 86400 - 60
    flight.record_usage(app.state.store, "chat_window.opened", "human:ben", {"surface": "web"}, now=old)
    assert send(client, "ben-test", ev()).status_code == 202
    flight.sweep(app.state.store)
    assert [r[1] for r in stored(app)] == ["human:ben"] and len(stored(app)) == 1


@pytest.mark.parametrize(
    "token, body, status",
    [
        ("qa-test", ev(), 403),
        ("ben-test", ev("chat_window.closed"), 422),
        ("ben-test", ev(surface="phone"), 422),
        ("ben-test", {"event": "chat_window.opened", "surface": "web"}, 422),
        ("ben-test", ev(url="/team/x"), 422),
        ("ben-test", ev(window="short"), 422),
        ("ben-test", ev(agent="qa/../x"), 422),
        ("ben-test", ev(window=7), 422),
        ("ben-test", [1], 422),
    ],
)
def test_a_bot_or_unknown_event_is_refused_and_kept_nowhere(hub, token, body, status):
    app, client = hub
    assert send(client, token, body).status_code == status
    assert stored(app) == []


def test_over_a_persons_minute_allowance_events_are_dropped(hub):
    app, client = hub
    still = time.time()
    app.state.web_events_allowance.clock = lambda: still
    for _ in range(flight.WEB_EVENTS_PER_MINUTE):
        assert send(client, "ben-test", ev()).json() == {"kept": 1}
    assert send(client, "ben-test", ev()).json() == {"kept": 0}
    assert send(client, "ana-test", ev()).json() == {"kept": 1}
    assert len(stored(app)) == flight.WEB_EVENTS_PER_MINUTE + 1
