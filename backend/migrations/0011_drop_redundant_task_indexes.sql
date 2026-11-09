-- Three task indexes that no read needs (backend/hubdb.py): tasks_owner_status and tasks_requester_status are covered by
-- tasks_owner_status_cover and tasks_requester_status_cover, which every owner and requester status read uses, and
-- tasks_private is a strict prefix of tasks_private_parties (same WHERE). A new database never creates them; this drops
-- them from one that does. A database without them is unchanged.
DROP INDEX IF EXISTS tasks_owner_status;
DROP INDEX IF EXISTS tasks_requester_status;
DROP INDEX IF EXISTS tasks_private;
