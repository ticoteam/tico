"""A Finance connection reads only a credential its creator may use (backend/credentials.py `usable`, the rule the
vault's own list follows): the connect route refuses before its probe sends the key anywhere, connection_create refuses
on every path (the route and the generic actions route alike, a bot by its own grants), and a sync refuses, logged, a
connection whose creator may no longer use its key, without reading it."""

import json
import logging
from typing import Any

import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import banking, sync
from backend.finance.store import tico as FT
from backend.store import Store
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.finance_fixtures import (  # noqa: F401
    AT,
    OWNER,
    FakeMercury,
    act,
    books,
    finance_template,
    store,
    store_keys,
)

CARA = "human:cara"
BOT = "bot:finance-owner"
STRIPE_FOUND = {"account": "acct_test000001", "balances": [{"currency": "USD", "available": 0, "pending": 0}]}
STRIPE = {"provider": "stripe", "credential_id": "cred-stripe"}


def grant(c: Any, credential: str, subject: str) -> str:
    gid = H.new_id()
    c.execute(
        "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) VALUES(?,?,?,?,?)",
        (gid, credential, subject, OWNER, AT),
    )
    return gid


def finance_admin(c: Any, teammate: str) -> None:
    assert act(c, OWNER, "right_grant", {"teammate": teammate, "right": "finance.admin"}).status == "committed"


def test_the_connect_route_refuses_a_key_the_finance_admin_may_not_use_before_probing_it(api, monkeypatch):
    store = api.app.state.store
    FA.use_vault(api.app.state.vault)  # the api fixture starts no timers, which register it
    with store.transaction() as c:
        FA.setup(c, OWNER, AT)
        finance_admin(c, CARA)
        store_keys(c, "cred-stripe")
    probed: list[str] = []

    def stripe_probe(_store: Any, _vault: Any, credential_id: str) -> tuple[dict[str, object], str]:
        probed.append(credential_id)
        return STRIPE_FOUND, ""

    monkeypatch.setattr(FA, "stripe_probe", stripe_probe)
    refused = api.post("/api/v2/finance/connections", json=STRIPE, headers=headers("cara-test"))
    assert refused.status_code == 403 and FA.KEY_NOT_YOURS in refused.text, refused.text
    assert probed == []  # the key never reached Stripe
    with store.transaction() as c:
        grant(c, "cred-stripe", CARA)
    done = api.post("/api/v2/finance/connections", json=STRIPE, headers=headers("cara-test"))
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "committed", done.json()
    assert probed == ["cred-stripe"]
    with store.read() as c:
        assert c.execute("SELECT created_by FROM fin_connections").fetchone()[0] == CARA


def test_the_actions_route_refuses_connection_create_with_a_key_the_caller_may_not_use(api):
    store = api.app.state.store
    FA.use_vault(api.app.state.vault)
    with store.transaction() as c:
        FA.setup(c, OWNER, AT)
        finance_admin(c, CARA)
        store_keys(c, "cred-stripe")
    payload = {**STRIPE, "probe": STRIPE_FOUND}  # a probe the caller wrote: no provider was ever asked
    forged = post(api, "finance/actions", {"kind": "connection_create", "payload": payload}, token="cara-test")
    assert (forged["status"], forged["code"], forged["reason"]) == ("denied", "denied", FA.KEY_NOT_YOURS), forged
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM fin_connections").fetchone()[0] == 0
    # The owner, a credential administrator, keeps the reach the vault gives them.
    done = post(api, "finance/actions", {"kind": "connection_create", "payload": payload})
    assert done["status"] == "committed", done


def test_a_bot_connects_only_a_key_it_is_granted(books: Store):
    payload = {**STRIPE, "probe": STRIPE_FOUND}
    with books.transaction() as c:
        finance_admin(c, BOT)
        refused = act(c, BOT, "connection_create", payload)
        assert (refused.status, refused.reason) == ("denied", FA.KEY_NOT_YOURS), refused
        grant(c, "cred-stripe", BOT)
        allowed = act(c, BOT, "connection_create", payload)
        assert allowed.status in ("committed", "needs_approval"), allowed


