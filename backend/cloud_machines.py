"""Tico cloud machines (phase B): each bot of a Tico Cloud company runs on its own cloud machine, started when it has
work and stopped when it has none.

A machine is a boat.dev VM Tico HQ owns (tico-services); HQ keeps the boat key, billing, caps, the boat-side ttl and the
14-day keep-alive. This module is the server's half: it decides *when*. The contract (same key and auth as
`<Gateway URL>/billing`, JSON both ways, the Gateway error shape, `402 {"error": "frozen"|"locked"}` when credit is out):

    GET    machines                  {machines: [Machine], sizes: [Size], caps}
    POST   machines                  {key: <bot>, size, enroll_token, hub_url} -> Machine   (idempotent on key)
    POST   machines/<id>/start       {size?} -> Machine      resume; a size resizes on the same disk
    POST   machines/<id>/stop        -> Machine
    POST   machines/<id>/heartbeat   -> {ttl_s}              every minute while this server wants it on
    DELETE machines/<id>             -> {ok}                 deletes the disk

    Machine {id, key, size, state: starting|running|stopping|stopped|deleted|failed, started_at, stopped_at,
             cost_month_usd, starts_month, last_error}
    Size    {name, cpu, memory_gb, usd_per_hour (the customer's price), start_usd, idle_stop_s (break-even)}

One row per bot (`cloud_machines`): where it runs (`mode` cloud, or computer: any other Computer), the size it should
have, Always on, and the machine as HQ last described it. Off a cloud company (cloud_access.state is None) there are no
rows, the option is hidden and nothing here runs; on one, the same until HQ has listed its sizes (`offered`). On one, a bot with no computer defaults to its own machine
(placement.auto_place); choosing another computer for it (Execution.assign) sets `computer`, and the machine stops.

The machine's runner is a normal Computer, enrolled with a one-time code minted here (the add-a-runner flow,
`enrollments`) that HQ hands the machine and never keeps. Execution.enroll recognises the code (`cloud_machine_codes`),
names the computer after the bot, closes it to other bots (it is never placed by placement or onboarding) and assigns
the bot to it.

The manager (`Manager.tick`, every TICK seconds on its own thread, never inside a transaction while HQ answers):

    want     the bot is active and in cloud mode, and has a turn out, claimable queued work (the claim's own rule,
             execution.bot_claimable, on the machine's last report; a reason only a stopped machine leaves stale
             wakes it too), a watcher due (its last run plus its interval), Always on, or it waits to move here
    wake     a stopped machine with a want is started (created on first use); the size set since applies here
    keep     a running machine gets a heartbeat every HEARTBEAT seconds; HQ's ttl stops it if they stop coming
    idle     with no turn out, no attempt or watcher activity for the size's `idle_stop_s` since the later of its start
             and its last activity, it is stopped, unless Always on. A watcher running often keeps it on by itself.
    guard    a machine woken for one want that saw no activity is not woken again for that same want: a job its
             runner never takes would otherwise start (4 cents) and idle-stop it forever. New work wakes it.
    frozen   out of credit (cloud_access.frozen, or HQ's 402): nothing starts and the bot shows "Out of credit". HQ
             stops running machines and keeps their disks. A 402 is asked again once HQ says the company is open.
    errors   HQ not answering or refusing: the bot's queued work says so ("its cloud machine didn't start"), and the
             call is retried with backoff (BACKOFF to BACKOFF_MAX); one history event per streak, not per retry.

`decide` is the whole policy as a pure function of the row, the facts and the clock, so the tests drive it directly.
"""
import json
import logging
import secrets
import threading
import time
from datetime import timezone

import httpx

from clients import gateway as G
from clients.gateway import user_agent

