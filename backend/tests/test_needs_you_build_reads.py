"""One Needs-you build reads each kind of row once for the whole list, not once per item: a page to OK, a bot's ❓ and
a bot that cannot run each add no statement (live, Nov 18: 834 statements a build for the company's person, most of
them one page's reads at a time). The list a snapshot's batched reads give is the one the row-by-row reads give, byte
for byte; and the route serves a list's next pages from the build that gave its first (backend/needs_cache.py).

Synthetic rows only: the fictional company (backend/tests/helpers/engine.py).
"""

import json

import pytest

from backend import item_metadata as IM
from backend import needs_cache, read_cache
from backend import question_marks as Q
from backend import task_engine as E
from backend import task_pages as P
from backend import views as V
from backend.blobs import Blobs
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import Identity, headers
from backend.tests.helpers.page_requirement import _page, _say

ANA, BEN = X.ANA, "human:ben"
STAGES = (
    {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Review", "owner": "assignee", "requirements": [{"kind": "ok", "person": ANA, "line": "OK the build"}]},
)
SPEC = (
    {
        "name": "Spec",
        "owner": "assignee",
        "requirements": [{"kind": "page", "line": "Spec page", "person": ANA, "who_oks": "none"}],
    },
)
# A review page for the PR's newest commit, for Ana to OK. Its PR's repository is named in mixed case, as GitHub gives
# it: `item_key` is NOCASE, so its link finds its metadata whatever the case of either.
REVIEW = (
    {
        "name": "Review",
        "owner": "assignee",
        "requirements": [
            {
                "kind": "page",
                "line": "Review page",
                "person": ANA,
                "who_oks": "none",
                "settings": {"at_head": True, "rubric": "Reviewed"},
            }
        ],
    },
)
REVIEW_PRS = (("Acme/Widgets#7", "Acme/Widgets#7"), ("ACME/widgets#8", "acme/Widgets#8"))  # (link's key, saved key)
WHO = {ANA: Identity(ANA, "owner", "ana@acme.example"), BEN: Identity(BEN, "human", "ben@acme.example")}


BOTS = [f"worker{n:02d}" for n in range(20)]


def company(api, pages, questions, idle):
    """OKs and work for twenty bots, then `more` of the rest."""
    store = api.app.state.store
    store.blobs = store.blobs or Blobs(store.settings)
    with store.transaction() as c:
        for slug in [*BOTS, "manager", "writer"]:
            X.bot(c, slug)
        X.bot(c, "idle", computer=False)  # a cloud bot whose machine is being made
        c.execute("INSERT INTO cloud_machines (bot, mode, placement) VALUES ('idle', 'cloud', 'own')")
        X.bot(c, "stuck", computer=False)
        c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot='worker01'")
        X.pipeline(c, "eng", "Engineering", STAGES, manager="bot:manager")
        X.pipeline(c, "product", "Product", SPEC, manager="bot:manager")
        for k in range(40):
            row = X.new_task(c, ANA, f"Company task {k}", "eng", assignee=f"bot:{BOTS[k % len(BOTS)]}")
            if k % 4 == 0:
                person = BEN if k % 8 == 0 else ANA
                shows = X.shown(c, bot=row["owner_id"])
                E.add_requirement(
                    c, row["id"], row["owner_id"], "ok", person, f"OK {k}", seen_version=row["version"], shows=shows
                )
        X.new_task(c, ANA, "Waits for its machine", "eng", assignee="bot:idle")
        X.new_task(c, ANA, "Held up by a bot on no computer", "eng", assignee="bot:stuck")
        X.pipeline(c, "review", "Review", REVIEW, manager="bot:manager")
        for linked, saved in REVIEW_PRS:
            row = X.new_task(c, ANA, f"Review {linked}", "review", assignee="bot:writer")
            repo, number = linked.split("#")
            c.execute(
                "INSERT INTO task_links (id, task_id, kind, url, repo, number, source, item_type, item_key, created_at) "
                "VALUES (?,?,'pr',?,?,?,'github','pr',?,?)",
                (H.new_id(), row["id"], f"https://github.com/{repo}/pull/{number}", repo, int(number), linked, H.now()),
            )
            IM.save(c, "github", "pr", saved, {"state": "open", "head_sha": "aaa", "merged_sha": None}, 1)
            fid, _ = _page(c, store, f"<p>Reviewed {linked} at aaa</p>")
            made = P.attach(c, row["id"], "Review page", "page:" + fid, "bot:writer", seen_version=row["version"])
            _say(c, made, "met")
    more(store, pages, questions, idle)
    return store


def more(store, pages, questions, idle, start=0):
    """`pages` more specs for Ana to OK (the first one ever sent back once and attached again), `questions` more ❓ to
    Ana (half on tasks, half in direct chats) and `idle` more bots on no computer, numbered on from `start`."""
    with store.transaction() as c:
        for n in range(start, start + idle):
            X.bot(c, f"idle{n:02d}", computer=False)
        for k in range(start, start + pages):
            row = X.new_task(c, ANA, f"Spec {k}", "product", assignee="bot:writer")
            fid, _ = _page(c, store, f"<h1>Spec {k}</h1>")
            made = P.attach(c, row["id"], "Spec page", "page:" + fid, "bot:writer", seen_version=row["version"])
            _say(c, made, "met")
            if k == 0:  # sent back once, then a new version attached: the person who sent it back is asked again
                X.answer(c, E.task(c, row["id"]), made["requirement_id"], "no", ANA, "Say what it costs")
                _page(c, store, "<h1>Spec 0</h1><p>It costs 10 dollars.</p>", fid=fid)
                again = P.attach(
                    c, row["id"], "Spec page", "page:" + fid, "bot:writer", seen_version=E.task(c, row["id"])["version"]
                )
                _say(c, again, "met")
        tasks = [r[0] for r in c.execute("SELECT id FROM tasks WHERE title LIKE 'Company task %' ORDER BY rowid")]
        half = questions // 2
        for k, task_id in enumerate(tasks[start : start + half], start):
            owner = E.task(c, task_id)["owner_id"]
            said = H.task_comment(c, owner, task_id, f"Should note {k} go out on Friday?", wake=False)
            Q.mark(c, owner, said["id"], ANA)
        for k in range(start, start + questions - half):
            Q.ask_in_chat(c, f"bot:{BOTS[k]}", ANA, f"Which tone should note {k} take?")


def build(api, person, how, **kw):
    """(the person's Needs you, the statements it took): `how` "snapshot" in one read transaction, as the route
    reads it; "rows" with no snapshot cache (read_cache.cache gives None, as outside a snapshot), where nothing is
    primed or kept between reads and each item reads its own rows."""
    store, seen = api.app.state.store, []
    with pytest.MonkeyPatch.context() as patched:
        if how == "rows":
            patched.setattr(read_cache, "cache", lambda c: None)
        with store.read_transaction() as c:
            c.set_trace_callback(seen.append)
            items = V.needs_items(c, api.app.state.auth, WHO[person], **kw)
            c.set_trace_callback(None)
    if how == "rows":  # the reference read each PR's metadata on its own, never in a batch
        assert not any("FROM item_metadata" in sql and "json_each" in sql for sql in seen)
    return items, (len(seen) if how == "snapshot" else seen)


@pytest.mark.parametrize("count_only", [False, True])
def test_the_batched_build_is_the_row_by_row_build_byte_for_byte(api, count_only):
    company(api, pages=6, questions=6, idle=4)
    for person in (ANA, BEN):
        batched, _ = build(api, person, "snapshot", count_only=count_only)
        rows, read = build(api, person, "rows", count_only=count_only)
        assert json.dumps(batched, sort_keys=True, default=str) == json.dumps(rows, sort_keys=True, default=str)
        if person == ANA and not count_only:
            assert any("FROM item_metadata WHERE source=" in sql for sql in read)
    if not count_only:
        mine, _ = build(api, ANA, "snapshot")
        kinds = {item["kind"] for item in mine}
        assert {"ask", "question", "paused"} <= kinds, kinds
        assert len([item for item in mine if item["kind"] == "ask" and (item.get("ask") or {}).get("page")]) == 6 + 2
        # The review pages are for their PRs' newest commits: each is Ana's to OK, not stale.
        reviews = [item for item in mine if str((item.get("task") or {}).get("title")).startswith("Review ")]
        assert len(reviews) == len(REVIEW_PRS), [item["line"] for item in mine]


def test_more_pages_questions_and_idle_bots_read_no_more_statements(api):
    """A build's statements do not grow with its items: six times the pages, ❓s and bots that cannot run read the same
    statements, give or take a chunk."""
    store = company(api, pages=3, questions=2, idle=2)
    few_items, few = build(api, ANA, "snapshot")
    more(store, pages=15, questions=12, idle=12, start=3)
    many_items, many = build(api, ANA, "snapshot")
    assert len(many_items) >= len(few_items) + 20
    assert many <= few + 3, (few, many)
    assert many <= 150, many


def test_the_next_pages_come_from_the_build_that_gave_the_first(api, monkeypatch):
    """A walk through the pages builds the list once: the cursor names the version its page came from, and a page of
    that version is cut from the same build while it is held. Once a build gives the person a newer version (the live
    `needs` event's mend), or the held one is TTL old, a cursor from before starts again from the top."""
    company(api, pages=3, questions=4, idle=2)
    builds = []
    real = V.needs_items

    def counted(*args, **kw):
        builds.append(kw.get("count_only", False))
        return real(*args, **kw)

    monkeypatch.setattr(V, "needs_items", counted)
    whole = api.get("/api/v2/needs-you", headers=headers("ana-test")).json()
    builds.clear()
    walked, url = [], "/api/v2/needs-you?limit=5"
    while url:
        page = api.get(url, headers=headers("ana-test")).json()
        assert page["version"] == whole["version"] and page["count"] == whole["count"]
        assert page["bots"] and page["by_kind"] == whole["by_kind"] and "snoozed_until" in page
        walked += page["items"]
        url = f"/api/v2/needs-you?limit=5&cursor={page['next']}" if page["next"] else None
    assert walked == whole["items"] and len(walked) > 10
    assert builds == [False]  # the first page built it; every next page was cut from that build
    # Another person never gets Ana's build, not even with her cursor.
    builds.clear()
    ben = api.get("/api/v2/needs-you?limit=5", headers=headers("ben-test")).json()
    theirs = api.get(f"/api/v2/needs-you?limit=5&cursor={whole['version']}.5", headers=headers("ben-test")).json()
    assert builds == [False, False] and theirs["restart"] is True
    assert all(item["id"] not in {i["id"] for i in walked} for item in ben["items"] + theirs["items"])
    # A write: the cursor still names the list its first page came from, and is cut from it...
    first = api.get("/api/v2/needs-you?limit=5", headers=headers("ana-test")).json()
    with api.app.state.store.transaction() as c:
        task_id = c.execute("SELECT id FROM tasks WHERE title='Company task 30'").fetchone()[0]
        said = H.task_comment(c, E.task(c, task_id)["owner_id"], task_id, "Ship it on Monday?", wake=False)
        Q.mark(c, E.task(c, task_id)["owner_id"], said["id"], ANA)
    builds.clear()
    pinned = api.get(f"/api/v2/needs-you?limit=5&cursor={first['next']}", headers=headers("ana-test")).json()
    assert pinned["version"] == first["version"] and "restart" not in pinned and builds == []
    # ...until the live event's mend builds the new version: then it starts again from a new build.
    mend = api.get(f"/api/v2/needs-you?ids={walked[0]['id']}", headers=headers("ana-test")).json()
    assert mend["version"] != first["version"] and mend["count"] == first["count"] + 1
    builds.clear()
    later = api.get(f"/api/v2/needs-you?limit=5&cursor={first['next']}", headers=headers("ana-test")).json()
    assert later["restart"] is True and later["version"] == mend["version"] and builds == [False]
    # A held list serves for TTL seconds from when its snapshot began, no longer.
    now = [needs_cache.clock() + 1.0]
    monkeypatch.setattr(needs_cache, "clock", lambda: now[0])
    fresh = api.get("/api/v2/needs-you?limit=5", headers=headers("ana-test")).json()
    now[0] += needs_cache.TTL + 1
    builds.clear()
    aged = api.get(f"/api/v2/needs-you?limit=5&cursor={fresh['next']}", headers=headers("ana-test")).json()
    assert builds == [False] and aged["version"] == fresh["version"] and "restart" not in aged
    assert aged["items"] == api.get("/api/v2/needs-you", headers=headers("ana-test")).json()["items"][5:10]


def test_the_cache_holds_a_bounded_number_of_lists_and_one_version_a_viewer():
    held = needs_cache.NeedsCache()
    for n in range(needs_cache.MAX_ENTRIES + 10):
        held.keep(f"viewer{n}", "v1", [{"id": n}], None, needs_cache.begin())
    assert len(held) == needs_cache.MAX_ENTRIES and held.get("viewer0", "v1") is None
    held.keep("viewer99", "v1", [], None, needs_cache.begin())
    held.keep("viewer99", "v2", [], None, needs_cache.begin())
    assert held.get("viewer99", "v1") is None and held.get("viewer99", "v2") is not None


def test_a_build_that_began_before_another_never_takes_its_place(monkeypatch):
    """Two builds of a viewer's list run at once. The one whose snapshot began first (before a change) ends last: it
    is not kept over the newer one, whose cursors stay served, and nothing it held ages from when it ended."""
    now = [5000.0]
    monkeypatch.setattr(needs_cache, "clock", lambda: now[0])
    held = needs_cache.NeedsCache()
    older = needs_cache.begin()
    now[0] += 1
    newer = needs_cache.begin()
    now[0] += 4
    held.keep("ana", "after", [{"id": "b"}], None, newer)
    now[0] += 10
    held.keep("ana", "before", [{"id": "a"}], None, older)  # the slow build that began first ends last
    assert held.get("ana", "before") is None
    kept = held.get("ana", "after")
    assert kept is not None and kept.items == [{"id": "b"}]
    # Its newer one evicted for room, the older build is still not kept.
    for n in range(needs_cache.MAX_ENTRIES):
        held.keep(f"viewer{n}", "v1", [], None, needs_cache.begin())
    assert held.get("ana", "after") is None
    held.keep("ana", "before", [{"id": "a"}], None, older)
    assert held.get("ana", "before") is None
    # A list serves until TTL after its snapshot began, not after the build ended; one that old already is not kept.
    now[0] = newer + needs_cache.TTL + 0.5
    held.keep("ana", "after", [{"id": "b"}], None, newer)
    assert held.get("ana", "after") is None
    fresh = needs_cache.begin()
    now[0] += needs_cache.TTL - 1
    held.keep("ana", "later", [{"id": "c"}], None, fresh)
    assert held.get("ana", "later") is not None
    now[0] += 2
    assert held.get("ana", "later") is None


def test_a_page_cut_from_a_held_build_leaves_out_a_task_the_caller_no_longer_reads(api, monkeypatch):
    """Ben loses a task in his list (it is made private) after he read the first page: his next cursor does not give a
    page of the held build, whose counts still hold it. The list is built again without it, starting from the top
    (`restart`), as before the cache. A cursor hit while he still reads every task in it checks them all in one
    statement besides his task rules."""
    company(api, pages=0, questions=0, idle=0)
    store = api.app.state.store
    first = api.get("/api/v2/needs-you?limit=2", headers=headers("ben-test")).json()
    assert first["next"] and first["count"] >= 4, first
    whole = api.get("/api/v2/needs-you", headers=headers("ben-test")).json()
    second = api.get(f"/api/v2/needs-you?limit=2&cursor={first['next']}", headers=headers("ben-test")).json()
    assert "restart" not in second and second["items"] == whole["items"][2:4]
    # A cursor hit reads the caller's task rules and one statement for the page's tasks, never a build.
    seen = []
    take, give = store._take, store._give

    def counting_take():
        c = take()
        c.set_trace_callback(seen.append)
        return c

    def quiet_give(c):
        c.set_trace_callback(None)
        give(c)

    with monkeypatch.context() as patched:
        patched.setattr(store, "_take", counting_take)
        patched.setattr(store, "_give", quiet_give)
        again = api.get(f"/api/v2/needs-you?limit=2&cursor={first['next']}", headers=headers("ben-test")).json()
    assert again == second
    page_reads = [sql for sql in seen if "FROM tasks WHERE id IN" in sql]
    print(f"\n  a cursor hit's statements: {len(seen)}", *seen, sep="\n    ")
    assert len(page_reads) == 1 and len(seen) <= 12, seen
    gone = whole["items"][-1]["task_id"]  # on his last page, not the one he asks for: only the counts hold it
    assert gone and gone not in {item.get("task_id") for item in second["items"]}
    with store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (gone,))
    later = api.get(f"/api/v2/needs-you?limit=2&cursor={first['next']}", headers=headers("ben-test")).json()
    assert later["restart"] is True and later["version"] != first["version"]
    rebuilt = api.get("/api/v2/needs-you", headers=headers("ben-test")).json()
    assert gone not in {item.get("task_id") for item in rebuilt["items"]}
    assert later["items"] == rebuilt["items"][:2] and later["count"] == first["count"] - 1
