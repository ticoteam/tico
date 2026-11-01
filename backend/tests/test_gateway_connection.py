"""Settings > Computers > Tico AI Gateway (backend/gateway_connection.py), against a fake Gateway on localhost."""

import json
import logging
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.credentials import CredentialCipher
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, put, runner  # noqa: F401
from backend.tests.test_credentials import FakeKMS
from backend.tests.test_decide_gateway import answer_all
from backend.tests.test_judge import QUESTIONS
from clients import gateway as G

KEY = "tgw-synthetic-fixture-key-0042"
OTHER = "tgw-synthetic-fixture-key-0099"
# The Gateway's error table (Dev 181, tico-services PR #11): code -> (HTTP status, message). The same on every endpoint.
ERRORS = {
    "unknown_key": (401, "Unknown gateway key."),
    "revoked_key": (401, "This gateway key was revoked."),
    "rate_limited": (429, "Too many refused keys from this address. Try again later."),
    "cap_reached": (429, "The Tico AI Gateway's monthly cap is reached."),
    "unknown_model": (400, "Unknown model. Tico models: tico-cheap-1.0, tico-mid-1.0, tico-power-1.0."),
    "unsupported_field": (400, "Tico models here take text and tools only: no images, audio, files or more than one "
                               "choice."),
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
                if owner.moved:                             # `moved`: every path redirects there
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

            def do_POST(self):                          # `<base>/decisions`: Tico Decide 1.0 answers every question
                key = self.headers.get("Authorization", "").removeprefix("Bearer ")
                owner.calls.append((self.path, key))
                asked = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                data = json.dumps({"model": "tico-decide-1.0", "answers": answer_all(asked["questions"]),
                                   "usage": {"input_tokens": 120, "output_tokens": 0, "cost_usd": 0.00001}}).encode()
                self.send_response(200)
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

    def refuse(self, key, status, code, flat=False):
        message = ERRORS.get(code, (status, "Refused"))[1]
        self.keys[key] = (status, {"error": code} if flat else {"error": {"code": code, "message": message,
                                                                          "type": "invalid_request_error"}})

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
    return post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {
        "schema_version": 1, "isolated": True, "runtimes": {}, "bots": {bot: row for bot in bots}}}, token=r["token"])


def setup(api, monkeypatch, page_checks=False):
    from backend import gateway_connection
    monkeypatch.setattr(gateway_connection, "_tried", {})
    if not page_checks:   # opening the page asks the Gateway on a thread; only the test about that lets it
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


def test_decide_asks_with_the_saved_key_falls_back_when_it_is_cleared_and_the_environments_key_wins(api, monkeypatch):
    setup(api, monkeypatch)
    from backend import judge
    settings = api.app.state.store.settings
    monkeypatch.setattr(settings, "gateway_key", "", raising=False)
    own = lambda state, questions, label=None: {"model": "own", "answers": answer_all(questions), "usage": {}, "ms": 1}
    own.model, own.provider = "own", "own"
    monkeypatch.setattr(judge, "fallback_engine", lambda *args, **kwargs: own)
    api.app.state.judge = judge.primary_engine(settings, api.app.state.store, api.app.state.vault)   # as at start

    def decide():
        return post(api, "decisions", {"state": {"subject": "Invoice 4471"}, "questions": QUESTIONS})["model"]
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        assert decide() == "own"                                  # no key saved: the Gateway is not asked
        save(api, fake)
        assert decide() == "tico-decide-1.0" and fake.calls[-1] == ("/v1/gateway/decisions", KEY)
        delete(api, "providers/gateway")
        asked = len(fake.calls)
        assert decide() == "own" and len(fake.calls) == asked     # cleared: on to the next provider at once
        # A key the environment sets wins over a saved one, at the environment's URL.
        save(api, fake)
        monkeypatch.setattr(settings, "gateway_key", OTHER, raising=False)
        monkeypatch.setattr(settings, "gateway_url", fake.url + "/decisions", raising=False)
        api.app.state.judge = judge.primary_engine(settings, api.app.state.store, api.app.state.vault)
        assert decide() == "tico-decide-1.0" and fake.calls[-1] == ("/v1/gateway/decisions", OTHER)


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
        put(api, "providers", {"enabled": ["anthropic"], "model": "tico-general-medium-1.0", "expected_revision": view["revision"]})
        view = get(api, "providers")
        assert view["default"] == {"runtime": "pi", "model": "tico-general-medium-1.0"} and "tico" in view["enabled"]
        rows = {m["id"]: m for m in get(api, "models")["models"] if m["provider"] == "tico" and not m.get("deprecated")
                and not m.get("meeting")}
        assert len(rows) == 60 and all(m.endswith("-1.0") for m in rows)
        assert (rows["tico-general-medium-1.0"]["label"], rows["tico-general-medium-1.0"]["description"]) == (
            "General · Medium 1.0", "Claude Sonnet 5.5 underneath")
        assert rows["tico-sales-low-fast-1.0"]["tier"] == {"department": "sales", "level": "low", "fast": True,
                                                           "version": "1.0", "auto": False}
        delete(api, "providers/gateway")
        assert "tico" not in get(api, "providers")["enabled"]
        with api.app.state.store.read() as c:
            stored = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='providers'").fetchone()[0])
        assert "tico" not in stored["enabled"]


def new_bot(api, slug, expected=200, **choice):
    return post(api, "bots", {"slug": slug, "display_name": slug.title(), "description": "A test bot.",
                              "status": "active", "repo": "emp-" + slug, "thread_mode": "personal",
                              "owner_id": "ana", "owners": ["ana"], "runner_id": None, **choice}, expected=expected)


