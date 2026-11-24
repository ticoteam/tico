-- A private sub-ticket has its own provider session now (hubdb.session_root): an open private parent whose agent's
-- session carried a private sub-ticket's turns starts that session anew, so neither ticket's words reach the other.
INSERT INTO session_epochs(conversation_id, epoch, updated_at)
SELECT DISTINCT p.conversation_id, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM tasks p
JOIN task_relations r ON r.to_task = p.id AND r.kind = 'parent'
JOIN tasks c ON c.id = r.from_task
WHERE p.status = 'open' AND p.conversation_id IS NOT NULL AND p.owner_id LIKE 'bot:%' AND c.owner_id = p.owner_id
  AND COALESCE(p.private, 1) <> 0 AND COALESCE(c.private, 1) <> 0
ON CONFLICT(conversation_id) DO UPDATE SET epoch = epoch + 1, updated_at = excluded.updated_at;
