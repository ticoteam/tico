-- Pipeline and stage notes: what the team learned on a pipeline (stage_id NULL) or one stage, served to a bot's first
-- turn there (backend/pipeline_notes.py). A bot's note is proposed until the pipeline's manager or a person accepts it.
CREATE TABLE pipeline_notes (
  id TEXT PRIMARY KEY,
  pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
  stage_id TEXT REFERENCES stages(id),
  note TEXT NOT NULL CHECK (length(note) BETWEEN 1 AND 1000),
  why TEXT NOT NULL CHECK (length(why) BETWEEN 1 AND 1000),
  since TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '' CHECK (length(evidence) <= 500),
  guard TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL CHECK (state IN ('active', 'proposed', 'declined', 'retired')),
  position INTEGER NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX pipeline_notes_by_pipeline ON pipeline_notes (pipeline_id, state, stage_id, position);
-- One line per add, edit, retire, proposal and decision: what changed and why, so a note's tightening and loosening
-- stay readable under it.
CREATE TABLE pipeline_note_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id TEXT NOT NULL REFERENCES pipeline_notes(id),
  by_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('added', 'edited', 'retired', 'proposed', 'accepted', 'declined')),
  line TEXT NOT NULL CHECK (length(line) BETWEEN 1 AND 1000),
  before TEXT CHECK (before IS NULL OR json_valid(before)),
  created_at TEXT NOT NULL
);
CREATE INDEX pipeline_note_changes_by_note ON pipeline_note_changes (note_id, id);
