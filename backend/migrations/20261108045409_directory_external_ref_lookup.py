"""Directory external ids (track 7): a lookup by external id seeks an index.

get by external id (spec §9.1) reads entity_external_refs by external_id alone. The table's only index covers
(system, external_type, external_id) and (entity_id), so that lookup scanned the entity index. This adds the index the
lookup needs. The gate (bench/directory_scale.py) fails a lookup whose query plan scans, which is how this was found.
"""


def up(conn):
    conn.execute("CREATE INDEX IF NOT EXISTS entity_external_refs_external_id ON entity_external_refs(external_id)")
