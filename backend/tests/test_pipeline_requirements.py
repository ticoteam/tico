"""Stage requirements and pipeline settings: only the pipeline's owner and admins change them, every change keeps the
version it replaces, a no-op edit adds nothing, and a prefix is one or two letters and unique among pipelines."""

import pytest

from backend import hubdb as H
from backend import pipeline_requirements as R
from backend.store import Problem

from .test_pipeline_foundation import ANA, boot, hub, step_id

BEN = "human:ben"


def _review_stage(c, name="Pipeline"):
    dev = H.type_create(
        c, ANA, name, [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    return dev, step_id(c, dev["id"], "Review")


def _body(**changes):
    body = {
        "name": "Shows the page live",
        "kind": "attachment",
        "needs": {"formats": ["link"]},
        "reviewed_by": "bot",
        "rubric": "The link opens the page",
    }
    body.update(changes)
    return body


def test_only_the_owner_changes_rules_and_each_change_keeps_the_version_it_replaces(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev, stage = _review_stage(c)
    req = R.add(c, ANA, stage, _body())
    assert req["version"] == 1

    with pytest.raises(Problem) as refused:
        R.edit(c, BEN, req["id"], _body(rubric="Anything goes"))
    assert refused.value.status == 403

    unchanged = R.edit(c, ANA, req["id"], _body())
    assert unchanged["version"] == 1  # an edit that changes nothing adds no version

    edited = R.edit(c, ANA, req["id"], _body(rubric="The link opens the live page"))
    assert edited["version"] == 2
    versions = R.versions(c, req["id"])
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["definition"]["rubric"] == "The link opens the page"  # the version it replaced is still there


def test_a_prefix_is_one_or_two_letters_and_unique_among_pipelines(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    first, _ = _review_stage(c, "Dev")
    second, _ = _review_stage(c, "Content")

    assert R.set_prefix(c, ANA, first["id"], "ab") == "AB"
    with pytest.raises(Problem) as too_long:
        R.set_prefix(c, ANA, second["id"], "ABC")
    assert too_long.value.status == 422
    with pytest.raises(Problem) as taken:
        R.set_prefix(c, ANA, second["id"], "AB")
    assert taken.value.status == 409


def test_a_bot_reviewed_attachment_needs_a_rubric_and_a_person_reviewer_is_named(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    _, stage = _review_stage(c)
    with pytest.raises(Problem) as no_rubric:
        R.add(c, ANA, stage, _body(rubric=""))
    assert no_rubric.value.status == 422
    with pytest.raises(Problem) as no_person:
        R.add(c, ANA, stage, _body(reviewed_by="person", rubric=None))
    assert no_person.value.status == 422
