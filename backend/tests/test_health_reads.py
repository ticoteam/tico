"""The health checks read the company's settings once a page, and each bot's own rows once for all the bots."""
import contextlib

from backend.tests.test_api import api, headers  # noqa: F401
from backend.tests.test_assigned_start import advance, cancel, clock, sweep  # noqa: F401
from backend.tests.test_status_reads import busy, setup
from backend.tests.test_task_lanes import new_task, task_bot


def counted(api, monkeypatch, path, token="ana-test", trace=None):
    """GET `path` as `token`: the body, and the statements it took on the one snapshot it read."""
    store, seen = api.app.state.store, []
    if trace is not None:
        seen = trace
    store.roster_rows.clear()                        # per-store caches start empty, so every count is the same count
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):
            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(lambda q: seen.append(q))
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)
                return borrowed()
            patched.setattr(store, name, traced)
        response = api.get(path, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def test_health_issues_cost_a_few_statements_per_extra_bot(api, monkeypatch):
    # Production: the bot-by-bot pass over every bot read about 30 statements a bot (its tools, its repositories, its
    # usage limit, its status, the company's settings once per bot). The company's settings are read once a page now,
    # and each bot's rows are read for all bots at once, so a bot costs about its own tools and repositories, not a repeat of the company's rows.
    machine = setup(api, monkeypatch, tasks=2)
    busy(api, machine, 0, 6)
    counted(api, monkeypatch, "/api/v2/health/issues")              # warm: the table checks each connection keeps
    few = counted(api, monkeypatch, "/api/v2/health/issues")[1]
    busy(api, machine, 6, 12)
    body, many = counted(api, monkeypatch, "/api/v2/health/issues")
    assert body["counts"] and body["bots"] >= 12
    assert (many - few) / 6 <= 12, (few, many)   # about 33 a bot before; what is left is each bot's own tools and repositories


def test_health_page_reads_the_same_statements_however_many_bots_it_shows(api, monkeypatch):
    machine = setup(api, monkeypatch, tasks=2)
    busy(api, machine, 0, 6)
    counted(api, monkeypatch, "/api/v2/health")
    few = counted(api, monkeypatch, "/api/v2/health")[1]
    busy(api, machine, 6, 12)
    assert counted(api, monkeypatch, "/api/v2/health")[1] == few


def lost_assignments(api, clock, names):
    """Tasks of one bot whose first start was lost: rewoken once, and still not started two hours on."""
    for name in names:
        new_task(api, name)
    cancel(api)
    advance(api, clock, 1)
    sweep(api)
    advance(api, clock, 3)


def overdue_items(body):
    return next((c["items"] for c in body["checks"] if c["id"] == "assigned_not_started"), [])


def test_health_reads_the_same_statements_for_many_overdue_assigned_tasks(api, clock, monkeypatch):
    # Each overdue assignment of one bot used to read that bot's whole start check again (owner, config, computer,
    # claim refusal, lanes): about 20 statements a task. The bot's check is read once for all of its tasks now.
    task_bot(api, lanes=True)
    lost_assignments(api, clock, [f"Lost {n}" for n in range(3)])
    counted(api, monkeypatch, "/api/v2/health")
    body, few = counted(api, monkeypatch, "/api/v2/health")
    assert len(overdue_items(body)) == 3
    lost_assignments(api, clock, [f"Lost {n}" for n in range(3, 27)])
    body, many = counted(api, monkeypatch, "/api/v2/health")
    assert len(overdue_items(body)) == 27
    # Each overdue task still reads its own queued job's fences (put-back, check model, attempts): about 9 each, not 47.
    assert many - few <= 10 * (27 - 3)


def test_employees_read_the_same_statements_however_many_bots_it_lists(api, monkeypatch):
    # Each bot's task flow (its going and waiting counts) was read with its own wait, step and start checks: nine
    # statements a bot (571 at 60 bots, about 1,000 at 110 in production's shape). They are read once for all bots now.
    machine = setup(api, monkeypatch, tasks=2)
    busy(api, machine, 0, 6)
    counted(api, monkeypatch, "/api/employees")
    few = counted(api, monkeypatch, "/api/employees")[1]
    busy(api, machine, 6, 12)
    body, many = counted(api, monkeypatch, "/api/employees")
    assert len([row for row in body if str(row.get("name", "")).startswith("busy-")]) >= 18
    assert many == few, (few, many)


def test_health_queue_checks_read_the_suspect_tasks_once_for_all_aged_bots(api, monkeypatch):
    # Thirty bots, each with two queued jobs past the wait threshold. The suspect tasks of every queue used to be
    # read bot by bot (about seven statements a bot, 217 at thirty bots); they are read once for all the queues now,
    # and so are the per-job claim checks (~1,450 statements here, then 166).
    from backend.tests.test_run_backoff import at
    machine = setup(api, monkeypatch, tasks=2)
    at(monkeypatch, api, "2026-10-24T12:00:00.000000Z")
    busy(api, machine, 0, 30)
    at(monkeypatch, api, "2026-10-24T12:40:00.000000Z")
    counted(api, monkeypatch, "/api/v2/health")
    body, total = counted(api, monkeypatch, "/api/v2/health")
    assert any(row["id"] == "waiting" for row in body["checks"])
    assert total < 250, total


def test_review_children_seek_the_parent_instead_of_scanning_every_task(api):
    """A waiting PR's review check looks for the children of one task. Starting from every task (a scan that read each
    row's pages) took 26 ms a check on Tico Team's 8k tasks; the parent's children are a seek on task_relations_to."""
    from backend import hubdb as H
    from backend.health import review_children_sql
    store = api.app.state.store
    with store.transaction() as c:
        c.executemany("INSERT INTO tasks(id,title,body,requester_id,owner_id,status,created_at,updated_at,private) "
                      "VALUES(?,?,?,?,?,?,?,?,?)",
                      [(H.new_id(), "Task", "Done.", "human:ana", "bot:ops", "done", H.now(), H.now(), 0)
                       for _ in range(2000)])
        c.execute("ANALYZE")
    with store.read_transaction() as c:
        plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + review_children_sql("1"), ("x",))]
    assert any(line.startswith("SEARCH r USING") and "task_relations_to" in line for line in plan), plan
    assert not [line for line in plan if line.startswith("SCAN tasks")], plan


