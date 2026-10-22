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

`run_weekly` in `hub sql` (backend/sql.py) adds these up per week, bot and trigger. A run from before this
was recorded reads `unknown` for both, and is still counted.
"""

TRIGGERS = ("comment", "status_or_relation", "pr_checks", "stall_check", "schedule", "person_message",
            "background_done", "other")
OUTCOMES = ("acted", "no_change", "nothing_to_do")
UNKNOWN = "unknown"

# Events a run leaves that change nothing anyone else sees: reads, its own status line, lint and refusals.
BOOKKEEPING = frozenset({
    "status.set", "sql.query", "db.query", "conversation.open", "message.read", "message.delivered",
    "mail.read", "calendar.read", "credential.revealed", "bot.repository_read", "task.inspect",
    "refused", "message.lint", "wake.skipped", "task.lint", "path.mentioned", "judge.call", "mcp.call", "service_key.use",
})
BOOKKEEPING_PREFIXES = ("attempt.", "job.", "turn.", "runner.")
# The runner's own calls about the run (claim, events, complete) carry the run token too; they are not the run's work.
RUNNER_PATHS = ("/api/v2/attempts/", "/api/v2/jobs/")

# Task states with work still to do: H.ACTIVE_STATUSES, plus `blocked` as backend/github.py counts it.
OPEN_TASK = ("open", "doing", "waiting", "review", "ready", "blocked")


def stamp(refs, kind, source=None):
    """The wake's refs with what woke it. Every place that writes a wake for a bot calls this."""
    if kind not in TRIGGERS:
        raise ValueError(f"unknown trigger {kind!r}")
    out = {**(refs or {}), "trigger": kind}
    if source:
        out["trigger_source"] = str(source)
    return out


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
    row = conn.execute("SELECT task_id FROM turns WHERE id=? AND finished IS NULL", (turn_id,)).fetchone()
    if not pushes or not row or not row[0]:
        return 0
    paths = {r[0] for r in conn.execute("SELECT path FROM task_links WHERE task_id=? AND kind='worktree' "
                                        "AND computer_id=?", (row[0], computer))}
    count = sum(p.count for p in pushes if p.path in paths)
    if count:
        conn.execute("UPDATE turns SET writes=coalesce(writes,0)+? WHERE id=?", (count, turn_id))
    return count
