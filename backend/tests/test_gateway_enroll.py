"""An install's own Tico AI Gateway key (backend/gateway_connection.py `auto_tick`), against a fake HQ that answers by
the contract in fixtures/gateway_enroll_contract.json: byte for byte the file Tico HQ's own tests check HQ against."""

import datetime
import hashlib
import hmac
import json
import pathlib
import re
import secrets
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.tests.test_api import api, assign, claim, get, post, runner  # noqa: F401
from backend.tests.test_decide_gateway import answer_all
from backend.tests.test_gateway_connection import (delete, everything, handed_to, put_bot_on, ready, save,
                                                   setup as connection_setup)
from backend.tests.test_judge import QUESTIONS

CONTRACT = json.loads((pathlib.Path(__file__).parent / "fixtures" / "gateway_enroll_contract.json").read_text())
INVITE = "tgi_" + "s" * 43                       # a synthetic invite code
PASTED = "tgw-synthetic-fixture-key-0042"


class FakeHQ:
    """HQ's enroll, rotate, key and decisions routes, answering as the contract says. `revoke_install` is staff's."""

    def __init__(self):
        self.invites, self.installs, self.keys, self.calls = {INVITE}, {}, {}, []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def bearer(self):
                return self.headers.get("Authorization", "").removeprefix("Bearer ")

            def known(self):
                """The caller's key record, or the contract's refusal (status, code)."""
                held = owner.keys.get(self.bearer())
                if not held:
                    return None, (401, "unknown_key")
                if held["revoked"] or owner.installs[held["install"]]["revoked"]:
                    return None, (401, "revoked_key")
                return held, None

            def do_GET(self):
                owner.calls.append(("GET", self.path, dict(self.headers)))
                held, refused = self.known()
                if refused:
                    return self.reply(refused[0], {"error": refused[1]})
                self.reply(200, {"status": "active", "month": "2026-10", "cap_usd": 50.0, "spent_usd": 1.0, "used": 0.02,
                                 "warning": False, "cap_reached": False, "rpm": 60, "tpm": 100000,
                                 "source": "enrolled", "rotate_after": held["rotate_after"]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                owner.calls.append(("POST", self.path, dict(self.headers), body))
                path = self.path.removeprefix("/v1/gateway")
                if path == CONTRACT["enroll"]["path"].removeprefix("/v1/gateway"):
                    status, answer = owner.enroll(body)
                elif path == CONTRACT["rotate"]["path"].removeprefix("/v1/gateway"):
                    held, refused = self.known()
                    status, answer = refused if refused else owner.rotate(held, self.bearer())
                elif path == "/decisions":
                    held, refused = self.known()
                    status, answer = refused or (200, {"model": "tico-decide-1.0", "answers": answer_all(
                        body["questions"]), "usage": {"input_tokens": 120, "output_tokens": 0, "cost_usd": 0.00001}})
                else:
                    status, answer = 404, "not_found"
                self.reply(status, answer if isinstance(answer, dict) else {"error": answer})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/gateway"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def mint(self, install, days=30):
        key = "tgw_" + secrets.token_urlsafe(32)
        after = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days))
        self.keys[key] = {"install": install, "revoked": False, "rotated": False, "key_id": "gk_" + secrets.token_hex(6),
                          "rotate_after": after.strftime("%Y-%m-%dT%H:%M:%SZ")}
        return key

    def enroll(self, body):
        install, secret = body.get("install", ""), body.get("secret", "")
        patterns = CONTRACT["patterns"]
        if not re.fullmatch(patterns["install"], install) or not re.fullmatch(patterns["secret"], secret):
            return 422, "invalid"
        known = self.installs.get(install)
        if known and known["revoked"]:
            return 403, "install_revoked"
        if known and known["secret"] != hashlib.sha256(secret.encode()).hexdigest():
            return 409, "already_enrolled"
        if not known and body.get("invite") not in self.invites:
            return 403, "invalid_invite"
        for held in self.keys.values():
            if held["install"] == install:
                held["revoked"] = True                    # re-enrolling by the secret revokes the old key at once
        self.installs[install] = {"secret": hashlib.sha256(secret.encode()).hexdigest(), "revoked": False}
        key = self.mint(install)
        return 201, {"key": key, "key_id": self.keys[key]["key_id"], "rotate_after": self.keys[key]["rotate_after"]}

    def rotate(self, held, key):
        if held["rotated"]:
            return 409, "already_rotated"
        held["rotated"] = True
        new = self.mint(held["install"])
        return 201, {"key": new, "key_id": self.keys[new]["key_id"], "rotate_after": self.keys[new]["rotate_after"],
                     "old_valid_until": "2026-10-19T18:10:00Z"}

    def revoke_install(self, install):
        self.installs[install]["revoked"] = True

    def posts(self, path):
        return [call for call in self.calls if call[0] == "POST" and call[1].endswith(path)]


