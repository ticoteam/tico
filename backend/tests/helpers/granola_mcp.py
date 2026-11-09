"""Helpers that other tests share, moved out of test_granola_mcp.py so they import no test file."""

import json
import httpx
from backend.tests.helpers.api import headers


BASE = "/api/v2/meetings/granola"


class Provider:
    def __init__(self, api):
        self.api = api
        self.service = api.app.state.granola
        self.now = 2_000_000_000.0
        self.calls, self.sleeps = [], []
        self.poll_errors = []
        self.refresh_error = False
        self.unauthorized = False
        self.rate_limit = False
        self.paid = False
        self.transcript_denied = False
        self.registration_rejected = False
        self.service.clock = lambda: self.now
        self.service.transport = httpx.MockTransport(self.handle)
        self.service.sleep = self.sleep

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def handle(self, request):
        from urllib.parse import parse_qs

        path = request.url.path
        self.calls.append((path, request))
        if path == "/oauth2/register":
            body = json.loads(request.content)
            assert body["token_endpoint_auth_method"] == "none"
            # Granola's real server refuses a registration without a redirect_uris array (verified live).
            if not isinstance(body.get("redirect_uris"), list):
                return httpx.Response(
                    400,
                    json={"error": "invalid_client_metadata", "error_description": "redirect_uris must be an array"},
                )
            return httpx.Response(200, json={"client_id": "public-client"})
        if path == "/oauth2/device_authorization":
            if self.registration_rejected:
                self.registration_rejected = False
                return httpx.Response(401, json={"error": "invalid_client"})
            data = parse_qs(request.content.decode())
            assert data["resource"] == ["https://mcp.granola.ai/mcp"]
            return httpx.Response(
                200,
                json={
                    "device_code": "fake-device-sensitive",
                    "user_code": "ABCD",
                    "verification_uri": "https://granola.ai/device",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        if path == "/oauth2/token":
            data = parse_qs(request.content.decode())
            assert data["resource"] == ["https://mcp.granola.ai/mcp"]
            if data["grant_type"] == ["refresh_token"]:
                if self.refresh_error:
                    return httpx.Response(400, json={"error": "invalid_grant", "description": "fake-refresh-sensitive"})
                return httpx.Response(200, json={"access_token": "fake-access-refreshed", "expires_in": 3600})
            if self.poll_errors:
                return httpx.Response(400, json={"error": self.poll_errors.pop(0)})
            return httpx.Response(
                200,
                json={
                    "access_token": "fake-access-sensitive",
                    "refresh_token": "fake-refresh-sensitive",
                    "expires_in": 3600,
                },
            )
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"revocation_endpoint": "https://mcp-auth.granola.ai/oauth2/revoke"})
        if path == "/oauth2/revoke":
            return httpx.Response(200, json={})
        assert path == "/mcp"
        body = json.loads(request.content)
        method = body["method"]
        if method == "initialize":
            if self.unauthorized:
                self.unauthorized = False
                return httpx.Response(401, json={})
            return httpx.Response(
                200, headers={"Mcp-Session-Id": "session"}, json={"result": {"protocolVersion": "2025-03-26"}}
            )
        assert request.headers["Mcp-Session-Id"] == "session"
        if method == "notifications/initialized":
            return httpx.Response(202)
        if self.rate_limit:
            self.rate_limit = False
            return httpx.Response(429, headers={"Retry-After": "3"}, json={})
        if method == "tools/list":
            tools = [
                {"name": "list_meetings", "inputSchema": {"properties": {"since": {}, "until": {}}}},
                {
                    "name": "get_meetings",
                    "inputSchema": {"properties": {"meeting_ids": {}}, "required": ["meeting_ids"]},
                },
            ]
            if self.paid:
                tools.append(
                    {
                        "name": "get_meeting_transcript",
                        "inputSchema": {"properties": {"meeting_id": {}}, "required": ["meeting_id"]},
                    }
                )
            return httpx.Response(200, json={"result": {"tools": tools}})
        tool = body["params"]["name"]
        if tool == "list_meetings":
            assert set(body["params"]["arguments"]) == {"since", "until"}
            value = {"meetings": [{"id": "not_12345678901234", "title": "Planning"}]}
        elif tool == "get_meetings":
            value = {
                "meetings": [
                    {
                        "id": "not_12345678901234",
                        "title": "Planning",
                        "date": "2026-10-01T00:00:00Z",
                        "summary_markdown": "## Decisions\nShip it",
                        "private_notes": "Never import this",
                    }
                ]
            }
        else:
            assert tool == "get_meeting_transcript"
            if self.transcript_denied:
                return httpx.Response(
                    200,
                    json={
                        "result": {
                            "isError": True,
                            "content": [{"type": "text", "text": "Paid plan required: permission denied"}],
                        }
                    },
                )
            value = {"transcript": "Ana: Ship it"}
        return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": json.dumps(value)}]}})

    def connect(self, token="ana-test"):
        response = self.api.post(BASE + "/connect", headers=headers(token))
        assert response.status_code == 200, response.text
        assert "device_code" not in response.text
        self.now += 5
        response = self.api.get(BASE + "/connect/status", headers=headers(token))
        assert response.status_code == 200, response.text
        return response.json()

    def sync(self, actor="human:ana"):
        self.api.portal.call(self.service.sync, actor)
