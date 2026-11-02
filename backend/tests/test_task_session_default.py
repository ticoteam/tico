"""Every task is its own session by default: the shared keying, and the one-time clearing of the old written default."""
import json

from backend.tests.test_api import api  # noqa: F401  (api is a fixture)
from clients import sessions


def test_task_turns_get_their_own_session_unless_the_bot_opts_out():
    assert sessions.session_key({}, "ops", "t1", "c1") == "task:t1"
    assert sessions.session_key({}, "ops", None, "c1") == sessions.BOT_THREAD          # chat keeps one thread
    assert sessions.lane({}, "ops", "t1", "c1") == "task:t1"                             # server and runner agree
    assert sessions.session_key({"session": "bot"}, "ops", "t1", "c1") == sessions.BOT_THREAD
    assert sessions.lane({"session": "bot"}, "ops", "t1", "c1") == sessions.MAIN_LANE
    assert sessions.session_key({"session": "task"}, "ops", None, "c1") == "conversation:c1"
    assert sessions.session_key({}, "botops", None, "c1") == "conversation:c1"
    # A stateless bot's task keeps its own session even on `session: bot`; its chat keeps the bot's thread.
    assert sessions.session_key({"stateless": True, "session": "bot"}, "ops", "t1", "c1") == "task:t1"
    assert sessions.lane({"stateless": True, "session": "bot"}, "ops", "t1", "c1") == "task:t1"
    assert sessions.session_key({"stateless": True, "session": "bot"}, "ops", None, "c1") == sessions.BOT_THREAD


def test_the_old_written_bot_default_is_cleared_once(api):
    store = api.app.state.store
    def session(bot):
        with store.transaction() as c:
            return json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]).get("session", "")
    def put(bot, value):
        with store.transaction() as c:
            c.execute("UPDATE bot_config SET config=json_set(config,'$.session',?) WHERE bot=?", (value, bot))
    put("ops", "bot"), put("cpo", "task")
    with store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='task_session_default'")
    store.initialize(seed_market=False)
    assert (session("ops"), session("cpo")) == ("", "task")
    put("ops", "bot")                                     # a later explicit choice stays
    store.initialize(seed_market=False)
    assert session("ops") == "bot"


def test_a_retired_assignment_hands_its_task_back_awake_but_waits_out_a_running_turn(api):
    """Temporary assignments are retired (#420): an open task goes back to its role through the handoff,
    which queues the role's turn, and its setup reminder is closed; an assignment with a turn still out is
    left for a later startup."""
    from backend.shared_bots import retire_assignments
    from backend.store import H
    from backend.tests.test_api import post, runner
    machine = runner(api)
    task = post(api, "tasks", {"owner": "ops", "title": "Assigned work", "body": "Keep going."})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state,created_at) VALUES('ops-a1','ops-a1','Ops A1','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES('ops-a1',?,'ana')",
                  (json.dumps({"assignment_branch": True, "shared_from": "ops"}),))
        c.execute("UPDATE tasks SET owner_id='bot:ops-a1',status='doing' WHERE id=?", (task["id"],))
        job = c.execute("SELECT id FROM jobs WHERE bot_id='ops'").fetchone()[0]
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
                  "VALUES('a1',?,'ops-a1',?,0,'h','running',?,?)", (job, machine["runner_id"], H.now(), H.now()))
        reminder = H._setup_reminder(c, "ops-a1")
        retire_assignments(c)
        assert H.task(c, task["id"])["owner_id"] == "bot:ops-a1"
        assert H.bot(c, "ops-a1")["state"] == "active"
        assert H.task(c, reminder["id"])["status"] == "open"
        c.execute("UPDATE attempts SET state='completed' WHERE id='a1'")
        retire_assignments(c)
        retire_assignments(c)
        assert H.task(c, task["id"])["owner_id"] == "bot:ops"
        assert H.bot(c, "ops-a1")["state"] == "archived"
        assert H.task(c, reminder["id"])["status"] == "done", "a retired actor's setup reminder leaves Needs you"
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='ops' AND state='queued'").fetchone()[0] == 1
