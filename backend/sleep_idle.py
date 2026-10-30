"""Sleep when idle: a server Tico hosts asks Tico HQ to stop it (and its bot computers) after N quiet hours.

Only a hosted server can sleep, and only Tico HQ can start it again: a webhook through Tico Gateway, a routine's
`wake_at` or a portal sign-in wakes it (the HQ side is tico-services). This module is the install's half. It keeps the
two settings (`hours`, 0 = off, and `runners`, whether the bot computers stop with the server), asks HQ whether this
install is hosted (`GET servers/self`, kept six hours), looks every five minutes for the quiet that was asked for, and
on finding it records why in the history and asks HQ to stop the server (`POST servers/self/sleep`).

Quiet means all of these hold for the whole of `hours`: nobody used the app signed in, no run started or ended, and right
now nothing is queued, running, waiting for a worktree, a computer's job, or an outgoing send, and no routine is due in
the next 15 minutes. The time of the last signed-in request is kept in memory only: a restart (a wake-up included)
starts the count again from the start, which is never later than the real last activity.

The call is made outside any write transaction. A refusal that only says "not now" (`busy`, `cooldown`) is recorded and
asked again after 15 minutes; anything else backs off up to six hours and reaches Health. Rehearsal and demo never ask,
and a server that is not hosted never does either.
"""
import asyncio
import threading
import time
from datetime import datetime, timezone

from fastapi import Request
from pydantic import Field, StrictBool

from . import hubdb as H
from .hook_relay import Failed, Link, Stop, link_of
from .models import Contract
from .store import Problem

TRANSPORT = None                 # tests' fake HQ, never the live one
EVERY = 300                      # seconds between looks for quiet
HOSTED_FOR = 6 * 3600            # how long HQ's word on "hosted" is kept
HOSTED_RETRY = 300               # a failed hosted check is not repeated sooner
SOON = 15 * 60                   # a routine due this soon keeps the server up; also the wait after an answer
BACKOFF_MAX = 6 * 3600
UI_EVERY = 60                    # the last-seen mark moves at most this often
ANSWER_CAP = 64 * 1024
HOURS_MAX = 24
NOT_NOW = ("busy", "cooldown")   # HQ refusals that are no failure
STREAM = "/api/v2/stream"        # an open stream says nothing about a person being there
# Outgoing sends: a row waiting for its next try (`next_attempt`, NULL = now) keeps the server up only when that try is
# near; one Svix-style retry ten hours away must not. `sending` always does.
SENDS = {"slack_posts": "state IN ('ready','retry','rate_limited')",
         "webhook_sends": "state IN ('ready')"}
NO_KEY = "Connect the Tico Gateway in Settings > Gateway first: Sleep when idle asks Tico with its key."
NOT_HOSTED = "Sleep when idle is only for servers Tico hosts."
now = time.time                  # tests' controlled clock


class SleepSet(Contract):
    hours: int | None = Field(default=None, ge=0, le=HOURS_MAX)
    runners: StrictBool | None = None


# ----------------------------------------------------------------------------- state
def state(c):
    row = c.execute("SELECT * FROM sleep_idle_state WHERE singleton=1").fetchone()
    return dict(row) if row else {"hours": 0, "runners": 1, "changed_by": "", "changed_at": None, "hosted": None,
                                  "layout": "", "checked_at": None, "last_request_at": None, "last_result": "",
                                  "last_wake_at": None, "last_error": "", "failures": 0, "next_ask": None}


def save(c, **fields):
    c.execute("INSERT OR IGNORE INTO sleep_idle_state(singleton) VALUES(1)")
    c.execute("UPDATE sleep_idle_state SET " + ",".join(k + "=?" for k in fields) + " WHERE singleton=1",
              tuple(fields.values()))


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if seconds else None


def stamp(seconds):
    """A time in this server's own timestamp format (hubdb.now), which its tables compare as text."""
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def epoch(value):
    at = H.parse_ts(value) if isinstance(value, str) else None
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()


