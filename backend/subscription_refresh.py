"""Explicit, bounded runner reads of provider allowance. Credentials stay on the computer."""

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Request
from pydantic import Field

from clients.harness_manifests import caps

from . import hubdb as H, models as M
from . import runner_online
from .repositories import metadata, save_metadata
from .store import Problem
from .api_contract import api_v2

LIFETIME = 120
COOLDOWN = 60


class Refresh(M.Contract):
    runner_id: M.ID
    profile: M.Slug
    runtime: M.WeeklyRuntime


class PlanRead(M.Contract):
    """A Claude plan's week from the rate-limit event of a one-line turn (`request_plan_read`)."""

    used_percent: float = Field(ge=0, le=100, allow_inf_nan=False)
    resets_at: str = Field(max_length=40)  # an ISO instant with its offset


class Report(M.Contract):
    # A plan's read names its plan (`subscription`, `plan`) and no profile; a computer's own login names its profile.
    profile: M.Slug | Literal[''] = ''
    runtime: M.WeeklyRuntime
    state: Literal['succeeded', 'unavailable', 'failed']
    weekly: M.SubscriptionWeekly | None = None
    subscription: M.ID | None = None
    plan: PlanRead | None = None


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
    runner = c.execute(
        'SELECT * FROM runners_with_state WHERE id=? AND revoked_at IS NULL', (body.runner_id,)
    ).fetchone()
    if (
        not runner
        or who.role not in ('human', 'owner')
        or not (auth.bot_admin(who) or runner['owner_id'] == H.actor_id(who.actor))
    ):
        raise Problem('forbidden', 'Only a computer operator or administrator can refresh weekly usage', 403)
    row = c.execute(
        'SELECT runtimes FROM computer_profiles WHERE runner_id=? AND profile=?', (body.runner_id, body.profile)
    ).fetchone()
    state = json.loads(row[0] or '{}').get(body.runtime) if row else None
    if state is None:
        raise Problem('not_found', 'Subscription runtime not found on this computer', 404)
    if c.execute(
        "SELECT 1 FROM model_logins WHERE runner_id=? AND profile=? AND runtime=? "
        "AND state IN ('requested','starting','waiting','finishing') AND expires_at>?",
        (body.runner_id, body.profile, body.runtime, H.now()),
    ).fetchone():
        raise Problem('busy', 'Finish signing in before refreshing weekly usage', 409)
    storage_key = key(body.runner_id, body.profile, body.runtime)
    old = metadata(c, storage_key)
    same_signin = old and old['requested_at'] >= signin_after(c, body.runner_id, body.profile).get(body.runtime, '')
    if same_signin and (
        (old['state'] == 'requested' and old['expires_at'] > H.now())
        or old['requested_at'] > H.shift(H.now(), seconds=-COOLDOWN)
    ):
        return view(old)
    now = H.now()
    value = {
        'id': H.new_id(),
        'profile': body.profile,
        'runtime': body.runtime,
        'state': 'requested',
        'requested_at': now,
        'updated_at': now,
        'expires_at': H.shift(now, seconds=LIFETIME),
        'weekly': old.get('weekly'),
    }
    # Only a runtime whose weekly usage can be read on demand (`weekly_usage = "refresh"`) is asked.
    if caps(body.runtime).get('weekly_usage') != 'refresh':
        value['state'] = 'unavailable'
    elif state.get('signed_in') is False:
        raise Problem('signed_out', 'Sign in before refreshing this subscription', 409)
    elif not runner_online.is_online(runner['last_seen_at']):
        raise Problem('offline', 'This computer is offline; its last weekly reading is unchanged', 409)
    elif len(pending(c, body.runner_id)) >= 5:
        raise Problem('busy', 'This computer already has weekly refreshes queued; try again shortly', 409)
    save_metadata(c, storage_key, value)
    H.event(c, who.actor, 'subscription.refresh_requested', body.profile, body.model_dump())
    return view(value)


def pending(c, runner):
    prefix = f'subscription-refresh:{runner}:'
    # LIKE is unnecessary: runner IDs may contain wildcard characters.
    rows = c.execute('SELECT value FROM registry_metadata WHERE substr(key,1,?)=? ORDER BY key', (len(prefix), prefix))
    reads = [
        view(value)
        for row in rows
        if (value := json.loads(row[0])).get('state') == 'requested' and value['expires_at'] > H.now()
    ]
    return (reads + plan_pending(c, runner))[:5]


