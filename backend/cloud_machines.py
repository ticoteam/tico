"""Tico cloud machines (phase B): the bots of a Tico Cloud company run on cloud machines, started when there is work
and stopped when there is none. Light bots share one machine per company; heavy ones (developers, the video producer)
each get their own.

A machine is a boat.dev VM Tico HQ owns (tico-services); HQ keeps the boat key, billing, caps, the boat-side ttl and the
14-day keep-alive. This module is the server's half: it decides *when*. The contract (same key and auth as
`<Gateway URL>/billing`, JSON both ways, the Gateway error shape, `402 {"error": "frozen"|"locked"}` when credit is out):

    GET    machines                  {machines: [Machine], sizes: [Size], caps}
    POST   machines                  {key, size, enroll_token, hub_url} -> Machine   (idempotent on key)
    POST   machines/<id>/start       {size?} -> Machine      resume; a size resizes on the same disk
    POST   machines/<id>/stop        -> Machine
    POST   machines/<id>/heartbeat   -> {ttl_s}              every minute while this server wants it on
    DELETE machines/<id>             -> {ok}                 deletes the disk

    Machine {id, key, size, state: starting|running|stopping|stopped|deleted|failed, started_at, stopped_at,
             cost_month_usd, starts_month, last_error}
    Size    {name, cpu, memory_gb, usd_per_hour (the customer's price), start_usd, idle_stop_s (break-even)}

`key` is a bot's id for its own machine and SHARED (`_shared`, never a bot id: ids are [a-z0-9-]) for the company's
shared one, so each is made once. HQ counts the shared machine as one machine against its caps.

One row per bot (`cloud_machines`): where it runs (`mode` cloud, or computer: any other Computer), on which cloud
machine (`placement` shared, the default, or own), its own machine's size and Always on, and that machine as HQ last
described it. The shared machine is one more row, keyed SHARED, with the company's size and Always on for it. A bot's
placement defaults by role when its row is made (`default_placement`): `own` for a bot from a template in OWN_TEMPLATES
(the video producer) or in the Engineering department of the team chart, `shared` for every other; a person switches
it either way. Off a cloud company (cloud_access.state is None) there are no rows, the option is hidden and nothing here
runs; on one, the same until HQ has listed its sizes (`offered`). On one, a bot with no computer defaults to a cloud
machine (placement.auto_place; a setup that finished before HQ listed its sizes is placed when they come,
Onboarding.on_cloud_offered); choosing another computer for it (Execution.assign) sets `computer`, and another bot's
own machine is never one to choose (`check_host`).

A machine's runner is a normal Computer, enrolled with a one-time code minted here (the add-a-runner flow,
`enrollments`) that HQ hands the machine and never keeps. Execution.enroll recognises the code (`cloud_machine_codes`)
and names the computer: "<bot> (cloud)", owned by the bot's operator and closed to other bots, or "Shared cloud
machine", owned by the company owner and open to members' bots. Neither is ever placed by placement or onboarding:
each bot whose machine it is gets assigned to it (`bind`), now or at the manager's next look if a turn still holds it
elsewhere. A bot joining or leaving the shared machine is an assignment; the machine is not restarted for it. Bots on
the shared machine are one trust group (one disk, one user with sudo), like the bots sharing any computer; the runner's
own turns-at-once limit keeps one from crowding the rest.

The manager (`Manager.tick`, every TICK seconds on its own thread, never inside a transaction while HQ answers), per
machine; a shared machine's facts are its member bots' together:

    want     a bot of the machine is active and in cloud mode, and has a turn out, claimable queued work (the claim's
             own rule, execution.bot_claimable, on the machine's last report; a reason only a stopped machine leaves
             stale wakes it too), a watcher due (its last run plus its interval), or it waits to move here; or the
             machine is Always on. On the shared machine any member's want wakes it, the newest work first. Failing
             those, a stopped machine whose computer reports an older release than this server's is wanted to update
             (`update:<release>`, `runner_behind`: never for a blank, dev or unparsed release, nor pinned or blocked),
             once its `spread` (0..UPDATE_SPREAD_S, per runner and release) after this server first ran the release
             has passed (`update_due`): a release on a shared host starts no storm of machines at once.
    wake     a stopped machine with a want is started (created on first use); the size set since applies here
    keep     a running machine gets a heartbeat every HEARTBEAT seconds; HQ's ttl stops it if they stop coming
    idle     with no turn out on it, and no attempt or watcher activity on it for the size's `idle_stop_s` since the
             later of its start, its last activity and its want (a job queued for it, a watcher due: work its runner
             has not taken yet), it is stopped, unless Always on: the shared one only once every member is idle. A
             watcher running often keeps it on by itself, and so does a runner still updating (`runner_updating`).
             With no active bot left on it, it stops, once no turn is out and no update under way: every stop but out
             of credit's waits for both.
    guard    a machine woken for one want that saw no activity is not woken again for that same want: a job its
             runner never takes would otherwise start (4 cents) and idle-stop it forever. New work wakes it. An update
             wake is held the same way, for its release, and flags nothing: no bot's work waits on it.
    enroll   a machine being made has until its newest code's expiry (ENROLL_MINUTES, plus ENROLL_GRACE) to enroll; it
             is not idle-stopped meanwhile. One that has not by then never can (a resume gets no new code), and it has
             nothing worth keeping: it is deleted, and the next want makes a new one with a fresh code. After
             ENROLL_TRIES such machines in a row nothing more is made ("no_enroll") until one enrolls or a person
             changes the machine's settings.
    frozen   out of credit (cloud_access.frozen, or HQ's 402): nothing starts and the bot shows "Out of credit". HQ
             stops running machines and keeps their disks. A 402 is asked again once HQ says the company is open.
    errors   HQ not answering or refusing: the bot's queued work says so ("its cloud machine didn't start"), and the
             call is retried with backoff (BACKOFF to BACKOFF_MAX); one history event per streak, not per retry.
    fence    `decide` reads a snapshot, and a claim can lease work between it and HQ's stop. So a stop or delete is
             reserved first (`reserve`), in a write transaction, which the claim's own write serializes with: `decide`
             again on the facts read under that lock, and no turn leased or running on the machine's computer, or it
             is not sent. The reservation (`stop_until`, STOP_HOLD from the clock read under that lock) and HQ's
             stopping or stopped keep the claim from handing that computer work (`held`), every member bot's on the
             shared one, and its company-wide waiters too. HQ saying it did not stop lets it go at once; an answer
             that leaves it open (`stopped_unknown`) keeps it until HQ's next list says, and a server that died
             mid-stop lets it lapse.

`decide` is the whole policy as a pure function of the row, the facts and the clock, so the tests drive it directly.
"""
import hashlib
import json
import logging
import secrets
import threading
import time
from datetime import timezone

import httpx

from clients import gateway as G
from clients.gateway import user_agent

