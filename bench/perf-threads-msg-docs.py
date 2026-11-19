"""Measures three reads that ran slow on the hosted TIDY company (Nov 19, last 2 h): GET /api/v2/threads/{id} (611
calls, p95 1.1 s, worst 4.4 s), GET /api/v2/messaging/bots (55 calls, p95 1.4 s) and GET /api/v2/docs/search (39
calls, p95 1.1 s), on bench/perf-hub-overload.py's fictional company grown to TIDY's size: 18.6k tasks (1,150 open),
a room for each, about 39k messages and 31k thread_followers (one person following 17k rooms), 165 bots, 17
mailboxes the bots work on, 300 bot automations with 1,400 runs, and 595 team docs of about 14 KB each (a few far
larger).

    nice -n 10 .venv/bin/python bench/perf-threads-msg-docs.py

Env as bench/perf-hub-overload.py (RUNS, DB=<file> to keep and reuse the seeded database), plus ANSWERS=<file> (each
read's answer as JSON, to compare two checkouts on one DB), STATEMENTS=1 (each read's repeated statement shapes),
PROFILE=<path> (cProfile one read as ana), GMAIL_MS=<ms> (each Gmail call answers after this long, with two review
labels, as a real mailbox does; default 0 answers nothing) and READS=<name,...> (the reads measured, by name).

Only fictional data, in a temporary folder removed afterwards (unless DB is given). Never point it at a real hub.
"""

import collections
import importlib.util
import json
import os
import random
import re
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("OPEN", "1150")
os.environ.setdefault("CLOSED", "17450")
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

_spec = importlib.util.spec_from_file_location("perf_hub_overload", ROOT / "bench" / "perf-hub-overload.py")
assert _spec and _spec.loader
overload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(overload)

from backend.store import H
from backend.tests.helpers.api import headers

RUNS = int(os.environ.get("RUNS", 5))
GMAIL_MS = float(os.environ.get("GMAIL_MS", 0))
WORDS = (
    "launch pricing customer cleaning schedule turnover guest checklist refund invoice supplier onboarding policy "
    "review payroll market rental booking calendar inspection photo laundry inventory damage claim host airport "
    "weekly monthly report growth retention channel partner budget forecast hiring training escalation support "
    "quality standard process owner metric target deadline vendor contract renewal region city team"
).split()
QUERIES = ("cleaning checklist", "refund policy for damage claims", "how do we onboard a new host", "zzqx")


def _text(rnd, words):
    return " ".join(rnd.choice(WORDS) for _ in range(words))


def _doc(rnd, n):
    """A markdown doc of about 14 KB in sections; one in sixty far larger (TIDY's largest is 500 KB)."""
    size = 14000 if n % 60 else 160000
    parts, total = [f"# Doc {n} {_text(rnd, 3)}\n"], 0
    while total < size:
        section = f"\n## {_text(rnd, 3).title()}\n\n" + "\n\n".join(_text(rnd, 60) for _ in range(4)) + "\n"
        parts.append(section)
        total += len(section)
    return "".join(parts)


def seed(api):
    overload.seed(api)
    grow(api)


