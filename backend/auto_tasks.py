"""Start assigned work at the runner's claim boundary, under the ordinary turn and usage fences."""
from . import hubdb as H
from . import task_privacy as privacy
from .task_snooze import snoozed

KEY = "keep_tasks_going"
PUT_BACK_MINUTES = 60
START_NOTE = 'Automatic start on a free turn'


PUT_BACK_SQL = ("SELECT rowid,created_at,actor,old,new FROM task_events WHERE task_id=? AND actor=? "
                "AND field IN ('status','queue_return') AND new='open' ORDER BY rowid DESC LIMIT 1")
STEP_SQL = 'SELECT name FROM task_steps WHERE id=?'
WORKTREE_SQL = "SELECT 1 FROM worktree_waits WHERE task_id=? AND state IN ('waiting','reserved') LIMIT 1"
APPROVAL_SQL = 'SELECT 1 FROM approvals WHERE task_id=? AND decision IS NULL LIMIT 1'      # H.wait_detail's too
OTHER_CHILD_SQL = ("SELECT 1 FROM tasks WHERE id IN (" + H.TR.CHILD_IDS + ") "
                   "AND status IN ('open','doing','waiting','review','ready') AND owner<>? LIMIT 1")


def put_back_held(c, row):
    """An owner's queue return holds until its hour expires or new task input arrives.

    Status history survives restarts; ordinary note edits and automatic notices do not
    silently undo the owner's decision.
    """
    if not row or row['status'] != 'open':
        return False
    from .read_cache import one
    move = one(c, PUT_BACK_SQL, (row['id'], row['owner_id']))
    if (not move or move['actor'] != row['owner_id'] or move['old'] not in ('doing', 'waiting', 'done', 'review')
            or move['new'] != 'open' or move['created_at'] <= H.shift(H.now(), minutes=-PUT_BACK_MINUTES)):
        return False
    # Derived Blocked and its keeper resume preserve the owner's hold. A later working move ends it.
    if c.execute("SELECT 1 FROM task_events WHERE task_id=? AND rowid>? AND field='status' "
                 "AND (actor<>? OR new NOT IN ('waiting','open')) LIMIT 1",
                 (row['id'], move['rowid'], H.KEEPER)).fetchone():
        return False
    if c.execute("SELECT 1 FROM task_events WHERE task_id=? AND rowid>? AND new IS NOT NULL "
                 "AND field IN ('link','related','blocked_by','blocks','parent_id','subtask','follow_up_of','follow_ups') "
                 "LIMIT 1", (row['id'], move['rowid'])).fetchone():
        return False
    if c.execute("SELECT 1 FROM messages m WHERE m.conversation_id=? AND m.created_at>=? "
                 "AND json_extract(m.refs,'$.task')=? "
                 "AND m.sender_id<>? AND m.deleted_at IS NULL AND json_extract(m.refs,'$.comment')=1 "
                 "LIMIT 1", (row['conversation_id'], move['created_at'], row['id'], H.KEEPER)).fetchone():
        return False
    if c.execute("SELECT 1 FROM task_events e WHERE e.task_id=? AND e.rowid>? AND e.field='blocked_by' "
                 "AND e.new IS NULL AND EXISTS (SELECT 1 FROM task_events done WHERE done.task_id=e.old "
                 "AND done.field='status' AND done.new IN ('done','closed','declined') AND done.created_at>=?) LIMIT 1",
                 (row['id'], move['rowid'], move['created_at'])).fetchone():
        return False
    if c.execute("SELECT 1 FROM task_relations r JOIN task_events e ON e.task_id=r.from_task "
                 "WHERE r.to_task=? AND r.kind='blocks' AND e.field='status' AND e.new IN ('done','closed','declined') "
                 "AND e.created_at>=? LIMIT 1", (row['id'], move['created_at'])).fetchone():
        return False
    return True