def test_the_old_lineup_is_retired_but_a_default_or_bot_already_on_it_keeps_it(api, monkeypatch):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
        view = get(api, "providers")
        refused = put(api, "providers", {"enabled": ["anthropic"], "model": "tico-cheap-1.0",
                                         "expected_revision": view["revision"]}, expected=422)
        assert "retired" in json.dumps(refused)
        # A default saved as Cheap 1.0 before 1.1 shipped.
        put(api, "providers", {"enabled": ["anthropic"], "model": "tico-general-medium-1.0", "expected_revision": view["revision"]})
        with api.app.state.store.transaction() as c:
            stored = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='providers'").fetchone()[0])
            c.execute("UPDATE registry_metadata SET value=? WHERE key='providers'",
                      (json.dumps({**stored, "runtime": "pi", "model": "tico-cheap-1.0"}),))
        # Saving something else keeps it, and a bot that names no model is made on it.
        view = get(api, "providers")
        put(api, "providers", {"enabled": ["anthropic", "openai"], "model": "tico-cheap-1.0",
                               "expected_revision": view["revision"]})
        assert get(api, "providers")["default"] == {"runtime": "pi", "model": "tico-cheap-1.0"}
        new_bot(api, "scout")
        with api.app.state.store.read() as c:
            assert json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (H.resolve_bot(c, "scout")["id"],)).fetchone()[0])[
                "model"] == "tico-cheap-1.0"
        # Choosing it fresh is refused: for a new bot, and as a default once the company has moved off it.
        new_bot(api, "scribe", expected=422, model="tico-cheap-1.0", harness="pi", effort="low")
        view = get(api, "providers")
        put(api, "providers", {"enabled": ["anthropic"], "model": "tico-general-low-1.0", "expected_revision": view["revision"]})
        view = get(api, "providers")
        put(api, "providers", {"enabled": ["anthropic"], "model": "tico-cheap-1.0", "expected_revision": view["revision"]},
            expected=422)
        # A bot already on it still runs there.
        put_bot_on(api, "ops", "tico-cheap-1.0")
        machine = runner(api)
        assign(api, machine, "ops")
        ready(api, machine, ["ops"])
        post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
        config = claim(api, machine, "ops")["config"]
        assert (config["runtime"], config["model"]) == ("pi", "tico-cheap-1.0")


def test_openrouter_never_serves_a_tico_model_and_no_default_fallback_spends_the_key(api, monkeypatch):
    from backend import providers
    tico = providers.MODEL_BY_ID["tico-mid-1.0"]
    assert not providers.serves("openrouter", tico) and providers.serves("tico", tico)
    assert providers.serves("openrouter", providers.MODEL_BY_ID["kimi-k3"])
    record = {"enabled": ["tico"], "runtime": "pi", "model": "tico-mid-1.0", "auto_fallback": True}
    assert providers.default_fallbacks(record) == []
    # A Tico company default doesn't reach the fallbacks through another provider on pi: that one's own model does.
    record = {**record, "enabled": ["anthropic", "openrouter", "tico"]}
    fallbacks = providers.default_fallbacks(record)
    assert not any(providers.gateway_model(row["model"]) for row in fallbacks), fallbacks
    assert {"runtime": "pi", "harness": "pi", "model": providers.recommended("openrouter")["id"]} in fallbacks


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
        # A restart with another key in the environment: the old key's word is not shown for it, and the stored check
        # names neither key.
        fake.revoked(OTHER)
        monkeypatch.setenv("TICO_GATEWAY_KEY", OTHER)
        assert get(api, "providers/gateway")["state"] == "unchecked"
        assert post(api, "providers/gateway/check", {})["state"] == "revoked"
        assert KEY not in everything(api) and OTHER not in everything(api)


def test_the_key_goes_only_to_an_https_or_exact_loopback_url_and_never_follows_a_redirect(api, monkeypatch):
    setup(api, monkeypatch)
    from backend import gateway_connection
    with fake_gateway() as fake, fake_gateway() as elsewhere:
        for lookalike in ("http://localhost.acme.example/v1/gateway", "http://127.0.0.1.acme.example/v1/gateway",
                          "http://localhost@acme.example/v1/gateway", "http://acme.example/v1/gateway", "https://"):
            put(api, "providers/gateway", {"key": KEY, "url": lookalike, "expected_revision": 0}, expected=422)
        # A redirect to another origin is not followed: the key reaches only the URL it was saved for.
        fake.active(KEY, 0.1)
        fake.moved = elsewhere.url.removesuffix("/v1/gateway")
        assert save(api, fake)["state"] == "not_ready"
        assert fake.calls == [("/v1/gateway/key", KEY)] and elsewhere.calls == []
        # The environment's URL is held to the same rule: no check is sent and no run is handed the key.
        monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
        monkeypatch.setenv("TICO_GATEWAY_URL", "http://localhost.acme.example/v1/gateway/decisions")
        shown = post(api, "providers/gateway/check", {})
        assert shown["state"] == "bad_url" and shown["working"] is False
        with api.app.state.store.read() as c:
            assert gateway_connection.run_credentials(c, api.app.state.vault, None, "tico-mid-1.0") == []
        assert len(fake.calls) == 1 and elsewhere.calls == []


@pytest.mark.parametrize("answer,state,status,words", [
    (lambda f: f.active(KEY, 0.79), "connected", None, "Connected."),
    (lambda f: f.active(KEY, 0.8), "warning", "warn", "80% of its monthly Gateway cap"),
    (lambda f: f.active(KEY, 1.0), "cap_reached", "bad", "Gateway cap reached"),
    (lambda f: f.refuse(KEY, 429, "cap_reached"), "cap_reached", "bad", "Gateway cap reached"),
    (lambda f: f.refuse(KEY, 429, "cap_reached", flat=True), "cap_reached", "bad", "Gateway cap reached"),
    (lambda f: f.revoked(KEY), "revoked", "bad", "Not working: key not recognised"),
    (lambda f: f.revoked(KEY, flat=True), "revoked", "bad", "Not working: key not recognised"),
    (lambda f: None, "unknown", "bad", "Not working: key not recognised"),
    (lambda f: f.refuse(KEY, 401, "unknown_key", flat=True), "unknown", "bad", "Not working: key not recognised"),
])
def test_the_gateways_word_on_the_key_reaches_settings_and_the_owners_health(api, monkeypatch, answer, state,
                                                                              status, words):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        answer(fake)
        shown = save(api, fake)
        assert shown["state"] == state and words in shown["message"]
        assert shown["working"] is (state not in ("revoked", "unknown"))
        rows = {r["id"]: r for r in get(api, "health")["checks"]}
        assert (rows["gateway"]["status"], words in rows["gateway"]["summary"]) == (status, True) if status \
            else "gateway" not in rows
        # The connection's people only: an admin who does not own it is not told.
        assert "gateway" not in {r["id"] for r in get(api, "health", "ben-test")["checks"]}


