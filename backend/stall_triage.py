"""Stall triage: the cheap rung between "code finds nothing changed" and a bot's turn (docs/tasks.md).

A quiet task its bot owns, with nothing recorded to wait on (H.stall_skip) and standing exactly as at the bot's last
checkpoint (`wake_rules.at_checkpoint`), used to get no check at all: the stall wake is held (`same_state`) and the task
sat there. Now Tico Decide reads the task's last MESSAGES messages and its state once per stand
(`questions/stall-triage.json`, `H._stall_stand`) and says which it is:

- `waiting_on_someone`: the wait it names is set through the functions a person or bot would use, and no run starts. A
  task (blocked_by), a date (snooze) or a person (the owner's own `waiting_on`, with its plain ask). A target Decide
  cannot name from the options built out of the messages, or that a rule refuses, is `unsure`.
- `finished_unmarked`: the task is closed with a note crediting the last person or bot who spoke on it, which can be
  reopened.
- `stuck`: one wake with today's way-out text. The ledger row carries the go-ahead (`go_ahead`); H.wake_stalled sends the
  wake at its usual place, so the doubling gap and the daily cap of attempted wakes still hold, and marks it sent.
- `unsure` (and anything below its threshold): recorded, and named in the Task Manager's next quiet-task item.

Nothing is automatic after that: a verdict is kept per (task, stand) and the task is not asked about again until it
changes. A stand with no verdict is today's behaviour exactly: the held check stays silent. So Decide off, not configured,
over its hourly or daily limit, out of the spend cap, an error or a task that changed meanwhile all leave things as they
were (fail open). On by default; the team's `stall_triage` rule turns it off (Settings > System routines).

The model is never called inside a database transaction: `inputs` runs in the scheduler's read, `run` reads each task
again, asks outside any transaction, and writes in a new transaction that checks the task still stands as it did. One
`judge.call` event per call (label `stall-triage@1`), one `stall_triage.decided` event per verdict. Calls run on the pass's
own thread (`tick`), at most CALLS_PER_TICK a minute."""

import json
import logging
import re
import threading

from clients import judge as J
from . import task_relations as TR
from . import team_rules
from . import wake_rules
from .judge import CHAIN_SECONDS, audit_detail, daily_cap, primary_engine, server_engine, used_today
from .store import H

LOG = logging.getLogger("tico.stall_triage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stall_triage(
 task_id TEXT PRIMARY KEY, stand TEXT NOT NULL, class TEXT NOT NULL, confidence REAL, outcome TEXT NOT NULL,
 detail TEXT, judged_at TEXT NOT NULL, woke_at TEXT);
"""

QUESTION_SET = "stall-triage"
RULE = "stall_triage"
WAITING, FINISHED, STUCK, UNSURE = "waiting_on_someone", "finished_unmarked", "stuck", "unsure"
CLASSES = (WAITING, FINISHED, STUCK, UNSURE)
MESSAGES, MESSAGE_CHARS = 6, 600
CALLS_PER_TICK = 5
CALLS_PER_HOUR = 60
OPTIONS_EACH = 3
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
DATE = re.compile(r"\b(20\d\d-[01]\d-[0-3]\d)\b")
_RUNNING = threading.Lock()         # one pass at a time in this process


def enabled(c):
    return bool(team_rules.load(c)[RULE])


# ----------------------------------------------------------------------------- the ledger
def verdict_of(c, task_id):
    row = c.execute("SELECT * FROM stall_triage WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def class_of(c, row):
    """The class Decide gave this task at the way it stands now (`unsure` or `stuck`, the two a person may still have to
    act on), or None: no verdict, or the task changed since. For the Task Manager's quiet-task items."""
    try:
        found = verdict_of(c, row["id"])
        if found and found["class"] in (UNSURE, STUCK) and found["stand"] == H._stall_stand(c, row):
            return found["class"]
    except Exception:       # noqa: BLE001 - a missing table or an unreadable task is no class
        pass
    return None


def go_ahead(c, row, stand):
    """Whether Decide said this task is stuck at this stand and its one wake has not been sent (H.wake_stalled)."""
    found = verdict_of(c, row["id"])
    return bool(found and found["stand"] == stand and found["outcome"] == STUCK and not found["woke_at"])


def sent(c, row, at):
    c.execute("UPDATE stall_triage SET woke_at=? WHERE task_id=? AND woke_at IS NULL", (at, row["id"]))