def back_off(c, row):
    """Undo only an automatic start that has not produced a finished task run."""
    if row['status'] != 'doing' or not H.TR.live_blocker(c, row['id'], H.ACTIVE_STATUSES):
        return False
    move = c.execute("SELECT rowid,actor,new,note FROM task_events WHERE task_id=? AND field='status' "
                     "ORDER BY rowid DESC LIMIT 1", (row['id'],)).fetchone()
    if not move or (move['actor'], move['new'], move['note']) != (H.KEEPER, 'doing', START_NOTE):
        return False
    if c.execute("SELECT 1 FROM task_events WHERE task_id=? AND rowid>? AND field IN ('step','owner') "
                 "AND actor<>? LIMIT 1", (row['id'], move['rowid'], H.KEEPER)).fetchone():
        return False
    if c.execute('SELECT 1 FROM turns WHERE task_id=? AND finished_at IS NOT NULL LIMIT 1', (row['id'],)).fetchone():
        return False
    H._set_status(c, H.KEEPER, row, status='open', note='Automatic start returned to the queue: a blocker was linked',
                  skip_step_wake=(row['owner_id'],))
    c.execute('UPDATE tasks SET version=version+1 WHERE id=?', (row['id'],))
    H.recount(c, [row['owner_id'], row['requester_id']])
    return True


def of(config):
    """None means unlimited (the default); zero turns automatic starts off."""
    value = config.get(KEY)
    return value if type(value) is int and 0 <= value <= 1000 else None


def startable(c, row, steps=None):
    """`steps`: {step id: name} read for a whole list of tasks (`step_names`); without it, one read of the row's step."""
    if not row or row['status'] != 'open':
        return False
    if steps is None:
        from .read_cache import one
        step = one(c, STEP_SQL, (row.get('step_id'),))
        name = step[0] if step else None
    else:
        name = steps.get(row.get('step_id'))
    return row.get('type_id') == H.GENERAL_TYPE or name == 'On Deck'


def step_names(c, rows):
    """{step id: name} for the steps of these tasks, read in one query per 500 steps."""
    ids = sorted({r['step_id'] for r in rows if r and r.get('step_id')})
    names = {}
    for part in H.chunks(ids):
        names.update({sid: name for sid, name in c.execute(
            f"SELECT id,name FROM task_steps WHERE id IN ({_marks(part)})", part)})
    return names


def prime_checks(c, rows):
    """Seed the one-row reads `put_back_held` and `startable` make for each of these tasks (read_cache.prime), one
    query a table for all of them: Health checks every queued job of every bot whose work is old. A batch row carries
    its task after the columns the one-row read selects; those are read by name and position only."""
    from .read_cache import cache, prime
    rows = [r for r in rows if r]
    if cache(c) is None or not rows:
        return
    opened = {r['id']: r['owner_id'] for r in rows if r['status'] == 'open' and r['owner_id']}
    moves = {}
    for part in H.chunks(sorted(opened)):
        for move in c.execute("SELECT rowid,created_at,actor,old,new,task_id FROM task_events WHERE task_id IN (%s) "
                              "AND field IN ('status','queue_return') AND new='open' ORDER BY rowid DESC"
                              % _marks(part), part):
            if move['actor'] == opened[move['task_id']]:
                moves.setdefault(move['task_id'], move)
    prime(c, PUT_BACK_SQL, {(tid, owner): moves.get(tid) for tid, owner in opened.items()})
    steps = sorted({r.get('step_id') for r in rows if r['status'] == 'open'}, key=str)
    found = {}
    for part in H.chunks([s for s in steps if s is not None]):
        found.update((row['id'], row) for row in c.execute(
            f"SELECT name,id FROM task_steps WHERE id IN ({_marks(part)})", part))
    prime(c, STEP_SQL, {(sid,): found.get(sid) for sid in steps})


