"""Whether a person approved an exact file version (backend/file_reviews.py, `hub file approved`): an OK on an ask
bound to the version pinned when it was asked approves it, a send back asks for changes, a newer verdict decides, and an
answer taken back, a plain answer, a closed task or a giver who left counts for nothing. On a store with no app."""

import pytest

from backend import file_reviews as F
from backend import task_asks
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
    # Whether the giver may still open the file is files.py's (test_task_asks_routes checks it through the routes):
    # here, everyone may.
    from types import SimpleNamespace

    from backend import files

    monkeypatch.setattr(F, "AUTH", SimpleNamespace(identity_for_actor=lambda conn, actor: Identity(actor, "human")))
    monkeypatch.setattr(files, "file_visible", lambda *a: True)
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


def _ask(c, subject, by=VIDEO):
    """A bound OK of a file, asked as a bot's POST /api/v2/tasks/{id}/oks asks it: the version is pinned now."""
    task = H.task_create(c, ANA, "Cut the intro", "Make the intro cut.", VIDEO, lint=False)
    asked = task_asks.ask_ok(
        c, by, ANA, "Is this cut ready to post?", shows=[subject], subject=subject, content={}, task_id=task["id"]
    )
    return task["id"], asked["requirement_id"]


def _ok(c, rid, person=ANA):
    """Her own click on the preview she opened (GET /api/v2/oks/{rid}, then the OK route)."""
    shown = task_asks.preview(c, person, rid, own_click=True)["shown_hash"]
    task_asks.give_ok(c, person, rid, shown, own_click=True)


def _verdict(c, *versions, by="human"):
    return F._verdict(c, Identity(ANA, "owner"), list(versions), by=by)


def test_an_ok_from_its_preview_approves_the_pinned_version_only(c):
    fid = _file(c)
    one = _version(c, fid, 1, "d1")
    task, rid = _ask(c, f"file:{fid}@1")
    assert not _verdict(c, one)["approved"]
    _ok(c, rid)
    found = _verdict(c, one)
    assert (found["approved"], found["by"], found["task"], found["question"]) == (True, ANA, task, rid)
    two = _version(c, fid, 2, "d2")
    assert not _verdict(c, two)["approved"]  # a new version is a new review


def test_a_plain_ok_on_an_ask_that_only_shows_the_file_approves_nothing(c):
    """Codex finding 1: an ordinary answer, a token's say, is no file approval."""
    fid = _file(c)
    one = _version(c, fid, 1, "d1")
    task = H.task_create(c, ANA, "Cut the intro", "Make the intro cut.", VIDEO, lint=False)
    asked = H.task_ask(
        c, VIDEO, task["id"], ANA, "Ready?", kind="ok", shows=[f"file:{fid}@1"], seen_version=task["version"]
    )
    H.task_answer(c, ANA, task["id"], asked["requirement_id"], "ok")
    assert _verdict(c, one)["verdict"] is None


def test_a_send_back_asks_for_changes_and_an_answer_taken_back_counts_for_nothing(c):
    fid = _file(c)
    one = _version(c, fid, 1, "d1")
    task, rid = _ask(c, f"file:{fid}@1")
    sent_back = H.task_answer(c, ANA, task, rid, "no", "Trim the opening.")
    found = _verdict(c, one)
    assert (found["approved"], found["verdict"]) == (False, "changes")
    H.task_answer_undo(c, ANA, sent_back["answer_id"])
    assert _verdict(c, one)["verdict"] is None


@pytest.mark.parametrize("ref", ["file:f1", "file:cuts/intro.md"])
def test_a_ref_with_no_version_is_pinned_when_asked_and_a_later_version_is_never_approved(c, ref):
    """Codex finding 2: Ana opens version 1; the bot publishes version 2 before she clicks. Her OK is version 1's."""
    fid = _file(c)
    one = _version(c, fid, 1, "d1", at=H.shift(H.now(), seconds=-60))
    task, rid = _ask(c, ref)
    with_grant = task_asks.grant(c, rid)
    assert with_grant["subject"] == f"file:{fid}@1" and with_grant["content"]["digest"] == "d1"
    two = _version(c, fid, 2, "d2", at=H.shift(H.now(), seconds=-30))
    _ok(c, rid)
    assert _verdict(c, one)["approved"] and not _verdict(c, two)["approved"]


def test_a_path_ref_is_the_asking_bots_own_file(c):
    fid = _file(c)
    _version(c, fid, 1, "d1", at=H.shift(H.now(), seconds=-30))
    with pytest.raises(H.Refused):
        _ask(c, "file:cuts/intro.md", by="bot:editor")


def test_an_ok_on_a_closed_task_or_from_someone_who_left_approves_nothing(c):
    fid = _file(c)
    one = _version(c, fid, 1, "d1")
    task, rid = _ask(c, f"file:{fid}@1")
    _ok(c, rid)
    assert _verdict(c, one)["approved"]
    c.execute("SAVEPOINT gone")
    X.left(c, ANA)
    assert _verdict(c, one)["verdict"] is None
    c.execute("ROLLBACK TO gone")
    c.execute("RELEASE gone")
    assert _verdict(c, one)["approved"]
    H.task_close(c, ANA, task, "Not needed.")
    assert _verdict(c, one)["verdict"] is None


def test_only_the_verdicts_by_names_count(c):
    fid = _file(c)
    one = _version(c, fid, 1, "d1")
    _, rid = _ask(c, f"file:{fid}@1")
    _ok(c, rid)
    assert _verdict(c, one, by="any")["approved"]
    assert _verdict(c, one, by="bot:editor")["verdict"] is None
