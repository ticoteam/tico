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

    # The Task Manager pairs twins by the same rule, and only when their bodies name the same ticket.
    first = H.task_create(conn, ANA, "Renew the domain", "Renews for TK-AAAA1111.", OPS, private=False)
    # Made before this rule, or by a path that skips it: a twin that differs by inner spacing and case.
    twin = H.task_create(conn, ANA, "Renew the domain later", "Renews for TK-AAAA1111.", OPS, private=False)
    conn.execute(
        "UPDATE tasks SET title='renew  THE domain', created_at=? WHERE id=?", (H.shift(H.now(), minutes=1), twin["id"])
    )
    H.task_create(conn, ANA, "Renew the lease", "x", OPS, private=False)
    found = [i for i in TM._duplicates(conn) if "login" not in i["title"].lower()]
    assert len(found) == 1 and first["id"] in found[0]["why"] and found[0]["task"] == twin["id"]


def make(conn, title, body, requester=ANA, owner=OPS):
    # Filed by the keeper with no duplicate refusal, so only the rule under test decides what is reported.
    return H.task_create(
        conn, H.KEEPER, title, body, owner, private=False, deduplicate=False, requester_actor=requester
    )


def duplicates_of(conn, title):
    return [i for i in TM._duplicates(conn) if i["title"] == title]


def test_a_same_title_pair_is_a_duplicate_only_when_the_bodies_name_the_same_ticket(conn):
    # Different tickets: no item, so no relate or close line for either task.
    make(conn, "Queue idle", "Work stalls, see TK-AAAA1111.")
    make(conn, "Queue idle", "Work stalls, see TK-BBBB2222.")
    assert duplicates_of(conn, "Queue idle") == []

    # The same ticket: one item, for the later task, with the relate and close lines.
    first = make(conn, "Mail held", "Held reply for TK-CCCC3333.")
    later = make(conn, "Mail held", "Held reply, TK-CCCC3333 again.")
    found = duplicates_of(conn, "Mail held")
    assert len(found) == 1 and found[0]["task"] == later["id"] and first["id"] in found[0]["why"]
    assert f"hub task relate {later['id']} {first['id']} --kind duplicate_of" in found[0]["fix"]
    assert "hub task close" in found[0]["fix"]

    # One names a ticket and the other names none: not shown to be the same work, so no item.
    make(conn, "Hold the stock", "Hold for TK-DDDD4444.")
    make(conn, "Hold the stock", "Hold the stock as well.")
    assert duplicates_of(conn, "Hold the stock") == []

    # No ticket, but the same inbound message id: one item.
    make(conn, "Reply pending", "From inbound message id msg_7f3a9c21 about the refund.")
    later = make(conn, "Reply pending", "Same mail, inbound message id msg_7f3a9c21.")
    assert [i["task"] for i in duplicates_of(conn, "Reply pending")] == [later["id"]]

    # Different tickets that happen to share a message id: no item.
    make(conn, "Refund check", "Refund for TK-EEEE5555, inbound message id msg_9b1d0e42.")
    make(conn, "Refund check", "Refund for TK-FFFF6666, inbound message id msg_9b1d0e42.")
    assert duplicates_of(conn, "Refund check") == []

    # Neither names a ticket or a message id: no item.
    make(conn, "Check stock", "Count the shelves.")
    make(conn, "Check stock", "Count the shelves again.")
    assert duplicates_of(conn, "Check stock") == []

    # The same ticket with a different requester or owner: no item, as before.
    make(conn, "Ship the kit", "For TK-GGGG7777.")
    make(conn, "Ship the kit", "For TK-GGGG7777.", requester="human:ben")
    make(conn, "Ship the kit", "For TK-GGGG7777.", owner="bot:cmo")
    assert duplicates_of(conn, "Ship the kit") == []
