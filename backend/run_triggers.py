"""What woke each run, what it cost and whether it changed anything (token saving 1/9).

Every run (`turns`, same id as its attempt) records three things, all in plain code, none asked of a model:

- `trigger_kind`: what woke it. The place that writes a wake stamps it in the wake's refs (`trigger`, plus
  `trigger_source` where there is a source to name); the claim copies it onto the run. A message a person
  wrote is `person_message`, and a message a bot wrote (a task comment, a direct message) is `comment`: the
  message is the wake and its author says which. A keeper wake nothing stamped is `other`.
- tokens: `tokens_in` (all input), `cached_tokens` (the cached part of it, where the harness reports it),
  `input_tokens` (the rest) and `output_tokens`, as the run's usage already records them.
- `did_nothing`: 1 when the run made no write Tico counts (`writes` is 0), else 0. Exact for every write that
  reaches Tico with the run's token, and for a git push from a worktree of the run's own task, which its runner
  reports with the result (`pushes`, see `pushed`). A push from anywhere else is not seen.
- `outcome`: `acted` when the run wrote something: a hub write made with its own run token (`writes`), or a
  reply Tico posted for it (to a person, or an answer to a bot's ask), or a push its runner reported. A commit or pull request counts once Tico
  sees it (`hub task link`, a task note); one that never reaches Tico cannot. Reads and bookkeeping (`BOOKKEEPING`) are not writes.
  With no write it is `no_change` when its task is still open (it looked; nothing had moved) and
  `nothing_to_do` when there was no open task to act on (no task, or one already done, closed or declined).

- `rotated`: `tokens` or `turns` when the run started a fresh provider session because the bot's last one had
  grown past its limit (`rotate_at`, `rotate_after_turns`), reported by older runners with the result (`rotation`);
  NULL otherwise. Sessions are no longer rotated for size: auto-compaction is the only size limit.
- `session_kind`, `session_why`: whether the run resumed its thread's session (`resumed`) or started a new one
  (`new`), and why (SESSION_WHY), reported by the runner with the result (`session`); NULL from an older runner. A run
  that failed because its prompt was too large to send even after compacting reads `new` / `prompt_too_long_after_compact`:
  Tico cleared the session for the next run. A new session for a reason other than `none_saved` is also a line in
  its task's history (field `session`).
- `purpose`: a short stable label for the work (`review PR`, `answer person message`), so a week of runs can be
  grouped by what they were for. The place that writes a wake may name it (`stamp(..., purpose=)`); a run nobody
  named gets `purpose_for(bot, trigger_kind)`, the same map that fills the label on runs from before it existed. A
  pair the map does not know reads `unknown`, and the run is still counted.

What became of a run's work is written later, by whoever acts on it: when a bot's task sits at `done` or `review`
(its result) and someone other than the bot or the keeper moves it on, its run gets one `turn.outcome` event (target
the turn id, detail `{task_id, outcome, from, to}`), `outcome` one of TASK_OUTCOMES: `accepted` (closed, or `review`
moved to `done`), `edited` (its title or body rewritten) or `overridden` (sent back to work, declined or handed to
another owner). The run is the bot's latest on that task that started before the task reached its result; the first
of these is its outcome and later ones add nothing. A run that never delivered a result gets none, and runs from
before it existed keep their rows as they were.

`run_weekly` in `hub sql` (backend/sql.py) adds these up per week, bot, trigger and purpose. A run whose
trigger or purpose is not known reads `unknown` for that field, and is still counted.
"""
import re

TRIGGERS = ("comment", "status_or_relation", "pr_checks", "stall_check", "schedule", "person_message",
            "background_done", "release_published", "other")
OUTCOMES = ("acted", "no_change", "nothing_to_do")
# What a person (or another bot) did with an acted run's result: `turn_result`.
TASK_OUTCOMES = ("accepted", "edited", "overridden")
RESULT_STATES = ("done", "review")
UNKNOWN = "unknown"

# What a run is for when no caller said, by what woke it. The label is the work, not the wake, and stays short.
TRIGGER_PURPOSES = {
    "comment": "answer comment", "status_or_relation": "react to task change", "pr_checks": "handle PR checks",
    "stall_check": "check stalled task", "schedule": "run routine", "person_message": "answer person message",
    "background_done": "resume after background work", "release_published": "follow up a release",
}
# Bots whose work is one thing: a reviewer woken by a task change or a PR's checks is reviewing that PR.
REVIEWERS = ("pr-reviewer-sol", "reviewer-2")
BOT_PURPOSES = {(bot, kind): "review PR" for bot in REVIEWERS for kind in ("status_or_relation", "pr_checks")}


