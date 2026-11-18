"""The rail's slow statements, rewritten to start from the rows they can return, answer as the SQL they replace did.

The old SQL stays here as the oracle: readable_sql built whole for each statement with a task room's participants read
first, the rail's two title lists over every open root with the finished-task check first, and Catch up's walk with
that check first and every finished task's root tested row by row.
Each reader's rail (every page), Catch up and readable set are compared whole. Fictional people only: ana (owner), ben
(bot admin) and cara."""

import itertools
import re

from backend import thread_rail, threads
from backend.store import H
from backend.tests.helpers.api import get, headers  # noqa: F401  (the fixture and its helper)
from backend.tests.helpers.thread_rail_batches import (  # noqa: F401  (the runner fixture)
    EARLIER,
    runner,
    seed_finished_task_replies,
)

READERS = {"human:ana": "ana-test", "human:ben": "ben-test", "human:cara": "cara-test"}
_NEW_LEGACY = "(CASE WHEN r.kind='task' AND r.task_id IS NOT NULL THEN 1 ELSE CASE COALESCE(r.scope,'direct')"
_NEW_LEGACY_END = " END END) ELSE CASE r.audience"


def old_readable_sql(sql):
    """readable_sql as it was: a legacy root's scope (and participants) read first, then whether it is a task room."""
    if _NEW_LEGACY not in sql:
        return sql
    start = sql.index(_NEW_LEGACY)
    end = sql.index(_NEW_LEGACY_END, start)
    body = sql[start + len(_NEW_LEGACY) : end]
    return (
        f"{sql[:start]}(CASE COALESCE(r.scope,'direct'){body} END OR (r.kind='task' AND r.task_id IS NOT NULL))"
        f"{sql[end + len(' END END)') :]}"
    )


def old_rail_lists(c, v, marks):
    """The rail's two lists as they were: every open root, the finished-task check first."""
    kind, readable = thread_rail._RAIL_KIND, threads.readable_sql(c, v, "r")
    base = [thread_rail.root_open(c, v, f"{kind} AND {readable}"), kind, readable]
    pinned = thread_rail.PINNED_SQL
    return (
        "conversations r",
        " AND ".join([*base, pinned]),
        " AND ".join([*base, thread_rail._in(marks), f"NOT {pinned}"]),
    )


def old_walk_where(c, v, rest, args):
    """Catch up's walk as it was: the finished-task check first, readable built whole, the reader's membership before
    something new, and no pass over finished tasks' roots on lists."""
    old = [threads.readable_sql(c, v, "r"), rest[2], rest[0], rest[3]]
    return [thread_rail.root_open(c, v, " AND ".join(old), args), *old], "1", {}


def _root(c, task_id):
    return c.execute("SELECT id FROM conversations WHERE task_id=? AND parent_id IS NULL", (task_id,)).fetchone()[0]


def _finish(c, task_id):
    H.task_done(c, "human:ana", task_id, "Done.", seen_version=H.task(c, task_id)["version"])
    c.execute("UPDATE history_events SET created_at=? WHERE subject_id=?", (EARLIER, task_id))


