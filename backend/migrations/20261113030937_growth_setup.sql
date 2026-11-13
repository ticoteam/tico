-- Growth's first run (backend/growth_setup.py): once per team, when it first opened Growth, when each of the four
-- setup steps was first done, when all four were, and when its first numbers were first seen. Each is written once and
-- never changed: the Growth activation rate is read from them. `set_up_before_first_open` is 1 when the first read
-- already found setup done (a team set up before this was counted), so the rate can leave it out. One row, id 1.
CREATE TABLE IF NOT EXISTS growth_setup(
 id INTEGER PRIMARY KEY CHECK(id=1),
 first_opened_at TEXT, first_opened_by TEXT,
 settings_at TEXT, channel_at TEXT, source_at TEXT, month_at TEXT, done_at TEXT,
 numbers_seen_at TEXT, numbers_seen_by TEXT,
 set_up_before_first_open INTEGER);