def report(c, runner, rid, body):
    from .subscriptions import weekly_snapshot

    if body.subscription:
        return plan_report(c, runner, rid, body)
    storage_key = key(runner, body.profile, body.runtime)
    value = metadata(c, storage_key)
    if not value or value['id'] != rid:
        raise Problem('not_found', 'Weekly refresh request not found', 404)
    if value['requested_at'] < signin_after(c, runner, body.profile).get(body.runtime, ''):
        raise Problem('changed', 'Sign-in changed after this refresh was requested', 409)
    if value['state'] != 'requested' or value['expires_at'] <= H.now():
        return view(value)
    row = c.execute(
        'SELECT runtimes FROM computer_profiles WHERE runner_id=? AND profile=?', (runner, body.profile)
    ).fetchone()
    state = json.loads(row[0] or '{}').get(body.runtime) if row else None
    weekly = weekly_snapshot(body.weekly.model_dump()) if body.weekly else None
    if weekly:
        # A named login's refresh is never a plan's reading, whatever the computer sent: no provenance is kept.
        weekly['origin'] = weekly['subscription_id'] = None
    if body.state == 'succeeded' and (
        not weekly
        or H.parse_ts(weekly['reported_at']) < H.parse_ts(value['requested_at'])
        or state is None
        or state.get('signed_in') is False
    ):
        raise Problem('invalid', 'Weekly refresh has no current valid reading', 422)
    value.update(state=body.state, updated_at=H.now())
    if body.state == 'succeeded':
        value['weekly'] = weekly
    save_metadata(c, storage_key, value)
    return view(value)


# A Claude plan's usage, read on demand (Settings > Models: the refresh button beside the plan).
#
# The server asks one online computer that has the plan to read its week, and gives it the plan's setup-token for that one
# read. A setup-token may only run prompts (Claude Code refuses it `/usage`), so the computer runs a one-line prompt on
# Haiku in an empty home of its own (runner-rs subscription_refresh.rs `read_claude_plan`) and sends back the week from
# the rate-limit event that turn prints, as any turn's reading. The server keeps it as the plan's reading, which
# `_plan_usage` takes like a turn's (origin "subscription", this plan's). The read spends a few tokens of the plan. Only a
# Claude setup-token is read this way: it does not rotate, so a read elsewhere cannot strand the login. A ChatGPT login's
# refresh token rotates, so its week still comes from its computers' runs.
PLAN_CAPABILITY = 'plan_usage_v1'  # what a runner says it can do this read
PLAN_PREFIX = 'subscription-usage:'
PLAN_WEEK = timedelta(days=8)  # the longest a week runs from its reading to its reset (subscriptions.WINDOWS)


def _instant(text):
    """An ISO instant with its offset, in UTC; None for anything else."""
    try:
        when = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return when.astimezone(timezone.utc) if when.tzinfo else None


def plan_key(sid):
    return PLAN_PREFIX + sid


def plan_reading(value):
    """The plan's last reading as a read reported it (validated where `_plan_usage` reads it), or None."""
    return (value or {}).get('reading')


def plan_view(value):
    """The plan's refresh as Settings > Models shows it: `state` ("requested", "succeeded", "failed", "unavailable",
    "expired") and when it was asked; None when never asked."""
    request = (value or {}).get('request')
    if not request:
        return None
    state = request.get('state')
    if state == 'requested' and request['expires_at'] <= H.now():
        state = 'expired'
    return {'state': state, 'requested_at': request['requested_at']}


def _can_read_plan(runner):
    from . import model_login

    return model_login._online(runner) and PLAN_CAPABILITY in (H._json(runner['capabilities'], []) or [])


def request_plan_read(c, sid, actor):
    """Ask a computer that has the plan to read its week now: one read at a time, and not again within COOLDOWN.
    The route admits admins only. Returns the plan's refresh (`view`)."""
    from . import model_subscriptions as MS

    sub = MS.row(c, sid)
    if not sub:
        raise Problem('not_found', 'Subscription not found', 404)
    if sub['kind'] != 'claude_token':
        raise Problem(
            'unavailable',
            "Only a Claude plan is read on demand; a ChatGPT plan's week comes from its computers' runs",
            409,
        )
    if sub['status'] == 'needs_reconnect':
        raise Problem('signed_out', 'Sign in again before reading this plan', 409)
    ctx = MS._listing(c, sid)
    grants = [g for g in ctx['grants'].get(sid, []) if g['runtime'] == 'claude' and g['runner_id'] in ctx['runners']]
    if not grants:
        raise Problem('no_computer', 'Give this plan to a computer first', 409)
    ready = [g['runner_id'] for g in grants if _can_read_plan(ctx['runners'][g['runner_id']])]
    if not ready:
        raise Problem('offline', 'Its computers are offline or not yet updated to read usage', 409)
    value = metadata(c, plan_key(sid)) or {}
    old = value.get('request') or {}
    now = H.now()
    if (old.get('state') == 'requested' and old.get('expires_at', '') > now) or old.get('requested_at', '') > H.shift(
        now, seconds=-COOLDOWN
    ):
        return plan_view(value)
    value['request'] = {
        'id': H.new_id(),
        'runner_id': ready[0],
        'requested_at': now,
        'updated_at': now,
        'expires_at': H.shift(now, seconds=LIFETIME),
        'state': 'requested',
    }
    save_metadata(c, plan_key(sid), value)
    H.event(c, actor, 'subscription.usage_requested', sid, {'runner_id': ready[0]})
    return plan_view(value)


