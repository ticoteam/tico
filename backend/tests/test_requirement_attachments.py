"""Evidence on a requirement: the attachment records the digest it was attached as and the ticket's work version, and an
attach at a stage the ticket is not at is refused."""

import hashlib
from types import SimpleNamespace

import pytest

from backend import files as files_module
from backend import hubdb as H
from backend import pipeline_engine
from backend import pipeline_requirements as R
from backend import requirement_attachments as A
from backend import task_privacy as privacy
from backend.store import Problem

from .test_pipeline_foundation import ANA, T, boot, hub, insert, step_id


class Auth:
    """Every ticket is readable: the privacy rule has its own check in the reviewer's read."""

    def task(self, conn, who, task_id):
        return None


def _ticket(c, stage_name="Review"):
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    stage = step_id(c, dev["id"], stage_name)
    task = H.task_create(c, ANA, "Ship the page", "", ANA, type=dev["id"], step="Review", lint=False)
    return dev, stage, task


def test_an_attachment_records_its_digest_and_the_work_version_and_a_work_requirement_bumps_it(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev, stage, task = _ticket(c)
    req = R.add(
        c,
        ANA,
        stage,
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"], "is_work": True},
            "reviewed_by": "nobody",
        },
    )
    who = SimpleNamespace(actor=ANA)
    url = "https://example.com/launch"

    result = A.attach(c, Auth(), who, task["id"], req["id"], {"source": "link", "url": url})

    assert result["digest"] == hashlib.sha256(url.encode()).hexdigest()
    assert result["work_version"] == 1  # the attach is work, so the ticket's work revision moved to 1
    assert result["attached_by"] == ANA
    assert result["reviewed_by"] == "nobody"


def test_an_attachment_on_a_requirement_of_another_stage_is_refused(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev, _, task = _ticket(c)
    other = step_id(c, dev["id"], "Done")
    req = R.add(
        c,
        ANA,
        other,
        {"name": "Live link", "kind": "attachment", "needs": {"formats": ["link"]}, "reviewed_by": "nobody"},
    )

    with pytest.raises(Problem) as refused:
        A.attach(
            c, Auth(), SimpleNamespace(actor=ANA), task["id"], req["id"], {"source": "link", "url": "https://x.example"}
        )
    assert refused.value.status == 409


def _person_reviewed(c, stage, reviewer):
    return R.add(
        c,
        ANA,
        stage,
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "person",
            "reviewer_id": reviewer,
        },
    )


