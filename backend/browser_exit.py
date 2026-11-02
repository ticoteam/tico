"""Browser exits (docs/computers.md, "Share internet with bots"): a bot's browser reaches the web from another Tico
computer instead of the one it runs on, a person's laptop at home for instance.

Two consents, both stored here: the exit computer's owner turns sharing on and picks the bots that may use it, with a
daily byte cap (`browser_exits`); a person who manages a bot then points it at one of those computers
(`bot_browser_exits`). Off by default on both sides.

The relay (relay-rs/src/exit.rs) moves the bytes. This module decides: each tunnel a bot computer asks for is checked
here (the bot is on that computer, its exit still shares with it), then granted at the relay with the exit's policy as
read now. Two durable generations fence an older read: the exit's policy generation and the bot's selection generation
(its exit and the computer it runs on; hubdb triggers bump it with the assignment). A grant is also bound to the relay
lease it was read under (screen_relay.RemoteRegistry). Nothing is written per tunnel; the relay sends the bytes moved,
open tunnels included, in its heartbeat, each on the UTC day they moved, and they are added up per exit, day, bot and
hostname (`browser_exit_usage`), kept 30 days.

Every refusal fails closed and names the exit computer, and its code starts `browser_exit` (the runner shows the detail
as it is: runner-rs/src/browser_exit.rs).
"""
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import Request

from .store import H, Problem

GB = 1_000_000_000
DEFAULT_CAP = 5 * GB
MIN_CAP, MAX_CAP = 100_000_000, 10_000 * GB
MAX_BOTS = 500
RETENTION_DAYS = 30
USAGE_PAGE, USAGE_PAGE_MAX = 200, 500
PORTS = (80, 443)
LINK_PATH = "/api/v2/runners/me/browser-exit/ws"
# A runner that advertises this runs a bot's turns through its browser exit (runner-rs/src/browser_exit.rs). Any other
# runner would ignore the exit and browse from its own address, so a bot with an exit waits for an updated runner
# (execution.claim_refusal `browser_exit_update`).
CAPABILITY = "browser_exit_v1"
_HOST = re.compile(r"[A-Za-z0-9._-]{1,253}|[0-9A-Fa-f:.]{2,45}")


# Bumps a bot's selection generation in the same transaction as any change to the computer it runs on, whichever
# code path writes the assignment or removes that computer, and an exit's policy generation when its computer is
# removed.
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS bot_browser_exit_assigned AFTER INSERT ON assignments BEGIN
  UPDATE bot_browser_exits SET generation=generation+1 WHERE bot=NEW.bot; END;
CREATE TRIGGER IF NOT EXISTS bot_browser_exit_moved AFTER UPDATE OF runner_id ON assignments
  WHEN OLD.runner_id IS NOT NEW.runner_id BEGIN
  UPDATE bot_browser_exits SET generation=generation+1 WHERE bot=NEW.bot; END;
CREATE TRIGGER IF NOT EXISTS bot_browser_exit_unassigned AFTER DELETE ON assignments BEGIN
  UPDATE bot_browser_exits SET generation=generation+1 WHERE bot=OLD.bot; END;
CREATE TRIGGER IF NOT EXISTS browser_exit_computer_removed AFTER UPDATE OF revoked_at ON runners
  WHEN OLD.revoked_at IS NOT NEW.revoked_at BEGIN
  UPDATE browser_exits SET generation=generation+1 WHERE runner_id=NEW.id; END;
CREATE TRIGGER IF NOT EXISTS bot_browser_exit_computer_removed AFTER UPDATE OF revoked_at ON runners
  WHEN OLD.revoked_at IS NOT NEW.revoked_at BEGIN
  UPDATE bot_browser_exits SET generation=generation+1
    WHERE bot IN (SELECT bot FROM assignments WHERE runner_id=NEW.id); END;
"""


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _computer(c, rid):
    return c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()


def _bots(value):
    try:
        bots = json.loads(value or "[]")
    except ValueError:
        return []
    return [b for b in bots if isinstance(b, str)] if isinstance(bots, list) else []


def policy(c, rid):
    """The computer's sharing as stored, and off for a removed or unknown computer, with its generation."""
    row = c.execute("SELECT e.sharing,e.bots,e.daily_cap_bytes,e.generation,r.revoked_at FROM runners r "
                    "LEFT JOIN browser_exits e ON e.runner_id=r.id WHERE r.id=?", (rid,)).fetchone()
    if not row:
        return {"sharing": False, "bots": [], "daily_cap_bytes": DEFAULT_CAP, "generation": 0}
    return {"sharing": bool(row["sharing"]) and not row["revoked_at"], "bots": _bots(row["bots"]),
            "daily_cap_bytes": int(row["daily_cap_bytes"] or DEFAULT_CAP), "generation": int(row["generation"] or 0)}


