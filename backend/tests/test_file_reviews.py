"""Whether a person approved an exact file version (backend/file_reviews.py, `hub file approved`): an OK on an ask
that shows the version approves it, a send back asks for changes, a newer verdict decides, and an answer taken back
counts for nothing. On a store with no app."""

import pytest

from backend import file_reviews as F
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers import engine as X

ANA = "human:ana"
VIDEO = "bot:video"


@pytest.fixture
def c(company, monkeypatch):
    # Who may read the task is task_privacy's (the routes track): here, the engine's own rule for a private task.
    monkeypatch.setattr(
        F.privacy, "task_readable", lambda conn, who, row: H.task_private_readable(conn, who.actor, row)
    )
    with company.transaction() as conn:
        X.bot(conn, "video")
        X.bot(conn, "editor")
        yield conn


def _file(c, fid="f1", path="cuts/intro.md"):
    at = H.now()
    c.execute(
        "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,locator,current_version,first_activity_at,"
        "last_activity_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (fid, "video", "bot", path, "Intro cut", "doc", "tico_blob", 0, at, at),
    )
    return fid


def _version(c, fid, number, digest, path="cuts/intro.md", at=None):
    c.execute(
        "INSERT OR IGNORE INTO blobs(id,owner,digest,size,name,content_type,created_at) VALUES(?,?,?,?,?,?,?)",
        ("b" + str(number), VIDEO, digest, 10, "intro.md", "text/markdown", H.now()),
    )
    c.execute(
        "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,repo_path,actor,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (fid, number, "b" + str(number), digest, 10, "intro.md", "text/markdown", path, VIDEO, at or H.now()),
    )
    c.execute("UPDATE bot_files SET current_version=? WHERE id=?", (number, fid))
    return (fid, number, digest)


def _ask(c, shows, by=VIDEO):
    task = H.task_create(c, ANA, "Cut the intro", "Make the intro cut.", VIDEO, lint=False)
    asked = H.task_ask(
        c, by, task["id"], ANA, "Is this cut ready to post?", kind="ok", shows=shows, seen_version=task["version"]
    )
    return task["id"], asked["requirement_id"]


def _verdict(c, *versions, by="human"):
    return F._verdict(c, Identity(ANA, "owner"), list(versions), by=by)


def test_an_ok_on_an_ask_that_shows_a_version_approves_that_version_only(c):
    fid = _file(c)
    one = _version(c, fid, 1, "d1")
    task, rid = _ask(c, [f"file:{fid}@1"])
    assert not _verdict(c, one)["approved"]
    H.task_answer(c, ANA, task, rid, "ok")
    found = _verdict(c, one)
    assert (found["approved"], found["by"], found["task"], found["question"]) == (True, ANA, task, rid)
    two = _version(c, fid, 2, "d2")
    assert not _verdict(c, two)["approved"]  # a new version is a new review


def test_a_send_back_asks_for_changes_and_an_answer_taken_back_counts_for_nothing(c):
    fid = _file(c)
    one = _version(c, fid, 1, "d1")
    task, rid = _ask(c, [f"file:{fid}@1"])
    sent_back = H.task_answer(c, ANA, task, rid, "no", "Trim the opening.")
    found = _verdict(c, one)
    assert (found["approved"], found["verdict"]) == (False, "changes")
    H.task_answer_undo(c, ANA, sent_back["answer_id"])
    assert _verdict(c, one)["verdict"] is None


def test_a_path_ref_names_the_version_that_was_newest_when_the_person_answered(c):
    fid = _file(c)
    one = _version(c, fid, 1, "d1", at=H.shift(H.now(), seconds=-60))
    two = _version(c, fid, 2, "d2", at=H.shift(H.now(), seconds=-30))
    task, rid = _ask(c, ["file:cuts/intro.md"])
    H.task_answer(c, ANA, task, rid, "ok")
    assert _verdict(c, two)["approved"] and not _verdict(c, one)["approved"]


def test_a_path_ref_is_the_asking_bots_own_file(c):
    fid = _file(c)
    one = _version(c, fid, 1, "d1", at=H.shift(H.now(), seconds=-30))
    task = H.task_create(c, ANA, "Review the cut", "Look at the cut.", "bot:editor", lint=False)
    asked = H.task_ask(
        c,
        "bot:editor",
        task["id"],
        ANA,
        "Is the cut ready?",
        kind="ok",
        shows=["file:cuts/intro.md"],
        seen_version=task["version"],
    )
    H.task_answer(c, ANA, task["id"], asked["requirement_id"], "ok")
    assert _verdict(c, one)["verdict"] is None


def test_only_the_verdicts_by_names_count(c):
    fid = _file(c)
    one = _version(c, fid, 1, "d1")
    task, rid = _ask(c, [f"file:{fid}@1"])
    H.task_answer(c, ANA, task, rid, "ok")
    assert _verdict(c, one, by="any")["approved"]
    assert _verdict(c, one, by="bot:editor")["verdict"] is None
