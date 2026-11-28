-- Files use counts (backend/file_use.py): one row per UTC day, person or bot, action and actor, its uses added up.
-- Read only through the files_use_weekly view (backend/sql.py GRANTED), which never shows the actor.
CREATE TABLE file_use_daily (
    day TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('person', 'bot')),
    action TEXT NOT NULL CHECK (action IN ('list', 'open', 'search', 'review')),
    actor TEXT NOT NULL,
    uses INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, kind, action, actor)
) WITHOUT ROWID;
