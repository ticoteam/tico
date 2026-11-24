ALTER TABLE runners ADD COLUMN cloud_key TEXT NOT NULL DEFAULT '';

UPDATE runners
SET cloud_key = (
    SELECT bot FROM cloud_machines WHERE runner_id = runners.id LIMIT 1
)
WHERE id IN (SELECT runner_id FROM cloud_machines WHERE runner_id IS NOT NULL);