@contextmanager
def fake_hq():
    fake = FakeHQ()
    try:
        yield fake
    finally:
        fake.server.shutdown()


def setup(api, monkeypatch, fake, invite=INVITE):
    connection_setup(api, monkeypatch)
    from backend import gateway_connection
    monkeypatch.setattr(gateway_connection, "check_later", lambda *args, **kwargs: None)
    monkeypatch.setenv("TICO_GATEWAY_URL", fake.url)
    if invite:
        monkeypatch.setenv("TICO_GATEWAY_INVITE", invite)
    else:
        monkeypatch.delenv("TICO_GATEWAY_INVITE", raising=False)


def tick(api, now=None):
    from backend import gateway_connection
    return gateway_connection.auto_tick(api.app.state.store, api.app.state.vault, api.app.state.store.settings,
                                        now=now)


def check(api):
    from backend import gateway_connection
    return gateway_connection.check_now(api.app.state.store, api.app.state.vault, api.app.state.store.settings)


def record(api):
    from backend import gateway_connection
    with api.app.state.store.read() as c:
        return gateway_connection._row(c)


def plain_key(api):
    from backend import gateway_connection
    with api.app.state.store.read() as c:
        return gateway_connection.key(c, api.app.state.vault, api.app.state.store.settings)


def owner_health(api):
    from backend import gateway_connection
    with api.app.state.store.read() as c:
        return gateway_connection.health(c, api.app.state.store.settings)


def test_the_app_holds_to_the_same_contract_as_hq():
    from backend import gateway_connection as GC
    from clients import gateway as G
    assert CONTRACT["invite_env"] == GC.INVITE_ENV and CONTRACT["bot_header"] == G.BOT_HEADER
    assert CONTRACT["app"]["health_stopped"] == GC.STOPPED and CONTRACT["app"]["stop"] == [G.INSTALL_REVOKED]
    assert set(CONTRACT["app"]["enroll_again"]) <= set(GC.ENROLL_AGAIN)
    assert G.ENROLL == CONTRACT["enroll"]["path"].removeprefix("/v1/gateway")
    assert G.ROTATE == CONTRACT["rotate"]["path"].removeprefix("/v1/gateway")
    secret, slug = "gis_" + "k" * 43, "ops"
    expected = "gb_" + hmac.new(secret.encode(), slug.encode(), hashlib.sha256).hexdigest()[:16]
    assert G.bot_id(secret, slug) == expected and re.fullmatch(CONTRACT["patterns"]["bot"], expected)
    ui = (pathlib.Path(__file__).parents[2] / "ui" / "app" / "settings-providers.js").read_text()
    assert CONTRACT["app"]["settings_line"] in ui


def test_without_an_invite_nothing_is_asked(api, monkeypatch):
    with fake_hq() as fake:
        setup(api, monkeypatch, fake, invite="")
        assert tick(api) is None and fake.calls == [] and owner_health(api) is None


