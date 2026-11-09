"""Tico merges approved work itself (backend/merge_arm.py): it arms the merge at the approved head, re-arms a head the
queue dropped for a reason that is not the change's, hands a red or conflicting pull request to the pipeline manager,
and moves a merged card to Pending Release."""

import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import github as G
from backend import hubdb as H
from backend import merge_arm as M
from backend import bot_settings as BS
from backend import rules
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import encode

SECRET = "hook-secret"
PR = "https://github.com/ticoteam/tico/pull/412"
HEAD, NEW = "a" * 40, "d" * 40
STEPS = [
    ("On Deck", "open"),
    ("In Progress", "doing"),
    ("Review 1", "review"),
    ("Review 1 Fixes", "doing"),
    ("Review 2", "review"),
    ("Review 2 Fixes", "doing"),
    ("In QA", "review"),
    ("QA Fixes", "doing"),
    ("Pending Release", "review"),
    ("Post Release QA", "review"),
    ("Done", "done"),
]


@pytest.fixture
def api(tmp_path):
    app = create_app(
        Settings(
            db_path=tmp_path / "hub.db",
            github_webhook_secret=SECRET,
            release_commit="",
            release_repo="ticoteam/tico",
            test_identities={"ana-test": Identity("human:ana", "owner", "ana@acme.example")},
        )
    )
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ("cpo", "lead")}
            people = {
                "people": [{"id": "ana", "email": "ana@acme.example", "team": "leadership", "primary_for": ["*"]}]
            }
            H.sync_registry(c, bots, people)
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode(people),))
            for slug, config in bots.items():
                c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)", (slug, "product", "ana"))
                BS.write(c, slug, config)
            c.execute(
                "INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),)
            )
            c.execute(
                "INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created_at,"
                "created_by) VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,'human:ana')",
                (H.now(),),
            )
            c.execute(
                "INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at,default_branch) "
                "VALUES('ticoteam/tico',1,1,'human:ana',?,'main')",
                (H.now(),),
            )
        yield client


def headers():
    return {"Authorization": "Bearer ana-test", "Idempotency-Key": str(uuid.uuid4())}


def hook(api, event, payload):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    r = api.post(
        G.PATH,
        content=body,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": event, "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text


def pr_event(action, head=HEAD, **over):
    pr = {"html_url": PR, "draft": False, "merged": False, "merge_commit_sha": None, "merged_at": None, **over}
    return {
        "action": action,
        "pull_request": {**pr, "head": {"sha": head}},
        "repository": {"full_name": "ticoteam/tico"},
    }


def queue(api, action, reason=None):
    body = pr_event(action)
    if reason:
        body["reason"] = reason
    hook(api, "pull_request", body)


class GitHub:
    """A fake GitHub: GraphQL reads and merge requests, a pull request read, and merge-queue runs with their jobs."""

    def __init__(self, api, monkeypatch):
        self.service = api.app.state.github_app
        self.calls, self.refuse, self.queued, self.jobs = [], "", False, None
        monkeypatch.setattr(self.service, "mint", lambda *a, **kw: ("test-token", "later"))
        monkeypatch.setattr(self.service, "_call", self.call)

    def call(self, method, path, headers=None, json=None, params=None, **kw):
        if path == "/graphql":
            mutation = json["query"].lstrip().startswith("mutation")
            self.calls.append(("mutation" if mutation else "read", json["variables"]))
            if mutation:
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"errors": [{"message": self.refuse}]} if self.refuse else {"data": {}},
                )
            found = {
                "id": "PR_node",
                "state": "OPEN",
                "merged": False,
                "headRefOid": HEAD,
                "mergeable": "MERGEABLE",
                "isInMergeQueue": self.queued,
                "isMergeQueueEnabled": True,
                "autoMergeRequest": None,
            }
            return SimpleNamespace(status_code=200, json=lambda: {"data": {"repository": {"pullRequest": found}}})
        self.calls.append((method, path))
        if path.endswith("/pulls/412"):
            return SimpleNamespace(
                status_code=200, json=lambda: {"state": "open", "merged": False, "head": {"sha": HEAD}}
            )
        if path.endswith("/actions/runs"):
            run = {"id": 7, "head_branch": "gh-readonly-queue/main/pr-412-" + "b" * 40, "head_sha": "e" * 40}
            run["created_at"] = H.now()
            return SimpleNamespace(status_code=200, json=lambda: {"workflow_runs": [run] if self.jobs else []})
        if path.endswith("/runs/7/jobs"):
            return SimpleNamespace(status_code=200, json=lambda: {"jobs": self.jobs})
        return SimpleNamespace(status_code=404, json=lambda: {})

    def mutations(self):
        return [v for kind, v in self.calls if kind == "mutation"]


