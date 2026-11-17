"""D11-11: every Tico secret in one encrypted store. A subscription's value, the Tico AI Gateway key and install secret,
and the Tico company server key are service rows of the vault (backend/credentials.py SERVICE); an older server's
values, sealed in their own rows, move there once (backend/migrations/20261117103033_one_encrypted_store.py), or at a
later start when the vault's key was out of reach."""

import importlib
import json
import sqlite3
from contextlib import closing

import pytest

from backend import company_server, gateway_connection, migrations
from backend import model_subscriptions as MS
from backend.config import Settings
from backend.credentials import KEPT, CredentialCipher, Vault
from backend.store import Problem, Store
from scripts.benchmark_model_keys import insert

MOVE = "20261117103033_one_encrypted_store"
ONE = importlib.import_module("backend.migrations." + MOVE)
NOW = "2026-11-01T00:00:00Z"
TOKEN = "sk-ant-oat01-" + "c" * 24
GATEWAY = "tgk_" + "g" * 32
INSTALL = "gis_" + "i" * 32
SERVER = "tsk_" + "s" * 32
SECRETS = (TOKEN, GATEWAY, INSTALL, SERVER)


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    monkeypatch.delenv(gateway_connection.ENV_KEY, raising=False)


def _older_server(tmp_path, cipher=None):
    """A database as an older server left it, this move not run yet: each secret sealed in its own row or record."""
    store = Store(Settings(db_path=tmp_path / "hub.db", company_name="Acme"))
    store.initialize()
    vault = Vault(store, cipher)
    with store.transaction() as c:
        c.execute(
            "INSERT INTO model_subscriptions(id,name,kind,sealed,preview,added_by,created_at,updated_at) "
            "VALUES('sub1','Company Max','claude_token',?,'sk-ant-…cccc','human:ana',?,?)",
            (vault.cipher.seal(c, MS._context("sub1"), TOKEN), NOW, NOW),
        )
        gateway = {
            "key": vault.cipher.seal(c, gateway_connection.CONTEXT, GATEWAY),
            "install": "gi_older",
            "install_secret": vault.cipher.seal(c, gateway_connection.INSTALL_CONTEXT, INSTALL),
            "origin": "automatic",
            "revision": 3,
        }
        server = {"key": vault.cipher.seal(c, company_server.CONTEXT, SERVER), "company": {"id": "co1"}}
        for key, record in ((gateway_connection.KEY, gateway), (company_server.KEY, server)):
            c.execute("INSERT INTO registry_metadata VALUES(?,?)", (key, json.dumps(record, sort_keys=True)))
        c.execute("DELETE FROM schema_migrations WHERE version=?", (MOVE,))
    return store, vault


def _start(store):
    with closing(sqlite3.connect(store.settings.db_path, isolation_level=None)) as conn:
        migrations.apply(conn)


def _values(store, vault):
    with store.read() as c:
        return (
            MS.value_of(c, vault.cipher, MS.row(c, "sub1")),
            gateway_connection.key(c, vault),
            gateway_connection._secret(c, vault, gateway_connection._row(c), "install_secret"),
            company_server._key(store, vault)[0],
        )


def _raw(tmp_path):
    return b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith("hub.db"))


def test_an_older_servers_sealed_secrets_move_into_service_rows_once(tmp_path, caplog):
    store, vault = _older_server(tmp_path)
    assert _values(store, vault) == SECRETS  # read where they are before the move, as before
    caplog.set_level("INFO")
    _start(store)
    with store.read() as c:
        sub = MS.row(c, "sub1")
        assert (sub["sealed"], sub["credential_id"]) == ("", "model-subscription-sub1")
        rows = {r["id"]: r["kind"] for r in c.execute("SELECT id,kind FROM credentials")}
        assert rows == dict.fromkeys(
            ("model-subscription-sub1", "gateway-key", "gateway-install-secret", "company-server-key"), "service"
        )
        records = [gateway_connection._row(c), company_server._row(c)]
        assert all(r["key"].startswith(KEPT) for r in records) and records[0]["install_secret"].startswith(KEPT)
        assert (records[0]["revision"], records[0]["install"]) == (3, "gi_older")  # the rest of the record stays
        assert not ONE.left(c)
    assert _values(store, vault) == SECRETS
    assert not any(s.encode() in _raw(tmp_path) for s in SECRETS) and not any(s in caplog.text for s in SECRETS)
    with store.transaction() as c:
        assert ONE.move(c, vault.cipher) == 0  # nothing left: a second run changes nothing


def test_a_value_the_vault_cannot_open_is_left_logged_and_moved_at_a_later_start(tmp_path, monkeypatch, caplog):
    from backend.tests.helpers.credentials import FakeKMS

    class Down:
        def decrypt(self, **kwargs):
            raise RuntimeError("KMS unreachable")

    kms, reach = FakeKMS(), {"kms": None}
    monkeypatch.setenv("TICO_CREDENTIAL_KMS_KEY", "test-key")
    monkeypatch.setattr("boto3.client", lambda name, *a, **k: reach["kms"] if name == "kms" else pytest.fail(name))
    store, vault = _older_server(tmp_path, CredentialCipher("test-key", kms))
    reach["kms"] = Down()
    caplog.set_level("INFO")
    _start(store)  # the start does not fail
    assert "category=vault_unavailable id=model_subscriptions:sub1" in caplog.text
    assert "id=gateway-connection:key" in caplog.text and "id=company-server:key" in caplog.text
    with store.read() as c:
        assert ONE.left(c) and MS.row(c, "sub1")["credential_id"] is None
    assert ONE.settle(store, CredentialCipher("test-key", Down())) == 0  # still down: left again
    # KMS is back: the next start moves them, and the owners read them from the vault.
    assert ONE.settle(store, CredentialCipher("test-key", kms)) == 4
    assert _values(store, Vault(store, CredentialCipher("test-key", kms))) == SECRETS
    with store.read() as c:
        assert not ONE.left(c)
    assert not any(s in caplog.text for s in SECRETS)


