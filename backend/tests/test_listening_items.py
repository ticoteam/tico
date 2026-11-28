"""Listing what a listening gathered (GET /api/v2/listening/items, backend/listening.py): pages that follow a cursor
to the end, the true total and counts by status, and filters by source and gathered time."""

import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import time_machine

from backend.tests.helpers.api import get, headers
from backend.tests.helpers.listening import SEARCH, item, listening, save, source_id

START = datetime(2026, 11, 2, 7, 30, tzinfo=timezone.utc)
SOURCES = (SEARCH, {**SEARCH, "id": "s2", "target": "team agents"}, {**SEARCH, "id": "s3", "target": "standup bots"})


def made(api, sources=(SEARCH,)):
    with time_machine.travel(START, tick=False):
        return listening(api, sources=sources)["id"]


def fill(api, source, numbers, when=START):
    """Saves one post per number, 200 to a save, all gathered at `when`."""
    numbers = list(numbers)
    with time_machine.travel(when, tick=False):
        for at in range(0, len(numbers), 200):
            save(api, source, [item(n, f"Post {n} about standups") for n in numbers[at : at + 200]])


def mark(api, status, where="1=1", args=()):
    with api.app.state.store.transaction() as c:
        c.execute(f"UPDATE listening_items SET status=? WHERE {where}", (status, *args))


def ordered(api, aid, status):
    """The ids of a status in the route's order, read straight from the table."""
    with api.app.state.store.read() as c:
        return [
            r[0]
            for r in c.execute(
                "SELECT id FROM listening_items WHERE automation_id=? AND status=? ORDER BY created_at DESC, rowid DESC",
                (aid, status),
            )
        ]


def page(api, aid, token="ana-test", expected=200, **query):
    return get(api, "listening/items?" + urlencode({"automation": aid, **query}), token=token, expected=expected)


def pages(api, aid, **query):
    out, cursor = [], None
    for _ in range(20):  # a cursor that does not move fails here, not as a hang
        out.append(page(api, aid, **query, **({"cursor": cursor} if cursor else {})))
        cursor = out[-1]["next_cursor"]
        if not cursor:
            return out
    raise AssertionError("still a next_cursor after 20 pages")


def test_1200_dropped_items_page_to_the_end_each_once_with_the_true_total_and_counts(api):
    aid = made(api)
    fill(api, source_id(api, aid), range(1230))
    mark(api, "dropped")
    mark(api, "kept", "native_id IN (" + ",".join("?" * 30) + ")", [str(n) for n in range(30)])
    read = pages(api, aid, status="dropped", limit=500)
    assert [len(p["items"]) for p in read] == [500, 500, 200]
    assert {p["total"] for p in read} == {1200}
    assert [i["id"] for p in read for i in p["items"]] == ordered(api, aid, "dropped")
    counts = read[0]["counts"]
    assert (counts["dropped"], counts["kept"], counts["sent"]) == (1200, 30, 0)
    assert set(counts) == {"new", "duplicate", "kept", "dropped", "sending", "sent", "failed", "expired"}


def test_a_limit_over_500_still_answers_500_and_a_cursor_for_the_rest(api):
    aid = made(api)
    fill(api, source_id(api, aid), range(600))
    first = page(api, aid, limit=5000)
    assert (len(first["items"]), first["total"], first["counts"]["new"]) == (500, 600, 600)
    assert first["next_cursor"]


def test_a_source_filter_lists_only_that_source_and_totals_it(api):
    aid = made(api, SOURCES)
    for key, numbers in (("s1", range(0, 5)), ("s2", range(5, 12)), ("s3", range(12, 21))):
        fill(api, source_id(api, aid, key), numbers)
    two = source_id(api, aid, "s2")
    read = page(api, aid, source=two)
    assert {i["source_id"] for i in read["items"]} == {two}
    assert (len(read["items"]), read["total"], read["counts"]["new"]) == (7, 7, 7)


