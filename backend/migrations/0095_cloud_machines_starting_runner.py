"""Every heartbeat asks whether its computer is a cloud machine still booting (cloud_machines.checked_in). This partial
index visits only the rows in 'starting', so a self-hosted computer's beat reads none.
"""


def up(conn):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_machines'").fetchone():
        conn.execute("CREATE INDEX IF NOT EXISTS cloud_machines_starting_runner ON cloud_machines(runner_id) "
                     "WHERE state='starting'")
