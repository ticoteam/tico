"""An unfinished task's title is taken whatever its case and spacing (hubdb.title_key): creating or renaming another
task between the same requester and owner, under the same parent, to that title is refused; the Task Manager and the
stale review find the same pairs. Synthetic fixtures."""

import pytest

from backend import task_manager as TM
from backend.store import H

ANA, OPS = "human:ana", "bot:ops"


def twins(word):
    return (f"{word} login", f"{word.capitalize()}  login", f" {word.capitalize()} login ", f"{word.upper()}\tlogin")


@pytest.fixture
def conn(api):
    with api.app.state.store.transaction() as c:
        yield c


from backend.tests.test_tasks_board import api  # noqa: E402,F401


def test_unfinished_titles_are_taken_whatever_their_case_and_spacing(conn):
    # Create: every twin is refused, a different title is not.
    first = H.task_create(conn, ANA, "Fix login", "x", OPS)
    for title in twins("fix"):
        with pytest.raises(H.Refused) as refused:
            H.task_create(conn, ANA, title, "x", OPS)
        assert refused.value.rule == "duplicate" and first["id"] in str(refused.value)
    assert H.task(conn, H.task_create(conn, ANA, "Fix logins", "x", OPS)["id"])["title"] == "Fix logins"

    # Rename: every twin is refused; its own title in another case is a rename, kept as typed.
    H.task_create(conn, ANA, "Mend login", "x", OPS)
    other = H.task_create(conn, ANA, "Mend signup", "x", OPS)
    for title in twins("mend"):
        with pytest.raises(H.Refused) as refused:
            H.task_update(conn, ANA, other["id"], title=title)
        assert refused.value.rule == "duplicate"
    H.task_update(conn, ANA, other["id"], title="mend  SIGNUP")
    assert H.task(conn, other["id"])["title"] == "mend  SIGNUP"

    # A finished twin or one under another parent does not block.
    done = H.task_create(conn, ANA, "Patch login", "x", OPS)
    H.task_update(conn, OPS, done["id"], status="done", note="Fixed.")
    again = H.task_create(conn, ANA, "patch login", "x", OPS)
    parent = H.task_create(conn, ANA, "Plan the release", "x", OPS)
    child = H.task_create(conn, ANA, "PATCH LOGIN", "x", OPS, parent_id=parent["id"])
    assert again["id"] != child["id"]

    # The Task Manager pairs twins by the same rule.
    first = H.task_create(conn, ANA, "Renew the domain", "x", OPS, private=False)
    # Made before this rule, or by a path that skips it: a twin that differs by inner spacing and case.
    twin = H.task_create(conn, ANA, "Renew the domain later", "x", OPS, private=False)
    conn.execute("UPDATE tasks SET title='renew  THE domain', created_at=? WHERE id=?", (H.shift(H.now(), minutes=1), twin["id"]))
    H.task_create(conn, ANA, "Renew the lease", "x", OPS, private=False)
    found = [i for i in TM._duplicates(conn) if "login" not in i["title"].lower()]
    assert len(found) == 1 and first["id"] in found[0]["why"] and found[0]["task"] == twin["id"]