def _record(c, row, stand, cls, confidence, outcome, at, detail=None):
    c.execute("INSERT INTO stall_triage(task_id,stand,class,confidence,outcome,detail,judged_at) VALUES(?,?,?,?,?,?,?) "
              "ON CONFLICT(task_id) DO UPDATE SET stand=excluded.stand,class=excluded.class,"
              "confidence=excluded.confidence,outcome=excluded.outcome,detail=excluded.detail,"
              "judged_at=excluded.judged_at,woke_at=NULL",
              (row["id"], stand, cls, round(float(confidence or 0), 3), outcome, detail, at))
    H.event(c, H.KEEPER, "stall_triage.decided", row["id"],
            {"bot": H.actor_id(row["owner"]), "class": cls, "confidence": round(float(confidence or 0), 3),
             "outcome": outcome, **({"detail": detail} if detail else {})})


# ----------------------------------------------------------------------------- what to ask about (plain code)
def _wake_open(c, row, at):
    """Whether the stall check could send a wake for this task now: not within the repeat gap of the last one and not
    past the day's cap (the two limits H.wake_stalled keeps)."""
    last, count = c.execute("SELECT max(ts), count(CASE WHEN ts>? THEN 1 END) FROM events "
                            "WHERE action='task.stall_wake' AND target=?",
                            (H.shift(at, seconds=-86400), row["id"])).fetchone()
    return not (last and last > H.shift(at, seconds=-H.STALL_REPEAT_MINUTES * 60)) and (count or 0) < H.STALL_WAKES_PER_DAY


def inputs(c, at, rows):
    """[{"id", "stand"}] of the tasks among `rows` (H.stalled_tasks / H.spot_check_tasks, already past H.stall_skip) that
    Decide has not read at their present stand: it stands as at its owner's last checkpoint, no recorded wait, its bot is
    set up and its stall wake could be sent. At most CALLS_PER_TICK, oldest first. Reads only."""
    if not rows or not enabled(c):
        return []
    parked, out, seen = H.parked_bots(c), [], set()
    for row in rows:
        if row["id"] in seen or row.get("held") or row.get("own_blockers") or H.actor_id(row["owner"]) in parked:
            continue
        if H.task_private(c, row):
            continue                # its messages stay with its parties: Decide never reads them
        seen.add(row["id"])
        if not wake_rules.at_checkpoint(c, row, row["owner"]) or not _wake_open(c, row, at):
            continue
        stand = H._stall_stand(c, row)
        found = verdict_of(c, row["id"])
        if found and found["stand"] == stand:
            continue
        out.append({"id": row["id"], "stand": stand})
        if len(out) >= CALLS_PER_TICK:
            break
    return out


# ----------------------------------------------------------------------------- what Decide sees
def _who(c, row, actor):
    if actor == H.KEEPER:
        return "Tico"
    if actor == row["owner"]:
        return "the bot that owns it"
    if H.is_human(actor):
        return "person: " + H.person_name(c, actor)
    return "another bot: " + ((H.bot(c, H.actor_id(actor)) or {}).get("display_name") or H.actor_id(actor))


def _messages(c, row):
    out = []
    for msg in H.task_comments(c, row["id"])[-MESSAGES:]:
        text = " ".join(str(msg.get("body") or "").split())
        out.append({"from": _who(c, row, msg.get("from_actor")), "at": str(msg.get("created_at") or "")[:16],
                    "text": text[:MESSAGE_CHARS]})
    return out


def _options(c, row, at):
    """The things the task could wait on that its last messages name, as {key: (kind, ref, words)}: a person other than
    its owner, a task another message mentions that is open and public, and a future date. Opaque keys: the model reads
    the words, the code maps the key back."""
    found = {}
    messages = H.task_comments(c, row["id"])[-MESSAGES:]
    people = []
    for actor in [m.get("from_actor") for m in reversed(messages)] + [row["requester"]]:
        if actor and H.is_human(actor) and actor not in people and H._actor_exists(c, actor):
            people.append(actor)
    for n, actor in enumerate(people[:OPTIONS_EACH], 1):
        found[f"person_{n}"] = ("person", actor, f"Waiting on {H.person_name(c, actor)}, a person on this task")
    text = " ".join(str(m.get("body") or "") for m in messages)
    tasks = []
    for tid in UUID.findall(text):
        other = H.task(c, tid) if tid != row["id"] and tid not in tasks else None
        if other and other["status"] in H.ACTIVE_STATUSES and not H.task_private(c, other):
            tasks.append(tid)
    for n, tid in enumerate(tasks[:OPTIONS_EACH], 1):
        found[f"task_{n}"] = ("task", tid, f"Waiting on the task '{H.task(c, tid)['title'][:100]}' to finish")
    dates = []
    for day in DATE.findall(text):
        stamp = H.TS.parse_until(day)
        if stamp and at < stamp <= H.shift(at, days=H.TS.MAX_DAYS) and stamp not in [d[0] for d in dates]:
            dates.append((stamp, day))
    for n, (stamp, day) in enumerate(dates[:OPTIONS_EACH], 1):
        found[f"date_{n}"] = ("date", day, f"Waiting until {day}")
    return found


