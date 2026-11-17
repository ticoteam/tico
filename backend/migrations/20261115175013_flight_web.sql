-- People's browser timings (backend/flight.py, POST /api/v2/web-timings): histogram buckets per minute or hour, metric
-- (lcp, inp) and screen template only; never who, which device or which address.
CREATE TABLE IF NOT EXISTS flight_web(created_at INTEGER NOT NULL, span INTEGER NOT NULL, metric TEXT NOT NULL,
  route TEXT NOT NULL, n INTEGER NOT NULL, hist TEXT NOT NULL, PRIMARY KEY(created_at, span, metric, route)) WITHOUT ROWID;