@pytest.mark.parametrize("flat", [False, True])
@pytest.mark.parametrize("code", list(ERRORS))
def test_every_gateway_code_on_the_key_endpoint_reads_plainly(code, flat):
    """Each row of the Gateway's table, in both its shapes: only a refused key says the connection is not working,
    the cap says so, and the rest say nothing about the key (never the Gateway's raw text, never the key)."""
    with fake_gateway() as fake:
        fake.refuse(KEY, ERRORS[code][0], code, flat)
        seen = G.check(KEY, fake.url)
    state = {"unknown_key": "unknown", "revoked_key": "revoked", "cap_reached": "cap_reached",
             "rate_limited": "busy"}.get(code, "unreachable")
    assert seen["state"] == state and seen["working"] is (code not in ("unknown_key", "revoked_key"))
    assert seen["message"] == G.MESSAGES[state] or state == "cap_reached"
    assert ERRORS[code][1] not in seen["message"] and KEY not in json.dumps(seen)


def test_an_unreachable_gateway_is_not_called_broken(api, monkeypatch):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        url = fake.url
    shown = put(api, "providers/gateway", {"key": KEY, "url": url, "expected_revision": 0})
    assert shown["state"] == "unreachable" and shown["working"] is True


@pytest.mark.parametrize("flat", [False, True])
def test_a_busy_or_down_gateway_never_replaces_what_it_said_about_the_key(api, monkeypatch, flat):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.85)
        assert save(api, fake)["state"] == "warning"
        fake.refuse(KEY, 429, "rate_limited", flat)            # 6 a minute per key; past that, rate_limited
        assert post(api, "providers/gateway/check", {})["state"] == "warning"
        url = fake.url
    assert post(api, "providers/gateway/check", {})["state"] == "warning"   # and the Gateway gone altogether
    # With nothing known yet, a busy Gateway says so, without calling the key broken.
    with fake_gateway() as fake:
        fake.refuse(OTHER, 429, "rate_limited", flat)
        shown = put(api, "providers/gateway", {"key": OTHER, "url": fake.url, "expected_revision": _revision(api)})
        assert shown["state"] == "busy" and shown["working"] is True
    assert url


def test_opening_the_page_asks_the_gateway_at_most_once_a_minute(api, monkeypatch):
    setup(api, monkeypatch, page_checks=True)
    from backend import gateway_connection
    clock = [1000.0]
    monkeypatch.setattr(gateway_connection.time, "monotonic", lambda: clock[0])
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)                                         # the save's own check
        def asked(n):
            for _ in range(100):
                if len(fake.calls) >= n:
                    break
                threading.Event().wait(0.05)
            return len(fake.calls)
        get(api, "providers/gateway")
        assert asked(2) == 2                                    # the owner opened the page: asked once
        get(api, "providers/gateway")
        get(api, "providers/gateway", "ben-test")               # a member's view never asks
        clock[0] += 59
        get(api, "providers/gateway")
        assert asked(3) == 2                                    # not again within the minute
        clock[0] += 1
        fake.active(KEY, 0.8)
        get(api, "providers/gateway")
        assert asked(3) == 3
        for _ in range(100):
            if get(api, "providers/gateway")["state"] == "warning":
                break
            threading.Event().wait(0.05)
        assert get(api, "providers/gateway")["state"] == "warning"


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


def test_a_plain_http_check_to_this_computer_never_goes_through_a_proxy(monkeypatch):
    """Plain HTTP is allowed only to this computer, so the key must go straight there: an environment HTTP proxy that
    does not list loopback in no_proxy would otherwise receive the request, Authorization header and all, in clear."""
    with fake_gateway() as fake, fake_gateway() as proxy:
        fake.active(KEY, 0.1)
        proxy.active(KEY, 0.1)                                # a proxy that answers as if it were the Gateway
        for name in ("http_proxy", "HTTP_PROXY"):
            monkeypatch.setenv(name, proxy.url.removesuffix("/v1/gateway"))
        for name in ("no_proxy", "NO_PROXY"):
            monkeypatch.delenv(name, raising=False)
        # The control: urllib's own opener sends it to the proxy.
        with pytest.raises(urllib.error.HTTPError):           # no key: the proxy answers 401
            urllib.request.build_opener().open(urllib.request.Request(fake.url + "/key"), timeout=5)
        assert proxy.calls and fake.calls == []
        proxy.calls.clear()
        assert G.check(KEY, fake.url)["state"] == "connected"
        assert fake.calls == [("/v1/gateway/key", KEY)] and proxy.calls == []


def put_bot_on(api, bot, model):
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()[0])
        c.execute("UPDATE bot_config SET config=? WHERE bot=?",
                  (json.dumps({**config, "runtime": "pi", "harness": "pi", "model": model}), bot))


def handed_to(api, machine, attempt, expected=200):
    """What the computer running `attempt` is handed for the Gateway, by env name."""
    got = get(api, f"runner-gateway-credentials?attempt={attempt['id']}", machine["token"], expected=expected)
    return {r["env"]: r for r in got["credentials"]} if expected == 200 else got


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
        assert attempt["gateway"] is True and attempt["config"]["model"] == "tico-mid-1.0"
        handed = handed_to(api, machine, attempt)
        assert handed["TICOAI_GATEWAY_KEY"]["value"] == KEY
        assert handed["TICOAI_GATEWAY_URL"]["value"] == fake.url
        post(api, "chat/finance", {"text": "Use only the synthetic fixture"})
        other = claim(api, machine, "finance")
        assert other["gateway"] is False and other["credential_vault"] is False
        assert handed_to(api, machine, other) == {}
        # Clearing the key takes it from the next fetch; nothing about it is in an event.
        delete(api, "providers/gateway")
        assert handed_to(api, machine, attempt) == {}
        assert KEY not in everything(api)


