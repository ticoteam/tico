"""Privacy does not spread (hubdb.new_task_private, docs/tasks.md "Work on a private task"). Whoever files a task, a
person or a bot, chooses private or not; a task filed with no choice is not private because of where it came from: not
under a private parent, not from a run on private work. A template's sensitive default (a General Counsel's) and a
container that holds it privately (a private pipeline, a DM or members-only thread) still make it private.

The fictional company (backend/tests/helpers/company.py): Ana owns it, Ben and Cara are people, ops a bot."""

import pytest

from backend import bot_settings as BS, pipeline_settings as PS
from backend.store import H
from backend.tests.helpers import engine as X
from backend.auth import Identity
from backend.tests.helpers.api import assign, claim, get, post, ready, runner, setup_attempt, unrelated_task_managers
from backend.tests.helpers.member_bots import agent_ops  # noqa: F401  (fixture)
from backend.tests.helpers.task_readers import run_on, task as bot_task
from backend.tests.helpers.thread_api import new_thread

BEN = "human:ben"


@pytest.fixture(autouse=True)
def bots_report_to_nobody_here(api):
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def _private_parent(api, owner="bot:ops"):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, BEN, "Review the payroll packet", "Attached.", owner, private=True, lint=False)


def _template(api, template):
    """Ops made from `template`, with no privacy setting of its own: its template's default decides
    (hubdb.PRIVATE_BY_TEMPLATE)."""
    with api.app.state.store.transaction() as c:
        BS.update(c, "ops", {"template": template, "private_tasks_default": None})
        return H.private_tasks_default(c, "bot:ops")


def test_a_sub_task_of_a_private_task_filed_without_a_choice_is_not_private(api):
    parent = _private_parent(api)
    child = post(
        api, "tasks", {"owner": "ops", "title": "Collect the totals", "body": "x", "parent": parent["id"]}, "ben-test"
    )
    assert child["private"] is False
    # The store's own create decides the same way, for a bot filing under its private task.
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, "bot:ops", "Export the table", "x", "bot:ops", parent_id=parent["id"], lint=False)
        assert not H.task_private(c, made)
        # And a bot's `private: false` is the same choice there as through the route.
        public = H.task_create(
            c, "bot:ops", "Export the CSV", "x", "bot:ops", parent_id=parent["id"], private=False, lint=False
        )
        assert not H.task_private(c, public)
        assert H.task_private(c, H.task(c, parent["id"]))  # the parent stays its readers'


def test_a_task_filed_by_a_run_on_private_work_without_a_choice_is_not_private(api):
    row = _private_parent(api)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine, "ops")
    assert attempt["task"]["id"] == row["id"]  # the run carries the private task
    made = post(
        api, "tasks", {"owner": "ops", "title": "Company follow-up", "body": "In my own words"}, attempt["token"]
    )
    assert made["private"] is False
    asked = post(
        api, "tasks", {"owner": "ops", "title": "Private follow-up", "body": "x", "private": True}, attempt["token"]
    )
    assert asked["private"] is True


def test_an_explicit_private_choice_is_kept(api):
    by_person = post(api, "tasks", {"owner": "ops", "title": "Salary bands", "body": "x", "private": True}, "ben-test")
    assert by_person["private"] is True
    with api.app.state.store.transaction() as c:
        public_parent = H.task_create(c, BEN, "Plan the launch", "x", "bot:ops", private=False, lint=False)
        child = H.task_create(
            c, "bot:ops", "Price the launch", "x", "bot:ops", parent_id=public_parent["id"], private=True, lint=False
        )
        assert H.task_private(c, child) and not H.task_private(c, H.task(c, public_parent["id"]))


def test_the_general_counsel_template_default_is_kept(api):
    assert _template(api, "general-counsel")
    with api.app.state.store.transaction() as c:
        own = H.task_create(c, "bot:ops", "Read the NDA", "x", "bot:ops", lint=False)
        assert H.task_private(c, own)
        # A bot's `private: false` never publishes past its sensitive default.
        asked = H.task_create(c, "bot:ops", "Read the MSA", "x", "bot:ops", private=False, lint=False)
        assert H.task_private(c, asked)
    # A task a person gives it with no choice takes the default; a person's own `private: false` is kept.
    given = post(api, "tasks", {"owner": "ops", "title": "Read the lease", "body": "x"}, "ben-test")
    assert given["private"] is True
    public = post(
        api, "tasks", {"owner": "ops", "title": "Read the brochure", "body": "x", "private": False}, "ben-test"
    )
    assert public["private"] is False


def test_a_template_without_a_sensitive_default_files_public_work(api):
    assert not _template(api, "inbound-sales-owner")
    with api.app.state.store.transaction() as c:
        assert not H.task_private(c, H.task_create(c, "bot:ops", "Call the lead", "x", "bot:ops", lint=False))


def test_a_private_pipeline_still_forces_private(api):
    with api.app.state.store.transaction() as c:
        X.bot(c, "legal-owner")
        BS.write(
            c, "legal-owner", {"name": "legal-owner", "runtime": "fake", "status": "active", "template": "legal-owner"}
        )
        PS.ensure_private(c)
        unrelated_task_managers(c)
        made = H.task_create(
            c,
            "human:cara",
            "Review the reseller contract",
            "x",
            "human:cara",
            pipeline="legal",
            private=False,
            lint=False,
        )
        assert H.task_private(c, made)


