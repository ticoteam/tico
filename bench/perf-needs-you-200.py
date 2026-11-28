"""Measures GET /api/v2/needs-you, whole and as the new web app asks for it (limit=200), and GET /api/v2/company/floor,
which counts the same list, on a fictional company the size of a large hosted one: 300 bots, 1,155 open pipeline tasks
in 19 pipelines, 70 OKs asked of the company's person, 112 open ❓ from bots (79 of them to her), some to others in
direct chats, ten Review-stage tasks with their second reviews, a few Paused bots, a few still being set up, and @s.

    nice -n 10 .venv/bin/python bench/perf-needs-you-200.py

The readers: Ana, the company's person (the long list); Ben, an Admin; Cara, Eve and Fay, people with a few ❓ each.
SHAPE=copy seeds it as a dev copy of it reads (backend/devcopy.py empties `assignments`): no bot is on a computer, so
each is a Paused cause of the company's person, holding up every task it has.
Per read and reader it prints the statements one call takes, the median wall time of RUNS calls and the answer's size.
Env: RUNS (7), DB=<file> (keep the seeded database and reuse it, to run two checkouts on the same rows),
ANSWERS=<file> (each answer as JSON, only the request clock's own fields blanked, to compare two checkouts),
PROFILE=<file> (cProfile Ana's limit=200 build there), SHAPE (live, or copy).

Only fictional data, in a temporary folder removed afterwards (unless DB is given). Never point it at a real hub.
"""

import importlib.util
import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

_spec = importlib.util.spec_from_file_location("perf_hub_overload", ROOT / "bench" / "perf-hub-overload.py")
assert _spec and _spec.loader
overload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(overload)

from backend import check_models, question_marks
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers

RUNS = int(os.environ.get("RUNS", 7))
SHAPE = os.environ.get("SHAPE", "live")
TOKENS = ("ana-test", "ben-test", "cara-test", "eve-test", "fay-test")
# The reads: Needs you whole and as the web app pages it, and the Company page's Floor, which counts it.
PATHS = tuple(os.environ.get("PATHS", "/api/v2/needs-you,/api/v2/needs-you?limit=200,/api/v2/company/floor").split(","))
SIZES = (491, 375, 225, 35, 14, 6, 4, 3, 1, 1)  # open tasks per pipeline; nine more pipelines have none
BOTS = [f"bot{n:03d}" for n in range(300)]
STAGES = (
    {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Review", "owner": "assignee", "requirements": [{"kind": "ok", "person": X.ANA, "line": "OK the build"}]},
)
REVIEW = (
    {
        "name": "Review",
        "owner": "assignee",
        "requirements": [{"kind": "page", "settings": {"at_head": True}}, {"kind": "linked_done"}],
    },
)
REVIEW_CHECK = {"runtime": "codex", "model": "gpt-5", "harness": "codex", "effort": "", "role": "review"}
# The request's own clock: the same database read twice a few seconds apart differs here and nowhere else.
CLOCK = {"server_time", "snoozed_until_next"}


def seed(api):
    from backend import access as Access
    from backend.views import roster

    store, rnd = api.app.state.store, random.Random(23)
    with store.transaction() as c:
        for slug, name in (("eve", "Eve"), ("fay", "Fay")):
            c.execute("INSERT OR IGNORE INTO humans(id,name,email) VALUES(?,?,?)", (slug, name, f"{slug}@acme.example"))
        whole = roster(c)
        people = [*whole["people"], {"id": "eve", "name": "Eve"}, {"id": "fay", "name": "Fay", "team": "ops"}]
        Access.save_roster(c, {**whole, "people": people, "org_groups": {"ops": {"name": "Ops"}}})
    for start in range(0, len(BOTS), 50):
        with store.transaction() as c:
            for slug in BOTS[start : start + 50]:
                X.bot(c, slug)
    with store.transaction() as c:
        for n in range(19):
            X.pipeline(c, f"p{n:02d}", f"Pipeline {n}", STAGES, manager=f"bot:{BOTS[n]}" if n < 3 else None)
        c.execute("UPDATE pipelines SET owner_id='human:ben' WHERE id='p03'")
    tasks, k = [], 0
    for n, size in enumerate(SIZES):
        for start in range(0, size, 200):
            with store.transaction() as c:
                for _ in range(start, min(size, start + 200)):
                    k += 1
                    bot = f"bot:{BOTS[k % len(BOTS)]}"
                    row = X.new_task(c, X.ANA, f"Task {k}", f"p{n:02d}", assignee=bot)
                    tasks.append((row["id"], bot))
                    if k % 16 == 0:  # 72 OKs asked of the company's person
                        X.ok_ask(c, row["id"], bot, X.ANA, f"OK {k}")
    # Open ❓: on tasks, most to the company's person; and in direct chats, to the others.
    people = ["human:ana"] * 79 + ["human:cara"] * 12 + ["human:eve"] * 11 + ["human:fay"] * 10
    with store.transaction() as c:
        for n, person in enumerate(people):
            task_id, bot = tasks[(n * 7) % len(tasks)]
            if n % 3 == 0 and person != "human:ana":
                question_marks.ask_in_chat(c, bot, person, f"Which one, {n}?", checked=False)
            else:
                question_marks.ask(c, bot, task_id, person, f"Should I go ahead with step {n}?", checked=False)
        # @s of the company's person by people, on tasks.
        for n in range(12):
            task_id, _ = tasks[(n * 11 + 3) % len(tasks)]
            H.task_comment(c, rnd.choice(["human:ben", "human:cara"]), task_id, f"@ana a look at this, {n}")
        # A Review stage's tasks, each with its second review filed and a message naming the commit it read.
        X.pipeline(c, "rev", "Reviews", REVIEW)
        for n in range(10):
            parent = X.new_task(c, X.ANA, f"Reviewed change {n}", "rev", assignee=f"bot:{BOTS[n]}")
            review = X.new_task(c, X.ANA, f"Second review {n}", assignee=f"bot:{BOTS[n + 1]}", parent=parent["id"])
            check_models.record(c, f"bot:{BOTS[n + 1]}", review["id"], REVIEW_CHECK, parent["id"], review["title"])
            H.task_comment(c, X.ANA, review["id"], f"PR https://github.com/acme/app/pull/{n} at {'a1' * 20}")
        # A few bots that cannot run: each is a Paused cause for whoever fixes it; and a few still being set up.
        c.execute("UPDATE bots SET state='paused' WHERE id IN ('bot290','bot291','bot292')")
        c.execute("UPDATE bots SET state='planned' WHERE id IN ('bot280','bot281','bot282','bot283','bot284','bot285')")
        if SHAPE == "copy":
            c.execute("DELETE FROM assignments")


def blank_clock(value):
    if isinstance(value, dict):
        return {k: (None if k in CLOCK else blank_clock(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [blank_clock(v) for v in value]
    return value


def measure(api, path, token):
    store, seen, times = api.app.state.store, [], []
    first = api.get(path, headers=headers(token))
    assert first.status_code == 200, (path, first.status_code, first.text[:300])
    with overload.counting(store, seen):
        api.get(path, headers=headers(token))
    for _ in range(RUNS):
        t = time.perf_counter()
        api.get(path, headers=headers(token))
        times.append((time.perf_counter() - t) * 1000)
    return seen, statistics.median(times), len(first.content), first.json()


def report(api):
    answers = {}
    for path in PATHS:
        for token in TOKENS:
            seen, ms, size, answer = measure(api, path, token)
            items = answer.get("items") or [] if isinstance(answer, dict) else []
            print(
                f"{path:30s} {token:9s} {len(seen):5d} statements {ms:8.1f} ms {size / 1024:7.1f} KB {len(items):4d} items"
            )
            answers[f"{path} {token}"] = blank_clock(answer)
    if os.environ.get("ANSWERS"):
        Path(os.environ["ANSWERS"]).write_text(json.dumps(answers, sort_keys=True, default=str))
    if os.environ.get("PROFILE"):
        import cProfile

        profile = cProfile.Profile()
        profile.enable()
        api.get("/api/v2/needs-you?limit=200", headers=headers("ana-test"))
        profile.disable()
        profile.dump_stats(os.environ["PROFILE"])


def main():
    from backend.tests.helpers.api_app import api_client

    os.environ.setdefault("TICO_READ_RATE_SECONDS", "-1")  # each call computes: identical reads are not shared here
    keep = os.environ.get("DB")
    with tempfile.TemporaryDirectory(prefix=".tmp-needs-200-", dir=ROOT) as directory:
        folder = Path(directory)
        reuse = keep and Path(keep).exists()
        install = overload.reopened(folder, keep) if reuse else api_client(folder, None)
        api = next(install)
        try:
            if not reuse:
                t = time.perf_counter()
                seed(api)
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
