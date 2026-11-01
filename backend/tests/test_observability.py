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


@pytest.mark.parametrize("half", [{}, {"sentry_server_dsn": DSN}, {"observability_environment": "production"}])
def test_default_never_initializes_sdk(tmp_path, monkeypatch, half):
    def forbidden(**kw):
        pytest.fail("Disabled telemetry must not initialize a client")
    monkeypatch.setattr(sentry_sdk, "Client", forbidden)
    telemetry = Observability(Settings(db_path=tmp_path / "db", **half))
    telemetry.start()
    telemetry.capture("request", RuntimeError(SECRET), 500)
    assert browser_config(telemetry.settings, "human:ana") == {}


EMAIL, TOKEN, TEXT = "ana@acme.example", "Bearer tico_live_" + "k" * 24, "Task: renew the Acme lease by Friday"


def events(transport):
    return [item.payload.json for env in transport.envelopes for item in env.items if item.type == "event"]


@pytest.fixture
def reporting_api(request, monkeypatch, recording):
    """The test_api fixture's install with server error reporting on, named "Acme Co", at release 0.3.99."""
    from backend.tests import test_api
    plain = test_api.api_settings
    monkeypatch.setattr(test_api, "api_settings", lambda tmp: replace(
        plain(tmp), observability_environment="test", sentry_server_dsn=DSN, instance_name="Acme Co"))
    monkeypatch.setenv("TICO_VERSION", "v0.3.99")
    client = request.getfixturevalue("api")

    def failing():
        raise ValueError(f"{EMAIL} {TOKEN} {TEXT}")
    client.app.add_api_route("/api/v2/tasks/{tid}/explode", failing, methods=["GET"])
    client.app.router.routes.insert(0, client.app.router.routes.pop())  # ahead of the API's catch-all

    def locked():
        raise sqlite3.OperationalError(f"database is locked for {EMAIL}")
    client.app.add_api_route("/api/v2/notes/{nid}/locked", locked, methods=["GET"])
    client.app.router.routes.insert(0, client.app.router.routes.pop())
    return client


def test_route_and_runner_failures_each_send_one_tagged_event(reporting_api, recording):
    from backend.tests.test_api import headers, runner
    client = TestClient(reporting_api.app, raise_server_exceptions=False)
    assert client.get("/api/v2/tasks/t-secret-1/explode", headers=headers()).status_code == 500
    machine = runner(reporting_api)
    sent = reporting_api.post("/api/v2/runners/errors", headers=headers(machine["token"]), json={
        "version": "0.5.4", "panics": [{"file": "src/runner.rs", "line": 812}]})
    assert sent.status_code == 200, sent.text
    route, panic = events(recording)
    for event in (route, panic):
        assert event["release"] == "0.3.99" and event["tags"]["instance"] == "acme-co"
    assert route["tags"]["route"] == "GET /api/v2/tasks/{tid}/explode" and route["tags"]["status"] == "500"
    assert route["exception"]["values"][0]["type"] == "ValueError"
    assert {"filename": "backend/app.py", "function": "request_guard"}.items() <= route[
        "exception"]["values"][0]["stacktrace"]["frames"][0].items()
    assert panic["tags"]["source"] == "runner" and panic["tags"]["runner"] == "0.5.4"
    assert panic["exception"]["values"][0] == {"type": "RunnerPanic", "stacktrace": {"frames": [
        {"filename": "runner-rs/src/runner.rs", "lineno": 812, "in_app": True}]}}
    wire = json.dumps(events(recording))
    assert "t-secret-1" not in wire and EMAIL not in wire and TOKEN not in wire and TEXT not in wire
    # A runner posting a burst gets five reports out of it; only a runner reports a runner's panics.
    burst = [{"file": "src/lanes.rs", "line": n} for n in range(1, 21)]
    assert reporting_api.post("/api/v2/runners/errors", headers=headers(machine["token"]),
                              json={"panics": burst}).status_code == 200
    assert len(events(recording)) == 2 + 5
    # The storage handler's 503 names its route too.
    assert client.get("/api/v2/notes/n-1/locked", headers=headers()).status_code == 503
    assert events(recording)[-1]["tags"]["route"] == "GET /api/v2/notes/{nid}/locked"
    assert reporting_api.post("/api/v2/runners/errors", headers=headers(), json={"panics": []}).status_code == 403


