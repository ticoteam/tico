"""Privacy does not spread (hubdb.new_task_private, docs/tasks.md "Work on a private task"). Whoever files a task, a
person or a bot, chooses private or not; a task filed with no choice is not private because of where it came from: not
under a private parent, not from a run on private work. A template's sensitive default (a General Counsel's) and a
container that holds it privately (a private pipeline, a DM or members-only thread) still make it private.

The fictional company (backend/tests/helpers/company.py): Ana owns it, Ben and Cara are people, ops a bot."""

import pytest

from backend import bot_settings as BS, pipeline_settings as PS
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import assign, claim, post, ready, runner, unrelated_task_managers
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
