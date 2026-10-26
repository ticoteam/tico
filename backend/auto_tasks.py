"""Start assigned work at the runner's claim boundary, under the ordinary turn and usage fences."""
from . import hubdb as H
from . import task_privacy as privacy
from .task_snooze import snoozed

KEY = "keep_tasks_going"
PUT_BACK_MINUTES = 60
START_NOTE = 'Automatic start on a free turn'


def put_back_held(c, row):
    """An owner's queue return holds until its hour expires or new task input arrives.

    Status history survives restarts; ordinary note edits and automatic notices do not
    silently undo the owner's decision.
    """
    if not row or row['status'] != 'open':
        return False
    move = c.execute("SELECT rowid,ts,actor,old,new FROM task_events WHERE task_id=? AND actor=? "
                     "AND field IN ('status','queue_return') AND new='open' ORDER BY rowid DESC LIMIT 1",
                     (row['id'], row['owner'])).fetchone()
    if (not move or move['actor'] != row['owner'] or move['old'] not in ('doing', 'waiting', 'done', 'review')
            or move['new'] != 'open' or move['ts'] <= H.shift(H.now(), minutes=-PUT_BACK_MINUTES)):
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
    if c.execute("SELECT 1 FROM messages m WHERE m.conversation_id=? AND m.created>=? "
                 "AND json_extract(m.refs_json,'$.task')=? "
                 "AND m.from_actor<>? AND m.deleted_at IS NULL AND json_extract(m.refs_json,'$.comment')=1 "
                 "LIMIT 1", (row['conversation_id'], move['ts'], row['id'], H.KEEPER)).fetchone():
        return False
    if c.execute("SELECT 1 FROM task_events e WHERE e.task_id=? AND e.rowid>? AND e.field='blocked_by' "
                 "AND e.new IS NULL AND EXISTS (SELECT 1 FROM task_events done WHERE done.task_id=e.old "
                 "AND done.field='status' AND done.new IN ('done','closed','declined') AND done.ts>=?) LIMIT 1",
                 (row['id'], move['rowid'], move['ts'])).fetchone():
        return False
    if c.execute("SELECT 1 FROM task_relations r JOIN task_events e ON e.task_id=r.from_task "
                 "WHERE r.to_task=? AND r.kind='blocks' AND e.field='status' AND e.new IN ('done','closed','declined') "
                 "AND e.ts>=? LIMIT 1", (row['id'], move['ts'])).fetchone():
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
    if c.execute('SELECT 1 FROM turns WHERE task_id=? AND finished IS NOT NULL LIMIT 1', (row['id'],)).fetchone():
        return False
    H._set_status(c, H.KEEPER, row, status='open', note='Automatic start returned to the queue: a blocker was linked',
                  skip_step_wake=(row['owner'],))
    c.execute('UPDATE tasks SET version=version+1 WHERE id=?', (row['id'],))
    H.recount(c, [row['owner'], row['requester']])
    return True


def of(config):
    """None means unlimited (the default); zero turns automatic starts off."""
    value = config.get(KEY)
    return value if type(value) is int and 0 <= value <= 1000 else None


def startable(c, row):
    if not row or row['status'] != 'open':
        return False
    step = c.execute('SELECT name FROM task_steps WHERE id=?', (row.get('step_id'),)).fetchone()
    return row.get('type_id') == H.GENERAL_TYPE or bool(step and step[0] == 'On Deck')


def review(c, row, *, parent_id=None):
    """A delegated review child has its own session and no task cap."""
    parent = parent_id or (H.TR.parent_of(c, row['id']) if row.get('id') else None)
    parent = H.task(c, parent) if parent else None
    if not parent or parent['owner'] == row['owner']:
        return False
    step = c.execute('SELECT name FROM task_steps WHERE id=?',
                     (parent.get('blocked_from_step') if parent.get('blocked_since') else parent.get('step_id'),)).fetchone()
    return bool(step and step[0] in ('Review 1', 'Review 2', 'In QA', 'Post Release QA'))


