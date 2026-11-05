"""STUB for track B only: track A's backend/api_tools.py replaces this file on merge (keep A's version).

`subject_level` and `key_for` as the build contract (reports/2026-11-05-tools-redesign-build.md) states them.
"""
RANK = {'read': 1, 'read_write': 2}


def subject_level(c, tool_id, subject):
    """The highest grant level `subject` holds over the tool's keys: 'read', 'read_write' or None."""
    best = None
    for row in c.execute('SELECT g.access_level FROM credential_grants g JOIN credentials v ON v.id=g.credential_id '
                         'WHERE v.tool_id=? AND g.subject=? AND g.revoked_at IS NULL', (tool_id, subject)):
        if RANK.get(row[0], 0) > RANK.get(best, 0):
            best = row[0]
    return best


def key_for(c, tool_id, subject):
    """The stored key a call at `subject`'s level uses: a Read caller's read key, else the read_write key."""
    level = subject_level(c, tool_id, subject)
    if not level:
        return None
    keys = c.execute('SELECT * FROM credentials WHERE tool_id=? AND ciphertext IS NOT NULL ORDER BY created_at,id',
                     (tool_id,)).fetchall()
    if level == 'read':
        for row in keys:
            if row['access_level'] == 'read':
                return row
    return next((row for row in keys if row['access_level'] == 'read_write'), None)
