"""When this install moved the write keys of ads, payments and CRM tools into the proxy (backend/api_tools.py
`write_by_proxy`): a grant made from now on reaches runs only through the proxy, and one made before keeps its key until
it is rotated at the vendor. The time is this migration's run, stored once in registry_metadata; running it again
changes nothing. It reads and writes only registry_metadata, so it runs in any order."""

from datetime import datetime, timezone

KEY = "write-keys-by-proxy-since"


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='registry_metadata'").fetchone():
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    conn.execute("INSERT OR IGNORE INTO registry_metadata(key, value) VALUES(?, ?)", (KEY, now))
