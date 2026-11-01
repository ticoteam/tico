"""`python3 -m setup.cell_stats`: the cell's numbers and debug tools, run against a fake host (no Docker, no root)."""
import gzip
import io
import json
import os
import sqlite3
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import pytest

from backend import flight_file
from setup import cell, cell_stats as cs
from setup.cell import Result

ROOT = Path(__file__).resolve().parents[2]
NOW = 1_793_361_600.0                      # 2026-10-30T12:00:00Z
KEY = "tch_" + "K" * 43
STAT_A = "cpu  1000 0 500 8000 100 0 0 0 0 0\ncpu0 500 0 250 4000 50 0 0 0 0 0\ncpu1 500 0 250 4000 50 0 0 0 0 0\n"
STAT_B = "cpu  1100 0 550 8300 140 0 0 10 0 0\ncpu0 550 0 275 4150 70 0 0 5 0 0\ncpu1 550 0 275 4150 70 0 0 5 0 0\n"
Vfs = namedtuple("Vfs", "f_blocks f_frsize f_bavail f_files f_favail")


def server_id(slug):
    return f"{slug:a<8}".replace("-", "0")[:8].encode().hex() * 4


class FakeBox(cs.Box):
    """A host under tmp_path: /proc files, a cell with companies, Docker answers from tables, HQ answers queued."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path / "host", tmp_path / "host" / "srv" / "tico-cell")
        self.calls, self.posts, self.kills, self.answers = [], [], [], []
        self.t, self.stats_reads = 0.0, 0
        self.health, self.ready = {}, (200, 2, b'{"readyConnections": 4, "connectorId": "x"}')
        self.containers = {}               # name -> (ps row, inspect row, stats row)
        self.tick = 0.05                   # monotonic seconds each call costs
        proc = self.path("proc")
        (proc / "sys" / "kernel").mkdir(parents=True)
        (proc / "loadavg").write_text("0.40 0.50 0.60 1/300 4242\n")
        (proc / "meminfo").write_text("MemTotal:       15974400 kB\nMemFree: 1000 kB\nMemAvailable:    9318400 kB\n"
                                      "SwapTotal: 0 kB\nSwapFree: 0 kB\n")
        (proc / "uptime").write_text("123456.78 2000.00\n")
        (proc / "vmstat").write_text("nr_free_pages 1\noom_kill 3\n")
        (proc / "sys" / "kernel" / "osrelease").write_text("6.8.0-1015-aws\n")
        self.path("srv/tico-cell").mkdir(parents=True)
        (self.cell_root / "cell.json").write_text(json.dumps({"domain": "tico.team"}))

    # companies and containers
    def add_company(self, slug, state="running", health="healthy", version="v0.5.15", memory="4g", updater=False):
        d = self.cell_root / "companies" / slug
        (d / "data" / "flight").mkdir(parents=True)
        (d / "company.json").write_text(json.dumps({"slug": slug, "domain": f"{slug}.tico.team", "port": 20000,
                                                    "index": 0, "version": version, "memory": memory,
                                                    "state": "running", "aliases": []}))
        roles = ["server"] + (["updater"] if updater else [])
        for i, role in enumerate(roles):
            name = f"tico-{slug}-{role}-1"
            cid = (server_id(slug)[:-1] + str(i))
            labels = f"com.docker.compose.project=tico-{slug},com.docker.compose.service={role}" + \
                (f",{cell.LABEL}={slug}" if role == "server" else "")
            status = f"Up 4 hours ({health})" if state == "running" else "Exited (1) 2 minutes ago"
            log = f"/var/lib/docker/containers/{cid}/{cid}-json.log"
            self.containers[name] = (
                {"ID": cid, "Names": name, "Labels": labels, "State": state, "Status": status},
                {"id": cid, "name": "/" + name, "restarts": 2, "memory": 4 * 2 ** 30, "log": log,
                 "labels": dict(p.split("=", 1) for p in labels.split(",")),
                 "state": {"Status": state, "Pid": 4100 + i if state == "running" else 0, "OOMKilled": False,
                           "StartedAt": "2026-10-30T08:08:00.123456789Z",
                           "Health": {"Status": health, "Log": [{"Output": "secret probe output"}]}}},
                {"Name": name, "ID": cid, "CPUPerc": "3.10%", "MemUsage": "612MiB / 4GiB", "NetIO": "120.1MB / 88MB",
                 "BlockIO": "10MB / 900MB", "PIDs": "41"})
        return d / "data"

    def add_edge(self, tunnel_state="running"):
        self.containers["tico-cell-router"] = ({"ID": "r" * 64, "Names": "tico-cell-router", "State": "running",
                                                "Labels": "com.docker.compose.project=tico-cell", "Status": "Up"}, None, None)
        self.containers["tico-cell-tunnel"] = ({"ID": "t" * 64, "Names": "tico-cell-tunnel", "State": tunnel_state,
                                                "Labels": "com.docker.compose.project=tico-cell", "Status": "Up"}, None, None)

    # the box
    def read(self, p, limit=1 << 20):
        if str(p).lstrip("/") == "proc/stat":
            self.stats_reads += 1
            return STAT_A if self.stats_reads % 2 else STAT_B
        return super().read(p, limit)

    def run(self, argv, timeout=20, cwd=None):
        self.calls.append(list(argv))
        self.t += self.tick
        if argv[:2] == ["docker", "ps"]:
            return Result(0, "".join(json.dumps(ps) + "\n" for ps, _, _ in self.containers.values()))
        if argv[:2] == ["docker", "inspect"]:
            fmt, ids = argv[3], argv[4:]
            assert "Env" not in fmt
            rows = [i for _, i, _ in self.containers.values() if i and i["id"] in ids]
            if "mounts" in fmt:
                rows = [{**i, "image": "ghcr.io/ticoteam/tico:v0.5.15", "host_config": {"memory": i["memory"]},
                         "mounts": [{"Type": "bind", "Source": "/srv/tico-cell/companies/x/data", "Destination": "/data",
                                     "RW": True},
                                    {"Type": "bind", "Source": "/root/secret", "Destination": "/x", "RW": False}],
                         "labels": {**i["labels"], "com.example.other": "x"}} for i in rows]
            return Result(0, "".join(json.dumps(r) + "\n" for r in rows))
        if argv[:2] == ["docker", "stats"]:
            return Result(0, "".join(json.dumps(s) + "\n" for _, _, s in self.containers.values() if s))
        if argv[:3] == ["docker", "system", "df"]:
            return Result(0, '{"Type":"Images","Size":"40GB"}\n{"Type":"Local Volumes","Size":"1.2GB"}\n'
                             '{"Type":"Build Cache","Size":"0B"}\n')
        if argv[:2] == ["docker", "info"]:
            return Result(0, "/var/lib/docker\n")
        if argv[:2] == ["docker", "logs"]:
            return Result(0, "2026-10-30T11:59:00Z hello\n", "2026-10-30T11:59:01Z ERROR x token=abc123\n")
        if argv[:2] == ["docker", "exec"]:
            return self.exec(argv[2:])
        if argv[0] == "journalctl":
            return Result(0, "Oct 30 docker[1]: started with Authorization: Bearer sk-abcdefghijklmnop\n")
        return Result(0, f"{argv[0]} output\n")

    def exec(self, argv):
        """`docker exec NAME python -I -c SCRIPT …`: the script really runs (this python, its own process) against the
        company's database, which is at /data/hub.sqlite in its container."""
        name, python, *rest = argv
        ps = self.containers.get(name, ({},))[0]
        if ps.get("State") != "running":
            return Result(1, "", f"Error response from daemon: container {name} is not running")
        assert python == cs.EXEC_PYTHON and rest[:2] == ["-I", "-c"]
        slug = name.removeprefix("tico-").rsplit("-", 2)[0]
        args = [str(self.cell_root / "companies" / slug / "data" / "hub.sqlite") if a == cs.CONTAINER_DB else a
                for a in rest[3:]]
        p = subprocess.run([sys.executable, "-I", "-c", rest[2], *args], capture_output=True, text=True, check=False)
        return Result(p.returncode, p.stdout, p.stderr)

    def http_get(self, host, path, port=cell.ROUTER_PORT, timeout=3):
        if port == cs.TUNNEL_METRICS:
            return self.ready
        if host == cell.ROUTER_HEALTH_HOST:
            return 200, 1, b"ok"
        code = self.health.get(host, 200)
        return (code, 12, b"") if code else (None, None, b"")

    def post(self, url, body, headers, timeout=15):
        self.posts.append((url, body, headers))
        if "/stats" in url and self.answers:
            return self.answers.pop(0)
        return 200, {}, b'{"ok": true}'

    def statvfs(self, p):
        return Vfs(f_blocks=193_000_000, f_frsize=1000, f_bavail=120_400_000, f_files=1000, f_favail=920)

    def kill(self, pid, sig):
        self.kills.append((pid, sig))
        if self.on_kill:
            self.on_kill(pid, sig)

    on_kill = None

    def euid(self):
        return 0

    def time(self):
        return NOW

    def monotonic(self):
        self.t += 0.001
        return self.t

    def sleep(self, s):
        self.t += s


