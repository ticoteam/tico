"""Explicit, bounded runner reads of provider allowance. Credentials stay on the computer."""
import json
from typing import Literal

from fastapi import Request

from clients.harness_manifests import caps

from . import hubdb as H, models as M
from .repositories import metadata, save_metadata
from .store import Problem

LIFETIME = 120
COOLDOWN = 60


class Refresh(M.Contract):
    runner_id: M.ID
    profile: M.Slug
    runtime: M.WeeklyRuntime


class Report(M.Contract):
    profile: M.Slug
    runtime: M.WeeklyRuntime
    state: Literal['succeeded', 'unavailable', 'failed']
    weekly: M.SubscriptionWeekly | None = None


def key(runner, profile, runtime):
    return f'subscription-refresh:{runner}:{profile}:{runtime}'


def view(value):
    result = {k: v for k, v in value.items() if k != 'weekly'}
    if result.get('state') == 'requested' and result['expires_at'] <= H.now():
        result['state'] = 'expired'
    return result


def signin_key(runner, profile):
    return f'subscription-reading-after:{runner}:{profile}'


def signin_after(c, runner, profile):
    return metadata(c, signin_key(runner, profile))


def invalidate_signin(c, runner, profile, runtime, at):
    if profile:
        value = signin_after(c, runner, profile)
        save_metadata(c, signin_key(runner, profile), {**value, runtime: at})
        storage_key = key(runner, profile, runtime)
        refresh = metadata(c, storage_key)
        if refresh and refresh['state'] == 'requested':
            save_metadata(c, storage_key, {**refresh, 'state': 'failed', 'updated_at': at})


def request_refresh(c, auth, who, body):
    auth.domain(who)
    runner = c.execute('SELECT * FROM runners WHERE id=? AND revoked_at IS NULL', (body.runner_id,)).fetchone()
    if not runner or who.role not in ('human', 'owner') or not (
            auth.bot_admin(who) or runner['operator'] == H.actor_id(who.actor)):
        raise Problem('forbidden', 'Only a computer operator or administrator can refresh weekly usage', 403)
    row = c.execute('SELECT runtimes FROM computer_profiles WHERE runner_id=? AND profile=?',
                    (body.runner_id, body.profile)).fetchone()
    state = json.loads(row[0] or '{}').get(body.runtime) if row else None
    if state is None:
        raise Problem('not_found', 'Subscription runtime not found on this computer', 404)
    if c.execute("SELECT 1 FROM model_logins WHERE runner_id=? AND profile=? AND runtime=? "
                 "AND state IN ('requested','starting','waiting','finishing') AND expires_at>?",
                 (body.runner_id, body.profile, body.runtime, H.now())).fetchone():
        raise Problem('busy', 'Finish signing in before refreshing weekly usage', 409)
    storage_key = key(body.runner_id, body.profile, body.runtime)
    old = metadata(c, storage_key)
    same_signin = old and old['requested_at'] >= signin_after(c, body.runner_id, body.profile).get(body.runtime, '')
    if same_signin and ((old['state'] == 'requested' and old['expires_at'] > H.now())
                        or old['requested_at'] > H.shift(H.now(), seconds=-COOLDOWN)):
        return view(old)
    now = H.now()
    value = {'id': H.new_id(), 'profile': body.profile, 'runtime': body.runtime,
             'state': 'requested', 'requested_at': now, 'updated_at': now,
             'expires_at': H.shift(now, seconds=LIFETIME), 'weekly': old.get('weekly')}
    # Only a runtime whose weekly usage can be read on demand (`weekly_usage = "refresh"`) is asked.
    if caps(body.runtime).get('weekly_usage') != 'refresh':
        value['state'] = 'unavailable'
    elif state.get('signed_in') is False:
        raise Problem('signed_out', 'Sign in before refreshing this subscription', 409)
    elif not runner['last_seen_at'] or runner['last_seen_at'] < H.shift(now, seconds=-60):
        raise Problem('offline', 'This computer is offline; its last weekly reading is unchanged', 409)
    elif len(pending(c, body.runner_id)) >= 5:
        raise Problem('busy', 'This computer already has weekly refreshes queued; try again shortly', 409)
    save_metadata(c, storage_key, value)
    H.event(c, who.actor, 'subscription.refresh_requested', body.profile, body.model_dump())
    return view(value)


def pending(c, runner):
    prefix = f'subscription-refresh:{runner}:'
    # LIKE is unnecessary: runner IDs may contain wildcard characters.
    rows = c.execute('SELECT value FROM registry_metadata WHERE substr(key,1,?)=? ORDER BY key',
                     (len(prefix), prefix))
    return [view(value) for row in rows if (value := json.loads(row[0])).get('state') == 'requested'
            and value['expires_at'] > H.now()][:5]


def report(c, runner, rid, body):
    from .subscriptions import weekly_snapshot
    storage_key = key(runner, body.profile, body.runtime)
    value = metadata(c, storage_key)
    if not value or value['id'] != rid:
        raise Problem('not_found', 'Weekly refresh request not found', 404)
    if value['requested_at'] < signin_after(c, runner, body.profile).get(body.runtime, ''):
        raise Problem('changed', 'Sign-in changed after this refresh was requested', 409)
    if value['state'] != 'requested' or value['expires_at'] <= H.now():
        return view(value)
    row = c.execute('SELECT runtimes FROM computer_profiles WHERE runner_id=? AND profile=?',
                    (runner, body.profile)).fetchone()
    state = json.loads(row[0] or '{}').get(body.runtime) if row else None
    weekly = weekly_snapshot(body.weekly.model_dump()) if body.weekly else None
    if body.state == 'succeeded' and (not weekly or H.parse_ts(weekly['reported_at']) < H.parse_ts(value['requested_at'])
                                    or state is None or state.get('signed_in') is False):
        raise Problem('invalid', 'Weekly refresh has no current valid reading', 422)
    value.update(state=body.state, updated_at=H.now())
    if body.state == 'succeeded':
        value['weekly'] = weekly
    save_metadata(c, storage_key, value)
    return view(value)


def install(app, store, auth, mutate):
    @app.post('/api/v2/subscriptions/refresh')
    def refresh_subscription(request: Request, body: Refresh):
        return mutate(request, body, lambda c: request_refresh(c, auth, request.state.identity, body))

    @app.get('/api/v2/runner-subscription-refreshes')
    def runner_refreshes(request: Request):
        with store.read() as c:
            who = request.state.identity
            request.app.state.execution.runner(c, who)
            return {'refreshes': pending(c, who.runner_id)}

    @app.post('/api/v2/runner-subscription-refreshes/{rid}/report')
    def report_refresh(request: Request, rid: str, body: Report):
        who = request.state.identity
        return mutate(request, body, lambda c: (request.app.state.execution.runner(c, who), report(c, who.runner_id, rid, body))[1])
