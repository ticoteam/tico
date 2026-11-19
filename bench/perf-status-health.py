"""Measures the polls that loaded the hosted TIDY company's server on Nov 19 (GET /api/v2/health 676 statements and
4.3 s, GET /api/status 119, GET /api/employees 50 at 797 KB, GET /api/v2/tasks 200 and 5.4 s) on bench/perf-hub-overload.py's
fictional company, with the shapes those reads repeat on: each bot's status naming a task that has closed or is gone,
an open pull request on most bot-owned open tasks, and a cloud machine for most bots.

    nice -n 10 .venv/bin/python bench/perf-status-health.py

Env: as perf-hub-overload.py (OPEN, CLOSED, THREADS, RUNS, DB=<file> to keep and reuse the seeded database,
TOKENS), plus PATHS (default the four reads above), ANSWERS=<file> (each read's answer as JSON, the clock's own fields
blanked, to compare two checkouts on one DB) and STATEMENTS=1 (each read's repeated statement shapes).

Only fictional data, in a temporary folder removed afterwards (unless DB is given). Never point it at a real hub.
"""

import collections
import importlib.util
import json
import os
import re
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

from backend.store import H
from backend.tests.helpers.api import headers

PATHS = tuple(
    os.environ.get("PATHS", "/api/v2/health,/api/status,/api/employees,/api/v2/tasks?status=open&limit=500").split(",")
)
TOKENS = tuple(os.environ.get("TOKENS", "ana-test,ben-test").split(","))
RUNS = int(os.environ.get("RUNS", 5))
# The answer's own clock: the same database read twice a few seconds apart differs here and nowhere else.
CLOCK = {"server_time", "tick_age_s", "last_tick", "seq", "age_minutes", "quiet", "minutes", "cursor"}


def seed(api):
    overload.seed(api)
    store = api.app.state.store
    with store.transaction() as c:
        bots = [row[0] for row in c.execute("SELECT id FROM bots ORDER BY id")]
        done = [row[0] for row in c.execute("SELECT id FROM tasks WHERE status='done' ORDER BY rowid LIMIT 400")]
        for n, slug in enumerate(bots):
            own = c.execute(
                "SELECT id FROM tasks WHERE owner_id=? AND status='open' ORDER BY rowid LIMIT 1", ("bot:" + slug,)
            ).fetchone()
            # A third name a finished task, a third a task that is gone, the rest their open one (TIDY: 49, 57, 8).
            task_id = (
                done[n % len(done)] if n % 3 == 0 else f"gone-{n:04d}" if n % 3 == 1 else (own[0] if own else None)
            )
            c.execute(
                "INSERT OR REPLACE INTO bot_status(bot,state,focus,task_id,changed_at,updated_at) VALUES(?,?,?,?,?,?)",
                (slug, "idle", f"Working on item {n}", task_id, H.now(), H.now()),
            )
            if n % 3 != 2:
                c.execute(
                    "INSERT OR REPLACE INTO cloud_machines(bot,machine_id,runner_id,state,updated_at) VALUES(?,?,?,?,?)",
                    (slug, f"m-{n:04d}", f"r{n % 4}", "running", H.now()),
                )
        # Eighteen active bots with no computer (TIDY, Nov 19): the status page asks after each one's cloud machine.
        c.execute(
            "DELETE FROM assignments WHERE bot IN (SELECT id FROM bots WHERE id LIKE 'bot%' ORDER BY id LIMIT 18)"
        )
        for k, (tid,) in enumerate(
            c.execute("SELECT id FROM tasks WHERE status='open' AND owner_id LIKE 'bot:%' ORDER BY rowid").fetchall()
        ):
            c.execute(
                "INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,number,detail) "
                "VALUES(?,?,'pr',?,?,'open','bot:x',?,'acme/app',?,'{}')",
                (H.new_id(), tid, f"https://github.example/acme/app/pull/{k + 1}", f"PR {k + 1}", H.now(), k + 1),
            )
    from backend import health

    health.record_never_stuck(store)  # the hourly reading Health's Tasks check shows


def blank_clock(value):
    if isinstance(value, dict):
        return {k: (None if k in CLOCK else blank_clock(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [blank_clock(v) for v in value]
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+Z", value):
        return "<time>"
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
    with api.app.state.store.read() as c:
        counts = {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("bots", "tasks", "conversations", "messages", "task_links", "bot_status", "cloud_machines")
        }
    print(counts)
    answers = {}
    for path in PATHS:
        for token in TOKENS:
            seen, ms, size, answer = measure(api, path, token)
            print(f"{path:40s} {token:9s} {len(seen):6d} statements {ms:9.1f} ms {size / 1024:8.1f} KB")
            answers[f"{path} {token}"] = blank_clock(answer)
            if os.environ.get("SIZES") and isinstance(answer, list) and answer and isinstance(answer[0], dict):
                fields = collections.Counter()
                for row in answer:
                    for key, value in row.items():
                        fields[key] += len(json.dumps(value, separators=(",", ":"), default=str))
                print("      bytes by field:", ", ".join(f"{k} {v // 1024} KB" for k, v in fields.most_common(12)))
            if os.environ.get("STATEMENTS"):
                shapes = collections.Counter(re.sub(r"'[^']*'|\b\d+\b", "?", " ".join(q.split()))[:160] for q in seen)
                for q, k in shapes.most_common(12):
                    if k > 1:
                        print(f"      {k:4d}  {q}")
    if os.environ.get("ANSWERS"):
        Path(os.environ["ANSWERS"]).write_text(json.dumps(answers, sort_keys=True, default=str))


def main():
    from backend.tests.helpers.api_app import api_client

    keep = os.environ.get("DB")
    with tempfile.TemporaryDirectory(prefix=".tmp-status-health-", dir=ROOT) as directory:
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
