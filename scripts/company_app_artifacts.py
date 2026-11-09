"""Encrypt CI bundles before they enter publicly readable Actions artifacts.

Streaming AES-GCM uses the existing private updater signing secret with a separate
key derivation domain. Authentication binds each archive to its company, tag and run.
Plaintext stays on ephemeral build/publisher disks and in the company's bucket.
"""

import hmac
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"TICOAPP1"
LIMIT = 2 * 1024**3
NAME = re.compile(r"[\w][\w. +()-]{0,200}")


def context(company):
    secret = os.environ.get("TAURI_SIGNING_PRIVATE_KEY", "")
    if not secret.strip():
        raise ValueError("Missing updater signing secret")
    key = hmac.digest(secret.encode(), b"tico-company-artifacts-v1", "sha256")
    aad = "\n".join((company, os.environ["GITHUB_REF_NAME"], os.environ["GITHUB_RUN_ID"])).encode()
    return key, aad


def pack(company, target):
    if not re.fullmatch(r"[a-z0-9_-]+", target):
        raise ValueError("Invalid target")
    if __package__:
        from .app_release import classify
    else:
        from app_release import classify
    files = classify(f"app/target/{target}/release/bundle")["files"]
    names = [p.name for p in files]
    if (
        not files
        or len(files) > 32
        or len(set(names)) != len(names)
        or any(not NAME.fullmatch(name) for name in names)
        or sum(p.stat().st_size for p in files) > LIMIT
    ):
        raise ValueError("Invalid company bundles")
    key, aad = context(company)
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(aad)
    Path("out").mkdir(exist_ok=True)
    with Path("out/bundles.enc").open("wb") as output:
        output.write(MAGIC + nonce)

        class Writer:
            def write(self, data):
                output.write(encryptor.update(data))
                return len(data)

        with tarfile.open(fileobj=Writer(), mode="w|") as archive:
            for path in files:
                archive.add(path, arcname=path.name, recursive=False)
        output.write(encryptor.finalize())
        output.write(encryptor.tag)


def unpack(company):
    key, aad = context(company)
    sources = list(Path("encrypted").glob("company-*/bundles.enc"))
    if len(sources) != 3:
        raise ValueError("Missing company platform archives")
    if sum(source.stat().st_size for source in sources) > LIMIT + 3 * 1024 * 1024:
        raise ValueError("Company archives exceed the disk budget")
    destination = Path("bundles")
    destination.mkdir(exist_ok=False)
    try:
        names, total = set(), 0
        for source in sources:
            size = source.stat().st_size
            if not 36 < size <= LIMIT + 1024 * 1024:
                raise ValueError("Invalid encrypted archive size")
            with source.open("rb") as stream, tempfile.TemporaryFile() as plain:
                if stream.read(8) != MAGIC:
                    raise ValueError("Invalid encrypted archive")
                nonce = stream.read(12)
                stream.seek(-16, 2)
                tag = stream.read(16)
                stream.seek(20)
                decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
                decryptor.authenticate_additional_data(aad)
                remaining = size - 36
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Truncated encrypted archive")
                    plain.write(decryptor.update(chunk))
                    remaining -= len(chunk)
                plain.write(decryptor.finalize())
                # Authenticate the entire archive before extracting a single file.
                plain.seek(0)
                with tarfile.open(fileobj=plain, mode="r|") as archive:
                    for member in archive:
                        total += member.size
                        if (
                            not member.isfile()
                            or not NAME.fullmatch(member.name)
                            or member.name in names
                            or len(names) >= 32
                            or total > LIMIT
                        ):
                            raise ValueError("Invalid company archive member")
                        names.add(member.name)
                        with archive.extractfile(member) as data, (destination / member.name).open("xb") as output:
                            shutil.copyfileobj(data, output, 1024 * 1024)
    except Exception:
        shutil.rmtree(destination)
        raise
