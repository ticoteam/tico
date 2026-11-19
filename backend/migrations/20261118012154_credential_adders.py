"""Credential adders, recovered from the record: a person or bot who added a credential may remove it.

1. A legacy vault row may have a creation event but no recorded adder. When every `credential.created` event for it
   names the same person or bot, that one is its adder. A computer-file import emits `credential.migrated`, which names
   the runner and recipient rather than the adder, so it recovers nothing.
2. A shared model key moved from the vault to Models (20261117061243_computer_keys_to_subscriptions) took 'tico' as its
   fallback adder; it takes its credential's adder. On a company from before that move, `model_subscriptions` has no
   `credential_id` yet: the move runs later and reads the adder recovered here.
"""

CREDENTIALS = """
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
  AND (creators.actor GLOB 'human:?*' OR creators.actor GLOB 'bot:?*')
"""

SUBSCRIPTIONS = """
UPDATE model_subscriptions
SET added_by = credentials.created_by
FROM credentials
WHERE model_subscriptions.credential_id = credentials.id
  AND model_subscriptions.added_by = 'tico'
  AND credentials.kind <> 'service'
  AND (credentials.created_by GLOB 'human:?*' OR credentials.created_by GLOB 'bot:?*')
"""


def up(conn):
    conn.execute(CREDENTIALS)
    if "credential_id" in {r[1] for r in conn.execute("PRAGMA table_info(model_subscriptions)")}:
        conn.execute(SUBSCRIPTIONS)
