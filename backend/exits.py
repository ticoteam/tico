"""Browser use through a residential exit: a person's own computer carries chosen bots' traffic to chosen sites.

- **The computer's Exit setting** (`runners.exit_json`): NULL is off; `{"bots": [...]}` is on for those bots. Only its
  operator or an admin changes it (screen.may_change), and only on a runner that offers `exit_v1`. The heartbeat
  answer's `exit` tells the runner to keep its exit link open (runner-rs/src/exit_link.rs).
- **A bot's site list** (`bot_sites`): host names, exact (`shop.acme.example`) or one wildcard label in front
  (`*.acme.example`, every name under it, not the name itself), each with `exit` `direct` (the default) or
  `computer:<runner id>`. The kind prefix leaves room for `provider:<id>` (a paid static ISP address) without a new
  column; nothing reads one yet, so `parse_exit` refuses it. Only listed sites ever go through an exit.
- **Streams.** A turn's runner gives it a proxy on 127.0.0.1 (runner-rs/src/exit_proxy.rs) that sends listed hosts,
  as CONNECT-style streams over one websocket, to the relay (relay-rs/src/exit.rs), and everything else direct. The
  relay asks `authorize` here for every stream (`open`: the run is on that runner and live, the host is on its bot's
  list with this exit, the exit still takes the bot, the port is 80 or 443), then forwards it to the exit computer's
  websocket; the exit runner resolves the name and refuses private, loopback, link-local, CGNAT and metadata
  addresses after resolving it. Nothing here falls back to direct: a refusal is a refusal.
- **Offline.** An exit with no link open at the relay refuses the stream (`exit_offline`); the relay's next `report`
  puts the run's task on a card, `exit:<runner id>` (backend/reconnects.py), so it waits with "waiting for exit
  computer <name>" and is woken once when that computer's link comes back (`attach`).
- **History** (`exit_sessions`): one row per run, host, port and exit, summed from the relay's reports: streams,
  bytes each way, first and last use, and refusals. The relay batches its counts (one report every two seconds while
  anything moved), so a page with fifty connections costs one write, not a hundred.
"""
import json
import re
import threading
from urllib.parse import urlparse

from fastapi import Request

from .store import H, Problem

CAPABILITY = "exit_v1"
PRIVATE_PATH = "/_tico/exit/v1/authorize"
WS_PATH = "/api/v2/runners/me/exit/ws"
PORTS = (80, 443)
MAX_SITES = 200
MAX_BOTS = 200
# Open streams one bot may hold through exits at once; the relay enforces it (relay-rs/src/exit.rs).
STREAMS_PER_BOT = 32
HISTORY_PAGE = 100
# The relay's report body cap: 1,024 usage + 256 offline rows of a few hundred bytes each.
REPORT_BYTES = 512 * 1024
DIRECT = "direct"
KINDS = ("computer",)          # later: "provider" (a paid static ISP address)
UNAVAILABLE = "This computer's runner can't be an exit. Update it first."

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_sites(
 bot TEXT NOT NULL, host TEXT NOT NULL, exit TEXT NOT NULL DEFAULT 'direct', created TEXT NOT NULL,
 updated TEXT NOT NULL, updated_by TEXT, PRIMARY KEY(bot, host));
CREATE TABLE IF NOT EXISTS exit_sessions(
 id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, bot TEXT NOT NULL, task_id TEXT, host TEXT NOT NULL,
 port INTEGER NOT NULL, exit TEXT NOT NULL, exit_name TEXT NOT NULL, origin_runner TEXT NOT NULL,
 started TEXT NOT NULL, updated TEXT NOT NULL, ended TEXT, streams INTEGER NOT NULL DEFAULT 0,
 open_streams INTEGER NOT NULL DEFAULT 0, bytes_up INTEGER NOT NULL DEFAULT 0, bytes_down INTEGER NOT NULL DEFAULT 0,
 refused INTEGER NOT NULL DEFAULT 0, last_error TEXT, UNIQUE(attempt_id, host, port, exit));
