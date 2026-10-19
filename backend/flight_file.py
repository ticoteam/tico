"""The flight recorder's local file: what the server was doing, readable when the server is not.

The recorder's database tables (backend/flight.py) live in the database a stall is usually about, and are read through
the API a stalled server cannot answer. So every stall, slow request and per-minute row is written first to an
append-only NDJSON file next to the database (`<TICO_DB's folder>/flight/flight.ndjson`, rotated at 10 MB, ten files
kept), by its own thread from a bounded queue: a full queue drops the record and counts it, it never blocks the caller.
`python -m backend.flight summary --since 30m` reads only these files. The record format is in docs/observability.md.

Also here: the resource counters a stall is classified by (cpu, memory, disk, lock or unknown), and the two signals:
SIGUSR1 writes every thread's stack to `stacks.txt` (faulthandler, so it works while the event loop is stuck in C),
SIGUSR2 starts tracemalloc and, sent again, writes the top 50 allocation sites and stops it.
"""
import collections
import json
import logging
import os
import queue
import resource
import sys
import threading
import time
from pathlib import Path

LOG = logging.getLogger("tico.flight")

FORMAT = 1
NAME = "flight.ndjson"
MAX_BYTES = 10 * 1024 * 1024
KEEP = 10                       # the live file and nine rotated ones
QUEUE = 10_000
STACKS = "stacks.txt"           # SIGUSR1's dumps, appended; started over past STACKS_MAX
STACKS_MAX = 2 * 1024 * 1024
PID = "server.pid"


def directory(db_path):
    """Where the files go: a `flight` folder beside the database (Docker: /data/flight; a local install: its
    environment's folder)."""
    return Path(db_path).expanduser().absolute().parent / "flight"


def _private_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


# ---------------------------------------------------------------------------- the writer

class Writer(threading.Thread):
    """Appends records to the file from a bounded queue. `put` never blocks: when the queue is full the record is
    dropped and counted, and the count is written as a `drops` record once there is room."""

    def __init__(self, folder, max_bytes=MAX_BYTES, keep=KEEP, size=QUEUE):
        super().__init__(name="tico-flight-file", daemon=True)
        self.folder, self.max_bytes, self.keep = Path(folder), max_bytes, keep
        self.path = self.folder / NAME
        self.queue = queue.Queue(maxsize=size)
        self.dropped = 0            # records lost to a full queue or a failed write, since the start
        self.reported = 0
        self.written = 0
        self.file = None
        self.size = 0

    def put(self, record):
        record = {"v": FORMAT, "ts": round(time.time(), 3), **record}
        try:
            self.queue.put_nowait(record)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def close(self, timeout=5):
        try:
            self.queue.put(None, timeout=1)
        except queue.Full:
            pass
        if self.is_alive():
            self.join(timeout=timeout)

    def run(self):
        while True:
            batch = [self.queue.get()]
            while len(batch) < 500:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            stop = None in batch
            lines = [json.dumps(r, separators=(",", ":"), default=str) + "\n" for r in batch if r is not None]
            if self.dropped > self.reported:
                n, self.reported = self.dropped - self.reported, self.dropped
                lines.append(json.dumps({"v": FORMAT, "ts": round(time.time(), 3), "kind": "drops", "n": n},
                                        separators=(",", ":")) + "\n")
            self._write(lines)
            if stop:
                if self.file:
                    self.file.close()
                    self.file = None
                return

    def _write(self, lines):
        try:
            if self.file is None:
                _private_dir(self.folder)
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                self.file = os.fdopen(fd, "a", encoding="utf-8")
                self.size = os.fstat(fd).st_size
            for line in lines:
                n = len(line.encode())
                if self.size and self.size + n > self.max_bytes:
                    self._rotate()
                self.file.write(line)
                self.size += n
                self.written += 1
            self.file.flush()
        except OSError as exc:
            self.dropped += len(lines)
            self.reported = self.dropped        # nowhere to report them: the file is what failed
            LOG.warning("Flight file write failed: %s", type(exc).__name__)
            try:
                if self.file:
                    self.file.close()
            except OSError:
                pass
            self.file = None

    def _rotate(self):
        self.file.close()
        for i in range(self.keep - 1, 0, -1):
            older = self.folder / f"{NAME}.{i}"
            if i == self.keep - 1:
                older.unlink(missing_ok=True)
            newer = self.folder / (NAME if i == 1 else f"{NAME}.{i - 1}")
            if newer.exists():
                newer.rename(older)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        self.file = os.fdopen(fd, "a", encoding="utf-8")
        self.size = 0


