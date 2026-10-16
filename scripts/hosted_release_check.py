#!/usr/bin/env python3
"""Manual validation controls, separate from the immutable candidate.

Only ephemeral GitHub Ubuntu VMs may reclaim the fixed unused SDKs. Each VM runs one part of the candidate's own
release gate (TICO_RELEASE_PART, and TICO_RELEASE_SHARD for the Python tests); together the parts are the whole gate.
Never tags, publishes, deploys, or uses a production installation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

GIB = 1024 ** 3
UNUSED_SDKS = ("/usr/local/lib/android", "/usr/share/dotnet", "/opt/ghc", "/usr/local/.ghcup")
LOG_LIMIT = 5 * 1024 ** 2
NATIVE_PARTS = ("all", "python")   # the parts whose tests run the candidate's native runner and relay


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def inputs(env):
    sha, previous = env.get("CANDIDATE_SHA", ""), env.get("PREVIOUS_RELEASE", "")
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "candidate must be a full lowercase commit SHA")
    require(re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", previous), "previous must be an explicit stable release tag")
    return sha, previous


def selection(env):
    part, shard = env.get("TICO_RELEASE_PART", "all"), env.get("TICO_RELEASE_SHARD", "")
    require(part in ("all", "docker", "python", "browser"), "the release part must be all, docker, python or browser")
    require(not shard or (part == "python" and re.fullmatch(r"[1-9][0-9]*/[1-9][0-9]*", shard)),
            "a shard is K/N and only splits the python part")
    return part, shard


def hosted(env):
    require(env.get("GITHUB_ACTIONS") == "true" and env.get("TICO_HOSTED_ENVIRONMENT") == "github-hosted"
            and env.get("RUNNER_OS") == "Linux" and env.get("RUNNER_ARCH") == "X64"
            and platform.system() == "Linux" and platform.machine() == "x86_64",
            "requires an ephemeral GitHub-hosted Linux x64 VM; never run on a user or bot host")


def output(command, cwd=None):
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def source(root, sha):
    require(output(["git", "rev-parse", "HEAD"], root) == sha, "candidate HEAD differs from requested SHA")
    require(not output(["git", "status", "--porcelain", "--untracked-files=normal"], root), "candidate source is not clean")
    return output(["git", "rev-parse", "HEAD^{tree}"], root)


def capacity(root, minimum, env):
    hosted(env)  # Must precede every possible SDK removal.
    before, removed = shutil.disk_usage(root).free, []
    for name in UNUSED_SDKS:
        if shutil.disk_usage(root).free >= minimum:
            break
        path = Path(name)
        if not path.is_dir() or path.is_symlink() or path.resolve() != path or path.stat().st_uid != 0:
            continue
        subprocess.run(["sudo", "rm", "-rf", "--", name], check=True)
        removed.append(name)
    after = shutil.disk_usage(root).free
    result = {"free_before": before, "free_after": after, "required": minimum, "removed_unused_sdks": removed}
    print(json.dumps({"disk_preflight": result}), flush=True)
    require(after >= minimum, "insufficient ephemeral disk capacity; gate was not started")
    return result


def fingerprint(root, kind):
    if kind == "runner":
        paths = [root / "runner-rs" / name for name in ("build.rs", "Cargo.toml", "Cargo.lock")]
        paths += list((root / "runner-rs/src").rglob("*"))
        paths += list((root / "runner/harnesses").glob("*.toml"))
        named = [(p.relative_to(root).as_posix(), p) for p in paths if p.is_file()]
    else:
        crate = root / "relay-rs"
        paths = [crate / name for name in ("build.rs", "Cargo.toml", "Cargo.lock")]
        paths += list((crate / "src").iterdir())
        named = [(p.relative_to(crate).as_posix(), p) for p in paths if p.is_file()]
    digest = hashlib.sha256()
    for name, path in sorted(named):
        data = path.read_bytes()
        digest.update(f"{name}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()[:16]


def native_binaries(root):
    result = {}
    for kind, crate, name in (("runner", "runner-rs", "tico-runner"), ("relay", "relay-rs", "tico-screen-relay")):
        binary = root / crate / "target/release" / name
        actual = output([str(binary), "--build-id"]).split()[-1]
        expected = fingerprint(root, kind)
        require(actual == expected, kind + " native binary does not match the candidate source")
        result[kind] = {"path": str(binary), "build_id": actual, "expected_build_id": expected,
                        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        if kind == "runner":
            result[kind]["version"] = output([str(binary), "--version"])
    return result


def collect(temp, report):
    """Only bounded text logs from this gate's own temp directory, never home/config/DB."""
    budget = 50 * 1024 ** 2
    log_root = temp / "tico-release-gate"
    require(not log_root.is_symlink(), "gate log root must not be a link")
    for path in sorted(log_root.glob("tico-release-check-*/*.log")):
        if not path.is_file() or path.is_symlink() or path.parent.is_symlink() or budget <= 0:
            continue
        length = min(path.stat().st_size, LOG_LIMIT, budget)
        with path.open("rb") as stream:
            stream.seek(-length, os.SEEK_END)
            data = stream.read(length)
        (report / (path.parent.name + "-" + path.name)).write_bytes(data)
        budget -= len(data)


