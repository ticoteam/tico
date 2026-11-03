"""Evidence-backed pilot reports. No model calls, repository writes or scheduled work.

Counters use the admission ledger, reviewer decisions and authenticated computer
reports. Coverage is part of a count: an absent record never establishes zero.
"""
import json
import re
from collections import Counter
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import Request
from pydantic import Field

from . import memory_tidy as tidy, memory_tidy_controls as controls, models as M
from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_tidy_corrections (
 bot TEXT NOT NULL, evidence TEXT NOT NULL, occurred_at TEXT NOT NULL,
 reason TEXT NOT NULL, recorded_by TEXT NOT NULL, recorded_at TEXT NOT NULL,
 PRIMARY KEY(bot,evidence));
CREATE TABLE IF NOT EXISTS memory_tidy_keep_metrics (
 card_id TEXT PRIMARY KEY, parent_commit TEXT NOT NULL, commit_sha TEXT NOT NULL,
 before_metrics TEXT NOT NULL, after_metrics TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_tidy_reverts (
 bot TEXT NOT NULL, sha TEXT NOT NULL, target TEXT NOT NULL, committed_at TEXT NOT NULL,
 reason TEXT NOT NULL, lost_rule TEXT NOT NULL, evidence TEXT NOT NULL,
 PRIMARY KEY(bot,sha,target));
CREATE TABLE IF NOT EXISTS memory_tidy_report_coverage (
 bot TEXT NOT NULL, since TEXT NOT NULL, until TEXT NOT NULL,
 PRIMARY KEY(bot,since));
"""
LIVE = 'memory_tidy:report_live_since'
REVIEWER = 'bot:product-lead'
SHA = r'[0-9a-f]{40,64}'


def stamp(at):
    from .memory_tidy_runs import stamp as utc
    return utc(at)


def at(value):
    result = H.parse_ts(value)
    if result is None or result.tzinfo is None:
        raise Problem('validation', 'Use a timestamp with its timezone', 422)
    return result


def ensure(c):
    c.execute('INSERT OR IGNORE INTO registry_metadata(key,value) VALUES(?,?)', (LIVE, H.now()))


def metrics(snapshot):
    return tidy.snapshot_metrics(snapshot)


class SnapshotMetrics(M.Contract):
    rule_count: int | None = Field(default=None, ge=0, le=10_000_000)
    rule_characters: int | None = Field(default=None, ge=0, le=100_000_000)
    learnings_bytes: int | None = Field(default=None, ge=0, le=100_000_000)
    memory_knowledge_bytes: int = Field(ge=0, le=1_000_000_000)


class KeepMetrics(M.Contract):
    parent_commit: str = Field(pattern='^' + SHA + '$')
    before: SnapshotMetrics
    after: SnapshotMetrics


class HistoryCommit(M.Contract):
    sha: str = Field(pattern='^' + SHA + '$')
    committed: str = Field(max_length=40)
    message: str = Field(max_length=16000)


class CorrectionRef(M.Contract):
    bot: str = Field(min_length=1, max_length=100)
    evidence: str = Field(min_length=1, max_length=2000)


class Correction(CorrectionRef):
    occurred_at: str = Field(max_length=40)
    reason: str = Field(min_length=1, max_length=500)


def valid_link(value):
    parsed = urlsplit(value)
    return parsed.scheme in ('https', 'http') and bool(parsed.hostname) and not parsed.username and not parsed.password


def record_correction(c, auth, who, body):
    auth.domain(who)
    if who.role not in ('human', 'owner') and who.actor != REVIEWER:
        raise Problem('forbidden', 'Only a person or the named reviewer records a correction', 403)
    bot = H.resolve_bot(c, body.bot)
    if not bot or bot['id'] not in {r['bot'] for r in controls.pilots(c)}:
        raise Problem('not_found', 'Pilot bot not found', 404)
    if who.actor != REVIEWER:
        auth.require_read(c, who, bot['id'])
    if not valid_link(body.evidence) or '\n' in body.reason or '\r' in body.reason or not body.reason.strip():
        raise Problem('validation', 'Supply an evidence link and one line saying what was wrong', 422)
    when = stamp(at(body.occurred_at))
    if when > stamp(at(H.now())):
        raise Problem('validation', 'A correction cannot be in the future', 422)
    old = c.execute('SELECT occurred_at,reason FROM memory_tidy_corrections WHERE bot=? AND evidence=?',
                    (bot['id'], body.evidence)).fetchone()
    if old and tuple(old) != (when, body.reason):
        raise Problem('conflict', 'This evidence already has a correction record', 409)
    c.execute('INSERT OR IGNORE INTO memory_tidy_corrections VALUES(?,?,?,?,?,?)',
              (bot['id'], body.evidence, when, body.reason, who.actor, H.now()))
    return {'recorded': True}


def record_history(c, bot, commits, complete_from=None):
    """Called only from the assigned computer's authenticated memory report.

    The supplied range contains every pushed memory commit, including already
    reported commits. A bounded Git log can leave gaps; ranges never bridge them.
    """
    now = stamp(at(H.now()))
    if complete_from and (not commits or at(complete_from) != min(at(r.committed) for r in commits)):
        raise Problem('validation', 'Coverage needs the complete reported commit range', 422)
    for commit in commits:
        when = stamp(at(commit.committed))
        # Standard git revert body supplies an exact target, never the subject.
        targets = re.findall(r'^This reverts commit (' + SHA + r')\.$', commit.message, re.M)
        lines = {}
        for line in commit.message.splitlines():
            for key in ('Reason', 'Lost-rule', 'Evidence'):
                if line.startswith(key + ': '):
                    lines[key] = line[len(key) + 2:].strip()
        for target in set(targets):
            # History can arrive before a lost Keep-completion response is
            # recovered. Retain the link now; report only confirmed Keep targets.
            reason = lines.get('Reason', '')[:500]
            rule, evidence = lines.get('Lost-rule', '')[:500], lines.get('Evidence', '')[:2000]
            if reason.lower() == 'lost a rule' and (not rule or not valid_link(evidence)):
                reason = ''  # incomplete reason is unknown, never an ordinary revert
            c.execute('INSERT OR IGNORE INTO memory_tidy_reverts VALUES(?,?,?,?,?,?,?)',
                      (bot, commit.sha, target, when, reason, rule, evidence))
    if complete_from:
        since = stamp(at(complete_from))
        live = c.execute('SELECT value FROM registry_metadata WHERE key=?', (LIVE,)).fetchone()[0]
        since = max(since, stamp(at(live)))
        if since > now:
            raise Problem('validation', 'Invalid history coverage interval', 422)
        c.execute('INSERT INTO memory_tidy_report_coverage VALUES(?,?,?) '
                  'ON CONFLICT(bot,since) DO UPDATE SET until=max(until,excluded.until)', (bot, since, now))


def covered(c, bot, start, end):
    if end <= start:
        return False
    cursor = start
    for row in c.execute('SELECT since,until FROM memory_tidy_report_coverage WHERE bot=? AND until>=? '
                         'AND since<=? ORDER BY since', (bot, start, end)):
        if row['since'] > cursor:
            return False
        cursor = max(cursor, row['until'])
    return cursor >= end


def measure(count, known):
    return {'value': count if known else None, 'recorded': count, 'coverage': 'complete' if known else 'unknown'}


def no_higher(current, baseline, key):
    left, right = current[key]['value'], baseline[key]['value']
    return left <= right if left is not None and right is not None else None


def reopened(c, bot, start, end):
    # Owner changes after a reopening must not move it into another bot's result.
    first = c.execute('SELECT min(created_at) FROM task_events').fetchone()[0]
    count = 0
    for row in c.execute("SELECT e.*,t.owner_id FROM task_events e JOIN tasks t ON t.id=e.task_id "
                         "WHERE e.field='status' AND e.old IN ('done','closed') AND e.new='open' "
                         "AND e.created_at>=? AND e.created_at<?", (start, end)):
        future = c.execute("SELECT old FROM task_events WHERE task_id=? AND field IN ('owner','owner_id') "
                            "AND created_at>? ORDER BY created_at,id LIMIT 1", (row['task_id'], row['created_at'])).fetchone()
        owner = future[0] if future else row['owner_id']
        count += owner == 'bot:' + bot
    return measure(count, bool(first and first <= start))


def outcomes(c, bot, start, end, now):
    passes = [dict(r) for r in c.execute('SELECT p.*,t.id AS card_id,t.status AS card_status,t.decided_at,'
        'k.commit_sha,m.parent_commit,m.before_metrics,m.after_metrics FROM memory_tidy_passes p '
        'LEFT JOIN memory_tidy_cards t ON t.pass_id=p.id '
        "LEFT JOIN memory_tidy_keeps k ON k.card_id=t.id AND k.status='kept' "
        'LEFT JOIN memory_tidy_keep_metrics m ON m.card_id=t.id '
        'WHERE p.bot=? AND p.started_at>=? AND p.started_at<? ORDER BY p.started_at,p.id LIMIT 201', (bot, start, end))]
    if len(passes) > 200:
        raise Problem('too_large', 'Pilot pass cohort exceeds the report limit', 413)
    counts, skips = Counter(), Counter()
    costs = []
    kept = []
    for p in passes:
        ran = p['status'] in ('offered', 'kept', 'discarded', 'flagged', 'no_change') or c.execute(
            'SELECT 1 FROM turns WHERE task_id=? AND started_at IS NOT NULL LIMIT 1', (p['check_task_id'],)).fetchone() is not None
        counts['passes_run'] += bool(ran)
        if p['status'] == 'skipped':
            skips[p['reason']] += 1
        state = p['card_status'] or p['status']
        if state == 'kept' and not p['commit_sha']:
            state = 'keep_unconfirmed'
        counts[state] += 1
        if state == 'kept':
            kept.append(p)
        costs.append({'pass_id': p['id'], 'model': p['model'], 'estimated_cost_usd': p['cost_usd'], 'ran': bool(ran)})
    targets = {p['commit_sha'] for p in kept}
    reverts = [dict(r) for r in c.execute('SELECT * FROM memory_tidy_reverts WHERE bot=? AND committed_at<?', (bot, now)) if r['target'] in targets]
    full = covered(c, bot, start, min(end, now))
    reverted = measure(len({r['target'] for r in reverts}), full)
    lost = measure(len({r['target'] for r in reverts if r['reason'].lower() == 'lost a rule'}), full and all(r['reason'] for r in reverts))
    # Applied snapshots include edits skipped against moving main. Proposal diaries do not.
    sizes = None
    if kept and all(p['before_metrics'] and p['after_metrics'] for p in kept):
        applied = sorted(kept, key=lambda p: (p['decided_at'], p['id']))
        sizes = {'before': json.loads(applied[0]['before_metrics']), 'after': json.loads(applied[-1]['after_metrics']),
                 'before_commit': applied[0]['parent_commit'], 'after_commit': applied[-1]['commit_sha'],
                 'scope': 'first kept parent to last kept commit in this pass cohort'}
    live = stamp(at(c.execute('SELECT value FROM registry_metadata WHERE key=?', (LIVE,)).fetchone()[0]))
    corrections = c.execute('SELECT count(*) FROM memory_tidy_corrections WHERE bot=? AND occurred_at>=? AND occurred_at<?',
                             (bot, start, min(end, now))).fetchone()[0]
    return {'counts': dict(counts), 'skipped_by_reason': dict(skips), 'per_pass': costs,
            'kept_rate': counts['kept'] / counts['passes_run'] if counts['passes_run'] else None,
            'estimated_cost_usd': sum(p['cost_usd'] for p in passes), 'sizes': sizes,
            'reverted': reverted, 'lost_rule_reverts': lost,
            'reopened_tasks': reopened(c, bot, start, min(end, now)),
            'corrections': measure(corrections, live <= start < now),
            'complete_period': now >= end}


def build(c, pilot_start, week, now):
    start, now = stamp(at(pilot_start)), stamp(at(now))
    anchor = at(start)
    end = stamp(anchor + timedelta(days=7 * week))
    week_start = stamp(anchor + timedelta(days=7 * (week - 1)))
    baseline_start = stamp(anchor - timedelta(days=14))
    rows = []
    # Retain historical members when the current list is edited after a pass.
    bots = {p['bot'] for p in controls.pilots(c)} | {r[0] for r in c.execute('SELECT DISTINCT bot FROM memory_tidy_passes')}
    if len(bots) > 20:
        raise Problem('too_large', 'Pilot membership history exceeds the report limit', 413)
    for bot in sorted(bots):
        weekly = outcomes(c, bot, week_start, end, now)
        two = outcomes(c, bot, start, stamp(anchor + timedelta(days=14)), now)
        live = stamp(at(c.execute('SELECT value FROM registry_metadata WHERE key=?', (LIVE,)).fetchone()[0]))
        corrections = c.execute('SELECT count(*) FROM memory_tidy_corrections WHERE bot=? AND occurred_at>=? AND occurred_at<?',
                                 (bot, baseline_start, start)).fetchone()[0]
        prior_reverts = [dict(r) for r in c.execute('SELECT r.* FROM memory_tidy_reverts r WHERE r.bot=? AND r.committed_at>=? AND r.committed_at<? '
            "AND EXISTS(SELECT 1 FROM memory_tidy_keeps k JOIN memory_tidy_cards t ON t.id=k.card_id WHERE t.bot=r.bot AND k.status='kept' AND k.commit_sha=r.target)",
                                                   (bot, baseline_start, start))]
        baseline = {'corrections': measure(corrections, live <= baseline_start),
                    'lost_rule_reverts': measure(len({r['target'] for r in prior_reverts if r['reason'].lower() == 'lost a rule'}),
                                                 covered(c, bot, baseline_start, start) and all(r['reason'] for r in prior_reverts)),
                    'reopened_tasks': reopened(c, bot, baseline_start, start)}
        unknown = []
        for key in ('corrections', 'lost_rule_reverts'):
            if baseline[key]['value'] is None:
                unknown.append('baseline ' + key.replace('_', ' '))
        for key in ('corrections', 'lost_rule_reverts', 'reopened_tasks'):
            if two[key]['value'] is None:
                unknown.append('pilot ' + key.replace('_', ' '))
        if baseline['reopened_tasks']['value'] is None:
            unknown.append('baseline reopened tasks')
        if not two['sizes'] or any(two['sizes'][side]['rule_characters'] is None for side in ('before', 'after')):
            unknown.append('actual working-rule sizes')
        if two['kept_rate'] is None:
            unknown.append('keep rate')
        size = two['sizes']
        before = size['before']['rule_characters'] if size else None
        after = size['after']['rule_characters'] if size else None
        movement = (after <= tidy.TARGET or abs(after - tidy.TARGET) < abs(before - tidy.TARGET)) if before is not None and after is not None else None
        thresholds = {'kept_at_least_60_percent': two['kept_rate'] >= .6 if two['kept_rate'] is not None else None,
                      'no_lost_rule_reverts': two['lost_rule_reverts']['value'] == 0 if two['lost_rule_reverts']['value'] is not None else None,
                      'reopenings_no_higher': no_higher(two, baseline, 'reopened_tasks'),
                      'corrections_no_higher': no_higher(two, baseline, 'corrections'), 'rules_toward_target': movement}
        goal = two['complete_period'] and not unknown and all(value is True for value in thresholds.values())
        # Installation never backdates coverage to fill the pre-pilot baseline.
        rows.append({'bot': bot, 'slug': (H.bot(c, bot) or {}).get('slug', bot), 'weekly': weekly,
                     'two_week': two, 'baseline': baseline, 'goal_pass': goal, 'thresholds': thresholds,
                     'unknown': unknown, 'two_week_complete': two['complete_period']})
    total = c.execute('SELECT coalesce(sum(cost_usd),0) FROM memory_tidy_passes').fetchone()[0]
    return {'title': 'Nightly memory tidy', 'pilot_start': start, 'week': week,
            'window_start': week_start, 'window_end': end, 'as_of': now,
            'baseline_start': baseline_start, 'baseline_end': start, 'bots': rows,
            'estimated_pilot_cost_usd': total, 'spend_stop_usd': 50, 'spend_stopped': total >= 50,
            'working_rules_target_characters': tidy.TARGET, 'goal_pass': bool(rows) and all(r['goal_pass'] for r in rows),
            'note': 'Any unknown record coverage prevents a Goal pass; thresholds are unchanged.'}


def markdown(report):
    def value(metric):
        return str(metric['value']) if metric['value'] is not None else ('unknown' + (f" ({metric['recorded']} recorded)" if metric['recorded'] else ''))
    lines = [f"# {report['title']} — week {report['week']}", '',
             f"Pass cohort: {report['window_start']} to {report['window_end']} (end excluded). Results as of {report['as_of']}.",
             f"Baseline: {report['baseline_start']} to {report['baseline_end']} (14 days).",
             f"Total estimated pilot spend: ${report['estimated_pilot_cost_usd']:.4f} / $50 stop.", '',
             '| Bot | Passes run | Kept | Discarded | Withdrawn | Reverted | Estimated cost |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for bot in report['bots']:
        w, n = bot['weekly'], bot['weekly']['counts']
        lines.append(f"| {bot['slug']} | {n.get('passes_run', 0)} | {n.get('kept', 0)} | {n.get('discarded', 0)} | {n.get('withdrawn', 0)} | {value(w['reverted'])} | ${w['estimated_cost_usd']:.4f} |")
    for bot in report['bots']:
        w, two, base = bot['weekly'], bot['two_week'], bot['baseline']
        lines += ['', f"## {bot['slug']}", '', 'Skipped passes by reason: ' + (json.dumps(w['skipped_by_reason'], ensure_ascii=False) if w['skipped_by_reason'] else 'none recorded') + '.',
                  f"This week: reopened tasks {value(w['reopened_tasks'])}; corrections {value(w['corrections'])}.",
                  'Actual applied Keep snapshot sizes: ' + ('shown below' if w['sizes'] else 'unknown') + '.',
                  f"Two-week kept rate: {two['kept_rate']:.1%} (target at least 60%)." if two['kept_rate'] is not None else 'Two-week kept rate: unknown.',
                  f"Two-week reopened tasks / prior 14 days: {value(two['reopened_tasks'])} / {value(base['reopened_tasks'])}.",
                  f"Two-week corrections / prior 14 days: {value(two['corrections'])} / {value(base['corrections'])}.",
                  f"Lost-rule reverts / prior 14 days: {value(two['lost_rule_reverts'])} / {value(base['lost_rule_reverts'])}.",
                  ('Goal: pass.' if bot['goal_pass'] else 'Goal: not a pass.') + ' Unknown: ' + (', '.join(bot['unknown']) or 'none') + '.',
                  'Working-rule size target: 20,000 characters; it is not a hard stop.', '',
                  'Two-week thresholds: ' + json.dumps(bot['thresholds']) + '.', '']
        if w['sizes']:
            lines += ['| Applied snapshot measure | Before | After |', '|---|---:|---:|']
            for key, label in [('rule_count', 'Working rules'), ('rule_characters', 'Rule characters'),
                               ('learnings_bytes', 'Learnings file bytes'), ('memory_knowledge_bytes', 'Memory and knowledge bytes')]:
                lines.append(f"| {label} | {w['sizes']['before'][key]} | {w['sizes']['after'][key]} |")
        lines += ['', '| Pass | Model | Estimated cost |', '|---|---|---:|']
        lines += [f"| {p['pass_id']} | {p['model']} | ${p['estimated_cost_usd']:.6f} |" for p in w['per_pass']]
    return '\n'.join(lines) + '\n'


def weekly(c, auth, now):
    """Deliver each completed pilot week once to its owner and named reviewer.

    This consumes the existing clock; it never switches on or creates a routine.
    Reports remain readable/refetchable for later card decisions or reversions.
    """
    first = c.execute('SELECT min(started_at) FROM memory_tidy_passes').fetchone()[0]
    if not first:
        return []
    from . import memory_tidy_runs as runs
    runs.refresh_costs(c)
    owner = auth.owner_id(c)
    reviewer = H.bot(c, H.actor_id(REVIEWER))
    recipients = (['human:' + owner] if owner else []) + ([REVIEWER] if reviewer and reviewer['state'] == 'active' else [])
    delivered = []
    for week in (1, 2):
        if at(now) < at(first) + timedelta(days=7 * week):
            continue
        report = build(c, first, week, now)
        for recipient in recipients:
            key = f'memory_tidy:weekly:{first}:{week}:{recipient}'
            if c.execute('SELECT 1 FROM registry_metadata WHERE key=?', (key,)).fetchone():
                continue
            task = H.task_create(c, H.KEEPER, f'Nightly memory tidy: week {week} report', markdown(report),
                                 recipient, private=True, lint=False, deduplicate=False)
            c.execute('INSERT INTO registry_metadata VALUES(?,?)', (key, task['id']))
            delivered.append(task['id'])
    return delivered


def install(app, store, auth, mutate):
    @app.get('/api/v2/memory-tidy/report')
    def report(request: Request, pilot_start: str, week: int = 1):
        who = request.state.identity
        if who.role != 'owner' and who.actor != REVIEWER:
            raise Problem('forbidden', 'Only the owner and named reviewer read the pilot report', 403)
        if week not in (1, 2):
            raise Problem('validation', 'Choose pilot week 1 or 2', 422)
        with store.transaction() as c:
            from .memory_tidy_runs import refresh_costs
            refresh_costs(c)
            result = build(c, pilot_start, week, H.now())
            payload = {'report': result, 'markdown': markdown(result)}
            if len(json.dumps(payload).encode()) > 256_000:
                raise Problem('too_large', 'Pilot report exceeds the response limit', 413)
            return payload

    @app.post('/api/v2/memory-tidy/corrections')
    def correction(request: Request, body: Correction):
        return mutate(request, body, lambda c: record_correction(c, auth, request.state.identity, body))

    @app.delete('/api/v2/memory-tidy/corrections')
    def remove_correction(request: Request, body: CorrectionRef):
        def write(c):
            who = request.state.identity
            auth.domain(who)
            bot = H.resolve_bot(c, body.bot)
            if not bot:
                raise Problem('not_found', 'Pilot bot not found', 404)
            row = c.execute('SELECT recorded_by FROM memory_tidy_corrections WHERE bot=? AND evidence=?',
                            (bot['id'], body.evidence)).fetchone()
            if who.role != 'owner' and who.actor != REVIEWER and (not row or row[0] != who.actor):
                raise Problem('forbidden', 'Only its recorder, owner or named reviewer removes a correction', 403)
            if who.actor != REVIEWER:
                auth.require_read(c, who, bot['id'])
            c.execute('DELETE FROM memory_tidy_corrections WHERE bot=? AND evidence=?', (bot['id'], body.evidence))
            H.event(c, who.actor, 'memory_tidy.correction_removed', bot['id'], {})
            return {'recorded': False}
        return mutate(request, body, write)