def grow(api):
    """perf-hub-overload's company grown to TIDY's messages, followers, mailboxes, automations and docs."""
    store = api.app.state.store
    rnd = random.Random(19)
    now = H.now()
    with store.transaction() as c:
        rooms = [
            r[0]
            for r in c.execute(
                "SELECT c.id FROM conversations c JOIN tasks t ON t.id=c.task_id WHERE c.parent_id IS NULL ORDER BY c.rowid"
            )
        ]
        bots = [r[0] for r in c.execute("SELECT id FROM bots ORDER BY id")]
        # A message in each task room (a bot's update), so every scan over rooms and their messages pays.
        for k, room in enumerate(rooms):
            c.execute(
                "INSERT INTO messages(id,conversation_id,sender_id,kind,body,refs,created_at) VALUES(?,?,?,?,?,?,?)",
                (H.new_id(), room, "bot:" + bots[k % len(bots)], "say", f"Update {k}: {_text(rnd, 12)}", "{}", now),
            )
        # A long room: TIDY's busiest has 1,045 messages.
        long_room = rooms[3]
        for _ in range(1000):
            c.execute(
                "INSERT INTO messages(id,conversation_id,sender_id,kind,body,refs,created_at) VALUES(?,?,?,?,?,?,?)",
                (H.new_id(), long_room, rnd.choice(["human:ana", "bot:" + bots[0]]), "say", _text(rnd, 20), "{}", now),
            )
        # Followers: ana follows nearly every room (TIDY's busiest person follows 17k), ben and cara some.
        for k, room in enumerate(rooms):
            for actor, every in (("human:ana", 1), ("human:ben", 6), ("human:cara", 8), ("bot:" + bots[1], 9)):
                if k % every == 0:
                    c.execute(
                        "INSERT OR IGNORE INTO thread_followers(id,thread_id,actor,following,notify,reason,"
                        "last_read_seq,created_at,updated_at) VALUES(?,?,?,1,'live','task',0,?,?)",
                        (H.new_id(), room, actor, now, now),
                    )
        # Mailboxes the bots work on, each read by a few bots, and their automations.
        for m in range(17):
            mid, address = f"mb{m:02d}", f"inbox{m:02d}@acme.example"
            c.execute(
                "INSERT INTO mailboxes(id,address,kind,created_by,created_at,updated_at,checked_at) "
                "VALUES(?,?,'shared','human:ana',?,?,?)",
                (mid, address, now, now, now),
            )
            for b in range(m * 7, m * 7 + 7):
                c.execute(
                    "INSERT INTO mailbox_accesses(id,mailbox_id,grantee_type,grantee_id,level,granted_by,created_at,"
                    "updated_at) VALUES(?,?,'bot',?,'read_write','human:ana',?,?)",
                    (H.new_id(), mid, bots[b % len(bots)], now, now),
                )
        for a in range(300):
            aid = f"auto{a:03d}"
            c.execute(
                "INSERT INTO automations(id,owner_id,name,enabled,trigger_type,flow,version,next_due_at,created_at,"
                "created_by_id,updated_at) VALUES(?,?,?,1,'schedule','{}',1,?,?,'human:ana',?)",
                (aid, "bot:" + bots[a % len(bots)], f"Check {a}", now, now, now),
            )
            for r in range(5 if a % 13 else 0):
                c.execute(
                    "INSERT INTO automation_runs(id,automation_id,version,started_at,status) VALUES(?,?,1,?,'ok')",
                    (f"{aid}-run{r}", aid, now),
                )
    from backend import docs as D

    with store.transaction() as c:
        for n in range(595):
            body = _doc(rnd, n)
            path = f"_librarian/doc-{n}.md" if n < 8 else f"team/doc-{n}.md"
            c.execute(
                "INSERT INTO docs(id,path,title,body,version,created_by_id,created_at,updated_by_id,updated_at) "
                "VALUES(?,?,?,?,1,'human:ana',?,'human:ana',?)",
                (f"doc-{n:04d}", path, f"Doc {n} {_text(rnd, 2)}", body, now, now),
            )
            rowid = c.execute("SELECT rowid FROM docs WHERE id=?", (f"doc-{n:04d}",)).fetchone()[0]
            D.index(c, rowid, f"Doc {n}", path, body)


def reads(api):
    """{name: (path, token)}: each read as the readers that matter."""
    with api.app.state.store.read() as c:
        task_room = c.execute(
            "SELECT c.id FROM conversations c JOIN tasks t ON t.id=c.task_id WHERE t.status='open' "
            "AND c.parent_id IS NULL ORDER BY c.rowid LIMIT 1 OFFSET 3"
        ).fetchone()[0]
        done_room = c.execute(
            "SELECT c.id FROM conversations c JOIN tasks t ON t.id=c.task_id WHERE t.status='done' "
            "AND c.parent_id IS NULL ORDER BY c.rowid LIMIT 1"
        ).fetchone()[0]
        company = c.execute(
            "SELECT id FROM conversations WHERE audience='company' AND parent_id IS NULL ORDER BY rowid LIMIT 1"
        ).fetchone()[0]
        dm = c.execute(
            "SELECT id FROM conversations WHERE audience IN ('personal','participants') AND parent_id IS NULL "
            "ORDER BY rowid LIMIT 1"
        ).fetchone()[0]
    out = {}
    for token in ("ana-test", "ben-test", "cara-test"):
        who = token.split("-")[0]
        out[f"thread_task {who}"] = (f"/api/v2/threads/{task_room}", token)
        out[f"thread_done {who}"] = (f"/api/v2/threads/{done_room}", token)
        out[f"thread_company {who}"] = (f"/api/v2/threads/{company}", token)
    out["thread_dm ana"] = (f"/api/v2/threads/{dm}", "ana-test")
    out["messaging ana"] = ("/api/v2/messaging/bots", "ana-test")
    out["messaging ben"] = ("/api/v2/messaging/bots", "ben-test")
    for n, q in enumerate(QUERIES):
        out[f"docs_search q{n} ana"] = (f"/api/v2/docs/search?q={q.replace(' ', '+')}", "ana-test")
        out[f"docs_search_all q{n} ben"] = (f"/api/v2/docs/search?q={q.replace(' ', '+')}&collection=all", "ben-test")
    wanted = os.environ.get("READS")
    return {k: v for k, v in out.items() if not wanted or k.split()[0] in wanted.split(",")}


