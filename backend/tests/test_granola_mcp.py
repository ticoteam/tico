"""Fake official OAuth/MCP: no live accounts, network or secrets."""

import asyncio
import json
import logging

import httpx

from backend.auth import Identity
from backend.tests.helpers.api import headers, setup_attempt  # noqa: F401
from backend.tests.helpers.media import import_meeting
from backend.tests.helpers.member_bots import botops  # noqa: F401
from backend.tests.helpers.granola_mcp import BASE, Provider


def test_registration_device_encryption_status_and_disconnect(api, caplog):
    caplog.set_level(logging.DEBUG)
    provider = Provider(api)
    assert provider.connect()["connected"]
    assert provider.connect("ben-test")["connected"]
    assert sum(path == "/oauth2/register" for path, _ in provider.calls) == 1
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM granola_connections WHERE actor='human:ana'").fetchone()
        assert b"fake-refresh-sensitive" not in bytes(row["ciphertext"])
        assert "fake-" not in row["metadata"]
        assert c.execute("SELECT count(*) FROM credentials WHERE id=?", (row["id"],)).fetchone()[0] == 0
    for path in (BASE, BASE + "/connect/status", "/api/v2/credentials"):
        text = api.get(path, headers=headers("ana-test")).text
        assert all(
            secret not in text
            for secret in ("fake-access-sensitive", "fake-refresh-sensitive", "fake-device-sensitive")
        )
    assert "fake-refresh-sensitive" not in caplog.text
    assert api.delete(BASE + "/connect", headers=headers("ana-test")).status_code == 200
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM granola_connections WHERE actor='human:ana'").fetchone()
        assert c.execute("SELECT 1 FROM granola_connections WHERE actor='human:ben'").fetchone()
    assert any(path == "/oauth2/revoke" for path, _ in provider.calls)


