"""The turn fixer: a built-in system routine over every computer, plain code with no model and no cost.

Each scheduler pass it frees only starts that are proven dead, keeps one Health finding per company-turns incident
(backend/org_turns.py), and hands what it cannot fix to BotOps.

Proven dead is one of two things. A person stopped a turn that had not started: a stopped start can never be
granted a place again (start_places.reserve), so its place is freed now rather than when its lease runs out. Or the
turn's lease is lost and its process is gone: its computer has been revoked, or has claimed lately and its busy list
no longer names the bot. A live lease is a slow reply or a healthy wait, and a computer that has not claimed lately
proves nothing, so neither is touched. The task and its history stay; the task gets one quiet note.

A dead turn that may have pushed, sent or paid is never retried blindly. A turn that only read on a host that reports
every call runs again through auto_reconcile; any other is held for BotOps at once with the tools it used.

While an incident is open its finding names the counts by wait reason, the oldest waits, the last progress of each
running turn, each computer's CPU and memory and the likely bottleneck in one sentence. Later passes rewrite the same
finding; when the incident clears Health says it resolved and when. An incident still open after FIX_WINDOW_S gets
exactly one task for BotOps (the Support bot where there is no BotOps) with the finding. The owner gets one notice
per incident, only when it is still open after NOTICE_AFTER_S or its main wait needs a person (a login, quota or
money)."""

from . import org_turns, start_places
from . import task_privacy as privacy
from .store import H

FIX_WINDOW_S = 300          # an incident the fixer has had this long to clear is handed to BotOps
NOTICE_AFTER_S = 3600       # an unfixed incident reaches the owner after this long
FINDING_EVERY_S = 60        # the finding is rewritten at most this often while nothing was freed
STALE_PROGRESS_S = 300      # a running turn with no event or heartbeat this long has stopped making progress
RUNNING_LISTED = 10         # running turns named in the finding, least recent progress first
# Wait reasons only a person can clear (start_places.summary's words).
NEEDS_PERSON = {'shared login': 'a login', 'sign-in': 'a sign-in', 'quota': 'quota', 'spending limit': 'money'}
SUPPORT_BOT = 'support'

FREED = "Tico freed a dead start, will retry."
FREED_STOPPED = "Tico freed a start a person stopped; it is not retried."
FREED_READ_ONLY = "Tico freed a dead turn that only read, will retry."


def _gone(c, row):
    """Whether the turn's process is proven gone: its computer is revoked, or claimed lately without naming the bot."""
    from .execution import claimed_busy, claiming
    runner = c.execute('SELECT revoked_at FROM runners WHERE id=?', (row['runner_id'],)).fetchone()
    if not runner or runner['revoked_at']:
        return True
    if not claiming(row['runner_id']):
        return False
    bots, lanes = claimed_busy(row['runner_id'])
    return row['bot'] not in bots and not any(bot == row['bot'] for bot, _ in lanes)


# INDEXED BY: the OR defeats the live-run index, and the plain plan scanned every attempt ever made (45-150 ms on
# 92k attempts, inside the write transaction turn_fixer.free holds). The index holds only live runs, so this reads those.
DEAD_SQL = ("SELECT * FROM attempts INDEXED BY attempts_live_lease WHERE state IN ('leased','running') AND "
            "(lease_until<=? OR (state='leased' AND started_at IS NULL AND stop_requested_at IS NOT NULL)) "
            "ORDER BY created_at,id")


def dead(c, now=None):
    """[(attempt row, why)] for every proven-dead turn: 'stopped' or 'gone'."""
    now = now or H.now()
    out = []
    for row in c.execute(DEAD_SQL, (now,)).fetchall():
        if row['state'] == 'leased' and row['started_at'] is None and row['stop_requested_at']:
            out.append((row, 'stopped'))
        elif row['lease_until'] <= now and _gone(c, row):
            out.append((row, 'gone'))
    return out


def _task_of(c, job_id):
    job = c.execute('SELECT message_id FROM jobs WHERE id=?', (job_id,)).fetchone()
    msg = H.message(c, job['message_id']) if job else None
    conv = H.conversation(c, msg['conversation_id']) if msg else None
    task_id = H.message_task_id(msg, conv) if msg else None
    return msg, (H.task(c, task_id) if task_id else None)