def test_an_invited_install_meters_decide_and_bot_runs_under_its_own_key_until_tico_turns_it_off(api, monkeypatch):
    """Brief test 5: no key is typed or copied; Decide and a Tico-model bot run under the install's automatic key,
    each naming its bot opaquely; revoking the install stops both, with a plain Health finding."""
    from backend import judge
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        assert tick(api) == "enrolled"
        kept = record(api)
        key = plain_key(api)
        assert kept["origin"] == "automatic" and kept["key"].startswith("sealed:v1:") and key in fake.keys
        assert re.fullmatch(CONTRACT["patterns"]["install"], kept["install"])
        assert kept["install_secret"].startswith("sealed:v1:")
        assert fake.posts("/enroll")[0][3]["invite"] == INVITE
        shown = get(api, "providers/gateway")
        assert shown["connected"] and shown["automatic"] and shown["source"] == "settings"
        # Neither the key, the install's secret nor the invite is in any read, row or event.
        secret = fake.posts("/enroll")[0][3]["secret"]
        for value in (key, secret, INVITE):
            assert value not in json.dumps(shown) and value not in everything(api)

        # Decide asks with the automatic key and names itself by an opaque id, never a name.
        settings = api.app.state.store.settings
        monkeypatch.setattr(settings, "gateway_key", "", raising=False)
        own = lambda state, questions, label=None: {"model": "own", "answers": answer_all(questions), "usage": {}, "ms": 1}
        own.model, own.provider = "own", "own"
        monkeypatch.setattr(judge, "fallback_engine", lambda *args, **kwargs: own)
        api.app.state.judge = judge.primary_engine(settings, api.app.state.store, api.app.state.vault)
        asked = post(api, "decisions", {"state": {"subject": "Invoice 4471"}, "questions": QUESTIONS})
        assert asked["model"] == "tico-decide-1.0"
        sent = fake.posts("/decisions")[-1][2]
        assert sent["Authorization"] == "Bearer " + key
        assert sent["X-Tico-Bot"] == "gb_" + hmac.new(secret.encode(), judge.DECIDE_BOT.encode(),
                                                      hashlib.sha256).hexdigest()[:16]

        # A bot's run on a Tico model: its runner gets the key and the bot's opaque id for its relay.
        put_bot_on(api, "ops", "tico-mid-1.0")
        machine = runner(api)
        assign(api, machine, "ops")
        ready(api, machine, ["ops"])
        post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
        attempt = claim(api, machine, "ops")
        handed = handed_to(api, machine, attempt)
        assert handed["TICOAI_GATEWAY_KEY"]["value"] == key
        assert handed["TICOAI_GATEWAY_BOT"]["value"] == "gb_" + hmac.new(secret.encode(), b"ops",
                                                                         hashlib.sha256).hexdigest()[:16]
        with api.app.state.store.read() as c:
            paid = c.execute("SELECT gateway_key_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        assert paid == kept["key_id"]

        # Tico turns the install off: the key is refused, enrolling again is refused, and it is not asked again.
        fake.revoke_install(kept["install"])
        assert check(api)["state"] == "revoked"
        assert tick(api) == "stopped"
        assert owner_health(api) == {"status": "bad", "summary": CONTRACT["app"]["health_stopped"]}
        health = get(api, "health")
        assert CONTRACT["app"]["health_stopped"] in json.dumps(health)
        assert plain_key(api) == "" and get(api, "providers/gateway")["connected"] is False
        assert handed_to(api, machine, attempt) == {}
        asked = post(api, "decisions", {"state": {"subject": "Invoice 4471"}, "questions": QUESTIONS})
        assert asked["model"] == "own"
        enrolls = len(fake.posts("/enroll"))
        days = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
        assert tick(api) is None and tick(api, now=days) is None and len(fake.posts("/enroll")) == enrolls
        # Once Tico turns it back on, the owner can try again from the card.
        assert get(api, "providers/gateway")["auto_off"] is True
        fake.installs[kept["install"]]["revoked"] = False
        post(api, "providers/gateway/automatic", {})
        assert tick(api) == "enrolled" and record(api)["install"] == kept["install"]


def test_a_refused_invite_reads_plainly_and_is_tried_again_hourly_not_on_every_page_open(api, monkeypatch):
    with fake_hq() as fake:
        setup(api, monkeypatch, fake, invite="tgi_" + "u" * 43)
        assert tick(api) == "invalid_invite"
        health = owner_health(api)
        assert health["status"] == "warn" and "invite code" in health["summary"]
        assert tick(api) is None and len(fake.posts("/enroll")) == 1
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1, minutes=1)
        assert tick(api, now=later) == "invalid_invite" and len(fake.posts("/enroll")) == 2