def test_free_sync_dedup_private_notes_and_person_isolation(api):
    provider = Provider(api)
    provider.connect()
    existing = import_meeting(api, notes="API notes", source="granola", external_id="not_12345678901234", private=True)
    provider.sync()
    status = api.get(BASE, headers=headers("ana-test")).json()
    assert status["last_synced_at"] and status["plan_hint"] == "free" and not status["last_error"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM meetings").fetchone()[0] == 1
        row = c.execute("SELECT metadata FROM meetings WHERE id=?", (existing["id"],)).fetchone()
        assert json.loads(row[0])["private"]
    record = api.get("/api/meetings/" + existing["id"], headers=headers("ana-test")).json()
    assert record["notes"] == "API notes" and "Never import this" not in json.dumps(record)
    assert not api.get(BASE, headers=headers("ben-test")).json()["connected"]
    assert api.post(BASE + "/sync", headers=headers("ben-test")).json()["state"] == "off"
    assert api.get(BASE + "?person=ana", headers=headers("ben-test")).json()["mode"] == "off"
    _, _, attempt = setup_attempt(api)
    for method, path in (
        ("get", BASE),
        ("post", BASE + "/sync"),
        ("post", BASE + "/connect"),
        ("get", BASE + "/connect/status"),
        ("delete", BASE + "/connect"),
    ):
        assert getattr(api, method)(path, headers=headers(attempt["token"])).status_code == 403


def test_401_refresh_and_429_backoff(api):
    provider = Provider(api)
    provider.connect()
    provider.unauthorized, provider.rate_limit = True, True
    provider.sync()
    assert not api.get(BASE, headers=headers("ana-test")).json()["last_error"]
    assert 3 in provider.sleeps
    assert provider.service.load("human:ana")[2]["access_token"] == "fake-access-refreshed"
    assert len(provider.sleeps) >= 4  # Every MCP request is paced, including initialize and retry.


def test_refresh_failure_needs_signin_and_health_privacy(api):
    provider = Provider(api)
    provider.connect("ben-test")
    provider.unauthorized, provider.refresh_error = True, True
    provider.sync("human:ben")
    status = api.get(BASE, headers=headers("ben-test")).json()
    assert status["needs_signin"] and not status["connected"]
    assert "fake-" not in json.dumps(status)
    # Health is scoped even for an administrator who is not the owner.
    for token, visible in (("ben-test", True), ("ana-test", True), ("cara-test", False)):
        response = api.get("/api/v2/health", headers=headers(token))
        assert response.status_code == 200
        assert ("Granola needs sign-in again" in response.text) == visible


def test_structured_and_xml_notes_exclude_private_fields_and_keep_provider_id():
    from backend.granola_mcp import GranolaMCP

    value = GranolaMCP.content(
        {
            "content": [
                {
                    "type": "text",
                    "text": '<meetings><meeting id="not_12345678901234"><title>Planning</title><enhanced_notes>Ship it</enhanced_notes><private_notes>Never import</private_notes></meeting></meetings>',
                }
            ]
        }
    )
    item = GranolaMCP.item(value["meetings"][0])
    assert item.external_id == "not_12345678901234" and item.notes == "Ship it"
    assert "Never import" not in item.model_dump_json()
    assert (
        GranolaMCP.item({"id": "uuid", "note_id": "not_12345678901234", "ai_summary": "Ship it"}).external_id
        == item.external_id
    )
    assert GranolaMCP.item({"id": "uuid", "notes": "Private note", "private_notes": "Private note"}) is None
    assert (
        GranolaMCP.content({"content": [{"type": "text", "text": "Ana: Ship it"}]}, allow_text=True)["transcript"]
        == "Ana: Ship it"
    )


def test_botops_mcp_uses_the_human_requester(api, botops):
    from backend.tests.helpers.member_bots import turn
    from backend.tests.helpers.mcp import call

    provider = Provider(api)
    provider.connect("cara-test")
    attempt = turn(api, botops, person="cara-test", text="Sync my Granola notes")
    error, result = call(api, "tico_meeting_granola_status", token=attempt["token"])
    assert not error and result["connected"] and result["email"] is None
    error, result = call(api, "tico_meeting_granola_sync", token=attempt["token"])
    assert not error and result["state"] == "syncing"

    async def finish():
        await asyncio.gather(*list(provider.service.jobs.values()))

    api.portal.call(finish)
    assert provider.service.status(Identity("human:cara", "human", "cara@acme.example"))["imported_count"] == 1
    assert not provider.service.status(Identity("human:ana", "owner", "ana@acme.example"))["connected"]


def test_sql_cannot_read_connection_secrets(api):
    Provider(api).connect()
    response = api.post("/api/v2/sql", json={"sql": "SELECT * FROM granola_connections"}, headers=headers("ana-test"))
    assert response.status_code in (403, 422)
    assert "fake-access-sensitive" not in response.text


def test_transient_refresh_keeps_token_and_retries(api, caplog):
    failure = "network"
    caplog.set_level(logging.DEBUG)
    provider = Provider(api)
    provider.connect()
    previous = provider.handle

    def handle(request):
        if request.url.path == "/oauth2/token":
            if failure in ("network", "registration_network"):
                if failure == "network":
                    raise httpx.ConnectError("fake-refresh-sensitive", request=request)
                return httpx.Response(401, json={"error": "invalid_client"})
            if failure == "bad_response":
                return httpx.Response(200, text="fake-refresh-sensitive")
            return httpx.Response(int(failure), text="fake-refresh-sensitive")
        if failure == "registration_network" and request.url.path == "/oauth2/register":
            raise httpx.ConnectError("fake-refresh-sensitive", request=request)
        return previous(request)

    provider.service.transport = httpx.MockTransport(handle)
    provider.now += 3600
    provider.sync()
    saved = provider.service.load("human:ana")
    assert saved[2]["refresh_token"] == "fake-refresh-sensitive"
    assert saved[1]["state"] == "connected" and not saved[1]["needs_signin"]
    assert saved[1]["last_error"] and saved[1]["retry_after"] > provider.now
    assert all(
        secret not in caplog.text
        for secret in ("fake-refresh-sensitive", "fake-access-sensitive", "fake-device-sensitive")
    )
    provider.service.transport = httpx.MockTransport(previous)
    provider.sync()
    assert provider.service.load("human:ana")[1]["last_sync"]


def test_inactive_person_connection_deleted_and_revoked(api):
    field, value = "hidden", True
    provider = Provider(api)
    provider.connect()
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata(key,value) VALUES('people',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps({"people": [{"id": "ana", field: value}]}),),
        )
    provider.sync()
    assert provider.service.load("human:ana") is None

    async def finish():
        await asyncio.gather(*list(provider.service.revocations))

    api.portal.call(finish)
    assert any(path == "/oauth2/revoke" for path, _ in provider.calls)
    assert not any(path == "/mcp" for path, _ in provider.calls)


def test_untrusted_verification_urls_rejected(api):
    url = "https://granola.ai.example.com/device"
    provider = Provider(api)
    previous = provider.handle

    def handle(request):
        response = previous(request)
        if request.url.path == "/oauth2/device_authorization":
            return httpx.Response(200, json={**response.json(), "verification_uri_complete": url})
        return response

    provider.service.transport = httpx.MockTransport(handle)
    response = api.post(BASE + "/connect", headers=headers("ana-test"))
    assert response.status_code == 503 and url not in response.text
    assert provider.service.load("human:ana") is None