def purpose_for(bot, kind):
    """The default purpose of a run by `bot` woken by `kind`; `unknown` for a wake the map does not name."""
    return BOT_PURPOSES.get((str(bot), kind)) or TRIGGER_PURPOSES.get(kind) or UNKNOWN


def clean_purpose(value):
    """A caller's label as stored: trimmed and short, or None when there is none."""
    text = " ".join(str(value or "").split())[:60]
    return text or None

# Events a run leaves that change nothing anyone else sees: reads, its own status line, lint and refusals.
BOOKKEEPING = frozenset({
    "status.set", "sql.query", "db.query", "conversation.open", "message.read", "message.delivered",
    "mail.read", "calendar.read", "credential.revealed", "bot.repository_read", "task.inspect",
    "refused", "decision.outcome", "message.lint", "wake.skipped", "task.lint", "path.mentioned", "judge.call", "mcp.call", "service_key.use",
})
BOOKKEEPING_PREFIXES = ("attempt.", "job.", "turn.", "runner.")
# The runner's own calls about the run (claim, events, complete) carry the run token too; they are not the run's work.
RUNNER_PATHS = ("/api/v2/attempts/", "/api/v2/jobs/")

# Task states with work still to do: H.ACTIVE_STATUSES, plus `blocked` as backend/github.py counts it.
OPEN_TASK = ("open", "doing", "waiting", "review", "ready", "blocked")


def stamp(refs, kind, source=None, purpose=None):
    """The wake's refs with what woke it, and what the run is for where the caller knows. Every place that writes a
    wake for a bot calls this."""
    if kind not in TRIGGERS:
        raise ValueError(f"unknown trigger {kind!r}")
    out = {**(refs or {}), "trigger": kind}
    if source:
        out["trigger_source"] = str(source)
    if clean_purpose(purpose):
        out["purpose"] = clean_purpose(purpose)
    return out


def backfill(conn):
    """Give every run with no purpose the mapped default for its bot and trigger. Safe to run twice."""
    pairs = conn.execute("SELECT DISTINCT bot, trigger_kind FROM turns WHERE purpose IS NULL").fetchall()
    for bot, kind in pairs:
        conn.execute("UPDATE turns SET purpose=? WHERE purpose IS NULL AND bot IS ? AND trigger_kind IS ?",
                     (purpose_for(bot, kind), bot, kind))
    return len(pairs)


def of_message(msg):
    """(trigger, source) of the message that started a run."""
    refs = msg.get("refs") or {}
    kind = refs.get("trigger")
    if kind in TRIGGERS:
        return kind, refs.get("trigger_source")
    author = str(msg.get("from_actor") or "")
    if author.startswith("human:"):
        return "person_message", msg.get("id")
    if author.startswith("bot:"):
        return "comment", msg.get("id")
    return "other", None


def purpose_of(msg, bot, kind):
    """The purpose of the run a message starts: the caller's label on its refs, else the mapped default."""
    return clean_purpose((msg.get("refs") or {}).get("purpose")) or purpose_for(bot, kind)


def counts(action):
    """Whether an event a run leaves is a write that counts as acting."""
    return action not in BOOKKEEPING and not str(action).startswith(BOOKKEEPING_PREFIXES)


# A finished run's `outcome` and `did_nothing`, set by H.turn_finish (and by a restore that ends runs it finds open).
OUTCOME_SQL = ("CASE WHEN coalesce(writes,0)>0 THEN 'acted' "
               "WHEN (SELECT k.status FROM tasks k WHERE k.id=turns.task_id) IN ("
               + ",".join(f"'{s}'" for s in OPEN_TASK) + ") THEN 'no_change' ELSE 'nothing_to_do' END")
SETTLE_SQL = f"outcome={OUTCOME_SQL}, did_nothing=(coalesce(writes,0)=0)"


def pushed(conn, turn_id, computer, pushes):
    """Add the pushes a runner reported for a run to its `writes`: only those from a worktree of the run's own task on
    that computer, and only while the run is still open, so a result sent again is not counted twice. A runner that
    reports none (an older one, or a run that pushed nothing) leaves the run as it was."""
    row = conn.execute("SELECT task_id FROM turns WHERE id=? AND finished_at IS NULL", (turn_id,)).fetchone()
    if not pushes or not row or not row[0]:
        return 0
    paths = {r[0] for r in conn.execute("SELECT path FROM task_links WHERE task_id=? AND kind='worktree' "
                                        "AND computer_id=?", (row[0], computer))}
    count = sum(p.count for p in pushes if p.path in paths)
    if count:
        conn.execute("UPDATE turns SET writes=coalesce(writes,0)+? WHERE id=?", (count, turn_id))
    return count


