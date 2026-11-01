"""Nightly memory tidy admission and durable ledger. No model calls or repository writes.

Filing is atomic with the switch, membership, spend and model checks. Runtime protection
must be installed before a child is queued. Result checking/card delivery has its own
trusted handoff; an unfinished child is withdrawn at 05:00 Pacific even with the switch off.
"""
import json
from datetime import datetime, time, timezone

from . import check_models, memory_tidy_controls as controls, providers, system_routines as SR, usage_limits
from .store import H, Problem
from .updates import ZONE

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_tidy_passes(
 id TEXT PRIMARY KEY, night TEXT NOT NULL, bot TEXT NOT NULL, model TEXT NOT NULL,
 started TEXT NOT NULL, deadline TEXT NOT NULL, status TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '', parent_task_id TEXT, check_task_id TEXT,
 baseline TEXT NOT NULL DEFAULT '', cost_usd REAL NOT NULL DEFAULT 0,
 UNIQUE(night,bot));
CREATE INDEX IF NOT EXISTS memory_tidy_passes_bot ON memory_tidy_passes(bot,started);
"""
STOP = 50.0


def stamp(at):
    return at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def changed(c, bot, since):
    # Only pushed memory/ counts. A knowledge-only change does not buy a model call.
    excluded = {r[0] for r in c.execute(
        "SELECT t.id FROM turns t JOIN memory_tidy_passes p "
        "ON t.task_id=p.parent_task_id OR t.task_id=p.check_task_id WHERE p.bot=?", (bot,))}
    for row in c.execute("SELECT subject,files_json,attempt_id FROM bot_memory_updates "
                         "WHERE bot=? AND shared=1 AND committed>?", (bot, since)):
        if row['subject'].startswith('Dreamed ') or row['attempt_id'] in excluded:
            continue
        if any(p.startswith('memory/') for p in json.loads(row['files_json'])):
            return True
    return False


def refresh_costs(c):
    # Usage has already been priced from providers.PRICES. Never filter subscription
    # segments or replace an existing estimate with zero cash billing.
    from .usage_records import RUNS
    for p in c.execute("SELECT id,check_task_id,cost_usd FROM memory_tidy_passes WHERE check_task_id IS NOT NULL").fetchall():
        rows = c.execute("SELECT est_cost_usd,model,input_tokens,cached_tokens,output_tokens FROM " + RUNS +
                         " u WHERE task_id=?", (p['check_task_id'],)).fetchall()
        total = 0.0
        for r in rows:
            estimate = providers.estimate_cost(r['model'], r['input_tokens'], r['cached_tokens'], r['output_tokens'])
            total += max(float(r['est_cost_usd'] or 0), float(estimate or 0))
        c.execute("UPDATE memory_tidy_passes SET cost_usd=max(cost_usd,?) WHERE id=?", (total, p['id']))
    return c.execute("SELECT coalesce(sum(cost_usd),0) FROM memory_tidy_passes").fetchone()[0]


def log(c, bot, reason):
    run = SR.Run(controls.KEY)
    run.did(bot + ': ' + reason)
    SR.save(c, run)


def report_stop(c, total):
    # One durable report, not a new Product turn on every scheduler tick.
    key = 'memory_tidy:spend_stop_report'
    if c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (key,)).fetchone():
        return
    report = H.task_create(c, H.KEEPER, 'Nightly memory tidy: pilot spend stop',
        f'Pilot estimated usage has reached ${total:.2f}, including subscription usage. '
        f'The amount past $50 is ${max(0, total - STOP):.2f}. '
        'No further passes may start. This is a stop report, not approval for more spend.',
        'bot:product-lead', private=True, lint=False, deduplicate=False)
    c.execute("INSERT INTO registry_metadata VALUES(?,?)", (key, json.dumps({'task': report['id'], 'total': total})))


def close_tasks(c, row, reason):
    # Closing the last child wakes an open parent even with quiet=True. Retire
    # the container first so deadline cancellation never starts an ordinary turn.
    for key in ('parent_task_id', 'check_task_id'):
        if row[key]:
            H.task_close(c, H.KEEPER, row[key], note=reason, quiet=True)


def close_pass(c, row, reason):
    close_tasks(c, row, reason)
    c.execute("UPDATE memory_tidy_passes SET status='skipped',reason=? WHERE id=?", (reason, row['id']))
    log(c, row['bot'], reason)


def sweep(c, at):
    for row in c.execute("SELECT * FROM memory_tidy_passes WHERE status='filed' AND deadline<=?", (stamp(at),)).fetchall():
        close_pass(c, row, 'skipped: not finished')


def settle_terminal(c):
    # Runtime model-unavailable skips close only their check. Retire the private
    # container as well, then let the usage settlement guard release the slot.
    for row in c.execute("SELECT p.* FROM memory_tidy_passes p JOIN tasks t ON t.id=p.check_task_id "
                         "WHERE p.status='filed' AND t.status IN ('declined','closed')").fetchall():
        close_pass(c, row, 'skipped: model unavailable')


def unsettled(c):
    """Keep the global slot until both execution and its accounting have settled.

    Closing a deadline task requests cancellation; it does not prove the runner has
    stopped. Unknown usage likewise cannot release a slot as a free pass.
    """
    if c.execute("SELECT 1 FROM memory_tidy_passes WHERE status='filed' LIMIT 1").fetchone():
        return True
    return c.execute(
        "SELECT 1 FROM memory_tidy_passes p JOIN turns t ON t.task_id=p.check_task_id "
        "WHERE t.finished IS NULL OR (t.usage_reason IS NOT NULL AND "
        "NOT EXISTS(SELECT 1 FROM turn_usage_segments s WHERE s.turn_id=t.id) "
        "AND coalesce(t.input_tokens,0)+coalesce(t.output_tokens,0)=0 "
        "AND (NOT EXISTS(SELECT 1 FROM attempts a WHERE a.id=t.id) OR "
        "EXISTS(SELECT 1 FROM attempts a WHERE a.id=t.id AND a.started IS NOT NULL))) LIMIT 1").fetchone() is not None


def packet_runner(c, bot):
    row = c.execute('SELECT r.capabilities_json FROM assignments a JOIN runners r ON r.id=a.runner_id '
                    'WHERE a.bot=? AND r.revoked_at IS NULL', (bot,)).fetchone()
    return bool(row and 'memory_tidy_packets_v1' in json.loads(row[0] or '[]'))


def before_start(c, settings, task_id, at):
    """Recheck admission under the claim lock, before a runner gets a model call."""
    row = c.execute('SELECT * FROM memory_tidy_passes WHERE check_task_id=?', (task_id,)).fetchone()
    if not row:
        return True
    if row['status'] != 'filed':
        return False
    total = refresh_costs(c)
    selected = controls.eligible(c, settings, row['bot'])
    reason = ('skipped: spend stop' if total >= STOP else
              'skipped: not finished' if stamp(at) >= row['deadline'] else
              'skipped: model unavailable' if not selected or selected['model'] != row['model'] or not packet_runner(c, row['bot']) else
              'skipped: spend limit' if usage_limits.blocked(c, row['bot'], at) else '')
    if reason:
        close_pass(c, row, reason)
        if total >= STOP:
            report_stop(c, total)
        return False
    return True


def admit(c, settings, at):
    """One nightly admission transaction; all skips precede task creation/model dispatch."""
    sweep(c, at)
    settle_terminal(c)
    sync_decisions(c)
    total = refresh_costs(c)
    # The store's write transaction is the admission lock. Never admit against
    # an earlier pass's incomplete cost, including after deadline cancellation.
    if unsettled(c):
        return []
    if not SR.config_of(c, controls.KEY)['on']:
        return []
    local = at.astimezone(ZONE)
    night = local.date().isoformat()
    deadline = datetime.combine(local.date(), time(5), ZONE)
    stopped = total >= STOP
    if stopped:
        report_stop(c, total)
    rows = []
    for entry in controls.pilots(c):
        bot, model = entry['bot'], entry['model']
        if c.execute("SELECT 1 FROM memory_tidy_passes WHERE night=? AND bot=?", (night, bot)).fetchone():
            continue
        prior = c.execute("SELECT baseline FROM memory_tidy_passes WHERE bot=? AND status IN ('kept','discarded') "
                           "ORDER BY started DESC LIMIT 1", (bot,)).fetchone()
        since = prior['baseline'] if prior else ''
        reason = ('skipped: spend stop' if stopped else
                  'skipped: no change' if not changed(c, bot, since) else
                  'skipped: model floor' if 'haiku' in model.lower() else
                  'skipped: spend limit' if usage_limits.blocked(c, bot, at) else
                  'skipped: not finished' if at >= deadline else '')
        if not reason and controls.eligible(c, settings, bot) is None:
            reason = 'skipped: model unavailable'
        if not reason:
            # No approximate substitute for pass-only runtime protection.
            if not hasattr(check_models, 'file_pass'):
                raise Problem('conflict', controls.UNAVAILABLE, 409)
            try:
                choice = check_models.choose(c, settings, bot, model, 'check', memory_pass=True)
                from clients.harness_manifests import caps
                if not packet_runner(c, bot) or not caps(choice['harness']).get('packet_only'):
                    reason = 'skipped: model unavailable'
            except check_models.CheckModelError:
                reason = 'skipped: model unavailable'
        ident = H.new_id()
        c.execute("INSERT INTO memory_tidy_passes(id,night,bot,model,started,deadline,status,reason,baseline) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (ident, night, bot, model, stamp(at), stamp(deadline),
                                               'skipped' if reason else 'filed', reason, stamp(at)))
        if reason:
            log(c, bot, reason)
        else:
            parent = H.task_create(c, H.KEEPER, 'Nightly memory tidy: ' + night,
                'Private result container. Code checks the child attachment and files the reviewer card; '
                'no ordinary bot turn is needed.', 'bot:' + bot, private=True, quiet_notice=True,
                lint=False, deduplicate=False)
            child = H.task_create(c, H.KEEPER, 'Nightly memory tidy pass: ' + night,
                'Read only the pushed memory/ and knowledge/ checkout: working rules, log entries added '
                'since ' + (since or 'the first pass') + ', and cited original entries. No transcripts or '
                'evidence dumps. Return numbered edits as a JSON attachment using the Nightly memory tidy '
                'contract. Write no repository files. Do not mark this child Done or Declined: code reads '
                'the attachment, runs checks, files the card, then closes it. Deadline 05:00 Pacific.',
                'bot:' + bot, parent_id=parent['id'], private=True, lint=False, deduplicate=False)
            check_models.file_pass(c, settings, child['id'], model)
            c.execute("UPDATE memory_tidy_passes SET parent_task_id=?,check_task_id=? WHERE id=?",
                      (parent['id'], child['id'], ident))
            log(c, bot, 'filed: ' + model)
        rows.append(ident)
        if not reason:
            break
    return rows


def deliver(c, pass_id, *, snapshot, raw, checkout_before, checkout_after,
            conflict_check, memory_commit, reviewer, since=None, previous_learnings=None):
    """Trusted runner handoff only; never expose caller-supplied manifests as an API.

    Read/check the result and file the independent card before retiring either task.
    A deadline, terminal skip or retry cannot leave a partial or duplicate card.
    """
    from . import memory_tidy, memory_tidy_cards
    row = c.execute('SELECT * FROM memory_tidy_passes WHERE id=?', (pass_id,)).fetchone()
    if not row or row['status'] != 'filed':
        return None
    if H.now() >= row['deadline']:
        close_pass(c, row, 'skipped: not finished')
        return None
    child = H.task(c, row['check_task_id']) if row['check_task_id'] else None
    if not child or child['status'] in ('declined', 'closed'):
        close_pass(c, row, 'skipped: model unavailable')
        return None
    refresh_costs(c)
    row = c.execute('SELECT * FROM memory_tidy_passes WHERE id=?', (pass_id,)).fetchone()
    result = memory_tidy.evaluate(snapshot, raw, since=since, today=H.parse_ts(H.now()).astimezone(ZONE).date(),
                                  checkout_before=checkout_before,
                                  checkout_after=checkout_after, conflict_check=conflict_check,
                                  previous_learnings=previous_learnings)
    card = memory_tidy_cards.file_card(c, bot=row['bot'], reviewer=reviewer, pass_id=pass_id,
        memory_commit=memory_commit, model=row['model'], cost_usd=row['cost_usd'], result=result,
        started_at=row['started'], parent_task_id=row['parent_task_id'], check_task_id=row['check_task_id'])
    close_tasks(c, row, result.status)
    c.execute('UPDATE memory_tidy_passes SET status=?,reason=? WHERE id=?',
              ('offered' if card else result.status, '; '.join(result.flags), pass_id))
    log(c, row['bot'], 'result: ' + result.status)
    return card


def sync_decisions(c):
    # Only the last kept/discarded pass advances the free-skip baseline. Expiry,
    # replacement and failed checks leave genuinely new memory eligible next night.
    c.execute("UPDATE memory_tidy_passes SET status=(SELECT status FROM memory_tidy_cards "
              "WHERE pass_id=memory_tidy_passes.id) WHERE status='offered' AND EXISTS "
              "(SELECT 1 FROM memory_tidy_cards WHERE pass_id=memory_tidy_passes.id AND status IN ('kept','discarded'))")