from . import cloud_access, outbound_tls
from .store import H, Problem, digest

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_machines(
 bot TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'cloud', size TEXT NOT NULL DEFAULT 'default',
 always_on INTEGER NOT NULL DEFAULT 0, machine_id TEXT, runner_id TEXT, state TEXT NOT NULL DEFAULT 'none',
 machine_size TEXT, started_at TEXT, stopped_at TEXT, cost_month_usd REAL NOT NULL DEFAULT 0,
 starts_month INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
 active_at REAL, heartbeat_at REAL, ttl_s INTEGER, next_try REAL, failures INTEGER NOT NULL DEFAULT 0,
 problem TEXT NOT NULL DEFAULT '', problem_at TEXT, wake_key TEXT, woke_at REAL,
 changed_by TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS cloud_machine_codes(
 code_hash TEXT PRIMARY KEY, bot TEXT NOT NULL, created TEXT NOT NULL);
"""
MODES = ("cloud", "computer")
SIZES = ("small", "default", "large", "xlarge")       # the names before HQ has listed its own
STATES = ("starting", "running", "stopping", "stopped", "deleted", "failed")
ON = ("starting", "running")
SIZES_KEY = "cloud-machine-sizes"
TICK = 10                       # seconds between looks
SYNC = 60                       # seconds between HQ lists
HEARTBEAT = 60
IDLE_FALLBACK = 600             # a size HQ has not listed stops soonest: stopping early never makes a bill large
BACKOFF, BACKOFF_MAX = 15, 900
STOPPING_RETRY = 5              # boat answers 409 sandbox_stopping to a resume right after a stop
FROZEN_RETRY = 600              # a 402 is asked again this late, or as soon as HQ says the company is open
ENROLL_MINUTES = 15
# A claim refusal that only says the machine's last report is stale: a stopped machine is woken for it, and its
# runner reports afresh (the guard keeps a refusal that stays from waking it again).
STALE = ("readiness_unknown", "not_ready", "isolation_unknown", "lane_held", "maintaining")
WAIT = 15
ANSWER_CAP = 256 * 1024
TRANSPORT = None                # tests' fake HQ (httpx transport), never the live one
now = time.time                 # tests' clock


class Refused(Exception):
    """HQ refused or did not answer. `status` 0 is no answer; `code` is stable and holds no HQ text."""

    def __init__(self, code, status=0):
        super().__init__(code)
        self.code, self.status = code, status


# ----------------------------------------------------------------------------- HQ
def _number(value, low=0):
    if type(value) not in (int, float) or value != value or value < low:
        raise ValueError("a number")
    return value


def _text(value, cap=100):
    return value[:cap] if isinstance(value, str) else None


def parse_machine(value):
    """HQ's Machine, field by field; ValueError for any other shape."""
    if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not 0 < len(value["id"]) <= 100 \
            or value.get("state") not in STATES or not isinstance(value.get("key"), str):
        raise ValueError("not a machine")
    starts = value.get("starts_month") or 0
    if type(starts) is not int or starts < 0:
        raise ValueError("starts")
    return {"id": value["id"], "key": value["key"][:100], "size": _text(value.get("size"), 20) or "",
            "state": value["state"], "started_at": _text(value.get("started_at"), 40),
            "stopped_at": _text(value.get("stopped_at"), 40),
            "cost_month_usd": float(_number(value.get("cost_month_usd") or 0)), "starts_month": starts,
            "last_error": _text(value.get("last_error"), 200) or ""}


def parse_size(value):
    if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not 0 < len(value["name"]) <= 20:
        raise ValueError("not a size")
    idle = value.get("idle_stop_s")
    if type(idle) is not int or idle <= 0:
        raise ValueError("idle_stop_s")
    return {"name": value["name"], "cpu": _number(value.get("cpu"), 1), "memory_gb": _number(value.get("memory_gb")),
            "usd_per_hour": float(_number(value.get("usd_per_hour"))),
            "start_usd": float(_number(value.get("start_usd") or 0)), "idle_stop_s": idle}


class HQ:
    """The machines routes at `<Gateway URL>/machines`, with the install's gateway key. Raises Refused."""

    def __init__(self, key, base):
        self.key, self.base = key, base

    def call(self, method, path="", body=None):
        headers = {"Authorization": "Bearer " + self.key, "User-Agent": user_agent(), "Accept-Encoding": "identity"}
        try:
            with httpx.Client(transport=TRANSPORT, timeout=WAIT, trust_env=False, follow_redirects=False,
                              verify=outbound_tls.context(trust_env=False)) as client:
                with client.stream(method, f"{self.base}/machines{path}", json=body, headers=headers) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > ANSWER_CAP:
                            raise Refused("oversized")
                    status = response.status_code
        except httpx.HTTPError:
            raise Refused("unreachable") from None
        try:
            answer = json.loads(bytes(raw) or b"{}")
        except ValueError:
            answer = {}
        answer = answer if isinstance(answer, dict) else {}
        if status == 401:
            raise Refused("key_refused", 401)
        if status >= 400:
            error = answer.get("error", answer.get("code"))
            error = error.get("code") if isinstance(error, dict) else error
            ok = isinstance(error, str) and 0 < len(error) <= 40 and error.replace("_", "").isalnum()
            raise Refused(error.lower() if ok else f"http_{status}", status)
        return answer

    def machine(self, answer):
        try:
            return parse_machine(answer)
        except ValueError:
            raise Refused("bad_answer") from None

    def list(self):
        answer = self.call("GET")
        machines, sizes = [], []
        for row in answer.get("machines") or []:
            try:
                machines.append(parse_machine(row))
            except ValueError:
                continue
        for row in answer.get("sizes") or []:
            try:
                sizes.append(parse_size(row))
            except ValueError:
                continue
        caps = answer.get("caps") if isinstance(answer.get("caps"), dict) else {}
        return {"machines": machines, "sizes": sizes,
                "caps": {k: v for k, v in caps.items() if isinstance(k, str) and type(v) is int}}

    def create(self, key, size, enroll_token, hub_url):
        return self.machine(self.call("POST", "", {"key": key, "size": size, "enroll_token": enroll_token,
                                                   "hub_url": hub_url}))

    def start(self, machine_id, size=None):
        return self.machine(self.call("POST", f"/{machine_id}/start", {"size": size} if size else {}))

    def stop(self, machine_id):
        return self.machine(self.call("POST", f"/{machine_id}/stop"))

    def heartbeat(self, machine_id):
        ttl = self.call("POST", f"/{machine_id}/heartbeat").get("ttl_s")
        return ttl if type(ttl) is int else None

    def delete(self, machine_id):
        self.call("DELETE", f"/{machine_id}")


def client_of(c, vault, settings):
    """The HQ client for the connected gateway key, or None."""
    from . import gateway_connection
    key, base, _ = gateway_connection.target(c, vault, settings)
    if not key:
        return None
    try:
        return HQ(key, G.safe_base(base))
    except ValueError:
        return None


# ----------------------------------------------------------------------------- rows
def cloud_company(c, settings=None):
    """A Tico Cloud company, outside a demo or rehearsal: the manager asks HQ about machines."""
    if settings is not None and (getattr(settings, "demo", False) or getattr(settings, "rehearsal", False)):
        return False
    return cloud_access.state(c) is not None


def offered(c, settings=None):
    """Whether bots here may run on their own cloud machine: a Tico Cloud company whose HQ has listed machine sizes.
    Until HQ answers GET machines, nothing defaults to a machine HQ cannot make, and the option stays hidden."""
    return cloud_company(c, settings) and bool(sizes(c))


def row(c, bot):
    found = c.execute("SELECT * FROM cloud_machines WHERE bot=?", (bot,)).fetchone()
    return dict(found) if found else None


def rows(c):
    return {r["bot"]: dict(r) for r in c.execute("SELECT * FROM cloud_machines")}


def save(c, bot, **fields):
    fields["updated"] = H.now()
    c.execute("UPDATE cloud_machines SET " + ",".join(k + "=?" for k in fields) + " WHERE bot=?",
              (*fields.values(), bot))


def adopt(c, bot, actor=H.KEEPER):
    """A bot of a cloud company with no computer and no choice yet: its own machine (the default). Returns whether it
    is in cloud mode now. A bot someone moved to a computer keeps that; an external agent's has no computer at all."""
    from .agents import external_harness
    if external_harness(c, bot):
        return False
    c.execute("INSERT OR IGNORE INTO cloud_machines(bot,changed_by,updated) VALUES(?,?,?)", (bot, actor, H.now()))
    return c.execute("SELECT mode FROM cloud_machines WHERE bot=?", (bot,)).fetchone()["mode"] == "cloud"


def cloud_runners(c, but=None):
    """Computer ids that are some bot's own cloud machine (other than `but`'s): never offered to another bot."""
    return {r["runner_id"] for r in c.execute("SELECT bot,runner_id FROM cloud_machines WHERE runner_id IS NOT NULL")
            if r["bot"] != but}


def label_for(c, bot):
    found = H.bot(c, bot)
    return f"{(found or {}).get('display_name') or bot} (cloud)"[:100]


def sizes(c):
    """HQ's sizes as last listed, or [] before it has said."""
    from .read_cache import metadata
    found = metadata(c, SIZES_KEY)
    try:
        value = json.loads(found[0]) if found else {}
    except (ValueError, TypeError):
        value = {}
    return value.get("sizes") or [] if isinstance(value, dict) else []


def size_names(c):
    return [s["name"] for s in sizes(c)] or list(SIZES)


def idle_after(c, size, known=None):
    """The break-even idle time of a size (HQ's `idle_stop_s`), or IDLE_FALLBACK for one HQ has not listed."""
    for found in known if known is not None else sizes(c):
        if found["name"] == size:
            return found["idle_stop_s"]
    return IDLE_FALLBACK


# ----------------------------------------------------------------------------- enrollment
def mint(c, bot):
    """A one-time runner enrollment code for this bot's machine: the add-a-runner flow's, with the bot's operator as
    the computer's. Only its digest is kept, with the bot it binds to."""
    config = c.execute("SELECT operator FROM bot_config WHERE bot=?", (bot,)).fetchone()
    if not config or not H.human(c, config["operator"]):
        raise Problem("operator", "This bot has no operator on the roster to own its computer", 409)
    code = secrets.token_urlsafe(32)
    c.execute("INSERT INTO enrollments(code_hash,operator,expires) VALUES(?,?,?)",
              (digest(code), config["operator"], H.shift(H.now(), minutes=ENROLL_MINUTES)))
    c.execute("INSERT INTO cloud_machine_codes VALUES(?,?,?)", (digest(code), bot, H.now()))
    return code


def enrolling(c, code_hash):
    """The bot an enrollment code was minted for, or None for any other code."""
    found = c.execute("SELECT bot FROM cloud_machine_codes WHERE code_hash=?", (code_hash,)).fetchone()
    return found["bot"] if found and row(c, found["bot"]) else None


def enrolled(c, execution, runner_id, bot):
    """The bot's machine's runner is in: it becomes the bot's computer (a machine made again after its disk was
    deleted replaces the old one), and the bot is assigned to it now, or by the manager's next look if it is busy."""
    current = row(c, bot)
    old = current.get("runner_id")
    if old and old != runner_id:
        c.execute("UPDATE runners SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (H.now(), old))
    save(c, bot, runner_id=runner_id)
    H.event(c, H.KEEPER, "cloud_machine.enrolled", bot, {"runner": runner_id})
    bind(c, execution, bot)


def unplaced(c, bot):
    """Whether a cloud-mode bot's machine has a computer the bot is not assigned to yet."""
    return bool(c.execute("SELECT 1 FROM cloud_machines m LEFT JOIN assignments a ON a.bot=m.bot WHERE m.bot=? AND "
                          "m.mode='cloud' AND m.runner_id IS NOT NULL AND a.runner_id IS NOT m.runner_id",
                          (bot,)).fetchone())


def bind(c, execution, bot):
    """Assign a cloud-mode bot to its machine's computer if it is not on it. False while it can't be (a turn still out
    on the old computer): asked again at the next look."""
    current = row(c, bot)
    if not current or current["mode"] != "cloud" or not current["runner_id"]:
        return False
    placed = c.execute("SELECT runner_id,generation FROM assignments WHERE bot=?", (bot,)).fetchone()
    if placed and placed["runner_id"] == current["runner_id"]:
        return True
    from types import SimpleNamespace
    c.execute("SAVEPOINT cloud_bind")
    try:
        execution.assign(c, execution.auth.owner_identity(c), bot, SimpleNamespace(
            runner_id=current["runner_id"], expected_generation=placed["generation"] if placed else 0))
    except Problem:
        c.execute("ROLLBACK TO cloud_bind")
        c.execute("RELEASE cloud_bind")
        return False
    c.execute("RELEASE cloud_bind")
    return True


def placed(c, bot, runner_id, actor):
    """Execution.assign put the bot on a computer: its own machine's keeps cloud mode, any other is the person's
    choice of computer, and the machine stops at the next look."""
    current = row(c, bot)
    if not current:
        return
    mode = "cloud" if runner_id == current["runner_id"] else "computer"
    if mode != current["mode"]:
        save(c, bot, mode=mode, changed_by=actor)


# ----------------------------------------------------------------------------- the policy
def epoch(value):
    at = H.parse_ts(value) if isinstance(value, str) else None
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()


def facts(c, settings, current, at):
    """What `decide` needs about a bot, read from the hub: whether it should be on its machine at all, whether a turn
    is out, its last attempt or watcher activity (epoch seconds), and its first want (a stable key, or None)."""
    from .execution import bot_claimable
    bot = current["bot"]
    found = H.bot(c, bot)
    active = bool(found and found["state"] == "active") and current["mode"] == "cloud"
    drained = c.execute("SELECT 1 FROM bot_control WHERE bot=? AND draining=1", (bot,)).fetchone()
    turns = bool(c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running') LIMIT 1",
                           (bot,)).fetchone())
    latest = c.execute("SELECT max(coalesce(finished,started,created)) FROM attempts WHERE bot=?", (bot,)).fetchone()[0]
    ran = c.execute("SELECT max(finished) FROM watcher_runs WHERE bot=?", (bot,)).fetchone()[0]
    activity = max([t for t in (epoch(latest), epoch(ran)) if t] or [0])
    out = {"active": active and not drained, "turns": turns, "activity": activity, "want": None,
           "frozen": cloud_access.frozen(c), "retry_now": False}
    if current["problem"] == "out_of_credit" and not out["frozen"]:
        said = cloud_access.state(c) or {}
        out["retry_now"] = bool(said.get("checked") and current["problem_at"] and said["checked"] > current["problem_at"])
    if not out["active"]:
        return out
    placed_on = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
    runner_id = current["runner_id"]
    if placed_on and placed_on["runner_id"] != runner_id:
        out["want"] = "move"                 # chosen while on another computer: its machine is made now
        return out
    # Keyed by the newest queued job, so new work wakes a machine the guard holds for older work.
    newest = c.execute("SELECT id FROM jobs WHERE bot=? AND state='queued' AND held=0 ORDER BY created DESC,id DESC "
                       "LIMIT 1", (bot,)).fetchone()
    if newest and runner_id:
        why, _, _, job = bot_claimable(c, settings, runner_id, bot, with_job=True)
        if (why is None and job) or why in STALE:
            out["want"] = "job:" + newest["id"]
    elif newest:
        out["want"] = "job:" + newest["id"]
    if out["want"] is None and runner_id:
        for watcher in c.execute("SELECT name,finished,every FROM watcher_runs WHERE bot=? ORDER BY finished",
                                 (bot,)):
            done = epoch(watcher["finished"])
            if done is not None and watcher["every"] and done + watcher["every"] <= at:
                out["want"] = f"watcher:{watcher['name']}:{watcher['finished']}"
                break
    return out


def decide(current, found, at, idle_s):
    """(action, problem) for one bot: action is None, "create", "start", "heartbeat" or "stop"; problem is what its
    queued work waits on when nothing is done ("stuck"), or None to leave the row's as it is. Pure: the clock is `at`."""
    on = current["state"] in ON and current["machine_id"]
    if not found["active"]:
        # Paused, drained, archived or moved: stopped, but never under a turn still out (a drain lets it finish).
        if on and found["turns"]:
            return ("heartbeat" if at - (current["heartbeat_at"] or 0) >= HEARTBEAT else None), None
        return ("stop" if on else None), None
    if current["next_try"] and at < current["next_try"] and not found["retry_now"]:
        return None, None                                   # waiting out a refusal
    if found["frozen"]:
        return ("stop" if on else None), "out_of_credit"
    if on:
        last = max(found["activity"] or 0, current["active_at"] or 0)
        if not found["turns"] and not current["always_on"] and at - last >= idle_s:
            return "stop", None
        if at - (current["heartbeat_at"] or 0) >= HEARTBEAT:
            return "heartbeat", None
        return None, None
    key = "always_on" if current["always_on"] else found["want"]
    if not key:
        return None, None
    if key not in ("always_on",) and key == current["wake_key"] and not (
            (found["activity"] or 0) > (current["woke_at"] or 0)):
        return None, "stuck"                                # woken for this once already, and nothing ran
    if not current["machine_id"] or current["state"] == "deleted":
        return "create", None
    return "start", None


# ----------------------------------------------------------------------------- the manager
class Manager:
    """Runs `decide` for every cloud-mode bot and carries it out against HQ. `client` is a test's fake HQ; the live one
    is built from the gateway key at each look."""

    def __init__(self, store, settings, execution, vault=None, client=None):
        self.store, self.settings, self.execution, self.vault = store, settings, execution, vault
        self.client = client
        self.lock = threading.Lock()
        self.synced = None

    @property
    def off(self):
        return bool(self.settings.demo or self.settings.rehearsal)

    vault_of = None              # install's: the app's vault, made after this

    def hq(self, c):
        if self.client is not None:
            return self.client
        vault = self.vault or (self.vault_of() if self.vault_of else None)
        return client_of(c, vault, self.settings) if vault is not None else None

    def tick(self):
        if self.off or not self.lock.acquire(blocking=False):
            return
        try:
            self._tick()
        finally:
            self.lock.release()

    def _tick(self):
        at = now()
        with self.store.read() as c:
            if not cloud_company(c, self.settings):
                return
            hq = self.hq(c)
            if hq is None:
                return
        if self.synced is None or at - self.synced >= SYNC:
            self.sync(hq, at)                               # also how this server learns HQ makes machines at all
        with self.store.read() as c:
            if not offered(c, self.settings):
                return
            bots = [r["bot"] for r in c.execute("SELECT bot FROM cloud_machines ORDER BY bot")]
        for bot in bots:
            try:
                self.step(hq, bot, at)
            except Exception:  # noqa: BLE001 - one bot's failure never holds the others
                log.exception("Cloud machine step failed for %s", bot)

    def sync(self, hq, at):
        """HQ's list: every machine's state, size, cost and starts onto its bot's row, and the sizes."""
        self.synced = at
        try:
            listed = hq.list()
        except Refused:
            return None
        with self.store.transaction() as c:
            if listed["sizes"]:
                c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                          "value_json=excluded.value_json",
                          (SIZES_KEY, json.dumps({"sizes": listed["sizes"], "caps": listed["caps"], "at": H.now()})))
            mine = rows(c)
            for machine in listed["machines"]:
                current = mine.get(machine["key"])
                if current and current["machine_id"] in (None, machine["id"]):
                    save(c, machine["key"], **self.described(machine))
        return listed

    @staticmethod
    def described(machine):
        return {"machine_id": machine["id"], "state": machine["state"], "machine_size": machine["size"],
                "started_at": machine["started_at"], "stopped_at": machine["stopped_at"],
                "cost_month_usd": machine["cost_month_usd"], "starts_month": machine["starts_month"],
                "last_error": machine["last_error"]}

    def step(self, hq, bot, at):
        with self.store.read() as c:
            unbound = unplaced(c, bot)
        if unbound:                                         # a write only when there is one to make
            with self.store.transaction() as c:
                bind(c, self.execution, bot)
        with self.store.read() as c:
            current = row(c, bot)
            if not current:
                return None
            found = facts(c, self.settings, current, at)
            idle_s = idle_after(c, current["machine_size"] or current["size"])
        action, problem = decide(current, found, at, idle_s)
        if problem is not None and problem != current["problem"] and not action:
            with self.store.transaction() as c:
                save(c, bot, problem=problem, problem_at=H.now())
        elif problem is None and not action and current["problem"] == "stuck" and found["want"] != current["wake_key"]:
            with self.store.transaction() as c:
                save(c, bot, problem="", problem_at=None)
        if action:
            self.act(hq, current, action, found, at)
        return action

    def act(self, hq, current, action, found, at):
        bot = current["bot"]
        code = None
        if action == "create":
            with self.store.transaction() as c:
                code = mint(c, bot)
        try:
            if action == "create":
                machine = hq.create(bot, current["size"], code, self.settings.runner_url)
            elif action == "start":
                resize = current["size"] if current["size"] != current["machine_size"] else None
                machine = hq.start(current["machine_id"], resize)
            elif action == "stop":
                machine = hq.stop(current["machine_id"])
            else:
                ttl = hq.heartbeat(current["machine_id"])
                with self.store.transaction() as c:
                    save(c, bot, heartbeat_at=at, ttl_s=ttl, failures=0, next_try=None)
                return
        except Refused as refused:
            self.refused(current, action, refused, at)
            return
        fields = self.described(machine)
        if action in ("create", "start"):
            key = "always_on" if current["always_on"] else found["want"]
            fields.update(active_at=at, heartbeat_at=at, wake_key=key, woke_at=at)
        with self.store.transaction() as c:
            save(c, bot, **fields, failures=0, next_try=None, problem="", problem_at=None)
            H.event(c, H.KEEPER, "cloud_machine." + {"create": "created", "start": "started", "stop": "stopped"}[action],
                    bot, {"size": machine["size"], "machine": machine["id"]})

    def refused(self, current, action, refused, at):
        bot = current["bot"]
        fields = {}
        if refused.status == 402:
            fields = {"problem": "out_of_credit", "problem_at": H.now(), "next_try": at + FROZEN_RETRY}
        elif refused.status == 409 and refused.code == "sandbox_stopping":
            fields = {"next_try": at + STOPPING_RETRY}
        elif refused.status == 404 and action != "create":
            # HQ has no such machine (its disk was deleted): the next want makes a new one.
            fields = {"machine_id": None, "state": "deleted", "next_try": None}
        else:
            failures = current["failures"] + 1
            fields = {"failures": failures, "next_try": at + min(BACKOFF_MAX, BACKOFF * 2 ** (failures - 1))}
            if action in ("create", "start"):
                fields.update(problem="hq:" + refused.code, problem_at=H.now())
        with self.store.transaction() as c:
            save(c, bot, **fields)
            if refused.status == 402 and current["problem"] != "out_of_credit" or (
                    "failures" in fields and fields["failures"] == 1):
                H.event(c, H.KEEPER, "cloud_machine.refused", bot, {"action": action, "code": refused.code,
                                                                     "status": refused.status})

    async def loop(self, stop):
        import asyncio
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), TICK)
            except TimeoutError:
                pass
            if stop.is_set():
                return
            try:
                await asyncio.to_thread(self.tick)
            except Exception:  # noqa: BLE001 - the next look tries again
                log.exception("Cloud machines look failed")


