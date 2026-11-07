"""Re-send unanswered system asks to their askers' reachable managers or the owner.

Keep the old ask as history and link it to its replacement. Fresh databases have no asks;
the repair runs on existing installs, whose message, reach and thread tables already exist.
"""


def up(conn):
    from backend import ask_routing, hubdb as H
    if H._has_table(conn, "messages") and conn.execute(
            "SELECT 1 FROM messages WHERE kind='ask' AND recipient_id='keeper' LIMIT 1").fetchone():
        ask_routing.repair(conn)