# ----------------------------------------------------------------------------- quiet
def table(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def routine_times(c, at):
    """When the server next has something to do on a clock, soonest first: each switched-on schedule automation (an
    overdue one is due now), the controlled system jobs that run at a fixed time, the daily release hours, the later
    tries of waiting sends and the next deferred item's deadline. Jobs that repeat on an interval are housekeeping that
    catches up after a wake, so they are left out: a 15-minute job would otherwise keep the server up for good."""
    when = datetime.fromtimestamp(at, timezone.utc)
    out = []
    if table(c, "automations"):
        for row in c.execute("SELECT next_due FROM automations WHERE enabled=1 AND deleted_at IS NULL "
                             "AND next_due IS NOT NULL").fetchall():
            if (due := epoch(row[0])) is not None:
                out.append(due)
    out.extend(t for t in fixed_times(c, when) if t > at)      # one already past is being run by the scheduler now
    out.extend(later_sends(c, at))
    # A deferred item's deadline wakes its bot like a schedule (backend/delivery.py); one past is due now.
    from . import delivery
    if table(c, "deferrals") and (due := epoch(delivery.next_due(c))) is not None:
        out.append(max(due, at))
    return sorted(out)


def fixed_times(c, when):
    """Next run of each switched-on system job set to a time of day, and of the release schedule."""
    from . import release_flow
    from . import system_routines as SR
    out = []
    try:
        last = SR.last_started(c, list(SR.CONTROLS))
        for key, cfg in SR.config(c).items():
            if cfg["on"] and cfg["preset"] == SR.DAILY:
                found = SR.next_night(c, cfg, when) if key == "learnings" else SR.next_due(cfg, last.get(key), when)
                if found:
                    out.append(found.timestamp())
    except Exception:
        pass
    try:
        found = release_flow.next_at(release_flow.load(c), when)
        if found:
            out.append(found.timestamp())
    except Exception:
        pass
    return out


def later_sends(c, at):
    """The earliest `next_attempt` of each kind of waiting send that is beyond the 15 minutes `busy` already covers."""
    out = []
    for name, where in SENDS.items():
        if table(c, name):
            row = c.execute(f"SELECT min(next_attempt) FROM {name} WHERE {where} AND next_attempt>?",
                            (stamp(at + SOON),)).fetchone()
            if row and row[0] and (found := epoch(row[0])):
                out.append(found)
    return out


def busy(c, at):
    """The first thing keeping the server up right now, or ''. Names are stable: the CLI and the app show them."""
    if c.execute("SELECT 1 FROM jobs WHERE state='queued' LIMIT 1").fetchone():
        return "run_queued"
    if (c.execute("SELECT 1 FROM jobs WHERE state IN ('leased','running') LIMIT 1").fetchone()
            or c.execute("SELECT 1 FROM attempts WHERE state IN ('leased','running') LIMIT 1").fetchone()):
        return "run_in_progress"
    if table(c, "worktree_waits") and c.execute("SELECT 1 FROM worktree_waits WHERE state IN ('waiting','reserved') LIMIT 1").fetchone():
        return "task_session"
    if c.execute("SELECT 1 FROM runner_harness_actions WHERE state IN ('requested','running') LIMIT 1").fetchone():
        return "runner_job"
    for name, where in SENDS.items():
        if table(c, name) and c.execute(
                f"SELECT 1 FROM {name} WHERE state='sending' OR ({where} AND coalesce(next_attempt,'')<=?) LIMIT 1",
                (stamp(at + SOON),)).fetchone():
            return "send_pending"
    if (times := routine_times(c, at)) and times[0] <= at + SOON:
        return "routine_due"
    return ""


def last_work(c, at, hours):
    """When the last run started or ended, or None. Only recent runs are looked at (the created index)."""
    row = c.execute("SELECT max(coalesce(finished,started,created)) FROM attempts WHERE created>=?",
                    (stamp(at - (hours + 48) * 3600),)).fetchone()
    return epoch(row[0]) if row and row[0] else None


def wake_time(c, at):
    """The next routine due after the 15 minutes that would have kept the server up, or None."""
    return next((t for t in routine_times(c, at) if t > at + SOON), None)


class SleepIdle:
    def __init__(self, store, settings, app, auth=None):
        self.store, self.settings, self.app, self.auth = store, settings, app, auth
        self.lock = threading.Lock()
        self.started = now()
        self.ui_at = 0.0
        self.hosted_try = 0.0

    @property
    def off(self):
        return bool(self.settings.demo or self.settings.rehearsal)

    def vault(self):
        return getattr(self.app.state, "vault", None)

    def link(self, c):
        vault = self.vault()
        if not vault:
            return None
        link = link_of(c, vault, self.settings)
        return Link(link.key, link.base, link.fingerprint) if link else None

    def ask(self, link, method, path, body=None, idempotency=None):
        return link.call(method, path, body, idempotency, area="servers", transport=TRANSPORT, cap=ANSWER_CAP)

    # ------------------------------------------------------------------------- signed-in activity
    def seen(self, who, path):
        """A person using the app signed in. A token, a bot or a stream does not count: a script polling must not keep a
        server up, and an open stream only says a tab exists."""
        if (who.role not in ("owner", "human") or who.via or who.via_token or not path.startswith("/api/")
                or path.startswith(STREAM)):
            return
        at = now()
        if at - self.ui_at >= UI_EVERY:
            self.ui_at = at

    # ------------------------------------------------------------------------- is this server hosted
    def hosted(self, link, force=False):
        """HQ's word on whether Tico hosts this server, kept six hours; None when it cannot be asked. Raises Failed on
        a force that fails."""
        with self.store.read() as c:
            current = state(c)
        at = now()
        fresh = current["checked_at"] is not None and at - current["checked_at"] < HOSTED_FOR
        if fresh and not force:
            return bool(current["hosted"])
        if not link:
            return None if current["hosted"] is None else bool(current["hosted"])
        if not force and at - self.hosted_try < HOSTED_RETRY:
            return None if current["hosted"] is None else bool(current["hosted"])
        self.hosted_try = at
        try:
            answer = self.ask(link, "GET", "self")
        except Stop:
            if force:
                raise Failed("key_revoked", 401) from None
            return None if current["hosted"] is None else bool(current["hosted"])
        except Failed:
            if force:
                raise
            return None if current["hosted"] is None else bool(current["hosted"])
        hosted = answer.get("hosted") is True
        layout = answer.get("layout") if answer.get("layout") in ("same", "separate") else ""
        with self.store.transaction() as c:
            save(c, hosted=int(hosted), layout=layout if hosted else "", checked_at=at)
        return hosted

    # ------------------------------------------------------------------------- the five-minute look
    def tick(self):
        if self.off or not self.lock.acquire(blocking=False):
            return
        try:
            self._tick()
        finally:
            self.lock.release()

    def _tick(self):
        with self.store.read() as c:
            current = state(c)
            link = self.link(c)
        if not current["hours"] or not link:
            return
        if not self.hosted(link):
            return
        at = now()
        if current["next_ask"] and at < current["next_ask"]:
            return
        with self.store.read() as c:
            quiet = self.quiet(c, current["hours"], at)
            wake = wake_time(c, at) if quiet["idle"] else None
        if not quiet["idle"]:
            return
        detail = {"hours": current["hours"], "runners": bool(current["runners"]), "idle_since": iso(quiet["since"]),
                  "wake_at": iso(wake)}
        with self.store.transaction() as c:
            H.event(c, "keeper", "server.sleep", "", detail)
        self.request(link, current, wake, quiet["since"])

    def request(self, link, current, wake, since):
        body = {"wake_at": iso(wake), "runners": bool(current["runners"]), "reason": "idle"}
        key = f"sleep:{int(since)}:{current['hours']}"
        at = now()
        result, error = "requested", ""
        try:
            self.ask(link, "POST", "self/sleep", body, key)
            delay, failures = SOON, 0
        except Stop:
            result, error, failures = "failed", "key_revoked", current["failures"] + 1
            delay = min(BACKOFF_MAX, SOON * 2 ** (failures - 1))
        except Failed as failed:
            if failed.status == 409 and failed.code in NOT_NOW:
                result, failures, delay = failed.code, 0, SOON
            elif failed.status == 409 and failed.code == "not_hosted":
                result, failures, delay = "not_hosted", 0, HOSTED_FOR
            else:
                result, error, failures = "failed", failed.code, current["failures"] + 1
                delay = min(BACKOFF_MAX, SOON * 2 ** (failures - 1))
        with self.store.transaction() as c:
            save(c, last_request_at=at, last_result=result, last_wake_at=iso(wake) if result == "requested" else None,
                 last_error=error, failures=failures, next_ask=at + delay)
            if result == "not_hosted":
                save(c, hosted=0, layout="", checked_at=at)

    def quiet(self, c, hours, at):
        """`{idle, since, blocker}`: `since` is the last moment anything happened, `idle` whether it was `hours` ago."""
        blocker = busy(c, at)
        marks = [self.started, self.ui_at, last_work(c, at, hours) or 0]
        since = max(marks)
        return {"idle": not blocker and at - since >= hours * 3600, "since": None if blocker else since,
                "blocker": blocker}

    async def loop(self, stop):
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), EVERY)
            except TimeoutError:
                pass
            if stop.is_set():
                return
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                pass                                # the next look tries again; the failure is in the state row

    # ------------------------------------------------------------------------- what people see and change
    def view(self, c):
        current = state(c)
        link = self.link(c)
        hosted = None if current["hosted"] is None else bool(current["hosted"])
        active = bool(current["hours"] and hosted and link and not self.off)
        quiet = self.quiet(c, current["hours"], now()) if current["hours"] and hosted else None
        last = None
        if current["last_request_at"]:
            last = {"at": iso(current["last_request_at"]), "result": current["last_result"],
                    "wake_at": current["last_wake_at"]}
        return {"hosted": bool(hosted), "layout": current["layout"] or None, "hours": current["hours"],
                "runners": bool(current["runners"]), "active": active,
                "idle_since": iso(quiet["since"]) if quiet else None,
                "blocker": quiet["blocker"] if quiet else "",
                "wake_at": iso(wake_time(c, now())) if active else None,
                "last_request": last, "last_error": current["last_error"]}

    def read(self):
        """The state; a first look at a server nobody has asked about yet also asks HQ whether it is hosted."""
        with self.store.read() as c:
            current, link = state(c), self.link(c)
        if link and not self.off and (current["checked_at"] is None or now() - current["checked_at"] >= HOSTED_FOR):
            self.hosted(link)
        with self.store.read() as c:
            return self.view(c)

    def change(self, who, hours, runners):
        if hours is None and runners is None:
            raise Problem("validation", "Send hours, runners or both", 422)
        with self.store.read() as c:
            current, link = state(c), self.link(c)
        target = current["hours"] if hours is None else hours
        if target:                                   # turning it on needs HQ's word that this server is Tico's
            if not link:
                raise Problem("gateway", NO_KEY, 409)
            try:
                hosted = self.hosted(link, force=True)
            except Failed as failed:
                raise Problem("gateway", "Tico did not answer. Try again in a minute." if failed.code == "unreachable"
                              else f"Tico refused this ({failed.code}).", 502) from None
            if not hosted:
                raise Problem("not_hosted", NOT_HOSTED, 409)
        fields = {"changed_by": who.actor, "changed_at": H.now(), "next_ask": None, "failures": 0, "last_error": ""}
        if hours is not None:
            fields["hours"] = hours
        if runners is not None:
            fields["runners"] = int(runners)
        with self.store.transaction() as c:
            save(c, **fields)
            H.event(c, who.actor, "server.sleep_set", "", {"hours": target, "runners": bool(
                current["runners"] if runners is None else runners)})
        return self.read()


