-- A reply to a person reaches them as an @mention does (threads.plan, reason `reply`), so Needs you's "messages to
-- answer" (thread_attention._addressed) and the rail's flags count it: the partial index holds those deliveries too.
DROP INDEX IF EXISTS message_deliveries_addressed;
CREATE INDEX IF NOT EXISTS message_deliveries_addressed ON message_deliveries(recipient, message_id)
  WHERE reason IN ('mention', 'ask', 'assign', 'addressed', 'reply') AND state IN ('pending', 'delivered', 'read');

-- A bot's say, notice or ask in a task's own thread reaches a person for an @mention, a reply to them or following the
-- task (hubdb.BOT_TASK_REASONS). The trigger's own row is always 'addressed' or 'ask', so it writes what it wrote
-- before; it is re-made so its condition stays hubdb.delivery_counts_sql word for word (an applied file is never
-- edited).
DROP TRIGGER IF EXISTS threads_primary_delivery;
CREATE TRIGGER threads_primary_delivery AFTER INSERT ON messages WHEN COALESCE(NEW.recipient_id,'')<>'' AND NOT
    COALESCE(json_extract(CASE WHEN json_valid(NEW.refs) THEN NEW.refs END,'$.carries'),'')<>'' AND NOT
    (NEW.recipient_id LIKE 'human:%' AND ((NEW.sender_id='keeper' AND COALESCE(json_extract(CASE WHEN
    json_valid(NEW.refs) THEN NEW.refs END,'$.task'),'')<>'') OR ((NEW.sender_id LIKE 'bot:%' AND NEW.kind IN
    ('say','notice','ask') AND EXISTS (SELECT 1 FROM tasks t WHERE t.conversation_id=NEW.conversation_id)) AND
    COALESCE('addressed','') NOT IN ('mention','reply','follower')))) BEGIN INSERT OR IGNORE INTO
    message_deliveries(id,message_id,recipient,thread_id,root_id,mode,reason,cause,state,revision,delivered_at,read_at,created_at,updated_at,unsolicited)
    VALUES(lower(hex(randomblob(16))),NEW.id,NEW.recipient_id,NEW.conversation_id,(SELECT COALESCE(root_id,id) FROM
    conversations WHERE id=NEW.conversation_id),CASE WHEN NEW.recipient_id LIKE 'slack:%' OR NEW.recipient_id LIKE
    'peer:%' THEN 'none' WHEN COALESCE(json_extract(CASE WHEN json_valid(NEW.refs) THEN NEW.refs END,'$.quiet'),0)
    THEN 'defer' ELSE 'queue' END,CASE WHEN NEW.recipient_id LIKE 'slack:%' OR NEW.recipient_id LIKE 'peer:%' THEN
    'outside' WHEN NEW.kind='ask' THEN 'ask' ELSE 'addressed' END,CASE WHEN NEW.sender_id LIKE 'bot:%' THEN 'bot' WHEN
    NEW.sender_id LIKE 'human:%' THEN 'human' WHEN NEW.sender_id LIKE 'slack:%' OR NEW.sender_id LIKE 'peer:%' THEN
    'outside' ELSE 'keeper' END,CASE WHEN NEW.delivered_at IS NOT NULL AND NEW.read_at IS NOT NULL THEN 'read' WHEN
    NEW.delivered_at IS NOT NULL THEN 'delivered' ELSE 'pending'
    END,COALESCE(NEW.revision,0),NEW.delivered_at,NEW.read_at,NEW.created_at,NEW.created_at,COALESCE(json_extract(CASE
    WHEN json_valid(NEW.refs) THEN NEW.refs END,'$.unsolicited'),0)); END;
