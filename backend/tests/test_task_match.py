"""The Tasks page's search and filters on the server (backend/task_match.py, perf I16): a search or a filter answers
bounded keyset pages whose rows, page after page, are exactly what the page's own match (ui/app/task-row.ts
`taskFilterMatch`, run here in node) keeps of the whole view, in the same order; a private task is in no page and no
`next_after`; a page starts after the last row given whatever moved meanwhile; a catch-up takes the same filters."""

import itertools
import json
import pathlib
import shutil
import subprocess
import urllib.parse

import pytest

from backend import hubdb as H
from backend.tests.helpers.api import get, post

ROW_TS = pathlib.Path(__file__).resolve().parents[2] / "ui" / "app" / "task-row.ts"

# The page's own match, as the browser runs it: task-row.ts with its types stripped, its clock set to the server's.
HARNESS = r"""
const fs = require('fs'), vm = require('vm'), {stripTypeScriptTypes} = require('module');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ctx = {console};
vm.createContext(ctx);
vm.runInContext(stripTypeScriptTypes(fs.readFileSync(input.file, 'utf8')), ctx);
vm.runInContext(`Date.now = () => ${Date.parse(input.now)};`, ctx);
ctx.cases = input.cases;
console.log(JSON.stringify(vm.runInContext(
  'cases.map(c => c.rows.filter(t => taskFilterMatch(t, c.q, c.filters)).map(t => t.id))', ctx)));
"""


def page_match(tmp_path, cases):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node runs the page's own match")
    harness = tmp_path / "match.js"
    harness.write_text(HARNESS)
    run = subprocess.run(
        [node, str(harness)],
        input=json.dumps({"file": str(ROW_TS), "now": H.now(), "cases": cases}),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if "stripTypeScriptTypes is not a function" in run.stderr:
        pytest.skip("this node cannot strip TypeScript types")
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


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


def server_query(q, f):
    """The page's search and filters as the page sends them (ui/app/tasks-page.ts tasksNarrowQuery)."""
    out = {}
    if q.strip():
        out["search"] = q
    for k, name in (("owner", "owners"), ("stage", "stages"), ("asked_by", "requesters"), ("tag", "labels")):
        if f.get(k):
            out[name] = ",".join(f[k])
    if f.get("waiting_on"):
        out["turn"] = ",".join(f["waiting_on"])
    if f.get("due"):
        out["due"] = f["due"]
    if f.get("late"):
        out["late"] = "true"
    if f.get("pushed"):
        out["pushed_past"] = "true"
    return urllib.parse.urlencode(out)


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
    late = task("Late one in spec", owner="cara")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE visits SET late_told_at=? WHERE task_id=? AND left_at IS NULL", (now, late["id"]))
    secret = task("Secret latency plan", private=True, labels=["perf"])
    snoozed = task("Snoozed latency idea")
    post(api, f"tasks/{snoozed['id']}/snooze", {"until": H.shift(now, days=5)[:10]})
    return made, secret


VIEW = "pipeline=product&status=open&snoozed=all&sort=queue"


def cases_for(rows):
    """Each filter alone with each value the view offers, a pair of values, and each combined with a search."""
    of = {
        "owner": sorted({t["owner_id"] for t in rows if t.get("owner_id")}),
        "stage": sorted({t["stage_id"] for t in rows if t.get("stage_id")}),
        "asked_by": sorted({t["requester_id"] for t in rows if t.get("requester_id")}),
        "tag": sorted({label for t in rows for label in t.get("labels") or []}),
        "waiting_on": sorted({(t.get("turn") or {}).get("who") for t in rows if (t.get("turn") or {}).get("who")}),
    }
    filters = [{}]
    for k, values in of.items():
        filters += [{k: [v]} for v in values] + [{k: values[:2]}]
    filters += [{"due": d} for d in ("overdue", "week", "any")] + [{"late": True}, {"pushed": True}]
    filters += [{"owner": of["owner"][:1], "tag": ["perf"]}, {"stage": ["spec"], "due": "any"}]
    searches = ["", "latency", "LATENCY home", "#1", "1", "perf", "release", "straße", "ümlaut café", "zzqx", "ui"]
    return [(q, f) for q, f in itertools.product(searches, filters) if q or f]


@pytest.mark.parametrize("token", ["ana-test", "cara-test"])
def test_each_search_and_filter_reaches_exactly_the_rows_the_page_matched_in_the_same_order(api, tmp_path, token):
    make_view(api)
    whole = read(api, VIEW + "&limit=500", token=token)
    assert whole["next_after"] is None
    cases = cases_for(whole["tasks"])
    expected = page_match(tmp_path, [{"rows": whole["tasks"], "q": q, "filters": f} for q, f in cases])
    assert any(expected) and not all(expected)
    for (q, f), want in zip(cases, expected):
        got, pages = every_page(api, VIEW + "&" + server_query(q, f), limit=3, token=token)
        assert [t["id"] for t in got] == want, (q, f)
        assert all(n <= 3 for n in pages)


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