def test_the_key_rotates_when_due_and_a_lost_one_is_replaced_by_the_installs_secret(api, monkeypatch):
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        tick(api)
        first, kept = plain_key(api), record(api)
        assert tick(api) is None                                     # not due yet: nothing asked
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=31)
        assert tick(api, now=later) == "rotated"
        second = plain_key(api)
        assert second != first and fake.posts("/rotate")[-1][2]["Authorization"] == "Bearer " + first
        assert record(api)["revision"] == kept["revision"] + 1 and record(api)["install"] == kept["install"]
        # A key HQ already replaced (the answer was lost): enroll again by the secret, no invite needed any more.
        monkeypatch.setenv("TICO_GATEWAY_INVITE", "tgi_" + "x" * 43)
        fake.keys[second]["rotated"] = True
        assert tick(api, now=later + datetime.timedelta(days=31)) == "enrolled"
        assert record(api)["install"] == kept["install"] and plain_key(api) not in (first, second)
        assert fake.keys[second]["revoked"]


def test_a_pasted_key_wins_and_clearing_an_automatic_key_is_not_undone_behind_the_owner(api, monkeypatch):
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        tick(api)
        install = record(api)["install"]
        # The owner clears it: the install is not enrolled again until the owner turns it back on.
        cleared = delete(api, "providers/gateway")
        assert cleared["connected"] is False and cleared["auto_off"] is True
        assert tick(api) is None and len(fake.posts("/enroll")) == 1
        on = post(api, "providers/gateway/automatic", {})
        assert on["auto_off"] is False
        assert tick(api) == "enrolled" and record(api)["install"] == install
        # A pasted key replaces the automatic one and is never rotated.
        fake.keys[PASTED] = {"install": install, "revoked": False, "rotated": False, "key_id": "gk_000000000001",
                             "rotate_after": "2026-01-01T00:00:00Z"}
        save(api, fake, key=PASTED)
        assert get(api, "providers/gateway")["automatic"] is False
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=60)
        assert tick(api, now=later) is None and plain_key(api) == PASTED
        # Even when the Gateway refuses the pasted key, it is the owner's to replace.
        fake.keys[PASTED]["revoked"] = True
        assert check(api)["state"] == "revoked" and tick(api, now=later) is None and plain_key(api) == PASTED


def test_a_key_pasted_while_the_install_enrolls_wins(api, monkeypatch):
    from clients import gateway as G
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        enroll = G.enroll

        def meanwhile(*args, **kwargs):
            answer = enroll(*args, **kwargs)
            fake.keys[PASTED] = {"install": "", "revoked": False, "rotated": False, "key_id": "gk_000000000001",
                                 "rotate_after": ""}
            save(api, fake, key=PASTED)                 # the owner saves a key while HQ answers
            return answer
        monkeypatch.setattr(G, "enroll", meanwhile)
        assert tick(api) is None
        assert plain_key(api) == PASTED and get(api, "providers/gateway")["automatic"] is False


def test_an_install_hq_enrolled_whose_answer_was_lost_tries_again_as_itself(api, monkeypatch):
    """The identity is kept before HQ is asked: the next try is the same install, so HQ counts it once against the
    invite and the key comes back by the install's secret."""
    from clients import gateway as G
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        enroll = G.enroll

        def lost(*args, **kwargs):
            enroll(*args, **kwargs)                     # HQ enrolls the install; its answer never arrives
            return 0, {}, "unreachable"

        def died(*args, **kwargs):
            enroll(*args, **kwargs)                     # HQ enrolls the install; the server stops before the answer
            raise SystemExit
        monkeypatch.setattr(G, "enroll", died)
        with pytest.raises(SystemExit):
            tick(api)
        install = record(api)["install"]
        assert install in fake.installs and record(api)["install_secret"].startswith("sealed:v1:")
        assert plain_key(api) == ""
        monkeypatch.setattr(G, "enroll", lost)
        assert tick(api) == "unreachable" and plain_key(api) == "" and record(api)["install"] == install
        monkeypatch.setattr(G, "enroll", enroll)
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1, minutes=1)
        assert tick(api, now=later) == "enrolled"
        assert record(api)["install"] == install and list(fake.installs) == [install]
        assert [call[3]["install"] for call in fake.posts("/enroll")] == [install] * 3
        assert plain_key(api) in fake.keys and check(api)["state"] == "connected"