CREATE INDEX IF NOT EXISTS exit_sessions_bot ON exit_sessions(bot, started);
CREATE INDEX IF NOT EXISTS exit_sessions_exit ON exit_sessions(exit, started);
CREATE INDEX IF NOT EXISTS exit_sessions_open ON exit_sessions(ended) WHERE ended IS NULL;
"""

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
# When each exit's link last opened at the relay (this process's relay: it starts and stops with it), so an offline
# report that crossed a reconnect opens no card for a computer already back.
_attached = {}
_attached_lock = threading.Lock()


def ensure(c):
    c.executescript(SCHEMA)
    H.add_column(c, "runners", "exit_json", "TEXT")


# ------------------------------------------------------------------------------------------------ names
def normalize_host(text):
    """A site as stored: lower case, IDNA (punycode), no trailing dot, `*.` allowed only as the whole first label,
    at least two labels, never an IP address. A pasted URL is read for its host. Returns None when it is no name."""
    value = str(text or "").strip().lower()
    if "://" in value:
        value = urlparse(value).hostname or ""
    value = value.rstrip(".")
    wildcard = value.startswith("*.")
    name = value[2:] if wildcard else value
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = name.split(".")
    if (len(name) > 253 or len(labels) < 2 or not all(_LABEL.match(label) for label in labels)
            or labels[-1].isdigit()):
        return None
    return ("*." if wildcard else "") + name


def matches(pattern, host):
    """Whether `host` (already normalized, no wildcard) is the site `pattern` names."""
    if pattern.startswith("*."):
        return host.endswith(pattern[1:])
    return host == pattern


def route(sites, host):
    """The site entry that decides `host`: the exact name first, else the longest wildcard that covers it, or None.
    `sites` is [{"host", "exit"}]; the runner's proxy decides the same way (runner-rs/src/exit_proxy.rs `route`)."""
    exact = next((s for s in sites if s["host"] == host), None)
    if exact:
        return exact
    covering = [s for s in sites if s["host"].startswith("*.") and matches(s["host"], host)]
    return max(covering, key=lambda s: len(s["host"])) if covering else None


def parse_exit(value):
    """("direct", None) or ("computer", <runner id>) for a stored or requested exit; None for anything else."""
    value = str(value or DIRECT).strip()
    if value == DIRECT:
        return DIRECT, None
    kind, _, ident = value.partition(":")
    if kind in KINDS and ident and len(ident) <= 128:
        return kind, ident
    return None


# ------------------------------------------------------------------------------------------------ the exit setting
def setting(row):
    """The computer's Exit setting: {"on", "bots"}."""
    keys = row.keys() if hasattr(row, "keys") else row
    raw = H._json(row["exit_json"], None) if "exit_json" in keys else None
    bots = [b for b in (raw or {}).get("bots") or [] if isinstance(b, str)] if isinstance(raw, dict) else []
    return {"on": isinstance(raw, dict), "bots": bots}


def available(row):
    """Whether the computer's last heartbeat offered to be an exit."""
    return bool(row) and CAPABILITY in set(H._json(row["capabilities_json"], []) or [])


def listing(row):
    """The fields a computer's row carries in the Operations and Computers listings."""
    return {"exit": setting(row), "exit_available": available(row)}


def serving(c, runner_id):
    """Whether this computer should keep its exit link open: on, for at least one bot (the heartbeat answer)."""
    row = c.execute("SELECT exit_json FROM runners WHERE id=? AND revoked_at IS NULL", (runner_id,)).fetchone()
    return bool(row) and bool(setting(row)["bots"])


def takes(row, bot):
    """Whether this computer carries `bot`'s traffic now: not revoked, Exit on, the bot on its list."""
    return bool(row) and not row["revoked_at"] and bot in setting(row)["bots"]


def computer_label(c, runner_id):
    row = c.execute("SELECT label FROM runners WHERE id=?", (runner_id,)).fetchone()
    return row["label"] if row else runner_id


# ------------------------------------------------------------------------------------------------ site lists
def sites(c, bot):
    return H._rows(c.execute("SELECT host,exit,updated FROM bot_sites WHERE bot=? ORDER BY host", (bot,)))


def exits_for(c, bot):
    """The computers that take `bot`'s traffic now: the choices its site list offers besides direct."""
    rows = c.execute("SELECT id,label,exit_json,revoked_at FROM runners WHERE exit_json IS NOT NULL "
                     "AND revoked_at IS NULL ORDER BY label").fetchall()
    return [{"exit": f"computer:{r['id']}", "name": r["label"]} for r in rows if takes(r, bot)]


