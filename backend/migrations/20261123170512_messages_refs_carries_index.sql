-- The carriers of a message (threads._carrier, refs.carries): deleting a message ends the task delegations they lent
-- (hubdb.expire_delegations), found by what they carry, with no scan of the delegations or the room. Partial, so a
-- message that carries nothing adds no entry.
CREATE INDEX IF NOT EXISTS messages_refs_carries ON messages(json_extract(refs,'$.carries'))
WHERE json_extract(refs,'$.carries') IS NOT NULL;
