"""The task list reads its page's ids from an index, then each row once: the same rows in the same order as the
whole read, and the same statements however many tasks the company has."""

from backend import task_privacy
from backend.store import H
from backend.tests.helpers.status_reads import page


def seed(api, n, start):
    with api.app.state.store.transaction() as c:
        for k in range(start, start + n):
            H.task_create(
                c,
                "human:ana",
                f"List task {k}",
                "The body of a task. " * 150,
                "bot:ops" if k % 2 else "bot:assistant",
                labels=["area-a"] if k % 3 == 0 else None,
            )


def counted(api, monkeypatch):
    """The page's statements, with the roster cache empty so that every read of it is counted."""
    api.app.state.store.roster_rows.clear()
    body, count = page(api, monkeypatch, "/api/v2/tasks?limit=500")
    return body, count


def test_a_task_page_is_the_same_rows_in_the_same_order_and_costs_the_same_statements_at_any_size(api, monkeypatch):
    seed(api, 10, 0)
    counted(api, monkeypatch)  # the first read of a company writes its roster rows
    _, small = counted(api, monkeypatch)
    seed(api, 90, 10)
    body, big = counted(api, monkeypatch)
    assert big == small
    store = api.app.state.store
    with store.read_transaction() as c:
        whole = [row["id"] for row in H.tasks(c, limit=None)]  # the unpaged read, which sorts whole rows
    assert len(whole) == 100 and [row["id"] for row in body["tasks"]] == whole[:500]
