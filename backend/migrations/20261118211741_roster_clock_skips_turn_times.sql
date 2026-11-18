-- A finished turn writes `bots.last_turn_at` (hubdb.turn_finish, status_result), and that write moved the roster
-- clock (backend/roster_clock.py): every turn threw away the shared copy of every bot's settings and every bot's
-- declaration, which the next request read again in whole. The clock now moves on a change to any other column of
-- `bots`; hubdb.bots reads `last_turn_at` fresh in each snapshot. A test keeps this column list equal to `bots`'
-- columns without `last_turn_at` (backend/tests/test_roster_clock.py).
DROP TRIGGER IF EXISTS roster_clock_bots_update;
CREATE TRIGGER roster_clock_bots_update AFTER UPDATE OF id, slug, display_name, effort, state, created_at ON bots
BEGIN UPDATE roster_clock SET value=value+1 WHERE id=1; END;
