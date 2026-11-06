"""Meeting invitations grant the same access regardless of who invited a teammate."""
import pytest

from backend.tests.test_api import api, get, headers, post, restrict  # noqa: F401
from backend.tests.test_live_meetings import attach, chunk, live, settle  # noqa: F401
from backend.tests.test_mcp import call


def test_added_person_can_add_people_and_bots_without_joining_private_meeting(live):
    mid = post(live, "live-meetings", {"title": "Planning", "visibility": "private"})["id"]
    post(live, f"live-meetings/{mid}/access", {"add": ["ben"]})
    shared = post(live, f"live-meetings/{mid}/access", {"add": ["cara"]}, token="ben-test")
    assert {p["actor"] for p in shared["access"]} == {"human:ben", "human:cara"}
    assert attach(live, mid, "ops", token="ben-test")["added"] == ["ops"]
    assert get(live, f"live-meetings/{mid}", token="ops-live")["id"] == mid
    assert get(live, f"live-meetings/{mid}", token="cara-test")["id"] == mid
    assert get(live, f"live-meetings/{mid}", token="finance-live", expected=404)
    for body in ({"visibility": "team"}, {"remove": ["cara"]}, {"add": ["nobody"]}):
        post(live, f"live-meetings/{mid}/access", body, token="ben-test",
             expected=404 if "add" in body else 403)
    denied = live.request("DELETE", f"/api/v2/live-meetings/{mid}/bots/ops", json={}, headers=headers("cara-test"))
    assert denied.status_code == 403
    # Leaving revokes the person's grant, but the teammates they let in keep theirs.
    assert post(live, f"live-meetings/{mid}/access", {"remove": ["ben"]}, token="ben-test")["left"]
    get(live, f"live-meetings/{mid}", token="ben-test", expected=404)
    assert get(live, f"live-meetings/{mid}", token="ops-live")["id"] == mid
    assert get(live, f"live-meetings/{mid}", token="cara-test")["id"] == mid
    removed = live.request("DELETE", f"/api/v2/live-meetings/{mid}/bots/ops", json={}, headers=headers())
    assert removed.status_code == 200 and removed.json()["removed"]
    get(live, f"live-meetings/{mid}", token="ops-live", expected=404)
    assert post(live, f"live-meetings/{mid}/access", {"remove": ["ana"]})["left"]
    assert "human:ana" not in {p["actor"] for p in get(live, f"live-meetings/{mid}")["humans"]}


def test_private_conversion_keeps_explicit_people_and_bots_but_drops_team_joiners(live):
    mid = post(live, "live-meetings", {"title": "Planning"})["id"]
    post(live, f"live-meetings/{mid}/access", {"add": ["cara"]}, token="ben-test", expected=403)
    post(live, f"live-meetings/{mid}/join", {}, token="ben-test")
    post(live, f"live-meetings/{mid}/access", {"add": ["cara"]}, token="ben-test")
    attach(live, mid, "ops", token="ben-test")
    post(live, f"live-meetings/{mid}/join", {}, token="cara-test")
    shared = post(live, f"live-meetings/{mid}/access", {"visibility": "private"})
    assert {p["actor"] for p in shared["humans"]} == {"human:ana", "human:cara"}
    assert [b["bot"] for b in shared["bots"]] == ["ops"]
    get(live, f"live-meetings/{mid}", token="ben-test", expected=404)
    assert get(live, f"live-meetings/{mid}", token="ops-live")["id"] == mid
    attach(live, mid, "finance", token="cara-test")
    removed = live.request("DELETE", f"/api/v2/live-meetings/{mid}/bots/finance", json={}, headers=headers("cara-test"))
    assert removed.status_code == 200 and removed.json()["removed"]
    post(live, f"live-meetings/{mid}/access", {"remove": ["cara"]})
    get(live, f"live-meetings/{mid}", token="cara-test", expected=404)


