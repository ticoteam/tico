-- tasks_private_parties (id, requester_id, owner_id, guest, private) WHERE private IS NOT 0 is a strict prefix of
-- tasks_private_status (the same columns, then status and waiting_on) under the same WHERE. The private tasks' parties
-- read (backend/row_clock.py PARTIES) now names tasks_private_status and is still answered from the index alone, so
-- every private-task write keeps one index fewer. A new database never creates it; this drops it from one that does.
DROP INDEX IF EXISTS tasks_private_parties;