def test_a_person_who_has_left_may_use_no_key(books: Store):
    with books.transaction() as c:
        grant(c, "cred-stripe", CARA)
        assert FT.may_use_credential(c, CARA, "cred-stripe")
        c.execute(  # Cara (third on the roster) leaves
            "UPDATE registry_metadata SET value=json_set(value, '$.people[2].hidden', json('true')) WHERE key='people'"
        )
        assert not FT.may_use_credential(c, CARA, "cred-stripe")
        assert not FT.may_use_credential(c, "system", "cred-stripe")


def test_a_person_uses_a_key_they_stored_for_a_bot_until_someone_else_saves_it(books: Store):
    with books.transaction() as c:
        c.execute(
            "INSERT INTO credentials(id,name,kind,member_stored,created_at,updated_at,updated_by,created_by) "
            "VALUES('cred-cara','cred-cara','token',1,?,?,?,?)",
            (AT, AT, CARA, CARA),
        )
        assert FT.may_use_credential(c, CARA, "cred-cara")  # `owned`, read for this one key
        c.execute("UPDATE credentials SET updated_by=? WHERE id='cred-cara'", (OWNER,))
        assert not FT.may_use_credential(c, CARA, "cred-cara")


def test_a_bot_never_sends_a_default_read_key_it_holds_no_grant_to(books: Store):
    with books.transaction() as c:
        c.execute(
            "INSERT INTO tools(id,name,everyone_reads,created_by,created_at,updated_at) VALUES('bank','Bank',1,?,?,?)",
            (OWNER, AT, AT),
        )
        c.execute("UPDATE credentials SET tool_id='bank' WHERE id='cred-stripe'")
        assert not FT.may_use_credential(c, BOT, "cred-stripe")  # the proxy holds a default read to reads
        grant(c, "cred-stripe", BOT)
        assert FT.may_use_credential(c, BOT, "cred-stripe")


def test_with_no_sign_in_the_credential_administrators_follow_an_ownership_transfer(books: Store):
    assert books.settings.credential_admins_follow_owner
    with books.transaction() as c:
        ana, cara = FT.person_email(c, OWNER), FT.person_email(c, CARA)
        assert books.settings.credential_admins == (ana,)
        assert FT.may_use_credential(c, OWNER, "cred-stripe") and not FT.may_use_credential(c, CARA, "cred-stripe")
        # The transfer is stored (backend/access.py transfer); the settings this worker started with still name Ana.
        c.execute(
            "INSERT OR REPLACE INTO registry_metadata(key,value) VALUES('owner',?)",
            (json.dumps({"email": cara, "revision": 2, "source": "transfer"}),),
        )
        assert books.settings.credential_admins == (ana,)
        assert FT.may_use_credential(c, CARA, "cred-stripe")
        assert not FT.may_use_credential(c, OWNER, "cred-stripe")


def _sealed(c: Any, cid: str, value: str) -> None:
    ciphertext, nonce = FT._VAULT[0].cipher.encrypt(c, cid, value)
    c.execute(
        "INSERT INTO credentials(id,name,kind,ciphertext,nonce,created_at,updated_at,updated_by,created_by) "
        "VALUES(?,?,'token',?,?,?,?,?,?)",
        (cid, cid, ciphertext, nonce, AT, AT, OWNER, OWNER),
    )


