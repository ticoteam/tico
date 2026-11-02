"""The status page reads each table once for every bot, and says exactly what each bot's own status line says."""
from backend import task_privacy as privacy, usage_limits
from backend.events import prime_status_lines, status_line
from backend.store import H
from backend.tests.test_api import api, assign, claim, post, ready  # noqa: F401
from backend.tests.test_run_backoff import hold_bot, setup


def lines(api, slugs, primed):
    """Every bot's status line from one snapshot, and the statements it took."""
    store, auth, who = api.app.state.store, api.app.state.auth, api.app.state.store.settings.test_identities["ana-test"]
    seen = []
    with store.read_transaction() as c:
        c.set_trace_callback(seen.append)
        default = usage_limits.company(c)
        inputs = privacy.status_inputs(c, slugs)
        if primed:
            prime_status_lines(c, slugs)
        out = {slug: status_line(c, who, slug, default, inputs[slug], auth=auth) for slug in slugs}
        c.set_trace_callback(None)
    return out, len(seen)


def test_primed_status_lines_match_each_bots_own_and_read_each_table_once(api, monkeypatch):
    machine = setup(api, monkeypatch)
    hold_bot(api, machine, monkeypatch)                  # ops: held after five failed runs, its last stop on record
    assign(api, machine, "doc-updater")
    ready(api, machine, ["ops", "doc-updater"])
    post(api, "tasks", {"owner_id": "doc-updater", "title": "Update the guide", "body": "Please update it."})
    live = claim(api, machine, "doc-updater")
    post(api, f"attempts/{live['id']}/started", {"thread_id": "t-live"}, machine["token"])       # a live run
    with api.app.state.store.transaction() as c:
        for slug, state in (("cpo", "idle"), ("finance", "idle"), ("coo", "crashed"), ("inbox", "idle")):
            H.status_set(c, H.KEEPER, slug, state=state, focus="Checked the queue")
        c.execute("INSERT INTO bot_budget(bot,held_at,window,runs,budget) VALUES('cpo',?,'hour',301,300)", (H.now(),))
        c.execute("INSERT INTO run_backoff(bot,fails,wait_until) VALUES('finance',2,?)",
                  (H.shift(H.now(), seconds=600),))
        c.execute("INSERT INTO usage_limits(bot,daily_usd) VALUES('coo',0.000001)")
    slugs = ["coo", "cpo", "doc-updater", "finance", "inbox", "ops", "product-design"]
    each, plain = lines(api, slugs, primed=False)
    together, primed = lines(api, slugs, primed=True)
    assert together == each
    assert each["ops"]["stopped"] == "run_hold" and each["cpo"]["stopped"] == "budget"
    assert each["finance"]["backoff"] and each["doc-updater"]["state"] == "running"
    # Seven single-row reads a bot become one read a table: what is left does not grow with the bots.
    # One read a table for every bot: idle bots added to the page add no statements, where each added several.
    fewer = [s for s in slugs if s not in ("inbox", "product-design")]
    assert lines(api, fewer, primed=True)[1] == primed < lines(api, fewer, primed=False)[1] < plain


