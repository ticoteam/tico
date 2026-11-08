"""Stable computer-local connection identity; display names never rename credentials."""
import json

from fastapi import Request

from . import hubdb as H, models as M
from .repositories import metadata, save_metadata
from .store import Problem


def connection_id(runner, profile):
    # The computer and immutable profile key identify a connection, not a claim
    # that matching provider accounts were discovered. Encode legacy keys exactly.
    return 'subscription:' + json.dumps([runner, profile], separators=(',', ':'), ensure_ascii=False)


def identity(c, runner, profile):
    sid = connection_id(runner, profile)
    label = metadata(c, sid).get('display_name') or profile
    return {'id': sid, 'display_name': label}


def display_names(c):
    """{connection_id: display_name} for every renamed subscription in one pass (Operations shared_logins), from the
    subscription rows Operations' profiles already read in this snapshot."""
    from .subscriptions import subscription_meta
    return {key: value['display_name'] for key, value in subscription_meta(c).items()
            if key.startswith('subscription:') and value.get('display_name')}


def usage_label(c, sid):
    if sid.startswith('subscription:'):
        runner, profile = json.loads(sid.removeprefix('subscription:'))
        row = c.execute('SELECT label FROM runners WHERE id=?', (runner,)).fetchone()
        computer = row['label'] if row else runner
        return f"{identity(c, runner, profile)['display_name']} · {computer}"
    if sid.startswith('legacy-profile:'):
        return sid.removeprefix('legacy-profile:') + ' · computer not recorded'
    return sid or 'Not recorded / not applicable'


def install(app, store, auth, mutate):
    @app.put('/api/v2/subscriptions/name')
    def rename(request: Request, body: M.SubscriptionNameEdit):
        who = request.state.identity
        def work(c):
            auth.domain(who)
            runner = c.execute('SELECT * FROM runners WHERE id=? AND revoked_at IS NULL', (body.runner_id,)).fetchone()
            if not runner or who.role not in ('human', 'owner') or not (
                    auth.bot_admin(who) or runner['owner_id'] == H.actor_id(who.actor)):
                raise Problem('forbidden', 'Only a computer operator or administrator can rename a subscription', 403)
            if not c.execute('SELECT 1 FROM computer_profiles WHERE runner_id=? AND profile=?',
                             (body.runner_id, body.profile)).fetchone():
                raise Problem('not_found', 'Subscription not found on this computer', 404)
            name = body.display_name.strip()
            if not name:
                raise Problem('invalid', 'Enter a subscription name', 422)
            sid = connection_id(body.runner_id, body.profile)
            save_metadata(c, sid, {'display_name': name})
            H.event(c, who.actor, 'subscription.renamed', sid, {'display_name': name})
            return identity(c, body.runner_id, body.profile)
        return mutate(request, body, work)