def prime_waits(c, rows):
    """Seed the first reads `waiting` and H.wait_detail make for each of these tasks, one query a table for all of
    them (read_cache): `maybe_waiting`'s suspects, which Health asks `waiting` about for every queued job. A read is
    seeded where the batch gives exactly the one-task answer: an empty one, or a single row (a tie on the one-task
    read's order is left to that read). A batch row carries its key after the columns the one-task read selects;
    those are read by name and position only."""
    from .milestone_waits import PENDING_SQL, _present as milestones_present
    from .read_cache import cache, prime, prime_rows
    from .task_relations import live_blocker_sql
    rows = [r for r in rows if r]
    if cache(c) is None or not rows:
        return
    ids = sorted({r['id'] for r in rows})

    def batch(sql, args=()):
        for part in H.chunks(ids):
            yield from c.execute(sql.replace('{m}', _marks(part)), (*part, *args))

    def absent(sql, select):
        have = {row[0] for row in batch(select)}
        prime(c, sql, {(tid,): None for tid in ids if tid not in have})
    if H._has_table(c, 'worktree_waits'):
        absent(WORKTREE_SQL, "SELECT DISTINCT task_id FROM worktree_waits WHERE task_id IN ({m}) "
                             "AND state IN ('waiting','reserved')")
    if milestones_present(c):
        absent(PENDING_SQL, "SELECT DISTINCT task_id FROM task_milestone_waits WHERE task_id IN ({m}) AND woke_at IS NULL")
    absent(APPROVAL_SQL, "SELECT DISTINCT task_id FROM approvals WHERE task_id IN ({m}) AND decision IS NULL")
    first = {}
    for row in batch("SELECT t.id,r.to_task FROM task_relations r JOIN tasks t ON t.id=r.from_task "
                     "WHERE r.to_task IN ({m}) AND r.kind='blocks' AND t.status IN (" + _marks(H.ACTIVE_STATUSES) + ") "
                     "ORDER BY r.created_at, r.rowid", H.ACTIVE_STATUSES):
        first.setdefault(row['to_task'], row)
    prime(c, live_blocker_sql(len(H.ACTIVE_STATUSES)),
          {(tid, *H.ACTIVE_STATUSES): first.get(tid) for tid in ids})
    # Open subtasks (wait_detail), and whether one has another owner (`waiting` when starting).
    kids, other = {tid: [] for tid in ids}, set()
    owner_of = {r['id']: r['owner'] for r in rows}
    for row in batch("SELECT t.id,t.owner_id,t.title,t.status,r.to_task FROM task_relations r JOIN tasks t ON t.id=r.from_task "
                     "WHERE r.kind='parent' AND r.to_task IN ({m}) ORDER BY t.created_at,t.id"):
        if row['status'] is not None and row['status'] not in ('done', 'closed', 'declined'):
            kids[row['to_task']].append(row)
        if (row['status'] in H.ACTIVE_STATUSES and None not in (row['owner'], owner_of[row['to_task']])
                and row['owner'] != owner_of[row['to_task']]):
            other.add(row['to_task'])
    prime_rows(c, H.OPEN_CHILDREN_SQL, {(tid,): found for tid, found in kids.items()})
    prime(c, OTHER_CHILD_SQL, {(tid, owner_of[tid]): (1,) if tid in other else None for tid in ids})
    # The owner's unanswered questions about each task (wait_detail), and what answered them (answers_to).
    asks = {(r['owner'], r['id']): [] for r in rows}
    for part in H.chunks(sorted({owner for owner, _ in asks})):
        for row in c.execute(
                f"SELECT m.id,m.recipient_id,m.sender_id,{H.MESSAGE_TASK_SQL} task FROM messages m "
                f"JOIN conversations cv ON cv.id=m.conversation_id WHERE m.sender_id IN ({_marks(part)}) "
                "AND m.kind='ask' AND m.superseded_at IS NULL", part):
            if (row['sender_id'], row['task']) in asks:
                asks[(row['sender_id'], row['task'])].append(row)
    prime_rows(c, H.WAIT_ASKS_SQL, {pair: found for pair, found in asks.items() if len(found) <= 1})
    asked = sorted({found[0]['id'] for found in asks.values() if len(found) == 1})
    answers = {}
    for part in H.chunks(asked):
        for row in c.execute(f"SELECT * FROM messages WHERE in_reply_to IN ({_marks(part)}) AND kind='answer' "
                             "AND deleted_at IS NULL", part):
            answers.setdefault(row['in_reply_to'], []).append(row)
    prime(c, H.ANSWER_SQL, {(mid,): answers[mid][0] if mid in answers else None
                            for mid in asked if len(answers.get(mid, ())) <= 1})
    for part in H.chunks(asked):
        prime(c, H.ANSWERED_BY_SQL, {(row['id'],): row for row in c.execute(
            f"SELECT answered_by_id,id FROM messages WHERE id IN ({_marks(part)})", part)})


def _marks(values):
    return ",".join("?" * len(values))


