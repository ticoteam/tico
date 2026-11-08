"""Stale ticket review: once an hour, plain code finds the open tasks that look finished or forgotten, Tico Decide
judges only the ones that are new since the last pass (`questions/stale-review.json`), and each owner gets a suggestion,
never an action.

A task is a candidate (open, doing, waiting or review, not snoozed, nothing running on it) when at least one is true:
its linked PRs are all merged, closed or shipped; its parent is done or closed; everything it is blocked by is done;
its snooze ended over a day ago with nothing since; it has the title of an older open task of the same owner; nothing
happened on it for QUIET_DAYS (doing: QUIET_DOING_DAYS).

Only a new candidate is judged: one with no `stale_judged` row at its current mark (version, last change, last
checkpoint) and no suggestion in the last SUGGEST_EVERY_DAYS. A pass with no new candidate ends there: no Decide call,
no event, no note; only the watermark (the last pass's time, `registry_metadata` WATERMARK) moves. At most CALLS_PER_HOUR
Decide calls a team an hour, counted from the `judge.call` audit; the rest wait for the next pass, as does a candidate
whose call failed, ran out of the pass's time, or found Decide off or over its cap. Nothing changes for them.

What Decide sees: the title, status, step, dates, the candidate reason and the last COMMENTS comments. What it answers:
update (mark done), close, ask the owner, or leave. What comes of it:
- a bot owner: one "Stale check: <reason>. Suggest: <x>." comment on the task, addressed to it, which wakes it;
- a person owner: a row on their one "Stale tickets: N suggestions" card in Needs you (`card`), with Accept and Skip,
  and Accept all. Accept is the person's own change (`accept`), refused exactly as by hand when it isn't theirs to make.
Each suggestion keeps its outcome (accepted, skipped, or the task's next change) for the acceptance share.

Off unless the team's `stale_review` rule is on (Settings > General; backend/team_rules.py). The calls run on the pass's
own thread, off the write lock: one can take up to `judge.CHAIN_SECONDS`, and the pass stops before PASS_SECONDS so it
never runs into the next hour. One `stale_review.pass` event per pass that had a new candidate: the counts, the cost
and which route answered (the Gateway, the team's own TypeSafe key, or the team's AI provider)."""

import json
import logging
import re
import threading
import time

from fastapi import Request

from clients import judge as J
from . import task_relations as TR
from . import team_rules
from .judge import CHAIN_SECONDS, audit_detail, daily_cap, primary_engine, server_engine, used_today
from .store import H, Problem

