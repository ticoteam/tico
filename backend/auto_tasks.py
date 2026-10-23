"""Start assigned work at the runner's claim boundary, under the ordinary turn and usage fences."""
from . import hubdb as H
from . import task_privacy as privacy
from .task_snooze import snoozed

KEY = "keep_tasks_going"


def of(config):
    """None means unlimited (the default); zero turns automatic starts off."""
    value = config.get(KEY)
    return value if type(value) is int and 0 <= value <= 1000 else None


def startable(c, row):
    if not row or row['status'] != 'open':
        return False
    step = c.execute('SELECT name FROM task_steps WHERE id=?', (row.get('step_id'),)).fetchone()
    return row.get('type_id') == H.GENERAL_TYPE or bool(step and step[0] == 'On Deck')


def review(c, row):
    """A delegated review child has its own session and no task cap."""
    parent = H.TR.parent_of(c, row['id'])
    parent = H.task(c, parent) if parent else None
    if not parent or parent['owner'] == row['owner']:
        return False
    step = c.execute('SELECT name FROM task_steps WHERE id=?', (parent.get('step_id'),)).fetchone()
    return bool(step and step[0] in ('Review 1', 'Review 2', 'In QA'))


def waiting(c, row, starting=False):
    if snoozed(row) or row.get('next_run'):
        return True
    if H._has_table(c, 'worktree_waits') and c.execute(
            "SELECT 1 FROM worktree_waits WHERE task_id=? AND state IN ('waiting','reserved') LIMIT 1",
            (row['id'],)).fetchone():
        return True
    from .milestone_waits import pending
    if (row.get('waiting_on') or H.TR.live_blocker(c, row['id'], H.ACTIVE_STATUSES)
            or pending(c, row['id']) or c.execute(
                'SELECT 1 FROM approvals WHERE task_id=? AND decision IS NULL LIMIT 1', (row['id'],)).fetchone()):
        return True
    why = H.waiting_for(c, row)
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


def refusal(c, row, config):
    if not startable(c, row):
        return None
    if waiting(c, row, starting=True):
        return 'task_wait'
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
    for bot in {b for b, _ in busy}:
        held[bot] = max(held.get(bot, 0), sum(b == bot for b, _ in busy))
    share = E.turn_share(c, runner['id'], runner['capacity'])
    held_bots = set(body.busy_bots or ()) | ({b for b, _ in busy} if not E.lanes_on(runner) else set())
    bots = c.execute(f"SELECT a.bot,bc.config_json,bc.onboarding_state,{E.DRAINING_SQL} draining "
                     "FROM assignments a JOIN bots b ON b.slug=a.bot JOIN bot_config bc ON bc.bot=a.bot "
                     "WHERE a.runner_id=? AND b.state='active' AND (? IS NULL OR a.bot=?)",
                     (runner['id'], body.bot, body.bot)).fetchall()
    for bot in sorted(bots, key=lambda r: held.get(r['bot'], 0)):
        slug = bot['bot']
        if bot['onboarding_state'] in ('needs_setup', 'needs_onboarding') or held.get(slug, 0) >= share:
            continue
        why, config = E.claim_refusal(c, settings, runner, slug, bot['config_json'], bot['draining'], ready, held_bots)
        if why:
            continue
        config = follow(c, slug, config)
        for row in H._rows(c.execute("SELECT * FROM tasks WHERE owner=? AND status='open' "
                                     "ORDER BY (rank IS NULL),rank,created,id", ('bot:' + slug,))):
            if not startable(c, row) or refusal(c, row, config) or not H.task_private_readable(c, row['owner'], row):
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
                         "OR carrier.state NOT IN ('completed','cancelled')) LIMIT 1",
                         (slug, row['id'])).fetchone():
                continue
            return row
    return None


def enqueue(c, row):
    """Catalog action: reuse task wake delivery; claim still checks every execution fence."""
    return H._wake(c, row, row['owner'], 'Start assigned task: ' + row['title'],
                   {'auto_start': True}, trigger='other')


def started(c, row):
    if not startable(c, row):
        return row
    steps = H.type_get(c, row['type_id'])['steps']
    step = next((s['id'] for s in steps if s['name'] == 'In Progress' and s['status'] == 'doing'), None)
    H._set_status(c, H.KEEPER, row, status='doing', step=step, note='Automatic start on a free turn')
    c.execute('UPDATE tasks SET version=version+1 WHERE id=?', (row['id'],))
    H.recount(c, [row['owner'], row['requester']])
    return H.task(c, row['id'])