def policies(c):
    """{runner_id: policy} for every computer with a stored row: one query for a listing."""
    return {r["runner_id"]: {"sharing": bool(r["sharing"]), "bots": _bots(r["bots"]),
                             "daily_cap_bytes": int(r["daily_cap_bytes"] or DEFAULT_CAP)}
            for r in c.execute("SELECT runner_id,sharing,bots,daily_cap_bytes FROM browser_exits")}


def used(c, rid, day=None):
    row = c.execute("SELECT COALESCE(SUM(bytes),0) FROM browser_exit_usage WHERE runner_id=? AND day=?",
                    (rid, day or today())).fetchone()
    return int(row[0])


def sharing(c, rid):
    """The heartbeat's flag: whether the runner holds its exit link open."""
    return policy(c, rid)["sharing"]


def is_owner(who, runner):
    """The person whose computer it is: the only one who shares its internet."""
    return bool(runner) and who.role in ("human", "owner") and who.actor == "human:" + runner["operator"]


BOT_EXIT_SQL = "SELECT * FROM bot_browser_exits WHERE bot=?"


def bot_exit(c, bot):
    """The computer `bot` browses through, or None. Read once per snapshot (read_cache): Health and the claim check
    prime every bot's row together (events.prime_bot_rows)."""
    from .read_cache import one
    row = one(c, BOT_EXIT_SQL, (bot,))
    return row["runner_id"] if row else None


def bot_generation(c, bot):
    row = c.execute("SELECT generation FROM bot_browser_exits WHERE bot=?", (bot,)).fetchone()
    return int(row[0]) if row else 0


def generations(c):
    """({exit runner_id: policy generation}, {bot: selection generation}): what the relay should have."""
    return ({r[0]: int(r[1]) for r in c.execute("SELECT runner_id,generation FROM browser_exits")},
            {r[0]: int(r[1]) for r in c.execute("SELECT bot,generation FROM bot_browser_exits")})


def bot_exits(c):
    return {r["bot"]: r["runner_id"] for r in c.execute(
        "SELECT bot,runner_id FROM bot_browser_exits WHERE runner_id IS NOT NULL")}


def bots_on(c, rid):
    """The bots on this computer with an exit set: the heartbeat answer's `browser_exit_bots`, against which the runner
    stops the proxies of every other bot (runner-rs/src/browser_exit.rs keep_proxies)."""
    return [r[0] for r in c.execute(
        "SELECT e.bot FROM bot_browser_exits e JOIN assignments a ON a.bot=e.bot WHERE a.runner_id=? "
        "AND e.runner_id IS NOT NULL ORDER BY e.bot LIMIT 1000", (rid,))]


def for_turn(c, bot):
    """What a claimed turn carries: the exit computer's id and name, or None. A set exit that no longer shares or was
    removed is still handed out, so the bot's browser fails with words that say so instead of going direct."""
    rid = bot_exit(c, bot)
    if not rid:
        return None
    row = c.execute("SELECT label FROM runners WHERE id=?", (rid,)).fetchone()
    return {"runner_id": rid, "name": (row["label"] if row else "") or "The exit computer"}


def listing(rows, manage, owner, hidden=frozenset(), used_today=0):
    """A computer row's `browser_exit` in the Computers listing. The bots another member may not see are left out."""
    p = rows or {"sharing": False, "bots": [], "daily_cap_bytes": DEFAULT_CAP}
    view = {"sharing": p["sharing"], "bots": [b for b in p["bots"] if b not in hidden],
            "daily_cap_bytes": p["daily_cap_bytes"], "may_change": owner, "may_turn_off": manage}
    if manage:
        view["used_today_bytes"] = used_today
    return view


def used_today_all(c):
    return {r[0]: int(r[1]) for r in c.execute(
        "SELECT runner_id,SUM(bytes) FROM browser_exit_usage WHERE day=? GROUP BY runner_id", (today(),))}


def valid_host(host):
    return isinstance(host, str) and bool(_HOST.fullmatch(host)) and ".." not in host


