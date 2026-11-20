-- The open threads with something unread for someone (last_unread_seq past 0), with their roots: Catch up and the rail
-- read the roots that may hold something new for a reader from here (threads.maybe_unread_roots), a few hundred
-- threads, instead of testing every root of the company (most of them finished tasks' rooms) for it.
CREATE INDEX IF NOT EXISTS conversations_unread_threads ON conversations(last_unread_seq, root_id)
WHERE archived_at IS NULL AND closed_at IS NULL AND last_unread_seq>0;