def rotation(conn, turn_id, rotated):
    """Record why a run left its session for a fresh one, while the run is open. A runner that reports none (an
    older one, or a run that resumed) leaves `rotated` NULL."""
    if rotated is None:
        return 0
    return conn.execute("UPDATE turns SET rotated=? WHERE id=? AND finished_at IS NULL", (rotated.reason, turn_id)).rowcount


# Why a run started a new session, in the words a task's history uses ("Started a new session: ..."). The first six are
# the runner's (runner-rs/src/sessions.rs); the last three are Tico's own clears (views.clear_chat_session).
SESSION_WHY = {
    "none_saved": "there was no saved session to continue",
    "not_found": "the old one wasn't on this computer",
    "resume_failed": "the old one could not be continued",
    "compact_failed": "the old one could not be compacted",
    "interrupted": "the old one was interrupted before it was saved",
    "fallback_side": "the run used a fallback model, which keeps a session of its own",
    "prompt_too_long_after_compact": "the old one was still too large to send after compacting",
    "topic_change": "the conversation moved to a new topic",
    "person_clear": "a person cleared it",
}
# Reasons that make a new session a sign of trouble (Health): the old session was lost or would not continue. A first
# run (`none_saved`), a person's Clear and a topic change are the normal ways to start one.
SESSION_LOST = ("not_found", "resume_failed", "compact_failed", "interrupted", "prompt_too_long_after_compact")


def session_words(why):
    """The sentence a task's history gives a new session. An unknown reason (a newer runner's) is named as it came."""
    if why in SESSION_WHY:
        return f"Started a new session: {SESSION_WHY[why]}"
    name = re.sub(r"[^a-z0-9_ -]", "", str(why or "").lower())[:40].strip()
    return f"Started a new session ({name})" if name else "Started a new session"


def new_session(c, task_id, why, actor=None):
    """Say in the task's history that its session started anew, and why (field `session`). Nothing without a task."""
    from .store import H
    if task_id and c.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
        H._task_event(c, task_id, actor or H.KEEPER, "session", "", "new", session_words(why))


def session(conn, turn_id, session):
    """Record whether a run resumed its session or started a new one, while the run is open. A new session for any
    reason but `none_saved` is also said in its task's history. A runner that reports none leaves the columns NULL."""
    if session is None:
        return 0
    why = (session.why or "")[:60] or None
    done = conn.execute("UPDATE turns SET session_kind=?, session_why=? WHERE id=? AND finished_at IS NULL",
                        (session.kind, why, turn_id)).rowcount
    if done and session.kind == "new" and why != "none_saved":
        row = conn.execute("SELECT task_id FROM turns WHERE id=?", (turn_id,)).fetchone()
        new_session(conn, row[0] if row else None, why)
    return done


def result_outcome(before, after):
    """The outcome a status move from a bot's result gives its run: `accepted` or `overridden`, or None."""
    if before not in RESULT_STATES or after == before:
        return None
    if after == "closed" or before == "review" and after == "done":
        return "accepted"
    return "overridden"


def turn_result(conn, actor, row, outcome, event, to=None):
    """Write the one `turn.outcome` event for the run that delivered `row`'s result (the task as it was before this
    change), when `actor` is neither its owner bot nor the keeper. `event` is H.event (this module is below hubdb).
    Returns the event row, or None when there is no result, no run, or the run already has its outcome."""
    owner = str(row.get("owner") or "")
    if (outcome not in TASK_OUTCOMES or row.get("status") not in RESULT_STATES or not owner.startswith("bot:")
            or actor in (owner, "keeper")):
        return None
    reached = conn.execute("SELECT max(ts) FROM task_events WHERE task_id=? AND field='status' AND new=?",
                           (row["id"], row["status"])).fetchone()[0]
    turn = conn.execute("SELECT id FROM turns WHERE bot=? AND task_id=? AND (? IS NULL OR started_at<=?) "
                        "ORDER BY started_at DESC LIMIT 1", (owner[4:], row["id"], reached, reached)).fetchone()
    if not turn or conn.execute("SELECT 1 FROM events WHERE target=? AND action='turn.outcome' LIMIT 1",
                                (turn[0],)).fetchone():
        return None
    return event(conn, actor, "turn.outcome", turn[0], {"task_id": row["id"], "outcome": outcome,
                                                         "from": row["status"], "to": to or row["status"]})