def test_service_rows_are_never_listed_opened_by_id_or_sent_to_a_computer(tmp_path):
    store, vault = _older_server(tmp_path)
    _start(store)
    with store.read() as c:
        for cid in ("model-subscription-sub1", "gateway-key", "company-server-key"):
            with pytest.raises(Problem) as found:
                Vault.row(c, cid)
            assert found.value.status == 404
            row = c.execute("SELECT * FROM credentials WHERE id=?", (cid,)).fetchone()
            with pytest.raises(Problem):
                vault.cipher.decrypt(c, row)  # the generic open: 404, as if absent
    with store.transaction() as c:
        insert(c, "humans", {"id": "ana", "name": "Ana", "email": "ana@example.test"})
        insert(c, "runners", {"id": "r1", "label": "Box", "owner_id": "ana", "token_hash": "h", "created_at": NOW})
        c.execute(
            "INSERT INTO computer_subscriptions(runner_id,runtime,subscription_id,set_by,updated_at,person) "
            "VALUES('r1','claude','sub1','human:ana',?,'')",
            (NOW,),
        )
        # GET runner-model-credentials, which runners of v0.6.0-rc.1 poll, sends a kept key only: never a subscription.
        assert MS.keys_for(c, vault, "r1", "claude") == ({"credentials": []}, [])
        assert MS.key_listing(c) == {}
        assert MS._given(c)[("r1", "claude")]["id"] == "sub1"  # still the computer's subscription


def test_a_change_writes_the_vault_and_a_cleared_record_leaves_no_copy(tmp_path):
    store, vault = _older_server(tmp_path)  # not moved: each write moves its own
    who = type("Who", (), {"actor": "human:ana", "role": "owner"})()
    with store.transaction() as c:
        before = MS.row(c, "sub1")
        assert MS._keep_value(c, vault, before, TOKEN[:-1] + "d", "p", "human:ana", NOW) == "model-subscription-sub1"
        gateway_connection.clear(c, who, NOW)
        company_server._forget(c, "human:ana", "company_server.disconnected", company_server._row(c)["key"])
    with store.read() as c:
        kinds = {r[0] for r in c.execute("SELECT id FROM credentials WHERE kind='service'")}
        assert kinds == {"model-subscription-sub1"}
        assert gateway_connection.key(c, vault) == "" and company_server._row(c) == {}
        assert gateway_connection._row(c)["install_secret"].startswith("sealed:")  # kept as it was, still opens
        assert gateway_connection._secret(c, vault, gateway_connection._row(c), "install_secret") == INSTALL


# The model-key reads on the claim and turn path: each a seek by key, whatever sqlite_stat1 says.
STALE = [
    ("model_subscriptions", None, "2"),
    ("credentials", None, "2"),
    ("computer_subscriptions", None, "2"),
    ("registry_metadata", None, "2"),
]


def _plans(c, call):
    seen = []
    c.set_trace_callback(seen.append)
    try:
        call()
    finally:
        c.set_trace_callback(None)
    return {
        sql: [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, ("x",) * sql.count("?"))]
        for sql in dict.fromkeys(s for s in seen if s.lstrip().upper().startswith("SELECT"))
    }


@pytest.mark.parametrize("stats", [None, STALE], ids=["analysed", "stale"])
def test_the_turns_value_and_the_gateway_key_are_sought_by_key(tmp_path, stats):
    store, vault = _older_server(tmp_path)
    _start(store)
    path = store.settings.db_path
    with sqlite3.connect(path) as c:
        c.execute("ANALYZE")
        if stats is not None:
            c.execute("DELETE FROM sqlite_stat1")
            c.executemany("INSERT INTO sqlite_stat1(tbl,idx,stat) VALUES(?,?,?)", stats)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    sub = c.execute("SELECT * FROM model_subscriptions WHERE id='sub1'").fetchone()
    found = _plans(c, lambda: (MS.value_of(c, vault.cipher, sub), gateway_connection.target(c, vault)))
    reads = [p for sql, p in found.items() if "FROM credentials" in sql]
    assert reads and all(p == ["SEARCH credentials USING INDEX sqlite_autoindex_credentials_1 (id=?)"] for p in reads)
    kept = c.execute(
        f"EXPLAIN QUERY PLAN SELECT 1 FROM model_subscriptions WHERE id=? AND {MS.KEPT_KEY.format('')}", ("x",)
    )
    assert [r[3] for r in kept] == [
        "SEARCH model_subscriptions USING INDEX sqlite_autoindex_model_subscriptions_1 (id=?)"
    ]
    assert not [p for plan in found.values() for p in plan if p.startswith("SCAN")], found
