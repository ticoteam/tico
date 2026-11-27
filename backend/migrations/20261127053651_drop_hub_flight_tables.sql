-- The flight recorder's tables moved to their own file, hub.flight.db (backend/flight.py), which seeded itself from these
-- once; since then nothing reads or writes them here. Their index (flight_events_kind) goes with its table. perf_* stay.
DROP TABLE IF EXISTS flight_events;
DROP TABLE IF EXISTS flight_requests;
DROP TABLE IF EXISTS flight_process;
DROP TABLE IF EXISTS flight_db;
DROP TABLE IF EXISTS flight_slow;
DROP TABLE IF EXISTS flight_sql;
DROP TABLE IF EXISTS flight_web;