def write_flight(folder, records):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / flight_file.NAME, "a") as f:
        for r in records:
            f.write(json.dumps({"v": 1, **r}) + "\n")


def busy_minute(ts):
    return [{"ts": ts, "kind": "minute", "cpu": 3.0, "rss": 600 * 2 ** 20, "threads": 30, "lag_max": 40,
             "requests": 240, "sql_ms": 180, "wait_max": 20, "wait_ms": 8, "hold_max": 300, "locked": 0},
            {"ts": ts, "kind": "routes", "span": 60, "routes": [
                {"route": "GET /api/v2/tasks", "caller": "human", "n": 200, "errors": 0, "total_ms": 1600,
                 "p50": 8, "p95": 90, "max_ms": 400, "sql_ms": 100, "wait_ms": 0, "bytes": 0, "bytes_max": 0},
                {"route": "GET /api/v2/slow", "caller": "bot", "n": 4, "errors": 1, "total_ms": 6000,
                 "p50": 1500, "p95": 2100, "max_ms": 2500, "sql_ms": 50, "wait_ms": 8, "bytes": 0, "bytes_max": 0}]}]


def make_db(data, freelist=False):
    c = sqlite3.connect(data / "hub.sqlite")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE flight_sql(created_at INTEGER NOT NULL, fingerprint TEXT NOT NULL, n INTEGER NOT NULL, "
              "total_ms REAL NOT NULL, p95 REAL, max_ms REAL, hist TEXT, PRIMARY KEY(created_at, fingerprint)) WITHOUT ROWID")
    hour = int(NOW) // 3600 * 3600
    c.executemany("INSERT INTO flight_sql VALUES(?,?,?,?,?,?,?)", [
        (hour, "SELECT id FROM tasks WHERE status = '?' AND n > N", 300, 400.0, 3.0, 50.0, "{}"),
        (hour - 3600, "SELECT id FROM tasks WHERE status = '?' AND n > N", 100, 100.0, 3.0, 20.0, "{}"),
        (hour, "UPDATE jobs SET x = ? WHERE id = ?", 10, 900.0, 3.0, 200.0, "{}"),
        (hour - 7 * 3600, "SELECT old FROM gone", 10, 9999.0, 3.0, 200.0, "{}")])
    c.execute("CREATE TABLE junk(x)")
    c.executemany("INSERT INTO junk VALUES(?)", [("x" * 1000,)] * 50)
    c.commit()
    if freelist:
        c.execute("DELETE FROM junk")
        c.commit()
    c.close()


@pytest.fixture
def box(tmp_path):
    b = FakeBox(tmp_path)
    b.add_edge()
    data = b.add_company("boatyard", updater=True)
    make_db(data, freelist=True)
    (data / "blobs" / "ab").mkdir(parents=True)
    (data / "blobs" / "ab" / "file").write_bytes(b"x" * 1_500_000)
    write_flight(data / "flight", busy_minute(NOW - 120) + [
        {"ts": NOW - 100, "kind": "slow", "route": "GET /api/v2/slow", "ms": 2500},
        {"ts": NOW - 90, "kind": "stall", "id": 1, "seconds": 2.5, "class": "lock",
         "threads": {"busy": [{"thread": "MainThread", "loop": True, "stack": ["a.py:1 f"]}], "idle": 3}},
        {"ts": NOW - 85, "kind": "stall_end", "id": 1, "seconds": 3.1, "class": "lock"}])
    proc_root = b.path("proc/4100/root/tmp")
    proc_root.mkdir(parents=True)
    (proc_root / "tico-backup-status.json").write_text(json.dumps({"last_replicated_at": "2026-10-30T11:59:51Z",
                                                                   "credential_key": {"size": 32}}))
    ps, ins, _ = b.containers["tico-boatyard-server-1"]
    log = b.path(ins["log"])
    log.parent.mkdir(parents=True)
    log.write_text("".join(json.dumps(r) + "\n" for r in [
        {"log": "ERROR tico.old: too old\n", "stream": "stderr", "time": "2026-10-30T11:50:00.0Z"},
        {"log": "INFO fine\n", "stream": "stdout", "time": "2026-10-30T11:59:40.0Z"},
        {"log": "ERROR tico.runner: login failed for ann@example.com with Bearer abc.def.ghi\n", "stream": "stderr",
         "time": "2026-10-30T11:59:40.5Z"},
        {"log": "Traceback (most recent call last):\n", "stream": "stderr", "time": "2026-10-30T11:59:41.0Z"}]))
    _, upd, _ = b.containers["tico-boatyard-updater-1"]
    b.path(upd["log"]).parent.mkdir(parents=True)
    b.path(upd["log"]).write_text(json.dumps({"log": "updater idle\n", "time": "2026-10-30T11:59:50Z"}) + "\n")
    b.add_company("stuck", state="exited", health="", memory="2g")
    b.health["stuck.tico.team"] = 502
    return b


# --- the snapshot -----------------------------------------------------------------------------------------------------