def site_view(c, bot):
    names = {e["exit"]: e["name"] for e in exits_for(c, bot)}
    out = []
    for s in sites(c, bot):
        parsed = parse_exit(s["exit"]) or (None, None)
        name = names.get(s["exit"]) or (computer_label(c, parsed[1]) if parsed[0] == "computer" else "")
        out.append({"host": s["host"], "exit": s["exit"], "exit_name": name,
                    "exit_takes_bot": s["exit"] == DIRECT or s["exit"] in names})
    return {"sites": out, "exits": [{"exit": k, "name": v} for k, v in names.items()]}


def replace_sites(c, actor, bot, entries):
    """Replace the bot's whole site list. Every host must be a name, at most once; an exit other than direct must be a
    computer that takes this bot now (both people agree: the computer's operator lists the bot, the bot's manager
    picks the computer). Returns the new list."""
    if len(entries) > MAX_SITES:
        raise Problem("validation", f"A bot lists at most {MAX_SITES} sites", 422)
    allowed = {e["exit"] for e in exits_for(c, bot)}
    seen, rows = set(), []
    for entry in entries:
        host = normalize_host(entry.host)
        if not host:
            raise Problem("validation", f"{entry.host!r} is not a site name (like shop.acme.example or *.acme.example)", 422)
        if host in seen:
            raise Problem("validation", f"{host} is listed twice", 422)
        seen.add(host)
        exit_ = (entry.exit or DIRECT).strip()
        if parse_exit(exit_) is None:
            raise Problem("validation", f"Unknown exit {exit_!r}: use direct or computer:<id>", 422)
        if exit_ != DIRECT and exit_ not in allowed:
            raise Problem("exit_not_allowed", f"{computer_label(c, exit_.partition(':')[2])} is not an exit for this bot. "
                                              "Its owner turns it on for the bot first.", 409)
        rows.append((bot, host, exit_))
    before = {(s["host"], s["exit"]) for s in sites(c, bot)}
    now = H.now()
    c.execute("DELETE FROM bot_sites WHERE bot=?", (bot,))
    c.executemany("INSERT INTO bot_sites(bot,host,exit,created,updated,updated_by) VALUES(?,?,?,?,?,?)",
                  [(b, h, e, now, now, actor) for b, h, e in rows])
    after = {(h, e) for _, h, e in rows}
    if before != after:
        H.event(c, actor, "bot.sites", bot, {"sites": [{"host": h, "exit": e} for h, e in sorted(after)]})
    return site_view(c, bot)


def routes(c, bot):
    """What a turn's proxy routes by: the bot's whole site list (direct entries too, since an exact direct name beats a
    wildcard through an exit), or nothing when no entry names an exit. The claim hands it to the run as `exit_routes`
    (backend/execution.py), so a run that has any always starts with its proxy."""
    listed = sites(c, bot)
    if not any((parse_exit(s["exit"]) or (DIRECT,))[0] != DIRECT for s in listed):
        return []
    out = []
    for s in listed:
        parsed = parse_exit(s["exit"])
        out.append({"host": s["host"], "exit": s["exit"],
                    "name": computer_label(c, parsed[1]) if parsed and parsed[0] != DIRECT else ""})
    return out


# ------------------------------------------------------------------------------------------------ the relay's questions
def _attempt(c, aid):
    """(attempt row, task id or None) for a run, from the message that started it."""
    row = c.execute("SELECT a.id,a.bot,a.runner_id,a.state,m.refs_json,cv.task_id AS conversation_task "
                    "FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
                    "LEFT JOIN conversations cv ON cv.id=m.conversation_id WHERE a.id=?", (aid,)).fetchone()
    if not row:
        return None, None
    task = H.message_task_id({"refs": H._json(row["refs_json"], {}) or {}}, {"task_id": row["conversation_task"]})
    return row, task


def refuse(code, detail):
    return {"allowed": False, "code": code, "detail": detail}


