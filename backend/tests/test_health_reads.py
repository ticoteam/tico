"""The health checks read the company's settings once a page, and each bot's own rows once for all the bots."""

import contextlib
import re
import threading
from collections import Counter

from backend.tests.helpers.api import headers, ready  # noqa: F401
from backend.tests.helpers.health_reads import counted
from backend.tests.helpers.run_failures import setup as bare_setup
from backend.tests.helpers.status_reads import busy
from backend.tests.helpers.task_lanes import RUNNER_CAPABILITIES, new_task, task_bot


def setup(api, monkeypatch, tasks=8):
    """A computer that reports the capabilities of the oldest supported release (0.6.0-rc.1): its claims take the
    session root and lane rules every computer runs, so the query counts below measure those."""
    machine = bare_setup(api, monkeypatch, tasks=tasks)
    ready(api, machine, ["ops"], capabilities=RUNNER_CAPABILITIES)
    return machine


def warmed(api, monkeypatch, path, trace):
    """`counted` after one untraced read of `path`: a statement a first read fills in (a cache, a table check, a
    thread's first pass) is not counted, while one that repeats for each bot still is."""
    api.get(path, headers=headers("ana-test"))
    return counted(api, monkeypatch, path, trace=trace)


def statement_shape(sql):
    """A statement with its ids, values and numbers blanked, so the same query compares equal across the two reads."""
    text = " ".join(sql.split())
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "U", text)
    text = re.sub(r"'[^']*'", "'S'", text)
    return re.sub(r"\b\d+(\.\d+)?\b", "N", text)[:200]


def statements_apart(few_trace, many_trace):
    """What only the twelve-bot read ran, and what only the six-bot read ran, each with its count."""
    few_count, many_count = Counter(map(statement_shape, few_trace)), Counter(map(statement_shape, many_trace))
    more = [f"{n} x {sql}" for sql, n in (many_count - few_count).items()]
    fewer = [f"{n} x {sql}" for sql, n in (few_count - many_count).items()]
    return f"only at 12 bots: {more or ['none']}; only at 6 bots: {fewer or ['none']}"


def test_health_issues_health_and_employees_cost_few_or_no_statements_per_extra_bot(api, monkeypatch):
    # One company, six then twelve busy bots, three endpoints. Issues: production's bot-by-bot pass read about 30
    # statements a bot (its tools, its repositories, its usage limit, its status, the company's settings once per bot);
    # the company's settings are read once a page now, and each bot's rows for all bots at once, so a bot costs about
    # its own tools and repositories. Health and the employees list read the same statements however many bots.
    # Employees: each bot's task flow (going and waiting counts) was read with its own wait, step and start checks:
    # nine statements a bot (571 at 60 bots, about 1,000 at 110 in production's shape); once for all bots now.
    machine = setup(api, monkeypatch, tasks=2)
    busy(api, machine, 0, 6)
    paths = ("/api/v2/health/issues", "/api/v2/health", "/api/employees")
    for path in paths:
        counted(api, monkeypatch, path)  # warm: the table checks each connection keeps
    few_sql = {path: [] for path in paths}
    few = {path: counted(api, monkeypatch, path, trace=few_sql[path])[1] for path in paths}
    busy(api, machine, 6, 12)
    many_sql = {path: [] for path in paths}
    body, many = warmed(api, monkeypatch, "/api/v2/health/issues", many_sql["/api/v2/health/issues"])
    assert body["counts"] and body["bots"] >= 12
    assert (many - few["/api/v2/health/issues"]) / 6 <= 12, (
        few,
        many,
        statements_apart(few_sql["/api/v2/health/issues"], many_sql["/api/v2/health/issues"]),
    )  # about 33 a bot before; what is left is each bot's own tools and repositories
    _, health = warmed(api, monkeypatch, "/api/v2/health", many_sql["/api/v2/health"])
    assert health == few["/api/v2/health"], (
        few,
        health,
        statements_apart(few_sql["/api/v2/health"], many_sql["/api/v2/health"]),
    )
    body, many = warmed(api, monkeypatch, "/api/employees", many_sql["/api/employees"])
    assert len([row for row in body if str(row.get("name", "")).startswith("busy-")]) >= 18
    assert many == few["/api/employees"], (
        few,
        many,
        statements_apart(few_sql["/api/employees"], many_sql["/api/employees"]),
    )