def ready_card(api, owner="cpo"):
    """A bot's Dev ticket at In QA, tagged to skip QA (so the merge is next), with green CI at HEAD."""
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, "Dev tickets", [{"name": n, "status": s} for n, s in STEPS])
        rules.seed_dev(c)
        c.execute("UPDATE task_types SET manager='lead' WHERE id=?", (typ["id"],))
    r = api.post(
        "/api/v2/tasks",
        headers=headers(),
        json={"owner": owner, "title": "Improve messages", "body": "Build.", "type": typ["id"], "links": [PR]},
    )
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    hook(api, "pull_request", pr_event("opened"))
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ana", task["id"], labels=["skip-qa"])
        H.task_update(c, "human:ana", task["id"], step=step_id(c, task, "In QA"), strict=False)
        c.execute("UPDATE task_links SET checks='passing',mergeable='clean' WHERE task_id=?", (task["id"],))
        c.execute("DELETE FROM jobs")
    return task


def step_id(c, task, name):
    return c.execute(
        "SELECT id FROM task_steps WHERE type_id=? AND name=?", (H.task(c, task["id"])["type_id"], name)
    ).fetchone()[0]


def card(api, task):
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
        link = H.task_links(c, task["id"])[0]
        step = c.execute("SELECT name FROM task_steps WHERE id=?", (row["step_id"],)).fetchone()[0]
        return step, H._json(link["detail"], {}) or {}