# ---------------------------------------------------------------------------- resource counters

_LIBPROC = None


def rss_now():
    """Current resident memory in bytes: /proc on Linux, proc_pid_rusage on macOS (not getrusage's peak)."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    if sys.platform == "darwin":
        global _LIBPROC
        try:
            import ctypes
            import struct
            if _LIBPROC is None:
                _LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib")
            buf = ctypes.create_string_buffer(512)
            if _LIBPROC.proc_pid_rusage(os.getpid(), 0, buf) == 0:       # RUSAGE_INFO_V0
                return struct.unpack_from("<Q", buf, 64)[0]               # ri_resident_size
        except Exception:
            _LIBPROC = False
    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak if sys.platform == "darwin" else peak * 1024
    except Exception:
        return None


def _io():
    """Bytes this process read from and wrote to storage (Linux /proc/self/io), or None where there is none."""
    try:
        with open("/proc/self/io") as f:
            fields = dict(line.split(":", 1) for line in f.read().splitlines() if ":" in line)
        return int(fields["read_bytes"]), int(fields["write_bytes"])
    except (OSError, KeyError, ValueError):
        return None


def counters(lock_wait_ms=0.0):
    """One sample: monotonic time, CPU seconds, page faults, involuntary context switches, block operations, storage
    bytes (Linux), resident memory, and the write-lock wait finished so far (ms)."""
    u = resource.getrusage(resource.RUSAGE_SELF)
    io = _io()
    return (time.monotonic(), u.ru_utime, u.ru_stime, u.ru_majflt, u.ru_minflt, u.ru_nivcsw, u.ru_inblock,
            u.ru_oublock, io[0] if io else None, io[1] if io else None, rss_now(), lock_wait_ms)


# A stall's class, from what the process did while the event loop was not waking. Checked in this order; the first that
# holds wins. `lock_share`: the write-lock wait in the window (finished waits plus waits still going) over the window;
# `cpu_share`: CPU seconds over the window (1.0 = one core busy); `majflt`: page faults that read from storage
# (memory pressure); `io_mb`: storage megabytes read and written (Linux), or block operations elsewhere.
RULES = {"lock_share": 0.5, "majflt": 50, "rss_mb": 512, "cpu_share": 0.8, "nivcsw_per_s": 200, "io_mb": 50,
         "blocks": 2000}


def classify(d, rules=RULES):
    if d["lock_ms"] >= rules["lock_share"] * d["window_ms"]:
        return "lock"
    if d["majflt"] >= rules["majflt"] or (d["rss_mb"] or 0) >= rules["rss_mb"]:
        return "memory"
    seconds = max(d["window_ms"] / 1000, 1e-3)
    if d["cpu_s"] >= rules["cpu_share"] * seconds or d["nivcsw"] / seconds >= rules["nivcsw_per_s"]:
        return "cpu"
    if (d["io_mb"] or 0) >= rules["io_mb"] or d["inblock"] + d["oublock"] >= rules["blocks"]:
        return "disk"
    return "unknown"


class Usage:
    """The last few minutes of counters, sampled every recorder tick, so a stall's window can be measured after the
    fact."""

    def __init__(self, keep=1200):
        self.ring = collections.deque(maxlen=keep)

    def sample(self, lock_wait_ms=0.0):
        self.ring.append(counters(lock_wait_ms))

    def window(self, start, waiting=(), now=None):
        """Deltas from the newest sample at or before `start` (monotonic) to the newest sample; `waiting` holds the
        monotonic start of each write-lock wait still going."""
        if not self.ring:
            return None
        end = self.ring[-1]
        first = self.ring[0]
        for s in self.ring:
            if s[0] > start:
                break
            first = s
        now = end[0] if now is None else now
        window_ms = max((end[0] - first[0]) * 1000, 1.0)
        going = sum(max(0.0, now - max(w, first[0])) for w in waiting) * 1000
        io = None if end[8] is None or first[8] is None else (end[8] - first[8] + end[9] - first[9]) / 1e6
        return {"window_ms": round(window_ms), "cpu_s": round(end[1] - first[1] + end[2] - first[2], 3),
                "majflt": end[3] - first[3], "minflt": end[4] - first[4], "nivcsw": end[5] - first[5],
                "inblock": end[6] - first[6], "oublock": end[7] - first[7],
                "io_mb": round(io, 1) if io is not None else None,
                "rss_mb": round((end[10] - first[10]) / 1e6, 1) if end[10] and first[10] else None,
                "lock_ms": round(end[11] - first[11] + going)}


# ---------------------------------------------------------------------------- signals

_SIGNALS = {}


def install_signals(folder, writer=None):
    """SIGUSR1: every thread's stack to `stacks.txt`. SIGUSR2: start tracemalloc; sent again, the top 50 allocation sites
    to `memory-<time>.txt` (and a `memory` record), and tracing stops. Main thread only (a signal handler can only be
    set there); neither signal is used by uvicorn or litestream, which forwards only SIGINT and SIGTERM, so a signal
    sent to the container's first process does not reach the server: send it to the pid in `server.pid`."""
    import signal
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGUSR1"):
        return False
    folder = Path(folder)
    try:
        _private_dir(folder)
        (folder / PID).write_text(str(os.getpid()))
        path = folder / STACKS
        if path.exists() and path.stat().st_size > STACKS_MAX:
            path.unlink()
        import faulthandler
        old = _SIGNALS.pop("stacks", None)
        if old:
            old.close()
        stacks = open(path, "a")
        _SIGNALS["stacks"] = stacks
        faulthandler.register(signal.SIGUSR1, file=stacks, all_threads=True, chain=False)
    except (OSError, AttributeError, ValueError, RuntimeError) as exc:
        LOG.warning("Flight stack signal not installed: %s", type(exc).__name__)

    def memory(signum, frame):
        # Snapshots take a while: off the main thread, which is the event loop.
        threading.Thread(target=memory_toggle, args=(folder, writer), name="tico-flight-memory", daemon=True).start()
    try:
        signal.signal(signal.SIGUSR2, memory)
    except (OSError, ValueError) as exc:
        LOG.warning("Flight memory signal not installed: %s", type(exc).__name__)
    return True