def _note(c, task, text):
    if not task:
        return
    try:
        H.task_comment(c, H.KEEPER, task['id'], text, wake=False)
    except H.Refused:
        pass


def free(c, execution):
    """Free every proven-dead turn through the usual expiry; returns how many were freed."""
    from .execution import retry_facts
    freed = 0
    for row, why in dead(c):
        if why == 'stopped' and row['lease_until'] > H.now():
            c.execute('UPDATE attempts SET lease_until=? WHERE id=?', (H.now(), row['id']))
        execution.expire(c, attempt_ids=[row['id']])
        after = c.execute('SELECT a.state,j.state job_state FROM attempts a LEFT JOIN jobs j ON j.id=a.job_id '
                          'WHERE a.id=?', (row['id'],)).fetchone()
        if after['state'] in ('leased', 'running'):
            continue                        # given grace by expiry: not freed after all
        freed += 1
        msg, task = _task_of(c, row['job_id'])
        H.event(c, H.KEEPER, 'turns.dead_freed', row['id'], {'bot': row['bot'], 'why': why,
                                                              'job_state': after['job_state']})
        if after['job_state'] == 'queued':
            _note(c, task, FREED)
        elif after['job_state'] == 'cancelled':
            _note(c, task, FREED_STOPPED)
        elif after['job_state'] == 'uncertain':
            job = c.execute('SELECT * FROM jobs WHERE id=?', (row['job_id'],)).fetchone()
            ended = c.execute('SELECT started_at,finished_at FROM attempts WHERE id=?', (row['id'],)).fetchone()
            facts = retry_facts(c, job, ended['started_at'], ended['finished_at'])
            if facts['safe']:
                _note(c, task, FREED_READ_ONLY)
                continue
            acted = facts['acted']
            did = ("used " + ", ".join(acted)) if acted else (
                "used up an approval" if facts['consumed'] else "ran on a host that does not report its tool calls")
            _note(c, task, f"Tico freed a dead turn that {did}, so it may have pushed, sent or paid. It is not "
                           "retried: BotOps checks what already happened first.")
            if not c.execute("SELECT 1 FROM events WHERE action='job.held' AND target=?", (row['id'],)).fetchone():
                H.event(c, H.KEEPER, 'job.held', row['id'], {'job_id': job['id'], 'tools': acted})
                # The BotOps task is company-wide: it names the task only when BotOps may read the request.
                if privacy.message_readable(c, 'bot:' + H.FLEET_MAINTAINER, msg):
                    execution._hand_to_maintainer(c, job, msg, task, acted)
    return freed


