"""Get the Rust relay matching this checkout; release downloads are checksum verified."""
import fcntl
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def source_id(root=ROOT):
    crate = Path(root) / "relay-rs"
    paths = [crate / name for name in ("Cargo.toml", "Cargo.lock", "build.rs")]
    paths += list((crate / "src").glob("*"))
    digest = hashlib.sha256()
    for path in sorted(p for p in paths if p.is_file()):
        data = path.read_bytes()
        digest.update(f"{path.relative_to(crate).as_posix()}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()[:16]


def current(path, wanted):
    if not path.is_file() or not os.access(path, os.X_OK):
        return False
    try:
        result = subprocess.run([str(path), "--build-id"], capture_output=True, text=True, timeout=10)
        return result.returncode == 0 and result.stdout.strip() == wanted
    except (OSError, subprocess.SubprocessError):
        return False


def prepare(root=ROOT, env=None):
    env = dict(os.environ if env is None else env)
    root = Path(root)
    wanted = source_id(root)
    target = Path(env.get("CARGO_TARGET_DIR") or root / "relay-rs" / "target").resolve()
    downloaded = target / "dist" / wanted / "tico-screen-relay"
    candidates = [Path(env.get("TICO_SCREEN_RELAY_BIN") or shutil.which("tico-screen-relay") or "/nonexistent"),
                  target / "release" / "tico-screen-relay", downloaded]
    for path in candidates:
        if current(path, wanted):
            return path
    target.mkdir(parents=True, exist_ok=True)
    with (target / "screen-prepare.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for path in candidates:
            if current(path, wanted):
                return path
        from runner.engine import _get, asset
        from runner.release_update import checkout_release
        release = checkout_release(root)
        name = asset().replace("tico-runner-", "tico-screen-relay-")
        if release and name:
            base = f"https://github.com/ticoteam/tico/releases/download/v{release.lstrip('v')}/"
            try:
                sums = _get(base + "tico-screen-relay-SHA256SUMS", 65536).decode()
                expected = next(line.split()[0] for line in sums.splitlines() if line.split()[1:] == [name])
                data = _get(base + name, 64 << 20)
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError("screen relay checksum mismatch")
                downloaded.parent.mkdir(parents=True, exist_ok=True)
                part = downloaded.with_name(f".relay-{os.getpid()}")
                part.write_bytes(gzip.decompress(data))
                part.chmod(0o755)
                os.replace(part, downloaded)
                if current(downloaded, wanted):
                    return downloaded
            except (OSError, ValueError, StopIteration, EOFError):
                pass  # A source build can still supply the exact revision.
        cargo = shutil.which("cargo", path=env.get("PATH"))
        if not cargo:
            raise RuntimeError("No matching Rust screen relay. Install this release's binary or Rust 1.92+ to build it.")
        subprocess.run([cargo, "build", "--release", "--locked", "--manifest-path", str(root / "relay-rs/Cargo.toml"),
                        "--target-dir", str(target)], env=env, check=True, timeout=1800)
        if not current(candidates[1], wanted):
            raise RuntimeError("Rust screen relay does not match this checkout")
        return candidates[1]
