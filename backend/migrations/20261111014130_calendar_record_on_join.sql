-- A person's "Record calls I join" (Calendar, spec 12.5): null means on.
ALTER TABLE humans ADD COLUMN record_on_join INTEGER CHECK (record_on_join IN (0, 1));