def _seed(api):
    """Threads of every kind the rail and Catch up list or leave out, read by each reader differently."""
    seed_finished_task_replies(api, 3, "rb")  # ben's finished tasks, each brought back by bot:ops's reply
    seed_finished_task_replies(api, 2, "rc", owner="human:cara")
    with api.app.state.store.transaction() as c:
        made = []
        for i, (who, audience) in enumerate(
            [("human:ana", "company"), ("human:ben", "company"), ("human:ana", "members"), ("human:cara", "members")]
            + [("human:ana", "company"), ("human:cara", "members"), ("human:ben", "members")]
        ):
            out = threads.post(
                c, who, None, f"Hello {i}", start={"title": f"Topic {chr(90 - i)}", "audience": audience}
            )
            made.append(out.get("thread_id") or out.get("conversation_id"))
            if i % 3 == 2:  # an untitled thread (written before titles were required): the lists put it last
                c.execute("UPDATE conversations SET subject='' WHERE id=?", (made[-1],))
        threads.add_member(c, "human:ana", made[2], "human:ben")
        threads.add_member(c, "human:cara", made[3], "human:ana")
        threads.post(c, "human:cara", made[2], "Seen")
        threads.post(c, "human:ana", None, "Group", start={"dm": ["human:ben", "human:cara"], "title": "Trio"})
        threads.post(c, "human:ben", None, "Just us", start={"dm": ["human:cara"], "title": ""})
        thread_rail.pin(c, "human:ben", made[0])
        thread_rail.pin(c, "human:cara", made[3])
        c.execute("UPDATE conversations SET pinned_for_everyone_at=? WHERE id=?", (H.now(), made[1]))
        c.execute("UPDATE conversations SET archived_at=? WHERE id=?", (H.now(), made[4]))
        c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), made[5]))
        # Finished tasks: one quiet, one a person posted in since, and two whose threads hold a stored company
        # audience (so the rail's own lists may hold them), one posted in since and one quiet.
        quiet = H.task_create(c, "human:ana", "Quiet", "", "human:ben", lint=False)["id"]
        talked = H.task_create(c, "human:ana", "Talked", "", "human:ben", lint=False)["id"]
        listed = H.task_create(c, "human:ana", "Listed", "", "human:cara", lint=False)["id"]
        unlisted = H.task_create(c, "human:ana", "Unlisted", "", "human:cara", lint=False)["id"]
        for task in (listed, unlisted):
            c.execute("UPDATE conversations SET audience='company' WHERE id=?", (_root(c, task),))
        for task in (quiet, talked, listed, unlisted):
            _finish(c, task)
        threads.post(c, "human:cara", _root(c, talked), "One more thing")
        threads.post(c, "human:ana", _root(c, listed), "Reopened")
        H.task_create(c, "human:ana", "Still open", "", "human:cara", lint=False)
        c.execute("UPDATE messages SET restricted=1 WHERE sender_id LIKE 'bot:%'")


def _pages(api, token, path):
    """Every page of a rail (2 a page, so its cursors are walked) or Catch up, without the snapshot's seq."""
    out, cursor = [], None
    while True:
        query = f"{path}?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = get(api, query, token)
        page.pop("seq", None)
        out.append(page)
        cursor = page.get("next_cursor")
        if not cursor or len(out) > 30:
            return out


def test_the_open_thread_index_holds_exactly_the_rows_its_terms_name(api):
    """The migration's partial index and thread_rail.OPEN_THREADS name the same rows: a query naming the index (INDEXED
    BY) must AND in its WHERE, and SQLite matches the two term by term."""
    with api.app.state.store.read_transaction() as c:
        [sql] = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='conversations_open_thread_titles'"
        ).fetchone()
    where = sql[sql.index(" WHERE ") + len(" WHERE ") :]
    assert " ".join(where.split()) == " ".join(thread_rail.OPEN_THREADS.replace("r.", "").split())
    assert thread_rail._TITLE_ORDER[0].replace("r.", "") in sql and thread_rail._TITLE_ORDER[1].replace("r.", "") in sql


def test_readable_sql_reads_task_rooms_as_before(api):
    """readable_sql tests a legacy task room before its scope and participants, built whole or row by row (per_row),
    and every reader reads the same rows as with the old SQL: legacy rooms of each scope, with and without a task (one
    private to ana), the reader among the participants or not, unreadable participants, and sub-threads under each."""
    _seed(api)
    with api.app.state.store.transaction() as c:
        public = H.task_create(c, "human:ana", "Public", "", "human:ben", lint=False)["id"]
        private = H.task_create(c, "human:ana", "Private", "", "human:ana", private=True, lint=False)["id"]
        shapes = itertools.product(
            ("task", "chat", None),
            (None, public, private),
            ("direct", "personal", "shared"),
            ('["human:ben","bot:ops"]', '["human:cara"]', "not json", None),
        )
        for n, (kind, task, scope, people) in enumerate(shapes):
            c.execute(
                "INSERT INTO conversations(id,kind,task_id,scope,participants,room_key,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (f"legacy-{n}", kind, task, scope, people, "ops" if scope == "shared" else None, H.now()),
            )
            c.execute(
                "INSERT INTO conversations(id,kind,parent_id,root_id,created_at) VALUES(?,?,?,?,?)",
                (f"legacy-{n}-sub", "chat", f"legacy-{n}", f"legacy-{n}", H.now()),
            )
    with api.app.state.store.read_transaction() as c:
        total = c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        for actor in [*READERS, "bot:ops"]:
            v = threads.actor_viewer(c, actor)
            for alias in ("x", ""):
                new = threads.readable_sql(c, v, alias)
                old = old_readable_sql(new)
                assert old != new or actor == "bot:ops", actor
                table = f"conversations {alias}" if alias else "conversations"
                got = {r[0] for r in c.execute(f"SELECT id FROM {table} WHERE {new}")}
                want = {r[0] for r in c.execute(f"SELECT id FROM {table} WHERE {old}")}
                assert got == want, (actor, alias, got ^ want)
                assert 0 < len(got) < total, (actor, len(got))
                if alias:
                    rows = threads.readable_sql(c, v, alias, per_row=True)
                    assert ("rd.id=COALESCE" in rows) == (actor != "bot:ops"), rows[:200]
                    each = {r[0] for r in c.execute(f"SELECT id FROM {table} WHERE {rows}")}
                    assert each == want, (actor, "per_row", each ^ want)


