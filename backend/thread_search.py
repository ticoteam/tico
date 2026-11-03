"""Full-text search over thread messages: SQLite FTS5, kept in step with `messages` by triggers.

A message is indexed while it has text, is not deleted, and is not a carrier (a keeper notice that starts a second
bot's run, `refs.carries`, which no timeline shows). An edit re-indexes it, and a tombstone or a hard delete removes it,
so search never returns text the thread no longer shows. Only the start of a long body is indexed (TEXT_MAX), which
bounds what a write pays for.

Search never decides who may read. The caller passes the visibility of the thread as SQL over a `conversations` row
(alias `cv`), of the message as SQL over a `messages` row (alias `m`), and the full decision on each candidate as
`readable` (threads.readable_sql, threads.message_sql and threads.message_readable, through threads.search). All three
are required, so nothing is searched unfiltered. A hit the viewer may not read is not counted, paged or sent, and a
hidden thread's existence does not show: a page that comes back empty has no `next_cursor`, whatever the hidden rows
hold. Candidates are read in batches until the page fills or SCAN_MAX of them have been decided.

Rows are keyed by `message_search_keys.id`, which is the FTS rowid. An update or delete finds its index row through
that key, so no write scans the index.
"""

import re

HITS_MAX = 50             # hits in one page
SCAN_BATCH = 200          # candidates read at a time
SCAN_MAX = 2000           # candidates one page decides; a full scan with hits still offers the next page
MATCH_WORDS = 16          # words a query may use; the rest are dropped
TEXT_MAX = 20_000         # characters of a body that are indexed (written into migration 0045's triggers)
EXCERPT_WORDS = 16        # words of text each hit shows
WORD = re.compile(r"\w+")


def ensure(c):
    """Whether the index is there to search: migration 0045 makes it, its key table and its triggers, and fills it from
    the messages a file already has. False on a SQLite built without FTS5 (search then finds nothing)."""
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_search'").fetchone() is not None


def match_expression(query):
    """The query as FTS5: its words, each a prefix match, ANDed. Punctuation and operators never reach MATCH."""
    words = WORD.findall(str(query or "").casefold())[:MATCH_WORDS]
    return " AND ".join(f'"{word}"*' for word in words)


def search(c, query, *, thread_sql, message_sql, readable, args=(), limit=20, before=None):
    """The newest visible hits for `query`: {"hits": [...], "next_cursor"}. `thread_sql` and `message_sql` are SQL
    booleans over `cv` and `m` (see the module docstring), `args` their parameters, in order; `readable(message)` decides
    each candidate from its row. `before` is the `next_cursor` of the page before. Each hit: message_id, thread_id,
    title, from_actor, created, excerpt."""
    match = match_expression(query)
    if not match:
        return {"hits": [], "next_cursor": None}
    limit = max(1, min(int(limit or 20), HITS_MAX))
    sql = ("SELECT m.*, k.id AS search_key, cv.subject AS search_title, "
           f"snippet(message_search, 0, '', '', '…', {EXCERPT_WORDS}) AS excerpt "
           "FROM message_search JOIN message_search_keys k ON k.id=message_search.rowid "
           "JOIN messages m ON m.id=k.message_id JOIN conversations cv ON cv.id=m.conversation_id "
           f"WHERE message_search MATCH ? AND ({thread_sql}) AND ({message_sql}) AND k.id<? "
           "ORDER BY k.id DESC LIMIT ?")
    below = int(before) if before else 1 << 62
    page, scanned, more, last_key = [], 0, False, None
    while scanned < SCAN_MAX and not more:
        rows = c.execute(sql, (match, *args, below, SCAN_BATCH)).fetchall()
        for row in rows:
            below = row["search_key"]
            scanned += 1
            message = dict(row)
            if not readable(message):
                continue
            if len(page) == limit:
                more = True
                break
            last_key = row["search_key"]
            page.append({"message_id": message["id"], "thread_id": message["conversation_id"],
                         "title": message["search_title"], "sender_id": message["sender_id"],
                         "created_at": message["created_at"], "excerpt": message["excerpt"]})
        if len(rows) < SCAN_BATCH:
            break
    else:
        more = more or scanned >= SCAN_MAX      # the budget ran out with candidates left: the page after goes on
    return {"hits": page, "next_cursor": str(last_key) if more and page else None}