def maybe_waiting(c, rows):
    """The ids of the tasks `waiting` might hold: a superset, found with one read per table for all the rows at once.
    A task outside it has no wait that `waiting` would find, so a caller need not ask it. Each test below is the one
    `waiting` and `H.wait_detail` make, only read for every task together; a row that any of them could match is kept
    in, and `waiting` decides for it."""
    rows = [r for r in rows if r]
    suspect = {r['id'] for r in rows if snoozed(r) or r.get('next_run') or r.get('waiting_on')
               or r.get('blocked_kind') == 'manual'}
    ids = [r['id'] for r in rows if r['id'] not in suspect]
    open_ids = [r['id'] for r in rows if r['id'] not in suspect and r['status'] == 'open']
    cutoff = H.shift(H.now(), minutes=-PUT_BACK_MINUTES)

    def found(sql, values, args=()):
        for part in H.chunks(values):
            suspect.update(r[0] for r in c.execute(sql.replace('{m}', _marks(part)), (*part, *args)))

    # put_back_held: a queue return in the hour, for a task still open
    found("SELECT DISTINCT e.task_id FROM task_events e JOIN tasks t ON t.id=e.task_id AND t.owner_id=e.actor "
          "WHERE e.task_id IN ({m}) AND e.field IN ('status','queue_return') AND e.new='open' "
          "AND e.old IN ('doing','waiting','done','review') AND e.created_at>?", open_ids, (cutoff,))
    if H._has_table(c, 'worktree_waits'):
        found("SELECT DISTINCT task_id FROM worktree_waits WHERE task_id IN ({m}) AND state IN ('waiting','reserved')", ids)
    # live_blocker, and the blocker in wait_detail
    found("SELECT DISTINCT r.to_task FROM task_relations r JOIN tasks t ON t.id=r.from_task "
          "WHERE r.to_task IN ({m}) AND r.kind='blocks' AND t.status IN (" + _marks(H.ACTIVE_STATUSES) + ")",
          ids, tuple(H.ACTIVE_STATUSES))
    from .milestone_waits import _present as milestones_present
    if milestones_present(c):
        found("SELECT DISTINCT task_id FROM task_milestone_waits WHERE task_id IN ({m}) AND woke_at IS NULL", ids)
    found("SELECT DISTINCT task_id FROM approvals WHERE task_id IN ({m}) AND decision IS NULL", ids)
    # an open child task (a review child is checked on its own, so it is kept in)
    found("SELECT DISTINCT r.to_task FROM task_relations r JOIN tasks t ON t.id=r.from_task "
          "WHERE r.kind='parent' AND r.to_task IN ({m}) AND t.status NOT IN ('done','closed','declined')", ids)
    found("SELECT DISTINCT task_id FROM task_links WHERE task_id IN ({m}) AND kind='pr' "
          "AND state IN ('open','draft','merged') AND json_extract(detail,'$.wake_once') IS NOT NULL", ids)
    # an unanswered question the owner asked about the task: owner and task are matched in Python
    pairs = {(r['owner'], r['id']) for r in rows if r['id'] not in suspect}
    for part in H.chunks(sorted({actor for actor, _ in pairs})):
        for actor, tid in c.execute(
                f"SELECT m.sender_id,{H.MESSAGE_TASK_SQL} FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
                f"WHERE m.sender_id IN ({_marks(part)}) AND m.kind='ask' AND m.superseded_at IS NULL", part).fetchall():
            if (actor, tid) in pairs:
                suspect.add(tid)
    return suspect


def review(c, row, *, parent_id=None):
    """A delegated review child has its own session and no task cap."""
    parent = parent_id or (H.TR.parent_of(c, row['id']) if row.get('id') else None)
    parent = H.task(c, parent) if parent else None
    if not parent or parent['owner_id'] == row['owner_id']:
        return False
    from . import step_flow
    step = step_flow.step(c, parent.get('blocked_from_step') if parent.get('blocked_since') else parent.get('step_id'))
    return bool(step and step_flow.check_kind(step))


def actionable_notice(row, message):
    """Only owner-directed work notices release quiet carriage; informational news stays quiet."""
    refs = (message or {}).get('refs') or {}
    return bool(message and message.get('sender_id') == H.KEEPER and message.get('kind') == 'notice'
                and message.get('recipient_id') == row['owner_id'] and not refs.get('quiet')
                and (refs.get('wake') in ('due', 'snooze', 'resume')
                     or refs.get('trigger') == 'pr_checks' and refs.get('wake') == 'pr_checks_done'))


