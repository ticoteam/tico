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


def test_a_list_row_carries_a_tag_cut_down_but_hides_one_naming_a_page_the_reader_cannot_open(api):
    """A list row's tag is its id, key, label, kind, owner and metadata. A tag whose notes name a page the reader may
    not open (its notes are the deleted page's link) is hidden whole, on the list as on the task's own read: it is
    checked before it is cut down."""
    from backend.tests.helpers.api import headers

    gone = "file-" + "d" * 24
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Tagged task", None, "bot:ops")
        now = H.now()
        c.execute(
            "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,state,first_activity_at,"
            "last_activity_at) VALUES(?,'ops','bot','gone','Gone','page','text/html','tico_blob','deleted',?,?)",
            (gone, now, now),
        )
        c.execute("INSERT INTO pages(file_id,deleted_at,created_at,updated_at) VALUES(?,?,?,?)", (gone, now, now, now))
        for tag, markdown in (("tag-plain", "Ship it"), ("tag-gone", f"/api/v2/files/{gone}")):
            c.execute(
                "INSERT INTO tags(id,key,label,metadata,markdown,owner,created_at,updated_at) "
                "VALUES(?,?,?,'{\"area\":\"web\"}',?,'human:ben',?,?)",
                (tag, tag, tag.title(), markdown, now, now),
            )
            c.execute("INSERT INTO task_tags(task_id,tag_id) VALUES(?,?)", (task["id"], tag))
    listed = api.get("/api/v2/tasks?ids=" + task["id"], headers=headers()).json()["tasks"][0]["tags"]
    whole = api.get("/api/v2/tasks/" + task["id"], headers=headers()).json()["task"]["tags"]
    assert listed[0] == {
        "id": "tag-plain",
        "key": "tag-plain",
        "label": "Tag-Plain",
        "kind": "",
        "owner": "human:ben",
        "owner_name": "Ben",
        "metadata": {"area": "web"},
    }
    assert whole[0]["markdown"] == "Ship it" and whole[0]["created_at"]  # the task's own read keeps the whole tag
    assert listed[1] == whole[1] == {"removed": True, "label": "A deleted page"}
