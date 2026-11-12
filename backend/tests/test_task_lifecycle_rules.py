"""Closing, reopening, privacy and ownership rules on a task: who may end it, which times it keeps, who it reaches."""

import pytest

from backend.store import H
from backend.tests.helpers import engine as X

ANA, BEN, CARA = H.human_actor("ana"), H.human_actor("ben"), H.human_actor("cara")
CMO, SEO, LEGAL = H.bot_actor("cmo"), H.bot_actor("seo"), H.bot_actor("legal")
BODY = "Do the work described here."


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        for slug in ("cmo", "seo", "legal"):
            X.bot(conn, slug)
        # Ana leads and Ben is on the product team (both mover teams); Cara is in sales and only reads.
        conn.execute("UPDATE humans SET teams=? WHERE id='ana'", ('["leadership"]',))
        conn.execute("UPDATE humans SET teams=? WHERE id='ben'", ('["product"]',))
        conn.execute("UPDATE humans SET teams=? WHERE id='cara'", ('["sales"]',))
        yield conn


def _make(c, actor, title, owner, **kw):
    return H.task_create(c, actor, title, BODY, owner, lint=False, **kw)


def _version(c, task_id):
    return H.task(c, task_id)["version"]


def _refused(rule):
    return pytest.raises(H.Refused, match=rule)


# ----------------------------------------------------------------------------- leaving closed
def test_reopening_a_closed_task_drops_its_close_time_and_done_after_keeps_when_it_was_done(c):
    t = _make(c, ANA, "Draft the launch post", CMO)
    H.task_close(c, ANA, t["id"], "Not now")
    H.task_move(c, ANA, t["id"], H.TASK_REOPEN, "It went out after all")
    H.task_done(c, ANA, t["id"], "It went out after all")
    row = H.task(c, t["id"])
    assert (row["status"], row["closed_at"], row["closed_by_id"]) == ("done", None, None)
    assert row["done_at"] is not None


def test_a_done_task_is_reopened_before_it_closes_and_keeps_when_it_was_done(c):
    t = _make(c, ANA, "Draft the hiring post", CMO)
    H.task_done(c, CMO, t["id"], "Drafted.", _version(c, t["id"]))
    done_at = H.task(c, t["id"])["done_at"]
    assert done_at is not None
    with _refused("task_ended"):
        H.task_close(c, ANA, t["id"], "Not running it.")
    row = H.task(c, t["id"])
    assert (row["status"], row["done_at"], row["closed_by_id"]) == ("done", done_at, None)


# ----------------------------------------------------------------------------- who closes
def test_a_person_who_can_only_read_a_task_cannot_close_it(c):
    t = _make(c, ANA, "Draft the launch post", CMO)
    with _refused("This task is worked by"):
        H.task_close(c, CARA, t["id"], "Not needed")
    assert H.task(c, t["id"])["status"] == "open"


def test_requester_owner_mover_and_a_party_above_close(c):
    for closer in (ANA, BEN):  # the requester; a person on a mover team
        t = _make(c, ANA, f"Draft the post for {closer}", CMO)
        assert H.task_close(c, closer, t["id"], "Not now.")["status"] == "closed"
    mine = _make(c, ANA, "Review the launch plan", CARA)
    assert H.task_close(c, CARA, mine["id"], "Not now.")["status"] == "closed"  # its owner
    parent = _make(c, CARA, "Plan the sales launch", CMO)
    child = _make(c, CMO, "Draft the sales email", SEO, parent_id=parent["id"])
    assert H.task_close(c, CARA, child["id"], "Not now.")["status"] == "closed"  # requester of the parent


def test_the_person_a_bots_task_asks_does_not_close_it(c):
    """A bot's task that asks a person is still the bot's: they answer the ask, they do not end the task."""
    t = _make(c, CMO, "Book the launch ads", CMO)
    line = "Approve the launch budget? I'll book the ads after."
    H.task_ask(c, CMO, t["id"], CARA, line, kind="ok", seen_version=_version(c, t["id"]))
    with _refused("This task is worked by"):
        H.task_close(c, CARA, t["id"], "Not now")
    row = H.task(c, t["id"])
    assert (row["status"], row["owner_id"]) == ("open", CMO)


