-- Needs you's "messages to answer" (thread_attention._addressed) start from one person's deliveries. No index matched
-- them (message_deliveries_waiting leaves out `read`), so the plan rode on sqlite_stat1: TIDY's stale stats (messages
-- analysed at 16k rows, 121k by then) put messages first, a scan per call that grew to minutes and queued every
-- request behind it (v0.5.56, Nov 3). This index holds exactly those deliveries, and the query names its join order.
CREATE INDEX IF NOT EXISTS message_deliveries_addressed ON message_deliveries(recipient, message_id)
  WHERE reason IN ('mention', 'ask', 'assign', 'addressed') AND state IN ('pending', 'delivered', 'read');
