"""Thread ranking: how important each root thread is, from 1 (noise) to 10 (read now), for the Important view of the
Threads list (`GET /api/v2/threads?sort=important`, backend/threads.py `sidebar`).

Two parts. Plain rules first (`noise_sql`): a task's thread is noise once the task is finished, or while no person is
its owner or requester (bots handing each other work); noise never lists in Important, whatever a model said. Then the
decision model (`questions/thread-rank.json`, one `score` question on ten levels): on its own timer
(backend/scheduler.py, every RUN_SECONDS) a pass ranks the roots with new messages since their last ranking, newest
first, each at most once an hour, CALLS_PER_PASS a pass and CALLS_PER_HOUR an hour, and only while the server's Decide
calls today stay under DAILY_SHARE of the daily cap, so the checks that act on tasks keep their room. A thread
never ranked reads as DEFAULT; level 1 is noise too. The model sees the title, the kind, who is in it and the last few
messages cut short; every call is audited as `judge.call` with the thread id, and a failed or missing model leaves the
rank as it was (fails open). One pass at a time per process, on its own thread, never under the scheduler's lock."""

import logging
import threading
import time

from clients import judge as J
from . import decide_groups as DG
from .judge import CHAIN_SECONDS, daily_cap, primary_engine, server_engine, used_today
from .store import H

LOG = logging.getLogger("tico.thread_rank")

# The `thread_rankings` table: backend/migrations/0039_thread_rankings.sql.
QUESTION_SET = "thread-rank"
LEVELS = 10
DEFAULT = 5  # a thread no pass has ranked yet
NOISE = 1  # this level and below never list in Important
RUN_SECONDS = 300
CALLS_PER_PASS = 20  # threads a pass ranks at most
GROUP = DG.MOST  # threads asked about in one call (backend/decide_groups.py); 1 asks one thread per call
CALLS_PER_HOUR = 40
DAILY_SHARE = 0.8  # ranking stops once the server's Decide calls today reach this share of the daily cap
RERANK_SECONDS = 3600  # a thread is ranked again at most this often
WINDOW_DAYS = 14  # only threads with a message this recent are ranked
PASS_SECONDS = 4 * 60
MESSAGES, MESSAGE_CHARS, TITLE_CHARS, MEMBERS = 6, 300, 200, 8
FINISHED = ("done", "closed")
_RUNNING = threading.Lock()


def _audience(r):
    from .threads import _derived_audience

    return f"COALESCE({r}.audience,{_derived_audience(r)})"


def noise_sql(r="r"):
    """SQL true for a root that is noise by rule: a task's thread whose task is finished, or that no person owns or
    asked for."""
    finished = ",".join(f"'{s}'" for s in FINISHED)
    return (
        f"({_audience(r)}='task' AND EXISTS (SELECT 1 FROM tasks t WHERE t.id={r}.task_id AND "
        f"(t.status IN ({finished}) OR (COALESCE(t.owner_id,'') NOT LIKE 'human:%' "
        f"AND COALESCE(t.requester_id,'') NOT LIKE 'human:%'))))"
    )


def importance_sql(r="r"):
    """SQL for a root's level: 0 when noise by rule, else its ranking, else DEFAULT."""
    return (
        f"(CASE WHEN {noise_sql(r)} THEN 0 ELSE COALESCE((SELECT k.score FROM thread_rankings k "
        f"WHERE k.thread_id={r}.id),{DEFAULT}) END)"
    )


def level_of(score):
    """The 1-10 level of a score answer (a position 0..LEVELS-1, possibly fractional)."""
    return max(1, min(LEVELS, int(round(float(score))) + 1))


# ----------------------------------------------------------------------------- candidates and state (plain code)
def candidates(c, at, limit=CALLS_PER_PASS):
    """The roots to rank now, newest activity first: open, with a message in the last WINDOW_DAYS, not noise by rule,
    never ranked or with messages since their ranking, and not tried (answered or not) in the last hour."""
    since, again = H.shift(at, days=-WINDOW_DAYS), H.shift(at, seconds=-RERANK_SECONDS)
    sql = (
        f"SELECT * FROM (SELECT r.id, (SELECT MAX(s.last_seq) FROM conversations s WHERE s.root_id=r.id) AS activity, "
        f"(SELECT MAX(s.last_message_at) FROM conversations s WHERE s.root_id=r.id) AS active_at, "
        f"k.ranked_seq, k.tried_at FROM conversations r "
        f"LEFT JOIN thread_rankings k ON k.thread_id=r.id "
        f"WHERE r.parent_id IS NULL AND r.archived_at IS NULL AND r.closed_at IS NULL AND NOT {noise_sql('r')}) "
        "WHERE active_at>:since AND activity>0 AND (ranked_seq IS NULL OR activity>ranked_seq) "
        "AND (tried_at IS NULL OR tried_at<:again) "
        "ORDER BY active_at DESC, id DESC LIMIT :n"
    )
    return [dict(row) for row in c.execute(sql, {"since": since, "again": again, "n": limit})]