def memory_toggle(folder, writer=None, top=50):
    import tracemalloc
    if not tracemalloc.is_tracing():
        tracemalloc.start(1)
        LOG.warning("Flight: tracemalloc started; send SIGUSR2 again to write the top %d allocation sites", top)
        if writer:
            writer.put({"kind": "memory", "state": "started"})
        return None
    snapshot = tracemalloc.take_snapshot()
    traced, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    from .flight import _where
    rows = [{"where": f"{_where(s.traceback[0].filename)}:{s.traceback[0].lineno}", "bytes": s.size, "count": s.count}
            for s in snapshot.statistics("lineno")[:top]]
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    try:
        _private_dir(Path(folder))
        with open(Path(folder) / f"memory-{stamp}.txt", "w") as f:
            f.write(f"traced {traced} bytes, peak {peak}\n")
            for r in rows:
                f.write(f"{r['bytes']:>12} {r['count']:>8} {r['where']}\n")
    except OSError:
        pass
    if writer:
        writer.put({"kind": "memory", "state": "dumped", "traced": traced, "peak": peak, "top": rows})
    LOG.warning("Flight: wrote the top %d allocation sites to %s/memory-%s.txt", top, folder, stamp)
    return rows


# ---------------------------------------------------------------------------- reading the files

def files(folder):
    """The files, oldest first."""
    folder = Path(folder)
    out = [folder / f"{NAME}.{i}" for i in range(KEEP + 5, 0, -1)] + [folder / NAME]
    return [p for p in out if p.exists()]


