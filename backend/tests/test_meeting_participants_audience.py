"""Who reads a saved meeting: the Team, or only the meeting's participants (owners too are held to it). Synthetic
text only."""

from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.media import import_meeting
from backend.tests.helpers.participants_audience import live_meeting, readable


def test_participants_only_is_read_by_who_filed_it_and_not_by_the_company_owner(api):
    # ana is the company owner; ben files it.
    rid = import_meeting(
        api, "ben-test", title="Comp review", private=True, audience="participants", participants=["cara@acme.example"]
    )["id"]
    assert readable(api, rid, "ben-test")
    assert not readable(api, rid, "ana-test")
    # Named participants are not the audience: only the people the live meeting had are.
    assert not readable(api, rid, "cara-test")
    assert rid not in {row["id"] for row in api.get("/api/meetings", headers=headers()).json()}
    team = import_meeting(api, "ben-test", title="Standup", private=False)["id"]
    assert readable(api, team, "cara-test") and readable(api, team, "ana-test")


def test_participants_only_cannot_be_widened_by_a_later_import(api):
    body = {
        "title": "Comp review",
        "source": "granola",
        "external_id": "rec-1",
        "private": True,
        "audience": "participants",
    }
    rid = import_meeting(api, "ben-test", **body)["id"]
    import_meeting(api, "ben-test", **{**body, "private": False, "audience": None, "notes": "later"})
    assert not readable(api, rid, "cara-test") and not readable(api, rid, "ana-test")


def test_a_live_meeting_saved_for_participants_reads_to_who_was_added_or_joined(api):
    # Open to the team while live: cara joined, eve was added; fay never came.
    mid = live_meeting(api, visibility="team", joiner="cara", added="eve")
    post(api, f"live-meetings/{mid}/control", {"action": "end"}, token="ben-test")
    rid = post(api, f"live-meetings/{mid}/finalize", {}, token="ben-test")["meeting_id"]
    post(
        api,
        f"meetings/{rid}/review",
        {"action": "approve", "private": True, "audience": "participants"},
        token="ben-test",
    )
    assert readable(api, rid, "ben-test") and readable(api, rid, "cara-test") and readable(api, rid, "eve-test")
    assert not readable(api, rid, "ana-test") and not readable(api, rid, "fay-test")
    # The ended live room it came from is held to the same people.
    room = lambda token: api.get(f"/api/v2/live-meetings/{mid}", headers=headers(token)).status_code == 200
    assert room("ben-test") and room("cara-test") and room("eve-test")
    assert not room("fay-test") and not room("ana-test")


def test_saving_again_for_participants_after_a_team_approval_narrows_it(api):
    # A Save approved for the Team whose later step failed, retried as Participants only.
    mid = live_meeting(api, visibility="team", joiner="cara")
    post(api, f"live-meetings/{mid}/control", {"action": "end"}, token="ben-test")
    rid = post(api, f"live-meetings/{mid}/finalize", {}, token="ben-test")["meeting_id"]
    post(api, f"meetings/{rid}/review", {"action": "approve", "private": False, "audience": "team"}, token="ben-test")
    assert readable(api, rid, "fay-test")
    post(
        api,
        f"meetings/{rid}/review",
        {"action": "approve", "private": True, "audience": "participants"},
        token="ben-test",
    )
    assert readable(api, rid, "cara-test") and not readable(api, rid, "fay-test")
    assert api.get(f"/api/v2/live-meetings/{mid}", headers=headers("fay-test")).status_code == 404


def test_an_unknown_audience_is_refused_so_nothing_is_shared_wider(api):
    mid = live_meeting(api)
    post(api, f"live-meetings/{mid}/control", {"action": "end"}, token="ben-test")
    rid = post(api, f"live-meetings/{mid}/finalize", {}, token="ben-test")["meeting_id"]
    refused = api.post(
        f"/api/v2/meetings/{rid}/review",
        headers=headers("ben-test"),
        json={"action": "approve", "private": True, "audience": "everyone"},
    )
    assert refused.status_code == 422
    assert get(api, f"meetings/{rid}", token="ben-test")["review_state"] == "pending"


def test_once_sent_to_a_task_it_cannot_be_narrowed_but_can_be_widened(api):
    mid = live_meeting(api, visibility="team", joiner="cara")
    post(api, f"live-meetings/{mid}/control", {"action": "end"}, token="ben-test")
    rid = post(api, f"live-meetings/{mid}/finalize", {}, token="ben-test")["meeting_id"]
    post(api, f"meetings/{rid}/review", {"action": "approve", "private": False, "audience": "team"}, token="ben-test")
    # The transcript went into a task for the team (media.finish_delivery records it so).
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO meeting_deliveries(meeting_id,requested_by,sender_name,destination,instructions,"
            "status,task_id,requested_at) VALUES(?,?,?,?,?,?,?,?)",
            (rid, "ben@acme.example", "Ben", "ops", "", "delivered", "2417", "2026-11-04T00:00:00Z"),
        )
    for body in (
        {"action": "approve", "private": True, "audience": "participants"},
        {"action": "approve", "private": True},
    ):
        refused = api.post(f"/api/v2/meetings/{rid}/review", json=body, headers=headers("ben-test"))
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "already_sent"
        assert refused.json()["error"]["detail"] == ("Already sent to #2417 for the team. Change who can see it there.")
    assert readable(api, rid, "fay-test")


def test_a_participants_save_retried_for_the_team_widens(api):
    mid = live_meeting(api, visibility="private", added="eve")
    post(api, f"live-meetings/{mid}/control", {"action": "end"}, token="ben-test")
    rid = post(api, f"live-meetings/{mid}/finalize", {}, token="ben-test")["meeting_id"]
    post(
        api,
        f"meetings/{rid}/review",
        {"action": "approve", "private": True, "audience": "participants"},
        token="ben-test",
    )
    assert not readable(api, rid, "fay-test")
    post(api, f"meetings/{rid}/review", {"action": "approve", "private": False, "audience": "team"}, token="ben-test")
    assert readable(api, rid, "fay-test")
    # A retry that names no audience: private:false alone still means the Team.
    post(
        api,
        f"meetings/{rid}/review",
        {"action": "approve", "private": True, "audience": "participants"},
        token="ben-test",
    )
    post(api, f"meetings/{rid}/review", {"action": "approve", "private": False}, token="ben-test")
    assert readable(api, rid, "fay-test")