def test_a_refused_automatic_key_is_enrolled_again_hourly_not_on_every_tick(api, monkeypatch):
    from clients import gateway as G
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        tick(api)
        first = plain_key(api)
        fake.keys[first]["revoked"] = True
        assert check(api)["state"] == "revoked"
        enroll = G.enroll
        monkeypatch.setattr(G, "enroll", lambda *args, **kwargs: (429, {}, "rate_limited"))
        assert tick(api) == "rate_limited"
        now = datetime.datetime.now(datetime.timezone.utc)
        for minutes in (0, 1, 30, 59):
            assert tick(api, now=now + datetime.timedelta(minutes=minutes)) is None
        monkeypatch.setattr(G, "enroll", lambda *args, **kwargs: (0, {}, "unreachable"))
        assert tick(api, now=now + datetime.timedelta(minutes=61)) == "unreachable"
        assert tick(api, now=now + datetime.timedelta(minutes=62)) is None
        monkeypatch.setattr(G, "enroll", enroll)
        assert tick(api, now=now + datetime.timedelta(minutes=122)) == "enrolled"
        assert plain_key(api) not in ("", first) and len(fake.posts("/enroll")) == 2


def test_a_rotation_that_fails_waits_the_hour(api, monkeypatch):
    from clients import gateway as G
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        tick(api)
        first = plain_key(api)
        rotate = G.rotate
        monkeypatch.setattr(G, "rotate", lambda *args, **kwargs: (0, {}, "unreachable"))
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=31)
        assert tick(api, now=later) == "unreachable" and owner_health(api) is None
        monkeypatch.setattr(G, "rotate", rotate)
        assert tick(api, now=later + datetime.timedelta(minutes=30)) is None and plain_key(api) == first
        assert tick(api, now=later + datetime.timedelta(minutes=61)) == "rotated" and plain_key(api) != first


def test_a_key_pasted_before_the_identity_is_kept_wins(api, monkeypatch):
    from clients import gateway as G
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        safe_base = G.safe_base

        def meanwhile(base):
            if sys._getframe(1).f_code.co_name != "_auto_tick":
                return safe_base(base)                  # only once the install has read the connection
            fake.keys[PASTED] = {"install": "", "revoked": False, "rotated": False, "key_id": "gk_000000000001",
                                 "rotate_after": ""}
            save(api, fake, key=PASTED)                 # the owner saves a key before the install keeps its identity
            return safe_base(base)
        monkeypatch.setattr(G, "safe_base", meanwhile)
        assert tick(api) is None and fake.posts("/enroll") == []
        assert plain_key(api) == PASTED and get(api, "providers/gateway")["automatic"] is False


def test_an_enrolled_install_keeps_its_key_with_no_invite_until_tico_turns_it_off(api, monkeypatch):
    """The invite is only for the first sign-up: with it gone the install still rotates, enrolls again by its secret,
    and takes Tico's stop (Health and the card's "connect automatically" too)."""
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        assert tick(api) == "enrolled"
        install = record(api)["install"]
        monkeypatch.delenv("TICO_GATEWAY_INVITE")
        from backend import gateway_connection
        with api.app.state.store.read() as c:
            assert gateway_connection.maintained(c, api.app.state.store.settings)
        first = plain_key(api)
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=31)
        assert tick(api, now=later) == "rotated"
        second = plain_key(api)
        # A lost key: enroll again by the secret, with no invite sent.
        fake.keys[second]["rotated"] = True
        assert tick(api, now=later + datetime.timedelta(days=31)) == "enrolled"
        assert "invite" not in fake.posts("/enroll")[-1][3] and record(api)["install"] == install
        assert plain_key(api) not in (first, second)
        # Tico turns the install off: it stops, says so, and is not asked again.
        fake.revoke_install(install)
        assert check(api)["state"] == "revoked"
        assert tick(api) == "stopped"
        assert owner_health(api) == {"status": "bad", "summary": CONTRACT["app"]["health_stopped"]}
        enrolls = len(fake.posts("/enroll"))
        assert tick(api, now=later + datetime.timedelta(days=90)) is None and len(fake.posts("/enroll")) == enrolls
        assert get(api, "providers/gateway")["auto_off"] is True
        # Tico turns it back on and the owner connects automatically again, still with no invite.
        fake.installs[install]["revoked"] = False
        post(api, "providers/gateway/automatic", {})
        assert tick(api) == "enrolled" and record(api)["install"] == install