def fix_tasks(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE title LIKE 'Fix PR #412%'")]


def stale(api, task):
    """The last arming was long ago."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE task_links SET detail=json_set(detail,'$.merge_arm.at',?) WHERE task_id=?",
            (H.shift(H.now(), hours=-1), task["id"]),
        )


def builder_merge_wakes(api):
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT message_id FROM jobs WHERE bot_id='cpo' ORDER BY rowid").fetchall()
        return [
            m
            for m in (H.message(c, r[0]) for r in rows)
            if (m["refs"] or {}).get("handoff") in ("merge", "merge_conflict")
        ]


def test_tico_arms_a_ready_pull_request_once_at_its_approved_head(api, monkeypatch):
    gh = GitHub(api, monkeypatch)
    task = ready_card(api)
    assert M.sweep(gh.service)
    assert gh.mutations() == [{"id": "PR_node", "head": HEAD}]  # auto-merge at the exact head: the queue takes it
    step, detail = card(api, task)
    assert step == "In QA" and detail["merge_arm"]["head"] == HEAD and detail["merge_arm"]["arms"] == 1
    assert M.sweep(gh.service) == [] and len(gh.calls) == 2  # armed: nothing is asked again
    queue(api, "enqueued")
    stale(api, task)
    assert M.sweep(gh.service) == [] and len(gh.calls) == 2  # queued at the head: nothing to do
    assert builder_merge_wakes(api) == [] and fix_tasks(api) == []


def test_without_the_github_app_or_on_a_persons_card_nothing_is_armed(api, monkeypatch):
    gh = GitHub(api, monkeypatch)
    task = ready_card(api, owner="ana")
    assert M.sweep(gh.service) == [] and gh.calls == []  # a person merges their own
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='bot:cpo' WHERE id=?", (task["id"],))
        row = H.task(c, task["id"])
        assert M.tico_merges(c, row, H.task_links(c, task["id"])[0])
        c.execute("DELETE FROM github_app")  # no App: Tico cannot merge, so the builder is woken as before
        assert not M.tico_merges(c, row, H.task_links(c, task["id"])[0])


def test_a_head_github_will_not_take_goes_to_the_pipeline_manager_after_three_tries(api, monkeypatch):
    gh = GitHub(api, monkeypatch)
    gh.refuse = "Auto merge is not allowed for this repository"
    task = ready_card(api)
    M.sweep(gh.service)
    assert len(gh.mutations()) == 2  # auto-merge, then enqueue
    for _ in range(2):
        stale(api, task)
        M.sweep(gh.service)
    _, detail = card(api, task)
    assert detail["merge_arm"]["arms"] == 3 and detail["merge_arm"]["handed"]
    [fix] = fix_tasks(api)
    assert fix["owner_id"] == "bot:lead" and "Auto merge is not allowed" in fix["body"]
    stale(api, task)
    calls = len(gh.calls)
    M.sweep(gh.service)
    assert len(gh.calls) == calls  # handed over: Tico stops at this head
    assert builder_merge_wakes(api) == []


def test_armed_but_dropped_auto_merge_is_armed_again_after_it_goes_stale(api, monkeypatch):
    gh = GitHub(api, monkeypatch)
    task = ready_card(api)
    M.sweep(gh.service)
    stale(api, task)  # GitHub never queued it (auto-merge turned off): the next sweep arms it again
    M.sweep(gh.service)
    assert len(gh.mutations()) == 2
    gh.queued = True  # GitHub has it queued now: a read, no merge request
    stale(api, task)
    M.sweep(gh.service)
    assert len(gh.mutations()) == 2
    _, detail = card(api, task)
    assert detail["merge_queue"]["state"] == "queued"


def test_a_cancelled_queue_run_is_armed_again_once_then_goes_to_the_manager(api, monkeypatch):
    gh = GitHub(api, monkeypatch)
    task = ready_card(api)
    M.sweep(gh.service)
    queue(api, "enqueued")
    gh.jobs = [
        {"name": "python (3)", "conclusion": "cancelled", "html_url": "https://github.com/x/1"},
        {"name": "checks", "conclusion": "failure", "html_url": "https://github.com/x/2"},
    ]
    queue(api, "dequeued", "CI_FAILURE")
    G.refresh_queue_removals(gh.service)
    step, detail = card(api, task)
    assert step == "In QA" and detail["merge_arm"]["rearmed"]  # the card stays; Tico arms it again
    M.sweep(gh.service)
    assert len(gh.mutations()) == 2 and fix_tasks(api) == []
    queue(api, "enqueued")
    queue(api, "dequeued", "CI_FAILURE")  # dropped again at the same head
    G.refresh_queue_removals(gh.service)
    [fix] = fix_tasks(api)
    assert fix["owner_id"] == "bot:lead" and "dropped it twice" in fix["body"]
    assert card(api, task)[0] == "In QA" and builder_merge_wakes(api) == []
    from backend import repositories as R

    with api.app.state.store.read() as c:
        assert not R.metadata(c, "github-task-wake:" + task["id"]).get("items")  # the builder is not told


def test_a_real_queue_failure_goes_to_the_manager_with_the_failing_job_and_its_log(api, monkeypatch):
    gh = GitHub(api, monkeypatch)
    task = ready_card(api)
    M.sweep(gh.service)
    queue(api, "enqueued")
    gh.jobs = [
        {
            "name": "python (3)",
            "conclusion": "failure",
            "html_url": "https://github.com/ticoteam/tico/actions/runs/7/job/9",
        },
        {"name": "lint", "conclusion": "success", "html_url": "https://github.com/x/3"},
    ]
    queue(api, "dequeued", "CI_FAILURE")
    G.refresh_queue_removals(gh.service)
    [fix] = fix_tasks(api)
    with api.app.state.store.read() as c:
        assert fix["owner_id"] == "bot:lead" and H.TR.parent_of(c, fix["id"]) == task["id"]
    assert "python (3): https://github.com/ticoteam/tico/actions/runs/7/job/9" in fix["body"]
    M.sweep(gh.service)
    assert len(gh.mutations()) == 1  # not armed again at this head
    queue(api, "dequeued", "CI_FAILURE")  # a redelivery files no second task
    hook(api, "pull_request", pr_event("closed", merged=True, merge_commit_sha="c" * 40, merged_at=H.now()))
    with api.app.state.store.read() as c:
        assert H.task(c, fix["id"])["status"] not in H.ACTIVE_STATUSES  # the merge closes the fix task
        assert H.task(c, task["id"])["step_id"] == step_id(c, task, "Pending Release")


def test_an_approved_pull_request_that_conflicts_goes_to_the_manager_not_the_builder(api, monkeypatch):
    gh = GitHub(api, monkeypatch)
    task = ready_card(api)
    hook(api, "pull_request", pr_event("refresh", mergeable=False, mergeable_state="dirty"))
    hook(api, "pull_request", pr_event("synchronize", mergeable=False, mergeable_state="dirty"))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET checks='passing',mergeable='conflict' WHERE task_id=?", (task["id"],))
    M.sweep(gh.service)
    assert gh.mutations() == []
    [fix] = fix_tasks(api)
    assert fix["owner_id"] == "bot:lead" and "conflicts with its base branch" in fix["body"]
    assert builder_merge_wakes(api) == []


def test_a_conflict_webhook_on_an_approved_head_does_not_wake_the_builder(api, monkeypatch):
    GitHub(api, monkeypatch)
    task = ready_card(api)
    hook(api, "pull_request", {**pr_event("reopened", mergeable=False, mergeable_state="dirty")})
    from backend import repositories as R

    with api.app.state.store.read() as c:
        burst = R.metadata(c, "github-task-wake:" + task["id"])
    assert not burst.get("handoff")
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM github_app")  # Tico does not merge: a conflict is the builder's, as before
        c.execute("UPDATE task_links SET detail=json_remove(detail,'$.conflict_head') WHERE task_id=?", (task["id"],))
    hook(api, "pull_request", {**pr_event("reopened", mergeable=False, mergeable_state="dirty")})
    with api.app.state.store.read() as c:
        assert R.metadata(c, "github-task-wake:" + task["id"]).get("handoff") == "merge_conflict"


def test_a_merge_read_without_its_webhook_moves_the_card_to_pending_release(api, monkeypatch):
    gh = GitHub(api, monkeypatch)
    task = ready_card(api)
    merged = pr_event("refresh", merged=True, state="closed", merge_commit_sha="c" * 40, merged_at=H.now())
    with api.app.state.store.transaction() as c:
        G.pull_request(c, merged, release_repo="ticoteam/tico", read_refresh=True)
    assert card(api, task)[0] == "In QA"  # a read refresh does not move the card
    M.sweep(gh.service)
    assert card(api, task)[0] == "Pending Release"


@pytest.mark.parametrize(
    "jobs, real",
    [
        (None, False),
        ([{"conclusion": "cancelled"}, {"conclusion": "failure"}], False),
        ([{"conclusion": "failure"}, {"conclusion": "success"}], True),
        ([{"conclusion": "failure"}, {"conclusion": "cancelled"}, {"conclusion": "failure"}], True),
        ([{"conclusion": "success"}], False),
    ],
)
def test_a_real_failure_is_a_failed_leg_not_a_cancelled_run(jobs, real):
    assert M.real_failure(jobs) is real


@pytest.mark.parametrize(
    "reason, kind",
    [
        ("MERGE_CONFLICT", "conflict"),
        ("MANUAL", "manual"),
        ("QUEUE_CLEARED", "soft"),
        ("CI_FAILURE", "check"),
        ("", "check"),
    ],
)
def test_removal_reasons(reason, kind):
    assert M.removal_kind(reason) == kind
