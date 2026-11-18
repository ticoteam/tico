-- Legacy vault rows may have a creation event but no recorded adder. Computer-file imports
-- emit credential.migrated, which identifies the runner and recipient rather than the adder.
WITH creators AS (
  SELECT target, min(actor) AS actor
  FROM events
  WHERE action = 'credential.created'
  GROUP BY target
  HAVING count(DISTINCT actor) = 1 AND count(actor) = count(*)
)
UPDATE credentials
SET created_by = creators.actor
FROM creators
WHERE credentials.id = creators.target
  AND credentials.kind <> 'service'
  AND (credentials.created_by IS NULL OR credentials.created_by = '')
  AND (creators.actor GLOB 'human:?*' OR creators.actor GLOB 'bot:?*');

-- A shared model key moved from the vault to Models with 'tico' as its fallback adder.
UPDATE model_subscriptions
SET added_by = credentials.created_by
FROM credentials
WHERE model_subscriptions.credential_id = credentials.id
  AND model_subscriptions.added_by = 'tico'
  AND credentials.kind <> 'service'
  AND (credentials.created_by GLOB 'human:?*' OR credentials.created_by GLOB 'bot:?*');
