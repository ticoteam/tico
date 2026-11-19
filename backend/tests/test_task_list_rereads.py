"""A task list page reads its rows once: the turn and late columns take them from the page's own read."""

from backend import hubdb
from backend.tests.helpers import engine as X
from backend.tests.helpers.status_reads import page


def test_task_list_page_reads_its_tasks_once_and_answers_the_same(api, monkeypatch):
    # TIDY, Nov 19: GET /api/v2/tasks read its 500 rows, then waiting_on.prime read the same 500 rows again for each
    # row's turn (SELECT * FROM tasks WHERE id IN json_each), 0.7-1.3 s of a busy server. The page's read now serves
    # the engine's one-task reads of them.
    store = api.app.state.store
    with store.transaction() as c:
        X.bot(c, "builder", computer=False)
        X.pipeline(c)
        for k in range(12):
            X.new_task(c, X.ANA, f"Open task {k}", assignee="bot:builder" if k % 2 else X.ANA)
    for path in ("/api/v2/tasks?status=open", "/api/v2/tasks?status=open&sort=finished"):
        page(api, monkeypatch, path)  # warm: the table checks each connection keeps
        kept, statements = page(api, monkeypatch, path)
        reread = [
            sql for sql in statements_of(api, monkeypatch, path) if sql.startswith("SELECT * FROM tasks WHERE id")
        ]
        assert len(kept["tasks"]) == 12 and reread == [], (path, reread)
        with monkeypatch.context() as patched:
            patched.setattr(hubdb, "_TASK_ROW_READS", ())
            plain, more = page(api, monkeypatch, path)
            assert any(
                sql.startswith("SELECT * FROM tasks WHERE id") for sql in statements_of(api, monkeypatch, path)
            ), "the control reads the rows again"
        assert {k: v for k, v in plain.items() if k != "cursor"} == {k: v for k, v in kept.items() if k != "cursor"}
        assert more > statements


def statements_of(api, monkeypatch, path):
    """The statements one read of `path` runs, as text."""
    import contextlib

    from backend.tests.helpers.api import headers
    from backend.tests.helpers.statement_scope import measuring, recorder

    store, seen = api.app.state.store, []
    with monkeypatch.context() as patched:

        def traced(opened=store.read_transaction):
            @contextlib.contextmanager
            def borrowed():
                with opened() as c:
                    c.set_trace_callback(recorder(seen))
                    try:
                        yield c
                    finally:
                        c.set_trace_callback(None)

            return borrowed()

        patched.setattr(store, "read_transaction", traced)
        with measuring(seen):
            assert api.get(path, headers=headers("ana-test")).status_code == 200
    return [" ".join(sql.split()) for sql in seen]