def test_primed_status_lines_offer_the_same_held_run_for_review(api, monkeypatch):
    # A crashed bot offers its newest held run for review; with a stop on record, only that run's own held job.
    # The clock is fixed, so jobs share a created time: the last one written is the newest, read one by one or together.
    # coo and finance repeat ops's and cpo's shapes, so the page's held runs add no statements per bot.
    from backend.store import encode
    machine = setup(api, monkeypatch, tasks=9)
    first = claim(api, machine)
    with api.app.state.store.transaction() as c:
        jobs = [row[0] for row in c.execute("SELECT id FROM jobs WHERE bot_id='ops' ORDER BY rowid")]
        for job, bot, created, run, state, started in (
                (jobs[0], "ops", "2026-10-24T11:00:00.000000Z", "old", "failed", True),
                (jobs[1], "ops", H.now(), "tie-first", "failed", True),
                (jobs[2], "ops", H.now(), "tie-last", "expired", False),
                (jobs[3], "cpo", "2026-10-24T11:00:00.000000Z", "marked", "expired", True),
                (jobs[4], "cpo", H.now(), "newer", "failed", True),
                (jobs[5], "coo", "2026-10-24T11:00:00.000000Z", "coo-old", "failed", True),
                (jobs[6], "coo", H.now(), "coo-new", "expired", False),
                (jobs[7], "finance", "2026-10-24T11:00:00.000000Z", "finance-marked", "expired", True),
                (jobs[8], "finance", H.now(), "finance-newer", "failed", True)):
            c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,"
                      "started_at,finished_at) SELECT ?,?,?,runner_id,generation,?,?,lease_until,?,?,? FROM attempts WHERE id=?",
                      (run, job, bot, "hash-" + run, state, created, created if started else None, H.now(), first["id"]))
            c.execute("UPDATE jobs SET bot_id=?,state='uncertain',created_at=?,attempt_id=? WHERE id=?", (bot, created, run, job))
        c.execute("UPDATE attempts SET state='expired' WHERE id=?", (first["id"],))
        c.execute("DELETE FROM registry_metadata WHERE key='last-stop:ops'")
        for slug, run in (("cpo", "marked"), ("finance", "finance-marked")):
            c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)",
                      ("last-stop:" + slug, encode({"attempt_id": run, "at": H.now()})))
        for slug in ("ops", "cpo", "coo", "finance"):
            H.status_set(c, H.KEEPER, slug, state="crashed", focus="Checked the queue")
    each = lines(api, ["coo", "cpo", "finance", "ops"], primed=False)[0]
    together, primed = lines(api, ["coo", "cpo", "finance", "ops"], primed=True)
    assert together == each and lines(api, ["cpo", "ops"], primed=True)[1] == primed
    assert (each["coo"]["last_stop"]["attempt_id"], each["finance"]["last_stop"]["attempt_id"]) == (
        "coo-new", "finance-marked")
    ops, cpo = each["ops"]["last_stop"], each["cpo"]["last_stop"]
    assert (ops["attempt_id"], ops["kind"], ops["action"]) == ("tie-last", "never_started", "review")
    assert ops["reason"].startswith("Its computer refused the run before it started.")
    assert (cpo["attempt_id"], cpo["kind"], cpo["action"]) == ("marked", "stopped", "review")
    assert "lease ran out" in cpo["reason"]


def busy(api, machine, first, count, live=1):
    """`count` more active bots on the test computer, each with open tasks, two questions to a person, a task for a
    person, private tasks and a stopped run on record; the first `live` of them running, every third one crashed."""
    from backend.store import encode
    from backend.tests.test_api import assign
    store = api.app.state.store
    slugs = [f"busy-{n}" for n in range(first, first + count)]
    with store.transaction() as c:
        bots = {row["slug"]: {"name": row["slug"], "runtime": "fake", "status": row["state"]}
                for row in c.execute("SELECT slug,state FROM bots")}
        H.sync_registry(c, {**bots, **{s: {"name": s, "runtime": "fake", "status": "active"} for s in slugs}},
                        {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]})
        for slug in slugs:
            c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES(?,?,?)",
                      (slug, encode({"name": slug, "runtime": "fake"}), "ana"))
    for slug in slugs:
        assign(api, machine, slug)
    with store.transaction() as c:
        for n, slug in enumerate(slugs):
            first_task = None
            for k in range(4):
                task = H.task_create(c, "human:ana", f"Work {slug} {k}", "Do it.", "bot:" + slug, private=k == 3)
                first_task = first_task or task["id"]
                if k in (1, 2):
                    H.task_ask(c, "bot:" + slug, task["id"], "Which one?")
            H.task_create(c, "bot:" + slug, f"Need {slug}", "Please look.", "human:ben", private=bool(n % 2))
            H.status_set(c, "bot:" + slug, slug, state="crashed" if n % 3 == 2 else "idle", focus="Working",
                         task_id=first_task)
    ready(api, machine, ["ops", "doc-updater", *slugs])
    with store.transaction() as c:
        c.execute("UPDATE runners SET capacity=100")
    for slug in slugs[:live]:
        attempt = claim(api, machine, slug)
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + slug}, machine["token"])
    with store.transaction() as c:
        runs = [row[0] for row in c.execute("SELECT id FROM attempts ORDER BY created_at")]
        for n, slug in enumerate(slugs[live:]):
            c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)",
                      ("last-stop:" + slug, encode({"attempt_id": runs[n % len(runs)], "at": H.now()})))


