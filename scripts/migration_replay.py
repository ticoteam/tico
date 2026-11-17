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


def docker(*args, stdout=None):
    """The only Docker boundary. Never relay Docker or migration diagnostics."""
    result = subprocess.run(
        ["docker", *args], stdout=subprocess.PIPE if stdout is None else stdout, stderr=subprocess.DEVNULL, check=False
    )
    if result.returncode:
        raise ReplayError("Docker step failed")
    return result.stdout


def live_database(container):
    mounts = json.loads(docker("inspect", "--format", "{{json .Mounts}}", container))
    source = next((mount.get("Source") for mount in mounts if mount.get("Destination") == "/data"), None)
    if not source or not Path(source).is_absolute():
        raise SourceError("container /data mount has no absolute host Source")
    database = Path(source) / "hub.sqlite"
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
    return database, size


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
    return {**diff, "failures": failures, "ok": not failures}


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
                f"rows {json.dumps(checks['counts'], sort_keys=True)}"
            )
    seconds = result.get("migration_seconds")
    lines.append(f"Migration: {seconds:.3f}s" if seconds is not None else "Migration: not run")
    lines.append("Result: " + ("PASS" if result["ok"] else "FAIL: " + "; ".join(result["failures"])))
    lines.append("REPLAY " + json.dumps(result, sort_keys=True))
    return "\n".join(lines)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True, help="running company server container on this cell")
    parser.add_argument("--image", required=True, help="already pulled candidate server image, preferably by digest")
    parser.add_argument(
        "--keep-free", type=int, default=10 * GIB, metavar="BYTES", help="bytes to leave free (default: 10 GiB)"
    )
    parser.add_argument(
        "--allow-loss", action="append", default=[], metavar="TABLE", help="allow fewer rows in this table; repeatable"
    )
    args = parser.parse_args(argv)
    if args.keep_free < 0:
        parser.error("--keep-free must be nonnegative")
    if any(not value.strip() or value.startswith("-") for value in (args.container, args.image)):
        parser.error("--container and --image must be nonempty names, not options")
    if any(not name.strip() for name in args.allow_loss):
        parser.error("--allow-loss must name a table")
    return args


def replay(args):
    result = {"ok": False, "failures": [], "migration_seconds": None, "migration_ok": None}
    phase = "live database discovery"
    try:
        database, size = live_database(args.container)
        base = tempfile.gettempdir()
        if Path(base).resolve().is_relative_to(database.parent.resolve()):
            raise SourceError("host scratch directory must be outside the /data mount")
        result["disk"] = disk_check(size, shutil.disk_usage(base).free, args.keep_free)
        if not result["disk"]["ok"]:
            raise ReplayError("insufficient disk space")
        phase = "snapshot"
        with tempfile.TemporaryDirectory(prefix="tico-replay-", dir=base) as scratch:
            name = "tico-replay-" + uuid.uuid4().hex

            def run(code, stdout=None):
                return docker(
                    "run",
                    "--rm",
                    "--name",
                    name,
                    "--pull",
                    "never",
                    "--network",
                    "none",
                    "--log-driver",
                    "none",
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "-v",
                    f"{scratch}:/replay:rw",
                    args.image,
                    "python3",
                    "-c",
                    code,
                    stdout=stdout,
                )

            try:
                snapshot(database, Path(scratch) / "hub.sqlite")
                phase = "before checks"
                result["before"] = json.loads(run(INSPECT))
                phase = "migration"
                started = time.monotonic()
                migration_ok = True
                try:
                    run(MIGRATE, stdout=subprocess.DEVNULL)
                except ReplayError:
                    migration_ok = False
                finally:
                    result["migration_seconds"] = round(time.monotonic() - started, 3)
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
                    docker("rm", "--force", name)
                except ReplayError:
                    pass  # --rm normally already removed it.
    except KeyboardInterrupt:
        result["failures"].append("interrupted")
    except SourceError as error:
        result["failures"].append(str(error))
    except (ReplayError, OSError, sqlite3.Error, ValueError, KeyError):
        result["failures"].append(f"{phase} failed")
        if "disk" in result and not result["disk"]["ok"]:
            result["failures"][-1] = "insufficient disk space"
    return result


def main(argv=None):
    result = replay(arguments(argv))
    print(report(result))
    return int(not result["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