@pytest.mark.parametrize("on", ["pi_on_tico", "claude_with_a_tico_fallback"])
def test_the_key_goes_to_the_runner_alone_never_to_the_run(api, monkeypatch, on):
    """The run's own token (HUB_TOKEN, readable by every command the run starts) never gets the Gateway's key, whether
    its own model or only its fallback is a Tico model: only the computer running it does, while it runs, with the
    computer's own token."""
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
        put_bot_on(api, "ops", "tico-mid-1.0")
        if on == "claude_with_a_tico_fallback":
            with api.app.state.store.transaction() as c:
                config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
                c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (json.dumps({
                    **config, "runtime": "claude", "harness": "claude", "model": "opus-5-5",
                    "fallback": {"harness": "pi", "model": "tico-mid-1.0"}}),))
        machine, stranger = runner(api), runner(api, label="Other Mac")
        assign(api, machine, "ops")
        ready(api, machine, ["ops"])
        post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
        attempt = claim(api, machine, "ops")
        assert attempt["gateway"] is True and attempt["config"]["harness"] == on.split("_")[0]
        assert attempt["credential_vault"] is False                # nothing granted, so the run fetches nothing
        assert get(api, "credential-runtime", attempt["token"])["credentials"] == []
        handed_to(api, stranger, attempt, expected=403)            # a computer not running it
        handed_to(api, {"token": attempt["token"]}, attempt, expected=403)   # the run itself
        handed_to(api, {"token": "ana-test"}, attempt, expected=403)         # a person
        handed_to(api, machine, {"id": "no-such-attempt"}, expected=403)
        assert handed_to(api, machine, attempt)["TICOAI_GATEWAY_KEY"]["value"] == KEY
        finish(api, machine, attempt, "Tico AI Gateway: no Tico route for this request (no_route)")
        handed_to(api, machine, attempt, expected=409)             # not once it has ended
        assert KEY not in everything(api)


@pytest.mark.parametrize("change", ["bot_to_tico", "bot_from_tico", "default_to_tico", "check_to_tico"])
def test_the_key_follows_what_the_run_was_handed_not_a_later_setting(api, monkeypatch, change):
    """A run gets the key by the model its claim handed it out on: a setting changed while it runs (the bot's model,
    the company default it inherits, or its task's check model) neither hands the key to a run that started on
    another model nor takes it from one that started on a Tico model."""
    from backend import check_models
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
    started_on_tico = change == "bot_from_tico"
    put_bot_on(api, "ops", "tico-mid-1.0" if started_on_tico else "kimi-k3")
    if change == "default_to_tico":
        with api.app.state.store.transaction() as c:
            config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
            c.execute("UPDATE bot_config SET config=? WHERE bot='ops'",
                      (json.dumps({k: v for k, v in config.items() if k not in ("runtime", "harness", "model")}),))
        record = get(api, "providers")
        put(api, "providers", {"enabled": ["openrouter"], "runtime": "pi", "model": "kimi-k3",
                               "expected_revision": record["revision"]})
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    if change == "check_to_tico":
        post(api, "tasks", {"owner_id": "ops", "title": "Synthetic check", "body": "Use only the synthetic fixture"})
    else:
        post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    assert attempt["config"]["model"] == ("tico-mid-1.0" if started_on_tico else "kimi-k3")
    # While the run is under way, the setting moves.
    if change == "bot_to_tico":
        put_bot_on(api, "ops", "tico-mid-1.0")
    elif change == "bot_from_tico":
        put_bot_on(api, "ops", "kimi-k3")
    elif change == "default_to_tico":
        record = get(api, "providers")
        put(api, "providers", {"enabled": ["openrouter", "tico"], "runtime": "pi", "model": "tico-general-medium-1.0",
                               "expected_revision": record["revision"]})
    else:
        monkeypatch.setattr(check_models, "of", lambda c, task_id, bot: {
            "runtime": "pi", "model": "tico-mid-1.0", "harness": "pi"})
    assert attempt["gateway"] is started_on_tico
    handed = handed_to(api, machine, attempt)
    assert ("TICOAI_GATEWAY_KEY" in handed) is started_on_tico, (change, list(handed))
    # Its outcome is read the same way: a refusal by code only for a run that was on a Tico model.
    finish(api, machine, attempt, "Tico AI Gateway: no Tico route for this request (no_route)")
    with api.app.state.store.read() as c:
        assert ("No Tico route" in H.status(c, "ops")["focus"]) is started_on_tico
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key LIKE 'gateway-attempt:%'").fetchone()[0] == 0


def test_a_tico_bot_is_ready_with_the_connection_not_an_openrouter_grant(api, monkeypatch):
    setup(api, monkeypatch)
    from backend import readiness
    put_bot_on(api, "ops", "tico-mid-1.0")
    machine = runner(api)
    assign(api, machine, "ops")
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {
        "schema_version": 1, "isolated": True, "runtimes": {"pi": {"installed": True}}, "bots": {"ops": {"ready": True}}}},
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