def test_a_snapshot_from_proc_docker_and_the_volume(box):
    snap = cs.collect(box, {}, host_id="cell-1")
    assert snap["format"] == 1 and snap["host_id"] == "cell-1" and snap["at"] == "2026-10-30T12:00:00Z"
    h = snap["host"]
    # 100+50+300+40+10 jiffies; busy = user+system+steal = 160; iowait 40; steal 10
    assert (h["cpu_pct"], h["iowait_pct"], h["steal_pct"]) == (32.0, 8.0, 2.0)
    assert h["cpus"] == 2 and h["load"] == [0.4, 0.5, 0.6]
    assert (h["mem_total_mb"], h["mem_avail_mb"], h["swap_used_mb"]) == (15600, 9100, 0)
    assert (h["disk_total_gb"], h["disk_free_gb"], h["inodes_free_pct"]) == (193.0, 120.4, 92.0)
    assert h["docker_disk_gb"] == 41.2 and h["uptime_s"] == 123456 and h["kernel"] == "6.8.0-1015-aws"
    assert h["oom_kills_1h"] == 0
    assert snap["router"] == {"up": True, "ms": 1}
    assert snap["tunnel"] == {"up": True, "detail": "cloudflared ready, 4 connections"}

    by = {c["slug"]: c for c in snap["companies"]}
    b = by["boatyard"]
    assert b["state"] == "running" and b["version"] == "v0.5.15" and b["memory_limit_mb"] == 4096
    assert b["healthz"] == {"code": 200, "ms": 12}
    server = next(c for c in b["containers"] if c["role"] == "server")
    assert server == {"role": "server", "name": "tico-boatyard-server-1", "state": "running", "health": "healthy",
                      "restarts": 2, "oom_killed": False, "started_at": "2026-10-30T08:08:00Z", "cpu_pct": 3.1,
                      "mem_mb": 612, "mem_limit_mb": 4096, "pids": 41, "net_rx_mb": 120.1, "net_tx_mb": 88.0,
                      "blk_read_mb": 10.0, "blk_write_mb": 900.0}
    assert {c["role"] for c in b["containers"]} == {"server", "updater"}
    assert b["db"]["size_mb"] > 0 and b["db"]["wal_mb"] is not None and b["db"]["freelist_pct"] > 0
    assert b["db"]["files_mb"] == 1.5
    assert b["backup"] == {"age_s": 9, "behind_s": None, "checked_s": None, "stale": False, "unsent_files": None,
                           "detail": "litestream"}
    f = b["flight"]
    assert f["window_s"] == 300
    assert f["requests"]["count"] == 204 and f["requests"]["errors_5xx"] == 1
    assert f["requests"]["p50_ms"] < 10 and 80 < f["requests"]["p95_ms"] < 400
    assert f["slow_routes"] == [{"route": "GET /api/v2/slow", "count": 4, "p95_ms": 2100, "max_ms": 2500,
                                 "total_ms": 6000}]
    assert f["sql"]["total_ms"] == 180 and f["sql"]["lock_wait_ms"] == 8 and f["sql"]["lock_hold_ms_max"] == 300
    assert f["sql"]["locked_errors"] == 0
    assert [t["shape"] for t in f["sql"]["top"]] == ["UPDATE jobs SET x = ? WHERE id = ?",
                                                    "SELECT id FROM tasks WHERE status = '?' AND n > N"]
    assert f["sql"]["top"][1]["count"] == 400        # this hour and the last, not seven hours ago
    assert f["process"] == {"cpu_pct": 3.0, "rss_mb": 600, "loop_lag_ms_max": 40, "threads": 30}
    assert f["stalls"]["count"] == 1 and f["stalls"]["last"]["class"] == "lock"
    assert [e["line"] for e in b["errors"]] == ["ERROR tico.runner: login failed for [redacted] with Bearer [redacted]",
                                                "Traceback (most recent call last):"]

    s = by["stuck"]
    assert s["state"] == "stopped" and s["healthz"]["code"] == 502 and s["backup"] is None
    assert s["flight"]["requests"]["count"] == 0
    text = json.dumps(snap)
    assert "secret probe output" not in text and "ann@example.com" not in text


def test_a_backup_behind_the_database_past_its_warning_time_is_stale_and_its_pile_up_is_counted(box):
    root = box.path("proc/4300/root/tmp")
    root.mkdir(parents=True)
    status = {"last_replicated_at": "2026-10-30T11:59:51Z", "warn_after_s": 600, "unsent_files": 27914,
              "behind_since": "2026-10-30T11:45:00Z"}
    (root / "tico-backup-status.json").write_text(json.dumps(status))
    assert cs.backup_block(box, 4300, NOW) == {"age_s": 9, "behind_s": 900, "checked_s": None, "stale": True,
                                               "unsent_files": 27914, "detail": "litestream"}
    (root / "tico-backup-status.json").write_text(json.dumps({**status, "behind_since": "2026-10-30T11:55:00Z"}))
    assert cs.backup_block(box, 4300, NOW)["stale"] is False and cs.backup_block(box, 4300, NOW)["behind_s"] == 300
    (root / "tico-backup-status.json").write_text(json.dumps({**status, "behind_since": None, "unsent_files": None}))
    assert cs.backup_block(box, 4300, NOW) == {"age_s": 9, "behind_s": None, "checked_s": None, "stale": False,
                                               "unsent_files": None, "detail": "litestream"}
    # A status the loop has not checked within the warning time is stale too: the copy state is then unknown.
    (root / "tico-backup-status.json").write_text(json.dumps({**status, "behind_since": None, "checked_at": "2026-10-30T11:45:00Z"}))
    assert cs.backup_block(box, 4300, NOW) == {"age_s": 9, "behind_s": None, "checked_s": 900, "stale": True,
                                               "unsent_files": 27914, "detail": "litestream"}


def test_docker_runs_once_for_every_company_and_never_reads_the_environment(box):
    for slug in ("a1", "b2", "c3"):
        box.add_company(slug)
    cs.collect(box, {})
    kinds = [tuple(c[:2]) for c in box.calls]
    assert kinds.count(("docker", "ps")) == 1
    assert kinds.count(("docker", "inspect")) == 1
    assert kinds.count(("docker", "stats")) == 1
    assert ("docker", "logs") not in kinds       # read from each container's json-file log
    inspect = next(c for c in box.calls if c[:2] == ["docker", "inspect"])
    assert len(inspect) - 4 == 6                 # every company's containers, one call; not the router or tunnel
    assert all("Env" not in " ".join(c) for c in box.calls)


def test_docker_logs_only_for_a_running_container_without_a_log_file(box):
    _, ins, _ = box.containers["tico-boatyard-server-1"]
    ins["log"] = ""
    snap = cs.collect(box, {})
    calls = [c for c in box.calls if c[:2] == ["docker", "logs"]]
    assert calls == [["docker", "logs", "--since", "70s", "--tail", "500", "--timestamps", "tico-boatyard-server-1"]]
    lines = [e["line"] for e in snap["companies"][0]["errors"]]
    assert lines == ["ERROR x token=[redacted]"]


def test_one_broken_company_does_not_cost_the_others(box, monkeypatch):
    real = cs.db_block

    def broken(box_, root, slug, *a):
        if slug == "stuck":
            raise RuntimeError("disk on fire at /srv/x?q=abc")
        return real(box_, root, slug, *a)
    monkeypatch.setattr(cs, "db_block", broken)
    by = {c["slug"]: c for c in cs.collect(box, {})["companies"]}
    assert by["boatyard"]["db"]["size_mb"] > 0
    assert by["stuck"]["error"] == "RuntimeError: disk on fire at /srv/x?[redacted]"


def test_slow_numbers_are_cached_for_15_minutes(box, monkeypatch):
    cache = {}
    opened = []
    real = cs.sql_top
    monkeypatch.setattr(cs, "sql_top", lambda box_, name, now: opened.append(name) or real(box_, name, now))
    cs.collect(box, cache)
    cs.collect(box, cache)
    assert len(opened) == 1                      # boatyard's server, once, not once per push; stuck's is not running
    assert sum(1 for c in box.calls if c[:3] == ["docker", "system", "df"]) == 1
    cache["df"]["at"] -= cs.SLOW_EVERY
    for v in cache["db"].values():
        v["at"] -= cs.SLOW_EVERY
    cs.collect(box, cache)
    assert len(opened) == 2 and sum(1 for c in box.calls if c[:3] == ["docker", "system", "df"]) == 2


def test_oom_kills_count_the_last_hour_from_the_kernel_counter(box):
    cache = {"oom": [[NOW - 3700, 1], [NOW - 3600, 1], [NOW - 600, 2]]}
    assert cs.oom_kills(box, cache) == 2
    assert cache["oom"][-1] == [NOW, 3] and all(NOW - t <= 3900 for t, _ in cache["oom"])


def test_a_missing_tunnel_and_a_down_router(box):
    del box.containers["tico-cell-tunnel"]
    box.http_get = lambda host, path, port=8080, timeout=3: (None, None, b"")
    snap = cs.collect(box, {})
    assert snap["tunnel"] == {"up": None, "detail": "no tunnel on this host"}
    assert snap["router"] == {"up": False, "ms": None}
    assert snap["companies"][0]["healthz"] == {"code": None, "ms": None}


