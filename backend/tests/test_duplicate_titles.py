"""An unfinished task's title is taken whatever its case and spacing (hubdb.title_key): creating or renaming another
task between the same requester and owner, under the same parent, to that title is refused; the Task Manager and the
stale review find the same pairs. Synthetic fixtures."""

import pytest

from backend import task_manager as TM
from backend.store import H

ANA, OPS = "human:ana", "bot:ops"
TWINS = ("fix login", "Fix  login", " Fix login ", "FIX\tlogin")


@pytest.fixture
def conn(api):
    with api.app.state.store.transaction() as c:
        yield c


from backend.tests.test_tasks_board import api  # noqa: E402,F401


def test_the_same_title_in_other_case_or_spacing_is_refused_on_create(conn):
    first = H.task_create(conn, ANA, "Fix login", "x", OPS)
    for title in TWINS:
        with pytest.raises(H.Refused) as refused:
            H.task_create(conn, ANA, title, "x", OPS)
        assert refused.value.rule == "duplicate" and first["id"] in str(refused.value)
    assert H.task(conn, H.task_create(conn, ANA, "Fix logins", "x", OPS)["id"])["title"] == "Fix logins"


def test_the_same_title_in_other_case_or_spacing_is_refused_on_rename(conn):
    H.task_create(conn, ANA, "Fix login", "x", OPS)
    other = H.task_create(conn, ANA, "Fix signup", "x", OPS)
    for title in TWINS:
        with pytest.raises(H.Refused) as refused:
            H.task_update(conn, ANA, other["id"], title=title)
        assert refused.value.rule == "duplicate"
    # Its own title in another case is a rename, not a twin; the stored title is kept as typed.
    H.task_update(conn, ANA, other["id"], title="fix  SIGNUP")
    assert H.task(conn, other["id"])["title"] == "fix  SIGNUP"


def test_a_finished_twin_or_one_under_another_parent_does_not_block(conn):
    done = H.task_create(conn, ANA, "Fix login", "x", OPS)
    H.task_update(conn, OPS, done["id"], status="done", note="Fixed.")
    again = H.task_create(conn, ANA, "fix login", "x", OPS)
    parent = H.task_create(conn, ANA, "Plan the release", "x", OPS)
    child = H.task_create(conn, ANA, "FIX LOGIN", "x", OPS, parent_id=parent["id"])
    assert again["id"] != child["id"]


def test_the_task_manager_pairs_twins_by_the_same_rule(conn):
    first = H.task_create(conn, ANA, "Renew the domain", "x", OPS, private=False)
    # Made before this rule, or by a path that skips it: a twin that differs by inner spacing and case.
    twin = H.task_create(conn, ANA, "Renew the domain later", "x", OPS, private=False)
    conn.execute("UPDATE tasks SET title='renew  THE domain', created_at=? WHERE id=?", (H.shift(H.now(), minutes=1), twin["id"]))
    H.task_create(conn, ANA, "Renew the lease", "x", OPS, private=False)
    found = [i for i in TM._duplicates(conn)]
    assert len(found) == 1 and first["id"] in found[0]["why"] and found[0]["task"] == twin["id"]
