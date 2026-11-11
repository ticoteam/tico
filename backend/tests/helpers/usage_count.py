"""Helpers that other tests share, moved out of test_usage_count.py so they import no test file."""

import sqlite3


def complete_a_bot_turn(api, when, bot="assistant"):
    with sqlite3.connect(str(api.app.state.store.settings.db_path)) as c:
        c.execute(
            "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,finished_at) "
            "VALUES(?,'j1',?,'r1',1,?,'completed',?,?,?)",
            ("a-" + bot, bot, "h-" + bot, when, when, when),
        )
