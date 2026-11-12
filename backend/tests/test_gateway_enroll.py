"""An install's own Tico AI Gateway key (backend/gateway_connection.py `auto_tick`), against a fake HQ that answers by
the contract in fixtures/gateway_enroll_contract.json: byte for byte the file Tico HQ's own tests check HQ against."""

import datetime
import hashlib
import hmac
import json
import re
import sys

import pytest

from backend.tests.helpers.api import assign, claim, get, headers, post, runner  # noqa: F401
from backend.tests.helpers.gateway_connection import delete, everything, handed_to, put_bot_on, ready, save
from backend.tests.helpers.judge import QUESTIONS
from backend.tests.helpers.gateway_enroll import CONTRACT, INVITE, fake_hq, setup, tick

PASTED = "tgw-synthetic-fixture-key-0042"


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
        api.app.state.judge = judge.primary_engine(settings, api.app.state.store, api.app.state.vault)
        asked = post(api, "decisions", {"state": {"subject": "Invoice 4471"}, "questions": QUESTIONS})
        assert asked["model"] == "tico-decide-low"
        sent = fake.posts("/decisions")[-1][2]
        assert sent["Authorization"] == "Bearer " + key
        assert (
            sent["X-Tico-Bot"]
            == "gb_" + hmac.new(secret.encode(), judge.DECIDE_BOT.encode(), hashlib.sha256).hexdigest()[:16]
        )

        # A bot's run on a Tico model: its runner gets the key and the bot's opaque id for its relay.
        put_bot_on(api, "ops", "tico-medium-1.0")
        machine = runner(api)
        assign(api, machine, "ops")
        ready(api, machine, ["ops"])
        post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
        attempt = claim(api, machine, "ops")
        handed = handed_to(api, machine, attempt)
        assert handed["TICOAI_GATEWAY_KEY"]["value"] == key
        assert (
            handed["TICOAI_GATEWAY_BOT"]["value"]
            == "gb_" + hmac.new(secret.encode(), b"ops", hashlib.sha256).hexdigest()[:16]
        )
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
        decided = api.post(
            "/api/v2/decisions", json={"state": {"subject": "Invoice 4471"}, "questions": QUESTIONS}, headers=headers()
        )
        assert decided.status_code == 503, "with no key, Decide is off: no other model answers"
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
        assert tick(api) is None  # not due yet: nothing asked
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
        fake.keys[PASTED] = {
            "install": install,
            "revoked": False,
            "rotated": False,
            "key_id": "gk_000000000001",
            "rotate_after": "2026-01-01T00:00:00Z",
        }
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
            fake.keys[PASTED] = {
                "install": "",
                "revoked": False,
                "rotated": False,
                "key_id": "gk_000000000001",
                "rotate_after": "",
            }
            save(api, fake, key=PASTED)  # the owner saves a key while HQ answers
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
            enroll(*args, **kwargs)  # HQ enrolls the install; its answer never arrives
            return 0, {}, "unreachable"

        def died(*args, **kwargs):
            enroll(*args, **kwargs)  # HQ enrolls the install; the server stops before the answer
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
                return safe_base(base)  # only once the install has read the connection
            fake.keys[PASTED] = {
                "install": "",
                "revoked": False,
                "rotated": False,
                "key_id": "gk_000000000001",
                "rotate_after": "",
            }
            save(api, fake, key=PASTED)  # the owner saves a key before the install keeps its identity
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
        assert tick(api, now=later) is None  # no invite: the stop holds
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
        monkeypatch.setattr(credentials, "Vault", lambda store: api.app.state.vault)  # the test's fake KMS
        Scheduler(api.app.state.store, api.app.state.execution).tick()
        assert threads and threads[0] is not None
        threads[0].join(10)
        assert record(api)["install"] == install and plain_key(api) in fake.keys
        assert "invite" not in fake.posts("/enroll")[-1][3]
