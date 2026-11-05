"""Learn keeps items only for bots that run, without creating packet tasks."""
import pytest

from backend import learnings as L
from backend import maintenance, scheduler
from backend.store import H
from backend.tests.test_api import api, headers, post, setup_attempt  # noqa: F401
from backend import bot_settings as BS


def test_only_a_bot_that_ran_today_gets_pending_items_and_no_packet_task(api):
    store = api.app.state.store
    machine, _, attempt = setup_attempt(api)               # ops ran today; finance and an external bot did not
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'today'}, machine['token'])
    with store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('grok-designer','grok-designer','Designer','active')")
        c.execute("INSERT INTO bot_config(bot,owner_id) VALUES('grok-designer','ana')")
        BS.write(c, 'grok-designer', {'harness': 'grokbot'})
        team = L.roster(c, store.settings)
        assert set(team) == {"ops"}
        item = {"key": "chat:1", "source": "chat", "author": "human:ana", "created_at": H.now(), "ref": {},
                "label": "Chat", "text": "Invoices go out on Fridays.", "learnable": 0.9, "scores": {"ops": 0.9}}
        before = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
        deliveries = []
        for night in ("2026-10-12", "2026-10-13"):
            run_id = H.new_id()
            c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES(?,?,'running',?)", (run_id, night, H.now()))
            L.dispatch(c, run_id, night, [dict(item)], {"ops": [item]}, team, {})
            deliveries.append(c.execute("SELECT task_id,update_batch_id FROM learning_deliveries WHERE run_id=?",
                                        (run_id,)).fetchone())
        assert all(tuple(row) == (None, None) for row in deliveries)
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == before


@pytest.mark.parametrize("path", ["learnings/run", "system-routines/learnings/run"])
def test_run_now_uses_maintenance_switch_permissions_and_idempotency(api, monkeypatch, path):
    store = api.app.state.store
    monkeypatch.setattr(maintenance, "_start", lambda s, a, run_id, only, wait: maintenance._execute(s, a, run_id, only))
    monkeypatch.setattr(L, "engine_for", lambda *args: None)
    post(api, path, {}, token="cara-test", expected=403)
    _, _, attempt = setup_attempt(api)
    post(api, path, {}, token=attempt["token"], expected=403)
    request_headers = {**headers("ben-test"), "Idempotency-Key": "learn-once"}
    first = api.post("/api/v2/" + path, json={}, headers=request_headers)
    assert first.status_code == 200, first.text
    first = first.json()
    assert first["started"] and first["run"]["step"] == "learn"
    assert first["run"]["steps"][0]["status"] == "off"
    with store.transaction() as c:
        maintenance.save_step(c, "human:ana", {"key": "learn", "enabled": True})
    repeated = api.post("/api/v2/" + path, json={}, headers=request_headers).json()
    assert not repeated["started"] and repeated["run"]["id"] == first["run"]["id"]
    reply = post(api, path, {}, token="ben-test")
    assert reply["started"] and reply["run"]["steps"][0]["reason"] == "no decision model"
    with store.read() as c:
        assert [tuple(row) for row in c.execute("SELECT id,status FROM learning_runs")] == [
            (reply["run"]["id"], "unconfigured")]
        assert c.execute("SELECT count(*) FROM system_runs WHERE job='learnings'").fetchone()[0] == 0


def test_both_run_now_routes_join_the_live_maintenance_run(api, monkeypatch):
    store, auth = api.app.state.store, api.app.state.auth
    monkeypatch.setattr(maintenance, "_start", lambda *args, **kwargs: None)
    live, _ = maintenance.run_now(store, auth, "human:ana")
    for path in ("learnings/run", "system-routines/learnings/run"):
        reply = post(api, path, {}, token="ben-test")
        assert not reply["started"] and reply["run"]["id"] == live["id"]
    assert scheduler.run_now(store, auth, "learnings", "human:ana") is False
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM maintenance_runs").fetchone()[0] == 1
