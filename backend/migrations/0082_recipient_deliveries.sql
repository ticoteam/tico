-- Subtree unread starts at one reader's deliveries, including read, failed and moot ones: the thread's read mark
-- decides what is unread, not a delivery's state. The waiting/addressed partial indexes leave some of these out,
-- so the bot half otherwise scans the company's delivery history. Cover its message lookup and counting rule too.
CREATE INDEX IF NOT EXISTS message_deliveries_recipient ON message_deliveries(recipient, message_id, reason);
