"""Privacy contracts exercised against the real SDK and authenticated ASGI app."""

import hashlib
import hmac
import json
import sqlite3
import threading
from dataclasses import replace

import pytest
import sentry_sdk
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.observability import Observability, browser_config, public_dsn, sanitize_event
from backend.store import H, Problem
from backend.tests.test_api import api
from backend.tests.test_auth import signing

DSN = "https://" + "a" * 32 + "@o123.ingest.us.sentry.io/123"
SECRET = "server-only-secret-" * 3


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=tmp_path / "hub.db", observability_environment="test",
                    observability_id_secret=SECRET, posthog_key="phc_public_test",
                    posthog_host="https://us.i.posthog.com", sentry_dsn=DSN,
                    sentry_server_dsn=DSN, release_id="test-123",
                    test_identities={"owner": Identity("human:ana", "owner"),
                                     "human": Identity("human:ben", "human")})


@pytest.mark.parametrize("host", ["https://us.i.posthog.com.evil.test", "https://user@us.i.posthog.com"])
def test_posthog_rejects_other_destinations(settings, host):
    assert browser_config(replace(settings, posthog_host=host, sentry_dsn=""), "a") == {}


class RecordingTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


@pytest.fixture
def recording(monkeypatch):
    transport = RecordingTransport()
    real_client = sentry_sdk.Client
    monkeypatch.setattr(sentry_sdk, "Client", lambda **kw: real_client(transport=transport, **kw))
    return transport


def test_sdk_wire_allowlist(settings, recording):
    telemetry = Observability(settings)
    telemetry.start()
    assert telemetry.client is not None
    assert telemetry.client.integrations == {}
    dirty = {"tags": {"source": "request", "status": "503", "secret": SECRET},
             "request": {"url": SECRET, "headers": {"authorization": SECRET}, "data": SECRET},
             "user": {"email": SECRET}, "breadcrumbs": [{"message": SECRET}], "extra": {"sql": SECRET},
             "contexts": {"trace": {"trace_id": SECRET}}, "message": SECRET,
             "exception": {"values": [{"type": SECRET, "value": SECRET, "stacktrace": {"frames": [
                 {"filename": "backend/app.py", "lineno": 42, "vars": {"password": SECRET},
                  "abs_path": SECRET, "function": SECRET, "context_line": SECRET},
                 {"filename": SECRET, "lineno": 1}]}}]}}
    telemetry.client.capture_event(dirty)
    wire = [item.payload.json for env in recording.envelopes for item in env.items if item.type == "event"]
    assert len(wire) == 1
    assert SECRET not in json.dumps(wire)
    assert set(wire[0]) <= {"event_id", "level", "platform", "tags", "exception", "environment", "release"}
    assert wire[0]["exception"]["values"][0]["stacktrace"]["frames"] == [
        {"filename": "backend/app.py", "lineno": 42, "in_app": True}]
    assert sanitize_event({"message": SECRET}) is None
    telemetry.close()


def test_default_never_initializes_sdk(tmp_path, monkeypatch):
    def forbidden(**kw):
        pytest.fail("Disabled telemetry must not initialize a client")
    monkeypatch.setattr(sentry_sdk, "Client", forbidden)
    telemetry = Observability(Settings(db_path=tmp_path / "db"))
    telemetry.start()
    telemetry.capture("request", RuntimeError(SECRET), 500)
    assert browser_config(telemetry.settings, "human:ana") == {}