def health(c, settings=None):
    """Health's row, or None: Sleep when idle is on and Tico's last answer was neither success nor a plain 'not now'."""
    current = state(c)
    if not current["hours"] or not current["hosted"] or not current["last_error"]:
        return None
    words = ("Tico does not accept this server's Gateway key" if current["last_error"] == "key_revoked"
             else "this server cannot reach Tico" if current["last_error"] == "unreachable"
             else f"Tico refused it ({current['last_error']})")
    return {"status": "warn", "summary": f"Sleep when idle is on, but the last request to stop this server failed: {words}. "
                                         "The server keeps running and tries again."}


def install(app, settings, store, auth):
    service = SleepIdle(store, settings, app, auth)
    app.state.sleep_idle = service

    @app.get("/api/v2/sleep")
    def sleep_status(request: Request):
        """Sleep when idle: whether this server is hosted, the idle hours (0 = off), whether bot computers stop with it,
        since when it has been quiet, and Tico's last answer."""
        who = request.state.identity
        auth.domain(who)
        if who.role not in ("owner", "human", "bot"):
            raise Problem("forbidden", "Only people and bots read this", 403)
        return {"sleep": service.read()}

    @app.post("/api/v2/sleep")
    def sleep_set(request: Request, body: SleepSet):
        """Change Sleep when idle: `hours` 1-24 turns it on, 0 turns it off; `runners` says whether bot computers stop and
        start with the server. An owner or an admin (BotOps for one) only; turning it on needs a server Tico hosts."""
        who = request.state.identity
        auth.domain(who)
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only an owner or an admin changes Sleep when idle", 403)
        return {"sleep": service.change(who, body.hours, body.runners)}
