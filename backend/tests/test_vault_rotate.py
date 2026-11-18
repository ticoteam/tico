"""`python -m backend.manage vault-rotate` (backend/vault_rotate.py) and the company KMS context of the credential vault.

Synthetic only: a fake KMS that, like AWS, opens a wrapped key only under the encryption context it was wrapped with, and
synthetic secrets. Nothing reaches AWS."""

import dataclasses
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend import vault_rotate
from backend.credentials import (
    KEY_FILES_RETIRED,
    ROTATED_AT,
    CredentialCipher,
    Vault,
    keep_service,
    key_files_retired,
    kms_context,
)
from backend.store import Problem

KMS_KEY = "arn:aws:kms:eu-west-1:000000000000:key/synthetic"
PEM = "synthetic-pem-not-a-key"


class ContextKMS:
    """A KMS key that binds each wrapped key to its KeyId and encryption context, as AWS does: a decrypt under another
    context fails. `blocked` stands for KMS unreachable or a denied call."""

    def __init__(self):
        self.master, self.blocked = os.urandom(32), False

    def _aad(self, key_id, context):
        return json.dumps([key_id, sorted(context.items())]).encode()

    def _check(self):
        if self.blocked:
            raise ConnectionError("KMS unreachable (synthetic)")

    def encrypt(self, KeyId, Plaintext, EncryptionContext):
        self._check()
        nonce = os.urandom(12)
        return {
            "CiphertextBlob": nonce + AESGCM(self.master).encrypt(nonce, Plaintext, self._aad(KeyId, EncryptionContext))
        }

    def generate_data_key(self, KeyId, KeySpec, EncryptionContext):
        assert KeySpec == "AES_256"
        key = os.urandom(32)
        return {"Plaintext": key, **self.encrypt(KeyId=KeyId, Plaintext=key, EncryptionContext=EncryptionContext)}

    def decrypt(self, CiphertextBlob, EncryptionContext, KeyId=None):
        self._check()
        blob = bytes(CiphertextBlob)
        return {"Plaintext": AESGCM(self.master).decrypt(blob[:12], blob[12:], self._aad(KeyId, EncryptionContext))}


def kms_settings(store, company="acme"):
    return dataclasses.replace(store.settings, credential_kms_key=KMS_KEY, credential_kms_context=company)


def folder(store):
    return Path(store.settings.db_path).parent


def file_cipher(store):
    """The vault as a hosted company has it today: a plain data key in credential.key, no KMS."""
    return CredentialCipher("", key_file=folder(store) / "credential.key", company="", access=("", "", ""))


def github(store, settings, vault=None):
    from backend.directory import Directory
    from backend.github_app import GitHubApp

    app = GitHubApp(settings, store, vault)
    return app, Directory(settings, store, app)


def fill(store):
    """A company with a key file and stored secrets of each family: two vault rows (one a finance payee's bank details
    with its fingerprint), a sealed value, and the GitHub App and a directory source sealed with github-app.key."""
    from backend.finance.core.feeds import details_hash

    cipher = file_cipher(store)
    app, directory = github(store, store.settings)
    details = {"accountNumber": "000123456789", "routingNumber": "000000001"}
    with store.transaction() as c:
        keep_service(c, cipher, "gateway-key", "Tico AI Gateway key", "gw-synthetic-123", "tico", "gateway")
        keep_service(
            c, cipher, "fin-details", "Payee", json.dumps(details, sort_keys=True), "tico", "finance", reference=False
        )
        c.execute(
            "INSERT INTO support_tickets(id,actor,created_at,message,hq_id,hq_secret) VALUES('t1','human:ana','2026-11-18',"
            "'help','hq1',?)",
            (cipher.seal(c, "support_tickets:t1", "hq-synthetic-secret"),),
        )
        old = cipher.key(c)
        import hashlib
        import hmac

        fp = hmac.new(old, b"tico-finance:payee:" + details_hash("ach", details).encode(), hashlib.sha256).hexdigest()
        c.execute(
            "INSERT INTO fin_recipients(id,name,method,details_hash,details_ref,created_at,created_by) "
            "VALUES('r1','Payee','ach',?,'fin-details','2026-11-18','tico')",
            (fp,),
        )
        app.save(
            c,
            "human:ana",
            "acme",
            True,
            {
                "id": 1,
                "slug": "acme-tico",
                "client_id": "cid",
                "pem": PEM,
                "owner": {"type": "Organization", "login": "acme"},
            },
        )
        directory.put_credentials(c, "human:ana", "entra", {"client_secret": "entra-synthetic"}, "app c in t")
    return {name: (folder(store) / name).read_bytes() for name in ("credential.key", "github-app.key")}


