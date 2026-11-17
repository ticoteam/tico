#!/usr/bin/env python3
"""Replay a candidate's migrations on a private snapshot, entirely on the cell.

Run on the cell through SSM, with the candidate server image already pulled:
    sudo python3 scripts/migration_replay.py --container <server> --image <candidate-image>
Only table names, counts, timings and check results leave the replay.
"""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid

GIB = 1024**3

INSPECT = """
import json, sqlite3
from contextlib import closing
with closing(sqlite3.connect('file:/replay/hub.sqlite?mode=ro', uri=True)) as conn:
    # Integrity diagnostics and FK rows can contain customer values. Return counts only.
    integrity_errors = sum(row[0] != 'ok' for row in conn.execute('PRAGMA integrity_check'))
    foreign_keys = sum(1 for _ in conn.execute('PRAGMA foreign_key_check'))
    counts = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        counts[name] = conn.execute('SELECT count(*) FROM ' + quoted).fetchone()[0]
print(json.dumps(dict(integrity_errors=integrity_errors, foreign_keys=foreign_keys, counts=counts)))
"""

# The server's own order (Store.migrate): the floor preflight, then hubdb.connect runs the pending migrations.
MIGRATE = """
import logging
logging.basicConfig(level=logging.WARNING, format='%(levelname)s:%(name)s:%(message)s')
from contextlib import closing
from pathlib import Path
from backend import hubdb, migrations
migrations.preflight(Path('/replay/hub.sqlite'))
with closing(hubdb.connect('/replay/hub.sqlite')):
    pass
"""


class ReplayError(RuntimeError):
    """A replay step failed; its output may contain customer data and stays private."""


class SourceError(ReplayError):
    """A safe explanation of an unmet live-source prerequisite."""


def docker(*args, stdout=None, stderr=subprocess.DEVNULL):
    """The only Docker boundary. Never relay Docker or migration diagnostics."""
    result = subprocess.run(
        ["docker", *args], stdout=subprocess.PIPE if stdout is None else stdout, stderr=stderr, check=False
    )
    if result.returncode:
        raise ReplayError("Docker step failed")
    return result.stdout


def live_database(container):
    info = json.loads(docker("inspect", "--format", "{{json .}}", container))
    mounts = info["Mounts"]
    env = {}
    for entry in info["Config"].get("Env") or []:
        # A name without "=" is a Compose variable left unset, so the server does not have it either.
        if not isinstance(entry, str) or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        if not key.startswith(COPIED_ENV):
            continue
        # Docker env files cannot represent embedded newlines without changing the value.
        if any(char in entry for char in "\r\n\0") or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key):
            raise SourceError("live environment cannot be represented in an env file")
        env[key] = value
    source = next((mount.get("Source") for mount in mounts if mount.get("Destination") == "/data"), None)
    if not source or not Path(source).is_absolute():
        raise SourceError("container /data mount has no absolute host Source")
    configured = Path(os.path.normpath(env.get("TICO_DB") or "/data/hub.sqlite"))
    if not configured.is_absolute() or not configured.is_relative_to("/data"):
        raise SourceError("live database setting must be inside the /data mount")
    database = Path(source) / configured.relative_to("/data")
    size = 0
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(database) + suffix)
        try:
            with path.open("rb") as stream:
                # Include committed WAL pages, but not the shared-memory index.
                if suffix != "-shm":
                    size += os.fstat(stream.fileno()).st_size
        except OSError:
            raise SourceError(f"{path.name} is missing or not readable") from None
    return database, size, env, Path(source)


# The settings migrations read: Tico's own and AWS's (for KMS). The rest belong to the live image.
COPIED_ENV = ("TICO_", "AWS_")


def private_write(path, data):
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
        stream.write(data)


def replay_settings(database, env, scratch):
    """Copy Tico's and AWS's settings and the two keys resolved beside TICO_DB, without writing to the live mount.
    The live container's other variables (PATH, PYTHONPATH and the like) come from its own image, so the candidate
    image keeps its own."""
    env = {key: value for key, value in env.items() if key.startswith(COPIED_ENV)}
    env["TICO_DB"] = "/replay/hub.sqlite"
    env_file = scratch / "container.env"
    private_write(env_file, "".join(f"{key}={value}\n" for key, value in env.items()).encode())
    mounts = []
    for name in ("credential.key", "slack.key"):
        try:
            with (database.parent / name).open("rb") as stream:
                data = stream.read()
        except FileNotFoundError:
            continue  # An install that never sealed anything may not have key files.
        private_write(scratch / name, data)
        mounts.extend(["-v", f"{scratch / name}:/replay/{name}:ro"])
    return env_file, mounts