def state_of(c, row, at):
    """(state sent to Decide, the options it may name). Titles, bodies and messages are text; the question says to treat
    them as data."""
    step = c.execute("SELECT name FROM task_steps WHERE id=?", (row["step_id"],)).fetchone() if row.get("step_id") else None
    quiet = max(0, int((H.parse_ts(at) - H.parse_ts(row["updated_at"])).total_seconds() // 60))
    options = _options(c, row, at)
    return ({"task": {"title": row["title"] or "", "status": row["status"], "step": step[0] if step else "",
                      "quiet_minutes": quiet, "messages": _messages(c, row)}}, options)


def calls_this_hour(c, at, label):
    return c.execute("SELECT count(*) FROM events WHERE action='judge.call' AND target=? AND ts>?",
                     (label, H.shift(at, seconds=-3600))).fetchone()[0]


# ----------------------------------------------------------------------------- what comes of it
def _last_speaker(c, row):
    """Who spoke last on the task, in words, for the note on a close: a person, a bot, never Tico."""
    for msg in reversed(H.task_comments(c, row["id"])):
        actor = msg.get("from_actor")
        if actor and actor != H.KEEPER:
            if H.is_human(actor):
                return H.person_name(c, actor)
            return (H.bot(c, H.actor_id(actor)) or {}).get("display_name") or H.actor_id(actor)
    return (H.bot(c, H.actor_id(row["owner"])) or {}).get("display_name") or H.actor_id(row["owner"])


def _set_wait(c, row, kind, ref):
    """Set the wait `kind` names, as the writer who would: a blocker or a snooze as Tico, a person as the owner bot.
    Raises H.Refused when a rule says no."""
    if kind == "task":
        TR.relate(c, H.KEEPER, row["id"], ref, "blocked_by", mover=True, note="the stall check, from the latest messages")
        why = f"it waits on task {ref}"
    elif kind == "date":
        H.TS.snooze(c, H.KEEPER, row["id"], ref, mover=True)
        why = f"it waits until {ref}"
    else:
        name = H.person_name(c, ref)
        H.task_update(c, row["owner"], row["id"], status="waiting", waiting_on=ref,
                      note="Can you reply on this task when you can?")
        why = f"it waits on {name}"
    H.task_comment(c, H.KEEPER, row["id"], f"Waiting: the stall check read the latest messages and {why}. "
                   "Change it if that is wrong.", wake=False)


def _apply(c, row, stand, cls, confidence, target, at):
    """One verdict, in the write transaction, with the task already checked unchanged. Returns the outcome."""
    if cls == STUCK:
        _record(c, row, stand, cls, confidence, STUCK, at)
        return STUCK
    if cls == WAITING and target:
        c.execute("SAVEPOINT stall_triage")
        try:
            _set_wait(c, row, *target)
        except H.Refused as exc:
            c.execute("ROLLBACK TO stall_triage")
            c.execute("RELEASE stall_triage")
            _record(c, row, stand, UNSURE, confidence, UNSURE, at, "refused: " + exc.rule)
            return UNSURE
        c.execute("RELEASE stall_triage")
        _record(c, row, stand, cls, confidence, "wait_set", at, target[0])
        return "wait_set"
    if cls == FINISHED:
        who = _last_speaker(c, row)
        c.execute("SAVEPOINT stall_triage")
        try:
            H.task_close(c, H.KEEPER, row["id"], quiet=True, note=(
                f"Closed by the stall check: the last word on it was {who}'s, and it reads as finished though nobody "
                "marked it done. Reopen it if there is more to do."))
        except H.Refused as exc:
            c.execute("ROLLBACK TO stall_triage")
            c.execute("RELEASE stall_triage")
            _record(c, row, stand, UNSURE, confidence, UNSURE, at, "refused: " + exc.rule)
            return UNSURE
        c.execute("RELEASE stall_triage")
        _record(c, row, stand, cls, confidence, "closed", at, who)
        return "closed"
    _record(c, row, stand, UNSURE, confidence, UNSURE, at)
    return UNSURE


def _still(c, row, stand, at):
    """The task as it is now if it still stands as Decide read it: the same stand, still the bot's, nothing recorded to
    wait on, still the bot's own checkpoint. Else None."""
    now = H.task(c, row["id"])
    if (not now or now["status"] not in ("open", "doing") or not enabled(c) or H.stall_skip(c, now, at)
            or not H.is_bot(now["owner"]) or H._stall_stand(c, now) != stand
            or not wake_rules.at_checkpoint(c, now, now["owner"])):
        return None
    return now


def _stop(c, settings, at, label):
    if not enabled(c):
        return "switched_off"
    if calls_this_hour(c, at, label) >= CALLS_PER_HOUR:
        return "hourly_cap"
    if used_today(c, H.KEEPER, at) >= daily_cap(settings, "bot"):
        return "daily_cap"
    return None


def decide(answers, options, thresholds):
    """(class, confidence, target) from the answers: a class under its threshold is `unsure`, a wait with no target Decide
    can name is `unsure`. `target` is (kind, ref) or None."""
    cls, confidence = J.choice(answers, "class")
    if cls not in CLASSES or confidence < thresholds.get(cls, 1.01):
        return UNSURE, confidence, None
    if cls != WAITING:
        return cls, confidence, None
    key, sure = J.choice(answers, "waiting_on") if "waiting_on" in answers else ("unclear", 0.0)
    if key not in options or sure < thresholds.get("wait_target", 1.01):
        return UNSURE, confidence, None
    kind, ref, _ = options[key]
    return cls, confidence, (kind, ref)


# ----------------------------------------------------------------------------- the pass
def run(store, found, at=None, engine=None):
    """Ask Decide about each of `found` (`inputs`) and apply what it says. On the caller's thread; the model is never
    called in a transaction. Returns what it did, or None when there was nothing to ask."""
    at = at or H.now()
    if not found:
        return None
    settings = store.settings
    try:
        qset = J.load_set(QUESTION_SET)
    except J.JudgeError as exc:
        LOG.warning("Stall triage has no question set: %s", exc.detail)
        return {"asked": 0, "why": "unavailable"}
    label = qset["label"]
    if engine is None:
        with store.read() as c:
            engine = server_engine(store, c, primary_engine(settings, store))
    summary = {"asked": 0, "applied": {}, "errors": 0, "changed": 0, "why": None}
    if engine is None:
        summary["why"] = "unavailable"
        return summary
    for item in found:
        with store.read() as c:
            summary["why"] = _stop(c, settings, at, label)
            row = None if summary["why"] else _still(c, {"id": item["id"]}, item["stand"], at)
            state, options = state_of(c, row, at) if row else (None, {})
        if summary["why"]:
            break
        if row is None:
            summary["changed"] += 1
            continue
        questions = {"class": qset["questions"]["class"]}
        if options:
            questions["waiting_on"] = J.with_options(qset["questions"]["waiting_on"],
                                                     {key: words for key, (_, _, words) in options.items()})
        summary["asked"] += 1
        result, error, answers = None, None, None
        try:
            result = engine(state, questions, label)
            answers = J.check_answers(result.get("answers"), questions)
        except J.JudgeError as exc:
            error = J.error_code(exc)
        except Exception as exc:        # noqa: BLE001 - a fake, a provider bug: the task is asked again later
            LOG.warning("Stall triage call failed: %s", type(exc).__name__)
            error = "error"
        detail = {"label": label, "questions": len(questions), "types": ["choice"] * len(questions), "task": row["id"]}
        if isinstance(result, dict):
            detail.update(audit_detail(result))
        cls, confidence, target = UNSURE, 0.0, None
        if error:
            detail["error"] = error
            summary["errors"] += 1
        else:
            cls, confidence, target = decide(answers, options, qset["thresholds"])
            detail["answers"] = J.summary(answers)
        with store.transaction() as c:
            H.event(c, H.KEEPER, "judge.call", label, detail)       # the call happened, whatever comes of it
            if error:
                continue
            now = _still(c, row, item["stand"], at)
            if now is None:
                summary["changed"] += 1
                continue
            outcome = _apply(c, now, item["stand"], cls, confidence, target, at)
            summary["applied"][outcome] = summary["applied"].get(outcome, 0) + 1
    return summary


def tick(store, found, at=None, engine=None):
    """From the scheduler: run `found` on its own thread, so no call holds the scheduler or the write lock. One pass at
    a time. Returns the thread, or None when nothing started."""
    if not found or store.settings.rehearsal:
        return None
    if not _RUNNING.acquire(blocking=False):
        return None

    def work():
        try:
            run(store, found, at, engine)
        except Exception as exc:    # noqa: BLE001
            LOG.warning("Stall triage pass failed: %s", type(exc).__name__)
        finally:
            _RUNNING.release()
    thread = threading.Thread(target=work, name="stall-triage", daemon=True)
    thread.start()
    return thread
