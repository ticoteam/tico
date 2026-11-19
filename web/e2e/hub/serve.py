"""A local server on a pruned, read-only copy of a real company, for building and testing the new app.

    python3 web/e2e/hub/serve.py --company tico-team|tidy [--refresh] [--from <path | s3 url>] [--port 8765]

Makes the copy once (or again with --refresh, or from --from), then serves it on 127.0.0.1 in rehearsal mode until
stopped (Ctrl-C or SIGTERM stops the server too). The copy lives outside the repository, under _next-data/<company>/
beside the worktrees (TICO_NEXT_DATA overrides). The source is only ever read:

- the company's latest backup, its Litestream replica in S3 (the default), restored with the pinned Litestream to a
  scratch file that is deleted once the copy is made; key files and attachments in the bucket are never fetched;
- or --from: a database file, a replica s3://bucket/prefix, or a backup bundle s3://bucket/backups/<name>.sqlite.

`python -m backend.devcopy` prunes it (nothing can act, every token revoked) and writes a fresh local sign-in token.
The last line printed once the server answers is `HUB {json}` (url, pid, token_file, db): web/e2e/hub/hub.ts reads it.
"""

import argparse
import hashlib
import json
import os
import platform
import shlex
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

W = Path(__file__).resolve().parents[3]
DATA = Path(os.environ.get("TICO_NEXT_DATA") or W.parent / "_next-data")
SOURCES = DATA / "sources.json"


def companies():
    """Where each company's backup is: kept beside the copies, never in the repository.

    {"tico-team": {"source": "s3://bucket/prefix", "profile": "...", "region": "..."}, ...}
    """
    if not SOURCES.exists():
        sys.exit(f"serve: no {SOURCES}; write it first (see this function's docstring), or pass --from")
    return json.loads(SOURCES.read_text())


def say(text):
    print(text, flush=True)


def python():
    venv = W / ".venv" / "bin" / "python"
    if not venv.exists():
        sys.exit(f"serve: no {venv}; run scripts/dev_setup.sh in {W} first")
    return str(venv)


def aws_env(profile):
    """The profile's credentials as environment variables, for Litestream (never printed)."""
    out = subprocess.run(
        ["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        sys.exit(f"serve: no AWS credentials for profile {profile}: {out.stderr.strip()}")
    creds = json.loads(out.stdout)
    env = {"AWS_ACCESS_KEY_ID": creds["AccessKeyId"], "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"]}
    if creds.get("SessionToken"):
        env["AWS_SESSION_TOKEN"] = creds["SessionToken"]
    return env


def litestream():
    """The Litestream the server image pins (docker/versions.env), fetched once and checked against its release sums."""
    pins = dict(line.split("=", 1) for line in (W / "docker/versions.env").read_text().split() if "=" in line)
    version = pins["LITESTREAM_VERSION"]
    binary = DATA / ".bin" / f"litestream-{version}"
    if binary.exists():
        return str(binary)
    system = {"Darwin": "darwin", "Linux": "linux"}[platform.system()]
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "AMD64": "x86_64"}[platform.machine()]
    name = f"litestream-{version}-{system}-{arch}.tar.gz"
    base = f"https://github.com/benbjohnson/litestream/releases/download/v{version}/"
    say(f"Fetching Litestream {version} ({name})")
    with urllib.request.urlopen(base + "checksums.txt", timeout=60) as r:
        sums = dict(reversed(line.split()) for line in r.read().decode().splitlines() if line.strip())
    with urllib.request.urlopen(base + name, timeout=300) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != sums.get(name):
        sys.exit(f"serve: {name} does not match the release checksum")
    binary.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=binary.parent) as scratch:
        archive = Path(scratch) / name
        archive.write_bytes(data)
        with tarfile.open(archive) as tar:
            tar.extract("litestream", scratch, filter="data")
        (Path(scratch) / "litestream").chmod(0o755)
        (Path(scratch) / "litestream").rename(binary)
    return str(binary)


def restore(replica, region, profile, scratch):
    """The replica's newest state as a database file in `scratch` (only reads the bucket)."""
    db = Path(scratch) / "source.sqlite"
    config = Path(scratch) / "litestream.yml"
    config.write_text(f"dbs:\n  - path: {db}\n    replica:\n      url: {replica}\n      region: {region}\n")
    say(f"Restoring the latest backup from {replica} (read only)")
    started = time.monotonic()
    env = {**os.environ, **aws_env(profile)}
    done = subprocess.run([litestream(), "restore", "-config", str(config), str(db)], env=env)
    if done.returncode != 0 or not db.exists():
        sys.exit(f"serve: could not restore {replica}")
    say(f"Restored {db.stat().st_size // 2**20} MB in {time.monotonic() - started:.0f} s")
    # devcopy revokes only job-backed attempts' tokens but then refuses any unrevoked one, so a company with job-less
    # runs fails it. Revoke those here, on this scratch file, until backend/devcopy.py does it itself.
    with closing(sqlite3.connect(db)) as c, c:
        c.execute("UPDATE attempts SET token_hash='devcopy-revoked:'||id WHERE job_id IS NULL AND token_hash IS NOT NULL")
    return db