def next_run_held(c, row):
    """A real next-run deferral holds; a task reopened from Waiting has work of its own again.

    Read the transition so already-queued Open notices recover too. A later quiet-work decision,
    stored resume condition or a run that picked up the reopened work still owns it.
    """
    if not row.get('next_run'):
        return False
    if row.get('resume_json') or row['status'] != 'open':
        return True
    move = c.execute("SELECT rowid,ts,old,new FROM task_events WHERE task_id=? AND field='status' "
                     "ORDER BY rowid DESC LIMIT 1", (row['id'],)).fetchone()
    if not move or (move['old'], move['new']) != ('waiting', 'open'):
        return True
    if c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='quiet' AND actor<>? AND rowid>? LIMIT 1",
                 (row['id'], H.KEEPER, move['rowid'])).fetchone():
        return True
    if row.get('carried_by') and H._has_table(c, 'attempts'):
        carrier = c.execute("SELECT state,created FROM attempts WHERE id=?", (row['carried_by'],)).fetchone()
        if carrier and (carrier['state'] in ('leased', 'running') or
                        carrier['state'] == 'completed' and carrier['created'] >= move['ts']):
            # Settlement clears untouched work's carrier. A completed carrier retained after this
            # reopen already worked it; only a later reopen makes that delivery obsolete.
            return True
    return False


def waiting(c, row, starting=False, *, include_next_run=True, result=False):
    """`result`: the claim is handing out a subtask's result (refs.subtask). The task's open subtasks never hold
    that back, or a parent waiting on two subtasks hears from neither until both finish; every other wait does."""
    if snoozed(row) or put_back_held(c, row) or include_next_run and next_run_held(c, row):
        return True
    if H._has_table(c, 'worktree_waits') and c.execute(
            "SELECT 1 FROM worktree_waits WHERE task_id=? AND state IN ('waiting','reserved') LIMIT 1",
            (row['id'],)).fetchone():
        return True
    from .milestone_waits import pending
    if (row.get('waiting_on') or H.live_blocker(c, row['id'], children=not result)
            or pending(c, row['id']) or c.execute(
                'SELECT 1 FROM approvals WHERE task_id=? AND decision IS NULL LIMIT 1', (row['id'],)).fetchone()):
        return True
    why = H.waiting_for(c, row, children=not result)
    # A new parent and the work someone filed beneath it may share the same bot's session.
    # Let that session coordinate its own work; another owner's child remains an awaited result.
    if starting and why == 'an open child task':
        return bool(c.execute("SELECT 1 FROM tasks WHERE id IN (" + H.TR.CHILD_IDS + ") "
                              "AND status IN ('open','doing','waiting','review','ready') AND owner<>? LIMIT 1",
                              (row['id'], row['owner'])).fetchone())
    return bool(why)


def counts(c, bot, who=None, tasks=None):
    """Only tasks readable by the viewer contribute to the displayed counts."""
    tasks = tasks if tasks is not None else H._rows(c.execute(
        "SELECT * FROM tasks WHERE owner=? AND status IN ('open','doing')", ('bot:' + bot,)))
    going = queued = 0
    for row in tasks:
        if who is not None and not privacy.task_readable(c, who, row):
            continue
        if waiting(c, row):
            continue
        if row['status'] == 'doing':
            going += 1
        elif startable(c, row):
            queued += 1
    return {'going': going, 'waiting': queued}


def refusal(c, row, config, woken=False, result=False):
    """Why this open task is not started now, or None. `woken` is a start a wake rule already decided (a claimed job,
    or the attempt it becomes): the profile's `pulls` only limits what Tico starts by itself, so it does not veto
    that, while waits, the off switch and the task cap still do. `result`: the job is a subtask's result, which a
    Waiting task's open subtasks do not hold back (`waiting`)."""
    if row and row.get("blocked_since") and (row.get("blocked_kind") == "gate"
                                              or waiting(c, row, starting=True, result=result)):
        return "task_wait"
    if not startable(c, row):
        return None
    if waiting(c, row, starting=True, include_next_run=False):
        return 'task_wait'
    if next_run_held(c, row):
        return 'task_next_run'
    from . import profiles
    if not woken and (why := profiles.pull_refusal(c, row, config)):
        return why                  # the profile does not pull open tasks by itself (backend/profiles.py)
    cap = of(config)
    if cap == 0:
        return 'auto_start_off'
    if cap is not None and not review(c, row) and counts(c, H.actor_id(row['owner']))['going'] >= cap:
        return 'task_cap'
    return None