def records(folder, since=0.0):
    for path in files(folder):
        try:
            if path.stat().st_mtime < since:
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue                    # a line cut short by a crash
                    if isinstance(r, dict) and (r.get("ts") or 0) >= since:
                        yield r
        except OSError:
            continue


def summary(folder, since_s=1800, now=None, stall_limit=10, stack_frames=12):
    """What the files say about the last `since_s` seconds."""
    now = now or time.time()
    since = now - since_s
    routes, slow, stalls, ends, minutes, starts, memory, holds = {}, [], [], {}, [], [], [], []
    drops = 0
    for r in records(folder, since):
        kind = r.get("kind")
        if kind == "routes":
            for x in r.get("routes") or []:
                m = routes.setdefault(x.get("route"), {"route": x.get("route"), "n": 0, "errors": 0, "total_ms": 0.0,
                                                        "max_ms": 0.0, "p95": 0.0, "bytes": 0, "bytes_max": 0,
                                                        "sql_ms": 0.0, "wait_ms": 0.0})
                m["n"] += x.get("n") or 0
                m["errors"] += x.get("errors") or 0
                m["total_ms"] += x.get("total_ms") or 0
                m["max_ms"] = max(m["max_ms"], x.get("max_ms") or 0)
                m["p95"] = max(m["p95"], x.get("p95") or 0)
                m["bytes"] += x.get("bytes") or 0
                m["bytes_max"] = max(m["bytes_max"], x.get("bytes_max") or 0)
                m["sql_ms"] += x.get("sql_ms") or 0
                m["wait_ms"] += x.get("wait_ms") or 0
        elif kind == "slow":
            slow.append(r)
        elif kind == "long_hold":
            holds.append(r)
        elif kind == "stall":
            stalls.append(r)
        elif kind == "stall_end":
            ends[r.get("id")] = r
        elif kind == "minute":
            minutes.append(r)
            drops += r.get("dropped") or 0
        elif kind == "drops":
            drops += r.get("n") or 0
        elif kind == "start":
            starts.append({"ts": r.get("ts"), "version": r.get("version"), "pid": r.get("pid")})
        elif kind == "memory" and r.get("state") == "dumped":
            memory.append(r)
    for row in routes.values():
        row["total_ms"] = round(row["total_ms"], 1)
        row["sql_ms"] = round(row["sql_ms"], 1)
        row["wait_ms"] = round(row["wait_ms"], 1)
        row["avg_bytes"] = row["bytes"] // row["n"] if row["n"] else 0
    chosen = stalls[-stall_limit:]
    if not any(s.get("threads") for s in chosen):
        # Stacks are taken at most once a minute: keep the latest stall that has them.
        latest = next((s for s in reversed(stalls) if s.get("threads")), None)
        if latest is not None:
            chosen = [latest] + chosen[1:]
    out_stalls = []
    for s in chosen:
        end = ends.get(s.get("id"))
        threads = (s.get("threads") or {}).get("busy") or []
        out_stalls.append({"ts": s.get("ts"), "seconds": end.get("seconds") if end else s.get("seconds"),
                           "ongoing": end is None, "class": (end or s).get("class"),
                           "usage": (end or s).get("usage"),
                           "threads": [{"thread": t.get("thread"), "loop": t.get("loop"),
                                        "stack": (t.get("stack") or [])[-stack_frames:]} for t in threads[:8]],
                           "idle_threads": (s.get("threads") or {}).get("idle")})
    waits = [m for m in minutes if (m.get("wait_max") or 0) >= 1000 or m.get("locked")]
    folder = Path(folder)
    dump = None
    try:
        st = (folder / STACKS).stat()
        if st.st_mtime >= since and st.st_size:
            with open(folder / STACKS, "rb") as f:
                f.seek(max(0, st.st_size - 16384))
                dump = {"ts": round(st.st_mtime), "text": f.read().decode("utf-8", "replace").splitlines()[-120:]}
    except OSError:
        pass

    def peak(key):
        values = [m.get(key) for m in minutes if m.get(key) is not None]
        return max(values) if values else None
    return {"folder": str(folder), "since": round(since), "now": round(now), "starts": starts,
            "routes": sorted(routes.values(), key=lambda x: -x["total_ms"])[:15],
            "slow": sorted(slow, key=lambda x: -(x.get("ms") or 0))[:15],
            "long_holds": sorted(holds, key=lambda x: -(x.get("hold_s") or 0))[:15],
            "stalls": out_stalls, "stalls_total": len(stalls),
            "minutes": [{k: m.get(k) for k in ("ts", "cpu", "rss", "lag_max", "requests", "wait_max", "wait_ms",
                                                "hold_max", "locked", "dropped", "db_failed", "db_pending")}
                        for m in minutes],
            "peaks": {"cpu": peak("cpu"), "rss": peak("rss"), "lag_max": peak("lag_max"), "wait_max": peak("wait_max"),
                      "hold_max": peak("hold_max")},
            "lock_waits": {"minutes": len(waits), "wait_ms": round(sum(m.get("wait_ms") or 0 for m in minutes)),
                           "locked": sum(m.get("locked") or 0 for m in minutes)},
            "drops": drops, "stack_dump": dump, "memory": memory[-1:] if memory else []}


