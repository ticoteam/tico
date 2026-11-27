"""The flight recorder's tables moved to their own file, hub.flight.db (backend/flight.py); nothing reads or writes
the old copies in the server's database. A server that never made that file (an update straight from a release before
it) gets it first, seeded from them (`flight.keep_before_drop`). Then the seven leave hub.sqlite, their index
(flight_events_kind) with its table. The runner perf_* tables stay."""

OLD = ("flight_events", "flight_requests", "flight_process", "flight_db", "flight_slow", "flight_sql", "flight_web")


def up(conn):
    from backend import flight

    flight.keep_before_drop(conn)
    for table in OLD:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
