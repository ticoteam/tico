-- Thread ranking (backend/thread_rank.py): each root thread's 1-10 level for the Important view of the Threads list.
-- `score` is NULL while no call has answered; `tried_at` spaces the calls, answered or not.
CREATE TABLE IF NOT EXISTS thread_rankings(
 thread_id TEXT PRIMARY KEY, score INTEGER, ranked_seq INTEGER NOT NULL DEFAULT 0, tried_at TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
