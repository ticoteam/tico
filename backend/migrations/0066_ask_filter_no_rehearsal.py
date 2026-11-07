"""The ask filter has two modes, Off and On: Rehearsal is gone (backend/ask_ingest.py). A stored 'rehearsal' (a person's
own setting in ask_ingest_settings, or the team default's row, person '*') becomes 'on', the mode the filter ships as.
Nothing else changes: the other modes stay, and so does every item row, since those rows are the record of what was
judged then.

On a new file the table does not exist yet: backend/ask_ingest.py SCHEMA makes it, with no 'rehearsal' in it.
"""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ask_ingest_settings'").fetchone():
        return
    conn.execute("UPDATE ask_ingest_settings SET mode='on' WHERE mode='rehearsal'")