def finding(c, settings, value):
    """The incident's one finding, from what the server already knows. Bounded: no task, message or login text."""
    from . import usage_limits
    from .performance import load_status
    reasons, oldest, computers = {}, {}, []
    # One company Default for the whole pass: each summary's claim_refusal used to rebuild pools/groups alone.
    default = usage_limits.company(c)
    for runner in c.execute('SELECT id,label FROM runners WHERE revoked_at IS NULL ORDER BY id').fetchall():
        here = start_places.summary(c, runner['id'], settings, default)
        for wait in here['waits']:
            reasons[wait['reason']] = reasons.get(wait['reason'], 0) + wait['count']
            oldest[wait['reason']] = min(oldest.get(wait['reason'], wait['oldest']), wait['oldest'])
        load = load_status(c, runner['id'])
        sample = load['latest_sample'] or {}
        computers.append({'label': runner['label'] or runner['id'], 'cpu_pct': sample.get('cpu_pct'),
                          'mem_pct': sample.get('mem_pct'), 'stale': bool(sample.get('stale')),
                          'overloaded': load['overloaded'],
                          'running': here['running']})
    now = H.now()
    running = []
    for row in c.execute("SELECT a.id,a.bot,a.runner_id,a.started_at,d.heartbeat_at,d.last_event_at FROM attempts a "
                         "LEFT JOIN attempt_diagnostics d ON d.attempt_id=a.id WHERE a.state='running'"):
        progress = max(x for x in (row['last_event_at'], row['heartbeat_at'], row['started_at'], '') if x is not None)
        running.append({'turn': row['id'], 'bot': row['bot'], 'last_progress': progress or None})
    running.sort(key=lambda r: (r['last_progress'] or '', r['turn']))
    stale = [r for r in running if not r['last_progress'] or r['last_progress'] < H.shift(now, seconds=-STALE_PROGRESS_S)]
    waits = sorted(({'reason': k, 'count': n, 'oldest': oldest[k]} for k, n in reasons.items()),
                   key=lambda w: (-w['count'], w['oldest']))
    busy = [x for x in computers if x['overloaded']]
    if busy:
        x = busy[0]
        bottleneck = (f"{x['label']} is overloaded (CPU {x['cpu_pct']}%, memory {x['mem_pct']}%), so new starts there "
                      "wait.")
    elif waits and waits[0]['count'] >= value['running']:
        bottleneck = f"Most turns are waiting for {waits[0]['reason']} ({waits[0]['count']} of {value['in_use']})."
    elif stale and len(stale) * 2 >= max(1, value['running']):
        bottleneck = f"{len(stale)} of {value['running']} running turns have made no progress for 5 minutes or more."
    else:
        bottleneck = f"{value['running']} turns are running and making progress; the company is busy, not stuck."
    counts = ', '.join(f"{w['count']} waiting for {w['reason']}" for w in waits) or 'none waiting'
    first = min(waits, key=lambda w: w['oldest'], default=None)
    if first:
        minutes = max(0, int((H.parse_ts(now) - H.parse_ts(first['oldest'])).total_seconds() // 60))
        age = f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"
        oldest_words = f"oldest wait {age} ({first['reason']})"
    else:
        oldest_words = "no turn waiting"
    sampled = [x for x in computers if not x['stale'] and (x['cpu_pct'] is not None or x['mem_pct'] is not None)]
    if sampled:
        top = max(sampled, key=lambda x: max(x['cpu_pct'] or 0, x['mem_pct'] or 0))
        cpu = 'unknown' if top['cpu_pct'] is None else f"{round(top['cpu_pct'])}%"
        mem = 'unknown' if top['mem_pct'] is None else f"{round(top['mem_pct'])}%"
        load_words = f"busiest computer {top['label']} at CPU {cpu}, memory {mem}"
    else:
        load_words = "CPU and memory not reported"
    text = (f"{value['in_use']} company turns in use (warning {value['warning']}, stop {value['stop']}): "
            f"{value['running']} running, {value['starting']} starting, {counts}; {oldest_words}; {load_words}. "
            f"Likely bottleneck: {bottleneck}")
    return {'text': text, 'bottleneck': bottleneck, 'waits': waits[:5], 'running': running[:RUNNING_LISTED],
            'stale_running': len(stale), 'computers': computers[:10], 'updated_at': now,
            'needs_person': NEEDS_PERSON.get(waits[0]['reason']) if waits else None}


def _older(stamp, seconds):
    return bool(stamp) and stamp <= H.shift(H.now(), seconds=-seconds)


def _helper(c):
    for slug in (H.FLEET_MAINTAINER, SUPPORT_BOT):
        bot = H.bot(c, slug)
        if bot and bot['state'] == 'active':
            return slug
    return None


def draft_finding(c, settings, *, force=False):
    """Build the next finding on a read connection. Returns None when no active incident needs a rewrite.

    The scheduler classifies here, outside the writer. `force` is set after dead starts were freed so wait counts
    refresh even when the previous finding is still young.
    """
    incident = org_turns.saved(c, org_turns.INCIDENT)
    if not incident.get('active') or not incident.get('id'):
        return None
    last = (incident.get('finding') or {}).get('updated_at')
    if not force and last and not _older(last, FINDING_EVERY_S):
        return None
    value = org_turns.snapshot(c, settings)
    return {'incident_id': incident['id'], 'finding': finding(c, settings, value)}


def tend(c, settings, owner_id='', freed=0, draft=None):
    """Update the open incident's finding, hand what the fixer cannot fix to BotOps once, and tell the owner when due.
    Returns the incident.

    `draft` is an optional finding built outside the writer (`draft_finding`). It is installed only when the same
    incident is still active; otherwise this pass rebuilds under the lock so a cleared incident cannot keep a stale
    finding.
    """
    org_turns.observe(c, settings, detailed=False)
    incident = org_turns.saved(c, org_turns.INCIDENT)
    if not incident.get('id'):
        return incident
    old = dict(incident)
    if not incident.get('active'):
        if incident.get('task_id') and not incident.get('told_resolved'):
            _note(c, H.task(c, incident['task_id']),
                  f"The company-turns incident resolved at {incident.get('resolved_at', '')}.")
            incident['told_resolved'] = True
    else:
        last = (incident.get('finding') or {}).get('updated_at')
        if freed or not last or _older(last, FINDING_EVERY_S):
            if (draft and draft.get('incident_id') == incident.get('id') and draft.get('finding')):
                incident['finding'] = draft['finding']
            else:
                incident['finding'] = finding(c, settings, org_turns.snapshot(c, settings))
        found = incident['finding']
        if not incident.get('task_id') and _older(incident['opened_at'], FIX_WINDOW_S):
            helper = _helper(c)
            if helper:
                try:
                    task = H.task_create(
                        c, H.KEEPER, f"Company turns incident {incident['id'][:8]}: what the turn fixer could not fix",
                        f"The company has been at or over its warning level since {incident['opened_at']} and the turn "
                        f"fixer freed every start it could prove dead.\n\n{found['text']}\n\nFind and fix what keeps "
                        "turns busy; Settings > Health shows the finding as it changes.", H.bot_actor(helper),
                        labels=["incident"])
                    incident['task_id'], incident['handed_to'] = task['id'], helper
                except H.Refused as exc:
                    incident['handed_to'] = ''
                    H.event(c, H.KEEPER, 'turns.handoff_refused', incident['id'], {'reason': str(exc)[:300]})
        needs = found.get('needs_person')
        if owner_id and not incident.get('noticed_at') and (needs or _older(incident['opened_at'], NOTICE_AFTER_S)):
            why = f"it needs {needs}" if needs else "it is still not fixed after an hour"
            try:
                H.say(c, H.KEEPER, 'human:' + owner_id,
                      f"Your company's turns are at their warning level and {why}. {found['text']}", kind='notice')
                incident['noticed_at'] = H.now()
            except H.Refused as exc:
                H.event(c, H.KEEPER, 'turns.notice_refused', incident['id'], {'reason': str(exc)[:300]})
    if incident != old:
        org_turns.save(c, org_turns.INCIDENT, incident)
    return incident


def run(c, execution, settings, owner_id='', draft=None):
    """One pass in one writer: free the dead, then tend the incident. Returns (freed, incident).

    For a caller that already holds one write transaction. The scheduler uses `run_split`, which keeps the fleet
    classification out of the writer."""
    freed = free(c, execution)
    return freed, tend(c, settings, owner_id, freed, draft)


def run_split(execution, settings, write, read, owner_id=None):
    """The scheduler's pass: free the dead under one short write, classify the fleet for the finding on a read
    connection (`draft_finding`), then tend the incident under a second short write. Claim renewals and events are
    not parked behind every queued bot's refusal check. Returns (freed, incident).

    `write(step)` and `read()` are context managers that give a connection; `owner_id` None means this
    environment's owner, looked up under the first write."""
    with write("turn_fixer.free") as c:
        if owner_id is None:
            owner_id = (execution.auth.owner_id(c) if execution.auth else '') or ''
        freed = free(c, execution)
    with read() as c:
        draft = draft_finding(c, settings, force=bool(freed))
    with write("turn_fixer.tend") as c:
        return freed, tend(c, settings, owner_id, freed, draft)


def words(freed, incident):
    parts = [f"{freed} dead start{'s' if freed != 1 else ''} freed"] if freed else []
    if incident.get('active'):
        parts.append('incident open' + (', handed to ' + incident['handed_to'] if incident.get('handed_to') else ''))
    return '; '.join(parts) or 'Nothing dead'

