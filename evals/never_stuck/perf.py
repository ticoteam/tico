"""How long one never-stuck pass takes over many open tasks: `python -m evals.never_stuck.perf [--tasks 10000]`.

Seeded and mostly idle, as an org's tasks are: most bot tasks have had their run and wait for their next try, some wait
on a person's answer, some are snoozed, a few are due for a try now. It times one pass of the real check after the
first (the first only sets each task's next try time), counts its SQL statements by kind, and prints where the time
goes. It changes nothing in the engine."""

import argparse
import cProfile
import datetime as dt
import io
import pstats
import random
import re
import time
from collections import Counter
from typing import Any

from . import world as Wd
from .world import ANA, BEN, BUILDER, CARA, GENERAL, REVIEWER, WRITER, World


def fill(world: World, count: int, rng: random.Random) -> dict[str, int]:
    """`count` open General tasks. Returns how many of each kind."""
    from backend import task_engine as E
    from backend.store import H

    kinds: Counter[str] = Counter()
    bots = (BUILDER, WRITER, REVIEWER)
    with world.tx() as c:
        for n in range(count):
            bot = bots[n % len(bots)]
            row = H.task_create(
                c, ANA, f"Chore {n}", "What to do and how to tell it is done.", bot, pipeline=GENERAL, lint=False
            )
            roll = rng.random()
            if roll < 0.10:
                E.add_requirement(c, row["id"], ANA, "answer", rng.choice((BEN, CARA)), "Which one?")
                kinds["waits on a person"] += 1
            elif roll < 0.15:
                E.snooze(c, row["id"], ANA, None, H.shift(H.now(), days=3))
                kinds["snoozed"] += 1
            else:
                kinds["a bot's turn"] += 1
        # Every wake so far had its run, which ended: the bots' tasks wait for their next try.
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
        c.execute(
            "INSERT INTO attempts (id, job_id, bot_id, runner_id, generation, token_hash, state, lease_until, "
            "created_at, started_at, finished_at, message_id, task_id) "
            "SELECT 'a' || j.id, j.id, j.bot_id, 'r1', 1, 'a' || j.id, 'completed', ?, ?, ?, ?, j.message_id, "
            "substr(m.wake_key, 1, instr(m.wake_key, ':') - 1) FROM jobs j JOIN messages m ON m.id = j.message_id "
            "WHERE m.wake_key IS NOT NULL",
            (H.now(), H.now(), H.now(), H.now()),
        )
    return dict(kinds)


def measure(count: int = 10_000, seed: int = 1, profile: bool = True) -> dict[str, Any]:
    from backend import never_stuck as NS

    rng = random.Random(seed)
    with World(seed) as world:
        started = time.perf_counter()
        kinds = fill(world, count, rng)
        made = time.perf_counter() - started
        world.shift(30)
        with world.tx() as c:
            first = NS.check(c)  # sets every bot task's next try time
        world.shift(150)  # 2.5 minutes on: the tasks whose bot stopped first are due for try 1
        due = int(count * 0.02)
        with world.tx() as c:
            # Most tasks stopped later: their next try is not due yet.
            c.execute(
                "UPDATE visits SET next_try_at=? WHERE left_at IS NULL AND id NOT IN "
                "(SELECT id FROM visits WHERE left_at IS NULL ORDER BY id LIMIT ?)",
                (Wd.ts(world.at() + dt.timedelta(minutes=10)), due),
            )
        Wd.METER.trace = []
        before = Wd.METER.count
        profiler = cProfile.Profile() if profile else None
        started = time.perf_counter()
        if profiler:
            profiler.enable()
        with world.tx() as c:
            done = NS.check(c)
        if profiler:
            profiler.disable()
        seconds = time.perf_counter() - started
        statements = Wd.METER.count - before
        trace, Wd.METER.trace = Wd.METER.trace, None
        shapes = Counter(_shape(s) for s in trace)
        top = ""
        if profiler:
            out = io.StringIO()
            pstats.Stats(profiler, stream=out).sort_stats("cumulative").print_stats(25)
            top = out.getvalue()
        return {
            "tasks": count,
            "kinds": kinds,
            "made_seconds": round(made, 1),
            "first_pass": {k: n for k, n in first.items() if n},
            "pass": {k: n for k, n in done.items() if n},
            "seconds": round(seconds, 3),
            "statements": statements,
            "statements_per_task": round(statements / count, 1),
            "top_statements": shapes.most_common(12),
            "profile": top,
        }


def _shape(statement: str) -> str:
    text = re.sub(r"\s+", " ", statement.strip())
    return text[:110]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tasks", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--no-profile", action="store_true")
    args = parser.parse_args()
    out = measure(args.tasks, args.seed, not args.no_profile)
    print(f"{out['tasks']} open tasks {out['kinds']} made in {out['made_seconds']} s")
    print(f"first pass: {out['first_pass']}")
    print(
        f"one pass: {out['seconds']} s, {out['statements']} statements ({out['statements_per_task']} a task): {out['pass']}"
    )
    print("most run statements:")
    for shape, n in out["top_statements"]:
        print(f"  {n:7d}  {shape}")
    if out["profile"]:
        print(out["profile"])


if __name__ == "__main__":
    main()
