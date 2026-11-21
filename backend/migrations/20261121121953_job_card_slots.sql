-- A card's slot (backend/card_slot.py): `<visit id>:<bot id>` on a job about a card in a pipeline that runs card work
-- by slot, NULL on every other job, which keeps the older path.
ALTER TABLE jobs ADD COLUMN slot TEXT;
CREATE INDEX jobs_slot_queued ON jobs(slot) WHERE slot IS NOT NULL AND state='queued';
