"""Evidence on a requirement: the attachment records the digest it was attached as and the ticket's work version, and an
attach at a stage the ticket is not at is refused."""

import hashlib
from types import SimpleNamespace

import pytest

from backend import hubdb as H
from backend import pipeline_requirements as R
from backend import requirement_attachments as A
from backend.store import Problem

from .test_pipeline_foundation import ANA, boot, hub, step_id


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