def server_vault(store, kms, company="acme"):
    """The server after the step: KMS settings, no key file."""
    for name, value in (("credential_kms_key", KMS_KEY), ("credential_kms_context", company)):
        setattr(store.settings, name, value)
    return Vault(store, cipher=CredentialCipher.for_settings(store.settings, kms))


def all_open(store, kms):
    vault = server_vault(store, kms)
    app, directory = github(store, store.settings, vault)
    with store.read() as c:
        rows = {r["id"]: r for r in c.execute("SELECT * FROM credentials")}
        ticket = c.execute("SELECT hq_secret FROM support_tickets WHERE id='t1'").fetchone()[0]
        return {
            "gateway": vault.cipher.open_service(c, rows["gateway-key"]),
            "ticket": vault.cipher.unseal(c, "support_tickets:t1", ticket),
            "pem": app.private_key(c, app.row(c)),
            "entra": directory.credentials(c, "entra")["client_secret"],
            "details_hash": c.execute("SELECT details_hash FROM fin_recipients WHERE id='r1'").fetchone()[0],
        }


def test_a_migrated_company_keeps_every_secret_and_loses_both_key_files(company):
    old = fill(company)
    kms = ContextKMS()
    report, code = vault_rotate.run(kms_settings(company), kms)
    assert (code, report["status"], report["failed"]) == (0, "done", {})
    assert sorted(report["deleted"]) == ["credential.key", "github-app.key"]
    assert report["resealed"] == {
        "credentials:service": 2,
        "support_tickets": 1,
        "github_app": 1,
        "directory_credentials": 1,
        "fin_recipients.details_hash": 1,
    }
    assert not any((folder(company) / name).exists() for name in old)
    opened = all_open(company, kms)
    assert {k: opened[k] for k in ("gateway", "ticket", "pem", "entra")} == {
        "gateway": "gw-synthetic-123",
        "ticket": "hq-synthetic-secret",
        "pem": PEM,
        "entra": "entra-synthetic",
    }
    with company.read() as c:
        assert key_files_retired(c)
        assert c.execute("SELECT kms_key FROM credential_keys").fetchone()[0] == KMS_KEY
        # The payee fingerprint is the one Finance computes now, with the fresh key: a duplicate is still refused.
        from backend.finance.core.feeds import details_hash
        from backend.finance.store import tico

        kept, tico._VAULT[:] = list(tico._VAULT), [server_vault(company, kms)]
        try:
            details = {"accountNumber": "000123456789", "routingNumber": "000000001"}
            assert opened["details_hash"] == tico.fingerprint(c, "payee", details_hash("ach", details))
        finally:
            tico._VAULT[:] = kept
    # The old key files and a database copied after the step open nothing.
    with company.read() as c:
        row = c.execute("SELECT * FROM credentials WHERE id='gateway-key'").fetchone()
        ticket = c.execute("SELECT hq_secret FROM support_tickets").fetchone()[0]
        app = c.execute("SELECT * FROM github_app").fetchone()
    for key in old.values():
        with pytest.raises(Exception):
            AESGCM(key).decrypt(row["nonce"], row["ciphertext"], b"tico-credential:v1:gateway-key")
        with pytest.raises(Exception):
            vault_rotate._open_sealed(key, "support_tickets:t1", ticket)
        with pytest.raises(Exception):
            AESGCM(key).decrypt(app["nonce"], app["ciphertext"], b"tico-github-app:v1")
    again, code = vault_rotate.run(kms_settings(company), kms)
    assert (code, again["status"], again["deleted"]) == (0, "already_done", [])