def _names(c, actors):
    out = []
    for a in actors:
        if H.is_human(a):
            row = c.execute("SELECT name FROM humans WHERE id=?", (a[6:],)).fetchone()
            out.append({"name": (row[0] if row else "") or "a person", "is": "person"})
        elif H.is_bot(a):
            row = c.execute("SELECT display_name FROM bots WHERE id=?", (a[4:],)).fetchone()
            out.append({"name": (row[0] if row else "") or a[4:], "is": "bot"})
    return out


def _from(actor):
    return "person" if H.is_human(actor) else "bot" if H.is_bot(actor) else "Tico"


def state_of(c, thread_id):
    """What the model reads of a root: its title, kind, who is in it and its last MESSAGES messages, oldest first."""
    from . import threads

    row = threads.thread_row(c, thread_id)
    if row is None:
        return None
    audience = threads.audience_of(row)
    kind = {
        "task": "a task's thread",
        "personal": "a direct chat",
        "participants": "a direct chat",
        "home": "a person's home thread",
        "meeting": "a meeting",
    }.get(audience, "a chat")
    members = [
        r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=? LIMIT ?", (thread_id, MEMBERS))
    ]
    messages = c.execute(
        "SELECT m.sender_id, m.kind, m.body FROM messages m JOIN conversations s ON s.id=m.conversation_id "
        "WHERE s.root_id=? AND m.deleted_at IS NULL ORDER BY m.seq DESC, m.created_at DESC LIMIT ?",
        (thread_id, MESSAGES),
    ).fetchall()
    state = {
        "title": str(row.get("subject") or "")[:TITLE_CHARS],
        "kind": kind,
        "members": _names(c, members),
        "messages": [
            {"from": _from(m["sender_id"]), "text": str(m["body"] or "").strip()[:MESSAGE_CHARS]}
            for m in reversed(messages)
        ],
    }
    if audience == "task" and row.get("task_id"):
        task = H.task(c, row["task_id"])
        if task:
            state["task"] = {
                "status": task.get("status") or "",
                "owner": _from(task.get("owner_id") or ""),
                "requester": _from(task.get("requester_id") or ""),
            }
    return {"thread": state}


def calls_this_hour(c, at, label):
    return c.execute(
        "SELECT count(*) FROM events WHERE action='judge.call' AND target=? AND created_at>?",
        (label, H.shift(at, seconds=-3600)),
    ).fetchone()[0]


def save(c, thread_id, level, seq, at):
    """Keep a thread's new level as of message `seq`; a level that moved tells the readers' lists (`ranked`)."""
    old = c.execute("SELECT score FROM thread_rankings WHERE thread_id=?", (thread_id,)).fetchone()
    c.execute(
        "INSERT INTO thread_rankings(thread_id,score,ranked_seq,tried_at,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET score=excluded.score, "
        "ranked_seq=excluded.ranked_seq, tried_at=excluded.tried_at, updated_at=excluded.updated_at",
        (thread_id, int(level), int(seq or 0), at, at, at),
    )
    if (old[0] if old else None) != int(level) and (old is not None or int(level) != DEFAULT):
        c.execute(
            "INSERT INTO changes(topic,kind,subject_id,conversation_id,created_at) VALUES('threads','ranked',?,?,?)",
            (thread_id, thread_id, H.now()),
        )


def tried(c, thread_id, at):
    """A call that did not answer: the thread keeps its level and waits the hour like any other."""
    c.execute(
        "INSERT INTO thread_rankings(thread_id,score,ranked_seq,tried_at,created_at,updated_at) "
        "VALUES(?,NULL,0,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET tried_at=excluded.tried_at, "
        "updated_at=excluded.updated_at",
        (thread_id, at, at, at),
    )


