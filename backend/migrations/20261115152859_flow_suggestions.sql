-- Suggested rules at the end of Flow (backend/flow_suggestions.py): sentences drawn from people's send-back notes on a
-- page check (a stage requirement Jev judges), shared by everyone who may change the pipeline. `added` ones are asked
-- of the next page Jev judges for that check, each as one more yes/no part (backend/task_pages.py `judged_against`).
-- `notes_used` of `notes_of` is how many of the send backs it was drawn from rest on it. `rewriting_at` is set while a
-- person's feedback is being worked into it. `acted_by` / `acted_at`: the last person to add, undo or dismiss it.
CREATE TABLE IF NOT EXISTS flow_suggestions (
  id TEXT PRIMARY KEY,
  requirement_id TEXT NOT NULL,
  stage_id TEXT NOT NULL,
  pipeline_id TEXT NOT NULL,
  text TEXT NOT NULL,
  notes_used INTEGER NOT NULL DEFAULT 0,
  notes_of INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'added', 'dismissed')),
  rewriting_at TEXT,
  acted_by TEXT,
  acted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS flow_suggestions_requirement ON flow_suggestions(requirement_id, state);
-- One row per check that has been worked out: `started_at` is the claim of whoever is working it out now (NULL when
-- none), `through_at` the newest send back the last one read, so only send backs after it count toward the next;
-- `error` the code of a failed one at `written_at` (NULL when it answered).
CREATE TABLE IF NOT EXISTS flow_suggestion_runs (
  requirement_id TEXT PRIMARY KEY,
  started_at TEXT,
  through_at TEXT,
  written_at TEXT,
  error TEXT
);