def page(api, monkeypatch, path, token="ana-test"):
    """One status page as `token` sees it, without its clock fields, and the statements it took."""
    import contextlib
    from backend.tests.test_api import headers
    store, seen = api.app.state.store, []
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):
            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(seen.append)
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)
                return borrowed()
            patched.setattr(store, name, traced)
        response = api.get(path, headers=headers(token))
    assert response.status_code == 200, response.text
    body = {k: v for k, v in response.json().items() if k not in ("server_time", "tick_age_s", "last_tick")}
    return body, len(seen)


def unprimed(monkeypatch):
    """The pages as they read before priming: every bot's rows and every question's message one at a time."""
    from backend import events, views
    for module, name in ((views, "prime_machines"), (privacy, "prime_messages"), (events, "prime_status_lines")):
        monkeypatch.setattr(module, name, lambda *args: None)


PAGES = ("/api/status", "/api/v2/status")


def test_both_status_pages_read_a_fixed_number_of_statements_however_many_bots_and_jobs(api, monkeypatch):
    # Dev 509: on a team of about sixty bots these pages took 353 and 700 statements, 3.5-4 s each.
    machine = setup(api, monkeypatch, tasks=4)
    busy(api, machine, 0, 6)
    small = {path: page(api, monkeypatch, path)[1] for path in PAGES}
    busy(api, machine, 6, 6)
    big = {path: page(api, monkeypatch, path) for path in PAGES}
    assert {path: count for path, (_, count) in big.items()} == small
    assert len(big["/api/v2/status"][0]["bots"]) >= 12 and len(big["/api/status"][0]["queued"]) >= 48
    # Control: without the priming each added bot adds statements again, so the counts above do measure it.
    unprimed(monkeypatch)
    plain = {path: page(api, monkeypatch, path)[1] for path in PAGES}
    assert all(plain[path] > small[path] + 12 for path in PAGES)


def test_primed_status_pages_show_each_person_exactly_what_they_showed_before(api, monkeypatch):
    machine = setup(api, monkeypatch, tasks=4)
    busy(api, machine, 0, 6, live=2)
    # Ana owns the company; Ben is not on the private tasks, the bots' asks to Ana, or the Inbox bot.
    for token in ("ana-test", "ben-test"):
        primed = {path: page(api, monkeypatch, path, token)[0] for path in PAGES}
        with monkeypatch.context() as patched:
            unprimed(patched)
            plain = {path: page(api, monkeypatch, path, token)[0] for path in PAGES}
        assert primed == plain
        lines = {line["bot"]: line for line in primed["/api/v2/status"]["bots"]}
        assert lines["busy-0"]["state"] == "running" and lines["busy-2"]["last_stop"]
        assert any(line["needs_human"] for line in lines.values())
    assert "inbox" not in {line["bot"] for line in page(api, monkeypatch, "/api/v2/status", "ben-test")[0]["bots"]}


