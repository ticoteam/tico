"""What watchers do on the server: open a task for a bot, or wake it, when a program in its repository saw something
(docs/watchers.md, clients/watchers.py).

The runner runs the program on the bot's computer and posts its events here (`POST /api/v2/runners/watchers`). The
program holds no hub credential, so a program that is fed hostile text (a ticket, an issue) can do nothing but say
these three things about a key it names:

    task     a task for the bot, once per key
    comment  a note on that task, which wakes the bot
    done     a note that it ended elsewhere

Every event's text is data from outside: it is stored as the task's text and never interpreted here. The runner also
reports each run (exit status, timeout, a short redacted log) so Health shows a watcher that fails or has stopped.
"""
import re
from typing import Literal

from fastapi import Request
from pydantic import Field

from clients.watchers import LIMITS, MAX_EVENTS
from . import rooms, run_triggers, screening, support_triage
from .models import Contract, Slug
from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS watcher_items(
 bot TEXT NOT NULL, key TEXT NOT NULL, task_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(bot, key));
CREATE TABLE IF NOT EXISTS watcher_refs(
 bot TEXT NOT NULL, key TEXT NOT NULL, ref TEXT NOT NULL, PRIMARY KEY(bot, key, ref));
CREATE TABLE IF NOT EXISTS watcher_runs(
 bot TEXT NOT NULL, name TEXT NOT NULL, runner_id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
 exit_code INTEGER NOT NULL, timed_out INTEGER NOT NULL DEFAULT 0, every INTEGER NOT NULL, output TEXT NOT NULL DEFAULT '',
 events INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(bot, name));
""" + support_triage.SCHEMA
STALE_AFTER = 3            # runs missed before Health says a watcher has stopped
# A path into a secrets folder or another bot's repository is refused in a task or a message (hubdb rule 8), so text
# from outside is written with the slash set apart rather than losing the whole event. `emp-<anything>/` always counts;
# `bot-<name>/` only when it is a real bot's folder, the same test the hub applies (H.known_repos).
PROTECTED = re.compile(r"\b(secrets|emp-[A-Za-z0-9-]+)/")
BOT_REPO = re.compile(r"\b(bot-[A-Za-z0-9-]+)/")


class WatcherEvent(Contract):
    op: Literal["task", "comment", "done"]
    incident: bool = False
    key: str = Field(min_length=1, max_length=LIMITS["key"])
    ref: str = Field(default="", max_length=LIMITS["ref"])
    title: str = Field(default="", max_length=LIMITS["title"])
    body: str = Field(default="", max_length=LIMITS["body"])
    text: str = Field(default="", max_length=LIMITS["text"])
    note: str = Field(default="", max_length=LIMITS["note"])


class WatcherReport(Contract):
    bot: Slug
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    started: str = Field(max_length=40)
    finished: str = Field(max_length=40)
    exit: int = Field(ge=-255, le=255)
    timed_out: bool = False
    every: int = Field(ge=60, le=86400)
    output: str = Field(default="", max_length=4000)
    events: list[WatcherEvent] = Field(default_factory=list, max_length=MAX_EVENTS)


def defang(text, c=None):
    text = PROTECTED.sub("\\1\u200b/", str(text or ""))
    if c is None:
        return text
    known = H.known_repos(c)
    return BOT_REPO.sub(lambda m: m.group(1) + "\u200b/" if m.group(1).lower() in known else m.group(0), text)


def live(task):
    return bool(task) and task["status"] not in ("closed", "declined")


def _open(c, auth, bot, title, body, owner=None, *, incident=False):
    owner = owner or "bot:" + bot
    conversation = rooms.task_conversation_id(c, auth, owner, H.KEEPER)
    return H.task_create(c, H.KEEPER, defang(title, c).strip()[:300] or "From a watcher", defang(body, c), owner,
                         deduplicate=False, conversation_id=conversation, lint=not owner.startswith("human:"),
                         labels=["incident"] if incident else None)


def _wake(c, bot, task, text, why):
    H.say(c, H.KEEPER, "bot:" + bot, defang(text, c) or "Something changed.", kind="notice",
          conversation_id=task["conversation_id"],
          refs=run_triggers.stamp({"task": task["id"], "wake": why}, "comment" if why == "watcher" else "status_or_relation"))


def _note(c, task, text):
    """A note on a held support ticket's task, for the person holding it: the bot is not woken."""
    H.task_comment(c, H.KEEPER, task["id"], defang(text, c) or "Something changed.", wake=False)
    return task["id"]


