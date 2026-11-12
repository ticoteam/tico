"""The shared base of the Product and Engineering pipelines: their tables, the five kinds' places in the registry, and
the stores every track reads (task_facts, page_verdicts, task_pages) with the fake GitHub the tests and the rig use."""

import importlib
import inspect
import sqlite3

import pytest

from backend import page_verdicts as PV
from backend import pipeline_seats as Seats
from backend.item_sources import github as F
from backend import task_pages as P
from backend import task_requirements as R
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub, GitHubError

MIGRATION = importlib.import_module("backend.migrations.20261110213830_product_engineering_facts")
V3 = importlib.import_module("backend.migrations.20261110010000_pipelines_v3")
REPO = "acme/app"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def test_the_migration_runs_twice_and_gives_effects_their_pr():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    V3.up(conn)
    MIGRATION.up(conn)
    MIGRATION.up(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"pr_facts", "releases", "release_reads", "release_commits", "github_deliveries"} <= tables
    assert {"requirement_pages", "page_verdicts"} <= tables
    effects = {r[1] for r in conn.execute("PRAGMA table_info(effects)")}
    assert {"repo", "number", "head_sha", "next_try_at"} <= effects


def test_a_pr_fact_keeps_the_newest_read(c):
    assert F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a", "checks": []}, 200)
    assert not F.save_pr(c, REPO, 1, {"state": "closed", "head_sha": "a"}, 100)  # an older read began first
    fact = F.pr(c, REPO, 1)
    assert (fact["state"], fact["metadata_version"], fact["checks_state"]) == ("open", 200, "none")
    assert F.pr(c, "ACME/App", 1)["id"] == fact["id"]  # a repo's name in any case


def test_checks_run_from_when_they_were_first_seen_running_and_tico_s_own_never_count(c):
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a", "checks": [{"state": "pending"}]}, 1)
    first = F.pr(c, REPO, 1)
    assert first["checks_state"] == "pending" and first["checks_started_at"] and first["head_seen_at"]
    F.save_pr(c, REPO, 1, {"checks": [{"state": "pending"}, {"state": "success"}]}, 2)
    assert F.pr(c, REPO, 1)["checks_started_at"] == first["checks_started_at"]
    F.save_pr(c, REPO, 1, {"checks": [{"state": "success"}, {"state": "failure", "tico": True}]}, 3)
    done = F.pr(c, REPO, 1)
    assert (done["checks_state"], done["checks_started_at"]) == ("success", None)
    F.save_pr(c, REPO, 1, {"head_sha": "b"}, 4)  # a new commit: its checks are not known yet
    moved = F.pr(c, REPO, 1)
    assert (moved["checks_state"], moved["checks"]) == ("none", [])
    assert moved["head_seen_at"] > first["head_seen_at"]


def test_a_queue_removal_is_saved_once_with_the_check_that_failed(c):
    gh = FakeGitHub()
    gh.open_pr(REPO, 1)
    gh.enqueue(REPO, 1, gh.read_pr(REPO, 1)["head_sha"])
    F.save_pr(c, REPO, 1, gh.read_pr(REPO, 1), 1)
    queued = F.pr(c, REPO, 1)
    assert queued["queue_state"] == "queued" and queued["queued_at"] and queued["removed_at"] is None
    gh.drop(REPO, 1, "Checks failed in the merge queue", check="e2e", url="https://ci/1")
    F.save_pr(c, REPO, 1, gh.read_pr(REPO, 1), 2)
    gone = F.pr(c, REPO, 1)
    assert (gone["queue_state"], gone["queued_at"], gone["failing_check"]) == ("removed", None, "e2e")
    assert gone["removed_reason"] == "Checks failed in the merge queue" and gone["failing_check_url"] == "https://ci/1"
    F.save_pr(c, REPO, 1, gh.read_pr(REPO, 1), 3)  # the same removal read again: one removed_at, so one wake
    assert F.pr(c, REPO, 1)["removed_at"] == gone["removed_at"]