def record(c, rows):
    """Add the relay's usage rows (bytes moved and tunnels closed, per exit, day, bot and hostname) to the per-day log.
    A malformed row is skipped."""
    for r in rows:
        try:
            rid, day, bot, host = str(r["exit"]), str(r["day"]), str(r["bot"]), str(r["host"]).lower()[:253]
            n, tunnels = max(0, int(r["bytes"])), max(0, int(r.get("tunnels", 0)))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        c.execute("INSERT INTO browser_exit_usage(runner_id,day,bot,host,bytes,tunnels) VALUES(?,?,?,?,?,?) "
                  "ON CONFLICT(runner_id,day,bot,host) DO UPDATE SET bytes=bytes+excluded.bytes,"
                  "tunnels=tunnels+excluded.tunnels", (rid, day, bot, host, n, tunnels))


def record_batch(c, boot, batch):
    """Record one of the relay's usage batches, once: it resends a batch until acknowledged, however long that takes,
    and an acknowledgement can be lost. Each batch of a relay boot has the next sequence number, so a batch at or below
    the boot's mark was recorded already; the mark moves in the same transaction as the rows. Returns the id to
    acknowledge, or None."""
    if not isinstance(batch, dict) or not isinstance(batch.get("id"), str) or not 0 < len(batch["id"]) <= 64:
        return None
    seq = batch.get("seq")
    if not isinstance(boot, str) or not 0 < len(boot) <= 64 or type(seq) is not int or seq < 1:
        return None
    mark = c.execute("SELECT seq FROM browser_exit_marks WHERE boot=?", (boot,)).fetchone()
    if not mark or seq > mark[0]:
        record(c, batch.get("rows") if isinstance(batch.get("rows"), list) else [])
        c.execute("INSERT INTO browser_exit_marks(boot,seq) VALUES(?,?) ON CONFLICT(boot) DO UPDATE SET seq=excluded.seq",
                  (boot, seq))
    return batch["id"]