def test_a_stop_with_no_invite_is_tried_again_under_a_new_invite(api, monkeypatch):
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        tick(api)
        install = record(api)["install"]
        monkeypatch.delenv("TICO_GATEWAY_INVITE")
        fake.revoke_install(install)
        check(api)
        assert tick(api) == "stopped" and tick(api) is None
        fake.installs[install]["revoked"] = False
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
        assert tick(api, now=later) is None             # no invite: the stop holds
        monkeypatch.setenv("TICO_GATEWAY_INVITE", INVITE)
        assert tick(api, now=later) == "enrolled" and record(api)["install"] == install


def test_a_stop_under_an_invite_holds_once_the_invite_is_removed(api, monkeypatch):
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        tick(api)
        fake.revoke_install(record(api)["install"])
        check(api)
        assert tick(api) == "stopped"
        monkeypatch.delenv("TICO_GATEWAY_INVITE")
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
        assert tick(api, now=later) is None and len(fake.posts("/enroll")) == 2
        assert owner_health(api) == {"status": "bad", "summary": CONTRACT["app"]["health_stopped"]}


def test_a_member_cannot_turn_automatic_connecting_back_on(api, monkeypatch):
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        tick(api)
        delete(api, "providers/gateway")
        before = record(api)
        post(api, "providers/gateway/automatic", {}, "ben-test", expected=403)
        assert record(api) == before and before["auto"]["off"] is True
        assert tick(api) is None and len(fake.posts("/enroll")) == 1
        # Nor after Tico's stop.
        post(api, "providers/gateway/automatic", {})
        tick(api)
        fake.revoke_install(record(api)["install"])
        check(api)
        assert tick(api) == "stopped"
        before = record(api)
        post(api, "providers/gateway/automatic", {}, "ben-test", expected=403)
        assert record(api) == before and tick(api) is None and len(fake.posts("/enroll")) == 3


def test_the_hourly_sweep_reconnects_an_enrolled_install_with_no_invite(api, monkeypatch):
    """The scheduler's due check, the page-load check's fingerprint and its auto_tick gate all count the install's
    identity, not only the invite: turned back on after Tico's stop, the install connects with no invite."""
    from backend import credentials, gateway_connection
    from backend.scheduler import Scheduler
    later_check, soon = gateway_connection.check_later, gateway_connection.check_soon
    with fake_hq() as fake:
        setup(api, monkeypatch, fake)
        tick(api)
        install = record(api)["install"]
        monkeypatch.delenv("TICO_GATEWAY_INVITE")
        fake.revoke_install(install)
        check(api)
        assert tick(api) == "stopped"
        fake.installs[install]["revoked"] = False
        post(api, "providers/gateway/automatic", {})
        monkeypatch.setattr(gateway_connection, "check_later", later_check)
        threads = []
        monkeypatch.setattr(gateway_connection, "check_soon", lambda *args: threads.append(soon(*args)))
        monkeypatch.setattr(credentials, "Vault", lambda store: api.app.state.vault)   # the test's fake KMS
        Scheduler(api.app.state.store, api.app.state.execution).tick()
        assert threads and threads[0] is not None
        threads[0].join(10)
        assert record(api)["install"] == install and plain_key(api) in fake.keys
        assert "invite" not in fake.posts("/enroll")[-1][3]


def test_the_contract_covers_the_key_usage_and_cap_routes_the_app_calls():
    """The app reads a key's monthly meter and sets its self cap through these two routes (finance gateway_sync); the
    contract names their paths, the fields the app reads and the refusals it handles."""
    usage, cap = CONTRACT["key_usage"], CONTRACT["key_cap"]
    assert (usage["method"], usage["path"]) == ("GET", "/v1/gateway/key/usage")
    assert usage["fields"] == ["month", "rows", "totals", "as_of"]
    assert usage["row_fields"] == ["model", "category", "charged", "calls", "price_usd"]
    assert (cap["method"], cap["path"]) == ("POST", "/v1/gateway/key/cap")
    assert cap["request"]["required"] == ["self_cap_usd"]
    assert cap["refused"]["unknown_key"] == 401 and cap["refused"]["rate_limited"] == 429