def decide(c, runner_id, aid, host, port):
    """One stream's verdict (`open`), from a read: {"allowed": True, "bot", "task", "exit", "exit_runner", "name",
    "limit"} or {"allowed": False, "code", "detail"}. Whether the exit's link is open is the relay's to check."""
    attempt, task = _attempt(c, aid)
    if not attempt or attempt["runner_id"] != runner_id:
        return refuse("forbidden", "That run is not on this computer")
    if attempt["state"] not in ("leased", "running"):
        return refuse("stale_lease", "That run has ended")
    name = normalize_host(host)
    if not name or name.startswith("*."):
        return refuse("not_listed", f"{host} is not a site name")
    if port not in PORTS:
        return refuse("port", "Exits carry web traffic only (ports 80 and 443)")
    bot = attempt["bot"]
    entry = route(sites(c, bot), name)
    parsed = parse_exit(entry["exit"]) if entry else None
    if not parsed or parsed[0] == DIRECT:
        return refuse("not_listed", f"{name} is not on this bot's site list for an exit")
    exit_row = c.execute("SELECT id,label,operator,exit_json,revoked_at FROM runners WHERE id=?", (parsed[1],)).fetchone()
    if not takes(exit_row, bot):
        label = exit_row["label"] if exit_row else parsed[1]
        return refuse("exit_not_allowed", f"{label} does not carry this bot's traffic now")
    return {"allowed": True, "bot": bot, "task": task, "host": name, "exit": entry["exit"], "exit_runner": exit_row["id"],
            "name": exit_row["label"], "limit": STREAMS_PER_BOT}


def attach(store, auth, token):
    """A runner opens its exit link: who it is and whether it serves as an exit now. A serving exit coming back fixes
    every card waiting for it, so those tasks wake once (backend/reconnects.py)."""
    who = auth.authenticate({"authorization": token}, WS_PATH, "GET")
    if who.role != "runner" or not who.runner_id:
        raise Problem("forbidden", "A registered computer is required", 403)
    with store.read() as c:
        row = c.execute("SELECT id,label,exit_json,revoked_at FROM runners WHERE id=? AND revoked_at IS NULL",
                        (who.runner_id,)).fetchone()
        if not row:
            raise Problem("forbidden", "This computer is no longer registered", 403)
        on = bool(setting(row)["bots"])
        waiting = on and _waiting(c, row["id"])
    if on:
        with _attached_lock:
            _attached[row["id"]] = H.now()
    if waiting:
        with store.transaction() as c:
            back_online(c, row["id"])
    return {"runner_id": row["id"], "name": row["label"], "serving": on}


def card_key(runner_id):
    return f"exit:{runner_id}"


def _waiting(c, runner_id):
    from . import reconnects
    return reconnects._present(c) and c.execute(
        "SELECT 1 FROM task_reconnects WHERE key=? AND fixed_at IS NULL LIMIT 1", (card_key(runner_id),)).fetchone() is not None


def back_online(c, runner_id):
    from . import reconnects
    return reconnects.fixed(c, card_key(runner_id), why="exit_online")


def record(c, store, report):
    """Apply one relay report: `usage` sums streams and bytes into each session's row (and ends it when no stream is
    left open), `offline` records each refusal and sets the run's task waiting on its exit. Returns what it did."""
    now = H.now()
    done = {"usage": 0, "offline": 0, "cards": 0}
    for item in (report.get("usage") or [])[:1024]:
        if _session(c, item, now, error=None):
            done["usage"] += 1
    for item in (report.get("offline") or [])[:256]:
        row = _session(c, {**item, "refused": item.get("refused", 1)}, now, error="exit offline")
        if not row:
            continue
        done["offline"] += 1
        if _card(c, store, row, item.get("at") or now):
            done["cards"] += 1
    return done


def _int(value, cap=1 << 62):
    try:
        return max(0, min(int(value), cap))
    except (TypeError, ValueError):
        return 0