# ----------------------------------------------------------------------------- what people see
PROBLEMS = {"out_of_credit": "Out of credit", "stuck": "Started, but its work did not run there"}


def problem_words(problem):
    if not problem:
        return ""
    if problem.startswith("hq:"):
        return f"Didn't start ({problem[3:]}): trying again"
    return PROBLEMS.get(problem, problem)


def waiting(c, bot):
    """What a cloud-mode bot's queued work waits for while its machine is not up (views.queued_label), or ''."""
    current = row(c, bot)
    if not current or current["mode"] != "cloud":
        return ""
    if current["problem"] == "out_of_credit" or cloud_access.frozen(c):
        return "out of credit: its cloud machine can't start"
    if current["problem"].startswith("hq:"):
        return f"its cloud machine didn't start ({current['problem'][3:]}); trying again"
    if current["problem"] == "stuck":
        return "its cloud machine started, but this did not run there"
    return "starting its cloud machine"


def view(c, bot, settings=None):
    """The bot's cloud machine as Settings and the bot's page show it, or None off a cloud company."""
    if not offered(c, settings):
        return None
    from .agents import external_harness
    if external_harness(c, bot):
        return None
    assigned = bool(c.execute("SELECT 1 FROM assignments WHERE bot=?", (bot,)).fetchone())
    return shown(bot, row(c, bot), assigned)


