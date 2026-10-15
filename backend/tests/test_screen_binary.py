"""Only a checksummed native relay built from this checkout may start."""
import gzip
import hashlib

import pytest

from backend import screen_binary
from runner import engine, release_update


def test_native_download_checks_checksum_and_refuses_a_stale_source_fingerprint(tmp_path, monkeypatch):
    crate = tmp_path / "relay-rs"
    (crate / "src").mkdir(parents=True)
    for name in ("build.rs", "Cargo.toml", "Cargo.lock", "src/main.rs"):
        (crate / name).write_text("fictional source\n")
    wanted = screen_binary.source_id(tmp_path)
    binary = f"#!/bin/sh\necho {wanted}\n".encode()
    data = gzip.compress(binary)
    asset = engine.asset().replace("tico-runner-", "tico-screen-relay-")
    checksum = "incorrect"
    def fetch(url, limit):
        return (f"{checksum}  {asset}\n".encode() if url.endswith("SHA256SUMS") else data)
    monkeypatch.setattr(engine, "_get", fetch)
    monkeypatch.setattr(release_update, "checkout_release", lambda root: "9.9.9")
    env = {"PATH": "", "CARGO_TARGET_DIR": str(tmp_path / "target")}
    with pytest.raises(RuntimeError, match="No matching Rust screen relay"):
        screen_binary.prepare(tmp_path, env)
    checksum = hashlib.sha256(data).hexdigest()
    path = screen_binary.prepare(tmp_path, env)
    assert screen_binary.current(path, wanted)
    (crate / "src/main.rs").write_text("new source\n")
    with pytest.raises(RuntimeError, match="No matching Rust screen relay"):
        screen_binary.prepare(tmp_path, env)
