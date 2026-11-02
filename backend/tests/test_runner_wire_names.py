"""A runner in the release window reads the names the wire had before the column renames (backend/runner_wire.py)."""
from backend import runner_wire as RW


def test_a_message_and_a_conversation_keep_their_old_time_names_beside_the_new_ones():
    m = {"id": "m1", "created_at": "t1"}
    RW.message(None, m, as_read=True)
    assert m["created"] == "t1" and m["created_at"] == "t1" and m["refs_json"] == "{}"
    assert RW._times("conversations", {"created_at": "t2"}) == {"created_at": "t2", "created": "t2"}


def test_a_task_and_a_worktree_link_go_out_under_their_old_names():
    t = RW.task({"id": "t1", "labels": "[]", "created_at": "a", "updated_at": "b"})
    assert t == {"id": "t1", "labels_json": "[]", "created": "a", "updated": "b"}
    link = RW.worktree({"id": "l1", "detail": "{}", "updated_at": "u"})
    assert link["detail_json"] == "{}" and link["updated"] == "u"     # runner-rs worktrees.rs reads row["updated"]


def test_a_worktree_link_keeps_its_owner_for_a_runner_that_reads_the_old_name():
    assert RW.worktree({"id": "l1", "detail": "{}", "owner_id": "bot:ops"})["owner"] == "bot:ops"


def test_an_assignment_adds_its_config_text_and_old_time_names():
    assert RW.assignment({"config": "{}", "updated_at": "u"}) == {"config_json": "{}", "updated": "u"}


def test_an_old_name_the_row_already_has_is_never_overwritten():
    assert RW._times("messages", {"created_at": "new", "created": "own"})["created"] == "own"
