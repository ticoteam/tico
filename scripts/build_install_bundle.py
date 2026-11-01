#!/usr/bin/env python3
"""Builds what a release publishes for scripts/install.sh: the script with its version baked in, the Windows (WSL 2)
installer infra/windows/install-wsl.ps1 likewise, the compose bundle for that exact tag, and SHA256SUMS over all three.
Used by .github/workflows/release.yml.

    python3 scripts/build_install_bundle.py --version v0.2.0 --output dist
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

PLACEHOLDER = "@TICO_VERSION@"
VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$")
# What `docker compose up` and the setup wizard need on a server; nothing else is shipped here.
# Ship compose.yaml verbatim so upgrades receive its current server environment forwarding.
# backend/redaction.py is standard library only: the wizard redacts what it offers to send to Tico support with it.
FILES = ("compose.yaml", ".env.example", "docker/runner.compose.yaml", "docker/runner-memory.sh", "scripts/tico-setup", "LICENSE", "NOTICE",
         "licenses/Apache-2.0.txt", "backend/redaction.py")
WIZARD_DIR = "setup"


def bundle_names(source: Path) -> list[str]:
    names = [f for f in FILES if (source / f).is_file()]
    for p in sorted((source / WIZARD_DIR).rglob("*.py")):
        rel = p.relative_to(source)
        if "tests" not in rel.parts and "__pycache__" not in rel.parts:
            names.append(rel.as_posix())
    # The same pure validator and transport as the app, without the app's imports.
    for p in sorted((source / "backend/company_file").glob("*")):
        if p.suffix in (".py", ".json"):
            names.append(p.relative_to(source).as_posix())
    return sorted(names)


def build_bundle(source: Path, version: str) -> bytes:
    """Reproducible: fixed order, times, owners, so the same tag always gives the same checksum."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        def add(name: str, data: bytes, mode: int) -> None:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime, info.uname, info.gname = len(data), mode, 0, "root", "root"
            tar.addfile(info, io.BytesIO(data))
        for name in bundle_names(source):
            path = source / name
            data = path.read_bytes()
            if name == ".env.example":
                data = re.sub(rb"(?m)^TICO_TAG=.*$", ("TICO_TAG=" + version).encode(), data)
            add(name, data, 0o755 if path.stat().st_mode & 0o111 else 0o644)
        # This is content provenance, not a capability granted by a version label.
        # Incomplete or modified source can still build fixtures, but cannot
        # advertise the same-installation operator protocol.
        # Running this file as a script otherwise searches scripts/, not the
        # repository containing the release builder's contract.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from setup.visual_contract import PROTOCOL, RUNTIME_FILES
        names = [n for n in bundle_names(source) if n.startswith("setup/") or n == "compose.yaml"]
        runtime = {n: hashlib.sha256((source / n).read_bytes()).hexdigest()
                   for n in RUNTIME_FILES if (source / n).is_file()}
        revision = ""
        try:
            selected = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                      capture_output=True, check=True, timeout=5).stdout.decode().strip()
            if re.fullmatch(r"[0-9a-f]{40}", selected) and len(runtime) == len(RUNTIME_FILES):
                for name in sorted(set(names) | set(RUNTIME_FILES)):
                    tracked = subprocess.run(["git", "-C", str(source), "show", selected + ":" + name],
                                             capture_output=True, check=True, timeout=5).stdout
                    if tracked != (source / name).read_bytes():
                        break
                else:
                    revision = selected
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        manifest = {"protocol": PROTOCOL, "revision": revision,
                    "files": {n: hashlib.sha256((source / n).read_bytes()).hexdigest() for n in names},
                    "runtime_files": runtime}
        add("installer-manifest.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n", 0o644)
        add("VERSION", (version.lstrip("v") + "\n").encode(), 0o644)
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def bake(script: str, version: str, name: str = "install.sh") -> str:
    if script.count(PLACEHOLDER) != 1:
        raise SystemExit(f"{name} must contain {PLACEHOLDER} exactly once")
    return script.replace(PLACEHOLDER, version)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", required=True, help="the tag, such as v0.2.0")
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)
    if not VERSION_RE.match(args.version):
        print(f"version must be a tag such as v1.2.3, not {args.version!r}", file=sys.stderr)
        return 2
    out: Path = args.output
    out.mkdir(parents=True, exist_ok=True)
    installer = out / "install.sh"
    installer.write_text(bake((args.source / "scripts/install.sh").read_text(), args.version))
    installer.chmod(0o755)
    windows = out / "install-wsl.ps1"
    windows.write_text(bake((args.source / "infra/windows/install-wsl.ps1").read_text(), args.version, windows.name))
    bundle = out / f"tico-bundle-{args.version}.tar.gz"
    bundle.write_bytes(build_bundle(args.source, args.version))
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in (installer, windows, bundle))
    (out / "SHA256SUMS").write_text(sums)
    print(sums, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