def _clock(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts or 0))


def _mb(n):
    return "-" if n is None else f"{n / 1e6:.0f}MB"


def render(s):
    """The summary as plain text for a terminal."""
    out = [f"Flight file {s['folder']}: {_clock(s['since'])} to {_clock(s['now'])}"]
    for st in s["starts"]:
        out.append(f"  server start {_clock(st['ts'])} version {st.get('version') or '?'} pid {st.get('pid')}")
    out.append(f"\nStalls ({s['stalls_total']}):" if s["stalls_total"] else "\nStalls: none")
    for st in s["stalls"]:
        u = st.get("usage") or {}
        out.append(f"  {_clock(st['ts'])} {st.get('seconds')}s{' (still going)' if st['ongoing'] else ''} "
                   f"class={st.get('class')} cpu={u.get('cpu_s')}s lock_wait={u.get('lock_ms')}ms "
                   f"majflt={u.get('majflt')} io={'-' if u.get('io_mb') is None else str(u['io_mb']) + 'MB'} "
                   f"blocks={(u.get('inblock') or 0) + (u.get('oublock') or 0)}")
        for t in st["threads"]:
            out.append(f"    {'[loop] ' if t.get('loop') else ''}{t.get('thread')}:")
            out += [f"      {frame}" for frame in reversed(t.get("stack") or [])]
    out.append("\nSlowest routes (by total time):")
    out.append(f"  {'route':<48} {'n':>6} {'total s':>8} {'p95 ms':>8} {'max ms':>8} {'sql ms':>8} {'lock ms':>8} "
               f"{'avg B':>8} {'max B':>9}")
    for r in s["routes"]:
        out.append(f"  {str(r['route'])[:48]:<48} {r['n']:>6} {r['total_ms'] / 1000:>8.1f} {r['p95']:>8.0f} "
                   f"{r['max_ms']:>8.0f} {r['sql_ms']:>8.0f} {r['wait_ms']:>8.0f} {r['avg_bytes']:>8} {r['bytes_max']:>9}")
    if s["slow"]:
        out.append("\nSlow requests:")
        for r in s["slow"]:
            out.append(f"  {_clock(r.get('ts'))} {r.get('route')} {r.get('ms')}ms status {r.get('status')} "
                       f"sql {r.get('sql_ms')}ms lock wait {r.get('wait_ms')}ms queries {r.get('queries')} "
                       f"bytes {r.get('bytes')}")
    if s.get("long_holds"):
        out.append("\nWrite lock held longest (who held it):")
        for h in s["long_holds"]:
            out.append(f"  {_clock(h.get('ts'))} {h.get('hold_s')}s{' (still held)' if h.get('open') else ''} "
                       f"{h.get('route') or 'background job'} at {h.get('site') or '?'}")
    p = s["peaks"]
    out.append(f"\nPer minute (peaks: cpu {p['cpu']}%, rss {_mb(p['rss'])}, loop lag {p['lag_max']}ms, "
               f"lock wait {p['wait_max']}ms, hold {p['hold_max']}ms):")
    out.append(f"  {'minute':<9} {'cpu%':>6} {'rss':>7} {'lag ms':>8} {'reqs':>6} {'wait max':>9} {'hold max':>9} "
               f"{'locked':>6}")
    for m in s["minutes"][-30:]:
        out.append(f"  {_clock(m['ts']):<9} {m.get('cpu') if m.get('cpu') is not None else '-':>6} {_mb(m.get('rss')):>7} "
                   f"{m.get('lag_max') or 0:>8} {m.get('requests') or 0:>6} {m.get('wait_max') or 0:>9} "
                   f"{m.get('hold_max') or 0:>9} {m.get('locked') or 0:>6}")
    lw = s["lock_waits"]
    out.append(f"\nLock waits: {lw['wait_ms']}ms in all, {lw['minutes']} minute(s) with a wait over 1s or a "
               f"'database is locked', {lw['locked']} locked error(s)")
    out.append(f"Dropped: {s['drops']} (samples or records the recorder could not keep up with)")
    if s["stack_dump"]:
        out.append(f"\nLast SIGUSR1 stack dump ({_clock(s['stack_dump']['ts'])}), tail:")
        out += ["  " + line for line in s["stack_dump"]["text"][-60:]]
    for m in s["memory"]:
        out.append(f"\nMemory top sites ({_clock(m.get('ts'))}, traced {_mb(m.get('traced'))}):")
        out += [f"  {_mb(x['bytes']):>7} {x['count']:>8} {x['where']}" for x in (m.get("top") or [])[:20]]
    return "\n".join(out)