LOG = logging.getLogger("tico.stale_review")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stale_judged(
 task_id TEXT PRIMARY KEY, mark TEXT NOT NULL, verdict TEXT NOT NULL, judged_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stale_suggestions(
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL, owner TEXT NOT NULL, reason TEXT NOT NULL, verdict TEXT NOT NULL,
 action TEXT, suggest TEXT NOT NULL, confidence REAL, mark TEXT NOT NULL, created_at TEXT NOT NULL,
 outcome TEXT, outcome_at TEXT, outcome_by TEXT);
CREATE INDEX IF NOT EXISTS stale_suggestions_task ON stale_suggestions(task_id, created_at);
CREATE INDEX IF NOT EXISTS stale_suggestions_open ON stale_suggestions(owner, outcome);
"""

QUESTION_SET = "stale-review"
RULE = "stale_review"
WATERMARK = "stale_review"
STATUSES = ("open", "doing", "waiting", "review")
EVERY_SECONDS = 3600            # the default schedule (Settings > Routines may space passes further)
CALLS_PER_HOUR = 30
PASS_SECONDS = 50 * 60          # the pass stops asking here, so it never runs into the next one
SUGGEST_EVERY_DAYS = 7
QUIET_DAYS, QUIET_DOING_DAYS = 7, 3
SNOOZE_GRACE_HOURS = 24
COMMENTS, COMMENT_CHARS = 3, 600
FINISHED_PR = ("merged", "closed", "shipped")
UPDATE, CLOSE, ASK_OWNER, LEAVE = "update", "close", "ask_owner", "leave"
# What each verdict suggests: (action Accept takes, the bot comment's words, the person row's words).
SUGGEST = {
    UPDATE: ("done", "mark done", "Mark done"),
    CLOSE: ("close", "close it", "Close it"),
    ASK_OWNER: (None, "check it is still needed, then update or close it", "Check it is still needed"),
}
ROUTES = {J.GATEWAY_PROVIDER: "gateway", J.TYPESAFE_PROVIDER: "own_key",
          **{name: "team_llm" for name in J.LLM_PROVIDERS.values()}}
_RUNNING = threading.Lock()     # one pass at a time in this process


# ----------------------------------------------------------------------------- the switch and the watermark
def enabled(c):
    return bool(team_rules.load(c)[RULE])


def last_pass(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (WATERMARK,)).fetchone()
    try:
        return (json.loads(row[0]) or {}).get("last_pass") if row else None
    except (ValueError, AttributeError):
        return None


def _set_watermark(c, at):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (WATERMARK, json.dumps({"last_pass": at})))


def due(c, at):
    """Whether the saved schedule (Settings > Routines, hourly by default) says a pass is due since the last one."""
    from . import system_routines as SR
    last = last_pass(c)
    return SR.due(SR.config_of(c, RULE), H.parse_ts(at), H.parse_ts(last) if last else None)


# ----------------------------------------------------------------------------- candidates (plain code)
def mark(row):
    """What "unchanged" means for a judged task: its version, its last change and its last checkpoint."""
    return f"{row.get('version') or 0}|{row.get('updated_at') or ''}|{row.get('checkpoint_at') or ''}"


def _day(ts):
    at = H.parse_ts(ts)
    return f"{at:%b} {at.day}" if at else ""


def _pr_number(link):
    if link.get("number"):
        return link["number"]
    found = re.search(r"/pull/(\d+)", str(link.get("url") or ""))
    return int(found[1]) if found else None


def _pr_reason(c, row):
    links = H._rows(c.execute("SELECT * FROM task_links WHERE task_id=? AND kind='pr' ORDER BY created_at", (row["id"],)))
    if not links or any(link.get("state") not in FINISHED_PR for link in links):
        return None                                  # no PR, or one still open
    last = links[-1]
    number = _pr_number(last)
    when = _day(last.get("pr_merged_at")) if last.get("state") in ("merged", "shipped") else ""
    return " ".join(part for part in ("PR" + (f" #{number}" if number else ""), last["state"], when) if part)


def _parent_reason(c, row):
    parent = H.task(c, TR.parent_of(c, row["id"]) or "")
    # A company task filed from private work sits under a private parent: its state never goes to Decide.
    if parent and not H.task_private(c, parent) and parent["status"] in ("done", "closed"):
        return f"Its parent task is {parent['status']}"
    return None


def _blocker_reason(c, row):
    statuses = [r[0] for r in c.execute("SELECT t.status FROM task_relations r JOIN tasks t ON t.id=r.from_task "
                                        "WHERE r.to_task=? AND r.kind='blocks'", (row["id"],))]
    if statuses and all(status == "done" for status in statuses):
        return "Everything it was blocked by is done"
    return None


def _snooze_reason(c, row, at):
    ended = c.execute("SELECT created_at FROM task_events WHERE task_id=? AND field='snoozed_until' AND note='snooze over' "
                      "ORDER BY created_at DESC LIMIT 1", (row["id"],)).fetchone()
    if not ended or ended[0] > H.shift(at, hours=-SNOOZE_GRACE_HOURS):
        return None
    # The wake itself writes `updated_at`; anything later is activity.
    if max(row.get("updated_at") or "", row.get("checkpoint_at") or "") > H.shift(ended[0], minutes=1):
        return None
    return f"Its snooze ended {_day(ended[0])} and nothing happened since"


def _twin_reason(c, row):
    marks = ",".join("?" * len(STATUSES))
    key = H.title_key(row["title"])
    twin = next((r for r in c.execute(f"SELECT id,created_at,title FROM tasks WHERE owner_id=? AND id<>? AND status IN ({marks}) "
                                      "AND (created_at<? OR (created_at=? AND id<?)) ORDER BY created_at",
                                      (row["owner_id"], row["id"], *STATUSES, row["created_at"] or "",
                                       row["created_at"] or "", row["id"]))
                 if H.title_key(r[2]) == key), None)
    return f"Same title as an older open task of the same owner (from {_day(twin[1])})" if twin else None


def _quiet_reason(row, at):
    days = QUIET_DOING_DAYS if row["status"] == "doing" else QUIET_DAYS
    last = max(row.get("updated_at") or "", row.get("checkpoint_at") or "")
    if last and last < H.shift(at, days=-days):
        then, now = H.parse_ts(last), H.parse_ts(at)
        return f"Nothing happened on it for {(now - then).days} days"
    return None


def reason(c, row, at):
    """Why this task looks stale, in words a person reads, or None."""
    return (_pr_reason(c, row) or _parent_reason(c, row) or _blocker_reason(c, row) or _snooze_reason(c, row, at)
            or _twin_reason(c, row) or _quiet_reason(row, at))


def _live(c, task_id):
    try:
        return c.execute("SELECT 1 FROM turns t JOIN attempts a ON a.id=t.id WHERE t.task_id=? "
                         "AND a.state IN ('leased','running') LIMIT 1", (task_id,)).fetchone() is not None
    except Exception:               # a local hub has no attempts table
        return False


def _owner_reachable(c, owner):
    if H.is_bot(owner):
        return (H.bot(c, H.actor_id(owner)) or {}).get("state") == "active"
    return H.is_human(owner) and H._actor_exists(c, owner)


def candidates(c, at):
    """[(task row, reason)] for every task plain code finds stale now. A private task is never one: its words never
    go to Decide."""
    marks = ",".join("?" * len(STATUSES))
    out = []
    for row in H._rows(c.execute(f"SELECT * FROM tasks WHERE status IN ({marks}) AND private=0 ORDER BY created_at,id",
                                 STATUSES)):
        if H.TS.snoozed(row, at) or not (H.is_bot(row["owner_id"]) or H.is_human(row["owner_id"])):
            continue
        why = reason(c, row, at)
        if why and not _live(c, row["id"]):
            out.append((row, why))
    return out


def new_candidates(c, at):
    """The candidates Decide has not judged at their current mark, with no suggestion this week, whose owner can
    hear one."""
    judged = {r[0]: r[1] for r in c.execute("SELECT task_id,mark FROM stale_judged")}
    recent = {r[0] for r in c.execute("SELECT DISTINCT task_id FROM stale_suggestions WHERE created_at>?",
                                      (H.shift(at, days=-SUGGEST_EVERY_DAYS),))}
    return [(row, why) for row, why in candidates(c, at)
            if judged.get(row["id"]) != mark(row) and row["id"] not in recent and _owner_reachable(c, row["owner_id"])]


# ----------------------------------------------------------------------------- what Decide sees
def _comments(c, row):
    out = []
    for msg in H.task_comments(c, row["id"])[-COMMENTS:]:
        text = str(msg.get("body") or "").strip()
        out.append({"from": "a person" if H.is_human(msg.get("sender_id")) else "a bot",
                    "at": str(msg.get("created_at") or "")[:16], "text": text[:COMMENT_CHARS]})
    return out


def state_of(c, row, why):
    step = c.execute("SELECT name FROM task_steps WHERE id=?", (row["step_id"],)).fetchone() \
        if row.get("step_id") else None
    return {"task": {"title": row["title"] or "", "status": row["status"], "step": step[0] if step else "",
                     "created_at": row.get("created_at") or "", "updated_at": row.get("updated_at") or "",
                     "due_at": row.get("due_at") or "", "reason": why, "comments": _comments(c, row)}}


def calls_this_hour(c, at, label):
    return c.execute("SELECT count(*) FROM events WHERE action='judge.call' AND target=? AND created_at>?",
                     (label, H.shift(at, seconds=-EVERY_SECONDS))).fetchone()[0]


def route_of(result):
    return ROUTES.get((result or {}).get("provider") or "", "other")


# ----------------------------------------------------------------------------- outcomes
def _record_outcomes(c, at):
    """A suggestion nobody answered whose task changed since: the task's next change is its outcome."""
    for sid, task_id, was in c.execute("SELECT id,task_id,mark FROM stale_suggestions WHERE outcome IS NULL").fetchall():
        row = H.task(c, task_id)
        if not row:
            c.execute("UPDATE stale_suggestions SET outcome='gone',outcome_at=? WHERE id=?", (at, sid))
        elif mark(row) != was or row["status"] not in STATUSES:
            c.execute("UPDATE stale_suggestions SET outcome=?,outcome_at=? WHERE id=?", ("changed:" + row["status"], at, sid))


def _judged(c, task_id, row, verdict, at):
    c.execute("INSERT INTO stale_judged VALUES(?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
              "mark=excluded.mark,verdict=excluded.verdict,judged_at=excluded.judged_at",
              (task_id, mark(row), verdict, at))


def _apply(c, row, why, verdict, confidence, at):
    """One judged candidate's result, in the write transaction: the comment or the Needs you row, then the judged
    record at the task's mark after it, so our own comment never makes the task look new."""
    if verdict == LEAVE:
        _judged(c, row["id"], row, verdict, at)
        return None
    action, bot_words, person_words = SUGGEST[verdict]
    sid = H.new_id()
    if H.is_bot(row["owner_id"]):
        text = f"Stale check: {why}. Suggest: {bot_words}."
        H.task_comment(c, H.KEEPER, row["id"], text, extra_refs={"stale_check": sid, "wake": "stall"})
        after = H.task(c, row["id"])
        suggest = bot_words
    else:
        after, suggest = row, person_words
    c.execute("INSERT INTO stale_suggestions(id,task_id,owner,reason,verdict,action,suggest,confidence,mark,created_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)",
              (sid, row["id"], row["owner_id"], why, verdict, action, suggest, round(confidence, 3), mark(after), at))
    _judged(c, row["id"], after, verdict, at)
    return sid


# ----------------------------------------------------------------------------- the pass
def eligible(c, row, at):
    """The task as it is now if it is still the candidate the pass found: unchanged since, open, not private, not
    snoozed, nobody working it, and an owner who can hear a suggestion. Starting a turn does not change the task's
    mark, so a call in flight is checked again before its answer is used."""
    now = H.task(c, row["id"])
    if (not now or mark(now) != mark(row) or now["status"] not in STATUSES or H.task_private(c, now)
            or H.TS.snoozed(now, at)
            or _live(c, now["id"]) or not _owner_reachable(c, now["owner_id"])):
        return None
    return now


def _stop(c, settings, at, label):
    """Why the pass makes no more calls now, or None: the switch went off, or this hour's or today's calls are used."""
    if not enabled(c):
        return "switched_off"
    if calls_this_hour(c, at, label) >= CALLS_PER_HOUR:
        return "hourly_cap"
    if used_today(c, H.KEEPER, at) >= daily_cap(settings, "bot"):
        return "daily_cap"
    return None


def _claim(store, at, force=False):
    """In one transaction: the switch, the schedule and the watermark. The new candidates, or None when no pass is due.
    `force` (Run now) skips only the schedule."""
    with store.transaction() as c:
        if not enabled(c) or not (force or due(c, at)):
            return None
        _set_watermark(c, at)
        _record_outcomes(c, at)
        return new_candidates(c, at)


def run_pass(store, at=None, engine=None, clock=time.monotonic, force=False):
    """One pass, start to end, on the caller's thread (the scheduler's own thread, or a test). Returns what it did,
    or None when no pass was due. `engine` is the decision model (a test's fake); by default the server's. `force` is
    Run now: due or not, with the same per-call limits."""
    at = at or H.now()
    started = clock()
    found = _claim(store, at, force)
    if not found:
        return None if found is None else {"new": 0}
    settings = store.settings
    try:
        qset = J.load_set(QUESTION_SET)
    except J.JudgeError as exc:
        LOG.warning("Stale ticket review has no question set: %s", exc.detail)
        qset = None
    label = qset["label"] if qset else QUESTION_SET
    if engine is None and qset:
        with store.read() as c:
            engine = server_engine(store, c, primary_engine(settings, store))
    summary = {"candidates": len(found), "new": len(found), "calls": 0, "suggested": 0, "left": 0,
               "deferred": 0, "errors": 0, "changed": 0, "routes": {}, "cost_usd": 0.0, "fallback": None}
    if engine is None or qset is None:
        summary["deferred"], summary["why"] = len(found), "unavailable"
    else:
        questions = {"verdict": qset["questions"]["verdict"]}
        threshold = qset["thresholds"].get("suggest", 1.01)
        for n, (row, why) in enumerate(found):
            # Each call is admitted on its own: the switch, this hour's and today's allowance read from the audit log
            # as they are now, and the task still the candidate that was found.
            with store.read() as c:
                stop = _stop(c, settings, at, label) or (
                    "time" if clock() - started > PASS_SECONDS - CHAIN_SECONDS else None)
                now = None if stop else eligible(c, row, at)
                state = state_of(c, now, why) if now else None
            if stop:
                summary["deferred"] += len(found) - n
                summary["why"] = stop
                break
            if now is None:
                summary["changed"] += 1         # it moved or is being worked: the next pass sees it fresh
                continue
            summary["calls"] += 1
            result, error = None, None
            try:
                result = engine(state, questions, label)
                answers = J.check_answers(result.get("answers"), questions)
                verdict, confidence = J.choice(answers, "verdict")
            except J.JudgeError as exc:
                error = J.error_code(exc)
            except Exception as exc:    # a fake, a provider bug: the task waits for the next pass
                LOG.warning("Stale ticket review call failed: %s", type(exc).__name__)
                error = "error"
            detail = {"label": label, "questions": 1, "types": ["choice"], "task": row["id"]}
            if result is not None and isinstance(result, dict):
                detail.update(audit_detail(result))
                summary["cost_usd"] = round(summary["cost_usd"] + float(result.get("cost_usd") or 0), 6)
                route = route_of(result)
                summary["routes"][route] = summary["routes"].get(route, 0) + 1
                if result.get("fallback") and not summary["fallback"]:
                    summary["fallback"] = {"code": result["fallback"],
                                           "from": route_of({"provider": result.get("fallback_from")})}
            if error:
                detail["error"] = error
                summary["errors"] += 1
                summary["deferred"] += 1
            else:
                if verdict not in SUGGEST or confidence < threshold:
                    verdict = LEAVE
                detail["verdict"] = {"value": verdict, "confidence": round(confidence, 3)}
                detail["answers"] = J.summary(answers)     # what Decide answered, before the threshold left it
            off = False
            with store.transaction() as c:
                H.event(c, H.KEEPER, "judge.call", label, detail)     # the call happened, whatever comes of it
                if error:
                    continue
                off = not enabled(c)
                now = None if off else eligible(c, row, at)
                if now is not None:
                    if _apply(c, now, why, verdict, confidence, at):
                        summary["suggested"] += 1
                    else:
                        summary["left"] += 1
            if off:                             # turned off while Decide was asked: nothing more is said or asked
                summary["deferred"] += len(found) - n
                summary["why"] = "switched_off"
                break
            if now is None:
                summary["changed"] += 1
    with store.transaction() as c:
        H.event(c, H.KEEPER, "stale_review.pass", label, summary)
    return summary


def tick(store, at=None, force=False, engine=None):
    """From the scheduler: when a pass is due, run it on its own thread, so no call holds the scheduler or the write
    lock. One pass at a time; the watermark keeps a restart from running two inside the schedule. `force` is Run now
    (Settings > Routines): the pass runs though not due, still only while the switch is on and within the same caps.
    Returns the thread, or None when nothing started."""
    settings = store.settings
    if settings.rehearsal:
        return None
    at = at or H.now()
    with store.read() as c:
        if not enabled(c) or not (force or due(c, at)):
            return None
    if not _RUNNING.acquire(blocking=False):
        return None

    def work():
        from . import system_routines as SR
        run = SR.Run("stale_review")
        try:
            summary = run_pass(store, at, engine=engine, force=force)
            if summary is not None:
                words(run, summary)
        except Exception as exc:
            LOG.warning("Stale ticket review pass failed: %s", type(exc).__name__)
            run.error = SR.failure_words(exc)
            summary = {}
        finally:
            _RUNNING.release()
        if summary is not None:
            SR.write(store, run)
    thread = threading.Thread(target=work, name="stale-review", daemon=True)
    thread.start()
    return thread


WHY_WORDS = {"unavailable": "Decide unavailable", "switched_off": "switched off", "hourly_cap": "hourly limit reached",
             "daily_cap": "daily limit reached", "time": "out of time"}


def words(run, summary):
    """A pass's summary as its System routines run: "9 checked, 2 suggestions", the route and the cost."""
    if not summary.get("candidates"):
        return run.did("No new candidates")
    parts = [f"{summary.get('calls', 0)} checked",
             f"{summary.get('suggested', 0)} suggestion{'' if summary.get('suggested') == 1 else 's'}"]
    if summary.get("deferred"):
        why = WHY_WORDS.get(summary.get("why"), "")
        parts.append(f"{summary['deferred']} left for later" + (f" ({why})" if why else ""))
    run.did(", ".join(parts))
    if summary.get("calls") and summary.get("errors") == summary.get("calls") and not summary.get("routes"):
        run.error = "Failed: Decide did not answer"
    if summary.get("routes") or summary.get("cost_usd"):
        run.ai(summary.get("routes"), summary.get("fallback"), summary.get("cost_usd"))
    return run


# ----------------------------------------------------------------------------- the person's card
TITLE = "Stale tickets: {n} suggestion{s}"


def pending(c, person):
    """The open suggestions made to the person whose task is still open, oldest first. A task handed to someone else
    since stays on their card until the next pass records the change: Accept is then refused as by hand."""
    out = []
    for row in H._rows(c.execute("SELECT s.* FROM stale_suggestions s LEFT JOIN tasks t ON t.id=s.task_id "
                                 "WHERE s.owner=? AND s.outcome IS NULL ORDER BY s.created_at,t.created_at,t.id", (person,))):
        task = H.task(c, row["task_id"])
        if task and task["status"] in STATUSES:
            out.append((row, task))
    return out


def card(c, person):
    """The one Needs you card for the person's suggestions, or None."""
    rows = pending(c, person)
    if not rows:
        return None
    n = len(rows)
    return {"kind": "stale", "id": "stale:" + person, "title": TITLE.format(n=n, s="" if n == 1 else "s"),
            "first_line": "", "created_at": rows[0][0]["created_at"], "requester_id": None, "origin_actor": None,
            "from_person": None,
            "rows": [{"id": s["id"], "task": t["id"], "title": t["title"], "status": t["status"],
                      "reason": s["reason"], "suggest": s["suggest"], "action": s["action"],
                      "version": t.get("version")} for s, t in rows]}


def retitle(item):
    """The card's title for the rows it shows."""
    n = len(item["rows"])
    return {**item, "title": TITLE.format(n=n, s="" if n == 1 else "s")}


def _outcome(c, sid, outcome, actor):
    c.execute("UPDATE stale_suggestions SET outcome=?,outcome_at=?,outcome_by=? WHERE id=?",
              (outcome, H.now(), actor, sid))


def _accept_one(c, auth, who, row, version):
    """The person's own change for one row, with the checks a change by hand has: the task is still at the version the
    row showed, the owner marks done, the requester or an authorized person closes; anything else is refused as it is
    today."""
    task = auth.task(c, who, row["task_id"])
    if task["status"] not in STATUSES:
        _outcome(c, row["id"], "changed:" + task["status"], who.actor)
        return "already " + task["status"]
    if task["version"] != version:
        raise Problem("version_conflict", "Task changed since the card showed it; open it to see what changed", 409)
    note = f"Stale check: {row['reason']}."
    if row["action"] == "done":
        H.task_update(c, who.actor, row["task_id"], status="done", note=note)
    elif row["action"] == "close":
        H.task_close(c, who.actor, row["task_id"], note=note)
    else:
        raise Problem("invalid", "This suggestion has nothing to accept: open the task, or skip it", 422)
    c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (row["task_id"],))
    _outcome(c, row["id"], "accepted", who.actor)
    return "done" if row["action"] == "done" else "closed"


