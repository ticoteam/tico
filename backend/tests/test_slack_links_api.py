"""The Slack link routes (`/api/v2/slack/links`, backend/slack_threads.py `install`): ask, list, unlink and check again.

Admins only. No Slack is needed: these routes only write the rows the gateway acts on later.
"""

import pytest

from backend.store import H
from backend.tests.test_api import api, headers  # noqa: F401  (the api fixture)

THREAD = "T-marketing"
ADMIN, MEMBER = "ana-test", "cara-test"          # ana is the owner; cara is an ordinary person (test_api.api_settings)


@pytest.fixture
def thread(api):
    """A company thread: the root a Slack channel can be linked to."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO conversations (id,kind,subject,created,audience,root_id,path) VALUES (?,?,?,?,?,?,'')",
                  (THREAD, "thread", "Marketing", H.now(), "company", THREAD))
    return THREAD


def request_link(api, token=ADMIN):
    r = api.post("/api/v2/slack/links", json={"thread_id": THREAD, "channel": "#marketing"}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def listed(api, token=ADMIN):
    r = api.get("/api/v2/slack/links", params={"thread": THREAD}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()["links"]


def test_a_person_who_is_not_an_admin_is_refused_on_every_link_route(api, thread):
    link = request_link(api)
    member = headers(MEMBER)
    assert api.get("/api/v2/slack/links", params={"thread": THREAD}, headers=member).status_code == 403
    assert api.post("/api/v2/slack/links", json={"thread_id": THREAD, "channel": "#marketing"},
                    headers=member).status_code == 403
    assert api.delete("/api/v2/slack/links/" + link["id"], headers=member).status_code == 403
    assert api.post("/api/v2/slack/links/" + link["id"] + "/resume", headers=member).status_code == 403
    assert [row["state"] for row in listed(api)] == ["requested"]     # the refused calls changed nothing


def test_a_request_names_a_thread_and_a_channel_and_nothing_else(api, thread):
    base = {"thread_id": THREAD, "channel": "#marketing"}
    assert api.post("/api/v2/slack/links", json={**base, "channel": ""}, headers=headers()).status_code == 422
    assert api.post("/api/v2/slack/links", json={**base, "audience": "company"}, headers=headers()).status_code == 422
    assert listed(api) == []


def test_a_requested_link_is_listed_until_it_is_unlinked(api, thread):
    link = request_link(api)
    assert link["state"] == "requested" and link["thread_id"] == THREAD
    assert [row["id"] for row in listed(api)] == [link["id"]]
    assert listed(api)[0]["state"] == "requested"
    r = api.delete("/api/v2/slack/links/" + link["id"], headers=headers())
    assert r.status_code == 200 and r.json()["state"] == "unlinked"
    assert listed(api) == []


def test_only_a_suspended_or_refused_link_is_checked_again(api, thread):
    link = request_link(api)
    r = api.post("/api/v2/slack/links/" + link["id"] + "/resume", headers=headers())
    assert r.status_code == 409, r.text


def test_a_thread_that_does_not_exist_is_not_found(api):
    r = api.post("/api/v2/slack/links", json={"thread_id": "no-such-thread", "channel": "#marketing"}, headers=headers())
    assert r.status_code == 404, r.text
