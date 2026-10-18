"""Settings > AI providers > Tico AI Gateway (backend/gateway_connection.py), against a fake Gateway on localhost."""

import json
import logging
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.credentials import CredentialCipher
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, put, ready, runner  # noqa: F401
from backend.tests.test_credentials import FakeKMS
from clients import gateway as G

KEY = "tgw-synthetic-fixture-key-0042"
OTHER = "tgw-synthetic-fixture-key-0099"


class FakeGateway:
    """The Gateway's `GET <base>/key`: what each key answers is set per test (`keys`); every call is kept."""

    def __init__(self):
        self.keys, self.calls = {}, []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                key = self.headers.get("Authorization", "").removeprefix("Bearer ")
                owner.calls.append((self.path, key))
                status, body = owner.keys.get(key, (401, {"error": {"code": "invalid_key"}}))
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/gateway"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def active(self, key, used):
        self.keys[key] = (200, {"status": "active", "month": "2026-10", "cap_usd": 50.0, "spent_usd": 50.0 * used,
                                "used": used, "warning": used >= 0.8, "cap_reached": used >= 1})

    def revoked(self, key):
        self.keys[key] = (401, {"error": {"code": "key_revoked", "message": "This key was revoked"}})


@contextmanager
def fake_gateway():
    fake = FakeGateway()
    try:
        yield fake
    finally:
        fake.server.shutdown()


def setup(api, monkeypatch):
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
        return " ".join(str([tuple(r) for r in c.execute(f"SELECT * FROM {t}")])
                        for t in ("registry_metadata", "events", "idempotency", "credentials"))


def test_one_key_saves_seals_and_clears_and_is_never_read_back(api, monkeypatch, caplog):
    setup(api, monkeypatch)
    caplog.set_level(logging.DEBUG)
    with fake_gateway() as fake:
        fake.active(KEY, 0.25)
        assert get(api, "providers/gateway")["connected"] is False
        saved = save(api, fake)
        assert saved["connected"] and saved["source"] == "settings" and saved["preview"] == "tgw…042"
        assert saved["state"] == "connected" and saved["working"] and saved["message"] == "Connected."
        assert fake.calls == [("/v1/gateway/key", KEY)]
        # Never in a read, a stored row, an event, a receipt or a log line: only sealed, and only its preview shown.
        assert KEY not in json.dumps(get(api, "providers/gateway")) and KEY not in json.dumps(saved)
        assert KEY not in everything(api) and KEY not in caplog.text
        with api.app.state.store.read() as c:
            from backend import gateway_connection
            assert gateway_connection._row(c)["key"].startswith("sealed:v1:")
            assert gateway_connection.key(c, api.app.state.vault) == KEY
        # The URL alone changes with the key kept; a stale editor is refused.
        again = put(api, "providers/gateway", {"url": fake.url, "expected_revision": _revision(api)})
        assert again["preview"] == "tgw…042"
        put(api, "providers/gateway", {"key": OTHER, "url": fake.url, "expected_revision": 0}, expected=409)
        cleared = delete(api, "providers/gateway")
        assert cleared["connected"] is False and cleared["state"] == "none" and cleared["preview"] == ""
        with api.app.state.store.read() as c:
            assert "key" not in gateway_connection._row(c)


def test_only_the_owner_changes_it_and_only_the_owner_sees_its_state(api, monkeypatch):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.9)
        save(api, fake, token="ben-test", expected=403)
        save(api, fake)
        delete(api, "providers/gateway", "ben-test", expected=403)
        post(api, "providers/gateway/check", {}, "ben-test", expected=403)
        theirs = get(api, "providers/gateway", "ben-test")
        assert theirs["connected"] is True and "preview" not in theirs and "state" not in theirs
        mine = get(api, "providers/gateway")
        assert mine["state"] == "warning" and "90%" in mine["message"]


def test_tico_models_are_on_only_while_a_key_is_set(api, monkeypatch):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        assert "tico" not in get(api, "providers")["enabled"]
        assert "tico" not in get(api, "models")["enabled_providers"]
        # Ticking it does nothing without a key: it follows the connection, never the owner's list.
        view = get(api, "providers")
        put(api, "providers", {"enabled": ["anthropic", "tico"], "expected_revision": view["revision"]})
        assert get(api, "providers")["enabled"] == ["anthropic"]
        save(api, fake)
        view = get(api, "providers")
        assert view["enabled"] == ["anthropic", "tico"]
        assert "tico" in get(api, "models")["enabled_providers"]
        # Picked like any other model, with Tico names and what runs underneath.
        put(api, "providers", {"enabled": ["anthropic"], "model": "tico-mid-1.0", "expected_revision": view["revision"]})
        view = get(api, "providers")
        assert view["default"] == {"runtime": "pi", "model": "tico-mid-1.0"} and "tico" in view["enabled"]
        rows = {m["id"]: m for m in get(api, "models")["models"] if m["provider"] == "tico"}
        assert [(rows[m]["label"], rows[m]["description"]) for m in sorted(rows)] == [
            ("Tico Cheap 1.0", "Claude Haiku 5.5 underneath"), ("Tico Mid 1.0", "Claude Sonnet 5.5 underneath"),
            ("Tico Power 1.0", "Claude Opus 5.5 underneath")]
        delete(api, "providers/gateway")
        assert "tico" not in get(api, "providers")["enabled"]
        with api.app.state.store.read() as c:
            stored = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='providers'").fetchone()[0])
        assert "tico" not in stored["enabled"]