def test_a_secret_that_does_not_open_stops_the_step_before_anything_changes(company):
    fill(company)
    with company.transaction() as c:
        c.execute(
            "UPDATE support_tickets SET hq_secret=?",
            (vault_rotate._seal(os.urandom(32), "support_tickets:t1", b"sealed with a lost key"),),
        )
        before = c.execute("SELECT kms_key,wrapped_key FROM credential_keys").fetchone()
        gateway = bytes(c.execute("SELECT ciphertext FROM credentials WHERE id='gateway-key'").fetchone()[0])
    report, code = vault_rotate.run(kms_settings(company), ContextKMS())
    assert (code, report["status"], report["failed"], report["deleted"]) == (3, "stopped", {"support_tickets": 1}, [])
    assert "nothing was changed" in report["reason"]
    assert (folder(company) / "credential.key").exists() and (folder(company) / "github-app.key").exists()
    with company.read() as c:
        assert tuple(c.execute("SELECT kms_key,wrapped_key FROM credential_keys").fetchone()) == tuple(before)
        assert bytes(c.execute("SELECT ciphertext FROM credentials WHERE id='gateway-key'").fetchone()[0]) == gateway
        assert c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (ROTATED_AT,)).fetchone() is None


def test_a_sealed_value_the_registry_does_not_know_stops_the_step(company):
    fill(company)
    cipher = file_cipher(company)
    with company.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata VALUES('someone-new',?)",
            (json.dumps({"token": cipher.seal(c, "someone-new", "synthetic")}),),
        )
        # Prose that merely starts with the prefix is not a sealed value.
        c.execute("INSERT INTO registry_metadata VALUES('a-note','sealed:v1: values are what CredentialCipher makes')")
    report, code = vault_rotate.run(kms_settings(company), ContextKMS())
    assert (code, report["status"], report["failed"]) == (3, "stopped", {"registry_metadata.value": 1})
    assert "not in the step's registry" in report["reason"]
    assert (folder(company) / "credential.key").exists()


def test_interrupted_after_the_commit_the_next_run_finishes_with_nothing_lost(company, monkeypatch):
    fill(company)
    kms = ContextKMS()
    real = vault_rotate._verify

    def crash(db, cipher):
        raise vault_rotate.Stop("synthetic interruption")

    monkeypatch.setattr(vault_rotate, "_verify", crash)
    report, code = vault_rotate.run(kms_settings(company), kms)
    assert (code, report["deleted"]) == (3, [])
    assert (folder(company) / "credential.key").exists()
    with company.read() as c:
        assert c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (ROTATED_AT,)).fetchone()
        assert not key_files_retired(c)
    monkeypatch.setattr(vault_rotate, "_verify", real)
    report, code = vault_rotate.run(kms_settings(company), kms)
    assert (code, report["status"], report["resealed"]) == (0, "done", {})  # resumed: no second rotation
    assert "resumed" in report["reason"]
    assert all_open(company, kms)["pem"] == PEM
    with company.read() as c:
        assert c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (KEY_FILES_RETIRED,)).fetchone()


def test_one_companys_context_cannot_unwrap_anothers_key():
    kms = ContextKMS()  # the cell's one KMS key
    a = CredentialCipher(KMS_KEY, kms, company="company-a", access=("", "", ""))
    b = CredentialCipher(KMS_KEY, kms, company="company-b", access=("", "", ""))
    assert b.context == {"application": "tico-credentials", "company": "company-b"}
    assert kms_context("") == {"application": "tico-credentials"}
    key, wrapped = b.generate()
    assert b.unwrap(wrapped) == key
    with pytest.raises(Exception):
        a.unwrap(wrapped)