def test_an_attachment_the_requirements_reviewer_cannot_read_is_refused(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    _, stage, task = _ticket(c)
    c.execute("UPDATE tasks SET private=1, owner_id=?, requester_id=?, readers=NULL WHERE id=?", (ANA, ANA, task["id"]))
    req = _person_reviewed(c, stage, "human:ben")
    body = {"source": "link", "url": "https://x.example"}

    with pytest.raises(Problem) as refused:
        A.attach(c, Auth(), SimpleNamespace(actor=ANA), task["id"], req["id"], body)
    assert refused.value.status == 403
    assert c.execute("SELECT COUNT(*) FROM requirement_attachments").fetchone()[0] == 0

    # Once the reviewer can read the ticket, the same attach goes through.
    c.execute("UPDATE tasks SET readers=? WHERE id=?", ('["human:ben"]', task["id"]))
    assert A.attach(c, Auth(), SimpleNamespace(actor=ANA), task["id"], req["id"], body)["attached_by"] == ANA


def test_a_bot_cannot_attach_to_a_requirement_on_a_ticket_it_cannot_edit(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    _, stage, task = _ticket(c)
    req = R.add(
        c,
        ANA,
        stage,
        {"name": "Live link", "kind": "attachment", "needs": {"formats": ["link"]}, "reviewed_by": "nobody"},
    )
    body = {"source": "link", "url": "https://x.example"}

    class NoAccess(Auth):
        def task(self, conn, who, task_id):
            raise Problem("not_found", "No such task", 404)

    with pytest.raises(Problem) as hidden:  # a ticket the bot cannot read is a 404, as for every other route
        A.attach(c, NoAccess(), SimpleNamespace(actor="bot:ghost"), task["id"], req["id"], body)
    assert hidden.value.status == 404
    with pytest.raises(H.Refused):  # a bot hubdb does not know cannot write at all
        A.attach(c, Auth(), SimpleNamespace(actor="bot:ghost"), task["id"], req["id"], body)
    assert c.execute("SELECT COUNT(*) FROM requirement_attachments").fetchone()[0] == 0


def test_a_bot_that_only_reads_the_type_cannot_attach_evidence_and_one_that_works_it_can(tmp_path):
    """Attaching changes the ticket, so it takes the rights a change takes: a bot the type lets only read it may not."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c,
        ANA,
        "Dev",
        [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}],
        mover=True,
        bots="read",
    )
    stage = step_id(c, dev["id"], "Review")
    task = H.task_create(c, ANA, "Ship the page", "", ANA, type=dev["id"], step="Review", lint=False)
    insert(c, "bots", id="reader", slug="reader", display_name="Reader", state="active", created_at=T)
    req = R.add(
        c,
        ANA,
        stage,
        {"name": "Live link", "kind": "attachment", "needs": {"formats": ["link"]}, "reviewed_by": "nobody"},
    )
    body = {"source": "link", "url": "https://x.example"}
    reader = SimpleNamespace(actor="bot:reader")

    with pytest.raises(Problem) as refused:
        A.attach(c, Auth(), reader, task["id"], req["id"], body)
    assert refused.value.status == 403
    assert c.execute("SELECT COUNT(*) FROM requirement_attachments").fetchone()[0] == 0

    c.execute("UPDATE task_types SET bots='work' WHERE id=?", (dev["id"],))
    assert A.attach(c, Auth(), reader, task["id"], req["id"], body)["attached_by"] == "bot:reader"


class Reviewing(Auth):
    """Reads the reviewer's identity from its name, as the auth layer does for a person on the roster."""

    def identity_for_actor(self, conn, actor):
        return SimpleNamespace(actor=actor, role="human")


def test_evidence_the_reviewer_cannot_read_is_refused_for_a_file_and_for_a_blob(tmp_path, monkeypatch):
    """A file or blob the reviewer may not read is refused (403), and one the caller may not read is not found (404)."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    _, stage, task = _ticket(c)
    insert(c, "bots", id="coo", slug="coo", display_name="COO", state="active", created_at=T)
    digest = "d" * 64
    insert(
        c,
        "blobs",
        id="blob-1",
        owner=ANA,
        digest=digest,
        size=3,
        name="proof.pdf",
        content_type="application/pdf",
        created_at=T,
    )
    insert(
        c,
        "bot_files",
        id="file-1",
        bot_id="coo",
        scope="task:" + task["id"],
        identity="proof",
        title="Proof",
        kind="file",
        mime="application/pdf",
        locator="proof",
        current_version=1,
        state="ready",
        task_id=task["id"],
        first_activity_at=T,
        last_activity_at=T,
        archived=0,
    )
    insert(
        c,
        "bot_file_versions",
        file_id="file-1",
        version=1,
        blob_id="blob-1",
        digest=digest,
        size=3,
        name="proof.pdf",
        mime="application/pdf",
        actor="bot:coo",
        created_at=T,
    )
    file_req = R.add(
        c,
        ANA,
        stage,
        {
            "name": "Proof file",
            "kind": "attachment",
            "needs": {"formats": ["page"]},
            "reviewed_by": "person",
            "reviewer_id": "human:ben",
        },
    )
    blob_req = R.add(
        c,
        ANA,
        stage,
        {
            "name": "Proof blob",
            "kind": "attachment",
            "needs": {"formats": ["page"]},
            "reviewed_by": "person",
            "reviewer_id": "human:ben",
        },
    )
    caller = SimpleNamespace(actor=ANA)
    readable = {"caller": True, "reviewer": False}

    def file_visible(conn, auth, who, row, cache):
        return who.actor == ANA or readable["reviewer"]

    def blob_readable(conn, actor, blob_id, memo=None):
        return actor == ANA or readable["reviewer"]

    monkeypatch.setattr(files_module, "file_visible", file_visible)
    monkeypatch.setattr(privacy, "blob_readable", blob_readable)
    auth = Reviewing()

    for requirement, body in (
        (file_req, {"source": "file", "file_id": "file-1"}),
        (blob_req, {"source": "blob", "blob_id": "blob-1"}),
    ):
        with pytest.raises(Problem) as refused:
            A.attach(c, auth, caller, task["id"], requirement["id"], body)
        assert refused.value.status == 403, requirement["name"]
    assert c.execute("SELECT COUNT(*) FROM requirement_attachments").fetchone()[0] == 0

    readable["reviewer"] = True  # once the reviewer can read it, the same attach goes through
    assert (
        A.attach(c, auth, caller, task["id"], file_req["id"], {"source": "file", "file_id": "file-1"})["attached_by"]
        == ANA
    )
    assert (
        A.attach(c, auth, caller, task["id"], blob_req["id"], {"source": "blob", "blob_id": "blob-1"})["attached_by"]
        == ANA
    )

    readable["caller"] = False  # a file the caller cannot read is not found to them
    monkeypatch.setattr(files_module, "file_visible", lambda conn, auth, who, row, cache: False)
    with pytest.raises(Problem) as hidden:
        A.attach(c, auth, caller, task["id"], file_req["id"], {"source": "file", "file_id": "file-1"})
    assert hidden.value.status == 404


def test_a_file_attachment_links_to_the_version_it_pinned_and_names_its_media(tmp_path):
    """A reviewer sees what was attached: the pinned version's address, its name, and whether it is an image, a page or
    a file. A later version of the same file leaves the pinned address alone."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    c.execute("PRAGMA foreign_keys=OFF")  # the file row is not what this test reads; only its version rows are
    c.execute(
        "INSERT INTO bot_file_versions (file_id, version, blob_id, digest, size, name, mime, actor, created_at) "
        "VALUES ('f1', 1, 'b1', 'd1', 3, 'shot.png', 'image/png', ?, ?)",
        (ANA, H.now()),
    )
    c.execute(
        "INSERT INTO bot_file_versions (file_id, version, blob_id, digest, size, name, mime, actor, created_at) "
        "VALUES ('f1', 2, 'b2', 'd2', 3, 'shot.png', 'image/png', ?, ?)",
        (ANA, H.now()),
    )
    pinned = {"id": "a1", "source": "file", "file_id": "f1", "file_version": 1, "blob_id": None, "url": None}
    [view] = A.views(c, [pinned])

    assert view["format"] == "image"
    assert view["name"] == "shot.png"
    assert view["href"] == "/api/v2/files/f1/versions/1"


def test_a_link_and_a_blob_show_their_own_address_and_media(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    c.execute(
        "INSERT INTO blobs (id, owner, digest, size, name, content_type, created_at) "
        "VALUES ('b9', ?, 'd', 4, 'launch.pdf', 'application/pdf', ?)",
        (ANA, H.now()),
    )
    link = {"id": "a1", "source": "link", "url": "https://example.com/launch"}
    blob = {"id": "a2", "source": "blob", "blob_id": "b9", "file_id": None, "file_version": None, "url": None}
    link_view, blob_view = A.views(c, [link, blob])

    assert (link_view["format"], link_view["href"]) == ("link", "https://example.com/launch")
    assert (blob_view["format"], blob_view["name"], blob_view["href"]) == ("page", "launch.pdf", "/api/v2/files/b9")


def test_a_ticket_lists_each_requirements_attachments_with_their_addresses_in_one_read(tmp_path):
    """The ticket's requirement list carries each requirement's newest attachments, so the page makes no request per
    attachment requirement, and the list is capped."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev, stage, task = _ticket(c)
    req = R.add(
        c,
        ANA,
        stage,
        {"name": "Live link", "kind": "attachment", "needs": {"formats": ["link"]}, "reviewed_by": "nobody"},
    )
    who = SimpleNamespace(actor=ANA)
    for n in range(A.ATTACH_LIMIT + 2):
        A.attach(c, Auth(), who, task["id"], req["id"], {"source": "link", "url": f"https://example.com/{n}"})

    [entry] = pipeline_engine.detail(c, task["id"])

    assert len(entry["attachments"]) == A.ATTACH_LIMIT
    assert entry["attachments"][0]["href"] == f"https://example.com/{A.ATTACH_LIMIT + 1}"
    assert entry["attachments"][0]["format"] == "link"