def bundle(folder, since_s=86400, now=None):
    """The support bundle's flight section: the last day's summary, shallow (HQ refuses deep nesting) and small; every
    string still goes through the bundle's redactor."""
    s = summary(folder, since_s, now, stall_limit=5, stack_frames=10)
    hours = {}
    for m in s["minutes"]:
        h = hours.setdefault(int((m["ts"] or 0) // 3600 * 3600), {"ts": int((m["ts"] or 0) // 3600 * 3600),
                                                                   "cpu_max": 0, "rss_max": 0, "lag_max": 0,
                                                                   "wait_max": 0, "locked": 0, "requests": 0})
        h["cpu_max"] = max(h["cpu_max"], m.get("cpu") or 0)
        h["rss_max"] = max(h["rss_max"], m.get("rss") or 0)
        h["lag_max"] = max(h["lag_max"], m.get("lag_max") or 0)
        h["wait_max"] = max(h["wait_max"], m.get("wait_max") or 0)
        h["locked"] += m.get("locked") or 0
        h["requests"] += m.get("requests") or 0
    return {"since": s["since"], "drops": s["drops"], "stalls_total": s["stalls_total"],
            "routes": [{k: r[k] for k in ("route", "n", "errors", "total_ms", "p95", "max_ms", "sql_ms", "wait_ms",
                                          "avg_bytes", "bytes_max")} for r in s["routes"][:10]],
            "slow": [{"ts": r.get("ts"), "route": r.get("route"), "ms": r.get("ms"), "status": r.get("status"),
                      "sql_ms": r.get("sql_ms"), "wait_ms": r.get("wait_ms")} for r in s["slow"][:10]],
            "stalls": [{"ts": st["ts"], "seconds": st["seconds"], "class": st["class"],
                        "usage": json.dumps(st.get("usage") or {}, separators=(",", ":"), sort_keys=True),
                        "threads": [("[loop] " if t.get("loop") else "") + str(t.get("thread")) + ": "
                                    + " < ".join(reversed(t.get("stack") or [])) for t in st["threads"][:6]]}
                       for st in s["stalls"]],
            "hours": sorted(hours.values(), key=lambda h: h["ts"])}
