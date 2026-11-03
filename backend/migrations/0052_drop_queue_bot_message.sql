-- A message's job is queued in Python by every writer, in the same transaction as the message (backend/enqueue.py),
-- so the rule is code a test can call. The trigger that made the job on INSERT goes.
DROP TRIGGER IF EXISTS queue_bot_message;
