"""`python -m runner_support run` is a shim: it execs this checkout's Rust runner (runner-rs/). launchd plists and systemd
units from every install start it, and the binary's path changes with the checkout, so no unit names the binary.

The binary is $TICO_RUNNER_BIN (the Docker image's), else this checkout's runner-rs/target/release/tico-runner, else
the one downloaded for this release (runner-rs/target/dist/<source id>/tico-runner), and only one whose `--version` is
this checkout's RUNNER_VERSION and whose `--build-id` is this checkout's source fingerprint (`source_id`, stamped in by
runner-rs/build.rs), so a checkout that moved on while the binary stayed put is never run. Without one, a checkout on
a release tag downloads that release's binary from GitHub (checked against the release's tico-runner-SHA256SUMS);
otherwise, with cargo on PATH, it is built now, behind a lock. When neither works the shim says why and exits 75, so
launchd or systemd starts it again. `"engine": "python"` in runner.json and TICO_RUNNER_ENGINE=python are ignored with
a warning: there is no Python runner any more.

`python -m runner_support.engine prepare` gets the binary ready (scripts/tico install, a release update) and exits non-zero
with the reason when it cannot; `python -m runner_support.engine path` prints the ready binary's path.
"""

import fcntl
import gzip
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zlib
from pathlib import Path

from .version import RUNNER_VERSION

ROOT = Path(__file__).resolve().parents[1]
BUILD_TIMEOUT_S = 1800
RELEASES = "https://github.com/ticoteam/tico/releases"
SUMS = "tico-runner-SHA256SUMS"
FETCH_TIMEOUT_S = 30
MAX_BINARY_BYTES = 64 << 20
RETRY_EXIT = 75               # EX_TEMPFAIL: the supervisor starts the shim again


def log(line):
    """To stderr: `doctor` and `enroll` print JSON on stdout, and a unit's stderr goes to the same log."""
    print(time.strftime("%Y-%m-%d %H:%M:%S") + " " + line, file=sys.stderr, flush=True)


def ignored(config, env):
    """Warn once per start about a Python-engine setting left from an older release."""
    if str(env.get("TICO_RUNNER_ENGINE") or "").strip().lower() == "python":
        log("Tico runner: TICO_RUNNER_ENGINE=python is ignored; the Python runner was removed")
    if str((config or {}).get("engine") or "").strip().lower() == "python":
        log('Tico runner: "engine": "python" in runner.json is ignored; the Python runner was removed')


