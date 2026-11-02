"""Source discovery is identical over HTTP and MCP, with no private-source leakage."""
from backend import market, meetings
from backend.tests.test_api import api, headers, setup_attempt, runner  # noqa: F401
from backend.tests.test_mcp import call


def seed(api):
    with api.app.state.store.transaction() as c:
        market.create_evidence(c, market.SEED_ACTOR, quote="Cancellation terms in the market",
                               source_url="https://example.test/market")
        for rid, kind, private in (("shared", "meeting", False), ("private", "meeting", True), ("note", "note", False)):
            meetings.snapshot({"id": rid, "title": "Pricing discussion", "kind": kind,
                "private": private, "owner": "ben@acme.example", "status": "done",
                "calendar": {"attendees": ["ben@acme.example"]},
                "turns": [{"text": "Cancellation window is seven days", "speaker": "Dana",
                           "start_ms": 1000, "end_ms": 4000}]},
                transcript="Cancellation window is seven days", readable="Cancellation window is seven days", conn=c)


def test_meetings_search_and_full_transcript_respect_source_access(api):
    seed(api)
    _, _, attempt = setup_attempt(api)
    token = attempt["token"]
    args = {"q": "Cancellation", "person": "Dana"}
    err, result = call(api, "hub_meeting_search", args, token=token)
    assert not err
    assert result == api.get('/api/v2/meetings/search', params=args, headers=headers(token)).json()
    assert [r["id"] for r in result["results"]] == ["shared"]
    # The old name of the route keeps answering for installed clients.
    assert api.get('/api/v2/recordings/search', params=args, headers=headers(token)).json() == result
    assert result["results"][0]["passages"][0]["start_ms"] == 1000
    for rid in ("private", "note", "missing"):
        err, _ = call(api, "hub_meeting_read", {"id": rid}, token=token)
        assert err
    err, first = call(api, "hub_meeting_read", {"id": "shared", "limit": 12}, token=token)
    assert not err and first["next_offset"] == 12
    err, rest = call(api, "hub_meeting_read", {"id": "shared", "offset": 12}, token=token)
    assert not err and rest["next_offset"] is None
    assert first["text"] + rest["text"] == "Cancellation window is seven days"
    # Human owner/attendee access remains available; bots do not borrow that identity.
    assert api.get('/api/v2/meetings/transcript?id=private', headers=headers("ben-test")).status_code == 200
    assert api.get('/api/v2/meetings/transcript?id=private', headers=headers("cara-test")).status_code == 403
    page = api.get('/api/v2/meetings/search?limit=1', headers=headers()).json()
    assert page["next_offset"] == 1
    next_page = api.get('/api/v2/meetings/search?limit=1&offset=1', headers=headers()).json()
    assert page["results"][0]["id"] != next_page["results"][0]["id"]
    assert not api.get('/api/v2/meetings/search?since=2999-01-01', headers=headers()).json()["results"]
    assert api.get('/api/v2/meetings/search?since=2026-02-01&until=2026-01-01', headers=headers()).status_code == 422
    machine = runner(api)
    assert api.get('/api/v2/meetings/search', headers=headers(machine["token"])).status_code == 403
    # Deleting or making a meeting private removes it from both discovery and direct reads immediately.
    with api.app.state.store.transaction() as c:
        meta = meetings.get("shared", c)["metadata"]
        meta["private"] = True
        meetings.snapshot(meta, conn=c)
    assert not api.get('/api/v2/meetings/search', headers=headers(token)).json()["results"]
    assert api.get('/api/v2/meetings/transcript?id=shared', headers=headers(token)).status_code == 404
    with api.app.state.store.transaction() as c:
        meta["private"] = None
        meetings.snapshot(meta, conn=c)
    assert api.get('/api/v2/meetings/transcript?id=shared', headers=headers(token)).status_code == 404
    with api.app.state.store.transaction() as c:
        meta["private"] = False
        meetings.snapshot(meta, conn=c)
        c.execute("INSERT INTO media_control(meeting_id,deleted_at) VALUES('shared','2026-09-22T00:00:00Z')")
    assert not api.get('/api/v2/meetings/search', headers=headers(token)).json()["results"]
    assert api.get('/api/v2/meetings/transcript?id=shared', headers=headers(token)).status_code == 404
    assert api.get('/api/v2/meetings/transcript?id=shared', headers=headers()).status_code == 404