def carrier_held(c, row):
    """A carried task cannot be released around its live, queued or interrupted run."""
    if not row.get('carried_by'):
        return False
    carrier = c.execute("SELECT a.state,j.state job_state FROM attempts a LEFT JOIN jobs j ON j.id=a.job_id "
                        "WHERE a.id=?", (row['carried_by'],)).fetchone()
    return bool(carrier and (carrier['state'] in ('leased', 'running') or
                            carrier['job_state'] in ('queued', 'leased', 'running', 'uncertain')))


def notice_releases(c, row, message):
    """An actionable notice starts its own work only after the previous carrier has let go."""
    if not row.get('next_run') or row['status'] != 'open' or row.get('resume'):
        return False
    if not actionable_notice(row, message):
        return False
    if carrier_held(c, row):
        return False
    # A task may have moved lanes or have a run of its own without a carried_by marker.
    if c.execute("SELECT 1 FROM attempts a JOIN turns t ON t.id=a.id LEFT JOIN jobs j ON j.id=a.job_id "
                 "WHERE a.bot_id=? AND t.task_id=? AND (a.state IN ('leased','running') OR j.state='uncertain') "
                 "LIMIT 1", (H.actor_id(row['owner_id']), row['id'])).fetchone():
        return False
    return True


def next_run_held(c, row, *, message=None):
    """A real next-run deferral holds; a task reopened from Waiting has work of its own again.

    Read the transition so already-queued Open notices recover too. A later quiet-work decision,
    stored resume condition or a run that picked up the reopened work still owns it.
    """
    if not row.get('next_run'):
        return False
    if row.get('resume') or row['status'] != 'open' or carrier_held(c, row):
        return True
    if actionable_notice(row, message):
        return not notice_releases(c, row, message)
    move = c.execute("SELECT rowid,created_at,old,new FROM task_events WHERE task_id=? AND field='status' "
                     "ORDER BY rowid DESC LIMIT 1", (row['id'],)).fetchone()
    if not move or (move['old'], move['new']) != ('waiting', 'open'):
        return True
    if c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='quiet' AND actor<>? AND rowid>? LIMIT 1",
                 (row['id'], H.KEEPER, move['rowid'])).fetchone():
        return True
    if row.get('carried_by') and H._has_table(c, 'attempts'):
        carrier = c.execute("SELECT state,created_at FROM attempts WHERE id=?", (row['carried_by'],)).fetchone()
        if carrier and (carrier['state'] in ('leased', 'running') or
                        carrier['state'] == 'completed' and carrier['created_at'] >= move['created_at']):
            # Settlement clears untouched work's carrier. A completed carrier retained after this
            # reopen already worked it; only a later reopen makes that delivery obsolete.
            return True
    return False


def waiting(c, row, starting=False, *, include_next_run=True, result=False):
    """`result`: the claim is handing out a subtask's result (refs.subtask). The task's open subtasks never hold
    that back, or a parent waiting on two subtasks hears from neither until both finish; every other wait does."""
    if snoozed(row) or put_back_held(c, row) or include_next_run and next_run_held(c, row):
        return True
    from .read_cache import one
    if H._has_table(c, 'worktree_waits') and one(c, WORKTREE_SQL, (row['id'],)):
        return True
    from .milestone_waits import pending
    if (row.get('waiting_on') or H.live_blocker(c, row['id'], children=not result)
            or pending(c, row['id']) or one(c, APPROVAL_SQL, (row['id'],))):
        return True
    why = H.waiting_for(c, row, children=not result)
    # A new parent and the work someone filed beneath it may share the same bot's session.
    # Let that session coordinate its own work; another owner's child remains an awaited result.
    if starting and why == 'an open child task':
        return bool(one(c, OTHER_CHILD_SQL, (row['id'], row['owner'])))
    return bool(why)


def counts(c, bot, who=None, tasks=None):
    """Only tasks readable by the viewer contribute to the displayed counts."""
    return counts_many(c, {bot: tasks}, who)[bot]


