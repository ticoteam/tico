-- Who an OK was asked of when it was asked (backend/task_asks.py `_authority`). Giving the ask to someone else
-- renames the requirement's person; for a subject its module asks of one named person, only that person's OK counts.
ALTER TABLE ok_grants ADD COLUMN asked_of_id TEXT;