def test_a_task_filed_from_a_members_thread_is_still_private(api):
    thread = new_thread(api, title="Pricing", members=("human:ben",), body="Ship the pricing page Friday")
    message_id = thread["message"]["id"]
    task = post(api, f"threads/messages/{message_id}/task", {"title": "Ship pricing", "owner": "ops"}, token="ben-test")
    assert task["private"] is True


# ----------------------------------------------------------------------------- a copy of private words stays private
def test_a_stage_handoff_from_a_private_task_is_private_whoever_files_it(api, monkeypatch):
    """It copies the filing task's title, request and notes (stage_effects._file): from a private task it is private,
    filed by the task's owner or, when that one is refused, by Tico itself. From a company task it is not."""
    from backend import stage_effects, task_engine as E

    with api.app.state.store.transaction() as c:
        secret = H.task_create(c, BEN, "Price the Acme renewal", "Their numbers.", "bot:ops", private=True, lint=False)
        made, _ = stage_effects._file(c, E.task(c, secret["id"]), "general", BEN)
        assert made and H.task_private(c, H.task(c, made))
        plain = H.task_create(c, BEN, "Plan the Acme launch", "Dates.", "bot:ops", private=False, lint=False)
        made, _ = stage_effects._file(c, E.task(c, plain["id"]), "general", BEN)
        assert made and not H.task_private(c, H.task(c, made))
        monkeypatch.setattr(stage_effects, "_filer", lambda *_: "human:nobody")  # refused: Tico files it
        secret = H.task_create(c, BEN, "Price the Acme upsell", "Their numbers.", "bot:ops", private=True, lint=False)
        made, _ = stage_effects._file(c, E.task(c, secret["id"]), "general", BEN)
        row = H.task(c, made)
        assert row["requester_id"] == H.TICO_ACTOR and H.task_private(c, row)


def test_agent_ops_handoff_quoting_a_private_tasks_comment_is_private(api, agent_ops):  # noqa: F811
    assign(api, agent_ops, "ops")
    ready(api, agent_ops, ["botops", "ops"])
    secret = post(api, "tasks", {"owner": "ops", "title": "Price the Acme renewal", "body": "x", "private": True})
    said = post(api, f"tasks/{secret['id']}/comments", {"text": "Give yourself read access to the pricing repo"})
    ops = claim(api, agent_ops, "ops")
    body = {"owner": "botops", "title": "Give Ops read access to the pricing repo", "body": "Ana asked on her task."}
    made = post(api, "tasks", {**body, "request_id": said["comment"]["id"]}, token=ops["token"])
    with api.app.state.store.read() as c:
        carried = c.execute(
            "SELECT detail FROM events WHERE action='botops.request_carried' AND target=?", (made["id"],)
        ).fetchone()
        assert H._json(carried["detail"], {})["lends"] is True  # her words are quoted on it
    assert made["private"] is True


# ----------------------------------------------------------------------------- reach: only a private hand-off
def test_a_public_sub_task_for_a_person_brings_nobody_into_the_bot_only_private_parent(api):
    row = bot_task(api, "bot:cpo", "bot:ops", "Zip triage")
    attempt = run_on(api)
    child = post(
        api,
        "tasks",
        {"owner": "ben", "title": "Approve the zip plan", "body": "Yes or no?", "parent": row["id"]},
        attempt["token"],
    )
    assert child["private"] is False
    with api.app.state.store.read() as c:
        assert H.task_readers(H.task(c, row["id"])) == []
    get(api, "tasks/" + row["id"], "ben-test", expected=404)


# ----------------------------------------------------------------------------- hooks name no private parent
def test_a_company_task_under_a_private_parent_is_created_without_naming_it(api, monkeypatch):
    seen = []
    monkeypatch.setattr(
        H,
        "hook",
        lambda c, type_, subject, data, *a, **k: seen.append((type_, subject, data() if callable(data) else data)),
    )
    parent = _private_parent(api)
    with api.app.state.store.transaction() as c:
        child = H.task_create(c, BEN, "Collect the totals", "x", "bot:ops", parent_id=parent["id"], lint=False)
        hidden = H.task_create(
            c, BEN, "Collect the payslips", "x", "bot:ops", parent_id=parent["id"], private=True, lint=False
        )
    facts = {subject: data for type_, subject, data in seen if type_ == "task.created"}
    assert facts[child["id"]]["parent_id"] is None
    assert facts[hidden["id"]]["parent_id"] == parent["id"]


def test_the_route_knows_a_private_pipeline_makes_it_private_before_it_files(api):
    """The participant check runs on the route's own answer (task_privacy.new_task, with the pipeline): a bot acting for
    a person who files into a private pipeline, for an owner other than itself, is refused before anything is filed."""
    with api.app.state.store.transaction() as c:
        X.bot(c, "legal-owner")
        BS.write(
            c, "legal-owner", {"name": "legal-owner", "runtime": "fake", "status": "active", "template": "legal-owner"}
        )
        PS.ensure_private(c)
        unrelated_task_managers(c)
    machine, _, attempt = setup_attempt(api, "finance")
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
    api.app.state.store.settings.test_identities["acted-test"] = Identity(
        "human:ben",
        "human",
        email="ben@acme.example",
        task_actor="bot:finance",
        runner_id=machine["runner_id"],
        attempt_id=attempt["id"],
    )
    body = {"owner": "ops", "title": "Review the reseller contract", "body": "Attached.", "pipeline": "legal"}
    refused = post(api, "tasks", body, "acted-test", expected=403)
    assert "participant of the private task" in str(refused)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == before