def counts_many(c, tasks_by_bot, who=None):
    """`counts` for many bots at once: {bot: {'going', 'waiting'}} from `tasks_by_bot` ({bot: its open tasks}, or None to
    read them). The wait checks and the step names are read once for all their tasks, not once a bot."""
    for bot, tasks in tasks_by_bot.items():
        if tasks is None:
            tasks_by_bot[bot] = H._rows(c.execute(
                "SELECT * FROM tasks WHERE owner_id=? AND status IN ('open','doing')", ('bot:' + bot,)))
    every = [row for tasks in tasks_by_bot.values() for row in tasks]
    suspect = maybe_waiting(c, every)
    steps = step_names(c, every)
    out = {}
    for bot, tasks in tasks_by_bot.items():
        going = queued = 0
        for row in tasks:
            if who is not None and not privacy.task_readable(c, who, row):
                continue
            if row['id'] in suspect and waiting(c, row):
                continue
            if row['status'] == 'doing':
                going += 1
            elif startable(c, row, steps):
                queued += 1
        out[bot] = {'going': going, 'waiting': queued}
    return out


def refusal(c, row, config, woken=False, result=False, *, message=None, suspect=None):
    """Why this open task is not started now, or None. `woken` is a start a wake rule already decided (a claimed job,
    or the attempt it becomes): the profile's `pulls` only limits what Tico starts by itself, so it does not veto
    that, while waits, the off switch and the task cap still do. `result`: the job is a subtask's result, which a
    Waiting task's open subtasks do not hold back (`waiting`). `suspect`: `maybe_waiting` over the caller's tasks; a
    task outside it is not asked `waiting` again."""
    if row and row.get("blocked_since") and (row.get("blocked_kind") == "gate"
                                              or waiting(c, row, starting=True, result=result)):
        return "task_wait"
    if not startable(c, row):
        return None
    if (suspect is None or row['id'] in suspect) and waiting(c, row, starting=True, include_next_run=False):
        return 'task_wait'
    if next_run_held(c, row, message=message):
        return 'task_next_run'
    from . import profiles
    if not woken and (why := profiles.pull_refusal(c, row, config)):
        return why                  # the profile does not pull open tasks by itself (backend/profiles.py)
    cap = of(config)
    if cap == 0:
        return 'auto_start_off'
    if cap is not None and not review(c, row) and counts(c, H.actor_id(row['owner_id']))['going'] >= cap:
        return 'task_cap'
    return None


def missing(c, settings, runner, body, failed=None, waiting=None):
    """The highest ranked eligible task without an unfinished run. Reads only, including on idle claims. A bot whose
    claim check raised is passed over as the claim passes it over: `failed` (the claim's ClaimFaults) records it, and
    each bot whose checks all pass, and `waiting` says its check is still backing off. A task whose own checks raise
    is passed over and logged; the bot's other tasks are still looked at."""
    from . import execution as E
    from .batch_work import report, shared_check, shared_scope, storage_error
    from .shared_bots import follow
    from .store import readiness_document
    ready = readiness_document(runner['readiness'])
    busy = set(E.live_lanes(c))
    busy |= {((H.resolve_bot(c, b) or {}).get("id", b), lane) for b, lane in
             (v.split('|', 1) for v in body.busy_lanes or () if '|' in v)}
    held = E.turns_here(c, runner['id'])
    held_bots = {(H.resolve_bot(c, b) or {}).get("id", b) for b in body.busy_bots or ()} | (
        {b for b, _ in busy} if not E.lanes_on(runner) else set())
    want = (H.resolve_bot(c, body.bot) or {}).get("id", body.bot) if body.bot is not None else None
    bots = c.execute(f"SELECT a.bot,bc.config,bc.onboarding_state,{E.DRAINING_SQL} draining "
                     "FROM assignments a JOIN bots b ON b.id=a.bot JOIN bot_config bc ON bc.bot=a.bot "
                     "WHERE a.runner_id=? AND b.state='active' AND (? IS NULL OR a.bot=?)",
                     (runner['id'], want, want)).fetchall()
    for bot in sorted(bots, key=lambda r: held.get(r['bot'], 0)):
        slug = bot['bot']
        if bot['onboarding_state'] in ('needs_setup', 'needs_onboarding'):
            continue
        # Most bots own no open task: asking first spares each a claim_refusal (readiness, spend, logins), which was
        # most of an idle claim's time on an install of forty bots.
        if not c.execute("SELECT 1 FROM tasks WHERE owner_id=? AND status='open' LIMIT 1", ('bot:' + slug,)).fetchone():
            continue
        if (failed is not None and slug in failed.bots) or (waiting is not None and waiting(slug)):
            continue
        try:
            with shared_check():
                why, config = E.claim_refusal(c, settings, runner, slug, bot['config'], bot['draining'], ready,
                                              held_bots)
                if not why:
                    config = follow(c, slug, config)
                    scope = E.claim_scope(c, slug)
        except Exception as exc:
            # One bot's raising check must not stop the other bots' open tasks from starting.
            if storage_error(exc) or shared_scope(exc) == "team":
                raise
            if failed is not None:
                failed.bot(slug, exc)
            else:
                report("auto task check", slug, exc)
            continue
        if why:
            # A refusal is not a clean check: draining or a busy bot is refused before its checks run.
            continue
        if failed is not None:
            failed.clean.add(slug)
        for row in H._rows(c.execute("SELECT * FROM tasks WHERE owner_id=? AND status='open' "
                                     "ORDER BY (rank IS NULL),rank,created_at,id", ('bot:' + slug,))):
            try:
                if found := _startable_task(c, row, slug, config, scope, runner, busy):
                    return found
            except Exception as exc:
                # One task whose checks raise is that task's alone: no run exists to hold, so it is logged and passed
                # over, and the other tasks still start.
                if storage_error(exc) or shared_scope(exc) == "team":
                    raise
                report("auto task", row['id'], exc)
    return None