def missing(c, settings, runner, body):
    """The highest ranked eligible task without an unfinished run. Reads only, including on idle claims."""
    from . import execution as E
    from .shared_bots import follow
    from .store import readiness_document
    ready = readiness_document(runner['readiness_json'])
    busy = {(r[0], r[1]) for r in c.execute(
        "SELECT bot,lane FROM attempts WHERE state IN ('leased','running')")}
    busy |= {tuple(v.split('|', 1)) for v in body.busy_lanes or () if '|' in v}
    held = E.turns_here(c, runner['id'])
    held_bots = set(body.busy_bots or ()) | ({b for b, _ in busy} if not E.lanes_on(runner) else set())
    bots = c.execute(f"SELECT a.bot,bc.config_json,bc.onboarding_state,{E.DRAINING_SQL} draining "
                     "FROM assignments a JOIN bots b ON b.slug=a.bot JOIN bot_config bc ON bc.bot=a.bot "
                     "WHERE a.runner_id=? AND b.state='active' AND (? IS NULL OR a.bot=?)",
                     (runner['id'], body.bot, body.bot)).fetchall()
    for bot in sorted(bots, key=lambda r: held.get(r['bot'], 0)):
        slug = bot['bot']
        if bot['onboarding_state'] in ('needs_setup', 'needs_onboarding'):
            continue
        # Most bots own no open task: asking first spares each a claim_refusal (readiness, spend, logins), which was
        # most of an idle claim's time on an install of forty bots.
        if not c.execute("SELECT 1 FROM tasks WHERE owner=? AND status='open' LIMIT 1", ('bot:' + slug,)).fetchone():
            continue
        why, config = E.claim_refusal(c, settings, runner, slug, bot['config_json'], bot['draining'], ready, held_bots)
        if why:
            continue
        config = follow(c, slug, config)
        scope = E.claim_scope(c, slug)
        for row in H._rows(c.execute("SELECT * FROM tasks WHERE owner=? AND status='open' "
                                     "ORDER BY (rank IS NULL),rank,created,id", ('bot:' + slug,))):
            if not startable(c, row) or refusal(c, row, config) or not H.task_private_readable(c, row['owner'], row):
                continue
            # A bot limited to one task (execution.scoped_sql) starts only that task or a direct subtask: a start
            # queued for any other would wait, unclaimable, until the limit is lifted.
            if scope and not E.in_scope(c, scope, row['id']):
                continue
            lane = E.job_lane(c, slug, config, {'refs': {'task': row['id']}},
                              H.conversation(c, row['conversation_id']), E.lanes_on(runner), E.roots_on(runner))
            if (slug, lane) in busy:
                continue
            # Failed/uncertain work belongs to explicit recovery, never an automatic retry.
            if c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "JOIN conversations cv ON cv.id=m.conversation_id "
                         "LEFT JOIN jobs carrier ON carrier.id=j.wake_carrier "
                         f"WHERE j.bot=? AND {H.MESSAGE_TASK_SQL}=? AND (j.state NOT IN ('completed','cancelled') "
                         f"AND {H.unheld_sql()} OR carrier.state NOT IN ('completed','cancelled') "
                         f"AND {H.unheld_sql('carrier')}) LIMIT 1",
                         (slug, row['id'])).fetchone():
                continue
            return row
    return None


def enqueue(c, row):
    """Catalog action: reuse task wake delivery; claim still checks every execution fence."""
    move = c.execute("SELECT id FROM task_events WHERE task_id=? AND field='status' ORDER BY rowid DESC LIMIT 1",
                     (row['id'],)).fetchone()
    # A fresh return to Open is new work even if the previous automatic start just finished.
    return H._wake(c, row, row['owner'], 'Start assigned task: ' + row['title'],
                   {'auto_start': True}, trigger='other', source=move['id'] if move else None)


def started(c, row):
    if not startable(c, row):
        return row
    if row.get('next_run') and not next_run_held(c, row):
        c.execute('UPDATE tasks SET next_run=0,carried_by=NULL WHERE id=?', (row['id'],))
        H._task_event(c, row['id'], H.KEEPER, 'next_run', '1', '0', 'reopened from waiting')
    steps = H.type_get(c, row['type_id'])['steps']
    step = next((s['id'] for s in steps if s['name'] == 'In Progress' and s['status'] == 'doing'), None)
    H._set_status(c, H.KEEPER, row, status='doing', step=step, note=START_NOTE)
    c.execute('UPDATE tasks SET version=version+1 WHERE id=?', (row['id'],))
    H.recount(c, [row['owner'], row['requester']])
    return H.task(c, row['id'])
