-- Growth's first run on the web (backend/growth_setup.py, ui/app/growth-start.ts): when the Get started panel said
-- "you're set up". It is said once per team, then the panel never comes back. Its own one-row table, so it stands
-- alone: it does not depend on growth_setup's migration having run first.
CREATE TABLE IF NOT EXISTS growth_setup_shown(
 id INTEGER PRIMARY KEY CHECK(id=1),
 done_shown_at TEXT);