def shown(bot, current, assigned):
    """`view` from a row already read. `mode` is where the bot runs: its own machine (`cloud`) or another computer;
    a bot with no row yet runs where it was put, or on its own machine once it has work. `size` is what the next start
    uses; `machine_size` what HQ last said it has."""
    if current is None:
        return {"mode": "computer" if assigned else "cloud", "size": "default", "always_on": False, "state": "none",
                "machine_size": None, "cost_month_usd": 0.0, "starts_month": 0, "runner_id": None, "problem": ""}
    return {"mode": current["mode"], "size": current["size"], "always_on": bool(current["always_on"]),
            "state": current["state"], "machine_size": current["machine_size"],
            "cost_month_usd": round(current["cost_month_usd"] or 0, 2), "starts_month": current["starts_month"],
            "runner_id": current["runner_id"], "problem": problem_words(current["problem"])}


def change(c, execution, who, bot, mode=None, size=None, always_on=None):
    """A person's choice for the bot: its own machine (`mode` cloud), its size (applied at its next start) and Always
    on. Moving to another computer is Execution.assign's (which sets `computer`). Those who manage the bot only."""
    auth = execution.auth
    if not (auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)):
        raise Problem("forbidden", "You do not manage this bot's computer", 403)
    if not offered(c, execution.store.settings):
        raise Problem("not_cloud", "Cloud machines are for Tico Cloud companies", 409)
    from .agents import external_harness
    if external_harness(c, bot):
        raise Problem("harness", "This bot is run by an external agent; it has no computer", 422)
    if size is not None and size not in size_names(c):
        raise Problem("validation", "Choose one of: " + ", ".join(size_names(c)), 422)
    if mode is not None and mode != "cloud":
        raise Problem("validation", "Choose a computer to move it there; mode only takes cloud", 422)
    c.execute("INSERT OR IGNORE INTO cloud_machines(bot,mode,changed_by,updated) VALUES(?,?,?,?)",
              (bot, "computer" if c.execute("SELECT 1 FROM assignments WHERE bot=?", (bot,)).fetchone() else "cloud",
               who.actor, H.now()))
    before = row(c, bot)
    fields = {"changed_by": who.actor}
    if mode is not None:
        fields.update(mode=mode, wake_key=None, problem="")
    if size is not None:
        fields["size"] = size
    if always_on is not None:
        fields["always_on"] = int(always_on)
    save(c, bot, **fields)
    after = row(c, bot)
    if mode == "cloud":
        bind(c, execution, bot)
    H.event(c, who.actor, "cloud_machine.changed", bot, {
        key: after[key] for key in ("mode", "size", "always_on") if after[key] != before[key]})
    return view(c, bot, execution.store.settings)