def warning_count(path):
    # Logging has a fixed severity prefix in MIGRATE; Python warnings include a source line and category.
    record = re.compile(r"^(?:WARNING|ERROR|CRITICAL|FATAL):|^.+:\d+: [\w.]+: ")
    with path.open(encoding="utf-8", errors="replace") as stream:
        return sum(bool(record.search(line)) for line in stream)


def snapshot(database, destination):
    # Read through SQLite so the copy participates in the live WAL's locking.
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst, pages=-1)


def disk_check(database_bytes, free_bytes, keep_free):
    required = database_bytes * 2 + keep_free
    return {
        "database_bytes": database_bytes,
        "free_bytes": free_bytes,
        "required_bytes": required,
        "ok": free_bytes >= required,
    }


def count_diff(before, after, allow_loss=()):
    common = before.keys() & after.keys()
    return {
        "dropped": sorted(before.keys() - after.keys()),
        "new": sorted(after.keys() - before.keys()),
        "changed": {
            name: {"before": before[name], "after": after[name]}
            for name in sorted(common)
            if before[name] != after[name]
        },
        "lost": sorted(name for name in common if after[name] < before[name] and name not in allow_loss),
    }


def verdict(before, after, migration_ok, allow_loss=()):
    diff = count_diff(before["counts"], after["counts"], allow_loss)
    failures = []
    if not migration_ok:
        failures.append("migration failed")
    for phase, checks in (("before", before), ("after", after)):
        if checks["integrity_errors"]:
            failures.append(f"{phase} integrity_check failed")
    if after["foreign_keys"] > before["foreign_keys"]:
        failures.append("foreign_key_check grew")
    if diff["lost"]:
        failures.append("row loss: " + ", ".join(diff["lost"]))
    return {**diff, "failures": failures}


def report(result):
    # Dropped tables come first so deliberate removals still get a person's review.
    lines = [
        "Dropped tables: " + ", ".join(result.get("dropped", []))
        if result.get("dropped")
        else "Dropped tables: none"
        if result.get("after")
        else "Dropped tables: not checked"
    ]
    lines.append(
        "New tables: " + (", ".join(result.get("new", [])) or "none")
        if result.get("after")
        else "New tables: not checked"
    )
    for name, counts in result.get("changed", {}).items():
        lines.append(f"Rows {name}: {counts['before']} -> {counts['after']}")
    if "disk" in result:
        disk = result["disk"]
        lines.append(
            f"Disk: {'ok' if disk['ok'] else 'FAIL'}; database {disk['database_bytes']} bytes, "
            f"free {disk['free_bytes']}, required {disk['required_bytes']}"
        )
    for phase in ("before", "after"):
        checks = result.get(phase)
        if checks:
            lines.append(
                f"{phase.title()}: integrity {'ok' if not checks['integrity_errors'] else 'FAIL'}, "
                f"integrity errors {checks['integrity_errors']}, foreign keys {checks['foreign_keys']}; "
                f"{len(checks['counts'])} tables, {sum(checks['counts'].values())} rows"
            )
    seconds = result.get("migration_seconds")
    lines.append(f"Migration: {seconds:.3f}s" if seconds is not None else "Migration: not run")
    if "warnings" in result:
        lines.append(f"Migration warnings: {result['warnings']}")
    if "network" in result:
        lines.append("network: " + result["network"])
    lines.append("Result: " + ("PASS" if result["ok"] else "FAIL: " + "; ".join(result["failures"])))
    # Per-table counts stay out of the summary line; changed tables are already in "changed".
    summary = {key: value for key, value in result.items() if key not in ("before", "after")}
    for phase in ("before", "after"):
        if result.get(phase):
            checks = result[phase]
            summary[phase] = {
                "integrity_errors": checks["integrity_errors"],
                "foreign_keys": checks["foreign_keys"],
                "tables": len(checks["counts"]),
                "rows": sum(checks["counts"].values()),
            }
    lines.append("REPLAY " + json.dumps(summary, sort_keys=True))
    return "\n".join(lines)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True, help="running company server container on this cell")
    parser.add_argument("--image", required=True, help="already pulled candidate server image, preferably by digest")
    parser.add_argument(
        "--keep-free", type=int, default=10 * GIB, metavar="BYTES", help="bytes to leave free (default: 10 GiB)"
    )
    parser.add_argument(
        "--code",
        type=Path,
        metavar="DIR",
        help="a checkout whose backend/ replaces the image's, read-only: replays migrations not yet in an image",
    )
    parser.add_argument(
        "--allow-loss", action="append", default=[], metavar="TABLE", help="allow fewer rows in this table; repeatable"
    )
    parser.add_argument("--allow-warnings", action="store_true", help="allow migration warnings")
    args = parser.parse_args(argv)
    if args.keep_free < 0:
        parser.error("--keep-free must be nonnegative")
    if any(not value.strip() or value.startswith("-") for value in (args.container, args.image)):
        parser.error("--container and --image must be nonempty names, not options")
    if any(not name.strip() for name in args.allow_loss):
        parser.error("--allow-loss must name a table")
    if args.code is not None:
        args.code = args.code.resolve()
        if not (args.code / "backend" / "migrations").is_dir():
            parser.error("--code must be a checkout with backend/migrations")
    return args


