"""Helpers that other tests share, moved out of test_gateway_connection.py so they import no test file."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from backend.credentials import CredentialCipher
from backend.tests.helpers.api import get, headers, post, put
from backend.tests.helpers.credentials import FakeKMS
from backend.tests.helpers.decide_gateway import answer_all
from backend import bot_settings as BS


KEY = "tgw-synthetic-fixture-key-0042"


OTHER = "tgw-synthetic-fixture-key-0099"


# The Gateway's error table (Dev 181, tico-services PR #11): code -> (HTTP status, message). The same on every endpoint.
ERRORS = {
    "unknown_key": (401, "Unknown gateway key."),
    "revoked_key": (401, "This gateway key was revoked."),
    "rate_limited": (429, "Too many refused keys from this address. Try again later."),
    "cap_reached": (429, "The Tico AI Gateway's monthly cap is reached."),
    "unknown_model": (400, "Unknown model. Tico models: tico-low-1.0, tico-medium-1.0, tico-high-1.0."),
    "unsupported_field": (
        400,
        "Tico models here take text and tools only: no images, audio, files or more than one choice.",
    ),
    "bad_request": (400, "The request is not a chat completion the gateway can send."),
    "upstream_refused": (400, "The model refused this request as it was sent."),
    "no_route": (503, "No route for this model meets the request's filters."),
    "upstream_unavailable": (503, "The model is unavailable. Try again later."),
}


class FakeGateway:
    """The Gateway's `GET <base>/key`: what each key answers is set per test (`keys`); every call is kept. An unknown
    key gets 401 `unknown_key`. Errors come in the Gateway's two shapes: nested `{"error": {"code": ...}}` (chat) or a
    flat `{"error": "<code>"}` (decisions)."""

    def __init__(self):
        self.keys, self.calls, self.moved = {}, [], ""
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                key = self.headers.get("Authorization", "").removeprefix("Bearer ")
                owner.calls.append((self.path, key))
                if owner.moved:  # `moved`: every path redirects there
                    self.send_response(302)
                    self.send_header("Location", owner.moved + self.path)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status, body = owner.keys.get(key, (401, {"error": {"code": "unknown_key", "message": "Unknown key"}}))
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # `<base>/decisions`: Tico Decide 1.0 answers every question
                key = self.headers.get("Authorization", "").removeprefix("Bearer ")
                owner.calls.append((self.path, key))
                asked = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                data = json.dumps(
                    {
                        "model": "tico-decide-low",
                        "answers": answer_all(asked["questions"]),
                        "usage": {"input_tokens": 120, "output_tokens": 0, "cost_usd": 0.00001},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/gateway"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def active(self, key, used):
        self.keys[key] = (
            200,
            {
                "status": "active",
                "month": "2026-10",
                "cap_usd": 50.0,
                "spent_usd": 50.0 * used,
                "used": used,
                "warning": used >= 0.8,
                "cap_reached": used >= 1,
            },
        )

    def refuse(self, key, status, code, flat=False):
        message = ERRORS.get(code, (status, "Refused"))[1]
        self.keys[key] = (
            status,
            {"error": code} if flat else {"error": {"code": code, "message": message, "type": "invalid_request_error"}},
        )

    def revoked(self, key, flat=False):
        self.refuse(key, 401, "revoked_key", flat)


@contextmanager
def fake_gateway():
    fake = FakeGateway()
    try:
        yield fake
    finally:
        fake.server.shutdown()


def ready(api, r, bots):
    """test_api's `ready` from a computer that runs bots as their own user, the only kind a Tico model runs on
    (execution.claim_refusal holds it anywhere else)."""
    row = {"ready": True, "repository_present": True, "configuration_valid": True}
    return post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "readiness": {"schema_version": 1, "isolated": True, "runtimes": {}, "bots": {bot: row for bot in bots}},
        },
        token=r["token"],
    )


def setup(api, monkeypatch, page_checks=False):
    from backend import gateway_connection

    monkeypatch.setattr(gateway_connection, "_tried", {})
    if not page_checks:  # opening the page asks the Gateway on a thread; only the test about that lets it
        monkeypatch.setattr(gateway_connection, "check_soon", lambda *args, **kwargs: None)
    monkeypatch.delenv("TICO_GATEWAY_KEY", raising=False)
    monkeypatch.delenv("TICO_GATEWAY_URL", raising=False)
    api.app.state.store.settings.credential_kms_key = "test-key"
    api.app.state.vault.cipher = CredentialCipher("test-key", FakeKMS())


def delete(api, path, token="ana-test", expected=200):
    r = api.delete("/api/v2/" + path, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def save(api, fake, key=KEY, token="ana-test", expected=200, revision=None):
    revision = _revision(api) if revision is None else revision
    return put(api, "providers/gateway", {"key": key, "url": fake.url, "expected_revision": revision}, token, expected)


def _revision(api):
    with api.app.state.store.read() as c:
        from backend import gateway_connection

        return int(gateway_connection._row(c).get("revision") or 0)


def everything(api):
    """Every table a key could leak into, as one string."""
    with api.app.state.store.read() as c:
        return " ".join(
            str([tuple(r) for r in c.execute(f"SELECT * FROM {t}")])
            for t in ("registry_metadata", "events", "idempotency", "credentials")
        )


def put_bot_on(api, bot, model):
    with api.app.state.store.transaction() as c:
        config = BS.read(c, bot)
        BS.write(c, bot, {**config, 'runtime': 'pi', 'harness': 'pi', 'model': model})


def handed_to(api, machine, attempt, expected=200):
    """What the computer running `attempt` is handed for the Gateway, by env name."""
    got = get(api, f"runner-gateway-credentials?attempt={attempt['id']}", machine["token"], expected=expected)
    return {r["env"]: r for r in got["credentials"]} if expected == 200 else got


def finish(api, machine, attempt, error, **result):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "gateway"}, machine["token"])
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {"events": [{"seq": 1, "kind": "error", "payload": {"error": error, "limit": bool(result.get("limited"))}}]},
        machine["token"],
    )
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "failed", "last_seq": 1, "text": "", **result},
        machine["token"],
    )
