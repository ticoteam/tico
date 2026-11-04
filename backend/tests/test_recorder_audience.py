"""Who reads what the Recorder saves: the Team, or only the meeting's participants (owners too are held to it), and
what is left in Tico when a live share is kept on the computer instead. Synthetic text only."""

import json

from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_media import import_meeting


def readable(api, rid, token):
    return api.get("/api/meetings/" + rid, headers=headers(token)).status_code == 200


def test_participants_only_is_read_by_the_recorder_and_not_by_the_company_owner(api):
    # ana is the company owner; ben records.
    rid = import_meeting(api, "ben-test", title="Comp review", private=True, audience="participants",
                         participants=["cara@acme.example"])["id"]
    assert readable(api, rid, "ben-test")
    assert not readable(api, rid, "ana-test")
    # Named participants are not the audience: only the people the Recorder names are.
    assert not readable(api, rid, "cara-test")
    assert rid not in {row["id"] for row in api.get("/api/meetings", headers=headers()).json()}
    team = import_meeting(api, "ben-test", title="Standup", private=False)["id"]
    assert readable(api, team, "cara-test") and readable(api, team, "ana-test")


def test_participants_only_cannot_be_widened_by_a_later_import(api):
    body = {"title": "Comp review", "source": "tico-recorder", "external_id": "rec-1", "private": True,
            "audience": "participants"}
    rid = import_meeting(api, "ben-test", **body)["id"]
    import_meeting(api, "ben-test", **{**body, "private": False, "audience": None, "notes": "later"})
    assert not readable(api, rid, "cara-test") and not readable(api, rid, "ana-test")


def on_roster(api, *names):
    """eve and fay sign in like the others; here they are on the roster too."""
    with api.app.state.store.transaction() as c:
        raw = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for name in names:
            c.execute("INSERT OR IGNORE INTO humans(id, name, email) VALUES(?,?,?)",
                      (name, name.title(), name + "@acme.example"))
            raw["people"].append({"id": name, "email": name + "@acme.example", "name": name.title()})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(raw),))


def live_meeting(api, *, visibility="private", joiner=None, added=None):
    on_roster(api, "eve", "fay")
    mid = post(api, "live-meetings", {"title": "Sync", "visibility": visibility}, token="ben-test")["id"]
    api.post(f"/api/v2/live-meetings/{mid}/chunks", headers=headers("ben-test"), json={"chunks": [
        {"seq": 1, "speaker": "Ben", "start_ms": 0, "end_ms": 900, "text": "Synthetic words"}]})
    post(api, f"live-meetings/{mid}/chat", {"text": "Synthetic chat"}, token="ben-test")
    if added:
        post(api, f"live-meetings/{mid}/access", {"add": [added]}, token="ben-test")
    if joiner:
        post(api, f"live-meetings/{mid}/join", {}, token=f"{joiner}-test")
    return mid


def test_a_live_meeting_saved_for_participants_reads_to_who_was_added_or_joined(api):
    # Open to the team while live: cara joined, eve was added; fay never came.
    mid = live_meeting(api, visibility="team", joiner="cara", added="eve")
    post(api, f"live-meetings/{mid}/control", {"action": "end"}, token="ben-test")
    rid = post(api, f"live-meetings/{mid}/finalize", {}, token="ben-test")["meeting_id"]
    post(api, f"meetings/{rid}/review", {"action": "approve", "private": True, "audience": "participants"},
         token="ben-test")
    assert readable(api, rid, "ben-test") and readable(api, rid, "cara-test") and readable(api, rid, "eve-test")
    assert not readable(api, rid, "ana-test") and not readable(api, rid, "fay-test")


def test_a_live_meeting_saved_for_the_team_reads_to_everyone(api):
    mid = live_meeting(api)
    post(api, f"live-meetings/{mid}/control", {"action": "end"}, token="ben-test")
    rid = post(api, f"live-meetings/{mid}/finalize", {}, token="ben-test")["meeting_id"]
    post(api, f"meetings/{rid}/review", {"action": "approve", "private": False, "audience": "team"},
         token="ben-test")
    assert readable(api, rid, "fay-test")


def test_an_unknown_audience_is_refused_so_nothing_is_shared_wider(api):
    mid = live_meeting(api)
    post(api, f"live-meetings/{mid}/control", {"action": "end"}, token="ben-test")
    rid = post(api, f"live-meetings/{mid}/finalize", {}, token="ben-test")["meeting_id"]
    refused = api.post(f"/api/v2/meetings/{rid}/review", headers=headers("ben-test"),
                       json={"action": "approve", "private": True, "audience": "everyone"})
    assert refused.status_code == 422
    assert get(api, f"meetings/{rid}", token="ben-test")["review_state"] == "pending"


def test_discard_ends_the_live_meeting_and_removes_what_was_said(api):
    mid = live_meeting(api, added="cara")
    assert api.post(f"/api/v2/live-meetings/{mid}/discard", json={}, headers=headers("cara-test")).status_code == 403
    assert post(api, f"live-meetings/{mid}/discard", {}, token="ben-test") == {
        "id": mid, "state": "ended", "discarded": True}
    detail = get(api, f"live-meetings/{mid}", token="ben-test")
    assert detail["state"] == "ended" and detail["chunks"] == [] and detail["chat"] == []
    with api.app.state.store.read() as c:
        for table in ("live_meeting_chunks", "live_meeting_chunk_versions", "live_meeting_chat"):
            assert c.execute(f"SELECT count(*) FROM {table} WHERE meeting_id=?", (mid,)).fetchone()[0] == 0
        events = [row[0] for row in c.execute("SELECT payload FROM live_meeting_events WHERE meeting_id=?", (mid,))]
    assert not any("Synthetic" in payload for payload in events)
    # Discarding again is a no-op answer, not an error.
    assert post(api, f"live-meetings/{mid}/discard", {}, token="ben-test")["discarded"] is True