@pytest.mark.parametrize("at, retry_after", [
    # Mid-month: the cap lifts at the next month's start, far beyond the 30-minute minimum wait.
    ("2026-10-15T12:00:00.000000Z", "2026-11-01 00:00 UTC"),
    # Last minutes of the month: the next month's start is 10 minutes off, so the minimum wait (LIMIT_COOLDOWN,
    # 30 minutes, execution.reset_cooldown) is what holds the bot, and the retry lands at 00:20.
    ("2026-10-31T23:50:00.000000Z", "2026-11-01 00:20 UTC"),
])
def test_at_the_cap_the_bot_says_gateway_cap_reached(api, monkeypatch, at, retry_after):
    # The clock is pinned so the retry time does not depend on when the suite runs.
    monkeypatch.setattr(H, "now", lambda: at)
    setup(api, monkeypatch)
    put_bot_on(api, "ops", "tico-mid-1.0")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    # The Gateway's Retry-After (here as the reset the runner reported) is not believed for the cap.
    soon = H.shift(H.now(), seconds=60)
    finish(api, machine, attempt, "Gateway cap reached: this gateway key has used its monthly spend limit (cap_reached)",
           limited=True, limit_resets_at=soon)
    with api.app.state.store.read() as c:
        status = H.status(c, "ops")
    assert status["state"] == "limited" and status["focus"].startswith("Gateway cap reached at ")
    assert "usage limit" not in status["focus"]
    # No retry this UTC month: the bot waits for the next one, or the minimum wait if that is sooner.
    assert status["focus"].endswith(f"retrying after {retry_after}"), status["focus"]
    assert claim(api, machine, "ops") is None


def tico_run(api, monkeypatch):
    setup(api, monkeypatch)
    put_bot_on(api, "ops", "tico-mid-1.0")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    return machine, claim(api, machine, "ops")


def ended(api, attempt):
    """The bot's status focus and what became of the run's job."""
    with api.app.state.store.read() as c:
        job = c.execute("SELECT j.state FROM jobs j JOIN attempts a ON a.job_id=j.id WHERE a.id=?",
                        (attempt["id"],)).fetchone()[0]
        return H.status(c, "ops"), job


@pytest.mark.parametrize("reported", [False, True])
def test_a_rate_limited_gateway_waits_its_retry_after_and_is_not_called_a_cap(api, monkeypatch, reported):
    machine, attempt = tico_run(api, monkeypatch)
    finish(api, machine, attempt, "Tico AI Gateway: rate limited after too many refused keys from this address; wait "
           "an hour (rate_limited)", limited=True,
           **({"limit_resets_at": H.shift(H.now(), seconds=3600)} if reported else {}))
    status, job = ended(api, attempt)
    assert status["state"] == "limited" and job == "queued"
    assert status["focus"].startswith("Tico AI Gateway rate limited at ") and "cap" not in status["focus"]
    # Never a tight retry: an early one with a refused key would extend the address block.
    with api.app.state.store.read() as c:
        from backend.execution import limit_cooldown
        assert limit_cooldown(c, "ops") >= G.RETRY_AFTER
    assert claim(api, machine, "ops") is None


def test_gateway_work_resumes_after_the_reported_rate_window(api, monkeypatch):
    machine, attempt = tico_run(api, monkeypatch)
    wait = 42
    at = H.now()
    monkeypatch.setattr(H, "now", lambda: at)
    reset = H.shift(at, seconds=wait)
    finish(api, machine, attempt, "Tico AI Gateway: rate limited; retrying after the Gateway's requested wait "
           "(rate_limited)", limited=True, limit_resets_at=reset)
    status, job = ended(api, attempt)
    assert job == "queued" and "refused keys" not in status["focus"]
    with api.app.state.store.read() as c:
        from backend.execution import limit_cooldown
        assert wait <= limit_cooldown(c, "ops") <= wait + 1
    assert claim(api, machine, "ops") is None
    at = H.shift(at, seconds=wait + 2)
    ready(api, machine, ["ops"])
    retried = claim(api, machine, "ops")
    assert retried and retried["job_id"] == attempt["job_id"] and retried["id"] != attempt["id"]


# What a company out of credit ends a Tico run with: the runner's plain words (runner-rs/src/hosts/pi.rs `plain_error`),
# or the Gateway's own 429 as an older runner passes it on (tico-services gateway/chat.py ERRORS).
OUT_OF_CREDIT = [
    "Tico models are paused: this company is out of credit. Your bots use their other models until credit is added "
    "(out_of_credit)",
    '429: {"code":"out_of_credit","message":"Tico models are paused: this company is out of credit.",'
    '"type":"insufficient_quota","param":null}',
]


@pytest.mark.parametrize("reported", [False, True])
@pytest.mark.parametrize("error", OUT_OF_CREDIT, ids=["plain", "older_runner"])
def test_out_of_credit_is_the_gateways_wait_and_tico_models_are_back_within_a_minute(api, monkeypatch, error, reported):
    """Waitlist spec v3 tests 9 and 12 h (app side): a company out of credit is not a provider login's limit. No
    login is held past the Gateway's minute; the bot asks the Gateway again after its Retry-After (a minute), so it is back on
    Tico models within about a minute of credit landing, and says so plainly."""
    from backend.execution import billing_limit, limit_cooldown
    machine, attempt = tico_run(api, monkeypatch)
    at = H.now()
    monkeypatch.setattr(H, "now", lambda: at)
    finish(api, machine, attempt, error, limited=True,
           **({"limit_resets_at": H.shift(at, seconds=G.OUT_OF_CREDIT_WAIT)} if reported else {}))
    status, job = ended(api, attempt)
    assert status["state"] == "limited" and job == "queued"
    assert status["focus"].startswith(G.PAUSED_MESSAGE + " at "), status["focus"]
    assert "{" not in status["focus"] and "usage limit" not in status["focus"] and "cap" not in status["focus"]
    with api.app.state.store.read() as c:
        # The Tico login waits only the Gateway's minute, never the 30-minute backoff.
        holds = [json.loads(r[0]) for r in c.execute("SELECT value FROM registry_metadata WHERE key LIKE 'login-hold:%'")]
        assert all(hold["until"] <= H.shift(at, seconds=G.OUT_OF_CREDIT_WAIT + 1) for hold in holds), holds
        assert G.OUT_OF_CREDIT_WAIT <= limit_cooldown(c, "ops") <= G.OUT_OF_CREDIT_WAIT + 1
        # Not an API key's plan or billing: Health doesn't send the person to check one.
        assert not billing_limit(c, "ops")
    assert claim(api, machine, "ops") is None
    at = H.shift(at, seconds=G.OUT_OF_CREDIT_WAIT + 2)
    with api.app.state.store.transaction() as c:
        # The computer has been reporting in all along, not waking from a silence.
        c.execute("UPDATE runners SET last_seen_at=?,awake_since=NULL", (at,))
    ready(api, machine, ["ops"])
    retried = claim(api, machine, "ops")
    assert retried and retried["job_id"] == attempt["job_id"] and retried["config"]["model"] == "tico-mid-1.0"


