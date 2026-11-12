"""Find the pages a response names by their identities, not by reading every page's history (files.page_identities).

A response names a page by its id, a version's blob, poster, thumb, digest or source digest, or an alias a purge kept.
blob_id already has bot_file_versions_blob_media and page_identities its primary key; these index the other four, so a
read costs the same however many page versions the company keeps. Poster, thumb and source digest are often empty, so
those indexes are partial. hubdb's own steps make bot_file_versions, so a new database has it here too.
"""

INDEXES = (
    ("digest", "CREATE INDEX IF NOT EXISTS bot_file_versions_digest ON bot_file_versions(digest)"),
    (
        "source_digest",
        "CREATE INDEX IF NOT EXISTS bot_file_versions_source_digest ON bot_file_versions(source_digest) "
        "WHERE source_digest IS NOT NULL",
    ),
    (
        "poster_blob_id",
        "CREATE INDEX IF NOT EXISTS bot_file_versions_poster ON bot_file_versions(poster_blob_id) "
        "WHERE poster_blob_id IS NOT NULL",
    ),
    (
        "thumb_blob_id",
        "CREATE INDEX IF NOT EXISTS bot_file_versions_thumb ON bot_file_versions(thumb_blob_id) "
        "WHERE thumb_blob_id IS NOT NULL",
    ),
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(bot_file_versions)")}
    for column, statement in INDEXES:
        if column in columns:
            conn.execute(statement)