def test_the_rail_and_catch_up_answer_as_the_old_sql(api, monkeypatch):
    """Every reader's rail (each page, its cursors, Catch up's count and ids, its Unread rows, its DMs) and Catch up
    are the same from the index and with the cheap terms first as from the old SQL: readable_sql's old legacy order,
    every open root with the finished-task check first, and the walk's old term order."""
    _seed(api)
    paths = ("threads/rail", "threads/catch-up")
    new = {(p, t): _pages(api, t, p) for p in paths for t in READERS.values()}
    real = threads.readable_sql
    monkeypatch.setattr(
        threads, "readable_sql", lambda *a, **kw: old_readable_sql(real(*a, **{**kw, "per_row": False}))
    )
    monkeypatch.setattr(thread_rail, "_rail_lists", old_rail_lists)
    monkeypatch.setattr(thread_rail, "_walk_where", old_walk_where)
    monkeypatch.setattr(thread_rail, "OPEN_ROOTS_FROM", "conversations r")
    for (path, token), pages in new.items():
        assert _pages(api, token, path) == pages, f"{path} changed for {token}"
    rail = {t: new[("threads/rail", t)] for t in READERS.values()}
    assert all(len(pages) > 1 for pages in rail.values()), {t: len(p) for t, p in rail.items()}
    titles = {t: [r["title"] for p in pages for r in p["pinned"] + p["threads"]] for t, pages in rail.items()}
    assert "Listed" in titles["cara-test"] and "Unlisted" not in titles["cara-test"], titles
    assert all(pages[0]["catch_up"] for pages in rail.values()), {t: p[0]["catch_up"] for t, p in rail.items()}


def test_the_rails_lists_read_the_open_thread_index_in_title_order(api):
    """Both title lists read the open-thread index in its order (no sort of every open root), and test the
    finished-task check after the reader's own terms."""
    _seed(api)
    with api.app.state.store.read_transaction() as c:
        v = threads.actor_viewer(c, "human:ben")
        extra, marks = thread_rail._names(v, c)
        source, pinned, listed = thread_rail._rail_lists(c, v, marks)
        assert source == thread_rail.OPEN_THREADS_FROM
        for where in (pinned, listed):
            assert where.rindex("NOT EXISTS (SELECT 1 FROM tasks t") > where.index(thread_rail.PINNED_SQL)
            sql = (
                f"SELECT * FROM (SELECT r.*, {thread_rail._TITLE_ORDER[0]} AS t_blank, {thread_rail._TITLE_ORDER[1]} "
                f"AS t_key FROM {source} WHERE {where}) ORDER BY t_blank, t_key, id LIMIT 20"
            )
            plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql, {"me": v.actor, **extra})]
            assert any(re.search(r"\bSCAN r USING INDEX conversations_open_thread_titles\b", p) for p in plan), plan
            assert not any("TEMP B-TREE FOR ORDER BY" in p for p in plan), plan
            # Each row's root and task by id: no statement-long list of every readable root or task.
            assert not [
                p for p in plan if re.match(r"SCAN (r|rd|rt|t|tasks|conversations)\b(?! USING INDEX conv)", p)
            ], plan