def test_a_task_s_heads_leave_out_a_pr_closed_without_merging(c):
    X.pipeline(c)
    task = X.new_task(c, X.ANA, "Build it")
    for number in (1, 2, 3):
        H.task_link(c, X.ANA, task["id"], f"https://github.com/{REPO}/pull/{number}")
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a"}, 1)
    F.save_pr(c, REPO, 2, {"state": "closed", "head_sha": "b"}, 1)
    assert F.heads(c, task["id"]) == {"acme/app#1": "a", "acme/app#3": None}
    assert [p["number"] for p in F.prs_of(c, task["id"])] == [1, 2, 3]


def test_releases_count_pre_releases_only_when_asked_and_a_read_with_none_is_a_fact(c):
    assert F.publishes_releases(c, REPO) is None
    F.saw_releases(c, REPO, 1)
    assert F.publishes_releases(c, REPO) is False
    F.save_release(c, REPO, {"tag": "v1.0.0-rc.1", "commit_sha": "a", "prerelease": True}, 2)
    assert F.publishes_releases(c, REPO) is True
    assert F.releases(c, REPO) == []
    assert [r["tag"] for r in F.releases(c, REPO, prereleases=True)] == ["v1.0.0-rc.1"]
    release = F.releases(c, REPO, prereleases=True)[0]
    assert F.included(c, release["id"], "m") is None
    F.save_included(c, release["id"], "m", True)
    assert F.included(c, release["id"], "m") is True


def test_a_webhook_delivery_is_taken_once(c):
    assert not F.seen_delivery(c, "d-1", "pull_request")
    assert F.seen_delivery(c, "d-1", "pull_request")
    assert not F.seen_delivery(c, None, "pull_request")


def test_a_verdict_is_owed_once_and_three_failed_calls_give_it_to_a_person(c):
    key = PV.rubric_hash("Covers goal, scope", {"b": 1, "a": 2})
    assert key == PV.rubric_hash(" Covers goal, scope ", {"a": 2, "b": 1})
    row = PV.owe(c, "f1", 2, key)
    assert PV.owe(c, "f1", 2, key)["id"] == row["id"]
    assert [r["id"] for r in PV.owed(c, H.now())] == [row["id"]]
    for n in (1, 2):
        after = PV.record(c, row["id"], {"reason": "timeout"}, failed=True)
        assert (after["verdict"], after["tries"]) == (None, n)
        assert PV.owed(c, H.now()) == []  # not due again yet
    assert PV.record(c, row["id"], {"reason": "timeout"}, failed=True)["verdict"] == "failed"


def test_a_verdict_keeps_jev_s_answer(c):
    row = PV.owe(c, "f1", 1, PV.rubric_hash("A real review"))
    done = PV.record(c, row["id"], {"verdict": "not_met", "p": 0.1, "reason": "No findings", "model": "jev"})
    assert (done["verdict"], done["p"], done["reasons"]) == ("not_met", 0.1, "No findings")
    assert PV.record(c, row["id"], {"verdict": "met", "p": 0.9})["verdict"] == "not_met"  # given once


def test_a_page_ref_names_a_file_and_maybe_a_version():
    assert P.parse_ref("page:abc") == ("abc", None)
    assert P.parse_ref(" page:abc@3 ") == ("abc", 3)
    for bad in ("abc", "page:", "page:abc@0", "file:abc@1"):
        with pytest.raises(ValueError):
            P.parse_ref(bad)


def test_the_page_that_counts_is_the_newest_not_replaced(c):
    X.pipeline(c)
    task = X.new_task(c, X.ANA, "Spec it")
    visit, stage = task["visit"]["id"], task["stage_id"]
    rid = c.execute("SELECT id FROM requirements WHERE stage_id=?", (stage,)).fetchone()[0]
    now = H.now()
    for pid, version, replaced in (("p1", 1, now), ("p2", 2, None)):
        c.execute(
            "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, attached_by_id, "
            "created_at, replaced_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, visit, rid, "f1", version, X.ANA, now, replaced),
        )
    assert P.current(c, visit, rid)["version"] == 2
    assert P.newest_for_task(c, task["id"], stage)["id"] == "p2"