def plan_pending(c, runner):
    """This computer's reads asked and not yet answered (at most five), as its runner polls them."""
    rows = c.execute(
        'SELECT key, value FROM registry_metadata WHERE key>=? AND key<?', (PLAN_PREFIX, PLAN_PREFIX[:-1] + ';')
    )
    now = H.now()
    out = []
    for row in rows:
        request = (json.loads(row['value'] or '{}')).get('request') or {}
        if request.get('state') == 'requested' and request.get('runner_id') == runner and request['expires_at'] > now:
            out.append(
                {
                    'id': request['id'],
                    'subscription': row['key'][len(PLAN_PREFIX) :],
                    'profile': '',
                    'runtime': 'claude',
                    'state': 'requested',
                    'requested_at': request['requested_at'],
                    'expires_at': request['expires_at'],
                }
            )
    return out[:5]


def plan_with_value(c, vault, runner, item):
    """The pending read with its plan's setup-token, for this poll only: the value is never kept in a request, and it
    goes only to a computer the plan is still given to."""
    from . import model_subscriptions as MS

    sid = item['subscription']
    sub = MS.row(c, sid)
    given = any(
        g['runner_id'] == runner and g['runtime'] == 'claude' for g in MS._listing(c, sid)['grants'].get(sid, [])
    )
    if not sub or sub['status'] == 'needs_reconnect' or not given:
        return item
    spec = MS.delivered(sub['kind'], vault.cipher.unseal(c, MS._context(sid), sub['sealed']))
    return {**item, 'env': {spec['env']: spec['value']}}


def plan_report(c, runner, rid, body):
    """A computer's answer to a read it was asked. A succeeded read with its week (a reset in a week's reach) is kept as
    the plan's reading; any other answer only ends the read with its state."""
    storage = plan_key(body.subscription)
    value = metadata(c, storage) or {}
    request = value.get('request') or {}
    if not request or request.get('id') != rid or request.get('runner_id') != runner:
        raise Problem('not_found', 'Usage request not found', 404)
    now = H.now()
    if request['state'] != 'requested' or request['expires_at'] <= now:
        return plan_view(value)
    if body.state == 'succeeded':
        now_at = datetime.now(timezone.utc)
        when = _instant(body.plan.resets_at) if body.plan else None
        if when is None or not now_at < when <= now_at + PLAN_WEEK:
            raise Problem('invalid', 'Usage has no current valid week', 422)
        value['reading'] = {
            'used_percent': body.plan.used_percent,
            'resets_at': when.isoformat(),
            'reported_at': now,
            'status': None,
            'origin': 'subscription',
            'subscription_id': body.subscription,
        }
    value['request'] = {**request, 'state': body.state, 'updated_at': now}
    save_metadata(c, storage, value)
    return plan_view(value)


def install(app, store, auth, mutate):
    @app.post(
        '/api/v2/subscriptions/refresh',
        **api_v2(
            "Subscriptions",
            "refreshSubscriptionWeekly",
            "Request a bounded weekly allowance read on its computer",
            "SubscriptionRefreshResult",
        ),
    )
    def refresh_subscription(request: Request, body: Refresh):
        return mutate(request, body, lambda c: request_refresh(c, auth, request.state.identity, body))

    @app.get('/api/v2/runner-subscription-refreshes')
    def runner_refreshes(request: Request):
        with store.read() as c:
            who = request.state.identity
            request.app.state.execution.runner(c, who)
            vault = request.app.state.vault
            # A plan's read gets its token here, for this poll only (`plan_with_value`); nothing stores it.
            return {
                'refreshes': [
                    plan_with_value(c, vault, who.runner_id, item) if item.get('subscription') else item
                    for item in pending(c, who.runner_id)
                ]
            }

    @app.post('/api/v2/runner-subscription-refreshes/{rid}/report')
    def report_refresh(request: Request, rid: str, body: Report):
        who = request.state.identity
        return mutate(
            request,
            body,
            lambda c: (request.app.state.execution.runner(c, who), report(c, who.runner_id, rid, body))[1],
        )
