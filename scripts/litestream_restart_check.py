#!/usr/bin/env python3
"""Do restarts with no data changes add a copy of the whole database to a Litestream replica?

Builds a synthetic install in WORKDIR (new or empty), lets docker/entrypoint.sh `prepare` write the Litestream config,
then runs the production order for each restart: prepare, Litestream starts, the app's start-up runs beside it, the app
stops, and Litestream stops (its last sync). It prints what each restart uploads from the database (L0 files: the
changed pages) and what compaction re-copies at the higher levels, as shares of the database. Exits 1 when a restart
uploads 5% of the database or more. Compaction is not gated: it re-copies data already uploaded, so a snapshot made
earlier is copied again at each level, with or without a restart.

    python3 scripts/litestream_restart_check.py --litestream /path/to/litestream WORKDIR

The binary must be Litestream 0.5.17 (docker/versions.env). Nothing reads or writes outside WORKDIR.
"""
import argparse
import glob
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
LIFESPAN = """
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.config import Settings
with TestClient(create_app(Settings.from_env())):
    pass
"""
THRESHOLD = 0.05

sys.path.insert(0, str(ROOT / "scripts"))
import page_churn  # noqa: E402


def environment(work, litestream):
    # Built from nothing but what the install needs: no credentials from the caller's environment reach the children.
    bin_dir = work / "bin"
    bin_dir.mkdir(exist_ok=True)
    link = bin_dir / "litestream"
    if not link.exists():
        link.symlink_to(Path(litestream).resolve())
    data = work / "data"
    return {"PATH": f"{bin_dir}:{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
            "HOME": os.environ.get("HOME", ""), "PYTHONPATH": str(ROOT),
            "TICO_DATA_DIR": str(data), "TICO_DB": str(data / "hub.sqlite"), "TICO_REGISTRY_DIR": str(data / "registry"),
            "TICO_BLOB_DIR": str(data / "blobs"), "TICO_BACKUP_DIR": str(work / "backups"),
            "TICO_LITESTREAM_CONFIG": str(work / "litestream.yml"),
            "TICO_TEAM_NAME": "Acme", "TICO_OWNER_EMAIL": "owner@example.com", "TICO_APP_NAME": "Acme",
            "TICO_ASSISTANT_NAME": "Assistant", "TICO_ENVIRONMENT_ID": "litestream-check",
            "TICO_PUBLIC_URL": "http://127.0.0.1:8765",
            "TICO_UPDATE_CHECK": "off", "TICO_TELEMETRY": "off", "TICO_SUPPORT": "off"}


def prepare(env):
    result = subprocess.run(["bash", str(ENTRYPOINT), "prepare"], env=env, cwd=ROOT, capture_output=True, text=True,
                            timeout=300, check=False)
    if result.returncode:
        sys.exit(f"prepare failed: {result.stderr[-1000:]}")


def replica_files(work):
    return {os.path.relpath(p, work / "backups"): os.path.getsize(p)
            for p in glob.glob(str(work / "backups" / "ltx" / "*" / "*.ltx"))}


def start_litestream(work, env):
    log = open(work / "litestream.log", "ab")
    return subprocess.Popen([str(work / "bin" / "litestream"), "replicate", "-config", env["TICO_LITESTREAM_CONFIG"]],
                            env=env, stdout=log, stderr=log)


def stop_litestream(process):
    process.send_signal(signal.SIGTERM)
    process.wait(timeout=120)


def app_start(env):
    result = subprocess.run([sys.executable, "-c", LIFESPAN], cwd=ROOT, env=env, capture_output=True, text=True,
                            timeout=600, check=False)
    if result.returncode:
        sys.exit(f"the app's start-up failed: {result.stderr[-1000:]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--litestream", required=True, help="the Litestream 0.5.17 binary")
    parser.add_argument("--restarts", type=int, default=2)
    args = parser.parse_args()
    work = args.workdir.resolve()
    if work.exists() and any(work.iterdir()):
        sys.exit(f"{work} is not empty; give a new directory")
    work.mkdir(parents=True, exist_ok=True)
    env = environment(work, args.litestream)
    (work / "backups").mkdir(exist_ok=True)
    (work / "data").mkdir(exist_ok=True)

    prepare(env)  # creates the database and the Litestream config
    data = work / "data"
    page_churn.fill_synthetic(data / "hub.sqlite")
    size = os.path.getsize(data / "hub.sqlite")
    print(f"database: {size / 1e6:.1f} MB ({size // 4096} pages)", flush=True)

    # First attach: Litestream uploads a full snapshot. Wait for it, then stop as a clean stop does.
    ls = start_litestream(work, env)
    try:
        deadline = time.monotonic() + 120
        while not replica_files(work) and time.monotonic() < deadline:
            time.sleep(0.5)
        time.sleep(3)
    finally:
        stop_litestream(ls)
    print(f"first attach: replica holds {sum(replica_files(work).values()) / 1e6:.1f} MB", flush=True)

    worst = 0.0
    for number in range(1, args.restarts + 1):
        before = replica_files(work)
        prepare(env)
        ls = start_litestream(work, env)
        try:
            time.sleep(3)
            app_start(env)
            time.sleep(3)
        finally:
            stop_litestream(ls)
        added = {k: v for k, v in replica_files(work).items() if k not in before}
        uploaded = sum(v for k, v in added.items() if k.startswith("ltx/0/"))
        compacted = sum(added.values()) - uploaded
        worst = max(worst, uploaded / size)
        print(f"restart {number}: uploads {uploaded / 1e6:.2f} MB ({uploaded / size:.1%} of the database); "
              f"compaction re-copies {compacted / 1e6:.2f} MB", flush=True)
        for name, found in sorted(added.items()):
            print(f"    {found / 1e6:9.3f} MB  {name}", flush=True)
    if worst >= THRESHOLD:
        print(f"FAIL: a restart uploaded {worst:.1%} of the database (limit {THRESHOLD:.0%})")
        return 1
    print(f"OK: every restart uploaded under {THRESHOLD:.0%} of the database")
    return 0


if __name__ == "__main__":
    sys.exit(main())
