-- A card's slot (backend/card_slot.py): `slot` is `<visit id>:<bot id>` on a job about a card in a pipeline that runs
-- card work by slot, NULL on every other job. `slot_rev` is the input revision a claimed slot job handed its run: the
-- newest message id (rowid) it carried. Both NULL keeps a job on the older path.
ALTER TABLE jobs ADD COLUMN slot TEXT;
ALTER TABLE jobs ADD COLUMN slot_rev INTEGER;
CREATE INDEX jobs_slot_queued ON jobs(slot) WHERE slot IS NOT NULL AND state='queued';