def prune(c):
    """Drop days past retention."""
    now = datetime.now(timezone.utc)
    c.execute("DELETE FROM browser_exit_usage WHERE day<?",
              ((now - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d"),))


def authorize_link(store, auth, execution, body):
    """The relay's question for an exit's link: a registered computer whose owner shares its internet."""
    token = body.get("authorization")
    if not isinstance(token, str) or len(token) > 8192:
        raise Problem("forbidden", "Invalid relay authorization", 403)
    who = auth.authenticate({"authorization": token}, LINK_PATH, "GET")
    with store.read() as c:
        runner = execution.runner(c, who) if who.role == "runner" else None
        if runner is None or not sharing(c, runner["id"]):
            raise Problem("forbidden", "This computer does not share its internet", 403)
    return {"allowed": True, "runner_id": runner["id"]}


def install(app, store, auth, mutate, execution, sessions):
    from . import models as M
    from .views import human_only

    def changed(c, rid):
        # The heartbeat answer carries `browser_exit`: a change row asks the runner for one now (backend/events.py).
        c.execute("INSERT INTO changes(topic,kind,subject_id,at) VALUES('runner','browser_exit',?,?)", (rid, H.now()))

    def view(c, who, runner):
        p = policy(c, runner["id"])
        p.pop("generation")
        manage = auth.bot_admin(who) or is_owner(who, runner)
        if not manage:      # the bots this member may not see stay out, as in the Computers listing
            unseen = {bot for bot, a in auth.bot_accesses(c, who).items() if not a["see"]}
            p["bots"] = [b for b in p["bots"] if b not in unseen]
        return {**p, "computer": {"id": runner["id"], "label": runner["label"]},
                "used_today_bytes": used(c, runner["id"]) if manage else None,
                "may_change": is_owner(who, runner), "may_turn_off": manage}

    @app.get("/api/v2/runners/{rid}/browser-exit")
    def exit_policy(request: Request, rid: str):
        """Whether this computer shares its internet with bots, which bots, and its daily cap in bytes."""
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            runner = _computer(c, rid)
            if not runner:
                raise Problem("not_found", "Computer not found", 404)
            return view(c, who, runner)

    @app.post("/api/v2/runners/{rid}/browser-exit")
    def set_exit_policy(request: Request, rid: str, body: M.BrowserExitPolicy):
        """Share this computer's internet with bots, or stop. Only the person whose computer it is turns it on or
        changes the bots and cap; an admin may turn it off. Turning it off, or taking a bot off, closes their
        connections at once."""
        who = request.state.identity
        human_only(who)

        def work(c):
            runner = _computer(c, rid)
            if not runner:
                raise Problem("not_found", "Computer not found", 404)
            now = policy(c, rid)
            bots = now["bots"] if body.bots is None else list(dict.fromkeys(body.bots))
            cap = now["daily_cap_bytes"] if body.daily_cap_bytes is None else body.daily_cap_bytes
            if not is_owner(who, runner):
                stopping = not body.sharing and bots == now["bots"] and cap == now["daily_cap_bytes"]
                if not (stopping and auth.bot_admin(who)):
                    raise Problem("forbidden", "Only the person whose computer it is shares its internet", 403)
            if bots:
                known = {r[0] for r in c.execute(
                    f"SELECT slug FROM bots WHERE slug IN ({','.join('?' * len(bots))})", bots)}
                if unknown := [b for b in bots if b not in known]:
                    raise Problem("not_found", "No such bot: " + ", ".join(unknown), 404)
            c.execute("INSERT INTO browser_exits(runner_id,sharing,bots,daily_cap_bytes,changed_by,changed_at) "
                      "VALUES(?,?,?,?,?,?) ON CONFLICT(runner_id) DO UPDATE SET sharing=excluded.sharing,"
                      "bots=excluded.bots,daily_cap_bytes=excluded.daily_cap_bytes,"
                      "generation=generation+1,changed_by=excluded.changed_by,changed_at=excluded.changed_at",
                      (rid, int(body.sharing), json.dumps(bots), cap, who.actor, H.now()))
            H.event(c, who.actor, "runner.browser_exit", rid,
                    {"sharing": body.sharing, "bots": bots, "daily_cap_bytes": cap})
            changed(c, rid)
            return {"sharing": body.sharing, "bots": bots, "daily_cap_bytes": cap}
        result = mutate(request, body, work)
        sessions.push_exit_policy(rid)      # what is stored now, never this request's (a replay must not reopen)
        return result

    @app.get("/api/v2/runners/{rid}/browser-exit/usage")
    def exit_usage(request: Request, rid: str, days: int = 7, limit: int = USAGE_PAGE, offset: int = 0):
        """What went through this computer for bots: per day, bot and hostname, the bytes and connections, newest day
        first, kept 30 days. The person whose computer it is, or an admin."""
        who = request.state.identity
        human_only(who)
        days, limit, offset = max(1, min(days, RETENTION_DAYS)), max(1, min(limit, USAGE_PAGE_MAX)), max(0, offset)
        since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        with store.read() as c:
            runner = _computer(c, rid)
            if not runner:
                raise Problem("not_found", "Computer not found", 404)
            if not (auth.bot_admin(who) or is_owner(who, runner)):
                raise Problem("forbidden", "Only the person whose computer it is or an admin sees what went through it",
                              403)
            rows = [dict(r) for r in c.execute(
                "SELECT day,bot,host,bytes,tunnels FROM browser_exit_usage WHERE runner_id=? AND day>=? "
                "ORDER BY day DESC,bytes DESC,bot,host LIMIT ? OFFSET ?", (rid, since, limit + 1, offset))]
            totals = [dict(r) for r in c.execute(
                "SELECT day,SUM(bytes) AS bytes FROM browser_exit_usage WHERE runner_id=? AND day>=? "
                "GROUP BY day ORDER BY day DESC", (rid, since))]
        more = len(rows) > limit
        return {"rows": rows[:limit], "days": totals, "next": offset + limit if more else None}

    def choices(c, bot):
        return [{"runner_id": r["id"], "label": r["label"], "operator": r["operator"]}
                for r in c.execute("SELECT r.id,r.label,r.operator,e.bots FROM browser_exits e "
                                   "JOIN runners r ON r.id=e.runner_id WHERE e.sharing=1 AND r.revoked_at IS NULL "
                                   "ORDER BY r.label")
                if bot in _bots(r["bots"])]

    @app.get("/api/v2/bots/{bot}/browser-exit")
    def bot_browser_exit(request: Request, bot: str):
        """The computer the bot's browser reaches the web through (null: its own), and the computers it may pick:
        those whose owners share their internet with it."""
        who = request.state.identity
        with store.read() as c:
            auth.require_read(c, who, bot)
            return {"browser_exit": for_turn(c, bot), "choices": choices(c, bot),
                    "may_change": auth.bot_manager(c, who, bot)}

    @app.post("/api/v2/bots/{bot}/browser-exit")
    def set_bot_browser_exit(request: Request, bot: str, body: M.BotBrowserExit):
        """Browse through another computer's internet, or null for the bot's own. Only a computer whose owner shares
        its internet with this bot; a person who manages the bot."""
        who = request.state.identity
        before = []

        def work(c):
            auth.require_read(c, who, bot)
            if not auth.bot_manager(c, who, bot):
                raise Problem("forbidden", "You cannot manage this bot", 403)
            before.append(bot_exit(c, bot))
            if body.runner_id is None:
                # Cleared, not deleted: the selection generation only grows, so an older grant stays fenced.
                c.execute("UPDATE bot_browser_exits SET runner_id=NULL,generation=generation+(runner_id IS NOT NULL),"
                          "set_by=?,set_at=? WHERE bot=?", (who.actor, H.now(), bot))
            else:
                runner = _computer(c, body.runner_id)
                if not runner:
                    raise Problem("not_found", "Computer not found", 404)
                p = policy(c, body.runner_id)
                if not (p["sharing"] and bot in p["bots"]):
                    raise Problem("browser_exit_not_shared",
                                  f"{runner['label']} doesn't share its internet with this bot", 409)
                c.execute("INSERT INTO bot_browser_exits(bot,runner_id,set_by,set_at) VALUES(?,?,?,?) "
                          "ON CONFLICT(bot) DO UPDATE SET runner_id=excluded.runner_id,"
                          "generation=generation+(runner_id IS NOT excluded.runner_id),set_by=excluded.set_by,"
                          "set_at=excluded.set_at", (bot, body.runner_id, who.actor, H.now()))
            H.event(c, who.actor, "bot.browser_exit", bot, {"runner_id": body.runner_id})
            for (rid,) in c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchall():
                changed(c, rid)             # its runner hears `browser_exit_bots` now
            return {"browser_exit": for_turn(c, bot)}
        result = mutate(request, body, work)
        if before and before[0] != body.runner_id:
            sessions.push_bot_generation(bot, "browser exit changed")
        return result

    def refuse(code, detail, status):
        raise Problem("browser_exit_" + code, detail, status)

    @app.post("/api/v2/runners/me/browser-exit/tunnels")
    def open_tunnel(request: Request, body: M.BrowserExitTunnel):
        """One connection of a bot's browser through its exit computer: a one-use ticket for the relay. Asked by the
        computer the bot runs on; nothing is written."""
        who = request.state.identity
        if not valid_host(body.host) or body.port not in PORTS:
            refuse("refused", "Only hostnames on ports 80 and 443 go through a browser exit", 400)
        for _ in range(2):      # once more when a policy change or a new relay lease fenced the first read
            epoch = sessions.exit_epoch()     # before the read: a grant read under an older lease is never sent
            with store.read() as c:
                runner = execution.runner(c, who) if who.role == "runner" else None
                if not runner:
                    raise Problem("forbidden", "This computer is no longer registered", 403)
                if not c.execute("SELECT 1 FROM assignments WHERE bot=? AND runner_id=?",
                                 (body.bot, runner["id"])).fetchone():
                    raise Problem("forbidden", "This bot is not on this computer", 403)
                exit_rid = bot_exit(c, body.bot)
                if not exit_rid:
                    refuse("none", "This bot has no browser exit", 409)
                exit_row = c.execute("SELECT label,revoked_at FROM runners WHERE id=?", (exit_rid,)).fetchone()
                name = (exit_row["label"] if exit_row else "") or "The exit computer"
                if not exit_row or exit_row["revoked_at"]:
                    refuse("removed", f"{name} was removed from Tico", 409)
                p, bot_gen = policy(c, exit_rid), bot_generation(c, body.bot)
                if not (p["sharing"] and body.bot in p["bots"]):
                    refuse("not_shared", f"{name} doesn't share its internet with this bot", 403)
                day = today()
                spent = used(c, exit_rid, day)
            if spent >= p["daily_cap_bytes"]:
                refuse("over_cap", f"{name} reached its daily limit", 429)
            tunnel = H.new_id()
            code = sessions.exit_grant({
                "id": tunnel, "runner_id": runner["id"], "exit": exit_rid, "bot": body.bot, "host": body.host.lower(),
                "port": body.port, "day": day, "used": spent, "bot_generation": bot_gen,
                "policy": {"generation": p["generation"], "sharing": p["sharing"], "bots": p["bots"],
                           "cap": p["daily_cap_bytes"]}}, epoch)
            if isinstance(code, dict):
                return {"tunnel_id": tunnel, "ticket": code["ticket"], "exit": {"runner_id": exit_rid, "name": name}}
            if code != "exit_stale":
                break
        refusals = {
            "exit_offline": ("offline", f"{name} is offline", 409),
            "exit_not_shared": ("not_shared", f"{name} doesn't share its internet with this bot", 403),
            "exit_over_cap": ("over_cap", f"{name} reached its daily limit", 429),
            "exit_busy": ("busy", f"{name} has too many connections open; try again shortly", 429),
        }
        refuse(*refusals.get(code, ("unavailable", f"The relay is unavailable, so {name} can't be used right now", 503)))

    return {"choices": choices}