# ----------------------------------------------------------------------------- the pass
def run_pass(store, at=None, engine=None, clock=time.monotonic):
    """One pass on the caller's thread. Returns what it did. `engine` is the decision model (a test's fake); by default
    the server's."""
    at = at or H.now()
    started = clock()
    settings = store.settings
    try:
        qset = J.load_set(QUESTION_SET)
    except J.JudgeError as exc:
        LOG.warning("Thread ranking has no question set: %s", exc.detail)
        return {"ranked": 0, "why": "unavailable"}
    label = qset["label"]
    with store.read() as c:
        found = candidates(c, at)
        if found and engine is None:
            engine = server_engine(store, c, primary_engine(settings, store))
    summary = {"candidates": len(found), "ranked": 0, "errors": 0, "cost_usd": 0.0, "routes": {}}
    if not found:
        return summary
    if engine is None:
        return {**summary, "why": "unavailable"}
    questions = {"importance": qset["questions"]["importance"]}

    def gate():
        """Why the pass stops before its next call, or None."""
        with store.read() as c:
            if calls_this_hour(c, at, label) >= CALLS_PER_HOUR:
                return "hourly_cap"
            if used_today(c, H.KEEPER, at) >= DAILY_SHARE * daily_cap(settings, "bot"):
                return "daily_cap"
        if clock() - started > PASS_SECONDS - CHAIN_SECONDS:
            return "time"
        return None

    def ask(rows):
        """Several threads to a call (backend/decide_groups.py), each ranked from its own answer only."""
        pairs = [(state, questions) for _, state in rows]
        for run in DG.packs(pairs, GROUP):
            why = gate()
            if why:
                summary["why"] = why
                return False
            calls = []
            replies = DG.ask_run(engine, [pairs[n] for n in run], label, calls, run)
            with store.transaction() as c:
                for call in calls:
                    detail = DG.audit_row(call, label, types=["score"])
                    detail["thread" if len(call.items) == 1 else "threads"] = (
                        rows[call.items[0]][0]["id"] if len(call.items) == 1 else [rows[n][0]["id"] for n in call.items]
                    )
                    if call.result is not None:
                        summary["cost_usd"] = round(summary["cost_usd"] + float(call.result.get("cost_usd") or 0), 6)
                        route = route_of(call.result)
                        summary["routes"][route] = summary["routes"].get(route, 0) + 1
                    H.event(c, H.KEEPER, "judge.call", label, detail)
                for n, reply in zip(run, replies, strict=True):
                    row = rows[n][0]
                    level = None
                    if not isinstance(reply, BaseException):
                        try:
                            level = level_of(J.score(reply.get("answers") or {}, "importance")[0])
                        except Exception as exc:  # noqa: BLE001 - a fake, a provider bug: the thread keeps its rank
                            LOG.warning("Thread ranking answer unreadable: %s", type(exc).__name__)
                    elif not isinstance(reply, J.JudgeError):
                        LOG.warning("Thread ranking call failed: %s", type(reply).__name__)
                    if level is None:
                        summary["errors"] += 1
                        tried(c, row["id"], at)
                    else:
                        save(c, row["id"], level, row["activity"], at)
                        summary["ranked"] += 1
        return True

    waiting = []
    for row in found:
        with store.read() as c:
            state = state_of(c, row["id"])
        if state is None:
            continue
        waiting.append((row, state))
        if len(waiting) == GROUP:
            if not ask(waiting):
                return summary
            waiting = []
    if waiting:
        ask(waiting)
    return summary


# Which way a Decide call went, by the provider that answered it (system_routines counts calls per route).
ROUTES = {
    J.GATEWAY_PROVIDER: "gateway",
    J.TYPESAFE_PROVIDER: "own_key",
    **{name: "team_llm" for name in J.LLM_PROVIDERS.values()},
}


def route_of(result):
    return ROUTES.get((result or {}).get("provider") or "", "other")


def tick(store, at=None, engine=None):
    """From the scheduler: a pass on its own thread, one at a time. Returns the thread, or None when none started."""
    if store.settings.rehearsal or not _RUNNING.acquire(blocking=False):
        return None

    def work():
        from . import system_routines as SR

        run = SR.Run("thread_rank")
        try:
            summary = run_pass(store, at, engine=engine)
            run.did(
                f"Ranked {summary['ranked']} thread{'s' if summary['ranked'] != 1 else ''}"
                if summary.get("ranked")
                else "Nothing to rank",
                quiet=not summary.get("ranked"),
            )
            if summary.get("routes") or summary.get("cost_usd"):
                run.ai(summary.get("routes"), None, summary.get("cost_usd"))
            if summary.get("errors") and not summary.get("ranked"):
                run.error = "Failed: Decide did not answer"
        except Exception as exc:  # noqa: BLE001 - a failed pass is a run record, never a crashed thread
            LOG.warning("Thread ranking pass failed: %s", type(exc).__name__)
            run.error = SR.failure_words(exc)
        finally:
            _RUNNING.release()
        SR.write(store, run)

    thread = threading.Thread(target=work, name="thread-rank", daemon=True)
    thread.start()
    return thread