def test_private_participant_still_needs_see_and_write_access_to_attach(live):
    mid = post(live, "live-meetings", {"title": "Planning", "visibility": "private"})["id"]
    post(live, f"live-meetings/{mid}/access", {"add": ["cara"]})
    with live.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    post(live, f"live-meetings/{mid}/bots", {"bots": ["ops"]}, token="cara-test", expected=403)
    with live.app.state.store.transaction() as c:
        restrict(c, "ops", see={"people": ["cara"]}, read={"people": ["cara"]}, write={"people": ["ana"]})
    post(live, f"live-meetings/{mid}/bots", {"bots": ["ops"]}, token="cara-test", expected=403)
    assert get(live, f"live-meetings/{mid}")["bots"] == []


@pytest.mark.parametrize("visibility", ["private", "team"])
def test_nonowner_bot_and_added_people_read_participants_only_filed_meeting(live, visibility):
    live.app.state.live_meeting_decider = lambda *args: {}
    mid = post(live, "live-meetings", {"title": "Shared planning", "visibility": visibility})["id"]
    post(live, f"live-meetings/{mid}/access", {"add": ["ben"]})
    post(live, f"live-meetings/{mid}/access", {"add": ["cara"]}, token="ben-test")
    attach(live, mid, "ops", token="ben-test")
    chunk(live, mid, 1, 0, 1000, "A shared transcript for planning")
    post(live, f"live-meetings/{mid}/control", {"action": "end"})
    rid = post(live, f"live-meetings/{mid}/finalize", {})["meeting_id"]
    saved = get(live, f"meetings/{rid}")
    assert {p["email"] for p in saved["participants"]} >= {"ben@acme.example", "cara@acme.example"}
    post(live, f"meetings/{rid}/edit", {"version": saved["version"], "note": "Shared planning notes"})
    upload = live.post(f"/api/meetings/{rid}/attachments", headers=headers(),
                       files={"files": ("notes.txt", b"Shared planning download", "text/plain")})
    assert upload.status_code == 200, upload.text
    bid = upload.json()["attachments"][0]["id"]
    reads = (f"/api/v2/meetings/{rid}", f"/api/meetings/{rid}", f"/api/meetings/{rid}/versions",
             f"/api/v2/meetings/transcript?id={rid}", f"/api/v2/files/{bid}")
    for path in reads:
        assert live.get(path, headers=headers("ops-live")).status_code == (403 if "/files/" in path else 404)
    post(live, f"meetings/{rid}/review", {"action": "approve", "audience": "participants"})
    for token in ("ben-test", "cara-test", "ops-live"):
        detail = get(live, f"meetings/{rid}", token=token)
        assert detail["note"] == "Shared planning notes" and not detail["can_edit"]
        assert "shared transcript" in get(live, f"meetings/transcript?id={rid}", token=token)["text"]
    for path in reads:
        assert live.get(path, headers=headers("ops-live")).status_code == 200, path
        assert live.get(path, headers=headers("finance-live")).status_code in (403, 404), path
    assert live.get(f"/api/v2/files/{bid}", headers=headers("ops-live")).content == b"Shared planning download"
    versions = live.get(f"/api/meetings/{rid}/versions", headers=headers("ops-live")).json()["versions"]
    assert any(v["metadata"].get("note") == "Shared planning notes" for v in versions)
    assert rid in {r["id"] for r in get(live, "meetings/search?q=shared", token="ops-live")["results"]}
    assert not get(live, "meetings/search?q=shared", token="finance-live")["results"]
    err, transcript = call(live, "hub_meeting_read", {"id": rid}, token="ops-live")
    assert not err and "shared transcript" in transcript["text"]
    err, _ = call(live, "hub_meeting_read", {"id": rid}, token="finance-live")
    assert err
    post(live, f"meetings/{rid}/edit", {"version": detail["version"], "note": "Cannot edit"},
         token="ops-live", expected=403)
    post(live, f"meetings/{rid}/delete", {})
    for path in reads:
        assert live.get(path, headers=headers("ops-live")).status_code in (403, 404), path