def _startable_task(c, row, slug, config, scope, runner, busy):
    """`row` when the backfill may start it now, else None."""
    from . import claim_holds
    from . import execution as E
    if not startable(c, row) or refusal(c, row, config) or not H.task_private_readable(c, row['owner_id'], row):
        return None
    # A bot limited to one task (execution.scoped_sql) starts only that task or a direct subtask: a start queued for
    # any other would wait, unclaimable, until the limit is lifted.
    if scope and not E.in_scope(c, scope, row['id']):
        return None
    lane = E.job_lane(c, slug, config, {'refs': {'task': row['id']}},
                      H.conversation(c, row['conversation_id']), E.lanes_on(runner), E.roots_on(runner))
    if (slug, lane) in busy:
        return None
    # Failed/uncertain work belongs to explicit recovery, never an automatic retry. A job its claim holds is still the
    # task's run, waiting for its bot's release: no second one is filed beside it.
    if c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
                 "JOIN conversations cv ON cv.id=m.conversation_id "
                 "LEFT JOIN jobs carrier ON carrier.id=j.wake_carrier "
                 f"WHERE j.bot_id=? AND {H.MESSAGE_TASK_SQL}=? AND (j.state NOT IN ('completed','canceled') "
                 f"AND ({H.unheld_sql()} OR j.hold_reason=?) OR carrier.state NOT IN ('completed','canceled') "
                 f"AND {H.unheld_sql('carrier')}) LIMIT 1",
                 (slug, row['id'], claim_holds.REASON)).fetchone():
        return None
    return row


def enqueue(c, row):
    """Catalog action: reuse task wake delivery; claim still checks every execution fence."""
    move = c.execute("SELECT id FROM task_events WHERE task_id=? AND field='status' ORDER BY rowid DESC LIMIT 1",
                     (row['id'],)).fetchone()
    # A fresh return to Open is new work even if the previous automatic start just finished.
    return H._wake(c, row, row['owner_id'], 'Start assigned task: ' + row['title'],
                   {'auto_start': True}, trigger='other', source=move['id'] if move else None)


def started(c, row, *, message=None):
    if not startable(c, row):
        return row
    if row.get('next_run') and not next_run_held(c, row, message=message):
        c.execute('UPDATE tasks SET next_run=0,carried_by=NULL WHERE id=?', (row['id'],))
        H._task_event(c, row['id'], H.KEEPER, 'next_run', '1', '0',
                      'actionable notice released stale carriage' if notice_releases(c, row, message)
                      else 'reopened from waiting')
    steps = H.type_get(c, row['type_id'])['steps']
    step = next((s['id'] for s in steps if s['name'] == 'In Progress' and s['status'] == 'doing'), None)
    H._set_status(c, H.KEEPER, row, status='doing', step=step, note=START_NOTE)
    c.execute('UPDATE tasks SET version=version+1 WHERE id=?', (row['id'],))
    H.recount(c, [row['owner_id'], row['requester_id']])
    return H.task(c, row['id'])