def test_since_and_until_list_what_was_gathered_in_between_since_included_until_not(api):
    aid = made(api)
    source = source_id(api, aid)
    for day in range(10):
        fill(api, source, [day], when=START + timedelta(days=day))
    since = (START + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = (START + timedelta(days=7)).isoformat()  # +00:00 reads the same as Z
    read = page(api, aid, since=since, until=until)
    assert [i["native_id"] for i in read["items"]] == ["6", "5", "4", "3"]
    assert read["total"] == 4
    assert page(api, aid, since="2026-11-10")["total"] == 2  # a date is its midnight, UTC


def offset_page(api, aid, status, offset, limit):
    """What offset paging would answer now: the order the route uses, skipping `offset` rows."""
    with api.app.state.store.read() as c:
        return [
            r[0]
            for r in c.execute(
                "SELECT id FROM listening_items WHERE automation_id=? AND status=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (aid, status, limit, offset),
            )
        ]


def read_on(api, aid, first, status, limit):
    """The ids of page one, then of every later page the cursor reads."""
    seen, cursor = [i["id"] for i in first["items"]], first["next_cursor"]
    for _ in range(20):
        if not cursor:
            return seen
        later = page(api, aid, status=status, limit=limit, cursor=cursor)
        seen += [i["id"] for i in later["items"]]
        cursor = later["next_cursor"]
    raise AssertionError("still a next_cursor after 20 pages")


def kept_30(api):
    aid = made(api)
    source = source_id(api, aid)
    fill(api, source, range(30))
    mark(api, "kept")
    return aid, source


def test_an_item_leaving_an_earlier_page_while_paging_skips_nothing(api):
    """An item on page one is sent after page one is read. Offset paging then skips the first item of page two."""
    aid, _ = kept_30(api)
    first = page(api, aid, status="kept", limit=10)
    before = ordered(api, aid, "kept")
    gone = first["items"][0]["id"]
    mark(api, "sent", "id=?", (gone,))
    by_offset = [i["id"] for i in first["items"]] + [
        n for at in (10, 20) for n in offset_page(api, aid, "kept", at, 10)
    ]
    assert before[10] not in by_offset  # the scenario does catch offset paging
    seen = read_on(api, aid, first, "kept", 10)
    assert len(seen) == len(set(seen)) == 30
    assert seen == before


def test_an_item_arriving_ahead_while_paging_repeats_nothing(api):
    """A newer item arrives after page one is read. Offset paging then repeats the last item of page one."""
    aid, source = kept_30(api)
    first = page(api, aid, status="kept", limit=10)
    before = ordered(api, aid, "kept")
    fill(api, source, [99], when=START + timedelta(hours=1))
    mark(api, "kept", "native_id='99'")
    by_offset = [i["id"] for i in first["items"]] + [
        n for at in (10, 20, 30) for n in offset_page(api, aid, "kept", at, 10)
    ]
    assert by_offset.count(before[9]) == 2  # the scenario does catch offset paging
    seen = read_on(api, aid, first, "kept", 10)
    assert len(seen) == len(set(seen)) == 30
    assert seen == before


def test_a_few_items_come_on_one_page_and_none_give_an_empty_last_page(api):
    aid = made(api)
    fill(api, source_id(api, aid), range(3))
    few = page(api, aid)
    assert (len(few["items"]), few["total"], few["next_cursor"]) == (3, 3, None)
    none = page(api, aid, status="failed")
    assert (none["items"], none["total"], none["next_cursor"]) == ([], 0, None)
    exact = page(api, aid, limit=3)
    assert (len(exact["items"]), exact["next_cursor"]) == (3, None)


def made_cursor(at, rowid):
    raw = json.dumps([at, rowid]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_a_broken_cursor_a_backwards_range_or_another_listenings_source_is_refused_by_name(api):
    aid = made(api)
    fill(api, source_id(api, aid), range(3))
    with time_machine.travel(START, tick=False):
        other = listening(api)["id"]
    for query, param in (
        ({"cursor": "not-a-cursor"}, "cursor"),
        ({"cursor": "eyJ4IjoxfQ"}, "cursor"),  # base64 of a JSON object, not a position
        ({"cursor": made_cursor("not a time", 5)}, "cursor"),
        ({"cursor": made_cursor("2026-11-02T07:30:00.000000Z", 2**63)}, "cursor"),  # past what SQLite binds
        ({"cursor": made_cursor("2026-11-02T07:30:00.000000Z", True)}, "cursor"),
        ({"since": "2026-11-09", "until": "2026-11-03"}, "since"),
        ({"since": "yesterday"}, "since"),
        ({"until": "soon"}, "until"),
        ({"source": source_id(api, other)}, "source"),
    ):
        refused = page(api, aid, expected=422, **query)
        assert refused["error"]["param"] == param, refused


def test_a_listening_the_caller_may_not_see_is_not_found_with_any_filter(api):
    aid = made(api)
    source = source_id(api, aid)
    fill(api, source, range(3))
    hidden = page(api, aid, token="cara-test", expected=404, source=source, since="2026-11-01", status="new")
    assert "counts" not in str(hidden) and "total" not in str(hidden)


def test_a_call_with_only_status_and_limit_answers_the_same_items_in_the_same_order(api):
    aid = made(api)
    source = source_id(api, aid)
    fill(api, source, range(150))
    fill(api, source, range(150, 160), when=START + timedelta(hours=2))
    r = api.get(f"/api/v2/listening/items?automation={aid}&status=new&limit=120", headers=headers())
    assert [i["id"] for i in r.json()["items"]] == ordered(api, aid, "new")[:120]
    default = page(api, aid)
    assert [i["id"] for i in default["items"]] == ordered(api, aid, "new")[:100]
