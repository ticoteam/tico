"""Public Actions artifacts contain authenticated ciphertext, never branded files."""
import shutil
from pathlib import Path

import pytest

from scripts.company_app_artifacts import pack, unpack


@pytest.fixture
def archives(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAURI_SIGNING_PRIVATE_KEY", "test-only-private-signing-secret")
    monkeypatch.setenv("GITHUB_REF_NAME", "v1.2.3")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    company = "a" * 64
    expected = {}
    for target, name in (("universal-apple-darwin", "Acme Tico.app.tar.gz"),
                         ("x86_64-pc-windows-msvc", "Acme Tico-setup.exe"),
                         ("x86_64-unknown-linux-gnu", "Acme Tico.AppImage")):
        bundle = Path(f"app/target/{target}/release/bundle")
        bundle.mkdir(parents=True)
        content = b"Acme Tico https://tico.example.com team.tico.env.12345678 acme-files"
        (bundle / name).write_bytes(content)
        (bundle / (name + ".sig")).write_text("signature")
        expected.update({name: content, name + ".sig": b"signature"})
        pack(company, target)
        encrypted = Path(f"encrypted/company-{company}-{target}")
        encrypted.mkdir(parents=True)
        shutil.move("out/bundles.enc", encrypted / "bundles.enc")
        cipher = (encrypted / "bundles.enc").read_bytes()
        for private in (name.encode(), b"Acme Tico", b"https://tico.example.com", b"team.tico.env.12345678", b"acme-files"):
            assert private not in cipher
    return company, expected


def test_ciphertext_round_trip_preserves_signed_bundles(archives):
    company, expected = archives
    unpack(company)
    assert {path.name: path.read_bytes() for path in Path("bundles").iterdir()} == expected


@pytest.mark.parametrize("failure", ["company", "tamper"])
def test_artifacts_reject_wrong_context_and_tampering_before_publish(archives, monkeypatch, failure):
    company, _ = archives
    if failure == "company":
        company = "b" * 64
    elif failure in ("tag", "run", "key"):
        monkeypatch.setenv({"tag": "GITHUB_REF_NAME", "run": "GITHUB_RUN_ID", "key": "TAURI_SIGNING_PRIVATE_KEY"}[failure], "other")
    else:
        source = next(Path("encrypted").glob("*/bundles.enc"))
        content = bytearray(source.read_bytes())
        content[50] ^= 1
        source.write_bytes(content)
    with pytest.raises(Exception):
        unpack(company)
    assert not Path("bundles").exists()


def test_missing_archive_and_path_traversal_are_rejected(archives):
    import io
    import os
    import tarfile
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from scripts.company_app_artifacts import MAGIC, context
    company, _ = archives
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("../escape")
        member.size = 4
        archive.addfile(member, io.BytesIO(b"evil"))
    key, aad = context(company)
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(aad)
    content = encryptor.update(buffer.getvalue()) + encryptor.finalize()
    source = next(Path("encrypted").glob("*/bundles.enc"))
    source.write_bytes(MAGIC + nonce + content + encryptor.tag)
    with pytest.raises(ValueError, match="member"):
        unpack(company)
    assert not Path("bundles").exists() and not Path("escape").exists()
    source.unlink()
    with pytest.raises(ValueError, match="Missing"):
        unpack(company)
