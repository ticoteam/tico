"""Learn keeps items only for bots that run, without creating packet tasks."""
from backend import learnings as L
from backend.store import H
from backend.tests.test_api import api, post, setup_attempt  # noqa: F401
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