def _session(c, item, now, error):
    """Find or make the session row for (run, host, port, exit) and add the item's counts."""
    aid, host, exit_ = str(item.get("attempt") or ""), normalize_host(item.get("host")), str(item.get("exit") or "")
    port = _int(item.get("port"))
    parsed = parse_exit(exit_)
    if not aid or not host or not parsed or parsed[0] == DIRECT or port not in PORTS:
        return None
    attempt, task = _attempt(c, aid)
    if not attempt:
        return None
    open_now = _int(item.get("open"), 1 << 20)
    c.execute(
        "INSERT INTO exit_sessions(id,attempt_id,bot,task_id,host,port,exit,exit_name,origin_runner,started,updated,ended,"
        "streams,open_streams,bytes_up,bytes_down,refused,last_error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(attempt_id,host,port,exit) DO UPDATE SET updated=excluded.updated,"
        "streams=streams+excluded.streams,open_streams=excluded.open_streams,bytes_up=bytes_up+excluded.bytes_up,"
        "bytes_down=bytes_down+excluded.bytes_down,refused=refused+excluded.refused,"
        "last_error=coalesce(excluded.last_error,last_error),"
        "ended=CASE WHEN excluded.open_streams=0 THEN excluded.updated ELSE NULL END",
        (H.new_id(), aid, attempt["bot"], task, host, port, exit_, computer_label(c, parsed[1]), attempt["runner_id"],
         now, now, now if open_now == 0 else None, _int(item.get("opened")), open_now, _int(item.get("up")),
         _int(item.get("down")), _int(item.get("refused")), error))
    row = c.execute("SELECT * FROM exit_sessions WHERE attempt_id=? AND host=? AND port=? AND exit=?",
                    (aid, host, port, exit_)).fetchone()
    return dict(row) if row else None


def _card(c, store, session, at):
    """The run's task waits on its exit computer: one card per task and exit, for the computer's operator (who can
    turn it on), else the bot's own fixer. Not when the exit came back after this refusal."""
    from . import reconnects
    parsed = parse_exit(session["exit"])
    if not session["task_id"] or not parsed:
        return None
    with _attached_lock:
        back = _attached.get(parsed[1])
    if back and back >= at:
        return None
    exit_row = c.execute("SELECT id,label,operator FROM runners WHERE id=?", (parsed[1],)).fetchone()
    if not exit_row:
        return None
    task = H.task(c, session["task_id"])
    person = H.human_actor(exit_row["operator"]) if exit_row["operator"] else None
    if not task or not person or not H.task_private_readable(c, person, task):
        person = reconnects.person_for(c, store.settings, session["bot"], task)
    return reconnects.open(c, session["task_id"], session["bot"], card_key(exit_row["id"]), "exit", exit_row["label"],
                           person, {"kind": "exit", "computer": exit_row["id"]})


def end_open_sessions(c):
    """Runs at every server start: a session left open ended when it last moved; a relay still carrying its streams
    reopens it with its next report (the upsert sets ended back to NULL while streams are open)."""
    c.execute("UPDATE exit_sessions SET ended=updated,open_streams=0 WHERE ended IS NULL")


def private_request(request, settings):
    import hmac
    return (bool(settings.screen_relay_key) and request.method == "POST" and request.url.path == PRIVATE_PATH
            and hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + settings.screen_relay_key))


def history_view(row):
    return {k: row[k] for k in ("id", "bot", "task_id", "host", "port", "exit", "exit_name", "started", "updated",
                                "ended", "streams", "bytes_up", "bytes_down", "refused", "last_error")}