def test_a_rate_limited_bot_its_fallback_finished_for_is_back_on_tico_models_after_a_minute(api, monkeypatch):
    """Dev 351: the Gateway rate-limited the bot's Tico model and its other model finished the turn. The Tico login
    is held only the Gateway's minute, not a provider login's 30 minutes, so the bot's next turn after it is on Tico
    models again."""
    machine, attempt = tico_run(api, monkeypatch)
    at = H.now()
    monkeypatch.setattr(H, "now", lambda: at)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "gateway"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/events", {"events": [
        {"seq": 1, "kind": "error", "payload": {"error": "Tico AI Gateway: rate limited; retrying after the Gateway's "
                                                         "requested wait (rate_limited)", "limit": True}},
        {"seq": 2, "kind": "diagnostic", "payload": {"text": "pi hit its usage limit; running this turn on codex"}},
    ]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 2, "text": "Done.",
                                                     "fallback": "codex", "primary_limited": True}, machine["token"])
    with api.app.state.store.read() as c:
        holds = [json.loads(r[0]) for r in c.execute("SELECT value FROM registry_metadata WHERE key LIKE 'login-hold:%'")]
    assert holds and all(hold["until"] <= H.shift(at, seconds=G.GATEWAY_WAIT + 1) for hold in holds), holds
    at = H.shift(at, seconds=G.GATEWAY_WAIT + 2)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen_at=?,awake_since=NULL", (at,))
    post(api, "chat/ops", {"text": "Use only the synthetic fixture again"})
    ready(api, machine, ["ops"])
    again = claim(api, machine, "ops")
    assert again and again["config"]["model"] == "tico-mid-1.0" and not again["config"].get("primary_held")


@pytest.mark.parametrize("code", ["revoked_key", "unknown_key"])
def test_a_refused_key_says_the_connection_is_not_working(api, monkeypatch, code):
    machine, attempt = tico_run(api, monkeypatch)
    finish(api, machine, attempt, f"Tico AI Gateway: key not recognised (api key invalid: {code})",
           auth_rejected={"runtime": "pi", "reason": f"key not recognised (api key invalid: {code})"})
    assert ended(api, attempt)[0]["focus"].startswith("The Tico AI Gateway key is not working")


REFUSED = "Tico AI Gateway refused the request as sent: {message} ({code})"


# What the runner ends a refused Tico run with (runner-rs/src/hosts/pi.rs `plain_error`), and what the bot says.
@pytest.mark.parametrize("code,error,focus", [
    ("unknown_model", "Tico AI Gateway: this model isn't offered; Settings > Gateway lists the picks (unknown_model)",
     "The Tico AI Gateway doesn't offer this model; Settings > Gateway lists the picks"),
    ("unsupported_field", REFUSED, "The Tico AI Gateway refused the request as sent: " + ERRORS["unsupported_field"][1]),
    ("bad_request", REFUSED, "The Tico AI Gateway refused the request as sent: " + ERRORS["bad_request"][1]),
    ("upstream_refused", REFUSED, "The Tico AI Gateway refused the request as sent: " + ERRORS["upstream_refused"][1]),
    ("no_route", "Tico AI Gateway: no Tico route for this request (no_route)", "No Tico route for this request"),
])
def test_a_request_the_gateway_refused_as_sent_is_not_retried(api, monkeypatch, code, error, focus):
    machine, attempt = tico_run(api, monkeypatch)
    # Refused at once, before anything ran: still not back in the queue, since asking again gets the same answer.
    finish(api, machine, attempt, error.format(code=code, message=ERRORS[code][1]))
    status, job = ended(api, attempt)
    assert status["focus"] == f"{focus}. This run is saved for later", status["focus"]
    assert job == "uncertain" and status["state"] == "crashed"


def test_a_tico_model_on_a_runner_that_runs_bots_as_itself_says_so_once_and_is_not_retried(api, monkeypatch):
    """The runner refuses the turn before it starts (runner-rs/src/gateway_relay.rs NOT_ISOLATED): the bot says why
    and what to do, and the run waits for a person rather than coming back to the same runner."""
    machine, attempt = tico_run(api, monkeypatch)
    error = ("Tico models need a runner that runs bots as their own user (Tico's Docker runner). This computer can't, "
             "so pick another model or move the bot (not_isolated)")
    # Any other turn still says it started before it ends.
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": 0, "text": ""}, machine["token"],
         expected=409)
    post(api, f"attempts/{attempt['id']}/events", {"events": [
        {"seq": 1, "kind": "error", "payload": {"error": error}},
        {"seq": 2, "kind": "diagnostic", "payload": {"text": f"RuntimeError: execution interrupted ({error})"}}]},
         machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": 2, "text": ""}, machine["token"])
    status, job = ended(api, attempt)
    assert status["focus"] == error.removesuffix(" (not_isolated)") + ". This run is saved for later", status["focus"]
    stop = get(api, "status?bot=ops")["status"]["last_stop"]
    assert stop["run_id"] == attempt["id"] and stop["action"] == "review"
    assert "never started" in stop["text"] and "changed something" not in stop["reason"]
    assert "pick another model or move the bot" in stop["reason"]
    assert job == "uncertain" and claim(api, machine, "ops") is None
    # Nor does the review of stopped runs send it back to the same runner, sweep after sweep: it never started and
    # called no tool, so on a harness that reports its tool calls it would count as safe to resume. It waits for a
    # person to move the bot or change its model.
    from backend import execution
    from backend.execution import STALL_MAX
    monkeypatch.setattr(execution, "reports_tools", lambda *_: True)
    for _ in range(2):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE attempts SET finished_at=? WHERE id=?", (H.shift(H.now(), seconds=-STALL_MAX - 1),
                                                                     attempt["id"]))
            assert api.app.state.execution.auto_reconcile(c) == []
        assert ended(api, attempt)[1] == "uncertain" and claim(api, machine, "ops") is None
    # Its owner sees why in Needs you, not a bot that just went quiet.
    issue = next(i for i in api.get("/api/status", headers=headers()).json()["health_issues"]
                 if i["bot_id"] == "ops" and i["kind"] == "uncertain_work")
    assert issue["needs_person"] and "Docker runner" in issue["action"], issue
    assert issue["detail"].startswith(error.removesuffix(" (not_isolated)") + "."), issue


