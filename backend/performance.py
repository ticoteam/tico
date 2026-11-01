"""Computer performance diagnostics (docs/computers.md#performance).

On for every computer unless its operator or an admin turns it off (Settings > Computers). While on, the heartbeat
reply says so and the runner posts a sample every 60 s (runner-rs/src/perf.rs): CPU, load, memory, swap, free disk
and the busiest process names. Numbers and process names only: never command lines, prompts or file contents. Which
turns ran at the time comes from this server's own attempts, not from the runner.

Samples land as one-minute points, kept 48 hours, then ten-minute points, kept 14 days. A computer that stays very
loaded for ten minutes (CPU or memory over 90%, swapping, or a nearly full disk) records an overload with what was
running. That is diagnostics only: Health shows it and nothing acts on it. Every person and bot may read a
computer's performance; the data stays on this server and reaches Tico only when a person attaches a window to a
support ticket (backend/diagnostics.py), redacted like the rest of the bundle.
"""
import json
import time

from fastapi import Request

from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_load_samples(runner_id TEXT NOT NULL, ts INTEGER NOT NULL,
  sampled_at INTEGER NOT NULL, cpu_pct REAL, mem_used INTEGER, mem_total INTEGER, swap_used INTEGER,
  swap_total INTEGER, running_turns INTEGER NOT NULL, PRIMARY KEY(runner_id,ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS attempt_load_snapshots(attempt_id TEXT PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE,
  at_start TEXT, at_end TEXT) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS perf_points(runner_id TEXT NOT NULL, span INTEGER NOT NULL, ts INTEGER NOT NULL,
  n INTEGER NOT NULL, scope TEXT NOT NULL DEFAULT 'host', cpu REAL NOT NULL, cpu_max REAL NOT NULL, load1 REAL,
  mem_used INTEGER, mem_max INTEGER, mem_total INTEGER, swap_used INTEGER, swap_total INTEGER, disk_free INTEGER,
  disk_total INTEGER, active INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(runner_id, span, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS perf_usage(runner_id TEXT NOT NULL, span INTEGER NOT NULL, ts INTEGER NOT NULL,
  kind TEXT NOT NULL, key TEXT NOT NULL, bot TEXT NOT NULL DEFAULT '', harness TEXT NOT NULL DEFAULT '',
  n INTEGER NOT NULL, cpu REAL NOT NULL, cpu_max REAL NOT NULL, mem INTEGER NOT NULL, mem_max INTEGER NOT NULL,
  PRIMARY KEY(runner_id, span, ts, kind, key)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS perf_overloads(id INTEGER PRIMARY KEY, runner_id TEXT NOT NULL, started_at INTEGER NOT NULL,
  last INTEGER NOT NULL, ended INTEGER, reasons TEXT NOT NULL, cpu_max REAL NOT NULL, mem_max REAL NOT NULL,
  runs_max INTEGER NOT NULL DEFAULT 0, bots TEXT NOT NULL DEFAULT '[]', procs TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS perf_overloads_runner ON perf_overloads(runner_id, started_at);
"""
MODES = ("on", "off")
MINUTE, TEN = 60, 600
MINUTE_HOURS = 48               # one-minute points, then ten-minute ones
KEEP_DAYS = 14
SAMPLE_S = 30                   # legacy duration unit: a usage row's n covers n * 30 s
SUSTAINED = 10                  # loaded minutes in a row that make an overload
CPU_HIGH, MEM_HIGH = 90.0, 90.0
SWAP_HIGH, SWAP_MEM = 50.0, 80.0    # swapping: half the swap in use while memory is already 80% used
DISK_LOW = 5.0                  # percent free on the fullest volume
STALE_S = 15 * 60               # an overload whose computer stopped reporting ends where its samples did
RECENT_S = 24 * 3600            # Health shows an overload this long after it ended
MAX_HOURS = KEEP_DAYS * 24
REASON_WORDS = {"cpu": "CPU over 90%", "memory": "memory over 90%", "swap": "swapping", "disk": "disk nearly full"}

OFF = "Performance diagnostics are off for this computer. Turn them back on in Settings > Computers."


def listing(row):
    """The field a computer's row carries in the Operations and Computers listings."""
    keys = row.keys() if hasattr(row, "keys") else row
    mode = row["performance"] if "performance" in keys else "on"
    return {"performance": mode if mode in MODES else "on"}


def on(c, runner_id):
    row = c.execute("SELECT performance FROM runners WHERE id=? AND revoked_at IS NULL", (runner_id,)).fetchone()
    return bool(row) and row["performance"] == "on"


def may_see(who):
    """Every person and bot of the company: performance holds numbers and process names only."""
    return who.role in ("owner", "human", "bot")


def may_switch(auth, who, runner):
    """The off switch: the computer's operator or an admin, the same rule as its screen and restart."""
    from .screen import may_change
    return who.role in ("owner", "human") and may_change(auth, who, runner)


# ------------------------------------------------------------------------------------------------ ingest
def _merge(old_n, old, x):
    """A running mean over `old_n` earlier values plus `x`."""
    return x if old is None or old_n <= 0 else (old * old_n + x) / (old_n + 1)


def _volume(sample):
    """(free, total) of the fullest volume the sample names."""
    disks = [d for d in sample.disk if d.total_bytes > 0]
    if not disks:
        return None, None
    d = min(disks, key=lambda d: d.free_bytes / d.total_bytes)
    return d.free_bytes, d.total_bytes


def ingest(c, runner_id, samples, now=None):
    """Store a batch of samples; returns the number kept. A sample from more than a day away is dropped, and one
    ahead of the server's clock counts as now, so a computer's fast clock cannot hold back the minutes after it."""
    now = int(now or time.time())
    kept, minutes = 0, []
    for s in sorted(samples, key=lambda s: s.at):
        at = min(int(s.at), now)
        if now - at > 86400:
            continue
        kept += 1
        ts = at // MINUTE * MINUTE
        if not minutes or minutes[-1] != ts:
            minutes.append(ts)
        # The busy reading is CPU time actually used. Load average counts runnable and waiting threads, so a
        # box with many agent turns idling on I/O reads far above its cores while it still has room to start more.
        # A retry replaces its minute, never duplicates it; delayed older samples cannot replace a newer one.
        cpu_pct = round(s.cpu, 1)
        c.execute("INSERT INTO runner_load_samples VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(runner_id,ts) DO UPDATE SET "
                  "sampled_at=excluded.sampled_at,cpu_pct=excluded.cpu_pct,mem_used=excluded.mem_used,"
                  "mem_total=excluded.mem_total,swap_used=excluded.swap_used,swap_total=excluded.swap_total,"
                  "running_turns=excluded.running_turns WHERE excluded.sampled_at>=runner_load_samples.sampled_at",
                  (runner_id, ts, at, cpu_pct, s.mem_used, s.mem_total,
                   s.load_swap_used if s.load_swap_used is not None else s.swap_used,
                   s.load_swap_total if s.load_swap_total is not None else s.swap_total, s.active))
        free, total = _volume(s)
        scope = "container_cpu" if s.scope == "container" and s.memory_scope == "host" else s.scope
        used = s.mem_used if s.mem_total else None
        load1 = s.load[0] if s.load else None
        row = c.execute("SELECT * FROM perf_points WHERE runner_id=? AND span=? AND ts=?", (runner_id, MINUTE, ts)).fetchone()
        if row:
            n = row["n"]
            c.execute("UPDATE perf_points SET n=?,scope=?,cpu=?,cpu_max=?,load1=?,mem_used=?,mem_max=?,mem_total=?,"
                      "swap_used=?,swap_total=?,disk_free=?,disk_total=?,active=? WHERE runner_id=? AND span=? AND ts=?",
                      (n + 1, scope, _merge(n, row["cpu"], s.cpu), max(row["cpu_max"], s.cpu),
                       load1 if row["load1"] is None else _merge(n, row["load1"], load1) if load1 is not None else row["load1"],
                       None if used is None else int(_merge(n, row["mem_used"], used)),
                       max(row["mem_max"] or 0, used or 0) or None, s.mem_total or row["mem_total"],
                       s.swap_used if s.swap_total else row["swap_used"], s.swap_total or row["swap_total"],
                       free if free is not None and (row["disk_free"] is None or free < row["disk_free"]) else row["disk_free"],
                       total or row["disk_total"], max(row["active"], s.active), runner_id, MINUTE, ts))
        else:
            c.execute("INSERT INTO perf_points VALUES(?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (runner_id, MINUTE, ts, scope, s.cpu, s.cpu, load1, used, used, s.mem_total or None,
                       s.swap_used if s.swap_total else None, s.swap_total or None, free, total, s.active))
        for p in s.procs:
            _add_usage(c, runner_id, MINUTE, ts, "proc", p.name, "", "", s.interval_s // SAMPLE_S, p.cpu, p.cpu, p.mem, p.mem)
    for ts in sorted(set(minutes)):    # each minute in turn: a backlog's calm minute ends an overload
        detect(c, runner_id, ts)
    c.execute("DELETE FROM runner_load_samples WHERE runner_id=? AND ts<?", (runner_id, now - 48 * 3600))
    return kept


def _add_usage(c, runner_id, span, ts, kind, key, bot, harness, n, cpu, cpu_max, mem, mem_max):
    """Fold `n` samples averaging `cpu`/`mem` into a usage row."""
    old = c.execute("SELECT n,cpu,cpu_max,mem,mem_max FROM perf_usage WHERE runner_id=? AND span=? AND ts=? AND kind=? "
                    "AND key=?", (runner_id, span, ts, kind, key)).fetchone()
    if old:
        total = old["n"] + n
        c.execute("UPDATE perf_usage SET n=?,cpu=?,cpu_max=?,mem=?,mem_max=?,bot=?,harness=? WHERE runner_id=? AND "
                  "span=? AND ts=? AND kind=? AND key=?",
                  (total, (old["cpu"] * old["n"] + cpu * n) / total, max(old["cpu_max"], cpu_max),
                   int((old["mem"] * old["n"] + mem * n) / total), max(old["mem_max"], mem_max), bot, harness,
                   runner_id, span, ts, kind, key))
    else:
        c.execute("INSERT INTO perf_usage VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (runner_id, span, ts, kind, key, bot, harness, n, cpu, cpu_max, int(mem), int(mem_max)))


# ------------------------------------------------------------------------------------------------ simple load
LOAD_FRESH_S = 120


def load_status(c, runner_id, now=None):
    """Numbers only, readable by every company bot. Six consecutive readings span five full minutes.

    A missing minute, a calm reading, a stale sample or diagnostics being off clears the current flag.
    """
    return load_statuses(c, [runner_id], now)[runner_id]


def load_statuses(c, runner_ids, now=None):
    """`load_status` for many computers: one read of their modes, one of their newest readings."""
    now = int(time.time() if now is None else now)
    ids = sorted(set(runner_ids))
    out = {rid: {"latest_sample": None, "overloaded": False} for rid in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    modes = {r["id"]: r["performance"] for r in c.execute(
        f"SELECT id,performance FROM runners WHERE id IN ({marks}) AND revoked_at IS NULL", ids)}
    on_ids = [rid for rid in ids if modes.get(rid) == "on"]
    if on_ids:
        points = _newest_samples(c, on_ids, now - 48 * 3600)
        for rid in on_ids:
            out[rid] = _load_from(points[rid], now)
    return out


def _newest_samples(c, runner_ids, since):
    """The six newest readings of each computer since `since`, newest first. One statement per 200 computers, each
    part a seek to its own (runner_id, ts) key: a computer's 48 hours of minutes are never read to find its six."""
    out = {rid: [] for rid in runner_ids}
    for start in range(0, len(runner_ids), 200):
        part = runner_ids[start:start + 200]
        sql = " UNION ALL ".join("SELECT * FROM (SELECT * FROM runner_load_samples WHERE runner_id=? AND ts>=? "
                                 "ORDER BY ts DESC LIMIT 6)" for _ in part)
        for row in c.execute(sql, [v for rid in part for v in (rid, since)]):
            out[row["runner_id"]].append(row)
    return out


def _load_from(points, now):
    if not points:
        return {"latest_sample": None, "overloaded": False}
    p = points[0]
    fresh = now - p["sampled_at"] <= LOAD_FRESH_S
    latest = {"at": p["sampled_at"], "cpu_pct": p["cpu_pct"],
              "mem_pct": _pct(p["mem_used"], p["mem_total"]),
              "mem_used": p["mem_used"], "mem_total": p["mem_total"],
              "swap_used": p["swap_used"], "swap_total": p["swap_total"],
              "running_turns": p["running_turns"], "stale": not fresh}
    loaded = lambda x: (x["cpu_pct"] or 0) > 90 or (_pct(x["mem_used"], x["mem_total"]) or 0) > 90
    overloaded = (fresh and len(points) == 6
                  and all(points[i]["ts"] - points[i+1]["ts"] == 60 for i in range(5))
                  and points[0]["sampled_at"] - points[-1]["sampled_at"] >= 300
                  and all(loaded(x) for x in points))
    return {"latest_sample": latest, "overloaded": bool(overloaded)}


def stamp_attempt(c, attempt_id, runner_id, phase, snapshot=None):
    """Fresh runner readings when available; older runners use the latest sample, with its original timestamp.

    An absent or stale sample stays unknown. Snapshot history outlives the 48-hour rolling sample window.
    """
    if snapshot is not None and on(c, runner_id):
        value = snapshot.model_dump()
    else:
        latest = load_status(c, runner_id)["latest_sample"] if on(c, runner_id) else None
        value = ({k: latest[k] for k in ("at", "cpu_pct", "mem_pct", "running_turns")}
                 if latest and not latest["stale"] else None)
    column = {"start": "at_start", "end": "at_end"}[phase]
    if value is None:
        c.execute(f"UPDATE attempt_load_snapshots SET {column}=NULL WHERE attempt_id=?", (attempt_id,))
        return
    c.execute(f"INSERT INTO attempt_load_snapshots(attempt_id,{column}) VALUES(?,?) "
              f"ON CONFLICT(attempt_id) DO UPDATE SET {column}=excluded.{column}",
              (attempt_id, json.dumps(value)))


def attempt_snapshots(c, attempt_id):
    from .read_cache import one
    row = one(c, "SELECT at_start,at_end FROM attempt_load_snapshots WHERE attempt_id=?", (attempt_id,))
    return {"load_start": json.loads(row["at_start"]) if row and row["at_start"] else None,
            "load_end": json.loads(row["at_end"]) if row and row["at_end"] else None}


# ------------------------------------------------------------------------------------------------ overloads
def reasons(point):
    """What made a point very loaded: any of cpu, memory, swap, disk."""
    out = []
    if point["cpu"] >= CPU_HIGH:
        out.append("cpu")
    mem = _pct(point["mem_used"], point["mem_total"])
    if mem is not None and mem >= MEM_HIGH:
        out.append("memory")
    swap = _pct(point["swap_used"], point["swap_total"])
    if swap is not None and swap >= SWAP_HIGH and (mem or 0) >= SWAP_MEM:
        out.append("swap")
    if point["disk_total"] and point["disk_free"] is not None and 100 * point["disk_free"] / point["disk_total"] < DISK_LOW:
        out.append("disk")
    return out


def _pct(used, total):
    return 100.0 * used / total if used is not None and total else None


def detect(c, runner_id, upto):
    """Judge the minute at `upto`: open an overload after SUSTAINED loaded minutes in a row; extend it while they
    last; end it on the first minute that is not. The newest minute may still be filling, so it is judged as it stands."""
    points = c.execute("SELECT * FROM perf_points WHERE runner_id=? AND span=? AND ts<=? ORDER BY ts DESC LIMIT ?",
                       (runner_id, MINUTE, upto, SUSTAINED)).fetchall()
    if not points:
        return
    latest = points[0]
    newest = c.execute("SELECT * FROM perf_overloads WHERE runner_id=? ORDER BY id DESC LIMIT 1", (runner_id,)).fetchone()
    # A late minute from before what the newest overload already covers changes nothing.
    if newest and latest["ts"] < (newest["last"] if newest["ended"] is None else newest["ended"]):
        return
    loaded = reasons(latest)
    # The minute that ended it can fill in later (its samples split across posts): loaded after all, it carries on.
    if newest and latest["ts"] == newest["ended"]:
        if not loaded:
            return
        c.execute("UPDATE perf_overloads SET ended=NULL WHERE id=?", (newest["id"],))
        newest = dict(newest) | {"ended": None}
    open_ = newest if newest and newest["ended"] is None else None
    if open_:
        if loaded:
            why = sorted(set(json.loads(open_["reasons"])) | set(loaded))
            _update(c, open_["id"], runner_id, open_["started_at"], latest["ts"], why)
        elif latest["ts"] > open_["last"]:
            c.execute("UPDATE perf_overloads SET ended=? WHERE id=?", (latest["ts"], open_["id"]))
        return
    if len(points) < SUSTAINED or points[0]["ts"] - points[-1]["ts"] > (SUSTAINED - 1) * MINUTE:
        return
    all_reasons = [reasons(p) for p in points]
    if all(all_reasons):
        started = points[-1]["ts"]
        cur = c.execute("INSERT INTO perf_overloads(runner_id,started_at,last,reasons,cpu_max,mem_max) VALUES(?,?,?,?,0,0)",
                        (runner_id, started, latest["ts"], "[]"))
        _update(c, cur.lastrowid, runner_id, started, latest["ts"], sorted({r for rs in all_reasons for r in rs}))
        H.event(c, "system", "runner.overloaded", runner_id, {"reasons": sorted({r for rs in all_reasons for r in rs})})


def _update(c, oid, runner_id, started, last, why):
    """An overload's peaks and what ran during it."""
    peak = c.execute("SELECT max(cpu_max),max(100.0*mem_max/mem_total),max(active) FROM perf_points WHERE runner_id=? "
                     "AND span=? AND ts>=? AND ts<=?", (runner_id, MINUTE, started, last)).fetchone()
    bots = ran(c, runner_id, started, last + MINUTE)[:8]
    procs = c.execute("SELECT key name,sum(cpu*n)/sum(n) cpu,max(mem_max) mem FROM perf_usage WHERE runner_id=? AND "
                      "span=? AND ts>=? AND ts<=? AND kind='proc' GROUP BY key ORDER BY cpu DESC LIMIT 6",
                      (runner_id, MINUTE, started, last)).fetchall()
    c.execute("UPDATE perf_overloads SET last=?,reasons=?,cpu_max=?,mem_max=?,runs_max=?,bots=?,procs=? "
              "WHERE id=?",
              (last, json.dumps(why), round(peak[0] or 0, 1), round(peak[1] or 0, 1), peak[2] or 0,
               json.dumps(bots),
               json.dumps([{"name": p["name"], "cpu": round(p["cpu"], 1), "mem": p["mem"]} for p in procs]), oid))


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".000000Z"     # sorts with H.now() stamps


def _runs(c, runner_id, start, end, limit=500):
    """The turns this computer ran that overlap [start, end), oldest first: this server's own record. A busy window
    keeps its newest `limit`."""
    # A leased turn counts from its lease: the runner reports `started_at` once its harness is up. Only the turns still
    # open or finished since `start` are read (attempts_runner_finished, two index ranges): an open overload runs this
    # under the write lock on every post, and visiting each turn the computer ever ran held that lock ~100 s on a slow
    # disk (Dev 249).
    return c.execute("SELECT a.id,a.bot,coalesce(a.started_at,a.created_at) started_at,a.finished_at,a.state,t.model FROM attempts a "
                     "LEFT JOIN turns t ON t.id=a.id WHERE a.rowid IN (SELECT rowid FROM attempts WHERE runner_id=? "
                     "AND finished_at>=? UNION ALL SELECT rowid FROM attempts WHERE runner_id=? AND finished_at IS NULL) AND "
                     "coalesce(a.started_at,a.created_at)<? ORDER BY coalesce(a.started_at,a.created_at) DESC LIMIT ?",
                     (runner_id, _iso(start), runner_id, _iso(end), limit)).fetchall()[::-1]


def ran(c, runner_id, start, end):
    """Who ran during [start, end): each bot and model with its number of turns, most turns first."""
    counts = {}
    for r in _runs(c, runner_id, start, end):
        key = (r["bot"].removeprefix("bot:"), r["model"] or "")
        counts[key] = counts.get(key, 0) + 1
    return [{"bot": b, "model": m, "runs": n} for (b, m), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def overload_view(row):
    return {"started_at": row["started_at"], "ended": row["ended"], "last": row["last"],
            "minutes": (( row["ended"] or row["last"] + MINUTE) - row["started_at"]) // MINUTE,
            "reasons": json.loads(row["reasons"]), "cpu_max": row["cpu_max"], "mem_max": row["mem_max"],
            "runs_max": row["runs_max"], "bots": json.loads(row["bots"]), "procs": json.loads(row["procs"])}


def recent_overload(c, runner_id, within=RECENT_S, now=None):
    """The newest overload still open, or ended within `within` seconds; None otherwise."""
    return recent_overloads(c, [runner_id], within, now)[runner_id]


def recent_overloads(c, runner_ids, within=RECENT_S, now=None):
    """`recent_overload` for many computers, in one statement."""
    now = int(now or time.time())
    ids = sorted(set(runner_ids))
    out = {rid: None for rid in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    for row in c.execute(f"SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY runner_id ORDER BY id DESC) AS n "
                         f"FROM perf_overloads WHERE runner_id IN ({marks}) AND (ended IS NULL OR ended>=?)) WHERE n=1",
                         (*ids, now - within)):
        out[row["runner_id"]] = overload_view(row)
    return out


def _who_ran(bots, label=lambda b: b):
    names = []
    for b in bots[:3]:
        what = label(b["bot"])
        if b.get("runs", 1) > 1:
            what += f" x{b['runs']}"
        names.append(what)
    return ", ".join(names)


def describe(o, label=lambda b: b):
    """One line: when, why and what ran."""
    why = " and ".join(REASON_WORDS.get(r, r) for r in o["reasons"]) or "very loaded"
    words = f"{why} for {o['minutes']} minutes" + ("" if o["ended"] else ", still going")
    ran = _who_ran(o["bots"], label)
    if ran:
        words += f"; running: {ran}"
    if o["procs"]:
        words += "; busiest processes: " + ", ".join(p["name"] for p in o["procs"][:3])
    return words


def summary(overloads, days, label=lambda b: b):
    """'Overloaded 6 times in the last 7 days (CPU over 90%); each time 3 or more runs were active, ...'."""
    window = f"the last {days} day{'s' if days != 1 else ''}" if days >= 1 else "this window"
    if not overloads:
        return f"No overloads in {window}."
    n = len(overloads)
    counts = {}
    for o in overloads:
        for r in o["reasons"]:
            counts[r] = counts.get(r, 0) + 1
    main = sorted(counts, key=lambda r: (-counts[r], r))[:2]
    text = (f"Overloaded {'once' if n == 1 else f'{n} times'} in {window} ("
            + ", ".join(REASON_WORDS.get(r, r) for r in main) + ")")
    if n == 1:
        return text + ": " + describe(overloads[0], label) + "."
    common = []
    least = min(o["runs_max"] for o in overloads)
    if least >= 2:
        common.append(f"{least} or more runs were active")
    sets = [{b["bot"] for b in o["bots"]} for o in overloads]
    shared = sorted(set.intersection(*sets)) if sets else []
    if shared:
        common.append(", ".join(label(b) for b in shared[:3]) + " " + ("was" if len(shared[:3]) == 1 else "were") + " running")
    procs = [{p["name"] for p in o["procs"]} for o in overloads]
    busy = sorted(set.intersection(*procs)) if procs else []
    if busy:
        common.append(", ".join(busy[:3]) + " " + ("was" if len(busy[:3]) == 1 else "were") + " among the busiest processes")
    return text + ("; each time " + " and ".join(common) if common else "; no single cause ran every time") + "."


# ------------------------------------------------------------------------------------------------ reading
def _window(c, runner_id, since, span):
    """Points from both resolutions at `since` or later, folded into `span`-second buckets."""
    buckets = {}
    for p in c.execute("SELECT * FROM perf_points WHERE runner_id=? AND ts>=? ORDER BY ts", (runner_id, since)):
        ts = p["ts"] // span * span
        b = buckets.get(ts)
        if b is None:
            buckets[ts] = dict(p) | {"ts": ts}
            continue
        n, m = b["n"], p["n"]
        mean = lambda a, x: x if a is None else a if x is None else (a * n + x * m) / (n + m)
        b.update(cpu=mean(b["cpu"], p["cpu"]), cpu_max=max(b["cpu_max"], p["cpu_max"]), load1=mean(b["load1"], p["load1"]),
                 mem_used=mean(b["mem_used"], p["mem_used"]), mem_max=max(b["mem_max"] or 0, p["mem_max"] or 0),
                 mem_total=p["mem_total"] or b["mem_total"], swap_used=p["swap_used"], swap_total=p["swap_total"] or b["swap_total"],
                 disk_free=min(x for x in (b["disk_free"], p["disk_free"], 1 << 62) if x is not None),
                 disk_total=p["disk_total"] or b["disk_total"], active=max(b["active"], p["active"]), n=n + m)
    return [buckets[k] for k in sorted(buckets)]


COLUMNS = ["ts", "cpu", "cpu_max", "mem", "mem_max", "load1", "swap", "disk_free", "active"]


def _shown(bots, access, limit=None):
    """`bots` with those the reader may not see folded into one unnamed row ("another bot"), which `limit` (how many
    named bots to keep) never cuts."""
    out, hidden = [], 0
    for b in bots:
        if access is None or (access.get(b["bot"]) or {}).get("see"):
            out.append(b)
        else:
            hidden += b.get("runs", 1)
    return out[:limit] + ([{"bot": "", "model": "", "runs": hidden}] if hidden else [])


def view(c, runner_id, hours, now=None, label=lambda b: b, access=None):
    """What the Performance page and a support attachment show for the last `hours`. With `access` (the reader's
    bot_accesses), a bot the reader may not see is unnamed, and a turn's id shows only to a reader of its bot."""
    now = int(now or time.time())
    hours = max(1, min(int(hours), MAX_HOURS))
    since = now - hours * 3600
    span = MINUTE if hours <= MINUTE_HOURS else TEN
    points = []
    for p in _window(c, runner_id, since, span):
        mem = _pct(p["mem_used"], p["mem_total"])
        mem_max = _pct(p["mem_max"], p["mem_total"])
        swap = _pct(p["swap_used"], p["swap_total"])
        points.append([p["ts"], round(p["cpu"], 1), round(p["cpu_max"], 1), None if mem is None else round(mem, 1),
                       None if mem_max is None else round(mem_max, 1),
                       None if p["load1"] is None else round(p["load1"], 2), None if swap is None else round(swap, 1),
                       p["disk_free"], p["active"]])
    latest = c.execute("SELECT mem_total,swap_total,disk_total,scope FROM perf_points WHERE runner_id=? ORDER BY ts DESC "
                       "LIMIT 1", (runner_id,)).fetchone()
    seen = lambda slug, right="see": access is None or bool((access.get(slug) or {}).get(right))
    runs = []
    for r in _runs(c, runner_id, since, now + 1):
        slug = r["bot"].removeprefix("bot:")
        shown = seen(slug)
        runs.append({"attempt": r["id"] if seen(slug, "read") else None, "bot": label(slug) if shown else "",
                     "model": (r["model"] or "") if shown else "", "started_at": r["started_at"], "finished_at": r["finished_at"],
                     "state": r["state"]})
    name = lambda b: label(b) if b else ""
    bots = [b | {"bot": name(b["bot"])} for b in _shown(ran(c, runner_id, since, now + 1), access, 20)]
    procs = []
    for r in c.execute("SELECT key,sum(cpu*n) cpu_n,max(cpu_max) cpu_max,max(mem_max) mem_max FROM perf_usage "
                       "WHERE runner_id=? AND ts>=? AND kind='proc' GROUP BY key ORDER BY cpu_n DESC LIMIT 15",
                       (runner_id, since)):
        procs.append({"name": r["key"], "cpu_minutes": round(r["cpu_n"] * SAMPLE_S / 60 / 100, 1),
                      "cpu_max": round(r["cpu_max"], 1), "mem_max": r["mem_max"]})
    overloads = [overload_view(o) for o in c.execute(
        "SELECT * FROM perf_overloads WHERE runner_id=? AND coalesce(ended,last)>=? ORDER BY started_at", (runner_id, since))]
    for o in overloads:
        o["bots"] = [b | {"bot": name(b["bot"])} for b in _shown(o["bots"], access)]
    return {"hours": hours, "span": span, "since": since, "until": now, "columns": COLUMNS, "points": points,
            "mem_total": latest["mem_total"] if latest else None, "swap_total": latest["swap_total"] if latest else None,
            "disk_total": latest["disk_total"] if latest else None, "scope": latest["scope"] if latest else None,
            "runs": runs, "busiest": {"bots": bots, "processes": procs}, "overloads": overloads,
            "summary": summary(overloads, hours / 24, lambda b: b or "another bot")}


# ------------------------------------------------------------------------------------------------ retention
def sweep(store, now=None):
    """Minutes older than 48 hours become ten-minute points; everything older than 14 days goes. Each hour of
    minutes is its own short transaction."""
    now = int(now or time.time())
    cutoff = now - MINUTE_HOURS * 3600
    while True:
        with store.transaction() as c:
            row = c.execute("SELECT min(ts) FROM perf_points WHERE span=? AND ts<?", (MINUTE, cutoff)).fetchone()
            if row[0] is None:
                break
            hour = row[0] // 3600 * 3600
            end = min(hour + 3600, cutoff)
            for runner_id in [r[0] for r in c.execute("SELECT DISTINCT runner_id FROM perf_points WHERE span=? AND ts>=? "
                                                      "AND ts<?", (MINUTE, hour, end))]:
                _roll(c, runner_id, hour, end)
            c.execute("DELETE FROM perf_points WHERE span=? AND ts>=? AND ts<?", (MINUTE, hour, end))
            c.execute("DELETE FROM perf_usage WHERE span=? AND ts>=? AND ts<?", (MINUTE, hour, end))
    old = now - KEEP_DAYS * 86400
    with store.transaction() as c:
        c.execute("DELETE FROM runner_load_samples WHERE ts<?", (cutoff,))
        c.execute("DELETE FROM perf_points WHERE ts<?", (old,))
        c.execute("DELETE FROM perf_usage WHERE ts<?", (old,))
        c.execute("DELETE FROM perf_overloads WHERE coalesce(ended,last)<?", (old,))
        # A computer that stopped reporting mid-overload: it ends where its samples did.
        c.execute("UPDATE perf_overloads SET ended=last+? WHERE ended IS NULL AND last<?", (MINUTE, now - STALE_S))
        gone = "SELECT id FROM runners WHERE revoked_at IS NOT NULL"
        for table in ("perf_points", "perf_usage", "perf_overloads", "runner_load_samples"):
            c.execute(f"DELETE FROM {table} WHERE runner_id IN ({gone})")


def _roll(c, runner_id, start, end):
    for p in _window_between(c, runner_id, start, end):
        old = c.execute("SELECT * FROM perf_points WHERE runner_id=? AND span=? AND ts=?", (runner_id, TEN, p["ts"])).fetchone()
        if old:                                         # the earlier part of the ten minutes, rolled by an earlier sweep
            n, m = old["n"], p["n"]
            mean = lambda a, x: x if a is None else a if x is None else (a * n + x * m) / (n + m)
            p.update(cpu=mean(old["cpu"], p["cpu"]), cpu_max=max(old["cpu_max"], p["cpu_max"]),
                     load1=mean(old["load1"], p["load1"]), mem_used=mean(old["mem_used"], p["mem_used"]),
                     mem_max=max(old["mem_max"] or 0, p["mem_max"] or 0),
                     disk_free=min(x for x in (old["disk_free"], p["disk_free"], 1 << 62) if x is not None),
                     active=max(old["active"], p["active"]), n=n + m)
        c.execute("INSERT OR REPLACE INTO perf_points VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (runner_id, TEN, p["ts"], p["n"], p["scope"], p["cpu"], p["cpu_max"], p["load1"],
                   None if p["mem_used"] is None else int(p["mem_used"]), p["mem_max"] or None, p["mem_total"],
                   p["swap_used"], p["swap_total"], None if p["disk_free"] == 1 << 62 else p["disk_free"],
                   p["disk_total"], p["active"]))
    for u in c.execute("SELECT ts/? * ? ts,kind,key,max(bot) bot,max(harness) harness,sum(n) n,sum(cpu*n)/sum(n) cpu,"
                       "max(cpu_max) cpu_max,sum(mem*n)/sum(n) mem,max(mem_max) mem_max FROM perf_usage WHERE runner_id=? "
                       "AND span=? AND ts>=? AND ts<? GROUP BY ts/?,kind,key",
                       (TEN, TEN, runner_id, MINUTE, start, end, TEN)).fetchall():
        _add_usage(c, runner_id, TEN, u["ts"], u["kind"], u["key"], u["bot"], u["harness"], u["n"], u["cpu"],
                   u["cpu_max"], u["mem"], u["mem_max"])


def _window_between(c, runner_id, start, end):
    rows = c.execute("SELECT * FROM perf_points WHERE runner_id=? AND span=? AND ts>=? AND ts<? ORDER BY ts",
                     (runner_id, MINUTE, start, end)).fetchall()
    buckets = {}
    for p in rows:
        ts = p["ts"] // TEN * TEN
        b = buckets.get(ts)
        if b is None:
            buckets[ts] = dict(p) | {"ts": ts}
            continue
        n, m = b["n"], p["n"]
        mean = lambda a, x: x if a is None else a if x is None else (a * n + x * m) / (n + m)
        b.update(cpu=mean(b["cpu"], p["cpu"]), cpu_max=max(b["cpu_max"], p["cpu_max"]), load1=mean(b["load1"], p["load1"]),
                 mem_used=mean(b["mem_used"], p["mem_used"]), mem_max=max(b["mem_max"] or 0, p["mem_max"] or 0),
                 mem_total=p["mem_total"] or b["mem_total"], swap_used=p["swap_used"],
                 swap_total=p["swap_total"] or b["swap_total"],
                 disk_free=min(x for x in (b["disk_free"], p["disk_free"], 1 << 62) if x is not None),
                 disk_total=p["disk_total"] or b["disk_total"], active=max(b["active"], p["active"]), n=n + m)
    return [buckets[k] for k in sorted(buckets)]


def erase(c, runner_id):
    c.execute("DELETE FROM attempt_load_snapshots WHERE attempt_id IN (SELECT id FROM attempts WHERE runner_id=?)",
              (runner_id,))
    for table in ("perf_points", "perf_usage", "perf_overloads", "runner_load_samples"):
        c.execute(f"DELETE FROM {table} WHERE runner_id=?", (runner_id,))


# ------------------------------------------------------------------------------------------------ routes
def install(app, store, auth, mutate, execution):
    from . import models as M

    def computer(c, rid):
        return c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()

    @app.post("/api/v2/runners/performance")
    def report(request: Request, body: M.PerformanceBatch):
        """A runner's performance samples, while its computer has them on (409 when off: the runner drops them)."""
        who = request.state.identity

        def work(c):
            runner = execution.runner(c, who)
            if not runner:
                raise Problem("forbidden", "This computer is no longer registered", 403)
            if runner["performance"] != "on":
                raise Problem("performance_off", OFF, 409)
            return {"kept": ingest(c, runner["id"], body.samples)}
        return store.write(who, work)   # a poll: no idempotency record

    @app.post("/api/v2/runners/{rid}/performance")
    def performance_mode(request: Request, rid: str, body: M.PerformanceMode):
        """Turn performance diagnostics off (or back on) for this computer; they are on by default. Its operator or an
        admin. Off stops sampling; what was recorded stays until it ages out (14 days) or is deleted."""
        who = request.state.identity

        def work(c):
            runner = computer(c, rid)
            if not runner:
                raise Problem("not_found", "Computer not found", 404)
            if not may_switch(auth, who, runner):
                raise Problem("forbidden", "You cannot change this computer", 403)
            c.execute("UPDATE runners SET performance=? WHERE id=?", (body.mode, rid))
            if body.mode == "off":      # no more samples will end it, so Health stops saying "overloaded now"
                c.execute("UPDATE perf_overloads SET ended=last+? WHERE runner_id=? AND ended IS NULL", (MINUTE, rid))
            H.event(c, who.actor, "runner.performance", rid, {"mode": body.mode})
            # The heartbeat reply carries the switch: a `performance` event asks the runner for one now.
            c.execute("INSERT INTO changes(topic,kind,subject_id,at) VALUES('runner','performance',?,?)", (rid, H.now()))
            return {"performance": body.mode}
        return mutate(request, body, work)

    @app.delete("/api/v2/runners/{rid}/performance")
    def performance_erase(request: Request, rid: str):
        """Delete this computer's recorded performance history now. Its operator or an admin."""
        who = request.state.identity

        def work(c):
            runner = computer(c, rid)
            if not runner:
                raise Problem("not_found", "Computer not found", 404)
            if not may_switch(auth, who, runner):
                raise Problem("forbidden", "You cannot change this computer", 403)
            erase(c, rid)
            H.event(c, who.actor, "runner.performance_erased", rid, {})
            return {"erased": True}
        return store.write(who, work)

    def named(c, name):
        """A registered computer by id or label (any case). Every reader of this route may read every registered
        computer, so the computer list's narrower scope does not apply. An unknown name and a removed computer's get the
        same answer."""
        runner = computer(c, name)
        if runner:
            return runner
        hit = c.execute("SELECT * FROM runners WHERE revoked_at IS NULL AND lower(trim(label))=lower(trim(?))",
                        (name,)).fetchall()
        if len(hit) != 1:
            raise Problem("not_found", f"No computer named {name!r}" + ("; more than one has that label, use its id"
                                                                       if hit else "; hub computer list names them"), 404)
        return hit[0]

    @app.get("/api/v2/runners/{rid}/performance")
    def performance_view(request: Request, rid: str, hours: int = 24):
        """The computer's performance over the last `hours` (1 to 336): CPU, memory, load, swap and disk points, the
        turns that ran, the bots with the most turns and the busiest processes, overloads and a summary. Every person
        and bot of the company; `rid` is the computer's id or its label."""
        who = request.state.identity
        if not may_see(who):     # before the lookup, so an outsider learns nothing about which names exist
            raise Problem("forbidden", "A person or bot of this company reads a computer's performance", 403)
        with store.read() as c:
            runner = named(c, rid)
            return {"computer": {"id": runner["id"], "label": runner["label"]}, **listing(runner),
                    "may_switch": may_switch(auth, who, runner),
                    **view(c, runner["id"], hours, access=auth.bot_accesses(c, who))}