def test_openrouter_never_serves_a_tico_model_and_no_default_fallback_spends_the_key(api, monkeypatch):
    from backend import providers
    tico = providers.MODEL_BY_ID["tico-mid-1.0"]
    assert not providers.serves("openrouter", tico) and providers.serves("tico", tico)
    assert providers.serves("openrouter", providers.MODEL_BY_ID["kimi-k3"])
    record = {"enabled": ["tico"], "runtime": "pi", "model": "tico-mid-1.0", "auto_fallback": True}
    assert providers.default_fallbacks(record) == []


def test_a_key_the_environment_sets_shows_as_connected_and_is_not_edited_here(api, monkeypatch):
    setup(api, monkeypatch)
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    with fake_gateway() as fake:
        monkeypatch.setenv("TICO_GATEWAY_URL", fake.url + "/decisions")   # Dev 178 names the decisions URL
        fake.active(KEY, 0.5)
        shown = get(api, "providers/gateway")
        assert shown["connected"] and shown["source"] == "environment" and shown["env"] == "TICO_GATEWAY_KEY"
        assert shown["editable"] is False and shown["preview"] == "" and shown["url"] == fake.url
        assert "tico" in get(api, "providers")["enabled"]
        save(api, fake, expected=409)
        delete(api, "providers/gateway", expected=409)
        assert post(api, "providers/gateway/check", {})["state"] == "connected"
        assert fake.calls == [("/v1/gateway/key", KEY)]


@pytest.mark.parametrize("answer,state,status,words", [
    (lambda f: f.active(KEY, 0.79), "connected", None, "Connected."),
    (lambda f: f.active(KEY, 0.8), "warning", "warn", "80% of its monthly Gateway cap"),
    (lambda f: f.active(KEY, 1.0), "cap_reached", "bad", "Gateway cap reached"),
    (lambda f: f.revoked(KEY), "revoked", "bad", "Not working: Tico has revoked this gateway key"),
    (lambda f: None, "invalid", "bad", "Not working: the Gateway does not know this key"),
])
def test_the_gateways_word_on_the_key_reaches_settings_and_the_owners_health(api, monkeypatch, answer, state,
                                                                              status, words):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        answer(fake)
        shown = save(api, fake)
        assert shown["state"] == state and words in shown["message"]
        assert shown["working"] is (state not in ("revoked", "invalid"))
        rows = {r["id"]: r for r in get(api, "health")["checks"]}
        assert (rows["gateway"]["status"], words in rows["gateway"]["summary"]) == (status, True) if status \
            else "gateway" not in rows
        # The connection's people only: an admin who does not own it is not told.
        assert "gateway" not in {r["id"] for r in get(api, "health", "ben-test")["checks"]}


def test_an_unreachable_gateway_is_not_called_broken(api, monkeypatch):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        url = fake.url
    shown = put(api, "providers/gateway", {"key": KEY, "url": url, "expected_revision": 0})
    assert shown["state"] == "unreachable" and shown["working"] is True


def test_a_check_is_about_the_key_it_was_made_with(api, monkeypatch):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.revoked(KEY)
        assert save(api, fake)["state"] == "revoked"
        fake.active(OTHER, 0.1)
        with api.app.state.store.transaction() as c:
            from backend import gateway_connection
            fingerprint = gateway_connection._fingerprint(c)
        put(api, "providers/gateway", {"key": OTHER, "url": fake.url, "expected_revision": _revision(api)})
        # An answer about the old key that lands after the new one was saved is dropped.
        with api.app.state.store.transaction() as c:
            gateway_connection.record_check(c, {"state": "revoked", "message": "old"}, fingerprint, H.now())
        assert get(api, "providers/gateway")["state"] == "connected"


def put_bot_on(api, bot, model):
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()[0])
        c.execute("UPDATE bot_config SET config_json=? WHERE bot=?",
                  (json.dumps({**config, "runtime": "pi", "harness": "pi", "model": model}), bot))