def test_show_json_prints_one_company_without_sending(box):
    out = io.StringIO()
    assert cs.show(box, cs.parser().parse_args(["show", "--company", "boatyard", "--json"]), out=out) == 0
    got = json.loads(out.getvalue())
    assert got["slug"] == "boatyard" and box.posts == []
    out = io.StringIO()
    assert cs.show(box, cs.parser().parse_args(["show"]), out=out) == 0
    assert "boatyard" in out.getvalue() and "stuck" in out.getvalue()
    assert cs.show(box, cs.parser().parse_args(["show", "--company", "nope"]), out=io.StringIO()) == 1


# --- the size cap -----------------------------------------------------------------------------------------------------

def big_snapshot(companies=40, errors=20, routes=10):
    return {"format": 1, "trimmed": False, "host": {}, "companies": [
        {"slug": f"c{i}", "errors": [{"at": "x", "line": "E" * 300}] * errors,
         "flight": {"slow_routes": [{"route": "R" * 200}] * routes, "sql": {"top": [{"shape": "S" * 200}] * 10},
                    "requests": {"count": 1}}} for i in range(companies)]}


def test_a_snapshot_under_the_cap_is_sent_untouched():
    snap = big_snapshot(companies=2)
    body = cs.fit(snap)
    assert json.loads(body) == snap and snap["trimmed"] is False


def test_trimming_drops_errors_first_then_flight_detail():
    snap = big_snapshot()
    body = cs.fit(snap, cap=200_000)
    got = json.loads(body)
    assert len(body) <= 200_000 and got["trimmed"] is True
    assert all(len(c["errors"]) <= 3 for c in got["companies"])
    assert all(c["flight"]["slow_routes"] for c in got["companies"])     # errors alone were enough

    snap = big_snapshot()
    body = cs.fit(snap, cap=120_000)
    got = json.loads(body)
    assert len(body) <= 120_000 and all(c["errors"] == [] for c in got["companies"])
    assert all(c["flight"] and c["flight"]["requests"] for c in got["companies"])
    assert len(got["companies"]) == 40

    snap = big_snapshot()
    body = cs.fit(snap, cap=1_000)
    got = json.loads(body)
    assert len(body) <= 1_000 and got["companies_dropped"] > 0 and all(c["flight"] is None for c in got["companies"])


# --- the scrubber -----------------------------------------------------------------------------------------------------