def test_the_caller_says_when_a_person_moves_tasks(c):
    t = _make(c, ANA, "Draft the launch post", CMO)
    assert H.task_close(c, CARA, t["id"], "Not now.", mover=True)["status"] == "closed"


# ----------------------------------------------------------------------------- private parent
def test_making_a_parent_private_takes_every_task_below_it(c):
    parent = _make(c, ANA, "Plan the board pack", CMO)
    child = _make(c, CMO, "Collect the revenue numbers", SEO, parent_id=parent["id"])
    grandchild = _make(c, SEO, "Export the revenue table", LEGAL, parent_id=child["id"])
    H.task_update(c, ANA, parent["id"], private=True)
    for tid in (child["id"], grandchild["id"]):
        row = H.task(c, tid)
        assert H.task_private(c, row)
        assert row["visibility"] == "private"
        assert not H.task_private_readable(c, BEN, row)


def test_the_bot_owning_a_private_parent_hears_its_subtasks_finished(c):
    parent = _make(c, ANA, "Plan the board pack", CMO, private=True)
    child = _make(c, CMO, "Collect the revenue numbers", SEO, parent_id=parent["id"])
    assert H.task_private(c, H.task(c, child["id"]))
    H.task_done(c, SEO, child["id"], "Numbers attached", _version(c, child["id"]))
    told = c.execute(
        "SELECT recipient_id FROM messages WHERE wake_key LIKE ? OR wake_key LIKE ?",
        (f"{child['id']}:%:result", f"{parent['id']}:%:filed:{child['id']}"),
    ).fetchall()
    assert [r[0] for r in told] == [CMO]


# ----------------------------------------------------------------------------- renaming
def test_renaming_to_a_finished_tasks_title_is_allowed_as_creating_one_is(c):
    old = _make(c, ANA, "Draft the launch post", CMO)
    H.task_done(c, CMO, old["id"], "Drafted.", _version(c, old["id"]))
    new = _make(c, ANA, "Draft the post", CMO)
    H.task_update(c, ANA, new["id"], title="Draft the launch post")
    assert H.task(c, new["id"])["title"] == "Draft the launch post"
    live = _make(c, ANA, "Draft the pricing post", CMO)
    with _refused("duplicate|already"):
        H.task_update(c, ANA, new["id"], title=live["title"])


# ----------------------------------------------------------------------------- ownership
def test_the_same_owner_in_another_form_is_no_reassignment(c):
    t = _make(c, ANA, "Draft the launch post", CMO)
    before = c.execute("SELECT count(*) FROM messages WHERE recipient_id=?", (CMO,)).fetchone()[0]
    version = _version(c, t["id"])
    assert H.task_reassign(c, BEN, t["id"], "cmo")["version"] == version
    assert c.execute("SELECT count(*) FROM messages WHERE recipient_id=?", (CMO,)).fetchone()[0] == before
    assert X.events(c, t["id"]).count("reassign") == 0


def test_a_bot_cannot_hand_on_a_task_a_person_closed(c):
    t = _make(c, CMO, "Draft the launch post", SEO)
    H.task_close(c, ANA, t["id"], "Not needed")
    with _refused("task_ended"):
        H.task_reassign(c, CMO, t["id"], "legal", "Legal should take it.", _version(c, t["id"]))
    with _refused("task_needs_person"):
        H.task_move(c, CMO, t["id"], H.TASK_REOPEN, "Legal should take it.", _version(c, t["id"]))
    assert H.task(c, t["id"])["owner_id"] == SEO
    assert X.wakes(c, t["id"], LEGAL) == []
    own = _make(c, CMO, "Draft the pricing post", SEO)
    H.task_close(c, CMO, own["id"], "Not needed.", _version(c, own["id"]))
    H.task_move(c, CMO, own["id"], H.TASK_REOPEN, "Needed after all.", _version(c, own["id"]))
    out = H.task_reassign(c, CMO, own["id"], "legal", "Legal should take it.", _version(c, own["id"]))
    assert out["owner_id"] == LEGAL