def make_copy(company, source, profile, region, days, port):
    folder = DATA / company
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    db = folder / "hub.sqlite"
    (folder / "source.json").unlink(missing_ok=True)  # written last: without it the copy is unfinished
    bundle = source.startswith("s3://") and source.endswith(".sqlite")
    with tempfile.TemporaryDirectory(prefix=f".restore-{company}-", dir=DATA) as scratch:
        os.chmod(scratch, 0o700)
        src = restore(source, region, profile, scratch) if source.startswith("s3://") and not bundle else source
        cmd = [python(), "-m", "backend.devcopy", "--from", str(src), "--to", str(db), "--port", str(port)]
        cmd += ["--days", str(days), "--force"]
        if bundle:
            cmd += ["--profile", profile, "--region", region]
        say("Pruning it into a rehearsal copy (python -m backend.devcopy)")
        done = subprocess.run(cmd, cwd=W, capture_output=True, text=True)
        (folder / "devcopy.log").write_text(done.stdout + done.stderr)
        if done.returncode != 0:
            sys.exit(f"serve: devcopy failed (see {folder / 'devcopy.log'}):\n{done.stderr.strip()[-2000:]}")
        for line in done.stdout.splitlines():
            if line.startswith(("Emptied", "No owner", "Copy:")):
                say(line)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (folder / "source.json").write_text(json.dumps({"source": str(source), "made_at": stamp, "days": days}) + "\n")
    return db


def answering(url):
    try:
        with urllib.request.urlopen(url + "/healthz", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def serve(company, port):
    folder = DATA / company
    env = dict(os.environ)
    for line in (folder / "server.env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = shlex.split(value)[0] if value else ""
    url = f"http://127.0.0.1:{port}"
    env.update(TICO_PUBLIC_URL=url, TICO_RUNNER_URL=url, TICO_PORT=str(port), TICO_REHEARSAL="1", TICO_SCHEDULER="0")
    if answering(url):
        sys.exit(f"serve: something already answers on {url}; stop it or pass --port")
    log = open(folder / "server.log", "ab")
    server = subprocess.Popen(
        [python(), "-m", "backend.server", "--host", "127.0.0.1", "--port", str(port)],
        cwd=W,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    def stop(*_):
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(30)
            except subprocess.TimeoutExpired:
                server.kill()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    deadline = time.monotonic() + 600  # a first start runs the copy's pending migrations
    while not answering(url):
        if server.poll() is not None:
            tail = (folder / "server.log").read_text(errors="replace").splitlines()[-30:]
            sys.exit("serve: the server stopped while starting:\n" + "\n".join(tail))
        if time.monotonic() > deadline:
            stop()
        time.sleep(1)
    token = folder / "local-owner.token"
    db = folder / "hub.sqlite"
    say(f"Server: {url} (pid {server.pid}, rehearsal mode, log {folder / 'server.log'})")
    say(f"Sign in (browser, as the copy's owner): {url}/api/v2/local-signin?token=$(cat {token})")
    say(f'API: curl -H "Authorization: Bearer $(cat {token})" {url}/api/v2/me')
    say("HUB " + json.dumps({"company": company, "url": url, "pid": server.pid, "token_file": str(token), "db": str(db)}))
    server.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--company", required=True, help="a name in sources.json, or any name with --from")
    parser.add_argument("--from", dest="source", help="a database file, s3://bucket/prefix (Litestream) or a bundle")
    parser.add_argument("--refresh", action="store_true", help="make the copy again from the latest backup")
    parser.add_argument("--copy-only", action="store_true", help="make or refresh the copy, do not serve it")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--days", type=int, default=30, help="history kept, in days (devcopy --days)")
    parser.add_argument("--profile", help="AWS profile for an s3 source (default: the company's)")
    parser.add_argument("--region", help="AWS region for an s3 source (default: the company's)")
    args = parser.parse_args()
    folder = DATA / args.company
    if args.source or args.refresh or not (folder / "source.json").exists():
        known = {} if args.source else companies().get(args.company) or sys.exit(f"serve: {args.company} is not in {SOURCES}")
        make_copy(
            args.company,
            args.source or known["source"],
            args.profile or known.get("profile"),
            args.region or known.get("region"),
            args.days,
            args.port,
        )
    else:
        made = json.loads((folder / "source.json").read_text())
        say(f"Using the copy made {made['made_at']}; --refresh makes it again")
    if not args.copy_only:
        serve(args.company, args.port)


if __name__ == "__main__":
    main()
