"""Clean up: pruning only (docs/maintenance.md, plan 2.2 and Amendment v2 G). These are the retention sweeps the hourly
cleanup block of backend/scheduler.py ran, the VACUUM that gives their pages back, and the run history past 30 days.

Deadline work stays in the engine on its own cadence (scheduler.py): support triage, screening, webhook auto-disable,
approval expiry and the finance sweep. No model, and no customer tokens.
"""
from ..store import (H, reclaim_free_pages, sweep_credential_uses, sweep_deltas, sweep_events, sweep_idempotency,
                     sweep_mail, sweep_tokens)

RUN_HISTORY_DAYS = 30


def _prune_history(c, now):
    """Maintenance's own history: the runs past RUN_HISTORY_DAYS, with their step rows. A live run is never pruned."""
    cutoff = H.shift(now, days=-RUN_HISTORY_DAYS)
    c.execute("DELETE FROM maintenance_run_steps WHERE maintenance_run_id IN (SELECT id FROM maintenance_runs "
              "WHERE started_at<? AND finished_at IS NOT NULL)", (cutoff,))
    c.execute("DELETE FROM maintenance_runs WHERE started_at<? AND finished_at IS NOT NULL", (cutoff,))


def run(c, ctx):
    store, now = ctx["store"], ctx["now"]
    from .. import automations, events, flight, performance, rules, triggers, webhook_delivery
    from .. import system_routines as SR
    with store.transaction() as tx:
        # A duplicate is not a failure (batch_work.isolated); rows an older server wrote for one go.
        tx.execute("DELETE FROM service_health WHERE service LIKE 'background:%' AND "
                   "julianday(json_extract(detail,'$.failed_at')) < julianday(?) - 30", (now,))
        tx.execute("DELETE FROM service_health WHERE service LIKE 'background:%' AND last_error LIKE '%: duplicate: %'")
        SR.prune(tx)                                   # each system job keeps its last runs
        rules.prune_events(tx)                         # the webhook rules' repeat ledger keeps 30 days
        webhook_delivery.prune(tx, now)                # outgoing webhooks: events and finished sends keep a month
        automations.prune(tx, now)                     # automation runs: step detail keeps 30 days
        _prune_history(tx, now)
    sweep_idempotency(store, now)
    sweep_mail(store, now)
    sweep_deltas(store, now)                           # finished runs' streamed text pieces keep a day
    sweep_tokens(store, now)                           # finished runs' token rows keep a week
    sweep_events(store, now)                           # bookkeeping events keep 7 or 30 days
    sweep_credential_uses(store, now)                  # old credential use rows to one per USE_WINDOW a pass
    reclaim_free_pages(store)                          # the pages those sweeps freed go back to the disk
    events.sweep(store, now)                           # live events keep a day
    triggers.sweep(store, now)                         # webhook bodies keep their trigger's days, delivery ids 30
    at = H.parse_ts(now).timestamp()
    flight.sweep(store, at)                            # the flight recorder's minutes become hours
    performance.sweep(store, at)                       # computers' minutes become ten-minute points
    return {"status": "ran", "reason": "Done", "checked": 0, "found": 0, "woke": 0, "cost_usd": 0.0, "details": {}}
