"""Measures one task response against the company's page history (perf I05): the same task read with no other pages,
then after adding PAGES unrelated pages with VERSIONS versions each, on a fictional company.

    nice -n 19 python3 bench/perf-page-identities.py
    PAGES=300 python3 bench/perf-page-identities.py   # a quicker run

Per case it prints the response size, the median wall time of RUNS reads, the SQL statements one read runs, and how
many of them read every row of bot_file_versions, bot_files, pages or page_identities (by EXPLAIN QUERY PLAN). It then
prints the statements a page check runs for a response that names 1 page and one that names 200 (they should match).
Run it on two trees to compare them: it uses only routes and files.page_ref_denials, which both have.
"""

import hashlib
import os
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import files
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import post
from backend.tests.helpers.api_app import api_client
from backend.tests.helpers.files import turn
from backend.tests.helpers.pages import page
from backend.tests.helpers.task_privacy_reads import traced

PAGES, VERSIONS = int(os.environ.get("PAGES", 3000)), int(os.environ.get("VERSIONS", 20))
RUNS = int(os.environ.get("RUNS", 15))
WHOLE = re.compile(
    r"\bSCAN (?:bot_file_versions|bot_files|pages|page_identities|[vfp])\b"
)  # a full index scan reads every entry too
TABLES = re.compile(r"\b(?:bot_file_versions|bot_files|pages|page_identities)\b")


def html(text):
    return f"<!doctype html><html><body><h1>{text}</h1><p>Weekly numbers.</p></body></html>".encode()


def seed_task(api):
    """A task with a page, a comment that attaches it and one that names it: a few KB as JSON."""
    _, attempt = turn(api)
    tid = post(
        api, "tasks", {"owner": "ops", "title": "Weekly review", "body": "Check the numbers.", "private": False}
    )["id"]
    fid = page(api, attempt, task=tid, title="Weekly numbers", data=html("weekly")).json()["page"]["file_id"]
    post(api, f"tasks/{tid}/comments", {"text": "The numbers are in.", "attachments": [fid + "@1"]})
    post(api, f"tasks/{tid}/comments", {"text": f"See page:{fid} for the totals."})
    return tid, attempt


def named_pages(api, attempt, tid, count):
    """`count` live pages on the task, made the way a bot makes them."""
    made = []
    for n in range(count):
        r = page(api, attempt, name=f"named-{n}.html", task=tid, title=f"Named {n}", data=html(f"named {n}"))
        assert r.status_code == 200, r.text
        made.append(r.json()["page"]["file_id"])
    return made


def seed_history(store, pages, versions):
    """`pages` unrelated bot pages with `versions` versions each, written straight to the tables."""
    now = H.now()
    with store.transaction() as c:
        blobs, rows, page_rows, version_rows = [], [], [], []
        for p in range(pages):
            fid = f"file-bench{p:019d}"
            rows.append((fid, "ops", "bot", f"bench-{p}.html", f"Bench page {p}", now, now, versions))
            page_rows.append((fid, now, now))
            for v in range(1, versions + 1):
                digest = hashlib.sha256(f"{p}:{v}".encode()).hexdigest()
                bid = f"blob-bench{p:08d}-{v:03d}"
                blobs.append((bid, digest, now))
                version_rows.append((fid, v, bid, digest, now))
        c.executemany(
            "INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) VALUES(?,'bot:ops',?,100,'p.html',"
            "'text/html',?)",
            blobs,
        )
        c.executemany(
            "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,current_version,"
            "first_activity_at,last_activity_at) VALUES(?,?,?,?,?,'page','text/html','tico_blob',?,?,?)",
            [
                (fid, bot, scope, ident, title, version, first, last)
                for fid, bot, scope, ident, title, first, last, version in rows
            ],
        )
        c.executemany("INSERT INTO pages(file_id,created_at,updated_at) VALUES(?,?,?)", page_rows)
        c.executemany(
            "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,actor,created_at) "
            "VALUES(?,?,?,?,100,'p.html','text/html','bot:ops',?)",
            version_rows,
        )


def whole_table_reads(db_path, statements):
    """The statements whose plan reads every row of one of the page tables."""
    found = []
    with sqlite3.connect(db_path) as c:
        # A partial index holds only the rows its WHERE names (the media worker's pending queue), not every row.
        partial = {
            r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '% WHERE %'")
        }
        for q in statements:
            if not TABLES.search(q) or not q.lstrip().upper().startswith(("SELECT", "WITH")):
                continue
            try:
                plan = " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + q))
            except sqlite3.Error:
                continue
            # A subquery the statement names itself (CO-ROUTINE p) is not the pages table.
            own = set(re.findall(r"(?:CO-ROUTINE|MATERIALIZE) (\w+)", plan))
            if any(
                m.group(0).split()[1] not in own
                and not re.match(
                    rf"\s+USING (?:COVERING )?INDEX ({'|'.join(map(re.escape, partial)) or '$^'})\b", plan[m.end() :]
                )
                for m in WHOLE.finditer(plan)
            ):
                found.append((q[:140], plan[:200]))
    return found


def measure(api, tid):
    path = "/api/v2/tasks/" + tid
    first = api.get(path, headers={"Authorization": "Bearer ana-test"})
    assert first.status_code == 200, first.text
    response, statements = traced(api, lambda: api.get(path, headers={"Authorization": "Bearer ana-test"}))
    times = []
    for _ in range(RUNS):
        t = time.perf_counter()
        api.get(path, headers={"Authorization": "Bearer ana-test"})
        times.append((time.perf_counter() - t) * 1000)
    return len(first.content), statistics.median(times), statements


def page_check_statements(api, refs):
    """The statements files.page_ref_denials runs for `refs`, in a fresh snapshot, as Cara."""
    store, seen = api.app.state.store, []
    with store.read() as c:
        c.set_trace_callback(seen.append)
        try:
            files.page_ref_denials(c, api.app.state.auth, Identity("human:cara", "human", "cara@acme.example"), refs)
        finally:
            c.set_trace_callback(None)
    return len(seen)


def report(api, label, tid):
    size, ms, statements = measure(api, tid)
    whole = whole_table_reads(api.app.state.store.settings.db_path, statements)
    print(f"{label:34s} {size:6d} B {ms:8.1f} ms median {len(statements):5d} statements {len(whole):3d} whole-table")
    for q, plan in whole:
        print(f"    {plan}\n      {q}")
    return ms


def test_task_read_against_page_history(api):
    store = api.app.state.store
    tid, attempt = seed_task(api)
    other = post(api, "tasks", {"owner": "ops", "title": "Many pages", "body": "Pages.", "private": False})["id"]
    pages = named_pages(api, attempt, other, 200)
    print()
    before = report(api, "task read, no other pages", tid)
    t = time.perf_counter()
    seed_history(store, PAGES, VERSIONS)
    print(f"added {PAGES} pages x {VERSIONS} versions in {time.perf_counter() - t:.1f}s")
    after = report(api, f"task read, +{PAGES * VERSIONS} versions", tid)
    print(f"ratio {after / before:.2f}x")
    one = page_check_statements(api, {"comments": [{"text": "see page:" + pages[0]}]})
    many = page_check_statements(api, {"comments": [{"text": "see page:" + fid} for fid in pages]})
    print(f"page check statements: names 1 page {one}, names 200 pages {many}")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory(prefix=".tmp-pages-", dir=Path(__file__).resolve().parents[1]) as directory:
        install = api_client(Path(directory), None)
        try:
            test_task_read_against_page_history(next(install))
        finally:
            install.close()
