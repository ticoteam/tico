"""Helpers that other tests share, moved out of test_status_reads.py so they import no test file."""

from backend import question_tasks as QT
from backend.store import H
from backend.tests.helpers.api import claim, post, ready
from backend.tests.helpers.run_backoff import setup
from backend import bot_settings as BS


def busy(api, machine, first, count, live=1):
    """`count` more active bots on the test computer, each with open tasks, two questions to a person, a task for a
    person, private tasks and a stopped run on record; the first `live` of them running, every third one crashed."""
    from backend.store import encode
    from backend.tests.helpers.api import assign

    store = api.app.state.store
    slugs = [f"busy-{n}" for n in range(first, first + count)]
    with store.transaction() as c:
        bots = {
            row["slug"]: {"name": row["slug"], "runtime": "fake", "status": row["state"]}
            for row in c.execute("SELECT slug,state FROM bots")
        }
        H.sync_registry(
            c,
            {**bots, **{s: {"name": s, "runtime": "fake", "status": "active"} for s in slugs}},
            {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]},
        )
        for slug in slugs:
            c.execute('INSERT INTO bot_config(bot,owner_id) VALUES(?,?)', (slug, 'ana'))
            BS.write(c, slug, {'name': slug, 'runtime': 'fake'})
    for slug in slugs:
        assign(api, machine, slug)
    with store.transaction() as c:
        for n, slug in enumerate(slugs):
            first_task = None
            for k in range(4):
                task = H.task_create(c, "human:ana", f"Work {slug} {k}", "Do it.", "bot:" + slug, private=k == 3)
                first_task = first_task or task["id"]
                if k in (1, 2):
                    QT.ask_plain(c, "bot:" + slug, "human:ana", "Which one?", parent=task)
            H.task_create(c, "bot:" + slug, f"Need {slug}", "Please look.", "human:ben", private=bool(n % 2))
            H.status_set(
                c, "bot:" + slug, slug, state="crashed" if n % 3 == 2 else "idle", focus="Working", task_id=first_task
            )
    ready(api, machine, ["ops", "doc-updater", *slugs])
    with store.transaction() as c:
        c.execute("UPDATE runner_states SET capacity=100")
    for slug in slugs[:live]:
        attempt = claim(api, machine, slug)
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + slug}, machine["token"])
    with store.transaction() as c:
        runs = [row[0] for row in c.execute("SELECT id FROM attempts ORDER BY created_at")]
        for n, slug in enumerate(slugs[live:]):
            c.execute(
                "INSERT OR REPLACE INTO registry_metadata VALUES(?,?)",
                ("last-stop:" + slug, encode({"attempt_id": runs[n % len(runs)], "at": H.now()})),
            )


def page(api, monkeypatch, path, token="ana-test"):
    """One status page as `token` sees it, without its clock fields, and the statements it took."""
    import contextlib
    from backend.tests.helpers.api import headers

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