def test_scrubber_drops_email_token_and_customer_text(settings):
    dirty = {"tags": {"source": "request", "instance": EMAIL, "route": "GET /api/v2/tasks?q=" + TEXT,
                      "runner": TOKEN, "status": "500", "user": EMAIL},
             "message": TEXT, "logentry": {"message": TEXT}, "extra": {"task": TEXT, "comment": TEXT},
             "request": {"headers": {"Authorization": TOKEN, "Cookie": TOKEN}, "query_string": "token=" + TOKEN,
                         "data": {"body": TEXT}, "cookies": {"s": TOKEN}},
             "user": {"email": EMAIL}, "breadcrumbs": {"values": [{"message": TEXT, "data": {"to": EMAIL}}]},
             "exception": {"values": [{"type": TOKEN, "value": f"{EMAIL} {TOKEN} {TEXT}", "stacktrace": {"frames": [
                 {"filename": "backend/app.py", "lineno": 7, "function": "create_task",
                  "vars": {"body": TEXT, "email": EMAIL}, "context_line": TEXT, "pre_context": [TOKEN]},
                 {"filename": "/home/" + EMAIL + "/x.py", "lineno": 1, "function": TEXT}]}}]}}
    clean = sanitize_event(dirty)
    wire = json.dumps(clean)
    assert EMAIL not in wire and TOKEN not in wire and TEXT not in wire and "acme" not in wire
    assert clean["tags"] == {"app": "tico", "source": "request", "status": "500"}
    assert clean["exception"]["values"][0] == {"type": "RequestError", "stacktrace": {"frames": [
        {"filename": "backend/app.py", "lineno": 7, "in_app": True, "function": "create_task"}]}}


def test_hub_limits_what_one_runner_token_sends(settings, recording):
    from backend.models import RunnerPanic
    telemetry = Observability(settings)
    telemetry.start()
    same = [RunnerPanic(file="src/runner.rs", line=9)] * 2
    telemetry.capture_runner("r1", "0.5.4", same, now=0)
    telemetry.capture_runner("r2", "0.5.4", same, now=0)          # another runner's own allowance
    for n in range(10):                                            # a token posting new lines over and over
        telemetry.capture_runner("r1", "0.5.4", [RunnerPanic(file="src/lanes.rs", line=n * 5 + k) for k in range(5)],
                                 now=1)
    assert len(events(recording)) == 1 + 1 + 19                    # 20 an hour for r1, its repeat dropped
    telemetry.capture_runner("r1", "0.5.4", same[:1], now=3601)
    assert len(events(recording)) == 22
    telemetry.close()


@pytest.mark.parametrize("named, env_id, expected", [
    ({"instance_name": "Tidy Prod"}, "", "tidy-prod"), ({"company_name": "Acme Co"}, "e-1", "acme-co"),
    ({}, "e-1", "e-1"), ({}, "", "")])
def test_instance_tag_falls_back_from_setting_to_team_name_to_install_id(settings, recording, named, env_id, expected):
    telemetry = Observability(replace(settings, environment_id=env_id, **named))
    telemetry.start()
    assert telemetry.instance == expected
    telemetry.close()


def test_operator_test_error_beside_a_docker_server(tmp_path, recording, monkeypatch, capsys):
    """As `docker compose exec server python -m backend.observability test`: no TICO_DB, no TICO_ENVIRONMENT_ID."""
    from backend import observability
    with sqlite3.connect(tmp_path / "hub.sqlite") as c:
        c.execute("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT)")
        c.execute("INSERT INTO registry_metadata VALUES('docker-environment-id', '\"acme-7f3a\"')")
    for name in ("TICO_DB", "TICO_ENVIRONMENT_ID", "TICO_INSTANCE_NAME", "TICO_RELEASE", "TICO_REHEARSAL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TICO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TICO_TEAM_NAME", "Acme Co")
    monkeypatch.setenv("TICO_SENTRY_SERVER_DSN", DSN)
    monkeypatch.setenv("TICO_OBSERVABILITY_ENVIRONMENT", "test")
    monkeypatch.setenv("TICO_VERSION", "0.3.99")
    assert observability.main(["test"]) == 0
    (event,) = events(recording)
    assert event["tags"]["source"] == "test" and event["tags"]["instance"] == "acme-co"
    assert event["release"] == "0.3.99" and event["exception"]["values"][0]["type"] == "TestError"
    assert "nothing in it" not in json.dumps(event) and "Sent test error" in capsys.readouterr().out
    monkeypatch.delenv("TICO_TEAM_NAME")
    assert observability.reporting_settings().environment_id == "acme-7f3a"
    monkeypatch.setenv("TICO_REHEARSAL", "1")
    assert observability.main(["test"]) == 1 and "off" in capsys.readouterr().out
