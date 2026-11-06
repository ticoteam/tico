"""GET /api/v2/health reads each computer, its bots and its failed runs in batches, so the statements it takes grow by a
constant (at most one a computer, for a computer's login hold) rather than by a few a computer or a bot."""
import contextlib
import random
import uuid
from concurrent.futures import ThreadPoolExecutor

from backend import health, queue_refusals as Q
from backend.store import H, encode
from backend.tests.test_api import api, headers, post, runner  # noqa: F401  (the api fixture)
from backend.tests.test_run_backoff import at, setup
from backend.tests.test_status_reads import busy


def counted(api, monkeypatch, path, token="ana-test"):
    """GET `path` as `token`: the body, and the statements on the page's read snapshot. Each request starts from cold
    roster caches, as a poll from a new process would."""
    store, seen = api.app.state.store, []
    store.roster_rows.clear()
    with monkeypatch.context() as patched:
        opened = store.read_transaction

        @contextlib.contextmanager
        def traced():
            with opened() as c:
                c.set_trace_callback(seen.append)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        # Background workers borrow store.read too; their periodic reads are not the page's work.
        patched.setattr(store, "read_transaction", traced)
        response = api.get(path, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def test_health_statement_count_excludes_background_media_reads(api, monkeypatch):
    counted(api, monkeypatch, "/api/v2/health")             # warm the token-usage cache and table checks
    _, quiet = counted(api, monkeypatch, "/api/v2/health")
    view = health.view
    with ThreadPoolExecutor(max_workers=1) as worker:
        def with_media_batch(*args, **kwargs):
            # Run the real worker's read during measurement, without waiting for its five-second timer.
            assert worker.submit(api.app.state.file_metadata.batch).result(timeout=5) is False
            return view(*args, **kwargs)

        monkeypatch.setattr(health, "view", with_media_batch)
        _, during = counted(api, monkeypatch, "/api/v2/health")
    assert during == quiet and quiet > 0, (quiet, during)


def add_computers(api, monkeypatch, machines, first, last):
    """Computers first..last-1 of the company, each with one bot that has a failed run and a worktree that failed."""
    at(monkeypatch, api, "2026-10-24T12:00:00.000000Z")
    for k in range(first, last):
        machine = runner(api, "ana", label=f"Mac {k}")
        machines.append(machine)
        slug = f"busy-{k}"
        busy(api, machine, k, 1)                           # its one run is running: it is the one that fails
        with api.app.state.store.transaction() as c:
            attempt = c.execute("SELECT id FROM attempts WHERE runner_id=? AND state='running'",
                                (machine["runner_id"],)).fetchone()[0]
        post(api, f"attempts/{attempt}/events", {"events": [{"seq": 1, "kind": "error",
                                                              "payload": {"error": "Tool crashed"}}]}, machine["token"])
        post(api, f"attempts/{attempt}/complete", {"outcome": "failed", "last_seq": 1, "text": "boom"}, machine["token"])
        with api.app.state.store.transaction() as c:
            task = c.execute("SELECT id FROM tasks WHERE owner_id=? ORDER BY rowid LIMIT 1", ("bot:" + slug,)).fetchone()[0]
            c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,computer_id,detail) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (H.new_id(), task, "worktree", f"file:///work/{k}", "work", "failed", "human:ana", H.now(), "",
                       '{"error": "disk full", "size_mb": 3}'))
    at(monkeypatch, api, "2026-10-24T12:40:00.000000Z")


def test_health_takes_the_same_statements_however_many_computers_bots_and_failed_runs_it_reads(api, monkeypatch):
    machines = [setup(api, monkeypatch, tasks=2)]
    add_computers(api, monkeypatch, machines, 1, 3)
    counted(api, monkeypatch, "/api/v2/health")            # warm: the table checks each connection keeps
    body, few = counted(api, monkeypatch, "/api/v2/health")
    assert any(row["id"] == "worktrees" for row in body["checks"]) and any(row["id"] == "failed" for row in body["checks"])
    add_computers(api, monkeypatch, machines, 3, 15)
    body, many = counted(api, monkeypatch, "/api/v2/health")
    assert "disk full" in next(row for row in body["checks"] if row["id"] == "worktrees")["summary"]
    # 12 more computers, each with a bot, a failed run and a failed worktree: one statement a computer is left, the
    # login hold each computer with queued work reads (login_holds.get); nothing else grows.
    assert many - few <= 12, (
        f"GET /api/v2/health takes {many} statements with 15 computers against {few} with 3: a read runs once per "
        "computer or bot again instead of once for the whole page. Read the rows for all of them at once.")


def test_health_takes_the_same_statements_however_many_refused_queues_it_shows(api, monkeypatch):
    """Each refused queue's job and message are read together, and its bots' access in one check, so a page that shows
    more refused queues takes no more statements than one that shows fewer."""
    rng = random.Random(7)                                 # ids decide which queues refuse: the same ones each run
    monkeypatch.setattr(H, "new_id", lambda: str(uuid.UUID(int=rng.getrandbits(128), version=4)))
    monkeypatch.setattr(H, "uuid7", lambda: str(uuid.UUID(int=rng.getrandbits(128), version=4)))
    machine = setup(api, monkeypatch, tasks=2)
    at(monkeypatch, api, "2026-10-24T12:00:00.000000Z")
    busy(api, machine, 0, 40)
    at(monkeypatch, api, "2026-10-24T12:40:00.000000Z")
    store = api.app.state.store
    with store.read() as c:
        values = {f"busy-{k}": Q.current(c, store.settings, f"busy-{k}") for k in range(40)}
    refusable = sorted(slug for slug, value in values.items() if value and value["reason"] != "lane_held")
    assert len(refusable) >= 12, len(refusable)
    statements, done = {}, 0
    for count in (0, 4, 12):
        with store.transaction() as c:
            for slug in refusable[done:count]:                   # refused for over 12 hours, as the sweep leaves them
                c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (Q.KEY + slug, encode(
                    {**values[slug], "since": "2026-10-23T00:00:00.000000Z", "observed_at": H.now()})))
        done = count
        body, statements[count] = counted(api, monkeypatch, "/api/v2/health")
        shown = next((row for row in body["checks"] if row["id"] == "queue_refused"), {}).get("items", [])
        assert len(shown) == count, (count, len(shown))
    assert statements[12] == statements[4], (
        f"GET /api/v2/health takes {statements[12]} statements with 12 refused queues against {statements[4]} with 4: a "
        "refused queue reads its bot's access, job or message on its own again instead of once for the page.")