def test_a_refusal_for_want_of_isolation_is_shown_only_to_who_may_read_its_run(api, monkeypatch):
    """Ben reads the bot and its company task, not Ana's private one: an ordinary held run of the first doesn't let
    the refusal of the second show him its words."""
    setup(api, monkeypatch)
    put_bot_on(api, "ops", "tico-mid-1.0")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "tasks", {"owner_id": "ops", "title": "Company work", "body": "Use only the synthetic fixture"})
    ordinary = claim(api, machine, "ops")
    post(api, f"attempts/{ordinary['id']}/started", {"thread_id": "company"}, machine["token"])
    post(api, f"attempts/{ordinary['id']}/events", {"events": [
        {"seq": 1, "kind": "tool", "payload": {"tool": "Bash", "status": "started", "item_id": "b1"}},
        {"seq": 2, "kind": "error", "payload": {"error": "the harness stopped"}}]}, machine["token"])
    post(api, f"attempts/{ordinary['id']}/complete", {"outcome": "failed", "last_seq": 2, "text": ""}, machine["token"])
    post(api, "tasks", {"owner_id": "ops", "title": "Ana only", "body": "Private work.", "private": True})
    ready(api, machine, ["ops"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE run_backoff SET wait_until=NULL")        # the fast failure's wait is over
    refused = claim(api, machine, "ops")
    assert refused and refused["id"] != ordinary["id"]
    error = ("Tico models need a runner that runs bots as their own user (Tico's Docker runner). This computer can't, "
             "so pick another model or move the bot (not_isolated)")
    post(api, f"attempts/{refused['id']}/events", {"events": [{"seq": 1, "kind": "error", "payload": {"error": error}}]},
         machine["token"])
    post(api, f"attempts/{refused['id']}/complete", {"outcome": "failed", "last_seq": 1, "text": ""}, machine["token"])
    with api.app.state.store.read() as c:
        assert {r[0] for r in c.execute("SELECT state FROM jobs WHERE attempt_id IN (?,?)",
                                        (ordinary["id"], refused["id"]))} == {"uncertain"}
    held = lambda token: [i for i in get(api, "operations", token=token)["issues"] if i["kind"] == "uncertain_work"]
    assert [i["title"] for i in held("ana-test")] == ["ops can't run on its computer"], held("ana-test")
    seen = held("ben-test")
    assert seen and all("Docker" not in i["detail"] + str(i["action"]) for i in seen), seen


@pytest.mark.parametrize("acted", [False, True])
def test_an_unavailable_gateway_says_try_again(api, monkeypatch, acted):
    machine, attempt = tico_run(api, monkeypatch)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "gateway"}, machine["token"])
    events = [{"seq": 1, "kind": "tool", "payload": {"name": "read"}}] if acted else []
    events.append({"seq": len(events) + 1, "kind": "error", "payload": {
        "error": "Tico AI Gateway is unavailable, try again (upstream_unavailable)"}})
    post(api, f"attempts/{attempt['id']}/events", {"events": events}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": len(events), "text": ""},
         machine["token"])
    status, job = ended(api, attempt)
    assert status["focus"].startswith("Tico AI Gateway is unavailable, try again"), status["focus"]
    # Nothing ran: back in the queue. A run that had acted is kept for a person, as any interrupted run.
    assert (job, "back in the queue" in status["focus"]) == (("uncertain", False) if acted else ("queued", True))


def test_a_gateway_outage_retries_a_run_a_few_times_each_later_then_ends_it(api, monkeypatch):
    """An outage can't loop a run forever: each retry waits longer (and the bot's other work waits with it), and the
    last try ends the run with the Gateway's plain words."""
    from backend import execution
    from backend.repositories import metadata
    machine, attempt = tico_run(api, monkeypatch)
    waits = []
    for n in range(1, execution.NO_EFFECT_TRIES + 1):
        finish(api, machine, attempt, "Tico AI Gateway is unavailable, try again (upstream_unavailable)")
        status, job = ended(api, attempt)
        if n == execution.NO_EFFECT_TRIES:
            break
        assert job == "queued" and "retries after" in status["focus"] and f"try {n} of 3" in status["focus"]
        assert claim(api, machine, "ops") is None, "the bot waits before it asks the Gateway again"
        with api.app.state.store.transaction() as c:
            wait = metadata(c, execution.GATEWAY_WAIT + "ops")
            waits.append((H.parse_ts(wait["until"]) - H.parse_ts(wait["since"])).total_seconds())
            # The wait has passed.
            c.execute("UPDATE registry_metadata SET value=? WHERE key=?",
                      (json.dumps({**wait, "until": H.shift(H.now(), seconds=-1)}), execution.GATEWAY_WAIT + "ops"))
        attempt = claim(api, machine, "ops")
        assert attempt, "after the wait the run is tried again"
    assert waits == [execution.GATEWAY_BACKOFF, 2 * execution.GATEWAY_BACKOFF]
    assert job == "uncertain" and status["state"] == "crashed"
    assert status["focus"] == "Tico AI Gateway is unavailable, try again. This run is saved for later"