def log_line(log, line):
    log.write(line.encode())
    if log.tell() > LOG_LIMIT:
        log.seek(-LOG_LIMIT // 2, os.SEEK_END)
        tail = log.read()
        log.seek(0)
        log.truncate()
        log.write(tail)
    log.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run", "collect"))
    args = parser.parse_args()
    hosted(os.environ)
    sha, previous = inputs(os.environ)
    part, shard = selection(os.environ)
    temp = Path(os.environ["RUNNER_TEMP"])
    root = Path(os.environ["GITHUB_WORKSPACE"]) / "candidate"
    report = temp / "release-validation-report"
    report.mkdir(mode=0o700, exist_ok=True)
    if args.mode == "collect":
        collect(temp, report)
        return 0
    receipt = {"candidate_sha": sha, "previous_release": previous,
               "workflow_sha": os.environ["TICO_VALIDATION_WORKFLOW_SHA"],
               "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
               "architecture": platform.machine(), "python": platform.python_version(),
               "cpu_count": os.cpu_count(), "started_unix": time.time(), "mode": args.mode,
               "part": part, "shard": shard or None,
               "test_settings": {name: os.environ.get(name) for name in
                                 ("TICO_PYTHON_WORKERS", "TICO_UI_JOBS", "TICO_UI_SLOWDOWN", "TICO_BROWSER_CHANNEL")},
               "gate_started": False, "success": False}
    gate_clock = None
    def checkpoint():
        (report / (args.mode + ".json")).write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        receipt["control_tree"] = source(Path(__file__).resolve().parents[1], receipt["workflow_sha"])
        receipt["source_tree"] = source(root, sha)
        receipt["disk"] = capacity(root, (35 if args.mode == "prepare" else 30) * GIB, os.environ)
        receipt["docker"] = output(["docker", "info", "--format", "{{.ServerVersion}} {{.Architecture}} cpus={{.NCPU}} memory={{.MemTotal}}"])
        receipt["compose"] = output(["docker", "compose", "version", "--short"])
        require(not output(["docker", "ps", "-aq"]), "ephemeral Docker VM has existing containers")
        if args.mode == "prepare":
            receipt["success"] = True
            return 0
        gate_temp = temp / "tico-release-gate"
        gate_temp.mkdir(mode=0o700, exist_ok=True)
        env = {**os.environ, "TMPDIR": str(gate_temp)}
        if part in NATIVE_PARTS:
            receipt["native_binaries"] = native_binaries(root)
            env["TICO_SCREEN_RELAY_BIN"] = receipt["native_binaries"]["relay"]["path"]
        command = [sys.executable, "scripts/release_checks.py", "--release", "--previous", previous]
        if part != "all":
            command += ["--part", part] + (["--shard", shard] if shard else [])
        receipt.update({"command": command, "gate_start_requested": True, "gate_state": "starting"})
        checkpoint()  # Survives timeout/cancellation even when the finalizer cannot run.
        gate_clock = time.monotonic()
        with (report / "gate.log").open("w+b") as log:
            process = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            receipt.update({"gate_started": True, "gate_state": "running", "gate_pid": process.pid,
                            "gate_started_unix": time.time()})
            checkpoint()
            for line in process.stdout:
                log_line(log, line)
                print(line, end="", flush=True)
            code = process.wait()
        receipt["gate_exit"] = code
        receipt["gate_state"] = "finished"
        receipt["source_tree_after"] = source(root, sha)
        require(receipt["source_tree_after"] == receipt["source_tree"], "source tree changed during validation")
        receipt["success"] = code == 0
        return code
    except Exception as error:
        receipt["error"] = str(error)
        raise
    finally:
        receipt["finished_unix"] = time.time()
        if gate_clock is not None:
            receipt["gate_elapsed_seconds"] = round(time.monotonic() - gate_clock, 3)
        checkpoint()
        collect(temp, report)


if __name__ == "__main__":
    raise SystemExit(main())
