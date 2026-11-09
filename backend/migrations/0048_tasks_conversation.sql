-- A task's own thread is found by its conversation (hubdb.task_post_sql, the write-side trigger and the read-side unread,
-- inbox and sidebar predicates): an index on tasks.conversation_id keeps each EXISTS a point lookup.
CREATE INDEX IF NOT EXISTS tasks_conversation ON tasks(conversation_id);

-- A bot's say, notice or ask in a task's own thread reaches a person only by an @mention (hubdb.counts_for_person).
-- Migration 0047 made threads_primary_delivery with the task-news rule alone, and an applied file is never edited, so
-- this re-makes it. The condition is hubdb.delivery_counts_sql with the reason the trigger gives a row ('addressed'
-- or 'ask', never 'mention'): such a post gets no row here, and threads.deliver writes the mention's row as 'mention'.
DROP TRIGGER IF EXISTS threads_primary_delivery;
CREATE TRIGGER threads_primary_delivery AFTER INSERT ON messages WHEN COALESCE(NEW.recipient_id,'')<>'' AND NOT
    COALESCE(json_extract(CASE WHEN json_valid(NEW.refs) THEN NEW.refs END,'$.carries'),'')<>'' AND NOT
    (NEW.recipient_id LIKE 'human:%' AND ((NEW.sender_id='keeper' AND COALESCE(json_extract(CASE WHEN
    json_valid(NEW.refs) THEN NEW.refs END,'$.task'),'')<>'') OR ((NEW.sender_id LIKE 'bot:%' AND NEW.kind IN
    ('say','notice','ask') AND EXISTS (SELECT 1 FROM tasks t WHERE t.conversation_id=NEW.conversation_id)) AND
    COALESCE('addressed','')<>'mention'))) BEGIN INSERT OR IGNORE INTO
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
