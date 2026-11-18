"""The Tico Recorder, the Mac app, is gone (meetings come from Granola now), and with it what only it wrote.

Dropped: `recorder_installs` (which Recorder each person ran), `calls.recorder_actor` and `calls.recorder_claimed_at`
(whose Recorder was a Google Meet call's recording source), `live_meetings.client` (only ever `tico-recorder`) and
`humans.record_on_join` (the Recorder's Record calls I join setting). A call whose status came from a Recorder has
none now: a live one is counted empty until Google answers, as when its Recorder let go. Each step checks first, so a
second run changes nothing.

`calls.status_source` keeps its CHECK naming 'recorder': changing it means rebuilding the table, and nothing writes
it any more.
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    conn.execute("DROP TABLE IF EXISTS recorder_installs")
    calls = _columns(conn, "calls")
    if "status_source" in calls:
        conn.execute(
            "UPDATE call_participants SET in_call=0 WHERE call_id IN "
            "(SELECT id FROM calls WHERE status_source='recorder' AND state='live')"
        )
        conn.execute(
            "UPDATE calls SET live_count=CASE WHEN state='live' THEN 0 ELSE live_count END,status_source=NULL "
            "WHERE status_source='recorder'"
        )
    for column in ("recorder_actor", "recorder_claimed_at"):
        if column in calls:
            conn.execute(f"ALTER TABLE calls DROP COLUMN {column}")
    if "client" in _columns(conn, "live_meetings"):
        conn.execute("ALTER TABLE live_meetings DROP COLUMN client")
    if "record_on_join" in _columns(conn, "humans"):
        conn.execute("ALTER TABLE humans DROP COLUMN record_on_join")