SECRETS = [
    ("Authorization: Bearer " + "a" * 40, "a" * 40),
    ("curl -H 'authorization: token ghp_" + "A1b2" * 9 + "'", "ghp_"),
    ("bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.sig-nature_x", "eyJhbGci"),
    ("Cookie: tico_session=abc123def456; other=yes", "abc123def456"),
    ("set-cookie: sid=deadbeef; Path=/; HttpOnly", "deadbeef"),
    ("password=hunter2", "hunter2"),
    ("db passwd: 'correct horse'", "correct horse"),
    ('{"api_key": "abcd1234efgh"}', "abcd1234efgh"),
    ("TICO_OIDC_CLIENT_SECRET=s3cr3t-value", "s3cr3t-value"),
    ("x-api-key:  zzzz9999", "zzzz9999"),
    ("access_token=ya29.a0AfH6SMB", "ya29.a0AfH6SMB"),
    ("session=0123456789", "0123456789"),
    ("key " + KEY + " rotated", KEY[4:]),
    ("stats tcs_" + "Q" * 30, "Q" * 30),
    ("invite tgi_" + "w" * 24, "w" * 24),
    ("token tico_" + "AbC1" * 8, "AbC1AbC1"),
    ("openai sk-proj-" + "Ab1" * 15, "Ab1Ab1"),
    ("anthropic sk-ant-api03-" + "x9Y" * 20, "x9Yx9Y"),
    ("stripe sk_live_" + "51H" * 10, "51H51H"),
    ("oauth gho_" + "Z" * 36, "Z" * 36),
    ("pat github_pat_11ABCDEFG0_" + "q" * 40, "github_pat_"),
    ("slack xoxb-1234-5678-abcdefgh", "xoxb-"),
    ("slack xoxp-99999-11111-zzz", "xoxp-"),
    ("aws AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("sts ASIAY34FZKBOKMUTVV7A", "ASIAY34FZKBOKMUTVV7A"),
    ("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
    ("owner ann.lee+tico@example.co.uk signed in", "ann.lee"),
    ("GET https://hub.tico.team/auth/callback?code=4/0AbCd&state=xyz HTTP/1.1", "code=4"),
    ('"GET /api/v2/tasks?token=abc&limit=5 HTTP/1.1" 200', "token=abc"),
    ("GET /api/v2/tasks?q=ann&page=2", "q=ann"),
    ("redirect to https://accounts.example/x?abcdef", "abcdef"),
    ("sha " + "0123456789abcdef" * 4, "0123456789abcdef"),
    ("blob " + "Zm9vYmFyYmF6cXV4" + "QUJDREVGR0hJSktM" + "MTIzNDU2Nzg5MA==", "Zm9vYmFy"),
    ("cloudflared token eyJhIjoiYWJjIn0", "eyJhIjoiYWJjIn0"),
    ("LITESTREAM_SECRET_ACCESS_KEY=bk/" + "x" * 37, "x" * 37),
    ("tunnel " + "MjAyNi0xMC0zMFQxMjowMDowMFo" + "Ab3" * 4, "MjAyNi0x"),
    # JSON carried inside a string: the quotes come escaped once, or twice when that string was escaped again.
    (r'{\"token\": \"abc123def456\"}', "abc123def456"),
    (r'\"password\":\"hunter2x\"', "hunter2x"),
    (r'{\"password\": \"correct horse\"}', "horse"),
    (r'{\\\"api_key\\\": \\\"zz99yy88\\\"}', "zz99yy88"),
    (r'\\\"client_secret\\\":\\\"two words\\\"', "words"),
    (r'key=\"value123\"', "value123"),
    (r'secret=\\\"v4lue x\\\"', "v4lue"),
    (r"\'session\': \'s3ss10n x\'", "s3ss10n"),
    (r'{\"authorization\": \"Basic dXNlcjpwYXNz\"}', "dXNlcjpwYXNz"),
    (r'\\\"Cookie\\\": \\\"sid=feedface\\\"', "feedface"),
    (r'{"log": "{\"refresh_token\":\"rt abc 123\",\"ok\":1}"}', "rt abc"),
    # An escaped quote inside the value itself, at each depth: nothing after it survives either.
    (r'"password":"ab\"cdzq"', "cdzq"),
    (r"'secret': 'it\'s minezq'", "minezq"),
    (r'{\"password\":\"ab\\\"cdzq\"}', "cdzq"),
    (r'{\\\"password\\\":\\\"ab\\\\\\\"cdzq\\\\\\\"efzq\\\"}', "zq"),
    (r'{\"Authorization\": \"Bearer a\\\"zqzq\"}', "zqzq"),
    (r'Cookie: a="x\"yzq"; b=2', "yzq"),
    # A key followed by more fields on the same line: the rest of the line goes with it.
    ("api_key=abc123 user=ann x=1", "user=ann"),
    (r'{"token": "ab\"cd", "next": "x"}', '"next"'),
    ("session: s1 started for owner 42", "owner 42"),
    # The value on the line after its name.
    ("password:\n  hunter2zq", "hunter2zq"),
]

KEPT = [
    "ERROR tico.runner: task 42 failed",
    "File \"/srv/tico-cell/companies/boatyard/data/flight/stacks.txt\", line 12, in run",
    "File \"/usr/lib/python3.12/concurrent/futures/thread.py\", line 58",
    "KeyError: 'task_id'",
    "GET /api/v2/tasks/{id}",
    "SELECT id FROM tasks WHERE status = '?' AND n > N",
    "UPDATE jobs SET x = ? WHERE id IN (?,…)",
    "sqlite3.OperationalError: database is locked",
    "container 123e4567-e89b-12d3-a456-426614174000 restarted",
    "what? nothing",
    "is the flight recorder on?)",
    "GET /api/v2/tasks?",
    "risk-free and sk-learn",
    "tico_flight_cost",
    "image ghcr.io/ticoteam/tico:v0.5.15",
]


@pytest.mark.parametrize("text,secret", SECRETS)
def test_the_scrubber_removes_real_secret_shapes(text, secret):
    out = cs.scrub(text)
    assert secret not in out, out
    assert "[redacted]" in out


@pytest.mark.parametrize("text", KEPT)
def test_the_scrubber_keeps_what_debugging_needs(text):
    assert cs.scrub(text) == text


def test_the_scrubber_stops_at_the_end_of_the_line_that_names_a_secret():
    text = "ERROR login token=abc \\\"x\\\" user=ann\nINFO kept line\r\nauthorization: Basic zz\nlast"
    assert cs.scrub(text) == "ERROR login token=[redacted]\nINFO kept line\r\nauthorization: [redacted]\nlast"


def test_the_scrubber_redacts_a_whole_header_and_is_idempotent():
    once = cs.scrub("authorization: Basic dXNlcjpwYXNz extra words")
    assert once == "authorization: [redacted]" and cs.scrub(once) == once


# --- the allow-list ---------------------------------------------------------------------------------------------------

SLUGS = {"boatyard", "stuck"}


@pytest.mark.parametrize("tool,args,why", [
    ("rm", {}, "unknown tool"),
    (None, {}, "unknown tool"),
    ("host_top", {"x": 1}, "takes no x"),
    ("host_top", [], "must be an object"),
    ("logs", {"company": "boatyard", "role": "server", "minutes": 5}, "needs lines"),
    ("logs", {"company": "boatyard", "role": "db", "minutes": 5, "lines": 10}, "one of server, updater"),
    ("logs", {"company": "boatyard", "role": "server", "minutes": 0, "lines": 10}, "whole number from 1 to 60"),
    ("logs", {"company": "boatyard", "role": "server", "minutes": 61, "lines": 10}, "from 1 to 60"),
    ("logs", {"company": "boatyard", "role": "server", "minutes": "5", "lines": 10}, "whole number"),
    ("logs", {"company": "boatyard", "role": "server", "minutes": True, "lines": 10}, "whole number"),
    ("logs", {"company": "boatyard", "role": "server", "minutes": 5.0, "lines": 10}, "whole number"),
    ("logs", {"company": "boatyard", "role": "server", "minutes": 5, "lines": 1001}, "from 1 to 1000"),
    ("flight", {"company": "boatyard", "minutes": 1441}, "from 1 to 1440"),
    ("db", {"company": "other"}, "no such company"),
    ("db", {"company": "../../etc"}, "no such company"),
    ("db", {"company": ["boatyard"]}, "no such company"),
    ("db", {"company": "boatyard", "path": "/etc/shadow"}, "takes no path"),
    ("stacks", {}, "needs company"),
    ("tracemalloc", {"company": "boatyard", "action": "stop"}, "one of start, dump"),
    ("journal", {"unit": "ssh", "minutes": 5}, "not an allowed unit"),
    ("journal", {"unit": "tico-cell-$(id)", "minutes": 5}, "not an allowed unit"),
    ("journal", {"unit": "docker; rm -rf /", "minutes": 5}, "not an allowed unit"),
    ("journal", {"unit": "--since=yesterday", "minutes": 5}, "not an allowed unit"),
    ("journal", {"unit": "docker", "minutes": 120}, "from 1 to 60"),
])
def test_arguments_outside_the_schema_are_refused(tool, args, why):
    with pytest.raises(cs.Refused, match=why.replace("(", r"\(")):
        cs.validate(tool, args, SLUGS)


def test_arguments_inside_the_schema_pass():
    assert cs.validate("host_top", None, SLUGS) == {}
    assert cs.validate("containers", {}, SLUGS) == {}
    assert cs.validate("containers", {"company": "stuck"}, SLUGS) == {"company": "stuck"}
    assert cs.validate("journal", {"unit": "tico-cell-stats.service", "minutes": 60}, SLUGS)["unit"] == \
        "tico-cell-stats.service"
    for unit in ("docker", "containerd", "cloudflared", "caddy", "tico-cell-firewall", "tico-cell-stats.timer"):
        cs.validate("journal", {"unit": unit, "minutes": 1}, SLUGS)
    assert set(cs.TOOLS) == set(cs.RUNNERS) == set(cs.TIMEOUTS)


def test_a_refused_request_runs_nothing(box):
    r = cs.run_tool(box, "logs", {"company": "boatyard", "role": "server", "minutes": 5, "lines": 5, "x": 1})
    assert r["ok"] is False and r["output"].startswith("refused:")
    assert not any(c[:2] == ["docker", "logs"] for c in box.calls)


# --- debug tools ------------------------------------------------------------------------------------------------------

def test_logs_and_journal_are_scrubbed_argument_lists(box):
    r = cs.run_tool(box, "logs", {"company": "boatyard", "role": "updater", "minutes": 5, "lines": 50})
    assert r["ok"] and "abc123" not in r["output"] and "token=[redacted]" in r["output"]
    assert ["docker", "logs", "--since", "5m", "--tail", "50", "--timestamps", "tico-boatyard-updater-1"] in box.calls
    r = cs.run_tool(box, "journal", {"unit": "docker", "minutes": 10})
    assert r["ok"] and "sk-abc" not in r["output"]
    assert box.calls[-1][:5] == ["journalctl", "-u", "docker", "--since", "10 min ago"]
    r = cs.run_tool(box, "logs", {"company": "stuck", "role": "updater", "minutes": 5, "lines": 50})
    assert r["ok"] is False and "no updater" in r["output"]


def test_containers_hides_mounts_outside_srv_and_unknown_labels(box):
    r = cs.run_tool(box, "containers", {"company": "boatyard"})
    got = json.loads(r["output"])
    mounts = got["containers"][0]["mounts"]
    assert mounts[0]["source"] == "/srv/tico-cell/companies/x/data"
    assert mounts[1]["source"] == "[outside /srv and /var/lib/docker]"
    assert "com.example.other" not in got["containers"][0]["labels"]
    assert "secret probe output" not in r["output"]
    assert "tico-boatyard-server-1" in got["stats"]


def test_db_reads_read_only_and_flight_reads_the_files(box):
    r = cs.run_tool(box, "db", {"company": "boatyard"})
    got = json.loads(r["output"])
    sq = got["sqlite"]
    assert r["ok"] and sq["journal_mode"] == "wal" and sq["quick_check"] == ["ok"] and sq["page_count"] > 0
    assert isinstance(sq["largest"], (list, str)) and got["header"]["page_count"] == sq["page_count"]
    r = cs.run_tool(box, "flight", {"company": "boatyard", "minutes": 60})
    got = json.loads(r["output"])
    assert r["ok"] and got["stalls_total"] == 1 and got["routes"][0]["route"] == "GET /api/v2/slow"
    r = cs.run_tool(box, "db", {"company": "stuck"})
    assert r["ok"] is False and "no database" in r["output"]


# --- SQLite: never on the host ----------------------------------------------------------------------------------------

def header(page=4096, pages=10, free=3, valid=True):
    h = bytearray(b"SQLite format 3\x00" + bytes(84))
    h[16:18] = (1 if page == 65536 else page).to_bytes(2, "big")
    h[24:28] = (7).to_bytes(4, "big")
    h[28:32] = pages.to_bytes(4, "big")
    h[36:40] = free.to_bytes(4, "big")
    h[92:96] = (7 if valid else 6).to_bytes(4, "big")
    return bytes(h)


def test_the_header_gives_page_size_count_and_free_pages():
    assert cs.parse_header(header(), 40960) == {"page_size": 4096, "page_count": 10, "freelist_count": 3}
    assert cs.parse_header(header(page=65536), 0)["page_size"] == 65536            # stored as 1
    assert cs.parse_header(header(pages=99, valid=False), 4096 * 12)["page_count"] == 12   # stale count: the size says
    assert cs.parse_header(header(pages=0), 4096 * 5)["page_count"] == 5
    for bad in (b"", header()[:99], b"garbage" * 20, header(page=1000), header(page=256)):
        assert cs.parse_header(bad, 40960) is None


def test_the_header_of_a_real_database_matches_what_sqlite_says(tmp_path):
    root = tmp_path / "co"
    (root / "data").mkdir(parents=True)
    make_db(root / "data", freelist=True)
    h = cs.db_header(root)
    c = sqlite3.connect(root / "data" / "hub.sqlite")
    want = [c.execute(f"PRAGMA {p}").fetchone()[0] for p in ("page_size", "page_count", "freelist_count")]
    c.close()
    assert [h["page_size"], h["page_count"], h["freelist_count"]] == want and h["freelist_count"] > 0
    (root / "data" / "hub.sqlite").write_bytes(b"not a database at all" * 10)
    assert cs.db_header(root)["size"] == 210 and "page_count" not in cs.db_header(root)


def test_nothing_on_the_host_opens_a_company_database(box, monkeypatch):
    def no(*a, **kw):
        raise AssertionError("sqlite3.connect on the host")
    monkeypatch.setattr(sqlite3, "connect", no)     # the fake docker exec runs its script in another process
    fake_process(box, 4100, 1)
    fake_process(box, 4150, 57)
    (box.cell_root / "companies" / "boatyard" / "data" / "flight" / "server.pid").write_text("57")
    snap = cs.collect(box, {})
    b = next(c for c in snap["companies"] if c["slug"] == "boatyard")
    assert b["db"]["freelist_pct"] > 0 and len(b["flight"]["sql"]["top"]) == 2
    for tool in cs.TOOLS:
        args = {"journal": {"unit": "docker", "minutes": 5}, "logs": {"company": "boatyard", "role": "server",
                "minutes": 5, "lines": 10}, "flight": {"company": "boatyard", "minutes": 5},
                "tracemalloc": {"company": "boatyard", "action": "start"}}.get(tool)
        if args is None:
            args = {"company": "boatyard"} if "company" in cs.TOOLS[tool] else {}
        r = cs.run_tool(box, tool, args)
        assert "sqlite3.connect on the host" not in r["output"], tool
    assert json.loads(cs.run_tool(box, "db", {"company": "boatyard"})["output"])["sqlite"]["quick_check"] == ["ok"]


def test_the_database_reads_run_in_the_server_container_as_its_user_with_a_fixed_script(box):
    cs.collect(box, {})
    cs.run_tool(box, "db", {"company": "boatyard"})
    execs = [c for c in box.calls if c[:2] == ["docker", "exec"]]
    assert [c[7] for c in execs] == ["top", "db"]
    for c in execs:
        assert c[2:7] == ["tico-boatyard-server-1", cs.EXEC_PYTHON, "-I", "-c", cs.DB_SCRIPT]
        assert "-u" not in c and "--user" not in c and not any(x.startswith("--user") for x in c)
        assert c[8] == cs.CONTAINER_DB and all(x.isdigit() for x in c[9:])
    with pytest.raises(cs.Refused):
        cs.exec_db(box, "--privileged", "db", 5)
    with pytest.raises(cs.Refused):
        cs.exec_db(box, "tico-boatyard-server-1", "import os", 5)


def test_a_stopped_server_says_so_for_the_sqlite_parts(box):
    got = json.loads(cs.run_tool(box, "db", {"company": "boatyard"})["output"])
    box.containers["tico-boatyard-server-1"][0]["State"] = "exited"
    box.containers["tico-boatyard-server-1"][1]["state"]["Status"] = "exited"
    r = cs.run_tool(box, "db", {"company": "boatyard"})
    stopped = json.loads(r["output"])
    assert r["ok"] and stopped["sqlite"] == "server not running" and stopped["header"] == got["header"]
    snap = cs.collect(box, {})
    b = next(c for c in snap["companies"] if c["slug"] == "boatyard")
    assert b["db"]["size_mb"] > 0 and b["flight"]["sql"]["top"] is None


def fake_process(box, host_pid, inner, ns="pid:[4026531999]", cmd=b"python\0-m\0backend.server\0"):
    d = box.path(f"proc/{host_pid}")
    (d / "ns").mkdir(parents=True, exist_ok=True)
    if not (d / "ns" / "pid").is_symlink():
        os.symlink(ns, d / "ns" / "pid")
    (d / "status").write_text(f"Name:\tpython\nNSpid:\t{host_pid}\t{inner}\n")
    (d / "cmdline").write_bytes(cmd)


def test_stacks_signals_the_server_by_its_host_pid_and_returns_what_it_wrote(box):
    import signal
    data = box.cell_root / "companies" / "boatyard" / "data" / "flight"
    (data / "server.pid").write_text("57")
    fake_process(box, 4100, 1)                           # the container's init
    fake_process(box, 4150, 57)                          # the server
    fake_process(box, 999, 57, ns="pid:[1]")             # pid 57 in another namespace: never signalled

    def wrote(pid, sig):
        with open(data / "stacks.txt", "a") as f:
            f.write('Thread 0x1 (most recent call first):\n  File "/app/backend/x.py", line 3 in f\n')
    box.on_kill = wrote
    r = cs.run_tool(box, "stacks", {"company": "boatyard"})
    assert r["ok"] and "backend/x.py" in r["output"], r
    assert box.kills == [(4150, signal.SIGUSR1)]


def test_stacks_refuses_a_pid_that_is_not_the_server(box):
    data = box.cell_root / "companies" / "boatyard" / "data" / "flight"
    (data / "server.pid").write_text("57")
    fake_process(box, 4100, 1)
    fake_process(box, 4150, 57, cmd=b"python\0-m\0backend.replication\0loop\0")
    r = cs.run_tool(box, "stacks", {"company": "boatyard"})
    assert r["ok"] is False and "not the Tico server" in r["output"] and box.kills == []
    r = cs.run_tool(box, "stacks", {"company": "stuck"})
    assert r["ok"] is False and box.kills == []


def test_tracemalloc_start_then_dump(box):
    import signal
    data = box.cell_root / "companies" / "boatyard" / "data" / "flight"
    (data / "server.pid").write_text("57")
    fake_process(box, 4100, 1)
    fake_process(box, 4150, 57)
    r = cs.run_tool(box, "tracemalloc", {"company": "boatyard", "action": "dump"})
    assert r["ok"] is False and "not on" in r["output"]
    r = cs.run_tool(box, "tracemalloc", {"company": "boatyard", "action": "start"})
    assert r["ok"] and box.kills == [(4150, signal.SIGUSR2)]
    write_flight(data, [{"ts": NOW - 5, "kind": "memory", "state": "started"}])
    assert cs.run_tool(box, "tracemalloc", {"company": "boatyard", "action": "start"})["ok"] is False
    box.on_kill = lambda pid, sig: (data / "memory-20261030T120000Z.txt").write_text("traced 1 bytes\n  100 1 x.py:1\n")
    r = cs.run_tool(box, "tracemalloc", {"company": "boatyard", "action": "dump"})
    assert r["ok"] and "traced 1 bytes" in r["output"]


# --- what a company controls: no symlink is followed out of it -------------------------------------------------------

LEAK = "LEAKED-" + KEY


def outside(box):
    """Files a company should never get read for it: here, next to this host's own stats key."""
    o = box.path("etc/tico-cell")
    (o / "flight").mkdir(parents=True)
    (o / "blobs").mkdir()
    (o / "stats.json").write_text(json.dumps({"key": KEY}))
    write_flight(o / "flight", busy_minute(NOW - 120) + [{"ts": NOW - 60, "kind": "slow", "route": "GET /" + LEAK}])
    (o / "flight" / "stacks.txt").write_text(LEAK + "\n")
    (o / "flight" / "server.pid").write_text("57")
    (o / "flight" / "memory-20261030T120000Z.txt").write_text(LEAK + "\n")
    (o / "blobs" / "big").write_bytes(b"x" * 3_000_000)
    make_db(o)
    (o / "tico-backup-status.json").write_text(json.dumps({"last_replicated_at": "2026-10-30T11:59:51Z", "k": LEAK}))
    (o / "log").write_text(json.dumps({"log": f"ERROR {LEAK}\n", "time": "2026-10-30T11:59:59Z"}) + "\n")
    return o


def plant(box, how):
    """Swap boatyard's files for symlinks out of the company: each file itself ("leaf"), or a folder on the way."""
    import shutil
    o = outside(box)
    data = box.cell_root / "companies" / "boatyard" / "data"
    tmp = box.path("proc/4100/root/tmp")
    _, ins, _ = box.containers["tico-boatyard-server-1"]
    log = box.path(ins["log"])
    if how == "leaf":
        for name in ("flight.ndjson", "stacks.txt", "server.pid", "memory-20261030T120000Z.txt"):
            (data / "flight" / name).unlink(missing_ok=True)
            os.symlink(o / "flight" / name, data / "flight" / name)
        for side in ("", "-wal", "-shm"):
            (data / f"hub.sqlite{side}").unlink(missing_ok=True)
        os.symlink(o / "hub.sqlite", data / "hub.sqlite")
        shutil.rmtree(data / "blobs")
        os.symlink(o / "blobs", data / "blobs")
        (tmp / "tico-backup-status.json").unlink()
        os.symlink(o / "tico-backup-status.json", tmp / "tico-backup-status.json")
        log.unlink()
        os.symlink(o / "log", log)
    else:
        shutil.rmtree(data)
        os.symlink(o, data)                  # data/ itself: flight/, hub.sqlite and blobs/ all lie outside
        shutil.rmtree(tmp)
        os.symlink(o, tmp)                   # /tmp in the server's root
        shutil.move(str(log), str(o / log.name))
        shutil.rmtree(log.parent)
        os.symlink(o, log.parent)            # the container's log folder
    return o


@pytest.mark.parametrize("how", ["leaf", "parent"])
def test_symlinks_out_of_a_company_are_never_followed(box, how):
    plant(box, how)
    fake_process(box, 4100, 1)
    fake_process(box, 4150, 57)
    snap = cs.collect(box, {})
    b = next(c for c in snap["companies"] if c["slug"] == "boatyard")
    assert LEAK not in json.dumps(snap) and KEY not in json.dumps(snap)
    assert b["db"] == {"size_mb": None, "wal_mb": None, "freelist_pct": None, "files_mb": None}
    assert b["backup"] is None and b["errors"] == []
    assert b["flight"] is None if how == "parent" else b["flight"]["requests"]["count"] == 0
    for tool, args in [("flight", {"minutes": 60}), ("db", {}), ("stacks", {}), ("tracemalloc", {"action": "dump"})]:
        r = cs.run_tool(box, tool, {"company": "boatyard", **args})
        assert LEAK not in r["output"] and KEY not in r["output"], (tool, r)
        assert r["ok"] is (tool == "flight" and how == "leaf"), (tool, r)
    assert box.kills == []                   # server.pid was outside too: nothing signalled


def test_stacks_written_through_a_symlink_are_not_read(box):
    o = plant(box, "leaf")
    flight = box.cell_root / "companies" / "boatyard" / "data" / "flight"
    (flight / "server.pid").unlink()
    (flight / "server.pid").write_text("57")
    fake_process(box, 4100, 1)
    fake_process(box, 4150, 57)
    box.on_kill = lambda pid, sig: (o / "flight" / "stacks.txt").write_text(LEAK * 2)
    r = cs.run_tool(box, "stacks", {"company": "boatyard"})
    assert r["ok"] is False and LEAK not in r["output"] and "not readable" in r["output"]


def test_safe_open_takes_only_a_small_regular_file_below_its_root(tmp_path):
    root = tmp_path / "root"
    (root / "d").mkdir(parents=True)
    (root / "d" / "f").write_text("ok")
    (tmp_path / "secret").write_text(LEAK)
    os.symlink(tmp_path / "secret", root / "d" / "leaf")
    os.symlink(tmp_path, root / "up")
    os.mkfifo(root / "d" / "fifo")           # opening a FIFO to read would wait for a writer forever
    assert cs.safe_read(root, "d/f", 10) == b"ok"
    for rel in ("d/leaf", "up/secret", "d/fifo", "../secret", "/etc/passwd", "d/../../secret"):
        with pytest.raises(OSError):
            cs.safe_read(root, rel, 100)
    with pytest.raises(cs.Unsafe):
        cs.safe_read(root, "d/f", 10, cap=1)
    with pytest.raises(FileNotFoundError):
        cs.safe_stat(root, "d/missing")
    with pytest.raises(OSError):
        cs.safe_listdir(root, "up")
    assert cs.files_mb(root, "up") is None


def test_long_output_is_cut_with_a_note(box, monkeypatch):
    monkeypatch.setitem(cs.RUNNERS, "host_top", lambda *a: (True, "line\n" * 100_000))
    r = cs.run_tool(box, "host_top", {})
    assert len(r["output"].encode()) <= cs.MAX_OUTPUT and "[cut:" in r["output"]


# --- push -------------------------------------------------------------------------------------------------------------

def enrolled(box):
    path = box.path(cs.CONFIG)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"hq": "https://updates.tico.team", "host_id": "cell-1", "key": KEY}))


