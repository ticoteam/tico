"""An existing cloud_machines table gains Grow storage automatically and the resize drain (backend/cloud_machines.py):
`grow_storage` (NULL is the default: on for the shared machine, the company's for an own one), `grow_note` (the last
word on growing, shown with the machine), `grow_refused_size` (a size HQ refused, never grown to again until a person
changes the size or the switch) and `drain_started_at` (when a running machine began waiting for its turns to restart
at another size; NULL for none).

cloud_machines is created by the module schema code after the migration files run (hubdb.migrate), and that CREATE has
the columns, so a new database has no table here yet and the step is skipped. Running it again changes nothing.
"""

COLUMNS = (("grow_storage", "INTEGER"), ("grow_note", "TEXT NOT NULL DEFAULT ''"), ("grow_refused_size", "TEXT"),
           ("drain_started_at", "REAL"))


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_machines'").fetchone():
        return
    have = {row[1] for row in conn.execute("PRAGMA table_info(cloud_machines)")}
    for name, kind in COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE cloud_machines ADD COLUMN {name} {kind}")
