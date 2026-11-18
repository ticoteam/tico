"""Offline regressions for sign-in, retry scheduling and source-owned Granola summaries."""

import asyncio
import base64
import json
from datetime import datetime, timezone

import httpx
import pytest

from backend.auth import Identity
from backend.granola_mcp import GranolaMCP
from backend.tests.helpers.granola_mcp import Provider, headers  # noqa: F401


def metadata(provider, **changes):
    saved = provider.service.load("human:ana")
    saved[1].update(changes)
    provider.service.save(*saved)
    return saved


@pytest.mark.parametrize(
    "claim,expected",
    [
        ({"email": "account@example.com", "email_verified": False}, None),
    ],
)
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
        if (
            request.url.path == "/mcp"
            and json.loads(request.content).get("params", {}).get("name") == "get_meeting_transcript"
        ):
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
        started, canceling, finish_cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def blocked(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                canceling.set()
                await finish_cancel.wait()
                raise

        provider.service.rpc = blocked
        who = Identity("human:ana", "human", "ana@acme.example")
        await provider.service.trigger(who)
        await started.wait()
        original_job = provider.service.jobs[who.actor]
        deleting = asyncio.create_task(provider.service.disconnect(who))
        await canceling.wait()
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
    edit = api.post(
        "/api/meetings/" + rid + "/edit",
        json={"version": record["version"], "title": "Human title", "note": "Human meeting log"},
        headers=headers(),
    )
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
            return httpx.Response(
                200,
                json={
                    "result": {
                        "structuredContent": {
                            "meetings": [
                                {
                                    "id": "not_12345678901234",
                                    "title": "Provider title",
                                    "summary": "Regenerated summary",
                                }
                            ]
                        }
                    }
                },
            )
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


PLACEHOLDER = (
    "No AI summary has been generated for this meeting, but it has a transcript. Read it with get_meeting_transcript."
)


def dated_provider(api, paid=False):
    """A provider whose clock is a day after the fake meeting, so the meeting is inside the revisit window."""
    provider = Provider(api)
    provider.paid = paid
    provider.now = datetime(2026, 10, 1, 1, tzinfo=timezone.utc).timestamp()
    answer_with(provider)
    provider.connect()
    return provider


def answer_with(provider, summary=None, transcript=None):
    """Granola's answers with the meeting dated and the summary or transcript replaced; the rest is the provider's."""
    original = provider.handle

    def handle(request):
        tool = json.loads(request.content).get("params", {}).get("name") if request.url.path == "/mcp" else None
        meeting = {"id": "not_12345678901234", "title": "Planning", "date": "2026-10-01T00:00:00Z"}
        if tool == "list_meetings":
            return text_answer({"meetings": [meeting]})
        if tool == "get_meetings" and summary is not None:
            return httpx.Response(
                200,
                json={"result": {"structuredContent": {"meetings": [{**meeting, "summary_markdown": summary}]}}},
            )
        if tool == "get_meeting_transcript" and transcript is not None:
            return text_answer({"transcript": transcript})
        return original(request)

    provider.service.transport = httpx.MockTransport(handle)


def text_answer(value):
    return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": json.dumps(value)}]}})


def stored_meeting(api):
    with api.app.state.store.read() as c:
        rid = c.execute("SELECT id FROM meetings").fetchone()[0]
    return api.get("/api/meetings/" + rid, headers=headers()).json()


def test_granola_placeholder_is_never_a_summary_and_a_real_summary_stays_with_a_transcript():
    note = {"id": "not_12345678901234", "title": "Planning", "summary_markdown": PLACEHOLDER.upper()}
    assert GranolaMCP.item(note) is None
    assert GranolaMCP.item(note, "Ana: Ship it").notes == ""
    summarized = {"id": "not_12345678901234", "title": "Planning", "summary_markdown": "## Decisions\nShip it"}
    assert GranolaMCP.item(summarized, "Ana: Ship it").notes == "## Decisions\nShip it"


def test_granola_names_its_microphone_and_files_other_audio_as_others():
    turns = GranolaMCP.named_turns("Microphone: Hello there\nSpeaker: Hi\nSystem: Yes\nDana: Thanks", "Ana Test")
    assert [t["speaker"] for t in turns] == ["Ana Test", "Others", "Others", "Dana"]


def test_granola_revisit_clears_a_stored_summary_once_granola_only_has_the_placeholder(api):
    provider = dated_provider(api, paid=True)
    provider.sync()
    assert stored_meeting(api)["notes"] == "## Decisions\nShip it"
    answer_with(provider, summary=PLACEHOLDER)
    provider.sync()
    record = stored_meeting(api)
    assert record["notes"] == "" and record["turns"]


def test_granola_placeholder_on_a_meeting_that_gains_a_transcript_clears_its_summary(api):
    provider = dated_provider(api)
    provider.sync()
    assert stored_meeting(api)["notes"] == "## Decisions\nShip it"
    provider.paid = True
    answer_with(provider, summary=PLACEHOLDER)
    provider.sync()
    record = stored_meeting(api)
    assert record["notes"] == "" and record["turns"]


def test_granola_summary_a_person_edited_is_kept_when_granola_has_none(api):
    provider = dated_provider(api, paid=True)
    provider.sync()
    rid = stored_meeting(api)["id"]
    with api.app.state.store.transaction() as c:
        from backend.media import save
        from backend.meetings import get

        save(c, get(rid, c)["metadata"], notes="Edited by hand")
    answer_with(provider, summary=PLACEHOLDER)
    provider.sync()
    assert stored_meeting(api)["notes"] == "Edited by hand"


def test_granola_microphone_is_the_note_taker_and_other_audio_is_others(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO humans(id,name,email) VALUES('ana','Ana Test','ana@acme.example')")
        c.execute("UPDATE humans SET name='Ana Test' WHERE id='ana'")
    provider = dated_provider(api, paid=True)
    answer_with(provider, transcript="Microphone: I'm just testing.\nSpeaker: Yes, it works.\nSystem: Hello.")
    provider.sync()
    assert [t["speaker"] for t in stored_meeting(api)["turns"]] == ["Ana Test", "Others", "Others"]