from . import cloud_access, outbound_tls, releases
from .store import H, Problem, digest

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_machines(
 bot TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'cloud', placement TEXT NOT NULL DEFAULT 'shared',
 size TEXT NOT NULL DEFAULT 'default',
 always_on INTEGER NOT NULL DEFAULT 0, machine_id TEXT, runner_id TEXT, state TEXT NOT NULL DEFAULT 'none',
 machine_size TEXT, started_at TEXT, stopped_at TEXT, cost_month_usd REAL NOT NULL DEFAULT 0,
 starts_month INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
 active_at REAL, heartbeat_at REAL, ttl_s INTEGER, next_try REAL, failures INTEGER NOT NULL DEFAULT 0,
 problem TEXT NOT NULL DEFAULT '', problem_at TEXT, wake_key TEXT, woke_at REAL,
 enroll_by REAL, enroll_fails INTEGER NOT NULL DEFAULT 0, changed_by TEXT, updated TEXT, stop_until REAL);
CREATE TABLE IF NOT EXISTS cloud_machine_codes(
 code_hash TEXT PRIMARY KEY, bot TEXT NOT NULL, created TEXT NOT NULL);
"""
MODES = ("cloud", "computer")
PLACEMENTS = ("shared", "own")
SHARED = "_shared"              # the shared machine's row and HQ key: no bot id has an underscore
OWN_TEMPLATES = ("video-producer",)     # heavy work (renders, encodes) whatever department the bot is in
OWN_DEPARTMENTS = ("engineering",)      # developers: builds, tests and toolchains of their own
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
ENROLL_GRACE = 60               # past its newest code's expiry, a machine not enrolled yet never will be
ENROLL_TRIES = 3                # machines made in a row that never enrolled before it waits for a person
STOP_HOLD = 120                 # a stop's reservation lapses this late: a server that died mid-stop strands no work
DEAD = ("deleted", "failed")     # HQ's word for a machine that is gone or never got a computer: a want makes a new one
GOING = ("stopping", "stopped")  # HQ's word for a machine that is going down, or down: its runner takes no new turn
UPDATE_WANT = "update:"          # a stopped machine's want to update, keyed "update:" and this server's release
UPDATE_STALE = 15 * 60           # an update its runner reports with no progress this long has failed (runner-rs follow.rs)
UPDATE_SPREAD_S = 600            # update wakes spread over this long after a release (`update_due`): no start storm
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
    """Whether bots here may run on cloud machines: a Tico Cloud company whose HQ has listed machine sizes.
    Until HQ answers GET machines, nothing defaults to a machine HQ cannot make, and the option stays hidden."""
    return cloud_company(c, settings) and bool(sizes(c))


def row(c, bot):
    found = c.execute("SELECT * FROM cloud_machines WHERE bot=?", (bot,)).fetchone()
    return dict(found) if found else None


def rows(c):
    return {r["bot"]: dict(r) for r in c.execute("SELECT * FROM cloud_machines")}


def save(c, key, **fields):
    fields["updated"] = H.now()
    c.execute("UPDATE cloud_machines SET " + ",".join(k + "=?" for k in fields) + " WHERE bot=?",
              (*fields.values(), key))


def default_placement(c, bot):
    """Where a bot's cloud machine is when nobody has chosen: its own for a template in OWN_TEMPLATES or a bot in an
    OWN_DEPARTMENTS department of the team chart (its group, or its manager's up the chain), the shared one for the
    rest. Read once, when its row is made: moving it on the chart later moves no machine."""
    found = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    try:
        config = json.loads(found["config_json"] or "{}") if found else {}
    except ValueError:
        config = {}
    if isinstance(config, dict) and config.get("template") in OWN_TEMPLATES:
        return "own"
    from .task_manager import Chart
    return "own" if Chart(c).of("bot:" + bot) in OWN_DEPARTMENTS else "shared"


def adopt(c, bot, actor=H.KEEPER):
    """A bot of a cloud company with no computer and no choice yet: a cloud machine (the default), shared or its own by
    its role. Returns whether it is in cloud mode now. A bot someone moved to a computer keeps that; an external agent's
    has no computer at all."""
    from .agents import external_harness
    if external_harness(c, bot):
        return False
    current = row(c, bot)
    if current is None:
        placement = default_placement(c, bot)
        c.execute("INSERT OR IGNORE INTO cloud_machines(bot,placement,changed_by,updated) VALUES(?,?,?,?)",
                  (bot, placement, actor, H.now()))
        if placement == "shared":
            ensure_shared(c)
        current = row(c, bot)
    return current["mode"] == "cloud"


def ensure_shared(c):
    c.execute("INSERT OR IGNORE INTO cloud_machines(bot,mode,placement,changed_by,updated) VALUES(?,?,?,?,?)",
              (SHARED, "cloud", "shared", H.KEEPER, H.now()))


def members(c):
    """The bots set to run on the shared machine, in id order."""
    return [r["bot"] for r in c.execute("SELECT bot FROM cloud_machines WHERE bot<>? AND mode='cloud' AND "
                                        "placement='shared' ORDER BY bot", (SHARED,))]


def hosted(c, key):
    """The bots set to run on machine `key`: the shared machine's members, or the bot whose own machine it is."""
    if key == SHARED:
        return members(c)
    current = row(c, key)
    return [key] if current and current["mode"] == "cloud" and current["placement"] == "own" else []


def home(c, bot, current=None):
    """The row of the machine a cloud-mode bot runs on (its own, or the shared one), or None in computer mode."""
    current = current or row(c, bot)
    if not current or current["mode"] != "cloud":
        return None
    return row(c, SHARED) if current["placement"] == "shared" else current


def cloud_runners(c, but=None):
    """Computer ids that are a cloud machine (other than `but`'s own): never offered as a computer to choose. A bot
    goes on the shared one by choosing it as its cloud machine."""
    return {r["runner_id"] for r in c.execute("SELECT bot,runner_id FROM cloud_machines WHERE runner_id IS NOT NULL")
            if r["bot"] != but}


def label_for(c, key):
    if key == SHARED:
        return "Shared cloud machine"
    found = H.bot(c, key)
    return f"{(found or {}).get('display_name') or key} (cloud)"[:100]


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
def mint(c, key, owner=None):
    """A one-time runner enrollment code for machine `key`: the add-a-runner flow's, with the bot's operator as the
    computer's, or `owner` (the company's) for the shared machine. Only its digest is kept, with the key it binds to."""
    if key == SHARED:
        operator = owner
    else:
        config = c.execute("SELECT operator FROM bot_config WHERE bot=?", (key,)).fetchone()
        operator = config["operator"] if config else None
    if not operator or not H.human(c, operator):
        raise Problem("operator", "Nobody on the roster to own this cloud machine's computer", 409)
    code = secrets.token_urlsafe(32)
    c.execute("INSERT INTO enrollments(code_hash,operator,expires) VALUES(?,?,?)",
              (digest(code), operator, H.shift(H.now(), minutes=ENROLL_MINUTES)))
    c.execute("INSERT INTO cloud_machine_codes VALUES(?,?,?)", (digest(code), key, H.now()))
    return code


def awaiting(c, key, at):
    """A code was just minted for machine `key` at `at`: the machine HQ is making must enroll by that code's expiry.
    `enroll_by` is that deadline while a machine is being made, 0 once it has enrolled, and NULL when the row's machine
    is gone (deleted here or at HQ, or never made). A create retried after a lost answer mints another code but gets
    the same machine back (HQ is idempotent on key): one that enrolled meanwhile stays enrolled (0), and one still
    booting may use either code, so the newer expiry counts."""
    if row(c, key)["enroll_by"] != 0:
        save(c, key, enroll_by=at + ENROLL_MINUTES * 60 + ENROLL_GRACE)


def enrolling(c, code_hash):
    """The machine key (a bot, or SHARED) an enrollment code was minted for, or None for any other code."""
    found = c.execute("SELECT bot FROM cloud_machine_codes WHERE code_hash=?", (code_hash,)).fetchone()
    return found["bot"] if found and row(c, found["bot"]) else None


def opens(key):
    """Whether machine `key`'s computer takes members' bots (`accepts_member_bots`): the shared one hosts everyone's."""
    return key == SHARED


def enrolled(c, execution, runner_id, key):
    """Machine `key`'s runner is in: it becomes that machine's computer (a machine made again after its disk was
    deleted replaces the old one), and each bot set to run there is assigned to it now, or by the manager's next look
    if it is busy."""
    current = row(c, key)
    old = current.get("runner_id")
    if old and old != runner_id:
        c.execute("UPDATE runners SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (H.now(), old))
    save(c, key, runner_id=runner_id, enroll_by=0, enroll_fails=0)
    H.event(c, H.KEEPER, "cloud_machine.enrolled", key, {"runner": runner_id})
    for bot in hosted(c, key):
        bind(c, execution, bot)
    from . import model_subscriptions as MS
    for bot in hosted(c, key):                  # a new computer, even while its bot's last turn is still out elsewhere
        chooser = (row(c, bot) or {}).get("changed_by") or ""
        if MS.may_grant(c, execution.auth, chooser):
            lend_plans(c, execution, bot, runner_id, chooser, new=True)


def unplaced(c, bot):
    """Whether a cloud-mode bot's machine has a computer the bot is not assigned to yet."""
    target = (home(c, bot) or {}).get("runner_id")
    if not target:
        return False
    placed_on = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
    return not placed_on or placed_on["runner_id"] != target


TEMPORARY_REFUSALS = ("busy", "removing", "version_conflict")


def bind(c, execution, bot, lasting=False):
    """Assign a cloud-mode bot to its machine's computer if it is not on it. False while it can't be (a turn still out
    on the old computer): asked again at the next look. With `lasting` (a person's choice of a machine), any other
    refusal raises its Problem; the looks keep it to False."""
    target = (home(c, bot) or {}).get("runner_id")
    if not target:
        return False
    placed_on = c.execute("SELECT runner_id,generation FROM assignments WHERE bot=?", (bot,)).fetchone()
    if placed_on and placed_on["runner_id"] == target:
        return True
    from types import SimpleNamespace
    c.execute("SAVEPOINT cloud_bind")
    try:
        execution.assign(c, execution.auth.owner_identity(c), bot, SimpleNamespace(
            runner_id=target, expected_generation=placed_on["generation"] if placed_on else 0))
    except Problem as exc:
        c.execute("ROLLBACK TO cloud_bind")
        c.execute("RELEASE cloud_bind")
        if lasting and exc.code not in TEMPORARY_REFUSALS:
            raise
        return False
    c.execute("RELEASE cloud_bind")
    return True


def check_host(c, bot, runner_id):
    """Execution.assign's check, whoever assigns: a bot's own cloud machine's computer takes that bot only. Placement
    never offers it to another, and this keeps the assignment API from putting another bot there. The shared machine's
    takes any bot: choosing it is choosing the shared cloud machine (`placed`)."""
    found = c.execute("SELECT bot FROM cloud_machines WHERE runner_id=?", (runner_id,)).fetchone()
    if found and found["bot"] not in (bot, SHARED):
        raise Problem("cloud_machine", "That computer is another bot's own cloud machine. Choose the shared cloud "
                      "machine or another computer", 409)


def lend_plans(c, execution, bot, runner_id, chooser, new=False):
    """An admin's choice put `bot` on its cloud machine's computer: `chooser` is that admin, already checked. Called at
    only two moments, an admin's request (`change`) and a new computer's enrollment (`enrolled`), and never from a look,
    so nothing a member does later brings back a plan an admin took off. That computer takes the company's plan for each
    runtime the bot runs there when it has no plan or sign-in of its own (model_subscriptions.lend). On a computer that
    isn't `new`, a runtime another bot there already runs is left: its first bot decided. No other computer is touched.
    The shared machine's computer is first in the order a plan is picked from."""
    from . import model_subscriptions as MS
    if (home(c, bot) or {}).get("runner_id") != runner_id:
        return
    settings = execution.store.settings
    shared = (row(c, SHARED) or {}).get("runner_id")
    others = [r["runner_id"] for r in c.execute("SELECT runner_id FROM cloud_machines WHERE runner_id IS NOT NULL "
                                                "AND bot<>? ORDER BY bot", (SHARED,))]
    fellows = [] if new else [r["bot"] for r in c.execute("SELECT bot FROM assignments WHERE runner_id=? AND bot<>? "
                                                          "ORDER BY bot", (runner_id, bot))]
    taken = {runtime for fellow in fellows for runtime in MS.bot_runtimes(c, settings, fellow)}
    for runtime in MS.bot_runtimes(c, settings, bot):
        if runtime not in taken:
            MS.lend(c, runner_id, runtime, chooser, shared, others)


def placed(c, bot, runner_id, actor):
    """Execution.assign put the bot on a computer: its own machine's keeps cloud mode on its own, the shared one's on
    the shared one, and any other is the person's choice of computer; a machine left without its bot stops at the next
    look. A bot with no row yet that is put on the shared machine's computer gets one there."""
    current = row(c, bot)
    if not current:
        shared = row(c, SHARED)
        if shared and shared["runner_id"] and runner_id == shared["runner_id"]:
            c.execute("INSERT OR IGNORE INTO cloud_machines(bot,mode,placement,changed_by,updated) VALUES(?,?,?,?,?)",
                      (bot, "cloud", "shared", actor, H.now()))
        return
    shared = row(c, SHARED) if runner_id != current["runner_id"] else None
    if runner_id == current["runner_id"]:
        fields = {"mode": "cloud", "placement": "own"}
    elif shared and runner_id == shared["runner_id"]:
        fields = {"mode": "cloud", "placement": "shared"}
    else:
        fields = {"mode": "computer"}
    if any(current[k] != v for k, v in fields.items()):
        save(c, bot, **fields, changed_by=actor)


# ----------------------------------------------------------------------------- the policy
def epoch(value):
    at = H.parse_ts(value) if isinstance(value, str) else None
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()


def facts(c, settings, current, at):
    """What `decide` needs about machine `current` (a bot's own, or the shared one), read from the hub: whether any
    bot of it should be on it at all, whether a turn is out on it, the last attempt or watcher activity on it (epoch
    seconds), its first want (a stable key, or None): the newest claimable job of any of its bots, else a bot
    waiting to move here, else a watcher due, and `want_at`, the latest moment any of its bots' wants arose (epoch
    seconds; 0 for none, or a move). `update`: a stopped machine's want to update to this server's release
    (`runner_behind`), or None; `updating`: its runner is part-way through an update (`runner_updating`)."""
    key, runner_id = current["bot"], current["runner_id"]
    bots = hosted(c, key)
    # Turns and activity on its computer: its bots', and a bot's that just left with a turn still out there.
    seen = set(bots) | ({r["bot"] for r in c.execute("SELECT bot FROM assignments WHERE runner_id=?", (runner_id,))}
                        if runner_id else set())
    turns, activity = False, 0
    for bot in sorted(seen) if runner_id else ():
        turns = turns or bool(c.execute("SELECT 1 FROM attempts WHERE bot=? AND runner_id=? AND state IN "
                                        "('leased','running') LIMIT 1", (bot, runner_id)).fetchone())
        latest = c.execute("SELECT max(coalesce(finished,started,created)) FROM attempts WHERE bot=? AND runner_id=?",
                           (bot, runner_id)).fetchone()[0]
        ran = c.execute("SELECT max(finished) FROM watcher_runs WHERE bot=? AND runner_id=?",
                        (bot, runner_id)).fetchone()[0]
        activity = max([activity] + [t for t in (epoch(latest), epoch(ran)) if t])
    out = {"active": False, "turns": turns, "activity": activity, "want": None, "want_at": 0,
           "frozen": cloud_access.frozen(c), "retry_now": False, "update": None, "updating": False}
    if current["problem"] == "out_of_credit" and not out["frozen"]:
        said = cloud_access.state(c) or {}
        out["retry_now"] = bool(said.get("checked") and current["problem_at"] and said["checked"] > current["problem_at"])
    best = None
    for bot in bots:
        active, want, rank, since = bot_want(c, settings, bot, runner_id, at)
        out["active"] = out["active"] or active
        out["want_at"] = max(out["want_at"], since)
        if want and (best is None or rank > best[0]):
            best = (rank, want)
    out["want"] = best[1] if best else None
    if out["active"] and runner_id and current["state"] == "stopped" and current["machine_id"] \
            and runner_behind(c, runner_id) and at >= update_due(c, runner_id, releases.version()):
        out["update"] = UPDATE_WANT + releases.version()
    out["updating"] = bool(runner_id) and runner_updating(c, runner_id, at)
    return out


def runner_behind(c, runner_id):
    """Whether computer `runner_id` reports a release older than this server's and will follow the server's: not one its
    owner pinned, nor one whose update to this same release failed or was blocked (a failed one waits hours before it
    tries again, a blocked one until its checkout changes: a wake now would be spent on nothing). A block names the
    release it stopped at, so a newer release wakes the machine once more: what blocked it may be gone by then. A blank
    or unparsed release, and a development server, are never behind: nothing is woken for what cannot be compared."""
    found = c.execute("SELECT release,update_state,update_target FROM runner_versions WHERE runner_id=?",
                      (runner_id,)).fetchone()
    have, want = releases.parse(found["release"]) if found else None, releases.parse(releases.version())
    if not (have and want and have < want) or found["update_state"] == "pinned":
        return False
    target = releases.parse(found["update_target"])
    if found["update_state"] == "blocked":
        return bool(target and target < want)
    return not (found["update_state"] in ("failed", "rolled_back") and target == want)


def spread(runner_id, release):
    """A machine's update wake's offset, 0..UPDATE_SPREAD_S seconds: the same for a computer and a release, whatever
    restarts, and different across computers. Keyed by the runner (unique across companies), not the machine's key:
    every company's shared machine is SHARED, and those would all wake together."""
    return int.from_bytes(hashlib.sha256(f"{runner_id}:{release}".encode()).digest()[:8], "big") % UPDATE_SPREAD_S


def update_due(c, runner_id, release):
    """When computer `runner_id`'s machine may be woken to update to `release` (epoch seconds): its `spread` after this
    server first ran that release (releases.history, written at startup, so a restart does not move it). A release
    landing on a shared host updates many companies at once, and their stopped machines would all start together
    against the one boat account's per-minute start limits. A release with no history (none recorded yet) waits for
    nothing: there is no moment to spread from."""
    since = next((epoch(r.get("since")) for r in reversed(releases.history(c)) if r.get("version") == release), None)
    return since + spread(runner_id, release) if since else 0


def covers(woke_for, key):
    """Whether a machine woken for `woke_for` was woken for `key`: an update wake names the work want it stood in for
    ("update:0.5.21 job:..."), so neither is woken for again while nothing runs."""
    return bool(key) and key in (woke_for or "").split(" ")


def wake_for(current, found):
    """(key, stuck) for a stopped machine: what to wake it for, or None, and whether its bots' work waits on a wake
    already spent. Always on first, then its work, then an update. Work woken for once with nothing run is not woken
    for again; an update can still stand in for it (its runner may be too old to take the work), once per release."""
    if current["always_on"]:
        return "always_on", False
    fresh = (found["activity"] or 0) > (current["woke_at"] or 0)
    spent = lambda k: covers(current["wake_key"], k) and current["state"] not in DEAD and not fresh   # noqa: E731
    want, update = found["want"], found.get("update")
    if want and not spent(want):
        return want, False
    if update and not spent(update):
        return (update + " " + want) if want else update, False
    return None, bool(want)


def runner_updating(c, runner_id, at):
    """Whether computer `runner_id` reports an update under way that is still fresh. One that has gone quiet for
    UPDATE_STALE is a dead runner's last word, and holds nothing up."""
    found = c.execute("SELECT reported FROM runner_versions WHERE runner_id=? AND update_state='updating'",
                      (runner_id,)).fetchone()
    return bool(found) and at - (epoch(found["reported"]) or 0) < UPDATE_STALE


def bot_want(c, settings, bot, runner_id, at):
    """(active, want, rank, since) for one bot of a machine whose computer is `runner_id` (None before its first
    enroll). `rank` orders wants across the shared machine's bots: newer work first, then a move, then a watcher.
    `since` is when the want arose (the job's queueing, the watcher's due time; 0 for a move or none), epoch seconds."""
    from .execution import bot_claimable
    found = H.bot(c, bot)
    if not (found and found["state"] == "active") or c.execute(
            "SELECT 1 FROM bot_control WHERE bot=? AND draining=1", (bot,)).fetchone():
        return False, None, None, 0
    placed_on = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
    if placed_on and placed_on["runner_id"] != runner_id:
        return True, "move:" + bot, (1, ""), 0      # chosen while on another computer: its machine is made now
    # Keyed by the newest queued job, so new work wakes a machine the guard holds for older work.
    newest = c.execute("SELECT id,created FROM jobs WHERE bot=? AND state='queued' AND held=0 "
                       "ORDER BY created DESC,id DESC LIMIT 1", (bot,)).fetchone()
    if newest and runner_id:
        why, _, _, job = bot_claimable(c, settings, runner_id, bot, with_job=True)
        if (why is None and job) or why in STALE:
            return True, "job:" + newest["id"], (2, newest["created"]), epoch(newest["created"]) or 0
    elif newest:
        return True, "job:" + newest["id"], (2, newest["created"]), epoch(newest["created"]) or 0
    if runner_id:
        for watcher in c.execute("SELECT name,finished,every FROM watcher_runs WHERE bot=? ORDER BY finished", (bot,)):
            done = epoch(watcher["finished"])
            if done is not None and watcher["every"] and done + watcher["every"] <= at:
                return True, f"watcher:{bot}:{watcher['name']}:{watcher['finished']}", (0, ""), done + watcher["every"]
    return True, None, None, 0


def decide(current, found, at, idle_s):
    """(action, problem) for one machine: action is None, "create", "start", "heartbeat", "stop" or "delete" (a
    machine that never enrolled, made again at the next want); problem is what its bots' queued work waits on when
    nothing is done ("stuck", "no_enroll"), or None to leave the row's as it is. Pure: the clock is `at`. `found` is
    `facts`: on the shared machine every member's at once, so any want wakes it and only all idle stop it."""
    on = current["state"] in ON and current["machine_id"]
    # A machine being made that has not enrolled yet (enroll_by is its deadline; 0 once it enrolled).
    booting = bool(current["enroll_by"] and current["machine_id"] and current["state"] not in DEAD)
    if not found["active"]:
        # Paused, drained, archived or moved (every member, on the shared one): stopped, but never under a turn still
        # out (a drain lets it finish), nor mid-update (a cut-short update is a broken runner at the next start). Only
        # out of credit and a person's delete stop it under one: HQ stops a frozen company's machines itself (and the
        # fence, `reserve`, still sends no stop while an attempt is leased or running there).
        if found["frozen"]:
            return ("stop" if on else None), "out_of_credit"
        if on and (found["turns"] or found["updating"]):
            return ("heartbeat" if at - (current["heartbeat_at"] or 0) >= HEARTBEAT else None), None
        return ("stop" if on else None), None
    if current["next_try"] and at < current["next_try"] and not found["retry_now"]:
        return None, None                                   # waiting out a refusal
    if found["frozen"]:
        return ("stop" if on else None), "out_of_credit"
    if booting and at >= current["enroll_by"]:
        return "delete", None                               # its code expired unused: it can never enroll
    if on:
        # Work queued for it that its runner has not taken yet is activity: stopping now would only start it again.
        # So is an update under way: stopping it would cut the update short, and the wake for it would be spent.
        last = max(found["activity"] or 0, current["active_at"] or 0, found["want_at"] or 0)
        if not found["turns"] and not found["updating"] and not current["always_on"] and not booting \
                and at - last >= idle_s:
            return "stop", None
        if at - (current["heartbeat_at"] or 0) >= HEARTBEAT:
            return "heartbeat", None
        return None, None
    key, stuck = wake_for(current, found)
    if not key:
        # Woken for this once already, and nothing ran. A spent update flags nothing: no bot's work waits on it.
        return None, ("stuck" if stuck else None)
    # (A dead machine never ran it: the next is made, and enroll_fails caps how many.)
    if not current["machine_id"] or current["state"] in DEAD:
        if current["enroll_fails"] >= ENROLL_TRIES:
            return None, "no_enroll"                        # its last machines never enrolled: a person looks first
        return "create", None
    return "start", None


def reserve(c, settings, key, action, machine_id):
    """Hold machine `key` for a stop or delete (`action`) before HQ is asked, in the write transaction a claim also
    takes, so the two serialize: the row as it is now, or None to send nothing. `decide` must still say `action` on
    the facts read here, at the clock read here (a look's own clock may be minutes old by now: HQ's list and other
    machines' calls come first), and no attempt may be leased or running on its computer, whichever bot's: a claim
    that came first has its turn out, and one that comes after finds the reservation (`held`) and takes nothing. The
    reservation lapses STOP_HOLD after this clock."""
    at = now()
    current = row(c, key)
    if not current or current["machine_id"] != machine_id:
        return None
    found = facts(c, settings, current, at)
    if decide(current, found, at, idle_after(c, current["machine_size"] or current["size"]))[0] != action:
        return None
    if current["runner_id"] and c.execute("SELECT 1 FROM attempts WHERE runner_id=? AND state IN ('leased','running') "
                                          "LIMIT 1", (current["runner_id"],)).fetchone():
        return None
    save(c, key, stop_until=at + STOP_HOLD)
    return {**current, "stop_until": at + STOP_HOLD}


def held(c, runner_id):
    """Why the claim gives computer `runner_id` no new turn, or "": it is a cloud machine reserved for a stop
    (`reserve`, until its reservation lapses) or one HQ says is stopping or stopped. Any other computer: ""."""
    found = c.execute("SELECT state,stop_until FROM cloud_machines WHERE runner_id=? LIMIT 1", (runner_id,)).fetchone()
    if not found or not (found["state"] in GOING or (found["stop_until"] or 0) > now()):
        return ""
    return "This cloud machine is stopping. Its bots' work waits for it to start again."


def stopped_unknown(refused):
    """Whether HQ's answer to a stop or delete leaves open that the machine is going down: no answer or one this
    server could not read (status 0: a timeout, an oversized or malformed answer), HQ's own failure (5xx), out of
    credit (402: HQ stops it itself) or already stopping (409 sandbox_stopping). Any other 4xx is HQ saying it did
    not, and a 404 that it has no such machine."""
    return (refused.status == 0 or refused.status >= 500 or refused.status == 402
            or (refused.status == 409 and refused.code == "sandbox_stopping"))


# ----------------------------------------------------------------------------- the manager
class Manager:
    """Runs `decide` for every machine (each bot's own, and the shared one) and carries it out against HQ. `client` is
    a test's fake HQ; the live one is built from the gateway key at each look."""

    def __init__(self, store, settings, execution, vault=None, client=None):
        self.store, self.settings, self.execution, self.vault = store, settings, execution, vault
        self.client = client
        self.lock = threading.Lock()
        self.synced = None
        self.relist = False             # a create was refused: no other create until HQ's list has been read

    @property
    def off(self):
        return bool(self.settings.demo or self.settings.rehearsal)

    vault_of = None              # install's: the app's vault, made after this
    on_offered = None            # install's: onboarding's hook, run with the sizes HQ first lists (on_cloud_offered)

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
            keys = [r["bot"] for r in c.execute("SELECT bot FROM cloud_machines ORDER BY bot")]
            unshared = SHARED not in keys and bool(members(c))
        if unshared:                                        # a member whose row came another way than adopt
            with self.store.transaction() as c:
                ensure_shared(c)
            keys.append(SHARED)
        for key in keys:
            try:
                self.step(hq, key, at)
            except Exception:  # noqa: BLE001 - one machine's failure never holds the others
                log.exception("Cloud machine step failed for %s", key)

    def sync(self, hq, at):
        """HQ's list: every machine's state, size, cost and starts onto its row (by key), and the sizes. The first sizes
        this process sees, or the first since HQ had none, also run `on_offered`: a setup that finished before HQ
        answered puts its bots on cloud machines then."""
        first = self.synced is None
        self.synced = at
        try:
            listed = hq.list()
        except Refused as refused:
            if refused.status == 503 and refused.code == "not_configured":   # off, or turned off for this company
                with self.store.transaction() as c:
                    c.execute("DELETE FROM registry_metadata WHERE key=?", (SIZES_KEY,))
            return None
        self.relist = False
        with self.store.transaction() as c:
            learned = bool(listed["sizes"]) and (first or not sizes(c))
            if listed["sizes"]:
                c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                          "value_json=excluded.value_json",
                          (SIZES_KEY, json.dumps({"sizes": listed["sizes"], "caps": listed["caps"], "at": H.now()})))
            mine = rows(c)
            # Per key, a machine that is not dead wins, then the one this row already has. HQ renames a machine a new
            # one replaces (key ":superseded:"), so it lists one per key; a dead one already counted is never re-read.
            latest = {}
            for machine in listed["machines"]:
                had = latest.get(machine["key"])
                mine_id = (mine.get(machine["key"]) or {}).get("machine_id")
                if had is None or had["state"] in DEAD and (machine["state"] not in DEAD or machine["id"] == mine_id):
                    latest[machine["key"]] = machine
            for key, machine in latest.items():
                current = mine.get(key)
                if current and current["state"] == "deleted" and current["machine_id"] == machine["id"]:
                    continue                                # deleted here: live at HQ only until boat confirms
                if current and (current["machine_id"] in (None, machine["id"]) or (
                        current["state"] in DEAD and machine["id"] != current["machine_id"])):
                    gone = {"enroll_by": None} if machine["state"] == "deleted" else {}
                    if machine["state"] == "failed" and (current["machine_id"], current["state"]) != (
                            machine["id"], "failed"):
                        # HQ never got it a computer: it counts like one that never enrolled, so a boat that keeps
                        # failing stops being asked (no_enroll), and the work it was woken for may wake a new one.
                        # Only HQ's list says so: a refused or unanswered create may be a passing outage.
                        gone.update(enroll_by=None, enroll_fails=current["enroll_fails"] + 1, wake_key=None)
                    elif machine["state"] in ON and current["machine_id"] != machine["id"]:
                        # a create whose answer was lost: HQ made it after all, so it starts as act() would have it
                        gone.update(active_at=at, heartbeat_at=at,
                                    **({"problem": ""} if current["problem"].startswith("hq:") else {}))
                    save(c, key, **self.described(machine), **gone)
            if learned and self.on_offered is not None and offered(c, self.settings):
                c.execute("SAVEPOINT cloud_offered")
                try:
                    self.on_offered(c)
                except Exception:  # noqa: BLE001 - the sizes stay; the scheduler's placement sweep still places bots
                    c.execute("ROLLBACK TO cloud_offered")
                    log.exception("Placing bots on cloud machines failed")
                c.execute("RELEASE cloud_offered")
        return listed

    @staticmethod
    def described(machine):
        return {"machine_id": machine["id"], "state": machine["state"], "machine_size": machine["size"],
                "started_at": machine["started_at"], "stopped_at": machine["stopped_at"],
                "cost_month_usd": machine["cost_month_usd"], "starts_month": machine["starts_month"],
                "last_error": machine["last_error"]}

    def step(self, hq, key, at):
        """One machine's look. A bot's own row also binds that bot to its machine, own or shared, once it has one."""
        if key != SHARED:
            with self.store.read() as c:
                unbound = unplaced(c, key)
            if unbound:                                     # a write only when there is one to make
                with self.store.transaction() as c:
                    bind(c, self.execution, key)
        with self.store.read() as c:
            current = row(c, key)
            if not current:
                return None
            found = facts(c, self.settings, current, at)
            idle_s = idle_after(c, current["machine_size"] or current["size"])
        action, problem = decide(current, found, at, idle_s)
        if problem is not None and problem != current["problem"] and not action:
            with self.store.transaction() as c:
                save(c, key, problem=problem, problem_at=H.now())
        elif problem is None and not action and current["problem"] == "stuck" \
                and not covers(current["wake_key"], found["want"]):
            with self.store.transaction() as c:
                save(c, key, problem="", problem_at=None)
        if action:
            self.act(hq, current, action, found, at)
        return action

    def act(self, hq, current, action, found, at):
        key = current["bot"]
        code = None
        if action in ("stop", "delete"):
            with self.store.transaction() as c:
                current = reserve(c, self.settings, key, action, current["machine_id"])
            if current is None:
                return                                      # a claim came first, or the facts changed: not now
        if action == "create" and self.relist:
            return                                          # the last create was refused, and HQ's list not read since
        if action == "create":
            with self.store.transaction() as c:
                code = mint(c, key, self.execution.auth.owner_id(c) if key == SHARED else None)
                awaiting(c, key, at)
        try:
            if action == "create":
                machine = hq.create(key, current["size"], code, self.settings.runner_url)
            elif action == "start":
                resize = current["size"] if current["size"] != current["machine_size"] else None
                machine = hq.start(current["machine_id"], resize)
            elif action == "stop":
                machine = hq.stop(current["machine_id"])
            elif action == "delete":
                try:
                    hq.delete(current["machine_id"])
                except Refused as refused:
                    if refused.status != 404:               # already gone at HQ is what was asked
                        raise
                self.never_enrolled(current)
                return
            else:
                ttl = hq.heartbeat(current["machine_id"])
                with self.store.transaction() as c:
                    save(c, key, heartbeat_at=at, ttl_s=ttl, failures=0, next_try=None)
                return
        except Refused as refused:
            self.refused(current, action, refused, at)
            return
        if action == "create" and current["state"] in DEAD and machine["id"] == current["machine_id"]:
            # HQ still has the machine deleted (until boat confirms) or failed here: the new one is made once it is
            # gone, and HQ answering with that same machine is not a new one.
            with self.store.transaction() as c:
                save(c, key, next_try=at + BACKOFF, enroll_by=None)     # the code it took for that goes unused
            return
        fields = self.described(machine)
        if action == "create" and machine["state"] == "failed":
            fields.update(enroll_by=None, enroll_fails=current["enroll_fails"] + 1)   # as when HQ's list says so
        elif action in ("create", "start"):
            woke_for = wake_for(current, found)[0]
            fields.update(active_at=at, heartbeat_at=at, wake_key=woke_for, woke_at=at)
        with self.store.transaction() as c:
            save(c, key, **fields, failures=0, next_try=None, problem="", problem_at=None, stop_until=None)
            H.event(c, H.KEEPER, "cloud_machine." + {"create": "created", "start": "started", "stop": "stopped"}[action],
                    key, {"size": machine["size"], "machine": machine["id"]})

    def never_enrolled(self, current):
        """Its machine was deleted for never enrolling: the next want makes a new one with a fresh code, unless
        ENROLL_TRIES in a row have failed so ("no_enroll", until one enrolls or a person changes its settings)."""
        key, fails = current["bot"], current["enroll_fails"] + 1
        with self.store.transaction() as c:
            # Its id stays, as deleted: HQ keeps a machine live until boat confirms the delete, and a create meanwhile
            # answers with that one (act), which is not the new machine.
            save(c, key, state="deleted", enroll_by=None, enroll_fails=fails, wake_key=None,
                 woke_at=None, active_at=None, heartbeat_at=None, failures=0, next_try=None, stop_until=None,
                 **({"problem": "no_enroll", "problem_at": H.now()} if fails >= ENROLL_TRIES
                    else {"problem": "", "problem_at": None}))
            H.event(c, H.KEEPER, "cloud_machine.deleted", key, {"machine": current["machine_id"],
                                                                 "reason": "never_enrolled", "tries": fails})

    def refused(self, current, action, refused, at):
        key = current["bot"]
        fields = {}
        if refused.status == 402:
            fields = {"problem": "out_of_credit", "problem_at": H.now(), "next_try": at + FROZEN_RETRY}
        elif refused.status == 409 and refused.code == "sandbox_stopping":
            fields = {"next_try": at + STOPPING_RETRY}
        elif refused.status == 404 and action != "create":
            # HQ has no such machine (its disk was deleted): the next want makes a new one.
            fields = {"machine_id": None, "state": "deleted", "next_try": None, "enroll_by": None}
        else:
            failures = current["failures"] + 1
            fields = {"failures": failures, "next_try": at + min(BACKOFF_MAX, BACKOFF * 2 ** (failures - 1))}
            if action in ("create", "start"):
                fields.update(problem="hq:" + refused.code, problem_at=H.now())
            if action == "create":
                self.synced, self.relist = None, True       # HQ's list first: a machine it failed counts before a retry
        if action in ("stop", "delete"):
            if stopped_unknown(refused):
                # It may be stopping all the same: the fence holds for STOP_HOLD more, and the next look asks HQ's
                # list, whose stopping or stopped holds it from then (`held`). Running there, it lapses.
                fields["stop_until"] = now() + STOP_HOLD
                self.synced = None
            else:
                fields["stop_until"] = None                 # HQ said it did not: its runner may take work again
        with self.store.transaction() as c:
            save(c, key, **fields)
            if refused.status == 402 and current["problem"] != "out_of_credit" or (
                    "failures" in fields and fields["failures"] == 1):
                H.event(c, H.KEEPER, "cloud_machine.refused", key, {"action": action, "code": refused.code,
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
PROBLEMS = {"out_of_credit": "Out of credit", "stuck": "Started, but its work did not run there",
            "no_enroll": "Made, but it never connected to this server"}


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
    machine = home(c, bot, current) or current
    if machine["problem"] == "out_of_credit" or cloud_access.frozen(c):
        return "out of credit: its cloud machine can't start"
    if machine["problem"].startswith("hq:"):
        return f"its cloud machine didn't start ({machine['problem'][3:]}); trying again"
    if machine["problem"] == "stuck":
        return "its cloud machine started, but this did not run there"
    if machine["problem"] == "no_enroll":
        return "its cloud machine never connected to this server"
    return "starting its cloud machine"


def homes(c):
    """{bot: {runner_id, problem}} for every cloud-mode bot: its machine's computer and what keeps it from starting."""
    found = rows(c)
    shared = found.get(SHARED)
    out = {}
    for bot, current in found.items():
        if bot == SHARED or current["mode"] != "cloud":
            continue
        machine = (shared if current["placement"] == "shared" else current) or current
        out[bot] = {"runner_id": machine["runner_id"], "problem": machine["problem"]}
    return out


def view(c, bot, settings=None):
    """The bot's cloud machine as Settings and the bot's page show it, or None off a cloud company."""
    if not offered(c, settings):
        return None
    from .agents import external_harness
    if external_harness(c, bot):
        return None
    assigned = bool(c.execute("SELECT 1 FROM assignments WHERE bot=?", (bot,)).fetchone())
    return shown(bot, row(c, bot), assigned, row(c, SHARED))


def machine_view(current):
    """A machine row as people see it: the size the next start uses (`size`), the one HQ last said it has, Always on,
    its state, this month's cost and starts, its computer and what keeps it from starting."""
    if current is None:
        return {"size": "default", "always_on": False, "state": "none", "machine_size": None, "cost_month_usd": 0.0,
                "starts_month": 0, "runner_id": None, "problem": ""}
    return {"size": current["size"], "always_on": bool(current["always_on"]), "state": current["state"],
            "machine_size": current["machine_size"], "cost_month_usd": round(current["cost_month_usd"] or 0, 2),
            "starts_month": current["starts_month"], "runner_id": current["runner_id"],
            "problem": problem_words(current["problem"])}


def shown(bot, current, assigned, shared=None):
    """`view` from rows already read. `mode` is where the bot runs: a cloud machine (`cloud`) or another computer;
    `placement` which cloud machine, shared or its own, and the rest is that machine's (machine_view). A bot with no row
    yet runs where it was put, or on a cloud machine once it has work."""
    if current is None:
        return {"mode": "computer" if assigned else "cloud", "placement": "shared", **machine_view(shared)}
    machine = shared if current["placement"] == "shared" else current
    return {"mode": current["mode"], "placement": current["placement"], **machine_view(machine)}


def change(c, execution, who, bot, mode=None, size=None, always_on=None, placement=None):
    """A person's choice for the bot: a cloud machine (`mode` cloud), which one (`placement` shared or own), its own
    machine's size (applied at its next start) and Always on. Moving to another computer is Execution.assign's (which
    sets `computer`). Those who manage the bot only."""
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
    if placement is not None and placement not in PLACEMENTS:
        raise Problem("validation", "Choose shared or own", 422)
    c.execute("INSERT OR IGNORE INTO cloud_machines(bot,mode,placement,changed_by,updated) VALUES(?,?,?,?,?)",
              (bot, "computer" if c.execute("SELECT 1 FROM assignments WHERE bot=?", (bot,)).fetchone() else "cloud",
               placement or default_placement(c, bot), who.actor, H.now()))
    before = row(c, bot)
    fields = {"changed_by": who.actor, "enroll_fails": 0}        # a person's choice lets a machine be made again
    if before["problem"] == "no_enroll":
        fields["problem"] = ""
    if mode is not None:
        fields.update(mode=mode, wake_key=None, problem="")
    if placement is not None:
        fields.update(placement=placement, wake_key=None, problem="")
    if size is not None:
        fields["size"] = size
    if always_on is not None:
        fields["always_on"] = int(always_on)
    save(c, bot, **fields)
    after = row(c, bot)
    if after["mode"] == "cloud" and after["placement"] == "shared":
        ensure_shared(c)
        if row(c, SHARED)["enroll_fails"]:                  # and so does a member's, for the shared machine
            save(c, SHARED, enroll_fails=0, **({"problem": ""} if row(c, SHARED)["problem"] == "no_enroll" else {}))
    if after["mode"] == "cloud":
        moving = unplaced(c, bot)
        chose = mode is not None or placement is not None
        # onto a machine that is there already; else when it is made. A person's choice of a machine hears a lasting refusal.
        bind(c, execution, bot, lasting=chose)
        from . import model_subscriptions as MS
        target = (home(c, bot) or {}).get("runner_id")
        if moving and target and chose and MS.is_admin(auth, who):
            lend_plans(c, execution, bot, target, who.actor)
    H.event(c, who.actor, "cloud_machine.changed", bot, {
        key: after[key] for key in ("mode", "placement", "size", "always_on") if after[key] != before[key]})
    return view(c, bot, execution.store.settings)


def shared_view(c):
    """The shared machine as Settings shows it, with the bots set to run there."""
    return {**machine_view(row(c, SHARED)), "bots": members(c)}


def change_shared(c, auth, who, size=None, always_on=None):
    """The company's choice for the shared machine: its size (applied at its next start) and Always on. Owners and
    admins only: it spends the company's credit for every bot on it. The route checks the company is on Tico Cloud."""
    if not auth.bot_admin(who):
        raise Problem("forbidden", "Only owners and admins change the shared cloud machine", 403)
    if size is not None and size not in size_names(c):
        raise Problem("validation", "Choose one of: " + ", ".join(size_names(c)), 422)
    ensure_shared(c)
    before = row(c, SHARED)
    fields = {"changed_by": who.actor, "enroll_fails": 0}        # a person's choice lets a machine be made again
    if before["problem"] == "no_enroll":
        fields["problem"] = ""
    if size is not None:
        fields["size"] = size
    if always_on is not None:
        fields["always_on"] = int(always_on)
    save(c, SHARED, **fields)
    after = row(c, SHARED)
    H.event(c, who.actor, "cloud_machine.changed", SHARED, {
        key: after[key] for key in ("size", "always_on") if after[key] != before[key]})
    return shared_view(c)


def removable(c, auth, who, key):
    """The row of machine `key`, for deleting; Problem otherwise. A bot's own: owner, admin or the bot's manager, and
    never while the bot is set to run there. The shared one (SHARED): owners and admins, whoever runs there: it is
    deleted to replace a broken one, and the next work of any member makes a fresh one."""
    if key == SHARED:
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only owners and admins delete the shared cloud machine", 403)
    elif not (auth.bot_admin(who) or auth.bot_manager(c, who, key)):
        raise Problem("forbidden", "You do not manage this bot's computer", 403)
    current = row(c, key)
    if not current or not (present(current) or current["runner_id"]):
        raise Problem("not_found", "There is no shared cloud machine" if key == SHARED
                      else "This bot has no cloud machine of its own", 404)
    if key != SHARED and current["mode"] == "cloud" and current["placement"] == "own":
        raise Problem("in_use", "Move the bot to another computer or the shared cloud machine first", 409)
    return current


def present(current):
    """Whether the row names a machine HQ may still have: one deleted here keeps its id (HQ keeps it live until boat
    confirms, and a create meanwhile answers with it: `act`), but there is nothing left to delete."""
    return bool(current["machine_id"]) and current["state"] != "deleted"


def remove(store, hq, who, auth, key):
    """Delete machine `key` at HQ (its disk; the month's cost stays billed) and the computer it was: a bot's own once
    the bot runs elsewhere (the bot keeps where it runs now), or the shared one (its members' next work makes a new
    one). HQ first: a failure there leaves everything as it was, to try again."""
    with store.read() as c:
        current = removable(c, auth, who, key)
    if present(current):
        if hq is None:
            raise Problem("gateway", "Connect Tico Gateway first", 409)
        try:
            hq.delete(current["machine_id"])
        except Refused as refused:
            if refused.status != 404:
                raise Problem("gateway", "Tico did not answer. Try again in a minute." if refused.status == 0
                              else f"Tico refused this ({refused.code}).", 502) from None
    with store.transaction() as c:
        current = removable(c, auth, who, key)
        if current["runner_id"]:
            c.execute("UPDATE runners SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (H.now(), current["runner_id"]))
        # The machine goes; the choices stay (size, Always on, where the bot runs): a bot's own is not made again unless
        # someone picks it, and the shared one is made again at a member's next work (or at once if Always on). Its id
        # stays, as deleted, as never_enrolled leaves it: a create before boat confirms the delete gets this machine
        # back from HQ (idempotent on key), which `act` takes for no new one and tries again later.
        save(c, key, runner_id=None, state="deleted", machine_size=None, started_at=None,
             stopped_at=None, cost_month_usd=0, starts_month=0, last_error="", active_at=None, heartbeat_at=None,
             ttl_s=None, next_try=None, failures=0, problem="", problem_at=None, wake_key=None, woke_at=None,
             enroll_by=None, enroll_fails=0, stop_until=None, changed_by=who.actor)
        H.event(c, who.actor, "cloud_machine.deleted", key, {"machine": current["machine_id"]})
    return {"ok": True}


def health(c, settings=None):
    """Health's row, or None: some bot's machine could not be started (out of credit, or HQ refusing)."""
    if not offered(c, settings):
        return None
    stuck = sorted(bot for bot, machine in homes(c).items() if machine["problem"])
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
    placement: str | None = Field(None, max_length=20)
    size: str | None = Field(None, max_length=20)
    always_on: bool | None = None


class SharedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    size: str | None = Field(None, max_length=20)
    always_on: bool | None = None


def install(app, settings, store, auth, execution, mutate):
    service = Manager(store, settings, execution)
    service.vault_of = lambda: getattr(app.state, "vault", None)
    onboarding = getattr(app.state, "onboarding", None)
    service.on_offered = onboarding.on_cloud_offered if onboarding is not None else None
    app.state.cloud_machines = service

    @app.get("/api/v2/cloud-machines")
    def cloud_machine_sizes(request: Request):
        """Whether bots here may run on cloud machines, the sizes HQ offers (its customer price per hour, one start's
        price, and the idle time after which it stops), and the shared machine (`shared`: its size, Always on, state
        and the bots set to run there). `offered` false off Tico Cloud."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if not offered(c, settings):
                return {"offered": False, "sizes": [], "caps": {}, "shared": None}
            from .read_cache import metadata
            found = metadata(c, SIZES_KEY)
            try:
                caps = (json.loads(found[0]) or {}).get("caps") or {} if found else {}
            except (ValueError, TypeError, AttributeError):
                caps = {}
            return {"offered": True, "sizes": sizes(c) or [{"name": n} for n in SIZES], "caps": caps,
                    "shared": shared_view(c)}

    @app.post("/api/v2/cloud-machines/shared")
    def cloud_machine_shared_set(request: Request, body: SharedChange):
        """The shared cloud machine's `size` (applied at its next start) and `always_on`. Owners and admins only."""
        who = request.state.identity
        auth.domain(who)

        def run(c):
            if not offered(c, settings):
                raise Problem("not_cloud", "Cloud machines are for Tico Cloud companies", 409)
            return {"shared": change_shared(c, auth, who, body.size, body.always_on)}
        return mutate(request, body, run)

    @app.delete("/api/v2/cloud-machines/shared")
    def cloud_machine_shared_delete(request: Request):
        """Delete the shared cloud machine (its disk) and the computer it was, to replace a broken one: a member's next
        work makes a new one. Its size and Always on stay. Owners and admins only."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if not offered(c, settings):
                raise Problem("not_cloud", "Cloud machines are for Tico Cloud companies", 409)
            hq = service.hq(c)
        return remove(store, hq, who, auth, SHARED)

    @app.post("/api/v2/bots/{bot}/cloud-machine")
    def cloud_machine_set(bot: str, request: Request, body: Change):
        """Where the bot runs and how: `mode` cloud puts it on a cloud machine (another computer is chosen with a
        machine transition), `placement` on the shared one or its own; its own machine's `size` applies at that
        machine's next start, and `always_on` keeps it running. Those who manage the bot only; Tico Cloud only."""
        who = request.state.identity
        auth.domain(who)
        return mutate(request, body, lambda c: {"cloud_machine": change(
            c, execution, who, _known(c, bot), body.mode, body.size, body.always_on, body.placement)})

    @app.delete("/api/v2/bots/{bot}/cloud-machine")
    def cloud_machine_delete(bot: str, request: Request):
        """Delete the bot's own cloud machine (its disk) and the computer it was, once the bot runs elsewhere."""
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