def deliver(c, auth, bot, event, triage=None):
    """Apply one event. Returns the id of the task it touched, or None when it changed nothing.

    `triage(c, event)` is True when the event waits for support triage or screening instead (backend/support_triage.py,
    backend/screening.py); screening may instead give back the event to apply, with a warning line on its body."""
    item = c.execute("SELECT task_id FROM watcher_items WHERE bot=? AND key=?", (bot, event.key)).fetchone()
    task = H.task(c, item["task_id"]) if item else None
    if event.ref and c.execute("SELECT 1 FROM watcher_refs WHERE bot=? AND key=? AND ref=?",
                               (bot, event.key, event.ref)).fetchone():
        return None
    if event.ref:
        c.execute("INSERT INTO watcher_refs VALUES(?,?,?)", (bot, event.key, event.ref))
    if support_triage.pending(c, bot, event.key):
        support_triage.later(c, bot, event)            # a note on its task once triage opens it
        return None
    if screening.waiting(c, "watcher", bot, event.key):
        screening.later(c, "watcher", bot, event)      # a note on its task once screening opens it
        return None
    if event.op != "task" and support_triage.held(c, bot, event.key, task):
        return _note(c, task, event.text or event.note) if live(task) else None
    if event.op == "task":
        if item:
            return None
        waits = triage(c, event) if triage else False
        if waits is True:
            return None
        event = waits or event
        task = _open(c, auth, bot, event.title or event.key, event.body, incident=event.incident)
    elif event.op == "comment":
        if live(task):
            _wake(c, bot, task, event.text, "watcher")
            return task["id"]
        task = _open(c, auth, bot, event.title or event.key, event.body or event.text, incident=event.incident)
    else:
        if not live(task):
            return None
        _wake(c, bot, task, event.note or "This ended where it was watched.", "watcher-done")
        return task["id"]
    c.execute("INSERT INTO watcher_items VALUES(?,?,?,?) ON CONFLICT(bot,key) DO UPDATE SET task_id=excluded.task_id",
              (bot, event.key, task["id"], H.now()))
    return task["id"]


def report(c, auth, runner, body, triage=None):
    """A run of a watcher: its events, then the run itself. `triage(c, event)` holds a new HQ ticket for support
    triage (backend/support_triage.py); every other new task is screened for prompt injection (backend/screening.py).
    Without `triage` every event applies at once."""
    hosted = c.execute("SELECT 1 FROM assignments a JOIN bots b ON b.slug=a.bot "
                       "WHERE a.bot=? AND a.runner_id=? AND b.state='active'", (body.bot, runner["id"])).fetchone()
    if not hosted:
        raise Problem("forbidden", "That bot is not active on this computer", 403)
    touched = {}
    for event in body.events:
        waits = (triage if support_triage.applies(body.name, event) else
                 (lambda c, e: screening.watcher_check(c, body.bot, e))) if triage and event.op == "task" else None
        touched[event.key] = deliver(c, auth, body.bot, event, waits) or touched.get(event.key)
    c.execute("INSERT INTO watcher_runs(bot,name,runner_id,started_at,finished_at,exit_code,timed_out,every,output,events) "
              "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(bot,name) DO UPDATE SET runner_id=excluded.runner_id,"
              "started_at=excluded.started_at,finished_at=excluded.finished_at,exit_code=excluded.exit_code,"
              "timed_out=excluded.timed_out,every=excluded.every,output=excluded.output,events=excluded.events",
              (body.bot, body.name, runner["id"], body.started, H.now(), body.exit, int(body.timed_out),
               body.every, body.output, len(body.events)))
    return {"tasks": {k: v for k, v in touched.items() if v}}


def problems(c, online_ids):
    """Watchers that failed their last run or have stopped running, for Health: [(bot, name, why)]."""
    out = []
    late = H.shift(H.now(), minutes=-10)
    for r in c.execute("SELECT w.*,a.runner_id AS host FROM watcher_runs w JOIN bots b ON b.slug=w.bot AND b.state='active' "
                       "LEFT JOIN assignments a ON a.bot=w.bot ORDER BY w.bot,w.name"):
        if r["host"] != r["runner_id"]:
            continue                                   # the bot moved to another computer; its new runs replace this
        if r["timed_out"]:
            out.append((r["bot"], r["name"], "timed out"))
        elif r["exit_code"]:
            out.append((r["bot"], r["name"], f"exited {r['exit_code']}" + (": " + r["output"].strip()[-160:] if r["output"].strip() else "")))
        elif r["host"] in online_ids and r["finished_at"] < min(late, H.shift(H.now(), seconds=-r["every"] * STALE_AFTER)):
            out.append((r["bot"], r["name"], "has not run lately"))
    return out


def install(app, store, auth, execution, mutate):
    @app.post("/api/v2/runners/watchers")
    def watcher_report(request: Request, body: WatcherReport):
        who = request.state.identity

        waited = []

        def triage(c, event):
            if support_triage.engine_for(store, c, getattr(app.state, "judge", None)) is None:
                return False                           # Decide is off: the ticket goes on at once, as before
            waited.append(event.key)
            return support_triage.hold(c, body.bot, event)

        def work(c):
            runner = execution.runner(c, who)
            return report(c, auth, runner, body, triage)
        result = mutate(request, body, work)
        if not waited:                                 # tickets that waited when the server last stopped
            with store.read() as c:
                waited = c.execute("SELECT 1 FROM watcher_pending LIMIT 1").fetchall()
        if waited:
            support_triage.kick(app, store, auth)
        return result
