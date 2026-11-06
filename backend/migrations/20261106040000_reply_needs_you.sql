-- A reply to a person reaches them as an @mention does (threads.plan, reason `reply`), so Needs you's "messages to
-- answer" (thread_attention._addressed) and the rail's flags count it: the partial index holds those deliveries too.
DROP INDEX IF EXISTS message_deliveries_addressed;
CREATE INDEX IF NOT EXISTS message_deliveries_addressed ON message_deliveries(recipient, message_id)
  WHERE reason IN ('mention', 'ask', 'assign', 'addressed', 'reply') AND state IN ('pending', 'delivered', 'read');
