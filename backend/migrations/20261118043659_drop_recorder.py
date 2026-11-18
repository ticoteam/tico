"""The Tico Recorder, the Mac app, is gone (meetings come from Granola now), and with it what only it wrote.

Dropped: `recorder_installs` (which Recorder each person ran) and `live_meetings.client` (only ever `tico-recorder`).
A call whose status came from a Recorder has none now: a live one is counted empty until Google answers, as when its
Recorder let go. Each step checks first, so a second run changes nothing.

Kept, unused: `calls.recorder_actor`, `calls.recorder_claimed_at` and `humans.record_on_join`. Pending migrations must
give the same schema in any order, and an install still below the floor may add these after this file runs
(20261110060000_meet_calls, 20261111012124_calendar_tables); drop them once the floor is past both. `calls.status_source`
keeps its CHECK naming 'recorder': changing it means rebuilding the table, and nothing writes it any more.
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    conn.execute("DROP TABLE IF EXISTS recorder_installs")
    if "status_source" in _columns(conn, "calls"):
        conn.execute(
            "UPDATE call_participants SET in_call=0 WHERE call_id IN "
            "(SELECT id FROM calls WHERE status_source='recorder' AND state='live')"
        )
        conn.execute(
            "UPDATE calls SET live_count=CASE WHEN state='live' THEN 0 ELSE live_count END,status_source=NULL "
            "WHERE status_source='recorder'"
        )
    if "client" in _columns(conn, "live_meetings"):
        conn.execute("ALTER TABLE live_meetings DROP COLUMN client")
