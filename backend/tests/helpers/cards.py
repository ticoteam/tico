"""Helpers that other tests share, moved out of test_cards.py so they import no test file."""

import uuid
from backend.store import H
from backend.tests.helpers.api import get, headers


def place(api, name, token="ana-test", expected=200):
    return get(api, "places/" + name, token, expected)


def page(api, bot, title="Weekly numbers", task=None):
    """A page straight into the database: a bot-wide file, or a task's."""
    fid = "file-" + uuid.uuid4().hex[:24]
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,task_id,first_activity_at,"
            "last_activity_at) VALUES(?,?,?,?,?,'file','text/html','tico_blob',?,?,?)",
            (fid, bot, "task:" + task if task else "bot", fid, title, task, H.now(), H.now()),
        )
        c.execute("INSERT INTO pages(file_id,created_at,updated_at) VALUES(?,?,?)", (fid, H.now(), H.now()))
    return fid


def task(api, owner, title, private=False, **columns):
    with api.app.state.store.transaction() as c:
        tid = H.task_create(c, "human:ana", title, "Details.", owner, lint=False, private=private)["id"]
        for name, value in columns.items():
            c.execute(f"UPDATE tasks SET {name}=? WHERE id=?", (value, tid))
    return tid


def delete(api, path, token="ana-test", expected=200):
    r = api.delete("/api/v2/" + path, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def patch(api, path, body, token="ana-test", expected=200):
    r = api.patch("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()