def test_kms_blocked_reads_fail_as_vault_unavailable_and_the_step_deletes_nothing(company):
    kms = ContextKMS()
    vault = server_vault(company, kms)
    with company.transaction() as c:
        sealed = vault.cipher.seal(c, "support_tickets:t9", "synthetic")
    kms.blocked = True
    cold = CredentialCipher.for_settings(company.settings, kms)  # a restarted server: nothing cached
    with company.read() as c:
        with pytest.raises(Problem) as refused:
            cold.unseal(c, "support_tickets:t9", sealed)
    assert (refused.value.code, refused.value.status) == ("vault_unavailable", 503)
    # Another company's server (its own context) cannot read this one's key either: the same clean refusal.
    kms.blocked = False
    other = CredentialCipher(KMS_KEY, kms, company="someone-else", access=("", "", ""))
    with company.read() as c:
        with pytest.raises(Problem) as refused:
            other.key(c)
    assert refused.value.code == "vault_unavailable"
    kms.blocked = True
    (folder(company) / "github-app.key").write_bytes(os.urandom(32))
    report, code = vault_rotate.run(kms_settings(company), kms)
    assert (code, report["status"], report["deleted"]) == (3, "stopped", [])
    assert "vault_unavailable" in report["reason"]
    assert (folder(company) / "github-app.key").exists()


def test_a_new_company_with_kms_never_makes_a_key_file(company):
    kms = ContextKMS()
    vault = server_vault(company, kms)
    app, directory = github(company, company.settings, vault)
    with company.transaction() as c:
        keep_service(c, vault.cipher, "gateway-key", "Tico AI Gateway key", "gw-synthetic", "tico", "gateway")
        vault.cipher.seal(c, "support_tickets:t1", "synthetic")
        app.save(c, "human:ana", "acme", True, {"id": 1, "slug": "s", "client_id": "c", "pem": PEM})
        directory.put_credentials(c, "human:ana", "entra", {"client_secret": "entra-synthetic"}, "hint")
    assert not (folder(company) / "credential.key").exists()
    assert not (folder(company) / "github-app.key").exists()
    with company.read() as c:
        assert c.execute("SELECT kms_key FROM credential_keys").fetchone()[0] == KMS_KEY
        assert app.private_key(c, app.row(c)) == PEM


def test_no_kms_key_is_a_configuration_error_naming_the_setting(company, monkeypatch, capsys):
    report, code = vault_rotate.run(company.settings, ContextKMS())
    assert (code, report["status"]) == (2, "stopped")
    assert "TICO_CREDENTIAL_KMS_KEY" in report["reason"]
    from backend import manage

    monkeypatch.setenv("TICO_DB", str(company.settings.db_path))
    monkeypatch.delenv("TICO_CREDENTIAL_KMS_KEY", raising=False)
    monkeypatch.delenv("TICO_TEAM_NAME", raising=False)
    monkeypatch.delenv("TICO_COMPANY_NAME", raising=False)
    assert manage.main(["vault-rotate", "--json"]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "stopped" and "TICO_CREDENTIAL_KMS_KEY" in printed["reason"]


def test_the_kms_client_uses_the_companys_own_access_key_and_never_half_a_pair(monkeypatch):
    import boto3

    made = []
    monkeypatch.setattr(boto3, "client", lambda service, **kw: made.append((service, kw)) or object())
    CredentialCipher(KMS_KEY, company="acme", access=("AKIASYNTHETIC", "synthetic-secret", "eu-west-1")).kms_client()
    service, kw = made[0]
    assert service == "kms"
    assert (kw["aws_access_key_id"], kw["aws_secret_access_key"], kw["region_name"]) == (
        "AKIASYNTHETIC",
        "synthetic-secret",
        "eu-west-1",
    )
    with pytest.raises(Problem) as refused:
        CredentialCipher(KMS_KEY, access=("AKIASYNTHETIC", "", "")).kms_client()
    assert refused.value.code == "vault_unavailable"
    CredentialCipher(KMS_KEY, access=("", "", "")).kms_client()
    assert "aws_access_key_id" not in made[1][1]
