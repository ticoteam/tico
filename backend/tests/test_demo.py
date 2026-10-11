"""Demo mode: a fixed fictional company that passes the app's own rules, cannot be mistaken for an
install, listens only where it may, and never touches the network."""

import socket
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)      # a Friday, so a week in review exists


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    directory = tmp_path_factory.mktemp("demo")
    return directory, demo.build(directory, now=NOW)


def rows(path):
    with sqlite3.connect(path) as db:
        tables = [table for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                                   "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        # Ordered by the first two columns, or the only one.
        return {table: db.execute(f'SELECT * FROM "{table}" ORDER BY ' + ("1" if len(
            db.execute(f'PRAGMA table_info("{table}")').fetchall()) == 1 else "1,2")).fetchall() for table in tables}


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def test_saved_demo_reopens_with_edits_and_the_same_owner_session(built, monkeypatch):
    directory, settings = built
    with signed_in(settings) as api:
        response = api.post("/api/v2/tasks", json={"title": "Keep this demo edit", "body": "Sample work",
                                                 "owner": "bot:support"},
                            headers={"Idempotency-Key": "restart-edit"})
        assert response.status_code == 200
        task_id = response.json()["task"]["id"]
    before = rows(settings.db_path)
    token = settings.local_owner_token_file.read_bytes()
    from backend import demo_seed
    monkeypatch.setattr(demo_seed, "populate", lambda *args: pytest.fail("saved demo was reseeded"))
    reopened = demo.build(directory, now=NOW)
    assert reopened.local_owner_token_file.read_bytes() == token
    assert rows(reopened.db_path) == before
    with signed_in(reopened) as api:
        assert api.get("/api/v2/tasks/" + task_id).json()["task"]["title"] == "Keep this demo edit"


def test_demo_refuses_existing_unrecognized_data_without_writing(tmp_path):
    database = tmp_path / "hub.sqlite"
    database.write_bytes(b"existing install or incomplete seed")
    with pytest.raises(demo.Problem, match="completed Tico demo"):
        demo.build(tmp_path, now=NOW)
    assert database.read_bytes() == b"existing install or incomplete seed"
    assert list(tmp_path.iterdir()) == [database]


def test_only_localhost_may_ask(built):
    with signed_in(built[1]) as api:
        assert api.get("/api/v2/config", headers={"Host": "192.168.1.20:8765"}).status_code == 421
        assert api.get("/api/v2/config", headers={"Host": "localhost:8765"}).status_code == 200


def test_a_public_demo_is_read_only(tmp_path):
    settings = demo.build(tmp_path, now=NOW, url="https://demo.example.com", public=True)
    with signed_in(settings) as api:
        api.headers["Host"] = "demo.example.com"
        assert api.get("/api/v2/config").status_code == 200
        blocked = api.post("/api/v2/tasks", json={"title": "x", "owner": "bot:support"}, headers={"Idempotency-Key": "p1"})
        assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "demo"
        assert api.post("/api/v2/updates/read", json={"ids": []}, headers={"Idempotency-Key": "p2"}).status_code == 200


def test_nothing_leaves_the_machine(built):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    demo.ATTEMPTS.clear()
    with demo.network_blocked():
        for target in (("example.com", 80), ("93.184.216.34", 443)):
            with pytest.raises(OSError, match="no outbound"):
                socket.create_connection(target, timeout=1)
        with pytest.raises(OSError):
            socket.getaddrinfo("api.github.com", 443)
        socket.create_connection(server.getsockname(), timeout=1).close()      # this machine is fine
        with signed_in(built[1]) as api:                                       # and so is every page of the demo
            for path in ("/api/v2/config", "/api/v2/health", "/api/v2/updates", "/api/v2/market/entities"):
                assert api.get(path).status_code == 200
            assert api.post("/api/v2/market/ask", json={"question": "Who competes with us?"},
                            headers={"Idempotency-Key": "net-1"}).status_code == 200      # falls back to the graph, no model
    server.close()
    assert demo.ATTEMPTS == ["example.com", "93.184.216.34", "api.github.com"]     # only the three we tried
    with pytest.raises(OSError):                                                   # and the block is gone outside
        socket.create_connection(("192.0.2.1", 9), timeout=0.2)


def test_the_tidy_sample_is_a_reviewed_check_and_a_decision_left_to_ana(built):
    """The outside-specialist example: the check is done and closed by Ana, the booking waits on her, nothing is live."""
    from backend.demo_content import SAMPLE_LABEL
    with sqlite3.connect(built[1].db_path) as db:
        db.row_factory = sqlite3.Row
        task = lambda title: db.execute("SELECT * FROM tasks WHERE title=?", (title,)).fetchone()
        check = task("Check the office is ready for next week's customer workshop")
        follow_up = task("Decide whether to request an extra cleaning before the workshop")
        moves = db.execute("SELECT actor,field,new FROM task_events WHERE task_id=? AND field IN ('status','note') "
                           "ORDER BY ts", (check["id"],)).fetchall()
        relation = db.execute("SELECT kind FROM task_relations WHERE from_task=? AND to_task=?",
                              (follow_up["id"], check["id"])).fetchone()
    assert (check["owner"], check["requester"], check["status"]) == ("bot:coo", "human:ana", "closed")
    assert [tuple(m)[:2] for m in moves][-3:] == [("bot:coo", "note"), ("bot:coo", "status"), ("human:ana", "status")]
    assert moves[-3]["new"].startswith(SAMPLE_LABEL) and "no booking has been made" in moves[-3]["new"]
    assert [m["new"] for m in moves[-2:]] == ["done", "closed"] and check["closed_by"] == "human:ana"
    assert (follow_up["owner"], follow_up["requester"], follow_up["status"]) == ("human:ana", "bot:coo", "open")
    assert relation["kind"] == "follow_up"
    demo.ATTEMPTS.clear()
    with demo.network_blocked(), signed_in(built[1]) as api:                    # reading it reaches no one
        comments = api.get(f"/api/v2/tasks/{check['id']}/comments").json()["comments"]
        assert any(SAMPLE_LABEL in c["body"] and "TIDY" in c["body"] for c in comments)
        tidy = [t for t in api.get("/api/v2/bots/coo/tools").json()["tools"] if t["service"] == "tidy"]
        assert len(tidy) == 1 and tidy[0]["status"] == "unknown" and SAMPLE_LABEL in tidy[0]["note"]   # never shown healthy
        assert not any(t["service"] == "tidy" for t in api.get("/api/v2/bots/support/tools").json()["tools"])
    assert demo.ATTEMPTS == []