def test_push_sends_gzip_json_and_answers_the_debug_requests(box):
    enrolled(box)
    box.answers = [(200, {}, json.dumps({"ok": True, "debug": [
        {"id": "dbg_0123456789abcdef", "tool": "journal", "args": {"unit": "docker", "minutes": 5}},
        {"id": "dbg_fedcba9876543210", "tool": "shell", "args": {"cmd": "id"}},
        {"id": "../../v1/staff", "tool": "host_top", "args": {}}]}).encode())]
    out = io.StringIO()
    assert cs.push(box, out=out) == 0
    url, body, headers = box.posts[0]
    assert url == "https://updates.tico.team/v1/cells/cell-1/stats"
    assert headers["Authorization"] == "Bearer " + KEY and headers["Content-Encoding"] == "gzip"
    snap = json.loads(gzip.decompress(body))
    assert snap["host_id"] == "cell-1" and {c["slug"] for c in snap["companies"]} == {"boatyard", "stuck"}
    results = {u.rsplit("/", 1)[1]: (json.loads(b), h) for u, b, h in box.posts[1:]}
    assert set(results) == {"dbg_0123456789abcdef", "dbg_fedcba9876543210"}      # no address for the third
    ok, h = results["dbg_0123456789abcdef"]
    assert ok["ok"] is True and "sk-abc" not in ok["output"] and isinstance(ok["ms"], int)
    assert "Content-Encoding" not in h
    bad, _ = results["dbg_fedcba9876543210"]
    assert bad == {"ok": False, "ms": bad["ms"], "output": "refused: unknown tool 'shell'"}
    assert all(u.startswith("https://updates.tico.team/v1/cells/cell-1/debug/dbg_") for u, _, _ in box.posts[1:])
    report = json.loads(out.getvalue())
    assert report["ok"] and report["debug"] == {"ran": 2, "left": 0}
    assert (box.path(cs.CACHE).stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize("status,code,detail", [(429, 0, "too soon (Retry-After 15)"), (401, 1, "refused the key"),
                                                (403, 1, "refused this host"), (0, 1, "could not be reached")])
def test_a_refused_push_is_said_once_and_not_retried(box, status, code, detail):
    enrolled(box)
    box.answers = [(status, {"Retry-After": "15"}, b"connection refused")]
    out = io.StringIO()
    assert cs.push(box, out=out) == code
    assert [u for u, _, _ in box.posts] == ["https://updates.tico.team/v1/cells/cell-1/stats"]
    assert detail in json.loads(out.getvalue())["detail"]


def test_requests_the_tick_has_no_time_for_wait_for_the_next(box):
    enrolled(box)
    items = [{"id": f"dbg_{i:016x}", "tool": "host_top", "args": {}} for i in range(3)]
    box.answers = [(200, {}, json.dumps({"ok": True, "debug": items}).encode())]
    real = cs.run_tool

    def slow(*a, **kw):
        box.t += 30                                      # each one takes most of the budget
        return real(*a, **kw)
    cs_run = cs.run_tool
    cs.run_tool = slow
    try:
        out = io.StringIO()
        cs.push(box, out=out)
        assert json.loads(out.getvalue())["debug"] == {"ran": 2, "left": 1}
        queued = json.loads(box.path(cs.QUEUE).read_text())["items"]
        assert [q["id"] for q in queued] == ["dbg_0000000000000002"]
        box.answers = [(200, {}, b'{"ok": true, "debug": []}')]
        out = io.StringIO()
        cs.push(box, out=out)
        assert json.loads(out.getvalue())["debug"] == {"ran": 1, "left": 0}
        assert json.loads(box.path(cs.QUEUE).read_text())["items"] == []
    finally:
        cs.run_tool = cs_run
    posted = [u.rsplit("/", 1)[1] for u, _, _ in box.posts if "/debug/" in u]
    assert posted == [f"dbg_{i:016x}" for i in range(3)]


def test_push_without_enrollment_says_so(box):
    with pytest.raises(SystemExit, match="not enrolled"):
        cs.push(box, out=io.StringIO())


# --- enroll -----------------------------------------------------------------------------------------------------------

def test_enroll_reads_the_key_from_stdin_and_installs_the_timer(box):
    core = box.path("opt/tico-cell/core-v0.5.16")
    (core / "setup").mkdir(parents=True)
    (core / "setup" / "cell_stats.py").write_text("")
    box.path("opt/tico-cell/core-v0.5.9/setup").mkdir(parents=True)
    (box.path("opt/tico-cell/core-v0.5.9/setup") / "cell_stats.py").write_text("")
    args = cs.parser().parse_args(["enroll", "--hq", "https://updates.tico.team/", "--host-id", "cell-1"])
    out = io.StringIO()
    assert cs.enroll(box, args, stdin=io.StringIO(KEY + "\n"), out=out) == 0, out.getvalue()
    assert KEY not in out.getvalue()
    cfg = box.path(cs.CONFIG)
    assert json.loads(cfg.read_text()) == {"hq": "https://updates.tico.team", "host_id": "cell-1", "key": KEY}
    assert cfg.stat().st_mode & 0o777 == 0o600
    assert os.readlink(box.path(cs.CORE_LINK)) == str(core)          # the newest by version, not by name
    service = box.path(cs.UNIT_DIR, ) / cs.SERVICE
    text = service.read_text()
    for line in ("Type=oneshot", "ExecStart=/usr/bin/python3 -m setup.cell_stats push", "TimeoutStartSec=50",
                 "Nice=10", "IOSchedulingClass=idle", "WorkingDirectory=/opt/tico-cell/stats-core"):
        assert line in text.splitlines()
    timer = (box.path(cs.UNIT_DIR) / cs.TIMER).read_text().splitlines()
    assert "OnUnitActiveSec=60s" in timer and "AccuracySec=5s" in timer and "Persistent=false" in timer
    assert ["systemctl", "enable", "--now", cs.TIMER] in box.calls

    out = io.StringIO()
    assert cs.enroll(box, cs.parser().parse_args(["enroll", "--uninstall"]), out=out) == 0
    assert not cfg.exists() and not service.exists() and not box.path(cs.CORE_LINK).is_symlink()
    assert ["systemctl", "disable", "--now", cs.TIMER] in box.calls


@pytest.mark.parametrize("argv,key,error", [
    (["--hq", "https://updates.tico.team", "--host-id", "cell-1"], "tch_short", "bad_key"),
    (["--hq", "https://updates.tico.team", "--host-id", "cell-1"], "", "bad_key"),
    (["--hq", "http://updates.tico.team", "--host-id", "cell-1"], KEY, "bad_args"),
    (["--hq", "https://updates.tico.team/v1", "--host-id", "cell-1"], KEY, "bad_args"),
    (["--hq", "https://u:p@updates.tico.team", "--host-id", "cell-1"], KEY, "bad_args"),
    (["--hq", "https://updates.tico.team", "--host-id", "Cell_1"], KEY, "bad_args"),
])
def test_enroll_refuses_bad_input_and_writes_nothing(box, argv, key, error):
    out = io.StringIO()
    assert cs.enroll(box, cs.parser().parse_args(["enroll", *argv]), stdin=io.StringIO(key), out=out) == 1
    assert json.loads(out.getvalue())["error"] == error and not box.path(cs.CONFIG).exists()


def test_enroll_has_no_key_flag():
    with pytest.raises(SystemExit):
        cs.parser().parse_args(["enroll", "--hq", "https://x.example", "--host-id", "c", "--key", KEY])


def test_push_moves_the_link_to_a_newer_core(box):
    enrolled(box)
    old = box.path("opt/tico-cell/core-v0.5.16/setup")
    old.mkdir(parents=True)
    (old / "cell_stats.py").write_text("")
    cs.push(box, out=io.StringIO())
    assert os.readlink(box.path(cs.CORE_LINK)).endswith("core-v0.5.16")
    new = box.path("opt/tico-cell/core-v0.5.17/setup")
    new.mkdir(parents=True)
    box.path("opt/tico-cell/core-v0.6.0").mkdir()                     # no collector in it: not picked
    (new / "cell_stats.py").write_text("")
    cs.push(box, out=io.StringIO())
    assert os.readlink(box.path(cs.CORE_LINK)).endswith("core-v0.5.17")


# --- the flight reader this relies on ---------------------------------------------------------------------------------

def test_flight_file_loads_by_path_with_plain_python():
    code = ("import sys; from setup import cell_stats as cs; ff = cs.flight_file(); "
            "print(ff.FORMAT, sorted(k for k in sys.modules if k.split('.')[0] == 'backend'))")
    r = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); {code}"],
                       capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "1 []"