def stuck_company(api, monkeypatch, count):
    """Production's shape on Tico Team: `count` bots with old queues whose tasks wait on another bot's subtask or
    on a blocker, a few running, and a refusal Health has watched for over 12 hours on each."""
    from backend import health
    from backend.store import H, encode
    from backend.tests.helpers.run_failures import at

    monkeypatch.setattr(
        health,
        "quiet_minutes",
        lambda ts: int((H.parse_ts(H.now()) - H.parse_ts(ts)).total_seconds() // 60) if H.parse_ts(ts) else 0,
    )
    machine = setup(api, monkeypatch, tasks=2)
    at(monkeypatch, api, "2026-10-24T12:00:00.000000Z")
    busy(api, machine, 0, count)
    with api.app.state.store.transaction() as c:
        for k in range(count):
            slug, other = f"busy-{k}", f"busy-{(k + 1) % count}"
            tasks = [r[0] for r in c.execute("SELECT id FROM tasks WHERE owner_id=? ORDER BY rowid", ("bot:" + slug,))]
            # A task that waits on the one it filed, and one filed for another bot by a person.
            H.task_create(c, "bot:" + slug, f"Part of {slug}", "Part.", "bot:" + other, parent_id=tasks[0])
            H.task_create(c, "human:ana", f"First for {slug}", "First.", "bot:" + other)
    at(monkeypatch, api, "2026-10-24T12:40:00.000000Z")
    with api.app.state.store.transaction() as c:
        for k in range(count):
            c.execute(
                "INSERT OR REPLACE INTO registry_metadata VALUES(?,?)",
                (
                    f"queue-refusal:busy-{k}",
                    encode({"since": "2026-10-23T00:00:00.000000Z", "observed_at": H.now(), "job": "j", "reason": "r"}),
                ),
            )


def refuse_queues(api, monkeypatch, slugs):
    """Each of these bots' queues refused for over 12 hours, as stuck_company leaves them."""
    from backend.store import H, encode
    from backend.tests.helpers.run_failures import at

    at(monkeypatch, api, "2026-10-24T12:40:00.000000Z")
    with api.app.state.store.transaction() as c:
        for slug in slugs:
            c.execute(
                "INSERT OR REPLACE INTO registry_metadata VALUES(?,?)",
                (
                    f"queue-refusal:{slug}",
                    encode({"since": "2026-10-23T00:00:00.000000Z", "observed_at": H.now(), "job": "j", "reason": "r"}),
                ),
            )


def test_refused_queue_checks_read_the_company_limit_once_for_all_bots(api, monkeypatch):
    # Each refused queue's claim check read the company's limit default fresh: its budget cutoffs (the Finance book
    # first, one read a bot) and every period's spend. One pass over the refused queues shares that default now.
    from backend.tests.helpers.run_failures import at

    machine = setup(api, monkeypatch, tasks=2)
    at(monkeypatch, api, "2026-10-24T12:00:00.000000Z")
    busy(api, machine, 0, 6)
    refuse_queues(api, monkeypatch, [f"busy-{k}" for k in range(6)])
    counted(api, monkeypatch, "/api/v2/health")
    few = counted(api, monkeypatch, "/api/v2/health")[1]
    at(monkeypatch, api, "2026-10-24T12:00:00.000000Z")
    busy(api, machine, 6, 12)
    refuse_queues(api, monkeypatch, [f"busy-{k}" for k in range(6, 18)])
    many = counted(api, monkeypatch, "/api/v2/health")[1]
    assert many == few, (few, many)


def test_health_claim_checks_read_every_queued_job_together_and_say_the_same(api, monkeypatch):
    # Tico Team (Oct 30): GET /api/v2/health ran about 1,600 statements a poll, 1.1 s of SQL. Every queued job of
    # every bot with old work was checked by the claim's rules one at a time, twice (bot_claimable, then
    # queued_reasons), plus each 12-hour refusal (queue_refusals.current): its task's queue return, step, check model,
    # fallback continuation and every wait. Those
    # reads are made once a table for all the jobs now, and the page says exactly what it said.
    from backend import execution

    stuck_company(api, monkeypatch, 30)
    counted(api, monkeypatch, "/api/v2/health")
    body, total = counted(api, monkeypatch, "/api/v2/health")
    primed = api.get("/api/v2/health", headers=headers("ana-test")).content
    assert any(row["id"] == "waiting" for row in body["checks"])
    # About 200 on v3: the Needs you plate reads every open task's waits together (waiting_on.prime), with each bot's
    # "can it run" (task_wakes.prime_bots) and each queued job's snooze (execution.prime_job_checks) read once for
    # all. 2,360 before the batching; a return to per-job reads is far above this bound.
    assert total < 320, total
    with monkeypatch.context() as unprimed:
        # Each job read on its own: what the checks read before.
        unprimed.setattr(execution, "prime_job_checks", lambda c, jobs: None)
        alone, slow = counted(api, monkeypatch, "/api/v2/health")
        # The control reads several times the batched total (about 560 against 200).
        assert slow > 2 * total, (total, slow)
        assert api.get("/api/v2/health", headers=headers("ana-test")).content == primed


def test_a_read_another_thread_runs_during_the_request_is_not_counted(api, monkeypatch):
    # CI run 43990123595 counted one more bot_status read at 12 bots than at 6 on /api/employees: a read another thread
    # ran on the store while the request was in flight. Here the first connection the request borrows starts a read on
    # a thread of its own and joins it, so that read lands in the window every time. The request's count must stay what
    # it was without it.
    store = api.app.state.store
    machine = setup(api, monkeypatch, tasks=2)
    busy(api, machine, 0, 6)
    counted(api, monkeypatch, "/api/employees")  # warm
    _, alone = counted(api, monkeypatch, "/api/employees")
    real_read, ran = store.read, []

    def elsewhere():
        with store.read() as other:
            other.execute("SELECT * FROM bot_status WHERE bot IN ('busy-0','busy-1')").fetchall()

    @contextlib.contextmanager
    def read_with_a_read_elsewhere():
        if not ran:
            ran.append(True)
            worker = threading.Thread(target=elsewhere)
            worker.start()
            worker.join()
        with real_read() as c:
            yield c

    monkeypatch.setattr(store, "read", read_with_a_read_elsewhere)
    body, beside = counted(api, monkeypatch, "/api/employees")
    assert ran, "the employees request borrowed no connection"
    assert len([row for row in body if str(row.get("name", "")).startswith("busy-")]) >= 6
    assert beside == alone, (alone, beside)
