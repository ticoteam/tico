#!/usr/bin/env python3
"""Offline integrity and Cargo-routing check for the GLib security backport."""

import hashlib
import json
from pathlib import Path
import tomllib


APP = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, detail):
    if not condition:
        raise RuntimeError(detail)


def check():
    provenance = json.loads((APP / "vendor/glib-provenance.json").read_text())
    require(
        provenance["archive_sha256"] == "233daaf6e83ae6a12a52055f568f9d7cf4671dabb78ff9560ab6da230ce00ee5",
        "Unexpected upstream package",
    )
    require(provenance["fix_commit"] == "b5a4071e439bef2b5eea76c3aa25e5ae84839e34", "Unexpected upstream fix")
    original = provenance["original_file_sha256"]
    patched = provenance["patched_file_sha256"]
    require(set(patched) == {"src/variant_iter.rs"}, "Unexpected modified source files")
    vendor = APP / "vendor/glib"
    files = {p.relative_to(vendor).as_posix(): p for p in vendor.rglob("*") if p.is_file()}
    require(set(files) == set(original), "Vendored package file list changed")
    for name, path in files.items():
        require(not path.is_symlink(), "Symlink: " + name)
        require(sha(path.read_bytes()) == patched.get(name, original[name]), "Changed file: " + name)
    data = files["src/variant_iter.rs"].read_bytes()
    before = data.replace(
        b"let mut p: *mut libc::c_char = std::ptr::null_mut();", b"let p: *mut libc::c_char = std::ptr::null_mut();", 1
    )
    before = before.replace(b"                &mut p,", b"                &p,", 1)
    require(sha(before) == original["src/variant_iter.rs"], "Backport differs from the exact upstream two-line fix")
    package = tomllib.loads(files["Cargo.toml"].read_text())["package"]
    require(
        package["name"] == "glib" and package["version"] == "0.18.5" and package["license"] == "MIT",
        "Package metadata changed",
    )
    app = tomllib.loads((APP / "Cargo.toml").read_text())
    require(app["patch"]["crates-io"]["glib"] == {"path": "vendor/glib", "version": "=0.18.5"}, "App patch changed")
    regression = APP / "tests/glib-backport"
    dependency = tomllib.loads((regression / "Cargo.toml").read_text())["dependencies"]["glib"]
    require((regression / dependency["path"]).resolve() == vendor, "Regression uses a different source")
    require(dependency["version"] == "=0.18.5", "Regression version changed")
    for lock in (APP / "Cargo.lock", regression / "Cargo.lock"):
        glib = [p for p in tomllib.loads(lock.read_text())["package"] if p["name"] == "glib"]
        require(len(glib) == 1 and glib[0]["version"] == "0.18.5", "GLib lock entry changed: " + str(lock))
        require("source" not in glib[0] and "checksum" not in glib[0], "Cargo lock still selects registry GLib")
    print(f"GLib backport verified: {len(files)} files, exact upstream fix, MIT license and both Cargo routes")


if __name__ == "__main__":
    check()