def test_only_a_run_on_a_tico_model_is_handed_the_key(api, monkeypatch):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
        put_bot_on(api, "ops", "tico-mid-1.0")
        put_bot_on(api, "finance", "kimi-k3")
        machine = runner(api)
        for bot in ("ops", "finance"):
            assign(api, machine, bot)
        ready(api, machine, ["ops", "finance"])
        post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
        attempt = claim(api, machine, "ops")
        assert attempt["credential_vault"] is True and attempt["config"]["model"] == "tico-mid-1.0"
        handed = {r["env"]: r for r in get(api, "credential-runtime", attempt["token"])["credentials"]}
        assert handed["TICOAI_GATEWAY_KEY"]["value"] == KEY
        assert handed["TICOAI_GATEWAY_URL"]["value"] == fake.url
        post(api, "chat/finance", {"text": "Use only the synthetic fixture"})
        other = claim(api, machine, "finance")
        assert other["credential_vault"] is False
        assert get(api, "credential-runtime", other["token"])["credentials"] == []
        # Clearing the key takes it from the next fetch; nothing about it is in an event.
        delete(api, "providers/gateway")
        assert get(api, "credential-runtime", attempt["token"])["credentials"] == []
        assert KEY not in everything(api)


def test_a_tico_bot_is_ready_with_the_connection_not_an_openrouter_grant(api, monkeypatch):
    setup(api, monkeypatch)
    from backend import readiness
    put_bot_on(api, "ops", "tico-mid-1.0")
    machine = runner(api)
    assign(api, machine, "ops")
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {
        "schema_version": 1, "runtimes": {"pi": {"installed": True}}, "bots": {"ops": {"ready": True}}}},
        token=machine["token"])
    with api.app.state.store.read() as c:
        verdict = readiness.can_run(c, "ops", "pi", model="tico-mid-1.0")
    assert verdict["can_run"] is False and "Tico AI Gateway is not connected" in verdict["problem"]
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
    with api.app.state.store.read() as c:
        assert readiness.can_run(c, "ops", "pi", model="tico-mid-1.0")["can_run"] is True


def finish(api, machine, attempt, error, **result):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "gateway"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/events", {"events": [{"seq": 1, "kind": "error", "payload": {
        "error": error, "limit": bool(result.get("limited"))}}]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": 1, "text": "", **result},
         machine["token"])


def test_at_the_cap_the_bot_says_gateway_cap_reached(api, monkeypatch):
    setup(api, monkeypatch)
    put_bot_on(api, "ops", "tico-mid-1.0")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    finish(api, machine, attempt, "Gateway cap reached: this gateway key has used its monthly spend limit (cap_reached)",
           limited=True)
    with api.app.state.store.read() as c:
        status = H.status(c, "ops")
    assert status["state"] == "limited" and status["focus"].startswith("Gateway cap reached at ")
    assert "usage limit" not in status["focus"]


def test_a_rate_limit_is_not_called_a_cap(api, monkeypatch):
    setup(api, monkeypatch)
    put_bot_on(api, "ops", "tico-mid-1.0")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    finish(api, machine, attempt, "429 rate limited (rate_limited)", limited=True)
    with api.app.state.store.read() as c:
        assert "Gateway cap reached" not in H.status(c, "ops")["focus"]


def test_a_revoked_key_says_the_connection_is_not_working(api, monkeypatch):
    setup(api, monkeypatch)
    put_bot_on(api, "ops", "tico-mid-1.0")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    finish(api, machine, attempt, "Tico AI Gateway: api key revoked (key_revoked)",
           auth_rejected={"runtime": "pi", "reason": "api key revoked (key_revoked)"})
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["focus"].startswith("The Tico AI Gateway key is not working")


def test_the_servers_default_gateway_url_does_not_hide_the_saved_one(api, monkeypatch):
    """Dev 178's settings always carry a gateway URL, Tico HQ's decisions endpoint by default; only one the
    environment chose wins over the URL saved here."""
    setup(api, monkeypatch)
    settings = api.app.state.store.settings
    monkeypatch.setattr(settings, "gateway_url", G.decisions_url(), raising=False)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        assert save(api, fake)["url"] == fake.url
        monkeypatch.setattr(settings, "gateway_url", "https://gateway.example.test/v1/gateway/decisions", raising=False)
        assert get(api, "providers/gateway")["url"] == "https://gateway.example.test/v1/gateway"


def test_base_url_takes_one_setting_for_both_endpoints():
    assert G.base_url("") == G.DEFAULT_BASE
    assert G.base_url("https://gw.example/v1/gateway/decisions/") == "https://gw.example/v1/gateway"
    assert G.decisions_url("https://gw.example/v1/gateway") == "https://gw.example/v1/gateway/decisions"
    assert G.cap_reached("429 {\"error\":{\"code\":\"cap_reached\"}}") and not G.cap_reached("429 rate_limited")