def _mine(c, who, sid):
    row = c.execute("SELECT * FROM stale_suggestions WHERE id=? AND owner=? AND outcome IS NULL",
                    (sid, who.actor)).fetchone()
    if not row:
        raise Problem("not_found", "No such suggestion waiting for you", 404)
    return dict(row)


def decide(c, auth, who, sid, decision, version=None):
    """Accept or skip one row of the person's card. Accept needs the task version the row showed."""
    row = _mine(c, who, sid)
    if decision == "skip":
        _outcome(c, sid, "skipped", who.actor)
        return {"id": sid, "outcome": "skipped"}
    if version is None:
        raise Problem("invalid", "Accept needs the task version the card showed", 422)
    return {"id": sid, "outcome": "accepted", "did": _accept_one(c, auth, who, row, version)}


def accept_all(c, auth, who, shown):
    """The rows the card showed (`shown`: {suggestion id: task version then}) that have something to accept, in one
    go, each with its own checks: a row whose task changed since, or that the person may not change by hand, is
    refused and stays on the card, and the others still go through. A row added since the card was drawn is left."""
    done, refused = [], []
    waiting = {row["id"]: row for row, _ in pending(c, who.actor)}
    for sid, version in shown.items():
        row = waiting.get(sid)
        if row is None:
            refused.append({"id": sid, "task": None, "reason": "No longer waiting for you"})
            continue
        if not row["action"]:
            continue
        c.execute("SAVEPOINT stale_accept")
        try:
            did = _accept_one(c, auth, who, row, version)
        except (H.Refused, Problem) as exc:
            c.execute("ROLLBACK TO stale_accept")
            refused.append({"id": row["id"], "task": row["task_id"],
                            "reason": getattr(exc, "detail", "") or str(exc)})
        else:
            done.append({"id": row["id"], "task": row["task_id"], "did": did})
        c.execute("RELEASE stale_accept")
    return {"accepted": done, "refused": refused}


def install(app, store, auth, mutate):
    from .models import StaleAcceptAll, StaleDecision

    @app.post("/api/v2/stale-review/accept-all")
    def stale_accept_all(request: Request, body: StaleAcceptAll):
        """Accept the suggestions the caller's Stale tickets card showed that have an action, each as their own change
        and at the task version the card showed."""
        who = request.state.identity
        auth.domain(who)
        shown = {r.id: r.version for r in body.rows}
        return mutate(request, body, lambda c: accept_all(c, auth, who, shown))

    @app.post("/api/v2/stale-review/{suggestion_id}")
    def stale_decide(request: Request, suggestion_id: str, body: StaleDecision):
        """Accept or skip one suggestion on the caller's Stale tickets card."""
        who = request.state.identity
        auth.domain(who)
        if body.decision not in ("accept", "skip"):
            raise Problem("invalid", "decision is accept or skip", 422)
        return mutate(request, body, lambda c: decide(c, auth, who, suggestion_id, body.decision, body.version))