def _ask(binary, flag):
    """The last word `binary flag` prints, or "" when it fails (a binary from before --build-id fails)."""
    try:
        out = subprocess.run([str(binary), flag], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    words = out.stdout.split() if out.returncode == 0 else []
    return words[-1] if words else ""


def version_of(binary):
    return _ask(binary, "--version")


def source_id(root):
    """The source fingerprint runner-rs/build.rs stamps into the binary, or "" when this tree has no runner-rs:
    the first 16 hex digits of the SHA-256 of `path \0 length \0 bytes` for every file of runner-rs/build.rs,
    Cargo.toml, Cargo.lock, runner-rs/src/** and harnesses/*.toml, sorted by path from `root`."""
    root = Path(root)
    if not (root / "runner-rs" / "src").is_dir():
        return ""
    paths = [root / "runner-rs" / name for name in ("build.rs", "Cargo.toml", "Cargo.lock")]
    paths += [p for p in (root / "runner-rs" / "src").rglob("*")]
    paths += list((root / "harnesses").glob("*.toml"))
    digest = hashlib.sha256()
    for name, path in sorted((p.relative_to(root).as_posix(), p) for p in paths if p.is_file()):
        data = path.read_bytes()
        digest.update(f"{name}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()[:16]


def current(binary, root):
    """Why `binary` cannot run this checkout, or "" when it can. The fingerprint applies to $TICO_RUNNER_BIN too:
    the image builds it from the tree it ships, so there it always matches."""
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return f"{binary} is missing"
    found = version_of(binary)
    if found != RUNNER_VERSION:
        return f"{binary} is version {found or 'unknown'}, this checkout is {RUNNER_VERSION}"
    wanted = source_id(root)
    if wanted and _ask(binary, "--build-id") != wanted:
        return f"{binary} was built from other runner-rs sources"
    return ""




def build(root, env):
    """Build the checkout's release binary, waiting for it; two shims wait on one build. "" when it is built, else why."""
    cargo = shutil.which("cargo", path=env.get("PATH"))
    if not cargo:
        return "cargo is not on PATH to build one"
    crate = Path(root) / "runner-rs"
    (crate / "target").mkdir(parents=True, exist_ok=True)
    with open(crate / "target" / "tico-runner-build.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not current(crate / "target" / "release" / "tico-runner", root):
            return ""                        # another shim built it while this one waited
        log("Tico runner: building the Rust runner (cargo build --release); this takes a few minutes once")
        try:
            done = subprocess.run([cargo, "build", "--release", "--locked", "-q"], cwd=crate, env=env,
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=BUILD_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"cargo build failed ({exc})"
        if done.returncode:
            return "cargo build failed: " + (done.stderr.strip().splitlines() or ["no output"])[-1][:300]
        problem = current(crate / "target" / "release" / "tico-runner", root)
        return f"the build does not fit this checkout ({problem})" if problem else ""


def asset(system=sys.platform, machine=None):
    """This computer's release asset name ("tico-runner-macos-arm64.gz"), or "" when no release builds one."""
    name = {"darwin": "macos", "linux": "linux"}.get(system)
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}.get(
        (machine or platform.machine()).lower())
    return f"tico-runner-{name}-{arch}.gz" if name and arch else ""


def dist(root):
    """Where a downloaded binary for this tree lives: one folder per source fingerprint, so a rollback finds its own."""
    return Path(root) / "runner-rs" / "target" / "dist" / (source_id(root) or "none") / "tico-runner"


def candidates(env, root):
    given = str(env.get("TICO_RUNNER_BIN") or "").strip()
    return [*([Path(given)] if given else []), Path(root) / "runner-rs" / "target" / "release" / "tico-runner", dist(root)]


def ready(env, root):
    """The first current binary, or None."""
    return next((binary for binary in candidates(env, root) if not current(binary, root)), None)


def _get(url, limit):
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as reply:
        data = reply.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url} is larger than {limit} bytes")
    return data


def fetch(root, env, release=None):
    """Download this release's binary, checked against the release's checksums, into `dist(root)`. Only a checkout on
    a release tag has one to download. "" when it is there and current, else why."""
    if release is None:
        from .release_update import checkout_release
        release = checkout_release(root)
    name = asset()
    if not release:
        return "this checkout is not on a release tag, so there is no release binary to download"
    if not name:
        return f"no release binary is built for {sys.platform} {platform.machine()}"
    if not source_id(root):
        return "this checkout has no runner-rs sources"
    base = f"{(env.get('TICO_RUNNER_RELEASES') or RELEASES).rstrip('/')}/download/v{release.lstrip('v')}/"
    target = dist(root)
    try:
        sums = _get(base + SUMS, 1 << 16).decode()
        want = next((line.split()[0] for line in sums.splitlines() if line.split()[1:] in ([name], ["*" + name])), "")
        if not want:
            raise ValueError(f"{name} is not in this release")
        data = _get(base + name, MAX_BINARY_BYTES)
        if hashlib.sha256(data).hexdigest() != want:
            raise ValueError(f"{name} does not match its checksum")
        binary = gzip.decompress(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(f".tico-runner.{os.getpid()}")
        part.write_bytes(binary)
        part.chmod(0o755)
        os.replace(part, target)
    except (OSError, ValueError, EOFError, zlib.error) as exc:
        return f"could not download the Rust runner for {release} ({exc})"
    problem = current(target, root)
    if problem:
        return f"the downloaded Rust runner for {release} does not fit this checkout ({problem})"
    log(f"Tico runner: downloaded the Rust runner for {release}")
    return ""


def prepare(root, env):
    """A current binary for this checkout, downloading or building one if need be: (path, "") or (None, why)."""
    binary = ready(env, root)
    if binary:
        return binary, ""
    missing = current(candidates(env, root)[0], root)
    crate = Path(root) / "runner-rs"
    if not crate.is_dir():
        return None, f"no Rust runner here ({missing}) and no runner-rs sources to get one for"
    (crate / "target").mkdir(parents=True, exist_ok=True)
    with open(crate / "target" / "tico-runner-prepare.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        binary = ready(env, root)          # another shim got it while this one waited
        if binary:
            return binary, ""
        fetched = fetch(root, env)
        if not fetched:
            return dist(root), ""
        built = build(root, env)
        if not built:
            return crate / "target" / "release" / "tico-runner", ""
    return None, f"no Rust runner for this checkout ({missing}); {fetched}; {built}"


def read_config(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def exec_binary(argv, config_path=None, env=None, execv=os.execve, root=ROOT):
    """Become the Rust runner with `argv` (its arguments, --config included). Never returns: when no binary can be
    had it exits RETRY_EXIT, so launchd or systemd tries again shortly."""
    env = dict(os.environ if env is None else env)
    if config_path is not None:
        ignored(read_config(config_path), env)
    binary, why = prepare(root, env)
    if binary is None:
        log(f"Tico runner: cannot start: {why}. Trying again shortly.")
        raise SystemExit(RETRY_EXIT)
    env.setdefault("TICO_ROOT", str(root))
    env.setdefault("TICO_RUNNER_PYTHON", sys.executable)
    execv(str(binary), [str(binary), *argv], env)
    raise SystemExit(RETRY_EXIT)            # only a stand-in execv returns


def main(argv=None):
    """`python -m runner_support.engine prepare|path`: get this checkout's Rust runner now / print where it is."""
    args = sys.argv[1:] if argv is None else argv
    if args not in (["prepare"], ["path"]):
        print("usage: python -m runner_support.engine prepare|path", file=sys.stderr)
        return 2
    env = dict(os.environ)
    if args == ["path"]:
        binary = ready(env, ROOT)
        if not binary:
            print(f"no Rust runner for this checkout ({current(candidates(env, ROOT)[0], ROOT)})", file=sys.stderr)
            return 1
        print(binary)
        return 0
    binary, why = prepare(ROOT, env)
    if not binary:
        print(f"Tico runner: no Rust runner for this checkout: {why}", file=sys.stderr)
        return 1
    print(f"Rust runner ready: {binary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
