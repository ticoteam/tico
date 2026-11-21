"""The Tasks page's search and filters on the server (backend/task_match.py, perf I16): a search or a filter answers
bounded keyset pages; a private task is in no page and no `next_after`; a page starts after the last row given whatever
moved meanwhile; a catch-up takes the same filters."""

import json
import urllib.parse

import pytest

from backend import hubdb as H
from backend.tests.helpers.api import get, post


def product(api):
    stage = {"owner": "assignee", "requirements": [{"kind": "owner_done"}]}
    record = {
        "name": "Product",
        "goal": "Specs that ship",
        "stages": [{"id": "spec", "name": "Spec", **stage}, {"id": "build", "name": "Build", **stage}],
    }
    return post(api, "pipelines/product", record)["pipeline"]


def read(api, query, token="ana-test"):
    return get(api, "tasks?brief=1&" + query, token=token)


def every_page(api, query, limit, token="ana-test"):
    """Every row a person reaches: the first page, then More until it goes away (`next_after`). Also the pages."""
    rows, pages, after = [], [], None
    while True:
        page = read(api, f"{query}&limit={limit}" + (f"&after={after}" if after else ""), token=token)
        rows += page["tasks"]
        pages.append(len(page["tasks"]))
        after = page["next_after"]
        if not after:
            return rows, pages
        assert len(pages) < 100


def make_view(api):
    """A pipeline of varied open tasks: owners, requesters, stages, labels, numbers, dates, a late one, a pushed-past
    one, a snoozed one, and a private one Cara may not read."""
    product(api)
    now = H.now()
    made = []

    def task(title, owner="ben", token="ana-test", **more):
        t = post(
            api,
            "tasks",
            {"title": title, "body": "Body words never match.", "owner": owner, "pipeline": "product", **more},
            token=token,
        )
        made.append(t)
        return t

    task("Cut latency on task open", labels=["perf", "ui"])
    task("Latency budget for Home", owner="cara", labels=["perf"], due=H.shift(now, days=-2))
    task("Straße und Ümlaut CAFÉ", owner="ops", due=H.shift(now, days=3))
    task("Rename the billing page", owner="cara", token="ben-test", due=H.shift(now, days=30))
    task("ship the zzqx-free build", token="cara-test", labels=["release-1"])
    for i in range(12):
        task(f"Plain task {i}", owner=["ben", "cara", "ops"][i % 3], token=["ana-test", "ben-test"][i % 2])
    pushed = task("Guest checkout", labels=["ui"])
    post(api, f"tasks/{pushed['id']}/move", {"to": "build", "why": "The spec is in the PR"})
    # A person's task is Late only past its due date, so this one is past it.
    late = task("Late one in spec", owner="cara", due=H.shift(now, days=-1))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE visits SET late_told_at=? WHERE task_id=? AND left_at IS NULL", (now, late["id"]))
    secret = task("Secret latency plan", private=True, labels=["perf"])
    snoozed = task("Snoozed latency idea")
    post(api, f"tasks/{snoozed['id']}/snooze", {"until": H.shift(now, days=5)[:10]})
    return made, secret


VIEW = "pipeline=product&status=open&snoozed=all&sort=queue"


def test_a_private_match_is_in_no_page_and_no_more_signal(api):
    made, secret = make_view(api)
    seen = read(api, VIEW + "&search=latency&limit=500", token="cara-test")["tasks"]
    assert secret["id"] not in {t["id"] for t in seen} and seen
    # Exactly a page of what Cara may read: no next_after because of the task she may not.
    exact = read(api, VIEW + f"&search=latency&limit={len(seen)}", token="cara-test")
    assert exact["next_after"] is None and [t["id"] for t in exact["tasks"]] == [t["id"] for t in seen]
    assert secret["id"] in {t["id"] for t in read(api, VIEW + "&search=latency&limit=500")["tasks"]}


def test_a_search_that_matches_nothing_is_one_small_answer(api):
    make_view(api)
    page = read(api, VIEW + "&search=nothing-matches-this&limit=100")
    assert page["tasks"] == [] and page["next_after"] is None and page["next_offset"] is None