def code_mount(image, code):
    """The `-v` arguments that put a checkout's backend/ over the image's, at the image's working directory."""
    if code is None:
        return []
    workdir = docker("image", "inspect", "--format", "{{.Config.WorkingDir}}", image).decode().strip()
    if not workdir.startswith("/"):
        raise ReplayError("the image has no absolute working directory")
    return ["-v", f"{code / 'backend'}:{workdir}/backend:ro"]


def replay(args):
    result = {"failures": [], "migration_seconds": None, "migration_ok": None}
    phase = "live database discovery"
    try:
        database, size, env, source = live_database(args.container)
        base = tempfile.gettempdir()
        if Path(base).resolve().is_relative_to(source.resolve()):
            raise SourceError("host scratch directory must be outside the /data mount")
        result["disk"] = disk_check(size, shutil.disk_usage(base).free, args.keep_free)
        if not result["disk"]["ok"]:
            raise ReplayError("insufficient disk space")
        phase = "snapshot"
        mounts = code_mount(args.image, args.code)
        with tempfile.TemporaryDirectory(prefix="tico-replay-", dir=base) as scratch:
            scratch = Path(scratch)
            phase = "replay settings"
            env_file, key_mounts = replay_settings(database, env, scratch)
            kms = bool(env.get("TICO_CREDENTIAL_KMS_KEY", "").strip())
            result["network"] = "on (KMS)" if kms else "none"
            name = "tico-replay-" + uuid.uuid4().hex

            def run(code, stdout=None, stderr=subprocess.DEVNULL):
                return docker(
                    "run",
                    "--rm",
                    "--name",
                    name,
                    "--pull",
                    "never",
                    "--network",
                    "bridge" if kms else "none",
                    "--log-driver",
                    "none",
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "--env-file",
                    str(env_file),
                    "-v",
                    f"{scratch}:/replay:rw",
                    *key_mounts,
                    *mounts,
                    args.image,
                    "python3",
                    "-c",
                    code,
                    stdout=stdout,
                    stderr=stderr,
                )

            try:
                phase = "snapshot"
                snapshot(database, Path(scratch) / "hub.sqlite")
                phase = "before checks"
                result["before"] = json.loads(run(INSPECT))
                phase = "migration"
                started = time.monotonic()
                migration_ok = True
                try:
                    diagnostics = scratch / "migration.stderr"
                    with os.fdopen(os.open(diagnostics, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stderr:
                        run(MIGRATE, stdout=subprocess.DEVNULL, stderr=stderr)
                except ReplayError:
                    migration_ok = False
                finally:
                    result["migration_seconds"] = round(time.monotonic() - started, 3)
                    result["warnings"] = warning_count(diagnostics)
                result["migration_ok"] = migration_ok
                if not migration_ok:
                    result["failures"].append("migration failed")
                phase = "after checks"
                result["after"] = json.loads(run(INSPECT))
                result.update(verdict(result["before"], result["after"], migration_ok, args.allow_loss))
            finally:
                # Docker CLI interruption does not guarantee the container stopped.
                # Remove our named container before releasing its private bind mount.
                try:
                    try:
                        docker("rm", "--force", name)
                    except (ReplayError, OSError):
                        pass  # --rm normally already removed it; verify independently.
                finally:
                    try:
                        if docker("ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.ID}}").strip():
                            result["failures"].append(f"replay container still present: {name}")
                    except (ReplayError, OSError):
                        result["failures"].append("replay container cleanup verification failed")
    except KeyboardInterrupt:
        result["failures"].append("interrupted")
    except SourceError as error:
        result["failures"].append(str(error))
    except (ReplayError, OSError, sqlite3.Error, ValueError, KeyError):
        result["failures"].append(f"{phase} failed")
        if "disk" in result and not result["disk"]["ok"]:
            result["failures"][-1] = "insufficient disk space"
    if result.get("warnings", 0) and not args.allow_warnings:
        result["failures"].append("migration warnings")
    result["ok"] = not result["failures"]
    return result


def main(argv=None):
    result = replay(arguments(argv))
    print(report(result))
    return int(not result["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
