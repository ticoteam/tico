"""Offline regressions for sign-in, retry scheduling and source-owned Granola summaries."""
import asyncio
import base64
import json

import httpx
import pytest

from backend.auth import Identity
from backend.tests.test_granola_mcp import Provider, api, headers  # noqa: F401


def metadata(provider, **changes):
    saved = provider.service.load("human:ana")
    saved[1].update(changes)
    provider.service.save(*saved)
    return saved


@pytest.mark.parametrize("claim,expected", [
    ({"email": "account@example.com", "email_verified": False}, None),
])
def test_account_email_is_display_only_and_omits_missing_or_unverified_claims(api, claim, expected):
    provider = Provider(api)
    original = provider.handle
    payload = base64.urlsafe_b64encode(json.dumps(claim).encode()).decode().rstrip("=")
    token = "header." + payload + ".signature"

    def handle(request):
        response = original(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={**response.json(), "id_token": token})
        return response

    provider.service.transport = httpx.MockTransport(handle)
    assert provider.connect()["email"] == expected
    saved = provider.service.load("human:ana")
    assert "id_token" not in saved[2] and token not in json.dumps(saved[1])


def test_refresh_rate_limit_keeps_token_and_honors_provider_deadline(api):
    provider = Provider(api)
    provider.connect()
    original = provider.handle

    def handle(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(429, headers={"Retry-After": "900"}, json={})
        return original(request)

    provider.service.transport = httpx.MockTransport(handle)
    provider.now += 3600
    provider.sync()
    saved = provider.service.load("human:ana")
    assert saved[2]["refresh_token"] == "fake-refresh-sensitive"
    assert saved[1]["last_error"] == "rate_limited: refresh_token"
    assert saved[1]["retry_after"] == provider.now + 900


@pytest.mark.parametrize("failure", ["network", "server"])
def test_transient_transcript_failure_never_infers_free_plan(api, failure):
    provider = Provider(api)
    provider.paid = True
    provider.connect()
    metadata(provider, account_plan_hint="paid", plan_hint="paid")
    original = provider.handle

    def handle(request):
        if request.url.path == "/mcp" and json.loads(request.content).get("params", {}).get("name") == "get_meeting_transcript":
            if failure == "network":
                raise httpx.ConnectError("fake-provider-sensitive", request=request)
            if failure == "rate_limit":
                return httpx.Response(429, headers={"Retry-After": "1"}, json={})
            if failure == "server":
                return httpx.Response(503, json={})
            return httpx.Response(200, json={"result": {"isError": True}})
        return original(request)

    provider.service.transport = httpx.MockTransport(handle)
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["plan_hint"] == "paid" and not meta["transcripts_unavailable"]
    assert provider.service.load("human:ana")[2]["refresh_token"] == "fake-refresh-sensitive"
    if failure in ("server", "tool_error"):
        assert meta["last_sync"] and meta["imported_count"] == 1
    else:
        assert meta["last_error"].startswith("rate_limited" if failure == "rate_limit" else "unreachable")


def test_disconnect_blocks_trigger_during_cancellation_and_cannot_resurrect_connection(api):
    provider = Provider(api)
    provider.connect()
    old = provider.service.load("human:ana")

    async def exercise():
        started, cancelling, finish_cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def blocked(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                await finish_cancel.wait()
                raise

        provider.service.rpc = blocked
        who = Identity("human:ana", "human", "ana@acme.example")
        await provider.service.trigger(who)
        await started.wait()
        original_job = provider.service.jobs[who.actor]
        deleting = asyncio.create_task(provider.service.disconnect(who))
        await cancelling.wait()
        assert (await provider.service.trigger(who))["state"] == "off"
        assert provider.service.jobs[who.actor] is original_job
        finish_cancel.set()
        await asyncio.wait_for(deleting, 1)
        assert not provider.service.jobs and provider.service.load(who.actor) is None
        # A late save from an old worker cannot insert the deleted row.
        await asyncio.to_thread(provider.service.save, *old)
        assert provider.service.load(who.actor) is None
        assert (await provider.service.trigger(who))["state"] == "off"

    api.portal.call(exercise)


def test_regenerated_summary_keeps_human_notes_and_other_fields(api):
    provider = Provider(api)
    provider.paid = True
    provider.connect()
    provider.sync()
    with api.app.state.store.read() as c:
        rid = c.execute("SELECT id FROM meetings").fetchone()[0]
    record = api.get("/api/meetings/" + rid, headers=headers()).json()
    original_notes = record["notes"]
    edit = api.post("/api/meetings/" + rid + "/edit", json={"version": record["version"],
                    "title": "Human title", "note": "Human meeting log"}, headers=headers())
    assert edit.status_code == 200
    with api.app.state.store.transaction() as c:
        from backend.media import save
        from backend.meetings import get
        stored = get(rid, c)
        save(c, stored["metadata"], notes="Human summary")
    before = api.get("/api/meetings/" + rid, headers=headers()).json()
    original = provider.handle

    def handle(request):
        if request.url.path == "/mcp" and json.loads(request.content).get("params", {}).get("name") == "get_meetings":
            return httpx.Response(200, json={"result": {"structuredContent": {"meetings": [
                {"id": "not_12345678901234", "title": "Provider title", "summary": "Regenerated summary"}]}}})
        return original(request)

    provider.service.transport = httpx.MockTransport(handle)
    provider.sync()
    after = api.get("/api/meetings/" + rid, headers=headers()).json()
    assert after["notes"] == "Human summary"
    for key in ("title", "note", "started", "participants", "turns", "private", "review_state", "transcript_readable"):
        assert after[key] == before[key]
    versions = api.get("/api/meetings/" + rid + "/versions", headers=headers()).json()
    assert original_notes in [v["notes"] for v in versions["versions"]]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM meetings").fetchone()[0] == 1