def fake_gmail():
    """Gmail as a real mailbox answers: two review labels and a count, each call after GMAIL_MS."""
    from backend import gmail_live, google_tokens

    def call(mailbox, path, token, **query):
        time.sleep(GMAIL_MS / 1000)
        if path == "labels":
            return {
                "labels": [
                    {"id": "L1", "name": "hub/needs-reply", "type": "user"},
                    {"id": "L2", "name": "hub/drafted", "type": "user"},
                    {"id": "INBOX", "name": "INBOX", "type": "system"},
                ]
            }
        return {"messagesTotal": 3}

    gmail_live._call = call
    google_tokens.token = lambda c, mailbox, scope, write=False: "fake-token"


def measure(api, path, token):
    from backend import messaging

    store, seen, times, cpu = api.app.state.store, [], [], []
    messaging._REVIEW.clear()
    first = api.get(path, headers=headers(token))
    assert first.status_code == 200, (path, first.status_code, first.text[:300])
    messaging._REVIEW.clear()
    with overload.counting(store, seen):
        api.get(path, headers=headers(token))
    for _ in range(RUNS):
        messaging._REVIEW.clear()  # every call as the first in a minute: the page is opened less often than that
        t, u = time.perf_counter(), time.process_time()
        api.get(path, headers=headers(token))
        times.append((time.perf_counter() - t) * 1000)
        cpu.append((time.process_time() - u) * 1000)
    return seen, statistics.median(times), statistics.median(cpu), len(first.content), first.json()


def report(api):
    with api.app.state.store.read() as c:
        counts = {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("bots", "tasks", "conversations", "messages", "thread_followers", "docs", "mailboxes")
        }
    print(counts)
    if GMAIL_MS:
        fake_gmail()
    answers = {}
    for name, (path, token) in reads(api).items():
        seen, ms, cpu, size, answer = measure(api, path, token)
        print(f"{name:28s} {len(seen):6d} statements {ms:9.1f} ms median {cpu:9.1f} ms CPU {size / 1024:8.1f} KB")
        answers[name] = answer
        if os.environ.get("STATEMENTS"):
            shapes = collections.Counter(re.sub(r"'[^']*'|\b\d+\b", "?", " ".join(q.split()))[:160] for q in seen)
            for q, k in shapes.most_common(10):
                if k > 1:
                    print(f"      {k:4d}  {q}")
    if os.environ.get("ANSWERS"):
        Path(os.environ["ANSWERS"]).write_text(json.dumps(answers, sort_keys=True, default=str, indent=1))
    if os.environ.get("PROFILE"):
        import cProfile
        import pstats

        api.get(os.environ["PROFILE"], headers=headers(os.environ.get("TOKEN", "ana-test")))  # warm: what starts once
        prof = cProfile.Profile()
        prof.enable()
        for _ in range(3):
            api.get(os.environ["PROFILE"], headers=headers(os.environ.get("TOKEN", "ana-test")))
        prof.disable()
        pstats.Stats(prof).sort_stats(os.environ.get("SORT", "cumulative")).print_stats(int(os.environ.get("TOP", 45)))


def main():
    from backend.tests.helpers.api_app import api_client

    keep = os.environ.get("DB")
    with tempfile.TemporaryDirectory(prefix=".tmp-threads-msg-docs-", dir=ROOT) as directory:
        folder = Path(directory)
        reuse = keep and Path(keep).exists()
        base = os.environ.get("BASE")  # perf-hub-overload's own seed, kept to grow again (a DB= file of that bench)
        if not reuse and base and Path(base).exists():
            install = overload.reopened(folder, base)
        else:
            install = overload.reopened(folder, keep) if reuse else api_client(folder, None)
        api = next(install)
        try:
            if not reuse:
                t = time.perf_counter()
                grow(api) if base and Path(base).exists() else seed(api)
                print(f"seeded in {time.perf_counter() - t:.1f}s")
                if keep:
                    with api.app.state.store.read() as c:
                        c.execute("VACUUM INTO ?", (keep,))
            report(api)
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
