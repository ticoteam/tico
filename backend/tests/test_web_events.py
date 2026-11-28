"""A person's page sends usage events (POST /api/v2/web-events) that a feature's measure reads later from
flight_events: an agent's chat opened in its own window (chat_window.opened, with web or desktop)."""

import json
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


def stored(app):
    rec = app.state.flight
    with flight.tables(app.state.store, write=True) as c:  # the recorder's rows are in their own file
        flight.write(c, rec.take(int(time.time() // 60 * 60)))
    with flight.tables(app.state.store) as c:
        return [
            (r[0], json.loads(r[1]))
            for r in c.execute("SELECT kind,detail FROM flight_events WHERE kind<>'start' ORDER BY id")
        ]


def test_a_chat_window_opened_is_kept_with_who_and_where(hub):
    app, client = hub
    assert send(client, "ben-test", {"event": "chat_window.opened", "surface": "web"}).status_code == 202
    assert send(client, "ana-test", {"event": "chat_window.opened", "surface": "desktop"}).status_code == 202
    assert stored(app) == [
        ("chat_window.opened", {"actor": "human:ben", "surface": "web"}),
        ("chat_window.opened", {"actor": "human:ana", "surface": "desktop"}),
    ]
    owner = client.get("/api/v2/system/metrics?section=events", headers={"Authorization": "Bearer ana-test"}).json()
    assert {e.get("actor") for e in owner["events"]["recent"] if e["kind"] == "chat_window.opened"} == {
        "human:ben",
        "human:ana",
    }
    # A bot reading the metrics gets the event, not who opened the window.
    bot = client.get("/api/v2/system/metrics?section=events", headers={"Authorization": "Bearer qa-test"}).json()
    opened = [e for e in bot["events"]["recent"] if e["kind"] == "chat_window.opened"]
    assert len(opened) == 2 and all("actor" not in e for e in opened)


@pytest.mark.parametrize(
    "token, body, status",
    [
        ("qa-test", {"event": "chat_window.opened", "surface": "web"}, 403),
        ("ben-test", {"event": "chat_window.closed", "surface": "web"}, 422),
        ("ben-test", {"event": "chat_window.opened", "surface": "phone"}, 422),
        ("ben-test", {"event": "chat_window.opened"}, 422),
        ("ben-test", {"event": "chat_window.opened", "surface": "web", "url": "/team/x"}, 422),
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
    body = {"event": "chat_window.opened", "surface": "web"}
    for _ in range(flight.WEB_EVENTS_PER_MINUTE):
        assert send(client, "ben-test", body).json() == {"kept": 1}
    assert send(client, "ben-test", body).json() == {"kept": 0}
    assert send(client, "ana-test", body).json() == {"kept": 1}
    assert len(stored(app)) == flight.WEB_EVENTS_PER_MINUTE + 1
