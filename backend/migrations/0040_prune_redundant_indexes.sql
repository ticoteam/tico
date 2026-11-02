-- Two indexes no read needs (backend/invariants.py). invariants_lease is a strict prefix of attempts_live_lease, with the
-- same WHERE: every lease read that used it reads attempts_live_lease, which holds the id too (a covering read) and the
-- id order the scheduler's ORDER BY lease_until, id needs. invariants_open_approvals is an exact copy of
-- approvals_open_created (store.py). A new database never creates them; this drops them from one that does. A database
-- without them is unchanged.
DROP INDEX IF EXISTS invariants_lease;
DROP INDEX IF EXISTS invariants_open_approvals;