def test_a_sync_reads_the_key_while_its_connector_may_use_it_and_refuses_once_they_may_not(books: Store, caplog):
    fake = FakeMercury()
    vault = FT._VAULT[0]
    with books.transaction() as c:
        finance_admin(c, CARA)
        _sealed(c, "cred-live", "mercury-secret-0001")
        gid = grant(c, "cred-live", CARA)
        done = act(
            c,
            CARA,
            "connection_create",
            {"provider": "mercury", "credential_id": "cred-live", "probe": sync.probe(fake)[0]},  # type: ignore[arg-type]
        )
        assert done.status == "committed", done
    cid = str(done.result["connection_id"])
    tokens: list[str] = []

    def make(token: str) -> Any:
        tokens.append(token)
        return fake

    assert sync.connection(books, vault, cid, make=make, at=AT) in ("ok", "failed")
    assert tokens == ["mercury-secret-0001"]
    with books.transaction() as c:
        c.execute("UPDATE credential_grants SET revoked_at=? WHERE id=?", (AT, gid))
        c.execute("UPDATE fin_connections SET next_sync_at=NULL WHERE id=?", (cid,))
    with caplog.at_level(logging.WARNING, logger="backend.finance.key_use"):
        assert sync.connection(books, vault, cid, make=make, at=AT) == "refused"
    assert tokens == ["mercury-secret-0001"]  # never read again
    with books.read() as c:
        row = c.execute("SELECT status, last_error FROM fin_connections WHERE id=?", (cid,)).fetchone()
    assert (row["status"], row["last_error"]) == ("error", banking.KEY_NOT_USABLE_NOW)
    assert "category=credential_not_usable" in caplog.text and f"id={cid}" in caplog.text
    assert "mercury-secret-0001" not in caplog.text


@pytest.mark.parametrize("provider", ["stripe", "brex"])
def test_a_stripe_or_brex_sync_refuses_a_key_its_connector_may_not_use(books: Store, provider: str):
    with books.transaction() as c:
        _sealed(c, "cred-held", "provider-secret-0001")
        cid = H.new_id()
        c.execute(
            "INSERT INTO fin_connections(id,provider,purpose,credential_ref,status,created_at,created_by) "
            "VALUES(?,?,'bank','cred-held','active',?,?)",
            (cid, provider, AT, CARA),
        )

    def make(_token: str) -> Any:
        pytest.fail("a key its connector may not use was read")

    assert sync.connection(books, FT._VAULT[0], cid, make=make, at=AT) == "refused"


def test_an_archived_bot_may_use_no_key_and_its_connection_stops_syncing(books: Store):
    with books.transaction() as c:
        _sealed(c, "cred-bot", "stripe-secret-0001")
        grant(c, "cred-bot", BOT)
        assert FT.may_use_credential(c, BOT, "cred-bot")
        cid = H.new_id()
        c.execute(
            "INSERT INTO fin_connections(id,provider,purpose,credential_ref,status,created_at,created_by) "
            "VALUES(?,'stripe','bank','cred-bot','active',?,?)",
            (cid, AT, BOT),
        )
        # Archiving keeps the bot's grants: bot auth (backend/auth.py bot_identity) is what refuses it.
        c.execute("UPDATE bots SET state='archived' WHERE id=?", (H.actor_id(BOT),))
        assert not FT.may_use_credential(c, BOT, "cred-bot")

    def make(_token: str) -> Any:
        pytest.fail("an archived bot's key reached Stripe")

    assert sync.connection(books, FT._VAULT[0], cid, make=make, at=AT) == "refused"


def test_the_check_reads_by_key_and_scans_no_table(books: Store):
    """Run at connect and for every connection on every sync: each statement it runs is a seek (no scan of credentials,
    humans, grants or bots, as the owner-by-email lookup and `owned` once were)."""
    with books.read() as c:
        seen: list[str] = []
        c.set_trace_callback(seen.append)
        try:
            for actor in (OWNER, CARA, BOT):
                FT.may_use_credential(c, actor, "cred-stripe")
        finally:
            c.set_trace_callback(None)
        plans = {
            sql: [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql)]
            for sql in dict.fromkeys(seen)
            if sql.lstrip().upper().startswith("SELECT")
        }
    assert plans
    scanned = {sql: plan for sql, plan in plans.items() if any(p.startswith("SCAN") for p in plan)}
    assert not scanned, scanned
