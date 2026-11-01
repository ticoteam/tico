"""Outside failures have one durable incident, independent of bot/task recovery.

Only transitions send notes. A changed waiter count or repeated failed probe updates the same incident; neither
opens work nor wakes a manager. Source keys are digests and notices carry no task, login name or provider error.
"""
import hashlib
import json

from .store import H

PREFIX = 'source-outage:'


def observe(c, source, kind, *, waiting=0, until=None, active=True, since=None):
    """Record an observation in the caller's transaction; a rollback also rolls back the notice."""
    key = PREFIX + hashlib.sha256(source.encode()).hexdigest()[:32]
    saved = c.execute('SELECT value FROM registry_metadata WHERE key=?', (key,)).fetchone()
    old = H._json(saved[0], {}) if saved else {}
    if not active and not old.get('active'):
        return
    if old.get('active') == bool(active) and old.get('kind') == kind and old.get('waiting') == int(waiting) and old.get('until') == until:
        return
    now = H.now()
    record = {'source': source, 'kind': kind, 'active': bool(active), 'since': old.get('since') if old.get('active') else since or now,
              'waiting': int(waiting), 'until': until, 'updated_at': now}
    c.execute('INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (key, json.dumps(record)))
    if bool(old.get('active')) == bool(active):
        return
    transition = 'waiting' if active else 'restored'
    expected = (until[:16].replace('T', ' ') + ' UTC' if until and until > now else
                'checking availability now' if until else 'unknown; checked automatically') if active else 'available now'
    text = (f"{kind}: {'waiting' if active else 'available again'}. Since {record['since'][:16].replace('T', ' ')} UTC; "
            f"{int(waiting)} turns waiting. Expected return: {expected}.")
    # A board can name its own Engineering manager. Never assume the company's bot slug.
    managers = {r[0] for r in c.execute("SELECT manager FROM task_types WHERE role='engineering' AND manager IS NOT NULL")}
    from .health_monitor import SLUG
    recipients = {H.bot_actor(x) for x in managers if x} | {H.bot_actor(SLUG)}
    for target in sorted(recipients):
        bot = H.bot(c, H.actor_id(target))
        if not bot or bot['state'] in ('planned', 'archived'):
            continue
        H.say(c, H.KEEPER, target, text, kind='notice',
              refs={'quiet': True, 'source_outage': {'key': key, 'state': transition,
                    **{k: record[k] for k in ('kind', 'since', 'waiting', 'until')}}})
    H.event(c, H.KEEPER, 'source_outage.' + transition, key,
            {k: record[k] for k in ('kind', 'since', 'waiting', 'until')})


def sync(c, settings):
    """Observe persisted login holds, remembered GitHub reads and the computer's busy brake each scheduler tick.

    A reset permits one probe; it does not claim the source recovered. A successful turn removes the login hold,
    which ends the incident. Expired reset times are never presented as future availability.
    """
    from . import execution, login_holds, move_retry, performance
    seen = set()
    queued = [dict(r) for r in c.execute(
        "SELECT j.bot,count(*) n,a.runner_id,b.config FROM jobs j JOIN assignments a ON a.bot=j.bot "
        "JOIN bot_config b ON b.bot=j.bot WHERE j.state='queued' GROUP BY j.bot,a.runner_id,b.config")]
    counts = {}
    for row in queued:
        config = H._json(row['config'], {}) or {}
        primary = execution.login_key(c, settings, row['runner_id'], row['bot'], config)
        last = execution.last_limit(c, row['bot'])
        ended = login_holds.for_attempt(c, last['id']) if last else {}
        keys = set()
        if ended:
            keys.add((ended['runner_id'], ended.get('profile') or '', ended['runtime']))
        if not execution.fallback_choice(c, settings, row['runner_id'], row['bot'], config):
            keys.add(primary)
        for key in keys:
            counts[key] = counts.get(key, 0) + row['n']
    for row in queued:
        # Reuse wake batching's history/digest carrier; retries retain their own jobs and checkpoints.
        if execution.cooling_off(c, row['bot']):
            batch_waiters(c, row['bot'])
    for (raw,) in c.execute('SELECT value FROM registry_metadata WHERE key LIKE ?', (login_holds.PREFIX + '%',)).fetchall():
        hold = H._json(raw, {}) or {}
        if not isinstance(hold, dict) or not hold.get('runner_id') or not hold.get('runtime'):
            continue
        source = login_holds.key(hold['runner_id'], hold.get('profile'), hold['runtime'])
        count = counts.get((hold['runner_id'], hold.get('profile') or '', hold['runtime']), 0)
        kind = f"{hold['runtime'].capitalize()} " + ('provider capacity' if hold.get('capacity') else 'subscription quota')
        observe(c, source, kind, waiting=count, until=hold.get('until'), since=hold.get('since'))
        seen.add(PREFIX + hashlib.sha256(source.encode()).hexdigest()[:32])
    github = {}
    # Read the latest remembered move per ticket; stale moves neither count waiters nor keep an incident open.
    cutoff = H.shift(H.now(), seconds=-int(move_retry.RETRY_FOR.total_seconds()))
    for (task_id,) in c.execute("SELECT DISTINCT task_id FROM task_events WHERE field=? AND ts>=?",
                                (move_retry.FIELD, cutoff)).fetchall():
        row = H.task(c, task_id)
        found = move_retry.remembered(c, dict(row), H.parse_ts(H.now()))
        if not found or not found[1].get('outage') or found[1].get('local'):
            continue
        detail = found[1]
        from .review_gate import links
        for link in links(c, dict(row)):
            # All checks for the same repository share access; PR numbers never split an incident.
            source = 'github:' + (link.get('repo') or (link.get('url') or '').split('/pull/')[0]).lower()
            saved = c.execute('SELECT value FROM registry_metadata WHERE key=?',
                              (PREFIX + hashlib.sha256(source.encode()).hexdigest()[:32],)).fetchone()
            proof = H._json(saved[0], {}) if saved else {}
            if proof and not proof.get('active') and (detail.get('at') or '') <= proof.get('updated_at', ''):
                continue  # A successful read supersedes this old refusal, even if the ticket has not moved.
            entry = github.setdefault(source, {'tasks': set(), 'since': detail.get('since')})
            entry['tasks'].add(row['id'])
    for source, entry in github.items():
        observe(c, source, 'GitHub checks access', waiting=len(entry['tasks']), since=entry['since'])
        seen.add(PREFIX + hashlib.sha256(source.encode()).hexdigest()[:32])
    # A move's expiry, close or cancellation is not evidence that GitHub's access recovered.
    for key, raw in c.execute('SELECT key,value FROM registry_metadata WHERE key LIKE ?', (PREFIX + '%',)).fetchall():
        value = H._json(raw, {}) or {}
        if value.get('active') and value.get('source', '').startswith('github:') and key not in seen:
            observe(c, value['source'], value['kind'], waiting=0)
            seen.add(key)
    for (runner_id,) in c.execute('SELECT id FROM runners').fetchall():
        if performance.load_status(c, runner_id, now=H.parse_ts(H.now()).timestamp())['overloaded']:
            source = 'computer:' + runner_id
            observe(c, source, 'Computer capacity', waiting=sum(x['n'] for x in queued if x['runner_id'] == runner_id))
            seen.add(PREFIX + hashlib.sha256(source.encode()).hexdigest()[:32])
    for key, raw in c.execute('SELECT key,value FROM registry_metadata WHERE key LIKE ?', (PREFIX + '%',)).fetchall():
        value = H._json(raw, {}) or {}
        if value.get('active') and key not in seen and not value.get('source', '').startswith('github:'):
            # The digest is already the durable identity; finish through the same transition writer without hashing twice.
            _finish(c, key, value)


def _finish(c, key, value):
    # The raw identity stays in private metadata; notices and transition events use only its digest.
    source = value.get('source')
    if source:
        observe(c, source, value['kind'], active=False)


def batch_waiters(c, bot):
    from . import wake_presets
    groups = {}
    for row in c.execute("SELECT j.*,m.conversation_id,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "WHERE j.bot=? AND j.state='queued' AND j.attempt_id IS NULL AND j.redirect=0 "
                         "AND m.from_actor=? AND m.kind='notice' AND m.deleted_at IS NULL "
                         "AND coalesce(json_extract(m.refs,'$.quiet'),0)=0 "
                         "AND coalesce(json_extract(m.refs,'$.command'),0)=0 "
                         "ORDER BY j.created_at,j.rowid", (bot, H.KEEPER)).fetchall():
        refs = H._json(row['refs'], {}) or {}
        if refs.get('caused_by', '').startswith('human:') or refs.get('wake_digest'):
            continue
        groups.setdefault((row['conversation_id'], refs.get('task')), []).append(dict(row))
    for jobs in groups.values():
        if len(jobs) > 1:
            wake_presets._release(c, bot, jobs)


def github_read(c, prs, snapshots):
    """Only a complete successful head/checks read confirms access recovery; stale moves prove nothing."""
    repos = {}
    for pr in prs:
        if pr.get('repo'):
            repos.setdefault(pr['repo'].lower(), []).append(snapshots.get(pr['id']))
    for repo, results in repos.items():
        if results and all(r and not r.get('error') and r.get('head_sha') for r in results):
            observe(c, 'github:' + repo, 'GitHub checks access', active=False)