@pytest.mark.parametrize("error,acted,slow,job,words", [
    ("Tico AI Gateway: no Tico route for this request (no_route)", False, False, "uncertain",
     "No Tico route for this request. This run is saved for later"),
    ("Tico AI Gateway: no Tico route for this request (no_route)", True, False, "uncertain",
     "No Tico route for this request. This run is saved for later"),
    ("404 status code (no body)", False, False, "uncertain", "The Tico AI Gateway doesn't serve Tico models at its URL"),
    ("Tico AI Gateway is unavailable, try again (upstream_unavailable)", False, False, "queued", "retries after"),
    ("Tico AI Gateway is unavailable, try again (upstream_unavailable)", False, True, "queued", "retries after"),
    ("Tico AI Gateway is unavailable, try again (upstream_unavailable)", True, False, "uncertain",
     "Tico AI Gateway is unavailable, try again. This run is saved for later"),
])
def test_a_tico_fallback_the_gateway_refused_reads_as_the_gateway(api, monkeypatch, error, acted, slow, job, words):
    """A bot on another model whose own fallback is a Tico model: when its limit sent the turn to the fallback and the
    Gateway refused that, the run is the Gateway's, as on a Tico primary (and slow to fail is no reason to hold it)."""
    setup(api, monkeypatch)
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
        c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (json.dumps({
            **config, "runtime": "claude", "harness": "claude", "model": "opus-5-5",
            "fallback": {"harness": "pi", "model": "tico-mid-1.0"}}),))
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "gateway"}, machine["token"])
    if slow:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE attempts SET started_at=? WHERE id=?", (H.shift(H.now(), seconds=-600), attempt["id"]))
    events = [{"seq": 1, "kind": "tool", "payload": {"name": "read"}}] if acted else []
    events.append({"seq": len(events) + 1, "kind": "error", "payload": {"error": error}})
    post(api, f"attempts/{attempt['id']}/events", {"events": events}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": len(events), "text": "",
         "fallback": "pi", "primary_limited": True}, machine["token"])
    status, state = ended(api, attempt)
    assert state == job and words in status["focus"], (state, status["focus"])


def test_a_reply_the_gateway_cut_off_is_posted_with_what_happened(api, monkeypatch):
    """A stream that broke after its first chunk: the runner completes the turn with the partial reply and a note
    (runner-rs/src/hosts/pi.rs), so the bot never stops mid-sentence without a word."""
    machine, attempt = tico_run(api, monkeypatch)
    reply = ("Hello there, friend\n\n(The Tico AI Gateway cut this reply off: Tico AI Gateway is unavailable, try "
             "again (upstream_unavailable))")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "gateway"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/events", {"events": [{"seq": 1, "kind": "message", "payload": {
        "text": reply, "final": True}}]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 1, "text": reply},
         machine["token"])
    status, job = ended(api, attempt)
    assert job == "completed" and status["state"] == "idle"
    with api.app.state.store.read() as c:
        said = [r[0] for r in c.execute("SELECT body FROM messages WHERE sender_id='bot:ops'")]
    assert any("cut this reply off" in body and body.startswith("Hello there") for body in said), said


@pytest.mark.parametrize("acted", [False, True])
def test_a_gateway_with_no_chat_endpoint_yet_reads_plainly(api, monkeypatch, acted):
    """Dev 181 not deployed: the key is saved, but the Gateway answers `<base>/key` and `<base>/chat/completions` with
    404. Settings says it is not ready (not broken, not "did not answer"), and the bot says the Gateway doesn't serve
    Tico models yet. Asking again gets the same 404, so the run is saved for later either way."""
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.keys[KEY] = (404, {"error": {"message": "Not Found"}})
        shown = save(api, fake)
        assert shown["state"] == "not_ready" and shown["working"] is True
        assert shown["message"].startswith("Not ready: the Gateway at this URL doesn't serve Tico models yet")
        assert {r["id"]: r for r in get(api, "health")["checks"]}["gateway"]["status"] == "warn"
    put_bot_on(api, "ops", "tico-mid-1.0")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "gateway"}, machine["token"])
    events = [{"seq": 1, "kind": "tokens", "payload": {"input": 0, "output": 0, "total": 0}}] if acted else []
    events.append({"seq": len(events) + 1, "kind": "error", "payload": {"error": "404 status code (no body)"}})
    post(api, f"attempts/{attempt['id']}/events", {"events": events}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": len(events), "text": ""},
         machine["token"])
    with api.app.state.store.read() as c:
        focus = H.status(c, "ops")["focus"]
    assert focus == ("The Tico AI Gateway doesn't serve Tico models at its URL yet; check the Gateway URL in "
                     "Settings > Computers. This run is saved for later"), focus


def test_a_404_on_another_model_is_not_blamed_on_the_gateway(api, monkeypatch):
    setup(api, monkeypatch)
    put_bot_on(api, "ops", "kimi-k3")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    finish(api, machine, claim(api, machine, "ops"), "404 status code (no body)")
    with api.app.state.store.read() as c:
        assert "Tico AI Gateway" not in H.status(c, "ops")["focus"]


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


def test_a_new_bot_on_the_gateway_default_takes_its_templates_level(api, monkeypatch):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
        view = get(api, "providers")
        put(api, "providers", {"enabled": ["anthropic"], "model": "tico-general-medium-1.0", "expected_revision": view["revision"]})
        # The Inbox template says Low; a bot with no template, or one that names its own model, is not touched.
        made = new_bot(api, "mailwatch", template="inbox")
        plain = new_bot(api, "plain")
        own = new_bot(api, "chosen", template="inbox", model="tico-general-high-1.0", harness="pi", effort="high")
        with api.app.state.store.read() as c:
            models = dict(c.execute("SELECT slug, model FROM bots WHERE slug IN ('mailwatch','plain','chosen')").fetchall())
            stored = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (H.resolve_bot(c, "mailwatch")["id"],)).fetchone()[0])
        assert models == {"mailwatch": "tico-general-low-1.0", "plain": "tico-general-medium-1.0", "chosen": "tico-general-high-1.0"}, (made, plain, own)
        assert stored["model"] == "tico-general-low-1.0" and stored["gateway"] == {"level": "low"}