def stuck_company(api, monkeypatch, count):
    """Production's shape on Tico Team: `count` bots with old queues whose tasks wait on another bot's subtask or
    on a blocker, a few running, and a refusal Health has watched for over 12 hours on each."""
    from backend import health
    from backend.store import H, encode
    from backend.tests.test_run_backoff import at
    monkeypatch.setattr(health, "quiet_minutes", lambda ts: int(
        (H.parse_ts(H.now()) - H.parse_ts(ts)).total_seconds() // 60) if H.parse_ts(ts) else 0)
    machine = setup(api, monkeypatch, tasks=2)
    at(monkeypatch, api, "2026-10-24T12:00:00.000000Z")
    busy(api, machine, 0, count)
    with api.app.state.store.transaction() as c:
        for k in range(count):
            slug, other = f"busy-{k}", f"busy-{(k + 1) % count}"
            tasks = [r[0] for r in c.execute("SELECT id FROM tasks WHERE owner_id=? ORDER BY rowid", ("bot:" + slug,))]
            H.task_create(c, "bot:" + slug, f"Part of {slug}", "Part.", "bot:" + other, parent_id=tasks[0])
            c.execute("UPDATE tasks SET status='waiting',blocked_since=?,blocked_kind='dependency' WHERE id=?",
                      (H.now(), tasks[0]))
            blocker = H.task_create(c, "human:ana", f"First for {slug}", "First.", "bot:" + other)
            c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) "
                      "VALUES(?,?,'blocks','human:ana',?)", (blocker["id"], tasks[3], H.now()))
            if k % 3 == 0:
                c.execute("UPDATE tasks SET status='doing' WHERE id=?", (tasks[2],))
    at(monkeypatch, api, "2026-10-24T12:40:00.000000Z")
    with api.app.state.store.transaction() as c:
        for k in range(count):
            c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (f"queue-refusal:busy-{k}", encode(
                {"since": "2026-10-23T00:00:00.000000Z", "observed_at": H.now(), "job": "j", "reason": "r"})))


def test_health_claim_checks_read_every_queued_job_together_and_say_the_same(api, monkeypatch):
    # Tico Team (Oct 30): GET /api/v2/health ran about 1,600 statements a poll, 1.1 s of SQL. Every queued job of
    # every bot with old work was checked by the claim's rules one at a time, twice (bot_claimable, then
    # queued_reasons), plus each 12-hour refusal (queue_refusals.current): its task's queue return, step, check model,
    # fallback continuation and every wait (blockers, subtasks, questions, approvals, worktrees, milestones). Those
    # reads are made once a table for all the jobs now, and the page says exactly what it said.
    from backend import execution
    stuck_company(api, monkeypatch, 30)
    counted(api, monkeypatch, "/api/v2/health")
    body, total = counted(api, monkeypatch, "/api/v2/health")
    primed = api.get("/api/v2/health", headers=headers("ana-test")).content
    assert any(row["id"] == "waiting" for row in body["checks"])
    assert total < 250, total          # 2,360 before
    with monkeypatch.context() as unprimed:
        # Each job read on its own, every task asked every wait: what the checks read before.
        unprimed.setattr(execution, "_queue_suspects", lambda c, queue: None)
        unprimed.setattr(execution, "prime_job_checks", lambda c, jobs: None)
        alone, slow = counted(api, monkeypatch, "/api/v2/health")
        assert slow > 1500, slow
        assert api.get("/api/v2/health", headers=headers("ana-test")).content == primed
