"""Record computer session keys once for existing runs, before serving claims."""

import json

from backend import bot_settings, execution
from backend.shared_bots import follow


def up(conn):
    conn.execute(
        "CREATE TABLE session_computers (bot_id TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE, "
        "session_key TEXT NOT NULL, runner_id TEXT NOT NULL, fence INTEGER NOT NULL, "
        "PRIMARY KEY (bot_id, session_key))"
    )
    conn.execute("ALTER TABLE attempts ADD COLUMN computer_session_key TEXT")
    conn.execute(
        f"UPDATE attempts SET computer_session_key=lane WHERE lane LIKE 'task:%' AND {execution.SAVED_SESSION_RUN}"
    )
    configs = {}
    for run in conn.execute(
        "SELECT id,bot_id,message_id,task_id,lane,computer_session_key FROM attempts "
        f"WHERE lane IN ('main','default') AND {execution.SAVED_SESSION_RUN}"
    ):
        bot = run["bot_id"]
        if bot not in configs:
            row = conn.execute(bot_settings.ONE_SQL, (bot,)).fetchone()
            configs[bot] = follow(conn, bot, json.loads(row[0]) if row else {})
        if key := execution.run_session_key(conn, bot, configs[bot], run):
            conn.execute("UPDATE attempts SET computer_session_key=? WHERE id=?", (key, run["id"]))
    conn.execute(
        "CREATE INDEX attempts_saved_session ON attempts(bot_id, computer_session_key, created_at) "
        f"WHERE computer_session_key IS NOT NULL AND {execution.SAVED_SESSION_RUN}"
    )
    conn.execute(
        "INSERT INTO session_computers(bot_id,session_key,runner_id,fence) "
        "SELECT a.bot_id,a.computer_session_key,a.runner_id,0 FROM ("
        "SELECT bot_id,computer_session_key,runner_id,row_number() OVER ("
        "PARTITION BY bot_id,computer_session_key ORDER BY created_at DESC,rowid DESC) AS newest "
        f"FROM attempts WHERE computer_session_key IS NOT NULL AND {execution.SAVED_SESSION_RUN}"
        ") a JOIN bots b ON b.id=a.bot_id WHERE a.newest=1"
    )