def test_the_next_page_starts_after_the_last_row_whatever_moved_meanwhile(api):
    product(api)
    made = [
        post(api, "tasks", {"title": f"Report {i}", "body": "x", "owner": "ben", "pipeline": "product"})
        for i in range(6)
    ]
    first = read(api, VIEW + "&search=report&limit=3")
    assert [t["id"] for t in first["tasks"]] == [t["id"] for t in made[:3]]
    # Meanwhile a row already given is deleted, and a new matching task is added (it sorts last).
    post(api, f"tasks/{made[0]['id']}/delete", {})
    added = post(api, "tasks", {"title": "Report late", "body": "x", "owner": "ben", "pipeline": "product"})
    rest, _ = every_page(api, VIEW + "&search=report&after=" + first["next_after"], limit=3)
    assert [t["id"] for t in rest] == [t["id"] for t in made[3:]] + [added["id"]]


def test_the_archive_pages_most_recently_finished_first(api):
    product(api)
    done = []
    for i in range(5):
        t = post(api, "tasks", {"title": f"Audit {i}", "body": "x", "owner": "ben", "pipeline": "product"})
        post(api, f"tasks/{t['id']}/move", {"to": "done", "why": "Finished."})
        done.append(t["id"])
    query = "pipeline=product&status=done&snoozed=hide&sort=finished&search=audit"
    whole = read(api, query + "&limit=500")["tasks"]
    got, pages = every_page(api, query, limit=2)
    assert [t["id"] for t in got] == [t["id"] for t in whole] and set(done) == {t["id"] for t in got}
    assert pages == [2, 2, 1]


def test_a_catch_up_takes_the_same_search_and_filters(api):
    product(api)
    keep = post(api, "tasks", {"title": "Ship the latency fix", "body": "x", "owner": "ben", "pipeline": "product"})
    other = post(api, "tasks", {"title": "Write the notes", "body": "x", "owner": "ben", "pipeline": "product"})
    query = VIEW + "&search=latency&owners=human:ben"
    first = read(api, query)
    assert [t["id"] for t in first["tasks"]] == [keep["id"]]
    post(api, "tasks/" + other["id"], {"version": other["version"], "title": "Write the latency notes"})
    post(api, "tasks/" + keep["id"], {"version": keep["version"], "title": "Ship the fix"})
    got = get(api, f"tasks?brief=1&{query}&changed_after={first['cursor']}")
    assert [t["id"] for t in got["tasks"]] == [other["id"]] and got["gone"] == [keep["id"]]


@pytest.mark.parametrize(
    "bad",
    [
        "after=nonsense",
        "after=WzEsMl0",
        "sort=finished&after=WzAsMSwiYSIsImIiXQ",
        "due=tomorrow",
        "after=WzBd&offset=3",
    ],
)
def test_a_bad_page_start_or_filter_is_refused(api, bad):
    get(api, "tasks?" + bad, expected=422)


def test_a_task_with_no_created_date_is_still_reached_page_by_page(api):
    product(api)
    made = [
        post(api, "tasks", {"title": f"Report {i}", "body": "x", "owner": "ben", "pipeline": "product"})
        for i in range(4)
    ]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created_at=NULL, rank=NULL WHERE id IN (?, ?)", (made[1]["id"], made[2]["id"]))
    whole = read(api, VIEW + "&search=report&limit=500")["tasks"]
    got, pages = every_page(api, VIEW + "&search=report", limit=1)
    assert [t["id"] for t in got] == [t["id"] for t in whole] and len(got) == 4 and pages == [1, 1, 1, 1]


def test_waiting_on_a_private_blocker_matches_only_for_someone_who_may_open_it(api):
    """The Waiting on filter reads each wait as the board says it to the reader (waiting_on.unnamed): a blocker the
    reader may not open is said without its id, so its id never matches for them."""
    product(api)
    blocked = post(api, "tasks", {"title": "Ship the importer", "owner": "ben", "pipeline": "product"})
    secret = post(api, "tasks", {"title": "Secret blocker", "owner": "ben", "pipeline": "product", "private": True})
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO task_relations (from_task, to_task, kind, created_by, created_at) VALUES (?,?,?,?,?)",
            (blocked["id"], secret["id"], "depends_on", "human:ana", H.now()),
        )
    query = "pipeline=product&status=open&turn=" + urllib.parse.quote(secret["id"])
    assert [t["id"] for t in read(api, query)["tasks"]] == [blocked["id"]]
    assert [t["id"] for t in read(api, query, token="cara-test")["tasks"]] == []