def test_request_percentiles_are_exact_for_one_row_and_weighted_across_rows():
    assert flight_file.quantiles([(10, 5, 50, 100)]) == [5.0, 50.0, 90.0]
    assert flight_file.quantiles([]) == [None, None, None]
    assert flight_file.quantiles([(1, 7, 7, 7)]) == [7.0, 7.0, 7.0]
    p50, p95, p99 = flight_file.quantiles([(1000, 5, 10, 20), (30, 3000, 5000, 9000)])
    assert p50 < 6 and p95 < 20 and p99 > 20


def test_a_big_flight_file_is_read_from_where_the_window_starts(tmp_path, monkeypatch):
    folder = tmp_path / "flight"
    rows = [{"ts": NOW - 86400 + i * 6, "kind": "slow", "route": f"GET /r{i}", "ms": 1500, "pad": "x" * 150}
            for i in range(86400 // 6)]
    write_flight(folder, rows)
    assert (folder / flight_file.NAME).stat().st_size > flight_file.TAIL_FROM
    want = [r["route"] for r in rows if r["ts"] >= NOW - 600]
    got = [r["route"] for r in flight_file.records(folder, NOW - 600)]
    assert got == want
    monkeypatch.setattr(flight_file, "TAIL_FROM", 10 ** 12)          # the whole file, as before
    assert [r["route"] for r in flight_file.records(folder, NOW - 600)] == want