# ------------------------------------------------------------------------------------------------ routes
def install(app, store, auth, mutate, execution, settings):
    from . import models as M
    from .screen import may_change

    def computer(c, rid):
        return c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()

    @app.post("/api/v2/runners/{rid}/exit")
    def exit_setting(request: Request, rid: str, body: M.ExitSetting):
        """Exit for bots: off, or on for the bots named. The computer's operator or an admin; on needs a runner that
        offers to be an exit. Turning it off, or taking a bot off, refuses that bot's next streams at once and closes
        the link at the computer's next heartbeat."""
        who = request.state.identity

        def work(c):
            runner = computer(c, rid)
            if not may_change(auth, who, runner):
                raise Problem("forbidden", "You cannot change this computer", 403)
            if body.on and not available(runner):
                raise Problem("exit_unavailable", UNAVAILABLE, 409)
            bots = list(dict.fromkeys(b.strip() for b in body.bots if b.strip()))
            if len(bots) > MAX_BOTS:
                raise Problem("validation", f"At most {MAX_BOTS} bots", 422)
            for slug in bots:
                bot = H.bot(c, slug)
                if not bot or bot.get("state") == "archived":
                    raise Problem("not_found", f"No bot {slug}", 404)
            value = json.dumps({"bots": bots}) if body.on else None
            c.execute("UPDATE runners SET exit_json=? WHERE id=?", (value, rid))
            H.event(c, who.actor, "runner.exit", rid, {"on": body.on, "bots": bots if body.on else []})
            return {"exit": {"on": body.on, "bots": bots if body.on else []}, "exit_available": available(runner)}
        return mutate(request, body, work)

    def may_read_bot(c, who, slug):
        return auth.bot_manager(c, who, slug) or (who.role == "bot" and who.actor == H.bot_actor(slug))

    @app.get("/api/v2/bots/{slug}/sites")
    def bot_sites(request: Request, slug: str):
        """The bot's site list, and the computers it may pick as an exit (those whose owner turned Exit on for it)."""
        who = request.state.identity
        with store.read() as c:
            if not H.bot(c, slug) or not may_read_bot(c, who, slug):
                raise Problem("forbidden", "You cannot see this bot's sites", 403)
            return site_view(c, slug)

    @app.put("/api/v2/bots/{slug}/sites")
    def set_bot_sites(request: Request, slug: str, body: M.BotSites):
        """Replace the bot's site list: each host direct (the default) or through one exit computer. A person who
        can manage the bot."""
        who = request.state.identity

        def work(c):
            if not H.bot(c, slug) or not auth.bot_manager(c, who, slug):
                raise Problem("forbidden", "You cannot change this bot's sites", 403)
            return replace_sites(c, who.actor, slug, body.sites)
        return mutate(request, body, work)

    @app.get("/api/v2/exit-sessions")
    def exit_history(request: Request, bot: str = "", computer: str = "", before: str = "", limit: int = 50):
        """Exit history, newest first: one bot's (its manager) or one exit computer's (its operator or an admin).
        Paged by `before` (a `started` time)."""
        who = request.state.identity
        limit = max(1, min(int(limit), HISTORY_PAGE))
        with store.read() as c:
            if bot:
                if not H.bot(c, bot) or not auth.bot_manager(c, who, bot):
                    raise Problem("forbidden", "You cannot see this bot's history", 403)
                where, args = "bot=?", [bot]
            elif computer:
                if not may_change(auth, who, c.execute("SELECT * FROM runners WHERE id=?", (computer,)).fetchone()):
                    raise Problem("forbidden", "You cannot see this computer's history", 403)
                where, args = "exit=?", [f"computer:{computer}"]
            else:
                raise Problem("validation", "Name a bot or a computer", 422)
            if before:
                where += " AND started<?"
                args.append(before)
            rows = c.execute(f"SELECT * FROM exit_sessions WHERE {where} ORDER BY started DESC, id DESC LIMIT ?",
                             (*args, limit + 1)).fetchall()
        page = [history_view(r) for r in rows[:limit]]
        return {"sessions": page, **({"before": page[-1]["started"]} if len(rows) > limit else {})}

    @app.post(PRIVATE_PATH, include_in_schema=False)
    def authorize(request: Request, body: dict):
        """The relay's questions (relay-rs/src/exit.rs), on loopback with its private key: `attach` (a runner's link),
        `open` (one stream) and `report` (usage and refusals since the last one)."""
        if not private_request(request, settings):
            raise Problem("forbidden", "Private relay key required", 403)
        kind = body.get("kind")
        if kind == "attach":
            token = body.get("authorization")
            if not isinstance(token, str) or len(token) > 8192:
                raise Problem("forbidden", "Invalid relay authorization", 403)
            return attach(store, auth, token)
        if kind == "open":
            rid, aid, host, port = body.get("runner_id"), body.get("attempt"), body.get("host"), body.get("port")
            if (not all(isinstance(v, str) and 0 < len(v) <= 255 for v in (rid, aid, host))
                    or type(port) is not int):
                return refuse("invalid", "Invalid stream")
            with store.read() as c:
                return decide(c, rid, aid, host, port)
        if kind == "report":
            with store.transaction() as c:
                return record(c, store, body)
        raise Problem("validation", "Unknown relay question", 422)