def test_status_task_reads_skip_finished_tasks_and_keep_the_same_rows(api, monkeypatch):
    # Dev 509: OR-ing owner and requester read every task the bots ever had; on a long-lived team that was most of
    # a second of each status page. Finished company tasks now cost nothing: the work done stays the same.
    machine = setup(api, monkeypatch, tasks=4)
    busy(api, machine, 0, 6)
    slugs = [f"busy-{n}" for n in range(6)]
    store = api.app.state.store
    finished = finish_private(store, "busy-1")

    def read():
        steps = [0]
        with store.read_transaction() as c:
            inputs = privacy.status_inputs(c, slugs)
            actors = ["bot:" + slug for slug in slugs]
            marks = " IN (" + ",".join("?" * len(actors)) + ")"
            # Only the task read is counted: each new task also gets a room under a random id, and where those ids
            # fall moves the later reads' index seeks by a step or two, whatever the tasks' status.
            c.set_progress_handler(lambda: steps.__setitem__(0, steps[0] + 1), 1)
            c.execute(privacy.status_tasks_sql(marks), actors * 4).fetchall()
            c.set_progress_handler(None, 1)
            # The rows an unindexed read of the same filter gives: live or private tasks a bot owns or asked for.
            wanted = {tuple(t) for t in c.execute(
                f"SELECT {privacy.STATUS_TASK_COLUMNS} FROM tasks WHERE (owner_id{marks} OR requester_id{marks}) "
                f"AND {privacy.STATUS_TASKS_SQL}", actors * 2)}
        got = {slug: sorted(tuple(t[k] for k in t) for t in kinds["tasks"]) for slug, kinds in inputs.items()}
        assert {t for rows in got.values() for t in rows} == wanted
        return got, steps[0]

    before, steps = read()
    # Finished private tasks still come back, one the bot owns and one it asked for: they decide whose eyes its focus
    # is kept from, and only the private part of the read finds them.
    assert {t[0] for t in before["busy-1"] if t[4] and t[3] not in H.ACTIVE_STATUSES} == finished
    with store.transaction() as c:
        for n in range(40):
            task = H.task_create(c, "bot:" + slugs[n % 6], f"Old {n}", "Done long ago. " * 200,
                                 "bot:" + slugs[(n + 1) % 6], private=False)
            c.execute("UPDATE tasks SET status=? WHERE id=?", ("done" if n % 2 else "closed", task["id"]))
    after, more = read()
    assert after == before and more == steps and steps


def test_status_task_rows_come_back_as_one_row_with_the_statements_own_rows(api, monkeypatch):
    # Tico Team: the status task read stepped 1,100 rows a page (850 of them private tasks), each step the GIL given up
    # and taken back; with other requests running Python it took 100 ms to 1 s for 2 ms of SQLite work. The rows now
    # come back as one row of JSON: the same rows, values and types, for many bots and for one.
    machine = setup(api, monkeypatch, tasks=4)
    busy(api, machine, 0, 4)
    store = api.app.state.store
    finish_private(store, "busy-1")                         # finished private tasks: one it owns, one it asked for
    with store.transaction() as c:
        guest = H.task_create(c, "human:ben", "Guest copy", "Read along.", "bot:busy-2", private=True)
        c.execute("UPDATE tasks SET guest='human:cara', status='waiting', waiting_on='human:ben' WHERE id=?",
                  (guest["id"],))
        H.task_create(c, "bot:busy-3", "Asked only", "For a person.", "human:cara", private=False)   # requester only
        done = H.task_create(c, "bot:busy-0", "Done company work", "Old.", "bot:busy-0", private=False)
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (done["id"],))
    actors = [f"bot:busy-{n}" for n in range(4)]
    marks = " IN (" + ",".join("?" * len(actors)) + ")"

    def typed(rows):
        return sorted(tuple((k, type(v).__name__, v) for k, v in row.items()) for row in rows)

    with store.read_transaction() as c:
        stepped = [dict(row) for row in c.execute(privacy.status_tasks_sql(marks), actors * 4)]
        seen = []
        c.set_trace_callback(seen.append)
        packed = privacy.status_tasks(c, marks, actors * 4)
        c.set_trace_callback(None)
        assert len(seen) == 1 and len(c.execute(seen[0]).fetchall()) == 1
        assert [list(row) for row in packed] == [privacy.STATUS_TASK_COLUMNS.split(",")] * len(packed)
        assert typed(packed) == typed(stepped)
        for bot in actors:
            assert typed(privacy.status_tasks(c, "=?", (bot,) * 4)) == typed(
                dict(row) for row in c.execute(privacy.status_tasks_sql("=?"), (bot,) * 4))
    ids = {row["id"] for row in packed}
    assert guest["id"] in ids and done["id"] not in ids
    assert any(row["private"] and row["status"] not in H.ACTIVE_STATUSES for row in packed)
    assert any(row["guest"] == "human:cara" and row["waiting_on"] == "human:ben" for row in packed)
    assert any(row["owner_id"] == "human:cara" and row["requester_id"] == "bot:busy-3" for row in packed)


