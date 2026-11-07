"""The requester seat on a Dev ticket may comment on it while its owner bot holds it, and nobody else may.

Synthetic fixtures only (a disposable test hub from test_tasks_board): the owner bot `ops`, the requester bot `cmo`
and an unrelated bot `cpo`. The owner's access and contact settings are restricted in each case, so the gates a
comment meets (its write level and bot contact) are the ones under test.
"""

import json

import pytest

from backend import bot_settings as BS
from backend import hubdb as H
from backend.tests.test_tasks_board import api, bot_token, get, headers, post  # noqa: F401  (fixture)

EVERYONE = {"everyone": True, "people": [], "teams": [], "bots": []}
PEOPLE_ONLY = {"everyone": False, "people": ["ben"], "teams": [], "bots": []}


def make(api, *, owner="bot:ops", requester="bot:cmo", private=False):
    with api.app.state.store.transaction() as c:
        number = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
        return H.task_create(
            c, requester, f"Build the requester check {number}", "Build it.", owner, private=private, lint=False
        )


def restrict(api, slug, *, write=EVERYONE, contact="open"):
    """The owner bot's Access write level and its bot contact, as a person would set them."""
    access = {"see": EVERYONE, "read": EVERYONE, "write": write}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET access=? WHERE bot=?", (json.dumps(access), slug))
        config = BS.read(c, slug)
        config["bot_contact"] = contact
        BS.write(c, slug, config)


def comment(api, task, token, text="Please check the scope."):
    return api.post("/api/v2/tasks/" + task["id"] + "/comments", json={"text": text}, headers=headers(token))


@pytest.mark.parametrize(
    "write, contact",
    [(EVERYONE, "open"), (PEOPLE_ONLY, "open"), (EVERYONE, "replies"), (PEOPLE_ONLY, "replies")],
    ids=["open", "write-restricted", "replies", "write-restricted-and-replies"],
)
def test_requester_comment_on_its_dev_ticket_is_accepted(api, write, contact):
    restrict(api, "ops", write=write, contact=contact)
    task = make(api)
    r = comment(api, task, bot_token(api, "cmo"))
    assert r.status_code == 200, r.text
    assert r.json()["comment"]["sender_id"] == "bot:cmo"


def test_unrelated_bot_is_still_refused_and_the_refusal_names_no_owner_none(api):
    restrict(api, "ops", write=PEOPLE_ONLY, contact="replies")
    task = make(api)
    r = comment(api, task, bot_token(api, "cpo"), "Drive-by comment.")
    assert r.status_code in (403, 404), r.text
    assert "None" not in r.text, r.text


def test_requester_comment_only_reaches_the_ticket_it_requested(api):
    """The seat is task-scoped: the same requester is still refused on a ticket owned by ops that it did not request."""
    restrict(api, "ops", write=PEOPLE_ONLY, contact="replies")
    other = make(api, requester="bot:cpo")
    r = comment(api, other, bot_token(api, "cmo"), "Not my ticket.")
    assert r.status_code in (403, 404), r.text


def test_owner_edits_its_own_title_and_body_under_restricted_settings(api):
    restrict(api, "ops", write=PEOPLE_ONLY, contact="replies")
    task = make(api)
    token = bot_token(api, "ops")
    current = get(api, "tasks/" + task["id"], token=token)["task"]
    edited = post(
        api,
        "tasks/" + task["id"],
        {"version": current["version"], "title": "Build the owner check", "body": "Build the owner's check."},
        token=token,
    )
    assert edited["title"] == "Build the owner check"
    assert edited["body"] == "Build the owner's check."
