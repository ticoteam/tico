-- A grant a person passed on to their bot is never above the person's own (backend/credentials.py set_grant_level):
-- rows stored at Read and write under a Read grant drop to Read, as Vault.grant now stores them.
UPDATE credential_grants SET access_level = 'read'
WHERE parent_id IS NOT NULL AND access_level <> 'read'
  AND parent_id IN (SELECT id FROM credential_grants WHERE access_level = 'read');
