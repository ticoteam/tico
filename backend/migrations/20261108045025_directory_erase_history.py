"""Directory erase (track 7): a history row may be deleted once its subject is gone.

history_events (migration 0064) is append-only: its delete trigger refuses every row whose subject is not a task. Erasing a
person (spec "Deleting a person") must remove the history rows that name the person, the market_entity rows, and the
market_edge rows of the links that touched them. This replaces that one trigger: a market_entity or market_edge row can be
deleted only when its entity (or link) no longer exists. Live subjects stay append-only. erase_rows deletes the history
after it deletes the entities and links, so the rows it removes are exactly the erased ones.
"""

TRIGGER = """CREATE TRIGGER IF NOT EXISTS history_events_no_delete BEFORE DELETE ON history_events
WHEN OLD.subject_type<>'task' BEGIN
  SELECT RAISE(ABORT, 'history_events is append-only') WHERE NOT (
    (OLD.subject_type = 'market_entity' AND NOT EXISTS (SELECT 1 FROM entities WHERE id = OLD.subject_id))
    OR (OLD.subject_type = 'market_edge' AND NOT EXISTS (SELECT 1 FROM entity_links WHERE id = OLD.subject_id))
  );
END"""


def up(conn):
    conn.execute("DROP TRIGGER IF EXISTS history_events_no_delete")
    conn.execute(TRIGGER)
