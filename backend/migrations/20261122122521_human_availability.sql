-- A person's availability for routed work (backend/routing.py): on, async or off, set by them or an admin; '' is
-- derived from their working hours. availability_until ends a set value: past it, the derived value applies again.
ALTER TABLE humans ADD COLUMN availability TEXT NOT NULL DEFAULT '' CHECK (availability IN ('', 'on', 'async', 'off'));
ALTER TABLE humans ADD COLUMN availability_until TEXT;