def removable(c, auth, who, bot):
    """The row of a machine the bot no longer runs on, for deleting; Problem otherwise. Owner, admin or the bot's
    manager; never while the bot is set to run there."""
    if not (auth.bot_admin(who) or auth.bot_manager(c, who, bot)):
        raise Problem("forbidden", "You do not manage this bot's computer", 403)
    current = row(c, bot)
    if not current:
        raise Problem("not_found", "This bot has no cloud machine", 404)
    if current["mode"] == "cloud":
        raise Problem("in_use", "Move the bot to another computer first", 409)
    return current


def remove(store, hq, who, auth, bot):
    """Delete the bot's machine at HQ (its disk; the month's cost stays billed) and the computer it was; the bot keeps
    the computer it is on. HQ first: a failure there leaves everything as it was, to try again."""
    with store.read() as c:
        current = removable(c, auth, who, bot)
    if current["machine_id"]:
        if hq is None:
            raise Problem("gateway", "Connect Tico Gateway first", 409)
        try:
            hq.delete(current["machine_id"])
        except Refused as refused:
            if refused.status != 404:
                raise Problem("gateway", "Tico did not answer. Try again in a minute." if refused.status == 0
                              else f"Tico refused this ({refused.code}).", 502) from None
    with store.transaction() as c:
        current = removable(c, auth, who, bot)
        if current["runner_id"]:
            c.execute("UPDATE runners SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (H.now(), current["runner_id"]))
        c.execute("DELETE FROM cloud_machines WHERE bot=?", (bot,))
        # Its row goes, and with it the default: the bot stays where it was put, so it is not made again.
        c.execute("INSERT INTO cloud_machines(bot,mode,changed_by,updated) VALUES(?,?,?,?)",
                  (bot, "computer", who.actor, H.now()))
        H.event(c, who.actor, "cloud_machine.deleted", bot, {"machine": current["machine_id"]})
    return {"ok": True}


def health(c, settings=None):
    """Health's row, or None: some bot's machine could not be started (out of credit, or HQ refusing)."""
    if not offered(c, settings):
        return None
    stuck = [r["bot"] for r in c.execute("SELECT bot,problem FROM cloud_machines WHERE mode='cloud' AND problem<>''")]
    if not stuck:
        return None
    return {"status": "warn", "summary": f"{len(stuck)} bot{'s' if len(stuck) != 1 else ''} waiting on a cloud machine "
                                         f"that did not start: {', '.join(stuck[:5])}"}


# ----------------------------------------------------------------------------- routes
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402 - with the routes that use them
from fastapi import Request  # noqa: E402


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str | None = Field(None, max_length=20)
    size: str | None = Field(None, max_length=20)
    always_on: bool | None = None


def install(app, settings, store, auth, execution, mutate):
    service = Manager(store, settings, execution)
    service.vault_of = lambda: getattr(app.state, "vault", None)
    app.state.cloud_machines = service

    @app.get("/api/v2/cloud-machines")
    def cloud_machine_sizes(request: Request):
        """Whether bots here may run on their own cloud machine, and the sizes HQ offers (its customer price per
        hour, one start's price, and the idle time after which it stops). `offered` false off Tico Cloud."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if not offered(c, settings):
                return {"offered": False, "sizes": [], "caps": {}}
            from .read_cache import metadata
            found = metadata(c, SIZES_KEY)
            try:
                caps = (json.loads(found[0]) or {}).get("caps") or {} if found else {}
            except (ValueError, TypeError, AttributeError):
                caps = {}
            return {"offered": True, "sizes": sizes(c) or [{"name": n} for n in SIZES], "caps": caps}

    @app.post("/api/v2/bots/{bot}/cloud-machine")
    def cloud_machine_set(bot: str, request: Request, body: Change):
        """Where the bot runs and how: `mode` cloud puts it on its own cloud machine (another computer is chosen with
        a machine transition), `size` applies at the machine's next start, `always_on` keeps it running. Those who
        manage the bot only; Tico Cloud companies only."""
        who = request.state.identity
        auth.domain(who)
        return mutate(request, body, lambda c: {"cloud_machine": change(
            c, execution, who, _known(c, bot), body.mode, body.size, body.always_on)})

    @app.delete("/api/v2/bots/{bot}/cloud-machine")
    def cloud_machine_delete(bot: str, request: Request):
        """Delete the bot's cloud machine (its disk) and the computer it was, once the bot runs elsewhere."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            _known(c, bot)
            hq = service.hq(c)
        return remove(store, hq, who, auth, bot)


def _known(c, bot):
    if not H.bot(c, bot):
        raise Problem("not_found", "No such bot", 404)
    return bot
