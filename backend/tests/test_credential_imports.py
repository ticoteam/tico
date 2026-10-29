"""Importing a password manager's export as logins (docs/credential-vault.md, Import from a password manager).

The parser is tested as a pure function. The route tests stub `credentials.create_login` (the login kind is stored by
its own code, not this one), so they check what the import hands over: who may import, what is skipped, and that a
refusal never repeats a value.
"""
import json

import pytest

from backend import credential_files
from backend import credentials as creds
from backend.credential_files import FileError, identity, parse
from backend.credentials import CredentialCipher
from backend.tests.test_api import api, post  # noqa: F401  (fixture)
from backend.tests.test_credentials import FakeKMS

BITWARDEN = json.dumps({"encrypted": False, "folders": [], "items": [
    {"id": "1", "type": 1, "name": "Acme portal",
     "login": {"username": "ana@acme.example", "password": "  pw one  ",
               "totp": "otpauth://totp/Acme:ana?secret=JBSWY3DPEHPK3PXP&issuer=Acme",
               "uris": [{"match": None, "uri": "https://portal.acme.example/login"},
                        {"match": None, "uri": "androidapp://example.acme.app"},
                        {"match": None, "uri": "acme.example/signin"}]}},
    {"id": "2", "type": 2, "name": "Acme note", "notes": "a note, not a login"},
    {"id": "3", "type": 1, "name": "No site", "login": {"username": "ben", "password": "pw two", "uris": []}},
]})

KEEPASS = ('﻿"Group","Title","Username","Password","URL","Notes","TOTP"\n'
           '"Acme","Acme, Portal","ana@acme.example","pa,ss ""word"" ","https://portal.acme.example/login","",'
           '"otpauth://totp/Acme:ana?secret=JBSWY3DPEHPK3PXP"\n'
           '"Acme","Acme Mail","ben@acme.example","mail-pass","https://mail.acme.example","",""\n'
           '"Acme","No password","ben@acme.example","","https://wiki.acme.example","",""\n')


def test_bitwarden_export_keeps_logins_with_their_hosts_and_totp():
    rows, errors = parse("bitwarden", BITWARDEN)
    assert rows == [{"row": 1, "name": "Acme portal", "username": "ana@acme.example", "password": "  pw one  ",
                     "totp": "otpauth://totp/Acme:ana?secret=JBSWY3DPEHPK3PXP&issuer=Acme",
                     "sites": ["portal.acme.example", "acme.example"]}]
    assert errors == [{"row": 3, "reason": "no web address to log in to"}]


def test_keepassxc_csv_reads_quoted_fields_and_the_totp_column():
    rows, errors = parse("keepass", KEEPASS)
    assert [(r["name"], r["username"], r["password"], r["totp"], r["sites"]) for r in rows] == [
        ("Acme, Portal", "ana@acme.example", 'pa,ss "word" ', "otpauth://totp/Acme:ana?secret=JBSWY3DPEHPK3PXP",
         ["portal.acme.example"]),
        ("Acme Mail", "ben@acme.example", "mail-pass", None, ["mail.acme.example"]),
    ]
    assert errors == [{"row": 3, "reason": "no password"}]


def test_an_encrypted_bitwarden_export_is_refused_without_reading_it():
    encrypted = json.dumps({"encrypted": True, "data": "2.synthetic-ciphertext|iv|mac"})
    with pytest.raises(FileError) as refused:
        parse("bitwarden", encrypted)
    assert "encrypted" in str(refused.value) and "synthetic-ciphertext" not in str(refused.value)


def setup(api):
    api.app.state.store.settings.credential_kms_key = "test-key"
    api.app.state.store.settings.credential_admins = ("ana@acme.example", "ben@acme.example")
    api.app.state.vault.cipher = CredentialCipher("test-key", FakeKMS())


@pytest.fixture
def made(api, monkeypatch):
    """Stub the login writer: records what the import hands it, and never stores anything."""
    setup(api)
    calls = []

    def create_login(c, who, *, name, username, password, totp, sites, source="manual"):
        assert source == "import"
        calls.append({"name": name, "username": username, "password": password, "totp": totp, "sites": sites})
        return "login-%d" % len(calls)

    monkeypatch.setattr(creds, "create_login", create_login, raising=False)
    return calls


def test_import_keeps_new_logins_skips_duplicates_and_reports_what_it_cannot_store(api, made, monkeypatch):
    monkeypatch.setattr(credential_files, "existing_identities",
                        lambda c: {identity("Acme, Portal", "ana@acme.example", ["portal.acme.example"])})
    content = KEEPASS + '"Acme","Acme Mail","ben@acme.example","mail-pass","https://mail.acme.example","",""\n'
    result = post(api, "credentials/import", {"format": "keepass", "content": content})
    assert result == {"imported": 1, "skipped": 2, "errors": [{"row": 3, "reason": "no password"}]}
    assert made == [{"name": "Acme Mail", "username": "ben@acme.example", "password": "mail-pass", "totp": None,
                     "sites": ["mail.acme.example"]}]
    assert "mail-pass" not in json.dumps(result)
    with api.app.state.store.read() as c:
        events = str([tuple(r) for r in c.execute("SELECT * FROM events WHERE action='credential.imported'")])
    assert "mail-pass" not in events and "credential.imported" in events


def test_only_a_credential_administrator_imports_and_an_encrypted_export_is_refused(api, made):
    body = {"format": "bitwarden", "content": BITWARDEN}
    assert post(api, "credentials/import", body, "cara-test", expected=403)["error"]["code"] == "forbidden"
    assert made == []
    refused = post(api, "credentials/import", {"format": "bitwarden", "content": json.dumps({"encrypted": True})},
                   expected=422)
    assert refused["error"]["code"] == "import" and "encrypted" in refused["error"]["detail"]