def finish_private(store, slug):
    """Closes the private task `slug` owns and the private one it asked a person for; returns their ids."""
    with store.transaction() as c:
        ids = {row[0] for row in c.execute(
            "SELECT id FROM tasks WHERE private=1 AND ? IN (owner_id,requester_id)", ("bot:" + slug,))}
        assert {row[0] for row in c.execute(f"SELECT owner_id='bot:{slug}' FROM tasks WHERE id IN ({','.join('?' * len(ids))})",
                                            tuple(ids))} == {0, 1}
        c.execute(f"UPDATE tasks SET status=CASE WHEN owner_id=? THEN 'done' ELSE 'closed' END "
                  f"WHERE id IN ({','.join('?' * len(ids))})", ("bot:" + slug, *ids))
    return ids


def test_a_finished_private_task_still_keeps_its_bots_focus_from_who_cannot_read_it(api, monkeypatch):
    # A status line with no task id hides its focus when any private task of the bot is unreadable to the viewer:
    # once that task is finished only the private part of the status task read finds it.
    machine = setup(api, monkeypatch, tasks=4)
    busy(api, machine, 0, 2)
    store = api.app.state.store
    finish_private(store, "busy-1")
    with store.transaction() as c:
        c.execute("UPDATE bot_status SET task_id=NULL, focus='Drafting the offer letter' WHERE bot='busy-1'")
    for token, focus in (("ana-test", "Drafting the offer letter"), ("ben-test", "")):
        who = store.settings.test_identities[token]
        with store.read_transaction() as c:
            row = c.execute("SELECT * FROM bot_status WHERE bot='busy-1'").fetchone()
            batched = privacy.status(c, who, row, **privacy.status_inputs(c, ["busy-1"])["busy-1"])
            alone = privacy.status(c, who, row)
        assert batched["focus"] == alone["focus"] == focus


def test_stopped_runs_of_each_uncertain_bot_cost_a_bounded_number_of_statements(api, monkeypatch):
    # A bot with a stopped run costs its own queries on the status page (held runs, the keeper's leftovers, the
    # queued work behind them). Its runs are read for all bots together, so each further bot adds fewer statements
    # than before (about ten, now about seven); the bot's own checks still read per bot.
    machine = setup(api, monkeypatch, tasks=4)
    busy(api, machine, 0, 6)
    store = api.app.state.store

    def stop(count):
        with store.transaction() as c:
            c.execute("UPDATE jobs SET state='uncertain' WHERE id IN "
                      "(SELECT id FROM jobs WHERE state='queued' GROUP BY bot_id LIMIT ?)", (count,))

    stop(2)
    two = page(api, monkeypatch, "/api/status")[1]
    stop(6)
    six = page(api, monkeypatch, "/api/status")
    assert len(six[0]["health_issues"]) >= 6
    assert (six[1] - two) / 4 <= 8
