"""Thread message search: the FTS5 index follows edits and deletes, carriers stay out, and a hidden thread or message is
never selected, so nothing about it shows."""

import sqlite3

from backend import thread_search as search

# The visibility a viewer gets in these tests: company threads only, and never a message from one actor.
THREADS = "cv.audience='company'"
MESSAGES = "m.from_actor<>'human:eve'"


def database():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE conversations(id TEXT PRIMARY KEY, subject TEXT, audience TEXT);
        CREATE TABLE messages(id TEXT PRIMARY KEY, conversation_id TEXT, from_actor TEXT, body TEXT,
          refs_json TEXT, created TEXT, deleted_at TEXT);
        INSERT INTO conversations VALUES ('t-open', 'Budget', 'company'), ('t-private', 'Launch', 'members');
    """)
    assert search.ensure(c)
    return c


def post(c, mid, thread, body, actor="human:ana", refs=None):
    c.execute("INSERT INTO messages(id, conversation_id, from_actor, body, refs_json, created) "
              "VALUES (?,?,?,?,?,?)", (mid, thread, actor, body, refs, f"2026-11-01T10:00:0{len(mid) % 10}Z"))


def find(c, query, **kw):
    return search.search(c, query, thread_sql=THREADS, message_sql=MESSAGES, readable=kw.pop("readable", lambda m: True),
                         **kw)


def ids(result):
    return [hit["message_id"] for hit in result["hits"]]


def test_a_new_message_is_found_with_its_thread():
    c = database()
    post(c, "m1", "t-open", "Quarterly budget review moved to Friday")
    hits = find(c, "budget review")["hits"]
    assert [(h["message_id"], h["thread_id"], h["title"]) for h in hits] == [("m1", "t-open", "Budget")]
    assert "budget" in hits[0]["excerpt"].casefold()


def test_an_edit_replaces_what_is_found():
    c = database()
    post(c, "m1", "t-open", "Quarterly budget review")
    c.execute("UPDATE messages SET body='Pricing call with Acme' WHERE id='m1'")
    assert ids(find(c, "budget")) == []
    assert ids(find(c, "pricing acme")) == ["m1"]


def test_a_tombstone_and_a_hard_delete_leave_no_hits():
    c = database()
    post(c, "m1", "t-open", "Budget notes for Acme")
    post(c, "m2", "t-open", "Budget draft two")
    c.execute("UPDATE messages SET body='', deleted_at='2026-11-01T11:00:00Z' WHERE id='m1'")
    c.execute("DELETE FROM messages WHERE id='m2'")
    assert ids(find(c, "budget")) == []
    assert c.execute("SELECT COUNT(*) FROM message_search_keys WHERE message_id='m2'").fetchone()[0] == 0


def test_a_carrier_is_never_indexed():
    c = database()
    post(c, "carrier", "t-open", "budget carrier text", refs='{"carries": "m1"}')
    assert ids(find(c, "carrier")) == []
    assert ids(find(c, "budget")) == []


def test_a_hidden_thread_or_message_is_never_selected():
    c = database()
    post(c, "private", "t-private", "Launch secret for the board")
    post(c, "eve", "t-open", "Launch plans from Eve", actor="human:eve")
    post(c, "open", "t-open", "Launch timing agreed")
    # The viewer sees one hit, and the hidden ones give no sign of themselves: no cursor, no count.
    page = find(c, "launch", limit=1)
    assert ids(page) == ["open"] and page["next_cursor"] is None
    assert find(c, "secret")["hits"] == [] and find(c, "secret")["next_cursor"] is None


def test_pages_cover_each_visible_hit_once_newest_first():
    c = database()
    for n in range(5):
        post(c, f"m{n}", "t-open", f"status update {n} for the team")
    post(c, "hidden", "t-private", "status update hidden")
    first = find(c, "status update", limit=2)
    second = find(c, "status update", limit=2, before=first["next_cursor"])
    third = find(c, "status update", limit=2, before=second["next_cursor"])
    assert ids(first) == ["m4", "m3"] and ids(second) == ["m2", "m1"] and ids(third) == ["m0"]
    assert third["next_cursor"] is None


def test_query_punctuation_never_reaches_match_as_syntax():
    c = database()
    post(c, "m1", "t-open", "Budget review")
    for query in ('NEAR(budget review)', '"budget', 'budget*', 'budget OR', ':', '', '   '):
        assert "hits" in find(c, query)
    # Parentheses and quotes are dropped, so the words still match; NEAR is just a word that no message has.
    assert ids(find(c, '(budget) "review"')) == ["m1"]
    assert ids(find(c, 'NEAR(budget review)')) == []
    assert search.match_expression('a "b" c-d') == '"a"* AND "b"* AND "c"* AND "d"*'


def test_ensure_fills_an_index_from_existing_messages_and_keeps_it():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE conversations(id TEXT PRIMARY KEY, subject TEXT, audience TEXT);
        CREATE TABLE messages(id TEXT PRIMARY KEY, conversation_id TEXT, from_actor TEXT, body TEXT,
          refs_json TEXT, created TEXT, deleted_at TEXT);
        INSERT INTO conversations VALUES ('t-open', 'Budget', 'company');
        INSERT INTO messages VALUES ('old', 't-open', 'human:ana', 'Old budget memo', NULL, '2026-10-01', NULL);
    """)
    assert search.ensure(c)
    assert ids(find(c, "memo")) == ["old"]
    before = c.execute("SELECT COUNT(*) FROM message_search").fetchone()[0]
    assert search.ensure(c)
    assert c.execute("SELECT COUNT(*) FROM message_search").fetchone()[0] == before == 1
    assert ids(find(c, "memo")) == ["old"]


def test_a_hit_the_full_check_refuses_is_not_counted_paged_or_hinted_at():
    c = database()
    for n in range(9):
        post(c, f"m{n}", "t-open", "budget")
    shown = {"m8", "m6", "m1"}
    readable = lambda m: m["id"] in shown
    first = find(c, "budget", limit=2, readable=readable)
    assert ids(first) == ["m8", "m6"] and first["next_cursor"]
    second = find(c, "budget", limit=2, readable=readable, before=first["next_cursor"])
    assert ids(second) == ["m1"] and second["next_cursor"] is None
    # An exact fit has no further page, and a viewer who reads nothing gets nothing that says there was something.
    exact = find(c, "budget", limit=3, readable=readable)
    assert len(ids(exact)) == 3 and exact["next_cursor"] is None
    assert find(c, "budget", readable=lambda m: False) == {"hits": [], "next_cursor": None}


def test_a_page_past_the_scan_budget_goes_on_from_its_last_hit_and_an_empty_one_ends(monkeypatch):
    monkeypatch.setattr(search, "SCAN_BATCH", 2)
    monkeypatch.setattr(search, "SCAN_MAX", 4)
    c = database()
    for n in range(8):
        post(c, f"m{n}", "t-open", "budget")
    readable = lambda m: m["id"] in ("m7", "m1")
    first = find(c, "budget", readable=readable)
    assert ids(first) == ["m7"] and first["next_cursor"]
    assert find(c, "budget", readable=lambda m: m["id"] == "m0") == {"hits": [], "next_cursor": None}
