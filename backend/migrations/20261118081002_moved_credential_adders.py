"""Adders of credentials brought over from a computer, recovered from their grants: whoever brought a key over may
remove it, the same as one they added by hand.

A key imported from a computer's files has no `credential.created` event, so 20261118012154_credential_adders left its
adder empty. When every grant of such a credential was made by the same person or bot, that one brought it over and is
its adder. A grant made by a runner, by 'tico', or grants by more than one actor recover nothing.
"""

CREDENTIALS = """
WITH granters AS (
  SELECT credential_id, min(granted_by) AS actor
  FROM credential_grants
  GROUP BY credential_id
  HAVING count(DISTINCT granted_by) = 1 AND count(granted_by) = count(*)
)
UPDATE credentials
SET created_by = granters.actor
FROM granters
WHERE credentials.id = granters.credential_id
  AND credentials.kind <> 'service'
  AND (credentials.created_by IS NULL OR credentials.created_by = '')
  AND (granters.actor GLOB 'human:?*' OR granters.actor GLOB 'bot:?*')
"""


def up(conn):
    conn.execute(CREDENTIALS)
