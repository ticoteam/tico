"""A page shared into a thread before shares kept the page's link (thread_api.shared_files) was stored as a new blob of
its bytes, linked to the message (message_assets), with nothing naming the page: narrowing the page left the copy open
to every reader of the thread. Each such copy becomes an identity of the page its bytes are (page_identities), so the
page checks every door already runs (files.page_ref_denials on the message, page_blob_visible on its download) take it
from a reader outside the page's audience.

Bytes alone do not say which page a copy came from, so a copy whose bytes are a version of several pages names each of
them, and its reader must be allowed every one: the safe side, never wider. A file posted into a chat that is
byte for byte a page's version is treated as that page in the same way."""


def up(conn):
    conn.execute(
        "INSERT OR IGNORE INTO page_identities(identity,file_id) "
        "SELECT DISTINCT a.blob_id,v.file_id FROM message_assets a JOIN blobs b ON b.id=a.blob_id "
        "JOIN bot_file_versions v ON v.digest=b.digest JOIN pages p ON p.file_id=v.file_id "
        "WHERE NOT EXISTS (SELECT 1 FROM bot_file_versions own WHERE own.blob_id=a.blob_id)"
    )
